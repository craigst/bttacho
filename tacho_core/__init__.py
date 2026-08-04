"""Shared tachograph card reading and parsing.

Single source of truth for the card protocol, the .ddd container format, and
the report model. Every Python frontend imports from here -- a fix to an EF
offset or a record layout lands once.

The Kotlin implementation under android/ mirrors this by hand and does not
pick up changes automatically.
"""

from .codec import decode_ascii, decode_odometer, decode_text, decode_timestamp
from .countries import COUNTRIES
from .ddd import iter_blocks, pack_block, read_files
from .models import DownloadProgress, DriverReport, TripRecord
from .parser import DDDParser
from .reader import CardError, NotATachographCard, TachoReader
from .report import build_report

__all__ = [
    "COUNTRIES",
    "TachoReader", "CardError", "NotATachographCard",
    "DDDParser", "build_report",
    "DriverReport", "TripRecord", "DownloadProgress",
    "pack_block", "iter_blocks", "read_files",
    "decode_text", "decode_ascii", "decode_timestamp", "decode_odometer",
]
