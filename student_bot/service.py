"""Orchestration: schedule -> first lesson -> building address -> route -> exit plan.

Honest failure modes (no invented times):
- schedule API down -> ScheduleApiError propagates, bot shows schedule-unavailable
- unknown building code -> unknown_building=True, no route
- geocode/route down -> route_failed=True, schedule shown without road time
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .buildings import BuildingStore
from .config import Settings
from .exit_time import ExitPlan, compute_exit, first_lesson_of_day, first_relevant_lesson
from .geocode import NominatimGeocoder
from .metro import MetroGraph
from .models import DaySchedule
from .normalize import normalize_day
from .routing_base import RouteLeg, RouteResult
from .routing_osrm import OsrmProvider
from .schedule_client import ScheduleClient


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
    metro_summary: str = ""  # e.g. "метро Савёловская → Новослободская: 1 перегон, 1 пересадка"


async def _walk_seconds(foot, a_lonlat: tuple[float, float], b_lonlat: tuple[float, float]) -> int:
    """`foot` is FosFootProvider (preferred) or any router with .route()."""
    if hasattr(foot, "foot_seconds"):
        return await foot.foot_seconds(a_lonlat, b_lonlat)
    r = await foot.route(a_lonlat, b_lonlat, "foot")
    return r.travel_seconds


async def _door_to_door_transit(
    foot, metro: MetroGraph,
    from_xy: tuple[float, float], to_xy: tuple[float, float], n_each: int = 3,
) -> tuple[RouteResult, str]:
    """Minimize full door-to-door over TOP-N entry AND exit stations:
    total = walk(home->A) + [board + legs + transfers] + walk(B->corp).
    Falls back to direct walk when it wins. All legs measured, nothing
    double-counted: BOARD_WAIT lives inside ride.seconds exactly once.
    from_xy/to_xy are (lat, lon); foot takes (lon, lat)."""
    from datetime import datetime, timezone

    now_utc = datetime.now(timezone.utc)
    boards = metro.nearest_n(from_xy[0], from_xy[1], n_each)
    alights = metro.nearest_n(to_xy[0], to_xy[1], n_each)

    walks_in = {(s.lon, s.lat): await _walk_seconds(foot, (from_xy[1], from_xy[0]), (s.lon, s.lat))
                for s in boards}
    walks_out = {(s.lon, s.lat): await _walk_seconds(foot, (s.lon, s.lat), (to_xy[1], to_xy[0]))
                 for s in alights}
    direct = await _walk_seconds(foot, (from_xy[1], from_xy[0]), (to_xy[1], to_xy[0]))

    best: tuple[int, object, object, object] | None = None  # total, b, a, ride
    for b in boards:
        for a in alights:
            ride = metro.ride(b.id, a.id)
            total = walks_in[(b.lon, b.lat)] + ride.seconds + walks_out[(a.lon, a.lat)]
            if best is None or total < best[0]:
                best = (total, b, a, ride)
    assert best is not None
    total, b, a, ride = best
    if ride.stops == 0 or direct <= total:
        route = RouteResult(travel_seconds=direct, legs=(RouteLeg("foot", direct),),
                            is_approximate=False, calculated_at=now_utc, provider="foot")
        return route, ""
    tr = MetroGraph.transfers_of(ride)
    tr_txt = ("; пересадки: " + ", ".join(f"{s} {f}→{t}" for s, f, t in tr)) if tr else "; без пересадок"
    summary = (f"метро {b.name} → {a.name}: {ride.stops} перег., "
               f"{ride.transfers} пересад.{tr_txt}")
    route = RouteResult(
        travel_seconds=total,
        legs=(RouteLeg("foot", walks_in[(b.lon, b.lat)], f"до ст. {b.name}"),
              RouteLeg("метро", ride.seconds, summary),
              RouteLeg("foot", walks_out[(a.lon, a.lat)], f"от ст. {a.name}")),
        is_approximate=True, calculated_at=now_utc, provider="metro-topology")
    return route, summary


async def build_day_view(
    *,
    settings: Settings,
    schedule_client: ScheduleClient,
    buildings: BuildingStore,
    geocoder: NominatimGeocoder,
    router: OsrmProvider,
    group: str,
    day: date,
    now: datetime,
    home_address: str,
    transport: str,
    buffer_min: int,
    for_today: bool,
    metro: MetroGraph | None = None,
    foot=None,  # FosFootProvider (preferred) or router with .route(); defaults to router
    home_coords: tuple[float, float] | None = None,  # (lat, lon) from location pin
) -> DayView:
    tz = ZoneInfo(settings.institution_tz)
    day_iso = day.strftime(settings.schedule_date_format)
    # Schedule JSON and home geocode are independent -> run in parallel.
    # If the schedule fails we cancel the stray geocode (no wasted work,
    # no extra latency on the schedule_failed path).
    sched_task = asyncio.ensure_future(schedule_client.get_day_raw(group, day_iso))
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
    if foot is None:
        foot = router
    try:
        from_xy = home_coords or (await geo_task if geo_task is not None else None)
        # Verified building coords (buildings.yaml) skip geocoding entirely.
        to_xy = (b.lat, b.lon) if b.lat is not None and b.lon is not None else await geocoder.geocode(dest_addr)
        if not from_xy or not to_xy:
            return DayView(schedule=schedule, skipped=skipped, target=target, plan=None, route_failed=True)
        metro_summary = ""
        if transport == "transit" and metro is not None:
            # Walk -> metro -> walk (static topology estimate, no schedules).
            route, metro_summary = await _door_to_door_transit(foot, metro, from_xy, to_xy)
        else:
            # OSRM wants (lon, lat)
            route = await router.route(
                (from_xy[1], from_xy[0]), (to_xy[1], to_xy[0]), transport, arrive_by=target.starts_at
            )
    except Exception:
        return DayView(schedule=schedule, skipped=skipped, target=target, plan=None, route_failed=True)
    plan = compute_exit(target, route, buffer_min, now.astimezone(tz))
    return DayView(schedule=schedule, skipped=skipped, target=target, plan=plan,
                   building_heuristic=heuristic, metro_summary=metro_summary)
