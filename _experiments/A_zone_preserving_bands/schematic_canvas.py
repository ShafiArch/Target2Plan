"""
SchematicCanvas — one schematic plan, bubble or rectangle mode.

Journal version: the rectangle mode is a real schematic plan — rooms
slid into contact, double-line walls, doors on the connectivity edges,
windows sized by the planned WWR, an open living/kitchen and furniture
blocks (see :mod:`models.schematic_layout`). The synthesis is recorded
as frames, so the canvas can **replay** it (``replay()``) at a given
speed (``set_anim_speed_ms``); the last frame is the static plan.

The paint routine is a module function (:func:`paint_frame`) so the
GIF / MP4 exporter and the batch contact sheet draw exactly what the
widget shows.
"""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal, QTimer
from PySide6.QtGui import (QBrush, QColor, QFont, QMouseEvent, QPainter,
                           QPainterPath, QPen)
from PySide6.QtWidgets import QSizePolicy, QWidget

from app import theme as theme_mod
from models.schematic_layout import (
    synthesize, Synthesis, WALL_EXT_M, WALL_INT_M, _bounds,
)

REFERENCE_BOX_SIZE = 600.0


def _theme_palette(widget: QWidget) -> dict:
    top = widget.window()
    if top is not None and hasattr(top, "store"):
        return theme_mod.PALETTES.get(
            top.store.ui.theme, theme_mod.PALETTES["offwhite"])
    return theme_mod.PALETTES["offwhite"]


def reference_box(schematic: dict) -> tuple[float, float]:
    """Reference box (w, h) for a schematic: the long side is
    REFERENCE_BOX_SIZE, letterboxed to the plan's aspect ratio."""
    conn = schematic.get("source_connectivity") or {}
    plan_w = float(conn.get("plan_width", 0.0) or 0.0)
    plan_h = float(conn.get("plan_height", 0.0) or 0.0)
    if plan_w > 0 and plan_h > 0:
        aspect = plan_w / plan_h
        if aspect >= 1.0:
            return REFERENCE_BOX_SIZE, REFERENCE_BOX_SIZE / aspect
        return REFERENCE_BOX_SIZE * aspect, REFERENCE_BOX_SIZE
    return REFERENCE_BOX_SIZE, REFERENCE_BOX_SIZE


def get_synthesis(schematic: dict, alignment: str) -> Synthesis:
    """Synthesis for a schematic + alignment, cached on the schematic
    dict so thumbnails, detail view and exporters share one run."""
    cache = schematic.setdefault("_synthesis", {})
    syn = cache.get(alignment)
    if syn is None:
        bw, bh = reference_box(schematic)
        syn = synthesize(schematic, alignment, bw, bh)
        cache[alignment] = syn
    return syn


# ═════════════════════════════════════════════════════════════════════
#  Painting
# ═════════════════════════════════════════════════════════════════════

def paint_frame(p: QPainter, schematic: dict, syn: Synthesis, frame: dict,
                w: float, h: float, pal: dict, *, mode: str = "rectangle",
                show_edges: bool = True, show_labels: bool = False,
                show_walls: bool = True, show_furniture: bool = True,
                show_caption: bool = False, draw_border: bool = True,
                font_scale: float = 1.0) -> dict:
    """Draw one frame into ``p`` (a ``w``×``h`` area). Returns the
    transform ``{"scale", "ox", "oy", "box"}`` used (for hit-testing)."""
    ink     = QColor(*pal["ink"])
    ink2    = QColor(*pal["ink2"])
    ink3    = QColor(*pal["ink3"])
    border  = QColor(*pal["border"])
    accent  = QColor(*pal["accent"])
    surface = QColor(*pal["surface"])

    if draw_border:
        p.setPen(QPen(border, 0.5)); p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0))

    rx, ry, rw, rh = syn.box
    margin = max(8.0, min(w, h) * 0.06)
    avail_w = max(1.0, w - 2.0 * margin)
    avail_h = max(1.0, h - 2.0 * margin - (12.0 if show_caption else 0.0))
    scale = min(avail_w / rw, avail_h / rh) if rw > 0 and rh > 0 else 1.0
    box_w_px, box_h_px = rw * scale, rh * scale
    box_x_px = (w - box_w_px) / 2.0
    box_y_px = margin + (avail_h - box_h_px) / 2.0
    ox, oy = box_x_px - rx * scale, box_y_px - ry * scale
    T = {"scale": scale, "ox": ox, "oy": oy, "box": (box_x_px, box_y_px, box_w_px, box_h_px)}

    def TX(x): return ox + x * scale
    def TY(y): return oy + y * scale
    def R(x0, y0, x1, y1): return QRectF(TX(x0), TY(y0), (x1 - x0) * scale, (y1 - y0) * scale)

    shapes = frame.get("shapes", [])
    by_id = {s["room_id"]: s for s in shapes}
    walls_frac = float(frame.get("walls", 0.0)) if (show_walls and mode == "rectangle") else 0.0
    n_open = int(frame.get("openings", 0)) if walls_frac >= 1.0 else 0
    n_furn = int(frame.get("furniture", 0)) if (show_furniture and mode == "rectangle") else 0
    metre = syn.metre

    # 1. connectivity edges (dashed, behind everything)
    if show_edges:
        faint = QColor(ink3.red(), ink3.green(), ink3.blue(), 110)
        pen = QPen(faint, 0.7); pen.setStyle(Qt.CustomDashLine); pen.setDashPattern([2.0, 2.0])
        p.setPen(pen)
        for e in schematic.get("edges", []) or []:
            a, b = by_id.get(e.get("source")), by_id.get(e.get("target"))
            if a is None or b is None:
                continue
            p.drawLine(QPointF(TX(a["cx"]), TY(a["cy"])), QPointF(TX(b["cx"]), TY(b["cy"])))

    # 2. rooms
    fill_alpha = 0.55 if (walls_frac > 0 or n_furn > 0) else 0.9
    for s in shapes:
        if s["room"]["room_type"] == "hall":
            # circulation: a faint neutral wash, no outline
            col = QColor(ink3); col.setAlphaF(0.10)
            p.setBrush(QBrush(col)); p.setPen(Qt.NoPen)
            p.drawRect(QRectF(TX(s["cx"] - s["w"] / 2), TY(s["cy"] - s["h"] / 2), s["w"] * scale, s["h"] * scale))
            continue
        col = QColor(theme_mod.ROOM_COLORS.get(s["room"]["room_type"], "#9aa0a6"))
        col.setAlphaF(fill_alpha)
        p.setBrush(QBrush(col)); p.setPen(QPen(surface, 0.8))
        kind = s.get("kind", "circle")
        if kind == "rect" and "w" in s:
            m = float(s.get("morph", 1.0))
            r_ = s.get("r", math.sqrt(s["w"] * s["h"] / math.pi))
            # morph: circle → rectangle (rounded corners shrink to 0)
            ww = r_ * 2 + (s["w"] - r_ * 2) * m
            hh = r_ * 2 + (s["h"] - r_ * 2) * m
            rect = QRectF(TX(s["cx"] - ww / 2), TY(s["cy"] - hh / 2), ww * scale, hh * scale)
            rad = (1.0 - m) * min(ww, hh) / 2 * scale + 1.0 * m
            p.drawRoundedRect(rect, rad, rad)
        else:
            p.drawEllipse(QPointF(TX(s["cx"]), TY(s["cy"])), s["r"] * scale, s["r"] * scale)

    # 3. walls (hollow bands = double lines) with openings cut out
    if walls_frac > 0 and syn.walls:
        t_ext, t_int = WALL_EXT_M * metre, WALL_INT_M * metre
        ext = [wl for wl in syn.walls if wl["kind"] == "ext"]
        inn = [wl for wl in syn.walls if wl["kind"] == "int"]
        rails = [wl for wl in syn.walls if wl["kind"] == "rail"]
        n_ext = int(round(len(ext) * min(1.0, walls_frac * 2.0)))
        n_int = int(round(len(inn) * max(0.0, walls_frac * 2.0 - 1.0)))
        band = QPainterPath(); band.setFillRule(Qt.WindingFill)

        def add_band(wl, thick, outward: bool):
            nx, ny = wl["out"]
            x1, y1, x2, y2 = wl["x1"], wl["y1"], wl["x2"], wl["y2"]
            if outward:
                a0, a1 = 0.0, thick
            else:
                a0, a1 = -thick / 2.0, thick / 2.0
            # build as an axis-aligned rect
            if abs(ny) > 0:      # horizontal wall, band goes in y
                xs = (min(x1, x2), max(x1, x2))
                ys = sorted((y1 + ny * a0, y1 + ny * a1))
            else:                # vertical wall, band goes in x
                xs = sorted((x1 + nx * a0, x1 + nx * a1))
                ys = (min(y1, y2), max(y1, y2))
            # extend exterior bands past true outside corners
            if outward:
                ext_by = thick
                if abs(ny) > 0:
                    xs = (xs[0] - ext_by * _corner_free(wl, syn, "start"), xs[1] + ext_by * _corner_free(wl, syn, "end"))
                else:
                    ys = (ys[0] - ext_by * _corner_free(wl, syn, "start"), ys[1] + ext_by * _corner_free(wl, syn, "end"))
            band.addRect(R(xs[0], ys[0], xs[1], ys[1]))

        for wl in ext[:n_ext]:
            add_band(wl, t_ext, True)
        for wl in inn[:n_int]:
            add_band(wl, t_int, False)
        band = band.simplified()

        # cut the openings out of the wall band
        cut = QPainterPath(); cut.setFillRule(Qt.WindingFill)
        opens = syn.openings[:n_open]
        for o in opens:
            wl = o["wall"]
            th = (t_ext if wl["kind"] == "ext" else t_int) * 1.3
            nx, ny = wl["out"]
            if abs(ny) > 0:
                xs = (min(o["x1"], o["x2"]), max(o["x1"], o["x2"]))
                yc = o["y1"]
                ys = (yc - th, yc + th) if wl["kind"] == "int" else sorted((yc - th * 0.25, yc + ny * th))
            else:
                ys = (min(o["y1"], o["y2"]), max(o["y1"], o["y2"]))
                xc = o["x1"]
                xs = (xc - th, xc + th) if wl["kind"] == "int" else sorted((xc - th * 0.25, xc + nx * th))
            cut.addRect(R(xs[0], ys[0], xs[1], ys[1]))
        if opens:
            band = band.subtracted(cut)

        p.setBrush(QBrush(surface)); p.setPen(QPen(ink, max(0.6, 0.9 * scale / 2.0)))
        p.drawPath(band)
        # balcony railings: one thin line
        if walls_frac >= 0.5:
            p.setPen(QPen(ink2, max(0.5, 0.6 * scale / 2.0)))
            for wl in rails:
                p.drawLine(QPointF(TX(wl["x1"]), TY(wl["y1"])), QPointF(TX(wl["x2"]), TY(wl["y2"])))

        # 4. openings: windows (three thin lines), doors (leaf + swing), entry marker
        for o in opens:
            wl = o["wall"]
            nx, ny = wl["out"]
            if o["kind"] == "window":
                th = t_ext
                p.setPen(QPen(ink, max(0.5, 0.6 * scale / 2.0)))
                for f in (0.0, 0.5, 1.0):
                    off = th * f
                    p.drawLine(QPointF(TX(o["x1"] + nx * off), TY(o["y1"] + ny * off)),
                               QPointF(TX(o["x2"] + nx * off), TY(o["y2"] + ny * off)))
            elif o["kind"] in ("door", "balcony_door", "entry"):
                hx, hy = o["hinge"]
                # far end of the opening (from the hinge)
                fx, fy = (o["x2"], o["y2"]) if (abs(o["x1"] - hx) < 1e-6 and abs(o["y1"] - hy) < 1e-6) else (o["x1"], o["y1"])
                dw = o["width"]
                # swing direction: into the ``into`` room
                into = by_id.get(o["into"])
                ix, iy = (into["cx"], into["cy"]) if into else (hx - nx, hy - ny)
                # wall direction unit (hinge → far)
                ux, uy = (fx - hx) / dw, (fy - hy) / dw
                # perpendicular pointing towards the room centre
                px_, py_ = -uy, ux
                if (ix - hx) * px_ + (iy - hy) * py_ < 0:
                    px_, py_ = uy, -ux
                leaf_pen = QPen(ink, max(0.6, 0.8 * scale / 2.0))
                p.setPen(leaf_pen); p.setBrush(Qt.NoBrush)
                p.drawLine(QPointF(TX(hx), TY(hy)), QPointF(TX(hx + px_ * dw), TY(hy + py_ * dw)))
                # quarter arc from leaf tip to the far jamb
                arc = QPainterPath()
                arc.moveTo(TX(hx + px_ * dw), TY(hy + py_ * dw))
                a0 = math.degrees(math.atan2(-(py_), px_))   # Qt angles: counter-clockwise, y up
                a1 = math.degrees(math.atan2(-(uy), ux))
                sweep = ((a1 - a0 + 540) % 360) - 180
                arc.arcTo(QRectF(TX(hx) - dw * scale, TY(hy) - dw * scale, 2 * dw * scale, 2 * dw * scale), a0, sweep)
                thin = QPen(ink2, max(0.4, 0.5 * scale / 2.0)); thin.setStyle(Qt.SolidLine)
                p.setPen(thin); p.drawPath(arc)
                if o["kind"] == "entry":
                    # small filled triangle outside the entrance, pointing in
                    mx, my = (hx + fx) / 2.0, (hy + fy) / 2.0
                    tri = QPainterPath()
                    s_ = 0.35 * metre
                    tri.moveTo(TX(mx + nx * (t_ext + 0.05 * metre)), TY(my + ny * (t_ext + 0.05 * metre)))
                    tri.lineTo(TX(mx + nx * (t_ext + s_) + ux * s_ * 0.6), TY(my + ny * (t_ext + s_) + uy * s_ * 0.6))
                    tri.lineTo(TX(mx + nx * (t_ext + s_) - ux * s_ * 0.6), TY(my + ny * (t_ext + s_) - uy * s_ * 0.6))
                    tri.closeSubpath()
                    p.setBrush(QBrush(accent)); p.setPen(Qt.NoPen); p.drawPath(tri)
            # "open" passages: nothing drawn — the wall is simply absent

    # 5. furniture (thin outlines)
    if n_furn > 0:
        pen = QPen(ink2, max(0.45, 0.55 * scale / 2.0))
        p.setPen(pen); p.setBrush(Qt.NoBrush)
        for piece in syn.furniture[:n_furn]:
            for prim in piece["prims"]:
                if prim[0] == "rect":
                    _, x, y, pw, ph = prim
                    p.drawRect(QRectF(TX(x), TY(y), pw * scale, ph * scale))
                elif prim[0] == "circle":
                    _, cx, cy, r_ = prim
                    p.drawEllipse(QPointF(TX(cx), TY(cy)), r_ * scale, r_ * scale)
                elif prim[0] == "line":
                    _, x1, y1, x2, y2 = prim
                    p.drawLine(QPointF(TX(x1), TY(y1)), QPointF(TX(x2), TY(y2)))

    # 6. labels (detail view)
    if show_labels:
        f = QFont(); f.setPointSizeF(7 * font_scale); f.setBold(True)
        p.setFont(f)
        halo = QColor(0, 0, 0, 130); white = QColor(255, 255, 255)
        for s in shapes:
            txt = "hall" if s["room"]["room_type"] == "hall" else s["room_id"].replace("_", " ")
            if s["room"]["room_type"] == "hall" and min(s["w"], s["h"]) * scale < 14:
                continue
            box = QRectF(TX(s["cx"]) - 40 * font_scale, TY(s["cy"]) - 6 * font_scale,
                         80 * font_scale, 12 * font_scale)
            p.setPen(QPen(halo, 2.0)); p.drawText(box, Qt.AlignCenter, txt)
            p.setPen(QPen(white)); p.drawText(box, Qt.AlignCenter, txt)

    # 7. compass + optional phase caption
    bx, by, bw, bh = T["box"]
    fs = font_scale
    f_c = QFont(); f_c.setPointSizeF(7 * fs); f_c.setBold(True)
    p.setFont(f_c); p.setPen(QPen(accent))
    p.drawText(QRectF(bx, max(0.0, by - 14 * fs), bw, 12 * fs), Qt.AlignHCenter | Qt.AlignBottom, "N")
    p.drawText(QRectF(bx, min(h - 12 * fs, by + bh + 1), bw, 12 * fs), Qt.AlignHCenter | Qt.AlignTop, "S")
    p.drawText(QRectF(min(w - 14 * fs, bx + bw + 1), by, 12 * fs, bh), Qt.AlignLeft | Qt.AlignVCenter, "E")
    p.drawText(QRectF(max(0.0, bx - 14 * fs), by, 12 * fs, bh), Qt.AlignRight | Qt.AlignVCenter, "W")
    if show_caption and frame.get("label"):
        f_l = QFont(); f_l.setPointSizeF(7 * fs)
        p.setFont(f_l); p.setPen(QPen(ink3))
        p.drawText(QRectF(6 * fs, h - 14 * fs, w - 12 * fs, 12 * fs), Qt.AlignLeft | Qt.AlignVCenter, frame["label"])
    return T


def _corner_free(wl: dict, syn: Synthesis, end: str) -> float:
    """1.0 when the corner at this end of an exterior wall is a true
    outside corner (the perpendicular side there is exterior too), else 0."""
    x = wl["x1"] if end == "start" else wl["x2"]
    y = wl["y1"] if end == "start" else wl["y2"]
    rid = wl["rooms"][0]
    horizontal = abs(wl["y1"] - wl["y2"]) < 1e-6
    for o in syn.walls:
        if o is wl or o["rooms"][0] != rid:
            continue
        o_h = abs(o["y1"] - o["y2"]) < 1e-6
        if o_h == horizontal:
            continue
        # does the perpendicular wall touch this corner?
        if (abs(o["x1"] - x) < 1e-3 and abs(o["y1"] - y) < 1e-3) or (abs(o["x2"] - x) < 1e-3 and abs(o["y2"] - y) < 1e-3):
            return 1.0 if o["kind"] == "ext" else 0.0
    return 0.0


# ═════════════════════════════════════════════════════════════════════
#  Widget
# ═════════════════════════════════════════════════════════════════════

class SchematicCanvas(QWidget):
    """One schematic plan, rendered as bubble or rectangle layout."""

    roomClicked = Signal(dict)
    animationFinished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._schematic: Optional[dict] = None
        self._mode = "rectangle"
        self._alignment = "N"
        self._show_edges = True
        self._show_labels = False
        self._show_walls = True
        self._show_furniture = True
        self._show_caption = False
        self._syn: Optional[Synthesis] = None
        self._frame_idx = -1            # -1 → final frame
        self._anim_speed_ms = 0
        self._timer = QTimer(self); self._timer.timeout.connect(self._tick)
        self._T = None
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    # ── public API ────────────────────────────────────────────────
    def set_schematic(self, sch: Optional[dict]) -> None:
        if sch is not self._schematic:
            self._schematic = sch
            self._invalidate()

    def set_mode(self, mode: str) -> None:
        if mode in ("bubble", "rectangle") and mode != self._mode:
            self._mode = mode
            self.update()

    def set_alignment(self, alignment: str) -> None:
        if alignment in ("N", "S", "E", "W", "C") and alignment != self._alignment:
            self._alignment = alignment
            self._invalidate()

    def set_show_edges(self, on: bool) -> None:
        if self._show_edges != bool(on):
            self._show_edges = bool(on); self.update()

    def set_show_labels(self, on: bool) -> None:
        if self._show_labels != bool(on):
            self._show_labels = bool(on); self.update()

    def set_show_walls(self, on: bool) -> None:
        if self._show_walls != bool(on):
            self._show_walls = bool(on); self.update()

    def set_show_furniture(self, on: bool) -> None:
        if self._show_furniture != bool(on):
            self._show_furniture = bool(on); self.update()

    def set_show_caption(self, on: bool) -> None:
        self._show_caption = bool(on); self.update()

    def set_anim_speed_ms(self, ms: int) -> None:
        """Milliseconds per synthesis frame; 0 = show the final plan."""
        self._anim_speed_ms = max(0, int(ms))
        if self._anim_speed_ms == 0:
            self._timer.stop(); self._frame_idx = -1; self.update()

    def replay(self) -> None:
        """Replay the synthesis from the first frame."""
        self._timer.stop()
        syn = self.synthesis()
        if syn is None or not syn.frames or self._anim_speed_ms <= 0 or self._mode != "rectangle":
            self._frame_idx = -1; self.update(); return
        self._frame_idx = 0
        self._timer.start(self._anim_speed_ms)
        self.update()

    def synthesis(self) -> Optional[Synthesis]:
        if self._schematic is None:
            return None
        if self._syn is None:
            self._syn = get_synthesis(self._schematic, self._alignment)
        return self._syn

    # kept for callers that peek at the packed shapes
    @property
    def _cached_shapes(self) -> list:
        syn = self.synthesis()
        if syn is None:
            return []
        return syn.rects if self._mode == "rectangle" else syn.circles

    # ── internals ─────────────────────────────────────────────────
    def _invalidate(self) -> None:
        self._timer.stop()
        self._syn = None
        self._frame_idx = -1
        self.update()

    def _tick(self) -> None:
        syn = self.synthesis()
        if syn is None:
            self._timer.stop(); return
        self._frame_idx += 1
        if self._frame_idx >= len(syn.frames) - 1:
            self._frame_idx = -1
            self._timer.stop()
            self.animationFinished.emit()
        self.update()

    def current_frame(self) -> Optional[dict]:
        syn = self.synthesis()
        if syn is None or not syn.frames:
            return None
        if self._mode == "bubble":
            # the bubble view is the packed-circle state
            idx = next((i for i, lbl in syn.phases if lbl.startswith("Zone frame")), len(syn.frames))
            return syn.frames[max(0, idx - 1)]
        if self._frame_idx < 0:
            return syn.frames[-1]
        return syn.frames[min(self._frame_idx, len(syn.frames) - 1)]

    def paintEvent(self, _e) -> None:
        if not self._schematic:
            return
        syn = self.synthesis()
        frame = self.current_frame()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if syn is None or frame is None:
            pal = _theme_palette(self)
            p.setPen(QPen(QColor(*pal["border"]), 0.5)); p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0))
            p.end(); return
        self._T = paint_frame(
            p, self._schematic, syn, frame, float(self.width()), float(self.height()),
            _theme_palette(self), mode=self._mode, show_edges=self._show_edges,
            show_labels=self._show_labels, show_walls=self._show_walls,
            show_furniture=self._show_furniture,
            show_caption=self._show_caption and self._frame_idx >= 0)
        p.end()

    # ── interaction ───────────────────────────────────────────────
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.LeftButton or self._T is None:
            super().mousePressEvent(e); return
        scale = self._T["scale"]; ox, oy = self._T["ox"], self._T["oy"]
        px = (float(e.position().x()) - ox) / scale
        py = (float(e.position().y()) - oy) / scale
        frame = self.current_frame() or {}
        for s in reversed(frame.get("shapes", [])):
            if self._hit_test(s, px, py):
                self.roomClicked.emit(s.get("room", {}))
                e.accept(); return
        super().mousePressEvent(e)

    @staticmethod
    def _hit_test(shape: dict, x: float, y: float) -> bool:
        if shape.get("kind") == "rect" and "w" in shape:
            cx, cy = float(shape["cx"]), float(shape["cy"])
            hw, hh = float(shape["w"]) / 2.0, float(shape["h"]) / 2.0
            return (cx - hw) <= x <= (cx + hw) and (cy - hh) <= y <= (cy + hh)
        if "r" in shape:
            cx, cy = float(shape["cx"]), float(shape["cy"])
            r = float(shape["r"]); dx, dy = x - cx, y - cy
            return (dx * dx + dy * dy) <= r * r
        return False
