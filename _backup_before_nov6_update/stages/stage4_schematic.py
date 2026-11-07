"""
Stage 4 — Schematic Design.

Port of ``SchematicStage.jsx`` + ``SchematicGrid.jsx`` + ``SchematicThumbnail.jsx``.

Layout::

    ┌─────────────────────────────────────────────────────────────┐
    │  4 × N grid of schematic cards (320 px row height)           │
    │  Each card: #NN header + schematic canvas + Q/A/SPL/rooms    │
    ├─────────────────────────────────────────────────────────────┤
    │  AQI × QVCI Pareto front (kept from previous version)        │
    ├─────────────────────────────────────────────────────────────┤
    │  Regenerate | [Bubble|Rectangle] | ALIGN N S E W C |          │
    │   ● Show edges | ■ Select all | ◆ Select Pareto-dominant     │
    │           [mock pill]               N plans selected         │
    └─────────────────────────────────────────────────────────────┘

Stage-level controls persist in ``QSettings``:
  - ``ui.schematicMode``       — "bubble" | "rectangle" (default "bubble")
  - ``ui.schematicAlignment``  — N/S/E/W/C (default "N")
  - ``ui.schematicShowEdges``  — bool (default True)
"""

from __future__ import annotations

from typing import List

from PySide6.QtCore import QSettings, Qt, Signal, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QDoubleSpinBox,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from app import theme as theme_mod
from app.state import SessionStore
from app.widgets.pareto_view import ParetoView
from app.widgets.schematic_canvas import SchematicCanvas
from app.widgets.batch_export import (
    save_batch_png, rank_by_target, get_targets, get_target_params)
from models.schematic_mock import mock_schematics, pareto_front_indices


MODE_KEY      = "ui.schematicMode2"
ALIGN_KEY     = "ui.schematicAlignment"
EDGES_KEY     = "ui.schematicShowEdges"
QV_KEY        = "ui.qvThreshold"
DEFAULT_MODE  = "rectangle"
DEFAULT_ALIGN = "N"
DEFAULT_QV    = 0.10
ALIGNMENTS    = ["N", "S", "E", "W", "C"]


def recompute_qvci(schematic: dict, threshold: float = 0.0) -> float:
    """Journal version: QVCI is the area-weighted mean of the per-room view
    scores over the regularly occupied rooms (no threshold), computed once
    by the zone stage. This shim just returns the stored value so older
    callers keep working."""
    return float((schematic.get("computed", {}) or {}).get("qvci", 0.0))


def _set_prop(widget: QWidget, name: str, value) -> None:
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


# ─────────────────────────────────────────────────────────────────────
#  _SchematicThumbnail
# ─────────────────────────────────────────────────────────────────────

class _SchematicThumbnail(QFrame):
    clicked       = Signal(int)
    doubleClicked = Signal(int)
    roomClicked   = Signal(int, dict)   # (plan_index, room_dict)

    def __init__(self, schematic: dict, mode: str, alignment: str,
                 show_edges: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.plan_index = int(schematic["plan_index"])
        self.schematic  = schematic
        self._selected = False

        self.setProperty("schemCard", True)
        self.setProperty("schemCardSelected", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setFixedHeight(320)
        self.setMinimumWidth(190)

        # Header
        header = QFrame(self); header.setObjectName("SchemCardHeader")
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

        # Body — canvas
        body = QFrame(self); body.setObjectName("SchemCardBody")
        bv = QVBoxLayout(body); bv.setContentsMargins(4, 4, 4, 4)
        self.canvas = SchematicCanvas(body)
        self.canvas.set_schematic(schematic)
        self.canvas.set_mode(mode)
        self.canvas.set_alignment(alignment)
        self.canvas.set_show_edges(show_edges)
        # Forward room clicks up to the stage panel (which positions a
        # tooltip popup at the click site).
        self.canvas.roomClicked.connect(
            lambda room: self.roomClicked.emit(self.plan_index, room))
        bv.addWidget(self.canvas, 1)

        # Footer — Q / A / SPL / N rooms
        footer = QFrame(self); footer.setObjectName("SchemCardFooter")
        f = QHBoxLayout(footer)
        f.setContentsMargins(8, 4, 8, 4); f.setSpacing(10)
        m = schematic.get("computed", {})
        qv_wrap, self._q_val = self._metric_with_val(
            "Q", f"{m.get('qvci', 0):.2f}", accent=True)
        f.addWidget(qv_wrap)
        f.addWidget(self._make_metric("A", f"{m.get('aqi', 0):.2f}",
                                        accent=True))
        f.addWidget(self._make_metric("SPL", f"{m.get('avg_spl_dB', 0):.0f}",
                                        suffix="dB(A)"))
        f.addStretch(1)
        n_rooms = len(schematic.get("rooms", []))
        rooms_lbl = QLabel(f"{n_rooms} rooms")
        rooms_lbl.setProperty("schemRoomsLbl", True)
        f.addWidget(rooms_lbl)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        root.addWidget(header)
        root.addWidget(body, 1)
        root.addWidget(footer)

    @staticmethod
    def _metric_with_val(label: str, value: str,
                         accent: bool = False, suffix: str = ""):
        """Like _make_metric but also returns the value QLabel so the
        caller can update it later (used for the live QVCI readout)."""
        wrap = QFrame()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0); h.setSpacing(2)
        lbl = QLabel(label)
        lbl.setProperty("schemMetricLbl", True)
        val = QLabel(value)
        val.setProperty("schemMetricVal", True)
        val.setProperty("schemMetricAccent", bool(accent))
        h.addWidget(lbl)
        h.addWidget(val)
        if suffix:
            suf = QLabel(suffix)
            suf.setProperty("schemMetricLbl", True)
            h.addWidget(suf)
        return wrap, val

    @staticmethod
    def _make_metric(label: str, value: str,
                     accent: bool = False, suffix: str = "") -> QWidget:
        wrap, _ = _SchematicThumbnail._metric_with_val(
            label, value, accent, suffix)
        return wrap

    def set_qv_threshold(self, threshold: float) -> None:
        """Recompute QVCI at the given threshold, store it on the
        schematic, and update the footer readout."""
        q = recompute_qvci(self.schematic, threshold)
        self.schematic.setdefault("computed", {})["qvci"] = q
        self._q_val.setText(f"{q:.2f}")

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        _set_prop(self, "schemCardSelected", selected)
        self.selected_badge.setVisible(selected)

    def set_mode(self, m: str) -> None:
        self.canvas.set_mode(m)

    def set_alignment(self, a: str) -> None:
        self.canvas.set_alignment(a)

    def set_show_edges(self, on: bool) -> None:
        self.canvas.set_show_edges(on)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.plan_index)
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self.doubleClicked.emit(self.plan_index)
        super().mouseDoubleClickEvent(e)


# ─────────────────────────────────────────────────────────────────────
#  _RoomTooltip — popup card shown on room-click
# ─────────────────────────────────────────────────────────────────────

class _RoomTooltip(QFrame):
    """Floating tooltip card with one room's properties.

    Matches the web app's design (see screenshot): room id + type tag
    at the top, dismissable with the ✕ button, then a small two-column
    label/value table for zone, area, aspect ratio, WWR, then an
    achievement panel for quality view + acoustics.
    """

    closeRequested = Signal()

    def __init__(self, room: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("roomTooltip", True)
        self.setObjectName("RoomTooltip")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumWidth(220)

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 10); v.setSpacing(6)

        # Header: room_id · ROOM_TYPE_UPPER · ✕
        header = QFrame(self); hh = QHBoxLayout(header)
        hh.setContentsMargins(0, 0, 0, 0); hh.setSpacing(6)
        rid = QLabel(room.get("room_id", ""))
        rid.setStyleSheet(
            "font-family: 'JetBrains Mono', Consolas, monospace; "
            "font-size: 12px; font-weight: 600;")
        rt = QLabel(room.get("room_type", "").upper())
        rt.setProperty("sectionSubtitle", True)
        rt.setStyleSheet("font-size: 9px; letter-spacing: 0.5px;")
        close = QPushButton("✕"); close.setProperty("iconBtn", True)
        close.setFixedSize(20, 20); close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.closeRequested.emit)
        hh.addWidget(rid); hh.addStretch(1); hh.addWidget(rt); hh.addWidget(close)
        v.addWidget(header)

        # Divider
        div = QFrame(); div.setFrameShape(QFrame.HLine)
        div.setStyleSheet("background:rgba(0,0,0,0.10); max-height:1px;")
        v.addWidget(div)

        # Properties — two columns: label (left, dim) | value (right, mono)
        zone_raw = room.get("zone_assignment")
        if zone_raw is None:
            zone_txt = "not zoned"
        else:
            zone_idx = int(zone_raw)
            zone_txt = f"{_ZONE_LABEL_SHORT.get(zone_idx, str(zone_idx))} (#{zone_idx + 1})"
        area_val = float(room.get("area", 0.0))
        area_m2  = room.get("area_m2")
        ar       = float(room.get("aspect_ratio", 1.0) or 1.0)
        wwr      = float(room.get("wwr", 0.0) or 0.0)
        v.addWidget(self._prop_row("Zone", zone_txt))
        v.addWidget(self._prop_row("Area", f"{float(area_m2):.1f} m²" if area_m2 is not None
                                   else f"{area_val:,.1f}"))
        v.addWidget(self._prop_row("Aspect ratio", f"{ar:.2f}"))
        v.addWidget(self._prop_row("Planned WWR", f"{wwr * 100:.0f}%"))

        # Scores panel
        div2 = QFrame(); div2.setFrameShape(QFrame.HLine)
        div2.setStyleSheet("background:rgba(0,0,0,0.10); max-height:1px;")
        v.addWidget(div2)

        qv_score = room.get("quality_view_score", None)
        aq_score = room.get("acoustic_score", None)
        spl      = room.get("est_avg_spl_dB", None)
        guide    = room.get("guideline_dB", None)
        ac_ok    = room.get("acoustic_ok", None)
        weighted = bool(room.get("scored", False))
        if qv_score is not None:
            v.addWidget(self._prop_row("View score q_i", f"{float(qv_score):.3f}"))
        if aq_score is not None:
            v.addWidget(self._prop_row("Acoustic score a_i", f"{float(aq_score):.3f}"))
        if spl is not None:
            v.addWidget(self._prop_row("Indoor level", f"{float(spl):.1f} dB(A)"
                                       + (f"  (guideline {float(guide):.0f})" if guide else "")))
        if ac_ok is not None:
            v.addWidget(self._achievement_row("Acoustic guideline", bool(ac_ok),
                                                "Met", "Exceeded"))
        if zone_raw is not None:
            v.addWidget(self._prop_row("Index weight",
                                       f"{float(room.get('weight', 0.0)):.3f}" if weighted
                                       else "0 (not a regularly occupied room)"))

    def _prop_row(self, label: str, value: str) -> QFrame:
        row = QFrame(); h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
        lbl = QLabel(label); lbl.setStyleSheet(
            "font-size: 10px; color: rgba(0,0,0,0.55);")
        val = QLabel(value); val.setAlignment(Qt.AlignRight)
        val.setStyleSheet(
            "font-family: 'JetBrains Mono', Consolas, monospace; "
            "font-size: 10px; font-weight: 600;")
        h.addWidget(lbl); h.addStretch(1); h.addWidget(val)
        return row

    def _achievement_row(self, label: str, ok: bool,
                          ok_text: str, fail_text: str) -> QFrame:
        row = QFrame(); h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
        lbl = QLabel(label); lbl.setStyleSheet(
            "font-size: 10px; color: rgba(0,0,0,0.55);")
        chip = QLabel(("✓ " + ok_text) if ok else ("✗ " + fail_text))
        chip.setProperty("achChip", "pass" if ok else "fail")
        chip.setStyleSheet(
            "font-size: 10px; font-weight: 600; padding: 1px 6px; "
            "border-radius: 6px; " +
            ("background: rgba(229, 78, 80, 0.12); color: rgb(192, 65, 63);"
             if ok else
             "color: rgba(0,0,0,0.45); text-decoration: line-through;"))
        h.addWidget(lbl); h.addStretch(1); h.addWidget(chip)
        return row


_ZONE_LABEL_SHORT = {
    0: "NW", 1: "N",  2: "NE",
    3: "W",  4: "C",  5: "E",
    6: "SW", 7: "S",  8: "SE",
}


# ─────────────────────────────────────────────────────────────────────
#  _SchematicDetailView — opened on double-click of a card
# ─────────────────────────────────────────────────────────────────────

class _SchematicDetailView(QFrame):
    """Per-plan schematic detail panel (big canvas + side panel).

    Header: PLAN #NN · X rooms · Y edges  · [select] · [✕]
    Left:   large SchematicCanvas
    Right:  PLAN METRICS block + ROOMS (N) table
    """

    closeRequested = Signal()
    selectToggled  = Signal(int)   # plan_index

    def __init__(self, schematic: dict, is_selected: bool,
                 mode: str, alignment: str, show_edges: bool,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.schematic = schematic
        self._is_selected = bool(is_selected)
        self.setProperty("schemDetail", True)
        self.setObjectName("SchemDetail")

        idx     = int(schematic["plan_index"])
        rooms   = schematic.get("rooms", []) or []
        n_rooms = len(rooms)
        n_edges = len(schematic.get("edges", []) or [])

        # ── Header ─────────────────────────────────────────────────
        header = QFrame(self); header.setObjectName("SchemDetailHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(14, 8, 10, 8); h.setSpacing(8)
        tag = QLabel("PLAN"); tag.setProperty("sectionHeader", True)
        idx_lbl = QLabel(f"#{idx:02d}")
        idx_lbl.setStyleSheet("font-size: 14px; font-weight: 600;")
        meta = QLabel(f"·  {n_rooms} rooms · {n_edges} edges")
        meta.setProperty("sectionSubtitle", True)
        h.addWidget(tag); h.addWidget(idx_lbl); h.addWidget(meta); h.addStretch(1)

        self.btn_select = QPushButton(
            "✓ Selected (click to remove)" if is_selected
            else "Add to selection")
        self.btn_select.setProperty(
            "pillRole", "primary" if is_selected else "ghost")
        self.btn_select.setCursor(Qt.PointingHandCursor)
        self.btn_select.clicked.connect(
            lambda: self.selectToggled.emit(idx))
        h.addWidget(self.btn_select)

        btn_close = QPushButton("✕")
        btn_close.setProperty("iconBtn", True)
        btn_close.setFixedSize(28, 28)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setToolTip("Close detail (Esc)")
        btn_close.clicked.connect(self.closeRequested.emit)
        h.addWidget(btn_close)

        # ── Body — two columns ─────────────────────────────────────
        body = QFrame(self)
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(14, 10, 14, 14); body_lay.setSpacing(14)

        # Left: large canvas
        self.canvas = SchematicCanvas(body)
        self.canvas.set_schematic(schematic)
        self.canvas.set_mode(mode)
        self.canvas.set_alignment(alignment)
        self.canvas.set_show_edges(show_edges)
        self.canvas.set_show_labels(True)   # always labelled in detail
        self.canvas.setMinimumSize(320, 320)
        self.canvas.setMaximumSize(520, 520)
        # Forward room clicks so the stage panel can show a tooltip
        self.canvas.roomClicked.connect(self._on_canvas_room_clicked)
        body_lay.addWidget(self.canvas, 3)

        # Right: side panel
        side = QFrame(body); side.setObjectName("SchemDetailSide")
        side.setMinimumWidth(300); side.setMaximumWidth(420)
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 0, 0); side_lay.setSpacing(0)

        # Plan metrics block
        metrics_sec = QFrame(side); metrics_sec.setObjectName("SchemDetailSection")
        ms = QVBoxLayout(metrics_sec)
        ms.setContentsMargins(12, 10, 12, 10); ms.setSpacing(6)
        ms.addWidget(self._section_title("PLAN METRICS"))
        c = schematic.get("computed", {}) or {}
        ms.addWidget(self._metric_bar_row("QVCI",
                                            float(c.get("qvci", 0.0))))
        ms.addWidget(self._metric_bar_row("AQI",
                                            float(c.get("aqi", 0.0))))
        if c.get("d_target") is not None:
            ms.addWidget(self._metric_value_row(
                "Distance to target", f"{float(c['d_target']):.3f}"))
        ms.addWidget(self._metric_value_row(
            "Avg indoor level", f"{float(c.get('avg_spl_dB', 0.0)):.1f} dB(A)"))
        ms.addWidget(self._metric_value_row(
            "Zone spread", f"{int(c.get('zone_spread', 0))} / 9"))
        side_lay.addWidget(metrics_sec)

        # Rooms table
        rooms_sec = QFrame(side); rooms_sec.setObjectName("SchemDetailSection")
        rs = QVBoxLayout(rooms_sec)
        rs.setContentsMargins(12, 10, 12, 10); rs.setSpacing(4)
        rs.addWidget(self._section_title(f"ROOMS ({n_rooms})"))
        hint = QLabel("Click a row or room to see details")
        hint.setProperty("sectionSubtitle", True)
        rs.addWidget(hint)
        # Column header
        col_hdr = QFrame(); ch = QHBoxLayout(col_hdr)
        ch.setContentsMargins(0, 0, 0, 0); ch.setSpacing(6)
        for txt, stretch, align in (
                ("Room", 4, Qt.AlignLeft),
                ("m²",   2, Qt.AlignRight),
                ("Zone", 1, Qt.AlignCenter),
                ("WWR",  1, Qt.AlignRight),
                ("QV",   1, Qt.AlignRight),
                ("AQ",   1, Qt.AlignRight),
                ("dB(A)", 1, Qt.AlignRight)):
            lbl = QLabel(txt); lbl.setProperty("tableHeader", True)
            lbl.setAlignment(align | Qt.AlignVCenter)
            ch.addWidget(lbl, stretch)
        rs.addWidget(col_hdr)
        for r in rooms:
            rs.addWidget(self._room_row(r))
        rs.addStretch(1)
        rooms_scroll = QScrollArea(side)
        rooms_scroll.setWidgetResizable(True)
        rooms_scroll.setFrameShape(QFrame.NoFrame)
        rooms_scroll.setWidget(rooms_sec)
        side_lay.addWidget(rooms_scroll, 1)

        body_lay.addWidget(side, 2)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        root.addWidget(header)
        root.addWidget(body, 1)

        # State for the in-detail tooltip
        self._tooltip: _RoomTooltip | None = None

    def set_mode(self, m: str) -> None:
        self.canvas.set_mode(m)

    def set_alignment(self, a: str) -> None:
        self.canvas.set_alignment(a)

    def set_show_edges(self, on: bool) -> None:
        self.canvas.set_show_edges(on)

    def set_selected(self, is_selected: bool) -> None:
        self._is_selected = bool(is_selected)
        self.btn_select.setText(
            "✓ Selected (click to remove)" if is_selected
            else "Add to selection")
        self.btn_select.setProperty(
            "pillRole", "primary" if is_selected else "ghost")
        self.btn_select.style().unpolish(self.btn_select)
        self.btn_select.style().polish(self.btn_select)

    def _on_canvas_room_clicked(self, room: dict) -> None:
        if self._tooltip is not None:
            self._tooltip.setParent(None)
            self._tooltip.deleteLater()
        self._tooltip = _RoomTooltip(room, self)
        self._tooltip.closeRequested.connect(self._dismiss_tooltip)
        # Position near the clicked room (use canvas centre as a
        # reasonable default — the click coord isn't routed up).
        cw = self.canvas.geometry()
        tx = cw.x() + 30
        ty = cw.y() + 30
        self._tooltip.move(tx, ty)
        self._tooltip.adjustSize()
        self._tooltip.show()
        self._tooltip.raise_()

    def _dismiss_tooltip(self) -> None:
        if self._tooltip is None:
            return
        self._tooltip.setParent(None)
        self._tooltip.deleteLater()
        self._tooltip = None

    # ── row builders (same style as Stage 2 detail) ──────────────
    def _section_title(self, txt: str) -> QLabel:
        lbl = QLabel(txt); lbl.setProperty("sectionHeader", True)
        return lbl

    def _metric_bar_row(self, name: str, value: float) -> QWidget:
        row = QFrame()
        v = QVBoxLayout(row); v.setContentsMargins(0, 2, 0, 2); v.setSpacing(2)
        top = QFrame(); th = QHBoxLayout(top)
        th.setContentsMargins(0, 0, 0, 0); th.setSpacing(0)
        nl = QLabel(name); nl.setStyleSheet("font-size: 11px;")
        vl = QLabel(f"{value:.3f}")
        vl.setStyleSheet(
            "font-family: 'JetBrains Mono', Consolas, monospace; "
            "font-size: 11px; font-weight: 600;")
        vl.setAlignment(Qt.AlignRight)
        th.addWidget(nl); th.addStretch(1); th.addWidget(vl)
        v.addWidget(top)
        bar = _MetricBar(value)
        v.addWidget(bar)
        return row

    def _metric_value_row(self, name: str, value_str: str) -> QWidget:
        row = QFrame(); h = QHBoxLayout(row)
        h.setContentsMargins(0, 2, 0, 2); h.setSpacing(0)
        nl = QLabel(name); nl.setStyleSheet("font-size: 11px;")
        vl = QLabel(value_str)
        vl.setStyleSheet(
            "font-family: 'JetBrains Mono', Consolas, monospace; "
            "font-size: 11px; font-weight: 600;")
        vl.setAlignment(Qt.AlignRight)
        h.addWidget(nl); h.addStretch(1); h.addWidget(vl)
        return row

    def _room_row(self, room: dict) -> QWidget:
        row = QFrame(); h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
        rt = room["room_type"]
        # Room cell with swatch
        cell = QFrame(); ch = QHBoxLayout(cell)
        ch.setContentsMargins(0, 0, 0, 0); ch.setSpacing(6)
        sw = QFrame(); sw.setFixedSize(8, 8)
        sw.setStyleSheet(
            f"background:{theme_mod.ROOM_COLORS.get(rt, '#9aa0a6')};"
            "border-radius:2px;")
        name = QLabel(room["room_id"].replace("_", " "))
        name.setStyleSheet(
            "font-family: 'JetBrains Mono', Consolas, monospace; "
            "font-size: 10px;")
        ch.addWidget(sw); ch.addWidget(name); ch.addStretch(1)
        # Numeric cells
        area_val = float(room.get("area_m2", room.get("area", 0.0)) or 0.0)
        zone_raw = room.get("zone_assignment")
        zone_txt = "—" if zone_raw is None else _ZONE_LABEL_SHORT.get(int(zone_raw), str(zone_raw))
        wwr_v    = float(room.get("wwr", 0.0) or 0.0)
        qv_raw   = room.get("quality_view_score", None)
        qv_v     = float(qv_raw) if qv_raw is not None else None
        weighted = bool(room.get("scored", False))
        qv_ok    = bool(weighted and qv_v is not None and qv_v >= 0.5)
        aq_raw   = room.get("acoustic_score", None)
        aq_ok    = bool(weighted and room.get("acoustic_ok", False))
        spl_v    = room.get("est_avg_spl_dB", None)
        def _num(txt: str, align, color: str | None = None):
            lbl = QLabel(txt)
            style = ("font-family: 'JetBrains Mono', Consolas, monospace; "
                     "font-size: 10px;")
            if color:
                style += f" color: {color};"
            lbl.setStyleSheet(style)
            lbl.setAlignment(align | Qt.AlignVCenter)
            return lbl
        # Highlight achieved QV in the accent rose so the eye picks up
        # which rooms actually crossed the threshold.
        accent_hex = theme_mod.PALETTES.get(
            self.window().store.ui.theme,
            theme_mod.PALETTES["offwhite"]).get("accent")
        accent_rgb = (f"rgb({accent_hex[0]},{accent_hex[1]},{accent_hex[2]})"
                       if isinstance(accent_hex, tuple) else None)
        qv_str = "—" if qv_v is None else (f"✓ {qv_v:.2f}" if qv_ok else f"{qv_v:.2f}")
        aq_str = "—" if aq_raw is None else (f"✓ {float(aq_raw):.2f}" if aq_ok else f"{float(aq_raw):.2f}")
        h.addWidget(cell, 4)
        h.addWidget(_num(f"{area_val:,.1f}", Qt.AlignRight), 2)
        h.addWidget(_num(zone_txt, Qt.AlignCenter), 1)
        h.addWidget(_num(f"{wwr_v * 100:.0f}%", Qt.AlignRight), 1)
        h.addWidget(_num(qv_str, Qt.AlignRight,
                          color=accent_rgb if qv_ok else None), 1)
        h.addWidget(_num(aq_str, Qt.AlignRight,
                          color=accent_rgb if aq_ok else None), 1)
        h.addWidget(_num(f"{int(round(float(spl_v)))}" if spl_v is not None
                          else "—", Qt.AlignRight), 1)
        return row

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            if self._tooltip is not None:
                self._dismiss_tooltip()
                return
            self.closeRequested.emit()
            return
        super().keyPressEvent(e)


class _MetricBar(QWidget):
    """Tiny 0..1 horizontal bar used in the metrics block."""

    def __init__(self, value: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = max(0.0, min(1.0, float(value)))
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, _e) -> None:
        from PySide6.QtGui import QPainter, QColor, QBrush, QPen
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = theme_mod.PALETTES.get(
            self.window().store.ui.theme
            if hasattr(self.window(), "store") else "offwhite",
            theme_mod.PALETTES["offwhite"])
        w, h = float(self.width()), float(self.height())
        bg = QColor(*pal["border"]); bg.setAlphaF(0.25)
        p.setBrush(QBrush(bg)); p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, int(w), int(h), 2, 2)
        fg = QColor(*pal["accent"])
        p.setBrush(QBrush(fg))
        p.drawRoundedRect(0, 0, int(w * self._value), int(h), 2, 2)
        p.end()


# ─────────────────────────────────────────────────────────────────────
#  SchematicStage
# ─────────────────────────────────────────────────────────────────────

class SchematicStage(QWidget):
    """Stage 4 — schematic designs + Pareto front."""

    exportRequested = Signal()

    def __init__(self, store: SessionStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self._thumbs: list[_SchematicThumbnail] = []
        self._detail: _SchematicDetailView | None = None
        self._grid_widget: QWidget | None = None
        self._tooltip: _RoomTooltip | None = None
        self._last_schematics_id: int | None = None

        # Restore stage-level display settings
        settings = QSettings("ARML", "LayoutGenerator")
        self._mode = settings.value(MODE_KEY, DEFAULT_MODE) or DEFAULT_MODE
        if self._mode not in ("bubble", "rectangle"):
            self._mode = DEFAULT_MODE
        self._alignment = settings.value(ALIGN_KEY, DEFAULT_ALIGN) or DEFAULT_ALIGN
        if self._alignment not in ALIGNMENTS:
            self._alignment = DEFAULT_ALIGN
        raw_edges = settings.value(EDGES_KEY, "true")
        self._show_edges = raw_edges in (True, "true", "True", "1", 1)
        # Journal version: QVCI is fixed by the index equation (no view
        # threshold); the thesis-era slider is gone.
        self._qv_threshold = None

        # ── Layout shell ─────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        self.content = QFrame(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(12, 12, 12, 8)
        self.content_layout.setSpacing(8)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll, 1)

        # Pareto strip (kept)
        self.pareto_wrap = self._build_pareto_section()
        self.pareto_wrap.setVisible(False)
        root.addWidget(self.pareto_wrap)

        # Toolbar
        self.toolbar = self._build_toolbar()
        root.addWidget(self.toolbar)

        # Signals
        self.store.stage3Changed.connect(self._refresh)
        self.store.stage4Changed.connect(self._refresh)
        self.store.themeChanged.connect(self._on_theme_changed)
        self._refresh()

    # ───────────────────────────────────────────────────────────────
    # Public API
    # ───────────────────────────────────────────────────────────────
    def regenerate(self) -> None:
        st2 = self.store.stage2_zones
        st3 = self.store.stage3_conn
        if not st3.graphs or not st3.selected_idxs:
            return
        schematics = mock_schematics(
            st2.tables, st3.graphs, st3.selected_idxs, self.store.inputs)

        # The mock packer computes its own view metrics, which DON'T match
        # the real zone_model pool (they saturate, so every plan lands at
        # ~the same QVCI). Re-link each schematic's plan-level metrics and
        # per-room quality_view_score back to the real Stage-2 table, so the
        # Pareto foreground aligns with the (real) background pool and the
        # QV-threshold recompute stays meaningful.
        _tbl_by_idx = {int(t.get("table_index", i)): t
                       for i, t in enumerate(st2.tables)}
        for s in schematics:
            real = _tbl_by_idx.get(int(s.get("plan_index", -1)))
            if real is None:
                continue
            rc = real.get("computed", {}) or {}
            comp = s.setdefault("computed", {})
            for k in ("qvci", "aqi", "concentration",
                      "avg_spl_dB", "zone_spread", "d_target", "valid"):
                if k in rc:
                    comp[k] = rc[k]
            for k in ("targets", "reachable", "capacity"):
                if k in real:
                    s[k] = real[k]
            by_room = {r.get("room_id"): r for r in real.get("rooms", [])}
            for r in s.get("rooms", []):
                src = by_room.get(r.get("room_id"))
                if src is None:
                    continue
                for k in ("quality_view_score", "quality_view_achieved", "acoustic_score",
                          "acoustic_ok", "est_avg_spl_dB", "guideline_dB", "area_m2",
                          "weight", "scored", "zoned", "wwr"):
                    if k in src:
                        r[k] = src[k]

        # Stage 4 packing is always algorithmic (not a trained model);
        # the "Derived from mocked upstream data" pill should only
        # appear when one of the *upstream* stages was mocked.
        upstream_mocked = bool(getattr(st3, "mocked", False))
        self.store.set_stage4_schematics(
            schematics=schematics,
            selected_idxs=[int(s["plan_index"]) for s in schematics],
            mocked=upstream_mocked,
        )

    # ───────────────────────────────────────────────────────────────
    # Pareto section
    # ───────────────────────────────────────────────────────────────
    def _build_pareto_section(self) -> QFrame:
        wrap = QFrame(self); wrap.setObjectName("ParetoWrap")
        v = QVBoxLayout(wrap); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)

        header = QFrame(wrap); header.setObjectName("ParetoHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(14, 5, 14, 5); h.setSpacing(8)
        title = QLabel("AQI × QVCI PARETO FRONT")
        title.setProperty("sectionHeader", True)
        meta = QLabel("· dominant designs highlighted")
        meta.setProperty("sectionSubtitle", True)
        h.addWidget(title); h.addWidget(meta); h.addStretch(1)

        # High-resolution PNG export button (right side of the header).
        # Opens a file dialog and renders the Pareto plot to a square
        # 2400-px image — comfortable for thesis figures (~4″ at 600 dpi).
        btn_save = QPushButton("Save image")
        btn_save.setProperty("pillRole", "ghost")
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_save.setToolTip(
            "Save the Pareto plot as a high-resolution PNG (2400 × 2400 px)")
        btn_save.clicked.connect(self._save_pareto_image)
        h.addWidget(btn_save)

        # CSV export — dumps every point (foreground + background) so the
        # user can plot in Excel / R / Python without re-running anything.
        btn_csv = QPushButton("Save CSV")
        btn_csv.setProperty("pillRole", "ghost")
        btn_csv.setCursor(Qt.PointingHandCursor)
        btn_csv.setToolTip(
            "Export all Pareto data points to CSV "
            "(foreground schematics + full background pool)")
        btn_csv.clicked.connect(self._save_pareto_csv)
        h.addWidget(btn_csv)

        v.addWidget(header)

        self.pareto = ParetoView(wrap)
        self.pareto.dotClicked.connect(self._on_pareto_click)
        v.addWidget(self.pareto)
        return wrap

    def _save_pareto_image(self) -> None:
        """Open a save dialog and write a high-resolution Pareto PNG."""
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Pareto plot",
            "pareto_front.png",
            "PNG image (*.png);;JPEG image (*.jpg *.jpeg)")
        if not path:
            return
        ok = self.pareto.save_image(path, size=2400)
        if not ok:
            QMessageBox.warning(
                self, "Save failed",
                f"Could not write image to:\n{path}")

    def _save_pareto_csv(self) -> None:
        """Open a save dialog and write a CSV of every Pareto point."""
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Pareto data",
            "pareto_front.csv",
            "CSV file (*.csv)")
        if not path:
            return
        ok = self.pareto.save_csv(path)
        if not ok:
            QMessageBox.warning(
                self, "Save failed",
                f"Could not write CSV to:\n{path}")

    # ───────────────────────────────────────────────────────────────
    # Rendering
    # ───────────────────────────────────────────────────────────────
    def _clear_content(self) -> None:
        # Tear down the tooltip / detail if visible — they reference
        # widgets we're about to delete.
        self._dismiss_tooltip()
        if self._detail is not None:
            self._detail.setParent(None)
            self._detail.deleteLater()
            self._detail = None
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._thumbs = []
        self._grid_widget = None

    def _refresh(self) -> None:
        st3 = self.store.stage3_conn
        st4 = self.store.stage4_sch

        # ── Smart-refresh: same schematics, only selection changed ──
        # Avoid rebuilding the entire grid (which would tear down
        # the detail view + tooltip + reset packing caches).
        if (st4.schematics and self._thumbs
                and id(st4.schematics) == self._last_schematics_id):
            sel = set(st4.selected_idxs)
            for t in self._thumbs:
                t.set_selected(t.plan_index in sel)
            if self._detail is not None:
                self._detail.set_selected(
                    self._detail.schematic["plan_index"] in sel)
            self.pareto.set_schematics(st4.schematics)
            self.pareto.set_background_points(self._bg_points())
            self.pareto.set_selected(st4.selected_idxs)
            self._refresh_toolbar()
            return

        # Full rebuild
        self._clear_content()

        if not st3.selected_idxs:
            self.content_layout.addWidget(self._build_empty(
                "Complete Stage 3 (select one or more connectivity "
                "graphs) to generate schematic designs."))
            self.content_layout.addStretch(1)
            self.pareto_wrap.setVisible(False)
        elif not st4.schematics:
            self.content_layout.addWidget(self._build_cta(
                len(st3.selected_idxs)))
            self.content_layout.addStretch(1)
            self.pareto_wrap.setVisible(False)
        else:
            self._grid_widget = self._build_grid(st4.schematics)
            self.content_layout.addWidget(self._grid_widget)
            self.content_layout.addStretch(1)
            self.pareto.set_schematics(st4.schematics)
            self.pareto.set_background_points(self._bg_points())
            self.pareto.set_selected(st4.selected_idxs)
            self.pareto_wrap.setVisible(True)
        self._last_schematics_id = (
            id(st4.schematics) if st4.schematics else None)
        self._refresh_toolbar()

    # ───────────────────────────────────────────────────────────────
    # Detail view (double-click → big card + side panel)
    # ───────────────────────────────────────────────────────────────
    def _open_detail(self, plan_idx: int) -> None:
        st4 = self.store.stage4_sch
        sch = next((s for s in st4.schematics
                    if int(s["plan_index"]) == int(plan_idx)), None)
        if sch is None:
            return
        # Close any existing detail first
        if self._detail is not None:
            self._close_detail()
        self._dismiss_tooltip()

        is_selected = int(plan_idx) in set(st4.selected_idxs)
        self._detail = _SchematicDetailView(
            sch, is_selected,
            self._mode, self._alignment, self._show_edges,
            self.content)
        self._detail.closeRequested.connect(self._close_detail)
        self._detail.selectToggled.connect(self._on_detail_select_toggle)

        # Replace the grid with the detail view (keep grid in memory)
        if self._grid_widget is not None:
            self._grid_widget.setVisible(False)
        self.content_layout.insertWidget(0, self._detail)
        self._detail.setFocus()

    def _close_detail(self) -> None:
        if self._detail is None:
            return
        self.content_layout.removeWidget(self._detail)
        self._detail.setParent(None)
        self._detail.deleteLater()
        self._detail = None
        if self._grid_widget is not None:
            self._grid_widget.setVisible(True)

    def _on_detail_select_toggle(self, plan_idx: int) -> None:
        # Same logic as _toggle_select — kept separate so the detail
        # view's button can wire through here.
        self._toggle_select(plan_idx)

    # ───────────────────────────────────────────────────────────────
    # Room tooltip (single-click on a room inside any card)
    # ───────────────────────────────────────────────────────────────
    def _show_room_tooltip(self, plan_idx: int, room: dict) -> None:
        # Always close any existing tooltip first
        self._dismiss_tooltip()

        # Find the source card to position the tooltip near
        card = next((t for t in self._thumbs
                     if t.plan_index == int(plan_idx)), None)
        if card is None:
            return

        self._tooltip = _RoomTooltip(room, self)
        self._tooltip.closeRequested.connect(self._dismiss_tooltip)

        # Anchor the tooltip at the canvas's top-left, in this stage's
        # coordinate space. Clamp to stay on-screen.
        canvas_tl = card.canvas.mapTo(self, card.canvas.rect().topLeft())
        tx = canvas_tl.x() + 10
        ty = canvas_tl.y() + 10
        self._tooltip.adjustSize()
        # Keep within self bounds
        tw = self._tooltip.width()
        th = self._tooltip.height()
        max_x = max(0, self.width()  - tw - 4)
        max_y = max(0, self.height() - th - 4)
        tx = min(tx, max_x); ty = min(ty, max_y)
        self._tooltip.move(tx, ty)
        self._tooltip.show()
        self._tooltip.raise_()

    def _dismiss_tooltip(self) -> None:
        if self._tooltip is None:
            return
        self._tooltip.setParent(None)
        self._tooltip.deleteLater()
        self._tooltip = None

    def _build_empty(self, msg: str) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w); v.setAlignment(Qt.AlignCenter)
        v.setContentsMargins(20, 80, 20, 20); v.setSpacing(4)
        title = QLabel("Waiting for connectivity selection")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 13px; font-weight: 600;")
        sub = QLabel(msg)
        sub.setAlignment(Qt.AlignCenter)
        sub.setProperty("sectionSubtitle", True)
        sub.setWordWrap(True); sub.setMaximumWidth(420)
        v.addWidget(title); v.addWidget(sub)
        return w

    def _build_cta(self, n: int) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w); v.setAlignment(Qt.AlignCenter)
        v.setContentsMargins(20, 60, 20, 20); v.setSpacing(6)
        title = QLabel("Ready to build schematics")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 13px; font-weight: 600;")
        sub = QLabel(
            f"Combine the selected zone assignments and connectivity "
            f"graphs into {n} schematic{'s' if n != 1 else ''}. Switch "
            "between bubble and rectangle view, adjust alignment, and "
            "compare side by side.")
        sub.setAlignment(Qt.AlignCenter)
        sub.setProperty("sectionSubtitle", True)
        sub.setWordWrap(True); sub.setMaximumWidth(440)
        btn = QPushButton(f"Build {n} schematic{'s' if n != 1 else ''}")
        btn.setProperty("pillRole", "primary")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(self.regenerate)
        wrap = QHBoxLayout()
        wrap.addStretch(1); wrap.addWidget(btn); wrap.addStretch(1)
        v.addWidget(title); v.addWidget(sub); v.addSpacing(6)
        v.addLayout(wrap)
        return w

    def _build_grid(self, schematics: List[dict]) -> QWidget:
        wrap = QFrame()
        grid = QGridLayout(wrap)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8); grid.setVerticalSpacing(8)
        sel = set(self.store.stage4_sch.selected_idxs)
        for i, s in enumerate(schematics):
            card = _SchematicThumbnail(
                s, self._mode, self._alignment, self._show_edges, wrap)
            card.set_selected(card.plan_index in sel)
            card.clicked.connect(self._toggle_select)
            card.doubleClicked.connect(self._open_detail)
            card.roomClicked.connect(self._show_room_tooltip)
            self._thumbs.append(card)
            grid.addWidget(card, i // 4, i % 4)
        for c in range(4):
            grid.setColumnStretch(c, 1)
        return wrap

    # ───────────────────────────────────────────────────────────────
    # Toolbar
    # ───────────────────────────────────────────────────────────────
    def _build_toolbar(self) -> QFrame:
        tb = QFrame(self); tb.setObjectName("SchemToolbar")
        lay = QHBoxLayout(tb)
        lay.setContentsMargins(14, 8, 14, 8); lay.setSpacing(6)

        self.btn_regenerate = QPushButton("Regenerate")
        self.btn_regenerate.setProperty("linkBtn", True)
        self.btn_regenerate.setCursor(Qt.PointingHandCursor)
        self.btn_regenerate.clicked.connect(self.regenerate)
        lay.addWidget(self.btn_regenerate)

        lay.addWidget(self._sep())

        # Mode pill toggle
        mode_pill = QFrame(); mode_pill.setObjectName("ModePill")
        mp = QHBoxLayout(mode_pill)
        mp.setContentsMargins(2, 2, 2, 2); mp.setSpacing(0)
        self.btn_bubble = QPushButton("Bubble")
        self.btn_bubble.setProperty("pillToggle", True)
        self.btn_bubble.setProperty("pillActive", self._mode == "bubble")
        self.btn_bubble.setCursor(Qt.PointingHandCursor)
        self.btn_bubble.clicked.connect(lambda: self._set_mode("bubble"))
        self.btn_rect = QPushButton("Rectangle")
        self.btn_rect.setProperty("pillToggle", True)
        self.btn_rect.setProperty("pillActive", self._mode == "rectangle")
        self.btn_rect.setCursor(Qt.PointingHandCursor)
        self.btn_rect.clicked.connect(lambda: self._set_mode("rectangle"))
        mp.addWidget(self.btn_bubble); mp.addWidget(self.btn_rect)
        lay.addWidget(mode_pill)

        lay.addWidget(self._sep())

        # Alignment buttons
        align_lbl = QLabel("ALIGN")
        align_lbl.setProperty("sectionHeader", True)
        lay.addWidget(align_lbl)
        self.align_buttons: dict[str, QPushButton] = {}
        for a in ALIGNMENTS:
            btn = QPushButton(a)
            btn.setProperty("alignBtn", True)
            btn.setProperty("alignActive", self._alignment == a)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, k=a: self._set_align(k))
            lay.addWidget(btn)
            self.align_buttons[a] = btn

        lay.addWidget(self._sep())

        # Show edges
        self.btn_edges = QPushButton("● Show edges")
        self.btn_edges.setProperty("toggleChip", True)
        self.btn_edges.setProperty("toggleChipActive", self._show_edges)
        self.btn_edges.setCursor(Qt.PointingHandCursor)
        self.btn_edges.clicked.connect(self._toggle_edges)
        lay.addWidget(self.btn_edges)

        lay.addWidget(self._sep())

        # Select all
        self.btn_select_all = QPushButton("■ Select all")
        self.btn_select_all.setProperty("toggleChip", True)
        self.btn_select_all.setProperty("toggleChipActive", False)
        self.btn_select_all.setCursor(Qt.PointingHandCursor)
        self.btn_select_all.clicked.connect(self._toggle_select_all)
        lay.addWidget(self.btn_select_all)

        # Pareto-dominant
        self.btn_dominant = QPushButton("◆ Select Pareto-dominant")
        self.btn_dominant.setProperty("toggleChip", True)
        self.btn_dominant.setCursor(Qt.PointingHandCursor)
        self.btn_dominant.clicked.connect(self._select_pareto)
        lay.addWidget(self.btn_dominant)

        lay.addStretch(1)

        # Mock pill — copy matches the web app exactly
        self.mock_pill = QLabel("Derived from mocked upstream data")
        self.mock_pill.setProperty("mockPill", True)
        self.mock_pill.setVisible(False)
        lay.addWidget(self.mock_pill)

        lay.addStretch(1)

        # Selection text
        self.sel_label = QLabel("No plans selected")
        self.sel_label.setProperty("sectionSubtitle", True)
        lay.addWidget(self.sel_label)

        # Save batch contact sheet (top-9 PNG)
        self.btn_save_batch = QPushButton("Save Batch")
        self.btn_save_batch.setProperty("linkBtn", True)
        self.btn_save_batch.setCursor(Qt.PointingHandCursor)
        self.btn_save_batch.clicked.connect(self._on_save_batch)
        lay.addWidget(self.btn_save_batch)

        # Export selected
        self.btn_export = QPushButton("Export selected  →")
        self.btn_export.setProperty("pillRole", "primary")
        self.btn_export.setCursor(Qt.PointingHandCursor)
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self._on_export)
        lay.addWidget(self.btn_export)

        self._sync_align_enabled()
        return tb

    @staticmethod
    def _sep() -> QFrame:
        s = QFrame(); s.setObjectName("ToolbarSep")
        s.setFixedSize(1, 18)
        return s

    def _sync_align_enabled(self) -> None:
        is_rect = (self._mode == "rectangle")
        for btn in self.align_buttons.values():
            btn.setEnabled(is_rect)

    def _refresh_toolbar(self) -> None:
        st4 = self.store.stage4_sch
        has = bool(st4.schematics)
        n_sel = len(st4.selected_idxs)
        self.mock_pill.setVisible(has and st4.mocked)
        self.btn_select_all.setEnabled(has)
        self.btn_dominant.setEnabled(has)
        self.btn_edges.setEnabled(has)
        self.btn_bubble.setEnabled(has)
        self.btn_rect.setEnabled(has)
        for btn in self.align_buttons.values():
            btn.setEnabled(has and self._mode == "rectangle")
        self.btn_regenerate.setEnabled(
            bool(self.store.stage3_conn.selected_idxs))
        if not has:
            self.sel_label.setText("")
            self.btn_export.setEnabled(False)
        elif n_sel == 0:
            self.sel_label.setText("No plans selected")
            self.btn_export.setEnabled(False)
        elif n_sel == 1:
            self.sel_label.setText("1 plan selected")
            self.btn_export.setEnabled(True)
        else:
            self.sel_label.setText(f"{n_sel} plans selected")
            self.btn_export.setEnabled(True)
        all_sel = has and n_sel == len(st4.schematics)
        _set_prop(self.btn_select_all, "toggleChipActive", all_sel)
        self.btn_select_all.setText(
            "■ Select none" if all_sel else "■ Select all")

    # ───────────────────────────────────────────────────────────────
    # Toolbar slots
    # ───────────────────────────────────────────────────────────────
    def _set_mode(self, mode: str) -> None:
        if mode not in ("bubble", "rectangle") or mode == self._mode:
            return
        self._mode = mode
        QSettings("ARML", "LayoutGenerator").setValue(MODE_KEY, mode)
        _set_prop(self.btn_bubble, "pillActive", mode == "bubble")
        _set_prop(self.btn_rect,   "pillActive", mode == "rectangle")
        self._sync_align_enabled()
        for t in self._thumbs:
            t.set_mode(mode)
        if self._detail is not None:
            self._detail.set_mode(mode)

    def _set_align(self, alignment: str) -> None:
        if alignment not in ALIGNMENTS or alignment == self._alignment:
            return
        self._alignment = alignment
        QSettings("ARML", "LayoutGenerator").setValue(ALIGN_KEY, alignment)
        for k, btn in self.align_buttons.items():
            _set_prop(btn, "alignActive", k == alignment)
        for t in self._thumbs:
            t.set_alignment(alignment)
        if self._detail is not None:
            self._detail.set_alignment(alignment)

    def _toggle_edges(self) -> None:
        self._show_edges = not self._show_edges
        QSettings("ARML", "LayoutGenerator").setValue(
            EDGES_KEY, "true" if self._show_edges else "false")
        _set_prop(self.btn_edges, "toggleChipActive", self._show_edges)
        for t in self._thumbs:
            t.set_show_edges(self._show_edges)
        if self._detail is not None:
            self._detail.set_show_edges(self._show_edges)

    # ── Pareto background ──────────────────────────────────────────
    def _bg_points(self) -> list[dict]:
        """Background pool points: every decoded sample of the zone stage
        (QVCI / AQI as computed by the journal equations)."""
        pool = getattr(self.store.stage2_zones, "sample_pool", None) or []
        return [{"qvci": float(p.get("qvci", 0.0)), "aqi": float(p.get("aqi", 0.0))}
                for p in pool]

    def _toggle_select(self, plan_idx: int) -> None:
        st = self.store.stage4_sch
        sel = set(st.selected_idxs)
        if plan_idx in sel:
            sel.remove(plan_idx)
        else:
            sel.add(plan_idx)
        new = sorted(sel)
        self.store.set_stage4_selection(new)
        for t in self._thumbs:
            t.set_selected(t.plan_index in sel)
        self.pareto.set_selected(new)
        self._refresh_toolbar()

    def _toggle_select_all(self) -> None:
        st = self.store.stage4_sch
        if len(st.selected_idxs) == len(st.schematics):
            new: list[int] = []
        else:
            new = [int(s["plan_index"]) for s in st.schematics]
        self.store.set_stage4_selection(new)
        sel = set(new)
        for t in self._thumbs:
            t.set_selected(t.plan_index in sel)
        self.pareto.set_selected(new)
        self._refresh_toolbar()

    def _select_pareto(self) -> None:
        dom = pareto_front_indices(self.store.stage4_sch.schematics)
        self.store.set_stage4_selection(dom)
        sel = set(dom)
        for t in self._thumbs:
            t.set_selected(t.plan_index in sel)
        self.pareto.set_selected(dom)
        self._refresh_toolbar()

    def _on_pareto_click(self, plan_idx: int) -> None:
        self._toggle_select(int(plan_idx))

    def _on_save_batch(self) -> None:
        """Render the top-9 schematics (ranked by closeness to the QVCI/
        AQI targets) into one contact-sheet PNG, using the live QV
        threshold so the sheet matches what's on screen."""
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        st4 = self.store.stage4_sch
        schematics = list(st4.schematics or [])
        if not schematics:
            QMessageBox.information(
                self, "Nothing to export",
                "Generate schematics before saving a batch sheet.")
            return

        tq, ta = get_targets(self.store)
        ranked = rank_by_target(schematics, tq, ta, n=9)
        header_info = get_target_params(self.store)

        path, _ = QFileDialog.getSaveFileName(
            self, "Save batch contact sheet",
            "batch_contact_sheet.png",
            "PNG image (*.png)")
        if not path:
            return

        try:
            theme = self.store.ui.theme
        except Exception:
            theme = "offwhite"

        title = f"Batch contact sheet \u2014 top {len(ranked)} of {len(schematics)}"
        ok = save_batch_png(
            ranked, header_info,
            mode=self._mode,
            alignment=self._alignment,
            show_edges=self._show_edges,
            path=path,
            theme=theme,
            title=title,
            qv_threshold=None,
        )
        if ok:
            QMessageBox.information(
                self, "Saved", f"Batch contact sheet written to:\n{path}")
        else:
            QMessageBox.warning(
                self, "Export failed",
                "Could not write the batch contact sheet.")

    def _on_export(self) -> None:
        """Render each selected schematic card to a high-resolution
        transparent PNG. One file per card, dropped in a folder the
        user picks.

        Mirrors the Stage 2 "Export selected" approach: build an
        off-screen ``_SchematicThumbnail`` per card, neutralise filled
        backgrounds via a one-off stylesheet, then ``render()`` it into
        an ARGB32 ``QImage`` with a 3× painter scale. Foreground stays
        untouched — room fills, compass labels, metric badges all keep
        their colours.
        """
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from PySide6.QtCore import QPoint
        from PySide6.QtGui  import QImage, QPainter

        st4 = self.store.stage4_sch
        sel_idxs = sorted(set(st4.selected_idxs))
        if not sel_idxs:
            QMessageBox.information(
                self, "Nothing to export",
                "Select at least one schematic card before exporting.")
            self.exportRequested.emit()
            return

        out_dir = QFileDialog.getExistingDirectory(
            self, "Choose folder for schematic PNG exports")
        if not out_dir:
            return

        # Cards indexed by plan_index, not list position
        by_idx = {int(s["plan_index"]): s for s in st4.schematics}

        # Transparent-background overrides — wipes filled card surfaces
        # without touching the canvas paint or any label colours.
        transparent_qss = (
            "QFrame[schemCard='true'] { "
            "  background: transparent; border: none; }"
            "QFrame#SchemCardHeader, QFrame#SchemCardBody, "
            "QFrame#SchemCardFooter { background: transparent; }"
            "QFrame { background: transparent; }"
            "QLabel { background: transparent; }"
        )

        CARD_W, CARD_H = 320, 380
        SCALE = 3.0

        saved = 0
        skipped: list[str] = []
        for idx in sel_idxs:
            sch = by_idx.get(idx)
            if sch is None:
                continue

            card = _SchematicThumbnail(
                sch, self._mode, self._alignment,
                self._show_edges, parent=None)
            card.set_selected(True)
            card.setStyleSheet(transparent_qss)
            card.setFixedHeight(CARD_H)
            card.resize(CARD_W, CARD_H)
            # Force a layout pass — children only compute geometry
            # after the widget has been realised at least once.
            card.show(); card.hide()

            img = QImage(int(CARD_W * SCALE), int(CARD_H * SCALE),
                          QImage.Format_ARGB32)
            img.fill(Qt.transparent)
            painter = QPainter(img)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.TextAntialiasing)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.scale(SCALE, SCALE)
            # DrawChildren only — no window background. Off-screen
            # parent's background never ends up in the image.
            card.render(painter, QPoint(0, 0), card.rect(),
                         QWidget.RenderFlag.DrawChildren)
            painter.end()

            out_path = f"{out_dir}/schematic_card_{idx:02d}.png"
            if img.save(out_path):
                saved += 1
            else:
                skipped.append(out_path)
            card.deleteLater()

        msg = f"Saved {saved} of {len(sel_idxs)} card(s) to:\n{out_dir}"
        if skipped:
            msg += (f"\n\nFailed to write {len(skipped)} file(s)"
                    f" — check folder permissions.")
        QMessageBox.information(self, "Export complete", msg)

        # Still emit the signal in case any external listener wires up
        # additional behaviour (analytics, etc.).
        self.exportRequested.emit()

    def _on_theme_changed(self, _name: str) -> None:
        for t in self._thumbs:
            t.canvas.update()
        self.pareto.update()
