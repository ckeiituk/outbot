"""Prefix commands implementing the target mini-game."""

from __future__ import annotations

import asyncio
import random

import discord
from discord.ext import commands


class TargetGameCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.target_participants: set[discord.User] = set()
        self.target_game_active: bool = False
        self.target_game_event: asyncio.Event = asyncio.Event()

    @commands.command(name="target", help="Start a target game where users can join by typing +")
    async def target(self, ctx: commands.Context) -> None:
        if self.target_game_active:
            await ctx.send("A target game is already running!")
            return

        self.target_participants = set()
        self.target_game_active = True
        self.target_game_event.clear()
        await ctx.send("Type + to join the target game! You have 15 seconds.")

        def check(message: discord.Message) -> bool:
            return message.content == "+" and message.channel == ctx.channel

        async def collect_participants() -> None:
            try:
                loop = asyncio.get_running_loop()
                end_time = loop.time() + 15
                while not self.target_game_event.is_set():
                    timeout = end_time - loop.time()
                    if timeout <= 0:
                        break
                    try:
                        message = await asyncio.wait_for(
                            self.bot.wait_for("message", check=check),
                            timeout=timeout,
                        )
                    except asyncio.TimeoutError:
                        break
                    else:
                        self.target_participants.add(message.author)
            finally:
                self.target_game_event.set()

        try:
            await collect_participants()

            if self.target_participants:
                winner = random.choice(list(self.target_participants))
                await ctx.send(f"The winner is {winner.mention}!")
            else:
                await ctx.send("No participants.")
        finally:
            self.target_game_active = False

    @commands.command(name="go", help="End the target game early and choose a winner")
    async def go(self, ctx: commands.Context) -> None:
        if self.target_game_active:
            self.target_game_event.set()
            await ctx.send("Ending the target game early!")
            if self.target_participants:
                winner = random.choice(list(self.target_participants))
                await ctx.send(f"The winner is {winner.mention}!")
            else:
                await ctx.send("No participants.")
            self.target_game_active = False
        else:
            await ctx.send("No target game is running.")
