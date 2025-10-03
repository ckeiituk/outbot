import asyncio
from datetime import datetime
import random
import pathlib
import traceback
from typing import Optional, Dict

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    BOT_TOKEN,
    ADMIN_USER_ID,
    BOOST_REPORT_CHANNEL_ID,
    GUILD_ID,
    GOOGLE_SHEET_URL,
    INVITE_CODE_FOR_BOT_BOOSTER,
    ROLE_BOT_BOOSTER,
    ROLE_MOVIES,
    ROLE_SERVER_BOOSTER,
    TRACK_USER_ID,
)
from aiohttp.client_exceptions import ClientConnectionResetError

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = True          # нужно для on_message и relay
intents.voice_states = True
intents.presences = True                # нужно для on_presence_update

bot = commands.Bot(command_prefix="!", intents=intents)
commands_synced = False

# ====== STATE ======
auto_report_boosters = True
invites = {}  # guild_id -> list(invites)
sticky_voice_channels = {}  # guild_id -> voice_channel_id

# target game
target_participants = set()
target_game_active = False
target_game_event = asyncio.Event()

# voice reconnect control
reconnect_attempts = {}  # guild_id -> int
MAX_RECONNECT_ATTEMPTS = 3

# track presence state
tracking_enabled = False
_last_applied_tracking_status: Optional[discord.Status] = None

# DM relay state
dm_ticket_map: Dict[str, int] = {}      # ticket -> user_id
dm_user_ticket: Dict[int, str] = {}     # user_id -> ticket
dm_last_seen: Dict[int, datetime] = {}  # user_id -> last dm time
dm_forward_map: Dict[int, int] = {}     # forwarded_message_id (к вам) -> original_user_id


# ====== UTILS ======
async def notify_admin(error_message: str):
    """DM админу + лог в файл."""
    try:
        admin = bot.get_user(ADMIN_USER_ID)
        if admin:
            try:
                await admin.send(f"⚠️ **Bot Error:**\n```\n{error_message}\n```")
            except Exception as dm_error:
                print(f"Failed to send DM to admin: {dm_error}")
    finally:
        try:
            with open("error_log.txt", "a", encoding="utf-8") as f:
                f.write(f"{datetime.now()} - {error_message}\n")
        except Exception as file_err:
            print(f"Failed to write error log: {file_err}")


def has_role(role_name: str):
    """Проверка роли для слэш-команд."""
    def predicate(interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member):
            raise app_commands.CheckFailure("Команда доступна только на сервере.")

        role = discord.utils.get(interaction.user.roles, name=role_name)
        if role is None:
            raise app_commands.CheckFailure(f"Нужна роль: {role_name}")
        return True
    return app_commands.check(predicate)


async def get_invite_by_code(invite_list, code):
    for inv in invite_list:
        if inv.code == code:
            return inv
    return None


def can_connect(guild: discord.Guild, ch: discord.abc.Connectable) -> tuple[bool, str]:
    me = guild.me
    if not me:
        return False, "no me"
    perms = ch.permissions_for(me)
    if not perms.view_channel:
        return False, "no view_channel"
    if not perms.connect:
        return False, "no connect"
    if isinstance(ch, discord.VoiceChannel) and ch.user_limit and len(ch.members) >= ch.user_limit and not perms.move_members:
        return False, "channel full"
    return True, "ok"


class OpusSilence(discord.AudioSource):
    def read(self) -> bytes:
        return b"\xF8\xFF\xFE"
    def is_opus(self) -> bool:
        return True


async def ensure_silence_playing(vc: discord.VoiceClient):
    try:
        if vc and vc.is_connected() and not vc.is_playing():
            vc.play(OpusSilence(), after=lambda e: None)
    except Exception:
        pass


async def ensure_self_mute(guild: discord.Guild):
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
        # сокет в закрытии — повторим позже при стабильном соединении
        return


async def safe_connect(channel: discord.VoiceChannel | discord.StageChannel, reason: str, guild_id: int) -> bool:
    """Безопасный connect с self-mute; включает 'опус-тишину' и обнуляет попытки."""
    try:
        ok_perms, why = can_connect(channel.guild, channel)
        if not ok_perms:
            await notify_admin(f"{reason}: cannot connect to {channel.id} ({why})")
            return False

        await channel.connect(self_mute=True, self_deaf=True)
        reconnect_attempts[guild_id] = 0
        # поддерживаем UDP активным
        await ensure_silence_playing(channel.guild.voice_client)
        return True
    except IndexError:
        sticky_voice_channels.pop(guild_id, None)
        await notify_admin(
            f"{reason}: IndexError while connecting to voice. Sticky disabled for guild {guild_id}.\n{traceback.format_exc()}"
        )
        return False
    except Exception:
        await notify_admin(
            f"{reason}: Unexpected error while connecting to voice in guild {guild_id}.\n{traceback.format_exc()}"
        )
        return False


def is_online_like(status: discord.Status) -> bool:
    """Онлайн-статусы: online/idle/dnd — считаем 'в сети'. Invisible/offline — считаем оффлайн."""
    return status in (discord.Status.online, discord.Status.idle, discord.Status.dnd)


async def apply_tracking_by_status(user_status: discord.Status, guild: discord.Guild):
    """Применяем нужный статус бота на основе статуса целевого пользователя."""
    global _last_applied_tracking_status
    if not tracking_enabled:
        return

    desired: discord.Status
    # пользователь 'в сети' -> бот невидимка, иначе -> idle
    if is_online_like(user_status):
        desired = discord.Status.invisible
    else:
        desired = discord.Status.idle

    if _last_applied_tracking_status == desired:
        return

    try:
        await bot.change_presence(status=desired)
        _last_applied_tracking_status = desired
    except Exception:
        await notify_admin(f"apply_tracking_by_status failed:\n{traceback.format_exc()}")


async def evaluate_tracking_now(guild: discord.Guild):
    """Проверить текущее состояние целевого пользователя и применить статус бота."""
    try:
        member = guild.get_member(TRACK_USER_ID)
        if member is None:
            try:
                member = await guild.fetch_member(TRACK_USER_ID)
            except Exception:
                member = None

        if member is None:
            await notify_admin(f"Track: user {TRACK_USER_ID} не найден в гильдии {guild.id}")
            return

        await apply_tracking_by_status(member.status, guild)
    except Exception:
        await notify_admin(f"evaluate_tracking_now error:\n{traceback.format_exc()}")


def _to_base36(n: int) -> str:
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if n == 0:
        return "0"
    sign = ""
    if n < 0:
        sign, n = "-", -n
    s = ""
    while n:
        n, r = divmod(n, 36)
        s = digits[r] + s
    return sign + s


def get_or_make_ticket(user_id: int) -> str:
    """Стабильный короткий ticket по user_id (6 последних base36-символов, UPPER)."""
    if user_id in dm_user_ticket:
        return dm_user_ticket[user_id]
    code = _to_base36(user_id)[-6:].upper().rjust(6, "0")
    suffix = 0
    ticket = code
    while ticket in dm_ticket_map and dm_ticket_map[ticket] != user_id:
        suffix += 1
        ticket = (code + _to_base36(suffix))[-6:].upper()
    dm_user_ticket[user_id] = ticket
    dm_ticket_map[ticket] = user_id
    return ticket


async def dm_admin() -> Optional[discord.User]:
    user = bot.get_user(ADMIN_USER_ID)
    if user is None:
        try:
            user = await bot.fetch_user(ADMIN_USER_ID)
        except Exception:
            user = None
    return user


# ====== EVENTS ======
@bot.event
async def on_ready():
    global commands_synced
    print(f"Logged in as {bot.user} (id: {bot.user.id})")
    if not commands_synced:
        try:
            guild_object = discord.Object(id=GUILD_ID)
            synced = await bot.tree.sync(guild=guild_object)
            commands_synced = True
            print(f"Synced {len(synced)} application commands for guild {GUILD_ID}.")
        except Exception:
            await notify_admin(
                f"Failed to sync app commands for guild {GUILD_ID}:\n{traceback.format_exc()}"
            )
    # заполняем кэш инвайтов
    for guild in bot.guilds:
        try:
            invites[guild.id] = await guild.invites()
        except Exception as e:
            await notify_admin(f"Failed to fetch invites for guild {guild.id}: {e}\n{traceback.format_exc()}")

        if tracking_enabled:
            await evaluate_tracking_now(guild)


@bot.event
async def on_member_join(member: discord.Member):
    global invites
    try:
        new_invites = await member.guild.invites()
        for new_invite in new_invites:
            old_invite = await get_invite_by_code(invites.get(member.guild.id, []), new_invite.code)
            if old_invite and new_invite.uses > old_invite.uses:
                if new_invite.code == INVITE_CODE_FOR_BOT_BOOSTER:
                    role = discord.utils.get(member.guild.roles, name=ROLE_BOT_BOOSTER)
                    if role:
                        await member.add_roles(role, reason="Used special bot booster invite")
                        channel = bot.get_channel(BOOST_REPORT_CHANNEL_ID)
                        if channel:
                            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            await channel.send(f"{member.mention}, {now}")
                    break
        invites[member.guild.id] = new_invites
    except Exception as e:
        await notify_admin(f"Error in on_member_join: {e}\n{traceback.format_exc()}")


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if not auto_report_boosters:
        return
    try:
        channel = bot.get_channel(BOOST_REPORT_CHANNEL_ID)
        if not channel:
            return

        booster_role = discord.utils.get(after.guild.roles, name=ROLE_SERVER_BOOSTER)
        bot_booster_role = discord.utils.get(after.guild.roles, name=ROLE_BOT_BOOSTER)

        if booster_role in before.roles and booster_role not in after.roles:
            if bot_booster_role in after.roles:
                await channel.send(f"{after.display_name} has stopped boosting the server.")
    except Exception as e:
        await notify_admin(f"Error in on_member_update: {e}\n{traceback.format_exc()}")


@bot.event
async def on_presence_update(before: discord.Member, after: discord.Member):
    """Ключевой обработчик трекинга статуса пользователя."""
    try:
        if not tracking_enabled:
            return
        if after.id != TRACK_USER_ID:
            return
        await apply_tracking_by_status(after.status, after.guild)
    except Exception:
        await notify_admin(f"on_presence_update error:\n{traceback.format_exc()}")


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    try:
        if member.id != bot.user.id:
            return

        guild = member.guild
        target_channel_id = sticky_voice_channels.get(guild.id)
        if not target_channel_id:
            return

        # если бота вынесло из канала/он в другом канале
        if after.channel is None or (after.channel and after.channel.id != target_channel_id):
            attempts = reconnect_attempts.get(guild.id, 0)
            if attempts >= MAX_RECONNECT_ATTEMPTS:
                sticky_voice_channels.pop(guild.id, None)
                await notify_admin(f"Auto-reconnect stopped in guild {guild.id}: too many failed attempts.")
                return

            delay = 2 ** attempts
            await asyncio.sleep(delay)

            # перечитаем целевой канал — за это время всё могло поменяться
            target_channel = bot.get_channel(sticky_voice_channels.get(guild.id, 0))
            if target_channel is None or not isinstance(target_channel, (discord.VoiceChannel, discord.StageChannel)):
                sticky_voice_channels.pop(guild.id, None)
                return

            ok_perms, reason = can_connect(guild, target_channel)
            if not ok_perms:
                reconnect_attempts[guild.id] = attempts + 1
                # нет смысла жечь попытку дальше: подождём следующего апдейта
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
                    ok = await safe_connect(target_channel, "Auto-reconnect", guild.id)
                    success = ok

                if success:
                    reconnect_attempts[guild.id] = 0
                    await ensure_self_mute(guild)
                    await ensure_silence_playing(guild.voice_client)
                else:
                    reconnect_attempts[guild.id] = next_attempt
                    return
            except IndexError:
                reconnect_attempts[guild.id] = next_attempt
                sticky_voice_channels.pop(guild.id, None)
                await notify_admin(
                    f"Auto-reconnect IndexError in guild {guild.id}. Sticky disabled.\n{traceback.format_exc()}"
                )
                return
            except Exception:
                reconnect_attempts[guild.id] = next_attempt
                await notify_admin(
                    f"Auto-reconnect unexpected error in guild {guild.id}:\n{traceback.format_exc()}"
                )
                return

        # NOTE: ветку "else: ensure_self_mute" намеренно убрали — мьютим только при move/connect.
    except Exception:
        await notify_admin(f"Error in on_voice_state_update:\n{traceback.format_exc()}")


@bot.event
async def on_message(message: discord.Message):
    """Перехват ЛС боту и переотправка админу. Поддержка reply-ответов от админа обратно пользователю."""
    try:
        # не отвечаем на ботов
        if message.author.bot:
            return

        # === Серверные сообщения: просто пропускаем к командам ===
        if message.guild is not None:
            await bot.process_commands(message)
            return

        # === DM-канал ===
        # 1) Если пишет АДМИН и это reply на пересланное сообщение — отправляем ответ исходному пользователю
        if message.author.id == ADMIN_USER_ID:
            if message.reference and message.reference.message_id:
                ref_id = message.reference.message_id
                user_id = dm_forward_map.get(ref_id)

                # Если ответили на ответ (цепочка), попробуем найти корень
                if not user_id:
                    try:
                        ref_msg = await message.channel.fetch_message(ref_id)
                        if ref_msg.reference and ref_msg.reference.message_id:
                            user_id = dm_forward_map.get(ref_msg.reference.message_id)
                    except Exception:
                        pass

                if user_id:
                    try:
                        out_files = []
                        for att in message.attachments[:10]:
                            out_files.append(await att.to_file())

                        content = (message.content or "").strip()
                        if not content and not out_files:
                            await message.add_reaction("⛔")
                            return

                        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
                        await user.send(content or " ", files=out_files if out_files else None)

                        await message.add_reaction("✅")
                    except Exception:
                        await notify_admin(f"Reply DM relay failed:\n{traceback.format_exc()}")
                        try:
                            await message.add_reaction("⚠️")
                        except Exception:
                            pass
                    finally:
                        return  # не отдаём дальше командам, чтобы не конфликтовало
            # не reply — позволим использовать команды в ЛС при желании
            await bot.process_commands(message)
            return

        # 2) Если пишет КТО-ТО ДРУГОЙ (пользователь) в ЛС боту — пересылаем админу
        ticket = get_or_make_ticket(message.author.id)
        dm_last_seen[message.author.id] = datetime.now()

        admin = await dm_admin()
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
                await notify_admin(f"Attachment fetch failed:\n{traceback.format_exc()}")

            try:
                forwarded = await admin.send(f"{header}\n{content}", files=files if files else None)
                # привяжем пересланное вам сообщение к исходному пользователю (для reply)
                dm_forward_map[forwarded.id] = message.author.id
            except Exception:
                await notify_admin(f"Failed to forward DM to admin:\n{traceback.format_exc()}")

        # В DM боту (от не-админа) команд мы не ждём, но на всякий случай:
        await bot.process_commands(message)

    except Exception:
        await notify_admin(f"on_message error:\n{traceback.format_exc()}")
        try:
            await bot.process_commands(message)
        except Exception:
            pass


# ===== Slash commands =====

@bot.tree.command(name="фильмы", description="Ссылка на таблицу с фильмами (видно только вам)")
@has_role(ROLE_MOVIES)
async def films(interaction: discord.Interaction):
    try:
        await interaction.response.send_message(f"[Таблица с фильмами]({GOOGLE_SHEET_URL})", ephemeral=True)
    except Exception as e:
        await notify_admin(f"Error in /фильмы: {e}\n{traceback.format_exc()}")
        if not interaction.response.is_done():
            await interaction.response.send_message("Ошибка при обработке команды.", ephemeral=True)


@bot.tree.command(name="invite", description="Получить пригласительную ссылку")
async def invite(interaction: discord.Interaction):
    try:
        await interaction.response.send_message(
            f"Пригласительная ссылка для ботов: https://discord.gg/{INVITE_CODE_FOR_BOT_BOOSTER}",
            ephemeral=True
        )
    except Exception as e:
        await notify_admin(f"Error in /invite: {e}\n{traceback.format_exc()}")
        if not interaction.response.is_done():
            await interaction.response.send_message("Ошибка при обработке команды.", ephemeral=True)


@bot.tree.command(name="sync", description="Глобально синхронизировать слэш-команды и показать список")
async def sync_commands(interaction: discord.Interaction):
    try:
        if interaction.user.id != ADMIN_USER_ID:
            await interaction.response.send_message("You must be the the owner to use this command!", ephemeral=True)
            return

        await interaction.response.send_message("Syncing commands globally…", ephemeral=True)
        synced = await bot.tree.sync()

        names = [f"/{cmd.name}" for cmd in synced]
        txt = ", ".join(names) if names else "— (нет команд)"

        if len(txt) > 1900:
            parts, chunk, curr_len = [], [], 0
            for n in names:
                add_len = len(n) + 2
                if curr_len + add_len > 1800:
                    parts.append(", ".join(chunk))
                    chunk = [n]
                    curr_len = len(n)
                else:
                    chunk.append(n)
                    curr_len += add_len
            if chunk:
                parts.append(", ".join(chunk))

            await interaction.followup.send(f"Synced {len(synced)} commands:", ephemeral=True)
            for p in parts:
                await interaction.followup.send(p, ephemeral=True)
        else:
            await interaction.followup.send(f"Synced {len(synced)} commands:\n{txt}", ephemeral=True)

        print(f"Synced {len(synced)} commands: {names}")
    except Exception as e:
        await notify_admin(f"Error in /sync: {e}\n{traceback.format_exc()}")
        if not interaction.response.is_done():
            await interaction.response.send_message("An error occurred while syncing commands.", ephemeral=True)


@bot.tree.command(
    name="kick_expired_boosters",
    description="Кикнуть пользователей с ролью 'Бот Бустер', которые больше не бустят"
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.default_permissions(administrator=True)
async def kick_expired_boosters(interaction: discord.Interaction):
    try:
        if interaction.user.id != ADMIN_USER_ID:
            await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)
            return

        guild = interaction.guild
        booster_role = discord.utils.get(guild.roles, name=ROLE_SERVER_BOOSTER)
        bot_booster_role = discord.utils.get(guild.roles, name=ROLE_BOT_BOOSTER)
        if not bot_booster_role:
            channel = bot.get_channel(BOOST_REPORT_CHANNEL_ID)
            if channel:
                await channel.send(f"Role '{ROLE_BOT_BOOSTER}' not found")
            await interaction.response.send_message(f"Role '{ROLE_BOT_BOOSTER}' not found", ephemeral=True)
            return

        kicked_users = []
        for member in list(bot_booster_role.members):
            if not booster_role or booster_role not in member.roles:
                try:
                    await guild.kick(member, reason="No longer boosting the server")
                    kicked_users.append(member.display_name)
                except Exception as e:
                    await notify_admin(f"Kick failed for {member.id}: {e}")

        message = ("Kicked for expired boosts: " + ", ".join(kicked_users)) if kicked_users \
            else "No users kicked. All boosters are up to date."

        channel = bot.get_channel(BOOST_REPORT_CHANNEL_ID)
        if channel:
            await channel.send(message)
            await interaction.response.send_message(
                f"Kick report sent to the channel <#{channel.id}>.", ephemeral=True
            )
        else:
            await interaction.response.send_message("Report channel not found!", ephemeral=True)
    except Exception as e:
        await notify_admin(f"Error in /kick_expired_boosters: {e}\n{traceback.format_exc()}")
        if not interaction.response.is_done():
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)


@bot.tree.command(
    name="report_expired_boosters",
    description="Список пользователей с 'Бот Бустер', которые больше не бустят"
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.default_permissions(administrator=True)
async def report_expired_boosters(interaction: discord.Interaction):
    try:
        if interaction.user.id != ADMIN_USER_ID:
            await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)
            return

        channel = bot.get_channel(BOOST_REPORT_CHANNEL_ID)
        if not channel:
            await interaction.response.send_message("Report channel not found!", ephemeral=True)
            return

        message = "Users potentially no longer boosting the server:\n"
        guild = interaction.guild
        booster_role = discord.utils.get(guild.roles, name=ROLE_SERVER_BOOSTER)
        bot_booster_role = discord.utils.get(guild.roles, name=ROLE_BOT_BOOSTER)
        if not bot_booster_role:
            await interaction.response.send_message(f"Role '{ROLE_BOT_BOOSTER}' not found", ephemeral=True)
            return

        for member in bot_booster_role.members:
            if not booster_role or booster_role not in member.roles:
                message += f"{member.display_name}\n"

        if len(message) > 2000:
            message = "Message too long to send."

        await channel.send(message)
        await interaction.response.send_message("Report sent to the channel.", ephemeral=True)
    except Exception as e:
        await notify_admin(f"Error in /report_expired_boosters: {e}\n{traceback.format_exc()}")
        if not interaction.response.is_done():
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)


@bot.tree.command(name="toggle_auto_report", description="Включить/выключить авто-репорты бустеров")
async def toggle_auto_report(interaction: discord.Interaction):
    try:
        if interaction.user.id != ADMIN_USER_ID:
            await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)
            return
        global auto_report_boosters
        auto_report_boosters = not auto_report_boosters
        state = "enabled" if auto_report_boosters else "disabled"
        await interaction.response.send_message(
            f"Automatic reporting of boosters is now {state}.", ephemeral=True
        )
    except Exception as e:
        await notify_admin(f"Error in /toggle_auto_report: {e}\n{traceback.format_exc()}")
        if not interaction.response.is_done():
            await interaction.response.send_message("An error occurred while toggling auto report.", ephemeral=True)


@bot.tree.command(
    name="tmdb",
    description="Отправить 4 локальных PNG-изображения пользователю в ЛС"
)
@app_commands.describe(user="Кому отправить изображения")
async def tmdb(interaction: discord.Interaction, user: discord.User):
    try:
        image_dir = pathlib.Path("images")
        image_paths = list(image_dir.glob("*.png"))[:4]
        await interaction.response.send_message(f"Отправляю {len(image_paths)} изображений пользователю {user.mention} в ЛС…", ephemeral=True)

        if not image_paths:
            await interaction.followup.send("Изображения не найдены.", ephemeral=True)
            return

        for image_path in image_paths:
            try:
                with image_path.open("rb") as image_file:
                    file = discord.File(image_file, filename=image_path.name)
                    await user.send(file=file)  # <-- DM пользователю
            except FileNotFoundError:
                await interaction.followup.send(
                    f"Файл {image_path.name} не найден для {user.mention}.", ephemeral=True
                )

        await interaction.followup.send("Готово: отправлено в ЛС.", ephemeral=True)
    except Exception as e:
        await notify_admin(f"Error in /tmdb: {e}\n{traceback.format_exc()}")
        if not interaction.response.is_done():
            await interaction.response.send_message("An error occurred while sending images.", ephemeral=True)


@bot.tree.command(name="roll", description="Случайное число")
@app_commands.describe(start="Начало интервала", end="Конец интервала")
async def roll(interaction: discord.Interaction, start: int = 1, end: int = 100):
    try:
        if start > end:
            await interaction.response.send_message(
                "Invalid interval! Start should be less than or equal to end.", ephemeral=True
            )
            return
        result = random.randint(start, end)
        await interaction.response.send_message(f"🎲 You rolled a {result}!")
    except Exception as e:
        await notify_admin(f"Error in /roll: {e}\n{traceback.format_exc()}")
        if not interaction.response.is_done():
            await interaction.response.send_message("An error occurred while rolling the number.", ephemeral=True)


# === Накрутка: держать бота в голосовом канале ===

@bot.tree.command(name="накрутка", description="Бот зайдёт в ваш голосовой канал и будет там находиться (серый микрофон)")
async def nakrutka(interaction: discord.Interaction):
    try:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("Зайдите в голосовой канал и повторите команду.", ephemeral=True)
            return

        channel = interaction.user.voice.channel
        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            await interaction.response.send_message("Это не голосовой канал.", ephemeral=True)
            return

        ok_perms, reason = can_connect(interaction.guild, channel)
        if not ok_perms:
            await interaction.response.send_message(f"Не могу подключиться: {reason}.", ephemeral=True)
            return

        guild = interaction.guild
        vc = guild.voice_client

        sticky_voice_channels[guild.id] = channel.id
        reconnect_attempts.setdefault(guild.id, 0)

        if vc and vc.is_connected():
            if vc.channel.id != channel.id:
                try:
                    await vc.move_to(channel)
                    reconnect_attempts[guild.id] = 0
                    await ensure_self_mute(guild)
                    await ensure_silence_playing(guild.voice_client)
                    await interaction.response.send_message(
                        f"Перешёл в канал **{channel.name}** и буду там находиться (замьючен).",
                        ephemeral=True
                    )
                except IndexError:
                    sticky_voice_channels.pop(guild.id, None)
                    await notify_admin(f"Error in /накрутка (move_to): IndexError\n{traceback.format_exc()}")
                    await interaction.response.send_message(
                        "Не удалось перейти в голосовой канал (внутренняя ошибка голосового клиента). "
                        "Автодержание отключено.", ephemeral=True
                    )
                except Exception:
                    await notify_admin(f"Error in /накрутка (move_to):\n{traceback.format_exc()}")
                    await interaction.response.send_message("Произошла ошибка при переходе в канал.", ephemeral=True)
            else:
                await ensure_self_mute(guild)
                await ensure_silence_playing(guild.voice_client)
                await interaction.response.send_message(
                    f"Я уже в канале **{channel.name}** и останусь здесь (замьючен).",
                    ephemeral=True
                )
        else:
            ok = await safe_connect(channel, "Error in /накрутка (connect)", guild.id)
            if ok:
                await interaction.response.send_message(
                    f"Зашёл в канал **{channel.name}** и буду там находиться (замьючен).",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "Не удалось подключиться к голосовому каналу (внутренняя ошибка голосового клиента). "
                    "Автодержание отключено.", ephemeral=True
                )
    except Exception:
        await notify_admin(f"Error in /накрутка (outer):\n{traceback.format_exc()}")
        if not interaction.response.is_done():
            await interaction.response.send_message("Произошла ошибка при подключении к голосу.", ephemeral=True)


@bot.tree.command(name="стопнакрутка", description="Отключить 'прилипание' и выйти из голосового канала")
async def stop_nakrutka(interaction: discord.Interaction):
    try:
        guild = interaction.guild
        vc = guild.voice_client

        sticky_voice_channels.pop(guild.id, None)

        if vc and vc.is_connected():
            await vc.disconnect(force=True)
            await interaction.response.send_message("Отключился, автодержание выключено.", ephemeral=True)
        else:
            await interaction.response.send_message("Я и так не в голосовом канале.", ephemeral=True)
    except Exception:
        await notify_admin(f"Error in /стопнакрутка:\n{traceback.format_exc()}")
        if not interaction.response.is_done():
            await interaction.response.send_message("Произошла ошибка при отключении от голоса.", ephemeral=True)


# ===== Префиксные команды =====

@bot.command(name="target", help="Start a target game where users can join by typing +")
async def target(ctx: commands.Context):
    global target_participants, target_game_active, target_game_event

    if target_game_active:
        await ctx.send("A target game is already running!")
        return

    target_participants = set()
    target_game_active = True
    target_game_event.clear()
    await ctx.send("Type + to join the target game! You have 15 seconds.")

    def check(message: discord.Message) -> bool:
        return message.content == "+" and message.channel == ctx.channel

    async def collect_participants() -> None:
        try:
            loop = asyncio.get_running_loop()
            end_time = loop.time() + 15
            while not target_game_event.is_set():
                timeout = end_time - loop.time()
                if timeout <= 0:
                    break
                try:
                    message = await asyncio.wait_for(bot.wait_for("message", check=check), timeout=timeout)
                except asyncio.TimeoutError:
                    break
                else:
                    target_participants.add(message.author)
        finally:
            target_game_event.set()

    try:
        await collect_participants()

        if target_participants:
            winner = random.choice(list(target_participants))
            await ctx.send(f"The winner is {winner.mention}!")
        else:
            await ctx.send("No participants.")
    finally:
        target_game_active = False


@bot.command(name="go", help="End the target game early and choose a winner")
async def go(ctx: commands.Context):
    global target_game_active, target_game_event

    if target_game_active:
        target_game_event.set()
        await ctx.send("Ending the target game early!")
        if target_participants:
            winner = random.choice(list(target_participants))
            await ctx.send(f"The winner is {winner.mention}!")
        else:
            await ctx.send("No participants.")
        target_game_active = False
    else:
        await ctx.send("No target game is running.")


# ===== /status (дефер + followup) =====

@bot.tree.command(name="status", description="Изменить статус бота и активность")
@app_commands.describe(
    state="Статус присутствия",
    activity_type="Тип активности (необязательно)",
    text="Текст активности (необязательно)"
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
    ]
)
async def set_status(
    interaction: discord.Interaction,
    state: app_commands.Choice[str],
    activity_type: Optional[app_commands.Choice[str]] = None,
    text: Optional[str] = None,
):
    try:
        if interaction.user.id != ADMIN_USER_ID:
            await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
            return

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

        await bot.change_presence(status=status_map[state.value], activity=activity)

        await interaction.followup.send(
            f"✅ Статус обновлён на **{state.name}**"
            + (f", активность: **{text}**" if activity else ""),
            ephemeral=True
        )
    except Exception as e:
        msg = f"Ошибка при смене статуса: {e}"
        await notify_admin(f"{msg}\n{traceback.format_exc()}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


# ===== /track (вкл/выкл трекинг presence пользователя) =====

@bot.tree.command(name="track", description="Включить/выключить трекинг статуса пользователя и автосмену присутствия бота")
@app_commands.describe(mode="Режим: on/off (или не указывать — тогда переключение)")
@app_commands.choices(
    mode=[
        app_commands.Choice(name="on (включить)", value="on"),
        app_commands.Choice(name="off (выключить)", value="off"),
    ]
)
async def track_cmd(interaction: discord.Interaction, mode: Optional[app_commands.Choice[str]] = None):
    try:
        if interaction.user.id != ADMIN_USER_ID:
            await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=False)

        global tracking_enabled, _last_applied_tracking_status

        if mode is None:
            tracking_enabled = not tracking_enabled
        elif mode.value == "on":
            tracking_enabled = True
        elif mode.value == "off":
            tracking_enabled = False

        _last_applied_tracking_status = None

        if tracking_enabled:
            await evaluate_tracking_now(interaction.guild)
            state_msg = "включён"
        else:
            state_msg = "выключен"

        member = interaction.guild.get_member(TRACK_USER_ID)
        target_status = str(member.status) if member else "unknown"

        me = interaction.guild.me
        bot_status = str(me.status) if me else "unknown"

        await interaction.followup.send(
            f"🔎 Трекинг: **{state_msg}**\n"
            f"Целевой пользователь: `<@{TRACK_USER_ID}>` статус сейчас: **{target_status}**\n"
            f"Статус бота: **{bot_status}**",
            ephemeral=True
        )
    except Exception:
        await notify_admin(f"/track error:\n{traceback.format_exc()}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send("Произошла ошибка при переключении трекинга.", ephemeral=True)
            else:
                await interaction.response.send_message("Произошла ошибка при переключении трекинга.", ephemeral=True)
        except Exception:
            pass


# ===== Доп: /dm — резерв =====

@bot.tree.command(name="dm", description="Ответить пользователю в ЛС по ticket-коду или user_id (резерв, обычно не нужен)")
@app_commands.describe(
    target="Ticket (6 символов) или числовой user_id",
    text="Текст сообщения",
    attachment="Необязательное вложение (1 файл)"
)
async def dm_send(
    interaction: discord.Interaction,
    target: str,
    text: str,
    attachment: Optional[discord.Attachment] = None
):
    try:
        if interaction.user.id != ADMIN_USER_ID:
            await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=False)

        # распознаём target
        user_id: Optional[int] = None
        target = target.strip().upper()
        if target.isdigit() and len(target) >= 15:
            user_id = int(target)
        else:
            user_id = dm_ticket_map.get(target)

        if not user_id:
            await interaction.followup.send("Не найден получатель: неверный ticket или user_id.", ephemeral=True)
            return

        user: Optional[discord.User] = bot.get_user(user_id) or await bot.fetch_user(user_id)
        if not user:
            await interaction.followup.send("Не удалось получить пользователя по указанной цели.", ephemeral=True)
            return

        files = None
        if attachment is not None:
            try:
                files = [await attachment.to_file()]
            except Exception:
                await notify_admin(f"/dm: failed to fetch attachment:\n{traceback.format_exc()}")

        await user.send(text, files=files)

        ticket = get_or_make_ticket(user_id)
        await interaction.followup.send(f"✅ Отправлено в ЛС пользователю **{user}** (ID `{user_id}`) — Ticket `#{ticket}`", ephemeral=True)
    except Exception:
        await notify_admin(f"/dm error:\n{traceback.format_exc()}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send("Произошла ошибка при отправке ЛС.", ephemeral=True)
            else:
                await interaction.response.send_message("Произошла ошибка при отправке ЛС.", ephemeral=True)
        except Exception:
            pass


# ===== Error handlers =====

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    msg = f"An error occurred: {error}\n{traceback.format_exc()}"
    print(msg)
    await notify_admin(msg)
    try:
        if interaction.response.is_done():
            await interaction.followup.send("Произошла ошибка при выполнении команды.", ephemeral=True)
        else:
            await interaction.response.send_message("Произошла ошибка при выполнении команды.", ephemeral=True)
    except Exception:
        pass


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    error_message = f"Error in command '{getattr(ctx.command, 'name', 'unknown')}': {error}\n{traceback.format_exc()}"
    print(error_message)
    await notify_admin(error_message)
    if isinstance(error, commands.CheckFailure):
        await ctx.send("You don't have permission to use this command.")
    else:
        await ctx.send("An error occurred while processing the command.")


@bot.event
async def on_error(event_method, *args, **kwargs):
    error_message = f"Unhandled exception in {event_method}: {args} {kwargs}\n{traceback.format_exc()}"
    print(error_message)
    await notify_admin(error_message)


if __name__ == "__main__":
    bot.run(BOT_TOKEN)
