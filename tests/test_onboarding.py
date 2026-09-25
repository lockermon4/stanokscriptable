"""Onboarding scenarios x RU/EN + human settings + menu matching."""
from student_bot.cards import build_evening, format_telegram_evening, format_telegram_morning, build_morning
from student_bot.models import DaySchedule
from student_bot.texts import (menu_kb, menu_match, minutes, norm_lang, settings_view, start_back,
                               start_need_group, start_need_home, start_new, start_route, transport_name)
from datetime import date


def test_start_route_branches():
    assert start_route(True, True) == "back"
    assert start_route(True, False) == "need_home"
    assert start_route(False, True) == "need_group"
    assert start_route(False, False) == "new"


def test_start_new_both_langs():
    ru, en = start_new("ru"), start_new("en")
    assert "расписание" in ru and "ИДБ-26-14" in ru
    assert "timetable" in en and "ИДБ-26-14" in en


def test_start_back_and_partial():
    assert "возвращением" in start_back("ru") and "Welcome" in start_back("en")
    assert "ИДБ-26-14" in start_need_home("ru", "ИДБ-26-14")
    assert "ИДБ-26-14" in start_need_home("en", "ИДБ-26-14")
    assert "адрес" not in start_need_group("ru") or "группу" in start_need_group("ru")


def test_settings_human_no_dump():
    ru = settings_view("ИДБ-26-14", "Москва, ул. Островитянова, д. 33А",
                       "transit", 10, "21:00", 60, "ru")
    assert "Группа: ИДБ-26-14" in ru and "Способ передвижения: общественный транспорт" in ru
    assert "Запас: 10 минут" in ru and "группа=" not in ru and "transport=" not in ru
    en = settings_view("ИДБ-26-14", "Moscow", "foot", 1, "21:00", 60, "en")
    assert "Group: ИДБ-26-14" in en and "Transport: on foot" in en and "Buffer: 1 minute" in en


def test_minutes_forms():
    assert minutes(1, "ru") == "1 минута" and minutes(2, "ru") == "2 минуты"
    assert minutes(5, "ru") == "5 минут" and minutes(60, "ru") == "60 минут"
    assert minutes(1, "en") == "1 minute" and minutes(5, "en") == "5 minutes"


def test_menu_both_langs_and_legacy():
    m = menu_match()
    assert m["📅 Сегодня"] == "today" and m["📅 Today"] == "today"
    assert m["🚪 Когда выходить"] == "leave" and m["🚪 When to leave"] == "leave"
    assert m["Когда выходить?"] == "leave" and m["Настройки"] == "settings"
    assert len(menu_kb("ru")) == 3 and len(menu_kb("en")) == 3


def test_norm_lang():
    assert norm_lang("en") == "en" and norm_lang("en-US") == "en"
    assert norm_lang("ru") == "ru" and norm_lang(None) == "ru"


def test_cards_english():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from student_bot.models import Lesson
    tz = ZoneInfo("Europe/Moscow")
    les = Lesson(group="G", day=date(2026, 9, 25),
                 starts_at=datetime(2026, 9, 25, 10, 15, tzinfo=tz),
                 ends_at=datetime(2026, 9, 25, 11, 50, tzinfo=tz),
                 subject="Math", room="0209")
    sched = DaySchedule(day=date(2026, 9, 25), group="G", lessons=(les,))
    t = format_telegram_evening(build_evening(sched, "coat"), "en")
    assert t.startswith("🌙 Tomorrow — 1 class") and "room 0209" in t and "Take: coat" in t
    t2 = format_telegram_morning(build_morning(les, None, route_failed=True), "en")
    assert "Couldn't calculate" in t2 and "Выйти" not in t2
    assert transport_name("transit", "en") == "public transit"
