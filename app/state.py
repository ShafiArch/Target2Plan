"""
Session state — the single source of truth for the whole app.

Every stage panel reads from and writes to ``SessionStore``, which
mirrors the shape of zone_app's SessionContext.jsx so we can read and
write the same JSON the web app produces.

Stage panels never talk to each other directly — they observe each
other's outputs through this object via Qt signals.

Convention
----------
* Dataclasses describe the *shape* of the data with sensible defaults.
* SessionStore is a QObject wrapper that owns the mutable state and
  emits granular signals (``stage2Changed`` etc.) so panels only
  redraw when their slice changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal


# ─────────────────────────────────────────────────────────────────────
#  Stage 0 — Inputs
# ─────────────────────────────────────────────────────────────────────

ZONED_TYPES = ("living", "kitchen", "bedroom", "bathroom")   # placed by the zone model
EXTRA_TYPES = ("balcony",)                                   # connectivity / schematic only

@dataclass
class PlanGeometry:
    """Unit outline (a rectangle) in the chosen units.

    The journal zone model works in metres: ``m`` and ``ft`` convert
    directly, ``px`` needs ``px_per_m`` (how many pixels one metre is).
    """
    width:    float = 12.0    # E–W extent
    height:   float = 9.0     # N–S extent
    units:    str   = "m"     # "px" | "m" | "ft"
    px_per_m: float = 100.0   # only used when units == "px"

    @property
    def total_area(self) -> float:
        return self.width * self.height

    @property
    def scale_to_m(self) -> float:
        """Metres per plan unit."""
        if self.units == "px":
            return 1.0 / max(float(self.px_per_m), 1e-6)
        return {"m": 1.0, "ft": 0.3048}.get(self.units, 1.0)

    @property
    def total_area_m2(self) -> float:
        return self.total_area * self.scale_to_m ** 2


@dataclass
class SiteSide:
    """One cardinal direction's outdoor conditions.

    ``spl_dB`` is the outdoor level in dB(A); the corpus covers 30–80 dB(A)
    and the model is only trusted inside that range.
    """
    view:   str   = "Vegetation"  # Vegetation|Sky|Urban|Balcony|No View
    spl_dB: float = 62.0


@dataclass
class SiteConditions:
    N: SiteSide = field(default_factory=lambda: SiteSide("Vegetation", 55.0))
    S: SiteSide = field(default_factory=lambda: SiteSide("Urban",      70.0))
    E: SiteSide = field(default_factory=lambda: SiteSide("No View",    65.0))
    W: SiteSide = field(default_factory=lambda: SiteSide("Sky",        60.0))


@dataclass
class Preferences:
    """Target conditioning of the v13 zone model and its developer controls.

    * ``target_qvci`` / ``target_aqi`` — the requested plan-level indices
      (the model is conditioned on exactly these two numbers);
    * ``targets_enabled`` — off = unconditioned sampling (target-present
      flag 0), the paper's B1 setting;
    * ``capacity_override`` — rooms allowed per zone class; ``None`` =
      the trained rule by plan size (corner 2 / 3, cardinal 3, interior
      1 / 2, the step at 8 rooms);
    * ``interior_bathrooms_only`` — habitable rooms must have a façade.
    """
    target_qvci:             float = 0.70
    target_aqi:              float = 0.60
    targets_enabled:         bool  = True
    capacity_override:       Optional[Dict[str, int]] = None
    interior_bathrooms_only: bool  = True


@dataclass
class Params:
    """How much work each stage should do."""
    n_plans:     int   = 12   # number of zone-assignment plans shown (Stage 2)
    samples_n:   int   = 200  # area-model sample pool (Stage 1)
    temperature: float = 0.8  # zone-model sampling temperature (Stage 2; the paper used 0.8)
    zone_samples: int  = 80   # zone-model sample pool (Stage 2), plus one greedy decode


@dataclass
class InputsState:
    # The journal vocabulary: four zoned types (living, kitchen, bedroom,
    # bathroom) plus balconies, which are not zoned but take part in the
    # connectivity graph and the schematic.
    room_counts: Dict[str, int] = field(default_factory=lambda: {
        "living": 1, "bedroom": 2, "kitchen": 1, "bathroom": 1, "balcony": 1,
    })
    plan_geometry:   PlanGeometry   = field(default_factory=PlanGeometry)
    site_conditions: SiteConditions = field(default_factory=SiteConditions)
    # Per-room-type aspect ratio (N–S extent / E–W extent). Medians of the
    # corpus rooms (dataset_fix_floored): living 0.9, kitchen 1.0,
    # bedroom 1.0, bathroom 1.2; balcony kept from the thesis app.
    aspect_ratios:   Dict[str, float] = field(default_factory=lambda: {
        "living": 0.90, "kitchen": 1.00, "bedroom": 1.00, "bathroom": 1.20, "balcony": 2.50,
    })
    # Planned window-to-wall ratio per room type — a design requirement,
    # applied to every façade of the zone the room is placed in (full view
    # credit at 0.40). Defaults are the corpus medians (planned WWR of
    # dataset_fix_floored): living 0.24, kitchen 0.47, bedroom 0.37,
    # bathroom 0.23.
    wwr:             Dict[str, float] = field(default_factory=lambda: {
        "living": 0.25, "kitchen": 0.45, "bedroom": 0.35, "bathroom": 0.20, "balcony": 0.60,
    })
    prefs:           Preferences      = field(default_factory=Preferences)
    params:          Params           = field(default_factory=Params)

    @property
    def total_rooms(self) -> int:
        return sum(self.room_counts.values())

    @property
    def zoned_rooms(self) -> int:
        """Rooms the zone model places (the four-type vocabulary)."""
        return sum(v for k, v in self.room_counts.items() if k in ZONED_TYPES)


# ─────────────────────────────────────────────────────────────────────
#  Stage outputs — kept as plain dicts so they round-trip through JSON
#  with the same field names the web app uses.
# ─────────────────────────────────────────────────────────────────────

@dataclass
class Stage1AreasState:
    candidates:   List[Dict[str, Any]] = field(default_factory=list)
    selected_idx: Optional[int]        = None
    mocked:       bool                 = False


@dataclass
class Stage2ZonesState:
    tables:        List[Dict[str, Any]] = field(default_factory=list)
    selected_idxs: List[int]            = field(default_factory=list)
    mocked:        bool                 = False
    # Lightweight (plan_index, qvci, aqi, concentration) tuples for
    # every unique sample the zone model produced — including those
    # that didn't make it into ``tables`` (which is capped at
    # n_tables). Used by Stage 4's Pareto plot to show the full
    # sampled distribution as faded background dots.
    sample_pool:   List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class Stage3ConnectivityState:
    graphs:        List[Dict[str, Any]] = field(default_factory=list)
    selected_idxs: List[int]            = field(default_factory=list)
    mocked:        bool                 = False


@dataclass
class Stage4SchematicsState:
    schematics:    List[Dict[str, Any]] = field(default_factory=list)
    selected_idxs: List[int]            = field(default_factory=list)
    mocked:        bool                 = False


@dataclass
class UIState:
    active_stage: str = "inputs"  # inputs|area|zones|connectivity|schematic
    theme:        str = "offwhite"


# ─────────────────────────────────────────────────────────────────────
#  SessionStore
# ─────────────────────────────────────────────────────────────────────

class SessionStore(QObject):
    """
    Holds the entire session state and broadcasts changes.

    Use granular signals (``stage2Changed`` etc.) to redraw only the
    panel that cares; ``stateChanged`` fires on every mutation, useful
    for status dots and the save indicator.
    """

    stateChanged       = Signal()
    inputsChanged      = Signal()
    stage1Changed      = Signal()
    stage2Changed      = Signal()
    stage3Changed      = Signal()
    stage4Changed      = Signal()
    activeStageChanged = Signal(str)
    themeChanged       = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.inputs:       InputsState              = InputsState()
        self.stage1_areas: Stage1AreasState         = Stage1AreasState()
        self.stage2_zones: Stage2ZonesState         = Stage2ZonesState()
        self.stage3_conn:  Stage3ConnectivityState  = Stage3ConnectivityState()
        self.stage4_sch:   Stage4SchematicsState    = Stage4SchematicsState()
        self.ui:           UIState                  = UIState()

    # ── inputs ────────────────────────────────────────────────────
    def set_inputs(self, inputs: InputsState) -> None:
        self.inputs = inputs
        self.inputsChanged.emit()
        self.stateChanged.emit()

    # ── stage 1 ───────────────────────────────────────────────────
    def set_stage1_areas(self,
                         candidates: List[Dict[str, Any]],
                         selected_idx: Optional[int] = None,
                         mocked: bool = False) -> None:
        self.stage1_areas = Stage1AreasState(candidates, selected_idx, mocked)
        self.stage1Changed.emit()
        self.stateChanged.emit()

    def set_stage1_selection(self, idx: Optional[int]) -> None:
        self.stage1_areas.selected_idx = idx
        self.stage1Changed.emit()
        self.stateChanged.emit()

    # ── stage 2 ───────────────────────────────────────────────────
    def set_stage2_zones(self,
                         tables: List[Dict[str, Any]],
                         selected_idxs: Optional[List[int]] = None,
                         mocked: bool = False,
                         sample_pool: Optional[List[Dict[str, Any]]] = None
                         ) -> None:
        self.stage2_zones = Stage2ZonesState(
            tables, selected_idxs or [], mocked,
            sample_pool or [])
        self.stage2Changed.emit()
        self.stateChanged.emit()

    def set_stage2_selection(self, idxs: List[int]) -> None:
        self.stage2_zones.selected_idxs = sorted(set(idxs))
        self.stage2Changed.emit()
        self.stateChanged.emit()

    # ── stage 3 ───────────────────────────────────────────────────
    def set_stage3_connectivity(self,
                                graphs: List[Dict[str, Any]],
                                selected_idxs: Optional[List[int]] = None,
                                mocked: bool = False) -> None:
        self.stage3_conn = Stage3ConnectivityState(
            graphs, selected_idxs or [], mocked,
        )
        self.stage3Changed.emit()
        self.stateChanged.emit()

    def set_stage3_selection(self, idxs: List[int]) -> None:
        self.stage3_conn.selected_idxs = sorted(set(idxs))
        self.stage3Changed.emit()
        self.stateChanged.emit()

    # ── stage 4 ───────────────────────────────────────────────────
    def set_stage4_schematics(self,
                              schematics: List[Dict[str, Any]],
                              selected_idxs: Optional[List[int]] = None,
                              mocked: bool = False) -> None:
        self.stage4_sch = Stage4SchematicsState(
            schematics, selected_idxs or [], mocked,
        )
        self.stage4Changed.emit()
        self.stateChanged.emit()

    def set_stage4_selection(self, idxs: List[int]) -> None:
        self.stage4_sch.selected_idxs = sorted(set(idxs))
        self.stage4Changed.emit()
        self.stateChanged.emit()

    # ── ui ────────────────────────────────────────────────────────
    def set_active_stage(self, stage_id: str) -> None:
        if self.ui.active_stage != stage_id:
            self.ui.active_stage = stage_id
            self.activeStageChanged.emit(stage_id)
            self.stateChanged.emit()

    def set_theme(self, theme_name: str) -> None:
        if self.ui.theme != theme_name:
            self.ui.theme = theme_name
            self.themeChanged.emit(theme_name)
            self.stateChanged.emit()

    # ── helpers ──────────────────────────────────────────────────
    def reset(self) -> None:
        """Wipe everything back to defaults. Theme is preserved."""
        keep_theme = self.ui.theme
        self.inputs       = InputsState()
        self.stage1_areas = Stage1AreasState()
        self.stage2_zones = Stage2ZonesState()
        self.stage3_conn  = Stage3ConnectivityState()
        self.stage4_sch   = Stage4SchematicsState()
        self.ui           = UIState(theme=keep_theme)
        # Emit every slice so all panels refresh.
        self.inputsChanged.emit()
        self.stage1Changed.emit()
        self.stage2Changed.emit()
        self.stage3Changed.emit()
        self.stage4Changed.emit()
        self.stateChanged.emit()

    def per_stage_status(self) -> Dict[str, str]:
        """
        Status dot for each stage card: ``"done" | "pending" | "idle"``.

        * idle    — stage hasn't been run yet
        * pending — stage has output but no selection committed
        * done    — selection committed (= ready to feed next stage)
        """
        def status(populated: bool, selected: bool) -> str:
            if selected:   return "done"
            if populated:  return "pending"
            return "idle"
        return {
            "inputs": "done" if self.inputs.total_rooms > 0 else "idle",
            "area":   status(bool(self.stage1_areas.candidates),
                             self.stage1_areas.selected_idx is not None),
            "zones":  status(bool(self.stage2_zones.tables),
                             bool(self.stage2_zones.selected_idxs)),
            "connectivity": status(bool(self.stage3_conn.graphs),
                                   bool(self.stage3_conn.selected_idxs)),
            "schematic":    status(bool(self.stage4_sch.schematics),
                                   bool(self.stage4_sch.selected_idxs)),
        }
