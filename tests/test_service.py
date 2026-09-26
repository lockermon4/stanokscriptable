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

    async def get_day_raw(self, group, day_iso, fresh=False):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeGeo:
    def __init__(self, ok=True):
        self.ok = ok

    async def geocode(self, addr):
        return (37.6, 55.7) if self.ok else None


class FakeRouting:
    """TwoGisRouting-like: walking()/metro() с флагами падения."""

    def __init__(self, fail_walk=False, fail_metro=False, no_metro=False, secs=1200):
        self.fail_walk = fail_walk
        self.fail_metro = fail_metro
        self.no_metro = no_metro
        self.secs = secs
        self.calls: list[str] = []

    def _opt(self, mode):
        from student_bot.routing import RouteOption
        return RouteOption(mode=mode, duration_s=self.secs, distance_m=5000,
                           summary=f"{mode} {self.secs // 60} мин")

    async def walking(self, fr, to, use_cache=True):
        self.calls.append("walk")
        if self.fail_walk:
            raise RuntimeError("down")
        return [self._opt("walk")]

    async def metro(self, fr, to, use_cache=True):
        self.calls.append("metro")
        if self.fail_metro:
            raise RuntimeError("down")
        if self.no_metro:
            from student_bot.routing import NoMetroError
            raise NoMetroError("нет метро")
        return [self._opt("metro")]


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _store():
    return BuildingStore.from_mapping(
        {"вадковский-3а": {"address": "Москва, Вадковский пер., 3А", "lat": 55.79, "lon": 37.59},
         "фрезер-10": {"address": "Москва, шоссе Фрезер, 10", "lat": 55.73, "lon": 37.73}},
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
                              geocoder=FakeGeo(), routing=FakeRouting(), group="G",
                              day=date(2026, 9, 24), now=datetime(2026, 9, 24, 7, tzinfo=TZ),
                              home_address="дом", transport="metro", buffer_min=10, for_today=False))
    assert view.unknown_building and view.plan is None


def test_route_failure_shows_schedule_without_times():
    sched = FakeSched([{"subject": "М", "date": "2026-09-24", "startTime": "09:00", "endTime": "10:30",
                        "cabinet": "0303", "type": "Лекция", "groupName": "G"}])
    view = run(build_day_view(settings=S, schedule_client=sched, buildings=_store(),
                              geocoder=FakeGeo(), routing=FakeRouting(fail_walk=True, fail_metro=True), group="G",
                              day=date(2026, 9, 24), now=datetime(2026, 9, 24, 7, tzinfo=TZ),
                              home_address="дом", transport="metro", buffer_min=10, for_today=False))
    assert view.route_failed and view.plan is None and view.schedule.count == 1


def test_frezer_cabinet_resolves_to_frezer():
    sched = FakeSched([{"subject": "М", "date": "2026-09-24", "startTime": "09:00", "endTime": "10:30",
                        "cabinet": "Фрезер 303(ММ)", "type": "Лекция", "groupName": "G"}])
    view = run(build_day_view(settings=S, schedule_client=sched, buildings=_store(),
                              geocoder=FakeGeo(), routing=FakeRouting(), group="G",
                              day=date(2026, 9, 24), now=datetime(2026, 9, 24, 7, tzinfo=TZ),
                              home_address="дом", transport="metro", buffer_min=10, for_today=False))
    assert view.plan is not None and view.building_heuristic is False


def test_plain_cabinet_uses_heuristic_default():
    sched = FakeSched([{"subject": "М", "date": "2026-09-24", "startTime": "09:00", "endTime": "10:30",
                        "cabinet": "0303", "type": "Лекция", "groupName": "G"}])
    view = run(build_day_view(settings=S, schedule_client=sched, buildings=_store(),
                              geocoder=FakeGeo(), routing=FakeRouting(), group="G",
                              day=date(2026, 9, 24), now=datetime(2026, 9, 24, 7, tzinfo=TZ),
                              home_address="дом", transport="metro", buffer_min=10, for_today=False))
    assert view.plan is not None and view.building_heuristic is True


def test_schedule_api_failure_flag():
    sched = FakeSched(RuntimeError("down"))
    view = run(build_day_view(settings=S, schedule_client=sched, buildings=BuildingStore([]),
                              geocoder=FakeGeo(), routing=FakeRouting(), group="G",
                              day=date(2026, 9, 24), now=datetime(2026, 9, 24, 7, tzinfo=TZ),
                              home_address="дом", transport="metro", buffer_min=10, for_today=False))
    assert view.schedule_failed


def _sched_one():
    return FakeSched([{"subject": "М", "date": "2026-09-24", "startTime": "09:00", "endTime": "10:30",
                       "cabinet": "0303", "type": "Лекция", "groupName": "G"}])


def test_metro_unavailable_falls_back_to_walk():
    r = FakeRouting(no_metro=True)
    view = run(build_day_view(settings=S, schedule_client=_sched_one(), buildings=_store(),
                              geocoder=FakeGeo(), routing=r, group="G",
                              day=date(2026, 9, 24), now=datetime(2026, 9, 24, 7, tzinfo=TZ),
                              home_address="дом", transport="metro", buffer_min=10, for_today=False))
    assert view.plan is not None and view.metro_fallback is True
    assert r.calls == ["metro", "walk"]  # сначала метро, потом пешком


def test_walk_mode_uses_first_option():
    r = FakeRouting(secs=1800)
    view = run(build_day_view(settings=S, schedule_client=_sched_one(), buildings=_store(),
                              geocoder=FakeGeo(), routing=r, group="G",
                              day=date(2026, 9, 24), now=datetime(2026, 9, 24, 7, tzinfo=TZ),
                              home_address="дом", transport="walk", buffer_min=10, for_today=False))
    assert view.plan is not None and view.plan.travel_seconds == 1800
    assert view.metro_fallback is False and view.plan.exit_at.strftime("%H:%M") == "08:20"


def test_lesson_target_ok_and_errors():
    from student_bot.service import LessonTargetError, lesson_target
    kw = dict(settings=S, schedule_client=_sched_one(), buildings=_store(),
              group="G", day=date(2026, 9, 24),
              now=datetime(2026, 9, 24, 7, tzinfo=TZ), for_today=False)
    tgt = run(lesson_target(**kw))
    assert not isinstance(tgt, LessonTargetError)
    assert abs(tgt.lat) > 0 and "0303" in tgt.label
    bad = run(lesson_target(settings=S, schedule_client=FakeSched(RuntimeError("x")),
                            buildings=_store(), group="G", day=date(2026, 9, 24),
                            now=datetime(2026, 9, 24, 7, tzinfo=TZ), for_today=False))
    assert isinstance(bad, LessonTargetError) and bad.reason == "schedule_failed"
