# Phase 2A 開發與人工驗收報告

日期：2026-10-14（Asia/Taipei）。依據使用者提供的 [完整規格](phase2a-specification.md) 及 AGENTS.md。

Phase 1A～1D 保持 **Accepted**：使用者回報真實 MySQL 整合 14 Passed／0 Failed／0 Skipped，測試前後六表均 0 筆，無資料列殘留。

最新 Phase 2A 狀態為 **Implemented（Foundation 範圍）、Unit Tested、Repository 隔離 Integration Accepted、DB-disabled Interaction Transport Accepted — User Verified**。真實 Discord＋MySQL 持久化互動仍 Pending，資格／發點／實際交換／Daily 派送與恢復未實作，不將整個 Phase 2A 視為全面完成。Phase 2B 未開始。

Phase 2A.1 [Repository 整合](phase2a1-repository-acceptance.md) Accepted：13 Passed／0 Failed／0 Skipped，隔離庫前後六表全 0，採 rollback-only savepoint，不代表正式 Commit／跨提交驗收。Phase 2A.2 Registration 四項人工 Passed；Phase 2A.3 人工 Preflight、身分、Remote Command Contract Matched、Gateway Ready 已確認，五組 DB-disabled 互動僅記 User Verified。逐字回覆、第二帳號 ephemeral 可見性與異常情境沒有逐項獨立證據；歷史離線總數 298 Passed，不是本次重跑。完整矩陣及缺口見 [Closeout](phase2a-closeout.md)。下列其餘測試數字與 Pending 敘述保留為原階段歷史紀錄，功能表描述正式服務，不是 Gateway acceptance-only 行為。

## 五組指令

| 指令 | 本階段實作 | 明確限制 |
|---|---|---|
| `/share url` | 嚴格 YouTube 解析；保存 pending 歌曲及去重投稿嘗試；重複回覆 | 不發點，不標記 verified，不加入交換池；既有 verified/rejected 不被覆寫 |
| `/exchange` | 本機註冊及私密 Pending 回覆；不呼叫 repository | 候選選取、額度判斷、扣點、推薦與外部發送／恢復尚未實作 |
| `/balance` | 查詢本人餘額；不存在的使用者顯示 0；查詢失敗顯示服務不可用 | 真實 MySQL 結果待驗收，不把故障當成 0 |
| `/history` | 本人 delivered 紀錄；UTC 時間；時間降冪加推薦 ID 降冪；最多 10 筆 | 無互動分頁；回覆上限 1900 字元；只顯示有效 canonical YouTube URL |
| `/daily mode:on/off` | 持久化最終偏好；重複設定相同結果；新使用者建立預設帳號 | 不啟動排程、不建立 daily_dispatches、不發 DM；回覆明確說明限制 |

所有回覆均 ephemeral，避免公開揭露點數、投稿與歷史。回覆停用 mentions。先 defer 再執行服務，避免同步 SQL 阻塞事件迴圈。Discord HTTP／本機網路錯誤只記錄例外類型；資料庫錯誤只記錄類型，使用者只收到通用服務錯誤。框架的意外 callback 錯誤由自訂 CommandTree 統一處理，不記錄原始例外內容、參數或 traceback。

## 分層與交易

- `bot/discord_app.py`：Client、CommandTree、指令註冊、Interaction 與同步入口。
- `bot/services.py`：Discord-independent use cases；完整 repository 工作單元透過 `asyncio.to_thread` 執行。
- `bot/database/repository.py`：原生參數化 SQL；重用 Phase 1 `transaction()`；每次操作建立／使用／關閉自己的連線。
- `bot/urls.py` 與 `bot/models.py`：純解析及小型結果模型，不使用外部 metadata。
- `bot/config.py` 與 `bot/__main__.py`：secret-safe 設定及明確執行入口；import 不讀 Token、不連線、不同步。

投稿在同一交易內建立使用者、以 `(platform, platform_track_id)` 唯一鍵 upsert 歌曲、`SELECT ... FOR UPDATE` 鎖定 canonical song row，再記錄去重投稿。相同影片的競爭操作由唯一鍵與列鎖序列化；使用者／歌曲唯一鍵提供最後防護。現有來源、verified/rejected 狀態與驗證證據均保留；新歌依 Schema default 為 pending，YouTube delivery URL 留 NULL。提交的原始 URL 保存在首次來源欄位，重試不覆寫，符合 Phase 1 去重嘗試語義，並非原始請求事件日誌。

交易成功後才回覆保存成功；SQL 或 commit 失敗由既有 context rollback／close，服務不偽裝成成功。Daily 以帳號主鍵及同一交易中的最終值 UPDATE 實作；並發不同偏好由資料庫寫入順序決定最後值，不覆寫點數。餘額與歷史只讀查詢，亦透過既有短交易關閉資源。

**此階段沒有任何 ledger 寫入或 credit_balance 更新**，因此不会因投稿重試／競爭重複發點；這是保護性停用，不是「已完成併發安全授予點數」。同理，交換入口不預約或扣點，不代表已完成交換並發控制。真實 MySQL 鎖定、死鎖／timeout 及並行案例沒有執行，不宣稱已驗證。

## Pending 與具體待決事項

1. **歌曲資格政策**：URL/Video ID 只能證明連結形狀，不能證明影片是歌曲。沒有 API、抓取或人工驗證政策，不能授予點數。需先批准誰可以驗證、可接受的證據、verified provenance 的格式與可信來源、拒絕／待處理流程。
2. **全域首次獎勵歸屬**：pending 嘗試在驗證後由誰獲得首次有效提交獎勵、是否及如何觸發補發尚未定義。既有 song award 唯一键足以提供約束基礎，但不自行選擇獲獎人或新增管理指令。
3. **實際交換及恢復流程**：需明確實作 users 列鎖、候選排除與隨機選擇、同交易推薦／ledger／餘額，並接上 reserved → sending → delivered／failed／uncertain。需明確未送出失敗的退款、網路 timeout、Discord 接受而 DB 成功更新失敗、重啟後的調查／恢復政策。不可只扣點後回覆 URL 就宣稱成功或 exactly-once。
4. **缺少候選、額度不足、排除自己及既有歷史、並發交換**：受上述 gate 阻擋，本階段未實作且未以假測試宣稱通過。現有 Schema 沒有發現必須遷移的證據，不需要或執行 DDL。
5. **真實驗收的最新範圍**：Repository 隔離 SQL、Registration 及 DB-disabled transport 已各自有分項證據，不能合併宣稱真實 Discord＋MySQL 持久化互動完成；逐字回覆、第二帳號 ephemeral 可見性、完整權限及異常情況仍未取得逐項獨立真人證據。

## 啟動與設定

目前禁止登入，本節命令是日後另行授權的人工流程，本次未執行。

環境必須包含 `DISCORD_BOT_TOKEN` 與正式 `DB_HOST`、`DB_PORT`（預設 3306）、`DB_USER`、`DB_PASSWORD`、`DB_NAME`。缺少 Token 會在建立 client／登入前失敗，不使用假 Token。設定 repr 不輸出 Token；既有 DB 密碼保護維持。

```powershell
.\.venv\Scripts\python.exe -m bot
```

Linux 對應 `.venv/bin/python -m bot`。入口只讀 process environment，不自動讀 `.env`。既有 `load_database_settings(Path('.env'))` 與新增 `load_discord_settings(Path('.env'))` 可由明确的本機呼叫使用，皆不覆蓋環境值；本次未修改或讀取真實 `.env`。不要將 Token 寫進 shell 歷史、程式或驗收截圖。

Bot construction 只建立本機 command tree；最小 intents 為 `Intents.none()`，不要求 message_content、members、presences。沒有 `on_ready` 同步 hook、background task 或 Daily scheduler。

`TEST_DB_*`、`JUKEBOX_TEST_SCHEMA_READY`、`JUKEBOX_DISPOSABLE_DATABASE`、`JUKEBOX_RUN_MYSQL_TEST` 屬於 Phase 1 測試專用入口。應用程式不使用它們，也不將測試設定回退至正式設定。既有測試安全 helper 未修改；不要把測試憑證放入 `.env.example`。

## 指令同步

明確可測試入口：`await synchronize_commands(client)` 預設回傳空清單，不呼叫遠端 API。兩種 opt-in 不能同時指定：

```python
await synchronize_commands(client, guild_id=approved_development_guild_id)
await synchronize_commands(client, global_sync=True)
```

上述片段需由已登入 client 的控制流程呼叫；本階段未提供自動登入／同步輔助腳本，沒有預設 Guild ID 或全域同步環境開關。Guild ID 必須由人工確認且為有效正整數 snowflake，不接受布林值、0 或字串。登入本身不會同步。

Guild sync 將本機全域定義複製至指定開發伺服器範圍，再同步該範圍；只供該 Guild 驗收。Global sync 更新應用程式全域定義，範圍更廣；應先完成開發 Guild 驗收，再另行批准。`tree.sync` 是該範圍指令集合的同步，可能取代現有定義，人工執行前須核對 application 與 Guild。切換至 global 後，Guild 版本清理另行審查，不自動刪除任何遠端指令。

## 離線測試結果

```powershell
$env:JUKEBOX_RUN_MYSQL_READONLY = '0'
$env:JUKEBOX_RUN_MYSQL_TEST = '0'
.\.venv\Scripts\python.exe -m pytest -m 'not mysql and not mysql_readonly' -q --tb=short
.\.venv\Scripts\python.exe -m pytest tests/test_mysql_readonly.py::test_check_rendering_normalization -q --tb=short
```

第一個命令：**147 Passed、0 Failed、0 Skipped、21 Deselected**。21 項排除為 14 真實寫入整合、5 真實唯讀驗證及 2 純文字 normalization 測試；第二個命令補跑該 2 項純測試，**2 Passed、0 Failed、0 Skipped**。兩次合計 **149 個不同離線案例通過**：新增 Phase 2A 79 個、既有離線 70 個。19 項真實 MySQL 案例仍未執行；不將排除計入 skipped 或 passed。

覆蓋本機 Bot 初始化／五指令及 choice 註冊、callback 綁定、private defer／reply、Token 缺失與安全 repr、import 外部副作用、Guild／Global mock 同步及預設停用、URL 形式／tracking／大小寫／惡意主機／模糊識別、pending／duplicate／既有狀態、不發點 gate、交換不扣點 gate、新帳號餘額、DB 故障、worker thread 邊界、Daily 最終偏好、空歷史／上限／不可信網址、交易 rollback／close 與 SQL 參數／锁順序。所有 Discord 互動皆 mock。

首次 callback 測試缺少 discord.py 的 binding 參數，已修正測試呼叫；首次 import 測試使用 reload 影響其他測試的 class identity，改為獨立程序隔離；worker thread 觀察 helper 修正參數契約。完整重跑通過，未刪除既有測試或放寬業務斷言。

Windows 沙箱中 asyncio 所需的標準庫本機 socket pair 卡住，因此上述含 asyncio 的離線集合在經自動審查准許的沙箱外執行；仍停用／排除真實 DB，Discord API 使用 mock。沒有真實平台連線。一項 warning 為已安裝 discord.py 的 `audioop` 棄用提示，Python 3.12 下不影響結果，未變更依賴。

靜態 AST 檢查驗證 bot 全部函式實作不超過 25 行、控制巢狀不超過 3 層、每個函式具 labeled docstring；不新增品質檢查依賴。`git diff --check` 沒有輸出，但目前全部成果未追蹤，該命令不能代替未追蹤檔案品質驗證。

## 新增／修改檔案

新增：`bot/__main__.py`、`bot/discord_app.py`、`bot/models.py`、`bot/services.py`、`bot/urls.py`、`bot/database/repository.py`；`tests/test_discord_app.py`、`tests/test_repository.py`、`tests/test_services.py`、`tests/test_urls.py`、`tests/test_phase2_quality.py`；本報告與 `docs/phase2a-specification.md`。

修改：`bot/config.py`、`bot/__init__.py`、`.env.example`、`README.md`、`ROADMAP.md`。沒有修改 `schema.sql`、既有 connection transaction、Phase 1 安全測試、依賴清單或使用者 `.env`。

## 人工驗收建議與停止點

先人工審查上述 Pending 決策，確認接受 Foundation 部分及保護性停用。另行批准後，才在已隔離的測試庫驗證新增 SQL、重複／並行 pending 投稿、rollback／close、Daily 冪等與歷史排序；不可直接跑正式庫寫入驗收。再批准 Discord 登入及指定開發 Guild sync，確認 application、邀請权限、五指令、私密回覆與故障行為。不執行全域同步、實際交換或 Daily 派送來代替缺失的流程。

Git Status 仍為既有全案未追蹤狀態：`.env.example`、`.gitignore`、`AGENTS.md`、`README.md`、`ROADMAP.md`、`bot/`、`dependency-versions.txt`、`docs/`、`pyproject.toml`、`tests/` 均 `??`。沒有 Commit、Push、Merge 或 Tag。

本次沒有 MySQL 連線／寫入、DDL／Migration、真實 Token 登入、遠端指令同步、真實 DM、背景 Daily 排程、套件安裝、Spotify、部署或 Git 變更歷史操作。完成授權範圍內可安全實作的 Phase 2A Foundation 後停止，等待人工驗收，不自行開始 Phase 2B。
