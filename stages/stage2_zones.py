"""
Stage 2 — Zone Assignments.

Layout (matches zone_app screenshots, image 3):

    ┌─────────────────────────────────────────────────────────────┐
    │  [CTA "Generate N plans"]   or   [4×3 zone-card grid]        │
    │                                                              │
    ├─────────────────────────────────────────────────────────────┤
    │  Regenerate · [Zone weight] · [Select all] · [mock pill]    │
    │                            N plans selected · Proceed →     │
    └─────────────────────────────────────────────────────────────┘

Each card combines:
  * #NN  +  ✓ SELECTED badge
  * mini 3×3 grid (room dots by type; balconies are not zoned and are
    listed below the grid only)
  * plan-level metric column: Q (QVCI), A (AQI), SPL (energy-average
    indoor level, dB(A)), Z (zones used)
  * compact rooms list: room name | zone | QV (q_i) | AQ (a_i)

Journal version: the tables come from the v13 cGAT-AR model conditioned
on the requested (QVCI, AQI); every number is computed with
``models.zone_scoring`` (the corpus / paper equations).  A strip above
the grid shows the targets and the reachable range of this plan–site.

Multi-select: click any card to toggle; Select-all/none in the
toolbar. Selection forwards to Stage 3 (one plan → one graph).
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal, QTimer
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPen,
)
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from app import theme as theme_mod
from app.state import SessionStore
from app.widgets.design_explorer import DesignExplorerPanel
from models.zone_mock import (
    ZONE_LABELS, arrange_rooms_in_cell, mock_zone_tables, zone_cell_box,
)
from models.zone_model import (
    ModelNotAvailable as ZoneModelNotAvailable,
    generate_zone_tables,
)


def _set_prop(widget: QWidget, name: str, value) -> None:
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _theme_palette(widget: QWidget) -> dict:
    top = widget.window()
    if top is not None and hasattr(top, "store"):
        return theme_mod.PALETTES.get(
            top.store.ui.theme, theme_mod.PALETTES["offwhite"])
    return theme_mod.PALETTES["offwhite"]


# ─────────────────────────────────────────────────────────────────────
#  _ZoneCanvas — paints the 3×3 grid with room dots
# ─────────────────────────────────────────────────────────────────────

class _ZoneCanvas(QWidget):
    """3×3 zone grid + room dots arranged inside each cell.

    Hover/click on a dot is **not** supported here (cards forward
    clicks to their parent for selection).
    """

    DOT_RADIUS = 3.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._table: Optional[dict] = None
        self._show_weights = False
        self.setFixedSize(96, 96)

    def set_table(self, table: dict) -> None:
        self._table = table
        self.update()

    def set_show_weights(self, on: bool) -> None:
        if self._show_weights != on:
            self._show_weights = on
            self.update()

    # ── plan-aspect letterbox ────────────────────────────────────
    def _plan_box(self) -> tuple[float, float, float, float]:
        """Return ``(box_x, box_y, box_w, box_h)`` for a rectangle
        inscribed in the widget whose aspect matches the building
        plan (``plan_width`` / ``plan_height`` stashed on the table at
        generation time). The box is centred horizontally and
        vertically. If the table has no plan dimensions, falls back to
        the full widget size — preserving the legacy square behaviour.
        """
        w, h = float(self.width()), float(self.height())
        plan_w = float((self._table or {}).get("plan_width",  0.0) or 0.0)
        plan_h = float((self._table or {}).get("plan_height", 0.0) or 0.0)
        if plan_w <= 0.0 or plan_h <= 0.0:
            return 0.0, 0.0, w, h
        aspect = plan_w / plan_h
        if aspect >= 1.0:           # landscape — width-limited
            box_w = w
            box_h = w / aspect
            if box_h > h:
                box_h = h
                box_w = box_h * aspect
        else:                       # portrait — height-limited
            box_h = h
            box_w = h * aspect
            if box_w > w:
                box_w = w
                box_h = box_w / aspect
        box_x = (w - box_w) / 2.0
        box_y = (h - box_h) / 2.0
        return box_x, box_y, box_w, box_h

    def paintEvent(self, _e) -> None:
        if not self._table:
            return
        pal = _theme_palette(self)
        border = QColor(*pal["border"])
        accent = QColor(*pal["accent"])

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # Letterbox to the building's plan aspect: translate so all the
        # zone_cell_box math (which assumes the rect starts at 0,0) lands
        # inside an inscribed plan-aspect rectangle instead of stretching
        # to a square.
        box_x, box_y, w, h = self._plan_box()
        p.translate(box_x, box_y)
        weights = self._table.get("zone_weights", {})

        # Cells
        for zone in range(9):
            cx, cy, cw, ch = zone_cell_box(zone, w, h)
            if self._show_weights:
                wgt = float(weights.get(f"zone_{zone}", 0.0))
                col = QColor(accent)
                col.setAlphaF(0.08 + wgt * 0.92)
                p.setPen(QPen(col, 0.5 + wgt * 1.0))
            else:
                p.setPen(QPen(border, 0.5))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(cx + 0.5, cy + 0.5,
                              cw - 1.0, ch - 1.0))

        # Group rooms by zone and arrange dots inside their cell
        # (rooms without a zone — balconies — are not drawn on the grid)
        by_zone: dict[int, List[dict]] = {}
        for r in self._table.get("rooms", []):
            if r.get("zone_assignment") is None:
                continue
            by_zone.setdefault(int(r["zone_assignment"]), []).append(r)

        p.setPen(Qt.NoPen)
        for zone, rooms in by_zone.items():
            cx, cy, cw, ch = zone_cell_box(zone, w, h)
            positions = arrange_rooms_in_cell(rooms, cx, cy, cw, ch)
            for r, pos in zip(rooms, positions):
                col = QColor(theme_mod.ROOM_COLORS.get(r["room_type"],
                                                       "#9aa0a6"))
                p.setBrush(QBrush(col))
                p.drawEllipse(QPointF(pos["cx"], pos["cy"]),
                              self.DOT_RADIUS, self.DOT_RADIUS)
        p.end()


# ─────────────────────────────────────────────────────────────────────
#  _ZoneRoomsTable — compact rooms list (custom-painted)
# ─────────────────────────────────────────────────────────────────────

class _ZoneRoomsTable(QWidget):
    """Read-only 4-column table painted in a single paintEvent.

    Columns: room name | zone | QV | AQ — the per-room view score q_i and
    acoustic score a_i of the journal equations. Rooms that carry no
    index weight (bathrooms) are dimmed; rooms without a zone (balconies)
    show "—".

    Hover highlight is drawn but not wired (low-value polish).
    """

    HEADER_H  = 18
    ROW_H     = 14
    PAD_L     = 8
    PAD_R     = 8
    COL_Z_W   = 30
    COL_QV_W  = 36
    COL_AQ_W  = 36

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rooms: List[dict] = []
        self.setMinimumHeight(self.HEADER_H + 6 * self.ROW_H + 6)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)

    def set_rooms(self, rooms: List[dict]) -> None:
        self._rooms = rooms or []
        # Resize hint based on row count
        h_needed = self.HEADER_H + max(1, len(self._rooms)) * self.ROW_H + 8
        self.setMinimumHeight(min(h_needed, 320))
        self.update()

    def paintEvent(self, _e) -> None:
        pal = _theme_palette(self)
        ink   = QColor(*pal["ink"])
        ink2  = QColor(*pal["ink2"])
        ink3  = QColor(*pal["ink3"])
        accent = QColor(*pal["accent"])
        border = QColor(*pal["border"])

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.height()

        # Column geometry (right-anchored Z / QV / AQ)
        col_aq_x = w - self.PAD_R - self.COL_AQ_W
        col_qv_x = col_aq_x - self.COL_QV_W
        col_z_x  = col_qv_x - self.COL_Z_W
        col_room_x = self.PAD_L
        col_room_w = col_z_x - col_room_x - 4

        # ── Header ──────────────────────────────────────────────
        p.setPen(QPen(ink3))
        f_hdr = QFont(); f_hdr.setPointSize(8); f_hdr.setBold(True)
        f_hdr.setLetterSpacing(QFont.PercentageSpacing, 110)
        p.setFont(f_hdr)
        p.drawText(QRectF(col_room_x, 2, col_room_w, self.HEADER_H),
                   Qt.AlignLeft | Qt.AlignVCenter, "ROOM")
        p.drawText(QRectF(col_z_x, 2, self.COL_Z_W, self.HEADER_H),
                   Qt.AlignHCenter | Qt.AlignVCenter, "Z")
        p.drawText(QRectF(col_qv_x, 2, self.COL_QV_W, self.HEADER_H),
                   Qt.AlignRight | Qt.AlignVCenter, "QV")
        p.drawText(QRectF(col_aq_x, 2, self.COL_AQ_W, self.HEADER_H),
                   Qt.AlignRight | Qt.AlignVCenter, "AQ")

        # Bottom divider under header
        p.setPen(QPen(border, 1.0))
        p.drawLine(self.PAD_L, self.HEADER_H,
                   w - self.PAD_R, self.HEADER_H)

        # ── Rows ────────────────────────────────────────────────
        f_row = QFont("JetBrains Mono"); f_row.setPointSize(8)
        f_row.setStyleHint(QFont.Monospace)
        p.setFont(f_row)
        fm = QFontMetrics(f_row)

        y = self.HEADER_H + 2
        for r in self._rooms:
            if y + self.ROW_H > h:
                break

            # Room name (eligible for elision)
            name = r["room_id"].replace("_", " ")
            elided = fm.elidedText(name, Qt.ElideRight, col_room_w)
            p.setPen(QPen(ink))
            p.drawText(QRectF(col_room_x, y, col_room_w, self.ROW_H),
                       Qt.AlignLeft | Qt.AlignVCenter, elided)

            # Zone label ("—" for rooms the model does not zone)
            z0 = r.get("zone_assignment")
            zlbl = "—" if z0 is None else ZONE_LABELS.get(int(z0), "?")
            p.setPen(QPen(ink2))
            p.drawText(QRectF(col_z_x, y, self.COL_Z_W, self.ROW_H),
                       Qt.AlignHCenter | Qt.AlignVCenter, zlbl)

            weighted = bool(r.get("scored", r["room_type"] in ("living", "kitchen", "bedroom")))
            base_ink = ink if weighted else ink3

            # QV = q_i (accent when >= 0.5)
            qv = r.get("quality_view_score")
            if qv is None:
                p.setPen(QPen(ink3)); qv_text = "—"
            else:
                p.setPen(QPen(accent if (weighted and qv >= 0.5) else base_ink))
                qv_text = f"{qv:.2f}"
            p.drawText(QRectF(col_qv_x, y, self.COL_QV_W, self.ROW_H),
                       Qt.AlignRight | Qt.AlignVCenter, qv_text)

            # AQ = a_i (accent when the indoor level is at or below the guideline)
            aq_val = r.get("acoustic_score")
            if aq_val is None:
                p.setPen(QPen(ink3)); aq_text = "—"
            else:
                p.setPen(QPen(accent if (weighted and r.get("acoustic_ok")) else base_ink))
                aq_text = f"{aq_val:.2f}"
            p.drawText(QRectF(col_aq_x, y, self.COL_AQ_W, self.ROW_H),
                       Qt.AlignRight | Qt.AlignVCenter, aq_text)

            y += self.ROW_H
        p.end()


# ─────────────────────────────────────────────────────────────────────
#  _ZoneThumbnail — one selectable card
# ─────────────────────────────────────────────────────────────────────

class _ZoneThumbnail(QFrame):
    """One zone-assignment card."""

    clicked       = Signal(int)
    doubleClicked = Signal(int)

    def __init__(self, table: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.table       = table
        self.table_index = int(table["table_index"])
        self._selected   = False

        self.setProperty("zoneCard", True)
        self.setProperty("zoneCardSelected", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(180, 290)

        # ── Header ──────────────────────────────────────────────
        header = QFrame(self)
        header.setObjectName("ZoneCardHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(6)

        self.idx_lbl = QLabel(f"#{self.table_index:02d}")
        self.idx_lbl.setProperty("zoneCardIdx", True)

        self.selected_badge = QLabel("✓ SELECTED")
        self.selected_badge.setProperty("selectedBadge", True)
        self.selected_badge.setVisible(False)

        h.addWidget(self.idx_lbl)
        h.addStretch(1)
        h.addWidget(self.selected_badge)

        # ── Body: mini canvas + metrics column ──────────────────
        body_top = QFrame(self)
        body_top.setObjectName("ZoneCardBodyTop")
        bt = QHBoxLayout(body_top)
        bt.setContentsMargins(6, 6, 6, 4)
        bt.setSpacing(6)

        self.canvas = _ZoneCanvas(body_top)
        self.canvas.set_table(table)
        bt.addWidget(self.canvas)

        m = table.get("computed", {})
        self.metrics = self._build_metric_column(m)
        bt.addWidget(self.metrics, 1)

        # ── Rooms table ─────────────────────────────────────────
        self.rooms_tbl = _ZoneRoomsTable(self)
        self.rooms_tbl.set_rooms(table.get("rooms", []))

        # ── Root layout ─────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(header)
        root.addWidget(body_top)
        root.addWidget(self.rooms_tbl, 1)

    @staticmethod
    def _build_metric_column(m: dict) -> QWidget:
        wrap = QFrame()
        v = QGridLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setHorizontalSpacing(4)
        v.setVerticalSpacing(2)

        def row(r: int, label: str, value: str, suffix: str = "",
                accent: bool = False) -> None:
            lbl = QLabel(label); lbl.setProperty("zoneMetricLabel", True)
            val = QLabel(value)
            val.setProperty("zoneMetricValue", True)
            val.setProperty("zoneMetricAccent", accent)
            val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            v.addWidget(lbl, r, 0)
            v.addWidget(val, r, 1)
            if suffix:
                suf = QLabel(suffix); suf.setProperty("zoneMetricSuffix", True)
                v.addWidget(suf, r, 2)

        qvci = m.get("qvci", 0.0)
        aqi  = m.get("aqi", 0.0)
        spl  = m.get("avg_spl_dB", 0.0)
        zsp  = m.get("zone_spread", 0)
        dt   = m.get("d_target")
        row(0, "Q",   f"{qvci:.3f}", accent=True)
        row(1, "A",   f"{aqi:.3f}",  accent=True)
        row(2, "SPL", f"{spl:.0f}",  suffix="dB(A)")
        row(3, "Z",   f"{zsp}")
        if dt is not None:
            row(4, "Δ", f"{dt:.3f}")
        v.setColumnStretch(0, 0)
        v.setColumnStretch(1, 1)
        return wrap

    def set_show_weights(self, on: bool) -> None:
        self.canvas.set_show_weights(on)

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        _set_prop(self, "zoneCardSelected", selected)
        self.selected_badge.setVisible(selected)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.table_index)
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self.doubleClicked.emit(self.table_index)
        super().mouseDoubleClickEvent(e)


# ─────────────────────────────────────────────────────────────────────
#  _ZoneDetailView — opened on double-click of a card
# ─────────────────────────────────────────────────────────────────────

class _ZoneDetailView(QFrame):
    """Per-plan detail panel: big zone canvas + metrics + rooms list.

    Mirrors the web app's ZoneDetail layout (see screenshot):
      • Header: PLAN  #NN  ·  N rooms   [✓ Selected / Add to selection] [✕]
      • Left:  large 3×3 zone canvas (labels + zone weights × 100)
      • Right: Plan Metrics block (QVCI/AQI/Concentration bars,
               Avg SPL, Zone spread) + Rooms (N) table with QV column

    Closes on Esc or the ✕ button. The Selected button mirrors the
    grid-card behaviour so the user can toggle selection from here too.
    """

    closeRequested = Signal()
    selectToggled  = Signal(int)   # table_index

    def __init__(self, table: dict, is_selected: bool,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._table       = table
        self._is_selected = bool(is_selected)
        self.setProperty("zoneDetail", True)
        self.setObjectName("ZoneDetail")

        idx     = int(table["table_index"])
        rooms   = table.get("rooms", []) or []
        n_rooms = len(rooms)

        # ── Header ─────────────────────────────────────────────────
        header = QFrame(self); header.setObjectName("ZoneDetailHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(14, 8, 10, 8); h.setSpacing(8)

        tag = QLabel("PLAN"); tag.setProperty("sectionHeader", True)
        idx_lbl = QLabel(f"#{idx:02d}")
        idx_lbl.setStyleSheet("font-size: 14px; font-weight: 600;")
        meta = QLabel(f"·  {n_rooms} rooms")
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
        body_lay.setContentsMargins(14, 10, 14, 14)
        body_lay.setSpacing(14)

        # Left: zone canvas (with labels + zone-weight numbers).
        # Capped so the grid stays a comfortable thumbnail-detail size
        # rather than dominating the panel.
        self.canvas = _ZoneDetailCanvas(table, body)
        self.canvas.setMinimumSize(280, 280)
        self.canvas.setMaximumSize(440, 440)
        body_lay.addWidget(self.canvas, 3)

        # Right: side panel — metrics + rooms table
        side = QFrame(body); side.setObjectName("ZoneDetailSide")
        side.setMinimumWidth(280); side.setMaximumWidth(380)
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 0, 0); side_lay.setSpacing(0)

        # — Plan metrics block ——
        metrics_sec = QFrame(side); metrics_sec.setObjectName("ZoneDetailSection")
        ms = QVBoxLayout(metrics_sec)
        ms.setContentsMargins(12, 10, 12, 10); ms.setSpacing(6)
        m_lbl = QLabel("PLAN METRICS"); m_lbl.setProperty("sectionHeader", True)
        ms.addWidget(m_lbl)
        c = table.get("computed", {}) or {}
        tg = table.get("targets", {}) or {}
        rr = table.get("reachable", {}) or {}
        present = bool(tg.get("present", False))
        ms.addWidget(self._metric_bar_row(
            "QVCI", float(c.get("qvci", 0.0)),
            target=float(tg["qvci"]) if present and "qvci" in tg else None,
            band=rr.get("qvci")))
        ms.addWidget(self._metric_bar_row(
            "AQI", float(c.get("aqi", 0.0)),
            target=float(tg["aqi"]) if present and "aqi" in tg else None,
            band=rr.get("aqi")))
        if present and c.get("d_target") is not None:
            ms.addWidget(self._metric_value_row(
                "Distance to target", f"{float(c['d_target']):.3f}  (|ΔQ| + |ΔA|)"))
        if rr.get("qvci") and rr.get("aqi"):
            q0, q1 = rr["qvci"]; a0, a1 = rr["aqi"]
            ms.addWidget(self._metric_value_row(
                "Reachable", f"Q {q0:.2f}–{q1:.2f} · A {a0:.2f}–{a1:.2f}"))
            if rr.get("aqi_collapsed"):
                warn = QLabel("AQI cannot be changed on this site (every façade gives "
                              "about the same indoor level).")
                warn.setWordWrap(True)
                warn.setStyleSheet("font-size: 10px; color: rgb(181,36,62);")
                ms.addWidget(warn)
        ms.addWidget(self._metric_value_row(
            "Avg indoor level", f"{float(c.get('avg_spl_dB', 0.0)):.1f} dB(A)"))
        ms.addWidget(self._metric_value_row(
            "Zone spread", f"{int(c.get('zone_spread', 0))} / 9"))
        cap = table.get("capacity", {}) or {}
        n_z = sum(1 for r in rooms if r.get("zone_assignment") is not None)
        fullest = int(round(float(c.get("concentration", 0.0)) * max(n_z, 1)))
        cap_txt = (f"corner {cap.get('corner')} · edge {cap.get('cardinal')} · "
                   f"interior {cap.get('interior')}") if cap else "—"
        ms.addWidget(self._metric_value_row(
            "Fullest zone", f"{fullest} room{'s' if fullest != 1 else ''}"))
        ms.addWidget(self._metric_value_row("Capacity", cap_txt))
        valid = c.get("valid")
        if valid is not None:
            ms.addWidget(self._metric_value_row(
                "Capacity rule", "respected ✓" if valid else "violated ✕"))
        ms.addWidget(self._metric_value_row(
            "Decode", str(table.get("decode", "")) + ("  ·  v13" if table.get("model") == "v13" else "  ·  mock")))
        side_lay.addWidget(metrics_sec)

        # — Rooms table ——
        rooms_sec = QFrame(side); rooms_sec.setObjectName("ZoneDetailSection")
        rs = QVBoxLayout(rooms_sec)
        rs.setContentsMargins(12, 10, 12, 10); rs.setSpacing(4)
        rs_lbl = QLabel(f"ROOMS ({n_rooms})"); rs_lbl.setProperty("sectionHeader", True)
        rs.addWidget(rs_lbl)
        # Column header — Room / Zone / QV / AQ / dB(A)
        hdr = QFrame(); hh = QHBoxLayout(hdr)
        hh.setContentsMargins(0, 0, 0, 0); hh.setSpacing(8)
        c1 = QLabel("Room"); c1.setProperty("tableHeader", True)
        c2 = QLabel("Zone"); c2.setProperty("tableHeader", True); c2.setAlignment(Qt.AlignCenter)
        c3 = QLabel("QV");   c3.setProperty("tableHeader", True); c3.setAlignment(Qt.AlignRight)
        c4 = QLabel("AQ");   c4.setProperty("tableHeader", True); c4.setAlignment(Qt.AlignRight)
        c5 = QLabel("dB(A)"); c5.setProperty("tableHeader", True); c5.setAlignment(Qt.AlignRight)
        hh.addWidget(c1, 3); hh.addWidget(c2, 2); hh.addWidget(c3, 2); hh.addWidget(c4, 2); hh.addWidget(c5, 2)
        rs.addWidget(hdr)
        for r in rooms:
            rs.addWidget(self._room_row(r))
        note = QLabel("QV = view score q_i, AQ = acoustic score a_i, dB(A) = modelled indoor level / guideline. "
                      "Bathrooms are placed but carry no index weight; balconies are not zoned.")
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 9px;")
        note.setProperty("sectionSubtitle", True)
        rs.addWidget(note)
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

    # ── Row builders ──────────────────────────────────────────────
    def _metric_bar_row(self, name: str, value: float,
                        target: Optional[float] = None,
                        band: Optional[list] = None) -> QWidget:
        """One metric row: label · bar (with the reachable band and the
        requested target tick) · 3-decimal value."""
        row = QFrame()
        v = QVBoxLayout(row); v.setContentsMargins(0, 2, 0, 2); v.setSpacing(2)
        # Top: name + numeric value (+ target)
        top = QFrame(); th = QHBoxLayout(top)
        th.setContentsMargins(0, 0, 0, 0); th.setSpacing(0)
        nl = QLabel(name); nl.setStyleSheet("font-size: 11px;")
        txt = f"{value:.3f}"
        if target is not None:
            txt += f"   (target {target:.2f})"
        vl = QLabel(txt)
        vl.setStyleSheet("font-family: 'JetBrains Mono', Consolas, monospace; "
                          "font-size: 11px; font-weight: 600;")
        vl.setAlignment(Qt.AlignRight)
        th.addWidget(nl); th.addStretch(1); th.addWidget(vl)
        v.addWidget(top)
        # Bottom: bar
        bar = _ZoneMetricBar(value, accent=True, target=target, band=band)
        v.addWidget(bar)
        return row

    def _metric_value_row(self, name: str, value_str: str) -> QWidget:
        """One metric row without a bar."""
        row = QFrame(); h = QHBoxLayout(row)
        h.setContentsMargins(0, 2, 0, 2); h.setSpacing(0)
        nl = QLabel(name); nl.setStyleSheet("font-size: 11px;")
        vl = QLabel(value_str)
        vl.setStyleSheet("font-family: 'JetBrains Mono', Consolas, monospace; "
                          "font-size: 11px; font-weight: 600;")
        vl.setAlignment(Qt.AlignRight)
        h.addWidget(nl); h.addStretch(1); h.addWidget(vl)
        return row

    def _room_row(self, room: dict) -> QWidget:
        row = QFrame(); h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
        # Swatch + room id
        rt = room["room_type"]
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
        # Zone label ("—" for rooms the model does not zone)
        z0 = room.get("zone_assignment")
        zlbl = QLabel("—" if z0 is None else ZONE_LABELS.get(int(z0), str(z0)))
        zlbl.setStyleSheet(
            "font-family: 'JetBrains Mono', Consolas, monospace; "
            "font-size: 10px;")
        zlbl.setAlignment(Qt.AlignCenter)
        pal = _theme_palette(self)
        acc = 'rgb' + str(tuple(pal['accent']))
        weighted = bool(room.get("scored", rt in ("living", "kitchen", "bedroom")))
        dim_color = "" if weighted else f"color: rgb{tuple(pal['ink3'])};"

        # QV = q_i  (✓ when >= 0.5 and the room counts in the index)
        qv_raw = room.get("quality_view_score", None)
        if qv_raw is None:
            qvl = QLabel("—"); qv_style = dim_color
        else:
            qv = float(qv_raw); ok = weighted and qv >= 0.5
            qvl = QLabel(("✓ " if ok else "") + f"{qv:.2f}")
            qv_style = f"color: {acc};" if ok else dim_color
        qvl.setStyleSheet(
            "font-family: 'JetBrains Mono', Consolas, monospace; "
            f"font-size: 10px; {qv_style}")
        qvl.setAlignment(Qt.AlignRight)

        # AQ = a_i  (✓ when the indoor level is at or below the guideline)
        aq_raw = room.get("acoustic_score", None)
        aq_ok  = bool(room.get("acoustic_ok", False)) and weighted
        if aq_raw is None:
            aql_text = "—"; aq_style = dim_color
        else:
            aql_text = ("✓ " if aq_ok else "") + f"{float(aq_raw):.2f}"
            aq_style = f"color: {acc};" if aq_ok else dim_color
        aql = QLabel(aql_text)
        aql.setStyleSheet(
            "font-family: 'JetBrains Mono', Consolas, monospace; "
            f"font-size: 10px; {aq_style}")
        aql.setAlignment(Qt.AlignRight)

        # Indoor level / guideline
        lin = room.get("est_avg_spl_dB", None)
        g = room.get("guideline_dB", None)
        lbl_txt = "—" if lin is None else f"{float(lin):.0f}" + (f"/{int(g)}" if g else "")
        lin_l = QLabel(lbl_txt)
        lin_l.setToolTip("modelled indoor level / guideline, dB(A)")
        lin_l.setStyleSheet(
            "font-family: 'JetBrains Mono', Consolas, monospace; "
            f"font-size: 10px; {dim_color}")
        lin_l.setAlignment(Qt.AlignRight)

        h.addWidget(cell, 3); h.addWidget(zlbl, 2)
        h.addWidget(qvl, 2); h.addWidget(aql, 2); h.addWidget(lin_l, 2)
        return row

    def set_selected(self, is_selected: bool) -> None:
        self._is_selected = bool(is_selected)
        self.btn_select.setText(
            "✓ Selected (click to remove)" if is_selected
            else "Add to selection")
        self.btn_select.setProperty(
            "pillRole", "primary" if is_selected else "ghost")
        self.btn_select.style().unpolish(self.btn_select)
        self.btn_select.style().polish(self.btn_select)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self.closeRequested.emit()
            return
        super().keyPressEvent(e)


class _ZoneDetailCanvas(_ZoneCanvas):
    """Bigger _ZoneCanvas variant with zone labels + zone-weight numbers."""

    DOT_RADIUS = 6.0

    def __init__(self, table: dict,
                 parent: QWidget | None = None) -> None:
        # Don't call super().__init__ — that sets fixed 96×96. We set our own.
        QWidget.__init__(self, parent)
        self._table = table
        self._show_weights = False
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(260, 260)

    def paintEvent(self, _e) -> None:
        if not self._table:
            return
        pal = _theme_palette(self)
        border = QColor(*pal["border"])
        ink2   = QColor(*pal["ink2"])
        ink3   = QColor(*pal["ink3"])

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # Letterbox to the building's plan aspect (same trick as the
        # thumbnail canvas).
        box_x, box_y, w, h = self._plan_box()
        p.translate(box_x, box_y)
        weights = self._table.get("zone_weights", {})
        levels = self._table.get("zone_levels_dB", {}) or {}

        f_label = QFont(); f_label.setPointSize(8); f_label.setBold(True)
        f_value = QFont(); f_value.setPointSize(8)

        # Cells — outline + zone label (NW/N/NE/…) top-left, weight×100 top-right
        for z0 in range(9):
            cx, cy, cw, ch = zone_cell_box(z0, w, h)
            p.setPen(QPen(border, 0.8)); p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(cx + 0.5, cy + 0.5, cw - 1.0, ch - 1.0))
            # Zone label
            p.setPen(QPen(ink2)); p.setFont(f_label)
            p.drawText(QRectF(cx + 6, cy + 4, cw - 12, 14),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       ZONE_LABELS.get(z0, str(z0)))
            # View weight v(z) × 100 (top right) and outdoor level (bottom left)
            wgt = float(weights.get(f"zone_{z0}", 0.0))
            p.setPen(QPen(ink3)); p.setFont(f_value)
            p.drawText(QRectF(cx + 6, cy + 4, cw - 12, 14),
                       Qt.AlignRight | Qt.AlignVCenter,
                       f"{int(round(wgt * 100))}")
            lv = levels.get(f"zone_{z0}")
            if lv is not None:
                p.drawText(QRectF(cx + 6, cy + ch - 18, cw - 12, 14),
                           Qt.AlignLeft | Qt.AlignVCenter,
                           f"{float(lv):.0f} dB(A)")

        # Room dots — grouped by zone, packed inside each cell, bigger
        # radius than the thumbnail canvas (6 px vs 3 px). Unzoned rooms
        # (balconies) are not drawn.
        by_zone: dict[int, list[dict]] = {}
        for r in self._table.get("rooms", []) or []:
            if r.get("zone_assignment") is None:
                continue
            by_zone.setdefault(int(r["zone_assignment"]), []).append(r)
        p.setPen(Qt.NoPen)
        for z0, rooms in by_zone.items():
            cx, cy, cw, ch = zone_cell_box(z0, w, h)
            # Shrink the packable area so dots don't overlap the labels
            inset = 22
            positions = arrange_rooms_in_cell(
                rooms, cx + 4, cy + inset, cw - 8, ch - inset - 18)
            for r, pos in zip(rooms, positions):
                col = QColor(theme_mod.ROOM_COLORS.get(r["room_type"],
                                                       "#9aa0a6"))
                p.setBrush(QBrush(col))
                p.drawEllipse(QPointF(pos["cx"], pos["cy"]),
                              self.DOT_RADIUS, self.DOT_RADIUS)
        p.end()


class _ZoneMetricBar(QWidget):
    """Tiny horizontal bar 0..1 used in the metrics block, with the
    reachable band (darker track) and the requested target (tick)."""

    def __init__(self, value: float, accent: bool = True,
                 target: Optional[float] = None, band: Optional[list] = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value  = max(0.0, min(1.0, float(value)))
        self._accent = accent
        self._target = None if target is None else max(0.0, min(1.0, float(target)))
        self._band   = None
        if band and len(band) == 2:
            self._band = (max(0.0, min(1.0, float(band[0]))), max(0.0, min(1.0, float(band[1]))))
        self.setFixedHeight(8 if (self._target is not None or self._band) else 6)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if self._band:
            self.setToolTip(f"reachable {self._band[0]:.2f} – {self._band[1]:.2f}")

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = _theme_palette(self)
        w, h = float(self.width()), float(self.height())
        bg = QColor(*pal["border"]); bg.setAlphaF(0.25)
        p.setBrush(QBrush(bg)); p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(0, 0, w, h), 2, 2)
        if self._band:
            bd = QColor(*pal["ink3"]); bd.setAlphaF(0.35)
            p.setBrush(QBrush(bd))
            p.drawRoundedRect(QRectF(w * self._band[0], 0,
                                     max(w * (self._band[1] - self._band[0]), 1.0), h), 2, 2)
        fg = QColor(*pal["accent"]) if self._accent else QColor(*pal["ink2"])
        p.setBrush(QBrush(fg))
        p.drawRoundedRect(QRectF(0, h * 0.2, w * self._value, h * 0.6), 2, 2)
        if self._target is not None:
            p.setPen(QPen(QColor(*pal["ink"]), 1.5))
            x = w * self._target
            p.drawLine(QPointF(x, -1), QPointF(x, h + 1))
        p.end()


# ─────────────────────────────────────────────────────────────────────
#  ZoneStage — the main Stage 2 panel
# ─────────────────────────────────────────────────────────────────────

class ZoneStage(QWidget):
    """Stage 2 panel — zone assignments grid with multi-select."""

    proceedRequested = Signal()  # user clicked "Proceed to Connectivity →"

    def __init__(self, store: SessionStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self._thumbs: list[_ZoneThumbnail] = []
        self._show_weights = False
        # Detail view + cache for the smart-refresh path
        self._detail: Optional[_ZoneDetailView] = None
        self._grid_widget: Optional[QWidget]    = None
        self._last_tables_id: Optional[int]     = None
        self._last_load_error: Optional[str]    = None

        # Layout shell
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

        # Design Explorer (collapsible parcoords). Sits between the
        # grid and the toolbar. Hidden when there are no tables.
        self.explorer = DesignExplorerPanel(self)
        self.explorer.lineClicked.connect(self._on_explorer_click)
        self.explorer.lineHovered.connect(self._on_explorer_hover)
        self.explorer.setVisible(False)
        root.addWidget(self.explorer)

        self.toolbar = self._build_toolbar()
        root.addWidget(self.toolbar)

        # Signal hookups
        self.store.stage1Changed.connect(self._refresh)
        self.store.stage2Changed.connect(self._refresh)
        self.store.themeChanged.connect(self._on_theme_changed)

        # Auto-regenerate when Stage-0 inputs change (e.g. the target
        # sliders). Debounced so dragging a slider coalesces into a single
        # re-run, and gated on _has_generated so editing inputs *before*
        # proceeding to zones doesn't fire the model.
        self._has_generated = False
        self._regen_timer = QTimer(self)
        self._regen_timer.setSingleShot(True)
        self._regen_timer.setInterval(350)
        self._regen_timer.timeout.connect(self.regenerate)
        self.store.inputsChanged.connect(self._schedule_regen)

        self._refresh()

    def _schedule_regen(self) -> None:
        if self._has_generated:
            self._regen_timer.start()

    # ───────────────────────────────────────────────────────────────
    # Public API
    # ───────────────────────────────────────────────────────────────
    def regenerate(self) -> None:
        """Re-sample zone assignments from the selected area variation.

        Tries the real GAT+AR model first; falls back to the mock if
        PyTorch isn't installed, no checkpoint is on disk, or the
        checkpoint isn't v8/v11. The ``mocked`` flag on stage2 state
        reflects which one ran.
        """
        st1 = self.store.stage1_areas
        if st1.selected_idx is None or not st1.candidates:
            return
        av = next((c for c in st1.candidates
                   if int(c["variation_idx"]) == st1.selected_idx), None)
        if av is None:
            return
        self._has_generated = True
        n      = self.store.inputs.params.n_plans
        temp   = float(self.store.inputs.params.temperature)
        ns     = int(self.store.inputs.params.zone_samples)

        tables: list[dict] = []
        raw_pool: list[dict] = []
        used_mock: bool = True
        load_error: str | None = None
        try:
            tables, raw_pool = generate_zone_tables(
                self.store.inputs,
                area_variation=av,
                n_tables=n,
                n_samples=ns,
                temperature=temp,
            )
            used_mock = False
        except ZoneModelNotAvailable as e:
            load_error = str(e)
        except Exception as e:
            # An unexpected error in the real model (attribute access,
            # tensor shape mismatch, etc.) — surface the traceback so
            # the user can see what went wrong instead of silently
            # falling back to the mock.
            import traceback
            print(f"[stage2] zone model raised:\n{traceback.format_exc()}")
            load_error = f"Zone model raised: {e}"

        if used_mock:
            tables, raw_pool = mock_zone_tables(self.store.inputs, av,
                                                 n_tables=n, n_samples=ns)
            if load_error:
                print(f"[stage2] using mock — {load_error}")
            self._last_load_error = load_error
        else:
            self._last_load_error = None

        # zone_model.generate_zone_tables returns:
        #   tables   — unique zone arrangements (deduped), sorted by score
        #   raw_pool — one metric-tuple per sample (duplicates included),
        #              so the Stage 4 Pareto scatter can show as many
        #              dots as the user requested via ``samples``.
        # The display grid takes the top N from ``tables``. The sample
        # pool drawn behind the foreground dots comes from ``raw_pool``.
        display = tables[:n]
        # Re-index display so the visible cards read 0..n-1.
        # Stash plan_width / plan_height so the detail canvas can
        # letterbox the 3×3 grid to the building's actual aspect ratio
        # instead of stretching to a square.
        pg = self.store.inputs.plan_geometry
        plan_w = float(pg.width)
        plan_h = float(pg.height)
        for i, t in enumerate(display):
            t["table_index"]  = i
            t["plan_width"]   = plan_w
            t["plan_height"]  = plan_h

        # Fast lookup: which displayed-card metric tuples match each
        # raw pool entry? We mark pool dots whose (qvci, aqi) coincides
        # with a displayed card so the Pareto view can avoid drawing
        # both a faded grey background dot AND a solid foreground dot
        # at the same pixel.
        display_keys = {
            (round(float(t["computed"]["qvci"]),    6),
             round(float(t["computed"]["aqi"]),     6))
            for t in display
        }
        pool = [
            {
                "qvci":          float(s.get("qvci", 0.0)),
                "aqi":           float(s.get("aqi",  0.0)),
                "concentration": float(s.get("concentration", 0.0)),
                "d_target":      s.get("d_target"),
                "valid":         s.get("valid", True),
                "in_display":    (
                    round(float(s.get("qvci", 0.0)), 6),
                    round(float(s.get("aqi",  0.0)), 6)
                ) in display_keys,
            }
            for s in raw_pool
        ]

        # All tables start selected so the next stage can run straight away;
        # the user deselects the ones to drop.
        self.store.set_stage2_zones(
            display, selected_idxs=[int(t["table_index"]) for t in display],
            mocked=used_mock, sample_pool=pool)

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
        st1 = self.store.stage1_areas
        st2 = self.store.stage2_zones
        sel_set = set(st2.selected_idxs)

        # Smart refresh: if the *tables* themselves didn't change (just
        # the selection), do an in-place sync instead of tearing down
        # the grid. This keeps a double-click detail view alive across
        # single-click selections (same bug Stage 1 had).
        tables_id = id(st2.tables) if st2.tables else None
        if (tables_id is not None
                and self._last_tables_id == tables_id
                and self._thumbs):
            for t in self._thumbs:
                t.set_selected(t.table_index in sel_set)
            self.explorer.set_selected_idxs(list(sel_set))
            if self._detail is not None:
                idx = int(self._detail._table["table_index"])
                self._detail.set_selected(idx in sel_set)
            self._refresh_toolbar()
            return

        # Full rebuild
        self._last_tables_id = tables_id
        if self._detail is not None:
            self._close_detail()
        self._clear_content()

        if st1.selected_idx is None:
            self.content_layout.addWidget(self._build_empty(
                "Complete Stage 1 (pick an area variation) "
                "to generate zone assignments."))
            self.content_layout.addStretch(1)
            self.explorer.setVisible(False)
        elif not st2.tables:
            self.content_layout.addWidget(self._build_cta())
            self.content_layout.addStretch(1)
            self.explorer.setVisible(False)
        else:
            self.content_layout.addWidget(self._build_range_strip(st2.tables))
            self._grid_widget = self._build_grid(st2.tables)
            self.content_layout.addWidget(self._grid_widget)
            self.explorer.set_tables(st2.tables)
            self.explorer.set_selected_idxs(st2.selected_idxs)
            self.explorer.setVisible(True)

        self._refresh_toolbar()

    def _build_range_strip(self, tables: List[dict]) -> QWidget:
        """One line above the grid: targets, reachable range, pool size."""
        t0 = tables[0] if tables else {}
        tg = t0.get("targets", {}) or {}
        rr = t0.get("reachable", {}) or {}
        pal = _theme_palette(self)
        acc = "rgb(%d,%d,%d)" % tuple(pal["accent"])
        parts = []
        if tg.get("present"):
            parts.append(f"Targets  QVCI <b>{float(tg.get('qvci', 0)):.2f}</b> · "
                         f"AQI <b>{float(tg.get('aqi', 0)):.2f}</b>")
        else:
            parts.append("Unconditioned sampling (no targets)")
        if rr.get("qvci") and rr.get("aqi"):
            q0, q1 = rr["qvci"]; a0, a1 = rr["aqi"]
            parts.append(f"reachable  QVCI {q0:.2f}–{q1:.2f} · AQI {a0:.2f}–{a1:.2f}")
            if rr.get("aqi_collapsed"):
                parts.append(f"<span style='color:{acc};'>AQI cannot be changed on this site</span>")
        n_pool = len(self.store.stage2_zones.sample_pool)
        parts.append(f"{n_pool} decodes · {len(tables)} shown"
                     + (" · sorted by distance to target" if tg.get("present") else ""))
        lbl = QLabel("  ·  ".join(parts))
        lbl.setTextFormat(Qt.RichText)
        lbl.setWordWrap(True)
        lbl.setProperty("sectionSubtitle", True)
        lbl.setContentsMargins(2, 0, 2, 2)
        lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)   # wrap to the column width
        return lbl

    # ── Detail view ───────────────────────────────────────────────
    def _open_detail(self, table_idx: int) -> None:
        st2 = self.store.stage2_zones
        table = next((t for t in st2.tables
                       if int(t["table_index"]) == int(table_idx)), None)
        if table is None:
            return
        is_sel = int(table_idx) in set(st2.selected_idxs)
        if self._grid_widget is not None:
            self._grid_widget.setVisible(False)
        self._detail = _ZoneDetailView(table, is_sel, self.content)
        self._detail.closeRequested.connect(self._close_detail)
        self._detail.selectToggled.connect(self._on_detail_select_toggle)
        self.content_layout.insertWidget(0, self._detail)
        self._detail.setFocus(Qt.OtherFocusReason)

    def _close_detail(self) -> None:
        if self._detail is None:
            return
        self._detail.setParent(None)
        self._detail.deleteLater()
        self._detail = None
        if self._grid_widget is not None:
            self._grid_widget.setVisible(True)

    def _on_detail_select_toggle(self, table_idx: int) -> None:
        self._toggle_select(int(table_idx))
        # _toggle_select doesn't touch the detail view's button label
        # (it only updates the thumbnail grid + explorer), so do it here.
        if self._detail is not None:
            sel_set = set(self.store.stage2_zones.selected_idxs)
            self._detail.set_selected(int(table_idx) in sel_set)

    def _build_empty(self, msg: str) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignCenter)
        v.setContentsMargins(20, 80, 20, 20)
        v.setSpacing(4)
        title = QLabel("Waiting for area selection")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 13px; font-weight: 600;")
        sub = QLabel(msg)
        sub.setAlignment(Qt.AlignCenter)
        sub.setProperty("sectionSubtitle", True)
        sub.setWordWrap(True)
        sub.setMaximumWidth(360)
        v.addWidget(title)
        v.addWidget(sub)
        return w

    def _build_cta(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignCenter)
        v.setContentsMargins(20, 60, 20, 20)
        v.setSpacing(6)
        title = QLabel("Ready to generate zone assignments")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 13px; font-weight: 600;")
        n = self.store.inputs.params.n_plans
        sub = QLabel(
            f"The v13 zone model will decode one greedy and "
            f"{self.store.inputs.params.zone_samples} sampled arrangements for the "
            "selected area distribution and site, conditioned on the requested "
            f"QVCI / AQI, and show the {n} closest to the targets. Each arrangement "
            "assigns every living, kitchen, bedroom and bathroom to one of 9 zones "
            "in a 3×3 grid; balconies join at the connectivity stage.")
        sub.setAlignment(Qt.AlignCenter)
        sub.setProperty("sectionSubtitle", True)
        sub.setWordWrap(True)
        sub.setMaximumWidth(420)
        btn = QPushButton(f"Generate {n} plans")
        btn.setProperty("pillRole", "primary")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(self.regenerate)
        wrap = QHBoxLayout()
        wrap.addStretch(1); wrap.addWidget(btn); wrap.addStretch(1)
        v.addWidget(title)
        v.addWidget(sub)
        v.addSpacing(6)
        v.addLayout(wrap)
        return w

    def _build_grid(self, tables: List[dict]) -> QWidget:
        wrap = QFrame()
        grid = QGridLayout(wrap)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        sel = set(self.store.stage2_zones.selected_idxs)
        for i, t in enumerate(tables):
            card = _ZoneThumbnail(t, wrap)
            card.set_selected(card.table_index in sel)
            card.set_show_weights(self._show_weights)
            card.clicked.connect(self._toggle_select)
            card.doubleClicked.connect(self._open_detail)
            self._thumbs.append(card)
            grid.addWidget(card, i // 4, i % 4)
        for c in range(4):
            grid.setColumnStretch(c, 1)
        return wrap

    # ───────────────────────────────────────────────────────────────
    # Toolbar
    # ───────────────────────────────────────────────────────────────
    def _build_toolbar(self) -> QFrame:
        tb = QFrame(self)
        tb.setObjectName("ZoneToolbar")
        lay = QHBoxLayout(tb)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(8)

        self.btn_regenerate = QPushButton("Regenerate")
        self.btn_regenerate.setProperty("linkBtn", True)
        self.btn_regenerate.setCursor(Qt.PointingHandCursor)
        self.btn_regenerate.clicked.connect(self.regenerate)

        self.btn_weights = QPushButton("● Zone weight")
        self.btn_weights.setProperty("toggleChip", True)
        self.btn_weights.setProperty("toggleChipActive", False)
        self.btn_weights.setCursor(Qt.PointingHandCursor)
        self.btn_weights.clicked.connect(self._toggle_weights)

        self.btn_select_all = QPushButton("■ Select all")
        self.btn_select_all.setProperty("toggleChip", True)
        self.btn_select_all.setProperty("toggleChipActive", False)
        self.btn_select_all.setCursor(Qt.PointingHandCursor)
        self.btn_select_all.clicked.connect(self._toggle_select_all)

        # Export selected — saves each selected card as a high-resolution
        # transparent PNG (one file per card). Disabled until at least one
        # plan is selected.
        self.btn_export = QPushButton("⤓ Export selected")
        self.btn_export.setProperty("toggleChip", True)
        self.btn_export.setProperty("toggleChipActive", False)
        self.btn_export.setCursor(Qt.PointingHandCursor)
        self.btn_export.setEnabled(False)
        self.btn_export.setToolTip(
            "Save each selected card as a high-resolution transparent PNG")
        self.btn_export.clicked.connect(self._export_selected_to_pngs)

        self.mock_pill = QLabel("Random admissible arrangements — v13 model unavailable")
        self.mock_pill.setProperty("mockPill", True)
        self.mock_pill.setVisible(False)

        self.sel_label = QLabel("Click plans to select")
        self.sel_label.setProperty("sectionSubtitle", True)

        self.btn_proceed = QPushButton("Proceed to Connectivity  →")
        self.btn_proceed.setProperty("pillRole", "primary")
        self.btn_proceed.setCursor(Qt.PointingHandCursor)
        self.btn_proceed.setEnabled(False)
        self.btn_proceed.clicked.connect(self._on_proceed)

        lay.addWidget(self.btn_regenerate)
        lay.addWidget(self.btn_weights)
        lay.addWidget(self.btn_select_all)
        lay.addWidget(self.btn_export)
        lay.addStretch(1)
        lay.addWidget(self.mock_pill)
        lay.addStretch(1)
        lay.addWidget(self.sel_label)
        lay.addWidget(self.btn_proceed)
        return tb

    def _refresh_toolbar(self) -> None:
        st = self.store.stage2_zones
        has_tables = bool(st.tables)
        n_sel = len(st.selected_idxs)

        self.mock_pill.setVisible(has_tables and st.mocked)
        if has_tables and st.mocked and self._last_load_error:
            self.mock_pill.setToolTip(self._last_load_error)
        self.btn_weights.setEnabled(has_tables)
        self.btn_select_all.setEnabled(has_tables)
        self.btn_export.setEnabled(has_tables and n_sel > 0)
        self.btn_regenerate.setEnabled(self.store.stage1_areas.selected_idx is not None)

        if not has_tables:
            self.sel_label.setText("")
            self.btn_proceed.setEnabled(False)
        elif n_sel == 0:
            self.sel_label.setText("Click plans to select")
            self.btn_proceed.setEnabled(False)
        elif n_sel == 1:
            self.sel_label.setText("1 plan selected")
            self.btn_proceed.setEnabled(True)
        else:
            self.sel_label.setText(f"{n_sel} plans selected")
            self.btn_proceed.setEnabled(True)

        # Update select-all chip state
        all_sel = has_tables and n_sel == len(st.tables)
        _set_prop(self.btn_select_all, "toggleChipActive", all_sel)
        self.btn_select_all.setText("■ Select none" if all_sel else "■ Select all")

    # ───────────────────────────────────────────────────────────────
    # Slots
    # ───────────────────────────────────────────────────────────────
    def _toggle_select(self, table_idx: int) -> None:
        st = self.store.stage2_zones
        sel = set(st.selected_idxs)
        if table_idx in sel:
            sel.remove(table_idx)
        else:
            sel.add(table_idx)
        new = sorted(sel)
        self.store.set_stage2_selection(new)
        # Local refresh of thumbnail badges (cheaper than full _refresh)
        for t in self._thumbs:
            t.set_selected(t.table_index in sel)
        self.explorer.set_selected_idxs(new)
        self._refresh_toolbar()

    def _on_explorer_click(self, table_idx: int) -> None:
        """Polyline click → same as clicking a card."""
        self._toggle_select(int(table_idx))

    def _on_explorer_hover(self, idx_or_neg1: int) -> None:
        """Polyline hover → could highlight matching card.

        Card-side hover sync is light-touch for now (no badge or border
        flash). The readout in the parcoords already gives the user
        feedback. If needed, this is where to add cross-component
        hover binding later.
        """
        return

    def _toggle_select_all(self) -> None:
        st = self.store.stage2_zones
        if len(st.selected_idxs) == len(st.tables):
            new: list[int] = []
        else:
            new = [int(t["table_index"]) for t in st.tables]
        self.store.set_stage2_selection(new)
        sel = set(new)
        for t in self._thumbs:
            t.set_selected(t.table_index in sel)
        self.explorer.set_selected_idxs(new)
        self._refresh_toolbar()

    def _toggle_weights(self) -> None:
        self._show_weights = not self._show_weights
        for t in self._thumbs:
            t.set_show_weights(self._show_weights)
        _set_prop(self.btn_weights, "toggleChipActive", self._show_weights)

    def _on_proceed(self) -> None:
        self.store.set_active_stage("connectivity")
        self.proceedRequested.emit()

    # ── Export selected to PNGs ────────────────────────────────────
    def _export_selected_to_pngs(self) -> None:
        """Render each selected card to a high-resolution transparent PNG.

        Approach: build an off-screen ``_ZoneThumbnail`` per selected
        plan, neutralise its filled background via a one-off stylesheet,
        and ``render()`` it into an ARGB32 ``QImage`` with a 3× painter
        scale. Result: ~840 × 1200 px PNGs with a transparent
        background, sharp text and crisp dot edges — ready to drop into
        thesis figures or slide decks.
        """
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from PySide6.QtCore  import QPoint
        from PySide6.QtGui   import QImage, QPainter
        from PySide6.QtCore  import Qt as _Qt

        st = self.store.stage2_zones
        sel_idxs = sorted(set(st.selected_idxs))
        if not sel_idxs:
            QMessageBox.information(
                self, "Nothing to export",
                "Select at least one card before exporting.")
            return

        out_dir = QFileDialog.getExistingDirectory(
            self, "Choose folder for PNG exports")
        if not out_dir:
            return

        # Transparent-background overrides for the card and all its
        # internal frames. We don't touch foreground colours — text,
        # swatches, accent bars, room dots all keep their normal hues.
        transparent_qss = (
            "QFrame[zoneCard='true'] { "
            "  background: transparent; border: none; }"
            "QFrame#ZoneCardHeader, QFrame#ZoneCardBodyTop { "
            "  background: transparent; }"
            "QFrame { background: transparent; }"
            "QLabel { background: transparent; }"
        )

        # Fixed design size — gives every exported PNG the same aspect
        # ratio regardless of how the live cards happen to be sized.
        CARD_W, CARD_H = 280, 400
        SCALE = 3.0

        saved = 0
        skipped: list[str] = []
        for idx in sel_idxs:
            if idx < 0 or idx >= len(st.tables):
                continue
            table = st.tables[idx]
            plan_no = int(table.get("table_index", idx))

            # Build an off-screen card. Parent=None so it's a top-level
            # widget Qt is willing to render even though we never show it.
            card = _ZoneThumbnail(table, parent=None)
            card.set_selected(idx in sel_idxs)
            card.setStyleSheet(transparent_qss)
            card.resize(CARD_W, CARD_H)
            # Force a layout pass — the children compute their geometry
            # only after the widget has been shown at least once.
            card.show(); card.hide()

            img = QImage(int(CARD_W * SCALE), int(CARD_H * SCALE),
                          QImage.Format_ARGB32)
            img.fill(_Qt.transparent)
            painter = QPainter(img)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.TextAntialiasing)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.scale(SCALE, SCALE)
            # DrawChildren only — no window background. The off-screen
            # parent's background never gets painted into the image.
            card.render(painter, QPoint(0, 0), card.rect(),
                         QWidget.RenderFlag.DrawChildren)
            painter.end()

            out_path = f"{out_dir}/zone_card_{plan_no:02d}.png"
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

    def _on_theme_changed(self, _name: str) -> None:
        # Force a repaint of every custom-painted child.
        for t in self._thumbs:
            t.canvas.update()
            t.rooms_tbl.update()
