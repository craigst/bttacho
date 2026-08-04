"""Read a .ddd back into driver identity and vehicle records."""

from pathlib import Path
from typing import List, Optional

from .codec import decode_ascii, decode_odometer, decode_text, decode_timestamp
from .countries import COUNTRIES
from .ddd import read_files
from .reader import EF_IDENTIFICATION, EF_VEHICLES_USED

VEHICLE_RECORD_LEN = 31


class DDDParser:
    """Canonical form takes bytes; use from_file() for a path on disk."""

    def __init__(self, ddd_data: bytes):
        self.raw = bytes(ddd_data)
        self.data = {}
        self.driver_name: Optional[str] = None
        self.card_number: Optional[str] = None
        self.card_expiry = None
        self.issuing_country: Optional[str] = None
        self.vehicles: List[dict] = []

    @classmethod
    def from_file(cls, path) -> "DDDParser":
        return cls(Path(path).read_bytes())

    def parse(self) -> "DDDParser":
        self.data = read_files(self.raw)
        self._parse_identification()
        self._parse_vehicles()
        return self

    def _parse_identification(self):
        data = self.data.get(EF_IDENTIFICATION)
        if not data or len(data) < 143:
            return
        self.issuing_country = COUNTRIES.get(data[0], "??")
        self.card_number = decode_ascii(data[1:17])
        self.card_expiry = decode_timestamp(data[61:65])
        surname = decode_text(data[65:101])
        firstname = decode_text(data[101:137])
        self.driver_name = f"{surname} {firstname}".strip()

    def _parse_vehicles(self):
        """EF_Vehicles_Used: 2-byte newest-record pointer, then 31-byte records.

        Record layout:
            0:3   odometer at first use (3-byte BE km)
            3:6   odometer at last use
            6:10  first use timestamp
            10:14 last use timestamp
            15:29 vehicle registration (codepage-prefixed)
        """
        data = self.data.get(EF_VEHICLES_USED)
        if not data or len(data) < 4:
            return

        pos = 2  # skip the newest-record pointer
        while pos + VEHICLE_RECORD_LEN <= len(data):
            rec = data[pos:pos + VEHICLE_RECORD_LEN]
            pos += VEHICLE_RECORD_LEN

            first_use = decode_timestamp(rec[6:10])
            registration = decode_text(rec[15:29])

            # Empty slots in the ring read back as zeroed records.
            if not first_use or not registration:
                continue

            begin = decode_odometer(rec[0:3])
            end = decode_odometer(rec[3:6])
            self.vehicles.append({
                "registration": registration,
                "first_use": first_use,
                "last_use": decode_timestamp(rec[10:14]),
                "odometer_begin": begin,
                "odometer_end": end,
                "distance": end - begin,
            })
