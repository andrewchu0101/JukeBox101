# Batch B 真實 MySQL Test Harness（已實作，真實驗收待執行）

> 最新狀態（2026-10-16）：**RECOVERY_SUCCESS**；四條 FK DDL／DML Commit 全部 ACKNOWLEDGED，15 PK 零殘留、非 Manifest 異動 0、Control 0／0 與三開關 OFF，原 FK／Schema／Migration 已恢復驗證，Exit Code 0。依使用者已驗收結果記錄，詳見 [成功紀錄](recovery-success-checkpoint.md)。Session 仍 Blocked；Batch B 真實 MySQL 測試尚未完成。下文保留原事故與授權方案歷史，所有舊「最新／Pending／尚未成功／下一次授權」描述均不覆蓋本段，也不是重新執行許可。本輪未重跑或連線。

## 歷史紀錄（不代表當前執行授權）


> 1826 最新狀態（2026-10-16）：已失敗的一次性 Recovery 不得重試。已離線改為不同名稱、ADD 優先的四步 FK bridge；尚未執行真實 DDL/DML。唯讀現況已確認原 FK NO ACTION、原 Schema 指紋、15 PK／Control／Registry 不變，狀態 READY_FOR_NEW_RECOVERY_APPROVAL。先前單條 ALTER 同名 ADD/DROP 方案已失效，不能執行。新範圍見 [1826 修復方案](batch-b-recovery-fk-plan.md)。

> 最新狀態（2026-10-16）：已完成暫時 self-FK CASCADE 的離線精確 Recovery 設計與獨立預設停用入口；尚未執行真實 FK DDL／Recovery，Session 仍 Blocked。完整範圍與失敗矩陣見 [Recovery FK 授權方案](batch-b-recovery-fk-plan.md)。一般 Harness 在原 NO ACTION 結構下會於開啟測試寫入前停止，不能直接重跑舊 A/B。

## 現在先停止一般 A＋B 啟動

2026-10-16：獨立 **-RestoreRecovery** 寫入模式已完成離線實作，**未執行、仍待 Recovery Execution 授權**。唯一命令、確認字串、approved fingerprint、既有 Harness 密碼與全部停止條件見 [Recovery Implementation](batch-b-recovery-investigation.md)。本輪不重跑 Stage A/B、不解除舊 Session Block。下方「沒有可寫 Recovery CLI」等敘述屬前次 planning 歷史，最新狀態以本段及報告頂端為準。

最新 Controlled Recovery Planning 見 [R6–R10 與精確交易提案](batch-b-recovery-investigation.md) 及 [15 PK Manifest](batch-b-recovery-manifest.json)。原 Journal 的 FK error target 與 actual foreign_key_rules 原文尚未齊備；目前沒有可寫 Recovery CLI，不能把唯讀模式當成恢復命令。已有共用版本圖預檢與嚴格 manifest/counter 純函式，沒有恢復寫入授權。

Run `14d8aee30b034a9883e9f71cdec8146e` 發生 cleanup 1451，環境尚待恢復。不可重跑 Stage A／B 或解除 session block。請使用 [Recovery investigation](batch-b-recovery-investigation.md) 的同入口 **-RecoveryJournal** 唯讀模式，先取得現在的 23 表／Control 狀態；該模式不執行帳號配置、test switches 或 cleanup。原 journal 未修改。恢復寫入仍需另行核准具體清單。

## A_PRECHECK_FAILED OperationalError 安全診斷

第二次使用者執行回報只含 OperationalError，沒有實際數字錯誤碼，因此**根因尚未確認**。原 Python main 刻意不輸出原始 exception，但也遺漏安全可用的 args[0] 整數；PowerShell 又會丟棄 raw stderr，故先前報告無法區分連線、認證、metadata 或權限失敗。現在保留固定子步驟與數字碼，不輸出 SQL、參數、密碼、連線字串、raw exception／traceback 或自動重試。

| failure_step | 可能失敗的既有呼叫 |
|---|---|
| A_DBA_CONNECT | open_connection：TCP、認證、選定測試庫及 SET time_zone 初始化 |
| A_DBA_IDENTITY_AND_VISIBILITY | DATABASE／CURRENT_USER／port／UUID／VERSION／mandatory_roles／FK 的 SELECT；DBA SHOW GRANTS |
| A_EXCLUSIVE_LOCK | GET_LOCK |
| A_SCHEMA_AND_BASELINE_METADATA | information_schema.TABLES／TRIGGERS、23 表 SHOW CREATE／SELECT、KEY_COLUMN_USAGE（本庫及跨庫 incoming FK） |
| A_EXISTING_ACCOUNTS | mysql.user 非認證欄位 SELECT，App／Migrator SHOW GRANTS |
| A_HARNESS_ABSENCE | mysql.user 的 Harness 名稱／host SELECT |
| A_CONSTRAINT_METADATA | TABLE_CONSTRAINTS／CHECK_CONSTRAINTS／KEY_COLUMN_USAGE SELECT |
| A_APPLICATION_LOGIN_AND_PRIVILEGES | 獨立 App 連線及身分、角色、session timeout、privilege metadata／SHOW GRANTS／column grants；rollback／close |
| A_DBA_CLOSE | DBA 連線釋放 |

這些是固定操作群組，部分群組含多個 SQL，不宣稱已定位到單一 SQL。建立 Harness、25 grants、權限核對、unlock、Harness 回連屬 Stage A 後續階段，另有固定 A_HARNESS_CREATE_LOCKED／A_HARNESS_GRANT_01…25／A_HARNESS_GRANTS_VERIFY 等標籤；stage 仍保留 A_PROVISION_FAILED／A_PRIVILEGE_VERIFY_FAILED 的區別。A_PRECHECK_FAILED 不表示已執行這些寫入。

新增欄位：current_step、failure_step、mysql_error_number（僅 PyMySQL MySQLError 的 exact int，0…65535；缺少或非整數時省略）。error_type 與既有固定 safety_code 保留；不以錯誤碼自動判定完整根因或補權限。第一次失敗保留，即使後續 DBA close 也失敗，另報 dba_close_failed／dba_close_error_type／dba_close_mysql_error_number，不覆蓋原碼與步驟。PowerShell 允許清單同步更新，拒絕帶 SQL 空白等任意文字的步驟、字串型錯誤碼、boolean 或越界數字。

本輪新增 22 個 Python 離線案例，涵蓋連線／認證／權限／metadata／中斷碼的合成失敗、真實 precheck wiring、無效錯誤碼、完整 main 的安全 JSON，以及主次錯誤保留。回歸為 **748 Passed／0 Failed／23 Skipped**，`READONLY_GUARD_BLOCKED_EVENTS=[]`；PowerShell Mock **37 項斷言通過**。所有數字錯誤碼均來自合成測試，不是這次 MySQL 實際錯誤。沒有連線、重新啟動 A＋B、配置帳號、執行權限 SQL 或修改 Schema／Migration。既有入口已可供使用者後續安全重試，以取得實際 failure_step／數字碼；不保證 Precheck 已通過。

## FOREIGN_KEY_DRIFT 離線修復紀錄

`reviewed_foreign_keys()` 原先直接按分號切割檔案。原始 `schema.sql:38` 的 submissions 表內註解含 `user/song;`，因此 CREATE 區塊被切斷，漏掉 `fk_submissions_user` 與 `fk_submissions_song`。已沿用 migration_plan 的方式，在切割前移除 `--` 行註解。修正前預期為 27 個 FK 欄位關聯，修正後為 29 個（原六表 12 個、新表 17 個；複合 FK 每個欄位位置分別計算）。沒有修改 Schema、Migration 或放寬實際 metadata 相等檢查。

這是已離線重現的 Harness 缺陷；**實際 MySQL metadata 未取得，尚不能確認資料庫只有這兩個差異或沒有其他異常**。現有比較包含子表／欄位／位置及參照 schema／表／欄位，不比較 constraint name 或 ON DELETE／ON UPDATE，故名稱與動作文字不會觸發此錯誤；大小寫仍嚴格比較，沒有加入大小寫折疊。沒有執行 A＋B、連線或要求憑證。11 個新回歸涵蓋註解分號、誤導性註解、複合位置、真正缺漏／額外 FK／參照／schema／大小寫差異；合併離線結果 **726 Passed／0 Failed／23 Skipped**，防護事件為空。

若要取得 actual，可在既有已登入且有 metadata 可見權限的本機 MySQL session 一次執行下列唯讀查詢；不重新啟動 launcher 或輸入密碼。不需要輸出憑證、authentication 欄位或業務資料。若沒有既有 session，先停止並回報此限制。

```sql
SELECT k.TABLE_NAME, k.CONSTRAINT_NAME, k.COLUMN_NAME, k.ORDINAL_POSITION,
       k.REFERENCED_TABLE_SCHEMA, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME,
       r.UPDATE_RULE, r.DELETE_RULE
FROM information_schema.KEY_COLUMN_USAGE AS k
JOIN information_schema.REFERENTIAL_CONSTRAINTS AS r
  ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA
 AND r.TABLE_NAME = k.TABLE_NAME AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME
WHERE k.TABLE_SCHEMA = 'jukebox101_test' AND k.REFERENCED_TABLE_NAME IS NOT NULL
ORDER BY k.TABLE_NAME, k.CONSTRAINT_NAME, k.ORDINAL_POSITION;
```

## 單次互動式 A＋B 入口（Credential Bootstrap）

最新 A＋B 授權已包含建立 Harness 與執行 23 個真實案例；下方較早的「尚未授權」描述是歷史紀錄。此次只完成離線啟動器驗證，尚未連線、配置帳號或執行真實測試。不要重新執行 Migration 或先手動 source grants SQL。

在本機互動式 PowerShell 執行一次：

```powershell
powershell.exe -NoProfile -File "D:\Project\MusicBased\discordBot_JukeBox101\scripts\run-batch-b-acceptance.ps1"
```

先停止其他測試庫寫入／DDL 並輸入 `EXCLUSIVE`，再輸入本機 DBA 名稱、**先前核准的 server UUID**，以及三次遮罩密碼：DBA、Application、新 Harness。新 Harness 密碼請以使用者自行核准的安全方式保管，程式不保存密碼；不要貼到聊天。不得把當次未核對的 UUID 當成獨立核准指紋。若不知道 UUID，停止啟動，不以自動採信目前伺服器代替核對。

此入口重用既有 25 筆 grants、Harness、安全檢查與 `tests/test_batch_b_mysql.py`。DBA 必須已具有完整 global SELECT／TRIGGER／CREATE USER 與 GRANT OPTION（或 ALL PRIVILEGES），程式不會補授權。DBA 身分只接受本機 `@127.0.0.1`／`@localhost`，Application 與 Harness 實際身分仍嚴格要求 `@127.0.0.1`。查詢不讀 authentication_string。完整可見的 Trigger、FK、23 表、空業務基線、Registry、原帳號狀態與 Application ACL 都在 CREATE USER 前檢查。任何不符即停止。

Stage A 先取得既有 Harness advisory lock，拒絕任何 host 的既有同名 Harness；只建立鎖定帳號，再執行精確比對過的 25 筆 grants，核對全部 table／column／額外權限、原帳號及基線未變，再解鎖並回連。部分失敗不重跑或 DROP；只對已確認由本次建立的帳號嘗試鎖回。CREATE 成功但回應遺失時，可能留下鎖定帳號，須人工核對，不能盲目重啟。

Stage A 結束關閉 DBA 連線並移除其程式參照，再進 Stage B；DBA 密碼不進任何環境變數或測試 worker。Stage B 原樣使用 Baseline → Enable → 23 Real Tests → 精確 Cleanup → Restore，最後以新 Harness 連線獨立比較全部 23 表（包含 Registry／Control）與結構。原 Application／Migrator 身分、grant 及 migrator 鎖定狀態在 Stage A 前後核對；丟棄 DBA 後不宣稱再次獨立驗證 mysql 帳號 metadata。持續依賴獨占承諾，禁止同期管理變更。

### 憑證傳遞與剩餘風險

- PowerShell `SecureString` → 短暫 managed plaintext → Python 匿名 stdin pipe；密碼不進命令列、父程序環境、檔案或報告。每個密碼以單行 JSON 編碼，支援特殊字元；送出後 dispose SecureString 並關閉 pipe。
- 為相容既有 profile loader，Python **僅在 Stage B 子程序生命週期內**建立 TEST_DB_PASSWORD／TEST_HARNESS_DB_PASSWORD。Application worker 沿用既有 private multiprocessing IPC；Harness／DBA／Migration namespace 不傳入業務 worker。finally 清除 credentials adapter 並關閉 opt-ins，父 PowerShell 的 JUKEBOX_RUN_* 也關閉。沒有新增永久環境變數或任何 credential store。
- Python／.NET immutable strings、PyMySQL 的連線／SQL buffer 不能保證實體記憶體抹除；同使用者 debugger、系統管理員、記憶體 dump、pagefile、惡意本機軟體仍可能取得明文。程序環境也不是對本機特權使用者的秘密保管箱。CREATE USER 傳給本機 MySQL 時含密碼；不保證額外安裝的第三方監控／攔截器會遮罩。此設計沒有新增 TLS 或對惡意本機伺服器的密碼學認證。
- 原始 Python／MySQL stderr 及 pytest traceback 不輸出；僅回傳固定 stage、error class／固定 safety code、真實 pytest counts、exit code 及既有非機密 journal。不要使用 PowerShell Transcript、debugger、SQL debug logging 或錄製輸入。硬斷電、強制結束 Python 無法保證 finally；不要關閉執行視窗或在失敗後盲目重啟，應依 journal 核對。
- 不會重新套用 Migration、解鎖 Migrator、更改 App grants、發送 Discord／YouTube 請求或啟用正式 Nickel。測試用開關只由既有 Harness 管理。READY、Mock Passed、Migration 成功均不等於真實業務驗收。

成功只回報 `COMPLETE_MANUAL_ACCEPTANCE_PENDING`，且要求 23 Passed／0 Failed／0 Skipped 和獨立基線一致。失敗保留 sanitized stage／journal，不自動 Accepted 或開始 Batch C。

離線驗證命令：`python -B -m pytest tests/test_batch_b_acceptance.py -p no:cacheprovider -p no:logging -o addopts= -q --tb=short`（關閉所有 real-test opts）；`powershell.exe -NoProfile -File tests/test_batch_b_acceptance_launcher.ps1`。前者只用合成憑證／metadata spies，後者連 Python child 都不啟動。

本次離線結果：新 Python bootstrap 測試 34 Passed；與下方完整防護回歸命令合併執行為 **715 Passed／0 Failed／23 Skipped**，`READONLY_GUARD_BLOCKED_EVENTS=[]`。Windows PowerShell 5.1 Mock 為 **31 項斷言通過**，包含 Unicode／surrogate pair／引號／換行的 ASCII pipe framing、SecureString dispose、禁止繼承密碼／Migration 開關、錯誤遮罩與 exit code。23 個 Skipped 是未執行的真實 MySQL 案例，不是 Passed。沒有本次資料庫變更或測試資料殘留。

本輪僅實作與離線驗證。既有 Application／Migrator Preflight、Migration 2b2-b1、23 表、Application 最小權限與鎖定的 Migrator 均保留；沒有重跑部署或修改 Schema。所有下列 MySQL 命令都尚未由 Codex 執行。

## 職責與檔案

- `tests/batch_b_harness_contract.py`：固定兩個角色、明確 opt-in、Run ID／精確主體登記、逐表權限、關聯所有權與帳務一致性規則。
- `tests/batch_b_harness_database.py`：驗證真實帳號及最小 ACL、唯讀基線／結構比對、受限 control 更新、逐主鍵清理。
- `tests/batch_b_harness.py`：Baseline → Enable → 有期限 worker → Cleanup → Restore → Verify；原子寫入本機復原 manifest。
- `tests/batch_b_mysql_cases.py`：合成的可信音樂輸入、Application Repository factory 與新連線帳務查詢。
- `tests/batch_b_mysql_faults.py`：真正 PyMySQL 物件的客戶端 fault adapter，不提供任何虛構 SQL 結果。
- `tests/batch_b_mysql_scenarios.py`：僅在明確 opt-in 的 spawned worker 執行真實交易。
- `tests/test_batch_b_mysql.py`：23 個預設跳過的真實 MySQL 案例。
- `tests/test_batch_b_harness_safety.py`：133 個明確獨立的離線安全案例，使用合成 inventories／connection spies，不宣稱真實 MySQL 通過。
- [一次性 DBA SQL](batch-b-test-harness-grants.sql)：只建立新 harness 帳號及其精確權限，不更動既有帳號或 Registry。

## 安全流程

1. 同時要求 `JUKEBOX_RUN_BATCH_B_MYSQL=1`、`JUKEBOX_RUN_BATCH_B_HARNESS=1`、`JUKEBOX_BATCH_B_EXCLUSIVE=jukebox101_test`。其他 JUKEBOX_RUN_* 必須 OFF。預設 skip 發生在任何 credentials 載入及連線之前。
2. Application 只讀取 TEST_DB_*；Harness 只讀取 TEST_HARNESS_DB_*，不 fallback，不讀 .env。兩者固定 127.0.0.1:3306/jukebox101_test；分別只接受 app／harness 的精確 CURRENT_USER()，拒絕 localhost、root、Migrator、其他 schema、active 或尚未啟用的 applicable roles、未啟用 FK 或非 MySQL 8；另要求 TEST_DB_SERVER_UUID 符合一次 DBA 核准的實際 server_uuid。Harness 的 SERIALIZABLE 讀取要求 MySQL 8.0.22+，目前使用者報告 8.0.46。
3. Harness 取得 session advisory lock，拒絕同時第二個 harness。每次測試開始只接受 23 個 InnoDB base tables，Registry 只有正確 version/checksum/applied，一筆 control 且三開關 OFF；其餘 21 表全空。所有測試根身分先 fsync 寫入 manifest，再啟用測試庫三個 control switches。這是隔離庫中的合成測試帳務，沒有啟用正式 Nickel 或外部驗證。
4. 所有業務流程皆由 app 帳號執行原有 SubmissionRepository，沒有攔截或取消真實 Commit。Harness 只管理 control／提醒測試時間及清理。worker 的 inherited environment 刪除 harness／migration credential namespaces；只透過私有 IPC 傳入 app settings。
5. 每情境獨立 spawned process，30 秒到期 terminate + join 5 秒，必要時 kill + join 5 秒；若仍存活，禁止清理。PyMySQL 原有 connect 5 秒／read-write 10 秒；InnoDB lock wait 2 秒。清理先取得 control lock，避免在已結束的 client 對應 server transaction 尚未釋放時刪資料。沒有使用任意 Python Lock 作為資料庫正確性保障。
6. Worker 停止後，Harness 開 SERIALIZABLE transaction 及 control row lock（以 revision=revision 的精確 singleton no-op UPDATE 取得，不要求 table-level UPDATE／LOCK TABLES），讀取全 23 表，不任意分頁。根身分與完整 FK 關聯都確認後，才允許 DELETE。未知資料／孤兒 event 或 receipt／balance-Ledger 不一致／Claim-Settlement 關聯錯誤會停止 DELETE，保留資料及診斷，嘗試僅關閉三開關。後續測試 session 被 poison，不繼續寫入。
7. 成功清理：按 FK 子表到父表，使用實際完整主鍵 WHERE（含複合鍵）逐筆刪除、每筆 rowcount 必須 1。版本 self-FK 先移除被引用 alias 的葉節點再刪 root；遇 cycle 停止並 rollback。沒有全表 DELETE、TRUNCATE、DDL、停用 FK 或修改 Migration Registry。
8. 清理與原 control accepted_sequence／revision／三開關的恢復在同一交易 Commit；再開新 transaction 比對完整 baseline／原 Registry／control。SHOW CREATE 比對只忽略 AUTO_INCREMENT counter，因為 rollback／精確 DELETE 不會重設 sequence；不執行 ALTER 來偽裝完全相同。
9. Cleanup commit 或連線失敗時，嘗試用新的已驗證 harness session 取得 ownership lock、只關閉三開關；不重試 DELETE 或擅自重設 counters。保留 manifest，停止後續寫入。正常流程不需使用者逐次手動清理。

### 所有權不是只有 Run ID

manifest 預先登記每個 operation 的 user/video/version、Owner、所有允許 decision/evidence keys。實際 song_id 經已登記 platform/video 找出，ledger_id 必須由已登記 user、song 與 sha256(operation) 的 deterministic ledger key 找出。其他子資料使用這些實際父鍵驗證；日期為主鍵的提醒摘要會解碼全部 operation_ids 並核對 Owner；稽核例外會檢查每個 original_claims ledger_id。清理前額外確認 event/receipt、video/event/history/evidence/reminder、Claim/Ledger/Settlement、recipient/song/key、原始回執／結算／review／evidence JSON、受理水位與 user balance 皆一致。沒有假設 Ledger 或各表本身帶 Run ID 欄位。

## DBA 最小權限與一次性操作

SQL 草案：`docs/batch-b-test-harness-grants.sql`。DBA 先審核，在本機管理會話替換密碼 placeholder 後一次執行；不要把真實密碼寫回專案 SQL 或貼至聊天。本輪未授權執行。若帳號已存在，停止查明，勿忽略錯誤繼續 GRANT。新帳號先 ACCOUNT LOCK，完成前置與後置核對後只解鎖 Harness；Migrator 保持鎖定。SELECT 結果必須由受控執行程序判斷，不能用不檢查結果的 SQL 批次直接跑到底。

| 權限範圍 | Harness 權限 | 用途 |
|---|---|---|
| 全部 23 表 | 逐表 SELECT | 基線、實際主鍵、關聯及結束後比對 |
| 18 個實際會寫入的業務表 | 逐表 DELETE | 僅由程式以完整 PK 清理已證明的本次資料 |
| jb2_control | UPDATE 僅 accepted_sequence、revision、三開關欄位 | 啟用隔離測試與完整還原；不能改 schema_version |
| jb2_reminders | UPDATE 僅 next_due_at | 不更改受理時間的提醒到期測試 setup |
| jb2_migrations、submissions、recommendations、daily_dispatches | SELECT only | 不由 Harness 修改 |

不授予 INSERT、CREATE、ALTER、DROP、REFERENCES、INDEX、LOCK TABLES、GRANT OPTION、global 或 schema grants；不增加 app DELETE；不解鎖 Migrator。MySQL grants 無法限制 DELETE 的 WHERE 條件，逐主鍵限制由此受審程式執行，不能宣稱資料庫 ACL 本身是 row-level policy。

DBA 必須一次確認沒有其他 grants、可用角色、不明表／trigger／跨 schema FK，並停止所有其他測試庫寫入者。Metadata 可見性受 ACL 限制，Harness 看不到隱藏物件不代表它們不存在；本次不新增廣泛 schema SELECT／TRIGGER 權限以繞過最小化要求。帳號已具有必要最小權限後，正常各次測試不需 root、Migrator 或 DBA 清理。

## 真實 MySQL 測試清單（23 項，尚未執行）

| 類別 | 數量 | 實際驗證 |
|---|---:|---|
| workflow | 10 | 持久化與原始回執冪等、A Pending/B Approved、A Rejected/B Approved、並行受理、並行同版本發獎、並行同 operation 重送、真實 1205 鎖等待、Owner revision/evidence/未授權拒絕、Identity conflict freeze 保留既有獎勵、可信不同版本獨立發獎後合併稽核 |
| rollback | 8 | 在實際執行 event、receipt、evidence、Ledger、Claim、balance、candidate 或 Settlement SQL 後拋出客戶端例外，確認所有 rows/control rollback 與重試一致 |
| unknown commit | 4 | intake/settlement 各在 Commit 前及真正 Commit 後遺失客戶端 acknowledgement，以相同 key 重試不多建事件／不多發幣 |
| reminder | 1 | 原受理 +24h 初次 deadline、due setup 只由 Harness、摘要持久化／重送穩定、不改受理順序 |

Fault adapter 並非網路斷線或 MySQL crash 的實測：SQL 與 Commit 使用真正 server，例外是可重現的客戶端注入，不能稱為已驗證所有自然網路故障。真實 InnoDB、權限、FK cleanup、client/server timeout 與 commit uncertainty 案例仍須後續授權執行。

## 未來執行命令（本輪不得執行）

一次性 DBA SQL 完成並**另行核准真實寫入及隔離庫開關暫時啟用後**，在專案根目錄的專用本機 PowerShell 執行以下區塊。使用既有 pytest，不新增 Launcher 或 Preflight 工具。

```powershell
Set-Location -LiteralPath 'D:\Project\MusicBased\discordBot_JukeBox101'
$batchBAppSecret = $null
$batchBHarnessSecret = $null
try {
    foreach ($batchBGate in @(Get-ChildItem Env: | Where-Object Name -Like 'JUKEBOX_RUN_*' | Select-Object -ExpandProperty Name)) {
        Set-Item -LiteralPath ('Env:' + $batchBGate) -Value '0'
    }
    foreach ($batchBProfile in @(@('TEST_DB_', 'jukebox101_test_app'), @('TEST_HARNESS_DB_', 'jukebox101_test_harness'))) {
        Set-Item -LiteralPath ('Env:' + $batchBProfile[0] + 'HOST') -Value '127.0.0.1'
        Set-Item -LiteralPath ('Env:' + $batchBProfile[0] + 'PORT') -Value '3306'
        Set-Item -LiteralPath ('Env:' + $batchBProfile[0] + 'USER') -Value $batchBProfile[1]
        Set-Item -LiteralPath ('Env:' + $batchBProfile[0] + 'NAME') -Value 'jukebox101_test'
    }
    $batchBAppSecret = Read-Host 'Application password' -AsSecureString
    $batchBHarnessSecret = Read-Host 'Harness password' -AsSecureString
    $env:TEST_DB_PASSWORD = [System.Net.NetworkCredential]::new('', $batchBAppSecret).Password
    $env:TEST_HARNESS_DB_PASSWORD = [System.Net.NetworkCredential]::new('', $batchBHarnessSecret).Password
    $env:JUKEBOX_RUN_BATCH_B_MIGRATION = '0'
    $env:JUKEBOX_RUN_BATCH_B_MIGRATION_PREFLIGHT = '0'
    if ([string]::IsNullOrWhiteSpace($env:TEST_DB_SERVER_UUID)) { throw 'Missing approved TEST_DB_SERVER_UUID from one-time setup' }
    $env:JUKEBOX_BATCH_B_EXCLUSIVE = 'jukebox101_test'
    $env:JUKEBOX_RUN_BATCH_B_MYSQL = '1'
    $env:JUKEBOX_RUN_BATCH_B_HARNESS = '1'
    $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
    $env:PYTEST_ADDOPTS = ''
    & .\.venv\Scripts\python.exe -B -m pytest tests/test_batch_b_mysql.py -p no:cacheprovider -p no:logging -o addopts= -q -s --tb=short --maxfail=1
    $batchBExitCode = $LASTEXITCODE
    Write-Host ('Exit code: ' + $batchBExitCode)
}
finally {
    foreach ($batchBGate in @(Get-ChildItem Env: | Where-Object Name -Like 'JUKEBOX_RUN_*' | Select-Object -ExpandProperty Name)) {
        Set-Item -LiteralPath ('Env:' + $batchBGate) -Value '0'
    }
    Remove-Item Env:TEST_DB_PASSWORD, Env:TEST_HARNESS_DB_PASSWORD, Env:JUKEBOX_BATCH_B_EXCLUSIVE -ErrorAction SilentlyContinue
    if ($null -ne $batchBAppSecret) { $batchBAppSecret.Dispose() }
    if ($null -ne $batchBHarnessSecret) { $batchBHarnessSecret.Dispose() }
}
```

預期成功 23 Passed，逐 run journal 為 RESTORED_VERIFIED，前後都是 control 一筆、registry 一筆、其餘 21 表零，並完整還原 control counters；AUTO_INCREMENT counter 允許增加。這是預期，不是本輪實測結果。任何 failure／STOP／殘留都停止，不重跑或自動清空。請回報 sanitized 結果與 journal 路徑，不貼 credentials、完整環境或 raw driver error。不要和 pytest-xdist／其他 MySQL suites 同時執行。

## 中斷與仍需人工處理的例外

一般 assertion／例外／子程序逾時都會嘗試 cleanup/restore。作業系統強制終止 pytest、斷電、父程序 crash 或 MySQL 不可用時，無法承諾 finally 執行：資料和開關可能保留，下一次 run 會拒絕非空／非 OFF 基線，不能從 prefix 自行接管。使用者需保留 manifest 並另行核准精確復原；目前刻意沒有自動 reaper、DROP、清全表或 Registry 修改。當不一致資料可能代表功能缺陷時，保留證據優先於「看起來清乾淨」。

## 本輪離線證據

完整 Batch A／Batch B 回歸及新 Harness 安全測試：**681 Passed、0 Failed、23 Skipped**。Skipped 全是未授權的真實 MySQL 案例。133 個新離線安全案例已含於 Passed，既有 548 個回歸保留通過。未連線 MySQL，未執行帳號建立／GRANT／Migration／資料庫開關變更。以下為實際使用的完整防護命令，禁止 socket、.env 讀取及任何檔案寫入，結果 READONLY_GUARD_BLOCKED_EVENTS=[]：

```powershell
@'
import os
import sys
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
os.environ['PYTEST_ADDOPTS']=''
blocked=[]
write_flags=os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND
mutations={'os.mkdir','os.remove','os.rmdir','os.rename','os.link','os.symlink','os.truncate','os.chmod','os.utime','subprocess.Popen'}
def readonly_guard(event,arguments):
    forbidden=event in mutations or event.startswith('socket.')
    if event=='open':
        path,mode,flags=arguments
        forbidden=(isinstance(mode,str) and any(character in mode for character in 'wax+')) or bool(flags & write_flags)
        forbidden=forbidden or (isinstance(path,(str,bytes)) and os.path.basename(os.fsdecode(path))=='.env')
    if forbidden:
        blocked.append(event)
        raise RuntimeError('Read-only audit blocked: '+event)
sys.addaudithook(readonly_guard)
os.environ['JUKEBOX_RUN_BATCH_B_MYSQL']='0'
os.environ['JUKEBOX_RUN_BATCH_B_HARNESS']='0'
import pytest
arguments=["tests/test_submission_snapshot.py","tests/test_submission_workflow.py","tests/test_version_identity.py","tests/test_reward_candidates.py","tests/test_share_replies.py","tests/test_review.py","tests/test_eligibility.py","tests/test_song_identity.py","tests/test_urls.py","tests/test_phase2_quality.py","tests/test_repository.py","tests/test_schema_static.py","tests/test_mysql_safety.py","tests/test_mysql_assertions.py","tests/test_repository_acceptance_safety.py","tests/test_submission_migration.py","tests/test_submission_persistence.py","tests/test_batch_b_preflight.py","tests/test_dual_account_safety.py","tests/test_batch_b_harness_safety.py","tests/test_batch_b_mysql.py"]+['-p','no:cacheprovider','-p','no:logging','-o','addopts=','--capture=sys','-q','--tb=short']
result=pytest.main(arguments)
print('READONLY_GUARD_BLOCKED_EVENTS:',blocked)
raise SystemExit(result or bool(blocked))
'@ | .\.venv\Scripts\python.exe -B -
```

本輪未安裝套件、使用付費服務、連線 Discord／YouTube／其他業務 API、執行 Git 寫入、啟用正式 Nickel 或開始 Batch C。真實 Batch B 功能驗收與人工 Accepted 尚未完成。



## Harness 最終安全審核與部署準備

本輪是既有 Batch B 的安全收斂，不新增 Phase、Launcher、Preflight 或 Migration。以下結論為原始碼／SQL 與離線驗證，不是 MySQL 實測。

| 審查項目 | 結論與修正 |
|---|---|
| submissions SELECT only | 足夠：Batch B 只從舊 Repository 匯入 _ensure_user 與 _upsert_pending_song，未呼叫 Phase 2A _record_submission。新靜態測試核對可達寫入集合，不增加無用 DELETE。若未來 suite 開始寫 submissions，必須重新審核清理與權限。 |
| 18 張 DELETE 表 | 與全部 Batch B 實際 INSERT／UPDATE 目標扣除 control 完全一致；其餘只讀表出現任何資料即拒絕清理。沒有加大 SQL 授權。 |
| FK／PK／未知 Commit | 新增兩份 Schema 的 child-before-parent 檢查；維持完整 PK、版本 self-FK 葉節點清理。generated ledger/song IDs 經已登記自然鍵及 deterministic ledger key 回查，不依賴 caller lastrowid 或 Run ID 全表傳播；孤兒或不一致保留診斷。 |
| control 恢復 | 原先只驗證 revision 下限會掩蓋額外修改，已改成精確公式：原 revision + committed events + claims + Owner decision 的實際 revision effect。no-op merge 不加 revision；其他 review／exclude 與實際 merge 各加一。三開關須仍為本 run 的 ON 狀態。異常在任何 DELETE／counter 還原前停止，僅嘗試關閉開關，不覆蓋不明 counters。 |
| 角色及額外 grants | 現在拒絕 APPLICABLE_ROLES，即使 CURRENT_ROLE=NONE。own SHOW GRANTS 額外排除 dynamic、role、proxy、routine、GRANT OPTION 等表 metadata 不足以涵蓋的形式；raw grant text 不輸出。Application 另外拒絕未審查 column grants，避免 Registry 的 table SELECT 被 column UPDATE 繞過。 |
| 目標 instance | 固定 loopback／port／schema／account 之外，TEST_DB_SERVER_UUID 必須等於一次 DBA 設定時核准的 server_uuid；缺少 pin 在連線前拒絕。這是防止誤連的指紋，不是對惡意伺服器的密碼學認證。 |
| 清理失敗 | journal 現在保留最初 cleanup 及後續 shutdown／close 的錯誤類別、數字 MySQL code 與固定 safety code，不保存 raw driver text。任何失敗 poison session。清理 commit 不確定時不重試 DELETE，以新已驗證 session 嘗試關開關並保留資料。 |

### Trigger 與管理者信任界線

Harness 沒有 TRIGGER 權限，information_schema.TRIGGERS 對它可能不完整，因此 runtime 查不到 trigger **不能證明不存在**。不能為了看完整 metadata 給 Harness TRIGGER（這也會授予建立／刪除 trigger 的能力）。SQL 草案前置查詢要求 DBA 以完整視野確認 23 個預期 InnoDB BASE TABLE、無 trigger、所有 FK 均在測試庫且符合實際 Schema，以及沒有任何 host 的同名 Harness 帳號；後置查詢核對最小 table/column grants、global/dynamic/routine/proxy/role metadata。沒有符合條件就停止，不以額外權限補洞。

此條件仍依賴 DBA 及停止其他寫入／DDL 的獨占承諾。無法辨識具特權管理者故意偽造整套 run 資料，或在設定後加入對 Harness 隱藏的 trigger；部署後若有其他管理變更，必須重審。這不是多租戶正式環境的隔離保證。

### 一次授權可以涵蓋的精確範圍

1. 僅對 127.0.0.1:3306 的 jukebox101_test 及測試帳號，使用一次性安全 DBA 連線讀取前後置檢查所列 metadata；mysql 系統表僅讀 username/host、grant、role 等非認證欄位，不讀 authentication_string／密碼。
2. 僅建立新 jukebox101_test_harness@127.0.0.1，先鎖定，套用既有 25 筆逐表／指定欄位 GRANT，核對成功後只解鎖此帳號；若配置失敗，保留／鎖回此帳號並停止。不 DROP／重建既有帳號，不修改 app／migrator，無 Migration 或 Schema DDL。
3. 讀取並記錄非機密 server_uuid，設定為執行程序的 TEST_DB_SERVER_UUID。DBA 密碼只限本機遮蔽輸入／經明確授權的程序憑證方式，不寫回 SQL／專案／報告，不擅自讀任何 credential store。正式測試前釋放 DBA 連線及 credentials；子程序也會剔除 TEST_DBA_DB_* namespace。
4. 若同一次授權**另外明列真實測試**，才開啟 test opt-in 與本機測試庫三個業務開關，使用 app 跑 23 案例，由 Harness 逐 PK 清理、還原及核對；未包括此項則配置驗證後停止，不進行業務寫入。

最少人工操作方式是由 Codex 在一次受控管理工作中依序讀取 SQL 草案、檢查前置結果、建立鎖定帳號、套用並核對 grants、解鎖 Harness、以新帳號回連驗證，再依已授權範圍繼續測試。不是要求使用者逐條執行 SQL，也不是無條件 source 整份 SQL。前提是執行程序有已明確授權的安全憑證取得方式；目前未讀取或驗證任何真實憑證來源，不能承諾在無憑證的 Codex 程序中自動登入。

最終本輪離線結果：681 Passed／0 Failed／23 Skipped，較原 650 增加 31 個驗證（含參數化），新增後所有 Harness 安全案例共 133。READONLY_GUARD_BLOCKED_EVENTS=[]。真實 MySQL 23 案例未執行，SQL 尚未實際建立帳號／授權／解鎖，Batch B 未標為人工 Accepted。
