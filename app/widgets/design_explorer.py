"""
Design Explorer — collapsible panel hosting a ParallelCoords plot.

Renders inside Stage 2, between the thumbnail grid and the toolbar.
Header is clickable; the expanded state persists across sessions via
``QSettings``. Port of ``DesignExplorerPanel.jsx``.

When collapsed, just a thin header strip with a hint ("Show parallel
coordinates"). Expanded, it shows the full ParallelCoords.

Public API
----------
``set_tables(tables)``           — re-feed the plot
``set_selected_idxs(idxs)``      — sync selection highlight
``set_hover_idx(idx)``           — sync hover highlight from outside
``lineClicked(int)``             — emitted when user clicks a polyline
``lineHovered(int)``             — emitted when user hovers a polyline
                                   (−1 = nothing hovered)
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from app.widgets.parallel_coords import ParallelCoords


STORAGE_KEY = "ui.designExplorerExpanded"


class _ExplorerHeader(QFrame):
    """Clickable header bar: chevron + DESIGN EXPLORER + meta + hint."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ExplorerHeader")
        self.setCursor(Qt.PointingHandCursor)
        self._expanded = False

        h = QHBoxLayout(self)
        h.setContentsMargins(14, 5, 14, 5)
        h.setSpacing(8)

        self.caret = QLabel("▸")
        self.caret.setProperty("caret", True)
        self.caret.setFixedWidth(10)

        self.title_lbl = QLabel("DESIGN EXPLORER")
        self.title_lbl.setProperty("sectionHeader", True)

        self.meta_lbl = QLabel("· 0 plans · 6 metrics")
        self.meta_lbl.setProperty("sectionSubtitle", True)

        self.hint_lbl = QLabel("Show parallel coordinates")
        self.hint_lbl.setProperty("linkHint", True)
        self.hint_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        h.addWidget(self.caret)
        h.addWidget(self.title_lbl)
        h.addWidget(self.meta_lbl)
        h.addStretch(1)
        h.addWidget(self.hint_lbl)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self.caret.setText("▾" if expanded else "▸")
        self.hint_lbl.setVisible(not expanded)

    def set_meta(self, n_plans: int) -> None:
        self.meta_lbl.setText(f"· {n_plans} plans · 6 metrics")

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class DesignExplorerPanel(QFrame):
    """Collapsible parcoords panel."""

    lineClicked = Signal(int)
    lineHovered = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DesignExplorerPanel")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        # Restore last-used expanded state
        settings = QSettings("ARML", "LayoutGenerator")
        self._expanded = (settings.value(STORAGE_KEY, "false") in
                          ("true", True, "True", "1"))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = _ExplorerHeader(self)
        self.header.clicked.connect(self._toggle)
        root.addWidget(self.header)

        # Divider between header and body when expanded
        self.divider = QFrame(self)
        self.divider.setFixedHeight(1)
        self.divider.setObjectName("ExplorerDivider")

        self.body = QFrame(self)
        self.body.setObjectName("ExplorerBody")
        body_v = QVBoxLayout(self.body)
        body_v.setContentsMargins(0, 0, 0, 0)
        body_v.setSpacing(0)

        self.parcoords = ParallelCoords(self.body)
        self.parcoords.lineClicked.connect(self.lineClicked.emit)
        self.parcoords.lineHovered.connect(self.lineHovered.emit)
        body_v.addWidget(self.parcoords)

        root.addWidget(self.divider)
        root.addWidget(self.body)

        self._apply_expanded()

    # ── public API ─────────────────────────────────────────────────
    def set_tables(self, tables: List[dict]) -> None:
        self.parcoords.set_tables(tables)
        self.header.set_meta(len(tables or []))

    def set_selected_idxs(self, idxs) -> None:
        self.parcoords.set_selected_idxs(idxs)

    def set_hover_idx(self, idx: Optional[int]) -> None:
        self.parcoords.set_hover_idx(idx)

    # ── internal ──────────────────────────────────────────────────
    def _toggle(self) -> None:
        self._expanded = not self._expanded
        QSettings("ARML", "LayoutGenerator").setValue(
            STORAGE_KEY, "true" if self._expanded else "false")
        self._apply_expanded()

    def _apply_expanded(self) -> None:
        self.header.set_expanded(self._expanded)
        self.divider.setVisible(self._expanded)
        self.body.setVisible(self._expanded)
