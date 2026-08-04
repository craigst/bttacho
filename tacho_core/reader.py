"""Driver card reader -- the APDU sequence that produces a .ddd."""

from typing import Callable, Optional

from .codec import decode_ascii, decode_text, decode_timestamp
from .countries import COUNTRIES
from .ddd import pack_block
from .models import DownloadProgress

# SELECT by name: "\xffTACHO" -- the Tachograph dedicated file
TACHOGRAPH_DF = [0xFF, 0x54, 0x41, 0x43, 0x48, 0x4F]

# READ BINARY caps out well below 256; 200 matches what the cards tolerate.
CHUNK = 200

SW_OK = 0x9000

# EF identifiers
EF_ICC = 0x0002
EF_IC = 0x0005
EF_APPLICATION_IDENTIFICATION = 0x0501
EF_EVENTS_DATA = 0x0502
EF_FAULTS_DATA = 0x0503
EF_DRIVER_ACTIVITY_DATA = 0x0504
EF_VEHICLES_USED = 0x0505
EF_PLACES = 0x0506
EF_CURRENT_USAGE = 0x0507
EF_CONTROL_ACTIVITY_DATA = 0x0508
EF_CARD_DOWNLOAD = 0x050E
EF_IDENTIFICATION = 0x0520
EF_DRIVING_LICENCE_INFO = 0x0521
EF_SPECIFIC_CONDITIONS = 0x0522
EF_CARD_CERTIFICATE = 0xC100
EF_CA_CERTIFICATE = 0xC108


class CardError(Exception):
    """A required file could not be read."""


class NotATachographCard(CardError):
    """The card is readable but has no Tachograph application."""


ProgressFn = Callable[[DownloadProgress], None]


class TachoReader:
    def __init__(self, connection):
        self.conn = connection
        self.ddd = bytearray()
        self.driver_name: Optional[str] = None
        self.driver_surname: Optional[str] = None
        self.driver_firstname: Optional[str] = None
        self.card_number: Optional[str] = None
        self.card_expiry = None
        self.issuing_country: Optional[str] = None
        self.params: dict = {}
        self._bytes_done = 0
        self._bytes_total: Optional[int] = None
        self._progress: Optional[ProgressFn] = None

    # ------------------------------------------------------------------ APDUs

    def select(self, file_id: int, by_name: bool = False) -> int:
        if by_name:
            apdu = [0x00, 0xA4, 0x04, 0x0C, len(TACHOGRAPH_DF)] + TACHOGRAPH_DF
        else:
            apdu = [0x00, 0xA4, 0x02, 0x0C, 0x02,
                    (file_id >> 8) & 0xFF, file_id & 0xFF]
        _, sw1, sw2 = self.conn.transmit(apdu)
        return (sw1 << 8) | sw2

    def read_binary(self, size: int) -> Optional[bytes]:
        """Read `size` bytes, reporting progress as each chunk lands."""
        out = bytearray()
        pos = 0
        while pos < size:
            n = min(CHUNK, size - pos)
            data, sw1, _ = self.conn.transmit(
                [0x00, 0xB0, (pos >> 8) & 0xFF, pos & 0xFF, n])
            if sw1 != 0x90:
                return None
            if not data:
                break
            out.extend(data)
            pos += len(data)
            self._bytes_done += len(data)
            self._emit()
        return bytes(out)

    def perform_hash(self) -> bool:
        _, sw1, _ = self.conn.transmit([0x80, 0x2A, 0x90, 0x00])
        return sw1 == 0x90

    def compute_signature(self) -> Optional[bytes]:
        data, sw1, _ = self.conn.transmit([0x00, 0x2A, 0x9E, 0x9A, 0x80])
        return bytes(data) if sw1 == 0x90 else None

    # ------------------------------------------------------------- collection

    def _emit(self, stage: Optional[str] = None):
        if self._progress:
            self._progress(DownloadProgress(
                stage=stage or self._stage,
                bytes_done=self._bytes_done,
                bytes_total=self._bytes_total,
                driver_name=self.driver_name,
            ))

    def read_file(self, fid: int, size: int, sign: bool = False,
                  store: bool = True) -> Optional[bytes]:
        if self.select(fid) != SW_OK:
            return None
        if sign:
            self.perform_hash()
        data = self.read_binary(size)
        if data is None:
            return None
        if store:
            self.ddd.extend(pack_block(fid, data))
        if sign:
            sig = self.compute_signature()
            if sig:
                self.ddd.extend(pack_block(fid, sig, is_signature=True))
        return data

    # ---------------------------------------------------------------- download

    def download(self, progress: Optional[ProgressFn] = None) -> bytes:
        """Run the full read sequence and return the .ddd bytes."""
        self.ddd = bytearray()
        self._bytes_done = 0
        self._bytes_total = None
        self._progress = progress
        self._stage = "Connecting"
        self._emit()

        # -- Root files, before the Tachograph application is selected --------
        self._stage = "Reading card identity"
        if self.read_file(EF_ICC, 25) is None:
            raise CardError("Failed to read EF_ICC")
        if self.read_file(EF_IC, 8) is None:
            raise CardError("Failed to read EF_IC")

        if self.select(0, by_name=True) != SW_OK:
            raise NotATachographCard("No Tachograph application on this card")

        # -- Record counts drive every subsequent read size ------------------
        data = self.read_file(EF_APPLICATION_IDENTIFICATION, 10, sign=True)
        if data is None or len(data) < 10:
            raise CardError("Failed to read EF_Application_Identification")

        self.params = {
            "events": data[3],
            "faults": data[4],
            "activity": (data[5] << 8) | data[6],
            "vehicles": (data[7] << 8) | data[8],
            "places": data[9],
        }

        # Sizes are only knowable now, so the total arrives mid-download.
        sized = self._plan()
        self._bytes_total = self._bytes_done + sum(s for _, s, _ in sized)
        self._emit()

        for fid, size, sign in sized:
            self._stage = _STAGE_NAMES.get(fid, "Reading card")
            self._emit()
            self.read_file(fid, size, sign=sign)
            if fid == EF_IDENTIFICATION:
                self._read_identity()

        self._stage = "Download complete"
        self._emit()
        return bytes(self.ddd)

    def _plan(self):
        """(file_id, size, signed) for everything after the app identification."""
        p = self.params
        return [
            (EF_CARD_CERTIFICATE, 194, False),
            (EF_CA_CERTIFICATE, 194, False),
            (EF_IDENTIFICATION, 143, True),
            (EF_CARD_DOWNLOAD, 4, False),
            (EF_DRIVING_LICENCE_INFO, 53, True),
            (EF_EVENTS_DATA, p["events"] * 24 * 6, True),
            (EF_FAULTS_DATA, p["faults"] * 24 * 2, True),
            (EF_DRIVER_ACTIVITY_DATA, p["activity"] + 4, True),
            (EF_VEHICLES_USED, p["vehicles"] * 31 + 2, True),
            (EF_PLACES, p["places"] * 10 + 1, True),
            (EF_CURRENT_USAGE, 19, True),
            (EF_CONTROL_ACTIVITY_DATA, 46, True),
            (EF_SPECIFIC_CONDITIONS, 280, True),
        ]

    def _read_identity(self):
        """Pull driver identity out of the EF_Identification block just stored."""
        from .ddd import read_files
        data = read_files(bytes(self.ddd)).get(EF_IDENTIFICATION)
        if not data or len(data) < 143:
            return
        self.issuing_country = COUNTRIES.get(data[0], "??")
        self.card_number = decode_ascii(data[1:17])
        self.card_expiry = decode_timestamp(data[61:65])
        self.driver_surname = decode_text(data[65:101])
        self.driver_firstname = decode_text(data[101:137])
        self.driver_name = f"{self.driver_surname} {self.driver_firstname}".strip()


_STAGE_NAMES = {
    EF_CARD_CERTIFICATE: "Reading certificates",
    EF_CA_CERTIFICATE: "Reading certificates",
    EF_IDENTIFICATION: "Reading driver identity",
    EF_CARD_DOWNLOAD: "Reading download record",
    EF_DRIVING_LICENCE_INFO: "Reading licence details",
    EF_EVENTS_DATA: "Reading events",
    EF_FAULTS_DATA: "Reading faults",
    EF_DRIVER_ACTIVITY_DATA: "Reading driver activity",
    EF_VEHICLES_USED: "Reading vehicles used",
    EF_PLACES: "Reading places",
    EF_CURRENT_USAGE: "Reading current usage",
    EF_CONTROL_ACTIVITY_DATA: "Reading control activity",
    EF_SPECIFIC_CONDITIONS: "Reading specific conditions",
}
