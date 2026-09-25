from datetime import datetime
from zoneinfo import ZoneInfo

from student_bot.exit_time import compute_exit, first_relevant_lesson
from student_bot.models import DaySchedule, Lesson
from student_bot.routing_base import RouteResult

TZ = ZoneInfo("Europe/Moscow")


def _les(h1, m1, h2=10, m2=30, subj="Матан", bld="1", status="scheduled"):
    d = datetime(2026, 9, 24, h1, m1, tzinfo=TZ)
    e = datetime(2026, 9, 24, h2, m2, tzinfo=TZ)
    return Lesson(group="G", day=d.date(), starts_at=d, ends_at=e, subject=subj, building_code=bld, status=status)


def test_exit_formula():
    les = _les(9, 0)
    route = RouteResult(travel_seconds=1800, is_approximate=True, provider="osrm")
    now = datetime(2026, 9, 24, 7, 0, tzinfo=TZ)
    plan = compute_exit(les, route, 10, now)
    assert plan.exit_at == datetime(2026, 9, 24, 8, 20, tzinfo=TZ)
    assert plan.is_approximate is True
    assert plan.already_passed is False


def test_exit_already_passed():
    les = _les(9, 0)
    route = RouteResult(travel_seconds=3600, is_approximate=True, provider="osrm")
    now = datetime(2026, 9, 24, 8, 30, tzinfo=TZ)  # exit would be 07:50
    plan = compute_exit(les, route, 10, now)
    assert plan.already_passed is True


def test_first_relevant_skips_past_and_cancelled():
    past = _les(8, 0, 8, 45, subj="Прошлая")
    cancelled = _les(9, 0, 10, 30, subj="Отмена", status="отменена")
    future = _les(10, 45, 12, 15, subj="Физика", bld="2")
    sched = DaySchedule(day=past.day, group="G", lessons=(past, cancelled, future))
    now = datetime(2026, 9, 24, 9, 30, tzinfo=TZ)
    assert first_relevant_lesson(sched, now) == future


def test_different_buildings_first_wins():
    a = _les(9, 0, 10, 30, subj="A", bld="корпус А")
    b = _les(10, 45, 12, 15, subj="B", bld="корпус Б")
    sched = DaySchedule(day=a.day, group="G", lessons=(a, b))
    now = datetime(2026, 9, 24, 7, 0, tzinfo=TZ)
    first = first_relevant_lesson(sched, now)
    assert first.building_code == "корпус А"
