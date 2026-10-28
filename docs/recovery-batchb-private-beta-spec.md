# Recovery、Batch B、Ledger Integrity 與 Private Beta 統一規格

狀態：**DRAFT_FOR_OWNER_REVIEW**（2026-10-23，第五版：第四版內容保留；第五版同步 P 節裁決狀態、補登 D-7 紀錄、新增 §15.2A 候選綁定摘要，並修正已過期的狀態）。本文件只定義規格、靜態審查與工作草稿。所有行為皆為 `Unverified`；本文件沒有任何測試或實機結果被標記為 `Passed`。

產品規格草案見**第 16 節**（完整產品流程、版本分層、需求裁決來源、TK-14 至 TK-24 候選與產品阻擋編號）。第 1 至 15 節為既有技術規格與證據，保留不覆寫。

## 交接摘要（2026-10-23，新 session 請先讀）

**目前目標**：收斂 TK-05（唯讀帳號）操作方案與 D-5（baseline）審核。本 session 到此收尾，TK-05 未執行。

**下一步（依序，不跳到 TK-14）**：
1. 讀取 CLAUDE.md、CODING_STANDARD.md、本 Spec（特別是第 15.1、15.2、15.7 與 P 節）與本摘要。
2. 唯讀核對現況：`git status`、`git log`、本摘要所列的檔案狀態。不連線、不執行 SQL。
3. 收斂 TK-05 的管理身分與操作方案（見 15.1 的待修正細節），再由 Owner 決定是否授權 D-8。
4. 完整候選 baseline 的審核（見 P 節），由 Owner 決定 D-5。

**任務進度**：
- TK-01 至 TK-04：離線完成。
- TK-05 至 TK-12：尚待各自的前置條件與授權，未開始。
- TK-13（長期部署）：雲端方向已記錄於第 14 節，尚未設計。
- TK-14 至 TK-24：完整產品的候選工作，已列於第 16.4 節，未核准。

**已核准的設計與適用範圍**（D-1 至 D-4、D-6）：
- D-1：方案 B 的設計，適用於 TK-06。只保證適用的 InnoDB 資料查詢使用一致 snapshot；**不宣稱 metadata 也受同一 snapshot 保護**。實際連線與執行另待授權。
- D-2：REC-A9 列為必要條件。
- D-3：23 張表的表層級 SELECT 方案，以及 REC-A10 的 23 基表、26 約束、29 欄位位置分開核對。計數是正向控制，不單憑數量相同就宣稱 metadata 完整正確。
- D-4：NO ACTION 與 RESTRICT 的語意等價政策，僅適用已確認的 InnoDB 範圍；保留原始字串並揭露差異。
- D-6：採用 `jukebox101_test_readonly`@`127.0.0.1`；若已存在則停止，不覆寫。

**未核准**：
- D-5（baseline 核准）：未核准。
- D-8（TK-05 建立帳號）：未核准。
- D-9（TK-06 Recovery 實機）：未授權。

**D-7 的性質**：Owner 手動執行並提供的觀測證據（§15.7 O 節）。**不是** Recovery 通過，也**不是**已核准的 baseline。

**平台政策**：仍未決（PB-POL）。正式發點停用，但不阻擋投稿查詢 Beta。

**工作樹狀態**（2026-10-23 唯讀盤點，見下節）：未提交，尚未備份到遠端。

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
| REC-F4 | 核對 FK metadata：欄位、參照表、參照欄位與 `ON DELETE`／`ON UPDATE`，必須符合**已審核的預期規則清單**。來源為 `KEY_COLUMN_USAGE` 與 `REFERENTIAL_CONSTRAINTS` 的**觀測值**，觀測值不得同時作為預期值（自我證明）。**來源缺口（2026-10-23 靜態追溯）**：`schema.sql` 與 `002` 遷移檔沒有任何 FK 明列 `ON DELETE`／`ON UPDATE`；`docs/batch-b-recovery-fk-plan.md` 只對兩條特定 FK 記載已核准的子句；`docs/batch-b-recovery-manifest.json` 的 `reviewed_fk_column_edges` 只有六欄，不含規則。因此完整的 29 條預期規則**目前沒有已審核來源**，缺少時驗證 fail-closed 為 NOT_VERIFIED（`rule_source_missing`），不得自行補造。**Owner 裁決（2026-10-23）**：取消「內容相同即拒絕」。內容相同是比對通過的必要條件，不能用來判定來源不獨立。改為：預期規則須來自可追溯、經 Owner 核准的 baseline，涵蓋完整 29 條 FK 定義與 ON UPDATE／ON DELETE 動作；觀測結果不得自行充當 baseline（換檔名、複製物件或標示 approved 皆不足以證明獨立審核）；來源獨立且已核准時，即使內容完全相同也應接受；缺少完整 baseline、核准證據或來源無法確認時維持 NOT_VERIFIED 並明確回報原因。baseline 完整不代表整體 VERIFIED，仍須所有必要驗收條件通過。候選清單見第 12 節，**未核准**。 |
| REC-F5 | 讀取 `jb2_control`（singleton_id = 1）並比對**恢復後**的預期值：`accepted_sequence = 0`、`revision = 0`、`snapshot_ready = 0`、`legacy_writers_quiesced = 0`、`settlement_enabled = 0`。**區分**：Manifest 的 `control_restore` 為恢復後狀態（與本項一致）；`control_precondition`（accepted_sequence 1、revision 2）為歷史恢復**前**狀態，**不得**作為恢復後的驗收值。 |
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
| REC-A9 | 15 個已刪 PK 的反向參照列為零（定義見 1.10） | **必要**（PD-2，D-2 已裁決，2026-10-23；實機未驗證） |
| REC-A10 | 正向控制：唯讀帳號可見的基表數為 23、FK 邊數為 29，且與 DBA 特權基準計數一致（定義見 1.11） | **必要**（PD-3，D-3 已裁決，2026-10-23；實機未驗證） |
| REC-A8 | 結果只能是 `RECOVERY_VERIFIED_READONLY` 或 `RECOVERY_NOT_VERIFIED` | 必要 |

**Manifest 的 `unresolved` 五項分類**（依 2026-10-23 的 Manifest 內容重新分類）：

| Manifest 項目 | 分類 | 依據 |
| --- | --- | --- |
| `historical_failing_delete_target_not_recorded` | HEG-1 | 歷史事實。`docs/batch-b-recovery-investigation.md` 記載失敗的 delete 目標未留存，無法還原。 |
| `actual_foreign_key_rules_not_supplied_to_auditor` | 由 REC-A3 取代 | 現行 FK metadata 可由 REC-F4 直接讀取。仍需實機執行。 |
| `fresh_version_canonical_id_and_all_row_links` | 拆為 HEG-2a 與 REC-F9／PD-2 | 見 1.10。已刪 row 的歷史 `canonical_id` 無法還原（HEG-2a）；但「目前是否仍有列參照已刪 PK」可直接查詢，不屬於 HEG。 |
| `fresh_current_fingerprint_and_full_metadata_visibility` | REC-A2 ＋ VEG-1 | fingerprint 由 REC-A2 取代。「完整 metadata 可見性」受唯讀帳號權限限制，見 1.11。 |
| `explicit_recovery_write_authorization` | 維持未授權 | 不屬於唯讀複驗（REC-G2）。 |

- **VEG-1（可見性證據缺口）**：trigger 存在性與完整 metadata 可見性，屬於「現況與權限」的證據缺口。它可以隨授權查詢或權限調整而補充，不是不可恢復的歷史事實，因此**不歸類為 HEG**。若唯讀帳號的 metadata 權限不足，`information_schema` 可能不完整，無法證明 trigger 或隱藏物件不存在。結論中必須明寫此限制。

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

### 1.9 PD-1：交易控制與 consistent snapshot（D-1 已裁決為方案 B；程式與實機未更新）

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

### 1.10 PD-2：REC-A9「已刪 PK 無殘留參照」（D-2 已裁決為必要條件；程式預設仍關閉）

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
- REC-A9 只涵蓋 FK 強制的參照。觸發器或應用程式層的參照不在範圍內，屬於 VEG-1 的 metadata 可見性議題（見 1.11）。
- 通過 REC-A9 不代表歷史 `canonical_id` 已還原；HEG-2a 仍然存在。

**Owner 決策**：是否將 REC-A9 列為必要條件（建議：是）。這是新增的驗收條件，**未經 Owner 核准前，不列入 Q8 已核准的條件**。

### 1.11 PD-3：唯讀帳號的 metadata 權限（D-3 已裁決；DBA 觀測見 §15.7 O；帳號建立另待 D-8）

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
5. TRIGGER 的處理方式：若方案不含 TRIGGER 權限，trigger 存在性不可證明，對應 VEG-1。
6. 在 DBA 的特權連線中，以相同查詢取得「特權基準計數」：基表數量 23、FK 邊數 29。此為 DBA 工作，需要獨立授權。

**正向控制 REC-A10（D-3 已裁決，2026-10-23；實機未驗證）**：
- 唯讀帳號讀取到的基表數量必須等於 23，FK 邊數必須等於 29（與 Baseline 及 Manifest 一致）。
- 數量不符 → `RECOVERY_NOT_VERIFIED`，判定為可見性不足，**不得**解讀為「表或 FK 不存在」。
- 若 DBA 無法提供特權基準計數，REC-A10 無法執行 → 停止，不以空集合或不完整結果通過。

**VEG-1 的分界**：
- 若 DBA 證明方案的 TRIGGER 可見性不足，VEG-1 仍然存在，結論需明寫。
- 若要消除 VEG-1，需另行裁決授予 TRIGGER 權限。那屬於權限擴大，不是預設值。

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
SELECT u.discord_user_id
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
SELECT discord_user_id
FROM users
WHERE credit_balance < 0;
```

- 草案的欄位已依 `002` 核對（`jb2_claims` 只用 `ledger_id` 與 `discord_user_id`）。實際執行前仍需 P-L1 的正式核對。
- **金額不出現在查詢結果中（LED-A4）**：F1 的餘額與 ledger 加總比較在資料庫內以 `HAVING` 完成，只回傳不一致的使用者識別；F5 只回傳識別。草案中先前的 `credit_balance` 與 `ledger_total` 欄位已移除。
- 實作時須使用參數化語句，不得字串插值。

### 3.7 Acceptance Criteria

- **LED-A1**：LED-F1 至 LED-F5 皆為零列。
- **LED-A2**：全部查詢在同一 snapshot 中完成。**受 PD-1 限制**：若採方案 A，必須在結論中揭露「未保證跨查詢 snapshot」，不得宣稱已保證。
- **LED-A3**：對外（報告、日誌、錯誤訊息、判定結果）只輸出各規則的異常計數、異常類型與遮蔽後的識別；不輸出原始 ID、個別餘額、Ledger 加總或差額。金額明細須 Owner 另行明確授權。
- **LED-A4**：內部計算與對外輸出分離。SQL 不回傳金額欄位；對帳程式如需金額，只能在記憶體內比較，且不得出現在任何輸出中。
- **LED-A5**：成功、異常與錯誤路徑（含例外訊息與判定物件的 repr）都不得洩漏金額或原始 ID。此項以離線測試驗證，不以人工檢查代替。
- 目前狀態：**全部 Unverified**。

### 3.8 Blocked Behavior（Q15）

- **LED-B1**：任一條件不通過即 Blocked：立即停止相關驗收；Private Beta 不得開放任何資料庫寫入；建立獨立 Ledger Integrity Audit 紀錄，只提交異常計數與遮蔽識別（LED-A3），不含金額。
- **LED-B2**：保留原始 Ledger、餘額與歷史資料，不修改。
- **LED-B3**：不自動補寫 opening ledger、不歸零、不自動修復或補償。
- **LED-B4**：Owner 裁決前，Blocked 不解除。
- **LED-B5**（空快照）：若 `users` 快照為空，判定為 `INCONCLUSIVE_EMPTY_SNAPSHOT`（資料為空、無法完成有效驗收）。這**不是**帳務損壞，也**不是** PASS；它阻擋 Beta 啟用，不作為 BLOCKED 的帳務異常回報，計數為零，不輸出任何識別。

### 3.9 Test Plan

- **離線（未來實作後）**：純函式測試，驗證 LED-F1 至 LED-F5 的判定，使用合成資料。
- **實機（需授權）**：唯讀查詢一次，記錄 LED-A1 至 LED-A3。
- 目前狀態：全部 Unverified。

### 3.10 Owner Authorization Gates

- **LED-G1**：當次授權，須指定 Beta DB 名稱、唯讀帳號與 PD-1 的收尾方式。
- **LED-G2**：任何修復、補寫或歸零須另行授權，且不屬於本規格範圍。

### 3.11 Dependencies / Pending

- **依賴**：REC-A*；P-L3；PD-1。
- **P-L1（分開記錄）**：「靜態欄位已核對」（欄位名稱已依 `schema.sql` 與 `002` 逐一比對，離線測試以設計層級檢查覆蓋）；「實機 Schema 未核對」（尚未與 Beta DB 或測試庫的實際 Schema 比對，需要授權後才能做）。兩者不可合併為「已核對」。
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
| Q8 | 複驗完成標準與 HEG | REC-A1～A8、HEG-1、HEG-2a、VEG-1、REC-A9（PD-2） | 1 |
| Q9 | Ledger 一致性定義（Beta 啟用前） | LED-F1～F5、LED-A1～A5、P-L3 | 3 |
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
| PD-1 | 方案 B 語句清單，並擴大 Q7 的字面範圍（1.9） | 設計已裁決（D-1，見 P 節）；實機執行另需授權 | TK-01（收尾子項）、TK-06、TK-09 |
| PD-2 | 是否將 REC-A9 列為必要條件（1.10） | 設計已裁決（D-2，見 P 節）；程式預設仍關閉，需實作確認 | TK-06 |
| PD-3 | 唯讀帳號的 DBA 權限方案與 REC-A10 三項分開計數（1.11） | 設計已裁決（D-3，見 P 節）；DBA 觀測見 §15.7 O；帳號建立與實機仍待 D-8、D-9 | TK-05、TK-06 |
| PD-4 | BETA-DB-1 Control 三開關變更與 E-S／E-L 證據（4.4） | **仍為 Blocked，未核准**；與 PD-3、D-1～D-4 無關，不因上述裁決而核准 | TK-11 |
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
| Recovery 獨立複驗 | Unverified（僅使用者回報的 `RECOVERY_SUCCESS`；journal 已遺失，無法由本次工作重驗；見 §15.9.9） |
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

**狀態更新（2026-10-23，離線實作結果）**：

| Ticket | 狀態 | 離線證據（本輪實際執行，非歷史） |
| --- | --- | --- |
| TK-01 | 離線完成（Unverified 於真實 Recovery；無已核准 baseline，實機 VERIFIED 不可達） | `tests/test_batch_b_readonly_recovery.py` 122 passed；REC-F4 依 Owner 裁決：內容相同不即拒絕，需可追溯、已核准、獨立的完整 baseline（含 provenance），觀測值不得充當 baseline；REC-A9、REC-A10 預設關閉，待 PD-2、PD-3 |
| TK-02 | 離線完成（Unverified 於真實 Beta DB） | `tests/test_ledger_integrity.py` 45 passed；SQL 只做靜態設計層級檢查；P-L1 待決 |
| TK-03 | 離線完成（Unverified 於真實 Batch B） | 入口守衛涵蓋所有 Batch B 指令入口（含 recovery、recovery_write、recovery_fk、fk_lab、migrate、preflight、migration_preflight、acceptance 六種模式、worker、harness）；pytest 在 `-O` 下以退出碼 3 結束；啟動器在任何憑證提示前以實際解譯器判定 assert 是否停用（涵蓋 `-O`、`-OO`、`PYTHONOPTIMIZE`），退出碼 3 不提示、不連線、不產生 PASS；離線測試：`tests/test_batch_b_interpreter_check.py` 8 passed、PowerShell 拒絕模擬 13 項、既有啟動器模擬 119 項。未執行真實 MySQL、真實啟動器子程序或 Discord。 |
| TK-04 | 完成 | `.env.example` 註解依 CONF-3 修訂；`.env` 未讀取或修改 |

- 完整離線回歸（本輪）：1426 passed、55 skipped（MySQL 與 Batch B opt-in 皆未開啟）、1 warning（既有 audioop 棄用）。
- TK-05 至 TK-12 仍為授權待決，阻擋類別見各 ticket；本輪未執行任何資料庫操作、Gateway、commit 或 push。

**原有審查條件（保留）**：可供授權審查，尚未授權實作。 每張 ticket 的阻擋類別以三種區分：**設計決策未定**（需 Owner 決定）、**執行授權未取得**（需當次授權）、**前置證據未完成**（需先有資料或前置工作）。

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
- 驗收條件：離線測試通過；欄位核對與 `002` 一致；SQL 不回傳金額（LED-A4）；成功、異常與錯誤路徑不洩漏金額或原始 ID（LED-A5）。
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
- Requirement ID：REC-G1、REC-A1～A10、REC-S1、HEG-1、HEG-2a、VEG-1。
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
- TK-12 → TK-13（長期部署；排在私人 Beta 人工驗收之後，不阻擋本機 Beta 測試）

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

## 12. 候選預期規則清單（靜態整理，未核准，供 Owner 審核）

**狀態**：靜態候選，**未核准，不得作為 baseline**。來源見各列。Owner 裁決（2026-10-23）：觀測值不得充當 baseline；此清單只供審核與 DBA 補充。

**靜態事實（可核對）**：
- 26 個 FK 約束：`schema.sql` 9 個（行 45–111）＋ `002_submission_economy.sql` 17 個（行 22–140）。
- 29 個欄位位置：24 個單欄約束＋`fk_ledger_recommendation`（2 欄）＋`fk_dispatch_owner`（3 欄）；與 Manifest 的 `reviewed_fk_column_edges` 29 條數量一致。
- 與 `batch-b-recovery-investigation.md` 第 106 行的「26 個約束、29 個欄位位置」一致。
- DDL 沒有任何 FK 子句宣告 `ON DELETE`／`ON UPDATE`（`schema.sql`、`002` 的 FK 行均不含規則）；第 106 行亦明載「Schema 沒有寫 ON DELETE／ON UPDATE 動作」。
- `batch-b-recovery-investigation.md` 第 69 行定義的**接受集合**：DELETE_RULE、UPDATE_RULE 只接受 RESTRICT 或 NO ACTION；拒絕 CASCADE、SET NULL、未知或缺失規則。這是可接受範圍，不是逐條的確定值。

**衝突與缺口**：
- 逐條確定值**無法由靜態資料判定**；DDL 未宣告，文件只給接受集合。
- 第 106 行明載：最近唯讀報告的 `foreign_key_rules` 原文未提供，不能假稱已核對實際 DELETE_RULE／UPDATE_RULE。
- 本表的「接受集合」不是 baseline。baseline 需要 Owner 核准、可追溯，並逐條列出 ON UPDATE、ON DELETE 動作。

| # | 約束名稱 | 來源 | 子表.欄 | 父表.欄 | DDL 宣告的規則 | 候選值與狀態 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `fk_submissions_user` | schema.sql:45 | `discord_user_id` | `users.discord_user_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 2 | `fk_submissions_song` | schema.sql:46 | `song_id` | `songs.song_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 3 | `fk_recommendations_user` | schema.sql:63 | `discord_user_id` | `users.discord_user_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 4 | `fk_recommendations_song` | schema.sql:64 | `song_id` | `songs.song_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 5 | `fk_ledger_user` | schema.sql:84 | `discord_user_id` | `users.discord_user_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 6 | `fk_ledger_award` | schema.sql:85 | `awarded_song_id` | `songs.song_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 7 | `fk_ledger_recommendation` | schema.sql:86 | `(recommendation_id, discord_user_id)` | `recommendations(recommendation_id, discord_user_id)` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 8 | `fk_dispatch_user` | schema.sql:109 | `discord_user_id` | `users.discord_user_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 9 | `fk_dispatch_owner` | schema.sql:111 | `(recommendation_id, discord_user_id, song_id)` | `recommendations(recommendation_id, discord_user_id, song_id)` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 10 | `fk_jb2_version_root` | 002:22 | `canonical_id` | `jb2_versions.version_id（self）` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 11 | `fk_jb2_video_song` | 002:32 | `song_id` | `songs.song_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 12 | `fk_jb2_legacy_song` | 002:38 | `song_id` | `songs.song_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 13 | `fk_jb2_event_user` | 002:50 | `discord_user_id` | `users.discord_user_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 14 | `fk_jb2_event_video` | 002:51 | `video_id` | `jb2_videos.video_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 15 | `fk_jb2_receipt_event` | 002:58 | `operation_id` | `jb2_events.operation_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 16 | `fk_jb2_claim_version` | 002:67 | `version_id` | `jb2_versions.version_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 17 | `fk_jb2_claim_event` | 002:68 | `operation_id` | `jb2_events.operation_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 18 | `fk_jb2_claim_ledger` | 002:69 | `ledger_id` | `credit_ledger.ledger_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 19 | `fk_jb2_claim_user` | 002:70 | `discord_user_id` | `users.discord_user_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 20 | `fk_jb2_settlement_event` | 002:76 | `operation_id` | `jb2_events.operation_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 21 | `fk_jb2_review_video` | 002:87 | `video_id` | `jb2_videos.video_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 22 | `fk_jb2_evidence_video` | 002:97 | `video_id` | `jb2_videos.video_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 23 | `fk_jb2_exclusion_version` | 002:116 | `version_id` | `jb2_versions.version_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 24 | `fk_jb2_barrier_version` | 002:122 | `version_id` | `jb2_versions.version_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 25 | `fk_jb2_exception_version` | 002:133 | `version_id` | `jb2_versions.version_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |
| 26 | `fk_jb2_reminder_video` | 002:140 | `video_id` | `jb2_videos.video_id` | 未宣告；候選接受集合 RESTRICT／NO ACTION | **待確認（DBA 以已核准 baseline 逐條指定）** |

**仍需 Owner 或 DBA 提供**：
1. 已核准的逐條 baseline（26 個約束、29 個欄位位置），含 ON UPDATE、ON DELETE 動作，以及核准紀錄與來源可追溯資訊。
2. 若 DBA 認定 DDL 未宣告的規則等於 MySQL 預設，須以該版本的實際行為書面確認，並由 Owner 核准；這仍不能取代 baseline 本身。
3. 與本清單不一致時，以已核准 baseline 為準，並記錄衝突。

### 12.1 官方文件技術查證（2026-10-23，唯讀，未連線資料庫）

**來源**（MySQL 8.0 Reference Manual，官方網頁，逐字引用經網頁讀取確認）：
- FOREIGN KEY 約束：https://dev.mysql.com/doc/refman/8.0/en/create-table-foreign-keys.html
- REFERENTIAL_CONSTRAINTS 資料表：https://dev.mysql.com/doc/refman/8.0/en/information-schema-referential-constraints-table.html

**查證結果（逐字引用）**：
1. 省略子句的預設值：「For an `ON DELETE` or `ON UPDATE` that is not specified, the default action is always `NO ACTION`.」
2. 等價關係：「`RESTRICT`: ... Specifying `RESTRICT` (or `NO ACTION`) is the same as omitting the `ON DELETE` or `ON UPDATE` clause.」InnoDB 的 `NO ACTION` 「is equivalent to `RESTRICT`」。
3. SHOW CREATE TABLE：「an `ON DELETE NO ACTION` or `ON UPDATE NO ACTION` clause that is specified explicitly does not appear in [`SHOW CREATE TABLE`] output」；`RESTRICT` 則會出現在輸出中。
4. REFERENTIAL_CONSTRAINTS：`UPDATE_RULE`、`DELETE_RULE` 的可能值為 `CASCADE`、`SET NULL`、`SET DEFAULT`、`RESTRICT`、`NO ACTION`。**頁面沒有說明省略子句時顯示哪一個值。**

**版本標示**：兩頁都只標示「MySQL 8.0 Reference Manual」與 version 8.0 選單，沒有 8.0.x 的修補版號。本專案的 DB 版本證據來自既有文件 `docs/batch-b-fk-lab.md`（MySQL 8.0.46，本機觀察，2026-10 前後）與 harness 最低要求 8.0.22（`tests/batch_b_harness_database.py`）。兩者都是專案文件記載，不是官方頁面的版號。

**語意等價與字串顯示的區分**：
- 語意等價：省略、`NO ACTION`、`RESTRICT` 在 InnoDB 上行為相同（官方明載）。
- 字串顯示：`REFERENTIAL_CONSTRAINTS` 回傳的字串可能是 `NO ACTION` 或 `RESTRICT`，**官方頁面未說明省略時的顯示值**。因此 metadata 比對不得把兩者視為相同字串，除非有經核准的等價政策（見 PD-5）。

### 12.2 26 個約束、29 個欄位位置的對應

| 項目 | 結果 |
| --- | --- |
| DDL 是否宣告 ON DELETE／ON UPDATE | **26 個約束皆未宣告**（12 附錄列表的來源行均不含子句） |
| 由 DDL 可形成的預期語意 | 每個約束的 DELETE 與 UPDATE 語意皆為 `NO ACTION`，等價於 `RESTRICT`（依官方 1 與 2） |
| 由 DDL 可形成的預期字串 | **無法唯一決定**：metadata 字串可能為 `NO ACTION` 或 `RESTRICT`（依官方 4），需要經核准的 baseline 或等價政策指定 |
| 來自已審核 DDL 的預期規則 | **可形成候選**：依 schema.sql 與 002 遷移檔的 DDL，預期語意為 NO ACTION（等價 RESTRICT）；是否採納、以哪一字串呈現，屬 Owner 裁決 |
| 是否可自動標為已核准 | **否**。本節只是技術依據，不是 baseline，也不是 Owner 核准 |

**衍生的待決事項（PD-5，設計裁決）**：FK 規則比對的等價政策。候選方案：
- 方案 A：只接受 metadata 的字串完全相同（最嚴格，但需要 baseline 逐條指定字串）。
- 方案 B：以官方語意為準，將 `NO ACTION` 與 `RESTRICT` 視為同一等價類，並在 baseline 中記錄等價政策與來源。

建議：方案 B，但須由 Owner 核准，且須在 baseline 中明列政策版本與官方來源。本文件不自行採用。

**仍需證據或授權（不在離線收尾範圍）**：
1. 經 Owner 核准的 baseline（26 約束、29 欄位位置，含 ON UPDATE 與 ON DELETE 的語意與字串）。
2. 實機 `REFERENTIAL_CONSTRAINTS` 的省略子句顯示值，需 DBA 在授權的唯讀查詢中確認（目前未連線）。
3. 目標 MySQL 修補版本的書面確認（目前只有專案文件中的 8.0.46 觀察值）。

## 13. 最終離線收尾與真正待決事項（2026-10-23）

**離線 ticket 狀態**：TK-01 離線完成（122 測試）；TK-02 離線完成（45 測試）；TK-03 離線完成（見上表）；TK-04 完成。TK-05 至 TK-12 未開始，需授權。

**真正待決事項（逐項標明類別）**：

| 編號 | 事項 | 類別 | 說明 |
| --- | --- | --- | --- |
| PD-1 | 交易控制與 snapshot 方案 | 設計裁決（尚未核准，非已拒絕） | 現行 allowlist 拒絕 START TRANSACTION、ROLLBACK、COMMIT、SET；這是程式現行行為，不代表 PD-1 被否決。 |
| PD-2 | REC-A9（已刪 PK 無殘留參照）是否列為必要條件 | 設計裁決（尚未核准） | 程式中預設關閉。 |
| PD-3 | REC-A10 正向控制與 DBA 最小權限方案 | 設計裁決（尚未核准）＋DBA 證據 | 程式中預設關閉。 |
| PD-4 | BETA-DB-1 的 Control 三開關變更 | 設計裁決＋證據（Blocked） | 需 E-S1～E-S3、E-L1～E-L4 證據與 Owner 裁決。 |
| PD-5 | FK 規則比對的等價政策（NO ACTION 與 RESTRICT） | 設計已裁決（D-4，見 P 節；僅限已確認的 InnoDB 範圍） | 見 12.1 與 15.3；政策不取代 D-5 基準。 |
| B-1 | FK baseline（26 約束、29 位置，含 ON UPDATE 與 ON DELETE，含 provenance） | 候選已綁定（§15.2A）；核准缺（D-5 未核准） | 未核准前 Recovery 實機 VERIFIED 不可達。 |
| B-2 | 實機 REFERENTIAL_CONSTRAINTS 省略子句的顯示值 | 已有觀測（§15.7 O：29 列全部 NO ACTION） | 觀測只作比對，不取代 D-5 的預期來源。 |
| B-3 | 目標 MySQL 修補版本的書面確認 | 證據（缺） | 目前只有專案文件的 8.0.46 觀察值。 |
| B-4 | 實機 Schema 與 Beta DB 核對（P-L1 實機部分） | 證據（需授權） | 靜態欄位已核對；實機未核對。 |
| B-5 | Beta DB 名稱（P-L3） | 設計輸入（Owner 指定） | 不得為 `jukebox101_test`。 |
| B-6 | E-S 與 E-L 證據（快照、寫入者清單、processlist） | 證據（缺，需 DBA） | 為 PD-4 的前提。 |
| X-1 | 唯讀帳號建立（TK-05） | 執行授權（DBA 獨立授權） | 不在離線範圍。 |
| X-2 | Recovery 實機唯讀複驗（TK-06） | 執行授權（REC-G1）＋前置證據（B-1、B-2、X-1） | |
| X-3 | Batch B 實機 23 項（TK-07） | 執行授權（BB-G1～G3）＋前置證據（TK-06 通過） | |
| X-4 | Beta DB 唯讀核對（TK-08） | 執行授權＋設計輸入（B-5） | |
| X-5 | Ledger 實機對帳（TK-09） | 執行授權（LED-G1）＋前置證據（TK-08） | |
| X-6 | Beta 啟用條件核對（TK-10） | 前置證據（TK-06、TK-07、TK-09） | 文件工作，無寫入。 |
| X-7 | Control 三開關變更（TK-11） | 執行授權（BETA-AUTH-3）＋ PD-4 核准 | |
| X-8 | Gateway 與兩帳號人工驗收（TK-12） | 執行授權（BETA-AUTH-1、BETA-AUTH-2）＋ Q12 程序環境 | |
| X-9 | TK-13 長期部署（免費方案選型、帳號註冊、資源建立、資料遷移）| 執行授權（Owner 選型與獨立授權）＋前置證據（TK-12 完成） | 本輪不建立任何雲端資源。 |
| G-1 | 本機 commit | 執行授權（每次 GIT-1） | 本階段未授權 commit。 |
| G-2 | push 至 origin | 執行授權（每次） | 本階段未授權 push。 |

**已由 Owner 裁決並完成，不再列為待決**：退出碼 3 為拒絕；子模組入口守衛範圍（已授權）；測試檔 `test_batch_b_launcher_refusal.ps1` 範圍（已接受）；空快照為 INCONCLUSIVE 並阻擋 Beta；REC-F4 的相同內容規則已依裁決取消；憑證提示前的解譯器預檢查（已實作）。

**已知的小問題（未修，屬文字與測試）**：`tests/test_batch_b_acceptance_launcher.ps1` 的標頭仍寫「never starts Python」，但現在會實際啟動探測模組；行為正確，只是註解過時，需由 Owner 決定是否由 agent-c 修正（不在本輪授權的必要範圍）。

## 14. 未來部署方向與後續 ticket（TK-13）

**狀態**：方向性規格，**本輪不實作、不建立雲端資源、不註冊帳號、不搬移資料、不修改現有資料庫目標或授權條件**。供應商未指定；實際選型時須查證最新官方條款與額度。

### 14.1 部署方向

- 以免費額度的雲端主機執行 Discord Bot，目標為持續運作。
- 以免費額度的雲端資料庫保存投稿、Ledger、餘額與歷史。
- **相容性前提**：優先維持目前 MySQL 的相容性要求。不得僅因服務標示「MySQL 相容」就認定可直接遷移；必須逐項驗證第 14.3 節所列的行為。
- **費用原則**：免費為優先目標。若無法符合必要條件，回報限制與低成本替代方案，由 Owner 決定；不自行啟用付費資源。
- **時程**：排在目前私人 Beta 人工驗收（TK-12）之後，作為長期部署階段，不阻擋本機 Beta 測試。

### 14.2 TK-13 長期部署 ticket（草稿，未授權）

**TK-13 免費雲端部署：評估、驗證、規劃與驗收（長期）**
- 狀態：**未開始**。阻擋類別：前置證據未完成（TK-12 完成）；設計裁決未定（供應商選型，Owner 決定）；執行授權未取得（註冊、建立資源、遷移，各需獨立授權）。
- 目的：在不影響本機 Beta 測試的前提下，規劃以免費額度運作的 Bot 與資料庫，並驗證其相容性與安全性。
- Requirement ID：本節 DEP-1 至 DEP-4；對應 Q13（每次 commit 與 push 另行授權）與 Q5（每次實機與資源建立需當次授權）。
- 交付物：下列四項各自的評估紀錄，以及選型建議（不含已建立的資源）。
- 依賴：TK-12（私人 Beta 人工驗收）。
- 驗收條件：下列 DEP-1 至 DEP-4 全部有書面結論，且 Owner 已選定供應商或接受替代方案。
- 授權範圍：文件評估不需授權；註冊帳號、建立資源、搬移資料各需獨立授權。
- 停止條件：任何方案需要付費才能符合必要條件（須先回報，不得自行啟用）；任何條款無法於官方來源確認。

### 14.3 子項與驗收重點

**DEP-1 免費方案評估**
- 持續運作：主機是否會休眠、重啟或回收；Bot 的 Gateway 連線是否可持續。
- 額度：CPU、記憶體、儲存、連線數、流量、資料庫容量與請求數上限。
- 信用卡要求：註冊或使用是否需要信用卡；超額是否自動計費。
- 超額費用：超額後的計費方式與上限設定。
- 資料保存限制：免費資料庫的保留期限、備份策略與是否會刪除閒置資料。
- 所有條款以實際選型時的官方頁面為準（本文件不引用任何價格或條款數字）。

**DEP-2 資料庫相容性驗證（不得僅憑「MySQL 相容」）**
- 交易：InnoDB 交易、隔離等級、commit 與 rollback 行為。
- 外鍵：FK 的 ON DELETE／ON UPDATE 行為與 metadata 顯示（與第 12 節的規則比對一致）。
- CHECK：目前 schema 含 CHECK 約束（`users.credit_balance >= 0`、`jb2_control` 的單例條件等）；須確認目標版本實際強制執行。
- 鎖定：`SELECT ... FOR UPDATE`、列鎖等待與 lock wait timeout（目前測試依賴 1205 錯誤）；`GET_LOCK` 的可用性。
- 權限：應用程式、Migrator、唯讀帳號的權限模型是否可等價實作；information_schema 的可見性。
- 備份與還原：是否可匯出、還原，並驗證還原後的 schema fingerprint 與 FK metadata 一致。
- 每一項都需以目標服務的官方文件與實測（經授權）確認；未驗證的項目標為 Unverified。

**DEP-3 秘密管理、加密連線與營運**
- 秘密管理：Discord token、資料庫密碼以平台秘密機制注入，不寫入倉庫或映像檔；`.env` 不進入部署產物。
- 加密連線：應用程式至資料庫的 TLS，以及憑證驗證方式。
- 網路存取限制：資料庫僅允許應用程式來源存取；管理介面不對外公開。
- 自動重啟：程序異常終止後的重啟策略，且重啟不得自動重送已回覆不明的操作（BETA-T5）。
- 日誌：不得記錄 token、密碼、原始 ID 或金額（沿用 LED-A4、A5 的輸出規則）。
- 基本監控：程序存活、資料庫連線失敗、錯誤率與磁碟使用量。

**DEP-4 遷移、部署驗收、回復與費用上限**
- 資料遷移：遷移前的 schema fingerprint 與逐表筆數；遷移後與來源比對；不得在驗收前刪除來源資料。
- 部署驗收：以非正式資料演練，驗證 Bot 可登入、Gateway 可持續連線、`/share`、`/history`、`/balance` 行為與本機一致。
- 失敗回復：遷移失敗或驗收失敗時，回到本機來源，不以新資料覆寫來源。
- 費用上限：Owner 指定的上限與告警門檻；超過上限須停止資源，不得自動升級方案。

### 14.4 與既有 ticket 與依賴的關係

- TK-01 至 TK-12 的編號與狀態保持不變。
- 依賴：TK-12 → TK-13。
- TK-13 不阻擋 TK-05 至 TK-12 的任何實機步驟。
- 若 TK-13 的評估結論需要修改 BETA 或 Recovery 的規格，須另行提出裁決，不得由 TK-13 自行修改。

## 15. TK-05／TK-06 前置資料包（離線，不連線、不執行）

**本節性質**：審閱用草案與技術查證。**不執行任何 SQL、不建立帳號、不連線資料庫、不核准 baseline。** 所有 SQL 以佔位符撰寫，真實密碼不得寫入倉庫或聊天。

### 15.1 TK-05 唯讀帳號方案（建議，待 Owner 核准）

**建議帳號**：`'jukebox101_test_readonly'@'127.0.0.1'`。命名沿用既有的 `jukebox101_test_app`、`jukebox101_test_harness`、`jukebox101_test_migrator`（皆綁定 `127.0.0.1`）。**帳號名稱已由 D-6 採用**；存在即停止（前置檢查，見下方步驟 0b）。

**目標限制**：僅限 `127.0.0.1:3306` 的 `jukebox101_test`。不得用於 `jukebox101` 或 Beta DB。

**必要權限（最小集合）**：
- 對 23 張表各授予表層級 `SELECT`（清單見下方 SQL）。
  - 用途：REC-F3 的 `SHOW CREATE TABLE`、REC-F5 讀取 `jb2_control`、REC-F6 筆數、REC-F7 讀取 `jb2_migrations`、REC-F9 反向參照列計數。
  - 官方依據：`SHOW CREATE TABLE` 需要「對該表擁有某種權限」（[SHOW CREATE TABLE](https://dev.mysql.com/doc/refman/8.0/en/show-create-table.html)）；`SELECT` 權限可在表層級授予（[GRANT](https://dev.mysql.com/doc/refman/8.0/en/grant.html)）。
- **不授予**：全域權限（`*.*`）、schema 層級權限（`jukebox101_test.*`）、`TRIGGER`、`PROCESS`、`REFERENCES`、`CREATE`、`INSERT`、`UPDATE`、`DELETE`、`DROP`、`ALTER`、`GRANT OPTION`、任何角色。
  - `TRIGGER` 不授予的理由：官方說明 `TRIGGER` 權限是「display triggers」所必需（[Privileges Provided](https://dev.mysql.com/doc/refman/8.0/en/privileges-provided.html)）。不授予即代表 trigger 存在性無法由唯讀帳號證明，對應 VEG-1。

**information_schema 可見性**：官方明載「對大多數 INFORMATION_SCHEMA 表，使用者只能看到其有適當權限之物件對應的列」，且「必須對某物件擁有某種權限，才能看到其資訊」（[INFORMATION_SCHEMA and Privileges](https://dev.mysql.com/doc/refman/8.0/en/information-schema-introduction.html)）。因此：
- 上述 23 張表的表層級 SELECT 可使對應物件出現在 `TABLES`、`KEY_COLUMN_USAGE` 與 `SHOW CREATE TABLE` 的結果中。
- `REFERENTIAL_CONSTRAINTS` 每一列在何種條件下可見，**官方頁面未逐字說明，待 DBA 以實機確認**，不得假定。

**正向控制（REC-A10，D-3 已裁決）**：唯讀帳號讀到的數量必須與基準相符，**並分開計數**：
- `TABLES` 的 `BASE TABLE` 數：23。
- `REFERENTIAL_CONSTRAINTS` 的約束數：**26**（每列一個約束）。
- `KEY_COLUMN_USAGE` 中有參照的欄位位置數：**29**（每列一個欄位位置）。
- 注意：舊版規格 REC-A10 寫為「FK 邊數 29」，語意不精確，本節改為上述三項分開計數；**此修正需寫回 REC-A10 條文**（見第 15.4 節 D-3）。

**連線與交易（與 PD-1 相關）**：唯讀帳號的交易行為是 PD-1 的待決事項，本節不預設。

**最終 TK-05 SQL 草案（D-6 已採用；D-8 另待授權，本輪不執行）**

**管理身分前提**：執行者必須是 Owner 指定、並由 DBA 證明具備 `CREATE USER` 與 GRANT OPTION 的管理帳號。repo 內既有帳號都不具備：app 無此權限；migrator 只有表層 DDL 與 SELECT 等權限，無 `CREATE USER`，也無 GRANT OPTION；harness 只有清理權限，不得用於此操作。管理帳號的名稱不寫入文件。

**步驟 0（管理身分前置，唯讀；2026-10-23 修訂，檢查方式見下方清單）**：
```sql
SHOW GRANTS FOR CURRENT_USER();
```
必須顯示具備 `CREATE USER` 與 GRANT OPTION 的全域權限。不具備即停止。

**管理身分檢查清單（2026-10-23 修訂；只看 `mysql.user` 的兩個 Y 不足，須全部通過）**：
1. **身分**：管理帳號為 Owner 指定的本機既有 MySQL 管理帳號（名稱不寫入文件，不預設為 root）。不得是 `jukebox101_test_app`、`jukebox101_test_harness`、`jukebox101_test_migrator`，也不得是 TK-05 要建立的 `jukebox101_test_readonly`。
2. **全域權限（直接授權）**：`mysql.user` 中與 `CURRENT_USER()` 對應的該列，`Create_user_priv` 與 `Grant_priv` 皆為 `Y`（欄位名已由官方 grant-tables 頁確認）；另 `SHOW GRANTS FOR CURRENT_USER();` 逐行解析，確認全域 `CREATE USER` 與 GRANT OPTION。官方 SHOW GRANTS 說明：全域 GRANT OPTION 套用於已授予的靜態全域權限。
3. **有效角色**：`SELECT CURRENT_ROLE();` 必須為 `NONE`。若否，逐一以 `SHOW GRANTS FOR CURRENT_USER() USING <角色>` 檢視，並記錄。官方：未啟用的角色權限不生效，`SHOW GRANTS` 不顯示有效權限全貌。`mysql.role_edges` 對該帳號必須零列。**未驗證**：透過角色取得的權限能否滿足 GRANT OPTION；因此本流程只接受直接授予的權限。
4. **指定表的授權能力**：官方 GRANT 規定，授權者須同時具備 GRANT OPTION「與」被授予的權限。故管理帳號須對 `jukebox101_test` 的 23 張表各自持有 `SELECT`（全域 SELECT 或表級 SELECT 皆可），且 GRANT OPTION 為直接授予。實機核對方式（`SHOW GRANTS` 表級列，或 `mysql.tables_priv`）**欄位與輸出格式待 DBA 於目標版本確認**。
5. **結論**：任一項不符或無法判定即停止，不以「兩個 Y」判定通過，也不進入步驟 0b。

**步驟 0b（前置，唯讀）**：
```sql
SELECT user, host, account_locked FROM mysql.user WHERE user = 'jukebox101_test_readonly';
```
預期零列。若已存在即停止，不覆寫、不 ALTER。

**步驟 1（建立，保持鎖定）**：
```sql
CREATE USER 'jukebox101_test_readonly'@'127.0.0.1' IDENTIFIED BY RANDOM PASSWORD ACCOUNT LOCK;  -- 方案 C；密碼由伺服器產生，見下方「密碼輸入」
```
密碼只由 DBA 在本機輸入，不得出現在對話、文件或命令列參數中（見下方「密碼輸入」）。

**步驟 2（授權，23 表 SELECT）**：
```sql
GRANT SELECT ON `jukebox101_test`.`users` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`songs` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`submissions` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`recommendations` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`credit_ledger` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`daily_dispatches` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`jb2_control` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`jb2_versions` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`jb2_videos` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`jb2_legacy` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`jb2_events` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`jb2_receipts` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`jb2_claims` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`jb2_settlements` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`jb2_review_history` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`jb2_evidence` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`jb2_decisions` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`jb2_exclusions` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`jb2_barriers` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`jb2_exceptions` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`jb2_reminders` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`jb2_reminder_batches` TO 'jukebox101_test_readonly'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`jb2_migrations` TO 'jukebox101_test_readonly'@'127.0.0.1';
```

**步驟 3（核對，帳號仍鎖定）**：
```sql
SHOW GRANTS FOR 'jukebox101_test_readonly'@'127.0.0.1';
SELECT FROM_USER, FROM_HOST, TO_USER, TO_HOST FROM mysql.role_edges WHERE TO_USER = 'jukebox101_test_readonly' AND TO_HOST = '127.0.0.1';
SELECT user, host, account_locked FROM mysql.user WHERE user = 'jukebox101_test_readonly';
```
通過條件（以有效權限判定，不以行數判定）：
- `SHOW GRANTS` 允許含 `GRANT USAGE ON *.*`。除此之外，有效權限只能是 `jukebox101_test` 的 23 個指定表層級 `SELECT`，不得有 `WITH GRANT OPTION`、其他權限、schema 或全域權限。
- `mysql.role_edges` 必須零列（無角色）。
- `account_locked` 必須為 `Y`（仍鎖定）。

任何不符即停止，不進入步驟 4。

**步驟 4（解鎖，獨立閘門）**：只有在步驟 3 全部通過，且 Owner 另行確認後才執行：
```sql
ALTER USER 'jukebox101_test_readonly'@'127.0.0.1' ACCOUNT UNLOCK;
```

**步驟 5（解鎖後，以新帳號核對）**：由操作者在本機以 `jukebox101_test_readonly` 登入，依 §15.7 N 節的同一組 Q1、Q2、Q3 核對：
- Q1：`account` 必須為 `jukebox101_test_readonly@127.0.0.1`；`roles` 為 `NONE`；`fk` 為 `1`；`server_uuid` 相符。
- Q2：必須為 29 列與 26 個約束，且名稱與 §15.7 O 節觀測值一致。這一項同時驗證新帳號對 FK metadata 的可見性；列數不足即停止並回報，不修復。
- Q3：必須為 23 個 BASE TABLE、InnoDB。

**撤銷與刪除（另案授權，不在 TK-05 內）**：
**撤回候選步驟（候選，未執行；每一項都需 Owner 另行授權）**：
- 撤回 A（先停止使用，可逆）：`ALTER USER 'jukebox101_test_readonly'@'127.0.0.1' ACCOUNT LOCK;`。效果：拒絕新登入，既有授權保留，可用步驟 4 的語句解鎖。
- 撤回 B（移除權限，保留帳號）：對 23 張表逐條 `REVOKE SELECT ON jukebox101_test.<表名> FROM 'jukebox101_test_readonly'@'127.0.0.1';`。效果：之後 TK-06 的權限核對會拒絕（`READONLY_GRANTS_REJECTED`）；不影響其他表與其他帳號。
- 撤回 C（刪除帳號）：`DROP USER 'jukebox101_test_readonly'@'127.0.0.1';`。效果：帳號與其全部權限消失；不影響 `jukebox101_test` 的資料與 Schema、harness 與 application 帳號。若之後仍需要，須重新走 D-8。
- 驗證：A 以 `SELECT user, host, account_locked FROM mysql.user WHERE user = 'jukebox101_test_readonly';` 確認 `account_locked` 為 `Y`；B 以 `SHOW GRANTS` 確認僅剩 `USAGE`；C 以同一查詢確認零列。
- 影響與限制：以上全部為 DDL，會隱含提交，**不能 ROLLBACK**。建議順序為先 A 停止使用，再由 Owner 決定 B 或 C。撤回後若 Recovery 仍需唯讀檢查，須重新授權。

**重要限制**：`CREATE USER`、`GRANT`、`ALTER USER` 都是 DDL，會隱含提交，**不能用 ROLLBACK 撤銷**。任何步驟失敗後，應立即停止，並回報當前帳號狀態（步驟 0b 與步驟 3 的查詢結果），撤銷或刪除另待授權。

**密碼輸入（2026-10-23 更新，方案 C：伺服器產生隨機密碼；Owner 原則接受，本輪未執行 CREATE USER）**：
- 語句（逐字，不含密碼值）：`CREATE USER 'jukebox101_test_readonly'@'127.0.0.1' IDENTIFIED BY RANDOM PASSWORD ACCOUNT LOCK;`
- 官方依據（MySQL 8.0 CREATE USER 頁，2026-10-23 查證）：隨機密碼自 8.0.18 起可用；語句會把明文密碼以結果集回傳給執行者；需具備全域 `CREATE USER` 權限（或 `mysql` schema 的 INSERT 權限）；若啟用 `read_only`，另需 `CONNECTION_ADMIN` 或 `SUPER`。目標版本 8.0.46 ≥ 8.0.18。
- **未查證**：結果集欄位格式、是否只顯示一次、預設長度與 `generated_random_password_length` 的範圍（位於 password-management 官方頁，本輪未取得）。
- **日誌與紀錄**（官方 password-logging 頁）：`IDENTIFIED BY <字面密碼>` 的密碼會在一般查詢日誌、慢查詢日誌與 binary log 中被改寫。本語句不含字面密碼，因此沒有需要改寫的值。官方 CREATE USER 頁仍警示：CREATE USER 在某些情況下可能被記錄在 server log 或 `~/.mysql_history`。
- **未解風險**：CREATE USER 頁的 binary log 條目說明中，隨機密碼以一個欄位寫入，描述不一致（有處稱 password-hash，有處稱隨機密碼值）。**需 DBA 於執行前確認 binary log 是否啟用，以及隨機值是否寫入**；若寫入且 binlog 會被保留，本方案風險升高，須另行決定處置。
- **暴露途徑評估（依實際途徑，不以「短暫存在記憶體」一概否決）**：
  1. 終端機畫面與捲動紀錄：密碼在結果集中出現，屬人工可見；由 Owner 在本機操作，不交給代理工具。
  2. 互動式 client history：語句不含密碼值，依官方 mysql 客戶端說明，風險低（推論，未實測）。
  3. 代理工具輸出、對話、文件：不進入，不得貼入。
  4. 記憶體：client 與 server 短暫持有，屬一般資料庫操作的固有面，不作為單獨否決理由。
- **輸入方式**：`mysql -p` 不帶值會提示輸入（官方 user-names 頁）。提示是否直接讀取 TTY 未查證，因此不設計 stdin 送密碼。
- **失敗處理**：若產生的密碼遺失，需另案 `ALTER USER` 重設（DDL，需獨立授權）。

**密碼輸入（舊方案，已由上方方案 C 取代，保留為對照）**：
- 方案 A（優先）：以 PowerShell `Read-Host -AsSecureString` 在記憶體中組出 `CREATE USER` 語句，經 stdin 送入管理身分的 `mysql.exe`；不寫入磁碟、不進入命令列參數、不進入 mysql 歷史。**此方式未實測**，因為 `--password` 提示與 stdin 管線的互動尚未在本機驗證。
- 方案 B（備援）：在管理身分的互動 mysql 會話中手動輸入語句。啟動時加入 `--histignore="*IDENTIFIED*"`（`mysql.exe --help` 確認此選項存在；其預設值未查證，因此明確指定）。注意：互動輸入時，密碼會顯示在終端機畫面上，操作後應清除捲動紀錄。
- 方案 A 是否可行，需由 DBA 在執行前自行確認；無法確認就改用方案 B。

**TK-05 尚需修正的技術細節（記錄，本輪不實作）**：
1. **管理身分的權限要求**：除 `CREATE USER` 與 GRANT OPTION 外，管理身分還須對指定 23 表具有可轉授的有效 SELECT 權限，並具備讀取 `mysql.user` 與 `mysql.role_edges` 的權限，以完成步驟 0b 與步驟 3 的核對。
2. **SHOW GRANTS 的解讀**：不能只搜尋 `WITH GRANT OPTION` 等關鍵字。必須逐行解析完整輸出，判定有效權限集合（範圍、權限種類與是否可轉授），並與 23 表 SELECT 的預期集合比對。
3. **密碼方案 A 尚未驗證**：不能直接標為可安全執行。執行前須在獲授權的環境中確認 stdin 與密碼提示能否同時運作，且不會把明文寫入磁碟或歷史。
4. **方案 B 不等於完整保密**：畫面明文與歷史排除只是降低風險，不等於完整保密。不得以「清除捲動紀錄」作為保密保證。
5. **不假定管理帳號**：repo 內沒有任何已證實具備所需權限的帳號。管理身分由 Owner 指定，並由 DBA 證明。（2026-10-23：Owner 決定使用本機既有 MySQL 管理帳號，不新增專用 DBA 帳號；權限尚未證明，名稱不寫入文件。）

**需要實機才能確認的項目（不自行假定）**：
1. 帳號名稱是否已被使用。
2. `REFERENTIAL_CONSTRAINTS` 的列級可見條件。
3. `TRIGGERS` 在無 TRIGGER 權限時的實際結果（預期不完整，需以 DBA 確認）。
4. 目標 MySQL 修補版本的行為（官方 8.0 頁面沒有修補版號）。

### 15.2 FK baseline 審核包（26 約束、29 欄位位置）

**說明**：以下每列由程式自 DDL 解析產生（來源、行號、原始宣告文字均為逐字取得）。**獨立性規則**：預期值的來源是已審核的 baseline（目前未核准）。實機觀測值只用於比較與確認呈現方式，不得用來自行批准 baseline，也不得以觀測值充當預期答案。
- **DDL 推導的預期語意**：省略子句 ⇒ `NO ACTION`，等價於 `RESTRICT`（官方依據見 12.1）。
- **實機觀測值**：已由 Owner 手動取得，見 §15.7 O 節（29 列、26 約束、全部 `NO ACTION`，與本表一致）。觀測只作比對，不取代下列預期來源。
- **provenance**：來源檔案與行號；**無 Owner 核准紀錄**。
- **狀態**：全部為「候選，未核准」。

| # | 約束 | 子表 | 子表欄位（位置） | 父表(欄位) | 欄位位置數 | 來源（檔案:行） | 原始宣告（逐字） | DDL 推導的語意（DELETE／UPDATE） | 實機 metadata 字串 | 狀態 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `fk_submissions_user` | `submissions` | discord_user_id | `users`(discord_user_id) | 1 | `bot/database/schema.sql:45` | `CONSTRAINT fk_submissions_user FOREIGN KEY (discord_user_id) REFERENCES users (discord_user_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 2 | `fk_submissions_song` | `submissions` | song_id | `songs`(song_id) | 1 | `bot/database/schema.sql:46` | `CONSTRAINT fk_submissions_song FOREIGN KEY (song_id) REFERENCES songs (song_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 3 | `fk_recommendations_user` | `recommendations` | discord_user_id | `users`(discord_user_id) | 1 | `bot/database/schema.sql:63` | `CONSTRAINT fk_recommendations_user FOREIGN KEY (discord_user_id) REFERENCES users (discord_user_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 4 | `fk_recommendations_song` | `recommendations` | song_id | `songs`(song_id) | 1 | `bot/database/schema.sql:64` | `CONSTRAINT fk_recommendations_song FOREIGN KEY (song_id) REFERENCES songs (song_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 5 | `fk_ledger_user` | `credit_ledger` | discord_user_id | `users`(discord_user_id) | 1 | `bot/database/schema.sql:84` | `CONSTRAINT fk_ledger_user FOREIGN KEY (discord_user_id) REFERENCES users (discord_user_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 6 | `fk_ledger_award` | `credit_ledger` | awarded_song_id | `songs`(song_id) | 1 | `bot/database/schema.sql:85` | `CONSTRAINT fk_ledger_award FOREIGN KEY (awarded_song_id) REFERENCES songs (song_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 7 | `fk_ledger_recommendation` | `credit_ledger` | recommendation_id、discord_user_id | `recommendations`(recommendation_id, discord_user_id) | 2 | `bot/database/schema.sql:86-87` | `CONSTRAINT fk_ledger_recommendation FOREIGN KEY (recommendation_id, discord_user_id) REFERENCES recommendations (recommendation_id, discord_user_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 8 | `fk_dispatch_user` | `daily_dispatches` | discord_user_id | `users`(discord_user_id) | 1 | `bot/database/schema.sql:109` | `CONSTRAINT fk_dispatch_user FOREIGN KEY (discord_user_id) REFERENCES users (discord_user_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 9 | `fk_dispatch_owner` | `daily_dispatches` | recommendation_id、discord_user_id、song_id | `recommendations`(recommendation_id, discord_user_id, song_id) | 3 | `bot/database/schema.sql:111-112` | `CONSTRAINT fk_dispatch_owner FOREIGN KEY (recommendation_id, discord_user_id, song_id) REFERENCES recommendations (recommendation_id, discord_user_id, song_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 10 | `fk_jb2_version_root` | `jb2_versions` | canonical_id | `jb2_versions`(version_id) | 1 | `bot/database/migrations/002_submission_economy.sql:22` | `CONSTRAINT fk_jb2_version_root FOREIGN KEY (canonical_id) REFERENCES jb2_versions(version_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 11 | `fk_jb2_video_song` | `jb2_videos` | song_id | `songs`(song_id) | 1 | `bot/database/migrations/002_submission_economy.sql:32` | `CONSTRAINT fk_jb2_video_song FOREIGN KEY (song_id) REFERENCES songs(song_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 12 | `fk_jb2_legacy_song` | `jb2_legacy` | song_id | `songs`(song_id) | 1 | `bot/database/migrations/002_submission_economy.sql:38` | `CONSTRAINT fk_jb2_legacy_song FOREIGN KEY (song_id) REFERENCES songs(song_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 13 | `fk_jb2_event_user` | `jb2_events` | discord_user_id | `users`(discord_user_id) | 1 | `bot/database/migrations/002_submission_economy.sql:50` | `CONSTRAINT fk_jb2_event_user FOREIGN KEY (discord_user_id) REFERENCES users(discord_user_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 14 | `fk_jb2_event_video` | `jb2_events` | video_id | `jb2_videos`(video_id) | 1 | `bot/database/migrations/002_submission_economy.sql:51` | `CONSTRAINT fk_jb2_event_video FOREIGN KEY (video_id) REFERENCES jb2_videos(video_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 15 | `fk_jb2_receipt_event` | `jb2_receipts` | operation_id | `jb2_events`(operation_id) | 1 | `bot/database/migrations/002_submission_economy.sql:58` | `CONSTRAINT fk_jb2_receipt_event FOREIGN KEY (operation_id) REFERENCES jb2_events(operation_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 16 | `fk_jb2_claim_version` | `jb2_claims` | version_id | `jb2_versions`(version_id) | 1 | `bot/database/migrations/002_submission_economy.sql:67` | `CONSTRAINT fk_jb2_claim_version FOREIGN KEY (version_id) REFERENCES jb2_versions(version_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 17 | `fk_jb2_claim_event` | `jb2_claims` | operation_id | `jb2_events`(operation_id) | 1 | `bot/database/migrations/002_submission_economy.sql:68` | `CONSTRAINT fk_jb2_claim_event FOREIGN KEY (operation_id) REFERENCES jb2_events(operation_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 18 | `fk_jb2_claim_ledger` | `jb2_claims` | ledger_id | `credit_ledger`(ledger_id) | 1 | `bot/database/migrations/002_submission_economy.sql:69` | `CONSTRAINT fk_jb2_claim_ledger FOREIGN KEY (ledger_id) REFERENCES credit_ledger(ledger_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 19 | `fk_jb2_claim_user` | `jb2_claims` | discord_user_id | `users`(discord_user_id) | 1 | `bot/database/migrations/002_submission_economy.sql:70` | `CONSTRAINT fk_jb2_claim_user FOREIGN KEY (discord_user_id) REFERENCES users(discord_user_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 20 | `fk_jb2_settlement_event` | `jb2_settlements` | operation_id | `jb2_events`(operation_id) | 1 | `bot/database/migrations/002_submission_economy.sql:76` | `CONSTRAINT fk_jb2_settlement_event FOREIGN KEY (operation_id) REFERENCES jb2_events(operation_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 21 | `fk_jb2_review_video` | `jb2_review_history` | video_id | `jb2_videos`(video_id) | 1 | `bot/database/migrations/002_submission_economy.sql:87` | `CONSTRAINT fk_jb2_review_video FOREIGN KEY (video_id) REFERENCES jb2_videos(video_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 22 | `fk_jb2_evidence_video` | `jb2_evidence` | video_id | `jb2_videos`(video_id) | 1 | `bot/database/migrations/002_submission_economy.sql:97` | `CONSTRAINT fk_jb2_evidence_video FOREIGN KEY (video_id) REFERENCES jb2_videos(video_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 23 | `fk_jb2_exclusion_version` | `jb2_exclusions` | version_id | `jb2_versions`(version_id) | 1 | `bot/database/migrations/002_submission_economy.sql:116` | `CONSTRAINT fk_jb2_exclusion_version FOREIGN KEY (version_id) REFERENCES jb2_versions(version_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 24 | `fk_jb2_barrier_version` | `jb2_barriers` | version_id | `jb2_versions`(version_id) | 1 | `bot/database/migrations/002_submission_economy.sql:122` | `CONSTRAINT fk_jb2_barrier_version FOREIGN KEY (version_id) REFERENCES jb2_versions(version_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 25 | `fk_jb2_exception_version` | `jb2_exceptions` | version_id | `jb2_versions`(version_id) | 1 | `bot/database/migrations/002_submission_economy.sql:133` | `CONSTRAINT fk_jb2_exception_version FOREIGN KEY (version_id) REFERENCES jb2_versions(version_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |
| 26 | `fk_jb2_reminder_video` | `jb2_reminders` | video_id | `jb2_videos`(video_id) | 1 | `bot/database/migrations/002_submission_economy.sql:140` | `CONSTRAINT fk_jb2_reminder_video FOREIGN KEY (video_id) REFERENCES jb2_videos(video_id)` | 省略（ON DELETE：NO ACTION；ON UPDATE：NO ACTION） | NO ACTION（O 節觀測一致） | 候選，待核准 |

**合計核對**：26 個約束，29 個欄位位置＝24 個單欄約束（各 1 位置）＋ 2 個複合約束（`fk_ledger_recommendation` 2 位置、`fk_dispatch_owner` 3 位置）。與 Manifest 的 `reviewed_fk_column_edges` 29 條數量一致（數量一致，逐條對應尚未在本輪完成）。

### 15.2A D-5 候選綁定摘要（2026-10-23，待核准）

- **候選識別**：`D5-CAND-2026-10-23-A`；內容雜湊 **SHA-256 `c04869f7571d97b250dd6f29d48843dda5c242a032b7a5c8082e49609cad8685`**。
- **雜湊計算**：解析 `bot/database/schema.sql` 與 `bot/database/migrations/002_submission_economy.sql` 的全部 `CONSTRAINT … FOREIGN KEY` 宣告，依約束名排序，欄位保留 DDL 順序（即 `ORDINAL_POSITION` 1..n），以無空白、排序鍵的 canonical JSON（UTF-8）計算 SHA-256。解析腳本位於會話 scratchpad：`/home/gaston/.claude/projects/-mnt-d-Project-MusicBased-discordBot-JukeBox101/7672195e-005f-44bf-9f65-a98c8bb9058e/scratchpad/d5_candidate.py`（非專案檔）。
- **雜湊涵蓋**：約束名、子表、子表欄位與順序、父表、父欄與順序、來源檔案行號。**不涵蓋**規則（規則由省略子句推得，見下）與實機觀測。DDL 任一處改動即雜湊失效。
- **複合欄位順序（ORDINAL_POSITION）**：
  - `fk_ledger_recommendation`：1 `recommendation_id`→`recommendations.recommendation_id`；2 `discord_user_id`→`recommendations.discord_user_id`。
  - `fk_dispatch_owner`：1 `recommendation_id`、2 `discord_user_id`、3 `song_id`，依序對應父表同名欄位。
  - 其餘 24 個單欄約束，欄位位置皆為 1。
- **規則分類來源（共用，不逐列重複）**：DDL 無 `ON UPDATE`／`ON DELETE` 子句 ⇒ 推得 `NO ACTION`（官方依據見 12.1）；分類政策見 15.3（D-4 已裁決，方案 B，僅限 InnoDB）。
- **實機觀測引用**：§15.7 O 節 Q2 的 29 列；Q1、Q3 亦見 O 節。觀測與本候選逐條一致（子表、欄位、順序、父表、父欄、規則）。
- **Manifest 比對**：`docs/batch-b-recovery-manifest.json` 的 `reviewed_fk_column_edges` 29 條，以（子表、欄位、順序、父表、父欄）比對，**全部一致**。Manifest 不含約束名，因此約束名層級的綁定仍只依 DDL 與 O 節。
- **核准人與核准日期**：待核准（不預填）。
- **本摘要不含**：TRIGGERS、CHECK、索引、ON UPDATE 之外的其他規則。

### 15.3 PD-5 比對政策建議（候選，待 Owner 核准）

**官方事實**（12.1）：省略子句、`NO ACTION`、`RESTRICT` 在 InnoDB 上語意等價；但 `REFERENTIAL_CONSTRAINTS` 的顯示字串未說明省略時的值。

**建議政策（方案 B）**：
- 比對以「語意類別」進行：`{NO ACTION, RESTRICT}` 視為同一類別，其他值（`CASCADE`、`SET NULL`、`SET DEFAULT`）不屬於此類別，須拒絕。
- baseline 必須逐條記錄：語意類別、政策版本、官方來源 URL、核准人與日期。
- 若 baseline 寫的是字串（例如 `NO ACTION`），而實機顯示 `RESTRICT`，依政策屬於同一類別，但須在結論中揭露「字串不同、語意等價」。

**適用條件**：只適用於 InnoDB 表，且 baseline 已核准。

**限制**：
- 本政策不改變 `CASCADE`、`SET NULL`、`SET DEFAULT` 的拒絕。
- 本政策不取代 baseline 本身；沒有已核准 baseline 時，比對仍 fail-closed。
- 本政策依賴官方文件目前的語意說明；目標版本若有不同行為，需重新查證。

**方案 A（對照）**：只接受字串完全相同。較嚴格，但需要 baseline 逐條指定字串，且對 DDL 未宣告的約束沒有依據。

### 15.4 Owner 決策清單（一次回答）

| 編號 | 白話說明 | 類別 | 建議 | 影響 |
| --- | --- | --- | --- | --- |
| D-1（PD-1） | 唯讀檢查要不要用「同一時間點」的交易來讀？ 方案 A：只發 SELECT，每個查詢各自讀最新資料，不保證多個查詢是同一時間點。方案 B：先設定 REPEATABLE READ，再用 `START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY` 開交易，最後 `ROLLBACK`；這需要你另外授權這三類語句。 | 設計裁決 | 方案 B，因為官方明載 `WITH CONSISTENT SNAPSHOT` 才會開始一致性讀取，且只在 REPEATABLE READ 下生效；方案 A 無法主張同一時間點證據。 | 方案 B 擴大 Q7 的字面授權；方案 A 的結論必須揭露無 snapshot 保證。 |
| D-2（PD-2） | 是否把「已刪除的主鍵，在其他表中是否仍被參照」列為 Recovery 必要條件（REC-A9）？ | 設計裁決 | 列為必要，因為這是目前可直接查驗的現況，且只讀取。 | 會增加一項必要條件，實機結果可能因殘留參照而 NOT_VERIFIED。 |
| D-3（PD-3） | 唯讀帳號的權限方案，與正向控制（REC-A10）的計數要求。計數請分開為：23 張基表、26 個約束、29 個欄位位置（修正舊規格的「FK 邊數 29」）。 | 設計裁決（方案與條文）＋ DBA 證據（基準計數） | 採 15.1 的方案，並把 REC-A10 條文修正為三項分開計數。 | 不採用 REC-A10 則無法區分「可見性不足」與「確實不存在」。 |
| D-4（PD-5） | FK 規則比對的語意政策：`NO ACTION` 與 `RESTRICT` 是否視為同一類別（方案 B）或只接受字串相同（方案 A）。 | 設計裁決 | 方案 B，並依 15.3 的限制與條件。 | 方案 A 需要 baseline 逐條指定字串。 |
| D-5 | 是否核准 15.2 的 26 列候選作為 baseline？目前沒有 Owner 核准紀錄，也沒有實機觀測值。 | 證據＋核准 | 尚未具備，§15.7 O 節的觀測已由 Owner 補登；仍缺核准人、核准日期與綁定候選版本的核准文字（見 15.8）。 | 未核准前，實機驗證無法達成 VERIFIED。 |
| D-6 | 唯讀帳號名稱是否採用 `jukebox101_test_readonly`？ | 設計裁決 | 採用，但需 DBA 確認名稱未被使用。 | 名稱衝突時需改名，不得覆寫既有帳號。 |
| D-7 | 是否授權一次受限的唯讀版本與 metadata 查詢（草稿見 15.7），以取得 PD-3、D-5 所需的證據？ | 執行授權 | 授權，但只限下列三項 SELECT，且不得寫入。 | 會以 `jukebox101_test_harness` 連線測試庫（非唯讀帳號，見 15.7 B）。 |
| D-8 | 是否授權 TK-05（建立帳號與授權，DBA 執行）？ | 執行授權 | 待 D-1 至 D-7 裁決後再授權。 | DDL 性質，需獨立授權。 |
| D-9 | 是否授權 TK-06（Recovery 實機唯讀複驗）？ | 執行授權 | 待 D-5 與 TK-05 完成後再授權。 | 需 REC-G1 的當次授權。 |
| D-10 | 啟動器預檢查、子模組守衛與測試檔範圍等已完成項目，是否需另行確認？ | 已裁決 | 不需要。 | — |

### 15.5 執行順序與授權邊界

**TK-05 與 TK-06 的前置條件**：
- TK-05（建立唯讀帳號）：需 D-1（若採方案 B 則需 D-1 的授權）、D-3 方案、D-6 名稱確認，以及 DBA 的獨立授權。**不需要** D-5 的 baseline 即可建立帳號，但建立後不得用於任何實機驗證，直到 D-5 完成。
- TK-06（Recovery 實機）：需 TK-05 完成、D-5 的核准 baseline（或明確記錄為 fail-closed）、D-2 與 D-4 的裁決、REC-G1 的當次授權。

**版本與 metadata 證據的循環問題**：
- 取得目標版本與省略子句的實際顯示值，需要一次受限的唯讀查詢。
- 這次查詢不需要唯讀帳號，因為唯讀帳號尚未建立。因此建議以**既有的 `jukebox101_test_harness` 帳號**（非唯讀，D-7 不使用其寫入能力）做一次性的受限查詢，且僅限下列三項：
  - 正式語句、連線初始化、輸出範圍與停止條件，以第 15.7 節為準（本節較早的草稿已取代，不再使用）。
- 上述查詢只做讀取，不寫入，不建立任何物件。其輸出需遮蔽後存檔，不得包含密碼。
- 這需要 D-7 的獨立授權，且必須在 TK-05 之前完成，因為 D-5 的 baseline 需要它。

**Beta DB 名稱與 TK-13 雲端部署不阻擋 TK-05 與 TK-06**：兩者都只針對 `jukebox101_test`。

**下一次實機操作的精確授權範圍（待 Owner 核准，本輪未授權）**：
- 操作一：D-7 的一次性受限唯讀查詢（15.5 的三項 SELECT）。
  - 目標：`127.0.0.1:3306/jukebox101_test`。
  - 帳號：`jukebox101_test_harness`（非唯讀），由操作者本機輸入，不得寫入文件。（此項已由 Owner 手動執行，見 P 節 D-7 與 §15.7 O 節。）
  - 允許語句：僅上述三項。
  - 停止條件：任何非 SELECT；目標不符；輸出含密碼；查詢失敗。
- 操作二（D-8）：TK-05 的 DBA 建立帳號與授權（15.1 的 SQL，名稱經確認、密碼由 DBA 本機產生）。
  - 停止條件：帳號已存在；權限超出 15.1 的清單。
- 操作三（D-9）：TK-06 的 Recovery 實機唯讀複驗。
  - 允許語句：PD-1 核准的清單，以及 REC-A1 至 A9（A10 依 D-3）。
  - 停止條件：任一 REC 失敗；非清單語句；目標不符；收尾失敗。

**本輪未授權**：資料庫連線、任何 SQL、DDL、DML、GRANT、Recovery、Gateway、遠端指令同步、`.env` 修改、commit、push。

### 15.6 本節的技術查證來源

- [GRANT](https://dev.mysql.com/doc/refman/8.0/en/grant.html)、[Privileges Provided](https://dev.mysql.com/doc/refman/8.0/en/privileges-provided.html)、[CREATE USER](https://dev.mysql.com/doc/refman/8.0/en/create-user.html)、[REVOKE](https://dev.mysql.com/doc/refman/8.0/en/revoke.html)（讀取摘要已逐字核對部分段落；長文件的 SUMMARY 段落視為轉述，已標示）。
- [INFORMATION_SCHEMA and Privileges](https://dev.mysql.com/doc/refman/8.0/en/information-schema-introduction.html)、[SHOW CREATE TABLE](https://dev.mysql.com/doc/refman/8.0/en/show-create-table.html)。
- [START TRANSACTION／COMMIT](https://dev.mysql.com/doc/refman/8.0/en/commit.html)、[InnoDB 隔離等級](https://dev.mysql.com/doc/refman/8.0/en/innodb-transaction-isolation-levels.html)。
- **未確認**：`transaction_isolation`、`transaction_read_only` 的變數名稱與 `tx_isolation` 的移除狀態（系統變數頁面的讀取不完整）。標為待 DBA 以目標版本確認。
- **未確認**：`READ ONLY` 單獨是否建立 snapshot（官方頁面未說明）。因此 PD-1 方案 B 必須使用 `WITH CONSISTENT SNAPSHOT`。

### 15.7 D-7 受限唯讀查詢授權包（修訂版，離線草稿，未授權、未執行）

**本節性質**：D-7 授權包與已由 Owner 手動執行的觀測紀錄（O 節）。D-1～D-4、D-6 已於 P 節裁決；D-5、D-8、D-9 未核准。本節不授權連線、SQL 執行、測試、Gateway、commit 或 push。

**修訂紀錄（相對於前版）**：(1) 前版「禁止完整資料列」與 metadata 輸出要求相互矛盾，本版區分「允許的 metadata 列」與「禁止的業務資料與秘密」。(2) 前版把 `verified_connection` 描述為唯讀路徑，但它需要寫入相關開關，本版改為兩條明確路徑供選擇。(3) 前版未列出 PyMySQL 送出的全部語句，本版逐條列出。

#### A. 目標綁定

- 主機與埠：client 必須以 TCP 連到 `127.0.0.1:3306`。`@@port` 只是伺服器設定值，不能單獨證明連線位址；身分判定需同時符合 `DATABASE()`、`CURRENT_USER()`、`@@server_uuid`，以及 client 的連線設定（主機 `127.0.0.1`）。
- 資料庫：`jukebox101_test`。
- 預期 server UUID：`ab73bd54-c3c3-11f1-bc47-005056c00001`（來源：`docs/stage-b-only-run.md` 的 DBA 核准值；非秘密）。

#### B. 帳號（建議，需 D-7 核准）

- 建議帳號：`jukebox101_test_harness`@`127.0.0.1`。依 `docs/batch-b-test-harness-grants.sql`，它對 23 張表有表層級 SELECT。
- **明確揭露：此帳號不是唯讀帳號。** 它另有寫入與清理權限（同一份 grants 檔案）。D-7 **不授權**使用其寫入或清理能力，授權範圍只限第 F 節的語句。
- 替代帳號（不建議，需另行決定）：`jukebox101_test_app`、`jukebox101_test_migrator`（皆有寫入或 DDL 權限，且同樣不是唯讀帳號）。
- 不使用 root 或任何 DBA 管理帳號（僅指 D-7 查詢；TK-05 的管理身分見 15.1 步驟 0）。

#### C. 憑證

- 由操作者在本機輸入，不貼入對話、文件或 `.env`。
- 不讀取 `.env`，不列印程序環境變數或密碼。
- 若使用 `mysql` 用戶端：以 `-p`（不帶值）觸發本機密碼提示。
- 若使用既有 Python 路徑：憑證只能經程序環境注入，見第 D 節的前置條件。

#### D. 執行路徑（需 D-7 選定其一）

**路徑 A（建議）：`mysql` 用戶端，只送出下列語句。**
- 指令形式（不含密碼）：`mysql -h 127.0.0.1 -P 3306 -u jukebox101_test_harness -p jukebox101_test`。
- 實際送出的語句（依序）：
  1. 握手，指定資料庫 `jukebox101_test`（無 SQL 文字）。
  2. 用戶端自動的字元集設定（通常為 `SET NAMES utf8mb4`；**是否送出、確切語句未經官方文件或本機實測確認，需實機前驗證**）。
  3. Q1。
  4. Q2。
  5. Q3。
  6. `QUIT`（用戶端協議指令，不是 SQL）。
- 不送出：`SET AUTOCOMMIT`、`ROLLBACK`、`COMMIT`、`SET SESSION`、`SET TRANSACTION`。
- 交易行為：`mysql` 用戶端預設 autocommit 開啟（**未經本機驗證**）。若是，三項 SELECT 各自是獨立讀取，**不保證同一時間點**；此限制須揭露，並與 D-1 相關。

**路徑 B：既有 Python 路徑 `verified_connection`（不建議用於 D-7，除非 Owner 明確接受其開關）。**
- 不能直接用於唯讀查詢的原因：`tests/batch_b_harness_contract.py` 的 `load_profiles()` 要求 `JUKEBOX_RUN_BATCH_B_HARNESS=1`、`JUKEBOX_BATCH_B_EXCLUSIVE=jukebox101_test`，並呼叫 `profile_settings(RUN_SWITCH)`，後者需要應用程式的寫入相關開關與 `TEST_DB_*` 憑證；同時還需要 `TEST_HARNESS_DB_*` 憑證。這些開關與寫入能力超出 D-7 的讀取授權。
- 若採此路徑，實際送出語句如下（依 `verified_connection` 的原始程式順序）：
  1. 握手：`host=127.0.0.1`、`port=3306`、`user=jukebox101_test_harness`、`database=jukebox101_test`、`charset=utf8mb4`、`autocommit=False`、`connect_timeout`／`read_timeout`／`write_timeout` 依 `bot/database/connection.py` 的常數、`init_command="SET time_zone = '+00:00'"`。
  2. `SET NAMES utf8mb4`（PyMySQL 在 charset 有指定時送出，`pymysql/connections.py` 行 647–656、704–711）。
  3. `SET time_zone = '+00:00'`（`init_command`，`bot/database/connection.py` 行 35）。
  4. `SET AUTOCOMMIT = 0`（PyMySQL 依 `autocommit=False` 送出，`pymysql/connections.py` 行 485、729–730）。
  5. Q1（`check_identity`，`tests/batch_b_harness_database.py` 行 43–44）。
  6. `SELECT 1 FROM information_schema.APPLICABLE_ROLES LIMIT 1`（`check_identity`，行 53）。**用途**：確認沒有可啟用的角色；本草稿要求它必須回傳零列。
  7. `SET SESSION innodb_lock_wait_timeout=2`（`verified_connection`，行 29）。**用途**：測試用的鎖等待上限，與讀取結果無關；它是 session 變數變更，**不屬於唯讀語句**。
  8. `ROLLBACK`（`connection.rollback()`，`verified_connection` 行 30；PyMySQL 的 `rollback()` 送出 `ROLLBACK`，`pymysql/connections.py` 行 504–511）。
  9. Q2、Q3。
  10. 關閉連線：PyMySQL `close()` 送出 `COM_QUIT`（協議指令，`pymysql/connections.py` 行 422–436），不是 SQL。
- 環境變數：`TEST_DB_SERVER_UUID`（`expected_server_uuid()`，只讀取，不列印）。

**如果採用路徑 B，額外前置條件**：必須先由 Owner 核准「本次讀取期間開啟寫入相關開關」的風險，或另行撰寫僅含讀取語句的一次性腳本（那是新程式，需另行授權）。本草稿不建議。

#### E. 三項 SELECT（逐字，不含結尾分號，每次只執行一條）

Q1（身分、版本、角色與 server UUID）：
`SELECT DATABASE() AS db,CURRENT_USER() AS account,@@port AS port,VERSION() AS version, CURRENT_ROLE() AS roles,@@foreign_key_checks AS fk,@@server_uuid AS server_uuid`

Q2（FK 欄位位置與 ON UPDATE／ON DELETE 觀測值）：
`SELECT k.CONSTRAINT_NAME AS constraint_name, k.TABLE_NAME AS child_table, k.COLUMN_NAME AS child_column, k.ORDINAL_POSITION AS ordinal_position, k.REFERENCED_TABLE_NAME AS parent_table, k.REFERENCED_COLUMN_NAME AS parent_column, r.UPDATE_RULE AS update_rule, r.DELETE_RULE AS delete_rule FROM information_schema.KEY_COLUMN_USAGE AS k JOIN information_schema.REFERENTIAL_CONSTRAINTS AS r ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME AND r.TABLE_NAME = k.TABLE_NAME WHERE k.TABLE_SCHEMA = 'jukebox101_test' AND k.REFERENCED_TABLE_NAME IS NOT NULL ORDER BY k.CONSTRAINT_NAME, k.ORDINAL_POSITION`

Q3（基表與儲存引擎）：
`SELECT TABLE_NAME AS table_name, TABLE_TYPE AS table_type, ENGINE AS storage_engine FROM information_schema.TABLES WHERE TABLE_SCHEMA = 'jukebox101_test' ORDER BY TABLE_NAME`

**欄位名稱的官方查證（2026-10-23，MySQL 8.0 Reference Manual）**：
- `KEY_COLUMN_USAGE`：`CONSTRAINT_NAME`、`TABLE_NAME`、`COLUMN_NAME`、`ORDINAL_POSITION`、`REFERENCED_TABLE_NAME`、`REFERENCED_COLUMN_NAME`、`TABLE_SCHEMA`、`CONSTRAINT_SCHEMA` 皆在官方欄位列表中。`ORDINAL_POSITION` 定義為「約束內的位置，從 1 開始」。
- `REFERENTIAL_CONSTRAINTS`：`CONSTRAINT_SCHEMA`、`CONSTRAINT_NAME`、`TABLE_NAME`、`UPDATE_RULE`、`DELETE_RULE`、`REFERENCED_TABLE_NAME` 皆在官方欄位列表中。`UPDATE_RULE`、`DELETE_RULE` 的可能值為 `CASCADE`、`SET NULL`、`SET DEFAULT`、`RESTRICT`、`NO ACTION`。
- `TABLES`：`TABLE_SCHEMA`、`TABLE_NAME`、`TABLE_TYPE`、`ENGINE` 皆在官方欄位列表中。`TABLE_TYPE` 可能值為 `BASE TABLE`、`VIEW`、`SYSTEM VIEW`；臨時表不列出。
- **未由官方頁面確認**：`KEY_COLUMN_USAGE` 是否「只列出有被參照鍵的約束」。官方頁面的範例同時列出主鍵列，因此 Q2 以 `REFERENCED_TABLE_NAME IS NOT NULL` 篩選，但該欄位對非 FK 列的實際值仍需實機確認。

#### F. Q1 欄位與預期值的逐一對應

| 欄位 | 預期值 | 判定依據與限制 |
| --- | --- | --- |
| `db` | `jukebox101_test` | `DATABASE()`，依既有程式的同一判定。 |
| `account` | `jukebox101_test_harness@127.0.0.1` | `CURRENT_USER()` 回傳認證帳號的 user@host 組合（官方頁面以範例說明，未給固定模板）。這是**帳號身分**，不是 client 位址。 |
| `port` | `3306` | `@@port` 為伺服器設定值，範圍與型別**未由官方頁面確認**（官方摘要表未逐字取得）。不得單獨用來證明連線主機。 |
| `version` | `8.0.x`，且 x 與 patch 須符合既有程式的最低要求（8.0.22 或以上，非 MariaDB） | `VERSION()`；既有程式的最低版本要求來源為 `tests/batch_b_harness_database.py` 行 49–50。 |
| `roles` | 字串 `NONE` | 官方頁面明載 `CURRENT_ROLE()` 「或 `NONE` if there are none」。**是字串 `NONE`，不是 SQL `NULL`。** |
| `fk` | `1` | `@@foreign_key_checks`；型別與範圍**未由官方頁面逐字確認**，此預期值來自既有程式（行 47），屬程式來源而非官方來源。 |
| `server_uuid` | `ab73bd54-c3c3-11f1-bc47-005056c00001` | `@@server_uuid`；既有程式以小寫比較（`expected_server_uuid()`）。官方條目未逐字確認。 |

**判定規則**：`db`、`account`、`roles`、`server_uuid` 必須全部相符，`port`、`version`、`fk` 也必須相符；**任一不符即停止**。`port` 不能單獨證明連線主機，因此連線主機由 client 指令中的 `127.0.0.1` 與 `server_uuid` 共同支撐。

#### G. 正向控制（D-3，分開判定）

- 基表 23：Q3 中 `table_type = 'BASE TABLE'` 的列數必須為 23，且 `storage_engine` 皆為 `InnoDB`。
- FK 約束 26：Q2 中不重複的 `constraint_name` 數量必須為 26。
- FK 欄位位置 29：Q2 的列數必須為 29。
- **若 Q2 沒有任何列，或列數不符，只能宣稱「未取得 KEY_COLUMN_USAGE，29 個欄位位置未驗證」，不得宣稱已驗證 29 個位置。**
- `update_rule`、`delete_rule` 只作為觀測值記錄，用於與 baseline 比較及確認呈現方式；本授權包不用它們批准 baseline。

#### H. 允許輸出與禁止輸出（修正矛盾）

- **允許輸出（完整的核准 metadata 列）**：
  - Q1 的七個欄位值。
  - Q2 的所有列：約束名、子表名、欄位名、ordinal、父表名、父欄名、`update_rule`、`delete_rule`。
  - Q3 的所有列：表名、表類型、引擎。
  - 各項計數與比較結果。
- **禁止輸出**：
  - Q1、Q2、Q3 以外的任何查詢結果，包括業務資料列、使用者 ID、餘額、投稿內容。
  - 密碼、連線字串、程序環境變數值。
  - 完整 `SHOW CREATE TABLE` 輸出。
- 輸出存入本機工作檔時須遮蔽密碼；metadata 列可原樣保存，但不得貼入聊天或文件之外的位置。

#### I. 停止條件（任一即停止，不重試、不修復、不改寫語句）

- Q1 的任一身分欄位不符（見第 F 節判定規則）。
- 路徑 B 中：`APPLICABLE_ROLES` 回傳任何列。
- 任何語句錯誤、連線錯誤、逾時。
- Q2 或 Q3 結果不完整（列數不符、欄位缺失、空結果）。
- 出現第 D 節以外的任何 SQL。
- 輸出含禁止項目。

#### J. 收尾語句

- 路徑 A：送出 `QUIT`。
- 路徑 B：`ROLLBACK`（`connection.rollback()`，已列於第 D 節第 8 步），再 `COM_QUIT`（`close()`）。
- 任何路徑都不送出 `COMMIT`。

#### K. 本授權包不包含

- `information_schema.TRIGGERS`（屬 VEG-1；唯讀帳號不授予 TRIGGER）。
- 業務資料查詢、`SHOW CREATE TABLE`。
- `START TRANSACTION`、`SET TRANSACTION`（PD-1 未核准）。
- `GRANT`、`CREATE USER`、任何寫入。
- 路徑 B 的 `SET SESSION innodb_lock_wait_timeout=2`（若採路徑 A，本來就不送出）。

#### L. 需要 Owner 核准的項目

1. 路徑 A（`mysql` 用戶端）或路徑 B（既有 Python 路徑，風險較高）。
2. 帳號 `jukebox101_test_harness`，並接受其寫入權限不被使用的承諾。
3. 第 E 節三項 SELECT 與第 D 節語句清單。
4. 第 H 節允許輸出與第 I 節停止條件。
5. 憑證由操作者本機輸入的方式（第 C 節）。

#### M. 仍未驗證的項目（實機前須確認，不得假定）

- `mysql` 用戶端是否送出 `SET NAMES`、預設 autocommit 是否為 1、用戶端版本行為。
- `@@port`、`@@foreign_key_checks` 的官方型別與範圍（官方摘要表未逐字取得）。
- `@@server_uuid` 的官方條目。
- `KEY_COLUMN_USAGE` 對非 FK 列的 `REFERENCED_TABLE_NAME` 實際值。
- `CURRENT_USER()` 在本帳號上的實際字串格式（官方頁面以範例說明，未給固定模板）。
- 三項 SELECT 的實際執行結果（全部 Unverified）。

#### N. D-7 執行指南（2026-10-23 修訂；仍未授權執行）

**狀態**：執行指南。未連線、未執行 SQL。D 節中與本節衝突者，以本節為準。

**一、帳號**
- `jukebox101_test_harness`@`127.0.0.1` 經 Owner 原則接受，作為本次 metadata 查詢帳號。
- 此帳號**不是唯讀帳號**。本次不批准其用於 Recovery 正式唯讀驗收，不授權任何寫入、清理、DDL 或 GRANT。

**二、靜態查證（未連線，未讀取憑證內容）**

已確認：
- 用戶端為 `C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe`，版本 8.0.46（`--version`）。
- `--help` 說明：`--no-defaults` 為「不讀取任何 option file，登入檔（login file）除外」。因此 `--no-defaults` **不能**排除登入檔。
- 8.0.46 的 `--help` 未列出 `--no-login-paths`，不得使用該選項。
- `--no-auto-rehash`（`-A`）：`--help` 說明關閉自動補全用的 rehash；自動 rehash 會增加啟動時間，即為連線後的額外查詢。
- `--protocol=tcp`：`--help` 列出的合法值為 tcp、socket、pipe、memory。
- 本機（WSL 端）檢查：`C:\Users\*\AppData\Roaming\MySQL\.mylogin.cnf` 不存在（只檢查存在性）；環境變數名稱中無以 `MYSQL` 開頭者（只列名稱）。
- `C:\ProgramData\MySQL\MySQL Server 8.0\my.ini` 存在；`--no-defaults` 會略過它。其 `[client]` 段有 `port`，`[mysql]` 段只有 `no-beep`（僅檢查鍵名）。

未確認（本次無法查證）：
- `--default-character-set=utf8mb4` 是否會在連線後送出 `SET NAMES` 或其他語句。官方與本機說明未逐字確認此行為，因此本指南**不保證**連線後只有一條初始化語句。
- 8.0.46 用戶端在連線後是否還有其他內部語句。

**三、操作者在 Windows PowerShell 的前置確認（不連線）**

在執行連線命令前，先確認登入檔不存在，且沒有 MySQL 環境變數：

```powershell
Test-Path "$env:APPDATA\MySQL\.mylogin.cnf"
Get-ChildItem Env:MYSQL* | Select-Object Name
```

預期結果：第一行為 `False`，第二行無輸出。若任一不符，**停止**，不連線，並回報結果。

**四、連線命令（PowerShell）**

```powershell
& "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" --no-defaults --protocol=tcp --host=127.0.0.1 --port=3306 --user=jukebox101_test_harness --password --database=jukebox101_test --default-character-set=utf8mb4 --no-auto-rehash --batch
```

| 參數 | 作用 |
| --- | --- |
| `--no-defaults` | 不讀取 `my.ini` 等 option file（登入檔另見第三節前置確認）。 |
| `--protocol=tcp` | 強制 TCP。 |
| `--host=127.0.0.1`、`--port=3306` | 明確指定目標。 |
| `--user=jukebox101_test_harness` | 本次授權帳號。 |
| `--password`（不帶值） | 出現提示後由操作者在本機隱藏輸入。 |
| `--database=jukebox101_test` | 指定資料庫。 |
| `--default-character-set=utf8mb4` | 指定字元集，見第二節的未確認項。 |
| `--no-auto-rehash` | 關閉啟動時的自動補全查詢。 |
| `--batch` | 制表分隔輸出。 |

**五、輸入密碼與登入**

1. 執行第四節命令，出現 `Enter password:` 時，由操作者在本機直接輸入，畫面不會顯示。
2. 密碼不得貼入對話、文件、命令列、腳本或 `.env`。
3. 登入失敗即停止。不得重試、不得更換帳號。
4. 登入成功後，才進入第六節。

**六、查詢順序（每條 SQL 各為一個區塊，逐條貼入）**

步驟 1：Q1（身分、版本、角色與 server UUID）。

```sql
SELECT DATABASE() AS db,CURRENT_USER() AS account,@@port AS port,VERSION() AS version, CURRENT_ROLE() AS roles,@@foreign_key_checks AS fk,@@server_uuid AS server_uuid;
```

Q1 判定（逐欄）：
- `db` 必須是 `jukebox101_test`。
- `account` 必須是 `jukebox101_test_harness@127.0.0.1`。
- `roles` 必須是 `NONE`（官方 Information Functions 頁說明無作用角色時回傳 `NONE`）。
- `fk` 必須是 `1`（官方 FOREIGN KEY 頁說明 `foreign_key_checks` 預設啟用；布林值以 1 表示開啟）。
- `server_uuid` 必須是 `ab73bd54-c3c3-11f1-bc47-005056c00001`（大小寫不敏感）。
- `port` 必須輸出為 `3306`。**未查證**：`@@port` 的官方型別條目未取得，因此只做字串比對，其他格式即停止。
- `version` 必須是 MySQL 8.0.22 以上，且不得含 `mariadb`。

**Q1 任一欄不符，停止。不執行步驟 2 與 3。**

步驟 2：Q2（FK 欄位位置與 UPDATE、DELETE 規則；僅在 Q1 通過後執行）。

```sql
SELECT k.CONSTRAINT_NAME AS constraint_name, k.TABLE_NAME AS child_table, k.COLUMN_NAME AS child_column, k.ORDINAL_POSITION AS ordinal_position, k.REFERENCED_TABLE_NAME AS parent_table, k.REFERENCED_COLUMN_NAME AS parent_column, r.UPDATE_RULE AS update_rule, r.DELETE_RULE AS delete_rule FROM information_schema.KEY_COLUMN_USAGE AS k JOIN information_schema.REFERENTIAL_CONSTRAINTS AS r ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME AND r.TABLE_NAME = k.TABLE_NAME WHERE k.TABLE_SCHEMA = 'jukebox101_test' AND k.REFERENCED_TABLE_NAME IS NOT NULL ORDER BY k.CONSTRAINT_NAME, k.ORDINAL_POSITION;
```

Q2 判定：列數必須為 29；不重複的 `constraint_name` 必須為 26。列數或數量不符，只能宣稱「29 個欄位位置未驗證」，不得宣稱已驗證。`update_rule` 與 `delete_rule` 僅作觀測值，不用於核准 baseline。

步驟 3：Q3（基表與儲存引擎；僅在 Q1 通過後執行）。

```sql
SELECT TABLE_NAME AS table_name, TABLE_TYPE AS table_type, ENGINE AS storage_engine FROM information_schema.TABLES WHERE TABLE_SCHEMA = 'jukebox101_test' ORDER BY TABLE_NAME;
```

Q3 判定：`BASE TABLE` 必須為 23 列，且引擎皆為 `InnoDB`。

結束：輸入 `exit`（用戶端指令，不是 SQL）。

**七、允許與停止**

**允許，不算違規**：
- 登入流程。
- 用戶端在連線時依命令列選項產生的內部行為，包括第二節列為未確認的字元集設定。這些不是操作者送出的語句。
- 步驟 1 至 3 的 Q1、Q2、Q3。
- `exit`。

**停止（任一即停，不重試、不修復、不改寫語句）**：
- 登入失敗、連線錯誤或逾時。
- Q1 任一欄不符。
- 操作者送出 Q1、Q2、Q3 以外的任何語句，包括 `SET`、`SHOW`、`START TRANSACTION`、`COMMIT`、`ROLLBACK`、`INSERT`、`UPDATE`、`DELETE`、DDL 與 `GRANT`。
- Q2 或 Q3 出現錯誤、空結果或列數不符。
- 輸出含密碼、連線字串、業務資料、使用者 ID、餘額或投稿內容。

**八、輸出與回報**
- 輸出保存至專案目錄之外的本機路徑（例如 `%USERPROFILE%\jb_d7\`）。
- 可回傳 Q1 七欄、Q2 與 Q3 的 metadata 列與計數給 Owner；不得回傳密碼或業務資料。

**九、結論限制**
- 三條查詢之間無 snapshot，不宣稱跨查詢一致（D-1 待決）。
- 不宣稱 Recovery 通過；D-5 baseline 未核准。
- `@@port` 型別未查證，只做字串比對。
- 無法在不連線的情況下排除用戶端內部的連線後語句；本指南不保證其數量或內容。

**十、更正紀錄**
- 先前的 §15.7 N 節曾寫「`--no-defaults` 排除所有設定檔與登入路徑檔」。此說法錯誤，`--no-defaults` 不排除登入檔。本節已修正，並以前置確認取代。
- 先前曾寫「恰好一條 `SET NAMES utf8mb4`」，此保證未經證實，已刪除。


#### O. D-7 觀測紀錄（Owner 手動執行並提供；觀測證據，未核准為 baseline）

**性質與來源**：
- 由 Owner 依第 N 節的連線命令登入後，**只手動執行 Q1、Q2、Q3**，沒有手動執行其他 SQL，結果由 Owner 提供。
- 代理未親自執行，也未稽核完整的連線過程；用戶端內部的初始化行為不在 Owner 人工確認範圍內。
- 執行時間：**未知**（未精確記錄，本節不自行補造）。
- 本節不保存密碼或其他秘密。

**目標與環境**：
- 目標：`127.0.0.1:3306`（TCP），資料庫 `jukebox101_test`。
- 帳號：`jukebox101_test_harness`@`127.0.0.1`（非唯讀帳號，本次僅用於上列查詢）。
- MySQL 版本：8.0.46。
- server UUID：`ab73bd54-c3c3-11f1-bc47-005056c00001`。

**Q1 結果**（身分、版本與角色）：

```
db              jukebox101_test
account         jukebox101_test_harness@127.0.0.1
port            3306
version         8.0.46
roles           NONE
fk              1
server_uuid     ab73bd54-c3c3-11f1-bc47-005056c00001
```

Q1 判定：七欄全部符合預期。

**Q3 結果**（基表與引擎）：23 列 `BASE TABLE`，全部 `InnoDB`，表名與 Spec 的 23 個受審表完全相同。

```
credit_ledger, daily_dispatches, jb2_barriers, jb2_claims, jb2_control,
jb2_decisions, jb2_events, jb2_evidence, jb2_exceptions, jb2_exclusions,
jb2_legacy, jb2_migrations, jb2_receipts, jb2_reminder_batches, jb2_reminders,
jb2_review_history, jb2_settlements, jb2_versions, jb2_videos, recommendations,
songs, submissions, users
```

Q3 判定：23 列，符合預期。

**Q2 結果**（FK 欄位位置與規則）：29 列欄位位置，26 個不重複約束名。觀測到的 `UPDATE_RULE` 與 `DELETE_RULE` 在 29 列全部為 `NO ACTION`。

```
constraint_name          child_table         child_column       ord  parent_table        parent_column
fk_dispatch_owner        daily_dispatches    recommendation_id  1    recommendations     recommendation_id
fk_dispatch_owner        daily_dispatches    discord_user_id    2    recommendations     discord_user_id
fk_dispatch_owner        daily_dispatches    song_id            3    recommendations     song_id
fk_dispatch_user         daily_dispatches    discord_user_id    1    users               discord_user_id
fk_jb2_barrier_version   jb2_barriers         version_id         1    jb2_versions        version_id
fk_jb2_claim_event       jb2_claims          operation_id       1    jb2_events          operation_id
fk_jb2_claim_ledger      jb2_claims          ledger_id          1    credit_ledger       ledger_id
fk_jb2_claim_user        jb2_claims          discord_user_id    1    users               discord_user_id
fk_jb2_claim_version     jb2_claims          version_id         1    jb2_versions        version_id
fk_jb2_event_user        jb2_events          discord_user_id    1    users               discord_user_id
fk_jb2_event_video       jb2_events          video_id           1    jb2_videos          video_id
fk_jb2_evidence_video    jb2_evidence        video_id           1    jb2_videos          video_id
fk_jb2_exception_version jb2_exceptions      version_id         1    jb2_versions        version_id
fk_jb2_exclusion_version jb2_exclusions      version_id         1    jb2_versions        version_id
fk_jb2_legacy_song       jb2_legacy          song_id            1    songs               song_id
fk_jb2_receipt_event     jb2_receipts        operation_id       1    jb2_events          operation_id
fk_jb2_reminder_video    jb2_reminders       video_id           1    jb2_videos          video_id
fk_jb2_review_video      jb2_review_history  video_id           1    jb2_videos          video_id
fk_jb2_settlement_event  jb2_settlements     operation_id       1    jb2_events          operation_id
fk_jb2_version_root      jb2_versions        canonical_id       1    jb2_versions        version_id
fk_jb2_video_song        jb2_videos          song_id            1    songs               song_id
fk_ledger_award          credit_ledger       awarded_song_id    1    songs               song_id
fk_ledger_recommendation credit_ledger       recommendation_id  1    recommendations     recommendation_id
fk_ledger_recommendation credit_ledger       discord_user_id    2    recommendations     discord_user_id
fk_ledger_user           credit_ledger       discord_user_id    1    users               discord_user_id
fk_recommendations_song  recommendations     song_id            1    songs               song_id
fk_recommendations_user  recommendations     discord_user_id    1    users               discord_user_id
fk_submissions_song      submissions         song_id            1    songs               song_id
fk_submissions_user      submissions         discord_user_id    1    users               discord_user_id
```

Q2 判定：
- 列數 29，符合預期。
- 不重複約束名 26，符合預期。
- 26 個約束名與子表欄位，與 §15.2 的 DDL 候選清單逐一相符。
- 觀測到的規則字串為 `NO ACTION`，與 §15.2 的 DDL 推導一致。

**本節的限制（重要）**：
- 本節是比對用的觀測證據，**不是 baseline**。
- baseline 的預期來源仍是 §15.2 的 DDL 候選及其 provenance；本次觀測不能取代獨立預期來源。
- D-5 尚未核准。
- 三條查詢之間沒有 snapshot，不宣稱同一時間點的一致性（D-1 待決）。
- 不宣稱 Recovery 通過。
- 不宣稱用戶端內部的連線初始化行為已稽核。


#### P. 2026-10-23 Owner 裁決紀錄與 D-5 審閱狀態

| 編號 | 裁決 | 狀態 |
| --- | --- | --- |
| D-1 | 核准方案 B 的設計，適用於 TK-06。只保證適用的 InnoDB 資料查詢使用一致 snapshot；**不宣稱 metadata 也受同一 snapshot 保護**。實際連線與執行另待授權。 | 已裁決（設計） |
| D-2 | 核准 REC-A9 列為必要條件。 | 已裁決 |
| D-3 | 核准 23 張表的表層級 SELECT 方案，以及 REC-A10 的 23 基表／26 約束／29 欄位位置分開核對。計數是正向控制，**不單憑數量相同就宣稱 metadata 完整正確**，仍須比對名稱與結構。 | 已裁決 |
| D-4 | 核准 NO ACTION／RESTRICT 的語意等價政策，僅適用已確認的 InnoDB 範圍；保留原始字串並揭露差異；不接受其他規則。 | 已裁決 |
| D-5 | baseline 核准。預期來源仍是 §15.2 的 DDL 候選及其 provenance；§15.7 O 節的觀測只作獨立比對證據，不取代預期來源。候選綁定摘要見 §15.2A（雜湊 `c04869f7571d97b250dd6f29d48843dda5c242a032b7a5c8082e49609cad8685`）。 | **待最後審閱，未核准**；核准人與日期待填 |
| D-6 | 採用 `jukebox101_test_readonly`@`127.0.0.1`；若已存在則停止，不覆寫。 | 已裁決 |
| D-7 | **補登紀錄**：Owner 手動執行並提供 Q1～Q3 結果（目標 `127.0.0.1:3306/jukebox101_test`，帳號 `jukebox101_test_harness`@`127.0.0.1`），內容見 §15.7 O 節，不在此複製。執行時間：未知。事前授權日期與授權人：未留存，**未補造**。Owner 人工確認的是查詢結果；用戶端內部連線初始化未稽核。 | 事實已由 Owner 提供；事前授權紀錄缺漏（待 Owner 決定是否接受補登） |

**本節限制**：
- 以上裁決不授權 D-8（TK-05 建立帳號）或 D-9（TK-06 Recovery 實機）。
- D-1～D-4 的設計裁決**不代表**相關程式已更新、測試已通過或實機已驗證；程式與實機狀態分列於 §9 與 §13。
- 不代表 D-7 已批准為 Recovery 驗收。
- 不代表 TK-06 的實際執行已授權。

**TK-05 授權狀態**：未授權。最終操作與授權包見 §15.1 的步驟 0 至 5。

**D-5 核准的綁定要求**：
- 核准必須綁定**完整候選版本**，包含：每個約束的子表與父表、欄位順序（`ORDINAL_POSITION`）、複合欄位的組合、規則（UPDATE／DELETE）與 DDL 的來源檔案與行號。
- 本節的精簡清單只供審閱，**不能取代完整 baseline**。
- 完整 baseline 必須以 §15.2 的逐條表格為準。
- D-7 的觀測結果不取代預期來源。
- 唯讀複核（2026-10-23）：以既有 scratchpad 腳本對 repo 內 DDL 重算，26 約束、29 欄位位置，SHA-256 與 §15.2A 相同；`bot/database/schema.sql` 與 migration 002 相對 HEAD 無變更。O 節觀測與候選逐條一致，但觀測不是預期來源。
- D-5 仍缺：核准人、核准日期與明確核准文字（不預填）。

---

### 15.8 離線審閱包（2026-10-23，待 Owner 審閱；未核准、未執行）

**性質**：離線靜態核對與待核准文件，只整理事實與選項。本節**不是** baseline 核准，不含核准人或核准時間，不授權 TK-05、TK-06、TK-07 的任何帳號、連線或實機操作。核對方式：唯讀讀取 repo 內 DDL、manifest 與本文件，以 Python 標準函式庫比對；未匯入專案模組、未連線資料庫、未讀取 `.env`。審閱用腳本位於會話 scratchpad（`d5_candidate.py`、`d5_verify.py`），**不在版本庫內**；若要作為長期證據，需 Owner 另行決定是否納入。

#### 15.8.1 候選版本與雜湊

| 版本 | 內容 | SHA-256 | 狀態 |
| --- | --- | --- | --- |
| v1 `D5-CAND-2026-10-23-A` | 只含 DDL 解析欄位：約束名、子表、欄位與順序、父表、父欄與順序、來源檔與起始行 | `c04869f7571d97b250dd6f29d48843dda5c242a032b7a5c8082e49609cad8685` | 由 `d5_candidate.py` 重算一致。**不含規則**，是 DDL 來源摘要，不是完整候選綁定摘要，不得單獨核准。 |
| v2 `D5-CAND-2026-10-23-B`（建議） | v1 欄位＋來源範圍（多行宣告為 `起始-結束`）＋ `on_update`、`on_delete`（由省略子句推得，皆為 `NO ACTION`） | `7e5a6c094c6552f43773b5e9e1ace198b3a9983d8e11f7d5c73d5da8d751eef7` | **待 Owner 核准**；由 `d5_verify.py` 重算。內容即 §15.2 的 26 列，每列加上規則欄。 |

**v2 雜湊算法**：解析 `bot/database/schema.sql` 與 `bot/database/migrations/002_submission_economy.sql` 全部 `CONSTRAINT … FOREIGN KEY` 宣告；每筆記錄為 `{constraint, child, child_cols, parent, parent_cols, source, on_update, on_delete}`；依 `constraint` 排序；以 `json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"))` 序列化為 UTF-8；取 SHA-256。欄位順序保留 DDL 宣告順序（即 `ORDINAL_POSITION` 1..n）。

**雜湊涵蓋與不涵蓋**：涵蓋上列 DDL 欄位與規則推導值。不涵蓋 TRIGGER、CHECK、索引、實機觀測值，也不涵蓋 `REFERENTIAL_CONSTRAINTS` 的實際字串（那由 O 節比對）。DDL 任一處改動即雜湊失效。

**核對結果（2026-10-23，靜態）**：
- §15.2 的 26 列與 DDL 重解析逐欄一致（子表、欄位、順序、父表、父欄、來源）。修正前有 2 列來源只寫起始行（`fk_ledger_recommendation` 86、`fk_dispatch_owner` 111），已改為 `86-87`、`111-112`。
- 26 列每一處引用的來源行都含該約束宣告；約束名唯一。
- 26 列的規則欄皆為省略子句，DDL 中沒有任何 `ON UPDATE`／`ON DELETE`。

#### 15.8.2 數量各自指什麼

| 數字 | 計的是 | 核對來源 | 與 Manifest 的關係 |
| --- | --- | --- | --- |
| 23 | `jukebox101_test` 的 BASE TABLE，其中 19 張被 FK 參照，4 張不被任何 FK 參照（`jb2_control`、`jb2_decisions`、`jb2_migrations`、`jb2_reminder_batches`） | O 節 Q3 的 23 張表 = DDL 的 `CREATE TABLE` 名稱（逐一相符）= §15.1 的 `GRANT SELECT` 清單（23 條，逐一相符） | Manifest 不含完整 23 表清單。4 張不被 FK 參照的表不在 29 條邊內，只受 23 表 SELECT 與計數檢查。 |
| 26 | FK 約束數（每個約束一個名稱） | 兩個 DDL 來源共 26 個 `CONSTRAINT`，名稱唯一 | Manifest 不含約束名，約束名層級只依 DDL 與 O 節。 |
| 29 | FK 欄位位置數（子表欄位與父表欄位的配對）：24 個單欄約束各 1 位置，加上 `fk_ledger_recommendation` 2 位置、`fk_dispatch_owner` 3 位置 | 由 §15.2 逐列算出 | Manifest `reviewed_fk_column_edges` 共 29 條，以（子表、欄位、位置、父表、父欄）比對，**集合完全相同**。 |

#### 15.8.3 規則依據與適用範圍

- **省略子句 ⇒ `NO ACTION`**：§12.1 已逐字記錄 MySQL 8.0 官方 FK 頁的句子（「the default action is always `NO ACTION`」）。**本輪未重新抓取官方頁面**，沿用 §12.1 的既有紀錄（2026-10-23）。
- **InnoDB 下 `NO ACTION` 與 `RESTRICT` 等價**：§12.1 已記錄官方原文。
- **適用範圍**：D-4 只適用於已確認的 InnoDB 表。O 節 Q3 觀測 23 張表全部為 `InnoDB`，本 server 上成立。
- **實機字串**：O 節 Q2 的 29 列 `UPDATE_RULE`、`DELETE_RULE` 全為 `NO ACTION`，與推導一致。
- **缺口**：官方頁沒有說明省略子句時 `REFERENTIAL_CONSTRAINTS` 顯示哪一個字串。本 server 的觀測已給出答案，但這只對本 server 版本成立。換版本或換 server 需重核。

#### 15.8.4 D-7 觀測的證據邊界

| 項目 | 類別 | 說明 |
| --- | --- | --- |
| Q1：`db`、`port`、版本 8.0.46、`roles` NONE、`fk` 1、`server_uuid` 相符 | 觀測事實，可逐項比對 | 與 §15.7 預期一致 |
| Q2：29 列欄位位置、26 個約束名、規則 NO ACTION | 觀測事實，可逐項比對 | 與 v2 的 DDL 欄位及規則推導一致 |
| Q3：23 個 BASE TABLE，全部 InnoDB | 觀測事實，可逐項比對 | 與 DDL 名稱一致 |
| 用戶端是否送出 `SET NAMES`、autocommit 行為 | 已知但未驗證 | §15.7 D 節已標示；只影響三條 SELECT 的交易語意 |
| 三條 SELECT 之間沒有共同 snapshot | 限制 | 由 D-1 方案 B 解決 InnoDB 資料的一致讀；**metadata 不受該 snapshot 保護**（P 節 D-1）。Recovery 的 REC-A3 會在正式流程重新讀取 FK metadata。 |
| 執行時間、事前授權日期與授權人 | 缺失，不補造 | P 節 D-7 已標示「未留存」。這是治理紀錄的缺口，不能由後續操作補寫成事實。 |
| 使用的是 `jukebox101_test_harness`（非唯讀帳號） | 限制 | 這次查詢只送出三條 SELECT，但帳號本身有寫入權限。這不影響觀測值本身，但不能作為最小權限的證據。 |
| 由 Owner 手動執行，代理未稽核連線過程 | 限制 | §15.7 O 節已標示 |

**誰能解決**：
- 由未來正式 Recovery 流程解決：metadata 的新鮮讀取（REC-A3）、schema fingerprint（REC-A2）、已刪主鍵殘留（REC-A9）。
- 需獨立授權與另案處理：每次複驗的當次授權（REC-G1）、TK-05 唯讀帳號的可見性（D-3、D-8）。
- **仍是工程缺口，Recovery 也解決不了**：正式 Recovery 的讀取路徑目前仍使用 harness 帳號（見 15.8.6），與 §15.1 的唯讀帳號設計不一致。

#### 15.8.5 TK-05 唯讀帳號：審閱重點

- **用途**：只供 Recovery 唯讀檢查（REC-F3～F9）讀取 `jukebox101_test` 的 23 張表與 metadata。
- **目標**：`127.0.0.1:3306` 的 `jukebox101_test`，不得用於 `jukebox101` 或 Beta DB。
- **權限**：`jukebox101_test_readonly`@`127.0.0.1` 僅取得 23 張表的表層級 `SELECT`，不含全域、schema 層級、角色、`TRIGGER`、`REFERENCES`、任何寫入權限或 `GRANT OPTION`（§15.1）。
- **建立者與檢查者**：建立者為 DBA，以 Owner 指定的本機既有管理帳號執行（名稱不寫入文件；權限尚未由 DBA 證明）。檢查者為 Owner 或 DBA，依 §15.1 步驟 0、0b、3、5 核對。
- **憑證**：目前方案為 C（`IDENTIFIED BY RANDOM PASSWORD`，密碼由伺服器產生並只由 DBA 本機看見）。方案 A 與 B 為對照，未驗證。
- **停止條件**：步驟 0b 不為零列（帳號已存在）；步驟 0 或 2 出現非清單權限；步驟 3 的 `mysql.role_edges` 非零列；`account_locked` 不為 `Y`；步驟 5 的 Q1、Q2、Q3 不符。
- **撤回**：已於 §15.1「撤回候選步驟」列出 A（`ACCOUNT LOCK`）、B（逐表 `REVOKE`）、C（`DROP USER`）的候選語句、效果與驗證。全部未執行，需 Owner 另行授權。
- **不可回滾**：`CREATE USER`、`GRANT`、`ALTER USER` 是 DDL，會隱含提交。

**本節發現的文件矛盾（未擅自改寫，列給 Owner 決定）**：
1. ~~§15.1 步驟 1 的明文密碼範例~~：**已於 2026-10-23 修正**，步驟 1 改為方案 C 的 `IDENTIFIED BY RANDOM PASSWORD ACCOUNT LOCK`；舊明文寫法已移除。
2. §15.1 的管理身分：Owner 已決定使用本機既有管理帳號（§15.1 第 5 項），但目前沒有任何證據顯示該帳號具備所需權限；步驟 0 只檢查 `SHOW GRANTS`，不足以證明 GRANT OPTION 對 23 表有效。

#### 15.8.6 TK-06 ／ TK-07 的執行路徑與授權對照

**阻擋點（先看這一段）**：
- **Journal 不存在（缺檔影響）**：此檔是 run `14d8…` 事故的唯一機器可讀紀錄（`STOP_MANUAL_RECOVERY_REQUIRED`、唯一失敗為 MySQL 1451、原始 control 與 PK 基線）。`read_journal` 在連線前強制檢查路徑、檔名、大小與內容結構，缺檔即 `JOURNAL_PATH_REJECTED`／`JOURNAL_FILE_REJECTED`，因此**在任何資料庫連線之前即阻擋 TK-06 唯讀 inspect 與 Recovery write**，進而阻擋 TK-07。來源：文件 `docs/batch-b-recovery-investigation.md` A 節記載原檔當時可讀且未修改；`docs/batch-b-recovery-manifest.json` 記錄 `journal_sha256`。路徑字串最早出現於 commit `5b34314`、`54e1ac2` 的文件，檔案內容從未進入版本庫。原檔現況見本段下一句（2026-10-23 唯讀檢視結果） 為 `C:\Users\Yume\AppData\Local\Temp\jukebox101-batch-b-hst16g0d\run.json`。2026-10-23 以 WSL 路徑 `/mnt/c/...` 唯讀檢視，該資料夾**存在但為空**，`run.json` 不存在。harness 的 journal 是每次執行時 `tempfile.mkdtemp` 產生的暫存檔（`tests/batch_b_harness.py:45`），並非永久保存。Manifest 記錄的 journal sha256 為 `6a75a0bd379a08fa1f57992187b039f75f19bde1c51f7e457bc61dd0fcbfd71d`。找回後可用此值核對；找不到則 TK-06 的唯讀檢查無法依現有入口啟動。
- **讀取帳號（2026-10-23 離線已接線，實機未驗證）**：`--inspect-recovery` 現在要求 header 明確帶 `account: jukebox101_test_readonly`，缺少或為其他名稱（含 harness、application、migrator）時在讀 journal 與密碼前拒絕；連線只經 `open_recovery_readonly`，不使用 harness 連線函式；連線後以 `check_identity` 與 `verify_readonly_grants` 核對實際身分、角色（須為 `NONE`）與權限（僅 `USAGE` 與 23 表 `SELECT`，拒絕 `WITH GRANT OPTION`、全域或 schema 權限）。**帳號名稱本身不構成權限證明**，實際權限仍須於 TK-05 之後的實機核對。Recovery write（`--restore-recovery`）仍使用 harness，未改動，且不屬於 TK-06 驗收路徑。
- **TK-06 通過前不得啟動 TK-07**（§15.5 的 Q1 順序）。NOT_VERIFIED 不是通過，不能替代 TK-06。

| 階段 | 前提 | 操作者與帳號 | 精確入口 | 讀／寫 | 成功證據 | 停止條件與清理 | 需另行授權 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S0 journal 來源 | 找到原檔並通過 sha256 比對，或定義新的受控產生方式 | Owner | 本機檔案搜尋；不由代理讀取內容 | 無 | sha256 等於 `6a75a0bd…71d` | 不符則停止，不以其他檔案替代 | Owner 決定找回或另行設計 |
| S1 TK-05 帳號 | D-6 名稱、§15.1 修正矛盾（見上） | DBA，以 Owner 指定的管理帳號 | §15.1 步驟 0、0b、1、2、3、4、5 | DDL（`CREATE USER`、`GRANT`），不可 ROLLBACK | 步驟 3 有效權限等於 23 表 SELECT；步驟 5 的 Q1～Q3 符合 | 任一步不符即停止，回報當前帳號狀態；不自動撤回 | **D-8**（建立帳號）；步驟 4 的 `ALTER USER … UNLOCK` 需 Owner 另行確認 |
| S2 D-5 baseline | 已核准 baseline（或明確記錄為 fail-closed） | Owner | 文件核准（§15.8.1 的 v2） | 無 | 核准紀錄含核准人、日期、版本與雜湊 | 未核准即 TK-06 不得回報 VERIFIED | **D-5**（核准 v2） |
| S3 TK-06 唯讀 inspect | S0、S1 完成（或在 S1 之前，改用 harness 路徑，需另決定）、S2 | 操作者，以 `jukebox101_test_harness` 連線（現行程式） | `.\scripts\run-batch-b-acceptance.ps1 -RecoveryJournal <journal路徑>`（**不加** `-RestoreRecovery`）；提示時輸入 `READONLY` | 讀取 REC-F 系列 metadata 與計數 | REC-A1～A9 全部通過，REC-A10 依 D-3 分開計數 | 任一 REC 失敗即停止，不重試；輸出不得含密碼 | **D-9**（TK-06 實機）；**REC-G1**（當次授權，須指定 Session UUID、帳號、PD-1 收尾方式與 PD-2 結果） |
| S3-W 恢復寫入（不屬 TK-06 驗收） | 同 S3 且另有 Recovery 寫入核准 | 操作者 | `-RecoveryJournal … -RestoreRecovery`（寫入路徑） | **寫入**，會修改資料 | 依寫入核准 | 不可作為唯讀驗證 | 另有 Recovery 寫入核准；本文件未授權 |
| S4 TK-07 Batch B（A＋B） | S3 通過（TK-06 VERIFIED） | 操作者；需 DBA 密碼、應用程式帳號密碼、harness 密碼；輸入 `EXCLUSIVE` | `.\scripts\run-batch-b-acceptance.ps1`（無參數，即 A＋B） | 測試庫寫入與清理（`pytest tests/test_batch_b_mysql.py`，全部 24 項） | 24 項中，Batch B 矩陣的 23 項與 `reexam_settlement` 的結果分開記錄 | 單案逾時 30 秒；失敗後依 harness 的 cleanup 還原控制列，不得手動改寫 | **BB-G1～G3**（每次執行授權）；Q16 DBA 時段；`reexam_settlement` 是否納入本次執行（見下） |

**其他模式（本路徑不使用）**：`-StageBOnly`、`-FkLab`、`-RepairRecoveryFk` 各有獨立授權與獨立入口，不屬於 TK-06 或 TK-07。

**`reexam_settlement` 的範圍問題**：A＋B 會執行 `pytest tests/test_batch_b_mysql.py` 整個檔案，因此 `reexam_settlement` 也會被執行。它**不在** §2.10 已審閱的 23 項矩陣內，Owner 尚未審閱。下次實機執行前，Owner 須決定：審閱後納入，或以測試選擇排除（需另行授權並寫明）。

#### 15.8.7 需要 Owner 或 DBA 額外授權的操作

1. 核准 baseline v2（D5-CAND-2026-10-23-B）或 v1，並補核准人與日期。
2. D-8：DBA 建立 `jukebox101_test_readonly` 並授權 23 表 SELECT。
3. TK-05 步驟 4：`ALTER USER … ACCOUNT UNLOCK`。
4. 撤回語句（另案，尚未草擬）。
5. D-9 與 REC-G1：TK-06 唯讀 inspect（含 journal 來源確認）。
6. 讀取帳號的決定（見下方第一項決定）。
7. `-RestoreRecovery`：寫入，須另行核准，本文件不包含。
8. BB-G1～G3、Q16：TK-07 的 A＋B 執行，含 `reexam_settlement` 的納入決定。

#### 15.8.8 文件內其他不一致（已記錄，未改寫）

- ~~§1.11 與 REC-A10「待 Owner 核准」~~：**已於 2026-10-23 修正**為 D-2、D-3 已裁決、實機未驗證（REC-A9 與 REC-A10 表格列同步修正）。
- §15.5「下一次實機操作」段的操作一仍寫為待核准，但 §15.7 O 節與 P 節 D-7 已記錄該操作已由 Owner 手動執行。
- 舊有 §15.4 的 D-5 列已修正（見本輪）。

#### 15.8.8A 第三輪離線修正（2026-10-23，已完成，未執行實機）

- **讀取帳號接線**：`tests/batch_b_recovery.py` 新增 `RECOVERY_READONLY_USER`、`open_recovery_readonly`、`verify_readonly_grants`、`require_readonly_intent`；`inspect_connection` 改為核對專用帳號。`scripts/run-batch-b-acceptance.ps1` 的唯讀分支改為專用帳號的密碼提示與 header（`account`），寫入分支維持 harness 不變。
- **Batch B 案例範圍**：`tests/test_batch_b_mysql.py` 的 23 項既有參數化名單不含 `reexam_settlement`。新案例改為獨立函式 `test_real_batch_b_reexam_settlement`，只有設定 `JUKEBOX_BATCH_B_REEXAM_SETTLEMENT=1` 才執行；案例本體與登記保留。
- **離線測試（非 MySQL，未連線）**：`tests/test_batch_b_recovery_readonly_account.py`、`tests/test_batch_b_scope_guard.py`、`tests/test_batch_b_recovery.py`、`tests/test_batch_b_acceptance.py`、`tests/test_batch_b_recovery_write.py`：176 passed。修正前新增的 23 項失敗測試已先確認為紅燈。
- **未執行**：PowerShell 啟動器測試（`tests/test_batch_b_acceptance_launcher.ps1`）未執行，因此啟動器修改仍屬未驗證。
- **未處理**：journal 缺檔（見 15.8.6）、v2 baseline 未核准、撤回語句未執行。

#### 15.8.9 Owner 核准範本（未填，不代為核准）

```text
D-5 baseline 核准
候選版本：D5-CAND-2026-10-23-B（或 A）
SHA-256：<依版本填入完整值，不得縮寫>
核准人：<Owner 姓名或角色，由 Owner 填寫>
核准日期：<YYYY-MM-DD，由 Owner 填寫>
範圍：§15.2 的 26 列與 §15.8.2 的 29 欄位位置；不含 TRIGGER、CHECK、索引
限制：實機結果只對 127.0.0.1:3306/jukebox101_test 與 MySQL 8.0.46 有效
```

---

### 15.9 新庫驗收路徑（候選 `jukebox101_acceptance_v2`，離線設計，未授權）

**Owner 決定（2026-10-23）**：保留原事故環境；原 `jukebox101_test` 不修改、不清理、不解除事故 block；新路徑不依賴遺失的 journal，但**不代表舊事故 Recovery 通過**，也不解除其他既有阻擋。候選名稱不能假設未被使用，建立前必須先確認不存在。

**本節性質**：只定義前提、停止條件、修改位置與授權包。不建立資料庫或帳號，不執行 SQL、migration 或測試，不修改程式或 DDL。

#### 15.9.1 目標身分與白名單（取代寫死的單一目標，但不接受任意環境變數）

- **目標身分**：每個目標是程式內固定的設定列，欄位為：目標名稱、host `127.0.0.1`、port `3306`、`server_uuid`（與已核准 UUID 相同）、目前角色必須為 `NONE`、用途（`frozen_incident` 或 `acceptance_v2`）、各自的帳號名稱。
- **選取方式**：入口以**程式內的列舉值**選取目標，由 header 的目標 ID 比對白名單；不讀取任意環境變數決定目標。
- **原目標**：`jukebox101_test` 的用途為 `frozen_incident`，所有寫入、清理與 migration 路徑一律拒絕，唯讀路徑也不得自動使用，除非另有 Owner 授權。
- **新目標**：`jukebox101_acceptance_v2` 的用途為 `acceptance_v2`，只有在授權包全部完成後才可用。
- **連線後核對**：`DATABASE()`、`CURRENT_USER()`、`@@port`、`@@server_uuid`、`CURRENT_ROLE()`、`@@foreign_key_checks` 必須與目標列完全相同，不符即停止。
- **拒絕條件**：目標 ID 不在白名單、名稱與帳號不屬於同一列、用途不符、或名稱等於 Beta DB 名稱（P-L3，名稱待 Owner 提供）。

#### 15.9.2 需要修改的位置（候選，未實作）

| 類別 | 位置 | 目前狀態 | 需要的變更 |
| --- | --- | --- | --- |
| 生產程式（需獨立審閱） | `bot/database/test_profiles.py:21` | 寫死 `jukebox101_test` | 改為查目標白名單；帳號仍需與 profile 完全一致 |
| 生產程式（需獨立審閱） | `bot/database/submission_migration.py:76` | 寫死 migration 身分 | 改為查白名單；只有 `acceptance_v2` 的 migrator 身分可通過 |
| 生產程式（需獨立審閱） | `bot/database/test_privileges.py:63` | 只接受 `jukebox101_test` | 以白名單 schema 名稱集合替代 |
| 測試基礎 | `tests/mysql_safety.py:16,30,41` | 固定測試庫名稱 | 新增 `acceptance_v2` 目標，保留 `JUKEBOX_DISPOSABLE_DATABASE` 的顯式比對 |
| Batch B 新路徑 | `tests/batch_b_harness_contract.py:75,85` | `EXCLUSIVE` 與 destination 寫死 | 改為目標列；`EXCLUSIVE` 必須等於目標名稱 |
| Batch B 新路徑 | `tests/batch_b_harness_database.py:22,47,72,99,287` | 身分與 schema 範圍寫死 | 改為目標列；帳號白名單含 v2 帳號 |
| Batch B 新路徑 | `tests/batch_b_acceptance.py:48,72` | 設定與 exclusive 環境變數寫死 | 目標由 header 指定；刪除寫死的 `os.environ` 設定 |
| Batch B 新路徑 | `tests/batch_b_acceptance_setup.py:85,137,151,157,201–204` | 身分、FK 範圍與 grants 寫死 | 改為目標列；grants 期望值由目標產生 |
| Batch B 新路徑 | `tests/batch_b_preflight.py:67,95,137,140` | schema 寫死 | 改為目標列 |
| 啟動器 | `scripts/run-batch-b-acceptance.ps1` | header 無目標欄位 | 新增目標 ID 欄位；提示仍為遮罩密碼 |
| 啟動器 | `scripts/run-batch-b-app-preflight.ps1:34` | `TEST_DB_NAME` 寫死 | 改為目標 ID 對應的名稱，或只允許原目標 |
| **保持不動（frozen）** | `tests/batch_b_recovery.py`、`batch_b_recovery_write.py`、`batch_b_recovery_fk.py`、`batch_b_readonly_recovery.py`、`batch_b_fk_window.py`、`ORIGINAL_SCHEMA` 常數 | 綁定原事故環境 | **不改**；新增測試確認它們的目標為 `frozen_incident`，不可被指向 v2 |

**測試要求**：新增非 MySQL 的離線測試，驗證白名單、用途、身分不符、Beta 名稱拒絕、`frozen_incident` 目標不能寫入等，並以 AST 確認沒有新增寫死的第二目標。

#### 15.9.3 Schema fingerprint 與預期來源

- **既有算法**：`tests/batch_b_harness_database.py` 的 `schema_fingerprints` 讀取 `SHOW CREATE TABLE`，移除 `AUTO_INCREMENT=N`，依表名排序後取 SHA-256。
- **原指紋 `5f201eab…c62a8`**：屬於 `frozen_incident`，**不得用於 v2**。
- **v2 的預期來源必須獨立於新庫觀測**：
  - 第一層（離線、現在可做）：以 §15.2 的 26 個約束、23 基表名稱、引擎 InnoDB 與 utf8mb4，以及 `schema.sql` 與 `002_submission_economy.sql` 的內容雜湊作為審閱基準。
  - 第二層（需授權）：由一個獨立的參考建置產生 `SHOW CREATE` 正規化定義與其 SHA-256。參考建置須與 v2 分開，且結果由 Owner 核准為預期值。新庫的觀測只能與它比對，不能反過來成為基準。
- **比對失敗即停止**：不做任何自動修復，不以觀測值改寫預期值。
- **風險**：`SHOW CREATE` 的外鍵輸出可能受資料庫名稱影響，因此參考建置必須在實際的 v2 名稱下完成，或另行證明名稱不影響輸出。這一點尚未驗證。

#### 15.9.4 帳號與權限（新庫使用獨立帳號）

- **候選帳號**（皆為 `@127.0.0.1`，名稱待 Owner 確認）：`jukebox101_v2_app`、`jukebox101_v2_harness`、`jukebox101_v2_migrator`、`jukebox101_v2_readonly`。
- **理由**：不共用原目標的帳號。若 v2 帳號的權限誤指向原庫，原庫的權限不會被使用。
- **權限**：各帳號只授予 `jukebox101_acceptance_v2` 的表層級權限，沿用 `docs/batch-b-test-harness-grants.sql` 與 §15.1 的權限模板，只把 schema 名稱換為 v2。不授予全域權限、GRANT OPTION、DROP 或 TRIGGER。
- **建立**：所有帳號以 `ACCOUNT LOCK` 與 `RANDOM PASSWORD` 建立，密碼由 DBA 本機取得，不進入對話或文件。
- **驗證**：每個帳號 `SHOW GRANTS` 必須與目標列的期望集合完全相同；`mysql.role_edges` 必須零列。

#### 15.9.5 Migration 與初始資料

- **初始狀態**：空的 `jukebox101_acceptance_v2`，無資料表。
- **Schema 來源**：`bot/database/schema.sql` 與 `bot/database/migrations/002_submission_economy.sql`，經過審閱的 SHA-256。
- **執行方式（建議）**：以現有 migration runner 執行，使 `jb2_migrations` 的 checksum 紀錄與程式一致。這需要 §15.9.2 的白名單修改，屬於生產程式變更，需另行審閱。
- **替代方式**：由 DBA 逐句執行審閱過的 SQL。但 `jb2_migrations` 紀錄須與 runner 的期望一致，否則視為 migration 不合格。
- **初始資料**：只允許 `jb2_control` 的單列（`schema_version` 必須與 runner 寫入值一致，計數器為 0，三個開關為 OFF）以及 migration 紀錄。其餘業務表必須為零列。
- **禁止**：不匯入原庫資料、不複製 `jukebox101_test` 的任何列、不使用 dump。

#### 15.9.6 必要順序（每一步都是獨立授權）

| 順序 | 內容 | 成功條件 | 停止條件 |
| --- | --- | --- | --- |
| G0 | Owner 決定：名稱、帳號名稱、參考建置是否採用、Q1 順序修改、舊 block 只適用原庫的書面確認 | 文件核准紀錄 | 任一項未決即不往下 |
| G1 | 唯讀確認 `jukebox101_acceptance_v2` 不存在（DBA 執行 `information_schema.SCHEMATA`） | 零列 | 已存在即停止，不重用 |
| G2 | DBA 建立空庫與四個帳號（DDL，需授權） | 帳號鎖定、權限與期望集合一致 | 權限超出模板 |
| G3 | 執行 schema 與 migration 002（需授權） | `jb2_migrations` 與 runner 期望一致；表數 23；InnoDB | 任何 checksum 或 DDL 錯誤即停止，不自動修復 |
| G4 | 新庫與預期 baseline 比對（唯讀） | 指紋、FK 規則、information_schema 與 grants 與預期一致 | 任一不符即停止 |
| G5 | 初始資料唯讀檢查 | 只有 control 與 migration 紀錄；業務表零列 | 有業務列即停止 |
| G6 | 清理能力驗證：第一個 Batch B 案例本身包含寫入與清理，失敗時不做任何 SQL 補救 | 清理後的基線與 G5 相同 | 清理失敗即 session block，**只限 v2** |
| G7 | 正常流程（sequential）：persistence、priority、rejected、owner_review | 各案例 PASS；帳務 `assert_accounting` 成立 | 任一 FAIL 即停止 |
| G8 | 並行：concurrent_accept、concurrent_retry、concurrent_reward、lock_timeout | 同版本只發一次；鎖逾時回傳 1205 | 任一 FAIL 即停止 |
| G9 | 故障：rollback 8 項、unknown commit 4 項 | 全部回滾；ACK 遺失後以相同 key 重播不重複 | 任一 FAIL 即停止 |
| G10 | 領域：conflict、remix_merge、reminder_seed、reminder_read | 狀態與審核結果符合預期 | 任一 FAIL 即停止 |
| G11 | `reexam_settlement`（單獨 opt-in，範圍獨立） | 見 15.9.7 | 同上 |

#### 15.9.7 新案例與原 23 項的範圍

- **原 23 項**：10 個 workflow（G7 的 4 項、G8 的 4 項、G10 的 2 項）、8 個 rollback 與 4 個 unknown commit（G9）、1 個 reminder（G10，由 reminder_seed 與 reminder_read 組成）。這是已審閱的範圍，不得擴大。
- **`reexam_settlement`**：不在 23 項內，需要自己的 opt-in，且與既有 MySQL 與 harness 授權共同成立（JUKEBOX_RUN_BATCH_B_MYSQL、JUKEBOX_RUN_BATCH_B_HARNESS、`JUKEBOX_BATCH_B_EXCLUSIVE`、`JUKEBOX_BATCH_B_REEXAM_SETTLEMENT`）。它是循序整合案例，**不是並行證明**。
- **注意**：目前測試模組的 `pytestmark` 要求 RUN 與 HARNESS 兩個開關，新案例另外要求其專屬開關，三者是合取。

#### 15.9.8 全域停止條件

- 目標身分、用途或白名單不符。
- 指紋、FK 規則或權限與預期不符。
- 出現任何 frozen_incident 目標的連線或寫入。
- 清理失敗、基線不符或 session block。
- 單案逾時超過 30 秒（既有 harness 限制）。
- 非注入的 MySQL 錯誤、未知的 commit 結果，或任何 1451 類錯誤。
- 任何需要手動 SQL 補救的情況：一律停止，不補救。

#### 15.9.9 與舊事故的邊界

- 新庫的任何 PASS **不代表**舊事故 Recovery 通過，也不解除舊 block。
- 不得繼承、覆寫或引用舊 journal、manifest 的 `journal_sha256`、或原 fingerprint `5f201eab…`。
- 歷史 `RECOVERY_SUCCESS` 的標示保留為「2026-10-16 使用者回報的已驗收結果」，**未獨立複驗**，journal 已遺失，因此無法由本次工作證明。它不被改寫為「從未成功」。
- 原庫 `jukebox101_test` 不修改、不清理、不作為新路徑的 fallback。

#### 15.9.10 DBA 候選操作（未執行，待授權）

建庫前檢查（唯讀）：

```sql
SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = 'jukebox101_acceptance_v2';
-- Must return zero rows. If any row is returned, stop and do not reuse it.
```

建庫與帳號（需 D-A、D-B 授權，名稱待確認）：

```sql
CREATE DATABASE jukebox101_acceptance_v2 CHARACTER SET utf8mb4;
CREATE USER 'jukebox101_v2_app'@'127.0.0.1' IDENTIFIED BY RANDOM PASSWORD ACCOUNT LOCK;
CREATE USER 'jukebox101_v2_harness'@'127.0.0.1' IDENTIFIED BY RANDOM PASSWORD ACCOUNT LOCK;
CREATE USER 'jukebox101_v2_migrator'@'127.0.0.1' IDENTIFIED BY RANDOM PASSWORD ACCOUNT LOCK;
CREATE USER 'jukebox101_v2_readonly'@'127.0.0.1' IDENTIFIED BY RANDOM PASSWORD ACCOUNT LOCK;
-- Grants: follow the reviewed templates in docs/batch-b-test-harness-grants.sql and section 15.1,
-- with the schema name replaced. Each grant list is reviewed separately in D-B. No GRANT OPTION.
```

注意：`CREATE DATABASE` 與 `CREATE USER` 都是 DDL，會隱含提交，**不可 ROLLBACK**。

#### 15.9.11 授權包（需分別核准）

| 代號 | 內容 | 不包含 |
| --- | --- | --- |
| D-A | 名稱、帳號名稱、參考建置是否採用、Q1 順序修改、舊 block 的書面範圍 | 任何 DDL |
| D-B | DBA 建空庫與四個帳號（15.9.10） | migration、資料寫入 |
| D-C | 生產程式白名單修改（15.9.2）與 migration（G3） | 實機測試 |
| D-D | 參考建置與 v2 預期指紋核准（G4） | 實機寫入 |
| D-E | Batch B 實機執行，每次指定 Session UUID、目標 ID、案例範圍與停止條件 | Recovery、frozen 目標 |
| D-F | `reexam_settlement` 的獨立 opt-in（在 D-E 之外另行核准） | 其他案例的擴大 |

#### 15.9.12 文件與待辦

- 本節是 §15 的一部分，不另建競爭中的規格來源。
- 待辦（不阻擋 journal 查找，也不阻擋本節）：PowerShell 啟動器修改尚未驗證；新案例的 opt-in 需與既有授權共同成立（見 15.9.7）。
- v2 baseline（`D5-CAND-2026-10-23-B`，SHA-256 `7e5a6c09…eef7`）仍未核准，且它屬於原庫的 FK 候選，不直接等同於 v2 的 schema 預期。

---

## 16. 完整產品規格草案（2026-10-23，待 Owner 核准）

**本章性質**：Q1 至 Q37 的產品裁決整理為單一草案。本章不代表整份產品規格已完成最終核准；無法由裁決確定者標為**待決**，不自行補答案。本章不改寫第 1 至 15 節，也不改變 TK-01 至 TK-13 的內容、編號與狀態。

### 16.0 狀態欄位與修正紀錄

**每一需求三個獨立狀態**（Q1 原則）：
- **需求狀態**：已裁決（註明 Q 編號）／待決（註明類別）。
- **實作狀態**：未開始／離線設計中／離線實作與測試通過（僅離線）／已接線未實機。
- **驗證狀態**：Unverified／離線通過（不等於實機）／實機待授權／實機通過（目前無任何項目）。

**修正紀錄（相對於 2026-10-23 的摘要草稿）**：
1. **48 小時首次回覆**：適用**交付案件與審核補件兩類**（Q33、Q25）。7 日查核與 14 日補償決定**只適用 uncertain 交付案件**，不延伸到審核補件（Q33）。
2. **每日額度**：明確未交付且退款完成後釋放額度，**但不自動重送**；uncertain 即使之後獲善意補償，仍占用額度（Q30）。
3. **Pending 與不發點的定位**：「投稿初始為 Pending、本次 Beta 不發點」是**Beta 範圍限制**，不是永久禁止。完整產品仍依後續審核與結算推進（Q4、Q6、Q36）。
4. **未來事項的區分**：uncertain 的善意補償（Q28）與錄音版本跨 Video ID 的獎勵去重（Q12、Q13）是**核心必要功能**，不延後。延後的是撤銷、追回、負餘額與作品層級識別等（見第 16.7 節）。
5. **技術閘門**：Recovery 與 Batch B 的實機驗收是上線的技術條件，不屬於產品規則，但**仍會阻擋上線**（見第 16.6 節）。

### 16.1 完整產品流程（階段規格）

| 階段 | 規格要點 | 裁決來源 | 需求狀態 | 實作狀態 | 驗證狀態 |
| --- | --- | --- | --- | --- | --- |
| S1 投稿 | 只接受 YouTube 影片網址；Video ID 作為投稿識別與去重依據；寫入 Pending；Beta 不發點、不交換 | Q3、Q4、Q12 | 已裁決 | 已接線（Beta 路徑） | 離線通過；Batch B 實機待 |
| S2 歌曲資格 | 完整影片時長 60 至 720 秒（含）為必要條件，不等於歌曲核准；不得以網址時間戳記或使用者填寫的長度代替；無可靠證據保持 Pending | Q15 | 已裁決（必要條件） | 時長判定離線實作；完整歌曲判定與可靠證據取得**未完成**（見 TK-22） | 離線通過；實機未驗證 |
| S3 身分與版本 | 獎勵單位為「經確認的錄音版本」；Video ID 只用於投稿識別；版本歸屬依可追溯證據與 Owner 確認；標題、標籤、頻道名稱或不同 Video ID 單獨不足以證明版本；無法確認保持 UNRESOLVED，矛盾為 CONFLICT | Q12、Q16、Q17、Q18 | 已裁決（原則）；證據來源的取得與審核流程**待實作**（TK-23） | 版本模型離線實作（2B.2）；核對流程未完成 | 離線通過；實機未驗證 |
| S4 審核 | 單一 Owner；approve 與 reject 附理由；版本衝突時禁止核准（任何寫入前拒絕）；拒絕可執行，且不清除衝突、不發點；本次不開放解除衝突；改變狀態的操作需理由、證據引用與預覽→確認 | Q9、Q35 | 已裁決 | Repository 方法離線存在；**Discord 指令未註冊**（TK-14） | 未實機 |
| S5 重審 | 以新證據重審；保留原決策與版本歷史；已核准未發點的投稿進入重審時立即讓出原受理優先權（不再以舊位置阻擋同版本後續投稿）；重審通過的核准與新排序事件同一操作原子完成，新事件排在當時隊尾；不恢復原受理優先權；重審通過不保證發點；不推翻他人已完成的獎勵 | Q10、Q14 | 已裁決 | **未實作**（重審入口 TK-14、排序事件 TK-15） | 未實作 |
| S6 首次發點 | 固定 1 Nickel；僅限符合首次獎勵資格並完成審核與結算的錄音版本；同版本一次；Owner 審核通過只是必要條件；自動發點需平台政策閘門與另行核准；不建立人工 Grant 繞過；證據衝突不自動追回 | Q6、Q7、Q8、Q11、Q36 | 已裁決；政策閘門為**待決**（PB-POL） | 離線結算程式存在；`settlement_enabled` 預設 0 | Batch B 實機待；政策未決 |
| S7 排序與排除 | 以系統可信的受理序號排序，不使用使用者提供的時間；較早投稿仍 Pending 或身分未決時，較晚投稿不得因先完成審核而獲獎；超過 24 小時不失去優先權；較早投稿確定不合格或被拒絕後，才推進下一位；不因最早 raw pending 就必然得獎 | Q7 | 已裁決 | 排序離線實作（候選排序） | 離線通過；實機待 |
| S8 交換扣點 | 成功保留推薦時扣 1 Nickel；推薦、扣點與 ledger 在同一原子交易完成；餘額不足或無候選時不扣點、不建立成功推薦；同一操作的技術重試不得重複扣點或建立第二筆推薦 | Q19、Q37 | 已裁決 | 離線已實作（TK-16 第一片，假資料） | 離線通過；實機待 |
| S9 候選條件 | 歌曲通過資格、審核與版本身分，無未解衝突，且該獎勵版本已有合法完成的首次結算；候選不得是本人已投稿的錄音版本；不得是本人已收到、目前已保留或 uncertain 的版本；明確失敗且退款完成的保留不算已收到；不得以換 Video ID 繞過；暫不依喜好、熱度或付費排序 | Q20、Q23 | 已裁決（Q20 為新增產品裁決，不回寫 Q7、Q12） | 離線已實作（TK-16 第一片，假資料） | 離線通過；實機待 |
| S10 選曲 | 均勻隨機；抽選單位為符合條件的錄音版本，不以 Video ID 或投稿筆數抽選；版本選定後再選擇符合交付條件的 YouTube 連結；抽選與保留處理並行競爭，不保證不同使用者收到不同歌曲 | Q23 | 已裁決 | 離線已實作（TK-16 第一片，假資料） | 離線通過；實機待 |
| S11 交付 | DM 交付；Discord 接受 DM 建立請求並回傳訊息識別即視為交付成功，不宣稱已讀；DM 成功後即使 ephemeral 狀態回覆失敗，也不退款、不重送；DM 明確未送出依 S12 退款；結果未知依 S13 uncertain；ephemeral 回覆不附歌曲連結，不自動改為公開頻道 | Q22 | 已裁決 | 離線已實作（TK-17 第一片，假 DM adapter）；Discord 實際 DM 與 ephemeral 未接線 | 離線通過；實機待 |
| S12 退款 | 只有能證明「未成功送出」的失敗才可退款；以新增 ledger 補償紀錄處理，與推薦失敗狀態一致提交；每筆扣款最多退款一次；退款完成後釋放該使用者的保留，歌曲可再被選中，但不自動重送 | Q21 | 已裁決 | 離線第一片已實作（TK-18：明確未送出的退款、一次性、釋放保留）；對外 `/exchange` 未開放，真實扣點仍不可啟用 | 離線通過；實機待 |
| S13 uncertain | 逾時、連線中斷、程序重啟或未知提交結果，不直接視為明確失敗；標記 uncertain，保留關聯，不自動退款、不盲目重送；立即保存操作碼，告知使用者扣點與交付尚待確認，不要求重新交換同一操作 | Q21、Q25 | 已裁決 | 離線已實作（TK-17 交付側、TK-18 第一片的不退款規則）；查核與補償（14 日、7 日案件）未實作 | 離線通過；查核待實作 |
| S14 查核期限 | 自動建立待查案件，不必等使用者申訴；建案後 48 小時內首次回覆；7 個日曆日內完成查核或提供明確進度；以 Asia/Taipei 計算；確認已交付則保留扣點，確認未交付則依 S12 退款；第 7 日仍無法判定，轉入逾期案件，不自動退款、不重送 | Q25、Q33 | 已裁決（48 小時適用交付案件與審核補件；7 日與 14 日只適用 uncertain 交付案件） | **未實作**（TK-18） | 未實作 |
| S15 善意補償 | 每次原始扣款所對應的案件，最多一次、1 Nickel；僅 Owner 可核准；須記錄理由、證據與獨立補償類型；第 7 日起符合申請條件，不自動發放；Owner 須於第 14 日前做有理由的決定並通知使用者；證據仍不足時可結案為「交付無法確認」，不得捏造成功或失敗 | Q28、Q25 | 已裁決 | **未實作**；新 ledger 補償類型與資料設計由 TK-18 交付 | 未實作 |
| S16 補償上限 | 同一次扣款的退款與善意補償，累計最多返還 1 Nickel，並行操作不得重複返還；補償後若確認未送達，可更新查核結論，但不得再退款；確認已交付也不自動追回善意補償 | Q28 | 已裁決 | 退款部分離線已實作（一次性）；善意補償未實作（TK-18） | 退款離線通過；補償待實作 |
| S17 交付狀態與財務分離 | 交付狀態（已交付、未交付、uncertain、無法確認）與財務處理（扣點、退款、善意補償）分開記錄；善意補償不代表交付成功或失敗，不自動解除 uncertain 的歌曲保留 | Q28 | 已裁決 | 部分實作：交付狀態與退款狀態分開記錄（`status` 與 `released_at`／refund ledger）；無 uncertain 查核與補償 | 部分離線通過 |
| S18 申訴與補件 | 統一私人問題回報入口，附操作碼並產生案件編號；類別一為交付、扣點、退款與補償問題；類別二為審核結果的異議與新證據補件；入口驗證操作屬於本人；同一操作的同類未結案件優先追加資料，不重複建案；不得要求密碼或 Token；補件只建立案件，不自動重開審核、不改狀態或排序、不保證發點；Owner 判定有新證據後，才依 S5 進入正式重審；介面名稱待決（PB-NAME） | Q25、Q33 | 已裁決（名稱待決） | **未實作**（TK-19） | 未實作 |
| S19 個人歷史 | 完整顯示本人的投稿、審核、獎勵、交換、交付、退款與善意補償；支援分類與分頁；區分投稿、交付與財務狀態，不把「已補償」顯示為「已交付」；相關紀錄可關聯查看，提供操作碼與使用者可理解的原因；已成功交付的歌曲可重新查看連結，不另扣點；不顯示他人紀錄、內部敏感證據或管理資訊 | Q32 | 已裁決 | **未實作**（TK-20）；Beta 的 `/history` 維持投稿與審核狀態（Q3） | 未實作 |
| S20 Owner 介面 | 指定私人 Guild 中的 Owner 專用 Discord 指令；以實際 `interaction.user.id` 與已設定的 Owner ID 比對，隱藏指令或 Discord 角色不能替代後端驗證；查看與操作回覆採 ephemeral，不公開案件或證據；只讀查詢與一般案件留言不需二段確認 | Q34、Q35 | 已裁決 | **未實作**（TK-14） | 未實作 |
| S21 預覽→確認 | 預覽顯示對象、目前狀態、預計變更與財務影響；確認綁定 Owner、目標、操作內容與目前 revision，具有效期限，只能使用一次；確認時重新檢查權限與狀態，資料已變更則拒絕並要求重新預覽；重複點擊或技術重試不得重複執行；稽核紀錄只新增，更正透過新紀錄，不覆寫歷史 | Q35 | 已裁決 | 未實作（TK-14，為所有高風險操作共用） | 未實作 |
| S22 每日派送 | 使用者明確開啟、隨時可關；每首扣 1 Nickel，派送前清楚告知；每人每個 Asia/Taipei 日曆日最多一次自動開始交付；手動 `/exchange` 不計入；當日排程時間已到、仍開啟、尚未保留、送出或 uncertain 者，可在同一日曆日內補執行；已開始但結果未知者不得當成漏跑再送；前一天錯過者次日不補；無餘額或無候選時不扣點、不建立成功交付；派送時刻由 Owner 透過部署設定指定單一固定時刻（Asia/Taipei），缺失或格式錯誤不啟動 | Q26、Q29、Q30、Q31 | 已裁決；**派送時刻數值待 Owner 指定**（P-4） | 未實作（TK-21） | 未實作 |
| S23 每日額度 | 保留成功時占用當日額度；明確未交付且退款完成後釋放；uncertain 仍占用，即使獲善意補償也不自動釋放；釋放不代表自動重新派送；第一階段每日派送每人每天最多自動開始一次交付，失敗退款後不因下一輪排程再嘗試；每日重試另訂規則 | Q30 | 已裁決 | 未實作（TK-21） | 未實作 |
| S24 連結完整性 | 成功交付後不保證永久可用，不自動退款或換歌，可接受使用者回報並標記；交付前已知失效者不得照常交付並扣點，應排除，無其他候選則不扣點；未驗證的連結不得宣稱「即時確認可播放」；外部檢查方式、檢查時效與地區限制待決（PB-LNK） | Q27 | 已裁決（交付規則）；檢查方式**待決** | 未實作 | 未實作 |
| S25 冷啟動 | 不設全域最低歌曲數；依使用者當下的可用候選判斷；有至少一個合格候選且餘額足夠，才可原子保留並扣點；無候選回覆「目前沒有符合條件的歌曲」，不扣點；不為冷啟動自動加入 Pending、歷史未核准歌曲或人工補發點數 | Q37 | 已裁決 | 離線已實作（TK-16 第一片，假資料） | 離線通過；實機待 |

### 16.2 本次 Beta 邊界與版本分層

| 版本 | 範圍（需求） | 上線條件 | 狀態 |
| --- | --- | --- | --- |
| **投稿查詢 Beta**（本次） | `/share`（YouTube）、本人 `/history`（投稿與審核狀態）、`/balance`（顯示既有餘額，不保證為 0）；不發點、不交換、不每日派送；新投稿不由本次測試流程推進審核或結算，使用者可清楚看到「尚待審核」狀態 | 規格第 2、3、4、13 節的技術與啟用條件（Batch B 實機、Recovery 複驗、Ledger 唯讀對帳、Owner 當次啟用授權） | 技術規格已存在；實機未驗證 |
| **核心交換版本**（完整產品第一個上線版本） | S1 至 S25 中除 S22、S23 外的全部流程；含首次發點、交換扣點、DM 交付、退款、uncertain、善意補償、申訴、個人歷史 | 整條流程端到端驗收通過（TK-24）；PB-POL 平台政策閘門通過；帳務一致；權限與防重驗證；必要實機驗收；Owner 當次啟用授權（PB-ACC）；**不建立人工 Grant 繞過**；**不假設舊餘額作為冷啟動點數來源** | 需求已裁決；實作未開始 |
| **每日派送** | S22、S23 | 核心交換版本驗收後獨立開放；派送時刻設定完成（P-4）；不阻擋核心交換 | 需求已裁決；時刻待決 |
| **雲端部署** | 依 TK-13（第 14 節）獨立評估與驗收 | 不以「部署完成」代表產品功能已驗收 | 既有，TK-13 不變 |

### 16.3 需求與裁決來源索引

| 需求群組 | 裁決來源 | 歷史或現行來源（只作參考，不覆寫） |
| --- | --- | --- |
| 點數來源與首次發點 | Q6、Q7、Q8、Q11 | `CLAUDE.md` 第 9 節 §2（歷史：一點、成功持久化後發點）；`reward_settlement.py` 固定 +1；`ROADMAP.md` 第 18 行（政策未解） |
| 身分與版本 | Q12、Q13、Q16、Q17、Q18 | `README.md` 第 27–29 行（版本映射需可信證據）；`ROADMAP.md` 第 43 行（Phase 1 Video ID 例外，僅歷史範圍） |
| 資格 | Q15 | `ROADMAP.md` 第 31 行（60–720 秒，並非歌曲核准） |
| 重審與排序 | Q7、Q10、Q14 | `README.md` 第 31 行（受理序號排序）；`phase2a-closeout.md` 第 54–55 行（未決問題，已由 Q7、Q10 裁決） |
| 交換與扣點 | Q19、Q20、Q23、Q37 | `CLAUDE.md` 第 9 節 §2（歷史：只有成功保留才消耗點數、隨機選取） |
| 交付與失敗 | Q21、Q22、Q27 | `README.md` 第 197–206 行（派送與恢復設計，標示尚未實作） |
| uncertain 與善意補償 | Q21、Q25、Q28、Q30 | `README.md` 第 197–206 行（uncertain 不盲目重送或退款）；`schema.sql` 的 status 列舉 |
| 每日派送 | Q26、Q29、Q30、Q31 | `CLAUDE.md` 第 9 節 §2（歷史：每日一次、有點數與候選才派送） |
| 申訴與補件 | Q25、Q33 | 你的 Q25、Q33 裁決 |
| 歷史查詢 | Q3、Q32 | `docs/private-beta-mvp-specification.md` 第 5 行（Beta 的 `/history` 核准紀錄） |
| Owner 介面與二段確認 | Q9、Q34、Q35 | `review_persistence.py` 第 50 行（`require_owner`） |
| 上線閉環 | Q36 | `ROADMAP.md` 第 18 行；`phase2b2-batch-b-acceptance.md` 第 122 行 |

**歷史差異**：`CLAUDE.md` 第 9 節 §2 寫「在成功持久化之後發點」，與 Q2 的裁決（審核通過且符合條件後才發點）不同。新產品規格以 Q2、Q6、Q7 為準，歷史文字保留不改。

### 16.4 候選 ticket TK-14 至 TK-24

**阻擋編號規則**：產品阻擋使用 **PB-** 前綴，與既有 Recovery 編號（B-1 至 B-6、X-1 至 X-9）區分。

**阻擋類別**：
- **未決政策**：需要 Owner 或外部政策決定。
- **待實作**：需要程式或設計交付。
- **待驗證**：需要離線或實機驗證。
- **待執行授權**：需要當次明確授權。

**離線開發依賴與功能啟用條件分開**：政策未解不代表離線設計與測試不能開始。每張 ticket 先列離線開發依賴，再列功能啟用條件。

**產品阻擋項目（PB）**：

| 編號 | 內容 | 類別 |
| --- | --- | --- |
| PB-POL | 平台政策閘門：YouTube API 與 Nickel 獎勵政策適用性的確認，以及自動發點的另行核准 | 未決政策 |
| PB-EVD | 可靠證據與完整歌曲判定的取得與審核流程（不要求先建立封閉來源清單，但需可執行的取得與審核流程） | 待實作、未決政策（外部來源部分） |
| PB-VER | 版本歸屬的證據取得、核對與查重流程 | 待實作 |
| PB-ACC | Owner 當次啟用授權與實機測試帳號的核准 | 待執行授權 |
| PB-TECH | Recovery 與 Batch B 的技術閘門（引用既有 TK-06、TK-07、TK-09、TK-10、TK-11，不重新命名） | 待驗證、待執行授權 |
| PB-NAME | 申訴入口的介面名稱（不阻擋功能，只阻擋命名） | 未決政策（命名） |
| PB-LNK | 外部連結檢查的方式、時效與地區限制 | 未決政策 |
| PB-P4 | 每日派送的固定時刻 | 待 Owner 指定數值 |

**Owner 裁決（2026-10-23，已寫入對應條文）**：(1) 版本為 CONFLICT 時允許查看與拒絕，禁止核准；拒絕不得清除版本衝突，不因此發點；本次不開放解除衝突。(2) 已核准但尚未發點的投稿進入重審時立即讓出原受理優先權，不再以原位置阻擋同版本後續投稿；重審通過後以系統產生的新排序事件排到當時隊尾；原受理序號與決策歷史保留，不推翻他人已完成的獎勵；讓出位置不代表自動獲獎，其他資格與防重規則仍須成立。

**TK-14 Owner 審核介面、重審入口與預覽確認**
- 需求：S4、S5（重審入口）、S20、S21；Q9、Q10、Q34、Q35。
- 交付物：Owner 專用指令（查看待審、必要證據、目前 revision、衝突原因；approve、reject 附理由）；重審入口（新證據與保留原決策）；預覽→確認的共用元件，綁定 Owner、目標、內容與 revision，一次性，有效期限；稽核只增不改。
- 離線開發依賴：無（可用既有 repository 與假資料）。
- 功能啟用條件：PB-EVD 與 PB-VER 的資料來源可供顯示（只影響啟用，不影響離線開發）；PB-ACC。
- 驗收條件（離線）：非 Owner 一律拒絕，且在任何資料變更前拒絕；過期或已變更的確認被拒絕；重複點擊只執行一次；版本衝突時核准在任何寫入前被拒絕、拒絕可執行且衝突保留；所有操作寫入稽核。
- 阻擋類別：待實作（正式入口未接線；離線部分見下）。
- 離線進度（2026-10-23，TK-14 S21 切片）：預覽時保存「身分、canonical 版本與證據集合」的一致快照（`bot/decision_snapshot.py`、`bot/database/review_projection.py`）；核准、拒絕、重審與版本確認於鎖定交易內比對快照，不一致即拒絕且不寫入（`bot/database/review_persistence.py`）；重播先於比對，保留原結果；重審預覽以同一次讀取計算 `projected_identity`，確認結果與預覽一致。Owner 端顯示中文的「請重新預覽」說明（`bot/discord_owner_review.py`）。測試：`tests/test_decision_snapshot.py`。Discord transport 合成入口：`tests/owner_transport_acceptance.py`，測試：`tests/test_owner_transport_acceptance.py`。以上皆為離線與假資料驗證，未連線資料庫或 Discord，未通過實機驗收。
- A 類人工測試（2026-10-23，部分通過，合成記憶體案件，不連 MySQL，不發點）：`update-guild` 同步通過完整契約核對；`/owner list`、`show`、取消、拒絕確認與重複確認、核准確認、A-2 非 Owner 的 list 拒絕、預覽逾時的 UI 結果，以及 DM 中指令不可選用，已人工觀察。未驗收：版本確認與重審、實際分頁、其他 Guild 的拒絕、@ 提及防護的專門測試、服務層權杖過期檢查（逾時未觸發服務層）、DM 後端拒絕；監聽程序是否已停止未確認。核准結果不代表真實歌曲資格或版本證據已驗證。**TK-14 仍為部分完成，不是完成；B 類 MySQL 持久化未驗證；正式功能未啟用。** 詳見 `docs/owner-transport-acceptance.md` 的「人工測試結果」。
- TK-14 指令選單可見性（2026-10-23，離線）：`owner` 群組改為預設權限 0（一般成員預設看不到），後端 Owner 身分檢查不變；已以審閱的預期變更更新遠端 `owner` 為預設權限 0；兩個測試帳號（Owner 與一般成員）的選單可見性已人工通過，非 Owner 管理員未測試；先前契約比對對遠端 `default_member_permissions`、`dm_permission`、`nsfw` 有盲點，已修正。Owner 可操作已人工通過。TK-14 仍為部分完成。
- TK-14 版本確認與同版本重審（2026-10-23，合成記憶體案件，單一輪次）：版本確認、核准、同版本正式重審的預覽與確認均依預期完成，版本維持已確認、未產生衝突；證據與修訂變化符合預期。重審後核准被阻擋（UI 回覆英文原文，show 核對狀態不變；TK-15 未實作，阻擋需於排序事件實作後重新評估）。重審後拒絕（同一輪）：預覽與 show 核對通過，已拒絕、修訂 4、版本與證據不變（未驗證內部 priority_released、後續投稿解鎖、排序或發點）。獨立 CONFLICT 輪次（同日，重啟後）：兩版確認的預覽與 show 核對通過，版本形成衝突、審核狀態與修訂不變。未驗收：衝突後核准或拒絕的行為（S4 解讀待確認）、解除衝突（程式未開放）、Ledger、發點與資料庫凍結、其他版本類別、持久化、Ledger 與點數。詳見 `docs/owner-transport-acceptance.md`。TK-14 仍為部分完成。

**TK-15 首次獎勵發點與重審排序事件**
- 需求：S5 的排序事件部分、S6、S7；Q6、Q7、Q14；Owner 裁決（2026-10-23）：已核准未發點的投稿進入重審時立即讓出原受理優先權；重審通過的核准與新排序事件為同一操作。
- 交付物：發點結算接線（僅在政策閘門開啟時可啟用）；重審通過時建立新的排序事件，使用系統產生、可穩定排序的序號，排在當時隊尾；原受理序號與歷史不變；重審通過後仍需符合首次資格；不推翻他人已完成的獎勵，不重複發放。
- 離線開發依賴：TK-14 的審核狀態資料模型（可用假資料先開發）。
- 功能啟用條件：PB-POL、PB-ACC、PB-TECH（Batch B 實機）。
- 驗收條件（離線）：同版本只發一次；較早 Pending 未決時，較晚投稿不獲獎；重審排序位於隊尾；並行結算不重複；政策閘門關閉時拒絕發點。
- 離線第一片（2026-10-23，Owner 裁決後）：重審釋放已核准未發點項目的原優先權（`bot/review.py` `reopen_for_reexamination`）；重審通過的核准與系統產生的新排序事件於同一交易原子完成（`bot/database/review_persistence.py` `_with_ordering_sequence`，序號來自 `jb2_control.accepted_sequence` 計數器），排到建立當時的隊尾；候選以 `ReviewSnapshot.ordering_sequence` 取得優先順位（`bot/database/persisted_snapshot.py`）。不需遷移：排序序號存於既有審核 JSON 契約，舊資料以預設值解碼。已發點項目不套用讓位。測試：`tests/test_owner_ruling_conflict_ordering.py`。未驗收：發點接線、政策閘門開啟後的發點、同版本並行結算、實機。TK-15 仍為待實作（第一片離線完成）。
- 唯讀核對（2026-10-23，TK-15 第一片，未修改程式、未執行測試）：
  1. 排序序號：新序號 = 交易內 `jb2_control.accepted_sequence` + 1，與受理序號共用同一計數器（受理見 `bot/database/submission_store.py` 約第 42–46 行），在 `submission_transaction` 的鎖內配置，因此嚴格大於所有既有受理與先前的排序事件。配置、重審通過的核准、決策與稽核均在同一 `apply_review` 交易內提交（`bot/database/review_persistence.py`）。**缺陷 D1**：`apply_review` 配置序號後未更新交易內的 `control` 字典，隨後的 `current_decisions(cursor, control)` 以過期的 `accepted_sequence` 建立 `through_sequence`（`bot/database/persisted_snapshot.py` 第 63 行），使剛通過的候選 `accepted_sequence` 大於 `through_sequence`，`snapshot_is_complete` 判為不完整（`bot/submission_snapshot.py` 第 76 行），候選因 `INCOMPLETE_SNAPSHOT` 被標為等待（`bot/reward_candidates.py` 第 182 行）。結果：`persist_decisions` 寫入的 `reward_status` 可能過期，而 `/history` 讀取的正是該欄位（`bot/database/submission_repository.py` 第 90 行）。發點路徑 `settle_reward` 以新交易的 `control` 重算，因此本核對未發現錯誤發點；但顯示狀態可能不正確。現有測試以新 `control` 讀取決策，未覆蓋此路徑。
  2. 相容性：舊資料的審核 JSON 解碼時缺少 `ordering_sequence` 會使用預設值（`bot/database/submission_codec.py` 的 `_unpack` 以欄位名稱建構），已有測試覆蓋。已儲存的決策摘要與 `jb2_review_history` 不被改寫；`review_command_digest`、`reexamination_digest`、`version_confirmation_digest` 的欄位未變，舊摘要仍可重播。**相容性缺口**：本次之前已處於重審中、且已核准未發點的舊資料，其 `priority_released` 仍為 False，`reward_candidates.py` 第 232 行只讀取該旗標，因此這些舊項目仍會阻擋後續同版本投稿，直到被核准或拒絕。目前沒有遷移或推導規則處理此情況；離線測試資料不存在此狀態，正式資料庫尚未執行遷移，因此屬於啟用前必須處理的缺口。
  3. 已發點判斷：`_is_awarded` 讀取 `jb2_claims`（`bot/database/review_persistence.py`），該表的 `operation_id` 為 UNIQUE（`bot/database/migrations/002_submission_economy.sql` 第 61–69 行），發點寫入位於 `reward_settlement.py` 第 79 行，同一交易。真實 MySQL 的查詢形狀與候選載入使用的 LEFT JOIN 一致。本核對**未在真實資料庫執行**；fake 的同形查詢分支已於本次補上。
  4. 讓位與等待：已釋放的候選不阻擋他人（`bot/reward_candidates.py` 第 232–233 行）；重審中的候選本身仍為 WAITING_VALIDATION。重審通過後，以新序號參與排序；所有早於新序號且仍未決的投稿仍依 S7 阻擋它，這是隊尾位置的預期結果，不是缺陷。
  5. 指令契約：本輪未修改任何斜線指令的名稱、選項或描述；`owner` 群組與五個全域指令維持上一輪的內容。預覽說明與錯誤文字為 Owner 可見的文字，不屬於指令契約，人工測試不需要重新同步。
  6. 潛在風險（不可達）：`reexamination_pending` 的核准不檢查 `approval_source` 是否為 OWNER。產品程式碼目前不使用 `ApprovalSource.AUTO`（僅見於審核模組與測試），因此不可達；若未來接入自動核准，重審通過應限定為 Owner。
  7. 未驗證：真實 MySQL 的鎖與交易行為、D1 的實機影響、舊資料遷移、顯示的新排序位置（目前 `show` 不顯示排序序號）。
  8. 另一個 Owner 必須知道的事實：`/owner show` 不顯示 `ordering_sequence`，因此單一假案件的畫面無法證明新序號或相對隊尾順序。
- 已修正並離線驗證（2026-10-23，TK-15 第一片缺陷處理；未執行資料庫、未實機）：
  - **D1 已修正**：`bot/database/review_persistence.py` 的 `_with_ordering_sequence` 現在同時回傳與資料庫一致的交易內 `control`（序號與 revision 已遞增）；後續 `current_decisions` 以正確的 `through_sequence` 計算，重審通過後立即持久化的候選狀態與重新計算結果一致，不再因新序號誤判為 `INCOMPLETE_SNAPSHOT`。快照完整性檢查（`bot/submission_snapshot.py`）未放寬。測試：`tests/test_ordering_consistency.py::test_persisted_decisions_after_a_reexamination_pass_match_a_fresh_recomputation`。
  - **舊資料相容（以既有權威資料推導，不遷移）**：`bot/review.py` 的 `effective_priority_released`：重審中、`priority_released=False`、且未發點者視為已讓出。依據（逐分支）：`reopen_for_reexamination` 的釋放規則為：已拒絕者設為 True（舊版與新版相同）；已核准且未發點者設為 True（新版；舊版留下 False）；已核准且已發點者保留原值（新版與舊版皆為 False）。因此重審中且為 False 的項目，只會是「已核准」的重審，其中未發點者來自舊版，已發點者來自新版或舊版。推導規則以 `jb2_claims` 排除已發點者，所以只有未發點的舊資料會被視為已讓出；已發點者保留既有獎勵與優先語意。推導只作用於候選投影（`bot/database/persisted_snapshot.py`），不改寫歷史決策、審核 JSON 或既有獎勵。測試：`tests/test_ordering_consistency.py`（舊格式資料重現與修正、已發點保持、規則純測試）。
  - **排序證據（離線）**：新排序事件大於所有既有受理與先前排序事件（`tests/test_ordering_consistency.py::test_an_ordering_event_sits_after_every_intake_and_every_earlier_ordering_event`）；重審通過後立即讀回的候選序號等於審核中的序號（`test_the_passed_item_reads_back_its_new_position_and_the_snapshot_is_complete`）；重試不重複配置事件（`test_a_replayed_pass_does_not_allocate_another_ordering_event`）；不相關案件保持受理順序，同版本只發一次（`test_unrelated_submissions_keep_their_intake_order_and_the_same_version_is_awarded_once`）。
  - **已發點判斷**：`_is_awarded` 讀取 `jb2_claims`（發點與認領同一交易寫入）；fake 已補上 `operation_id` 查詢分支。真實 MySQL 的查詢形狀與候選載入的 LEFT JOIN 一致，**未在真實資料庫執行**。
  - **指令契約**：本輪未修改斜線指令的名稱、選項或描述，人工重驗不需要重新同步。
  - **實機未驗證**：真實 MySQL 的鎖與交易、舊資料是否存在於正式資料庫（本次不表示存在或不存在）、Owner 可見的排序位置（`show` 不顯示序號）、重審通過後的發點（仍需政策與結算開關）。
  - **仍未處理**：`ApprovalSource.AUTO` 可達性（目前產品不使用）；英文錯誤文字；Owner 介面的排序位置顯示。
- 提示文字修正（2026-10-23，文字，不改邏輯）：Owner 可見的重審預覽說明改為「重審會保留原決策與歷史；重審通過核准時，建立新的排序事件並排到當時隊尾，不恢復原優先權，也不保證發點。」；重審通過的核准預覽另附「確認時產生新排序事件」。已移除舊的「重審期間不得直接核准」與「等待 TK-15 排序事件」說明（`bot/owner_review.py`、`bot/review.py`、`bot/database/review_persistence.py` 的註解與文件字串）。測試：`tests/test_reexamination_wording.py`；針對性離線測試 126 passed。
- MySQL 案例（2026-10-23，只寫入，未執行）：`reexam_settlement`（`tests/batch_b_mysql_scenarios.py`）已登記於 `tests/test_batch_b_mysql.py` 參數化。實際狀態：`pytest --collect-only` 收集為該檔 24 項之一；無 `RUN_SWITCH` 與 `HARNESS_SWITCH` 時 skip（模組層只讀環境變數，不讀 `.env`、不連線）。全套離線結果中的 skip 含此案例。
- 性質：重審排序與結算的**循序整合**案例，**不是併發案例**。斷言：op2 先獲獎；op1 重審通過後不得獲獎；op2 重播仍為 AWARDED 且不新增獎勵（`assert_accounting(rows, 1)` 核對 ledger、餘額、claims、settlements；claims 只含 op2）；op1 的新序號大於 op2。
- 未納入：§2.10 已審閱的 23 項矩陣，Owner 尚未審閱；清理沿用 `MySQLHarness.cleanup` 的 run key 逐列刪除與控制列還原，失敗處理沿用 worker 逾時與寫入鎖。未獲實機執行授權。
- 併發缺口：`concurrent_reward` 已涵蓋同版本競爭。其中第二次 `settle(op1)` 是**原結果重播**；未重複發點須由 claim、ledger 與餘額斷言確認，不由回傳值證明。併發注入未補（fake 以 RLock 序列化，不能證明資料庫列鎖）。
- 阻擋類別：待實作；啟用時未決政策。

**TK-16 交換保留與扣點（原子交易）**
- 需求：S8、S9、S10 的保留部分、S25；Q19、Q20、Q23、Q37。
- 交付物：候選判斷（資格、審核、版本身分、無衝突、已有合法首次結算、本人投稿版本排除、已收到或保留中或 uncertain 版本排除）；均勻隨機、以錄音版本為抽選單位；推薦、扣點與 ledger 在同一交易；無候選或餘額不足不扣點；並行競爭時，候選失效不得扣點卻無保留；技術重試不得重複扣點。
- 離線開發依賴：TK-15 的「已完成首次結算」資料狀態（可用假資料）；TK-17 的狀態機定義（可先定義介面）。
- 功能啟用條件：PB-POL（因為點數來源依賴發點）、PB-ACC、PB-TECH。
- 驗收條件（離線）：以合成資料驗證排除規則；換 Video ID 不繞過；並行保留不超扣；失敗時沒有半筆保留或扣點；餘額不足與無候選的回覆正確。
- 衝突後處理（PB-REV-C，依 17.7.1）：CONFLICT 發生後，該版本不再被新保留或新交換選中；已保留但未送出的紀錄，需能被 TK-17 取消。
- 離線第一片（2026-10-23，假資料，未連線、未執行 MySQL、未實際扣點或發點）：
  - 候選純函式 `bot/exchange_selection.py`：只接受已完成首次結算的版本（`jb2_claims`）、連結已驗證（`songs.resolution_status='verified'` 且有 `youtube_url`）者；排除本人投稿的版本、本人已有任何推薦紀錄的歌曲；以錄音版本為抽選單位，均勻隨機，抽選注入 RNG 以便重現。
  - 交易實作 `bot/database/exchange_reservation.py`：在既有 writer gate 內（`submission_transaction`）先查重試回放，再以 `lock_reconciled_account` 鎖定並核對餘額，餘額不足即回覆 `INSUFFICIENT_CREDITS` 且不寫入；候選為空即回覆 `NO_CANDIDATE` 且不扣點；成功時在同一交易插入 `recommendations`（`reserved`）、`credit_ledger`（`exchange_debit`，`-1`）並更新 `users.credit_balance`。
  - 重試：同一 request key 以 ledger 的唯一 `idempotency_key` 回放原保留，不重複扣點、不建立第二筆推薦。
  - 衝突：版本只要有 `CONFLICT` 的候選決策，就不再被新交換選中，且既有 claim 與 ledger 不被撤銷（`_conflict_versions` 重用 `current_decisions`）。
  - 介面：`SubmissionRepository.exchange(discord_user_id, request_key, rng)`；尚未接到 Discord 指令。
  - 離線測試：`tests/test_exchange_selection.py`（7 項）、`tests/test_exchange_reservation.py`（9 項）。突變檢查（暫時停用單一排除或回放規則）已確認各規則都會被測試抓到。
- 限制（未處理，不宣稱完成）：
  - 最終規則（TK-18 第一片起）：成功交付、送出中、uncertain 與未退款的 failed 仍排除；退款完成（`released_at` 已設定）的明確失敗不再占用該歌曲（S12）。TK-16 先前的「failed 一律排除」只是退款前的保守暫態，已移除。
  - 並行保留的正確性只以 writer gate 序列化驗證（fake 的 RLock），不證明 InnoDB 列鎖或 MySQL 實機行為。
  - 交付連結取自該版本 claim 所對應影片的歌曲列；未驗證連結時不交付。
  - 每次交換會讀取兩次 snapshot，尚未優化。
  - 使用者回覆文字（餘額不足、無候選）未在 Spec 中定義具體文字；S25 只規定無候選時回覆「目前沒有符合條件的歌曲」，留給 TK-17 的回覆層。
  - 控制列 `settlement_enabled` 在已記載的基線中為 OFF（見 Manifest 的 control 紀錄）。在 PB-POL 核准開啟結算前，實際環境沒有已結算版本，候選會是空的；這是既定限制，不是本切片的缺陷。

**TK-17 DM 交付與交付狀態機**
- 需求：S10 的交付部分、S11；Q22、Q23（連結選擇）、Q27（交付前已知失效排除）。
- 離線第一片（2026-10-23，假 DM adapter，未接 Discord、未實際發訊）：
  - 流程：`exchange_and_deliver`（`bot/exchange_delivery.py`）先呼叫 `repository.exchange`（保留與扣點同一交易），再 `deliver_recommendation`；同步資料庫工作仍經 repository 的交易邊界，DM 呼叫不在任何交易或鎖內。
  - 狀態機：先在交易中把 `reserved` 轉為 `sending` 並提交，之後才呼叫 DM；DM 結果記錄為 `delivered`（含訊息識別與 `delivered_at`）、`failed`（明確未送出，待 TK-18 退款）、`uncertain`（逾時、斷線、任何例外、或程序中斷後的 `sending`）。只有 `DmKind.NOT_SENT` 可證明未送出。
  - 不重送：`sending`、`uncertain`、`delivered`、`failed` 的重入都不再呼叫 DM，回覆 `IN_PROGRESS` 或原狀態；重啟時 `recover_interrupted_deliveries(now)` 只把租約（10 分鐘）已過期的 `sending` 轉為 `uncertain`，新鮮的 claim 不被觸碰。
  - 回覆：ephemeral 文案不含連結；ephemeral 失敗只記錄 `reply_failed`，不改變交付狀態、不退款、不重送。
  - 文案：無候選「目前沒有符合條件的歌曲」；餘額不足「點數不足，交換一首歌曲需要 1 Nickel。」；其餘文案依本 Spec 狀態定義自行完成（見 `bot/exchange_delivery.py`）。
  - 資料結構：沿用既有 `recommendations`（`status` enum、`discord_message_id`、`delivered_at`）與 `delivered`／`delivered_at` 的 CHECK；本輪**不需要遷移**。
  - 測試：`tests/test_exchange_delivery.py`，13 項（成功、明確未送出、uncertain、例外、重啟恢復、重試、重複進入、交付後回覆失敗、無候選與餘額不足、連結不外洩、送出前提交 sending、送出時不持有 writer gate）。突變檢查：把未知結果當成未送出、讓回覆失敗外拋、停用重啟恢復，各自都會被抓到。
- 限制：
  - 沒有接到 Discord 的 DM 或 ephemeral，也沒有啟用 `/exchange`（目前仍是待開放回覆）。
  - 送出後、記錄前若資料庫失敗，該筆會停在 `sending`，租約過期後轉為 `uncertain`；這是保守結果，不代表已送出或未送出。晚到的結果由 claim token 條件保護，不覆寫較新的終態。
  - 並行只以 fake 的 RLock 模擬，不證明 InnoDB 行為。
  - 退款、查核與善意補償（TK-18）未實作；因此真實扣點交換仍不可啟用。
- 交付物：DM 交付狀態（reserved、sending、delivered、failed、uncertain）；送出前持久化 sending；Discord 接受 DM 建立請求並回傳訊息識別即為 delivered；ephemeral 狀態回覆失敗不影響交付結論；DM 明確未送出標為可退款狀態，交給 TK-18；未知結果標為 uncertain；ephemeral 不附連結。
- 離線開發依賴：TK-16 的保留資料模型。
- 功能啟用條件：PB-ACC（Discord 實機）、PB-TECH。
- 驗收條件（離線）：每個狀態轉換的合法性；重啟於 sending 時標為 uncertain，不自動重送；ephemeral 失敗不退款；交付前已知失效排除（僅依已知紀錄，不宣稱即時檢查）。
- 衝突後處理（PB-REV-C，依 17.7.1）：保留未送出者取消後不會被 worker 發出；送出結果未知者標為 uncertain，不重送、不退點；衝突處理與送出之間以狀態條件或鎖防止「先退點、後送出」。
- 阻擋類別：待實作；啟用時待驗證（實機）。

**TK-18 退款、uncertain、查核與善意補償（含資料設計）**
- 需求：S12、S13、S14、S15、S16、S17；Q21、Q25、Q28、Q30。
- 離線第一片（2026-10-23，假資料，未連線、未執行 MySQL、未實際退點）：
  - 退款條件：只有 `status='failed'`（明確未送出，由 DM adapter 的 `NOT_SENT` 記錄）且尚未釋放的保留可退款；`sending`、`uncertain`、`delivered` 與結果未知一律回覆 `not_refundable`，不寫入任何東西。DM 成功但 ephemeral 回覆失敗，不會觸發退款。
  - 權威來源：退款金額與收件人只取自該保留唯一的原始扣款 ledger（`exchange_debit`）；呼叫者只能指定保留編號。原扣款缺失、收件人不符、重複扣款或已有退款紀錄時拋出 `RefundContradiction`，不補帳、不猜測修復。
  - 一次性：退款在同一交易中新增 `delivery_refund` ledger（唯一鍵 `(recommendation_id, reason)` 為第二道保護）、加回餘額、設定 `released_at`；重複呼叫回傳 `already_released`，不再加點。
  - 釋放：`released_at` 已設定的保留不再占用該使用者的歌曲，因此同一歌曲可被再次選中（S12）；推薦與財務歷史保留，不刪除。
  - 交易：退款的三步（ledger、餘額、釋放）任一失敗即整體回滾（測試以 fake 的 SQL 故障注入驗證）。
  - 失敗重試：退款若因暫時性錯誤失敗，保留仍為 `failed` 且未釋放，之後可再次呼叫，只會退一次。
  - 流程：`exchange_and_deliver` 在記錄 `failed` 後立即嘗試退款；退款結果為 `refunded`、`already_released`、`pending` 或 `contradiction`，回覆文案分開；`contradiction` 只顯示人工查核，不退款。
  - 測試：`tests/test_exchange_refund.py`（10 項）。突變檢查：讓 uncertain 也退款，會被抓到。
- 限制（未處理，不宣稱完成）：
  - 善意補償（S15）、7 日與 14 日查核、申訴、uncertain 的查核案件均未實作。
  - `recommendations` 的 `(discord_user_id, song_id)` 唯一鍵必須改為釋放感知才能真正重新選中同一歌曲。版本化候選 `bot/database/migrations/003_exchange_delivery.sql` 已準備（新增 `claim_token`、`claimed_at`、`released_at`、生成欄位 `active_slot` 與新唯一鍵、兩個 CHECK），**未執行、未註冊到 runner**，需 Owner 核准、DDL 授權與 MySQL 8.0 離線驗證。`schema.sql` 也未更新，因為它會改變已審閱的 schema 指紋基準（v2 預期值需一併重核）。在此之前，fake 無法證明唯一鍵行為。
  - 單一程序假設與租約：`sending` 只在超過 10 分鐘（`DELIVERY_LEASE`）後才會被啟動時的恢復轉為 `uncertain`；新鮮的 claim 不會被觸碰。晚到的結果由 `claim_token` 條件保護，不會覆寫較新的終態。多程序同時執行尚未設計，目前部署範圍為單一程序。
  - 未確認身分的殘留風險：本人投稿的 Video ID 會排除候選，即使版本未知；但同一錄音以不同 Video ID、且身分未確認時，系統不會猜測其為同一錄音，因此仍可能漏掉。這是保守處理的已知缺口，列為 Owner 待決，不由本切片自行擴大規則。
  - 非同步邊界：資料庫操作經 `asyncio.to_thread`；DM 與 ephemeral 以 `await` 留在事件迴圈上，coroutine 不會被丟進 worker。`asyncio.CancelledError` 不會被捕捉，會留下 `sending`，由租約處理。
  - 例外分類：任何一般例外都視為 `uncertain`，不視為未送出；只有 adapter 明確回傳 `NOT_SENT` 才退款。日誌只記錄例外類別名稱。
  - 對外 `/exchange` 仍是待開放回覆，未接線；真實扣點交換在 TK-18 退款路徑與 migration 003 核准前不可啟用。

- 第二片（2026-10-23，假資料與 mock，未連 Discord、未實際發訊、未動真實點數）：
  - 合成交換路由：`MusicService(..., synthetic_exchange=True)` 只由明確選取的驗收路徑設定；預設為 False，`/exchange` 維持原閘門（`exchange()` 回覆「未扣點」）。`CommandHandlers.exchange` 只在 `synthetic_exchange_enabled` 為真時 defer 並呼叫 `synthetic_exchange`，沒有新增指令、沒有平行 dispatcher。
  - 合成標示：所有合成 DM 與 ephemeral 回覆都加上「【合成測試】」前綴；服務層以包裝器完成，不需改動流程本身。
  - Discord 邊界（`bot/discord_exchange.py`）：`DiscordDirectMessageSender` 以 `await user.send(...)` 送出，回傳結果如下：查找收件人失敗（GET）→ `NOT_SENT`（未建立任何訊息）；403 且錯誤碼 50007（Discord 官方的「無法傳訊給此使用者」）→ `NOT_SENT`；其餘 403、5xx、429、逾時、連線中斷與任何其他例外 → `UNKNOWN`。核對依據：本機 discord.py 2.7.1 原始碼，其 HTTP 層對 500／502／504／524 會無條件重試，因此一次 `send` 呼叫可能實際送出多次；這是不能把 5xx 當成未送出的理由。
  - 退款條件的補強：退款只在 `NOT_SENT` 記錄後觸發；退款交易失敗時，保留仍為 `failed` 且未釋放，`pending_refund_ids` 會在之後的請求中重試，每筆仍只退一次（`released_at` 與 ledger 唯一鍵雙重保護）。
  - 回覆文案：退款已提交才說「已退回 1 點」；退款失敗為「退款待處理，請稍後查詢」；契約矛盾為「需人工查核，尚未退款」；uncertain 顯示操作碼並說明不會自動重送。
  - 查詢投影（不暴露秘密）：`user_exchange_results(user)` 只回傳該使用者的操作碼、交付狀態與財務狀態，不含 claim token 或連結；`owner_exchange_summary()` 只回傳各狀態計數與待退款數，不含任何使用者識別。
  - 遷移後的查核：`tests/test_exchange_migration_mysql.py` 是 opt-in、唯讀（只查 information_schema），需要既有的 RUN 與 HARNESS 開關再加上專屬的 `JUKEBOX_BATCH_B_EXCHANGE_MIGRATION_CHECK=1`；本輪未執行、未收集。
- 租約、超時與恢復的關係（第三輪核對）：
  - 原本只在啟動時恢復的缺口：若程序啟動時某筆 `sending` 尚未滿 10 分鐘，之後就沒有機制再發現它。已修正：每次 `exchange` 請求開始時都會執行 `recover_interrupted_deliveries(now)`，因此過期的 `sending` 會在下一個請求中轉為 `uncertain`；仍保留啟動時的呼叫。
  - 常數關係：`DM_TIMEOUT_SECONDS = 20`，`DELIVERY_LEASE = 10 分鐘`。單一程序內，任何仍在進行的 DM 呼叫都在超時內結束（`asyncio.wait_for`），因此不會被誤判為遺失；`test_the_dm_bound_is_far_below_the_recovery_lease` 固定這個不等式。若未來改為多程序，必須重新設計租約（例如由持有者定期續租），不能只調整時間常數。
  - 未覆寫的結果：租約過期後才抵達的成功結果，由 `claim_token` 條件保護，不覆寫已轉為 `uncertain` 的終態；DM 實際已送出但記錄失敗時，該列會停在 `sending`，租約過期後轉為 `uncertain`，這是保守結果。
  - 殘留：若沒有任何新請求，過期的 `sending` 會停留到下一次請求或重啟；它不會自動退款或重送，但 Owner 的計數會顯示其存在。
- 遷移 003 與新建環境的順序（第三輪核對，未執行）：
  - 目前已批准的順序：`schema.sql` → `002_submission_economy.sql`（由 runner 註冊，含 checksum）。
  - 若 003 獲批准：`schema.sql` → `002` → `003`，003 必須加入 runner 的註冊清單（生產程式變更，需另行審閱），否則新建環境與程式不一致。
  - `schema.sql` 本輪不更動：它是已審閱 schema 指紋的基準，改動會使 v2 預期值失效。因此 003 獲批准前，新建環境不含 `claim_token`、`claimed_at`、`released_at`、`active_slot` 欄位；在此之前，生產的 `/exchange` 閘門保證這些欄位不會被寫入。
  - 003 的舊資料相容性：既有 `reserved`、`delivered`、`failed`、`uncertain` 列都沒有 `sending`，`released_at` 皆為 NULL，`active_slot` 皆為 1，因此既有唯一性不變；CHECK 不會被既有資料違反。唯一鍵的釋放感知只在釋放後才改變行為。
  - 引擎行為未驗證：生成欄位、唯一鍵與 CHECK 的實際行為只能在 MySQL 8.0.16 以上的核准環境驗證；fake 不執行這些約束，因此 fake 測試不能證明它們。


- 交付物：退款補償紀錄（以新 ledger 紀錄，每筆扣款最多一次）；善意補償的新 ledger 類型與資料設計（本 ticket 交付，不由外部前置）；uncertain 案件的建案與期限計時（48 小時首次回覆，7 日查核，14 日補償決定，皆以 Asia/Taipei 計算）；退款與補償累計上限 1 Nickel；釋放保留與額度的規則（Q30）；交付狀態與財務狀態分開記錄。
- 離線開發依賴：TK-17 的狀態機；本 ticket 自身的資料設計。
- 功能啟用條件：PB-TECH；PB-ACC。（uncertain 案件由 TK-18 自動建案，不依賴 TK-19；使用者申訴入口由 TK-19 另行啟用。）
- 驗收條件（離線）：退款一次性；並行退款與補償不超過 1 Nickel；第 7 日前不得申請補償；第 14 日未決定的處理；確認已交付不追回補償；「交付無法確認」可結案且不捏造結論。
- 衝突後處理（PB-REV-C，依 17.7.1）：未送出者一次性退點並記錄原因；每筆扣款最多退款一次；已送達者保留派送與 Ledger 歷史，不自動追回，該版本不再用於新交換。
- 阻擋類別：待實作；啟用時待驗證。

**TK-19 私人問題回報與申訴入口**
- 需求：S18；Q25、Q33。
- 交付物：統一私人入口，分兩類；驗證操作屬於本人；案件編號與狀態查詢；同一操作的同類未結案件追加資料；不得要求秘密；補件只建案，不重開審核；審核補件與 uncertain 案件的首次回覆期限；介面名稱待 PB-NAME。
- 離線開發依賴：TK-18 的案件模型（建案由 TK-18 觸發；TK-19 負責使用者入口與查詢，兩者不互相依賴實作）。
- 功能啟用條件：PB-ACC、PB-TECH。
- 驗收條件（離線）：非本人無法查詢或追加；案件查詢不洩漏他人資料；同類未結案件不重複建案；補件不改變審核狀態或排序。
- 阻擋類別：待實作；名稱待決（不阻擋功能）。

**TK-20 個人歷史完整版**
- 需求：S19；Q32。
- 交付物：本人投稿、審核、獎勵、交換、交付、退款與善意補償的分類與分頁；區分投稿、交付與財務狀態；相關紀錄關聯與操作碼；已交付連結可重看，不扣點；不顯示他人或內部證據。
- 離線開發依賴：TK-16、TK-17、TK-18 的讀取模型。
- 功能啟用條件：PB-ACC、PB-TECH。
- 驗收條件（離線）：不同狀態不互相混淆（「已補償」不顯示為「已交付」）；查看連結不扣點；分頁與分類正確；無他人資料。
- 阻擋類別：待實作。

**TK-21 每日派送**
- 需求：S22、S23；Q26、Q29、Q30、Q31。
- 交付物：opt-in 與關閉；每日排程（由 Owner 指定的固定時刻，缺失則不啟動）；每人每個 Asia/Taipei 日曆日最多一次自動開始交付；同日內可補執行尚未開始的派送；已開始未知者不補；額度占用與釋放；不自動重送。
- 離線開發依賴：TK-16、TK-17、TK-18 的介面；可在核心交換驗收前離線開發。
- 功能啟用條件：TK-24 核心交換驗收通過（Q26、Q36）；P-4 派送時刻設定；PB-ACC。
- 驗收條件（離線）：同一日曆日最多一次；停機後同日補執行、跨日不補；已開始未知者不補；額度釋放不自動重送；設定缺失不啟動。
- 阻擋類別：待 Owner 指定數值（P-4）；待實作。

**TK-22 歌曲資格與完整歌曲判定的證據流程**
- 需求：S2、S3 的資格部分；Q15、Q18。
- 交付物：可靠證據的記錄模型（來源、取得時間、理由、判定者）；完整歌曲判定的流程（時長、完整性、內容判斷與 Owner 審核）；取得與審核流程的文件化；不得以網址時間戳記或使用者長度取代；無可靠證據保持 Pending。
- 離線開發依賴：無（記錄模型與規則可離線設計與測試）。
- 功能啟用條件：PB-POL（任何外部來源）；PB-EVD 的流程核准。
- 驗收條件（離線）：時長邊界 60 與 720 秒含；無證據保持 Pending；使用者填寫長度不被採用。
- 阻擋類別：待實作；外部來源部分為未決政策。

**TK-23 版本歸屬核對與查重流程**
- 需求：S3、版本分類原則；Q12、Q16、Q17、Q18。
- 交付物：版本分類核對（MV 與 Audio 同一錄音；Remix 需可追溯證據；Live 與翻唱需獨立演出證據；重錄需證據；remaster 同一原始演出錄音；剪輯、片段、延長版與速度或變調處理，依 Q17 不因此另獎）；證據來源類型的取得與審核流程（不要求封閉清單，但須有可執行流程）；版本查重：同一錄音版本只發一次；矛盾為 CONFLICT；Owner 核對紀錄。
- 離線開發依賴：無。
- 功能啟用條件：PB-VER 流程核准；PB-POL（若用平台 metadata 作輔助）。
- 驗收條件（離線）：標題、標籤、頻道或不同 Video ID 單獨不被採信；證據矛盾進入 CONFLICT；同版本查重正確。
- 阻擋類別：待實作。

**TK-24 核心交換端到端驗收**
- 需求：Q36 的核心交換版本全部流程。
- 交付物：端到端驗收計畫與結果：正常路徑（投稿→資格→審核→發點→交換→交付→歷史）；失敗路徑（DM 明確失敗→退款一次→釋放保留；uncertain→保存操作碼→建案→查核結論）；並行路徑（並行保留、並行退款與補償、重複點擊）；重啟路徑（sending 中重啟→uncertain）；申訴路徑；完整個人歷史。
- 離線開發依賴：TK-14 至 TK-23 的離線實作。
- 功能啟用條件：PB-POL、PB-ACC、PB-TECH（Batch B 實機與帳務一致性）。
- 驗收條件：上述所有路徑離線通過，並在授權的實機環境中逐項驗證；未驗證項目標為 Unverified。
- 阻擋類別：待驗證、待執行授權。

**依賴圖**（只列依賴，無循環）：
TK-22 與 TK-23 → TK-14（審核需要證據與版本核對的資料）→ TK-15 → TK-16 → TK-17 → TK-18 → TK-19 與 TK-20（兩者不互相依賴）→ TK-24 → TK-21。TK-21 依 TK-24 驗收後才可啟用，但離線開發可在 TK-17 之後進行。TK-13 與 TK-01 至 TK-12 不因本圖改變。

**對照表：需求與 ticket**

| 需求 | 負責 ticket |
| --- | --- |
| 審核介面、重審入口、預覽→確認 | TK-14 |
| 重審排序事件、首次發點 | TK-15 |
| 版本查重與版本分類核對 | TK-23 |
| 原子扣點、無候選與餘額判斷、並行保留 | TK-16 |
| 防重交付、DM 交付狀態機 | TK-17 |
| 退款、補償上限、uncertain 查核、補償資料設計 | TK-18 |
| 申訴入口、案件與歷史隔離 | TK-19（案件建立由 TK-18 觸發） |
| 個人歷史完整版 | TK-20 |
| 每日派送與額度 | TK-21 |
| 可靠證據與完整歌曲判定 | TK-22 |
| 端到端的正常、失敗、並行、重啟與 uncertain 路徑 | TK-24 |

### 16.5 集中待決表（真正仍需 Owner 回答或承擔的事項）

| 編號 | 事項 | 類別 | 阻擋範圍 |
| --- | --- | --- | --- |
| PB-POL | YouTube API 與 Nickel 政策適用性，以及自動發點核准 | 未決政策 | 核心交換上線、自動發點 |
| PB-EVD | 可靠證據來源的取得與審核流程（不要求封閉清單） | 未決政策（外部來源）、待實作 | 核心交換上線 |
| PB-VER | 版本證據的取得與核對流程（Q18 原則已定） | 待實作 | 首次發點、交換 |
| PB-NAME | 申訴入口的介面名稱 | 未決政策（命名） | 不阻擋功能 |
| PB-LNK | 外部連結檢查方式、時效與地區限制 | 未決政策 | 不阻擋核心交換（不宣稱即時確認） |
| PB-P4 | 每日派送的固定時刻（見 17.3） | 未指定，依 Owner 指示不追問 | 每日派送（不阻擋核心交換） |
| PB-ACC | Owner 當次啟用授權與實機測試帳號核准 | 待執行授權 | 所有實機啟用 |
| PB-TECH | Recovery 與 Batch B 技術閘門（TK-06、TK-07、TK-09、TK-10、TK-11） | 待驗證、待執行授權 | 核心交換與 Beta 啟用 |
| PB-HIST | 歷史歌曲的入池例外（Q24） | 已確認延後（見 17.6） | 不阻擋核心交換 |
| PB-REV-C | 獎勵後版本出現 CONFLICT 的處理（見 17.7.1） | 已裁決，核心必要，待實作 | 核心交換上線 |
| PB-REV-S | 版本拆分與合併（見 17.7.2） | 已裁決：第一階段不開放 | 不阻擋核心交換 |
| PB-REVOKE | 人工補償、撤銷、負餘額與已花掉點數的處理（Q11） | 未決政策 | 不阻擋核心交換（列為後續） |
| PB-RETRY | 每日派送的重試規則（Q30） | 未決政策 | 不阻擋每日派送第一階段 |
| PB-WORK | 作品層級識別與跨平台作品去重 | 未決政策（後續階段） | 不阻擋核心交換 |

**技術軌道（既有，不重複列出）**：規格第 13 與第 15 節的 PD-1 至 PD-5、B-1 至 B-6、X-1 至 X-9 保持不變，屬於實機驗證條件。

### 16.6 不得延後的核心必要功能

以下需求已裁決，**必須納入核心交換版本**，不得因為「尚未實作」而移到後續階段：
- uncertain 的善意補償與其查核流程（Q28、S13 至 S16）。
- 錄音版本跨 Video ID 的獎勵去重（Q12、Q13、S6）。
- 私人問題回報與申訴入口（Q25、Q33）。
- 完整個人歷史（Q32、S19）。
- 退款與補償的累計上限（Q28）。

### 16.7 可延後的未來事項（不阻擋核心交換）

- PB-REVOKE：撤銷、追回、負餘額與人工補償（Q11）。
- PB-WORK：作品層級識別與跨平台作品去重（ROADMAP 第 43 行）。
- PB-HIST：歷史歌曲的入池例外（Q24），已確認延後。
- PB-RETRY：每日派送的重試規則（Q30）。
- PB-LNK：外部連結檢查（Q27）。
- Spotify 與其他平台的投稿。
- 網頁後台（Q34 已排除於第一階段）。

### 16.8 仍真正需要 Owner 回答的問題

1. **PB-POL**：是否同意啟動平台政策研究與自動發點的核准流程？在政策確認前，核心交換的上線條件無法滿足。
2. **PB-EVD**：是否接受以「可追溯來源與人工審核紀錄」作為可靠證據的流程，而不是封閉清單？
3. **PB-NAME**：申訴入口的名稱（可延後，見 17.4）。
4. **PB-ACC**：何時授權實機啟用，以及測試帳號的核准（見 17.5）。

已由 Owner 裁決，不再列為問題：PB-HIST 延後、PB-REV-C 與 PB-REV-S 的規則（見第 17 章）。PB-P4 維持未指定，依 Owner 指示本輪不追問（見 17.3）。

這次授權只包含 Spec 文件更新。本草案不代表整份產品規格已核准。TK-01 至 TK-13 的內容、編號與狀態保持不變；程式、測試、資料庫、Gateway、`.env`、commit 與 push 均未操作。

---

## 17. 平台政策研究、證據流程與待決說明（2026-10-23 更新，草案）

**本章性質**：PB-POL 的公開文件研究、PB-EVD 證據流程、PB-HIST／PB-REV 的裁決與待決說明。本章不是法律意見，不把本專案的判斷寫成「平台官方核准」。本章不授權呼叫 API、爬取、下載音訊、註冊服務或啟用發點。整份 Spec 仍為草案，待最終審查。

### 17.1 PB-POL 公開文件研究

**來源與讀取狀態**（以實際讀取結果為準，不以搜尋摘要代替）：

- YouTube 使用條款（English）：https://www.youtube.com/t/terms?hl=en&override_hl=1 ，版頭與結尾為 2022-01-05，全文讀取。
- YouTube API 服務條款：https://developers.google.com/youtube/terms/api-services-terms-of-service ，頁尾「Last updated 2026-09-14 UTC」，全文讀取。
- 開發者政策：https://developers.google.com/youtube/terms/developer-policies ，頁面摘要標示「Last updated 2026-10-15 UTC」。
  - 逐字讀到前約 39,396 字元（約 51%）。
  - 重新讀取後，III.E.4、III.E.6、III.F.3、III.I 仍只回傳摘要，無法逐字核對。
  - 因此下列四段在本 Spec 中一律視為**未驗證**，不得當作條文原文引用。
- 搜尋結果提到的「Developer Policies Guide」與 Subscribe Button 條件，屬於輔助說明頁，同樣未逐字核對，只作為線索。

**A. 人工觀看公開內容（與 API 無關）**

- 使用條款允許「for your personal, non-commercial use」觀看或收聽內容，並允許以嵌入播放器呈現。
- 同一節第 1 項禁止「access, reproduce, download, distribute, transmit, broadcast, display, sell, license, alter, modify or otherwise use any part of the Service or any Content」，但「as expressly authorized by the Service」或取得書面許可者除外。
- 同一節第 9 項禁止「use the Service to view or listen to Content other than for personal, non-commercial use」。
- 本專案的影響：Owner 人工瀏覽公開頁面、核對來源、記錄識別碼與判斷理由，屬於個人觀看。但不得保存影音內容的副本，也不得重新散布內容。
- 未定：人工判斷是否會被視為條款第 3 項所稱的「自動化存取」。目前建議只做人工判斷，不使用任何自動抓取工具。

**B. YouTube API 的使用（未來才可能適用，目前沒有任何核准）**

- API 服務條款：「you do not have our permission to, and you must not, access or use any of the YouTube API Services」；只能為開發與營運自己的 API Client 而存取（§2.2、§3.1）。
- API 服務條款 §15：不得「exceed or circumvent use or quota restrictions」。
- API 服務條款 §24.3：終止時須「delete all YouTube API Services (including all API Data)」。
- 開發者政策（逐字，III.D.7）：「You must not use undocumented APIs without express permission.」
- 開發者政策（逐字，III.E.1）：未經 YouTube 事先書面同意，不得「download, import, backup, cache, or store copies of YouTube audiovisual content」。
- 開發者政策（逐字，III.D.2）：撤銷授權時的刪除期限分為兩項，分別為 7 個日曆日與 30 個日曆日。
- 開發者政策（摘要，未逐字）：非授權統計資料保存期限與使用者資料刪除期限。**未驗證，不得引用為條文。**
- **目前狀態**：本專案沒有任何已核准的 API 使用，也沒有 API 憑證。上述條款只作為未來申請前的必要條件。

**C. Nickel 獎勵與平台條款的交集**

Nickel 是本專案內部的點數概念，沒有外部官方文件定義它。下列三類必須分開判斷，不得混為一談：

- **(a) 獎勵 YouTube 互動（觀看、點擊、訂閱）**：搜尋結果與摘要指向開發者政策 III.F 的禁止方向，包括不得對觀看、按讚、分享、訂閱給予獎勵，以及不得以觀看次數換取費用。**未逐字核對 III.F.3。** 本專案的 Nickel 不以觀看、點擊或訂閱發放；但若交換流程本身被解讀為對「與 YouTube 應用程式互動」的報酬，仍須審查確認。
- **(b) 獎勵投稿與推薦歌曲（本 bot 內）**：投稿獎勵的觸發是 Owner 審核後的錄音版本，不是觀看 YouTube 內容。它與 (a) 不等同，不能因為「沒有觀看獎勵」就直接推定無限制。但使用者提交 YouTube 連結並獲得點數，是否屬於「engaging with YouTube Applications」，目前沒有依據可判定。
- **(c) 人工瀏覽公開頁面與 API 資料存取**：人工觀看屬於 A 類的個人使用；取得 API 資料則屬於 B 類，受 API 條款與開發者政策約束。兩者不得混用結論。

**D. 「只保存識別碼與 URL」的依據狀態**

本專案目前主張證據只保存識別碼、來源 URL 與判定理由。此主張目前**只有部分依據**，需依資料來源分開判斷：

1. **API 資料**：若識別碼或 URL 來自 API 回應，是否屬於「API Data」而受保存期限與刪除義務約束，尚未確認；需逐字核對 API 條款與開發者政策的定義與期限（摘要部分未驗證）。
2. **人工審核紀錄**：Owner 自行寫下的判定理由與時間，不來自平台內容，但若理由中摘錄歌詞、影片描述或字幕，即構成影音內容的衍生文字，不應保存。
3. **第三方內容**：使用者投稿的 URL、標題、頻道名稱，可能屬於第三方內容或個人資料；目前沒有審查確認其保存期限與刪除要求。

**結論**：本研究完成，但**不構成政策核准**。Nickel 獎勵是否合規、人工審核紀錄的保存範圍，都尚未確定。PB-POL 仍是核心交換上線的政策閘門。

**E. 交給專業審查的具體問題**（本 Spec 不發送，需 Owner 另行授權）

1. Nickel 投稿獎勵，是否屬於開發者政策禁止的「對與 YouTube 應用程式互動給予報酬」？請逐字核對 III.F.3 及其例外。
2. Nickel 交換點數（花點數換取他人推薦的 YouTube 連結），是否構成「以觀看次數換取費用」或類似的交易，還是只是站內虛擬點數？
3. 人工瀏覽公開頁面並保存判定紀錄，是否屬於條款第 3 項的「自動化存取」，是否需要書面許可？
4. 證據中保存的 Video ID、URL、標題與頻道名稱，各自是否屬於 API Data、第三方內容或個人資料？各自的保存期限與刪除要求為何？
5. 開發者政策 III.E.4（保留期限）、III.E.6（爬取）、III.I（禁止項目）的完整逐字條文，與本專案的功能是否有衝突？
6. 外部連結檢查（PB-LNK）若需要發出請求，是否構成自動化存取或 API 使用？
7. 歌曲時長與完整性資料若依賴 API，保存期限與刪除義務如何適用？

### 17.2 PB-EVD 證據流程（Owner 已接受原則）

**原則**（Owner 裁決）：接受「可追溯來源＋Owner 人工審核紀錄」，不要求封閉的網站清單。

**每次判定必須保存**：

- 來源（URL 或發行方參照）。
- 適用的影片或錄音版本識別。
- 判定理由。
- 審核者身分與時間。

**限制**：

- 來源本身不保證內容正確；證據不足或矛盾，仍依既有規則處理（UNRESOLVED 保持 Pending，CONFLICT 暫停）。
- 本流程不解除資料取得方式與平台政策的限制（PB-POL）。
- 證據不得包含影音內容的副本，也不得保存未經核對的第三方內容（見 17.1 A、D）。
- 標題、標籤、頻道名稱或不同 Video ID，單獨不足以證明版本（Q18）。

**待實作**：審核介面與保存欄位由 TK-23 實作。

### 17.3 PB-P4 每日派送時刻

- Owner 裁決：暫不指定，保持為每日派送啟用前的必填設定。
- **只阻擋每日派送**，不阻擋 Beta、核心交換或其他工作。
- 缺失或格式錯誤時，每日派送不啟動（Q31）。
- 依 Owner 指示，本輪不再追問，也不自行填入預設值。

### 17.4 PB-NAME 申訴入口名稱

- Owner 裁決：名稱留待介面設計時提出建議，不另開問題。
- 案件隔離、補件與查詢等已核准功能仍須完整實作（Q33、Q25）。名稱不影響這些功能的實作與驗收。

### 17.5 PB-ACC 實機操作授權包的格式

**目前沒有任何實機操作被授權。** 每次需要實機時，必須依下列欄位提出具體授權包，由 Owner 逐一批准：

- ticket：對應的 TK 編號。
- 目標環境：主機、埠、資料庫名稱（不得是正式庫或 Beta DB，除非另行授權）。
- 帳號角色：帳號名稱、角色說明、是否為唯讀；若含寫入權限，必須明確揭露。
- 操作範圍：允許的語句或操作清單，不含省略號。
- 風險：可能造成的資料變更與不可逆性。
- 停止條件：任一失敗即停止的條件，以及不得重試的情況。
- 輸出範圍：允許與禁止的輸出內容。

### 17.6 PB-HIST：歷史歌曲的入池（延後，已確認）

**指的是什麼**：舊資料遷移後，歌曲已存在於 `jb2_legacy`，但沒有首次獎勵的結算紀錄，身分也可能未知（`docs/phase2b2-batch-b-acceptance.md` 第 70 行）。Q24 的裁決是：**不入池、不補發獎勵**。

**Owner 已確認**：歷史歌曲入池屬於延後事項。

- 不進入交換池，不被推薦，不產生新獎勵。
- 不阻擋核心交換上線。
- 核心交換池只包含新投稿且通過完整條件的歌曲。

**影響**：

- 只影響舊歌曲的流通與歷史顯示，不影響新投稿的完整流程。
- 不影響已核准的首次獎勵規則與重審規則。

**日後若要開放，需另行裁決**（不得自行推定）：

1. 是否允許「經 Owner 審核後免補獎入池」的例外（Q24 的未來方案）。
2. 若允許，審核的證據標準與紀錄要求。
3. 「入池」與「獲獎」分開判斷；目前裁決是舊歌曲**不補發**獎勵。

### 17.7 PB-REV：獎勵後版本的後續變化

**指的是什麼**：某個錄音版本已獲得首次獎勵，之後新證據顯示它的版本關係改變，例如被判定為 CONFLICT、被合併或被拆分。

**既有裁決**：

- 證據衝突不自動追回已發點數（Q11）。
- 證據衝突暫停未結算的候選（`README.md` 第 31 行）。
- 重審不推翻他人已完成的獎勵，不重複發放（Q10、Q14）。
- 有未解衝突的歌曲不得入池（Q20）。

#### 17.7.1 PB-REV-C：衝突發生後的處理（Owner 已裁決，核心必要）

Owner 的裁決如下，取代先前的「既有保留照常完成」方案：

1. **停止新保留**：發現 CONFLICT 後，立即停止該版本的新保留與未結算獎勵。
2. **已保留但未開始發送**：取消派送，確認該訊息不會再被發出；依既有規則一次性退還點數，並記錄退點原因。
3. **發送中且結果未知**：標為 uncertain，不盲目重送，也不退點。
4. **已確認送達**：保留派送紀錄與 Ledger 歷史，不自動追回；該版本不再用於新交換。
5. **既有獎勵不自動追回**（Q11）。
6. **並行保護**：衝突處理與 worker 發送之間必須有鎖或狀態條件保護，不得出現「先退點、後又發出」的情況。

**對應驗收**（依 Owner 指定的歸屬，對應 16.4 的 TK-16 至 TK-18）：

- **TK-16（交換保留）**：衝突後，該版本不再被新保留或新交換選中；已保留但未送出的紀錄，需能被 TK-17 取消。
- **TK-17（DM 交付狀態機）**：送出前取消，保留未送出者取消後不會被 worker 發出；送出結果未知者標為 uncertain，不重送、不退點。衝突處理與送出之間需以狀態條件或鎖保證，不會出現「先退點、後送出」。
- **TK-18（退款、uncertain 與補償）**：未送出者依既有規則一次性退點，並記錄原因；每筆扣款最多退款一次；已確認送達者保留派送與 Ledger 歷史，不自動追回，該版本不再用於新交換。
- **TK-19、TK-20**：只承擔案件與歷史的呈現要求（例如使用者可查到 uncertain 或退點原因；歷史不把已追回的紀錄誤顯示為已交付），不承擔交付控制本身。

#### 17.7.2 PB-REV-S：版本拆分與合併（Owner 已裁決，第一階段不開放）

Owner 的裁決如下：

- 第一個核心交換版本**不提供**正式的版本拆分或合併操作介面。
- **不自動重算**歷史獎勵。
- 維持 CONFLICT 暫停；再次核准**不會**清除 CONFLICT。
- 內部可以保留合併方法，但**不作為產品功能開放**。

**區分兩種操作**：

- **把新證據指派給已確認版本**：屬於核心版本對帳，**不延後**。
- **修改既有已確認版本的拆分或合併**：屬於延後事項。

**安全原則**：若衝突無法在既有的安全流程內解決，維持隔離狀態，不啟用有風險的修正。

### 17.8 集中待決修訂（取代第 16.5 與 16.8 中的對應條目）

| 編號 | 事項 | 類別 | 阻擋範圍 |
| --- | --- | --- | --- |
| PB-POL | 平台政策閘門。研究已完成，**未獲政策核准**；需專業審查，並逐字核對摘要段落（17.1 D、E） | 未決政策 | 核心交換上線、自動發點 |
| PB-EVD | 證據流程原則已接受（17.2）；審核介面與保存欄位由 TK-23 實作 | 原則已裁決，待實作 | 核心交換上線（實作部分） |
| PB-REV-C | 衝突後的處理（17.7.1） | 已裁決，待實作 | 核心交換上線（實作與驗收） |
| PB-REV-S | 版本拆分與合併（17.7.2） | 已裁決：第一階段不開放 | 不阻擋核心交換 |
| PB-HIST | 歷史歌曲入池（17.6） | **已確認延後** | 不阻擋核心交換 |
| PB-P4 | 每日派送的固定時刻（17.3） | 未指定，依 Owner 指示不追問 | 只阻擋每日派送 |
| PB-NAME | 申訴入口名稱（17.4） | 命名延後 | 不阻擋功能 |
| PB-ACC | 實機授權包（17.5） | 每次逐項批准 | 所有實機啟用 |
| PB-REVOKE | 人工補償、撤銷、負餘額與已花掉點數的處理（Q11） | 未決政策，非核心 | 不阻擋核心交換 |
| PB-RETRY | 每日派送的重試（Q30） | 未決政策，非核心 | 不阻擋每日派送第一階段 |
| PB-WORK | 作品層級識別與跨平台作品去重 | 未決政策，後續階段 | 不阻擋核心交換 |
| PB-LNK | 外部連結檢查方式與地區限制（Q27） | 未決政策（與 PB-POL 相關） | 不阻擋核心交換（不宣稱即時確認） |
| PB-TECH | Recovery 與 Batch B 技術閘門（既有 TK-06、TK-07、TK-09、TK-10、TK-11） | 待驗證、待執行授權 | 核心交換與 Beta 啟用 |

### 17.9 仍需 Owner 回答的問題

只列真正需要裁決者，本輪不再追問 PB-P4。

1. **PB-POL**：是否核准正式的政策或法律審查？審查範圍應包含 17.1 E 的七個問題，特別是 Nickel 投稿獎勵是否屬於「對與 YouTube 應用程式互動給予報酬」，以及摘要段落的逐字核對。

**不需要裁決（已由 Owner 給定規則）**：PB-REV-C、PB-REV-S、PB-HIST。

**不得自行補答**：PB-P4、PB-POL 的審查結論、Nickel 獎勵是否合規。
