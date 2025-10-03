"""Runtime configuration for the Discord bot.

Required values are read from environment variables. Copy `.env.example` to
`.env` (ignored by git) or export the variables before starting the bot.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _load_dotenv(path: Path) -> None:
    """Populate os.environ from a simple KEY=VALUE .env file if present."""
    if not path.exists():
        return
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key.startswith("#"):
            continue
        os.environ.setdefault(key, value.strip())


_load_dotenv(Path(".env"))


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    raise RuntimeError(f"Environment variable {name} is required.")


def _int_env(name: str, default: Optional[int] = None) -> int:
    value = os.getenv(name)
    if value is not None and value.strip():
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"Environment variable {name} must be an integer.") from exc
    if default is None:
        raise RuntimeError(f"Environment variable {name} is required.")
    return default


BOT_TOKEN = _require_env("DISCORD_BOT_TOKEN")

ADMIN_USER_ID = _int_env("ADMIN_USER_ID", 233981175956242433)
TRACK_USER_ID = _int_env("TRACK_USER_ID", ADMIN_USER_ID)
GUILD_ID = _int_env("GUILD_ID", 233981443766878208)
BOOST_REPORT_CHANNEL_ID = _int_env("BOOST_REPORT_CHANNEL_ID", 1252628666639450236)

INVITE_CODE_FOR_BOT_BOOSTER = os.getenv("INVITE_CODE_FOR_BOT_BOOSTER", "Q9EesfD7Gs")
ROLE_BOT_BOOSTER = os.getenv("ROLE_BOT_BOOSTER", "Бот Бустер")
ROLE_SERVER_BOOSTER = os.getenv("ROLE_SERVER_BOOSTER", "Server Booster")
ROLE_MOVIES = os.getenv("ROLE_MOVIES", "Кино")
MODERATOR_ROLE = os.getenv("MODERATOR_ROLE", "")
GOOGLE_SHEET_URL = os.getenv(
    "GOOGLE_SHEET_URL",
    "https://docs.google.com/spreadsheets/d/1JjNffnZHc-D8KdnLAdT09LTvoBJUVtX4ao9Wc-NM_6A",
)