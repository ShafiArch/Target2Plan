"""
MainWindow — the top-level frame.

Top:    a Header bar with the app title, version pill, action pills
        (Load / Export / Reset) and a theme switcher.
Below:  a horizontal QScrollArea hosting the five StageCard columns
        (Inputs, Area, Zones, Connectivity, Schematic).

Stage panels are added as bodies via ``StageCard.set_body``. Each stage
panel will live in ``stages/stageN_xxx.py``. While those panels don't
exist yet, each card gets a "coming soon" placeholder so the chrome is
visible and theme-checkable.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from . import theme as theme_mod
from .state import SessionStore
from .widgets.stage_card import StageCard


# Stage column widths — exact port of App.jsx (className="w-[460px]" etc.)
STAGE_WIDTHS = {
    "inputs":       460,
    "area":         860,
    "zones":        860,
    "connectivity": 1080,
    "schematic":    1080,
}

# (stage_id, number, title, subtitle)
STAGE_META = [
    ("inputs",       0, "Inputs",            "Rooms · geometry · site · targets"),
    ("area",         1, "Area Distribution", "Pick one variation"),
    ("zones",        2, "Zone Assignments",  "v13 model · targets QVCI / AQI"),
    ("connectivity", 3, "Connectivity",      "Nodes & edges per plan"),
    ("schematic",    4, "Schematic Design",  "Areas × zones × connectivity"),
]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Target2Plan")
        self.resize(1700, 1000)

        self.settings = QSettings("ARML", "LayoutGenerator")
        self.store = SessionStore(self)

        # Restore last-used theme.
        saved_theme = self.settings.value("theme", "offwhite")
        if saved_theme not in theme_mod.PALETTES:
            saved_theme = "offwhite"
        self.store.ui.theme = saved_theme

        # ── Central widget ────────────────────────────────────────
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        # Header
        self.header = self._build_header()
        root.addWidget(self.header)

        # Horizontal strip of stage cards
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        strip = QWidget(self.scroll)
        strip.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        strip_h = QHBoxLayout(strip)
        strip_h.setContentsMargins(16, 16, 16, 16)
        strip_h.setSpacing(12)

        self.cards: dict[str, StageCard] = {}
        self.stage_panels: dict[str, QWidget] = {}
        for stage_id, number, title, subtitle in STAGE_META:
            card = StageCard(
                stage_id=stage_id, number=number,
                title=title, subtitle=subtitle,
                width=STAGE_WIDTHS[stage_id], parent=strip,
            )
            body = self._build_stage_body(stage_id, title)
            self.stage_panels[stage_id] = body
            card.set_body(body)
            self.cards[stage_id] = card
            strip_h.addWidget(card)
        strip_h.addStretch(1)

        # ── Cross-stage wiring ────────────────────────────────────
        # Inputs' "Generate" button kicks off Stage 1 generation.
        self._wire_cross_stage_signals()

        self.scroll.setWidget(strip)
        root.addWidget(self.scroll, 1)

        # ── Wire status dots ──────────────────────────────────────
        self.store.stateChanged.connect(self._refresh_status_dots)
        self._refresh_status_dots()

        # ── Apply theme last ──────────────────────────────────────
        theme_mod.apply(QApplication.instance(), saved_theme)
        self._update_theme_button_label()

    # ──────────────────────────────────────────────────────────────
    # Header
    # ──────────────────────────────────────────────────────────────
    def _build_header(self) -> QFrame:
        h = QFrame(self)
        h.setObjectName("AppHeader")
        h.setFixedHeight(56)
        lay = QHBoxLayout(h)
        lay.setContentsMargins(20, 8, 20, 8)
        lay.setSpacing(12)

        title = QLabel("◈ Target2Plan")
        title.setObjectName("AppTitle")

        lay.addWidget(title)
        lay.addStretch(1)

        self.btn_load   = self._make_pill("Load session",   "default")
        self.btn_export = self._make_pill("Export session", "default")
        self.btn_reset  = self._make_pill("Reset",          "danger")
        self.btn_theme  = self._make_pill("Off-white",      "theme")

        self.btn_load.clicked.connect(self._on_load_session)
        self.btn_export.clicked.connect(self._on_export_session)
        self.btn_reset.clicked.connect(self._on_reset)
        self.btn_theme.clicked.connect(self._on_cycle_theme)

        lay.addWidget(self.btn_load)
        lay.addWidget(self.btn_export)
        lay.addWidget(self.btn_reset)

        sep = QFrame()
        sep.setObjectName("HeaderSep")
        sep.setFixedSize(1, 22)
        lay.addWidget(sep)

        lay.addWidget(self.btn_theme)
        return h

    @staticmethod
    def _make_pill(text: str, role: str) -> QPushButton:
        b = QPushButton(text)
        b.setProperty("pillRole", role)
        b.setCursor(Qt.PointingHandCursor)
        return b

    def _build_stage_body(self, stage_id: str, title: str) -> QWidget:
        """Return the panel widget for ``stage_id``.

        Each stage maps to a module under ``stages/``. Stages that
        don't exist yet fall back to a 'coming soon' placeholder so
        the chrome is fully usable while we build out the rest.
        """
        if stage_id == "inputs":
            from stages.stage0_inputs import InputsPanel
            return InputsPanel(self.store)
        if stage_id == "area":
            from stages.stage1_area import AreaStage
            return AreaStage(self.store)
        if stage_id == "zones":
            from stages.stage2_zones import ZoneStage
            return ZoneStage(self.store)
        if stage_id == "connectivity":
            from stages.stage3_connectivity import ConnectivityStage
            return ConnectivityStage(self.store)
        if stage_id == "schematic":
            from stages.stage4_schematic import SchematicStage
            return SchematicStage(self.store)
        return self._placeholder_body(f"{title} — coming soon")

    def _wire_cross_stage_signals(self) -> None:
        """Connect panel signals that need to talk between stages."""
        from stages.stage0_inputs import InputsPanel
        from stages.stage1_area import AreaStage
        from stages.stage2_zones import ZoneStage
        from stages.stage3_connectivity import ConnectivityStage
        from stages.stage4_schematic import SchematicStage

        inp   = self.stage_panels.get("inputs")
        area  = self.stage_panels.get("area")
        zones = self.stage_panels.get("zones")
        conn  = self.stage_panels.get("connectivity")
        sch   = self.stage_panels.get("schematic")

        # Inputs → Stage 1: the room counts and aspect ratios live in the
        # generate strip of Stage 1; the Inputs button opens the room
        # program (and samples variations when the generator is active).
        if isinstance(inp, InputsPanel) and isinstance(area, AreaStage):
            area.adopt_input_sections([getattr(inp, "_sec_room_counts", None),
                                       getattr(inp, "_sec_plan", None),
                                       getattr(inp, "_sec_aspect", None)])
            inp.generateRequested.connect(area.open_room_program)

        # Stage 1 → Stage 2
        if isinstance(area, AreaStage) and isinstance(zones, ZoneStage):
            area.proceedRequested.connect(zones.regenerate)

        # Stage 2 → Stage 3
        if isinstance(zones, ZoneStage) and isinstance(conn, ConnectivityStage):
            zones.proceedRequested.connect(conn.regenerate)

        # Stage 3 → Stage 4
        if isinstance(conn, ConnectivityStage) and isinstance(sch, SchematicStage):
            conn.proceedRequested.connect(sch.regenerate)

    @staticmethod
    def _placeholder_body(label: str) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.addStretch(1)
        lbl = QLabel(label)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setProperty("placeholder", True)
        v.addWidget(lbl)
        v.addStretch(1)
        return w

    # ──────────────────────────────────────────────────────────────
    # Slots
    # ──────────────────────────────────────────────────────────────
    def _refresh_status_dots(self) -> None:
        statuses = self.store.per_stage_status()
        for stage_id, card in self.cards.items():
            card.set_status(statuses.get(stage_id, "idle"))

    def _on_cycle_theme(self) -> None:
        nxt = theme_mod.next_theme(self.store.ui.theme)
        self.store.set_theme(nxt)
        self.settings.setValue("theme", nxt)
        theme_mod.apply(QApplication.instance(), nxt)
        self._update_theme_button_label()

    def _update_theme_button_label(self) -> None:
        self.btn_theme.setText(theme_mod.THEME_LABELS[self.store.ui.theme])

    def _on_reset(self) -> None:
        ans = QMessageBox.question(
            self, "Reset",
            "Reset the whole session? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ans == QMessageBox.Yes:
            self.store.reset()

    def _on_load_session(self) -> None:
        # session_io.py will be wired once Inputs panel exists.
        QMessageBox.information(
            self, "Load session",
            "Session loading will be wired once the Inputs panel exists.",
        )

    def _on_export_session(self) -> None:
        QMessageBox.information(
            self, "Export session",
            "Session export will be wired once Stage 1+ outputs exist.",
        )
