"""Presence tracking for a specific user."""

from __future__ import annotations

import traceback
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils import notify_admin
from config import GUILD_ID


class TrackingCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.tracking_enabled: bool = False
        self._last_applied_tracking_status: Optional[discord.Status] = None

    def _is_online_like(self, status: discord.Status) -> bool:
        return status in (discord.Status.online, discord.Status.idle, discord.Status.dnd)

    async def _apply_tracking_by_status(self, user_status: discord.Status, guild: discord.Guild) -> None:
        if not self.tracking_enabled:
            return

        desired = discord.Status.invisible if self._is_online_like(user_status) else discord.Status.idle

        if self._last_applied_tracking_status == desired:
            return

        try:
            await self.bot.change_presence(status=desired)
            self._last_applied_tracking_status = desired
        except Exception:
            await notify_admin(self.bot, f"apply_tracking_by_status failed:\n{traceback.format_exc()}")

    async def _evaluate_tracking_now(self, guild: discord.Guild) -> None:
        try:
            member = guild.get_member(self.bot.settings.track_user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(self.bot.settings.track_user_id)
                except Exception:
                    member = None

            if member is None:
                await notify_admin(
                    self.bot,
                    f"Track: user {self.bot.settings.track_user_id} не найден в гильдии {guild.id}",
                )
                return

            await self._apply_tracking_by_status(member.status, guild)
        except Exception:
            await notify_admin(self.bot, f"evaluate_tracking_now error:\n{traceback.format_exc()}")

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self.tracking_enabled:
            return
        for guild in self.bot.guilds:
            await self._evaluate_tracking_now(guild)

    @commands.Cog.listener()
    async def on_presence_update(self, _before: discord.Member, after: discord.Member) -> None:
        try:
            if not self.tracking_enabled:
                return
            if after.id != self.bot.settings.track_user_id:
                return
            if after.guild:
                await self._apply_tracking_by_status(after.status, after.guild)
        except Exception:
            await notify_admin(self.bot, f"on_presence_update error:\n{traceback.format_exc()}")

    @app_commands.command(
        name="track",
        description="Включить/выключить трекинг статуса пользователя и автосмену присутствия бота",
    )
    @app_commands.describe(mode="Режим: on/off (или не указывать — тогда переключение)")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="on (включить)", value="on"),
            app_commands.Choice(name="off (выключить)", value="off"),
        ]
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def track_cmd(
        self,
        interaction: discord.Interaction,
        mode: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        if interaction.user.id != self.bot.settings.admin_user_id:
            await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
            return

        try:
            if interaction.guild is None:
                await interaction.response.send_message(
                    "Команда доступна только на сервере.", ephemeral=True
                )
                return

            await interaction.response.defer(ephemeral=True, thinking=False)

            if mode is None:
                self.tracking_enabled = not self.tracking_enabled
            elif mode.value == "on":
                self.tracking_enabled = True
            elif mode.value == "off":
                self.tracking_enabled = False

            self._last_applied_tracking_status = None

            if self.tracking_enabled:
                await self._evaluate_tracking_now(interaction.guild)
                state_msg = "включён"
            else:
                state_msg = "выключен"

            member = interaction.guild.get_member(self.bot.settings.track_user_id)
            target_status = str(member.status) if member else "unknown"

            me = interaction.guild.me
            bot_status = str(me.status) if me else "unknown"

            await interaction.followup.send(
                f"🔎 Трекинг: **{state_msg}**\n"
                f"Целевой пользователь: `<@{self.bot.settings.track_user_id}>` статус сейчас: **{target_status}**\n"
                f"Статус бота: **{bot_status}**",
                ephemeral=True,
            )
        except Exception:
            await notify_admin(self.bot, f"/track error:\n{traceback.format_exc()}")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send("Произошла ошибка при переключении трекинга.", ephemeral=True)
                else:
                    await interaction.response.send_message(
                        "Произошла ошибка при переключении трекинга.",
                        ephemeral=True,
                    )
            except Exception:
                pass
