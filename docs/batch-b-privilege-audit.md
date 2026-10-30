# Batch B 最終權限核對（離線、待人工審核）

本報告依現有原始 SQL 盤點，不代表任何 GRANT、DDL 或交易已在 MySQL 執行。權限草案見 [batch-b-minimum-grants.sql](batch-b-minimum-grants.sql)；Application 的權限同時涵蓋保留的 Phase 2A Repository 與 Batch B Repository，沒有新增 Exchange／Daily 功能。

## 資料表 × SQL 操作 × 所需權限 × 帳號

下列所有列皆包含 `SELECT`，供 Preflight COUNT／SHOW CREATE／Migration marker 及 Repository 查詢使用。權限名稱直接對應 SQL 操作；`REFERENCES` 對應建立其他表指向本表的 FK，`CREATE` 對應本表 CREATE TABLE。`ON DUPLICATE KEY UPDATE` 即使原值寫回也需要 UPDATE，不能因為業務上稱為冪等就移除。沒有 DELETE。

A = `jukebox101_test_app@127.0.0.1`；M = `jukebox101_test_migrator@127.0.0.1`。路徑均以專案根目錄為基準；表內行號是本輪查閱位置。

| 資料表 | A：SQL 操作／所需權限 | M：SQL 操作／所需權限 | 實際程式證據／用途 |
|---|---|---|---|
| `credit_ledger` | INSERT, SELECT | REFERENCES, SELECT | SUM／歷史稽核 SELECT、發獎 INSERT；reward_settlement.py:76、101；reward_history.py:15 |
| `daily_dispatches` | SELECT | SELECT | 目前僅 Preflight 筆數／結構盤點；尚無 Daily 派送 Repository 寫入 |
| `jb2_barriers` | INSERT, SELECT, UPDATE | CREATE, SELECT | 封鎖 upsert、SELECT；submission_maintenance.py:58、60；reward_history.py:73 |
| `jb2_claims` | INSERT, SELECT | CREATE, SELECT | 獎勵 Claim INSERT／snapshot SELECT，不 UPDATE；reward_settlement.py:79；persisted_snapshot.py:22 |
| `jb2_control` | SELECT, UPDATE | CREATE, INSERT, SELECT | 交易 FOR UPDATE、受理水位／revision UPDATE；submission_transaction.py:57；submission_store.py:45 |
| `jb2_decisions` | INSERT, SELECT | CREATE, SELECT | Owner 決策 SELECT／INSERT；review_persistence.py:80、99 |
| `jb2_events` | INSERT, SELECT, UPDATE | CREATE, REFERENCES, SELECT | 受理 INSERT、reward／freeze UPDATE、snapshot SELECT；submission_store.py:141；reward_settlement.py:114 |
| `jb2_evidence` | INSERT, SELECT | CREATE, SELECT | 追加證據 INSERT；其 SELECT 為 Preflight 所需；submission_store.py:163 |
| `jb2_exceptions` | INSERT, SELECT, UPDATE | CREATE, SELECT | 稽核例外 upsert；SELECT 供 Preflight；reward_history.py:71 |
| `jb2_exclusions` | INSERT, SELECT | CREATE, SELECT | 排除證據 INSERT／snapshot SELECT；submission_maintenance.py:81；persisted_snapshot.py:58 |
| `jb2_legacy` | INSERT, SELECT, UPDATE | CREATE, INSERT, SELECT | 歷史 membership upsert／identity UPDATE；submission_store.py:94；review_persistence.py:175 |
| `jb2_migrations` | SELECT | CREATE, INSERT, SELECT, UPDATE | Application 僅 Preflight SELECT；Runner CREATE／SELECT／INSERT／UPDATE；submission_migration.py:85、89、97、161 |
| `jb2_receipts` | INSERT, SELECT | CREATE, SELECT | 原始回執 SELECT／INSERT，不 UPDATE；submission_store.py:38、144 |
| `jb2_reminder_batches` | INSERT, SELECT | CREATE, SELECT | 每日摘要 SELECT／INSERT；submission_maintenance.py:107、132 |
| `jb2_reminders` | INSERT, SELECT, UPDATE | CREATE, SELECT | 初次提醒 upsert、JOIN UPDATE／SELECT；submission_store.py:146；submission_maintenance.py:135 |
| `jb2_review_history` | INSERT, SELECT | CREATE, SELECT | revision SELECT／INSERT；submission_store.py:175、182 |
| `jb2_settlements` | INSERT, SELECT | CREATE, SELECT | 冪等結算 SELECT／INSERT；reward_settlement.py:44、89 |
| `jb2_versions` | INSERT, SELECT, UPDATE | CREATE, REFERENCES, SELECT | 版本 upsert、合併 UPDATE、snapshot SELECT；submission_store.py:26；submission_maintenance.py:57 |
| `jb2_videos` | INSERT, SELECT, UPDATE | CREATE, REFERENCES, SELECT | 影片 INSERT、review UPDATE、snapshot SELECT；submission_store.py:125、160 |
| `recommendations` | SELECT | SELECT | Phase 2A history JOIN SELECT；repository.py:77 |
| `songs` | INSERT, SELECT, UPDATE | REFERENCES, SELECT | pending upsert、影片查詢／鎖定；repository.py:106；submission_store.py:84、87 |
| `submissions` | INSERT, SELECT | SELECT | Phase 2A 投稿紀錄 SELECT／INSERT；repository.py:120、126 |
| `users` | INSERT, SELECT, UPDATE | REFERENCES, SELECT | 帳號 upsert、Daily 偏好及餘額更新／鎖定；repository.py:93、66；reward_settlement.py:81、99 |

## Migration / bootstrap

- `migration_plan()`：唯讀載入 16 個 CREATE TABLE；registry `jb2_migrations` 由 Runner 額外建立，共 17 個新表。原六表沒有 ALTER／DROP／UPDATE／DELETE。
- `_finish_migration()`：`INSERT jb2_legacy SELECT song_id FROM songs` 需要 songs SELECT + jb2_legacy INSERT；control 初始 singleton INSERT；registry UPDATE applied。app 不執行此 bootstrap，因此 control 沒有 app INSERT。
- FK 父表精確集合：`users`、`songs`、`credit_ledger`、`jb2_versions`（含自參照）、`jb2_videos`、`jb2_events`。只有 M 對它們具有 REFERENCES。一般 DML 不需要 REFERENCES。
- 內嵌 INDEX／UNIQUE／CHECK／FK 隨 CREATE TABLE 建立，不額外給 ALTER 或 INDEX。未知未來 Migration 必須重新審權，不可沿用本次最小集。
- SELECT FOR UPDATE 出現在 users／songs／jb2_control，這三表已有 UPDATE，不需要擴增 DELETE 或 LOCK TABLES。MySQL 8 的 FOR UPDATE 仍需要 SELECT 加 UPDATE／DELETE／LOCK TABLES 其中之一；8.0.22 只需 SELECT 的變更是針對 FOR SHARE，不能混用。
- session timezone／sql_mode／innodb_lock_wait_timeout、transaction／commit／rollback、同名 GET_LOCK 不需要資料表 DML 權限；未設定 GLOBAL 或 restricted administrative variables。

## schema-level / table-level Preflight

`test_privileges.application_requirements()` 為逐表權限契約，`privilege_blockers()` 合併 schema 與 table rights 後同時檢查缺權與超權。App 對 registry、recommendations、daily_dispatches 只能 SELECT；Ledger、Claim、Receipt、Review History 等不可 UPDATE／DELETE。

兩個 profile 均允許 schema SELECT 加各表所需的寫入權限，也允許全數逐表授權。**不再接受 Application schema-level INSERT／UPDATE／DELETE**：它們會套用到唯讀 registry，逐表 SELECT 無法覆蓋或撤銷。Migration schema CREATE／REFERENCES／INSERT／UPDATE 也會擴張至不需要該權限的表而 STOP。

舊帳號若仍有 schema DML，新的 Preflight 會因 EXCESS_APPLICATION_PRIVILEGES 停止，這是本次刻意收斂。SQL GRANT 只有增加效果；本草案沒有自動 REVOKE 或 DROP USER。DBA 須另經授權審查舊 schema/table/global grants 及 roles，再移除超額權限，不能只執行新的 GRANT 就宣稱最小化。Application 帳號已存在，不重新建立。其逐表 GRANT 必須等 Migration 成功建立目標表後才套用，不能為了提前 GRANT 而給 app CREATE。

Migration 前 app 只要求既有六表的必要權限；Migration 後新表逐一納入。registry 即使未建立，schema-level 寫權限會被視為未來可修改 registry 的過度授權。正常流程可先以六表逐表權限檢查 app，再用 M 完成經核准 Migration，再由 DBA 補 app 新表權限。所有現有入口對非空／既有 jb2_* 仍保守 STOP；這些是空白庫首次 Migration readiness，不是 Migration 後功能驗收入口。

Metadata 可見性受授權影響：逐表授權帳號可能看不到不明物件。完整隔離必須由 DBA 完整 inventory 證明，或比較具有完整 schema SELECT 的獨立唯讀盤點；unknown_tables=[] 不足以證明沒有隱藏表。Preflight 不查 mysql.user、不輸出 SHOW GRANTS 認證文字；active roles／global／跨 schema／GRANT OPTION 均阻擋。欄位授權不作為本輪必要權限的替代，DBA 仍須確認沒有額外 column grants 或可用未啟用角色；此檢查不是完整 ACL 證明器。

## 127.0.0.1 與 localhost

設定僅允許字面 `127.0.0.1:3306`，PyMySQL 使用 TCP；`localhost`／`::1`／其他 port 均在連線前拒絕。MySQL 帳號的 user@host 匹配仍受伺服器解析及已存在帳號影響，不能因 client host 是 loopback 就假設實際帳號必為 @127.0.0.1。

Runner 與兩個 Preflight 都核對 `CURRENT_USER()` 的精確 user@host，而非只相信使用者輸入或 USER()；若實際為 @localhost 即停止，不自動 fallback、不建立替代帳號、不更改 skip_name_resolve。還會核對 DATABASE()、@@port、MySQL 8 及無 active role。需由 DBA 在真實環境確認帳號匹配；密碼不放命令列或報告。

## 官方依據（2026-10-15 查閱）

- [MySQL GRANT](https://dev.mysql.com/doc/refman/8.0/en/grant.html)：權限累加、table-level 授權、未建立表的授權需包含 CREATE。
- [MySQL Privileges](https://dev.mysql.com/doc/refman/8.0/en/privileges-provided.html)：FK REFERENCES、CREATE／INDEX／UPDATE 權限。
- [Locking Reads](https://dev.mysql.com/doc/refman/8.0/en/innodb-locking-reads.html)：FOR UPDATE 權限，不將 FOR SHARE 的例外套用到 FOR UPDATE。
- [Connection Verification](https://dev.mysql.com/doc/refman/8.0/en/connection-access.html)、[Connecting](https://dev.mysql.com/doc/refman/8.0/en/connecting.html)：user@host 匹配、CURRENT_USER 及 TCP 連線方式。

## 測試與待驗證

本輪新增 16 個離線案例（含參數化），驗證 registry 寫權拒絕、immutable tables 的 UPDATE／DELETE 拒絕、schema DML 無法被 table SELECT 蓋掉、混合 schema SELECT／table grants、SQL 草案與兩組 Python 權限逐表一致、實際 Migration 的全部 FK parents，以及 Repository SQL 寫入與權限契約一致。

定向測試與品質檢查 67 Passed；Batch A／Batch B 回歸 548 Passed、0 Failed、0 Skipped（含定向），禁止 socket／.env 讀取／檔案寫入的 audit hook 事件為空。沒有執行真實 Preflight、GRANT 或 Migration。

真實 MySQL 尚待：未建立表的 CREATE+table grants 可用性、最小權限 bootstrap、全部 FK／SHOW CREATE 可見性、CURRENT_USER 匹配、實際 GRANT metadata 的 scope spelling、舊權限／角色收斂、InnoDB 並行／回滾／Commit uncertainty／原子 Ledger。未授予 app DELETE，後續自行 Commit 的資料清理不能沿用 app 全表清理；須另核准精確清理執行者／資料範圍或維持可回滾測試，且不得修改 registry／原 Ledger 以假造乾淨結果。

本輪未連線資料庫、執行 SQL、讀取機密、安裝套件、呼叫外部業務 API、執行 Git 寫入或啟動 Batch C。僅閱讀公開官方文件，尚未人工 Accepted。
