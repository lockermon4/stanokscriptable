"""Все тексты бота на русском и английском. Никакой логики, только строки.
Язык: UserSettings.lang ("ru"/"en"); новым берётся из Telegram locale,
меняется командой «язык en» / «language ru».
"""
from __future__ import annotations

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
    "transit": (RU, "общественный транспорт", "public transit"),
    "foot": (RU, "пешком", "on foot"),
    "driving": (RU, "на машине", "by car"),
    "bike": (RU, "велосипед", "bicycle"),
}


def transport_name(code: str, lang: str) -> str:
    for k, (_, ru, en) in TRANSPORT.items():
        if k == code:
            return en if lang == EN else ru
    return code


# Кнопки меню: (ru, en, legacy_aliases)
MENU_TODAY = ("📅 Сегодня", "📅 Today", ("Сегодня",))
MENU_TOMORROW = ("🗓 Завтра", "🗓 Tomorrow", ("Завтра",))
MENU_LEAVE = ("🚪 Когда выходить", "🚪 When to leave", ("Когда выходить?", "Когда выходить"))
MENU_NOTES = ("📝 Заметки", "📝 Notes", ("Заметка на день", "Заметка"))
MENU_SETTINGS = ("⚙️ Настройки", "⚙️ Settings", ("Настройки",))


def menu_kb(lang: str) -> list[list[str]]:
    ru = lang != EN
    return [[MENU_TODAY[0] if ru else MENU_TODAY[1], MENU_TOMORROW[0] if ru else MENU_TOMORROW[1]],
            [MENU_LEAVE[0] if ru else MENU_LEAVE[1]],
            [MENU_NOTES[0] if ru else MENU_NOTES[1], MENU_SETTINGS[0] if ru else MENU_SETTINGS[1]]]


def menu_match() -> dict[str, str]:
    """button text -> action id (оба языка + старые тексты)."""
    out = {}
    for aid, item in (("today", MENU_TODAY), ("tomorrow", MENU_TOMORROW), ("leave", MENU_LEAVE),
                      ("notes", MENU_NOTES), ("settings", MENU_SETTINGS)):
        for t in (item[0], item[1], *item[2]):
            out[t] = aid
    return out


def notes_menu_text(lang: str) -> str:
    return "📝 Notes: add, view or delete." if lang == "en" else "📝 Заметки: добавить, посмотреть, удалить."


def notes_menu_buttons(lang: str):
    from .bot import ikb
    if lang == "en":
        return ikb([[("➕ Add note", "note:add")],
                    [("👁 View", "note:view"), ("🗑 Delete", "note:del")],
                    [("◀️ Back", "note:back")]])
    return ikb([[("➕ Добавить заметку", "note:add")],
                [("👁 Посмотреть", "note:view"), ("🗑 Удалить", "note:del")],
                [("◀️ Назад", "note:back")]])


def note_date_buttons(lang: str, prefix: str):
    from .bot import ikb
    if lang == "en":
        rows = [
            [("Today", f"{prefix}:today"), ("Tomorrow", f"{prefix}:tomorrow")],
            [("Pick a date", f"{prefix}:custom")],
            [("Cancel", "note:menu")],
        ]
    else:
        rows = [
            [("Today", f"{prefix}:today"), ("Tomorrow", f"{prefix}:tomorrow")],
            [("Choose date", f"{prefix}:custom")],
            [("Cancel", "note:menu")],
        ]
    return ikb(rows)


def notes_menu_text(lang: str) -> str:
    return "📝 Notes: add, view or delete." if lang == "en" else "📝 Заметки: добавить, посмотреть, удалить."


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


# ---------- настройки человеческим языком ----------

def settings_view(group: str, home: str, transport: str, buffer_min: int,
                  evening: str, morn_min: int, lang: str) -> str:
    if lang == EN:
        return (f"⚙️ Settings\nGroup: {group or '—'}\nHome: {home or '—'}\n"
                f"Transport: {transport_name(transport, lang)}\n"
                f"Buffer: {minutes(buffer_min, lang)}\n"
                f"Evening reminder: {evening}\n"
                f"Morning reminder: {minutes(morn_min, lang)} before leaving\n\n"
                f"To change, send: address <text> (or a pin) | transport <transit/foot/driving/bike> | "
                f"buffer <min> | evening <HH:MM> | morning <min> | group <name> | language <ru/en>")
    return (f"⚙️ Настройки\nГруппа: {group or '—'}\nДом: {home or '—'}\n"
            f"Способ передвижения: {transport_name(transport, lang)}\n"
            f"Запас: {minutes(buffer_min, lang)}\n"
            f"Вечернее уведомление: {evening}\n"
            f"Утреннее уведомление: за {minutes(morn_min, lang)} до выхода\n\n"
            f"Чтобы изменить, пришлите: адрес <текст> (или точку) | транспорт "
            f"<transit/foot/driving/bike> | запас <мин> | вечер <ЧЧ:ММ> | утро <мин> | "
            f"группа <название> | язык <ru/en>")


def need_group_first(lang: str) -> str:
    return "First choose your group via /start." if lang == EN else \
        "Сначала укажите группу через /start."


def need_home(lang: str) -> str:
    if lang == EN:
        return ("Add your home address first: type it (e.g. \"ul. Ostrovityanova, 33А\") "
                "or send a location pin (📎 → Location). I can't calculate the exit time without it.")
    return ("Сначала добавьте домашний адрес: напишите его текстом "
            "(например, «ул. Островитянова, 33А») или отправьте точку "
            "(скрепка → Геопозиция). Без него время выхода посчитать не выйдет.")


def settings_buttons(lang: str):
    from .bot import ikb
    if lang == "en":
        return ikb([[("Change group", "set:group"), ("Change address", "set:address")],
                    [("Transport", "set:transport"), ("Buffer", "set:buffer")],
                    [("Notifications", "set:notify"), ("Language", "set:lang")],
                    [("◀️ Back", "set:back")]])
    return ikb([[("Изменить группу", "set:group"), ("Изменить адрес", "set:address")],
                [("Способ передвижения", "set:transport"), ("Запас времени", "set:buffer")],
                [("Время уведомлений", "set:notify"), ("Язык", "set:lang")],
                [("◀️ Назад", "set:back")]])


def transport_buttons(lang: str):
    from .bot import ikb
    from .texts import transport_name

    rows = [[(transport_name(c, lang), f"tr:{c}")] for c in ("transit", "foot", "driving", "bike")]
    rows.append([("◀️ Back", "set:menu")] if lang == "en" else [("◀️ Назад", "set:menu")])
    return ikb(rows)


def notes_menu_buttons(lang: str):
    from .bot import ikb
    if lang == "en":
        return ikb([[("➕ Add note", "note:add")],
                    [("👁 View", "note:view"), ("🗑 Delete", "note:del")],
                    [("◀️ Back", "note:back")]])
    return ikb([[("➕ Добавить заметку", "note:add")],
                [("👁 Посмотреть", "note:view"), ("🗑 Удалить", "note:del")],
                [("◀️ Назад", "note:back")]])


def note_date_buttons(lang: str, prefix: str):
    from .bot import ikb
    if lang == "en":
        rows = [
            [("Today", f"{prefix}:today"), ("Tomorrow", f"{prefix}:tomorrow")],
            [("Pick a date", f"{prefix}:custom")],
            [("Cancel", "note:menu")],
        ]
    else:
        rows = [
            [("Today", f"{prefix}:today"), ("Tomorrow", f"{prefix}:tomorrow")],
            [("Choose date", f"{prefix}:custom")],
            [("Cancel", "note:menu")],
        ]
    return ikb(rows)


def notes_menu_text(lang: str) -> str:
    return "📝 Notes: add, view or delete." if lang == "en" else "📝 Заметки: добавить, посмотреть, удалить."


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


# ---------- настройки человеческим языком ----------

def settings_view(group: str, home: str, transport: str, buffer_min: int,
                  evening: str, morn_min: int, lang: str) -> str:
    if lang == EN:
        return (f"⚙️ Settings\nGroup: {group or '—'}\nHome: {home or '—'}\n"
                f"Transport: {transport_name(transport, lang)}\n"
                f"Buffer: {minutes(buffer_min, lang)}\n"
                f"Evening reminder: {evening}\n"
                f"Morning reminder: {minutes(morn_min, lang)} before leaving\n\n"
                f"To change, send: address <text> (or a pin) | transport <transit/foot/driving/bike> | "
                f"buffer <min> | evening <HH:MM> | morning <min> | group <name> | language <ru/en>")
    return (f"⚙️ Настройки\nГруппа: {group or '—'}\nДом: {home or '—'}\n"
            f"Способ передвижения: {transport_name(transport, lang)}\n"
            f"Запас: {minutes(buffer_min, lang)}\n"
            f"Вечернее уведомление: {evening}\n"
            f"Утреннее уведомление: за {minutes(morn_min, lang)} до выхода\n\n"
            f"Чтобы изменить, пришлите: адрес <текст> (или точку) | транспорт "
            f"<transit/foot/driving/bike> | запас <мин> | вечер <ЧЧ:ММ> | утро <мин> | "
            f"группа <название> | язык <ru/en>")


def need_group_first(lang: str) -> str:
    return "First choose your group via /start." if lang == EN else \
        "Сначала укажите группу через /start."


def need_home(lang: str) -> str:
    if lang == EN:
        return ("Add your home address first: type it (e.g. \"ul. Ostrovityanova, 33А\") "
                "or send a location pin (📎 → Location). I can't calculate the exit time without it.")
    return ("Сначала добавьте домашний адрес: напишите его текстом "
            "(например, «ул. Островитянова, 33А») или отправьте точку "
            "(скрепка → Геопозиция). Без него время выхода посчитать не выйдет.")


def settings_buttons(lang: str):
    from .bot import ikb
    if lang == "en":
        return ikb([[("Change group", "set:group"), ("Change address", "set:address")],
                    [("Transport", "set:transport"), ("Buffer", "set:buffer")],
                    [("Notifications", "set:notify"), ("Language", "set:lang")],
                    [("◀️ Back", "set:back")]])
    return ikb([[("Изменить группу", "set:group"), ("Изменить адрес", "set:address")],
                [("Способ передвижения", "set:transport"), ("Запас времени", "set:buffer")],
                [("Время уведомлений", "set:notify"), ("Язык", "set:lang")],
                [("◀️ Назад", "set:back")]])


def transport_buttons(lang: str):
    from .bot import ikb
    from .texts import transport_name

    rows = [[(transport_name(c, lang), f"tr:{c}")] for c in ("transit", "foot", "driving", "bike")]
    rows.append([("◀️ Back", "set:menu")] if lang == "en" else [("◀️ Назад", "set:menu")])
    return ikb(rows)


def notes_menu_buttons(lang: str):
    from .bot import ikb
    if lang == "en":
        return ikb([[("➕ Add note", "note:add")],
                    [("👁 View", "note:view"), ("🗑 Delete", "note:del")],
                    [("◀️ Back", "note:back")]])
    return ikb([[("➕ Добавить заметку", "note:add")],
                [("👁 Посмотреть", "note:view"), ("🗑 Удалить", "note:del")],
                [("◀️ Назад", "note:back")]])


def note_date_buttons(lang: str, prefix: str):
    from .bot import ikb
    if lang == "en":
        rows = [
            [("Today", f"{prefix}:today"), ("Tomorrow", f"{prefix}:tomorrow")],
            [("Pick a date", f"{prefix}:custom")],
            [("Cancel", "note:menu")],
        ]
    else:
        rows = [
            [("Today", f"{prefix}:today"), ("Tomorrow", f"{prefix}:tomorrow")],
            [("Choose date", f"{prefix}:custom")],
            [("Cancel", "note:menu")],
        ]
    return ikb(rows)


def notes_menu_text(lang: str) -> str:
    return "📝 Notes: add, view or delete." if lang == "en" else "📝 Заметки: добавить, посмотреть, удалить."


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


# ---------- настройки человеческим языком ----------

def settings_view(group: str, home: str, transport: str, buffer_min: int,
                  evening: str, morn_min: int, lang: str) -> str:
    if lang == EN:
        return (f"⚙️ Settings\nGroup: {group or '—'}\nHome: {home or '—'}\n"
                f"Transport: {transport_name(transport, lang)}\n"
                f"Buffer: {minutes(buffer_min, lang)}\n"
                f"Evening reminder: {evening}\n"
                f"Morning reminder: {minutes(morn_min, lang)} before leaving\n\n"
                f"To change, send: address <text> (or a pin) | transport <transit/foot/driving/bike> | "
                f"buffer <min> | evening <HH:MM> | morning <min> | group <name> | language <ru/en>")
    return (f"⚙️ Настройки\nГруппа: {group or '—'}\nДом: {home or '—'}\n"
            f"Способ передвижения: {transport_name(transport, lang)}\n"
            f"Запас: {minutes(buffer_min, lang)}\n"
            f"Вечернее уведомление: {evening}\n"
            f"Утреннее уведомление: за {minutes(morn_min, lang)} до выхода\n\n"
            f"Чтобы изменить, пришлите: адрес <текст> (или точку) | транспорт "
            f"<transit/foot/driving/bike> | запас <мин> | вечер <ЧЧ:ММ> | утро <мин> | "
            f"группа <название> | язык <ru/en>")


def need_group_first(lang: str) -> str:
    return "First choose your group via /start." if lang == EN else \
        "Сначала укажите группу через /start."


def need_home(lang: str) -> str:
    if lang == EN:
        return ("Add your home address first: type it (e.g. \"ul. Ostrovityanova, 33А\") "
                "or send a location pin (📎 → Location). I can't calculate the exit time without it.")
    return ("Сначала добавьте домашний адрес: напишите его текстом "
            "(например, «ул. Островитянова, 33А») или отправьте точку "
            "(скрепка → Геопозиция). Без него время выхода посчитать не выйдет.")


def settings_buttons(lang: str):
    from .bot import ikb
    if lang == "en":
        return ikb([[("Change group", "set:group"), ("Change address", "set:address")],
                    [("Transport", "set:transport"), ("Buffer", "set:buffer")],
                    [("Notifications", "set:notify"), ("Language", "set:lang")],
                    [("◀️ Back", "set:back")]])
    return ikb([[("Изменить группу", "set:group"), ("Изменить адрес", "set:address")],
                [("Способ передвижения", "set:transport"), ("Запас времени", "set:buffer")],
                [("Время уведомлений", "set:notify"), ("Язык", "set:lang")],
                [("◀️ Назад", "set:back")]])


def transport_buttons(lang: str):
    from .bot import ikb
    from .texts import transport_name

    rows = [[(transport_name(c, lang), f"tr:{c}")] for c in ("transit", "foot", "driving", "bike")]
    rows.append([("◀️ Back", "set:menu")] if lang == "en" else [("◀️ Назад", "set:menu")])
    return ikb(rows)


def notes_menu_buttons(lang: str):
    from .bot import ikb
    if lang == "en":
        return ikb([[("➕ Add note", "note:add")],
                    [("👁 View", "note:view"), ("🗑 Delete", "note:del")],
                    [("◀️ Back", "note:back")]])
    return ikb([[("➕ Добавить заметку", "note:add")],
                [("👁 Посмотреть", "note:view"), ("🗑 Удалить", "note:del")],
                [("◀️ Назад", "note:back")]])


def note_date_buttons(lang: str, prefix: str):
    from .bot import ikb
    if lang == "en":
        rows = [
            [("Today", f"{prefix}:today"), ("Tomorrow", f"{prefix}:tomorrow")],
            [("Pick a date", f"{prefix}:custom")],
            [("Cancel", "note:menu")],
        ]
    else:
        rows = [
            [("Today", f"{prefix}:today"), ("Tomorrow", f"{prefix}:tomorrow")],
            [("Choose date", f"{prefix}:custom")],
            [("Cancel", "note:menu")],
        ]
    return ikb(rows)


def notes_menu_text(lang: str) -> str:
    return "📝 Notes: add, view or delete." if lang == "en" else "📝 Заметки: добавить, посмотреть, удалить."


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


# ---------- настройки человеческим языком ----------

def settings_view(group: str, home: str, transport: str, buffer_min: int,
                  evening: str, morn_min: int, lang: str) -> str:
    if lang == EN:
        return (f"⚙️ Settings\nGroup: {group or '—'}\nHome: {home or '—'}\n"
                f"Transport: {transport_name(transport, lang)}\n"
                f"Buffer: {minutes(buffer_min, lang)}\n"
                f"Evening reminder: {evening}\n"
                f"Morning reminder: {minutes(morn_min, lang)} before leaving\n\n"
                f"To change, send: address <text> (or a pin) | transport <transit/foot/driving/bike> | "
                f"buffer <min> | evening <HH:MM> | morning <min> | group <name> | language <ru/en>")
    return (f"⚙️ Настройки\nГруппа: {group or '—'}\nДом: {home or '—'}\n"
            f"Способ передвижения: {transport_name(transport, lang)}\n"
            f"Запас: {minutes(buffer_min, lang)}\n"
            f"Вечернее уведомление: {evening}\n"
            f"Утреннее уведомление: за {minutes(morn_min, lang)} до выхода\n\n"
            f"Чтобы изменить, пришлите: адрес <текст> (или точку) | транспорт "
            f"<transit/foot/driving/bike> | запас <мин> | вечер <ЧЧ:ММ> | утро <мин> | "
            f"группа <название> | язык <ru/en>")


def need_group_first(lang: str) -> str:
    return "First choose your group via /start." if lang == EN else \
        "Сначала укажите группу через /start."


def need_home(lang: str) -> str:
    if lang == EN:
        return ("Add your home address first: type it (e.g. \"ul. Ostrovityanova, 33А\") "
                "or send a location pin (📎 → Location). I can't calculate the exit time without it.")
    return ("Сначала добавьте домашний адрес: напишите его текстом "
            "(например, «ул. Островитянова, 33А») или отправьте точку "
            "(скрепка → Геопозиция). Без него время выхода посчитать не выйдет.")


def settings_buttons(lang: str):
    from .bot import ikb
    if lang == "en":
        return ikb([[("Change group", "set:group"), ("Change address", "set:address")],
                    [("Transport", "set:transport"), ("Buffer", "set:buffer")],
                    [("Notifications", "set:notify"), ("Language", "set:lang")],
                    [("◀️ Back", "set:back")]])
    return ikb([[("Изменить группу", "set:group"), ("Изменить адрес", "set:address")],
                [("Способ передвижения", "set:transport"), ("Запас времени", "set:buffer")],
                [("Время уведомлений", "set:notify"), ("Язык", "set:lang")],
                [("◀️ Назад", "set:back")]])


def transport_buttons(lang: str):
    from .bot import ikb
    from .texts import transport_name

    rows = [[(transport_name(c, lang), f"tr:{c}")] for c in ("transit", "foot", "driving", "bike")]
    rows.append([("◀️ Back", "set:menu")] if lang == "en" else [("◀️ Назад", "set:menu")])
    return ikb(rows)


def notes_menu_buttons(lang: str):
    from .bot import ikb
    if lang == "en":
        return ikb([[("➕ Add note", "note:add")],
                    [("👁 View", "note:view"), ("🗑 Delete", "note:del")],
                    [("◀️ Back", "note:back")]])
    return ikb([[("➕ Добавить заметку", "note:add")],
                [("👁 Посмотреть", "note:view"), ("🗑 Удалить", "note:del")],
                [("◀️ Назад", "note:back")]])


def note_date_buttons(lang: str, prefix: str):
    from .bot import ikb
    if lang == "en":
        rows = [
            [("Today", f"{prefix}:today"), ("Tomorrow", f"{prefix}:tomorrow")],
            [("Pick a date", f"{prefix}:custom")],
            [("Cancel", "note:menu")],
        ]
    else:
        rows = [
            [("Today", f"{prefix}:today"), ("Tomorrow", f"{prefix}:tomorrow")],
            [("Choose date", f"{prefix}:custom")],
            [("Cancel", "note:menu")],
        ]
    return ikb(rows)


def notes_menu_text(lang: str) -> str:
    return "📝 Notes: add, view or delete." if lang == "en" else "📝 Заметки: добавить, посмотреть, удалить."


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


# ---------- настройки человеческим языком ----------

def settings_view(group: str, home: str, transport: str, buffer_min: int,
                  evening: str, morn_min: int, lang: str) -> str:
    if lang == EN:
        return (f"⚙️ Settings\nGroup: {group or '—'}\nHome: {home or '—'}\n"
                f"Transport: {transport_name(transport, lang)}\n"
                f"Buffer: {minutes(buffer_min, lang)}\n"
                f"Evening reminder: {evening}\n"
                f"Morning reminder: {minutes(morn_min, lang)} before leaving\n\n"
                f"To change, send: address <text> (or a pin) | transport <transit/foot/driving/bike> | "
                f"buffer <min> | evening <HH:MM> | morning <min> | group <name> | language <ru/en>")
    return (f"⚙️ Настройки\nГруппа: {group or '—'}\nДом: {home or '—'}\n"
            f"Способ передвижения: {transport_name(transport, lang)}\n"
            f"Запас: {minutes(buffer_min, lang)}\n"
            f"Вечернее уведомление: {evening}\n"
            f"Утреннее уведомление: за {minutes(morn_min, lang)} до выхода\n\n"
            f"Чтобы изменить, пришлите: адрес <текст> (или точку) | транспорт "
            f"<transit/foot/driving/bike> | запас <мин> | вечер <ЧЧ:ММ> | утро <мин> | "
            f"группа <название> | язык <ru/en>")


def need_group_first(lang: str) -> str:
    return "First choose your group via /start." if lang == EN else \
        "Сначала укажите группу через /start."


def need_home(lang: str) -> str:
    if lang == EN:
        return ("Add your home address first: type it (e.g. \"ul. Ostrovityanova, 33А\") "
                "or send a location pin (📎 → Location). I can't calculate the exit time without it.")
    return ("Сначала добавьте домашний адрес: напишите его текстом "
            "(например, «ул. Островитянова, 33А») или отправьте точку "
            "(скрепка → Геопозиция). Без него время выхода посчитать не выйдет.")



def pretty_street(raw: str, fallback: str) -> tuple[str, str | None]:
    """Capitalize street name and extract type from raw address or fallback."""
    from .address_check import _parse_street_token
    nm, tp = _parse_street_token(raw or "")
    name = (nm or "").strip() if nm else ""
    return (name[:1].upper() + name[1:] if name else fallback, tp)
