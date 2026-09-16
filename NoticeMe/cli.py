"""NoticeMe CLI — server, MCP, and data management commands."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click

from . import __version__
from .core import database as db


@click.group()
@click.version_option(version=__version__, prog_name="nme")
def main():
    """NoticeMe — notification system CLI."""
    pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  serve — start the HTTP server
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@main.command()
@click.option("--host", default="0.0.0.0", help="Bind address")
@click.option("--port", default=8200, type=int, help="Bind port")
@click.option("--reload", is_flag=True, help="Auto-reload on changes (dev mode)")
@click.option("--log-level", default="info", type=click.Choice(["critical", "error", "warning", "info", "debug"]))
def serve(host: str, port: int, reload: bool, log_level: str):
    """Start the NoticeMe HTTP server (API + WebUI + WebSocket + MCP streamable-http)."""
    import uvicorn

    db.init_db()
    uvicorn.run(
        "NoticeMe.app:create_app" if not reload else "NoticeMe.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level=log_level,
        factory=not reload,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  mcp — stdio MCP server
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@main.command()
def mcp():
    """Run MCP server on stdio (for editor/agent integration)."""
    from .mcp_server import run_mcp_stdio
    run_mcp_stdio()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  source — manage notification sources
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@main.group()
def source():
    """Manage notification sources."""
    pass


@source.command("list")
def source_list():
    """List all notification sources."""
    db.init_db()
    sources = db.list_sources()
    if not sources:
        click.echo("No sources configured.")
        return
    for s in sources:
        status = "✓" if s.enabled else "✗"
        wh = f"  webhook: {s.webhook_path}" if s.webhook_path else ""
        click.echo(f"  [{status}] {s.id}  {s.name}  ({s.type}){wh}")


@source.command("add")
@click.option("--name", "-n", required=True, help="Source name")
@click.option("--type", "-t", "stype", type=click.Choice(["webhook", "mqtt"]), required=True)
@click.option("--broker", help="MQTT broker address")
@click.option("--mqtt-port", default=1883, type=int, help="MQTT broker port")
@click.option("--topic", help="MQTT topic to subscribe")
@click.option("--username", help="MQTT username")
@click.option("--password", help="MQTT password")
@click.option("--disabled", is_flag=True, help="Create in disabled state")
def source_add(name: str, stype: str, broker: str | None, mqtt_port: int,
               topic: str | None, username: str | None, password: str | None, disabled: bool):
    """Add a new notification source."""
    from .core.models import SourceCreate

    db.init_db()
    cfg: dict = {}
    if stype == "mqtt":
        cfg["broker"] = broker or "localhost"
        cfg["port"] = mqtt_port
        cfg["topic"] = topic or "#"
        if username:
            cfg["username"] = username
        if password:
            cfg["password"] = password

    data = SourceCreate(name=name, type=stype, enabled=not disabled, config=cfg)
    src = db.create_source(data)
    click.echo(f"Created source: {src.id} ({src.name})")
    if src.webhook_path:
        click.echo(f"  Webhook endpoint: POST {src.webhook_path}")


@source.command("remove")
@click.argument("source_id")
def source_remove(source_id: str):
    """Remove a notification source."""
    db.init_db()
    if db.delete_source(source_id):
        click.echo(f"Removed source {source_id}")
    else:
        click.echo(f"Source {source_id} not found", err=True)
        sys.exit(1)


@source.command("map")
@click.argument("source_id")
@click.argument("channel_ids", nargs=-1)
def source_map(source_id: str, channel_ids: tuple[str, ...]):
    """Map channels to a source. Usage: nme source map <source_id> <ch1> <ch2> ..."""
    db.init_db()
    if not db.get_source(source_id):
        click.echo(f"Source {source_id} not found", err=True)
        sys.exit(1)
    db.set_source_channels(source_id, list(channel_ids))
    click.echo(f"Mapped source {source_id} → channels {list(channel_ids)}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  channel — manage notification channels
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@main.group()
def channel():
    """Manage notification channels."""
    pass


@channel.command("list")
def channel_list():
    """List all notification channels."""
    db.init_db()
    channels = db.list_channels()
    if not channels:
        click.echo("No channels configured.")
        return
    for c in channels:
        status = "✓" if c.enabled else "✗"
        cfg_summary = ""
        if c.type == "mqtt":
            cfg_summary = f"  {c.config.get('broker', 'localhost')}:{c.config.get('port', 1883)} → {c.config.get('topic', '?')}"
        elif c.type == "api":
            cfg_summary = f"  {c.config.get('method', 'POST')} {c.config.get('url', '?')}"
        click.echo(f"  [{status}] {c.id}  {c.name}  ({c.type}){cfg_summary}")


@channel.command("add")
@click.option("--name", "-n", required=True, help="Channel name")
@click.option("--type", "-t", "ctype", type=click.Choice(["mqtt", "api"]), required=True)
# MQTT options
@click.option("--broker", help="MQTT broker address")
@click.option("--mqtt-port", default=1883, type=int, help="MQTT broker port")
@click.option("--topic", help="MQTT publish topic")
@click.option("--username", help="MQTT username")
@click.option("--password", help="MQTT password")
# API options
@click.option("--url", help="API endpoint URL")
@click.option("--method", default="POST", help="HTTP method")
@click.option("--headers", help="HTTP headers as JSON string")
@click.option("--body-template", help="Body template with {{title}}, {{content}}, {{level}} vars")
@click.option("--disabled", is_flag=True, help="Create in disabled state")
def channel_add(name: str, ctype: str, broker: str | None, mqtt_port: int,
                topic: str | None, username: str | None, password: str | None,
                url: str | None, method: str, headers: str | None,
                body_template: str | None, disabled: bool):
    """Add a new notification channel."""
    from .core.models import ChannelCreate

    db.init_db()
    cfg: dict = {}
    if ctype == "mqtt":
        cfg["broker"] = broker or "localhost"
        cfg["port"] = mqtt_port
        cfg["topic"] = topic or "noticeme/out"
        if username:
            cfg["username"] = username
        if password:
            cfg["password"] = password
    elif ctype == "api":
        if not url:
            click.echo("--url is required for API channels", err=True)
            sys.exit(1)
        cfg["url"] = url
        cfg["method"] = method.upper()
        if headers:
            try:
                cfg["headers"] = json.loads(headers)
            except json.JSONDecodeError:
                click.echo("--headers must be valid JSON", err=True)
                sys.exit(1)
        if body_template:
            cfg["body_template"] = body_template

    data = ChannelCreate(name=name, type=ctype, enabled=not disabled, config=cfg)
    ch = db.create_channel(data)
    click.echo(f"Created channel: {ch.id} ({ch.name})")


@channel.command("remove")
@click.argument("channel_id")
def channel_remove(channel_id: str):
    """Remove a notification channel."""
    db.init_db()
    if db.delete_channel(channel_id):
        click.echo(f"Removed channel {channel_id}")
    else:
        click.echo(f"Channel {channel_id} not found", err=True)
        sys.exit(1)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  notify — push notifications from CLI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@main.command()
@click.option("--id", "notif_id", help="Notification ID (for real-time)")
@click.option("--title", "-t", required=True, help="Notification title")
@click.option("--content", "-c", default="", help="Notification content")
@click.option("--level", "-l", default="info", type=click.Choice(["info", "success", "warning", "error"]))
@click.option("--source", "-s", help="Source ID (for channel routing)")
@click.option("--channel", "-ch", multiple=True, help="Channel ID(s) to push directly (can repeat)")
@click.option("--extra", help="Extra data as JSON string")
def notify(notif_id: str | None, title: str, content: str, level: str,
           source: str | None, channel: tuple[str, ...], extra: str | None):
    """Push a notification (real-time if --id given, regular otherwise)."""
    from .core.models import WSEvent

    db.init_db()
    extra_data: dict = {}
    if extra:
        try:
            extra_data = json.loads(extra)
        except json.JSONDecodeError:
            click.echo("--extra must be valid JSON", err=True)
            sys.exit(1)

    async def _push():
        from .core.manager import NoticeManager
        mgr = NoticeManager()
        await mgr.start()
        ch_ids = list(channel) if channel else None
        try:
            if notif_id:
                notif = await mgr.push_realtime(notif_id, title, content, level, source, extra_data, channel_ids=ch_ids)
                click.echo(f"Pushed real-time notification: {notif.id}")
            else:
                results = await mgr.push_regular(title, content, level, source, extra_data, channel_ids=ch_ids)
                click.echo(f"Pushed regular notification (channels: {len(results)})")
        finally:
            await mgr.stop()

    asyncio.run(_push())


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  realtime — query real-time notifications
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@main.group("realtime")
def realtime_group():
    """Manage real-time notifications."""
    pass


@realtime_group.command("list")
def realtime_list():
    """List active real-time notifications."""
    db.init_db()
    notifs = db.list_realtime()
    if not notifs:
        click.echo("No active real-time notifications.")
        return
    for n in notifs:
        level_tag = {"info": "ℹ", "success": "✓", "warning": "⚠", "error": "✗"}.get(n.level, "?")
        click.echo(f"  {level_tag} [{n.id}] {n.title}")
        if n.content:
            preview = n.content[:80] + ("…" if len(n.content) > 80 else "")
            click.echo(f"    {preview}")


@realtime_group.command("clear")
@click.argument("notification_id", required=False)
def realtime_clear(notification_id: str | None):
    """Clear a real-time notification, or all if no ID given."""
    db.init_db()
    if notification_id:
        if db.delete_realtime(notification_id):
            click.echo(f"Cleared notification {notification_id}")
        else:
            click.echo(f"Notification {notification_id} not found", err=True)
            sys.exit(1)
    else:
        count = db.clear_realtime()
        click.echo(f"Cleared {count} notification(s)")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  history — view notification history
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@main.command()
@click.option("--limit", "-n", default=20, type=int, help="Number of entries")
@click.option("--notification", help="Filter by notification ID")
def history(limit: int, notification: str | None):
    """View notification history."""
    db.init_db()
    if notification:
        entries = db.get_history_for_notification(notification)
    else:
        entries = db.list_history(limit)
    if not entries:
        click.echo("No history entries.")
        return
    for e in entries:
        level_tag = {"info": "ℹ", "success": "✓", "warning": "⚠", "error": "✗"}.get(e.level, "?")
        nid = f" #{e.notification_id}" if e.notification_id else ""
        click.echo(f"  {level_tag} [{e.action}]{nid} {e.title}  ({e.created_at})")
        if e.content:
            preview = e.content[:80] + ("…" if len(e.content) > 80 else "")
            click.echo(f"    {preview}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  init — initialize database
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@main.command()
def init():
    """Initialize the database."""
    db.init_db()
    click.echo(f"Database initialized at {db.DB_PATH}")


if __name__ == "__main__":
    main()
