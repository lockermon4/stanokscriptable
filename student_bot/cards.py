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
    end: str = ""  # "11:50" (конец пары; пусто — показать только начало)


def _end_str(lesson) -> str:
    """Конец пары 'HH:MM' (неизвестен → +90 мин от начала)."""
    from datetime import timedelta

    end = lesson.ends_at or (lesson.starts_at + timedelta(minutes=90))
    return end.strftime("%H:%M")


def _range(lesson) -> str:
    """'10:15–11:50': начало и конец пары."""
    return f"{lesson.starts_at.strftime('%H:%M')}–{_end_str(lesson)}"


def _lr(line) -> str:
    """Диапазон для готовой строки: '10:15–11:50' (без конца — как было).
    Утиный тип: LessonLine и MorningData (поля time/end)."""
    return f"{line.time}–{line.end}" if line.end else line.time


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
    end: str = ""  # конец первой пары
    subject: str = ""
    place: str = ""  # raw room, e.g. "ИГ-1" (ауд./room added at render)
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
        LessonLine(time=l.starts_at.strftime("%H:%M"), subject=l.subject, room=l.room,
                   end=_end_str(l))
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
    base = dict(ok=True, time=lesson.starts_at.strftime("%H:%M"), end=_end_str(lesson),
                subject=lesson.subject, place=lesson.room or "", note=note.strip())
    if plan is None:
        err = "unknown_building" if unknown_building else ("route_failed" if route_failed else "")
        return MorningData(route_ok=False, route_error=err, **base)  # type: ignore
    from .exit_time import format_duration

    return MorningData(route_ok=True, travel_txt="~" + format_duration(plan.travel_seconds),
                       exit=plan.exit_at.strftime("%H:%M"), late=plan.already_passed, **base)  # type: ignore


def with_metro(data: MorningData, metro_summary: str) -> MorningData:
    return MorningData(**{**data.__dict__, "metro_txt": metro_summary})


# ---------- Telegram ----------

def format_telegram_evening(d: EveningData, lang: str = "ru") -> str:
    en = lang == "en"
    if not d.ok:
        return "🌙 Couldn't fetch tomorrow's timetable. Try later." if en else \
            "🌙 Не получилось получить расписание на завтра. Попробуйте позже."
    if not d.lessons:
        t = "🌙 No classes tomorrow — enjoy!" if en else "🌙 Завтра пар нет — отдыхайте!"
        note = f"\n🎒 Note: {d.note}" if en else f"\n🎒 Заметка: {d.note}"
        return t if not d.note else t + note
    head = f"🌙 Tomorrow — {len(d.lessons)} {plural(len(d.lessons), lang)}" if en else \
        f"🌙 Завтра — {len(d.lessons)} {plural(len(d.lessons), lang)}"
    lines = [head]
    lines += [f"{_lr(l)} — {l.subject}" + (f", {_room(l.room, lang)}" if l.room else "")
              for l in d.lessons]
    if d.note:
        lines.append(f"🎒 Take: {d.note}" if en else f"🎒 Взять: {d.note}")
    return "\n".join(lines)


def plural(n: int, lang: str = "ru") -> str:
    if lang == "en":
        return "class" if abs(n) == 1 else "classes"
    n = abs(n) % 100
    d = n % 10
    if 11 <= n <= 14:
        return "пар"
    return "пара" if d == 1 else ("пары" if 2 <= d <= 4 else "пар")


def _room(room: str, lang: str) -> str:
    if not room:
        return ""
    return f"room {room}" if lang == "en" else f"ауд. {room}"


def format_telegram_morning(d: MorningData, lang: str = "ru") -> str:
    en = lang == "en"
    if not d.ok:
        return "🌅 Couldn't fetch the timetable. Try later." if en else \
            "🌅 Не получилось получить расписание. Попробуйте позже."
    if not d.time:
        return "🌅 No more classes today." if en else "🌅 Сегодня пар больше нет."
    head = f"🏃 Leave at {d.exit}" if (d.route_ok and en) else \
        (f"🏃 Выйти в {d.exit}" if d.route_ok else
         ("🏃 Couldn't calculate when to leave" if en else "🏃 Во сколько выйти — посчитать не вышло"))
    lines = [head, f"{_lr(d)} — {d.subject}" + (f", {_room(d.place, lang)}" if d.place else "")]
    if d.route_ok:
        ride = d.travel_txt + (f" ({d.metro_txt})" if d.metro_txt else "")
        lines.append(f"🚇 {ride} travel time" if en else f"🚇 {ride} в пути")
        if d.late:
            lines.append("⚠️ Already past — leave now!" if en else "⚠️ Время уже прошло — выходите сейчас!")
    elif d.route_error == "unknown_building":
        lines.append("⚠️ Building address unknown — exit time not calculated." if en else
                     "⚠️ Адрес корпуса неизвестен — время выхода не посчитано.")
    else:
        lines.append("⚠️ Couldn't calculate the route — leave with spare time." if en else
                     "⚠️ Дорогу посчитать не получилось — выходите с запасом.")
    if d.note:
        lines.append(f"🎒 Take: {d.note}" if en else f"🎒 Взять: {d.note}")
    return "\n".join(lines)


# ---------- Intraday-фокус («Сегодня»): ближайшая пара, на которую можно попасть ----------

def build_focus(lesson, next_lesson, travel_s: int | None, buffer_min: int,
                now, route_ok: bool, lang: str = "ru") -> str:
    """Вердикт по фокусной паре дня. travel_s=None — маршрут неизвестен.
    Никогда не помечает пару «пропущенной» лишь потому, что время выхода прошло."""
    from datetime import timedelta

    en = lang == "en"
    t = _range(lesson)
    subj = lesson.subject
    if travel_s is None or not route_ok:
        if lesson.starts_at <= now < (lesson.ends_at or (lesson.starts_at + timedelta(minutes=90))):
            base = f"🟡 {t} — {subj}: пара уже идёт." if not en else \
                f"🟡 {t} — {subj}: class in progress."
        else:
            base = f"🎯 {t} — {subj}." if not en else f"🎯 {t} — {subj}."
        tail = "Дорогу посчитать не получилось — выходите с запасом." if not en else \
            "Couldn't calculate the route — leave with spare time."
        return f"{base}\n⚠️ {tail}"
    arrival = now + timedelta(seconds=travel_s)
    exit_rec = lesson.starts_at - timedelta(seconds=travel_s, minutes=buffer_min)
    end = lesson.ends_at or (lesson.starts_at + timedelta(minutes=90))
    head = f"🎯 {t} — {subj}"
    if now < lesson.starts_at:
        if exit_rec > now:
            v = f"✅ Успеваешь — выйти в {exit_rec.strftime('%H:%M')}." if not en else \
                f"✅ On track — leave at {exit_rec.strftime('%H:%M')}."
            return f"{head}\n{v}"
        if arrival <= lesson.starts_at:
            v = f"⚠️ Лучше выходить сейчас — приедешь к {arrival.strftime('%H:%M')}." if not en else \
                f"⚠️ Better leave now — arrival at {arrival.strftime('%H:%M')}."
            return f"{head}\n{v}"
        if arrival < end:
            left = int((end - arrival).total_seconds() // 60)
            v = (f"🔴 К началу уже не успеть. Выйдя сейчас, приедешь в "
                 f"{arrival.strftime('%H:%M')}, останется ~{left} мин.") if not en else \
                (f"🔴 Can't make the start. Leaving now gets you there at "
                 f"{arrival.strftime('%H:%M')}, ~{left} min left.")
            return f"{head}\n{v}"
        nxt = ""
        if next_lesson is not None:
            nxt = (f"\nСледующая: {_range(next_lesson)} — "
                   f"{next_lesson.subject}.") if not en else \
                (f"\nNext: {_range(next_lesson)} — {next_lesson.subject}.")
        v = "🔴 Даже выйдя сейчас, к концу не успеть." if not en else \
            "🔴 Even leaving now won't make it before the end."
        return f"{head}\n{v}{nxt}"
    # ongoing
    if arrival < end:
        left = int((end - arrival).total_seconds() // 60)
        v = (f"🟡 Пара уже идёт. Выйдя сейчас, приедешь в {arrival.strftime('%H:%M')}, "
             f"останется ~{left} мин.") if not en else \
            (f"🟡 Class in progress. Leaving now gets you there at "
             f"{arrival.strftime('%H:%M')}, ~{left} min left.")
        return f"{head}\n{v}"
    nxt = ""
    if next_lesson is not None:
        nxt = (f"\nСледующая: {_range(next_lesson)} — "
               f"{next_lesson.subject}.") if not en else \
            (f"\nNext: {_range(next_lesson)} — {next_lesson.subject}.")
    v = "🔴 К этой уже не успеть." if not en else "🔴 Too late for this one."
    return f"{head}\n{v}{nxt}"


def format_telegram_day(label: str, d: EveningData, exit_line: str = "", lang: str = "ru") -> str:
    """Дневное расписание («Сегодня»/«Завтра») в том же стиле."""
    en = lang == "en"
    if not d.ok:
        return f"📅 {label}: couldn't fetch the timetable. Try later." if en else \
            f"📅 {label}: не получилось получить расписание. Попробуйте позже."
    if not d.lessons:
        t = f"📅 {label}: no classes — enjoy!" if en else f"📅 {label}: пар нет — отдыхайте!"
        note = f"\n🎒 Note: {d.note}" if en else f"\n🎒 Заметка: {d.note}"
        return t if not d.note else t + note
    lines = [f"📅 {label} — {len(d.lessons)} {plural(len(d.lessons), lang)}"]
    lines += [f"{_lr(l)} — {l.subject}" + (f", {_room(l.room, lang)}" if l.room else "")
              for l in d.lessons]
    if d.note:
        lines.append(f"🎒 Note: {d.note}" if en else f"🎒 Заметка: {d.note}")
    if exit_line:
        lines.append(exit_line)
    return "\n".join(lines)


def format_day_list(schedule, now, lang: str = "ru") -> str:
    """Всё расписание дня одной простынёй: ◾ прошла, 🟡 идёт, ▫️ будет.
    Чистая функция — «Сегодня» всегда показывает и фокус, и весь день."""
    from datetime import timedelta

    en = lang == "en"
    if not schedule.active_lessons:
        return "No classes today." if en else "Сегодня пар нет."
    lines = []
    for les in schedule.active_lessons:
        end = les.ends_at or (les.starts_at + timedelta(minutes=90))
        if end <= now:
            mark = "◾"
        elif les.starts_at <= now:
            mark = "🟡"
        else:
            mark = "▫️"
        room = (f", {_room(les.room, lang)}" if les.room else "")
        lines.append(f"{mark} {_range(les)} — {les.subject}{room}")
    return "\n".join(lines)


# ---------- Push-шаблоны (iOS; отправки нет, только тексты) ----------

def format_push_evening(d: EveningData, variant: str = "a", lang: str = "ru") -> PushMsg:
    en = lang == "en"
    if not d.ok:
        return PushMsg("Timetable pending ⏳" if en else "Расписание на завтра ⏳",
                       "Couldn't fetch — open the bot later." if en else
                       "Не получилось получить — откройте бота позже.",
                       {"kind": "evening", "ok": False, "day": d.day})
    if not d.lessons:
        return PushMsg("No classes tomorrow 🌙" if en else "Завтра без пар 🌙",
                       "No classes. Enjoy!" if en else "Занятий нет. Отдыхайте!",
                       {"kind": "evening", "ok": True, "count": 0, "day": d.day})
    first = d.lessons[0]
    subj = short(first.subject, PUSH_SUBJECT_LEN)
    take = (f". Take: {short(d.note, 60)}" if en else f". Взять: {short(d.note, 60)}") \
        if d.note else ""
    room = f" ({first.room})" if first.room else ""
    if variant == "b":
        n = len(d.lessons)
        title = f"📚 {n} {plural(n, lang)} " + ("tomorrow" if en else "завтра")
        body = (f"First at {_lr(first)}{room}{take}" if en else f"Первая в {_lr(first)}{room}{take}")
    else:
        n = len(d.lessons)
        title = (f"Tomorrow: {n} {plural(n, lang)} 📚" if en else f"Завтра: {n} {plural(n, lang)} 📚")
        body = f"{_lr(first)} {subj}{take}"
    return PushMsg(title, body, {"kind": "evening", "ok": True, "count": len(d.lessons),
                                 "first_at": first.time, "day": d.day,
                                 "note": bool(d.note)})


def format_push_morning(d: MorningData, variant: str = "a", lang: str = "ru") -> PushMsg:
    en = lang == "en"
    if not d.ok or not d.time:
        return PushMsg("No classes today 🌅" if en else "Пар сегодня нет 🌅",
                       "No classes left." if en else "Актуальных занятий не осталось.",
                       {"kind": "morning", "ok": d.ok})
    subj = short(d.subject, PUSH_SUBJECT_LEN)
    span = _lr(d)
    if not d.route_ok:
        return PushMsg(f"Class at {span} 🔔" if en else f"Пара в {span} 🔔",
                       f"{subj}. Couldn't calculate the route — leave with spare time." if en else
                       f"{subj}. Дорогу посчитать не вышло — выходите с запасом.",
                       {"kind": "morning", "route_ok": False, "at": d.time})
    room = f", room {d.place}" if (en and d.place) else (f", ауд. {d.place}" if d.place else "")
    if variant == "b":
        title = f"🏃 {d.exit} — exit" if en else f"🏃 {d.exit} — выход"
        body = (f"Class at {span}{room}. Travel {d.travel_txt}." if en else
                f"Пара в {span}{room}. Дорога {d.travel_txt}.")
    else:
        title = f"Leave at {d.exit} 🏃" if en else f"Выйти в {d.exit} 🏃"
        body = (f"{span} {subj}, {d.travel_txt} travel." if en else
                f"{span} {subj}, {d.travel_txt} в пути.")
    if d.late:
        body += " Already past!" if en else " Время уже прошло!"
    return PushMsg(title, body, {"kind": "morning", "route_ok": True, "exit": d.exit,
                                 "at": d.time, "late": d.late})
