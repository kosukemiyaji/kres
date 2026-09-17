"""
cogs/shop.py
-------------
個人・会社が商品/サービス/不動産を出品し、購入（消費）できる機能。
購入時は buyer -> seller の transfer() が走り、そのまま収支明細に反映される。
"""

import discord
from discord import app_commands
from discord.ext import commands

from db.database import InsufficientFundsError


class ShopCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db  # type: ignore

    shop_group = app_commands.Group(name="shop", description="商品・サービスの出品と購入")

    # ------------------------------------------------------
    # 出品（個人 or 会社）
    # ------------------------------------------------------
    @shop_group.command(name="list", description="商品・サービスを出品します")
    @app_commands.describe(
        name="商品名", price="価格", category="goods / service / real_estate",
        company_id="法人として出品する場合は会社IDを指定（省略時は個人として出品）",
        stock="在庫数（省略時は無制限）",
    )
    async def list_item(self, interaction: discord.Interaction, name: str, price: int,
                         category: str = "goods", company_id: int = None, stock: int = -1):
        if company_id is not None:
            company = await self.db.get_company(interaction.guild_id, company_id)
            if company is None or company["owner_id"] != interaction.user.id:
                await interaction.response.send_message("その会社の代表者ではありません。", ephemeral=True)
                return
            seller_type, seller_id = "company", company_id
        else:
            seller_type, seller_id = "user", interaction.user.id

        await self.db.conn.execute(
            "INSERT INTO shop_items (guild_id, seller_type, seller_id, name, price, stock, category) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (interaction.guild_id, seller_type, seller_id, name, price, stock, category),
        )
        await self.db.conn.commit()
        await interaction.response.send_message(f"**{name}**（{price}コイン）を出品しました。")

    # ------------------------------------------------------
    # 一覧表示
    # ------------------------------------------------------
    @shop_group.command(name="browse", description="出品されている商品・サービスを一覧表示します")
    async def browse(self, interaction: discord.Interaction):
        cur = await self.db.conn.execute(
            "SELECT * FROM shop_items WHERE guild_id = ? ORDER BY item_id DESC LIMIT 20",
            (interaction.guild_id,),
        )
        items = await cur.fetchall()
        if not items:
            await interaction.response.send_message("出品されている商品はありません。", ephemeral=True)
            return

        embed = discord.Embed(title="🛒 出品一覧", color=discord.Color.green())
        for item in items:
            stock_label = "無制限" if item["stock"] < 0 else str(item["stock"])
            embed.add_field(
                name=f"[{item['item_id']}] {item['name']}",
                value=f"{item['price']}コイン / 在庫: {stock_label} / 区分: {item['category']}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------
    # 購入
    # ------------------------------------------------------
    @shop_group.command(name="buy", description="商品を購入します")
    @app_commands.describe(item_id="商品ID（/shop browse で確認）",
                            as_company="購入者として振る舞う会社ID（省略時は個人として購入）")
    async def buy(self, interaction: discord.Interaction, item_id: int, as_company: int = None):
        cur = await self.db.conn.execute("SELECT * FROM shop_items WHERE item_id = ?", (item_id,))
        item = await cur.fetchone()
        if item is None:
            await interaction.response.send_message("商品が見つかりません。", ephemeral=True)
            return
        if item["stock"] == 0:
            await interaction.response.send_message("この商品は売り切れです。", ephemeral=True)
            return

        if as_company is not None:
            company = await self.db.get_company(interaction.guild_id, as_company)
            if company is None or company["owner_id"] != interaction.user.id:
                await interaction.response.send_message("その会社の代表者ではありません。", ephemeral=True)
                return
            buyer_type, buyer_id = "company", as_company
        else:
            buyer_type, buyer_id = "user", interaction.user.id

        try:
            await self.db.transfer(
                interaction.guild_id, buyer_type, buyer_id,
                item["seller_type"], item["seller_id"],
                item["price"], category="purchase", memo=f"購入: {item['name']}"
            )
        except InsufficientFundsError:
            await interaction.response.send_message("所持コイン（または会社残高）が不足しています。", ephemeral=True)
            return

        if item["stock"] > 0:
            await self.db.conn.execute(
                "UPDATE shop_items SET stock = stock - 1 WHERE item_id = ?", (item_id,)
            )
            await self.db.conn.commit()

        await interaction.response.send_message(f"**{item['name']}** を {item['price']}コインで購入しました。")


async def setup(bot: commands.Bot):
    await bot.add_cog(ShopCog(bot))
