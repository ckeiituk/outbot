"""Voice related helpers and commands."""

from __future__ import annotations

import asyncio
import traceback
from typing import Dict, Tuple

import discord
from aiohttp.client_exceptions import ClientConnectionResetError
from discord import app_commands
from discord.ext import commands

from bot.utils import notify_admin


class _OpusSilence(discord.AudioSource):
    def read(self) -> bytes:
        return b"\xF8\xFF\xFE"

    def is_opus(self) -> bool:
        return True


class VoiceCog(commands.Cog):
    MAX_RECONNECT_ATTEMPTS = 3

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.sticky_voice_channels: Dict[int, int] = {}
        self.reconnect_attempts: Dict[int, int] = {}

    def _can_connect(self, guild: discord.Guild, channel: discord.abc.Connectable) -> Tuple[bool, str]:
        me = guild.me
        if not me:
            return False, "no me"
        perms = channel.permissions_for(me)
        if not perms.view_channel:
            return False, "no view_channel"
        if not perms.connect:
            return False, "no connect"
        if (
            isinstance(channel, discord.VoiceChannel)
            and channel.user_limit
            and len(channel.members) >= channel.user_limit
            and not perms.move_members
        ):
            return False, "channel full"
        return True, "ok"

    async def _ensure_silence_playing(self, vc: discord.VoiceClient | None) -> None:
        try:
            if vc and vc.is_connected() and not vc.is_playing():
                vc.play(_OpusSilence(), after=lambda _: None)
        except Exception:
            pass

    async def _ensure_self_mute(self, guild: discord.Guild) -> None:
        vc = guild.voice_client
        if not vc or not vc.is_connected():
            return
        me = guild.me
        if not me or not me.voice or me.voice.channel != vc.channel:
            return
        if getattr(me.voice, "self_mute", False) and getattr(me.voice, "self_deaf", False):
            return
        try:
            await guild.change_voice_state(channel=vc.channel, self_mute=True, self_deaf=True)
        except ClientConnectionResetError:
            return

    async def _safe_connect(
        self,
        channel: discord.VoiceChannel | discord.StageChannel,
        reason: str,
        guild_id: int,
    ) -> bool:
        try:
            ok_perms, why = self._can_connect(channel.guild, channel)
            if not ok_perms:
                await notify_admin(self.bot, f"{reason}: cannot connect to {channel.id} ({why})")
                return False

            await channel.connect(self_mute=True, self_deaf=True)
            self.reconnect_attempts[guild_id] = 0
            await self._ensure_silence_playing(channel.guild.voice_client)
            return True
        except IndexError:
            self.sticky_voice_channels.pop(guild_id, None)
            await notify_admin(
                self.bot,
                f"{reason}: IndexError while connecting to voice. Sticky disabled for guild {guild_id}.\n{traceback.format_exc()}",
            )
            return False
        except Exception:
            await notify_admin(
                self.bot,
                f"{reason}: Unexpected error while connecting to voice in guild {guild_id}.\n{traceback.format_exc()}",
            )
            return False

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        _before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.id != self.bot.user.id:
            return

        guild = member.guild
        target_channel_id = self.sticky_voice_channels.get(guild.id)
        if not target_channel_id:
            return

        if after.channel is None or (after.channel and after.channel.id != target_channel_id):
            attempts = self.reconnect_attempts.get(guild.id, 0)
            if attempts >= self.MAX_RECONNECT_ATTEMPTS:
                self.sticky_voice_channels.pop(guild.id, None)
                await notify_admin(
                    self.bot,
                    f"Auto-reconnect stopped in guild {guild.id}: too many failed attempts.",
                )
                return

            delay = 2 ** attempts
            await asyncio.sleep(delay)

            target_channel = self.bot.get_channel(self.sticky_voice_channels.get(guild.id, 0))
            if target_channel is None or not isinstance(
                target_channel, (discord.VoiceChannel, discord.StageChannel)
            ):
                self.sticky_voice_channels.pop(guild.id, None)
                return

            ok_perms, reason = self._can_connect(guild, target_channel)
            if not ok_perms:
                self.reconnect_attempts[guild.id] = attempts + 1
                return

            next_attempt = attempts + 1
            vc = guild.voice_client
            success = False
            try:
                if vc and vc.is_connected():
                    if vc.channel and vc.channel.id == target_channel.id:
                        success = True
                    elif vc.channel:
                        await vc.move_to(target_channel)
                        success = True
                if not success:
                    success = await self._safe_connect(target_channel, "Auto-reconnect", guild.id)

                if success:
                    self.reconnect_attempts[guild.id] = 0
                    await self._ensure_self_mute(guild)
                    await self._ensure_silence_playing(guild.voice_client)
                else:
                    self.reconnect_attempts[guild.id] = next_attempt
                    return
            except IndexError:
                self.reconnect_attempts[guild.id] = next_attempt
                self.sticky_voice_channels.pop(guild.id, None)
                await notify_admin(
                    self.bot,
                    f"Auto-reconnect IndexError in guild {guild.id}. Sticky disabled.\n{traceback.format_exc()}",
                )
                return
            except Exception:
                self.reconnect_attempts[guild.id] = next_attempt
                await notify_admin(
                    self.bot,
                    f"Auto-reconnect unexpected error in guild {guild.id}:\n{traceback.format_exc()}",
                )
                return

    @app_commands.command(name="накрутка", description="Бот зайдёт в ваш голосовой канал и будет там находиться (серый микрофон)")
    async def nakrutka(self, interaction: discord.Interaction) -> None:
        try:
            if not interaction.guild:
                await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
                return

            if not interaction.user.voice or not interaction.user.voice.channel:
                await interaction.response.send_message(
                    "Зайдите в голосовой канал и повторите команду.",
                    ephemeral=True,
                )
                return

            channel = interaction.user.voice.channel
            if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                await interaction.response.send_message("Это не голосовой канал.", ephemeral=True)
                return

            ok_perms, reason = self._can_connect(interaction.guild, channel)
            if not ok_perms:
                await interaction.response.send_message(
                    f"Не могу подключиться: {reason}.", ephemeral=True
                )
                return

            guild = interaction.guild
            vc = guild.voice_client

            self.sticky_voice_channels[guild.id] = channel.id
            self.reconnect_attempts.setdefault(guild.id, 0)

            if vc and vc.is_connected():
                if vc.channel.id != channel.id:
                    try:
                        await vc.move_to(channel)
                        self.reconnect_attempts[guild.id] = 0
                        await self._ensure_self_mute(guild)
                        await self._ensure_silence_playing(guild.voice_client)
                        await interaction.response.send_message(
                            f"Перешёл в канал **{channel.name}** и буду там находиться (замьючен).",
                            ephemeral=True,
                        )
                    except IndexError:
                        self.sticky_voice_channels.pop(guild.id, None)
                        await notify_admin(
                            self.bot,
                            f"Error in /накрутка (move_to): IndexError\n{traceback.format_exc()}",
                        )
                        await interaction.response.send_message(
                            "Не удалось перейти в голосовой канал (внутренняя ошибка голосового клиента). "
                            "Автодержание отключено.",
                            ephemeral=True,
                        )
                    except Exception:
                        await notify_admin(
                            self.bot,
                            f"Error in /накрутка (move_to):\n{traceback.format_exc()}",
                        )
                        await interaction.response.send_message(
                            "Произошла ошибка при переходе в канал.",
                            ephemeral=True,
                        )
                else:
                    await self._ensure_self_mute(guild)
                    await self._ensure_silence_playing(guild.voice_client)
                    await interaction.response.send_message(
                        f"Я уже в канале **{channel.name}** и останусь здесь (замьючен).",
                        ephemeral=True,
                    )
            else:
                ok = await self._safe_connect(channel, "Error in /накрутка (connect)", guild.id)
                if ok:
                    await interaction.response.send_message(
                        f"Зашёл в канал **{channel.name}** и буду там находиться (замьючен).",
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        "Не удалось подключиться к голосовому каналу (внутренняя ошибка голосового клиента). "
                        "Автодержание отключено.",
                        ephemeral=True,
                    )
        except Exception:
            await notify_admin(self.bot, f"Error in /накрутка (outer):\n{traceback.format_exc()}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Произошла ошибка при подключении к голосу.", ephemeral=True
                )

    @app_commands.command(name="стопнакрутка", description="Отключить 'прилипание' и выйти из голосового канала")
    async def stop_nakrutka(self, interaction: discord.Interaction) -> None:
        try:
            if not interaction.guild:
                await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
                return

            guild = interaction.guild
            vc = guild.voice_client

            self.sticky_voice_channels.pop(guild.id, None)

            if vc and vc.is_connected():
                await vc.disconnect(force=True)
                await interaction.response.send_message(
                    "Отключился, автодержание выключено.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "Я и так не в голосовом канале.", ephemeral=True
                )
        except Exception:
            await notify_admin(self.bot, f"Error in /стопнакрутка:\n{traceback.format_exc()}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Произошла ошибка при отключении от голоса.", ephemeral=True
                )
