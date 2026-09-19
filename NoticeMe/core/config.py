"""Configuration management for NoticeMe — token, settings stored in ~/.noticeme/config.json."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

DATA_DIR = Path.home() / ".noticeme"
CONFIG_PATH = DATA_DIR / "config.json"


def _ensure_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    """Load config from disk. Creates default with random token if missing."""
    _ensure_dir()
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    # Generate default config
    cfg = {"token": secrets.token_urlsafe(32)}
    _save_config(cfg)
    return cfg


def _save_config(cfg: dict[str, Any]) -> None:
    _ensure_dir()
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def get_token() -> str:
    """Get the current API token."""
    return load_config().get("token", "")


def regenerate_token() -> str:
    """Generate and save a new token. Returns the new token."""
    cfg = load_config()
    cfg["token"] = secrets.token_urlsafe(32)
    _save_config(cfg)
    return cfg["token"]


def set_token(token: str) -> None:
    """Set a specific token."""
    cfg = load_config()
    cfg["token"] = token
    _save_config(cfg)


def verify_token(token: str) -> bool:
    """Verify a token matches the stored token."""
    expected = get_token()
    if not expected:
        return False
    return secrets.compare_digest(token, expected)
