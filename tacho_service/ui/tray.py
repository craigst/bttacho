"""Tray icon.

The icon is drawn at runtime from the palette rather than loaded from a file,
so it stays legible on light and dark panels and can carry a state badge
without shipping a sprite per state.
"""

from typing import Callable, Optional

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

IDLE = "idle"
READING = "reading"
PENDING = "pending"
ERROR = "error"
ONLINE = "online"

BADGE = {
    READING: QColor("#3daee9"),
    PENDING: QColor("#e67e22"),
    ERROR: QColor("#e74c3c"),
    ONLINE: QColor("#39e58c"),
}

TOOLTIP = {
    IDLE: "Tacho — waiting for card",
    READING: "Tacho — reading card",
    PENDING: "Tacho — deliveries queued",
    ERROR: "Tacho — delivery failed",
    ONLINE: "Tacho — SQL online",
}


def _icon(state: str, colour: QColor) -> QIcon:
    """A card outline with a chart line, plus a state badge."""
    size = 64
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    pen = QPen(colour, 5)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)

    p.drawRoundedRect(QRectF(8, 14, 48, 36), 5, 5)
    p.drawLine(8, 25, 56, 25)                     # magnetic stripe
    p.drawLine(17, 41, 26, 34)                    # activity trace
    p.drawLine(26, 34, 35, 39)
    p.drawLine(35, 39, 47, 31)

    badge = BADGE.get(state)
    if badge:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(badge)
        p.drawEllipse(QRectF(40, 34, 20, 20))
    p.end()
    return QIcon(pm)


class Tray(QSystemTrayIcon):
    def __init__(self, palette_colour: QColor, parent=None):
        super().__init__(parent)
        self._colour = palette_colour
        self._state = IDLE
        self._sql_online: Optional[bool] = None

        self.menu = QMenu()
        self.act_show = QAction("Show last report")
        self.act_send = QAction("Send queued now")
        self.act_auto = QAction("Auto-sync")
        self.act_auto.setCheckable(True)
        self.act_settings = QAction("Settings…")
        self.act_quit = QAction("Quit")

        self.status_action = QAction("Waiting for card")
        self.status_action.setEnabled(False)

        self.menu.addAction(self.status_action)
        self.menu.addSeparator()
        self.menu.addAction(self.act_show)
        self.menu.addAction(self.act_send)
        self.menu.addSeparator()
        self.menu.addAction(self.act_auto)
        self.menu.addAction(self.act_settings)
        self.menu.addSeparator()
        self.menu.addAction(self.act_quit)
        self.setContextMenu(self.menu)

        self.set_state(IDLE)

    def set_state(self, state: str, detail: Optional[str] = None):
        if state == IDLE:
            if self._sql_online is True:
                state = ONLINE
            elif self._sql_online is False:
                state = ERROR
                detail = detail or "Tacho — SQL offline"
        self._state = state
        self.setIcon(_icon(state, self._colour))
        self.setToolTip(detail or TOOLTIP.get(state, "Tacho"))
        self.status_action.setText(detail or TOOLTIP.get(state, "Tacho"))

    def set_sql_status(self, online: Optional[bool], detail: str = ""):
        """Show a factual SQL health badge without masking active card work."""
        self._sql_online = online
        if self._state in (IDLE, ONLINE) or online is False:
            if online is True:
                self.set_state(ONLINE, detail or "Tacho — SQL online")
            elif online is False:
                self.set_state(ERROR, detail or "Tacho — SQL offline")
            else:
                self.set_state(IDLE, detail or "Tacho — SQL not checked")

    def notify(self, title: str, message: str, error: bool = False):
        icon = (QSystemTrayIcon.MessageIcon.Critical if error
                else QSystemTrayIcon.MessageIcon.Information)
        self.showMessage(title, message, icon, 8000)
