"""
Mock area-distribution generator (Stage 1).

The real area model (LogisticNormalNet, to be extracted into
``models/area_model.py``) samples area proportions from a learned
posterior. Until that's wired, this module fakes it with a hand-tuned
Dirichlet-like sampler using per-room-type priors that roughly match
real residential floor plans.

The circle-packing algorithm is the **real** one — lifted verbatim
from ``Area_distribution_generation_code_pyside_v5_Final.py``. It's a
d3-pack-style greedy tangent placement with round-robin interleaving
by room type, so colors scatter naturally instead of forming
mono-color petals.

When the real model lands, swap the area sampler; the packing stays.

Output schema per variation::

    {
      "variation_idx":      int,
      "rooms": [
        {
          "room_id":     str,         # e.g. "bedroom_0"
          "room_type":   str,
          "area":        float,
          "radius":      float,
          "pos_natural": (x, y),      # natural-coord centre
        },
        ...
      ],
      "total_area":          float,
      "area_share_by_type":  {type: fraction_0_to_1},
      "counts_by_type":      {type: int},
    }
"""

from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Tuple

from app.state import InputsState


# Per-room-type prior area weights — tuned to look like a real plan.
# Higher weight → larger area on average.
TYPE_WEIGHTS = {
    "living":   4.0, "bedroom":  2.8, "kitchen": 2.0,
    "bathroom": 1.0, "dining":   2.2, "balcony": 1.4,
    "storage":  0.6, "study":    1.8,
    "stair":    0.8, "veranda":  1.6, "garden":  3.5, "parking": 2.5,
    "other":    1.0,
}


# ─────────────────────────────────────────────────────────────────────
#  Circle-packing primitives (lifted from existing PySide app)
# ─────────────────────────────────────────────────────────────────────

def _circle_circle_tangent(a: dict, b: dict, c_r: float, eps: float = 1e-3):
    """Two positions where a new circle of radius ``c_r`` is simultaneously
    tangent to placed circles ``a`` and ``b``. Returns 0–2 (x, y) tuples."""
    dx = b["x"] - a["x"]
    dy = b["y"] - a["y"]
    d2 = dx * dx + dy * dy
    if d2 < 1e-12:
        return []
    d  = math.sqrt(d2)
    R1 = a["r"] + c_r
    R2 = b["r"] + c_r
    if d > R1 + R2 + eps or d < abs(R1 - R2) - eps:
        return []
    aa = (R1 * R1 - R2 * R2 + d2) / (2.0 * d)
    h2 = R1 * R1 - aa * aa
    if h2 < 0:
        return []
    h = math.sqrt(h2)
    ux, uy = dx / d, dy / d
    mx = a["x"] + aa * ux
    my = a["y"] + aa * uy
    return [
        (mx - h * uy, my + h * ux),
        (mx + h * uy, my - h * ux),
    ]


def _no_overlap(p: Tuple[float, float], c_r: float,
                placed: List[dict], eps: float = 0.01) -> bool:
    px, py = p
    for c in placed:
        dx = px - c["x"]
        dy = py - c["y"]
        threshold = c["r"] + c_r - eps
        if dx * dx + dy * dy < threshold * threshold:
            return False
    return True


def pack_circles_natural(rooms: List[dict], seed: int = 42) -> None:
    """Greedy tangent-pair packing in natural coordinates.

    Mutates each room dict in place, adding ``pos_natural`` = (x, y).
    Coordinates are centered near the origin; downstream fitting maps
    them onto a canvas.

    Placement order uses ROUND-ROBIN INTERLEAVING by room type so
    same-type rooms don't cluster into mono-color petals. The largest
    room of any type still anchors the centre; subsequent rooms
    alternate across types.
    """
    active = [r for r in rooms if r.get("radius", 0) > 0]
    if not active:
        return
    rng = random.Random(seed)

    # Working copies keep a back-ref so we can write pos_natural at the
    # end without losing per-variation room order.
    work = [{"src": r, "id": r["room_id"], "r": r["radius"],
             "type": r["room_type"], "x": 0.0, "y": 0.0}
            for r in active]

    # ─── Round-robin order by type ────────────────────────────
    by_type: Dict[str, List[dict]] = {}
    for c in work:
        by_type.setdefault(c["type"], []).append(c)
    for lst in by_type.values():
        rng.shuffle(lst)

    ordered: List[dict] = []
    while any(by_type.values()):
        active_groups = [(typ, lst) for typ, lst in by_type.items() if lst]
        active_groups.sort(key=lambda x: -x[1][0]["r"])
        for typ, lst in active_groups:
            ordered.append(lst.pop(0))
    work = ordered

    work[0]["x"], work[0]["y"] = 0.0, 0.0
    placed = [work[0]]

    if len(work) >= 2:
        ang = rng.uniform(0, 2 * math.pi)
        d = placed[0]["r"] + work[1]["r"] + 0.05
        work[1]["x"] = d * math.cos(ang)
        work[1]["y"] = d * math.sin(ang)
        placed.append(work[1])

    for c in work[2:]:
        best: Optional[Tuple[float, float]] = None
        best_d2 = float("inf")

        # Pair-tangent (d3-pack style)
        for i in range(len(placed)):
            for j in range(i + 1, len(placed)):
                for p in _circle_circle_tangent(placed[i], placed[j], c["r"]):
                    if _no_overlap(p, c["r"], placed):
                        d2 = p[0] * p[0] + p[1] * p[1]
                        if d2 < best_d2:
                            best_d2 = d2
                            best = p

        # Single-tangent fallback (sweep angles)
        if best is None:
            for ref in placed:
                for ai in range(36):
                    a = (ai / 36.0) * 2 * math.pi
                    d = ref["r"] + c["r"] + 0.1
                    tx = ref["x"] + d * math.cos(a)
                    ty = ref["y"] + d * math.sin(a)
                    if _no_overlap((tx, ty), c["r"], placed):
                        d2 = tx * tx + ty * ty
                        if d2 < best_d2:
                            best_d2 = d2
                            best = (tx, ty)

        # Last resort: push outward
        if best is None:
            max_r = max(math.sqrt(p["x"] ** 2 + p["y"] ** 2) + p["r"]
                        for p in placed)
            a = rng.uniform(0, 2 * math.pi)
            d_out = max_r + c["r"] + 5
            best = (d_out * math.cos(a), d_out * math.sin(a))

        c["x"], c["y"] = best
        placed.append(c)

    for w in work:
        w["src"]["pos_natural"] = (w["x"], w["y"])


def fit_natural_to_canvas(rooms: List[dict],
                          canvas_w: float, canvas_h: float,
                          pad: float = 4.0):
    """Map natural-coord packing to pixel coords on a canvas.

    Returns ``(positions_px, radii_px)`` as dicts keyed by room_id.
    Uniform scale so circles never overflow.
    """
    active = [r for r in rooms
              if r.get("radius", 0) > 0 and r.get("pos_natural")]
    if not active:
        return {}, {}

    min_x = min(r["pos_natural"][0] - r["radius"] for r in active)
    max_x = max(r["pos_natural"][0] + r["radius"] for r in active)
    min_y = min(r["pos_natural"][1] - r["radius"] for r in active)
    max_y = max(r["pos_natural"][1] + r["radius"] for r in active)

    bbox_w = max(max_x - min_x, 1e-6)
    bbox_h = max(max_y - min_y, 1e-6)
    avail_w = canvas_w - 2 * pad
    avail_h = canvas_h - 2 * pad
    scale = min(avail_w / bbox_w, avail_h / bbox_h)

    bbox_cx = (min_x + max_x) / 2.0
    bbox_cy = (min_y + max_y) / 2.0
    target_cx = canvas_w / 2.0
    target_cy = canvas_h / 2.0

    positions_px: Dict[str, Tuple[float, float]] = {}
    radii_px:     Dict[str, float] = {}
    for r in active:
        nx, ny = r["pos_natural"]
        positions_px[r["room_id"]] = (
            (nx - bbox_cx) * scale + target_cx,
            (ny - bbox_cy) * scale + target_cy,
        )
        radii_px[r["room_id"]] = r["radius"] * scale
    return positions_px, radii_px


# ─────────────────────────────────────────────────────────────────────
#  Mock variation sampler
# ─────────────────────────────────────────────────────────────────────

def mock_area_variations(inputs: InputsState,
                          n_variations: int = 12,
                          diversity: float = 1.0,
                          seed: Optional[int] = None) -> List[Dict]:
    """Generate ``n_variations`` mock area distributions.

    Per variation: each room's area is sampled as
        weight_i = type_prior_i × exp(N(0, σ_log))
        area_i   = weight_i / Σ weights × total_area
    where ``σ_log = 0.25 × diversity`` so the toolbar's Diversity
    control affects the mock in spirit (wider spread = more visibly
    different variations), even though the mock doesn't use the
    learned σ from the real model.

    ``seed=None`` → fresh entropy each call (different output every
    regenerate). Pass an int for reproducibility.
    """
    total_area = inputs.plan_geometry.total_area
    counts = inputs.room_counts

    # Build the canonical room list (one entry per room instance)
    room_list: List[Tuple[str, str]] = []
    for rt, n in counts.items():
        for i in range(int(n)):
            room_list.append((f"{rt}_{i}", rt))

    if not room_list:
        return []

    # σ in log-space — scaled by the user's diversity setting
    sigma_log = 0.25 * max(0.05, float(diversity))

    # Pick a per-call base seed when none provided so each variation
    # is independent but the mock is otherwise reproducible within
    # the call.
    if seed is None:
        seed = random.randint(0, (1 << 30) - 1)

    variations: List[Dict] = []
    for var_idx in range(n_variations):
        rng = random.Random(seed + var_idx)

        # Per-room weight: prior × log-normal jitter
        weights: List[float] = []
        for _room_id, rt in room_list:
            base = TYPE_WEIGHTS.get(rt, 1.0)
            w = base * math.exp(rng.gauss(0, sigma_log))
            weights.append(w)

        total_w = sum(weights)
        if total_w <= 0:
            continue

        rooms: List[Dict] = []
        for (room_id, rt), w in zip(room_list, weights):
            area = w / total_w * total_area
            radius = math.sqrt(area / math.pi)
            rooms.append({
                "room_id":   room_id,
                "room_type": rt,
                "area":      area,
                "radius":    radius,
            })

        # Real circle packing
        pack_circles_natural(rooms, seed=seed + var_idx)

        # Area share by room type, for the comparison strip
        share: Dict[str, float] = {}
        for r in rooms:
            share[r["room_type"]] = share.get(r["room_type"], 0.0) + r["area"]
        share = {k: v / total_area for k, v in share.items()}

        variations.append({
            "variation_idx":      var_idx,
            "rooms":              rooms,
            "total_area":         total_area,
            "units":              str(getattr(inputs.plan_geometry, "units", "m")),
            "area_share_by_type": share,
            "counts_by_type":     dict(counts),
        })

    return variations
