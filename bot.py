"""
bot.py
-------
EconoBot のエントリーポイント。
DBへの接続と各Cogのロードを行い、discord.py のBotを起動する。
"""

import asyncio
import logging
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from db.database import Database
from data.import_addresses import ensure_addresses_imported

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
ADDRESS_SOURCE_PATH = Path(__file__).parent / "data" / "000925835.xlsx"


async def handle_health_check(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Render のヘルスチェックと外部監視用の最小 HTTP エンドポイント。"""
    try:
        await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        pass

    writer.write(
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Content-Length: 2\r\n"
        b"Connection: close\r\n\r\n"
        b"OK"
    )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def start_health_server() -> asyncio.AbstractServer:
    """Web Service として認識させるため、PORT でヘルスチェックを受け付ける。"""
    port = int(os.environ.get("PORT", "10000"))
    server = await asyncio.start_server(handle_health_check, host="0.0.0.0", port=port)
    logging.info("ヘルスチェックサーバーをポート %s で開始しました", port)
    return server


class EconoBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=INTENTS)
        self.db = Database()

    async def setup_hook(self):
        if ADDRESS_SOURCE_PATH.is_file():
            if ensure_addresses_imported(str(ADDRESS_SOURCE_PATH)):
                logging.info("住所マスタを初期投入しました")
        else:
            logging.warning("住所マスタの Excel が見つかりません: %s", ADDRESS_SOURCE_PATH)
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
    health_server = await start_health_server()

    @bot.event
    async def on_ready():
        print(f"ログイン完了: {bot.user} (ID: {bot.user.id})")

    token = os.environ["DISCORD_BOT_TOKEN"]
    try:
        async with bot:
            await bot.start(token)
    finally:
        health_server.close()
        await health_server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
