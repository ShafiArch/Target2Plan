"""
Stage 0 — Inputs panel.

Journal version (v13 zone model): collapsible sections for room counts
(four zoned types + balconies), plan geometry in metres, site conditions
(view category and outdoor level per façade), aspect ratios, planned
WWR, the QVCI / AQI targets with the reachable range, the zone-capacity
rule (developer control), generation parameters and the assembled
payload.

Every control is bound to ``SessionStore.inputs`` in real time —
each change mutates the dataclass and emits ``inputsChanged`` so
downstream stages and the status dot refresh immediately.

Visual styling is theme-aware: this module uses QSS property
selectors (``sectionHeader``, ``fieldLabel``, ``counterBtn``,
``unitsToggle``, etc.) defined in :mod:`app.theme`. No raw colours
are hardcoded; switching themes restyles everything in one shot.

Layout reference: zone_app screenshots, image 1.
"""

from __future__ import annotations

import json
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout,
    QLabel, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from app import theme as theme_mod
from app.state import InputsState, SessionStore, ZONED_TYPES, EXTRA_TYPES
from models import zone_scoring as zs


# ─────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────

PRIMARY_TYPES = ["living", "bedroom", "kitchen", "bathroom"]     # zoned by the model
OUTDOOR_TYPES = ["balcony"]                                       # connectivity / schematic only

ROOM_LABELS = {
    "living":   "Living",    "bedroom":  "Bedroom",
    "kitchen":  "Kitchen",   "bathroom": "Bathroom",
    "balcony":  "Balcony",
}

VIEW_OPTIONS = ["Vegetation", "Sky", "Urban", "Balcony", "No View"]
DIRECTIONS   = ["N", "S", "E", "W"]
SPL_RANGE    = (int(zs.L_RANGE[0]), int(zs.L_RANGE[1]))          # 30–80 dB(A), the corpus range


def _set_prop(widget: QWidget, name: str, value) -> None:
    """Set a Qt property and force a QSS restyle."""
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


# ─────────────────────────────────────────────────────────────────────
#  Collapsible section
# ─────────────────────────────────────────────────────────────────────

class _SectionHeader(QWidget):
    """Clickable header row: caret + title + subtitle."""

    clicked = Signal()

    def __init__(self, title: str, subtitle: str = "",
                 expanded: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 6)
        lay.setSpacing(8)

        self.caret = QLabel("▾" if expanded else "▸")
        self.caret.setProperty("caret", True)
        self.caret.setFixedWidth(10)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(0)
        self.title_lbl = QLabel(title)
        self.title_lbl.setProperty("sectionHeader", True)
        self.subtitle_lbl: QLabel | None = None
        if subtitle:
            self.subtitle_lbl = QLabel(subtitle)
            self.subtitle_lbl.setProperty("sectionSubtitle", True)
        text_col.addWidget(self.title_lbl)
        if self.subtitle_lbl is not None:
            text_col.addWidget(self.subtitle_lbl)

        lay.addWidget(self.caret)
        lay.addLayout(text_col, 1)

    def set_expanded(self, expanded: bool) -> None:
        self.caret.setText("▾" if expanded else "▸")

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class _Section(QFrame):
    """A header + body with click-to-collapse behaviour."""

    def __init__(self, title: str, subtitle: str = "",
                 expanded: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._expanded = expanded

        self.header = _SectionHeader(title, subtitle, expanded, self)
        self.header.clicked.connect(self._toggle)

        self.body = QFrame(self)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 2, 0, 10)
        self.body_layout.setSpacing(8)
        self.body.setVisible(expanded)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.header)
        root.addWidget(self.body)

    def add(self, widget: QWidget) -> None:
        self.body_layout.addWidget(widget)

    def add_layout(self, layout) -> None:
        self.body_layout.addLayout(layout)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self.header.set_expanded(self._expanded)
        self.body.setVisible(self._expanded)


# ─────────────────────────────────────────────────────────────────────
#  Counter row: swatch · label · [−] [+] · value
# ─────────────────────────────────────────────────────────────────────

class _CounterRow(QWidget):
    """One room-type row with a color swatch and ± buttons."""

    valueChanged = Signal(str, int)

    def __init__(self, room_type: str, label: str, value: int,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.room_type = room_type

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        sw = QLabel(self)
        sw.setFixedSize(10, 10)
        color = theme_mod.ROOM_COLORS.get(room_type, "#9aa0a6")
        sw.setStyleSheet(f"background: {color}; border-radius: 2px;")

        lbl = QLabel(label, self)
        lbl.setMinimumWidth(60)

        self.btn_minus = QPushButton("−", self)
        self.btn_minus.setFixedSize(20, 20)
        self.btn_minus.setProperty("counterBtn", True)
        self.btn_minus.setCursor(Qt.PointingHandCursor)
        self.btn_minus.clicked.connect(self._decrement)

        self.spin = QSpinBox(self)
        self.spin.setRange(0, 99)
        self.spin.setValue(value)
        self.spin.setButtonSymbols(QSpinBox.NoButtons)
        self.spin.setAlignment(Qt.AlignCenter)
        self.spin.setFixedWidth(36)
        self.spin.valueChanged.connect(self._emit)

        self.btn_plus = QPushButton("+", self)
        self.btn_plus.setFixedSize(20, 20)
        self.btn_plus.setProperty("counterBtn", True)
        self.btn_plus.setCursor(Qt.PointingHandCursor)
        self.btn_plus.clicked.connect(self._increment)

        lay.addWidget(sw)
        lay.addWidget(lbl, 1)
        lay.addWidget(self.btn_minus)
        lay.addWidget(self.spin)
        lay.addWidget(self.btn_plus)

    def _decrement(self) -> None:
        self.spin.setValue(max(0, self.spin.value() - 1))

    def _increment(self) -> None:
        self.spin.setValue(self.spin.value() + 1)

    def _emit(self, v: int) -> None:
        self.valueChanged.emit(self.room_type, int(v))

    def set_value(self, v: int) -> None:
        if self.spin.value() != v:
            self.spin.setValue(int(v))


# ─────────────────────────────────────────────────────────────────────
#  Compass widget — small N / S / E / W diagram
# ─────────────────────────────────────────────────────────────────────

class _Compass(QWidget):
    """Decorative square with cardinal-direction labels around it.

    Theme-aware: pulls the accent + ink3 colours from the current
    palette by inspecting the QApplication's effective stylesheet.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(110, 110)

    def _theme_palette(self) -> dict:
        """Best-effort: read the current store's theme through the
        toplevel window. Falls back to off-white."""
        top = self.window()
        if top is not None and hasattr(top, "store"):
            return theme_mod.PALETTES.get(
                top.store.ui.theme, theme_mod.PALETTES["offwhite"])
        return theme_mod.PALETTES["offwhite"]

    def paintEvent(self, _e) -> None:
        pal = self._theme_palette()
        accent = QColor(*pal["accent"])
        ink3   = QColor(*pal["ink3"])

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        box_pad = 22
        box = self.rect().adjusted(box_pad, box_pad, -box_pad, -box_pad - 8)
        p.setPen(QPen(accent, 2))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(box, 2, 2)

        # Cardinal labels around the box
        f = QFont(); f.setBold(True); f.setPointSize(8)
        p.setFont(f)
        p.setPen(QPen(accent, 1))
        cx = self.width() // 2
        cy = (self.height() - 8) // 2

        p.drawText(cx - 4,            box_pad - 6,                "N")
        p.drawText(cx - 4,            self.height() - 18,         "S")
        p.drawText(self.width() - 12, cy + 4,                     "E")
        p.drawText(4,                 cy + 4,                     "W")

        # Footer label
        p.setPen(QPen(ink3, 1))
        f2 = QFont(); f2.setPointSize(7)
        f2.setLetterSpacing(QFont.PercentageSpacing, 115)
        p.setFont(f2)
        p.drawText(self.rect().adjusted(0, self.height() - 12, 0, 0),
                   Qt.AlignHCenter, "ORIENTATION")
        p.end()


# ─────────────────────────────────────────────────────────────────────
#  Units segmented toggle  (px / m / ft)
# ─────────────────────────────────────────────────────────────────────

class _UnitsToggle(QWidget):
    """Three-way pill toggle for unit system."""

    unitsChanged = Signal(str)

    def __init__(self, value: str = "px", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: dict[str, QPushButton] = {}

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        for unit in ("px", "m", "ft"):
            b = QPushButton(unit, self)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(22)
            b.setMinimumWidth(34)
            b.setProperty("unitsToggle", True)
            b.setProperty("unitsActive", unit == value)
            b.clicked.connect(lambda _=False, u=unit: self._select(u))
            self._buttons[unit] = b
            lay.addWidget(b)

    def _select(self, unit: str) -> None:
        for u, b in self._buttons.items():
            active = (u == unit)
            b.setChecked(active)
            _set_prop(b, "unitsActive", active)
        self.unitsChanged.emit(unit)

    def set_value(self, unit: str) -> None:
        self._select(unit)


# ─────────────────────────────────────────────────────────────────────
#  InputsPanel — the main Stage 0 widget
# ─────────────────────────────────────────────────────────────────────

class InputsPanel(QWidget):
    """The full Stage 0 inputs panel, bound to ``SessionStore``."""

    # Bottom-bar action: user pressed "Generate area distributions →".
    # Stage 1 will connect to this when it lands.
    generateRequested = Signal()

    def __init__(self, store: SessionStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self._counter_rows: dict[str, _CounterRow] = {}

        # Repaint compass + theme-sensitive bits when theme changes.
        self.store.themeChanged.connect(self._on_theme_changed)

        # ── Scroll area ────────────────────────────────────────────
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        inner = QWidget(scroll)
        v = QVBoxLayout(inner)
        v.setContentsMargins(14, 8, 14, 8)
        v.setSpacing(4)

        # Targets first: the two indices the model is conditioned on are the
        # primary design input of the journal version.
        v.addWidget(self._build_targets_section())
        v.addWidget(self._build_room_counts())
        self._sec_plan = self._build_plan_geometry()
        v.addWidget(self._sec_plan)
        v.addWidget(self._build_site_conditions())

        # Advanced sections — collapsed.
        self._sec_aspect = self._build_aspect_ratios_section()
        v.addWidget(self._sec_aspect)
        v.addWidget(self._build_wwr_section())
        v.addWidget(self._build_capacity_section())
        v.addWidget(self._build_generation_section())
        v.addWidget(self._build_payload_debug())

        v.addStretch(1)
        scroll.setWidget(inner)

        # ── Bottom bar ─────────────────────────────────────────────
        bottom = self._build_bottom_bar()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(scroll, 1)
        root.addWidget(bottom)

        self._refresh_room_total()
        self._refresh_total_area()
        self._refresh_capacity_hint()
        self._refresh_reachable()
        self._refresh_payload_debug()
        # The reachable range depends on the selected area variation.
        self.store.stage1Changed.connect(self._refresh_reachable)

    # ───────────────────────────────────────────────────────────────
    # Room Counts
    # ───────────────────────────────────────────────────────────────
    def _build_room_counts(self) -> _Section:
        sec = _Section("ROOM COUNTS", "6 rooms total", expanded=True)
        sec.setToolTip("Used by the area generator; in draw mode the counts come from the drawing")
        self._sec_room_counts = sec
        counts = self.store.inputs.room_counts

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(6)
        # One grid for every room type: the four zoned types and the balcony.
        for i, rt in enumerate(PRIMARY_TYPES + OUTDOOR_TYPES):
            row = _CounterRow(rt, ROOM_LABELS[rt], counts.get(rt, 0), sec)
            row.valueChanged.connect(self._on_room_count_changed)
            self._counter_rows[rt] = row
            grid.addWidget(row, i // 2, i % 2)
        sec.add_layout(grid)

        note = QLabel("Balcony joins the plan at the connectivity stage", sec)
        note.setProperty("sectionSubtitle", True)
        note.setContentsMargins(0, 2, 0, 0)
        note.setToolTip("Balconies are a façade view category rather than a zoned room: "
                        "the zone model places the indoor rooms, and the balcony is "
                        "attached to the plan when the connectivity graph is built.")
        sec.add(note)
        return sec

    def _on_room_count_changed(self, room_type: str, value: int) -> None:
        self.store.inputs.room_counts[room_type] = int(value)
        self._refresh_room_total()
        self._refresh_payload_debug()
        self.store.inputsChanged.emit()
        self.store.stateChanged.emit()

    def _refresh_room_total(self) -> None:
        n = self.store.inputs.total_rooms
        nz = self.store.inputs.zoned_rooms
        if self._sec_room_counts.header.subtitle_lbl is not None:
            extra = n - nz
            self._sec_room_counts.header.subtitle_lbl.setText(
                f"{n} rooms total · {nz} zoned" + (f" · {extra} balcony" if extra else ""))
        if hasattr(self, "_cap_hint"):
            self._refresh_capacity_hint()

    # ───────────────────────────────────────────────────────────────
    # Plan Geometry
    # ───────────────────────────────────────────────────────────────
    def _build_plan_geometry(self) -> _Section:
        sec = _Section("PLAN GEOMETRY", "", expanded=True)

        # Units row
        row1 = QHBoxLayout(); row1.setSpacing(8)
        lbl_units = QLabel("UNITS")
        lbl_units.setProperty("fieldLabel", True)
        self.units_toggle = _UnitsToggle(self.store.inputs.plan_geometry.units)
        self.units_toggle.unitsChanged.connect(self._on_units_changed)
        row1.addWidget(lbl_units)
        row1.addStretch(1)
        row1.addWidget(self.units_toggle)
        sec.add_layout(row1)

        # W / H
        row2 = QHBoxLayout(); row2.setSpacing(12)

        col_w = QVBoxLayout(); col_w.setSpacing(2)
        lbl_w = QLabel("WIDTH (E−W)")
        lbl_w.setProperty("fieldLabel", True)
        self.sp_width = QDoubleSpinBox()
        self.sp_width.setRange(1, 100000)
        self.sp_width.setDecimals(1 if self.store.inputs.plan_geometry.units != "px" else 0)
        self.sp_width.setValue(self.store.inputs.plan_geometry.width)
        self.sp_width.setSuffix("  " + self.store.inputs.plan_geometry.units)
        self.sp_width.valueChanged.connect(self._on_width_changed)
        col_w.addWidget(lbl_w); col_w.addWidget(self.sp_width)

        col_h = QVBoxLayout(); col_h.setSpacing(2)
        lbl_h = QLabel("HEIGHT (N−S)")
        lbl_h.setProperty("fieldLabel", True)
        self.sp_height = QDoubleSpinBox()
        self.sp_height.setRange(1, 100000)
        self.sp_height.setDecimals(1 if self.store.inputs.plan_geometry.units != "px" else 0)
        self.sp_height.setValue(self.store.inputs.plan_geometry.height)
        self.sp_height.setSuffix("  " + self.store.inputs.plan_geometry.units)
        self.sp_height.valueChanged.connect(self._on_height_changed)
        col_h.addWidget(lbl_h); col_h.addWidget(self.sp_height)

        row2.addLayout(col_w, 1)
        row2.addLayout(col_h, 1)
        sec.add_layout(row2)

        # px scale (only meaningful when units == px)
        row_px = QHBoxLayout(); row_px.setSpacing(8)
        self.lbl_px_scale = QLabel("PIXELS PER METRE")
        self.lbl_px_scale.setProperty("fieldLabel", True)
        self.sp_px_scale = QDoubleSpinBox()
        self.sp_px_scale.setRange(1, 10000)
        self.sp_px_scale.setDecimals(0)
        self.sp_px_scale.setValue(self.store.inputs.plan_geometry.px_per_m)
        self.sp_px_scale.setSuffix("  px/m")
        self.sp_px_scale.setFixedWidth(110)
        self.sp_px_scale.setToolTip("The zone model works in metres; tell it how many pixels one metre is.")
        self.sp_px_scale.valueChanged.connect(self._on_px_scale_changed)
        row_px.addWidget(self.lbl_px_scale)
        row_px.addStretch(1)
        row_px.addWidget(self.sp_px_scale)
        sec.add_layout(row_px)
        self._px_scale_widgets = (self.lbl_px_scale, self.sp_px_scale)

        # Total area
        row3 = QHBoxLayout()
        lbl_total = QLabel("TOTAL AREA")
        lbl_total.setProperty("fieldLabel", True)
        self.lbl_total_area = QLabel("108  m²")
        self.lbl_total_area.setProperty("monoValue", True)
        self.lbl_total_area.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row3.addWidget(lbl_total)
        row3.addStretch(1)
        row3.addWidget(self.lbl_total_area)
        sec.add_layout(row3)
        self._update_px_scale_visibility()
        return sec

    def _update_px_scale_visibility(self) -> None:
        on = self.store.inputs.plan_geometry.units == "px"
        for w in self._px_scale_widgets:
            w.setVisible(on)

    def _on_px_scale_changed(self, v: float) -> None:
        self.store.inputs.plan_geometry.px_per_m = float(v)
        self._refresh_total_area()
        self._refresh_reachable()
        self._refresh_payload_debug()
        self.store.inputsChanged.emit()
        self.store.stateChanged.emit()

    def _on_units_changed(self, unit: str) -> None:
        self.store.inputs.plan_geometry.units = unit
        self.sp_width.setSuffix("  " + unit)
        self.sp_height.setSuffix("  " + unit)
        self.sp_width.setDecimals(1 if unit != "px" else 0)
        self.sp_height.setDecimals(1 if unit != "px" else 0)
        self._update_px_scale_visibility()
        self._refresh_total_area()
        self._refresh_payload_debug()
        self.store.inputsChanged.emit()
        self.store.stateChanged.emit()

    def _on_width_changed(self, v: float) -> None:
        self.store.inputs.plan_geometry.width = float(v)
        self._refresh_total_area()
        self._refresh_payload_debug()
        self.store.inputsChanged.emit()
        self.store.stateChanged.emit()

    def _on_height_changed(self, v: float) -> None:
        self.store.inputs.plan_geometry.height = float(v)
        self._refresh_total_area()
        self._refresh_payload_debug()
        self.store.inputsChanged.emit()
        self.store.stateChanged.emit()

    def _refresh_total_area(self) -> None:
        g = self.store.inputs.plan_geometry
        area = g.width * g.height
        if g.units == "m":
            self.lbl_total_area.setText(f"{area:,.1f}  m²")
        else:
            self.lbl_total_area.setText(f"{area:,.0f}  {g.units}²  ≈ {g.total_area_m2:,.1f} m²")

    # ───────────────────────────────────────────────────────────────
    # Site Conditions
    # ───────────────────────────────────────────────────────────────
    def _build_site_conditions(self) -> _Section:
        sec = _Section("SITE CONDITIONS",
                       "View category & outdoor level per façade (30–80 dB(A))",
                       expanded=True)

        outer = QHBoxLayout(); outer.setSpacing(14)
        self._compass = _Compass(sec)
        outer.addWidget(self._compass)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self.view_combos: dict[str, QComboBox] = {}
        self.spl_spins:   dict[str, QDoubleSpinBox] = {}

        sc = self.store.inputs.site_conditions
        side_data = {"N": sc.N, "S": sc.S, "E": sc.E, "W": sc.W}

        for i, d in enumerate(DIRECTIONS):
            side = side_data[d]
            d_lbl = QLabel(d, sec)
            d_lbl.setProperty("compassDirection", True)
            d_lbl.setFixedWidth(12)
            d_lbl.setStyleSheet(
                "color: rgb(181,36,62); font-weight: 700; font-size: 11px;")

            cb = QComboBox(sec)
            cb.addItems(VIEW_OPTIONS)
            cb.setCurrentText(side.view)
            cb.currentTextChanged.connect(
                lambda txt, dr=d: self._on_view_changed(dr, txt))

            spl = QDoubleSpinBox(sec)
            spl.setRange(SPL_RANGE[0], SPL_RANGE[1])
            spl.setDecimals(0)
            spl.setSuffix("  dB(A)")
            spl.setValue(side.spl_dB)
            spl.setFixedWidth(92)
            spl.setToolTip("Outdoor A-weighted level at this façade. The corpus covers "
                           f"{SPL_RANGE[0]}–{SPL_RANGE[1]} dB(A); 'No View' plus a low level "
                           "models a façade attached to another building.")
            spl.valueChanged.connect(
                lambda v, dr=d: self._on_spl_changed(dr, v))

            self.view_combos[d] = cb
            self.spl_spins[d]   = spl

            grid.addWidget(d_lbl, i, 0)
            grid.addWidget(cb,    i, 1)
            grid.addWidget(spl,   i, 2)

        outer.addLayout(grid, 1)
        sec.add_layout(outer)
        return sec

    def _on_view_changed(self, direction: str, view: str) -> None:
        side = getattr(self.store.inputs.site_conditions, direction)
        side.view = view
        self._refresh_reachable()
        self._refresh_payload_debug()
        self.store.inputsChanged.emit()
        self.store.stateChanged.emit()

    def _on_spl_changed(self, direction: str, v: float) -> None:
        side = getattr(self.store.inputs.site_conditions, direction)
        side.spl_dB = float(v)
        self._refresh_reachable()
        self._refresh_payload_debug()
        self.store.inputsChanged.emit()
        self.store.stateChanged.emit()

    # ───────────────────────────────────────────────────────────────
    # Aspect Ratios / Window-to-Wall Ratio
    # ───────────────────────────────────────────────────────────────
    # Primary room types shown in the per-room AR/WWR sections. Other
    # types (stair, garden, …) fall back to the dataclass defaults at
    # generation time. Matches the upstream zone tool's editable rows.
    _AR_WWR_TYPES = ("living", "bedroom", "kitchen", "bathroom", "balcony")

    def _build_aspect_ratios_section(self) -> _Section:
        """ASPECT RATIOS — per-room-type shape (height ÷ width).

        AR > 1 means taller (N–S) than wide (E–W). Applies to every
        room of that type. Default values come from
        generate_zones_v11_new5.py's ``ROOM_DEFAULTS`` table; balcony
        and storage extended from the web app.
        """
        sec = _Section("ASPECT RATIOS",
                       "Shape per room type (N–S ÷ E–W)",
                       expanded=False)
        hint = QLabel("AR > 1 = taller (N–S) than wide (E–W). Applies to every room "
                      "of that type; sets the façade lengths the acoustic model sees.")
        hint.setProperty("sectionSubtitle", True)
        hint.setWordWrap(True)
        sec.add(hint)
        ar_dict = self.store.inputs.aspect_ratios
        for rt in self._AR_WWR_TYPES:
            sec.add(self._room_slider_row(
                rt, ar_dict.get(rt, 1.0), 0.5, 3.0, 0.05,
                on_change=lambda v, k=rt: self._set_ar(k, v)))
        return sec

    def _build_wwr_section(self) -> _Section:
        """WINDOW-TO-WALL RATIO — per-room-type window glazing fraction."""
        sec = _Section("PLANNED WINDOW-TO-WALL RATIO",
                       "Per room type · full view credit at 0.40",
                       expanded=False)
        hint = QLabel("Planned glazing fraction of the room's exterior wall — a design "
                      "requirement, applied to every façade of the zone the room is placed in. "
                      "View credit rises linearly to 1 at WWR 0.40; more glazing also lets "
                      "more outdoor noise in.")
        hint.setProperty("sectionSubtitle", True)
        hint.setWordWrap(True)
        sec.add(hint)
        wwr_dict = self.store.inputs.wwr
        for rt in self._AR_WWR_TYPES:
            sec.add(self._room_slider_row(
                rt, wwr_dict.get(rt, 0.15), 0.0, 1.0, 0.01,
                on_change=lambda v, k=rt: self._set_wwr(k, v)))
        return sec

    def _room_slider_row(self, room_type: str, value: float,
                          lo: float, hi: float, step: float,
                          on_change) -> QWidget:
        """colour-swatch · room name · slider · value readout."""
        row = QWidget(self)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)

        # Swatch
        sw = QLabel(self)
        sw.setFixedSize(10, 10)
        sw.setStyleSheet(
            f"background: {theme_mod.ROOM_COLORS.get(room_type, '#9aa0a6')};"
            "border-radius: 2px;")
        h.addWidget(sw)

        # Name
        lbl = QLabel(room_type.capitalize())
        lbl.setProperty("fieldLabel", True)
        lbl.setMinimumWidth(76)
        h.addWidget(lbl)

        # Slider — integer step internally so range maps cleanly
        n_steps = max(1, int(round((hi - lo) / step)))
        sl = QSlider(Qt.Horizontal)
        sl.setRange(0, n_steps)
        sl.setValue(int(round((value - lo) / step)))
        sl.setSingleStep(1)
        h.addWidget(sl, 1)

        # Value readout
        decimals = 2 if step < 0.1 else 2
        val_lbl = QLabel(f"{value:.{decimals}f}")
        val_lbl.setProperty("fieldValue", True)
        val_lbl.setMinimumWidth(40)
        val_lbl.setAlignment(Qt.AlignRight)
        h.addWidget(val_lbl)

        def _on_slider(i: int) -> None:
            v = lo + i * step
            val_lbl.setText(f"{v:.{decimals}f}")
            on_change(v)
        sl.valueChanged.connect(_on_slider)
        return row

    # ── Field mutators (AR / WWR) ─────────────────────────────────
    def _set_ar(self, room_type: str, value: float) -> None:
        self.store.inputs.aspect_ratios[room_type] = float(value)
        self.store.inputsChanged.emit()
        self.store.stateChanged.emit()
        self._refresh_reachable()
        self._refresh_payload_debug()

    def _set_wwr(self, room_type: str, value: float) -> None:
        self.store.inputs.wwr[room_type] = float(value)
        self.store.inputsChanged.emit()
        self.store.stateChanged.emit()
        self._refresh_reachable()
        self._refresh_payload_debug()

    # ───────────────────────────────────────────────────────────────
    # Targets / Capacity / Generation params
    # ───────────────────────────────────────────────────────────────
    def _build_targets_section(self) -> _Section:
        """TARGETS — the two numbers the v13 model is conditioned on.

        QVCI and AQI are plan-level indices (area-weighted over the living,
        kitchen and bedroom rooms); the model is asked for an arrangement
        whose indices land close to them. The reachable range below tells
        the user which values this plan and site can actually produce.
        """
        sec = _Section("TARGETS",
                       "Requested plan-level QVCI · AQI (conditions the v13 model)",
                       expanded=True)
        prefs = self.store.inputs.prefs
        self.chk_targets = QCheckBox("Condition on targets (off = unconditioned sampling)")
        self.chk_targets.setChecked(bool(prefs.targets_enabled))
        self.chk_targets.toggled.connect(self._on_targets_enabled)
        sec.add(self.chk_targets)
        self._target_rows = {}
        self._target_rows["target_qvci"] = self._slider_row(
            "Target QVCI", prefs.target_qvci, 0.0, 1.0, 0.01,
            on_change=lambda v: self._set_pref("target_qvci", v),
            sublabel="Quality View Coverage Index")
        self._target_rows["target_aqi"] = self._slider_row(
            "Target AQI", prefs.target_aqi, 0.0, 1.0, 0.01,
            on_change=lambda v: self._set_pref("target_aqi", v),
            sublabel="Acoustic Quality Index")
        sec.add(self._target_rows["target_qvci"])
        sec.add(self._target_rows["target_aqi"])

        # Reachable range readout (needs the selected Stage-1 variation)
        self.lbl_reach_title = QLabel("REACHABLE RANGE")
        self.lbl_reach_title.setProperty("fieldLabel", True)
        self.lbl_reach_title.setContentsMargins(0, 6, 0, 0)
        sec.add(self.lbl_reach_title)
        self.lbl_reach = QLabel("—")
        self.lbl_reach.setProperty("sectionSubtitle", True)
        self.lbl_reach.setWordWrap(True)
        self.lbl_reach.setTextFormat(Qt.RichText)
        sec.add(self.lbl_reach)

        # Future indices — shown faded and disabled so the reader sees where
        # the method is heading (daylight / thermal conditioning).
        fut_title = QLabel("FUTURE UPDATES")
        fut_title.setProperty("fieldLabel", True)
        fut_title.setContentsMargins(0, 8, 0, 0)
        sec.add(fut_title)
        self._future_rows = []
        for name, full in (("Daylight Index", "Daylight Index · future update"),
                           ("Thermal Index", "Thermal Index · future update")):
            row = self._slider_row(name, 0.5, 0.0, 1.0, 0.01,
                                   on_change=lambda v: None, sublabel=full)
            row.setEnabled(False)
            row.setToolTip("Planned conditioning index — not part of the v13 model.")
            # Fade the whole row (labels + slider) without touching the theme.
            eff = QGraphicsOpacityEffect(row)
            eff.setOpacity(0.38)
            row.setGraphicsEffect(eff)
            self._future_rows.append(row)
            sec.add(row)
        return sec

    def _on_targets_enabled(self, on: bool) -> None:
        self.store.inputs.prefs.targets_enabled = bool(on)
        for row in self._target_rows.values():
            row.setEnabled(bool(on))
        self._refresh_payload_debug()
        self.store.inputsChanged.emit()
        self.store.stateChanged.emit()

    def _selected_area_variation(self):
        st1 = self.store.stage1_areas
        if st1.selected_idx is None or not st1.candidates:
            return None
        return next((c for c in st1.candidates
                     if int(c["variation_idx"]) == st1.selected_idx), None)

    def _refresh_reachable(self) -> None:
        """Recompute the reachable QVCI / AQI range for the current inputs
        and the selected area variation (cheap: no model call)."""
        if not hasattr(self, "lbl_reach"):
            return
        av = self._selected_area_variation()
        if av is None:
            self.lbl_reach.setText(
                "Pick an area variation in Stage 1 — the range depends on the room areas.")
            return
        from models.zone_model import reachable_range
        rr = reachable_range(self.store.inputs, av)
        if rr is None:
            self.lbl_reach.setText("No zoned rooms in the selected variation.")
            return
        pal = theme_mod.PALETTES.get(self.store.ui.theme, theme_mod.PALETTES["offwhite"])
        acc = "rgb(%d,%d,%d)" % tuple(pal["accent"])
        prefs = self.store.inputs.prefs
        q0, q1 = rr["qvci"]; a0, a1 = rr["aqi"]
        def mark(v, lo, hi):
            return "" if lo - 1e-9 <= v <= hi + 1e-9 else f" <span style='color:{acc};'>· target outside</span>"
        txt = (f"QVCI <b>{q0:.2f} – {q1:.2f}</b>{mark(prefs.target_qvci, q0, q1)}<br>"
               f"AQI <b>{a0:.2f} – {a1:.2f}</b>{mark(prefs.target_aqi, a0, a1)}")
        if rr.get("aqi_collapsed"):
            txt += (f"<br><span style='color:{acc};'>AQI cannot be changed on this site "
                    f"(reachable width {a1 - a0:.2f}): every façade gives about the same indoor level.</span>")
        elif not rr.get("aqi_live", True):
            txt += f"<br>AQI range is narrow ({a1 - a0:.2f}); view will dominate."
        cap = rr.get("capacity", {})
        txt += (f"<br><span style='opacity:0.7;'>{rr.get('n_zoned', 0)} zoned rooms · capacity "
                f"corner {cap.get('corner')} · edge {cap.get('cardinal')} · interior {cap.get('interior')}</span>")
        self.lbl_reach.setText(txt)

    def _build_capacity_section(self) -> _Section:
        """ZONE CAPACITY — the rooms-per-zone rule the model decodes under.

        Trained rule (98th percentile of real plans): corner ≤ 2 (3 for
        plans with ≥ 8 rooms), edge ≤ 3, interior ≤ 1 (2 for ≥ 8 rooms),
        interior for bathrooms only, never every room in one zone. The
        override is a developer / researcher control — the model was not
        trained with other caps, so results outside the rule are untested.
        """
        sec = _Section("ZONE CAPACITY",
                       "Rooms per zone · trained rule by plan size (advanced)",
                       expanded=False)
        prefs = self.store.inputs.prefs
        self._cap_hint = QLabel("")
        self._cap_hint.setProperty("sectionSubtitle", True)
        self._cap_hint.setWordWrap(True)
        sec.add(self._cap_hint)

        self.chk_cap_override = QCheckBox("Override the rule")
        self.chk_cap_override.setChecked(prefs.capacity_override is not None)
        self.chk_cap_override.toggled.connect(self._on_cap_override_toggled)
        sec.add(self.chk_cap_override)

        self.cap_spins: dict[str, QSpinBox] = {}
        grid = QGridLayout(); grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10); grid.setVerticalSpacing(4)
        ov = prefs.capacity_override or {}
        auto = zs.capacity(self.store.inputs.zoned_rooms)
        for i, (cls, label) in enumerate((("corner", "Corner zones"),
                                          ("cardinal", "Edge zones"),
                                          ("interior", "Interior zone"))):
            lbl = QLabel(label); lbl.setProperty("fieldLabel", True)
            sp = QSpinBox(); sp.setRange(1, 14); sp.setFixedWidth(60)
            sp.setValue(int(ov.get(cls, auto[cls])))
            sp.valueChanged.connect(lambda v, c=cls: self._on_cap_changed(c, int(v)))
            sp.setEnabled(prefs.capacity_override is not None)
            self.cap_spins[cls] = sp
            grid.addWidget(lbl, i, 0); grid.addWidget(sp, i, 1)
        grid.setColumnStretch(2, 1)
        sec.add_layout(grid)

        self.chk_interior = QCheckBox("Habitable rooms never interior (bathrooms only)")
        self.chk_interior.setChecked(bool(prefs.interior_bathrooms_only))
        self.chk_interior.toggled.connect(self._on_interior_rule)
        sec.add(self.chk_interior)
        return sec

    def _refresh_capacity_hint(self) -> None:
        if not hasattr(self, "_cap_hint"):
            return
        n = self.store.inputs.zoned_rooms
        auto = zs.capacity(n)
        self._cap_hint.setText(
            f"Trained rule for {n} zoned rooms: corner ≤ {auto['corner']}, edge ≤ {auto['cardinal']}, "
            f"interior ≤ {auto['interior']} (step at 8 rooms); never every room in one zone.")
        if self.store.inputs.prefs.capacity_override is None and hasattr(self, "cap_spins"):
            for cls, sp in self.cap_spins.items():
                sp.blockSignals(True); sp.setValue(int(auto[cls])); sp.blockSignals(False)

    def _on_cap_override_toggled(self, on: bool) -> None:
        if on:
            self.store.inputs.prefs.capacity_override = {
                cls: int(sp.value()) for cls, sp in self.cap_spins.items()}
        else:
            self.store.inputs.prefs.capacity_override = None
            self._refresh_capacity_hint()
        for sp in self.cap_spins.values():
            sp.setEnabled(bool(on))
        self._refresh_reachable()
        self._refresh_payload_debug()
        self.store.inputsChanged.emit()
        self.store.stateChanged.emit()

    def _on_cap_changed(self, cls: str, value: int) -> None:
        if self.store.inputs.prefs.capacity_override is None:
            return
        self.store.inputs.prefs.capacity_override[cls] = int(value)
        self._refresh_reachable()
        self._refresh_payload_debug()
        self.store.inputsChanged.emit()
        self.store.stateChanged.emit()

    def _on_interior_rule(self, on: bool) -> None:
        self.store.inputs.prefs.interior_bathrooms_only = bool(on)
        self._refresh_reachable()
        self._refresh_payload_debug()
        self.store.inputsChanged.emit()
        self.store.stateChanged.emit()

    def _build_generation_section(self) -> _Section:
        """GENERATION PARAMS — Num Tables / Temperature / Samples."""
        sec = _Section("GENERATION PARAMETERS",
                       "Plans shown · sampling pool · temperature",
                       expanded=False)
        params = self.store.inputs.params
        # Num Tables
        sec.add(self._spinbox_row(
            "Num Tables", params.n_plans, 1, 24, 1,
            on_change=lambda v: self._set_param("n_plans", int(v))))
        # Temperature
        sec.add(self._spinbox_row(
            "Temperature", params.temperature, 0.10, 1.50, 0.05,
            decimals=2,
            tooltip="Sampling temperature of the zone decoder. The paper's protocol used 0.8; "
                    "lower = closer to the greedy decode, higher = more diverse.",
            on_change=lambda v: self._set_param("temperature", float(v))))
        # Samples (zone-model sample pool)
        sec.add(self._spinbox_row(
            "Samples", params.zone_samples, 8, 400, 8,
            tooltip="Sampled arrangements per request (plus one greedy decode). "
                    "The paper reports best-of-12.",
            on_change=lambda v: self._set_param("zone_samples", int(v))))
        return sec

    # ── Slider/spinbox helpers ────────────────────────────────────
    def _slider_row(self, label: str, value: float,
                     lo: float, hi: float, step: float,
                     on_change, sublabel: str | None = None) -> QWidget:
        """label (· small sublabel underneath) · slider · value-readout."""
        row = QWidget(self)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)

        lbl = QLabel(label); lbl.setProperty("fieldLabel", True)
        lbl.setMinimumWidth(150)
        if sublabel:
            col = QVBoxLayout(); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(0)
            col.addWidget(lbl)
            sub = QLabel(sublabel); sub.setProperty("sectionSubtitle", True)
            sub.setMinimumWidth(150)
            col.addWidget(sub)
            h.addLayout(col)
        else:
            h.addWidget(lbl)

        n_steps = max(1, int(round((hi - lo) / step)))
        sl = QSlider(Qt.Horizontal)
        sl.setRange(0, n_steps)
        sl.setValue(int(round((value - lo) / step)))
        sl.setSingleStep(1)
        h.addWidget(sl, 1)

        val_lbl = QLabel(f"{value:.2f}")
        val_lbl.setProperty("fieldValue", True)
        val_lbl.setMinimumWidth(44)
        val_lbl.setAlignment(Qt.AlignRight)
        h.addWidget(val_lbl)

        def _on_slider(i: int) -> None:
            v = lo + i * step
            val_lbl.setText(f"{v:.2f}")
            on_change(v)
        sl.valueChanged.connect(_on_slider)
        return row

    def _spinbox_row(self, label: str, value: float,
                      lo: float, hi: float, step: float,
                      decimals: int = 0,
                      tooltip: str | None = None,
                      on_change=None) -> QWidget:
        """label · spinbox-with-arrows  (integer if decimals=0)."""
        row = QWidget(self)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)

        lbl = QLabel(label); lbl.setProperty("fieldLabel", True)
        lbl.setMinimumWidth(150)
        h.addWidget(lbl)
        h.addStretch(1)

        if decimals > 0:
            sp = QDoubleSpinBox()
            sp.setDecimals(decimals)
            sp.setRange(float(lo), float(hi))
            sp.setSingleStep(float(step))
            sp.setValue(float(value))
        else:
            sp = QSpinBox()
            sp.setRange(int(lo), int(hi))
            sp.setSingleStep(int(step))
            sp.setValue(int(value))
        sp.setFixedWidth(88)
        if tooltip:
            sp.setToolTip(tooltip)
        if on_change is not None:
            sp.valueChanged.connect(on_change)
        h.addWidget(sp)
        return row

    # ── Field mutators ────────────────────────────────────────────
    def _set_pref(self, key: str, value: float) -> None:
        setattr(self.store.inputs.prefs, key, float(value))
        self.store.inputsChanged.emit()
        self.store.stateChanged.emit()
        self._refresh_reachable()
        self._refresh_payload_debug()

    def _set_param(self, key: str, value) -> None:
        setattr(self.store.inputs.params, key, value)
        self.store.inputsChanged.emit()
        self.store.stateChanged.emit()
        self._refresh_payload_debug()

    # ───────────────────────────────────────────────────────────────
    # Assembled payload (debug)
    # ───────────────────────────────────────────────────────────────
    def _build_payload_debug(self) -> _Section:
        sec = _Section("ASSEMBLED PAYLOAD (DEBUG)",
                       "What will be sent to the backend",
                       expanded=False)
        self._payload_view = QPlainTextEdit(sec)
        self._payload_view.setReadOnly(True)
        font = QFont("JetBrains Mono", 10)
        font.setStyleHint(QFont.Monospace)
        self._payload_view.setFont(font)
        self._payload_view.setMinimumHeight(160)
        sec.add(self._payload_view)
        return sec

    def _refresh_payload_debug(self) -> None:
        if not hasattr(self, "_payload_view"):
            return
        d = self._assemble_payload()
        self._payload_view.setPlainText(json.dumps(d, indent=2))

    def _assemble_payload(self) -> dict:
        ip = self.store.inputs
        return {
            "model": "zone v13 (cGAT-AR, outcome-labelled corpus)",
            "room_counts": dict(ip.room_counts),
            "plan_geometry": {
                "width":         ip.plan_geometry.width,
                "height":        ip.plan_geometry.height,
                "units":         ip.plan_geometry.units,
                "px_per_m":      ip.plan_geometry.px_per_m,
                "total_area":    ip.plan_geometry.total_area,
                "total_area_m2": round(ip.plan_geometry.total_area_m2, 2),
            },
            "site_conditions": {
                d: {"view":   getattr(ip.site_conditions, d).view,
                    "L_dBA":  getattr(ip.site_conditions, d).spl_dB}
                for d in DIRECTIONS
            },
            "aspect_ratios": dict(ip.aspect_ratios),
            "wwr_planned":   dict(ip.wwr),
            "targets": {
                "qvci":    ip.prefs.target_qvci,
                "aqi":     ip.prefs.target_aqi,
                "enabled": ip.prefs.targets_enabled,
            },
            "capacity": {
                "override":                ip.prefs.capacity_override,
                "rule_for_plan":           zs.capacity(ip.zoned_rooms, ip.prefs.capacity_override),
                "interior_bathrooms_only": ip.prefs.interior_bathrooms_only,
            },
            "params": {
                "n_plans":      ip.params.n_plans,
                "samples_n":    ip.params.samples_n,
                "temperature":  ip.params.temperature,
                "zone_samples": ip.params.zone_samples,
            },
        }

    # ───────────────────────────────────────────────────────────────
    # Bottom bar
    # ───────────────────────────────────────────────────────────────
    def _build_bottom_bar(self) -> QFrame:
        bar = QFrame(self)
        bar.setObjectName("InputsBottomBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(8)

        self.btn_reset = QPushButton("Reset to defaults", bar)
        self.btn_reset.setProperty("linkBtn", True)
        self.btn_reset.setCursor(Qt.PointingHandCursor)
        self.btn_reset.clicked.connect(self._on_reset_defaults)

        self.btn_generate = QPushButton("Room program  →", bar)
        self.btn_generate.setProperty("pillRole", "primary")
        self.btn_generate.setCursor(Qt.PointingHandCursor)
        self.btn_generate.clicked.connect(self._on_generate_clicked)

        lay.addWidget(self.btn_reset)
        lay.addStretch(1)
        lay.addWidget(self.btn_generate)
        return bar

    def _on_reset_defaults(self) -> None:
        ans = QMessageBox.question(
            self, "Reset inputs",
            "Reset all inputs to defaults?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        self.store.set_inputs(InputsState())
        ip = self.store.inputs
        for rt, row in self._counter_rows.items():
            row.set_value(ip.room_counts.get(rt, 0))
        self.sp_width.setValue(ip.plan_geometry.width)
        self.sp_height.setValue(ip.plan_geometry.height)
        self.units_toggle.set_value(ip.plan_geometry.units)
        for d in DIRECTIONS:
            side = getattr(ip.site_conditions, d)
            self.view_combos[d].setCurrentText(side.view)
            self.spl_spins[d].setValue(side.spl_dB)
        self.sp_px_scale.setValue(ip.plan_geometry.px_per_m)
        self.chk_targets.setChecked(bool(ip.prefs.targets_enabled))
        self.chk_cap_override.setChecked(ip.prefs.capacity_override is not None)
        self.chk_interior.setChecked(bool(ip.prefs.interior_bathrooms_only))
        self._refresh_room_total()
        self._refresh_total_area()
        self._refresh_capacity_hint()
        self._refresh_reachable()
        self._refresh_payload_debug()

    def _on_generate_clicked(self) -> None:
        if self.store.inputs.total_rooms <= 0:
            QMessageBox.warning(
                self, "No rooms",
                "Add at least one room before generating.",
            )
            return
        if self.store.inputs.zoned_rooms <= 0:
            QMessageBox.warning(
                self, "No zoned rooms",
                "Add at least one living, kitchen, bedroom or bathroom — "
                "balconies alone cannot be zoned.",
            )
            return
        if self.store.inputs.zoned_rooms > 14:
            QMessageBox.warning(
                self, "Too many rooms",
                "The v13 zone model was trained on plans with up to 14 zoned rooms.",
            )
            return
        self.store.set_active_stage("area")
        self.generateRequested.emit()

    # ───────────────────────────────────────────────────────────────
    # Theme reactions
    # ───────────────────────────────────────────────────────────────
    def _on_theme_changed(self, _name: str) -> None:
        """Force the compass to repaint with new palette colors."""
        if hasattr(self, "_compass"):
            self._compass.update()
        self._refresh_reachable()
