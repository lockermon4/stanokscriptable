from __future__ import annotations

import asyncio
import difflib
import os
import re
import time
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (CallbackQuery, KeyboardButton,
                           Message, ReplyKeyboardMarkup)

from .address_check import verify_address_text
from .buildings import BuildingStore, load_buildings_yaml
from .cards import (build_evening, build_morning, evening_failed, format_day_list,
                    format_telegram_day, format_telegram_evening, format_telegram_morning,
                    with_metro, build_focus)
from .config import Settings
from .exit_time import (anchor_to_open, compute_exit, first_relevant_lesson, format_duration,
                         parse_target_time)
from .geocode import NominatimGeocoder
from .metro_hours import OPEN_H, OPEN_M, metro_state
from .normalize import normalize_day, normalize_groups
from .notifications import morning_notify_time, parse_hhmm
from .routing import NoMetroError, RoutingError, TwoGisRouting
from .schedule_client import ScheduleClient
from .health import start_health_server, stop_health_server
from .sendlog import SendLogMiddleware, install_send_logging, setup_logging
from .service import LessonTargetError, build_day_view, compute_night_exit, lesson_target
from .store import Store, UserSettings, fmt_coords, norm_transport, parse_coords
from .texts import (MENU_LEAVE, MENU_NOTES, MENU_SETTINGS, MENU_TODAY, MENU_TOMORROW,
                     ask_address, ask_buffer, ask_custom_date, ask_evening, ask_lang,
                     ask_morning_lead, ask_new_address, ask_new_group, ask_note_date,
                     ask_note_text, ask_notify, ask_route_mode, ask_transport, buffer_buttons,
                     cancel_buttons,                      evening_buttons, lang_buttons, leave_error_text,
                     ios_key_buttons, ios_key_text, leave_now_buttons, leave_now_line,
                     main_menu_text, menu_kb, menu_match, mode_buttons, morning_lead_buttons,
                     metro_closed, metro_gray,
                     need_group_first, need_home, norm_lang, no_metro_fallback,
                     note_card, note_confirm_delete, note_date_buttons, note_deleted, note_item_buttons,
                     note_saved, notes_menu_buttons, notes_menu_text, notify_menu_buttons,
                     addr_saved_new, fav_confirm_delete, fav_deleted, fav_item_buttons,
                     fav_list_buttons, fav_list_text, night_exit_text, recalc_failed, route_details,
                     route_details_buttons, route_failed, route_saved, route_session_expired, settings_buttons, settings_view,
                     start_back, start_need_group, start_need_home, start_new, start_route,
                     transport_buttons, transport_name, variants_buttons, variants_text,
                     target_buttons, target_exit_text, ask_target_time, addr_confirm_buttons,
                     addr_pick_buttons)
from .address_check import format_confirm, match_candidate, parse_address


def kb_for(lang: str) -> ReplyKeyboardMarkup:
    rows = menu_kb(lang)
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in row] for row in rows],
        resize_keyboard=True,
    )


MENU = menu_match()


def start_route(has_group: bool, has_home: bool) -> str:
    if has_group and has_home:
        return "back"
    if has_group:
        return "need_home"
    if has_home:
        return "need_group"
    return "new"


def settings_card(u: UserSettings) -> str:
    home = u.home_address or ""
    return settings_view(u.group, home, u.transport, u.buffer_min,
                         u.evening_time, u.morning_min_before_exit, u.lang)


def parse_note_date(text: str) -> str | None:
    """'2026-09-26' | '26.09.2026' | '26.09' -> ISO date or None (pure)."""
    t = (text or "").strip()
    try:
        return date.fromisoformat(t).isoformat()
    except Exception:
        pass
    mt = re.match(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?$", t)
    if mt:
        try:
            return date(int(mt.group(3) or date.today().year),
                        int(mt.group(2)), int(mt.group(1))).isoformat()
        except ValueError:
            return None
    return None


def note_label(iso: str) -> str:
    """'2026-09-26' -> '26.09' (pure)."""
    y, mo, d = iso.split("-")
    return f"{d}.{mo}"


def home_coords_of(u: UserSettings) -> tuple[float, float] | None:
    if u.home_lat is not None and u.home_lon is not None:
        return (u.home_lat, u.home_lon)
    return None


async def show_main_menu(target: Message | CallbackQuery, lang: str) -> None:
    """После «Назад»/выхода из подраздела — вернуть главное меню, а не голый экран.
    Наше меню — reply-клавиатура (kb_for): она постоянна, но сообщение раздела
    надо закрыть осмысленно. Callback -> правим текущее сообщение (снимаем
    инлайн-кнопки); править нечего/не вышло -> шлём новое с клавиатурой."""
    text = main_menu_text(lang)
    msg = target.message if isinstance(target, CallbackQuery) else None
    if msg is not None:
        try:
            await msg.edit_text(text, reply_markup=None)
            return
        except Exception:
            pass
        await msg.answer(text, reply_markup=kb_for(lang))
        return
    if isinstance(target, CallbackQuery):
        return  # показать негде; спиннер снимает вызывающий код
    await target.answer(text, reply_markup=kb_for(lang))


def day_exit_line(view, lang: str = "ru") -> str:
    en = lang == "en"
    if view.plan is None:
        if view.target is None:
            return ""
        if getattr(view, "unknown_building", False):
            return "⚠️ Building address unknown — exit time not calculated." if en else \
                "⚠️ Адрес корпуса неизвестен — время выхода не посчитано."
        return "⚠️ Couldn't calculate the route — leave with spare time." if en else \
            "⚠️ Дорогу посчитать не получилось — выходите с запасом."
    p = view.plan
    s = f"🏃 Leave at {p.exit_at.strftime('%H:%M')} (~{format_duration(p.travel_seconds)} travel)" if en else \
        f"🏃 Выйти в {p.exit_at.strftime('%H:%M')} (~{format_duration(p.travel_seconds)} в пути)"
    if p.already_passed:
        s += ". ⚠️ Already past — leave now!" if en else ". ⚠️ Время уже прошло — выходите сейчас!"
    if getattr(view, "metro_fallback", False):
        # Метро отвалилось/соврало — время посчитано пешком. Молчать об этом
        # нельзя: иначе "выйти в 04:42" выглядит как баг бота, а не данных.
        s += "\n" + no_metro_fallback(lang)
    return s


def exit_line_for(lesson, duration_s: int, buffer_min: int, lang: str = "ru") -> str:
    """Выход/прибытие для выбранного варианта маршрута (чистая функция)."""
    exit_at = lesson.starts_at - timedelta(seconds=duration_s, minutes=buffer_min)
    arr = exit_at + timedelta(seconds=duration_s)
    dur = format_duration(duration_s)
    if lang == "en":
        return (f"🏃 Leave at {exit_at:%H:%M} (~{dur} travel), arrival ~{arr:%H:%M}.")
    return (f"🏃 Выйти в {exit_at:%H:%M} (~{dur} в пути), прибытие ~{arr:%H:%M}.")


def morning_card(view, note: str):
    from .cards import MorningData

    if view.schedule_failed:
        return MorningData(ok=False)
    d = build_morning(getattr(view, "target", None), view.plan, note,
                      route_failed=view.route_failed,
                      unknown_building=view.unknown_building)
    if getattr(view, "metro_summary", "") and d.route_ok:
        d = with_metro(d, view.metro_summary)
    return d


async def scheduler_loop(bot: Bot, settings: Settings, store: Store, deps: dict,
                       last_calc: dict[int, float]):
    """Every 60 s. Evening: schedule+note (1 API call, no routing).
    Morning: cheap schedule-only check for first lesson; full routing calc
    (2GIS, first option) at most every 30 min per user and only within 4 h
    before the first lesson. Sent-flags persist in DB (no dupes on restart).

    last_calc is passed explicitly (NOT inside deps): deps is splatted into
    build_day_view(), which rejects unknown kwargs (see TypeError crash)."""
    tz = ZoneInfo(settings.institution_tz)
    sched_client: ScheduleClient = deps["schedule_client"]
    while True:
        try:
            now = datetime.now(tz)
            for u in store.all_users():
                if not u.group or (not u.home_address and home_coords_of(u) is None):
                    continue
                # Ночью (01:00–05:30) метро закрыто: 2GIS не дёргаем, считаем
                # пешком и честно пишем об этом в утреннем сообщении.
                night_metro = u.transport == "metro" and metro_state(now) == "closed"
                kw = dict(group=u.group, home_address=u.home_address,
                          home_coords=home_coords_of(u),
                          transport=("walk" if night_metro else u.transport),
                          buffer_min=u.buffer_min, settings=settings, **deps)
                # --- evening ---
                try:
                    et = parse_hhmm(u.evening_time)
                except Exception:
                    et = None
                if et is not None and now.hour == et.hour and now.minute == et.minute:
                    tomorrow = now.date() + timedelta(days=1)
                    key_day = tomorrow.isoformat()
                    if not store.was_sent(u.user_id, key_day, "eve"):
                        view = await build_day_view(day=tomorrow, now=now, for_today=False, **kw)
                        note = store.get_note(u.user_id, key_day)
                        card = evening_failed(key_day) if view.schedule_failed \
                            else build_evening(view.schedule, note)
                        await bot.send_message(u.user_id, format_telegram_evening(card, u.lang))
                        store.mark_sent(u.user_id, key_day, "eve")
                # --- morning: cheap schedule check first ---
                today = now.date()
                if store.was_sent(u.user_id, today.isoformat(), "morn"):
                    continue
                try:
                    raw = await sched_client.get_day_raw(
                        u.group, today.strftime(settings.schedule_date_format))
                except Exception:
                    continue
                sched, _ = normalize_day(raw, group=u.group, day=today,
                                         tz_name=settings.institution_tz)
                first = first_relevant_lesson(sched, now)
                if first is None:
                    continue
                if not (timedelta(0) <= (first.starts_at - now) <= timedelta(hours=4)):
                    continue
                if time.monotonic() - last_calc.get(u.user_id, 0) < 30 * 60:
                    continue
                last_calc[u.user_id] = time.monotonic()
                view = await build_day_view(day=today, now=now, for_today=True, **kw)
                if view.plan is None:
                    continue
                notify_at = morning_notify_time(view.plan, u.morning_min_before_exit)
                # fresh recalc right before sending: never after exit
                if notify_at <= now < view.plan.exit_at and (now - notify_at) < timedelta(minutes=2):
                    note = store.get_note(u.user_id, today.isoformat())
                    text = format_telegram_morning(morning_card(view, note), u.lang)
                    if night_metro:
                        text += "\n" + metro_closed(u.lang)
                    await bot.send_message(u.user_id, text)
                    store.mark_sent(u.user_id, today.isoformat(), "morn")
        except Exception:
            pass  # never crash loop; errors surface in day views
        await asyncio.sleep(60)


async def main() -> None:
    settings = Settings.from_env()
    if not settings.bot_token:
        raise SystemExit("Set BOT_TOKEN env var (see .env.example).")
    try:
        buildings: BuildingStore = load_buildings_yaml(settings.buildings_file)
    except FileNotFoundError:
        buildings = BuildingStore([])
    store = Store(settings.database_url or settings.database_path)
    sched_client = ScheduleClient(settings)
    geocoder = NominatimGeocoder(settings)
    routing = TwoGisRouting()  # ключ из $GIS_API_KEY; только пешком и метро
    deps = {"schedule_client": sched_client, "buildings": buildings, "geocoder": geocoder,
            "routing": routing}
    last_calc: dict[int, float] = {}  # scheduler throttle, kept OUT of deps (see above)
    addr_picks: dict[int, list[tuple[str, float, float]]] = {}  # uid -> [(label, lat, lon)]
    note_tmp: dict[int, dict] = {}  # uid -> {"op": add/view/del, "date": iso}
    route_sessions: dict[int, dict] = {}  # uid -> {from,to,label,lesson,buffer,mode,options}

    def routing_changed(uid: int) -> None:
        """Address/group/transport/buffer changed: drop everything computed
        for the old home. Today's morning flag is cleared so the next loop
        iteration recomputes from the NEW home (the send-window check still
        guards against late/duplicate sends)."""
        last_calc.pop(uid, None)
        route_sessions.pop(uid, None)
        try:
            routing.drop()
        except Exception:
            pass
        try:
            today = datetime.now(ZoneInfo(settings.institution_tz)).date().isoformat()
            store.clear_sent(uid, today, "morn")
        except Exception:
            pass

    async def verify_address(text: str, lang: str = "ru") -> tuple[str, list[tuple[str, float, float]], str]:
        return await verify_address_text(geocoder, text, lang)

    async def home_xy(u) -> tuple[float, float] | None:
        """Точка отправления: пин или геокод адреса. None — посчитать нельзя."""
        hc = home_coords_of(u)
        if hc is not None:
            return hc
        try:
            return await geocoder.geocode(u.home_address) if u.home_address.strip() else None
        except Exception:
            return None

    async def fetch_mode_travel(sess: dict, mode: str,
                                use_cache: bool = True) -> tuple[int | None, str, bool]:
        """Свежая дорога по режиму: (travel_s, travel_txt, used_walk_fallback).
        Метро недоступно -> пешком с флагом (честно, не молча)."""
        try:
            if mode == "metro":
                try:
                    opts = await routing.metro(sess["from"], sess["to"], use_cache=use_cache)
                except NoMetroError:
                    opts = await routing.walking(sess["from"], sess["to"], use_cache=use_cache)
                    return (opts[0].duration_s, opts[0].summary, True) if opts else (None, "", True)
            else:
                opts = await routing.walking(sess["from"], sess["to"], use_cache=use_cache)
            if not opts:
                return None, "", False
            return opts[0].duration_s, opts[0].summary, False
        except RoutingError:
            return None, "", False

    async def recalc_block(u) -> str:
        """Fresh exit/travel/arrival for today from CURRENT stored settings.
        Called after group/address/transport/buffer changes. Never returns
        stale numbers: empty string only when recalc is impossible (no group
        or no home yet); otherwise a fresh calc or an honest failure line."""
        lang = u.lang
        if not u.group or (not u.home_address and home_coords_of(u) is None):
            return ""
        tz = ZoneInfo(settings.institution_tz)
        now = datetime.now(tz)
        try:
            view = await build_day_view(
                group=u.group, day=now.date(), now=now, home_address=u.home_address,
                home_coords=home_coords_of(u), transport=u.transport,
                buffer_min=u.buffer_min, for_today=True, **deps, settings=settings)
        except Exception:
            return recalc_failed(lang)
        if view.plan is None or view.target is None:
            return recalc_failed(lang)
        p = view.plan
        arr = (p.exit_at + timedelta(seconds=p.travel_seconds)).strftime("%H:%M")
        dur = format_duration(p.travel_seconds)
        if lang == "en":
            return (f"🔄 Recalculated from the current home: leave at {p.exit_at:%H:%M}, "
                    f"~{dur} travel, arrival ~{arr}.")
        return (f"🔄 Пересчитано от текущего дома: выйти в {p.exit_at:%H:%M}, "
                f"~{dur} в пути, прибытие ~{arr}.")

    async def ask_confirm(m: Message, label: str, lat: float, lon: float, lang: str = "ru") -> None:
        addr_picks[m.from_user.id] = [(label, lat, lon)]
        pending[m.from_user.id] = "address_confirm"
        kb = kb_for(lang)
        if lang == "en":
            await m.answer(f"Found: {label}.\nIs this your home?",
                           reply_markup=addr_confirm_buttons(lang))
        else:
            await m.answer(f"Нашёл: {label}.\nЭто ваш дом?",
                           reply_markup=addr_confirm_buttons(lang))

    async def save_home_text(uid: int, u) -> str:
        """Сохранить addr_picks[uid][0] как дом; вернуть текст ответа.
        Общий для текстового 'да' и кнопки addr:yes."""
        lang = u.lang
        picks = addr_picks.pop(uid, [])
        if not picks:
            pending.pop(uid, None)
            return ("Options expired, send the address again." if lang == "en" else
                    "Варианты устарели, введите адрес ещё раз.")
        label, lat, lon = picks[0]
        u.home_address = label
        u.home_lat, u.home_lon = lat, lon
        store.save_user(u)
        pending.pop(uid, None)
        routing_changed(uid)  # old home numbers are stale from here on
        recalc = await recalc_block(u)
        saved = addr_saved_new(lang, label)
        tail = recalc if recalc else (recalc_failed(lang) if u.group else "")
        return saved + (f"\n\n{tail}" if tail else "")

    groups_cache: dict[str, list[str]] = {"at": 0.0, "names": []}

    async def group_names() -> list[str]:
        if time.monotonic() - groups_cache["at"] < 3600 and groups_cache["names"]:
            return groups_cache["names"]
        try:
            names = [g.name for g in normalize_groups(await sched_client.get_groups_raw())]
            groups_cache["at"] = time.monotonic()
            groups_cache["names"] = names
            return names
        except Exception:
            return groups_cache["names"]

    bot = Bot(settings.bot_token)
    dp = Dispatcher()
    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
    try:  # revision marker: tracebacks show file paths, this line shows WHICH copy runs
        import subprocess as _sp

        _rev = _sp.check_output(["git", "rev-parse", "--short", "HEAD"],
                                text=True, stderr=_sp.DEVNULL).strip()
    except Exception:
        _rev = "unknown"
    import logging as _logging

    _logging.getLogger("bot").info("starting, code rev=%s", _rev)
    send_stats = install_send_logging(bot)  # SEND/EDIT lines + session counters
    dp.message.middleware(SendLogMiddleware())
    dp.callback_query.middleware(SendLogMiddleware())
    pending: dict[int, str] = {}  # user_id -> expected input state

    @dp.message(Command("start"))
    async def start(m: Message):
        u = store.get_user(m.from_user.id)
        has_group, has_home = bool(u.group), bool(u.home_address or home_coords_of(u))
        if not has_group and not has_home:
            # brand-new user: inherit Telegram locale once, then it sticks
            u.lang = norm_lang(m.from_user.language_code or "")
            store.save_user(u)
        lang = u.lang
        kb = kb_for(lang)
        route = start_route(has_group, has_home)
        if route == "back":
            await m.answer(start_back(lang), reply_markup=kb)
            return
        if route == "need_home":
            pending[m.from_user.id] = "address"
            await m.answer(start_need_home(lang, u.group), reply_markup=kb)
            return
        if route == "need_group":
            pending[m.from_user.id] = "group"
            await m.answer(start_need_group(lang), reply_markup=kb)
            return
        pending[m.from_user.id] = "group"
        await m.answer(start_new(lang), reply_markup=kb)

    @dp.message(lambda m: (m.text or "") in MENU)
    async def menu(m: Message):
        action = MENU[m.text or ""]
        u = store.get_user(m.from_user.id)
        lang = u.lang
        kb = kb_for(lang)
        if action in ("today", "tomorrow", "leave"):
            if not u.group:
                await m.answer(need_group_first(lang), reply_markup=kb)
                return
            if not u.home_address and home_coords_of(u) is None:
                await m.answer(need_home(lang), reply_markup=kb)
                return
            tz = ZoneInfo(settings.institution_tz)
            now = datetime.now(tz)
            day = now.date() if action != "tomorrow" else now.date() + timedelta(days=1)
            for_today = action in ("today", "leave")
            view = None
            if action != "leave":
                # leave идёт своим флоу (lesson_target + выбор типа) — лишний
                # предрасчёт 2GIS здесь не нужен.
                view = await build_day_view(group=u.group, day=day, now=now, home_address=u.home_address,
                                            home_coords=home_coords_of(u),
                                            transport=u.transport, buffer_min=u.buffer_min,
                                            for_today=for_today, **deps, settings=settings)
            note = store.get_note(m.from_user.id, day.isoformat())
            if action == "leave":
                # Шаг 1: пара + точка назначения; тип маршрута выбирает пользователь.
                tgt = await lesson_target(settings=settings,
                                          schedule_client=deps["schedule_client"],
                                          buildings=deps["buildings"],
                                          group=u.group, day=day, now=now, for_today=True)
                if isinstance(tgt, LessonTargetError):
                    await m.answer(leave_error_text(lang, tgt.reason), reply_markup=kb)
                else:
                    from_xy = await home_xy(u)
                    if from_xy is None:
                        await m.answer(route_failed(lang), reply_markup=kb)
                    else:
                        lesson_line = (f"{tgt.lesson.starts_at.strftime('%H:%M')} — "
                                       f"{tgt.lesson.subject}, {tgt.label}")
                        route_sessions[m.from_user.id] = {
                            "from": from_xy, "to": (tgt.lat, tgt.lon), "label": tgt.label,
                            "lesson_line": lesson_line, "lesson": tgt.lesson,
                            "buffer": u.buffer_min, "mode": norm_transport(u.transport),
                            "options": [], "fallback": False}
                        await m.answer(ask_route_mode(lang, lesson_line),
                                       reply_markup=mode_buttons(lang))
            else:
                if action == "today":
                    # Intraday: фокус (ближайшая пара, на которую можно попасть)
                    # + весь день простынёй, чтобы остальное расписание было видно.
                    if view.schedule_failed:
                        await m.answer(format_telegram_day(
                            (f"Today, {day.strftime('%d.%m')}" if lang == "en" else
                             f"Сегодня, {day.strftime('%d.%m')}"),
                            evening_failed(day.isoformat()), "", lang), reply_markup=kb)
                    elif view.target is None:
                        day_list = format_day_list(view.schedule, now, lang)
                        done = "🌅 No more classes today." if lang == "en" else \
                            "🌅 Все пары на сегодня закончились."
                        tail = f"\n🎒 {note}" if note else ""
                        await m.answer(f"{done}\n\n{day_list}{tail}", reply_markup=kb)
                    else:
                        lesson = view.target
                        next_lesson = None
                        try:
                            idx = list(view.schedule.active_lessons).index(lesson)
                            rest = list(view.schedule.active_lessons)[idx + 1:]
                            next_lesson = rest[0] if rest else None
                        except ValueError:
                            next_lesson = None
                        travel_s = view.plan.travel_seconds if view.plan else None
                        route_ok = view.plan is not None
                        focus_text = build_focus(lesson, next_lesson, travel_s,
                                                 u.buffer_min, now, route_ok, lang)
                        if view.metro_fallback and norm_transport(u.transport) == "metro":
                            # Время посчитано пешком вместо метро: ночью — потому что
                            # закрыто, днём — потому что 2GIS соврал. Молчать нельзя.
                            if metro_state(now) == "closed":
                                focus_text += "\n" + metro_closed(lang)
                            else:
                                focus_text += "\n" + no_metro_fallback(lang)
                        day_list = format_day_list(view.schedule, now, lang)
                        tail = f"\n🎒 {note}" if note else ""
                        # Сессия для кнопки "Выйти сейчас" (свежее прибытие).
                        markup: Any = kb
                        from_xy = await home_xy(u)
                        if from_xy is not None:
                            tgt = await lesson_target(
                                settings=settings, schedule_client=deps["schedule_client"],
                                buildings=deps["buildings"], group=u.group, day=day,
                                now=now, for_today=True)
                            if not isinstance(tgt, LessonTargetError):
                                lesson_line = (f"{tgt.lesson.starts_at.strftime('%H:%M')} — "
                                               f"{tgt.lesson.subject}, {tgt.label}")
                                route_sessions[m.from_user.id] = {
                                    "from": from_xy, "to": (tgt.lat, tgt.lon),
                                    "label": tgt.label, "lesson_line": lesson_line,
                                    "lesson": tgt.lesson, "buffer": u.buffer_min,
                                    "mode": norm_transport(u.transport),
                                    "options": [], "fallback": False}
                                markup = leave_now_buttons(lang)
                        await m.answer(f"{focus_text}\n\n{day_list}{tail}", reply_markup=markup)
                else:
                    label = ("Today" if action == "today" else "Tomorrow") if lang == "en" else \
                        ("Сегодня" if action == "today" else "Завтра")
                    if view.schedule_failed:
                        card = evening_failed(day.isoformat())
                    else:
                        card = build_evening(view.schedule, note)
                    # Сессия для "🎯 Приехать к...": первая пара завтра + точка дома.
                    t_markup: Any = kb
                    if view.target is not None:
                        from_xy = await home_xy(u)
                        if from_xy is not None:
                            tgt = await lesson_target(
                                settings=settings, schedule_client=deps["schedule_client"],
                                buildings=deps["buildings"], group=u.group, day=day,
                                now=now, for_today=False)
                            if not isinstance(tgt, LessonTargetError):
                                lesson_line = (f"{tgt.lesson.starts_at.strftime('%H:%M')} — "
                                               f"{tgt.lesson.subject}, {tgt.label}")
                                route_sessions[m.from_user.id] = {
                                    "from": from_xy, "to": (tgt.lat, tgt.lon),
                                    "label": tgt.label, "lesson_line": lesson_line,
                                    "lesson": tgt.lesson, "buffer": u.buffer_min,
                                    "mode": norm_transport(u.transport),
                                    "options": [], "fallback": False}
                                t_markup = target_buttons(lang)
                    await m.answer(format_telegram_day(f"{label}, {day.strftime('%d.%m')}", card,
                                                       day_exit_line(view, lang) if view.target else "",
                                                       lang), reply_markup=t_markup)
            return
        if action == "notes":
            await m.answer(notes_menu_text(lang), reply_markup=notes_menu_buttons(lang))
            return
        if action == "routes":
            favs = store.list_favorites(m.from_user.id)
            await m.answer(fav_list_text(lang, favs), reply_markup=fav_list_buttons(favs, lang))
            return
        # settings
        await m.answer(settings_card(u), reply_markup=settings_buttons(lang))

    @dp.message(F.location)
    async def save_location(m: Message):
        uid = m.from_user.id
        u = store.get_user(uid)
        lang = u.lang
        kb = kb_for(lang)
        lat = round(m.location.latitude, 6)
        lon = round(m.location.longitude, 6)
        label = await geocoder.reverse_label(lat, lon)
        near = (f" Nearby: {label}." if label else "") if lang == "en" else \
            (f" Рядом с: {label}." if label else "")
        # Pin is NOT claimed to be an exact house match.
        addr_picks[uid] = [(f"📍 {lat}, {lon}", lat, lon)]
        pending[uid] = "address_confirm"
        if lang == "en":
            await m.answer(f"Pinned: {lat}, {lon}.{near}\nSave as home?",
                           reply_markup=addr_confirm_buttons(lang))
        else:
            await m.answer(f"Принял точку: {lat}, {lon}.{near}\n"
                           f"Сохранить как дом?",
                           reply_markup=addr_confirm_buttons(lang))

    @dp.message(F.text)
    async def fallback(m: Message):
        uid = m.from_user.id
        u = store.get_user(uid)
        lang = u.lang
        kb = kb_for(lang)
        en = lang == "en"
        text = (m.text or "").strip()
        low = text.lower()
        state = pending.get(uid, "")

        def variants_msg(suitable):
            lines = "\n".join(f"{i + 1}. {lbl}" for i, (lbl, _, _) in enumerate(suitable))
            if en:
                return f"Found several matching houses:\n{lines}\nPick the number below."
            return (f"Нашёл несколько подходящих домов:\n{lines}\n"
                    f"Выберите номер кнопкой ниже.")

        async def run_verify(a):
            status, suitable, msg = await verify_address(a, lang)
            if status == "ok-one":
                await ask_confirm(m, *suitable[0], lang)
            elif status == "ok-many":
                addr_picks[uid] = suitable
                pending[uid] = "address_pick"
                await m.answer(variants_msg(suitable),
                               reply_markup=addr_pick_buttons(suitable, lang))
            else:
                pending[uid] = "address"
                await m.answer(msg, reply_markup=kb)

        if state == "group" or low.startswith(("группа ", "group ")):
            prefix = "группа " if low.startswith("группа ") else ("group " if low.startswith("group ") else "")
            g = text[len(prefix):].strip() if prefix else text
            names = await group_names()
            if names:
                exact = next((n for n in names if n.casefold() == g.casefold()), None)
                if exact is None:
                    sug = difflib.get_close_matches(g, names, n=5, cutoff=0.5)
                    hint = (" Similar: " + ", ".join(sug) if sug else "") if en else \
                        (" Похожие: " + ", ".join(sug) if sug else "")
                    await m.answer((f"No group “{g}” in the list.{hint}\nCheck the name and send again."
                                    if en else
                                    f"Группы «{g}» нет в списке.{hint}\nПроверьте название и пришлите ещё раз."),
                                   reply_markup=kb)
                    return
                g = exact
            u.group = g
            store.save_user(u)
            pending.pop(uid, None)
            if u.home_address or home_coords_of(u) is not None:
                # group changed with a home present: old routing is stale
                routing_changed(uid)
                recalc = await recalc_block(u)
                card = settings_card(u)
                await m.answer(card + (f"\n\n{recalc}" if recalc else ""), reply_markup=kb)
            else:
                pending[uid] = "address"
                await m.answer(ask_address(lang, g), reply_markup=kb)
            return
        if state == "address_confirm":
            if low in ("да", "ага", "точно", "верно", "подтверждаю", "yes", "y", "yeah", "ok"):
                await m.answer(await save_home_text(uid, u), reply_markup=kb)
                return
            if low in ("нет", "не", "no", "n", "не мой", "не мой дом", "not mine"):
                addr_picks.pop(uid, None)
                pending[uid] = "address"
                await m.answer("OK, not saving. Clarify the address or send a pin." if en else
                               "Хорошо, не сохраняю. Уточните адрес текстом или пришлите геоточку.",
                               reply_markup=kb)
                return
            # any other text = refined address, verify again
            pending.pop(uid, None)
            addr_picks.pop(uid, None)
            await run_verify((m.text or "").strip())
            return
        if state == "address_pick":
            picks = addr_picks.get(uid, [])
            if text.strip().isdigit():
                i = int(text.strip()) - 1
                if 0 <= i < len(picks):
                    await ask_confirm(m, *picks[i], lang)
                    return
                await m.answer(f"Number from 1 to {len(picks)}." if en else
                               f"Номер от 1 до {len(picks)}.", reply_markup=kb)
                return
            # refined text instead of a number
            addr_picks.pop(uid, None)
            pending.pop(uid, None)
            await run_verify(text)
            return
        if state == "address" or low.startswith(("адрес ", "address ")):
            a = text
            for pfx in ("адрес ", "address "):
                if low.startswith(pfx):
                    a = text[len(pfx):].strip()
                    break
            await run_verify(a)
            return
        if low.startswith(("язык ", "language ", "lang ")):
            v = text.split(None, 1)[1].strip().lower() if len(text.split(None, 1)) > 1 else ""
            if v.startswith("en"):
                u.lang = "en"
            elif v.startswith("ru") or v.startswith("ру"):
                u.lang = "ru"
            else:
                await m.answer("язык ru/en · language ru/en", reply_markup=kb)
                return
            store.save_user(u)
            await m.answer(settings_card(u), reply_markup=kb_for(u.lang))
            return
        if low.startswith(("транспорт ", "transport ")):
            v = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
            mode = norm_transport(v)
            if v.lower() not in ("walk", "metro", "пешком", "метро", "transit", "foot",
                                 "общественный"):
                await m.answer("walk | metro", reply_markup=kb)
                return
            u.transport = mode
            store.save_user(u)
            await m.answer(settings_card(u), reply_markup=kb)
            return
        if low.startswith(("запас ", "buffer ")):
            try:
                u.buffer_min = max(0, min(120, int(text.split(None, 1)[1])))
                store.save_user(u)
                await m.answer(settings_card(u), reply_markup=kb)
            except Exception:
                await m.answer("запас 15 · buffer 15", reply_markup=kb)
            return
        if low.startswith(("вечер ", "evening ")):
            try:
                parse_hhmm(text.split(None, 1)[1])
                u.evening_time = text.split(None, 1)[1].strip()
                store.save_user(u)
                await m.answer(settings_card(u), reply_markup=kb)
            except Exception:
                await m.answer("вечер 21:30 · evening 21:30", reply_markup=kb)
            return
        if low.startswith(("утро ", "morning ")):
            try:
                u.morning_min_before_exit = max(5, min(240, int(text.split(None, 1)[1])))
                store.save_user(u)
                await m.answer(settings_card(u), reply_markup=kb)
            except Exception:
                await m.answer("утро 60 · morning 60", reply_markup=kb)
            return
        if low.startswith(("группа ", "group ")):
            pending[uid] = "group"
            await m.answer("ИДБ-26-14" if en else "Например: ИДБ-26-14", reply_markup=kb)
            return
        if state in ("note_add_custom", "note_view_custom", "note_del_custom"):
            iso = parse_note_date(text)
            if iso is None:
                await m.answer(ask_custom_date(lang), reply_markup=kb)
                return
            label = note_label(iso)
            if state == "note_add_custom":
                note_tmp[uid] = {"op": "add", "date": iso}
                pending[uid] = "note_text"
                await m.answer(ask_note_text(lang, label), reply_markup=cancel_buttons(lang))
            elif state == "note_view_custom":
                pending.pop(uid, None)
                await m.answer(note_card(lang, label, store.get_note(uid, iso)),
                               reply_markup=note_item_buttons(lang, iso))
            else:  # note_del_custom
                pending.pop(uid, None)
                existing = store.get_note(uid, iso)
                if existing:
                    t, kb2 = note_confirm_delete(lang, iso, label, existing)
                    await m.answer(t, reply_markup=kb2)
                else:
                    await m.answer(note_card(lang, label, ""),
                                   reply_markup=notes_menu_buttons(lang))
            return
        if state == "note_text":
            data = note_tmp.get(uid, {})
            iso = data.get("date")
            if not iso:
                pending.pop(uid, None)
                await m.answer(notes_menu_text(lang), reply_markup=notes_menu_buttons(lang))
                return
            if not text:
                await m.answer(ask_note_text(lang, note_label(iso)),
                               reply_markup=cancel_buttons(lang))
                return
            store.set_note(uid, iso, text)
            note_tmp.pop(uid, None)
            pending.pop(uid, None)
            await m.answer(note_saved(lang, note_label(iso), text),
                           reply_markup=notes_menu_buttons(lang))
            return
        if state == "target_time":
            # "🎯 Приехать к ЧЧ:ММ": обратный расчёт, статус метро — на момент выхода.
            sess = route_sessions.get(uid)
            tz = ZoneInfo(settings.institution_tz)
            now = datetime.now(tz)
            lesson = (sess or {}).get("lesson")
            if not sess or lesson is None:
                pending.pop(uid, None)
                await m.answer(route_session_expired(lang), reply_markup=kb)
                return
            target = parse_target_time(text, lesson.starts_at.date(), tz)
            if target is None:
                await m.answer(ask_target_time(lang, sess["lesson_line"]),
                               reply_markup=cancel_buttons(lang))
                return
            pending.pop(uid, None)
            mode = sess.get("mode", "walk")
            buf = sess.get("buffer", 10)
            travel_s, _, _ = await fetch_mode_travel(sess, mode)
            if travel_s is None:
                await m.answer(route_failed(lang), reply_markup=kb)
                return
            exit_needed = target - timedelta(seconds=travel_s, minutes=buf)
            open_dt = datetime(target.year, target.month, target.day,
                               OPEN_H, OPEN_M, tzinfo=tz)
            kind, exit_at, arrival_at = anchor_to_open(exit_needed, lesson.starts_at,
                                                       travel_s, open_dt)
            walk_exit_at = walk_arrival_at = None
            if kind == "miss" and mode == "metro":
                w_s, _, _ = await fetch_mode_travel(sess, "walk")
                if w_s is not None:
                    cand = target - timedelta(seconds=w_s, minutes=buf)
                    if cand >= now:
                        walk_exit_at = cand
                        walk_arrival_at = cand + timedelta(seconds=w_s)
            await m.answer(target_exit_text(lang, sess["lesson_line"], kind, exit_at,
                                            arrival_at, f"~{format_duration(travel_s)}",
                                            walk_exit_at, walk_arrival_at),
                           reply_markup=kb)
            return
        if low.startswith(("покажи ", "show ")) or \
                (len(text) >= 10 and text[:10].replace("-", "").isdigit()):
            if low.startswith(("покажи ", "show ")):
                d = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
                note = store.get_note(uid, d)
                await m.answer(f"{d}: {note or ('(empty)' if en else '(пусто)')}", reply_markup=kb)
                return
            parts = text.split(None, 1)
            try:
                date.fromisoformat(parts[0])
                store.set_note(uid, parts[0], parts[1] if len(parts) > 1 else "")
                pending.pop(uid, None)
                await m.answer("Note saved." if en else "Заметка сохранена.", reply_markup=kb)
                return
            except Exception:
                pass
        await m.answer("Use the buttons or /start." if en else
                       "Не понял. Используйте кнопки или /start.", reply_markup=kb)

    # --- Inline buttons: settings + notes (all callbacks answered; every
    # --- path edits the message or deletes it, nothing hangs silently).
    @dp.callback_query()
    async def callbacks(cb: CallbackQuery):
        uid = cb.from_user.id
        data = cb.data or ""
        await cb.answer()  # dismiss the spinner on every path

        def fresh() -> UserSettings:
            return store.get_user(uid)

        async def safe_edit(text: str, reply_markup=None) -> None:
            try:
                await cb.message.edit_text(text, reply_markup=reply_markup)
            except Exception:
                await cb.message.answer(text, reply_markup=reply_markup)

        async def edit_settings(extra: str = "") -> None:
            u = fresh()
            card = settings_card(u)
            await safe_edit(card + (f"\n\n{extra}" if extra else ""),
                            settings_buttons(u.lang))

        async def apply_routing_change() -> None:
            """Save already done by caller: drop stale routing, refresh card + fresh recalc."""
            routing_changed(uid)
            await edit_settings(await recalc_block(fresh()))

        def day_iso(which: str) -> str:
            tz = ZoneInfo(settings.institution_tz)
            today = datetime.now(tz).date()
            return today.isoformat() if which == "today" else (today + timedelta(days=1)).isoformat()

        async def show_note(iso: str) -> None:
            u = fresh()
            await safe_edit(note_card(u.lang, note_label(iso), store.get_note(uid, iso)),
                            note_item_buttons(u.lang, iso))

        async def ask_delete(iso: str) -> None:
            u = fresh()
            existing = store.get_note(uid, iso)
            if existing:
                t, kb2 = note_confirm_delete(u.lang, iso, note_label(iso), existing)
                await safe_edit(t, kb2)
            else:
                await safe_edit(note_card(u.lang, note_label(iso), ""),
                                notes_menu_buttons(u.lang))

        # --- cancel for text-input flows ---
        if data == "op:cancel":
            pending.pop(uid, None)
            addr_picks.pop(uid, None)
            note_tmp.pop(uid, None)
            route_sessions.pop(uid, None)
            await show_main_menu(cb, fresh().lang)
            return

        # --- address confirm / pick (buttons mirror the "да/нет" and number text) ---
        if data == "addr:yes":
            await safe_edit(await save_home_text(uid, fresh()))
            return
        if data == "addr:no":
            u = fresh()
            addr_picks.pop(uid, None)
            pending[uid] = "address"
            await safe_edit("OK, not saving. Clarify the address or send a pin." if u.lang == "en" else
                            "Хорошо, не сохраняю. Уточните адрес текстом или пришлите геоточку.")
            return
        if data.startswith("addrpick:"):
            try:
                i = int(data[len("addrpick:"):])
            except ValueError:
                return
            picks = addr_picks.get(uid, [])
            u = fresh()
            if 0 <= i < len(picks):
                label, lat, lon = picks[i]
                addr_picks[uid] = [(label, lat, lon)]
                pending[uid] = "address_confirm"
                ask = (f"Found: {label}.\nIs this your home?") if u.lang == "en" else \
                    f"Нашёл: {label}.\nЭто ваш дом?"
                await safe_edit(ask, addr_confirm_buttons(u.lang))
            else:
                await safe_edit(f"Number from 1 to {len(picks)}." if u.lang == "en" else
                                f"Номер от 1 до {len(picks)}.")
            return

        # --- settings ---
        if data == "set:menu":
            await edit_settings()
            return
        if data == "set:back":
            await show_main_menu(cb, fresh().lang)
            return
        if data == "set:group":
            pending[uid] = "group"
            await safe_edit(ask_new_group(fresh().lang), cancel_buttons(fresh().lang))
            return
        if data == "set:address":
            pending[uid] = "address"
            u = fresh()
            await safe_edit(ask_new_address(u.lang), cancel_buttons(u.lang))
            return
        if data == "set:transport":
            await safe_edit(ask_transport(fresh().lang), transport_buttons(fresh().lang))
            return
        if data.startswith("tr:"):
            mode = norm_transport(data[3:])
            if mode not in ("walk", "metro"):
                return
            u = fresh()
            u.transport = mode
            store.save_user(u)
            await apply_routing_change()
            return
        if data == "set:buffer":
            await safe_edit(ask_buffer(fresh().lang), buffer_buttons(fresh().lang))
            return
        if data.startswith("buf:"):
            try:
                n = max(0, min(120, int(data[4:])))
            except ValueError:
                return
            u = fresh()
            u.buffer_min = n
            store.save_user(u)
            await apply_routing_change()
            return
        if data == "set:notify":
            await safe_edit(ask_notify(fresh().lang), notify_menu_buttons(fresh().lang))
            return
        if data == "ntfmenu:eve":
            await safe_edit(ask_evening(fresh().lang), evening_buttons(fresh().lang))
            return
        if data == "ntfmenu:morn":
            await safe_edit(ask_morning_lead(fresh().lang), morning_lead_buttons(fresh().lang))
            return
        if data.startswith("ntf:eve:"):
            try:
                parse_hhmm(data[len("ntf:eve:"):])
            except Exception:
                return
            u = fresh()
            u.evening_time = data[len("ntf:eve:"):]
            store.save_user(u)
            await edit_settings()
            return
        if data.startswith("ntf:morn:"):
            try:
                n = max(5, min(240, int(data[len("ntf:morn:"):])))
            except ValueError:
                return
            u = fresh()
            u.morning_min_before_exit = n
            store.save_user(u)
            await edit_settings()
            return
        if data == "set:lang":
            await safe_edit(ask_lang(), lang_buttons())
            return
        if data in ("lang:ru", "lang:en"):
            u = fresh()
            u.lang = "en" if data.endswith("en") else "ru"
            store.save_user(u)
            await edit_settings()  # re-rendered in the NEW language
            return
        if data in ("set:ioskey", "set:ioskey_reissue"):
            # Токен показываем только здесь, в личке; в логи он не попадает
            # (sendlog пишет длины сообщений, не текст).
            u = fresh()
            token = store.issue_api_token(uid) if data == "set:ioskey_reissue" \
                else (store.get_api_token(uid) or store.issue_api_token(uid))
            base = settings.public_base_url
            link = f"{base}/api/v1/today?token={token}" if base else None
            await safe_edit(ios_key_text(u.lang, link), ios_key_buttons(u.lang))
            return

        # --- notes ---
        if data in ("note:menu",):
            u = fresh()
            await safe_edit(notes_menu_text(u.lang), notes_menu_buttons(u.lang))
            return
        if data == "note:back":
            await show_main_menu(cb, fresh().lang)
            return
        if data == "note:add":
            u = fresh()
            await safe_edit(ask_note_date(u.lang), note_date_buttons(u.lang, "nadd"))
            return
        if data in ("nadd:today", "nadd:tomorrow"):
            iso = day_iso(data.split(":")[1])
            note_tmp[uid] = {"op": "add", "date": iso}
            pending[uid] = "note_text"
            u = fresh()
            await safe_edit(ask_note_text(u.lang, note_label(iso)), cancel_buttons(u.lang))
            return
        if data == "nadd:custom":
            pending[uid] = "note_add_custom"
            await safe_edit(ask_custom_date(fresh().lang), cancel_buttons(fresh().lang))
            return
        if data == "note:view":
            u = fresh()
            await safe_edit(ask_note_date(u.lang), note_date_buttons(u.lang, "nview"))
            return
        if data in ("nview:today", "nview:tomorrow"):
            await show_note(day_iso(data.split(":")[1]))
            return
        if data == "nview:custom":
            pending[uid] = "note_view_custom"
            await safe_edit(ask_custom_date(fresh().lang), cancel_buttons(fresh().lang))
            return
        if data == "note:del":
            u = fresh()
            await safe_edit(ask_note_date(u.lang), note_date_buttons(u.lang, "ndel"))
            return
        if data in ("ndel:today", "ndel:tomorrow"):
            await ask_delete(day_iso(data.split(":")[1]))
            return
        if data == "ndel:custom":
            pending[uid] = "note_del_custom"
            await safe_edit(ask_custom_date(fresh().lang), cancel_buttons(fresh().lang))
            return
        if data.startswith("note:edit:"):
            iso = data[len("note:edit:"):]
            try:
                date.fromisoformat(iso)
            except ValueError:
                return
            note_tmp[uid] = {"op": "edit", "date": iso}
            pending[uid] = "note_text"
            u = fresh()
            await safe_edit(ask_note_text(u.lang, note_label(iso)), cancel_buttons(u.lang))
            return
        if data.startswith("note:delone:"):
            iso = data[len("note:delone:"):]
            try:
                date.fromisoformat(iso)
            except ValueError:
                return
            await ask_delete(iso)
            return
        if data.startswith("note:del:yes:"):
            iso = data[len("note:del:yes:"):]
            try:
                date.fromisoformat(iso)
            except ValueError:
                return
            store.delete_note(uid, iso)
            u = fresh()
            await safe_edit(note_deleted(u.lang, note_label(iso)),
                            notes_menu_buttons(u.lang))
            return

        # --- маршруты 2GIS: тип -> варианты -> детали + сохранить; избранное ---
        async def show_options(sess: dict, use_cache: bool) -> None:
            """Запросить варианты по sess[from/to/mode], показать кнопками.
            Ночью (01:00–05:30) + метро + известная пара: якорный расчёт от 05:30
            (compute_night_exit), а не мусорное ожидание и не пешие варианты.
            Ночью без пары (избранное): 'закрыто' + пешком, как раньше.
            В серой зоне (00:30–01:00) считаем метро, но с предупреждением."""
            u = fresh()
            mode = sess.get("mode", "walk")
            tz = ZoneInfo(settings.institution_tz)
            now = datetime.now(tz)
            state = metro_state(now)
            if mode == "metro" and state == "closed" and sess.get("lesson") is not None:
                lesson = sess["lesson"]
                open_dt = datetime(lesson.starts_at.year, lesson.starts_at.month,
                                   lesson.starts_at.day, OPEN_H, OPEN_M, tzinfo=tz)
                out = await compute_night_exit(
                    lesson_start=lesson.starts_at, from_xy=sess["from"], to_xy=sess["to"],
                    buffer_min=sess.get("buffer", 10), routing=routing,
                    open_dt=open_dt, now=now, use_cache=use_cache)
                sess["closed"] = True
                sess["options"] = []
                await safe_edit(night_exit_text(u.lang, sess["lesson_line"], out),
                                cancel_buttons(u.lang))
                return
            sess["closed"] = state == "closed" and mode == "metro"
            try:
                if mode == "metro" and state != "closed":
                    try:
                        options = await routing.metro(sess["from"], sess["to"],
                                                      use_cache=use_cache)
                    except NoMetroError:
                        options = await routing.walking(sess["from"], sess["to"],
                                                        use_cache=use_cache)
                        sess["fallback"] = True
                    else:
                        sess["fallback"] = False
                else:
                    options = await routing.walking(sess["from"], sess["to"],
                                                    use_cache=use_cache)
                    sess["fallback"] = False
            except RoutingError:
                await safe_edit(route_failed(u.lang))
                return
            if not options:
                await safe_edit(route_failed(u.lang))
                return
            sess["options"] = options
            txt = variants_text(u.lang, sess["lesson_line"], options)
            if sess.get("closed"):
                txt = metro_closed(u.lang) + "\n\n" + txt
            elif sess.get("fallback"):
                txt = no_metro_fallback(u.lang) + "\n\n" + txt
            elif state == "gray" and mode == "metro":
                txt = metro_gray(u.lang) + "\n\n" + txt
            await safe_edit(txt, variants_buttons(options, u.lang))

        if data in ("rtm:walk", "rtm:metro"):
            sess = route_sessions.get(uid)
            if not sess:
                await safe_edit(route_session_expired(fresh().lang))
                return
            sess["mode"] = data[4:]
            await show_options(sess, use_cache=True)
            return
        if data.startswith("rtv:"):
            sess = route_sessions.get(uid)
            try:
                i = int(data[4:])
            except ValueError:
                return
            if not sess or i >= len(sess.get("options", [])):
                await safe_edit(route_session_expired(fresh().lang))
                return
            opt = sess["options"][i]
            sess["selected"] = i
            u = fresh()
            exit_line = ""
            if sess.get("lesson") is not None:
                exit_line = exit_line_for(sess["lesson"], opt.duration_s,
                                          sess.get("buffer", 10), u.lang)
            await safe_edit(route_details(u.lang, opt, exit_line),
                            route_details_buttons(i, u.lang))
            return
        if data == "rt:now":
            # "Выйти сейчас": свежее прибытие пешком и (если открыто) метро.
            sess = route_sessions.get(uid)
            if not sess:
                await safe_edit(route_session_expired(fresh().lang))
                return
            u = fresh()
            en = u.lang == "en"
            tz = ZoneInfo(settings.institution_tz)
            now = datetime.now(tz)
            state = metro_state(now)
            fr, to = sess["from"], sess["to"]
            lesson = sess.get("lesson")
            lines = [sess.get("lesson_line") or ("🏃 Leaving now" if en else "🏃 Выйти сейчас")]
            try:
                w = await routing.walking(fr, to, use_cache=False)
                w_arr = now + timedelta(seconds=w[0].duration_s) if w else None
            except RoutingError:
                w_arr = None
            if lesson is not None:
                lines.append(leave_now_line(u.lang, "🚶", w_arr, lesson))
            else:
                lines.append(f"🚶 arrival at {w_arr:%H:%M}." if (en and w_arr) else
                             (f"🚶 приедешь в {w_arr:%H:%M}." if w_arr else
                              ("🚶 — couldn't calculate." if en else "🚶 — не посчиталось.")))
            if state == "closed":
                lines.append(metro_closed(u.lang))
            else:
                try:
                    m = await routing.metro(fr, to, use_cache=False)
                    m_arr = now + timedelta(seconds=m[0].duration_s) if m else None
                except RoutingError:
                    m_arr = None
                if state == "gray":
                    lines.append(metro_gray(u.lang))
                if lesson is not None:
                    lines.append(leave_now_line(u.lang, "🚇", m_arr, lesson))
                else:
                    lines.append(f"🚇 arrival at {m_arr:%H:%M}." if (en and m_arr) else
                                 (f"🚇 приедешь в {m_arr:%H:%M}." if m_arr else
                                  ("🚇 — couldn't calculate." if en else "🚇 — не посчиталось.")))
            await safe_edit("\n".join(lines))
            return
        if data == "rt:target":
            sess = route_sessions.get(uid)
            u = fresh()
            if not sess or sess.get("lesson") is None:
                await safe_edit(route_session_expired(u.lang))
                return
            pending[uid] = "target_time"
            await safe_edit(ask_target_time(u.lang, sess["lesson_line"]),
                            cancel_buttons(u.lang))
            return
        if data.startswith("rt:save:"):
            sess = route_sessions.get(uid)
            try:
                i = int(data[len("rt:save:"):])
            except ValueError:
                return
            if not sess or i >= len(sess.get("options", [])):
                await safe_edit(route_session_expired(fresh().lang))
                return
            u = fresh()
            name = f"Дом → {sess['label']}"
            store.add_favorite(uid, name, fmt_coords(*sess["from"]),
                               fmt_coords(*sess["to"]), sess.get("mode", "walk"))
            await safe_edit(route_saved(u.lang, name))
            return
        if data == "rt:cancel":
            route_sessions.pop(uid, None)
            await show_main_menu(cb, fresh().lang)
            return
        if data.startswith("fav:"):
            try:
                fid = int(data[4:])
            except ValueError:
                return
            fav = next((f for f in store.list_favorites(uid) if f.id == fid), None)
            if fav is None:
                await safe_edit(route_session_expired(fresh().lang))
                return
            fr, to = parse_coords(fav.from_coords), parse_coords(fav.to_coords)
            if fr is None or to is None:
                await safe_edit(route_failed(fresh().lang))
                return
            route_sessions[uid] = {"from": fr, "to": to, "label": fav.name,
                                   "lesson_line": fav.name, "lesson": None,
                                   "buffer": 10, "mode": fav.transport_type,
                                   "options": [], "fallback": False}
            await show_options(route_sessions[uid], use_cache=False)  # свежее время, не из кэша
            return
        if data.startswith("favdel:"):
            try:
                fid = int(data[len("favdel:"):])
            except ValueError:
                return
            fav = next((f for f in store.list_favorites(uid) if f.id == fid), None)
            if fav is None:
                return
            u = fresh()
            q, kb2 = fav_confirm_delete(u.lang, fav.name, fid)
            await safe_edit(q, kb2)
            return
        if data.startswith("favdel_yes:"):
            try:
                fid = int(data[len("favdel_yes:"):])
            except ValueError:
                return
            fav = next((f for f in store.list_favorites(uid) if f.id == fid), None)
            name = fav.name if fav else ""
            store.delete_favorite(uid, fid)
            u = fresh()
            await safe_edit(fav_deleted(u.lang, name),
                            fav_list_buttons(store.list_favorites(uid), u.lang))
            return

    # Health-порт для Render Free + iOS API: работает параллельно с polling.
    # aiogram start_polling сам ловит SIGINT/SIGTERM -> выходим в finally и всё закрываем.
    from .api import ApiCtx

    api_ctx = ApiCtx(settings=settings, store=store, schedule_client=sched_client,
                     buildings=buildings, geocoder=geocoder, routing=routing)
    health_runner = await start_health_server(ctx=api_ctx)
    sched_task = asyncio.create_task(scheduler_loop(bot, settings, store, deps, last_calc))
    try:
        await dp.start_polling(bot)
    finally:
        sched_task.cancel()
        await stop_health_server(health_runner)
        try:
            store.close()
        except Exception:
            pass
        for c in (sched_client, geocoder, routing):
            try:
                if hasattr(c, "close"):
                    await c.close()
                else:
                    await c._http.aclose()
            except Exception:
                pass
        try:
            await bot.session.close()
        except Exception:
            pass


if __name__ == "__main__":
    import asyncio as _a

    _a.run(main())