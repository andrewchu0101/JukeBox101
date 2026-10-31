# VPS 夥伴部署前清單：API 資料保存（含新程式、migration 0004、啟用開關）

狀態：**清單，尚未執行任何一步。** 目前沒有授權 push、合併或部署；Owner 的假設是「任何分支的 push 都可能立即觸發正式 Bot 部署」，本清單依此處理。細節與理由見 [API 資料保存發行說明](../api-retention-release.md)。

## 0. 先回答這四個問題（在任何 push 之前）

倉庫本身**看不出**正式環境怎麼被更新，所以必須由夥伴回答：

| # | 問題 | 倉庫裡能確認的 | 需要夥伴確認的 |
| --- | --- | --- | --- |
| Q1 | push 之後，**實際監看哪個分支或標籤**？是 webhook、排程輪詢（cron／systemd timer），還是人工？ | `.github/workflows/ci.yml` 只在 PR 與 push 到 `main` 時跑測試，**沒有任何部署、SSH 或秘密**；倉庫內沒有 CD | 主機上是否有任何自動拉取（`crontab -l`、`systemctl list-timers`、託管面板的 Git 部署、GitHub repository 的 Webhooks／Deploy keys） |
| Q2 | 觸發後會做到哪一步：只拉程式？`build`？`up -d`？ | 文件記載的更新方式是**人工**：取得交付包 → 核對 SHA-256 → 覆蓋程式（不覆蓋 `.env` 與秘密目錄）→ `build` → `up -d`（`docs/deploy/partner-deployment.md`） | 是否有人把這個流程自動化 |
| Q3 | 觸發的更新會不會**自動執行 migration**？ | migration 是獨立的 `--profile migrate` 服務，`up -d` 不會啟動它 | 確認沒有任何腳本在更新後自動 `migrate apply` |
| Q4 | 是否使用 `compose.auto-review.yaml` 疊加檔？ | 該檔是選用疊加檔；**只有用 `-f` 指名才會把 `JUKEBOX_API_RETENTION` 傳進容器** | 目前實際的 `docker compose` 指令與檔案清單 |

在 Q1 到 Q3 有書面答案前，一律視為「任何 push 會直接部署到正式 Bot」。

## 1. 備份（部署前，夥伴操作）

| 項目 | 做法 | 注意 |
| --- | --- | --- |
| 目前映像 | 記下 `docker image inspect --format '{{.Id}}' jukebox101-bot:v7`，再 `docker tag jukebox101-bot:v7 jukebox101-bot:rollback-<日期>` | 保留舊映像是回滾的唯一依據，不要 `docker image prune` |
| 目前執行的版本 | 記下 `docker compose ps`、容器的重啟次數、目前部署的交付包 SHA-256 或 Git commit | |
| 設定 | 複製 `.env` 與使用中的 Compose 檔案到**主機上不進 Git 的私人位置**；秘密目錄只記錄檔名、擁有者與權限，**不複製內容到任何對話或文件** | |
| 資料庫 | Supabase Free 沒有自動備份。migration 前可做 `pg_dump --schema-only`（不含資料，因此不含 API 值）；若要資料傾印，視為 API 資料副本，保留 ≤14 天或排除 `jb2_api_observations` | 備份天數是估算，不是官方認可 |
| 目前開關 | 記下 `JUKEBOX_AUTO_REVIEW`、`JUKEBOX_AUTO_SETTLEMENT`、`JUKEBOX_API_RETENTION` 的實際值 | |

## 2. 第一階段：只更新程式，所有開關保持關閉

- 不改 `.env` 的開關；`JUKEBOX_API_RETENTION` 不設定或設為 `off`；**不執行 migration**。
- 預期：行為與目前版本相同；不讀寫新表（已用「沒有套用 0004」的資料庫實測，見發行說明）。
- 部署後立即檢查（每一項都要有輸出才算）：
  1. `docker compose -f <實際使用的檔案> config | grep JUKEBOX_`：三個開關仍是原值，`JUKEBOX_API_RETENTION` 為 `off`。
  2. `python -m bot.readiness` 回報 `ready`；`python -m bot.healthcheck` 結束碼 0 且 Gateway 為 ready（等 180 秒啟動加 120 秒觀察）。
  3. `/balance`、`/history` 正常；Owner 在開發環境以外**不要**測 `/share` 的真實投稿，除非 Owner 同意。
  4. 日誌沒有 `UndefinedTable`、`duration_verdict`、`Traceback`。
- **停止條件**（任一發生就回滾到備份映像並回報）：健康檢查失敗、重啟次數增加、日誌出現上列字樣。

## 3. 第二階段：migration 0004（需要 Owner 明確授權；執行人：夥伴，使用遷移角色）

| 步驟 | 指令 | 預期 |
| --- | --- | --- |
| 1 | `docker compose --profile migrate run --rm migrate status` | 顯示已套用 0001 到 0003 |
| 2 | `... migrate plan` | **待套用只有 0004**，且不顯示任何未知、被編輯或順序錯誤的歷史 |
| 3 | `... migrate apply --apply` | 一個交易；失敗會整個回復 |
| 4 | 唯讀角色執行 [S8 盤點](../api-retention-inventory.md) 的 A 區 | `migration_0004_applied` 為 true |
| 5 | `python -m bot.readiness` | `ready` |

- **停止條件**：`plan` 出現 0004 以外的任何項目、歷史不一致、`apply` 失敗、步驟 5 不是 `ready`。停止後不要重試，回報輸出。
- 提醒：舊版（目前正式版）的 `bot.migrate` 在帳本有 0004 後會拒絕執行；之後要用新版的遷移工具。

## 4. 第三階段：啟用保存開關（需要 Owner 明確授權；執行人：Owner 決定，夥伴操作）

先決條件：S8 盤點已執行並由 Owner 看過結果。

- `JUKEBOX_API_RETENTION=on` 並重啟。**`JUKEBOX_AUTO_REVIEW` 的啟用是另一個決定**，不在本階段。
- 啟動後 1 小時內檢查 `python -m bot.api_retention status`：最近一次成功清除為「剛剛」。`jb2_retention_runs` 無錯誤類別。
- **停止條件**：清除失敗（`status` 顯示 overdue rows 增加，或 `last successful purge` 不是最近）；readiness 不是 `ready`。處置：保留新版程式；若自動審核已開，先把 `JUKEBOX_AUTO_REVIEW` 改為 `off`（只關保存而自動審核仍開，新版會拒絕啟動）。

## 5. 寫入新格式前後各自可用的回滾版本

名詞：**R0** = 目前正式版。**R1** = 含本變更的版本。「寫入新格式」= `JUKEBOX_API_RETENTION=on` **且**自動審核實際對一支影片得出結論（兩者缺一不會寫出含結論的列；單開保存開關、自動審核關閉時沒有新格式列）。

| 時間點 | 可回滾到 | 方式 |
| --- | --- | --- |
| 第一階段後（R1，開關關，未 migration） | R0 | 切回備份映像 |
| 第二階段後（0004 已套用，開關關） | R0 的執行程式 | 切回備份映像；新表閒置無害；但 R0 的 `bot.migrate` 會拒絕，遷移用 R1 |
| 第三階段後，自動審核仍關（沒有新格式列） | R0 | 先把開關改 `off`，再切映像；要先以 [發行說明第六節的 SQL](../api-retention-release.md) 確認五個計數皆為 0 |
| 已寫出第一筆含結論的列之後 | **最低只能回到 R1** | 把 `JUKEBOX_AUTO_REVIEW` 與 `JUKEBOX_API_RETENTION` 都改為 `off`、保留 R1 程式（只關後者會拒絕啟動）；**不要**退回 R0（R0 讀到這些列會出錯）；除非上述 SQL 的計數全為 0 |

## 6. 「保存開關關、自動審核開」不是已解決的組合

| `JUKEBOX_AUTO_REVIEW` | `JUKEBOX_API_RETENTION` | 結果（已在可丟棄 PostgreSQL 實測） |
| --- | --- | --- |
| off | off／on | 沒有 API 查詢；沒有新資料；（on 時仍會執行清除） |
| **on** | **off** | **新版拒絕啟動**（設定檢查階段，早於任何連線與 Gateway）。原因：此組合會讓官方時長秒數、頻道 ID、取得時間與 API 來源標籤**永久**寫進 `jb2_videos`、`jb2_review_history`、`jb2_evidence`、`jb2_decisions`（第二位投稿時還有 `jb2_receipts`）。拒絕訊息說明必須先完成 migration 並開啟保存處理 |
| on | on | 官方數值只在 `jb2_api_observations`，決定後清空，到期清除；永久表只有結論。啟動時另外檢查 0004 的兩張表與權限，缺少就拒絕啟動 |

注意：`bot.healthcheck` 也會載入同一組設定，所以「審核開、保存關」的容器會被判為不健康，而不是悄悄運作。系統不會自動套用 migration，也不會自行修改任何設定值。

## 7. 避免多次意外部署的交付方式（建議）

PR 可以拆成四個供審查，但**不要假設能依序 push／合併到正式環境**：

1. **先取得 Q1 到 Q3 的答案。**
2. 如果監看的只是 `main`：功能分支的 push 不會部署，但**合併一個 PR 就是一次部署**。因此只開**一個**進入 `main` 的 PR（逐 commit 審查，四組變更已是獨立的 commit），或先把四個 PR 合進同一條**整合分支**（不是 `main`），審完再用**一次**合併進 `main`。這樣正式環境只收到一次更新。
3. 如果監看任何分支，或答案不明：**不 push**。改用文件記載的方式——由我產生交付包（`git archive` 加 SHA-256，放在 Git 之外）交給夥伴人工部署；審查用 `git format-patch` 的補丁交給審查者。
4. 無論哪種，順序一律是：第一階段（程式，開關關）→ 驗證 → 第二階段（migration）→ 驗證 → 第三階段（開關）。不要把三個階段放進同一次更新。

映像、部署包、秘密與私人檔案不進 Git。
