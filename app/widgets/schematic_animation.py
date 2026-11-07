"""
Export the schematic synthesis animation as GIF and/or MP4.

Frames are rendered off-screen with the same :func:`paint_frame` the
canvas uses, so the files show exactly what the app shows. GIF needs
Pillow; MP4 needs ``imageio`` + ``imageio-ffmpeg`` (bundles ffmpeg)::

    pip install pillow imageio imageio-ffmpeg
"""

from __future__ import annotations

import os
from typing import List, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter

from app.widgets.schematic_canvas import get_synthesis, paint_frame


def encoders_available() -> Tuple[bool, bool]:
    """(gif_ok, mp4_ok)"""
    try:
        import PIL  # noqa: F401
        gif_ok = True
    except Exception:
        gif_ok = False
    try:
        import imageio  # noqa: F401
        import imageio_ffmpeg  # noqa: F401
        mp4_ok = True
    except Exception:
        mp4_ok = False
    return gif_ok, mp4_ok


def _qimage_to_rgb(img: QImage):
    import numpy as np
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h = img.width(), img.height()
    ptr = img.constBits()
    arr = np.frombuffer(ptr, dtype=np.uint8, count=img.sizeInBytes())
    arr = arr.reshape(h, img.bytesPerLine())[:, : w * 3].reshape(h, w, 3)
    return arr.copy()


def render_frames(schematic: dict, alignment: str, pal: dict, size: int = 900, *,
                  show_edges: bool = True, show_walls: bool = True,
                  show_furniture: bool = True, every: int = 1) -> List[QImage]:
    """Render every ``every``-th synthesis frame to a ``size``×``size`` image."""
    syn = get_synthesis(schematic, alignment)
    bg = pal.get("surface", (255, 255, 255))
    out: List[QImage] = []
    frames = syn.frames
    # keep the last frame always, and collapse the identical "hold" repeats
    # into their own timing by just emitting them (cheap).
    for i, f in enumerate(frames):
        if i % every and i != len(frames) - 1:
            continue
        img = QImage(size, size, QImage.Format_ARGB32)
        img.fill(QColor(*bg) if bg is not None else Qt.white)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        paint_frame(p, schematic, syn, f, float(size), float(size), pal,
                    mode="rectangle", show_edges=show_edges, show_labels=True,
                    show_walls=show_walls, show_furniture=show_furniture,
                    show_caption=True, draw_border=False, font_scale=max(1.0, size / 320.0))
        p.end()
        out.append(img)
    return out


def export_animation(schematic: dict, alignment: str, pal: dict, out_dir: str,
                     stem: str, *, frame_ms: int = 40, size: int = 900,
                     show_edges: bool = True, show_walls: bool = True,
                     show_furniture: bool = True) -> dict:
    """Write ``<out_dir>/<stem>.gif`` and ``.mp4``. Returns
    ``{"written": [...], "failed": [...]}``."""
    gif_ok, mp4_ok = encoders_available()
    imgs = render_frames(schematic, alignment, pal, size, show_edges=show_edges,
                         show_walls=show_walls, show_furniture=show_furniture)
    written: List[str] = []
    failed: List[str] = []
    if not imgs:
        return {"written": written, "failed": [stem]}
    arrays = None
    try:
        arrays = [_qimage_to_rgb(im) for im in imgs]
    except Exception:
        arrays = None

    if gif_ok:
        try:
            from PIL import Image
            if arrays is not None:
                pil = [Image.fromarray(a) for a in arrays]
            else:
                pil = []
                for im in imgs:
                    im = im.convertToFormat(QImage.Format_RGB888)
                    pil.append(Image.frombytes("RGB", (im.width(), im.height()),
                                               bytes(im.constBits()), "raw", "RGB", im.bytesPerLine()))
            # GIF: palette-quantise, hold the last frame for 2 s
            durations = [frame_ms] * len(pil)
            durations[-1] = 2000
            path = os.path.join(out_dir, stem + ".gif")
            pil[0].save(path, save_all=True, append_images=pil[1:], duration=durations,
                        loop=0, optimize=False)
            written.append(path)
        except Exception:
            failed.append(stem + ".gif")
    if mp4_ok and arrays is not None:
        try:
            import imageio
            fps = max(1, int(round(1000.0 / float(frame_ms))))
            path = os.path.join(out_dir, stem + ".mp4")
            # macro_block_size=1 keeps the exact pixel size; libx264 default
            with imageio.get_writer(path, fps=fps, codec="libx264", quality=8,
                                    macro_block_size=1, pixelformat="yuv420p") as wr:
                for a in arrays:
                    wr.append_data(a)
                for _ in range(fps * 2):          # hold the finished plan 2 s
                    wr.append_data(arrays[-1])
            written.append(path)
        except Exception:
            failed.append(stem + ".mp4")
    return {"written": written, "failed": failed}
