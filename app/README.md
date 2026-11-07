# Layout Generator ARML — journal version

A PySide6 desktop app that combines three trained models — area
distribution, zone assignment (the journal paper's v13 cGAT-AR model,
conditioned on the requested QVCI / AQI), and connectivity generation —
into a single end-to-end pipeline, plus a final schematic-design view
with a 2-D AQI × QVCI Pareto front. See `JOURNAL_VERSION_NOTES.md` at
the repository root for what changed against the thesis app.

The chrome is a 1:1 port of the existing web frontend (the same one
that runs against mock data); the compute backbone is lifted from the
three working PySide6 standalone apps.

## Layout

```
Layout_Generator_ARML/
├── main.py                          entry point
├── app/
│   ├── theme.py                     QSS + palettes for 3 themes
│   ├── state.py                     SessionStore (single source of truth)
│   ├── session_io.py                load/export session JSON   [todo]
│   ├── main_window.py               header + horizontal stage strip
│   └── widgets/
│       ├── stage_card.py            numbered column container
│       ├── slim_slider.py           shared narrow slider        [todo]
│       └── pareto_view.py           AQI × QVCI Pareto strip     [todo]
├── stages/
│   ├── stage0_inputs.py             room counts / geometry / site  [todo]
│   ├── stage1_area.py               area distribution variations   [todo]
│   ├── stage2_zones.py              zone assignments + parcoords   [todo]
│   ├── stage3_connectivity.py       graph generation with anim     [todo]
│   └── stage4_schematic.py          rectangles + Pareto            [todo]
├── models/                          headless compute, no Qt
│   ├── area_model.py                [todo — refactored from existing]
│   ├── zone_model.py                [todo — refactored from existing]
│   ├── connectivity_model.py        [todo — refactored from existing]
│   └── schematic_layout.py          [todo — packing + alignment]
└── assets/
    ├── checkpoints/                 *.pt files
    └── sessions/                    *.json
```

## Run

```bash
python main.py
```

The current build shows the header bar (theme switcher works) and five
empty stage columns. Stage panels are added incrementally.

## Theme

Three themes, switchable by clicking the theme pill in the header:

| Theme    | Use case                                |
|----------|------------------------------------------|
| Off-white| Default, matches the web-app screenshots |
| White    | Pure white for printing or strong light  |
| Dark     | Near-black with cool tint                |

Theme is remembered between launches via `QSettings`.
