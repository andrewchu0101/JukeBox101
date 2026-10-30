# 補償模型與狀態流程（離線實作，2026-10-24，第二版）

**性質**：離線實作、測試與文件。**沒有執行 DDL，沒有啟動或連線 MySQL，沒有解密憑證，沒有新增帳號或增權，沒有 Discord 指令，沒有真實 DM，沒有提交或推送，沒有啟用正式交換或補償。** 本文件不代表補償功能已上線。SQLite 離線通過不等於 MySQL 通過。

## 1. 現行規則（Owner 最終處置）

1. 補償按**事故**計算。**沒有使用者或時間窗的配額**，不計算風險分數，不設冷卻期，不自動拒絕，不自動停權。
2. 事故有三種結果（`exchange_incidents.state`）：
   - **`settled`（系統故障已確認）**：原扣款退回一次（`refund_status = refunded_once`）。Owner 另行決定是否免費補送（`redelivery_decision` 為 `approved` 或 `not_approved`）。**拒絕補送不取消退款。**
   - **`not_system_fault`（非系統故障）**：先記為待決，扣款保留，使用者解除阻擋，訂單留在 Owner 的待決清單。**2026-10-24 Owner 決定**：若確認原訂單未交付，退回原扣款一次、不提供免費補送、完成結案（`non_fault_refunded`，見第 5 節）；未確認未交付則不能用此路徑。
   - **`non_fault_refunded`（非系統故障・未交付・已退款）**：結案狀態，不產生補送資格，不寫零金額 Ledger。
   - **`void`（作廢）**：結算時原訂單已被 claim 或釋放，不退款、不補送。
3. 免費補送資格（`exchange_redelivery_grants`）每個事故**最多一筆**，派送最多一次。claim 後結果不明者轉為 `uncertain`，**不自動重送**。
4. **帳號阻擋與歌曲持有分開記錄**：
   - 帳號阻擋（`pending_block`）：存在滯留保留，或存在 `closed_at` 為空的補送資格（`open`、`claimed`、`not_sent`、`uncertain`）。Owner 完成處置前，阻擋整個帳號的新交換。
   - 歌曲持有（`holds_song`）：補送取得的歌曲記錄於 `holds_song`，使重抽時不會重複同一首。`not_sent` 經 `no_further_action` 結案即釋放；`uncertain` 經任何結案（`confirmed_delivered` 或 `confirmed_not_delivered`）皆仍保留（依上一輪指定規則，2026-10-24 已修正程式；Owner 尚未另行確認，見第 7 節 Q-B）。
5. **24 小時逾期**：未結案的補送資格，以及仍待決定的事故（已核准待結算，或判定為非系統故障），自建立起超過 24 小時，只在 Owner 待處理清單中標示 `overdue = true`（補送由 `open_grants`，事故由 `pending_incident_rows`）。**不自動解除阻擋、不退款、不重送、不通知。**
6. **同 key 重放與重複結算**：
   - 已結算事故的原訂單 claim 回傳 `failed`，不派送。
   - 同一事故重複結算回傳 `already_settled`，不再退款、不再建立補送資格，無論第二次要求的補送決定為何。
   - 退款由 `refund_status`、`refund_ledger_id` 唯一鍵與共用的 `refund:` 冪等鍵三重保護。
7. **原訂單 claim 與 Owner 核准、結算互斥**：所有操作都在 writer gate 之下序列化（第 2 節）。
8. 介面：`bot/database/compensation_store.py` 的 `settle_incident(cursor, incident_id, redelivery_approved, now)` 是唯一結算入口；`redelivery_approved` 必須由 Owner 明確給定，沒有預設值。

## 2. 派送與互斥邊界

原訂單（reservation）的 claim 在 `claim_reservation`（`bot/database/exchange_delivery_store.py`）中檢查事故。只要保留列存在任何事故，claim 就回傳 `held_for_review`：不送 DM、不改變保留狀態，並寫入 `claim_held` 稽核事件。檢查位於交易邊界內，直接呼叫、同 key 重放與競爭都受同一規則約束。

| 狀態 | 原訂單（reservation） | 補送資格（grant） | 離線測試 |
| --- | --- | --- | --- |
| 無事故 | 一般 claim 與派送照常 | 不存在 | 已測試 |
| `approved` | claim 被保留，不派送 | 不存在 | 已測試 |
| `not_system_fault` | claim 被保留，不派送，不退款 | 不存在 | 已測試 |
| `settled`（結算先於 claim） | 原列已 `failed`／釋放，claim 回傳 `failed`，不派送 | `open`，可派送一次 | 已測試 |
| `settled`（補送不核准） | 原列已 `failed`／釋放，不派送 | 不存在；只有退款 | 已測試 |
| `void` | 原列未釋放前即被 claim；不退款 | 不存在 | 已測試 |
| 補送 `open` | — | 只能由 claim 進入 `claimed`；第二次 claim 不送 | 已測試 |
| 補送 `claimed` 過期 | — | 轉為 `uncertain`，不重送 | 已測試 |
| 補送 `claimed` 中 Owner 處置 | — | 拒絕（`NOT_CLOSABLE_FOR_STATE`） | 已測試 |
| 補送 `not_sent`／`uncertain` | — | 不自動重送，等待 Owner | 已測試 |

**claim 與核准、結算的競爭**（都在同一把 gate 之下，每種順序都有唯一且可稽核的結果）：
- 認領先提交：核准被拒絕 `NOT_STRANDED`，不退款，原訂單送出一次。
- 核准先提交：認領被保留並留下 `claim_held` 事件，之後由結算決定。
- 結算先提交：認領回傳 `failed`，不派送。

**原訂單與補送不會同時交付**：原訂單只在無事故時可 claim；補送只在結算後存在，結算時原列已釋放。

**派送被拒絕時**：`held_for_review` 的回覆為「此訂單待 Owner 處理，目前未派送，也未重複扣點；請聯絡 Owner。」不回報已送出，也不自動退款或解除阻擋。

## 3. 部署相容性、功能開關與 schema 守衛

- **預設關閉**：`SubmissionRepository(..., compensation_enabled=False)` 是預設值。關閉時，一般交換、claim、持有排除與阻擋判斷都不讀取任何補償表（離線測試以查詢文字檢查）。
- **啟用時的守衛**：每個補償相關交易先執行 `require_schema`。`bot/database/compensation_schema.py` 的 `SCHEMA_SPEC` 逐一列出三張表的欄位、唯一鍵與具名 CHECK。
  - 表不存在 → `CompensationSchemaMissing`，只含表名。
  - 欄位、唯一鍵或具名 CHECK 缺少 → `CompensationSchemaMismatch`，只含缺少的元素名稱。
  - 其他資料庫錯誤（例如鎖逾時、連線中斷）原樣傳出，不被偽裝成缺表。
- **停用時的 Owner 入口**：所有補償 Owner 方法在停用時拒絕，錯誤為 `CompensationDisabled`，且在任何資料庫工作之前。

## 4. 資料模型（候選，未執行）

| 表 | 用途 | 關鍵約束 |
| --- | --- | --- |
| `exchange_incidents` | 一筆事故：綁定保留與原始扣款，記錄核准、退款、補送決定與結案 | `UNIQUE(recommendation_id)`、`UNIQUE(debit_ledger_id)`、`UNIQUE(refund_ledger_id)`；`chk_incidents_refund`、`chk_incidents_settled`、`chk_incidents_non_fault`、`chk_incidents_closed` |
| `exchange_redelivery_grants` | 一次免費補送資格與其派送狀態、交付事實、Owner 處置 | `UNIQUE(incident_id)`；`chk_grants_claim`、`chk_grants_sent`、`chk_grants_holds` |
| `exchange_incident_events` | append-only 審計事件（含 `claim_held`） | 只寫入 |

候選 DDL：`docs/compensation-candidate-004.sql`，未註冊、未執行。**啟用功能需要這份 DDL**；關閉功能時不需要。DDL 的每個欄位與具名約束都與 `SCHEMA_SPEC` 和 SQLite 離線鏡像一致（離線測試以此驗證）。

## 5. 非系統故障（`not_system_fault`）

取代舊的「婉拒（declined）」用語。兩者的行為相同：扣款保留、保留仍為 `reserved` 且未釋放、使用者解除阻擋、訂單留在 Owner 的待決清單（`pending_incident_rows`，逾期標示同第 1 節第 5 點）。**系統沒有把它視為已賺取，也沒有把它標成已履約**。Owner 確認未交付後，`close_non_fault_refund`（`bot/database/compensation_store.py`）以下列順序結案，全部在同一個 writer-gate 交易內：

1. 驗證：事故仍為 `not_system_fault` 且未結案；`confirmed_undelivered` 必須為明確的 `True`（無預設值）；保留仍為 `reserved`、未被 claim、未釋放，且超過門檻（`_is_stranded`）。任何一項不符即拒絕，不寫入任何資料（`NOT_NON_FAULT`、`NOT_CONFIRMED_UNDELIVERED`、`NOT_STRANDED`）。
2. 退款：沿用既有的原扣款綁定（`_legitimate_debit` 與 `incident.debit_ledger_id` 一致），寫入一筆 `delivery_refund`，使用共用的 `refund:` 冪等鍵，並把保留改為 `failed` 且 `released_at` 設定。
3. 結案：事故改為 `non_fault_refunded`、`refund_status = refunded_once`、`redelivery_decision = not_approved`、`closure = non_fault_refund`，並寫入 `non_fault_refund` 事件（detail 為 `confirmed_undelivered`，actor 為 Owner）。
4. 失敗：任一步失敗整個交易回滾；事故仍為 `not_system_fault`、保留仍為 `reserved`，可安全重試，不會出現「已結案但未退款」。
5. 重放：`non_fault_refunded` 再次呼叫回傳 `already_closed`，不再退款，也不建立補送資格。

**邊界**：此路徑只適用於「非系統故障且確認未交付」。已 claim、`uncertain`、`sending` 的保留不適用，不能因此退款。它不是 D2 的例外：歌曲持有規則不變，且本路徑不建立補送資格，因此沒有 `holds_song`。定時退款掃描（`retry_pending_refunds`）只掃 `failed` 且未釋放的保留，不會選到 `reserved` 的非系統故障保留，也不會重複退款。已核准的事故不能事後改為非系統故障（`ALREADY_DECIDED`）。

## 6. failed 的多重語意

`failed` 同時表示「DM 被分類為未送出」與「從未 claim 而沖回」。沖回的保留一定有一筆 `exchange_incidents` 列指向它（唯一鍵保證一對一），而 DM 未送出的 `failed` 沒有事故列。第一版以事故關聯區分，這是應用程式約定，不是資料庫保證。

## 7. 仍待 Owner 決定（不代替決定）

| 編號 | 事項 | 目前行為 | 影響 | 建議（不代替決定） |
| --- | --- | --- | --- | --- |
| Q-A | 非系統故障的最終結案 | **已由 Owner 決定（2026-10-24）**：確認未交付則退回原扣款一次、不補送、結案；見第 5 節 | 未確認未交付者仍留在待決清單 | 已依決定實作（D1），見第 5 節 |
| Q-B | `uncertain` 經 `confirmed_not_delivered` 後是否釋放歌曲 | 保留（已依上一輪指定規則修正；原先為釋放） | 保留時使用者不能再付費抽同一首，直到 Owner 另行決定 | 維持保留；若 Owner 要改為釋放，需明確指示 |
| Q-C | 補送未送出後是否可再次補送 | 不允許，不自動重送 | 使用者無法取得補送 | 預設不允許，需 Owner 明確決定 |
| Q-D | 24 小時逾期的提醒方式 | 只在名單標示，無通知 | Owner 需主動查看名單 | 通知方式與頻率另行決定 |
| Q-E | `failed` 的多重語意 | 以事故關聯區分（約定） | 審計依賴事故表 | 接受；若要資料庫保證需新狀態與 DDL |

## 8. 測試與離線結果

**本輪新增與更新的測試**：
- `tests/test_compensation_disposition.py`：退款僅一次並結案（不補送）；同一事故重複結算不再退款或補送；已結算事故不能改為非系統故障或重新核准；非系統故障不能被結算；uncertain 補送保留歌曲並阻擋帳號，確認已送出後解除阻擋、歌曲仍保留；not_sent 經 Owner 結案後釋放歌曲與阻擋；claim 中不能被 Owner 處置；24 小時逾期旗標只在名單中出現。
- 2026-10-24 補充（先紅後綠）：`uncertain` 經 `confirmed_not_delivered` 結案仍保留歌曲並解除阻擋、未改寫 `unknown`；待處理事故（已核准待結算、非系統故障）超過 24 小時標示逾期，且不改變阻擋或金額。紅燈時為「釋放」與「函式不存在」，綠燈後 `tests/test_compensation_disposition.py` 全數通過。
- `tests/compensation_sqlite.py` 的 `approved_at` 與 `settled_at` 已改為 `DATETIME`，與候選 DDL 一致（先前為 `TEXT`）。`closed_at` 與其餘時間欄位在鏡像中仍為 `TEXT`，與 DDL 的 `DATETIME(6)` 有型別差異，列入 D5 人工比對範圍。
- **D1（2026-10-24 Owner 決定，先紅後綠）**：`tests/test_compensation_disposition.py` 新增 5 項：確認未交付的非系統故障退款一次並結案、不建立補送、寫入審計事件並釋放保留；重放回傳 `already_closed`、不重複退款；未確認未交付則拒絕且無寫入；`claimed`／`uncertain` 保留不適用此路徑；退款中途失敗整體回滾，重試恰好退款一次。紅燈時為 `close_non_fault_refund` 不存在（5 項失敗）。`tests/test_compensation_repository.py` 新增 Owner 邊界的結案與定時掃描不重複退款測試，並把新方法加入非 Owner 拒絕清單。
- `tests/test_compensation_schema.py`：完整 schema 通過；缺表、缺欄位、缺唯一鍵、缺具名 CHECK 各自以名稱拒絕；其他資料庫錯誤原樣傳出。
- 既有的 `test_compensation_store.py`、`test_compensation_gate.py`、`test_compensation_repository.py` 與 `test_reserved_protection.py` 已改為新的 API（結算需明確給定補送決定，婉拒改名為非系統故障）。

**TDD 的誠實紀錄**：本輪先修改了 store 與 schema 守衛，再撰寫上述新測試，因此新測試不是在紅燈階段先寫的。已確認的是：舊測試在改名後轉綠，新測試全數通過。

**離線結果（2026-10-24 實際執行，`python -m pytest`）**：**2219 passed、79 skipped、0 failed。** 79 項跳過全部是需要隔離開關的 MySQL 案例。

## 9. 尚未驗證與限制

- **MySQL 上的所有新 SQL 未執行**，包括 `information_schema.STATISTICS` 與 `TABLE_CONSTRAINTS` 的查詢。SQLite 只驗證語意與約束，不等於 InnoDB 的鎖與隔離。
- **SQLite 鏡像與 MySQL DDL 分別撰寫**，兩者一致性靠人工比對與 `SCHEMA_SPEC`，不是自動比對。
- **協定替身（`submission_persistence_fake.py`）的 schema 探測是依 `SCHEMA_SPEC` 回答的**，因此它驗證的是守衛的流程，不是 schema 本身；schema 本身由 SQLite 鏡像測試驗證。
- **跨程序的 claim 與核准、結算競爭未在 MySQL 上執行。** 目前依賴 writer gate；見 `docs/compensation-mysql-verification-plan.md`。
- **真實 DM 未驗證**；`RecordingSender` 不是 Discord。
- **Discord 指令未實作**，Owner 只能透過 repository 介面操作。

## 10. 歷史：前一版規則（已被第 1 節取代）

- 前一版把 Owner 的兩件事混成一個 `declined` 狀態：婉拒既可能指「不是系統故障」，也可能指「不補送」。第二版拆開：退款永遠隨確認的系統故障發生（`refund_status`），補送是獨立決定（`redelivery_decision`），非系統故障有自己的狀態（`not_system_fault`）。
- 前一版的 `exchange_incidents.state` 包含 `declined`；第二版改為 `not_system_fault`，並新增 `refund_status`、`redelivery_decision`、`closure`。候選 DDL 已同步更新。
- 前一版的 `settle_incident(cursor, incident_id, now)` 沒有補送決定參數；第二版必須明確給定。
- 更早的「每使用者 30 天配額」與「零金額 ledger 列」已在 `docs/exchange-compensation-design.md` 標示為失效。

## 11. 進入隔離 MySQL 前真正剩下的阻礙

1. ~~Owner 決定 Q-A~~（已於 2026-10-24 決定並實作，見第 5 節）。Q-B 維持保留，若 Owner 要改變需另行指示。
2. 候選 DDL 4 的執行授權（啟用功能需要；本輪未執行）。
3. 隔離 MySQL 上的 DDL 與跨程序競爭測試，需另行授權；計畫見 `docs/compensation-mysql-verification-plan.md`。
4. 在上述授權完成前，功能保持關閉。
