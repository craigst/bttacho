#!/usr/bin/env python3
"""
Simple Tachograph - Download card data and show 2-week report
"""

import sys
import time
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smartcard.System import readers
from smartcard.Exceptions import NoCardException, CardConnectionException


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"


COUNTRIES = {
    0x00: "---", 0x01: "AT", 0x02: "AL", 0x03: "AD", 0x04: "AM", 0x05: "AZ",
    0x06: "BE", 0x07: "BG", 0x08: "BA", 0x09: "BY", 0x0A: "CH", 0x0B: "CY",
    0x0C: "CZ", 0x0D: "DE", 0x0E: "DK", 0x0F: "ES", 0x10: "EE", 0x11: "FR",
    0x12: "FI", 0x13: "LI", 0x14: "FO", 0x15: "GB", 0x16: "GE", 0x17: "GR",
    0x18: "HU", 0x19: "HR", 0x1A: "IT", 0x1B: "IE", 0x1C: "IS", 0x1D: "KZ",
    0x1E: "LU", 0x1F: "LT", 0x20: "LV", 0x21: "MT", 0x22: "MC", 0x23: "MD",
    0x24: "MK", 0x25: "NO", 0x26: "NL", 0x27: "PT", 0x28: "PL", 0x29: "RO",
    0x2A: "SM", 0x2B: "RU", 0x2C: "SE", 0x2D: "SK", 0x2E: "SI", 0x2F: "TM",
    0x30: "TR", 0x31: "UA", 0x32: "VA", 0xFD: "EU", 0xFE: "EUR", 0xFF: "WLD"
}


OUTPUT_DIR = Path(__file__).parent / "downloads"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def clear():
    print("\033[H\033[2J\033[3J", end="", flush=True)


class TachoReader:
    def __init__(self, connection):
        self.conn = connection
        self.ddd = bytearray()
        self.driver_name = None
        self.driver_surname = None
        self.driver_firstname = None
        self.card_number = None
        self.card_expiry = None
        self.issuing_country = None
        self.params = {}

    def select(self, file_id, by_name=False):
        if by_name:
            apdu = [0x00, 0xA4, 0x04, 0x0C, 0x06, 0xFF, 0x54, 0x41, 0x43, 0x48, 0x4F]
        else:
            apdu = [0x00, 0xA4, 0x02, 0x0C, 0x02, (file_id >> 8) & 0xFF, file_id & 0xFF]
        data, sw1, sw2 = self.conn.transmit(apdu)
        return (sw1 << 8) | sw2

    def read_binary(self, size):
        result = bytearray()
        pos = 0
        while pos < size:
            chunk = min(200, size - pos)
            apdu = [0x00, 0xB0, (pos >> 8) & 0xFF, pos & 0xFF, chunk]
            data, sw1, sw2 = self.conn.transmit(apdu)
            if sw1 != 0x90:
                return None
            result.extend(data)
            pos += len(data)
        return bytes(result)

    def perform_hash(self):
        apdu = [0x80, 0x2A, 0x90, 0x00]
        _, sw1, sw2 = self.conn.transmit(apdu)
        return sw1 == 0x90

    def compute_signature(self):
        apdu = [0x00, 0x2A, 0x9E, 0x9A, 0x80]
        data, sw1, sw2 = self.conn.transmit(apdu)
        if sw1 == 0x90:
            return bytes(data)
        return None

    def append_ddd(self, fid, data, is_sig=False):
        self.ddd.extend([(fid >> 8) & 0xFF, fid & 0xFF])
        self.ddd.append(0x01 if is_sig else 0x00)
        self.ddd.extend([(len(data) >> 8) & 0xFF, len(data) & 0xFF])
        self.ddd.extend(data)

    def read_file(self, fid, size, store=True, sign=False):
        sw = self.select(fid)
        if sw != 0x9000:
            return None
        if sign:
            self.perform_hash()
        data = self.read_binary(size)
        if data is None:
            return None
        if store:
            self.append_ddd(fid, data)
        if sign:
            sig = self.compute_signature()
            if sig:
                self.append_ddd(fid, sig, is_sig=True)
        return data

    def decode_name(self, raw):
        if len(raw) < 2:
            return ""
        codepage = raw[0]
        text = raw[1:].rstrip(b'\x00').rstrip(b' ')
        try:
            if codepage == 0:
                return ""
            return text.decode(f'iso-8859-{codepage}', errors='ignore')
        except:
            return text.decode('ascii', errors='ignore')

    def parse_timestamp(self, data):
        if len(data) < 4:
            return None
        ts = (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3]
        if ts == 0:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    def download(self, progress_cb=None):
        self.ddd = bytearray()
        files_done = 0
        total_files = 16

        def update(name):
            nonlocal files_done
            files_done += 1
            if progress_cb:
                progress_cb(name, files_done, total_files, self.driver_name)

        update("EF_ICC")
        if self.read_file(0x0002, 25, True, False) is None:
            raise Exception("Failed to read EF_ICC")

        update("EF_IC")
        if self.read_file(0x0005, 8, True, False) is None:
            raise Exception("Failed to read EF_IC")

        sw = self.select(0, by_name=True)
        if sw != 0x9000:
            raise Exception("Failed to select Tachograph DF")

        update("EF_Application_Identification")
        data = self.read_file(0x0501, 10, True, True)
        if data is None:
            raise Exception("Failed to read EF_Application_Identification")

        self.params = {
            'events': data[3],
            'faults': data[4],
            'activity': (data[5] << 8) | data[6],
            'vehicles': (data[7] << 8) | data[8],
            'places': data[9],
        }

        update("EF_Card_Certificate")
        self.read_file(0xC100, 194, True, False)

        update("EF_CA_Certificate")
        self.read_file(0xC108, 194, True, False)

        update("EF_Identification")
        data = self.read_file(0x0520, 143, True, True)
        if data and len(data) >= 143:
            self.issuing_country = COUNTRIES.get(data[0], "??")
            self.card_number = data[1:17].rstrip(b'\x00').rstrip(b' ').decode('ascii', errors='ignore')
            self.card_expiry = self.parse_timestamp(data[61:65])
            self.driver_surname = self.decode_name(data[65:101])
            self.driver_firstname = self.decode_name(data[101:137])
            self.driver_name = f"{self.driver_surname} {self.driver_firstname}".strip()

        update("EF_Card_Download")
        self.read_file(0x050E, 4, True, False)

        update("EF_Driving_Licence_Info")
        self.read_file(0x0521, 53, True, True)

        update("EF_Events_Data")
        self.read_file(0x0502, self.params['events'] * 24 * 6, True, True)

        update("EF_Faults_Data")
        self.read_file(0x0503, self.params['faults'] * 24 * 2, True, True)

        update("EF_Driver_Activity_Data")
        self.read_file(0x0504, self.params['activity'] + 4, True, True)

        update("EF_Vehicles_Used")
        self.read_file(0x0505, self.params['vehicles'] * 31 + 2, True, True)

        update("EF_Places")
        self.read_file(0x0506, self.params['places'] * 10 + 1, True, True)

        update("EF_Current_Usage")
        self.read_file(0x0507, 19, True, True)

        update("EF_Control_Activity_Data")
        self.read_file(0x0508, 46, True, True)

        update("EF_Specific_Conditions")
        self.read_file(0x0522, 280, True, True)

        return self.ddd

    def save(self):
        name = self.driver_name or "driver"
        name = ''.join(c if c.isalnum() or c == ' ' else '_' for c in name)
        name = name.replace(' ', '_').lower()
        timestamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        filename = f"{name}_{timestamp}.ddd"
        filepath = OUTPUT_DIR / filename
        with open(filepath, 'wb') as f:
            f.write(self.ddd)
        return filepath


class DDDParser:
    def __init__(self, filepath):
        self.filepath = Path(filepath)
        self.data = {}
        self.driver_name = None
        self.card_number = None
        self.vehicles = []
        self.activities = []

    def read_file(self):
        with open(self.filepath, 'rb') as f:
            raw = f.read()

        pos = 0
        while pos < len(raw) - 5:
            fid = (raw[pos] << 8) | raw[pos + 1]
            rec_type = raw[pos + 2]
            length = (raw[pos + 3] << 8) | raw[pos + 4]
            pos += 5

            if pos + length > len(raw):
                break

            data = raw[pos:pos + length]
            pos += length

            if rec_type == 0:
                self.data[fid] = data

        self._parse_identification()
        self._parse_vehicles()
        self._parse_activities()

    def _parse_timestamp(self, data):
        if len(data) < 4:
            return None
        ts = (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3]
        if ts == 0:
            return None
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except:
            return None

    def _parse_odometer(self, data):
        if len(data) < 3:
            return 0
        return (data[0] << 16) | (data[1] << 8) | data[2]

    def _decode_text(self, data):
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
        data = self.data.get(0x0520)
        if not data or len(data) < 143:
            return

        self.card_number = data[1:17].rstrip(b'\x00').rstrip(b' ').decode('ascii', errors='ignore')
        surname = self._decode_text(data[65:101])
        firstname = self._decode_text(data[101:137])
        self.driver_name = f"{surname} {firstname}".strip()

    def _parse_vehicles(self):
        data = self.data.get(0x0505)
        if not data or len(data) < 4:
            return

        pos = 2
        while pos + 31 <= len(data):
            rec = data[pos:pos + 31]

            odometer_begin = self._parse_odometer(rec[0:3])
            odometer_end = self._parse_odometer(rec[3:6])
            first_use = self._parse_timestamp(rec[6:10])
            last_use = self._parse_timestamp(rec[10:14])

            nation_code = rec[14]
            nation = COUNTRIES.get(nation_code, "??")
            reg_raw = rec[15:29]

            if reg_raw[0] > 0:
                reg_text = reg_raw[1:].rstrip(b'\x00').rstrip(b' ')
                try:
                    reg = reg_text.decode(f'iso-8859-{reg_raw[0]}', errors='ignore')
                except:
                    reg = reg_text.decode('ascii', errors='ignore')
            else:
                reg = reg_raw[1:].rstrip(b'\x00').rstrip(b' ').decode('ascii', errors='ignore')

            if first_use and reg:
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
        data = self.data.get(0x0504)
        if not data or len(data) < 6:
            return

        oldest_ptr = (data[0] << 8) | data[1]
        newest_ptr = (data[2] << 8) | data[3]
        activity_data = data[4:]

        if len(activity_data) == 0:
            return

        ptr = oldest_ptr
        if ptr >= len(activity_data):
            ptr = 0

        records_parsed = 0
        max_records = 100

        while records_parsed < max_records:
            if ptr + 4 > len(activity_data):
                break

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
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

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

        return sorted(daily_data.values(), key=lambda x: x['date'], reverse=True)


def print_header():
    print(f"""
{Colors.CYAN}{Colors.BOLD}================================================
       TACHOGRAPH CARD DOWNLOADER & REPORT
================================================{Colors.RESET}
""")


def print_driver_card(driver):
    print(f"\n{Colors.GREEN}Driver:{Colors.RESET}     {Colors.BOLD}{driver.driver_name or 'UNKNOWN'}{Colors.RESET}")
    print(f"{Colors.GREEN}Card:{Colors.RESET}       {driver.card_number or '---'}")
    print(f"{Colors.GREEN}Country:{Colors.RESET}     {driver.issuing_country or '--'}")
    print(f"{Colors.GREEN}Expiry:{Colors.RESET}     {driver.card_expiry.strftime('%d/%m/%Y') if driver.card_expiry else '--/--/----'}\n")


def print_progress(name, done, total, driver):
    pct = done / total * 100
    bar = "#" * int(pct / 100 * 40) + "-" * (40 - int(pct / 100 * 40))
    print(f"  Downloading [{bar}] {pct:5.1f}% - {name} ({driver or 'Reading...'})")
    sys.stdout.flush()


def print_report(parser, days=14):
    print(f"\n{Colors.CYAN}{Colors.BOLD}{'=' * 56}{Colors.RESET}")
    print(f"{Colors.CYAN}{Colors.BOLD}  ACTIVITY REPORT - LAST {days} DAYS{Colors.RESET}")
    print(f"{Colors.CYAN}{Colors.BOLD}{'=' * 56}{Colors.RESET}\n")

    print(f"  {Colors.BOLD}{'DATE':<12} {'START':<8} {'END':<8} {'VEHICLE':<14} {'KM':>10}{Colors.RESET}")
    print(f"  {'-' * 56}")

    summary = parser.get_daily_summary(days)

    if not summary:
        print(f"  {Colors.YELLOW}No activity in last {days} days{Colors.RESET}\n")
        return

    total_km = 0
    total_trips = 0

    for day in summary:
        date_str = day['date'].strftime('%a %d/%m')

        for i, v in enumerate(day['vehicles']):
            start_time = v['first_use'].strftime('%H:%M') if v['first_use'] else '--:--'
            end_time = v['last_use'].strftime('%H:%M') if v['last_use'] else '--:--'
            reg = v['registration'][:14]

            if i == 0:
                date_col = f"{Colors.GREEN}{date_str:<12}{Colors.RESET}"
            else:
                date_col = " " * 12

            print(f"  {date_col} {start_time:<8} {end_time:<8} {Colors.BOLD}{reg:<14}{Colors.RESET} {v['distance']:>10,}")
            total_km += v['distance']
            total_trips += 1

    print(f"  {'-' * 56}")
    print(f"  {Colors.BOLD}{'TOTAL':<44} {total_km:>11,} km{Colors.RESET}")
    print(f"  {'':<44} {total_trips:>11,} trips{Colors.RESET}\n")


def main():
    parser = argparse.ArgumentParser(description="Download tacho card and show 2-week report")
    parser.add_argument('-t', '--timeout', type=int, default=120,
                       help='Wait timeout in seconds (default: 120)')
    parser.add_argument('-d', '--days', type=int, default=14,
                       help='Report days (default: 14)')
    parser.add_argument('-r', '--report-only', type=Path,
                       help='Show report for existing file, skip download')
    args = parser.parse_args()

    clear()
    print_header()

    if args.report_only:
        ddd_file = args.report_only
        if not ddd_file.exists():
            print(f"{Colors.RED}File not found: {ddd_file}{Colors.RESET}")
            sys.exit(1)

        print(f"{Colors.CYAN}Reading {ddd_file.name}...{Colors.RESET}\n")
        ddd_parser = DDDParser(ddd_file)
        ddd_parser.read_file()
        print_driver_card(type('obj', (object,), {
            'driver_name': ddd_parser.driver_name,
            'card_number': ddd_parser.card_number,
            'issuing_country': '',
            'card_expiry': None
        }))
        print_report(ddd_parser, days=args.days)
        return

    reader_list = readers()
    if not reader_list:
        print(f"{Colors.RED}ERROR: No card readers found!{Colors.RESET}")
        print(f"{Colors.CYAN}Connect a smart card reader and try again.{Colors.RESET}\n")
        sys.exit(1)

    reader = reader_list[0]
    print(f"{Colors.CYAN}Reader:{Colors.RESET}      {reader}")
    print(f"{Colors.CYAN}Waiting for card... (Ctrl+C to cancel){Colors.RESET}\n")

    start_time = time.time()

    try:
        while True:
            elapsed = time.time() - start_time
            if elapsed > args.timeout:
                print(f"{Colors.YELLOW}TIMEOUT: No card detected{Colors.RESET}\n")
                sys.exit(0)

            try:
                conn = reader.createConnection()
                conn.connect()

                clear()
                print_header()
                print(f"{Colors.GREEN}Card detected!{Colors.RESET}\n")

                tacho = TachoReader(conn)
                tacho.download(print_progress)

                filepath = tacho.save()
                conn.disconnect()

                print(f"\n{Colors.GREEN}Download complete!{Colors.RESET}")
                print(f"{Colors.CYAN}File:{Colors.RESET} {filepath}")
                print(f"{Colors.CYAN}Size:{Colors.RESET} {len(tacho.ddd):,} bytes\n")

                print_driver_card(tacho)

                ddd_parser = DDDParser(filepath)
                ddd_parser.read_file()
                print_report(ddd_parser, days=args.days)

                sys.exit(0)

            except (NoCardException, CardConnectionException):
                time.sleep(0.1)

    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Cancelled{Colors.RESET}\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
