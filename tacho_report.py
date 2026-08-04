#!/usr/bin/env python3
"""
Tachograph DDD File Report Generator
Shows last N days of activity with vehicle usage
"""

import sys
import argparse
import time
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

warnings.filterwarnings('ignore', category=DeprecationWarning)

# ANSI colors
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    WHITE = "\033[97m"
    MAGENTA = "\033[35m"

COUNTRIES = {
    0x00: "??", 0x01: "AT", 0x02: "AL", 0x03: "AD", 0x04: "AM", 0x05: "AZ",
    0x06: "BE", 0x07: "BG", 0x08: "BA", 0x09: "BY", 0x0A: "CH", 0x0B: "CY",
    0x0C: "CZ", 0x0D: "DE", 0x0E: "DK", 0x0F: "ES", 0x10: "EE", 0x11: "FR",
    0x12: "FI", 0x13: "LI", 0x14: "FO", 0x15: "GB", 0x16: "GE", 0x17: "GR",
    0x18: "HU", 0x19: "HR", 0x1A: "IT", 0x1B: "IE", 0x1C: "IS", 0x1D: "KZ",
    0x1E: "LU", 0x1F: "LT", 0x20: "LV", 0x21: "MT", 0x22: "MC", 0x23: "MD",
    0x24: "MK", 0x25: "NO", 0x26: "NL", 0x27: "PT", 0x28: "PL", 0x29: "RO",
    0x2A: "SM", 0x2B: "RU", 0x2C: "SE", 0x2D: "SK", 0x2E: "SI", 0x2F: "TM",
    0x30: "TR", 0x31: "UA", 0x32: "VA", 0xFD: "EU", 0xFE: "EUR", 0xFF: "WLD"
}


def clear():
    print("\033[H\033[2J\033[3J", end="", flush=True)


def print_header():
    print(f"""
 {C.CYAN}{C.BOLD} ╔══════════════════════════════════════════════════════════╗
 ║                                                          ║
 ║        ████████╗ █████╗  ██████╗██╗  ██╗ ██████╗         ║
 ║        ╚══██╔══╝██╔══██╗██╔════╝██║  ██║██╔═══██╗        ║
 ║           ██║   ███████║██║     ███████║██║   ██║        ║
 ║           ██║   ██╔══██║██║     ██╔══██║██║   ██║        ║
 ║           ██║   ██║  ██║╚██████╗██║  ██║╚██████╔╝        ║
 ║           ╚═╝   ╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝ ╚═════╝         ║
 ║                                                          ║
 ║           ACTIVITY REPORT GENERATOR                       ║
 ╚══════════════════════════════════════════════════════════╝{C.RESET}
""")


def animated_loading(message, frames=12, delay=0.08):
    spinners = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    for i in range(frames):
        spinner = spinners[i % len(spinners)]
        print(f"\r  {C.CYAN}{spinner}{C.RESET} {message}{'.' * ((i % 3) + 1)}", end="", flush=True)
        time.sleep(delay)
    print(f"\r  {C.GREEN}✓{C.RESET} {message}", flush=True)


class DDDParser:
    """Parse DDD tachograph files"""

    def __init__(self, filepath):
        self.filepath = Path(filepath)
        self.data = {}
        self.driver_name = None
        self.card_number = None
        self.vehicles = []
        self.activities = []

    def read_file(self):
        """Read and parse DDD file"""
        with open(self.filepath, 'rb') as f:
            raw = f.read()

        pos = 0
        while pos < len(raw) - 5:
            # Each record: FID (2 bytes) + type (1 byte) + length (2 bytes) + data
            fid = (raw[pos] << 8) | raw[pos + 1]
            rec_type = raw[pos + 2]  # 0 = data, 1 = signature
            length = (raw[pos + 3] << 8) | raw[pos + 4]
            pos += 5

            if pos + length > len(raw):
                break

            data = raw[pos:pos + length]
            pos += length

            # Only process data records (not signatures)
            if rec_type == 0:
                self.data[fid] = data

        self._parse_identification()
        self._parse_vehicles()
        self._parse_activities()

    def _parse_timestamp(self, data):
        """Parse 4-byte Unix timestamp"""
        if len(data) < 4:
            return None
        ts = (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3]
        if ts == 0:
            return None
        try:
            return datetime.utcfromtimestamp(ts)
        except:
            return None

    def _parse_odometer(self, data):
        """Parse 3-byte odometer value (km)"""
        if len(data) < 3:
            return 0
        return (data[0] << 16) | (data[1] << 8) | data[2]

    def _decode_text(self, data):
        """Decode ISO-8859 text with codepage byte"""
        if len(data) < 2:
            return ""
        codepage = data[0]
        text = data[1:].rstrip(b'\x00').rstrip(b' ')
        try:
            if codepage == 0:
                return text.decode('ascii', errors='ignore')
            return text.decode(f'iso-8859-{codepage}', errors='ignore')
        except:
            return text.decode('ascii', errors='ignore')

    def _parse_identification(self):
        """Parse EF_Identification (0x0520)"""
        data = self.data.get(0x0520)
        if not data or len(data) < 143:
            return

        self.card_number = data[1:17].rstrip(b'\x00').rstrip(b' ').decode('ascii', errors='ignore')
        surname = self._decode_text(data[65:101])
        firstname = self._decode_text(data[101:137])
        self.driver_name = f"{surname} {firstname}".strip()

    def _parse_vehicles(self):
        """Parse EF_Vehicles_Used (0x0505)"""
        data = self.data.get(0x0505)
        if not data or len(data) < 4:
            return

        # First 2 bytes: pointer to newest record
        # Then vehicle records, each 31 bytes
        pos = 2
        while pos + 31 <= len(data):
            rec = data[pos:pos + 31]

            odometer_begin = self._parse_odometer(rec[0:3])
            odometer_end = self._parse_odometer(rec[3:6])
            first_use = self._parse_timestamp(rec[6:10])
            last_use = self._parse_timestamp(rec[10:14])

            # Vehicle registration: nation (1 byte) + number (14 bytes with codepage)
            nation_code = rec[14]
            nation = COUNTRIES.get(nation_code, "??")
            reg_raw = rec[15:29]

            # Registration number has codepage as first byte
            if reg_raw[0] > 0:
                reg_text = reg_raw[1:].rstrip(b'\x00').rstrip(b' ')
                try:
                    reg = reg_text.decode(f'iso-8859-{reg_raw[0]}', errors='ignore')
                except:
                    reg = reg_text.decode('ascii', errors='ignore')
            else:
                reg = reg_raw[1:].rstrip(b'\x00').rstrip(b' ').decode('ascii', errors='ignore')

            if first_use and reg:  # Only add valid records
                self.vehicles.append({
                    'registration': reg,
                    'nation': nation,
                    'first_use': first_use,
                    'last_use': last_use,
                    'odometer_begin': odometer_begin,
                    'odometer_end': odometer_end,
                    'distance': odometer_end - odometer_begin
                })

            pos += 31

    def _parse_activities(self):
        """Parse EF_Driver_Activity_Data (0x0504)"""
        data = self.data.get(0x0504)
        if not data or len(data) < 6:
            return

        # First 4 bytes: pointers
        oldest_ptr = (data[0] << 8) | data[1]
        newest_ptr = (data[2] << 8) | data[3]

        # Activity records start at offset 4
        activity_data = data[4:]

        if len(activity_data) == 0:
            return

        # Parse circular buffer of daily records
        ptr = oldest_ptr
        if ptr >= len(activity_data):
            ptr = 0

        records_parsed = 0
        max_records = 100  # Safety limit

        while records_parsed < max_records:
            if ptr + 4 > len(activity_data):
                break

            # Record structure: prev_len(2) + rec_len(2) + date(4) + presence(2) + distance(2) + activities...
            rec_len = (activity_data[ptr + 2] << 8) | activity_data[ptr + 3]

            if rec_len < 12 or ptr + rec_len > len(activity_data):
                break

            rec_date = self._parse_timestamp(activity_data[ptr + 4:ptr + 8])
            daily_distance = (activity_data[ptr + 10] << 8) | activity_data[ptr + 11]

            if rec_date:
                self.activities.append({
                    'date': rec_date,
                    'distance': daily_distance
                })

            if ptr == newest_ptr:
                break

            ptr = (ptr + rec_len) % len(activity_data)
            records_parsed += 1

    def get_daily_summary(self, days=14):
        """Get summary for last N days with vehicle info"""
        cutoff = datetime.utcnow() - timedelta(days=days)

        # Group vehicles by date
        daily_data = {}

        for v in self.vehicles:
            if v['first_use'] and v['first_use'] >= cutoff:
                date_key = v['first_use'].strftime('%Y-%m-%d')

                if date_key not in daily_data:
                    daily_data[date_key] = {
                        'date': v['first_use'].date(),
                        'vehicles': [],
                        'total_distance': 0
                    }

                daily_data[date_key]['vehicles'].append(v)
                daily_data[date_key]['total_distance'] += v['distance']

        # Sort by date descending
        return sorted(daily_data.values(), key=lambda x: x['date'], reverse=True)


def print_report(parser, days=14):
    print(f"""
 {C.MAGENTA}{C.BOLD}╔══════════════════════════════════════════════════════════════╗
 ║            TACHOGRAPH ACTIVITY REPORT - LAST {days} DAYS          ║
 ╚══════════════════════════════════════════════════════════════╝{C.RESET}
""")

    print(f"  {C.CYAN}Driver{C.RESET}:     {C.WHITE}{C.BOLD}{parser.driver_name or 'UNKNOWN'}{C.RESET}")
    print(f"  {C.CYAN}Card{C.RESET}:       {C.DIM}{parser.card_number or '---'}{C.RESET}")
    print(f"  {C.CYAN}File{C.RESET}:       {C.DIM}{parser.filepath.name}{C.RESET}")
    print()

    summary = parser.get_daily_summary(days)

    if not summary:
        print(f"  {C.YELLOW}⚠ No activity found in the last {days} days.{C.RESET}\n")

        if parser.vehicles:
            print(f"  {C.DIM}All vehicle records on card:{C.RESET}")
            for v in sorted(parser.vehicles, key=lambda x: x['first_use'] or datetime.min, reverse=True)[:10]:
                if v['first_use']:
                    print(f"    {v['first_use'].strftime('%d/%m/%Y')} - {v['registration']} ({v['distance']} km)")
        return

    print(f"  {C.WHITE}{C.BOLD}{'DATE':<12} {'START':<8} {'END':<8} {'VEHICLE':<14} {'START KM':>10} {'END KM':>10} {'DISTANCE':>10}{C.RESET}")
    print(f"  {C.DIM}{'─' * 76}{C.RESET}")

    total_km = 0
    total_trips = 0

    for day in summary:
        date_str = day['date'].strftime('%a %d/%m')

        for i, v in enumerate(day['vehicles']):
            start_time = v['first_use'].strftime('%H:%M') if v['first_use'] else '--:--'
            end_time = v['last_use'].strftime('%H:%M') if v['last_use'] else '--:--'
            reg = v['registration'][:14]

            if i == 0:
                date_col = f"{C.GREEN}{date_str:<12}{C.RESET}"
            else:
                date_col = " " * 12

            print(f"  {date_col} {C.CYAN}{start_time:<8}{C.RESET} {C.YELLOW}{end_time:<8}{C.RESET} {C.WHITE}{C.BOLD}{reg:<14}{C.RESET} {v['odometer_begin']:>10,} {v['odometer_end']:>10,} {C.GREEN}{v['distance']:>9,} km{C.RESET}")
            total_km += v['distance']
            total_trips += 1

    print(f"  {C.DIM}{'─' * 76}{C.RESET}")
    print(f"  {C.WHITE}{C.BOLD}{'TOTAL':<54} {total_km:>19,} km{C.RESET}")
    print(f"  {C.DIM}{'':<54} {total_trips:>19,} trips{C.RESET}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Generate tachograph activity reports from .ddd files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # Use most recent file, last 14 days
  %(prog)s driver_xxx.ddd           # Use specific file, last 14 days
  %(prog)s driver_xxx.ddd -d 7      # Last 7 days only
  %(prog)s -d 30 -o report.txt      # Last 30 days, save to file
        """
    )

    parser.add_argument(
        'file',
        nargs='?',
        help='Path to .ddd file (default: most recent in downloads/)'
    )

    parser.add_argument(
        '-d', '--days',
        type=int,
        default=14,
        help='Number of days to report (default: 14)'
    )

    parser.add_argument(
        '-o', '--output',
        type=Path,
        help='Save report to file instead of stdout'
    )

    args = parser.parse_args()

    clear()
    print_header()

    # Find .ddd file
    if args.file:
        ddd_file = Path(args.file)
    else:
        downloads_dir = Path(__file__).parent / "downloads"
        print(f"  {C.CYAN}Searching for .ddd files in downloads/...{C.RESET}", flush=True)

        ddd_files = sorted(downloads_dir.glob("*.ddd"), key=lambda x: x.stat().st_mtime, reverse=True)
        if not ddd_files:
            print(f"  {C.RED}✗ No .ddd files found in {downloads_dir}{C.RESET}")
            print(f"  {C.DIM}  Run downloader first to get card data.{C.RESET}\n")
            sys.exit(1)
        ddd_file = ddd_files[0]
        print(f"  {C.GREEN}✓ Found: {ddd_file.name}{C.RESET}\n")

    if not ddd_file.exists():
        print(f"  {C.RED}✗ File not found: {ddd_file}{C.RESET}\n")
        sys.exit(1)

    animated_loading("Parsing DDD file")
    ddd_parser = DDDParser(ddd_file)
    ddd_parser.read_file()

    animated_loading("Generating report")

    if args.output:
        with open(args.output, 'w') as f:
            old_stdout = sys.stdout
            sys.stdout = f
            print_report(ddd_parser, days=args.days)
            sys.stdout = old_stdout
        print(f"  {C.GREEN}✓ Report saved to: {args.output}{C.RESET}\n")
    else:
        print_report(ddd_parser, days=args.days)


if __name__ == "__main__":
    main()
