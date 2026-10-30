# Batch B：單次 FK Lab（實驗已完成；Recovery 尚未授權）

最新狀態：2026-10-16 已獲使用者授權並完成三項真實 MySQL 實驗，三項皆通過。實驗資料均 Rollback、三張實驗表清理完成，保留空實驗庫；沒有操作 jukebox101_test，不代表 Recovery 或 Batch B 已驗收。以下準備與歷史離線結果保留供稽核。

## 下一次合併授權範圍

僅在既有 `127.0.0.1:3306`、經 UUID 核對的 MySQL 8.0 server，以現有 DBA 身分執行以下一次性操作。不新增帳號、GRANT、服務或套件。

1. 不指定預設資料庫連線，SELECT／SHOW 核對 server UUID、版本、帳號、外鍵檢查開啟及既有 DBA 的全域 metadata 可見性。取得專用 advisory lock。若 `jukebox101_fk_lab` 已存在，停止，不接管或清空。
2. CREATE DATABASE `jukebox101_fk_lab`；只建立 `probe_no_action`、`probe_cascade_root`、`probe_cascade_alias` 三表。VARCHAR(128)、NOT NULL、InnoDB、utf8mb4_bin 與原版本身分鍵一致；FK 名稱在 schema 中各自唯一。
3. 各表獨立交易：INSERT self-root；第三表另 INSERT alias；精確 DELETE root；SELECT 剩餘筆數；finally ROLLBACK。預期依序為 1451／剩一筆、成功／剩零筆、成功／root 和 alias 均刪除。這些是待驗證預期，尚非實測。
4. 三項都符合預期且 Rollback 確認後，核對三表完整集合與全空，才 DROP 這三張實驗表並核對無表。保留空的 lab database，**不 DROP DATABASE**。

使用既有 DBA 權限：lab 的 CREATE、INSERT、SELECT、DELETE、DROP、REFERENCES；metadata SELECT／SHOW 與 session/advisory-lock 操作。程式保守要求現有全域 ALL PRIVILEGES 或等價完整 MySQL 8.0 靜態權限列舉、WITH GRANT OPTION，並拒絕可見部分 REVOKE 的 DBA，以確保「lab 不存在」不是 metadata 權限遮蔽；這不是申請新增全域權限。若既有 DBA 不符合就停止。

DDL 會隱含 Commit，無法透過 ROLLBACK 撤銷。任何 DDL／網路／Rollback／結果不符錯誤，停止且不重試，保留 lab 供唯讀調查；不自動清除部分失敗證據。每次連線和 I/O 有 timeout，InnoDB lock wait 為 2 秒。執行期間不得有其他程序或 DBA 修改 lab；advisory lock 僅約束配合者。密碼只經既有 SecureString→匿名 stdin 管道，會短暫存在 PowerShell／Python 記憶體，無法承諾可完全抹除。

## 核准後的唯一啟動入口（本輪未執行）

```powershell
& 'D:\Project\MusicBased\discordBot_JukeBox101\scripts\run-batch-b-acceptance.ps1' -FkLab
```

輸入既有 DBA 名稱、先前核准的 server UUID，以及一次遮罩密碼。不需逐條 SQL 或複製 JSON；程序自行判定三項結果、Rollback 與清理，輸出摘要及原 exit code。此命令本身不構成使用者已授權資料庫操作，仍需先明確核准以上完整範圍。

沿用既有 launcher 的清除繼承憑證、關閉所有 JUKEBOX_RUN_* 開關、丟棄 raw stderr、釋放 SecureString 與 child 的防護。`-FkLab` 與 Recovery 參數互斥；不會執行 A/B、Recovery、Migration，不讀 `.env`，不選取 `jukebox101_test`，不改 Journal／Manifest／Session Block。

## Lab 之後的最短路徑

Lab 成功只證明 server 行為，不自動授權更改測試庫 FK。依實測提出一次精確 Recovery 變更授權，包含 Schema 指紋變化與 DDL 不可交易限制；保持 Manifest、未知子列拒絕及 Commit Unknown 保護。

必須同時處理一般 Harness Cleanup 的 self-root 問題，否則只恢復事故資料後重跑仍會再次阻塞。目前沒有證據證明另有業務斷言缺陷；先區分 call failure 與 teardown failure，不猜測後修復。既有 A/B 入口會重新做 Stage A，不能直接拿來重跑已完成的 Provisioning；真實測試應重用現有 pytest/Harness，待基線復原及新授權後執行，無需重做帳號或 Migration。

Beta 仍需：恢復並驗證事故 Baseline、23 項 Batch B 真實測試通過及無殘留、人工驗收、DB-enabled Discord 整合與人工測試，以及真實影片驗證／YouTube 政策和正式獎勵開關的明確產品授權。Lab 不會啟用任何 Beta、API 或 Nickel 業務操作。

## 本輪離線驗證（2026-10-16）

沿用 `docs/batch-b-real-mysql-tests.md` 的 audit-hook 防護命令，加入 `test_batch_b_fk_lab.py`、`test_batch_b_recovery.py`、`test_batch_b_recovery_write.py`、`test_batch_b_acceptance.py`，執行既有完整 Batch A／Batch B 離線清單：**852 Passed／0 Failed／23 Skipped**。網路、`.env` 讀取、檔案寫入與 subprocess 防護無觸發（READONLY_GUARD_BLOCKED_EVENTS=[]）。23 Skipped 均為未執行的真實 MySQL 測試。

另外執行 `powershell -NoProfile -File tests/test_batch_b_acceptance_launcher.ps1`：**63 項 Mock 斷言通過**，沒有啟動 Python child 或 MySQL。覆蓋 lab 單一遮罩憑證、專屬 dispatch、互斥模式與既有 Recovery／A/B 行為。Mock 結果不代表三項 InnoDB 實驗已通過。


## FK Lab Preflight Hotfix 與真實結果（2026-10-16）

唯讀確認 CURRENT_USER() 為 root@localhost、MySQL 8.0.46、server UUID 為使用者已明確核准的 ab73bd54-c3c3-11f1-bc47-005056c00001。SHOW GRANTS 實際逐項列出完整靜態權限及 WITH GRANT OPTION，沒有輸出 ALL PRIVILEGES 固定字串；沒有可見部分 REVOKE，lab 不存在。故先前拒絕為表示格式誤判，並非已證實缺權限。

最小修正只讓 tests/batch_b_fk_lab.py 辨識兩種等價完整全域授權。重用既有 full_metadata_authority，仍要求全部 30 項已知 MySQL 8.0 靜態權限或 ALL、grant option、無可見部分撤銷；不把 schema-level、未知格式或缺任一項權限視為通過。未修改 Recovery Write、Migration、帳號或任何權限。

受影響離線回歸：343 Passed／0 Failed／23 Skipped，READONLY_GUARD_BLOCKED_EVENTS=[]；PowerShell Mock 63 項斷言通過。新增 38 項參數化驗證包含完整列舉／ALL、每一項缺失、schema-only、未知格式及部分撤銷。23 Skipped 是既有 Batch B 真實測試，與以下三項已實測 Lab 分開計數。

| 真實實驗 | DELETE 結果 | DELETE 後交易內列數 | Rollback |
| --- | --- | --- | --- |
| NO ACTION self-root | MySQL 1451 | 1 | ACKNOWLEDGED |
| CASCADE self-root | 成功 | 0 | ACKNOWLEDGED |
| CASCADE root + alias | 成功，兩筆皆移除 | 0 | ACKNOWLEDGED |

Stage 為 FK_LAB_COMPLETE，lab_cleaned=true、session_blocked=true。結果檔案：C:\Users\Yume\AppData\Local\Temp\jukebox101-fk-grants-0e0afbbcb3bc4bc39526114068bd9737\result.json。密碼只在既有 SecureString／匿名 stdin／受控程序記憶體中，未寫入結果或檔案；本次以一次輸入保留短期記憶體憑證，離線測試通過後傳遞非機密單次 resume 訊號，再由同一 lab executor 重新核對全部安全條件。

已在同一 MySQL 8.0.46 上重現 self-root + NO ACTION 的 1451 行為；歷史 Recovery 未記錄 constraint 名稱，仍不能追溯證明當次唯一阻擋來源。CASCADE 也會刪 alias，因此不得未經 Manifest 全集及入向參照驗證就採用。下一步僅提出獨立 Recovery 修復授權；不自動變更測試庫 FK 或放寬現有禁止 CASCADE 的 Recovery 檢查，不執行 Recovery／A/B、不解除 Block。
