"""
models/zone_model.py
====================
Real-model wrapper for Stage 2 (Zone Assignment) — **journal version**.

Runs the v13 cGAT-AR zone model trained on the outcome-labelled
arrangement corpus (``corpus_v13``, Oct 2026): a GAT encoder over the
rooms (site context broadcast to every room) and a GRU decoder that
assigns one zone per room in a fixed order, conditioned on the requested
(QVCI, AQI), a target-present flag, the rooms remaining, the zone usage
so far, v(z) and the zone noise.  A capacity mask (rooms per zone, by
plan size) and the type rule (habitable rooms never interior) are
applied inside the softmax, exactly as in training.

Everything the UI shows is computed with :mod:`models.zone_scoring`,
the same module the corpus was labelled with, so a card's QVCI / AQI
is the value the paper's evaluation would report for that arrangement.

Checkpoint discovery order:
    1. env  LAYOUTGEN_ZONE_CKPT
    2. assets/checkpoints/zone_model_v13.pt
    3. assets/checkpoints/best_model.pt
The thesis-era ``zone_model.pt`` (v8 / v11 / v12 format) is refused
with a clear message — its features, targets and indices are not the
journal's.

Output schema — one "table" per unique arrangement (see
``models/zone_mock.py`` for the field list).  Zone indices in the
per-room ``zone_assignment`` are 0-indexed (0 NW … 4 interior … 8 SE)
for the UI; rooms that are not zoned by the model (balconies, any type
outside the four-type vocabulary) carry ``zone_assignment = None``.
"""

from __future__ import annotations

import math
import os
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    _TORCH_OK = True
except ImportError:                                     # pragma: no cover
    _TORCH_OK = False
    torch = None  # type: ignore[assignment]
    nn = None     # type: ignore[assignment]
    F = None      # type: ignore[assignment]

from app.state import InputsState
from models import zone_scoring as zs


# ═══════════════════════════════════════════════════════════════
#  CONSTANTS (mirror corpus_v13/train.py)
# ═══════════════════════════════════════════════════════════════

NUM_ZONES = 9
MAX_ROOMS = 14
TYPE_ORDER = {"living": 0, "kitchen": 1, "bedroom": 2, "bathroom": 3}
ROOM_FEAT_DIM = 4 + 7            # one-hot type, area/50, area fraction, wwr, w/√area, h/√area, edr, front door
CTX_DIM = 3 + 1 + 9 + 9 + 9      # targets(2) + present, rooms remaining, zone usage, v(z), zone noise
GNN_DIM, GNN_LAYERS, GNN_HEADS, GRU_DIM, GRU_LAYERS, DROPOUT = 128, 2, 4, 256, 2, 0.1
MODEL_LABEL = "v13 cGAT-AR (outcome-labelled corpus, Oct 2026)"

VIEW_OPTIONS = ["Vegetation", "Sky", "Urban", "Balcony", "No View"]
AQI_COLLAPSED_WIDTH = 0.05       # reachable AQI width below this: the site cannot change AQI
AQI_LIVE_WIDTH = 0.15            # the evaluation's "live site" threshold


class ModelNotAvailable(RuntimeError):
    """Raised when the real model cannot be used. Stage 2 falls back to mock."""


# ═══════════════════════════════════════════════════════════════
#  FEATURES (ported verbatim)
# ═══════════════════════════════════════════════════════════════

def room_order(rooms: Sequence[dict]) -> List[int]:
    """Decoding order: living, kitchen, bedroom, bathroom; larger first."""
    return sorted(range(len(rooms)), key=lambda i: (TYPE_ORDER[rooms[i]["type"]], -rooms[i]["area"]))


def room_features(rooms: Sequence[dict]) -> np.ndarray:
    tot = sum(r["area"] for r in rooms)
    X = np.zeros((len(rooms), ROOM_FEAT_DIM), dtype=np.float32)
    for i, r in enumerate(rooms):
        X[i, TYPE_ORDER[r["type"]]] = 1.0
        sq = math.sqrt(max(r["area"], 0.5))
        X[i, 4:] = [r["area"] / 50.0, r["area"] / max(tot, 1.0), r["wwr"], r["w"] / sq, r["h"] / sq,
                    r.get("edr", 0.0), float(r.get("has_front_door", False))]
    return X


def site_context(site: dict) -> Tuple[np.ndarray, np.ndarray]:
    zv = np.array(zs.zone_view(site), dtype=np.float32)
    zn = np.array([(np.mean([site["L"][d] for d in zs.ZONE_SIDES[z]]) - 30.0) / 50.0 if zs.ZONE_SIDES[z] else 0.0
                   for z in range(1, 10)], dtype=np.float32)
    return zv, zn


# ═══════════════════════════════════════════════════════════════
#  MODEL CLASSES (torch-guarded; parameter names match the checkpoint)
# ═══════════════════════════════════════════════════════════════

if _TORCH_OK:

    class GATLayer(nn.Module):
        def __init__(self, din: int, dout: int, dropout: float) -> None:
            super().__init__()
            self.W = nn.Linear(din, dout, bias=False)
            self.a = nn.Linear(2 * dout, 1, bias=False)
            self.drop = nn.Dropout(dropout)

        def forward(self, x, mask):
            B, N, _ = x.shape
            h = self.W(x)
            e = F.leaky_relu(self.a(torch.cat([h.unsqueeze(2).expand(B, N, N, -1),
                                               h.unsqueeze(1).expand(B, N, N, -1)], -1)).squeeze(-1), 0.2)
            e = e.masked_fill((mask.unsqueeze(1) * mask.unsqueeze(2)) == 0, -1e9)
            return torch.bmm(self.drop(F.softmax(e, -1)), h)

    class MultiHeadGAT(nn.Module):
        def __init__(self, dim: int, heads: int, dropout: float) -> None:
            super().__init__()
            self.heads = nn.ModuleList([GATLayer(dim, dim // heads, dropout) for _ in range(heads)])
            self.norm = nn.LayerNorm(dim)
            self.drop = nn.Dropout(dropout)

        def forward(self, x, mask):
            return F.relu(self.drop(self.norm(torch.cat([h(x, mask) for h in self.heads], -1) + x)))

    class ZoneModel(nn.Module):
        """v13 cGAT-AR: GAT encoder + GRU decoder with capacity / type mask."""

        def __init__(self) -> None:
            super().__init__()
            self.proj = nn.Linear(ROOM_FEAT_DIM + 18, GNN_DIM)
            self.layers = nn.ModuleList([MultiHeadGAT(GNN_DIM, GNN_HEADS, DROPOUT) for _ in range(GNN_LAYERS)])
            self.gru = nn.GRU(GNN_DIM + CTX_DIM + NUM_ZONES, GRU_DIM, GRU_LAYERS, batch_first=True, dropout=DROPOUT)
            self.head = nn.Sequential(nn.Linear(GRU_DIM, 128), nn.ReLU(), nn.Dropout(DROPOUT), nn.Linear(128, NUM_ZONES))

        def encode(self, feats, zv, zn, mask):
            B, N, _ = feats.shape
            x = torch.cat([feats, zv.unsqueeze(1).expand(B, N, 9), zn.unsqueeze(1).expand(B, N, 9)], -1)
            h = F.relu(self.proj(x))
            for l in self.layers:
                h = l(h, mask)
            return h

        @torch.no_grad()
        def generate(self, feats, mask, n, zv, zn, tgt, present, cap, type_allowed,
                     greedy=True, temperature: float = 1.0):
            """feats (B,N,11), mask (B,N), n (B,), zv/zn (B,9), tgt (B,2), present (B,),
            cap (B,9) float, type_allowed (B,N,9) bool → zones (B,N) 0-indexed."""
            dev = next(self.parameters()).device
            feats, mask, n, zv, zn, tgt, present, cap, type_allowed = [
                x.to(dev) for x in (feats, mask, n, zv, zn, tgt, present, cap, type_allowed)]
            B, N, _ = feats.shape
            emb = self.encode(feats, zv, zn, mask)
            counts = torch.zeros(B, 9, device=dev)
            prev = torch.zeros(B, 1, 9, device=dev)
            hidden = None
            out_z = torch.zeros(B, N, dtype=torch.long, device=dev)
            tgt3 = torch.cat([tgt, present.unsqueeze(1)], 1)
            for t in range(N):
                rooms_rem = ((n.float() - t) / n.float().clamp(min=1)).clamp(0, 1).unsqueeze(1)
                ctx = torch.cat([tgt3, rooms_rem, counts / n.unsqueeze(1).float().clamp(min=1), zv, zn], 1).unsqueeze(1)
                o, hidden = self.gru(torch.cat([emb[:, t:t + 1], ctx, prev], -1), hidden)
                logits = self.head(o.squeeze(1))
                allowed = (counts < cap) & type_allowed[:, t]
                last = (t == n - 1).unsqueeze(1) & (n.unsqueeze(1) > 1)
                allowed = allowed & ~(last & (counts + 1 == n.unsqueeze(1).float()))
                # Safety: if nothing is allowed (impossible caps), relax to the capacity mask alone.
                none_ok = ~allowed.any(1)
                if none_ok.any():
                    relaxed = counts < cap
                    allowed[none_ok] = relaxed[none_ok] | ~relaxed[none_ok].any(1, keepdim=True)
                logits = logits.masked_fill(~allowed, -1e9)
                if greedy:
                    z = logits.argmax(-1)
                else:
                    z = torch.multinomial(F.softmax(logits / max(float(temperature), 1e-6), -1), 1).squeeze(1)
                out_z[:, t] = z
                active = (t < n).float().unsqueeze(1)
                oh = F.one_hot(z, 9).float() * active
                counts = counts + oh
                prev = oh.unsqueeze(1)
            return out_z.cpu()


# ═══════════════════════════════════════════════════════════════
#  CHECKPOINT DISCOVERY + LOADING
# ═══════════════════════════════════════════════════════════════

def find_checkpoint() -> Optional[Path]:
    """Locate the v13 zone checkpoint on disk. None if nothing found."""
    env = os.environ.get("LAYOUTGEN_ZONE_CKPT")
    if env:
        p = Path(env)
        if p.is_file():
            return p
    here = Path(__file__).resolve().parent.parent          # repo root
    ckdir = here / "assets" / "checkpoints"
    if not ckdir.is_dir():
        return None
    for name in ("zone_model_v13.pt", "best_model.pt"):
        p = ckdir / name
        if p.is_file():
            return p
    for p in sorted(ckdir.glob("*.pt")):
        if "v13" in p.name.lower():
            return p
    return None


def _load_model(path: Path):
    """Returns (model, info dict)."""
    if not _TORCH_OK:
        raise ModelNotAvailable("PyTorch is not installed.")
    try:
        ck = torch.load(str(path), map_location="cpu", weights_only=False)
    except Exception as e:
        raise ModelNotAvailable(f"Failed to torch.load checkpoint: {e}") from e

    info: Dict = {"path": str(path), "label": MODEL_LABEL}
    if isinstance(ck, dict) and "model_state" in ck:
        sd = ck["model_state"]
        info.update({k: v for k, v in ck.items() if k != "model_state"})
    elif isinstance(ck, dict) and "config" in ck and "model" in ck:
        raise ModelNotAvailable(
            "This is a thesis-era (v8/v11/v12) checkpoint. The journal app needs the v13 "
            "model: assets/checkpoints/zone_model_v13.pt (best_model.pt from train_v13).")
    else:
        sd = ck
    if not isinstance(sd, dict) or "proj.weight" not in sd:
        raise ModelNotAvailable("Checkpoint does not contain a v13 ZoneModel state_dict.")
    if tuple(sd["proj.weight"].shape) != (GNN_DIM, ROOM_FEAT_DIM + 18):
        raise ModelNotAvailable(
            f"Unexpected input width {tuple(sd['proj.weight'].shape)} — not the v13 model "
            f"(expected ({GNN_DIM}, {ROOM_FEAT_DIM + 18})).")
    model = ZoneModel()
    try:
        model.load_state_dict(sd)
    except Exception as e:
        raise ModelNotAvailable(f"load_state_dict failed: {e}") from e
    model.eval()
    info["params"] = int(sum(p.numel() for p in model.parameters()))
    return model, info


_CACHE: Dict[str, tuple] = {}


def _get_cached(path: Path):
    key = str(path)
    cached = _CACHE.get(key)
    if cached is None:
        cached = _load_model(path)
        _CACHE[key] = cached
    return cached


def model_info(checkpoint_path: Optional[Path] = None) -> Optional[Dict]:
    """Info dict of the loaded checkpoint (None if unavailable). Never raises."""
    try:
        p = checkpoint_path or find_checkpoint()
        if p is None:
            return None
        return _get_cached(p)[1]
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
#  PLAN SET-UP (shared with the mock and the reachable-range readout)
# ═══════════════════════════════════════════════════════════════

def capacity_for(inputs: InputsState, n_zoned: int) -> Dict[str, int]:
    prefs = getattr(inputs, "prefs", None)
    override = getattr(prefs, "capacity_override", None) if prefs is not None else None
    return zs.capacity(n_zoned, override if override else None)


def interior_rule(inputs: InputsState) -> bool:
    prefs = getattr(inputs, "prefs", None)
    v = getattr(prefs, "interior_bathrooms_only", None) if prefs is not None else None
    return zs.INTERIOR_BATHROOMS_ONLY if v is None else bool(v)


class PlanSetup:
    """Everything that depends on the plan + site but not on the arrangement:
    rooms in metres (decode order), score tables, weights, capacity,
    allowed zones and the reachable range."""

    def __init__(self, inputs: InputsState, area_variation: dict) -> None:
        zoned, extra = zs.rooms_from_variation(area_variation, inputs)
        order = room_order(zoned)
        self.rooms: List[dict] = [zoned[j] for j in order]          # decode order
        self.extra: List[dict] = extra
        self.n = len(self.rooms)
        self.site = zs.site_from_inputs(inputs.site_conditions)
        self.types = [r["type"] for r in self.rooms]
        self.ibo = interior_rule(inputs)
        self.cap = capacity_for(inputs, self.n)
        self.capv = zs.capacity_vector(self.cap)
        self.allowed = np.array([[zs.zone_allowed(t, z, self.ibo) for z in range(1, 10)] for t in self.types], dtype=bool)
        if self.n:
            self.A, self.Q, self.L, self.zv = zs.tables(self.rooms, self.site)
            self.w = zs.weights(self.rooms)
            self.reach = zs.reachable(self.A, self.Q, self.w, self.allowed, self.capv)
            self.reach_free = zs.reachable(self.A, self.Q, self.w, self.allowed)
        else:
            self.A = self.Q = self.L = np.zeros((0, 9)); self.zv = zs.zone_view(self.site); self.w = np.zeros(0)
            self.reach = self.reach_free = {"Q": (0.0, 0.0), "A": (0.0, 0.0)}
        self.zlevel = zs.zone_level(self.site)

    # ── derived ───────────────────────────────────────────────────
    @property
    def aqi_width(self) -> float:
        return self.reach["A"][1] - self.reach["A"][0]

    @property
    def aqi_collapsed(self) -> bool:
        return self.aqi_width < AQI_COLLAPSED_WIDTH

    def reachable_dict(self) -> dict:
        return {"qvci": [round(self.reach["Q"][0], 4), round(self.reach["Q"][1], 4)],
                "aqi":  [round(self.reach["A"][0], 4), round(self.reach["A"][1], 4)],
                "qvci_free": [round(self.reach_free["Q"][0], 4), round(self.reach_free["Q"][1], 4)],
                "aqi_free":  [round(self.reach_free["A"][0], 4), round(self.reach_free["A"][1], 4)],
                "aqi_collapsed": bool(self.aqi_collapsed), "aqi_live": bool(self.aqi_width >= AQI_LIVE_WIDTH)}

    def zone_weight_dicts(self) -> Tuple[dict, dict, dict]:
        zw = {f"zone_{z}": round(float(self.zv[z]), 4) for z in range(9)}
        lv = {f"zone_{z}": round(float(self.zlevel[z]), 1) for z in range(9)}
        aw = {f"zone_{z}": round(float(1.0 - (self.zlevel[z] - 30.0) / 50.0), 4) for z in range(9)}   # quietness 0–1
        return zw, aw, lv

    def valid(self, zones1: Sequence[int]) -> bool:
        return zs.valid(zones1, self.cap, self.types, self.ibo)

    # ── scoring of one arrangement (zones 1-based, decode order) ───
    def table(self, zones1: Sequence[int], targets: Tuple[float, float], present: bool,
              decode: str = "sampled") -> dict:
        q, a = zs.evaluate(zones1, self.A, self.Q, self.w)
        rooms_out: List[dict] = []
        levels: List[float] = []
        for i, (r, z1) in enumerate(zip(self.rooms, zones1)):
            L_in = float(self.L[i, z1 - 1]); qv = float(self.Q[i, z1 - 1]); aq = float(self.A[i, z1 - 1])
            levels.append(L_in)
            rooms_out.append({
                "room_id":               r["id"],
                "room_type":             r["type"],
                "area":                  float(r["area_units"]),
                "area_m2":               round(float(r["area"]), 2),
                "zone_assignment":       int(z1) - 1,
                "quality_view_score":    round(qv, 4),
                "quality_view_achieved": bool(qv >= 0.5),
                "acoustic_score":        round(aq, 4),
                "acoustic_ok":           bool(L_in <= zs.GUIDELINE[r["type"]]),
                "est_avg_spl_dB":        round(L_in, 1),
                "guideline_dB":          zs.GUIDELINE[r["type"]],
                "wwr":                   float(r["wwr"]),
                "aspect_ratio":          float(r["aspect_ratio"]),
                "weight":                round(float(self.w[i]), 4),
                "scored":                r["type"] in zs.SCORED_TYPES,
                "zoned":                 True,
                **({"w_m": round(float(r["w"]), 3), "h_m": round(float(r["h"]), 3)}
                   if r.get("drawn") else {}),
            })
        for r in self.extra:
            rooms_out.append({
                "room_id": r["id"], "room_type": r["type"], "area": float(r["area_units"]),
                "area_m2": round(float(r["area"]), 2), "zone_assignment": None,
                "quality_view_score": None, "quality_view_achieved": None, "acoustic_score": None,
                "acoustic_ok": None, "est_avg_spl_dB": None, "guideline_dB": None,
                "wwr": float(r["wwr"]), "aspect_ratio": float(r["aspect_ratio"]), "weight": 0.0,
                "scored": False, "zoned": False,
                **({"w_m": round(float(r["w"]), 3), "h_m": round(float(r["h"]), 3)}
                   if r.get("drawn") else {}),
            })
        zc = Counter(int(z) for z in zones1)
        tq, ta = float(targets[0]), float(targets[1])
        zw, aw, lv = self.zone_weight_dicts()
        return {
            "table_index": 0,
            "computed": {
                "qvci":          round(q, 4),
                "aqi":           round(a, 4),
                "avg_spl_dB":    round(zs.plan_level_spl(levels), 2),
                "zone_spread":   int(len(zc)),
                "concentration": round(max(zc.values()) / max(self.n, 1), 4) if zc else 0.0,
                "d_target":      round(abs(q - tq) + abs(a - ta), 4) if present else None,
                "valid":         bool(self.valid(zones1)),
            },
            "targets":          {"qvci": tq, "aqi": ta, "present": bool(present)},
            "reachable":        self.reachable_dict(),
            "capacity":         dict(self.cap),
            "zone_weights":     zw,
            "acoustic_weights": aw,
            "zone_levels_dB":   lv,
            "decode":           decode,
            "model":            "v13",
            "rooms":            rooms_out,
        }


def reachable_range(inputs: InputsState, area_variation: Optional[dict]) -> Optional[dict]:
    """Reachable QVCI / AQI for the current inputs and area variation (None
    when there is nothing to score). Used by the Stage 0 / Stage 2 readouts;
    never raises."""
    try:
        if not area_variation or not area_variation.get("rooms"):
            return None
        ps = PlanSetup(inputs, area_variation)
        if ps.n == 0:
            return None
        d = ps.reachable_dict(); d["n_zoned"] = ps.n; d["capacity"] = dict(ps.cap)
        return d
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
#  PUBLIC ENTRY POINT
# ═══════════════════════════════════════════════════════════════

def generate_zone_tables(
        inputs: InputsState,
        area_variation: Dict,
        n_tables: int = 12,
        n_samples: int = 80,
        temperature: float = 0.8,
        checkpoint_path: Optional[Path] = None,
        seed: Optional[int] = None,
) -> Tuple[List[Dict], List[Dict]]:
    """Run the v13 zone model. Raises ``ModelNotAvailable`` on any failure.

    One greedy decode plus ``n_samples`` sampled decodes at ``temperature``
    (the paper's M = 12, T = 0.8 protocol generalised to the pool size the
    user asks for).  Returns ``(tables, raw_pool)``: ``tables`` holds every
    unique arrangement (dedup on the zoned rooms), sorted by distance to
    the requested targets; ``raw_pool`` holds one metric dict per decoded
    sample (duplicates included) for the Stage-4 Pareto scatter.
    """
    if not _TORCH_OK:
        raise ModelNotAvailable("PyTorch is not installed.")
    if not area_variation or not area_variation.get("rooms"):
        return [], []

    if checkpoint_path is None:
        checkpoint_path = find_checkpoint()
    if checkpoint_path is None or not checkpoint_path.is_file():
        raise ModelNotAvailable(
            "No v13 zone checkpoint on disk. Copy train_v13/best_model.pt to "
            "assets/checkpoints/zone_model_v13.pt (or set LAYOUTGEN_ZONE_CKPT).")
    model, _info = _get_cached(checkpoint_path)

    ps = PlanSetup(inputs, area_variation)
    if ps.n == 0:
        raise ModelNotAvailable("No zoned room types (living / kitchen / bedroom / bathroom) in this variation.")
    if ps.n > MAX_ROOMS:
        raise ModelNotAvailable(f"{ps.n} zoned rooms — the v13 model was trained up to {MAX_ROOMS}.")

    prefs = inputs.prefs
    present = bool(getattr(prefs, "targets_enabled", True))
    tq = float(getattr(prefs, "target_qvci", 0.7) or 0.0)
    ta = float(getattr(prefs, "target_aqi", 0.6) or 0.0)

    # ── tensors ───────────────────────────────────────────────────
    B = max(int(n_samples), 1) + 1                      # + 1 greedy decode
    feats = torch.from_numpy(room_features(ps.rooms)).unsqueeze(0).expand(B, -1, -1).contiguous()
    mask = torch.ones(B, ps.n)
    n_t = torch.full((B,), ps.n, dtype=torch.long)
    zv, zn = site_context(ps.site)
    zv_t = torch.from_numpy(zv).unsqueeze(0).expand(B, -1).contiguous()
    zn_t = torch.from_numpy(zn).unsqueeze(0).expand(B, -1).contiguous()
    tgt = torch.tensor([[tq, ta] if present else [0.0, 0.0]] * B, dtype=torch.float32)
    pres = torch.full((B,), 1.0 if present else 0.0)
    cap = torch.tensor([ps.capv] * B, dtype=torch.float32)
    allowed = torch.from_numpy(ps.allowed).unsqueeze(0).expand(B, -1, -1).contiguous()

    if seed is not None:
        torch.manual_seed(int(seed))
    z_greedy = model.generate(feats[:1], mask[:1], n_t[:1], zv_t[:1], zn_t[:1], tgt[:1], pres[:1], cap[:1], allowed[:1],
                              greedy=True)
    z_samp = model.generate(feats[1:], mask[1:], n_t[1:], zv_t[1:], zn_t[1:], tgt[1:], pres[1:], cap[1:], allowed[1:],
                            greedy=False, temperature=float(temperature))
    assigned = torch.cat([z_greedy, z_samp], 0).numpy()

    # ── tables ────────────────────────────────────────────────────
    tables: List[Dict] = []
    raw_pool: List[Dict] = []
    seen: Dict[tuple, Dict] = {}
    for si in range(B):
        zones1 = [int(z) + 1 for z in assigned[si, :ps.n]]
        key = tuple(zones1)
        cached = seen.get(key)
        if cached is not None:
            raw_pool.append(dict(cached))
            continue
        t = ps.table(zones1, (tq, ta), present, decode="greedy" if si == 0 else "sampled")
        c = t["computed"]
        metrics = {"qvci": c["qvci"], "aqi": c["aqi"], "concentration": c["concentration"],
                   "d_target": c["d_target"], "valid": c["valid"]}
        seen[key] = metrics
        raw_pool.append(dict(metrics))
        tables.append(t)

    if present:
        tables.sort(key=lambda t: (t["computed"]["d_target"], t["decode"] != "greedy"))
    else:
        tables.sort(key=lambda t: (t["decode"] != "greedy", -t["computed"]["qvci"] - t["computed"]["aqi"]))
    for i, t in enumerate(tables):
        t["table_index"] = i
    return tables, raw_pool
