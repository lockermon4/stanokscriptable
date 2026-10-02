"""Детектор изменений расписания: stankinapp не отдаёт флаги отмены/переноса,
отменённая пара просто исчезает. Изменения ловим сравнением снимков.

Правила:
- при ошибке API снимок не меняется, уведомлений нет;
- изменение подтверждается вторым опросом подряд (pending в БД);
- первый снимок новой даты сохраняется без уведомлений.

Опрос по группам (одна группа = один запрос), сегодня + 7 дней, раз в 30 мин
в том же asyncio-цикле, что планировщик. Факты отправки — в таблице sent,
переживают рестарт.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger("bot.changes")

WATCH_DAYS = 8  # сегодня + 7 дней
WATCH_INTERVAL_S = 30 * 60


def canon_lesson(lesson) -> dict:
    """Канонический урок для снимка/сравнения (стабильные строковые поля)."""
    raw = lesson.raw if isinstance(getattr(lesson, "raw", None), dict) else {}
    return {
        "time": lesson.starts_at.strftime("%H:%M"),
        "end": lesson.ends_at.strftime("%H:%M") if lesson.ends_at else "",
        "subject": lesson.subject or "",
        "room": lesson.room or "",
        "teacher": str(raw.get("teacher") or ""),
        "kind": lesson.kind or "",
    }


def snapshot_of(schedule) -> list[dict]:
    return [canon_lesson(l) for l in schedule.active_lessons]


def _key(l: dict) -> tuple[str, str]:
    return (l["subject"], l["time"])


def diff_snapshots(old: list[dict], new: list[dict]) -> list[dict]:
    """removed/added/moved/room/teacher/time. Порядок детерминированный."""
    old_by = {_key(l): l for l in old}
    new_by = {_key(l): l for l in new}
    changes: list[dict] = []
    for k in sorted(set(old_by) & set(new_by)):
        o, n = old_by[k], new_by[k]
        subject, time = k
        if o["room"] != n["room"]:
            changes.append({"type": "room", "time": time, "subject": subject,
                            "detail": f"{o['room'] or '—'}→{n['room'] or '—'}"})
        if o["teacher"] != n["teacher"]:
            changes.append({"type": "teacher", "time": time, "subject": subject,
                            "detail": f"{o['teacher'] or '—'}→{n['teacher'] or '—'}"})
        if o["end"] != n["end"] and o["end"] and n["end"]:
            changes.append({"type": "time", "time": time, "subject": subject,
                            "detail": f"конец {o['end']}→{n['end']}"})
    removed = [old_by[k] for k in sorted(set(old_by) - set(new_by))]
    added = [new_by[k] for k in sorted(set(new_by) - set(old_by))]
    # Перенос = исчезла в одном слоте + появилась в другом с тем же предметом.
    by_subj_r: dict[str, list[dict]] = {}
    for l in removed:
        by_subj_r.setdefault(l["subject"], []).append(l)
    by_subj_a: dict[str, list[dict]] = {}
    for l in added:
        by_subj_a.setdefault(l["subject"], []).append(l)
    used_r, used_a = set(), set()
    for subj in sorted(set(by_subj_r) & set(by_subj_a)):
        rs, al = by_subj_r[subj], by_subj_a[subj]
        for o, n in zip(sorted(rs, key=lambda l: l["time"]),
                        sorted(al, key=lambda l: l["time"])):
            used_r.add((subj, o["time"]))
            used_a.add((subj, n["time"]))
            room_part = f", ауд. {n['room']}" if n["room"] and n["room"] != o["room"] else \
                (f", ауд. {n['room']}" if n["room"] else "")
            changes.append({"type": "moved", "time": n["time"], "subject": subj,
                            "detail": f"было {o['time']} → стало {n['time']}{room_part}"})
    for l in removed:
        if (l["subject"], l["time"]) not in used_r:
            changes.append({"type": "removed", "time": l["time"], "subject": l["subject"],
                            "detail": ""})
    for l in added:
        if (l["subject"], l["time"]) not in used_a:
            room_part = f", ауд. {l['room']}" if l["room"] else ""
            changes.append({"type": "added", "time": l["time"], "subject": l["subject"],
                            "detail": room_part.lstrip(", ")})
    changes.sort(key=lambda c: (c["time"], c["type"]))
    return changes


def changes_hash(changes: list[dict]) -> str:
    import json

    raw = json.dumps(changes, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


async def check_group_day(store, sched_client, buildings, group: str, day_iso: str,
                          tz_name: str) -> tuple[str, list[dict], list[dict]]:
    """Один опрос (группа, дата). Возвращает (статус, дифф, свежие_уроки):
    ok/no-diff | ok/confirmed | ok/pending | error | first-snapshot.
    Снимок обновляется только при успехе; при ошибке API — ничего не меняется."""
    from .normalize import normalize_day

    try:
        raw = await sched_client.get_day_raw(group, day_iso, fresh=True)
    except Exception as e:
        log.warning("watch %s %s: api failed, snapshot untouched: %s", group, day_iso, e)
        return "error", [], []
    try:
        day = datetime.strptime(day_iso, "%Y-%m-%d").date()
        sched, _ = normalize_day(raw, group=group, day=day, tz_name=tz_name)
    except Exception as e:
        log.warning("watch %s %s: normalize failed: %s", group, day_iso, e)
        return "error", [], []
    fresh = snapshot_of(sched)
    prev = store.get_snapshot(group, day_iso)
    if prev is None:
        store.save_snapshot(group, day_iso, fresh)  # первый снимок — без уведомлений
        return "first-snapshot", [], fresh
    diff = diff_snapshots(prev, fresh)
    if not diff:
        store.save_snapshot(group, day_iso, fresh)
        store.clear_pending(group, day_iso)
        return "no-diff", [], fresh
    pending = store.get_pending(group, day_iso)
    if pending == diff:
        return "confirmed", diff, fresh  # подтверждено вторым опросом подряд
    store.save_pending(group, day_iso, diff)  # первый раз видим — ждём повтора
    return "pending", diff, fresh


def _users_of_group(store, group: str) -> list:
    try:
        return [u for u in store.all_users() if u.group == group]
    except Exception as e:
        log.warning("watch users failed: %s", e)
        return []


async def handle_confirmed(bot, settings, store, deps, group: str, day_iso: str,
                           changes: list[dict], fresh_lessons: list[dict]) -> None:
    """Уведомить пользователей группы + пересчитать выход, если уехала первая пара."""
    from .cards import format_telegram_morning
    from .service import build_day_view
    from .texts import morning_recalc_text, schedule_change_text

    prev = store.get_snapshot(group, day_iso) or []
    store.save_changes(group, day_iso, changes)
    store.save_snapshot(group, day_iso, fresh_lessons)
    store.clear_pending(group, day_iso)
    h = changes_hash(changes)
    tz = ZoneInfo(settings.institution_tz)
    now = datetime.now(tz)
    day = datetime.strptime(day_iso, "%Y-%m-%d").date()
    first_changed = (prev[:1] != fresh_lessons[:1])
    for u in _users_of_group(store, group):
        try:
            text = schedule_change_text(u.lang, day_iso, changes)
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🚪 Пересчитать выход" if u.lang != "en"
                                          else "🚪 Recalculate exit",
                                     callback_data=f"chg:recalc:{day_iso}")]])
            await bot.send_message(u.user_id, text, reply_markup=kb)
            store.mark_sent(u.user_id, day_iso, f"chg_{h}")
        except Exception as e:
            log.warning("watch notify %s failed: %s", u.user_id, e)
        if first_changed and store.was_sent(u.user_id, day_iso, "morn") and \
                not store.was_sent(u.user_id, day_iso, "morn_recalc"):
            try:
                from .bot import home_coords_of

                view = await build_day_view(
                    settings=settings, schedule_client=deps["schedule_client"],
                    buildings=deps["buildings"], geocoder=deps["geocoder"],
                    group=u.group, day=day, now=now, home_address=u.home_address,
                    transport=u.transport, buffer_min=u.buffer_min,
                    for_today=(day == now.date()), routing=deps.get("routing"),
                    home_coords=home_coords_of(u), allow_cache=True, fresh=True)
                from .bot import morning_card

                await bot.send_message(
                    u.user_id, morning_recalc_text(u.lang) + "\n" +
                    format_telegram_morning(morning_card(view, ""), u.lang))
                store.mark_sent(u.user_id, day_iso, "morn_recalc")
            except Exception as e:
                log.warning("watch recalc %s failed: %s", u.user_id, e)


async def watch_loop(bot, settings, store, deps) -> None:
    """Фоновая задача раз в 30 мин, в том же цикле, что планировщик."""
    tz = ZoneInfo(settings.institution_tz)
    while True:
        try:
            now = datetime.now(tz)
            groups = sorted({u.group for u in _users_of_group_all(store) if u.group})
            for group in groups:
                for d in range(WATCH_DAYS):
                    day_iso = (now.date() + timedelta(days=d)).isoformat()
                    try:
                        status, diff, fresh = await check_group_day(
                            store, deps["schedule_client"], deps["buildings"],
                            group, day_iso, settings.institution_tz)
                    except Exception as e:
                        log.warning("watch check failed: %s", e)
                        continue
                    if status != "confirmed":
                        continue
                    await handle_confirmed(bot, settings, store, deps, group, day_iso,
                                           diff, fresh)
        except Exception as e:
            log.warning("watch loop failed: %s", e)
        await asyncio.sleep(WATCH_INTERVAL_S)


def _users_of_group_all(store) -> list:
    try:
        return store.all_users()
    except Exception:
        return []
