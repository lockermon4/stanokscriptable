"""Normalized schedule models. Pure dataclasses, no I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class Group:
    id: str
    name: str


@dataclass(frozen=True)
class Lesson:
    group: str
    day: date
    starts_at: datetime  # timezone-aware in institution tz
    ends_at: datetime | None
    subject: str
    building_code: str = ""  # как отдаёт API, e.g. "корпус А"
    room: str = ""
    kind: str = ""  # lecture/practice/lab if provided
    status: str = "scheduled"  # scheduled|cancelled|moved|...
    raw: dict = field(default_factory=dict, compare=False)

    @property
    def is_cancelled(self) -> bool:
        return self.status.lower() in {"cancelled", "canceled", "отмена", "отменено", "отменена"}


@dataclass(frozen=True)
class DaySchedule:
    day: date
    group: str
    lessons: tuple["Lesson", ...] = ()

    @property
    def active_lessons(self) -> tuple["Lesson", ...]:
        return tuple(l for l in self.lessons if not l.is_cancelled)

    @property
    def count(self) -> int:
        return len(self.active_lessons)
