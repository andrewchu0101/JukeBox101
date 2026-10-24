# 交換滯留保留（reserved）恢復設計（離線分析，2026-10-24）

**性質**：離線分析與設計提案。**沒有實作任何恢復機制，沒有執行資料庫測試，沒有新增 DDL。** 所有事實均來自目前程式（checkpoint `4f1e3f1` 加上本輪未提交的測試路由修正）。提案中標示「需 Owner 決定」的項目，在決定前不得實作。

## 1. 問題陳述

當交換的 COMMIT 已成功，但兩次嘗試（原請求與同 key 重放）都回報未知，客戶端只能得到 `CommitOutcomeUnknown`。此時：

- 伺服器上已有一筆 `recommendations` 列（`status='reserved'`）與一筆 `exchange_debit` 扣款。
- 這筆保留從未被 claim，沒有 DM 嘗試，沒有派送。
- 使用者若重新執行 `/exchange`，會得到**新的 request_key**（`discord:<interaction.id>`），因此不會被重放保護擋下，會再扣一次點。

目前沒有任何流程會處理這種列。已核對的事實：

- `claim_reservation` 只會在同一請求的 `exchange_and_deliver` 流程中，對該 `recommendation_id` 呼叫；沒有掃描器會領取 `reserved`。
- `recover_interrupted` 只處理 `sending` 的過期租約，轉為 `uncertain`。
- `refund_undelivered` 只接受 `failed`，`reserved` 會回傳 `not_refundable`。
- 因此 `reserved` 列目前**永久滯留**：有扣款、無派送、無退款。

## 2. 目前的狀態模型（事實）

| 狀態 | 進入條件 | 財務 | 派送 | 目前的自動路徑 | 可否退款 | 可否重送 |
| --- | --- | --- | --- | --- | --- | --- |
| `reserved`（未 claim） | 保留與扣款同交易提交 | 已扣 1 點 | 尚未嘗試 | 只有同 key 的重放可 claim | 否（無未送出證明） | 不適用（從未送出） |
| `sending` | claim 成功，DM 嘗試中 | 已扣 | 進行中，有租約 | 租約過期 → `uncertain` | 否 | 否 |
| `delivered` | DM 被 Discord 接受 | 已扣 | 已送達 | 無 | 否 | 否 |
| `failed` | DM 分類為 `NOT_SENT`（證明未送出） | 已扣 | 未送出 | 退款後設定 `released_at` | 是，一次 | 否（退款後不得直接重送） |
| `uncertain` | DM 分類為未知，或租約過期 | 已扣 | 未知 | 無自動路徑 | **否** | **否** |
| `released`（`released_at` 非空） | 只能由 `failed` 退款 | 已退 1 點 | 未送出 | 無 | 已退 | 否 |

- `reserved` 與 `sending`、`uncertain` 不得一律當成可退款或可重送。`reserved` 雖然沒有 DM 嘗試，但在 claim 之前仍可能有同 key 的重放正在進行，因此「未送出」在沒有互斥的情況下無法證明。

## 3. 關鍵限制：無法從請求反查保留

- `recommendations` 沒有 request_key 欄位。
- `credit_ledger` 的 `idempotency_key` 是 `"exchange:" + sha256(request_key)`，單向，無法還原。
- 因此**不能**用原請求的 key 找回它的保留，也無法在不增加持久化資料的情況下判斷「哪一筆 reserved 屬於哪一次未知請求」。

可用的關聯只剩**使用者層級**：`reserved`、無 claim、有一筆 `exchange_debit` 的列，屬於同一使用者。任何恢復設計都必須以使用者範圍為單位，而不是以請求為單位。

## 4. 提案

### 4.1 必做的最小安全項目（不改財務，不需 DDL）

1. **偵測與可見性（唯讀）**：列出每位使用者的「未 claim 的 reserved 列」（數量、建立時間）。不自動退款，不自動派送。供 Owner 查看。
2. **修正合成歷史的 KeyError**：`DELIVERY_LABELS` 缺少 `reserved`，離線已重現 `history()` 拋出 `KeyError: 'reserved'`。應為 `reserved` 提供明確標籤（例如「已保留，尚未派送」），並有回歸測試。這是本段唯一建議立即修正的項目，但仍需在本設計核准後才能實作。
3. **區分請求階段的未知結果**：目前 `exchange_and_deliver` 的 lease sweep 與 refund sweep 會先提交交易。若它們的提交未知，整個請求回報「結果未確認」，但**交換尚未開始，沒有保留被建立**。此時的正確回覆是「本次交換尚未開始，可稍後再試」，而不是「勿重新執行」。見 4.3。

### 4.2 需要 Owner 決定的使用者行為（三選一或組合）

| 方案 | 內容 | 需要 DDL | 財務變動 | 主要風險 | 建議 |
| --- | --- | --- | --- | --- | --- |
| A. 遺留優先派送 | 使用者下次 `/exchange` 時，先 claim 並派送最舊的未 claim reserved，不再扣第二次點 | 否 | 無新扣款，不退款 | 使用者下一次交換會被遺留列佔用；需確認是否可接受 | 候選 |
| B. 取消並退點 | 在 writer gate 下把未 claim 的 reserved 轉為 `cancelled`，並退 1 點 | 是（新狀態） | 退 1 點 | claim 與取消的競爭必須由狀態條件互斥；需保證取消後的重放不會再 claim | 需 Owner 決定是否接受新狀態 |
| C. 人工核對 | 只提供偵測清單，由 Owner 逐筆決定 A 或 B | 否 | 由 Owner 決定 | 人工負擔；需要 SLA | **最小安全起點** |
| D. 定時自動退款 | 未 claim 超過 T 時間自動退款 | 是 | 自動退款 | T 的選擇需證明大於任何重放的生命週期；誤判風險高 | 不建議，除非 Owner 明確接受 |
| E. 阻擋新交換 | 使用者有未 claim reserved 時，拒絕新交換並提示聯絡 Owner | 否 | 無 | 使用者被卡住，直到人工處理 | 可與 C 組合作為過渡 |

**建議的起點**：C（人工核對）加上 E（阻擋新交換，直到 Owner 處理）。這兩項不改財務、不需 DDL，可以先保證不會再次扣點。A 或 B 留待 Owner 在看過實際頻率後決定。

### 4.3 sweep 的未知提交是否應阻斷交換

目前行為：`exchange_and_deliver` 在交換之前執行兩個 sweep，它們的未知提交會拋出例外，中止整個請求。

- **保留阻斷的理由**：sweep 沒有成功確認，就不應讓交換在可能失序的狀態下執行。交換自己有 writer gate 與 key，但 sweep 的結果（例如過期租約是否已轉為 `uncertain`）影響後續判斷。
- **不建議吞掉錯誤**：忽略 sweep 的未知結果會讓狀態停留在不一致狀態，而且沒有可見紀錄。
- **建議**：保留阻斷，但在錯誤中標示**階段**。若失敗發生在 sweep，回覆應說明「本次交換尚未開始，沒有保留或扣點，可稍後重試」。若失敗發生在交換本身，才使用「請勿重新執行」。這是正確的回覆，不是吞掉錯誤。
- **需要 Owner 確認**：是否接受 sweep 的短暫失敗會讓所有交換暫時不可用。

### 4.4 冪等與互斥

- **同 key 重放**：只對**同一請求**有效。已有保留的 key 回傳 `REPLAYED`，不扣款。
- **claim 互斥**：`claim_reservation` 在 `FOR UPDATE` 下只允許 `reserved → sending`。任何提案都必須保持這個條件：不論是 A 的遺留派送，還是原請求的重放，同一保留只能有一次 claim。
- **B 的取消互斥**：取消必須與 claim 在同一個 gate 下，並把狀態改為 `cancelled`。之後的 `claim_reservation` 只接受 `reserved`，因此取消後的重放不能再派送。這需要 DDL 與測試。
- **不得雙重派送**：任何路徑只要 `claim_delivery` 回傳 `claim=None`，就不得送出 DM。

### 4.5 審計

- 每次恢復動作（A 的派送、B 的取消與退款、C 的人工決定）都應寫入 ledger 或獨立審計列，包含：使用者、保留編號、動作、前後狀態、操作者。
- 審計列不得包含 token、連結或其他使用者的資料。
- 退款仍以 `delivery_refund` 與 `released_at` 為唯一收據，不新增第二條退款路徑。

## 5. 狀態轉換規則（提案，不實作）

允許的轉換：

- `reserved → sending`：只能由 claim（同一 gate，`reserved` 條件）。
- `sending → delivered | failed | uncertain`：只能由持有 claim_token 的 record。
- `sending → uncertain`：只能由租約過期的 sweep（已存在）。
- `failed → released`：只能由退款（已存在）。
- `reserved → cancelled`（方案 B）：只能在 gate 下、狀態仍為 `reserved` 時。
- `cancelled → released`（方案 B）：退款收據寫入與取消在同一交易。

禁止的轉換：

- `reserved → failed`（沒有 DM 嘗試就標為未送出，會錯誤地允許退款）。
- `uncertain → failed | released`（沒有證明未送出，不得退款）。
- `uncertain → sending`（不得重送）。
- `delivered → *`（不可逆）。

## 6. 測試矩陣（提案，未實作）

| 編號 | 情境 | 預期 | 層級 |
| --- | --- | --- | --- |
| R1 | 提交未知後，同 key 重放找到 reserved | 不扣第二次點，只 claim 一次 | 已有（實機 1 項） |
| R2 | reserved 未 claim，列表查詢 | 只回傳該使用者的列，無連結與 token | 離線 |
| R3 | 合成 `/history` 遇到 `reserved` | 不拋出 KeyError，顯示保留中標籤 | 離線（需修正後） |
| R4 | 方案 A：遺留 reserved 與原請求重放同時 claim | 只有一次 DM，另一方得 claim=None | MySQL，需授權 |
| R5 | 方案 B：取消與 claim 競爭 | 恰好一方成功；取消後 claim 被拒絕 | MySQL，需 DDL 與授權 |
| R6 | 方案 B：取消退款後重放 | 不再派送，不再退款 | MySQL |
| R7 | sweep 提交未知 | 交換未開始，無保留，回覆為「可稍後重試」 | 離線 |
| R8 | 交換提交未知且重放也未知 | 回覆「未確認，勿重新執行」，保留狀態不變 | 離線（已有） |
| R9 | 租約過期的 sending | 轉為 uncertain，不退款、不重送 | 已有 |
| R10 | 帳務不變條件 | 餘額與 ledger 對帳，released ⇔ refund，無孤兒 | 已有（MySQL 斷言） |

## 7. 需要 Owner 決定的事項（列表）

1. 未 claim reserved 的處理：A、B、C、D，或 C＋E 作為起點？
2. 是否接受方案 B 所需的新狀態（DDL）？
3. 使用者在未確認期間是否可以再次交換（E）？
4. 偵測清單由誰查看，多久一次？
5. sweep 失敗時，所有交換暫時不可用是否可以接受（4.3）？
6. 使用者看到的文字：是否接受「保留中，尚未派送」與「請聯絡 Owner」？

## 8. 限制與未驗證事項

- 本文件是離線分析，沒有任何實機結果。
- 方案 A、B、C、D、E 都沒有實作或驗證。
- 合成 `/history` 的 KeyError 只在離線重現，尚未以實機確認。
- 正式 `/exchange` 仍未啟用，真實 DM 未驗證。
- 兩次未知都在 checkpoint 中以故障注入模擬，不是真實網路中斷。
- baseline manifest 雜湊失敗與本設計無關，保留為獨立已知問題。

## 9. 下一步

1. 由 Owner 回答第 7 節的問題（至少第 1、2、5 項）。
2. 依答案選定方案，之後才能以 TDD 實作。
3. 在實作前，先修正 4.1 第 2 項的 KeyError（小範圍，有回歸測試），並決定是否需要 DDL。
4. 任何需要 DDL、實機測試或真實 DM 的步驟，都需要另行授權。

## 10. 第一階段實作狀態（離線，2026-10-24）

**性質**：離線實作與測試。**沒有新增資料庫狀態，沒有 DDL，沒有自動退款，沒有自動補送，沒有解除阻擋功能，沒有實機或 MySQL 測試，沒有真實 DM，沒有啟用正式 `/exchange`。** 本節只代表離線結果，不代表正式上線。

### 10.1 已實作的保護

- **偵測條件**（`bot/database/pending_review.py`）：`status='reserved' AND claim_token IS NULL AND released_at IS NULL AND created_at < now − STRANDED_AFTER`。`STRANDED_AFTER` 等於既有的 `DELIVERY_LEASE`（10 分鐘），因為正常請求在 reserve 與 claim 之間只會經過數秒。
- **阻擋位置**（`reserve_exchange`）：在正式交易與 writer gate 之下、**重放檢查之後**、餘額檢查之前。因此同一 request_key 的技術重放會先找到原訂單並回傳 `REPLAYED`，不會被阻擋誤擋。新的 `ExchangeStatus.PENDING_REVIEW` 不扣點、不建立任何列。
- **時鐘**：`SubmissionRepository.exchange(..., now=None)` 預設使用目前時間（naive UTC），可注入以便測試；`reserve_exchange` 收到的是同一把 gate 內取得的時間。
- **呼叫端**（`exchange_and_deliver`）：`PENDING_REVIEW` 回覆「你有一筆交換尚待確認（已保留但尚未派送），目前暫停新的交換。請聯絡 Owner 處理。」，不送任何 DM。
- **合成入口**：回覆帶合成標籤，不含連結（已測試）。
- **Owner 清單**（`SubmissionRepository.owner_pending_reservations(requester_id, now)`）：非 Owner 在讀取前即以 `PermissionError("OWNER_ONLY")` 拒絕；只回傳滯留列，欄位為 `operation_code`、`requester`、`song_id`、`reserved_at`，每頁上限 50 筆；不含連結、token 或餘額。**注意**：目前沒有 Discord 指令，只能透過 repository 介面存取；Discord 指令需另行核准。
- **合成 `/history`**：`reserved` 顯示為「已保留，待確認（尚未派送）」，修正了離線已重現的 `KeyError: 'reserved'`。

### 10.2 TDD 證據（離線）

- 紅燈：`tests/test_reserved_protection.py` 先寫，因 `bot/database/pending_review.py` 不存在而收集失敗；另在獨立腳本中重現 `history()` 對 `reserved` 拋出 `KeyError: 'reserved'`。
- 綠燈：`tests/test_reserved_protection.py` **15 passed**。涵蓋：滯留阻擋新交換；未超過閾值不阻擋；恰好等於閾值不算滯留；已 claim、已派送、已釋放都不阻擋；同 key 重放不被阻擋；其他使用者不受影響；阻擋判斷在 writer gate 之後執行；Owner 清單只含滯留列；非 Owner 被拒絕；合成 `/history` 不拋例外；呼叫端不送 DM；合成入口的阻擋回覆帶標籤、無連結；正式 SQL 文字在 SQLite 上選出相同列。
- 完整離線套件：**2159 passed、79 skipped、0 failed**（5 個 warning 來自 Python 內建 datetime 轉換器的棄用提示，與本輪邏輯無關）。79 項跳過全部是需要隔離開關的資料庫案例。

### 10.3 限制與尚未驗證

- **SQL 只在 fake 與 SQLite 上驗證**，尚未在 MySQL 8.0.46 上執行。下一次實機執行需要包含新的查詢。
- **時間基準**：正式路徑使用應用程式時鐘，`created_at` 由資料庫寫入。兩者必須在同一台主機或時間同步的環境下比較；這是已知限制。
- **競爭**：阻擋判斷與 claim 都在 writer gate 下執行，因此兩者不會交錯。跨程序的實機驗證尚未完成。
- **仍未解決**：滯留列本身不會被自動處理。只有重放或人工處置才能讓它離開 `reserved`。解除阻擋的安全處置尚未設計完成，因此本輪沒有提供任何解除功能。
- **補償**：見 `docs/exchange-compensation-design.md`。本輪只做設計，沒有實作。
- sweep 的未知提交仍會讓整個請求回報未確認（第 4.3 節的建議尚未實作）。

### 10.4 下一個最小驗證步驟

1. 在隔離 3307 實例上，以已核准的流程執行新的 SQL（滯留計數與 Owner 清單）與第一階段的實機案例。這需要另行授權。
2. 由 Owner 決定第 7 節的問題，特別是未 claim 滯留列的長期處置方案（A、B、C、D、E）。
3. 依 `exchange-compensation-design.md` 第 11 節的步驟，先修正零金額補送列與退款掃描器的衝突（C6），再討論 DDL。
