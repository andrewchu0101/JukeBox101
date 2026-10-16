# Private Beta 接線與離線驗證紀錄

> 後續進度：Stage-B-only supervisor 已完成離線接線與回歸；本報告下文「尚需離線補接」是當時狀態。最新執行門檻、授權與測試結果以 [實跑 runbook](stage-b-only-run.md) 為準；真實 Stage B／Beta 均未由本輪執行，舊 Session 仍 Blocked。


日期：2026-10-16。狀態：**程式已接線／離線驗證通過／真實啟用待授權**。本輪未連線 MySQL、Discord Gateway 或 YouTube，沒有執行 Recovery、Stage A/B、Migration、DDL、帳號或權限變更，也沒有解除原 Session Block。Recovery 維持使用者已驗收的 RECOVERY_SUCCESS；Batch B 真實測試尚未完成。

## 實際指令路由

沿用 `bot/__main__.py → JukeBoxClient → SafeCommandTree → CommandHandlers → MusicService`；沒有新增 Cog、dispatcher、service、repository 類別或 runtime dependency。

| 指令 | 正式私人入口的接線 | 限制 |
| --- | --- | --- |
| `/share url` | Handler 以 `discord:<interaction.id>` 傳入穩定操作鍵；MusicService → 既有 SubmissionRepository.accept_pending → 既有 accept_submission／submission_transaction | 新影片以 unresolved identity／pending_validation 受理；只接受現行 YouTube parser。既有影片沿用可信目前審核與身分，不重設證據；不呼叫 settle，不發 Nickel |
| `/history` | MusicService → SubmissionRepository.history，以 WHERE discord_user_id=%s 在 SQL 內限制本人；JOIN 目前 review_status，依 accepted_sequence DESC、HISTORY_LIMIT 分頁上限 | 語意依本輪要求改為「本人投稿及目前審核狀態」，不是派送紀錄；無指定他人 ID 的參數。只執行 SELECT，關閉連線時丟棄讀取交易，不呼叫 snapshot、不取 Control 寫入鎖 |
| `/balance` | MusicService → 原 MusicRepository.balance → users.credit_balance SELECT | 只顯示已存餘額，不建立新 Ledger／餘額來源；服務透過 asyncio.to_thread 執行 |
| `/exchange` | 原停用說明 | 不保留推薦、不扣點、不發送 |
| `/daily on/off` | 私人 Beta 路徑只回覆未開放 | 不呼叫舊 set_daily writer、不保存偏好、不發 DM |

原 DB-disabled GatewayAcceptanceService 保持不持久化；只相容新增的可選操作鍵參數。舊 MusicService 的未注入 Batch B 路徑保留作現有離線／驗收相容，正式私人入口明確注入 Batch B，不會回退到舊 submit_pending。

## Guild 與設定

正式入口重用既有 load_development_guild_settings／environment_snowflake／GuildInteractionGuard；沒有新設定框架。必填 `DISCORD_BOT_TOKEN`、`DISCORD_DEV_GUILD_ID`（本輪指定私人 Guild）、`DISCORD_EXPECTED_APPLICATION_ID`、`DISCORD_OWNER_USER_ID` 與原 DB_* 環境變數。缺失或無效 ID 在 login 前失敗；Gateway dispatch 前拒絕 DM、外 Guild 或外 Application。Owner ID 只是既有 Repository 的必要配置，本輪沒有 Owner 審核指令。

`.env.example` 僅新增空白設定欄位；入口讀取 process environment，不自動載入 .env。不記錄 Token／密碼；原最小 intents、ephemeral、AllowedMentions.none 及安全錯誤回覆沿用。無自動 command sync；原本五指令的名稱與參數契約不變，history 的本地描述已更新，遠端描述未同步。

## 回覆與冪等

- 真正收到 Commit acknowledgement 才回覆已儲存與審核狀態；待驗證資料不等於可交換歌曲。
- 相同 Interaction ID 重送復用原回執，不建立第二個事件；相同 key 改使用者或影片會拒絕。不同 Interaction ID 的主動再投稿仍是既有 Batch B 的不同事件，不發獎；不把它謊稱技術重試。
- CommitOutcomeUnknown 不自動重送，也不產生新 key；顯示操作碼，請使用者先 /history 查詢並由 Owner 核對。查不到不代表確定未提交；新的 Slash invocation 會有新 Interaction ID，不能當作舊操作的安全重試。沒有新增使用者可輸入 retry key 的指令。
- PersistenceNotReady 回覆尚未通過啟用檢查；MySQL 失敗回覆資料服務暫時無法使用，不顯示假餘額、raw driver message 或私密輸入。Discord 回覆失敗沿用既有邊界，不自動重做業務操作。
- accept_pending 在同一 Control row lock 內要求 snapshot_ready=1、legacy_writers_quiesced=1、settlement_enabled=0；任一不符即拒絕，不自動切開關。這是公開 pending intake 的隔離前提；不修改既有內部 accept／review／settle 契約。

## Session Block 檢查結果與具體阻礙

1. `tests/test_batch_b_mysql.py::batch_b_write_guard` 是 scope=session 的記憶體字典。setup／測試／cleanup 失敗會設 blocked=True，使同一 pytest session 後續測試在寫入前停止；沒有 unblock CLI、持久化全域開關或可安全 resume 的舊 pytest process。
2. Recovery helper 故意輸出 session_blocked=True，包括成功的 R_FK_RESTORED_VERIFIED_BLOCKED。原 Journal／Recovery 記錄不可改寫為解鎖；本輪沒有修改它們。
3. 新 pytest process 自然產生新的 session guard，**不代表真實執行已獲授權**。仍需固定 127.0.0.1:3306/jukebox101_test、核准 UUID、既有 Application／Harness 帳號、精確 Schema／23 表 Baseline／Control／Registry、權限與獨占檢查、RUN_SWITCH 與 HARNESS_SWITCH 的獨立 opt-in。
4. 具體程式阻礙：MySQLHarness.start → verify_cleanup_readiness → require_cleanup_cascade 在原 NO ACTION self-FK 下拒絕開始，避免再次 cleanup 1451。Recovery 已還原原 FK，因此直接重跑仍不可行。
5. 一般 `tests/batch_b_acceptance.py::execute` 仍先 stage_a；入口只接受 BATCH_B_A_PLUS_B，沒有「重用既有帳號、只跑 Stage B」模式。DBA 在 Stage A 後即丟棄，一般套件尚未接入受控 FK window supervisor。不能以新 process 或刪除 blocked guard 迴避以上保護。本輪未擴大修改 Harness。

後續若要完成 Batch B，需先核准**離線補接既有入口的 Stage-B-only supervised FK window**（不新增平行 launcher），再審查其精確執行方案。真實執行至少需要另行授權：在固定測試庫、使用既有帳號與已驗收空 Baseline，執行已審四步 FK bridge（ADD 暫時 CASCADE、DROP 原 FK、測試後 ADD 原 FK、DROP 暫時 FK），以及 23 個原測試情境所需的受控 INSERT／UPDATE、精確 Run Manifest DELETE、Control 啟用與恢復；最後驗證原 Schema／Registry／Control 與全部 Baseline。沒有帳號重建、grant 或 Migration 授權；不能現在用一般 A＋B 入口替代。仍須先完成新 supervisor 的離線驗證及具體範圍審查，這段不是可執行命令或既有 Recovery 重跑許可。

## 私人 Guild 真正運行的最少後續操作（本輪未執行）

1. 先處理上述 Batch B 真實驗收門檻；原 Session 保持 Blocked，使用另行授權的新測試 session，不覆寫事故紀錄。
2. 明確指定私人 Beta 的 DB 目標與現有 Application 帳號，唯讀核對所需 Schema／Migration／權限；設定 Guild／Application／Owner 與環境憑證。不得把測試庫直接當私人 Beta 資料庫，也不默認需要 DDL／增權。
3. 在確認全部舊 writer 停用、歷史快照覆蓋與隔離條件已成立後，另行授權該明確目標庫的單筆 Control 變更：snapshot_ready 與 legacy_writers_quiesced 由 0→1；settlement_enabled 保持 0，不更改 accepted_sequence／revision／schema_version。不能僅為通過檢查而虛報就緒。當前 0／0 與三開關 OFF 未變更。
4. 另行授權該 Guild 的 Discord REST 契約核對與必要同步、Gateway 啟動，及少量真實 pending 投稿寫入驗收；核對 /history 本人隔離與 /balance 不變。啟動／真人投稿本身就會產生 DB 寫入，因此目前不提供可直接執行的啟動命令。

## 驗證結果

- 最終完整受控離線 pytest：**1163 Passed／0 Failed／55 Skipped**；所有 JUKEBOX_RUN_* 先設為 0，audit hook 阻擋 DNS 及非 Python socketpair 的連線。Skipped 是 opt-in 真實 MySQL 相關案例，不是已通過的真實驗收。1 個既有 discord.py audioop deprecation warning。
- PowerShell launcher 純 Mock：**90 個斷言 Passed**；未啟動 Python child 或 MySQL。
- 涵蓋真實本地 CommandTree 分派、私人 Guild／Application／DM 拒絕、設定缺失 Fail Closed、初始 Pending、原回執重播、不同 actor／video 拒絕、兩執行緒同 key、Commit 前後 acknowledgement loss、既有核准證據保留、SQL 本人限制與上限、讀取關閉、服務錯誤消毒、不進舊 writer／結算。
- Rule One 與 docstring AST 檢查包含在完整 pytest 中，通過；沒有新增套件。
- 初次測試攔截過嚴，誤擋 Windows asyncio 內部 socketpair，造成 107 Passed／111 Failed；修正 audit 例外後，沙箱仍無法完成該內部 socketpair，兩次等待程序已中止。最終經核准於沙箱外執行，保留外部連線阻擋，先通過 218 項針對測試，再完成上述完整回歸。這些失敗屬測試 runner 環境，未連線真實服務。
- 未進行真實 MySQL／Discord／YouTube 手動驗收；不標記 Beta 可用或 Batch B Accepted。沒有 Commit、Push 或部署。

實際程式修改：bot/__main__.py、bot/discord_app.py、bot/services.py、bot/database/submission_repository.py、bot/gateway_listener.py、bot/gateway_service.py。測試修改：tests/test_discord_app.py、tests/test_gateway_acceptance.py、tests/submission_persistence_fake.py，新增 tests/test_private_beta.py。必要文件：README.md、ROADMAP.md、.env.example 與本報告；原規格標註本輪已更新範圍。
