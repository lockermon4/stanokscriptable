"""Планировщик уведомлений на фейковых часах.

Учебный день тик-за-тиком (60 с): первая пара 09:00, выход 08:10.
Окно отправки (2 мин) проверяется каждый тик по кэшированному view,
тяжёлый пересчёт 2GIS — раз в 30 мин. Время пинга = выбранный
пользователем отступ (morning_min_before_exit / evening_time).
"""
import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from student_bot import bot as botmod
from student_bot.exit_time import ExitPlan
from student_bot.models import DaySchedule, Lesson
from student_bot.store import Store, UserSettings

TZ = ZoneInfo("Europe/Moscow")
DAY = date(2026, 10, 2)
LESSON_START = datetime(2026, 10, 2, 9, 0, tzinfo=TZ)
LESSON_END = datetime(2026, 10, 2, 10, 30, tzinfo=TZ)
EXIT_AT = datetime(2026, 10, 2, 8, 10, tzinfo=TZ)  # 09:00 - 40 мин - 10 запас


class _StopSim(RuntimeError):
    pass


def _run_sim(tmp_path, monkeypatch, start, stop, *, morning_lead=60, evening="21:00",
             route_ok=True, real_card=False):
    clock = [start]

    class FD(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0]

    lesson = Lesson(group="G", day=DAY, starts_at=LESSON_START, ends_at=LESSON_END,
                    subject="Матан", room="412")
    sched = DaySchedule(day=DAY, group="G", lessons=(lesson,))

    sent: list[tuple[str, str]] = []
    recalcs: list[str] = []

    class ShimAsyncio:
        @staticmethod
        async def sleep(sec):
            clock[0] += timedelta(seconds=sec)
            if clock[0] >= stop:
                raise _StopSim()

    def make_view(*a, **kw):
        recalcs.append(clock[0].strftime("%H:%M"))

        async def _v(day=None, **k):
            if not route_ok:
                return SimpleNamespace(
                    schedule=sched, plan=None, schedule_failed=False,
                    route_failed=True, unknown_building=False, metro_summary="",
                    target=lesson)
            return SimpleNamespace(
                schedule=sched,
                plan=ExitPlan(lesson=lesson, travel_seconds=2400, buffer_min=10,
                              exit_at=EXIT_AT, is_approximate=False,
                              route_calculated_at=None,
                              already_passed=EXIT_AT <= clock[0]),
                schedule_failed=False, route_failed=False, unknown_building=False,
                metro_summary="", target=None)
        return _v()

    async def no_weather(*a, **k):
        return None

    class BotStub:
        async def send_message(self, uid, text):
            sent.append((clock[0].strftime("%H:%M:%S"), text))

    stubs = {
        "datetime": FD, "time": SimpleNamespace(monotonic=lambda: clock[0].timestamp()),
        "asyncio": ShimAsyncio,
        "normalize_day": lambda raw, group, day, tz_name: (sched, None),
        "build_day_view": make_view, "get_weather": no_weather,
        "build_evening": lambda s, n: None,
        "format_telegram_evening": lambda c, lang: "EVENING",
        "evening_failed": lambda d: None,
    }
    if real_card:
        # утренний текст формируем настоящими morning_card/format_telegram_morning
        from student_bot.bot import morning_card
        from student_bot.cards import format_telegram_morning
        stubs["morning_card"] = morning_card
        stubs["format_telegram_morning"] = lambda d, lang: format_telegram_morning(d, lang)
    else:
        stubs["morning_card"] = lambda view, note: None
        stubs["format_telegram_morning"] = lambda d, lang: "MORNING"
    for name, val in stubs.items():
        monkeypatch.setattr(botmod, name, val)

    store = Store(str(tmp_path / "bot.sqlite3"))
    store.save_user(UserSettings(user_id=42, group="G", home_lat=55.79, home_lon=37.605,
                                 transport="metro", buffer_min=10,
                                 evening_time=evening, morning_min_before_exit=morning_lead))
    deps = {"schedule_client": SimpleNamespace(
                get_day_raw=lambda g, d: asyncio.sleep(0, [])),
            "buildings": None, "geocoder": None, "routing": None}
    settings = botmod.Settings(institution_tz="Europe/Moscow")
    try:
        asyncio.run(botmod.scheduler_loop(BotStub(), settings, store, deps, {}))
    except _StopSim:
        pass
    return sent, recalcs


def test_morning_ping_fires_at_user_selected_time(tmp_path, monkeypatch):
    sent, recalcs = _run_sim(
        tmp_path, monkeypatch,
        start=datetime(2026, 10, 2, 4, 0, tzinfo=TZ),
        stop=datetime(2026, 10, 2, 12, 0, tzinfo=TZ), morning_lead=60)
    morning = [t for t, text in sent if text == "MORNING"]
    # notify_at = exit 08:10 - 60 мин = 07:10
    assert morning == ["07:10:00"]
    # тяжёлый пересчёт — не чаще раза в 30 мин
    assert recalcs == ["05:00", "05:30", "06:00", "06:30", "07:00"]


def test_morning_ping_respects_custom_lead(tmp_path, monkeypatch):
    sent, _ = _run_sim(
        tmp_path, monkeypatch,
        start=datetime(2026, 10, 2, 4, 30, tzinfo=TZ),
        stop=datetime(2026, 10, 2, 9, 0, tzinfo=TZ), morning_lead=150)
    morning = [t for t, text in sent if text == "MORNING"]
    # 08:10 - 150 мин = 05:40 (первый тик после наступления окна)
    assert morning == ["05:40:00"]


def test_evening_ping_still_fires_at_user_evening_time(tmp_path, monkeypatch):
    sent, _ = _run_sim(
        tmp_path, monkeypatch,
        start=datetime(2026, 10, 1, 20, 55, tzinfo=TZ),
        stop=datetime(2026, 10, 1, 21, 30, tzinfo=TZ), evening="21:15")
    assert [t for t, text in sent if text == "EVENING"] == ["21:15:00"]


FALLBACK = "🚇 2GIS сейчас не отвечает, маршрут не посчитал. Расписание ниже верное — дорогу прикинь сам."


def test_morning_sends_schedule_without_route_when_2gis_fails(tmp_path, monkeypatch, caplog):
    # Регрессия на баг: 2GIS лежит (route_failed) — сообщение всё равно уходит.
    import logging
    with caplog.at_level(logging.INFO, logger="bot"):
        sent, _ = _run_sim(
            tmp_path, monkeypatch,
            start=datetime(2026, 10, 2, 4, 0, tzinfo=TZ),
            stop=datetime(2026, 10, 2, 12, 0, tzinfo=TZ),
            morning_lead=60, route_ok=False, real_card=True)
    morn = [t for t, text in sent if FALLBACK in text]
    # окно без маршрута: 09:00 − (45 дорога + 10 запас + 60 отступ) = 07:05
    assert morn == ["07:05:00"]
    text = next(t for _, t in sent if FALLBACK in t)
    assert "09:00–10:30" in text and "Матан" in text  # расписание в сообщении
    assert "Выйти в" not in text  # строка выхода заменена, не выдумана
    # в логах видно деградированную отправку
    assert "маршрут не получен" in caplog.text and "расписание без маршрута" in caplog.text


def test_morning_normal_route_unchanged_regression(tmp_path, monkeypatch):
    # маршрут посчитан — прежний пинг ровно во время пользователя, без fallback-текста
    sent, recalcs = _run_sim(
        tmp_path, monkeypatch,
        start=datetime(2026, 10, 2, 4, 0, tzinfo=TZ),
        stop=datetime(2026, 10, 2, 12, 0, tzinfo=TZ), morning_lead=60)
    assert [t for t, text in sent if text == "MORNING"] == ["07:10:00"]
    assert recalcs == ["05:00", "05:30", "06:00", "06:30", "07:00"]
