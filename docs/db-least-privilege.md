# 資料庫最小權限與帳號分工（草案）

- 日期：2026-10-28
- 依據：執行期程式碼的實際 SQL 盤點（`bot/` 內 SELECT、INSERT、UPDATE、FOR UPDATE 與結構檢查），不是猜測。
- 狀態：**草案，未連線、未修改任何正式資料庫。** 授權範本見 [db-least-privilege-grants.sql](db-least-privilege-grants.sql)，只能在經授權的隔離資料庫中由 Owner 審閱後執行。

## 1. 帳號分工

| 帳號 | 誰使用 | 權限 | 不得擁有 |
|---|---|---|---|
| `jukebox101_app` | Bot 容器（Gateway 程序，包含 Owner 審核指令） | 業務表的 SELECT、INSERT、UPDATE | DELETE、DDL（CREATE、ALTER、DROP）、GRANT、SUPER、FILE、PROCESS、root 權限 |
| `jukebox101_migrator` | Owner，只在經核准的維護時段手動使用 | 結構變更權限（DDL）與必要的讀取 | 不放入 Bot 容器；不作為日常執行帳號 |
| `jukebox101_readonly` | 開發者，經 SSH 隧道或主機內部連線查詢 | 業務表的 SELECT | 任何寫入、DDL 與 GRANT |

- 三個帳號的密碼由 Owner 產生並保管，不寫入交付包、映像、Compose 檔或聊天。
- Bot 容器只掛載 `jukebox101_app` 的秘密檔案，不掛載 migrator 的秘密。

## 2. Bot 執行期所需權限（依實際 SQL）

以下是執行期程式碼實際用到的表。全部需要 SELECT；標示 INSERT 或 UPDATE 的代表程式會寫入。

| 表 | 權限 | 來源（概述） |
|---|---|---|
| `users` | SELECT、INSERT、UPDATE | 餘額快取、每日偏好、使用者建立 |
| `credit_ledger` | SELECT、INSERT | 所有 Nickel 變動的帳本（只追加，不修改） |
| `songs` | SELECT、INSERT、UPDATE | 歌曲身分與版本 |
| `recommendations` | SELECT、INSERT、UPDATE | 交換與派送進度 |
| `submissions` | INSERT | 投稿紀錄 |
| `jb2_control` | SELECT、INSERT、UPDATE | 單例控制（`FOR UPDATE` 需要 SELECT） |
| `jb2_videos`、`jb2_versions`、`jb2_evidence`、`jb2_claims`、`jb2_barriers`、`jb2_exclusions`、`jb2_legacy`、`jb2_events`、`jb2_settlements`、`jb2_receipts`、`jb2_reminders`、`jb2_reminder_batches`、`jb2_review_history` | SELECT、INSERT、UPDATE（依各表實際寫入） | 審核、版本、獎勵與結算 |
| `exchange_incidents`、`exchange_incident_events`、`exchange_redelivery_grants` | SELECT、INSERT、UPDATE | 補償與交換事件 |

- `jb2_migrations`：只由 migrator 的程式路徑寫入，執行期不使用，**不授予 `jukebox101_app`**。
- 結構檢查（`require_schema`）使用 `SELECT * ... LIMIT 0` 與 `information_schema` 讀取，對自己的表需要 SELECT；不需要額外權限。
- `SET SESSION`、`SET TRANSACTION` 為連線層設定，不需要資料庫權限。

## 3. 不需要且不應授予的權限

- **DDL**：執行期程式碼中，`CREATE TABLE` 只出現在 `submission_migration.py`，該模組沒有被執行期程式引用。因此 Bot 不需要 CREATE、ALTER、DROP。
- **DELETE**：執行期程式碼沒有 DELETE 語句，不授予。
- **GRANT、SUPER、FILE、PROCESS**：不需要。
- **root 或管理員帳號**：不提供給 Bot 或部署環境。

## 4. Owner 審核的權限來源

- Owner 審核指令（`/owner`）在 Bot 程序內執行，因此使用 `jukebox101_app` 的權限，不需要額外帳號。
- 審核寫入的表包含在第 2 節的清單中（`jb2_review_history`、`jb2_versions`、`jb2_evidence` 等）。
- **待驗證**：上述清單是依靜態盤點得出的。需在隔離資料庫中，以 `jukebox101_app` 實際執行審核流程，確認沒有權限錯誤，且沒有任何路徑需要 DELETE 或 DDL。

## 5. 驗證方式

1. **靜態**：以測試掃描執行期 SQL，確認語句類型只包含 SELECT、INSERT、UPDATE（不含 DELETE、DDL、GRANT）。（待建立測試，屬於票 07 的延伸或新票。）
2. **隔離資料庫**：以 `jukebox101_app` 登入，執行 Owner 審核與交換的流程，確認沒有權限錯誤。需 Owner 授權隔離 MySQL。
3. **負向測試**：以 `jukebox101_app` 嘗試 DDL 與 DELETE，應被拒絕。

## 6. 待 Owner 決定或提供

- 三個帳號的實際名稱與主機來源（由 Owner 決定，範本使用佔位符）。
- 外部 MySQL 的位置與安全連線方式（TLS 與網路限制）。
- 備份與還原的責任人與範圍（部署前確認）。
- 隔離驗證的授權（見第 5 節第 2 點）。

## 6. 鎖定與讀取方式（/claim，已實測於隔離容器）

- 領取交易的順序固定：先 `INSERT IGNORE` 建立使用者列 → `SELECT ... FOR UPDATE` 鎖定使用者列 → 記錄請求鍵 → 以 `LOCK IN SHARE MODE` 讀取發放狀態 → 寫入帳本、發放與餘額。
- `LOCK IN SHARE MODE` 是當前讀取（讀最新已提交資料），只需要 SELECT 權限。
- `SELECT ... FOR UPDATE` 需要 **UPDATE** 權限。應用程式帳號對使用者列擁有 UPDATE（用於餘額），因此對 `users` 的鎖可以使用；對 `nickel_grants` 不使用 `FOR UPDATE`，因此不需要對該表授予 UPDATE。
- `/balance` 類的唯讀路徑使用一般讀取，不取得任何鎖，也不寫入。

| 表 | 應用程式帳號的實際授權 | 用途 |
|---|---|---|
| `users` | SELECT、INSERT、UPDATE | 建立列、鎖定列（FOR UPDATE）、餘額 |
| `credit_ledger` | SELECT、INSERT | 帳本只追加 |
| `nickel_grants` | SELECT、INSERT（**無 UPDATE**） | 發放紀錄，只追加 |
| `nickel_claim_requests` | SELECT、INSERT（**無 UPDATE**） | 請求鍵，只追加 |

注意：此表列的是草稿授權（`docs/db-least-privilege-grants.sql`）在隔離資料庫中已套用的結果，不代表正式資料庫已授權。
