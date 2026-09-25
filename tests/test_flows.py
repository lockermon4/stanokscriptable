"""Новые потоки: intraday-фокус, список дня, даты заметок, кнопки настроек."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from student_bot.bot import note_label, parse_note_date
from student_bot.cards import build_focus, format_day_list
from student_bot.models import DaySchedule, Lesson
from student_bot.texts import (buffer_buttons, evening_buttons, lang_buttons,
                               morning_lead_buttons, note_confirm_delete, note_date_buttons,
                               note_deleted, note_item_buttons, note_saved, settings_buttons,
                               settings_view, transport_buttons)

TZ = ZoneInfo("Europe/Moscow")
DAY = date(2026, 9, 26)


def les(h1, m1, h2, m2, subj="Math", room="0209"):
    return Lesson(group="G", day=DAY,
                  starts_at=datetime(2026, 9, 26, h1, m1, tzinfo=TZ),
                  ends_at=datetime(2026, 9, 26, h2, m2, tzinfo=TZ),
                  subject=subj, room=room)


def sched(*lessons):
    return DaySchedule(day=DAY, group="G", lessons=tuple(lessons))


# ---------- parse_note_date / note_label (pure) ----------

def test_parse_note_date_formats():
    assert parse_note_date("2026-09-26") == "2026-09-26"
    assert parse_note_date("26.09.2026") == "2026-09-26"
    assert parse_note_date("26.09").endswith("-09-26")
    assert parse_note_date("  2026-09-26  ") == "2026-09-26"
    assert parse_note_date("завтра") is None
    assert parse_note_date("32.13") is None
    assert parse_note_date("") is None


def test_note_label():
    assert note_label("2026-09-26") == "26.09"


# ---------- format_day_list ----------

def test_day_list_markers_and_empty():
    now = datetime(2026, 9, 26, 11, 0, tzinfo=TZ)
    t = format_day_list(sched(les(8, 30, 10, 0), les(10, 15, 11, 50), les(12, 20, 13, 55)), now, "ru")
    lines = t.splitlines()
    assert lines[0].startswith("◾ 08:30") and lines[1].startswith("🟡 10:15") \
        and lines[2].startswith("▫️ 12:20")
    assert "ауд. 0209" in t
    assert format_day_list(sched(), now, "ru") == "Сегодня пар нет."
    assert format_day_list(sched(), now, "en") == "No classes today."


# ---------- build_focus: до / идёт / закончилась ----------

def test_focus_future_on_track():
    now = datetime(2026, 9, 26, 7, 0, tzinfo=TZ)  # exit 08:30-69мин-10мин = 07:11 > now
    t = build_focus(les(8, 30, 10, 0), None, 69 * 60, 10, now, True, "ru")
    assert "Успеваешь" in t and "07:11" in t


def test_focus_future_leave_now():
    now = datetime(2026, 9, 26, 7, 30, tzinfo=TZ)  # exit passed, arrival 08:39 < 10:00 start? no:
    # travel 60 мин: exit_rec=07:20 < now, arrival=08:30 <= start 08:30 -> leave now
    t = build_focus(les(8, 30, 10, 0), None, 60 * 60, 10, now, True, "ru")
    assert "выходить сейчас" in t and "08:30" in t


def test_focus_future_miss_start_but_catch_tail():
    now = datetime(2026, 9, 26, 8, 0, tzinfo=TZ)  # arrival 09:00, end 10:00 -> 60 мин left
    t = build_focus(les(8, 30, 10, 0), les(10, 15, 11, 50), 60 * 60, 10, now, True, "ru")
    assert "не успеть" in t and "~60 мин" in t


def test_focus_ongoing_shows_arrival_and_left():
    now = datetime(2026, 9, 26, 9, 0, tzinfo=TZ)  # идёт 08:30-10:00, arrival 09:30 -> 30 мин
    t = build_focus(les(8, 30, 10, 0), None, 30 * 60, 10, now, True, "ru")
    assert "уже идёт" in t and "09:30" in t and "~30 мин" in t


def test_focus_ongoing_too_late_points_next():
    now = datetime(2026, 9, 26, 9, 50, tzinfo=TZ)  # arrival 10:30 > end 10:00
    t = build_focus(les(8, 30, 10, 0), les(10, 15, 11, 50), 40 * 60, 10, now, True, "ru")
    assert "не успеть" in t and "Следующая: 10:15" in t


def test_focus_never_marks_missed_on_exit_only_and_en():
    now = datetime(2026, 9, 26, 7, 30, tzinfo=TZ)
    t = build_focus(les(8, 30, 10, 0), None, 60 * 60, 10, now, True, "ru")
    assert "пропущ" not in t.lower()
    te = build_focus(les(8, 30, 10, 0), None, 60 * 60, 10, now, True, "en")
    assert "08:30" in te and "пропущ" not in te.lower()


def test_focus_no_route_is_honest():
    now = datetime(2026, 9, 26, 7, 0, tzinfo=TZ)
    t = build_focus(les(8, 30, 10, 0), None, None, 10, now, False, "ru")
    assert "посчитать не получилось" in t and "Выйти в" not in t


# ---------- кнопки: русские метки, callback-data, без дампа команд ----------

def test_settings_view_no_command_dump():
    ru = settings_view("ИДБ-26-14", "Дом", "transit", 10, "21:00", 60, "ru")
    assert "кнопки ниже" in ru and "адрес <текст>" not in ru and "группа=" not in ru


def test_settings_buttons_labels_and_callbacks():
    ru = settings_buttons("ru")
    texts = [b.text for row in ru.inline_keyboard for b in row]
    datas = [b.callback_data for row in ru.inline_keyboard for b in row]
    for want in ("Изменить группу", "Изменить адрес", "Способ передвижения",
                 "Запас времени", "Время уведомлений", "Язык", "◀️ Назад"):
        assert want in texts, want
    for want in ("set:group", "set:address", "set:transport", "set:buffer",
                 "set:notify", "set:lang", "set:back"):
        assert want in datas, want


def test_option_buttons_callbacks():
    assert "tr:foot" in [b.callback_data for row in transport_buttons("ru").inline_keyboard for b in row]
    assert "buf:15" in [b.callback_data for row in buffer_buttons("ru").inline_keyboard for b in row]
    assert "ntf:eve:21:00" in [b.callback_data for row in evening_buttons("ru").inline_keyboard for b in row]
    assert "ntf:morn:60" in [b.callback_data for row in morning_lead_buttons("ru").inline_keyboard for b in row]
    datas = [b.callback_data for row in lang_buttons().inline_keyboard for b in row]
    assert "lang:ru" in datas and "lang:en" in datas


def test_note_date_buttons_russian_and_prefixes():
    ru = note_date_buttons("ru", "nadd")
    texts = [b.text for row in ru.inline_keyboard for b in row]
    datas = [b.callback_data for row in ru.inline_keyboard for b in row]
    assert "Сегодня" in texts and "Завтра" in texts and "Выбрать дату" in texts
    assert "nadd:today" in datas and "nadd:custom" in datas
    assert "nview:today" in [b.callback_data for row in note_date_buttons("ru", "nview").inline_keyboard for b in row]


def test_note_texts_confirm_and_item_buttons():
    assert "26.09" in note_saved("ru", "26.09", "халат") and "халат" in note_saved("ru", "26.09", "халат")
    assert "удалена" in note_deleted("ru", "26.09")
    t, kb = note_confirm_delete("ru", "2026-09-26", "26.09", "халат")
    assert "халат" in t
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "note:del:yes:2026-09-26" in datas
    datas2 = [b.callback_data for row in note_item_buttons("ru", "2026-09-26").inline_keyboard for b in row]
    assert "note:edit:2026-09-26" in datas2 and "note:delone:2026-09-26" in datas2
