"""
Stage 3 — Connectivity.

Renders one card per *selected* zone-assignment plan from Stage 2.
Each card animates edges in the order an autoregressive sampler
would emit them: access edges first (room-to-room, in reveal order),
then exposure edges (room → direction anchor, dashed pink).

Primary rooms stay pinned at their Stage-2 zone positions; non-primary
rooms tween from a central starting position toward their settled
zone as edges accumulate (cubic ease-out on access-edge progress).

Layout (mirrors zone_app/ConnectivityStage.jsx):

    ┌─────────────────────────────────────────────────────────────┐
    │  [empty/CTA]   or   [4×N animated graph grid]                │
    ├─────────────────────────────────────────────────────────────┤
    │  Regenerate · Anim speed 25 ms ↻ Replay · Select all         │
    │                          N graphs selected · Proceed →       │
    └─────────────────────────────────────────────────────────────┘

Animation speed persists in QSettings ("ui.connectivityAnimSpeedMs").
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from app import theme as theme_mod
from app.state import SessionStore
from app.widgets.animated_graph import AnimatedGraph
from app.widgets.connectivity_export import save_connectivity_png
from models.connectivity_mock import mock_connectivity_graphs
from models.connectivity_model import (
    ModelNotAvailable as ConnectivityModelNotAvailable,
    generate_connectivity_graphs,
)


SPEED_KEY      = "ui.connectivityAnimSpeedMs"
DEFAULT_SPEED  = 25
MIN_SPEED      = 0    # 0 = animation off
MAX_SPEED      = 250


def _set_prop(widget: QWidget, name: str, value) -> None:
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


# ─────────────────────────────────────────────────────────────────────
#  _GraphThumbnail — one card with animated canvas
# ─────────────────────────────────────────────────────────────────────

class _GraphThumbnail(QFrame):
    clicked = Signal(int)

    def __init__(self, graph: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.plan_index = int(graph.get("meta", {}).get("plan_index", 0))
        self._selected = False

        self.setProperty("connCard", True)
        self.setProperty("connCardSelected", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMinimumSize(180, 230)
        self.setMaximumHeight(340)

        # Header
        header = QFrame(self); header.setObjectName("ConnCardHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(8, 4, 8, 4); h.setSpacing(6)
        self.idx_lbl = QLabel(f"#{self.plan_index:02d}")
        self.idx_lbl.setProperty("zoneCardIdx", True)
        self.selected_badge = QLabel("✓ SELECTED")
        self.selected_badge.setProperty("selectedBadge", True)
        self.selected_badge.setVisible(False)
        h.addWidget(self.idx_lbl)
        h.addStretch(1)
        h.addWidget(self.selected_badge)

        # Canvas — takes most of the card
        body = QFrame(self); body.setObjectName("ConnCardBody")
        body_v = QVBoxLayout(body)
        body_v.setContentsMargins(2, 2, 2, 2)
        self.canvas = AnimatedGraph(body)
        self.canvas.set_graph(graph)
        body_v.addWidget(self.canvas, 1)

        # Footer — room and edge counts
        footer = QFrame(self); footer.setObjectName("ConnCardFooter")
        f = QHBoxLayout(footer)
        f.setContentsMargins(8, 4, 8, 4); f.setSpacing(6)
        rooms_n = sum(1 for n in graph.get("nodes", [])
                      if not n.get("is_direction")
                      and n.get("type") != "outside")
        edges_n = len(graph.get("edges_ordered", []))
        self.rooms_lbl = QLabel()
        self.rooms_lbl.setTextFormat(Qt.RichText)
        self.rooms_lbl.setProperty("connFooter", True)
        self.rooms_lbl.setText(
            f"<b>{rooms_n}</b> <span style='color:gray'>rooms</span>")
        self.edges_lbl = QLabel()
        self.edges_lbl.setTextFormat(Qt.RichText)
        self.edges_lbl.setProperty("connFooter", True)
        self.edges_lbl.setText(
            f"<b>{edges_n}</b> <span style='color:gray'>edges</span>")
        f.addWidget(self.rooms_lbl)
        relocs = graph.get("relocations", []) or []
        if relocs:
            self.moved_lbl = QLabel()
            self.moved_lbl.setTextFormat(Qt.RichText)
            self.moved_lbl.setProperty("connFooter", True)
            self.moved_lbl.setText(
                f"<b>{len(relocs)}</b> <span style='color:gray'>moved</span>")
            self.moved_lbl.setToolTip(
                "Secondary rooms moved next to the primary room they connect to:\n"
                + "\n".join(d["reason"] for d in relocs))
            f.addWidget(self.moved_lbl)
        f.addStretch(1)
        f.addWidget(self.edges_lbl)

        # Root layout
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(header)
        root.addWidget(body, 1)
        root.addWidget(footer)

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        _set_prop(self, "connCardSelected", selected)
        self.selected_badge.setVisible(selected)

    def set_anim_speed_ms(self, ms: int) -> None:
        self.canvas.set_anim_speed_ms(ms)

    def replay(self) -> None:
        self.canvas.replay()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.plan_index)
        super().mousePressEvent(e)


# ─────────────────────────────────────────────────────────────────────
#  ConnectivityStage — the main panel
# ─────────────────────────────────────────────────────────────────────

class ConnectivityStage(QWidget):
    """Stage 3 — connectivity graphs panel."""

    proceedRequested = Signal()  # user clicked "Proceed to Schematic →"

    def __init__(self, store: SessionStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self._thumbs: list[_GraphThumbnail] = []

        # Restore last-used animation speed
        settings = QSettings("ARML", "LayoutGenerator")
        try:
            self._speed_ms = int(settings.value(SPEED_KEY, DEFAULT_SPEED))
        except (TypeError, ValueError):
            self._speed_ms = DEFAULT_SPEED
        self._speed_ms = max(MIN_SPEED, min(MAX_SPEED, self._speed_ms))

        # ── Layout shell ──────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.content = QFrame(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(12, 12, 12, 8)
        self.content_layout.setSpacing(8)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll, 1)

        self.toolbar = self._build_toolbar()
        root.addWidget(self.toolbar)

        # Hooks
        self.store.stage2Changed.connect(self._refresh)
        self.store.stage3Changed.connect(self._refresh)
        self.store.themeChanged.connect(self._on_theme_changed)
        self._refresh()

    # ───────────────────────────────────────────────────────────────
    # Public API
    # ───────────────────────────────────────────────────────────────
    def regenerate(self) -> None:
        """Generate one graph per selected zone-assignment plan.

        Tries the real EdgeARModel first; falls back to the mock if
        PyTorch isn't installed, no checkpoint is on disk, or the
        checkpoint's state-dict doesn't match. The ``mocked`` flag
        on stage3 state reflects which one ran.
        """
        st2 = self.store.stage2_zones
        if not st2.tables or not st2.selected_idxs:
            return

        pg = self.store.inputs.plan_geometry
        plan_w = float(getattr(pg, "width", 1500.0))
        plan_h = float(getattr(pg, "height", 2000.0))

        graphs: list[dict] = []
        used_mock: bool = True
        load_error: str | None = None
        try:
            graphs = generate_connectivity_graphs(
                st2.tables, st2.selected_idxs,
                plan_width=plan_w, plan_height=plan_h,
                room_counts=dict(self.store.inputs.room_counts))
            used_mock = False
        except ConnectivityModelNotAvailable as e:
            load_error = str(e)
        except Exception as e:
            import traceback
            print(f"[stage3] connectivity model raised:\n"
                  f"{traceback.format_exc()}")
            load_error = f"Connectivity model raised: {e}"

        if used_mock:
            graphs = mock_connectivity_graphs(
                st2.tables, st2.selected_idxs,
                plan_width=plan_w, plan_height=plan_h)
            if load_error:
                print(f"[stage3] using mock — {load_error}")

        # Pre-select all so the user can deselect rather than re-pick.
        self.store.set_stage3_connectivity(
            graphs=graphs,
            selected_idxs=[int(g["meta"]["plan_index"]) for g in graphs],
            mocked=used_mock,
        )

    # ───────────────────────────────────────────────────────────────
    # Rendering
    # ───────────────────────────────────────────────────────────────
    def _clear_content(self) -> None:
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._thumbs = []

    def _refresh(self) -> None:
        self._clear_content()
        st2 = self.store.stage2_zones
        st3 = self.store.stage3_conn

        if not st2.selected_idxs:
            self.content_layout.addWidget(self._build_empty(
                "Complete Stage 2 (select one or more zone-assignment "
                "plans) to generate connectivity graphs."))
            self.content_layout.addStretch(1)
        elif not st3.graphs:
            self.content_layout.addWidget(self._build_cta(len(st2.selected_idxs)))
            self.content_layout.addStretch(1)
        else:
            self.content_layout.addWidget(self._build_grid(st3.graphs))
            self.content_layout.addStretch(1)

        self._refresh_toolbar()

    def _build_empty(self, msg: str) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignCenter)
        v.setContentsMargins(20, 80, 20, 20)
        v.setSpacing(4)
        title = QLabel("Waiting for zone selection")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 13px; font-weight: 600;")
        sub = QLabel(msg)
        sub.setAlignment(Qt.AlignCenter)
        sub.setProperty("sectionSubtitle", True)
        sub.setWordWrap(True)
        sub.setMaximumWidth(420)
        v.addWidget(title); v.addWidget(sub)
        return w

    def _build_cta(self, n: int) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignCenter)
        v.setContentsMargins(20, 60, 20, 20)
        v.setSpacing(6)
        title = QLabel("Ready to generate connectivity")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 13px; font-weight: 600;")
        sub = QLabel(
            f"The connectivity model will produce a graph for each of the "
            f"{n} selected zone-assignment plan{'s' if n != 1 else ''}. "
            "Primary rooms stay pinned at their zone positions; "
            "non-primary rooms settle based on edges.")
        sub.setAlignment(Qt.AlignCenter)
        sub.setProperty("sectionSubtitle", True)
        sub.setWordWrap(True)
        sub.setMaximumWidth(440)
        btn = QPushButton(f"Generate {n} graph{'s' if n != 1 else ''}")
        btn.setProperty("pillRole", "primary")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(self.regenerate)
        wrap = QHBoxLayout()
        wrap.addStretch(1); wrap.addWidget(btn); wrap.addStretch(1)
        v.addWidget(title); v.addWidget(sub); v.addSpacing(6)
        v.addLayout(wrap)
        return w

    def _build_grid(self, graphs: List[dict]) -> QWidget:
        wrap = QFrame()
        grid = QGridLayout(wrap)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        sel = set(self.store.stage3_conn.selected_idxs)
        for i, g in enumerate(graphs):
            card = _GraphThumbnail(g, wrap)
            card.set_selected(card.plan_index in sel)
            card.set_anim_speed_ms(self._speed_ms)
            card.clicked.connect(self._toggle_select)
            self._thumbs.append(card)
            grid.addWidget(card, i // 4, i % 4)
        for c in range(4):
            grid.setColumnStretch(c, 1)
        return wrap

    # ───────────────────────────────────────────────────────────────
    # Toolbar
    # ───────────────────────────────────────────────────────────────
    def _build_toolbar(self) -> QFrame:
        tb = QFrame(self); tb.setObjectName("ConnToolbar")
        lay = QHBoxLayout(tb)
        lay.setContentsMargins(14, 8, 14, 8); lay.setSpacing(8)

        self.btn_regenerate = QPushButton("Regenerate")
        self.btn_regenerate.setProperty("linkBtn", True)
        self.btn_regenerate.setCursor(Qt.PointingHandCursor)
        self.btn_regenerate.clicked.connect(self.regenerate)

        # Animation speed input
        speed_lbl = QLabel("Anim speed")
        speed_lbl.setProperty("sectionSubtitle", True)
        self.spin_speed = QSpinBox()
        self.spin_speed.setRange(MIN_SPEED, MAX_SPEED)
        self.spin_speed.setSingleStep(5)
        self.spin_speed.setValue(self._speed_ms)
        self.spin_speed.setSuffix(" ms")
        self.spin_speed.setFixedWidth(78)
        self.spin_speed.setAlignment(Qt.AlignRight)
        self.spin_speed.valueChanged.connect(self._on_speed_changed)

        self.btn_replay = QPushButton("↻ Replay")
        self.btn_replay.setProperty("linkBtn", True)
        self.btn_replay.setCursor(Qt.PointingHandCursor)
        self.btn_replay.clicked.connect(self._replay_all)

        self.btn_select_all = QPushButton("■ Select all")
        self.btn_select_all.setProperty("toggleChip", True)
        self.btn_select_all.setProperty("toggleChipActive", False)
        self.btn_select_all.setCursor(Qt.PointingHandCursor)
        self.btn_select_all.clicked.connect(self._toggle_select_all)

        self.btn_save_batch = QPushButton("Save Batch")
        self.btn_save_batch.setProperty("linkBtn", True)
        self.btn_save_batch.setCursor(Qt.PointingHandCursor)
        self.btn_save_batch.clicked.connect(self._on_save_batch)

        self.mock_pill = QLabel("Using mock data — backend unreachable or disabled")
        self.mock_pill.setProperty("mockPill", True)
        self.mock_pill.setVisible(False)

        self.sel_label = QLabel("Click graphs to select")
        self.sel_label.setProperty("sectionSubtitle", True)

        self.btn_proceed = QPushButton("Proceed to Schematic  →")
        self.btn_proceed.setProperty("pillRole", "primary")
        self.btn_proceed.setCursor(Qt.PointingHandCursor)
        self.btn_proceed.setEnabled(False)
        self.btn_proceed.clicked.connect(self._on_proceed)

        lay.addWidget(self.btn_regenerate)
        lay.addWidget(speed_lbl)
        lay.addWidget(self.spin_speed)
        lay.addWidget(self.btn_replay)
        lay.addWidget(self.btn_select_all)
        lay.addWidget(self.btn_save_batch)
        lay.addStretch(1)
        lay.addWidget(self.mock_pill)
        lay.addStretch(1)
        lay.addWidget(self.sel_label)
        lay.addWidget(self.btn_proceed)
        return tb

    def _refresh_toolbar(self) -> None:
        st3 = self.store.stage3_conn
        has_graphs = bool(st3.graphs)
        n_sel = len(st3.selected_idxs)
        self.mock_pill.setVisible(has_graphs and st3.mocked)
        self.spin_speed.setEnabled(has_graphs)
        self.btn_replay.setEnabled(has_graphs and self._speed_ms > 0)
        self.btn_select_all.setEnabled(has_graphs)
        self.btn_regenerate.setEnabled(
            bool(self.store.stage2_zones.selected_idxs))

        if not has_graphs:
            self.sel_label.setText("")
            self.btn_proceed.setEnabled(False)
        elif n_sel == 0:
            self.sel_label.setText("Click graphs to select")
            self.btn_proceed.setEnabled(False)
        elif n_sel == 1:
            self.sel_label.setText("1 graph selected")
            self.btn_proceed.setEnabled(True)
        else:
            self.sel_label.setText(f"{n_sel} graphs selected")
            self.btn_proceed.setEnabled(True)

        all_sel = has_graphs and n_sel == len(st3.graphs)
        _set_prop(self.btn_select_all, "toggleChipActive", all_sel)
        self.btn_select_all.setText(
            "■ Select none" if all_sel else "■ Select all")

    # ───────────────────────────────────────────────────────────────
    # Slots
    # ───────────────────────────────────────────────────────────────
    def _toggle_select(self, plan_idx: int) -> None:
        st = self.store.stage3_conn
        sel = set(st.selected_idxs)
        if plan_idx in sel:
            sel.remove(plan_idx)
        else:
            sel.add(plan_idx)
        new = sorted(sel)
        self.store.set_stage3_selection(new)
        for t in self._thumbs:
            t.set_selected(t.plan_index in sel)
        self._refresh_toolbar()

    def _toggle_select_all(self) -> None:
        st = self.store.stage3_conn
        if len(st.selected_idxs) == len(st.graphs):
            new: list[int] = []
        else:
            new = [int(g["meta"]["plan_index"]) for g in st.graphs]
        self.store.set_stage3_selection(new)
        sel = set(new)
        for t in self._thumbs:
            t.set_selected(t.plan_index in sel)
        self._refresh_toolbar()

    def _on_speed_changed(self, v: int) -> None:
        self._speed_ms = int(v)
        QSettings("ARML", "LayoutGenerator").setValue(
            SPEED_KEY, self._speed_ms)
        for t in self._thumbs:
            t.set_anim_speed_ms(self._speed_ms)
        self.btn_replay.setEnabled(self._speed_ms > 0)

    def _replay_all(self) -> None:
        for t in self._thumbs:
            t.replay()

    def _on_save_batch(self) -> None:
        """Render all connectivity graphs into one US-Letter-width PNG
        contact sheet (no grid, no compass, no card borders)."""
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        st3 = self.store.stage3_conn
        graphs = list(st3.graphs or [])
        if not graphs:
            QMessageBox.information(
                self, "Nothing to export",
                "Generate connectivity graphs before saving a batch sheet.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Save connectivity contact sheet",
            "connectivity_contact_sheet.png",
            "PNG image (*.png)")
        if not path:
            return

        try:
            theme = self.store.ui.theme
        except Exception:
            theme = "offwhite"

        ok = save_connectivity_png(graphs, path, theme=theme)
        if ok:
            QMessageBox.information(
                self, "Saved",
                f"Connectivity contact sheet written to:\n{path}")
        else:
            QMessageBox.warning(
                self, "Export failed",
                "Could not write the connectivity contact sheet.")

    def _on_proceed(self) -> None:
        self.store.set_active_stage("schematic")
        self.proceedRequested.emit()

    def _on_theme_changed(self, _name: str) -> None:
        for t in self._thumbs:
            t.canvas.update()
