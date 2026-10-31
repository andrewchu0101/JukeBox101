# API 資料保存：可審查的 migration 與實作草稿

狀態：**草稿，等 Owner 核准。** 沒有任何 migration 檔案、沒有程式變更、沒有碰正式資料。草稿 SQL 刻意放在本文件內的程式碼區塊，**不**放在 `bot/database/postgres/`：遷移探索規則要求該目錄只有連號的 `NNNN_name.sql`，放進去就會被自動套用。

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
    expires_at       TIMESTAMPTZ NOT NULL,                 -- observed_at + 14 days (policy ceiling is 30)
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

**草稿 DDL 已在可丟棄的 PostgreSQL 17.11 與 18.6 驗證**（以管理者身分套用在已完成既有三個 migration 的測試資料庫；沒有經過遷移執行器，也沒有套用到任何正式資料庫）：語法有效；四種違規資料（保存超過 30 天、到期早於取得、清除後仍留有數值、有清除時間卻沒有原因）都被 `CHECK` 擋下；14 天內的合法資料可寫入；應用程式角色可 `UPDATE`（清除）、不能 `DELETE`、不能 `UPDATE` 歷史表。這證明草稿本身可行，不代表已核准。

`CHECK` 約束讓資料庫本身擋下「保存超過 30 天」與「清除後仍留有數值」，不依賴程式正確。清除用 `UPDATE` 把數值設為 NULL，不需要 `DELETE`，所以**不需要新增清除專用角色**（比上一版設計更簡單）。

### 1.2 資料格式變更（程式內，不是 DDL）

| 變更 | 說明 |
| --- | --- |
| 新契約 `DurationVerdict` | 欄位：結論（`PASS`／`REJECT`）、來源類別（`api`／`owner`）。註冊進編解碼器的契約表 |
| `ReviewSnapshot` 新增選填欄位 `duration_verdict` | 預設 `None`。`PENDING_REVIEW`／`APPROVED` 的「時長通過」規則改為接受「通過的 Owner 時長證據」**或**「通過的 API 結論」 |
| 保留給系統的來源標籤 | Owner 輸入的來源文字若以 `youtube-data-api-v3:videos.list@` 開頭即拒絕，避免人工資料被誤判為 API 資料 |
| 理由文字 | 自動審核理由改為 `auto-review policy=… reason=…`，不含頻道 ID 與取得時間 |
| 寫入路徑 | 自動審核不再把含秒數的 `DurationEvidence` 放進快照；API 原始值只寫入觀測表。`jb2_evidence`、`jb2_review_history`、`jb2_decisions`、`jb2_receipts` 從此只看到結論 |
| Owner 自述的時長 | **完全不變**：仍用 `DurationEvidence`，含秒數與來源，保留原樣 |

## 二、清除與刷新如何自動執行、失敗時如何阻止採信

### 2.1 自動執行（不新增付費服務）

| 層 | 做法 | 說明 |
| --- | --- | --- |
| 主要 | Bot 內建的巡檢迴圈（既有的 `_auto_sweep`）每日清除一次，**並在 Bot 啟動、開始接受工作之前先清除** | 不需要夥伴額外設定排程 |
| 後備（選用） | 主機的排程執行 `python -m bot.api_retention purge --apply` | 即使 Bot 停止也能清除；是否採用要看夥伴能否設定排程 |
| 刷新 | 只刷新**仍在等待決定**的案件，第 10 天一次，最多 50 個影片一個請求；已完成案件完成後 1 天內清除、不再呼叫 API | 配額幾乎可忽略 |
| 時間 | 全部以資料庫時間（`statement_timestamp()`）判斷，不用 VPS 時鐘 | 避免時鐘偏差讓資料多留 |
| 預設 | CLI 預設預覽，需明確 `--apply` | 與既有 `settlement-switch` 一致 |

### 2.2 失敗時阻止採信過期資料（不改既有 readiness 契約）

1. **採信條件寫進查詢**：所有讀取觀測值的地方都帶 `purged_at IS NULL AND expires_at > statement_timestamp()`。過期或已清除的列視為「不存在」，不會被當成依據，**即使清除工作沒有成功**。
2. **審核結論本身也會過期（預設嚴格）**：由 API 得出的 `DurationVerdict` 跟著觀測到期。`PENDING_REVIEW` 的案件若結論已過期，核准前必須重新取得（第 10 天已嘗試刷新）；API 無法取得時，Owner 可用卡片的「補時長」輸入人工時長（人工資料不受影響）。這**會改變核准的前置條件**，屬產品規則，需要你核准；若政策答覆認為內部結論可長期保存，再放寬。
3. **逾期逐級處理**（逾期 = 過了 `expires_at` 仍未清除）：

   | 逾期 | 行為 |
   | --- | --- |
   | ≥ 1 天 | Owner 卡片頂端顯示警示「API 資料清除逾期 N 筆」，Bot 記 ERROR |
   | ≥ 3 天 | 管線**停止新的 API 查詢與保存**，新案件轉人工 |
   | ≥ 7 天 | 警示標為緊急，需手動執行 `purge --apply` |
   | ≥ 12 天 | 建議關閉 `JUKEBOX_AUTO_REVIEW` 並手動清除（距 30 天上限剩 4 天以內） |

4. **觀察與通知**：每次清除寫入 `jb2_retention_runs`；唯讀的 `python -m bot.api_retention status` 回報逾期列數與最近成功時間；卡片警示直接查資料表，與清除工作是否存活無關。是否另外 DM Owner 屬新行為，預設不做。
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

## 四、分階段上線（每階段都需要你明確核准）

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
- 建議：dump 保留 ≤ 14 天，或 dump 時排除觀測表資料（`--exclude-table-data=jb2_api_observations`）；後者只在舊格式資料整理完成或確認從未存在之後才成立。這些天數是安全估算，**不是官方認可**。

**產品與營運決定**
- 結論過期是否阻擋核准（第 2.2 節第 2 點）。
- 是否採用主機排程作為後備清除。
- 是否在逾期時另外 DM Owner。

## 六、實作後的驗證計畫（可丟棄 PG17／18，TLS 開啟）

1. 寫入後掃描**所有**資料表的 JSON 與文字欄位，確認頻道 ID、秒數與取得時間只出現在觀測表。
2. 到期清除：數值設 NULL、`purged_at` 設定；重跑冪等；與管線和結算同時執行不死鎖、不重發。
3. `CHECK` 約束：嘗試寫入超過 30 天或清除後仍有數值，資料庫拒絕。
4. 新舊格式快照都可解碼；`_append_review_revision` 不誤判。
5. 重播：遷移前取得的決定，遷移後重送仍回傳原結果。
6. 還原模擬：載入含過期列的傾印，未清除前自動審核拒絕使用，清除後才恢復。
7. 帳務不變：帳本、請求、結算、餘額逐列相同；`evaluate_ledger_integrity` 通過。
8. 權限：應用程式角色不能 UPDATE 歷史表；唯讀角色不能寫。
9. 回滾演練：寫入旗標開啟後關閉，新程式仍可讀所有資料。
