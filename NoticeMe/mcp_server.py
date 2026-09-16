"""NoticeMe MCP server — tools for sources, channels, notifications, and history."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp import types
from mcp.server import Server

from . import __version__
from .core import database as db
from .core.models import SourceCreate, ChannelCreate, RealtimeUpdate

TOOLS: list[types.Tool] = [
    types.Tool(
        name="list_sources",
        description="List all notification sources (webhook / MQTT)",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    types.Tool(
        name="create_source",
        description="Create a notification source",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Source name"},
                "type": {"type": "string", "enum": ["webhook", "mqtt"], "description": "Source type"},
                "config": {"type": "object", "description": "Type-specific config (MQTT: broker, port, topic, username, password)"},
                "enabled": {"type": "boolean", "default": True},
            },
            "required": ["name", "type"],
        },
    ),
    types.Tool(
        name="delete_source",
        description="Delete a notification source",
        inputSchema={
            "type": "object",
            "properties": {"source_id": {"type": "string"}},
            "required": ["source_id"],
        },
    ),
    types.Tool(
        name="list_channels",
        description="List all notification channels (MQTT / API)",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    types.Tool(
        name="create_channel",
        description="Create a notification channel",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Channel name"},
                "type": {"type": "string", "enum": ["mqtt", "api"], "description": "Channel type"},
                "config": {"type": "object", "description": "Type-specific config (API: url, method, headers, body_template; MQTT: broker, port, topic)"},
                "enabled": {"type": "boolean", "default": True},
            },
            "required": ["name", "type"],
        },
    ),
    types.Tool(
        name="delete_channel",
        description="Delete a notification channel",
        inputSchema={
            "type": "object",
            "properties": {"channel_id": {"type": "string"}},
            "required": ["channel_id"],
        },
    ),
    types.Tool(
        name="get_source_channels",
        description="Get channel IDs mapped to a source",
        inputSchema={
            "type": "object",
            "properties": {"source_id": {"type": "string"}},
            "required": ["source_id"],
        },
    ),
    types.Tool(
        name="set_source_channels",
        description="Map channels to a source (replaces existing mapping)",
        inputSchema={
            "type": "object",
            "properties": {
                "source_id": {"type": "string"},
                "group_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["source_id", "group_ids"],
        },
    ),
    types.Tool(
        name="list_realtime_notifications",
        description="List all active real-time notifications",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    types.Tool(
        name="push_realtime_notification",
        description="Create or update a real-time notification (persistent, has ID)",
        inputSchema={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Unique notification ID"},
                "title": {"type": "string"},
                "content": {"type": "string", "default": ""},
                "level": {"type": "string", "enum": ["info", "success", "warning", "error"], "default": "info"},
                "source_id": {"type": "string", "description": "Optional source ID for channel routing"},
                "group_ids": {"type": "array", "items": {"type": "string"}, "description": "Push to specific group IDs"},
                "extra": {"type": "object", "description": "Extra metadata"},
            },
            "required": ["id", "title"],
        },
    ),
    types.Tool(
        name="update_realtime_notification",
        description="Update an existing real-time notification",
        inputSchema={
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "level": {"type": "string", "enum": ["info", "success", "warning", "error"]},
                "extra": {"type": "object"},
            },
            "required": ["id"],
        },
    ),
    types.Tool(
        name="clear_realtime_notification",
        description="Remove a real-time notification by ID, or all if id='*'",
        inputSchema={
            "type": "object",
            "properties": {"id": {"type": "string", "description": "Notification ID or '*' to clear all"}},
            "required": ["id"],
        },
    ),
    types.Tool(
        name="push_notification",
        description="Push a regular (fire-and-forget) notification",
        inputSchema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string", "default": ""},
                "level": {"type": "string", "enum": ["info", "success", "warning", "error"], "default": "info"},
                "source_id": {"type": "string", "description": "Optional source ID for channel routing"},
                "group_ids": {"type": "array", "items": {"type": "string"}, "description": "Push to specific group IDs"},
            },
            "required": ["title"],
        },
    ),
    types.Tool(
        name="get_history",
        description="Get notification history",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20, "description": "Max entries to return"},
                "notification_id": {"type": "string", "description": "Filter by notification ID"},
            },
            "required": [],
        },
    ),
]


async def _get_manager():
    from .core.manager import NoticeManager
    mgr = NoticeManager()
    await mgr.start()
    return mgr


async def _handle_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
    return types.ListToolsResult(tools=TOOLS)


async def _handle_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
    try:
        result = await _dispatch(params.name, params.arguments or {})
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
        )
    except Exception as e:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Error: {e}")],
            isError=True,
        )


async def _dispatch(name: str, args: dict[str, Any]) -> Any:
    db.init_db()

    if name == "list_sources":
        return [s.model_dump() for s in db.list_sources()]
    if name == "create_source":
        data = SourceCreate(name=args["name"], type=args["type"], config=args.get("config", {}), enabled=args.get("enabled", True))
        src = db.create_source(data)
        return src.model_dump()
    if name == "delete_source":
        return {"ok": db.delete_source(args["source_id"])}

    if name == "list_channels":
        return [c.model_dump() for c in db.list_channels()]
    if name == "create_channel":
        data = ChannelCreate(name=args["name"], type=args["type"], config=args.get("config", {}), enabled=args.get("enabled", True))
        ch = db.create_channel(data)
        return ch.model_dump()
    if name == "delete_channel":
        return {"ok": db.delete_channel(args["channel_id"])}

    if name == "get_source_channels":
        return db.get_source_channels(args["source_id"])
    if name == "set_source_channels":
        db.set_source_channels(args["source_id"], args["channel_ids"])
        return {"ok": True}

    if name == "list_realtime_notifications":
        return [n.model_dump() for n in db.list_realtime()]
    if name == "push_realtime_notification":
        mgr = await _get_manager()
        try:
            notif = await mgr.push_realtime(
                notification_id=args["id"], title=args["title"],
                content=args.get("content", ""), level=args.get("level", "info"),
                source_id=args.get("source_id"), extra=args.get("extra"),
                group_ids=args.get("group_ids"),
            )
            return notif.model_dump()
        finally:
            await mgr.stop()
    if name == "update_realtime_notification":
        mgr = await _get_manager()
        try:
            data = RealtimeUpdate(title=args.get("title"), content=args.get("content"), level=args.get("level"), extra=args.get("extra"))
            notif = await mgr.update_realtime(args["id"], data)
            if not notif:
                return {"error": f"Notification '{args['id']}' not found"}
            return notif.model_dump()
        finally:
            await mgr.stop()
    if name == "clear_realtime_notification":
        nid = args["id"]
        if nid == "*":
            return {"cleared": db.clear_realtime()}
        return {"ok": db.delete_realtime(nid)}

    if name == "push_notification":
        mgr = await _get_manager()
        try:
            results = await mgr.push_regular(
                title=args["title"], content=args.get("content", ""),
                level=args.get("level", "info"), source_id=args.get("source_id"),
                group_ids=args.get("group_ids"),
            )
            return [r.model_dump() for r in results]
        finally:
            await mgr.stop()

    if name == "get_history":
        nid = args.get("notification_id")
        if nid:
            entries = db.get_history_for_notification(nid)
        else:
            entries = db.list_history(limit=args.get("limit", 20))
        return [e.model_dump() for e in entries]

    raise ValueError(f"Unknown tool: {name}")


def _create_mcp_server() -> Server:
    """Create the MCP Server instance with handlers registered."""
    server = Server("noticeme", version=__version__)
    server.add_request_handler("tools/list", type(types.ListToolsRequest), _handle_list_tools)
    server.add_request_handler("tools/call", type(types.CallToolRequest), _handle_call_tool)
    return server


_mcp_server: Server | None = None


def _get_mcp_server() -> Server:
    global _mcp_server
    if _mcp_server is None:
        _mcp_server = _create_mcp_server()
    return _mcp_server


def mount_mcp_streamable(fastapi_app: Any, path: str = "/mcp") -> None:
    """Mount the MCP streamable-http endpoint onto a FastAPI app."""
    server = _get_mcp_server()
    starlette_app = server.streamable_http_app(streamable_http_path="/")
    fastapi_app.mount(path, starlette_app)


def run_mcp_stdio() -> None:
    """Run the MCP server on stdio (blocking)."""
    from mcp import stdio_server

    server = _get_mcp_server()

    async def _run():
        async with stdio_server() as (read_stream, write_stream):
            init_options = server.create_initialization_options()
            await server.run(read_stream, write_stream, init_options)

    asyncio.run(_run())
