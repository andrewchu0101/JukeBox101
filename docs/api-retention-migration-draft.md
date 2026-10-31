# API 資料保存：可審查的 migration 與實作草稿

狀態：**草稿，等 Owner 核准。** 沒有任何 migration 檔案、沒有程式變更、沒有碰正式資料。**新增兩張表只是第四節的步驟 S2，不等於完成**；完整的可審查步驟見 4.1。草稿 SQL 刻意放在本文件內的程式碼區塊，**不**放在 `bot/database/postgres/`：遷移探索規則要求該目錄只有連號的 `NNNN_name.sql`，放進去就會被自動套用。

設計背景、保存位置清單與政策原文位置見 [API 資料生命週期設計](api-data-lifecycle-design.md)。本文件只集中「要核准什麼」。

## 一、要核准的範圍

### 1.1 資料庫變更（一個新 migration，只新增、不改既有表）

| 項目 | 內容 | 風險 |
| --- | --- | --- |
| `jb2_api_observations` | 每支影片一列的短期表：API 取得的時長、頻道 ID、取得與到期時間、清除時間與原因 | 新表；不影響既有查詢 |
| `jb2_retention_runs` | 清除工作每次執行的紀錄（開始、結束、到期列數、清除列數、失敗數、錯誤類別） | 新表 |
| 權限 | 應用程式角色：觀測表 SELECT／INSERT／UPDATE（**不給 DELETE**）、執行紀錄 SELECT／INSERT；唯讀角色 SELECT | 沿用既有最小權限原則 |
| **不做** | 不 ALTER `jb2_review_history`、`jb2_evidence`、`jb2_decisions`、`jb2_receipts`；不改帳本與結算表 | — |

草稿 DDL（**未執行、未放入遷移目錄**）：

```sql
-- DRAFT 0004: short-lived store for values obtained from the YouTube Data API.
CREATE TABLE jb2_api_observations (
    video_id         VARCHAR(11) COLLATE "C" NOT NULL PRIMARY KEY REFERENCES jb2_videos (video_id),
    observed_at      TIMESTAMPTZ NOT NULL,
    expires_at       TIMESTAMPTZ NOT NULL,                 -- observed_at + RETENTION_DAYS (default 14); the CHECK below only bounds this column
    duration_seconds INTEGER,                              -- NULL after purge
    channel_id       VARCHAR(64),                          -- NULL after purge
    purged_at        TIMESTAMPTZ,
    purge_reason     VARCHAR(32),                          -- expired | video_unavailable | refreshed_away | restore
    CONSTRAINT chk_jb2_api_observation_window CHECK (expires_at > observed_at AND expires_at <= observed_at + INTERVAL '30 days'),
    CONSTRAINT chk_jb2_api_observation_purged CHECK ((purged_at IS NULL) = (purge_reason IS NULL)),
    CONSTRAINT chk_jb2_api_observation_values CHECK (purged_at IS NULL OR (duration_seconds IS NULL AND channel_id IS NULL))
);
CREATE INDEX ix_jb2_api_observations_expiry ON jb2_api_observations (expires_at) WHERE purged_at IS NULL;

CREATE TABLE jb2_retention_runs (
    run_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    finished_at    TIMESTAMPTZ,
    expired_rows   INTEGER NOT NULL DEFAULT 0,
    purged_rows    INTEGER NOT NULL DEFAULT 0,
    failed_rows    INTEGER NOT NULL DEFAULT 0,
    error_class    VARCHAR(64)
);

GRANT SELECT, INSERT, UPDATE ON jb2_api_observations TO jukebox101_app_role;
GRANT SELECT, INSERT         ON jb2_retention_runs   TO jukebox101_app_role;
GRANT UPDATE (finished_at, expired_rows, purged_rows, failed_rows, error_class) ON jb2_retention_runs TO jukebox101_app_role;
GRANT SELECT ON jb2_api_observations, jb2_retention_runs TO jukebox101_readonly_role;
```

**草稿 DDL 已在可丟棄的 PostgreSQL 17.11 與 18.6 驗證**（以管理者身分套用在已完成既有三個 migration 的測試資料庫；沒有經過遷移執行器，也沒有套用到任何正式資料庫）：語法有效；四種違規**寫入**（到期時間超過取得後 30 天、到期早於取得、清除後仍留有數值、有清除時間卻沒有原因）都被 `CHECK` 擋下；到期設為 14 天的合法資料可寫入；應用程式角色可 `UPDATE`（清除）、不能 `DELETE`、不能 `UPDATE` 歷史表。這證明草稿本身可行，不代表已核准。

`CHECK` 約束**只限制欄位值**：它讓資料庫拒絕寫入 `expires_at` 超過取得時間 30 天的列、以及已標記清除卻仍有數值的列。**它不會、也無法自動清除已到期的資料**——到期後列仍原封不動留在表裡，直到背景工作把數值設為 NULL。下一節把「欄位限制、讀取拒絕、實際清除、失敗補救」四層分開說明。清除用 `UPDATE` 把數值設為 NULL，不需要 `DELETE`，所以**不需要新增清除專用角色**（比上一版設計更簡單）。

### 1.2 資料格式變更（程式內，不是 DDL）

| 變更 | 說明 |
| --- | --- |
| 新契約 `DurationVerdict` | 欄位：結論（`PASS`／`REJECT`）、來源類別（`api`／`owner`）。註冊進編解碼器的契約表 |
| `ReviewSnapshot` 新增選填欄位 `duration_verdict` | 預設 `None`。`PENDING_REVIEW`／`APPROVED` 的「時長通過」規則改為接受「通過的 Owner 時長證據」**或**「通過的 API 結論」 |
| 保留給系統的來源標籤 | Owner 輸入的來源文字若以 `youtube-data-api-v3:videos.list@` 開頭即拒絕，避免人工資料被誤判為 API 資料 |
| 理由文字 | 自動審核理由改為 `auto-review policy=… reason=…`，不含頻道 ID 與取得時間 |
| 寫入路徑 | 自動審核不再把含秒數的 `DurationEvidence` 放進快照；API 原始值只寫入觀測表。`jb2_evidence`、`jb2_review_history`、`jb2_decisions`、`jb2_receipts` 從此只看到結論 |
| Owner 自述的時長 | **完全不變**：仍用 `DurationEvidence`，含秒數與來源，保留原樣 |

### 1.3 一組一致的預設參數（全部是建議預設，待核准；其他天數都由此推得）

| 參數 | 預設值 | 角色 |
| --- | --- | --- |
| `RETENTION_DAYS`（正常保存） | **14 天** | `expires_at = observed_at + 14 天`；程式寫入時使用 |
| 資料庫 `CHECK` 上限 | **30 天** | 護欄：擋下程式錯誤寫出超過政策上限的到期時間。**不是預設值**，也不負責清除 |
| 刷新（僅等待中案件） | 第 **10** 天一次 | 到期前留 4 天重試；失敗不延長到期 |
| 已完成案件 | 完成後 **1 天內**清除，不為保留歷史而刷新 | 少打 API |
| 清除頻率 | Bot 每日一次，另在啟動接受工作前先清 | 見 2.1 |
| 逾期警示 | 逾期（超過 `expires_at` 仍未清除）**1／3／7／12 天** | 12 天逾期＝取得後第 26 天，距 30 天上限剩 4 天 |
| 手動 dump 保留 `R` | **≤ 14 天** | 正常清除成功且已知副本都納入時的估算：14＋1＋14＝29 天。**不是硬性保證**：清除失敗、長期停機或未知副本會超過 |

先前文件出現的「14 天」是預設保存天數，「30 天」是 `CHECK` 護欄與政策上限，兩者並不矛盾；若核准時改變預設天數，只改 `RETENTION_DAYS`，其餘由上表推得。這些天數是安全估算，**不是官方認可**，也不是硬性上限；硬性機制只有第 2.0 節的 ①② 兩層。

## 二、清除與刷新如何自動執行、失敗時如何阻止採信

### 2.0 四層防線（各自負責不同的事，不可互相替代）

| 層 | 做什麼 | **不**做什麼 |
| --- | --- | --- |
| ① 期限欄位限制 | `CHECK`：`expires_at` 必須晚於取得時間且不超過 30 天；已標記清除的列不得留有數值 | 不會清除任何資料；到期的列照舊留著 |
| ② 所有讀取拒絕過期值 | 每個讀取觀測值的查詢都帶 `purged_at IS NULL AND expires_at > statement_timestamp()`，過期列視為不存在 | 不會刪除資料；副本仍在表裡、備份裡 |
| ③ 背景工作實際清空 | Bot 每日與啟動時把到期列的 `duration_seconds`、`channel_id` 設為 NULL 並標記 `purged_at`；P4 另處理舊格式散在歷史 JSON 的副本 | 清除失敗時不能保證資料消失，所以仍要靠 ② |
| ④ 失敗與長期停機補救 | 逾期警示（卡片）與唯讀 `status`；Bot 重啟先清除；還原備份後先清除才可啟用自動審核；必要時手動 `purge --apply` 或關閉自動審核 | 不自動刪除人工資料、不撤銷決定或獎勵 |

### 2.1 自動執行（不新增付費服務）

| 層 | 做法 | 說明 |
| --- | --- | --- |
| 主要 | Bot 內建的巡檢迴圈（既有的 `_auto_sweep`）每日清除一次，**並在 Bot 啟動、開始接受工作之前先清除** | 不需要夥伴額外設定排程 |
| 後備（選用） | 主機的排程執行 `python -m bot.api_retention purge --apply` | 即使 Bot 停止也能清除；是否採用要看夥伴能否設定排程 |
| 刷新 | 只刷新**仍在等待決定**的案件，第 10 天一次，最多 50 個影片一個請求；已完成案件完成後 1 天內清除、不再呼叫 API | 配額幾乎可忽略 |
| 時間 | 全部以資料庫時間（`statement_timestamp()`）判斷，不用 VPS 時鐘 | 避免時鐘偏差讓資料多留 |
| 預設 | **只有手動 CLI 預設預覽**，需明確 `--apply` 才執行，與既有 `settlement-switch` 一致。**背景清除不是預覽**：Bot 的每日清除與啟動前清除直接執行，因為清除只把已到期的數值設為 NULL（不碰人工資料、帳本、回執），且只在 Bot 開啟 `JUKEBOX_API_RETENTION_PURGE`（草案名稱，尚未存在）時；若要關掉自動清除，到期資料仍會因第 2.2 節的查詢條件被視為不存在 | 預覽是給人工操作的保護，不應讓到期資料因為「只預覽」而留著 |

### 2.2 失敗時阻止採信過期資料（不改既有 readiness 契約）

1. **採信條件寫進查詢**：所有讀取觀測值的地方都帶 `purged_at IS NULL AND expires_at > statement_timestamp()`。過期或已清除的列視為「不存在」，不會被當成依據，**即使清除工作沒有成功**。
2. **只有「缺乏有效 API 證據」的待處理案件受影響**（預設嚴格，等政策答覆；需要你核准，**本輪不實作**）：
   - 受影響：處於 `PENDING_REVIEW`、**唯一**的時長依據是已過期的 API 結論的案件。核准前必須重新驗證（第 10 天已嘗試刷新）；API 取不到時，Owner 可用卡片的「補時長」補人工證據。
   - **不受 API 保存期限影響**：Owner 自述的時長證據（`DurationEvidence`，含秒數與來源）不因 API 保存期限而失效或被清除；它仍適用**既有**的重審、衝突與修訂規則（例如較新的證據、與 API 結論衝突時的處理照舊，本草稿不改）。已核准（`APPROVED`）及之後的決定、已發放的獎勵、帳本、回執與冪等紀錄**不會被自動撤銷、降級或補發**。
   - 沒有新增狀態：「等待重新驗證」沿用既有的 `PENDING_VALIDATION` 與重新檢查機制（`reexamination_pending`）。

   **與現有狀態機的具體差異**（若核准才實作，目前程式未動）：

   | # | 現況 | 若核准後的差異 |
   | --- | --- | --- |
   | 1 | `ReviewSnapshot.duration` 只能是含秒數的 `DurationEvidence`，API 與人工同一種，永久存在歷史 JSON | 新增選填 `duration_verdict`（結論，無秒數）；API 路徑只寫結論；人工路徑不變 |
   | 2 | `review.py` 的不變條件：寫入 `PENDING_REVIEW`／`APPROVED` 的任何新修訂都要求 `_duration_passes` | 改為：**進入** `PENDING_REVIEW`／`APPROVED` 的轉換才檢查時長依據（人工證據，或**未過期**的 API 結論）。已在 `APPROVED` 的快照再寫新修訂（版本確認、結算）時，沿用既有結論、**不重新檢查期限**，否則過期後版本確認會被錯誤擋下 |
   | 3 | 只要快照有通過的時長就可由 Owner 核准 | `PENDING_REVIEW` → `APPROVED` 的確認多一個檢查：只有 API 結論且已過期 → 拒絕並提示「需重新驗證或補人工時長」，案件維持待處理 |
   | 4 | 沒有任何「依期限改變案件」的流程 | 期限只在上述兩個轉換點被讀取；清除工作**不改變任何案件狀態** |

   差異 2 是最容易出錯的一項（舊結論過期會連帶擋住已核准案件的後續操作），所以必須先有測試：已核准、API 結論已過期的案件，仍能完成版本確認與結算。
3. **逾期逐級處理**（逾期 = 過了 `expires_at` 仍未清除）：

   | 逾期 | 行為 |
   | --- | --- |
   | ≥ 1 天 | Owner 卡片頂端顯示警示「API 資料清除逾期 N 筆」，Bot 記 ERROR |
   | ≥ 3 天 | 管線**停止新的 API 查詢與保存**，新案件轉人工 |
   | ≥ 7 天 | 警示標為緊急，需手動執行 `purge --apply` |
   | ≥ 12 天 | 建議關閉 `JUKEBOX_AUTO_REVIEW` 並手動清除（距 30 天上限剩 4 天以內） |

4. **觀察與通知**：每次清除寫入 `jb2_retention_runs`；唯讀的 `python -m bot.api_retention status` 回報逾期列數與最近成功時間；卡片警示直接查資料表，與清除工作是否存活無關。**不新增逾期 DM**（Owner 已裁決）；只沿用卡片警示與唯讀 `status`。
5. **啟動前清除**：Bot 長期停機後，第一件事是清除。還原備份後同樣必須先清除才能啟用自動審核（還原手冊的強制步驟）。
6. `readiness`、Compose healthcheck 與結束碼**不改**。

## 三、與歷史、回執、冪等、人工來源的相容性

| 面向 | 相容做法 | 風險與處理 |
| --- | --- | --- |
| 解碼（編解碼器） | 重建物件時是 `契約(**欄位)`：**新程式讀舊資料沒問題**（缺的選填欄位用預設值）；**舊程式讀到含新欄位的資料會拋錯** | **回滾風險**：新格式寫入後，不能只把程式退回舊版。做法：分階段部署（見第四節），先讓新程式能讀、最後才開始寫新格式；回退時關閉寫入旗標，不退二進位 |
| 歷史 `jb2_review_history` | 只增不改。新修訂只寫結論；既有列不動（除非另案核准整理） | `_append_review_revision` 會比對同一修訂的新舊內容：比對前把 API 來源的時長證據**正規化**成結論，避免誤判矛盾 |
| 回執 `jb2_receipts` | 受理當下的結果含影片當時的快照；新回執只含結論。重播時原樣解碼 | 舊回執含秒數的情況仍可解碼 |
| 冪等 `jb2_decisions` | `request_digest` 儲存後不變；只在**同一決定鍵**再次出現時比對。決定鍵含修訂，修訂前進後不會再被重送 | 清除與整理只處理較舊資料（建議至少 5 天），避開進行中的重播視窗 |
| 人工來源 | `origin = owner` 的列與所有 `DurationEvidence` 原樣保留；來源分類看快照內的 `source`，**不看歷史列的 actor**（Owner 的後續決定會把含 API 時長的快照再寫一次） | 保留標籤規則防止誤分類 |
| 帳務 | 帳本、請求、結算、餘額不含 API 資料，**完全不動**；不新增自動撤獎或補發 | 測試：遷移前後逐列比對 |

## 四、實作步驟與分階段上線

### 4.1 實作拆成可審查步驟（每步一個獨立變更集，各自有測試；依序，前一步通過才進下一步）

「兩張表建好」只是 S2。以下全部完成才算實作完成。預期涉及的檔案是依現有結構的推估，實作時以實際為準。

| 步驟 | 內容 | 預期涉及 | 必備測試（可丟棄 PG17／18） | 依賴 |
| --- | --- | --- | --- | --- |
| S1 | 契約與編解碼：新增 `DurationVerdict`、`ReviewSnapshot.duration_verdict`（選填）；編解碼器能讀新舊兩種格式；**寫入旗標關閉** | `bot/review.py`、`bot/database/submission_codec.py`、契約註冊表 | 新舊快照都可解碼；舊程式讀新欄位會失敗的事實用測試記錄（確認需分階段）；重播遷移前的決定回傳原結果 | 無 |
| S2 | Migration：`jb2_api_observations`、`jb2_retention_runs`、`CHECK`、權限（App 無 `DELETE`、無歷史表 `UPDATE`） | `bot/database/postgres/0004_*.sql`（需 Owner 核准才放入） | 四種違規寫入被拒；到期列仍在表中；權限矩陣；全新資料庫與升級路徑都能套用 | S1 部署 |
| S3 | 寫入路徑：自動審核把 API 原始值只寫觀測表，快照／證據／決定／回執／理由文字只存結論；Owner 自述時長路徑不變 | `bot/auto_pipeline.py`、`bot/database/auto_review_store.py`、`submission_*` | **掃描所有表的 JSON 與文字欄位**，頻道 ID、秒數、取得時間只出現在觀測表（涵蓋設計文件第三節的 #1–#6）；`_append_review_revision` 比對不誤判 | S2 套用、寫入旗標 |
| S4 | 讀取拒絕過期值：所有讀取觀測值的查詢帶 `purged_at IS NULL AND expires_at > statement_timestamp()` | 同 S3 的讀取點 | 把 `expires_at` 設為過去、清除工作**未執行**，確認拒絕採信（證明不依賴清除成功） | S3 |
| S5 | 背景清除：Bot 啟動接受工作前先清；巡檢迴圈每日清；手動 `python -m bot.api_retention purge`（預設預覽，`--apply` 才執行）與唯讀 `status` | 新模組 `bot/api_retention.py`、`bot/auto_pipeline.py` | 清除冪等；與管線、結算並行不死鎖不重發；清除途中失敗可重跑；`retention_runs` 紀錄 | S4 |
| S6 | 重啟與長期停機恢復：重啟後先清除再接工作；還原含過期列的傾印後，未清除前拒絕採信，清除後恢復；逾期警示與 `status` 逾期列數 | `bot/__main__.py`（啟動順序）、`bot/discord_owner_cards.py`（警示） | 模擬重啟中斷於清除中途；模擬還原；逾期 1／3／7／12 天的行為 | S5 |
| S7 | 到期案件處理（**需 Owner 核准的產品規則，見 2.2**）：進入待審／已核准才檢查期限；已核准案件的後續操作不受影響 | `bot/review.py`、`bot/owner_review.py` | 已核准且 API 結論過期的案件仍可版本確認與結算；只有 API 結論的待審案件被擋並提示補證據；人工時長仍適用既有重審與衝突規則 | S1、S3 |
| S8 | 既有資料整理（**僅在唯讀查詢證明正式庫已有 API 資料時**）：分批、預覽 digest、可重跑；需臨時 `UPDATE` 權限 | 一次性腳本 | 前後帳本逐列比對；`evaluate_ledger_integrity` 通過 | S3 上線、Owner 授權 |

回滾：S1 之後新格式一旦寫入，不能只退回舊二進位；關閉寫入旗標即可停止新寫入，而新程式仍可讀所有資料。

### 4.2 分階段上線（每階段都需要你明確核准）

| 階段 | 內容 | 可逆性 |
| --- | --- | --- |
| P0 | 你核准本草稿，並回答第五節的問題 | — |
| P1 | 部署「能讀新舊兩種格式」的程式，寫入旗標關閉 | 可退回 |
| P2 | 套用 migration（新表與權限，加法式）。**需要正式資料庫的變更授權** | 新表可保留不用 |
| P3 | 打開寫入旗標：新資料只寫結論＋觀測表 | 關旗標即可；已寫入的新格式需新程式才能讀 |
| P4 | 只在正式資料庫**已有 API 資料**時：一次性整理歷史（需臨時 UPDATE 權限、預覽 digest、分批、可重跑） | 整理後不可逆；前後帳務逐列比對 |
| P5 | 打開排程清除與逾期警示 | 可關閉 |

**P4 是否需要，先用唯讀查詢確認**（由 Owner 或夥伴用唯讀角色對正式庫執行；我沒有執行，也沒有正式庫存取）：

```sql
SELECT count(*) FROM jb2_review_history WHERE review_json::text LIKE '%youtube-data-api-v3:videos.list@%';
```

若為 0（從未開啟過自動審核），P4 整段省略。

## 五、尚待確認的事項（不假設答案）

**政策**（完整問題草稿見 [設計文件第八節](api-data-lifecycle-design.md)，不代為寄送）
1. 由 API 回傳、為了把投稿對應到影片而保存的頻道 ID／影片 ID，是否受 30 日規則約束？
2. 內部結論（例如「完整時長在範圍內」）是否算 API 資料或衍生資料？**本草稿不假設它必然豁免**，預設讓結論跟著到期；官方若答覆可長期保存，再放寬第 2.2 節第 2 點。
3. 備份中的副本是否需要在 30 天內消失？
4. 「Owner 在某日核准了投稿 X」這類純內部紀錄是否可無期限保存？

**備份與環境**（Supabase 已確認是 Free，無自動備份；以下需 Owner 與夥伴確認）
- VPS 或任何主機是否有 `pg_dump` 排程或手動 dump？Hostinger 是否啟用快照，且 dump 是否存放在上面？
- Owner 本機或雲端是否有資料庫匯出？
- Docker 日誌保留天數。
- 建議：dump 保留 ≤ 14 天（29 天估算只在清除成功且已知副本都納入時成立，見 1.3 節），或 dump 時排除觀測表資料（`--exclude-table-data=jb2_api_observations`）；後者只在舊格式資料整理完成或確認從未存在之後才成立。這些天數是安全估算，**不是官方認可**。

**產品與營運決定**
- 結論過期是否阻擋核准（第 2.2 節第 2 點）。
- 是否採用主機排程作為後備清除。

## 六、實作後的驗證計畫（可丟棄 PG17／18，TLS 開啟）

1. 寫入後掃描**所有**資料表的 JSON 與文字欄位，確認頻道 ID、秒數與取得時間只出現在觀測表。
2. 到期清除：數值設 NULL、`purged_at` 設定；重跑冪等；與管線和結算同時執行不死鎖、不重發。
3. `CHECK` 約束：嘗試寫入到期時間超過 30 天或清除後仍有數值，資料庫拒絕；另測**已到期但未清除的列仍在表中**（證明 CHECK 不負責清除），且所有讀取路徑都不採信它。
4. 新舊格式快照都可解碼；`_append_review_revision` 不誤判。
5. 重播：遷移前取得的決定，遷移後重送仍回傳原結果。
6. 還原模擬：載入含過期列的傾印，未清除前自動審核拒絕使用，清除後才恢復。
7. 帳務不變：帳本、請求、結算、餘額逐列相同；`evaluate_ledger_integrity` 通過。
8. 權限：應用程式角色不能 UPDATE 歷史表；唯讀角色不能寫。
9. 回滾演練：寫入旗標開啟後關閉，新程式仍可讀所有資料。
