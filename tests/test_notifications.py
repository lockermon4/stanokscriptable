from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from student_bot.exit_time import compute_exit
from student_bot.models import DaySchedule, Lesson
from student_bot.notifications import (evening_text, morning_notify_time, morning_text,
                                        should_send_morning, should_send_morning_no_route,
                                        morning_no_route_window, MORNING_NO_ROUTE_TRAVEL_MIN)
from student_bot.routing_base import RouteResult

TZ = ZoneInfo("Europe/Moscow")


def _plan():
    les = Lesson(group="G", day=date(2026, 9, 24),
                 starts_at=datetime(2026, 9, 24, 9, 0, tzinfo=TZ),
                 ends_at=datetime(2026, 9, 24, 10, 30, tzinfo=TZ),
                 subject="Матан", building_code="1")
    route = RouteResult(travel_seconds=1800, is_approximate=True, provider="osrm",
                        calculated_at=datetime(2026, 9, 24, 7, 0, tzinfo=TZ))
    now = datetime(2026, 9, 24, 7, 0, tzinfo=TZ)
    return compute_exit(les, route, 10, now)


def test_morning_no_route_window_before_lesson():
    assert MORNING_NO_ROUTE_TRAVEL_MIN == 45
    start = datetime(2026, 9, 24, 9, 0, tzinfo=TZ)
    # lead=60, buffer=10 -> окно [09:00-(45+10+60), 09:00-(45+10)) = [07:05, 08:05)
    ws, we = morning_no_route_window(start, 60, 10)
    assert ws == datetime(2026, 9, 24, 7, 5, tzinfo=TZ)
    assert we == datetime(2026, 9, 24, 8, 5, tzinfo=TZ) and we <= start
    assert should_send_morning_no_route(datetime(2026, 9, 24, 7, 5, tzinfo=TZ), start, 60, 10) is True
    assert should_send_morning_no_route(datetime(2026, 9, 24, 7, 0, tzinfo=TZ), start, 60, 10) is False
    assert should_send_morning_no_route(datetime(2026, 9, 24, 8, 30, tzinfo=TZ), start, 60, 10) is False


def test_morning_window_never_after_exit():
    plan = _plan()  # exit 08:20
    assert morning_notify_time(plan, 60) == datetime(2026, 9, 24, 7, 20, tzinfo=TZ)
    assert should_send_morning(datetime(2026, 9, 24, 7, 20, tzinfo=TZ), plan, 60) is True
    assert should_send_morning(datetime(2026, 9, 24, 7, 0, tzinfo=TZ), plan, 60) is False  # too early
    assert should_send_morning(datetime(2026, 9, 24, 8, 30, tzinfo=TZ), plan, 60) is False  # after exit
    assert should_send_morning(datetime(2026, 9, 24, 7, 30, tzinfo=TZ), None, 60) is False


def test_evening_text_with_and_without_lessons():
    plan = _plan()
    sched = DaySchedule(day=date(2026, 9, 25), group="G", lessons=(plan.lesson,))
    t = evening_text(sched, "взять халат")
    assert "пар: 1" in t and "Матан" in t and "взять халат" in t
    assert "09:00–10:30 — Матан" in t and "Первая пара: 09:00–10:30" in t
    empty = DaySchedule(day=date(2026, 9, 27), group="G", lessons=())
    assert "занятий нет" in evening_text(empty, "")


def test_morning_text_honest_failures():
    assert "неизвестен" in morning_text(None, unknown_building=True)
    assert "посчитать не получилось" in morning_text(None, route_failed=True)
    t = morning_text(_plan())
    assert "приблизительная" in t  # метка приблизительности провайдера
    assert "Расчёт маршрута" in t  # время последнего расчёта показано
    assert "Первая пара: 09:00–10:30 — Матан" in t
