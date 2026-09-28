"""Orchestration: schedule -> first lesson -> building address -> route -> exit plan.

Маршруты — только 2GIS (пешком и метро, машины нет), см. routing.py.

Honest failure modes (no invented times):
- schedule API down -> schedule_failed, schedule shown unavailable
- unknown building code -> unknown_building=True, no route
- 2GIS down/empty -> route_failed=True, schedule shown without road time
- metro not available between points -> walk fallback + metro_fallback=True
  ("маршрута на метро нет, показываю пешком"), never a fake metro time
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .buildings import BuildingStore
from .config import Settings
from .exit_time import ExitPlan, anchor_to_open, compute_exit, first_lesson_of_day, first_relevant_lesson
from .geocode import NominatimGeocoder
from .models import DaySchedule
from .normalize import normalize_day
from .places import PlacesError
from .routing import NoMetroError, RouteOption, get_metro_route, get_walking_route, moving_seconds
from .routing_base import RouteLeg, RouteResult
from .schedule_client import ScheduleClient
from .store import norm_transport


@dataclass(frozen=True)
class NightOutcome:
    """Итог ночного метро-расчёта (запрос в 01:00–05:30).

    kind: ok — выход после открытия, обычный расчёт;
          anchored — выход перенесён на 05:30, к паре успевает;
          miss — даже от 05:30 не успеть (+ пеший вариант, если реален);
          no_data — метро ночью не посчитать.
    """
    kind: str
    exit_at: datetime | None = None
    arrival_at: datetime | None = None
    travel_s: int | None = None
    walk_exit_at: datetime | None = None
    walk_travel_s: int | None = None
    walk_arrival_at: datetime | None = None


async def compute_night_exit(
    *,
    lesson_start: datetime,
    from_xy: tuple[float, float],
    to_xy: tuple[float, float],
    buffer_min: int,
    routing,
    open_dt: datetime,
    now: datetime,
    use_cache: bool = True,
) -> NightOutcome:
    """Метро ночью: езда берётся из moving-суммы (waiting ночью бессмыслен),
    старт отсчёта — от open_dt (05:30). См. anchor_to_open."""
    try:
        # Ночью waiting бессмыслен (закрыто/гнильё) — берём moving-сумму,
        # фильтр ожиданий и дневной кэш отключаем (max_wait_s=None).
        options = await routing.metro(from_xy, to_xy, use_cache=use_cache,
                                      max_wait_s=None)
    except Exception:
        return NightOutcome(kind="no_data")
    if not options:
        return NightOutcome(kind="no_data")
    travel_s = moving_seconds(options[0])
    exit_needed = lesson_start - timedelta(seconds=travel_s, minutes=buffer_min)
    kind, exit_at, arrival_at = anchor_to_open(exit_needed, lesson_start, travel_s, open_dt)
    if kind != "miss":
        return NightOutcome(kind=kind, exit_at=exit_at, arrival_at=arrival_at, travel_s=travel_s)
    walk_exit = walk_travel = walk_arr = None
    try:
        walk_opts = await routing.walking(from_xy, to_xy, use_cache=use_cache)
    except Exception:
        walk_opts = []
    if walk_opts:
        walk_travel = walk_opts[0].duration_s
        cand = lesson_start - timedelta(seconds=walk_travel, minutes=buffer_min)
        if cand >= now:
            walk_exit, walk_arr = cand, cand + timedelta(seconds=walk_travel)
    return NightOutcome(kind="miss", exit_at=exit_at, arrival_at=arrival_at, travel_s=travel_s,
                        walk_exit_at=walk_exit, walk_travel_s=walk_travel,
                        walk_arrival_at=walk_arr)


@dataclass(frozen=True)
class Window:
    """Окно между парами: from/to — 'HH:MM', minutes — длительность,
    from_room — кабинет пары ПЕРЕД окном (место ищем рядом с её корпусом)."""
    from_time: str
    to_time: str
    minutes: int
    from_room: str
    lesson: Any = None  # Lesson ПЕРЕД окном (для резолва корпуса)


def find_windows(schedule: DaySchedule, min_gap_min: int = 45) -> list[Window]:
    """Чистая функция: разрывы end->start следующей пары >= порога."""
    out: list[Window] = []
    lessons = list(schedule.active_lessons)
    for prev, nxt in zip(lessons, lessons[1:]):
        prev_end = prev.ends_at or (prev.starts_at + timedelta(minutes=90))
        gap = int((nxt.starts_at - prev_end).total_seconds() // 60)
        if gap >= min_gap_min:
            out.append(Window(from_time=prev_end.strftime("%H:%M"),
                              to_time=nxt.starts_at.strftime("%H:%M"),
                              minutes=gap, from_room=prev.room or "",
                              lesson=prev))
    return out


def _place_json(p) -> dict:
    return {"name": p.name, "category": p.category,
            "walk_min": p.walk_min, "address": p.address}


async def windows_with_places(schedule: DaySchedule, buildings: BuildingStore,
                              places_client, router,
                              min_gap_min: int = 45) -> list[dict]:
    """Окна + места рядом с корпусом пары ПЕРЕД окном.
    Поиск упал/ничего нет/корпус неизвестен -> places=[], место null у потребителя.
    Кэш 24 ч живёт внутри places_client."""
    out: list[dict] = []
    for w in find_windows(schedule, min_gap_min):
        lat = lon = None
        les = w.lesson
        if les is not None:
            b = buildings.lookup(les.building_code) if les.building_code else None
            if b is None:
                b, _heur = buildings.resolve_cabinet(les.room)
            if b is not None and b.lat is not None and b.lon is not None:
                lat, lon = b.lat, b.lon
        places: list = []
        if lat is not None and places_client is not None:
            try:
                found = await places_client.search(lat, lon)
                try:
                    await places_client.attach_walk_times(found, (lat, lon), router, limit=4)
                except Exception:
                    pass
                places = [_place_json(p) for p in found[:4]]
            except PlacesError:
                places = []
            except Exception:
                places = []
        out.append({"from": w.from_time, "to": w.to_time, "minutes": w.minutes,
                    "building": ({"lat": lat, "lon": lon} if lat is not None else None),
                    "places": places})
    return out


@dataclass
class DayView:
    schedule: DaySchedule
    skipped: int
    target: object | None  # Lesson | None
    plan: ExitPlan | None
    route_failed: bool = False
    unknown_building: bool = False
    schedule_failed: bool = False
    building_heuristic: bool = False  # cabinet->building by default rule, not certain
    metro_summary: str = ""  # first 2GIS option summary, e.g. "21 мин, 1 пересадка"
    metro_fallback: bool = False  # metro requested but unavailable -> walked instead


@dataclass(frozen=True)
class LessonTarget:
    """Пара + точка назначения для построения маршрута (без самого маршрута)."""
    lesson: object  # Lesson
    lat: float
    lon: float
    label: str  # "ауд. 0303, новый корпус" — для сообщений
    heuristic: bool = False


@dataclass(frozen=True)
class LessonTargetError:
    reason: str  # "schedule_failed" | "no_lessons" | "no_home" | "unknown_building"


async def lesson_target(
    *,
    settings: Settings,
    schedule_client: ScheduleClient,
    buildings: BuildingStore,
    group: str,
    day: date,
    now: datetime,
    for_today: bool,
) -> LessonTarget | LessonTargetError:
    """Расписание -> ближайшая пара -> координаты корпуса. Чистый шаг перед
    выбором типа маршрута (варианты строит уже routing.py по этим координатам)."""
    tz = ZoneInfo(settings.institution_tz)
    day_iso = day.strftime(settings.schedule_date_format)
    try:
        raw = await schedule_client.get_day_raw(group, day_iso)
    except Exception:
        return LessonTargetError("schedule_failed")
    schedule, _ = normalize_day(raw, group=group, day=day, tz_name=settings.institution_tz)
    target = first_relevant_lesson(schedule, now.astimezone(tz)) if for_today \
        else first_lesson_of_day(schedule)
    if target is None:
        return LessonTargetError("no_lessons")
    b = buildings.lookup(target.building_code) if target.building_code else None
    heuristic = False
    if b is None:
        b, heuristic = buildings.resolve_cabinet(target.room)
    if b is None or not b.address or b.lat is None or b.lon is None:
        return LessonTargetError("unknown_building")
    label = f"ауд. {target.room}" if target.room else b.address
    return LessonTarget(lesson=target, lat=b.lat, lon=b.lon, label=label, heuristic=heuristic)


def option_to_route(opt: RouteOption) -> RouteResult:
    """Первый вариант 2GIS -> RouteResult для compute_exit (реальные данные API)."""
    return RouteResult(
        travel_seconds=opt.duration_s,
        legs=(RouteLeg("2gis-" + opt.mode, opt.duration_s, opt.summary),),
        is_approximate=False,  # живой ответ API, не топологическая оценка
        calculated_at=datetime.now(timezone.utc),
        provider="2gis",
    )


async def build_day_view(
    *,
    settings: Settings,
    schedule_client: ScheduleClient,
    buildings: BuildingStore,
    geocoder: NominatimGeocoder,
    group: str,
    day: date,
    now: datetime,
    home_address: str,
    transport: str,
    buffer_min: int,
    for_today: bool,
    routing=None,  # TwoGisRouting-like (walking()/metro()); default — модуль routing.py
    home_coords: tuple[float, float] | None = None,  # (lat, lon) from location pin
    use_cache: bool = True,
    fresh: bool = False,  # True: расписание мимо дневного кэша (iOS API перечитывает)
) -> DayView:
    tz = ZoneInfo(settings.institution_tz)
    day_iso = day.strftime(settings.schedule_date_format)
    # Schedule JSON and home geocode are independent -> run in parallel.
    # If the schedule fails we cancel the stray geocode (no wasted work,
    # no extra latency on the schedule_failed path).
    sched_task = asyncio.ensure_future(schedule_client.get_day_raw(group, day_iso, fresh=fresh))
    geo_task = None
    if home_coords is None and home_address.strip():
        geo_task = asyncio.ensure_future(geocoder.geocode(home_address))
    try:
        raw = await sched_task
    except Exception:
        if geo_task is not None:
            geo_task.cancel()
        empty = DaySchedule(day=day, group=group, lessons=())
        return DayView(schedule=empty, skipped=0, target=None, plan=None, schedule_failed=True)
    schedule, skipped = normalize_day(raw, group=group, day=day, tz_name=settings.institution_tz)
    target = first_relevant_lesson(schedule, now.astimezone(tz)) if for_today else first_lesson_of_day(schedule)
    if target is None:
        return DayView(schedule=schedule, skipped=skipped, target=None, plan=None)
    has_home = bool(home_address.strip()) or home_coords is not None
    if not has_home:
        return DayView(schedule=schedule, skipped=skipped, target=target, plan=None, route_failed=True)
    # stankinapp has no corpus field: resolve via cabinet ("Фрезер 303" -> Фрезер, 10).
    # Explicit building_code (other APIs) still takes precedence.
    b = buildings.lookup(target.building_code) if target.building_code else None
    heuristic = False
    if b is None:
        b, heuristic = buildings.resolve_cabinet(target.room)
    if b is None:
        return DayView(schedule=schedule, skipped=skipped, target=target, plan=None, unknown_building=True)
    dest_addr = b.address if b else ""
    if not dest_addr:
        return DayView(schedule=schedule, skipped=skipped, target=target, plan=None, unknown_building=True)
    mode = norm_transport(transport)
    walk = routing.walking if routing is not None else get_walking_route
    metro = routing.metro if routing is not None else get_metro_route
    try:
        from_xy = home_coords or (await geo_task if geo_task is not None else None)
        # Verified building coords (buildings.yaml) skip geocoding entirely.
        to_xy = (b.lat, b.lon) if b.lat is not None and b.lon is not None else await geocoder.geocode(dest_addr)
        if not from_xy or not to_xy:
            return DayView(schedule=schedule, skipped=skipped, target=target, plan=None, route_failed=True)
        metro_fallback = False
        if mode == "metro":
            try:
                options = await metro(from_xy, to_xy, use_cache=use_cache)
            except NoMetroError:
                options = await walk(from_xy, to_xy, use_cache=use_cache)
                metro_fallback = True
        else:
            options = await walk(from_xy, to_xy, use_cache=use_cache)
        if not options:
            return DayView(schedule=schedule, skipped=skipped, target=target, plan=None, route_failed=True)
        route = option_to_route(options[0])
        metro_summary = options[0].summary
    except Exception:
        return DayView(schedule=schedule, skipped=skipped, target=target, plan=None, route_failed=True)
    plan = compute_exit(target, route, buffer_min, now.astimezone(tz))
    return DayView(schedule=schedule, skipped=skipped, target=target, plan=plan,
                   building_heuristic=heuristic, metro_summary=metro_summary,
                   metro_fallback=metro_fallback)
