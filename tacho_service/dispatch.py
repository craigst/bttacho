"""Delivery worker -- drains the outbox over HTTP."""

import gzip
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

from .config import Config, Destination
from .outbox import Outbox

log = logging.getLogger(__name__)

# Retryable: the endpoint may yet accept this. Everything else 4xx is a request
# the endpoint will never accept, so burning 24h of retries on it is pointless.
RETRYABLE_STATUS = {408, 429}

POLL_SECONDS = 5
HEALTH_CHECK_SECONDS = 30


class Dispatcher(threading.Thread):
    def __init__(self, outbox: Outbox, config_fn: Callable[[], Config],
                 on_change: Optional[Callable[[], None]] = None,
                 on_health: Optional[Callable[[Optional[bool], str], None]] = None):
        super().__init__(daemon=True, name="dispatch")
        self.outbox = outbox
        self._config = config_fn
        self._on_change = on_change
        self._on_health = on_health
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._last_health_check = 0.0

    def stop(self):
        self._stop.set()
        self._wake.set()

    def poke(self):
        """Something was enqueued or released -- look now rather than in 5s."""
        self._wake.set()

    def run(self):
        while not self._stop.is_set():
            try:
                self._maybe_check_health()
                self._drain()
            except Exception:
                log.exception("dispatch loop error")
            self._wake.wait(POLL_SECONDS)
            self._wake.clear()

    def _maybe_check_health(self):
        """Probe configured PostgreSQL in the delivery thread, never the UI."""
        if not self._on_health or time.monotonic() - self._last_health_check < HEALTH_CHECK_SECONDS:
            return
        self._last_health_check = time.monotonic()
        targets = [d for d in self._config().enabled_destinations()
                   if d.type == "postgres"]
        if not targets:
            self._on_health(None, "SQL not configured")
            return
        ok, error = postgres_health(targets[0])
        self._on_health(ok, "SQL online" if ok else f"SQL offline — {error}")

    def _drain(self):
        cfg = self._config()
        rows = self.outbox.due()
        if not rows:
            return

        by_id = {d.id: d for d in cfg.destinations}
        for row in rows:
            if self._stop.is_set():
                return
            dest = by_id.get(row["destination_id"])
            if dest is None:
                self.outbox.mark_failed(row["id"], "destination no longer configured")
                continue
            self._attempt(row, dest, cfg)

        if self._on_change:
            self._on_change()

    def _attempt(self, row, dest: Destination, cfg: Config):
        self.outbox.mark_sending(row["id"])
        attempts = row["attempts"] + 1

        ok, status, error = deliver(dest, row["payload"])

        if ok:
            log.info("delivered #%s to %s (%s)", row["id"], dest.name, status)
            self.outbox.mark_delivered(row["id"], status)
            return

        permanent = (status is not None
                     and 400 <= status < 500
                     and status not in RETRYABLE_STATUS)
        if permanent:
            log.warning("permanent failure #%s -> %s: %s", row["id"], dest.name, error)
            self.outbox.mark_failed(row["id"], error, status)
            return

        still_trying = self.outbox.reschedule(
            row["id"], attempts, error, status, cfg.backoff_ceiling_seconds)
        log.warning("attempt %s failed #%s -> %s: %s%s",
                    attempts, row["id"], dest.name, error,
                    "" if still_trying else " (deadline passed, gave up)")


def deliver(dest: Destination, body: bytes):
    """Deliver a frozen payload through its configured transport."""
    if dest.type == "postgres":
        return upsert_postgres(dest, body)
    return post(dest, body)


def _trip_uid(payload: dict, trip: dict) -> str:
    """Match the stable identifier used by n8n's live `tacho ingest` workflow."""
    def value(v):
        return "" if v is None else str(v).strip()
    return "|".join(value(v) for v in (
        payload.get("card_number"), trip.get("date"),
        trip.get("vehicle_registration"), trip.get("card_in_time"),
        trip.get("card_out_time"), trip.get("start_mileage"),
        trip.get("end_mileage"),
    ))


def postgres_rows(payload: dict):
    """Map the fixed report wire format onto `public.tacho_daily` fields."""
    for trip in payload.get("trips", []):
        yield (
            _trip_uid(payload, trip), trip.get("date"), trip.get("day_of_week"),
            trip.get("vehicle_registration"), trip.get("card_in_time"),
            trip.get("card_out_time"), _time_diff(trip.get("card_in_time"), trip.get("card_out_time")),
            _hours_hhmm(trip.get("driving_hours")), trip.get("start_mileage"),
            trip.get("end_mileage"), trip.get("distance_km"), payload.get("driver_name"),
            payload.get("card_number"), payload.get("download_timestamp"),
            payload.get("country"), payload.get("card_expiry"),
        )


def _hours_hhmm(hours):
    if hours in (None, ""):
        return ""
    minutes = round(float(hours) * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _time_diff(start, end):
    try:
        sh, sm = (int(v) for v in str(start).split(":"))
        eh, em = (int(v) for v in str(end).split(":"))
    except (TypeError, ValueError):
        return ""
    minutes = (eh * 60 + em) - (sh * 60 + sm)
    if minutes < 0:
        minutes += 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def upsert_postgres(dest: Destination, body: bytes):
    """Transactionally upsert all trips.  A commit is the success signal."""
    try:
        import psycopg
    except ImportError:
        return False, 400, "PostgreSQL support missing: install python-psycopg"

    try:
        payload = json.loads(body)
        rows = list(postgres_rows(payload))
        if not rows:
            return True, 204, None
        conninfo = (f"host={dest.host} port={dest.port} dbname={dest.database} "
                    f"user={dest.username} password={dest.password} sslmode={dest.sslmode} "
                    f"connect_timeout={dest.timeout_seconds}")
        query = """
            INSERT INTO public.tacho_daily (
              trip_uid, trip_date, day_of_week, vehicle_registration,
              card_in_time, card_out_time, shift_duration, driving_hours,
              start_mileage, end_mileage, distance_km, driver_name, card_number,
              download_timestamp, country, card_expiry
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            ) ON CONFLICT (trip_uid) DO UPDATE SET
              trip_date = EXCLUDED.trip_date,
              day_of_week = EXCLUDED.day_of_week,
              vehicle_registration = EXCLUDED.vehicle_registration,
              card_in_time = EXCLUDED.card_in_time,
              card_out_time = EXCLUDED.card_out_time,
              shift_duration = EXCLUDED.shift_duration,
              driving_hours = EXCLUDED.driving_hours,
              start_mileage = EXCLUDED.start_mileage,
              end_mileage = EXCLUDED.end_mileage,
              distance_km = EXCLUDED.distance_km,
              driver_name = EXCLUDED.driver_name,
              card_number = EXCLUDED.card_number,
              download_timestamp = EXCLUDED.download_timestamp,
              country = EXCLUDED.country,
              card_expiry = EXCLUDED.card_expiry,
              updated_at = now()
        """
        with psycopg.connect(conninfo) as conn:
            with conn.cursor() as cur:
                cur.executemany(query, rows)
        return True, 200, None
    except psycopg.OperationalError as e:
        return False, None, f"PostgreSQL connection failed: {e}"
    except (psycopg.Error, ValueError, json.JSONDecodeError) as e:
        return False, 400, f"PostgreSQL rejected payload: {e}"


def postgres_health(dest: Destination):
    """Authenticated, cheap connectivity check used for the green tray state."""
    try:
        import psycopg
    except ImportError:
        return False, "PostgreSQL driver missing"
    try:
        conninfo = (f"host={dest.host} port={dest.port} dbname={dest.database} "
                    f"user={dest.username} password={dest.password} sslmode={dest.sslmode} "
                    f"connect_timeout={dest.timeout_seconds}")
        with psycopg.connect(conninfo) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                if cur.fetchone() != (1,):
                    return False, "health query returned no result"
        return True, ""
    except psycopg.Error as e:
        return False, str(e).splitlines()[0]


def post(dest: Destination, body: bytes):
    """POST body to dest. Returns (ok, status, error)."""
    req = urllib.request.Request(
        dest.url,
        data=body,
        headers=dest.resolved_headers(),
        method=dest.method or "POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=dest.timeout_seconds) as resp:
            # Some receivers gzip the response; read it so the socket closes clean.
            raw = resp.read()
            if resp.info().get("Content-Encoding") == "gzip":
                try:
                    gzip.decompress(raw)
                except OSError:
                    pass
            return True, resp.status, None
    except urllib.error.HTTPError as e:
        return False, e.code, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return False, None, f"connection failed: {e.reason}"
    except Exception as e:                      # timeouts, DNS, TLS
        return False, None, f"{type(e).__name__}: {e}"
