"""Единый слой сообщений: проверенные данные отдельно, оформление — отдельно.

Telegram и будущий iOS-клиент получают одни и те же структуры (EveningData /
MorningData / DayData), а форматируют их под себя: format_telegram() и
format_push(). Push НЕ работает — нет APNs-ключа и сервиса отправки; здесь
только шаблоны (заголовок + короткий текст с ключевым временем).

Правила: короткий заголовок с эмодзи-подсказкой, затем главное, без длинного
технического текста. Эмодзи — подсказки, не замена тексту. Если маршрут не
рассчитан — прямой текст об этом, никакого выдуманного времени выхода.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PUSH_SUBJECT_LEN = 34


def short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ---------- данные (общие для Telegram и iOS) ----------

@dataclass(frozen=True)
class LessonLine:
    time: str  # "10:15"
    subject: str
    room: str = ""  # "0209" (без "ауд.")


@dataclass(frozen=True)
class EveningData:
    day: str  # "2026-09-25"
    ok: bool  # расписание получено
    lessons: tuple[LessonLine, ...] = ()
    first_place: str = ""  # "ауд. ИГ-1"
    note: str = ""


@dataclass(frozen=True)
class MorningData:
    ok: bool  # расписание получено
    time: str = ""  # начало первой пары
    subject: str = ""
    place: str = ""  # "ауд. ИГ-1"
    route_ok: bool = False
    route_error: str = ""  # "unknown_building" | "route_failed" | ""
    travel_txt: str = ""  # "~1 ч 10 мин"
    metro_txt: str = ""  # коротко: "метро Коньково → Савёловская, пересадка Рижская 6→11"
    exit: str = ""  # "08:54"
    late: bool = False
    note: str = ""


@dataclass(frozen=True)
class PushMsg:
    title: str
    body: str
    data: dict = field(default_factory=dict)  # ключевые поля дублируются для клиента


# ---------- построение данных из доменных объектов ----------

def build_evening(schedule, note: str) -> EveningData:
    lessons = tuple(
        LessonLine(time=l.starts_at.strftime("%H:%M"), subject=l.subject, room=l.room)
        for l in schedule.active_lessons
    )
    first_place = f"ауд. {lessons[0].room}" if lessons and lessons[0].room else ""
    return EveningData(day=schedule.day.isoformat(), ok=True,
                       lessons=lessons, first_place=first_place, note=note.strip())


def evening_failed(day: str) -> EveningData:
    return EveningData(day=day, ok=False)


def build_morning(lesson, plan, note: str = "", *,
                  route_failed: bool = False, unknown_building: bool = False) -> MorningData:
    """lesson — первая актуальная пара (или None). plan — ExitPlan или None."""
    if lesson is None:
        return MorningData(ok=True)
    base = dict(ok=True, time=lesson.starts_at.strftime("%H:%M"), subject=lesson.subject,
                place=f"ауд. {lesson.room}" if lesson.room else "", note=note.strip())
    if plan is None:
        err = "unknown_building" if unknown_building else ("route_failed" if route_failed else "")
        return MorningData(route_ok=False, route_error=err, **base)  # type: ignore
    from .exit_time import format_duration

    return MorningData(route_ok=True, travel_txt="~" + format_duration(plan.travel_seconds),
                       exit=plan.exit_at.strftime("%H:%M"), late=plan.already_passed, **base)  # type: ignore


def with_metro(data: MorningData, metro_summary: str) -> MorningData:
    return MorningData(**{**data.__dict__, "metro_txt": metro_summary})


# ---------- Telegram ----------

def format_telegram_evening(d: EveningData) -> str:
    if not d.ok:
        return "🌙 Не получилось получить расписание на завтра. Попробуйте позже."
    if not d.lessons:
        t = "🌙 Завтра пар нет — отдыхайте!"
        return t if not d.note else t + f"\n🎒 Заметка: {d.note}"
    lines = [f"🌙 Завтра — {len(d.lessons)} {plural(len(d.lessons))}"]
    lines += [f"{l.time} — {l.subject}" + (f", ауд. {l.room}" if l.room else "")
              for l in d.lessons]
    if d.note:
        lines.append(f"🎒 Взять: {d.note}")
    return "\n".join(lines)


def plural(n: int) -> str:
    n = abs(n) % 100
    d = n % 10
    if 11 <= n <= 14:
        return "пар"
    return "пара" if d == 1 else ("пары" if 2 <= d <= 4 else "пар")


def format_telegram_morning(d: MorningData) -> str:
    if not d.ok:
        return "🌅 Не получилось получить расписание. Попробуйте позже."
    if not d.time:
        return "🌅 Сегодня пар больше нет."
    head = f"🏃 Выйти в {d.exit}" if d.route_ok else "🏃 Во сколько выйти — посчитать не вышло"
    lines = [head, f"{d.time} — {d.subject}" + (f", {d.place}" if d.place else "")]
    if d.route_ok:
        ride = d.travel_txt + (f" ({d.metro_txt})" if d.metro_txt else "")
        lines.append(f"🚇 {ride} в пути")
        if d.late:
            lines.append("⚠️ Время уже прошло — выходите сейчас!")
    elif d.route_error == "unknown_building":
        lines.append("⚠️ Адрес корпуса неизвестен — время выхода не посчитано.")
    else:
        lines.append("⚠️ Дорогу посчитать не получилось — выходите с запасом.")
    if d.note:
        lines.append(f"🎒 Взять: {d.note}")
    return "\n".join(lines)


def format_telegram_day(label: str, d: EveningData, exit_line: str = "") -> str:
    """Дневное расписание («Сегодня»/«Завтра») в том же стиле."""
    if not d.ok:
        return f"📅 {label}: не получилось получить расписание. Попробуйте позже."
    if not d.lessons:
        t = f"📅 {label}: пар нет — отдыхайте!"
        return t if not d.note else t + f"\n🎒 Заметка: {d.note}"
    lines = [f"📅 {label} — {len(d.lessons)} {plural(len(d.lessons))}"]
    lines += [f"{l.time} — {l.subject}" + (f", ауд. {l.room}" if l.room else "")
              for l in d.lessons]
    if d.note:
        lines.append(f"🎒 Заметка: {d.note}")
    if exit_line:
        lines.append(exit_line)
    return "\n".join(lines)


# ---------- Push-шаблоны (iOS; отправки нет, только тексты) ----------

def format_push_evening(d: EveningData, variant: str = "a") -> PushMsg:
    if not d.ok:
        return PushMsg("Расписание на завтра ⏳", "Не получилось получить — откройте бота позже.",
                       {"kind": "evening", "ok": False, "day": d.day})
    if not d.lessons:
        return PushMsg("Завтра без пар 🌙", "Занятий нет. Отдыхайте!",
                       {"kind": "evening", "ok": True, "count": 0, "day": d.day})
    first = d.lessons[0]
    subj = short(first.subject, PUSH_SUBJECT_LEN)
    if variant == "b":
        title, body = f"📚 {len(d.lessons)} {plural(len(d.lessons))} завтра", \
            f"Первая в {first.time}" + (f" ({first.room})" if first.room else "") + \
            (f". Взять: {short(d.note, 60)}" if d.note else "")
    else:
        title, body = f"Завтра: {len(d.lessons)} {plural(len(d.lessons))} 📚", \
            f"{first.time} {subj}" + (f". Взять: {short(d.note, 60)}" if d.note else "")
    return PushMsg(title, body, {"kind": "evening", "ok": True, "count": len(d.lessons),
                                 "first_at": first.time, "day": d.day,
                                 "note": bool(d.note)})


def format_push_morning(d: MorningData, variant: str = "a") -> PushMsg:
    if not d.ok or not d.time:
        return PushMsg("Пар сегодня нет 🌅", "Актуальных занятий не осталось.",
                       {"kind": "morning", "ok": d.ok})
    subj = short(d.subject, PUSH_SUBJECT_LEN)
    if not d.route_ok:
        return PushMsg(f"Пара в {d.time} 🔔", f"{subj}. Дорогу посчитать не вышло — выходите с запасом.",
                       {"kind": "morning", "route_ok": False, "at": d.time})
    if variant == "b":
        title, body = f"🏃 {d.exit} — выход", \
            f"Пара в {d.time}" + (f", {d.place}" if d.place else "") + f". Дорога {d.travel_txt}."
    else:
        title, body = f"Выйти в {d.exit} 🏃", f"{d.time} {subj}, {d.travel_txt} в пути."
    if d.late:
        body += " Время уже прошло!"
    return PushMsg(title, body, {"kind": "morning", "route_ok": True, "exit": d.exit,
                                 "at": d.time, "late": d.late})
