"""HTTP-сервер: health для Render Free + iOS API (/api/v1/*) поверх той же логики.

Render требует открытый HTTP-порт, иначе сервис "засыпает", а long polling
сам порт не слушает. Сервер работает ПАРАЛЛЕЛЬНО с polling — см. wiring в
student_bot.bot.main() (туда же передаётся ApiCtx).

Порт берётся из $PORT (Render выставляет сам), локально — 10000.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from aiohttp import web

log = logging.getLogger("bot.health")

API_CTX_KEY = web.AppKey("api_ctx", object)


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def build_app(ctx: Any = None) -> web.Application:
    """ctx: student_bot.api.ApiCtx или None (только health — для тестов/старта)."""
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    if ctx is not None:
        from .admin import register_admin
        from .api import exit_time_handler, today_handler, tomorrow_handler

        app[API_CTX_KEY] = ctx
        app.router.add_get("/api/v1/today", today_handler)
        app.router.add_get("/api/v1/tomorrow", tomorrow_handler)
        app.router.add_get("/api/v1/exit-time", exit_time_handler)
        register_admin(app)  # /admin/* (без ADMIN_TOKEN — все 404)
    return app


def health_port(default: int = 10000) -> int:
    try:
        return int(os.environ.get("PORT", str(default)))
    except ValueError:
        return default


async def start_health_server(port: int | None = None, ctx: Any = None) -> web.AppRunner:
    """Поднять сервер, вернуть runner для graceful shutdown."""
    port = health_port() if port is None else port
    runner = web.AppRunner(build_app(ctx))
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("health server listening on 0.0.0.0:%d (/health)", port)
    return runner


async def stop_health_server(runner: web.AppRunner) -> None:
    try:
        await runner.cleanup()
    except Exception as e:
        log.warning("health server cleanup: %s", e)
