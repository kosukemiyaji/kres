# KRES（空想国会住所・経済・調査システム）

## Render へのデプロイ

このリポジトリの `render.yaml` は、無料の **Web Service** として動作します。Bot 本体に `/health` エンドポイントを追加しているため、Render のヘルスチェックと外部監視のリクエストを受けられます。

1. このフォルダを GitHub リポジトリへ push する（`.env` は絶対に push しない）。
2. Render Dashboard で **New → Blueprint** を選び、対象リポジトリを選択する。
3. `DISCORD_BOT_TOKEN` の入力を求められたら、Discord Developer Portal で発行した Bot Token を入力する。
4. 作成・デプロイ後、Logs に `ログイン完了:` が表示されれば完了。

`render.yaml` はシンガポールリージョンの無料 Web Service 1 台と、`python bot.py` の起動を設定します。無料 Web Service は 15 分間の着信がないと停止するため、デプロイ後に UptimeRobot 等から `https://<Renderサービス名>.onrender.com/health` を **5 分間隔**で監視してください。

> 注意: 無料 Web Service には永続ディスクがありません。SQLite の DB は再デプロイ・再起動・スリープで消えます。本番データを保持するには、有料の永続ディスクか外部データベースへの移行が必要です。

住所マスタの Excel は `000925835.xlsx`（プロジェクト直下）または `data/000925835.xlsx` としてリポジトリに含めます。DB の `addresses` テーブルが空の場合、Bot の起動時に自動で取り込まれます。無料 Render で再起動して DB が消えた場合も、次回起動時に住所マスタは再作成されます。

```bash
python -m data.import_addresses /path/to/000925835.xlsx
```

ローカル開発では `DATABASE_PATH` を未設定にしておけば、従来どおり `db/econobot.db` を使用します。

## 1. 全体構造

```
econobot/
├── bot.py                    # エントリーポイント（Cogロード・DB接続）
├── requirements.txt
├── .env.example
├── db/
│   ├── schema.sql            # テーブル定義（DDL）
│   ├── database.py           # 非同期DBアクセス層（aiosqlite）
│   └── econobot.db           # 実行時に生成されるSQLiteファイル
├── data/
│   └── import_addresses.py   # 添付Excel → addressesテーブルへの取り込みスクリプト
└── cogs/
    ├── address.py             # 住所システム（1）
    ├── economy.py             # 個人の稼ぐ機能・ロール給料（2）
    ├── company.py             # 会社設立・雇用・給料自動払い（3）
    ├── shop.py                 # 消費（購入）機能（4）
    └── ledger.py               # 収支明細発行（5）
```

**設計方針**：
- discord.py は非同期フレームワークなので、DBアクセスも `aiosqlite` で統一し、コマンド処理をブロックしない。
- 「金が動く処理」は必ず `Database.transfer()` という単一の関数を経由させる。個人→個人、個人→会社、会社→個人、system→個人（労働報酬など）を同じ関数で扱うことで、**残高更新と履行履歴の記録を必ず1セットで行い、明細機能の整合性を保証**している。
- 将来的にFirebase（Firestore）へ移行する場合も、`Database` クラスのインターフェース（`get_user`, `transfer`, `get_statement` 等）はそのまま流用し、内部実装だけ Firestore SDK に置き換える設計にしておくと移行が楽です（後述）。

## 2. データベース設計

| テーブル | 役割 |
|---|---|
| `addresses` | 総務省「全国地方公共団体コード」を取り込んだ住所マスタ。都道府県コード＋市区町村コード＝団体コード(6桁)。政令指定都市の区は別シートから追加投入。 |
| `users` | Discordユーザーごとの残高・住所・最終稼働時刻 |
| `companies` | 会社（法人）の残高・代表者・本店所在地 |
| `employments` | 会社と従業員の雇用関係（役職・給料・支給間隔） |
| `role_salaries` | ロールに紐づく給料設定（役職ロールからの自動給与） |
| `shop_items` | 個人/法人が出品する商品・サービス・不動産 |
| `transactions` | **すべての金の動きの唯一の記録場所**。収支明細はこのテーブルの絞り込みで生成する |

添付いただいたExcel（`R6.1.1現在の団体` / `R6.1.1政令指定都市` の2シート構成）は実際に検証済みで、
`data/import_addresses.py` で以下のように取り込めます：

```bash
python -m data.import_addresses /path/to/000925835.xlsx
# → 都道府県・市区町村 1794件 + 政令指定都市の区 171件 = 1965件
```

政令指定都市の区（例：札幌市中央区）は「R6.1.1現在の団体」シートには単独で載っておらず、
「R6.1.1政令指定都市」シート側にのみ存在するため、両シートを合成して取り込む実装にしています。

## 3. 主要コマンド一覧

| コマンド | 説明 |
|---|---|
| `/address search <keyword>` | 都道府県名・市区町村名から団体コードを検索 |
| `/address set <code>` | 自分の住所を団体コードで設定 |
| `/address show [user]` | 住所の確認 |
| `/work` | 働いてコインを獲得（30分クールダウン） |
| `/balance [user]` | 所持コイン確認 |
| `/pay <user> <amount>` | 個人間送金 |
| `/salary set <role> <amount> <interval_min>` | ロール給料の設定（管理者） |
| `/salary manager_role <role>` | 給与設定を変更できるロールを指定（Discord管理者のみ） |
| `/company create <name> <capital>` | 出資して会社設立 |
| `/company hire <company_id> <user> <position> <salary> <interval_min>` | 従業員雇用 |
| `/company info <company_id>` | 会社の残高・従業員一覧 |
| `/shop list <name> <price> ...` | 商品・サービスの出品（個人/法人） |
| `/shop browse` | 出品一覧 |
| `/shop buy <item_id>` | 購入（消費） |
| `/statement [user]` | 個人の収支明細 |
| `/company_statement <company_id>` | 法人の収支明細 |

### ロール給料の権限設定

Discord の管理者権限を持つ人が、最初に `/salary manager_role role:<給与管理ロール>` を実行します。その後は、指定されたロールを持つ人だけが `/salary set` で給料額・支給間隔を変更できます。給与は設定した間隔ごとにロール所持者へ支給されます。

## 4. 実行方法

```bash
pip install -r requirements.txt
cp .env.example .env   # DISCORD_BOT_TOKEN を設定
python -m data.import_addresses /path/to/000925835.xlsx   # 住所マスタの初回投入
python bot.py
```

## 5. Firebase（Firestore）を使う場合の設計メモ

無料枠のFirestoreを使う場合、テーブル構造はそのままコレクション構造に対応させられます：

- `addresses/{code}`
- `users/{guildId}_{userId}`
- `companies/{companyId}` / サブコレクション `employments`
- `transactions`：`guildId` と `createdAt` で複合インデックスを張り、`where('fromId','==',...) or where('toId','==',...)` は
  Firestoreが `OR` クエリを直接サポートしないため、**2回クエリして結合する**か、各ドキュメントに
  非正規化した `participantIds: [fromId, toId]` 配列フィールドを持たせて `array-contains` で1回のクエリにするのがおすすめです。
- 給料の定期支払いは Cloud Functions の `pubsub.schedule`（例: 1分ごと）で本Botの `payroll_loop` と同等の処理を実行するか、
  Bot側で `discord.ext.tasks` を使い続けてFirestoreに対して読み書きする形でも問題ありません（今回の実装はこの後者の構成に相当します）。

SQLiteはシングルプロセス・低同時実行数のBotであれば十分な性能で、かつローカルでのデバッグが容易なため、
**まずSQLiteで機能を固め、複数サーバー展開やスケールが必要になった段階でFirestoreに移行する**のが実用的です。

## 6. 今後拡張しやすいポイント

- `transactions.category` を増やせば、税金・罰金・不動産購入など新しい収支種別も明細に自動反映される
- `shop_items.category = 'real_estate'` として購入すると同時に `users.address_code` や専用の `properties` テーブルを更新すれば不動産所有機能に拡張できる
- ロール給料は簡易的な「分の剰余判定」なので、厳密な支給管理をしたい場合は `role_salaries` にも `last_paid_at` を持たせ、`employments` と同じロジックに揃えると良い
