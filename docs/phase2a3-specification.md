# JukeBox101 — Phase 2A.3：Gateway Interaction Acceptance Preparation

## 0. 授權範圍

本次授權開始 Phase 2A.3 的本地準備工作。

允許：
- 審查及修改必要的 Python 程式碼。
- 新增隔離的 Gateway 驗收入口。
- 新增或修改離線 Mock 測試。
- 更新 README、ROADMAP 及驗收文件。
- 執行不連線 Discord／MySQL 的離線測試。

禁止：
- 真實 Discord Gateway 連線。
- 真實 Discord Login／REST 操作。
- Guild Sync 或 Global Sync。
- 真實 MySQL 連線或寫入。
- 修改 .env 或讀取真實 Token 內容。
- 安裝套件、啟用付費服務。
- Git Commit／Push／Merge／Tag。
- 自行開始 Phase 2B。

完成後停止，等待人工授權。

## 1. 已確認的歷史結果

Phase 1A～1D：Accepted。

Phase 2A.1 Repository Integration：Accepted。
- 13 Passed
- 0 Failed
- 0 Skipped
- 測試前後六表均為 0
- 此為使用者歷史人工驗收結果，本次不得假稱重新執行。

Phase 2A.2：
- Offline Preflight：人工 Passed。
- REST Login Smoke Test：人工 Passed。
- Guild-only Sync：人工 Passed。
- Discord 開發 Guild 已顯示五組 Slash Commands：人工 Passed。
- 真實 Interaction：Pending。

Application ID：1559751168723521576。
Development Guild ID：1559749813011087392。

這兩個 ID 僅作為已核對的開發環境背景；實作仍必須從明確設定讀取，不得硬編碼成不可變的正式設定。

既有五組 Slash Commands：
- /share
- /exchange
- /balance
- /history
- /daily

先前離線測試總數：240 Passed。
不得假稱本次已重新執行。

## 2. 核心目標

建立一個明確授權後才可啟動的 DB-disabled Discord Gateway Listener，讓使用者在專用 Development Guild 內實際操作已發布的五組 Slash Commands。

本階段只驗證 Discord Interaction transport 與 ephemeral 回覆。

不驗證：
- 真實投稿持久化。
- 真實歌曲驗證。
- 發點或扣點。
- 真實推薦交換。
- Daily 設定持久化。
- Daily 排程。
- DM 派送。
- 正式資料庫存取。

不得將上述未完成功能顯示為成功。

## 3. 開始前審查

先閱讀：
- AGENTS.md
- README.md
- ROADMAP.md
- bot/discord_app.py
- bot/services.py
- bot/config.py
- bot/__main__.py
- bot/acceptance_config.py
- bot/guild_acceptance.py
- bot/discord_acceptance.py
- docs/phase2a2-discord-acceptance.md
- 現有 Discord Mock 測試

確認既有 CommandHandlers 與 CommandTree 註冊流程，避免建立第二套互相衝突的正式 Bot。

優先重用現有程式碼及 Discord Client。

## 4. DB-disabled 的安全設計

建立明確的 Gateway Acceptance Mode。

要求：
1. 不載入 DB_* 或 TEST_DB_*。
2. 不建立 PyMySQL 連線。
3. 不建立正式 Repository。
4. 不使用正式資料庫連線池。
5. 不將正式 Repository 的成功回覆沿用於 DB-disabled 模式。
6. 任何資料持久化操作都必須被拒絕。
7. 即使環境中存在正式 DB_NAME=jukebox101，也不能使用它。
8. 不因資料庫不可用而回傳假的 0 餘額或空歷史。

可以使用既有 DisabledAcceptanceRepository，或以更明確的 Service／Handler Adapter 重用架構。

避免以散落各處的 if acceptance_mode 破壞正式業務邏輯。

## 5. Gateway Listener 安全限制

建立獨立 CLI 入口，例如：

python -m bot.gateway_acceptance

預設只執行 Offline Preflight 或顯示安全說明，不得直接連線。

真實 Gateway 連線必須具備明確 opt-in，例如：

--authorize-gateway

要求：
- 僅允許指定 Development Guild。
- 驗證預期 Application ID。
- Guild ID 必須由使用者提供。
- 不硬編碼 Token。
- 不自動 Guild Sync。
- 不呼叫 global_sync=True。
- 不在 on_ready／setup_hook 同步。
- 不啟動 Daily Scheduler。
- 不發送 DM。
- 不需要 Message Content Intent。
- 不要求 Administrator 權限。
- 不修改任何遠端指令。
- 不在 import 或 Client construction 時登入。
- 能以 Ctrl+C 正常關閉 Client。
- 對意外斷線、登入失敗及 Discord API 錯誤提供安全訊息。
- 不輸出 Token 或資料庫密碼。

特別注意：Discord Guild-scoped Commands 不是 Gateway 事件的天然存取控制。

必須在 Interaction handler 入口確認：
- interaction.guild_id 與授權 Guild ID 完全一致。
- 非 Guild Interaction 一律拒絕。
- 其他 Guild Interaction 一律拒絕。
- 未授權情境不得呼叫任何資料庫或業務寫入。
- 非預期 Application 身分不得啟動 Listener。

如果實際 Discord API 不提供可安全核對的資訊，停止並列出限制。

## 6. 五組指令的 Acceptance-only 行為

本階段要確保每組已發布指令都可以得到安全且真實的 ephemeral 回覆。

### /share

- 有效 YouTube URL：回覆目前為測試模式，沒有儲存歌曲。
- 非法 URL：可以回覆格式不正確。
- 不建立 songs 或 submissions。
- 不給 credit。
- 不聲稱 pending 已保存。

### /exchange

- 回覆交換功能尚未啟用。
- 不扣 credit。
- 不建立 recommendations。

### /balance

- 回覆目前資料庫停用，無法查詢餘額。
- 不顯示假的 0。
- 不讀取 users。

### /history

- 回覆目前資料庫停用，無法查詢歷史。
- 不顯示假的空歷史。
- 不讀取 recommendations。

### /daily

- on/off 都明確回覆設定未保存。
- 不修改 users。
- 不啟動排程。
- 不發送 DM。

所有回覆：
- 必須 ephemeral。
- 不得使用公開 fallback。
- 不得洩漏例外細節或 Token。
- 不得提及使用者群組或其他成員。
- 避免 @everyone、@here 或任意 mention。
- 不得因 Discord 回覆失敗而重新執行業務操作。

如需要在 Discord 上展示測試模式，可在回覆中明確標示「Development / DB-disabled」。

## 7. Command Registry 與已發布指令一致性

既有 Guild 已成功發布五組指令。

不得重新執行 Guild Sync。

必須確保本地 CommandTree 與已發布指令名稱、選項名稱、必要性及型別一致。

如果發現不一致：
- 不得自行修改遠端指令。
- 不得自動同步。
- 回報差異並停止真人 Gateway 驗收準備。

不能僅比較五個名稱就宣稱完整一致。

應盡可能利用既有已發布的結構化資料或安全的離線契約資料；若無法在離線狀態證明遠端結構完全一致，明確標示待真人測試確認。

## 8. 離線測試

至少涵蓋：
- 未授權時不連線。
- Token 缺失。
- Guild ID 錯誤。
- Application ID 不一致。
- 非 Guild Interaction。
- 其他 Guild Interaction。
- 五組指令均能安全回覆。
- 所有回覆 ephemeral。
- /share 有效／無效 URL。
- /exchange 不扣點。
- /balance 不回假 0。
- /history 不回假空歷史。
- /daily 不保存。
- DB_* 即使存在也不會被讀取。
- 沒有 PyMySQL 連線。
- 沒有 DM。
- 沒有 Daily Task。
- 沒有自動 Guild Sync。
- 沒有 Global Sync。
- Discord API 失敗。
- 取消或 Ctrl+C 清理。
- Client 關閉。
- Token 不進 log。
- 不需要 Message Content Intent。
- CommandTree 參數契約。

所有外部 API 必須 Mock。

不能使用真實 Token、Discord Gateway 或 MySQL。

不得弱化現有測試或以 Skip 掩蓋失敗。

## 9. AGENTS.md 規範

遵守：
- 函式實作最多 25 行。
- 巢狀深度最多 3 層。
- 優先 Guard Clauses。
- 保留必要 docstrings。
- 不重複建立架構。
- 不引入未批准的依賴。

## 10. 文件與驗收

新增：

docs/phase2a3-gateway-acceptance.md

更新：
- README.md
- ROADMAP.md

記錄：
1. 新增及修改檔案。
2. 安全邊界。
3. 五組指令的 acceptance-only 回覆。
4. Guild 與 Application 身分核對。
5. CommandTree 契約。
6. Offline Mock 測試結果。
7. 真實 Gateway 啟動指令。
8. PowerShell 安全 Token 輸入流程。
9. 人工驗收表。
10. 關閉與異常處理。
11. 已知限制。
12. Git Status。

Phase 2A.3 只能標記：
Implemented / Unit Tested / Gateway Manual Acceptance Pending。

不得標記 Accepted。

## 11. 最終回報

完成後請提供：
- 實作摘要。
- 修改檔案清單。
- Offline Passed／Failed／Skipped。
- Command Registry 契約結果。
- DB-disabled 保證及其測試證據。
- Gateway 人工驗收 PowerShell 指令。
- 真實操作時的預期回覆。
- 安全與剩餘風險。
- Git Status。

最後停止，等待人工授權。

不得自行連線 Discord Gateway，不得開始 Phase 2B。