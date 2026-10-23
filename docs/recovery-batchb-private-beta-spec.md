# Recovery、Batch B、Ledger Integrity 與 Private Beta 統一規格

狀態：**DRAFT_FOR_OWNER_REVIEW**（2026-10-23，第四版：PD-1 至 PD-4 的審查方案、REC-A9／REC-A10 待核准條件、TK-01～TK-12 授權審查草稿）。本文件只定義規格、靜態審查與工作草稿。所有行為皆為 `Unverified`；本文件沒有任何測試或實機結果被標記為 `Passed`。

## 0. 文件範圍與約束

- **決策來源**：`docs/grilling-decisions.md` 的 Q1～Q16 為唯一決策來源（歷史紀錄，本輪未修改）。本文件不改寫已核准的產品規則。
- **本輪不包含**：資料庫連線、SQL／DDL／DML 執行、Recovery 或 Harness 執行、任何 pytest 或測試收集、任何專案模組的匯入或執行、Discord Gateway 與遠端指令同步、程式碼、`.env`、`.env.example` 修改、套件安裝、Git 寫入。
- **工作樹狀態**：本輪未重新確認。2026-10-23 的 `git status` 顯示 62 個 `M`，其中 `CLAUDE.md` 為 `6b3e1fe` 之後由本次會話修改的檔案。其餘 61 個的數量與先前盤點相同，**未逐檔重新確認**，本文件不把該數字當作現況。
- **Requirement ID**：`<章節>-<主題>-<序號>`。Q1～Q16 對應見第 5 節；未裁決事項見第 9 節。
- **證據等級**：`Passed` 只能來自實際執行的測試或受授權的實機驗收。第 2.10 與 2.11 節是原始碼閱讀結果，不是測試結果。

### 0.1 執行順序（Q1、Q2）

```
Recovery 唯讀複驗（第 1 章）
  → Batch B 真實 MySQL 驗收（第 2 章）
  → Ledger Integrity 唯讀對帳（第 3 章，目標為 Beta DB，見 P-L3）
  → Private Beta 啟用（第 4 章，含 BETA-DB-1 的 Control 變更，見 PD-4）
```

- Recovery 與 Batch B 有技術依賴（Q1）。Ledger 對帳與 Batch B 沒有技術依賴，但目標是 Beta DB，且必須在 Beta 啟用前完成（Q9）。
- 任一前置階段未通過，後續階段不得開始。Spotify、`/exchange`、`/daily` 不在本規格的實作範圍。

### 0.2 已裁決與真正待決

**已裁決，不再列為 Owner 決策問題**：

| 項目 | 裁決內容 | 目前狀態 |
| --- | --- | --- |
| CONF-1 `/history` 語意 | Beta 採「本人投稿與目前審核狀態」，記為 Beta 範圍限制（BETA-SCOPE-1） | 裁決已定；文件段落修訂為 TK-04，未執行 |
| CONF-3 憑證措辭 | 區分「範本不應包含真實測試憑證」與「Owner 本機 `.env` 保留暫存欄位」 | 裁決已定；`.env.example` 修改為 TK-04，未執行 |
| P-R1 唯讀帳號方案 | 採 DBA 建立專用唯讀帳號 | 方案已定；帳號建立、權限配置與實際使用授權**均未完成**（TK-05） |
| Q11 三組暫存欄位 | 保留，不要求刪除，不構成阻擋，不授權使用 | 已定；`.env` 本輪未讀取、未修改 |

**真正待決（需要 Owner 最後裁決）**：PD-1、PD-2、PD-3、PD-4、P-L3。第 9 節列出完整清單。

---

## 1. Recovery 唯讀獨立複驗

### 1.1 Scope / Out of Scope

- **Scope**：在 `127.0.0.1:3306/jukebox101_test` 上，以唯讀方式核對 fingerprint、FK 規則、Control、各表筆數與 Manifest 15 個 PK 的殘留狀態，並依 PD-2 決定是否加入反向參照檢查。
- **Out of Scope**：任何 DDL、DML、Recovery 寫入、修復、重試或補償；對正式資料庫或 Beta 資料庫的任何操作。

### 1.2 Preconditions

- Owner 的當次明確授權（REC-G1）。
- 唯讀帳號已由 DBA 依 PD-3 的權限清單建立（P-R1 方案已定；建立為獨立授權）。帳號不得是 `jukebox101_test_app`、`jukebox101_test_migrator` 或 `jukebox101_test_harness`。
- 目標 `127.0.0.1:3306/jukebox101_test`。
- 全新 Session UUID。既有 Harness Session 維持 Blocked（REC-S3）。
- 注意：Owner 核准的目標實例 UUID 為 `ab73bd54-c3c3-11f1-bc47-005056c00001`（`TEST_DB_SERVER_UUID`，見 BB-P3），與 Session UUID 不是同一個值。

### 1.3 Functional Requirements

| ID | 要求 |
| --- | --- |
| REC-F1 | 連線前檢查目標 `host/port/database` 與授權開關完全相符，任一不符即中止。 |
| REC-F2 | 只發出 `SELECT`。交易收尾方式未裁決（PD-1），在 PD-1 裁決前，實作不得呼叫任何交易控制語句或 rollback。 |
| REC-F3 | 核對 `information_schema.TABLES` 中 23 個 InnoDB 基本表，並以 `SHOW CREATE TABLE` 計算 schema fingerprint，與 Baseline 比對（完整參考值記於 `docs/stage-b-only-run.md`）。 |
| REC-F4 | 核對 FK metadata：欄位、參照表、參照欄位與 `ON DELETE`／`ON UPDATE`，必須符合 `docs/batch-b-recovery-manifest.json` 的 `reviewed_fk_column_edges`（29 條）。來源為 `KEY_COLUMN_USAGE` 與 `REFERENTIAL_CONSTRAINTS`（現有 `tests/batch_b_recovery.py:259–260` 即使用此來源）。 |
| REC-F5 | 讀取 `jb2_control`（singleton_id = 1）：`accepted_sequence = 0`、`revision = 0`、`snapshot_ready = 0`、`legacy_writers_quiesced = 0`、`settlement_enabled = 0`。 |
| REC-F6 | 比對 23 張表的筆數。Manifest 的 delete manifest 合計 15 個 PK（10 張表），必須已不存在（零殘留）。 |
| REC-F7 | 核對 `jb2_migrations` 的 applied marker 與 Manifest 的 `schema_version`。 |
| REC-F8 | 輸出只含結構化摘要（計數、布林、fingerprint 雜湊），不輸出密碼、連線字串或完整資料列。 |
| REC-F9 | **（待 PD-2 裁決）** 對 15 個已刪 PK，在所有以其為參照目標的表中查詢殘留的參照列，結果必須為零。 |

### 1.4 Safety Invariants

- **REC-S1**：只發出 `SELECT`。任何非 SELECT 語句立即中止。
- **REC-S2**：不得停用 `FOREIGN_KEY_CHECKS`，不得修改 Control 或 Schema。
- **REC-S3**：不得解除或覆寫既有 Harness Session 的 Blocked 狀態。
- **REC-S4**：目標不符時立即失敗，不退而使用其他帳號或資料庫。
- **REC-S5**：不讀取、輸出或記錄 `.env` 中的密碼值（SEC-2）。

### 1.5 Acceptance Criteria 與 HEG 分類（Q7、Q8）

Recovery 複驗通過的必要條件（全部為真）：

| ID | 條件 | 判定 |
| --- | --- | --- |
| REC-A1 | 目標 host/port/database 與授權相符 | 必要 |
| REC-A2 | schema fingerprint 與 Baseline 一致 | 必要 |
| REC-A3 | 23 張表的 FK metadata 與 29 條 reviewed edges 一致 | 必要 |
| REC-A4 | `jb2_control` 符合 REC-F5 | 必要 |
| REC-A5 | 各表筆數與 Manifest 記錄一致 | 必要 |
| REC-A6 | 15 個 PK 零殘留 | 必要 |
| REC-A7 | 無非 SELECT 語句被執行 | 必要 |
| REC-A9 | 15 個已刪 PK 的反向參照列為零（定義見 1.10） | **新增驗收條件，待 Owner 核准**（PD-2）；未核准前不列為必要 |
| REC-A10 | 正向控制：唯讀帳號可見的基表數為 23、FK 邊數為 29，且與 DBA 特權基準計數一致（定義見 1.11） | **新增驗收條件，待 Owner 核准**（PD-3）；未核准前不列為必要 |
| REC-A8 | 結果只能是 `RECOVERY_VERIFIED_READONLY` 或 `RECOVERY_NOT_VERIFIED` | 必要 |

**Manifest 的 `unresolved` 五項分類**（依 2026-10-23 的 Manifest 內容重新分類）：

| Manifest 項目 | 分類 | 依據 |
| --- | --- | --- |
| `historical_failing_delete_target_not_recorded` | HEG-1 | 歷史事實。`docs/batch-b-recovery-investigation.md` 記載失敗的 delete 目標未留存，無法還原。 |
| `actual_foreign_key_rules_not_supplied_to_auditor` | 由 REC-A3 取代 | 現行 FK metadata 可由 REC-F4 直接讀取。仍需實機執行。 |
| `fresh_version_canonical_id_and_all_row_links` | 拆為 HEG-2a 與 REC-F9／PD-2 | 見 1.10。已刪 row 的歷史 `canonical_id` 無法還原（HEG-2a）；但「目前是否仍有列參照已刪 PK」可直接查詢，不屬於 HEG。 |
| `fresh_current_fingerprint_and_full_metadata_visibility` | REC-A2 ＋ HEG-3 | fingerprint 由 REC-A2 取代。「完整 metadata 可見性」受唯讀帳號權限限制，見 1.11。 |
| `explicit_recovery_write_authorization` | 維持未授權 | 不屬於唯讀複驗（REC-G2）。 |

- **HEG-3 的限制**：若唯讀帳號的 metadata 權限不足，`information_schema` 可能不完整，無法證明 trigger 或隱藏物件不存在。結論中必須明寫此限制。

### 1.6 Failure / Rollback Behavior

- 任何 REC-A* 失敗、連線異常、非預期 SQL、帳號權限異常、無法判定結果：立即停止，回報失敗點，不自動重試。
- **交易收尾**：PyMySQL 應用程式連線使用 `autocommit=False`（`bot/database/connection.py:31`），SELECT 亦會開啟交易。收尾方式屬於 PD-1，本節不自行決定。
- 失敗後不得進入 Batch B（BB-G1）。

### 1.7 Test Plan

- **離線（未來實作後）**：純函式測試，涵蓋 REC-F1 的目標比對、REC-S1 的語句白名單、REC-F8 的輸出遮蔽，使用合成 fixture，不連線 MySQL。
- **實機（需授權）**：一次唯讀執行，記錄 REC-A1～A9 的實際結果。
- 目前狀態：**全部 Unverified**。

### 1.8 Owner Authorization Gates

- **REC-G1**：每次複驗前的當次授權，須指定 Session UUID、唯讀帳號、PD-1 的收尾方式與 PD-2 的結果。
- **REC-G2**：任何寫入或 Recovery 寫入須另行授權。

### 1.9 PD-1：交易控制與 consistent snapshot（建議方案 B，待 Owner 核准）

**現況**：`bot/database/connection.py:31` 與 PyMySQL 預設皆為 `autocommit=False`。`tests/batch_b_recovery.py:254` 的既有 Recovery 讀取要求 `@@autocommit = 1`。兩者不一致。Q7 字面只授權 SELECT。

**建議方案 B 的完整語句清單（供 Owner 一次審查；尚未核准，尚未授權連線）**：

| 順序 | 語句 | 類別 | 用途 |
| --- | --- | --- | --- |
| 1 | `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ` | 交易控制 | 只作用於下一個交易；不用 `SET SESSION`，避免改變連線狀態 |
| 2 | `START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY` | 交易控制 | 開始交易，並在本語句執行時建立 snapshot |
| 3 | 之後的所有 `SELECT` | 讀取 | 必須在步驟 2 之後、同一連線、同一交易中執行 |
| 4 | `SELECT @@autocommit, @@transaction_isolation, @@transaction_read_only` | session 狀態讀取 | 連線初始化驗證；預期為 0、`REPEATABLE-READ`、1（變數名稱依 MySQL 版本，由 DBA 確認） |
| 5 | `ROLLBACK` | 交易收尾 | 成功或失敗都執行；不使用 `COMMIT` |

**連線初始化行為**：
- 必須使用 TK-05 的唯讀帳號，且該帳號不得具有寫入權限。
- 連線後的第一個交易控制語句為步驟 1，之前不得執行任何其他語句。
- 步驟 4 的任一值不符即停止，不繼續查詢。
- 不使用 `autocommit=True`（那是方案 A）。

**為何不能只寫 `START TRANSACTION READ ONLY`**：
- `START TRANSACTION READ ONLY` 只宣告唯讀，**不保證**在該語句執行當下建立 snapshot；InnoDB 可能延到第一次一致性讀取時才建立。因此步驟 2 必須包含 `WITH CONSISTENT SNAPSHOT`。
- 隔離層級明確設為 `REPEATABLE READ`，不依賴連線預設值；DBA 以步驟 4 驗證。

**收尾**：
- 所有查詢完成後（或任何錯誤後），以 `ROLLBACK` 結束交易，再關閉連線。
- `ROLLBACK` 失敗、連線中斷，或無法確定交易狀態 → 結論為 `RECOVERY_NOT_VERIFIED`（Ledger 為 Blocked）。不重試、不以 `COMMIT` 補救。

**適用範圍的限制（必須揭露）**：
- snapshot 保證的是 InnoDB 資料表的一致性讀取。
- `information_schema` 與 `SHOW CREATE TABLE` 屬於 metadata 讀取，**是否受同一 snapshot 約束，待 DBA 以實際 MySQL 版本確認**。若無法確認，結論須標明 fingerprint 部分的時間點不確定。
- 方案 B 只保證本連線視角的一致性，**不保證其他寫入者已停止**。寫入者是否凍結需要 PD-4 的 legacy writer 證據，不由本方案提供。

**需要 Owner 核准的內容**：
1. 上表是否為唯讀流程允許的完整語句集合。這會擴大 Q7 的字面範圍：SELECT 之外新增三類語句（交易控制、session 狀態讀取、收尾 ROLLBACK）。
2. 是否接受上述 metadata 不確定的揭露方式。

**實機驗證（Unverified）**：snapshot 語意本身只能在 DBA 授權的環境中以探測方式驗證，探測需要寫入，須另行授權。本文件不假設已驗證。

**與方案 A 的比較**：方案 A（SELECT-only，autocommit=True）不擴大授權，但不保證跨查詢一致；若 Owner 不核准方案 B，則採方案 A，並在結論中揭露無 snapshot 保證。

**本文件未核准任何方案，也未授權連線。**

### 1.10 PD-2：REC-A9「已刪 PK 無殘留參照」（建議新增為必要條件，待 Owner 核准）

**分類**：
- **可查驗現況（不歸為 HEG）**：目前資料庫中是否仍有列參照已刪 PK。
- **HEG-2a（歷史，不可還原）**：已刪 row 刪除前的 `canonical_id` 與其他 row 的連結。`delete_manifest` 只記錄 PK，原值未留存。本規格**不要求、也不接受以推測或捏造方式補齊**。

**查驗範圍**（2026-10-23 依 `docs/batch-b-recovery-manifest.json` 唯讀核對）：

`delete_manifest` 共 15 個 PK，分屬 10 張表：

| 已刪表 | PK 欄位 | 筆數 |
| --- | --- | --- |
| `jb2_decisions` | `decision_id` | 1 |
| `jb2_events` | `operation_id` | 1 |
| `jb2_evidence` | `evidence_id` | 4 |
| `jb2_receipts` | `operation_id` | 1 |
| `jb2_reminders` | `video_id` | 1 |
| `jb2_review_history` | `(revision, video_id)`（複合） | 3 |
| `jb2_versions` | `version_id` | 1 |
| `jb2_videos` | `video_id` | 1 |
| `songs` | `song_id` | 1 |
| `users` | `discord_user_id` | 1 |

- `jb2_review_history` 為複合 PK。目前沒有任何 reviewed FK 指向它。實作時若出現指向複合 PK 的邊，必須以整組 tuple 比對，不得只比對單欄。

**對應規則**：
- 以 `reviewed_fk_column_edges`（29 條，每條結構為 `[參照表, 參照欄, 順序, schema, 被參照表, 被參照欄]`）為準。
- 篩選條件：被參照表屬於上表的已刪表。
- 核對結果：29 條中 **23 條**符合，且其被參照欄全部為上表的 PK 欄位。
- 其餘 6 條參照 `credit_ledger` 與 `recommendations`。這兩表不在 delete manifest 中，不屬於 REC-A9 範圍，仍受 REC-A3 約束。

**23 條邊的明細（供 DBA 與 Owner 審閱）**：

| 參照表.欄 | 被參照表.欄 |
| --- | --- |
| `credit_ledger.awarded_song_id` | `songs.song_id` |
| `credit_ledger.discord_user_id` | `users.discord_user_id` |
| `daily_dispatches.discord_user_id` | `users.discord_user_id` |
| `jb2_barriers.version_id` | `jb2_versions.version_id` |
| `jb2_claims.discord_user_id` | `users.discord_user_id` |
| `jb2_claims.operation_id` | `jb2_events.operation_id` |
| `jb2_claims.version_id` | `jb2_versions.version_id` |
| `jb2_events.discord_user_id` | `users.discord_user_id` |
| `jb2_events.video_id` | `jb2_videos.video_id` |
| `jb2_evidence.video_id` | `jb2_videos.video_id` |
| `jb2_exceptions.version_id` | `jb2_versions.version_id` |
| `jb2_exclusions.version_id` | `jb2_versions.version_id` |
| `jb2_legacy.song_id` | `songs.song_id` |
| `jb2_receipts.operation_id` | `jb2_events.operation_id` |
| `jb2_reminders.video_id` | `jb2_videos.video_id` |
| `jb2_review_history.video_id` | `jb2_videos.video_id` |
| `jb2_settlements.operation_id` | `jb2_events.operation_id` |
| `jb2_versions.canonical_id` | `jb2_versions.version_id` |
| `jb2_videos.song_id` | `songs.song_id` |
| `recommendations.discord_user_id` | `users.discord_user_id` |
| `recommendations.song_id` | `songs.song_id` |
| `submissions.discord_user_id` | `users.discord_user_id` |
| `submissions.song_id` | `songs.song_id` |

**REC-A9 的查驗方式**（對 23 條逐條執行，參數化、只讀）：

```
SELECT COUNT(*) AS residual_count
FROM `<參照表>`
WHERE `<參照欄>` IN (%s, %s, …)   -- 值為 delete manifest 中該被參照欄的已刪 PK 值
```

- 每條邊獨立計數。結果只輸出「邊的識別與計數」，不輸出列值。
- 通過條件：23 條計數皆為 0。

**前置條件與停止行為**：
- 只有在 REC-A3 通過（29 條 FK metadata 與 Manifest 一致）後，REC-A9 才有效；否則停止，不執行 REC-A9。
- 任何一條邊的被參照欄不在對應的 PK 欄位中，或無法對應到 manifest 的已刪表 → 停止，判定 `RECOVERY_NOT_VERIFIED`，不推測。
- 任一計數查詢失敗或無法判定 → `RECOVERY_NOT_VERIFIED`。
- 任一計數 > 0 → `RECOVERY_NOT_VERIFIED`，只回報邊的識別與計數，不修復、不刪除、不輸出列值。

**範圍限制**：
- REC-A9 只涵蓋 FK 強制的參照。觸發器或應用程式層的參照不在範圍內，屬於 HEG-3 的 metadata 可見性議題（見 1.11）。
- 通過 REC-A9 不代表歷史 `canonical_id` 已還原；HEG-2a 仍然存在。

**Owner 決策**：是否將 REC-A9 列為必要條件（建議：是）。這是新增的驗收條件，**未經 Owner 核准前，不列入 Q8 已核准的條件**。

### 1.11 PD-3：唯讀帳號的 metadata 權限（由 DBA 提供方案，Owner 核准）

**原則**：
- MySQL metadata 權限細節由 DBA 依實際版本提供方案，Owner 只核准方案，不自行決定細節。
- 不預設需要全域權限、schema 層級的廣泛權限或 TRIGGER 權限。專案先例為不新增廣泛權限（`docs/batch-b-real-mysql-tests.md:138`）。
- **metadata 查詢回傳空集合，不得解讀為「沒有資料表或 FK」**。必須先通過正向控制（REC-A10）才能解讀查詢結果。

**專案既有證據（只列來源，不判定版本行為）**：

| 檢查 | 查詢來源 | 專案內的已知事實 |
| --- | --- | --- |
| 物件清單 | `information_schema.TABLES`（`tests/batch_b_harness_database.py:128`） | 受 ACL 限制 |
| Trigger | `information_schema.TRIGGERS`（`tests/batch_b_harness_database.py:132`） | `docs/batch-b-real-mysql-tests.md:254` 明載：無 TRIGGER 權限時，查不到 trigger **不能證明不存在** |
| FK metadata | `KEY_COLUMN_USAGE`、`REFERENTIAL_CONSTRAINTS`（`tests/batch_b_recovery.py:259–260`） | 受 ACL 限制 |
| 角色 | `information_schema.APPLICABLE_ROLES`（`tests/batch_b_harness_database.py:53`） | 專案 Preflight 要求無可用角色 |

**需要 DBA 提供的技術資料（不由 Owner 決定）**：
1. 目標 MySQL 的精確版本（`VERSION()` 的輸出，由 DBA 在授權環境取得）。
2. 唯讀帳號的最小權限方案，以 GRANT 語句清單形式提出。**本文件不執行、不核准任何 GRANT。**
3. 方案中每個 metadata 來源的可見性條件：`information_schema.TABLES`、`KEY_COLUMN_USAGE`、`REFERENTIAL_CONSTRAINTS`、`TRIGGERS`，以及 `SHOW CREATE TABLE`，各需要哪一層權限才能看到目標物件。
4. 確認唯讀帳號沒有可用角色（`CURRENT_ROLE()` 為 NONE）。
5. TRIGGER 的處理方式：若方案不含 TRIGGER 權限，trigger 存在性不可證明，對應 HEG-3。
6. 在 DBA 的特權連線中，以相同查詢取得「特權基準計數」：基表數量 23、FK 邊數 29。此為 DBA 工作，需要獨立授權。

**正向控制 REC-A10（新增驗收條件，待 Owner 核准）**：
- 唯讀帳號讀取到的基表數量必須等於 23，FK 邊數必須等於 29（與 Baseline 及 Manifest 一致）。
- 數量不符 → `RECOVERY_NOT_VERIFIED`，判定為可見性不足，**不得**解讀為「表或 FK 不存在」。
- 若 DBA 無法提供特權基準計數，REC-A10 無法執行 → 停止，不以空集合或不完整結果通過。

**HEG-3 的分界**：
- 若 DBA 證明方案的 TRIGGER 可見性不足，HEG-3 仍然存在，結論需明寫。
- 若要消除 HEG-3，需另行裁決授予 TRIGGER 權限。那屬於權限擴大，不是預設值。

**交付物與核准流程**：DBA 提供第 1 至 6 項 → Owner 核准方案與 REC-A10 → 才可進入 TK-05（建立帳號，另行授權）。

### 1.12 現有入口的缺口

- `tests/test_mysql_readonly.py` 第 76–79 行斷言目標為 `jukebox101_app@jukebox101`，不是測試庫。現有入口不能作為 Recovery 唯讀複驗使用（見 GAP-R1）。

---

## 2. Batch B 真實 MySQL 驗收

### 2.1 Scope / Out of Scope

- **Scope**：在 `jukebox101_test` 上，以 DBA 監督的臨時 CASCADE FK window 執行 23 項既有 MySQL 測試，驗證清理、Control 還原與 NO ACTION FK 恢復。
- **Out of Scope**：正式資料庫、Beta 資料庫、任何非 `127.0.0.1:3306/jukebox101_test` 的目標，以及 Stage A 的既有入口。

### 2.2 Preconditions

- Recovery 唯讀複驗通過（REC-A1～A8；若 PD-2 採納則含 A9）。
- 全部 Preflight 通過（BB-P）。
- Owner 當次授權（BB-G1～G3）。
- 全新 Session UUID；Supervisor 為唯一執行路徑（BB-SUP1）。

### 2.3 Functional Requirements

| ID | 要求 |
| --- | --- |
| BB-P1 | Preflight：目標 host/port/database 與授權相符；應用程式、Harness、Migrator、DBA 四組帳號的權限與預期相符。 |
| BB-P2 | Preflight：Schema fingerprint 與 Baseline 一致；23 張表為 InnoDB；業務資料為空；Control 為零 Baseline。 |
| BB-P3 | Preflight：`TEST_DB_SERVER_UUID` 與 DBA 核准的值相符。 |
| BB-SUP1 | 唯一執行路徑為既有 Supervisor（`scripts/run-batch-b-acceptance.ps1 -StageBOnly`）。 |
| BB-SUP2 | 臨時 CASCADE FK window：先新增不同名稱的暫時 CASCADE FK，再移除原 NO ACTION FK；測試後先新增原 NO ACTION FK，再移除暫時 FK。 |
| BB-SUP3 | 每條 DDL 送出前寫 PENDING Journal；ACK 後寫 ACKNOWLEDGED；未知結果寫 UNKNOWN 並停止。 |
| BB-T1 | 執行第 2.10 節的 23 項，每項使用獨立 RunScope 與 Manifest。 |
| BB-T2 | 每項結束後依 Manifest 精確清理 PK，不做範圍式 DELETE。 |
| BB-T3 | 清理後 Control 還原為 `accepted_sequence=0`、`revision=0`、三開關為 0。 |
| BB-T4 | 全部結束後，獨立連線驗證 23 表、Registry、Control 與 FK metadata 與 Baseline 一致。 |

### 2.4 Safety Invariants

- **BB-I1**：不得停用 `FOREIGN_KEY_CHECKS`，不得放寬 FK、CHECK 或唯一約束（Q10）。
- **BB-I2**：不得擴大任何帳號權限；Harness 只保有原有精確清理權限。
- **BB-I3**：不得自動重試 UNKNOWN 或失敗的 DDL／DML。
- **BB-I4**：不得解除或覆寫既有 Harness Session 的 Blocked 狀態（Q16 第 4 點）。
- **BB-I5**：不得將 DBA 連線傳入應用程式 worker 的參數或環境。
- **BB-I6**：不得以手動方式繞過 Supervisor（Q16 第 6 點）。
- **BB-I7**（新增）：所有可執行 Batch B 驗收的入口（直接入口、Supervisor、worker 路徑）在任何資料庫連線或副作用前，拒絕以停用 assert 的模式（`python -O`）執行。目前專案沒有此保護（見 2.12 X1）；在 TK-03 完成前，BB-A1 不得以 PASS 結論宣告通過。本條為要求定義，尚未實作或驗證。

### 2.5 Acceptance Criteria

- **BB-A1**：23 項 Passed、0 Failed、0 Skipped。
- **BB-A2**：每項 Manifest 精確清理完成，殘留為 0。
- **BB-A3**：Control 最終為 `accepted_sequence=0`、`revision=0`、三開關為 0。
- **BB-A4**：原 NO ACTION FK 完整恢復，臨時 CASCADE FK 已移除。
- **BB-A5**：Baseline fingerprint 與 FK metadata 與 Recovery 複驗一致。
- **BB-A6**：所有 DDL 與 Commit 的 Journal 為 ACKNOWLEDGED，沒有 UNKNOWN 或 PENDING 殘留。
- 目前狀態：**全部 Unverified**。

### 2.6 Failure / Rollback Behavior

- **停止條件**：任何 DDL 或 Commit 為 UNKNOWN；任何測試 FAIL；Manifest 清理不完整；Control 無法還原；FK 無法確認恢復；worker audit 不完整。
- **停止行為**：Supervisor 只 rollback 未提交的工作、記錄失敗、關閉連線；不自動關閉開關、清理資料或重試。
- **恢復**：FK 恢復無法確認時，停止並由 Owner 裁決，不自動補救。
- **不宣稱**：失敗或無法確認恢復時，不宣稱驗收成功，也不解除既有 Blocked。

### 2.7 Test Plan

- **離線（已存在，未執行）**：`tests/test_batch_b_stage_only.py`、`tests/test_batch_b_fk_window.py`、`tests/test_batch_b_harness_safety.py` 等，不連線 MySQL。
- **實機（需授權）**：Supervisor 執行一次，產出 23 項結果與 Journal。
- 目前狀態：全部 Unverified。

### 2.8 Owner Authorization Gates

- **BB-G1**：實機執行前的當次授權，指定資料庫、Session UUID、FK DDL 與測試寫入範圍。
- **BB-G2**：FK DDL 的授權只限本 Session。
- **BB-G3**：測試寫入只限第 2.10 節的 23 項範圍。
- **BB-G4**：任一 BB-A* 失敗需要重試時，另行授權新 Session。

### 2.9 Dependencies / Pending

- **依賴**：REC-A* 通過；Preflight 通過；BB-G1～G3；TK-03 完成（硬性前置，見 2.12 X1 與 BB-I7）。
- **P-B1**：23 項的 pytest collection 與實際執行尚未驗證。靜態對照已完成（2.10），內容審查已完成（2.11）。

### 2.10 23 項案例靜態來源對照（2026-10-23，唯讀閱讀，未收集、未執行）

**數量對齊**：

| 測試函式（`tests/test_batch_b_mysql.py`） | 定義行 | 參數化 | 項數 |
| --- | --- | --- | --- |
| `test_real_batch_b_workflow` | 69 | 行 67–68，10 個 scenario | 10 |
| `test_real_batch_b_rollback` | 82 | 行 79–80，8 個 prefix | 8 |
| `test_real_batch_b_unknown_commit` | 93 | 行 92，4 個 mode | 4 |
| `test_real_batch_b_reminder` | 103 | 無 | 1 |
| **合計** | | | **23** |

- **「18 個 def」**：`grep -c 'def '` 的文字比對結果為 18。精確頂層 `def` 為 16（`scenarios.py` 行 23、39、57、73、96、115、142、170、191、210、223、237、262、289、307、324），另 2 個巢狀（行 81 的 `accept`、行 332 的 `submit`）。16＋2＝18 是文字計數，不是案例數。**數量已完全對齊**。
- **路由**：`dispatch`（行 307）的 `cases` 字典（行 314–315）只含 12 個名稱：10 個 workflow 加 `reminder_seed`、`reminder_read`。`rollback:` 前綴 → `rollback_case`（行 237）；`uncertain:` 前綴 → `uncertain_case`（行 262）。

**逐項對照**：

| # | pytest 案例 ID | 測試入口 | 參數來源 | 執行實作 |
| --- | --- | --- | --- | --- |
| 1 | `workflow[persistence]` | test_batch_b_mysql.py:69 | :67–68 | scenarios.py:23 |
| 2 | `workflow[priority]` | :69 | :67–68 | scenarios.py:39 |
| 3 | `workflow[rejected]` | :69 | :67–68 | scenarios.py:57 |
| 4 | `workflow[concurrent_accept]` | :69 | :67–68 | scenarios.py:73（巢狀 accept 行 81） |
| 5 | `workflow[concurrent_reward]` | :69 | :67–68 | scenarios.py:96 |
| 6 | `workflow[concurrent_retry]` | :69 | :67–68 | scenarios.py:324（巢狀 submit 行 332） |
| 7 | `workflow[lock_timeout]` | :69 | :67–68 | scenarios.py:115 |
| 8 | `workflow[owner_review]` | :69 | :67–68 | scenarios.py:142 |
| 9 | `workflow[conflict]` | :69 | :67–68 | scenarios.py:170 |
| 10 | `workflow[remix_merge]` | :69 | :67–68 | scenarios.py:191 |
| 11 | `rollback[INSERT INTO jb2_events]` | test_batch_b_mysql.py:82 | :79–80 | scenarios.py:237（dispatch 行 316–317） |
| 12 | `rollback[INSERT INTO jb2_receipts]` | :82 | :79–80 | :237 |
| 13 | `rollback[INSERT INTO jb2_evidence]` | :82 | :79–80 | :237 |
| 14 | `rollback[INSERT INTO credit_ledger]` | :82 | :79–80 | :237 |
| 15 | `rollback[INSERT INTO jb2_claims]` | :82 | :79–80 | :237 |
| 16 | `rollback[UPDATE users SET credit_balance]` | :82 | :79–80 | :237 |
| 17 | `rollback[UPDATE jb2_events SET reward_status]` | :82 | :79–80 | :237 |
| 18 | `rollback[INSERT INTO jb2_settlements]` | :82 | :79–80 | :237 |
| 19 | `unknown[accept:before]` | test_batch_b_mysql.py:93 | :92 | scenarios.py:262（dispatch 行 318–319） |
| 20 | `unknown[accept:after]` | :93 | :92 | :262 |
| 21 | `unknown[settle:before]` | :93 | :92 | :262 |
| 22 | `unknown[settle:after]` | :93 | :92 | :262 |
| 23 | `reminder` | test_batch_b_mysql.py:103 | 無 | `reminder_seed`（scenarios.py:210）、`make_reminders_due`（batch_b_harness.py:343）、`reminder_read`（scenarios.py:223） |

**靜態驗證的額外事實**（2026-10-23 唯讀核對）：

- 八個 rollback 前綴在 `bot/database` 中各有一條實際 SQL，且 SQL 字串以該前綴開頭（`submission_store.py:141、144、163`；`reward_settlement.py:76、79、81、89、114`），故 `FaultCursor` 的 `startswith` 比對會命中。
- `tests/batch_b_harness_contract.py:52–53` 的 subjects：索引 1 與 2 同為 `:v1` 版本，索引 3 為 `:v2`。因此 `concurrent_reward`、`conflict`、`rejected` 使用 1、2 的同版本前提成立；`remix_merge` 使用 1、3 的不同版本前提成立。
- `skip` 條件：`test_batch_b_mysql.py:13–14` 以 `RUN_SWITCH` 與 `HARNESS_SWITCH` 控制；兩常數定義於 `tests/batch_b_harness_contract.py`。

### 2.11 23 項內容靜態審查（2026-10-23，逐項閱讀斷言與 helper，未執行）

**判定標準**：「支持」＝斷言能支持宣稱的需求；「部分」＝斷言只覆蓋宣稱的一部分；「流程」＝只檢查流程或計數，未檢查結果內容。`assert_accounting` 為 `tests/batch_b_mysql_cases.py:82`，`validate_payloads` 為 `:98`。

| # | 案例 | 實際斷言（摘要） | 判定 | 覆蓋缺口 |
| --- | --- | --- | --- | --- |
| 1 | persistence | 同 operation 重送：`create_share_event` 為 False、candidate 相同；events＝receipts＝1；`assert_accounting(…,0)` | 支持 | 重送以同一 operation_id 進行，不涵蓋不同 Interaction ID；`?t=30&list=test` 只間接證明 canonical 化。 |
| 2 | priority | accepted_sequence 先後；WAITING_PREDECESSOR → AWARDED → INELIGIBLE；awards＝1 | 支持 | 無 |
| 3 | rejected | op1 拒絕後 op2 AWARDED；awards＝1 | 支持 | 未斷言 op1 最終狀態與 review_history 內容。 |
| 4 | concurrent_accept | 兩個並行結果的 accepted_sequence 相鄰（`sequences[1]==sequences[0]+1`）；events＝2 | **流程** | 未呼叫 `assert_accounting`，未檢查 receipts 1:1、未呼叫 `validate_payloads`。只證明序號不重複。 |
| 5 | concurrent_reward | op1 AWARDED、op2 為 WAITING 或 INELIGIBLE；awards＝1 | 支持 | 結果依 predecessor 規則確定，斷言不依賴執行順序。 |
| 6 | concurrent_retry | 兩個並行技術重試中恰一個 `create_share_event=True`；events＝receipts＝1；awards＝0 | 支持 | 本案最能證明「同 key 不多建事件」。 |
| 7 | lock_timeout | 1205 錯誤；等待時間在 1.5～8 秒；`jb2_events` 為空 | **部分** | 未檢查 receipts、evidence、videos 等其他表未殘留；未檢查 Control 未被改寫；未呼叫 `assert_accounting`。時間區間為環境敏感斷言（X4）。 |
| 8 | owner_review | 重複 approve 結果相等；`StaleReviewVersion`；非 Owner 呼叫 `pending_reminders` 拋 `PermissionError`；計數 `>=` | **部分** | (a) 非 Owner 的 `review`（`submission_repository.py:113`）、`merge`（:127）、`exclude`（:140）拒絕**未測**，只測了 `pending_reminders`（:150）。(b) 計數使用 `>=`，未斷言 revision 內容。(c) 未呼叫 `assert_accounting`／`validate_payloads`，jb2_review_history 與 jb2_evidence 的內容一致性不被檢查。 |
| 9 | conflict | settle(op2)＝CONFLICT；op1 仍 AWARDED；存在 `conflict_frozen`；awards＝1 | 支持 | 衝突證據為合成 `VersionExclusion`，不驗證外部證據來源。 |
| 10 | remix_merge | 版本 1、3 各自 AWARDED；merge 前後 `credit_ledger` 不變；`jb2_exceptions`＝1；awards＝2 | 支持 | `merge` 回傳值未斷言；exceptions 的內容只驗數量。 |
| 11 | rollback[jb2_events] | 注入前後全部 23 表相等（含 control revision）；重試後 awards＝0 | 支持 | 單點注入、只命中第一條匹配語句。 |
| 12 | rollback[jb2_receipts] | 同上 | 支持 | 同上 |
| 13 | rollback[jb2_evidence] | 同上 | 支持 | 同上 |
| 14 | rollback[credit_ledger] | 注入前後全表相等；重試後 awards＝1 | 支持 | 同上 |
| 15 | rollback[jb2_claims] | 同上 | 支持 | 同上 |
| 16 | rollback[users balance] | 同上 | 支持 | 同上 |
| 17 | rollback[jb2_events reward_status] | 同上 | 支持 | 同上 |
| 18 | rollback[jb2_settlements] | 同上 | 支持 | 同上 |
| 19 | unknown[accept:before] | 注入未觸發則失敗；重試兩次；events＝1；awards＝0 | 支持 | `before` 注入在真實 commit 前丟出，不涵蓋「server 已提交、ACK 遺失」。bot 端是否把注入包成 `CommitOutcomeUnknown`，本次靜態未核對。 |
| 20 | unknown[accept:after] | 真實 commit 後才丟出；重試兩次；events＝1；awards＝0 | 支持 | 同上 |
| 21 | unknown[settle:before] | 同上，settlement 路徑；awards＝1 | 支持 | 同上 |
| 22 | unknown[settle:after] | 同上 | 支持 | 同上 |
| 23 | reminder | seed 的 deadline 差為 24 小時；due 前 `pending_reminders` 為空；read 兩次結果相同；batches＝1；sequence 不變 | 支持 | 測試不含 Discord，不驗證「不發送 DM」；batch 內容只驗數量與 tuple。`make_reminders_due` 只更新 `next_due_at`，且 rowcount 必須為 1（`batch_b_harness.py:361–368`）。 |

**小結**：23 項中 20 項判定為「支持」（1、2、3、5、6、9、10，以及 rollback 8 項、unknown 4 項、reminder 1 項）；2 項為「部分」（7 lock_timeout、8 owner_review）；1 項為「流程」（4 concurrent_accept）。其中 rollback、unknown、reminder 共 13 項的「支持」結論附有限制（見表格與 X6）。**所有判定僅為靜態閱讀，未執行。**

### 2.12 跨案例缺口（X1～X7）

- **X1（已確認）`-O` 無防護**：全專案沒有 `__debug__`、`sys.flags`、`PYTHONOPTIMIZE` 或 `-O` 的檢查（以 grep 確認）。scenario 與 cases 的所有判定都依賴 `assert`。若以 `python -O` 啟動，所有斷言被移除，worker 仍會回報 PASS。**影響最大**，需在 TK-03 處理。spawn 子程序是否繼承 `-O` 旗標，本次未實測，但不影響缺口成立，因為專案沒有防護。
- **X2（已確認）驗證器未全面套用**：`assert_accounting`（內含 `validate_payloads`）未出現在 concurrent_accept、lock_timeout、owner_review、reminder_seed、reminder_read。這些案例不檢查 receipt、review history、evidence 的內容一致性。
- **X3（已確認）Owner 授權負向測試不完整**：只有 `pending_reminders` 被測試拒絕非 Owner（案例 8）。
- **X4（已確認）時間敏感斷言**：lock_timeout 的 1.5～8 秒區間、`Barrier(timeout=5)`、`future.result(timeout=12)`、process `join(30)`。這些數值在不同機器上可能造成不穩定，本次未實測。文件所稱的「2 秒」InnoDB wait 設定，本次未在程式中核對。
- **X5（已確認）合成證據**：`request()`（`cases.py:19–30`）使用 `synthetic-test-only` 的 DurationEvidence 與合成 video ID。案例證明的是 repository 的交易與冪等邏輯，**不證明真實 YouTube 資料下的資格判定**。
- **X6（已確認）客戶端注入的範圍**：注入為一次性、prefix 比對、只命中第一條匹配語句。不涵蓋網路斷線、MySQL crash、多點同時失敗（`docs/batch-b-real-mysql-tests.md:149` 自我聲明）。
- **X7（已確認）PASS 的定義**：worker 只要沒有拋出例外即 PASS（`scenario_process`，`scenarios.py:289–304`）。因此 X1 的 `-O` 問題會直接轉為 PASS。

### 2.13 靜態審查的限制

- 本審查只閱讀原始碼與文件，未執行 pytest、未收集、未匯入專案模組、未連線資料庫。
- 「支持」是指斷言的邏輯能支持宣稱，不代表斷言在實機上會通過。
- 所有 23 項的實際狀態：**全部 Unverified**。

---

## 3. Ledger Integrity Audit（唯讀對帳）

### 3.1 Scope / Out of Scope

- **Scope**：定義對 `users.credit_balance` 與 `credit_ledger` 的唯讀對帳規則、孤立 Ledger／Claim 的檢查方式，以及不一致時的 Blocked 行為。
- **Out of Scope**：本輪不執行任何查詢；不補寫 opening ledger；不歸零；不自動修復（Q15）。

### 3.2 Preconditions

- Recovery 唯讀複驗通過（REC-A*）。
- **目標為 Beta DB**（Q9）。Beta DB 名稱待 Owner 提供（P-L3）。
- Owner 授權（LED-G1）。
- 單一 snapshot 的要求受 PD-1 限制。

### 3.3 Schema 事實（來源：`bot/database/schema.sql`、`bot/database/migrations/002_submission_economy.sql`）

- `users.credit_balance BIGINT NOT NULL DEFAULT 0`，`CHECK (credit_balance >= 0)`（`schema.sql` 行 11、14）。
- `credit_ledger`：`credit_delta BIGINT NOT NULL`；`reason ENUM('submission_award','exchange_debit','delivery_refund')`；`discord_user_id` 參照 `users`（行 72–84）。
- `jb2_claims`：主鍵 `version_id`；`ledger_id` UNIQUE；`ledger_id` 參照 `credit_ledger`（`002` 行 64、69）；`discord_user_id` 參照 `users`（行 70）。**沒有 `claim_id` 欄位**（`002` 行 61–66）。
- **reason 枚舉沒有 opening balance 類型**，舊資料的餘額若無 ledger 無法在不修改 Schema 的前提下合法補記。

### 3.4 Functional Requirements（對帳規則）

| ID | 規則 |
| --- | --- |
| LED-F1 | 每位使用者：`users.credit_balance` 等於 `SUM(credit_ledger.credit_delta)`（無 ledger 視為 0）。 |
| LED-F2 | 每筆 `reason='submission_award'` 的 ledger，恰有一筆對應 `jb2_claims`（以 `ledger_id`）。 |
| LED-F3 | 每筆 `jb2_claims` 的 `discord_user_id` 等於其 ledger 的 `discord_user_id`。 |
| LED-F4 | 無孤立 ledger（無對應 user）或孤立 claim（無對應 ledger）。FK 已防止，仍需唯讀確認。 |
| LED-F5 | `users.credit_balance` 不為負值（CHECK 已防止，仍需唯讀確認）。 |
| LED-F6 | 不假設初始餘額為零。無法被 LED-F1 解釋的餘額視為不一致。 |

### 3.5 Safety Invariants

- **LED-I1**：只能是 SELECT（同 PD-1）。
- **LED-I2**：不得修改任何 `users`、`credit_ledger`、`jb2_claims` 紀錄。
- **LED-I3**：不得為使對帳通過而新增 opening ledger 或修改 reason 枚舉（Q15）。
- **LED-I4**：`LedgerBalanceMismatch`（`bot/database/reward_settlement.py:17`）只表示需要稽核，不作為修復依據。

### 3.6 唯讀對帳 SQL（草案，本輪不執行）

```sql
-- LED-F1：每位使用者餘額與 ledger 加總比對
SELECT u.discord_user_id,
       u.credit_balance,
       COALESCE(SUM(l.credit_delta), 0) AS ledger_total
FROM users AS u
LEFT JOIN credit_ledger AS l ON l.discord_user_id = u.discord_user_id
GROUP BY u.discord_user_id, u.credit_balance
HAVING u.credit_balance <> COALESCE(SUM(l.credit_delta), 0);

-- LED-F2：submission_award ledger 缺少 claim
SELECT l.ledger_id, l.discord_user_id
FROM credit_ledger AS l
LEFT JOIN jb2_claims AS c ON c.ledger_id = l.ledger_id
WHERE l.reason = 'submission_award' AND c.ledger_id IS NULL;

-- LED-F3：claim 與 ledger 的使用者不一致
SELECT c.ledger_id, c.discord_user_id, l.discord_user_id AS ledger_user_id
FROM jb2_claims AS c
JOIN credit_ledger AS l ON l.ledger_id = c.ledger_id
WHERE c.discord_user_id <> l.discord_user_id;

-- LED-F4：孤立 ledger（無對應 user）
SELECT l.ledger_id, l.discord_user_id
FROM credit_ledger AS l
LEFT JOIN users AS u ON u.discord_user_id = l.discord_user_id
WHERE u.discord_user_id IS NULL;

-- LED-F5：負餘額
SELECT discord_user_id, credit_balance
FROM users
WHERE credit_balance < 0;
```

- 草案的欄位已依 `002` 核對（`jb2_claims` 只用 `ledger_id` 與 `discord_user_id`）。實際執行前仍需 P-L1 的正式核對。
- 實作時須使用參數化語句，不得字串插值。

### 3.7 Acceptance Criteria

- **LED-A1**：LED-F1 至 LED-F5 皆為零列。
- **LED-A2**：全部查詢在同一 snapshot 中完成。**受 PD-1 限制**：若採方案 A，必須在結論中揭露「未保證跨查詢 snapshot」，不得宣稱已保證。
- **LED-A3**：只輸出計數與遮蔽的使用者識別，不輸出完整餘額明細，除非 Owner 另行要求。
- 目前狀態：**全部 Unverified**。

### 3.8 Blocked Behavior（Q15）

- **LED-B1**：任一條件不通過即 Blocked：立即停止相關驗收；Private Beta 不得開放任何資料庫寫入；建立獨立 Ledger Integrity Audit 紀錄，提交差異與遮蔽證據。
- **LED-B2**：保留原始 Ledger、餘額與歷史資料，不修改。
- **LED-B3**：不自動補寫 opening ledger、不歸零、不自動修復或補償。
- **LED-B4**：Owner 裁決前，Blocked 不解除。

### 3.9 Test Plan

- **離線（未來實作後）**：純函式測試，驗證 LED-F1 至 LED-F5 的判定，使用合成資料。
- **實機（需授權）**：唯讀查詢一次，記錄 LED-A1 至 LED-A3。
- 目前狀態：全部 Unverified。

### 3.10 Owner Authorization Gates

- **LED-G1**：當次授權，須指定 Beta DB 名稱、唯讀帳號與 PD-1 的收尾方式。
- **LED-G2**：任何修復、補寫或歸零須另行授權，且不屬於本規格範圍。

### 3.11 Dependencies / Pending

- **依賴**：REC-A*；P-L3；PD-1。
- **P-L1**：正式執行前，依 `002` 逐欄核對 SQL。
- **P-L3（Owner 提供）**：Beta DB 名稱，並核對其 Schema 與權限。不得是 `jukebox101_test`。
- **P-L2**：Q14 的 `Asia/Taipei` 與本對帳無關；日期分帳未來需以該時區為準。

---

## 4. Discord Private Beta

### 4.1 Scope / Out of Scope

- **Scope**：`/share`（YouTube-only）、`/history`、`/balance`；兩帳號隔離測試；Discord ID 程序環境注入；Beta DB 寫入啟用條件與 Gateway 授權邊界。
- **Out of Scope**：Spotify（Q4）、`/exchange`、`/daily`（Q3、Q14）、正式資料庫、自動派送。

### 4.2 Preconditions（啟用條件，全部必須成立）

- **BETA-G-1**：Recovery 複驗通過（REC-A*）。
- **BETA-G-2**：Batch B 真實驗收通過（BB-A1～A6；且 X1 已處理，見 2.12）（Q2）。
- **BETA-G-3**：Ledger 唯讀對帳通過（LED-A1～A3）（Q3、Q9）。
- **BETA-G-4**：Owner 當次授權，指定 Beta DB 與 Discord 目標。
- **BETA-G-5**：Beta DB 的 Schema 與權限核對完成（P-L3）。

### 4.3 Functional Requirements

| ID | 要求 |
| --- | --- |
| BETA-SCOPE-1 | `/history` 範圍限制：只顯示本人投稿與目前審核狀態，不是派送紀錄（CONF-1 已裁決）。依 `docs/private-beta-mvp-specification.md` 第 5 行的核准紀錄。`AGENTS.md` 長期規範（現位於 `CLAUDE.md` 第 9 節 §2）不變。 |
| BETA-F1 | `/share`：只接受 YouTube 網址，Spotify 直接拒絕（Q4）。 |
| BETA-F2 | `/share`：寫入 Batch B Pending；只在 Commit ACK 後回覆；不發點數，不加入交換池。 |
| BETA-F3 | `/history`：只顯示本人的投稿與審核狀態（`WHERE discord_user_id=%s`）。 |
| BETA-F4 | `/balance`：只讀 `users.credit_balance`，顯示本人餘額。 |
| BETA-F5 | `/exchange`、`/daily`：回覆「未開放」，不扣點、不建立推薦、不發 DM。 |
| BETA-F6 | 回應使用 ephemeral，`AllowedMentions.none`。 |
| BETA-DB-1 | **待 PD-4 裁決，不是已批准需求。** 內容見 4.4。 |

### 4.4 BETA-DB-1 與 PD-4：Control 三開關（Blocked，本文件不核准）

**狀態**：**Blocked**。本文件不核准任何 Control 變更。`settlement_enabled` 維持 0。

**既有程式的分工**（2026-10-23 唯讀核對）：

| 開關 | 程式中的用途 | 證據 |
| --- | --- | --- |
| `snapshot_ready` | 與 `legacy_writers_quiesced` 共同決定「快照已認證」 | `bot/database/persisted_snapshot.py:61` |
| `legacy_writers_quiesced` | 同上；並為 `accept_pending` 的前提 | `persisted_snapshot.py:61`；`submission_repository.py:64` |
| `settlement_enabled` | 結算發幣的前提；`accept_pending` 要求它為 0 | `bot/database/reward_settlement.py:57`；`submission_repository.py:64` |

**程式條件的限制**：`submission_repository.py:64` 要求 `(snapshot_ready, legacy_writers_quiesced, settlement_enabled) = (1, 1, 0)` 才受理 Pending。這只表示程式需要的狀態，**不足以證明可以把旗標改為 1**。旗標代表的是外部事實（歷史快照已覆蓋、舊寫入者已停止），程式無法自行判定。

**`snapshot_ready = 1` 所需證據**：

| 證據 | 內容 | 提供者 | 綁定 |
| --- | --- | --- | --- |
| E-S1 | Beta DB 在時間點 T0 的歷史資料快照：各表列數與內容雜湊（只讀產生） | DBA | DB identity、T0 |
| E-S2 | 同一 T0 的 Ledger 對帳結果為 LED-A1～A3 全部通過（TK-09） | DBA 執行，Owner 審閱 | 同一 DB、同一 T0 |
| E-S3 | 涵蓋聲明：快照覆蓋全部既有投稿、claims、ledger 紀錄 | Owner 簽核 | 引用 E-S1、E-S2 |

**`legacy_writers_quiesced = 1` 所需證據**：

| 證據 | 內容 | 提供者 | 綁定 |
| --- | --- | --- | --- |
| E-L1 | 寫入者清單：所有可能寫入 Beta DB 的應用程式、排程、腳本、DBA 作業，逐項附停止證據（設定停用、排程停用、程序終止） | 操作者列出，DBA 核對 | 時間點 T1 |
| E-L2 | T1 之後的 processlist 或會話快照，證明 Beta DB 無進行中的寫入語句 | DBA | 同一 DB、T1 |
| E-L3 | T1 至 T3 之間無新寫入者出現的會話觀察證據 | DBA | T1 至 T3 |
| E-L4 | 涵蓋聲明：寫入者清單完整 | Owner 簽核 | 引用 E-L1 至 E-L3 |

**時序與綁定**：
1. T1：凍結並停止所有寫入者（E-L1、E-L2）。
2. T0 > T1：產生歷史快照（E-S1）。
3. T2 > T0：完成 Ledger 對帳（E-S2）。
4. T3 > T2：單筆 Control 變更（只在 T1 至 T3 之間無寫入者，E-L3）。
- 任何時間點出現新寫入者 → 所有證據失效，從 T1 重新開始。
- 證據必須綁定同一 DB identity（`@@server_uuid`、schema 名稱、port），不可跨 DB 使用。

**其他欄位**：`settlement_enabled` 維持 0。`accepted_sequence` 與 `revision` 的前後值需以 E-S1 或同等紀錄證明不變；此屬性目前未被程式單獨核對。

**核准前不可執行**：本文件不核准 `snapshot_ready` 或 `legacy_writers_quiesced` 的變更。需由 Owner 依上列證據裁決（PD-4）。

### 4.5 Discord ID 與環境注入（Q12）

| ID | 要求 |
| --- | --- |
| BETA-E1 | `DISCORD_DEV_GUILD_ID`、`DISCORD_EXPECTED_APPLICATION_ID`、`DISCORD_OWNER_USER_ID` 只由程序環境提供，不寫入 `.env`。 |
| BETA-E2 | 缺少或格式錯誤在 login 前失敗（Fail Closed）。 |
| BETA-E3 | `bot/__main__.py:30` 呼叫 `load_database_settings()` 不帶 `dotenv_path`；`bot/acceptance_config.py:50` 的 `load_discord_settings()` 同樣不帶路徑。DB 與 token 必須由程序環境提供（GAP-B2）。 |

### 4.6 兩帳號隔離測試（Q12）

- **BETA-T1**：兩個不同的 Discord 測試帳號 A 與 B。
- **BETA-T2**：A 投稿後，A 的 `/history` 可見，B 的 `/history` 不可見。
- **BETA-T3**：B 的 `/balance` 不因 A 的操作而改變。
- **BETA-T4**：同一 Interaction ID 重送，回覆原回執，不建立第二個事件。
- **BETA-T5**：CommitOutcomeUnknown 時，不自動重送，不產生新 key，回報操作碼並停止。
- 目前狀態：**全部 Unverified**。依 `docs/private-beta-implementation.md` 的記載離線有 mock 測試；本輪未核對、未執行。

### 4.7 Acceptance Criteria

- **BETA-A1**：BETA-T1 至 BETA-T5 的實機結果全部符合。
- **BETA-A2**：`/exchange`、`/daily` 回覆符合 BETA-F5。
- **BETA-A3**：Discord 無非預期的 DM 或公開回覆。
- 目前狀態：全部 Unverified。

### 4.8 Failure / Rollback Behavior

- Gateway 啟動失敗、ID 不符、來自非授權 Guild 或 DM：拒絕並記錄，不寫入資料庫。
- Commit 未知：停止，保留操作碼，由 Owner 核對，不重送。
- 人工驗收失敗：停止 Beta 啟用，不自動修復資料。

### 4.9 Owner Authorization Gates

- **BETA-AUTH-1**：Gateway 啟動的當次授權（Q5、Q12）。
- **BETA-AUTH-2**：每次真人投稿測試的授權，指定測試帳號與範圍。
- **BETA-AUTH-3**：Beta DB 寫入與 BETA-DB-1 的當次授權（PD-4 裁決後才可申請）。
- **BETA-AUTH-4**：Push 與 Remote 設定另行授權（Q13）。

### 4.10 Test Plan

- **離線（已存在，未執行）**：CommandTree 分派、Guild／Application／DM 拒絕、ID 缺失 Fail Closed、Pending、回執重播、CommitOutcomeUnknown、`/history` SQL 限制（見 `docs/private-beta-implementation.md`）。
- **實機（需授權）**：BETA-T1 至 BETA-T5。
- 目前狀態：全部 Unverified。

---

## 5. Q1～Q16 Requirement Traceability Matrix

| Q | 決策摘要 | 對應 Requirement ID | 章節 |
| --- | --- | --- | --- |
| Q1 | 順序：Recovery → Batch B → 新功能 | §0.1、BB-G1、BETA-G-1、BETA-G-2 | 0、2、4 |
| Q2 | Batch B 通過前不開放 Beta 寫入 | BETA-G-2、BETA-DB-1（PD-4） | 4 |
| Q3 | Beta 只含 `/share`、`/history`、`/balance`；啟用前確認 Ledger | BETA-SCOPE-1、BETA-F1～F6、BETA-G-3 | 4、3 |
| Q4 | Beta 維持 YouTube-only，Spotify 延後 | BETA-F1 | 4 |
| Q5 | DB 寫入、FK DDL、Gateway 啟動須當次授權 | BB-G1～G3、BETA-AUTH-1～3、REC-G1、LED-G1 | 1、2、3、4 |
| Q6 | 已由 Q11 取代 | （無獨立 ID） | — |
| Q7 | Recovery 唯讀限 `jukebox101_test` 與唯讀帳號，只允許 SELECT | REC-S1、REC-S4、REC-A1、REC-G1、P-R1（已定）、PD-1 | 1 |
| Q8 | 複驗完成標準與 HEG | REC-A1～A8、HEG-1、HEG-2a、HEG-3、REC-A9（PD-2） | 1 |
| Q9 | Ledger 一致性定義（Beta 啟用前） | LED-F1～F5、LED-A1～A3、P-L3 | 3 |
| Q10 | Harness 有限度離線修正，不放寬 DB 限制 | BB-I1、BB-I2、BB-SUP2 | 2 |
| Q11 | 三組暫存憑證 Owner 保留 | SEC-1～SEC-5 | 6 |
| Q12 | Discord ID 程序環境注入；兩測試帳號；Gateway 當次授權 | BETA-E1～E3、BETA-T1～T5、BETA-AUTH-1 | 4 |
| Q13 | 本機 Commit 須當次同意；Push 另行授權 | GIT-1～GIT-3、BETA-AUTH-4 | 6 |
| Q14 | `/daily` 時區 Asia/Taipei，本次不實作 | TZ-1、P-L2 | 3、6 |
| Q15 | 不一致即 Blocked，不自動補寫或修復 | LED-B1～B4、LED-I3 | 3 |
| Q16 | DBA 監督的臨時 CASCADE FK window 為唯一路徑 | BB-SUP1～SUP3、BB-I4～I6、BB-G1～G4 | 2 |

---

## 6. 衝突、安全要求與其他章節

### 6.1 衝突與裁決狀態

| ID | 內容 | 狀態 |
| --- | --- | --- |
| CONF-1 | `/history` 語意：`AGENTS.md` §2 寫「近期派送紀錄」，Beta 採投稿與審核狀態 | **已裁決**（BETA-SCOPE-1）。文件修訂為 TK-04 |
| CONF-2 | Spotify：`AGENTS.md` §2 要求接受並 pending；Beta 延後 | **已裁決**（Q4）。記為範圍限制 |
| CONF-3 | `.env.example` 註解與 Q11 的文字 | **措辭已裁決**（區分範本與 Owner 本機保留）。實際修改為 TK-04，本輪未執行 |
| CONF-4 | 唯讀帳號來源 | **方案已裁決**（P-R1）。建立為 TK-05，本輪未執行 |
| CONF-5 | Q7 字面只允許 SELECT，與 PyMySQL `autocommit=False` 不一致 | **待裁決**（PD-1） |

### 6.2 安全要求

- **SEC-1**：`.env` 持續被 `.gitignore` 忽略。
- **SEC-2**：不讀取、輸出、記錄或複製 `.env` 中的密碼值。文件、輸出與 Journal 中不得出現密碼。
- **SEC-3**：`DB_TESTAPP_PASSWORD`、`DB_jukebox101_test_migrator_PASSWORD`、`DB_HARNESS_PASSWORD` 為 Owner 保留的暫存憑證（Q11）。存在不構成阻擋，也不授權使用。
- **SEC-4**：程式不得為使用這些欄位而新增引用。
- **SEC-5**：`.env.example` 只列實際欄位與假值，不包含真實測試憑證；本機 `.env` 的 Owner 保留是另一件事（CONF-3 已裁決）。
- **GIT-1**：每個本機 Commit 需當次同意（Q13）。
- **GIT-2**：Commit 前審查差異、測試結果與敏感資訊；不混入無關既有修改。
- **GIT-3**：本階段不 Push、不 Merge、不設定 Remote。
- **TZ-1**：`/daily` 時區為 `Asia/Taipei`（Q14），不依賴作業系統時區；本次不實作。

---

## 7. Gap Analysis（與現有實作）

| ID | 規格要求 | 現況（證據） | 缺口與建議 |
| --- | --- | --- | --- |
| GAP-R1 | REC-F1～F9：指向 `jukebox101_test` 的唯讀入口 | `tests/test_mysql_readonly.py` 第 76–79 行斷言目標為 `jukebox101_app@jukebox101` | 缺少唯讀入口。需新增獨立實作（TK-01） |
| GAP-R2 | REC-A2：fingerprint | `schema_fingerprints`（`tests/batch_b_harness_database.py:123` 起）屬寫入型 Harness 的一部分 | 可參考，Recovery 路徑需獨立實作 |
| GAP-R3 | HEG 分類 | 見 1.5 節 | 第三版已依 Manifest 重分類；HEG-2 拆為 HEG-2a 與 REC-A9（PD-2） |
| GAP-R4 | REC-F2、REC-S1：SELECT-only 與交易收尾 | `bot/database/connection.py:31` 為 `autocommit=False`；`tests/batch_b_recovery.py:254` 要求 `autocommit=1` | PD-1 |
| GAP-R5 | PD-3：metadata 權限 | 查詢來源與先例已記於 1.11 | 最小權限清單待 DBA 確認；未執行 GRANT |
| GAP-B1 | BB-SUP1～SUP3 | 已離線實作（`docs/stage-b-only-run.md`） | 實機未執行 |
| GAP-B2 | BETA-E3 | `bot/__main__.py:30`、`bot/acceptance_config.py:50` 不帶 `dotenv_path` | runbook 需記載程序環境注入 |
| GAP-B3 | BB-I1 | `tests/batch_b_fk_window.py:178` 的 `require_cleanup_cascade` | 刻意保護，只能經 Supervisor |
| GAP-B4 | 23 項數量 | 2.10 節：10＋8＋4＋1＝23；「18 個 def」為 16 頂層＋2 巢狀 | **數量已對齊**。collection 與執行仍 Unverified |
| GAP-B5 | 23 項斷言內容 | 2.11 節：20 項支持（其中 13 項附限制），2 項部分，1 項流程 | **內容覆蓋不完整**，見 X1～X7；實機前需依 TK-03 補強 |
| GAP-B6 | X1：`-O` 無防護 | 全專案無 `__debug__`、`sys.flags`、`PYTHONOPTIMIZE` 檢查 | TK-03 |
| GAP-B7 | X2、X3：驗證器與 Owner 負向測試不完整 | 案例 4、7、8、9、23 未套用 `assert_accounting`；案例 8 只測 `pending_reminders` | TK-03 |
| GAP-B8 | X4、X5：時間敏感與合成證據 | 2.12 節 | 實機前可由 Owner 決定是否接受 |
| GAP-L1 | LED-F1～F5 | 現行檢查為交易內 `LedgerBalanceMismatch`（`reward_settlement.py:101–104`） | 需獨立唯讀對帳（TK-02） |
| GAP-L2 | 舊資料餘額來源 | `submission_migration.py` 行 151 附近說明「不修改舊餘額」 | 依 Q15 停止並稽核 |
| GAP-P1 | BETA-F1～F6 | `bot/discord_app.py`、`bot/services.py` 已接線；`/exchange`、`/daily` 回覆未開放 | 符合 |
| GAP-P2 | BETA-T1～T5 | 離線 mock 依文件存在，實機未驗證 | TK-12 |
| GAP-P3 | TZ-1 | 只見於 `schema.sql` 行 4 的註解 | 符合 Q14；延後 |
| GAP-P4 | BETA-DB-1 | `docs/private-beta-implementation.md` 的步驟；`submission_repository.py:64` 為 (1,1,0) 前提 | PD-4 裁決後才能執行（TK-11） |
| GAP-T1 | 測試狀態 | 本輪未執行、未收集 | 全部 Unverified |

---

## 8. 批次順序（依 11.2 依賴圖，皆待授權）

1. **離線（可獨立，不需資料庫）**：TK-04、TK-01、TK-02、TK-03。
2. **Recovery 實機**：TK-05（DBA，另行授權）→ TK-06。
3. **Batch B 實機**：TK-07（需 TK-06 與 TK-03 完成）。
4. **Beta DB 與 Ledger 實機**：TK-08 → TK-09。
5. **Beta 啟用**：TK-10 → TK-11 → TK-12。

- TK-03 為 TK-07 的硬性前置，不是建議。
- TK-11 只能在 TK-07、TK-09、TK-10 之後，且 PD-4 與其證據已全部提供並核准。

---

## 9. 待決事項總表

每項標明阻擋原因的類別：**設計決策未定**、**執行授權未取得**、**前置證據未完成**。

| ID | 事項 | 類別 | 阻擋的 TK |
| --- | --- | --- | --- |
| PD-1 | 方案 B 語句清單，並擴大 Q7 的字面範圍（1.9） | 設計決策未定 | TK-01（收尾子項）、TK-06、TK-09 |
| PD-2 | 是否將 REC-A9 列為必要條件（1.10） | 設計決策未定 | TK-06 |
| PD-3 | 唯讀帳號的 DBA 權限方案與 REC-A10 核准（1.11） | 前置證據未完成（DBA 資料）、設計決策未定（Owner 核准） | TK-05、TK-06 |
| PD-4 | BETA-DB-1 Control 變更與 E-S／E-L 證據（4.4） | 前置證據未完成、設計決策未定 | TK-11 |
| P-L3 | Beta DB 名稱，不得是 `jukebox101_test`（3.11） | 設計決策未定 | TK-08、TK-09 |

**執行授權（不屬於 PD，每次實機前另行取得）**：REC-G1（Recovery）、BB-G1～G3（Batch B）、LED-G1（Ledger）、BETA-AUTH-1～3（Gateway、真人測試、Beta DB 寫入）、DBA 獨立授權（建立帳號）、GIT-1（每個本機 commit）。

**需要 DBA 提供的技術資料**（供 PD-3、PD-1、PD-4 使用）：
1. 目標 MySQL 的精確版本與 `@@server_uuid`。
2. 唯讀帳號最小權限方案（GRANT 清單，未執行）及各 metadata 來源的可見性條件。
3. `SET TRANSACTION ISOLATION LEVEL`、`START TRANSACTION WITH CONSISTENT SNAPSHOT` 與 `@@transaction_*` 變數名稱在該版本的行為確認。
4. `information_schema`（TABLES、KEY_COLUMN_USAGE、REFERENTIAL_CONSTRAINTS、TRIGGERS）與 `SHOW CREATE TABLE` 是否受同一 snapshot 約束。
5. 特權基準計數：基表 23、FK 邊 29，供 REC-A10 使用。
6. Beta DB 的 Schema 與權限清單（P-L3 指定後）。
7. PD-4 所需的寫入者清單與 processlist 快照方法（E-L1 至 E-L3）。

**其他未驗證事項**：

| 項目 | 狀態 |
| --- | --- |
| Recovery 獨立複驗 | Unverified（僅使用者回報的 `RECOVERY_SUCCESS`） |
| 既有 Harness Session | Blocked，不解除 |
| Batch B 23 項 | 數量已對齊；內容審查完成；實機 Unverified |
| X1 `-O` 防護 | 缺口已確認；要求已定義（BB-I7），待 TK-03 實作 |
| Ledger 對帳 | Unverified（草案 SQL） |
| Beta 兩帳號隔離 | Unverified |
| 工作樹 62 個 `M` | 本輪未逐檔重新確認 |

---

## 10. 審查結論

- 第四版已完成：PD-1 提出完整語句清單與 snapshot 建立方式（方案 B，待核准）；PD-2 定義 REC-A9 的查驗範圍與停止行為，HEG-2a 保留；PD-3 改由 DBA 提供方案與正向控制 REC-A10；PD-4 列出各開關的證據、提供者與時序，維持 Blocked；BB-I7 定義 `-O` 拒絕要求，並在 TK-03 落實。
- 所有行為皆為 Unverified。本文件不核准任何實機操作、Control 變更或 Git 寫入。

---

## 11. 工作拆解（12 張，可供授權審查，尚未授權任何實作）

**狀態統一為：可供授權審查，尚未授權實作。** 每張 ticket 的阻擋類別以三種區分：**設計決策未定**（需 Owner 決定）、**執行授權未取得**（需當次授權）、**前置證據未完成**（需先有資料或前置工作）。

### 11.1 Ticket 草稿

**TK-01 Recovery 離線核對模組與白名單**
- 阻擋類別：執行授權未取得（實作）；設計決策未定（僅收尾子項，PD-1）
- 目的：建立不連線即可測試的唯讀核對邏輯。
- Requirement ID：REC-F1、REC-F3～F8、REC-S1、REC-S4、REC-A1～A8；GAP-R1。
- 交付物：目標比對、語句白名單、結構化摘要、輸出遮蔽的純函式與離線測試；收尾函式只定義介面，不實作 PD-1 的細節。
- 依賴：無。
- 驗收條件：離線測試（實作授權後產生）涵蓋非 SELECT 拒絕、目標不符拒絕、輸出不含密碼與列值，且測試過程零連線。
- 授權範圍：實作授權；不連線。
- 停止條件：需要任何非 SELECT 語句或非核准清單內的語句。

**TK-02 Ledger 對帳離線判定與欄位核對**
- 阻擋類別：執行授權未取得（實作）
- 目的：把 LED-F1～F5 做成純函式，並核對草案 SQL 的欄位。
- Requirement ID：LED-F1～F5、LED-A1～A3、LED-B1；GAP-L1；P-L1。
- 交付物：判定函式、離線測試、欄位核對紀錄（只讀定義檔，不執行 SQL）。
- 依賴：無。
- 驗收條件：離線測試通過；欄位核對與 `002` 一致。
- 授權範圍：實作授權；不連線。
- 停止條件：SQL 引用不存在的欄位，且無法確定正確欄位。

**TK-03 Batch B 離線防護與斷言補強**
- 阻擋類別：執行授權未取得（實作）
- 目的：落實 BB-I7（-O 拒絕），補強驗證器與 Owner 負向測試。
- Requirement ID：BB-I7、BB-A1、BB-T1；X1～X3；GAP-B6、GAP-B7。
- 交付物：
  - (a) 所有可執行 Batch B 驗收的入口（直接入口、Supervisor 呼叫的 Python 入口、worker `scenario_process`、pytest 路徑）在任何資料庫連線或副作用前，檢查 `sys.flags.optimize == 0` 且 `__debug__` 為 True；否則拒絕。
  - (b) 拒絕路徑的離線驗收：以 spy 連線證明拒絕時沒有連線、沒有寫入、沒有成功結果；涵蓋直接入口與 Supervisor、worker 路徑。
  - (c) 案例 4、7、8、9、23 套用 `assert_accounting`／`validate_payloads`。
  - (d) 補非 Owner 的 `review`、`merge`、`exclude` 拒絕測試。
- 依賴：無。為 TK-07 的硬性前置。
- 驗收條件：上列離線測試（實作授權後產生）通過；拒絕路徑的 spy 證明零連線。
- 授權範圍：實作授權；不連線；不執行 Batch B。
- 停止條件：需要修改 Q10 禁止的資料庫限制。

**TK-04 文件與 `.env.example` 措辭**
- 阻擋類別：執行授權未取得（文件修改；commit 另需 GIT-1）。CONF-1、CONF-3 已裁決，無設計阻擋。
- 目的：把 `/history` 範圍寫入 Beta 文件；區分 `.env.example` 的範本與 Owner 保留。
- Requirement ID：BETA-SCOPE-1、SEC-5、CONF-1、CONF-3。
- 交付物：`.env.example` 註解修改；Beta 文件段落修訂。
- 依賴：無（可獨立進行）。
- 驗收條件：文字與 Q11、CONF-1 一致；不含任何密碼值；`.env` 未被讀取或修改。
- 授權範圍：文件修改授權；commit 依 GIT-1。
- 停止條件：措辭與 Q11 衝突。

**TK-05 DBA 建立專用唯讀帳號**
- 阻擋類別：前置證據未完成（DBA 提供版本、最小權限方案、特權基準計數）；設計決策未定（Owner 核准方案，PD-3）；執行授權未取得（DBA 獨立授權）
- 目的：提供 Recovery 唯讀複驗的帳號。
- Requirement ID：P-R1、REC-G1、PD-3、REC-A10。
- 交付物：DBA 方案文件（1.11 第 1 至 6 項）、Owner 核准紀錄、建立紀錄（待授權後）。
- 依賴：DBA 資料與 PD-3 核准。
- 驗收條件：權限符合核准方案；無可用角色；不是既有應用程式、Migrator、Harness 帳號；REC-A10 可執行。
- 授權範圍：DBA 獨立授權（CREATE USER、GRANT 屬於 DDL 性質）。
- 停止條件：權限超出核准方案；帳號名稱與既有帳號衝突。

**TK-06 Recovery 唯讀複驗（實機）**
- 阻擋類別：設計決策未定（PD-1 語句清單、PD-2 REC-A9）；前置證據未完成（TK-01、TK-05、REC-A10 基準）；執行授權未取得（REC-G1）
- 目的：執行 REC-A1～A8，以及核准後的 REC-A9、REC-A10。
- Requirement ID：REC-G1、REC-A1～A10、REC-S1、HEG-1、HEG-2a、HEG-3。
- 交付物：`RECOVERY_VERIFIED_READONLY` 或 `RECOVERY_NOT_VERIFIED`；各 REC-A 結果；HEG 實際狀態；snapshot 與 metadata 限制的揭露。
- 依賴：TK-01、TK-05。
- 驗收條件：REC-A1～A8 全部通過；核准後加入 A9、A10 亦須通過。
- 授權範圍：當次 REC-G1，須指定 Session、唯讀帳號與核准的語句清單。
- 停止條件：任一 REC 失敗、語句超出清單、目標不符、連線異常、收尾失敗。

**TK-07 Batch B 實機（Preflight 與 23 項）**
- 阻擋類別：前置證據未完成（TK-06 通過、TK-03 完成）；執行授權未取得（BB-G1～G3）
- 目的：執行 BB-P1～P3、BB-T1～T4、BB-A1～A6。
- Requirement ID：BB-P1～P3、BB-SUP1～SUP3、BB-T1～T4、BB-A1～A6、BB-I1～I7。
- 交付物：Preflight 結果、23 項結果、Journal、Control 還原紀錄。
- 依賴：TK-03、TK-06。
- 驗收條件：BB-A1～A6。
- 授權範圍：當次 BB-G1～G3（含 FK DDL 與測試寫入範圍）。
- 停止條件：第 2.6 節任一停止條件。

**TK-08 Beta DB 指定與唯讀核對**
- 阻擋類別：設計決策未定（P-L3，Owner 指定名稱）；執行授權未取得（唯讀核對）
- 目的：指定 Beta DB 並核對其 Schema 與權限。
- Requirement ID：P-L3、BETA-G-4、BETA-G-5。
- 交付物：Schema 與權限核對紀錄。
- 依賴：P-L3。
- 驗收條件：DB 名稱不等於 `jukebox101_test`；Schema 與權限符合核對清單。
- 授權範圍：Owner 指定名稱；唯讀核對需當次授權。
- 停止條件：名稱與測試庫相同。

**TK-09 Ledger 唯讀對帳（Beta DB）**
- 阻擋類別：設計決策未定（PD-1 語句清單，與 TK-06 相同）；前置證據未完成（TK-02、TK-08）；執行授權未取得（LED-G1）
- 目的：執行 LED-F1～F5。
- Requirement ID：LED-G1、LED-A1～A3、LED-B1～B4、Q9、Q15。
- 交付物：LED-A1～A3 結果；不一致時的 Ledger Integrity Audit 紀錄。
- 依賴：TK-02、TK-08；PD-1。
- 驗收條件：LED-A1～A3 通過；不一致即 Blocked。
- 授權範圍：當次 LED-G1。
- 停止條件：任何不一致（不補寫、不修復）。

**TK-10 Beta 啟用條件核對（文件，不寫入）**
- 阻擋類別：前置證據未完成（TK-06、TK-07、TK-09）
- 目的：逐項核對 BETA-G-1～G-5 並留下書面紀錄。
- Requirement ID：BETA-G-1～G-5、Q2、Q3、Q9。
- 交付物：啟用條件核對表。
- 依賴：TK-06、TK-07、TK-09。
- 驗收條件：全部條件成立的書面紀錄。
- 授權範圍：文件核對，無資料庫寫入；文件 commit 依 GIT-1。
- 停止條件：任一條件不成立。

**TK-11 Beta Control 三開關變更（BETA-DB-1）**
- 阻擋類別：設計決策未定（PD-4 核准）；前置證據未完成（E-S1～E-S3、E-L1～E-L4，TK-07、TK-09、TK-10）；執行授權未取得（BETA-AUTH-3）
- 目的：在 Beta DB 完成單筆 Control 變更，使 `/share` 能寫入 Pending。
- Requirement ID：BETA-DB-1、BETA-AUTH-3、PD-4；`submission_repository.py:64`。
- 交付物：變更前後 Control 紀錄；E-S 與 E-L 證據；時序與 DB identity 綁定紀錄。
- 依賴：TK-07、TK-09、TK-10；PD-4 核准。
- 驗收條件：`(snapshot_ready, legacy_writers_quiesced, settlement_enabled) = (1, 1, 0)`；`accepted_sequence`、`revision` 前後相同。
- 授權範圍：當次 BETA-AUTH-3，且 PD-4 已核准。
- 停止條件：證據失效（任何寫入者出現）；讀取值與預期不同。

**TK-12 Gateway 啟動與兩帳號人工驗收**
- 阻擋類別：前置證據未完成（TK-10、TK-11）；執行授權未取得（BETA-AUTH-1、BETA-AUTH-2）
- 目的：執行 BETA-T1～T5 與 BETA-A1～A3。
- Requirement ID：BETA-AUTH-1、BETA-AUTH-2、BETA-E1～E3、BETA-T1～T5、BETA-A1～A3、Q12。
- 交付物：人工驗收紀錄，分開標示 Passed、Failed、Manual Pending。
- 依賴：TK-10、TK-11；Q12 的程序環境。
- 驗收條件：BETA-A1～A3。
- 授權範圍：當次 BETA-AUTH-1、BETA-AUTH-2。
- 停止條件：BETA-T5 的 Commit 未知，停止、保留操作碼、由 Owner 核對，不重送。

### 11.2 依賴檢查

**依賴邊**：
- TK-01 → TK-06
- TK-05 → TK-06
- TK-03 → TK-07
- TK-06 → TK-07
- TK-02 → TK-09
- TK-08 → TK-09
- TK-06、TK-07、TK-09 → TK-10
- TK-07、TK-09、TK-10 → TK-11
- TK-10、TK-11 → TK-12
- TK-04 無依賴

**檢查結果**：
- 編號：TK-01 至 TK-12 各出現一次，無漏號、無重複。
- 循環：無。拓撲順序為 TK-04、TK-01、TK-02、TK-03、TK-05、TK-08、TK-06、TK-07、TK-09、TK-10、TK-11、TK-12。
- 所有 PD 的阻擋歸屬已列於第 9 節。

### 11.3 Owner 需決定的事項

1. PD-1：是否核准 1.9 的語句清單（方案 B），並接受擴大 Q7 的字面範圍；或改採方案 A。
2. PD-2：是否將 REC-A9 列為必要條件。
3. PD-3：是否核准 DBA 提出的權限方案與 REC-A10。
4. PD-4：BETA-DB-1 的 Control 變更，需待 E-S、E-L 證據齊備後再裁決。
5. P-L3：指定 Beta DB 名稱。
6. 每次實機與每個 commit 的當次授權。

### 11.4 DBA 需提供的技術資料

見第 9 節的「需要 DBA 提供的技術資料」清單（共 7 項）。
