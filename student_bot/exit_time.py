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
