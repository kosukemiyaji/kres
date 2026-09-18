-- =========================================================
-- EconoBot データベーススキーマ (SQLite)
-- 総務省「全国地方公共団体コード」を住所マスタとして利用する構成
-- =========================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------
-- 住所マスタ：都道府県コード／市区町村コード（総務省 団体コード）
-- 添付Excel「R6.1.1現在の団体」をそのまま取り込む前提のテーブル
-- 団体コード = 都道府県2桁 + 市区町村3桁 + チェックディジット1桁 (計6桁)
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS addresses (
    code            TEXT PRIMARY KEY,   -- 団体コード（6桁, 例: '011002'）
    pref_code       TEXT NOT NULL,      -- 都道府県コード（先頭2桁, 例: '01'）
    pref_name       TEXT NOT NULL,      -- 都道府県名（漢字）
    pref_kana       TEXT,               -- 都道府県名（カナ）
    city_name       TEXT,               -- 市区町村名（漢字, 都道府県のみの行はNULL）
    city_kana       TEXT,               -- 市区町村名（カナ）
    is_designated_city_ward INTEGER NOT NULL DEFAULT 0 -- 政令指定都市の区かどうか
);

CREATE INDEX IF NOT EXISTS idx_addresses_pref ON addresses(pref_code);
CREATE INDEX IF NOT EXISTS idx_addresses_names ON addresses(pref_name, city_name);

-- ---------------------------------------------------------
-- ユーザー（Discordユーザー単位、サーバーごとに別レコード）
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    balance         INTEGER NOT NULL DEFAULT 0,   -- 個人の所持コイン
    address_code    TEXT,                          -- 住所（addresses.code）
    last_work_at    TEXT,                           -- 直近の「稼ぐ」コマンド実行時刻(ISO8601)
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (guild_id, user_id),
    FOREIGN KEY (address_code) REFERENCES addresses(code)
);

-- ---------------------------------------------------------
-- ロール給料設定：「このロールを持つ人には○分ごとに○コイン」
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS role_salaries (
    guild_id        INTEGER NOT NULL,
    role_id         INTEGER NOT NULL,
    amount          INTEGER NOT NULL,               -- 1回の支給額
    interval_min    INTEGER NOT NULL,                -- 支給間隔（分）
    pay_from        TEXT NOT NULL DEFAULT 'system',  -- 'system' or 'company:<company_id>'
    last_paid_at    TEXT,                            -- 最終支給時刻（ISO8601）
    PRIMARY KEY (guild_id, role_id)
);

-- 給与設定を変更できるロール（サーバーごとに1つ）
CREATE TABLE IF NOT EXISTS salary_manager_roles (
    guild_id        INTEGER PRIMARY KEY,
    role_id         INTEGER NOT NULL
);

-- ---------------------------------------------------------
-- 会社（法人）
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS companies (
    company_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    name            TEXT NOT NULL,
    owner_id        INTEGER NOT NULL,               -- 代表者のuser_id
    balance         INTEGER NOT NULL DEFAULT 0,      -- 法人の所持コイン
    address_code    TEXT,                            -- 本店所在地
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (address_code) REFERENCES addresses(code)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_name ON companies(guild_id, name);

-- ---------------------------------------------------------
-- 雇用関係：会社と従業員(user)の紐付け・給料額
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS employments (
    company_id      INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    guild_id        INTEGER NOT NULL,
    position        TEXT NOT NULL DEFAULT '社員',    -- 役職名（表示用）
    salary          INTEGER NOT NULL DEFAULT 0,      -- 支給額
    salary_interval_min INTEGER NOT NULL DEFAULT 1440, -- 支給間隔（分, 既定=1日）
    last_paid_at    TEXT,
    hired_at        TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (company_id, user_id),
    FOREIGN KEY (company_id) REFERENCES companies(company_id) ON DELETE CASCADE
);

-- ---------------------------------------------------------
-- 商品・サービス（消費機能用のショップ）
-- 出品者は個人(user) or 会社(company)のどちらでも可
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS shop_items (
    item_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    seller_type     TEXT NOT NULL CHECK (seller_type IN ('user', 'company')),
    seller_id       INTEGER NOT NULL,      -- user_id または company_id
    name            TEXT NOT NULL,
    price           INTEGER NOT NULL,
    stock           INTEGER NOT NULL DEFAULT -1,  -- -1 = 無制限
    category        TEXT NOT NULL DEFAULT 'goods', -- goods / service / real_estate 等
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------
-- 取引履歴（すべての金の動きを1本のテーブルに集約）
-- from/to は 'user' / 'company' / 'system' のいずれか
-- 収支明細はこのテーブルを絞り込んで生成する
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS transactions (
    tx_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    from_type       TEXT NOT NULL CHECK (from_type IN ('user', 'company', 'system')),
    from_id         INTEGER,               -- system の場合はNULL可
    to_type         TEXT NOT NULL CHECK (to_type IN ('user', 'company', 'system')),
    to_id           INTEGER,
    amount          INTEGER NOT NULL,
    category        TEXT NOT NULL,         -- work / salary / transfer / purchase / capital 等
    memo            TEXT
);

CREATE INDEX IF NOT EXISTS idx_tx_guild_time ON transactions(guild_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tx_from ON transactions(from_type, from_id);
CREATE INDEX IF NOT EXISTS idx_tx_to ON transactions(to_type, to_id);
