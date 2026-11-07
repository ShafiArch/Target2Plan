"""
Mock connectivity-graph generator (Stage 3).

The real model (EdgeARModel — an autoregressive edge sampler that
emits doorway/passage links one at a time, conditioned on the rooms'
types and zones) is to be extracted into ``connectivity_model.py``.
Until that lands, this module fakes the output with a simple
adjacency-based heuristic + WWR-driven exposure edges.

Output shape per graph (one per selected zone-assignment plan)::

    {
      "meta":           {"plan_index": int},
      "canvas_size":    200,                 # reference space in px
      "nodes": [
        # Direction anchors (pinned at canvas edges)
        {"id": "N", "type": "direction", "is_direction": True,
         "is_primary": False,
         "x": cx, "y": top,
         "x_start": cx, "y_start": top},
        ...
        # Rooms
        {"id": "bedroom_0", "type": "bedroom", "is_direction": False,
         "is_primary": True,
         "x": x_final, "y": y_final,
         "x_start": x_start, "y_start": y_start},
        ...
      ],
      "edges_ordered":  [{"source": "...", "target": "..."}, ...],  # access
      "exposure_edges": [{"source": "...", "target": "N"}, ...],
    }

Animation
---------
The canvas widget animates edges in ``edges_ordered`` order (access
edges first), then ``exposure_edges`` (dashed pink). Non-primary
rooms tween from ``(x_start, y_start)`` to ``(x, y)`` as a function
of the edge-reveal progress, so they "settle into" the layout while
connections appear.
"""

from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Tuple

from app.state import InputsState
from models.zone_mock import (
    arrange_rooms_in_cell, zone_cell_box,
)


# Reference canvas size — all positions are in this coord space.
# Render-time scaling handles target sizes other than 200×200.
STAGE3_SIZE = 200

# Rooms that count as "primary" — they get pinned (no tween) and
# rendered with a dashed ring to indicate they're load-bearing for
# the design (privacy, view, acoustic requirements).
PRIMARY_TYPES = {"living", "bedroom", "kitchen"}

# Which direction anchors a room in zone Z is exposed to.
# Matches the zone numbering used elsewhere (0=NW, 1=N, ..., 8=SE).
ZONE_TO_DIRECTIONS: Dict[int, List[str]] = {
    0: ["N", "W"], 1: ["N"], 2: ["N", "E"],
    3: ["W"],      4: [],    5: ["E"],
    6: ["S", "W"], 7: ["S"], 8: ["S", "E"],
}


# ─────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────

def _zones_adjacent(z1, z2) -> bool:
    """Two zones are adjacent if they share a row, column, or corner.
    A room without a zone (balcony) is adjacent to everything."""
    if z1 is None or z2 is None:
        return True
    if z1 == z2:
        return True
    r1, c1 = z1 // 3, z1 % 3
    r2, c2 = z2 // 3, z2 % 3
    return abs(r1 - r2) <= 1 and abs(c1 - c2) <= 1


def _direction_anchor_positions(canvas_size: int = STAGE3_SIZE,
                                pad: int = 14) -> Dict[str, Tuple[float, float]]:
    """Return {N, S, E, W: (x, y)} positions on the canvas border."""
    c = canvas_size / 2.0
    return {
        "N": (c,                  pad),
        "S": (c,                  canvas_size - pad),
        "E": (canvas_size - pad,  c),
        "W": (pad,                c),
    }


# ─────────────────────────────────────────────────────────────────────
#  Graph generation
# ─────────────────────────────────────────────────────────────────────

def mock_connectivity_graph(table: dict,
                            canvas_size: int = STAGE3_SIZE,
                            seed: Optional[int] = None,
                            plan_width: float = 1500.0,
                            plan_height: float = 2000.0) -> dict:
    """Generate one connectivity graph from a zone-assignment table.

    Positions and counts are deterministic given ``seed`` so animations
    look the same across regenerations.
    """
    if seed is None:
        seed = 9001 + int(table.get("table_index", 0))
    rng = random.Random(seed)

    plan_index = int(table.get("table_index", 0))
    rooms = table.get("rooms", [])

    # ── 1. Letterboxed plan rect — same math as the real model ──
    inset = max(8.0, canvas_size * 0.05)
    avail = float(canvas_size) - 2 * inset
    plan_aspect = (float(plan_width) / float(plan_height)
                    if plan_width > 0 and plan_height > 0 else 1.0)
    if plan_aspect >= 1.0:
        rect_w = avail
        rect_h = avail / plan_aspect
    else:
        rect_w = avail * plan_aspect
        rect_h = avail
    rect_x = (canvas_size - rect_w) / 2.0
    rect_y = (canvas_size - rect_h) / 2.0
    center = (rect_x + rect_w / 2.0, rect_y + rect_h / 2.0)

    nodes: List[dict] = []

    # ── 2. Primary positions: zone centroid (sub-grid for stacking) ──
    rooms_by_zone: Dict[int, List[dict]] = {}
    for r in rooms:
        if r.get("zone_assignment") is None:        # not zoned (balcony): starts at the centre
            continue
        rooms_by_zone.setdefault(int(r["zone_assignment"]), []).append(r)

    final_pos: Dict[str, Tuple[float, float]] = {}
    for zone, zone_rooms in rooms_by_zone.items():
        cx, cy, cw, ch = zone_cell_box(zone, rect_w, rect_h)
        cx += rect_x; cy += rect_y
        positions = arrange_rooms_in_cell(zone_rooms, cx, cy, cw, ch)
        for r, pos in zip(zone_rooms, positions):
            final_pos[r["room_id"]] = (pos["cx"], pos["cy"])

    for r in rooms:
        rid = r["room_id"]
        rt  = r["room_type"]
        is_primary = rt in PRIMARY_TYPES
        fx, fy = final_pos.get(rid, center)
        if is_primary:
            sx, sy = fx, fy
        else:
            # Non-primary rooms start at centre with a small jitter
            # so the tween animation doesn't collapse onto one pixel.
            sx = center[0] + rng.uniform(-6, 6)
            sy = center[1] + rng.uniform(-6, 6)
        nodes.append({
            "id":           rid,
            "type":         rt,
            "is_direction": False,
            "is_primary":   is_primary,
            "x":            fx,
            "y":            fy,
            "x_start":      sx,
            "y_start":      sy,
        })

    # ── 3. Access edges (between rooms) ─────────────────────────────
    # Strategy: walk rooms in a "primary first" order; for each one,
    # connect to one or two un-connected rooms in adjacent zones. This
    # produces a roughly-tree-shaped backbone, then we add a few extra
    # edges for cycles.
    sorted_rooms = sorted(
        rooms,
        key=lambda r: (0 if r["room_type"] in PRIMARY_TYPES else 1,
                       r["room_id"]),
    )
    edges_ordered: List[dict] = []
    placed: set[tuple[str, str]] = set()

    def _add_edge(a: dict, b: dict) -> None:
        key = tuple(sorted([a["room_id"], b["room_id"]]))
        if key in placed:
            return
        placed.add(key)
        edges_ordered.append({
            "source": a["room_id"],
            "target": b["room_id"],
        })

    for i, r1 in enumerate(sorted_rooms):
        # Find candidate neighbours in same or adjacent zones,
        # prefer ones we haven't already connected r1 to.
        candidates = [
            r2 for r2 in sorted_rooms
            if r2["room_id"] != r1["room_id"]
            and _zones_adjacent(r1["zone_assignment"], r2["zone_assignment"])
            and tuple(sorted([r1["room_id"], r2["room_id"]])) not in placed
        ]
        if not candidates:
            continue
        # Prefer rooms in the same zone (more likely doored in real plans),
        # then adjacent. Sort: same-zone first.
        candidates.sort(key=lambda r: (
            0 if r["zone_assignment"] == r1["zone_assignment"] else 1,
            r["room_id"],
        ))
        # Add 1 edge from r1, sometimes 2 if there's an obvious neighbour.
        n_edges = 1 if rng.random() < 0.6 else 2
        for r2 in candidates[:n_edges]:
            _add_edge(r1, r2)

    # Sprinkle a few extra random edges between adjacent-zone rooms
    # to introduce cycles (matches realistic floor plans with multiple
    # circulation paths).
    extras_target = max(1, len(rooms) // 4)
    extras_added = 0
    attempts = 0
    while extras_added < extras_target and attempts < 80:
        attempts += 1
        a, b = rng.sample(sorted_rooms, 2)
        if not _zones_adjacent(a["zone_assignment"], b["zone_assignment"]):
            continue
        key = tuple(sorted([a["room_id"], b["room_id"]]))
        if key in placed:
            continue
        _add_edge(a, b)
        extras_added += 1

    # ── 4. Exposure edges (room → direction anchor) ─────────────────
    # ── 4. Add an ``outside_0`` node (visible, single point) ──────
    # In the new design there are no N/S/E/W direction-anchor nodes.
    # ``outside`` is one regular node, sitting at the canvas centre
    # initially; it'll get pulled outward by spring-layout-like
    # heuristics in the real-model path. The mock just places it at
    # canvas bottom-centre so the rooms still have something to
    # connect to and the graph stays connected.
    outside_pos = (center[0], canvas_size - inset)
    nodes.append({
        "id":           "outside_0",
        "type":         "outside",
        "is_direction": False,
        "is_primary":   False,
        "x":            outside_pos[0],
        "y":            outside_pos[1],
        "x_start":      center[0],
        "y_start":      center[1],
    })
    # Connect any room that has wwr > 0.05 to outside_0 (mock heuristic
    # for "this room has windows to the exterior"), so the graph stays
    # connected just like the model would produce.
    for r in rooms:
        if r.get("wwr", 0.0) <= 0.05:
            continue
        edges_ordered.append({
            "source": r["room_id"],
            "target": "outside_0",
        })

    return {
        "meta":           {"plan_index": plan_index},
        "canvas_size":    canvas_size,
        "plan_width":     float(plan_width),
        "plan_height":    float(plan_height),
        "plan_rect":      [rect_x, rect_y, rect_w, rect_h],
        "nodes":          nodes,
        "edges_ordered":  edges_ordered,
        "exposure_edges": [],   # no longer used in this design
    }


def mock_connectivity_graphs(zone_tables: List[dict],
                             selected_idxs: List[int],
                             plan_width: float = 1500.0,
                             plan_height: float = 2000.0) -> List[dict]:
    """Generate one graph for each *selected* zone-assignment table."""
    if not zone_tables or not selected_idxs:
        return []
    by_idx = {int(t["table_index"]): t for t in zone_tables}
    out: List[dict] = []
    for idx in selected_idxs:
        t = by_idx.get(int(idx))
        if t is None:
            continue
        out.append(mock_connectivity_graph(
            t, plan_width=plan_width, plan_height=plan_height))
    return out
