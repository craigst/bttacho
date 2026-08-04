#!/usr/bin/env python3
"""
Preflight + live card test for the tacho service.

Verifies the environment (PC/SC, pyscard, PyQt6, tray support), then optionally
waits for a card and exercises the real read path end to end -- without sending
anything anywhere.

    tacho-check.py            # environment only
    tacho-check.py --card     # also wait for a card and do a full test read
    tacho-check.py --card -t 120
"""

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import time

PASS, FAIL, WARN, INFO = "PASS", "FAIL", "WARN", "INFO"
_MARK = {PASS: "\033[92m ok \033[0m", FAIL: "\033[91mFAIL\033[0m",
         WARN: "\033[93mwarn\033[0m", INFO: "\033[94minfo\033[0m"}

results = []

# Card identity is personal data. Redacted unless explicitly requested, so that
# ordinary check output can be shared in a public bug report.
SHOW_IDENTITY = False


def check(name, status, detail=""):
    results.append((name, status, detail))
    print(f"  [{_MARK[status]}] {name}" + (f" — {detail}" if detail else ""))
    return status == PASS


def section(title):
    print(f"\n\033[1m{title}\033[0m")


def run(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


# ----------------------------------------------------------------- environment

def check_python():
    section("Python")
    v = sys.version_info
    check("interpreter", PASS if v >= (3, 9) else FAIL,
          f"{sys.executable} ({v.major}.{v.minor}.{v.micro})")

    for mod, why in [("smartcard", "pyscard — card I/O"),
                     ("PyQt6", "tray + window")]:
        spec = importlib.util.find_spec(mod)
        check(mod, PASS if spec else FAIL, why if spec else f"missing — {why}")

    # sqlite3 backs the outbox queue
    check("sqlite3", PASS if importlib.util.find_spec("sqlite3") else FAIL,
          "outbox queue")

    if importlib.util.find_spec("smartcard"):
        try:
            from smartcard.CardMonitoring import CardMonitor  # noqa: F401
            from smartcard.ReaderMonitoring import ReaderMonitor  # noqa: F401
            check("CardMonitor / ReaderMonitor", PASS, "event-driven detection available")
        except Exception as e:
            check("CardMonitor / ReaderMonitor", FAIL, str(e))


def check_pcsc():
    section("PC/SC daemon")
    if not shutil.which("systemctl"):
        check("systemctl", WARN, "not found — skipping service checks")
        return

    rc, out, _ = run(["systemctl", "is-active", "pcscd.socket"])
    check("pcscd.socket", PASS if out == "active" else FAIL, out or "inactive")

    rc, out, _ = run(["systemctl", "is-active", "pcscd"])
    # socket-activated pcscd is normally inactive until first use -- that's fine
    check("pcscd", INFO, f"{out} (socket-activated starts on demand)")


def check_reader(allow_missing=False):
    section("Reader")
    try:
        from smartcard.System import readers
    except Exception as e:
        check("pyscard import", FAIL, str(e))
        return []

    try:
        rl = readers()
    except Exception as e:
        check("enumerate readers", FAIL, f"{type(e).__name__}: {e}")
        return []

    if not rl:
        check("readers found", WARN if allow_missing else FAIL,
              "none — plug the reader in when ready")
        return []

    check("readers found", PASS, f"{len(rl)}")
    for i, r in enumerate(rl):
        check(f"  reader[{i}]", INFO, str(r))

    rc, out, _ = run(["lsusb"])
    for line in out.splitlines():
        if any(k in line.lower() for k in ("smartcard", "ccid", "reader")):
            check("  usb", INFO, line.split(": ", 1)[-1])
    return rl


def check_desktop():
    section("Desktop / tray")
    de = os.environ.get("XDG_CURRENT_DESKTOP", "")
    st = os.environ.get("XDG_SESSION_TYPE", "")
    check("session", PASS if de else WARN, f"{de or 'unknown'} / {st or 'unknown'}")

    if importlib.util.find_spec("PyQt6"):
        try:
            from PyQt6.QtWidgets import QApplication, QSystemTrayIcon
            app = QApplication.instance() or QApplication(sys.argv)
            ok = QSystemTrayIcon.isSystemTrayAvailable()
            check("system tray", PASS if ok else FAIL,
                  "available" if ok else "no tray in this session")
        except Exception as e:
            check("system tray", WARN, f"could not probe — {e}")

    rc, out, _ = run(["systemctl", "--user", "is-system-running"])
    check("systemd --user", PASS if out in ("running", "degraded") else WARN, out)


# ------------------------------------------------------------------- card test

TACHO_DF = [0x00, 0xA4, 0x04, 0x0C, 0x06, 0xFF, 0x54, 0x41, 0x43, 0x48, 0x4F]


def wait_for_card(timeout):
    from smartcard.System import readers
    from smartcard.Exceptions import NoCardException, CardConnectionException

    rl = readers()
    if not rl:
        return None
    reader = rl[0]
    print(f"\n  waiting for card in {reader} … (up to {timeout}s)")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            conn = reader.createConnection()
            conn.connect()
            return conn
        except (NoCardException, CardConnectionException):
            time.sleep(0.3)
    return None


def check_card(timeout):
    section("Card read test")
    conn = wait_for_card(timeout)
    if conn is None:
        check("card present", FAIL, f"no card inserted within {timeout}s")
        return

    atr = conn.getATR()
    check("card present", PASS, "ATR " + " ".join(f"{b:02X}" for b in atr))

    _, sw1, sw2 = conn.transmit(TACHO_DF)
    if (sw1 << 8 | sw2) != 0x9000:
        check("Tachograph DF", FAIL, f"SW={sw1:02X}{sw2:02X} — not a tachograph card")
        conn.disconnect()
        return
    check("Tachograph DF", PASS, "selected")

    # EF_Identification (0x0520) -- driver identity, 143 bytes
    def select(fid):
        _, s1, s2 = conn.transmit([0x00, 0xA4, 0x02, 0x0C, 0x02,
                                   (fid >> 8) & 0xFF, fid & 0xFF])
        return (s1 << 8) | s2

    def read_binary(size):
        buf, pos = bytearray(), 0
        while pos < size:
            n = min(200, size - pos)
            data, s1, _ = conn.transmit([0x00, 0xB0, (pos >> 8) & 0xFF, pos & 0xFF, n])
            if s1 != 0x90:
                return None
            buf.extend(data)
            pos += len(data)
        return bytes(buf)

    if select(0x0520) != 0x9000:
        check("EF_Identification", FAIL, "select failed")
        conn.disconnect()
        return

    data = read_binary(143)
    if not data or len(data) < 143:
        check("EF_Identification", FAIL, "short read")
        conn.disconnect()
        return

    def decode_name(raw):
        if len(raw) < 2 or raw[0] == 0:
            return ""
        return raw[1:].rstrip(b"\x00").rstrip(b" ").decode(
            f"iso-8859-{raw[0]}", errors="ignore")

    card_no = data[1:17].rstrip(b"\x00").rstrip(b" ").decode("ascii", errors="ignore")
    surname = decode_name(data[65:101])
    first = decode_name(data[101:137])

    if SHOW_IDENTITY:
        check("EF_Identification", PASS, f"{surname} {first} / {card_no}".strip())
    else:
        # Redacted by default -- this output is safe to paste into a bug report.
        # Decoding is still proven: a wrong codepage or offset yields empty/garbage.
        check("EF_Identification", PASS,
              f"decoded ok — name {len(surname)}+{len(first)} chars, "
              f"card no {len(card_no)} digits  (--show-identity to reveal)")

    # EF_Application_Identification (0x0501) -- record counts drive read sizes
    if select(0x0501) == 0x9000:
        d = read_binary(10)
        if d and len(d) >= 10:
            check("EF_Application_Identification", PASS,
                  f"vehicles={(d[7] << 8) | d[8]} activity={(d[5] << 8) | d[6]} places={d[9]}")
        else:
            check("EF_Application_Identification", WARN, "short read")

    conn.disconnect()
    check("card released", PASS, "no data was sent anywhere")


# ------------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description="Tacho service preflight check")
    ap.add_argument("--card", action="store_true",
                    help="also wait for a card and do a live test read")
    ap.add_argument("-t", "--timeout", type=int, default=60,
                    help="seconds to wait for a card (default 60)")
    ap.add_argument("--show-identity", action="store_true",
                    help="print the driver name and card number in full "
                         "(personal data — do not paste the output publicly)")
    ap.add_argument("--allow-no-reader", action="store_true",
                    help="treat a missing reader as a warning (for installation)")
    args = ap.parse_args()

    global SHOW_IDENTITY
    SHOW_IDENTITY = args.show_identity

    print("\033[1mTacho service — preflight\033[0m")
    check_python()
    check_pcsc()
    check_reader(args.allow_no_reader)
    check_desktop()
    if args.card:
        check_card(args.timeout)

    fails = [r for r in results if r[1] == FAIL]
    warns = [r for r in results if r[1] == WARN]
    print(f"\n\033[1m{len(results)} checks — "
          f"{len(fails)} failed, {len(warns)} warnings\033[0m")
    for name, _, detail in fails:
        print(f"  \033[91m✗\033[0m {name}: {detail}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
