from __future__ import annotations

import asyncio
import difflib
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                           Message, ReplyKeyboardMarkup)

from .address_check import verify_address_text
from .buildings import BuildingStore, load_buildings_yaml
from .cards import (build_evening, build_morning, evening_failed, format_telegram_day,
                    format_telegram_evening, format_telegram_morning, with_metro, build_focus)
from .config import Settings
from .exit_time import first_relevant_lesson, format_duration
from .geocode import NominatimGeocoder
from .metro import MetroGraph
from .normalize import normalize_day, normalize_groups
from .notifications import morning_notify_time, parse_hhmm
from .routing_foot import FosFootProvider
from .routing_osrm import OsrmProvider
from .schedule_client import ScheduleClient
from .service import build_day_view
from .store import Store, UserSettings
from .texts import (MENU_LEAVE, MENU_NOTES, MENU_SETTINGS, MENU_TODAY, MENU_TOMORROW, ask_address,
                    menu_kb, menu_match, need_group_first, need_home, norm_lang, settings_view,
                    start_back, start_need_group, start_need_home, start_new, start_route,
                    notes_menu_text, notes_menu_buttons, note_date_buttons,
                    settings_buttons, transport_buttons, note_date_buttons, pretty_street,
                    menu_kb, menu_match, need_group_first, need_home, norm_lang, settings_view,
                    start_back, start_need_group, start_need_home, start_new, start_route,
                    notes_menu_text, notes_menu_buttons, note_date_buttons,
                    settings_buttons, transport_buttons, note_date_buttons, pretty_street)
from .address_check import format_confirm, match_candidate, parse_address


def kb_for(lang: str) -> ReplyKeyboardMarkup:
    rows = menu_kb(lang)
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in row] for row in rows],
        resize_keyboard=True,
    )


def ikb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
                         for row in rows])


def settings_buttons(lang: str) -> InlineKeyboardMarkup:
    if lang == "en":
        return ikb([[("Change group", "set:group"), ("Change address", "set:address")],
                    [("Transport", "set:transport"), ("Buffer", "set:buffer")],
                    [("Notifications", "set:notify"), ("Language", "set:lang")],
                    [("◀️ Back", "set:back")]])
    return ikb([[("Изменить группу", "set:group"), ("Изменить адрес", "set:address")],
                [("Способ передвижения", "set:transport"), ("Запас времени", "set:buffer")],
                [("Время уведомлений", "set:notify"), ("Язык", "set:lang")],
                [("◀️ Назад", "set:back")]])


def transport_buttons(lang: str) -> InlineKeyboardMarkup:
    from .texts import transport_name

    rows = [[(transport_name(c, lang), f"tr:{c}")] for c in ("transit", "foot", "driving", "bike")]
    rows.append([("◀️ Back", "set:menu")] if lang == "en" else [("◀️ Назад", "set:menu")])
    return ikb(rows)


def notes_menu_buttons(lang: str) -> InlineKeyboardMarkup:
    if lang == "en":
        return ikb([[("➕ Add note", "note:add")],
                    [("👁 View", "note:view"), ("🗑 Delete", "note:del")],
                    [("◀️ Back", "note:back")]])
    return ikb([[("➕ Добавить заметку", "note:add")],
                [("👁 Посмотреть", "note:view"), ("🗑 Удалить", "note:del")],
                [("◀️ Назад", "note:back")]])


def note_date_buttons(lang: str, prefix: str) -> InlineKeyboardMarkup:
    if lang == "en":
        return ikb([[("Today", f"{prefix}:today"), ("Tomorrow", f"{prefix}:tomorrow")],
                    [("Pick a date", f"{prefix}:custom")],
                    [("Cancel", "note:menu")]])
    return ikb([[("Сегодня", f"{prefix}:today"), ("Завтра", f"{prefix}:tomorrow")],
                [("Выбрать дату", f"{prefix}:custom")],
                [("Отмена", "note:menu")]])


def notes_menu_text(lang: str) -> str:
    return "📝 Notes: add, view or delete." if lang == "en" else "📝 Заметки: добавить, посмотреть, удалить."


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


def home_coords_of(u: UserSettings) -> tuple[float, float] | None:
    if u.home_lat is not None and u.home_lon is not None:
        return (u.home_lat, u.home_lon)
    return None


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
    return s


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


async def scheduler_loop(bot: Bot, settings: Settings, store: Store, deps: dict):
    """Every 60 s. Evening: schedule+note (1 API call, no routing).
    Morning: cheap schedule-only check for first lesson; full routing calc
    (up to ~7 foot calls) at most every 30 min per user and only within 4 h
    before the first lesson. Sent-flags persist in DB (no dupes on restart)."""
    tz = ZoneInfo(settings.institution_tz)
    last_calc: dict[int, float] = deps.setdefault("last_calc", {})
    sched_client: ScheduleClient = deps["schedule_client"]
    while True:
        try:
            now = datetime.now(tz)
            for u in store.all_users():
                if not u.group or (not u.home_address and home_coords_of(u) is None):
                    continue
                kw = dict(group=u.group, home_address=u.home_address,
                          home_coords=home_coords_of(u), transport=u.transport,
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
                    await bot.send_message(
                        u.user_id, format_telegram_morning(morning_card(view, note), u.lang))
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
    store = Store(settings.database_path)
    sched_client = ScheduleClient(settings)
    geocoder = NominatimGeocoder(settings)
    router = OsrmProvider(settings)
    foot = FosFootProvider(settings)
    try:
        metro = MetroGraph.load(settings.metro_data_file)
    except (FileNotFoundError, ValueError):
        metro = None
    deps = {"schedule_client": sched_client, "buildings": buildings, "geocoder": geocoder,
            "router": router, "foot": foot, "metro": metro}
    addr_picks: dict[int, list[tuple[str, float, float]]] = {}  # uid -> [(label, lat, lon)]
    note_tmp: dict[int, dict] = {}  # uid -> {"op": add/view/del, "date": iso}

    def routing_changed(uid: int) -> None:
        """Address/group/transport/buffer changed: drop everything computed
        for the old home. Today's morning flag is cleared so the next loop
        iteration recomputes from the NEW home (the send-window check still
        guards against late/duplicate sends)."""
        deps.setdefault("last_calc", {}).pop(uid, None)
        try:
            foot.drop()
        except Exception:
            pass
        try:
            today = datetime.now(ZoneInfo(settings.institution_tz)).date().isoformat()
            store.clear_sent(uid, today, "morn")
        except Exception:
            pass

    async def verify_address(text: str, lang: str = "ru") -> tuple[str, list[tuple[str, float, float]], str]:
        return await verify_address_text(geocoder, text, lang)

    async def ask_confirm(m: Message, label: str, lat: float, lon: float, lang: str = "ru") -> None:
        addr_picks[m.from_user.id] = [(label, lat, lon)]
        pending[m.from_user.id] = "address_confirm"
        kb = kb_for(lang)
        if lang == "en":
            await m.answer(f"Found: {label}.\nIs this your home? Reply “yes” or “no”.",
                           reply_markup=kb)
        else:
            await m.answer(f"Нашёл: {label}.\nЭто ваш дом? Ответьте «да» или «нет».",
                           reply_markup=kb)

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
            view = await build_day_view(group=u.group, day=day, now=now, home_address=u.home_address,
                                        home_coords=home_coords_of(u),
                                        transport=u.transport, buffer_min=u.buffer_min,
                                        for_today=for_today, **deps, settings=settings)
            note = store.get_note(m.from_user.id, day.isoformat())
            if action == "leave":
                await m.answer(format_telegram_morning(morning_card(view, note), lang), reply_markup=kb)
            else:
                if action == "today":
                    # Intraday focus for "Today" button
                    tz = ZoneInfo(settings.institution_tz)
                    now = datetime.now(tz)
                    lesson = view.target
                    next_lesson = None
                    if lesson and view.schedule.active_lessons:
                        idx = view.schedule.active_lessons.index(lesson)
                        if idx + 1 < len(view.schedule.active_lessons):
                            next_lesson = view.schedule.active_lessons[idx + 1]
                    travel_s = view.plan.travel_seconds if view.plan else None
                    route_ok = view.plan is not None
                    focus_text = build_focus(lesson, next_lesson, travel_s, u.buffer_min, now, route_ok, lang)
                    await m.answer(focus_text, reply_markup=kb)
                else:
                    label = ("Today" if action == "today" else "Tomorrow") if lang == "en" else \
                        ("Сегодня" if action == "today" else "Завтра")
                    if view.schedule_failed:
                        card = evening_failed(day.isoformat())
                    else:
                        card = build_evening(view.schedule, note)
                    await m.answer(format_telegram_day(f"{label}, {day.strftime('%d.%m')}", card,
                                                       day_exit_line(view, lang) if view.target else "",
                                                       lang), reply_markup=kb)
            return
        if action == "notes":
            await m.answer(notes_menu_text(lang), reply_markup=notes_menu_buttons(lang))
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
            await m.answer(f"Pinned: {lat}, {lon}.{near}\nSave as home? Reply “yes” or “no”.",
                           reply_markup=kb)
        else:
            await m.answer(f"Принял точку: {lat}, {lon}.{near}\n"
                           f"Сохранить как дом? Ответьте «да» или «нет».",
                           reply_markup=kb)

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
                return f"Found several matching houses:\n{lines}\nReply with the number you need."
            return (f"Нашёл несколько подходящих домов:\n{lines}\n"
                    f"Ответьте номером нужного.")

        async def run_verify(a):
            status, suitable, msg = await verify_address(a, lang)
            if status == "ok-one":
                await ask_confirm(m, *suitable[0], lang)
            elif status == "ok-many":
                addr_picks[uid] = suitable
                pending[uid] = "address_pick"
                await m.answer(variants_msg(suitable), reply_markup=kb)
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
            pending[uid] = "address"
            await m.answer(ask_address(lang, g), reply_markup=kb)
            return
        if state == "address_confirm":
            if low in ("да", "ага", "точно", "верно", "подтверждаю", "yes", "y", "yeah", "ok"):
                picks = addr_picks.pop(uid, [])
                if picks:
                    label, lat, lon = picks[0]
                    u.home_address = label
                    u.home_lat, u.home_lon = lat, lon
                    store.save_user(u)
                    pending.pop(uid, None)
                    routing_changed(uid)  # Invalidate old routing cache
                    done = "Home saved." if en else "Дом сохранён."
                    await m.answer(f"{done}\n\n" + settings_card(u), reply_markup=kb)
                else:
                    pending.pop(uid, None)
                    await m.answer("Options expired, send the address again." if en else
                                   "Варианты устарели, введите адрес ещё раз.", reply_markup=kb)
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
            if v not in ("transit", "foot", "walking", "driving", "car", "bike",
                         "общественный", "пешком", "машина", "вело"):
                await m.answer("transit | foot | driving | bike", reply_markup=kb)
                return
            u.transport = {"walking": "foot", "car": "driving"}.get(v, v)
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
        if state == "note" or low.startswith(("покажи ", "show ")) or \
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

    # --- Note flow text handlers ---
    @dp.message(F.text)
    async def note_text_handler(m: Message):
        uid = m.from_user.id
        u = store.get_user(uid)
        lang, en = u.lang, u.lang == "en"
        text = (m.text or "").strip()
        state = pending.get(uid, "")
        kb = kb_for(u.lang)

        if state == "note_add_custom":
            d = text.strip()
            try:
                date.fromisoformat(d)
            except Exception:
                await m.answer("Invalid date format. Use YYYY-MM-DD." if en else "Неверный формат даты. Используйте ГГГГ-ММ-ДД.", reply_markup=kb)
                return
            pending[uid] = {"op": "add", "date": d}
            await m.answer("What to take / do?" if en else "Что взять / сделать?", reply_markup=kb)
            return

        if state == "note_view":
            d = text.strip()
            try:
                date.fromisoformat(d)
            except Exception:
                await m.answer("Invalid date format. Use YYYY-MM-DD." if en else "Неверный формат даты. Используйте ГГГГ-ММ-ДД.", reply_markup=kb)
                return
            note = store.get_note(uid, d)
            await m.answer(f"{d}: {note or ('(empty)' if en else '(пусто)')}", reply_markup=kb)
            return

        if state == "note_del":
            d = text.strip()
            try:
                date.fromisoformat(d)
            except Exception:
                await m.answer("Invalid date format. Use YYYY-MM-DD." if en else "Неверный формат даты. Используйте ГГГГ-ММ-ДД.", reply_markup=kb)
                return
            store.delete_note(uid, d)
            await m.answer("Deleted." if en else "Удалено.", reply_markup=kb)
            return

        if state == "note_add":
            pending.pop(uid, None)
            data = pending.get(uid)
            if not data or data.get("op") != "add":
                return
            d = data.get("date")
            store.set_note(uid, d, text)
            pending.pop(uid, None)
            await m.answer("Note saved." if en else "Заметка сохранена.", reply_markup=kb)
            return

    asyncio.create_task(scheduler_loop(bot, settings, store, deps))
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio as _a

    _a.run(main())