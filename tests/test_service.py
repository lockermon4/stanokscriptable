"""Service-level: unknown building / route failure / schedule failure honesty."""
import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

from student_bot.buildings import BuildingStore
from student_bot.config import Settings
from student_bot.service import build_day_view

TZ = ZoneInfo("Europe/Moscow")
S = Settings(schedule_api_base="https://x.test")


class FakeSched:
    def __init__(self, payload):
        self.payload = payload

    async def get_day_raw(self, group, day_iso):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeGeo:
    def __init__(self, ok=True):
        self.ok = ok

    async def geocode(self, addr):
        return (37.6, 55.7) if self.ok else None


class FakeRouter:
    supports_transit = False
    supports_arrival_time = False
    supports_live_traffic = False

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    async def route(self, a, b, mode, arrive_by=None):
        from datetime import timezone

        self.calls += 1
        if self.fail:
            raise RuntimeError("down")
        from student_bot.routing_base import RouteLeg, RouteResult
        return RouteResult(travel_seconds=1200, legs=(RouteLeg(mode, 1200),),
                           is_approximate=True, calculated_at=datetime.now(timezone.utc), provider="fake")


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _store():
    return BuildingStore.from_mapping(
        {"вадковский-3а": "Москва, Вадковский пер., 3А", "фрезер-10": "Москва, шоссе Фрезер, 10"},
        cabinet_rules={"фрезер": "фрезер-10"},
        cabinet_default="вадковский-3а",
    )


def test_confirmed_gate_from_yaml():
    from student_bot.buildings import load_buildings_yaml
    import os
    p = os.path.join(os.path.dirname(__file__), "..", "buildings.yaml")
    s = load_buildings_yaml(p)
    for code in ("фрезер-10", "стадион", "старый", "новый"):
        assert s.lookup(code) is not None, code  # все подтверждены
    b, _ = s.resolve_cabinet("Фрезер 303(ММ)")
    assert b is not None and b.code == "фрезер-10"
    assert s.resolve_cabinet("0209")[0].code == "новый"
    assert s.resolve_cabinet("ИГ-1")[0].code == "старый"
    assert s.resolve_cabinet("Стадион 1")[0].code == "стадион"
    assert s.resolve_cabinet("С/З СТАНКИН")[0].code == "новый"
    assert s.resolve_cabinet("") == (None, False)


def test_unknown_building_no_invented_route():
    # empty cabinet -> honestly unknown, no guessing
    sched = FakeSched([{"subject": "М", "date": "2026-09-24", "startTime": "09:00", "endTime": "10:30",
                        "cabinet": "", "type": "Лекция", "groupName": "G"}])
    view = run(build_day_view(settings=S, schedule_client=sched, buildings=_store(),
                              geocoder=FakeGeo(), router=FakeRouter(), group="G",
                              day=date(2026, 9, 24), now=datetime(2026, 9, 24, 7, tzinfo=TZ),
                              home_address="дом", transport="transit", buffer_min=10, for_today=False))
    assert view.unknown_building and view.plan is None


def test_route_failure_shows_schedule_without_times():
    sched = FakeSched([{"subject": "М", "date": "2026-09-24", "startTime": "09:00", "endTime": "10:30",
                        "cabinet": "0303", "type": "Лекция", "groupName": "G"}])
    view = run(build_day_view(settings=S, schedule_client=sched, buildings=_store(),
                              geocoder=FakeGeo(), router=FakeRouter(fail=True), group="G",
                              day=date(2026, 9, 24), now=datetime(2026, 9, 24, 7, tzinfo=TZ),
                              home_address="дом", transport="transit", buffer_min=10, for_today=False))
    assert view.route_failed and view.plan is None and view.schedule.count == 1


def test_frezer_cabinet_resolves_to_frezer():
    sched = FakeSched([{"subject": "М", "date": "2026-09-24", "startTime": "09:00", "endTime": "10:30",
                        "cabinet": "Фрезер 303(ММ)", "type": "Лекция", "groupName": "G"}])
    view = run(build_day_view(settings=S, schedule_client=sched, buildings=_store(),
                              geocoder=FakeGeo(), router=FakeRouter(), group="G",
                              day=date(2026, 9, 24), now=datetime(2026, 9, 24, 7, tzinfo=TZ),
                              home_address="дом", transport="transit", buffer_min=10, for_today=False))
    assert view.plan is not None and view.building_heuristic is False


def test_plain_cabinet_uses_heuristic_default():
    sched = FakeSched([{"subject": "М", "date": "2026-09-24", "startTime": "09:00", "endTime": "10:30",
                        "cabinet": "0303", "type": "Лекция", "groupName": "G"}])
    view = run(build_day_view(settings=S, schedule_client=sched, buildings=_store(),
                              geocoder=FakeGeo(), router=FakeRouter(), group="G",
                              day=date(2026, 9, 24), now=datetime(2026, 9, 24, 7, tzinfo=TZ),
                              home_address="дом", transport="transit", buffer_min=10, for_today=False))
    assert view.plan is not None and view.building_heuristic is True


def test_schedule_api_failure_flag():
    sched = FakeSched(RuntimeError("down"))
    view = run(build_day_view(settings=S, schedule_client=sched, buildings=BuildingStore([]),
                              geocoder=FakeGeo(), router=FakeRouter(), group="G",
                              day=date(2026, 9, 24), now=datetime(2026, 9, 24, 7, tzinfo=TZ),
                              home_address="дом", transport="transit", buffer_min=10, for_today=False))
    assert view.schedule_failed
