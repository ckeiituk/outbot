"""Booster tracking and invite handling."""

from __future__ import annotations

import traceback
from typing import Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils import notify_admin


class BoostersCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.invites: Dict[int, List[discord.Invite]] = {}
        self.auto_report_boosters: bool = True

    async def _refresh_invites(self, guild: discord.Guild) -> None:
        try:
            self.invites[guild.id] = await guild.invites()
        except Exception as exc:
            await notify_admin(
                self.bot,
                f"Failed to fetch invites for guild {guild.id}: {exc}\n{traceback.format_exc()}",
            )

    def _find_invite(self, invite_list: List[discord.Invite], code: str) -> Optional[discord.Invite]:
        for inv in invite_list:
            if inv.code == code:
                return inv
        return None

    async def _report_booster_removal(self, member: discord.Member) -> None:
        channel_id = self.bot.settings.boost_report_channel_id
        channel = self.bot.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            await channel.send(f"{member.display_name} has stopped boosting the server.")

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            await self._refresh_invites(guild)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        try:
            new_invites = await member.guild.invites()
            for new_invite in new_invites:
                old_invite = self._find_invite(self.invites.get(member.guild.id, []), new_invite.code)
                if old_invite and new_invite.uses > old_invite.uses:
                    if new_invite.code == self.bot.settings.invite_code_for_bot_booster:
                        role = discord.utils.get(member.guild.roles, name=self.bot.settings.role_bot_booster)
                        if role:
                            await member.add_roles(role, reason="Used special bot booster invite")
                            channel = self.bot.get_channel(self.bot.settings.boost_report_channel_id)
                            if isinstance(channel, discord.TextChannel):
                                now = discord.utils.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                                await channel.send(f"{member.mention}, {now}")
                        break
            self.invites[member.guild.id] = new_invites
        except Exception:
            await notify_admin(
                self.bot,
                f"Error in on_member_join:\n{traceback.format_exc()}",
            )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if not self.auto_report_boosters:
            return
        try:
            channel = self.bot.get_channel(self.bot.settings.boost_report_channel_id)
            if not isinstance(channel, discord.TextChannel):
                return

            booster_role = discord.utils.get(after.guild.roles, name=self.bot.settings.role_server_booster)
            bot_booster_role = discord.utils.get(after.guild.roles, name=self.bot.settings.role_bot_booster)

            if booster_role in before.roles and booster_role not in after.roles:
                if bot_booster_role in after.roles:
                    await channel.send(f"{after.display_name} has stopped boosting the server.")
        except Exception:
            await notify_admin(
                self.bot,
                f"Error in on_member_update:\n{traceback.format_exc()}",
            )

    def _ensure_admin(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.bot.settings.admin_user_id:
            return False
        return True

    def _get_report_channel(self) -> Optional[discord.TextChannel]:
        channel = self.bot.get_channel(self.bot.settings.boost_report_channel_id)
        return channel if isinstance(channel, discord.TextChannel) else None

    @app_commands.command(
        name="kick_expired_boosters",
        description="Удалить из гильдии пользователей с 'Бот Бустер', которые больше не бустят",
    )
    async def kick_expired_boosters(self, interaction: discord.Interaction) -> None:
        if not self._ensure_admin(interaction):
            await interaction.response.send_message(
                "You do not have permission to use this command!", ephemeral=True
            )
            return
        try:
            guild = interaction.guild
            if guild is None:
                await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
                return

            booster_role = discord.utils.get(guild.roles, name=self.bot.settings.role_server_booster)
            bot_booster_role = discord.utils.get(guild.roles, name=self.bot.settings.role_bot_booster)
            if not bot_booster_role:
                await interaction.response.send_message(
                    f"Role '{self.bot.settings.role_bot_booster}' not found", ephemeral=True
                )
                return

            kicked_users = []
            for member in list(bot_booster_role.members):
                if not booster_role or booster_role not in member.roles:
                    try:
                        await guild.kick(member, reason="No longer boosting the server")
                        kicked_users.append(member.display_name)
                    except Exception as exc:
                        await notify_admin(
                            self.bot,
                            f"Kick failed for {member.id}: {exc}\n{traceback.format_exc()}",
                        )

            message = (
                "Kicked for expired boosts: " + ", ".join(kicked_users)
            ) if kicked_users else "No users kicked. All boosters are up to date."

            channel = self._get_report_channel()
            if channel:
                await channel.send(message)
                await interaction.response.send_message(
                    f"Kick report sent to the channel <#{channel.id}>.", ephemeral=True
                )
            else:
                await interaction.response.send_message("Report channel not found!", ephemeral=True)
        except Exception:
            await notify_admin(
                self.bot,
                f"Error in /kick_expired_boosters:\n{traceback.format_exc()}",
            )
            if not interaction.response.is_done():
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    @app_commands.command(
        name="report_expired_boosters",
        description="Список пользователей с 'Бот Бустер', которые больше не бустят",
    )
    async def report_expired_boosters(self, interaction: discord.Interaction) -> None:
        if not self._ensure_admin(interaction):
            await interaction.response.send_message(
                "You do not have permission to use this command!", ephemeral=True
            )
            return
        try:
            guild = interaction.guild
            if guild is None:
                await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
                return

            channel = self._get_report_channel()
            if not channel:
                await interaction.response.send_message("Report channel not found!", ephemeral=True)
                return

            booster_role = discord.utils.get(guild.roles, name=self.bot.settings.role_server_booster)
            bot_booster_role = discord.utils.get(guild.roles, name=self.bot.settings.role_bot_booster)
            if not bot_booster_role:
                await interaction.response.send_message(
                    f"Role '{self.bot.settings.role_bot_booster}' not found", ephemeral=True
                )
                return

            lines = ["Users potentially no longer boosting the server:"]
            for member in bot_booster_role.members:
                if not booster_role or booster_role not in member.roles:
                    lines.append(member.display_name)

            message = "\n".join(lines)
            if len(message) > 2000:
                message = "Message too long to send."

            await channel.send(message)
            await interaction.response.send_message("Report sent to the channel.", ephemeral=True)
        except Exception:
            await notify_admin(
                self.bot,
                f"Error in /report_expired_boosters:\n{traceback.format_exc()}",
            )
            if not interaction.response.is_done():
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    @app_commands.command(name="toggle_auto_report", description="Включить/выключить авто-репорты бустеров")
    async def toggle_auto_report(self, interaction: discord.Interaction) -> None:
        if not self._ensure_admin(interaction):
            await interaction.response.send_message(
                "You do not have permission to use this command!", ephemeral=True
            )
            return
        try:
            self.auto_report_boosters = not self.auto_report_boosters
            state = "enabled" if self.auto_report_boosters else "disabled"
            await interaction.response.send_message(
                f"Automatic reporting of boosters is now {state}.", ephemeral=True
            )
        except Exception:
            await notify_admin(
                self.bot,
                f"Error in /toggle_auto_report:\n{traceback.format_exc()}",
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An error occurred while toggling auto report.",
                    ephemeral=True,
                )
