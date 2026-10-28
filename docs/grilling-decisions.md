# Grilling 決策紀錄

本文件記錄 JukeBox101 開發方向的 Owner 決策。它不是執行授權：每次真實 DB 寫入、FK DDL、Recovery 寫入、Discord Gateway 啟動、Git Push 或 Merge 都需要當次明確授權。

## 已決定

| 編號 | 主題 | 決定 |
| --- | --- | --- |
| Q1 | 下一步順序 | 先 Recovery 唯讀獨立複驗，再 Batch B 真實 MySQL 驗收，最後才處理新功能。 |
| Q2 | Batch B 與 Beta 關係 | Batch B 真實整合與人工驗收通過前，不得開放 Beta 資料庫寫入。 |
| Q3 | Beta 功能範圍 | 第一版只含 `/share`（YouTube-only）、`/history`、`/balance`。啟用前須確認 Ledger 與餘額一致性。 |
| Q4 | Spotify | Beta 期間維持 YouTube-only；Spotify 延後到獨立階段。不修改 AGENTS 的長期產品規格。 |
| Q5 | 授權粒度 | 每次真實 DB 寫入、FK DDL、Discord Gateway 啟動都需當次明確授權，限定目標與 Session。 |
| Q6 | `.env` 清理 | 已由 Q11 取代，見下。 |
| Q7 | Recovery 唯讀複驗 | 由 Claude Code 使用既有唯讀入口執行一次。限定 `127.0.0.1:3306/jukebox101_test`、既有唯讀帳號、只允許 SELECT。異常即停止，不自動修復。實際執行前須另行當次授權。 |
| Q8 | Recovery 複驗完成標準 | 必須通過：fingerprint 與 Baseline 一致、FK 規則符合預期、Control 符合預期、各表筆數符合預期、Manifest 15 個 PK 零殘留。無法補齊的歷史缺口標為 Historical Evidence Gap，不得偽稱已補齊。任一必要條件未通過，Batch B 不得啟動。 |
| Q9 | Ledger 與餘額一致性 | Beta 啟用前，唯讀確認每位使用者 `users.credit_balance` 等於 `credit_ledger.credit_delta` 加總，且無孤立 Ledger 或 Claim。須依實際 Schema 建立檢查，不假定初始餘額為零。不一致時不得開放 Beta 寫入。 |
| Q10 | Harness NO ACTION 自引用 FK | 允許有限度的離線修正，但不得關閉 FK 檢查或放寬資料庫限制，不修改正式 Beta DB，不執行真實 DDL、DML 或 MySQL 整合測試。完成後提交差異與測試結果供審查。 |
| Q11 | `.env` 三組測試密碼 | **Owner 決定保留**。`DB_TESTAPP_PASSWORD`、`DB_jukebox101_test_migrator_PASSWORD`、`DB_HARNESS_PASSWORD` 為本機暫存憑證，保留現有值。不要求刪除，不修改權限設定，不要求程式引用。`.env` 必須持續被 Git 忽略。不得輸出、記錄、提交或複製密碼值。這三個欄位的存在不構成阻擋 Recovery、Harness 或 Beta 的理由。這不代表授權將來使用這些密碼連線。 |
| Q12 | 私人 Guild 與測試者 | `DISCORD_DEV_GUILD_ID`、`DISCORD_EXPECTED_APPLICATION_ID`、`DISCORD_OWNER_USER_ID` 於執行當下以程序環境提供，不寫入 `.env`。人工驗收使用兩個不同的 Discord 測試帳號驗證 `/history` 隔離。Gateway 啟動仍需當次授權。 |
| Q13 | Git Commit 與 Push | 每個本機 Commit 需當次同意，提交前審查差異、測試結果與敏感資訊；不得混入無關既有修改。Push、Merge、Remote 設定需另行授權，本階段不執行 GitHub 寫入。 |
| Q14 | `/daily` 時區 | 採用 `Asia/Taipei`，與 `schema.sql` 一致。未來 `/daily` 的日期切換、派送日與排程判斷都使用此時區，不依賴作業系統時區。本次不授權實作 `/daily`。 |

## 補充決定（Q15、Q16）

| 編號 | 主題 | 決定 |
| --- | --- | --- |
| Q15 | Ledger 與舊餘額不一致 | 唯讀檢查發現無法依已確認帳務規則對帳時：立即停止相關驗收；Private Beta 不得開放資料庫寫入；建立獨立的 Ledger Integrity Audit 階段；保留原始 Ledger、餘額與歷史資料；不自動補寫 Opening Ledger、不歸零餘額、不執行自動修復或補償交易；提交差異與證據由 Owner 另行裁決。在確認實際資料與歷史帳務規則前，不假設舊餘額必然錯誤。 |
| Q16 | Harness NO ACTION FK 阻礙 | 接受既有離線實作的 DBA-supervised temporary CASCADE FK window 作為 Batch B 真實驗收路徑，但每次執行須符合：當次 Owner 明確授權（指定資料庫、Session、FK DDL 與測試寫入範圍）；僅限 `127.0.0.1:3306/jukebox101_test`；全新 Session UUID；不解除或覆寫舊 Session 的 Blocked 狀態；執行前完成 Recovery 唯讀複驗與全部 Preflight；視窗由既有 Supervisor 管理，不得手動繞過；不得停用 `FOREIGN_KEY_CHECKS` 或擴大權限；測試後恢復原始 NO ACTION FK；驗證 Manifest 精確清理、Control 還原、Baseline fingerprint 與 FK metadata；任何失敗、未知結果或無法確認恢復即停止，不自動重試，不宣稱驗收成功；不得操作 Beta DB 或正式資料庫。 |

本決策只確認採用既有監督式方案，不代表已完成真實環境驗證，也不授權立即執行 DDL 或真實 MySQL 測試。

## 相關事實（只讀盤點）

- Recovery 狀態：`RECOVERY_SUCCESS`，來源為使用者回報，尚未獨立複驗。Manifest 的 `evidence_status` 為 `fresh_recheck_required`。
- Harness：`verify_cleanup_readiness` 在缺少臨時 CASCADE FK 視窗時拒絕啟動（`SELF_ROOT_CLEANUP_REQUIRES_FK_WINDOW`）。
- 餘額不變式：`bot/database/reward_settlement.py` 以 `LedgerBalanceMismatch` 拒絕不一致；遷移文件註明「不修改舊餘額」。
