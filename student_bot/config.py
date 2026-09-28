"""Central configuration from environment (.env file supported). No secrets in code/logs."""
from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()  # reads .env in the working directory if present
except ImportError:
    pass


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default)


# BOT_TOKEN — без него polling невозможен в принципе.
# PUBLIC_BASE_URL — без него ссылка «🔗 Ключ для iOS» соберётся битой
# (заглушка вместо домена), поэтому тоже фатальный.
# GIS_API_KEY — только предупреждение: без ключа дорога честно отдаёт
# route_failed, остальное (расписание, заметки, уведомления) работает.
REQUIRED_ENV_VARS = ("BOT_TOKEN", "PUBLIC_BASE_URL")
RECOMMENDED_ENV_VARS = ("GIS_API_KEY",)


def missing_env_vars() -> tuple[list[str], list[str]]:
    """(отсутствующие обязательные, отсутствующие рекомендуемые).
    Читает os.environ (туда уже подмешан .env через load_dotenv выше) —
    чистáя функция, тестируется без бота."""
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v, "").strip()]
    recommended = [v for v in RECOMMENDED_ENV_VARS if not os.environ.get(v, "").strip()]
    return missing, recommended


@dataclass(frozen=True)
class Settings:
    bot_token: str = ""
    schedule_api_base: str = "https://stankinapp.ru"
    schedule_groups_path: str = "/api/groups"
    schedule_path: str = "/api/schedule"
    # Query param names — configurable because real API shape is unknown.
    schedule_group_param: str = "groupName"
    schedule_start_param: str = "startDate"
    schedule_end_param: str = "endDate"
    schedule_date_format: str = "%Y-%m-%d"
    institution_tz: str = "Europe/Moscow"
    osrm_base: str = "https://router.project-osrm.org"
    foot_base: str = "https://routing.openstreetmap.de"
    nominatim_base: str = "https://nominatim.openstreetmap.org"
    nominatim_user_agent: str = "student-schedule-bot/0.1 (contact: admin@example.com)"
    buildings_file: str = "buildings.yaml"
    metro_data_file: str = "data/metro_moscow.json"
    groups_fallback_url: str = ""  # e.g. static JSON with the full group list
    groups_cache_file: str = "data/groups_cache.json"
    groups_cache_ttl_h: int = 168  # 7 days
    schedule_day_ttl_s: int = 900  # in-memory TTL for day/range schedule JSON (15 min)
    window_min_gap_min: int = 45  # разрыв между парами, считающийся "окном"
    database_path: str = "bot_data.sqlite3"
    database_url: str = ""  # postgres (Supabase): если задан — используется вместо sqlite-файла
    gis_api_key: str = ""  # 2GIS Routing API ($GIS_API_KEY)
    weatherapi_key: str = ""  # WeatherAPI.com ($WEATHERAPI_KEY); пусто — погода пропускается
    public_base_url: str = ""  # https://<сервис>.onrender.com — для ссылки iOS-ключа
    default_buffer_min: int = 10
    default_evening_time: str = "21:00"
    default_morning_minutes_before_exit: int = 60

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            bot_token=_get("BOT_TOKEN"),
            schedule_api_base=_get("SCHEDULE_API_BASE", "https://stankinapp.ru").rstrip("/"),
            schedule_groups_path=_get("SCHEDULE_GROUPS_PATH", "/api/groups"),
            schedule_path=_get("SCHEDULE_PATH", "/api/schedule"),
            schedule_group_param=_get("SCHEDULE_GROUP_PARAM", "groupName"),
            schedule_start_param=_get("SCHEDULE_START_PARAM", "startDate"),
            schedule_end_param=_get("SCHEDULE_END_PARAM", "endDate"),
            schedule_date_format=_get("SCHEDULE_DATE_FORMAT", "%Y-%m-%d"),
            institution_tz=_get("INSTITUTION_TZ", "Europe/Moscow"),
            osrm_base=_get("OSRM_BASE", "https://router.project-osrm.org").rstrip("/"),
            foot_base=_get("FOOT_BASE", "https://routing.openstreetmap.de").rstrip("/"),
            nominatim_base=_get("NOMINATIM_BASE", "https://nominatim.openstreetmap.org").rstrip("/"),
            nominatim_user_agent=_get("NOMINATIM_USER_AGENT", "student-schedule-bot/0.1 (contact: admin@example.com)"),
            buildings_file=_get("BUILDINGS_FILE", "buildings.yaml"),
            metro_data_file=_get("METRO_DATA_FILE", "data/metro_moscow.json"),
            groups_fallback_url=_get("GROUPS_JSON_URL", ""),
            groups_cache_file=_get("GROUPS_CACHE_FILE", "data/groups_cache.json"),
            groups_cache_ttl_h=int(_get("GROUPS_CACHE_TTL_H", "168") or 168),
            schedule_day_ttl_s=int(_get("SCHEDULE_DAY_TTL_S", "900") or 900),
            window_min_gap_min=int(_get("WINDOW_MIN_GAP_MIN", "45") or 45),
            database_path=_get("DATABASE_PATH", "bot_data.sqlite3"),
            database_url=_get("DATABASE_URL", ""),
            gis_api_key=_get("GIS_API_KEY"),
            weatherapi_key=_get("WEATHERAPI_KEY"),
            public_base_url=_get("PUBLIC_BASE_URL", "").rstrip("/"),
            default_buffer_min=int(_get("DEFAULT_BUFFER_MIN", "10") or 10),
            default_evening_time=_get("DEFAULT_EVENING_TIME", "21:00"),
            default_morning_minutes_before_exit=int(_get("DEFAULT_MORNING_MIN_BEFORE_EXIT", "60") or 60),
        )
