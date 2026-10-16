# Discord Private Beta 最小規格

歷史規格狀態：**BETA_SPEC_READY_FOR_REVIEW**。2026-10-16；本輪僅唯讀程式盤點與文件更新，沒有 Beta 實作或外部操作。Recovery 結果見 [成功紀錄](recovery-success-checkpoint.md)；Session 仍 Blocked，Batch B 真實 MySQL 驗收尚未完成，不能啟動或稱正式可用。

> 後續使用者已核准本輪功能接線：`/history` 改作本人投稿／審核狀態，不新增 `/submission_status` 或 Owner 審核命令。現行實作與驗證以 [接線紀錄](private-beta-implementation.md) 為準；下文保留原規格提案。

## 既有路由與 Cog

專案沒有 Cog、add_cog、load_extension 或 extension setup；因此不新增 Cog，也不設重複 Dispatcher／Service／Repository。

- 正式入口 `bot/__main__.py`：JukeBoxClient → SafeCommandTree → CommandHandlers（`bot/discord_app.py`）→ MusicService（`bot/services.py`）→ MusicRepository（`bot/database/repository.py`）。服務以 asyncio.to_thread 執行同步 Repository；連線在 worker 內建立／使用／關閉。入口會登入 Gateway，但沒有自動同步指令或排程。
- 開發 Gateway：`bot/gateway_acceptance.py` → `bot/gateway_listener.py` → 同一 JukeBoxClient／CommandHandlers → GatewayAcceptanceService；GuildInteractionGuard 檢查 Guild／Application，資料庫停用。這是 transport 驗收路徑，不能改稱持久化 Beta。
- `bot/discord_acceptance.py`／`bot/guild_acceptance.py`：既有離線 preflight 與明確授權 REST／Guild sync；不是 Beta dispatcher。`bot/command_contract.py` 與 development_command_contract.json 保存五指令契約，新增指令須同步檢查契約，不能直接覆蓋遠端指令。
- Batch B 的 SubmissionRepository.accept／snapshot／review／settle 已有持久化程式，重用 submission_workflow、review、reward_candidates 與交易 helper；正式 MusicService 尚未呼叫它。純規則、Fake／Mock 通過不等於 Discord＋MySQL 已接通。

## 指令盤點與最小集合

「可直接使用」僅表示現有 handler／service 已接線；不是本輪授權啟動，也不證明當前遠端註冊狀態。

| 指令／操作 | 現況與 Beta 取捨 | 權限 | 資料依賴 |
| --- | --- | --- | --- |
| `/share url` | 已註冊；現行僅 YouTube parser → submit_pending，保存待驗證，不發點。建議保留；需改接既有 Batch B accept，不能同時走舊 writer | 一般使用者，身分取 interaction.user.id | MySQL 寫入；受理不需結算／外部 API |
| `/balance` | 已註冊並讀 users.credit_balance；建議直接重用，回報既有交換點數／Nickel 帳務，不新增另一套餘額 | 一般使用者，僅本人 | MySQL 查詢；不呼叫 settle 或 API |
| `/history` | 已註冊；查本人成功 recommendations，不是投稿或 Owner 審核歷史。可沿用查詢；無派送時可能為空，非必要 Beta 主流程 | 一般使用者，僅本人 | MySQL 查詢；不結算、不用 API |
| `/exchange` | 已註冊，但僅回覆未開放、未扣點；保留停用說明，真實交換暫不納入 | 一般使用者 | 目前停用回覆無 DB／結算／API |
| `/daily mode:on/off` | 已註冊並寫偏好，沒有排程或 DM；暫不納入 Beta 主流程，保留明確未啟用說明，不宣稱已開始每日推薦 | 一般使用者，僅本人 | 現行偏好保存需 MySQL；無結算／API |
| `/submission_status operation_id`（提案名稱） | 尚未註冊；建議的必要投稿查詢入口，重用 snapshot 中的事件／審核／候選投影，不能用 /history 取代。不可顯示他人快照 | 一般使用者僅限本人；Owner 可查審核標的 | MySQL；無結算／API；snapshot 會進入既有交易與 control lock，不能當無鎖唯讀 API |
| `/review operation_id decision expected_revision reason`（提案名稱） | 尚未註冊；最小 Owner 審核入口，將可信 actor 與既有 ReviewCommand 交給 SubmissionRepository.review，不複製審核規則 | 僅設定的 Owner Discord user ID，不能用暱稱／角色／使用者輸入的 actor 冒充 | MySQL；不自動 settle。核准仍需可信完整影片時長、歌曲資格及身分證據，不能靠 URL 或標題猜測 |
| merge／exclude／pending_reminders | Repository 已有內部方法；沒有 Slash Commands。維護、排程摘要與 DM 暫不納入最小 Beta | 既有內部 Owner 檢查 | MySQL；摘要保留 UTC 日界線，並非 Discord 發送 |
| settle／Grant／退款／自動獎勵 | settle 有內部實作，沒有公開命令；暫不接 Discord，不開 Nickel 自動獎勵 | 非公開使用者指令 | MySQL 原子 Ledger／Claim／餘額結算；政策與真實驗收仍是門檻 |
| Spotify 投稿／解析 | 現行 Discord share 僅支援 YouTube；Spotify 解析／可靠 YouTube 對應尚缺，不列首輪 Beta | 暫不提供 | 未授權 API 不可加入；未解決不發幣、不進交換池 |
| play／pause／skip／queue | 未找到 voice client、播放、暫停、跳過或佇列實作；非必要 MVP | 不適用 | 不新增播放依賴 |

建議主流程僅為 `/share`、`/submission_status`、`/balance` 加 Owner `/review`；現有 `/history` 可保留。五個既有指令契約先保留，exchange／daily 不擴成功能。兩個新名稱只是待審提案，本輪未註冊；若沒有可信審核證據來源，首輪僅接受 pending 投稿與查詢，Owner 核准仍封鎖。

## 尚缺必要接線與啟用條件

1. Recovery 已成功，原 Session 仍 Blocked；另行決定測試授權與 Block 處置，不能由規格或 Commit 自動解除。Batch B 真實 InnoDB、並行、故障與人工驗收完成前不啟用持久化 Beta。
2. 在既有入口注入 SubmissionRepository，於 MusicService 增補最小協調；保留 MusicRepository 的餘額／派送歷史查詢。舊 submit_pending 與新 accept 不能雙寫；不得為了接線繞過 snapshot／legacy writer 隔離開關。
3. share 需可信受理 operation_id（技術重試保持同一 key）、未解析身分／初始 review 建構、原回執處理及 CommitOutcomeUnknown 的安全回覆；不讓使用者指定受理序號、已核准狀態或 READY。未知身分保持 unresolved。
4. Owner 設定尚未進正式入口；review 需 interaction.user.id、decision_id、expected_revision、必要 reason，重用 require_owner 與既有硬性門檻。可信時長／身分證據來源需先決定，未取得即 pending，不偽造 metadata、不新增 API。
5. 查詢只投影呼叫者可見結果，Owner 才可看待審標的；snapshot 不直接整份回傳 Discord。保留 ephemeral、最小 intents、無 mentions、消毒錯誤訊息；新增 PersistenceNotReady／CommitOutcomeUnknown／權限與過期 revision 的服務邊界處理，不散布重複 try/except。
6. 重用 GuildInteractionGuard 的指定 Guild／Application 邊界；正式入口目前未注入它。Private Beta 的參與者名單／Owner、測試 Guild 與 DB 目標需明確設定。遠端指令 read／sync／Gateway 要獨立授權，本輪不執行。
7. Control 目前三開關 OFF；任何未來隔離／快照啟用需另審必要前提與範圍，不能直接全開。Nickel 自動獎勵保持 OFF；Recovery 成功不是結算或 API 政策已核准。

## 後續最小修改順序（待核准）

1. 先完成另行授權的 Batch B 真實驗收方案與結果審查；不重跑 Stage A、不重做 Recovery、不由本文件授權 SQL。
2. 先核准「僅 pending 投稿／查詢、不自動獎勵」範圍與可信證據來源。優先僅修改既有 `bot/__main__.py`、`bot/config.py`、`bot/services.py`、`bot/discord_app.py`，按實際需求補既有 `bot/gateway_listener.py` 的可重用接線；不新增 Cog、dispatcher、平行 repository 或 runtime dependency。
3. 在同一 CommandHandlers 接入兩個待核准指令；同步既有 development_command_contract.json 與 guild_acceptance.py 五指令檢查。只在既有 Repository 無法安全表達授權查詢時，補最小投影方法；不重構整份 snapshot。
4. 補既有離線測試：路由、本人隔離、非 Owner 拒絕、stale revision、冪等 key、未知 commit、開關關閉、未結算與 DB-disabled 路徑。維持每函式實作 ≤25 行、巢狀 ≤3、複雜度目標 ≤10；重用現有 helper，不安裝檢查依賴。
5. 通過離線與真實 DB 門檻後，再另行授權指定 Guild 的同步與持久化 Discord 人工驗收；播放、派送、Daily、Spotify、API、自動獎勵均另階段決定。

## 本輪驗證

唯讀核對來源路由、git diff 與待提交文件；沒有修改 Python／SQL／契約／設定。未執行 pytest：通過 0、失敗 0、跳過 0（未啟動測試，不沿用歷史通過數）；未進行 MySQL／Discord／YouTube 人工測試。文件檢查與 Git checkpoint 結果以本輪提交及回報為準。
