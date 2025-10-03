"""Booster tracking and invite handling."""

from __future__ import annotations

import traceback
from typing import Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils import notify_admin
from config import GUILD_ID


class BoostersCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.invites: Dict[int, List[discord.Invite]] = {}
        self.auto_report_boosters: bool = True

    def _has_moderator_privileges(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.bot.settings.admin_user_id:
            return True
        role_name = (self.bot.settings.moderator_role or "").strip()
        if not role_name:
            return False
        if not isinstance(interaction.user, discord.Member):
            return False
        role = discord.utils.get(interaction.user.roles, name=role_name)
        return role is not None

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
            await channel.send(f"{member.display_name} больше не бустит сервер.")

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
                            await member.add_roles(role, reason="Использовал приглашение для бустеров")
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
                    await channel.send(f"{after.display_name} больше не бустит сервер.")
        except Exception:
            await notify_admin(
                self.bot,
                f"Error in on_member_update:\n{traceback.format_exc()}",
            )

    def _get_report_channel(self) -> Optional[discord.TextChannel]:
        channel = self.bot.get_channel(self.bot.settings.boost_report_channel_id)
        return channel if isinstance(channel, discord.TextChannel) else None

    @app_commands.command(
        name="kick_expired_boosters",
        description="Удалить из гильдии пользователей с 'Бот Бустер', которые больше не бустят",
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def kick_expired_boosters(self, interaction: discord.Interaction) -> None:
        if not self._has_moderator_privileges(interaction):
            await interaction.response.send_message(
                "Недостаточно прав для выполнения команды.", ephemeral=True
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
                    f"Роль '{self.bot.settings.role_bot_booster}' не найдена.", ephemeral=True
                )
                return

            kicked_users = []
            for member in list(bot_booster_role.members):
                if not booster_role or booster_role not in member.roles:
                    try:
                        await guild.kick(member, reason="Больше не бустит сервер")
                        kicked_users.append(member.display_name)
                    except Exception as exc:
                        await notify_admin(
                            self.bot,
                            f"Kick failed for {member.id}: {exc}\n{traceback.format_exc()}",
                        )

            message = (
                "Удалены за прекращение буста: " + ", ".join(kicked_users)
            ) if kicked_users else "Удалений нет: все бустеры актуальны."

            channel = self._get_report_channel()
            if channel:
                await channel.send(message)
                await interaction.response.send_message(
                    f"Отчёт об удалении отправлен в канал <#{channel.id}>.", ephemeral=True
                )
            else:
                await interaction.response.send_message("Канал для отчётов не найден.", ephemeral=True)
        except Exception:
            await notify_admin(
                self.bot,
                f"Error in /kick_expired_boosters:\n{traceback.format_exc()}",
            )
            if not interaction.response.is_done():
                await interaction.response.send_message("Произошла ошибка при удалении.", ephemeral=True)

    @app_commands.command(
        name="report_expired_boosters",
        description="Список пользователей с 'Бот Бустер', которые больше не бустят",
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def report_expired_boosters(self, interaction: discord.Interaction) -> None:
        if not self._has_moderator_privileges(interaction):
            await interaction.response.send_message(
                "Недостаточно прав для выполнения команды.", ephemeral=True
            )
            return
        try:
            guild = interaction.guild
            if guild is None:
                await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
                return

            channel = self._get_report_channel()
            if not channel:
                await interaction.response.send_message("Канал для отчётов не найден.", ephemeral=True)
                return

            booster_role = discord.utils.get(guild.roles, name=self.bot.settings.role_server_booster)
            bot_booster_role = discord.utils.get(guild.roles, name=self.bot.settings.role_bot_booster)
            if not bot_booster_role:
                await interaction.response.send_message(
                    f"Роль '{self.bot.settings.role_bot_booster}' не найдена.", ephemeral=True
                )
                return

            lines = ["Пользователи, которые, возможно, перестали бустить сервер:"]
            for member in bot_booster_role.members:
                if not booster_role or booster_role not in member.roles:
                    lines.append(member.display_name)

            message = "\n".join(lines)
            if len(message) > 2000:
                message = "Сообщение слишком длинное для отправки."

            await channel.send(message)
            await interaction.response.send_message("Отчёт отправлен в канал.", ephemeral=True)
        except Exception:
            await notify_admin(
                self.bot,
                f"Error in /report_expired_boosters:\n{traceback.format_exc()}",
            )
            if not interaction.response.is_done():
                await interaction.response.send_message("Произошла ошибка при формировании отчёта.", ephemeral=True)

    @app_commands.command(name="toggle_auto_report", description="Включить/выключить авто-репорты бустеров")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def toggle_auto_report(self, interaction: discord.Interaction) -> None:
        if not self._has_moderator_privileges(interaction):
            await interaction.response.send_message(
                "Недостаточно прав для выполнения команды.", ephemeral=True
            )
            return
        try:
            self.auto_report_boosters = not self.auto_report_boosters
            state = "включена" if self.auto_report_boosters else "выключена"
            await interaction.response.send_message(
                f"Автоматическая отправка отчётов теперь {state}.", ephemeral=True
            )
        except Exception:
            await notify_admin(
                self.bot,
                f"Error in /toggle_auto_report:\n{traceback.format_exc()}",
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Произошла ошибка при переключении автопроверки.",
                    ephemeral=True,
                )
