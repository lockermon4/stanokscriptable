"""Normalization of schedule JSON.

Primary: real stankinapp.ru shape (verified 2026-09-23, see schedule_client.py).
Fallback: tolerant generic parser for other shapes (never invents values).
If a lesson lacks start time or subject it is skipped (count returned honestly).
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from .models import DaySchedule, Group, Lesson

_GROUP_KEYS = ("id", "group_id", "code", "name", "title", "group", "label")
_SUBJECT_KEYS = ("subject", "title", "name", "discipline", "lesson", "pair", "text")
_START_KEYS = ("starts_at", "start", "start_time", "time_start", "begin", "from", "datetime", "date_start")
_END_KEYS = ("ends_at", "end", "end_time", "time_end", "to", "datetime_end", "date_end")
_BUILDING_KEYS = ("building", "corpus", "corp", "campus", "korpus", "building_code", "housing")
_ROOM_KEYS = ("room", "auditorium", "audience", "cabinet", "classroom")
_KIND_KEYS = ("kind", "type", "form", "lesson_type")
_STATUS_KEYS = ("status", "state", "condition")
_TIME_KEYS = ("time", "period", "slot")  # e.g. "09:00-10:30"


def _pick(d: dict, keys: tuple[str, ...]) -> object | None:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    # case-insensitive fallback
    low = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        if k.lower() in low and low[k.lower()] not in (None, ""):
            return low[k.lower()]
    return None


def normalize_groups(payload: object) -> list[Group]:
    """Accept: list[str] | list[dict] | {"groups": [...]} | {"data": [...]}."""
    if isinstance(payload, dict):
        for wrapper in ("groups", "data", "items", "result"):
            if isinstance(payload.get(wrapper), list):
                payload = payload[wrapper]
                break
    if not isinstance(payload, list):
        return []
    out: list[Group] = []
    for item in payload:
        if isinstance(item, str):
            out.append(Group(id=item, name=item))
        elif isinstance(item, dict):
            gid = _pick(item, _GROUP_KEYS)
            name = _pick(item, ("name", "title", "label", "group", "code", "id", "group_id"))
            sid = str(gid if gid is not None else name if name is not None else "")
            sname = str(name if name is not None else gid if gid is not None else "")
            if sid or sname:
                out.append(Group(id=sid or sname, name=sname or sid))
    return out


def _parse_dt(value: object, day: date, tz: ZoneInfo) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # unix timestamp (sec or ms)
        ts = float(value) / 1000.0 if float(value) > 1e11 else float(value)
        return datetime.fromtimestamp(ts, tz=tz)
    s = str(value).strip()
    if not s:
        return None
    # Try ISO datetime first
    try:
        iso = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt.astimezone(tz)
    except Exception:
        pass
    # Try "HH:MM" (optionally with seconds) -> combine with day
    for fmt in ("%H:%M:%S", "%H:%M", "%H.%M"):
        try:
            from datetime import time as dtime

            t = datetime.strptime(s, fmt).time()
            return datetime(day.year, day.month, day.day, t.hour, t.minute, t.second, tzinfo=tz)
        except Exception:
            continue
    # Try "HH:MM-HH:MM" handled by caller; here fail
    return None


def _split_period(s: str) -> tuple[str, str] | None:
    for sep in ("-", "–", "—", "−"):
        if sep in s:
            a, b = s.split(sep, 1)
            return a.strip(), b.strip()
    return None


def _parse_stankin(raw: dict, *, group: str, day: date, tz: ZoneInfo) -> Lesson | None:
    """Real stankinapp item: date/startTime/endTime/subject/teacher/type/cabinet.

    Returns None if this dict is not in stankin shape (caller falls back to
    generic parsing). building_code stays "" — API has no corpus field,
    cabinet->building resolution lives in buildings.py.
    """
    if "date" not in raw or "startTime" not in raw or "subject" not in raw:
        return None
    try:
        d = date.fromisoformat(str(raw["date"]).strip())
    except Exception:
        return None
    subject = str(raw.get("subject") or "").strip()
    start_s = str(raw.get("startTime") or "").strip()
    end_s = str(raw.get("endTime") or "").strip()
    if not subject or not start_s:
        return None
    starts = _parse_dt(start_s, d, tz)
    if starts is None:
        return None
    ends = _parse_dt(end_s, d, tz) if end_s else None
    cabinet = str(raw.get("cabinet") or "").strip()
    kind = str(raw.get("type") or "").strip()
    teacher = str(raw.get("teacher") or "").strip()
    # keep teacher/subgroup/slot info in kind-suffix-free fields: kind stays type,
    # teacher goes to raw only (Lesson has no teacher field; display uses raw).
    return Lesson(
        group=str(raw.get("groupName") or group),
        day=d,
        starts_at=starts,
        ends_at=ends,
        subject=subject,
        building_code="",
        room=cabinet,
        kind=kind,
        status="scheduled",  # API exposes no cancellation flag; absent = scheduled
        raw={"teacher": teacher, **raw},
    )


def normalize_day(payload: object, *, group: str, day: date, tz_name: str) -> tuple[DaySchedule, int]:
    """Return (DaySchedule, skipped_count). Cancelled lessons are kept with status."""
    tz = ZoneInfo(tz_name)
    items: list = []
    if isinstance(payload, dict):
        for wrapper in ("schedule", "lessons", "pairs", "data", "items", "result"):
            if isinstance(payload.get(wrapper), list):
                items = payload[wrapper]  # type: ignore
                break
        else:
            # maybe {"2024-01-01": [...]} mapping
            iso = day.isoformat()
            if isinstance(payload.get(iso), list):
                items = payload[iso]  # type: ignore
    elif isinstance(payload, list):
        items = payload

    lessons: list[Lesson] = []
    skipped = 0
    for raw in items:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        parsed = _parse_stankin(raw, group=group, day=day, tz=tz)
        if parsed is not None:
            if parsed.day != day:
                continue  # range response: keep only requested day
            lessons.append(parsed)
            continue
        subject = _pick(raw, _SUBJECT_KEYS)
        start_raw = _pick(raw, _START_KEYS)
        end_raw = _pick(raw, _END_KEYS)
        # "time": "09:00-10:30" fallback
        if start_raw is None:
            t = _pick(raw, _TIME_KEYS)
            if isinstance(t, str) and t:
                sp = _split_period(t)
                if sp:
                    start_raw, end_raw = sp[0], end_raw or sp[1]
                else:
                    start_raw = t
        starts = _parse_dt(start_raw, day, tz) if start_raw is not None else None
        if starts is None or subject is None or not str(subject).strip():
            skipped += 1
            continue
        ends = _parse_dt(end_raw, day, tz) if end_raw is not None else None
        building = _pick(raw, _BUILDING_KEYS)
        room = _pick(raw, _ROOM_KEYS)
        kind = _pick(raw, _KIND_KEYS)
        status = _pick(raw, _STATUS_KEYS)
        lessons.append(
            Lesson(
                group=group,
                day=day,
                starts_at=starts,
                ends_at=ends,
                subject=str(subject).strip(),
                building_code=str(building).strip() if building is not None else "",
                room=str(room).strip() if room is not None else "",
                kind=str(kind).strip() if kind is not None else "",
                status=str(status).strip().lower() if status is not None else "scheduled",
                raw=raw,
            )
        )
    lessons.sort(key=lambda l: l.starts_at)
    return DaySchedule(day=day, group=group, lessons=tuple(lessons)), skipped
