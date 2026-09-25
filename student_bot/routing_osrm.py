"""OSRM provider (MVP default).

Capabilities (verified, see README):
- profiles: driving / foot / bike (worldwide demo server)
- demo server: max ~1 req/s, reasonable non-commercial use, no uptime guarantee
- NO public-transport/metro legs, NO arrival-time param, NO live traffic/jams.
Therefore every result has is_approximate=True, and mode "transit" is served
as a walking estimate with approximate flag (honest fallback, not metro).
Self-host OSRM via OSRM_BASE for production.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from .config import Settings
from .routing_base import RouteLeg, RouteResult

_PROFILE = {"driving": "driving", "car": "driving", "foot": "foot", "walking": "foot", "bike": "bike", "cycling": "bike"}


class OsrmProvider:
    supports_transit = False
    supports_arrival_time = False
    supports_live_traffic = False
    name = "osrm"

    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        self.s = settings
        self._http = http or httpx.AsyncClient(timeout=15.0)

    async def route(
        self,
        from_lonlat: tuple[float, float],
        to_lonlat: tuple[float, float],
        mode: str,
        arrive_by: datetime | None = None,  # ignored: OSRM has no arrival-time
    ) -> RouteResult:
        profile = _PROFILE.get(mode, "foot")
        approx = True  # OSRM never gives live/arrival-accurate times
        if mode == "transit":
            profile = "foot"  # honest fallback, flagged approximate
        (flon, flat), (tlon, tlat) = from_lonlat, to_lonlat
        url = f"{self.s.osrm_base}/route/v1/{profile}/{flon},{flat};{tlon},{tlat}"
        try:
            r = await self._http.get(url, params={"overview": "false"})
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            raise RuntimeError(f"OSRM request failed: {e}") from e
        if data.get("code") != "Ok" or not data.get("routes"):
            raise RuntimeError(f"OSRM no route: {data.get('code')}")
        secs = int(float(data["routes"][0]["duration"]))
        leg = RouteLeg(mode=profile if mode != "transit" else "foot (вместо ОТ — приблизительно)", seconds=secs)
        return RouteResult(
            travel_seconds=secs,
            legs=(leg,),
            is_approximate=approx,
            calculated_at=datetime.now(timezone.utc),
            provider=self.name,
        )
