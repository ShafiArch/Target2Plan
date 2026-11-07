"""
ParetoView — 2D scatter plot of (QVCI, AQI) for a set of schematics.

Each schematic is one dot. The Pareto-dominant frontier (no other
schematic strictly dominates the point in both QVCI and AQI) is
connected with a pink polyline and dots on the front are filled
pink. Non-dominant dots are filled ink2.

Interactions:
  * hover a dot → it grows + label appears + ``dotHovered`` signal
  * click a dot → ``dotClicked(plan_index)`` signal
"""

from __future__ import annotations

import math
from typing import List, Optional, Set

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QMouseEvent, QPainter, QPen,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from app import theme as theme_mod
from models.schematic_mock import pareto_front_indices


def _theme_palette(widget: QWidget) -> dict:
    top = widget.window()
    if top is not None and hasattr(top, "store"):
        return theme_mod.PALETTES.get(
            top.store.ui.theme, theme_mod.PALETTES["offwhite"])
    return theme_mod.PALETTES["offwhite"]


class ParetoView(QWidget):
    """Custom-painted Pareto-front scatter."""

    LEFT_MARGIN   = 60
    RIGHT_MARGIN  = 24
    TOP_MARGIN    = 18
    BOTTOM_MARGIN = 30
    DOT_HIT_PX    = 9
    PLOT_HEIGHT   = 220

    dotClicked = Signal(int)   # plan_index
    dotHovered = Signal(int)   # plan_index, or -1 for None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._schematics: List[dict] = []
        self._selected: Set[int] = set()
        self._hover_idx: Optional[int] = None
        # Background dots — full Stage-2 sample pool. List of dicts
        # with "qvci" and "aqi" keys (plan_index optional). Drawn
        # behind the foreground schematic dots, in a faded grey.
        self._bg_points: List[dict] = []
        # Cached auto-fit data bounds (q_lo, q_hi, a_lo, a_hi). Lazily
        # computed by _data_bounds() and invalidated whenever the
        # schematics or background points change.
        self._cached_bounds: Optional[tuple[float, float, float, float]] = None
        # Render-size override — set transiently by ``save_image`` so
        # the existing paint code draws into an off-screen QImage at
        # high resolution without affecting on-screen rendering.
        self._override_w: Optional[int] = None
        self._override_h: Optional[int] = None
        self.setMinimumHeight(self.PLOT_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)

    # ── public API ─────────────────────────────────────────────────
    def set_schematics(self, schematics: List[dict]) -> None:
        self._schematics = schematics or []
        self._cached_bounds = None
        self.update()

    def set_background_points(self, points: List[dict]) -> None:
        """All-samples background scatter (Stage-2 full pool).

        Each point is a dict with ``qvci`` and ``aqi`` floats. Drawn
        as small faded grey dots behind the foreground schematic dots,
        so the user can see where the selected schematics sit within
        the full sampled distribution.
        """
        self._bg_points = points or []
        self._cached_bounds = None
        self.update()

    # ── export ────────────────────────────────────────────────────
    def save_image(self, path: str, size: int = 2400,
                    background: Optional[str] = "white") -> bool:
        """Render the Pareto plot to a high-resolution PNG / JPG.

        Approach: tell the existing paint code that the widget is a
        square of ``DESIGN_SIZE`` design units, then apply
        ``QPainter.scale()`` so every drawing primitive — pen widths,
        font sizes, dot radii, label rects — magnifies uniformly to
        the export resolution. No per-primitive scaling needed.

        ``DESIGN_SIZE`` is intentionally larger than ``PLOT_HEIGHT``
        (the on-screen reference). A larger design canvas makes the
        fixed-pixel margins, font sizes, and pen widths look
        *proportionally smaller* relative to the plot — i.e., the
        plot dominates the figure the way a thesis chart should,
        rather than its axis labels.

        ``size`` is the image side in pixels (square). 2400 px ≈ 4″
        at 600 dpi — comfortable for thesis figures and slide decks.
        """
        from PySide6.QtGui import QImage, QPainter, QColor
        from PySide6.QtCore import Qt

        DESIGN_SIZE = 300.0
        scale = float(size) / DESIGN_SIZE

        img = QImage(size, size, QImage.Format_ARGB32)
        img.fill(QColor(background) if background else Qt.transparent)

        saved_hover = self._hover_idx
        self._hover_idx = None
        self._override_w = int(DESIGN_SIZE)
        self._override_h = int(DESIGN_SIZE)
        try:
            painter = QPainter(img)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.TextAntialiasing)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.scale(scale, scale)
            self._render_into(painter)
            painter.end()
        finally:
            self._override_w = None
            self._override_h = None
            self._hover_idx = saved_hover
            self.update()

        return bool(img.save(path))

    def save_csv(self, path: str) -> bool:
        """Dump every Pareto point to CSV — selected schematics and the
        full background sample pool — so the user can re-plot in Excel,
        R, Python, etc.

        Schema (one row per point):

            type        "foreground" = selected schematic (a card on
                        the grid); "background" = a sample from the
                        full Stage-2 pool (one row per requested sample,
                        duplicates included).
            index       For foreground: ``plan_index``. For background:
                        the sample's position in the pool.
            qvci, aqi   The plotted coordinates (0–1).
            concentration   Model-reported concentration (0–1).
            is_pareto_dominant  True iff this foreground point lies on
                        the Pareto frontier. Blank for background.
            is_selected  True iff this foreground card is in the user's
                        current selection. Blank for background.
        """
        import csv
        try:
            dom_set = set(pareto_front_indices(self._schematics))
        except Exception:
            dom_set = set()
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["type", "index", "qvci", "aqi",
                             "concentration", "is_pareto_dominant",
                             "is_selected"])
                for s in self._schematics:
                    idx = int(s.get("plan_index", -1))
                    c = s.get("computed", {}) if isinstance(s, dict) else {}
                    w.writerow([
                        "foreground", idx,
                        f"{float(c.get('qvci', 0.0)):.6f}",
                        f"{float(c.get('aqi',  0.0)):.6f}",
                        f"{float(c.get('concentration', 0.0)):.6f}",
                        "true" if idx in dom_set else "false",
                        "true" if idx in self._selected else "false",
                    ])
                for i, pt in enumerate(self._bg_points):
                    w.writerow([
                        "background", i,
                        f"{float(pt.get('qvci', 0.0)):.6f}",
                        f"{float(pt.get('aqi',  0.0)):.6f}",
                        f"{float(pt.get('concentration', 0.0)):.6f}",
                        "", "",
                    ])
            return True
        except OSError as ex:
            print(f"[pareto_view] save_csv failed: {ex}")
            return False

    def set_selected(self, idxs) -> None:
        self._selected = set(idxs or [])
        self.update()

    def set_hover_idx(self, idx: Optional[int]) -> None:
        if self._hover_idx != idx:
            self._hover_idx = idx
            self.update()

    # ── render-size override (for high-resolution export) ─────────
    def _render_w(self) -> int:
        """Effective render width — either the export override (set by
        ``save_image`` during off-screen render) or the live widget size."""
        return self._override_w if self._override_w is not None else self.width()

    def _render_h(self) -> int:
        return self._override_h if self._override_h is not None else self.height()

    # ── geometry ──────────────────────────────────────────────────
    def _plot_geom(self) -> tuple[float, float, float]:
        """Return ``(plot_x, plot_y, side)`` for a SQUARE plot area
        centered horizontally inside the widget.

        The plot is square in *pixels* so the QVCI and AQI axes are
        visually the same length. The mapping from data → pixels is
        handled separately by ``_x_for_q`` / ``_y_for_a``, which
        auto-fit each axis to the data range.
        """
        avail_w = self._render_w() - self.LEFT_MARGIN - self.RIGHT_MARGIN
        avail_h = self._render_h() - self.TOP_MARGIN  - self.BOTTOM_MARGIN
        side    = max(0.0, float(min(avail_w, avail_h)))
        plot_x  = self.LEFT_MARGIN + (avail_w - side) / 2.0
        plot_y  = float(self.TOP_MARGIN)
        return plot_x, plot_y, side

    def _data_bounds(self) -> tuple[float, float, float, float]:
        """Return ``(q_lo, q_hi, a_lo, a_hi)`` auto-fit to current data.

        Bounds are padded by 10 % of the data range (minimum ±0.02),
        clamped to ``[0, 1]``, and widened to at least 0.10 on each
        axis so near-identical points don't crush together. With no
        data, the full ``[0, 1] × [0, 1]`` range is returned.
        """
        if self._cached_bounds is not None:
            return self._cached_bounds

        qs: List[float] = []
        as_: List[float] = []
        for s in self._schematics:
            c = s.get("computed", {}) if isinstance(s, dict) else {}
            qs.append(float(c.get("qvci", 0.0)))
            as_.append(float(c.get("aqi", 0.0)))
        for pt in self._bg_points:
            qs.append(float(pt.get("qvci", 0.0)))
            as_.append(float(pt.get("aqi", 0.0)))

        if not qs:
            self._cached_bounds = (0.0, 1.0, 0.0, 1.0)
            return self._cached_bounds

        def _fit(vals: List[float]) -> tuple[float, float]:
            lo, hi = min(vals), max(vals)
            pad = max(0.02, (hi - lo) * 0.10)
            lo = max(0.0, lo - pad)
            hi = min(1.0, hi + pad)
            # Ensure a minimum visible range so the dots aren't crushed.
            if hi - lo < 0.10:
                centre = (lo + hi) / 2.0
                lo = max(0.0, centre - 0.05)
                hi = min(1.0, centre + 0.05)
                # If still narrow (because clamping at 0 or 1 ate
                # padding), expand against the opposite boundary.
                if hi - lo < 0.10:
                    if lo <= 0.0:
                        hi = min(1.0, lo + 0.10)
                    else:
                        lo = max(0.0, hi - 0.10)
            return lo, hi

        q_lo, q_hi = _fit(qs)
        a_lo, a_hi = _fit(as_)
        self._cached_bounds = (q_lo, q_hi, a_lo, a_hi)
        return self._cached_bounds

    @staticmethod
    def _nice_ticks(lo: float, hi: float, target: int = 4) -> List[float]:
        """Return ``target±1`` 'nice' tick values within ``[lo, hi]``.

        Uses a 1-2-5 progression so steps come out as 0.01, 0.02,
        0.05, 0.10, 0.20, 0.25, 0.50… — the standard chart axis
        sequence that doesn't produce ugly fractions.
        """
        if hi <= lo:
            return [lo]
        span = hi - lo
        raw_step = span / max(1, target - 1)
        if raw_step <= 0:
            return [lo, hi]
        mag = 10.0 ** math.floor(math.log10(raw_step))
        norm = raw_step / mag
        if   norm < 1.5: step = 1.0 * mag
        elif norm < 3.5: step = 2.0 * mag
        elif norm < 7.5: step = 5.0 * mag
        else:            step = 10.0 * mag
        start = math.ceil(lo / step - 1e-9) * step
        ticks: List[float] = []
        v = start
        # Tolerance avoids floating-point miss at the right edge.
        while v <= hi + step * 1e-6:
            ticks.append(round(v, 10))
            v += step
        return ticks

    def _x_for_q(self, q: float) -> float:
        plot_x, _, side = self._plot_geom()
        q_lo, q_hi, _, _ = self._data_bounds()
        t = 0.5 if q_hi <= q_lo else (q - q_lo) / (q_hi - q_lo)
        t = max(0.0, min(1.0, t))
        return plot_x + t * side

    def _y_for_a(self, a: float) -> float:
        _, plot_y, side = self._plot_geom()
        _, _, a_lo, a_hi = self._data_bounds()
        t = 0.5 if a_hi <= a_lo else (a - a_lo) / (a_hi - a_lo)
        t = max(0.0, min(1.0, t))
        # AQI high at top, AQI low at bottom
        return plot_y + side - t * side

    def _dot_at(self, mx: float, my: float) -> Optional[int]:
        best = None
        best_d = float(self.DOT_HIT_PX)
        for s in self._schematics:
            idx = int(s["plan_index"])
            q = float(s.get("computed", {}).get("qvci", 0.0))
            a = float(s.get("computed", {}).get("aqi", 0.0))
            x = self._x_for_q(q)
            y = self._y_for_a(a)
            d = math.hypot(mx - x, my - y)
            if d < best_d:
                best_d = d
                best = idx
        return best

    # ── paint ─────────────────────────────────────────────────────
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        self._render_into(p)
        p.end()

    def _render_into(self, p: QPainter) -> None:
        """All drawing code. Called from ``paintEvent`` with a live
        painter on the widget, and from ``save_image`` with a painter
        on an off-screen QImage. The geometry helpers (`_plot_geom`,
        `_x_for_q`, `_y_for_a`) consult ``_override_w/h`` so the same
        code path works at any resolution.
        """
        pal = _theme_palette(self)
        ink     = QColor(*pal["ink"])
        ink2    = QColor(*pal["ink2"])
        ink3    = QColor(*pal["ink3"])
        border  = QColor(*pal["border"])
        accent  = QColor(*pal["accent"])
        surface = QColor(*pal["surface"])

        p.setRenderHint(QPainter.Antialiasing)

        plot_x, plot_y, side = self._plot_geom()
        plot_w = side
        plot_h = side

        q_lo, q_hi, a_lo, a_hi = self._data_bounds()
        q_ticks = self._nice_ticks(q_lo, q_hi)
        a_ticks = self._nice_ticks(a_lo, a_hi)

        # ── 1. Grid lines (faint) — interior ticks only ───────────
        grid_pen = QPen(border, 0.5)
        grid_pen.setColor(QColor(border.red(), border.green(),
                                  border.blue(), 70))
        p.setPen(grid_pen)
        edge_tol = max(1e-6, (q_hi - q_lo) * 1e-3)
        for v in q_ticks:
            if v <= q_lo + edge_tol or v >= q_hi - edge_tol:
                continue
            x = self._x_for_q(v)
            p.drawLine(QPointF(x, plot_y), QPointF(x, plot_y + plot_h))
        edge_tol = max(1e-6, (a_hi - a_lo) * 1e-3)
        for v in a_ticks:
            if v <= a_lo + edge_tol or v >= a_hi - edge_tol:
                continue
            y = self._y_for_a(v)
            p.drawLine(QPointF(plot_x, y), QPointF(plot_x + plot_w, y))

        # ── 2. Axes ────────────────────────────────────────────────
        axis_col = QColor(70, 70, 70)
        p.setPen(QPen(axis_col, 1.3))
        p.drawLine(QPointF(plot_x, plot_y),
                   QPointF(plot_x, plot_y + plot_h))
        p.drawLine(QPointF(plot_x, plot_y + plot_h),
                   QPointF(plot_x + plot_w, plot_y + plot_h))

        # ── 2b. Tick marks (short, at each labelled tick) ─────────
        p.setPen(QPen(axis_col, 1.0))
        TL = 4.0
        for v in q_ticks:
            x = self._x_for_q(v)
            p.drawLine(QPointF(x, plot_y + plot_h),
                       QPointF(x, plot_y + plot_h + TL))
        for v in a_ticks:
            y = self._y_for_a(v)
            p.drawLine(QPointF(plot_x - TL, y), QPointF(plot_x, y))

        # ── 3. Axis labels ─────────────────────────────────────────
        f_label = QFont(); f_label.setPointSize(9); f_label.setBold(True)
        f_label.setLetterSpacing(QFont.PercentageSpacing, 110)
        p.setFont(f_label)
        p.setPen(QPen(ink))
        # X label
        p.drawText(QRectF(plot_x, plot_y + plot_h + 14,
                           plot_w, 14),
                   Qt.AlignCenter, "QVCI →")
        # Y label (rotated)
        p.save()
        p.translate(plot_x - 48, plot_y + plot_h / 2)
        p.rotate(-90)
        p.drawText(QRectF(-50, -8, 100, 16),
                   Qt.AlignCenter, "AQI →")
        p.restore()

        # ── 4. Tick labels ─────────────────────────────────────────
        f_tick = QFont("JetBrains Mono"); f_tick.setPointSize(8)
        f_tick.setStyleHint(QFont.Monospace)
        p.setFont(f_tick)
        p.setPen(QPen(QColor(80, 80, 80)))
        for v in q_ticks:
            x = self._x_for_q(v)
            p.drawText(QRectF(x - 22, plot_y + plot_h + 2, 44, 12),
                       Qt.AlignCenter, f"{v:.2f}")
        for v in a_ticks:
            y = self._y_for_a(v)
            p.drawText(QRectF(plot_x - 38, y - 6, 32, 12),
                       Qt.AlignRight | Qt.AlignVCenter, f"{v:.2f}")

        # ── 5. Pareto-dominant frontier ────────────────────────────
        dom_idxs = pareto_front_indices(self._schematics)
        dom_set = set(dom_idxs)
        # Coordinates of dominant points, sorted by QVCI ascending
        dom_pts: List[QPointF] = []
        for idx in dom_idxs:
            s = next((ss for ss in self._schematics
                      if int(ss["plan_index"]) == idx), None)
            if s is None:
                continue
            q = float(s["computed"].get("qvci", 0.0))
            a = float(s["computed"].get("aqi", 0.0))
            dom_pts.append(QPointF(self._x_for_q(q), self._y_for_a(a)))

        if len(dom_pts) >= 2:
            pen = QPen(accent, 1.8)
            pen.setStyle(Qt.DashLine)
            pen.setDashPattern([4, 3])
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            for i in range(len(dom_pts) - 1):
                p.drawLine(dom_pts[i], dom_pts[i + 1])

        # ── 5b. Background sample-pool dots (faded grey) ──────────
        # Drawn before the foreground so the selected schematics stay
        # on top. We plot EVERY sample (no filtering) so the user sees
        # all 80 / 100 / 200 dots they requested.
        #
        # The trained model is often "decisive" at low temperature —
        # 80 samples can collapse onto a handful of unique zone tuples
        # and therefore onto a handful of (qvci, aqi) coordinates. To
        # make the requested sample count *visible*, we add a wider
        # deterministic jitter (±5 px) around the true coordinate.
        # That turns N coincident samples into a clearly-visible
        # cloud of dots while keeping a meaningful spatial relationship
        # to the actual metric value. The dots are also smaller and a
        # touch more transparent than the foreground markers, so the
        # selected schematics still dominate visually.
        if self._bg_points:
            faded = QColor(120, 120, 120, 215)   # mid-grey, clearly visible
            p.setBrush(QBrush(faded))
            p.setPen(QPen(QColor(0, 0, 0, 0), 0))
            for i, pt in enumerate(self._bg_points):
                q = float(pt.get("qvci", 0.0))
                a = float(pt.get("aqi", 0.0))
                # ONE-SIDED jitter: stable hash → spread only into the
                # "lower QVCI + lower AQI" quadrant relative to the
                # true coordinate. This guarantees a duplicate sample
                # never appears better (higher Q *and* higher A) than
                # the schematic the user actually selected, so the
                # Pareto-dominant card always reads as the best of its
                # cluster.
                #
                # On screen: lower QVCI = LEFT on the x-axis → jx ≤ 0.
                #            lower AQI  = DOWN on the y-axis (Qt y
                #            grows downward, AQI=1 is at the top)
                #            → jy ≥ 0.
                h = (i * 2654435761) & 0xFFFFFFFF
                jx = -((h         & 0xFFFF) / 65535.0) * 10.0  # ∈ [-10, 0]
                jy =  (((h >> 16) & 0xFFFF) / 65535.0) * 10.0  # ∈ [0, +10]
                x = self._x_for_q(q) + jx
                y = self._y_for_a(a) + jy
                # Keep the jittered dot fully inside the plot box — the
                # one-sided jitter (down-left) could otherwise push points
                # near the origin below the x-axis or left of the y-axis.
                r_bg = 1.8
                x = min(max(x, plot_x + r_bg), plot_x + plot_w - r_bg)
                y = min(max(y, plot_y + r_bg), plot_y + plot_h - r_bg)
                p.drawEllipse(QPointF(x, y), r_bg, r_bg)

        # ── 6. Dots ────────────────────────────────────────────────
        for s in self._schematics:
            idx = int(s["plan_index"])
            q = float(s["computed"].get("qvci", 0.0))
            a = float(s["computed"].get("aqi", 0.0))
            x = self._x_for_q(q)
            y = self._y_for_a(a)

            is_dom = idx in dom_set
            is_sel = idx in self._selected
            is_hov = idx == self._hover_idx

            r = 5.5 if (is_hov or is_sel) else 3.5
            if is_hov:
                r = 7.0

            if is_dom:
                p.setBrush(QBrush(accent))
                p.setPen(QPen(surface, 1.5))
            elif is_sel:
                fill = QColor(accent); fill.setAlphaF(0.55)
                p.setBrush(QBrush(fill))
                p.setPen(QPen(accent, 1.2))
            else:
                p.setBrush(QBrush(ink2))
                p.setPen(QPen(surface, 1.0))
            p.drawEllipse(QPointF(x, y), r, r)

        # ── 7. Hover label ─────────────────────────────────────────
        if self._hover_idx is not None:
            s = next((ss for ss in self._schematics
                      if int(ss["plan_index"]) == self._hover_idx), None)
            if s is not None:
                q = float(s["computed"].get("qvci", 0.0))
                a = float(s["computed"].get("aqi", 0.0))
                x = self._x_for_q(q)
                y = self._y_for_a(a)
                label = (f"#{int(self._hover_idx):02d}   "
                         f"QVCI {q:.3f}   AQI {a:.3f}")
                p.setFont(f_tick)
                # Background pill
                w_text = p.fontMetrics().horizontalAdvance(label) + 10
                pill_x = x + 9
                pill_y = y - 14
                if pill_x + w_text > self._render_w() - 6:
                    pill_x = x - w_text - 9
                bg = QColor(surface); bg.setAlphaF(0.92)
                p.setBrush(QBrush(bg))
                p.setPen(QPen(accent, 1.0))
                p.drawRoundedRect(QRectF(pill_x, pill_y, w_text, 16),
                                   3, 3)
                p.setPen(QPen(ink))
                p.drawText(QRectF(pill_x, pill_y, w_text, 16),
                           Qt.AlignCenter, label)

    # ── mouse ─────────────────────────────────────────────────────
    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        idx = self._dot_at(e.position().x(), e.position().y())
        if idx != self._hover_idx:
            self._hover_idx = idx
            self.dotHovered.emit(idx if idx is not None else -1)
            self.update()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.LeftButton:
            return
        idx = self._dot_at(e.position().x(), e.position().y())
        if idx is not None:
            self.dotClicked.emit(idx)

    def leaveEvent(self, _e) -> None:
        if self._hover_idx is not None:
            self._hover_idx = None
            self.dotHovered.emit(-1)
            self.update()
