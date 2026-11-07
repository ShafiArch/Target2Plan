# Layout Generator ARML — journal version (13 Oct 2026)

Copy of `Layout_Generator_ARML_june17` (the thesis app) with the zone-prediction stage
replaced by the journal paper's **v13 cGAT-AR model** and the journal's QVCI / AQI
equations. Stage 1 (area model), Stage 3 (connectivity model) and Stage 4 (schematic
packing, Pareto view, exports) are the thesis code; only what the zone model touches
has changed.

## Run

```
python main.py
```

Needs PySide6, numpy, torch (CPU is fine: ~0.1 s for 80 arrangements) and, optionally,
scipy (exact capacity-respecting reachable range; without it the range is the per-room
bound) and networkx (connectivity layout, as before). Checkpoints in `assets/checkpoints/`:

| file | stage | what |
|---|---|---|
| `area_model.pt` | 1 | thesis area model (unchanged) |
| `zone_model_v13.pt` | 2 | **v13 zone model** = `Train_set_34/train_v13/best_model.pt` (epoch 36 / 40, val CE 1.615, 793,609 parameters) wrapped with its config and provenance |
| `connectivity_model.pt` | 3 | thesis connectivity model (unchanged) |

The thesis checkpoint `zone_model.pt` (v8 / v11 / v12 format) is refused with a clear
message if it is dropped into this folder. Env overrides as before (`LAYOUTGEN_ZONE_CKPT`
etc.).

## What the zone stage does now

* **Inputs → rooms in metres.** Plan geometry defaults to metres (12 × 9 m); `ft`
  converts, `px` needs "pixels per metre". Each zoned room gets `w`, `h` from its area and
  the per-type aspect ratio (N–S ÷ E–W), the perimeter `2(w + h)`, the **planned WWR**
  of its type, and the largest living room holds the front door (EDR 0.15, the corpus
  median).
* **Vocabulary.** Four zoned types — living, kitchen, bedroom, bathroom — plus
  balconies, which are *not* zoned (they are a façade view category) but join the plan at
  the connectivity stage (spring layout) and appear in the schematic. Dining, storage,
  study, stair, veranda, garden and parking are gone from the inputs.
* **Site.** View category and outdoor level (dB(A)) per façade; the spin boxes are limited
  to 30–80 dB(A), the corpus range. "No View" with a low level models a façade attached to
  another building.
* **Targets.** Two sliders, QVCI and AQI — the only conditioning of the model. A checkbox
  turns the targets off (unconditioned sampling, the paper's B1 setting). The preference
  weights (w_view / w_acoustic) and the spread / concentration target of the thesis app
  are gone: room distribution is governed by the capacity rule instead.
* **Reachable range.** For the selected area variation and the current site the app
  computes the exact reachable QVCI and AQI (min–max over capacity- and type-respecting
  arrangements, Hungarian assignment on zone slots) and shows it under the target sliders
  (Stage 0), above the card grid and in every detail view (Stage 2). A target outside the
  range is flagged; a site whose reachable AQI width is < 0.05 is reported as "AQI cannot
  be changed on this site".
* **Capacity rule** (developer control, Stage 0 → ZONE CAPACITY): the trained rule by plan
  size — corner ≤ 2 (3 for ≥ 8 zoned rooms), edge ≤ 3, interior ≤ 1 (2 for ≥ 8), interior
  for bathrooms only, never every room in one zone. The override spin boxes change the
  decode mask and the reachable range; the model was not trained with other caps, so this
  is for exploration.
* **Decoding.** One greedy decode plus *Samples* (default 80) sampled decodes at the
  *Temperature* (default 0.8 — the paper's protocol), batched in one forward pass. Unique
  arrangements (on the zoned rooms) become the cards, sorted by distance to the targets
  |ΔQVCI| + |ΔAQI|; the full pool (duplicates included) feeds the Stage 4 Pareto scatter.

## Equations (models/zone_scoring.py = corpus_v13/scoring.py)

* zone view weight `v(z)` = best façade of the zone: Vegetation 1.0, Sky 0.9, Urban 0.7,
  Balcony 0.4, No View 0; interior 0.
* view score `q_i = v(z_i) · min(1, WWR_i / 0.40)`.
* indoor level `L_in,i`: façade levels of the zone, transmission from the planned WWR
  (STC 45 wall / 32 window / 25 door), room absorption `A = 0.15 (2·area + perim·2.7)`,
  façade area `length·2.7`; interior zone = mean outdoor level − 51 dB; floor 20 dB(A).
* acoustic score `a_i = clip((G_t + 15 − L_in,i) / 30, 0, 1)` with guideline G = 30 /
  35 / 40 / 45 dB(A) for bedroom / living / kitchen / bathroom.
* weights `w_i` = area share over living + kitchen + bedroom (bathrooms 0, balconies not
  zoned); **QVCI = Σ w_i q_i**, **AQI = Σ w_i a_i**.

## What the panels show

* **Stage 2 cards:** Q, A, SPL (energy-average indoor level of the zoned rooms, dB(A)),
  Z (zones used), Δ (distance to target); rooms table with zone, QV = q_i, AQ = a_i
  (accent when q ≥ 0.5 / when the indoor level is at or below the guideline); bathrooms
  dimmed (placed, no index weight); balconies "—".
* **Stage 2 detail:** QVCI / AQI bars with the target tick and the reachable band,
  distance to target, reachable range, average indoor level, zone spread, fullest zone,
  capacity, capacity-rule check, decode (greedy / sampled / random) and model (v13 /
  mock); rooms with QV, AQ and indoor level / guideline; the canvas prints v(z) × 100 and
  the zone's outdoor level.
* **Stage 4:** the thesis QV-threshold slider is gone (the view score is gradual, no
  threshold); room rows show m², zone, planned WWR, QV, AQ, dB(A); the Pareto background
  is the decoded pool. Batch contact sheet and PNG exports as before.
* **dB values:** everything shown in dB(A) is computed by the journal model (façade
  levels are inputs, indoor levels are modelled); nothing the thesis app could not compute
  any more is displayed.

## Fallback

Without torch or the checkpoint, Stage 2 draws *random admissible* arrangements (the
paper's random baseline) scored with the same equations and shows a pill saying so.

## Files changed vs the thesis app

`app/state.py`, `stages/stage0_inputs.py`, `stages/stage1_area.py` (units label),
`stages/stage2_zones.py`, `stages/stage4_schematic.py`, `app/widgets/parallel_coords.py`,
`app/widgets/design_explorer.py`, `app/widgets/batch_export.py`, `app/main_window.py`,
`models/zone_scoring.py` (new), `models/zone_model.py` (rewritten),
`models/zone_mock.py` (rewritten), `models/area_model.py` / `area_mock.py` (units stamp),
`models/connectivity_model.py` / `connectivity_mock.py` (rooms without a zone),
`assets/checkpoints/zone_model_v13.pt` (new; the thesis `zone_model.pt` removed).

## 2026-10-27 — Stage 3 vocabulary fix

`models/connectivity_model.py` now carries the verified vocabulary of the shipped
connectivity checkpoint (`_KNOWN_VOCABS[11]`, used before the alphabetical guess; the
checkpoint key and `LAYOUTGEN_CONN_VOCAB` still take precedence). The alphabetical guess
was wrong for this checkpoint and scrambled four of the eleven type indices (kitchen → the
model's "living", living → "parking", outside → "storage", storage → "veranda"), so every
Stage 3 graph generated by the thesis app and by this app before the fix was produced with
mis-typed rooms. Details and the measured effect in `assets/checkpoints/README.md` and in
`Train_set_34/paper_sections_v14/Stage2_connectivity_verification.md`. The checkpoint
itself is unchanged (epoch 200 of Train_set_05_graph/outputs_mr; its best validation
epoch was 22 — retraining with best-validation selection on the Stage 1 plan partition is
recommended before the results are read).

## 2026-10-27 (later) — retrained connectivity checkpoint

`assets/checkpoints/connectivity_model.pt` is now the retrained Stage 2 model: same
architecture, trained on the graphs of all 16,735 cleaned plans with the Stage 1 plan
partition, best-validation checkpoint (epoch 9), vocabulary stored in the file (the loader
reports `vocab from ckpt['type_vocab']`), doorless balconies joined to the room sharing a
door-length wall so every balcony of a program is connected. Held-out test (1,673 plans):
teacher-forced BCE 0.125, AUPRC 0.950, F1@0.5 0.860; free running: edge F1 0.74 (threshold)
/ 0.71 (sampling), all rooms reachable from outside in 97 % / 92 % of the graphs. Scripts,
graphs and evaluation in `Train_set_34\connectivity_v2\`.


## 6 Nov 2026 — final-version UI pass

* **Inputs.** Targets moved to the top of the panel: the QVCI / AQI sliders (with
  their full names underneath), then the reachable range, then two faded, disabled
  sliders — Daylight Index and Thermal Index — marked *future update*. Balcony sits
  in the Room Counts grid with the other types; a one-line note says it joins the
  plan at the connectivity stage.
* **Area distribution.** Each bubble shows the room area (m² / ft², following the
  units toggle) under its name in a smaller type.
* **Selection colour.** Selected zone / connectivity / schematic cards use a quiet
  mid-grey border and wash (plus a grey ✓ SELECTED badge); the accent red is kept
  for metrics and actions only. Zone tables and connectivity graphs start **all
  selected**; schematics start **unselected** (export is opt-in).
* **Connectivity.** The 3×3 grid lines are gone; small grey Z1 … Z9 labels sit in
  the top-left corner of each zone cell (row-major from NW). Every zoned room —
  bathrooms included — is pinned inside its own zone cell (12–88 % of the cell),
  balconies are kept inside the plan rectangle, and the *outside* node snaps just
  beyond the nearest plan edge.
* **Schematic synthesis** (`models/schematic_layout.py`, the former stub). After the
  thesis packing the rectangles **slide into contact** (gravity compaction towards
  the group centre, plus a pass that closes small gaps between connected rooms);
  every side is split into exterior and party walls, drawn as **double-line walls**
  (0.25 m exterior / 0.12 m partitions). **Doors** (0.9 m, 0.2 m from the corner,
  swinging into the more private room — door constants after Weber et al. 2024,
  `ramonweber/hypergraph` `Preset.cs`) sit on every shared wall of a connectivity
  edge; the **living–kitchen** wall is omitted entirely (open plan, no door); the
  entrance door goes on the longest exterior wall of the room the *outside* node
  connects to (accent triangle marks it). **Windows** are centred on exterior walls
  with length = planned WWR · 2.7 / 1.5 · wall length (1.5 m glazing in a 2.7 m
  storey), min 0.6 m, max 85 % of the free wall. Balconies get a railing line and a
  balcony door. **Furniture blocks** (bed + nightstands + wardrobe, sofa + coffee
  table + TV + dining set, kitchen counter with sink and hob + fridge, WC + basin +
  bathtub/shower, bistro set) are placed against walls, clear of door swings.
  Toolbar chips switch *Walls · doors · windows* and *Furniture* off; the schematic
  fill is 40 % of the box (was 25 %) because the compacted plan is tighter.
* **Synthesis animation.** The whole synthesis is recorded as ~125 frames (bubbles at
  the connectivity positions → bubble packing → rectangles → spring packing →
  compaction → contact → alignment → walls → doors & windows → furniture) and plays on
  every card when schematics are generated; *Anim speed* (ms per frame, 0 = finished
  plan only) and *Replay* are in the toolbar. **Save GIF / MP4** writes
  `schematic_synthesis_NN.gif` and `.mp4` (900 px, phase caption, 2 s hold on the
  finished plan) for the selected plans — or all plans if none is selected. GIF
  needs Pillow, MP4 needs `imageio` + `imageio-ffmpeg` (`pip install pillow imageio
  imageio-ffmpeg`); the dialog says which one is missing.
* **Pareto front.** A *0 – 1 | Scaled* pill in the panel header switches the axes
  between the full index range (default) and auto-fit.
* Toolbar of the schematic stage is now two rows. Settings keys added:
  `ui.schematicShowWalls`, `ui.schematicShowFurniture`, `ui.schematicAnimMs`,
  `ui.paretoFixedRange`.

### 6 Nov 2026 (evening) — secondary rooms relocated next to their primary room

`models/zone_relocation.py` runs between the zone stage and the connectivity
layout (called from `connectivity_model._build_graph_dict`):

1. **Reconcile with the inputs.** The zone table's rooms are matched with the
   requested room counts: requested rooms the zone model did not place (the
   balcony) are added with no zone, rooms that were not requested are dropped.
   The connectivity model's node list is built from the reconciled rooms.
2. **Relocate.** Primary living areas (living, bedroom, kitchen) keep their
   predicted zone. Every secondary room (bathroom, balcony …) must be in the same
   zone as, or adjacent to, the primary room it connects to (nearest primary
   through the predicted edges; the living room if it has none). If it is, it
   stays. If not, it moves to one of the anchor's adjacent zones (8-neighbour
   rule on the 3 × 3 grid): the least crowded one; Z5 only when every other
   adjacent zone is busier; a seeded random pick between equally crowded ones.
   Decisions are stored on the graph (`decisions`, `relocations`); the card
   footer shows "N moved" with the reasons as a tooltip.
3. **Animation.** The connectivity canvas first reveals the predicted edges with
   every room at its predicted zone, pauses, then moves the relocated rooms to
   their new zones (a faint ring marks the predicted spot, a dotted trail the
   move). Nodes and edges stay visible throughout; *Replay* repeats it. The
   schematic stage starts from the final (relocated) positions.

**Balcony rule (same evening, revised).** Decided entirely at the connectivity
stage; the schematic only follows it.

* *Which room.* The balcony keeps its predicted connection when that is a living
  room, bedroom or kitchen (living preferred if several). A balcony the model
  connected only to a bathroom (or to nothing) is re-assigned to the living room
  or a bedroom that does not yet have a balcony (living first); the bathroom edge is
  dropped and the new edge added (`rewire_balcony_edges`, logged as
  `balcony_rewired` and listed under "N moved").
* *Where.* The balcony has no zone: it hangs outside the plan on a façade of its
  room — a side of that room's zone that lies on the plan boundary (corner zones
  have two: the one whose three cells hold fewer rooms wins, tie random; an
  interior room gets the least crowded side of the plan). The node is drawn just
  beyond the plan edge, level with its room, and animates out from the centre.
* *Schematic.* The balcony takes no part in the packing or compaction; at every
  step it is simply kept flush against that façade of its room (`glue_balconies`),
  turned along the wall and **aligned with a corner of that façade** (the corner
  farther from the plan centre first, then the other corner, then positions in
  between; the room's other exterior sides only if none is free; and when every
  spot is taken, the balcony is pushed outward along the façade normal until it is
  clear of the rooms — it never overlaps one). A balcony that ends up **detached**
  from its room (no contact over a door's width) is **removed** from the plan; the
  animation shows a "Detached balcony removed" step and the final caption names it
  (`Synthesis.removed`). No
  separate "attach" phase any more.

## 6 Nov 2026 (late) — zone-preserving synthesis: tried and parked

The packing synthesis is kept. An exposure audit showed it preserves only ~50 % of
the predicted zones (see `_experiments/README.md`); two alternatives (band layout
with halls; zone-aware compaction) were built, measured and parked in
`_experiments/`. To be resolved tomorrow.

## 6 Nov 2026 (night) — Stage 1: Draw geometry mode

Stage 1 has a mode pill: **Generate area distributions** (as before) | **Draw
geometry** (`app/widgets/geometry_canvas.py`). In draw mode a palette of room
chips (living, bedroom, kitchen, bathroom, balcony) is dragged onto a canvas; a
square room of a typical size lands where it is dropped (20 / 14 / 9 / 5 / 5 m²).
Rooms are moved by dragging and sized by dragging an edge or corner (0.1 m snap,
1 m minimum side); each shows its area, w × h and aspect ratio. A m² / ft² pill
sets the display units (metres inside; the Stage-0 plan rectangle is drawn as a
reference). *Use this layout* turns the drawing into the single Stage-1 variation
(areas converted to the plan units of Stage 0, `area_m2`, `w_m`, `h_m`, per-room
aspect ratio N–S ÷ E–W) and sets the room counts of the inputs to the drawn
program; Stage 2 then runs on it as usual. Drawn rooms keep their own shape
downstream: `zone_scoring.rooms_from_variation` uses `h_m / w_m` instead of the
per-type aspect ratio, the zone tables carry `w_m` / `h_m` for drawn rooms only,
and the schematic uses `w_m / h_m`. Generated variations are unaffected.

Revision (22:30): *Draw room program* is the first and default mode and opens with
a sample program (1 living, 2 bedrooms, kitchen, bathroom, balcony); the canvas is
seamless with a dot grid (one dot per metre / foot) and a **Scale** slider
(px per metre) so large programs fit; palette chips are neutral grey with a swatch
of the room colour. The Room Counts and Aspect Ratios sections moved from the
Inputs panel into the *Generate area distributions* strip of Stage 1 (they are only
inputs of the generator — the drawing carries counts and shapes itself); the
Inputs button is now *Room program →* and only samples variations when the
generator mode is active.
