"""Direct-message relay between users and admin."""

from __future__ import annotations

from datetime import datetime
import traceback
from typing import Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils import notify_admin
from config import GUILD_ID


def _to_base36(n: int) -> str:
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if n == 0:
        return "0"
    sign = ""
    if n < 0:
        sign, n = "-", -n
    result = ""
    while n:
        n, r = divmod(n, 36)
        result = digits[r] + result
    return sign + result


class DmRelayCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.dm_ticket_map: Dict[str, int] = {}
        self.dm_user_ticket: Dict[int, str] = {}
        self.dm_last_seen: Dict[int, datetime] = {}
        self.dm_forward_map: Dict[int, int] = {}

    def _get_or_make_ticket(self, user_id: int) -> str:
        if user_id in self.dm_user_ticket:
            return self.dm_user_ticket[user_id]
        code = _to_base36(user_id)[-6:].upper().rjust(6, "0")
        suffix = 0
        ticket = code
        while ticket in self.dm_ticket_map and self.dm_ticket_map[ticket] != user_id:
            suffix += 1
            ticket = (code + _to_base36(suffix))[-6:].upper()
        self.dm_user_ticket[user_id] = ticket
        self.dm_ticket_map[ticket] = user_id
        return ticket

    async def _dm_admin(self) -> Optional[discord.User]:
        admin = self.bot.get_user(self.bot.settings.admin_user_id)
        if admin is None:
            try:
                admin = await self.bot.fetch_user(self.bot.settings.admin_user_id)
            except Exception:
                admin = None
        return admin

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        if message.guild is not None:
            await self.bot.process_commands(message)
            return

        try:
            if message.author.id == self.bot.settings.admin_user_id:
                if message.reference and message.reference.message_id:
                    ref_id = message.reference.message_id
                    user_id = self.dm_forward_map.get(ref_id)

                    if not user_id:
                        try:
                            ref_msg = await message.channel.fetch_message(ref_id)
                            if ref_msg.reference and ref_msg.reference.message_id:
                                user_id = self.dm_forward_map.get(ref_msg.reference.message_id)
                        except Exception:
                            pass

                    if user_id:
                        try:
                            files = [await att.to_file() for att in message.attachments[:10]]
                            content = (message.content or "").strip()
                            if not content and not files:
                                await message.add_reaction("⛔")
                                return

                            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                            await user.send(content or " ", files=files if files else None)
                            await message.add_reaction("✅")
                        except Exception:
                            await notify_admin(self.bot, f"Reply DM relay failed:\n{traceback.format_exc()}")
                            try:
                                await message.add_reaction("⚠️")
                            except Exception:
                                pass
                        finally:
                            return
                await self.bot.process_commands(message)
                return

            ticket = self._get_or_make_ticket(message.author.id)
            self.dm_last_seen[message.author.id] = datetime.now()

            admin = await self._dm_admin()
            if admin:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                header = (
                    f"📨 **DM #{ticket}**\n"
                    f"От: **{message.author}** (`{message.author.id}`)\n"
                    f"Время: {ts}\n"
                    f"------"
                )
                content = message.content.strip() if message.content else "*— без текста —*"

                files = []
                try:
                    for att in message.attachments[:10]:
                        files.append(await att.to_file())
                except Exception:
                    await notify_admin(self.bot, f"Attachment fetch failed:\n{traceback.format_exc()}")

                try:
                    forwarded = await admin.send(f"{header}\n{content}", files=files if files else None)
                    self.dm_forward_map[forwarded.id] = message.author.id
                except Exception:
                    await notify_admin(self.bot, f"Failed to forward DM to admin:\n{traceback.format_exc()}")

            await self.bot.process_commands(message)
        except Exception:
            await notify_admin(self.bot, f"on_message error:\n{traceback.format_exc()}")
            try:
                await self.bot.process_commands(message)
            except Exception:
                pass

    @app_commands.command(
        name="dm",
        description="Ответить пользователю в ЛС по ticket-коду или user_id (резерв, обычно не нужен)",
    )
    @app_commands.describe(
        target="Ticket (6 символов) или числовой user_id",
        text="Текст сообщения",
        attachment="Необязательное вложение (1 файл)",
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def dm_send(
        self,
        interaction: discord.Interaction,
        target: str,
        text: str,
        attachment: Optional[discord.Attachment] = None,
    ) -> None:
        if interaction.user.id != self.bot.settings.admin_user_id:
            await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
            return

        try:
            await interaction.response.defer(ephemeral=True, thinking=False)

            target = target.strip().upper()
            if target.isdigit() and len(target) >= 15:
                user_id = int(target)
            else:
                user_id = self.dm_ticket_map.get(target)

            if not user_id:
                await interaction.followup.send(
                    "Не найден получатель: неверный ticket или user_id.", ephemeral=True
                )
                return

            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            if not user:
                await interaction.followup.send(
                    "Не удалось получить пользователя по указанной цели.",
                    ephemeral=True,
                )
                return

            files = None
            if attachment is not None:
                try:
                    files = [await attachment.to_file()]
                except Exception:
                    await notify_admin(self.bot, f"/dm: failed to fetch attachment:\n{traceback.format_exc()}")

            await user.send(text, files=files)
            ticket = self._get_or_make_ticket(user_id)
            await interaction.followup.send(
                f"✅ Отправлено в ЛС пользователю **{user}** (ID `{user_id}`) — Ticket `#{ticket}`",
                ephemeral=True,
            )
        except Exception:
            await notify_admin(self.bot, f"/dm error:\n{traceback.format_exc()}")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        "Произошла ошибка при отправке ЛС.", ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        "Произошла ошибка при отправке ЛС.",
                        ephemeral=True,
                    )
            except Exception:
                pass
