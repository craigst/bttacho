#!/usr/bin/env python3
"""Tachograph Card Downloader - Download and show 2-week report"""

import argparse
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from smartcard.System import readers
from smartcard.Exceptions import NoCardException, CardConnectionException


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    WHITE = "\033[97m"


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


def parse_odometer(data):
    if len(data) < 3:
        return 0
    return (data[0] << 16) | (data[1] << 8) | data[2]


def decode_text(data):
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


def show_activity_report(ddd_file, days=14):
    print(f"\n{Colors.CYAN}{Colors.BOLD}{'=' * 56}{Colors.RESET}")
    print(f"{Colors.CYAN}{Colors.BOLD}  ACTIVITY REPORT - LAST {days} DAYS{Colors.RESET}")
    print(f"{Colors.CYAN}{Colors.BOLD}{'=' * 56}{Colors.RESET}\n")

    with open(ddd_file, 'rb') as f:
        raw = f.read()

    data = {}
    pos = 0
    while pos < len(raw) - 5:
        fid = (raw[pos] << 8) | raw[pos + 1]
        rec_type = raw[pos + 2]
        length = (raw[pos + 3] << 8) | raw[pos + 4]
        pos += 5

        if pos + length > len(raw):
            break

        record_data = raw[pos:pos + length]
        pos += length

        if rec_type == 0:
            data[fid] = record_data

    driver_name = None
    card_number = None

    if 0x0520 in data and len(data[0x0520]) >= 143:
        ident = data[0x0520]
        card_number = ident[1:17].rstrip(b'\x00').rstrip(b' ').decode('ascii', errors='ignore')
        surname = decode_text(ident[65:101])
        firstname = decode_text(ident[101:137])
        driver_name = f"{surname} {firstname}".strip()

    if 0x0505 in data:
        vehicles_data = data[0x0505]
        pos = 2
        vehicles = []
        while pos + 31 <= len(vehicles_data):
            rec = vehicles_data[pos:pos + 31]

            odometer_begin = parse_odometer(rec[0:3])
            odometer_end = parse_odometer(rec[3:6])

            nation_code = rec[14]
            nation = COUNTRIES.get(nation_code, "??")
            reg_raw = rec[15:29]

            if reg_raw[0] > 0:
                try:
                    reg = reg_raw[1:].rstrip(b'\x00').rstrip(b' ').decode(f'iso-8859-{reg_raw[0]}', errors='ignore')
                except:
                    reg = reg_raw[1:].rstrip(b'\x00').rstrip(b' ').decode('ascii', errors='ignore')
            else:
                reg = reg_raw[1:].rstrip(b'\x00').rstrip(b' ').decode('ascii', errors='ignore')

            try:
                ts_bytes = rec[6:10]
                if len(ts_bytes) == 4:
                    ts = (ts_bytes[0] << 24) | (ts_bytes[1] << 16) | (ts_bytes[2] << 8) | ts_bytes[3]
                    if ts > 0:
                        first_use = datetime.fromtimestamp(ts, tz=timezone.utc)

                        ts_bytes = rec[10:14]
                        if len(ts_bytes) == 4:
                            ts = (ts_bytes[0] << 24) | (ts_bytes[1] << 16) | (ts_bytes[2] << 8) | ts_bytes[3]
                            if ts > 0:
                                last_use = datetime.fromtimestamp(ts, tz=timezone.utc)

                                distance = odometer_end - odometer_begin
                                if first_use and reg and distance > 0:
                                    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                                    if first_use >= cutoff:
                                        vehicles.append({
                                            'registration': reg,
                                            'first_use': first_use,
                                            'last_use': last_use,
                                            'distance': distance
                                        })
            except:
                pass

            pos += 31

        if vehicles:
            print(f"  {Colors.BOLD}{'DATE':<12} {'START':<8} {'END':<8} {'VEHICLE':<14} {'KM':>10}{Colors.RESET}")
            print(f"  {'-' * 56}")

            total_km = 0
            total_trips = 0

            for v in sorted(vehicles, key=lambda x: x['first_use'], reverse=True)[:20]:
                date_str = v['first_use'].strftime('%a %d/%m')
                start_time = v['first_use'].strftime('%H:%M')
                end_time = v['last_use'].strftime('%H:%M')
                reg = v['registration'][:14]

                print(f"  {Colors.GREEN}{date_str:<12}{Colors.RESET} {start_time:<8} {end_time:<8} {Colors.BOLD}{reg:<14}{Colors.RESET} {v['distance']:>10,}")
                total_km += v['distance']
                total_trips += 1

            print(f"  {'-' * 56}")
            print(f"  {Colors.BOLD}{'TOTAL':<44} {total_km:>11,} km{Colors.RESET}")
            print(f"  {'':<44} {total_trips:>11,} trips{Colors.RESET}\n")
        else:
            print(f"  {Colors.YELLOW}No vehicle activity in last {days} days{Colors.RESET}\n")


def print_header():
    print(f"""
{Colors.CYAN}{Colors.BOLD}+========================================================+
|                                                        |
|        TACHOGRAPH CARD DOWNLOADER                 |
|                                                        |
+========================================================+{Colors.RESET}
""")


def print_driver_card(surname, firstname, card_number, country, expiry):
    sn = surname or "UNKNOWN"
    fn = firstname or ""
    cn = card_number or "----------------"
    ex = expiry.strftime("%d/%m/%Y") if expiry else "--/--/----"
    co = country or "--"

    print(f"""
{Colors.CYAN}{Colors.BOLD}+--------------------------------------------------+
{Colors.CYAN}|{Colors.RESET}                                                  {Colors.CYAN}|
{Colors.CYAN}|{Colors.RESET}    [====]     {Colors.GREEN}{Colors.BOLD}{sn} {fn}{Colors.RESET}
{Colors.CYAN}|{Colors.RESET}   [ID==]     {Colors.GREEN}{Colors.BOLD}Card:{Colors.RESET} {cn}
{Colors.CYAN}|{Colors.RESET}   [--v-]     {Colors.GREEN}{Colors.BOLD}Expiry:{Colors.RESET} {ex}
{Colors.CYAN}|{Colors.RESET}   [====]     {Colors.GREEN}{Colors.BOLD}Country:{Colors.RESET} {co}
{Colors.CYAN}|{Colors.RESET}                                                  {Colors.CYAN}|
{Colors.CYAN}{Colors.BOLD}+--------------------------------------------------+
{Colors.RESET}
""")


def print_waiting_card():
    print(f"""
{Colors.DIM}  +----------------------------------------+
{Colors.DIM}  |                                        |
{Colors.DIM}  |                                        |
{Colors.DIM}  |           +------------------+           |
{Colors.DIM}  |           |                  |           |
{Colors.DIM}  |           |  INSERT CARD     |           |
{Colors.DIM}  |           |       vvv vvv     |           |
{Colors.DIM}  |           |                  |           |
{Colors.DIM}  |           +------------------+           |
{Colors.DIM}  |                                        |
{Colors.DIM}  |                                        |
{Colors.DIM}  +----------------------------------------+  {Colors.RESET}
""")


def print_status_line(reader, status, extra=None):
    print(f"  {Colors.DIM}{'-' * 60}{Colors.RESET}")
    print(f"  {Colors.CYAN}READER{Colors.RESET}     {reader}")
    print(f"  {Colors.CYAN}STATUS{Colors.RESET}     {status}")
    if extra:
        for k, v in extra.items():
            print(f"  {Colors.CYAN}{k:<11}{Colors.RESET}{v}")
    print(f"  {Colors.DIM}{'-' * 60}{Colors.RESET}")


def print_progress_bar(filename, done, total, driver_name):
    bar_w = 44
    pct = done / total * 100
    filled = int(bar_w * done / total)
    bar = "#" * filled + "-" * (bar_w - filled)
    dots = "." * ((done % 3) + 1)

    print(f"""
  {Colors.WHITE}{Colors.BOLD}DOWNLOADING CARD DATA{dots:<3}{Colors.RESET}
  {Colors.DIM}{'-' * 60}{Colors.RESET}
  {Colors.CYAN}DRIVER{Colors.RESET}     {Colors.GREEN}{Colors.BOLD}{driver_name or 'Reading...'}{Colors.RESET}
  {Colors.CYAN}FILE{Colors.RESET}       {filename}
  {Colors.CYAN}PROGRESS{Colors.RESET}   [{Colors.GREEN}{bar}{Colors.RESET}] {pct:5.1f}%
  {Colors.DIM}{'-' * 60}{Colors.RESET}
""")


def animated_scan(frame):
    patterns = [
        "[=====>   ]",
        "[  ====>  ]",
        "[    ===> ]",
        "[     ==>]",
        "[      <==]",
        "[     <===]",
        "[    <====]",
        "[  <=====]",
        "[<======> ]",
        "[=======> ]",
    ]
    return patterns[frame % len(patterns)]


def show_success(tacho, filepath):
    clear()
    print_header()
    print_driver_card(
        tacho.driver_surname or "UNKNOWN",
        tacho.driver_firstname or "",
        tacho.card_number,
        tacho.issuing_country,
        tacho.card_expiry
    )

    print(f"""
  {Colors.GREEN}{Colors.BOLD}+========================================================+
  |                 DOWNLOAD COMPLETE                      |
  +========================================================+

  {Colors.CYAN}FILE{Colors.RESET}       {filepath.name}
  {Colors.CYAN}LOCATION{Colors.RESET}   {filepath.parent}/
  {Colors.CYAN}SIZE{Colors.RESET}       {len(tacho.ddd):,} bytes
  {Colors.CYAN}DRIVER{Colors.RESET}     {tacho.driver_name}
  {Colors.DIM}{'-' * 60}{Colors.RESET}
""")


def main():
    parser = argparse.ArgumentParser(
        description="Download tachograph card data and show 2-week report",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # Download with default timeout (120s), 14-day report
  %(prog)s -t 60                  # Wait 60 seconds for card
  %(prog)s -o mycard.ddd         # Save to specific filename
  %(prog)s -d 7                    # Show 7-day report instead of 14
        """
    )

    parser.add_argument(
        '-t', '--timeout',
        type=int,
        default=120,
        help='Wait timeout in seconds (default: 120)'
    )

    parser.add_argument(
        '-o', '--output',
        type=Path,
        help='Output filename (default: auto-generated)'
    )

    parser.add_argument(
        '-d', '--days',
        type=int,
        default=14,
        help='Show activity report for N days (default: 14)'
    )

    args = parser.parse_args()

    clear()
    print_header()

    reader_list = readers()
    if not reader_list:
        print(f"\n  {Colors.RED}ERROR: No card readers found!{Colors.RESET}")
        print(f"  {Colors.DIM}Connect a smart card reader and try again.{Colors.RESET}\n")
        sys.exit(1)

    reader = reader_list[0]
    reader_name = str(reader)

    frame = 0
    start_time = time.time()

    print(f"  {Colors.DIM}Initializing...{Colors.RESET}\n")

    try:
        while True:
            elapsed = time.time() - start_time
            remaining = args.timeout - elapsed

            if remaining <= 0:
                clear()
                print_header()
                print(f"\n  {Colors.YELLOW}TIMEOUT: No card detected within {args.timeout} seconds{Colors.RESET}")
                print(f"  {Colors.DIM}Exiting...{Colors.RESET}\n")
                sys.exit(0)

            try:
                conn = reader.createConnection()
                conn.connect()

                clear()
                print_header()
                print_status_line(reader_name, f"{Colors.GREEN}● CARD DETECTED{Colors.RESET}")
                print(f"\n  {Colors.YELLOW}Initializing card reader...{Colors.RESET}")
                time.sleep(0.3)

                try:
                    tacho = TachoReader(conn)

                    def progress(name, done, total, driver):
                        clear()
                        print_header()
                        if tacho.driver_surname:
                            print_driver_card(
                                tacho.driver_surname,
                                tacho.driver_firstname or "",
                                tacho.card_number,
                                tacho.issuing_country,
                                tacho.card_expiry
                            )
                        print_progress_bar(name, done, total, driver)

                    tacho.download(progress)

                    if args.output:
                        filepath = OUTPUT_DIR / args.output
                        with open(filepath, 'wb') as f:
                            f.write(tacho.ddd)
                    else:
                        filepath = tacho.save()

                    conn.disconnect()
                    show_success(tacho, filepath)

                    show_activity_report(filepath, args.days)

                    print(f"\n{Colors.CYAN}Press Enter to close...{Colors.RESET}", flush=True)
                    input()

                    sys.exit(0)

                except Exception as e:
                    try:
                        conn.disconnect()
                    except:
                        pass
                    print(f"\n  {Colors.RED}ERROR: {str(e)}{Colors.RESET}\n")
                    print(f"  {Colors.CYAN}Press Enter to close...{Colors.RESET}", flush=True)
                    input()
                    sys.exit(1)

            except (NoCardException, CardConnectionException):
                elapsed = time.time() - start_time
                remaining = args.timeout - elapsed
                time_str = f"{int(remaining):3d}s"
                scan_anim = animated_scan(frame)

                clear()
                print_header()
                print_waiting_card()
                print_status_line(
                    reader_name,
                    f"{Colors.YELLOW}◌ WAITING FOR CARD{Colors.RESET}  {Colors.CYAN}{scan_anim}{Colors.RESET}",
                    {"TIMEOUT": f"{time_str} remaining"}
                )
                print(f"\n  {Colors.DIM}Insert tachograph driver card to begin download...{Colors.RESET}")
                print(f"  {Colors.DIM}Press Ctrl+C to exit{Colors.RESET}\n")

                frame += 1
                time.sleep(0.08)

            except Exception as e:
                if "Card is not connected" not in str(e):
                    print(f"\n  {Colors.RED}Error: {e}{Colors.RESET}\n")
                time.sleep(0.3)

    except KeyboardInterrupt:
        print(f"\n\n  {Colors.DIM}Shutting down...{Colors.RESET}\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
