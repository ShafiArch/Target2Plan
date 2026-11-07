"""
StageCard — a numbered column container.

A bordered, rounded-corner card with a header row showing:
  * the two-digit stage number ("00", "01", ...)
  * a title and subtitle
  * a coloured status dot (green / yellow / grey)

The body fills the remaining vertical space and hosts the stage panel.

Visual reference: ``StageCard.jsx`` in the web app's
``components/layout/`` folder.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)


class StageCard(QFrame):
    """One stage column in the horizontal stage strip."""

    STATUS_GLYPH = {"done": "●", "pending": "●", "idle": "●"}
    STATUS_PROP  = {"done": "green", "pending": "yellow", "idle": "grey"}

    def __init__(self,
                 stage_id: str,
                 number: int,
                 title: str,
                 subtitle: str,
                 width: int,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.stage_id = stage_id

        self.setProperty("stageCard", True)
        self.setFixedWidth(width)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        # ── Header ────────────────────────────────────────────────
        header = QFrame(self)
        header.setObjectName("StageHeader")
        header.setFixedHeight(54)
        h = QHBoxLayout(header)
        h.setContentsMargins(14, 8, 14, 8)
        h.setSpacing(10)

        self.num_lbl = QLabel(f"{number:02d}", header)
        self.num_lbl.setProperty("stageNumber", True)

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(0)
        self.title_lbl = QLabel(title, header)
        self.title_lbl.setProperty("stageTitle", True)
        self.subtitle_lbl = QLabel(subtitle, header)
        self.subtitle_lbl.setProperty("stageSubtitle", True)
        title_col.addWidget(self.title_lbl)
        title_col.addWidget(self.subtitle_lbl)

        self.status_lbl = QLabel("●", header)
        self.status_lbl.setProperty("statusDot", "grey")
        self.status_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.status_lbl.setFixedWidth(16)

        h.addWidget(self.num_lbl)
        h.addLayout(title_col, 1)
        h.addWidget(self.status_lbl)

        # ── Divider ────────────────────────────────────────────────
        divider = QFrame(self)
        divider.setObjectName("StageDivider")
        divider.setFixedHeight(1)

        # ── Body ──────────────────────────────────────────────────
        self.body = QWidget(self)
        self.body.setObjectName("StageBody")
        self._body_layout = QVBoxLayout(self.body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(0)
        self.body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # ── Assemble ──────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(header)
        root.addWidget(divider)
        root.addWidget(self.body, 1)

    # ──────────────────────────────────────────────────────────────
    def set_status(self, status: str) -> None:
        prop  = self.STATUS_PROP.get(status, "grey")
        glyph = self.STATUS_GLYPH.get(status, "●")
        self.status_lbl.setProperty("statusDot", prop)
        self.status_lbl.setText(glyph)
        # QSS property-selector changes require unpolish/polish to take effect.
        self.status_lbl.style().unpolish(self.status_lbl)
        self.status_lbl.style().polish(self.status_lbl)

    def set_body(self, widget: QWidget) -> None:
        """Replace the body content with ``widget``."""
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._body_layout.addWidget(widget)
