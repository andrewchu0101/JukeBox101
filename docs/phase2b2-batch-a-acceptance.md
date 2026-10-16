# Phase 2B.2 Batch A — Integrated Offline Submission Workflow

狀態：Implemented／Offline Tested／Manual Acceptance Pending。Phase 2B.1、Implementation 01 及 Phase 2B.2 整體均未由本次標記 User Accepted。本文件保留 Batch A 當時範圍與結果；後續 Batch B 程式及尚未執行的 MySQL 驗收見 [Batch B 文件](phase2b2-batch-b-acceptance.md)。

## 完成範圍與流程

`submit_offline(SubmissionRequest, SubmissionSnapshot, original_result=..., random_source=...)` 是新增的同步純協調層，不接入正式 MusicService 或 Discord handler：

1. 重用 parse_youtube_url，保留來源與 canonical URL；list／時間戳記不改變 VideoIdentity。
2. 核對 URL、VersionAssignment、ReviewSnapshot 的影片身分。
3. 先核對全部同 operation 紀錄；技術重送要求可信原回執，保留原 candidate／review／reward／URL 結果，僅變更重送呈現與 create_share_event=False。不重新審核，也不因重試附帶較新證據改寫原結果；此路徑不套用新的 expected_review_version。正式審核更新必須另走版本化流程。
4. 新操作使用現有 record_validation／record_validation_error／approve_review／reject_review。expected_review_version 過期直接拒絕；AUTO／OWNER 均不能繞過可靠完整影片 60～720 秒與明確歌曲判定。
5. 聚合 manifest、所有既有候選、新候選、歷史版本與排除證據，重用 evaluate_reward_candidates。
6. 額外檢查未知歷史與歷史證據衝突。未知歷史在沒有可信特定版本排除證據時阻擋 READY；已確認歷史版本不重新開獎。
7. 分開回傳事件意圖、候選／審核、RewardDecision、ShareScenario 與核准 reply。

create_share_event=True 只是新增分享事件的意圖，不是已寫入資料庫。READY 是可重複的離線決策，不是唯一 Claim 或已發 Nickel。主動再分享保留新操作意圖，同版本已發獎勵時仍 INELIGIBLE。Pending 沒有逾期取消；既有 AWARDED 只呈現可信事實、不追回。

## Snapshot 與未來 Repository 契約

SnapshotManifest 是**獨立提供**的可信範圍證明，不由服務從剩餘 rows 推導。它包含 read revision、所有受理 operation IDs、歷史影片 IDs、歷史斷言筆數（同影片矛盾斷言不能只留一筆）、排除證據 references、through_sequence 與預設 False 的 coverage_certified。

SubmissionSnapshot 保留不可變候選、目前版本化 review、正負證據及未知／衝突歷史。manifest revision、集合、歷史斷言筆數或 watermark 不符時 complete=False；漏掉較早操作即使其餘候選 Approved，也不能得到 READY。相同 operation 的不同 current snapshots 由既有評估器拒絕，不自動挑選有利版本。

**信任限制**：這能偵測與 manifest 不一致的遺漏，無法證明 manifest 本身真實。如果來源同時漏報清單與資料，純離線程式無法發現；相同筆數／參照下錯誤替換事實也不能靠此結構認證。revision、證據 reference 和 coverage_certified 都不是加密憑證，也不是已實作 MySQL 一致讀。

Batch B Repository 必須：
- 在一致讀取與明確 watermark 下提供全球完整候選／目前 review projection、已領紀錄、歷史（含未知）與全部相關衝突／排除證據；不可只查目前使用者、Guild 或已核准資料。
- 從可靠來源產生獨立 scope manifest；不得拿已過濾查詢的結果自行宣告完整。所有事實與版本來源須可稽核。
- 原子配置成功受理順序、全域唯一 operation idempotency key，區分新事件與技術重送；以持久化回執重播原結果。
- 用 MySQL 交易、鎖定順序、唯一 version reward claim／Ledger idempotency key 保護真正結算，並處理不確定 Commit 後的安全重試。
- 保持 Ledger 與餘額一致、跨程序並行安全與歷史永久保存。現有六表與 Repository 尚未承諾支援這些新契約；先審查 Migration 設計，相容舊功能後另行授權。
- 本次未建立 Repository 實作、Migration、唯一 Claim、帳務交易或 Owner 認證。YouTube API 政策仍 UNRESOLVED，真實驗證與正式自動發幣不得因離線通過而啟用。

## 核准台詞池

新增 APPROVED_SHARE_REPLY_POOLS，唯讀映射每個情境至獨立 tuple。select_share_reply 只從該情境池挑選；目前每池只有原有一條逐字核准台詞。可注入 random.Random 以重現選擇；未注入時取首句，未新增任何候選文案。後續擴充文字仍須人工核准。文案選擇不影響 review／reward，未部署 Discord。

## 測試結果與可重現指令

本次最終安全回歸 **436 Passed／0 Failed／0 Skipped**，其中新增 Batch A **71** 項；包含 URL／時長邊界、兩種審核來源、版本別名與不同 Remix、前序等待／拒絕、未知歷史與排除、衝突範圍、快照漏項、重送回執、主動再分享、純評估一致性及核准台詞池。既有品質檢查也通過。

以下是實際使用的受保護命令；不載入第三方 pytest plugin、不寫 bytecode／cache、不建立外部 socket、不讀 .env。Repository／MySQL safety 測試是合成資料與 mock，不是真實 SQL。

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
arguments=["tests/test_submission_snapshot.py","tests/test_submission_workflow.py","tests/test_version_identity.py","tests/test_reward_candidates.py","tests/test_share_replies.py","tests/test_review.py","tests/test_eligibility.py","tests/test_song_identity.py","tests/test_urls.py","tests/test_phase2_quality.py","tests/test_repository.py","tests/test_schema_static.py","tests/test_mysql_safety.py","tests/test_mysql_assertions.py","tests/test_repository_acceptance_safety.py"]+['-p','no:cacheprovider','-p','no:logging','-o','addopts=','--capture=sys','-q','--tb=short']
result=pytest.main(arguments)
print('READONLY_GUARD_BLOCKED_EVENTS:',blocked)
raise SystemExit(result or bool(blocked))
'@ | .\.venv\Scripts\python.exe -B -
```

廣泛全套嘗試未完成：pytest logging 的 Windows NUL 裝置起初被嚴格寫入防護攔下；之後 Discord/aiohttp 收集時觸發 platform.system 的 Windows 版本查詢子程序，也被防護攔下（3 collection errors）。沒有對外連線或執行外部業務，沒有將這些嘗試計入成功。本次最後改為明列上述安全測試集合，全部通過且 READONLY_GUARD_BLOCKED_EVENTS=[]。沒有為通過測試修改既有測試或關閉業務安全條件。

未重新執行完整歷史套件、Discord／Gateway mocks、需要暫存 .env 的案例或任何真實 MySQL／Discord／YouTube 驗收；不宣稱原歷史 530／539 個案例已全部重跑。

## 修正與剩餘限制

開發中的反例發現歷史同影片衝突分支曾回傳 bool 而不是 RewardDecision，已移至布林衝突 helper，相關回歸通過。另補上同影片多筆歷史斷言漏項、排除證據漏項及已知無關版本衝突不阻擋的覆蓋。

已確認相同歷史影片不套用 SAME_VERSION_DIFFERENT_VIDEO 台詞；沒有原投稿者資訊時不猜測 SELF／OTHER。若帶有原回執但缺少對應 operation 歷史，明確拒絕而非產生新分享事件意圖。

版本身分、可靠來源、完整性證明、受理序號、原回執與已發獎勵均由可信內部提供，並非使用者自行聲稱；本階段不認證證據或 Owner、不抓取真實資料。不自動合併版本、不實作撤銷／追回、Exchange、Daily 或外部部署。未提供持久化的有效事件寫入、Review 更新儲存及並行交易；需 Batch B 另行批准。

本次沒有 Schema／正式 Service／Repository／handlers 變更，未讀取秘密、連線外部服務、安裝套件、操作 Nickel 或執行 Git 操作。完成後停止等待人工驗收與下一批授權。

