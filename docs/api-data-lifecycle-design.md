# YouTube API 資料生命週期設計（草案，尚未核准、尚未實作）

狀態：**設計文件**。沒有 migration、沒有程式變更、沒有操作任何正式資料。這份文件的目的，是讓 Owner 能針對具體取捨做決定，並讓日後實作有明確的邊界。

## 一、先講結論（白話）

- 自動審核會向 YouTube 官方 API 取得「完整時長」與「頻道 ID」，並把它們**複製到多張資料表**。目前其中好幾張表，Bot 的資料庫帳號只能新增、不能修改或刪除。
- YouTube 開發者政策對「非授權資料」有 30 日內刪除或更新的要求（原文位置見第二節）。所以光是在 JSON 裡把欄位清成空白**不夠**，也不是一個單純的 `UPDATE`：它牽涉到資料庫權限、讀取資料時的驗證規則、重播與冪等，以及備份。
- 建議做法：**不要再把 API 取得的數值寫進只增不改的表**；改把它們放進一張專用的、可更新的短期表，由獨立的清除工作處理；歷史表只留下「Owner 或系統做了什麼決定」這種不屬於 API 的紀錄。已發出的 Nickel 與帳本完全不動。
- 這個設計**不依賴**對政策的最終解讀：即使 Google 認為內部衍生結果可以長期保存，這個設計也只是多做了清除，不會造成損失。但有幾個問題（第七節）只有 Owner 或 Google 能回答。

## 二、政策原文位置、既有 Google 回覆已涵蓋與未決

**原文位置**：<https://developers.google.com/youtube/terms/developer-policies>，頁面顯示 `Last updated 2026-10-15 UTC`，章節為 `III. … E. Handling YouTube Data and Content`，項目 `4. Refreshing, Storing, and Displaying API Data`。官方頁面對項目用數字排列、交互參照用字母（例如 4.3 即 III.E.4.c），**下列字母編號是依該參照方式推得，送出詢問前請對照頁面原文**。

與本設計相關的條文（以下為摘要，不是逐字引用；請以官方頁面為準）：

| 項目 | 內容摘要 |
| --- | --- |
| III.E.4.c（第 3 項） | 其他類型的授權資料最多保存 30 個日曆日，之後必須刪除或更新。 |
| III.E.4.d（第 4 項） | 非授權資料只能暫時、少量保存，不得超過 30 個日曆日，之後必須刪除或更新。 |
| III.E.4.e（第 5 項） | 無論如何，都要合理努力讓保存的資料與 YouTube 目前提供的一致。 |
| III.E.4.h（第 8 項） | 不得以 API 資料取代為自行計算的類似資料，或使用 API 資料建立新的衍生資料或指標。 |
| 其他 | 授權權杖與少數統計資料有較長期限的例外；**頁面文字中沒有看到影片 ID 或頻道 ID 的豁免**。 |

**Google 既有回覆（Issue 576880673）已涵蓋**（見 `docs/youtube-policy-confirmation.md`）：Nickel 只在 Discord 內分享 URL 時發放、不要求任何 YouTube 互動；以 Data API v3 在後端驗證影片 metadata（例如時長）是允許的用途；並要求仍須遵守其他全部條款。

**回覆沒有涵蓋**：資料保存期限、快取方式與顯示條件。這正是本設計要處理的範圍。

**真正未決（不要當成已定論）**

1. 影片 ID、頻道 ID 若是為了「把使用者的投稿對應到某支影片」而保存，是否仍受 30 日規則約束？政策文字沒有直接回答。注意：使用者自己貼上的連結（含影片 ID）不是由 API 取得的資料；由 API 回傳的 `channelId` 才是。
2. 我們由 API 時長算出的內部結果（例如「完整時長在 60–720 秒內：通過」）是否屬於政策所稱的 API 資料或「衍生資料」？若屬於，連這個結果也需要在期限內處理。
3. 備份中的副本是否需要在 30 日內消失，還是還原後立即清除即可？
4. 「Owner 在某日核准了投稿 X」這種純內部紀錄是否可無期限保存？（直覺上屬於我們自己的決定，但仍建議向官方確認。）

## 三、API 資料目前實際保存在哪裡（已由程式與 schema 核對）

| # | 位置 | 內容 | 來源 | 應用程式帳號權限 | 備註 |
| --- | --- | --- | --- | --- | --- |
| 1 | `jb2_videos.review_json` | 目前的審核快照，含完整時長證據 | API 時長；或 Owner 自述時長 | SELECT、INSERT、**UPDATE** | 目前唯一可就地更新的一份 |
| 2 | `jb2_review_history.review_json` | 每個修訂的審核快照（含時長證據） | 同上 | 只有 SELECT、INSERT | 只增不改；每個修訂一列 |
| 3 | `jb2_evidence.evidence_json` | `persist_review` 每次都把「身分＋整份審核快照」再複製一份進來 | 同上 | 只有 SELECT、INSERT | 這是最容易漏掉的第三份副本 |
| 4 | `jb2_decisions.result_json` | 冪等決定的原始結果（通常是審核快照） | 同上 | 只有 SELECT、INSERT | 供重播時原樣回傳 |
| 5 | `jb2_receipts.receipt_json` | 受理當下的整個結果，含該影片當時的審核快照 | 同上 | 只有 SELECT、INSERT | 同一影片的後續投稿，回執也會帶著既有時長 |
| 6 | `jb2_review_history.decision_reason`、`jb2_decisions.decision_reason` | 自動審核理由文字：`policy=… reason=… channel=<頻道ID> fetched_at=<取得時間>` | 頻道 ID 與取得時間 | 只有 SELECT、INSERT | 文字欄位，不是 JSON |
| 7 | 記憶體 | 一次巡檢中的查詢結果 | API | 不適用 | 程序結束即消失，不需處理 |
| 8 | 備份與傾印 | 以上全部 | — | — | 本專案目前沒有備份保留期限的記錄，**未知** |

**實測確認**：我在可丟棄的 PostgreSQL 17.11 上，讓一支影片經自動審核取得官方時長，再由第二位使用者投稿同一支影片，之後掃描各表。#1–#5 的 JSON 都含有該時長（`jb2_review_history` 3 列中 2 列、`jb2_evidence` 5 列中 3 列、`jb2_receipts` 2 列中 1 列，即第二位投稿者的回執）；#6 的兩個理由文字欄位含頻道 ID；`jb2_events.source_url`、`jb2_settlements`、`credit_ledger` 不含。這是單一情境的探測，不代表窮舉。

不含 API 資料（已核對）：`credit_ledger`、`jb2_claims`、`jb2_settlements`（只存 `RewardDecision`：狀態與原因）、`users`、`jb2_events.source_url`（使用者自行投稿的網址）。**標題從未保存**。

Owner 自己補的時長（`/owner duration`、卡片的「補時長」）是 Owner 的陳述，不是 API 資料，不受本設計約束。

目前自動審核與自動結算的兩個開關預設為 off，PR #4 的 CI 與離線測試沒有連接真實 API；**正式資料庫裡是否已有 API 資料，沒有證據可判斷**（沒有 VPS 存取）。若從未開啟自動審核，則不會有。

## 四、設計

### 4.1 原則

1. API 取得的數值**只存在一個地方**：專用短期表。其他所有表只存「內部決定」。
2. 過期的 API 資料**絕不會被重新當成信任依據**，即使它還留在某個備份裡。
3. 帳本、`jb2_claims`、`jb2_settlements` 與既有獎勵完全不動，也不新增任何自動撤獎或補發。
4. 所有清除與更新都冪等、可重試、可預覽。

### 4.2 新增專用表（草案，非最終 DDL）

```sql
CREATE TABLE jb2_api_observations (
    video_id        VARCHAR(11) COLLATE "C" PRIMARY KEY REFERENCES jb2_videos (video_id),
    observed_at     TIMESTAMPTZ NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,          -- observed_at + 設定的保存上限（不得超過 30 日，建議 25 日以留緩衝）
    duration_seconds INTEGER,                      -- 清除後為 NULL
    channel_id      VARCHAR(64),                   -- 清除後為 NULL
    purged_at       TIMESTAMPTZ,                   -- 清除時間；非 NULL 表示數值已不存在
    purge_reason    VARCHAR(32)                    -- 'expired' | 'video_unavailable' | 'refreshed' | 'restore'
);
```

- 一支影片一列；**刷新是就地更新同一列**，舊值被覆蓋，不是新增一列。
- 狀態流程：新取得，然後在到期前刷新（覆蓋同一列），或到期時清除（數值欄位設為 NULL，並寫入 purged_at）。

### 4.3 審核快照改存「內部結論」，不再存 API 數值

- 現況：`ReviewSnapshot` 在 `PENDING_REVIEW`／`APPROVED` 時**必須**帶著通過的時長證據，否則建構時就會丟出 `InvalidReviewTransition`。因此**只把 JSON 裡的秒數清成 NULL，會讓這些快照無法解碼**。
- 設計：新增 `DurationVerdict`（結論：`PASS` 或 `REJECT`，依據：`youtube-api` 或 `owner-statement`，以及指向 `jb2_api_observations` 的 `video_id`），審核規則改為同時接受「Owner 自述的時長證據」與「API 結論」。API 的原始數值只存於專用表。
- 讀取相容：解碼同時接受舊格式（含秒數）與新格式，兩者經正規化後再比較，避免 `_append_review_revision` 的「Review history revision contradicts current projection」檢查把同一修訂的新舊形式當成矛盾。
- 自動審核理由文字改為 `auto-review policy=… reason=…`，**不再含頻道 ID 與取得時間**；需要追溯時查專用表（期限內）。

### 4.4 刷新與清除

- **預設是「清除」，不是「刷新」**：案件做完決定後，不再需要 API 資料，到期就清。只有**仍在等待決定的案件**（待驗證、待審核）才在到期前刷新。這也是最省 API 配額的做法：`videos.list` 一次最多 50 個 ID，成本 1 單位。
- 清除工作：每日一次的小程式（建議 `python -m bot.api_retention purge`），先預覽（影響列數與摘要 digest）再執行；就地把到期列的數值設為 NULL 並寫入 `purged_at`。
- 影片被刪除或變私人：刷新時 `NOT_FOUND` 即清除並記錄 `video_unavailable`；已決定或已結算的案件不受影響，**不撤獎**；仍在等待的案件回到「待驗證」，由既有流程處理。
- API 無法刷新（配額用盡、API 掛掉）：**不延長保存**。到期仍然清除；等待中的案件因此回到「待驗證」，恢復後再取得。寧可暫時卡住，也不保留過期資料。
- 長期停機：清除在**啟動時先執行**（服務開始接受新工作前），過期資料先清掉再說。

### 4.5 備份與還原

還原可能把已清除的資料帶回來，所以：

1. 還原後、啟用自動審核**之前**，必須執行 `purge`（依 `expires_at` 判斷），這是還原手冊的一個強制步驟。
2. 自動審核管線**只信任** `expires_at` 尚未到期且 `purged_at` 為空的觀測；其他一律視為不存在。
3. 備份本身的保留期限需 Owner 決定（見第七節）：若備份保留不超過 30 日，則自然過期；若更長，則依 1. 處理，但備份檔案本身仍含舊資料，是否可接受要看官方對第 3 項疑問的答覆。

### 4.6 既有資料的一次性整理（若正式資料庫已有 API 資料）

- 需要一支**一次性、可預覽、可分批、可重跑**的遷移工具，改寫第三節 #1–#6 的歷史資料：把含秒數的時長證據換成 `DurationVerdict`，把理由文字中的頻道 ID 與取得時間移除；數值移入專用表（若仍在期限內）或直接丟棄。
- 這需要對只增不改的表有 **UPDATE 權限**，而應用程式帳號刻意沒有。做法：只授權給**遷移專用角色**，且僅在遷移期間；執行後撤銷。這是權限變更，需要 Owner 核准。
- 工具要驗證：遷移前後 `credit_ledger`、`jb2_claims`、`jb2_settlements`、餘額完全相同；所有快照仍可解碼；所有 `request_digest` 不變。

### 4.7 權限

| 角色 | 新增權限 | 理由 |
| --- | --- | --- |
| 應用程式角色 | `jb2_api_observations`：SELECT、INSERT、UPDATE（不給 DELETE） | 取得與刷新；就地更新即可，無需刪除 |
| 清除專用角色（新，建議） | `jb2_api_observations`：UPDATE；對其他表無權限 | 到期清除不需要應用程式帳號，保持應用程式角色最小權限 |
| 遷移專用角色（暫時） | 第三節 #2–#5 的 UPDATE | 只在一次性遷移期間存在 |

原有「應用程式從不 DELETE、不改 DDL」的原則維持不變。

### 4.8 對解碼、審核、重播、digest、冪等與帳務的影響

| 面向 | 影響 | 處理 |
| --- | --- | --- |
| JSON 解碼 | 秒數被拿掉的 APPROVED 快照無法建構 | 新增 `DurationVerdict`，解碼相容新舊兩種格式 |
| 審核規則 | `_duration_passes` 需讀結論而非秒數 | 同一套 60–720 規則只在取得觀測當下執行一次，結論寫入快照 |
| 重播 | `jb2_decisions` 重播回傳原始 `result_json`；改寫後仍須可解碼 | 遷移後重播測試；決定鍵含修訂，過期後不會再被重送 |
| digest | `request_digest` 儲存後不變，只在同一決定鍵再次出現時比對 | 遷移不改 digest；測試證明重播仍通過 |
| 冪等 | `auto-review:<影片>:<修訂>` 在修訂前進後不可能再次寫入 | 清除只處理較舊資料（建議至少 5 日以上），避開進行中的重播視窗 |
| 帳務 | 帳本、請求、結算、餘額不含 API 資料 | 遷移前後比對，測試保證完全相同 |
| 健康契約 | 若新增「過期資料未清除」問題碼，會改變 `readiness` 輸出 | 見第七節，需 Owner 同意 |

## 五、「停自動核准」與「停 API 取得／保存」各自解決什麼

| | 停自動核准（`JUKEBOX_AUTO_REVIEW=off`） | 停 API 取得與保存 |
| --- | --- | --- |
| 做了什麼 | 不再查詢 API，所有案件走 Owner 人工審核 | 完全不呼叫 API，也不保存任何來自 API 的值 |
| 解決 | 之後**不會再有新的 API 資料**進入資料庫；也不再消耗配額 | 同左，且連「查了不存」的疑慮也不存在 |
| 不解決 | **已存的 API 資料仍在**，仍然會過期，仍需清除或整理 | 同左；已存的資料仍需整理 |
| 代價 | 每筆都要人工；卡片的「補時長」會變成日常動作 | 同左 |
| 備註 | 目前預設就是這個狀態 | 「查了不存」是否可行，取決於第二節問題 2 |

兩者**都不能**取代清除既有資料；它們只是阻止新資料累積。

## 六、驗證計畫（實作後使用可丟棄 PG17／18）

1. 寫入後掃描**所有**資料表的 JSON 與文字欄位，確認頻道 ID、秒數與取得時間只出現在專用表。
2. 到期清除：數值設 NULL、`purged_at` 設定；重跑冪等；與管線同時執行不死鎖、不重發。
3. 解碼相容：舊格式與新格式快照皆可解碼；`_append_review_revision` 不誤判。
4. 重播：遷移前取得的決定，遷移後重送仍回傳原結果。
5. 還原模擬：載入含過期資料的傾印，啟動前未清除時自動審核**拒絕使用**，清除後才恢復。
6. 帳務不變：帳本、請求、結算、餘額在遷移前後逐列相同；`evaluate_ledger_integrity` 通過。
7. 權限：應用程式角色不能 UPDATE 歷史表；清除角色不能碰帳本。
8. 並行：清除與結算同時進行不重複發獎。
9. PG17 與 PG18 皆執行，TLS 開啟；列出 skip 與通過。

## 七、需要 Owner 決定的取捨與我的建議

| # | 問題 | 選項 | 建議與理由 |
| --- | --- | --- | --- |
| 1 | 要不要先問 Google | A. 先問再做；B. 先做保守設計，同時詢問 | **B**。保守設計在任何答覆下都不吃虧；詢問草稿見第八節 |
| 2 | 備份保留期限 | A. 不超過 30 日；B. 較長，但還原後強制先清除 | 優先 **A**；若做不到，用 B，並承認備份檔本身仍含舊資料 |
| 3 | 逾期未清除時 | A. 只讓自動審核拒絕使用過期資料；B. 另外讓 `readiness` 回報 not ready | **A**。B 會改變健康契約與結束碼，除非 Owner 同意，否則不動 |
| 4 | 清除與刷新策略 | A. 只清除，不刷新；B. 只刷新等待中的案件；C. 全部定期刷新 | **B**。最省配額，且不保留已不需要的資料 |
| 5 | 清除工作的權限 | A. 專用清除角色；B. 給應用程式角色 DELETE | **A**。維持應用程式最小權限 |
| 6 | 既有歷史資料是否改寫 | A. 改寫（需臨時 UPDATE 權限）；B. 不改寫，只停止新增 | 若正式資料庫尚無 API 資料，**B 即可**；若已有，才需要 A |
| 7 | 影片被刪除後既有獎勵 | 保留（建議）或撤銷 | **保留**。本設計不新增任何自動撤獎或補發機制；若要改變，屬於產品規則變更，需另案討論 |

## 八、可向官方詢問的精準問題（草稿，**不代為寄送**）

> We operate a Discord bot that rewards a user for sharing a link to a YouTube video, and we use `videos.list` (contentDetails.duration, snippet.channelId, status) server-side only to check eligibility. We would like to confirm our retention approach under the YouTube API Services Developer Policies, section III.E.4 (Refreshing, Storing, and Displaying API Data):
>
> 1. When we store a video ID that the user supplied in the shared URL, and a channel ID returned by the API, solely to link a reward record to the video, are those identifiers subject to the 30-calendar-day refresh-or-delete requirement for non-authorized data?
> 2. If we compute from `contentDetails.duration` a purely internal boolean (for example, "full duration within our allowed range, evaluated on date D") and keep only that boolean and the date, is that stored result considered API Data or "new or derived data or metrics" under III.E.4 (item 8), and may it be retained beyond 30 days?
> 3. May we retain our own record that "a moderator approved submission X on date D" without any API-derived values?
> 4. Do backups that contain API Data need to be deleted or refreshed within 30 days, or is deleting expired data immediately after a restore acceptable?
> 5. Can we keep a record that a channel matched an entry in our own allowlist (by allowlist entry number) without storing the API-returned channel ID?

## 九、距離實作還缺什麼

- Owner 對第七節的決定；若選 A（改寫既有資料）還需要臨時 UPDATE 權限的核准。
- migration 檔（新表、權限）、`DurationVerdict` 與解碼相容、清除工具、啟動前清除、還原手冊步驟、第六節的測試。
- 以上都**尚未**核准或動工；正式 API 保存契約與 migration 仍須經 Owner 核准。
