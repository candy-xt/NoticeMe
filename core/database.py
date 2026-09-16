"""SQLite database layer for NoticeMe."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .models import (
    ChannelInfo,
    ChannelCreate,
    ChannelUpdate,
    HistoryEntry,
    RealtimeNotification,
    SourceInfo,
    SourceCreate,
    SourceUpdate,
)

DATA_DIR = Path.home() / ".noticeme"
DB_PATH = DATA_DIR / "notice.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    type         TEXT NOT NULL,
    enabled      INTEGER DEFAULT 1,
    config       TEXT DEFAULT '{}',
    webhook_path TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS channels (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    type       TEXT NOT NULL,
    enabled    INTEGER DEFAULT 1,
    config     TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS source_channels (
    source_id  TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    PRIMARY KEY (source_id, channel_id),
    FOREIGN KEY (source_id)  REFERENCES sources(id)  ON DELETE CASCADE,
    FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS realtime_notifications (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    content    TEXT NOT NULL,
    level      TEXT DEFAULT 'info',
    source_id  TEXT,
    extra      TEXT DEFAULT '{}',
    updated_at TEXT DEFAULT (datetime('now')),
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_id TEXT,
    action          TEXT NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    level           TEXT DEFAULT 'info',
    source_id       TEXT,
    extra           TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT (datetime('now'))
);
"""


def _get_conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = _get_conn()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ── Helpers ─────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


# ── Sources ─────────────────────────────────────────────────────────────

def _row_to_source(row: sqlite3.Row) -> SourceInfo:
    return SourceInfo(
        id=row["id"],
        name=row["name"],
        type=row["type"],
        enabled=bool(row["enabled"]),
        config=json.loads(row["config"]),
        webhook_path=row["webhook_path"],
        created_at=row["created_at"],
    )


def list_sources() -> list[SourceInfo]:
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT * FROM sources ORDER BY created_at").fetchall()
        return [_row_to_source(r) for r in rows]
    finally:
        conn.close()


def get_source(source_id: str) -> Optional[SourceInfo]:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return _row_to_source(row) if row else None
    finally:
        conn.close()


def get_source_by_webhook_path(path: str) -> Optional[SourceInfo]:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM sources WHERE webhook_path = ?", (path,)).fetchone()
        return _row_to_source(row) if row else None
    finally:
        conn.close()


def create_source(data: SourceCreate) -> SourceInfo:
    sid = _new_id()
    webhook_path = f"/hook/{sid}" if data.type == "webhook" else None
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO sources (id, name, type, enabled, config, webhook_path) VALUES (?,?,?,?,?,?)",
            (sid, data.name, data.type.value, int(data.enabled), json.dumps(data.config), webhook_path),
        )
        conn.commit()
        return get_source(sid)  # type: ignore
    finally:
        conn.close()


def update_source(source_id: str, data: SourceUpdate) -> Optional[SourceInfo]:
    existing = get_source(source_id)
    if not existing:
        return None
    conn = _get_conn()
    try:
        fields: list[str] = []
        values: list[Any] = []
        if data.name is not None:
            fields.append("name = ?")
            values.append(data.name)
        if data.type is not None:
            fields.append("type = ?")
            values.append(data.type.value)
        if data.enabled is not None:
            fields.append("enabled = ?")
            values.append(int(data.enabled))
        if data.config is not None:
            fields.append("config = ?")
            values.append(json.dumps(data.config))
        if not fields:
            return existing
        values.append(source_id)
        conn.execute(f"UPDATE sources SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()
        return get_source(source_id)
    finally:
        conn.close()


def delete_source(source_id: str) -> bool:
    conn = _get_conn()
    try:
        cur = conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── Channels ────────────────────────────────────────────────────────────

def _row_to_channel(row: sqlite3.Row) -> ChannelInfo:
    return ChannelInfo(
        id=row["id"],
        name=row["name"],
        type=row["type"],
        enabled=bool(row["enabled"]),
        config=json.loads(row["config"]),
        created_at=row["created_at"],
    )


def list_channels() -> list[ChannelInfo]:
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT * FROM channels ORDER BY created_at").fetchall()
        return [_row_to_channel(r) for r in rows]
    finally:
        conn.close()


def get_channel(channel_id: str) -> Optional[ChannelInfo]:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
        return _row_to_channel(row) if row else None
    finally:
        conn.close()


def create_channel(data: ChannelCreate) -> ChannelInfo:
    cid = _new_id()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO channels (id, name, type, enabled, config) VALUES (?,?,?,?,?)",
            (cid, data.name, data.type.value, int(data.enabled), json.dumps(data.config)),
        )
        conn.commit()
        return get_channel(cid)  # type: ignore
    finally:
        conn.close()


def update_channel(channel_id: str, data: ChannelUpdate) -> Optional[ChannelInfo]:
    existing = get_channel(channel_id)
    if not existing:
        return None
    conn = _get_conn()
    try:
        fields: list[str] = []
        values: list[Any] = []
        if data.name is not None:
            fields.append("name = ?")
            values.append(data.name)
        if data.type is not None:
            fields.append("type = ?")
            values.append(data.type.value)
        if data.enabled is not None:
            fields.append("enabled = ?")
            values.append(int(data.enabled))
        if data.config is not None:
            fields.append("config = ?")
            values.append(json.dumps(data.config))
        if not fields:
            return existing
        values.append(channel_id)
        conn.execute(f"UPDATE channels SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()
        return get_channel(channel_id)
    finally:
        conn.close()


def delete_channel(channel_id: str) -> bool:
    conn = _get_conn()
    try:
        cur = conn.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── Source ↔ Channel Mappings ───────────────────────────────────────────

def get_source_channels(source_id: str) -> list[str]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT channel_id FROM source_channels WHERE source_id = ?", (source_id,)
        ).fetchall()
        return [r["channel_id"] for r in rows]
    finally:
        conn.close()


def set_source_channels(source_id: str, channel_ids: list[str]) -> None:
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM source_channels WHERE source_id = ?", (source_id,))
        for cid in channel_ids:
            conn.execute(
                "INSERT OR IGNORE INTO source_channels (source_id, channel_id) VALUES (?,?)",
                (source_id, cid),
            )
        conn.commit()
    finally:
        conn.close()


def get_all_mappings() -> dict[str, list[str]]:
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT source_id, channel_id FROM source_channels").fetchall()
        result: dict[str, list[str]] = {}
        for r in rows:
            result.setdefault(r["source_id"], []).append(r["channel_id"])
        return result
    finally:
        conn.close()


# ── Real-time Notifications ────────────────────────────────────────────

def _row_to_realtime(row: sqlite3.Row) -> RealtimeNotification:
    return RealtimeNotification(
        id=row["id"],
        title=row["title"],
        content=row["content"],
        level=row["level"],
        source_id=row["source_id"],
        extra=json.loads(row["extra"]),
        updated_at=row["updated_at"],
        created_at=row["created_at"],
    )


def list_realtime() -> list[RealtimeNotification]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM realtime_notifications ORDER BY updated_at DESC"
        ).fetchall()
        return [_row_to_realtime(r) for r in rows]
    finally:
        conn.close()


def get_realtime(notification_id: str) -> Optional[RealtimeNotification]:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM realtime_notifications WHERE id = ?", (notification_id,)
        ).fetchone()
        return _row_to_realtime(row) if row else None
    finally:
        conn.close()


def upsert_realtime(
    notification_id: str,
    title: str,
    content: str,
    level: str = "info",
    source_id: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> RealtimeNotification:
    """Create or update a real-time notification."""
    now = _now()
    extra_json = json.dumps(extra or {})
    conn = _get_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM realtime_notifications WHERE id = ?", (notification_id,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE realtime_notifications SET title=?, content=?, level=?, source_id=?, extra=?, updated_at=? WHERE id=?",
                (title, content, level, source_id, extra_json, now, notification_id),
            )
        else:
            conn.execute(
                "INSERT INTO realtime_notifications (id, title, content, level, source_id, extra, updated_at, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (notification_id, title, content, level, source_id, extra_json, now, now),
            )
        conn.commit()
        return get_realtime(notification_id)  # type: ignore
    finally:
        conn.close()


def delete_realtime(notification_id: str) -> bool:
    conn = _get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM realtime_notifications WHERE id = ?", (notification_id,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def clear_realtime() -> int:
    conn = _get_conn()
    try:
        cur = conn.execute("DELETE FROM realtime_notifications")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ── History ─────────────────────────────────────────────────────────────

def _row_to_history(row: sqlite3.Row) -> HistoryEntry:
    return HistoryEntry(
        id=row["id"],
        notification_id=row["notification_id"],
        action=row["action"],
        title=row["title"],
        content=row["content"],
        level=row["level"],
        source_id=row["source_id"],
        extra=json.loads(row["extra"]),
        created_at=row["created_at"],
    )


def add_history(
    action: str,
    title: str,
    content: str,
    level: str = "info",
    notification_id: Optional[str] = None,
    source_id: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO history (notification_id, action, title, content, level, source_id, extra) VALUES (?,?,?,?,?,?,?)",
            (notification_id, action, title, content, level, source_id, json.dumps(extra or {})),
        )
        conn.commit()
    finally:
        conn.close()


def list_history(limit: int = 100, offset: int = 0) -> list[HistoryEntry]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM history ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        return [_row_to_history(r) for r in rows]
    finally:
        conn.close()


def get_history_for_notification(notification_id: str) -> list[HistoryEntry]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM history WHERE notification_id = ? ORDER BY id DESC",
            (notification_id,),
        ).fetchall()
        return [_row_to_history(r) for r in rows]
    finally:
        conn.close()
