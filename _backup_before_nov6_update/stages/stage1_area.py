"""
Stage 1 — Area Distribution.

Layout (mirrors zone_app screenshots, image 2):

    ┌─────────────────────────────────────────────────────────────┐
    │  [CTA: "Generate variations"]   or   [12-card grid]         │
    │                                                              │
    │  ▾  AREA SHARE COMPARISON · 12 vars · total NNN px² each    │
    │      Living  ▬▬▬  ▬▬   ▬▬▬  ...   (per-type rows × per-var) │
    │      Bedroom ▬▬   ▬▬▬  ▬▬   ...                              │
    │      ...                                                     │
    │      VARIATION    #00  #01  #02  #03 ...  #11                │
    ├─────────────────────────────────────────────────────────────┤
    │  Regenerate ·  [mocked pill]                Proceed →        │
    └─────────────────────────────────────────────────────────────┘

Selection: single-select. Click a thumbnail or a comparison column
to pick that variation. Selection is held in
``store.stage1_areas.selected_idx``; the "Proceed →" button enables
when it's non-None.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QPointF, QRectF, QSettings, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QMouseEvent, QPainter, QPen,
)
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFrame, QGraphicsOpacityEffect, QGridLayout,
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QVBoxLayout, QWidget,
)

from app import theme as theme_mod
from app.state import SessionStore
from models.area_mock import fit_natural_to_canvas, mock_area_variations
from models.area_model import (
    ModelNotAvailable, find_checkpoint, generate_area_variations,
)


# Types shown in the area-comparison strip (ordered for visual rhythm)
COMPARISON_TYPES = ["living", "bedroom", "kitchen", "bathroom", "balcony",
                    "dining", "storage", "study"]

ROOM_SHORT = {
    "living":  "liv", "bedroom":  "bed", "kitchen": "kit",
    "bathroom": "bat", "dining":  "din", "balcony": "bal",
    "storage": "sto", "study":   "stu",
    "stair":   "sta", "veranda": "ver", "garden":  "gar", "parking": "par",
}
ROOM_FULL = {
    "living": "Living",     "bedroom": "Bedroom",
    "kitchen": "Kitchen",   "bathroom": "Bathroom",
    "dining":  "Dining",    "balcony":  "Balcony",
    "storage": "Storage",   "study":    "Study",
}


def _set_prop(widget: QWidget, name: str, value) -> None:
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _theme_palette(widget: QWidget) -> dict:
    """Best-effort palette lookup from the top-level window."""
    top = widget.window()
    if top is not None and hasattr(top, "store"):
        return theme_mod.PALETTES.get(
            top.store.ui.theme, theme_mod.PALETTES["offwhite"])
    return theme_mod.PALETTES["offwhite"]


# ─────────────────────────────────────────────────────────────────────
#  _AreaCanvas — paints packed circles for one variation
# ─────────────────────────────────────────────────────────────────────

class _AreaCanvas(QWidget):
    """Bubble visualization of a variation's rooms (sized by area).

    Three runtime display toggles (driven from the stage toolbar):
      - ``show_labels``: draw room-type name in each circle large enough
      - ``solid_fill``:  fill circles with the room colour (else outline only)
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._variation: Optional[dict] = None
        self._show_labels: bool = True
        self._solid_fill:  bool = False
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_variation(self, variation: dict) -> None:
        self._variation = variation
        self.update()

    def set_show_labels(self, on: bool) -> None:
        if self._show_labels != bool(on):
            self._show_labels = bool(on)
            self.update()

    def set_solid_fill(self, on: bool) -> None:
        if self._solid_fill != bool(on):
            self._solid_fill = bool(on)
            self.update()

    def paintEvent(self, _e) -> None:
        if not self._variation:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        w, h = float(self.width()), float(self.height())
        positions_px, radii_px = fit_natural_to_canvas(
            self._variation["rooms"], w, h, pad=8.0,
        )

        # Theme palette for label colour (ink3 = same grey as info text)
        top = self.window()
        pal = (theme_mod.PALETTES.get(top.store.ui.theme,
                                       theme_mod.PALETTES["offwhite"])
                if top is not None and hasattr(top, "store")
                else theme_mod.PALETTES["offwhite"])
        # Outline mode reads better with a quiet grey; the filled
        # mode (used by the detail view) has saturated bubble colours
        # underneath the label, so the text needs to be white to stay
        # legible.
        if self._solid_fill:
            label_color = QColor(255, 255, 255)
        else:
            label_color = QColor(*pal["ink3"])
        # 7pt label font as the user requested (was 8pt).
        label_font  = QFont(); label_font.setPointSize(7)
        label_font.setBold(self._solid_fill)
        from PySide6.QtGui import QFontMetricsF
        fm = QFontMetricsF(label_font)

        for r in self._variation["rooms"]:
            rid = r["room_id"]
            if rid not in positions_px:
                continue
            cx, cy = positions_px[rid]
            rad = radii_px[rid]
            color = QColor(theme_mod.ROOM_COLORS.get(r["room_type"], "#9aa0a6"))

            if self._solid_fill:
                # Filled: brush in the room colour with slight alpha so
                # it reads as the muted palette the web app uses
                # (matches AreaThumbnail.jsx's fill-opacity treatment).
                faded = QColor(color)
                faded.setAlphaF(0.85)
                p.setBrush(QBrush(faded))
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(cx, cy), rad, rad)
            else:
                # Outline: thin coloured stroke (1.2 px) and a 1.5 px
                # radius shrink so two tangent circles never look like
                # they overlap. With a 1.6 px stroke at full radius the
                # anti-aliased edges merge into a fake "overlap" band.
                pen = QPen(color)
                pen.setWidthF(1.2)
                pen.setCosmetic(False)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                draw_r = max(0.5, rad - 1.5)
                p.drawEllipse(QPointF(cx, cy), draw_r, draw_r)

            # Room label (when enabled and the full word fits cleanly).
            if not self._show_labels:
                continue
            label = r["room_type"].capitalize()
            label_w = fm.horizontalAdvance(label)
            usable_w = 2 * rad - 4
            if label_w <= usable_w and rad >= 10:
                p.setPen(QPen(label_color))
                p.setFont(label_font)
                text_rect = QRectF(cx - rad, cy - 6, rad * 2, 12)
                p.drawText(text_rect, Qt.AlignCenter, label)
        p.end()


# ─────────────────────────────────────────────────────────────────────
#  _AreaThumbnail — one selectable card
# ─────────────────────────────────────────────────────────────────────

class _AreaThumbnail(QFrame):
    """One variation card. Click to (de)select."""

    clicked = Signal(int)
    doubleClicked = Signal(int)

    def __init__(self, variation: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.variation_idx = int(variation["variation_idx"])
        self._selected = False
        self._dimmed = False

        self.setProperty("areaCard", True)
        self.setProperty("areaCardSelected", False)
        self.setCursor(Qt.PointingHandCursor)
        # Match the web app's compact card height (auto-rows-[180px]).
        # Was: Expanding vertical + minimumHeight=180 → cards stretched
        # to fill the column, producing ~330 px tall portrait cards.
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(190)

        # Opacity effect for the fade-when-not-selected feature
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity)

        # Build "1 liv · 3 bed · 1 kit · ..." summary (truncated to 4)
        counts = variation.get("counts_by_type", {})
        parts: List[str] = []
        n_nonzero = 0
        for rt, n in counts.items():
            if n > 0:
                n_nonzero += 1
                if len(parts) < 4:
                    parts.append(f"{int(n)} {ROOM_SHORT.get(rt, rt[:3])}")
        summary = " · ".join(parts)
        if n_nonzero > 4:
            summary += " · …"

        # Header
        header = QFrame(self)
        header.setObjectName("AreaCardHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(8, 5, 8, 5)
        h.setSpacing(6)

        self.idx_lbl = QLabel(f"#{self.variation_idx:02d}")
        self.idx_lbl.setProperty("areaCardIdx", True)

        self.summary_lbl = QLabel(summary)
        self.summary_lbl.setProperty("areaCardSummary", True)
        self.summary_lbl.setTextInteractionFlags(Qt.NoTextInteraction)
        self.summary_lbl.setMinimumWidth(0)
        self.summary_lbl.setMaximumWidth(140)
        self.summary_lbl.setTextFormat(Qt.PlainText)

        self.selected_badge = QLabel("✓ SELECTED")
        self.selected_badge.setProperty("selectedBadge", True)
        self.selected_badge.setVisible(False)

        h.addWidget(self.idx_lbl)
        h.addWidget(self.summary_lbl, 1)
        h.addWidget(self.selected_badge)

        # Body — packed circles
        body = QFrame(self)
        body.setObjectName("AreaCardBody")
        body_v = QVBoxLayout(body)
        body_v.setContentsMargins(4, 4, 4, 4)
        self.canvas = _AreaCanvas(body)
        self.canvas.set_variation(variation)
        body_v.addWidget(self.canvas)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(header)
        root.addWidget(body, 1)

    def set_show_labels(self, on: bool) -> None:
        self.canvas.set_show_labels(on)

    def set_solid_fill(self, on: bool) -> None:
        self.canvas.set_solid_fill(on)

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        _set_prop(self, "areaCardSelected", selected)
        self.selected_badge.setVisible(selected)

    def set_dimmed(self, dimmed: bool) -> None:
        """Fade the card to 35 % opacity (selected card stays full)."""
        dimmed = bool(dimmed)
        if self._dimmed == dimmed:
            return
        self._dimmed = dimmed
        self._opacity.setOpacity(0.35 if dimmed else 1.0)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.variation_idx)
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self.doubleClicked.emit(self.variation_idx)
        super().mouseDoubleClickEvent(e)


# ─────────────────────────────────────────────────────────────────────
#  _AreaComparison — bottom bar-chart strip
# ─────────────────────────────────────────────────────────────────────

class _AreaComparison(QFrame):
    """Per-type × per-variation horizontal-bar grid. Custom-painted.

    Layout::

         Living    [bar1] [bar2] [bar3] ... [bar12]
         Bedroom   [bar1] [bar2] [bar3] ... [bar12]
         ...
         VARIATION  #00   #01   #02   ...  #11
    """

    columnClicked = Signal(int)

    PAD_L = 86      # space for room-type labels on the left
    PAD_R = 12
    PAD_T = 8
    PAD_B = 22      # space for "#NN" labels at the bottom
    ROW_GAP = 4

    def __init__(self,
                 variations: List[dict] | None = None,
                 selected_idx: Optional[int] = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._variations = variations or []
        self._selected_idx = selected_idx
        # Compact: 6 type rows × ~16 px + paddings = ~150 px. Fixed
        # vertical sizing prevents the strip from stretching to fill
        # empty space and pulling the header away from the bars.
        self.setFixedHeight(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setProperty("areaComparison", True)

    def set_data(self, variations: List[dict],
                 selected_idx: Optional[int]) -> None:
        self._variations = variations
        self._selected_idx = selected_idx
        self.update()

    def set_selected(self, idx: Optional[int]) -> None:
        self._selected_idx = idx
        self.update()

    # ── geometry ────────────────────────────────────────────────────
    def _types_present(self) -> List[str]:
        if not self._variations:
            return []
        seen: set[str] = set()
        for v in self._variations:
            for rt, share in v.get("area_share_by_type", {}).items():
                if share > 0:
                    seen.add(rt)
        return [rt for rt in COMPARISON_TYPES if rt in seen]

    def _column_rect(self, var_idx: int) -> QRectF:
        n = max(1, len(self._variations))
        avail_w = self.width() - self.PAD_L - self.PAD_R
        col_w = avail_w / n
        x = self.PAD_L + var_idx * col_w
        y = self.PAD_T
        h = self.height() - self.PAD_T - self.PAD_B
        return QRectF(x, y, col_w, h)

    def paintEvent(self, _e) -> None:
        pal = _theme_palette(self)
        ink   = QColor(*pal["ink"])
        ink2  = QColor(*pal["ink2"])
        ink3  = QColor(*pal["ink3"])
        accent = QColor(*pal["accent"])
        accent_soft = QColor(*pal["accent-soft"])
        border = QColor(*pal["border"])

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        if not self._variations:
            p.setPen(QPen(ink3))
            p.drawText(self.rect(), Qt.AlignCenter, "No data")
            return

        types = self._types_present()
        n_types = max(1, len(types))
        n_vars = len(self._variations)

        # Max share across the entire matrix (for normalization)
        max_share = 0.0
        for v in self._variations:
            for rt in types:
                s = v.get("area_share_by_type", {}).get(rt, 0.0)
                if s > max_share:
                    max_share = s
        if max_share <= 0:
            max_share = 1.0

        # Row heights
        avail_h  = self.height() - self.PAD_T - self.PAD_B
        row_h    = avail_h / n_types
        bar_h    = max(4.0, row_h - self.ROW_GAP)

        avail_w = self.width() - self.PAD_L - self.PAD_R
        col_w   = avail_w / max(1, n_vars)

        # ── Selected-column highlight (background) ─────────────────
        if self._selected_idx is not None and 0 <= self._selected_idx < n_vars:
            cr = self._column_rect(self._selected_idx)
            p.setPen(Qt.NoPen)
            highlight = QColor(accent_soft)
            highlight.setAlphaF(0.55)
            p.setBrush(highlight)
            p.drawRect(cr)

        # ── Row labels (left side) ─────────────────────────────────
        f_lbl = QFont(); f_lbl.setPointSize(8)
        p.setFont(f_lbl)
        swatch_size = 7
        for ri, rt in enumerate(types):
            y_centre = self.PAD_T + ri * row_h + row_h / 2
            # Swatch
            col = QColor(theme_mod.ROOM_COLORS.get(rt, "#9aa0a6"))
            p.setBrush(QBrush(col)); p.setPen(Qt.NoPen)
            p.drawRoundedRect(
                QRectF(self.PAD_L - 78, y_centre - swatch_size / 2,
                       swatch_size, swatch_size), 1, 1)
            # Text
            p.setPen(QPen(ink2))
            p.drawText(QRectF(self.PAD_L - 66, y_centre - 6, 60, 12),
                       Qt.AlignLeft | Qt.AlignVCenter, ROOM_FULL.get(rt, rt))

        # ── Bars (per row × per column) ─────────────────────────────
        # When a variation is selected, dim every other column's bars
        # so the strip mirrors the card grid's focus state.
        has_sel = (self._selected_idx is not None
                   and 0 <= self._selected_idx < n_vars)
        for ri, rt in enumerate(types):
            y_top = self.PAD_T + ri * row_h + (row_h - bar_h) / 2
            col_for_type = QColor(
                theme_mod.ROOM_COLORS.get(rt, "#9aa0a6"))
            faded_for_type = QColor(col_for_type)
            faded_for_type.setAlphaF(0.30)
            for ci, v in enumerate(self._variations):
                share = v.get("area_share_by_type", {}).get(rt, 0.0)
                x = self.PAD_L + ci * col_w + 2
                cell_w = col_w - 4
                bar_w = cell_w * (share / max_share) if max_share else 0
                # Faint cell background
                p.setBrush(QBrush(QColor(border.red(), border.green(),
                                         border.blue(), 26)))
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(QRectF(x, y_top, cell_w, bar_h), 1, 1)
                # Bar (foreground) — full colour if selected (or none
                # selected); faded if another column is the focus
                if has_sel and ci != self._selected_idx:
                    p.setBrush(QBrush(faded_for_type))
                else:
                    p.setBrush(QBrush(col_for_type))
                p.drawRoundedRect(QRectF(x, y_top, bar_w, bar_h), 1, 1)

        # ── Column labels (bottom) ──────────────────────────────────
        f_col = QFont(); f_col.setPointSize(8)
        f_col.setStyleHint(QFont.Monospace)
        f_col.setFamily("JetBrains Mono")
        p.setFont(f_col)
        y_lbl = self.height() - self.PAD_B + 6
        for ci, v in enumerate(self._variations):
            x = self.PAD_L + ci * col_w
            is_sel = (ci == self._selected_idx)
            p.setPen(QPen(accent if is_sel else ink3))
            label = f"#{int(v['variation_idx']):02d}"
            p.drawText(QRectF(x, y_lbl, col_w, 12),
                       Qt.AlignHCenter | Qt.AlignVCenter, label)

        # Y-axis label "VARIATION" at bottom-left
        p.setPen(QPen(ink3))
        f_axis = QFont(); f_axis.setPointSize(7); f_axis.setBold(True)
        f_axis.setLetterSpacing(QFont.PercentageSpacing, 110)
        p.setFont(f_axis)
        p.drawText(QRectF(0, y_lbl, self.PAD_L - 8, 12),
                   Qt.AlignRight | Qt.AlignVCenter, "VARIATION")

        p.end()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if not self._variations:
            return
        n = len(self._variations)
        avail_w = self.width() - self.PAD_L - self.PAD_R
        col_w = avail_w / max(1, n)
        x = e.position().x() - self.PAD_L
        if x < 0 or x > avail_w:
            return
        idx = int(x // col_w)
        if 0 <= idx < n:
            self.columnClicked.emit(int(self._variations[idx]["variation_idx"]))


# ─────────────────────────────────────────────────────────────────────
#  _AreaDetailView — opened on double-click of a card
# ─────────────────────────────────────────────────────────────────────

class _AreaDetailView(QFrame):
    """Detail panel for one variation.

    Layout::

        ┌────────────────────────────────────────────────────────────┐
        │ VARIATION  #11   ·  13 rooms  ·  35,501 px² total   [✓ Sel] [X] │
        ├──────────────────────────────────┬──────────────────────────┤
        │                                  │  AREA SHARE BY TYPE      │
        │                                  │   Bedroom ████ 45.5 %    │
        │      ●  Big labelled bubble      │   Living  ██   22.4 %    │
        │         canvas                   │   ...                    │
        │                                  ├──────────────────────────┤
        │                                  │  ROOMS (13)              │
        │                                  │   living 0   7,950   28.2│
        │                                  │   bedroom 0  5,386   23.2│
        │                                  │   ...                    │
        └──────────────────────────────────┴──────────────────────────┘

    Toggled in/out by ``AreaStage._open_detail`` / ``_close_detail``.
    Closes on Esc or the X button. Selection toggle button mirrors the
    card-grid behaviour so the user can pick the variation from here too.
    """

    closeRequested  = Signal()
    selectToggled   = Signal(int)   # variation_idx — user toggled Selected

    def __init__(self, variation: dict, is_selected: bool,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._variation = variation
        self._is_selected = bool(is_selected)
        self.setProperty("areaDetail", True)
        self.setObjectName("AreaDetail")

        idx     = int(variation["variation_idx"])
        rooms   = variation.get("rooms", [])
        n_rooms = len(rooms)
        total_a = float(variation.get("total_area", 0.0))

        # ── Header ─────────────────────────────────────────────────
        header = QFrame(self); header.setObjectName("AreaDetailHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(14, 8, 10, 8); h.setSpacing(8)
        tag = QLabel("VARIATION"); tag.setProperty("sectionHeader", True)
        idx_lbl = QLabel(f"#{idx:02d}")
        idx_lbl.setStyleSheet("font-size: 14px; font-weight: 600;")
        units = str(variation.get("units", "m"))
        meta = QLabel(f"·  {n_rooms} rooms  ·  {total_a:,.1f} {units}² total")
        meta.setProperty("sectionSubtitle", True)
        h.addWidget(tag); h.addWidget(idx_lbl); h.addWidget(meta); h.addStretch(1)

        self.btn_select = QPushButton(
            "✓ Selected" if is_selected else "Add to selection")
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
        body_lay.setContentsMargins(14, 10, 14, 14)
        body_lay.setSpacing(14)

        # Left: large bubble canvas, labels forced on, outline mode
        self.canvas = _AreaCanvas(body)
        self.canvas.set_variation(variation)
        self.canvas.set_show_labels(True)
        self.canvas.set_solid_fill(True)   # detail view uses filled, like the web app
        self.canvas.setMinimumSize(360, 360)
        body_lay.addWidget(self.canvas, 3)

        # Right: side panel — area-share-by-type + rooms table
        side = QFrame(body); side.setObjectName("AreaDetailSide")
        side.setMinimumWidth(280); side.setMaximumWidth(360)
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 0, 0); side_lay.setSpacing(0)

        # — Section 1: Share by type ——
        share_section = QFrame(side); share_section.setObjectName("AreaDetailSection")
        ss = QVBoxLayout(share_section)
        ss.setContentsMargins(12, 10, 12, 10); ss.setSpacing(8)
        sh_lbl = QLabel("AREA SHARE BY TYPE")
        sh_lbl.setProperty("sectionHeader", True)
        ss.addWidget(sh_lbl)
        share_dict: dict = variation.get("area_share_by_type", {}) or {}
        share_sorted = sorted(share_dict.items(),
                               key=lambda kv: -kv[1])
        for rt, frac in share_sorted:
            if frac <= 0.0005:
                continue
            row = self._build_share_row(rt, frac)
            ss.addWidget(row)
        side_lay.addWidget(share_section)

        # — Section 2: Rooms table ——
        rooms_section = QFrame(side); rooms_section.setObjectName("AreaDetailSection")
        rs = QVBoxLayout(rooms_section)
        rs.setContentsMargins(12, 10, 12, 10); rs.setSpacing(4)
        rs_lbl = QLabel(f"ROOMS ({n_rooms})")
        rs_lbl.setProperty("sectionHeader", True)
        rs.addWidget(rs_lbl)
        # Column header
        hdr = QFrame(); hh = QHBoxLayout(hdr)
        hh.setContentsMargins(0, 0, 0, 0); hh.setSpacing(8)
        c1 = QLabel("Room");   c1.setProperty("tableHeader", True)
        c2 = QLabel("Area");   c2.setProperty("tableHeader", True); c2.setAlignment(Qt.AlignRight)
        c3 = QLabel("Radius"); c3.setProperty("tableHeader", True); c3.setAlignment(Qt.AlignRight)
        hh.addWidget(c1, 3); hh.addWidget(c2, 2); hh.addWidget(c3, 2)
        rs.addWidget(hdr)
        # Rows sorted by area desc
        sorted_rooms = sorted(rooms, key=lambda r: -float(r.get("area", 0)))
        for r in sorted_rooms:
            rs.addWidget(self._build_room_row(r))
        rs.addStretch(1)
        # Wrap rooms list in a small scroll area so it never overflows
        rs_scroll = QScrollArea(side)
        rs_scroll.setWidgetResizable(True)
        rs_scroll.setFrameShape(QFrame.NoFrame)
        rs_scroll.setWidget(rooms_section)
        side_lay.addWidget(rs_scroll, 1)

        body_lay.addWidget(side, 2)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        root.addWidget(header)
        root.addWidget(body, 1)

    @staticmethod
    def _build_share_row(rt: str, frac: float) -> QWidget:
        """One row in the AREA SHARE BY TYPE block — swatch · name · bar · %."""
        row = QFrame()
        h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
        name = QLabel(ROOM_FULL.get(rt, rt.capitalize()))
        name.setMinimumWidth(68); name.setMaximumWidth(80)
        name.setStyleSheet("font-size: 11px;")
        bar = _MiniBar(rt, frac)
        bar.setMinimumWidth(80)
        pct = QLabel(f"{frac * 100:.1f} %")
        pct.setProperty("areaDetailPct", True)
        pct.setMinimumWidth(48); pct.setAlignment(Qt.AlignRight)
        h.addWidget(name)
        h.addWidget(bar, 1)
        h.addWidget(pct)
        return row

    @staticmethod
    def _build_room_row(room: dict) -> QWidget:
        """One row in the ROOMS table."""
        row = QFrame()
        h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
        rt = room["room_type"]
        name = QLabel(room["room_id"].replace("_", " "))
        name.setStyleSheet("font-family: 'JetBrains Mono', Consolas, monospace; "
                            "font-size: 10px;")
        # Swatch + name in same cell
        cell = QFrame(); ch = QHBoxLayout(cell)
        ch.setContentsMargins(0, 0, 0, 0); ch.setSpacing(6)
        sw = QFrame(); sw.setFixedSize(8, 8)
        sw.setStyleSheet(
            f"background:{theme_mod.ROOM_COLORS.get(rt, '#9aa0a6')};"
            "border-radius:2px;")
        ch.addWidget(sw); ch.addWidget(name); ch.addStretch(1)

        area_lbl = QLabel(f"{float(room.get('area', 0)):,.0f}")
        area_lbl.setStyleSheet("font-family: 'JetBrains Mono', Consolas, monospace; "
                                "font-size: 10px;")
        area_lbl.setAlignment(Qt.AlignRight)

        rad_lbl = QLabel(f"{float(room.get('radius', 0)):.1f}")
        rad_lbl.setStyleSheet("font-family: 'JetBrains Mono', Consolas, monospace; "
                               "font-size: 10px;")
        rad_lbl.setAlignment(Qt.AlignRight)

        h.addWidget(cell, 3); h.addWidget(area_lbl, 2); h.addWidget(rad_lbl, 2)
        return row

    def set_selected(self, is_selected: bool) -> None:
        self._is_selected = bool(is_selected)
        self.btn_select.setText(
            "✓ Selected" if is_selected else "Add to selection")
        self.btn_select.setProperty(
            "pillRole", "primary" if is_selected else "ghost")
        self.btn_select.style().unpolish(self.btn_select)
        self.btn_select.style().polish(self.btn_select)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self.closeRequested.emit()
            return
        super().keyPressEvent(e)


class _MiniBar(QWidget):
    """Tiny horizontal bar for the share-by-type readout."""

    def __init__(self, rt: str, frac: float,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rt = rt
        self._frac = max(0.0, min(1.0, float(frac)))
        self.setFixedHeight(8)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = float(self.width()), float(self.height())
        top = self.window()
        pal = (theme_mod.PALETTES.get(top.store.ui.theme,
                                       theme_mod.PALETTES["offwhite"])
                if top is not None and hasattr(top, "store")
                else theme_mod.PALETTES["offwhite"])
        bg = QColor(*pal["border"]); bg.setAlphaF(0.25)
        p.setBrush(QBrush(bg)); p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(0, 0, w, h), 2, 2)
        fg = QColor(theme_mod.ROOM_COLORS.get(self._rt, "#9aa0a6"))
        p.setBrush(QBrush(fg))
        # Normalise to a "soft max" so a 45 % bar reads visually full
        norm = min(1.0, self._frac / 0.5)
        p.drawRoundedRect(QRectF(0, 0, w * norm, h), 2, 2)
        p.end()


# ─────────────────────────────────────────────────────────────────────
#  AreaStage — the main Stage 1 panel
# ─────────────────────────────────────────────────────────────────────

class AreaStage(QWidget):
    """Stage 1 — Area Distribution panel."""

    proceedRequested = Signal()   # user clicked "Proceed to Zones →"

    def __init__(self, store: SessionStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self._thumbs: list[_AreaThumbnail] = []
        self._comparison: Optional[_AreaComparison] = None
        self._detail: Optional[_AreaDetailView] = None
        self._grid_widget: Optional[QWidget] = None

        # Root layout
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Scrollable content area (grid OR cta) ──────────────────
        self.content = QFrame(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(12, 12, 12, 8)
        self.content_layout.setSpacing(8)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll, 1)

        # ── Bottom toolbar ─────────────────────────────────────────
        self.toolbar = self._build_toolbar()
        root.addWidget(self.toolbar)

        # React to store changes & theme changes.
        self.store.stage1Changed.connect(self._refresh)
        self.store.themeChanged.connect(self._on_theme_changed)

        # Initial render — either CTA or grid (if state already populated).
        self._refresh()

    # ───────────────────────────────────────────────────────────────
    # Public API
    # ───────────────────────────────────────────────────────────────
    def regenerate(self) -> None:
        """Re-sample variations from the current ``store.inputs``.

        Pulls n_variations, diversity, and selection method from the
        toolbar controls. Passes ``seed=None`` so every click produces
        a different result (matches the standalone Area Generator's
        default "Use seed = off" behaviour).

        Tries the real LogisticNormalNet first; falls back to the mock
        sampler if PyTorch isn't installed or no checkpoint is on disk.
        """
        # Pull live values from the toolbar (if toolbar built — it is
        # built in __init__ before any external regenerate() call).
        n_variations = (int(self.sp_variations.value())
                        if hasattr(self, "sp_variations")
                        else self.store.inputs.params.n_plans)
        diversity    = (float(self.sp_diversity.value())
                        if hasattr(self, "sp_diversity") else 2.50)
        method       = (self.cb_method.currentText()
                        if hasattr(self, "cb_method") else "Farthest")
        n_samples    = self.store.inputs.params.samples_n
        # Honour the Seed chip — when active, reuse the spinbox value
        # so generations are reproducible; otherwise None for fresh
        # entropy each click.
        seed_arg     = (int(self.sp_seed.value())
                        if getattr(self, "_use_seed", False)
                            and hasattr(self, "sp_seed")
                        else None)

        variations:  list[dict] = []
        used_mock:   bool       = True
        load_error:  str | None = None
        try:
            variations = generate_area_variations(
                self.store.inputs,
                n_variations=n_variations,
                n_samples=n_samples,
                diversity=diversity,
                seed=seed_arg,
                method=method,           # "Farthest" or "KMeans"
            )
            used_mock = False
        except ModelNotAvailable as e:
            load_error = str(e)
        except Exception as e:
            load_error = f"Area model raised: {e}"

        if used_mock:
            variations = mock_area_variations(
                self.store.inputs,
                n_variations=n_variations,
                diversity=diversity,
                seed=seed_arg,
            )
            if load_error:
                print(f"[stage1] area model unavailable → mock: {load_error}")

        self.store.set_stage1_areas(
            candidates=variations,
            selected_idx=None,
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
        self._comparison = None

    def _refresh(self) -> None:
        candidates = self.store.stage1_areas.candidates
        sel        = self.store.stage1_areas.selected_idx

        # If candidates didn't change (only the selection did), do a
        # light in-place update instead of tearing down the grid. This
        # keeps the detail view alive across single-click selections —
        # without it, every mouse-press of a card destroyed the detail
        # before the matching mouseDoubleClick could even arrive.
        cand_id = id(candidates) if candidates else None
        if (cand_id is not None
                and getattr(self, "_last_cand_id", None) == cand_id
                and self._thumbs):
            for t in self._thumbs:
                t.set_selected(t.variation_idx == sel)
                t.set_dimmed(sel is not None and t.variation_idx != sel)
            if self._comparison is not None:
                self._comparison.set_selected(sel)
            if getattr(self, "_detail", None) is not None:
                is_sel = (sel == int(self._detail._variation["variation_idx"]))
                self._detail.set_selected(is_sel)
            self._refresh_toolbar()
            return

        # Full rebuild — candidates changed (or first paint)
        self._last_cand_id = cand_id
        if getattr(self, "_detail", None) is not None:
            self._close_detail()
        self._clear_content()
        if not candidates:
            self.content_layout.addWidget(self._build_cta())
            self.content_layout.addStretch(1)
        else:
            self._grid_widget = self._build_grid(candidates)
            self.content_layout.addWidget(self._grid_widget)
            # Spacer between the grid and the comparison strip so the
            # strip docks to the bottom of the content area instead of
            # floating just under the grid (which left a big gap and
            # made the header + bars look disconnected on tall windows).
            self.content_layout.addStretch(1)
            # Header + bars wrapped together so they're always
            # adjacent — addStretch above can't pry them apart.
            comp_wrap = QFrame(self.content)
            comp_wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            cw = QVBoxLayout(comp_wrap)
            cw.setContentsMargins(0, 0, 0, 0); cw.setSpacing(2)
            cw.addWidget(self._build_comparison_header(candidates))
            self._comparison = _AreaComparison(candidates, sel, comp_wrap)
            self._comparison.columnClicked.connect(self._select)
            cw.addWidget(self._comparison)
            self.content_layout.addWidget(comp_wrap)

        self._refresh_toolbar()

    # ── Detail view ───────────────────────────────────────────────
    def _open_detail(self, var_idx: int) -> None:
        candidates = self.store.stage1_areas.candidates
        variation = next((v for v in candidates
                           if int(v["variation_idx"]) == int(var_idx)), None)
        if variation is None:
            return
        is_sel = (self.store.stage1_areas.selected_idx == int(var_idx))
        # Hide the grid; insert the detail view in its place.
        if getattr(self, "_grid_widget", None) is not None:
            self._grid_widget.setVisible(False)
        self._detail = _AreaDetailView(variation, is_sel, self.content)
        self._detail.closeRequested.connect(self._close_detail)
        self._detail.selectToggled.connect(self._on_detail_select_toggle)
        # Insert at position 0 so detail sits above the comparison strip
        self.content_layout.insertWidget(0, self._detail)
        self._detail.setFocus(Qt.OtherFocusReason)

    def _close_detail(self) -> None:
        if getattr(self, "_detail", None) is None:
            return
        self._detail.setParent(None)
        self._detail.deleteLater()
        self._detail = None
        if getattr(self, "_grid_widget", None) is not None:
            self._grid_widget.setVisible(True)

    def _on_detail_select_toggle(self, var_idx: int) -> None:
        # Re-use the normal select logic so card grid + comparison
        # strip stay in sync.
        self._select(int(var_idx))
        # Update the detail's button label
        is_sel = (self.store.stage1_areas.selected_idx == int(var_idx))
        if getattr(self, "_detail", None) is not None:
            self._detail.set_selected(is_sel)

    def _build_cta(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignCenter)
        v.setSpacing(8)
        title = QLabel("Ready to generate area distributions")
        title.setStyleSheet("font-size: 13px; font-weight: 600;")
        title.setAlignment(Qt.AlignCenter)
        n = self.store.inputs.params.n_plans
        sub = QLabel(
            f"Sample {n} area variations from the inputs you've set up. "
            f"Each variation distributes the {self.store.inputs.total_rooms} "
            "rooms across the plan area using slightly different proportions.")
        sub.setWordWrap(True)
        sub.setMaximumWidth(420)
        sub.setStyleSheet("color: rgba(0,0,0,0.55); font-size: 11px;")
        sub.setAlignment(Qt.AlignCenter)
        sub.setProperty("class", "cta-sub")
        btn = QPushButton(f"Generate {n} variations")
        btn.setProperty("pillRole", "primary")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(self.regenerate)
        v.addStretch(1)
        v.addWidget(title)
        v.addWidget(sub)
        v.addSpacing(8)
        wrap = QHBoxLayout()
        wrap.addStretch(1); wrap.addWidget(btn); wrap.addStretch(1)
        v.addLayout(wrap)
        v.addStretch(2)
        return w

    def _build_grid(self, candidates: List[dict]) -> QWidget:
        wrap = QFrame()
        grid = QGridLayout(wrap)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        sel = self.store.stage1_areas.selected_idx
        for i, v in enumerate(candidates):
            t = _AreaThumbnail(v, wrap)
            t.set_selected(t.variation_idx == sel)
            t.set_dimmed(sel is not None and t.variation_idx != sel)
            # Apply current display toggles to the new card
            t.set_show_labels(getattr(self, "_show_labels", True))
            t.set_solid_fill(getattr(self, "_solid_fill", False))
            t.clicked.connect(self._select)
            t.doubleClicked.connect(self._open_detail)
            self._thumbs.append(t)
            grid.addWidget(t, i // 4, i % 4)
        # Make all columns stretch equally
        for c in range(4):
            grid.setColumnStretch(c, 1)
        return wrap

    def _build_comparison_header(self, candidates: List[dict]) -> QWidget:
        row = QFrame()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 6, 0, 0)
        h.setSpacing(8)
        # ▾ AREA SHARE COMPARISON · 12 variations · total NNN px² each
        caret = QLabel("▾"); caret.setProperty("caret", True)
        title = QLabel("AREA SHARE COMPARISON")
        title.setProperty("sectionHeader", True)
        n = len(candidates)
        total = int(candidates[0].get("total_area", 0))
        units = self.store.inputs.plan_geometry.units
        meta = QLabel(f"· {n} variations · total {total:,} {units}² each")
        meta.setProperty("sectionSubtitle", True)

        right = QLabel("Click a column to select")
        right.setProperty("sectionSubtitle", True)

        h.addWidget(caret); h.addWidget(title); h.addWidget(meta)
        h.addStretch(1)
        h.addWidget(right)
        return row

    # ───────────────────────────────────────────────────────────────
    # Toolbar
    # ───────────────────────────────────────────────────────────────
    def _build_toolbar(self) -> QFrame:
        tb = QFrame(self)
        tb.setObjectName("AreaToolbar")
        lay = QHBoxLayout(tb)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(8)

        # Restore display controls from QSettings
        s = QSettings("ARML", "LayoutGenerator")
        n_var_default  = int(s.value("ui.areaVariations", 12) or 12)
        diversity_def  = float(s.value("ui.areaDiversity", 2.50) or 2.50)
        method_def     = str(s.value("ui.areaMethod", "Farthest") or "Farthest")
        # New: per-chip display toggles + seed
        self._show_labels = self._qbool(s.value("ui.areaShowLabels", True), True)
        self._solid_fill  = self._qbool(s.value("ui.areaSolidFill",  False), False)
        self._use_seed    = self._qbool(s.value("ui.areaUseSeed",    False), False)
        self._seed_value  = int(s.value("ui.areaSeed", 42) or 42)

        self.btn_regenerate = QPushButton("Regenerate")
        self.btn_regenerate.setProperty("linkBtn", True)
        self.btn_regenerate.setCursor(Qt.PointingHandCursor)
        self.btn_regenerate.clicked.connect(self.regenerate)

        # ─── Display chips ─────────────────────────────────────────
        self.chip_text = self._make_chip("Text", self._show_labels,
                                          self._toggle_text)
        self.chip_text.setToolTip("Show room names inside each circle")
        self.chip_fill = self._make_chip("Fill", self._solid_fill,
                                          self._toggle_fill)
        self.chip_fill.setToolTip("Solid coloured fill vs outline only")

        # ─── Seed chip + value spinbox ─────────────────────────────
        self.chip_seed = self._make_chip("Seed", self._use_seed,
                                          self._toggle_seed)
        self.chip_seed.setToolTip("When on, reuse the seed value below "
                                   "for reproducible generations")
        self.sp_seed = QSpinBox()
        self.sp_seed.setRange(0, 999_999_999)
        self.sp_seed.setValue(self._seed_value)
        self.sp_seed.setFixedWidth(96)
        self.sp_seed.setEnabled(self._use_seed)
        self.sp_seed.valueChanged.connect(self._on_seed_changed)

        # ─── Variations spinbox ─────────────────────────────────────
        var_lbl = QLabel("N")
        var_lbl.setProperty("sectionSubtitle", True)
        var_lbl.setToolTip("Number of variations to generate")
        self.sp_variations = QSpinBox()
        self.sp_variations.setRange(4, 24)
        self.sp_variations.setValue(n_var_default)
        self.sp_variations.setFixedWidth(68)
        self.sp_variations.valueChanged.connect(self._on_variations_changed)

        # ─── Diversity double-spinbox ──────────────────────────────
        div_lbl = QLabel("×σ")
        div_lbl.setProperty("sectionSubtitle", True)
        div_lbl.setToolTip("Diversity multiplier on the sampling noise. "
                            "1.0 = trained σ; higher → wider spread.")
        self.sp_diversity = QDoubleSpinBox()
        self.sp_diversity.setRange(0.10, 5.00)
        self.sp_diversity.setSingleStep(0.25)
        self.sp_diversity.setDecimals(2)
        self.sp_diversity.setValue(diversity_def)
        self.sp_diversity.setFixedWidth(84)
        self.sp_diversity.valueChanged.connect(self._on_diversity_changed)

        # ─── Selection method combobox ─────────────────────────────
        self.cb_method = QComboBox()
        self.cb_method.addItems(["Farthest", "KMeans"])
        idx = self.cb_method.findText(method_def)
        if idx >= 0:
            self.cb_method.setCurrentIndex(idx)
        self.cb_method.setFixedWidth(86)
        self.cb_method.setToolTip("Representative-selection method")
        self.cb_method.currentTextChanged.connect(self._on_method_changed)

        # ─── Mock pill ──────────────────────────────────────────────
        self.mock_pill = QLabel("Using mock data — backend unreachable or disabled")
        self.mock_pill.setProperty("mockPill", True)
        self.mock_pill.setVisible(False)

        self.btn_proceed = QPushButton("Proceed to Zones  →")
        self.btn_proceed.setProperty("pillRole", "primary")
        self.btn_proceed.setCursor(Qt.PointingHandCursor)
        self.btn_proceed.setEnabled(False)
        self.btn_proceed.clicked.connect(self._on_proceed)

        lay.addWidget(self.btn_regenerate)
        lay.addWidget(self._sep())
        lay.addWidget(self.chip_text)
        lay.addWidget(self.chip_fill)
        lay.addWidget(self._sep())
        lay.addWidget(self.chip_seed)
        lay.addWidget(self.sp_seed)
        lay.addWidget(self._sep())
        lay.addWidget(var_lbl); lay.addWidget(self.sp_variations)
        lay.addWidget(div_lbl); lay.addWidget(self.sp_diversity)
        lay.addWidget(self.cb_method)
        lay.addStretch(1)
        lay.addWidget(self.mock_pill)
        lay.addStretch(1)
        lay.addWidget(self.btn_proceed)
        return tb

    @staticmethod
    def _qbool(raw, default: bool) -> bool:
        """QSettings stores bools as strings on some platforms — normalise."""
        if isinstance(raw, bool):
            return raw
        if raw is None:
            return default
        return str(raw).lower() in ("true", "1", "yes", "on")

    def _make_chip(self, text: str, active: bool, on_click) -> QPushButton:
        """Build a toggleChip-styled QPushButton (matches Stage 4's chips)."""
        b = QPushButton(text)
        b.setProperty("toggleChip", True)
        b.setProperty("toggleChipActive", bool(active))
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(on_click)
        return b

    @staticmethod
    def _sep() -> QFrame:
        s = QFrame(); s.setObjectName("ToolbarSep")
        s.setFixedSize(1, 18)
        return s

    @staticmethod
    def _set_prop(w: QWidget, name: str, value) -> None:
        w.setProperty(name, value)
        w.style().unpolish(w)
        w.style().polish(w)

    # ── Toolbar control slots ─────────────────────────────────────
    def _on_variations_changed(self, n: int) -> None:
        QSettings("ARML", "LayoutGenerator").setValue("ui.areaVariations", int(n))

    def _on_diversity_changed(self, v: float) -> None:
        QSettings("ARML", "LayoutGenerator").setValue("ui.areaDiversity",
                                                       float(v))

    def _on_method_changed(self, txt: str) -> None:
        QSettings("ARML", "LayoutGenerator").setValue("ui.areaMethod", txt)

    def _on_seed_changed(self, n: int) -> None:
        self._seed_value = int(n)
        QSettings("ARML", "LayoutGenerator").setValue("ui.areaSeed", int(n))

    def _toggle_text(self) -> None:
        self._show_labels = not self._show_labels
        QSettings("ARML", "LayoutGenerator").setValue(
            "ui.areaShowLabels", "true" if self._show_labels else "false")
        self._set_prop(self.chip_text, "toggleChipActive", self._show_labels)
        for t in self._thumbs:
            t.set_show_labels(self._show_labels)

    def _toggle_fill(self) -> None:
        self._solid_fill = not self._solid_fill
        QSettings("ARML", "LayoutGenerator").setValue(
            "ui.areaSolidFill", "true" if self._solid_fill else "false")
        self._set_prop(self.chip_fill, "toggleChipActive", self._solid_fill)
        for t in self._thumbs:
            t.set_solid_fill(self._solid_fill)

    def _toggle_seed(self) -> None:
        self._use_seed = not self._use_seed
        QSettings("ARML", "LayoutGenerator").setValue(
            "ui.areaUseSeed", "true" if self._use_seed else "false")
        self._set_prop(self.chip_seed, "toggleChipActive", self._use_seed)
        self.sp_seed.setEnabled(self._use_seed)

    def _refresh_toolbar(self) -> None:
        st = self.store.stage1_areas
        sel = st.selected_idx
        self.mock_pill.setVisible(bool(st.candidates) and st.mocked)
        self.btn_proceed.setEnabled(bool(st.candidates) and sel is not None)

    # ───────────────────────────────────────────────────────────────
    # Slots
    # ───────────────────────────────────────────────────────────────
    def _select(self, var_idx: int) -> None:
        st = self.store.stage1_areas
        new_idx = None if st.selected_idx == var_idx else var_idx
        for t in self._thumbs:
            t.set_selected(t.variation_idx == new_idx)
            # Dim every other card when something is selected
            t.set_dimmed(new_idx is not None and t.variation_idx != new_idx)
        if self._comparison is not None:
            self._comparison.set_selected(new_idx)
        self.store.set_stage1_selection(new_idx)
        self._refresh_toolbar()

    def _on_proceed(self) -> None:
        # Move active stage forward. Stage 2 will pick up the selection.
        self.store.set_active_stage("zones")
        self.proceedRequested.emit()

    def _on_theme_changed(self, _name: str) -> None:
        if self._comparison is not None:
            self._comparison.update()
