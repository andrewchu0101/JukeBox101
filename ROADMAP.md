> 最新狀態（2026-10-31，Asia/Taipei）：PR #2 已合併 main；hosted PR CI 1881 離線＋35 PostgreSQL 案例通過、零失敗／跳過，夥伴回報 main CI 也通過。Hostinger Bot 的首筆指定投稿已由 Codex 在 Supabase PG17.11 查到；使用者畫面確認補時長後歌曲核准、修訂 2，版本未確認、Nickel 0。CI 已完成，CD 未啟用，自動審核／發獎尚未完成。證據與限制見 [首筆審核紀錄](docs/deploy/supabase-first-review-2026-10-31.md)。下一 MVP 已選「官方 API＋認可頻道，其餘待審」，見 [實作規格](docs/mvp-auto-review-nickel.md)。以下舊階段狀態與 [雲端交接報告](docs/deploy/cloud-cicd-2026-10-30.md) 的未推送／403 為歷史紀錄，不覆蓋本摘要；不將首筆驗證擴大成全部功能驗收。

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
| 2B.0／2B.0A | 唯讀架構與政策研究 | 已完成文字審查；Nickel 獎勵的 Incentivized Engagement 範圍已取得 Google 有範圍回覆（見 [政策範圍確認](docs/youtube-policy-confirmation.md)）；YouTube API 其餘政策仍 UNRESOLVED，不視為官方核准 |
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

## 專用隔離 schema 真人驗收（2026-10-25）

- 結果：在專用隔離 schema `jukebox101_live_20261025` 上完成一次真人驗收流程（合成 C 投稿、Owner `/share` D、時長、核准、版本、指定結算、交換前核對、一次 `/exchange` 與 DM、交換後核對）。帳本共 3 筆、預留 1 筆 delivered、DM 內容對應 C，收尾已正常關閉 3307。
- 證據與細節：`docs/exchange-live-dev-acceptance-plan.md` §21；本機證據位於 `.local/live-run-evidence/20261025-01/`（不提交）。
- 限定：實機使用當時工作樹與本機包裝，未宣稱提交快照已獨立實機驗證；Beta 交換未開放；補償保持關閉。
- 已知缺口：`uncertain` 與無補償表時的舊 `reserved` 沒有解除入口，仍待另行設計。
- 文案待辦：使用者回覆中的內部操作編號後續修正。

## Session 接手入口（2026-10-25；雲端部署準備）

### 現在的結論

核心成功路徑已實作，且在本機專用隔離 schema 完成「投稿 → 時長 → 核准 → basic 版本確認 → 結算 → 交換扣點 → 真人 Discord DM」驗收。下一個工作主線是雲端部署準備；不能標記為所有產品功能皆完成或只剩部署：uncertain／舊 reserved 的解除入口仍缺，Daily Delivery、Spotify 與完整 Beta 範圍也不能因本次成功而標為完成。前面的 Phase 表為歷史階段紀錄；交換的最新狀態以本段與驗收文件 §21 為準，不把本次驗收擴張為整體 Phase 驗收。

### 已確認基準與證據

- 專案：D:\Project\MusicBased\discordBot_JukeBox101；WSL：/mnt/d/Project/MusicBased/discordBot_JukeBox101。
- 分支：feature/owner-review-offline-checkpoint。
- 本次 Codex 唯讀確認 HEAD：92f2fd6，真正索引為空。使用者提供的 Claude 回報：92f2fd6b55cc64d310b0be378d0f127ef6ccbaf2 已正常 push，遠端一致；Codex 本次未獨立查遠端。
- 近期提交：cc55793（核心前置）、b26aa69（N1/N2）、7990f2d（防重入、version／settle）、92f2fd6（驗收入口、預檢、結果文件）。
- 實機證據是當時工作樹與 .local 包裝，不是提交快照獨立重驗。工作樹相關離線 35 項通過來自 Claude 回報；沒有完成全套 MySQL／跨程序驗證。
- 入口：docs/exchange-live-dev-acceptance-plan.md §21；docs/exchange-formal-entry-wiring-plan.md；docs/compensation-mysql-verification-plan.md。
- 不新增 HANDOFF.md；本段是接手入口。此次僅新增交接文字，未提交或 push。

### 真人結果與保留環境（歷史回報，重新操作前須查現況）

- schema jukebox101_live_20261025：22 表，補償三表不存在；C 受理序號 1、D 序號 2，兩者 awarded；一筆交換 delivered、一則真人 DM；帳本三筆 +1／+1／−1；Owner 餘額 0→1→0，合成者餘額 1；未完成派送與退款為 0。
- C：https://youtu.be/cUfDOS2SINM（XG - HYPNOTIZE，189 秒）；D：https://youtu.be/aHTI0SXGVS0（XG - IS THIS LOVE，176 秒）。Owner 已人工確認標題、官方頻道、總時長、官方 MV 與連結；basic 不表示已比對其他音源為同一錄音。
- Owner：456708412697215018；Guild：1559749813011087392；Application：1559751168723521576；合成 ID：9000000000000000001，不作 DM 收件人。
- 本輪正常停止 bot、SHUTDOWN 3307；3306 MySQL80 未碰。settlement_enabled=0。這些是上輪收尾證據，不是持續監控結果。
- .local/live-run-evidence/20261025-01/ 保存本機證據，不提交；舊 jukebox101_test／jukebox101_window_n12 的資料保留。舊庫因前序未釋放／未知身分操作而 waiting_predecessor，不能只用候選池為空判定可結算。n12-op-1 不得自行結算。
- 遠端五指令 balance／daily／exchange／history／share 及額外 owner；保留 owner、不使用，不同步或覆蓋。

### 尚未完成及上線界線

- uncertain 與無補償表的舊 reserved 沒有解除入口；結果未知不解鎖、不退款、不重送。一般開放前必須明確處理或限定風險，不能當作已修復。
- 同一 key 重放沿用原結果；不同 key 在 reserved／sending／uncertain 時回 IN_PROGRESS。這是產品規則；沒有另建 DM 額度系統。
- Beta 交換未開放，補償保持關閉；本次成功不批准開放。
- 使用者回覆末尾內部「操作 1」仍是文案待辦，尚未修改。
- Daily Delivery／Spotify 不是本次成功路徑範圍，不標為完成。YouTube 政策的剩餘適用範圍沿用既有文件，不宣稱 Owner 人工證據是平台認證。

### 下一步：雲端部署依賴唯讀檢查

最終目標是免費雲端主機＋免費資料庫，整合 Discord Developer Portal 與伺服器應用程式／指令權限管理；不是另建 Discord 管理後台。

先從正式啟動入口做一次有限的依賴檢查：正式 bot 是否引用 tests／.local／DPAPI／Windows 路徑、是否寫死 schema／3307／測試 ID、環境與秘密注入方式、migration 與啟停方式、Gateway 常駐需求、單／多實例的 DB 去重與交易保障。區分 committed HEAD 與剩餘未提交工作，不直接將混合工作樹當部署版本。

先交付實際檔案依據、部署阻礙與最小修正建議，不全面重構、不追加廣泛測試。之後才查最新免費平台限制與 MySQL 相容性，確認休眠、背景程序、連線、儲存、期限及配額；平台尚未選定，未保證免費方案能長期運行。正式雲端不能依賴人工 token 輸入或 Windows DPAPI，需雲端秘密設定。Discord 安裝、權限與指令發布須另行具體安排，不默認同步。

### 接手角色、權限與操作限制

- Codex 預設只協調、解讀 Claude 回報、整理決策與撰寫 prompt；繁體中文白話。未明確授權不改程式、不跑測試、不操作 DB 或環境；Claude 回報不是執行授權。
- 前輪 DB 解密、建 schema、GRANT、寫入與真人 DM 授權是已完成的特定驗收，不延伸成下一輪的常設權限。秘密不貼聊天、不寫明文、不放命令列、不讀 .env。DPAPI helper 實際整包解開 root／exchange／migrator，不能宣稱只解單筆；新用途須另行授權。
- 不清空、不 DROP、不重建、不改舊資料、不增權、不解密、不啟動服務，除非新的具體授權。
- 剩餘工作樹包含其他工作與行尾差異，依最後 Claude 回報為修改 84、未追蹤 19；本次不重新盤點。ROADMAP 原有 2B.0 修改也保留。只精準取本輪段落；.local 未被忽略，禁止 git add -A／git add .。
- 不 force push、不 PR、不 merge、不碰 main；hook 失敗或遠端分歧停止，不繞過或自行 rebase。
- Claude prompt 提醒使用可用且適合的 tdd／start-dev-team skill；未用或不可用如實說明，不主動要求建立團隊，不補造紅燈證據。
- 持續推進具體下一步，不重問既定規則、不重開整體驗收。可重用 prompt 用 writing block。

## Session 接手入口更新（2026-10-25；HTTP 候選已離線通過，Oracle A1 待容量）

本段補充並更新前一接手入口；Claude 與 Codex 共用，不新增 HANDOFF.md。使用者準備離線，本次僅授權交接文件整理，不代表可在其離線期間繼續操作環境或部署。

### 已定方向與部署界線

- 選 Oracle Always Free VM＋MySQL HeatWave，以 Discord HTTP Interactions 接收指令；部署後不得依賴使用者電腦、Wi-Fi、本機隧道、DPAPI 或人工輸入 token。
- 先準備到部署前最終檢查，使用者確認後才部署。首次部署 JUKEBOX_FORMAL_EXCHANGE 維持未設定／關閉；不做真人交換或 DM。部署 HTTP 服務與開放交換是兩道不同門檻。
- 保留私人 Guild／Application／Owner 守門與既有指令，不自動同步、不改 global。Owner 暫保留 CLI，Discord 內審核未實作。Daily／Spotify 仍不在本次完成範圍。

### HTTP 候選與驗證（以下來自使用者轉貼的 Claude 回報，Codex 未獨立重跑或程式審閱）

- 分支基準沿用 feature/owner-review-offline-checkpoint；最後已知已提交並 push 的 HEAD 為 92f2fd6。HTTP 變更尚未暫存、提交或 push，不宣稱當前 HEAD 已含 HTTP。
- 新增程式：bot/discord_http.py、bot/http_interactions.py、bot/http_config.py、bot/http_app.py。新增測試：tests/test_discord_http.py、tests/test_http_interactions.py、tests/test_http_config.py。
- 新增部署資料：deploy/RUNBOOK.md、deploy/systemd/jukebox101-http.service.template、deploy/reverse-proxy/Caddyfile.template。
- 候選另修改 pyproject.toml（aiohttp、PyNaCl 直接依賴）、.gitignore（新增 /.local/）、.env.example（DISCORD_PUBLIC_KEY 假值／說明）、dependency-versions.txt（PyNaCl／cffi／pycparser）。既有未提交內容與 CRLF 差異必須保留，禁止整包暫存。
- Windows .venv Python 3.12.10：三檔 44 passed／0 failed／0 skipped。新增 PyNaCl 1.6.2、cffi 2.1.1、pycparser 3.12，未升級其他既有套件。沒有可追溯的 TDD 紅燈證據，不補造。
- WSL Ubuntu 26.04 x86_64、Python 3.12.15：三檔 44 passed／0 failed／0 skipped。專用 venv：/home/gaston/.claude/jobs/4d3e4ead/tmp/venv-http-linux（暫存位置，不保證下輪仍存在）。以 --only-binary=:all: 安裝，全為 wheel，未使用 sudo 或系統套件。
- Linux 直接依賴：discord.py 2.7.1、aiohttp 3.14.5、PyNaCl 1.6.2、PyMySQL 1.2.3、python-dotenv 1.2.4；測試 pytest 8.4.2。Linux 傳遞依賴 multidict 7.2.1／pluggy 1.7.0，與 Windows 的 7.1.0／1.6.0 不同，未改寫 Windows freeze 清單。Windows freeze 不直接當 Linux 安裝清單。
- 隔離假設定子程序：缺 DISCORD_PUBLIC_KEY 或空環境時皆退出碼 1，只輸出缺少設定名稱；於建立服務前停止，未監聽 8080。
- 測試用替身與行程內 TestClient，不連 DB／Discord／Oracle；未驗證真實 Portal、DM、簽章來源或 HeatWave。Oracle A1 是 ARM64，尚未驗證 ARM64 wheel 與測試，不把 x86_64 當作 A1 驗證。
- 最後一輪 Claude 只更新 RUNBOOK，未改程式或重跑全套回歸。詳細準備、啟停及回復以 deploy/RUNBOOK.md 為操作草案，執行前須審閱。

### 中斷與交換缺口（不得誤稱只有 UX 影響）

- 依 Claude 程式閱讀：reserved 保留後未領取可能滯留，沒有本服務內解除入口；sending 過期轉 uncertain 由後續 /exchange 開頭觸發，沒有自動計時器。
- to_thread 等待端取消不保證執行緒內 DB 交易取消或未提交；正常停止時間與程序中斷狀態仍有實機限制。
- 沿用同 key 重放原結果、處理中拒絕不同 key；結果未知不解鎖、不退款、不重送，不另建 DM 額度。不新增 worker、自動推進或持久 token。這些缺口限制交換開放，不自動阻止交換關閉的 HTTP 準備。

### Oracle 使用者操作進度（畫面歷史，非持續現況確認）

- 帳號 home region 選 Tokyo；原建置區域不可更改。使用者已建立 jukebox101-vcn（10.0.0.0/16）、Regional 公開子網 jukebox101-public（10.0.0.0/24）、Internet Gateway jukebox101-igw，並依引導加入預設路由 0.0.0.0/0 → IGW。
- Security List 截圖：stateful TCP 22 限當時使用者對外 IPv4 /32（可能變動，不在此固定保存）；TCP 443 來源 0.0.0.0/0；保留預設兩條 ICMP；出站 All Protocols → 0.0.0.0/0。未開公開 3306 或 8080。80 是否需要依憑證方案確認。
- VM 候選 jukebox101-http-01：A1.Flex 1 OCPU／6 GB、Canonical Ubuntu 24.04 Minimal aarch64、AD-1、on-demand、自動 fault domain；約 46.6 GB 預設開機卷，傳輸加密開啟；公開 IPv4 開啟。只啟用 Compute Instance Monitoring 插件，其他關閉；cloud-init 留空。
- 使用者已走產生 SSH key pair 流程，曾提醒下載公私鑰；接手不得假設檔案仍可用，不讀私鑰、不貼秘密。建立前由使用者確認私鑰保存。
- 建立 VM 失敗：Out of capacity for shape VM.Standard.A1.Flex in availability domain AD-1。尚無成功建立 VM 的證據；不改付費規格、不升級帳號、不自動反覆重試。暫保留 A1，稍後再試。
- Save as stack 流程：名稱 jukebox101-infra、Terraform 1.5.x、無自訂 providers，Review 的 Run apply 未勾。Codex 已指引按 Create 僅保存；尚未收到保存成功畫面，不能宣稱 Stack 已成功建立。Stack 不保留容量，不取代 SSH 私鑰。
- HeatWave 尚無建立證據；HTTPS 名稱／憑證、秘密設定、DB 帳號／migration、ARM64 實機驗證、Portal 端點切換均未完成。

### 恢復後具體下一步

1. 先接續使用者：確認 Stack 是否保存成功與 A1 容量狀態，不重填整套設定、不建立重複 VCN／子網／IGW。套用 Stack 前核對它引用既有網路及只建立預期資源；不得盲目 Apply。
2. 程式線：對精準 HTTP 候選做一次部署前程式／差異審閱，確認實際安裝方式與已知限制；不再無故擴大全套測試。需要提交時先取得具體授權，禁止 git add -A／git add .。
3. 有 A1 後，另行授權 ARM64 隔離安裝／原三檔驗證；雲端資源、秘密、DB 建置／權限、Portal 分階段明確授權，非每條命令重問，偏離費用／目標／權限即停止。
4. 完成部署前最終檢查，使用者確認後才部署；雲端連線與 Portal 真實確認在資源就緒後安排，不用假證據取代。首次不開交換、不發 DM、不重跑歷史真人驗收。

### 接手角色與離線授權

- Codex 預設僅協調、解讀回報、整理決策、撰寫繁體中文白話 prompt。此次交接文件整理不授權改碼、測試、安裝或外部操作。
- Claude 前輪安裝／測試授權為已完成的限定工作，不延伸為離線持續執行權限。使用者恢復前不自動部署、建立資源、Apply、連線 DB／Discord、同步指令或發 DM。
- 不讀 .env／秘密／.local，不解密、不增權、不清理重建資料；不提交／push，除非另行明確授權。不 force push、不 PR、不 merge、不碰 main；hook 失敗或遠端分歧停止，不繞過、不自行 rebase。
- Claude prompt 提醒使用可用且適合的 tdd／start-dev-team skill；不可用或未使用如實說明，不主動要求建立團隊。先前 Auto mode classifier 無 verdict 曾造成工具中斷，後來已恢復；不要當成目前持續故障。
