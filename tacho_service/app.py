"""Service orchestration: card events, download, delivery, window lifecycle."""

import logging
import re
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

from tacho_core import (CardError, DDDParser, DownloadProgress, DriverReport,
                        NotATachographCard, TachoReader, TripRecord, build_report)

from . import cardwatch
from .config import Config, OUTBOX_PATH
from .dispatch import Dispatcher
from .outbox import DELIVERED, FAILED, HELD, PENDING, SENDING, Outbox
from .ui import CardWindow, SettingsDialog, Tray

log = logging.getLogger(__name__)

LOCK_ADDR = "\0tacho-service"       # abstract socket: released by the kernel


class SingleInstance:
    """Two copies would fight over the reader and double-send every card."""

    def __init__(self):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)

    def acquire(self) -> bool:
        try:
            self._sock.bind(LOCK_ADDR)
            return True
        except OSError:
            return False


class Session:
    """One card insertion."""

    def __init__(self):
        self.report: Optional[DriverReport] = None
        self.batch_id: Optional[str] = None
        self.card_present = True
        self.delivered = False
        self.failed = False
        self.held = False
        self.errored = False
        # Auto-close arms when the card is pulled, and disarms the moment a
        # delivery attempt fails -- otherwise a retry succeeding 40 minutes
        # later would yank the window off screen.
        self.close_armed = False


class Service(QObject):
    _progress = pyqtSignal(object)
    _finished = pyqtSignal(object, object)      # report, ddd_path
    _failed = pyqtSignal(str)
    _removed = pyqtSignal()
    _delivery_changed = pyqtSignal()
    _sql_health = pyqtSignal(object, str)

    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.config = Config.load()
        self.outbox = Outbox(OUTBOX_PATH)
        self.session: Optional[Session] = None

        colour = app.palette().windowText().color()
        self.tray = Tray(colour)
        self.window = CardWindow()

        self.dispatcher = Dispatcher(self.outbox, lambda: self.config,
                                     on_change=self._delivery_changed.emit,
                                     on_health=lambda ok, detail:
                                     self._sql_health.emit(ok, detail))
        self.watcher = cardwatch.CardWatcher(self._on_insert, self._on_remove)

        self._wire()
        self._refresh_footer()

    # ------------------------------------------------------------------- wire

    def _wire(self):
        self._progress.connect(self._show_progress)
        self._finished.connect(self._on_report)
        self._failed.connect(self._on_error)
        self._removed.connect(self._on_card_gone)
        self._delivery_changed.connect(self._sync_delivery_state)
        self._sql_health.connect(self._on_sql_health)

        self.tray.act_show.triggered.connect(self._show_window)
        self.tray.act_settings.triggered.connect(self._open_settings)
        self.tray.act_send.triggered.connect(self._flush)
        self.tray.act_quit.triggered.connect(self._quit)
        self.tray.act_auto.setChecked(self.config.auto_sync)
        self.tray.act_auto.toggled.connect(self._toggle_auto)
        self.tray.activated.connect(self._tray_activated)

        self.window.settings_requested.connect(self._open_settings)
        self.window.send_requested.connect(self._flush)

    def start(self):
        self.tray.show()
        self.dispatcher.start()
        self.watcher.start()
        if not cardwatch.reader_present():
            self.window.set_reader_state(False)
            self.tray.set_state("error", "Tacho — no card reader")
        log.info("service started")

    # ---------------------------------------------------- card worker thread

    def _on_insert(self, card):
        """Runs on a worker thread -- all UI goes through signals."""
        self.session = Session()
        try:
            conn = cardwatch.connect(card)
        except Exception as e:
            self._failed.emit(f"Could not connect to card: {e}")
            return

        try:
            reader = TachoReader(conn)
            ddd = reader.download(progress=self._progress.emit)
            path = self._save(ddd, reader.driver_name)
            parser = DDDParser(ddd).parse()
            report = build_report(parser, window_days=self.config.send_window_days)
            self._finished.emit(report, path)
        except NotATachographCard:
            # A bank or ID card in the same reader is a silent no-op.
            log.info("non-tachograph card ignored")
            self.session = None
        except CardError as e:
            self._failed.emit(str(e))
        except Exception as e:
            self._failed.emit(f"{type(e).__name__}: {e}")
        finally:
            try:
                conn.disconnect()
            except Exception:
                pass

    def _on_remove(self):
        self._removed.emit()

    def _save(self, ddd: bytes, driver: Optional[str]) -> Optional[str]:
        try:
            d = self.config.downloads
            d.mkdir(parents=True, exist_ok=True)
            name = re.sub(r"[^a-z0-9]+", "_",
                          (driver or "driver").lower()).strip("_") or "driver"
            path = d / f"{name}_{datetime.now():%Y-%m-%d_%H%M%S}.ddd"
            path.write_bytes(ddd)
            self._prune(d)
            return str(path)
        except OSError as e:
            # Not fatal: the payload is already built, so still enqueue.
            log.warning("could not save .ddd: %s", e)
            return None

    def _prune(self, d: Path):
        files = sorted(d.glob("*.ddd"), key=lambda p: p.stat().st_mtime, reverse=True)
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=self.config.download_retention_days)
        for old in files:
            if datetime.fromtimestamp(old.stat().st_mtime, timezone.utc) >= cutoff:
                continue
            try:
                old.unlink()
            except OSError:
                pass

    # ----------------------------------------------------------- UI (main thread)

    def _show_progress(self, p: DownloadProgress):
        if not self.window.isVisible():
            self._show_window()
        self.tray.set_state("reading", "Tacho — reading card")
        self.window.show_reading(p.driver_name)
        self.window.set_progress(p.bytes_done, p.bytes_total, p.stage)

    def _on_report(self, report: DriverReport, ddd_path: Optional[str]):
        s = self.session
        if s is None:
            return
        s.report = report

        self.window.show_report(report, self.config.preview_trips,
                                self.config.send_window_days)
        self._show_window()

        dests = self.config.enabled_destinations()
        if not dests:
            self.window.set_delivery("● Saved — no destinations configured", None)
            self.tray.set_state("idle")
            return

        s.batch_id = self.outbox.enqueue(
            report.to_payload(), dests,
            ddd_path=ddd_path, driver_name=report.driver_name,
            trips=report.total_trips, auto_sync=self.config.auto_sync,
            retry_limit_hours=self.config.retry_limit_hours)

        if self.config.auto_sync:
            s.held = False
            self.window.set_delivery("● Sending…", None)
            self.dispatcher.poke()
        else:
            s.held = True
            self.window.set_delivery("● Held — auto-sync is off", "#e67e22")
            self.tray.set_state("pending", "Tacho — deliveries held")
        self._refresh_footer()

    def _on_error(self, message: str):
        if self.session:
            self.session.errored = True
        self._show_window()
        self.window.show_error(message)
        self.tray.set_state("error", f"Tacho — {message[:60]}")

    def _on_card_gone(self):
        s = self.session
        self.tray.set_state("idle")
        if s is None:
            return
        s.card_present = False

        # Delivered already: close now. Still sending: arm and close on success.
        # Everything else (held, failed, error) keeps the window up.
        if s.delivered:
            self._hide_window()
        elif s.held or s.failed or s.errored:
            pass
        else:
            s.close_armed = True

    def _sync_delivery_state(self):
        """Delivery outcomes arrived; reconcile window, tray and notifications."""
        s = self.session
        counts = self.outbox.counts()
        pending = counts.get(PENDING, 0) + counts.get(SENDING, 0)
        held = counts.get(HELD, 0)
        self._refresh_footer(pending + held)

        if s is None or not s.batch_id:
            self.tray.set_state("pending" if pending or held else "idle")
            return

        rows = self.outbox.batch(s.batch_id)
        if not rows:
            return
        states = [r["state"] for r in rows]

        if any(st == FAILED for st in states):
            failed = [r for r in rows if r["state"] == FAILED]
            s.failed = True
            s.close_armed = False          # disarm: don't vanish on a later retry
            names = ", ".join(r["destination_name"] or r["destination_id"]
                              for r in failed)
            err = failed[0]["last_error"] or "delivery failed"
            self.window.set_delivery(f"● Failed — {names}: {err}", "#e74c3c")
            self.tray.set_state("error", f"Tacho — {names} failed")
            if self.config.notify_on_failure:
                self.tray.notify("Tacho delivery failed", f"{names}: {err}",
                                 error=True)
            self._show_window()
            return

        if all(st == DELIVERED for st in states):
            s.delivered = True
            self.window.set_delivery("● Sent", "#2ecc71")
            self.tray.set_state("idle")
            if self.config.notify_on_success:
                self.tray.notify("Tacho", f"{s.report.driver_name} — sent to "
                                          f"{len(rows)} destination(s)")
            if s.close_armed or not s.card_present:
                self._hide_window()
            return

        if any(st in (PENDING, SENDING) for st in states):
            done = sum(1 for st in states if st == DELIVERED)
            self.window.set_delivery(f"● Sending… ({done}/{len(rows)})", None)
            self.tray.set_state("reading" if s.card_present else "pending")

    def _on_sql_health(self, online, detail: str):
        """Runs in the Qt thread after the dispatch worker's real DB probe."""
        self.tray.set_sql_status(online, f"Tacho — {detail}")
        self.window.set_sql_status(online, detail)

    def _refresh_footer(self, queued: int = 0):
        self.window.set_sync_state(self.config.auto_sync,
                                   len(self.config.enabled_destinations()),
                                   queued)

    # ----------------------------------------------------------------- actions

    def _show_window(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _hide_window(self):
        self.window.hide()

    def _tray_activated(self, reason):
        if reason == self.tray.ActivationReason.Trigger:
            if self.window.isVisible():
                self.window.hide()
            else:
                self._show_window()

    def _toggle_auto(self, on: bool):
        self.config.auto_sync = on
        self.config.save()
        if on:
            released = self.outbox.release_held()
            if released:
                log.info("released %s held deliveries", released)
            self.dispatcher.poke()
            if self.session:
                self.session.held = False
        self._refresh_footer()

    def _flush(self):
        released = self.outbox.release_held()
        self.dispatcher.poke()
        if self.session:
            self.session.held = False
        self.window.set_delivery("● Sending…", None)
        log.info("manual flush: released %s", released)

    def _open_settings(self):
        dlg = SettingsDialog(self.config, self.window
                             if self.window.isVisible() else None)
        if dlg.exec():
            self.config = Config.load()
            self.tray.act_auto.setChecked(self.config.auto_sync)
            self._refresh_footer()
            self.dispatcher.poke()

    def _quit(self):
        counts = self.outbox.counts()
        pending = counts.get(PENDING, 0) + counts.get(SENDING, 0)
        if pending:
            reply = QMessageBox.question(
                None, "Quit Tacho",
                f"{pending} delivery(s) are still queued.\n\n"
                "They will resume when the service next starts. Quit anyway?")
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.watcher.stop()
        self.dispatcher.stop()
        self.outbox.close()
        self.app.quit()


def main(argv=None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    args = list(argv or sys.argv)
    preview = "--preview" in args
    if preview:
        args.remove("--preview")

    app = QApplication(args)
    app.setApplicationName("Tacho")
    app.setQuitOnLastWindowClosed(False)     # tray outlives the window

    lock = SingleInstance()
    if not lock.acquire():
        print("tacho-service is already running", file=sys.stderr)
        return 1

    from PyQt6.QtWidgets import QSystemTrayIcon
    if not QSystemTrayIcon.isSystemTrayAvailable() and not preview:
        print("No system tray available in this session", file=sys.stderr)
        return 1

    if preview:
        _start_preview(app)
    else:
        service = Service(app)
        QTimer.singleShot(0, service.start)
    return app.exec()


def _start_preview(app: QApplication):
    """Open a visual preview without PC/SC, config, or network activity."""
    colour = app.palette().windowText().color()
    tray = Tray(colour)
    window = CardWindow()

    report = DriverReport(
        driver_name="Sample Driver",
        card_number="GB 1234 5678 9012 3456",
        country="GB",
        card_expiry="2029-04-11",
        download_timestamp=datetime.now().astimezone().isoformat(),
        report_period_days=7,
        trips=[
            TripRecord("2026-08-03", "Sunday", "AB12 CDE", "06:42", "15:18",
                       61_552, 61_964, 412, 8.6),
            TripRecord("2026-08-02", "Saturday", "AB12 CDE", "07:10", "13:52",
                       61_214, 61_552, 338, 6.7),
            TripRecord("2026-08-01", "Friday", "XY98 ZWQ", "05:55", "14:16",
                       60_713, 61_214, 501, 8.4),
        ],
        total_distance_km=1_251,
        total_trips=3,
    )
    window.show_report(report, preview=7, window_days=7)
    window.set_delivery("● Preview — no card or data sent", "#2ecc71")
    window.set_sync_state(True, destinations=1)
    window.send_btn.setEnabled(False)
    window.send_btn.setToolTip("Preview mode never sends data")

    tray.set_state("idle", "Tacho — UI preview (no card read)")
    tray.act_auto.setChecked(True)
    tray.act_auto.setEnabled(False)
    tray.act_send.setEnabled(False)
    tray.act_settings.setEnabled(False)
    tray.act_show.triggered.connect(window.showNormal)
    tray.act_quit.triggered.connect(app.quit)
    tray.activated.connect(lambda _reason: window.showNormal())
    tray.show()
    # Keep Python references alive for the lifetime of the preview application.
    app._tacho_preview = (tray, window)
    window.show()
    window.raise_()
    window.activateWindow()
