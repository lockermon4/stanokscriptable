from datetime import date

from student_bot.normalize import normalize_day, normalize_groups


def test_normalize_groups_variants():
    assert [g.name for g in normalize_groups(["А-11", "Б-22"])] == ["А-11", "Б-22"]
    assert normalize_groups({"groups": [{"id": "1", "name": "ИВТ-11"}]})[0].id == "1"
    assert normalize_groups({"data": [{"group": "X"}]})[0].name == "X"
    assert normalize_groups({"unexpected": 1}) == []


def test_normalize_day_common_shapes():
    day = date(2026, 9, 24)
    payload = [
        {"subject": "Матан", "start": "09:00", "end": "10:30", "building": "1", "room": "101"},
        {"title": "Физика", "time": "10:45-12:15", "corpus": "2"},
        {"name": "Без времени", "building": "1"},  # skipped
        {"status": "отменена", "subject": "Химия", "start": "13:00", "end": "14:30"},
    ]
    sched, skipped = normalize_day(payload, group="G", day=day, tz_name="Europe/Moscow")
    assert skipped == 1
    assert [l.subject for l in sched.lessons] == ["Матан", "Физика", "Химия"]
    assert sched.lessons[1].building_code == "2"
    assert sched.lessons[1].starts_at.strftime("%H:%M") == "10:45"
    assert sched.active_lessons[-1].subject == "Физика"  # cancelled excluded
    assert sched.count == 2


def test_schedule_change_updates():
    """Смена расписания/корпуса: новая нормализация даёт новый корпус первой пары."""
    day = date(2026, 9, 24)
    v1, _ = normalize_day([{"subject": "М", "start": "09:00", "building": "1"}], group="G", day=day, tz_name="Europe/Moscow")
    v2, _ = normalize_day([{"subject": "М", "start": "09:00", "building": "2"}], group="G", day=day, tz_name="Europe/Moscow")
    assert v1.lessons[0].building_code != v2.lessons[0].building_code
    # корпуса различаются -> ожидается пересчёт маршрута, не reuse
