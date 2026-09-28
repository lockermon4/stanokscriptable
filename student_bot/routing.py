"""Маршруты через 2GIS Routing API. Только пешком и метро, машины нет.

Координаты внутри — (lat, lon); в тела запросов конвертируем сами.

    async def get_walking_route(from_coords, to_coords) -> список маршрутов
    async def get_metro_route(from_coords, to_coords) -> список маршрутов

Ключ: $GIS_API_KEY. Кэш in-memory: walk TTL 1 ч (пеший от пробок не зависит),
metro TTL 15 мин. Ключ кэша: округление до 4 знаков + тип.

Схема ответов (прислана владельцем, 2026-09-26) зафиксирована в
WALK_FIELDS / METRO_FIELDS — названия полей оттуда, не выдуманы.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("bot.routing")

WALK_URL = "https://routing.api.2gis.com/routing/7.0.0/global"
METRO_URL = "https://routing.api.2gis.com/public_transport/2.0"

WALK_TTL_S = 3600
METRO_TTL_S = 900

# Санити-порог ожидания посадки: дневной интервал метро Москвы — минуты,
# вечером до ~10 мин. Больше 30 мин на одной посадке — битые данные API
# (живьём 2026-09-26: waiting_duration=15708с = 4.3ч посреди дня).
# Такие варианты отбрасываем, а не суммируем в "выйти в 04:42".
MAX_WAIT_S = 1800


class RoutingError(RuntimeError):
    pass


class NoMetroError(RoutingError):
    """Метро между точками не прокладывается (пусто или только пешком)."""


@dataclass(frozen=True)
class RouteOption:
    mode: str  # "walk" | "metro"
    duration_s: int
    distance_m: int = 0
    transfers: int = 0  # метро: число пересадок
    walk_before_s: int = 0  # метро: пешком до первой станции
    walk_after_s: int = 0  # метро: пешком от последней станции
    summary: str = ""  # короткая строка для кнопки/списка
    steps: tuple[str, ...] = ()  # пошаговое описание (станции/улицы)
    raw: dict = field(default_factory=dict, compare=False)


# ---------------------------------------------------------------------------
# Маппинг полей ответа — из документации владельца (2026-09-26), не выдумано.
# Walk: {"result": [{"total_distance": {"value", "text"},
#                    "total_duration": {"value", "text"},
#                    "maneuvers": [{"type", "comment", "outcoming_path"}]}]}
# Metro: top-level list [{total_duration, total_distance, total_walkway_distance,
#          transfer_count, crossing_count, pedestrian, transport_types,
#          movements: [{type: passage|walkway|crossing, moving_duration,
#                       waiting_duration,
#                       metro: {line_name, ui_direction_suggest, ui_station_count},
#                       platforms: {names}, (routes реально null),
#                       waypoint: {name, subtype, comment}}]}]
# (живьём 2026-09-26: routes=null, ветка в metro.line_name, тип crossing — переход)
# ---------------------------------------------------------------------------
WALK_FIELDS: dict[str, str] = {
    # Реальный ответ (проверен живьём 2026-09-26): total_distance/total_duration —
    # числа (метры/секунды); ui_total_distance {"unit", "value"},
    # ui_total_duration — строка; maneuvers — только start/finish;
    # algorithm — "по основным улицам" / "кратчайший".
    "routes": "result",
    "duration": "total_duration",
    "distance": "total_distance",
    "duration_text": "ui_total_duration",
    "distance_text": "ui_total_distance",
    "steps": "maneuvers[].comment",
}
METRO_FIELDS: dict[str, str] = {
    "routes": "<top-level list>",
    "duration": "total_duration",
    "distance": "total_distance",
    "transfers": "transfer_count",
    "walk_segments": "movements[type=walkway].moving_duration",
    "legs": "movements",
}


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def parse_walk_payload(payload: dict) -> list[RouteOption]:
    """Сырой JSON -> варианты пешком (поля из WALK_FIELDS)."""
    options: list[RouteOption] = []
    routes = payload.get("result") or []
    if not isinstance(routes, list):
        return options
    for r in routes:
        if not isinstance(r, dict):
            continue
        dur = _num(r.get("total_duration"))
        if dur is None:
            continue
        dist = _num(r.get("total_distance")) or 0
        dur_txt = r.get("ui_total_duration") or ""
        dd = r.get("ui_total_distance") or {}
        dist_txt = f"{dd.get('value', '')} {dd.get('unit', '')}".strip() if isinstance(dd, dict) else ""
        summary = ", ".join(t for t in (dur_txt, dist_txt) if t) or f"{int(dur // 60)} мин"
        algo = r.get("algorithm") or ""
        if algo:
            summary += f" ({algo})"
        # maneuvers в реальности — только start/finish, пошаговых улиц нет
        steps = tuple(m.get("comment") for m in (r.get("maneuvers") or [])
                      if isinstance(m, dict) and m.get("comment") not in (None, "", "start", "finish"))
        options.append(RouteOption(mode="walk", duration_s=int(dur), distance_m=int(dist),
                                   summary=summary, steps=steps, raw=r))
    return options


def _metro_steps(movements: list) -> tuple[str, ...]:
    out: list[str] = []
    for m in movements:
        if not isinstance(m, dict):
            continue
        wp = m.get("waypoint") or {}
        mtype = m.get("type")
        if mtype == "passage":
            # Реально: routes=null, данные в metro{line_name, ui_direction_suggest,
            # ui_station_count} + platforms{names}; waypoint.name — посадка.
            meta = m.get("metro") or {}
            line = meta.get("line_name") or ""
            station = wp.get("name") or ""
            direction = meta.get("ui_direction_suggest") or ""
            count = meta.get("ui_station_count") or ""
            move = _num(m.get("moving_duration")) or 0
            wait = _num(m.get("waiting_duration")) or 0
            tail = f" ~{int(move // 60)} мин" + \
                (f" + ожидание ~{int(wait // 60)} мин" if wait > 0 else "")
            info = ", ".join(t for t in (direction, count) if t)
            head = f"🚇 {station}" + (f" → {line}" if line else "") + \
                (f" ({info})" if info else "")
            out.append(head + tail)
        elif mtype == "walkway":
            comment = wp.get("comment") or "пешком"
            out.append(f"🚶 {comment}")
        elif mtype == "crossing":
            name = wp.get("name") or ""
            out.append(f"🚶 Переход: {name}" if name else "🚶 Переход")
    return tuple(out)


def _metro_walk_ends(movements: list) -> tuple[int, int]:
    """Пешком до первого проезда и после последнего (секунды)."""
    before, after = 0, 0
    seen_passage = False
    for m in movements:
        if not isinstance(m, dict):
            continue
        if m.get("type") == "passage":
            seen_passage = True
            continue
        if m.get("type") == "walkway":
            d = int(_num(m.get("moving_duration")) or 0)
            if seen_passage:
                after += d
            else:
                before += d
    return before, after


def moving_seconds(opt: "RouteOption") -> int:
    """Время в пути БЕЗ ожиданий посадок: Σ moving_duration всех участков.

    Нужно для ночного расчёта (01:00–05:30): waiting ночью бессмысленно
    (метро закрыто / данные гнилые), а езда и пешие куски — реальные.
    Нет raw movements — возвращаем полный duration (как есть, без выдумок)."""
    raw = opt.raw or {}
    movements = raw.get("movements")
    if not isinstance(movements, list) or not movements:
        return opt.duration_s
    total = 0
    for m in movements:
        if isinstance(m, dict):
            total += int(_num(m.get("moving_duration")) or 0)
    return total or opt.duration_s


def parse_metro_payload(payload: Any, max_wait_s: float | None = MAX_WAIT_S) -> list[RouteOption]:
    """Сырой JSON -> варианты на метро (поля из METRO_FIELDS).

    Эндпоинт может вернуть пешеходный вариант (pedestrian: true) или пустоту —
    такие отсеиваем; если метро-вариантов не осталось — пустой список
    (вызывающий код кидает NoMetroError).

    Отдельно отбрасываем варианты с безумным ожиданием посадки
    (waiting_duration > max_wait_s, по умолчанию MAX_WAIT_S): это битые данные
    API, а не реальное расписание — их суммирование давало "выйти в 04:42".
    max_wait_s=None — без фильтра (ночной расчёт: waiting игнорируется,
    берётся только moving-сумма)."""

    def _wait_ok(movements: list) -> bool:
        if max_wait_s is None:
            return True
        for m in movements:
            if isinstance(m, dict) and m.get("type") == "passage":
                w = _num(m.get("waiting_duration")) or 0
                if w > max_wait_s:
                    return False
        return True

    items = payload if isinstance(payload, list) else (payload.get("result") or [])
    if not isinstance(items, list):
        return []
    options: list[RouteOption] = []
    dropped_wait = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        if it.get("pedestrian"):
            continue  # только пешком, не метро
        tt = it.get("transport_types") or []
        if tt and "metro" not in tt:
            continue
        dur = _num(it.get("total_duration"))
        if dur is None:
            continue
        movements = it.get("movements") or []
        if not _wait_ok(movements):
            dropped_wait += 1
            continue
        before, after = _metro_walk_ends(movements)
        transfers = int(_num(it.get("transfer_count")) or 0)
        dist = int(_num(it.get("total_distance")) or 0)
        walk_txt = it.get("total_walkway_distance") or ""
        ch = "пересадка" if transfers == 1 else ("пересадки" if 2 <= transfers <= 4 else "пересадок")
        summary = f"{int(dur // 60)} мин, {transfers} {ch}" + \
            (f", {walk_txt}" if walk_txt else "")
        options.append(RouteOption(mode="metro", duration_s=int(dur), distance_m=dist,
                                   transfers=transfers, walk_before_s=before,
                                   walk_after_s=after, summary=summary,
                                   steps=_metro_steps(movements), raw=it))
    if dropped_wait and max_wait_s is not None:
        log.warning("2GIS metro: отброшено %d вариантов с ожиданием > %dс",
                    dropped_wait, max_wait_s)
    return options


def cache_key(fr: tuple[float, float], to: tuple[float, float], kind: str) -> tuple:
    return (round(fr[0], 4), round(fr[1], 4), round(to[0], 4), round(to[1], 4), kind)


class TwoGisRouting:
    """Клиент 2GIS с retry и TTL-кэшем. use_cache=False — свежий запрос (избранное).

    Лимиты: на демо-периоде 50 RPS. Наш флоу — 1–3 последовательных POST на
    расчёт, но при всплеске параллельных пользователей держим клиентский
    guard min_interval (по умолчанию 0.05 с -> не более ~20 RPS на инстанс),
    плюс retry 429/5xx с backoff в _post."""

    def __init__(self, api_key: str = "", http: httpx.AsyncClient | None = None,
                 walk_ttl_s: int = WALK_TTL_S, metro_ttl_s: int = METRO_TTL_S,
                 min_interval_s: float = 0.05):
        self.key = api_key or os.environ.get("GIS_API_KEY", "")
        self._http = http or httpx.AsyncClient(timeout=15.0)
        self._cache: dict[tuple, tuple[float, list[RouteOption]]] = {}
        self.walk_ttl = walk_ttl_s
        self.metro_ttl = metro_ttl_s
        self._rl_lock = asyncio.Lock()
        self._rl_last = 0.0
        self._rl_min_interval = min_interval_s

    async def _rate_limit(self) -> None:
        async with self._rl_lock:
            now = time.monotonic()
            wait = self._rl_min_interval - (now - self._rl_last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._rl_last = time.monotonic()

    def drop(self) -> None:
        self._cache.clear()

    def _cached(self, key: tuple, ttl: int) -> list[RouteOption] | None:
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        return None

    async def _post(self, url: str, body: dict) -> Any:
        if not self.key:
            raise RoutingError("GIS_API_KEY не задан (env). Маршрут посчитать не могу.")
        params = {"key": self.key}
        last: Exception | None = None
        for attempt in (0, 1, 2):  # retry сетевых + 429/5xx с backoff
            try:
                await self._rate_limit()
                r = await self._http.post(url, params=params, json=body)
                if r.status_code == 429 or 500 <= r.status_code < 600:
                    last = RoutingError(f"2GIS {r.status_code}, retry")
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                try:
                    r.raise_for_status()
                except httpx.HTTPStatusError as e:
                    raise RoutingError(f"2GIS {r.status_code}: {e}") from e
                data = r.json()
                # walk отдаёт объект {"result": [...]}, метро — список [...] top-level
                if not isinstance(data, (dict, list)):
                    raise RoutingError("2GIS вернул не JSON")
                return data
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last = e
                await asyncio.sleep(0.5 * (attempt + 1))
        raise RoutingError(f"2GIS недоступен после 3 попыток: {last}")

    async def walking(self, fr: tuple[float, float], to: tuple[float, float],
                      use_cache: bool = True) -> list[RouteOption]:
        key = cache_key(fr, to, "walk")
        if use_cache:
            hit = self._cached(key, self.walk_ttl)
            if hit is not None:
                return hit
        (flat, flon), (tlat, tlon) = fr, to
        body = {"points": [{"lon": flon, "lat": flat, "type": "stop"},
                           {"lon": tlon, "lat": tlat, "type": "stop"}],
                "transport": "pedestrian", "output": "detailed", "locale": "ru"}
        payload = await self._post(WALK_URL, body)
        options = parse_walk_payload(payload)
        if not options:
            raise RoutingError("2GIS не вернул пешеходных маршрутов (пустой ответ).")
        self._cache[key] = (time.monotonic(), options)
        return options

    async def metro(self, fr: tuple[float, float], to: tuple[float, float],
                    use_cache: bool = True,
                    max_wait_s: float | None = MAX_WAIT_S) -> list[RouteOption]:
        """max_wait_s=None: без фильтра ожиданий и без кэша (ночной расчёт —
        сырые опции нельзя класть в дневной кэш)."""
        key = cache_key(fr, to, "metro")
        if use_cache and max_wait_s is not None:
            hit = self._cached(key, self.metro_ttl)
            if hit is not None:
                return hit
        (flat, flon), (tlat, tlon) = fr, to
        body = {"source": {"point": {"lat": flat, "lon": flon}},
                "target": {"point": {"lat": tlat, "lon": tlon}},
                "transport": ["metro"], "locale": "ru"}
        payload = await self._post(METRO_URL, body)
        options = parse_metro_payload(payload, max_wait_s=max_wait_s)
        if not options:
            raise NoMetroError("Маршрута на метро нет (пустой ответ).")
        if max_wait_s is not None:
            self._cache[key] = (time.monotonic(), options)
        return options

    async def close(self) -> None:
        try:
            await self._http.aclose()
        except Exception:
            pass


# --- Модульные функции из ТЗ (общий клиент, ключ из env) ---
_shared: TwoGisRouting | None = None


def _client(http: httpx.AsyncClient | None = None, api_key: str | None = None) -> TwoGisRouting:
    global _shared
    if http is not None or api_key is not None:
        return TwoGisRouting(api_key or "", http)
    if _shared is None:
        _shared = TwoGisRouting()
    return _shared


async def get_walking_route(from_coords: tuple[float, float], to_coords: tuple[float, float],
                            **kw: Any) -> list[RouteOption]:
    """Список пеших маршрутов 2GIS. kw: http, api_key, use_cache (для тестов/свежих запросов)."""
    c = _client(kw.get("http"), kw.get("api_key"))
    return await c.walking(from_coords, to_coords, use_cache=kw.get("use_cache", True))


async def get_metro_route(from_coords: tuple[float, float], to_coords: tuple[float, float],
                          **kw: Any) -> list[RouteOption]:
    """Список маршрутов на метро 2GIS. Пусто/только пешком -> NoMetroError."""
    c = _client(kw.get("http"), kw.get("api_key"))
    return await c.metro(from_coords, to_coords, use_cache=kw.get("use_cache", True))
