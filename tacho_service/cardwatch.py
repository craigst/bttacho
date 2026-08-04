"""Event-driven card detection.

pyscard's CardMonitor sits on SCardGetStatusChange, so there is no polling and
reader hotplug is handled for free.

The observer callback runs on pyscard's own thread and does no card I/O: a full
download is ~110 APDU round trips and blocking here would delay detection of the
next event -- including the removal of the card being read.
"""

import logging
import threading
import time
from typing import Callable, Optional

from smartcard.CardMonitoring import CardMonitor, CardObserver
from smartcard.CardType import AnyCardType
from smartcard.Exceptions import CardConnectionException, NoCardException

log = logging.getLogger(__name__)

# Some readers emit a spurious remove/insert pair as the card seats. Without a
# debounce that is two downloads and two sends.
DEBOUNCE_SECONDS = 0.25


class CardWatcher(CardObserver):
    """Calls on_insert(card) / on_remove() from a worker thread."""

    def __init__(self, on_insert: Callable, on_remove: Callable):
        self._on_insert = on_insert
        self._on_remove = on_remove
        self._monitor: Optional[CardMonitor] = None
        self._worker: Optional[threading.Thread] = None
        self._present = False
        self._lock = threading.Lock()

    def start(self):
        self._monitor = CardMonitor()
        self._monitor.addObserver(self)
        self._detect_already_inserted()
        log.info("card monitor started")

    def stop(self):
        if self._monitor:
            try:
                self._monitor.deleteObserver(self)
            except Exception:
                pass
            self._monitor = None

    def _detect_already_inserted(self):
        """Treat a card present at service startup as an insertion.

        ``CardMonitor`` reports changes after it starts.  Without this probe a
        card inserted while the service is stopped is invisible until it is
        removed and inserted again, which is surprising for a tray service.
        """
        try:
            from smartcard.Card import Card
            from smartcard.System import readers
            for reader in readers():
                conn = reader.createConnection()
                try:
                    conn.connect()
                    atr = conn.getATR()
                except (NoCardException, CardConnectionException):
                    continue
                finally:
                    try:
                        conn.disconnect()
                    except Exception:
                        pass
                log.info("card already present in %s", reader)
                self.update(self._monitor, ([Card(reader, atr)], []))
                return                  # one active session at a time
        except Exception:
            log.exception("initial card detection failed")

    # -------------------------------------------------- pyscard observer thread

    def update(self, observable, actions):
        added, removed = actions

        if removed:
            with self._lock:
                if self._present:
                    self._present = False
                    log.info("card removed")
                    self._spawn(self._on_remove)

        if added:
            card = added[0]
            with self._lock:
                if self._present:
                    return
                self._present = True
            log.info("card inserted in %s", getattr(card, "reader", "?"))
            self._spawn(self._handle_insert, card)

    def _spawn(self, fn, *args):
        """Hand off immediately; never do work on the observer thread."""
        t = threading.Thread(target=_guard(fn), args=args, daemon=True)
        t.start()
        self._worker = t

    def _handle_insert(self, card):
        time.sleep(DEBOUNCE_SECONDS)
        with self._lock:
            if not self._present:      # pulled again during the debounce
                return
        self._on_insert(card)


def _guard(fn):
    def wrapped(*args):
        try:
            fn(*args)
        except Exception:
            log.exception("card handler failed")
    return wrapped


def connect(card, retries: int = 3):
    """Open a connection to an inserted card, tolerating a slow seat."""
    last = None
    for _ in range(retries):
        try:
            conn = card.createConnection()
            conn.connect()
            return conn
        except (NoCardException, CardConnectionException) as e:
            last = e
            time.sleep(0.2)
    raise last if last else CardConnectionException("could not connect")


def reader_present() -> bool:
    try:
        from smartcard.System import readers
        return bool(readers())
    except Exception:
        return False
