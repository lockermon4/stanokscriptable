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
    database_path: str = "bot_data.sqlite3"
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
            database_path=_get("DATABASE_PATH", "bot_data.sqlite3"),
            default_buffer_min=int(_get("DEFAULT_BUFFER_MIN", "10") or 10),
            default_evening_time=_get("DEFAULT_EVENING_TIME", "21:00"),
            default_morning_minutes_before_exit=int(_get("DEFAULT_MORNING_MIN_BEFORE_EXIT", "60") or 60),
        )
