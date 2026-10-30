# 交接：JukeBox101 v7（PostgreSQL + 多 Guild）— 給 Codex

> 由 Claude 在工作中途寫下並持續更新。最後更新：2026-10-30（Asia/Taipei），階段：**程式與隔離測試完成；v7 Docker 交付包完成（以文末本輪核對與交付狀態為準）**。
> 本文件只記錄可查證的狀態；未列為「已驗證」的項目一律視為未完成。不含任何秘密。

## 0. 授權與邊界（使用者原話摘要，務必遵守）

- 授權：獨立 worktree／分支、本機程式與文件修改、專案隔離依賴、公開文件與套件／Docker 映像下載、Docker build、
  本輪專用可丟棄 PostgreSQL Docker 測試庫（獨立名稱、隨機臨時憑證、獨立 volume、若需連接埠只綁 127.0.0.1）。
- **不授權**：commit、push、merge、正式部署、雲端資源、費用、啟動正式 Bot／Gateway、Discord REST 同步、真實 Token、
  讀寫任何既有正式 MySQL、新增 Agent／Agent Teams、全域安裝或改永久 PATH、廣泛 docker prune。
- 保留：原工作樹 `/mnt/c/Users/student/Project/jukebox101`（分支 feature/owner-review-offline-checkpoint，
  README.md 有使用者未提交修改，**不得 reset／清理／帶入新版**）、v6-r4 包與映像、既有分支。
- /claim、正式 /exchange、補償旗標維持 off；單一 Gateway 實例；PDF 不隨包。
- 只有交付要求與已知缺口全部結案才可標 COMPLETE；COMPLETE 不等於正式部署驗收。

## 1. 位置

| 項目 | 路徑 |
|---|---|
| 新 worktree（WSL ext4） | `/home/gaston/jukebox101-pg-multiguild`，分支 `feature/postgresql-multi-guild`，基準 `e6c53731a309a83e011ca5d8b392b648808df5e5`，**未 commit** |
| 本機輔助（不在 repo） | `/home/gaston/jb-v7-work/`：`dk.sh`（以完整路徑呼叫 Docker Desktop docker.exe）、`offline.sh`（重建測試映像並跑離線套件）、`logs/` |
| 舊交付 | v6-r4：`/home/gaston/jb-deploy-pack-2026-10-30-v6-r4/`；打包工具 `/home/gaston/jb-deploy-pack-tools-2026-10-30-v6/`（可重用其 v7 打包流程） |

Docker：WSL Ubuntu 沒有開 Docker Desktop WSL 整合，**不能** bind mount WSL 路徑；改用 build context（`wslpath -w`）把程式帶進映像。
`docker.exe` 需以 `/home/gaston/jb-v7-work/dk.sh` 呼叫（程序層級 PATH/WSLENV 讓 docker-credential-desktop 可用）。

## 2. 決策（已定）

- PostgreSQL **18**（官方支援至 2030-11，實測映像 `postgres:18.6-bookworm`，id `sha256:5c4783a8…`）。
- 驅動 **psycopg 3.3.6（純 Python）+ Debian bookworm `libpq5`**；不用 psycopg-binary（授權可驗證：psycopg 為 LGPL-3.0-only，
  v7 包需附 psycopg sdist；libpq5 走 v6-r4 已驗證的 Debian 簽章原始碼流程）。不引入 ORM，延續 `%s` 參數化。
- 固定 schema `jukebox101`；連線選項 `search_path=jukebox101, TimeZone=UTC, statement_timeout=10s, lock_timeout=5s`；
  json 欄位以文字載入（`AdaptersMap` context），沿用既有 codec。
- TLS：預設 `verify-full`；允許 `verify-ca`；`disable` 只限隔離本機測試；拒絕 prefer/allow/require。
- 角色：migrator（schema owner，唯一可 DDL）、`jukebox101_app_role`（群組：SELECT/INSERT 全部使用表＋UPDATE 9 表，無 DELETE/TRUNCATE）、
  `jukebox101_readonly_role`（SELECT）。登入角色加入群組；授權寫在 migration 0002。
- Migration：`bot/database/postgres/0001..0003.sql` + `bot/database/migration_runner.py` + `python -m bot.migrate status|plan|apply --apply`；
  advisory lock、SHA-256 ledger `jb_schema_migrations`、每檔單一交易、拒絕 superuser／非 schema owner、拒絕被改過或未知版本。
  0003 只在空庫建立 jb2_control（snapshot_ready/legacy_quiesced=TRUE、settlement=FALSE），有資料即中止。
- 多 Guild：`bot/production_config.py`（Application/Owner 固定、`DISCORD_OWNER_GUILD_ID` 選用、忽略 `DISCORD_DEV_GUILD_ID`、
  snowflake 上限 2^63−1）、`bot/interaction_policy.py`（MultiGuildInteractionGuard：只收 slash command、app 相符、拒 DM、ID 可儲存）、
  `bot/discord_app.py`（全域指令，contexts=[0] guild、integration_types=[0]、default_member_permissions=None）、
  Owner 群組只在 `DISCORD_OWNER_GUILD_ID` 設定時註冊於該 Guild（OwnerGate 驗 guild+owner 不變）。
- 指令同步：`python -m bot.command_sync preview|fetch|apply`（fetch/apply 需旗標；apply 需 `--expected-digest` 等於 preview）。本輪不得執行 fetch/apply。
- 邀請連結：`python -m bot.invite_link --application-id <id>`，scope `bot applications.commands`、permissions=0、integration_type=0；需 Owner 開啟 Public Bot。
- 跨 Guild 資料：schema 原本就沒有 guild 欄位；餘額、投稿、歌曲、首獎優先權、受理序號全域共用；不保存 Guild 資料。
- MySQL 專屬模組、schema、migration、測試、PowerShell 腳本已 `git mv` 到 `legacy/mysql/`（歷史保存，pytest 不收集、映像不含）。

## 3. 已完成（程式已改，未全部驗證）

- 方言移植：connection、submission_transaction、repository、submission_store、reward_settlement、reward_history、
  submission_maintenance、exchange_reservation、nickel_grant_store、compensation_schema、compensation_store、services。
- 發現並修正既有潛在缺陷：`nickel_grant_store` 以 tuple 讀取，但正式連線為 dict rows（/claim 關閉中未觸發）。
- 測試替身：`tests/submission_persistence_fake.py` 支援 `RETURNING`、`ON CONFLICT`、`statement_timestamp()`、PG 目錄查詢字樣。
- 依賴：`requirements-docker.txt`、`dependency-versions.txt`（PyMySQL→psycopg==3.3.6）、新增 `requirements-test.txt`；
  pyproject marker `postgres`。
- 開發測試映像：`test.Dockerfile` + `test.Dockerfile.dockerignore`（開發專用，非部署）。

## 4. 驗證狀態（2026-10-30 實測，Python 3.12 容器 + PostgreSQL 18.6 拋棄式容器）

- 基準（e6c5373，離線）：2698 passed / 0 failed / 42 skipped（109 mysql deselected）。
- v7 離線回歸（`-m "not postgres"`）：**1723 passed / 0 failed / 3 skipped**（新增 test_multi_guild_entry 24 項含在後續回歸）。
- v7 PostgreSQL 真實整合（`-m postgres`，jb-v7-pg）：
  - test_postgres_migrations 9 passed（套用、重跑、竄改／未知版本拒絕、失敗整筆回復、superuser／非 owner 拒絕、advisory lock、
    0003 有資料即中止、app 最小權限、readonly 只讀）。
  - test_postgres_intake 13 passed（首次自動初始化、跨 Guild 同帳號、隱私、重播、不同 payload、8 執行緒並行順序 1..8、
    同操作並行、語句失敗回滾、COMMIT 後失聯→未知→重播、COMMIT 前失聯、伺服器端終止連線釋放鎖、未就緒拒絕、關閉功能無帳務寫入、
    ledger 一致、json verbatim／大小寫 ID／2^63−1）。
  - test_postgres_owner_review 3 passed（Discord Owner 群組 approve/reject/confirm_version；開啟結算時一版本一獎、原子、重複不發）。
    **途中發現並修正**：review_persistence `UPDATE ... JOIN`（MySQL 語法）→ `UPDATE ... FROM`。
  - test_postgres_sql_catalogue 1 passed：147 條常數 SQL 中 117 條由 PostgreSQL PREPARE 驗證；28 條補償表（刻意不建）只允許 undefined_table；
    2 條 SQLite 離線分支；10 條動態 SQL 未 PREPARE。
- 未做：TLS 真實驗證、備份／還原、readiness/migrate CLI 實測、部署檔與文件、完整回歸、v7 包。

## 5. 本輪核對與接續（Codex，2026-10-30）

原第 5 節已過時，移除作為待辦的效力：離線回歸、PG 測試基礎、部署檔與 TLS／備份還原在接手前已有成果。第 1 節 `/home/gaston/...` 是先前 WSL 工作位置；本輪實際工作目錄為 `/mnt/c/Users/student/Project/jukebox101-pg-multiguild`，既有 `.git` 指向原 worktree metadata，保持不變。

本輪重新從目前工作目錄建置，不沿用舊來源映像：

- 完整離線：**1747 passed，35 deselected，0 skipped / failed**。
- 真實 PostgreSQL 18.6：**35 passed，1747 deselected，0 skipped / failed**。含多 Guild 入口與全域帳號／投稿順序、migration、最小權限、Owner review（4）、Nickel store（4，正式旗標仍 off）、TLS verify-full 及 CLI。
- 備份還原：所有表摘要相同、migration 無 pending、還原後新投稿成功，序號延續為 3。
- 正式交付映像：`jukebox101-bot:v7`，`sha256:655fa3a4efc881218ea8b700823edec6ff973bc34c865c2ed22210065cbd31df`，linux/amd64，uid/gid 10001:10001。
- 正式映像使用新建的隨機專用測試 DB／角色，實際執行 status → plan → apply → status → readiness；verify-full TLS 成功，最後僅移除該專用 DB／角色，未啟動 Bot。
- 映像逐層稽核：8 層；`/app` 與目前 `bot/` 逐檔一致；0 blob 錯誤、0 路徑違規、0 新增未分類 blocking；5 個沿用 v6-r4 的上游 fixture 分類。
- 授權：19 個 Python 套件、109 個 Debian 套件；100 個保守判定 copyleft binaries → 70 個確切 Debian source packages，224 個原始碼檔、326080701 bytes，2 組 Debian 簽章索引，離線重驗 0 problems。psycopg 3.3.6 sdist 的 PyPI SHA-256 已核對，pip vendor notices/provenance 版本不變沿用。
- `docker load` 成功；Compose `--dry-run up -d --no-build` 無 build/pull；另查詢確認該 project 沒有任何容器或網路。
- 僅離線 `command_sync preview`，digest `119c5bc1a3b0999c9dca4649704c221fc179d4f5b19b9ca2989abc2dd9f24c86`；未呼叫 fetch/apply。

本輪證據：`artifacts/v7/evidence/`。工具：`scripts/dev/delivery/`、`scripts/dev/verify-runtime-postgres.py`。`scripts/dev/enable-test-tls.sh` 補上本機 Compose TLS 設定的可重現步驟（已另起全新專用 cluster 實跑，35 項 PG 測試再次全過且零 skip，該次專用資源已移除）；一般 `compose.test.yaml` 沒有 TLS 時會明確 skip TLS 測試，不可宣稱全部通過。

交付封裝與解壓閘門結果另記於 `docs/deploy/v7-delivery-status.md`；該文件存在且記為 COMPLETE 才代表交付包完成。不得將包完成解讀為正式部署驗收。

仍未執行（使用者邊界）：真實 Discord／新 Guild 人工驗收、正式外部 PostgreSQL、MySQL 舊資料搬遷、開啟正式 exchange/claim/補償、commit/push。全部既有 worktree、v6-r4 包與映像保留，未 reset／清理 `.git`。

## 6. 封裝後授權更新（2026-10-30）

使用者於交付完成及 Beta 命名後明確要求「commit and push」，授權提交與推送目前開發分支。前文的禁止 commit/push 為封裝當時的歷史邊界，本次僅此項已被新指示取代。正式 Bot、正式資料庫及費用限制仍維持。既有 v7 包、manifest、雜湊與其未提交來源快照保持原樣；後續 Git 提交亦包含包外 Beta 命名與交接文件。
