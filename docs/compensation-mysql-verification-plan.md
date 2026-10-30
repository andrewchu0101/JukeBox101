# 補償功能隔離 MySQL 驗證計畫（第三版，2026-10-24）

**性質**：驗證計畫與 A＋B 有限授權草案。**本文件不授權任何執行。** 本輪（第三版）只完成離線準備：撰寫 MySQL 測試、建立獨立啟動器、離線靜態與離線測試。**沒有啟動或連線 MySQL，沒有解密憑證，沒有執行 DDL 或 DML，沒有碰 3306、`.env`、Discord 或真實 DM，沒有啟用補償，沒有 commit 或 push。** 第 7 節為待批准草案，須 Owner 明確回覆後才可執行。

**重要區分**：下列 MySQL 測試**尚未在實機執行**，不得宣稱已通過或已紅綠。實機證據只能在第 7 節授權的實機階段取得。離線 SQLite 通過**不等於** MySQL 通過。

## 0. 第三版變更摘要

| 項目 | 第二版 | 第三版（本輪） |
| --- | --- | --- |
| M1–M12 實作 | 無 MySQL 測試檔 | `tests/test_compensation_mysql.py` 共 20 項：覆蓋 M1–M8、M10、M11、M12，另加 D1 拒絕未確認未交付的案例；M9 僅離線 |
| 「先離線實機紅綠」前置 | 作為 B 的前置條件 | **移除**。離線準備完成後，實機階段取得驗證證據（第 7.4 節） |
| 授權草案 | 分 A、B 兩段，B 有前置 | 整合為一份 A＋B 草案（第 7 節），內含 A 門檻自動停止 |
| 啟動器 | 修改 `combined_authorized.py` 的建議 | 新建獨立入口 `.local/mysql-test/compensation_authorized.py`；舊啟動器保留不動 |
| 基線 schema 檢查 | 要求 `jb2_control.schema_version='2b2-b1'` | **更正**：基線要求 `jb2_control` 為空，因為既有 fixture 會插入與刪除該列；版本以 migration 003 形狀檢查代替 |
| 未處理缺口 | 無 | 關閉 flag 後，未結案事故不再持有 claim（已離線重現，見第 11 節） |

## 1. 目標與範圍

在既有隔離 MySQL 8 實例上，驗證候選 DDL `docs/compensation-candidate-004.sql` 與補償程式的實際行為：約束是否由引擎強制、交易與鎖是否符合離線模型、D1 結案的一次退款、重放不重複退款，以及 D1 與 claim 的競爭結果。

## 2. 目標實例與身分檢查

| 項目 | 規定 |
| --- | --- |
| 實例 | 既有隔離實例，`127.0.0.1:3307`，MySQL 8.0.46（≥ 8.0.16，CHECK 才會被強制） |
| datadir | `D:\Project\MusicBased\discordBot_JukeBox101\.local\mysql-test\20261024-122528\data` |
| server UUID | `f138d7c6-cf62-11f1-87be-c025a5a12877`（來源：`run-report.json` 與 `auto.cnf`） |
| 資料庫 | `jukebox101_test` |
| 帳號 | `jukebox101_test_exchange`（DML，測試用）；`jukebox101_test_migrator`（只在 B 階段 DDL 使用）；`root` 只用於身分讀取與 SHUTDOWN |
| 禁止 | 3306；`.env`；root 作為測試帳號；新增帳號或增權 |
| 身分檢查 | 由 `tests/isolated_mysql_target.py::check_server_identity` 核對 port、版本、UUID、database、datadir、log_error 前綴、socket |

## 3. 唯讀基線（A 階段）

全部只用 SELECT 或 SHOW，不寫入任何列。任一項不符即停止，不清空、不重建、不重設密碼、不增權。

1. **身分**：`check_server_identity` 通過。
2. **migration 003 形狀**：沿用 `require_migration_003`（欄位、唯一鍵、CHECK 名稱）。
3. **候選表不存在**：`information_schema.tables` 查詢三張補償表，應為 0。已存在即停止。
4. **營運表為空**：沿用 `require_empty_baseline`，即 `EMPTY_TABLES` 全部為 0，只允許兩筆固定版本根（`s1-original-a`、`s1-original-b`）。`jb2_control` 也在此列表中，因為 fixture 會插入並刪除該列。
5. **授權快照**：`SHOW GRANTS FOR CURRENT_USER()`，以 exchange 帳號執行，結果存為證據。
6. **migrator 權限**（僅 B）：`SHOW GRANTS FOR CURRENT_USER()` 以 migrator 帳號執行，必須具備：
   - `CREATE` 於 `jukebox101_test.*`（建立三表所需）。
   - `REFERENCES` 於 `recommendations` 與 `credit_ledger`（候選 DDL 的外鍵父表）。
   - 若權限不足，停止，不由 Claude 執行 GRANT，需 Owner 另行授權增權。
7. **schema 留存**：`SHOW CREATE TABLE` 取代 `mysqldump`。基線留存 `recommendations`、`credit_ledger`、`songs`、`users`、`jb2_control`；DDL 後留存三張新表。`SHOW CREATE TABLE` 只需任一權限，不需要 `PROCESS` 或 `LOCK TABLES`。

**注意**：`require_schema` 只檢查欄位名稱、唯一鍵集合與具名 CHECK，不檢查型別、ENUM 值、NULL 性或預設值。DDL 後須以 `schema-after` 的 `SHOW CREATE TABLE` 人工比對候選 DDL 的型別。這是唯一的型別證據。

## 4. DDL 範圍（B 階段）

- **來源**：`docs/compensation-candidate-004.sql`，SHA-256 `e1804816e6503eaa51d697cd31f61b38aa328aa17a3a9933082fa08feff36651`。執行前重算，不符即停止。
- **內容**：只有三個 `CREATE TABLE`，各含其內建索引與約束（`ix_grants_open`、`ix_events_incident`）。Launcher 會先移除所有註解再以分號切割，確保註解內的分號不會切斷語句；切出來必須恰好三句且都以 `CREATE TABLE` 開頭。
- **不包含**：ALTER、DROP、TRUNCATE、INSERT、GRANT、其他 CREATE，不修改 `credit_ledger.reason` 或 `recommendations.status`。
- **外鍵**：`exchange_incidents` 參照 `recommendations` 與 `credit_ledger`；`exchange_redelivery_grants` 參照 `exchange_incidents`；`exchange_incident_events` 無外鍵。

### 4.1 D1 相關定義（逐字，對應候選 DDL 第 20、25、36–37 行）

```sql
state ENUM('approved', 'settled', 'void', 'not_system_fault', 'non_fault_refunded') NOT NULL,
closure ENUM('refund_only', 'refund_and_redelivery', 'non_fault_refund') NULL,
CONSTRAINT chk_incidents_non_fault CHECK (state <> 'non_fault_refunded' OR (refund_status = 'refunded_once' AND closure = 'non_fault_refund'
    AND redelivery_decision = 'not_approved' AND settled_at IS NOT NULL AND closed_at IS NOT NULL)),
```

## 5. SQLite TEXT 與 MySQL DATETIME(6) 差異

鏡像（`tests/compensation_sqlite.py`）中仍為 TEXT 的欄位，對應候選 DDL 的 DATETIME(6)：

| 表 | 欄位 |
| --- | --- |
| `exchange_incidents` | `closed_at` |
| `exchange_redelivery_grants` | `claimed_at`、`delivered_at`、`disposition_at`、`closed_at` |
| `exchange_incident_events` | `at` |

`granted_at` 在鏡像中已是 DATETIME，不在差異清單內。

影響：
1. 鏡像接受任意字串，離線無法證明非法時間會被拒絕。
2. `expire_grant_claims` 的 `claimed_at < %s` 在鏡像是字串序比較。naive UTC 與同格式下字串序等於時間序，因此邏輯結論可沿用；MySQL 的 DATETIME 比較需實機確認。
3. 鏡像回傳 `str`，MySQL 回傳 `datetime`。Python 端目前沒有對這些欄位做算術（grep 核對），現行邏輯不受影響；但 Owner 名單的顯示格式與測試比較會不同。
4. 非法 ENUM 值在 MySQL strict mode 的錯誤碼與鏡像的 CHECK 不同，測試不斷言此錯誤碼。
5. `require_schema` 不檢查型別，無法自動偵測此差異。

附帶既有差異（不在本次範圍）：鏡像的 `recommendations.delivered_at` 為 TEXT，基底 `bot/database/schema.sql` 為 `DATETIME(6)`，建議另案處理。

## 6. M1–M12 對應表（2026-10-24 離線撰寫）

測試檔：`tests/test_compensation_mysql.py`，20 項。實作狀態欄的「已撰寫」指**程式已寫、離線收集與跳過正常；未在 MySQL 實機執行**。

| 編號 | 案例（測試函式） | 驗證內容 | 實機狀態 |
| --- | --- | --- | --- |
| M1 | `test_m1_two_concurrent_settlements_refund_once_and_issue_one_grant` | 兩執行緒同時結算同一事故：退款一次、補送一次 | 已撰寫，未實機 |
| M2 | `test_m2_two_concurrent_claims_of_one_grant_send_at_most_once` | 兩執行緒同時 claim 同一補送：只一個取得 payload | 已撰寫，未實機 |
| M3 | `test_m3_refund_only_closes_without_a_grant_and_releases_the_block` | 退款不補送：結案、無補送、解除阻擋後可再交換 | 已撰寫，未實機 |
| M4 | `test_m4_a_repeated_settlement_refunds_and_grants_nothing_more` | 重放結算與重複核准：不再退款或補送，事件數為 2 | 已撰寫，未實機 |
| M5 | `test_m5_a_claim_that_commits_first_refuses_a_later_approval`、`test_m5_an_approval_that_commits_first_holds_the_claim_and_is_audited` | 認領先：核准被拒；核准先：認領被保留並寫入 `claim_held` | 已撰寫，未實機 |
| M6 | `test_m6_a_settlement_first_refuses_the_claim_without_sending`、`test_m6_a_claim_first_leaves_no_incident_for_the_owner_to_void` | 結算先：認領回傳 failed；認領先：無事故可作廢（見第 11 節） | 已撰寫，未實機 |
| M7 | `test_m7_a_not_sent_grant_blocks_...`、`test_m7_an_uncertain_grant_keeps_its_song_...` | not_sent 阻擋，結案後釋放；uncertain 阻擋，結案後仍持有歌曲 | 已撰寫，未實機 |
| M8 | `test_m8_with_the_switch_off_and_the_tables_present_...` | flag 關閉、表存在時，一般交換與 claim 正常，且不寫補償列。**保留三表，不 DROP** | 已撰寫，未實機 |
| M8（已知缺口） | `test_m8_known_gap_a_switched_off_claim_still_holds_an_open_incident` | strict xfail：flag 關閉後未結案事故應持有 claim，現況不持有（第 11 節） | 已撰寫，未實機 |
| M9 | 不在實機。離線：`tests/test_compensation_schema.py`（缺表、缺欄、缺唯一鍵、缺 CHECK 各自拒絕） | 只在離線鏡像執行 | 離線已通過（既有） |
| M10 | `test_m10_engine_refuses_a_duplicate_reservation_and_an_invalid_closure`、`test_m10_engine_refuses_a_second_incident_with_the_same_refund_ledger` | 引擎拒絕重複事故（1062）與不一致結案（3819）；ROLLBACK 後無殘留 | 已撰寫，未實機 |
| M11 | `test_m11_concurrent_non_fault_closures_refund_once_with_no_grant_and_no_zero_row`、`test_m11_a_later_closure_repeats_nothing` | D1：兩執行緒同時結案，恰好一次退款；無補送、無零差額列；重放不退款 | 已撰寫，未實機 |
| D1 | `test_d1_refuses_closure_without_confirmed_non_delivery_and_writes_nothing` | 未確認未交付：拒絕且無寫入 | 已撰寫，未實機 |
| M12 | `test_m12_a_recorded_non_fault_holds_a_later_claim_and_the_closure_refunds_once`、`test_m12_a_claim_that_commits_first_leaves_no_refund_and_no_incident`、`test_m12_concurrent_closure_and_claim_never_refund_and_send_together` | D1 與 claim 競爭：三種順序皆須滿足「退款與 sending 不同時成立」；不預設 claim 必定成功 | 已撰寫，未實機 |

**並行的誠實說明**：並行案例使用同一程序內的執行緒，每個執行緒持有自己的連線。寫入門閘是資料庫的 `jb2_control` 列鎖，因此分離的連線足以驗證序列化。這不是獨立作業系統程序，也沒有直接觀察鎖等待。沿用 S1 的既有做法。

**M12 的設計推論**：離線程式碼顯示，事故存在時 claim 會被保留（`guard_incidents=True`），因此「事故先、認領後」的結果是固定的；「認領先、事故後」則因 `_is_stranded` 失敗而拒絕。並行測試只斷言不變條件，不預設哪一種先發生。

## 7. A＋B 整合授權草案（待 Owner 批准，批准前不得執行）

### 7.1 批准範圍

- **實例**：`127.0.0.1:3307`，`jukebox101_test`，datadir 與 UUID 如第 2 節。
- **啟動器**：`.local\mysql-test\compensation_authorized.py`（獨立入口，舊啟動器不動）。
- **憑證**：只使用既有 `credentials.dpapi` 的既有鍵（`root`、`exchange`、`migrator`）。不解密輸出、不列印、不寫檔。報告只記錄鍵名是否存在。
- **帳號**：既有 `jukebox101_test_exchange`、`jukebox101_test_migrator`。**不新增帳號、不 GRANT、不 REVOKE、不改密碼。**
- **釘選雜湊**（批准即綁定這些確切內容）：

| 檔案 | SHA-256 |
| --- | --- |
| `docs/compensation-candidate-004.sql` | `e1804816e6503eaa51d697cd31f61b38aa328aa17a3a9933082fa08feff36651` |
| `tests/test_compensation_mysql.py` | `c5a868980cbf9fa2923f34b7c6fca6f1446788a66ad0c5b1da86f0bb38e4f309`（第 14 節：修改前 `db990d79…3ec9`） |
| `.local\mysql-test\compensation_authorized.py` | `5dedf4368ff8b756a99fc9863cc928283fa529767d3e6a88a7a260e609c5e51c`（第 14 節：修改前 `e02872da…2c7b2`） |

### 7.2 執行方式（由 Owner 親自或批准後執行）

Owner 批准後，在 Windows PowerShell 依序執行。兩段都必須使用**新的 run-name**：

```powershell
cd D:\Project\MusicBased\discordBot_JukeBox101
$env:COMPENSATION_MYSQL_APPROVED_STAGE = 'A'
.venv\Scripts\python.exe -X utf8 .local\mysql-test\compensation_authorized.py --stage A --run-name a-<新名稱>
```

A 通過後，才可以執行 B（B 內部會再次跑 A 的全部檢查，任一不符即停止）：

```powershell
$env:COMPENSATION_MYSQL_APPROVED_STAGE = 'B'
.venv\Scripts\python.exe -X utf8 .local\mysql-test\compensation_authorized.py --stage B --run-name b-<新名稱>
```

### 7.3 A 階段：唯讀基線

**允許**：SELECT、SHOW（`TABLES`、`CREATE TABLE`、`GRANTS`）、`require_migration_003`、`require_empty_baseline` 所含的唯讀檢查。

**預期**：身分通過；migration 003 形狀通過；三張候選表不存在；營運表為空（僅兩筆固定版本根）。

**停止條件**（任一即停止，不進入 B，不重跑覆蓋）：身分不符；3307 已有監聽；候選表已存在；基線列數異常；憑證鍵缺漏；任何寫入語句；任何 3306 連線。

### 7.4 B 階段（A 通過後）：DDL 與 M 案例

**離線準備已完成**（第 6 節與第 9 節）。本授權的實機階段負責取得 M 案例的實機證據；**實機證據不是離線準備的前置條件**。

**DDL 步驟**：
1. 重算候選 DDL 的 SHA-256，必須符合 7.1 節。
2. 檢查 migrator 權限（第 3 節第 6 點），不足即停止。
3. 以 migrator 依序執行三個 `CREATE TABLE`，任一錯誤即停止，不補跑。
4. 以 `SHOW CREATE TABLE` 留存 `schema-after`，由 Owner 或 Claude 逐項比對型別（第 3 節末段）。
5. 以 exchange 帳號執行 `require_schema` 的唯讀探測。

**M 案例步驟**：
- 執行 `tests/test_compensation_mysql.py`（20 項，`-m mysql`）。
- **預期結果**：19 passed、1 xfailed（M8 已知缺口）、0 failed、0 error。任何 failed 或 error 均須如實回報，不得修改測試以求通過。
- 每個 M 案例的 stdout 與退出碼保存於證據資料夾內的 `pytest-m-cases.log`。

### 7.5 資料影響與清理（需 Owner 確認 D-1）

- **寫入**：B 階段 DDL 建立三張新表；M 案例只在合成使用者範圍 `900000000000000100–199` 與帶固定標記的合成歌曲內寫入列。
- **清理（D-1，建議）**：每個測試結束時，夾具只刪除合成使用者的補償列（子表先於父表），之後才由既有夾具刪除其他合成列。這與既有 S1、S2 測試一致，三張新表因此不會殘留資料。若 Owner 不接受刪除，須改變基線設計，需另行批准。
- **證據**：測試輸出、JSON 報告與 SHOW CREATE TABLE 檔案，均保存於新的證據資料夾，不靠保留資料列作證據。
- **回復**：本授權不含 DROP。若需撤回三表，須另行授權。

### 7.6 禁止事項

- 任何 ALTER、DROP、TRUNCATE；B 階段 DDL 以外的任何 DDL。
- 關閉外鍵檢查（`FOREIGN_KEY_CHECKS=0`）。
- 新增帳號、GRANT、REVOKE、改密碼、`--skip-grant-tables`、`--skip-networking`。
- 連線 3306、讀取 `.env`、讀取 `error.log` 的內容（只允許確認其存在）。
- Discord 指令、真實 DM。`RecordingSender` 或 fake adapter 才是測試的送信對象。
- 啟用 `compensation_enabled` 於任何正式路徑。
- commit、push、merge、rebase、reset、清理工作樹。
- 因失敗而修改測試、放寬斷言、重跑直到通過。

### 7.7 結束與殘留檢查（成功與失敗都執行）

啟動器的 `finally` 區塊會執行下列步驟，並寫入 `compensation-report.json`：

1. 以 root 發送 SHUTDOWN，記錄退出碼；只有正常停止逾時才強制結束，並記錄 `normal_stop_timed_out=true`。
2. 檢查 3307 的 Listen 監聽（`Get-NetTCPConnection -State Listen`），結果寫入 `listeners_on_3307_after_stop`。
3. 檢查指向本 datadir 的 `mysqld.exe`（CIM 命令列比對），結果寫入 `mysqld_for_isolated_datadir_after_stop`。
4. 檢查本 repo 的 `tests.exchange_worker_process` 殘留，只停止本 repo 路徑的程序。
5. 保留整個資料目錄與證據，不刪除。

三項皆為空、退出碼為 0，才可宣稱「已停止」。

### 7.8 證據與命名

- 每次執行建立新資料夾 `.local\mysql-test\compensation-<stage>-<run-name>`，已存在即拒絕（不覆寫）。
- 內容：`compensation-report.json`、`server-console.log`、`a-exchange-grants.txt`、`schema-before\`、`schema-after\`、`pytest-m-cases.log`。
- 既有證據（`pytest*.log`、`*-report.json`、`combined-*`）不覆寫。

### 7.9 Owner 需決定的三項

| 編號 | 決定事項 | 白話說明 | Claude 建議 |
| --- | --- | --- | --- |
| D-1 | 測試結束後是否刪除合成使用者的補償列 | 不刪，三張表就帶著測試資料，之後無法用 DROP 復原；刪，與既有測試一致，證據改由報告與 SHOW CREATE 檔保存 | 刪除（第 7.5 節） |
| D-2 | 關閉開關時未結案事故的 claim 缺口 | 目前開關關閉後，未結案事故的原訂單仍可能被送出。修正會改功能程式，本輪不做 | 另案決定，M8 已知缺口維持 xfail（第 11 節） |
| D-3 | 並行測試用執行緒而非獨立程序 | 執行緒已能驗證資料庫列鎖的序列化；獨立程序需擴充 harness，屬於較大變更 | 接受執行緒版本 |

### 7.10 批准回覆範例

> 批准第 7 節 A 階段；B 階段在 A 通過後自動接續（B 內含 A 的全部檢查）。D-1 採建議（測試結束後刪除合成補償列）；D-2 維持 xfail；D-3 接受執行緒版本。禁止事項與停止條件依第 7.3、7.6 節。

## 8. 仍待解決事項

1. **migrator 權限**：A 階段才能確認；不足則需另行批准增權（不在本授權範圍）。
2. **`credentials.dpapi` 的 `migrator` 鍵**：依寫入程式應存在，只確認鍵名，不解密值。
3. **Q-B（`uncertain` 經 `confirmed_not_delivered` 後是否釋放歌曲）**：維持保留；M7 uncertain 案例依此規則撰寫。Owner 若改規則，需同步修改測試。
4. **型別一致性**：只能由 DDL 後的 SHOW CREATE TABLE 人工比對（第 3 節末段）。
5. **D-1、D-2、D-3**：見第 7.9 節。

## 9. 啟動器（`.local\mysql-test\compensation_authorized.py`，2026-10-24 新建）

### 9.1 與舊啟動器的關係

- 舊的 `combined_authorized.py`、`verify_single_route_authorized.py`、`s1_*`、`retest_authorized.py`、`run_authorized.py` 全部保留不動，作為歷史。
- 新入口只做 compensation 驗證，不沿用舊的 `test_exchange_*` 三檔。

### 9.2 第 9.3 節舊修改清單的處理結果（已在新入口實作，**未執行**）

| 舊建議 | 新入口的做法 |
| --- | --- |
| 新增 DDL 步驟（migrator） | `stage_b_ddl`：先去除註解、以分號切割，必須恰好三句 `CREATE TABLE`；錯誤即停止 |
| 基線先於 pytest，不清理 | `stage_a` 只做唯讀檢查；pytest 只在 B 階段執行，且在 A 通過之後 |
| 新增 SHOW CREATE TABLE 留存 | `record_show_create`，寫入新子資料夾，已存在即失敗 |
| 殘留檢查（3307 Listen、datadir 的 mysqld） | `residual_checks`，寫入報告；另檢查 worker 殘留 |
| 證據檔名不覆寫 | `check_preconditions` 要求資料夾不存在；所有檔案名稱為新的 |
| 指定測試檔 | `run_m_cases` 只執行 `tests/test_compensation_mysql.py` |

### 9.3 其他安全條件

- **批准閘**：`COMPENSATION_MYSQL_APPROVED_STAGE` 必須等於 `--stage`，否則拒絕。這是防止誤執行的環境變數，不是授權本身。
- **雜湊閘**：候選 DDL 與測試檔必須符合第 7.1 節的 SHA-256；測試檔未釘選時 B 階段拒絕。
- **埠閘**：啟動前 bind 127.0.0.1:3307 必須成功，否則代表已有監聽。
- **身分閘**：DPAPI 內的 UUID 必須等於記錄值；連線後由 `check_server_identity` 核對。
- **憑證**：只在記憶體解密，報告只寫鍵名是否存在。子程序（pytest）只在子環境中接收 exchange 密碼。
- **失敗也停止**：所有步驟在 `try` 內；`finally` 一律執行停止與殘留檢查並寫報告。

### 9.4 已知限制與待確認

- **未執行**：本輪只做 `py_compile` 與近似的未定義名稱檢查，沒有執行啟動器。
- **MySQL 命令列**：啟動參數沿用 `combined_authorized.py` 的已驗證組合，只新增 `--bind-address` 與 `--skip-named-pipe`（同一組參數已在既有實機執行中使用）。
- **`TEST_SHA256` 的釘選**：測試檔若再改，必須同步更新啟動器與第 7.1 節的雜湊，否則 B 會拒絕執行。
- **dpapi 輔助模組**：沿用既有 `.local\mysql-test\dpapi_credentials.py`，本輪未讀取其內容。

## 10. 本輪離線檢查結果（2026-10-24，實際執行）

| 項目 | 指令與結果 |
| --- | --- |
| 新 MySQL 測試檔，隔離開關未設定 | `pytest tests/test_compensation_mysql.py -m mysql`：**20 skipped，無連線**（跳過發生在夾具之前） |
| 離線補償套件（gate、disposition、store、repository、schema） | **69 passed、1 xfailed**，約 94 秒 |
| 已知缺口的紅燈證據 | `--runxfail` 執行 `test_known_gap_with_the_switch_off_an_open_incident_still_holds_the_claim`：`AssertionError: assert 'sending' == 'held_for_review'`（**離線 SQLite 紅燈，證實缺口存在**） |
| 語法編譯 | `py_compile`：新測試檔、新啟動器、`test_compensation_gate.py` 皆通過 |
| 近似未定義名稱檢查 | 測試檔：無；啟動器：僅 `__file__`（模組屬性，正常） |
| 未執行 | 完整測試套件（依指示不重跑）；任何 MySQL 實機測試；啟動器本身 |

**TDD 紀錄（tdd skill）**：本輪新增的離線測試是「已知缺口」的紅燈測試，以 strict xfail 記錄，並以 `--runxfail` 取得真實紅燈。它不是功能修正，因此不宣稱綠燈。MySQL 測試本輪只完成撰寫與收集。

**skill 使用**：tdd 可用（`~/.claude/skills/tdd`），已依其規則撰寫離線測試並取得紅燈證據。start-dev-team 位於 `~/.claude/skills/start-dev-team`（指向 `/home/gaston/projects/claude-skills-bundle/skills/start-dev-team`），其指引要求：必須有 `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`、互動式工作階段、呈現配置表並取得 Owner 核准後才能建立 Teammate。目前環境變數為 1，但本輪沒有 Owner 核准的團隊配置，且 CLAUDE.md §4 不主動建立多代理人團隊，因此**未使用**。

## 11. 已知缺口與發現（本輪審閱）

### 11.1 關閉 flag 後未結案事故不再持有 claim（已離線重現）

- **現象**：`SubmissionRepository(..., compensation_enabled=False)` 的 `claim_delivery` 傳入 `guard_incidents=False`，因此不檢查事故，原訂單可被送出（離線紅燈 `'sending' == 'held_for_review'`）。
- **條件**：flag 預設為關閉；只有在開啟並建立事故後、又關閉時才會發生。
- **風險**：可能導致事故中的原訂單再次送出，與補償的一次退款或補送衝突。
- **最小修正範圍（未執行，需 Owner 決定 D-2）**：三種做法擇一：
  - (a) 只要三表存在就一律守衛（需先探測表是否存在，不可在表不存在時查詢）。
  - (b) 關閉前檢查是否有未結案事故，若有則拒絕啟動。
  - (c) 啟動時若偵測到未結案事故，強制開啟守衛。
- **Claude 建議**：(a)，因為它不依賴操作流程。修正後，`test_known_gap...` 應移除 xfail，M8 已知缺口改為正式斷言。

### 11.2 結案的 void 分支在正式 claim 路徑下不可達

- 有事故時 claim 會被保留（flag 開啟時）；沒有事故時 claim 成功後，核准會被拒絕（NOT_STRANDED）。因此「已核准後 claim 成功」在正式路徑中不會發生，void 分支無法由正式流程觸發。
- 第 6 節 M6 第二項記錄此不可達性，未假裝執行 void。
- 若 Owner 希望保留 void，須另行說明其觸發條件；否則可在後續清理時移除。

### 11.3 M12 的競爭結果不預設

- 事故先：claim 被保留，結案退款。
- 認領先：核准被拒，無退款，claim 送出一次。
- 結案後 claim：狀態為 failed，claim 不送出。
- 不變條件：退款與 sending 不同時成立，退款至多一次。

### 11.4 其他限制

- 並行案例以執行緒代替獨立程序（D-3）。
- 本輪沒有修改任何功能程式（`bot/`）。
- 候選 DDL 未註冊、未執行。
- `require_schema` 不檢查型別（第 3 節末段）。

## 12. 本計畫不包含

- Discord 指令、真實 DM、正式 `/exchange` 或補償功能的啟用。
- 3306 或正式資料庫。
- `.env` 或憑證檔的讀取。
- 新增帳號、增權或修改既有權限。
- 未知提交結果恢復、完整 Batch B 矩陣（另案）。

## 13. 實機執行紀錄

### 13.1 A 階段（2026-10-24 Owner 批准，已執行）

- **指令**：`COMPENSATION_MYSQL_APPROVED_STAGE=A`、`PYTHONPATH` 指向 repo 根目錄，執行 `.local\mysql-test\compensation_authorized.py --stage A --run-name run1`。`PYTHONPATH` 是因為該啟動器未把 repo 根目錄加入 `sys.path`；這是執行方式的補充，**啟動器檔案未修改，雜湊仍為第 7.1 節所列值**。
- **結果**：`passed: true`，退出碼 0。
- **身分**：port 3307、版本 8.0.46、UUID `f138d7c6-cf62-11f1-87be-c025a5a12877`、datadir 與 log 位於本 run 目錄、database `jukebox101_test`，全部與記錄相符。
- **基線**：migration 003 形狀通過；三張候選表不存在；營運表為空（除兩筆固定版本根）。
- **授權快照**：exchange 帳號僅有 `GRANT USAGE ON *.*` 與 `SELECT, INSERT, UPDATE, DELETE ON jukebox101_test.*`，沒有 CREATE 與 REFERENCES。migrator 權限尚未檢查（屬 B 階段）。
- **寫入**：無（A 階段只做 SELECT 與 SHOW）。`ddl_reapplied_by_stage_a=false`、`new_accounts=0`、`grants_changed=0`。
- **停止**：`server_stopped=true`，退出碼 0；3307 無 Listen；無 mysqld 指向本 datadir；無 worker 殘留。
- **證據**：`.local\mysql-test\compensation-a-run1\compensation-report.json`、`a-exchange-grants.txt`、`server-console.log`（空）。報告與 grants 檔中未出現 password 字樣。

### 13.2 B 階段：未執行，停止原因

- **阻塞**：D-1 要求清理前保留紀錄，目前夾具沒有實作。需要的修正（第 7.5 節未述及）：
  1. 測試夾具在刪除前把補償列與本輪識別寫入新的證據資料夾（JSON）。
  2. 啟動器傳入該資料夾路徑，並在 pytest 前建立。
  3. 既有的 `clear_repository_fixture` 所清除的列（recommendations、credit_ledger、submissions、jb2_* 等）同樣需要紀錄，否則「清理前紀錄」不完整。
- **影響**：修正會改動 `tests/test_compensation_mysql.py` 與 `compensation_authorized.py`，兩者的雜湊都會改變，需要重新批准。
- **未執行的項目**：DDL、migrator 權限檢查、M 案例、清理。資料庫狀態與 A 完成時相同。

## 14. 第二次批准的最小修正與重新釘選（2026-10-24）

### 14.1 雜湊

| 檔案 | 修改前 | 修改後 |
| --- | --- | --- |
| `docs/compensation-candidate-004.sql` | `e1804816e6503eaa51d697cd31f61b38aa328aa17a3a9933082fa08feff36651` | **不變**（執行前與修正後重算皆相符） |
| `tests/test_compensation_mysql.py` | `db990d79d6c733cfdb9a4f6fb94d6d002fcfda1ca75c3985d5c6c7a18f3b7ec9` | `072502747f840f2da6b628f10f91eb4ef1271bbd1f90e56b1b6b2762d07e77e5` |
| `.local\mysql-test\compensation_authorized.py` | `e02872da809ebeb1caf84e1f0ee50ff98704796e64f5b52c45a24878bf22c7b2` | `5dedf4368ff8b756a99fc9863cc928283fa529767d3e6a88a7a260e609c5e51c` |
| `tests/test_compensation_cleanup_guard.py`（新增，離線，不納入實機執行） | 無 | 未釘選；B 的 pytest 只執行 `tests/test_compensation_mysql.py` |

### 14.2 差異摘要

- **測試檔**：匯入改為明確列出；舊的 `clear_compensation_fixture` 與 `compensation_world` 改為 `guarded_world` 與 `compensation_world`。新增：`ROW_STEPS`（與既有夾具相同的清理範圍，補償表在前）、`decision_step`（只納入 Owner 行為與本輪影片的決定，其他列拒絕整體清理）、`plan_cleanup`、`execute_cleanup`、`write_json_new`（獨占建立、fsync、讀回比對）、`guarded_cleanup`。`guarded_world` 不再使用既有的 `repository_world` 清理，改由 `guarded_cleanup` 清理，並在清理後執行既有的空基線檢查。
- **啟動器**：`REPO` 改由檔案位置推算並加入 `sys.path`，不再依賴 `PYTHONPATH`；檢查 repo 根目錄存在。新增 `cleanup` 證據資料夾（必須新建），並以 `COMPENSATION_EVIDENCE_DIR` 傳入測試。`TEST_SHA256` 改為新值。其餘（階段閘、雜湊閘、埠閘、身分閘、憑證處理、停止與殘留檢查）未變。

### 14.3 Datadir 核對（執行前）

- 授權 datadir（第 2 節）：`D:\Project\MusicBased\discordBot_JukeBox101\.local\mysql-test\20261024-122528\data`
- 既有 `run-report.json` 的 identity datadir：同一路徑（結尾斜線不同）。
- `auto.cnf`：`server-uuid=f138d7c6-cf62-11f1-87be-c025a5a12877`，與授權一致。
- A 階段實際使用：`...\20261024-122528\data\`，UUID 相同。`log_error` 位於 run 目錄根（`...\20261024-122528\error.log`），證據資料夾位於 `.local\mysql-test\compensation-<stage>-<run>\`，皆不是 datadir。
- 結論：一致，未另建實例。

### 14.4 離線驗證（本輪，已執行）

| 項目 | 結果 |
| --- | --- |
| 新離線守衛測試（紅燈） | 測試檔尚未有清理函式時，收集失敗：`ImportError: cannot import name 'OPERATIONS'`（符號缺失，不是行為斷言；如實標示為**符號紅燈**） |
| 新離線守衛測試（實作後） | `tests/test_compensation_cleanup_guard.py`：**5 passed** |
| 變異檢查 M-a：證據寫入拋出錯誤 | 0 次 DELETE、rollback 1 次、未 commit（**阻擋清理**） |
| 變異檢查 M-b：證據寫入被靜默略過 | 順序測試失敗（`assert failed`）（**測試會抓到**） |
| 清理範圍 | 守衛測試檢查所有 DELETE 都帶 WHERE、參數只來自夾具範圍，且無 TRUNCATE、DROP |
| 啟動器靜態 | `py_compile` 通過；候選 DDL 切割為三句 CREATE TABLE；權限解析函式輸出與預期相符 |
| 未執行 | 任何 MySQL 實機測試（本節離線） |

### 14.5 TDD 紀錄

依 tdd skill 的垂直切片：先寫守衛測試，因符號不存在而紅燈，再實作，守衛測試全綠。紅燈是符號缺失，不是行為失敗；行為正確性由 M-a、M-b 變異檢查另行確認。實機 MySQL 測試沒有因本輪而重新紅綠，不宣稱為新一輪 TDD。

## 15. B 階段實機執行紀錄（2026-10-24，已執行）

- **指令**：`COMPENSATION_MYSQL_APPROVED_STAGE=B`，**未設定 PYTHONPATH**，執行 `.local\mysql-test\compensation_authorized.py --stage B --run-name run1`。啟動器自行設定 repo import 路徑，已驗證。
- **證據資料夾**：`.local\mysql-test\compensation-b-run1\`（報告、pytest 日誌、`schema-before\`、`schema-after\`、`cleanup\`）。
- **datadir**：與授權一致，`...\20261024-122528\data\`，UUID `f138d7c6-cf62-11f1-87be-c025a5a12877`。
- **A 基線（B 內重跑）**：全部通過（身分、migration 003 形狀、候選表不存在、營運表為空、exchange 授權快照）。
- **migrator 權限（DDL 前）**：`create_on_database=true`、`references_recommendations=true`、`references_credit_ledger=true`，通過；未增權、未換 root。
- **DDL**：三句 `CREATE TABLE` 依序執行，錯誤碼皆為 null，無重試。`schema-after` 型別確認：`closed_at` 與 `claimed_at` 為 `datetime(6)`，`delivery_fact` 為 ENUM，`chk_incidents_non_fault` 存在。
- **require_schema**：通過。
- **M 案例**：`pytest tests/test_compensation_mysql.py -m mysql`，**19 passed、1 xfailed、0 failed、0 error**（63.47 秒）。唯一 xfail 為 `test_m8_known_gap_...`（D-2）。
- **清理證據**：20 份計畫檔與 20 份結果檔（每測試一組）。每步計畫列數等於實際刪除數，清理後剩餘 0；所有被刪列的 `discord_user_id` 都在 `900000000000000100–199`，歌曲僅限 `Synth0001X1`、`Synth0002X2`。
- **停止與殘留**：伺服器正常關閉，退出碼 0；3307 無 Listen；無 mysqld 指向本 datadir；無 worker 殘留。
- **未處理**：D-2 仍是啟用補償前的阻塞；本次不宣稱補償功能已完整通過。

## 16. D-2 修正與實機驗證（2026-10-24，已完成，未 commit）

### 16.1 修正（最小範圍）

- **claim 入口**：全程式只有一處守衛決策，即 `SubmissionRepository.claim_delivery`（`bot/exchange_delivery.py` 唯一呼叫者）。`claim_grant` 等補償操作仍受 `_compensation_transaction` 的開關保護，未改。
- **修改前**：`guard_incidents = self._compensation_enabled`，開關關閉時守衛永遠為假。
- **修改後**：`guard_incidents = self._compensation_enabled or compensation_tables_present(cursor)`。開關開啟時短路，行為與修改前相同。
- **新增 `compensation_tables_present`**（`bot/database/compensation_schema.py`）：查詢 information_schema（SQLite 替身查 sqlite_master），只讀表名。全部不存在回傳 False；全部存在回傳 True；部分存在拋出 `CompensationSchemaMissing`（只列缺少的表名）；查詢失敗原樣拋出。沒有快取，因此不會因舊結果而錯誤放行。
- **守衛為真時**：`claim_reservation` 先執行完整 `require_schema`，欄位、唯一鍵或 CHECK 不符即拒絕；有未結案事故則回傳 `held_for_review`。
- **不變的部分**：開關關閉時 owner 補償方法仍拋出 `CompensationDisabled`；`reserve_exchange` 的阻擋與歌曲持有排除仍只在開關開啟時執行（見 16.5 殘留風險）。
- **未採用的做法**：直接把 `guard_incidents` 永久設為 True（會在表不存在時失敗，已排除）；快取探測結果（會在表被建立或移除後錯誤放行，已排除）。

### 16.2 測試替身與斷言（須如實說明）

- `tests/submission_persistence_fake.py`：新增 `information_schema.TABLES` 的回答（依 SCHEMA_SPEC 回報三表存在）。原本只會回答 STATISTICS 與 TABLE_CONSTRAINTS，遇到新探測會拋出 KeyError，這是替身缺口，不是修正邏輯錯誤。
- `tests/test_compensation_gate.py::test_the_general_exchange_path_issues_no_compensation_statement_when_the_switch_is_off`：**斷言語意改變，須審閱**。
  - 原斷言：開關關閉時，整個交換與 claim 都不得出現補償表名。
  - 新斷言：交換路徑仍然完全不得出現補償表名（與原斷言相同）；claim 路徑只允許唯讀語句（SELECT），不得寫入補償表。
  - 理由：D-2 要求表完整存在時，關閉開關的 claim 仍須讀取守衛。舊斷言與新要求直接衝突，因此改為「只讀、不寫」，不是放寬到允許寫入。
- 已移除兩處 strict xfail：`tests/test_compensation_gate.py`（改名為 `test_switched_off_with_all_tables_present_an_open_incident_still_holds_the_claim`）與 `tests/test_compensation_mysql.py`（改名為 `test_m8_a_switched_off_claim_still_holds_an_open_incident`）。

### 16.3 紅綠證據（真正的紅燈，依 tdd 紀錄）

| 層級 | 紅燈（修正前） | 綠燈（修正後） |
| --- | --- | --- |
| 離線：`test_compensation_claim_guard.py` 部分缺表 | `DID NOT RAISE CompensationSchemaMissing` | 通過 |
| 離線：同檔 schema 不相容 | `DID NOT RAISE CompensationSchemaMismatch` | 通過 |
| 離線：同檔全部缺表（正常交換） | 通過（回歸基線，修正前即通過） | 通過 |
| 離線：`test_compensation_tables_probe.py` | ImportError（符號缺失，屬符號紅燈，已如實標示） | 4 項通過 |
| 離線：gate 的 D-2 正面案例 | `AssertionError: assert 'sending' == 'held_for_review'` | 通過 |
| **MySQL（隔離實例）**：`test_m8_a_switched_off_...`，修正前執行 `--mode red` | `AssertionError: assert 'sending' == 'held_for_review'`（**實機紅燈**，證據 `compensation-d2-red-red1\pytest-d2.log`） | 見 16.4 |

### 16.4 實機與離線結果（修正後）

- **MySQL verify（`compensation-d2-verify-verify1`）**：**20 passed、0 failed、0 error、0 xfail**（85.19 秒）。包含 M1–M8、D-2 案例、M10、M11、M12、D1 拒絕案例。
- **離線受影響套件**（exchange、compensation、reserved_protection、identity_service_path，不含完整套件）：**197 passed、39 skipped、0 failed**。跳過的 39 項皆為未開隔離開關的 MySQL 案例。

### 16.5 基線、清理與停機

- **基線（red 與 verify 皆執行）**：身分通過；三表存在且定義與 run B 的 `schema-after` 一致（AUTO_INCREMENT 計數除外）；`require_schema` 通過；三表列數為 0；exchange 授權快照與 run A 完全一致；合成範圍外無殘留（`require_empty_baseline`）。未執行 DDL，未增權，未以 root 作為測試帳號。
- **清理**：verify 共 20 份計畫與 20 份結果，每步計畫列數等於實際刪除數，清理後剩餘 0，所有被刪列的使用者都在 `900000000000000100–199`。red 另有 1 組。
- **停機**：兩次皆為 `server_stopped=true`，退出碼 0，`normal_stop_timed_out` 未設定；3307 無 Listen，無 mysqld 指向本 datadir，無 worker 殘留。

### 16.6 已知殘留與阻塞

1. **交換端的阻擋與歌曲排除仍由開關決定**（`reserve_exchange` 的 `pending_block` 與 `_held_song_ids`）。開關關閉後，未結案事故的請求人仍可能再次交換，且補送歌曲不被排除。本輪修正只涵蓋 claim（原訂單送出），未擴大。這是另一個缺口，**啟用補償前仍須由 Owner 決定是否同樣修正**。
2. **未執行的既有 MySQL 回歸**：`tests/test_unknown_commit_mysql.py` 會經過 claim 路徑，但它使用的既有共用夾具清理不在本輪的紀錄範圍內（違反 D-1 的清理紀錄要求），因此**未在本輪實機執行**。離線 `test_exchange_unknown_commit.py` 已通過。
3. **表不存在的 MySQL 案例未實機驗證**：需要 DROP 三表，本輪未授權。該情境只在離線替身（`test_compensation_claim_guard.py::test_switched_off_with_all_tables_absent_...`）驗證。
4. **D-2 仍是啟用補償前的阻塞**：本輪只完成 claim 守衛的修正與驗證，不宣稱補償功能已完整通過；正式 `/exchange` 與真實 DM 未驗證。

### 16.7 檔案與雜湊（下一輪 commit 候選）

| 檔案 | 狀態 | SHA-256 |
| --- | --- | --- |
| `bot/database/compensation_schema.py` | 修改（新增 `compensation_tables_present`，LF） | `19b322848b05370af7c61af64748edcb0f718a23cb14409449598d38ba5beb24` |
| `bot/database/submission_repository.py` | 修改（`claim_delivery` 守衛與 import，**工作樹為 CRLF，HEAD 為 LF，既有差異**） | `741ff8835e8df90938fcdbd12f64a9fc6529794c4c6fb483b46651cd957e5499` |
| `bot/database/exchange_delivery_store.py` | 修改（僅 docstring；工作樹 CRLF） | `dc77fa385d864c8d243aad1c234246f529bdca8ca3048e28d1f63fcfd0c77903` |
| `tests/submission_persistence_fake.py` | 修改（presence 探測，CRLF） | `43003954a963a4d21883e0ab76d39d85c373f93ab7e4c5c0abe7b5b9dda07174` |
| `tests/test_compensation_gate.py` | 修改（移除 xfail、斷言改為只讀，CRLF） | `971a1bff74561f2d04916dbe9c3bd52fa949e819c1321e50eb2bb58a33ce3def` |
| `tests/test_compensation_mysql.py` | 修改（移除 xfail、改名；已釘選於啟動器） | `072502747f840f2da6b628f10f91eb4ef1271bbd1f90e56b1b6b2762d07e77e5` |
| `tests/test_compensation_claim_guard.py` | 新增（離線） | `6a6eea03f1b1daa87d9da0e0b7d4f7e7d4089ba48911b261726228a7f2f9733c` |
| `tests/test_compensation_tables_probe.py` | 新增（離線） | `5a959b443a359d66dda49ff27d9c625a34c2d08174de25600b1206f0f870f040` |
| `tests/test_compensation_cleanup_guard.py` | 新增（離線，清理守衛） | `05e0ec6ba55113bcd880aab0c731fce85c8f9985a4f840cd810af0d2120eb477` |
| `docs/compensation-mysql-verification-plan.md` | 修改（本文件） | — |
| `.local/mysql-test/compensation_d2_authorized.py` | 新增（本地，已被 `.gitignore` 排除） | `7ad18808e5711c54a308b165692b7d25e5e5bc34318868e7aefae018370d5198` |
| `.local/mysql-test/compensation_authorized.py` | 修改（本地，已被排除） | `5dedf4368ff8b756a99fc9863cc928283fa529767d3e6a88a7a260e609c5e51c` |

**不應提交**：工作樹中其他工作（batch-B、review、decision_snapshot、ledger_integrity 等 untracked 檔），以及私人 PDF（`Re Issue 576880673 ...pdf`）。

**建議的精準提交區塊**（需逐檔 `git add` 並核對 staged diff，不得使用 `git add -A`）：
1. `compensation_schema.py` 的 `compensation_tables_present` 區塊。
2. `submission_repository.py` 中 `claim_delivery` 與 import 行。
3. `exchange_delivery_store.py` 的 `guard_incidents` docstring。
4. 上列三個測試檔（gate 的兩處、fake 的 presence、新增的兩個離線檔）。
5. 離線清理守衛測試 `test_compensation_cleanup_guard.py` 與 MySQL 檔（若同批提交）。

**注意**：`submission_repository.py` 與 `exchange_delivery_store.py` 的 CRLF 與 HEAD 的 LF 不同，提交前須確認 diff 只含本次區塊，不可把整檔行尾變更一起提交。

### 16.8 交接摘要

- **狀態**：D-2 claim 守衛已修正並通過離線與實機驗證；MySQL 補償檔 20/20 通過。
- **下一步最小修正**：Owner 決定是否也把交換端的阻擋與歌曲排除改為「表存在即生效」（見 16.6 第 1 點），並決定是否在隔離實例上執行 `test_unknown_commit_mysql.py`（需先把其清理納入紀錄）。
- **未授權事項**：DROP、增權、commit、push、真實 DM、正式 `/exchange`。

## 17. 交換端保護、unknown-commit 回歸與收尾（2026-10-24，已完成，未 commit）

### 17.1 claim 守衛修正（第 16 節）的證據

已於第 16 節記錄，本節不重複。第 16 節的離線與實機結果仍有效，未與本節合併。

### 17.2 交換端保護（本輪）

- **修正位置**：`bot/database/exchange_reservation.py::reserve_exchange`。
- **修改前**：`pending_block` 與 `_held_song_ids` 只在 `compensation_enabled=True` 時執行。
- **修改後**：先完成同 key 重放判斷（重放不受阻擋），再計算 `guarded = compensation_enabled or compensation_tables_present(cursor)`。`guarded` 為真時先執行 `require_schema`，再套用阻擋與歌曲排除。
- **不變的部分**：開關關閉時 owner 補償操作仍拋出 `CompensationDisabled`；`claim_grant` 等補償操作仍受開關保護。
- **三表不存在且開關關閉**：`compensation_tables_present` 只查 information_schema，回傳 False，之後不再碰補償表。
- **部分缺表**：拋出 `CompensationSchemaMissing`，交換失敗，不寫入任何列。
- **schema 不相容**：`require_schema` 拋出 `CompensationSchemaMismatch`，交換失敗。
- **探測失敗**：原樣拋出，不吞錯。

### 17.3 紅綠證據

| 層級 | 紅燈（修正前） | 綠燈（修正後） |
| --- | --- | --- |
| 離線 `test_compensation_exchange_guard.py` | 6 項失敗：阻擋、開放 grant 阻擋、uncertain 歌曲持有、部分缺表拒絕、schema 不相容拒絕、探測失敗拒絕（`AssertionError`、`DID NOT RAISE`） | 10 項通過（含 4 項回歸：重放、釋放、表全缺正常、Owner 操作仍拒絕） |
| **MySQL** `m13`、`m14`、`m15`（`--mode red`，修正前） | 3 項失敗：`AssertionError`（證據 `compensation-d2-red-d3red\pytest-d2.log`） | `--mode verify` 通過（見 17.4） |

- 注意：m13 的紅燈訊息是 `INSUFFICIENT_CREDITS` 而非 `PENDING_REVIEW`，因為修正前阻擋未生效，請求在餘額檢查時就被拒絕。修正後阻擋先於餘額檢查，這是預期順序。
- 離線紅燈先於修正取得；實機紅燈也在修正前取得，兩者都不是事後補證據。

### 17.4 實機結果（`compensation-d2-verify-d3verify`，同一實例、既有帳號與 datadir）

| 檔案 | 結果 |
| --- | --- |
| `tests/test_compensation_mysql.py` | **23 passed**（M1–M8、M8 已知缺口、M10、M11、M12、D1，加上新增的 m13、m14、m15） |
| `tests/test_unknown_commit_mysql.py` | **4 passed**（lost ack、fail before commit、definite statement failure、caller replays） |
| 合計 | **27 passed、0 failed、0 error、0 xfail、0 skipped**（69.64 秒） |

- 這是本輪 unknown-commit 的**實際執行結果**，與歷史分批結果（第 `4f1e3f1` 的 21/1、路由修正單項 1 passed）**不合併**。
- 注入方式：第一條連線以測試用 wrapper 注入故障（`lost_ack_after_commit`、`fail_before_commit`、`fail_statement`）。這是注入式故障，不是真實網路中斷。DM adapter 為 fake，未發送真實 DM。

### 17.5 清理與基線

- **基線**（verify 執行前）：身分一致；三表存在且定義與 B 階段 `schema-after` 一致（AUTO_INCREMENT 除外）；`require_schema` 通過；三表列數為 0；exchange 授權快照與 A 階段一致；合成範圍外無殘留。
- **清理**：27 份計畫與 27 份結果，每步計畫列數等於實際刪除數，清理後剩餘 0，所有被刪列的 `discord_user_id` 都在 `900000000000000100–199`。未知提交檔的 4 組也已納入同一機制（先保存計畫，成功後才刪除）。
- **未使用** DDL、DROP、TRUNCATE、增權或重建。
- **停機**：正常關閉（退出碼 0，未強制結束）；3307 無 Listen；無 mysqld 指向本 datadir；無 worker 殘留。

### 17.6 測試檔與斷言語意的改變（須審閱）

1. **`tests/test_unknown_commit_mysql.py`**：`funded_pair`（共用夾具，清理不在紀錄內）改為 `guarded_funded_pair`（開關關閉、三表存在、清理前保存證據）。測試內容與斷言不變。
2. **`tests/test_compensation_gate.py::test_the_general_exchange_path_issues_no_compensation_statement_when_the_switch_is_off`**：原斷言要求開關關閉時交換與 claim 都不得提及補償表名。D-2 與 D-3 要求表存在時守衛會唯讀查詢，因此改為「只允許 SELECT，不得寫入補償表」。這是 D-2、D-3 直接要求的新不變量，不是放寬到允許寫入。
3. **`tests/submission_persistence_fake.py`**：新增測試用旋鈕（表存在性、缺欄、探測失敗、grant 列），並回答 `information_schema.TABLES` 探測。這些旋鈕只存在於測試替身中。
4. **`tests/test_compensation_mysql.py`**：夾具重構為 `guarded_steps` 生成器，供補償檔與未知提交檔共用；新增 m13、m14、m15。

### 17.7 剩餘限制與阻塞

1. **表不存在的 MySQL 案例未實機驗證**（需要 DROP，未授權）。該情境只在離線替身驗證。
2. **D-2 與 D-3 的守衛只在 claim 與交換入口**。`claim_grant`、`owner_*` 等補償業務操作仍由開關決定，與本輪要求一致（不因守衛而重新啟用補償）。
3. **派送守衛與交換守衛的探測是每次呼叫一次**，沒有快取。這會增加少量 information_schema 查詢，屬於刻意的代價，避免快取造成錯誤放行。
4. **真實網路中斷**未驗證；unknown-commit 案例只驗證注入式故障。
5. **真實 DM 與正式 `/exchange`** 未驗證，本輪不宣稱。
6. **是否可收尾**：本階段的離線與實機目標已達成，**可以進入提交準備**；但補償功能仍未啟用，啟用前仍需 Owner 決定正式啟用與真實 DM 驗證範圍。

### 17.8 交接與精準提交候選

| 檔案 | 狀態 | 說明 |
| --- | --- | --- |
| `bot/database/compensation_schema.py` | 修改 | `compensation_tables_present`（第 16 節） |
| `bot/database/submission_repository.py` | 修改 | `claim_delivery` 守衛（第 16 節）；工作樹 CRLF，HEAD LF |
| `bot/database/exchange_delivery_store.py` | 修改 | 僅 docstring（第 16 節）；工作樹 CRLF |
| `bot/database/exchange_reservation.py` | 修改（本輪） | `reserve_exchange` 的 guarded 判斷與 import；工作樹 CRLF |
| `tests/submission_persistence_fake.py` | 修改 | 測試旋鈕與 presence 探測；工作樹 CRLF |
| `tests/test_compensation_gate.py` | 修改 | 移除 xfail、斷言語意（第 16、17 節）；工作樹 CRLF |
| `tests/test_compensation_mysql.py` | 修改 | 夾具重構、m13–m15、移除 xfail |
| `tests/test_unknown_commit_mysql.py` | 修改（本輪） | 改用 `guarded_funded_pair` |
| `tests/test_compensation_claim_guard.py` | 新增 | 離線（第 16 節） |
| `tests/test_compensation_tables_probe.py` | 新增 | 離線（第 16 節） |
| `tests/test_compensation_cleanup_guard.py` | 新增 | 離線（第 16 節） |
| `tests/test_compensation_exchange_guard.py` | 新增（本輪） | 離線 |
| `docs/compensation-mysql-verification-plan.md` | 修改 | 第 14–17 節 |
| `.local/mysql-test/compensation_d2_authorized.py` | 本地（已排除） | 不提交 |

**雜湊（提交前核對用，完整 SHA-256）**：
- `bot/database/exchange_reservation.py`: `67399158da2b0631effe7357253cd43562db50ea58b466f63079b69329a4ef6d`
- `bot/database/compensation_schema.py`: `19b322848b05370af7c61af64748edcb0f718a23cb14409449598d38ba5beb24`
- `bot/database/submission_repository.py`: `741ff8835e8df90938fcdbd12f64a9fc6529794c4c6fb483b46651cd957e5499`
- `bot/database/exchange_delivery_store.py`: `dc77fa385d864c8d243aad1c234246f529bdca8ca3048e28d1f63fcfd0c77903`
- `tests/submission_persistence_fake.py`: `be199d4f539b7f4f4b0ab913964fdb280c4c8738373e7b3b61df95405db601bb`
- `tests/test_compensation_gate.py`: `97b732be9f4d67b6c92dd1b8ffa99884a9fe404fabc9f19d681a7334b4432dec`
- `tests/test_compensation_mysql.py`: `41ac70904fb0071e16da831ba37f89eae9818ee26c508214d032fa7bd81b652b`
- `tests/test_unknown_commit_mysql.py`: `b096bfca2a8c26e6f28da0dbf0ebd09d20e0715449e8c32af535a684336ec8d2`
- `tests/test_compensation_exchange_guard.py`: `452f50392a7f00cf2db3d9f45d52628577d77e3fdedc8cff07cd9c9887d93561`

**不應提交**：batch-B、review、decision_snapshot、ledger_integrity 等其他工作樹內容，私人 PDF，以及 `.local` 內的所有檔案。

**提交前必須注意**：`submission_repository.py`、`exchange_delivery_store.py`、`exchange_reservation.py`、`submission_persistence_fake.py`、`test_compensation_gate.py` 在工作樹為 CRLF，HEAD 為 LF。提交前必須以 `git diff --ignore-cr-at-eol` 確認只含本次區塊，不能把整檔行尾變更一併提交。

### 17.9 交接摘要

- **已完成**：claim 守衛（第 16 節）與交換端保護（本節）均已通過離線與實機驗證；unknown-commit 實機 4/4 通過；清理與停機檢查乾淨。
- **剩餘**：表不存在的實機情境（未授權 DROP）、真實網路中斷、真實 DM、正式 `/exchange` 與補償啟用。
- **下一步**：依 17.8 的區塊做精準提交（需 Owner 另行授權 commit／push）。
