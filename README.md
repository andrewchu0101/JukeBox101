# JukeBox101

Stage-B-only 目前狀態：**STAGE_B_ONLY_OFFLINE_READY_FOR_REVIEW**。已在既有 Harness 接入單次新 Session、受控四步 FK Window、原 23 項套件與逐 Commit／精確 Manifest 清理稽核；完整離線回歸 1228 Passed／0 Failed／55 Skipped，PowerShell Mock 119 斷言 Passed。未連線或執行真實 Stage B；舊 Session 仍 Blocked。下一次的三段授權與最短步驟見 [Stage-B-only／私人 Guild 實跑](docs/stage-b-only-run.md)。這取代下文「尚需離線補接 supervisor」的歷史待辦，不代表 Batch B 或 Beta 真實驗收已通過。


目前私人 Beta 接線：已完成既有 `/share` → Batch B Pending 持久化、`/history` 本人投稿／目前審核狀態、`/balance` 原餘額查詢；正式入口強制指定私人 Guild／Application／Owner，缺設定 Fail Closed。**程式已接線／離線通過，不代表真實可用**。Session 仍 Blocked，Batch B 真實 MySQL 驗收未完成；本輪沒有外部操作，資料庫三開關維持 OFF。完整離線結果 1163 Passed／0 Failed／55 Skipped，PowerShell Mock 90 斷言 Passed。後續所需操作與授權見 [私人 Beta 接線紀錄](docs/private-beta-implementation.md)。


目前狀態（2026-10-16）：**RECOVERY_SUCCESS**。四條 FK DDL 與 DML Commit 全部 ACKNOWLEDGED；原 Manifest 15 PK 零殘留、非 Manifest 異動 0；Control 0／0、三開關 OFF；原 FK／Schema／Migration 全部恢復並驗證；Recovery Exit Code 0。依使用者提供的已驗收結果記錄，本輪未連線複驗。Session 仍 Blocked，Batch B 真實 MySQL 測試尚未完成，不標記通過或正式可用。

Python 3.12 Discord 音樂交換 Bot，採本機優先、可部署 Linux 的架構，使用 MySQL 8.0.16+、PyMySQL 原生參數化 SQL。Phase 1A～1D、Phase 2A.1 Repository 隔離整合已 Accepted；Phase 2A.2 Registration 人工 Passed；Phase 2A.3 DB-disabled Discord Interaction Transport 已 Accepted — User Verified。真實 Discord＋MySQL 持久化互動仍 Pending，Phase 2A 整體不標記全面完成。Phase 2B.1 及 Phase 2B.2 Implementation 01 的離線契約已實作並通過離線測試，兩者仍等待人工驗收；不代表真實影片驗證或 Nickel 業務已完成。

使用者分享通過驗證的新歌曲後取得額度，再交換其他使用者的歌曲或選擇每日 DM。這些玩法屬於後續階段。

## Phase 2B.1：離線資格契約

新增 `bot/song_identity.py`、`bot/eligibility.py`、`bot/review.py`，不接入現有 Discord、Service 或 Repository。重用 `parse_youtube_url`，合法單影片帶 `list`、`t`、`start`、`index` 或追蹤參數仍保留同一 Video ID 與 canonical URL；純 Playlist 拒絕，不展開清單。

可靠且明確為完整原始影片的時長，在 60～720 秒（含）得到 Duration PASS；界外 REJECT；未知、無效或不可靠證據維持 PENDING_VALIDATION。僅接受非負整數或有限 Decimal 秒數，不四捨五入，不支援 ISO 8601 字串或 float。來源與可靠性由離線呼叫者明確提供，這不是實際證據認證。

Duration PASS 只進入等待歌曲審核；AUTO／OWNER 核准共用硬性條件，保留決策版本並拒絕過期結果。重複投稿結果與審核狀態分開；未核准、未知或重複投稿不具備未來獎勵前置資格。此布林條件不是全域首次有效投稿證明，也不會實際發放 Nickel。

本次完整離線回歸 **452 Passed／0 Failed／0 Skipped**：主回歸 450 Passed／34 Deselected，另執行 2 項純文字 CHECK normalization。涵蓋既有 298 項及新增 154 項；34 項排除包含 32 項真實 MySQL 與另補跑的 2 項純文字案例。Windows asyncio 回歸需允許本機 socket pair；沙箱內首次執行停滯後中止，未列入成功結果。

真實中繼資料取得、歌曲自動判定、Owner 身分驗證、Nickel Ledger 交易、Discord／MySQL 持久化業務整合、Exchange 與 Daily 均未由本階段實作。YouTube API 獎勵政策仍 UNRESOLVED；不啟用 API 驗證或正式自動發幣。詳見 [Phase 2B.1 驗收文件](docs/phase2b1-offline-acceptance.md)。完成後停止，不自行開始 Phase 2B.2。

## Phase 2B.2 Implementation 01：版本身分與投稿優先權

新增 `bot/version_identity.py`、`bot/reward_candidates.py`、`bot/share_replies.py`，保持 Phase 2B.1、Parser、Service、Repository、Schema 與 Discord handlers 不變。可信內部呼叫者明確提供版本映射與證據；同錄音 MV／Audio 可映射相同版本，具有可信證據的不同官方 Remix 可映射不同版本。不以標題、Video ID 或 Remix 字樣猜測身分。

候選依模擬的資料庫成功受理序號排序；後投稿即使先 Approved 仍等待較早的同版本投稿。較早未知身分須有明確版本排除證據才解除阻擋，Pending 不因 24 小時或任何時間逾期而失去優先權。歷史收錄版本不開放首次獎勵；證據衝突暫停受影響未結算候選，既有 AWARDED 事實不追回。READY 只是離線結算必要條件，沒有帳務操作。

五個情境代碼與五句使用者核准台詞獨立存於 presentation adapter，逐字固定、不新增隨機台詞，也未部署或接入 Discord。情境回覆不能單獨代表歌曲核准、首次歸屬或已發幣。

本子階段完整離線回歸 **530 Passed／0 Failed／0 Skipped**：主回歸 528 Passed／34 Deselected，另補跑 2 項純文字案例。包含原有 452 項及新增 78 項。32 項真實 MySQL 案例未執行；沒有外部服務操作或 MySQL 可靠受理順序實作。YouTube API 政策仍 UNRESOLVED。詳見 [Implementation 01 驗收文件](docs/phase2b2-implementation01-acceptance.md)。Phase 2B.2 整體未完成，後續子階段需另行授權。

## 安裝與設定

### Phase 2B.2 Batch A：完整離線投稿協調

新增 `submission_workflow.submit_offline` 與 `submission_snapshot`，串接既有 URL、完整影片時長、Hybrid Review、版本身分、歷史與前序獎勵候選決策。獨立 manifest 核對快照漏項；未知歷史與證據衝突保守阻擋，技術重送保留可信原回執且不建立新事件意圖。核准台詞採獨立唯讀情境池，可注入隨機來源；五句文字未改、未部署。

狀態為 **Implemented／Offline Tested／Manual Acceptance Pending**。Batch A 當時明列安全離線回歸 **436 Passed／0 Failed／0 Skipped**，不是歷史完整套件重跑。來源完整性、可靠受理序號、實際事件保存、唯一 reward Claim、Ledger 與並行交易在 Batch A 未實作；後續 Batch B 已新增其 Repository 程式與離線證據，尚無真實 MySQL 驗證。YouTube API 政策仍 UNRESOLVED，Phase 2B.1／2B.2 整體不標記人工通過。原契約見 [Batch A 驗收文件](docs/phase2b2-batch-a-acceptance.md)。

### Phase 2B.2 Batch B：持久化與原子 Nickel 結算程式

真實驗收改由使用者本機 PowerShell 執行；Codex 不需取得密碼。先執行 [第一輪唯讀 Preflight](docs/phase2b2-batch-b-user-run.md)，貼回報告後再準備 Migration 與真實交易測試。新 Preflight 防護已離線測試，安全回歸 514 Passed；尚無真實 MySQL 驗收結果。

新增獨立 `SubmissionRepository`、增量 Migration 計畫與離線交易測試。全域 InnoDB 控制鎖配置成功受理序號；原回執支援重送／不確定 Commit 重試；完整 Snapshot、Owner revision／證據歷史、版本合併例外與 UTC 待審摘要持久化程式已完成。原子獎勵交易協調唯一版本 Claim、既有 Ledger、餘額、候選狀態及結算結果，重用 Batch A 規則。

狀態為 **Implemented／Offline Tested／Real MySQL Integration Pending／Manual Acceptance Pending**。明列安全回歸 **492 Passed／0 Failed／0 Skipped**，其中新增 Batch B 56 項；Fake／Mock 不代表 InnoDB 或 DDL 已驗證。原六表 Schema 不變，新增 17 個表（含版本登錄表）；未執行 Migration 或連線任何資料庫。快照／舊 writer 隔離／發獎開關均預設關閉，未接正式 Discord 或實際發幣。Migration runner 僅容許固定測試庫身分，仍需後續人工授權；YouTube API 政策未解決前不啟用正式自動獎勵。完整契約、命令、DDL 復原與整合驗收待辦見 [Batch B 驗收文件](docs/phase2b2-batch-b-acceptance.md)。Batch C 未開始。

使用 Python 3.12.10，在專案根目錄執行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install "PyMySQL>=1.1,<2" "python-dotenv>=1,<2" "discord.py>=2.4,<3" "pytest>=8.4,<9"
```

Linux 使用 `.venv/bin/python`。已安裝的實際版本（包含間接依賴）記錄於 `dependency-versions.txt`；它是本次 Windows 環境紀錄。

僅在 `.env` 不存在時，由使用者複製 `.env.example` 成 `.env`，自行填入 DB_PASSWORD。不要覆寫現有檔案、不要提交憑證。DB_HOST=127.0.0.1、DB_PORT=3306、DB_USER=jukebox101_app、DB_NAME=jukebox101。離線測試與 Bot 初始化不需要 Discord Token；明確啟動時必須設定 DISCORD_BOT_TOKEN。

`load_database_settings()` 只讀取環境；本機可明確傳入 `Path('.env')`，檔案設定不覆蓋平台環境值。雲端直接設定環境變數即可。缺少必要 DB 設定會以設定名稱報錯，不輸出值；root 帳號會被拒絕。禁止記錄設定字典、原始連線參數、密碼或 Token。

## 結構

Phase 2A.3 DB-disabled Gateway 入口 `python -m bot.gateway_acceptance` 的 transport 已由使用者確認驗收；預設仍只有離線 preflight，`listen --authorize-gateway` 才能登入、唯讀核對遠端參數並連線。它重用五組原 handlers，於 CommandTree dispatch 前核對指定 Guild／Application，五組回覆明確不持久化，不使用 DB_*／TEST_DB_*。安全操作與 User Verified 證據限制見 [Phase 2A.3 報告](docs/phase2a3-gateway-acceptance.md)；本次文件收尾不授權重新啟動或重跑 Guild sync。

Phase 2A.2 新增 `bot/acceptance_config.py`、`bot/guild_acceptance.py`、`bot/discord_acceptance.py` 及 `tests/test_guild_acceptance.py`。使用 [Discord 開發 Guild 驗收流程](docs/phase2a2-discord-acceptance.md) 中的安全 PowerShell function 提供 masked Token、明確 Guild／Application ID；預設執行 `python -m bot.discord_acceptance preflight` 完全離線。登入與 Guild sync 各須額外 opt-in，三模式都停用資料庫與 Gateway，不能以正式 `python -m bot` 代替此驗收入口。

```text
bot/
  __init__.py
  __main__.py
  acceptance_config.py
  acceptance_runtime.py
  command_contract.py
  config.py
  development_command_contract.json
  discord_acceptance.py
  discord_app.py
  gateway_acceptance.py
  gateway_listener.py
  gateway_service.py
  guild_acceptance.py
  models.py
  services.py
  urls.py
  database/
    __init__.py
    connection.py
    repository.py
    schema.sql
tests/
  __init__.py
  conftest.py
  mysql_safety.py
  test_foundation.py
  test_mysql.py
  test_mysql_readonly.py
  test_mysql_safety.py
  test_schema_static.py
  test_discord_app.py
  test_repository.py
  test_services.py
  test_urls.py
  test_phase2_quality.py
  test_guild_acceptance.py
  test_gateway_acceptance.py
  repository_mysql_support.py
  repository_mysql_concurrency.py
  repository_mysql_counts.py
  test_repository_mysql.py
  test_repository_mysql_concurrent.py
  test_repository_acceptance_safety.py
pyproject.toml
.env.example
.gitignore
dependency-versions.txt
README.md
ROADMAP.md
AGENTS.md
```

## Schema 設計與人工建立

`bot/database/schema.sql` 為 Phase 1 v1，僅供人工審查執行。六張表均採 InnoDB / utf8mb4，時間欄位使用 UTC DATETIME(6)。每日派送日期明確依 Asia/Taipei 日曆計算。

| 表 | 用途及約束 |
|---|---|
| users | Discord BIGINT UNSIGNED 主鍵、非負額度、每日設定 |
| songs | 獨立 song_id；大小寫敏感平台/影片 ID 唯一；來源、待驗證狀態、YouTube 目標與驗證依據 |
| submissions | 去重後的投稿嘗試紀錄；使用者/歌曲唯一，不代表接受或獲獎 |
| recommendations | 使用者/歌曲唯一；預約、發送、成功、失敗、不確定狀態及成功時間 |
| credit_ledger | 全域冪等鍵、每首歌唯一獎勵、每次推薦的扣款/退款各最多一次 |
| daily_dispatches | 每人每日期唯一、與推薦擁有者一致的複合外鍵、嘗試次數與恢復狀態 |

所有歌曲外鍵指向內部 song_id。全域首次有效提交的額度以 `credit_ledger.awarded_song_id` 唯一约束防重；提交紀錄本身不代表已獲額度。未來服務須鎖定 users 與歌曲列，在同一交易內驗證資格、寫 ledger 及更新 balance。Schema 無法自行證明影片是歌曲、驗證網址或保證餘額等於 ledger 總和。

`submissions` 明確定義為對已取得影片識別之歌曲的去重投稿嘗試，可能對應 pending、verified 或 rejected 歌曲；它不是所有原始請求的事件日誌，無法解析的輸入與同一使用者的重試不另外建列。不同使用者可各自留下同影片的嘗試，不會因此各獲一次額度。只有第一個通過資格驗證的有效提交可寫入唯一歌曲獎勵，接受與授予額度仍須未來服務在同一交易處理。

本次批准的 AGENTS.md 例外：YouTube Video ID 是目前唯一外部識別，同 ID 重複，不同影片暫視為不同項目，即使音樂相同。Spotify、作品層级去重及跨平台識別延後。未來需版本化遷移，把平台連結分離到身分映射表，保留來源與證據，審查合併對歷史、額度及唯一約束的影響；不可直接重寫或刪除既有紀錄。

`platform_track_id VARCHAR(11)`、ascii_bin 大小寫敏感比較、11 字元長度 CHECK 及 youtube 平台 CHECK 僅適用本階段。Spotify 整合前必须審查 Schema Migration：支援其識別長度、調整平台與長度約束、建立平台連結映射，同時保留 song_id 外鍵與既有資料。不能直接把 Spotify ID 塞入現有欄位，也不能重跑 CREATE TABLE IF NOT EXISTS 當作遷移。

建表順序為 users → songs → submissions → recommendations → credit_ledger → daily_dispatches，各外鍵目標均先建立。Phase 1A 移除 daily_dispatches 的單獨 song_id 外鍵；非 pending 狀態必須有完整推薦/歌曲 ID，複合外鍵已透過 recommendations 驗證歌曲與收件人。pending 必須兩個 ID 皆為 NULL。

recommendations 的兩個以 recommendation_id 起首之 UNIQUE 索引分別供 ledger 的收件人複合外鍵、daily 的收件人/歌曲複合外鍵使用，因此保留明確的完整唯一目標；它們雖與主鍵重疊，並非相同欄位的重複索引。收件人歷史、ledger 歷史及派送恢復索引對應不同查詢。MySQL 自動生成之外鍵支援索引仍需建表後以 SHOW INDEX 核對，靜態檢查不能確認實際索引。

人工流程：

1. 在 Workbench 使用管理帳號選取既有 `jukebox101`，確認目前 Schema 與備份政策。
2. 檢閱 schema.sql，確認 MySQL >=8.0.16（CHECK 約束需支援強制執行）。已有同名表時先比較 `SHOW CREATE TABLE`，不要直接假設相容。
3. 手動執行 SQL；不建立資料庫、帳號或調整權限。DDL 可能隱含提交，應逐項核對執行結果。
4. 檢查六張表的 `SHOW CREATE TABLE`、外鍵、索引與 CHECK 約束。`IF NOT EXISTS` 僅略過同名表，不能驗證或修正舊結構。
5. 切回 `jukebox101_app@127.0.0.1` 執行唯讀連線測試，確認身分與資料庫；完成後才標示 Schema ready。

## 連線與測試

正式庫身分檢查使用 Phase 1C 獨立唯讀入口，不需要 Discord Token：

```powershell
$env:JUKEBOX_RUN_MYSQL_READONLY = '1'
.\.venv\Scripts\python.exe -m pytest tests/test_mysql_readonly.py::test_local_application_identity -q --basetemp=.pytest_tmp
```

預設測試使用 mock，整合測試預設跳過：

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest_tmp
```

`test_schema_static.py` 檢查已知關鍵字連寫錯誤、指定宣告、建表依賴順序、唯一鍵、派送 NULL 配對與重複明確索引。這些是針對目前 SQL 格式的文字回歸檢查，不是完整 SQL parser，不能證明 MySQL 語法或約束已實際生效。可選整合測試另涵蓋派送配對、收件人複合外鍵及成功時間約束，仍需人工建表與隔離環境。

Phase 1D 已將 `tests/test_mysql.py` 限定到獨立 `jukebox101_test` 與 `jukebox101_test_app@127.0.0.1`；正式庫絕對禁止寫入整合測試。只讀 TEST_DB_* 環境值，舊開關與 .env 都不會啟用此入口。人工建庫 SQL、帳號權限與未來執行指令見 [隔離測試準備](docs/mysql-test-preparation.md)。使用者已人工確認測試庫建立；本次最終審查僅靜態／mock 驗證，未執行 14 項案例。

連線使用 DictCursor、utf8mb4、明確 timeout、UTC session 及 autocommit=False。transaction context 在成功時 commit，包含 commit 失敗在內的例外時 rollback，最後 close；錯誤不轉成成功。整個 context 必須留在同一工作執行緒。Phase 2A 服務已使用 `asyncio.to_thread` 包住完整 repository 工作單元，不能只把連線建立送到執行緒後在事件迴圈操作。

### Phase 1C 獨立唯讀驗證入口

Phase 1C 沒有執行 `tests/test_mysql.py` 或 `-m mysql` 整合集合。集合共 14 案例，其中 12 項透過 isolated_mysql_rows 寫入，即使 rollback 仍不是唯讀。Phase 1D 已淘汰舊 JUKEBOX_RUN_MYSQL、JUKEBOX_SCHEMA_READY 啟用方式；新的隔離開關與固定測試目的地參見準備文件。

新入口 `tests/test_mysql_readonly.py` 使用獨立 mysql_readonly marker 和 JUKEBOX_RUN_MYSQL_READONLY 開關，從專案根目錄 .env 載入設定；測試暫時排除既有 DB 環境值以確認實際使用該檔案，不輸出或另存密碼。連線前限制目的地為 127.0.0.1:3306、jukebox101_app、jukebox101。

```powershell
$env:JUKEBOX_RUN_MYSQL = '0'
$env:JUKEBOX_DISPOSABLE_DATABASE = '0'
$env:JUKEBOX_RUN_MYSQL_READONLY = '1'
.\.venv\Scripts\python.exe -m pytest tests/test_mysql_readonly.py -m mysql_readonly -q --basetemp=.pytest_tmp --tb=short
```

連線建立後執行 START TRANSACTION READ ONLY；只執行固定 SELECT 查詢身分及 information_schema，結束 rollback / close。驅動也設定 session 時區和 autocommit，不執行 INSERT、UPDATE、DELETE 或 DDL，不查詢應用程式資料列。驗證涵蓋六表 InnoDB、六個主鍵、十個唯一鍵、九個外鍵的欄位與目標，以及八個 CHECK 的存在、ENFORCED=YES 與正規化後文字內容。文字比較只處理目前 Schema 的 MySQL 顯示格式，不是一般 SQL 語意等價驗證。

pytest 預設 addopts 也指定 --basetemp=.pytest_tmp；.gitignore 已排除該目錄。此目錄專供 pytest 暫存，pytest 可能清理其中內容，不應存放人工資料。

## 派送與恢復設計（尚未實作）

預約時鎖定 users，排除自己的提交及已保留/送過的歌曲，檢查額度，在短交易內保留推薦、扣款與記帳。每日作業以使用者/本地日期唯一列防重；無候選歌曲時不扣款。失敗預約保留歷史，若允許重試應復用原推薦列。

發送前持久化 sending，交易結束後呼叫 Discord；成功後記錄 message ID、delivered_at 及 delivered。明確未接受的失敗可在另一交易內以唯一退款鍵退款，並標示 failed。退款後若重新發送，需另行設計重新保留額度政策，不可直接無額度重送。

daily 的 pending 表示尚無推薦預約；recommendations 的 reserved 表示已有預約。進入 sending 時應在同一短交易中附加 daily 的推薦/歌曲並協調兩表狀態；後續 delivered、failed、uncertain 也需同交易更新。複合外鍵只強制收件人與歌曲一致，CHECK 只驗證各列形狀，無法跨表強制 status 相等或合法狀態轉移；此責任屬後續服務，不能由目前靜態檢查宣稱已完成。

程序在 sending 中重啟、網路 timeout，或 Discord 已接受但成功狀態寫入失敗，皆可能屬於 uncertain。不得盲目重送或退款；需保留嘗試與調查證據，待人工處理或未來經驗證的 reconciliation。資料庫與 Discord API 不是同一原子交易，無法無條件保證外部訊息恰好一次。

## 進度、限制與驗收

最新收尾狀態如下；細節、程式碼缺口及待決產品規則見 [Phase 2A Closeout](docs/phase2a-closeout.md)。本次只更新文件與唯讀審查，沒有重跑離線測試、真人操作或 MySQL 測試。

| 範圍 | 最新狀態／證據 |
|---|---|
| 2A.1 Repository 隔離整合 | Accepted；13 Passed／0 Failed／0 Skipped，前後六表全 0；限 rollback-only SQL／鎖測試 |
| 2A.2 Registration | Offline Preflight、REST Login、Development Guild Sync、五指令顯示均人工 Passed |
| 2A.3 連線前置 | 人工 Offline Preflight、Application／Guild 核對、Gateway Ready Passed；Remote Command Contract Matched |
| 2A.3 DB-disabled Interaction Transport | **Accepted — User Verified**；使用者確認五組真人互動完成 |
| 離線測試 | 歷史 298 Passed／0 Failed／0 Skipped，本次未重跑 |
| 真實 Discord＋MySQL 持久化互動 | Pending；分開的 Repository／transport 驗收不能替代端到端驗收 |
| 歌曲資格／發點／交換交易／Daily 派送與恢復 | 未實作；pending 投稿、查詢與 Daily 偏好 SQL 已有基礎，但不是完整業務 |
| Phase 2A 整體／Phase 2B | 整體不標記全面完成；Phase 2B.1 與 2B.2 Implementation 01 僅離線契約已實作／測試通過，人工驗收均 Pending，其餘業務需另行授權 |

User Verified 的範圍是 DB-disabled transport 整體互動。尚無逐指令實際回覆內容、第二帳號 ephemeral 可見性與異常情境的逐項證據，不能寫成全部獨立驗證通過。以下舊紀錄依當時階段保留，若 Pending 與上表不同，以收尾矩陣的限定範圍為準。

### 開發與準備歷史（不是新的執行結果）

Phase 2A.3 準備階段：當時為 Implemented／Unit Tested／Gateway Manual Acceptance Pending。離線主回歸 296 Passed／34 Deselected，加 2 項純文字案例，合計 298 Passed／0 Failed／0 Skipped；原有 240 項未退化，新增 58 項。當時所有外部 API 皆 mock，遠端結構離線未驗證；後續使用者已回報 Remote Command Contract Matched 及 transport Accepted — User Verified，不改寫離線 preflight 本身的行為。

使用者在 Phase 2A.3 規格確認：Phase 2A.2 Offline Preflight、REST Login Smoke、Guild-only Sync、開發 Guild 顯示五指令均人工 Passed。該規格當時的 Interaction Pending 已由本次收尾確認更新為 DB-disabled transport Accepted — User Verified；下方「未授權／未執行」等敘述屬當時準備歷史。

Phase 2A.2 準備歷史：當時為 **Implemented、Unit Tested、Discord Manual Acceptance Pending**。離線驗證 **240 Passed、0 Failed、0 Skipped**（主回歸 238 Passed／34 Deselected；另補跑 2 項純文字案例），包含當時原 182 項與新增 58 項，準備期間沒有真實 Discord 或 MySQL 操作。後續 Registration 與 DB-disabled transport 的使用者確認，以本頁收尾矩陣為準；原工具邊界見 [Phase 2A.2 報告](docs/phase2a2-discord-acceptance.md)。

使用者於 Phase 2A.2 規格確認 **Phase 2A.1 Integration Accepted**：13 Passed、0 Failed、0 Skipped、7.19 秒；前後六表全 0，無資料列殘留。此為人工歷史結果，本次沒有重跑。下方 Phase 2A.1 的 collect-only／Pending 敘述是之前準備階段的紀錄，已由此結果更新；Accepted 仍不包含正式 Commit／跨提交可見性。

2026-10-14 最新人工驗收結果：使用者確認 **Phase 1A～1D Accepted**。真實 MySQL 整合測試重跑為 **14 Passed、0 Failed、0 Skipped**；測試前後 users、songs、submissions、recommendations、credit_ledger、daily_dispatches 六張資料表均為 0 筆，未發現測試資料列殘留。此結果由使用者人工執行並回報，下方 Pending 記錄為先前歷史狀態。

Phase 2A — Discord Bot Application Foundation 規格已補齊並據此實作；狀態為 **Implemented（分享發點與實際交換 Pending）、Unit Tested、Integration Pending、Manual Acceptance Pending**，並非 Accepted。完整範圍、啟動／同步方式與驗收阻礙見 [Phase 2A 驗收報告](docs/phase2a-acceptance.md)。此階段未連線真實 MySQL、登入 Discord、執行 DDL、安裝新套件或 Git Commit／Push；完成後停止等待人工驗收，不自行開始 Phase 2B。

Phase 2A 最終離線驗證：新增 79 項及既有 70 項，共 **149 Passed、0 Failed、0 Skipped**。主回歸命令 147 Passed／21 Deselected；另補跑 2 項純文字 CHECK normalization 測試，2 Passed。19 項真實 MySQL 案例未執行；真實整合仍 Pending。測試命令與 Windows asyncio 沙箱限制說明見驗收報告。

Phase 2A.1 準備歷史：當時 13 項新增整合案例只 collect-only，未執行真實 MySQL SQL；33 項新安全測試及原 149 項離線測試共 **182 Passed、0 Failed、0 Skipped**（主回歸 180 Passed／34 Deselected，另補跑 2 項純文字測試）。之後 Repository 隔離整合已由使用者人工確認 Accepted，原 [準備文件](docs/phase2a1-repository-acceptance.md) 保留 SQL 審查、rollback-only 邊界、timeout 與六表比較紀錄；不將當時的 Pending 當成最新狀態。

2026-10-14 Phase 1 實作與 mock 自動驗證：19 Passed、0 Failed、6 Skipped；pip check 通過。首次執行有 1 項暫存目錄權限錯誤，改用專案 .venv/tmp 後完整重跑通過。MySQL 連線、SQL 實際執行、整合約束及人工驗收仍待使用者確認。Schema 只產生未執行；不宣告人工驗收通過。

Phase 1 狀態維持 **Pending**。Phase 1A 僅修訂 Schema、測試與文件；目前檔案的 NOT NULL / PRIMARY KEY 已正確，另加入回歸保護。本次沒有執行任何 SQL 或讀取真實憑證；修訂腳本不會自動更新既有資料表，需人工審查後另行確認 MySQL 實際結果。

2026-10-14 Phase 1A 自動測試結果：29 Passed、0 Failed、14 Skipped（19 項既有單元測試、10 項靜態檢查通過；14 項 MySQL 整合案例未啟用）。函式規範檢查無違規，實作最多 19 行、控制巢狀最多 3 層。靜態通過不表示已通過 MySQL 驗證或人工驗收。

2026-10-14 Phase 1C：使用者已於 Workbench 人工建表並確認約束；PyMySQL 真實唯讀驗證最終 7 Passed、0 Failed、0 Skipped（5 項真實驗證、2 項 CHECK 顯示格式測試）。確認 CURRENT_USER() = jukebox101_app@127.0.0.1、DATABASE() = jukebox101，六表及必要約束存在。首次沙箱執行因 socket 權限出現 5 項 setup error；沙箱外首次執行 4 Passed、1 Failed，原因為 CHECK 引號跳脫比較，修正後重跑通過。沒有執行舊的 14 項整合案例或任何寫入 SQL；資料完整性違規拒絕、真實 rollback、並行業務與派送仍未驗證，Phase 1 維持 Pending，等待人工驗收。

2026-10-14 Phase 1D：獨立測試庫準備與防誤寫修正完成，46 項 mock／單元／靜態測試通過、0 Failed、0 Skipped。14 項整合案例只執行 collect-only，沒有運行。未讀真實憑證、未連線、未建立測試庫或帳號、未改權限。測試庫與帳號實際存在、權限隔離與 Schema 完整性仍待人工確認，Phase 1 保持 Pending。

上述 Phase 1 歷史記錄之後，Phase 2A 已新增 URL 解析、Discord 指令、點數／歷史查詢與 Daily 偏好設定。歌曲資格驗證、分享發點、實際交換、排程、Spotify 或雲端部署仍未完成。歌曲默認 pending，後續需明確核准驗證政策才能授予額度。日後 schema 演進需版本化審查，不能靠重跑此腳本遷移。

Phase 1D 最終安全審查：52 項 mock／單元／靜態測試通過、0 Failed、0 Skipped。補強 fixture 再次確認 disposable 授權及必要鍵完整欄位／目標預檢；沒有執行真實 MySQL 寫入測試或 DDL。使用者已人工確認測試庫、專用帳號與約束，執行時仍須由測試程序再次核對；Phase 1 保持 Pending。

Phase 1D.1：使用者回報真實整合 12 Passed、2 Failed、0 Skipped，兩项外鍵斷言收到 1216 而預期 1452。依官方定義保留缺少父列的兩種錯誤碼，新增嚴格錯誤類型、約束訊息及父列條件驗證；68 項 mock／單元／靜態測試通過。本次未執行真實寫入、未修改 Schema；權限遮蔽為合理推論，具體環境原因與六表零殘留仍待確認。詳見 [外鍵調查與重跑步驟](docs/mysql-foreign-key-investigation.md)。


### Batch B 雙帳號 Migration 安全改造（離線，待人工驗收）

Application `TEST_DB_*` 與 Migration `TEST_MIGRATION_DB_*` 已分離，固定本機測試庫及各自帳號；Application 不需要也不得具有 DDL 權限。新增獨立唯讀 Migration Preflight 與預設關閉的執行入口，支援 schema/table grants。最小權限 SQL 與分階段 PowerShell 流程見 [本機驗收流程](docs/phase2b2-batch-b-user-run.md)。

本輪安全離線回歸 532 Passed／0 Failed／0 Skipped；無 MySQL、Discord、YouTube 或實際 Ledger 操作。真實 Migration／InnoDB 並行與原子 Nickel 驗收仍未執行。Phase 2B.1／Batch A／Batch B 均未由本輪標記為人工 Accepted，不開始 Batch C。


最終權限離線覆核：Application 改為逐表最小權限，無 DELETE，jb2_migrations 僅 SELECT；原 schema-level 寫入會被阻擋。詳見 [權限矩陣與操作限制](docs/batch-b-privilege-audit.md)。本輪離線回歸 548 Passed／0 Failed／0 Skipped；真實 MySQL 未驗證，等待人工審核。


### Batch B 真實 MySQL Harness（實作完成，待帳號與測試執行授權）

使用者已回報 Application／Migrator Preflight、Migration 2b2-b1 與 23 表部署通過，Migrator 已鎖回，control 三開關 OFF；本輪保留這些成果，未重新驗證或執行部署。前文「Migration 未執行」屬較早離線階段紀錄，以本段現況為準。

新增獨立 jukebox101_test_harness 的逐表／指定欄位 DBA SQL 草案及 Baseline → Enable → 真實交易 → 精確主鍵清理 → Restore → Verify 架構；Application 權限不變。已實作 23 個預設 skip 的真實 MySQL 案例，本輪 650 Passed／0 Failed／23 Skipped；其中 102 個為新離線安全測試，沒有連線 MySQL。一次性帳號建立／GRANT、真實寫入及隔離庫開關啟用均待另行人工授權，Batch B 尚未整體人工 Accepted，不開始 Batch C。詳見 [Harness 與真實測試操作文件](docs/batch-b-real-mysql-tests.md)。


Harness 最終安全收斂（離線）：保持 submissions 僅 SELECT 與 18 表 DELETE；新增精確 control revision／switch drift 檢查、server UUID pin、未啟用角色與額外 grants 檢查、清理失敗原因保存。DBA 草案改為先建立鎖定 Harness、完整核對後只解鎖 Harness；須受控執行，不能直接忽略 SELECT 結果跑完整批 SQL。本輪 681 Passed／0 Failed／23 Skipped；未連線 MySQL、未執行任何 DBA 或業務 SQL。詳見 [單次授權範圍與安全限制](docs/batch-b-real-mysql-tests.md)。

## Private Beta 規格 checkpoint（2026-10-16）

狀態：**BETA_SPEC_READY_FOR_REVIEW**；僅文件規格，未實作 Beta。見 [最小 Beta 規格](docs/private-beta-mvp-specification.md) 與 [Recovery 成功紀錄](docs/recovery-success-checkpoint.md)。下列或先前各階段測試數量屬歷史，不代表本輪真實 MySQL 驗收。
