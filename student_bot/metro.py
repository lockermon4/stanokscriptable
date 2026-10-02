"""Static Moscow-metro travel-time estimator (no train schedules, no realtime).

  total = walk(home -> station A) + metro(A -> B) + walk(B -> building)
  metro(A -> B) = BOARD_WAIT + stops * PER_STOP + transfers * TRANSFER
Station topology: data/metro_moscow.json.

Coverage: 17 subway lines, 215 stations. MCC, MCD, monorail and suburban rail
are not modeled — door-to-door via those falls back to plain routing.
"""
from __future__ import annotations

import heapq
import json
import math
from dataclasses import dataclass, field

# Static assumptions (seconds). Leg cost scales with inter-station distance.
# Transfers and boarding waits are separate weights, not folded into legs.
METRO_SPEED_MS = 11.0  # ~40 км/ч средняя участковая скорость
METRO_DWELL_S = 30  # стоянка на станции, включена в каждый перегон
TRANSFER_S = 300  # пересадка между линиями (переход + ожидание)
BOARD_WAIT_S = 150  # среднее ожидание первого поезда (половина интервала)


def haversine_m(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


@dataclass(frozen=True)
class Station:
    id: int
    name: str
    lat: float
    lon: float
    lines: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    labels: dict[str, str] = field(default_factory=dict)  # line -> station name on that line


@dataclass(frozen=True)
class Step:
    station: str  # display name valid for `line`
    line: str


@dataclass(frozen=True)
class Ride:
    from_id: int
    to_id: int
    stops: int  # inter-station legs ridden
    transfers: int
    seconds: int  # incl. BOARD_WAIT once + TRANSFER per transfer
    path: tuple[str, ...] = ()  # hub representative names along the way
    steps: tuple[Step, ...] = ()  # per-line display names, same length as path
    pretty: str = ""  # human-readable with explicit transfer markers


class MetroGraph:
    def __init__(self, stations: list[Station], lines: dict[str, list[int]]):
        self.stations = stations
        self._by_id = {s.id: s for s in stations}
        self.lines = lines
        # adjacency: station -> list[(neighbor, line, leg_seconds)]
        adj: dict[int, list[tuple[int, str, float]]] = {s.id: [] for s in stations}
        for ref, seq in lines.items():
            for a, b in zip(seq, seq[1:]):
                if a in adj and b in adj:
                    sa, sb = self._by_id[a], self._by_id[b]
                    w = haversine_m(sa.lat, sa.lon, sb.lat, sb.lon) / METRO_SPEED_MS + METRO_DWELL_S
                    adj[a].append((b, ref, w))
                    adj[b].append((a, ref, w))
        self._adj = adj

    @classmethod
    def load(cls, path: str) -> "MetroGraph":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        stations = [
            Station(id=s["id"], name=s["name"], lat=s["lat"], lon=s["lon"], lines=tuple(s["lines"]),
                    aliases=tuple(s.get("aliases", [])), labels=dict(s.get("labels", {})))
            for s in data["stations"]
        ]
        return cls(stations, {ref: list(v["stations"]) for ref, v in data["lines"].items()})

    def station(self, sid: int) -> Station:
        return self._by_id[sid]

    def nearest(self, lat: float, lon: float) -> Station:
        return min(self.stations, key=lambda s: haversine_m(lat, lon, s.lat, s.lon))

    def nearest_n(self, lat: float, lon: float, n: int = 3) -> list[Station]:
        return sorted(self.stations, key=lambda s: haversine_m(lat, lon, s.lat, s.lon))[:max(1, n)]

    def _label(self, sid: int, line: str) -> str:
        s = self._by_id[sid]
        return s.labels.get(line, s.name)

    @staticmethod
    def _pretty(steps: tuple[Step, ...]) -> str:
        if not steps:
            return ""
        parts = [f"{steps[0].station} ({steps[0].line})"]
        for prev, cur in zip(steps, steps[1:]):
            if cur.line == prev.line:
                parts.append(f"—({cur.line})→ {cur.station} ({cur.line})")
            else:
                parts.append(f"⟷пересадка {prev.line}→{cur.line}⟷ {cur.station} ({cur.line})")
        return " ".join(parts)

    @staticmethod
    def transfers_of(ride: "Ride") -> list[tuple[str, str, str]]:
        """Explicit transfer points: [(station_label, from_line, to_line)]."""
        out: list[tuple[str, str, str]] = []
        for prev, cur in zip(ride.steps, ride.steps[1:]):
            if cur.line != prev.line:
                out.append((cur.station, prev.line, cur.line))
        return out

    def ride(self, from_id: int, to_id: int) -> Ride:
        """Dijkstra over (station, line) states. Same-station => zero ride."""
        if from_id == to_id:
            name = self._by_id[from_id].name
            ln = (self._by_id[from_id].lines or ("",))[0]
            step = (Step(name, ln),)
            return Ride(from_id, to_id, 0, 0, 0, (name,), step, self._pretty(step))
        start_lines = self._by_id[from_id].lines or ("",)
        # (cost, transfers, stops, tiebreak, station, line, steps)
        import itertools as _it

        _tie = _it.count()
        pq: list[tuple[float, int, int, int, int, str, tuple[Step, ...]]] = []
        for ln in start_lines:
            s0 = (Step(self._label(from_id, ln), ln),)
            heapq.heappush(pq, (float(BOARD_WAIT_S), 0, 0, next(_tie), from_id, ln, s0))
        best: dict[tuple[int, str], float] = {}
        while pq:
            cost, transfers, stops, _, cur, line, steps = heapq.heappop(pq)
            if (cur, line) in best:
                continue
            best[(cur, line)] = cost
            if cur == to_id:
                path = tuple(s.station for s in steps)
                return Ride(from_id, to_id, stops, transfers, int(cost), path, steps,
                            self._pretty(steps))
            for nb, ln, w in self._adj.get(cur, []):
                if ln == line:
                    ns = steps + (Step(self._label(nb, ln), ln),)
                    heapq.heappush(pq, (cost + w, transfers, stops + 1, next(_tie), nb, ln, ns))
                else:
                    ns = steps + (Step(self._label(cur, ln), ln),)
                    heapq.heappush(pq, (cost + TRANSFER_S, transfers + 1, stops, next(_tie), cur, ln, ns))
        raise ValueError(f"no metro path {from_id}->{to_id}")
