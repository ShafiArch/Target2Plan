"""
Schematic canvas — port of SchematicCanvas.jsx.

Two modes:
  * **bubble**: circles at Stage 3 positions, sized by area, primaries
    pinned, packed via ``pack_circles`` (no springs)
  * **rectangle**: rectangles with per-type aspect ratios, packed via
    ``pack_rectangles_with_connectivity`` then optionally translated by
    ``align_group``

The layout is computed at paint time (depends on widget pixel size) and
cached by ``(schematic_id, mode, alignment, w, h)``. Repaints that don't
change those (e.g. toggling ``show_edges``) reuse the cache.

Connectivity edges drawn as faint dashed ink3 lines behind the shapes.
N / E / S / W compass labels drawn at the canvas edges in accent pink.
"""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from app import theme as theme_mod
from models.schematic_mock import (
    align_group, compute_area_scale, map_stage3_position,
    pack_circles, pack_rectangles_with_connectivity,
)


PRIMARY_TYPES = {"living", "bedroom", "kitchen"}
STAGE3_SIZE   = 200.0   # Stage 3 reference space (matches JS const)


def _theme_palette(widget: QWidget) -> dict:
    top = widget.window()
    if top is not None and hasattr(top, "store"):
        return theme_mod.PALETTES.get(
            top.store.ui.theme, theme_mod.PALETTES["offwhite"])
    return theme_mod.PALETTES["offwhite"]


class SchematicCanvas(QWidget):
    """One schematic plan, rendered as bubble or rectangle layout."""

    # Emitted when the user clicks a room shape. Payload is the room
    # dict from the schematic. Empty-area clicks propagate to the
    # parent (so card selection still works).
    roomClicked = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._schematic: Optional[dict] = None
        self._mode = "bubble"
        self._alignment = "N"
        self._show_edges = True
        self._show_labels = False
        self._cache_key = None
        self._cached_shapes: list = []
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
            self._invalidate()

    def set_alignment(self, alignment: str) -> None:
        if alignment in ("N", "S", "E", "W", "C") and alignment != self._alignment:
            self._alignment = alignment
            self._invalidate()

    def set_show_edges(self, on: bool) -> None:
        on = bool(on)
        if self._show_edges != on:
            self._show_edges = on
            self.update()   # cache stays valid — edges don't affect layout

    def set_show_labels(self, on: bool) -> None:
        on = bool(on)
        if self._show_labels != on:
            self._show_labels = on
            self.update()

    # ── cache invalidation ────────────────────────────────────────
    def _invalidate(self) -> None:
        self._cache_key = None
        self._cached_shapes = []
        self.update()

    def resizeEvent(self, e) -> None:
        # Layout is now size-independent (packed in reference coords),
        # so resizing only needs to repaint — the cache stays valid.
        self.update()
        super().resizeEvent(e)

    # ── layout ────────────────────────────────────────────────────
    def _ensure_layout(self) -> list:
        if self._schematic is None:
            return []
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return []
        # Cache key INTENTIONALLY excludes (w, h). The layout is computed
        # once at a fixed reference size and scaled at paint time, so the
        # thumbnail and the detail view show geometrically identical
        # arrangements — only the magnification changes.
        key = (id(self._schematic), self._mode, self._alignment)
        if self._cache_key == key and self._cached_shapes:
            return self._cached_shapes

        rooms = self._schematic.get("rooms", [])
        if not rooms:
            self._cache_key = key
            self._cached_shapes = []
            return self._cached_shapes

        # Stage 3 node lookup (room positions only — skip direction anchors)
        conn = self._schematic.get("source_connectivity") or {}
        stage3_size = float(conn.get("canvas_size", STAGE3_SIZE))
        stage3_by_id = {
            n["id"]: n for n in conn.get("nodes", [])
            if not n.get("is_direction")
        }

        # ── Fixed reference box (deterministic, size-independent) ──
        # Pack at REFERENCE_BOX_SIZE on the longer side, letterboxed to
        # the plan's aspect ratio if available. paintEvent transforms
        # this reference space to the actual widget size.
        REFERENCE_BOX_SIZE = 600.0
        plan_w = float(conn.get("plan_width",  0.0) or 0.0)
        plan_h = float(conn.get("plan_height", 0.0) or 0.0)
        if plan_w > 0 and plan_h > 0:
            aspect = plan_w / plan_h
            if aspect >= 1.0:
                box_w = REFERENCE_BOX_SIZE
                box_h = REFERENCE_BOX_SIZE / aspect
            else:
                box_h = REFERENCE_BOX_SIZE
                box_w = REFERENCE_BOX_SIZE * aspect
        else:
            # Legacy / mock — square reference layout
            box_w = box_h = REFERENCE_BOX_SIZE
        # Origin at (0, 0) in reference space — paint applies the offset.
        box_x = 0.0
        box_y = 0.0
        self._ref_box = (box_x, box_y, box_w, box_h)

        area_scale = compute_area_scale(rooms, box_w, box_h, target_fill=0.25)

        initial: list = []
        for r in rooms:
            src = stage3_by_id.get(r["room_id"])
            if src is not None:
                # The Stage 3 src["x"]/src["y"] are pixel coords in a
                # ``canvas_size``-square reference space whose plan_rect
                # is itself letterboxed. We re-map to the schematic's
                # equivalent letterboxed box.
                # Use plan_rect from the source so the mapping survives
                # any inset / centring the upstream graph applied.
                src_rect = conn.get("plan_rect")
                if (src_rect and len(src_rect) == 4
                        and src_rect[2] > 0 and src_rect[3] > 0):
                    sx, sy, sw, sh = src_rect
                    u = (float(src["x"]) - sx) / sw
                    v = (float(src["y"]) - sy) / sh
                else:
                    u = float(src["x"]) / stage3_size
                    v = float(src["y"]) / stage3_size
                bx = box_x + u * box_w
                by = box_y + v * box_h
            else:
                bx = box_x + box_w / 2.0
                by = box_y + box_h / 2.0
            radius = math.sqrt(
                max(1.0, float(r.get("area", 0))) / math.pi) * area_scale
            initial.append({
                "room":     r,
                "room_id":  r["room_id"],
                "cx":       bx,
                "cy":       by,
                "r":        radius,
                "pinned":   r["room_type"] in PRIMARY_TYPES,
            })

        if self._mode == "rectangle":
            rects: list = []
            for c in initial:
                ar = max(0.3, min(3.0,
                                  float(c["room"].get("aspect_ratio", 1.2))))
                area = math.pi * c["r"] * c["r"]
                rh = math.sqrt(area / ar)
                rw = ar * rh
                rects.append({
                    "room":    c["room"],
                    "room_id": c["room_id"],
                    "cx":      c["cx"],
                    "cy":      c["cy"],
                    "w":       rw,
                    "h":       rh,
                })
            pack_rectangles_with_connectivity(
                rects, self._schematic.get("edges", []),
                w=box_w, h=box_h, pad=4, cycles=30,
                centroid_pull=0.05, spring_k=0.3,
            )
            if self._alignment != "C":
                align_group(rects, self._alignment,
                             w=box_w, h=box_h, pad=4,
                             ox=box_x, oy=box_y)
            shapes = rects
        else:
            pack_circles(initial, w=box_w, h=box_h, pad=4,
                          iters=300, gap=0.6, step=0.55,
                          ox=box_x, oy=box_y)
            shapes = initial

        self._cache_key = key
        self._cached_shapes = shapes
        return shapes

    # ── paint ─────────────────────────────────────────────────────
    def paintEvent(self, _e) -> None:
        if not self._schematic:
            return
        shapes = self._ensure_layout()

        pal = _theme_palette(self)
        ink     = QColor(*pal["ink"])
        ink3    = QColor(*pal["ink3"])
        border  = QColor(*pal["border"])
        accent  = QColor(*pal["accent"])
        surface = QColor(*pal["surface"])

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = float(self.width()), float(self.height())

        # 1. Subtle canvas boundary
        p.setPen(QPen(border, 0.5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0))

        if not shapes:
            p.end()
            return

        # ── Reference → widget transform ───────────────────────────
        # Shapes live in a fixed reference-coordinate system (set by
        # _ensure_layout). At paint time we fit the reference box into
        # the available widget area with a uniform scale and offset
        # so the layout looks identical at any widget size (only the
        # magnification changes).
        rx, ry, rw, rh = getattr(self, "_ref_box", (0.0, 0.0, 600.0, 600.0))
        margin = max(8.0, min(w, h) * 0.05)
        avail_w = max(1.0, w - 2.0 * margin)
        avail_h = max(1.0, h - 2.0 * margin)
        scale = (min(avail_w / rw, avail_h / rh)
                  if rw > 0 and rh > 0 else 1.0)
        box_w_px = rw * scale
        box_h_px = rh * scale
        box_x_px = (w - box_w_px) / 2.0
        box_y_px = (h - box_h_px) / 2.0
        # Translate so reference (rx, ry) maps to (box_x_px, box_y_px)
        ox = box_x_px - rx * scale
        oy = box_y_px - ry * scale
        # Stash for compass labels + hit-testing
        self._layout_box  = (box_x_px, box_y_px, box_w_px, box_h_px)
        self._draw_scale  = scale
        self._draw_offset = (ox, oy)

        def TX(x: float) -> float: return ox + x * scale
        def TY(y: float) -> float: return oy + y * scale

        by_id = {s["room_id"]: s for s in shapes}

        # 2. Connectivity edges (dashed ink3, behind shapes)
        if self._show_edges:
            edges = self._schematic.get("edges", [])
            faint = QColor(ink3.red(), ink3.green(), ink3.blue(), 100)
            pen = QPen(faint, 0.6)
            pen.setStyle(Qt.CustomDashLine)
            pen.setDashPattern([2.0, 2.0])
            p.setPen(pen)
            for e in edges:
                a = by_id.get(e["source"])
                b = by_id.get(e["target"])
                if a is None or b is None:
                    continue
                p.drawLine(QPointF(TX(a["cx"]), TY(a["cy"])),
                           QPointF(TX(b["cx"]), TY(b["cy"])))

        # 3. Rooms — circles or rectangles (transformed at draw)
        for s in shapes:
            col = QColor(theme_mod.ROOM_COLORS.get(
                s["room"]["room_type"], "#9aa0a6"))
            col.setAlphaF(0.9)
            p.setBrush(QBrush(col))
            p.setPen(QPen(surface, 0.8))
            if self._mode == "rectangle":
                rect = QRectF(TX(s["cx"] - s["w"] / 2.0),
                              TY(s["cy"] - s["h"] / 2.0),
                              s["w"] * scale, s["h"] * scale)
                p.drawRoundedRect(rect, 1.0, 1.0)
            else:
                p.drawEllipse(QPointF(TX(s["cx"]), TY(s["cy"])),
                              s["r"] * scale, s["r"] * scale)

        # 4. Labels (optional — detail view only) — pixel-size constant
        # White text reads cleanly against the saturated room fills, but
        # labels routinely overflow small rooms (bathrooms, balconies,
        # storage) onto the light page background where pure white would
        # be invisible. A soft semi-transparent dark halo provides
        # legibility in both cases without dominating.
        if self._show_labels:
            f = QFont(); f.setPointSize(7); f.setBold(True)
            p.setFont(f)
            halo  = QColor(0, 0, 0, 130)
            white = QColor(255, 255, 255)
            for s in shapes:
                txt = s["room_id"].replace("_", " ")
                box = QRectF(TX(s["cx"]) - 40, TY(s["cy"]) - 6, 80, 12)
                p.setPen(QPen(halo, 2.0))   # legibility halo (dark)
                p.drawText(box, Qt.AlignCenter, txt)
                p.setPen(QPen(white))
                p.drawText(box, Qt.AlignCenter, txt)

        # 5. Compass labels at the letterboxed plan-rect edges
        bx, by, bw, bh = getattr(
            self, "_layout_box", (0.0, 0.0, w, h))
        f_c = QFont(); f_c.setPointSize(7); f_c.setBold(True)
        p.setFont(f_c)
        p.setPen(QPen(accent))
        p.drawText(QRectF(bx, max(0.0, by - 14), bw, 12),
                   Qt.AlignHCenter | Qt.AlignBottom, "N")
        p.drawText(QRectF(bx, min(h - 12, by + bh + 1), bw, 12),
                   Qt.AlignHCenter | Qt.AlignTop, "S")
        p.drawText(QRectF(min(w - 14, bx + bw + 1), by, 12, bh),
                   Qt.AlignLeft | Qt.AlignVCenter, "E")
        p.drawText(QRectF(max(0.0, bx - 14), by, 12, bh),
                   Qt.AlignRight | Qt.AlignVCenter, "W")
        p.end()

    # ── interaction ───────────────────────────────────────────────
    def mousePressEvent(self, e: QMouseEvent) -> None:
        """Hit-test against the packed shapes; emit ``roomClicked`` on
        a hit and consume the event. Empty-space clicks propagate up
        so the parent (card thumbnail) can still handle selection.

        Shapes are stored in *reference* coordinates (the layout is
        size-independent), so the widget-space mouse position is
        inverse-transformed back to reference coords before hit-testing.
        """
        if e.button() != Qt.LeftButton:
            super().mousePressEvent(e)
            return
        shapes = self._cached_shapes or self._ensure_layout()
        scale  = getattr(self, "_draw_scale", 1.0)
        ox, oy = getattr(self, "_draw_offset", (0.0, 0.0))
        if scale <= 0:
            super().mousePressEvent(e)
            return
        # Widget → reference space
        px = (float(e.position().x()) - ox) / scale
        py = (float(e.position().y()) - oy) / scale
        # Iterate in reverse so the topmost-painted shape wins on
        # overlaps (rectangles overlap occasionally with tight packing).
        for s in reversed(shapes):
            if self._hit_test(s, px, py):
                self.roomClicked.emit(s.get("room", {}))
                e.accept()
                return
        super().mousePressEvent(e)

    @staticmethod
    def _hit_test(shape: dict, x: float, y: float) -> bool:
        if "w" in shape and "h" in shape:
            # Rectangle
            cx, cy = float(shape["cx"]), float(shape["cy"])
            hw, hh = float(shape["w"]) / 2.0, float(shape["h"]) / 2.0
            return (cx - hw) <= x <= (cx + hw) and (cy - hh) <= y <= (cy + hh)
        if "r" in shape:
            # Circle
            cx, cy = float(shape["cx"]), float(shape["cy"])
            r = float(shape["r"])
            dx, dy = x - cx, y - cy
            return (dx * dx + dy * dy) <= r * r
        return False
