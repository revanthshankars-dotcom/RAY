"""Native Windows window for RAY.

Everything functional lives in the local engine and its web interface. This
module only wraps that interface in a proper desktop window, because that is
what a computer assistant should look like: its own window, its own taskbar
entry, a tray icon and the Ctrl+Space command center.

PySide6 is an optional dependency. If it (or Qt's system libraries) is missing,
RAY tells you exactly what to install and offers the browser instead - it never
pretends to have opened a window.
"""

from __future__ import annotations

import sys
from typing import Any


class DesktopUnavailable(RuntimeError):
    pass


def check_dependencies() -> tuple[bool, str]:
    try:
        import PySide6  # noqa: F401
        from PySide6 import QtWebEngineWidgets  # noqa: F401
    except ImportError as exc:
        return False, (
            f"PySide6 with QtWebEngine is not installed ({exc}).\n"
            "Install it with:  pip install PySide6\n"
            "Then run:  python main.py --desktop"
        )
    except OSError as exc:  # missing libGL and friends on Linux
        return False, f"Qt could not load its system libraries: {exc}"
    return True, "ready"


def run_desktop(url: str, app_name: str = "RAY", on_close=None) -> int:
    """Open the native window and block until it closes."""
    available, detail = check_dependencies()
    if not available:
        raise DesktopUnavailable(detail)

    from PySide6.QtCore import QUrl, Qt
    from PySide6.QtGui import QAction, QIcon, QKeySequence, QShortcut
    from PySide6.QtWidgets import (
        QApplication,
        QMainWindow,
        QMenu,
        QMessageBox,
        QSystemTrayIcon,
    )
    from PySide6.QtWebEngineWidgets import QWebEngineView

    class RayWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle(f"{app_name} - Real-time Autonomous Yourself")
            self.resize(1360, 860)
            self.setMinimumSize(1020, 620)

            self.web = QWebEngineView(self)
            self.web.setUrl(QUrl(url))
            self.setCentralWidget(self.web)

            command_shortcut = QShortcut(QKeySequence("Ctrl+Space"), self)
            command_shortcut.activated.connect(
                lambda: self.web.page().runJavaScript("window.openPalette && window.openPalette()")
            )
            refresh_shortcut = QShortcut(QKeySequence("Ctrl+R"), self)
            refresh_shortcut.activated.connect(self.web.reload)

            self._build_tray()
            self._build_menu()

        def _build_menu(self) -> None:
            menu = self.menuBar().addMenu("&RAY")
            for label, script in (
                ("Command center", "window.openPalette()"),
                ("Home", "go('home')"),
                ("Assistant", "go('assistant')"),
                ("Security center", "go('security')"),
                ("Settings", "go('settings')"),
            ):
                action = QAction(label, self)
                action.triggered.connect(lambda _checked=False, s=script: self.web.page().runJavaScript(s))
                menu.addAction(action)
            menu.addSeparator()
            quit_action = QAction("Quit RAY", self)
            quit_action.triggered.connect(self.close)
            menu.addAction(quit_action)

            view_menu = self.menuBar().addMenu("&View")
            reload_action = QAction("Reload interface", self)
            reload_action.triggered.connect(self.web.reload)
            view_menu.addAction(reload_action)
            zoom_in = QAction("Zoom in", self)
            zoom_in.triggered.connect(lambda: self.web.setZoomFactor(min(2.0, self.web.zoomFactor() + 0.1)))
            view_menu.addAction(zoom_in)
            zoom_out = QAction("Zoom out", self)
            zoom_out.triggered.connect(lambda: self.web.setZoomFactor(max(0.6, self.web.zoomFactor() - 0.1)))
            view_menu.addAction(zoom_out)

        def _build_tray(self) -> None:
            self.tray = QSystemTrayIcon(self)
            icon = self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon)
            self.tray.setIcon(icon)
            self.tray.setToolTip(app_name)
            tray_menu = QMenu()
            show_action = tray_menu.addAction("Show RAY")
            show_action.triggered.connect(self._restore)
            command_action = tray_menu.addAction("Command center")
            command_action.triggered.connect(
                lambda: (self._restore(), self.web.page().runJavaScript("window.openPalette()"))
            )
            tray_menu.addSeparator()
            quit_action = tray_menu.addAction("Quit")
            quit_action.triggered.connect(self.close)
            self.tray.setContextMenu(tray_menu)
            self.tray.activated.connect(lambda reason: self._restore() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
            if QSystemTrayIcon.isSystemTrayAvailable():
                self.tray.show()

        def _restore(self) -> None:
            self.showNormal()
            self.raise_()
            self.activateWindow()

        def closeEvent(self, event) -> None:  # noqa: N802
            if on_close:
                try:
                    on_close()
                except Exception:
                    pass
            self.tray.hide()
            super().closeEvent(event)

    qt_app = QApplication.instance() or QApplication(sys.argv)
    qt_app.setApplicationName(app_name)
    qt_app.setOrganizationName("RAY")
    window = RayWindow()
    window.show()
    return int(qt_app.exec())


def open_browser(url: str) -> bool:
    import webbrowser

    return webbrowser.open(url)
