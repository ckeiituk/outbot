"""Miscellaneous slash commands."""

from __future__ import annotations

import random
import traceback
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils import notify_admin
from config import GUILD_ID


class MiscCog(commands.Cog):
    """General-purpose commands that don't fit elsewhere."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _is_admin(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.bot.settings.admin_user_id

    @app_commands.command(name="фильмы", description="Ссылка на таблицу с фильмами (видно только вам)")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def films(self, interaction: discord.Interaction) -> None:
        try:
            if not isinstance(interaction.user, discord.Member):
                await interaction.response.send_message(
                    "Команда доступна только на сервере.", ephemeral=True
                )
                return

            role_name = self.bot.settings.role_movies
            role = discord.utils.get(interaction.user.roles, name=role_name)
            if role is None:
                await interaction.response.send_message(
                    f"Нужна роль: {role_name}", ephemeral=True
                )
                return

            await interaction.response.send_message(
                f"[Таблица с фильмами]({self.bot.settings.google_sheet_url})",
                ephemeral=True,
            )
        except Exception as exc:
            await notify_admin(self.bot, f"Error in /фильмы: {exc}\n{traceback.format_exc()}")
            if not interaction.response.is_done():
                await interaction.response.send_message("Ошибка при обработке команды.", ephemeral=True)

    @app_commands.command(name="invite", description="Получить пригласительную ссылку")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def invite(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.send_message(
                "Пригласительная ссылка для ботов: "
                f"https://discord.gg/{self.bot.settings.invite_code_for_bot_booster}",
                ephemeral=True,
            )
        except Exception as exc:
            await notify_admin(self.bot, f"Error in /invite: {exc}\n{traceback.format_exc()}")
            if not interaction.response.is_done():
                await interaction.response.send_message("Ошибка при обработке команды.", ephemeral=True)

    @app_commands.command(name="sync", description="Глобально синхронизировать слэш-команды и показать список")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def sync_commands(self, interaction: discord.Interaction) -> None:
        if not self._is_admin(interaction):
            await interaction.response.send_message(
                "Команда доступна только администратору.", ephemeral=True
            )
            return
        try:
            await interaction.response.send_message("Синхронизирую слэш-команды…", ephemeral=True)
            synced = await self.bot.tree.sync()
            names = [f"/{cmd.name}" for cmd in synced]
            txt = ", ".join(names) if names else "— команд нет"
            await interaction.followup.send(f"Готово: {txt}", ephemeral=True)
        except Exception as exc:
            await notify_admin(self.bot, f"Error in /sync: {exc}\n{traceback.format_exc()}")
            if not interaction.response.is_done():
                await interaction.response.send_message("Ошибка при синхронизации команд.", ephemeral=True)

    @app_commands.command(name="tmdb", description="Отправить 4 локальных PNG-изображения пользователю в ЛС")
    @app_commands.describe(user="Кому отправить изображения")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def tmdb(self, interaction: discord.Interaction, user: discord.User) -> None:
        image_dir = Path("images")
        image_paths = list(image_dir.glob("*.png"))[:4]
        try:
            await interaction.response.send_message(
                f"Отправляю {len(image_paths)} изображений пользователю {user.mention} в ЛС…",
                ephemeral=True,
            )

            if not image_paths:
                await interaction.followup.send("Изображения не найдены.", ephemeral=True)
                return

            for image_path in image_paths:
                try:
                    with image_path.open("rb") as image_file:
                        file = discord.File(image_file, filename=image_path.name)
                        await user.send(file=file)
                except FileNotFoundError:
                    await interaction.followup.send(
                        f"Файл {image_path.name} не найден для {user.mention}.", ephemeral=True,
                    )
            await interaction.followup.send("Готово: отправлено в ЛС.", ephemeral=True)
        except Exception as exc:
            await notify_admin(self.bot, f"Error in /tmdb: {exc}\n{traceback.format_exc()}")
            if not interaction.response.is_done():
                await interaction.response.send_message("Произошла ошибка при отправке изображений.", ephemeral=True)

    @app_commands.command(name="roll", description="Случайное число")
    @app_commands.describe(start="Начало интервала", end="Конец интервала")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def roll(self, interaction: discord.Interaction, start: int = 1, end: int = 100) -> None:
        try:
            if start > end:
                await interaction.response.send_message(
                    "Некорректный интервал: начало должно быть меньше либо равно концу.",
                    ephemeral=True,
                )
                return
            result = random.randint(start, end)
            await interaction.response.send_message(f"🎲 Выпавшее число: {result}")
        except Exception as exc:
            await notify_admin(self.bot, f"Error in /roll: {exc}\n{traceback.format_exc()}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Произошла ошибка при получении случайного числа.", ephemeral=True
                )

    @app_commands.command(name="status", description="Изменить статус бота и активность")
    @app_commands.describe(
        state="Статус присутствия",
        activity_type="Тип активности (необязательно)",
        text="Текст активности (необязательно)",
    )
    @app_commands.choices(
        state=[
            app_commands.Choice(name="Онлайн", value="online"),
            app_commands.Choice(name="Нет на месте", value="idle"),
            app_commands.Choice(name="Не беспокоить", value="dnd"),
            app_commands.Choice(name="Невидимый", value="invisible"),
        ],
        activity_type=[
            app_commands.Choice(name="Играет", value="playing"),
            app_commands.Choice(name="Слушает", value="listening"),
            app_commands.Choice(name="Смотрит", value="watching"),
            app_commands.Choice(name="Соревнуется", value="competing"),
        ],
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def set_status(
        self,
        interaction: discord.Interaction,
        state: app_commands.Choice[str],
        activity_type: Optional[app_commands.Choice[str]] = None,
        text: Optional[str] = None,
    ) -> None:
        if interaction.user.id != self.bot.settings.admin_user_id:
            await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
            return

        try:
            await interaction.response.defer(ephemeral=True, thinking=False)

            status_map = {
                "online": discord.Status.online,
                "idle": discord.Status.idle,
                "dnd": discord.Status.dnd,
                "invisible": discord.Status.invisible,
            }

            activity_map = {
                "playing": discord.ActivityType.playing,
                "listening": discord.ActivityType.listening,
                "watching": discord.ActivityType.watching,
                "competing": discord.ActivityType.competing,
            }

            activity = None
            if activity_type and text:
                activity = discord.Activity(type=activity_map[activity_type.value], name=text)

            await self.bot.change_presence(status=status_map[state.value], activity=activity)

            await interaction.followup.send(
                f"✅ Статус обновлён на **{state.name}**"
                + (f", активность: **{text}**" if activity else ""),
                ephemeral=True,
            )
        except Exception as exc:
            msg = f"Ошибка при смене статуса: {exc}\n{traceback.format_exc()}"
            await notify_admin(self.bot, msg)
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name="ping", description="Проверка отклика бота")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def ping(self, interaction: discord.Interaction) -> None:
        try:
            latency_ms = round(self.bot.latency * 1000)
            guild = interaction.guild
            voice_info = "не подключён к голосу"
            if guild:
                vc = guild.voice_client
                if vc and vc.is_connected():
                    state = []
                    if getattr(vc, "channel", None):
                        state.append(f"канал: {vc.channel.name}")
                    if vc.is_playing():
                        state.append("проигрывает аудио")
                    else:
                        state.append("поток не идёт")
                    me = guild.me
                    if me and me.voice:
                        markers = []
                        if getattr(me.voice, "self_mute", False):
                            markers.append("мьючен")
                        if getattr(me.voice, "self_deaf", False):
                            markers.append("деафнут")
                        if markers:
                            state.append(", ".join(markers))
                    voice_info = "; ".join(state)
            await interaction.response.send_message(
                f"🏓 Пинг: {latency_ms} мс\n🎧 Голос: {voice_info}",
                ephemeral=True,
            )
        except Exception as exc:
            await notify_admin(self.bot, f"Error in /ping: {exc}\n{traceback.format_exc()}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Не удалось получить информацию о пинге.", ephemeral=True
                )
