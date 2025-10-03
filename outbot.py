"""Entrypoint for running the Discord bot."""

from __future__ import annotations

from bot import create_bot
from config import BOT_TOKEN


def main() -> None:
    bot = create_bot()
    bot.run(BOT_TOKEN)


if __name__ == "__main__":
    main()
