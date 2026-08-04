import base64
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from tacho_core import TripRecord
from tacho_core.report import mileage_gap_km, total_unaccounted_km
from tacho_service.config import Config
from tacho_service.updates import (AUTO_APPLY_DELAY_SECONDS, ReleaseStore,
                                    UpdateError, _canonical, verify_manifest)


def _key_pair():
    private = Ed25519PrivateKey.generate()
    public = base64.urlsafe_b64encode(
        private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode().rstrip("=")
    return private, public


def _archive():
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        body = b'__version__ = "1.0.1"\n'
        info = tarfile.TarInfo("tacho_service/__init__.py")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    return out.getvalue()


class UpdateTests(unittest.TestCase):
    def test_trusted_card_uses_hash_only(self):
        card_number = "GB 1234 5678 9012 3456"
        fingerprint = Config.card_hash(card_number)
        self.assertEqual(len(fingerprint), 64)
        self.assertNotIn(card_number, fingerprint)
        config = Config(trusted_card_hash=fingerprint)
        self.assertTrue(config.card_is_trusted(card_number))
        self.assertFalse(config.card_is_trusted("GB 0000 0000 0000 0000"))

    def test_trust_card_enrollment_round_trips_and_masks_identifier(self):
        config = Config()
        card_number = "DB 0719 2162 0387 02"
        fingerprint = config.trust_card(card_number)
        self.assertTrue(config.card_is_trusted("DB07192162038702"))
        self.assertEqual(Config.masked_card_number(card_number), "••••8702")
        self.assertEqual(len(fingerprint), 64)

    def test_legacy_prefixed_or_uppercase_hash_remains_trusted(self):
        card_number = "DB07192162038702"
        config = Config(trusted_card_hash=" SHA256:" + Config.card_hash(card_number).upper())
        self.assertTrue(config.card_is_trusted("DB 0719 2162 0387 02"))

    def test_empty_trust_policy_does_not_trust_any_card(self):
        config = Config()
        self.assertFalse(config.card_is_trusted("DB07192162038702"))

    def test_mileage_gap_is_previous_truck_odometer_advance(self):
        older = TripRecord("2026-08-01", "Friday", "AB12", "06:00", "10:00",
                           1000, 1200, 200, 4.0)
        newer = TripRecord("2026-08-03", "Sunday", "AB12", "06:00", "10:00",
                           1300, 1500, 200, 4.0)
        other_truck = TripRecord("2026-08-02", "Saturday", "XY99", "06:00", "10:00",
                                 500, 600, 100, 4.0)
        trips = [newer, other_truck, older]
        self.assertEqual(mileage_gap_km(trips, newer), 100)
        self.assertIsNone(mileage_gap_km(trips, other_truck))
        self.assertEqual(total_unaccounted_km(trips), 100)

    def test_auto_apply_delay_is_visible_and_bounded(self):
        self.assertEqual(AUTO_APPLY_DELAY_SECONDS, 30)
        self.assertGreaterEqual(AUTO_APPLY_DELAY_SECONDS, 5)

    def test_countdown_emits_version_and_applies_after_delay(self):
        from tacho_service.updates import UpdateManager

        states = []
        manager = UpdateManager(config_fn=lambda: object(), idle_fn=lambda: True,
                                on_state=lambda state, detail: states.append((state, detail)))
        manager.apply_staged = lambda: states.append(("APPLY", "called"))
        with patch("tacho_service.updates.AUTO_APPLY_DELAY_SECONDS", 1):
            manager._countdown("9.9.9")
        self.assertEqual(states[0], ("COUNTDOWN", "v9.9.9 auto-update in 1s"))
        self.assertEqual(states[-1], ("APPLY", "called"))

    def test_signed_manifest_and_release_rollback(self):
        private, public = _key_pair()
        archive = _archive()
        raw = {
            "schema": 1,
            "repository": "craigst/bttacho",
            "version": "1.0.1",
            "minimum_version": "0.0.0",
            "artifact_url": "https://github.com/craigst/bttacho/releases/download/v1.0.1/tacho.tar.gz",
            "sha256": hashlib.sha256(archive).hexdigest(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        }
        raw["signature"] = base64.urlsafe_b64encode(
            private.sign(_canonical(raw))).decode().rstrip("=")
        manifest = verify_manifest(raw, public)

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            (source / "tacho_service").mkdir(parents=True)
            (source / "tacho_service" / "__init__.py").write_text(
                '__version__ = "1.0.0"\n')
            store = ReleaseStore(source, Path(tmp) / "releases")
            staged = store.stage_archive(manifest, archive)
            store.activate(staged, "1.0.0")
            self.assertEqual(store.current.resolve(), staged.resolve())
            self.assertTrue(store.pending.exists())
            store.rollback()
            self.assertEqual(store.current.resolve(), source.resolve())
            self.assertFalse(store.pending.exists())

    def test_bad_signature_and_digest_are_rejected(self):
        _, public = _key_pair()
        raw = {
            "repository": "craigst/bttacho",
            "version": "1.0.1",
            "artifact_url": "https://github.com/craigst/bttacho/releases/download/v1.0.1/tacho.tar.gz",
            "sha256": "0" * 64,
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "signature": "invalid",
        }
        with self.assertRaises(UpdateError):
            verify_manifest(raw, public)

    def test_archive_path_traversal_is_rejected(self):
        private, public = _key_pair()
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode="w:gz") as tar:
            body = b"bad"
            info = tarfile.TarInfo("../outside")
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
        archive = out.getvalue()
        raw = {
            "repository": "craigst/bttacho",
            "version": "1.0.1",
            "artifact_url": "https://github.com/craigst/bttacho/releases/download/v1.0.1/tacho.tar.gz",
            "sha256": hashlib.sha256(archive).hexdigest(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        }
        raw["signature"] = base64.urlsafe_b64encode(
            private.sign(_canonical(raw))).decode().rstrip("=")
        manifest = verify_manifest(raw, public)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(UpdateError):
                ReleaseStore(Path(tmp), Path(tmp) / "releases").stage_archive(
                    manifest, archive)


if __name__ == "__main__":
    unittest.main()
