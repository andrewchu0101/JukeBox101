# Stage-B-only 與私人 Guild 最短啟動步驟

狀態：**STAGE_B_ONLY_OFFLINE_READY_FOR_REVIEW**（2026-10-16）。本輪只改既有 Harness／launcher 與離線測試；沒有連線 MySQL、真實 DDL／DML、Recovery、Stage A/B、Gateway 或 API。Recovery 維持 RECOVERY_SUCCESS；舊 Session 仍 Blocked，Batch B 真實 23 項仍未驗收。本文件不是執行授權。

## 三次分開的授權

1. **Batch B 真實驗收**：核准在固定 `127.0.0.1:3306/jukebox101_test`、UUID `ab73bd54-c3c3-11f1-bc47-005056c00001` 與原 Schema fingerprint `5f201eabfb5828f80029497e3eca81432ed99a81aebbd15f2a23515cc93c62a8`，使用既有 DBA／jukebox101_test_app／jukebox101_test_harness 帳號，執行一次新的 Stage-B-only Session。精確範圍為下列四條既有 helper 產生的 FK DDL、既有 23 個情境所需測試 DML、每 Run Manifest 精確 PK 清理與 Control 還原。測試期間僅在這個可丟棄測試庫將三個 Control 開關暫設 1，才能驗證 Ledger／Claim／結算；最後全部還原 0，accepted_sequence／revision 還原 0／0。這不是私人 Beta 的 Nickel 自動獎勵授權。不執行 Stage A、建帳號、改密碼、GRANT、Migration、FK Lab 或 Recovery。
2. **私人 Beta DB 準備**：Batch B 結果審核後，先由使用者明確指定獨立的 Beta DB 目標與現有 Application 帳號，另行授權核對其 Schema／Migration／最小權限及資料隔離。不能把測試庫直接當 Beta 庫；缺 Schema／權限時停下列出精確差異，不自動 DDL 或增權。只有舊 writer 已停止、歷史快照覆蓋已核實，才另行授權在該明確目標的 singleton_id=1 將 snapshot_ready／legacy_writers_quiesced 由 0→1；settlement_enabled 維持 0，不重設該 Beta 庫的受理序號／revision／Schema 版本。
3. **Discord 人工驗收**：提供下列環境設定後，另行核准指定 Application／私人 Guild 的 REST 契約唯讀核對、必要的 Guild-only 同步（若不一致先停、不覆蓋未知遠端命令）、Gateway 啟動與少量真實 `/share` 投稿寫入。以兩名測試者各投稿，再查 `/history` 確認只看見本人與目前 Pending 審核狀態；`/balance` 應維持原值。不要以新的 Slash invocation 重送未知 Commit；先保留操作碼、停止寫入並人工核對。沒有 Owner 命令、派送、Daily、Spotify、YouTube API 或自動發獎。

只有第 1 項取得明確授權後，才在專案根目錄執行：

```powershell
& .\scripts\run-batch-b-acceptance.ps1 -StageBOnly
```

launcher 會顯示全新 Session UUID，要求輸入 `STAGE_B_ONLY <該 UUID>` 與 `EXCLUSIVE_NO_DDL`。操作人需停止所有其他 writers／DDL，確認無其他管理者或排程改動；此承諾是必要前提，advisory lock 不能阻止不配合的新 writer。三個密碼均遮罩輸入，透過既有匿名 stdin 傳入；不要貼回秘密或完整 Journal，只提供 sanitized 結果。每次執行需要新的明確授權；失敗時不能直接再跑此命令或改用舊 A＋B／Recovery 命令。

## 實際需要的設定名稱（只核對程式來源）

| 設定 | 來源與用途 |
| --- | --- |
| DISCORD_EXPECTED_APPLICATION_ID | bot/acceptance_config.py；指定 Application，dispatch 必須相同 |
| DISCORD_DEV_GUILD_ID | 同一既有設定讀取器；正式私人入口將它當唯一允許的 Guild，DM／其他 Guild 拒絕 |
| DISCORD_OWNER_USER_ID | bot/__main__.py；既有 SubmissionRepository 所需 Owner ID，沒有新增審核命令 |
| DISCORD_BOT_TOKEN | bot/config.py；秘密，只能在操作人的 process environment 提供 |
| DB_HOST／DB_PORT／DB_USER／DB_PASSWORD／DB_NAME | bot/config.py；正式 Application DB，DB_PORT 預設 3306，root 被拒絕 |

本輪未讀取真實 .env、未顯示任何設定值，也沒有驗證 IDs／密碼目前已存在或有效。正式入口只讀取 process environment，不自動載入 .env；缺必要設定或 ID 格式無效會在 login 前 Fail Closed。Batch B 使用獨立的固定測試目標與匿名憑證，不依賴以上正式 DB 設定。

## Stage-B-only 安全機制

- 入口仍是同一 `scripts/run-batch-b-acceptance.ps1`／`tests.batch_b_acceptance`，新增 `-StageBOnly`／`--stage-b-only`；與 Recovery／FK Lab 模式互斥。預設舊 A＋B 路徑沒有改成自動 Stage B，不可混用。長 launcher 協調函式拆為小型既有流程 helper，沒有第二套 launcher 或測試引擎。
- 新 UUID 先於連線建立一次性目錄 `TEMP/jukebox101-stage-b-<uuid>/session.json`。若同 UUID 目錄已存在，連線前即拒絕；不 resume、不覆寫或刪除舊目錄。`previous_session_blocked=true` 始終保留；只有這次新 Session 的 session_blocked 可能在完整成功後變 false。舊 pytest poison state／Recovery Journal 不修改。
- Preflight 重用原 DBA 完整 metadata visibility、既有 Application／Harness 登入與精確 grants、Migration checksum／applied marker、23 個 InnoDB tables、空業務資料、完整零 Control Baseline、FK 欄位／名稱／action、外部 incoming FK／trigger／constraint 檢查。原 Schema 必須等於固定 fingerprint，不能用當前未知結構自行建立新基線。
- Supervisor 僅持有 DBA 與驗證 Harness 的兩個連線，取得既有 FK window lock 與獨立 Stage-B supervisor lock。每條 DDL 前後、兩步 bridge 之間，以及每 Run 開始前，都重新確認只有這兩個前景 session、沒有目標庫事件或任何 schema 的 enabled scheduled event、資料完整等於原 Baseline。全部讀取交易先 rollback 釋放 metadata locks，才送 ALTER。
- 使用既有 apply_switch：先 ADD 不同名稱的暫時 CASCADE FK，再 DROP 原 FK；23 項真實測試均成功、清理資料與 Control 全部恢復後，先 ADD 原 NO ACTION FK，再 DROP 暫時 FK。每條 DDL 在送出前持久化 PENDING，收到 ACK 後獨立記錄，並核對原始／bridge／暫時／還原的完整 Schema 與 FK；同方向 PENDING／UNKNOWN／ACKNOWLEDGED 都不能在同 Session 重試。
- DBA 連線只留在父 supervisor，不進入 application worker 的參數或環境。原測試仍用 Application 帳號；Harness 仍只持有原有精確清理權限。每項測試使用既有 RunScope、獨立 Manifest、既有 30 秒 worker deadline，並綁定 parent_session_id、固定 temporary Schema 與原 Baseline。未建立正確 CASCADE Window 絕不啟動測試。
- 管理交易的 ENABLE／CLEANUP／REMINDER_SETUP Commit 都先寫入 Journal intent，再分別記錄 ACKNOWLEDGED／UNKNOWN。Application Repository 連線重用既有 FaultConnection adapter，為每條連線建立獨立 transactions/transaction-<uuid>.json，記錄 Commit、Rollback、Close 的 intent／ACK／UNKNOWN，不記錄密碼或 SQL 參數。未知／未關閉／不完整的 worker audit 禁止精確清理與接續寫入。
- 原始 4 個 acknowledgement-loss 測試仍保留，但只允許**有明確 InjectedClientFault 證據的已知 before-send／after-real-ACK 注入**走其既有相同 key 重播；真實 driver CommitOutcomeUnknown 不會被這個測試 catch 誤吞。注入不是宣稱真實未知 Commit 已確認。其他真正不明提交一律 Blocked。並行 worker 已送出的 I/O 無法保證瞬間撤回；父 Harness 等待／停止已存在的 worker，拒絕新連線與後續清理，不承諾外部 exactly-once。
- 每次 cleanup 仍先鎖完整 inventory，逐列核對 Run Manifest 歸屬、payload、帳務、受理序號、Control／Registry／Schema，再按 FK 順序對完整 PK 參數化 DELETE，檢查 rowcount=1。alias 必須已個別清除，不能靠 CASCADE 偷刪未知列。DELETE_ATTEMPT 與 EXECUTED_UNCOMMITTED 不等於 Commit；同一 cleanup 交易才恢復原 Control，ACK 後再完整核對 Baseline。
- 任一未知 DDL／Commit、未知資料、失敗測試或安全檢查失敗即停止；supervised 模式只 rollback 未提交工作、記錄失敗並關閉連線，不自動關閉開關、清理已提交列、補償 DDL 或重跑 pytest。可能因此留下 CASCADE／bridge、已提交測試資料或測試開關 ON；這是刻意保留待人工審查的 blocked 狀態，不能假稱已恢復。後續只有新的唯讀調查及另行核准的精確修復範圍。
- 成功需 23 Passed／0 Failed／0 Skipped、23 份新 Session Manifest 與對應 summaries 全部 restored、所有 Commit 與 worker audits 完整、最後獨立連線驗證原 23 表／Registry／Control／Schema 相同、父連線正常關閉、最終 Journal 成功保存。狀態才為 B_ONLY_COMPLETE_MANUAL_ACCEPTANCE_PENDING；仍需人工驗收，不解除舊 Session Block。

## 本輪離線結果與修改

- 完整 pytest：**1228 Passed／0 Failed／55 Skipped**；全部 JUKEBOX_RUN_* 先關閉，audit hook 阻擋 DNS／非 Python 內部 socketpair 的連線及真實專案 .env 存取。55 項 opt-in 真實 MySQL 相關案例沒有執行，含目標 23 項；1 個既有 audioop deprecation warning。
- PowerShell Mock：**119 個斷言 Passed**；沒有啟動 Python child、MySQL 或 Gateway。涵蓋新 Session confirmation、三角色匿名傳輸、互斥模式、固定 UUID／Schema、舊報告模式拒絕、舊 Session Block 保留。
- 新增 65 個 Python 離線案例；涵蓋完整四步 transition／bridge 還原、四位置失聯、資料／Schema／FK／獨占／排程變動拒絕、原 Control／Registry 檢查、同 Session DDL／pytest 重試阻擋、跨 Session Manifest 拒絕、未完成／Unknown Commit、worker audits、精確 cleanup、Journal 與連線邊界。Rule One／docstring AST 檢查包含於 pytest，通過。
- 本輪程式修改：scripts/run-batch-b-acceptance.ps1；tests/batch_b_acceptance.py、batch_b_harness.py、batch_b_harness_contract.py、batch_b_mysql_cases.py、batch_b_mysql_faults.py、batch_b_mysql_scenarios.py、test_batch_b_mysql.py；tests/test_batch_b_acceptance_launcher.ps1，新增 tests/test_batch_b_stage_only.py。新增本 runbook，更新 README／ROADMAP 與上一輪接線紀錄的目前狀態。
- 最後補充連線邊界測試時發現漏匯入 stage_call（該次 4 Failed／1223 Passed／55 Skipped），已修正；最終完整回歸為上列 1228 Passed／0 Failed／55 Skipped。
- 前輪尚未提交的私人 Beta 程式／測試與文件完整保留；本輪沒有再修改 Bot 業務程式、Schema、Migration、權限文件或原事故 Journal。沒有安裝依賴、Stage、Commit、Push、Tag 或部署。

停止於 **STAGE_B_ONLY_OFFLINE_READY_FOR_REVIEW**，等待驗收與上述分段授權。
