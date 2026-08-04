"""Configuration -- ~/.config/tacho/config.json, mode 0600.

JSON rather than TOML because the settings dialog must round-trip writes and
stdlib tomllib is read-only. Written atomically so a crash mid-save cannot
truncate the file.
"""

import json
import os
import shutil
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "tacho"
CONFIG_PATH = CONFIG_DIR / "config.json"

DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "tacho"
DEFAULT_DOWNLOAD_DIR = DATA_DIR / "downloads"
OUTBOX_PATH = DATA_DIR / "outbox.sqlite3"

SCHEMA_VERSION = 1


@dataclass
class Destination:
    id: str
    name: str
    type: str = "http"                 # http | postgres
    url: str = ""
    enabled: bool = True
    method: str = "POST"
    headers: Dict[str, str] = field(default_factory=dict)
    auth: Dict[str, str] = field(default_factory=lambda: {"type": "none"})
    timeout_seconds: int = 30
    host: str = ""
    port: int = 5432
    database: str = "postgres"
    username: str = ""
    password: str = ""
    sslmode: str = "prefer"

    def configured(self) -> bool:
        if self.type == "postgres":
            return bool(self.host and self.database and self.username and self.password)
        return bool(self.url)

    def resolved_headers(self) -> Dict[str, str]:
        """Headers actually sent, including auth."""
        h = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "User-Agent": "tacho-service/1.0",
        }
        h.update(self.headers)
        a = self.auth or {}
        kind = a.get("type", "none")
        if kind == "bearer" and a.get("token"):
            h["Authorization"] = f"Bearer {a['token']}"
        elif kind == "basic" and a.get("username"):
            import base64
            raw = f"{a['username']}:{a.get('password', '')}".encode()
            h["Authorization"] = "Basic " + base64.b64encode(raw).decode()
        elif kind == "header" and a.get("name") and a.get("value"):
            h[a["name"]] = a["value"]
        return h


@dataclass
class Config:
    auto_sync: bool = True
    send_window_days: Optional[int] = 14      # None = all data
    preview_trips: int = 7
    download_dir: str = str(DEFAULT_DOWNLOAD_DIR)
    download_retention_days: int = 7
    retry_limit_hours: int = 24
    backoff_ceiling_seconds: int = 1800
    notify_on_failure: bool = True
    notify_on_success: bool = False
    auto_update: bool = True
    update_auto_apply: bool = True
    update_poll_minutes: int = 10
    update_manifest_url: str = "https://raw.githubusercontent.com/craigst/bttacho/main/update-manifest.json"
    # Public Ed25519 key, base64url encoded. It is intentionally empty until
    # the operator provisions the release-signing key for this installation.
    update_public_key: str = ""
    destinations: List[Destination] = field(default_factory=list)

    # ------------------------------------------------------------------- io

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        if not path.exists():
            cfg = cls(destinations=[_placeholder_destination()])
            cfg.save(path)
            return cfg
        try:
            raw = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            # Never silently overwrite a config we failed to understand.
            backup = path.with_suffix(".json.bak")
            try:
                shutil.copy2(path, backup)
            except OSError:
                pass
            return cls(destinations=[_placeholder_destination()])
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Config":
        dests = [Destination(**d) for d in raw.get("destinations", [])]
        known = {f for f in cls.__dataclass_fields__ if f != "destinations"}
        kwargs = {k: v for k, v in raw.items() if k in known}
        return cls(destinations=dests, **kwargs)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["version"] = SCHEMA_VERSION
        return d

    def save(self, path: Path = CONFIG_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2) + "\n")
        os.chmod(tmp, 0o600)          # contains auth tokens
        os.replace(tmp, path)         # atomic

    # --------------------------------------------------------------- helpers

    @property
    def downloads(self) -> Path:
        return Path(self.download_dir).expanduser()

    def enabled_destinations(self) -> List[Destination]:
        return [d for d in self.destinations if d.enabled and d.configured()]


def _placeholder_destination() -> Destination:
    """Direct PostgreSQL is the default, but is inert until provisioned."""
    return Destination(
        id="postgres",
        name="PostgreSQL",
        type="postgres",
        host="",
        database="postgres",
        username="tacho_writer",
        url="",
        enabled=False,
    )
