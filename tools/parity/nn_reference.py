"""Ground truth from the prototype's own code + PyTorch.

Runs the real checkpoints through the prototype's model wrappers and
writes every input and output needed to check the JavaScript port.

usage: python tools/parity/nn_reference.py <repo_root> <out.json>
"""
import copy
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).parent
REPO = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(HERE / "fakeqt"), str(REPO)]

import numpy as np  # noqa: E402
import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402

from app.state import InputsState, SiteSide  # noqa: E402
from models import area_model, connectivity_model, zone_model  # noqa: E402
from models import zone_scoring as zs  # noqa: E402

torch.set_num_threads(1)
out = {"torch": torch.__version__}


def arr(a):
    return np.asarray(a, dtype=float).round(10).tolist()


# ─────────────────────────────── Stage 1 ────────────────────────────────
amodel, avocab, _ = area_model.load_model()
area_cases = []
for counts in ({"living": 1, "bedroom": 2, "kitchen": 1, "bathroom": 1, "balcony": 1},
               {"living": 1, "bedroom": 3, "kitchen": 1, "bathroom": 2, "balcony": 2},
               {"living": 1, "bedroom": 1, "kitchen": 1, "bathroom": 1, "balcony": 0},
               {"living": 2, "bedroom": 5, "kitchen": 1, "bathroom": 3, "balcony": 1, "storage": 1}):
    inp = InputsState()
    inp.room_counts = dict(counts)
    x = torch.from_numpy(np.log1p(np.array([float(counts.get(t, 0)) for t in avocab], dtype=np.float32))[None])
    with torch.no_grad():
        _, _, mu, ls = amodel(x)
    vars_ = area_model.generate_area_variations(inp, seed=42)
    area_cases.append({
        "counts": counts, "total_area": inp.plan_geometry.total_area,
        "mu": arr(mu[0]), "log_sigma": arr(ls[0]),
        "variations": [{"rooms": [{"room_id": r["room_id"], "area": r["area"], "radius": r["radius"],
                                   "pos": list(r["pos_natural"])} for r in v["rooms"]]} for v in vars_],
    })
out["area"] = {"vocab": avocab, "cases": area_cases}

# ─────────────────────────────── Stage 2 ────────────────────────────────
zmodel, _ = zone_model._get_cached(zone_model.find_checkpoint())
VIEWS = list(zs.VIEW_WEIGHTS)
rng = random.Random(7)


def forced_logits(model, feats, n, zv, zn, tgt, pres, cap, allowed, forced):
    """generate() with the choices given (teacher forcing); returns masked logits per step."""
    B = forced.shape[0]
    with torch.no_grad():
        emb = model.encode(feats, zv, zn, torch.ones(B, n))
        counts = torch.zeros(B, 9); prev = torch.zeros(B, 1, 9); hidden = None
        tgt3 = torch.cat([tgt, pres.unsqueeze(1)], 1)
        nt = torch.full((B,), n, dtype=torch.long)
        rec = []
        for t in range(n):
            rooms_rem = ((nt.float() - t) / nt.float().clamp(min=1)).clamp(0, 1).unsqueeze(1)
            ctx = torch.cat([tgt3, rooms_rem, counts / nt.unsqueeze(1).float().clamp(min=1), zv, zn], 1).unsqueeze(1)
            o, hidden = model.gru(torch.cat([emb[:, t:t + 1], ctx, prev], -1), hidden)
            logits = model.head(o.squeeze(1))
            al = (counts < cap) & allowed[:, t]
            last = (t == nt - 1).unsqueeze(1) & (nt.unsqueeze(1) > 1)
            al = al & ~(last & (counts + 1 == nt.unsqueeze(1).float()))
            none_ok = ~al.any(1)
            if none_ok.any():
                relaxed = counts < cap
                al[none_ok] = relaxed[none_ok] | ~relaxed[none_ok].any(1, keepdim=True)
            logits = logits.masked_fill(~al, -1e9)
            rec.append(logits.numpy().astype(float).round(8).tolist())
            z = forced[:, t]
            oh = F.one_hot(z, 9).float()
            counts = counts + oh; prev = oh.unsqueeze(1)
    return emb[0].numpy().astype(float).round(8).tolist(), rec


zone_cases = []
programs = [
    {"living": 1, "bedroom": 2, "kitchen": 1, "bathroom": 1, "balcony": 1},
    {"living": 1, "bedroom": 3, "kitchen": 1, "bathroom": 2, "balcony": 1},
    {"living": 1, "bedroom": 1, "kitchen": 1, "bathroom": 1},
    {"living": 1, "bedroom": 4, "kitchen": 1, "bathroom": 3, "balcony": 2},
    {"living": 2, "bedroom": 6, "kitchen": 2, "bathroom": 4},
]
for ci, prog in enumerate(programs):
    inp = InputsState()
    inp.room_counts = dict(prog)
    if ci > 0:   # random site and targets
        for d in "NSEW":
            setattr(inp.site_conditions, d, SiteSide(rng.choice(VIEWS), float(rng.randint(30, 80))))
        inp.prefs.target_qvci = round(rng.uniform(0.2, 0.9), 2)
        inp.prefs.target_aqi = round(rng.uniform(0.3, 0.9), 2)
        inp.prefs.targets_enabled = ci != 3
    var = area_model.generate_area_variations(inp, seed=42 + ci)[ci % 3]
    ps = zone_model.PlanSetup(inp, var)
    n = ps.n
    present = bool(inp.prefs.targets_enabled)
    tq, ta = inp.prefs.target_qvci, inp.prefs.target_aqi
    feats1 = zone_model.room_features(ps.rooms)
    zv, zn = zone_model.site_context(ps.site)
    B = 81
    feats = torch.from_numpy(feats1).unsqueeze(0).expand(B, -1, -1).contiguous()
    zv_t = torch.from_numpy(zv).unsqueeze(0).expand(B, -1).contiguous()
    zn_t = torch.from_numpy(zn).unsqueeze(0).expand(B, -1).contiguous()
    tgt = torch.tensor([[tq, ta] if present else [0.0, 0.0]] * B, dtype=torch.float32)
    pres = torch.full((B,), 1.0 if present else 0.0)
    cap = torch.tensor([ps.capv] * B, dtype=torch.float32)
    allowed = torch.from_numpy(ps.allowed).unsqueeze(0).expand(B, -1, -1).contiguous()
    mask = torch.ones(B, n); n_t = torch.full((B,), n, dtype=torch.long)
    torch.manual_seed(123)
    zg = zmodel.generate(feats[:1], mask[:1], n_t[:1], zv_t[:1], zn_t[:1], tgt[:1], pres[:1], cap[:1], allowed[:1], greedy=True)
    zsamp = zmodel.generate(feats[1:], mask[1:], n_t[1:], zv_t[1:], zn_t[1:], tgt[1:], pres[1:], cap[1:], allowed[1:],
                            greedy=False, temperature=0.8)
    allz = torch.cat([zg, zsamp], 0)
    emb0, logits = forced_logits(zmodel, feats, n, zv_t, zn_t, tgt, pres, cap, allowed, allz)
    # the prototype's own table builder for the full pool (tables sorted by distance to target)
    tables, pool = zone_model.generate_zone_tables(inp, var, n_samples=80, temperature=0.8, seed=123)
    zone_cases.append({
        "program": prog, "n": n, "present": present, "targets": [tq, ta],
        "rooms": [{k: r[k] for k in ("id", "type", "area", "w", "h", "perim", "wwr", "edr", "has_front_door")}
                  for r in ps.rooms],
        "site": ps.site, "cap": ps.capv, "allowed": ps.allowed.astype(int).tolist(),
        "feats": arr(feats1), "zv": arr(zv), "zn": arr(zn),
        "emb0": emb0, "greedy": zg[0].tolist(), "sampled": allz.tolist(), "logits": logits,
        "A": arr(ps.A), "Q": arr(ps.Q), "L": arr(ps.L), "w": arr(ps.w),
        "reach": {k: list(v) for k, v in ps.reach.items()}, "reach_free": {k: list(v) for k, v in ps.reach_free.items()},
        "tables_head": [{"zones": [r["zone_assignment"] for r in t["rooms"] if r["zoned"]],
                         "computed": t["computed"]} for t in tables[:12]],
    })
out["zone"] = zone_cases

# ─────────────────────────────── Stage 3 ────────────────────────────────
cmodel, cvocab, cdev = connectivity_model._get_cached(connectivity_model.find_checkpoint())
conn_cases = []
for prog in programs:
    types = [t for t, c in prog.items() for _ in range(c)]
    node_ids, node_types = connectivity_model._make_nodes(cvocab, types)
    edges = connectivity_model._run_ar(cmodel, cvocab, node_types, threshold=0.5, device=cdev, sample=True, seed=42)
    bits, probs = [], []
    eset = set(edges)
    h = None
    idx = [cvocab.get(t, cvocab["outside"]) for t in node_types]
    prev = 0
    for (i, j) in connectivity_model._lower_tri_edge_order(len(node_types)):
        p, h = cmodel.step(idx[i], idx[j], prev, h, cdev)
        b = 1 if (i, j) in eset else 0
        probs.append(round(p, 9)); bits.append(b); prev = b
    conn_cases.append({"program": prog, "node_ids": node_ids, "node_types": node_types, "type_idx": idx,
                       "edges": [list(e) for e in edges], "bits": bits, "probs": probs})
out["conn"] = {"vocab": cvocab, "cases": conn_cases}

# ───────────────────────── RNG streams (seeded) ─────────────────────────
r = random.Random(42)
lst = list(range(10)); r.shuffle(lst)
py = {"seed": 42, "random": [random.Random(42).random() for _ in range(1)],
      "stream": [r.random() for _ in range(5)], "shuffled": lst,
      "choice": [r.choice([3, 1, 4, 1, 5, 9, 2, 6]) for _ in range(10)],
      "uniform": [r.uniform(0, 6.283185307179586) for _ in range(3)]}
np.random.seed(42)
npr = {"randn": np.random.randn(12).tolist(), "randint": [int(np.random.RandomState(s).randint(0, 200)) for s in (0, 1, 42, 99)]}
out["rng"] = {"py": py, "np": npr}

Path(sys.argv[2]).write_text(json.dumps(out))
print("wrote", sys.argv[2], f"{Path(sys.argv[2]).stat().st_size / 1e6:.1f} MB")
