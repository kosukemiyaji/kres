"""
bot.py
-------
EconoBot のエントリーポイント。
DBへの接続と各Cogのロードを行い、discord.py のBotを起動する。
"""

import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from db.database import Database

load_dotenv()

logging.basicConfig(level=logging.INFO)

INTENTS = discord.Intents.default()
INTENTS.members = True  # ロール保持者の一覧取得（給料支払い）に必要

COGS = [
    "cogs.address",
    "cogs.economy",
    "cogs.company",
    "cogs.shop",
    "cogs.ledger",
]


class EconoBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=INTENTS)
        self.db = Database()

    async def setup_hook(self):
        await self.db.connect()
        for cog in COGS:
            await self.load_extension(cog)
        # ギルドコマンドとして即時反映したい場合は guild= を指定してsync
        await self.tree.sync()

    async def close(self):
        await self.db.close()
        await super().close()


async def main():
    bot = EconoBot()

    @bot.event
    async def on_ready():
        print(f"ログイン完了: {bot.user} (ID: {bot.user.id})")

    token = os.environ["DISCORD_BOT_TOKEN"]
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
