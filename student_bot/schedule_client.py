"""Client for stankinapp.ru schedule API (verified 2026-09-23).

GET {base}/api/groups -> {"items": ["ИДБ-26-14", ...]} (plain strings)
GET {base}/api/schedule?groupName=ИДБ-26-14&startDate=YYYY-MM-DD&endDate=YYYY-MM-DD
    -> {"items": [{"id","date":"2026-09-21","startTime":"08:30","endTime":"11:50",
        "groupName","subject","teacher","type","subgroup","cabinet",
        "slotNumber","pairs":null|[{startTime,endTime}]}]}
Param names remain configurable via Settings.
Normalization lives in normalize.py.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

import httpx

from .config import Settings


class ScheduleApiError(RuntimeError):
    pass


async def _get_with_retry(client: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
    """Retry transport-level failures (flaky TLS/timeouts, verified on
    stankinapp.ru); HTTP error statuses are NOT retried."""
    last: Exception | None = None
    for attempt in (0, 1, 2):
        try:
            return await client.get(url, **kw)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last = e
            await asyncio.sleep(1.0 * (attempt + 1))
    raise ScheduleApiError(f"request failed after retries: {last}")


class ScheduleClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        self.s = settings
        self._http = http or httpx.AsyncClient(timeout=15.0)
        # In-memory TTL cache for day/range JSON: the scheduler loop asks for
        # today's schedule every 60 s per user and every menu press refetches —
        # a fixed day's timetable effectively never changes within minutes.
        self._day: dict[tuple[str, str, str], tuple[float, object]] = {}
        self._day_ttl = getattr(settings, "schedule_day_ttl_s", 900)

    def _url(self, path: str) -> str:
        if not self.s.schedule_api_base:
            raise ScheduleApiError("SCHEDULE_API_BASE is not configured")
        return f"{self.s.schedule_api_base}{path}"

    def _cache_save(self, payload: object) -> None:
        try:
            if self.s.groups_cache_file:
                os.makedirs(os.path.dirname(self.s.groups_cache_file) or ".", exist_ok=True)
                with open(self.s.groups_cache_file, "w", encoding="utf-8") as f:
                    json.dump({"at": time.time(), "data": payload}, f, ensure_ascii=False)
        except Exception:
            pass  # cache is best-effort, never break the request

    def _cache_load(self, max_age_h: float | None = None) -> object | None:
        try:
            with open(self.s.groups_cache_file, encoding="utf-8") as f:
                wrap = json.load(f)
            if max_age_h is not None and time.time() - wrap.get("at", 0) > max_age_h * 3600:
                return None
            return wrap.get("data")
        except Exception:
            return None

    async def get_groups_raw(self) -> object:
        """Cascade: primary API -> GROUPS_JSON_URL fallback -> local cache.
        Any shape is returned raw; normalize_groups() handles list/dict/str."""
        errors: list[str] = []
        try:
            r = await _get_with_retry(self._http, self._url(self.s.schedule_groups_path))
            r.raise_for_status()
            payload = r.json()
            self._cache_save(payload)
            return payload
        except Exception as e:
            errors.append(f"primary: {e}")
        if self.s.groups_fallback_url:
            try:
                r = await _get_with_retry(self._http, self.s.groups_fallback_url)
                r.raise_for_status()
                try:
                    payload = r.json()
                except Exception:
                    payload = [x.strip() for x in r.text.split() if x.strip()]
                self._cache_save(payload)
                return payload
            except Exception as e:
                errors.append(f"fallback: {e}")
        cached = self._cache_load()  # any age: stale list beats no list
        if cached is not None:
            return cached
        raise ScheduleApiError("GET groups failed: " + "; ".join(errors))

    async def get_day_raw(self, group: str, day_iso: str, fresh: bool = False) -> object:
        """One day as a range query startDate=endDate=day_iso (stankinapp has no single-day endpoint).
        fresh=True: мимо in-memory кэша (iOS API перечитывает перед отдачей)."""
        return await self.get_range_raw(group, day_iso, day_iso, fresh=fresh)

    async def get_range_raw(self, group: str, start_iso: str, end_iso: str,
                            fresh: bool = False) -> object:
        key = (group, start_iso, end_iso)
        if not fresh:
            hit = self._day.get(key)
            if hit and time.monotonic() - hit[0] < self._day_ttl:
                return hit[1]
        try:
            r = await _get_with_retry(
                self._http,
                self._url(self.s.schedule_path),
                params={
                    self.s.schedule_group_param: group,
                    self.s.schedule_start_param: start_iso,
                    self.s.schedule_end_param: end_iso,
                },
                headers={"Referer": "https://stankinapp.ru/", "Accept": "application/json"},
            )
            if r.status_code == 204 or not (r.text or "").strip():
                return {"items": []}  # empty day (verified 2026-09-27: 204, no body)
            r.raise_for_status()
            payload = r.json()
            self._day[key] = (time.monotonic(), payload)
            return payload
        except ScheduleApiError:
            raise
        except Exception as e:
            raise ScheduleApiError(f"GET schedule failed: {e}") from e

    async def close(self) -> None:
        await self._http.aclose()
