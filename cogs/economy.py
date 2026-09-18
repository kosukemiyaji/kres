"""
cogs/economy.py
-----------------
- /work        : コマンド実行で個人がコインを獲得（クールダウンあり）
- /balance     : 所持コインの確認
- /pay         : 個人間の送金
- /salary set  : 管理者がロールに給料を設定（例：「社長」ロール保持者に毎日1000コイン）
- role_salary_loop : バックグラウンドで給料設定に従い自動支給するタスク
"""

import datetime
import random

import discord
from discord import app_commands
from discord.ext import commands, tasks

from db.database import InsufficientFundsError

WORK_COOLDOWN_MIN = 30
WORK_MIN_REWARD = 50
WORK_MAX_REWARD = 200


async def has_salary_manager_permission(interaction: discord.Interaction) -> bool:
    """給与設定担当ロール、未設定なら Discord 管理者だけを許可する。"""
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return False

    cur = await interaction.client.db.conn.execute(  # type: ignore[attr-defined]
        "SELECT role_id FROM salary_manager_roles WHERE guild_id = ?",
        (interaction.guild_id,),
    )
    manager = await cur.fetchone()
    if manager is None:
        return interaction.user.guild_permissions.administrator
    return any(role.id == manager["role_id"] for role in interaction.user.roles)


class EconomyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.role_salary_loop.start()

    def cog_unload(self):
        self.role_salary_loop.cancel()

    @property
    def db(self):
        return self.bot.db  # type: ignore

    # ------------------------------------------------------
    # 稼ぐ
    # ------------------------------------------------------
    @app_commands.command(name="work", description="働いてコインを稼ぎます（クールダウンあり）")
    async def work(self, interaction: discord.Interaction):
        guild_id, user_id = interaction.guild_id, interaction.user.id
        row = await self.db.get_user(guild_id, user_id)

        if row["last_work_at"]:
            last = datetime.datetime.fromisoformat(row["last_work_at"])
            elapsed = (datetime.datetime.utcnow() - last).total_seconds() / 60
            if elapsed < WORK_COOLDOWN_MIN:
                remaining = int(WORK_COOLDOWN_MIN - elapsed)
                await interaction.response.send_message(
                    f"まだ休憩中です。あと {remaining} 分後にまた働けます。", ephemeral=True
                )
                return

        reward = random.randint(WORK_MIN_REWARD, WORK_MAX_REWARD)
        await self.db.transfer(guild_id, "system", None, "user", user_id,
                                reward, category="work", memo="/work コマンドによる収入")
        await self.db.touch_last_work(guild_id, user_id)
        await interaction.response.send_message(f"働いて **{reward}コイン** を獲得しました！")

    # ------------------------------------------------------
    # 残高確認
    # ------------------------------------------------------
    @app_commands.command(name="balance", description="所持コインを確認します")
    async def balance(self, interaction: discord.Interaction, user: discord.Member = None):
        target = user or interaction.user
        row = await self.db.get_user(interaction.guild_id, target.id)
        await interaction.response.send_message(
            f"{target.display_name} の所持コイン: **{row['balance']}**"
        )

    # ------------------------------------------------------
    # 個人間送金
    # ------------------------------------------------------
    @app_commands.command(name="pay", description="他のユーザーにコインを送金します")
    @app_commands.describe(user="送金先のユーザー", amount="送金額")
    async def pay(self, interaction: discord.Interaction, user: discord.Member, amount: int):
        if user.id == interaction.user.id:
            await interaction.response.send_message("自分自身には送金できません。", ephemeral=True)
            return
        try:
            await self.db.transfer(interaction.guild_id, "user", interaction.user.id,
                                    "user", user.id, amount, category="transfer",
                                    memo=f"{interaction.user.display_name} -> {user.display_name}")
        except InsufficientFundsError:
            await interaction.response.send_message("所持コインが不足しています。", ephemeral=True)
            return
        await interaction.response.send_message(
            f"{interaction.user.display_name} が {user.display_name} に **{amount}コイン** 送金しました。"
        )

    # ------------------------------------------------------
    # ロール給料の設定（管理者用）
    # ------------------------------------------------------
    salary_group = app_commands.Group(name="salary", description="ロール給料の管理（管理者用）")

    @salary_group.command(name="set", description="ロールに給料を設定します")
    @app_commands.describe(role="対象ロール", amount="1回の支給額", interval_min="支給間隔（分）")
    @app_commands.check(has_salary_manager_permission)
    async def salary_set(self, interaction: discord.Interaction, role: discord.Role,
                          amount: int, interval_min: int):
        if amount <= 0 or interval_min <= 0:
            await interaction.response.send_message("金額と支給間隔は1以上で指定してください。", ephemeral=True)
            return
        await self.db.conn.execute(
            "INSERT INTO role_salaries (guild_id, role_id, amount, interval_min) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(guild_id, role_id) DO UPDATE SET amount=excluded.amount, interval_min=excluded.interval_min",
            (interaction.guild_id, role.id, amount, interval_min),
        )
        await self.db.conn.commit()
        await interaction.response.send_message(
            f"ロール **{role.name}** の保持者に {interval_min}分ごとに {amount}コインを支給するよう設定しました。"
        )

    @salary_group.command(name="manager_role", description="給与設定を変更できるロールを指定します（Discord管理者専用）")
    @app_commands.describe(role="給与設定を変更できるロール")
    @app_commands.checks.has_permissions(administrator=True)
    async def salary_manager_role(self, interaction: discord.Interaction, role: discord.Role):
        await self.db.conn.execute(
            "INSERT INTO salary_manager_roles (guild_id, role_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET role_id=excluded.role_id",
            (interaction.guild_id, role.id),
        )
        await self.db.conn.commit()
        await interaction.response.send_message(
            f"給与設定の変更権限を **{role.name}** ロールに設定しました。", ephemeral=True
        )

    @salary_set.error
    async def salary_set_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            message = "給与設定を変更するには、給与設定担当ロール（未設定の場合はDiscord管理者権限）が必要です。"
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return
        raise error

    # ------------------------------------------------------
    # 定期実行：ロール給料の自動支給
    # ------------------------------------------------------
    @tasks.loop(minutes=1)
    async def role_salary_loop(self):
        cur = await self.db.conn.execute("SELECT * FROM role_salaries")
        settings = await cur.fetchall()

        now = datetime.datetime.utcnow()
        for setting in settings:
            guild = self.bot.get_guild(setting["guild_id"])
            if guild is None:
                continue
            role = guild.get_role(setting["role_id"])
            if role is None:
                continue

            if setting["last_paid_at"]:
                last_paid_at = datetime.datetime.fromisoformat(setting["last_paid_at"])
                elapsed_minutes = (now - last_paid_at).total_seconds() / 60
                if elapsed_minutes < setting["interval_min"]:
                    continue

            for member in role.members:
                if member.bot:
                    continue
                await self.db.transfer(
                    setting["guild_id"], "system", None, "user", member.id,
                    setting["amount"], category="salary",
                    memo=f"ロール給料: {role.name}"
                )
            await self.db.conn.execute(
                "UPDATE role_salaries SET last_paid_at = ? WHERE guild_id = ? AND role_id = ?",
                (now.isoformat(), setting["guild_id"], setting["role_id"]),
            )
            await self.db.conn.commit()

    @role_salary_loop.before_loop
    async def before_role_salary_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(EconomyCog(bot))
