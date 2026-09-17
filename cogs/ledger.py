"""
cogs/ledger.py
---------------
個人・法人の収支明細（取引履歴）をEmbedで発行する機能。
transactions テーブルを直接参照するため、work / pay / salary / purchase / capital
など、すべての金の動きが自動的に明細に反映される。
"""

import discord
from discord import app_commands
from discord.ext import commands

CATEGORY_LABELS = {
    "work": "労働収入",
    "salary": "給料",
    "transfer": "個人間送金",
    "purchase": "購入/売上",
    "capital": "出資",
}


def format_row(row, entity_type: str, entity_id: int) -> str:
    is_income = (row["to_type"] == entity_type and row["to_id"] == entity_id)
    sign = "+" if is_income else "-"
    label = CATEGORY_LABELS.get(row["category"], row["category"])
    return f"`{row['created_at']}` {sign}{row['amount']}コイン ｜ {label} ｜ {row['memo'] or ''}"


class LedgerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db  # type: ignore

    @app_commands.command(name="statement", description="収支明細を表示します（個人）")
    async def statement(self, interaction: discord.Interaction, user: discord.Member = None, limit: int = 10):
        target = user or interaction.user
        rows = await self.db.get_statement("user", target.id, interaction.guild_id, limit=limit)

        if not rows:
            await interaction.response.send_message(f"{target.display_name} の取引履歴はありません。", ephemeral=True)
            return

        embed = discord.Embed(title=f"📒 {target.display_name} の収支明細", color=discord.Color.blurple())
        embed.description = "\n".join(format_row(r, "user", target.id) for r in rows)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="company_statement", description="収支明細を表示します（法人）")
    @app_commands.describe(company_id="会社ID")
    async def company_statement(self, interaction: discord.Interaction, company_id: int, limit: int = 10):
        company = await self.db.get_company(interaction.guild_id, company_id)
        if company is None:
            await interaction.response.send_message("会社が見つかりません。", ephemeral=True)
            return

        rows = await self.db.get_statement("company", company_id, interaction.guild_id, limit=limit)
        if not rows:
            await interaction.response.send_message(f"{company['name']} の取引履歴はありません。", ephemeral=True)
            return

        embed = discord.Embed(title=f"📒 {company['name']} の収支明細", color=discord.Color.orange())
        embed.description = "\n".join(format_row(r, "company", company_id) for r in rows)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(LedgerCog(bot))
