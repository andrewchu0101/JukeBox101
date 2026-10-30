# Phase 2B.2 Batch B — MySQL Persistence & Atomic Nickel Ledger

狀態：**Implemented／Offline Tested／Real MySQL Integration Pending／Manual Acceptance Pending**。本文件記錄 2026-10-15 的程式與 Fake／Mock 結果，不是資料庫驗收。Phase 2B.1、Batch A、Batch B 及 Phase 2B.2 整體均未由本次標記 User Accepted；Batch C 未開始。

後續改採使用者本機 PowerShell 執行真實測試，Codex 不需取得資料庫密碼。第一輪唯讀入口、可直接執行命令及停止條件見 [使用者執行流程](phase2b2-batch-b-user-run.md)。新增安全 Preflight 的離線回歸為 514 Passed；下方 492 為原 Batch B 結果。真實 Migration／並行／結算套件尚未完成，待第一輪實際盤點回報後補齊，不能直接把 Fake 測試當成真實整合。

## 邊界與公開介面

新增同步 `SubmissionRepository(connection_factory, owner_discord_user_id)`。建構不連線、不讀環境或 .env；每次方法呼叫才由注入 factory 在該 worker 建立／使用／關閉單一連線。未接入既有 MusicService、Discord handlers 或 Bot 啟動路徑。未來 async 呼叫端必須以既有 asyncio.to_thread 模式包住**完整 repository 方法**，不可跨 worker 共用 connection。

| 方法 | 持久化行為 |
|---|---|
| accept(request) | 用資料庫控制列配置受理序號，保存新事件、目前影片狀態、初始／驗證審核歷史、證據及原回執；本方法不發 Nickel。 |
| snapshot() | 在同一控制鎖下讀全球完整集合、獨立清單、目前 review、原 Claim、未知歷史與排除證據，回傳 Batch A Snapshot。無 caller complete 參數。 |
| review(command) | 進交易前核對 configured Owner ID；執行既有 Hybrid Review 轉換與 revision 檢查，保存每個中間 revision、理由、actor、證據、原決策結果及衝突凍結。 |
| settle(operation_id) | 重新評估最新完整狀態；READY 且政策開關允許才原子寫入 +1 Ledger、唯一 Claim、餘額、候選狀態與結算回執。 |
| merge(...) | Owner 證據確認後更新 flattened canonical root，保存原始 Claim／Ledger，新增永久 barrier 與去重的歷史重複獎勵例外。 |
| exclude(...) | Owner 稽核過的特定版本排除證據；不能因此把未知影片視為新版本。 |
| pending_reminders(owner_id) | 依 DB UTC 時間保留每日 Owner 待審摘要批次；沒有發送 Discord 或宣稱已送達。 |

既有 URL、eligibility、review、version identity、reward evaluator、share replies 與 Batch A workflow 直接重用，沒有重寫資格／優先權規則。新增 JSON codec 只使用型別 allowlist、現有 constructor 與標準庫，沒有 pickle、動態 import、ORM 或新套件。

接收新影片一律由 ReviewSnapshot revision 0 起步，不接受 caller 自行提供 APPROVED。accept 可保存可信完整影片時長驗證，核准必須走後續 audited review。新操作分享已存在影片時沿用 DB current review，不因新分享重新驗證終態；新證據須走 review。AUTO 與 OWNER 都使用既有硬性資格；目前持久化 review 邊界要求 configured Owner 授權，不開放任意 AUTO worker 或 Discord 使用者送入核准資料。

## 新增與修改檔案

新增：
- bot/database/submission_repository.py
- bot/database/submission_transaction.py
- bot/database/submission_store.py
- bot/database/persisted_snapshot.py
- bot/database/submission_codec.py
- bot/database/reward_settlement.py
- bot/database/review_persistence.py
- bot/database/submission_maintenance.py
- bot/database/reward_history.py
- bot/database/submission_migration.py
- bot/database/migrations/002_submission_economy.sql
- tests/submission_persistence_fake.py
- tests/test_submission_persistence.py
- tests/test_submission_migration.py
- docs/phase2b2-batch-b-acceptance.md

修改：README.md、ROADMAP.md、docs/phase2b2-batch-a-acceptance.md 的最新狀態交叉連結。既有六表 schema.sql、MusicRepository、MusicService、Parser、Phase 2B.1／Batch A Python 核心與 Discord handlers 不變。

## Migration 與資料模型

原始六表不 ALTER、不 DROP、不 TRUNCATE、不清空、不刪舊投稿、不改寫歷史 Ledger 或餘額。新增檔案包含 16 個 jb2_* 表；runner 另建立 jb2_migrations，合計 17 個新表：

| 表 | 責任 |
|---|---|
| jb2_migrations | 版本、確切腳本 checksum、applying／applied 狀態。 |
| jb2_control | 單例排序／寫入鎖、成功受理水位、read revision、快照及政策開關。 |
| jb2_versions | 明確 recording identity、可信證據 reference、flattened canonical root 與 self FK。 |
| jb2_videos | 影片對原 songs FK、目前版本映射與版本化 review 投影。 |
| jb2_legacy | 舊目錄成員及按需確認身分；沒有虛構受理順序。 |
| jb2_events | 每個主動操作一個事件；operation PK、accepted_sequence UNIQUE、user／video FK、狀態與 conflict freeze。 |
| jb2_receipts | 不可改寫的原始 submission outcome。 |
| jb2_claims | 原始獲獎版本 PK、operation UNIQUE、ledger UNIQUE、相關 FK。 |
| jb2_settlements | 成功結算的 durable idempotency result。 |
| jb2_review_history | (video, revision) PK；每個合法 revision 的原始狀態與稽核。 |
| jb2_evidence | Append-only 原始身分／影片資格證據、actor 與理由。 |
| jb2_decisions | Owner 決策 key、完整 intent digest、actor、理由與原結果。 |
| jb2_exclusions | 特定版本排除證據；保留 proof reference 與 Owner。 |
| jb2_barriers | 歷史合併後永久關閉的首次獎勵資格。 |
| jb2_exceptions | 原 Ledger 群組、actor／理由與唯一 audit_key；重複掃描不重複建例外。 |
| jb2_reminders | 原始 DB 受理時間 +24h 的 next_due_at、最後摘要日期。 |
| jb2_reminder_batches | 每個 UTC 日期一份不可變原摘要、原 Owner target。 |

Migration 不對歷史 songs 建新事件或 Claim。bootstrap 只 INSERT 舊 song IDs 至 jb2_legacy，未知身分保持 NULL／UNRESOLVED；不從 created_at 或既有 AUTO_INCREMENT 推測公平順序。

### 版本保護與 DDL 中斷復原

本次沒有呼叫任何真實 migration runner。未來仍需另外授權：
1. 停用舊 Bot／job／投稿 writer，確認隔離庫及備份；原六表基線必須含筆數、餘額合計、Ledger 筆數／總額／原 ID 與必要 FK／唯一鍵。
2. 只從 TEST_DB_* 取得測試設定，沿用 Phase 1 固定 127.0.0.1:3306、jukebox101_test、jukebox101_test_app 與 CURRENT_USER() 完整核對；不得回退 DB_* 或載入 .env。既有安全檢查在 tests/mysql_safety.py。本次未建立會自行連線的新測試入口。
3. 現有測試帳號可能沒有 CREATE／REFERENCES 等權限；如需測試 Migration，DBA 必須另行批准暫時且只限測試庫的最小 DDL 權限，驗證後撤回。本程式不 GRANT、不改帳號、不擴權。
4. apply_migration 的 deliberate token 不是人類授權。它先查實際 DATABASE()／CURRENT_USER()，只允許 jukebox101_test／jukebox101_test_app@127.0.0.1；沒有 production runner，也沒有 startup auto migration。
5. GET_LOCK 有 5 秒取得期限。runner 先讀版本 checksum；相同 applied 版本只做 metadata 檢查並返回 False。checksum 不同、applying 中斷標記或無版本的既存 jb2_* 表一律停止。
6. **MySQL DDL 隱式 Commit，不能靠一般 Rollback 取消。** applying 先單獨 Commit，再逐項 CREATE。DDL 部分成功後失敗，保留 applying；不得直接重跑、刪表或手動把 marker 改成 applied。
7. DBA 先逐表核對 SHOW CREATE TABLE、information_schema 的欄位／collation／engine／FK／unique／CHECK、腳本 checksum 與已存在資料。依另行核准的修復計畫向前完成剩餘部分，或從已確認備份恢復隔離庫。**本批次不提供自動破壞性 down migration。**
8. DDL 完成後，bootstrap、控制列與 applied marker 在普通資料交易中一起 Commit。snapshot_ready、legacy_writers_quiesced、settlement_enabled 均預設 FALSE，Migration 不啟用發幣。
9. runner 自動 metadata 驗證目前涵蓋所有新表存在及 InnoDB；**不等於完整的 column／FK／unique drift 證明**，上述人工／下一階段資訊綱要核對仍是必要啟用條件。套用過的腳本不可原地修改；後續變更需新版本。
10. Migration Commit 回覆不明時，先以新連線查版本狀態與內容，再決定處置；不得把部分 DDL 當成已全部回滾。

## 受理順序與交易邊界

每個新 Repository writer 都依序：
- 設定 UTC、STRICT_ALL_TABLES／NO_ENGINE_SUBSTITUTION、innodb_lock_wait_timeout=2，READ COMMITTED，BEGIN。嚴格 SQL mode 避免過長鍵或整數溢位被靜默截斷；這些僅是連線 session 設定，不改全域 DB 設定。
- SELECT jb2_control singleton FOR UPDATE，持有至 Commit／Rollback；schema marker 不符即停止。
- 已存在 operation 則重播原回執；新 operation 才將 accepted_sequence 加一，並保存事件與回執在同一交易。
- 關閉 cursor、Commit、關閉 connection。遇例外或中斷嘗試 Rollback，finally 關閉；cleanup 的次要失敗只加註原 exception，不覆蓋主要結果。

A 持有控制鎖時，B 無法取得下一個序號；A 成功 Commit 後 B 才能見到其水位。A Rollback 則計數器及其事件一起消失；後續成功 B 可使用下一序號。這不是用 AUTO_INCREMENT 或指令時間冒充 Commit 順序。MySQL 的實際等待／釋鎖／崩潰時序尚未真實驗證。

Commit 失去回覆會回報 CommitOutcomeUnknown；同 key 以新連線重試。實際已 Commit 就讀原 receipt；未 Commit 才重新受理。若 B 在 A 未成功受理後先完成，B 自然先於 A 的新成功重試。不能改 operation key 來「解決」不確定結果。

鎖定順序是 global control → song/video／version projection → recipient user → claim／ledger 相關寫入；所有新寫入路徑都先取得 global control。舊 MusicRepository 沒有這把鎖，**正式啟用前必須停止或隔離舊投稿 writer，不能只手動把 legacy_writers_quiesced 設成 True 就假設舊 writer 已停止**。應由部署路由及必要的帳號／權限隔離落實。

全域序列化與完整快照是初期正確性優先方案，不是已通過大型目錄效能／高吞吐驗證。使用既有 open_connection 的 connect/read/write timeout，在 worker 執行。不要把整份掃描放到 Discord event loop。

## Snapshot 完整性

Repository 在控制鎖內執行無 LIMIT、無每使用者／Guild／已核准篩選的完整事件集合查詢；獨立操作 ID 清單能發現 JOIN 投影缺列。歷史來自舊 songs 與 jb2_legacy，未註冊至新投影的舊影片也視為未知歷史，不能當新歌。

讀取目前 JSON review 後核對 SQL video ID、review_revision、review_status；混合或矛盾投影直接錯誤，不挑有利資料。原 Claim 的支付版本與現在映射不符時，保留原 AWARDED，但以衝突證據凍結可能受影響的未結算候選；原已獲獎版本也保持關閉。

受理水位、revision、候選 scope、歷史範圍與排除 references 由資料庫讀出。新操作在同一持鎖交易中加入其已配置的新 key。snapshot_ready 或 legacy_writers_quiesced 未確認時 complete=False；呼叫者無公開 complete 參數。完整性仍以可信資料庫、所有 writer 遵守鎖協議及 operator 真實隔離舊 writer 為前提，不能認證惡意管理者或被改寫的資料庫。

## 原子 Reward Claim 與 Nickel Ledger

settle 不接受 caller 提供的 READY。每次在鎖內用 Batch A evaluator、歷史 gate、merge barrier 與當前 Claim 重新判定。正常 +1 交易包括：
1. 鎖 recipient users row，檢查 users.credit_balance 等於 Ledger SUM；不一致則整筆停止，不擅自修復。
2. INSERT 原六表 credit_ledger 的 submission_award、delta=1、原 song FK、穩定 jb2:SHA256(operation_id) idempotency key。保留既有每影片獎勵唯一鍵。
3. INSERT jb2_claims：當前 canonical version PK、operation UNIQUE、ledger UNIQUE。
4. users.credit_balance +1。
5. 更新候選狀態／衝突凍結及 revision，INSERT 成功 settlement result。
6. 同一普通 MySQL 交易 Commit。

任何中途失敗，Claim、Ledger、Balance、Candidate 狀態與 Result 一起 Rollback；即使走到 provisional Ledger 後 Claim unique 衝突也不保留半筆帳。不存在僅靠 Python Lock 或無 unique constraint 的「先查後寫」。同 operation 成功重試先讀原 settlement result，不能再 +1。尚未 READY 的評估不寫永久「失敗已結算」回執，允許後續真實審核進展後再評估。

settlement_enabled 預設 FALSE；YouTube API＋獎勵政策仍 UNRESOLVED，不因程式或 Fake 測試成功啟用正式自動獎勵。Owner Grant、Exchange 扣幣、退款、撤銷／追回不在本批次。

## 歷史、合併與 Owner 稽核

- 未確認歷史保持阻擋；Owner 在有新分享事件後按需確認舊影片版本，但不替原歷史投稿建立順序或 Claim。
- 正式合併只由 configured Owner decision 進入，保留原 version 與所有 Claim／Ledger；flatten canonical root 用於新比較。
- 曾有支付或 barrier 的合併版本建立永久 barrier。發現多筆已付 Ledger 屬同版本，記錄有穩定 audit_key 的例外，保留所有原帳。
- 稽核包含舊六表獎勵，沒有將舊 Ledger 虛構成新 Claim。未知舊獎勵身分未確認前不推測重複。
- 決策 key 與完整 intent digest 綁定，原因／actor／證據或 expected revision 不同即拒絕；原 action 重試直接讀原結果。
- Owner identity 使用部署設定的 Discord User ID，比對在交易前完成。暱稱、管理員角色或一般指令選項不能替代。尚未接入真實 Discord actor 認證；未來 handler 必須從 authenticated interaction.user.id 提供 actor。
- 身分變更／明確 conflict unfreeze 即使沒有改變 review status，也推進 revision，防止舊身分決策覆蓋新證據。clear_conflict_freeze 須 Owner 明確提出；持續存在的矛盾仍會重新凍結。
- 原 submission receipt 不因審核進展或獎勵更新而改寫；讀取目前狀態應使用 snapshot／settlement，而不是把原 Pending 回執誤認為現在仍 Pending。

## Pending 提醒

第一次 due 精確取事件的 DB accepted_at + INTERVAL 24 HOUR，同影片主動再分享不重置。之後依 **UTC 日期**保留每日一份 Owner 待審摘要，包含 validation/review pending、validation_failed，以及 review 已 Approved 但 identity pending／conflict 的項目。等待另一投稿的已核准候選不單獨當成 Owner 待審項目。

同日重試回原摘要；新一天依目前狀態重新選取。只有被列入摘要的影片更新提醒狀態；原 Owner target 留在 batch，Owner 設定中途改變不悄悄改投遞對象。建立 batch 是 reservation，不是已發送；本批次沒有 scheduler daemon、Discord send、delivery ack 或 exactly-once 承諾。排程判定不修改 accepted_sequence，不因 24h／每日提醒失去優先權。實際首次發送時間仍受未來排程輪詢時間影響。

## 離線證據與實際命令

安全明列回歸：**492 Passed／0 Failed／0 Skipped**，包含既有安全集合 436 與 Batch B 新增 56 項。型別／docstring／函式實作長度／巢狀檢查通過；本批次 Python 與測試 helper 最多 22 行實作、巢狀最多 3，應用程式 AST 複雜度估算最高 9。

FakeDatabase 只實作被測 SQL protocol、Python copy-on-transaction 與有期限的 RLock；**RLock 只在測試替身，production 使用 MySQL row lock**。這沒有驗證真正的 FK、CHECK、collation、死鎖、MySQL DDL 語法／隱式 Commit、網路斷線或 InnoDB isolation。Core eligibility／review／workflow／evaluator 都使用實際程式，沒有 Mock 掉核心規則。

實際執行命令（既有 venv、不寫入 cache／bytecode、阻擋 socket／子程序／檔案寫入／.env 讀取）：

```powershell
@'
import os
import sys
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
os.environ['PYTEST_ADDOPTS']=''
blocked=[]
write_flags=os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND
mutations={'os.mkdir','os.remove','os.rmdir','os.rename','os.link','os.symlink','os.truncate','os.chmod','os.utime','subprocess.Popen'}
def readonly_guard(event,arguments):
    forbidden=event in mutations or event.startswith('socket.')
    if event=='open':
        path,mode,flags=arguments
        forbidden=(isinstance(mode,str) and any(character in mode for character in 'wax+')) or bool(flags & write_flags)
        forbidden=forbidden or (isinstance(path,(str,bytes)) and os.path.basename(os.fsdecode(path))=='.env')
    if forbidden:
        blocked.append(event)
        raise RuntimeError('Read-only audit blocked: '+event)
sys.addaudithook(readonly_guard)
import pytest
arguments=["tests/test_submission_snapshot.py","tests/test_submission_workflow.py","tests/test_version_identity.py","tests/test_reward_candidates.py","tests/test_share_replies.py","tests/test_review.py","tests/test_eligibility.py","tests/test_song_identity.py","tests/test_urls.py","tests/test_phase2_quality.py","tests/test_repository.py","tests/test_schema_static.py","tests/test_mysql_safety.py","tests/test_mysql_assertions.py","tests/test_repository_acceptance_safety.py","tests/test_submission_migration.py","tests/test_submission_persistence.py"]+['-p','no:cacheprovider','-p','no:logging','-o','addopts=','--capture=sys','-q','--tb=short']
result=pytest.main(arguments)
print('READONLY_GUARD_BLOCKED_EVENTS:',blocked)
raise SystemExit(result or bool(blocked))
'@ | .\.venv\Scripts\python.exe -B -
```

最後輸出 READONLY_GUARD_BLOCKED_EVENTS=[]。本次沒有嘗試完整 Discord／Gateway／暫存檔案測試；不宣稱完整歷史套件已重新通過。未新增或執行任何會自己連線 MySQL 的整合案例。

覆蓋：Migration 結構／checksum／重入／部分 DDL／固定目標防護、並行受理與發獎、A Pending/B Approved、拒絕釋放、同 key 技術重送、主動再分享、unknown Commit 前後與 B 介入排序、所有帳務寫入階段故障回滾、Claim unique backstop、Ledger／Balance 漂移、未知／已確認歷史、舊獎勵例外、版本合併、證據衝突、目前投影缺列／revision 矛盾、Owner 權限／過期版本／重複意圖、兩種核准硬時長限制、每個中間審核 revision、24h 邊界、UTC 每日摘要與原 Owner target。

## 下一階段真實 MySQL 驗收清單（本次未執行）

1. 另行授權後先確認原六表與新增表的欄位、索引、CHECK、FK、collation、MySQL 版本與 InnoDB；實際執行增量 DDL，驗證重入保護與 applying 中斷處置。
2. 僅用固定隔離資料庫／帳號、TEST_DB_* 與現有顯式測試開關；新增本批次專用開關與認證流程，不直接套用舊六表 fixture 就啟動新業務。
3. 驗證前後六表筆數、餘額／Ledger 合計與原 ID 集合，並檢查新增 17 表。不得清空資料表或把 production 當 disposable。
4. 真正的公開 Repository 會 Commit；**外層 Rollback/savepoint 不能撤銷它已 Commit 的多連線交易**。必須先核准隔離庫恢復／有 namespace 的精確 fixture cleanup 計畫、FK 清理順序及異常停止；不能假稱沿用 Phase 1 rollback-only fixture 就涵蓋真實 Commit。
5. 用兩個真實 connection／獨立 process 驗證 control row 鎖、2 秒 lock timeout、A/B 提交／回滾時序、技術重送及公平性；控制端與 worker 都要有期限與 finally 清理。
6. 在每個結算 SQL 後故障、斷線／kill worker、deadlock／lock timeout、Commit 送達與回覆丟失等情境，確認重試只保留一個 Claim／Ledger／+1。
7. 驗證一致完整投影、舊 writer 停用策略、未知舊目錄、版本合併及歷史帳務例外；確認六表資料不被回寫修復。
8. 驗證 DB UTC、accepted_at +24h、日期邊界與提醒批次 rollback/replay。沒有 Discord 實際發送測試。
9. 只有完整性／帳務／政策條件均另外核准，才可能啟用測試情境所需的 control flags；正式獎勵與 YouTube API 仍不得隨此開啟。

剩餘風險：尚無真實 MySQL 證據；Migration metadata 非完整漂移驗證；全域掃描／序列化未做容量測試；舊 writer 的停止是部署責任；JSON codec 是資料格式 allowlist 而非證據來源認證；Owner actor 身分取決於未來可信 transport；CommitOutcomeUnknown 必須由上層保留原 key、安全呈現錯誤，不能把 driver／SQL 內容直接回覆使用者。未實作生產部署、Owner Grant、Exchange、外部影片驗證或實際提醒投遞。

本次僅撰寫程式、Migration artifact、文件與執行離線測試：沒有 MySQL／Discord／YouTube／其他 API 連線、Migration 執行、真實 Nickel 操作、機密讀取、套件／模型安裝、付費服務或任何 Git 操作。完成後停止等待驗收及下一步授權，不開始 Batch C。



### Batch B 雙帳號 Migration 安全改造（離線，待人工驗收）

Application `TEST_DB_*` 與 Migration `TEST_MIGRATION_DB_*` 已分離，固定本機測試庫及各自帳號；Application 不需要也不得具有 DDL 權限。新增獨立唯讀 Migration Preflight 與預設關閉的執行入口，支援 schema/table grants。最小權限 SQL 與分階段 PowerShell 流程見 [本機驗收流程](phase2b2-batch-b-user-run.md)。

本輪安全離線回歸 532 Passed／0 Failed／0 Skipped；無 MySQL、Discord、YouTube 或實際 Ledger 操作。真實 Migration／InnoDB 並行與原子 Nickel 驗收仍未執行。Phase 2B.1／Batch A／Batch B 均未由本輪標記為人工 Accepted，不開始 Batch C。


最終權限離線覆核：Application 改為逐表最小權限，無 DELETE，jb2_migrations 僅 SELECT；原 schema-level 寫入會被阻擋。詳見 [權限矩陣與操作限制](batch-b-privilege-audit.md)。本輪離線回歸 548 Passed／0 Failed／0 Skipped；真實 MySQL 未驗證，等待人工審核。


### Batch B 真實 MySQL Harness（實作完成，待帳號與測試執行授權）

使用者已回報 Application／Migrator Preflight、Migration 2b2-b1 與 23 表部署通過，Migrator 已鎖回，control 三開關 OFF；本輪保留這些成果，未重新驗證或執行部署。前文「Migration 未執行」屬較早離線階段紀錄，以本段現況為準。

新增獨立 jukebox101_test_harness 的逐表／指定欄位 DBA SQL 草案及 Baseline → Enable → 真實交易 → 精確主鍵清理 → Restore → Verify 架構；Application 權限不變。已實作 23 個預設 skip 的真實 MySQL 案例，本輪 650 Passed／0 Failed／23 Skipped；其中 102 個為新離線安全測試，沒有連線 MySQL。一次性帳號建立／GRANT、真實寫入及隔離庫開關啟用均待另行人工授權，Batch B 尚未整體人工 Accepted，不開始 Batch C。詳見 [Harness 與真實測試操作文件](batch-b-real-mysql-tests.md)。


Harness 最終安全收斂（離線）：保持 submissions 僅 SELECT 與 18 表 DELETE；新增精確 control revision／switch drift 檢查、server UUID pin、未啟用角色與額外 grants 檢查、清理失敗原因保存。DBA 草案改為先建立鎖定 Harness、完整核對後只解鎖 Harness；須受控執行，不能直接忽略 SELECT 結果跑完整批 SQL。本輪 681 Passed／0 Failed／23 Skipped；未連線 MySQL、未執行任何 DBA 或業務 SQL。詳見 [單次授權範圍與安全限制](batch-b-real-mysql-tests.md)。
