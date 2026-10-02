"""П.1–3: sendlog (обёртки + middleware + статистика), show_main_menu,
кэш расписания, gather-отмена геокода, retry OSRM."""
import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, ReplyKeyboardMarkup

from student_bot.bot import show_main_menu
from student_bot.config import Settings
from student_bot.routing import TwoGisRouting
from student_bot.schedule_client import ScheduleClient
from student_bot.sendlog import SendLogMiddleware, SendStats, install_send_logging, setup_logging
from student_bot.service import build_day_view
from student_bot.texts import main_menu_text

S = Settings()
TZ = ZoneInfo("Europe/Moscow")


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------- sendlog: обёртки ----------

class FakeBot:
    def __init__(self, fail=None):
        self.fail = fail
        self.calls = []

    async def send_message(self, chat_id, text, *a, **kw):
        self.calls.append(("send", chat_id, text, kw))
        if self.fail is not None:
            raise self.fail
        return object()

    async def edit_message_text(self, *a, **kw):
        self.calls.append(("edit", a, kw))
        if self.fail is not None:
            raise self.fail
        return True


def menu_markup():
    return ReplyKeyboardMarkup(keyboard=[])


def btn_markup():
    return InlineKeyboardMarkup(inline_keyboard=[])


def test_send_ok_kinds_and_stats():
    bot, stats = FakeBot(), None
    stats = install_send_logging(bot)
    run(bot.send_message(5, "hi"))  # text
    run(bot.send_message(5, "m", reply_markup=menu_markup()))  # menu
    run(bot.send_message(5, "b", reply_markup=btn_markup()))  # buttons
    run(bot.edit_message_text(text="e", chat_id=5, message_id=1))
    assert (stats.sent_ok, stats.send_err, stats.edit_ok, stats.edit_err) == (3, 0, 1, 0)
    assert "ok=3" in stats.summary()


def test_send_telegram_error_logged_counted_reraised(caplog):
    bot = FakeBot(fail=TelegramAPIError("sendMessage", "Bad Request"))
    stats = install_send_logging(bot)
    with pytest.raises(TelegramAPIError):
        run(bot.send_message(7, "hi"))
    assert stats.send_err == 1 and stats.sent_ok == 0
    assert any("FAIL" in r.message and "user_id=7" in r.message for r in caplog.records)


def test_send_transport_error_counted_not_swallowed():
    bot = FakeBot(fail=RuntimeError("net down"))
    stats = install_send_logging(bot)
    with pytest.raises(RuntimeError):
        run(bot.edit_message_text(text="e", chat_id=9, message_id=1))
    assert stats.edit_err == 1


# ---------- sendlog: middleware ----------

def test_middleware_logs_incoming(caplog):
    mw = SendLogMiddleware()
    user = SimpleNamespace(id=5, username="bob")
    ev = SimpleNamespace(text="📅 Сегодня")

    async def h(e, d):
        return "ok"

    with caplog.at_level("INFO", logger="bot.send"):
        assert run(mw(h, ev, {"event_from_user": user})) == "ok"
    assert any("user_id=5" in r.message and "@bob" in r.message for r in caplog.records)


def test_middleware_logs_handler_error_and_reraises(caplog):
    mw = SendLogMiddleware()
    user = SimpleNamespace(id=6, username=None)

    async def h(e, d):
        raise ValueError("boom")

    with pytest.raises(ValueError), caplog.at_level("INFO", logger="bot.send"):
        run(mw(h, SimpleNamespace(data="set:back"), {"event_from_user": user}))
    assert any("FAIL" in r.message and "user_id=6" in r.message for r in caplog.records)


def test_setup_logging_defaults():
    setup_logging()
    setup_logging("debug")
    assert isinstance(SendStats().summary(), str)


# ---------- show_main_menu ----------

class FakeMsg:
    def __init__(self, fail_edit=False):
        self.fail_edit = fail_edit
        self.edits = []
        self.answers = []

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))
        if self.fail_edit:
            raise RuntimeError("cannot edit")
        return True

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))
        return True


def fake_cb(msg):
    return CallbackQuery.model_construct(
        id="1", chat_instance="c",
        from_user=SimpleNamespace(id=5),
        message=msg, data="set:back")


def test_main_menu_text_both_langs():
    assert "Главное меню" in main_menu_text("ru") and "📅 Сегодня" in main_menu_text("ru")
    assert "Main menu" in main_menu_text("en")


def test_show_main_menu_message_sends_keyboard():
    m = FakeMsg()
    run(show_main_menu(m, "ru"))
    assert len(m.answers) == 1 and "Главное меню" in m.answers[0][0]
    assert isinstance(m.answers[0][1], ReplyKeyboardMarkup)


def test_show_main_menu_callback_edits_and_clears_inline():
    m = FakeMsg()
    run(show_main_menu(fake_cb(m), "ru"))
    assert len(m.edits) == 1 and m.edits[0][1] is None  # инлайн-кнопки сняты
    assert m.answers == []  # новых сообщений нет


def test_show_main_menu_callback_edit_fail_sends_new():
    m = FakeMsg(fail_edit=True)
    run(show_main_menu(fake_cb(m), "en"))
    assert len(m.answers) == 1 and isinstance(m.answers[0][1], ReplyKeyboardMarkup)


def test_show_main_menu_callback_without_message_silent():
    run(show_main_menu(fake_cb(None), "ru"))  # не падает


# ---------- кэш расписания: повторный запрос не бьёт в API ----------

def test_schedule_day_cache_serves_repeats():
    calls = []

    def h(req):
        calls.append(str(req.url))
        return httpx.Response(200, json={"items": []})

    sc = ScheduleClient(S, http=httpx.AsyncClient(transport=httpx.MockTransport(h)))
    run(sc.get_day_raw("G", "2026-09-28"))
    run(sc.get_day_raw("G", "2026-09-28"))  # из кэша
    run(sc.get_day_raw("G", "2026-09-29"))  # другой день -> сеть
    assert len(calls) == 2
    run(sc.close())


# ---------- gather: падение расписания отменяет геокод ----------

class FailSchedule:
    async def get_day_raw(self, group, day_iso, fresh=False):
        raise RuntimeError("api down")


class CountingGeo:
    def __init__(self):
        self.calls = 0

    async def geocode(self, address):
        self.calls += 1
        await asyncio.sleep(5)
        return (55.0, 37.0)


def test_build_day_view_cancels_geocode_on_schedule_fail():
    import time as _t
    geo = CountingGeo()
    t0 = _t.monotonic()
    view = run(build_day_view(
        settings=S, schedule_client=FailSchedule(), buildings=object(),
        geocoder=geo, group="G", day=datetime(2026, 9, 28).date(),
        now=datetime(2026, 9, 28, 7, 0, tzinfo=TZ), home_address="Москва, Тверская, 1",
        transport="metro", buffer_min=10, for_today=True))
    dt = _t.monotonic() - t0
    assert view.schedule_failed and dt < 4  # 5-секундный геокод не ждём: отмена работает


# ---------- OSRM retry: два обрыва -> успех с третьей ----------

def test_gis_retries_transport_errors(monkeypatch):
    from student_bot import routing as R
    calls = []

    def h(req):
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json={"routes": []})

    def fake_parse(payload):
        from student_bot.routing import RouteOption
        return [RouteOption(mode="walk", duration_s=100, summary="x")]

    monkeypatch.setattr(R, "parse_walk_payload", fake_parse)
    r = TwoGisRouting(api_key="k", http=httpx.AsyncClient(transport=httpx.MockTransport(h)))
    res = run(r.walking((55.0, 37.0), (55.7, 37.5)))
    assert res[0].duration_s == 100 and len(calls) == 3
