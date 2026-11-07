"""
Mock zone-assignment generator (Stage 2) — journal version.

Used only when the real v13 model cannot run (PyTorch missing or no
``zone_model_v13.pt`` on disk).  It draws *random admissible*
arrangements — the same capacity- and type-respecting random baseline
the paper evaluates against — and scores them with
:mod:`models.zone_scoring`, so every number shown is a real value of the
journal's QVCI / AQI equations; only the *choice* of arrangement is
uninformed.

Zone numbering (UI, 0-indexed)::

    0 NW   1 N   2 NE
    3 W    4 C   5 E
    6 SW   7 S   8 SE

Output schema per "table" (one arrangement)::

    {
      "table_index": int,
      "computed": {
          "qvci":          float in [0, 1],   # area-weighted view index (scored rooms)
          "aqi":           float in [0, 1],   # area-weighted acoustic index (scored rooms)
          "avg_spl_dB":    float,             # energy-average indoor level of the zoned rooms, dB(A)
          "zone_spread":   int in [1, 9],     # zones used
          "concentration": float in [0, 1],   # rooms in the fullest zone / n (descriptive only)
          "d_target":      float or None,     # |QVCI − target| + |AQI − target|
          "valid":         bool,              # capacity + type rule respected
      },
      "targets":   {"qvci": float, "aqi": float, "present": bool},
      "reachable": {"qvci": [lo, hi], "aqi": [lo, hi], "qvci_free": [...], "aqi_free": [...],
                    "aqi_collapsed": bool, "aqi_live": bool},
      "capacity":  {"corner": int, "cardinal": int, "interior": int},
      "zone_weights":     {f"zone_{0..8}": v(z)},            # view weight per zone
      "acoustic_weights": {f"zone_{0..8}": quietness 0..1},  # display only
      "zone_levels_dB":   {f"zone_{0..8}": outdoor dB(A)},
      "decode": "greedy" | "sampled" | "random",
      "model":  "v13" | "mock",
      "rooms": [
        {
          "room_id":               str,
          "room_type":             str,
          "area":                  float,          # plan units (as Stage 1)
          "area_m2":               float,
          "zone_assignment":       int in [0, 8] or None (not zoned: balcony …),
          "quality_view_score":    float or None,  # q_i
          "quality_view_achieved": bool or None,   # q_i >= 0.5
          "acoustic_score":        float or None,  # a_i
          "acoustic_ok":           bool or None,   # indoor level <= guideline
          "est_avg_spl_dB":        float or None,  # indoor level L_in, dB(A)
          "guideline_dB":          float or None,
          "wwr":                   float,
          "aspect_ratio":          float,
          "weight":                float,          # index weight w_i (0 for bathrooms / unzoned)
          "scored":                bool,
          "zoned":                 bool,
        }, ...
      ],
    }
"""

from __future__ import annotations

import math
import random
from typing import Dict, List

from app.state import InputsState
from models import zone_scoring as zs


# ─────────────────────────────────────────────────────────────────────
#  Zone constants
# ─────────────────────────────────────────────────────────────────────

ZONE_LABELS = {
    0: "NW", 1: "N", 2: "NE",
    3: "W",  4: "C", 5: "E",
    6: "SW", 7: "S", 8: "SE",
}
ZONE_LAYOUT = [[0, 1, 2], [3, 4, 5], [6, 7, 8]]

# Rooms that carry index weight (the regularly occupied rooms).
PRIMARY_TYPES = set(zs.SCORED_TYPES)

# View-quality weight per outdoor-view category (journal values).
VIEW_WEIGHTS = dict(zs.VIEW_WEIGHTS)


# ─────────────────────────────────────────────────────────────────────
#  Zone-weight derivation from site conditions (display helpers)
# ─────────────────────────────────────────────────────────────────────

def compute_zone_view_weights(site_conditions) -> Dict[str, float]:
    """Per-zone view weight v(z) (0..1), keyed ``zone_0`` … ``zone_8``."""
    zv = zs.zone_view(zs.site_from_inputs(site_conditions))
    return {f"zone_{z}": float(zv[z]) for z in range(9)}


def compute_zone_acoustic_weights(site_conditions) -> Dict[str, float]:
    """Per-zone quietness (0..1, higher = quieter), keyed ``zone_0`` … ``zone_8``."""
    lv = zs.zone_level(zs.site_from_inputs(site_conditions))
    return {f"zone_{z}": max(0.0, min(1.0, 1.0 - (lv[z] - 30.0) / 50.0)) for z in range(9)}


# ─────────────────────────────────────────────────────────────────────
#  Geometry helpers (stand-in for zoneGeometry.js)
# ─────────────────────────────────────────────────────────────────────

def zone_cell_box(zone: int, w: float, h: float, gap: float = 0.0):
    """(x, y, w, h) of one 3×3 cell."""
    row, col = zone // 3, zone % 3
    cw, ch = w / 3.0, h / 3.0
    return (col * cw + gap, row * ch + gap,
            cw - 2 * gap, ch - 2 * gap)


def arrange_rooms_in_cell(rooms: List[dict],
                          cell_x: float, cell_y: float,
                          cell_w: float, cell_h: float):
    """Place N rooms inside a cell: n=1 centred, n=2 pair, n=3..4 two
    columns, n=5+ three columns; sub-grid 85 % of the cell, centred."""
    n = len(rooms)
    if n == 0:
        return []
    cx = cell_x + cell_w / 2.0
    cy = cell_y + cell_h / 2.0
    if n == 1:
        return [{"cx": cx, "cy": cy}]
    cols = n if n <= 2 else (2 if n <= 4 else 3)
    rows = int(math.ceil(n / cols))
    usable_w = cell_w * 0.85
    usable_h = cell_h * 0.85
    grid_x = cx - usable_w / 2.0
    grid_y = cy - usable_h / 2.0
    sub_w = usable_w / cols
    sub_h = usable_h / rows
    out = []
    for i in range(n):
        r = i // cols
        c = i % cols
        out.append({"cx": grid_x + sub_w * c + sub_w / 2.0,
                    "cy": grid_y + sub_h * r + sub_h / 2.0})
    return out


# ─────────────────────────────────────────────────────────────────────
#  Mock generator — random admissible arrangements, real scoring
# ─────────────────────────────────────────────────────────────────────

def _random_valid(ps, rng: random.Random, tries: int = 200) -> List[int]:
    """A random capacity- and type-respecting arrangement (1-based zones)."""
    for _ in range(tries):
        zones = [rng.choice([z for z in range(1, 10) if ps.allowed[i, z - 1]]) for i in range(ps.n)]
        if ps.valid(zones):
            return zones
    # Fallback: fill zones round-robin within capacity.
    zones = []
    cnt = [0] * 10
    for i in range(ps.n):
        order = sorted((z for z in range(1, 10) if ps.allowed[i, z - 1]), key=lambda z: (cnt[z], rng.random()))
        for z in order:
            if cnt[z] < ps.capv[z - 1]:
                zones.append(z); cnt[z] += 1
                break
        else:
            zones.append(order[0]); cnt[order[0]] += 1
    return zones


def mock_zone_tables(inputs: InputsState,
                     area_variation: Dict,
                     n_tables: int = 12,
                     n_samples: int = 0,
                     seed: int = 1234) -> tuple[List[Dict], List[Dict]]:
    """Random admissible arrangements for one area variation, scored with
    the journal equations. Returns ``(tables, raw_pool)`` in the same shape
    as :func:`models.zone_model.generate_zone_tables`."""
    from models.zone_model import PlanSetup        # no torch needed for PlanSetup
    if not area_variation or not area_variation.get("rooms"):
        return [], []
    ps = PlanSetup(inputs, area_variation)
    if ps.n == 0:
        return [], []
    prefs = inputs.prefs
    present = bool(getattr(prefs, "targets_enabled", True))
    tq = float(getattr(prefs, "target_qvci", 0.7) or 0.0)
    ta = float(getattr(prefs, "target_aqi", 0.6) or 0.0)
    n_total = max(int(n_tables), int(n_samples or 0))
    rng = random.Random(seed)

    tables: List[Dict] = []
    raw_pool: List[Dict] = []
    seen: Dict[tuple, Dict] = {}
    for _ in range(n_total):
        zones = _random_valid(ps, rng)
        key = tuple(zones)
        if key in seen:
            raw_pool.append(dict(seen[key]))
            continue
        t = ps.table(zones, (tq, ta), present, decode="random")
        t["model"] = "mock"
        c = t["computed"]
        m = {"qvci": c["qvci"], "aqi": c["aqi"], "concentration": c["concentration"],
             "d_target": c["d_target"], "valid": c["valid"]}
        seen[key] = m
        raw_pool.append(dict(m))
        tables.append(t)
    if present:
        tables.sort(key=lambda t: t["computed"]["d_target"])
    for i, t in enumerate(tables):
        t["table_index"] = i
    return tables, raw_pool
