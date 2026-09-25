"""Telegram interface (aiogram 3). Thin layer over service.py; all math stays testable."""
from __future__ import annotations

import asyncio
import difflib
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from .address_check import verify_address_text
from .buildings import BuildingStore, load_buildings_yaml
from .cards import (build_evening, build_morning, evening_failed, format_telegram_day,
                    format_telegram_evening, format_telegram_morning, with_metro)
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

KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Сегодня"), KeyboardButton(text="Завтра")],
        [KeyboardButton(text="Когда выходить?")],
        [KeyboardButton(text="Заметка на день"), KeyboardButton(text="Настройки")],
    ],
    resize_keyboard=True,
)


def settings_card(u: UserSettings) -> str:
    home = u.home_address or "—"
    if u.home_lat is not None:
        home += " (точка сохранена)"
    return (
        f"Ваши настройки:\nгруппа: {u.group or '—'}\nдом: {home}\n"
        f"транспорт: {u.transport}\nзапас: {u.buffer_min} мин\n"
        f"вечернее: {u.evening_time}\nутреннее: за {u.morning_min_before_exit} мин до выхода"
    )


def home_coords_of(u: UserSettings) -> tuple[float, float] | None:
    if u.home_lat is not None and u.home_lon is not None:
        return (u.home_lat, u.home_lon)
    return None


def day_exit_line(view) -> str:
    """Строка выхода для дневного расписания (тот же стиль)."""
    if view.plan is None:
        if view.target is None:
            return ""
        if getattr(view, "unknown_building", False):
            return "⚠️ Адрес корпуса неизвестен — время выхода не посчитано."
        return "⚠️ Дорогу посчитать не получилось — выходите с запасом."
    p = view.plan
    s = f"🏃 Выйти в {p.exit_at.strftime('%H:%M')} (~{format_duration(p.travel_seconds)} в пути)"
    if p.already_passed:
        s += ". ⚠️ Время уже прошло — выходите сейчас!"
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
    last_calc: dict[int, float] = {}
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
                        await bot.send_message(u.user_id, format_telegram_evening(card))
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
                        u.user_id, format_telegram_morning(morning_card(view, note)))
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

    async def verify_address(text: str) -> tuple[str, list[tuple[str, float, float]], str]:
        return await verify_address_text(geocoder, text)

    async def ask_confirm(m: Message, label: str, lat: float, lon: float) -> None:
        addr_picks[m.from_user.id] = [(label, lat, lon)]
        pending[m.from_user.id] = "address_confirm"
        await m.answer(f"Нашёл: {label}.\nЭто ваш дом? Ответьте «да» или «нет».",
                       reply_markup=KB)
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
        pending[m.from_user.id] = "group"
        try:
            names = [g.name for g in normalize_groups(await sched_client.get_groups_raw())]
        except Exception:
            names = []
        intro = (f"Привет! Введите вашу группу, например ИДБ-26-14." if names else
                 "Привет! Введите вашу группу.\n")
        await m.answer(
            f"{intro}\n\n"
            f"Текущие настройки: группа={u.group or '—'}, адрес={u.home_address or '—'}, "
            f"транспорт={u.transport}, запас={u.buffer_min} мин.",
            reply_markup=KB,
        )

    @dp.message(F.text.in_({"Сегодня", "Завтра", "Когда выходить?"}))
    async def show_day(m: Message):
        u = store.get_user(m.from_user.id)
        if not u.group:
            await m.answer("Сначала укажите группу через /start.", reply_markup=KB)
            return
        if not u.home_address and home_coords_of(u) is None:
            await m.answer("Сначала добавьте домашний адрес: напишите его текстом "
                           "(например, «ул. Островитянова, 33А») или отправьте точку "
                           "(скрепка → Геопозиция). Без него время выхода посчитать не выйдет.",
                           reply_markup=KB)
            return
        tz = ZoneInfo(settings.institution_tz)
        now = datetime.now(tz)
        day = now.date() if m.text != "Завтра" else now.date() + timedelta(days=1)
        for_today = m.text in ("Сегодня", "Когда выходить?")
        view = await build_day_view(group=u.group, day=day, now=now, home_address=u.home_address,
                                    home_coords=home_coords_of(u),
                                    transport=u.transport, buffer_min=u.buffer_min,
                                    for_today=for_today, **deps, settings=settings)
        note = store.get_note(m.from_user.id, day.isoformat())
        if m.text == "Когда выходить?":
            await m.answer(format_telegram_morning(morning_card(view, note)), reply_markup=KB)
        else:
            label = "Сегодня" if m.text == "Сегодня" else "Завтра"
            if view.schedule_failed:
                card = evening_failed(day.isoformat())
            else:
                card = build_evening(view.schedule, note)
            await m.answer(format_telegram_day(f"{label}, {day.strftime('%d.%m')}", card,
                                               day_exit_line(view) if view.target else ""),
                           reply_markup=KB)

    @dp.message(F.location)
    async def save_location(m: Message):
        uid = m.from_user.id
        lat = round(m.location.latitude, 6)
        lon = round(m.location.longitude, 6)
        label = await geocoder.reverse_label(lat, lon)
        near = f" Рядом с: {label}." if label else ""
        # Pin is NOT claimed to be an exact house match.
        addr_picks[uid] = [(f"точка {lat}, {lon}", lat, lon)]
        pending[uid] = "address_confirm"
        await m.answer(f"Принял точку: {lat}, {lon}.{near}\n"
                       f"Сохранить как дом? Ответьте «да» или «нет».",
                       reply_markup=KB)

    @dp.message(F.text == "Заметка на день")
    async def note_help(m: Message):
        pending[m.from_user.id] = "note"
        await m.answer("Пришлите заметку в формате: 2026-09-24 текст заметки\n"
                       "Пустой текст после даты удаляет заметку.\n"
                       "Команды: 'покажи 2026-09-24' — посмотреть.", reply_markup=KB)

    @dp.message(F.text == "Настройки")
    async def settings_help(m: Message):
        u = store.get_user(m.from_user.id)
        await m.answer(
            settings_card(u) + "\n\nМеняется без перезапуска: пришлите "
            f"адрес <текст> (или точку) | транспорт <transit/foot/driving/bike> | "
            f"запас <мин> | вечер <ЧЧ:ММ> | утро <мин> | группа <название>",
            reply_markup=KB,
        )

    @dp.message(F.text)
    async def fallback(m: Message):
        uid = m.from_user.id
        u = store.get_user(uid)
        text = (m.text or "").strip()
        low = text.lower()
        state = pending.get(uid, "")

        if state == "group" or text.lower().startswith("группа "):
            g = text[len("группа "):].strip() if text.lower().startswith("группа ") else text
            names = await group_names()
            if names:
                exact = next((n for n in names if n.casefold() == g.casefold()), None)
                if exact is None:
                    sug = difflib.get_close_matches(g, names, n=5, cutoff=0.5)
                    hint = (" Похожие: " + ", ".join(sug)) if sug else ""
                    await m.answer(f"Группы «{g}» нет в списке.{hint}\nПроверьте название и пришлите ещё раз.",
                                   reply_markup=KB)
                    return
                g = exact
            u.group = g
            store.save_user(u)
            pending[uid] = "address"
            await m.answer(f"Группа: {g}. Теперь пришлите домашний адрес одной строкой "
                           f"или отправьте точку (скрепка → Геопозиция).", reply_markup=KB)
            return
        if state == "address_confirm":
            if low in ("да", "ага", "точно", "верно", "подтверждаю", "yes", "y"):
                picks = addr_picks.pop(uid, [])
                if picks:
                    label, lat, lon = picks[0]
                    u.home_address = label
                    u.home_lat, u.home_lon = lat, lon
                    store.save_user(u)
                    pending.pop(uid, None)
                    await m.answer("Дом сохранён.\n\n" + settings_card(u), reply_markup=KB)
                else:
                    pending.pop(uid, None)
                    await m.answer("Варианты устарели, введите адрес ещё раз.", reply_markup=KB)
                return
            if low in ("нет", "не", "no", "n", "не мой", "не мой дом"):
                addr_picks.pop(uid, None)
                pending[uid] = "address"
                await m.answer("Хорошо, не сохраняю. Уточните адрес текстом или "
                               "пришлите геоточку.", reply_markup=KB)
                return
            # any other text = refined address, verify again
            pending.pop(uid, None)
            addr_picks.pop(uid, None)
            text = (m.text or "").strip()
            status, suitable, msg = await verify_address(text)
            if status == "ok-one":
                await ask_confirm(m, *suitable[0])
            elif status == "ok-many":
                addr_picks[uid] = suitable
                pending[uid] = "address_pick"
                lines = "\n".join(f"{i + 1}. {lbl}" for i, (lbl, _, _) in enumerate(suitable))
                await m.answer(f"Нашёл несколько подходящих домов:\n{lines}\n"
                               f"Ответьте номером нужного.", reply_markup=KB)
            else:
                pending[uid] = "address"
                await m.answer(msg, reply_markup=KB)
            return
        if state == "address_pick":
            picks = addr_picks.get(uid, [])
            if text.strip().isdigit():
                i = int(text.strip()) - 1
                if 0 <= i < len(picks):
                    await ask_confirm(m, *picks[i])
                    return
                await m.answer(f"Номер от 1 до {len(picks)}.", reply_markup=KB)
                return
            # refined text instead of a number
            addr_picks.pop(uid, None)
            pending.pop(uid, None)
            status, suitable, msg = await verify_address(text)
            if status == "ok-one":
                await ask_confirm(m, *suitable[0])
            elif status == "ok-many":
                addr_picks[uid] = suitable
                pending[uid] = "address_pick"
                lines = "\n".join(f"{i + 1}. {lbl}" for i, (lbl, _, _) in enumerate(suitable))
                await m.answer(f"Нашёл несколько подходящих домов:\n{lines}\n"
                               f"Ответьте номером нужного.", reply_markup=KB)
            else:
                pending[uid] = "address"
                await m.answer(msg, reply_markup=KB)
            return
        if state == "address" or text.lower().startswith("адрес "):
            a = text[len("адрес "):].strip() if text.lower().startswith("адрес ") else text
            status, suitable, msg = await verify_address(a)
            if status == "ok-one":
                await ask_confirm(m, *suitable[0])
            elif status == "ok-many":
                addr_picks[uid] = suitable
                pending[uid] = "address_pick"
                lines = "\n".join(f"{i + 1}. {lbl}" for i, (lbl, _, _) in enumerate(suitable))
                await m.answer(f"Нашёл несколько подходящих домов:\n{lines}\n"
                               f"Ответьте номером нужного.", reply_markup=KB)
            else:
                pending[uid] = "address"
                await m.answer(msg, reply_markup=KB)
            return
        if low.startswith("транспорт "):
            v = text.split(None, 1)[1].strip()
            if v not in ("transit", "foot", "walking", "driving", "car", "bike"):
                await m.answer("Транспорт: transit | foot | driving | bike", reply_markup=KB)
                return
            u.transport = "foot" if v == "walking" else ("driving" if v == "car" else v)
            store.save_user(u)
            await m.answer(f"Транспорт: {u.transport}", reply_markup=KB)
            return
        if low.startswith("запас "):
            try:
                u.buffer_min = max(0, min(120, int(text.split(None, 1)[1])))
                store.save_user(u)
                await m.answer(f"Запас: {u.buffer_min} мин", reply_markup=KB)
            except Exception:
                await m.answer("Пример: запас 15", reply_markup=KB)
            return
        if low.startswith("вечер "):
            try:
                parse_hhmm(text.split(None, 1)[1])
                u.evening_time = text.split(None, 1)[1].strip()
                store.save_user(u)
                await m.answer(f"Вечернее уведомление: {u.evening_time}", reply_markup=KB)
            except Exception:
                await m.answer("Пример: вечер 21:30", reply_markup=KB)
            return
        if low.startswith("утро "):
            try:
                u.morning_min_before_exit = max(5, min(240, int(text.split(None, 1)[1])))
                store.save_user(u)
                await m.answer(f"Утреннее уведомление за {u.morning_min_before_exit} мин до выхода", reply_markup=KB)
            except Exception:
                await m.answer("Пример: утро 60", reply_markup=KB)
            return
        if state == "note" or low.startswith("покажи ") or (len(text) >= 10 and text[:10].replace("-", "").isdigit()):
            # note formats: "YYYY-MM-DD text" | "покажи YYYY-MM-DD"
            if low.startswith("покажи "):
                d = text.split(None, 1)[1].strip()
                await m.answer(f"{d}: {store.get_note(uid, d) or '(пусто)'}", reply_markup=KB)
                return
            parts = text.split(None, 1)
            try:
                date.fromisoformat(parts[0])
                store.set_note(uid, parts[0], parts[1] if len(parts) > 1 else "")
                pending.pop(uid, None)
                await m.answer("Заметка сохранена.", reply_markup=KB)
                return
            except Exception:
                pass
        await m.answer("Не понял. Используйте кнопки или /start.", reply_markup=KB)

    asyncio.create_task(scheduler_loop(bot, settings, store, deps))
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio as _a

    _a.run(main())
