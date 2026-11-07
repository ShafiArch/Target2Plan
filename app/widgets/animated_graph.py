"""
AnimatedGraph — a custom-painted graph canvas with timed edge reveal.

Port of the web app's ``ConnectivityCanvas.jsx``. The widget owns a
``QTimer`` that steps through the graph's ``edges_ordered`` list one
edge per tick (animation speed configurable in milliseconds). While
the animation runs:

  * Direction anchors (N/S/E/W) stay pinned at canvas edges.
  * Phase 1 — the predicted connectivity: edges appear one at a time
    (latest edge thicker) with every room at the zone the model
    predicted (``x_start``, ``y_start``).
  * Phase 2 — after a short pause, the relocated secondary rooms
    (bathrooms, balcony) travel to their new zones (``x``, ``y``); a
    faint ring marks the predicted spot and a dotted trail the move.

Set ``set_anim_speed_ms(0)`` to skip the animation entirely — the
final state renders immediately. Call ``replay()`` to restart from
frame 0.

Data shape — see ``models/connectivity_mock.py``.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from app import theme as theme_mod


def _theme_palette(widget: QWidget) -> dict:
    top = widget.window()
    if top is not None and hasattr(top, "store"):
        return theme_mod.PALETTES.get(
            top.store.ui.theme, theme_mod.PALETTES["offwhite"])
    return theme_mod.PALETTES["offwhite"]


class AnimatedGraph(QWidget):
    """Connectivity graph canvas with edge-reveal animation."""

    DEFAULT_SIZE = 200
    NODE_RADIUS_DIVISOR = 40    # node_r = min(w,h) / 40
    # Direction anchors are noticeably larger than room dots so the
    # N/S/E/W labels never crowd the outline. We also enforce a 9 px
    # floor so they stay legible on tiny thumbnails.
    DIR_RADIUS_RATIO    = 1.8
    DIR_RADIUS_MIN_PX   = 9.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._graph: Optional[dict] = None
        self._anim_speed_ms = 25
        self._frame = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    # ── public API ─────────────────────────────────────────────────
    def set_graph(self, graph: dict) -> None:
        self._graph = graph or None
        self.replay()

    def set_anim_speed_ms(self, ms: int) -> None:
        """Set animation speed in ms-per-edge. 0 → skip animation."""
        self._anim_speed_ms = max(0, int(ms))
        if self._anim_speed_ms == 0:
            self._timer.stop()
            self._frame = self._total_steps()
            self.update()
        else:
            self.replay()

    def replay(self) -> None:
        """Restart animation from frame 0."""
        self._timer.stop()
        self._frame = 0
        if self._graph and self._anim_speed_ms > 0 and self._total_steps() > 0:
            self._timer.start(self._anim_speed_ms)
        else:
            # Skip directly to final state
            self._frame = self._total_steps()
        self.update()

    # ── internal ──────────────────────────────────────────────────
    # Frames of the second phase (secondary rooms travelling to their
    # relocated zones) and the pause between the two phases.
    MOVE_FRAMES = 24
    PAUSE_FRAMES = 8

    def _n_edges(self) -> int:
        return len(self._graph.get("edges_ordered", [])) if self._graph else 0

    def _has_moves(self) -> bool:
        if not self._graph:
            return False
        return any(abs(n["x"] - n["x_start"]) > 1e-6 or abs(n["y"] - n["y_start"]) > 1e-6
                   for n in self._graph["nodes"])

    def _total_steps(self) -> int:
        if not self._graph:
            return 0
        n = self._n_edges()
        if self._has_moves():
            n += self.PAUSE_FRAMES + self.MOVE_FRAMES
        return n

    def _tick(self) -> None:
        self._frame += 1
        if self._frame >= self._total_steps():
            self._timer.stop()
        self.update()

    def _eased_t(self) -> float:
        """Progress of the relocation phase (0..1), cubic ease-in-out.

        Phase 1 (frames 0 … n_edges) reveals the predicted connectivity
        with every room at its predicted zone; after a short pause the
        relocated rooms travel to their new zones in phase 2."""
        if not self._has_moves():
            return 1.0
        start = self._n_edges() + self.PAUSE_FRAMES
        t = (self._frame - start) / float(self.MOVE_FRAMES)
        t = max(0.0, min(1.0, t))
        return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2

    def _scaled(self, x: float, y: float) -> Tuple[float, float]:
        """Map (x, y) from STAGE3 reference space to widget pixels."""
        if not self._graph:
            return x, y
        src = float(self._graph.get("canvas_size", self.DEFAULT_SIZE))
        w, h = float(self.width()), float(self.height())
        side = min(w, h)
        # Centre the graph in the widget if it's not square.
        ox = (w - side) / 2.0
        oy = (h - side) / 2.0
        s = side / src
        return ox + x * s, oy + y * s

    def _node_positions(self) -> Dict[str, Tuple[float, float]]:
        """Compute current (animated) positions per node id."""
        if not self._graph:
            return {}
        eased = self._eased_t()
        out: Dict[str, Tuple[float, float]] = {}
        for n in self._graph["nodes"]:
            xs, ys = n["x_start"], n["y_start"]
            xf, yf = n["x"], n["y"]
            x = xs + (xf - xs) * eased
            y = ys + (yf - ys) * eased
            out[n["id"]] = self._scaled(x, y)
        return out

    # ── painting ──────────────────────────────────────────────────
    def paintEvent(self, _e) -> None:
        if not self._graph:
            return
        pal = _theme_palette(self)
        ink     = QColor(*pal["ink"])
        ink2    = QColor(*pal["ink2"])
        ink3    = QColor(*pal["ink3"])
        border  = QColor(*pal["border"])
        accent  = QColor(*pal["accent"])
        surface = QColor(*pal["surface"])

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        w, h = float(self.width()), float(self.height())
        side = min(w, h)
        node_r = max(3.0, side / self.NODE_RADIUS_DIVISOR)


        # ── 0. Background: 3×3 zone grid + compass labels ─────────
        # The plan_rect in the graph dict tells us the letterboxed
        # area inside the canvas that maps to the actual plan. We
        # draw the 3×3 zone grid inside that rect and put N/S/E/W
        # decorative labels at the rect edges. These are *not* nodes
        # in the graph — they're background ornament so the viewer
        # can read which zone each primary sits in.
        plan_rect = self._graph.get("plan_rect")
        canvas_src = float(self._graph.get("canvas_size", 200))
        if plan_rect and len(plan_rect) == 4:
            rx_s, ry_s, rw_s, rh_s = plan_rect
            rx,   ry   = self._scaled(rx_s,         ry_s)
            rx2,  ry2  = self._scaled(rx_s + rw_s,  ry_s + rh_s)
            rw, rh = rx2 - rx, ry2 - ry

            # Zone labels Z1 … Z9 (row-major from NW) in the top-left corner
            # of each cell — no grid lines, so the labels never collide
            # with the room nodes, which sit at the cell centres.
            f_zone = QFont(); f_zone.setPointSize(6)
            p.setFont(f_zone)
            zone_col = QColor(ink3); zone_col.setAlphaF(0.55)
            p.setPen(QPen(zone_col))
            cw, ch = rw / 3.0, rh / 3.0
            for row in range(3):
                for col in range(3):
                    z = row * 3 + col + 1
                    p.drawText(QRectF(rx + col * cw + 2.0, ry + row * ch + 1.0,
                                      cw - 4.0, 10.0),
                               Qt.AlignLeft | Qt.AlignTop, f"Z{z}")

            # Compass labels — small text just outside the grid edges
            f_compass = QFont(); f_compass.setPointSize(7); f_compass.setBold(True)
            p.setFont(f_compass)
            comp_col = QColor(ink3); comp_col.setAlphaF(0.85)
            p.setPen(QPen(comp_col))
            fm_pad = 2.0
            # N — above the grid, centred
            p.drawText(QRectF(rx, ry - 14 - fm_pad, rw, 12),
                       Qt.AlignHCenter | Qt.AlignBottom, "N")
            # S — below the grid, centred
            p.drawText(QRectF(rx, ry + rh + fm_pad, rw, 12),
                       Qt.AlignHCenter | Qt.AlignTop, "S")
            # E — right of the grid, vertically centred
            p.drawText(QRectF(rx + rw + fm_pad, ry, 14, rh),
                       Qt.AlignLeft | Qt.AlignVCenter, "E")
            # W — left of the grid, vertically centred
            p.drawText(QRectF(rx - 14 - fm_pad, ry, 14, rh),
                       Qt.AlignRight | Qt.AlignVCenter, "W")

        positions = self._node_positions()

        # Indexed views into the graph
        nodes    = self._graph["nodes"]
        edges    = self._graph.get("edges_ordered", [])
        n_edges  = len(edges)
        frame    = self._frame
        latest_idx = frame - 1

        # ── 0b. Relocation trails: faint ring at the predicted zone and a
        #        dotted line to the new one (only once the move has begun)
        eased = self._eased_t()
        if self._has_moves() and eased > 0.0:
            trail = QColor(ink3); trail.setAlphaF(0.55)
            tpen = QPen(trail, 0.8); tpen.setStyle(Qt.DotLine)
            for n in nodes:
                if not n.get("relocated"):
                    continue
                sx, sy = self._scaled(n["x_start"], n["y_start"])
                fx, fy = self._scaled(n["x"], n["y"])
                p.setPen(tpen); p.setBrush(Qt.NoBrush)
                p.drawLine(QPointF(sx, sy), QPointF(fx, fy))
                p.setPen(QPen(trail, 0.7))
                p.drawEllipse(QPointF(sx, sy), node_r * 0.9, node_r * 0.9)

        # ── 1. Edges (single style, drawn in reveal order) ────────
        visible = min(n_edges, frame)
        for i in range(visible):
            e = edges[i]
            a = positions.get(e["source"])
            b = positions.get(e["target"])
            if a is None or b is None:
                continue
            is_latest = (i == latest_idx)
            col = QColor(ink2)
            if is_latest:
                col.setAlpha(230)
                pen = QPen(col, 1.6)
            else:
                col.setAlpha(140)
                pen = QPen(col, 0.9)
            p.setPen(pen)
            p.drawLine(QPointF(*a), QPointF(*b))

        # ── 2. Room nodes + outside (no direction anchors) ────────
        for n in nodes:
            if n.get("is_direction"):
                # Belt-and-braces: ignore any direction nodes left in
                # a legacy graph dict.
                continue
            pos = positions[n["id"]]
            is_primary = bool(n.get("is_primary"))

            # Pinned-indicator ring around primaries
            if is_primary:
                ring = QColor(accent); ring.setAlphaF(0.30)
                ring_pen = QPen(ring, 0.7)
                ring_pen.setStyle(Qt.DashLine)
                ring_pen.setDashPattern([1.5, 1.5])
                p.setPen(ring_pen)
                p.setBrush(Qt.NoBrush)
                rr = node_r * 1.7
                p.drawEllipse(QPointF(*pos), rr, rr)

            # Filled node — outside uses its own theme colour, smaller
            nt = n.get("type", "other")
            col = QColor(theme_mod.ROOM_COLORS.get(nt, "#9aa0a6"))
            col.setAlphaF(0.92)
            p.setBrush(QBrush(col))
            p.setPen(QPen(surface, 0.8))
            r_dot = node_r * (0.85 if nt == "outside" else 1.0)
            p.drawEllipse(QPointF(*pos), r_dot, r_dot)
        p.end()
