"""SQLite storage for user settings + per-day notes. No addresses in logs."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class Favorite:
    id: int
    user_id: int
    name: str
    from_coords: str  # "lat,lon"
    to_coords: str  # "lat,lon"
    transport_type: str  # "walk" | "metro"


def norm_transport(v: str | None) -> str:
    """Только пешком и метро. Старые значения мигрируют: transit->metro,
    foot/walking->walk, driving/car/bike->walk."""
    t = (v or "").strip().lower()
    if t in ("metro", "transit", "метро", "общественный"):
        return "metro"
    if t in ("walk", "foot", "walking", "пешком"):
        return "walk"
    if t in ("driving", "car", "bike", "cycling", "машина", "вело"):
        return "walk"
    return "metro"


def parse_coords(s: str) -> tuple[float, float] | None:
    """'lat,lon' -> (lat, lon) или None. Чистая функция."""
    try:
        lat_s, lon_s = s.split(",")
        return (float(lat_s), float(lon_s))
    except Exception:
        return None


def fmt_coords(lat: float, lon: float) -> str:
    return f"{lat},{lon}"


@dataclass
class UserSettings:
    user_id: int
    group: str = ""
    home_address: str = ""
    home_lat: float | None = None  # set when user sends a location pin
    home_lon: float | None = None
    transport: str = "metro"
    buffer_min: int = 10
    evening_time: str = "21:00"  # HH:MM institution tz
    morning_min_before_exit: int = 60
    lang: str = "ru"


class Store:
    def __init__(self, path: str):
        self.path = path
        self._init()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def _init(self) -> None:
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS users(
                user_id INTEGER PRIMARY KEY, sgroup TEXT DEFAULT '',
                home_address TEXT DEFAULT '', transport TEXT DEFAULT 'metro',
                buffer_min INTEGER DEFAULT 10, evening_time TEXT DEFAULT '21:00',
                morning_min_before_exit INTEGER DEFAULT 60)"""
            )
            for col in ("home_lat REAL", "home_lon REAL", "lang TEXT DEFAULT 'ru'"):
                try:
                    c.execute(f"ALTER TABLE users ADD COLUMN {col}")
                except Exception:
                    pass  # already migrated
            c.execute(
                """CREATE TABLE IF NOT EXISTS notes(
                user_id INTEGER, day TEXT, text TEXT,
                PRIMARY KEY(user_id, day))"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS sent(
                user_id INTEGER, day TEXT, kind TEXT,
                PRIMARY KEY(user_id, day, kind))"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS favorites(
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                name TEXT, from_coords TEXT, to_coords TEXT, transport_type TEXT)"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS api_tokens(
                user_id INTEGER PRIMARY KEY, token TEXT, created_at TEXT)"""
            )

    def _row_user(self, r) -> UserSettings:
        keys = set(r.keys())
        return UserSettings(
            user_id=r["user_id"],
            group=r["sgroup"] or "",
            home_address=r["home_address"] or "",
            home_lat=r["home_lat"] if "home_lat" in keys else None,
            home_lon=r["home_lon"] if "home_lon" in keys else None,
            transport=norm_transport(r["transport"]) if r["transport"] else "metro",
            buffer_min=int(r["buffer_min"] or 10),
            evening_time=r["evening_time"] or "21:00",
            morning_min_before_exit=int(r["morning_min_before_exit"] or 60),
            lang=(r["lang"] if "lang" in keys and r["lang"] else "ru"),
        )

    def get_user(self, user_id: int) -> UserSettings:
        with self._conn() as c:
            r = c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not r:
            return UserSettings(user_id=user_id)
        return self._row_user(r)

    def save_user(self, u: UserSettings) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO users(user_id,sgroup,home_address,home_lat,home_lon,transport,buffer_min,evening_time,morning_min_before_exit,lang)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                sgroup=excluded.sgroup, home_address=excluded.home_address,
                home_lat=excluded.home_lat, home_lon=excluded.home_lon,
                transport=excluded.transport, buffer_min=excluded.buffer_min,
                evening_time=excluded.evening_time,
                morning_min_before_exit=excluded.morning_min_before_exit,
                lang=excluded.lang""",
                (u.user_id, u.group, u.home_address, u.home_lat, u.home_lon, u.transport, u.buffer_min, u.evening_time, u.morning_min_before_exit, u.lang),
            )

    def all_users(self) -> list[UserSettings]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM users").fetchall()
        return [self._row_user(r) for r in rows]

    def was_sent(self, user_id: int, day: str, kind: str) -> bool:
        with self._conn() as c:
            r = c.execute("SELECT 1 FROM sent WHERE user_id=? AND day=? AND kind=?",
                          (user_id, day, kind)).fetchone()
        return r is not None

    def mark_sent(self, user_id: int, day: str, kind: str) -> None:
        with self._conn() as c:
            c.execute("INSERT OR IGNORE INTO sent(user_id,day,kind) VALUES(?,?,?)",
                      (user_id, day, kind))

    def clear_sent(self, user_id: int, day: str, kind: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM sent WHERE user_id=? AND day=? AND kind=?",
                      (user_id, day, kind))

    # notes: day = YYYY-MM-DD
    def get_note(self, user_id: int, day: str) -> str:
        with self._conn() as c:
            r = c.execute("SELECT text FROM notes WHERE user_id=? AND day=?", (user_id, day)).fetchone()
        return r["text"] if r else ""

    def set_note(self, user_id: int, day: str, text: str) -> None:
        with self._conn() as c:
            if text.strip():
                c.execute("INSERT INTO notes(user_id,day,text) VALUES(?,?,?) ON CONFLICT(user_id,day) DO UPDATE SET text=excluded.text",
                          (user_id, day, text.strip()))
            else:
                c.execute("DELETE FROM notes WHERE user_id=? AND day=?", (user_id, day))

    def delete_note(self, user_id: int, day: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM notes WHERE user_id=? AND day=?", (user_id, day))

    # favorites: from_coords/to_coords = "lat,lon", transport_type = walk|metro
    def add_favorite(self, user_id: int, name: str, from_coords: str,
                     to_coords: str, transport_type: str) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO favorites(user_id,name,from_coords,to_coords,transport_type)"
                " VALUES(?,?,?,?,?)",
                (user_id, name.strip() or "Без названия", from_coords, to_coords,
                 norm_transport(transport_type)))
            return cur.lastrowid or 0

    def list_favorites(self, user_id: int) -> list[Favorite]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id,user_id,name,from_coords,to_coords,transport_type"
                " FROM favorites WHERE user_id=? ORDER BY id", (user_id,)).fetchall()
        return [Favorite(id=r["id"], user_id=r["user_id"], name=r["name"] or "",
                         from_coords=r["from_coords"] or "", to_coords=r["to_coords"] or "",
                         transport_type=norm_transport(r["transport_type"])) for r in rows]

    def delete_favorite(self, user_id: int, fav_id: int) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM favorites WHERE user_id=? AND id=?", (user_id, fav_id))
            return (cur.rowcount or 0) > 0

    # api_tokens: один токен на пользователя для iOS HTTP API (Scriptable).
    # Токен в логи не пишем (см. sendlog: логируются только длины).
    def get_api_token(self, user_id: int) -> str:
        """Существующий токен или "" (не создаёт молча — генерация явная)."""
        with self._conn() as c:
            r = c.execute("SELECT token FROM api_tokens WHERE user_id=?", (user_id,)).fetchone()
        return r["token"] if r and r["token"] else ""

    def issue_api_token(self, user_id: int) -> str:
        """Создать (или ПЕРЕВЫПУСТИТЬ — старый сразу невалиден)."""
        import secrets
        from datetime import datetime, timezone

        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as c:
            c.execute("INSERT INTO api_tokens(user_id,token,created_at) VALUES(?,?,?)"
                      " ON CONFLICT(user_id) DO UPDATE SET token=excluded.token,"
                      " created_at=excluded.created_at", (user_id, token, now))
        return token

    def user_id_by_token(self, token: str) -> int | None:
        if not token:
            return None
        with self._conn() as c:
            r = c.execute("SELECT user_id FROM api_tokens WHERE token=?", (token,)).fetchone()
        return int(r["user_id"]) if r else None
