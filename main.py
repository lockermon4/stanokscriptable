"""Единая точка входа (Render Start Command: python main.py).

Запускает health-сервер (:$PORT/health) и aiogram long polling параллельно,
graceful shutdown на SIGTERM — см. student_bot.bot.main().

Сначала — явная валидация окружения одним списком (а не падение на первой же
переменной глубоко в коде): чего не хватает — всё сразу в лог + выход.
"""
import asyncio
import logging
import os
import sys

from student_bot.bot import main
from student_bot.config import missing_env_vars
from student_bot.sendlog import setup_logging

log = logging.getLogger("bot")


def check_env() -> None:
    """BOT_TOKEN / PUBLIC_BASE_URL отсутствуют → полный список + выход.
    GIS_API_KEY отсутствует → громкое предупреждение, работаем дальше
    (дорога честно route_failed)."""
    missing, recommended = missing_env_vars()
    for var in recommended:
        log.warning("env %s is not set — degraded mode (see .env.example)", var)
    if missing:
        log.error("Missing required env vars: %s. "
                  "Set them in Render Dashboard → Environment (see .env.example).",
                  ", ".join(missing))
        raise SystemExit(f"Missing required env vars: {', '.join(missing)}")


if __name__ == "__main__":
    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
    check_env()
    asyncio.run(main())
