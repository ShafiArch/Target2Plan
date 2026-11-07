# Schematic-synthesis experiments (6 Nov 2026, evening) — NOT wired in

The app uses the packing synthesis (`models/schematic_layout.py` in the main tree:
bubbles → spring packing → compaction → walls / doors / windows / furniture).
An exposure audit (`exposure_audit.py`, the corpus zone rule of §3.2.3 applied to
the drawn plans) showed that this packing keeps only ~50 % (6 rooms) / ~44 %
(11 rooms) of the rooms in their predicted zone, so the drawing's QVCI / AQI differ
from the card values (mean |ΔAQI| ≈ 0.04, worst 0.12). Two alternatives were tried:

* `A_zone_preserving_bands/` — plan rectangle subdivided into three bands and zone
  cells, empty zones become halls, rooms rescaled/reshaped to fit. Rooms in zone:
  78 % / 68 %; mean |ΔQVCI| 0.000 / 0.002, |ΔAQI| 0.007 / 0.001. Rejected for now:
  room shapes change and 12–33 % of the plan becomes undefined circulation.
  (Drop the three files over `models/`, `app/widgets/`, `stages/` to try it; it also
  adds the realized-index audit to the cards.)
* `C_zone_aware_compaction/` — true-scale rooms with fixed shapes placed at their
  zone anchors and compacted. Rooms in zone: 33 % / 39 % — worse than the packing,
  because façade length is a hard budget (e.g. living Z7 + bedroom Z4 need 11.3 m of
  west façade on a 9 m plan).

Open question for tomorrow: how to realize the zone arrangement without changing the
room shapes or leaving undefined space — the two cannot both be exact in a fixed
frame, so the paper's synthesis claim needs a defined tolerance.
