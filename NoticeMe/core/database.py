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
    GroupInfo,
    GroupCreate,
    GroupUpdate,
    HistoryEntry,
    NotifierInfo,
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

CREATE TABLE IF NOT EXISTS groups (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    enabled     INTEGER DEFAULT 1,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS source_groups (
    source_id TEXT NOT NULL,
    group_id  TEXT NOT NULL,
    PRIMARY KEY (source_id, group_id),
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE,
    FOREIGN KEY (group_id)  REFERENCES groups(id)  ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS group_channels (
    group_id   TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    PRIMARY KEY (group_id, channel_id),
    FOREIGN KEY (group_id)   REFERENCES groups(id)   ON DELETE CASCADE,
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
    sender          TEXT DEFAULT '',
    extra           TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sender_channels (
    sender_id   TEXT NOT NULL,
    channel_id  TEXT NOT NULL,
    PRIMARY KEY (sender_id, channel_id),
    FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE
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
        # Migration: add sender column to history if missing
        _migrate(conn)
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Run lightweight migrations for schema changes."""
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(history)").fetchall()}
        if "sender" not in cols:
            conn.execute("ALTER TABLE history ADD COLUMN sender TEXT DEFAULT ''")
            conn.commit()
    except Exception:
        pass  # Table might not exist yet (fresh DB), schema handles it


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


# ── Groups ─────────────────────────────────────────────────────────────

def _row_to_group(row: sqlite3.Row) -> GroupInfo:
    return GroupInfo(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        enabled=bool(row["enabled"]),
        channel_ids=get_group_channels(row["id"]),
        created_at=row["created_at"],
    )


def list_groups() -> list[GroupInfo]:
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT * FROM groups ORDER BY created_at").fetchall()
        return [_row_to_group(r) for r in rows]
    finally:
        conn.close()


def get_group(group_id: str) -> Optional[GroupInfo]:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        return _row_to_group(row) if row else None
    finally:
        conn.close()


def create_group(data: GroupCreate) -> GroupInfo:
    gid = _new_id()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO groups (id, name, description, enabled) VALUES (?,?,?,?)",
            (gid, data.name, data.description, int(data.enabled)),
        )
        conn.commit()
        return get_group(gid)  # type: ignore
    finally:
        conn.close()


def update_group(group_id: str, data: GroupUpdate) -> Optional[GroupInfo]:
    existing = get_group(group_id)
    if not existing:
        return None
    conn = _get_conn()
    try:
        fields: list[str] = []
        values: list[Any] = []
        if data.name is not None:
            fields.append("name = ?")
            values.append(data.name)
        if data.description is not None:
            fields.append("description = ?")
            values.append(data.description)
        if data.enabled is not None:
            fields.append("enabled = ?")
            values.append(int(data.enabled))
        if not fields:
            return existing
        values.append(group_id)
        conn.execute(f"UPDATE groups SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()
        return get_group(group_id)
    finally:
        conn.close()


def delete_group(group_id: str) -> bool:
    conn = _get_conn()
    try:
        cur = conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── Group ↔ Channel Mappings ───────────────────────────────────────────

def get_group_channels(group_id: str) -> list[str]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT channel_id FROM group_channels WHERE group_id = ?", (group_id,)
        ).fetchall()
        return [r["channel_id"] for r in rows]
    finally:
        conn.close()


def set_group_channels(group_id: str, channel_ids: list[str]) -> None:
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM group_channels WHERE group_id = ?", (group_id,))
        for cid in channel_ids:
            conn.execute(
                "INSERT OR IGNORE INTO group_channels (group_id, channel_id) VALUES (?,?)",
                (group_id, cid),
            )
        conn.commit()
    finally:
        conn.close()


# ── Source ↔ Group Mappings ────────────────────────────────────────────

def get_source_groups(source_id: str) -> list[str]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT group_id FROM source_groups WHERE source_id = ?", (source_id,)
        ).fetchall()
        return [r["group_id"] for r in rows]
    finally:
        conn.close()


def set_source_groups(source_id: str, group_ids: list[str]) -> None:
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM source_groups WHERE source_id = ?", (source_id,))
        for gid in group_ids:
            conn.execute(
                "INSERT OR IGNORE INTO source_groups (source_id, group_id) VALUES (?,?)",
                (source_id, gid),
            )
        conn.commit()
    finally:
        conn.close()


def get_source_channels(source_id: str) -> list[str]:
    """Get all channel IDs for a source via its group mappings."""
    group_ids = get_source_groups(source_id)
    channel_ids: list[str] = []
    for gid in group_ids:
        channel_ids.extend(get_group_channels(gid))
    return list(set(channel_ids))


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
        sender=row["sender"] if "sender" in row.keys() else "",
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
    sender: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO history (notification_id, action, title, content, level, source_id, sender, extra) VALUES (?,?,?,?,?,?,?,?)",
            (notification_id, action, title, content, level, source_id, sender, json.dumps(extra or {})),
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


# ── Notifiers (Sender Channels) ────────────────────────────────────────

def list_notifiers() -> list[NotifierInfo]:
    """List all notifiers: unique senders from history, cross-referenced with sender_channels."""
    conn = _get_conn()
    try:
        # Get all unique senders from history (both from sender column and extra JSON)
        rows = conn.execute(
            "SELECT DISTINCT sender FROM history WHERE sender != '' AND sender IS NOT NULL"
        ).fetchall()
        senders_from_col = {r["sender"] for r in rows}

        # Also extract from extra JSON for backward compat
        extra_rows = conn.execute(
            "SELECT extra FROM history WHERE extra != '{}'"
        ).fetchall()
        senders_from_extra: set[str] = set()
        for r in extra_rows:
            try:
                extra = json.loads(r["extra"])
                sid = extra.get("sender_id", "")
                if sid:
                    senders_from_extra.add(sid)
            except (json.JSONDecodeError, KeyError):
                pass

        all_senders = senders_from_col | senders_from_extra

        # Get configured sender_channels
        sc_rows = conn.execute("SELECT DISTINCT sender_id FROM sender_channels").fetchall()
        configured_senders = {r["sender_id"] for r in sc_rows}

        # Build notifier list
        notifiers: list[NotifierInfo] = []
        for sender_id in sorted(all_senders | configured_senders):
            channel_ids = get_sender_channels(sender_id)
            in_history = sender_id in all_senders
            has_channels = len(channel_ids) > 0

            if in_history and has_channels:
                status = "green"
            elif has_channels and not in_history:
                status = "yellow"
            else:
                status = "gray"

            # Get last notification info
            last_row = conn.execute(
                "SELECT title, content, created_at FROM history WHERE sender = ? ORDER BY id DESC LIMIT 1",
                (sender_id,),
            ).fetchone()

            # Also try extra JSON for last notification
            if not last_row:
                last_row = conn.execute(
                    "SELECT title, content, created_at FROM history WHERE extra LIKE ? ORDER BY id DESC LIMIT 1",
                    (f'%"sender_id": "{sender_id}"%',),
                ).fetchone()

            notifiers.append(NotifierInfo(
                sender_id=sender_id,
                status=status,
                channel_ids=channel_ids,
                last_title=last_row["title"] if last_row else "",
                last_content=last_row["content"] if last_row else "",
                last_time=last_row["created_at"] if last_row else "",
            ))

        return notifiers
    finally:
        conn.close()


def get_sender_channels(sender_id: str) -> list[str]:
    """Get channel IDs mapped to a sender."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT channel_id FROM sender_channels WHERE sender_id = ?", (sender_id,)
        ).fetchall()
        return [r["channel_id"] for r in rows]
    finally:
        conn.close()


def set_sender_channels(sender_id: str, channel_ids: list[str]) -> None:
    """Replace channel mappings for a sender."""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM sender_channels WHERE sender_id = ?", (sender_id,))
        for cid in channel_ids:
            conn.execute(
                "INSERT OR IGNORE INTO sender_channels (sender_id, channel_id) VALUES (?,?)",
                (sender_id, cid),
            )
        conn.commit()
    finally:
        conn.close()
