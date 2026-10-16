# Phase 2B.1 — Offline Eligibility Acceptance

狀態：**Implemented／Offline Tested／Manual Acceptance Pending**。只完成離線歌曲資格契約與 Hybrid Review 基礎；未開始 Phase 2B.2。既有 Phase 2A Repository／DB-disabled Transport 的 Accepted 範圍不變，真實 Discord＋MySQL 持久化互動仍 Pending。

## 模組與輸入信任邊界

| 模組 | 責任 | 不包含 |
|---|---|---|
| `bot/song_identity.py` | 重用 Parser 建立大小寫敏感 VideoIdentity；SongIdentity 預留 recording_id；SubmissionDisposition 分離 UNKNOWN／NEW_CANDIDATE／DUPLICATE | 跨影片匹配、全域首次有效投稿判定、資料庫約束 |
| `bot/eligibility.py` | DurationEvidence 綁定 VideoIdentity、來源、可靠性、完整影片範圍；精確判定 PASS／REJECT／PENDING_VALIDATION | API、證據真偽認證、歌曲分類 |
| `bot/review.py` | immutable snapshot、版本檢查、驗證／核准／拒絕／失敗及重試；純獎勵必要條件 | Owner 驗證、實際自動核准規則、持久化、發幣 |

本階段的 reliable、full_video、song_eligible、NEW_CANDIDATE 都是離線呼叫者的明確輸入，不是本模組查證的外部事實。未來 adapter／service 必須由可信來源建立資料，不能直接接受 Discord 使用者傳入這些欄位。ApprovalSource.OWNER 不代表完成 Owner 身分驗證。

## URL 與時長契約

Parser 原始碼未修改，沿用 `https://www.youtube.com/watch?v=VIDEO_ID` canonical URL。單影片帶 list、index、t、start、追蹤參數須接受並移除參數；保留原來源欄位作 provenance。純 Playlist、假主機、缺少／無效／多重 ID、不支援路徑拒絕。不展開清單，不擷取 URL 指定區段。

時長只接受非負 int（不含 bool）或有限 Decimal；不接受 float、數字字串、ISO 8601、NaN、Infinity 或負值。此階段刻意不實作 ISO parser，未來若需要須獨立測試並授權。無可靠來源、非完整影片或無效資料一律 PENDING_VALIDATION，包括原本看似界外的數字；不能靠不可信證據下最終決定。

| 合成完整時長 | 結果 |
|---|---|
| 59 | REJECT |
| 60、61、719、720 | PASS |
| 721 | REJECT |
| None／不可靠／無效格式 | PENDING_VALIDATION |

另測 0、超大整數、精確 Decimal 邊界（59.999…／720.000…）、片段長度與完整影片 900 秒。沒有容許誤差或四捨五入；沒有真實影片觀測結果。

## 審核轉換

| 操作 | 前置狀態／條件 | 新狀態 |
|---|---|---|
| 初始化 | 合法 VideoIdentity | PENDING_VALIDATION，version 0 |
| record_validation | 非終態、版本相符、同影片 | PASS → PENDING_REVIEW；REJECT → REJECTED；未知 → PENDING_VALIDATION |
| record_validation_error | 非終態、版本相符、結構化錯誤 | VALIDATION_FAILED，清除可用時長證據 |
| 重試驗證 | VALIDATION_FAILED、版本相符 | 重新依時長結果轉換，清除錯誤；不直接核准 |
| approve_review | PENDING_REVIEW、時長仍 PASS、song_eligible 明確 True、AUTO／OWNER 來源、版本相符 | APPROVED |
| reject_review | PENDING_REVIEW、版本相符 | REJECTED |

每次合法轉換 version +1，不修改原 snapshot。所有終態操作均拒絕；未提供重啟已拒絕、核准撤銷或 Nickel 追回流程，這些仍待決策。舊版本以 StaleReviewVersion 拒絕；非法轉換以 InvalidReviewTransition 拒絕。

版本檢查要求呼叫者提供目前 authoritative snapshot。兩個呼叫者各自持有同一舊值，純函式本身無法完成跨程序 compare-and-swap；未來持久化需資料庫條件更新／鎖與原子交易，不能宣稱本階段已解決競態。

`is_submission_reward_eligible` 只在 APPROVED、可靠完整時長仍 PASS、外部投稿結果為 NEW_CANDIDATE 時為 True。它只代表必要前置條件，不保證全系統首次、不代表已分配獎勵、不能直接作為 production 發幣授權。UNKNOWN／DUPLICATE／待驗證／待審核／拒絕／失敗皆 False。

## 離線測試與命令

在專案根目錄 PowerShell 執行。第一個命令只在 Python 子程序內關閉資料庫授權開關，不改父程序環境或 `.env`：

```powershell
.\.venv\Scripts\python.exe -c "import os, pytest; os.environ.update({name: '0' for name in ('JUKEBOX_RUN_MYSQL_TEST', 'JUKEBOX_RUN_REPOSITORY_MYSQL', 'JUKEBOX_RUN_MYSQL_READONLY', 'JUKEBOX_DISPOSABLE_DATABASE', 'JUKEBOX_TEST_SCHEMA_READY')}); raise SystemExit(pytest.main(['-m', 'not mysql and not mysql_readonly', '-q', '--tb=short', '-o', 'faulthandler_timeout=15']))"
.\.venv\Scripts\python.exe -m pytest tests/test_mysql_readonly.py::test_check_rendering_normalization -q --tb=short
```

第二個命令僅執行兩個使用合成文字的 normalization 案例，沒有使用 MySQL fixture；檔案的 mysql_readonly marker 不代表這兩個案例會連線。不要改為執行整個檔案。

完整結果：主回歸 **450 Passed／0 Failed／0 Skipped／34 Deselected**；純文字 **2 Passed／0 Failed／0 Skipped**；合計 **452 個不同案例通過**。涵蓋既有 298 項及新增 154 項（新模組測試 148 項、URL 參數化新增 6 項）。32 項真實 MySQL 案例沒有執行，另外 2 項純文字案例先被 marker 排除再獨立執行。沒有藉由打開開關或修改安全檢查取得通過。

首次定向檢查在增補證據案例前為 167 Passed，屬中間結果，不與最終數字相加。完整回歸第一次在 Windows 沙箱 asyncio 案例停滯，已中止、未產生通過結果；最後經允許在沙箱外建立本機 socket pair，仍為離線測試。現有 discord.py 的 audioop DeprecationWarning 1 項保留，Python 3.12 下未新增或升級依賴。

既有 `test_phase2_quality.py` 一併通過，檢查 bot 函式實作不超過 25 行、控制巢狀不超過 3 層及函式必要 docstring。沒有修改既有 Service、Repository、Discord handlers、測試安全防護或 Schema。

## 未完成、風險與人工驗收

- 真實影片存取、時長證據取得、證據有效期、直播／Premiere 最終時長與歌曲分類均未實作。
- AUTO 僅是合成決策來源，沒有生產自動核准政策；Owner 身分驗證未實作。
- MV／Audio／Live／Cover／Remix／錄音或作品去重、首位有效投稿競爭、重審／撤銷仍待決策；recording_id 不進行映射或合併。
- 未寫 credit_ledger 或 users.credit_balance；沒有 Owner Grant、Exchange、Daily 或新管理指令。
- YouTube API＋Nickel 獎勵政策仍 UNRESOLVED。架構分離與離線測試通過不等於官方合規認可；後續 API、憑證、資料保存及正式發幣需先釐清並另行授權。
- 人工驗收：核對本文件、執行上述離線命令、檢視硬限制／版本／重複資格案例及原服務回歸。不得以本階段進行真實 MySQL／Discord／YouTube 測試。

本次不讀機密、不修改 `.env`、不安裝套件、不執行 Git 命令或外部業務操作。只根據實際編輯清單回報工作區；未查 Git status，不能宣稱乾淨或辨識其他既有變更。完成後停止等待人工驗收，不開始 Phase 2B.2。
