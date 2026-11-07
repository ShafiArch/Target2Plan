"""
connectivity_export.py — batch contact-sheet PNG for Stage-3 graphs.

Renders the connectivity graphs (nodes + edges, final/settled state) into
a single PNG laid out as a grid, sized to a US-Letter text column at
300 DPI so it drops cleanly into the thesis via
``\\includegraphics[width=\\linewidth]{...}``.

Deliberately different from the on-screen cards:
  * NO 3x3 zone grid lines
  * NO compass (N/S/E/W) ornament
  * NO card border / boundary
Kept: each graph's ``#index`` header and ``N rooms / N edges`` footer.

The node/edge drawing mirrors ``app.widgets.animated_graph.AnimatedGraph``
in its settled state (animation frame == last), so colours and styling
match what the user sees in the app.
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Sequence, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPen

from app import theme as theme_mod


# ── Page geometry (US Letter text column) ────────────────────────────
DPI          = 300
PAGE_W_IN    = 8.5            # US Letter
MARGIN_IN    = 1.0            # nominal; image scales to \linewidth anyway
TEXT_W_PX    = int(round((PAGE_W_IN - 2 * MARGIN_IN) * DPI))   # 1950

DEFAULT_COLS = 4
COL_GUTTER   = 56             # px between columns (keeps footers apart)
ROW_GUTTER   = 64             # px between rows
PAD          = 28             # px page padding
HEADER_H     = 40             # px per-cell index strip
FOOTER_H     = 38             # px per-cell rooms/edges strip
LEGEND_H     = 78             # px legend strip at the top

# Label colours (independent of theme — printed on white)
IDX_COLOR    = "#3c4043"
INFO_NUM     = "#3c4043"
INFO_LABEL   = "#9aa0a6"


def _pal(theme: str) -> dict:
    return theme_mod.PALETTES.get(theme, theme_mod.PALETTES["offwhite"])


def _node_positions(graph: dict, gx: float, gy: float,
                    side: float) -> Dict[str, Tuple[float, float]]:
    """Settled (final) node positions mapped into a square graph area."""
    src = float(graph.get("canvas_size", 200) or 200)
    s = side / src
    out: Dict[str, Tuple[float, float]] = {}
    for n in graph.get("nodes", []):
        out[n["id"]] = (gx + float(n["x"]) * s, gy + float(n["y"]) * s)
    return out


def _draw_graph(p: QPainter, graph: dict, pal: dict,
                gx: float, gy: float, side: float) -> None:
    """Draw one graph's edges + room nodes into the square (gx,gy,side).

    No grid, no compass, no boundary — just the settled node-link diagram.
    """
    ink2    = QColor(*pal["ink2"])
    accent  = QColor(*pal["accent"])
    surface = QColor(*pal["surface"])

    node_r = max(3.5, side / 32.0)
    pos = _node_positions(graph, gx, gy, side)

    # Edges (settled — single style, mirrors AnimatedGraph)
    for e in graph.get("edges_ordered", []):
        a = pos.get(e["source"]); b = pos.get(e["target"])
        if a is None or b is None:
            continue
        col = QColor(ink2); col.setAlpha(150)
        p.setPen(QPen(col, max(1.0, node_r / 7.0)))
        p.drawLine(QPointF(*a), QPointF(*b))

    # Room nodes (skip direction anchors, exactly like the widget)
    for n in graph.get("nodes", []):
        if n.get("is_direction"):
            continue
        pt = pos[n["id"]]
        if n.get("is_primary"):
            ring = QColor(accent); ring.setAlphaF(0.30)
            rpen = QPen(ring, max(0.8, node_r / 16.0))
            rpen.setStyle(Qt.DashLine)
            rpen.setDashPattern([1.5, 1.5])
            p.setPen(rpen); p.setBrush(Qt.NoBrush)
            rr = node_r * 1.7
            p.drawEllipse(QPointF(*pt), rr, rr)
        nt = n.get("type", "other")
        col = QColor(theme_mod.ROOM_COLORS.get(nt, "#9aa0a6"))
        col.setAlphaF(0.92)
        p.setBrush(QBrush(col))
        p.setPen(QPen(surface, max(0.8, node_r / 14.0)))
        r_dot = node_r * (0.85 if nt == "outside" else 1.0)
        p.drawEllipse(QPointF(*pt), r_dot, r_dot)


def _counts(graph: dict) -> Tuple[int, int]:
    rooms = sum(1 for n in graph.get("nodes", [])
                if not n.get("is_direction") and n.get("type") != "outside")
    edges = len(graph.get("edges_ordered", []))
    return rooms, edges


# Preferred legend order; only types actually present are shown.
_TYPE_ORDER = ["living", "bedroom", "kitchen", "dining", "study",
               "bathroom", "balcony", "storage", "stair", "veranda",
               "garden", "parking", "outside"]


def _collect_legend(graphs: Sequence[dict]) -> list:
    """Ordered [(type, '#rrggbb'), ...] for the room types that appear."""
    present = set()
    for g in graphs:
        for n in g.get("nodes", []):
            if n.get("is_direction"):
                continue
            present.add(n.get("type", "other"))
    ordered = [t for t in _TYPE_ORDER if t in present]
    ordered += sorted(t for t in present if t not in _TYPE_ORDER)
    return [(t, theme_mod.ROOM_COLORS.get(t, "#9aa0a6")) for t in ordered]


def _draw_legend(p: QPainter, items: list, x: float, y: float,
                 w: float, h: float) -> None:
    """One horizontal legend row: swatch + label per room type."""
    if not items:
        return
    f = QFont(); f.setPixelSize(27)
    p.setFont(f)
    fm = p.fontMetrics()
    dot_r = 11.0
    gap = 12.0          # dot → label
    item_gap = 40.0     # between legend entries
    widths = [2 * dot_r + gap + fm.horizontalAdvance(t.capitalize())
              for t, _ in items]
    total = sum(widths) + item_gap * (len(items) - 1)
    cx = x + max(0.0, (w - total) / 2.0)     # centre the legend
    cy = y + h / 2.0
    for (t, hexcol), iw in zip(items, widths):
        col = QColor(hexcol); col.setAlphaF(0.92)
        p.setBrush(QBrush(col)); p.setPen(QPen(QColor(255, 255, 255), 1.0))
        p.drawEllipse(QPointF(cx + dot_r, cy), dot_r, dot_r)
        p.setPen(QColor("#3c4043"))
        p.drawText(QRectF(cx + 2 * dot_r + gap, y, iw, h),
                   Qt.AlignLeft | Qt.AlignVCenter, t.capitalize())
        cx += iw + item_gap


def save_connectivity_png(graphs: Sequence[dict],
                          path: str,
                          cols: int = DEFAULT_COLS,
                          theme: str = "offwhite",
                          title: Optional[str] = None) -> bool:
    """Render ``graphs`` into one US-Letter-width contact sheet PNG.

    Returns True on a successful write.
    """
    n = len(graphs)
    if n == 0:
        return False
    cols = max(1, int(cols))
    rows = math.ceil(n / cols)
    pal = _pal(theme)

    cell_w = (TEXT_W_PX - 2 * PAD - (cols - 1) * COL_GUTTER) / cols
    graph_side = cell_w                      # square graph area
    cell_h = HEADER_H + graph_side + FOOTER_H

    title_h = 56 if title else 0
    legend = _collect_legend(graphs)
    page_w = TEXT_W_PX
    page_h = int(round(2 * PAD + title_h + LEGEND_H
                       + rows * cell_h + (rows - 1) * ROW_GUTTER))

    img = QImage(page_w, page_h, QImage.Format_ARGB32)
    img.fill(QColor(255, 255, 255))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)

    y0 = PAD
    if title:
        f_t = QFont(); f_t.setPixelSize(34); f_t.setBold(True)
        p.setFont(f_t); p.setPen(QColor(IDX_COLOR))
        p.drawText(QRectF(PAD, PAD, page_w - 2 * PAD, title_h - 10),
                   Qt.AlignLeft | Qt.AlignVCenter, title)
        y0 = PAD + title_h

    # Legend strip (one per sheet)
    _draw_legend(p, legend, PAD, y0, page_w - 2 * PAD, LEGEND_H)
    y0 += LEGEND_H

    f_idx = QFont(); f_idx.setPixelSize(29); f_idx.setBold(True)
    f_info = QFont(); f_info.setPixelSize(26)

    for i, g in enumerate(graphs):
        r, c = divmod(i, cols)
        cx = PAD + c * (cell_w + COL_GUTTER)
        cy = y0 + r * (cell_h + ROW_GUTTER)

        # Header: #index
        p.setFont(f_idx); p.setPen(QColor(IDX_COLOR))
        idx = int(g.get("meta", {}).get("plan_index", i))
        p.drawText(QRectF(cx, cy, cell_w, HEADER_H),
                   Qt.AlignLeft | Qt.AlignVCenter, f"#{idx:02d}")

        # Graph (no grid/compass/border)
        _draw_graph(p, g, pal, cx, cy + HEADER_H, graph_side)

        # Footer: N rooms ............ N edges
        rooms, edges = _counts(g)
        fy = cy + HEADER_H + graph_side
        p.setFont(f_info)
        p.setPen(QColor(INFO_NUM))
        p.drawText(QRectF(cx, fy, cell_w, FOOTER_H),
                   Qt.AlignLeft | Qt.AlignVCenter, f"{rooms}")
        p.setPen(QColor(INFO_LABEL))
        fm = p.fontMetrics()
        rn_w = fm.horizontalAdvance(f"{rooms} ")
        p.drawText(QRectF(cx + rn_w, fy, cell_w, FOOTER_H),
                   Qt.AlignLeft | Qt.AlignVCenter, "rooms")
        p.setPen(QColor(INFO_NUM))
        en = f"{edges}"
        lbl_w = fm.horizontalAdvance(" edges")
        p.drawText(QRectF(cx, fy, cell_w - lbl_w, FOOTER_H),
                   Qt.AlignRight | Qt.AlignVCenter, en)
        p.setPen(QColor(INFO_LABEL))
        p.drawText(QRectF(cx, fy, cell_w, FOOTER_H),
                   Qt.AlignRight | Qt.AlignVCenter, "edges")

    p.end()
    return bool(img.save(path, "PNG"))
