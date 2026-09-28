"""Exit-time math + first-lesson selection. Pure functions (easily tested)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import DaySchedule, Lesson
from .routing_base import RouteResult

# Formula: exit = lesson_start - travel - buffer. Always.


@dataclass(frozen=True)
class ExitPlan:
    lesson: Lesson
    travel_seconds: int
    buffer_min: int
    exit_at: datetime
    is_approximate: bool
    route_calculated_at: datetime | None
    already_passed: bool


def first_relevant_lesson(schedule: DaySchedule, now: datetime) -> Lesson | None:
    """First non-cancelled lesson with ends_at (or starts_at) still in the future.

    Multiple lessons in a row / different buildings: we return only the FIRST
    one needing a trip from home (inter-building moves are a later stage).
    """
    for les in schedule.active_lessons:
        end = les.ends_at or (les.starts_at + timedelta(minutes=90))
        if end > now:
            return les
    return None


def first_lesson_of_day(schedule: DaySchedule) -> Lesson | None:
    lessons = schedule.active_lessons
    return lessons[0] if lessons else None


def compute_exit(lesson: Lesson, route: RouteResult | None, buffer_min: int, now: datetime) -> ExitPlan | None:
    if route is None:
        return None
    exit_at = lesson.starts_at - timedelta(seconds=route.travel_seconds, minutes=buffer_min)
    return ExitPlan(
        lesson=lesson,
        travel_seconds=route.travel_seconds,
        buffer_min=buffer_min,
        exit_at=exit_at,
        is_approximate=route.is_approximate,
        route_calculated_at=route.calculated_at,
        already_passed=exit_at <= now,
    )


def format_duration(seconds: int) -> str:
    m = max(0, int(seconds) // 60)
    h, mm = divmod(m, 60)
    return f"{h} ч {mm} мин" if h else f"{mm} мин"


def anchor_to_open(exit_needed: datetime, lesson_start: datetime, travel_s: int,
                   open_dt: datetime) -> tuple[str, datetime, datetime]:
    """Привязка выхода к открытию метро (чистая функция).

    exit_needed < open_dt (выход попадает в 01:00–05:30) → старт отсчёта
    переносится на open_dt (05:30): ("anchored"|"miss", open_dt, arrival).
    "miss" — даже выйдя в открытие, к началу пары не успеть.
    Иначе ("ok", exit_needed, arrival) — обычный расчёт.
    """
    if exit_needed >= open_dt:
        return ("ok", exit_needed, exit_needed + timedelta(seconds=travel_s))
    arrival = open_dt + timedelta(seconds=travel_s)
    if arrival <= lesson_start:
        return ("anchored", open_dt, arrival)
    return ("miss", open_dt, arrival)


def parse_target_time(text: str, day, tz) -> datetime | None:
    """'08:00' / '8:05' (+пробелы) -> tz-aware datetime в указанный день. Иначе None."""
    import re

    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", text or "")
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return datetime(day.year, day.month, day.day, h, mi, tzinfo=tz)
