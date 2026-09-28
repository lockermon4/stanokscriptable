"""Все тексты бота на русском и английском + инлайн-кнопки.
Язык: UserSettings.lang ("ru"/"en"); новым берётся из Telegram locale.

Чистые функции без состояния (тестируются напрямую). Callback-data короткие,
префиксы: set:* (настройки), tr:* (транспорт), buf:* (запас), ntf* (уведомления),
lang:* (язык), note:*/nadd:*|nview:*|ndel:* (заметки), op:cancel (отмена ввода).
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

RU = "ru"
EN = "en"


def norm_lang(v: str | None) -> str:
    return EN if (v or "").lower().startswith("en") else RU


def minutes(n: int, lang: str) -> str:
    if lang == EN:
        return f"{n} minute" if n == 1 else f"{n} minutes"
    d = abs(n) % 10
    h = abs(n) % 100
    w = "минута" if d == 1 and h != 11 else ("минуты" if 2 <= d <= 4 and not 12 <= h <= 14 else "минут")
    return f"{n} {w}"


TRANSPORT = {
    "walk": (RU, "пешком", "on foot"),
    "metro": (RU, "на метро", "by metro"),
}


def transport_name(code: str, lang: str) -> str:
    from .store import norm_transport

    code = norm_transport(code)
    for k, (_, ru, en) in TRANSPORT.items():
        if k == code:
            return en if lang == EN else ru
    return code


def ikb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
                         for row in rows])


# Кнопки меню: (ru, en, legacy_aliases)
MENU_TODAY = ("📅 Сегодня", "📅 Today", ("Сегодня",))
MENU_TOMORROW = ("🗓 Завтра", "🗓 Tomorrow", ("Завтра",))
MENU_LEAVE = ("🚪 Когда выходить", "🚪 When to leave", ("Когда выходить?", "Когда выходить"))
MENU_NOTES = ("📝 Заметки", "📝 Notes", ("Заметка на день", "Заметка"))
MENU_SETTINGS = ("⚙️ Настройки", "⚙️ Settings", ("Настройки",))
MENU_ROUTES = ("⭐ Маршруты", "⭐ Routes", ("Маршруты", "Мои маршруты"))


def menu_kb(lang: str) -> list[list[str]]:
    ru = lang != EN
    return [[MENU_TODAY[0] if ru else MENU_TODAY[1], MENU_TOMORROW[0] if ru else MENU_TOMORROW[1]],
            [MENU_LEAVE[0] if ru else MENU_LEAVE[1], MENU_ROUTES[0] if ru else MENU_ROUTES[1]],
            [MENU_NOTES[0] if ru else MENU_NOTES[1], MENU_SETTINGS[0] if ru else MENU_SETTINGS[1]]]


def menu_match() -> dict[str, str]:
    """button text -> action id (оба языка + старые тексты)."""
    out = {}
    for aid, item in (("today", MENU_TODAY), ("tomorrow", MENU_TOMORROW), ("leave", MENU_LEAVE),
                      ("notes", MENU_NOTES), ("settings", MENU_SETTINGS), ("routes", MENU_ROUTES)):
        for t in (item[0], item[1], *item[2]):
            out[t] = aid
    return out


# ---------- /start ----------

def start_route(has_group: bool, has_home: bool) -> str:
    """Which /start branch: back | need_home | need_group | new."""
    if has_group and has_home:
        return "back"
    if has_group:
        return "need_home"
    if has_home:
        return "need_group"
    return "new"


def start_new(lang: str) -> str:
    if lang == EN:
        return ("Hi! I show the STANKIN timetable and calculate when to leave home "
                "to catch your first class. 📚🏃\n\nFirst, choose your group — type it, "
                "e.g. ИДБ-26-14")
    return ("Привет! Я показываю расписание СТАНКИН и считаю, во сколько выйти "
            "из дома, чтобы успеть на первую пару. 📚🏃\n\nСначала выберите группу — "
            "напишите её, например: ИДБ-26-14")


def start_back(lang: str) -> str:
    return "Welcome back! 👋" if lang == EN else "С возвращением! 👋"


def main_menu_text(lang: str) -> str:
    """Главное меню после выхода из подраздела (кнопка «Назад»)."""
    if lang == EN:
        return ("🏠 Main menu — pick an action below:\n"
                "📅 Today · 🗓 Tomorrow · 🚪 When to leave · ⭐ Routes · 📝 Notes · ⚙️ Settings")
    return ("🏠 Главное меню — выберите действие кнопками ниже:\n"
            "📅 Сегодня · 🗓 Завтра · 🚪 Когда выходить · ⭐ Маршруты · 📝 Заметки · ⚙️ Настройки")


def start_need_home(lang: str, group: str) -> str:
    if lang == EN:
        return (f"Welcome back! Your group is {group}. One step left: add your home "
                f"address — type it or send a location pin (📎 → Location).")
    return (f"С возвращением! Ваша группа — {group}. Остался один шаг: добавьте "
            f"домашний адрес — напишите текстом или отправьте точку (скрепка → Геопозиция).")


def start_need_group(lang: str) -> str:
    if lang == EN:
        return ("Welcome back! Your home is saved. Now choose your group — type it, "
                "e.g. ИДБ-26-14")
    return ("С возвращением! Дом сохранён. Теперь выберите группу — напишите её, "
            "например: ИДБ-26-14")


def ask_address(lang: str, group: str) -> str:
    if lang == EN:
        return (f"Group: {group}. Now your home address — type it in one line or "
                f"send a location pin (📎 → Location).")
    return (f"Группа: {group}. Теперь домашний адрес — напишите одной строкой "
            f"или отправьте точку (скрепка → Геопозиция).")


def need_group_first(lang: str) -> str:
    return "First choose your group via /start." if lang == EN else \
        "Сначала укажите группу через /start."


def need_home(lang: str) -> str:
    if lang == EN:
        return ("Add your home address first: type it (e.g. \"ul. Vadkovskii pereulok, 3\") "
                "or send a location pin (📎 → Location). I can't calculate the exit time without it.")
    return ("Сначала добавьте домашний адрес: напишите его текстом "
            "(например, «ул. Вадковский переулок, 1») или отправьте точку "
            "(скрепка → Геопозиция). Без него время выхода посчитать не выйдет.")


# ---------- настройки ----------

def settings_view(group: str, home: str, transport: str, buffer_min: int,
                  evening: str, morn_min: int, lang: str) -> str:
    """Текущие значения человеческим языком + подсказка про кнопки."""
    if lang == EN:
        return (f"⚙️ Settings\nGroup: {group or '—'}\nHome: {home or '—'}\n"
                f"Transport: {transport_name(transport, lang)}\n"
                f"Buffer: {minutes(buffer_min, lang)}\n"
                f"Evening reminder: {evening}\n"
                f"Morning reminder: {minutes(morn_min, lang)} before leaving\n\n"
                f"Use the buttons below to change anything.")
    return (f"⚙️ Настройки\nГруппа: {group or '—'}\nДом: {home or '—'}\n"
            f"Способ передвижения: {transport_name(transport, lang)}\n"
            f"Запас: {minutes(buffer_min, lang)}\n"
            f"Вечернее уведомление: {evening}\n"
            f"Утреннее уведомление: за {minutes(morn_min, lang)} до выхода\n\n"
            f"Чтобы изменить — кнопки ниже.")


def settings_buttons(lang: str):
    if lang == EN:
        return ikb([[("Change group", "set:group"), ("Change address", "set:address")],
                    [("Transport", "set:transport"), ("Time buffer", "set:buffer")],
                    [("Notifications", "set:notify"), ("Language", "set:lang")],
                    [("🔗 iOS key", "set:ioskey")],
                    [("◀️ Back", "set:back")]])
    return ikb([[("Изменить группу", "set:group"), ("Изменить адрес", "set:address")],
                [("Способ передвижения", "set:transport"), ("Запас времени", "set:buffer")],
                [("Время уведомлений", "set:notify"), ("Язык", "set:lang")],
                [("🔗 Ключ для iOS", "set:ioskey")],
                [("◀️ Назад", "set:back")]])


def transport_buttons(lang: str):
    if lang == EN:
        rows = [[("🚶 On foot", "tr:walk")], [("🚇 By metro", "tr:metro")],
                [("◀️ Back", "set:menu")]]
    else:
        rows = [[("🚶 Пешком", "tr:walk")], [("🚇 Метро", "tr:metro")],
                [("◀️ Назад", "set:menu")]]
    return ikb(rows)


def buffer_buttons(lang: str):
    rows = [[(minutes(n, lang), f"buf:{n}") for n in (5, 10, 15)],
            [(minutes(n, lang), f"buf:{n}") for n in (20, 30)]]
    rows.append([("◀️ Back", "set:menu")] if lang == EN else [("◀️ Назад", "set:menu")])
    return ikb(rows)


def notify_menu_buttons(lang: str):
    if lang == EN:
        return ikb([[("Evening time", "ntfmenu:eve"), ("Morning lead", "ntfmenu:morn")],
                    [("◀️ Back", "set:menu")]])
    return ikb([[("Вечернее время", "ntfmenu:eve"), ("Утреннее напоминание", "ntfmenu:morn")],
                [("◀️ Назад", "set:menu")]])


def evening_buttons(lang: str):
    rows = [[(t, f"ntf:eve:{t}") for t in ("20:00", "20:30", "21:00")],
            [(t, f"ntf:eve:{t}") for t in ("21:30", "22:00")]]
    rows.append([("◀️ Back", "set:notify")] if lang == EN else [("◀️ Назад", "set:notify")])
    return ikb(rows)


def morning_lead_buttons(lang: str):
    rows = [[(minutes(n, lang), f"ntf:morn:{n}") for n in (15, 30, 45)],
            [(minutes(n, lang), f"ntf:morn:{n}") for n in (60, 90, 120)]]
    rows.append([("◀️ Back", "set:notify")] if lang == EN else [("◀️ Назад", "set:notify")])
    return ikb(rows)


def lang_buttons():
    return ikb([[("Русский", "lang:ru"), ("English", "lang:en")],
                [("◀️ Назад", "set:menu")]])


def cancel_buttons(lang: str):
    return ikb([[("Отмена", "op:cancel")]] if lang != EN else [[("Cancel", "op:cancel")]])


def ask_new_group(lang: str) -> str:
    if lang == EN:
        return "Send the new group name, e.g. ИДБ-26-14 (or press Cancel)."
    return "Пришлите новое название группы, например ИДБ-26-14 (или нажмите «Отмена»)."


def ask_new_address(lang: str) -> str:
    if lang == EN:
        return ("Send the new home address in one line or a location pin (📎 → Location). "
                "I'll verify it, save the coordinates and recalculate from the new home.")
    return ("Пришлите новый домашний адрес одной строкой или точку (скрепка → Геопозиция). "
            "Проверю его, сохраню координаты и пересчитаю дорогу от нового дома.")


def addr_saved_new(lang: str, label: str) -> str:
    if lang == EN:
        return (f"Home saved: {label}.\nOld results are discarded — everything below "
                f"is calculated from the new home.")
    return (f"Дом сохранён: {label}.\nСтарые результаты сброшены — всё ниже "
            f"посчитано от нового дома.")

def addr_not_found(lang: str) -> str:
    if lang == EN:
        return ("Couldn't verify that address, so I did NOT save it and did NOT "
                "recalculate. Send it more precisely or send a location pin.")
    return ("Не смог проверить этот адрес, поэтому НЕ сохранил его и НЕ пересчитывал. "
            "Пришлите точнее или отправьте точку.")


def ask_transport(lang: str) -> str:
    return "Choose how you get to classes:" if lang == EN else "Выберите, как добираетесь до пар:"


def ask_buffer(lang: str) -> str:
    return "Choose the safety buffer:" if lang == EN else "Выберите запас времени:"


def ask_notify(lang: str) -> str:
    return "Which reminder to change?" if lang == EN else "Какое уведомление меняем?"


def ask_evening(lang: str) -> str:
    return "Evening reminder time:" if lang == EN else "Время вечернего уведомления:"


def ask_morning_lead(lang: str) -> str:
    return "How far before leaving to remind in the morning?" if lang == EN \
        else "За сколько до выхода напоминать утром?"


def ask_lang() -> str:
    return "Язык / Language:"


def recalc_failed(lang: str) -> str:
    if lang == EN:
        return "Saved, but the route from the new home can't be calculated yet — no old numbers shown."
    return "Сохранено, но дорогу от нового дома пока посчитать не вышло — старые цифры не показываю."


# ---------- заметки ----------

def notes_menu_text(lang: str) -> str:
    return "📝 Notes: add, view or delete." if lang == EN else "📝 Заметки: добавить, посмотреть, удалить."


def notes_menu_buttons(lang: str):
    if lang == EN:
        return ikb([[("➕ Add note", "note:add")],
                    [("👁 View", "note:view"), ("🗑 Delete", "note:del")],
                    [("◀️ Back", "note:back")]])
    return ikb([[("➕ Добавить заметку", "note:add")],
                [("👁 Посмотреть", "note:view"), ("🗑 Удалить", "note:del")],
                [("◀️ Назад", "note:back")]])


def note_date_buttons(lang: str, prefix: str):
    """prefix: nadd | nview | ndel. RU-метки на русском (фикс)."""
    if lang == EN:
        rows = [
            [("Today", f"{prefix}:today"), ("Tomorrow", f"{prefix}:tomorrow")],
            [("Pick a date", f"{prefix}:custom")],
            [("Cancel", "note:menu")],
        ]
    else:
        rows = [
            [("Сегодня", f"{prefix}:today"), ("Завтра", f"{prefix}:tomorrow")],
            [("Выбрать дату", f"{prefix}:custom")],
            [("Отмена", "note:menu")],
        ]
    return ikb(rows)


def ask_note_date(lang: str) -> str:
    return "Which date is the note for?" if lang == EN else "На какую дату заметка?"


def ask_custom_date(lang: str) -> str:
    return "Send the date as YYYY-MM-DD." if lang == EN else "Пришлите дату в виде ГГГГ-ММ-ДД."


def ask_note_text(lang: str, date_label: str) -> str:
    if lang == EN:
        return f"Note for {date_label}. What to take or do?"
    return f"Заметка на {date_label}. Что нужно взять или сделать?"


def note_saved(lang: str, date_label: str, text: str) -> str:
    if lang == EN:
        return f"✅ Saved for {date_label}: {text}"
    return f"✅ Сохранено на {date_label}: {text}"


def note_card(lang: str, date_label: str, note: str) -> str:
    body = note if note else ("(empty)" if lang == EN else "(пусто)")
    if lang == EN:
        return f"📝 {date_label}: {body}"
    return f"📝 {date_label}: {body}"


def note_item_buttons(lang: str, date_iso: str):
    if lang == EN:
        return ikb([[("✏️ Edit", f"note:edit:{date_iso}"), ("🗑 Delete", f"note:delone:{date_iso}")],
                    [("◀️ Back", "note:menu")]])
    return ikb([[("✏️ Изменить", f"note:edit:{date_iso}"), ("🗑 Удалить", f"note:delone:{date_iso}")],
                [("◀️ Назад", "note:menu")]])


def note_confirm_delete(lang: str, date_iso: str, date_label: str, note: str):
    txt = note_card(lang, date_label, note)
    if lang == EN:
        return txt + "\nDelete this note?", ikb([[("Yes, delete", f"note:del:yes:{date_iso}"),
                                                  ("Cancel", "note:menu")]])
    return txt + "\nУдалить эту заметку?", ikb([[("Да, удалить", f"note:del:yes:{date_iso}"),
                                                 ("Отмена", "note:menu")]])


def note_deleted(lang: str, date_label: str) -> str:
    return f"🗑 Deleted note for {date_label}." if lang == EN else f"🗑 Заметка на {date_label} удалена."


def pretty_street(raw: str, fallback: str) -> tuple[str, str | None]:
    """Capitalize street name and extract type from raw address or fallback."""
    from .address_check import _parse_street_token
    nm, tp = _parse_street_token(raw or "")
    name = (nm or "").strip() if nm else ""
    return (name[:1].upper() + name[1:] if name else fallback, tp)


# ---------- маршруты 2GIS: выбор типа, варианты, детали, избранное ----------

def mode_buttons(lang: str):
    """🚶 Пешком / 🚇 Метро — первый шаг построения маршрута."""
    if lang == EN:
        return ikb([[("🚶 On foot", "rtm:walk"), ("🚇 By metro", "rtm:metro")],
                    [("◀️ Back", "rt:cancel")]])
    return ikb([[("🚶 Пешком", "rtm:walk"), ("🚇 Метро", "rtm:metro")],
                [("◀️ Назад", "rt:cancel")]])


def ask_route_mode(lang: str, lesson_line: str) -> str:
    if lang == EN:
        return f"{lesson_line}\nHow are you going?"
    return f"{lesson_line}\nКак едем?"


def variant_label(opt, idx: int, lang: str) -> str:
    """Короткая подпись варианта для кнопки/списка."""
    from .exit_time import format_duration

    dur = format_duration(opt.duration_s)
    if opt.mode == "metro":
        n = len(opt.steps)
        tail = ""
        if opt.walk_before_s or opt.walk_after_s:
            tail = f", {format_duration(opt.walk_before_s + opt.walk_after_s)} пешком"
        if lang == EN:
            return f"{dur}, {opt.transfers} change(s){tail}"
        ch = "пересадка" if opt.transfers == 1 else ("пересадки" if 2 <= opt.transfers <= 4 else "пересадок")
        return f"{dur}, {opt.transfers} {ch}{tail}"
    km = f"{opt.distance_m / 1000:.1f} км" if opt.distance_m else ""
    return f"{dur} {km}".strip() if lang != EN else f"{dur} {km}".strip()


def variants_text(lang: str, lesson_line: str, options) -> str:
    lines = [lesson_line, ""]
    for i, o in enumerate(options):
        lines.append(f"{i + 1}. {variant_label(o, i, lang)}")
    lines.append("")
    lines.append("Choose an option:" if lang == EN else "Выберите вариант:")
    return "\n".join(lines)


def variants_buttons(options, lang: str):
    rows = [[(f"{i + 1}. {variant_label(o, i, lang)}", f"rtv:{i}")] for i, o in enumerate(options)]
    rows.append([("🎯 Arrive by...", "rt:target")] if lang == EN else
                [("🎯 Приехать к...", "rt:target")])
    rows.append([("◀️ Back", "rt:cancel")] if lang == EN else [("◀️ Назад", "rt:cancel")])
    return ikb(rows)


def target_buttons(lang: str):
    return ikb([[("🎯 Arrive by...", "rt:target")]] if lang == EN else
               [[("🎯 Приехать к...", "rt:target")]])


def ask_target_time(lang: str, lesson_line: str) -> str:
    if lang == EN:
        return (f"{lesson_line}\nWhat time must you ARRIVE? Send as HH:MM, e.g. 08:00.\n"
                f"Exit = arrival − route − buffer.")
    return (f"{lesson_line}\nВо сколько нужно БЫТЬ на месте? Пришли время ЧЧ:ММ, например 08:00.\n"
            f"Выход = прибытие − дорога − запас.")


def _hm(dt) -> str:
    return dt.strftime("%H:%M") if dt is not None else "—"


def night_exit_text(lang: str, lesson_line: str, out) -> str:
    """Результат ночного метро-расчёта (NightOutcome)."""
    from .metro_hours import opens_at_text

    opens = opens_at_text()
    head = lesson_line
    if out.kind == "no_data":
        return f"{head}\n" + route_failed(lang)
    if out.kind == "miss":
        if lang == EN:
            t = (f"{head}\n🔴 Can't make the first class by metro even leaving at {opens} "
                 f"(metro opens at {opens}).")
            if out.walk_exit_at is not None:
                t += (f"\n🚶 But on foot, leaving now: exit at {_hm(out.walk_exit_at)}, "
                      f"arrival ~{_hm(out.walk_arrival_at)}.")
            return t
        t = (f"{head}\n🔴 К первой паре через метро не успеть, даже выйдя в {opens} "
             f"(метро откроется в {opens}).")
        if out.walk_exit_at is not None:
            t += (f"\n🚶 А вот пешком сейчас: выйти в {_hm(out.walk_exit_at)}, "
                  f"приедешь в {_hm(out.walk_arrival_at)}.")
        return t
    # ok / anchored — одинаковая форма, разница лишь в якоре 05:30
    if lang == EN:
        return (f"{head}\n🚇 Leave at {_hm(out.exit_at)} to catch the class "
                f"(metro opens at {opens}).")
    return (f"{head}\n🚇 Выйти в {_hm(out.exit_at)}, чтобы успеть к первой паре "
            f"(метро откроется в {opens}).")


def target_exit_text(lang: str, lesson_line: str, kind: str, exit_at, arrival_at,
                     travel_txt: str, walk_exit_at=None, walk_arrival_at=None) -> str:
    """Результат 'Приехать к HH:MM': ok / anchored / miss / no_data."""
    from .metro_hours import opens_at_text

    opens = opens_at_text()
    if kind == "no_data":
        return f"{lesson_line}\n" + route_failed(lang)
    if kind == "miss":
        if lang == EN:
            t = (f"{lesson_line}\n🔴 Can't arrive by metro in time (even from {opens}).")
            if walk_exit_at is not None:
                t += (f"\n🚶 On foot instead: exit at {_hm(walk_exit_at)}, "
                      f"arrival ~{_hm(walk_arrival_at)}.")
            return t
        t = f"{lesson_line}\n🔴 На метро к этому времени не успеть (даже от {opens})."
        if walk_exit_at is not None:
            t += (f"\n🚶 А пешком: выйти в {_hm(walk_exit_at)}, "
                  f"приедешь в {_hm(walk_arrival_at)}.")
        return t
    extra = ""
    if kind == "anchored":
        extra = f" (метро откроется в {opens})" if lang != EN else \
            f" (metro opens at {opens})"
    if lang == EN:
        return (f"{lesson_line}\n🚇 To arrive by {_hm(arrival_at)}: "
                f"leave at {_hm(exit_at)} ({travel_txt}).{extra}")
    return (f"{lesson_line}\n🚇 Чтобы быть к {_hm(arrival_at)}: "
            f"выйти в {_hm(exit_at)} ({travel_txt}).{extra}")


def addr_confirm_buttons(lang: str):
    if lang == EN:
        return ikb([[("✅ Yes, my home", "addr:yes"), ("❌ Not mine", "addr:no")]])
    return ikb([[("✅ Да, мой дом", "addr:yes"), ("❌ Не мой", "addr:no")]])


def addr_pick_buttons(picks, lang: str):
    """Пронумерованные кнопки вместо ответа цифрой текстом."""
    rows = []
    for i, (lbl, _, _) in enumerate(picks):
        short = lbl if len(lbl) <= 32 else lbl[:31] + "…"
        rows.append([(f"{i + 1}. {short}", f"addrpick:{i}")])
    rows.append([("◀️ Back", "op:cancel")] if lang == EN else [("◀️ Назад", "op:cancel")])
    return ikb(rows)


def route_details(lang: str, opt, exit_line: str = "") -> str:
    """Детали выбранного варианта: станции/ветки метро, улицы пешком."""
    head = variant_label(opt, 0, lang)
    lines = [f"🗺 {head}"]
    if opt.steps:
        lines += [f"• {s}" for s in opt.steps]
    else:
        lines.append("(No step-by-step breakdown.)" if lang == EN else "(Пошагового описания нет.)")
    if exit_line:
        lines += ["", exit_line]
    return "\n".join(lines)


def route_details_buttons(idx: int, lang: str):
    if lang == EN:
        return ikb([[("🏃 Leave now", "rt:now")],
                    [("⭐ Save", f"rt:save:{idx}")], [("◀️ Back", "rt:cancel")]])
    return ikb([[("🏃 Выйти сейчас", "rt:now")],
                [("⭐ Сохранить", f"rt:save:{idx}")], [("◀️ Назад", "rt:cancel")]])


def leave_now_buttons(lang: str):
    if lang == EN:
        return ikb([[("🏃 Leave now", "rt:now")]])
    return ikb([[("🏃 Выйти сейчас", "rt:now")]])


def metro_closed(lang: str) -> str:
    if lang == EN:
        return "🚇 Metro is closed (01:00–05:30), opens at 05:30."
    return "🚇 Метро закрыто (01:00–05:30), откроется в 05:30."


def metro_gray(lang: str) -> str:
    if lang == EN:
        return "⚠️ Last trains: metro runs until 01:00 — double-check your timing."
    return "⚠️ Последние поезда: метро работает до 01:00 — проверь время."


def leave_now_line(lang: str, icon: str, arrival, lesson) -> str:
    """Строка 'приедешь в HH:MM — вердикт' для кнопки 'Выйти сейчас' (чистая)."""
    from datetime import timedelta

    if arrival is None:
        return f"{icon} — не посчиталось, попробуй позже." if lang != EN else \
            f"{icon} — couldn't calculate, try later."
    arr = arrival.strftime("%H:%M")
    start = lesson.starts_at
    end = lesson.ends_at or (lesson.starts_at + timedelta(minutes=90))
    if arrival <= start:
        v = "успеваешь" if lang != EN else "you'll make it"
    elif arrival < end:
        late = int((arrival - start).total_seconds() // 60)
        left = int((end - arrival).total_seconds() // 60)
        v = f"опоздаешь на ~{late} мин, останется ~{left} мин" if lang != EN else \
            f"~{late} min late, ~{left} min left"
    else:
        v = "не успеешь — кончится раньше" if lang != EN else "won't make it"
    return f"{icon} приедешь в {arr} — {v}" if lang != EN else \
        f"{icon} arrival at {arr} — {v}"


def route_saved(lang: str, name: str) -> str:
    return f"⭐ Saved: {name}" if lang == EN else f"⭐ Сохранено: {name}"


def no_metro_fallback(lang: str) -> str:
    if lang == EN:
        return "⚠️ No metro route here — showing on foot."
    return "⚠️ Маршрута на метро нет, показываю пешком."


def route_failed(lang: str) -> str:
    if lang == EN:
        return "⚠️ 2GIS routing failed — try again later."
    return "⚠️ 2GIS не отвечает — попробуйте позже."


def leave_error_text(lang: str, reason: str) -> str:
    if reason == "no_lessons":
        return "🌅 No more classes today." if lang == EN else "🌅 Сегодня пар больше нет."
    if reason == "unknown_building":
        return "⚠️ Building address unknown — exit time not calculated." if lang == EN else \
            "⚠️ Адрес корпуса неизвестен — время выхода не посчитано."
    return "🌅 Couldn't fetch the timetable. Try later." if lang == EN else \
        "🌅 Не получилось получить расписание. Попробуйте позже."


def route_session_expired(lang: str) -> str:
    if lang == EN:
        return "The route expired — press 🚪 When to leave again."
    return "Маршрут устарел — нажмите 🚪 Когда выходить ещё раз."


def fav_list_text(lang: str, favs) -> str:
    if not favs:
        return "⭐ No saved routes yet." if lang == EN else "⭐ Пока нет сохранённых маршрутов."
    head = "⭐ My routes (tap to recalculate live):" if lang == EN else \
        "⭐ Мои маршруты (нажмите — пересчитаю по свежим данным):"
    lines = [head]
    for f in favs:
        mark = "🚇" if f.transport_type == "metro" else "🚶"
        lines.append(f"{mark} {f.name}")
    return "\n".join(lines) + "\n"


def fav_list_buttons(favs, lang: str):
    rows = [[(f"{'🚇' if f.transport_type == 'metro' else '🚶'} {f.name}", f"fav:{f.id}")]
            for f in favs]
    rows.append([("◀️ Back", "rt:cancel")] if lang == EN else [("◀️ Назад", "rt:cancel")])
    return ikb(rows)


def fav_item_buttons(fav_id: int, lang: str):
    if lang == EN:
        return ikb([[("🗑 Delete", f"favdel:{fav_id}")], [("◀️ Back", "rt:cancel")]])
    return ikb([[("🗑 Удалить", f"favdel:{fav_id}")], [("◀️ Назад", "rt:cancel")]])


def fav_confirm_delete(lang: str, name: str, fav_id: int):
    q = f'Delete "{name}"?' if lang == EN else f'Удалить «{name}»?'
    kb = ikb([[("Yes", f"favdel_yes:{fav_id}"), ("Cancel", "rt:cancel")]] if lang == EN else
             [[("Да", f"favdel_yes:{fav_id}"), ("Отмена", "rt:cancel")]])
    return q, kb


def fav_deleted(lang: str, name: str) -> str:
    return f'🗑 Deleted "{name}".' if lang == EN else f'🗑 «{name}» удалён.'


def ios_key_text(lang: str, link: str | None) -> str:
    """Текст с готовой ссылкой для Scriptable."""
    if lang == EN:
        body = ("🔗 iOS key for local notifications (Scriptable).\n\n"
                "Paste this link into your Scriptable script:\n")
        tail = ("\nAnyone with this link reads your timetable — "
                "reissue it below if compromised.")
    else:
        body = ("🔗 Ключ для локальных уведомлений iOS (Scriptable).\n\n"
                "Вставь эту ссылку в Scriptable-скрипт:\n")
        tail = ("\nУ кого есть ссылка — тот видит твоё расписание. "
                "Если скомпрометирован — перевыпусти ниже.")
    if not link:
        no = "Domain not set (PUBLIC_BASE_URL)." if lang == EN else \
            "Домен не настроен (PUBLIC_BASE_URL)."
        return body + no + tail
    return body + link + tail


def ios_key_buttons(lang: str):
    if lang == EN:
        return ikb([[("🔄 Reissue key", "set:ioskey_reissue")],
                    [("◀️ Settings", "set:menu")]])
    return ikb([[("🔄 Перевыпустить ключ", "set:ioskey_reissue")],
                [("◀️ Настройки", "set:menu")]])


def schedule_change_text(lang: str, day_iso: str, changes: list) -> str:
    """Короткое уведомление об изменениях (тип/время/предмет уже в change)."""
    from datetime import date as _date

    try:
        label = _date.fromisoformat(day_iso).strftime("%d.%m")
    except Exception:
        label = day_iso
    lines = []
    for c in changes:
        t, subj, detail = c.get("time", ""), c.get("subject", ""), c.get("detail", "")
        typ = c.get("type", "")
        if lang == EN:
            head = {"removed": f"Class removed: {t} {subj}",
                    "added": f"New class: {t} {subj}",
                    "moved": f"Class moved: {subj}",
                    "room": f"Room changed: {t} {subj}",
                    "teacher": f"Teacher changed: {t} {subj}",
                    "time": f"Time changed: {subj}"}.get(typ, f"Changed: {t} {subj}")
        else:
            head = {"removed": f"Убрали пару: {t} {subj}",
                    "added": f"Добавили пару: {t} {subj}",
                    "moved": f"Пара перенесена: {subj}",
                    "room": f"Кабинет изменился: {t} {subj}",
                    "teacher": f"Преподаватель изменился: {t} {subj}",
                    "time": f"Время изменилось: {subj}"}.get(typ, f"Изменение: {t} {subj}")
        lines.append(head + (f" — {detail}" if detail else ""))
    title = f"⚠️ Schedule {label}:" if lang == EN else f"⚠️ Расписание {label}:"
    return title + "\n" + "\n".join(lines)


def morning_recalc_text(lang: str) -> str:
    return "🔄 Exit time recalculated (first class changed):" if lang == EN else \
        "🔄 Время выхода пересчитано (первая пара изменилась):"


def weather_line(lang: str, temp_c: float, precip_prob: int, kind: str) -> str:
    """Одна строка погоды: '🌧 +9°, дождь 70%: возьми зонт'."""
    sign = "+" if temp_c >= 0 else ""
    t = f"{sign}{int(round(temp_c))}°"
    rain_word = {"rain": "дождь", "snow": "снег"}.get(kind, "осадки")
    if lang == EN:
        rain_word = {"rain": "rain", "snow": "snow"}.get(kind, "precipitation")
        if precip_prob < 20:
            return f"☀️ {t}, no precipitation"
        icon = "🌧" if precip_prob >= 50 else "⛅"
        tail = ": take an umbrella" if precip_prob >= 50 else ""
        return f"{icon} {t}, {rain_word} {precip_prob}%{tail}"
    if precip_prob < 20:
        return f"☀️ {t}, без осадков"
    icon = "🌧" if precip_prob >= 50 else "⛅"
    tail = ": возьми зонт" if precip_prob >= 50 else ""
    return f"{icon} {t}, {rain_word} {precip_prob}%{tail}"
