"""The card window.

Native Qt widgets throughout -- the palette, accent colour and light/dark mode
come from the system theme. Hierarchy is built from spacing and font weight,
not from painted chrome.
"""

from typing import List, Optional

from PyQt6.QtCore import QEasingCurve, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QProgressBar,
    QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from tacho_core import DriverReport, TripRecord

# Semantic colour is the only colour used; everything else is theme greyscale.
OK = "#2ecc71"
WARN = "#e67e22"
ERR = "#e74c3c"

ROW_HEIGHT = 34
HEADER_HEIGHT = 30


def _dim(w: QWidget) -> QWidget:
    """Secondary text -- same colour as normal, just lighter."""
    c = w.palette().color(w.foregroundRole())
    c.setAlpha(150)
    w.setStyleSheet(f"color: rgba({c.red()},{c.green()},{c.blue()},{c.alpha()});")
    return w


class Divider(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("line")
        self.setFrameShape(QFrame.Shape.HLine)
        self.setFrameShadow(QFrame.Shadow.Plain)
        self.setFixedHeight(1)


class CardWindow(QWidget):
    send_requested = pyqtSignal()
    settings_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tacho // Card Link")
        self.setMinimumWidth(540)
        self.setStyleSheet("""
          QWidget { background: #080d13; color: #d9eef8; font-size: 12px; }
          QLabel#eyebrow { color: #4dcfff; font-size: 10px; font-weight: 700;
                            letter-spacing: 1.5px; }
          QLabel#statusPill { background: #102836; border: 1px solid #2d9fc8;
                              border-radius: 9px; padding: 3px 9px; }
          QLabel#telemetry { color: #7da8ba; font-family: monospace; }
          QFrame#line { color: #1d6988; background: #1d6988; max-height: 1px; }
          QTableWidget { background: #0d1822; alternate-background-color: #102330;
                         gridline-color: #193f52; border: 1px solid #1e607a;
                         border-radius: 3px; }
          QHeaderView::section { background: #0d2634; color: #67d9ff;
                                 border: 0; border-bottom: 1px solid #31bde9;
                                 padding: 6px; font-weight: 700; }
          QProgressBar { background: #07131c; border: 1px solid #298eb3;
                         border-radius: 3px; color: #dff7ff; font-weight: 700;
                         text-align: center; }
          QProgressBar::chunk { background: #19b9e6; border-radius: 2px; }
          QPushButton { background: #0e2937; border: 1px solid #287a9e;
                        border-radius: 3px; padding: 7px 13px; color: #ccecff;
                        font-weight: 600; }
          QPushButton:hover { background: #17495e; border-color: #7be4ff; }
          QPushButton:default { background: #12617b; border-color: #69e2ff; }
          QPushButton:disabled { color: #536d7a; border-color: #1b3541; }
        """)
        self._phase = "idle"
        self._reader_connected = True
        self._pulse_colour = None
        self._pulse = QVariantAnimation(self)
        self._pulse.setDuration(1600)
        self._pulse.setStartValue(0.4)
        self._pulse.setKeyValueAt(0.5, 1.0)
        self._pulse.setEndValue(0.4)
        self._pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._pulse.setLoopCount(-1)
        self._pulse.valueChanged.connect(self._apply_pulse)
        self._build()
        self.reset()

    # ------------------------------------------------------------------ build

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 19, 22, 18)
        root.setSpacing(0)

        # -- header ----------------------------------------------------------
        head = QHBoxLayout()
        title = QLabel("TACHO // CARD LINK")
        title.setObjectName("eyebrow")
        f = title.font()
        f.setPointSizeF(f.pointSizeF() * 1.18)
        f.setWeight(QFont.Weight.DemiBold)
        title.setFont(f)
        head.addWidget(title)
        self.protocol = QLabel("DIRECT SQL / PC-SC")
        self.protocol.setObjectName("telemetry")
        head.addSpacing(13)
        head.addWidget(self.protocol)
        head.addStretch()
        self.status = QLabel()
        self.status.setObjectName("statusPill")
        head.addWidget(self.status)
        root.addLayout(head)

        root.addSpacing(10)
        root.addWidget(Divider())
        root.addSpacing(14)

        # -- identity --------------------------------------------------------
        self.driver = QLabel()
        f = self.driver.font()
        f.setPointSizeF(f.pointSizeF() * 1.6)
        f.setWeight(QFont.Weight.DemiBold)
        self.driver.setFont(f)
        root.addWidget(self.driver)

        self.card_meta = QLabel()
        root.addWidget(_dim(self.card_meta))
        root.addSpacing(14)

        # -- progress --------------------------------------------------------
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setFormat("  LINK READY  ")
        self.progress.setFixedHeight(20)
        root.addWidget(self.progress)
        root.addSpacing(5)
        self.stage = QLabel()
        self.stage.setObjectName("telemetry")
        root.addWidget(_dim(self.stage))
        root.addSpacing(12)

        # -- trips -----------------------------------------------------------
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Date", "Vehicle", "Hours", "Distance"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        # The table is sized to its contents, so it must never scroll itself.
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setHighlightSections(False)
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft
                               | Qt.AlignmentFlag.AlignVCenter)
        hh.setFixedHeight(HEADER_HEIGHT)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Fixed)
        root.addWidget(self.table)

        self.totals = QLabel()
        self.totals.setObjectName("telemetry")
        root.addSpacing(6)
        root.addWidget(_dim(self.totals))

        # -- delivery status (reading → syncing → synced/failed) -------------
        self.delivery = QLabel()
        self.delivery.setObjectName("telemetry")
        self.delivery.setVisible(False)
        root.addSpacing(8)
        root.addWidget(self.delivery)

        # -- footer ----------------------------------------------------------
        root.addSpacing(12)
        root.addWidget(Divider())
        root.addSpacing(11)

        foot = QHBoxLayout()
        self.sync_state = QLabel()
        foot.addWidget(_dim(self.sync_state))
        foot.addStretch()
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.clicked.connect(self.settings_requested)
        foot.addWidget(self.settings_btn)
        self.send_btn = QPushButton("Send now")
        self.send_btn.setDefault(True)
        self.send_btn.clicked.connect(self.send_requested)
        foot.addWidget(self.send_btn)
        root.addLayout(foot)

    # ----------------------------------------------------------------- states

    def reset(self):
        self._phase = "idle"
        self._stop_pulse()
        self.delivery.setText("")
        self.delivery.hide()
        self._show_idle()
        self.driver.setText("—")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("  WAITING FOR CARD  ")
        self.stage.setText("")
        self.progress.show()
        self.stage.show()
        self.table.setRowCount(0)
        self.table.hide()
        self.totals.setText("")
        self.totals.hide()
        self.send_btn.setEnabled(False)

    def _show_idle(self):
        if self._reader_connected:
            self.set_status("Waiting for card", None)
            self.card_meta.setText("No card in reader")
        else:
            self.set_status("No card reader", None)
            self.card_meta.setText("No card reader detected — plug one in")

    def set_status(self, text: str, colour: Optional[str]):
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {colour};" if colour else "")

    def _start_pulse(self, colour: QColor):
        """A subtle status pulse while card I/O or delivery is active."""
        self._pulse_colour = colour
        if self._pulse.state() != QVariantAnimation.State.Running:
            self._pulse.start()

    def _stop_pulse(self):
        self._pulse.stop()
        self._pulse_colour = None

    def _apply_pulse(self, value):
        if self._pulse_colour is None:
            return
        alpha = max(0.0, min(1.0, float(value)))
        c = self._pulse_colour
        self.status.setStyleSheet(
            f"color: rgba({c.red()},{c.green()},{c.blue()},{int(alpha * 255)});")

    def set_reader_state(self, connected: bool):
        self._reader_connected = connected
        if self._phase == "idle":
            self._show_idle()

    def set_sql_status(self, online: Optional[bool], detail: str = ""):
        """Expose the authenticated database state inside the HUD."""
        if online is True:
            self.delivery.setText(f"● {detail or 'SQL online'}")
            self.delivery.setStyleSheet("color: #39e58c;")
        elif online is False:
            self.delivery.setText(f"● {detail or 'SQL offline'}")
            self.delivery.setStyleSheet("color: #ff5e70;")
        else:
            self.delivery.setText(f"● {detail or 'SQL not checked'}")
            self.delivery.setStyleSheet("color: #7a9caf;")
        self.delivery.show()

    def show_reading(self, driver: Optional[str]):
        self._phase = "reading"
        self._start_pulse(self.palette().color(QPalette.ColorRole.Highlight))
        self.progress.show()
        self.stage.show()
        self.progress.setFormat("  DOWNLOADING  %p%  ")
        # No trips yet -- an empty grid is dead space, so collapse it.
        self.table.hide()
        self.totals.hide()
        self.set_status("● Card connected", None)
        if driver:
            self.driver.setText(driver)
        else:
            self.driver.setText("Reading card…")
        self.card_meta.setText("")
        self.send_btn.setEnabled(False)

    def set_progress(self, done: int, total: Optional[int], stage: str):
        if total:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
            self.stage.setText(f"{stage} — {done * 100 // total}%  "
                               f"({done:,} of {total:,} bytes)")
        else:
            # Size isn't knowable until EF_Application_Identification is read.
            self.progress.setRange(0, 0)
            self.progress.setFormat("  ESTABLISHING SECURE LINK  ")
            self.stage.setText(f"{stage} — scanning card telemetry")

    def show_report(self, report: DriverReport, preview: int, window_days):
        self._phase = "report"
        self._stop_pulse()
        self.driver.setText(report.driver_name)
        expiry = f" · expires {report.card_expiry}" if report.card_expiry != "Unknown" else ""
        self.card_meta.setText(f"{report.country} {report.card_number}{expiry}")

        # Download is done -- a bar pinned at 100% is just noise.
        self.progress.hide()
        self.stage.hide()
        self.table.show()
        self.totals.show()

        self._fill(report.recent(preview))
        span = "all data" if window_days is None else f"last {window_days} days"
        self.totals.setText(
            f"{report.total_trips} trips · {report.total_distance_km:,} km · {span}")
        self.send_btn.setEnabled(True)

    def _fill(self, trips: List[TripRecord]):
        self.table.setRowCount(len(trips))
        for r, t in enumerate(trips):
            try:
                from datetime import datetime
                pretty = datetime.strptime(t.date, "%Y-%m-%d").strftime("%a %d %b")
            except ValueError:
                pretty = t.date
            cells = [pretty, t.vehicle_registration,
                     f"{t.driving_hours:.1f}", f"{t.distance_km:,} km"]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c >= 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, item)

        for r in range(len(trips)):
            self.table.setRowHeight(r, ROW_HEIGHT)
        self.table.setFixedHeight(HEADER_HEIGHT + max(1, len(trips)) * ROW_HEIGHT + 2)

    # -------------------------------------------------------------- delivery

    def set_delivery(self, text: str, colour: Optional[str] = None):
        self.delivery.setText(text)
        self.delivery.setStyleSheet(f"color: {colour};" if colour else "")
        self.delivery.show()
        if "Sending" in text:
            self._start_pulse(self.palette().color(QPalette.ColorRole.Highlight))
        else:
            self._stop_pulse()
        self.set_status(text, colour)

    def set_sync_state(self, auto_sync: bool, destinations: int, pending: int = 0):
        state = "on" if auto_sync else "off"
        bits = [f"Auto-sync {state}", f"{destinations} destination"
                + ("" if destinations == 1 else "s")]
        if pending:
            bits.append(f"{pending} queued")
        self.sync_state.setText(" · ".join(bits))

    def show_error(self, message: str):
        self._stop_pulse()
        self.set_status("● Error", ERR)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.stage.setText(message)
        self.send_btn.setEnabled(False)
