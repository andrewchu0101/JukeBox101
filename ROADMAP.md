# JukeBox101 Roadmap

Stage-B-only 目前狀態：**STAGE_B_ONLY_OFFLINE_READY_FOR_REVIEW**。已在既有 Harness 接入單次新 Session、受控四步 FK Window、原 23 項套件與逐 Commit／精確 Manifest 清理稽核；完整離線回歸 1228 Passed／0 Failed／55 Skipped，PowerShell Mock 119 斷言 Passed。未連線或執行真實 Stage B；舊 Session 仍 Blocked。下一次的三段授權與最短步驟見 [Stage-B-only／私人 Guild 實跑](docs/stage-b-only-run.md)。這取代下文「尚需離線補接 supervisor」的歷史待辦，不代表 Batch B 或 Beta 真實驗收已通過。


目前私人 Beta 接線：已完成既有 `/share` → Batch B Pending 持久化、`/history` 本人投稿／目前審核狀態、`/balance` 原餘額查詢；正式入口強制指定私人 Guild／Application／Owner，缺設定 Fail Closed。**程式已接線／離線通過，不代表真實可用**。Session 仍 Blocked，Batch B 真實 MySQL 驗收未完成；本輪沒有外部操作，資料庫三開關維持 OFF。完整離線結果 1163 Passed／0 Failed／55 Skipped，PowerShell Mock 90 斷言 Passed。後續所需操作與授權見 [私人 Beta 接線紀錄](docs/private-beta-implementation.md)。


目前狀態（2026-10-16）：**RECOVERY_SUCCESS**。四條 FK DDL 與 DML Commit 全部 ACKNOWLEDGED；原 Manifest 15 PK 零殘留、非 Manifest 異動 0；Control 0／0、三開關 OFF；原 FK／Schema／Migration 全部恢復並驗證；Recovery Exit Code 0。依使用者提供的已驗收結果記錄，本輪未連線複驗。Session 仍 Blocked，Batch B 真實 MySQL 測試尚未完成，不標記通過或正式可用。

| Phase | 範圍 | 狀態 |
|---|---|---|
| 1 | Foundation & MySQL Integration | Accepted；Phase 1A～1D 已由使用者人工驗收，真實 MySQL 整合 14 Passed／0 Failed／0 Skipped，測試前後六表皆為 0 筆，無測試資料列殘留 |
| 2A | Discord Bot Application Foundation | Repository 隔離整合及 DB-disabled Interaction Transport 分項已驗收；真實 Discord＋MySQL 持久化互動 Pending，資格／發點／交換交易／Daily 派送未實作；整體不標記全面完成 |
| 2A.1 | Repository Integration Acceptance | Accepted（使用者人工回報 13 Passed／0 Failed／0 Skipped、7.19 秒，前後六表全 0，無殘留）；本次未重跑 |
| 2A.2 | Discord Registration | Offline Preflight、REST Login、Development Guild Sync、五指令顯示均人工 Passed；後續 transport 驗收另列 2A.3 |
| 2A.3 | DB-disabled Gateway Interaction Transport | Accepted — User Verified；人工 Preflight／Application／Guild／Gateway Ready Passed、Remote Command Contract Matched；五組互動整體確認，逐字／第二帳號隱私／異常案例未逐項獨立驗證；歷史離線 298 Passed |
| 2B.0／2B.0A | 唯讀架構與政策研究 | 已完成文字審查；YouTube API＋Nickel 獎勵政策適用性仍 UNRESOLVED，不視為官方核准 |
| 2B.1 | Offline Song Eligibility Contract & Hybrid Review Foundation | Implemented／Offline Tested／Manual Acceptance Pending；452 Passed，只有純邏輯，未接外部驗證或發幣 |
| 2B.2 Implementation 01 | 離線版本身分、投稿優先權與獎勵候選 | Implemented／Offline Tested／Manual Acceptance Pending；完整離線 530 Passed，僅純決策與核准台詞介面，未接資料庫或發幣 |
| 2B.2 Batch A | 完整離線投稿與獎勵協調 | Implemented／Offline Tested／Manual Acceptance Pending；安全回歸 436 Passed，獨立 manifest、未知歷史保護、原回執重送及核准台詞池；未實作可靠 DB 順序、持久化或發幣 |
| 2B.2 Batch B | MySQL 持久化與原子 Nickel 結算程式 | Implemented／Offline Tested／Real MySQL Integration Pending／Manual Acceptance Pending；492 個安全離線案例 Passed，含 56 個新增案例；Migration 未執行、未連線或實際發幣，啟用開關預設關閉 |
| 2B.2 後續子階段 | 真實驗證與 YouTube Submission 業務 | 未開始；整體未完成，需另行授權，API 政策、持久化、合併與交易細則尚待確認 |
| 3 | Music Exchange | 未實作 |
| 4 | Daily Delivery | 未實作；需落實交易、防重、uncertain 恢復 |
| 5 | Spotify Integration | 延後；需批准 metadata/API 與跨平台識別方案 |
| 6 | Cloud Deployment | 未開始；需另行批准 |

Phase 2A 詳細狀態與驗收步驟見 [驗收報告](docs/phase2a-acceptance.md)。正式既有 `/share` 仍只保存 pending 嘗試，不寫 ledger、不發點、不加入交換池；`/exchange` 維持 Pending，不操作資料庫。授權的 2B.1、Implementation 01、Batch A 與 Batch B 均未由本次標記 User Accepted。Batch B 新 Repository 具備原子首次獎勵程式，但尚未經真實 MySQL 驗證，也未接入上述正式入口；YouTube API 政策仍 UNRESOLVED，控制開關預設關閉。見 [Batch B 驗收文件](docs/phase2b2-batch-b-acceptance.md) 的 Migration、交易及真實整合待驗事項；Batch C 未開始。

Phase 2B.1 [離線驗收文件](docs/phase2b1-offline-acceptance.md)：重用 URL Parser；單影片附帶 Playlist／時間戳記參數仍為同一 Video ID。可靠完整影片時長 60～720 秒（含）才 Duration PASS，並非歌曲核准。審核狀態、核准來源、驗證錯誤與投稿去重分開，使用版本辨識過期結果；核准／拒絕終態沒有重新開啟或撤銷入口。錄音身分只預留參照，不實作跨版本合併。Nickel 只評估未來發幣必要前置條件，不證明全域首次、不寫 Ledger、不改餘額。初始餘額、Owner Grant、退款、撤銷與真實派送均未新增。YouTube API 政策仍待官方確認，外部操作需另行授權。

Phase 2B.2 [Implementation 01 驗收文件](docs/phase2b2-implementation01-acceptance.md)：新增獨立版本映射、身分關係與可信排除證據；以完整候選快照及模擬受理序號評估等待身分／資格／前序、READY、AWARDED、INELIGIBLE、CONFLICT。歷史版本 +0，不追溯或重開首次獎勵；未知身分不樂觀當新歌；衝突不自動追回既有 Nickel。五句核准文案僅為離線映射，沒有新指令或部署。Phase 2B.1 介面保持不變，其獎勵布林函式仍只是必要條件，不能繞過新優先權或作正式發幣授權。

以上 `/share` pending 保存描述正式 service／Repository；已驗收的 Gateway 使用 acceptance-only 服務，明確不保存任何資料。統一驗收矩陣、程式碼缺口與產品決策見 [Phase 2A Closeout](docs/phase2a-closeout.md)。該次收尾僅文件更新與唯讀審查；本次 2B.1 已重跑包含既有 298 項的離線回歸，但沒有重跑真實整合或真人操作。

Phase 2A.1 [驗收與原準備紀錄](docs/phase2a1-repository-acceptance.md) 沿用固定 TEST_DB 安全檢查；Accepted 範圍是全 rollback 的 savepoint SQL 與未提交鎖競爭。正式 Commit／跨提交可見性不在已驗證範圍；真實 13 項結果由使用者人工提供，Codex 本次未連線或重跑。

Phase 2A.2 [人工操作與驗收表](docs/phase2a2-discord-acceptance.md) 的三模式仍停用 DB／Gateway、無 global 選項；Registration 已人工 Passed，現有 Guild 指令不重跑 sync。這些模式與 Phase 2A.3 listener 是不同入口；後者的 User Verified 不等於真實資料庫互動或第二帳號隱私已獨立驗證。

Phase 2A.3 [Gateway 驗收流程](docs/phase2a3-gateway-acceptance.md) 的 DB-disabled transport 已 Accepted — User Verified，使用者確認真實連線前置及五組互動完成；本次未重跑。DB 持久化、發點／扣點、交換、Daily 設定／排程／DM 不在這項驗收範圍；正式 Daily 偏好 SQL 已實作並通過隔離整合，派送／恢復未實作。

Phase 1 採已批准的 YouTube-only 暫時例外：相同 Video ID 去重、不同 ID 為不同項目，保留內部 song_id 與 platform/platform_track_id。後續歌曲作品層級識別須新增平台映射與證據，以版本化遷移保留來源、歷史及 ledger；先評估身分合併造成的額度與唯一约束衝突。此決策不修改 AGENTS.md 長期規範，也不授權自動執行後續 Phase。

Phase 1 的 platform_track_id 為 VARCHAR(11)，搭配 youtube-only 與長度 CHECK、大小寫敏感比較。Phase 5 Spotify 整合前需人工審查 Schema Migration，擴充識別容量與平台約束、建立平台映射，保留既有 song_id、歷史與額度；不會以重跑建表腳本自動遷移。

Phase 1A 定義 submissions 為去重投稿嘗試，不是已接受或已授予額度的紀錄；全域每首歌一次獎勵仍由 ledger 唯一鍵及未來服務交易保障。靜態回歸測試不能取代真實 MySQL 驗證。每日派送與推薦跨表狀態同步留待 Phase 4 實作。


### Batch B 雙帳號 Migration 安全改造（離線，待人工驗收）

Application `TEST_DB_*` 與 Migration `TEST_MIGRATION_DB_*` 已分離，固定本機測試庫及各自帳號；Application 不需要也不得具有 DDL 權限。新增獨立唯讀 Migration Preflight 與預設關閉的執行入口，支援 schema/table grants。最小權限 SQL 與分階段 PowerShell 流程見 [本機驗收流程](docs/phase2b2-batch-b-user-run.md)。

本輪安全離線回歸 532 Passed／0 Failed／0 Skipped；無 MySQL、Discord、YouTube 或實際 Ledger 操作。真實 Migration／InnoDB 並行與原子 Nickel 驗收仍未執行。Phase 2B.1／Batch A／Batch B 均未由本輪標記為人工 Accepted，不開始 Batch C。


最終權限離線覆核：Application 改為逐表最小權限，無 DELETE，jb2_migrations 僅 SELECT；原 schema-level 寫入會被阻擋。詳見 [權限矩陣與操作限制](docs/batch-b-privilege-audit.md)。本輪離線回歸 548 Passed／0 Failed／0 Skipped；真實 MySQL 未驗證，等待人工審核。


### Batch B 真實 MySQL Harness（實作完成，待帳號與測試執行授權）

使用者已回報 Application／Migrator Preflight、Migration 2b2-b1 與 23 表部署通過，Migrator 已鎖回，control 三開關 OFF；本輪保留這些成果，未重新驗證或執行部署。前文「Migration 未執行」屬較早離線階段紀錄，以本段現況為準。

新增獨立 jukebox101_test_harness 的逐表／指定欄位 DBA SQL 草案及 Baseline → Enable → 真實交易 → 精確主鍵清理 → Restore → Verify 架構；Application 權限不變。已實作 23 個預設 skip 的真實 MySQL 案例，本輪 650 Passed／0 Failed／23 Skipped；其中 102 個為新離線安全測試，沒有連線 MySQL。一次性帳號建立／GRANT、真實寫入及隔離庫開關啟用均待另行人工授權，Batch B 尚未整體人工 Accepted，不開始 Batch C。詳見 [Harness 與真實測試操作文件](docs/batch-b-real-mysql-tests.md)。


Harness 最終安全收斂（離線）：保持 submissions 僅 SELECT 與 18 表 DELETE；新增精確 control revision／switch drift 檢查、server UUID pin、未啟用角色與額外 grants 檢查、清理失敗原因保存。DBA 草案改為先建立鎖定 Harness、完整核對後只解鎖 Harness；須受控執行，不能直接忽略 SELECT 結果跑完整批 SQL。本輪 681 Passed／0 Failed／23 Skipped；未連線 MySQL、未執行任何 DBA 或業務 SQL。詳見 [單次授權範圍與安全限制](docs/batch-b-real-mysql-tests.md)。

## Private Beta 規格 checkpoint（2026-10-16）

狀態：**BETA_SPEC_READY_FOR_REVIEW**；僅文件規格，未實作 Beta。見 [最小 Beta 規格](docs/private-beta-mvp-specification.md) 與 [Recovery 成功紀錄](docs/recovery-success-checkpoint.md)。下列或先前各階段測試數量屬歷史，不代表本輪真實 MySQL 驗收。
