"""
Mock schematic generator (Stage 4) + packing algorithms.

Architecture mirrors the web app:

  * ``mock_schematic`` builds a data shell — raw rooms with aspect ratios,
    edges, and a reference to the source Stage 3 connectivity graph. NO
    packing happens here.
  * The packing helpers (``pack_circles``, ``pack_rectangles_with_connectivity``,
    ``align_group``, ``compute_area_scale``) are exposed for the canvas widget
    to call at render time. That way layout adapts to the canvas's actual
    pixel size (thumbnail vs detail), which is what the JS does via ``useMemo``.

All four packing functions are faithful Python ports of
``schematicPacking.js`` + ``schematicScale.js``.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

from app.state import InputsState


# Default aspect ratios when InputsState has none configured.
_DEFAULT_ASPECT = {
    "living":   1.40,
    "bedroom":  1.20,
    "kitchen":  1.40,
    "bathroom": 1.50,
    "dining":   1.30,
    "balcony":  2.20,
    "study":    1.20,
    "storage":  1.50,
    "stair":    1.00,
    "veranda":  2.00,
    "garden":   1.50,
    "parking":  2.00,
}


# ─────────────────────────────────────────────────────────────────────
#  Port of schematicScale.js
# ─────────────────────────────────────────────────────────────────────

def compute_area_scale(rooms: List[dict],
                       canvas_w: float, canvas_h: float,
                       target_fill: float = 0.25) -> float:
    """Pick a scale so total packed area ≈ ``target_fill`` × canvas.

    Default ``target_fill`` is 0.25 to match the JS (was 0.55 in my earlier
    pass — that's why rooms were oversized).
    """
    if not rooms:
        return 1.0
    total_area = sum(max(1.0, float(r.get("area", 0))) for r in rooms)
    if total_area <= 0:
        return 1.0
    return math.sqrt(canvas_w * canvas_h * target_fill / total_area)


def map_stage3_position(x: float, y: float,
                        stage3_size: float,
                        target_size: float) -> tuple:
    """Proportional map from Stage 3 ref space (200×200) → canvas square."""
    return (x / stage3_size) * target_size, (y / stage3_size) * target_size


# ─────────────────────────────────────────────────────────────────────
#  Port of schematicPacking.js — packCircles
# ─────────────────────────────────────────────────────────────────────

def pack_circles(nodes: List[dict],
                 w: float = 300, h: float = 300, pad: float = 4,
                 iters: int = 300, gap: float = 0.6,
                 step: float = 0.55,
                 ox: float = 0.0, oy: float = 0.0) -> List[dict]:
    """Non-overlap circle packing (bubble mode).

    Primaries (``pinned=True``) stay put; secondaries nudge outward each
    iter until no overlap remains. Non-pinned nodes are clamped to the
    canvas with ``pad`` margin. ``ox``/``oy`` shift the clamp box so the
    layout area can sit anywhere on a larger canvas (used by the
    letterboxed Stage 4 canvas).
    """
    n = len(nodes)
    for _ in range(iters):
        for i in range(n):
            a = nodes[i]
            for j in range(i + 1, n):
                b = nodes[j]
                dx = b["cx"] - a["cx"]
                dy = b["cy"] - a["cy"]
                dist = math.sqrt(dx * dx + dy * dy) + 1e-9
                min_dist = a["r"] + b["r"] + gap
                if dist >= min_dist:
                    continue
                overlap = min_dist - dist
                ux = dx / dist
                uy = dy / dist
                ap = bool(a.get("pinned"))
                bp = bool(b.get("pinned"))
                if ap and bp:
                    continue
                if ap:
                    b["cx"] += ux * overlap * step
                    b["cy"] += uy * overlap * step
                elif bp:
                    a["cx"] -= ux * overlap * step
                    a["cy"] -= uy * overlap * step
                else:
                    half = overlap * 0.5 * step
                    a["cx"] -= ux * half
                    a["cy"] -= uy * half
                    b["cx"] += ux * half
                    b["cy"] += uy * half
        # Clamp non-pinned to the [ox, ox+w] × [oy, oy+h] box
        for node in nodes:
            if node.get("pinned"):
                continue
            node["cx"] = max(ox + pad + node["r"],
                              min(ox + w - pad - node["r"], node["cx"]))
            node["cy"] = max(oy + pad + node["r"],
                              min(oy + h - pad - node["r"], node["cy"]))
    return nodes


# ─────────────────────────────────────────────────────────────────────
#  Port of schematicPacking.js — resolve_all_overlaps (private)
# ─────────────────────────────────────────────────────────────────────

def _resolve_all_overlaps(nodes: List[dict],
                          w: float, h: float, pad: float,
                          ox: float = 0.0, oy: float = 0.0,
                          max_iters: int = 300) -> None:
    n = len(nodes)
    for _ in range(max_iters):
        any_ovl = False
        for i in range(n):
            a = nodes[i]
            for j in range(i + 1, n):
                b = nodes[j]
                dx = b["cx"] - a["cx"]
                dy = b["cy"] - a["cy"]
                hw = (a["w"] + b["w"]) / 2.0
                hh = (a["h"] + b["h"]) / 2.0
                ovl_x = hw - abs(dx)
                ovl_y = hh - abs(dy)
                if ovl_x <= 0.01 or ovl_y <= 0.01:
                    continue
                any_ovl = True
                if ovl_x < ovl_y:
                    px = ovl_x if dx >= 0 else -ovl_x
                    py = 0.0
                else:
                    px = 0.0
                    py = ovl_y if dy >= 0 else -ovl_y
                a["cx"] -= px * 0.5
                a["cy"] -= py * 0.5
                b["cx"] += px * 0.5
                b["cy"] += py * 0.5
        for node in nodes:
            node["cx"] = max(ox + pad + node["w"] / 2.0,
                              min(ox + w - pad - node["w"] / 2.0,
                                  node["cx"]))
            node["cy"] = max(oy + pad + node["h"] / 2.0,
                              min(oy + h - pad - node["h"] / 2.0,
                                  node["cy"]))
        if not any_ovl:
            return


# ─────────────────────────────────────────────────────────────────────
#  Port of schematicPacking.js — packRectanglesWithConnectivity
# ─────────────────────────────────────────────────────────────────────

def pack_rectangles_with_connectivity(
        nodes: List[dict],
        edges: List[dict],
        w: float = 300, h: float = 300, pad: float = 4,
        cycles: int = 30,
        centroid_pull: float = 0.05,
        spring_k: float = 0.3,
        ox: float = 0.0, oy: float = 0.0) -> List[dict]:
    """Spring-based contact packing for rectangle mode.

    ``ox``/``oy`` offset the clamp box so packing happens inside a
    letterboxed sub-region of a larger canvas (used by Stage 4 when
    respecting the plan's aspect ratio).
    """
    by_id = {n["room_id"]: n for n in nodes}
    edge_pairs: List[tuple] = []
    for e in edges or []:
        a = by_id.get(e["source"])
        b = by_id.get(e["target"])
        if a is not None and b is not None and a is not b:
            edge_pairs.append((a, b))

    _resolve_all_overlaps(nodes, w, h, pad, ox, oy)

    n_nodes = len(nodes)
    if n_nodes == 0:
        return nodes

    for _ in range(cycles):
        cx_sum = sum(n["cx"] for n in nodes) / n_nodes
        cy_sum = sum(n["cy"] for n in nodes) / n_nodes
        for n in nodes:
            n["cx"] += (cx_sum - n["cx"]) * centroid_pull
            n["cy"] += (cy_sum - n["cy"]) * centroid_pull

        for a, b in edge_pairs:
            dx = b["cx"] - a["cx"]
            dy = b["cy"] - a["cy"]
            dist = math.sqrt(dx * dx + dy * dy) + 1e-9
            ux = dx / dist
            uy = dy / dist
            hw = (a["w"] + b["w"]) / 2.0
            hh = (a["h"] + b["h"]) / 2.0
            target = abs(ux) * hw + abs(uy) * hh
            diff = dist - target
            if diff > 0:
                force = spring_k * diff
                a["cx"] += ux * force
                a["cy"] += uy * force
                b["cx"] -= ux * force
                b["cy"] -= uy * force

        _resolve_all_overlaps(nodes, w, h, pad, ox, oy)

    return nodes


# ─────────────────────────────────────────────────────────────────────
#  Port of schematicPacking.js — alignGroup
# ─────────────────────────────────────────────────────────────────────

def align_group(nodes: List[dict], alignment: str,
                w: float = 300, h: float = 300,
                pad: float = 4,
                ox: float = 0.0, oy: float = 0.0) -> List[dict]:
    """Translate the whole packed group so one edge hugs the canvas.

    Works for circles (``{cx, cy, r}``) and rectangles
    (``{cx, cy, w, h}``). ``ox``/``oy`` shift the alignment box so the
    layout area can be a letterboxed sub-region of a larger widget
    (used by the Stage 4 schematic canvas).
    """
    if not nodes:
        return nodes

    def half_w(n: dict) -> float:
        return n["w"] / 2.0 if "w" in n else float(n.get("r", 0))

    def half_h(n: dict) -> float:
        return n["h"] / 2.0 if "h" in n else float(n.get("r", 0))

    min_x = min(n["cx"] - half_w(n) for n in nodes)
    max_x = max(n["cx"] + half_w(n) for n in nodes)
    min_y = min(n["cy"] - half_h(n) for n in nodes)
    max_y = max(n["cy"] + half_h(n) for n in nodes)

    cx_g = (min_x + max_x) / 2.0
    cy_g = (min_y + max_y) / 2.0
    width_g = max_x - min_x
    height_g = max_y - min_y

    target_cx = ox + w / 2.0
    target_cy = oy + h / 2.0
    if alignment == "N":
        target_cy = oy + pad + height_g / 2.0
    elif alignment == "S":
        target_cy = oy + h - pad - height_g / 2.0
    elif alignment == "E":
        target_cx = ox + w - pad - width_g / 2.0
    elif alignment == "W":
        target_cx = ox + pad + width_g / 2.0

    shift_x = target_cx - cx_g
    shift_y = target_cy - cy_g
    for n in nodes:
        n["cx"] += shift_x
        n["cy"] += shift_y
    return nodes


# ─────────────────────────────────────────────────────────────────────
#  Mock schematic data
# ─────────────────────────────────────────────────────────────────────

def mock_schematic(zone_table: dict,
                   connectivity_graph: Optional[dict],
                   aspect_ratios: Optional[Dict[str, float]] = None) -> dict:
    """Build one schematic data record. No packing — done at render time."""
    aspect_lookup: Dict[str, float] = dict(_DEFAULT_ASPECT)
    if aspect_ratios:
        for k, v in aspect_ratios.items():
            if v and float(v) > 0:
                aspect_lookup[k] = float(v)

    rooms: List[dict] = []
    for r in zone_table.get("rooms", []):
        rd = dict(r)
        if r.get("w_m") and r.get("h_m"):                      # drawn geometry: own shape (E–W ÷ N–S here)
            rd["aspect_ratio"] = float(r["w_m"]) / float(r["h_m"])
        else:
            rd["aspect_ratio"] = aspect_lookup.get(r["room_type"], 1.2)
        rooms.append(rd)

    edges: List[dict] = []
    if connectivity_graph:
        edges = list(connectivity_graph.get("edges_ordered", []))

    return {
        "plan_index":          int(zone_table.get("table_index", 0)),
        "rooms":               rooms,
        "edges":               edges,
        "source_connectivity": connectivity_graph,
        "computed":            dict(zone_table.get("computed", {})),
    }


def mock_schematics(zone_tables: List[dict],
                    connectivity_graphs: List[dict],
                    selected_idxs: List[int],
                    inputs: InputsState) -> List[dict]:
    """One schematic per selected plan."""
    if not zone_tables or not selected_idxs:
        return []
    by_table = {int(t["table_index"]): t for t in zone_tables}
    by_graph = {int(g["meta"]["plan_index"]): g
                for g in (connectivity_graphs or [])}
    out: List[dict] = []
    for idx in selected_idxs:
        t = by_table.get(int(idx))
        if t is None:
            continue
        g = by_graph.get(int(idx))
        out.append(mock_schematic(t, g, inputs.aspect_ratios))
    return out


# ─────────────────────────────────────────────────────────────────────
#  Pareto front (unchanged)
# ─────────────────────────────────────────────────────────────────────

def pareto_front_indices(schematics: List[dict]) -> List[int]:
    """Plan_index values on the Pareto-dominant frontier of (QVCI, AQI)."""
    if not schematics:
        return []
    pts = [
        (int(s["plan_index"]),
         float(s.get("computed", {}).get("qvci", 0.0)),
         float(s.get("computed", {}).get("aqi", 0.0)))
        for s in schematics
    ]
    out: List[tuple] = []
    for i, (idx_i, q_i, a_i) in enumerate(pts):
        dominated = False
        for j, (_, q_j, a_j) in enumerate(pts):
            if i == j:
                continue
            if q_j >= q_i and a_j >= a_i and (q_j > q_i or a_j > a_i):
                dominated = True
                break
        if not dominated:
            out.append((idx_i, q_i, a_i))
    out.sort(key=lambda p: p[1])
    return [p[0] for p in out]
