"""Логирование исходящих сообщений + входящих апдейтов (стандартный logging).

Почему два механизма:
- SendLogMiddleware (dp.message.middleware / dp.callback_query.middleware)
  видит ВХОДЯЩИЕ апдейты: user_id/username, тип, время обработки хендлером.
- install_send_logging(bot) оборачивает bot.send_message / bot.edit_message_text
  и видит ИСХОДЯЩИЕ: user_id, тип сообщения, успех/ошибку, время отправки.
  Middleware хендлеров результат отправки увидеть не может в принципе:
  вызовы Bot API идут мимо диспетчера. m.answer / cb.message.answer /
  bot.send_message планировщика — всё проходит через обёртки ниже.

Ничего не проглатывается: ошибки логируются и пробрасываются дальше.
"""
from __future__ import annotations

import functools
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, TelegramObject

log = logging.getLogger("bot.send")


def setup_logging(level: str = "INFO") -> None:
    import sys

    # Render читает логи из stdout/stderr — явно stdout, не файл.
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        stream=sys.stdout,
        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Шум транспортных библиотек в INFO не нужен (каждый HTTP-запрос строкой).
    for noisy in ("httpx", "httpcore", "aiohttp.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@dataclass
class SendStats:
    """Счётчики сессии: успешные/ошибочные отправки и правки."""
    sent_ok: int = 0
    send_err: int = 0
    edit_ok: int = 0
    edit_err: int = 0
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def summary(self) -> str:
        return (f"send ok={self.sent_ok} err={self.send_err} | "
                f"edit ok={self.edit_ok} err={self.edit_err}")


def _send_kind(reply_markup: Any) -> str:
    if isinstance(reply_markup, ReplyKeyboardMarkup):
        return "menu"  # главное меню / reply-клавиатура
    if isinstance(reply_markup, InlineKeyboardMarkup):
        return "buttons"  # инлайн-кнопки
    return "text"


def install_send_logging(bot: Any) -> SendStats:
    """Оборачивает bot.send_message / bot.edit_message_text. Возвращает счётчики."""
    stats = SendStats()
    orig_send = bot.send_message
    orig_edit = bot.edit_message_text

    @functools.wraps(orig_send)
    async def send_logged(chat_id: Any, text: str, *a: Any, **kw: Any) -> Any:
        kind = _send_kind(kw.get("reply_markup"))
        t0 = time.perf_counter()
        try:
            res = await orig_send(chat_id, text, *a, **kw)
        except TelegramAPIError as e:
            stats.send_err += 1
            log.error("SEND user_id=%s kind=%s FAIL err=%s: %s | %s",
                      chat_id, kind, type(e).__name__, e, stats.summary())
            raise
        except Exception as e:  # транспорт/сеть вне Telegram API
            stats.send_err += 1
            log.error("SEND user_id=%s kind=%s FAIL err=%s: %s | %s",
                      chat_id, kind, type(e).__name__, e, stats.summary())
            raise
        ms = (time.perf_counter() - t0) * 1000
        stats.sent_ok += 1
        log.info("SEND user_id=%s kind=%s ok len=%d %.0fms | %s",
                 chat_id, kind, len(text or ""), ms, stats.summary())
        return res

    @functools.wraps(orig_edit)
    async def edit_logged(*a: Any, **kw: Any) -> Any:
        kind = "edit_clear" if kw.get("reply_markup") is None else _send_kind(kw.get("reply_markup"))
        if kind == "text":
            kind = "edit_text"
        chat_id = kw.get("chat_id", "?")
        t0 = time.perf_counter()
        try:
            res = await orig_edit(*a, **kw)
        except TelegramAPIError as e:
            stats.edit_err += 1
            log.error("EDIT user_id=%s kind=%s FAIL err=%s: %s | %s",
                      chat_id, kind, type(e).__name__, e, stats.summary())
            raise
        except Exception as e:
            stats.edit_err += 1
            log.error("EDIT user_id=%s kind=%s FAIL err=%s: %s | %s",
                      chat_id, kind, type(e).__name__, e, stats.summary())
            raise
        ms = (time.perf_counter() - t0) * 1000
        stats.edit_ok += 1
        log.info("EDIT user_id=%s kind=%s ok %.0fms | %s", chat_id, kind, ms, stats.summary())
        return res

    bot.send_message = send_logged  # type: ignore[method-assign]
    bot.edit_message_text = edit_logged  # type: ignore[method-assign]
    return stats


class SendLogMiddleware(BaseMiddleware):
    """Логи входящих апдейтов: кто, что, сколько обрабатывалось, ошибки хендлера."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        uid = getattr(user, "id", "?")
        uname = getattr(user, "username", None) or "?"
        kind = type(event).__name__  # Message / CallbackQuery
        detail = ""
        if hasattr(event, "text") and event.text:
            detail = (event.text or "")[:60].replace("\n", " ")
        elif hasattr(event, "data"):
            detail = f"cb:{event.data}"
        elif hasattr(event, "location") and event.location:
            detail = "location"
        t0 = time.perf_counter()
        try:
            res = await handler(event, data)
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000
            log.error("IN user_id=%s @%s kind=%s FAIL err=%s: %s (%.0fms)",
                      uid, uname, kind, type(e).__name__, detail or "-", ms)
            raise
        ms = (time.perf_counter() - t0) * 1000
        log.info("IN user_id=%s @%s kind=%s ok %s (%.0fms)", uid, uname, kind, detail or "-", ms)
        return res
