"""
Batch export — top-N schematic contact sheet.
==============================================

Renders the best-matching schematic layouts from a Stage-4 batch into a
single PNG laid out as a 3-column contact sheet (the printed-template
look), with a header strip recording the batch parameters.

Layout::

    BATCH CONTACT SHEET — top 9 of N
    Target QVCI 0.700 · Target AQI 0.600 · Target Conc 0.50 · 10 rooms · …
    ───────────────────────────────────────────────────────────────────
    #NN                          #NN                          #NN
    [ canvas w/ N/S/E/W ]        [ … ]                        [ … ]
    Q <qvci> A <aqi>             …                            …
    PLAN METRICS                 …                            …
    QVCI [====] <achieved>       …                            …
    AQI  [==]   <achieved>       …                            …

Ranking ("top match") still uses closeness to the targets, but the
PLAN METRICS bars now show each layout's *achieved* QVCI / AQI, so the
bars vary card-to-card. The targets live in the header strip instead.

Public API
----------
    get_targets(store)              -> (t_qvci, t_aqi)        # for ranking
    get_target_params(store)        -> dict                   # for header
    rank_by_target(schematics, …)   -> list[dict]   (best first)
    save_batch_png(schematics, header_info, mode, alignment,
                   show_edges, path, …)                       -> bool
"""

from __future__ import annotations

import datetime as _dt
import math
from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QImage, QPainter, QPen,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from app import theme as theme_mod
from app.widgets.schematic_canvas import SchematicCanvas


# ─────────────────────────────────────────────────────────────────────
#  Geometry (logical px; multiplied by SCALE at render time)
# ─────────────────────────────────────────────────────────────────────
CARD_W       = 300
CARD_H       = 345
CANVAS_H     = 220
MARGIN       = 20
GUTTER       = 14
HEADER_H     = 40          # space reserved for the title + params strip
DEFAULT_COLS = 3
DEFAULT_SCALE = 3.0

# Readable colours on the white sheet (independent of theme).
LABEL_COLOR  = "#5f6368"   # medium grey — legible
VALUE_COLOR  = "#3c4043"   # near-black for numbers
TITLE_COLOR  = "#2b2f33"
BAR_FILL     = "#a0a0a0"    # light-medium grey measuring-bar fill
BAR_TRACK    = "#f0f0f0"    # extremely light track behind the fill


# ─────────────────────────────────────────────────────────────────────
#  Theme helpers
# ─────────────────────────────────────────────────────────────────────
def _palette(theme: str = "offwhite") -> dict:
    return theme_mod.PALETTES.get(theme, theme_mod.PALETTES["offwhite"])


def _rgb(t, fallback="#6b7075") -> str:
    if isinstance(t, (tuple, list)) and len(t) >= 3:
        return f"rgb({int(t[0])},{int(t[1])},{int(t[2])})"
    return fallback


# ─────────────────────────────────────────────────────────────────────
#  Targets / ranking / params
# ─────────────────────────────────────────────────────────────────────
def get_targets(store) -> Tuple[float, float]:
    """Pull (target_qvci, target_aqi) used for ranking. Safe defaults."""
    prefs = getattr(getattr(store, "inputs", None), "prefs", None)
    if prefs is None:
        return 0.0, 0.0
    tq = float(getattr(prefs, "target_qvci", 0.0) or 0.0)
    ta = float(getattr(prefs, "target_aqi", 0.0) or 0.0)
    return tq, ta


def get_target_params(store) -> dict:
    """Collect the parameters shown in the header strip.

    Every field is optional — anything that can't be read is simply
    omitted from the header rather than crashing the export.
    """
    out: dict = {}
    inputs = getattr(store, "inputs", None)
    prefs  = getattr(inputs, "prefs", None)

    if prefs is not None:
        out["target_qvci"] = float(getattr(prefs, "target_qvci", 0.0) or 0.0)
        out["target_aqi"]  = float(getattr(prefs, "target_aqi", 0.0) or 0.0)
        if not bool(getattr(prefs, "targets_enabled", True)):
            out["unconditioned"] = True

    # Total requested room count
    try:
        rc = getattr(inputs, "room_counts", None)
        if rc:
            out["room_count"] = int(sum(dict(rc).values()))
    except Exception:
        pass

    # Total plan area
    try:
        out["total_area"] = float(inputs.plan_geometry.total_area)
    except Exception:
        pass

    return out


def rank_by_target(schematics: Sequence[dict],
                   t_qvci: float, t_aqi: float,
                   n: int = 9,
                   qvci_weight: float = 1.0,
                   aqi_weight: float = 1.0) -> List[dict]:
    """Return the ``n`` schematics whose achieved (qvci, aqi) sit closest
    to the targets, best match first (stable on ties)."""
    def dist(s: dict) -> float:
        c = s.get("computed", {}) or {}
        dq = abs(float(c.get("qvci", 0.0)) - t_qvci)
        da = abs(float(c.get("aqi", 0.0)) - t_aqi)
        return qvci_weight * dq + aqi_weight * da

    ordered = sorted(
        schematics,
        key=lambda s: (dist(s), int(s.get("plan_index", 0))))
    return list(ordered[:max(0, int(n))])


# ─────────────────────────────────────────────────────────────────────
#  Achieved-value bar (self-contained; no circular import)
# ─────────────────────────────────────────────────────────────────────
class _ValueBar(QWidget):
    """Thin 0..1 horizontal bar; fill = achieved value, accent colour."""

    def __init__(self, value: float, theme: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = max(0.0, min(1.0, float(value)))
        self._pal = _palette(theme)
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = float(self.width()), float(self.height())
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(BAR_TRACK)))
        p.drawRoundedRect(0, 0, int(w), int(h), 2, 2)
        p.setBrush(QBrush(QColor(BAR_FILL)))
        p.drawRoundedRect(0, 0, int(w * self._value), int(h), 2, 2)
        p.end()


# ─────────────────────────────────────────────────────────────────────
#  Export card  (footer = achieved; PLAN METRICS bars = achieved)
# ─────────────────────────────────────────────────────────────────────
class _ExportCard(QFrame):
    def __init__(self, schematic: dict,
                 mode: str, alignment: str, show_edges: bool,
                 theme: str = "offwhite",
                 qv_threshold: Optional[float] = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        pal = _palette(theme)
        accent_rgb = _rgb(pal["accent"], "#b5333a")

        self.setFixedSize(CARD_W, CARD_H)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 5, 8, 5)
        root.setSpacing(3)

        c = schematic.get("computed", {}) or {}
        # If a QV threshold is supplied, recompute QVCI from the rooms so
        # the saved sheet matches the live slider; else use stored value.
        if qv_threshold is not None:
            rooms = schematic.get("rooms", []) or []
            total = sum(float(r.get("area", 0.0)) for r in rooms) or 1.0
            qva = sum(float(r.get("area", 0.0)) for r in rooms
                      if float(r.get("quality_view_score", 0.0)) >= qv_threshold)
            qvci = qva / total
        else:
            qvci = float(c.get("qvci", 0.0))
        aqi  = float(c.get("aqi", 0.0))

        # ── Header: #NN with a hairline rule under it ────────────────
        idx = int(schematic.get("plan_index", 0))
        head = QLabel(f"#{idx:02d}")
        head.setStyleSheet(
            "font-family:'JetBrains Mono',Consolas,monospace;"
            f"font-size:11px;font-weight:600;color:{LABEL_COLOR};"
            f"border-bottom:1px solid rgba(0,0,0,0.12);padding-bottom:4px;")
        root.addWidget(head)

        # ── Body: the schematic canvas ───────────────────────────────
        self.canvas = SchematicCanvas(self)
        self.canvas.set_schematic(schematic)
        self.canvas.set_mode(mode)
        self.canvas.set_alignment(alignment)
        self.canvas.set_show_edges(show_edges)
        self.canvas.setMinimumHeight(CANVAS_H)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.canvas, 1)

        # ── Footer: achieved Q / A  +  N rooms ───────────────────────
        footer = QFrame()
        f = QHBoxLayout(footer)
        f.setContentsMargins(0, 2, 0, 2); f.setSpacing(10)
        f.addWidget(self._metric("Q", f"{qvci:.2f}", accent_rgb))
        f.addWidget(self._metric("A", f"{aqi:.2f}", accent_rgb))
        f.addStretch(1)
        n_rooms = len(schematic.get("rooms", []))
        rl = QLabel(f"{n_rooms} rooms")
        rl.setStyleSheet(f"font-size:11px;color:{LABEL_COLOR};")
        f.addWidget(rl)
        root.addWidget(footer)

        # ── PLAN METRICS: achieved QVCI / AQI bars ───────────────────
        title = QLabel("PLAN METRICS")
        title.setStyleSheet(
            f"font-size:10px;font-weight:600;letter-spacing:1px;"
            f"color:{LABEL_COLOR};")
        root.addWidget(title)
        root.addWidget(self._bar_row("QVCI", qvci, theme))
        root.addWidget(self._bar_row("AQI",  aqi,  theme))

    # ----- builders --------------------------------------------------
    @staticmethod
    def _metric(label: str, value: str, accent_rgb: Optional[str],
                suffix: str = "") -> QWidget:
        wrap = QFrame()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0); h.setSpacing(3)
        lbl = QLabel(label)
        lbl.setStyleSheet(f"font-size:11px;color:{LABEL_COLOR};")
        val = QLabel(value)
        col = accent_rgb if accent_rgb else VALUE_COLOR
        val.setStyleSheet(
            "font-family:'JetBrains Mono',Consolas,monospace;"
            f"font-size:12px;font-weight:600;color:{col};")
        h.addWidget(lbl); h.addWidget(val)
        if suffix:
            suf = QLabel(suffix)
            suf.setStyleSheet(f"font-size:11px;color:{LABEL_COLOR};")
            h.addWidget(suf)
        return wrap

    @staticmethod
    def _bar_row(name: str, value: float, theme: str) -> QWidget:
        row = QFrame()
        v = QVBoxLayout(row); v.setContentsMargins(0, 1, 0, 1); v.setSpacing(2)
        top = QFrame(); th = QHBoxLayout(top)
        th.setContentsMargins(0, 0, 0, 0); th.setSpacing(0)
        nl = QLabel(name); nl.setStyleSheet(f"font-size:11px;color:{LABEL_COLOR};")
        vl = QLabel(f"{value:.3f}")
        vl.setStyleSheet(
            "font-family:'JetBrains Mono',Consolas,monospace;"
            f"font-size:11px;font-weight:600;color:{VALUE_COLOR};")
        vl.setAlignment(Qt.AlignRight)
        th.addWidget(nl); th.addStretch(1); th.addWidget(vl)
        v.addWidget(top)
        v.addWidget(_ValueBar(value, theme))
        return row


# ─────────────────────────────────────────────────────────────────────
#  Render a single card to a transparent QImage
# ─────────────────────────────────────────────────────────────────────
_TRANSPARENT_QSS = (
    "QFrame { background: transparent; }"
    "QLabel { background: transparent; }"
)


def _render_card(card: _ExportCard, scale: float) -> QImage:
    card.setStyleSheet(_TRANSPARENT_QSS)
    card.show(); card.hide()          # force an off-screen layout pass
    img = QImage(int(CARD_W * scale), int(CARD_H * scale),
                 QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.scale(scale, scale)
    card.render(p, QPoint(0, 0), card.rect(),
                QWidget.RenderFlag.DrawChildren)
    p.end()
    return img


# ─────────────────────────────────────────────────────────────────────
#  Header strip
# ─────────────────────────────────────────────────────────────────────
def _draw_header(painter: QPainter, info: dict,
                 page_w_px: float, scale: float, title: str) -> None:
    x0 = MARGIN * scale
    # Title
    f_title = QFont(); f_title.setPixelSize(int(14 * scale))
    f_title.setBold(True)
    painter.setFont(f_title)
    painter.setPen(QColor(TITLE_COLOR))
    painter.drawText(QRectF(x0, 3 * scale, page_w_px - 2 * x0, 18 * scale),
                     Qt.AlignLeft | Qt.AlignVCenter, title)

    # Params line
    parts: List[str] = []
    if "target_qvci" in info:
        parts.append(f"Target QVCI {info['target_qvci']:.3f}")
    if "target_aqi" in info:
        parts.append(f"Target AQI {info['target_aqi']:.3f}")
    if info.get("unconditioned"):
        parts.append("Unconditioned sampling")
    if "room_count" in info:
        parts.append(f"{info['room_count']} rooms")
    if "total_area" in info:
        parts.append(f"Total area {info['total_area']:,.0f}")
    parts.append(_dt.date.today().isoformat())
    params = "   ·   ".join(parts)

    f_params = QFont(); f_params.setPixelSize(int(10 * scale))
    painter.setFont(f_params)
    painter.setPen(QColor(LABEL_COLOR))
    painter.drawText(QRectF(x0, 21 * scale, page_w_px - 2 * x0, 15 * scale),
                     Qt.AlignLeft | Qt.AlignVCenter, params)

    # Separator rule under the header
    pen = QPen(QColor(0, 0, 0, 28)); pen.setWidthF(max(1.0, scale))
    painter.setPen(pen)
    y = (HEADER_H - 2) * scale
    painter.drawLine(int(x0), int(y), int(page_w_px - x0), int(y))


# ─────────────────────────────────────────────────────────────────────
#  Public: build the contact sheet
# ─────────────────────────────────────────────────────────────────────
def save_batch_png(schematics: Sequence[dict],
                   header_info: dict,
                   mode: str,
                   alignment: str,
                   show_edges: bool,
                   path: str,
                   theme: str = "offwhite",
                   cols: int = DEFAULT_COLS,
                   scale: float = DEFAULT_SCALE,
                   title: str = "Batch contact sheet",
                   qv_threshold: Optional[float] = None) -> bool:
    """Render ``schematics`` (already ranked, best first) into one PNG
    with a parameter header strip. Returns True on a successful write."""
    n = len(schematics)
    if n == 0:
        return False
    cols = max(1, int(cols))
    rows = math.ceil(n / cols)

    page_w = MARGIN * 2 + cols * CARD_W + (cols - 1) * GUTTER
    page_h = (MARGIN + HEADER_H + 8
              + rows * CARD_H + (rows - 1) * GUTTER + MARGIN)

    page = QImage(int(page_w * scale), int(page_h * scale),
                  QImage.Format_ARGB32)
    page.fill(QColor(255, 255, 255))

    painter = QPainter(page)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)

    # Faint page frame
    pen = QPen(QColor(0, 0, 0, 24)); pen.setWidth(int(max(1, scale)))
    painter.setPen(pen); painter.setBrush(Qt.NoBrush)
    inset = int(8 * scale)
    painter.drawRect(inset, inset,
                     int(page_w * scale) - 2 * inset,
                     int(page_h * scale) - 2 * inset)

    # Header strip
    _draw_header(painter, header_info, page_w * scale, scale, title)

    # Grid of cards, offset below the header
    grid_top = MARGIN + HEADER_H + 8
    for i, sch in enumerate(schematics):
        r, col = divmod(i, cols)
        x = MARGIN + col * (CARD_W + GUTTER)
        y = grid_top + r * (CARD_H + GUTTER)
        card = _ExportCard(sch, mode, alignment, show_edges, theme,
                           qv_threshold=qv_threshold)
        card_img = _render_card(card, scale)
        painter.drawImage(QPoint(int(x * scale), int(y * scale)), card_img)
        card.deleteLater()

    painter.end()

    if not path.lower().endswith(".png"):
        path += ".png"
    return bool(page.save(path))
