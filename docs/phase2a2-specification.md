# JukeBox101 — Phase 2A.2：Discord Development Guild Acceptance Preparation

## 0. 任務授權

本次正式授權開始 Phase 2A.2 的準備工作。

本次授權僅限本地程式碼、離線測試與文件。

不得自行進行真實 Discord 登入、遠端 Slash Command Sync、真實 MySQL 操作或 Git Commit／Push。

完成後停止，等待人工驗收。

## 1. 已確認的專案狀態

Phase 1A～1D：Accepted。

Phase 2A Foundation：Implemented / Unit Tested，Discord Manual Acceptance Pending。

Phase 2A.1 Repository Integration：Accepted。

Phase 2A.1 人工執行結果：

- 13 Passed
- 0 Failed
- 0 Skipped
- 執行時間 7.19 秒
- 測試前六張資料表全部 0 筆
- 測試後六張資料表全部 0 筆
- 未發現資料列殘留

先前離線測試：182 Passed。

這些是已知歷史結果，不得假稱本次重新執行。

## 2. 任務目標

建立一套可重現、安全且容易人工操作的 Discord 開發伺服器驗收流程。

驗收對象：

- Discord Bot 初始化與登入
- 開發 Guild 指令同步
- Slash Command 註冊
- Interaction 回覆
- Ephemeral 訊息
- 錯誤處理
- 設定與憑證保護
- 正常關閉與資源清理

不得擴充 MVP 業務範圍。

## 3. 開發前審查

先閱讀：

- AGENTS.md
- README.md
- ROADMAP.md
- pyproject.toml
- bot/config.py
- bot/__main__.py
- bot/discord_app.py
- bot/services.py
- bot/database/repository.py
- bot/database/connection.py
- docs/phase2a-acceptance.md
- docs/phase2a1-repository-acceptance.md
- 現有 Discord 與設定測試

若實際檔案路徑不同，請依專案現況定位，不得自行假設。

優先重用既有架構，避免重新建立第二套 Bot 啟動機制。

## 4. Discord Bot 啟動審查

確認：

1. 模組 import 不會登入 Discord。
2. Client construction 不會自動連線。
3. 缺少 Token 時會安全失敗。
4. Token 不會出現在 repr、例外、log 或 traceback 的自訂輸出。
5. Bot 啟動與 Command Sync 完全分離。
6. 沒有在 on_ready 中自動同步。
7. 沒有在 setup_hook 中未經授權同步。
8. 沒有自動啟動 Daily Scheduler。
9. 沒有意外發送 DM。
10. 正常退出時可以關閉 Discord Client。

如有問題，進行最小必要修正並新增離線測試。

## 5. Discord 設定管理

審查目前設定來源：

- DISCORD_BOT_TOKEN
- DB_HOST
- DB_PORT
- DB_USER
- DB_PASSWORD
- DB_NAME

注意目前既有入口只讀取 process environment，不保證自動載入 .env。

請設計一套明確且安全的本地開發啟動方式。

要求：

- 不修改使用者真實 .env。
- 不在程式碼中硬編碼 Token。
- 不要求使用者將 Token 貼進聊天或 Codex。
- 不將 Token 寫入 shell history。
- 不自動啟用 python.terminal.useEnvFile。
- 不將 TEST_DB_* 混入正式 Bot 設定。
- 不允許 Discord 驗收腳本意外指向正式 jukebox101 資料庫。

若驗收需要 MySQL，必須使用明確隔離的測試庫、測試帳號及人工確認的設定。

不得因缺少 Token 而建立假的正式設定值。

## 6. Discord 開發 Guild 策略

使用指定的 Development Guild。

不得使用 Global Sync 進行本次驗收。

確認既有 synchronize_commands(client, guild_id=...) 的行為。

驗收工具必須符合：

- Guild ID 由使用者明確提供。
- Guild ID 必須是有效的正整數。
- 不硬編碼任何 Guild ID。
- 不使用未知 Guild。
- 不在 Bot 每次啟動時同步。
- 同步必須由獨立的明確人工操作觸發。
- 同步前顯示目標 Guild ID、Application ID 及預計同步的指令名稱。
- 同步前需要額外人工確認。
- 不得使用 global_sync=True。
- 不得自動清理或刪除既有遠端指令。
- 若無法安全確認遠端狀態，應停止而非覆寫。

特別注意：Discord 的 Guild Sync 可能更新或移除目標範圍的現有指令。

必須審查既有 synchronize_commands 的 copy_global_to 與 tree.sync 行為，避免意外破壞其他指令。

## 7. 建立安全驗收入口

可以新增獨立的驗收腳本或 Python 模組，但不要建立另一套正式 Bot。

建議將流程分為：

### A. Offline Preflight

完全離線。

驗證：

- 必要設定名稱
- 設定格式
- Bot 初始化
- 指令註冊
- Command Tree
- 預期指令名稱
- Guild ID 格式
- 沒有預設遠端同步
- 沒有真實網路副作用

不得輸出 Token 或資料庫密碼。

### B. Discord Login Smoke Test

只準備流程，不執行。

未來由使用者另行授權。

應驗證：

- Bot 能成功登入指定 Application。
- 可以取得目前登入 Bot 的 Application ID。
- Bot 能正常關閉。
- 不進行 Command Sync。
- 不啟動 Daily。
- 不發送真實 DM。
- 不呼叫任何需要寫入資料庫的業務流程。

### C. Development Guild Sync

只準備流程，不執行。

未來必須由使用者明確授權。

同步前必須：

1. 顯示目標 Guild。
2. 顯示 Application ID。
3. 顯示本地指令清單。
4. 由使用者確認。
5. 只對指定 Guild 同步。
6. 不進行 Global Sync。

不得自動執行此流程。

## 8. 五組 Slash Command 驗收設計

為以下指令設計人工驗收表：

### /share

- 有效 YouTube URL。
- 非法 URL。
- 重複投稿。
- pending 狀態提示。
- 不發點。

### /exchange

- 指令存在。
- 回覆功能尚未啟用。
- 不扣點。
- 不建立推薦紀錄。

### /balance

- 新使用者顯示 0。
- 已存在使用者顯示正確餘額。
- DB 故障顯示服務錯誤。
- 不將 DB 故障偽裝成 0。

### /history

- 空歷史。
- 穩定排序。
- 最多 10 筆。
- Discord 字元長度限制。
- 不洩漏其他使用者資料。

### /daily on/off

- 開啟偏好。
- 關閉偏好。
- 重複設定。
- 持久化。
- 不啟動排程。
- 不發送 DM。

所有使用者回覆應為 ephemeral。

若某些案例需要事先準備測試資料，應提供獨立、安全、可審查的測試資料準備方案，不得直接修改正式資料庫。

## 9. 測試資料庫隔離

Phase 2A.2 不得連線真實 MySQL。

準備未來 Discord 人工驗收時，應特別處理：

- Discord Bot 是否必須使用測試資料庫。
- 測試帳號是否具備必要權限。
- 測試資料是否會持久化。
- 測試後如何檢查資料殘留。
- 是否有適當的清理與恢復策略。

注意：真實 Discord Interaction 與資料庫交易不一定能使用 Phase 2A.1 的外層 Rollback 測試方法。

因此不能宣稱真實 Discord 測試後一定自動還原資料庫。

如果無法安全保證隔離，請將需要資料庫的人工案例標記 Pending。

## 10. 離線測試

新增或完善測試，至少涵蓋：

- Offline preflight
- Token 缺失
- Guild ID 無效
- Guild ID 為 0、負數、字串、布林值
- 指令清單正確
- Client 初始化不登入
- 預設不同步
- 未授權禁止 Guild Sync
- 永遠禁止驗收入口 Global Sync
- 人工確認取消
- 同步前 Application ID 不一致
- Discord Login 失敗
- Discord API 錯誤
- Discord Client 正常關閉
- 不輸出 Token
- 不輸出資料庫密碼
- 不自動發送 DM
- 不啟動 Daily
- 不連線 MySQL

所有 Discord API 必須 Mock。

不得使用真實 Discord Token。

不得連線真實 MySQL。

## 11. 品質規範

遵守 AGENTS.md：

- 函式實作最多 25 行。
- 巢狀深度最多 3 層。
- 優先 Guard Clauses。
- 不重複建立架構。
- 不引入未批准的依賴。
- 不刪除既有安全測試。
- 不以 Skip 或弱化 Assertion 掩蓋失敗。

執行全部允許的離線測試。

## 12. 文件更新

更新：

- README.md
- ROADMAP.md
- Phase 2A.2 驗收文件

清楚區分：

- Implemented
- Unit Tested
- Integration Accepted
- Discord Manual Acceptance Pending
- Accepted

不得將 Phase 2A.2 標記為 Accepted。

必須記錄：

- 安全啟動流程。
- 環境變數設定方式。
- Offline Preflight 指令。
- Login Smoke Test 的人工操作步驟。
- Guild Sync 的人工確認方式。
- Discord 五組指令驗收表。
- DB 隔離限制。
- 錯誤與中止處理。
- 已知風險。

## 13. 嚴格禁止事項

本次禁止：

1. 真實 Discord 登入。
2. 真實 Discord Guild Sync。
3. Global Sync。
4. 真實 Discord DM。
5. 真實 MySQL 連線或寫入。
6. DDL／Migration。
7. 安裝套件。
8. 修改 .env。
9. 啟用正式 Daily Scheduler。
10. 實作真正的歌曲交換。
11. Spotify 整合。
12. 雲端部署。
13. Git Commit／Push／Merge／Tag。
14. 自行開始 Phase 2B。

## 14. 執行順序

請自主完成：

1. 專案審查。
2. Discord 啟動安全檢查。
3. Guild Sync 風險審查。
4. Offline Preflight。
5. 安全 Login Smoke Test 入口準備。
6. 安全 Guild Sync 入口準備。
7. Mock 測試。
8. 全部離線測試。
9. README／ROADMAP 更新。
10. 完整人工驗收報告。

若發現安全問題，允許修正必要程式碼。

若發現規格不明確或可能影響真實 Discord Application，停止相關操作並列出待決問題。

## 15. 最終回報

請提供：

1. Phase 2A.2 實作摘要。
2. 新增與修改檔案。
3. Offline Preflight 設計與執行結果。
4. Login Smoke Test 的安全機制。
5. Guild Sync 的安全機制。
6. 五組指令人工驗收表。
7. DB 隔離策略。
8. 離線測試 Passed／Failed／Skipped。
9. 尚未解決的風險。
10. 人工操作所需的 PowerShell 指令。
11. Git Status。

完成後停止，等待使用者回報與人工授權。

不得自行執行真實 Discord 操作，不得開始 Phase 2B。