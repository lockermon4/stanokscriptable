"""Фикстуры реальных ответов stankinapp.ru."""
from datetime import date

from student_bot.buildings import BuildingStore
from student_bot.normalize import normalize_day, normalize_groups

GROUPS_RAW = {"items": ["ИДБ-26-14", "МДБ-26-01"]}

DAY_RAW = {"items": [
    {"id": "ИДБ-26-14_2026-09-24_08:30_all", "date": "2026-09-24",
     "startTime": "08:30", "endTime": "10:05", "durationMinutes": 95,
     "groupName": "ИДБ-26-14", "subject": "Основы российской государственности",
     "teacher": "Сагал Д.А.", "type": "Лекция", "subgroup": "",
     "cabinet": "Фрезер 303(ММ)", "slotNumber": 1, "pairs": None},
    {"id": "ИДБ-26-14_2026-09-24_12:20_all", "date": "2026-09-24",
     "startTime": "12:20", "endTime": "13:55", "durationMinutes": 95,
     "groupName": "ИДБ-26-14", "subject": "Основы аналитических проектов",
     "teacher": "Ибатулин М.Ю.", "type": "Лекция", "subgroup": "",
     "cabinet": "0209", "slotNumber": 3, "pairs": None},
]}


def test_groups_shape():
    groups = normalize_groups(GROUPS_RAW)
    assert groups[0].name == "ИДБ-26-14"


def test_day_shape_and_filtering():
    sched, skipped = normalize_day(DAY_RAW, group="ИДБ-26-14", day=date(2026, 9, 24), tz_name="Europe/Moscow")
    assert skipped == 0 and sched.count == 2
    first = sched.lessons[0]
    assert first.subject == "Основы российской государственности"
    assert first.room == "Фрезер 303(ММ)" and first.kind == "Лекция"
    assert first.starts_at.strftime("%H:%M") == "08:30"
    assert first.ends_at.strftime("%H:%M") == "10:05"
    assert first.raw["teacher"] == "Сагал Д.А."
    # other day filtered out
    other, _ = normalize_day(DAY_RAW, group="ИДБ-26-14", day=date(2026, 9, 25), tz_name="Europe/Moscow")
    assert other.count == 0


def test_cabinet_resolver():
    store = BuildingStore.from_mapping(
        {"вадковский-3а": "Москва, Вадковский пер., 3А", "фрезер-10": "Москва, шоссе Фрезер, 10"},
        cabinet_rules={"фрезер": "фрезер-10"}, cabinet_default="вадковский-3а")
    b, heur = store.resolve_cabinet("Фрезер 303(ММ)")
    assert b.address == "Москва, шоссе Фрезер, 10" and heur is False
    b2, heur2 = store.resolve_cabinet("0303")
    assert b2.address == "Москва, Вадковский пер., 3А" and heur2 is True
    b3, _ = store.resolve_cabinet("")
    assert b3 is None
