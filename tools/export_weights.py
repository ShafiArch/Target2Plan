"""Export the three Target2Plan checkpoints to browser-friendly files.

usage: python3 tools/export_weights.py assets/checkpoints docs/models

For every model:  <name>.bin  (all tensors, float32 little-endian, concatenated)
                  <name>.json (tensor table {name: {offset, shape}} + metadata)
No torch needed (uses ptload).
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import ptload  # noqa: E402

CKPT = Path(sys.argv[1])
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)


def to_py(v):
    if isinstance(v, dict):
        return {str(k): to_py(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [to_py(x) for x in v]
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, (np.floating, np.integer)):
        return v.item()
    return v


def export(src, name, meta_keys):
    ck = ptload.load(CKPT / src)
    sd = ck["model_state"]
    table, chunks, off = {}, [], 0
    for k, v in sd.items():
        a = np.ascontiguousarray(v, dtype="<f4")
        table[k] = {"offset": off, "shape": list(a.shape)}
        chunks.append(a.ravel())
        off += a.size
    blob = np.concatenate(chunks).astype("<f4")
    (OUT / f"{name}.bin").write_bytes(blob.tobytes())
    meta = {k: to_py(ck[k]) for k in meta_keys if k in ck}
    (OUT / f"{name}.json").write_text(json.dumps({"tensors": table, "meta": meta, "count": int(off)}, indent=1))
    print(f"{name}: {off:,} floats → {blob.nbytes / 1e6:.2f} MB")


export("area_model.pt", "area", ["vocab", "config", "epoch", "best_val"])
export("zone_model_v13.pt", "zone", ["format", "config", "trained"])
export("connectivity_model.pt", "conn", ["type_vocab", "vocab", "epoch", "val_bce"])
