"""
Schematic synthesis (Stage 4) — from packed rectangles to a drawable plan.

The thesis packing (``schematic_mock.pack_rectangles_with_connectivity``)
places one rectangle per room, sized by area and aspect ratio and pulled
together along the predicted connectivity edges. This module continues
from there, deterministically:

  1. **Compaction** — every rectangle slides towards the centre of the
     group, one axis at a time, until it touches a neighbour. Rooms that
     were merely *near* each other end up sharing a wall.
  2. **Walls** — every rectangle side is split into the parts that
     coincide with a neighbour (interior partition, ``int``) and the parts
     that face outside (exterior wall, ``ext``). Walls are drawn as double
     lines (a hollow band) in the canvas.
  3. **Openings** — a door on every shared wall between two connected
     rooms (door width / corner offset after Weber et al. 2024,
     ``hypergraph`` ``Preset.cs``: 0.8 m door, 0.2 m from the corner — we
     use 0.9 m, the common residential leaf), *no* wall at all between
     the living room and the kitchen (open plan), an entrance door on
     the longest exterior wall of the room the ``outside`` node connects
     to, and a window centred on every exterior wall whose length comes
     from the room type's planned WWR (window height 1.5 m of a 2.7 m
     storey ⇒ window length = WWR · 2.7 / 1.5 · wall length).
  4. **Furniture** — simple blocks per room type (bed, wardrobe, sofa,
     table, counter, WC, basin, shower …) placed against walls, away
     from door clearances, in the manner of the furniture blocks of the
     hypergraph library.

Everything is computed in the canvas' *reference* coordinate system (the
packer's box, ~600 units on the long side); ``metre`` is the number of
reference units in one metre so that doors, walls and furniture have
real sizes regardless of the plan's units.

The whole process is recorded as a list of **frames** (``Synthesis.frames``)
so the canvas can replay it: bubbles at the connectivity positions →
bubble packing → rectangles → spring packing → compaction → alignment →
walls → doors & windows → furniture. The last frame *is* the static plan.
"""

from __future__ import annotations

import copy
import math
from typing import Dict, List, Optional, Tuple

from models.schematic_mock import (
    align_group, compute_area_scale, pack_circles,
    pack_rectangles_with_connectivity, _resolve_all_overlaps,
)

# ── Real-world sizes (metres) ────────────────────────────────────────
WALL_EXT_M   = 0.25
WALL_INT_M   = 0.12
DOOR_W_M     = 0.90
DOOR_OFF_M   = 0.20       # door leaf starts this far from the corner (Weber: doorOffset)
DOOR_CLEAR_M = 1.00       # furniture-free depth in front of a door (Weber: 1.5, tighter here)
WIN_FRAC_PER_WWR = 2.7 / 1.5   # window length fraction per unit WWR (1.5 m glazing in a 2.7 m storey)
WIN_MIN_M    = 0.60
WIN_MAX_FRAC = 0.85
EPS          = 1e-3

PRIMARY_TYPES = {"living", "bedroom", "kitchen"}
OUTDOOR_TYPES = {"balcony"}
OPEN_PAIRS    = {frozenset({"living", "kitchen"})}      # no wall, no door


# ═════════════════════════════════════════════════════════════════════
#  Small geometry helpers
# ═════════════════════════════════════════════════════════════════════

def _bounds(r: dict) -> Tuple[float, float, float, float]:
    return (r["cx"] - r["w"] / 2.0, r["cy"] - r["h"] / 2.0,
            r["cx"] + r["w"] / 2.0, r["cy"] + r["h"] / 2.0)


def _overlap_1d(a0: float, a1: float, b0: float, b1: float) -> Tuple[float, float] | None:
    lo, hi = max(a0, b0), min(a1, b1)
    return (lo, hi) if hi - lo > EPS else None


def _rect_hits(a: Tuple[float, float, float, float],
               b: Tuple[float, float, float, float], tol: float = 0.0) -> bool:
    return not (a[2] <= b[0] + tol or b[2] <= a[0] + tol or
                a[3] <= b[1] + tol or b[3] <= a[1] + tol)


# ═════════════════════════════════════════════════════════════════════
#  Compaction — slide every rectangle towards the group centre
# ═════════════════════════════════════════════════════════════════════

def _slide_limit(r: dict, others: List[dict], axis: str, direction: int,
                 tol: float) -> float:
    """How far ``r`` can move along ``axis`` in ``direction`` (+1 / −1)
    before touching one of ``others`` (rectangles that overlap it on the
    perpendicular axis)."""
    x0, y0, x1, y1 = _bounds(r)
    best = float("inf")
    for o in others:
        ox0, oy0, ox1, oy1 = _bounds(o)
        if axis == "x":
            if _overlap_1d(y0, y1, oy0, oy1) is None:
                continue
            d = (ox0 - x1) if direction > 0 else (x0 - ox1)
        else:
            if _overlap_1d(x0, x1, ox0, ox1) is None:
                continue
            d = (oy0 - y1) if direction > 0 else (y0 - oy1)
        if d >= -tol:
            best = min(best, max(0.0, d))
    return best


def compact_rectangles(rects: List[dict], passes: int = 8,
                       tol: float = 0.5, record=None) -> None:
    """Gravity compaction towards the centre of the group's bounding box.

    In every pass the rectangles are visited from the innermost outward;
    each slides along x, then along y, towards the centre until it meets
    a neighbour (or its own centre reaches the target). The target is
    fixed at the start so the group does not drift.
    """
    if not rects:
        return
    xs = [b for r in rects for b in (_bounds(r)[0], _bounds(r)[2])]
    ys = [b for r in rects for b in (_bounds(r)[1], _bounds(r)[3])]
    tcx = (min(xs) + max(xs)) / 2.0
    tcy = (min(ys) + max(ys)) / 2.0
    for _ in range(passes):
        moved = 0.0
        order = sorted(rects, key=lambda r: abs(r["cx"] - tcx) + abs(r["cy"] - tcy))
        for r in order:
            others = [o for o in rects if o is not r]
            for axis in ("x", "y"):
                c = r["cx"] if axis == "x" else r["cy"]
                t = tcx if axis == "x" else tcy
                if abs(t - c) < EPS:
                    continue
                direction = 1 if t > c else -1
                limit = _slide_limit(r, others, axis, direction, tol)
                step = min(limit, abs(t - c))
                if step > EPS:
                    if axis == "x":
                        r["cx"] += direction * step
                    else:
                        r["cy"] += direction * step
                    moved += step
        if record is not None:
            record()
        if moved < tol:
            break


def contact_pass(rects: List[dict], edges: List[dict], max_gap: float,
                 tol: float, record=None, guard=None) -> None:
    """For every connectivity edge whose rooms do not touch, slide the
    smaller room towards the larger one along the axis with the gap, if
    the gap is at most ``max_gap`` and nothing is in the way."""
    by_id = {r["room_id"]: r for r in rects}
    moved_any = False
    for e in edges:
        a, b = by_id.get(e.get("source")), by_id.get(e.get("target"))
        if a is None or b is None:
            continue
        mover, anchor = (a, b) if a["w"] * a["h"] <= b["w"] * b["h"] else (b, a)
        ax0, ay0, ax1, ay1 = _bounds(anchor)
        mx0, my0, mx1, my1 = _bounds(mover)
        gap_x = max(ax0 - mx1, mx0 - ax1)
        gap_y = max(ay0 - my1, my0 - ay1)
        others = [o for o in rects if o is not mover]
        if gap_x <= tol and gap_y <= tol:
            continue                                        # already touching
        before = guard() if guard is not None else 0.0
        key = None; old = None
        if gap_x > tol and gap_y <= tol and gap_x <= max_gap:
            direction = 1 if ax0 >= mx1 else -1
            if _slide_limit(mover, others, "x", direction, tol) >= gap_x - tol:
                key, old = "cx", mover["cx"]; mover["cx"] += direction * gap_x
        elif gap_y > tol and gap_x <= tol and gap_y <= max_gap:
            direction = 1 if ay0 >= my1 else -1
            if _slide_limit(mover, others, "y", direction, tol) >= gap_y - tol:
                key, old = "cy", mover["cy"]; mover["cy"] += direction * gap_y
        if key is not None:
            if guard is not None and guard() > before + EPS:
                mover[key] = old                        # would expose a blind side: undo
            else:
                moved_any = True
    if record is not None and moved_any:
        record()


def glue_balconies(rects: List[dict], facades: Dict[str, Tuple[str, str]], tol: float,
                   extra_obstacles: Optional[List[dict]] = None) -> None:
    """Keep every balcony flush against the façade chosen at the
    connectivity stage: ``facades`` maps balcony id → (anchor room id,
    side N/S/E/W). The balcony is turned so its long dimension runs along
    the façade and is **aligned with a corner** of that façade — the
    corner farther from the plan centre first — never centred on it. If
    neither corner (nor a position between them) is free of other rooms,
    the anchor's other exterior sides are tried in the same way."""
    by_id = {r["room_id"]: r for r in rects}
    indoor = [r for r in rects if r["room"]["room_type"] not in OUTDOOR_TYPES]
    if not indoor:
        return
    gcx = sum(r["cx"] for r in indoor) / len(indoor)
    gcy = sum(r["cy"] for r in indoor) / len(indoor)
    placed: List[dict] = []
    extra = list(extra_obstacles or [])
    for b in rects:
        if b["room"]["room_type"] not in OUTDOOR_TYPES:
            continue
        aid, side0 = facades.get(b["room_id"], (None, None))
        a = by_id.get(aid)
        if a is None or side0 not in ("N", "S", "E", "W"):
            placed.append(b); continue
        ax0, ay0, ax1, ay1 = _bounds(a)
        obstacles = [r for r in indoor if r is not a] + placed + extra
        long_, short_ = max(b["w"], b["h"]), min(b["w"], b["h"])
        dx, dy = a["cx"] - gcx, a["cy"] - gcy
        outward = {"N": -dy, "S": dy, "W": -dx, "E": dx}
        sides = [side0] + sorted((sd for sd in ("N", "S", "E", "W") if sd != side0),
                                 key=lambda sd: -outward[sd])

        def candidates(side: str):
            horizontal = side in ("N", "S")
            bw, bh = (long_, short_) if horizontal else (short_, long_)
            if horizontal:
                y0 = ay0 - bh if side == "N" else ay1
                # corner-aligned: flush with the W or E end of the façade
                ends = [("W", ax0), ("E", ax1 - bw)]
                ends.sort(key=lambda e: -abs((e[1] + bw / 2) - gcx))     # outer corner first
                xs = [e[1] for e in ends]
                span = ax1 - ax0 - bw
                if span > 0:
                    xs += [ax0 + span * f for f in (0.25, 0.5, 0.75)]
                for x0 in xs:
                    yield (x0, y0, x0 + bw, y0 + bh), bw, bh
            else:
                x0 = ax0 - bw if side == "W" else ax1
                ends = [("N", ay0), ("S", ay1 - bh)]
                ends.sort(key=lambda e: -abs((e[1] + bh / 2) - gcy))
                ys = [e[1] for e in ends]
                span = ay1 - ay0 - bh
                if span > 0:
                    ys += [ay0 + span * f for f in (0.25, 0.5, 0.75)]
                for y0 in ys:
                    yield (x0, y0, x0 + bw, y0 + bh), bw, bh

        normal = {"N": (0.0, -1.0), "S": (0.0, 1.0), "W": (-1.0, 0.0), "E": (1.0, 0.0)}

        def push_needed(c, side) -> float:
            """How far the box must be pushed outward (away from the
            plan, along the façade normal) before it overlaps nothing."""
            nx, ny = normal[side]
            step = max(tol, short_ * 0.1)
            d = 0.0
            while d <= long_ * 3:
                cc = (c[0] + nx * d, c[1] + ny * d, c[2] + nx * d, c[3] + ny * d)
                if not any(_rect_hits(cc, _bounds(o), tol) for o in obstacles):
                    return d
                d += step
            return float("inf")

        # Best position: no push on the chosen façade, else no push on
        # another exterior side, else the smallest outward push — the
        # balcony is pushed clear of the rooms like any other room is.
        best = None          # (rank, push, c, bw, bh, side)
        for rank, side in enumerate(sides):
            horizontal = side in ("N", "S")
            for c, bw, bh in candidates(side):
                touch = _overlap_1d(c[0], c[2], ax0, ax1) if horizontal else _overlap_1d(c[1], c[3], ay0, ay1)
                if touch is None or (touch[1] - touch[0]) < min(long_, ax1 - ax0 if horizontal else ay1 - ay0) * 0.5:
                    continue
                d = push_needed(c, side)
                if d == float("inf"):
                    continue
                key = (0 if d <= tol else 1, rank if d <= tol else 0, d, rank)
                if best is None or key < best[0]:
                    best = (key, d, c, bw, bh, side)
                if d <= tol and rank == 0:
                    break
            if best is not None and best[1] <= tol and best[5] == side0:
                break
        if best is None:
            c, bw, bh = next(candidates(side0)); side = side0; d = 0.0
        else:
            _, d, c, bw, bh, side = best
        nx, ny = normal[side]
        c = (c[0] + nx * d, c[1] + ny * d, c[2] + nx * d, c[3] + ny * d)
        chosen = (c, bw, bh)
        c, bw, bh = chosen
        b["cx"], b["cy"] = (c[0] + c[2]) / 2, (c[1] + c[3]) / 2
        b["w"], b["h"] = bw, bh
        placed.append(b)


def detached_balconies(rects: List[dict], facades: Dict[str, Tuple[str, str]],
                       metre: float, tol: float) -> set:
    """Ids of balconies that do not touch the room they open from over
    at least a door's width (door + corner offsets) — these are dropped."""
    by_id = {r["room_id"]: r for r in rects}
    need = (DOOR_W_M + 2 * DOOR_OFF_M) * metre
    out = set()
    for b in rects:
        if b["room"]["room_type"] not in OUTDOOR_TYPES:
            continue
        aid, side = facades.get(b["room_id"], (None, None))
        a = by_id.get(aid)
        if a is None:
            continue
        ax0, ay0, ax1, ay1 = _bounds(a)
        bx0, by0, bx1, by1 = _bounds(b)
        touching = False
        # flush against one of a's sides and overlapping along it?
        if abs(by1 - ay0) <= tol or abs(by0 - ay1) <= tol:
            ov = _overlap_1d(ax0, ax1, bx0, bx1)
            touching = ov is not None and (ov[1] - ov[0]) >= need
        if not touching and (abs(bx1 - ax0) <= tol or abs(bx0 - ax1) <= tol):
            ov = _overlap_1d(ay0, ay1, by0, by1)
            touching = ov is not None and (ov[1] - ov[0]) >= need
        if not touching:
            out.add(b["room_id"])
    return out


# ═════════════════════════════════════════════════════════════════════
#  Walls
# ═════════════════════════════════════════════════════════════════════

# A wall segment: dict(x1, y1, x2, y2, kind='ext'|'int', rooms=(a, b|None),
#                      side='N'|'S'|'E'|'W' as seen from room a, out=(nx, ny))
# ``out`` is the unit normal pointing *away* from room a.

_SIDE_NORMAL = {"N": (0.0, -1.0), "S": (0.0, 1.0), "W": (-1.0, 0.0), "E": (1.0, 0.0)}


def _side_segment(r: dict, side: str) -> Tuple[float, float, float, float]:
    x0, y0, x1, y1 = _bounds(r)
    return {"N": (x0, y0, x1, y0), "S": (x0, y1, x1, y1),
            "W": (x0, y0, x0, y1), "E": (x1, y0, x1, y1)}[side]


def build_walls(rects: List[dict], tol: float) -> List[dict]:
    """Split every rectangle side into shared (interior) and free
    (exterior) parts."""
    walls: List[dict] = []
    opposite = {"N": "S", "S": "N", "E": "W", "W": "E"}
    for a in rects:
        for side in ("N", "S", "E", "W"):
            ax1, ay1, ax2, ay2 = _side_segment(a, side)
            horizontal = side in ("N", "S")
            lo, hi = (ax1, ax2) if horizontal else (ay1, ay2)
            const = ay1 if horizontal else ax1
            shared: List[Tuple[float, float, dict]] = []
            for b in rects:
                if b is a:
                    continue
                bx1, by1, bx2, by2 = _side_segment(b, opposite[side])
                bconst = by1 if horizontal else bx1
                if abs(bconst - const) > tol:
                    continue
                blo, bhi = (bx1, bx2) if horizontal else (by1, by2)
                ov = _overlap_1d(lo, hi, blo, bhi)
                if ov is not None:
                    shared.append((ov[0], ov[1], b))
            shared.sort(key=lambda s: s[0])
            # walk the side: gaps between shared intervals are exterior
            cursor = lo
            pieces: List[Tuple[float, float, Optional[dict]]] = []
            for s0, s1, b in shared:
                if s0 - cursor > tol:
                    pieces.append((cursor, s0, None))
                pieces.append((max(s0, cursor), s1, b))
                cursor = max(cursor, s1)
            if hi - cursor > tol:
                pieces.append((cursor, hi, None))
            for p0, p1, b in pieces:
                if p1 - p0 <= tol:
                    continue
                if b is not None and a["room_id"] > b["room_id"] and \
                        "balcony" not in (a["room"]["room_type"], b["room"]["room_type"]):
                    continue          # emitted from the other room
                if horizontal:
                    seg = (p0, const, p1, const)
                else:
                    seg = (const, p0, const, p1)
                kind = "int" if b is not None else "ext"
                # A room–balcony party wall is a façade, not a partition;
                # the balcony's own free sides are a railing.
                if b is not None and ("balcony" in (a["room"]["room_type"], b["room"]["room_type"])):
                    kind = "ext"
                    if a["room"]["room_type"] == "balcony":
                        # emit the façade from the indoor room's side
                        continue
                elif b is None and a["room"]["room_type"] == "balcony":
                    kind = "rail"
                walls.append({"x1": seg[0], "y1": seg[1], "x2": seg[2], "y2": seg[3],
                              "kind": kind, "rooms": (a["room_id"], b["room_id"] if b else None),
                              "side": side, "out": _SIDE_NORMAL[side],
                              "types": (a["room"]["room_type"], b["room"]["room_type"] if b else None)})
    return walls


def _seg_len(w: dict) -> float:
    return math.hypot(w["x2"] - w["x1"], w["y2"] - w["y1"])


# ═════════════════════════════════════════════════════════════════════
#  Openings — doors, open passages, windows
# ═════════════════════════════════════════════════════════════════════

_PRIVACY = {"bathroom": 4, "bedroom": 3, "kitchen": 2, "living": 1, "balcony": 0}


def _sub_segment(w: dict, t0: float, t1: float) -> Tuple[float, float, float, float]:
    """Part of wall ``w`` between parameters t0..t1 (0..1 along the wall)."""
    return (w["x1"] + (w["x2"] - w["x1"]) * t0, w["y1"] + (w["y2"] - w["y1"]) * t0,
            w["x1"] + (w["x2"] - w["x1"]) * t1, w["y1"] + (w["y2"] - w["y1"]) * t1)


def _door_on(w: dict, metre: float, into_room: str, toward_pt: Tuple[float, float],
             kind: str = "door") -> Optional[dict]:
    """A door on wall ``w`` near the end closest to ``toward_pt``, swinging
    into ``into_room``."""
    L = _seg_len(w)
    dw = DOOR_W_M * metre
    off = DOOR_OFF_M * metre
    if L < dw + 0.05 * metre:
        return None
    if L < dw + 2 * off:
        off = (L - dw) / 2.0
    # which end of the wall is nearer the point we come from?
    d1 = math.hypot(w["x1"] - toward_pt[0], w["y1"] - toward_pt[1])
    d2 = math.hypot(w["x2"] - toward_pt[0], w["y2"] - toward_pt[1])
    if d1 <= d2:
        t0, t1 = off / L, (off + dw) / L
        hinge_t = t0
    else:
        t0, t1 = (L - off - dw) / L, (L - off) / L
        hinge_t = t1
    x1, y1, x2, y2 = _sub_segment(w, t0, t1)
    hx, hy = _sub_segment(w, hinge_t, hinge_t)[:2]
    return {"kind": kind, "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            "hinge": (hx, hy), "into": into_room, "wall": w, "width": dw}


def _room_centre(rects_by_id: Dict[str, dict], rid: str) -> Tuple[float, float]:
    r = rects_by_id[rid]
    return (r["cx"], r["cy"])


def build_openings(rects: List[dict], walls: List[dict], edges: List[dict],
                   metre: float, wwr_by_room: Dict[str, float]) -> List[dict]:
    rects_by_id = {r["room_id"]: r for r in rects}
    openings: List[dict] = []
    used: Dict[int, List[Tuple[float, float]]] = {}      # wall id → occupied (t0, t1)

    # group centre: doors are hung at the wall end nearest to it (so they
    # sit next to the corner the circulation comes from)
    gx = sum(r["cx"] for r in rects) / max(1, len(rects))
    gy = sum(r["cy"] for r in rects) / max(1, len(rects))
    living = next((r for r in rects if r["room"]["room_type"] == "living"), None)
    hub = (living["cx"], living["cy"]) if living else (gx, gy)

    def shared_walls(a: str, b: str) -> List[dict]:
        return [w for w in walls if set(w["rooms"]) == {a, b}]

    def occupy(w: dict, o: dict) -> None:
        L = _seg_len(w) or 1.0
        t0 = math.hypot(o["x1"] - w["x1"], o["y1"] - w["y1"]) / L
        t1 = math.hypot(o["x2"] - w["x1"], o["y2"] - w["y1"]) / L
        used.setdefault(id(w), []).append((min(t0, t1), max(t0, t1)))

    # 1. doors / open passages on connectivity edges
    entrance_room: Optional[str] = None
    for e in edges:
        a, b = e.get("source"), e.get("target")
        if a is None or b is None:
            continue
        if str(a).startswith("outside") or str(b).startswith("outside"):
            entrance_room = b if str(a).startswith("outside") else a
            continue
        if a not in rects_by_id or b not in rects_by_id:
            continue
        ws = shared_walls(a, b)
        if not ws:
            continue
        w = max(ws, key=_seg_len)
        ta, tb = rects_by_id[a]["room"]["room_type"], rects_by_id[b]["room"]["room_type"]
        if frozenset({ta, tb}) in OPEN_PAIRS:
            for ww in ws:
                openings.append({"kind": "open", "x1": ww["x1"], "y1": ww["y1"],
                                 "x2": ww["x2"], "y2": ww["y2"], "wall": ww,
                                 "rooms": (a, b)})
                used.setdefault(id(ww), []).append((0.0, 1.0))
            continue
        into = a if _PRIVACY.get(ta, 1) >= _PRIVACY.get(tb, 1) else b
        other = b if into == a else a
        kind = "balcony_door" if "balcony" in (ta, tb) else "door"
        if kind == "balcony_door":
            into = a if ta != "balcony" else b       # balcony doors swing inwards
        d = _door_on(w, metre, into, _room_centre(rects_by_id, other) if kind != "balcony_door" else hub, kind)
        if d is not None:
            d["rooms"] = (a, b)
            openings.append(d)
            occupy(w, d)

    # 2. entrance on the longest exterior wall of the entry room
    if entrance_room is None and living is not None:
        entrance_room = living["room_id"]
    if entrance_room in rects_by_id:
        cands = [w for w in walls if w["kind"] == "ext" and w["rooms"][0] == entrance_room
                 and w["rooms"][1] is None]
        cands.sort(key=_seg_len, reverse=True)
        for w in cands:
            d = _door_on(w, metre, entrance_room, hub, "entry")
            if d is not None:
                d["rooms"] = (entrance_room, None)
                openings.append(d)
                occupy(w, d)
                break

    # 3. windows on the remaining exterior walls, sized by the planned WWR
    for w in walls:
        if w["kind"] != "ext" or w["rooms"][1] is not None:
            continue
        rid = w["rooms"][0]
        rt = rects_by_id[rid]["room"]["room_type"]
        if rt == "balcony":
            continue
        wwr = float(wwr_by_room.get(rid, 0.0) or 0.0)
        if wwr <= 0.0:
            continue
        L = _seg_len(w)
        Lm = L / metre
        if Lm < 1.2:
            continue
        # free parameter interval: the largest stretch not used by a door
        occ = sorted(used.get(id(w), []))
        free: List[Tuple[float, float]] = []
        cur = 0.0
        for o0, o1 in occ:
            if o0 - cur > 0:
                free.append((cur, o0))
            cur = max(cur, o1)
        if 1.0 - cur > 0:
            free.append((cur, 1.0))
        if not free:
            continue
        f0, f1 = max(free, key=lambda f: f[1] - f[0])
        free_m = (f1 - f0) * Lm
        win_m = min(WIN_FRAC_PER_WWR * wwr * Lm, WIN_MAX_FRAC * free_m)
        if win_m < WIN_MIN_M or free_m < WIN_MIN_M + 0.3:
            continue
        mid = (f0 + f1) / 2.0
        half = (win_m / Lm) / 2.0
        x1, y1, x2, y2 = _sub_segment(w, mid - half, mid + half)
        openings.append({"kind": "window", "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                         "wall": w, "rooms": (rid, None), "wwr": wwr})
    return openings


# ═════════════════════════════════════════════════════════════════════
#  Furniture
# ═════════════════════════════════════════════════════════════════════

# Each piece: ("name", width_along_wall_m, depth_into_room_m)
# Drawn primitives are produced in reference coordinates by _draw_piece.

def _inner_rect(r: dict, metre: float, inset_m: float = 0.08):
    x0, y0, x1, y1 = _bounds(r)
    d = inset_m * metre
    return (x0 + d, y0 + d, x1 - d, y1 - d)


def _place_against(inner, side: str, w: float, d: float, at: float):
    """Rectangle of size w×d against ``side`` of ``inner``; ``at`` ∈ [0,1]
    positions it along that side (0 = start corner, 1 = end corner)."""
    ix0, iy0, ix1, iy1 = inner
    if side == "N":
        x = ix0 + (ix1 - ix0 - w) * at; return (x, iy0, x + w, iy0 + d)
    if side == "S":
        x = ix0 + (ix1 - ix0 - w) * at; return (x, iy1 - d, x + w, iy1)
    if side == "W":
        y = iy0 + (iy1 - iy0 - w) * at; return (ix0, y, ix0 + d, y + w)
    y = iy0 + (iy1 - iy0 - w) * at
    return (ix1 - d, y, ix1, y + w)


def _fits(box, inner, blocked: List, placed: List) -> bool:
    if box[0] < inner[0] - EPS or box[1] < inner[1] - EPS or box[2] > inner[2] + EPS or box[3] > inner[3] + EPS:
        return False
    return not any(_rect_hits(box, b) for b in blocked) and not any(_rect_hits(box, p) for p in placed)


def _try_place(inner, sides: List[str], w: float, d: float, blocked, placed,
               ats=(0.5, 0.0, 1.0, 0.25, 0.75)):
    for side in sides:
        for at in ats:
            box = _place_against(inner, side, w, d, at)
            if _fits(box, inner, blocked, placed):
                return side, box
    return None, None


def _door_sides(r: dict, openings: List[dict], metre: float) -> Tuple[List[str], List]:
    """Sides of room ``r`` that carry a door / open passage, and the
    clearance boxes (in reference coords) furniture must keep free."""
    rid = r["room_id"]
    x0, y0, x1, y1 = _bounds(r)
    sides: List[str] = []
    blocked: List = []
    depth = DOOR_CLEAR_M * metre
    for o in openings:
        if o["kind"] == "window" or rid not in (o.get("rooms") or ()):
            continue
        w = o["wall"]
        # which side of *this* room is the wall on?
        if abs(o["y1"] - o["y2"]) < EPS:          # horizontal wall
            side = "N" if abs(o["y1"] - y0) < abs(o["y1"] - y1) else "S"
            lo, hi = min(o["x1"], o["x2"]), max(o["x1"], o["x2"])
            if o["kind"] == "open":
                lo, hi = lo, hi
            else:
                lo -= depth * 0.5; hi += depth * 0.5
            blocked.append((lo, y0, hi, y0 + depth) if side == "N" else (lo, y1 - depth, hi, y1))
        else:
            side = "W" if abs(o["x1"] - x0) < abs(o["x1"] - x1) else "E"
            lo, hi = min(o["y1"], o["y2"]), max(o["y1"], o["y2"])
            if o["kind"] != "open":
                lo -= depth * 0.5; hi += depth * 0.5
            blocked.append((x0, lo, x0 + depth, hi) if side == "W" else (x1 - depth, lo, x1, hi))
        sides.append(side)
    return sides, blocked


def _ordered_sides(prefer_opposite: List[str]) -> List[str]:
    opp = {"N": "S", "S": "N", "E": "W", "W": "E"}
    order = ["S", "N", "W", "E"]
    first = [opp[s] for s in prefer_opposite if s in opp]
    rest = [s for s in order if s not in first and s not in prefer_opposite]
    return first + rest + [s for s in prefer_opposite if s in order]


def furnish_room(r: dict, openings: List[dict], metre: float) -> List[dict]:
    """Furniture pieces for one room: list of dicts with ``name`` and
    ``prims`` (drawing primitives in reference coordinates)."""
    rt = r["room"]["room_type"]
    inner = _inner_rect(r, metre)
    iw, ih = inner[2] - inner[0], inner[3] - inner[1]
    if iw < 1.2 * metre or ih < 1.2 * metre:
        return []
    door_sides, blocked = _door_sides(r, openings, metre)
    sides = _ordered_sides(door_sides)
    placed: List = []
    pieces: List[dict] = []
    m = metre

    def add(name, side, box, extra=None):
        placed.append(box)
        pieces.append({"name": name, "side": side, "box": box, "prims": _draw_piece(name, side, box, m, extra)})

    if rt == "bedroom":
        bw = 1.6 if min(iw, ih) >= 2.8 * m else 1.0
        side, box = _try_place(inner, sides, bw * m, 2.0 * m, blocked, placed)
        if box:
            add("bed", side, box)
            for at in (0.0, 1.0):
                # nightstands either side of the bed on the same wall
                nb = _nudge_beside(box, side, 0.5 * m, 0.5 * m, at)
                if _fits(nb, inner, blocked, placed):
                    add("nightstand", side, nb)
        side2, box2 = _try_place(inner, [s for s in sides if s != side], min(1.8 * m, max(iw, ih) * 0.5), 0.6 * m, blocked, placed)
        if box2:
            add("wardrobe", side2, box2)
    elif rt == "living":
        side, box = _try_place(inner, sides, 2.2 * m, 0.9 * m, blocked, placed)
        if box:
            add("sofa", side, box)
            ct = _in_front(box, side, 1.2 * m, 0.6 * m, 0.45 * m)
            if _fits(ct, inner, blocked, placed):
                add("coffee_table", side, ct)
            opp = {"N": "S", "S": "N", "E": "W", "W": "E"}[side]
            s3, tv = _try_place(inner, [opp], 1.6 * m, 0.45 * m, blocked, placed)
            if tv:
                add("tv", s3, tv)
        if (iw * ih) / (m * m) >= 16.0:
            s4, dt = _try_place(inner, sides, 1.6 * m + 1.0 * m, 0.9 * m + 1.0 * m, blocked, placed, ats=(0.0, 1.0, 0.5))
            if dt:
                add("dining", s4, dt)
    elif rt == "kitchen":
        long_sides = ["N", "S"] if iw >= ih else ["W", "E"]
        pref = [s for s in long_sides if s not in door_sides] + [s for s in sides if s not in long_sides]
        length = (iw if iw >= ih else ih) - 0.1 * m
        side, box = _try_place(inner, pref, length, 0.6 * m, blocked, placed, ats=(0.5,))
        if box is None:
            side, box = _try_place(inner, pref, length * 0.6, 0.6 * m, blocked, placed)
        if box:
            add("counter", side, box)
            # fridge at the end of the counter, turning the corner
            opp_axis = ["W", "E"] if side in ("N", "S") else ["N", "S"]
            s5, fr = _try_place(inner, opp_axis, 0.7 * m, 0.7 * m, blocked, placed, ats=(0.0, 1.0))
            if fr:
                add("fridge", s5, fr)
    elif rt == "bathroom":
        side, box = _try_place(inner, sides, 0.45 * m, 0.7 * m, blocked, placed, ats=(0.0, 1.0, 0.5))
        if box:
            add("wc", side, box)
            bs = _nudge_beside(box, side, 0.6 * m, 0.45 * m, 1.0)
            if _fits(bs, inner, blocked, placed):
                add("basin", side, bs)
            else:
                s6, bs = _try_place(inner, sides, 0.6 * m, 0.45 * m, blocked, placed)
                if bs:
                    add("basin", s6, bs)
        if max(iw, ih) >= 2.0 * m and (iw * ih) / (m * m) >= 4.5:
            s7, tub = _try_place(inner, sides, 1.7 * m, 0.75 * m, blocked, placed, ats=(0.0, 1.0, 0.5))
            if tub:
                add("bathtub", s7, tub)
            else:
                s7, sh = _try_place(inner, sides, 0.9 * m, 0.9 * m, blocked, placed, ats=(0.0, 1.0))
                if sh:
                    add("shower", s7, sh)
        else:
            s7, sh = _try_place(inner, sides, 0.9 * m, 0.9 * m, blocked, placed, ats=(0.0, 1.0))
            if sh:
                add("shower", s7, sh)
    elif rt == "balcony":
        s8, tb = _try_place(inner, sides, 0.6 * m, 0.6 * m, blocked, placed, ats=(0.5, 0.0, 1.0))
        if tb:
            add("bistro", s8, tb)
    return pieces


def _nudge_beside(box, side: str, w: float, d: float, at: float):
    """A w×d box next to ``box`` along the same wall (at=0 before, 1 after)."""
    x0, y0, x1, y1 = box
    if side in ("N", "S"):
        x = x0 - w if at == 0.0 else x1
        y = y0 if side == "N" else y1 - d
        return (x, y, x + w, y + d)
    y = y0 - w if at == 0.0 else y1
    x = x0 if side == "W" else x1 - d
    return (x, y, x + d, y + w)


def _in_front(box, side: str, w: float, d: float, gap: float):
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    if side == "N":
        return (cx - w / 2, y1 + gap, cx + w / 2, y1 + gap + d)
    if side == "S":
        return (cx - w / 2, y0 - gap - d, cx + w / 2, y0 - gap)
    if side == "W":
        return (x1 + gap, cy - w / 2, x1 + gap + d, cy + w / 2)
    return (x0 - gap - d, cy - w / 2, x0 - gap, cy + w / 2)


def _draw_piece(name: str, side: str, box, m: float, extra=None) -> List[tuple]:
    """Primitive list for a furniture block: ('rect', x, y, w, h),
    ('circle', cx, cy, r), ('line', x1, y1, x2, y2)."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    P: List[tuple] = [("rect", x0, y0, w, h)]
    horiz = side in ("N", "S")
    if name == "bed":
        # pillows at the wall end, a fold line a third of the way down
        if side == "N":
            P += [("rect", x0 + 0.1 * m, y0 + 0.1 * m, w / 2 - 0.15 * m, 0.4 * m),
                  ("rect", x0 + w / 2 + 0.05 * m, y0 + 0.1 * m, w / 2 - 0.15 * m, 0.4 * m),
                  ("line", x0, y0 + 0.75 * m, x1, y0 + 0.75 * m)]
        elif side == "S":
            P += [("rect", x0 + 0.1 * m, y1 - 0.5 * m, w / 2 - 0.15 * m, 0.4 * m),
                  ("rect", x0 + w / 2 + 0.05 * m, y1 - 0.5 * m, w / 2 - 0.15 * m, 0.4 * m),
                  ("line", x0, y1 - 0.75 * m, x1, y1 - 0.75 * m)]
        elif side == "W":
            P += [("rect", x0 + 0.1 * m, y0 + 0.1 * m, 0.4 * m, h / 2 - 0.15 * m),
                  ("rect", x0 + 0.1 * m, y0 + h / 2 + 0.05 * m, 0.4 * m, h / 2 - 0.15 * m),
                  ("line", x0 + 0.75 * m, y0, x0 + 0.75 * m, y1)]
        else:
            P += [("rect", x1 - 0.5 * m, y0 + 0.1 * m, 0.4 * m, h / 2 - 0.15 * m),
                  ("rect", x1 - 0.5 * m, y0 + h / 2 + 0.05 * m, 0.4 * m, h / 2 - 0.15 * m),
                  ("line", x1 - 0.75 * m, y0, x1 - 0.75 * m, y1)]
        if w < 1.3 * m or h < 1.3 * m:   # single bed: one pillow
            P = [p for p in P[:2]] + [P[3]]
    elif name == "sofa":
        if horiz:
            back = 0.25 * m
            by = y0 if side == "N" else y1 - back
            P += [("rect", x0, by, w, back),
                  ("line", x0 + w / 2, y0 + (back if side == "N" else 0), x0 + w / 2, y1 - (0 if side == "N" else back))]
        else:
            back = 0.25 * m
            bx = x0 if side == "W" else x1 - back
            P += [("rect", bx, y0, back, h),
                  ("line", x0 + (back if side == "W" else 0), y0 + h / 2, x1 - (0 if side == "W" else back), y0 + h / 2)]
    elif name == "dining":
        # table in the middle of the block, chairs around
        tw, td = (1.6 * m, 0.9 * m) if w >= h else (0.9 * m, 1.6 * m)
        tx, ty = x0 + (w - tw) / 2, y0 + (h - td) / 2
        P = [("rect", tx, ty, tw, td)]
        c = 0.42 * m
        if tw >= td:
            for i in range(2):
                cx = tx + tw * (0.25 + 0.5 * i) - c / 2
                P += [("rect", cx, ty - c - 0.05 * m, c, c), ("rect", cx, ty + td + 0.05 * m, c, c)]
        else:
            for i in range(2):
                cy = ty + td * (0.25 + 0.5 * i) - c / 2
                P += [("rect", tx - c - 0.05 * m, cy, c, c), ("rect", tx + tw + 0.05 * m, cy, c, c)]
    elif name == "counter":
        if horiz:
            # sink (rect) left third, hob (4 rings) right third
            sw, sd = 0.5 * m, 0.4 * m
            P += [("rect", x0 + w * 0.2 - sw / 2, y0 + (h - sd) / 2, sw, sd)]
            hx = x0 + w * 0.72
            for dx in (-0.12, 0.12):
                for dy in (-0.12, 0.12):
                    P += [("circle", hx + dx * m, y0 + h / 2 + dy * m, 0.09 * m)]
        else:
            sw, sd = 0.4 * m, 0.5 * m
            P += [("rect", x0 + (w - sw) / 2, y0 + h * 0.2 - sd / 2, sw, sd)]
            hy = y0 + h * 0.72
            for dx in (-0.12, 0.12):
                for dy in (-0.12, 0.12):
                    P += [("circle", x0 + w / 2 + dx * m, hy + dy * m, 0.09 * m)]
    elif name == "fridge":
        P += [("line", x0, y0, x1, y1), ("line", x0, y1, x1, y0)]
    elif name == "wc":
        if horiz:
            cy = y0 + (h * 0.35 if side == "N" else h * 0.65)
            P = [("rect", x0 + w * 0.15, y0 if side == "N" else y1 - 0.25 * m, w * 0.7, 0.25 * m),
                 ("circle", x0 + w / 2, cy + (0.1 * m if side == "N" else -0.1 * m), w * 0.42)]
        else:
            cx = x0 + (w * 0.35 if side == "W" else w * 0.65)
            P = [("rect", x0 if side == "W" else x1 - 0.25 * m, y0 + h * 0.15, 0.25 * m, h * 0.7),
                 ("circle", cx + (0.1 * m if side == "W" else -0.1 * m), y0 + h / 2, h * 0.42)]
    elif name == "basin":
        P += [("circle", x0 + w / 2, y0 + h / 2, min(w, h) * 0.32)]
    elif name == "shower":
        P += [("line", x0, y0, x1, y1), ("circle", x0 + w / 2, y0 + h / 2, 0.06 * m)]
    elif name == "bathtub":
        P += [("rect", x0 + 0.08 * m, y0 + 0.08 * m, w - 0.16 * m, h - 0.16 * m)]
    elif name == "wardrobe":
        if horiz:
            P += [("line", x0, y0 + h / 2, x1, y0 + h / 2)]
        else:
            P += [("line", x0 + w / 2, y0, x0 + w / 2, y1)]
    elif name == "bistro":
        P = [("circle", x0 + w / 2, y0 + h / 2, 0.25 * m),
             ("rect", x0 - 0.35 * m, y0 + h / 2 - 0.18 * m, 0.3 * m, 0.36 * m),
             ("rect", x1 + 0.05 * m, y0 + h / 2 - 0.18 * m, 0.3 * m, 0.36 * m)]
    return P


# ═════════════════════════════════════════════════════════════════════
#  The synthesis — packing + everything above, recorded as frames
# ═════════════════════════════════════════════════════════════════════

class Synthesis:
    """Result of :func:`synthesize`. ``frames[-1]`` is the final plan."""

    def __init__(self) -> None:
        self.metre: float = 1.0
        self.box: Tuple[float, float, float, float] = (0.0, 0.0, 600.0, 600.0)
        self.rects: List[dict] = []          # final rectangles (rectangle mode)
        self.circles: List[dict] = []        # packed circles (bubble mode)
        self.walls: List[dict] = []
        self.openings: List[dict] = []
        self.furniture: List[dict] = []      # pieces with room_id
        self.removed: List[str] = []         # balconies dropped (detached)
        self.halls: List[dict] = []          # circulation cells (empty zones)
        self.audit: dict = {}                # realized-zone audit
        self.area_scale_factor: float = 1.0  # rooms × this to fit beside the corridors
        self.frames: List[dict] = []
        self.phases: List[Tuple[int, str]] = []   # (first frame index, label)

    # frame helpers
    def final(self) -> dict:
        return self.frames[-1] if self.frames else {"shapes": [], "walls": 0.0,
                                                    "openings": 0, "furniture": 0, "label": ""}


def _snapshot(shapes: List[dict], kind: str, morph: float = 1.0) -> List[dict]:
    out = []
    for s in shapes:
        k = "rect" if kind == "hall" else kind
        d = {"room_id": s["room_id"], "room": s["room"], "cx": s["cx"], "cy": s["cy"], "kind": k}
        if "r" in s:
            d["r"] = s["r"]
        if "w" in s:
            d["w"] = s["w"]; d["h"] = s["h"]
        d["morph"] = s.get("morph", morph)
        out.append(d)
    return out


# ═════════════════════════════════════════════════════════════════════
#  Zone-aware compaction (option C)
# ═════════════════════════════════════════════════════════════════════

ZONE_FACADE_SET: Dict[int, frozenset] = {
    0: frozenset("NW"), 1: frozenset("N"), 2: frozenset("NE"),
    3: frozenset("W"), 4: frozenset(), 5: frozenset("E"),
    6: frozenset("SW"), 7: frozenset("S"), 8: frozenset("SE"),
}


def zone_target(r: dict, zone: int, W: float, H: float) -> Tuple[float, float]:
    """Centre a room should reach for its zone: flush with the plan corner
    / edge its zone exposes, centred otherwise."""
    F = ZONE_FACADE_SET.get(zone, frozenset())
    tx = r["w"] / 2 if "W" in F else (W - r["w"] / 2 if "E" in F else W / 2)
    ty = r["h"] / 2 if "N" in F else (H - r["h"] / 2 if "S" in F else H / 2)
    return tx, ty


def zone_place(rects: List[dict], zone_of: Dict[str, int], W: float, H: float,
               metre: float, tol: float, margin: float = 0.15) -> None:
    """Place the rooms one by one — corner rooms first, then edge, then
    interior; larger first — each at the free position nearest its zone
    anchor (L1 distance on a 0.25 m grid), inside the plan frame widened
    by ``margin`` on every side."""
    def rank(r):
        z = zone_of.get(r["room_id"], 4)
        return (-len(ZONE_FACADE_SET.get(z, ())), -(r["w"] * r["h"]))
    placed: List[dict] = []
    step = 0.25 * metre
    mx, my = margin * W, margin * H
    for r in sorted(rects, key=rank):
        z = zone_of.get(r["room_id"], 4)
        tx, ty = zone_target(r, z, W, H)
        hw, hh = r["w"] / 2, r["h"] / 2
        best = None
        cx = -mx + hw
        while cx <= W + mx - hw + EPS:
            cy = -my + hh
            while cy <= H + my - hh + EPS:
                cost = abs(cx - tx) + abs(cy - ty)
                if best is None or cost < best[0]:
                    box = (cx - hw, cy - hh, cx + hw, cy + hh)
                    if not any(_rect_hits(box, _bounds(o), tol) for o in placed):
                        best = (cost, cx, cy)
                cy += step
            cx += step
        if best is not None:
            r["cx"], r["cy"] = best[1], best[2]
        placed.append(r)


def zone_compaction(rects: List[dict], zone_of: Dict[str, int], W: float, H: float,
                    passes: int = 10, tol: float = 0.5, record=None) -> None:
    """Slide every room, one axis at a time, towards its zone anchor until
    it meets a neighbour or reaches the anchor. Corner rooms go first
    (they fix the plan's corners), then edge rooms, then interior ones;
    larger rooms before smaller ones."""
    def rank(r):
        z = zone_of.get(r["room_id"], 4)
        n_f = len(ZONE_FACADE_SET.get(z, ()))
        return (-n_f, -(r["w"] * r["h"]))
    order = sorted(rects, key=rank)
    for _ in range(passes):
        moved = 0.0
        for r in order:
            z = zone_of.get(r["room_id"], 4)
            tx, ty = zone_target(r, z, W, H)
            others = [o for o in rects if o is not r]
            for axis, t in (("x", tx), ("y", ty)):
                c = r["cx"] if axis == "x" else r["cy"]
                if abs(t - c) < EPS:
                    continue
                direction = 1 if t > c else -1
                limit = _slide_limit(r, others, axis, direction, tol)
                step = min(limit, abs(t - c))
                if step > EPS:
                    if axis == "x":
                        r["cx"] += direction * step
                    else:
                        r["cy"] += direction * step
                    moved += step
        if record is not None:
            record()
        if moved < tol:
            break


def exposure_by_side(rects: List[dict], r: dict, tol: float) -> Dict[str, float]:
    """Length of each side of ``r`` not covered by another rectangle's
    opposite side (what the wall builder will call exterior)."""
    x0, y0, x1, y1 = _bounds(r)
    out = {}
    for side in ("N", "S", "E", "W"):
        horizontal = side in ("N", "S")
        lo, hi = (x0, x1) if horizontal else (y0, y1)
        const = {"N": y0, "S": y1, "W": x0, "E": x1}[side]
        covered: List[Tuple[float, float]] = []
        for o in rects:
            if o is r:
                continue
            ox0, oy0, ox1, oy1 = _bounds(o)
            oconst = {"N": oy1, "S": oy0, "W": ox1, "E": ox0}[side]
            if abs(oconst - const) > tol:
                continue
            ov = _overlap_1d(lo, hi, ox0 if horizontal else oy0, ox1 if horizontal else oy1)
            if ov:
                covered.append(ov)
        covered.sort()
        cov = 0.0; cur = lo
        for a, b in covered:
            if b > cur:
                cov += b - max(a, cur); cur = max(cur, b)
        out[side] = (hi - lo) - cov
    return out


def unwanted_exposure(rects: List[dict], zone_of: Dict[str, int], metre: float, tol: float) -> float:
    """Total exposed length on sides that the rooms' zones do not expose
    (only lengths that would count under the corpus rule)."""
    total = 0.0
    for r in rects:
        F = ZONE_FACADE_SET.get(zone_of.get(r["room_id"], 4), frozenset())
        ex = exposure_by_side(rects, r, tol)
        for side, L in ex.items():
            extent = r["w"] if side in ("N", "S") else r["h"]
            if side not in F and L >= max(1.0 * metre, 0.25 * extent):
                total += L
    return total


def exposure_repair(rects: List[dict], zone_of: Dict[str, int], metre: float,
                    W: float, H: float, tol: float, max_gap: float, record=None,
                    rounds: int = 6) -> None:
    """Close gaps that expose a side a room's zone keeps blind: for every
    such side, the nearest room across the gap (or the room itself) is
    slid to contact when that lowers the total unwanted exposure."""
    normal = {"N": (0, -1), "S": (0, 1), "W": (-1, 0), "E": (1, 0)}
    for _ in range(rounds):
        base = unwanted_exposure(rects, zone_of, metre, tol)
        if base <= 0:
            break
        improved = False
        for r in rects:
            F = ZONE_FACADE_SET.get(zone_of.get(r["room_id"], 4), frozenset())
            ex = exposure_by_side(rects, r, tol)
            x0, y0, x1, y1 = _bounds(r)
            for side, L in ex.items():
                extent = r["w"] if side in ("N", "S") else r["h"]
                if side in F or L < max(1.0 * metre, 0.25 * extent):
                    continue
                nx, ny = normal[side]
                # nearest room across this side within max_gap
                best = None
                for o in rects:
                    if o is r:
                        continue
                    ox0, oy0, ox1, oy1 = _bounds(o)
                    if side in ("N", "S"):
                        if _overlap_1d(x0, x1, ox0, ox1) is None:
                            continue
                        gap = (y0 - oy1) if side == "N" else (oy0 - y1)
                    else:
                        if _overlap_1d(y0, y1, oy0, oy1) is None:
                            continue
                        gap = (x0 - ox1) if side == "W" else (ox0 - x1)
                    if tol < gap <= max_gap and (best is None or gap < best[0]):
                        best = (gap, o)
                if best is None:
                    continue
                gap, o = best
                # try moving the neighbour towards us, then us towards it
                for mover, sgn in ((o, -1), (r, 1)):
                    axis = "y" if side in ("N", "S") else "x"
                    direction = (ny if axis == "y" else nx) * sgn
                    others = [q for q in rects if q is not mover]
                    limit = _slide_limit(mover, others, axis, direction, tol)
                    step = min(limit, gap)
                    if step <= EPS:
                        continue
                    key = "cx" if axis == "x" else "cy"
                    old = mover[key]
                    mover[key] += direction * step
                    new = unwanted_exposure(rects, zone_of, metre, tol)
                    if new < base - EPS:
                        base = new; improved = True
                        break
                    mover[key] = old
        if record is not None:
            record()
        if not improved:
            break


# ═════════════════════════════════════════════════════════════════════
#  Zone-preserving band layout
# ═════════════════════════════════════════════════════════════════════

HALL_TYPE   = "hall"
CORRIDOR_M  = 1.2          # width of a circulation band where a zone is empty
ZONE_RC     = {z: (z // 3, z % 3) for z in range(9)}


def _split_cell(cell, rooms_in, vertical: bool, corner_first: Optional[str]) -> List[dict]:
    """Tile one cell (x0, y0, x1, y1) with the given rooms, side by side
    (``vertical`` cuts) or stacked, widths/heights proportional to area.
    ``corner_first`` = 'W'/'E' (vertical) or 'N'/'S' (stacked): which end
    the first (corner) room takes."""
    x0, y0, x1, y1 = cell
    total = sum(r["_area"] for r in rooms_in) or 1.0
    out = []
    order = list(rooms_in)
    if corner_first in ("E", "S"):
        order = order[::-1]
    cur = x0 if vertical else y0
    for r in order:
        f = r["_area"] / total
        if vertical:
            w = (x1 - x0) * f
            out.append({**r, "cx": cur + w / 2, "cy": (y0 + y1) / 2, "w": w, "h": y1 - y0})
            cur += w
        else:
            h = (y1 - y0) * f
            out.append({**r, "cx": (x0 + x1) / 2, "cy": cur + h / 2, "w": x1 - x0, "h": h})
            cur += h
    return out


AR_FLEX = 2.5              # a room may be stretched at most this × its requested aspect


def band_layout(indoor: List[dict], zone_of: Dict[str, int], W: float, H: float,
                metre: float, rows: bool = True) -> Tuple[List[dict], List[dict], float]:
    """Subdivide the W × H plan so every room's exposed sides match its
    zone.

    Three bands run N→S (``rows=True``; the alternative runs W→E and is
    the transposed problem). Each band is cut into the W / centre / E
    cells of its zones; a cell with no room becomes a circulation band
    (hall) of width ``CORRIDOR_M`` so the rooms beside it keep the
    exposure their zone says — an empty band still gets a corridor,
    otherwise the rooms behind it would reach the façade. Band heights
    follow the zone areas (one common scale ``s`` fits the three bands
    into H beside the corridors); a band is made taller when a room in
    it would otherwise be stretched beyond ``AR_FLEX`` × its requested
    aspect, and the width that frees up goes to that band's halls.
    Rooms sharing a cell are tiled side by side along the façade (edge
    cells: every room keeps its façade; corner cells: the largest
    scored room takes the corner; W/E cells: stacked). Returns
    ``(rects, halls, s)``.
    """
    if not rows:
        tz = {rid: (ZONE_RC[z][1] * 3 + ZONE_RC[z][0]) for rid, z in zone_of.items()}
        rects, halls, s = band_layout(indoor, tz, H, W, metre, rows=True)
        for r in rects + halls:
            r["cx"], r["cy"], r["w"], r["h"] = r["cy"], r["cx"], r["h"], r["w"]
        return rects, halls, s
    cmin = CORRIDOR_M * metre
    cells: Dict[Tuple[int, int], List[dict]] = {}
    for r in indoor:
        z = zone_of.get(r["room_id"], 4)
        cells.setdefault(ZONE_RC[z], []).append(r)
    for lst in cells.values():                       # corner / scored room first, then by area
        lst.sort(key=lambda r: (r["room"]["room_type"] not in PRIMARY_TYPES, -r["_area"]))
    T = {rc: sum(r["_area"] for r in lst) for rc, lst in cells.items()}
    row_tot = [sum(T.get((r, c), 0.0) for c in range(3)) for r in range(3)]
    n_empty = [sum(1 for c in range(3) if (r, c) not in cells) for r in range(3)]

    def layout_mode(r: int, c: int) -> str:
        if r == 1 and c != 1:
            return "stack"                                # W / E edge: all keep the façade
        if r == 1:
            return "auto"
        return "side"                                     # N / S band: side by side

    def ar_req(room: dict) -> float:
        return max(0.3, min(3.0, float(room["room"].get("aspect_ratio", 1.2))))

    def heights(s: float) -> List[float]:
        hs = []
        for r in range(3):
            if row_tot[r] <= 0:
                hs.append(cmin); continue
            h = s * row_tot[r] / max(W - n_empty[r] * cmin, 0.5 * W)
            # tallest band any of its rooms needs to stay within AR_FLEX
            for c in range(3):
                lst = cells.get((r, c))
                if not lst:
                    continue
                mode = layout_mode(r, c)
                if mode == "auto":
                    mode = "side"
                Tc = T[(r, c)]
                for room in lst:
                    a = room["_area"] * s
                    arm = AR_FLEX * ar_req(room)
                    if mode == "side":
                        h = max(h, math.sqrt(a / arm))
                    else:
                        h = max(h, math.sqrt(s * Tc * Tc / (arm * room["_area"])))
            hs.append(h)
        return hs

    # common scale s: bisection on Σ h_r(s) = H (monotone in s)
    lo, hi = 0.05, 4.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if sum(heights(mid)) > H:
            hi = mid
        else:
            lo = mid
    s = (lo + hi) / 2
    hs = heights(s)
    rects: List[dict] = []
    halls: List[dict] = []
    y = 0.0
    for r in range(3):
        h = hs[r]
        if row_tot[r] <= 0:
            halls.append({"room": {"room_id": f"hall_r{r}", "room_type": HALL_TYPE}, "room_id": f"hall_r{r}",
                          "cx": W / 2, "cy": y + h / 2, "w": W, "h": h})
            y += h
            continue
        widths = []
        for c in range(3):
            widths.append(s * T[(r, c)] / h if (r, c) in cells else cmin)
        leftover = W - sum(widths)
        if leftover > 0:
            if n_empty[r] > 0:                      # widen this band's corridors
                for c in range(3):
                    if (r, c) not in cells:
                        widths[c] += leftover / n_empty[r]
            else:                                   # no hall here: rooms share it
                tot = sum(widths)
                widths = [wv + leftover * wv / tot for wv in widths]
        elif leftover < 0:                          # rounding: shrink proportionally
            tot = sum(widths)
            widths = [wv * W / tot for wv in widths]
        x = 0.0
        pending_hall = None                         # merge consecutive empty cells
        for c in range(3):
            w = widths[c]
            cell = (x, y, x + w, y + h)
            if (r, c) not in cells:
                if pending_hall is None:
                    pending_hall = [x, x + w]
                else:
                    pending_hall[1] = x + w
                x += w
                continue
            if pending_hall is not None:
                hx0, hx1 = pending_hall
                halls.append({"room": {"room_id": f"hall_{r}{c}", "room_type": HALL_TYPE},
                              "room_id": f"hall_{r}{c}", "cx": (hx0 + hx1) / 2, "cy": y + h / 2,
                              "w": hx1 - hx0, "h": h})
                pending_hall = None
            if (r, c) in cells:
                lst = cells[(r, c)]
                mode = layout_mode(r, c)
                if mode == "auto":
                    mode = "side" if h <= w else "stack"
                vertical = (mode == "side")
                first = ("W" if c == 0 else ("E" if c == 2 else None)) if vertical else \
                        ("N" if r == 0 else ("S" if r == 2 else None))
                rects += _split_cell(cell, lst, vertical, first)
            x += w
        if pending_hall is not None:
            hx0, hx1 = pending_hall
            halls.append({"room": {"room_id": f"hall_{r}e", "room_type": HALL_TYPE},
                          "room_id": f"hall_{r}e", "cx": (hx0 + hx1) / 2, "cy": y + h / 2,
                          "w": hx1 - hx0, "h": h})
        y += h
    return rects, halls, s


def _aspect_penalty(rects: List[dict]) -> float:
    pen = 0.0
    for r in rects:
        req = max(0.3, min(3.0, float(r["room"].get("aspect_ratio", 1.2))))
        real = r["w"] / max(r["h"], 1e-6)
        pen += abs(math.log(real / req))
    return pen


# ═════════════════════════════════════════════════════════════════════
#  Exposure audit — the dataset's zone rule applied to the drawing
# ═════════════════════════════════════════════════════════════════════

_ROW = {"N": 0, "S": 2}
_COL = {"W": 0, "E": 2}


def realized_zone(syn: "Synthesis", rid: str) -> Tuple[int, Dict[str, float]]:
    """Zone of a drawn room under the exposure rule of the corpus: a side
    is exposed when ≥ max(1 m, 25 % of its extent) of it is exterior
    wall (a wall facing a balcony counts); none → interior, one → edge,
    two adjacent → corner, two opposite → the longer, three/four → the
    corner of the two longest adjacent exposures."""
    r = next(x for x in syn.rects if x["room_id"] == rid)
    ext = {"N": 0.0, "S": 0.0, "E": 0.0, "W": 0.0}
    for w in syn.walls:
        if w["rooms"][0] == rid and w["kind"] == "ext":
            ext[w["side"]] += _seg_len(w)
    extent = {"N": r["w"], "S": r["w"], "E": r["h"], "W": r["h"]}
    exposed = {sd for sd in ext if ext[sd] >= max(1.0 * syn.metre, 0.25 * extent[sd])}

    def zone(sides) -> int:
        rows_ = [_ROW[sd] for sd in sides if sd in _ROW]
        cols_ = [_COL[sd] for sd in sides if sd in _COL]
        return (rows_[0] if rows_ else 1) * 3 + (cols_[0] if cols_ else 1)
    if not exposed:
        return 4, ext
    if len(exposed) == 1:
        return zone(exposed), ext
    if len(exposed) == 2:
        if exposed in ({"N", "S"}, {"E", "W"}):
            return zone({max(exposed, key=lambda k: ext[k])}), ext
        return zone(exposed), ext
    best = None
    for a in exposed:
        for b in exposed:
            if a < b and {a, b} not in ({"N", "S"}, {"E", "W"}):
                v = ext[a] + ext[b]
                if best is None or v > best[0]:
                    best = (v, {a, b})
    return zone(best[1]), ext


def audit_zones(syn: "Synthesis", schematic: dict) -> dict:
    """Compare predicted and realized zones; recompute QVCI / AQI with
    the realized zones when the schematic carries its ``site``."""
    rooms = [r for r in schematic.get("rooms", []) if r.get("zone_assignment") is not None]
    rects_ids = {r["room_id"] for r in syn.rects}
    per_room = []
    for r in rooms:
        if r["room_id"] not in rects_ids:
            continue
        rz, ext = realized_zone(syn, r["room_id"])
        per_room.append({"room_id": r["room_id"], "room_type": r["room_type"],
                         "zone": int(r["zone_assignment"]), "realized": int(rz),
                         "exposed": {k: round(v / syn.metre, 2) for k, v in ext.items()}})
    off = [d for d in per_room if d["zone"] != d["realized"]]
    out = {"rooms": per_room, "off_zone": off, "n": len(per_room)}
    site = schematic.get("site")
    comp = schematic.get("computed", {}) or {}
    if site and per_room:
        try:
            from models import zone_scoring as zs
            by_id = {r["room_id"]: r for r in syn.rects}
            zrooms = []
            for d in per_room:
                r = by_id[d["room_id"]]
                src = next(x for x in rooms if x["room_id"] == d["room_id"])
                wm, hm = r["w"] / syn.metre, r["h"] / syn.metre
                zrooms.append({"id": d["room_id"], "type": d["room_type"], "area": wm * hm,
                               "w": wm, "h": hm, "perim": 2 * (wm + hm),
                               "wwr": float(src.get("wwr", 0.0) or 0.0), "edr": 0.0,
                               "has_front_door": False})
            A, Q, L, zv = zs.tables(zrooms, site)
            w = zs.weights(zrooms)
            q0, a0 = zs.evaluate([d["zone"] + 1 for d in per_room], A, Q, w)
            q1, a1 = zs.evaluate([d["realized"] + 1 for d in per_room], A, Q, w)
            # apply the zone effect to the official (Stage-2) values
            out["qvci_pred"] = float(comp.get("qvci", q0))
            out["aqi_pred"] = float(comp.get("aqi", a0))
            out["qvci_real"] = round(out["qvci_pred"] + (q1 - q0), 4)
            out["aqi_real"] = round(out["aqi_pred"] + (a1 - a0), 4)
        except Exception:
            pass
    return out


# ═════════════════════════════════════════════════════════════════════
#  The synthesis — zone-preserving layout, recorded as frames
# ═════════════════════════════════════════════════════════════════════

def synthesize(schematic: dict, alignment: str, box_w: float, box_h: float,
               units_per_m: float = 1.0, wwr_by_type: Optional[Dict[str, float]] = None,
               record: bool = True) -> Synthesis:
    """Run the full schematic synthesis for one plan.

    The plan rectangle (``box_w`` × ``box_h`` reference units, the user's
    plan geometry) is the frame; the rooms subdivide it so that each
    room's exposed sides are the ones its zone exposes (see
    :func:`band_layout`); balconies hang outside on the façade the
    connectivity stage chose. ``alignment`` is accepted for API
    compatibility and ignored. The whole process is recorded as frames.
    """
    syn = Synthesis()
    rooms = schematic.get("rooms", []) or []
    if not rooms:
        return syn
    conn = schematic.get("source_connectivity") or {}
    stage3_size = float(conn.get("canvas_size", 200.0))
    stage3_by_id = {n["id"]: n for n in conn.get("nodes", []) if not n.get("is_direction")}
    edges = list(schematic.get("edges", []) or [])
    facades: Dict[str, Tuple[str, str]] = {
        n["id"]: (n.get("anchor"), n.get("facade")) for n in conn.get("nodes", [])
        if n.get("facade") and n.get("anchor")}
    zone_of: Dict[str, int] = {n["id"]: int(n["zone"]) for n in conn.get("nodes", [])
                               if n.get("zone") is not None}
    for r in rooms:                                   # fall back to the zone table
        if r["room_id"] not in zone_of and r.get("zone_assignment") is not None:
            zone_of[r["room_id"]] = int(r["zone_assignment"])

    # ── scale: the plan rectangle maps to the whole reference box ──
    ratios = [math.sqrt(float(r["area"]) / float(r["area_m2"])) for r in rooms
              if r.get("area_m2") and float(r["area_m2"]) > 0 and float(r.get("area", 0)) > 0]
    if ratios:
        units_per_m = sum(ratios) / len(ratios)
    plan_w_units = float(conn.get("plan_width", 0.0) or 0.0)
    plan_h_units = float(conn.get("plan_height", 0.0) or 0.0)
    if plan_w_units > 0 and plan_h_units > 0:
        area_scale = box_w / plan_w_units                 # ref units per plan unit
    else:
        area_scale = compute_area_scale(rooms, box_w, box_h, target_fill=1.0)
    metre = area_scale * units_per_m
    syn.metre = metre

    frames: List[dict] = []
    phases: List[Tuple[int, str]] = []

    def push(label: str, shapes: List[dict], kind: str, morph: float = 1.0,
             walls: float = 0.0, n_open: int = 0, n_furn: int = 0, hold: int = 1) -> None:
        if not record and frames:
            frames.clear()
        f = {"label": label, "shapes": _snapshot(shapes, kind, morph), "walls": walls,
             "openings": n_open, "furniture": n_furn}
        for _ in range(max(1, hold)):
            frames.append(f)

    def phase(label: str) -> None:
        phases.append((len(frames), label))

    # ── 0. bubbles at the connectivity positions ──────────────────
    circles: List[dict] = []
    src_rect = conn.get("plan_rect")
    for r in rooms:
        src = stage3_by_id.get(r["room_id"])
        if src is not None:
            if src_rect and len(src_rect) == 4 and src_rect[2] > 0 and src_rect[3] > 0:
                sx, sy, sw, sh = src_rect
                u = (float(src["x"]) - sx) / sw; v = (float(src["y"]) - sy) / sh
            else:
                u = float(src["x"]) / stage3_size; v = float(src["y"]) / stage3_size
            bx, by = u * box_w, v * box_h
        else:
            bx, by = box_w / 2.0, box_h / 2.0
        circles.append({"room": r, "room_id": r["room_id"], "cx": bx, "cy": by,
                        "r": math.sqrt(max(1.0, float(r.get("area", 0))) / math.pi) * area_scale,
                        "pinned": r["room_type"] in PRIMARY_TYPES,
                        "_area": max(1.0, float(r.get("area", 0))) * area_scale * area_scale})
    phase("Rooms at their connectivity positions")
    push("Rooms at their connectivity positions", circles, "circle", hold=8)
    syn.circles = copy.deepcopy(circles)

    # ── 1. rectangles at true scale, overlaps resolved ────────────
    rects: List[dict] = []
    for c in circles:
        ar = max(0.3, min(3.0, float(c["room"].get("aspect_ratio", 1.2))))
        rh = math.sqrt(c["_area"] / ar); rw = ar * rh
        rects.append({**c, "w": rw, "h": rh})
    indoor = [r for r in rects if r["room"]["room_type"] not in OUTDOOR_TYPES]
    halls: List[dict] = []
    syn.halls = halls
    syn.area_scale_factor = 1.0
    phase("Rectangles from area and aspect ratio")
    for k in range(1, 7):
        push("Rectangles from area and aspect ratio", rects, "rect", morph=k / 6.0)
    # sequential placement at the zone anchors: corner rooms first, each
    # at the free spot nearest its anchor (the rooms add up to the plan
    # area, so the frame is allowed to be exceeded by 15 %)
    before = [(r["cx"], r["cy"]) for r in rects]
    zone_place(indoor, zone_of, box_w, box_h, metre, tol=0.5)
    after = [(r["cx"], r["cy"]) for r in rects]
    phase("Placement at the zone anchors")
    for k in range(1, 11):
        t = k / 10.0; te = 1 - (1 - t) ** 3
        tmp = [{**r, "cx": b[0] + (a[0] - b[0]) * te, "cy": b[1] + (a[1] - b[1]) * te}
               for r, b, a in zip(rects, before, after)]
        push("Placement at the zone anchors", tmp, "rect")

    # ── 1b. zone-aware compaction: every room slides to its zone anchor
    #        (corner → plan corner, edge → edge midpoint, interior → centre)
    phase("Zone compaction — rooms slide to their zone anchor")
    zone_compaction(indoor, zone_of, box_w, box_h, passes=10, tol=0.5,
                    record=lambda: push("Zone compaction — rooms slide to their zone anchor", rects, "rect"))

    # ── 1c. exposure repair: close the gaps that would expose a side the
    #        room's zone does not expose (a Z2 room must be flanked)
    phase("Exposure repair — closing gaps on blind sides")
    exposure_repair(indoor, zone_of, metre, box_w, box_h, tol=0.5, max_gap=2.0 * metre,
                    record=lambda: push("Exposure repair — closing gaps on blind sides", rects, "rect"))

    # ── 1d. connected rooms that are close but not touching: nudge into
    #        contact when that costs no exposure
    phase("Contact — connected rooms close small gaps")
    contact_pass(indoor, edges, max_gap=1.5 * metre, tol=0.5,
                 record=lambda: push("Contact — connected rooms close small gaps", rects, "rect"),
                 guard=lambda: unwanted_exposure(indoor, zone_of, metre, 0.5))

    # ── 1e. balconies outside, on the façade chosen at connectivity ──
    glue_balconies(rects, facades, tol=0.5)
    phase("Balconies on their façade")
    push("Balconies on their façade", rects, "rect", hold=4)

    # ── 2. detached balconies are removed ─────────────────────────
    removed = detached_balconies(rects, facades, metre, tol=0.5)
    if removed:
        rects = [r for r in rects if r["room_id"] not in removed]
        edges = [e for e in edges if e.get("source") not in removed and e.get("target") not in removed]
        phase("Detached balcony removed")
        push("Detached balcony removed: " + ", ".join(sorted(removed)), halls + rects, "rect", hold=6)
    syn.removed = sorted(removed)
    syn.rects = rects
    all_shapes = halls + rects
    # frame box = plan rectangle plus whatever hangs outside it
    xs = [b for r in all_shapes for b in (_bounds(r)[0], _bounds(r)[2])] + [0.0, box_w]
    ys = [b for r in all_shapes for b in (_bounds(r)[1], _bounds(r)[3])] + [0.0, box_h]
    syn.box = (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    # ── 3. walls (halls take part so room–hall boundaries are partitions)
    tol = max(0.75, 0.03 * metre)
    walls = build_walls(rects + halls, tol)
    syn.walls = walls
    phase("Walls — exterior, then partitions")
    for k in range(1, 11):
        push("Walls — exterior, then partitions", all_shapes, "rect", walls=k / 10.0)

    # ── 4. openings ───────────────────────────────────────────────
    wwr_by_room = {}
    for r in rooms:
        v = r.get("wwr")
        if v is None and wwr_by_type:
            v = wwr_by_type.get(r["room_type"], 0.0)
        wwr_by_room[r["room_id"]] = float(v or 0.0)
    openings = build_openings(rects + halls, walls, edges, metre, wwr_by_room)
    openings += hall_doors(rects, halls, walls, edges, openings, metre)
    order = {"open": 0, "door": 1, "balcony_door": 1, "hall_door": 1, "entry": 2, "window": 3}
    openings.sort(key=lambda o: order.get(o["kind"], 9))
    syn.openings = openings
    phase("Doors from connectivity, windows from WWR")
    for k in range(1, len(openings) + 1):
        push("Doors from connectivity, windows from WWR", all_shapes, "rect", walls=1.0, n_open=k)

    # ── 5. furniture ──────────────────────────────────────────────
    furniture: List[dict] = []
    for r in rects:
        for piece in furnish_room(r, openings, metre):
            piece["room_id"] = r["room_id"]
            furniture.append(piece)
    syn.furniture = furniture
    phase("Furniture blocks")
    for k in range(1, len(furniture) + 1):
        push("Furniture blocks", all_shapes, "rect", walls=1.0, n_open=len(openings), n_furn=k)

    # ── 6. audit: realized zones under the corpus exposure rule ───
    syn.audit = audit_zones(syn, schematic)
    off = syn.audit.get("off_zone", [])
    final_label = "Schematic plan"
    if off:
        final_label += " · off-zone: " + ", ".join(f"{d['room_id']} Z{d['zone'] + 1}→Z{d['realized'] + 1}" for d in off)
    if syn.removed:
        final_label += f" · removed {', '.join(syn.removed)}"
    push(final_label, all_shapes, "rect", walls=1.0, n_open=len(openings), n_furn=len(furniture), hold=12)

    syn.frames = frames
    syn.phases = phases
    return syn


def hall_doors(rects: List[dict], halls: List[dict], walls: List[dict], edges: List[dict],
               openings: List[dict], metre: float) -> List[dict]:
    """Connectivity edges whose rooms do not share a wall are realized
    through the circulation: each of the two rooms gets a door to a hall
    it touches (one door per room–hall pair)."""
    if not halls:
        return []
    by_id = {r["room_id"]: r for r in rects + halls}
    satisfied = set()
    for o in openings:
        rs = o.get("rooms") or ()
        if len(rs) == 2 and rs[1] is not None:
            satisfied.add(frozenset(rs))
    hall_ids = {h["room_id"] for h in halls}
    done = set()
    out: List[dict] = []
    gx = sum(r["cx"] for r in rects) / max(1, len(rects))
    gy = sum(r["cy"] for r in rects) / max(1, len(rects))
    for e in edges:
        a, b = e.get("source"), e.get("target")
        if a not in by_id or b not in by_id or frozenset((a, b)) in satisfied:
            continue
        if str(a).startswith("outside") or str(b).startswith("outside"):
            continue
        for rid in (a, b):
            if by_id[rid]["room"]["room_type"] in OUTDOOR_TYPES:
                continue
            ws = [w for w in walls if w["kind"] == "int" and rid in w["rooms"]
                  and (w["rooms"][0] in hall_ids or w["rooms"][1] in hall_ids)]
            if not ws:
                continue
            w = max(ws, key=_seg_len)
            key = (rid, w["rooms"][0] if w["rooms"][0] in hall_ids else w["rooms"][1])
            if key in done:
                continue
            d = _door_on(w, metre, rid, (gx, gy), "hall_door")
            if d is not None:
                d["rooms"] = (rid, key[1])
                out.append(d)
                done.add(key)
    return out
