"""NoticeMe — FastAPI application factory and lifespan management."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from . import __version__
from .core.config import verify_token
from .core.manager import manager
from .core.models import (
    ChannelCreate,
    ChannelUpdate,
    GroupCreate,
    GroupUpdate,
    RealtimeUpdate,
    SourceCreate,
    SourceUpdate,
    WSEvent,
)

STATIC_DIR = Path(__file__).parent / "static"
DATA_DIR = Path.home() / ".noticeme"

# Paths that don't require authentication
_PUBLIC_PATHS = {"/", "/login", "/api/auth/verify", "/hook"}


class AuthMiddleware(BaseHTTPMiddleware):
    """Check Bearer token on /api/* routes. Skip public paths and /hook/* (webhooks)."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Allow public paths and webhook endpoints
        if path in _PUBLIC_PATHS or path.startswith("/hook/") or path.startswith("/static"):
            return await call_next(request)
        # Check token on /api/* routes
        if path.startswith("/api/"):
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                token = auth[7:]
            else:
                token = request.query_params.get("token", "")
            if not verify_token(token):
                return JSONResponse(status_code=401, content={"ok": False, "detail": "Invalid or missing token"})
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure config exists on startup
    from .core.config import load_config
    load_config()
    await manager.start()
    yield
    await manager.stop()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="NoticeMe",
        description="Notification system with sources, channels, real-time and regular notifications",
        version=__version__,
        lifespan=lifespan,
    )

    app.add_middleware(AuthMiddleware)

    _register_routes(app)
    _register_static(app)

    # Mount MCP streamable-http endpoint
    try:
        from .mcp_server import mount_mcp_streamable
        mount_mcp_streamable(app)
    except ImportError:
        pass  # mcp SDK not installed

    return app


def _register_static(app: FastAPI) -> None:
    if STATIC_DIR.exists():

        @app.get("/", include_in_schema=False)
        async def index():
            return FileResponse(STATIC_DIR / "index.html")

        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _register_routes(app: FastAPI) -> None:
    # ── Auth ───────────────────────────────────────────────────

    @app.post("/api/auth/verify")
    async def api_auth_verify(body: dict[str, Any]):
        token = body.get("token", "")
        if verify_token(token):
            return {"ok": True}
        raise HTTPException(401, "Invalid token")

    # ── Sources ────────────────────────────────────────────────

    @app.get("/api/sources")
    async def api_list_sources():
        sources = manager.list_sources()
        return {"ok": True, "data": [s.model_dump() for s in sources]}

    @app.post("/api/sources")
    async def api_create_source(body: SourceCreate):
        src = await manager.create_source(body)
        return {"ok": True, "data": src.model_dump()}

    @app.get("/api/sources/{source_id}")
    async def api_get_source(source_id: str):
        src = manager.get_source(source_id)
        if not src:
            raise HTTPException(404, "Source not found")
        return {"ok": True, "data": src.model_dump()}

    @app.put("/api/sources/{source_id}")
    async def api_update_source(source_id: str, body: SourceUpdate):
        src = await manager.update_source(source_id, body)
        if not src:
            raise HTTPException(404, "Source not found")
        return {"ok": True, "data": src.model_dump()}

    @app.delete("/api/sources/{source_id}")
    async def api_delete_source(source_id: str):
        if not await manager.delete_source(source_id):
            raise HTTPException(404, "Source not found")
        return {"ok": True}

    @app.get("/api/sources/{source_id}/groups")
    async def api_get_source_groups(source_id: str):
        if not manager.get_source(source_id):
            raise HTTPException(404, "Source not found")
        group_ids = manager.get_source_groups(source_id)
        return {"ok": True, "data": group_ids}

    @app.put("/api/sources/{source_id}/groups")
    async def api_set_source_groups(source_id: str, body: dict[str, Any]):
        if not manager.get_source(source_id):
            raise HTTPException(404, "Source not found")
        manager.set_source_groups(source_id, body.get("group_ids", []))
        return {"ok": True}

    # ── Channels ───────────────────────────────────────────────

    @app.get("/api/channels")
    async def api_list_channels():
        channels = manager.list_channels()
        return {"ok": True, "data": [c.model_dump() for c in channels]}

    @app.post("/api/channels")
    async def api_create_channel(body: ChannelCreate):
        ch = manager.create_channel(body)
        return {"ok": True, "data": ch.model_dump()}

    @app.get("/api/channels/{channel_id}")
    async def api_get_channel(channel_id: str):
        ch = manager.get_channel(channel_id)
        if not ch:
            raise HTTPException(404, "Channel not found")
        return {"ok": True, "data": ch.model_dump()}

    @app.put("/api/channels/{channel_id}")
    async def api_update_channel(channel_id: str, body: ChannelUpdate):
        ch = manager.update_channel(channel_id, body)
        if not ch:
            raise HTTPException(404, "Channel not found")
        return {"ok": True, "data": ch.model_dump()}

    @app.delete("/api/channels/{channel_id}")
    async def api_delete_channel(channel_id: str):
        if not manager.delete_channel(channel_id):
            raise HTTPException(404, "Channel not found")
        return {"ok": True}

    # ── Groups ─────────────────────────────────────────────────

    @app.get("/api/groups")
    async def api_list_groups():
        groups = manager.list_groups()
        return {"ok": True, "data": [g.model_dump() for g in groups]}

    @app.post("/api/groups")
    async def api_create_group(body: GroupCreate):
        g = manager.create_group(body)
        return {"ok": True, "data": g.model_dump()}

    @app.get("/api/groups/{group_id}")
    async def api_get_group(group_id: str):
        g = manager.get_group(group_id)
        if not g:
            raise HTTPException(404, "Group not found")
        return {"ok": True, "data": g.model_dump()}

    @app.put("/api/groups/{group_id}")
    async def api_update_group(group_id: str, body: GroupUpdate):
        g = manager.update_group(group_id, body)
        if not g:
            raise HTTPException(404, "Group not found")
        return {"ok": True, "data": g.model_dump()}

    @app.delete("/api/groups/{group_id}")
    async def api_delete_group(group_id: str):
        if not manager.delete_group(group_id):
            raise HTTPException(404, "Group not found")
        return {"ok": True}

    @app.get("/api/groups/{group_id}/channels")
    async def api_get_group_channels(group_id: str):
        if not manager.get_group(group_id):
            raise HTTPException(404, "Group not found")
        channel_ids = manager.get_group_channels(group_id)
        return {"ok": True, "data": channel_ids}

    @app.put("/api/groups/{group_id}/channels")
    async def api_set_group_channels(group_id: str, body: dict[str, Any]):
        if not manager.get_group(group_id):
            raise HTTPException(404, "Group not found")
        manager.set_group_channels(group_id, body.get("channel_ids", []))
        return {"ok": True}

    # ── Notifications ──────────────────────────────────────────

    @app.get("/api/notifications/realtime")
    async def api_list_realtime():
        notifs = manager.list_realtime()
        return {"ok": True, "data": [n.model_dump() for n in notifs]}

    @app.get("/api/notifications/realtime/{notification_id}")
    async def api_get_realtime(notification_id: str):
        notif = manager.get_realtime(notification_id)
        if not notif:
            raise HTTPException(404, "Realtime notification not found")
        return {"ok": True, "data": notif.model_dump()}

    @app.post("/api/notifications/realtime")
    async def api_push_realtime(body: dict[str, Any]):
        nid = body.get("id")
        if not nid:
            raise HTTPException(400, "id is required for real-time notifications")
        title = body.get("title")
        content = body.get("content", "")
        if not title:
            raise HTTPException(400, "title is required")
        level = body.get("level", "info")
        source_id = body.get("source_id")
        group_ids = body.get("group_ids")
        extra = body.get("extra", {})
        notif = await manager.push_realtime(nid, title, content, level, source_id, extra, group_ids=group_ids)
        return {"ok": True, "data": notif.model_dump()}

    @app.put("/api/notifications/realtime/{notification_id}")
    async def api_update_realtime(notification_id: str, body: RealtimeUpdate):
        notif = await manager.update_realtime(notification_id, body)
        if not notif:
            raise HTTPException(404, "Realtime notification not found")
        return {"ok": True, "data": notif.model_dump()}

    @app.delete("/api/notifications/realtime/{notification_id}")
    async def api_clear_realtime(notification_id: str):
        if not await manager.clear_realtime(notification_id):
            raise HTTPException(404, "Realtime notification not found")
        return {"ok": True}

    @app.delete("/api/notifications/realtime")
    async def api_clear_all_realtime():
        count = await manager.clear_all_realtime()
        return {"ok": True, "data": {"cleared": count}}

    @app.post("/api/notifications/push")
    async def api_push_regular(body: dict[str, Any]):
        title = body.get("title")
        content = body.get("content", "")
        if not title:
            raise HTTPException(400, "title is required")
        level = body.get("level", "info")
        source_id = body.get("source_id")
        group_ids = body.get("group_ids")
        extra = body.get("extra", {})
        results = await manager.push_regular(title, content, level, source_id, extra, group_ids=group_ids)
        return {"ok": True, "data": [r.model_dump() for r in results]}

    # ── Notifiers ───────────────────────────────────────────────

    @app.get("/api/notifiers")
    async def api_list_notifiers():
        notifiers = manager.list_notifiers()
        return {"ok": True, "data": [n.model_dump() for n in notifiers]}

    @app.get("/api/notifiers/{sender_id}/channels")
    async def api_get_sender_channels(sender_id: str):
        channel_ids = manager.get_sender_channels(sender_id)
        return {"ok": True, "data": channel_ids}

    @app.put("/api/notifiers/{sender_id}/channels")
    async def api_set_sender_channels(sender_id: str, body: dict[str, Any]):
        manager.set_sender_channels(sender_id, body.get("channel_ids", []))
        return {"ok": True}

    # ── History ────────────────────────────────────────────────

    @app.get("/api/history")
    async def api_list_history(
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        entries = manager.list_history(limit, offset)
        return {"ok": True, "data": [e.model_dump() for e in entries]}

    @app.get("/api/history/{notification_id}")
    async def api_notification_history(notification_id: str):
        entries = manager.get_notification_history(notification_id)
        return {"ok": True, "data": [e.model_dump() for e in entries]}

    # ── Webhook ────────────────────────────────────────────────

    @app.post("/hook/{source_id}")
    async def api_webhook(source_id: str, request: Request):
        src = manager.get_source(source_id)
        if not src:
            raise HTTPException(404, "Source not found")
        try:
            payload = await request.json()
        except Exception:
            payload = {"raw": (await request.body()).decode(errors="replace")}
        await manager.source_mgr.handle_webhook(src, payload)
        return {"ok": True}

    # ── WebSocket ──────────────────────────────────────────────

    @app.websocket("/ws")
    async def ws_notifications(websocket: WebSocket):
        # Authenticate via query param
        token = websocket.query_params.get("token", "")
        if not verify_token(token):
            await websocket.close(code=4001, reason="Unauthorized")
            return
        await websocket.accept()
        queue = manager.subscribe_ws()
        try:
            for notif in manager.list_realtime():
                await websocket.send_json({
                    "type": "realtime_update",
                    "notification_id": notif.id,
                    "title": notif.title,
                    "content": notif.content,
                    "level": notif.level,
                    "extra": notif.extra,
                    "timestamp": notif.updated_at,
                })
            while True:
                try:
                    event: WSEvent = await asyncio.wait_for(queue.get(), timeout=30)
                    await websocket.send_json(event.model_dump(exclude_none=True))
                except asyncio.TimeoutError:
                    await websocket.send_json({"type": "ping"})
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            manager.unsubscribe_ws(queue)
