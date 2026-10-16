# Batch B：1826 Hotfix 與新 Recovery 授權方案

## 最新 checkpoint：1045，Recovery 仍 Blocked

後續一次性執行停於 `R_FK_PRECHECK / OperationalError 1045`，該次尚未執行 FK DDL 或 Manifest DELETE。舊診斷無法追溯辨識 DBA／Harness；目前已新增 `R_FK_DBA_CONNECT`、`R_FK_HARNESS_CONNECT` 與各自的 `SESSION_SETUP` 安全標記，不輸出原始驅動訊息或密碼。真實登入尚未重新驗證，Recovery 尚未成功，Session 仍 Blocked；重新執行需要新的明確授權。下文為先前 1826 修正證據及候選執行方案，不是恢復成功紀錄。

狀態：**READY_FOR_NEW_RECOVERY_APPROVAL**。本輪只有 SELECT/SHOW 診斷、離線修正及測試；沒有執行任何真實 DDL/DML、Recovery、Stage A/B 或 FK Lab。Session 仍 Blocked。先前一次性 Recovery 授權已消耗，不能自動重試。

## 唯一事故與原始證據

- 127.0.0.1:3306/jukebox101_test，server UUID `ab73bd54-c3c3-11f1-bc47-005056c00001`。
- Run ID `14d8aee30b034a9883e9f71cdec8146e`。
- 原始 Journal `C:\Users\Yume\AppData\Local\Temp\jukebox101-batch-b-hst16g0d\run.json`，SHA256 `6a75a0bd379a08fa1f57992187b039f75f19bde1c51f7e457bc61dd0fcbfd71d`。
- 失敗 Recovery Journal `C:\Users\Yume\AppData\Local\Temp\jukebox101-recovery-8odr8mus\recovery.json`，SHA256 `b4b728e4de2e136dcb83b0222cd3a997c0f94e7066fbc83dbdbf92e586d669ee`。
- 原始 Manifest `docs/batch-b-recovery-manifest.json`，SHA256 `10cf5cac90d8fa47ccfd6a15ae9ed82e2bf365c6bbb2855cbbe8ae968b52a3a2`。它仍標記 writes authorized=false；新執行授權不透過修改這份原始證據表示。
- 原 Schema 指紋 `5f201eabfb5828f80029497e3eca81432ed99a81aebbd15f2a23515cc93c62a8`。每次 fresh metadata 必須符合這個固定值，不能採信當次未知結構。


新事故 Journal：`C:\Users\Yume\AppData\Local\Temp\jukebox101-fk-recovery-kx8ef4k_\recovery.json`，SHA256 `c91b55f7fd929968d8bb44daf8bcf9efa8865176e5e1bcf9de9c59f7361fdeb4`。記錄 R_FK_FAILED_BLOCKED、cascade_ddl_outcome=UNKNOWN、OperationalError 1826、failure_step=R_FK_DDL_ATTEMPT、cleanup_progress=[]。沒有 DML Commit／Rollback 或 restore DDL 結果，不能假稱已回滾 DDL。

## 已確認的舊 helper 缺陷

事故 Journal 沒有保存 SQL 原文；依事故當時 helper 與已驗證 Schema 重建的固定結構如下。這是故障證據，**不得執行**：

```sql
ALTER TABLE `jukebox101_test`.`jb2_versions`
  DROP FOREIGN KEY `fk_jb2_version_root`,
  ADD CONSTRAINT `fk_jb2_version_root`
  FOREIGN KEY (`canonical_id`) REFERENCES `jb2_versions` (`version_id`)
  ON DELETE CASCADE;
```

MySQL 1826 是 ER_FK_DUP_NAME。舊 SQL 在同一 ALTER 中重新 ADD 已存在的 FK 名稱，發生名稱衝突。另有不能忽略的演算法限制：MySQL 8.0 文件指出，同一 ALTER 的 ADD/DROP FK 僅支援 INPLACE，而 foreign_key_checks=1 時 ADD FK 僅支援 COPY。不能只換名字仍沿用 ADD/DROP 合併語句，也不能關閉外鍵檢查。

來源（查閱 2026-10-16）：[1826 定義](https://dev.mysql.com/doc/mysql-errors/8.0/en/server-error-reference.html)、[ADD/DROP 限制](https://dev.mysql.com/doc/refman/8.0/en/create-table-foreign-keys.html)、[FK 檢查與 COPY](https://dev.mysql.com/doc/refman/8.0/en/innodb-online-ddl-operations.html)。

2026-10-16 修正後唯讀診斷已完成（READONLY_COMPLETE）：CURRENT_USER=root@localhost、UUID 與已核准值一致、MySQL 8.0.46、foreign_key_checks=1。實際只有原 fk_jb2_version_root，DELETE_RULE／UPDATE_RULE 均為 NO ACTION；沒有暫時 FK，alias_count=0。實際 23 表 Schema fingerprint 精確等於原始 5f201eabfb5828f80029497e3eca81432ed99a81aebbd15f2a23515cc93c62a8。這是新 SELECT/SHOW 的證據，不是從 1826 推定 DDL Rollback。

## 最小修正：不同名稱、ADD 優先、四條獨立 DDL

共用既有 helper，不新增 launcher。暫時名稱固定為 `fk_jb2_version_root_recovery`，只保護相同 canonical_id→version_id 關聯；不改資料欄位、索引、歌曲身分或其他 FK。

下列為**下一次新授權範圍**，本輪不執行：

```sql
-- 1. 原 NO ACTION FK 仍存在時，先加不同名稱的 CASCADE FK。
ALTER TABLE `jukebox101_test`.`jb2_versions`
  ADD CONSTRAINT `fk_jb2_version_root_recovery`
  FOREIGN KEY (`canonical_id`) REFERENCES `jb2_versions` (`version_id`)
  ON DELETE CASCADE;

-- 2. 完整驗證雙 FK bridge 後，才移除原名稱。
ALTER TABLE `jukebox101_test`.`jb2_versions`
  DROP FOREIGN KEY `fk_jb2_version_root`;

-- 在唯一已驗證的暫時 FK 下執行既有精確 Recovery DML 交易。

-- 3. DML Commit ACK 且資料 baseline 驗證後，先補回原 FK。
ALTER TABLE `jukebox101_test`.`jb2_versions`
  ADD CONSTRAINT `fk_jb2_version_root`
  FOREIGN KEY (`canonical_id`) REFERENCES `jb2_versions` (`version_id`);

-- 4. 驗證雙 FK bridge 後，才移除暫時名稱。
ALTER TABLE `jukebox101_test`.`jb2_versions`
  DROP FOREIGN KEY `fk_jb2_version_root_recovery`;
```

第 3 條使用經固定指紋核准的原始 SHOW CREATE clause，原本有明列 ON DELETE／ON UPDATE NO ACTION／RESTRICT 就原樣保留；沒有明列則不額外加入，保證可回到原指紋。

每條 DDL 都是獨立隱含 Commit，不與 DML 共用交易。這個順序永遠先新增可驗證的替代 FK，再移除前一個 FK，不產生無 FK 保護空窗，也不使用同一 ALTER 的 ADD/DROP 組合。雙 FK bridge 期間不允許任何 DELETE／UPDATE 業務操作，不依賴兩個不同 action 的執行先後。

## 精確 metadata 與失敗阻擋

- 原始狀態：原名稱及原 action、26 個 FK、完整 23 表指紋符合固定值。
- bridge 狀態：只增加暫時名稱的同欄位 CASCADE FK，共 27 個 FK。只允許兩個已知 FK 的兩種 SHOW CREATE 列舉順序，其餘每個位元組與原 metadata 相同。不得忽略任意結構差異。
- 暫時狀態：只有固定暫時名稱 CASCADE，其他 FK 完整不變，共 26 個 FK。Recovery DML 及一般 Cleanup 只允許此狀態，不能在 bridge 或未知 schema 中開始。
- 還原狀態：原名稱與 action、全部原始 Schema fingerprint 精確恢復，暫時名稱消失。

沿用固定 UUID、23 表完整 inventory、Migration Registry、四份既有原始證據 hash（包含 1826 Journal）、15 PK 歸屬及 Control precondition 檢查。每次 DDL 前後與兩步之間，都重新驗證 DBA 權限、無其他 foreground session／scheduled event、所有 FK／外部入向參照、完整 Schema 及資料未變。

使用者的獨占承諾仍是必要前提：advisory lock 和瞬時 process list 不能阻止不配合的新 writer／管理者。不能確認時停止，不殺其他 session 或擴權。DDL 前釋放所有讀取交易的 metadata locks；既有 lock timeout／I/O timeout 保留。

每條 DDL 先寫入新 attempt journal 的 `fk_ddl_progress`：方向、步驟、固定且無參數的 reviewed SQL、PENDING／ACKNOWLEDGED／UNKNOWN。通過 metadata checkpoint 後另記 `fk_verified_state`。任何失敗立即停止後續步驟，1826 也保持 UNKNOWN，絕不從錯誤碼推定 Rollback 或自動重試。

- ADD 成功、DROP 失敗：可能停在雙 FK bridge；保留來源 FK，不執行 DML，不自動補完 DROP。
- DROP 已 ACK 但後置驗證失敗：最後一步可能完成，但總 outcome 仍 UNKNOWN；只允許新的唯讀調查。
- DML 一般錯誤且 Rollback ACK：僅在 fresh Schema、完整原資料與獨占條件仍全部證實時，允許使用同一 helper 做原 FK 還原。安全條件失敗或 Unknown Commit 不執行補償寫入。
- 還原 ADD 成功、移除暫時 FK 失敗：即使原 FK 已存在，仍可能是 bridge，不能稱原 Schema 已恢復，保持 pending／Blocked。
- 現在已是 CASCADE、bridge 或 FK 缺失：不自動 resume，原始 preflight 仍拒絕；需另行唯讀確認及新的精確授權。

本事故精確目標（15 筆）：

| 依 FK 清理順序 | PK | 筆數 |
| --- | --- | ---: |
| jb2_receipts | operation_id=Run:1 | 1 |
| jb2_review_history | video_id=1955b45be54，revision=0,1,2 | 3 |
| jb2_evidence | evidence_id=1,2,3,4 | 4 |
| jb2_reminders | video_id=1955b45be54 | 1 |
| jb2_decisions | decision_id=Run:1:approve | 1 |
| jb2_events | operation_id=Run:1 | 1 |
| jb2_videos | video_id=1955b45be54 | 1 |
| songs | song_id=58 | 1 |
| users | discord_user_id=4000089534685964 | 1 |
| jb2_versions | version_id=Run:v1，自身 canonical_id 不修改 | 1 |

Run 代表上述完整 Run ID。其他允許清理表在本 Manifest 為空，不執行 DELETE。不刪除 jb2_control、jb2_migrations，不清空整表、不重設 AUTO_INCREMENT。

同一 Harness DML 交易內核對 Control 仍為原 precondition，再將 accepted_sequence/revision 恢復 0/0，三開關維持 OFF。保留 singleton_id/schema_version 與唯一 Migration Registry 的完整原值。所有 21 張業務表為空、Control 與 Registry 和原 baseline 完全一致，才允許 Commit。



## 一般 Harness 與既有入口

一般 Cleanup 共用同一 helper／metadata scope 與 alias-before-root 檢查。每個版本 DELETE 前仍需確認非自身子列為零；即使 alias 已登記但尚未單獨刪除，也拒絕依賴隱含級聯。Harness 不取得 DBA 或 DDL 權限。

一般 suite 仍需在後續授權中接入同一 DBA window supervisor，不能直接重跑舊 Stage A/B；目前未宣稱 23 項真實驗收完成。

下一次只有經使用者重新核准上述四條 DDL、既有 15 PK DML + Control 0/0/OFF、有限且已述的失敗處理與 fresh 驗證後，Codex 才能使用原入口：

```powershell
& 'D:\Project\MusicBased\discordBot_JukeBox101\scripts\run-batch-b-acceptance.ps1' -RepairRecoveryFk -RecoveryJournal 'C:\Users\Yume\AppData\Local\Temp\jukebox101-batch-b-hst16g0d\run.json'
```

這是新的執行授權，不能沿用已失敗的一次性執行。程式不自動開新 Recovery、Lab、A/B，不修改帳號、Migration、canonical_id 或 Session Block。原始事故 Journal／Manifest 均不寫入。

本輪修改只限共用 FK helper、必要的 metadata／checkpoint 接線、原 launcher 的安全輸出欄位、相關測試與文件。新增四步 DDL 在既有 schema 上尚未實測；離線 Mock 不代表真實 ALTER 已通過。


## 本輪驗證與變更清單（2026-10-16）

完整受控離線回歸：**956 Passed／0 Failed／23 Skipped**，最後 READONLY_GUARD_BLOCKED_EVENTS=[]。PowerShell Mock **88 項斷言通過**；沒有執行 Recovery 或 FK Lab。涵蓋 1826、每一方向兩個 DDL 的部分成功／UNKNOWN、bridge 不符後停止、已為 CASCADE 不自動 resume、FK 缺失、原 NO ACTION、還原失敗及四份原始證據保護。離線結果不代表四步 ALTER 已通過 MySQL。

修改檔案共九個：

- `tests/batch_b_fk_window.py`：分離 ADD／DROP、暫時名稱、精確 bridge、逐 DDL journal。
- `tests/batch_b_recovery_write.py`：固定原／暫時／bridge 的完整 FK 名稱與 action 集合。
- `tests/batch_b_recovery_fk.py`：每個 DDL checkpoint 共用原完整 boundary，加入 1826 Journal hash pin。
- `scripts/run-batch-b-acceptance.ps1`：只增加已審核 DDL progress／verified state 的 sanitized 輸出欄位，沒有新增 launcher。
- `tests/test_batch_b_fk_window.py`、`tests/test_batch_b_acceptance_launcher.ps1`：回歸與 Mock。
- `docs/batch-b-recovery-fk-plan.md`、`docs/batch-b-recovery-investigation.md`、`docs/batch-b-real-mysql-tests.md`：同步本次停止狀態與新授權範圍。

本輪首次一次性唯讀診斷的 LIKE 字面 `%` 被 PyMySQL 參數格式化拒絕（ProgrammingError），沒有寫入；後續改用 `%s` 綁定 LIKE 模式，重用既有遮罩輸入／匿名 stdin。該診斷不是 Recovery 重試。修正後 readonly report 已完成，路徑 C:\Users\Yume\AppData\Local\Temp\jukebox101-1826-readonly-fdf17bd05aa744ca88de999fa38c8db7\result.json。完整 Manifest 歸屬、payload、帳務與精確 PK 驗證通過，15 筆均存在、沒有額外業務列；Control 維持 accepted_sequence=1、revision=2、三開關 OFF；Registry 維持 2b2-b1／applied 與原 checksum ad9794a3d2f84b73c2d81edfeb83898a700aaee52fcfd3e9613f475c3e7fb843。兩次 inventory／Schema 比較無漂移。原事故 block 與 pending 記錄不修改。下一次仍必須 fresh preflight，不把這份報告當成永久執行許可。
