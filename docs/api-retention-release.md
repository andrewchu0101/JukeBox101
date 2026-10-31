# API 資料保存：本次實作、上線順序與回滾

狀態：**已在本機實作並在可丟棄 PostgreSQL 17／18 驗證；尚未 push、合併、部署，也沒有碰任何正式資料庫或正式 Discord。** 設計理由與政策原文位置見 [API 資料生命週期設計](api-data-lifecycle-design.md)；先前的核准草稿見 [migration 草稿](api-retention-migration-draft.md)（本文件以實作為準，兩者不同處見第七節）。

## 一、做了什麼（一句話）

自動審核從 YouTube API 取得的時長與頻道 ID，**只存進一張短期表**；永久歷程（快照、歷史、證據、決定、回執、理由文字）只存「通過／未通過」的結論。到期的數值由背景工作**真的設為 NULL**。到期的值一律不被採信，即使清除失敗。

## 二、四層機制（各自負責的事不同）

| 層 | 實作 | 不負責 |
| --- | --- | --- |
| ① 欄位限制 | `0004` 的 `CHECK`：`expires_at` 晚於取得時間且不超過 30 天；已清除的列不得留有數值 | **不會清除任何資料**；到期的列留在表中直到工作清除 |
| ② 讀取拒絕過期 | 所有判斷「結論是否有效」的查詢都帶 `purged_at IS NULL AND expires_at > statement_timestamp()`（資料庫時間） | 不刪除資料 |
| ③ 實際清除 | `bot/database/api_observations.py` 的 `purge_pass`：把 `duration_seconds`、`channel_id` 設為 NULL 並標記原因；Bot 啟動時、之後每 24 小時一次；手動 `python -m bot.api_retention purge --apply` | — |
| ④ 失敗與停機補救 | 清除每次寫入 `jb2_retention_runs`；失敗會記錄類別並在下次巡檢（5 分鐘）重試；逾期 ≥1／3／7／12 天的卡片警示；≥3 天停止新的官方查詢；唯讀 `python -m bot.api_retention status` | 不自動撤銷決定、不發 DM |

預設參數：保存 `RETENTION_DAYS = 14` 天；資料庫上限護欄 30 天；清除頻率 24 小時；已決定（核准或未通過）的案件，數值不再需要，於同一個交易（自動決定）或下一次清除（Owner 決定）清空，不必等到期。

## 三、各種資料現在怎麼處理

| 資料 | 處理 |
| --- | --- |
| 自動審核的時長、頻道 ID | 只在 `jb2_api_observations`；自動決定的案件在**同一個交易**清空，Owner 決定的案件在下一次清除（最多一天）清空 |
| 快照／歷史／證據／決定／回執 | 只含 `DurationVerdict`（結論，無秒數、無頻道、無時間） |
| 自動審核理由文字 | `auto-review policy=… reason=…`，不含頻道 ID 與取得時間 |
| Owner 自己輸入的時長與來源 | **不受 API 保存期限影響**，原樣保存；仍適用既有的重審、衝突與修訂規則 |
| 帳本、請求、結算、餘額 | 完全不動 |
| 開關關閉（預設）時 | 行為與先前版本相同（仍寫含秒數的證據），完全不讀寫新表 |

## 四、案件狀態機的差異（S7，已實作）

| 情況 | 行為 |
| --- | --- |
| 待審案件，唯一的時長依據是**已過期**的 API 結論 | 不能再依該證據核准：預覽與確認都拒絕（`DurationEvidenceExpired`），案件維持待審；卡片顯示「官方資料已過期」、停用「確認通過」、開放「補時長」 |
| 同一案件，Owner 補了人工時長 | 人工證據不會過期，可核准；與 API 結論衝突時仍依既有規則（例如超出範圍即未通過） |
| 同一案件，系統巡檢 | 只對這類案件向 API **再查一次**；仍通過才刷新數值，不產生新修訂；不通過或查不到則不動，等 Owner |
| 已核准／已決定的案件 | 不檢查期限、不撤銷、不重發；後續版本確認與既有結算照常（有測試） |
| 沒有新增狀態 | 「等待重新驗證」就是「待審＋資料過期」 |

## 五、必要的變更清單

| 項目 | 內容 |
| --- | --- |
| Migration | `bot/database/postgres/0004_api_observations.sql`：新增 `jb2_api_observations`、`jb2_retention_runs`，加法式；應用程式角色對觀測表有 SELECT／INSERT／UPDATE（**沒有 DELETE**），對執行紀錄 SELECT／INSERT 與三個欄位的 UPDATE；唯讀角色 SELECT。需要既有的兩個群組角色（0002 已要求） |
| 設定 | 新增 `JUKEBOX_API_RETENTION`（`on`／`off`，預設 `off`，值無效則拒絕啟動）。**這是 Compose 的變更**：`compose.auto-review.yaml` 多一行 `JUKEBOX_API_RETENTION: ${JUKEBOX_API_RETENTION:-off}`，另有 `.env.example` 一行 |
| 新指令 | `python -m bot.api_retention status`（唯讀）、`purge`（預設預覽）、`purge --apply` |
| 不變 | `readiness`／`healthcheck` 契約與結束碼、TLS、帳本與結算規則、權限模型 |

## 六、上線順序與可回滾的最低版本

名詞：**R0** = 目前正式環境執行的版本（`origin/main`，PR #4 合併後）。**R1** = 含本變更的版本。

**關鍵事實（已用 R0 的編解碼器實測）：** R0 讀得懂沒有 `duration_verdict` 的快照（本版在值為空時不寫這個欄位，所以位元組與 R0 完全相同），但**讀到含 `duration_verdict` 的快照會失敗**（`ValueError: Unknown persisted representation`）。一旦開啟 `JUKEBOX_API_RETENTION=on` 並寫出第一筆含結論的列，**關閉開關不會讓這些列重新相容 R0**；關開關只停止新寫入。

| 步驟 | 動作 | 此時能否回到 R0 |
| --- | --- | --- |
| 1 | 部署 R1，`JUKEBOX_API_RETENTION=off`（預設），**migration 尚未套用** | 可以。R1 在此狀態不讀寫新表；有測試（資料表不存在時投稿、列表、核准、Owner 檢視、警示、巡檢都正常） |
| 2 | 套用 `0004`（`python -m bot.migrate plan`，確認只有 0004，再 `apply --apply`） | 可以回到 R0 的執行程式（R0 不讀新表）；但 **R0 的 `bot.migrate` 會因帳本中有未知版本而拒絕**，要用 R1 的遷移工具 |
| 3 | 設 `JUKEBOX_API_RETENTION=on` 並重啟 | 從第一筆寫出含結論的列開始，**最低可回滾版本是 R1** |
| 4 | 觀察 `status`、卡片警示；確認 `jb2_retention_runs` 有成功紀錄 | — |

**回滾辦法（依情況）：**
1. 想停止使用 API 資料：停用 `JUKEBOX_AUTO_REVIEW`（停止查詢）。保持 `JUKEBOX_API_RETENTION=on`，讓清除繼續；清除與自動審核彼此獨立。
2. 想回到「舊寫入格式」：只把 `JUKEBOX_API_RETENTION` 改回 `off`，**保留 R1 程式**。已寫入的結論列仍可讀；新的自動審核回到含秒數的舊格式。
3. 想退回 R0 的程式：**只有在確認沒有任何含結論的列時才可以**。唯讀確認（請 Owner 或夥伴用唯讀角色執行；我沒有執行）：

```sql
SELECT (SELECT count(*) FROM jb2_videos           WHERE review_json::text LIKE '%duration_verdict%') AS videos,
       (SELECT count(*) FROM jb2_review_history   WHERE review_json::text LIKE '%duration_verdict%') AS history,
       (SELECT count(*) FROM jb2_evidence         WHERE evidence_json::text LIKE '%duration_verdict%') AS evidence,
       (SELECT count(*) FROM jb2_decisions        WHERE result_json::text LIKE '%duration_verdict%') AS decisions,
       (SELECT count(*) FROM jb2_receipts         WHERE receipt_json::text LIKE '%duration_verdict%') AS receipts;
```
   五個數字全是 0 才能退回 R0；否則一律留在 R1。
4. 資料表（0004）可以保留不用，不需要刪除。

## 七、與先前草稿的差異

- 草稿的「等待案件第 10 天刷新一次」**改為只在到期且仍在等待時才查一次**：更少的 API 呼叫，也更簡單。
- 觀測表多一個 `CHECK`：未清除的列必須有時長數值；清除原因限定為 `expired`／`completed`／`unavailable`。
- 草稿的 `jb2_retention_runs` 欄位精簡為開始、結束、清除筆數、錯誤類別。
- 「僅在開關開啟時讀寫新表」是新增的保證，讓 R1 能在 migration 之前先部署。

## 八、舊資料整理（S8）：只保留預覽方案，**未實作、未執行**

先確認有沒有需要整理的舊資料（請以唯讀角色對正式庫執行；我沒有執行也沒有正式庫存取）：

```sql
SELECT count(*) FROM jb2_review_history WHERE review_json::text LIKE '%youtube-data-api-v3:videos.list@%';
```

- 若為 0：不需要整理。
- 若大於 0：舊的含秒數快照散在六處（設計文件第三節 #1–#6）。整理需要「臨時 UPDATE 權限」改寫本來只增不改的歷史表，會碰到 `request_digest` 與重播，**屬於不可逆、需要另行核准的作業**。方案：（1）先寫一個只讀的預覽工具，列出每一列會被如何改寫並輸出 digest；（2）分批、可重跑、只處理比 5 天更舊的列；（3）前後逐列比對帳本與餘額；（4）Owner 以 digest 確認後才寫。目前不做任何一步。

## 九、已驗證與未驗證

已驗證（可丟棄 PostgreSQL，見工作報告的原始測試輸出）：所有副本只在觀測表、決定後清空、到期拒絕採信（含清除未執行時）、清除失敗後的重試與逾期升級、重啟先清除、只對到期的待審案件再查、人工時長不受影響、已核准案件照常完成版本與結算、舊格式列仍可讀、開關關閉與 migration 之前的行為、權限（無 DELETE、無法改歷史表）。

**未驗證：** 真實 YouTube API、真實 Supabase（Session Pooler）、真實 Discord 卡片、VPS 與備份狀態；這些需要 Owner 與夥伴在開發環境實測。備份期限仍只是估算（正常清除成功且已知副本都納入時約 29 天），**不是官方認可，也不是硬性保證**。
