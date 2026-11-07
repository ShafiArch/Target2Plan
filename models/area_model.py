"""
Area-distribution model — real LogisticNormalNet wrapper.

This is the production replacement for ``models/area_mock.py``. It loads
a trained LogisticNormalNet checkpoint and runs the full inference
pipeline from the existing PySide tool
(``Area_distribution_generation_code_pyside_v5_Final.py``):

    log1p(counts)  →  μ, log_σ
                  ↓
              200 draws of  softmax(μ + σ·diversity·ε)
                  ↓
        FarthestPoint or KMeans → 12 representative share-vectors
                  ↓
        Expand each variation: area_per_room = share × total_area / count
                  ↓
        Pack circles (``pack_circles_natural`` reused from area_mock)

If PyTorch isn't installed or no checkpoint is found,
``generate_area_variations`` raises ``ModelNotAvailable``. The Stage 1
panel catches that and falls back to the mock — so the app keeps
running while the user is wiring up checkpoints.

Output schema is byte-identical to ``mock_area_variations`` (see
``models/area_mock.py`` for the dict shape).

Checkpoint format (matches the existing PySide tool):
  ckpt["vocab"]        — list[str], room types in input/output order
  ckpt["config"]       — {"HIDDEN_DIM", "LATENT_DIM", "DROPOUT"}
  ckpt["model_state"]  — state_dict
  ckpt["epoch"]        — optional, for log messages
"""

from __future__ import annotations

import math
import os
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    _TORCH_OK = True
except ImportError:
    _TORCH_OK = False

from app.state import InputsState
from models.area_mock import pack_circles_natural   # reuse the real packer


# ─────────────────────────────────────────────────────────────────────
# Constants — match the existing PySide tool
# ─────────────────────────────────────────────────────────────────────
SAMPLES_N           = 200
DEFAULT_VARIATIONS  = 12
DEFAULT_DIVERSITY   = 1.0


class ModelNotAvailable(Exception):
    """Raised when the real model can't be used. Caller falls back to mock."""


# ─────────────────────────────────────────────────────────────────────
# Model class (verbatim port from Area_distribution_generation_code_pyside_v5_Final.py)
# ─────────────────────────────────────────────────────────────────────
if _TORCH_OK:
    class LogisticNormalNet(nn.Module):
        """fc1 → drop → fc2 → drop → to_latent → (mu_head, logsig_head)."""

        def __init__(self, input_dim: int, output_dim: int,
                     hidden_dim: int = 128, latent_dim: int = 16,
                     dropout: float = 0.10) -> None:
            super().__init__()
            self.fc1         = nn.Linear(input_dim, hidden_dim)
            self.fc2         = nn.Linear(hidden_dim, hidden_dim)
            self.drop        = nn.Dropout(dropout)
            self.to_latent   = nn.Linear(hidden_dim, latent_dim)
            self.mu_head     = nn.Linear(latent_dim, output_dim)
            self.logsig_head = nn.Linear(latent_dim, output_dim)

        def forward(self, x):
            h = F.relu(self.fc1(x));  h = self.drop(h)
            h = F.relu(self.fc2(h));  h = self.drop(h)
            z = self.to_latent(h)
            mu        = self.mu_head(z)
            log_sigma = self.logsig_head(z)
            return h, z, mu, log_sigma


# ─────────────────────────────────────────────────────────────────────
# Math helpers (verbatim port)
# ─────────────────────────────────────────────────────────────────────
def _softmax_np(x: np.ndarray, axis: int = -1) -> np.ndarray:
    x = x - np.max(x, axis=axis, keepdims=True)
    e = np.exp(x)
    return e / (np.sum(e, axis=axis, keepdims=True) + 1e-12)


def _kmeans_representatives(X: np.ndarray, k: int,
                             seed: int = 42, iters: int = 25) -> List[int]:
    n, d = X.shape
    k = max(1, min(k, n))
    rng = np.random.RandomState(seed)
    init_idx = rng.choice(n, size=k, replace=False)
    C = X[init_idx].copy()
    for _ in range(iters):
        dist = ((X[:, None, :] - C[None, :, :]) ** 2).sum(axis=2)
        labels = dist.argmin(axis=1)
        newC = C.copy()
        for j in range(k):
            mask = (labels == j)
            if mask.any():
                newC[j] = X[mask].mean(axis=0)
            else:
                newC[j] = X[rng.randint(0, n)]
        C = newC
    dist = ((X[:, None, :] - C[None, :, :]) ** 2).sum(axis=2)
    reps = dist.argmin(axis=0)
    seen, out = set(), []
    for i in reps.tolist():
        if i not in seen:
            out.append(i); seen.add(i)
    return out


def _farthest_point_indices(X: np.ndarray, k: int, seed: int = 42) -> List[int]:
    n = X.shape[0]
    k = max(1, min(k, n))
    rng = np.random.RandomState(seed)
    start = rng.randint(0, n)
    chosen = [start]
    d2 = ((X - X[start]) ** 2).sum(axis=1)
    for _ in range(1, k):
        nxt = int(np.argmax(d2))
        chosen.append(nxt)
        d2 = np.minimum(d2, ((X - X[nxt]) ** 2).sum(axis=1))
    seen, out = set(), []
    for i in chosen:
        if i not in seen:
            out.append(i); seen.add(i)
    return out


# ─────────────────────────────────────────────────────────────────────
# Checkpoint discovery
# ─────────────────────────────────────────────────────────────────────
def find_checkpoint() -> Optional[Path]:
    """Look for an area-model .pt in conventional locations.

    Priority:
      1. Env var  LAYOUTGEN_AREA_CKPT  (if set and the file exists)
      2. assets/checkpoints/area_model.pt
      3. assets/checkpoints/area_distribution.pt
      4. Any .pt file in assets/checkpoints/ (first match, alphabetical)
    """
    env = os.environ.get("LAYOUTGEN_AREA_CKPT")
    if env and Path(env).is_file():
        return Path(env)

    repo_root = Path(__file__).resolve().parent.parent
    ckpt_dir  = repo_root / "assets" / "checkpoints"
    for name in ("area_model.pt", "area_distribution.pt"):
        p = ckpt_dir / name
        if p.is_file():
            return p
    if ckpt_dir.is_dir():
        for p in sorted(ckpt_dir.glob("*.pt")):
            return p
    return None


# ─────────────────────────────────────────────────────────────────────
# Cached loader (avoid re-loading the checkpoint on every regenerate)
# ─────────────────────────────────────────────────────────────────────
_LOADED: Dict[str, Tuple[object, List[str], str]] = {}


def load_model(checkpoint_path: Optional[str | Path] = None,
               device: str = "cpu") -> Tuple[object, List[str], str]:
    """Load (and cache) a LogisticNormalNet checkpoint.

    Returns ``(model, vocab, device_str)``.
    Raises ``ModelNotAvailable`` on any failure — torch missing, no
    checkpoint, malformed file, state-dict mismatch, etc.
    """
    if not _TORCH_OK:
        raise ModelNotAvailable("PyTorch is not installed.")

    if checkpoint_path is None:
        checkpoint_path = find_checkpoint()
        if checkpoint_path is None:
            raise ModelNotAvailable(
                "No area-model checkpoint found. Drop a .pt file at "
                "assets/checkpoints/area_model.pt, or set the env var "
                "LAYOUTGEN_AREA_CKPT to point at one.")
    checkpoint_path = Path(checkpoint_path)
    key = str(checkpoint_path.resolve())
    if key in _LOADED:
        return _LOADED[key]

    try:
        ckpt = torch.load(checkpoint_path, map_location=device,
                          weights_only=False)
    except Exception as e:
        raise ModelNotAvailable(f"Failed to load checkpoint: {e}")

    vocab = ckpt.get("vocab")
    if not vocab:
        raise ModelNotAvailable("Checkpoint missing 'vocab' key.")
    cfg  = ckpt.get("config", {}) or {}
    hdim = int(cfg.get("HIDDEN_DIM", 128))
    ldim = int(cfg.get("LATENT_DIM", 16))
    drop = float(cfg.get("DROPOUT", 0.10))
    K    = len(vocab)

    model = LogisticNormalNet(K, K, hidden_dim=hdim, latent_dim=ldim,
                               dropout=drop).to(device)
    try:
        model.load_state_dict(ckpt["model_state"])
    except Exception as e:
        raise ModelNotAvailable(f"state_dict load failed: {e}")
    model.eval()

    _LOADED[key] = (model, list(vocab), device)
    return _LOADED[key]


def clear_cache() -> None:
    """Drop the cached models — call after dropping in a new checkpoint."""
    _LOADED.clear()


# ─────────────────────────────────────────────────────────────────────
# Public API — same signature shape as mock_area_variations
# ─────────────────────────────────────────────────────────────────────
def generate_area_variations(
        inputs: InputsState,
        n_variations: int = DEFAULT_VARIATIONS,
        n_samples: int = SAMPLES_N,
        diversity: float = DEFAULT_DIVERSITY,
        seed: Optional[int] = 42,
        method: str = "FarthestPoint",      # or "KMeans"
        checkpoint_path: Optional[str | Path] = None,
        device: str = "cpu",
) -> List[Dict]:
    """Generate area variations from the trained LogisticNormalNet.

    Output schema is identical to ``mock_area_variations`` so callers
    can swap between the two transparently::

        {
          "variation_idx":      int,
          "rooms": [{"room_id", "room_type", "area", "radius",
                     "pos_natural": (x, y)}],
          "total_area":         float,
          "area_share_by_type": {type: fraction},
          "counts_by_type":     {type: int},
        }

    Raises ``ModelNotAvailable`` if torch isn't installed or no
    checkpoint can be found.
    """
    model, vocab, dev = load_model(checkpoint_path, device=device)

    # Build counts vector in vocab order
    counts_dict = dict(inputs.room_counts)
    counts_vec  = np.array([float(counts_dict.get(t, 0)) for t in vocab],
                            dtype=np.float32)
    if counts_vec.sum() <= 0:
        return []

    # Seed RNGs for reproducibility
    if seed is not None:
        np.random.seed(int(seed))
        torch.manual_seed(int(seed))
        random.seed(int(seed))

    # Forward pass — log1p(counts) is the input feature
    x   = np.log1p(counts_vec)[None, :]
    x_t = torch.from_numpy(x).to(dev)
    with torch.no_grad():
        _, _, mu_t, log_sigma_t = model(x_t)
    mu        = mu_t.detach().cpu().numpy()[0]
    log_sigma = log_sigma_t.detach().cpu().numpy()[0]
    sigma     = np.exp(np.clip(log_sigma, -5.0, 1.0))

    # Sample pool
    samples: List[np.ndarray] = []
    for _ in range(int(n_samples)):
        eps    = np.random.randn(len(mu))
        logits = mu + (sigma * diversity) * eps
        samples.append(_softmax_np(logits))
    S = np.vstack(samples)

    # Pick K representatives
    k = int(min(n_variations, S.shape[0]))
    if method.lower() == "kmeans":
        rep_idx = _kmeans_representatives(
            S, k=k, seed=int(seed) if seed is not None else 42, iters=30)
    else:
        rep_idx = _farthest_point_indices(
            S, k=k, seed=int(seed) if seed is not None else 42)
    reps = S[rep_idx]

    # Expand to per-room areas, pack, build dict
    total_area = float(inputs.plan_geometry.total_area)
    variations: List[Dict] = []
    for var_idx in range(reps.shape[0]):
        share_vec = reps[var_idx]
        share_by_type = {vocab[i]: float(share_vec[i])
                         for i in range(len(vocab))}

        rooms: List[Dict] = []
        for t in vocab:
            c = int(counts_dict.get(t, 0))
            if c <= 0:
                continue
            share = share_by_type.get(t, 0.0)
            area_per_room = (share * total_area) / c if c > 0 else 0.0
            radius = (math.sqrt(area_per_room / math.pi)
                       if area_per_room > 0 else 2.0)
            for i in range(c):
                rooms.append({
                    "room_id":   f"{t}_{i}",
                    "room_type": t,
                    "area":      area_per_room,
                    "radius":    radius,
                })

        if not rooms:
            continue

        # Real circle packing (lifted from the existing app via area_mock)
        pack_circles_natural(rooms, seed=42 + var_idx)

        # Recompute the per-type share from the actual room areas
        share_out: Dict[str, float] = {}
        for r in rooms:
            share_out[r["room_type"]] = (
                share_out.get(r["room_type"], 0.0) + r["area"])
        if total_area > 0:
            share_out = {k: v / total_area for k, v in share_out.items()}

        variations.append({
            "variation_idx":      var_idx,
            "rooms":              rooms,
            "total_area":         total_area,
            "units":              str(getattr(inputs.plan_geometry, "units", "m")),
            "area_share_by_type": share_out,
            "counts_by_type":     dict(counts_dict),
        })

    return variations
