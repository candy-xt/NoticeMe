"""Notification sources — Webhook (API) and MQTT subscription."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Optional

from .models import SourceInfo, SourceType

logger = logging.getLogger("noticeme.sources")


def apply_template(
    source: SourceInfo,
    raw_payload: Any,
    topic: Optional[str] = None,
) -> dict[str, Any]:
    """Apply source template to extract notification fields from raw payload.

    Template config (source.config.template):
    - mode: "json" | "text"
    - title_field: key name for title (json mode)
    - content_field: key name for content
    - id_field: key name for notification ID (presence → realtime)
    - sender_field: key name for sender_id
    - default_title: fallback title

    Sender fallback (source.config.sender_from_topic):
    - "off" (default): only the payload provides the sender
    - "topic": use the MQTT topic as the sender
    - "last": use the last topic segment (e.g. ``noticeme/NodeRED`` → ``NodeRED``)

    Returns a normalized dict with: title, content, id (optional), extra.sender_id
    """
    result = _apply_template(source, raw_payload)
    return _apply_topic_sender(source, result, topic)


def _apply_topic_sender(
    source: SourceInfo, result: dict[str, Any], topic: Optional[str]
) -> dict[str, Any]:
    """Fill in the sender from the MQTT topic when the payload has none."""
    if not topic:
        return result
    if result.get("sender") or result.get("sender_id"):
        return result
    if (result.get("extra") or {}).get("sender_id"):
        return result

    mode = source.config.get("sender_from_topic", "off")
    if mode == "topic":
        sender = topic
    elif mode == "last":
        sender = topic.rstrip("/").rsplit("/", 1)[-1]
    else:
        return result

    sender = str(sender).strip()
    if not sender:
        return result

    result = dict(result)
    result["extra"] = {**(result.get("extra") or {}), "sender_id": sender}
    return result


def _apply_template(source: SourceInfo, raw_payload: Any) -> dict[str, Any]:
    """Normalize a payload using the source template (without topic fallback)."""
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
        sender = ""
        sender_field = template.get("sender_field") or "sender"
        fields: dict[str, Any] = {}
        if isinstance(raw_payload, dict):
            fields = raw_payload
            # MQTT wraps non-JSON payloads as {"raw": "<text>"}; unwrap it so the
            # content is the actual text rather than the wrapper's repr.
            if set(raw_payload.keys()) == {"raw"}:
                text = str(raw_payload["raw"])
            else:
                text = str(raw_payload)
        else:
            text = str(raw_payload)

        if fields:
            sender = str(
                fields.get(sender_field)
                or fields.get("sender")
                or fields.get("sender_id")
                or ""
            )

        result: dict[str, Any] = {
            "title": default_title,
            "content": text,
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
                return {"title": default_title, "content": raw_payload}
        else:
            return {"title": default_title, "content": str(raw_payload)}

    title_field = template.get("title_field", "title")
    content_field = template.get("content_field", "content")
    id_field = template.get("id_field", "id")
    sender_field = template.get("sender_field", "sender")
    result = {
        "title": str(raw_payload.get(title_field, default_title)),
        "content": str(raw_payload.get(content_field, "")),
    }

    # Notification ID — presence means realtime
    nid = raw_payload.get(id_field)
    if nid is not None:
        result["id"] = str(nid)

    # Sender ID — configured field first, then the common names
    sender = (
        raw_payload.get(sender_field)
        or raw_payload.get("sender")
        or raw_payload.get("sender_id")
    )
    if sender is not None and str(sender) != "":
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
                        topic = str(message.topic)
                        try:
                            raw = json.loads(message.payload.decode())
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            raw = {"raw": message.payload.decode(errors="replace")}
                        # Apply template to normalize payload (topic is used as a
                        # last-resort sender when configured).
                        normalized = apply_template(source, raw, topic=topic)
                        logger.debug("MQTT %s topic=%s sender=%r",
                                     source.id, topic, (normalized.get("extra") or {}).get("sender_id"))
                        if self.on_receive:
                            self.on_receive(source.id, normalized)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("MQTT source %s error: %s, retrying in 5s", source.id, e)
                await asyncio.sleep(5)
