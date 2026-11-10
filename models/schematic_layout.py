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
     from door clearances. This step is a Python port of the furniture
     placement logic in RGeoLib, the research geometry library of the
     Hypergraph project by Ramon Elias Weber
     (https://github.com/ramonweber/hypergraph, MIT License, Copyright (c)
     2024 ramonweber; see THIRD_PARTY_NOTICES.md). Paper: Weber, Mueller &
     Reinhart, Nature Communications 15, 8327 (2024),
     https://doi.org/10.1038/s41467-024-52506-z

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
                 tol: float, record=None) -> None:
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
        if gap_x > tol and gap_y <= tol and gap_x <= max_gap:
            direction = 1 if ax0 >= mx1 else -1
            if _slide_limit(mover, others, "x", direction, tol) >= gap_x - tol:
                mover["cx"] += direction * gap_x; moved_any = True
        elif gap_y > tol and gap_x <= tol and gap_y <= max_gap:
            direction = 1 if ay0 >= my1 else -1
            if _slide_limit(mover, others, "y", direction, tol) >= gap_y - tol:
                mover["cy"] += direction * gap_y; moved_any = True
    if record is not None and moved_any:
        record()


def glue_balconies(rects: List[dict], facades: Dict[str, Tuple[str, str]], tol: float) -> None:
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
    for b in rects:
        if b["room"]["room_type"] not in OUTDOOR_TYPES:
            continue
        aid, side0 = facades.get(b["room_id"], (None, None))
        a = by_id.get(aid)
        if a is None or side0 not in ("N", "S", "E", "W"):
            placed.append(b); continue
        ax0, ay0, ax1, ay1 = _bounds(a)
        obstacles = [r for r in indoor if r is not a] + placed
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
# Python port of the furniture placement logic in RGeoLib by Ramon Elias
# Weber (https://github.com/ramonweber/hypergraph, MIT License, Copyright (c)
# 2024 ramonweber; full text in THIRD_PARTY_NOTICES.md).

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
        self.frames: List[dict] = []
        self.phases: List[Tuple[int, str]] = []   # (first frame index, label)

    # frame helpers
    def final(self) -> dict:
        return self.frames[-1] if self.frames else {"shapes": [], "walls": 0.0,
                                                    "openings": 0, "furniture": 0, "label": ""}


def _snapshot(shapes: List[dict], kind: str, morph: float = 1.0) -> List[dict]:
    out = []
    for s in shapes:
        d = {"room_id": s["room_id"], "room": s["room"], "cx": s["cx"], "cy": s["cy"], "kind": kind}
        if "r" in s:
            d["r"] = s["r"]
        if "w" in s:
            d["w"] = s["w"]; d["h"] = s["h"]
        d["morph"] = morph
        out.append(d)
    return out


def synthesize(schematic: dict, alignment: str, box_w: float, box_h: float,
               units_per_m: float = 1.0, wwr_by_type: Optional[Dict[str, float]] = None,
               record: bool = True) -> Synthesis:
    """Run the full schematic synthesis for one plan.

    ``units_per_m``: how many plan units make one metre (1 for m,
    3.2808 for ft, px-per-metre for px) — needed to give walls, doors
    and furniture real sizes.
    """
    syn = Synthesis()
    rooms = schematic.get("rooms", []) or []
    if not rooms:
        return syn
    conn = schematic.get("source_connectivity") or {}
    stage3_size = float(conn.get("canvas_size", 200.0))
    stage3_by_id = {n["id"]: n for n in conn.get("nodes", []) if not n.get("is_direction")}
    edges = list(schematic.get("edges", []) or [])

    # 0.40 (not the thesis 0.25): the compacted plan is tighter than the
    # loose packing, so it can fill more of the box and stay legible on
    # the cards.
    facades: Dict[str, Tuple[str, str]] = {
        n["id"]: (n.get("anchor"), n.get("facade")) for n in conn.get("nodes", [])
        if n.get("facade") and n.get("anchor")}

    area_scale = compute_area_scale(rooms, box_w, box_h, target_fill=0.40)
    # 1 plan-length-unit = area_scale reference units; 1 m = units_per_m plan units.
    # Prefer the exact ratio when rooms carry both ``area`` (plan units²) and ``area_m2``.
    ratios = [math.sqrt(float(r["area"]) / float(r["area_m2"])) for r in rooms
              if r.get("area_m2") and float(r["area_m2"]) > 0 and float(r.get("area", 0)) > 0]
    if ratios:
        units_per_m = sum(ratios) / len(ratios)
    metre = area_scale * units_per_m
    syn.metre = metre
    syn.box = (0.0, 0.0, box_w, box_h)

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
                        "pinned": r["room_type"] in PRIMARY_TYPES})
    phase("Rooms at their connectivity positions")
    push("Rooms at their connectivity positions", circles, "circle", hold=6)

    # ── 1. bubble packing (300 iterations, recorded every 15) ─────
    phase("Bubble packing — resolving overlaps")
    for _ in range(20):
        pack_circles(circles, w=box_w, h=box_h, pad=4, iters=15, gap=0.6, step=0.55)
        push("Bubble packing — resolving overlaps", circles, "circle")
    syn.circles = copy.deepcopy(circles)

    # ── 2. circles → rectangles by aspect ratio ───────────────────
    rects: List[dict] = []
    for c in circles:
        ar = max(0.3, min(3.0, float(c["room"].get("aspect_ratio", 1.2))))
        area = math.pi * c["r"] * c["r"]
        rh = math.sqrt(area / ar); rw = ar * rh
        rects.append({"room": c["room"], "room_id": c["room_id"], "cx": c["cx"], "cy": c["cy"],
                      "w": rw, "h": rh, "r": c["r"]})
    glue_balconies(rects, facades, tol=0.5)
    phase("Rectangles from area and aspect ratio")
    for k in range(1, 7):
        push("Rectangles from area and aspect ratio", rects, "rect", morph=k / 6.0)

    indoor = [r for r in rects if r["room"]["room_type"] not in OUTDOOR_TYPES]

    # ── 3. spring packing along the connectivity edges ────────────
    phase("Spring packing along connectivity edges")
    for _ in range(30):
        pack_rectangles_with_connectivity(indoor, edges, w=box_w, h=box_h, pad=4, cycles=1,
                                          centroid_pull=0.05, spring_k=0.3)
        glue_balconies(rects, facades, tol=0.5)
        push("Spring packing along connectivity edges", rects, "rect")

    # ── 4. compaction: indoor rooms slide into contact; balconies ride
    #       along on the façade the connectivity stage gave them
    def _rec(label):
        glue_balconies(rects, facades, tol=0.5)
        push(label, rects, "rect")
    phase("Compaction — rooms slide until they share walls")
    compact_rectangles(indoor, passes=8, tol=0.5,
                       record=lambda: _rec("Compaction — rooms slide until they share walls"))
    _resolve_all_overlaps(indoor, box_w, box_h, 4)

    # ── 4b. connected rooms that are close but not touching are
    #        nudged into contact so their door has a wall to sit on
    phase("Contact — connected rooms close small gaps")
    contact_pass(indoor, edges, max_gap=1.5 * metre, tol=0.5,
                 record=lambda: _rec("Contact — connected rooms close small gaps"))
    glue_balconies(rects, facades, tol=0.5)

    # ── 4c. a balcony that ended up detached from its room (no façade
    #        left for it) is removed rather than left floating
    removed = detached_balconies(rects, facades, metre, tol=0.5)
    if removed:
        rects = [r for r in rects if r["room_id"] not in removed]
        edges = [e for e in edges if e.get("source") not in removed and e.get("target") not in removed]
        indoor = [r for r in rects if r["room"]["room_type"] not in OUTDOOR_TYPES]
        phase("Detached balcony removed")
        push("Detached balcony removed: " + ", ".join(sorted(removed)), rects, "rect", hold=6)
    syn.removed = sorted(removed)

    # ── 5. alignment of the group in the plan box ─────────────────
    if alignment != "C":
        before = [(r["cx"], r["cy"]) for r in rects]
        align_group(rects, alignment, w=box_w, h=box_h, pad=4)
        after = [(r["cx"], r["cy"]) for r in rects]
        phase(f"Align group to {alignment}")
        for k in range(1, 6):
            t = k / 5.0
            tmp = []
            for r, (bx, by), (ax, ay) in zip(rects, before, after):
                tmp.append({**r, "cx": bx + (ax - bx) * t, "cy": by + (ay - by) * t})
            push(f"Align group to {alignment}", tmp, "rect")
    syn.rects = rects

    # ── 6. walls ──────────────────────────────────────────────────
    tol = max(0.75, 0.03 * metre)
    walls = build_walls(rects, tol)
    syn.walls = walls
    phase("Walls — exterior, then partitions")
    for k in range(1, 11):
        push("Walls — exterior, then partitions", rects, "rect", walls=k / 10.0)

    # ── 7. openings ───────────────────────────────────────────────
    wwr_by_room = {}
    for r in rooms:
        v = r.get("wwr")
        if v is None and wwr_by_type:
            v = wwr_by_type.get(r["room_type"], 0.0)
        wwr_by_room[r["room_id"]] = float(v or 0.0)
    openings = build_openings(rects, walls, edges, metre, wwr_by_room)
    # draw order: open passages, doors, entry, windows
    order = {"open": 0, "door": 1, "balcony_door": 1, "entry": 2, "window": 3}
    openings.sort(key=lambda o: order.get(o["kind"], 9))
    syn.openings = openings
    phase("Doors from connectivity, windows from WWR")
    for k in range(1, len(openings) + 1):
        push("Doors from connectivity, windows from WWR", rects, "rect", walls=1.0, n_open=k)

    # ── 8. furniture ──────────────────────────────────────────────
    furniture: List[dict] = []
    for r in rects:
        for piece in furnish_room(r, openings, metre):
            piece["room_id"] = r["room_id"]
            furniture.append(piece)
    syn.furniture = furniture
    phase("Furniture blocks")
    for k in range(1, len(furniture) + 1):
        push("Furniture blocks", rects, "rect", walls=1.0, n_open=len(openings), n_furn=k)
    final_label = "Schematic plan" + (f" · removed {', '.join(syn.removed)}" if syn.removed else "")
    push(final_label, rects, "rect", walls=1.0, n_open=len(openings), n_furn=len(furniture), hold=12)

    syn.frames = frames
    syn.phases = phases
    return syn
