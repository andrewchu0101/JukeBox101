# 補償功能交接文件（2026-10-24，離線收尾）

**性質**：交接紀錄，不是產品規格。本次收尾**未執行**測試、MySQL、DDL、憑證解密、提交或推送，也**未修正程式**。所有 Git 資訊為 2026-10-24 收尾時的唯讀核對結果，會隨時間變動。本文件不宣稱工作樹乾淨，也不把任何實作視為 Owner 核准。

## 1. Git 現況（唯讀核對）

| 項目 | 結果 |
| --- | --- |
| 分支 | `feature/owner-review-offline-checkpoint` |
| HEAD | `4f1e3f1`（與預期 checkpoint 相同） |
| 遠端 | `origin` = `https://github.com/andrewchu0101/JukeBox101.git` |
| 同步狀態 | 本機 HEAD 與 `@{u}` 相同（ahead 0、behind 0） |
| `git status --short` | 121 項：82 項已修改（` M`）、39 項未追蹤（`??`） |

**本階段修改（補償相關，尚未提交）**：
- 已修改：`bot/database/exchange_delivery_store.py`、`bot/database/submission_repository.py`、`tests/submission_persistence_fake.py`。
- 未追蹤：`bot/compensation_delivery.py`、`bot/database/compensation_schema.py`、`bot/database/compensation_store.py`、`bot/database/pending_review.py`、`tests/compensation_sqlite.py`、`tests/test_compensation_disposition.py`、`tests/test_compensation_gate.py`、`tests/test_compensation_repository.py`、`tests/test_compensation_schema.py`、`tests/test_compensation_store.py`、`tests/test_reserved_protection.py`、`docs/compensation-candidate-004.sql`、`docs/compensation-mysql-verification-plan.md`、`docs/exchange-compensation-model.md`。

**其他既有未提交內容（不是本階段產生，不得混入補償提交）**：其餘約 100 項修改與未追蹤檔，包括 `CLAUDE.md`、`ROADMAP.md`、`README.md`、`.gitignore`、多數 `docs/` 與 `tests/batch_b_*`、`bot/decision_snapshot.py`、`bot/ledger_integrity.py`，以及一個含個人資訊的 PDF（`Re Issue 576880673 ...pdf`，**不得提交**）。提交前必須逐檔審核並 stage，禁止 `git add .`。

**工作樹狀態**：不乾淨。本次收尾沒有清理、重設或還原任何檔案。

## 2. 已驗證證據（區分來源與時間）

| 證據 | 內容 | 性質 |
| --- | --- | --- |
| checkpoint `4f1e3f1` 的隔離 MySQL | 21 passed、1 failed | 使用者提供的分批執行結果，不是同一快照的一次完整執行 |
| 測試路由修正後的單項實機重驗 | 1 passed | 呼叫端單項，使用者提供 |
| 補償最新離線套件 | 2219 passed、79 skipped、0 failed | 2026-10-24 本輪已實際執行；本次收尾**未重跑** |
| 候選 DDL 004 | 未執行，未註冊 | — |
| 新補償 SQL、schema guard、跨程序競爭 | **尚未經 MySQL 驗證** | — |
| 新行為測試的 TDD 紀錄 | **是實作後補寫，不能記為完成紅綠 TDD**（舊測試改名後轉綠，新測試全數通過） | — |

離線通過不等於 MySQL 通過。`2219 passed` 是離線 SQLite 與協定替身的結果。

## 3. Owner 規則的準確交接

以下為對話中確認，或作為上一輪實作指令提供的規則。**本文件仍不新增產品決策。**

1. 補償按事故計算，沒有每人 30 天配額；異常頻繁交給 Owner 審核。
2. 原款退款與免費補送分開處理。確認未交付且符合本階段適用條件的原訂單，退款最多一次。
3. 婉拒免費補送，不等於拒絕退回應退的原扣款。
4. 每個事故最多建立一次免費補送資格，不自動再次補送。
5. uncertain 不自動重送；人工結案不得改寫 unknown 的交付事實。
6. 上一輪指定：uncertain 結案後仍保留歌曲持有，但解除帳號整體交換阻擋。
7. 上一輪指定：not_sent 結案後釋放歌曲持有。
8. 上一輪指定：待處理超過 24 小時，在 Owner 清單標示逾期；範圍**不限於 open grant**，且不自動產生財務或派送動作。

## 4. 目前實作與上述規則的差異

| 編號 | 差異 | 狀態 |
| --- | --- | --- |
| D1 | `not_system_fault` 保留扣款、解除阻擋，但**沒有結案路徑**。 | 不是已解決的 Owner 決策；需新 session 釐清（見 model Q-A） |
| D2 | `uncertain` 被標記 `confirmed_not_delivered` 後，程式**釋放**歌曲；這與規則 6 的「uncertain 結案後仍保留歌曲持有」有差異。 | 不可默認已核准；需新 session 釐清（見 model Q-B） |
| D3 | 24 小時逾期目前只由 `open_grants` 計算，範圍限於 `exchange_redelivery_grants` 中 `closed_at IS NULL` 的列；**沒有涵蓋**未結案的 `not_system_fault` 事故、滯留保留或其他待處理事故。 | 與規則 8 不符，需修正範圍（只標示，不產生動作） |
| D4 | 「新命名」（如 `not_system_fault`、`refund_status`、`redelivery_decision`、`closure`、`overdue`）是本輪技術命名，不是 Owner 核准的名稱。 | 僅為實作命名 |
| D5 | 候選 DDL、SQLite 鏡像、`SCHEMA_SPEC` 三者是人工比對一致，沒有自動比對。 | 未經 MySQL 驗證 |
| D6 | 協定替身以 `SCHEMA_SPEC` 回答 schema 探測，驗證的是守衛流程，不是 schema 本身。 | 已知限制 |

**本輪只記錄差異，未修正程式。**

## 5. 精確檔案與執行邊界

**補償正式程式**
- `bot/database/compensation_store.py`：事故、結算、補送、處置、列表。
- `bot/database/compensation_schema.py`：`SCHEMA_SPEC` 與 `require_schema`。
- `bot/compensation_delivery.py`：補送 DM 派送。
- `bot/database/pending_review.py`：滯留判斷與 Owner 清單。
- `bot/database/submission_repository.py`：Owner 方法與功能開關（預設關閉）。
- `bot/database/exchange_delivery_store.py`：claim 守衛（`held_for_review`）。

**測試**
- `tests/compensation_sqlite.py`（SQLite 鏡像）、`tests/submission_persistence_fake.py`（協定替身）。
- `tests/test_compensation_{store,gate,repository,schema,disposition}.py`、`tests/test_reserved_protection.py`。

**文件與候選 DDL**
- `docs/exchange-compensation-model.md`：模型與狀態（第 1 節為現行規則，第 10 節為歷史）。
- `docs/compensation-candidate-004.sql`：候選 DDL，**未執行、未註冊**。
- `docs/compensation-mysql-verification-plan.md`：MySQL 計畫與授權文字，**未執行**。
- `docs/exchange-compensation-design.md`：舊設計，已加失效標示。
- `docs/exchange-isolated-mysql-2026-10-24.md`：既有隔離 MySQL 證據紀錄。

**下一輪必讀的 repo 指引**：`CLAUDE.md`（含第 8 節專案設定與第 9 節歷史規範）與 `CODING_STANDARD.md`（若存在；本次未核對其內容）。

**隔離目標（既有，不新增）**
- 實例與資料庫：既有 `127.0.0.1:3307`，資料庫 `jukebox101_test`。
- 資料目錄：`.local/mysql-test/20261024-122528/data`（路徑存在，本次未讀取內容）。
- 既有遷移帳號：依原計畫為 `jukebox101_test_migrator`；測試帳號為 `jukebox101_test_exchange`。**是否仍適用須下一輪核對，本次未查證，也未新增帳號。**
- 憑證：`.local/mysql-test/20261024-122528/credentials.dpapi`，**只記位置與用途**（DPAPI 加密，供既有啟動器讀取）。本次**未讀取、未複製、未解密**其內容。

**執行邊界（本階段）**
- 不連線 3306；不讀 `.env`（檔案存在，本次未讀取）；不啟用正式交換或補償。
- 不依賴聊天記憶或舊實驗啟動器。`.local/mysql-test/` 下有多個舊啟動器（如 `s1_authorized.py`、`s1_resume_authorized.py`、`run_authorized.py`、`retest_authorized.py`），**下一輪應先審核實際執行檔**，不得直接沿用。
- `.local/` 是否已被 `.gitignore` 忽略，本次未確認；提交前必須確認。

**已知 baseline 問題**：manifest 雜湊失敗與本次交換 checkpoint 無關。**不盲目改 hash**，需獨立調查。

## 6. 下一輪啟動 prompt（可直接使用）

> 請先閱讀 `docs/compensation-handoff-2026-10-24.md`、`docs/exchange-compensation-model.md`、`docs/compensation-mysql-verification-plan.md`，以及實際程式（`bot/database/compensation_store.py`、`compensation_schema.py`、`submission_repository.py`）。核對第 4 節列出的差異 D1 至 D6，不要把舊紀錄或新命名當成 Owner 核准。優先收斂模型（D1 結案路徑、D2 uncertain 持有、D3 逾期範圍），並補回歸測試，先寫紅燈再實作。本階段只允許離線工作，不預先授權 DDL 或 MySQL 執行；不連線 3306，不讀 `.env` 或解密憑證，不提交或推送。完成後停止，不做大規模重構。

## 7. 本次收尾的範圍聲明

本次只寫入本文件。沒有修改程式、測試或其他文件；沒有執行測試、MySQL、DDL；沒有解密或讀取憑證；沒有提交或推送。
