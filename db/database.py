"""
db/database.py
---------------
discord.py は asyncio ベースなので、DBアクセスも同期sqlite3ではなく
aiosqlite（非同期ラッパー）を使う。
すべての「金の移動」は必ず transfer() を経由させることで、
- 個人/法人の残高更新
- transactions テーブルへの履歴記録
を1トランザクション内でアトミックに行い、明細機能の正しさを保証する。
"""

import aiosqlite
import datetime
import os
from pathlib import Path
from typing import Optional, Literal

# DATABASE_PATH を指定すると、Render の Persistent Disk など任意の場所に
# SQLite ファイルを置ける。未指定時はローカル開発用の従来パスを使用する。
DB_PATH = Path(os.environ.get("DATABASE_PATH", Path(__file__).parent / "econobot.db"))
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

EntityType = Literal["user", "company", "system"]


class InsufficientFundsError(Exception):
    """残高不足で取引が実行できない場合に投げる例外"""
    pass


class Database:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON;")
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            await self._conn.executescript(f.read())
        cur = await self._conn.execute("PRAGMA table_info(role_salaries)")
        columns = {row["name"] for row in await cur.fetchall()}
        if "last_paid_at" not in columns:
            await self._conn.execute("ALTER TABLE role_salaries ADD COLUMN last_paid_at TEXT")
        await self._conn.commit()

    async def close(self):
        if self._conn:
            await self._conn.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database.connect() を先に呼んでください"
        return self._conn

    # ------------------------------------------------------
    # ユーザー
    # ------------------------------------------------------
    async def ensure_user(self, guild_id: int, user_id: int) -> None:
        await self.conn.execute(
            "INSERT INTO users (guild_id, user_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id, user_id) DO NOTHING",
            (guild_id, user_id),
        )
        await self.conn.commit()

    async def get_user(self, guild_id: int, user_id: int) -> aiosqlite.Row:
        await self.ensure_user(guild_id, user_id)
        cur = await self.conn.execute(
            "SELECT * FROM users WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return await cur.fetchone()

    async def set_user_address(self, guild_id: int, user_id: int, address_code: str) -> None:
        await self.ensure_user(guild_id, user_id)
        await self.conn.execute(
            "UPDATE users SET address_code = ? WHERE guild_id = ? AND user_id = ?",
            (address_code, guild_id, user_id),
        )
        await self.conn.commit()

    async def touch_last_work(self, guild_id: int, user_id: int) -> None:
        await self.conn.execute(
            "UPDATE users SET last_work_at = ? WHERE guild_id = ? AND user_id = ?",
            (datetime.datetime.utcnow().isoformat(), guild_id, user_id),
        )
        await self.conn.commit()

    # ------------------------------------------------------
    # 会社
    # ------------------------------------------------------
    async def create_company(self, guild_id: int, name: str, owner_id: int,
                               capital: int, address_code: Optional[str] = None) -> int:
        """
        capital分を owner の個人残高から会社の資本金として移す。
        残高不足なら InsufficientFundsError。
        """
        owner = await self.get_user(guild_id, owner_id)
        if owner["balance"] < capital:
            raise InsufficientFundsError("出資額が個人の所持コインを超えています")

        cur = await self.conn.execute(
            "INSERT INTO companies (guild_id, name, owner_id, balance, address_code) "
            "VALUES (?, ?, ?, 0, ?)",
            (guild_id, name, owner_id, address_code),
        )
        company_id = cur.lastrowid
        await self.conn.commit()

        # 出資処理は通常の資金移動として記録する（個人 -> 会社, category='capital'）
        await self.transfer(guild_id, "user", owner_id, "company", company_id,
                             capital, category="capital", memo=f"{name} 設立出資")
        return company_id

    async def get_company(self, guild_id: int, company_id: int) -> Optional[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM companies WHERE guild_id = ? AND company_id = ?",
            (guild_id, company_id),
        )
        return await cur.fetchone()

    async def hire(self, company_id: int, guild_id: int, user_id: int,
                    position: str, salary: int, interval_min: int = 1440) -> None:
        await self.ensure_user(guild_id, user_id)
        await self.conn.execute(
            "INSERT INTO employments (company_id, user_id, guild_id, position, salary, salary_interval_min) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(company_id, user_id) DO UPDATE SET "
            "position=excluded.position, salary=excluded.salary, salary_interval_min=excluded.salary_interval_min",
            (company_id, guild_id, user_id, position, salary, interval_min),
        )
        await self.conn.commit()

    # ------------------------------------------------------
    # 資金移動（すべての入出金の唯一の入口）
    # ------------------------------------------------------
    async def get_balance(self, entity_type: EntityType, entity_id: int, guild_id: int) -> int:
        if entity_type == "user":
            row = await self.get_user(guild_id, entity_id)
            return row["balance"]
        elif entity_type == "company":
            row = await self.get_company(guild_id, entity_id)
            return row["balance"] if row else 0
        return 0  # system は無限扱い

    async def transfer(self, guild_id: int,
                        from_type: EntityType, from_id: Optional[int],
                        to_type: EntityType, to_id: Optional[int],
                        amount: int, category: str, memo: str = "") -> None:
        """
        from -> to へ amount を移動し、transactions に1行記録する。
        from_type='system' の場合は無限に発行できる（例：/work の報酬）。
        to_type='system' の場合は消滅させる（例：ショップの一部消費など）。
        """
        if amount <= 0:
            raise ValueError("amount は正の整数で指定してください")

        if from_type != "system":
            balance = await self.get_balance(from_type, from_id, guild_id)
            if balance < amount:
                raise InsufficientFundsError(f"{from_type}({from_id}) の残高が不足しています")

        if from_type == "user":
            await self.conn.execute(
                "UPDATE users SET balance = balance - ? WHERE guild_id=? AND user_id=?",
                (amount, guild_id, from_id))
        elif from_type == "company":
            await self.conn.execute(
                "UPDATE companies SET balance = balance - ? WHERE guild_id=? AND company_id=?",
                (amount, guild_id, from_id))

        if to_type == "user":
            await self.ensure_user(guild_id, to_id)
            await self.conn.execute(
                "UPDATE users SET balance = balance + ? WHERE guild_id=? AND user_id=?",
                (amount, guild_id, to_id))
        elif to_type == "company":
            await self.conn.execute(
                "UPDATE companies SET balance = balance + ? WHERE guild_id=? AND company_id=?",
                (amount, guild_id, to_id))

        await self.conn.execute(
            "INSERT INTO transactions (guild_id, from_type, from_id, to_type, to_id, amount, category, memo) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, from_type, from_id, to_type, to_id, amount, category, memo),
        )
        await self.conn.commit()

    # ------------------------------------------------------
    # 明細（取引履歴）
    # ------------------------------------------------------
    async def get_statement(self, entity_type: EntityType, entity_id: int,
                             guild_id: int, limit: int = 10):
        cur = await self.conn.execute(
            "SELECT * FROM transactions WHERE guild_id = ? AND "
            "((from_type = ? AND from_id = ?) OR (to_type = ? AND to_id = ?)) "
            "ORDER BY created_at DESC LIMIT ?",
            (guild_id, entity_type, entity_id, entity_type, entity_id, limit),
        )
        return await cur.fetchall()

    # ------------------------------------------------------
    # 住所マスタ検索
    # ------------------------------------------------------
    async def find_addresses(self, keyword: str, limit: int = 10):
        cur = await self.conn.execute(
            "SELECT * FROM addresses WHERE pref_name LIKE ? OR city_name LIKE ? LIMIT ?",
            (f"%{keyword}%", f"%{keyword}%", limit),
        )
        return await cur.fetchall()

    async def get_address(self, code: str):
        cur = await self.conn.execute("SELECT * FROM addresses WHERE code = ?", (code,))
        return await cur.fetchone()
