"""Ночь 01:00–05:30 (якорь 05:30) + 🎯 целевое прибытие + новые кнопки."""
import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from student_bot.exit_time import anchor_to_open, parse_target_time
from student_bot.routing import RouteOption

TZ = ZoneInfo("Europe/Moscow")
DAY = date(2026, 9, 28)
OPEN = datetime(2026, 9, 28, 5, 30, tzinfo=TZ)
NIGHT = datetime(2026, 9, 28, 3, 0, tzinfo=TZ)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def lesson_at(h, mi, subj="М"):
    from student_bot.models import Lesson
    s = datetime(2026, 9, 28, h, mi, tzinfo=TZ)
    return Lesson(group="G", day=DAY, starts_at=s,
                  ends_at=s + timedelta(minutes=95), subject=subj, room="0303")


def metro_opt(moving_s, wait_s=60):
    return RouteOption(mode="metro", duration_s=moving_s + wait_s, summary="m",
                       raw={"movements": [
                           {"type": "walkway", "moving_duration": 300},
                           {"type": "passage", "moving_duration": moving_s - 600,
                            "waiting_duration": wait_s},
                           {"type": "walkway", "moving_duration": 300}]})


class FakeRouting:
    def __init__(self, metro_s=3600, walk_s=1800):
        self.metro_s = metro_s
        self.walk_s = walk_s
        self.metro_kwargs = None

    async def metro(self, fr, to, use_cache=True, max_wait_s=1800):
        self.metro_kwargs = {"use_cache": use_cache, "max_wait_s": max_wait_s}
        base = metro_opt(self.metro_s).raw["movements"]
        return [RouteOption(mode="metro", duration_s=self.metro_s + 60,
                            summary="m", raw={"movements": base})]

    async def walking(self, fr, to, use_cache=True):
        return [RouteOption(mode="walk", duration_s=self.walk_s, summary="w")]


# ---------- P1a: ночью успевает от 05:30 ----------

def test_night_anchored_from_open():
    from student_bot.service import compute_night_exit
    # Пара 06:35, езда 60 мин, запас 10: нужно выйти 05:25 < 05:30,
    # но от 05:30 приезд 06:30 <= 06:35 — успевает.
    out = run(compute_night_exit(
        lesson_start=datetime(2026, 9, 28, 6, 35, tzinfo=TZ),
        from_xy=(55.6, 37.5), to_xy=(55.79, 37.59), buffer_min=10,
        routing=FakeRouting(metro_s=3600), open_dt=OPEN, now=NIGHT))
    assert out.kind == "anchored"
    assert out.exit_at == OPEN
    assert out.arrival_at == datetime(2026, 9, 28, 6, 30, tzinfo=TZ)
    assert out.travel_s == 3600  # moving-сумма, waiting 60с проигнорирован


def test_night_uses_moving_despite_insane_wait():
    # Живой кейс 02:15: все 3 опции с waiting>1800 — ночной расчёт берёт
    # moving-сумму, а не no_data.
    from student_bot.service import compute_night_exit
    routing = FakeRouting(metro_s=3600)
    out = run(compute_night_exit(
        lesson_start=datetime(2026, 9, 28, 8, 30, tzinfo=TZ),
        from_xy=(55.6, 37.5), to_xy=(55.79, 37.59), buffer_min=10,
        routing=routing, open_dt=OPEN, now=NIGHT))
    assert out.kind == "ok" and out.travel_s == 3600
    assert routing.metro_kwargs["max_wait_s"] is None  # фильтр отключён ночью


def test_night_ok_when_exit_after_open():
    from student_bot.service import compute_night_exit
    # Пара 08:30: нужно выйти 07:20 >= 05:30 — обычный расчёт.
    out = run(compute_night_exit(
        lesson_start=datetime(2026, 9, 28, 8, 30, tzinfo=TZ),
        from_xy=(55.6, 37.5), to_xy=(55.79, 37.59), buffer_min=10,
        routing=FakeRouting(metro_s=3600), open_dt=OPEN, now=NIGHT))
    assert out.kind == "ok"
    assert out.exit_at == datetime(2026, 9, 28, 7, 20, tzinfo=TZ)


# ---------- P1b: ночью не успевает даже от 05:30 (+ пешком) ----------

def test_night_miss_with_walk_alternative():
    from student_bot.service import compute_night_exit
    # Пара 06:00, езда 60 мин: от 05:30 приезд 06:30 > 06:00 — miss.
    # Пешком 30 мин: выйти 05:20 >= now 03:00 — вариант реален.
    out = run(compute_night_exit(
        lesson_start=datetime(2026, 9, 28, 6, 0, tzinfo=TZ),
        from_xy=(55.6, 37.5), to_xy=(55.79, 37.59), buffer_min=10,
        routing=FakeRouting(metro_s=3600, walk_s=1800), open_dt=OPEN, now=NIGHT))
    assert out.kind == "miss"
    assert out.exit_at == OPEN
    assert out.walk_exit_at == datetime(2026, 9, 28, 5, 20, tzinfo=TZ)
    assert out.walk_arrival_at == datetime(2026, 9, 28, 5, 50, tzinfo=TZ)


def test_night_no_data_when_metro_down():
    from student_bot.service import compute_night_exit

    class Dead:
        async def metro(self, fr, to, use_cache=True):
            raise RuntimeError("down")

        async def walking(self, fr, to, use_cache=True):
            raise RuntimeError("down")

    out = run(compute_night_exit(
        lesson_start=datetime(2026, 9, 28, 8, 30, tzinfo=TZ),
        from_xy=(55.6, 37.5), to_xy=(55.79, 37.59), buffer_min=10,
        routing=Dead(), open_dt=OPEN, now=NIGHT))
    assert out.kind == "no_data" and out.exit_at is None


def test_moving_seconds_ignores_waiting():
    o = metro_opt(3600, wait_s=15708)  # живой мусор 2026-09-28
    from student_bot.routing import moving_seconds
    assert moving_seconds(o) == 3600
    assert moving_seconds(RouteOption(mode="metro", duration_s=999, summary="x")) == 999


def test_anchor_to_open_branches():
    start = datetime(2026, 9, 28, 8, 30, tzinfo=TZ)
    k, e, a = anchor_to_open(datetime(2026, 9, 28, 7, 20, tzinfo=TZ), start, 3600, OPEN)
    assert (k, e.strftime("%H:%M"), a.strftime("%H:%M")) == ("ok", "07:20", "08:20")
    k, e, a = anchor_to_open(datetime(2026, 9, 28, 5, 25, tzinfo=TZ),
                             datetime(2026, 9, 28, 6, 35, tzinfo=TZ), 3600, OPEN)
    assert (k, e.strftime("%H:%M"), a.strftime("%H:%M")) == ("anchored", "05:30", "06:30")
    k, e, a = anchor_to_open(datetime(2026, 9, 28, 4, 50, tzinfo=TZ),
                             datetime(2026, 9, 28, 6, 0, tzinfo=TZ), 3600, OPEN)
    assert k == "miss" and e == OPEN


def test_parse_target_time():
    assert parse_target_time("08:00", DAY, TZ) == datetime(2026, 9, 28, 8, 0, tzinfo=TZ)
    assert parse_target_time(" 8:05 ", DAY, TZ) == datetime(2026, 9, 28, 8, 5, tzinfo=TZ)
    assert parse_target_time("8-05", DAY, TZ) is None
    assert parse_target_time("25:00", DAY, TZ) is None
    assert parse_target_time("08:60", DAY, TZ) is None
    assert parse_target_time("", DAY, TZ) is None


# ---------- P2: target_arrival в API, день ----------

def _api_fakes(path, sched=None, routing=None):
    import os
    from student_bot.api import ApiCtx
    from student_bot.buildings import BuildingStore
    from student_bot.config import Settings
    try:
        os.remove(path)
    except OSError:
        pass
    from student_bot.store import Store

    class Sched:
        def __init__(self, payload):
            self.payload = payload

        async def get_day_raw(self, group, day_iso, fresh=False):
            return [dict(it, date=day_iso) for it in self.payload]

    class Geo:
        async def geocode(self, addr):
            return (55.63, 37.52)

    d = DAY.isoformat()
    lessons = [
        {"subject": "М", "date": d, "startTime": "08:30", "endTime": "10:05",
         "cabinet": "0303", "type": "Лекция", "groupName": "G"},
        {"subject": "Ф", "date": d, "startTime": "10:15", "endTime": "11:50",
         "cabinet": "0303", "type": "Лекция", "groupName": "G"},
    ]
    store = Store(path)
    buildings = BuildingStore.from_mapping(
        {"b": {"address": "А", "lat": 55.79, "lon": 37.59}},
        cabinet_default="b")
    return ApiCtx(settings=Settings(), store=store,
                  schedule_client=sched or Sched(lessons), buildings=buildings,
                  geocoder=Geo(), routing=routing or FakeRouting(metro_s=1260, walk_s=1800))


def _api_user(store):
    from student_bot.store import UserSettings
    u = UserSettings(user_id=7, group="G", home_address="дом",
                     transport="metro", buffer_min=10, lang="ru")
    store.save_user(u)
    return u


def test_api_target_today_daytime():
    from student_bot.api import today_payload
    ctx = _api_fakes("/tmp/nt1.sqlite3")
    u = _api_user(ctx.store)
    # Хочу быть к 08:00: выйти 08:00-21мин-10мин = 07:29 >= 05:30 -> ok.
    p = run(today_payload(ctx, u, datetime(2026, 9, 28, 7, 0, tzinfo=TZ), target="08:00"))
    assert p["status"] == "ok"
    t = p["target"]
    assert t["requested"] == "08:00" and t["outcome"] == "ok"
    # приезд = цель − запас: 08:00 − 10 мин = 07:50, выход 07:28
    assert t["exit"] == "07:28" and t["arrival"] == "07:50"


def test_api_target_tomorrow_daytime():
    from student_bot.api import tomorrow_payload
    ctx = _api_fakes("/tmp/nt2.sqlite3")
    u = _api_user(ctx.store)
    p = run(tomorrow_payload(ctx, u, datetime(2026, 9, 28, 21, 0, tzinfo=TZ), target="08:00"))
    assert p["status"] == "ok" and p["date"] == "2026-09-29"
    t = p["target"]
    assert t["outcome"] == "ok" and t["exit"] == "07:28"


def test_api_target_bad_format():
    from student_bot.api import parse_target_or_400
    dt, err = parse_target_or_400("08:00", DAY, TZ)
    assert dt is not None and err is None
    dt, err = parse_target_or_400("8-00", DAY, TZ)
    assert dt is None and err["error"] == "bad_target_arrival"


def test_api_target_night_gate_both_days():
    from student_bot.api import today_payload, tomorrow_payload
    # Ночь 03:00, хочу быть к 06:00: выйти 05:29 < 05:30, приезд 06:00 <= 08:30 -> anchored.
    ctx = _api_fakes("/tmp/nt3.sqlite3")
    u = _api_user(ctx.store)
    p = run(today_payload(ctx, u, NIGHT, target="06:00"))
    t = p["target"]
    assert t["outcome"] == "anchored" and t["exit"] == "05:30" and t["arrival"] == "05:52"
    ctx2 = _api_fakes("/tmp/nt4.sqlite3")
    u2 = _api_user(ctx2.store)
    p2 = run(tomorrow_payload(ctx2, u2, datetime(2026, 9, 28, 3, 0, tzinfo=TZ), target="06:00"))
    assert p2["target"]["outcome"] == "anchored"


def test_api_target_night_miss():
    from student_bot.api import today_payload
    # Езда 3.5ч: выйти 04:20 < 05:30, приезд 09:00 > 08:30 -> miss + пешком.
    ctx = _api_fakes("/tmp/nt5.sqlite3", routing=FakeRouting(metro_s=12600, walk_s=1800))
    u = _api_user(ctx.store)
    p = run(today_payload(ctx, u, NIGHT, target="08:00"))
    t = p["target"]
    assert t["outcome"] == "miss" and t["exit"] == "05:30"
    assert t["walk_exit"] == "07:20"  # пешком 30 мин: 08:00-40мин, >= now 03:00


# ---------- P3: новые кнопки ----------

def test_new_button_callbacks():
    from student_bot.texts import (addr_confirm_buttons, addr_pick_buttons,
                                   ask_target_time, target_buttons, variants_buttons)
    d = [b.callback_data for row in addr_confirm_buttons("ru").inline_keyboard for b in row]
    assert d == ["addr:yes", "addr:no"]
    picks = [("Москва, ул. А", 1.0, 2.0), ("Москва, ул. Б", 3.0, 4.0)]
    d2 = [b.callback_data for row in addr_pick_buttons(picks, "ru").inline_keyboard for b in row]
    assert d2[:2] == ["addrpick:0", "addrpick:1"] and "op:cancel" in d2
    d3 = [b.callback_data for row in target_buttons("ru").inline_keyboard for b in row]
    assert d3 == ["rt:target"]
    assert "ЧЧ:ММ" in ask_target_time("ru", "08:30 — М")
    o = metro_opt(1260)
    d4 = [b.callback_data for row in variants_buttons([o], "ru").inline_keyboard for b in row]
    assert d4[0] == "rtv:0" and "rt:target" in d4


def test_night_and_target_texts():
    from student_bot.service import NightOutcome
    from student_bot.texts import night_exit_text, target_exit_text
    line = "08:30 — М"
    ok = NightOutcome(kind="anchored", exit_at=OPEN,
                      arrival_at=datetime(2026, 9, 28, 6, 30, tzinfo=TZ), travel_s=3600)
    t = night_exit_text("ru", line, ok)
    assert "05:30" in t and "метро откроется" in t
    miss = NightOutcome(kind="miss", exit_at=OPEN,
                        arrival_at=datetime(2026, 9, 28, 6, 30, tzinfo=TZ), travel_s=3600,
                        walk_exit_at=datetime(2026, 9, 28, 5, 20, tzinfo=TZ),
                        walk_travel_s=1800,
                        walk_arrival_at=datetime(2026, 9, 28, 5, 50, tzinfo=TZ))
    t2 = night_exit_text("ru", line, miss)
    assert "не успеть" in t2 and "05:20" in t2
    t3 = target_exit_text("ru", line, "ok", datetime(2026, 9, 28, 7, 29, tzinfo=TZ),
                          datetime(2026, 9, 28, 8, 0, tzinfo=TZ), "~21 мин")
    assert "07:29" in t3 and "08:00" in t3
