"""Centralised error handling for the bot."""

from __future__ import annotations

import traceback

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils import notify_admin


class ErrorHandlerCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        msg = f"An error occurred: {error}\n{traceback.format_exc()}"
        print(msg)
        await notify_admin(self.bot, msg)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "Произошла ошибка при выполнении команды.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "Произошла ошибка при выполнении команды.",
                    ephemeral=True,
                )
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        error_message = (
            f"Error in command '{getattr(ctx.command, 'name', 'unknown')}': {error}\n{traceback.format_exc()}"
        )
        print(error_message)
        await notify_admin(self.bot, error_message)
        if isinstance(error, commands.CheckFailure):
            await ctx.send("You don't have permission to use this command.")
        else:
            await ctx.send("An error occurred while processing the command.")

    @commands.Cog.listener()
    async def on_error(self, event_method, *args, **kwargs) -> None:  # type: ignore[override]
        error_message = (
            f"Unhandled exception in {event_method}: {args} {kwargs}\n{traceback.format_exc()}"
        )
        print(error_message)
        await notify_admin(self.bot, error_message)
