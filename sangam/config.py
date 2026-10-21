"""Runtime settings, read once from environment variables."""
from __future__ import annotations

import base64
import binascii
import logging
import os
import secrets
from pathlib import Path

log = logging.getLogger("sangam")


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _decode_key(raw: str) -> bytes:
    try:
        key = base64.b64decode(raw.strip(), validate=True)
    except binascii.Error as exc:
        raise RuntimeError("Master keys must be base64 encoded") from exc
    if len(key) != 32:
        raise RuntimeError("Master keys must decode to exactly 32 bytes (AES-256)")
    return key


class Settings:
    def __init__(self) -> None:
        self.env = os.environ.get("SANGAM_ENV", "development")
        self.database_url = os.environ.get("SANGAM_DATABASE_URL", "sqlite:///./sangam.db")
        self.base_url = os.environ.get("SANGAM_BASE_URL", "http://localhost:8000").rstrip("/")
        self.allow_signup = _bool("SANGAM_ALLOW_SIGNUP", self.env != "production")
        self.run_workers = _bool("SANGAM_RUN_WORKERS", True)
        # Only trust X-Forwarded-For when running behind your own reverse proxy.
        self.trust_proxy = _bool("SANGAM_TRUST_PROXY", False)
        self.master_keys = self._load_master_keys()

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    def _load_master_keys(self) -> list[bytes]:
        """First key encrypts; every key (current + previous) can decrypt, which allows rotation."""
        keys: list[bytes] = []
        current = os.environ.get("SANGAM_MASTER_KEY")
        if current:
            keys.append(_decode_key(current))
        elif self.env == "production":
            raise RuntimeError("SANGAM_MASTER_KEY is required when SANGAM_ENV=production")
        else:
            path = Path(os.environ.get("SANGAM_DEV_KEY_FILE", ".sangam_dev_master_key"))
            if path.exists():
                keys.append(_decode_key(path.read_text()))
            else:
                key = secrets.token_bytes(32)
                path.write_text(base64.b64encode(key).decode())
                log.warning("Generated a development master key at %s. Never use it in production.", path)
                keys.append(key)
        for raw in os.environ.get("SANGAM_PREVIOUS_MASTER_KEYS", "").split(","):
            if raw.strip():
                keys.append(_decode_key(raw))
        return keys


settings = Settings()
