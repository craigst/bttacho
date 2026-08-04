#!/usr/bin/env python3
"""Create the signed update-manifest.json for a GitHub release asset.

The private Ed25519 key must stay off-repository. Clients only receive the
public key configured in their 0600 Tacho config.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


REPOSITORY = "craigst/bttacho"


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def canonical(value: dict) -> bytes:
    unsigned = {k: v for k, v in value.items() if k != "signature"}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("archive", type=Path, help="release .tar.gz")
    p.add_argument("version", help="semantic version, e.g. 1.0.1")
    p.add_argument("asset_url", help="GitHub Release asset URL")
    p.add_argument("--private-key", type=Path, required=True)
    p.add_argument("--output", type=Path, default=Path("update-manifest.json"))
    p.add_argument("--expires-days", type=int, default=14)
    args = p.parse_args()

    key = serialization.load_pem_private_key(
        args.private_key.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        p.error("private key must be Ed25519 PEM")
    digest = hashlib.sha256(args.archive.read_bytes()).hexdigest()
    payload = {
        "schema": 1,
        "repository": REPOSITORY,
        "version": args.version.lstrip("v"),
        "minimum_version": "0.0.0",
        "artifact_url": args.asset_url,
        "sha256": digest,
        "expires_at": (datetime.now(timezone.utc) +
                        timedelta(days=args.expires_days)).isoformat(),
    }
    payload["signature"] = b64(key.sign(canonical(payload)))
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({
        "manifest": str(args.output),
        "public_key": b64(key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)),
        "sha256": digest,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
