# Batch B incident investigation — restoration pending

> 1826 最新狀態（2026-10-16）：已失敗的一次性 Recovery 不得重試。已離線改為不同名稱、ADD 優先的四步 FK bridge；尚未執行真實 DDL/DML。唯讀現況已確認原 FK NO ACTION、原 Schema 指紋、15 PK／Control／Registry 不變，狀態 READY_FOR_NEW_RECOVERY_APPROVAL。先前單條 ALTER 同名 ADD/DROP 方案已失效，不能執行。新範圍見 [1826 修復方案](batch-b-recovery-fk-plan.md)。

> 最新狀態（2026-10-16）：已完成暫時 self-FK CASCADE 的離線精確 Recovery 設計與獨立預設停用入口；尚未執行真實 FK DDL／Recovery，Session 仍 Blocked。完整範圍與失敗矩陣見 [Recovery FK 授權方案](batch-b-recovery-fk-plan.md)。一般 Harness 在原 NO ACTION 結構下會於開啟測試寫入前停止，不能直接重跑舊 A/B。

## 精準 1451 Hotfix 診斷（2026-10-16，Case 3）

新證據檔案為 `C:\Users\Yume\AppData\Local\Temp\jukebox101-recovery-8odr8mus\recovery.json`，SHA256 `b4b728e4de2e136dcb83b0222cd3a997c0f94e7066fbc83dbdbf92e586d669ee`。本輪僅讀取必要欄位，沒有改檔或複製完整內容。它明確記錄：R_RECOVERY_FAILED_BLOCKED、IntegrityError 1451、14 筆 DELETE_EXECUTED_UNCOMMITTED、最後 DELETE_ATTEMPT 為 jb2_versions 的 R:v1、rollback ACKNOWLEDGED、baseline_restored=false、session_blocked=true。

已確認共用 delete_primary 產生 `DELETE FROM jb2_versions WHERE version_id=%s` 的完整 PK predicate（實作含 identifier backticks），最後 target 與 pinned Manifest 相符，不是全表刪除或 PK 選錯。self_fk_plan.self_roots 是從當時 SELECT rows 的 canonical_id==version_id 派生，確有「程式當時判定 self-root」的證據，但舊 report 沒有輸出兩個原始欄位，也沒有 MySQL error 的 constraint 名稱。不能把「最後 target 是 versions」直接等同「fk_jb2_version_root 是 blocking constraint」。14 個 execute acknowledgements 不等於 committed deletes；目前實際 rows 仍須 SELECT。

Migration 靜態定義 canonical_id VARCHAR(128) NOT NULL、FK canonical_id→jb2_versions.version_id；claims／exclusions／barriers／exceptions 也有 incoming version FK。是否為 self-root、alias、其他 constraint／隱藏參照，不能只看先前的 row_count 或 restrictive_rules_verified。既有 Harness 沒有 canonical_id UPDATE 權限。若後續確認必須更改 identity，屬 ADDITIONAL_APPROVAL_REQUIRED；NOT NULL 不允許直接設 NULL，不能在本輪實作這類寫入或提出可直接執行的 UPDATE workaround。

本輪只最小增補既有 tests/batch_b_recovery.py 的唯讀 report：

- 原 KEY_COLUMN_USAGE SELECT 加 CONSTRAINT_NAME，沒有新增 query round trip。
- 使用已存在於記憶體的 SELECT inventory 和 SHOW CREATE，增加 recovery_report.version_fk_diagnosis。
- 顯示 actual_create_table；每個 registered target 的 version_id／canonical_id／is_self_root、alias_count／registered_aliases／unregistered_alias_count。
- 每個入向 constraint 的實際 table／column／referenced column／ordinal／UPDATE_RULE／DELETE_RULE、matching_rows／non_self_rows。無法讀取的 reference 標 unresolved，不以零取代。未知 row IDs／payload 不輸出。
- historical_blocking_constraint 永遠明確為 NOT_RECORDED；Harness 可見性不足不能假稱排除 hidden references。

沒有修改 Recovery Write、Repository、Schema、Migration、權限或 Manifest，沒有新增 Launcher／模式。新測試涵蓋 self-root、alias、未知 alias、非 self constraint、missing target／unexpected canonical 及 unresolved reference。受影響回歸 **285 Passed／0 Failed／23 Skipped**，`READONLY_GUARD_BLOCKED_EVENTS=[]`。包含 Recovery rollback、Session Block、模式隔離與工程規範；未跑不相關的大型全回歸，不能將此數字描述為完整 826 回歸。

**唯一下一步：**使用既有唯讀入口取得這些欄位；不要加 -RestoreRecovery，不重跑 A＋B，不再次 DELETE。Codex 本輪沒有已交付的互動式憑證，未連線 MySQL，實際 SELECT/metadata 查詢 0、寫入 0。

```powershell
powershell.exe -NoProfile -File "D:\Project\MusicBased\discordBot_JukeBox101\scripts\run-batch-b-acceptance.ps1" -RecoveryJournal "C:\Users\Yume\AppData\Local\Temp\jukebox101-batch-b-hst16g0d\run.json"
```

輸入 READONLY、一次既有 Harness 遮罩密碼，只提供 sanitized report 的 version_fk_diagnosis 與 validation_failures；不要貼密碼、dump 或完整 Journal。若仍有 hidden reference 可見性缺口，不宣稱已確定根因，不用再次寫入來探測。必要時另外取得既有 DBA 的精確唯讀 metadata 授權，不擴權給 Harness。

狀態：Recovery 執行仍停止；環境恢復、Batch B 真實整合驗收與 Beta unblocking 仍 Pending。舊 block 及兩份 Journal 均保留。

## Recovery Implementation / Beta Unblocking（2026-10-16）

**Recovery Write 已實作、未執行；仍待另外核准 Recovery Execution。** 本節取代下方歷史紀錄「沒有可寫 Recovery CLI」的現況描述；舊調查、原 Journal 及精確 Manifest 都保留。

### 已實作入口與核准範圍

重用 scripts/run-batch-b-acceptance.ps1，僅同時指定 -RecoveryJournal 與 **-RestoreRecovery** 才選到獨立 Python --restore-recovery。一般 A＋B、唯讀 --inspect-recovery、未知參數均不會進入恢復寫入。Restore 缺 Journal、錯確認字串或錯 Schema hash 會在要求密碼／啟動 Python 前停止。所有 real-test／Migration switches 關閉，不使用 DBA、App 或 Migrator。

以下是**待核准後才可使用**的一次性命令。本輪 Codex 沒有執行它：

```powershell
powershell.exe -NoProfile -File "D:\Project\MusicBased\discordBot_JukeBox101\scripts\run-batch-b-acceptance.ps1" -RecoveryJournal "C:\Users\Yume\AppData\Local\Temp\jukebox101-batch-b-hst16g0d\run.json" -RestoreRecovery
```

依序輸入：

1. `RESTORE 14d8aee30b034a9883e9f71cdec8146e`。
2. 停止所有測試庫 writer／DDL，且確認 DBA 完整 metadata 審查後沒有 DDL／額外參照變更，輸入 `EXCLUSIVE_NO_DDL`。若不能確認，不應繼續。
3. 貼入**先前已審核唯讀報告**的 schema_fingerprint（64 位小寫 hex）。不得自動採信本次 freshly read 的 hash，也不以空值略過。若沒有可核准的指紋或 metadata 不完整，停止，先做既有唯讀診斷／管理者唯讀審查。
4. 遮罩輸入一次既有 `jukebox101_test_harness@127.0.0.1` 密碼。密碼沿用匿名 stdin，不進 argv／環境／檔案／報告。不要貼到聊天。

本階段授權僅實作與離線驗證，不是上述執行授權。後續明確核准應限於此 Run、原 Journal hash、15 PK artifact、單一受控交易、Control 0/0/OFF 及獨立驗證；錯誤時 rollback/stop，禁止附帶重跑測試或解除 block。

### 程式與證據綁定

- tests/batch_b_recovery_write.py：最小獨立恢復協調器，重用 read_journal、verified_connection、verify_harness_grants、lock_inventory、require_exact_recovery、delete_owned、set_control、persist_manifest。沒有 Migration／account provisioning／外部業務 API。
- 只接受固定 incident journal path、Run ID、原 SHA256 `6a75a0bd379a08fa1f57992187b039f75f19bde1c51f7e457bc61dd0fcbfd71d`。
- 精確 Manifest 檔案固定，檔案 SHA256 為 `10cf5cac90d8fa47ccfd6a15ae9ed82e2bf365c6bbb2855cbbe8ae968b52a3a2`。不接受 caller 自選 manifest 或變更清理清單。
- 固定 host/port/schema/account，UUID 由已綁定原 Journal 取得，連線後核對 CURRENT_USER、資料庫、server UUID／version／FK enabled／角色。沒有 TEST_DB／正式帳號 fallback。
- 當前 normalized Schema fingerprint 必須等於操作者先前核准值。FK KCU graph 必須精確等於 29 個 reviewed column edges；26 個 named constraints 必須完整，DELETE_RULE／UPDATE_RULE 只接受 RESTRICT／NO ACTION，拒絕 CASCADE、SET NULL、未知／缺失規則及可見跨 Schema incoming FK。
- 建立新 `%TEMP%\jukebox101-recovery-<unique>\recovery.json`，保存 approved fingerprint、Manifest hash、已核准 PK attempt、commit/rollback outcome 和結果。原 journal 絕不修改，前後持續核對原 hash。新檔不是清除舊 block 的機制。

### 交易、Self-FK 與恢復結果

取得既有 advisory lock，先做 metadata／baseline 檢查；如已是原始基線，回報 RECOVERY_ALREADY_BASELINE_NO_REPLAY，沒有 DELETE、Control restore 或 Commit。不能把重複請求當成新恢復成功。

真正恢復使用單一 SERIALIZABLE 交易，沿用 control row no-op UPDATE gate，鎖定所有 reviewed rows/gaps 後重新讀取完整集合。核對每個 PK、其父子／Owner／payload／帳務及 Control 1/2/OFF，與已核准 Manifest 完全相同才開始 DELETE。控制列鎖定的 no-op UPDATE 也屬未來需核准的交易範圍；本輪未執行。

順序仍為 receipts → review_history → evidence → reminders → decisions → events → videos → songs → users → versions，逐完整 PK、rowcount=1。alias 先於 root；完整 version graph 禁止 cycle、missing parent、額外／缺失 PK。Self-FK 的安全判定是**完整受控圖、所有受影響列可歸屬、FK 不會 cascade 刪其他列**，不聲稱已證明 MySQL 會接受自指 root DELETE。若不能證明這些安全條件，在 DELETE 前停止；若符合但 server 仍回 1451，整筆交易 rollback，不嘗試改 identity、FK 或 predicate。

刪除完成後，在同交易重新 SELECT FOR UPDATE 核對 Control 仍是預期 1/2/OFF，才以原五欄值還原 0/0/OFF，rowcount=1。Commit 前全 23 表必須吻合原 empty business baseline、Control／Registry 原樣及 approved Schema／FK；不碰 schema_version、Migration Registry、AUTO_INCREMENT、角色／grants。

Commit 只呼叫一次。Commit 前失敗嘗試 rollback，任何 rollback acknowledgement failure 都保持 blocked。Commit error／中斷記為 UNKNOWN，關閉原連線後只開新唯讀 SELECT／SHOW 連線調查，**不 rollback 假稱已回復，也不重送 DELETE**；看到基線仍回報 R_RECOVERY_COMMIT_UNKNOWN_BLOCKED、非零 exit，須人工審查。

已確認 commit 後，新唯讀連線再次核對身分、最小權限、兩次全表 inventory、Schema／FK／Journal hash。若驗證失敗，回報 failed/blocked，不能宣稱可 rollback 已提交的交易。只有獨立驗證成功才回報 `R_RECOVERY_RESTORED_VERIFIED_BLOCKED`、baseline_restored=true、session_blocked=true、exit 0。歷史 journal／事故 block／人工驗收不改，Stage A/B 均不啟動。

### 已知限制與操作身分

本輪實際變更：新增 tests/batch_b_recovery_write.py、tests/test_batch_b_recovery_write.py；修改既有 scripts/run-batch-b-acceptance.ps1、tests/batch_b_acceptance.py、tests/test_batch_b_acceptance_launcher.ps1、docs/batch-b-real-mysql-tests.md 及本報告。精確 Manifest、Schema、Migration、帳號與原 Journal 沒有修改。

最終離線回歸 **826 Passed／0 Failed／23 Skipped**（本輪新增 36 個 recovery-write cases）；23 個 Skipped 全是真實 MySQL 案例，沒有標成 Passed。PowerShell Mock **54 項斷言通過**，不啟動真實 Python child。Readonly audit hook 阻擋 socket／subprocess／.env／檔案寫入，`READONLY_GUARD_BLOCKED_EVENTS=[]`。AST 函式長度／巢狀／文件規範測試通過。原 Journal 與 Manifest SHA256 均與上述 pin 一致。

離線驗證沿用 docs/batch-b-real-mysql-tests.md 的完整防護命令，在 arguments 前加入 tests/test_batch_b_acceptance.py、tests/test_batch_b_recovery.py、tests/test_batch_b_recovery_write.py；PowerShell 命令為 `powershell.exe -NoProfile -File tests/test_batch_b_acceptance_launcher.ps1`。這些是 Mock／單元回歸，不是真實 Recovery 執行或 MySQL 原子性驗收。

本輪不能保證此次 self-FK DELETE 成功，亦不能宣稱歷史 1451 constraint 已查明。原始 Schema fingerprint 缺失，current fingerprint pin 只能保護已核准現況及本次前後一致，不能補造歷史證據。

Harness 的 information_schema 視野不是 DBA 全伺服器視野；偵測可見 extra FK 不代表可排除具權限管理者在未授權 Schema 新增隱藏 incoming references。此模式延續原 Harness 的信任界線：DBA 已完整審查、此後無管理者 DDL／extra FK 變更、操作期間確實獨占。若這項前提不能確認，**不得使用恢復命令**；需已有 DBA 的唯讀核對，不能給 Harness 新權限，也不能用寫入測試探測。

Recovery 不需新增 DELETE 或其他 DBA 權限；現有最小權限不符會停止。密碼只在 PowerShell／Python／driver 記憶體與私有 pipe 短暫存在，不能保證 .NET／Python immutable string、memory dump／debugger 的物理抹除。

如日後另行授權完整 A/B，DBA password 是核准 local root 的密碼；Application password 是 **jukebox101_test_app 的測試密碼，非正式 App 密碼**；Harness password 是既有測試 Harness 密碼。原 Stage A bootstrap 仍拒絕重建既有帳號，Recovery 後不要自行重跑它；使用既有 Harness 的後續測試需獨立授權，不屬本輪。

## Controlled Recovery Planning — R6–R10（本輪沒有資料庫操作）

### A. FK Analysis

下面是兩份已審查 Schema 的完整 FK graph，共 26 個約束、29 個欄位位置；箭頭為 child → parent。這不是重新取得的 live metadata。最近唯讀報告的 foreign_key_rules 原文未提供給本輪，不能假稱已核對實際 DELETE_RULE／UPDATE_RULE。Schema 沒有寫 ON DELETE／ON UPDATE 動作；current_timestamp 的 ON UPDATE 是欄位行為，不是 FK action。

| Child | Parent／欄位 |
|---|---|
| submissions | discord_user_id → users.discord_user_id；song_id → songs.song_id |
| recommendations | discord_user_id → users.discord_user_id；song_id → songs.song_id |
| credit_ledger | discord_user_id → users.discord_user_id；awarded_song_id → songs.song_id；(recommendation_id, discord_user_id) → recommendations 同欄位 |
| daily_dispatches | discord_user_id → users.discord_user_id；(recommendation_id, discord_user_id, song_id) → recommendations 同欄位 |
| jb2_versions | canonical_id → jb2_versions.version_id（self-FK） |
| jb2_videos | song_id → songs.song_id |
| jb2_legacy | song_id → songs.song_id |
| jb2_events | discord_user_id → users.discord_user_id；video_id → jb2_videos.video_id |
| jb2_receipts | operation_id → jb2_events.operation_id |
| jb2_claims | version_id → jb2_versions.version_id；operation_id → jb2_events.operation_id；ledger_id → credit_ledger.ledger_id；discord_user_id → users.discord_user_id |
| jb2_settlements | operation_id → jb2_events.operation_id |
| jb2_review_history | video_id → jb2_videos.video_id |
| jb2_evidence | video_id → jb2_videos.video_id |
| jb2_exclusions | version_id → jb2_versions.version_id |
| jb2_barriers | version_id → jb2_versions.version_id |
| jb2_exceptions | version_id → jb2_versions.version_id |
| jb2_reminders | video_id → jb2_videos.video_id |

users、songs、jb2_control、jb2_migrations、jb2_decisions、jb2_reminder_batches 沒有 outgoing FK。decisions actor／operation、video identity JSON 裡的 version 是業務關聯，不能當作不存在於 Schema 的 SQL FK。

本次 15 筆候選的實體圖：receipts → event → video → song，event → user；review_history(3)／evidence(4)／reminder → video；version 為獨立 self-FK 節點；decision 沒有 SQL FK。其他可能指向這些父 PK 的 submissions／recommendations／daily_dispatches／ledger／claims／settlements／legacy／exclusions／barriers／exceptions，依使用者最近回報為零，但恢復前必須重查全部 23 表及完整 references。

**Self-FK 結論：**多版本 alias 應先刪葉節點、最後刪 root；本次只有一個 version PK，實際 canonical_id 仍須 fresh row 核對，不把單靠表筆數當成直接證據。若 root 自指，沒有經證實必需或可合法執行的中間 UPDATE；canonical_id NOT NULL，不能設 NULL、插入臨時 anchor、停用 FK 或改 constraint。只能在 actual metadata／row 已確認並另行授權後，將該 root 的精確 PK DELETE 作為受控交易的一部分；若仍 1451，整體 rollback 並保持 blocked，不能承諾成功或採其他寫法繞過。

原順序符合所有非 self 的 Schema dependencies，不能斷言順序就是歷史 1451 根因。已確認的獨立缺口是 version cycle／缺父檢查太晚，原本會在其他表 DELETE 之後才失敗；現已移到第一個 DELETE 之前。此修正不宣稱解決此次 1451。

### B. Exact Recovery Manifest

機器可讀提案：[batch-b-recovery-manifest.json](batch-b-recovery-manifest.json)。綁定原 SHA256；標記 recovery_writes_authorized=false。令 R=`14d8aee30b034a9883e9f71cdec8146e`，V=`1955b45be54`：

| 清理順序 | Table／PK | 列數 | 歸屬驗證 |
|---:|---|---:|---|
| 1 | jb2_receipts.operation_id=R:1 | 1 | 登記 operation，原始 receipt 內容與 event/user/video/sequence 相符 |
| 2 | jb2_review_history.(video_id,revision)=(V,0)/(V,1)/(V,2) | 3 | 登記 video、revision、actor，review JSON 與 key 一致 |
| 3 | jb2_evidence.evidence_id=1/2/3/4 | 4 | generated PK 不是 Run ID；須核對 video、actor、可信 payload，不只看數字 |
| 4 | jb2_reminders.video_id=V | 1 | video parent 與登記對象一致 |
| 5 | jb2_decisions.decision_id=R:1:approve | 1 | 登記 decision、configured Owner actor、decision payload |
| 6 | jb2_events.operation_id=R:1 | 1 | subject 的 user/video/accepted_sequence=1 |
| 7 | jb2_videos.video_id=V | 1 | 登記 video、song_id=58、identity/review JSON |
| 8 | songs.song_id=58 | 1 | generated PK，須核對 platform=youtube、platform_track_id=V |
| 9 | users.discord_user_id=4000089534685964 | 1 | 登記 subject user；無 ledger 時 balance 必須為零 |
| 10 | jb2_versions.version_id=R:v1 | 1 | 登記 version、fresh canonical_id／evidence，self-FK gate 如上 |

全部 PK 已從未修改的 Journal 確認；「仍存在／unregistered_count=0／validation_failures=[]」是使用者提供的最近一次唯讀結果，不是本輪 live 驗證。**沒有任何一筆可標成已刪除**。現在狀態統一為 reported-present，fresh-recheck-required。若 fresh 集合少一筆或多一筆，本提案停止，不自動採用縮小／擴大後清單。

### C. Transactional Restoration Design

1. 仍需 fresh preflight：固定 Harness/host/port/db、核對 UUID；exact 23 InnoDB tables、Migration marker/checksum/applied 不變；原 Journal SHA256/Run ID/roots 不變；fresh Schema fingerprint 綁定核准計畫。取得 actual foreign_key_rules、version canonical_id、所有入向 FK（包括可見性限制）並核對 reviewed graph。缺原始 fingerprint 不可宣稱歷史 Schema 不變；若不能確認目前 metadata 完整，停止，必要時僅請既有 DBA 唯讀確認，不增權。
2. 停止其他測試 writer/DDL，確認無殘存 scenario process，再由未來受控 executor 取得既有 advisory lock 與 SERIALIZABLE control gate。鎖不是對管理者或不合作 writer 的絕對隔離；全 inventory 的 rows/gaps、Schema、grant、FK、pending operations 與 gate 必須在寫入前重查。
3. 執行純函式 require_exact_recovery，真實 ownership/accounting/payload/counter 檢查不省略；全 23 表、exact PK manifest、所有未登記 rows、declared parents 及 extra references 都核對。grant 驗證沿用 verify_harness_grants，含 18 張 table DELETE、僅限 control/reminder column UPDATE，沒有新增任何 privilege。
4. 保持三開關 OFF，不呼叫 Harness.start，不重建帳號，不借用 App／Migrator。依上表發送參數化 DELETE，review_history 使用兩欄完整 PK，每次 rowcount=1。帳號配置／Migration 完全不在此路徑。表數、row_count 或 LIKE Run-prefix 均不能代替 PK predicate。
5. 現在的 sequence=1 只能由單一已核准 event 解釋，revision=2 只能由 event 1 + Owner decision 1 + claim 0 解釋。fresh counters 必須同時等於 Journal observed watermark，且所有 immutable control 欄位不變；有額外修訂或開關開啟即拒絕。**刪除全部核准 rows 之後**才在同交易以五個欄位還原 sequence/revision=0/0、三開關 OFF；不改 singleton_id/schema_version，不改 Registry，不 reset AUTO_INCREMENT。
6. commit 前 SELECT 所有 23 表：21 個業務表全空、Control 原值、Registry 原樣、Schema 未變才可 commit。任何 1451、rowcount、ownership、counter、metadata 或刪除錯誤：rollback 全交易，保留 exact attempted PK、數字錯誤碼及原錯誤，禁止繼續或更換 predicate。root self-FK 若被阻擋，一樣整體 rollback，無例外。
7. commit 後新連線獨立核對同一完整基線及原 Journal hash。commit acknowledgement unknown／rollback acknowledgement unknown 時，停止並只讀分辨完整原基線、完整事故集合或部分／未知狀態；不自動重送 DELETE。後兩者全部 blocked。
8. 新 recovery evidence 另存既有非機密記錄位置；不能覆寫原 journal、清除其 Session Block 或改成 Accepted。即使恢復成功，也只標記 Environment Restored Verified；舊事故及 block 歷史保留，後續測試需另外授權。

### D. Code Changes

- tests/batch_b_harness_database.py：共用 version_delete_order，在任何 child DELETE 前拒絕 cycle、重複 version PK 或 inventory 外的 parent。正常 Cleanup 與未來 Recovery 共用同一 delete_owned；沒有改動 self-root DELETE 或 SQL FK semantics。
- tests/batch_b_recovery.py：新增不含 SQL 寫入的 require_exact_recovery／require_frozen_control，對未知 PK、未知 child、missing PK、不完整 inventory、counter drift、開關 ON、historical watermark 不符 fail closed。輸出仍為 proposal，remaining_gates 明列外部核對／明確寫入授權／self-FK 行為審查。
- tests/test_batch_b_recovery.py：補上 planner 正反例、版本 alias/root 順序、任何 DELETE 前拒絕 cycle／missing-parent、1451 時 rollback 與拒絕 Restoration Complete，以及本事故 15 PK artifact／FK order 一致性。
- docs/batch-b-recovery-manifest.json、這份報告及入口文件更新。沒有修改 Schema／Migration／原始 Journal／權限，也沒有新增可寫 CLI mode。

### E. Verification

本輪未連線 MySQL、未查閱或輸出密碼、未執行 SQL／DB 寫入／A＋B／Git 寫入。完整離線回歸 **790 Passed／0 Failed／23 Skipped**（本輪新增 14 個案例），`READONLY_GUARD_BLOCKED_EVENTS=[]`。PowerShell Mock **44 項斷言通過**；AST 函式長度、巢狀及 docstring 通過。原 Journal SHA256 再次核對不變。Fake connection 測試僅驗證呼叫與回滾路徑，不證明 MySQL self-FK DELETE 可成功。

### F. Execution Proposal

既有 scripts/run-batch-b-acceptance.ps1 -RecoveryJournal 仍然**只有唯讀**，不能拿它當成恢復執行命令。本輪不新增可寫模式，因 actual foreign_key_rules 原文／fresh live 狀態／historical failing target 未取得，不能宣稱全恢復已可執行。

後續在使用者核准 exact manifest + hash + 本節 transaction/rollback 範圍後，才可在同入口增加最小 **顯式 RestoreRecovery** 選項，並重用現有憑證 IPC、grant verification、locking、delete_owned、set_control、journal 和 baseline check。不是新 Launcher、不是重新 A＋B。建議一次授權涵蓋 fresh read gate → 15 PK transaction → Control 0/0/OFF → independent verification；允許任一步失敗即 rollback/stop，不承諾一定清除 self-root。

需要再次在使用者互動式 PowerShell 遮罩輸入**既有 Harness 密碼一次**，因未保存任何密碼。預計不需要新增 DBA 權限，也不需要 DBA 執行 DELETE。若現有 metadata 可見性不足，使用既有 DBA 的額外唯讀核對仍需明確授權；不能擴權解決。當前請先提供已取得報告中的 foreign_key_rules，不要求逐條 SQL 或立即重跑流程。

### G. Status

Harness Provisioning：Completed；Real MySQL Integration：Failed/Incomplete；Environment Restoration：Pending；Manual Acceptance：Pending。所有舊 Session Block 保留，沒有恢復寫入或測試重跑。

## A. Incident Summary

- Run ID：`14d8aee30b034a9883e9f71cdec8146e`。
- 原始 journal：`C:\Users\Yume\AppData\Local\Temp\jukebox101-batch-b-hst16g0d\run.json`；可讀、原檔未修改，沒有複製完整內容。
- SHA256：`6a75a0bd379a08fa1f57992187b039f75f19bde1c51f7e457bc61dd0fcbfd71d`。
- 使用者回報 A completed、B started；pytest counts 為 1 passed／1 failed，最終 B_CLEANUP_FAILED／FINAL_BASELINE_MISMATCH。這不是新的離線測試結果。
- Journal 最終狀態為 STOP_MANUAL_RECOVERY_REQUIRED，唯一記錄的 failure 為 cleanup／IntegrityError／MySQL 1451。Session 必須保持 blocked；本輪沒有解除、恢復或重試。

## B. Evidence

Journal 格式及 registered subjects／owner／decisions 已與 RunScope(run_id) 重建結果相符。original_control 的 sequence／revision 為 0／0，三開關均 OFF；Migration 2b2-b1、applied 及 checksum 與現有 migration_plan 相符。Journal 沒有完整原始 Schema fingerprint、原始 pytest nodeid／phase、worker outcome 或逐 DELETE 進度；不能憑空補造。

清理前快照記錄 **15 筆業務列**，加 Control／Registry 各一列，共 17 列。下表僅是歷史記錄與待核對候選，**不是現存資料或核准刪除清單**：

| 表 | 快照筆數 | 已記錄 PK／關聯識別 |
|---|---:|---|
| users | 1 | synthetic discord_user_id 4000089534685964 |
| songs | 1 | song_id 58 |
| jb2_versions | 1 | 本 Run 的 :v1 |
| jb2_videos | 1 | video_id 1955b45be54 |
| jb2_events | 1 | 本 Run 的 :1 |
| jb2_receipts | 1 | 同 :1 operation |
| jb2_decisions | 1 | 同 :1:approve |
| jb2_reminders | 1 | 同 video_id |
| jb2_review_history | 3 | 同 video_id，revision 0／1／2 |
| jb2_evidence | 4 | evidence_id 1／2／3／4；仍須 live parent／actor／payload 核對 |

其餘 11 張業務表快照為空，包括 credit_ledger／jb2_claims／jb2_settlements；不能據此保證現在仍為空、balance 一定正確或沒有後續寫入。Ledger idempotency key 可由 `jb2:` + SHA256(operation_id) 回查，不能只靠 Run ID 前綴推定歸屬。

Observed control 是清理前 snapshot：accepted_sequence=1、revision=2、三開關 ON。disable_only 的現有流程會先 rollback 原清理交易，再新連線僅關閉三開關並 commit，不重設 counters。Journal 沒有 shutdown／rollback failure，與流程成功相容，但沒有保存當前重新 SELECT 的值。**目前 Control OFF 與否、15 列是否仍在、是否有新增不明列，全部尚未 live 驗證。**

原 TestCounts 的 passed 只計 call，failed 會計 setup／call／teardown。因此 1 passed + 1 failed 完全可能是 call 成功、teardown 1451。suite 第一項是 test_real_batch_b_workflow[persistence]，此 run 的一筆 operation + approve + 無 reward 也相符；但缺失原始 pytest nodeid／phase，這僅是強烈推論，不能宣稱歷史已獨立確認。沒有證據顯示 business assertion、未知 commit 或重複發幣首先失敗。

## C. Root Cause

已確認：cleanup 發生 1451 外鍵阻擋；最後總 baseline 比較不相等。不能把最後 FINAL_BASELINE_MISMATCH 當成最初根因。

未確認：1451 所屬表、PK、constraint、失敗前哪些 DELETE 已送出、以及當前已提交結果。原子 cleanup 在 DELETE 完成前不會 commit；後續 rollback 路徑應撤銷之前的 uncommitted deletes，但仍需 live SELECT 核對，不能把「曾執行」當成「已清除」。

靜態 FK 順序涵蓋全部已審查 child→parent 連結；jb2_versions 另有 canonical_id→version_id self-FK，root 自指。現有 leaf 選擇忽略 self edge。這是需調查的特殊情況，**尚不能把它定為此次 1451 根因或宣稱已修正**。不得據此改 schema、停用 foreign_key_checks、增權或採用全表刪除。

## D. Code Changes

實際檔案：scripts/run-batch-b-acceptance.ps1、tests/batch_b_acceptance.py、tests/batch_b_harness.py、tests/batch_b_harness_database.py、tests/batch_b_recovery.py（新增）、tests/test_batch_b_recovery.py（新增）、tests/test_batch_b_acceptance_launcher.ps1、docs/batch-b-real-mysql-tests.md，以及本報告（新增）。沒有修改 Repository、Transaction、Schema 或 Migration。

- 既有 Harness journal 新增 scenario STARTED／safe worker outcome 與 exact PK cleanup progress。DELETE_ATTEMPT、DELETE_EXECUTED_UNCOMMITTED、ROLLBACK_ACKNOWLEDGED 分開，不表示 commit。
- 既有 acceptance report 保留 pytest nodeid／when／outcome，不保留原始 traceback 或 driver message；call pass 與 teardown failure 可區分。
- FINAL_BASELINE_MISMATCH 增加 baseline_diff：23 表 counts／content_changed、數字 Control 差異、Schema drift table names，不輸出未知 row payload。
- 新增純唯讀 recovery helper，接到**同一個** PowerShell／Python 入口的 --inspect-recovery 路徑；沒有新 Launcher、Preflight 或寫入恢復工具。固定 Harness／127.0.0.1:3306/jukebox101_test，使用 journal 的既有 UUID pin；不使用 DBA／Migrator。
- 回歸測試：read SQL guard、credential-store path 拒絕、唯一固定目標、read/write mode 分離、synthetic 1451 的 exact PK attempt、pytest phase accounting、未知 PK 不輸出、Control 純模擬、journal 不修改及 close failure。

## E. Verification

完整 Batch A／Batch B 安全離線回歸：**776 Passed／0 Failed／23 Skipped**（新增 28 個 recovery 案例）；全部 23 個真實 MySQL 案例跳過。PowerShell 純 Mock：**44 項斷言通過**，未啟動 Python child。Read-only audit hook 阻擋 socket／subprocess／.env／檔案寫入，`READONLY_GUARD_BLOCKED_EVENTS=[]`。另以 AST 核對四個變動中的 Python runtime 模組函式 ≤25 行、巢狀 ≤3 及 docstring，通過。原 journal SHA256 在修改／測試後再次核對不變。

本輪未連線 MySQL，未讀取任何真實密碼、未執行任何資料庫寫入、未執行 A＋B、未修改 Schema／Migration／grants／account／session block。

在無互動憑證的 Codex 環境不能完成 R3。唯一需要的使用者端命令為：

```powershell
powershell.exe -NoProfile -File "D:\Project\MusicBased\discordBot_JukeBox101\scripts\run-batch-b-acceptance.ps1" -RecoveryJournal "C:\Users\Yume\AppData\Local\Temp\jukebox101-batch-b-hst16g0d\run.json"
```

**不要省略 -RecoveryJournal，也不要改跑 A＋B。** 停止其他寫入／DDL，輸入 READONLY 並遮罩輸入一次既有 Harness 密碼。不是重新建立 Harness，不需要 DBA／Application 密碼。密碼只經既有匿名 stdin；不寫檔、不放命令列、不讀 credential store。一般 MySQL 連線 charset 協商由 driver 執行；診斷沒有 init_command、SET time_zone、BEGIN、GET_LOCK、Control no-op UPDATE 或 cleanup。SQL adapter 只允許固定 SELECT／SHOW，所有 real-test switches OFF。

報告包含所有 23 表 counts、記錄 PK 現存／缺失、未登記 PK 數、當前與原 Control、關聯／帳務／payload 驗證結果、可見 FK 規則及當前 Schema fingerprint。它不暴露未知 row payload，也不產生 DELETE 授權。兩次 SELECT inventory 一致只能偵測部分 concurrent change，**不是持有 write lock 的原子快照**，仍要求停止所有其他 writers。Harness 沒有完整 DBA trigger 視野，也沒有歷史 Schema fingerprint，不能宣稱獨立排除 hidden metadata drift。

請只提供 sanitized report，不提供密碼、原始資料 dump 或完整 journal。如未知列／驗證失敗／當前資料有變，停止，不能自動清理。

## F. Restoration Proposal — conditional, not approved for writes

1. 先取得上述唯讀 report，核對 SHA256、UUID、23 表、Registry、Control、所有關聯與 PK 差異。原 journal 缺失的歷史結果仍標示 unknown。
2. 只有 live rows 與已登記 roots／parent links／actor／payload／ledger keys 全部相符，才可把相符列列為「本 Run 可歸屬」。recorded_missing 只代表現在不在，不證明由本次 cleanup 刪除。unregistered rows 不納入刪除；有任何不明資料即中止。
3. 另行確認 1451 的阻擋目標及 actual FK rules；若目前 Schema／權限無法完成精確清理，停止並回報，不能以解除 FK 或擴權替代。現在尚無無條件可執行的 DELETE 清單，不能假稱恢復方案已可執行。
4. 待使用者審核 live 清單及另行授權，才可在單一受控交易內取得既有 control gate、重新核對 current inventory，依 FK child-before-parent 對核准 PK 精確 DELETE。歷史 snapshot 的候選次序為 review_history／evidence／receipts／reminders／decisions → event → video → song → user，versions 的 self-FK 處置仍待確認；Ledger／Claim／Settlement 快照為零，不能預先授權刪除後來出現的列。
5. 必須等所有核准 rows 已移除、無其他業務 rows／未知 references，才在同交易將五個 Control 欄位還原 original_control（此 run 為 counters=0／0、三開關 OFF）。不能先清 counters 來隱藏殘留，不修改 singleton/schema_version/Registry，不開啟結算。
6. 任一 DELETE、FK、rowcount、ownership 或 Control 驗證失敗即 rollback、保留進度與主錯誤，停止；不嘗試不同清理範圍。commit outcome unknown 時先獨立只讀核對，不重複 DELETE。
7. commit 後以新連線核對全部 23 表與完整基線、Control、Registry、必要 Schema；成功也不自動清 Session Block、不重跑測試、不標記 Accepted。後續真實測試須另行授權，使用已存在 Harness，不重建 Stage A。

## G. Batch B Status

- Harness Provisioning：Completed（使用者已回報）。
- Real MySQL Integration：Failed／Incomplete；目前歷史為 1 call passed + 1 phase failed，非 23 測試通過。
- Environment Restoration：Pending；當前 rows／Control 未驗證。
- Batch B Manual Acceptance：Pending。
