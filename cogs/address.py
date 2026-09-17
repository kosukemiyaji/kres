"""
cogs/address.py
-----------------
都道府県コード・市区町村コード（総務省 団体コード）を用いた住所設定・照会機能。
"""

import discord
from discord import app_commands
from discord.ext import commands


class AddressCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db  # type: ignore

    address_group = app_commands.Group(name="address", description="住所の設定・確認")

    @address_group.command(name="search", description="市区町村名・都道府県名から団体コードを検索します")
    @app_commands.describe(keyword="検索したい都道府県名または市区町村名（部分一致）")
    async def search(self, interaction: discord.Interaction, keyword: str):
        rows = await self.db.find_addresses(keyword, limit=15)
        if not rows:
            await interaction.response.send_message(
                f"「{keyword}」に一致する住所が見つかりませんでした。", ephemeral=True
            )
            return

        embed = discord.Embed(title=f"住所検索結果: {keyword}", color=discord.Color.blue())
        for row in rows:
            label = row["pref_name"] if not row["city_name"] else f"{row['pref_name']}{row['city_name']}"
            embed.add_field(name=f"{label}", value=f"団体コード: `{row['code']}`", inline=False)
        embed.set_footer(text="設定するには /address set コード:<団体コード> を使ってください")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @address_group.command(name="set", description="自分の住所を団体コードで設定します")
    @app_commands.describe(code="団体コード（/address search で確認できます）")
    async def set_address(self, interaction: discord.Interaction, code: str):
        address = await self.db.get_address(code.zfill(6))
        if not address:
            await interaction.response.send_message(
                "指定された団体コードが見つかりません。/address search で確認してください。",
                ephemeral=True,
            )
            return

        await self.db.set_user_address(interaction.guild_id, interaction.user.id, address["code"])
        label = address["pref_name"] if not address["city_name"] else f"{address['pref_name']}{address['city_name']}"
        await interaction.response.send_message(f"住所を **{label}** に設定しました。", ephemeral=True)

    @address_group.command(name="show", description="自分（または指定したユーザー）の住所を表示します")
    async def show(self, interaction: discord.Interaction, user: discord.Member = None):
        target = user or interaction.user
        row = await self.db.get_user(interaction.guild_id, target.id)
        if not row or not row["address_code"]:
            await interaction.response.send_message(f"{target.display_name} は住所未設定です。", ephemeral=True)
            return

        address = await self.db.get_address(row["address_code"])
        label = address["pref_name"] if not address["city_name"] else f"{address['pref_name']}{address['city_name']}"
        await interaction.response.send_message(f"{target.display_name} の住所: **{label}**")


async def setup(bot: commands.Bot):
    await bot.add_cog(AddressCog(bot))
