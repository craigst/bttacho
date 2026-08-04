"""Report data model shared by every frontend."""

from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class TripRecord:
    date: str
    day_of_week: str
    vehicle_registration: str
    card_in_time: str
    card_out_time: str
    start_mileage: int
    end_mileage: int
    distance_km: int
    driving_hours: float


@dataclass
class DriverReport:
    driver_name: str
    card_number: str
    country: str
    card_expiry: str
    download_timestamp: str
    report_period_days: int
    trips: List[TripRecord] = field(default_factory=list)
    total_distance_km: int = 0
    total_trips: int = 0

    def to_payload(self) -> dict:
        """Flattened JSON body posted to every destination.

        Wire format is frozen: existing n8n workflows consume these exact keys.
        """
        return {
            "driver_name": self.driver_name,
            "card_number": self.card_number,
            "country": self.country,
            "card_expiry": self.card_expiry,
            "download_timestamp": self.download_timestamp,
            "report_period_days": self.report_period_days,
            "total_distance_km": self.total_distance_km,
            "total_trips": self.total_trips,
            "trips": [asdict(t) for t in self.trips],
        }

    def recent(self, n: int) -> List[TripRecord]:
        """Most recent n trips. Trips are already sorted newest-first."""
        return self.trips[:n]


@dataclass
class DownloadProgress:
    """Byte-level progress.

    A full card is ~21KB over ~110 READ BINARY round trips, and two EFs
    (activity + vehicles) are ~95% of it -- so a per-file step counter appears
    frozen for most of the download. Bytes are the honest signal.
    """
    stage: str
    bytes_done: int = 0
    bytes_total: Optional[int] = None   # unknown until EF_Application_Identification
    driver_name: Optional[str] = None

    @property
    def fraction(self) -> Optional[float]:
        if not self.bytes_total:
            return None
        return min(1.0, self.bytes_done / self.bytes_total)
