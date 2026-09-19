"""Core manager — orchestrates sources, channels, notifications, and WebSocket broadcasts."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from . import database as db
from .channels import ChannelManager
from .models import (
    ChannelInfo,
    ChannelUpdate,
    GroupCreate,
    GroupUpdate,
    HistoryEntry,
    PushResult,
    RealtimeNotification,
    RealtimeUpdate,
    SourceCreate,
    SourceInfo,
    SourceUpdate,
    WSEvent,
)
from .sources import SourceManager

logger = logging.getLogger("noticeme")


class NoticeManager:
    """Central manager for the notification system."""

    def __init__(self) -> None:
        self.source_mgr = SourceManager()
        self.channel_mgr = ChannelManager()
        self._ws_subscribers: list[asyncio.Queue[WSEvent]] = []
        # Wire source → routing
        self.source_mgr.on_receive = self._on_source_receive

    async def start(self) -> None:
        """Initialize: start channels, start MQTT sources."""
        db.init_db()
        await self.channel_mgr.start()
        for src in db.list_sources():
            if src.enabled and src.type == "mqtt":
                await self.source_mgr.start_source(src)
        logger.info("NoticeManager started")

    async def stop(self) -> None:
        await self.source_mgr.stop_all()
        await self.channel_mgr.stop()
        logger.info("NoticeManager stopped")

    # ── Source CRUD ─────────────────────────────────────────────────────

    def list_sources(self) -> list[SourceInfo]:
        return db.list_sources()

    def get_source(self, source_id: str) -> Optional[SourceInfo]:
        return db.get_source(source_id)

    async def create_source(self, data: SourceCreate) -> SourceInfo:
        src = db.create_source(data)
        if src.enabled and src.type == "mqtt":
            await self.source_mgr.start_source(src)
        return src

    async def update_source(self, source_id: str, data: SourceUpdate) -> Optional[SourceInfo]:
        # Stop MQTT if running, restart after update
        await self.source_mgr.stop_source(source_id)
        src = db.update_source(source_id, data)
        if src and src.enabled and src.type == "mqtt":
            await self.source_mgr.start_source(src)
        return src

    async def delete_source(self, source_id: str) -> bool:
        await self.source_mgr.stop_source(source_id)
        return db.delete_source(source_id)

    # ── Channel CRUD ───────────────────────────────────────────────────

    def list_channels(self) -> list[ChannelInfo]:
        return db.list_channels()

    def get_channel(self, channel_id: str) -> Optional[ChannelInfo]:
        return db.get_channel(channel_id)

    def create_channel(self, data: ChannelCreate) -> ChannelInfo:
        return db.create_channel(data)

    def update_channel(self, channel_id: str, data: ChannelUpdate) -> Optional[ChannelInfo]:
        return db.update_channel(channel_id, data)

    def delete_channel(self, channel_id: str) -> bool:
        return db.delete_channel(channel_id)

    # ── Group CRUD ─────────────────────────────────────────────────────

    def list_groups(self) -> list:
        return db.list_groups()

    def get_group(self, group_id: str):
        return db.get_group(group_id)

    def create_group(self, data: GroupCreate):
        return db.create_group(data)

    def update_group(self, group_id: str, data: GroupUpdate):
        return db.update_group(group_id, data)

    def delete_group(self, group_id: str) -> bool:
        return db.delete_group(group_id)

    # ── Group ↔ Channel Mappings ───────────────────────────────────────

    def get_group_channels(self, group_id: str) -> list[str]:
        return db.get_group_channels(group_id)

    def set_group_channels(self, group_id: str, channel_ids: list[str]) -> None:
        db.set_group_channels(group_id, channel_ids)

    # ── Source ↔ Group Mappings ────────────────────────────────────────

    def get_source_groups(self, source_id: str) -> list[str]:
        return db.get_source_groups(source_id)

    def set_source_groups(self, source_id: str, group_ids: list[str]) -> None:
        db.set_source_groups(source_id, group_ids)

    # ── Notifications ──────────────────────────────────────────────────

    async def push_realtime(
        self,
        notification_id: str,
        title: str,
        content: str,
        level: str = "info",
        source_id: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
        group_ids: Optional[list[str]] = None,
        sender: str = "",
    ) -> RealtimeNotification:
        """Create or update a real-time notification and broadcast via WebSocket."""
        existing = db.get_realtime(notification_id)
        action = "updated" if existing else "created"

        notif = db.upsert_realtime(notification_id, title, content, level, source_id, extra)

        # Record history
        db.add_history(
            action=action,
            title=title,
            content=content,
            level=level,
            notification_id=notification_id,
            source_id=source_id,
            sender=sender,
            extra=extra,
        )

        # Broadcast to WebSocket clients
        event = WSEvent(
            type="realtime_update",
            notification_id=notification_id,
            title=title,
            content=content,
            level=level,
            extra=extra,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._broadcast_ws(event)

        # Route to channels via groups
        if group_ids:
            await self._push_to_groups(group_ids, title, content, level, extra)
        elif source_id:
            await self._route_to_channels(source_id, title, content, level, extra)

        # Route to sender channels
        if sender:
            sender_ch_ids = db.get_sender_channels(sender)
            if sender_ch_ids:
                await self._push_to_channels(sender_ch_ids, title, content, level, extra)

        return notif

    async def update_realtime(
        self, notification_id: str, data: RealtimeUpdate
    ) -> Optional[RealtimeNotification]:
        """Update fields on an existing real-time notification."""
        existing = db.get_realtime(notification_id)
        if not existing:
            return None

        title = data.title if data.title is not None else existing.title
        content = data.content if data.content is not None else existing.content
        level = data.level if data.level is not None else existing.level
        extra = data.extra if data.extra is not None else existing.extra

        notif = db.upsert_realtime(
            notification_id, title, content, level, existing.source_id, extra
        )

        db.add_history(
            action="updated",
            title=title,
            content=content,
            level=level,
            notification_id=notification_id,
            source_id=existing.source_id,
            extra=extra,
        )

        event = WSEvent(
            type="realtime_update",
            notification_id=notification_id,
            title=title,
            content=content,
            level=level,
            extra=extra,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._broadcast_ws(event)

        return notif

    async def clear_realtime(self, notification_id: str) -> bool:
        """Remove a single real-time notification."""
        existing = db.get_realtime(notification_id)
        if not existing:
            return False

        db.delete_realtime(notification_id)

        db.add_history(
            action="cleared",
            title=existing.title,
            content=existing.content,
            level=existing.level,
            notification_id=notification_id,
            source_id=existing.source_id,
        )

        event = WSEvent(
            type="realtime_clear",
            notification_id=notification_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._broadcast_ws(event)
        return True

    async def clear_all_realtime(self) -> int:
        """Remove all real-time notifications."""
        count = db.clear_realtime()
        db.add_history(action="cleared", title=f"Cleared {count} notifications", content="", level="info")
        event = WSEvent(type="realtime_clear", notification_id="*", timestamp=datetime.now(timezone.utc).isoformat())
        self._broadcast_ws(event)
        return count

    def list_realtime(self) -> list[RealtimeNotification]:
        return db.list_realtime()

    def get_realtime(self, notification_id: str) -> Optional[RealtimeNotification]:
        return db.get_realtime(notification_id)

    async def push_regular(
        self,
        title: str,
        content: str,
        level: str = "info",
        source_id: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
        group_ids: Optional[list[str]] = None,
        sender: str = "",
    ) -> list[PushResult]:
        """Push a regular (fire-and-forget) notification to all mapped channels."""
        # Record in history
        db.add_history(
            action="pushed",
            title=title,
            content=content,
            level=level,
            source_id=source_id,
            sender=sender,
            extra=extra,
        )

        # Broadcast via WebSocket
        event = WSEvent(
            type="notification",
            title=title,
            content=content,
            level=level,
            extra=extra,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._broadcast_ws(event)

        # Route to channels via groups
        results: list[PushResult] = []
        if group_ids:
            results = await self._push_to_groups(group_ids, title, content, level, extra)
        elif source_id:
            results = await self._route_to_channels(source_id, title, content, level, extra)

        # Route to sender channels
        if sender:
            sender_ch_ids = db.get_sender_channels(sender)
            if sender_ch_ids:
                sender_results = await self._push_to_channels(sender_ch_ids, title, content, level, extra)
                results.extend(sender_results)

        return results

    def list_history(self, limit: int = 100, offset: int = 0) -> list[HistoryEntry]:
        return db.list_history(limit, offset)

    def get_notification_history(self, notification_id: str) -> list[HistoryEntry]:
        return db.get_history_for_notification(notification_id)

    # ── WebSocket ───────────────────────────────────────────────────────

    def subscribe_ws(self) -> asyncio.Queue[WSEvent]:
        q: asyncio.Queue[WSEvent] = asyncio.Queue(maxsize=500)
        self._ws_subscribers.append(q)
        return q

    def unsubscribe_ws(self, q: asyncio.Queue[WSEvent]) -> None:
        if q in self._ws_subscribers:
            self._ws_subscribers.remove(q)

    def _broadcast_ws(self, event: WSEvent) -> None:
        for q in self._ws_subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    # ── Notifiers ─────────────────────────────────────────────────────

    def list_notifiers(self) -> list:
        return db.list_notifiers()

    def get_sender_channels(self, sender_id: str) -> list[str]:
        return db.get_sender_channels(sender_id)

    def set_sender_channels(self, sender_id: str, channel_ids: list[str]) -> None:
        db.set_sender_channels(sender_id, channel_ids)

    # ── Internal routing ────────────────────────────────────────────────

    def _on_source_receive(self, source_id: str, payload: dict[str, Any]) -> None:
        """Callback when a source receives data. Schedules routing."""
        title = payload.get("title", "Notification")
        content = payload.get("content", json.dumps(payload, ensure_ascii=False))
        level = payload.get("level", "info")
        notification_id = payload.get("id")
        extra = payload.get("extra", {})
        sender = extra.get("sender_id", "")

        if notification_id:
            # Real-time notification
            asyncio.create_task(
                self.push_realtime(notification_id, title, content, level, source_id, extra, sender=sender)
            )
        else:
            # Regular notification
            asyncio.create_task(
                self.push_regular(title, content, level, source_id, extra, sender=sender)
            )

    async def _push_to_groups(
        self,
        group_ids: list[str],
        title: str,
        content: str,
        level: str,
        extra: Optional[dict[str, Any]],
    ) -> list[PushResult]:
        """Push a notification to all channels in the specified groups."""
        channel_ids: list[str] = []
        for gid in group_ids:
            channel_ids.extend(db.get_group_channels(gid))
        # Deduplicate
        channel_ids = list(set(channel_ids))
        return await self._push_to_channels(channel_ids, title, content, level, extra)

    async def _push_to_channels(
        self,
        channel_ids: list[str],
        title: str,
        content: str,
        level: str,
        extra: Optional[dict[str, Any]],
    ) -> list[PushResult]:
        """Push a notification to specific channels by ID."""
        results: list[PushResult] = []
        for cid in channel_ids:
            ch = db.get_channel(cid)
            if ch and ch.enabled:
                result = await self.channel_mgr.push(ch, title, content, level, extra)
                results.append(result)
        return results

    async def _route_to_channels(
        self,
        source_id: str,
        title: str,
        content: str,
        level: str,
        extra: Optional[dict[str, Any]],
    ) -> list[PushResult]:
        """Route a notification to all channels mapped to a source."""
        channel_ids = db.get_source_channels(source_id)
        if not channel_ids:
            return []

        results: list[PushResult] = []
        for cid in channel_ids:
            ch = db.get_channel(cid)
            if ch and ch.enabled:
                result = await self.channel_mgr.push(ch, title, content, level, extra)
                results.append(result)
        return results


# Singleton
manager = NoticeManager()
