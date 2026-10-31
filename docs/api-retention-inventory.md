# S8 唯讀盤點：正式資料庫是否已有 YouTube API 資料

狀態：**只是盤點，不查寫正式庫、不改任何歷史。** 本文件的 SQL 已在可丟棄 PostgreSQL 17 上，以唯讀角色對「含舊格式 API 資料的測試資料庫」執行驗證（結果見最後一節）；我沒有對正式庫執行，也沒有正式庫存取。

## 執行身分與方式

| 項目 | 內容 |
| --- | --- |
| 誰執行 | Owner 或 VPS 夥伴 |
| 資料庫身分 | **唯讀登入角色**（`docs/deploy/postgres-setup.sql` 的 `jukebox101_readonly_role` 成員）。不要用應用程式角色、遷移角色或管理者帳號 |
| 連線 | 與 Bot 相同的主機與 `verify-full` TLS；舊的 Session Pooler 或直連皆可，只要是唯讀角色 |
| 交易 | SQL 以 `BEGIN READ ONLY` 開頭、`ROLLBACK` 結尾，無法寫入 |
| 成本 | 每個表一次全表掃描（`jb2_*` 表目前規模很小）；請在非尖峰時段執行一次 |
| 輸出 | **只有計數與日期**，沒有任何 JSON 內容、頻道 ID、影片 ID、使用者 ID、秒數或秘密。輸出可以直接貼回對話 |

## SQL（原樣執行，不需要修改）

```sql
BEGIN READ ONLY;
SET LOCAL search_path = jukebox101;

-- A. 這個資料庫的狀態：是否已套用 0004、是否已有含「結論」的新格式列
SELECT to_regclass('jukebox101.jb2_api_observations') IS NOT NULL AS migration_0004_applied,
       (SELECT count(*) FROM jb2_videos WHERE review_json::text LIKE '%duration_verdict%') AS videos_with_new_format;

-- B. 舊格式：API 來源標籤（含秒數的時長證據）散在六處的哪裡、各有多少列
SELECT 'jb2_videos.review_json' AS location, count(*) FILTER (WHERE review_json::text LIKE '%youtube-data-api-v3:videos.list@%') AS api_rows, count(*) AS total_rows FROM jb2_videos
UNION ALL SELECT 'jb2_review_history.review_json', count(*) FILTER (WHERE review_json::text LIKE '%youtube-data-api-v3:videos.list@%'), count(*) FROM jb2_review_history
UNION ALL SELECT 'jb2_evidence.evidence_json', count(*) FILTER (WHERE evidence_json::text LIKE '%youtube-data-api-v3:videos.list@%'), count(*) FROM jb2_evidence
UNION ALL SELECT 'jb2_decisions.result_json', count(*) FILTER (WHERE result_json::text LIKE '%youtube-data-api-v3:videos.list@%'), count(*) FROM jb2_decisions
UNION ALL SELECT 'jb2_receipts.receipt_json', count(*) FILTER (WHERE receipt_json::text LIKE '%youtube-data-api-v3:videos.list@%'), count(*) FROM jb2_receipts
UNION ALL SELECT 'jb2_review_history.decision_reason (channel/time text)', count(*) FILTER (WHERE decision_reason LIKE 'auto-review policy=%channel=%'), count(*) FROM jb2_review_history
UNION ALL SELECT 'jb2_decisions.decision_reason (channel/time text)', count(*) FILTER (WHERE decision_reason LIKE 'auto-review policy=%channel=%'), count(*) FROM jb2_decisions;

-- C. 受影響的規模與時間範圍（只有數量與日期）
SELECT count(DISTINCT video_id) AS videos_with_api_history,
       min(created_at)::date AS earliest_entry,
       max(created_at)::date AS latest_entry,
       (now()::date - min(created_at)::date) AS oldest_age_days
FROM jb2_review_history WHERE review_json::text LIKE '%youtube-data-api-v3:videos.list@%';

-- D. 自動審核是否真的跑過（以系統身分 0 的決定數估計）
SELECT count(*) AS automatic_decisions, min(created_at)::date AS first_automatic, max(created_at)::date AS last_automatic
FROM jb2_review_history WHERE actor_id = 0;
ROLLBACK;
```

## 怎麼解讀（不代表任何已核准的處理）

| 結果 | 意義 | 下一步 |
| --- | --- | --- |
| B 的 `api_rows` 全為 0，D 為 0 | 從未開啟過自動審核；沒有舊格式 API 資料 | S8 整段不需要；可直接按上線順序進行 |
| B 有大於 0 的列 | 舊格式 API 值散在永久表。**其中最舊的一筆已存在多久**是政策問題的依據（C 的 `oldest_age_days`，政策上限 30 天） | 不做任何改寫。回報給 Owner，由 Owner 決定是否另案核准整理（見 `api-retention-release.md` 第八節）。**在此之前，不要啟用 `JUKEBOX_AUTO_REVIEW`** |
| `migration_0004_applied` 為 true 但 `videos_with_new_format` 為 0 | 已套用 migration，尚未開啟寫入開關 | 正常 |
| `videos_with_new_format` 大於 0 | 已寫出新格式，**最低可回滾版本是含本變更的版本** | 不可退回舊程式 |

限制：Owner 自己輸入的時長來源文字理論上也可能包含標籤字串，所以 B 是「上限」不是精確值；不影響「是否為 0」的判斷。

## 已驗證的範圍

在可丟棄 PG17 建立兩個資料庫：（1）關閉保存開關下以自動審核寫入一支影片（舊格式）；（2）開啟開關的對照組。以唯讀角色執行上列 SQL：（1）B 在 `jb2_videos`、`jb2_review_history`、`jb2_evidence`、`jb2_decisions` 的 `api_rows` 均大於 0（各 1），兩個理由文字欄位也各 1，`jb2_receipts` 為 0（只有一位投稿者；第二位投稿同一支影片時回執才會帶著它），C 回傳 1 支影片與日期，D 回傳 1 筆自動決定；（2）開啟開關的對照組，B 全為 0，`migration_0004_applied` 與 `videos_with_new_format` 為 true 與 1。輸出欄位只有計數與日期。唯讀角色嘗試 `INSERT` 被拒（`ReadOnlySqlTransaction`）。尚未對正式庫執行。
