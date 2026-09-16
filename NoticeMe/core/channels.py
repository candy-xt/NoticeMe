"""Notification channels — MQTT publish and API request."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

import httpx

from .models import ChannelInfo, ChannelType, PushResult

logger = logging.getLogger("noticeme.channels")


class ChannelManager:
    """Dispatches notifications to external channels."""

    def __init__(self) -> None:
        self._mqtt_clients: dict[str, Any] = {}
        self._http: Optional[httpx.AsyncClient] = None

    async def start(self) -> None:
        self._http = httpx.AsyncClient(timeout=15.0)

    async def stop(self) -> None:
        for cid, client in self._mqtt_clients.items():
            try:
                await client.disconnect()
            except Exception:
                pass
        self._mqtt_clients.clear()
        if self._http:
            await self._http.aclose()
            self._http = None

    async def push(
        self,
        channel: ChannelInfo,
        title: str,
        content: str,
        level: str = "info",
        extra: Optional[dict[str, Any]] = None,
    ) -> PushResult:
        """Push a notification to a channel."""
        if not channel.enabled:
            return PushResult(ok=False, channel=channel.name, detail="Channel disabled")

        try:
            if channel.type == ChannelType.MQTT:
                return await self._push_mqtt(channel, title, content, level, extra)
            elif channel.type == ChannelType.API:
                return await self._push_api(channel, title, content, level, extra)
            else:
                return PushResult(ok=False, channel=channel.name, detail="Unknown channel type")
        except Exception as e:
            logger.error("Channel %s push failed: %s", channel.name, e)
            return PushResult(ok=False, channel=channel.name, detail=str(e))

    async def _push_mqtt(
        self,
        channel: ChannelInfo,
        title: str,
        content: str,
        level: str,
        extra: Optional[dict[str, Any]],
    ) -> PushResult:
        import aiomqtt

        cfg = channel.config
        broker = cfg.get("broker", "localhost")
        port = int(cfg.get("port", 1883))
        topic = cfg.get("topic", "noticeme/out")
        username = cfg.get("username")
        password = cfg.get("password")

        payload = json.dumps({
            "title": title,
            "content": content,
            "level": level,
            "extra": extra or {},
        }, ensure_ascii=False)

        try:
            async with aiomqtt.Client(
                broker,
                port=port,
                username=username or None,
                password=password or None,
            ) as client:
                await client.publish(topic, payload.encode(), qos=1)
            return PushResult(ok=True, channel=channel.name)
        except Exception as e:
            return PushResult(ok=False, channel=channel.name, detail=str(e))

    async def _push_api(
        self,
        channel: ChannelInfo,
        title: str,
        content: str,
        level: str,
        extra: Optional[dict[str, Any]],
    ) -> PushResult:
        if not self._http:
            return PushResult(ok=False, channel=channel.name, detail="HTTP client not initialized")

        cfg = channel.config
        url = cfg.get("url")
        if not url:
            return PushResult(ok=False, channel=channel.name, detail="No URL configured")

        method = cfg.get("method", "POST").upper()
        headers = cfg.get("headers", {})
        body_template = cfg.get("body_template")

        if body_template:
            # Simple template substitution
            body_str = body_template.replace("{{title}}", title)
            body_str = body_str.replace("{{content}}", content)
            body_str = body_str.replace("{{level}}", level)
            try:
                body = json.loads(body_str)
            except json.JSONDecodeError:
                body = body_str
        else:
            body = {
                "title": title,
                "content": content,
                "level": level,
                "extra": extra or {},
            }

        try:
            resp = await self._http.request(
                method, url, headers=headers, json=body if isinstance(body, dict) else None, content=body if isinstance(body, str) else None,
            )
            ok = 200 <= resp.status_code < 300
            return PushResult(ok=ok, channel=channel.name, detail=f"HTTP {resp.status_code}" if not ok else None)
        except Exception as e:
            return PushResult(ok=False, channel=channel.name, detail=str(e))
