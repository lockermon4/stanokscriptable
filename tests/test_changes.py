"""Детектор изменений: дифф, two-poll, ложные срабатывания, уведомления."""
import asyncio
from datetime import date

from student_bot.changes import (canon_lesson, check_group_day, diff_snapshots,
                                 handle_confirmed, snapshot_of)
from student_bot.store import Store, UserSettings

DAY = "2026-09-28"


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def L(time, subject, room="0303", teacher="Иванов", end="10:05"):
    return {"time": time, "end": end, "subject": subject, "room": room,
            "teacher": teacher, "kind": "Л"}


def test_diff_removed_added():
    old = [L("08:30", "М"), L("10:15", "Ф")]
    new = [L("08:30", "М")]
    d = diff_snapshots(old, new)
    assert d == [{"type": "removed", "time": "10:15", "subject": "Ф", "detail": ""}]
    d2 = diff_snapshots(new, old)
    assert d2[0]["type"] == "added" and d2[0]["time"] == "10:15"


def test_diff_moved_with_room():
    old = [L("08:30", "М"), L("12:20", "Ф", room="0209")]
    new = [L("08:30", "М"), L("14:05", "Ф", room="0206")]
    d = diff_snapshots(old, new)
    assert len(d) == 1 and d[0]["type"] == "moved"
    assert d[0]["time"] == "14:05" and "12:20" in d[0]["detail"] and "0206" in d[0]["detail"]


def test_diff_room_teacher_time():
    old = [L("10:15", "М", room="0209", teacher="Иванов", end="11:50")]
    new = [L("10:15", "М", room="0206", teacher="Петров", end="12:20")]
    kinds = {c["type"] for c in diff_snapshots(old, new)}
    assert kinds == {"room", "teacher", "time"}
    assert diff_snapshots(old, old) == []


def test_api_down_touches_nothing(tmp_path):
    s = Store(str(tmp_path / "w.sqlite3"))
    s.save_snapshot("G", DAY, [L("08:30", "М")])

    class Dead:
        async def get_day_raw(self, group, day_iso, fresh=False):
            raise RuntimeError("api down")

    from student_bot.buildings import BuildingStore
    status, diff, fresh = run(check_group_day(s, Dead(), BuildingStore([]), "G", DAY, "Europe/Moscow"))
    assert status == "error" and diff == []
    assert s.get_snapshot("G", DAY) == [L("08:30", "М")]  # снимок не тронут
    assert s.get_pending("G", DAY) is None


def test_two_poll_confirmation_and_flap(tmp_path):
    s = Store(str(tmp_path / "w.sqlite3"))
    s.save_snapshot("G", DAY, [L("08:30", "М"), L("10:15", "Ф")])

    class Sched:
        def __init__(self, payload):
            self.payload = payload

        async def get_day_raw(self, group, day_iso, fresh=False):
            return self.payload

    def raw(lessons):
        return [{"subject": l["subject"], "date": DAY, "startTime": l["time"],
                 "endTime": l["end"], "cabinet": "0303", "type": "Л",
                 "groupName": "G", "teacher": l["teacher"]} for l in lessons]

    from student_bot.buildings import BuildingStore
    b = BuildingStore([])
    # Опрос 1: Ф исчезла -> pending, молчим.
    st, d1, _ = run(check_group_day(s, Sched(raw([L("08:30", "М")])), b, "G", DAY, "Europe/Moscow"))
    assert st == "pending" and d1[0]["type"] == "removed"
    assert s.get_snapshot("G", DAY)[1]["subject"] == "Ф"  # снимок старый
    # Опрос 2 (флап: всё вернулось) -> расхождений нет, pending сброшен.
    st, d2, _ = run(check_group_day(
        s, Sched(raw([L("08:30", "М"), L("10:15", "Ф")])), b, "G", DAY, "Europe/Moscow"))
    assert st == "no-diff" and s.get_pending("G", DAY) is None
    # Опрос 3-4: исчезновение держится дважды -> confirmed.
    st, _, _ = run(check_group_day(s, Sched(raw([L("08:30", "М")])), b, "G", DAY, "Europe/Moscow"))
    assert st == "pending"
    st, d4, fresh = run(check_group_day(s, Sched(raw([L("08:30", "М")])), b, "G", DAY, "Europe/Moscow"))
    assert st == "confirmed" and d4[0] == {"type": "removed", "time": "10:15",
                                           "subject": "Ф", "detail": ""}
    assert fresh == [L("08:30", "М")]


def test_first_snapshot_silent(tmp_path):
    s = Store(str(tmp_path / "w.sqlite3"))

    class Sched:
        async def get_day_raw(self, group, day_iso, fresh=False):
            return [{"subject": "М", "date": DAY, "startTime": "08:30",
                     "endTime": "10:05", "cabinet": "0303", "type": "Л", "groupName": "G"}]

    from student_bot.buildings import BuildingStore
    st, diff, _ = run(check_group_day(s, Sched(), BuildingStore([]), "G", DAY, "Europe/Moscow"))
    assert st == "first-snapshot" and diff == []
    assert len(s.get_snapshot("G", DAY)) == 1


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, uid, text, reply_markup=None):
        self.sent.append((uid, text, reply_markup))


def _hub(tmp_path):
    from student_bot.buildings import BuildingStore
    from student_bot.config import Settings

    s = Store(str(tmp_path / "h.sqlite3"))
    buildings = BuildingStore.from_mapping(
        {"b": {"address": "А", "lat": 55.79, "lon": 37.59}}, cabinet_default="b")

    class Geo:
        async def geocode(self, addr):
            return (55.63, 37.52)

    class Routing:
        async def walking(self, fr, to, use_cache=True):
            from student_bot.routing import RouteOption
            return [RouteOption(mode="walk", duration_s=600, summary="10 мин")]

        async def metro(self, fr, to, use_cache=True):
            from student_bot.routing import RouteOption
            return [RouteOption(mode="metro", duration_s=1200, summary="20 мин")]

    class Sched:
        async def get_day_raw(self, group, day_iso, fresh=False):
            return []

    deps = {"schedule_client": Sched(), "buildings": buildings,
            "geocoder": Geo(), "routing": Routing()}
    return s, Settings(), deps


def test_handle_confirmed_notifies_and_recalcs(tmp_path):
    from student_bot.changes import handle_confirmed

    s, settings, deps = _hub(tmp_path)
    u = UserSettings(user_id=7, group="G", home_address="дом", transport="metro",
                     buffer_min=10, lang="ru")
    s.save_user(u)
    s.save_snapshot("G", DAY, [L("08:30", "М"), L("10:15", "Ф")])
    s.mark_sent(7, DAY, "morn")  # выход уже отправляли -> повод пересчитать
    bot = FakeBot()
    changes = [{"type": "removed", "time": "08:30", "subject": "М", "detail": ""}]
    run(handle_confirmed(bot, settings, s, deps, "G", DAY, changes, [L("10:15", "Ф")]))
    assert len(bot.sent) == 2  # уведомление + пересчитанный выход
    assert "⚠️" in bot.sent[0][1] and "08:30" in bot.sent[0][1]
    assert bot.sent[0][2] is not None  # кнопка «Пересчитать выход»
    assert "🔄" in bot.sent[1][1]
    assert s.was_sent(7, DAY, "morn_recalc")
    assert s.get_changes("G", DAY) == changes  # для iOS API
    assert s.get_snapshot("G", DAY) == [L("10:15", "Ф")]
    assert s.get_pending("G", DAY) is None


def test_handle_confirmed_no_recalc_without_morn(tmp_path):
    from student_bot.changes import handle_confirmed

    s, settings, deps = _hub(tmp_path)
    s.save_user(UserSettings(user_id=7, group="G", home_address="дом", lang="ru"))
    s.save_snapshot("G", DAY, [L("08:30", "М")])
    bot = FakeBot()
    changes = [{"type": "added", "time": "12:20", "subject": "Х", "detail": "ауд. 1"}]
    run(handle_confirmed(bot, settings, s, deps, "G", DAY, changes,
                         [L("08:30", "М"), L("12:20", "Х")]))
    assert len(bot.sent) == 1  # только уведомление, пересчёт не нужен
    assert not s.was_sent(7, DAY, "morn_recalc")


def test_snapshot_helpers_canon(tmp_path):
    from student_bot.models import DaySchedule, Lesson
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Europe/Moscow")
    les = Lesson(group="G", day=date(2026, 9, 28),
                 starts_at=datetime(2026, 9, 28, 8, 30, tzinfo=tz),
                 ends_at=datetime(2026, 9, 28, 10, 5, tzinfo=tz),
                 subject="М", room="0303", raw={"teacher": "Иванов"})
    snap = snapshot_of(DaySchedule(day=date(2026, 9, 28), group="G", lessons=(les,)))
    assert snap == [{"time": "08:30", "end": "10:05", "subject": "М",
                     "room": "0303", "teacher": "Иванов", "kind": ""}]
