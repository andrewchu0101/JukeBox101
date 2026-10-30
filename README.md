# JukeBox101

對外顯示名稱：**JukeBox101 v1.0.0 (Beta)**。此名稱對應已驗證的 **v7** 交付包；打包檔名、映像 tag 與程式技術版本保持原樣。夥伴交接清單見 `docs/deploy/partner-handoff-v1.0.0-beta.md`。

Discord 音樂交換 Bot。v7 起，**同一個 Bot 可以服務多個 Discord 伺服器**，資料庫使用 **PostgreSQL**（原 v7 驗證 18.6；指定 Supabase 環境已補驗證 17.11）。

> 狀態（2026-10-30）：v7 為開發分支 `feature/postgresql-multi-guild` 的成果，交付包封裝時來源尚未 commit；後續提交以 Git 紀錄為準。真實 Discord 與正式資料庫尚未驗收。
> 程式已完成，並通過離線測試與 PostgreSQL 18.6 拋棄式資料庫的整合測試；這些結果不代表正式部署已驗收。

## 使用者說明（user）

### 怎麼加入

伺服器管理員使用 Owner 提供的**邀請連結**，把 Bot 加入自己的伺服器即可。

- 不需要 Owner 為每個伺服器另外設定 ID，也不需要重新部署。
- 指令是全域指令。Bot 剛加入、或指令剛更新時，Discord 可能要一段時間才會在用戶端顯示，這不是故障。
- Bot 不需要任何頻道權限。只要成員在該頻道擁有「使用應用程式指令」權限，就能使用下列指令。

### 目前可用

| 指令 | 用途 |
|---|---|
| `/share <url>` | 投稿 YouTube 影片連結，進入審核流程（只接受 YouTube）。投稿不會立即發放 Nickel |
| `/history` | 查看**自己**的投稿與目前審核狀態 |
| `/balance` | 查看**自己**的 Nickel 餘額 |

- 所有回覆都只有你自己看得到（ephemeral），也不會提及任何人。
- 你的帳號在所有伺服器中是同一個：在 A 伺服器投稿，到 B 伺服器用 `/history` 也看得到；餘額同樣跨伺服器共用。
- 不會顯示其他人的投稿，也不會顯示其他伺服器的名稱、頻道或成員資訊。
- 不支援私訊（DM）。在私訊中使用指令會被拒絕，也不會執行任何操作。

### 目前未開放

- `/exchange`（正式交換）、`/daily`（每日推薦）：指令存在，但只會回覆「尚未開放」，不扣點、不保存設定。
- `/claim`（Nickel 領取）與補償功能：預設關閉，Discord 中看不到 `/claim`。
- Spotify 投稿。

## 開發者說明（dev）

### 固定工作目錄與同步方式

目前這台開發電腦統一使用以下主專案目錄；Windows 與 WSL 的 `/mnt/c/...` 路徑是同一份檔案：

| 操作環境 | 專案路徑 |
|---|---|
| Windows／編輯器 | `C:\Users\student\Project\jukebox101-pg-multiguild` |
| WSL 終端機 | `/mnt/c/Users/student/Project/jukebox101-pg-multiguild` |

```bash
cd /mnt/c/Users/student/Project/jukebox101-pg-multiguild
git status
```

- 舊 WSL 路徑 `/home/gaston/jukebox101-pg-multiguild` 已改為指向主專案的符號連結，兩個入口讀寫同一份檔案，不需要手動複製或 pull 來同步。
- 原 WSL 目錄完整保留在 `/home/gaston/jukebox101-pg-multiguild-backup-*`；該目錄只作歷史備份，不在裡面開發或執行 Git 操作。
- `.git` 指標維持原樣。不要刪除或重設它；先前兩份獨立目錄共用 Git metadata，不能靠相同 commit 判斷檔案已同步。
- 交付包放在主專案的 `artifacts/jukebox101-v7/`，秘密檔另行保存，不納入 Git。檔案同步與 GitHub 推送是兩件事，新的修改仍須另行 commit／push。
- 本節是此電腦的本機路徑安排；夥伴部署位置依部署手冊自行設定。

### 唯一部署入口

**`docs/deploy/v7-postgresql-deployment.md`**：涵蓋外部 PostgreSQL 準備、TLS、角色與秘密、migration、Compose、邀請連結、指令同步、健康檢查、備份與還原，以及人工驗收。

指定 Supabase 專案已於 2026-10-30 建立專用 schema、角色及 25 張表，使用現有 `postgres` 資料庫；實際設定、驗證結果與尚待完成的外部連線步驟見 **`docs/deploy/supabase-environment-status.md`**。原 v7 壓縮包維持不變。

| 主題 | 文件或指令 |
|---|---|
| 資料庫一次性設定（由 DBA 執行） | `docs/deploy/postgres-setup.sql` |
| Schema migration | `docker compose --profile migrate run --rm migrate plan`，確認後再執行 `... migrate apply --apply` |
| 就緒檢查（唯讀） | `python -m bot.readiness`（Compose 的 healthcheck 也是用它） |
| 邀請連結 | `python -m bot.invite_link --application-id <id>` |
| 指令同步 | `python -m bot.command_sync preview`；確認後才執行 `apply`（需要明確旗標與 digest） |
| 舊 MySQL 資料 | `docs/deploy/mysql-to-postgresql-migration-plan.md`（**未執行**的方案） |

### 架構重點

- **資料庫**：PostgreSQL 18（實測 18.6），驅動為 psycopg 3.3.6 加 Debian libpq5，使用原生參數化 SQL，不使用 ORM。
  - 所有資料表放在專屬 schema `jukebox101`，連線預設 TLS `verify-full`。
  - 角色分為 migrator（schema 擁有者）、app（Bot 使用，最小權限、不能 DELETE）與 readonly（只讀）。
- **多伺服器**：
  - 每次互動都檢查 Application 是否相符、是否來自伺服器（拒絕 DM），以及 ID 是否在可儲存範圍內，沒有伺服器白名單。
  - Owner 審核指令只會註冊在選用設定 `DISCORD_OWNER_GUILD_ID` 指定的伺服器，並驗證 Owner 身分。
- **跨伺服器資料**：資料表沒有任何伺服器欄位。使用者、投稿、歌曲身分、首次發獎優先權與受理順序都是全系統共用。
- **功能旗標**：`JUKEBOX_FORMAL_EXCHANGE`、`JUKEBOX_NICKEL_CLAIM` 預設 off。
- **部署限制**：只能執行單一 Gateway 實例。

### 開發與測試

測試在 Python 3.12 容器中執行（`test.Dockerfile`，僅供開發使用）：

```bash
docker build -f test.Dockerfile -t jukebox101-test:v7-local .
docker run --rm --network none jukebox101-test:v7-local -q -m "not postgres"     # 離線測試
JB_LOCALTEST_PG_PASSWORD=<random> docker compose -f compose.test.yaml up -d postgres
JB_LOCALTEST_PG_PASSWORD=<random> docker compose -f compose.test.yaml --profile tests run --rm tests   # PostgreSQL 整合測試
docker compose -f compose.test.yaml down -v
```

- `postgres` 標記的測試只會連到 `cluster_name=jukebox101-disposable-test` 的拋棄式伺服器；沒有設定時會被跳過，跳過不算通過。
- 本機 TLS：先對測試容器執行 `PG_CONTAINER=jukebox101-localtest-postgres-1 scripts/dev/enable-test-tls.sh`，再跑整合測試；未設定 TLS 時相關測試會 skip，不算通過。
- 本輪完整結果：1747 項離線、35 項 PostgreSQL 測試通過且零 skip；封裝狀態見 `docs/deploy/v7-delivery-status.md`。
- 備份與還原的驗證腳本：`scripts/dev/verify-backup-restore.sh`。
- 程式規範：`CODING_STANDARD.md`。協作規則：`CLAUDE.md`。

### 歷史區

- 舊版 README（MySQL 階段的完整歷史）：`docs/history/README-v6-mysql-history.md`。
- MySQL 專用的程式、schema、migration 與測試：`legacy/mysql/`（只保存供追溯，預設測試與映像都不包含）。
- v6 的部署與交接文件：`docs/deploy/partner-deployment.md`、`owner-secrets.md`、`handoff-2026-10-30.md`（MySQL 版，檔頭已標示）。
