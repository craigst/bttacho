"""Turn parsed card data into a DriverReport."""

from datetime import datetime, timedelta, timezone
from typing import Optional

from .models import DriverReport, TripRecord

KM_TO_MILES = 0.621371


def mileage_gap_km(trips, trip) -> int | None:
    """Return miles driven since this truck's previous card record.

    A positive gap means the odometer advanced between this card's previous
    record and this record, so the distance was not attributed to this card.
    ``None`` means the previous record is not available in ``trips``.
    """
    try:
        index = next(i for i, candidate in enumerate(trips) if candidate is trip)
    except StopIteration:
        return None
    for older in trips[index + 1:]:
        if older.vehicle_registration != trip.vehicle_registration:
            continue
        return max(0, trip.start_mileage - older.end_mileage)
    return None


def total_unaccounted_km(trips) -> int:
    return sum(mileage_gap_km(trips, trip) or 0 for trip in trips)


def build_report(parser, window_days: Optional[int] = 14) -> DriverReport:
    """Build a report over the trailing `window_days`.

    window_days=None means every record on the card.
    """
    cutoff = None
    if window_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    trips = []
    for v in parser.vehicles:
        first_use, last_use = v["first_use"], v["last_use"]
        if not first_use or not v["registration"]:
            continue
        if cutoff and first_use < cutoff:
            continue

        if first_use and last_use:
            driving_hours = round((last_use - first_use).total_seconds() / 3600, 2)
        else:
            driving_hours = 0.0

        trips.append(TripRecord(
            date=first_use.strftime("%Y-%m-%d"),
            day_of_week=first_use.strftime("%A"),
            vehicle_registration=v["registration"],
            card_in_time=first_use.strftime("%H:%M"),
            card_out_time=last_use.strftime("%H:%M") if last_use else "--:--",
            start_mileage=v["odometer_begin"],
            end_mileage=v["odometer_end"],
            distance_km=v["distance"],
            driving_hours=driving_hours,
        ))

    trips.sort(key=lambda t: t.date, reverse=True)

    return DriverReport(
        driver_name=parser.driver_name or "Unknown",
        card_number=parser.card_number or "Unknown",
        country=parser.issuing_country or "??",
        card_expiry=(parser.card_expiry.strftime("%Y-%m-%d")
                     if parser.card_expiry else "Unknown"),
        download_timestamp=datetime.now(timezone.utc).isoformat(),
        report_period_days=_observed_span(trips, window_days),
        trips=trips,
        total_distance_km=sum(t.distance_km for t in trips),
        total_trips=len(trips),
    )


def _observed_span(trips, window_days) -> int:
    """Span actually covered by the returned trips, not the requested window.

    Preserved deliberately: downstream n8n workflows already read the field
    this way.
    """
    if not trips:
        return window_days or 0
    oldest = min(t.date for t in trips)
    newest = max(t.date for t in trips)
    return (datetime.strptime(newest, "%Y-%m-%d")
            - datetime.strptime(oldest, "%Y-%m-%d")).days + 1
