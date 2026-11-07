"""
Draw-geometry mode of Stage 1: a room palette and a drawing canvas.

The user drags a room type (living, bedroom, kitchen, bathroom, balcony)
from the palette onto the canvas; a square room of a typical size lands
where it is dropped. Rooms are then moved by dragging their interior and
resized by dragging an edge or a corner, which sets the room's area and
aspect ratio. Sizes are kept in metres internally; the canvas displays
m² / ft² (``set_units``). ``rooms()`` returns the drawn program, which
Stage 1 turns into an area variation for the rest of the pipeline.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

from PySide6.QtCore import QMimeData, QPointF, QRectF, Qt, Signal, QSize
from PySide6.QtGui import (QBrush, QColor, QDrag, QFont, QKeyEvent, QMouseEvent,
                           QPainter, QPen, QCursor)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

from app import theme as theme_mod

MIME = "application/x-layoutgen-room-type"
FT_PER_M = 3.280839895
SQFT_PER_M2 = FT_PER_M * FT_PER_M

ROOM_LABELS = {"living": "Living", "bedroom": "Bedroom", "kitchen": "Kitchen",
               "bathroom": "Bathroom", "balcony": "Balcony"}
# typical area of a freshly dropped (square) room, m²
DEFAULT_AREA_M2 = {"living": 20.0, "bedroom": 14.0, "kitchen": 9.0,
                   "bathroom": 5.0, "balcony": 5.0}
MIN_SIDE_M = 1.0
SNAP_M = 0.1


def _theme_palette(widget: QWidget) -> dict:
    top = widget.window()
    if top is not None and hasattr(top, "store"):
        return theme_mod.PALETTES.get(top.store.ui.theme, theme_mod.PALETTES["offwhite"])
    return theme_mod.PALETTES["offwhite"]


# ═════════════════════════════════════════════════════════════════════
#  Palette chip — drag it onto the canvas
# ═════════════════════════════════════════════════════════════════════

class RoomChip(QLabel):
    """A draggable room-type chip."""

    def __init__(self, room_type: str, parent: QWidget | None = None) -> None:
        super().__init__(ROOM_LABELS.get(room_type, room_type), parent)
        self.room_type = room_type
        # neutral chip (the room colour appears only as the small swatch)
        self.setStyleSheet(
            "QLabel { padding: 4px 10px 4px 20px; border-radius: 10px; "
            "border: 1px solid rgba(0,0,0,0.14); font-size: 11px; "
            "background: rgba(0,0,0,0.05); }")
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip(f"Drag onto the canvas to add a {ROOM_LABELS.get(room_type, room_type).lower()} "
                        f"({DEFAULT_AREA_M2.get(room_type, 10):.0f} m² square to start)")
        self._press: Optional[QPointF] = None

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(theme_mod.ROOM_COLORS.get(self.room_type, "#9aa0a6"))))
        p.drawRoundedRect(QRectF(7, self.height() / 2 - 4, 8, 8), 2, 2)
        p.end()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self._press = e.position()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._press is None or not (e.buttons() & Qt.LeftButton):
            return
        if (e.position() - self._press).manhattanLength() < 6:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(MIME, self.room_type.encode("utf-8"))
        mime.setText(self.room_type)
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.setHotSpot(e.position().toPoint())
        self._press = None
        drag.exec(Qt.CopyAction)


class RoomPalette(QFrame):
    """Row of draggable chips, one per room type."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        hint = QLabel("Drag a room onto the canvas:")
        hint.setProperty("sectionSubtitle", True)
        lay.addWidget(hint)
        for rt in ("living", "bedroom", "kitchen", "bathroom", "balcony"):
            lay.addWidget(RoomChip(rt, self))
        lay.addStretch(1)


# ═════════════════════════════════════════════════════════════════════
#  Canvas
# ═════════════════════════════════════════════════════════════════════

_HANDLE_PX = 7


class GeometryCanvas(QWidget):
    """Drop target and editor for the drawn rooms (metres inside)."""

    changed = Signal()
    selectionChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(360)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._rooms: List[dict] = []        # {id, type, x, y, w, h} in metres
        self._units = "m"                   # display: "m" or "ft"
        self._plan_w_m = 12.0; self._plan_h_m = 9.0   # kept for the info line only
        self._px_per_m = 28.0               # drawing scale (see set_scale)
        self._pad = 24.0                    # canvas margin, px
        self._selected: set = set()         # ids of the selected rooms
        self._drag: Optional[dict] = None   # {"mode": move|resize|box, ...}
        self._pan = QPointF(0.0, 0.0)       # canvas pan (px) — the group is centred on first show
        self._auto_centre = True            # keep the group centred until the user moves something
        self._box: Optional[QRectF] = None  # rubber-band rectangle (px)
        self._hover_edges: set = set()
        self._counter: Dict[str, int] = {}

    # ── public API ────────────────────────────────────────────────
    def rooms(self) -> List[dict]:
        return [dict(r) for r in self._rooms]

    def set_rooms(self, rooms: List[dict]) -> None:
        self._rooms = [dict(r) for r in rooms]
        self._counter = {}
        for r in self._rooms:
            self._counter[r["type"]] = max(self._counter.get(r["type"], 0), int(r["id"].split("_")[-1]) + 1)
        self._selected = set()
        self.changed.emit(); self.update()

    def set_units(self, units: str) -> None:
        if units in ("m", "ft") and units != self._units:
            self._units = units; self.update()

    def units(self) -> str:
        return self._units

    def set_plan(self, w_m: float, h_m: float) -> None:
        self._plan_w_m, self._plan_h_m = max(1.0, float(w_m)), max(1.0, float(h_m))
        self.update()

    def set_scale(self, px_per_m: float) -> None:
        """Drawing scale in pixels per metre (smaller = more rooms fit)."""
        self._px_per_m = max(6.0, min(120.0, float(px_per_m)))
        self.centre_rooms()

    def scale(self) -> float:
        return self._px_per_m

    def load_sample(self) -> None:
        """A small default program: 1 living, 2 bedrooms, kitchen, bathroom, balcony."""
        # one row, bottoms aligned, in program order; 0.5 m apart
        sizes = [("living", 5.5, 4.0), ("bedroom", 4.0, 3.5), ("bedroom", 4.0, 3.5),
                 ("kitchen", 3.0, 3.0), ("bathroom", 2.0, 2.5), ("balcony", 1.5, 3.0)]
        base = max(h for _, _, h in sizes)
        sample = []
        x = 0.0
        for rt, w, h in sizes:
            sample.append((rt, x, base - h, w, h))
            x += w + 0.5
        self._rooms = []; self._counter = {}
        for rt, x, y, w, h in sample:
            n = self._counter.get(rt, 0); self._counter[rt] = n + 1
            self._rooms.append({"id": f"{rt}_{n}", "type": rt, "x": x, "y": y, "w": w, "h": h})
        self._selected = set()
        self._auto_centre = True
        self.centre_rooms()
        self.changed.emit(); self.selectionChanged.emit(); self.update()

    def centre_rooms(self) -> None:
        """Pan so the drawn group sits in the middle of the canvas."""
        if not self._rooms or self.width() <= 0:
            self._pan = QPointF(0.0, 0.0); self.update(); return
        s = self._px_per_m
        x0 = min(r["x"] for r in self._rooms); x1 = max(r["x"] + r["w"] for r in self._rooms)
        y0 = min(r["y"] for r in self._rooms); y1 = max(r["y"] + r["h"] for r in self._rooms)
        cx, cy = (x0 + x1) / 2 * s, (y0 + y1) / 2 * s
        self._pan = QPointF(self.width() / 2 - cx - self._pad, self.height() / 2 - cy - self._pad)
        self.update()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self._auto_centre and self.width() > 50:
            self.centre_rooms()

    def clear(self) -> None:
        self._rooms = []; self._counter = {}; self._selected = set()
        self.changed.emit(); self.selectionChanged.emit(); self.update()

    def remove_selected(self) -> None:
        if not self._selected:
            return
        self._rooms = [r for r in self._rooms if r["id"] not in self._selected]
        self._selected = set()
        self.changed.emit(); self.selectionChanged.emit(); self.update()

    def selected_ids(self) -> set:
        return set(self._selected)

    def add_room(self, room_type: str, at_m: Optional[tuple] = None) -> dict:
        side = math.sqrt(DEFAULT_AREA_M2.get(room_type, 10.0))
        n = self._counter.get(room_type, 0)
        self._counter[room_type] = n + 1
        if at_m is None:
            # first free spot on a coarse grid
            x = y = 0.5
            placed = False
            vis_w = max(self._plan_w_m, (self.width() - 2 * self._pad) / self._px_per_m)
            vis_h = max(self._plan_h_m, (self.height() - 2 * self._pad) / self._px_per_m)
            while y + side <= vis_h and not placed:
                x = 0.5
                while x + side <= vis_w:
                    if not any(self._overlaps((x, y, side, side), r) for r in self._rooms):
                        placed = True; break
                    x += 0.5
                if not placed:
                    y += 0.5
            at_m = (x, y)
        room = {"id": f"{room_type}_{n}", "type": room_type,
                "x": self._snap(at_m[0] - side / 2), "y": self._snap(at_m[1] - side / 2),
                "w": side, "h": side}
        self._rooms.append(room)
        self._selected = {room["id"]}
        self.changed.emit(); self.selectionChanged.emit(); self.update()
        return room

    def total_area_m2(self) -> float:
        return sum(r["w"] * r["h"] for r in self._rooms)

    def counts(self) -> Dict[str, int]:
        c: Dict[str, int] = {}
        for r in self._rooms:
            c[r["type"]] = c.get(r["type"], 0) + 1
        return c

    # ── geometry helpers ──────────────────────────────────────────
    @staticmethod
    def _snap(v: float) -> float:
        return round(v / SNAP_M) * SNAP_M

    @staticmethod
    def _overlaps(box, r) -> bool:
        x, y, w, h = box
        return not (x + w <= r["x"] or r["x"] + r["w"] <= x or y + h <= r["y"] or r["y"] + r["h"] <= y)

    def _scale(self) -> float:
        return self._px_per_m

    def _origin(self) -> QPointF:
        return QPointF(self._pad + self._pan.x(), self._pad + self._pan.y())

    def _to_px(self, xm: float, ym: float) -> QPointF:
        s = self._scale(); o = self._origin()
        return QPointF(o.x() + xm * s, o.y() + ym * s)

    def _to_m(self, p: QPointF) -> tuple:
        s = self._scale(); o = self._origin()
        return ((p.x() - o.x()) / s, (p.y() - o.y()) / s)

    def _rect_px(self, r: dict) -> QRectF:
        s = self._scale(); tl = self._to_px(r["x"], r["y"])
        return QRectF(tl.x(), tl.y(), r["w"] * s, r["h"] * s)

    def _hit(self, p: QPointF):
        """(room, edges) under the cursor; edges ⊆ {N,S,E,W} when on a handle."""
        for r in reversed(self._rooms):
            rect = self._rect_px(r)
            grow = rect.adjusted(-_HANDLE_PX, -_HANDLE_PX, _HANDLE_PX, _HANDLE_PX)
            if not grow.contains(p):
                continue
            edges = set()
            if abs(p.x() - rect.left()) <= _HANDLE_PX: edges.add("W")
            if abs(p.x() - rect.right()) <= _HANDLE_PX: edges.add("E")
            if abs(p.y() - rect.top()) <= _HANDLE_PX: edges.add("N")
            if abs(p.y() - rect.bottom()) <= _HANDLE_PX: edges.add("S")
            if edges or rect.contains(p):
                return r, edges
        return None, set()

    @staticmethod
    def _cursor_for(edges: set) -> Qt.CursorShape:
        if edges in ({"N", "W"}, {"S", "E"}): return Qt.SizeFDiagCursor
        if edges in ({"N", "E"}, {"S", "W"}): return Qt.SizeBDiagCursor
        if edges & {"N", "S"}: return Qt.SizeVerCursor
        if edges & {"E", "W"}: return Qt.SizeHorCursor
        return Qt.SizeAllCursor

    # ── drag & drop from the palette ──────────────────────────────
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e) -> None:
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        if not e.mimeData().hasFormat(MIME):
            return
        rt = bytes(e.mimeData().data(MIME)).decode("utf-8")
        xm, ym = self._to_m(e.position())
        self.add_room(rt, (xm, ym))
        e.acceptProposedAction()
        self.setFocus()

    # ── mouse: box-select / move (all selected) / resize ────────────
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.LeftButton:
            return
        r, edges = self._hit(e.position())
        shift = bool(e.modifiers() & Qt.ShiftModifier)
        if r is None:
            # empty canvas: start a selection box (AutoCAD-style)
            if not shift:
                self._selected = set()
            self._drag = {"mode": "box", "start": e.position()}
            self._box = QRectF(e.position(), e.position())
            self.selectionChanged.emit(); self.setFocus(); self.update()
            return
        if edges and len(self._selected) <= 1:
            self._selected = {r["id"]}
            self._drag = {"mode": "resize", "edges": edges, "start": e.position(),
                          "orig": dict(r), "room": r}
        else:
            if shift:
                self._selected ^= {r["id"]}
            elif r["id"] not in self._selected:
                self._selected = {r["id"]}
            movers = [x for x in self._rooms if x["id"] in self._selected]
            self._drag = {"mode": "move", "start": e.position(),
                          "origs": {x["id"]: (x["x"], x["y"]) for x in movers}, "rooms": movers}
        self.selectionChanged.emit()
        self.setFocus()
        self.update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag is None:
            r, edges = self._hit(e.position())
            self.setCursor(QCursor(self._cursor_for(edges)) if r else QCursor(Qt.ArrowCursor))
            return
        s = self._scale()
        dx = (e.position().x() - self._drag["start"].x()) / s
        dy = (e.position().y() - self._drag["start"].y()) / s
        mode = self._drag["mode"]
        if mode == "box":
            self._box = QRectF(self._drag["start"], e.position()).normalized()
            self.update(); return
        self._auto_centre = False
        if mode == "move":
            for r in self._drag["rooms"]:
                ox, oy = self._drag["origs"][r["id"]]
                r["x"] = self._snap(ox + dx); r["y"] = self._snap(oy + dy)
        else:
            o = self._drag["orig"]; r = self._drag["room"]
            edges = self._drag["edges"]
            x0, y0, x1, y1 = o["x"], o["y"], o["x"] + o["w"], o["y"] + o["h"]
            if "W" in edges: x0 = min(self._snap(o["x"] + dx), x1 - MIN_SIDE_M)
            if "E" in edges: x1 = max(self._snap(o["x"] + o["w"] + dx), x0 + MIN_SIDE_M)
            if "N" in edges: y0 = min(self._snap(o["y"] + dy), y1 - MIN_SIDE_M)
            if "S" in edges: y1 = max(self._snap(o["y"] + o["h"] + dy), y0 + MIN_SIDE_M)
            r["x"], r["y"], r["w"], r["h"] = x0, y0, x1 - x0, y1 - y0
        self.changed.emit(); self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if self._drag is None:
            return
        if self._drag["mode"] == "box" and self._box is not None:
            box = self._box
            if box.width() > 3 or box.height() > 3:
                for r in self._rooms:
                    if box.intersects(self._rect_px(r)):
                        self._selected.add(r["id"])
            self._box = None
            self.selectionChanged.emit()
        self._drag = None
        self.changed.emit(); self.update()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.remove_selected(); return
        super().keyPressEvent(e)

    # ── painting ──────────────────────────────────────────────────
    def _fmt_area(self, a_m2: float) -> str:
        return f"{a_m2:.1f} m²" if self._units == "m" else f"{a_m2 * SQFT_PER_M2:.0f} ft²"

    def _fmt_len(self, v_m: float) -> str:
        return f"{v_m:.1f}" if self._units == "m" else f"{v_m * FT_PER_M:.1f}"

    def paintEvent(self, _e) -> None:
        pal = _theme_palette(self)
        ink, ink2, ink3 = QColor(*pal["ink"]), QColor(*pal["ink2"]), QColor(*pal["ink3"])
        border, accent = QColor(*pal["border"]), QColor(*pal["accent"])
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self._scale(); o = self._origin()

        # dot grid: one dot per metre (per foot when drawing in feet and
        # the scale allows it, else per 5 ft) over the whole canvas
        if self._units == "m":
            step_m = 1.0
        else:
            step_m = (1.0 / FT_PER_M) if s / FT_PER_M >= 12 else (5.0 / FT_PER_M)
        dot = QColor(ink3); dot.setAlphaF(0.55)
        p.setPen(Qt.NoPen); p.setBrush(QBrush(dot))
        sp = step_m * s
        x = o.x() - math.ceil(o.x() / sp) * sp          # first dot at or left of the edge
        y_first = o.y() - math.ceil(o.y() / sp) * sp
        while x <= self.width():
            y = y_first
            while y <= self.height():
                p.drawEllipse(QPointF(x, y), 1.0, 1.0)
                y += sp
            x += sp
        f = QFont(); f.setPointSize(7)
        p.setFont(f); p.setPen(QPen(ink3))
        unit_lbl = "1 m" if self._units == "m" else ("1 ft" if step_m < 1.0 else "5 ft")
        p.drawText(QRectF(6, self.height() - 16, 200, 12), Qt.AlignLeft | Qt.AlignVCenter,
                   f"dots every {unit_lbl}")
        if not self._rooms:
            f2 = QFont(); f2.setPointSize(9)
            p.setFont(f2); p.setPen(QPen(ink3))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "Drop rooms here, then drag an edge or corner to size them")

        # rooms
        for r in self._rooms:
            rect = self._rect_px(r)
            col = QColor(theme_mod.ROOM_COLORS.get(r["type"], "#9aa0a6"))
            sel = r["id"] in self._selected
            fill = QColor(col); fill.setAlphaF(0.75 if sel else 0.55)
            p.setBrush(QBrush(fill))
            p.setPen(QPen(ink if sel else col.darker(115), 1.4 if sel else 1.0))
            p.drawRect(rect)
            # label: name, area, w × h
            f3 = QFont(); f3.setPointSize(8); f3.setBold(True)
            p.setFont(f3); p.setPen(QPen(QColor(255, 255, 255)))
            p.drawText(rect.adjusted(2, 2, -2, -2), Qt.AlignHCenter | Qt.AlignTop,
                       f"{ROOM_LABELS.get(r['type'], r['type'])} {r['id'].split('_')[-1]}")
            f4 = QFont(); f4.setPointSize(7)
            p.setFont(f4)
            ar = r["w"] / r["h"] if r["h"] > 0 else 1.0
            p.drawText(rect, Qt.AlignCenter,
                       f"{self._fmt_area(r['w'] * r['h'])}\n{self._fmt_len(r['w'])} × {self._fmt_len(r['h'])}\n"
                       f"ar {ar:.2f}")
            if sel and len(self._selected) == 1:
                # corner handles
                p.setBrush(QBrush(QColor(255, 255, 255))); p.setPen(QPen(ink, 1.0))
                for cx, cy in ((rect.left(), rect.top()), (rect.right(), rect.top()),
                               (rect.left(), rect.bottom()), (rect.right(), rect.bottom()),
                               (rect.center().x(), rect.top()), (rect.center().x(), rect.bottom()),
                               (rect.left(), rect.center().y()), (rect.right(), rect.center().y())):
                    p.drawRect(QRectF(cx - 3, cy - 3, 6, 6))
        if self._box is not None:
            bp = QPen(ink3, 1.0); bp.setStyle(Qt.DashLine)
            p.setPen(bp); fillb = QColor(ink3); fillb.setAlphaF(0.08); p.setBrush(QBrush(fillb))
            p.drawRect(self._box)
        p.end()

    def sizeHint(self) -> QSize:
        return QSize(640, 420)
