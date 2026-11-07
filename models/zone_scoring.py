"""
models/zone_scoring.py
======================
The journal-paper (v13) scoring module, shared by the real zone model,
the mock fallback and every panel that shows a number derived from a
zone assignment.  It is a verbatim port of ``corpus_v13/scoring.py``
(the module the training corpus was labelled with) plus the capacity
rule of ``corpus_v13/arrangements.py`` — so the app scores an
arrangement exactly the way the corpus and the paper's evaluation do.

Pure functions on plain dicts, no Qt, no torch.

Room dict (metres):  type, area (m²), w (E–W extent, m), h (N–S extent, m),
                     perim (m), wwr (planned window-to-wall ratio),
                     edr (exterior-door ratio, 0 unless the room holds the
                     front door)
Site dict:           {'views': {N,S,E,W: category}, 'L': {N,S,E,W: dB(A)}}

Zones are numbered 1 … 9 here (1 NW, 2 N, 3 NE, 4 W, 5 interior, 6 E,
7 SW, 8 S, 9 SE) exactly as in the corpus; the UI uses 0 … 8.

Indices (decisions of 10–11 Oct 2026)
-------------------------------------
* QVCI = Σ_i w_i · q_i   with q_i = v(z_i) · min(1, WWR_i / 0.40)
      v(z) = best façade view weight of the zone (Vegetation 1.0, Sky 0.9,
      Urban 0.7, Balcony 0.4, No View 0), interior 0.
* AQI  = Σ_i w_i · a_i   with a_i = clip((G_t + 15 − L_in,i) / 30, 0, 1)
      G_t = 30 / 35 / 40 / 45 dB(A) for bedroom / living / kitchen /
      bathroom; L_in from the façade levels, the planned WWR and the room
      geometry (STC 45 / 32 / 25 wall / window / door, 2.7 m ceiling,
      absorption 0.15), floored at 20 dB(A); interior zone = mean outdoor
      level − 51 dB.
* w_i = area share over the regularly occupied rooms (living, kitchen,
      bedroom); bathrooms carry weight 0 but are still placed and scored.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# ── constants (corpus_v13.scoring) ──────────────────────────────────
H = 2.7                       # ceiling height, m
ALPHA = 0.15                  # mean absorption coefficient of the room surfaces
STC_WALL, STC_WIN, STC_DOOR = 45, 32, 25
INTERIOR_ATT = 51             # interior zone: mean outdoor level minus this (dB)
L_IN_FLOOR = 20.0             # no room is quieter than this (dB(A))
GUIDELINE = {"bedroom": 30.0, "living": 35.0, "kitchen": 40.0, "bathroom": 45.0}
HALF_WIDTH = 15.0             # acoustic score: 1 at G−15, 0.5 at G, 0 at G+15
VIEW_WEIGHTS = {"Vegetation": 1.0, "Sky": 0.9, "Urban": 0.7, "Balcony": 0.4, "No View": 0.0}
WWR_REF = 0.40                # full window credit at 40 % glazing
L_RANGE = (30.0, 80.0)        # outdoor level the corpus (and the model) covers, dB(A)

ROOM_TYPES = ["living", "kitchen", "bedroom", "bathroom"]      # the zoned vocabulary
SCORED_TYPES = {"living", "kitchen", "bedroom"}                # regularly occupied → index weight
DIRS = ["N", "S", "E", "W"]
ZONE_SIDES = {1: ["N", "W"], 2: ["N"], 3: ["N", "E"], 4: ["W"], 5: [], 6: ["E"],
              7: ["S", "W"], 8: ["S"], 9: ["S", "E"]}
ZONE_CLASS = {1: "corner", 2: "cardinal", 3: "corner", 4: "cardinal", 5: "interior",
              6: "cardinal", 7: "corner", 8: "cardinal", 9: "corner"}
ZONE_RC = {1: (0, 0), 2: (0, 1), 3: (0, 2), 4: (1, 0), 5: (1, 1), 6: (1, 2), 7: (2, 0), 8: (2, 1), 9: (2, 2)}

_TAU_WIN, _TAU_DOOR, _TAU_WALL = 10 ** (-STC_WIN / 10), 10 ** (-STC_DOOR / 10), 10 ** (-STC_WALL / 10)

# ── capacity rule (corpus_v13.arrangements; 98th percentile of the real occupancy) ──
CAP_RULE = {"corner":   [(7, 2), (99, 3)],    # 2 rooms per corner zone, 3 for plans with ≥ 8 rooms
            "cardinal": [(99, 3)],            # 3 per cardinal zone
            "interior": [(7, 1), (99, 2)]}    # 1 interior room (bathrooms only), 2 for ≥ 8 rooms
INTERIOR_BATHROOMS_ONLY = True                # habitable rooms must have a façade


# ═══════════════════════════════════════════════════════════════
#  Scoring (verbatim port)
# ═══════════════════════════════════════════════════════════════

def indoor_level(room: dict, zone: int, site: dict) -> float:
    """Indoor level (dB(A)) of a room placed in a zone (1 … 9). The planned
    WWR applies to every façade of the zone."""
    sides = ZONE_SIDES[zone]
    if not sides:
        return max(sum(site["L"][d] for d in DIRS) / 4.0 - INTERIOR_ATT, L_IN_FLOOR)
    A = max(ALPHA * (2.0 * room["area"] + room["perim"] * H), 1.0)
    wwr = min(max(room["wwr"], 0.0), 0.95)
    edr = min(max(room.get("edr", 0.0), 0.0), 0.95)
    sol = max(1.0 - wwr - edr, 0.0)
    tau = wwr * _TAU_WIN + edr * _TAU_DOOR + sol * _TAU_WALL
    TL = -10.0 * math.log10(max(tau, 1e-12))
    energy = 0.0
    for d in sides:
        length = room["w"] if d in ("N", "S") else room["h"]
        S = max(length * H, 1.0)
        energy += 10 ** ((site["L"][d] - TL + 10.0 * math.log10(S / A)) / 10.0)
    return max(10.0 * math.log10(energy), L_IN_FLOOR) if energy > 1e-15 else L_IN_FLOOR


def score_a(room_type: str, L_in: float) -> float:
    G = GUIDELINE[room_type]
    return float(min(max((G + HALF_WIDTH - L_in) / (2.0 * HALF_WIDTH), 0.0), 1.0))


def zone_view(site: dict) -> List[float]:
    """v(z) for z = 1 … 9: best façade view weight of the zone, interior 0."""
    w = {d: VIEW_WEIGHTS.get(site["views"][d], 0.0) for d in DIRS}
    return [max((w[d] for d in ZONE_SIDES[z]), default=0.0) for z in range(1, 10)]


def zone_level(site: dict) -> List[float]:
    """Mean outdoor level of the zone's façades (dB(A)); interior = mean of all four."""
    out = []
    for z in range(1, 10):
        sides = ZONE_SIDES[z]
        out.append(sum(site["L"][d] for d in sides) / len(sides) if sides
                   else sum(site["L"][d] for d in DIRS) / 4.0)
    return out


def score_q(room: dict, zone: int, zv: Sequence[float]) -> float:
    return float(zv[zone - 1] * min(1.0, room["wwr"] / WWR_REF))


def weights(rooms: Sequence[dict]) -> np.ndarray:
    """Area weights over the regularly occupied rooms; bathrooms get 0."""
    w = np.array([r["area"] if r["type"] in SCORED_TYPES else 0.0 for r in rooms], dtype=float)
    s = w.sum()
    return w / s if s > 0 else w


def tables(rooms: Sequence[dict], site: dict):
    """Per-room score tables A, Q, L (n × 9) for a site, plus v(z)."""
    zv = zone_view(site)
    L = np.array([[indoor_level(r, z, site) for z in range(1, 10)] for r in rooms])
    A = np.array([[score_a(r["type"], L[i, z - 1]) for z in range(1, 10)] for i, r in enumerate(rooms)])
    Q = np.array([[score_q(r, z, zv) for z in range(1, 10)] for r in rooms])
    return A, Q, L, zv


def evaluate(zones: Sequence[int], A: np.ndarray, Q: np.ndarray, w: np.ndarray) -> Tuple[float, float]:
    """(QVCI, AQI) of an arrangement; ``zones`` are 1-based."""
    idx = np.arange(len(zones))
    z = np.asarray(zones) - 1
    return float((w * Q[idx, z]).sum()), float((w * A[idx, z]).sum())


# ═══════════════════════════════════════════════════════════════
#  Capacity rule, validity, type rule
# ═══════════════════════════════════════════════════════════════

def zone_allowed(room_type: str, z: int, interior_bathrooms_only: bool = INTERIOR_BATHROOMS_ONLY) -> bool:
    return not (interior_bathrooms_only and z == 5 and room_type in SCORED_TYPES)


def capacity(n_rooms: int, override: Optional[Dict[str, int]] = None) -> Dict[str, int]:
    """Rooms allowed per zone, by zone class — by plan size unless overridden."""
    cap: Dict[str, int] = {}
    for cls, steps in CAP_RULE.items():
        for n_max, c in steps:
            if n_rooms <= n_max:
                cap[cls] = c
                break
    if override:
        for cls in ("corner", "cardinal", "interior"):
            v = override.get(cls)
            if v is not None and int(v) > 0:
                cap[cls] = int(v)
    return cap


def capacity_vector(cap: Dict[str, int]) -> List[int]:
    """Capacity per zone 1 … 9 as a list (index 0 = zone 1)."""
    return [cap[ZONE_CLASS[z]] for z in range(1, 10)]


def counts(zones: Sequence[int]) -> List[int]:
    c = [0] * 10
    for z in zones:
        c[z] += 1
    return c


def valid(zones: Sequence[int], cap: Dict[str, int], types: Optional[Sequence[str]] = None,
          interior_bathrooms_only: bool = INTERIOR_BATHROOMS_ONLY) -> bool:
    c = counts(zones)
    if len(zones) > 1 and max(c[1:]) == len(zones):
        return False                                         # never all rooms in one zone
    if types is not None and any(not zone_allowed(t, z, interior_bathrooms_only)
                                 for t, z in zip(types, zones)):
        return False
    return all(c[z] <= cap[ZONE_CLASS[z]] for z in range(1, 10))


# ═══════════════════════════════════════════════════════════════
#  Reachable range
# ═══════════════════════════════════════════════════════════════

def _assignment_bound(M: np.ndarray, w: np.ndarray, allowed: np.ndarray,
                      capv: Sequence[int], maximize: bool) -> float:
    """Best (or worst) Σ w_i M[i, z_i] over capacity-respecting, type-respecting
    assignments — an assignment problem on zone *slots* (zone z repeated
    cap_z times).  Uses scipy's Hungarian solver; falls back to the
    per-room bound when scipy is missing."""
    n = M.shape[0]
    slots = [z for z in range(9) for _ in range(int(capv[z]))]
    if len(slots) < n:
        slots = slots + [z for z in range(9) for _ in range(n)]       # degenerate caps: relax
    try:
        from scipy.optimize import linear_sum_assignment
    except Exception:
        big = 1e9
        if maximize:
            return float((w * np.where(allowed, M, -big).max(1)).sum())
        return float((w * np.where(allowed, M, big).min(1)).sum())
    C = np.zeros((n, len(slots)))
    for j, z in enumerate(slots):
        col = w * M[:, z]
        col = np.where(allowed[:, z], col, (-1e6 if maximize else 1e6))
        C[:, j] = -col if maximize else col
    r, c = linear_sum_assignment(C)
    val = sum((w[i] * M[i, slots[j]]) for i, j in zip(r, c) if allowed[i, slots[j]])
    return float(val)


def reachable(A: np.ndarray, Q: np.ndarray, w: np.ndarray, allowed: Optional[np.ndarray] = None,
              capv: Optional[Sequence[int]] = None) -> Dict[str, Tuple[float, float]]:
    """Reachable (min, max) of QVCI and AQI for this plan–site.

    ``allowed`` (n × 9 bool) applies the type rule; ``capv`` (9 ints) the
    capacity rule.  Without ``capv`` the bound is the per-room one (every
    room in its own best/worst zone), which is the corpus definition used
    for the evaluation probes; with it the bound is the exact
    capacity-respecting optimum, i.e. what the generator can actually reach."""
    n = A.shape[0]
    if allowed is None:
        allowed = np.ones((n, 9), dtype=bool)
    if capv is None:
        big = 1e9
        Qlo = np.where(allowed, Q, big).min(1); Qhi = np.where(allowed, Q, -big).max(1)
        Alo = np.where(allowed, A, big).min(1); Ahi = np.where(allowed, A, -big).max(1)
        return {"Q": (float((w * Qlo).sum()), float((w * Qhi).sum())),
                "A": (float((w * Alo).sum()), float((w * Ahi).sum()))}
    return {"Q": (_assignment_bound(Q, w, allowed, capv, False), _assignment_bound(Q, w, allowed, capv, True)),
            "A": (_assignment_bound(A, w, allowed, capv, False), _assignment_bound(A, w, allowed, capv, True))}


# ═══════════════════════════════════════════════════════════════
#  App-side helpers: rooms and site from the session inputs
# ═══════════════════════════════════════════════════════════════

UNIT_TO_M = {"m": 1.0, "ft": 0.3048}


def unit_scale_to_m(units: str, px_per_m: float = 100.0) -> float:
    """Metres per plan unit (px needs the user's scale)."""
    if units == "px":
        return 1.0 / max(float(px_per_m), 1e-6)
    return UNIT_TO_M.get(units, 1.0)


def site_from_inputs(site_conditions) -> dict:
    """``SiteConditions`` dataclass → scoring site dict (levels clipped to the
    corpus range 30–80 dB(A))."""
    views = {}; L = {}
    for d in DIRS:
        side = getattr(site_conditions, d, None)
        views[d] = (getattr(side, "view", None) or "No View") if side is not None else "No View"
        lvl = float(getattr(side, "spl_dB", 60.0)) if side is not None else 60.0
        L[d] = min(max(lvl, L_RANGE[0]), L_RANGE[1])
    return {"views": views, "L": L}


def rooms_from_variation(area_variation: dict, inputs, front_door_type: str = "living",
                         edr_default: float = 0.15) -> Tuple[List[dict], List[dict]]:
    """Split a Stage-1 area variation into (zoned rooms, extra rooms).

    Zoned rooms are the four model types, expressed in metres with the
    per-type aspect ratio (h / w) and the planned WWR from Stage 0; the
    largest room of ``front_door_type`` holds the front door (EDR
    ``edr_default``, the corpus median).  Extra rooms (balconies and any
    other type) are carried through to connectivity without a zone.
    """
    scale = unit_scale_to_m(inputs.plan_geometry.units,
                            getattr(inputs.plan_geometry, "px_per_m", 100.0))
    ar_dict = dict(getattr(inputs, "aspect_ratios", {}) or {})
    wwr_dict = dict(getattr(inputs, "wwr", {}) or {})
    zoned: List[dict] = []
    extra: List[dict] = []
    for r in area_variation.get("rooms", []) or []:
        rt = str(r.get("room_type", "other")).lower()
        area_units = float(r["area"])
        area_m2 = max(area_units * scale * scale, 0.5)
        if r.get("w_m") and r.get("h_m"):                      # drawn geometry: own shape
            ar = float(r["h_m"]) / float(r["w_m"])                # N–S ÷ E–W
        else:
            ar = float(ar_dict.get(rt, r.get("aspect_ratio", 1.0)) or 1.0)
        ar = min(max(ar, 0.2), 5.0)
        w = math.sqrt(area_m2 / ar); h = math.sqrt(area_m2 * ar)
        rec = {"id": r["room_id"], "type": rt, "area": area_m2, "area_units": area_units,
               "w": w, "h": h, "perim": 2.0 * (w + h), "aspect_ratio": ar,
               "drawn": bool(r.get("w_m") and r.get("h_m")),
               "wwr": float(wwr_dict.get(rt, r.get("wwr", 0.0)) or 0.0),
               "edr": 0.0, "has_front_door": False}
        (zoned if rt in ROOM_TYPES else extra).append(rec)
    fd = [r for r in zoned if r["type"] == front_door_type]
    if not fd:
        fd = zoned
    if fd:
        big = max(fd, key=lambda r: r["area"])
        big["has_front_door"] = True
        big["edr"] = float(edr_default)
    return zoned, extra


def plan_level_spl(levels: Sequence[float]) -> float:
    """Energy-average of per-room indoor levels (dB(A))."""
    vals = [float(v) for v in levels if v is not None]
    if not vals:
        return 0.0
    return 10.0 * math.log10(sum(10 ** (v / 10.0) for v in vals) / len(vals))
