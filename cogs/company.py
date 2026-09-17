"""
cogs/company.py
-----------------
- /company create  : 出資して会社を設立
- /company hire     : 従業員を雇用し、給料額・支給間隔を設定
- /company info     : 会社の残高・従業員一覧を表示
- payroll_loop      : 雇用契約に基づき、会社残高から従業員へ自動的に給料を支払うバックグラウンドタスク
"""

import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from db.database import InsufficientFundsError


class CompanyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.payroll_loop.start()

    def cog_unload(self):
        self.payroll_loop.cancel()

    @property
    def db(self):
        return self.bot.db  # type: ignore

    company_group = app_commands.Group(name="company", description="会社（法人）の管理")

    # ------------------------------------------------------
    # 会社設立
    # ------------------------------------------------------
    @company_group.command(name="create", description="出資して会社を設立します")
    @app_commands.describe(name="会社名", capital="出資額（自分の所持コインから引かれます）")
    async def create(self, interaction: discord.Interaction, name: str, capital: int):
        try:
            company_id = await self.db.create_company(
                interaction.guild_id, name, interaction.user.id, capital
            )
        except InsufficientFundsError:
            await interaction.response.send_message("出資額が所持コインを超えています。", ephemeral=True)
            return

        await interaction.response.send_message(
            f"会社 **{name}** を設立しました！（会社ID: {company_id}, 資本金: {capital}コイン）"
        )

    # ------------------------------------------------------
    # 雇用
    # ------------------------------------------------------
    @company_group.command(name="hire", description="従業員を雇用します（代表者のみ）")
    @app_commands.describe(company_id="会社ID", user="雇用するユーザー",
                            position="役職名", salary="1回あたりの給料",
                            interval_min="給料の支給間隔（分, 既定=1440=1日）")
    async def hire(self, interaction: discord.Interaction, company_id: int,
                    user: discord.Member, position: str, salary: int, interval_min: int = 1440):
        company = await self.db.get_company(interaction.guild_id, company_id)
        if company is None:
            await interaction.response.send_message("会社が見つかりません。", ephemeral=True)
            return
        if company["owner_id"] != interaction.user.id:
            await interaction.response.send_message("代表者のみ雇用できます。", ephemeral=True)
            return

        await self.db.hire(company_id, interaction.guild_id, user.id, position, salary, interval_min)
        await interaction.response.send_message(
            f"{user.display_name} を **{position}** として雇用しました（給料: {salary}コイン / {interval_min}分ごと）。"
        )

    # ------------------------------------------------------
    # 会社情報
    # ------------------------------------------------------
    @company_group.command(name="info", description="会社の情報を表示します")
    @app_commands.describe(company_id="会社ID")
    async def info(self, interaction: discord.Interaction, company_id: int):
        company = await self.db.get_company(interaction.guild_id, company_id)
        if company is None:
            await interaction.response.send_message("会社が見つかりません。", ephemeral=True)
            return

        cur = await self.db.conn.execute(
            "SELECT * FROM employments WHERE company_id = ?", (company_id,)
        )
        employees = await cur.fetchall()

        embed = discord.Embed(title=f"🏢 {company['name']}", color=discord.Color.gold())
        embed.add_field(name="会社ID", value=str(company["company_id"]))
        embed.add_field(name="残高", value=f"{company['balance']}コイン")
        owner = interaction.guild.get_member(company["owner_id"])
        embed.add_field(name="代表者", value=owner.display_name if owner else str(company["owner_id"]))

        if employees:
            lines = []
            for e in employees:
                member = interaction.guild.get_member(e["user_id"])
                name = member.display_name if member else str(e["user_id"])
                lines.append(f"{name}（{e['position']}）: {e['salary']}コイン / {e['salary_interval_min']}分")
            embed.add_field(name="従業員", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="従業員", value="（なし）", inline=False)

        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------
    # 定期実行：給料の自動支払い
    # ------------------------------------------------------
    @tasks.loop(minutes=1)
    async def payroll_loop(self):
        cur = await self.db.conn.execute("SELECT * FROM employments")
        employments = await cur.fetchall()
        now = datetime.datetime.utcnow()

        for e in employments:
            base_time_str = e["last_paid_at"] or e["hired_at"]
            base_time = datetime.datetime.fromisoformat(base_time_str)
            elapsed_min = (now - base_time).total_seconds() / 60

            if elapsed_min < e["salary_interval_min"]:
                continue

            try:
                await self.db.transfer(
                    e["guild_id"], "company", e["company_id"], "user", e["user_id"],
                    e["salary"], category="salary",
                    memo=f"会社ID {e['company_id']} からの給料"
                )
            except InsufficientFundsError:
                # 会社の残高不足。今回はスキップし、次のタイミングで再試行する。
                continue

            await self.db.conn.execute(
                "UPDATE employments SET last_paid_at = ? WHERE company_id = ? AND user_id = ?",
                (now.isoformat(), e["company_id"], e["user_id"]),
            )
            await self.db.conn.commit()

    @payroll_loop.before_loop
    async def before_payroll_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(CompanyCog(bot))
