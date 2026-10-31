# JukeBox101

此專案與 [yuipan0611](https://github.com/yuipan0611) 共同協作。

對外顯示名稱：**JukeBox101 v1.0.0 (Beta)**。此名稱對應已驗證的 **v7** 交付包；打包檔名、映像 tag 與程式技術版本保持原樣。夥伴交接清單見 `docs/deploy/partner-handoff-v1.0.0-beta.md`。

Discord 音樂交換 Bot。v7 起，**同一個 Bot 可以服務多個 Discord 伺服器**，正式資料庫為 **PostgreSQL 18**。

> 狀態（2026-10-30）：v7 為開發分支 `feature/postgresql-multi-guild` 的成果，交付包封裝時來源尚未 commit；後續提交以 Git 紀錄為準。真實 Discord 與正式資料庫尚未驗收。
> 程式已完成，並通過離線測試與 PostgreSQL 18.6 拋棄式資料庫的整合測試；這些結果不代表正式部署已驗收。

## 使用者說明（user）

### 怎麼加入

伺服器管理員使用 Owner 提供的**邀請連結**，把 Bot 加入自己的伺服器即可。

- 不需要 Owner 為每個伺服器另外設定 ID，也不需要重新部署。
- 一般使用者指令是全域指令；Owner 指令只註冊在 `DISCORD_OWNER_GUILD_ID` 指定的伺服器（見下方）。Bot 剛加入、或指令剛更新時，Discord 可能要一段時間才會在用戶端顯示，這不是故障。
- Bot 不需要任何頻道權限。只要成員在該頻道擁有「使用應用程式指令」權限，就能使用下列指令。

### 版本狀態：先看這裡

| | 目前正式版本（VPS 上線中） | 本 PR 新增、待部署功能 |
|---|---|---|
| 時長驗證 | Owner 手動補 | 自動向 YouTube 官方 API 取得；失敗時 Owner 可在 Discord 補 |
| 歌曲審核 | Owner 手動核准 | 認可頻道且符合全部條件時自動核准，其餘待 Owner 審核 |
| Nickel | **不會發放** | 資格齊全時自動 +1（需 Owner 開啟發獎模式） |

以下標示「待部署」的內容在 Owner 完成部署與開關前**都不會發生**。

### 一般使用者指令

所有回覆只有你自己看得到（ephemeral），也不會提及任何人。

#### `/share url:<YouTube 連結>`：投稿歌曲

```text
/share url:https://www.youtube.com/watch?v=cUfDOS2SINM
```

只接受單一 YouTube 影片連結；Spotify、播放清單專用連結或其他網站會被拒絕，不會寫入任何資料。

| 情況 | 目前正式版本 | 本 PR（待部署） | Nickel |
|---|---|---|---|
| 新投稿，待驗證或待審核 | 回覆已儲存、狀態與操作編號 | 回覆審核狀態與尚待的條件 | 不變 |
| 審核通過，該錄音版本的首位合格投稿者 | 不適用 | 回覆「已發放 +1 Nickel」（交易確認後才顯示） | **+1** |
| 審核通過但錄音版本未確認 | 不適用 | 回覆「待 Owner 確認錄音版本」，確認後自動結算 | 暫不變 |
| 有更早的投稿尚未處理完 | 不適用 | 回覆「正在等待較早的投稿」，前者解決後自動重新處理 | 暫不變 |
| 同影片再次投稿、或同錄音的其他影片（如 MV 與 Audio） | 不發放 | 回覆「重複不發放」 | 不變 |
| 時長不在 60～720 秒（含邊界） | 由 Owner 處理 | 官方時長超出範圍即審核未通過 | 不變 |
| 影片已刪除、未公開、直播或預告中 | 由 Owner 處理 | 保留待處理，不視為投稿者違規 | 不變 |
| YouTube API 或資料庫暫時失敗 | 回覆暫時無法使用 | 投稿已存則保留，系統自動重試；**請勿重新投稿** | 不變 |
| 回覆顯示「結果未確認」 | 請勿重新投稿，用 `/history` 查詢 | 同左；系統以原操作查回結果，不會重複發放 | 依查回結果 |

Nickel 只因投稿的歌曲資格而發放，與觀看、按讚、訂閱或留言無關。

#### `/balance`：查詢 Nickel

```text
/balance
```

顯示你目前的可用 Nickel，不會改變餘額。餘額跨伺服器共用。

#### `/history`：查詢自己的投稿

```text
/history
```

列出你最近的投稿、影片連結與審核狀態；待部署版本另顯示獎勵狀態（已發放 +1、待確認版本、待較早投稿、不發放等）。不改變 Nickel，也不會顯示其他人的投稿。

### 未開放的功能（不可使用）

- `/exchange`（交換）、`/daily mode:on|off`（每日推薦）：指令存在，但只回覆尚未開放；不扣 Nickel、不保存設定、不發私訊。
- `/claim`（Nickel 領取）與補償功能：預設關閉，Discord 中看不到 `/claim`。
- Spotify 投稿。

### 其他使用限制

- 你的帳號在所有伺服器中是同一個，投稿與餘額跨伺服器共用；不會顯示其他伺服器的名稱、頻道或成員。
- 不支援私訊（DM）。在私訊中使用指令會被拒絕，也不會執行任何操作。

### Owner 管理指令（一般使用者無法使用）

只在 `DISCORD_OWNER_GUILD_ID` 指定的伺服器註冊，且每次驗證 Owner 身分。除 `list`、`show` 外都是「預覽 → 按確認」，確認按鈕 5 分鐘內有效且只能使用一次；案件已變更時需重新預覽。

| 指令 | 用途 | 狀態 |
|---|---|---|
| `/owner list` | 列出待處理案件 | 已上線 |
| `/owner show operation:<案件>` | 查看案件證據與歷程 | 已上線 |
| `/owner approve operation:<案件> citation:<引用> reason:<理由>` | 核准為歌曲 | 已上線 |
| `/owner reject operation:<案件> citation:<引用> reason:<理由>` | 拒絕 | 已上線 |
| `/owner confirm_version operation:<案件> reason:<理由> [version_name:<版本>] [source:<來源>] [evidence_id:<證據>]` | 確認錄音版本（新證據時需填版本參考與來源，依指令說明） | 已上線 |
| `/owner reexamine operation:<案件> reason:<理由> [version_name:<版本>] [source:<來源>] [evidence_id:<證據>]` | 正式重審 | 已上線 |
| `/owner duration operation:<案件> seconds:<秒> source:<來源> reason:<理由>` | 在 Discord 補完整影片時長 | **待部署** |

待部署且開啟發獎模式後，確認會在回覆中說明結算結果或尚待條件；Owner 不需操作 ledger 或輸入內部狀態。

Owner 最短流程與啟用步驟見 [自動審核與發獎部署](docs/deploy/auto-review-nickel-enable.md)。

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

### CI/CD 雲端工作分支（待審查，正式 CD 關閉）

新增 PR/main CI、主程序 Gateway 健康訊號與隔離回滾演練。這些檔案尚未安裝到正式 VPS，未發布 GHCR，也未改動正式 Bot／DB。驗證層級、實際測試結果及啟用阻擋項目見 [雲端 CI/CD 交接](docs/deploy/cloud-cicd-2026-10-30.md)。

### 唯一部署入口

**`docs/deploy/v7-postgresql-deployment.md`**：涵蓋外部 PostgreSQL 準備、TLS、角色與秘密、migration、Compose、邀請連結、指令同步、健康檢查、備份與還原，以及人工驗收。

| 主題 | 文件或指令 |
|---|---|
| 資料庫一次性設定（由 DBA 執行） | `docs/deploy/postgres-setup.sql` |
| Schema migration | `docker compose --profile migrate run --rm migrate plan`，確認後再執行 `... migrate apply --apply` |
| 就緒檢查（唯讀） | `python -m bot.readiness`（DB-only）；新版 Compose 用 `python -m bot.healthcheck` 同時檢查主 Gateway |
| 邀請連結 | `python -m bot.invite_link --application-id <id>` |
| 指令同步 | `python -m bot.command_sync preview` 會分別列出全域指令與 Owner 指令（`owner_guild`，未設定 `DISCORD_OWNER_GUILD_ID` 時為 `null`）及各自的 digest；確認後才執行 `apply`（需要明確旗標與對應 digest）。一般指令用 `--target global`；Owner 指令用 `--target owner-guild`，只覆寫 Owner 伺服器的指令集，不會同步到全域 |
| 舊 MySQL 資料 | `docs/deploy/mysql-to-postgresql-migration-plan.md`（**未執行**的方案） |

### 架構重點

- **資料庫**：PostgreSQL 18（實測 18.6），驅動為 psycopg 3.3.6 加 Debian libpq5，使用原生參數化 SQL，不使用 ORM。
  - 所有資料表放在專屬 schema `jukebox101`，連線預設 TLS `verify-full`。
  - 角色分為 migrator（schema 擁有者）、app（Bot 使用，最小權限、不能 DELETE）與 readonly（只讀）。
- **多伺服器**：
  - 每次互動都檢查 Application 是否相符、是否來自伺服器（拒絕 DM），以及 ID 是否在可儲存範圍內，沒有伺服器白名單。
  - Owner 審核指令只會註冊在選用設定 `DISCORD_OWNER_GUILD_ID` 指定的伺服器，並驗證 Owner 身分。
- **跨伺服器資料**：資料表沒有任何伺服器欄位。使用者、投稿、歌曲身分、首次發獎優先權與受理順序都是全系統共用。
- **功能旗標**：`JUKEBOX_FORMAL_EXCHANGE`、`JUKEBOX_NICKEL_CLAIM`、`JUKEBOX_AUTO_REVIEW`、`JUKEBOX_AUTO_SETTLEMENT` 預設 off。
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
