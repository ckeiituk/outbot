"""Bot factory and base class definition."""

from __future__ import annotations

from dataclasses import dataclass
import traceback

import discord
from discord.ext import commands

from config import (
    ADMIN_USER_ID,
    BOOST_REPORT_CHANNEL_ID,
    GUILD_ID,
    INVITE_CODE_FOR_BOT_BOOSTER,
    ROLE_BOT_BOOSTER,
    ROLE_MOVIES,
    ROLE_SERVER_BOOSTER,
    MODERATOR_ROLE,
    TRACK_USER_ID,
    GOOGLE_SHEET_URL,
)


@dataclass(frozen=True)
class BotSettings:
    admin_user_id: int
    track_user_id: int
    guild_id: int
    boost_report_channel_id: int
    invite_code_for_bot_booster: str
    role_bot_booster: str
    role_server_booster: str
    role_movies: str
    moderator_role: str
    google_sheet_url: str


class OutBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.guilds = True
        intents.message_content = True
        intents.voice_states = True
        intents.presences = True

        super().__init__(command_prefix="!", intents=intents)

        self.settings = BotSettings(
            admin_user_id=ADMIN_USER_ID,
            track_user_id=TRACK_USER_ID,
            guild_id=GUILD_ID,
            boost_report_channel_id=BOOST_REPORT_CHANNEL_ID,
            invite_code_for_bot_booster=INVITE_CODE_FOR_BOT_BOOSTER,
            role_bot_booster=ROLE_BOT_BOOSTER,
            role_server_booster=ROLE_SERVER_BOOSTER,
            role_movies=ROLE_MOVIES,
            moderator_role=MODERATOR_ROLE,
            google_sheet_url=GOOGLE_SHEET_URL,
        )

    async def setup_hook(self) -> None:
        from .cogs.boosters import BoostersCog
        from .cogs.dm_relay import DmRelayCog
        from .cogs.voice import VoiceCog
        from .cogs.tracking import TrackingCog
        from .cogs.misc import MiscCog
        from .cogs.target_game import TargetGameCog
        from .cogs.error_handlers import ErrorHandlerCog

        await self.add_cog(MiscCog(self))
        await self.add_cog(BoostersCog(self))
        await self.add_cog(VoiceCog(self))
        await self.add_cog(TrackingCog(self))
        await self.add_cog(DmRelayCog(self))
        await self.add_cog(TargetGameCog(self))
        await self.add_cog(ErrorHandlerCog(self))

        try:
            guild_object = discord.Object(id=self.settings.guild_id)
            synced = await self.tree.sync(guild=guild_object)
            print(f"Synced {len(synced)} application commands for guild {self.settings.guild_id}.")
        except Exception as exc:
            from .utils import notify_admin

            await notify_admin(
                self,
                f"Failed to sync app commands: {exc}\n{traceback.format_exc()}",
            )


def create_bot() -> OutBot:
    return OutBot()
