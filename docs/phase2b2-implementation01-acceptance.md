# Phase 2B.2 Implementation 01 — Offline Identity and Reward Priority

狀態：**Implemented／Offline Tested／Manual Acceptance Pending**。只完成本子階段，Phase 2B.2 整體未完成。Phase 2B.1 保持等待使用者人工驗收；前次 2B.1A 註解修復與接受建議不代表 User Accepted。

## 模組與公開契約

| 模組／介面 | 責任 |
|---|---|
| `version_identity.RecordingVersionIdentity` | 可信內部提供的 opaque canonical 版本 ID；不從歌名、影片 ID 或 Remix 字樣產生。 |
| `VersionAssignment`／`VersionIdentityStatus` | 綁定 VideoIdentity、版本及證據參照；UNRESOLVED／CONFIRMED／CONFLICT 分開。 |
| `VersionExclusion` | 明確證據排除某影片屬於某版本；用來解除未知前序的特定版本阻擋。 |
| `compare_version_assignments`／`VersionRelation` | 回傳 SAME／DIFFERENT／UNRESOLVED／CONFLICT，不擷取或推斷外部證據。 |
| `reward_candidates.RewardCandidate` | operation ID、使用者、受理序號、目前影片 ReviewSnapshot、版本映射、既有 awarded 事實及選填 audit time。 |
| `RewardEvaluationInput` | 不可變候選集、歷史版本集合、排除證據及完整性聲明；complete 預設 False。 |
| `evaluate_reward_candidates` | 依受理序號產生 RewardDecision；不修改輸入、不預約、不入帳、不發幣。 |
| `RewardCandidateStatus`／`RewardDecisionReason` | 獨立獎勵狀態與解釋原因，不混入既有 ReviewStatus。 |
| `share_replies.ShareScenario`／`classify_share_scenario` | 未來呈現層的五個穩定情境；不影響獎勵決策。 |
| `approved_share_reply`／`APPROVED_SHARE_REPLIES` | 唯讀固定映射，只含五句逐字核准台詞；無隨機選擇、Discord 呼叫或部署。 |

`require_reference` 是共用輸入檢查，拒絕空白或格式不明的內部參照。

## 信任邊界與 Phase 2B.1 相容性

所有版本、正負證據、catalog history、awarded、complete 與 accepted_sequence 均須由未來可信內部資料層提供。本模組只驗證結構與一致性，不認證證據真偽、不驗證 Owner，不接受一般 Discord 使用者直接聲稱。測試序號只模擬未來 MySQL 成功受理順序，不實作順序配置。

保留 `song_identity.py`、`eligibility.py`、`review.py`、Parser、Service、Repository、Schema、handlers 原有契約。新 VersionAssignment 是獨立模型，不自動解讀既有 SongIdentity.recording_id。Phase 2B.1 的 Approved 與獎勵布林函式不包含前序等待，不能直接拿來授權正式發幣。

ReviewSnapshot 仍執行可靠完整影片 60～720 秒硬限制。候選須綁定相同 VideoIdentity；APPROVED 只是必要條件，同一影片在完整快照中的「目前」ReviewSnapshot 應一致。若混入不同版本／狀態的舊 review，評估為衝突，須由未來資料層先建立一致的 current projection；不自動挑最新或猜測勝出證據。

## 優先權與獎勵狀態

| 狀態 | 語意 |
|---|---|
| WAITING_IDENTITY | 版本未知，不樂觀視為新歌。 |
| WAITING_VALIDATION | 影片驗證／歌曲審核未完成，包含暫時驗證失敗。 |
| WAITING_PREDECESSOR | 前序可能競爭同版本，或缺少完整快照聲明；回傳前序 operation IDs 或 INCOMPLETE_SNAPSHOT 原因。 |
| READY | 目前完整快照下最早的合格候選；純離線必要條件，沒有任何帳務效果。 |
| AWARDED | 僅呈現 caller 提供的既有結算事實，不由 evaluator 設定。 |
| INELIGIBLE | 明確拒絕、確認歷史收錄或版本已有獎勵；保留分享，不代表新增零額 Ledger。 |
| CONFLICT | 可信身分／排除／目前影片審核矛盾，或既有獎勵與優先權／唯一性不一致；受影響未結算候選凍結。 |

1. 同錄音 MV／Audio 在可信資料中共用 canonical 版本；每版本最多一個 READY，其餘等待。
2. 不同官方實質編曲 Remix 由可信證據明確給不同 canonical ID；可獨立競爭。此模組不認證 Official、編曲或名稱。
3. 已確認不同版本的 pending 不阻擋彼此。更早未知影片仍阻擋，除非 VersionExclusion 明確排除目標版本。
4. Review REJECTED 解除該候選優先權；暫時錯誤與時間逾期不解除。accepted_at 只作 audit，沒有 current time、24 小時 TTL、放棄或自動取消邏輯。
5. historical_versions 代表可信的歷史已收錄集合，即使沒有已知獲獎人仍不重開首次資格。集合的永久保存是未來持久化責任，不能移除集合後宣稱仍受保護。
6. exact operation 重複輸入只產生一個決策；同 operation 有不同快照或不同 operation 使用相同序號均拒絕。更新 review 應先由 caller 去重建立 current snapshot，不能把多版本 operation 當同時有效資料。
7. 已知衝突只影響同版本及相關影片；無法界定版本的衝突保守凍結可能受影響者，需可信排除證據才隔離。
8. 已 AWARDED 永遠保留該事實；重複獎勵或較晚已領而較早未定只凍結未結算候選，不自動追回或轉移 Nickel。

完整性是 caller 的明確斷言：必須包括可能的前序、相關已領紀錄、歷史版本與可能影響該組的衝突。default complete=False 不允許 READY；這不是跨程序鎖、資料庫 compare-and-swap 或全域唯一鍵。重複純評估可再次得到 READY，不會消耗資格；正式結算仍需未來資料庫原子交易。

沒有自動 identity merge、alias root 選取、拒絕重開或撤銷政策；若事後確認合併，caller 須提供統一 canonical snapshot 及既有結算事實，衝突結果只供後續人工處理。

## 五個已核准情境

| 情境 | 唯一已核准文字 |
|---|---|
| DUPLICATE_SELF | 又是這首？看來你真的很喜歡它！💿 |
| DUPLICATE_OTHER | 有人搶先一步把這首放進點唱機了！💿 |
| RETRY_SAME_OPERATION | 收到收到！不用連按兩次，我聽見了！🎵 |
| SAME_VERSION_DIFFERENT_VIDEO | 換了張封面，我還是認得這首歌！💿 |
| IDENTITY_UNRESOLVED | 這首歌好像有點耳熟……讓我確認一下！🔍 |

分類優先順序：相同 operation 重送 → 未確認／衝突身分 → 較早的同影片自己投稿 → 較早的同影片別人投稿 → 已確認同版本不同影片。其他情境回傳 None，不增加或改寫台詞；不同影片即使同使用者也使用較具體的 SAME_VERSION_DIFFERENT_VIDEO。

情境只描述分享歷史，不是核准、獎勵歸屬、發幣或成功收錄的完整回覆。未來 adapter 必須結合 RewardDecision 呈現實際狀態；衝突／pending 不得因一則趣味台詞而被隱藏。相同 operation 不可更改使用者、影片或受理序號，但可由 caller 提供更新後 current review。

## 測試與安全

定向回歸：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_version_identity.py tests/test_reward_candidates.py tests/test_share_replies.py tests/test_review.py tests/test_eligibility.py tests/test_song_identity.py tests/test_urls.py tests/test_phase2_quality.py -q --tb=short
```

實際 **263 Passed／0 Failed／0 Skipped**。新增 78 項涵蓋本次必要的 12 類情境，另測可信排除範圍、正負證據矛盾、同影片矛盾 review、無完整 scope、序號衝突、重送衝突、重複已領紀錄、不追回既有獎勵與五句文案逐字固定。後續只補充測試 purpose 註解與文件，不改變已驗證邏輯。

完整離線回歸：

```powershell
.\.venv\Scripts\python.exe -c "import os, pytest; os.environ.update({name: '0' for name in ('JUKEBOX_RUN_MYSQL_TEST', 'JUKEBOX_RUN_REPOSITORY_MYSQL', 'JUKEBOX_RUN_MYSQL_READONLY', 'JUKEBOX_DISPOSABLE_DATABASE', 'JUKEBOX_TEST_SCHEMA_READY')}); raise SystemExit(pytest.main(['-m', 'not mysql and not mysql_readonly', '-q', '--tb=short', '-o', 'faulthandler_timeout=15']))"
.\.venv\Scripts\python.exe -m pytest tests/test_mysql_readonly.py::test_check_rendering_normalization -q --tb=short
```

實際主回歸 **528 Passed／0 Failed／0 Skipped／34 Deselected**；另執行合成文字 normalization **2 Passed**，合計 **530 個不同案例 Passed**，涵蓋原有 452 與新增 78。34 排除包含 32 項真實 MySQL 案例與另外補跑的 2 項純文字案例；不要執行整個 mysql_readonly 檔案。所有執行開關只在 Python 子程序關閉，不讀真實 dotenv。

完整回歸在經允許的沙箱外執行，以建立既有 Windows asyncio 所需本機 socket pair；沒有 Discord／MySQL／YouTube 連線。保留一項既有 audioop 棄用警告。既有品質測試通過；新增業務函式實作不超過 25 行、巢狀不超過 3 層，標準 AST 複雜度估算均不超過 10，函式有型別標註與標準 docstring，核心變數有 purpose 註解。

本次不修改 Schema／Migration／Ledger／餘額／Repository／Service／handlers／`.env`，不讀取機密、不安裝套件、不使用外部 API 或付費服務、不執行 Git 命令。新測試只使用合成資料；測試快取屬執行產物，未宣稱 Git 工作區乾淨。

## 已知限制與下一步

### Implementation 01A — A08 修復與 A09 補證

本節是本次最新結果；上方 263／530 項結果是 Implementation 01 的歷史紀錄，並非本次重新執行結果。狀態仍為 Manual Acceptance Pending，未標記 User Accepted。

- A08 根因：找到第一筆一致 operation 後立即回傳，忽略其後矛盾紀錄。現在先遍歷並驗證全部同 operation 紀錄的使用者、影片及受理序號；任何矛盾維持拋出 ValueError，全部一致才回傳 RETRY_SAME_OPERATION。公開介面、分類優先順序及五句核准台詞不變。
- 新增 `test_conflicting_replay_history_is_rejected_in_any_order`（三種矛盾 × 兩種順序，共 6 項）、`test_multiple_identical_operation_records_are_retries`（2 項）、`test_new_operation_repeat_is_not_technical_retry`（1 項），直接呼叫實際分類器。
- 先執行 `tests/test_share_replies.py`：**23 Passed／0 Failed／0 Skipped**。再執行上述八個定向回歸檔案：**272 Passed／0 Failed／0 Skipped**，其中包含前述 23 項，不能相加為不同案例。
- 本次兩次執行均使用既有 Python 的 `-B`、停用 pytest 第三方自動載入、cacheprovider 與 logging、清空 pytest addopts、採 `--capture=sys`。Python audit hook 阻擋 socket、子程序、檔案寫入／變更與 `.env` 讀取；兩次被阻擋事件清單均為空。
- 未重新執行完整歷史 530 項：其中其他測試需要暫存檔案或 Windows asyncio 本機 socket；本次採嚴格無寫入、無 socket 的純邏輯回歸，不宣稱完整套件已重跑。
- A09 功能相容性：目前 Parser、Song Identity、Eligibility、Review 與新增版本／候選／回覆公開介面的相關離線測試通過。Service、Repository、Schema、handlers 未由本次測試做真實整合驗證。
- A09 歷史限制：唯讀 `git rev-parse --verify HEAD` 回報 `Needed a single revision`；相關舊模組的 `git ls-files` 沒有追蹤結果。因此無可信 HEAD／歷史快照可證明與 Phase 2B.1 基線逐位元相同；功能回歸不等於歷史檔案一致性。本次未初始化 Git，僅執行上述唯讀檢查。
- 本次只修正回覆分類器、補充其測試及本節文件；沒有實作帳務、持久化或外部整合，也不開始 Implementation 02。

- 證據真偽、canonical ID、成功受理順序、快照完整性與歷史保存尚無真實來源，皆為可信內部契約。
- 評估採小型離線集合掃描，未作大資料庫規模查詢最佳化，不能直接拿來全量掃 production。
- YouTube API＋Nickel 獎勵政策仍 UNRESOLVED；正式自動驗證／發幣維持未啟用。
- 歷史錯誤發幣、身分合併、Owner 權限、期限裁決、Ledger、Exchange、Daily 與 Discord 業務整合均未實作。
- 建議先人工驗收，再另行授權 current snapshot 聚合、持久化契約與 Migration 設計／mock Repository 測試；實際 DDL、整合測試及部署須分別批准。

完成後停止等待人工驗收，不自行開始下一個子階段，也不將 Phase 2B.2 整體或 Phase 2B.1 標為 User Accepted。
