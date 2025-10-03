"""Shared utilities for the Discord bot."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

ERROR_LOG_FILE = Path("error_log.txt")


async def notify_admin(bot: commands.Bot, message: str, *, error_log: Path = ERROR_LOG_FILE) -> None:
    """Send a diagnostic message to the configured admin and persist it locally."""
    admin_id: Optional[int] = getattr(getattr(bot, "settings", None), "admin_user_id", None)
    admin: Optional[discord.User] = None

    if admin_id is not None:
        admin = bot.get_user(admin_id)
        if admin is None:
            try:
                admin = await bot.fetch_user(admin_id)
            except Exception:
                admin = None

    if admin is not None:
        try:
            await admin.send(f"⚠️ **Bot Error:**\n```\n{message}\n```")
        except Exception:
            pass

    try:
        error_log.parent.mkdir(parents=True, exist_ok=True)
        with error_log.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now()} - {message}\n")
    except Exception:
        pass
