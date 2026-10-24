# 補償功能隔離 MySQL 驗證計畫（僅計畫，未執行，2026-10-24）

**性質**：計畫與授權文字。**本文件不授權任何執行。** 沒有執行 DDL，沒有啟動或連線 MySQL，沒有解密憑證，沒有新增帳號或增權，沒有操作 Discord、DM，沒有提交或推送。執行前須由 Owner 依第 7 節的授權文字另行核准。

## 1. 目標與範圍

在既有的隔離 MySQL 8 實例上，驗證候選 DDL `docs/compensation-candidate-004.sql` 與補償程式的實際行為：約束是否由引擎強制、交易與鎖是否符合離線模型、以及跨程序競爭的結果。離線 SQLite 通過**不等於** MySQL 通過。

## 2. 目標實例與身分檢查

| 項目 | 規定 |
| --- | --- |
| 實例 | 既有隔離實例，`127.0.0.1:3307`，版本需 ≥ 8.0.16 |
| 資料庫 | `jukebox101_test`（由 `tests/isolated_mysql_target.py` 的身分檢查確認） |
| 禁止 | 任何 3306 連線（既有 `MySQL80` 服務）；`.env`；root 作為測試帳號 |
| 身分檢查 | port、版本、UUID 釘選、datadir 須為 `run_dir/data`、log_error 須在 `run_dir` 之下、socket 不得為 `MySQL` |

## 3. DDL 目標、順序與影響

**DDL 目標**：只有 `docs/compensation-candidate-004.sql` 的三個 `CREATE TABLE`：`exchange_incidents`、`exchange_redelivery_grants`、`exchange_incident_events`。

**影響（精確）**：新增三張表與其索引與約束；**不 ALTER 任何既有表**，不改 `credit_ledger`、`recommendations`、`users`；不寫入任何資料列。

**順序**：
1. 唯讀確認基底 schema、`jb2_control.schema_version = '2b2-b1'`、基底 migration 002、003 已套用（只用 `SELECT`）。
2. 執行前取得 schema-only 快照（`mysqldump --no-data`，帳號須為已授權者），存入 `run_dir`，作為證據。
3. 以**獲授權的 migration 帳號**執行 004 的三個 `CREATE TABLE`。DDL 會隱含 commit，無法 rollback。
4. 唯讀確認三張表存在，並以 `require_schema`（只讀 `information_schema` 與 `SELECT ... LIMIT 0`）逐一核對欄位、唯一鍵與具名 CHECK。
5. 執行第 4 節的測試。

**回復方式（需另行授權）**：若必須撤回，只能 `DROP` 這三張新表，且須先確認三張表皆無資料；不得 `DROP` 或 `TRUNCATE` 任何既有表。

## 4. 測試案例（只在隔離實例執行）

| 編號 | 案例 | 通過條件 |
| --- | --- | --- |
| M1 | 同一事故兩個程序同時結算 | 退款一次、補送資格一次；`delivery_refund` 列恰好一筆 |
| M2 | 同一補送資格兩個程序同時 claim | 只有一個 `claimed`，另一個不派送 |
| M3 | 退款不補送的結案路徑 | 退款一次，`closure = refund_only`，無補送資格，阻擋解除 |
| M4 | 同 key 重放與結算後重複結算 | 不再退款、不再派送、不新增補送資格 |
| M5 | claim 與核准的兩種順序 | 認領先：核准被拒，原訂單送出一次；核准先：認領被保留並寫入 `claim_held` |
| M6 | claim 與結算的兩種順序 | 結算先：認領回傳 `failed`，不派送；認領先：事故為 `void`，不退款 |
| M7 | `not_sent` 與 `uncertain` 的持有與阻擋 | `not_sent` 未結案前阻擋且持有；`no_further_action` 後釋放；`uncertain` 阻擋且持有，直到 Owner 處置 |
| M8 | 功能關閉的回歸 | 關閉時一般交換與 claim 不查詢補償表；刪除補償表後一般 claim 照常派送 |
| M9 | 缺表與缺約束的拒絕 | 只在**離線鏡像**測試；MySQL 上不為了測試而刪改既有或候選表 |
| M10 | 約束由引擎強制 | 以 `INSERT` 嘗試違反 `UNIQUE(recommendation_id)`、`UNIQUE(refund_ledger_id)` 與 CHECK，預期被拒；測試列須以隔離交易執行並 `ROLLBACK` |
| M11 | 非系統故障（D1）結案的一次退款 | 兩個程序同時對同一 `not_system_fault` 事故呼叫結案（`confirmed_undelivered = True`） | 恰好一個 `closed_refunded`，另一個為 `already_closed` 或被 writer gate 序列化後拒絕且無寫入；`delivery_refund` 列恰好一筆；無補送資格；無零差額列；事故終態為 `non_fault_refunded`，`closure = non_fault_refund` |
| M12 | D1 結案與 claim 競爭 | 程序 A 對 `not_system_fault` 事故結案；程序 B 對同一保留呼叫 claim（`guard_incidents` 開啟） | 合法終態只有下列兩種，且**不得同時成立**：(a) 結案先於 claim：claim 回傳 `held_for_review`，DM 送出次數為 0，保留不改變，退款一次；(b) claim 先於結案：結案回傳 `NOT_STRANDED`，無退款，DM 送出至多一次。**不預設 claim 必定通過**；任何「退款結案且 DM 已送出」的結果即為失敗 |

每個跨程序案例須設定 timeout，逾時視為失敗，並保留 stdout、stderr 與退出碼。

## 5. 停止條件與證據保存

**立即停止**，不再執行後續案例，若發生：身分檢查失敗；任何對 3306 的連線；非預期的 1146；死結或鎖等待反覆出現；任何 DM 或 Discord 呼叫；任何非 `jukebox101_test` 的寫入。

**證據保存**：保留 schema-only 快照、每個案例的 stdout、stderr、退出碼與查詢結果摘要，存於 `run_dir`，不進入 Git。輸出中不得包含密碼、token、連結與私人 ID；使用者 ID 以角色代稱記錄。

**清理**：測試資料留在隔離資料庫作為證據；**不自動清理**。任何刪除列或刪除表的動作都需要另行授權。

## 6. 本計畫不包含

- 不包含 Discord 指令、真實 DM、正式 `/exchange` 或補償的啟用。
- 不包含 3306 或正式資料庫。
- 不包含 `.env` 或憑證檔的讀取。
- 不包含新增帳號、增權或修改既有權限。

## 7. 授權文字（可直接回覆，未經 Owner 核准前不得執行）

> 我授權在隔離 MySQL 實例 `127.0.0.1:3307` 的 `jukebox101_test` 資料庫，執行 `docs/compensation-candidate-004.sql` 中的三個 `CREATE TABLE`，並以已獲授權的 migration 帳號執行（不得使用 root 或應用程式帳號）。我授權執行第 4 節 M1 至 M8、M10、M11 與 M12 的案例，M9 只在離線鏡像執行。我授權在 `run_dir` 保存 schema-only 快照與測試證據。
>
> 我**不**授權：連線 3306；讀取 `.env` 或解密憑證；新增帳號或增權；ALTER 或刪除任何既有表或資料；`DROP` 或 `TRUNCATE`（回復需另行授權）；Discord 指令、DM；提交、推送；啟用正式交換或補償功能。

## 8. 進入執行前仍須解決的事項

1. Q-A 已由 Owner 決定並離線實作（D1，見 `docs/exchange-compensation-model.md` 第 5 節）；Q-B 維持保留。
2. 候選 DDL 已於 D1 變更：`exchange_incidents.state` 新增 `non_fault_refunded`，`closure` 新增 `non_fault_refund`，並新增具名 CHECK `chk_incidents_non_fault`。授權文字須明列此變更。
3. 下一階段第一步是核對既有隔離基線（唯讀），再取得有限範圍的 DDL 授權。SQLite 鏡像與 MySQL `DATETIME(6)` 的型別差異（`closed_at` 等）列為核對事項。
2. 核准第 7 節的授權文字，並指定 migration 帳號（已存在的帳號；本計畫不建立新帳號）。
3. 確認 migration 帳號對 `jukebox101_test` 具備 `CREATE` 權限，且不需要增權。若需要增權，須另行授權。
