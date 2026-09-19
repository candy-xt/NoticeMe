"""Notification sources — Webhook (API) and MQTT subscription."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Optional

from .models import SourceInfo, SourceType

logger = logging.getLogger("noticeme.sources")


def apply_template(source: SourceInfo, raw_payload: Any) -> dict[str, Any]:
    """Apply source template to extract notification fields from raw payload.

    Template config (source.config.template):
    - mode: "json" | "text"
    - title_field: key name for title (json mode)
    - content_field: key name for content
    - id_field: key name for notification ID (presence → realtime)
    - sender_field: key name for sender_id
    - level_field: key name for level
    - default_title: fallback title

    Returns a normalized dict with: title, content, level, id (optional), extra.sender_id
    """
    template = source.config.get("template")
    if not template:
        # No template — return payload as-is (legacy behavior)
        if isinstance(raw_payload, dict):
            return raw_payload
        return {"content": str(raw_payload)}

    mode = template.get("mode", "json")
    default_title = template.get("default_title", "通知")

    if mode == "text":
        # Text mode: raw text → content, default title, no realtime
        text = str(raw_payload) if not isinstance(raw_payload, str) else raw_payload
        sender = ""
        sender_field = template.get("sender_field", "sender")
        if isinstance(raw_payload, dict) and sender_field:
            sender = str(raw_payload.get(sender_field, ""))
        result: dict[str, Any] = {
            "title": default_title,
            "content": text,
            "level": "info",
        }
        if sender:
            result["extra"] = {"sender_id": sender}
        return result

    # JSON mode
    if not isinstance(raw_payload, dict):
        # Try to parse as JSON
        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except (json.JSONDecodeError, ValueError):
                return {"title": default_title, "content": raw_payload, "level": "info"}
        else:
            return {"title": default_title, "content": str(raw_payload), "level": "info"}

    title_field = template.get("title_field", "title")
    content_field = template.get("content_field", "content")
    id_field = template.get("id_field", "id")
    sender_field = template.get("sender_field", "sender")
    level_field = template.get("level_field", "level")

    result = {
        "title": str(raw_payload.get(title_field, default_title)),
        "content": str(raw_payload.get(content_field, "")),
        "level": str(raw_payload.get(level_field, "info")),
    }

    # Notification ID — presence means realtime
    nid = raw_payload.get(id_field)
    if nid is not None:
        result["id"] = str(nid)

    # Sender ID
    sender = raw_payload.get(sender_field)
    if sender is not None:
        result["extra"] = {"sender_id": str(sender)}

    return result


class SourceManager:
    """Manages notification sources: webhook endpoints and MQTT subscriptions."""

    def __init__(self) -> None:
        # source_id -> handler task
        self._mqtt_tasks: dict[str, asyncio.Task] = {}
        self._mqtt_clients: dict[str, Any] = {}
        # Callback invoked when a source receives data: (source_id, payload) -> None
        self.on_receive: Optional[Callable[[str, dict[str, Any]], None]] = None

    async def start_source(self, source: SourceInfo) -> None:
        """Start listening on a source (MQTT only; webhook is handled by FastAPI route)."""
        if source.type != SourceType.MQTT or not source.enabled:
            return
        if source.id in self._mqtt_tasks:
            return
        task = asyncio.create_task(self._mqtt_loop(source))
        self._mqtt_tasks[source.id] = task
        logger.info("MQTT source %s (%s) started", source.id, source.name)

    async def stop_source(self, source_id: str) -> None:
        """Stop a running MQTT source."""
        task = self._mqtt_tasks.pop(source_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        client = self._mqtt_clients.pop(source_id, None)
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
        logger.info("MQTT source %s stopped", source_id)

    async def stop_all(self) -> None:
        """Stop all MQTT sources."""
        for sid in list(self._mqtt_tasks.keys()):
            await self.stop_source(sid)

    async def handle_webhook(self, source: SourceInfo, payload: dict[str, Any]) -> None:
        """Process incoming webhook data for a source."""
        if not source.enabled:
            return
        # Apply template to normalize payload
        normalized = apply_template(source, payload)
        if self.on_receive:
            self.on_receive(source.id, normalized)

    async def _mqtt_loop(self, source: SourceInfo) -> None:
        """MQTT subscription loop for a source."""
        import aiomqtt

        cfg = source.config
        broker = cfg.get("broker", "localhost")
        port = int(cfg.get("port", 1883))
        topic = cfg.get("topic", "#")
        username = cfg.get("username")
        password = cfg.get("password")

        while True:
            try:
                async with aiomqtt.Client(
                    broker,
                    port=port,
                    username=username or None,
                    password=password or None,
                ) as client:
                    self._mqtt_clients[source.id] = client
                    await client.subscribe(topic)
                    logger.info("MQTT source %s connected to %s:%d topic=%s", source.id, broker, port, topic)
                    async for message in client.messages:
                        try:
                            raw = json.loads(message.payload.decode())
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            raw = {"raw": str(message.payload)}
                        # Apply template to normalize payload
                        normalized = apply_template(source, raw)
                        if self.on_receive:
                            self.on_receive(source.id, normalized)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("MQTT source %s error: %s, retrying in 5s", source.id, e)
                await asyncio.sleep(5)
