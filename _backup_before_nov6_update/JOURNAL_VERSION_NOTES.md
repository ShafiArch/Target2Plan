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
