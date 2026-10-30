# MySQL → PostgreSQL 資料搬遷方案（**未執行**）

> 狀態：**計畫書，沒有實作、沒有執行，也沒有連過任何 MySQL 或正式資料庫**。v7 預設部署使用全新的空 PostgreSQL 資料庫。
> 只有在 Owner 決定保留 v6 資料時，才需要依本方案另外實作與驗收。完成驗收前，不得宣稱資料搬遷已完成。

## 1. 原則

- **MySQL 端**：只用唯讀帳號，在交易一致的快照中讀取（例如 `START TRANSACTION WITH CONSISTENT SNAPSHOT`），不寫入、不刪除。
- **PostgreSQL 端**：匯入目標必須是全新的資料庫，只套用 `0001`（schema）與 `0002`（權限）。
- **不可沿用 `0003`**：它在已有資料時會拒絕執行，這是刻意的保護。control 列必須由一個另外審核的「匯入版」步驟建立。
- **單一交易**：整批匯入在 PostgreSQL 的一個交易內完成。任何驗證失敗都要整筆回復，只留下紀錄。

## 2. 欄位轉換

| MySQL | PostgreSQL | 注意事項 |
|---|---|---|
| `BIGINT UNSIGNED`（Discord ID） | `BIGINT` | 大於 2^63−1 的值無法存入；必須先盤點，再由 Owner 決定處理方式，不可截斷 |
| `AUTO_INCREMENT` 產生的 ID | `GENERATED ALWAYS AS IDENTITY` | 匯入時使用 `OVERRIDING SYSTEM VALUE` 保留原值，之後用 `setval` 把序列推進到最大值之後 |
| `DATETIME(6)`（UTC） | `TIMESTAMPTZ` | 以 UTC 解讀；`dispatch_date` 維持原本的 Asia/Taipei 日曆日 |
| `ENUM` | `TEXT` 加 CHECK | 值必須完全相同 |
| `JSON` | `json` | MySQL 會把 JSON 正規化；codec 解碼後必須等價，逐筆驗證 |
| ascii_bin 字串 | `COLLATE "C"` | 都區分大小寫；影片 ID 要通過 `^[A-Za-z0-9_-]{11}$` 檢查 |
| `recommendations.active_slot` | 部分唯一索引 | 不搬這個欄位，由 `released_at` 決定 |

## 3. 匯入後必須通過的驗證（待實作）

1. 每張表的筆數與 MySQL 快照相同，所有外鍵與 CHECK 約束都成立（匯入時不停用約束）。
2. `ledger_integrity` 檢查回報 PASS：
   - 每個使用者的餘額等於他的 ledger 總和。
   - 每筆發獎都只有一筆對應的領取紀錄。
   - 沒有孤兒資料。
3. `jb2_events.accepted_sequence` 從 1 開始連續、不重複。control 的 `accepted_sequence` 等於最大值，`revision` 不小於原值。
4. 所有 receipt、review 與 evidence 的 JSON，都能用現行 codec 解碼。
5. control 的旗標由 Owner 審核後決定：`settlement_enabled` 預設為 false；`snapshot_ready` 與 `legacy_writers_quiesced` 依舊資料的實際狀態判斷，不自動設為 true。
6. 匯入後的資料庫執行 `python -m bot.readiness` 與 `migrate status`，並保留一份匯入前的 PostgreSQL 備份與 MySQL 匯出檔，作為回滾依據。

## 4. 需要 Owner 決定

- 是否保留 v6 資料，以及搬遷的停機時段（搬遷期間 v6 Bot 必須停止）。
- 超出範圍的 ID、無法解碼的 JSON，或違反約束的資料要怎麼處理（不可自動修正或刪除）。
- 補償與交換相關的資料表是否搬遷。補償資料表不在 v7 的 schema 中。
