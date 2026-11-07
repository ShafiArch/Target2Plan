"""Run the desktop pipeline headlessly and dump every stage for the web parity check.

usage: python tools/parity/dump_pipeline.py <repo_root> <out.json>
Needs torch (the desktop models) but not PySide6 (a stub is on the path).
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
REPO = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(HERE / "fakeqt"), str(REPO)]

import torch  # noqa: E402

from app.state import InputsState, SiteSide  # noqa: E402
from models import area_model, connectivity_model, zone_model  # noqa: E402
from models.schematic_mock import mock_schematics, pareto_front_indices  # noqa: E402
from models.schematic_layout import synthesize  # noqa: E402

torch.set_num_threads(1)


def reference_box(schematic, size=600.0):
    """Same as app/widgets/schematic_canvas.reference_box (that module needs Qt)."""
    conn = schematic.get("source_connectivity") or {}
    pw, ph = float(conn.get("plan_width", 0) or 0), float(conn.get("plan_height", 0) or 0)
    if pw > 0 and ph > 0:
        a = pw / ph
        return (size, size / a) if a >= 1 else (size * a, size)
    return size, size


def inputs_dict(inp):
    sc = inp.site_conditions
    return {
        "room_counts": dict(inp.room_counts),
        "plan_geometry": {"width": inp.plan_geometry.width, "height": inp.plan_geometry.height,
                          "units": inp.plan_geometry.units, "px_per_m": inp.plan_geometry.px_per_m},
        "site_conditions": {d: {"view": getattr(sc, d).view, "spl_dB": getattr(sc, d).spl_dB} for d in "NSEW"},
        "aspect_ratios": dict(inp.aspect_ratios), "wwr": dict(inp.wwr),
        "prefs": {"target_qvci": inp.prefs.target_qvci, "target_aqi": inp.prefs.target_aqi,
                  "targets_enabled": inp.prefs.targets_enabled, "capacity_override": inp.prefs.capacity_override,
                  "interior_bathrooms_only": inp.prefs.interior_bathrooms_only},
    }


def clean(o):
    """JSON-safe copy (tuples → lists, drops private keys)."""
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items() if not str(k).startswith("_")}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    return o


cases = []
configs = [
    ("default", {}, None),
    ("big", {"room_counts": {"living": 1, "bedroom": 3, "kitchen": 1, "bathroom": 2, "balcony": 2}}, None),
    ("ft-site", {"room_counts": {"living": 1, "bedroom": 2, "kitchen": 1, "bathroom": 2, "balcony": 1},
                 "plan": (40.0, 30.0, "ft"),
                 "site": {"N": ("Urban", 72.0), "S": ("Vegetation", 45.0), "E": ("Sky", 58.0), "W": ("No View", 66.0)},
                 "targets": (0.55, 0.75, True)}, None),
    ("drawn", {}, [("living", 22.0, 5.5, 4.0), ("bedroom", 14.0, 4.0, 3.5), ("bedroom", 14.0, 4.0, 3.5),
                   ("kitchen", 9.0, 3.0, 3.0), ("bathroom", 5.0, 2.0, 2.5), ("balcony", 4.5, 1.5, 3.0)]),
    ("untargeted", {"room_counts": {"living": 1, "bedroom": 1, "kitchen": 1, "bathroom": 1, "balcony": 1},
                    "targets": (0.7, 0.6, False)}, None),
]
for ci, (name, cfg, drawn) in enumerate(configs):
    inp = InputsState()
    if "room_counts" in cfg:
        inp.room_counts = dict(cfg["room_counts"])
    if "plan" in cfg:
        inp.plan_geometry.width, inp.plan_geometry.height, inp.plan_geometry.units = cfg["plan"]
    if "site" in cfg:
        for d, (v, l) in cfg["site"].items():
            setattr(inp.site_conditions, d, SiteSide(v, l))
    if "targets" in cfg:
        inp.prefs.target_qvci, inp.prefs.target_aqi, inp.prefs.targets_enabled = cfg["targets"]
    if drawn:
        counts = {}
        rooms = []
        for t, a, w, h in drawn:
            k = counts.get(t, 0); counts[t] = k + 1
            rooms.append({"room_id": f"{t}_{k}", "room_type": t, "area": a, "w_m": w, "h_m": h,
                          "aspect_ratio": h / w, "radius": (a / 3.14159) ** 0.5})
        inp.room_counts = counts
        var = {"variation_idx": 0, "rooms": rooms, "total_area": inp.plan_geometry.total_area, "units": "m"}
    else:
        var = area_model.generate_area_variations(inp, seed=42)[ci % 4]
    tables, pool = zone_model.generate_zone_tables(inp, var, n_samples=80, temperature=0.8, seed=7 + ci)
    tables = tables[:12]
    sel = [t["table_index"] for t in tables]
    pg = inp.plan_geometry
    graphs = connectivity_model.generate_connectivity_graphs(tables, sel, plan_width=pg.width, plan_height=pg.height,
                                                             room_counts=dict(inp.room_counts))
    sch = mock_schematics(tables, graphs, [g["meta"]["plan_index"] for g in graphs], inp)
    syn_out = []
    for s in sch:
        bw, bh = reference_box(s)
        syn = synthesize(s, "C", bw, bh)
        wall_idx = {id(w): i for i, w in enumerate(syn.walls)}
        syn_out.append({
            "metre": syn.metre, "box": list(syn.box), "removed": syn.removed,
            "n_frames": len(syn.frames), "phases": [list(p) for p in syn.phases],
            "rects": [[r["room_id"], r["cx"], r["cy"], r["w"], r["h"]] for r in syn.rects],
            "walls": [[w["x1"], w["y1"], w["x2"], w["y2"], w["kind"], list(w["rooms"])] for w in syn.walls],
            "openings": [[o["kind"], o["x1"], o["y1"], o["x2"], o["y2"], wall_idx.get(id(o["wall"]))] for o in syn.openings],
            "furniture": [[f["room_id"], f["name"], f["side"], list(f["box"])] for f in syn.furniture],
            "frame_labels": [f["label"] for f in syn.frames],
            "frame_mid": [[s_["room_id"], s_["cx"], s_["cy"]] for s_ in syn.frames[len(syn.frames) // 3]["shapes"]],
        })
    cases.append({
        "name": name, "inputs": inputs_dict(inp), "variation": clean(var),
        "tables": clean(tables), "graphs": clean(graphs), "schematic_rooms": [clean(s["rooms"]) for s in sch],
        "synthesis": syn_out, "pareto": pareto_front_indices(sch),
    })

Path(sys.argv[2]).write_text(json.dumps({"cases": cases}))
print("wrote", sys.argv[2], f"{Path(sys.argv[2]).stat().st_size / 1e6:.1f} MB")
