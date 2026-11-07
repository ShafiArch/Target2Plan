"""
models/connectivity_model.py
============================
Real-model wrapper for Stage 3 (Connectivity).

Architecture (informed by user feedback after first draft):

  1. The connectivity model is called **once per regenerate**, on the
     first selected zone table. The same edges are then applied to
     every selected table — only the node positions change between
     cards. This matches the upstream Connectivity tool's behaviour:
     the AR model emits one canonical graph from the room counts; the
     visualization layer is responsible for placing the nodes.

  2. ``outside`` is treated as a single regular node, not split into
     four cardinal-direction anchors. Rooms connect to ``outside_0``
     via the model's predicted edges; the 3×3 zone grid + compass
     labels on the canvas are *decorative background*, not nodes.

  3. Primary rooms (living, bedroom, kitchen) are pinned at their
     zone-cell centroid as predicted by Stage 2. Secondary rooms
     (bathroom, balcony, storage, dining, …) plus ``outside_0`` are
     spring-layout-relaxed against the pinned primaries, so they
     migrate to sit adjacent to whichever primary they share an edge
     with. The user's intent: "secondary rooms should move and come
     close to the connected primary rooms so they are accessible
     from the primary room."

Output schema (matches ``connectivity_mock.mock_connectivity_graph``):

    {
      "meta":          {"plan_index": int},
      "canvas_size":   int,
      "plan_width":    float,   # used by the canvas to draw the
      "plan_height":   float,   # 3×3 grid at the right aspect ratio
      "plan_rect":     [x, y, w, h],
      "nodes":         [{id, type, is_direction, is_primary,
                          x, y, x_start, y_start}, ...],
      "edges_ordered": [{source, target}, ...],
      "exposure_edges": [],     # always empty in this design; kept
                                # for back-compat with the canvas widget
    }
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    import networkx as nx
    _NX_OK = True
except ImportError:                                     # pragma: no cover
    _NX_OK = False
    nx = None  # type: ignore[assignment]

try:
    import torch
    import torch.nn as nn
    _TORCH_OK = True
except ImportError:                                     # pragma: no cover
    _TORCH_OK = False
    torch = None  # type: ignore[assignment]
    nn = None     # type: ignore[assignment]

from models.connectivity_mock import (
    PRIMARY_TYPES, STAGE3_SIZE,
    arrange_rooms_in_cell, zone_cell_box,
)


# ═══════════════════════════════════════════════════════════════
#  CONSTANTS (mirror Connectivity_generation_code_pyside_final.py)
# ═══════════════════════════════════════════════════════════════

PREFERRED_ORDER = [
    "living", "kitchen", "bedroom", "bathroom", "stair",
    "balcony", "veranda", "garden", "storage", "parking", "pool",
    "dining", "study", "office", "laundry",
]


# ── Canonical room-type set ──────────────────────────────────────
# Matches what Connectivity_train_code.py:build_type_vocab() would
# produce on a typical residential floor-plan dataset: sorted
# alphabetically. The checkpoint itself does NOT store the vocab
# (the training script only saves "model_state" + "opt_state"), so
# the upstream tool re-derives it by scanning the dataset folder.
# We can't do that here — instead we hard-code the most common
# residential vocab below, and let the user override via
# LAYOUTGEN_CONN_VOCAB if their training set had different types.
#
# The 7-type set is overwhelmingly the common case for residential
# datasets. Other entries are added in alphabetical order to fill
# higher n_types.
_CORE_RESIDENTIAL_TYPES = (
    "balcony", "bathroom", "bedroom", "kitchen", "living",
    "outside", "storage",
)
_EXTRA_RESIDENTIAL_TYPES = (
    "dining", "garden", "laundry", "office", "parking",
    "pool", "stair", "study", "veranda",
)

# ── Verified vocabularies of the shipped checkpoints ─────────────
# The trainer (Train_set_05_graph/train.py:build_type_vocab) sorts the
# set of node types that actually occur in dataset_graph/. For the
# checkpoint shipped as assets/checkpoints/connectivity_model.pt
# (Train_set_05_graph/outputs_mr, 2,001 graphs, n_types = 11) that
# set was verified against the training folder on 2026-10-27: the ten
# ResPlan room types that survive extraction (no "pool") plus
# "outside". The alphabetical *guess* below gives a different list for
# n_types = 11 (dining/laundry/office instead of parking/stair/veranda),
# which scrambles the indices of kitchen, living, outside and storage —
# so a known vocabulary is always preferred over the guess.
_KNOWN_VOCABS: Dict[int, Tuple[str, ...]] = {
    11: ("balcony", "bathroom", "bedroom", "garden", "kitchen", "living",
         "outside", "parking", "stair", "storage", "veranda"),
}


def _guess_alphabetical_vocab(n_types: int) -> List[str]:
    """Best-guess alphabetical vocab to feed the model.

    Mirrors the trainer's behaviour: take the set of room types in the
    training data, sort alphabetically, map to indices 0..n-1.
    Without the actual dataset on disk, we approximate by starting
    from the 7 core residential types and adding the next-most-common
    types alphabetically until we hit ``n_types``.
    """
    core = list(_CORE_RESIDENTIAL_TYPES)
    if n_types <= len(core):
        # Reduce — keep the alphabetically-first n_types
        return sorted(core)[:n_types]
    extras_needed = n_types - len(core)
    combined = sorted(set(core) | set(_EXTRA_RESIDENTIAL_TYPES[:extras_needed]))
    return combined


class ModelNotAvailable(RuntimeError):
    """Raised when the real model can't be used. Stage 3 falls back to mock."""


# ═══════════════════════════════════════════════════════════════
#  MODEL (torch-guarded)
# ═══════════════════════════════════════════════════════════════

if _TORCH_OK:

    class EdgeARModel(nn.Module):
        """Direct port of Connectivity_train_code.py.

        Given the embeddings of two endpoint types and the previous
        edge bit (0 or 1), predicts the probability that the current
        lower-triangular position has an edge.
        """

        def __init__(self, n_types: int, type_emb_dim: int,
                     edge_emb_dim: int, hidden_dim: int) -> None:
            super().__init__()
            self.type_emb       = nn.Embedding(n_types, type_emb_dim)
            self.prev_edge_emb  = nn.Embedding(2, edge_emb_dim)
            in_dim = type_emb_dim * 2 + edge_emb_dim
            self.gru  = nn.GRU(input_size=in_dim, hidden_size=hidden_dim,
                                batch_first=True)
            self.head = nn.Linear(hidden_dim, 1)

        def step(self, type_i_idx: int, type_j_idx: int,
                 prev_edge_bit: int, h, device):
            ti = torch.tensor([[type_i_idx]],   device=device, dtype=torch.long)
            tj = torch.tensor([[type_j_idx]],   device=device, dtype=torch.long)
            pe = torch.tensor([[prev_edge_bit]], device=device, dtype=torch.long)
            ei = self.type_emb(ti)
            ej = self.type_emb(tj)
            ep = self.prev_edge_emb(pe)
            x  = torch.cat([ei, ej, ep], dim=-1)
            out, h = self.gru(x, h)
            logit = self.head(out).squeeze()
            prob  = torch.sigmoid(logit).item()
            return prob, h


# ═══════════════════════════════════════════════════════════════
#  CHECKPOINT DISCOVERY + LOADING
# ═══════════════════════════════════════════════════════════════

def find_checkpoint() -> Optional[Path]:
    env = os.environ.get("LAYOUTGEN_CONN_CKPT")
    if env:
        p = Path(env)
        if p.is_file():
            return p

    here = Path(__file__).resolve().parent.parent
    candidates_dir = here / "assets" / "checkpoints"
    if not candidates_dir.is_dir():
        return None

    for name in ("connectivity_model.pt", "connectivity.pt", "conn_model.pt"):
        p = candidates_dir / name
        if p.is_file():
            return p

    for p in sorted(candidates_dir.glob("*.pt")):
        if "conn" in p.name.lower():
            return p
    return None


def _infer_model_dims(ckpt: dict) -> Tuple[int, int, int, int]:
    sd = ckpt.get("model_state") or ckpt.get("state_dict") or ckpt
    try:
        n_types, type_emb_dim = sd["type_emb.weight"].shape
        _,        edge_emb_dim = sd["prev_edge_emb.weight"].shape
        hidden_dim = sd["gru.weight_ih_l0"].shape[0] // 3
    except (KeyError, IndexError, AttributeError) as e:
        raise ModelNotAvailable(
            f"Checkpoint state-dict doesn't look like an EdgeARModel: {e}"
        ) from e
    return int(n_types), int(type_emb_dim), int(edge_emb_dim), int(hidden_dim)


def _load_vocab(ckpt: dict, n_types: int) -> Dict[str, int]:
    """Recover the type→index vocab the model was trained against.

    The trainer (Connectivity_train_code.py:build_type_vocab) takes
    the alphabetical sort of every ``node_types`` value seen in the
    training-data JSONs. It does NOT save this dict in the checkpoint
    — the upstream PySide tool re-derives it by scanning the dataset
    folder at load time. We don't have that folder, so we fall back
    through three sources in order:

      1. ``type_vocab`` / ``vocab`` keys in the checkpoint, in case
         the user (or a future trainer) added them
      2. ``LAYOUTGEN_CONN_VOCAB`` env var — comma-separated list of
         types in the same order the trainer would have used
         (alphabetical)
      3. The verified vocabulary table ``_KNOWN_VOCABS`` (keyed by
         n_types) for the checkpoints shipped with the app
      4. The hard-coded ``_guess_alphabetical_vocab`` default, which
         matches what the trainer would have produced on a standard
         residential dataset (last resort — may be wrong)
    """
    # 1) Checkpoint-embedded vocab
    for key in ("type_vocab", "vocab", "node_types"):
        v = ckpt.get(key)
        if isinstance(v, dict) and len(v) == n_types:
            print(f"[connectivity] vocab from ckpt['{key}']: "
                  f"{sorted(v, key=lambda k: int(v[k]))}")
            return {str(k): int(idx) for k, idx in v.items()}
        if isinstance(v, list) and len(v) == n_types:
            print(f"[connectivity] vocab list from ckpt['{key}']: {list(v)}")
            return {str(t): i for i, t in enumerate(v)}

    # 2) Env-var override (user pins to their actual training types)
    env_vocab = os.environ.get("LAYOUTGEN_CONN_VOCAB", "").strip()
    if env_vocab:
        types = [t.strip().lower() for t in env_vocab.split(",")
                  if t.strip()]
        if len(types) != n_types:
            raise ModelNotAvailable(
                f"LAYOUTGEN_CONN_VOCAB has {len(types)} types but the "
                f"checkpoint expects n_types={n_types}. Adjust the "
                f"env var to match exactly.")
        print(f"[connectivity] vocab from LAYOUTGEN_CONN_VOCAB: {types}")
        return {t: i for i, t in enumerate(types)}

    # 3) Verified vocabulary of a shipped checkpoint (see _KNOWN_VOCABS)
    known = _KNOWN_VOCABS.get(int(n_types))
    if known is not None:
        print(f"[connectivity] vocab from verified checkpoint table "
              f"(n_types={n_types}): {list(known)}")
        return {t: i for i, t in enumerate(known)}

    # 4) Hard-coded residential default — alphabetical, matches what
    #    the trainer would have produced on a typical residential
    #    floor-plan dataset.
    guess = _guess_alphabetical_vocab(n_types)
    if len(guess) != n_types:
        raise ModelNotAvailable(
            f"Checkpoint has {n_types} types and no embedded vocab. "
            f"Set LAYOUTGEN_CONN_VOCAB to the comma-separated list "
            f"used at training time (must match alphabetical order).")
    if "outside" not in guess:
        # Defensive: every trained checkpoint had outside in the vocab.
        # If we somehow ended up without it, force-include it.
        guess = sorted(set(guess[:n_types - 1]) | {"outside"})
    print(f"[connectivity] vocab from alphabetical default: {guess}\n"
          f"  → If edge predictions look wrong, set\n"
          f"     LAYOUTGEN_CONN_VOCAB='<your,training,types,alphabetical>'")
    return {t: i for i, t in enumerate(guess)}


_CACHE: Dict[str, tuple] = {}


def _get_cached(path: Path):
    key = str(path)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    loaded = _load(path)
    _CACHE[key] = loaded
    return loaded


def _load(path: Path):
    if not _TORCH_OK:
        raise ModelNotAvailable("PyTorch is not installed.")
    try:
        ckpt = torch.load(str(path), map_location="cpu", weights_only=False)
    except Exception as e:
        raise ModelNotAvailable(f"Failed to torch.load checkpoint: {e}") from e

    n_types, type_emb_dim, edge_emb_dim, hidden_dim = _infer_model_dims(ckpt)
    vocab = _load_vocab(ckpt, n_types)
    if "outside" not in vocab:
        raise ModelNotAvailable(
            "Checkpoint vocab is missing 'outside'. The training set "
            "must contain an outside node in every graph.")

    model = EdgeARModel(n_types, type_emb_dim, edge_emb_dim, hidden_dim)
    sd = ckpt.get("model_state") or ckpt.get("state_dict") or ckpt
    try:
        model.load_state_dict(sd, strict=True)
    except Exception as e:
        raise ModelNotAvailable(f"load_state_dict failed: {e}") from e
    model.eval()

    device = torch.device("cpu")
    return model, vocab, device


# ═══════════════════════════════════════════════════════════════
#  NODE LIST + AR INFERENCE
# ═══════════════════════════════════════════════════════════════

def _make_nodes(type_vocab: Dict[str, int],
                room_types_in_table: List[str]
                ) -> Tuple[List[str], List[str]]:
    counts: Dict[str, int] = Counter(rt.lower() for rt in room_types_in_table)
    types_in_vocab = set(type_vocab.keys())

    node_ids: List[str]   = []
    node_types: List[str] = []
    used: set = set()

    for t in PREFERRED_ORDER:
        if t in types_in_vocab:
            for i in range(int(counts.get(t, 0))):
                node_ids.append(f"{t}_{i}")
                node_types.append(t)
            used.add(t)

    for t in sorted(types_in_vocab):
        if t in used or t == "outside":
            continue
        for i in range(int(counts.get(t, 0))):
            node_ids.append(f"{t}_{i}")
            node_types.append(t)

    # outside_0 is always the last node — matches the training graphs.
    node_ids.append("outside_0")
    node_types.append("outside")
    return node_ids, node_types


def _lower_tri_edge_order(n: int) -> List[Tuple[int, int]]:
    return [(i, j) for i in range(1, n) for j in range(0, i)]


def _run_ar(model, type_vocab: Dict[str, int],
            node_types: List[str], threshold: float, device,
            sample: bool = True, seed: int = 42
            ) -> List[Tuple[int, int]]:
    """Autoregressively decode the lower-triangular edge bits.

    The upstream Connectivity tool defaults to **probabilistic
    sampling** (Bernoulli on each prob), not threshold cutoff —
    ``cb_sample.setChecked(True)`` at line 947 of
    Connectivity_generation_code_pyside_final.py. Threshold mode tends
    to drop borderline edges (prob ≈ 0.3–0.5), which can leave a
    primary room isolated when none of its potential connections cross
    0.5. Sampling lets those borderline probs land as edges with their
    natural frequency, so the graph stays connected the way the
    training data was.

    We use a fixed seed so the output stays deterministic across
    regenerate calls — same inputs always yield the same edges, but
    the edge SET matches the distribution the model learned.
    """
    import random as _random
    rng = _random.Random(int(seed))

    type_idx = [type_vocab.get(t, type_vocab.get("outside"))
                for t in node_types]
    n = len(node_types)
    pairs = _lower_tri_edge_order(n)

    edges: List[Tuple[int, int]] = []
    prev_bit = 0
    h = None
    with torch.no_grad():
        for (i, j) in pairs:
            ti, tj = int(type_idx[i]), int(type_idx[j])
            prob, h = model.step(ti, tj, prev_bit, h, device)
            if sample:
                bit = 1 if rng.random() < prob else 0
            else:
                bit = 1 if prob >= threshold else 0
            if bit == 1:
                edges.append((i, j))
            prev_bit = bit
    return edges


# ═══════════════════════════════════════════════════════════════
#  POSITIONING — primaries pinned, secondaries spring-relaxed
# ═══════════════════════════════════════════════════════════════

def _build_graph_dict(table: dict,
                       node_ids: List[str],
                       node_types: List[str],
                       edges_ij: List[Tuple[int, int]],
                       canvas_size: int,
                       plan_width: float,
                       plan_height: float) -> dict:
    """Apply the canonical edges to one zone table — positions only.

    Primaries are pinned at their zone-cell centroid as predicted by
    Stage 2. Secondaries + ``outside_0`` are spring-layout-relaxed
    against the pinned primaries so they migrate adjacent to whichever
    primary they share an edge with.
    """
    plan_index = int(table.get("table_index", 0))
    rooms      = table.get("rooms", []) or []
    rooms_by_id: Dict[str, dict] = {r["room_id"]: r for r in rooms}

    # ── Letterboxed plan rect inside the canvas (respects aspect ratio)
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

    # ── 1. Primary positions: zone centroid (sub-grid for stacking) ──
    rooms_by_zone: Dict[int, List[dict]] = {}
    for r in rooms:
        if r.get("zone_assignment") is None:        # not zoned (balcony): spring layout
            continue
        rooms_by_zone.setdefault(int(r["zone_assignment"]), []).append(r)

    centre_x = rect_x + rect_w / 2.0
    centre_y = rect_y + rect_h / 2.0

    final_pos: Dict[str, Tuple[float, float]] = {}
    for zone, zone_rooms in rooms_by_zone.items():
        cx, cy, cw, ch = zone_cell_box(zone, rect_w, rect_h)
        cx += rect_x; cy += rect_y          # translate to letterboxed rect
        positions = arrange_rooms_in_cell(zone_rooms, cx, cy, cw, ch)
        for r, pos in zip(zone_rooms, positions):
            if r["room_type"] in PRIMARY_TYPES:
                final_pos[r["room_id"]] = (pos["cx"], pos["cy"])

    # ── 2. Spring-layout for non-primaries + outside_0 ──
    if _NX_OK:
        G = nx.Graph()
        for nid in node_ids:
            G.add_node(nid)
        for (i, j) in edges_ij:
            G.add_edge(node_ids[i], node_ids[j])

        initial: Dict[str, Tuple[float, float]] = {}
        fixed:   List[str] = []
        for nid in node_ids:
            if nid in final_pos:
                initial[nid] = final_pos[nid]
                fixed.append(nid)
            else:
                # Initial guess: canvas centre + tiny deterministic offset
                # so spring_layout doesn't see a degenerate stack of points.
                jitter_x = ((hash(nid)        & 0xFF) - 128) * 0.05
                jitter_y = ((hash(nid) >> 8   & 0xFF) - 128) * 0.05
                initial[nid] = (centre_x + jitter_x, centre_y + jitter_y)

        try:
            relaxed = nx.spring_layout(
                G, pos=initial, fixed=fixed,
                iterations=40, seed=42,
                k=min(rect_w, rect_h) * 0.18,
                scale=None,
            )
            margin = inset
            for nid, (rx, ry) in relaxed.items():
                if nid in final_pos:
                    continue
                final_pos[nid] = (
                    max(margin, min(float(rx), canvas_size - margin)),
                    max(margin, min(float(ry), canvas_size - margin)),
                )
        except Exception:
            pass

    for nid in node_ids:
        if nid not in final_pos:
            final_pos[nid] = (centre_x, centre_y)

    # ── 3. Visible nodes (rooms + outside_0, no direction anchors) ──
    nodes: List[dict] = []
    for nid, nt in zip(node_ids, node_types):
        is_primary = nt in PRIMARY_TYPES
        fx, fy     = final_pos[nid]
        if is_primary:
            sx, sy = fx, fy
        else:
            sx, sy = centre_x, centre_y
        nodes.append({
            "id":           nid,
            "type":         nt,
            "is_direction": False,
            "is_primary":   is_primary,
            "x":            fx,
            "y":            fy,
            "x_start":      sx,
            "y_start":      sy,
        })

    # ── 4. Edges — single style, all in edges_ordered ──
    edges_ordered = [
        {"source": node_ids[i], "target": node_ids[j]}
        for (i, j) in edges_ij
    ]

    return {
        "meta":           {"plan_index": plan_index},
        "canvas_size":    canvas_size,
        "plan_width":     float(plan_width),
        "plan_height":    float(plan_height),
        "plan_rect":      [rect_x, rect_y, rect_w, rect_h],
        "nodes":          nodes,
        "edges_ordered":  edges_ordered,
        "exposure_edges": [],
    }


# ═══════════════════════════════════════════════════════════════
#  PUBLIC ENTRY POINT
# ═══════════════════════════════════════════════════════════════

def generate_connectivity_graphs(
        zone_tables: List[dict],
        selected_idxs: List[int],
        threshold: float = 0.5,
        canvas_size: int = STAGE3_SIZE,
        plan_width: float  = 1500.0,
        plan_height: float = 2000.0,
        sample: bool = True,
        seed: int = 42,
        checkpoint_path: Optional[Path] = None,
) -> List[dict]:
    """Run the EdgeARModel **once**, then apply the resulting edge set
    to every selected zone table — producing N graphs with identical
    topology but per-table positioning.

    ``sample=True`` (default) matches the upstream tool's behaviour:
    each edge is a Bernoulli draw on the model's predicted probability,
    so borderline probs (0.2–0.5) make it in with their natural
    frequency. Set ``sample=False`` for hard ``prob >= threshold``
    decoding.

    With ``sample=True`` and a fixed ``seed``, output is fully
    deterministic across calls — same input → same edges — while
    still drawing from the model's true distribution.

    Raises ``ModelNotAvailable`` if torch / the checkpoint / vocab is
    unavailable. Stage 3 catches this and falls back to mock.
    """
    if not _TORCH_OK:
        raise ModelNotAvailable("PyTorch is not installed.")
    if not zone_tables or not selected_idxs:
        return []

    if checkpoint_path is None:
        checkpoint_path = find_checkpoint()
    if checkpoint_path is None or not checkpoint_path.is_file():
        raise ModelNotAvailable(
            "No connectivity checkpoint on disk. Drop a .pt file into "
            "assets/checkpoints/connectivity_model.pt (or set "
            "LAYOUTGEN_CONN_CKPT).")

    model, type_vocab, device = _get_cached(checkpoint_path)

    by_idx = {int(t["table_index"]): t for t in zone_tables}

    # ── Generate edges ONCE, using the first selected table's room list ──
    first = next((by_idx.get(int(i)) for i in selected_idxs
                  if int(i) in by_idx), None)
    if first is None:
        return []
    first_rooms = first.get("rooms", []) or []
    if not first_rooms:
        return []
    room_types = [str(r.get("room_type", "other")).lower()
                   for r in first_rooms]
    node_ids, node_types = _make_nodes(type_vocab, room_types)
    edges_ij = _run_ar(model, type_vocab, node_types,
                        threshold=float(threshold), device=device,
                        sample=bool(sample), seed=int(seed))

    # ── Apply to every selected table ──
    out: List[dict] = []
    for idx in selected_idxs:
        t = by_idx.get(int(idx))
        if t is None or not (t.get("rooms") or []):
            continue
        out.append(_build_graph_dict(
            t, node_ids, node_types, edges_ij, canvas_size,
            plan_width, plan_height))
    return out
