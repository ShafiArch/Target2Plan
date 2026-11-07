# Target2Plan

Target2Plan goes from a room program and two plan-level targets, QVCI (Quality View Coverage Index) and AQI (Acoustic Quality Index), to zone assignments, connectivity graphs and schematic floor plans. Three trained models drive it: an area model, the v13 cGAT-AR zone model and a connectivity model.

The repository has two front ends that share the same checkpoints in `assets/checkpoints/`:

| | where | how to run |
|---|---|---|
| Desktop app (PySide6) | `main.py`, `app/`, `models/`, `stages/` | `python main.py` (see `JOURNAL_VERSION_NOTES.md`) |
| Web app | `docs/` | **https://shafiarch.github.io/Target2Plan/** |

## Web app

The web app runs entirely in the browser. The checkpoints are exported to static files and evaluated in JavaScript inside a Web Worker, so there is no server, no install and nothing leaves the machine. GitHub Pages serves it straight from the `docs/` folder of `main`.

It follows the desktop workflow, one card per stage:

1. **Inputs**: targets with the reachable range, site conditions per facade, planned WWR, zone capacity, generation parameters.
2. **Area Distribution**: draw the room program on a grid, or sample area variations from the area model (Farthest point or KMeans selection).
3. **Zone Assignments**: v13 model decodes (one greedy plus sampled), sorted by distance to the targets, with the design explorer (parallel coordinates with brushing) and a detail view per plan.
4. **Connectivity**: access graphs from the connectivity model, with secondary rooms relocated next to the room they connect to; animated.
5. **Schematic Design**: packed plans with walls, doors, windows and furniture, the replayable synthesis, a details card for any room you click, and the Pareto view.

Exports: zone cards and plans as PNG (zipped when several), contact sheets, Pareto image and CSV, synthesis animations as GIF and MP4 (MP4 in browsers that can encode H.264 through WebCodecs, such as current Chrome and Edge; elsewhere the GIF alone), and the whole session as a `.json` file that can be loaded again. Three themes: off-white, white and dark.

### Run it locally

ES modules do not load from `file://`, so serve the folder:

```
cd docs
python3 -m http.server 8000
```

then open http://localhost:8000.

### Layout

```
docs/
  index.html, css/app.css
  js/app.js          header, stage strip, sessions, theme
  js/store.js        session state and change events
  js/runtime.js      starts the worker (falls back to the main thread)
  js/worker.js       runs the three models off the main thread
  js/engine/         ports of models/*.py: nn.js (the networks), area.js,
                     zones.js, scoring.js, relocation.js, connectivity.js,
                     schematic.js, rng.js and pymath.js (Python and numpy
                     random, rounding and summation)
  js/ui/             the five stage cards, canvases, detail views, exporters
  js/vendor/         gifenc 1.0.3 and mp4-muxer 5.2.2 (MIT, licences alongside)
  models/            exported weights: <model>.bin (float32) + <model>.json
tools/
  export_weights.py  checkpoints to docs/models (no PyTorch needed)
  ptload.py          minimal reader for PyTorch .pt files
  parity/            checks the web engine against the desktop code
```

### After retraining a model

Replace the checkpoint in `assets/checkpoints/` and export the weights again:

```
python3 tools/export_weights.py assets/checkpoints docs/models
```

Commit `docs/models/` and Pages picks it up. See `tools/README.md` for the parity checks.

### How Stage 2 uses the targets

The zone assignments are predicted by the trained v13 model, conditioned on the requested QVCI and AQI: the targets enter the decoder at every step, together with the site context and the zones already used. As in the desktop app, the model is decoded 81 times: once greedily (the most likely zone at every step, no randomness) and 80 times by drawing from the model's own predicted probabilities at temperature 0.8. The arrangements are scored with the journal equations and the 12 closest to the targets are shown.

### How close is it to the desktop app

The web engine is a step-for-step port. On the same inputs the networks agree with PyTorch to about 1e-5 on the logits, so the predicted probabilities and the greedy decode are the same as the desktop's, and Stages 2 to 4 (scoring, relocation, packing, walls, openings, furniture, Pareto front) reproduce the desktop output exactly. The 80 sampled decodes take their random draws from a JavaScript generator instead of PyTorch's. The desktop app draws a fresh set on every run as well, so in both apps the sampled cards change from run to run while coming from the same model probabilities.
