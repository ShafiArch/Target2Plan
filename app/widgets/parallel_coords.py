"""
Parallel-coordinates plot.

A vertical axis per metric; each plan is one polyline. Port of the
web app's ``ParallelCoords.jsx``:

  * hover a polyline → it lifts to the foreground in accent color
    + dots appear at every axis intersection
  * click a polyline → emits ``lineClicked(table_index)``
  * drag on an axis → set a ``[min, max]`` brush; polylines outside
    fade. Click an axis without dragging → clear that axis's brush.
  * "Clear filters" button + "N/M plans pass" summary
  * hovered-value readout strip at the bottom

Layout:

    ┌─────────────────────────────────────────────────────────┐
    │  N/M plans pass current filters       [Clear filters]   │  summary row
    ├─────────────────────────────────────────────────────────┤
    │  QVCI    AQI   Δtgt   dB(A)   Z-spr   Fullest           │
    │   │      │      │      │      │       │       │         │
    │   │      │      │      │      │       │       │         │  plot
    │   │      │      │      │      │       │       │         │
    ├─────────────────────────────────────────────────────────┤
    │  #07  QVCI: 0.51 · AQI: 0.09 · …                        │  readout
    └─────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QMouseEvent, QPainter, QPen,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
    QWidget,
)

from app import theme as theme_mod


# ─────────────────────────────────────────────────────────────────────
#  Axis specs
# ─────────────────────────────────────────────────────────────────────

@dataclass
class _Axis:
    key:    str
    label:  str
    domain: Tuple[float, float]
    fmt:    Callable[[float], str]
    extract: Callable[[dict], float]


def _fmt_3(v: float) -> str: return f"{v:.3f}"
def _fmt_2(v: float) -> str: return f"{v:.2f}"
def _fmt_dB(v: float) -> str: return f"{v:.0f}"
def _fmt_int(v: float) -> str: return f"{int(round(v))}"


AXES: List[_Axis] = [
    _Axis("qvci",          "QVCI",          (0.0, 1.0), _fmt_3,
          lambda t: t["computed"]["qvci"]),
    _Axis("aqi",           "AQI",           (0.0, 1.0), _fmt_3,
          lambda t: t["computed"]["aqi"]),
    _Axis("d_target",      "Δ target",      (0.0, 1.0), _fmt_3,
          lambda t: float(t["computed"].get("d_target") or 0.0)),
    _Axis("avg_spl_dB",    "Indoor dB(A)",  (20.0, 70.0), _fmt_dB,
          lambda t: t["computed"]["avg_spl_dB"]),
    _Axis("zone_spread",   "Zone spread",   (1.0, 9.0), _fmt_int,
          lambda t: float(t["computed"]["zone_spread"])),
    _Axis("concentration", "Fullest zone",  (0.0, 1.0), _fmt_2,
          lambda t: t["computed"]["concentration"]),
]
AXES_BY_KEY = {a.key: a for a in AXES}


# ─────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────

def _distance_to_segment(px: float, py: float,
                         x1: float, y1: float,
                         x2: float, y2: float) -> float:
    """Distance from point (px, py) to segment (x1,y1)-(x2,y2)."""
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    nx = x1 + t * dx
    ny = y1 + t * dy
    return math.hypot(px - nx, py - ny)


def _theme_palette(widget: QWidget) -> dict:
    top = widget.window()
    if top is not None and hasattr(top, "store"):
        return theme_mod.PALETTES.get(
            top.store.ui.theme, theme_mod.PALETTES["offwhite"])
    return theme_mod.PALETTES["offwhite"]


# ─────────────────────────────────────────────────────────────────────
#  _ParcoordsPlot — the custom-painted middle widget
# ─────────────────────────────────────────────────────────────────────

class _ParcoordsPlot(QWidget):
    """Inner plot widget. Owns axes, polylines, brush drag state."""

    PLOT_HEIGHT   = 180
    TOP_MARGIN    = 24
    BOTTOM_MARGIN = 28
    LEFT_MARGIN   = 28
    RIGHT_MARGIN  = 28
    AXIS_HIT_PX   = 10    # ±x to count as "on the axis"
    LINE_HIT_PX   = 6     # max distance to count as "on a polyline"

    lineClicked  = Signal(int)            # table_index
    lineHovered  = Signal(int)            # table_index, or -1 for None
    brushesChanged = Signal()             # any brush state changed

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tables: List[dict] = []
        self._selected_idxs: set[int] = set()
        self._hover_idx: Optional[int] = None
        self._brushes: Dict[str, Optional[Tuple[float, float]]] = {
            a.key: None for a in AXES
        }
        self._brush_drag: Optional[dict] = None  # {axis_key, y0, y1}
        self.setMouseTracking(True)
        self.setMinimumHeight(self.PLOT_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    # ── public API ─────────────────────────────────────────────────
    def set_tables(self, tables: List[dict]) -> None:
        self._tables = tables or []
        self.update()

    def set_selected_idxs(self, idxs) -> None:
        self._selected_idxs = set(idxs or [])
        self.update()

    def set_hover_idx(self, idx: Optional[int]) -> None:
        if self._hover_idx != idx:
            self._hover_idx = idx
            self.update()

    def brushes(self) -> Dict[str, Optional[Tuple[float, float]]]:
        return dict(self._brushes)

    def has_any_brush(self) -> bool:
        return any(b is not None for b in self._brushes.values())

    def clear_all_brushes(self) -> None:
        self._brushes = {a.key: None for a in AXES}
        self.brushesChanged.emit()
        self.update()

    def passing_count(self) -> int:
        return sum(1 for t in self._tables if self._passes_brushes(t))

    # ── geometry ───────────────────────────────────────────────────
    def _axis_xs(self) -> List[float]:
        w = self.width()
        avail = w - self.LEFT_MARGIN - self.RIGHT_MARGIN
        n = len(AXES)
        if n <= 1:
            return [self.LEFT_MARGIN + avail / 2]
        step = avail / (n - 1)
        return [self.LEFT_MARGIN + i * step for i in range(n)]

    def _y_for(self, value: float, axis: _Axis) -> float:
        lo, hi = axis.domain
        span = max(hi - lo, 1e-9)
        t = (value - lo) / span
        t = max(0.0, min(1.0, t))
        plot_top = self.TOP_MARGIN
        plot_bot = self.height() - self.BOTTOM_MARGIN
        # Top is the high value (inverted)
        return plot_bot - t * (plot_bot - plot_top)

    def _value_at(self, y: float, axis: _Axis) -> float:
        lo, hi = axis.domain
        plot_top = self.TOP_MARGIN
        plot_bot = self.height() - self.BOTTOM_MARGIN
        t = (plot_bot - y) / max(plot_bot - plot_top, 1e-9)
        t = max(0.0, min(1.0, t))
        return lo + t * (hi - lo)

    def _passes_brushes(self, table: dict) -> bool:
        for axis in AXES:
            b = self._brushes.get(axis.key)
            if b is None:
                continue
            v = axis.extract(table)
            if v < b[0] or v > b[1]:
                return False
        return True

    # ── painting ───────────────────────────────────────────────────
    def paintEvent(self, _e) -> None:
        pal = _theme_palette(self)
        ink     = QColor(*pal["ink"])
        ink2    = QColor(*pal["ink2"])
        ink3    = QColor(*pal["ink3"])
        accent  = QColor(*pal["accent"])
        border  = QColor(*pal["border"])
        surface = QColor(*pal["surface"])

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        xs = self._axis_xs()
        plot_top = self.TOP_MARGIN
        plot_bot = self.height() - self.BOTTOM_MARGIN

        # ── 1. Axes ────────────────────────────────────────────────
        f_label = QFont(); f_label.setPointSize(8); f_label.setBold(True)
        f_tick  = QFont("JetBrains Mono"); f_tick.setPointSize(7)
        f_tick.setStyleHint(QFont.Monospace)

        for i, axis in enumerate(AXES):
            x = xs[i]
            p.setPen(QPen(border, 1.0))
            p.drawLine(QPointF(x, plot_top), QPointF(x, plot_bot))

            # Axis label
            p.setFont(f_label)
            p.setPen(QPen(ink))
            p.drawText(QRectF(x - 40, plot_top - 18, 80, 12),
                       Qt.AlignHCenter | Qt.AlignVCenter, axis.label)

            # Max tick (top), min tick (bottom)
            p.setFont(f_tick)
            p.setPen(QPen(ink3))
            p.drawText(QRectF(x - 25, plot_top - 6, 50, 10),
                       Qt.AlignHCenter | Qt.AlignVCenter,
                       axis.fmt(axis.domain[1]))
            p.drawText(QRectF(x - 25, plot_bot + 2, 50, 10),
                       Qt.AlignHCenter | Qt.AlignVCenter,
                       axis.fmt(axis.domain[0]))

            # Brush rectangle (committed or currently dragging)
            brush = self._brushes.get(axis.key)
            if self._brush_drag and self._brush_drag["axis_key"] == axis.key:
                y0 = min(self._brush_drag["y0"], self._brush_drag["y1"])
                y1 = max(self._brush_drag["y0"], self._brush_drag["y1"])
                fill = QColor(accent); fill.setAlphaF(0.25)
                p.setBrush(QBrush(fill))
                p.setPen(QPen(accent, 1.0))
                p.drawRect(QRectF(x - 5, y0, 10, y1 - y0))
            elif brush is not None:
                y0 = self._y_for(brush[1], axis)
                y1 = self._y_for(brush[0], axis)
                fill = QColor(accent); fill.setAlphaF(0.15)
                p.setBrush(QBrush(fill))
                p.setPen(QPen(accent, 1.0))
                p.drawRect(QRectF(x - 5, y0, 10, y1 - y0))

        # ── 2. Polylines ────────────────────────────────────────────
        # Partition: failed-brush (very faint), passing-not-foreground,
        # foreground (hovered or selected, drawn last).
        fg_idxs = set(self._selected_idxs)
        if self._hover_idx is not None:
            fg_idxs.add(self._hover_idx)

        passing_bg, failing_bg, foreground = [], [], []
        for t in self._tables:
            ti = int(t["table_index"])
            passes = self._passes_brushes(t)
            if ti in fg_idxs:
                foreground.append((t, passes))
            elif passes:
                passing_bg.append(t)
            else:
                failing_bg.append(t)

        def build_path(t: dict) -> List[QPointF]:
            pts = []
            for i, axis in enumerate(AXES):
                pts.append(QPointF(xs[i], self._y_for(axis.extract(t), axis)))
            return pts

        # Failing brushes (drawn first, very faint)
        p.setPen(QPen(border, 1.0))
        pen_fail = QPen(border, 1.0)
        pen_fail.setColor(QColor(border.red(), border.green(),
                                  border.blue(), 75))
        p.setPen(pen_fail)
        for t in failing_bg:
            pts = build_path(t)
            for j in range(len(pts) - 1):
                p.drawLine(pts[j], pts[j + 1])

        # Passing (normal grey)
        pen_pass = QPen(QColor(ink2.red(), ink2.green(),
                                ink2.blue(), 128), 1.25)
        p.setPen(pen_pass)
        for t in passing_bg:
            pts = build_path(t)
            for j in range(len(pts) - 1):
                p.drawLine(pts[j], pts[j + 1])

        # Foreground — selected (thicker) and hover (thinner)
        for t, passes in foreground:
            ti = int(t["table_index"])
            is_sel = ti in self._selected_idxs
            pen = QPen(accent, 2.5 if is_sel else 2.0)
            if not passes:
                col = QColor(accent); col.setAlphaF(0.35)
                pen.setColor(col)
            p.setPen(pen)
            pts = build_path(t)
            for j in range(len(pts) - 1):
                p.drawLine(pts[j], pts[j + 1])

            # Dots at each axis (only for the hovered plan)
            if ti == self._hover_idx:
                p.setBrush(QBrush(accent))
                p.setPen(QPen(surface, 1.0))
                for pt in pts:
                    p.drawEllipse(pt, 3.5, 3.5)
        p.end()

    # ── mouse handling ─────────────────────────────────────────────
    def _axis_at(self, x: float) -> Optional[str]:
        xs = self._axis_xs()
        for i, ax in enumerate(AXES):
            if abs(x - xs[i]) <= self.AXIS_HIT_PX:
                return ax.key
        return None

    def _polyline_at(self, x: float, y: float) -> Optional[int]:
        if not self._tables:
            return None
        xs = self._axis_xs()
        best_idx: Optional[int] = None
        best_d = float(self.LINE_HIT_PX)
        for t in self._tables:
            ti = int(t["table_index"])
            for i in range(len(AXES) - 1):
                v1 = AXES[i].extract(t)
                v2 = AXES[i + 1].extract(t)
                y1 = self._y_for(v1, AXES[i])
                y2 = self._y_for(v2, AXES[i + 1])
                d = _distance_to_segment(x, y, xs[i], y1, xs[i + 1], y2)
                if d < best_d:
                    best_d = d
                    best_idx = ti
                    break
        return best_idx

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.LeftButton:
            return
        x = e.position().x()
        y = e.position().y()

        axis_key = self._axis_at(x)
        if axis_key is not None and self.TOP_MARGIN <= y <= self.height() - self.BOTTOM_MARGIN:
            self._brush_drag = {"axis_key": axis_key, "y0": y, "y1": y}
            self.update()
            return

        # Otherwise treat as polyline click
        idx = self._polyline_at(x, y)
        if idx is not None:
            self.lineClicked.emit(idx)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        x = e.position().x()
        y = e.position().y()

        if self._brush_drag is not None:
            self._brush_drag["y1"] = max(
                self.TOP_MARGIN,
                min(self.height() - self.BOTTOM_MARGIN, y),
            )
            self.update()
            return

        # Hover detection
        idx = self._polyline_at(x, y)
        if idx != self._hover_idx:
            self._hover_idx = idx
            self.lineHovered.emit(idx if idx is not None else -1)
            self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.LeftButton or self._brush_drag is None:
            return
        d = self._brush_drag
        dy = abs(d["y1"] - d["y0"])
        if dy < 4:
            # Treat as click → clear brush for this axis
            self._brushes[d["axis_key"]] = None
        else:
            axis = AXES_BY_KEY[d["axis_key"]]
            y_lo = min(d["y0"], d["y1"])
            y_hi = max(d["y0"], d["y1"])
            v_max = self._value_at(y_lo, axis)
            v_min = self._value_at(y_hi, axis)
            self._brushes[d["axis_key"]] = (v_min, v_max)
        self._brush_drag = None
        self.brushesChanged.emit()
        self.update()

    def leaveEvent(self, _e) -> None:
        if self._hover_idx is not None:
            self._hover_idx = None
            self.lineHovered.emit(-1)
            self.update()


# ─────────────────────────────────────────────────────────────────────
#  ParallelCoords — public widget (summary + plot + readout)
# ─────────────────────────────────────────────────────────────────────

class ParallelCoords(QWidget):
    """Parallel-coordinates widget. Read-only display + brush filters."""

    lineClicked = Signal(int)
    lineHovered = Signal(int)   # -1 means "nothing hovered"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tables: List[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Plot is built first so other subwidgets can reference it.
        self.plot = _ParcoordsPlot(self)
        self.plot.lineClicked.connect(self.lineClicked.emit)
        self.plot.lineHovered.connect(self._on_plot_hover)
        self.plot.brushesChanged.connect(self._refresh_summary)

        # Summary row
        self.summary = self._build_summary()
        root.addWidget(self.summary)

        root.addWidget(self.plot)

        # Readout
        self.readout = self._build_readout()
        root.addWidget(self.readout)

        self._refresh_summary()

    # ── public API ─────────────────────────────────────────────────
    def set_tables(self, tables: List[dict]) -> None:
        self._tables = tables or []
        self.plot.set_tables(self._tables)
        self._refresh_summary()

    def set_selected_idxs(self, idxs) -> None:
        self.plot.set_selected_idxs(idxs)

    def set_hover_idx(self, idx: Optional[int]) -> None:
        self.plot.set_hover_idx(idx)
        self._refresh_readout(idx)

    # ── subwidgets ─────────────────────────────────────────────────
    def _build_summary(self) -> QFrame:
        f = QFrame(self)
        f.setObjectName("ParcoordsSummary")
        h = QHBoxLayout(f); h.setContentsMargins(14, 4, 14, 4); h.setSpacing(8)
        self.summary_lbl = QLabel("")
        self.summary_lbl.setProperty("sectionSubtitle", True)
        self.btn_clear = QPushButton("Clear filters")
        self.btn_clear.setProperty("linkBtn", True)
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.setEnabled(False)
        self.btn_clear.clicked.connect(self.plot.clear_all_brushes)
        h.addWidget(self.summary_lbl)
        h.addStretch(1)
        h.addWidget(self.btn_clear)
        return f

    def _build_readout(self) -> QFrame:
        f = QFrame(self)
        f.setObjectName("ParcoordsReadout")
        h = QHBoxLayout(f); h.setContentsMargins(14, 2, 14, 4); h.setSpacing(10)
        self.readout_lbl = QLabel(
            "Hover a plan or polyline to see values · drag on an axis to filter")
        self.readout_lbl.setProperty("sectionSubtitle", True)
        font = QFont("JetBrains Mono"); font.setStyleHint(QFont.Monospace)
        font.setPointSize(8)
        self.readout_lbl.setFont(font)
        h.addWidget(self.readout_lbl)
        h.addStretch(1)
        return f

    # ── slots ──────────────────────────────────────────────────────
    def _on_plot_hover(self, idx_or_neg1: int) -> None:
        idx = None if idx_or_neg1 < 0 else int(idx_or_neg1)
        self._refresh_readout(idx)
        self.lineHovered.emit(idx_or_neg1)

    def _refresh_summary(self) -> None:
        n = len(self._tables)
        passing = self.plot.passing_count()
        self.summary_lbl.setText(
            f"{passing} / {n} plans pass current filters")
        self.btn_clear.setEnabled(self.plot.has_any_brush())

    def _refresh_readout(self, idx: Optional[int]) -> None:
        if idx is None or not self._tables:
            self.readout_lbl.setText(
                "Hover a plan or polyline to see values · "
                "drag on an axis to filter")
            return
        t = next((tt for tt in self._tables
                  if int(tt["table_index"]) == int(idx)), None)
        if t is None:
            return
        parts = [f"#{int(idx):02d}"]
        for axis in AXES:
            parts.append(f"{axis.label}: {axis.fmt(axis.extract(t))}")
        self.readout_lbl.setText("  ·  ".join(parts))
