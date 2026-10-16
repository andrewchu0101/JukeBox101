# Phase 2A 驗收收尾與後續盤點

本次只更新文件與唯讀審查程式，不修改 Python、測試或 Schema，不讀 Token／`.env`，不連線 Discord／Gateway／MySQL，不同步指令，不執行 Git Commit／Push／Merge／Tag。Phase 2B **未開始、未獲本次實作授權**。

下列結果依使用者本次確認與既有驗收紀錄整理，本次沒有重跑測試或獨立觀察真人 Discord 操作。既有 **298 Passed、0 Failed、0 Skipped** 是離線歷史結果，不是本次新產生的測試結果。

## 最新驗收矩陣與證據界線

| 範圍 | 狀態 | 已有證據 | 不代表 |
|---|---|---|---|
| Phase 1A～1D | Accepted | 既有使用者人工驗收 | 後續業務已完成 |
| Phase 2A.1 Repository Integration | Accepted | 13 Passed／0 Failed／0 Skipped；隔離庫六表前後皆 0 | 正式 Commit／跨提交競態及真人 Discord + MySQL 已驗證 |
| Phase 2A.2 Registration | Passed（人工） | Offline Preflight、REST Login、Development Guild Sync、五指令顯示皆 Passed | Global Sync、正式部署、完整產品流程已驗證 |
| Phase 2A.3 Offline | Passed（歷史） | 298 Passed／0 Failed／0 Skipped；人工 Offline Preflight Passed | 本次重跑或所有真人異常案例通過 |
| Phase 2A.3 身分／契約／連線 | Passed／Matched（使用者回報） | Application／Guild 核對 Passed、Remote Command Contract Matched、Gateway Ready Passed | 遠端永不變動、所有權限／localization metadata 完全相同 |
| Phase 2A.3 DB-disabled Interaction Transport | **Accepted — User Verified** | 使用者確認五組指令真人互動完成 | 逐字回覆、第二帳號 ephemeral 可見性、非法輸入／其他 Guild／斷線／取消等情境已逐項獨立驗證 |
| 真實 Discord + MySQL 持久化互動 | **Pending／未完成端到端驗收** | Repository 與 transport 各有分開證據 | 將兩組驗收相加就能聲稱端到端通過 |
| 歌曲資格驗證、發點、交換交易、Daily 派送 | **未實作** | 現有 schema／stub／偏好設定只是基礎 | 具有欄位或指令名稱即表示業務完成 |
| Phase 2A 整體 | **部分範圍已驗收；不標記全面完成** | 上述分項狀態 | 全部 MVP 或 Phase 2B 已完成 |

User Verified 只表示接受使用者對 DB-disabled transport 的整體操作確認。沒有收到五組各自的實際回覆紀錄、第二帳號隱私檢查或異常測試清單，因此不補造截圖、逐項 Passed 或獨立證據。原測試表保留為「預期／待補證據」，不自動逐列改成 Passed。程式設計與 mock 對 ephemeral／安全拒絕的驗證仍有效，但與真人獨立觀察分開記錄。

## 程式碼唯讀盤點

| 項目 | 現有程式 | 缺口／風險 |
|---|---|---|
| URL 與歌曲資格 | `bot/urls.py` 嚴格 YouTube 主機／11 字元 ID／追蹤參數正規化，沒有外部查詢 | 不證明影片存在、可播放、是歌曲、符合資格或是真正同一錄音；沒有 metadata／驗證來源／審核工作流 |
| canonical identity | songs 有內部 song_id、youtube-only platform／ID 大小寫敏感唯一鍵 | 現階段依已批准暫時例外採 Video ID 去重；不同上傳不能據此推論不同作品。Spotify／跨平台 mapping 仍延後，不在本次遷移 |
| pending 投稿 | `MusicService.share` → `MusicRepository.submit_pending`；user upsert、song no-op upsert、song FOR UPDATE、投稿去重在同一 transaction | 沒有驗證角色、verified／rejected 審核 API、重審、接受時間或獎勵流程。submissions 是第一次去重嘗試，不是每次請求／驗證事件日誌 |
| 全域首次有效投稿發點 | ledger 已有 awarded_song_id 唯一鍵、idempotency key、submission_award reason／+1 CHECK | Repository／service 沒有獎勵 INSERT 或 balance 增加；unique key 只提供防線，不能選擇獲獎人或保證 balance = ledger 總和 |
| 投稿交易與重複 | 目前 pending 寫入由 users 主鍵、song 唯一鍵／列鎖、user-song 唯一鍵保護；既有驗證／來源不被覆寫 | Accepted 整合採 savepoint 注入、外層 rollback；跨連線成功 Commit 後的 duplicate 判定、死鎖／timeout 重試及提交結果不確定仍未完整驗收。不能將「目前完全不發點」稱為已驗證防重發點交易 |
| Exchange | `MusicService.exchange` 只回覆尚未開放，不呼叫 Repository | 無候選隨機選取、排除本人／所有既有推薦、額度檢查與鎖、reservation／ledger／balance 原子交易、退款或交付程式 |
| balance／history | 正式 service／Repository 有餘額與本人 delivered 歷史查詢、穩定排序、10 筆與 1900 字元顯示限制 | 隔離 SQL 與離線 service 測試不等於真實 Discord 持久化互動；Gateway acceptance service 刻意不讀 DB，不回假的零／空歷史 |
| Daily 偏好 | 正式 `set_daily` 在 transaction 中 upsert user 並 UPDATE 明確布林值；整合測試驗證冪等與既有 balance 保留 | DB-disabled Gateway 只說未保存，真實 Discord 設定持久化／重啟後查詢尚未端到端驗收。偏好儲存不等於派送 |
| Daily 派送／恢復 | schema 有 user/date 唯一鍵、推薦所有權 FK、pending／sending／delivered／failed／uncertain 等形狀約束 | 沒有 scheduler、每日預約交易、重啟恢復、失敗退款／uncertain reconciliation。CHECK 不驗證跨表狀態同步或合法狀態轉移 |
| Gateway 與 Repository | `gateway_listener` 注入 `GatewayAcceptanceService`，有 Guild／Application guard、唯讀 contract 核對；正式服務用 asyncio.to_thread 執行整個 Repository 工作單元 | 沒有已驗收的「具相同安全邊界且啟用隔離 DB」Gateway 路徑；不能直接替換 service 就繼承端到端驗收 |
| 正式啟動差異 | `bot/__main__.py` 使用 DB_*、MusicRepository、MusicService，JukeBoxClient 預設 interaction_guard=None | 它不是已驗收的 DB-disabled listener：正式目標／最小權限／Guild admission／跨 server global 規則需重新審查，不能拿該入口直接跑真人寫入驗收 |
| 啟動／關閉與可觀測性 | 驗收入口有 opt-in、async context close、reconnect=False；正式連線有 I/O timeout、短交易、rollback／close | cancellation 不會自動取消已在 to_thread 執行的 DB 工作；需驗證停機時在途交易、可能已 commit 而 Discord 回覆失敗、重試及安全稽核，不假定 UI 失敗等於沒有寫入 |

目前沒有發現需要立即更改 Schema 的證據。若批准的資格、獎勵或重試規則需要保存更多時間／事件／驗證者資訊，先做 schema gap analysis，再審查版本化 migration；本次不新增欄位，也不重新執行建表腳本。

## 必須先界定的交易與恢復契約

- **授點交易**：鎖定需要的使用者／歌曲狀態、驗證資格及獎勵歸屬、寫唯一 ledger、更新 balance，必須是一個短交易；任何一步失敗都不能留下半個獎勵。需要具體鎖順序、重試／idempotency key 與 audit 證據，尚未實作。
- **交換交易**：有可用額度且存在他人提交、本人未提交及未取得的 eligible YouTube target 時，才同交易保留推薦、寫 exchange_debit 及減少 balance。無候選或不足額不扣點，並行／每日 job 不能重複推薦或超支；目前全未實作。
- **交付與退款**：DB 與 Discord 不共用原子交易。需要 reserved → sending → delivered／failed／uncertain，保留發送證據與歷史。明確未被 Discord 接受才可按批准規則退款；timeout／重啟／成功送出但 DB 記錄失敗不得盲目重送或退款，不能承諾 exactly-once。
- **每日冪等**：沿用明確 Asia/Taipei calendar day 的既有設計，一人一天最多一次 automatic delivery；與手動交換共享額度／歷史／並發保護。排程時間、cutoff、重啟補跑、改偏好與在途派送的關係仍需決策與實作。
- **端到端資料生命週期**：先批准固定隔離庫／帳號、真人測試使用者與 run markers、seed、正式 Commit、精確清理及異常停止政策。外層 rollback 測試不能用來包住跨 worker 的真人 Discord 流程；不得以清空測試表代替審查。

## 需要使用者決策（本次不代為批准）

1. **Phase 2B 的具體範圍與驗收條件**：先做歌曲資格／審核、授點、還是 DB-enabled transport 的哪個最小切片？以下所有缺口不是自動授權的下一階段待辦；Exchange／Daily 等原路線圖階段不因此提前。
2. **歌曲資格政策與證據來源**：誰可以核准／拒絕？接受哪些歌曲／版本與可播放性證據？是否使用人工審核；若要外部 API／credentials，需明確批准，不抓取或虛構 metadata。
3. **首次有效提交歸屬**：多人先後留下 pending 嘗試、之後同一歌曲被核准時，如何判定第一個符合資格的提交與獲獎人？驗證後由誰／何時觸發授點？不能默認最早 raw pending 嘗試就一定符合「首次有效」規則。全域每首歌最多一次獎勵的既定原則不改變。
4. **拒絕／重審與已有狀態**：rejected 能否修正證據再審？同 user-song 重試目前不新增 submission，如何留下核准、拒絕、重試與管理者行為的 audit？先前已發點歌曲若被撤銷資格，如何處理已花點數及推薦，不能默默回收造成負餘額。
5. **身份與 MVP 去重界線**：是否繼續既有 YouTube Video ID 暫時例外？不同上傳、live／remix／cover 的政策如何向使用者說明？跨平台仍留待後續明確批准。
6. **交付渠道與 uncertain 處理**：手動交換走 ephemeral、頻道或 DM？blocked DM／已退款後是否允許重試？誰處理 uncertain、需要什麼證據、何時可退款或重送？不清除歷史來製造可選歌曲。
7. **Daily 產品行為**：在既有明確時區／每日最多一次原則下，選定發送時間、錯過時段／重啟補跑、無額度／無候選的回覆策略，以及 off 與在途送出的關係。
8. **DB-enabled 真人驗收及證據標準**：同意哪個專用環境、seed／Commit／限縮清理方案？是否補充逐指令實際回覆、第二帳號 ephemeral 可見性、其他 Guild／DM 拒絕與斷線／取消等真人證據？補證據不等於本次必須重新執行已接受的 transport。

## 文件與程式是否一致

發現的是**狀態更新落差與範圍容易混淆**，沒有在唯讀審查中發現「程式已實作發點／交換，而文件漏記」的情況：

- README／ROADMAP／Phase 2A.3 文件仍以 Gateway Manual Acceptance Pending 為最新狀態，已更新成限定 DB-disabled transport 的 Accepted — User Verified。
- 舊文件仍說 remote contract 未核對，現在補記使用者確認 Matched 的歷史結果；不改寫離線 preflight 的 `not_checked_offline` 真實程式行為。
- 舊真人表的每一列都是預期，不可從整體 User Verified 推導逐列獨立 Passed；第二帳號隱私與異常情境明確保留未取得證據。
- README 結構樹漏列已存在的 acceptance 模組，已補齊。
- README／既有報告中重複「最新／Pending」的舊段落改以歷史標示，最新範圍以本文件及首頁矩陣為準；原規格檔與測試歷史不倒改成新的執行證據。
- Repository 的 Daily 偏好已實作，不能籠統寫成「Daily 全部未實作」；每日排程／交付／恢復才是未實作。Gateway 目前不保存 Daily 設定，兩種服務不混淆。

本次只做 Markdown 連結／狀態與檔案一致性靜態核對；沒有重跑 298 項離線測試、13 項真實整合或任何真人操作，也沒有把未執行項目記作新 Passed。

## 文件清單與停止點

新增本文件；修改 README、ROADMAP、Phase 2A Foundation／2A.2／2A.3 驗收文件。Phase 2A.1 頂部已有 Accepted 與 rollback-only 範圍，本次核對後不改動。原始 specification、Python、測試、Schema、依賴與 `.env` 均未修改。

Git Status 仍為既有全案 `??` 未追蹤：`.env.example`、`.gitignore`、`AGENTS.md`、`README.md`、`ROADMAP.md`、`bot/`、`dependency-versions.txt`、`docs/`、`pyproject.toml`、`tests/`。未 Commit／Push／Merge／Tag。

收尾僅記錄分項驗收及後續缺口，不授權新執行、部署、資料寫入或 Phase 2B 開發；完成後停止等待下一步。
