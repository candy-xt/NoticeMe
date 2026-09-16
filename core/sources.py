"""Notification sources — Webhook (API) and MQTT subscription."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Optional

from .models import SourceInfo, SourceType

logger = logging.getLogger("noticeme.sources")


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
        if self.on_receive:
            self.on_receive(source.id, payload)

    async def _mqtt_loop(self, source: SourceInfo) -> None:
        """MQTT subscription loop for a source."""
        try:
            import aiomqtt
        except ImportError:
            logger.error("aiomqtt not installed — MQTT source %s unavailable", source.id)
            return

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
                            payload = json.loads(message.payload.decode())
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            payload = {"raw": str(message.payload)}
                        if self.on_receive:
                            self.on_receive(source.id, payload)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("MQTT source %s error: %s, retrying in 5s", source.id, e)
                await asyncio.sleep(5)
