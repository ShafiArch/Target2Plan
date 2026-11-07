# Model checkpoints

Drop trained `.pt` files here. The Stage panels auto-detect them on startup
and use the real model; if the file is missing or torch isn't installed,
they fall back to the mock samplers.

Expected names (the loader tries these in order):

| Stage | Default filename                 | What it is                                     |
|-------|----------------------------------|------------------------------------------------|
| 1     | `area_model.pt`                  | LogisticNormalNet — room counts → area shares  |
| 2     | `zone_model_v13.pt` / `best_model.pt` | v13 cGAT-AR — rooms + site + targets → 3×3 zone assignment |
| 3     | `connectivity_model.pt` / `connectivity.pt`   | EdgeARModel — rooms → access + exposure edges |

## Checkpoint format

### Stage 1 (area)

```python
ckpt = {
    "vocab":       ["living", "bedroom", "kitchen", ...],   # room types
    "config":      {"HIDDEN_DIM": 128, "LATENT_DIM": 16, "DROPOUT": 0.10},
    "model_state": state_dict,
    "epoch":       42,                                       # optional
}
```

### Stage 2 (zone) — journal version

`zone_model_v13.pt` is the v13 cGAT-AR model of the journal paper
(`corpus_v13/train.py`, trained on the outcome-labelled arrangement corpus,
best epoch 36 / 40). The app accepts either the raw `state_dict` that
`train.py` writes as `best_model.pt` or the wrapped form shipped here:

```python
ckpt = {
    "format":      "v13",
    "model_state": state_dict,          # ZoneModel: proj / layers / gru / head (793,609 params)
    "config":      {"ROOM_FEAT_DIM": 11, "CTX_DIM": 31, "GNN_DIM": 128, "GNN_LAYERS": 2,
                    "GNN_HEADS": 4, "GRU_DIM": 256, "GRU_LAYERS": 2, "MAX_ROOMS": 14, ...},
    "trained":     {"corpus": ..., "best_epoch": 36, "val_ce": 1.615, ...},
}
```

Room features (11): type one-hot (living, kitchen, bedroom, bathroom),
area / 50, area fraction, planned WWR, w/√area, h/√area, exterior-door
ratio, front-door flag. Context (31): targets (QVCI, AQI) + present flag,
rooms remaining, zone usage, v(z), zone noise. Capacity and type masks
are applied in the softmax.

Thesis-era checkpoints (`zone_model.pt`, v8 / v11 / v12 with a
``config`` + ``model`` + scaler layout) are refused — their features and
indices are not the journal's. To refresh the model after a retraining,
copy `train_v13/best_model.pt` here as `zone_model_v13.pt`.

### Stage 3 (connectivity)

**Shipped checkpoint (since 2026-10-27): `connectivity_model.pt` = the retrained
Stage 2 model** (`Train_set_34\connectivity_v2\train\connectivity_model.pt`):
same EdgeARModel (type emb 128 ×2 + previous-bit emb 16 → GRU 512 → linear,
1,209,249 parameters), trained on the accessibility graphs of all 16,735 cleaned
plans partitioned by the Stage 1 plan split (13,389 / 1,673 / 1,673), best
validation epoch retained (epoch 9, val BCE 0.127; test BCE 0.125, AUPRC 0.950,
F1@0.5 0.860), balconies without a door joined to the room sharing a door-length
wall. The vocabulary is stored inside:

```python
ckpt = {
    "model_state": state_dict,                # EdgeARModel
    "type_vocab": {"balcony": 0, ..., "veranda": 10},   # str→int, used by the loader (priority 1)
    "vocab": [...],                           # the same, as a list
    "config": {...}, "epoch": 9, "val_bce": 0.1267, "train_bce": ...,
}
```

The previous checkpoint (thesis run on 2,001 plans, epoch 200, no vocabulary
stored) remains in `Train_set_05_graph\outputs_mr\checkpoints\Connectivity_model.pt`.

Loader format (unchanged): matches the upstream `Connectivity_train_code.py` /
`Connectivity_generation_code_pyside_final.py` checkpoints as well:

```python
ckpt = {
    "model_state": state_dict,                # EdgeARModel
    # Optional: "type_vocab" / "vocab" str→int map (alphabetical)
}
```

**Vocab caveat.** `Connectivity_train_code.py` does not save the vocab
in the checkpoint — it rebuilds it at training time via
`build_type_vocab(files)` which scans the JSON corpus and returns
`sorted(types) → 0..n-1`. The upstream PySide tool requires you to
point to the **same dataset folder** at generation time so it can
re-derive that exact alphabetical mapping.

This wrapper, in priority order:

1. **Checkpoint key** — if you `torch.save(...)` a `type_vocab` (dict
   or list) into the .pt file alongside `model_state`, it's used
   directly. This is the most reliable option for new training runs.
2. **`LAYOUTGEN_CONN_VOCAB`** env var — a comma-separated list of
   types in training order (= alphabetical). Use this if your
   training set had types outside the default below.
3. **Verified checkpoint table** (`_KNOWN_VOCABS` in
   `models/connectivity_model.py`) — the vocabulary of the shipped
   `connectivity_model.pt` (Train_set_05_graph/outputs_mr, epoch 200,
   `n_types=11`), verified on 2026-10-27 against the training folder:
   `balcony, bathroom, bedroom, garden, kitchen, living, outside,
   parking, stair, storage, veranda` (the sorted set of node types of
   `dataset_graph/`; no `pool` survives extraction). Confirmed by
   reproducing the logged validation BCE (0.166 vs 0.1635) with it.
4. **Built-in alphabetical default** — last resort, matches what
   `build_type_vocab()` would have produced on a standard residential
   dataset. For `n_types=7` that's
   `balcony, bathroom, bedroom, kitchen, living, outside, storage`.
   Extended alphabetically for higher n. **For `n_types=11` this guess
   was wrong** (it inserted dining/laundry/office and dropped
   parking/stair/veranda), which shifted the indices of kitchen,
   living, outside and storage and scrambled the Stage 3 predictions
   of the thesis app and of this app before 2026-10-27: on the 200
   held-out graphs the scrambled mapping gives edge F1 0.37 and only
   48 % of graphs with every room reachable from outside (threshold
   decoding), against 0.72 and 96.5 % with the verified mapping.

The vocab actually used at load time is printed to stdout so you can
verify it matches your training set. If you see fragmented graphs
where the upstream tool produces a connected one, the vocab is the
first place to check.

## Override paths

If you keep checkpoints somewhere else, point the loader with env vars:

```bash
LAYOUTGEN_AREA_CKPT=/path/to/my_area_ckpt.pt \
LAYOUTGEN_ZONE_CKPT=/path/to/my_zone_ckpt.pt \
LAYOUTGEN_CONN_CKPT=/path/to/my_conn_ckpt.pt \
python main.py
```
