"""Окна между парами + места 2GIS: окно+место, place=null при сбое,
порог разрыва, кэш 24 ч без повторных запросов."""
import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from student_bot.models import DaySchedule, Lesson
from student_bot.places import Place, PlacesError, TwoGisPlaces
from student_bot.service import find_windows, windows_with_places

TZ = ZoneInfo("Europe/Moscow")
DAY = date(2026, 9, 28)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def les(h1, m1, h2, m2, room="0209", cancelled=False):
    return Lesson(group="G", day=DAY,
                  starts_at=datetime(2026, 9, 28, h1, m1, tzinfo=TZ),
                  ends_at=datetime(2026, 9, 28, h2, m2, tzinfo=TZ),
                  subject="М", room=room,
                  status="cancelled" if cancelled else "scheduled")


def sched(*lessons):
    return DaySchedule(day=DAY, group="G", lessons=tuple(lessons))


class FakeBuildings:
    def lookup(self, code):
        return None

    def resolve_cabinet(self, room):
        from types import SimpleNamespace
        if not room:
            return None, False
        return SimpleNamespace(code="old", lat=55.79, lon=37.59), False


class FakeRouter:
    def __init__(self):
        self.calls = 0

    async def walking(self, fr, to, use_cache=True):
        from student_bot.routing import RouteOption
        self.calls += 1
        return [RouteOption(mode="walk", duration_s=180, summary="3 мин")]  # 3 мин


def test_window_with_place_found():
    sched1 = sched(les(8, 30, 10, 0), les(11, 0, 12, 30))  # разрыв 60 мин
    wins = find_windows(sched1)
    assert len(wins) == 1
    assert (wins[0].from_time, wins[0].to_time, wins[0].minutes) == ("10:00", "11:00", 60)

    # подменяем search целиком (кэш тестируем отдельно ниже)
    class P:
        async def search(self, lat, lon, use_cache=True):
            return [Place(name="Кофейня", category="Кофейни", lat=55.791, lon=37.591,
                          address="ул. X", raw={"id": "1"})]

        async def attach_walk_times(self, places, from_xy, router, limit=4):
            for p in places:
                p.walk_min = 3

    async def go2():
        rich = await windows_with_places(sched1, FakeBuildings(), P(), FakeRouter(), 45)
        return rich

    rich = run(go2())
    assert len(rich) == 1
    assert rich[0]["from"] == "10:00" and rich[0]["minutes"] == 60
    assert rich[0]["places"][0]["name"] == "Кофейня"
    assert rich[0]["places"][0]["walk_min"] == 3


def test_window_kept_place_null_on_api_failure():
    sched1 = sched(les(8, 30, 10, 0), les(11, 0, 12, 30))

    class DeadPlaces:
        async def search(self, lat, lon, use_cache=True):
            raise PlacesError("403")

        async def attach_walk_times(self, places, from_xy, router, limit=4):
            pass

    async def go():
        return await windows_with_places(sched1, FakeBuildings(), DeadPlaces(),
                                         FakeRouter(), 45)

    rich = run(go())
    assert len(rich) == 1 and rich[0]["places"] == []  # окно есть, места нет


def test_small_gap_is_not_a_window():
    sched1 = sched(les(8, 30, 10, 0), les(10, 15, 11, 50))  # разрыв 15 мин
    assert find_windows(sched1, 45) == []
    sched2 = sched(les(8, 30, 10, 0), les(10, 45, 12, 0))  # ровно 45 мин
    assert len(find_windows(sched2, 45)) == 1


def test_places_cache_no_second_request():
    import httpx

    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(200, json={
            "meta": {"code": 200},
            "result": {"items": [
                {"id": "9", "name": "Кофейня", "type": "branch",
                 "point": {"lat": 55.791, "lon": 37.591},
                 "address_name": "ул. X",
                 "rubrics": [{"name": "Кофейни", "kind": "primary"}]}],
                "total": 1}})

    async def go():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        p = TwoGisPlaces(api_key="k", http=http)
        first = await p.search(55.79, 37.59)  # 4 запроса (по одному на категорию)
        n1 = len(calls)
        assert n1 == 4 and len(first) >= 1
        second = await p.search(55.79, 37.59)  # всё из кэша 24 ч
        assert len(calls) == n1
        assert [x.name for x in second] == [x.name for x in first]
        await p.close()

    run(go())
