"""Маршруты через 2GIS Routing API. Только пешком и метро, машины нет.

Координаты внутри — (lat, lon); в тела запросов конвертируем сами.

    async def get_walking_route(from_coords, to_coords) -> список маршрутов
    async def get_metro_route(from_coords, to_coords) -> список маршрутов

Ключ: $GIS_API_KEY. Кэш in-memory: walk TTL 1 ч (пеший от пробок не зависит),
metro TTL 15 мин. Ключ кэша: округление до 4 знаков + тип.

ВАЖНО ПРО ПАРСИНГ: точные названия полей ответа берём ТОЛЬКО из документации
2GIS (пользователь пришлёт JSON-схему отдельно). Названия полей изолированы в
WALK_FIELDS / METRO_FIELDS ниже — сейчас там пометки TODO(docs), парсеры до их
заполнения кидают SchemaNotDocumented с перечислением нужного. Ничего не выдумано.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

WALK_URL = "https://routing.api.2gis.com/routing/7.0.0/global"
METRO_URL = "https://routing.api.2gis.com/public_transport/2.0"

WALK_TTL_S = 3600
METRO_TTL_S = 900


class RoutingError(RuntimeError):
    pass


class NoMetroError(RoutingError):
    """Метро между точками не прокладывается (пусто или только пешком)."""


class SchemaNotDocumented(RoutingError):
    """Парсер ждёт JSON-схему из документации — поля не выдуманы."""


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
# Маппинг полей ответа — ЗАПОЛНИТЬ ПО ДОКУМЕНТАЦИИ (сейчас заглушки).
# Формат: (назначение, json-путь в ответе). Парсеры ниже упадут с понятной
# ошибкой, пока хотя бы один путь не задан.
# ---------------------------------------------------------------------------
WALK_FIELDS: dict[str, str] = {
    # "routes": TODO(docs),      # список маршрутов
    # "duration": TODO(docs),    # секунды в пути
    # "distance": TODO(docs),    # метры
    # "steps": TODO(docs),       # детализация по шагам (улицы)
}
METRO_FIELDS: dict[str, str] = {
    # "routes": TODO(docs),        # список вариантов
    # "duration": TODO(docs),      # секунды всего
    # "transfers": TODO(docs),     # число пересадок
    # "walk_before": TODO(docs),   # пешком до метро, секунды
    # "walk_after": TODO(docs),    # пешком после метро, секунды
    # "legs": TODO(docs),          # участки (станции/ветки) для steps
}


def _need(schema: str, fields: dict[str, str]) -> None:
    missing = [k for k, v in fields.items() if v.startswith("TODO")]
    if missing or not fields:
        raise SchemaNotDocumented(
            f"{schema}: нет JSON-схемы, нужны пути: "
            f"{sorted(set(list(fields) + ['routes', 'duration']))}. "
            f"Пришлите документацию — заполню без выдумок.")


def parse_walk_payload(payload: dict) -> list[RouteOption]:
    """Сырой JSON -> варианты пешком. Ждёт WALK_FIELDS из документации."""
    _need("walk", WALK_FIELDS)
    raise SchemaNotDocumented("walk: WALK_FIELDS не заполнены")  # сменится реализацией по докам


def parse_metro_payload(payload: dict) -> list[RouteOption]:
    """Сырой JSON -> варианты на метро. Ждёт METRO_FIELDS из документации.

    Отдельно: если эндпоинт вернул пустоту или только пешеходный вариант
    (без участков метро) — кидать NoMetroError, см. get_metro_route()."""
    _need("metro", METRO_FIELDS)
    raise SchemaNotDocumented("metro: METRO_FIELDS не заполнены")  # сменится реализацией по докам


def cache_key(fr: tuple[float, float], to: tuple[float, float], kind: str) -> tuple:
    return (round(fr[0], 4), round(fr[1], 4), round(to[0], 4), round(to[1], 4), kind)


class TwoGisRouting:
    """Клиент 2GIS с retry и TTL-кэшем. use_cache=False — свежий запрос (избранное)."""

    def __init__(self, api_key: str = "", http: httpx.AsyncClient | None = None,
                 walk_ttl_s: int = WALK_TTL_S, metro_ttl_s: int = METRO_TTL_S):
        self.key = api_key or os.environ.get("GIS_API_KEY", "")
        self._http = http or httpx.AsyncClient(timeout=15.0)
        self._cache: dict[tuple, tuple[float, list[RouteOption]]] = {}
        self.walk_ttl = walk_ttl_s
        self.metro_ttl = metro_ttl_s

    def drop(self) -> None:
        self._cache.clear()

    def _cached(self, key: tuple, ttl: int) -> list[RouteOption] | None:
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        return None

    async def _post(self, url: str, body: dict) -> dict:
        if not self.key:
            raise RoutingError("GIS_API_KEY не задан (env). Маршрут посчитать не могу.")
        params = {"key": self.key}
        last: Exception | None = None
        for attempt in (0, 1, 2):  # retry сетевых + 429/5xx с backoff
            try:
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
                if not isinstance(data, dict):
                    raise RoutingError("2GIS вернул не JSON-объект")
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
                    use_cache: bool = True) -> list[RouteOption]:
        key = cache_key(fr, to, "metro")
        if use_cache:
            hit = self._cached(key, self.metro_ttl)
            if hit is not None:
                return hit
        (flat, flon), (tlat, tlon) = fr, to
        body = {"source": {"point": {"lat": flat, "lon": flon}},
                "target": {"point": {"lat": tlat, "lon": tlon}},
                "transport": ["metro"], "locale": "ru"}
        payload = await self._post(METRO_URL, body)
        options = parse_metro_payload(payload)  # внутри: пусто/только пешком -> NoMetroError
        if not options:
            raise NoMetroError("Маршрута на метро нет (пустой ответ).")
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
