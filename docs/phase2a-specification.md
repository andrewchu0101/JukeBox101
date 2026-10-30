# JukeBox101 — Phase 2A：Discord Bot Application Foundation

## 0. 任務授權與前置狀態

本次正式授權開始 Phase 2A。

Phase 1A～1D 已完成人工驗收：

- 正式 MySQL Schema 已建立並驗證。
- 獨立測試資料庫 jukebox101_test 已建立。
- 14 項真實 MySQL 整合測試全部通過。
- 測試前後六張表資料筆數皆為 0。
- 未發現測試資料列殘留。
- Phase 1A～1D：Accepted。

本次任務允許長時間自主開發、重構必要程式碼、執行離線測試、修正測試失敗及更新文件。

不得自行開始 Phase 2B。

---

## 1. 專案目標

JukeBox101 是 Python Discord 音樂交換機器人。

使用者透過分享 YouTube 音樂獲得交換點數，再消耗點數取得其他使用者分享的音樂。

MVP 以 YouTube 為唯一支援平台。

Spotify 整合保留至 Phase 5，不得提前實作。

技術基礎：

- Python 3.12
- discord.py 2.x
- PyMySQL
- MySQL 8
- python-dotenv
- pytest
- 原生參數化 SQL
- 不使用 ORM

必須遵守專案 AGENTS.md 的所有規範。

## 2. 開發前審查

首先閱讀：

- AGENTS.md
- README.md
- ROADMAP.md
- pyproject.toml
- bot/ 既有模組
- tests/ 既有測試
- Phase 1 的資料庫 Schema

盤點現有功能，避免重複實作。

若 README 或 ROADMAP 與實際程式碼不一致，以可驗證的程式碼與已完成的人工驗收結果為依據，修正文檔。

不要為了套用新架構而重寫已經驗收的資料庫功能。

## 3. Phase 2A 的架構範圍

建立或完善以下分層：

### Discord Interface

負責：

- Bot 初始化。
- Slash Command 註冊。
- Discord Interaction 回覆。
- 使用者可見的錯誤訊息。
- Discord API 例外處理。

不得直接承擔複雜的 SQL 與交易邏輯。

### Application Services

負責：

- 分享歌曲流程。
- 交換歌曲流程。
- 點數查詢。
- 歷史紀錄。
- Daily 設定。
- 業務規則與例外轉換。

服務層不得依賴真實 Discord 網路連線，必須可獨立測試。

### Persistence

重用既有 PyMySQL Repository 或資料庫操作模組。

維持參數化 SQL 與明確交易邊界。

不得新增 ORM。

允許為測試加入必要的依賴注入，但避免建立過度複雜的抽象架構。

## 4. Slash Commands 範圍

本階段建立下列五組指令的程式碼與離線測試。

### /share

輸入：YouTube URL。

需求：

- 驗證 URL 格式。
- 解析並驗證 11 字元 YouTube Video ID。
- 支援合理的 YouTube URL 形式。
- 拒絕非 YouTube 網址。
- 使用現有 Schema 的唯一性規則。
- 同一 YouTube Video ID 不得重複發放全域分享點數。
- 正確處理重複投稿。
- 分享及點數發放應具有一致的交易語義。
- 不得因重試或競態條件重複發點。

不得使用 YouTube API、抓取網頁或自行推測歌曲 metadata。

若現有規格未定義歌曲標題等必要欄位的來源，應採取與 Schema 相容的最小實作，並記錄待決事項，不得虛構 metadata。

### /exchange

需求：

- 檢查使用者點數。
- 點數不足時拒絕交換。
- 選擇其他使用者分享的歌曲。
- 不得推薦自己的歌曲。
- 不得重複推薦已取得的歌曲。
- 成功交換消耗一點點數。
- 推薦紀錄與扣點必須保持一致。
- 沒有可交換歌曲時不得扣點。
- 避免併發交換造成超額扣點或重複推薦。

若現有 Schema 或交易 API 不足以安全實作，應標記該部分為 Pending，提出具體問題，不得自行執行 Schema Migration。

### /balance

需求：

- 查詢目前使用者點數。
- 正確處理新使用者。
- 不得將資料庫錯誤顯示成零點數。
- 不得暴露內部資料庫資訊。

### /history

需求：

- 顯示使用者過去取得的推薦紀錄。
- 保持穩定排序。
- 處理空紀錄。
- 限制 Discord 訊息長度。
- 避免在訊息中洩漏其他使用者的敏感資訊。

若分頁需求尚未定義，先實作簡單、安全且有上限的查詢，不自行擴充互動式分頁系統。

### /daily on/off

需求：

- 使用者可開啟或關閉每日推薦。
- 設定應持久化於現有資料庫設計。
- 重複設定應具有可預期的結果。
- 指令只負責偏好設定，不在 Phase 2A 實作正式排程器。
- 不得在此階段啟動背景每日派送或發送真實 DM。

正式 Daily 排程與發送流程保留後續 Phase。

## 5. Discord 啟動與指令同步策略

### Bot 初始化

- 使用 discord.py 2.x。
- 以既有專案設定載入 Discord Token。
- Token 缺失時應明確失敗，不得使用假 Token 連線。
- 不得輸出 Token。
- 初始化與登入流程分離，便於離線測試。
- 避免在模組 import 時產生網路連線或副作用。

### Slash Command 同步

採取明確、可控制的同步策略：

- 不得在模組 import 時同步指令。
- 不得在每次 on_ready 事件發生時重複同步。
- 不得在 Phase 2A 實際連線 Discord API。
- 將指令註冊與遠端同步分離。
- 提供可測試的同步入口。
- 預設不得自動進行遠端全域指令同步。
- 若需要開發伺服器 Guild Sync，先設計明確的 opt-in 機制，不得使用未知 Guild ID。
- 文件說明 Guild Sync 與 Global Sync 的差異，以及後續人工驗收流程。

不得硬編碼 Discord Token、Guild ID 或使用者 ID。

## 6. 設定管理

檢查並重用既有設定載入機制。

需要區分：

- Discord Bot 執行設定。
- 正式資料庫連線設定。
- 測試資料庫連線設定。
- 測試專用開關。

要求：

- 不得讓 TEST_DB_* 回退至正式 DB_*。
- 不得將真實憑證寫入程式碼。
- 不得修改使用者的 .env。
- 可以更新 .env.example，但不得包含真實憑證。
- 不得在錯誤訊息或測試輸出中洩漏密碼。
- 不得在 import 階段要求真實 Discord Token 或 MySQL 連線。

## 7. 交易與資料完整性

必須遵守 Phase 1 已驗收的 Schema。

重點：

- 歌曲唯一性。
- 投稿紀錄語義。
- 點數不可重複發放。
- 點數不可超額消耗。
- 推薦不可重複。
- 推薦與點數交易的一致性。
- Daily 設定的冪等性。
- 交易失敗時 Rollback。
- 資料庫連線正確釋放。

不得單純依靠應用程式中的先查詢、後寫入流程來宣稱具備併發安全性。

對需要交易鎖、唯一約束或其他並發控制的操作，必須說明實際採用的機制。

如無法在現有 Schema 下完整保證，保留明確的 Pending 項目。

## 8. 離線測試

新增或完善測試，涵蓋：

- Bot 初始化。
- Slash Command 註冊。
- Discord Interaction 回覆。
- URL 驗證。
- 重複分享。
- 點數發放。
- 點數不足。
- 無可交換歌曲。
- 避免推薦自己的歌曲。
- 避免重複推薦。
- 交易失敗與 Rollback。
- Daily 設定。
- 空歷史紀錄。
- Discord API 例外。
- 資料庫例外。
- 敏感資訊不外洩。
- 指令同步預設停用。
- 模組 import 不產生外部副作用。

所有新增測試必須能在沒有真實 Discord Token、沒有真實 MySQL 連線的環境執行。

使用 Mock、Fake Repository 或其他可驗證的隔離方式。

不得透過刪除測試、跳過測試或降低斷言品質來取得 Passed。

## 9. 程式碼品質

遵守 AGENTS.md。

特別要求：

- 函式實作最多 25 行。
- 巢狀深度最多 3 層。
- 優先使用 Guard Clauses。
- 使用必要的型別註記。
- 避免重複 SQL 與業務規則。
- 避免不必要的全域變數。
- 避免過度抽象。
- 不得引入未經批准的新依賴。
- 不得破壞 Phase 1 測試。

如既有測試與新架構發生衝突，先調查原因，不得直接移除原有保護。

## 10. 文件更新

更新 README.md 與 ROADMAP.md。

進度標示必須區分：

- Implemented
- Unit Tested
- Integration Pending
- Manual Acceptance Pending
- Accepted

Phase 1A～1D 保持 Accepted。

Phase 2A 即使全部離線測試通過，也不得自行標記為 Accepted。

說明：

- Bot 啟動方式。
- 所需環境變數。
- Slash Command 註冊機制。
- 遠端同步的人工控制方式。
- 如何執行離線測試。
- 哪些功能仍需要真實 Discord／MySQL 驗收。

## 11. 禁止事項

本階段嚴格禁止：

1. 連線或寫入真實 MySQL。
2. 執行 DDL 或 Schema Migration。
3. 使用真實 Discord Token 登入。
4. 執行 Discord 遠端指令同步。
5. 啟動正式 Daily Scheduler。
6. 發送真實 Discord DM。
7. 安裝新套件。
8. 整合 Spotify。
9. 雲端部署。
10. 修改 .env。
11. Git Commit、Push、Merge 或建立 Tag。
12. 刪除已驗收的資料庫安全測試。

若任務需要上述操作，記錄阻礙與建議，留待人工核准。

## 12. 自主執行流程

本任務不需要每完成一個小模組就等待人工確認。

請依序：

1. 審查現有專案。
2. 制定最小必要架構。
3. 實作 Discord Interface。
4. 實作或整合 Application Services。
5. 補齊五組 Slash Commands。
6. 撰寫離線測試。
7. 修正離線測試失敗。
8. 檢查安全性與程式碼品質。
9. 更新文件。
10. 整理完整驗收報告。

可以自主反覆執行離線測試。

如遇到會影響業務規則或資料完整性的未定義事項，不要猜測規格；應將受影響功能標記為 Pending，繼續完成其他不受阻礙的部分。

## 13. 最終驗收條件

Phase 2A 完成開發後，至少需要：

- 五組 Slash Commands 已建立或明確說明阻礙。
- 指令邏輯與 Discord API 分離。
- 沒有真實 Discord 或 MySQL 連線。
- 新增離線測試全部通過。
- Phase 1 原有離線測試未退化。
- 沒有硬編碼憑證。
- 沒有不受控的指令同步。
- 沒有未經授權的 DDL 或 Schema 修改。
- README 與 ROADMAP 正確反映進度。
- 所有未驗證功能清楚標記 Pending。

## 14. 最終回報

請提供：

1. Phase 2A 完成摘要。
2. 新增／修改檔案清單。
3. 五組 Slash Commands 各自的實作狀態。
4. Application Services 與 Persistence 架構。
5. Discord 啟動與同步策略。
6. 交易一致性與併發安全設計。
7. 測試指令與 Passed／Failed／Skipped。
8. 尚未解決的問題。
9. 下一階段人工驗收建議。
10. Git Status。

完成後停止。

不得自行開始 Phase 2B，也不得自行 Git Commit／Push。