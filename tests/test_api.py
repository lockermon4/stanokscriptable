"""iOS HTTP API: токены, структура today/tomorrow/exit-time, unavailable, suggest_next."""
import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

from student_bot.api import (ApiCtx, exit_time_payload, today_payload,
                             tomorrow_payload)
from student_bot.buildings import BuildingStore
from student_bot.config import Settings
from student_bot.health import build_app, start_health_server, stop_health_server
from student_bot.store import Store, UserSettings

TZ = ZoneInfo("Europe/Moscow")
S = Settings()
DAY = date(2026, 9, 28)  # понедельник


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def lessons_two():
    d = DAY.isoformat()
    return [
        {"subject": "Матан", "date": d, "startTime": "08:30", "endTime": "10:05",
         "cabinet": "0303", "type": "Лекция", "groupName": "G", "teacher": "Иванов"},
        {"subject": "Физика", "date": d, "startTime": "10:15", "endTime": "11:50",
         "cabinet": "0303", "type": "Лекция", "groupName": "G"},
    ]


class FakeSched:
    def __init__(self, payload=None, fail=False):
        self.payload = payload if payload is not None else lessons_two()
        self.fail = fail
        self.calls = []

    async def get_day_raw(self, group, day_iso, fresh=False):
        self.calls.append((group, day_iso, fresh))
        if self.fail:
            raise RuntimeError("api down")
        # normalize отбрасывает чужие даты — подменяем под запрошенный день
        return [dict(it, date=day_iso) for it in self.payload]


class FakeGeo:
    async def geocode(self, addr):
        return (55.63, 37.52)


class FakeRouting:
    def __init__(self, secs=1260):
        self.secs = secs

    async def walking(self, fr, to, allow_cache=True):
        from student_bot.routing import RouteOption
        return [RouteOption(mode="walk", duration_s=self.secs, distance_m=5000,
                            summary="21 мин пешком")]

    async def metro(self, fr, to, allow_cache=True):
        from student_bot.routing import RouteOption
        return [RouteOption(mode="metro", duration_s=self.secs, distance_m=8000,
                            transfers=1, walk_before_s=300, walk_after_s=240,
                            summary="21 мин, 1 пересадка")]


def _store():
    return BuildingStore.from_mapping(
        {"вадковский-3а": {"address": "Москва, Вадковский пер., 3А", "lat": 55.79, "lon": 37.59}},
        cabinet_default="вадковский-3а")


def make_ctx(path="/tmp/api_t.sqlite3", sched=None, routing=None):
    import os
    try:
        os.remove(path)
    except OSError:
        pass
    store = Store(path)
    return ApiCtx(settings=S, store=store, schedule_client=sched or FakeSched(),
                  buildings=_store(), geocoder=FakeGeo(), routing=routing or FakeRouting())


def make_user(store, **kw):
    args = dict(user_id=7, group="G", home_address="дом",
                transport="metro", buffer_min=10, lang="ru")
    args.update(kw)
    u = UserSettings(**args)
    store.save_user(u)
    return u


# ---------- токены ----------

def test_token_issue_get_reissue(tmp_path):
    s = Store(str(tmp_path / "t.sqlite3"))
    assert s.get_api_token(7) == ""
    t1 = s.issue_api_token(7)
    assert len(t1) > 20 and s.get_api_token(7) == t1
    assert s.user_id_by_token(t1) == 7
    assert s.user_id_by_token("nope") is None
    assert s.user_id_by_token("") is None
    t2 = s.issue_api_token(7)  # перевыпуск: новый валиден, старый нет
    assert t2 != t1 and s.user_id_by_token(t2) == 7
    assert s.user_id_by_token(t1) is None


# ---------- today: структура ----------

def test_today_structure_and_fresh_read():
    ctx = make_ctx()
    u = make_user(ctx.store)
    now = datetime(2026, 9, 28, 7, 0, tzinfo=TZ)
    p = run(today_payload(ctx, u, now))
    assert p["status"] == "ok" and p["kind"] == "today" and p["date"] == DAY.isoformat()
    assert len(p["lessons"]) == 2
    first = p["lessons"][0]
    assert first["time"] == "08:30" and first["subject"] == "Матан"
    assert first["room"] == "0303" and first["teacher"] == "Иванов"
    assert first["building"] == "вадковский-3а"
    assert set(p["push"]) == {"title", "body", "data"}  # схема push-шаблонов
    assert p["push"]["data"]["kind"] == "morning"
    f = p["focus"]
    assert f["time"] == "08:30" and f["exit"] == "07:59"  # 08:30-21мин-10мин
    assert f["travel_s"] == 1260 and f["verdict"] == "on_track"
    assert f["ongoing"] is False and p["suggest_next"] is None  # не идёт — не навязываем
    assert p["note"] == ""
    assert ctx.schedule_client.calls and ctx.schedule_client.calls[0][2] is True  # fresh=True


def test_today_ongoing_suggests_next_with_travel():
    ctx = make_ctx()
    u = make_user(ctx.store)
    now = datetime(2026, 9, 28, 9, 0, tzinfo=TZ)  # Матан идёт 08:30–10:05
    p = run(today_payload(ctx, u, now))
    f = p["focus"]
    assert f["ongoing"] is True and f["verdict"] == "ongoing_catchable"
    assert f["arrival_if_leave_now"] == "09:21" and f["minutes_left_if_leave_now"] == 44
    nxt = p["suggest_next"]
    assert nxt is not None and nxt["available"] is True
    assert nxt["time"] == "10:15" and nxt["subject"] == "Физика"
    assert nxt["travel_s"] == 1260 and nxt["exit_at"] == "09:44"
    assert nxt["can_catch_start"] is True  # 09:21 <= 10:15


def test_today_ongoing_no_next_gives_null():
    sched = FakeSched([dict(lessons_two()[0])])
    ctx = make_ctx(sched=sched)
    u = make_user(ctx.store)
    p = run(today_payload(ctx, u, datetime(2026, 9, 28, 9, 0, tzinfo=TZ)))
    assert p["focus"]["ongoing"] is True
    assert p["suggest_next"] is None  # следующей пары нет -> null


def test_today_no_group_and_schedule_down():
    ctx = make_ctx()
    u = make_user(ctx.store, group="")
    p = run(today_payload(ctx, u, datetime(2026, 9, 28, 7, 0, tzinfo=TZ)))
    assert p["status"] == "unavailable" and p["reason"] == "no_group"
    assert set(p["push"]) == {"title", "body", "data"}

    ctx2 = make_ctx("/tmp/api_t2.sqlite3", sched=FakeSched(fail=True))
    u2 = make_user(ctx2.store)
    p2 = run(today_payload(ctx2, u2, datetime(2026, 9, 28, 7, 0, tzinfo=TZ)))
    assert p2["status"] == "unavailable" and p2["reason"] == "schedule_failed"
    assert p2["push"]["data"] == {"kind": "today", "ok": False}  # ok=False, пустой день


# ---------- tomorrow: структура + заметка ----------

def test_tomorrow_structure_and_note():
    ctx = make_ctx()
    u = make_user(ctx.store)
    ctx.store.set_note(7, "2026-09-29", "взять халат")
    p = run(tomorrow_payload(ctx, u, datetime(2026, 9, 28, 21, 0, tzinfo=TZ)))
    assert p["status"] == "ok" and p["kind"] == "tomorrow" and p["date"] == "2026-09-29"
    assert len(p["lessons"]) == 2 and p["note"] == "взять халат"
    assert set(p["push"]) == {"title", "body", "data"}
    assert p["push"]["data"]["kind"] == "evening"
    assert p["push"]["data"]["count"] == 2


# ---------- exit-time: узкий ----------

def test_exit_time_narrow_and_verdicts():
    ctx = make_ctx()
    u = make_user(ctx.store)
    p = run(exit_time_payload(ctx, u, datetime(2026, 9, 28, 7, 0, tzinfo=TZ)))
    assert p["status"] == "ok" and p["kind"] == "exit-time" and p["at"] == "07:00"
    assert p["exit"] == "07:59" and p["travel_s"] == 1260
    assert p["arrival"] == "08:20" and p["verdict"] == "on_track"
    assert set(p["push"]) == {"title", "body", "data"}

    late = run(exit_time_payload(ctx, u, datetime(2026, 9, 28, 10, 30, tzinfo=TZ)))
    assert late["focus"]["time"] == "10:15"  # первая кончилась — фокус на следующей
    assert late["verdict"] == "ongoing_catchable"  # 10:30 внутри 10:15–11:50, приезд 10:51

    done = run(exit_time_payload(ctx, u, datetime(2026, 9, 28, 23, 0, tzinfo=TZ)))
    assert done["focus"] is None and done["push"]["data"]["kind"] == "morning"


# ---------- HTTP-уровень: 401 ----------

def test_http_401_and_200():
    import urllib.request

    async def go():
        ctx = make_ctx("/tmp/api_t3.sqlite3")
        u = make_user(ctx.store)
        token = ctx.store.issue_api_token(7)
        runner = await start_health_server(0, ctx)
        try:
            port = runner.addresses[0][1]

            def get(path):
                import urllib.error
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}",
                                                timeout=10) as r:
                        return r.status, r.read()
                except urllib.error.HTTPError as e:
                    return e.code, e.read()

            import json
            loop = asyncio.get_running_loop()

            async def fetch(path):
                return await loop.run_in_executor(None, get, path)

            s1, _ = await fetch("/api/v1/today")
            assert s1 == 401  # без токена
            s2, b2 = await fetch("/api/v1/today?token=nope")
            assert s2 == 401  # чужой токен
            import json as j
            assert j.loads(b2)["error"] == "invalid_token"
            s3, b3 = await fetch(f"/api/v1/exit-time?token={token}")
            assert s3 == 200  # свой токен
            assert j.loads(b3)["status"] == "ok"
        finally:
            await stop_health_server(runner)

    run(go())
