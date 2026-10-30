# 18：Nickel 發放的 Schema 與 Migration（離線）

**What to build:** 新增發放紀錄所需的唯一鍵、帳本理由值，以及對應的 Migration 檔案，並在離線測試中驗證。不執行正式 Migration。

**Blocked by:** 17

**Status:** blocked

**File owner:** `bot/database/schema.sql`（僅新增欄位與約束，不改既有欄位）、`bot/database/migrations/`（新增一個 Migration 檔案）、`tests/test_nickel_grant_schema.py`（新）

**TDD 驗證：**
- 紅：同一人同一期同一類型只能有一筆發放（唯一鍵）。
- 紅：新理由值被接受；既有理由值不受影響。
- 紅：Migration 檔案可在隔離的離線替身上套用與回退（不連正式 MySQL）。
- 紅：既有測試的 schema 靜態檢查仍然通過（舊版本相容）。

**Acceptance criteria:**
- [ ] Migration 只新增，不修改或刪除既有資料。
- [ ] 回滾限制寫入 Migration 註解：已寫入的新理由紀錄不刪除，回滾只能停用功能。
- [ ] 不執行正式 Migration；不連線正式 MySQL。
- [ ] 離線測試以 Python 3.12 執行，列出通過、失敗、略過數量。

**Out of scope:** 發放服務邏輯（見 19）。
