"""Notifications visuelles : bandeaux « toast » dans la fenêtre et bulles
de la zone de notification Windows quand la fenêtre n'est pas au premier plan."""
from __future__ import annotations

import time

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                               QSystemTrayIcon, QVBoxLayout, QWidget)

from ..events import CATEGORY_LABEL, ERROR, SUCCESS, WARNING, NetEvent
from . import theme

LEVEL_TO_COLOR = {ERROR: 3, WARNING: 2, SUCCESS: 0}


class Toast(QFrame):
    def __init__(self, ev: NetEvent, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setFixedWidth(360)
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 12, 0)
        bar = QFrame()
        bar.setFixedWidth(5)
        col = theme.level_color(LEVEL_TO_COLOR.get(ev.level, -1))
        bar.setStyleSheet(f"background:{col}; border-top-left-radius:10px;"
                          "border-bottom-left-radius:10px;")
        lay.addWidget(bar)
        box = QVBoxLayout()
        box.setContentsMargins(6, 10, 0, 10)
        box.setSpacing(2)
        t = QLabel(f"<b>{ev.target or 'NetTrace Pro'}</b> · "
                   f"<span style='color:{col}'>{CATEGORY_LABEL.get(ev.category, ev.category)}</span>")
        m = QLabel(ev.message)
        m.setWordWrap(True)
        box.addWidget(t)
        box.addWidget(m)
        lay.addLayout(box, 1)
        self.eff = QGraphicsOpacityEffect(self)
        self.eff.setOpacity(0.0)
        self.setGraphicsEffect(self.eff)
        self.anim = QPropertyAnimation(self.eff, b"opacity", self)
        self.anim.setDuration(180)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()

    def mousePressEvent(self, _ev):
        self.close_toast()

    def close_toast(self):
        self.anim.stop()
        self.anim.setStartValue(self.eff.opacity())
        self.anim.setEndValue(0.0)
        self.anim.finished.connect(self.deleteLater)
        self.anim.start()


class Notifier:
    """Gère la pile de toasts (max 4) et les bulles système."""

    MAX = 4

    def __init__(self, window: QWidget, tray: QSystemTrayIcon | None, settings):
        self.window = window
        self.tray = tray
        self.settings = settings
        self.toasts: list[Toast] = []
        self._last_tray = 0.0

    def notify(self, ev: NetEvent) -> None:
        if ev.level not in (ERROR, WARNING, SUCCESS) or ev.category == "session":
            return
        if not self.settings.get("ui.notifications", True):
            return
        self._toast(ev)
        if self.settings.get("ui.sound", False) and ev.level == ERROR:
            QApplication.beep()
        active = self.window.isActiveWindow() and not self.window.isMinimized()
        if (self.tray and not active and self.settings.get("ui.tray_notifications", True)
                and ev.level in (ERROR, WARNING, SUCCESS)
                and time.monotonic() - self._last_tray > 5):
            self._last_tray = time.monotonic()
            icon = {ERROR: QSystemTrayIcon.Critical, WARNING: QSystemTrayIcon.Warning}.get(
                ev.level, QSystemTrayIcon.Information)
            self.tray.showMessage(f"NetTrace Pro — {ev.target}", ev.message, icon, 6000)

    def _toast(self, ev: NetEvent) -> None:
        self.toasts = [t for t in self.toasts if _alive(t)]
        while len(self.toasts) >= self.MAX:
            self.toasts.pop(0).close_toast()
        t = Toast(ev, self.window)
        t.adjustSize()
        t.show()
        t.raise_()
        self.toasts.append(t)
        QTimer.singleShot(7000 if ev.level != ERROR else 12000,
                          lambda: _alive(t) and t.close_toast())
        self.reposition()

    def reposition(self) -> None:
        self.toasts = [t for t in self.toasts if _alive(t)]
        y = self.window.height() - 16
        for t in reversed(self.toasts):
            t.adjustSize()
            y -= t.height()
            t.move(self.window.width() - t.width() - 18, y)
            y -= 8


def _alive(w) -> bool:
    try:
        w.objectName()
        return True
    except RuntimeError:  # objet C++ détruit
        return False
