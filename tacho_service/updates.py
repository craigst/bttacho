"""Signed GitHub release updates for the tray service.

Updates are deliberately separate from card delivery.  This module downloads
and verifies a release in a worker thread, stages it in the XDG data directory,
and only activates it when the caller says the service is idle.  Configuration,
credentials, the outbox, downloads, and card policy live outside a release.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.error
import urllib.request
from urllib.parse import urlparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .config import DATA_DIR

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:  # pragma: no cover - exercised by install/preflight
    InvalidSignature = ValueError
    Ed25519PublicKey = None

log = logging.getLogger(__name__)

REPOSITORY = "craigst/bttacho"
DEFAULT_MANIFEST_URL = (
    "https://raw.githubusercontent.com/craigst/bttacho/main/update-manifest.json"
)
DEFAULT_POLL_SECONDS = 600
AUTO_APPLY_DELAY_SECONDS = 30
MAX_RELEASE_BYTES = 100 * 1024 * 1024
VERSION_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+].*)?$")
GITHUB_HOSTS = {"github.com", "objects.githubusercontent.com",
                "raw.githubusercontent.com"}


class UpdateError(Exception):
    """A release was unavailable or failed verification."""


def _version(value: str) -> tuple[int, int, int]:
    match = VERSION_RE.match(str(value).strip())
    if not match:
        raise UpdateError(f"invalid release version: {value!r}")
    return tuple(int(part or 0) for part in match.groups())


def _canonical(value: dict) -> bytes:
    """Canonical bytes signed by the release publisher."""
    unsigned = {k: v for k, v in value.items() if k != "signature"}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _decode_b64(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise UpdateError("invalid base64 signature/key") from exc


@dataclass(frozen=True)
class Manifest:
    repository: str
    version: str
    artifact_url: str
    sha256: str
    signature: str
    expires_at: datetime
    minimum_version: str = "0.0.0"

    @classmethod
    def from_dict(cls, raw: dict) -> "Manifest":
        required = ("repository", "version", "artifact_url", "sha256",
                    "signature", "expires_at")
        missing = [name for name in required if not raw.get(name)]
        if missing:
            raise UpdateError("manifest missing: " + ", ".join(missing))
        if raw["repository"] != REPOSITORY:
            raise UpdateError("manifest repository does not match this app")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", raw["sha256"]):
            raise UpdateError("manifest has invalid SHA-256")
        try:
            expires = datetime.fromisoformat(str(raw["expires_at"]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise UpdateError("manifest has invalid expiry") from exc
        if expires.tzinfo is None:
            raise UpdateError("manifest expiry must include a timezone")
        minimum_version = raw.get("minimum_version", "0.0.0")
        if _version(minimum_version) > _version(raw["version"]):
            raise UpdateError("manifest minimum version exceeds release version")
        parsed_url = urlparse(str(raw["artifact_url"]))
        if parsed_url.scheme != "https" or parsed_url.hostname not in GITHUB_HOSTS:
            raise UpdateError("release artifact must be a GitHub HTTPS URL")
        return cls(
            repository=raw["repository"], version=raw["version"],
            artifact_url=raw["artifact_url"], sha256=raw["sha256"].lower(),
            signature=raw["signature"], expires_at=expires,
            minimum_version=raw.get("minimum_version", "0.0.0"),
        )


def verify_manifest(raw: dict, public_key: str, now: Optional[datetime] = None) -> Manifest:
    """Verify an Ed25519-signed manifest before trusting any release URL."""
    if Ed25519PublicKey is None:
        raise UpdateError("cryptography is not installed")
    manifest = Manifest.from_dict(raw)
    now = now or datetime.now(timezone.utc)
    if manifest.expires_at.astimezone(timezone.utc) <= now.astimezone(timezone.utc):
        raise UpdateError("manifest has expired")
    if not public_key:
        raise UpdateError("update verification key is not configured")
    try:
        key = Ed25519PublicKey.from_public_bytes(_decode_b64(public_key))
        key.verify(_decode_b64(manifest.signature), _canonical(raw))
    except (ValueError, InvalidSignature) as exc:
        raise UpdateError("manifest signature is invalid") from exc
    return manifest


def _safe_member(root: Path, member: tarfile.TarInfo) -> Path:
    if member.issym() or member.islnk():
        raise UpdateError("release archive may not contain links")
    target = (root / member.name).resolve()
    if target != root and root not in target.parents:
        raise UpdateError("release archive contains a path traversal")
    return target


class ReleaseStore:
    """Stage and atomically switch release directories via a symlink."""

    def __init__(self, source_root: Optional[Path] = None, root: Path = DATA_DIR / "releases"):
        self.source_root = (source_root or Path(__file__).resolve().parent.parent).resolve()
        self.root = root
        self.current = root / "current"
        self.pending = root / "pending.json"
        self.root.mkdir(parents=True, exist_ok=True)

    def _ensure_base(self, current_version: str) -> None:
        if self.current.exists() or self.current.is_symlink():
            return
        base = self.root / f"base-{_version(current_version)[0]}-{_version(current_version)[1]}-{_version(current_version)[2]}"
        if not base.exists() and not base.is_symlink():
            base.symlink_to(self.source_root, target_is_directory=True)
        tmp = self.root / ".current.base.tmp"
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        tmp.symlink_to(base, target_is_directory=True)
        os.replace(tmp, self.current)

    def stage_archive(self, manifest: Manifest, archive: bytes) -> Path:
        digest = hashlib.sha256(archive).hexdigest()
        if digest != manifest.sha256:
            raise UpdateError("release SHA-256 does not match manifest")
        version_dir = self.root / f"v{manifest.version.lstrip('v')}"
        if version_dir.exists():
            return version_dir
        tmp = Path(tempfile.mkdtemp(prefix=".release-", dir=self.root))
        try:
            archive_path = tmp / "release.tar.gz"
            archive_path.write_bytes(archive)
            with tarfile.open(archive_path, "r:gz") as tar:
                members = tar.getmembers()
                if not members or len(members) > 10000:
                    raise UpdateError("release archive is empty or too large")
                for member in members:
                    if member.size < 0 or member.size > MAX_RELEASE_BYTES:
                        raise UpdateError("release archive contains an oversized file")
                    _safe_member(tmp, member)
                tar.extractall(tmp, filter="data")
            # A release is a git-archive-style source tree, not an executable.
            candidate = tmp / "tacho_service"
            if not (candidate / "__init__.py").is_file():
                nested = [p for p in tmp.iterdir() if p.is_dir() and
                          (p / "tacho_service" / "__init__.py").is_file()]
                if len(nested) != 1:
                    raise UpdateError("release has no tacho_service package")
                candidate = nested[0]
            final_tmp = self.root / f".v{manifest.version.lstrip('v')}.tmp"
            shutil.rmtree(final_tmp, ignore_errors=True)
            shutil.copytree(candidate.parent, final_tmp)
            os.replace(final_tmp, version_dir)
            return version_dir
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def activate(self, version_dir: Path, current_version: str) -> None:
        self._ensure_base(current_version)
        previous = os.path.realpath(self.current)
        tmp = self.root / ".current.new.tmp"
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        tmp.symlink_to(version_dir, target_is_directory=True)
        os.replace(tmp, self.current)
        self.pending.write_text(json.dumps({
            "previous": previous, "active": str(version_dir),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }) + "\n")

    def mark_validated(self) -> None:
        self.pending.unlink(missing_ok=True)

    def rollback(self) -> None:
        if not self.pending.exists():
            raise UpdateError("no pending release to roll back")
        state = json.loads(self.pending.read_text())
        previous = Path(state["previous"])
        if not previous.exists() and not previous.is_symlink():
            raise UpdateError("previous release is missing")
        tmp = self.root / ".current.rollback.tmp"
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        tmp.symlink_to(previous, target_is_directory=True)
        os.replace(tmp, self.current)
        self.pending.unlink(missing_ok=True)


class UpdateManager:
    """Background checker; callbacks are invoked from its worker thread."""

    def __init__(self, *, config_fn: Callable[[], object], idle_fn: Callable[[], bool],
                 authorized_fn: Optional[Callable[[], bool]] = None,
                 on_state: Optional[Callable[[str, str], None]] = None):
        self._config = config_fn
        self._idle = idle_fn
        self._authorized = authorized_fn or (lambda: False)
        self._on_state = on_state
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._staged: Optional[tuple[Manifest, Path]] = None
        self._store = ReleaseStore()
        self._etag_path = DATA_DIR / "update-manifest.etag"
        self._manual_check = threading.Event()

    def notify_card_authorized(self):
        """Wake the worker so a staged update can use the approved card."""
        self._wake.set()

    def _state(self, state: str, detail: str = ""):
        if self._on_state:
            self._on_state(state, detail)
        log.info("update %s%s", state, f": {detail}" if detail else "")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="updates", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2)

    def check_now(self, *_args):
        """Request a manual check even when background checks are disabled."""
        self._manual_check.set()
        self._wake.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                cfg = self._config()
                if (self._staged and getattr(cfg, "update_auto_apply", True)
                        and self._authorized() and self._idle()):
                    self._countdown(self._staged[0].version)
                forced = self._manual_check.is_set()
                self._manual_check.clear()
                self.check_once(force=forced)
            except Exception as exc:  # update failures must never stop card I/O
                self._state("CHECK_FAILED", str(exc))
            minutes = max(5, min(15, int(getattr(self._config(), "update_poll_minutes", 10))))
            self._wake.wait(minutes * 60)
            self._wake.clear()

    def check_once(self, force: bool = False):
        cfg = self._config()
        if not getattr(cfg, "auto_update", True) and not force:
            self._state("DISABLED", "automatic updates are off")
            return
        key = getattr(cfg, "update_public_key", "")
        if not key:
            self._state("DISABLED", "verification key is not configured")
            return
        url = getattr(cfg, "update_manifest_url", DEFAULT_MANIFEST_URL)
        self._state("CHECKING", "GitHub Releases")
        headers = {"Accept": "application/json"}
        if self._etag_path.exists():
            headers["If-None-Match"] = self._etag_path.read_text().strip()
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                final_host = urlparse(response.geturl()).hostname
                if final_host not in GITHUB_HOSTS:
                    raise UpdateError("manifest redirected away from GitHub")
                etag = response.headers.get("ETag")
                if etag:
                    self._etag_path.write_text(etag)
                raw = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                self._state("CURRENT", "manifest unchanged")
                return
            raise UpdateError(f"manifest unavailable: HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise UpdateError(f"manifest unavailable: {exc}") from exc
        manifest = verify_manifest(raw, key)
        from . import __version__
        if _version(__version__) < _version(manifest.minimum_version):
            raise UpdateError("release requires a newer installed updater")
        if _version(manifest.version) <= _version(__version__):
            self._state("CURRENT", __version__)
            return
        if not self._idle():
            self._state("DEFERRED", "card or delivery is active")
            return
        self._state("AVAILABLE", f"v{manifest.version}")
        self._state("DOWNLOADING", f"v{manifest.version}")
        with urllib.request.urlopen(urllib.request.Request(manifest.artifact_url),
                                    timeout=30) as response:
            if urlparse(response.geturl()).hostname not in GITHUB_HOSTS:
                raise UpdateError("release redirected away from GitHub")
            archive = response.read(MAX_RELEASE_BYTES + 1)
        if len(archive) > MAX_RELEASE_BYTES:
            raise UpdateError("release artifact is too large")
        version_dir = self._store.stage_archive(manifest, archive)
        self._staged = (manifest, version_dir)
        self._state("VERIFIED", f"v{manifest.version}")
        if (getattr(cfg, "update_auto_apply", True) and self._idle()
                and self._authorized()):
            self._countdown(manifest.version)
        else:
            self._state("STAGED", f"v{manifest.version} waiting for approved card")

    def _countdown(self, version: str) -> None:
        """Give the operator a visible cancellation window before restart."""
        for remaining in range(AUTO_APPLY_DELAY_SECONDS, 0, -1):
            if self._stop.is_set():
                return
            if not self._idle():
                self._state("DEFERRED", "card or delivery became active")
                return
            self._state("COUNTDOWN", f"v{version} auto-update in {remaining}s")
            self._wake.wait(1)
            self._wake.clear()
        if self._idle():
            self.apply_staged()
        else:
            self._state("DEFERRED", "card or delivery became active")

    def apply_staged(self):
        if not self._staged:
            return False
        if not self._idle():
            self._state("DEFERRED", "waiting for an idle service")
            return False
        from . import __version__
        manifest, version_dir = self._staged
        self._state("APPLYING", f"v{manifest.version}")
        self._store.activate(version_dir, __version__)
        self._write_service_unit()
        self._state("APPLIED", f"v{manifest.version}; restarting service")
        self._restart_service()
        return True

    def _unit_path(self) -> Path:
        return (Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
                / "systemd/user/tacho.service")

    def _write_service_unit(self):
        """Point the user unit at the atomic release pointer."""
        unit = self._unit_path()
        unit.parent.mkdir(parents=True, exist_ok=True)
        python = os.path.abspath(sys.executable)
        current = self._store.current
        unit.write_text(
            "[Unit]\n"
            "Description=Tacho driver card service\n"
            "Documentation=https://github.com/craigst/bttacho\n"
            "PartOf=graphical-session.target\n"
            "After=graphical-session.target\n\n"
            "[Service]\nType=simple\n"
            f"WorkingDirectory={current}\n"
            f"ExecStart={python} -m tacho_service\n"
            "Restart=on-failure\nRestartSec=5\n\n"
            "[Install]\nWantedBy=graphical-session.target\n")
        os.chmod(unit, 0o600)
        subprocess.run(["systemctl", "--user", "daemon-reload"],
                       check=False, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)

    @staticmethod
    def _restart_service():
        try:
            subprocess.Popen(
                ["systemctl", "--user", "restart", "tacho.service"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            log.exception("could not request tacho.service restart")

    def validate_or_rollback(self, sql_online: Optional[bool]) -> bool:
        """Called by the service after startup health has had a chance to settle."""
        if not self._store.pending.exists():
            return True
        if sql_online is False:
            self._store.rollback()
            self._state("ROLLED_BACK", "PostgreSQL health check failed")
            self._write_service_unit()
            self._restart_service()
            return False
        cfg = self._config()
        postgres_configured = any(
            getattr(dest, "type", "") == "postgres" and
            getattr(dest, "enabled", False) and dest.configured()
            for dest in getattr(cfg, "destinations", [])
        )
        if sql_online is True or (sql_online is None and not postgres_configured):
            self._store.mark_validated()
            self._state("VALIDATED", "startup health checks passed")
            return True
        return True
