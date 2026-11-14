# tools

Helpers for the web app in `docs/`.

## export_weights.py

Converts the three checkpoints to the files the browser loads:

```
python3 tools/export_weights.py assets/checkpoints docs/models
```

For each model it writes `<name>.bin` (every tensor as float32 little-endian, concatenated) and `<name>.json` (a tensor table `{name: {offset, shape}}` plus the checkpoint metadata: vocabularies and config). It reads the `.pt` files with `ptload.py`, so it needs numpy but not PyTorch.

| checkpoint | output |
|---|---|
| `area_model.pt` | `area.bin`, `area.json` |
| `zone_model_v13.pt` | `zone.bin`, `zone.json` |
| `connectivity_model.pt` | `conn.bin`, `conn.json` |

## parity/

Checks that the JavaScript engine reproduces the original prototype's Python code. The Python side needs PyTorch and numpy (scipy and networkx as for the prototype); PySide6 is not needed because `fakeqt/` stubs the few Qt classes the prototype's state module imports. The JavaScript side needs Node 18 or newer.

1. Networks against PyTorch (area model, zone encoder and decoder, connectivity model):

   ```
   python tools/parity/nn_reference.py . reference.json
   node tools/parity/nn_parity.mjs docs/models reference.json
   ```

2. Stages 2 to 4 against the prototype's pipeline (zone tables, reachable range, relocation, connectivity graphs, schematics, synthesis, Pareto front), with and without targets:

   ```
   python tools/parity/dump_pipeline.py . pipeline.json
   node tools/parity/pipeline_parity.mjs pipeline.json
   ```

Both print a table of checks and end with `all N checks passed`. Set `PYTHONDONTWRITEBYTECODE=1` when running the Python side if you do not want `__pycache__` folders in the working tree.
