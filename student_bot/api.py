"""iOS HTTP API для Scriptable (локальные уведомления без Telegram).

Переиспользует существующую логику как есть, НЕ дублирует:
- schedule_client / normalize / buildings — расписание и корпуса;
- service.build_day_view — фокус-пара + план выхода;
- cards.build_morning/build_evening + format_push_* — схема ответа
  {title, body, data} (тот самый «задел под iOS-push»);
- exit_time.compute_exit / first_relevant_lesson — время выхода;
- store — настройки, заметки, api-токены.

Свежесть: каждый вызов перечитывает расписание (fresh=True мимо дневного
кэша) и считает дорогу свежим запросом 2GIS (use_cache=False). Отдельного
блокирующего лимита нет: last_calc раз в 30 мин — только шедулер пушей,
API-слой обслуживает каждый вызов (Scriptable дёргает 2-3 раза в день).

При идущей паре ответ сразу предлагает следующую: сколько ехать до неё
(suggest_next) — в today и exit-time.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from aiohttp import web

from .cards import (PushMsg, build_evening, build_morning,
                    format_push_evening, format_push_morning, with_metro)
from .exit_time import anchor_to_open, format_duration, parse_target_time
from .metro_hours import OPEN_H, OPEN_M, metro_state, opens_at_text
from .routing import NoMetroError
from .service import DayView, build_day_view
from .store import Store, UserSettings, norm_transport

log = logging.getLogger("bot.api")


@dataclass
class ApiCtx:
    settings: Any
    store: Store
    schedule_client: Any
    buildings: Any
    geocoder: Any
    routing: Any


def _now(ctx: ApiCtx, now: datetime | None = None) -> datetime:
    return now if now is not None else datetime.now(ZoneInfo(ctx.settings.institution_tz))


def metro_open_info(now: datetime) -> dict:
    """Состояние метро для Scriptable: open|gray|closed + во сколько откроется."""
    return {"state": metro_state(now), "opens_at": opens_at_text()}


def _home_coords(u: UserSettings) -> tuple[float, float] | None:
    if u.home_lat is not None and u.home_lon is not None:
        return (u.home_lat, u.home_lon)
    return None


def _push_json(p: PushMsg) -> dict:
    return {"title": p.title, "body": p.body, "data": p.data}


def _lesson_json(lesson: Any, ctx: ApiCtx) -> dict:
    b, _ = ctx.buildings.resolve_cabinet(lesson.room) if lesson.room else (None, False)
    if b is None and lesson.building_code:
        b = ctx.buildings.lookup(lesson.building_code)
    return {
        "time": lesson.starts_at.strftime("%H:%M"),
        "ends_at": lesson.ends_at.strftime("%H:%M") if lesson.ends_at else None,
        "subject": lesson.subject,
        "room": lesson.room or "",
        "teacher": (lesson.raw.get("teacher", "") or "") if isinstance(lesson.raw, dict) else "",
        "building": b.code if b else None,
        "building_address": b.address if b else None,
    }


def _building_of(ctx: ApiCtx, lesson: Any) -> tuple[float | None, float | None, str | None]:
    b, _ = ctx.buildings.resolve_cabinet(lesson.room) if lesson.room else (None, False)
    if b is None and lesson.building_code:
        b = ctx.buildings.lookup(lesson.building_code)
    if b is None or b.lat is None or b.lon is None:
        return None, None, None
    return b.lat, b.lon, b.code


async def _route_travel(ctx: ApiCtx, u: UserSettings, fr: tuple[float, float],
                        to: tuple[float, float]) -> tuple[int | None, str, bool]:
    """Свежая дорога (use_cache=False). Возвращает (секунды, summary, metro_fallback)."""
    from .routing import NoMetroError
    from .store import norm_transport

    mode = norm_transport(u.transport)
    try:
        if mode == "metro":
            try:
                opts = await ctx.routing.metro(fr, to, use_cache=False)
            except NoMetroError:
                opts = await ctx.routing.walking(fr, to, use_cache=False)
                return (opts[0].duration_s if opts else None,
                        opts[0].summary if opts else "", True)
        else:
            opts = await ctx.routing.walking(fr, to, use_cache=False)
        if not opts:
            return None, "", False
        return opts[0].duration_s, opts[0].summary, False
    except Exception as e:
        log.warning("api route failed: %s", e)
        return None, "", False


def _next_after(schedule: Any, focus: Any) -> Any | None:
    lessons = list(schedule.active_lessons)
    try:
        i = lessons.index(focus)
    except ValueError:
        return None
    return lessons[i + 1] if i + 1 < len(lessons) else None


async def suggest_next(ctx: ApiCtx, u: UserSettings, view: DayView,
                       now: datetime) -> dict | None:
    """Идущая пара + есть следующая: сколько ехать до следующей.
    None — предлагать нечего (не идёт / следующей нет)."""
    focus = view.target
    if focus is None:
        return None
    end = focus.ends_at or (focus.starts_at + timedelta(minutes=90))
    if not (focus.starts_at <= now < end):
        return None  # не идёт — следующий шаг не навязываем
    nxt = _next_after(view.schedule, focus)
    if nxt is None:
        return None
    fr = _home_coords(u)
    if fr is None:
        try:
            fr = await ctx.geocoder.geocode(u.home_address) if u.home_address.strip() else None
        except Exception:
            fr = None
    if fr is None:
        return {"available": False, "reason": "no_home"}
    lat, lon, _ = _building_of(ctx, nxt)
    if lat is None:
        return {"available": False, "reason": "unknown_building",
                "time": nxt.starts_at.strftime("%H:%M"), "subject": nxt.subject}
    travel_s, summary, fallback = await _route_travel(ctx, u, fr, (lat, lon))
    if travel_s is None:
        return {"available": False, "reason": "route_failed",
                "time": nxt.starts_at.strftime("%H:%M"), "subject": nxt.subject}
    exit_at = nxt.starts_at - timedelta(seconds=travel_s, minutes=u.buffer_min)
    arrival_now = now + timedelta(seconds=travel_s)
    return {
        "available": True,
        "time": nxt.starts_at.strftime("%H:%M"),
        "subject": nxt.subject,
        "room": nxt.room or "",
        "travel_s": travel_s,
        "travel_txt": "~" + format_duration(travel_s),
        "metro_summary": summary,
        "metro_fallback": fallback,
        "exit_at": exit_at.strftime("%H:%M"),
        "arrival_if_leave_now": arrival_now.strftime("%H:%M"),
        "can_catch_start": arrival_now <= nxt.starts_at,
    }


def _verdict(focus: Any, plan: Any, travel_s: int | None, now: datetime) -> str:
    if focus is None:
        return "done"
    if travel_s is None:
        return "unknown"
    end = focus.ends_at or (focus.starts_at + timedelta(minutes=90))
    arrival_now = now + timedelta(seconds=travel_s)
    if now < focus.starts_at:
        if plan is not None and plan.exit_at > now:
            return "on_track"
        if arrival_now <= focus.starts_at:
            return "leave_now"
        if arrival_now < end:
            return "catch_tail"
        return "missed"
    if arrival_now < end:
        return "ongoing_catchable"
    return "ongoing_missed"


def _focus_json(u: UserSettings, view: DayView, now: datetime) -> dict | None:
    focus = view.target
    if focus is None:
        return None
    end = focus.ends_at or (focus.starts_at + timedelta(minutes=90))
    ongoing = bool(focus.starts_at <= now < end)
    travel_s = view.plan.travel_seconds if view.plan else None
    arrival = None
    if view.plan is not None:
        arrival = (view.plan.exit_at + timedelta(seconds=view.plan.travel_seconds)).strftime("%H:%M")
    arrival_now, left = None, None
    if travel_s is not None:
        arrival_now = (now + timedelta(seconds=travel_s)).strftime("%H:%M")
        if ongoing:
            left = max(0, int((end - (now + timedelta(seconds=travel_s))).total_seconds() // 60))
    return {
        "time": focus.starts_at.strftime("%H:%M"),
        "subject": focus.subject,
        "room": focus.room or "",
        "teacher": (focus.raw.get("teacher", "") or "") if isinstance(focus.raw, dict) else "",
        "ongoing": ongoing,
        "exit": view.plan.exit_at.strftime("%H:%M") if view.plan else None,
        "travel_s": travel_s,
        "travel_txt": ("~" + format_duration(travel_s)) if travel_s is not None else None,
        "arrival": arrival,
        "arrival_if_leave_now": arrival_now,
        "minutes_left_if_leave_now": left,
        "late": view.plan.already_passed if view.plan else False,
        "route_ok": view.plan is not None,
        "metro": view.metro_summary or "",
        "metro_fallback": view.metro_fallback,
        "verdict": _verdict(focus, view.plan, travel_s, now),
    }


async def _fresh_view(ctx: ApiCtx, u: UserSettings, day: Any, now: datetime,
                      for_today: bool) -> DayView:
    """Свежее расписание (fresh=True) + свежая дорога (use_cache=False)."""
    return await build_day_view(
        settings=ctx.settings, schedule_client=ctx.schedule_client,
        buildings=ctx.buildings, geocoder=ctx.geocoder, group=u.group, day=day,
        now=now, home_address=u.home_address, transport=u.transport,
        buffer_min=u.buffer_min, for_today=for_today, routing=ctx.routing,
        home_coords=_home_coords(u), use_cache=False, fresh=True)


def unavailable(reason: str, detail: str, push: PushMsg) -> dict:
    return {"status": "unavailable", "reason": reason, "detail": detail,
            "push": _push_json(push)}


async def target_block(ctx: ApiCtx, u: UserSettings, lesson: Any, day: Any,
                       now: datetime, target_dt: datetime) -> dict:
    """'Приехать к HH:MM': обратный расчёт + якорь 05:30 (чистая логика та же,
    что в боте: anchor_to_open). lesson — опорная пара для проверки."""
    lang = u.lang
    mode = norm_transport(u.transport)
    fr = _home_coords(u)
    if fr is None:
        try:
            fr = await ctx.geocoder.geocode(u.home_address) if u.home_address.strip() else None
        except Exception:
            fr = None
    base = {"requested": target_dt.strftime("%H:%M"), "mode": mode,
            "lesson_time": lesson.starts_at.strftime("%H:%M")}
    if fr is None:
        return {**base, "outcome": "no_data", "exit": None, "arrival": None}
    lat, lon, _ = _building_of(ctx, lesson)
    if lat is None:
        return {**base, "outcome": "no_data", "exit": None, "arrival": None}
    try:
        if mode == "metro":
            try:
                opts = await ctx.routing.metro(fr, (lat, lon), use_cache=False)
            except NoMetroError:
                opts = await ctx.routing.walking(fr, (lat, lon), use_cache=False)
        else:
            opts = await ctx.routing.walking(fr, (lat, lon), use_cache=False)
    except Exception as e:
        log.warning("api target route failed: %s", e)
        opts = []
    if not opts:
        return {**base, "outcome": "no_data", "exit": None, "arrival": None}
    travel_s = opts[0].duration_s
    exit_needed = target_dt - timedelta(seconds=travel_s, minutes=u.buffer_min)
    open_dt = datetime(target_dt.year, target_dt.month, target_dt.day,
                       OPEN_H, OPEN_M, tzinfo=target_dt.tzinfo)
    kind, exit_at, arrival_at = anchor_to_open(exit_needed, lesson.starts_at, travel_s, open_dt)
    out = {**base, "outcome": kind, "exit": exit_at.strftime("%H:%M"),
           "arrival": arrival_at.strftime("%H:%M"), "travel_s": travel_s,
           "travel_txt": "~" + format_duration(travel_s)}
    if kind == "miss" and mode == "metro":
        try:
            w = await ctx.routing.walking(fr, (lat, lon), use_cache=False)
        except Exception:
            w = []
        if w:
            cand = target_dt - timedelta(seconds=w[0].duration_s, minutes=u.buffer_min)
            if cand >= now:
                out["walk_exit"] = cand.strftime("%H:%M")
                out["walk_arrival"] = (cand + timedelta(seconds=w[0].duration_s)).strftime("%H:%M")
    return out


def parse_target_or_400(target_raw: str | None, day: Any, tz) -> tuple[datetime | None, dict | None]:
    """Валидация target_arrival для хендлеров: (dt, None) или (None, 400-body)."""
    if target_raw is None:
        return None, None
    dt = parse_target_time(target_raw, day, tz)
    if dt is None:
        return None, {"status": "error", "error": "bad_target_arrival",
                      "hint": "HH:MM, e.g. 08:00"}
    return dt, None


async def today_payload(ctx: ApiCtx, u: UserSettings, now: datetime | None = None,
                      target: str | None = None) -> dict:
    now = _now(ctx, now)
    lang = u.lang
    if not u.group:
        return unavailable("no_group", "Группа не настроена — укажите её в боте.",
                           PushMsg("⏳ Группа не настроена" if lang != "en" else "⏳ Group not set",
                                   "Укажите группу в боте." if lang != "en" else "Set your group in the bot.",
                                   {"kind": "today", "ok": False}))
    day = now.date()
    try:
        view = await _fresh_view(ctx, u, day, now, for_today=True)
    except Exception as e:
        log.warning("api today schedule failed: %s", e)
        return unavailable("schedule_failed", "Расписание недоступно.",
                           PushMsg("⏳ Расписание недоступно" if lang != "en" else "⏳ Timetable unavailable",
                                   "Попробуйте позже." if lang != "en" else "Try later.",
                                   {"kind": "today", "ok": False}))
    if view.schedule_failed:
        return unavailable("schedule_failed", "API расписания недоступно.",
                           PushMsg("⏳ Расписание недоступно" if lang != "en" else "⏳ Timetable unavailable",
                                   "Попробуйте позже." if lang != "en" else "Try later.",
                                   {"kind": "today", "ok": False}))
    note = ctx.store.get_note(u.user_id, day.isoformat())
    lessons = [_lesson_json(l, ctx) for l in view.schedule.active_lessons]
    d = build_morning(view.target, view.plan, note,
                      route_failed=view.route_failed, unknown_building=view.unknown_building)
    if view.metro_summary and d.route_ok:
        d = with_metro(d, view.metro_summary)
    focus = _focus_json(u, view, now)
    nxt = await suggest_next(ctx, u, view, now)
    t_block = None
    if target is not None:
        tz = ZoneInfo(ctx.settings.institution_tz)
        tdt = parse_target_time(target, day, tz)
        if tdt is None or view.target is None:
            t_block = {"requested": target, "outcome": "no_data",
                       "exit": None, "arrival": None}
        else:
            t_block = await target_block(ctx, u, view.target, day, now, tdt)
    return {"status": "ok", "kind": "today", "date": day.isoformat(), "group": u.group,
            "lessons": lessons, "focus": focus, "suggest_next": nxt, "note": note,
            "metro_open": metro_open_info(now), "target": t_block,
            "push": _push_json(format_push_morning(d, "a", lang))}


async def tomorrow_payload(ctx: ApiCtx, u: UserSettings, now: datetime | None = None,
                         target: str | None = None) -> dict:
    now = _now(ctx, now)
    lang = u.lang
    if not u.group:
        return unavailable("no_group", "Группа не настроена — укажите её в боте.",
                           PushMsg("⏳ Группа не настроена" if lang != "en" else "⏳ Group not set",
                                   "Укажите группу в боте." if lang != "en" else "Set your group in the bot.",
                                   {"kind": "tomorrow", "ok": False}))
    day = now.date() + timedelta(days=1)
    try:
        view = await _fresh_view(ctx, u, day, now, for_today=False)
    except Exception as e:
        log.warning("api tomorrow schedule failed: %s", e)
        return unavailable("schedule_failed", "API расписания недоступно.",
                           PushMsg("⏳ Расписание недоступно" if lang != "en" else "⏳ Timetable unavailable",
                                   "Попробуйте позже." if lang != "en" else "Try later.",
                                   {"kind": "tomorrow", "ok": False}))
    if view.schedule_failed:
        return unavailable("schedule_failed", "API расписания недоступно.",
                           PushMsg("⏳ Расписание недоступно" if lang != "en" else "⏳ Timetable unavailable",
                                   "Попробуйте позже." if lang != "en" else "Try later.",
                                   {"kind": "tomorrow", "ok": False}))
    note = ctx.store.get_note(u.user_id, day.isoformat())
    lessons = [_lesson_json(l, ctx) for l in view.schedule.active_lessons]
    d = build_evening(view.schedule, note)
    t_block = None
    if target is not None:
        tz = ZoneInfo(ctx.settings.institution_tz)
        tdt = parse_target_time(target, day, tz)
        if tdt is None or view.target is None:
            t_block = {"requested": target, "outcome": "no_data",
                       "exit": None, "arrival": None}
        else:
            t_block = await target_block(ctx, u, view.target, day, now, tdt)
    return {"status": "ok", "kind": "tomorrow", "date": day.isoformat(), "group": u.group,
            "lessons": lessons, "note": note, "target": t_block,
            "push": _push_json(format_push_evening(d, "a", lang))}


async def exit_time_payload(ctx: ApiCtx, u: UserSettings, now: datetime | None = None) -> dict:
    """Узкий эндпоинт: только время выхода + suggest_next. Свежие данные."""
    now = _now(ctx, now)
    lang = u.lang
    if not u.group:
        return unavailable("no_group", "Группа не настроена — укажите её в боте.",
                           PushMsg("⏳ Группа не настроена" if lang != "en" else "⏳ Group not set",
                                   "Укажите группу в боте." if lang != "en" else "Set your group in the bot.",
                                   {"kind": "exit-time", "ok": False}))
    day = now.date()
    try:
        view = await _fresh_view(ctx, u, day, now, for_today=True)
    except Exception as e:
        log.warning("api exit-time schedule failed: %s", e)
        return unavailable("schedule_failed", "API расписания недоступно.",
                           PushMsg("⏳ Расписание недоступно" if lang != "en" else "⏳ Timetable unavailable",
                                   "Попробуйте позже." if lang != "en" else "Try later.",
                                   {"kind": "exit-time", "ok": False}))
    if view.schedule_failed:
        return unavailable("schedule_failed", "API расписания недоступно.",
                           PushMsg("⏳ Расписание недоступно" if lang != "en" else "⏳ Timetable unavailable",
                                   "Попробуйте позже." if lang != "en" else "Try later.",
                                   {"kind": "exit-time", "ok": False}))
    note = ctx.store.get_note(u.user_id, day.isoformat())
    d = build_morning(view.target, view.plan, note,
                      route_failed=view.route_failed, unknown_building=view.unknown_building)
    if view.metro_summary and d.route_ok:
        d = with_metro(d, view.metro_summary)
    focus = _focus_json(u, view, now)
    nxt = await suggest_next(ctx, u, view, now)
    base = {"status": "ok", "kind": "exit-time", "at": now.strftime("%H:%M"),
            "date": day.isoformat(), "focus": focus, "suggest_next": nxt,
            "metro_open": metro_open_info(now),
            "push": _push_json(format_push_morning(d, "a", lang))}
    if focus and focus["exit"]:
        base.update({"exit": focus["exit"], "travel_s": focus["travel_s"],
                     "arrival": focus["arrival"], "verdict": focus["verdict"]})
    return base


def _auth_user(ctx: ApiCtx, request: web.Request) -> UserSettings | None:
    uid = ctx.store.user_id_by_token(request.query.get("token", ""))
    return ctx.store.get_user(uid) if uid is not None else None


async def _guarded(ctx: ApiCtx, request: web.Request, kind: str) -> web.Response:
    u = _auth_user(ctx, request)
    if u is None:
        return web.json_response({"status": "error", "error": "invalid_token"}, status=401)
    target_raw = request.query.get("target_arrival")
    if target_raw is not None and kind in ("today", "tomorrow"):
        tz = ZoneInfo(ctx.settings.institution_tz)
        day = datetime.now(tz).date() + (timedelta(days=1) if kind == "tomorrow" else timedelta(0))
        _, err = parse_target_or_400(target_raw, day, tz)
        if err is not None:
            return web.json_response(err, status=400)
    try:
        if kind == "today":
            return web.json_response(await today_payload(ctx, u, target=target_raw))
        if kind == "tomorrow":
            return web.json_response(await tomorrow_payload(ctx, u, target=target_raw))
        return web.json_response(await exit_time_payload(ctx, u))
    except Exception as e:
        log.exception("api %s failed", kind)
        return web.json_response({"status": "unavailable", "reason": "internal",
                                  "detail": "Попробуйте позже."}, status=200)


async def today_handler(request: web.Request) -> web.Response:
    from .health import API_CTX_KEY

    return await _guarded(request.app[API_CTX_KEY], request, "today")


async def tomorrow_handler(request: web.Request) -> web.Response:
    from .health import API_CTX_KEY

    return await _guarded(request.app[API_CTX_KEY], request, "tomorrow")


async def exit_time_handler(request: web.Request) -> web.Response:
    from .health import API_CTX_KEY

    return await _guarded(request.app[API_CTX_KEY], request, "exit-time")
