"""Unified message layer: data builders, telegram/push formats, edge cases."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from student_bot.cards import (build_evening, build_morning, evening_failed, format_push_evening,
                               format_push_morning, format_telegram_day, format_telegram_evening,
                               format_telegram_morning, with_metro)
from student_bot.exit_time import compute_exit
from student_bot.models import DaySchedule, Lesson
from student_bot.routing_base import RouteResult

TZ = ZoneInfo("Europe/Moscow")


def _les(h, m, subj="Матан", room="0209", kind="Лекция"):
    from datetime import timedelta

    start = datetime(2026, 9, 25, h, m, tzinfo=TZ)
    return Lesson(group="G", day=date(2026, 9, 25), starts_at=start,
                  ends_at=start + timedelta(minutes=95),
                  subject=subj, room=room, kind=kind)


def _sched(*lessons):
    return DaySchedule(day=date(2026, 9, 25), group="G", lessons=tuple(lessons))


def _plan(les, travel_s=4230, at=(2026, 9, 25, 7, 0)):
    route = RouteResult(travel_seconds=travel_s, is_approximate=True, provider="metro-topology",
                        calculated_at=datetime(*at, tzinfo=TZ))
    return compute_exit(les, route, 10, datetime(*at, tzinfo=TZ))


def test_evening_answers_count_what_take():
    d = build_evening(_sched(_les(10, 15, "Начертательная геометрия", "ИГ-1"),
                             _les(12, 20, "Основы программирования", "0209")), "халат")
    t = format_telegram_evening(d)
    assert t.splitlines()[0] == "🌙 Завтра — 2 пары"
    assert "10:15–11:50 — Начертательная геометрия, ауд. ИГ-1" in t
    assert t.rstrip().endswith("🎒 Взять: халат")


def test_lesson_range_everywhere_and_fallback():
    """Каждая пара — 'HH:MM–HH:MM'; без ends_at — +90 мин."""
    from student_bot.cards import build_focus, format_day_list, format_telegram_day
    from student_bot.texts import lesson_range
    from datetime import timedelta

    les = _les(10, 15, "М", "0209")  # 10:15–11:50 (95 мин)
    assert lesson_range(les) == "10:15–11:50"
    no_end = Lesson(group="G", day=date(2026, 9, 25),
                    starts_at=datetime(2026, 9, 25, 8, 30, tzinfo=TZ),
                    ends_at=None, subject="Ф")
    assert lesson_range(no_end) == "08:30–10:00"
    d = build_evening(_sched(les), "")
    assert "10:15–11:50 — М, ауд. 0209" in format_telegram_day("Сегодня", d, "", "ru")
    m = format_telegram_morning(build_morning(les, _plan(les)))
    assert m.splitlines()[1].startswith("10:15–11:50 — М")
    now = datetime(2026, 9, 25, 7, 0, tzinfo=TZ)
    assert build_focus(les, None, 3600, 10, now, True, "ru").startswith("🎯 10:15–11:50 — М")
    assert "10:15–11:50 — М" in format_day_list(_sched(les), now, "ru")


def test_evening_long_subject_plural():
    d = build_evening(_sched(_les(8, 30, "Основы российской государственности")), "")
    t = format_telegram_evening(d)
    assert t.startswith("🌙 Завтра — 1 пара\n")
    assert "Основы российской государственности" in t  # telegram: no truncation


def test_evening_empty_and_failed():
    t = format_telegram_evening(build_evening(_sched(), ""))
    assert t == "🌙 Завтра пар нет — отдыхайте!"
    t2 = format_telegram_evening(build_evening(_sched(), "позвонить"))
    assert "🎒 Заметка: позвонить" in t2
    assert "получить расписание" in format_telegram_evening(evening_failed("2026-09-25"))


def test_morning_answers_exit_and_ride():
    les = _les(10, 15, "Начертательная геометрия", "ИГ-1")
    d = with_metro(build_morning(les, _plan(les)), "метро Коньково → Савёловская: 16 перег., 1 пересад.")
    t = format_telegram_morning(d)
    lines = t.splitlines()
    assert lines[0] == "🏃 Выйти в 08:54"
    assert "10:15" in lines[1] and "ИГ-1" in lines[1]
    assert lines[2].startswith("🚇 ~1 ч 10 мин")
    assert "пересад" in lines[2]


def test_morning_no_invented_exit_on_error():
    les = _les(10, 15)
    t = format_telegram_morning(build_morning(les, None, route_failed=True))
    assert "Выйти в" not in t and "посчитать не вышло" in t
    t2 = format_telegram_morning(build_morning(les, None, unknown_building=True))
    assert "Выйти в" not in t2 and "корпуса неизвестен" in t2
    assert "пар больше нет" in format_telegram_morning(build_morning(None, None))


def test_push_key_time_visible_and_short():
    les = _les(10, 15, "Основы российской государственности и чего-то ещё длинного", "ИГ-1")
    m = format_push_morning(with_metro(build_morning(les, _plan(les)), "x"))
    assert "08:54" in m.title and "🏃" in m.title
    assert "10:15" in m.body and len(m.body) <= 140 and m.body.endswith("в пути.")
    assert m.data["exit"] == "08:54"
    e = format_push_evening(build_evening(_sched(les, _les(12, 20)), "халат"))
    assert "3" not in e.title and "2 пары" in e.title and "халат" in e.body
    e2 = format_push_evening(build_evening(_sched(les, _les(12, 20)), "халат"), variant="b")
    assert e.title != e2.title  # variants differ


def test_push_empty_and_error():
    p = format_push_evening(build_evening(_sched(), ""))
    assert "без пар" in p.title and p.data["count"] == 0
    p2 = format_push_morning(build_morning(_les(10, 15), None, route_failed=True))
    assert "посчитать не вышло" in p2.body and "Выйти" not in p2.title


def test_day_view_unified():
    d = build_evening(_sched(_les(8, 30)), "x")
    t = format_telegram_day("Сегодня, 24.09", d, "🏃 Выйти в 07:14")
    assert t.startswith("📅 Сегодня, 24.09 — 1 пара")
    assert t.rstrip().endswith("🏃 Выйти в 07:14")
    assert "пар нет" in format_telegram_day("Сегодня, 24.09", build_evening(_sched(), ""))
