"""Хранение состояния: настройки пользователей, заметки, избранные, api-токены.

SQLite локально, Postgres (Supabase) по строке подключения:
  Store("bot_data.sqlite3")  -> sqlite3
  Store("postgresql://...")  -> psycopg + пул
SQL пишется с плейсхолдерами `?`, для PG переписываются в `%s`.
"""
from __future__ import annotations

import contextlib
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
    """'lat,lon' -> (lat, lon) или None."""
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


class _Q:
    """Тонкая обёртка над коннектом: переписывает `?` в `%s` для PG."""

    def __init__(self, raw, pg: bool):
        self._raw = raw
        self._pg = pg

    def execute(self, sql: str, params: tuple = ()):
        if self._pg:
            sql = sql.replace("?", "%s")
        return self._raw.execute(sql, params)


class Store:
    def __init__(self, path_or_url: str):
        self.path = path_or_url
        self._pg = path_or_url.startswith("postgres://") or \
            path_or_url.startswith("postgresql://")
        self._pool = None
        if self._pg:
            from psycopg_pool import ConnectionPool
            from psycopg.rows import dict_row

            # Пул держит тёплые коннекты (Supabase/pooler),
            # транзакции коммитятся выходом из контекста.
            self._pool = ConnectionPool(
                path_or_url, min_size=1, max_size=4, open=True,
                kwargs={"row_factory": dict_row, "connect_timeout": 10})
        self._init()

    def close(self) -> None:
        if self._pool is not None:
            try:
                self._pool.close()
            except Exception:
                pass
            self._pool = None

    @contextlib.contextmanager
    def _conn(self):
        """Единая точка: `with self._conn() as c: c.execute(sql, params)`.
        SQLite коммитит выходом, PG — средствами пула; `?` -> `%s` для PG."""
        if self._pg:
            assert self._pool is not None
            with self._pool.connection() as conn:
                yield _Q(conn, pg=True)
        else:
            raw = sqlite3.connect(self.path)
            raw.row_factory = sqlite3.Row
            try:
                yield _Q(raw, pg=False)
                raw.commit()
            finally:
                try:
                    raw.close()
                except Exception:
                    pass

    def _init(self) -> None:
        users_ddl = (
            """CREATE TABLE IF NOT EXISTS users(
            user_id BIGINT PRIMARY KEY, sgroup TEXT DEFAULT '',
            home_address TEXT DEFAULT '', home_lat DOUBLE PRECISION,
            home_lon DOUBLE PRECISION, transport TEXT DEFAULT 'metro',
            buffer_min INTEGER DEFAULT 10, evening_time TEXT DEFAULT '21:00',
            morning_min_before_exit INTEGER DEFAULT 60, lang TEXT DEFAULT 'ru')"""
            if self._pg else
            """CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY, sgroup TEXT DEFAULT '',
            home_address TEXT DEFAULT '', transport TEXT DEFAULT 'metro',
            buffer_min INTEGER DEFAULT 10, evening_time TEXT DEFAULT '21:00',
            morning_min_before_exit INTEGER DEFAULT 60)"""
        )
        int_pk = "BIGINT" if self._pg else "INTEGER"
        fav_id = "id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY" \
            if self._pg else "id INTEGER PRIMARY KEY AUTOINCREMENT"
        with self._conn() as c:
            c.execute(users_ddl)
            if not self._pg:
                for col in ("home_lat REAL", "home_lon REAL", "lang TEXT DEFAULT 'ru'"):
                    try:
                        c.execute(f"ALTER TABLE users ADD COLUMN {col}")
                    except Exception:
                        pass  # колонка уже есть
            c.execute(
                f"""CREATE TABLE IF NOT EXISTS notes(
                user_id {int_pk}, day TEXT, text TEXT,
                PRIMARY KEY(user_id, day))"""
            )
            c.execute(
                f"""CREATE TABLE IF NOT EXISTS sent(
                user_id {int_pk}, day TEXT, kind TEXT,
                PRIMARY KEY(user_id, day, kind))"""
            )
            c.execute(
                f"""CREATE TABLE IF NOT EXISTS favorites(
                {fav_id}, user_id {int_pk},
                name TEXT, from_coords TEXT, to_coords TEXT, transport_type TEXT)"""
            )
            c.execute(
                f"""CREATE TABLE IF NOT EXISTS api_tokens(
                user_id {int_pk} PRIMARY KEY, token TEXT, created_at TEXT)"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS route_cache(
                key TEXT PRIMARY KEY, payload TEXT NOT NULL,
                fetched_at {ts})""".replace("{ts}", "DOUBLE PRECISION" if self._pg else "REAL")
            )
            self._init_schedule_tables(c)

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
        sql = ("INSERT INTO sent(user_id,day,kind) VALUES(?,?,?)"
               " ON CONFLICT DO NOTHING") if self._pg else \
            "INSERT OR IGNORE INTO sent(user_id,day,kind) VALUES(?,?,?)"
        with self._conn() as c:
            c.execute(sql, (user_id, day, kind))

    def clear_sent(self, user_id: int, day: str, kind: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM sent WHERE user_id=? AND day=? AND kind=?",
                      (user_id, day, kind))

    # route_cache: кэш маршрутов 2GIS (payload — JSON опций или {"error":…}).
    # TTL-решение принимает routing.py; тут только хранение.
    def get_route_cache(self, key: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT payload, fetched_at FROM route_cache WHERE key=?",
                          (key,)).fetchone()
        return {"payload": r["payload"], "fetched_at": float(r["fetched_at"])} if r else None

    def put_route_cache(self, key: str, payload: str, fetched_at: float) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO route_cache(key, payload, fetched_at) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET payload=excluded.payload,
                fetched_at=excluded.fetched_at""",
                (key, payload, fetched_at))
            # удаление записей старше 7 дней
            c.execute("DELETE FROM route_cache WHERE fetched_at < ?",
                      (fetched_at - 86400 * 7,))

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
        params = (user_id, name.strip() or "Без названия", from_coords, to_coords,
                  norm_transport(transport_type))
        with self._conn() as c:
            if self._pg:
                r = c.execute(
                    "INSERT INTO favorites(user_id,name,from_coords,to_coords,transport_type)"
                    " VALUES(?,?,?,?,?) RETURNING id", params).fetchone()
                return int(r["id"]) if r else 0
            cur = c.execute(
                "INSERT INTO favorites(user_id,name,from_coords,to_coords,transport_type)"
                " VALUES(?,?,?,?,?)", params)
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
    def get_api_token(self, user_id: int) -> str:
        """Возвращает существующий токен или ""; ничего не создаёт."""
        with self._conn() as c:
            r = c.execute("SELECT token FROM api_tokens WHERE user_id=?", (user_id,)).fetchone()
        return r["token"] if r and r["token"] else ""

    def issue_api_token(self, user_id: int) -> str:
        """Выпустить новый токен; предыдущий перестаёт действовать."""
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

    # schedule_snapshots: последний подтверждённый снимок пар (группа, дата).
    # schedule_pending: неподтверждённый дифф (ждёт второго опроса подряд).
    # schedule_changes: последний подтверждённый батч изменений (для iOS API).
    # JSON-списки канонических уроков/изменений; TEXT работает на sqlite и PG.
    def _init_schedule_tables(self, c) -> None:
        c.execute(
            """CREATE TABLE IF NOT EXISTS schedule_snapshots(
            group_name TEXT, day TEXT, lessons_json TEXT, fetched_at TEXT,
            PRIMARY KEY(group_name, day))"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS schedule_pending(
            group_name TEXT, day TEXT, changes_json TEXT, seen_at TEXT,
            PRIMARY KEY(group_name, day))"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS schedule_changes(
            group_name TEXT, day TEXT, changes_json TEXT, notified_at TEXT,
            PRIMARY KEY(group_name, day))"""
        )

    @staticmethod
    def _json_loads(raw: str | None) -> list | None:
        if not raw:
            return None
        try:
            import json

            v = json.loads(raw)
            return v if isinstance(v, list) else None
        except Exception:
            return None

    @staticmethod
    def _json_dumps(items: list) -> str:
        import json

        return json.dumps(items, ensure_ascii=False)

    def get_snapshot(self, group: str, day: str) -> list | None:
        """None — снимка ещё нет."""
        with self._conn() as c:
            r = c.execute("SELECT lessons_json FROM schedule_snapshots WHERE group_name=? AND day=?",
                          (group, day)).fetchone()
        return self._json_loads(r["lessons_json"]) if r else None

    def save_snapshot(self, group: str, day: str, lessons: list) -> None:
        import datetime as _dt

        now = _dt.datetime.now(_dt.timezone.utc).isoformat()
        with self._conn() as c:
            c.execute("INSERT INTO schedule_snapshots(group_name,day,lessons_json,fetched_at)"
                      " VALUES(?,?,?,?) ON CONFLICT(group_name,day) DO UPDATE SET"
                      " lessons_json=excluded.lessons_json, fetched_at=excluded.fetched_at",
                      (group, day, self._json_dumps(lessons), now))

    def get_pending(self, group: str, day: str) -> list | None:
        with self._conn() as c:
            r = c.execute("SELECT changes_json FROM schedule_pending WHERE group_name=? AND day=?",
                          (group, day)).fetchone()
        return self._json_loads(r["changes_json"]) if r else None

    def save_pending(self, group: str, day: str, changes: list) -> None:
        import datetime as _dt

        now = _dt.datetime.now(_dt.timezone.utc).isoformat()
        with self._conn() as c:
            c.execute("INSERT INTO schedule_pending(group_name,day,changes_json,seen_at)"
                      " VALUES(?,?,?,?) ON CONFLICT(group_name,day) DO UPDATE SET"
                      " changes_json=excluded.changes_json, seen_at=excluded.seen_at",
                      (group, day, self._json_dumps(changes), now))

    def clear_pending(self, group: str, day: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM schedule_pending WHERE group_name=? AND day=?", (group, day))

    def save_changes(self, group: str, day: str, changes: list) -> None:
        import datetime as _dt

        now = _dt.datetime.now(_dt.timezone.utc).isoformat()
        with self._conn() as c:
            c.execute("INSERT INTO schedule_changes(group_name,day,changes_json,notified_at)"
                      " VALUES(?,?,?,?) ON CONFLICT(group_name,day) DO UPDATE SET"
                      " changes_json=excluded.changes_json, notified_at=excluded.notified_at",
                      (group, day, self._json_dumps(changes), now))

    def get_changes(self, group: str, day: str) -> list:
        with self._conn() as c:
            r = c.execute("SELECT changes_json FROM schedule_changes WHERE group_name=? AND day=?",
                          (group, day)).fetchone()
        return self._json_loads(r["changes_json"]) if r else []
