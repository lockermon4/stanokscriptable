"""Notification texts + send-time decisions. Pure functions (testable without Telegram)."""
from __future__ import annotations

from datetime import datetime, timedelta, time as dtime

from .exit_time import ExitPlan, format_duration
from .models import DaySchedule


def parse_hhmm(s: str) -> dtime:
    h, m = s.strip().split(":")
    return dtime(int(h), int(m))


def lesson_range(les) -> str:
    """'09:00–10:30': начало и конец пары (конец неизвестен → +90 мин)."""
    end = les.ends_at or (les.starts_at + timedelta(minutes=90))
    return f"{les.starts_at.strftime('%H:%M')}–{end.strftime('%H:%M')}"


def evening_text(schedule: DaySchedule, note: str) -> str:
    if not schedule.active_lessons:
        base = f"Завтра ({schedule.day.isoformat()}): занятий нет. Отдыхайте!"
    else:
        lines = [f"Завтра ({schedule.day.isoformat()}): пар: {schedule.count}"]
        for les in schedule.active_lessons:
            t = lesson_range(les)
            room = f", ауд. {les.room}" if les.room else ""
            kind = f" ({les.kind})" if les.kind else ""
            lines.append(f"• {t} — {les.subject}{kind}{room}")
        first = schedule.active_lessons[0]
        where = f"ауд. {first.room}" if first.room else "аудитория неизвестна"
        lines.append(f"Первая пара: {lesson_range(first)}, {where}.")
        base = "\n".join(lines)
    if note.strip():
        base += f"\nЧто взять: {note.strip()}"
    return base


def morning_text(plan: ExitPlan | None, lesson_note: str = "", *, route_failed: bool = False,
                 unknown_building: bool = False, tz_name: str = "Europe/Moscow") -> str:
    if plan is None:
        if unknown_building:
            return "Не могу рассчитать выход: адрес корпуса неизвестен."
        if route_failed:
            return "Расписание есть, но время дороги сейчас посчитать не получилось. Выходите с запасом."
        return "Сегодня пар больше нет."
    les = plan.lesson
    t = lesson_range(les)
    kind = f" ({les.kind})" if les.kind else ""
    where = f"ауд. {les.room}" if les.room else "аудитория неизвестна"
    approx = " (оценка приблизительная: провайдер не учитывает метро/пробки/ожидание в реальном времени)" if plan.is_approximate else ""
    calc_at = plan.route_calculated_at
    if calc_at is not None and tz_name:
        try:
            from zoneinfo import ZoneInfo
            calc_at = calc_at.astimezone(ZoneInfo(tz_name))
        except Exception:
            pass
    calc = f"Расчёт маршрута: {calc_at.strftime('%H:%M %d.%m')}" if calc_at else "Время расчёта неизвестно"
    late = "\nВнимание: рекомендуемое время выхода уже прошло — выходите сейчас!" if plan.already_passed else ""
    return (
        f"Первая пара: {t} — {les.subject}{kind}, {where}.\n"
        f"Дорога: ~{format_duration(plan.travel_seconds)}{approx}, запас {plan.buffer_min} мин.\n"
        f"Выйти в {plan.exit_at.strftime('%H:%M')}. {calc}.{late}"
    )


def should_send_morning(now: datetime, plan: ExitPlan | None, minutes_before_exit: int) -> bool:
    """Morning ping comes before exit time, never after it."""
    from datetime import timedelta

    if plan is None:
        return False
    notify_at = plan.exit_at - timedelta(minutes=minutes_before_exit)
    return notify_at <= now < plan.exit_at


def morning_notify_time(plan: ExitPlan, minutes_before_exit: int) -> datetime:
    from datetime import timedelta

    return plan.exit_at - timedelta(minutes=minutes_before_exit)
