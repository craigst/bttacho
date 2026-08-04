import base64
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from tacho_service.updates import (ReleaseStore, UpdateError, _canonical,
                                    verify_manifest)


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
