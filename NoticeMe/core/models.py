"""Pydantic models for NoticeMe."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────

class SourceType(str, Enum):
    WEBHOOK = "webhook"
    MQTT = "mqtt"


class ChannelType(str, Enum):
    MQTT = "mqtt"
    API = "api"


class NotificationType(str, Enum):
    REALTIME = "realtime"   # persistent, has ID, editable
    REGULAR = "regular"     # fire-and-forget, no ID


class HistoryAction(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    CLEARED = "cleared"
    PUSHED = "pushed"       # regular notification sent


# ── Source ───────────────────────────────────────────────────────────────

class SourceBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    type: SourceType
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class SourceCreate(SourceBase):
    """Webhook config: {} (auto-generated endpoint)
    MQTT config: {"broker": "host", "port": 1883, "topic": "...", "username": "...", "password": "..."}
    """
    pass


class SourceUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    type: Optional[SourceType] = None
    enabled: Optional[bool] = None
    config: Optional[dict[str, Any]] = None


class SourceInfo(SourceBase):
    id: str
    webhook_path: Optional[str] = None  # auto-generated for webhook sources
    created_at: str


# ── Channel ─────────────────────────────────────────────────────────────

class ChannelBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    type: ChannelType
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class ChannelCreate(ChannelBase):
    """MQTT config: {"broker": "host", "port": 1883, "topic": "...", "username": "...", "password": "..."}
    API config: {"url": "https://...", "method": "POST", "headers": {...}, "body_template": "..."}
    """
    pass


class ChannelUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    type: Optional[ChannelType] = None
    enabled: Optional[bool] = None
    config: Optional[dict[str, Any]] = None


class ChannelInfo(ChannelBase):
    id: str
    created_at: str


# ── Group ──────────────────────────────────────────────────────────────

class GroupBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = ""
    enabled: bool = True


class GroupCreate(GroupBase):
    pass


class GroupUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    enabled: Optional[bool] = None


class GroupInfo(GroupBase):
    id: str
    channel_ids: list[str] = Field(default_factory=list)
    created_at: str


# ── Source ↔ Group Mapping ────────────────────────────────────────────

class SourceGroupMapping(BaseModel):
    source_id: str
    group_id: str


class GroupChannelMapping(BaseModel):
    group_id: str
    channel_ids: list[str]


# ── Notifications ───────────────────────────────────────────────────────

class RealtimeNotification(BaseModel):
    id: str
    title: str
    content: str
    level: str = "info"  # info / warning / error / success
    source_id: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)
    updated_at: str
    created_at: str


class RealtimeUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    level: Optional[str] = None
    extra: Optional[dict[str, Any]] = None


class RegularNotification(BaseModel):
    title: str
    content: str
    level: str = "info"
    source_id: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)


class PushResult(BaseModel):
    ok: bool
    channel: str
    detail: Optional[str] = None


# ── History ─────────────────────────────────────────────────────────────

class HistoryEntry(BaseModel):
    id: int
    notification_id: Optional[str] = None  # None for regular notifications
    action: str
    title: str
    content: str
    level: str
    source_id: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)
    created_at: str


# ── WebSocket Events ───────────────────────────────────────────────────

class WSEvent(BaseModel):
    type: str  # "notification" | "realtime_update" | "realtime_clear" | "ping"
    notification_id: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    level: Optional[str] = None
    extra: Optional[dict[str, Any]] = None
    timestamp: Optional[str] = None
