"""Фейковый HTTP-сервер для Render Free Web Service.

Render требует открытый HTTP-порт, иначе сервис "засыпает", а long polling
сам порт не слушает. Этот сервер отвечает 200 OK на GET /health (и /) и
работает ПАРАЛЛЕЛЬНО с polling — см. wiring в student_bot.bot.main().

Порт берётся из $PORT (Render выставляет сам), локально — 10000.
Никакой логики бота здесь нет: только держать порт открытым.
"""
from __future__ import annotations

import logging
import os

from aiohttp import web

log = logging.getLogger("bot.health")


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    return app


def health_port(default: int = 10000) -> int:
    try:
        return int(os.environ.get("PORT", str(default)))
    except ValueError:
        return default


async def start_health_server(port: int | None = None) -> web.AppRunner:
    """Поднять сервер, вернуть runner для graceful shutdown."""
    port = health_port() if port is None else port
    runner = web.AppRunner(build_app())
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
