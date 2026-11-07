"""
Target2Plan — entry point.

Usage::

    python main.py

Requires:
    PySide6 >= 6.5
    (optional) torch — needed for the actual model inference once the
                       stages/models layers are wired in.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Target2Plan")
    app.setOrganizationName("ARML")

    win = MainWindow()
    win.showMaximized()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
