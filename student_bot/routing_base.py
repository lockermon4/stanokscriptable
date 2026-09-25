"""Route provider abstraction.

RouteResult.is_approximate=True means: provider does NOT support arrival-time /
live traffic / metro legs, estimate is rough. Bot MUST surface this honestly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class RouteLeg:
    mode: str  # walk/drive/bike/transit/...
    seconds: int
    description: str = ""


@dataclass(frozen=True)
class RouteResult:
    travel_seconds: int
    legs: tuple[RouteLeg, ...] = ()
    is_approximate: bool = True
    calculated_at: datetime | None = None
    provider: str = "unknown"


class RouteProvider(Protocol):
    supports_transit: bool
    supports_arrival_time: bool
    supports_live_traffic: bool

    async def route(
        self,
        from_lonlat: tuple[float, float],
        to_lonlat: tuple[float, float],
        mode: str,
        arrive_by: datetime | None = None,
    ) -> RouteResult: ...
