# 系統故障補償設計：退回 1 點並免費補送（離線設計，2026-10-24）

**性質**：離線設計與分析。**沒有實作任何補償、退款或派送操作；沒有新增 DDL；沒有執行資料庫或實機測試；沒有啟用正式 `/exchange`。** 所有事實來自目前程式（checkpoint `4f1e3f1` 加上本輪未提交的第一階段保護）。本文件不宣稱補償功能已完成。

> **再次更正（2026-10-24）**：本文件中所有「每使用者 30 天配額」「補償次數上限」「僅因配額不足而退款後結案」的描述均已失效。Owner 已決定補償按事故計算，取消所有使用者或時間窗的配額。現行規則見 `docs/exchange-compensation-model.md`。

> **更正（2026-10-24）**：本文件提出的「零金額 ledger 補送列」方案已被取代。Owner 決定補送是獨立的權利，不寫入財務分錄；退款與補送資格分開建模。目前的實作模型、狀態流程與離線測試見 `docs/exchange-compensation-model.md`。本文件保留為歷史紀錄。

## 1. Owner 的補償方向

> 確認是系統故障造成的未派送訂單，退回原本扣掉的 1 Nickel，並免費補送該次歌曲。

本設計把它拆成三個獨立的財務與派送動作，每個都需要明確的 Owner 核准與可審計的紀錄：

1. **沖回原扣款**：退 1 點，且只沖回原扣款，不額外發點。
2. **免費補送**：不扣點的新保留，只能補送同一首歌。
3. **同一事故最多退款一次、補送一次**。

## 2. 適用範圍：只限「從未 claim 的 reserved」

只有同時符合下列條件的訂單，才能被判定為「未送出」，因此可以進入補償流程：

- `status='reserved'`，且 `claim_token IS NULL`、`claimed_at IS NULL`，因此從未開始 DM 嘗試。
- 未釋放（`released_at IS NULL`）。
- 超過 `STRANDED_AFTER`（第一階段的滯留判定）。

依第一階段的程式，`claim_reservation` 只會把 `reserved` 轉為 `sending`，並在同一把 writer gate 下進行。因此「從未 claim」的列在 gate 之下可以被證明沒有 DM 嘗試。這是本設計能把它當作「未送出」的唯一依據。

**其他狀態不得進入本流程**，即使使用者抱怨沒收到歌：

| 狀態 | 可能的真實情況 | 本流程的處理 |
| --- | --- | --- |
| `reserved`（未 claim） | DM 從未嘗試 | **唯一可進入補償** |
| `sending` | DM 可能已在途或已送達 | 不得補償；等待租約過期（已存在的路徑轉為 `uncertain`） |
| `uncertain` | DM 送達結果未知 | **不得直接重送**；只能由 Owner 人工核對，不自動補送 |
| `delivered` | Discord 已接受訊息 | **不得補送**；這是已送達的訂單，不因使用者抱怨而重送 |
| `failed`（DM 分類為未送出） | 已證明未送出 | 既有退款路徑已可處理；是否補送屬於另一個 Owner 決定 |

關鍵原則：**不得把可能已送達的訂單當作可直接重送。** `uncertain` 與 `delivered` 永遠不在補償範圍內。

## 3. 現有 schema 能否表達這個補償

**不能準確表達。** 逐項核對：

| 需求 | 現有結構 | 結論 |
| --- | --- | --- |
| 記錄 Owner 核准 | 沒有任何核准表 | 缺少 |
| 「未 claim 而失效」的語意 | `recommendations.status` 只有 `reserved`、`sending`、`delivered`、`failed`、`uncertain` | `failed` 的語意是「DM 分類為未送出」。把從未嘗試的列標為 `failed` 會混淆審計，且退款掃描器會把它當成一般 DM 失敗處理 |
| 退 1 點 | `delivery_refund` +1，`uq_ledger_recommendation_reason(recommendation_id, reason)` | **可用**，且 DB 已保證每筆保留最多一次退款 |
| 免費補送（不扣點） | `chk_ledger_reason` 只允許 `exchange_debit`（−1）與 `delivery_refund`（+1），沒有零金額的原因 | **缺少**，需要新原因 |
| 補送的保留 | `recommendations` 可以再建一列；`active_slot` 讓已釋放的同一首歌可再次保留 | 可用，但補送列必須不產生扣款 |
| 一事故一次 | 沒有事故鍵 | **缺少**，需要事故表與唯一鍵 |
| 審計 | 只有 ledger 與 recommendations，沒有事件紀錄 | **缺少** |

### 3.1 會與現有程式衝突的互動（必須先處理）

1. **退款掃描器會撿到零扣款的補送列**：`pending_refund_ids` 選取 `status='failed' AND released_at IS NULL`。若補送列失敗（DM 未送出）並停在 `failed`，每次請求的退款掃描都會對它呼叫 `refund_undelivered`，而 `_single_debit` 找不到唯一扣款，會拋出 `RefundContradiction`。**結論**：補送列必須被排除在退款掃描之外，或 `refund_undelivered` 必須明確辨識零扣款列。這是必須修正的衝突，不是可選項。
2. **轉為 `failed` 的時機必須與退款原子**：若只把從未 claim 的列改為 `failed` 而沒有同時寫入退款，退款掃描器會在之後自動退款，造成未經核准的退款。因此「轉為 failed」與「退款」必須在同一交易內完成，不得留下 `failed` 且未釋放的中間狀態。
3. **補送保留必須與退款同一交易建立**：若退款與補送資格分開提交，中間會出現「已退款、同歌未被持有」的窗口，使用者可以用新交換再抽到同一首歌（付費）。同交易建立補送列可以關閉這個窗口。

## 4. 事故與補償狀態流程（提案）

### 4.1 狀態

| 狀態 | 是否儲存 | 意義 | 金流 |
| --- | --- | --- | --- |
| `detected` | 不儲存（由滯留判定推導） | 從未 claim 且超過閾值的 reserved | 無 |
| `approved` | 需要事故表 | Owner 已核准，尚未執行 | 無 |
| `settled` | 需要事故表 | 同一交易：原列轉為未送出分類、退款 +1、釋放、建立零扣款補送列 | **已退款，已建立補送資格** |
| `redelivery_sent` | 需要事故表 | 補送的 DM 被 Discord 接受 | 無 |
| `redelivery_not_sent` | 需要事故表 | 補送 DM 分類為未送出（已證明未送出） | 無（不再退款） |
| `redelivery_uncertain` | 需要事故表 | 補送 DM 送達結果未知 | 無（**不自動重送**） |
| `closed` | 需要事故表 | 已送達，或 Owner 結案 | 無 |

### 4.2 轉換規則

- `detected → approved`：只能由 Owner，且寫入核准紀錄。條件：原列仍為 `reserved`、未 claim、未釋放（在 gate 下檢查）。
- `approved → settled`：同一交易內完成以下全部動作，任一失敗整筆回滾：
  1. 原列條件更新為「未送出分類」（`reserved → failed`，`claim_token IS NULL` 條件）。
  2. 寫入 `delivery_refund` +1（既有 idempotency 規則）。
  3. 設定 `released_at`。
  4. 建立補送列（`reserved`，`discord_user_id` 相同，同一 `song_id`）。
  5. 寫入零金額的補送 ledger 列（新原因），關聯補送列。
  6. 把事故狀態設為 `settled`，記錄補送列的 ID。
- `settled → redelivery_sent | redelivery_not_sent | redelivery_uncertain`：補送列透過既有 `claim_delivery` → DM → `record_delivery` 路徑處理（同一保護）。
- `redelivery_not_sent → ?`：不得自動再補送。若 Owner 核准再次補送，必須建立新的補送嘗試（新列，零金額），並受 `attempt_no` 上限約束。
- `redelivery_uncertain → closed`：只能由 Owner 人工核對後結案，**不得自動重送**。

### 4.3 禁止的轉換

- `delivered` 或 `sending` 或 `uncertain`（原列）→ 任何補償狀態。
- `settled → settled`（重複退款）。
- 任何路徑在沒有 `approved` 的情況下寫入 `delivery_refund`。
- `redelivery_uncertain → 自動重送`。

## 5. 核准、競爭、重複與程序中斷

| 風險 | 機制 |
| --- | --- |
| Owner 重複核准 | `detected → approved` 以條件更新執行，第二次核准回傳「已核准」，不寫入第二筆 |
| 核准與 claim 競爭 | 核准與 settled 都在 writer gate 下；settled 會再次檢查 `claim_token IS NULL`。若原請求已 claim，settled 失敗且不退款 |
| 核准與重放競爭 | 重放在 gate 下找到同一保留；若已 settled，claim 會因狀態不是 `reserved` 而拒絕，不會派送；`refund_undelivered` 會回傳 `already_released` |
| 退款重複 | `uq_ledger_recommendation_reason(recommendation_id, 'delivery_refund')` 由 DB 強制；`refund:` 的 idempotency 規則也已存在 |
| 補送重複 | 事故表 `UNIQUE(recommendation_id)`（一事故一原列）；補送列的 `active_slot` 與事故表的 `redelivery_recommendation_id` 唯一 |
| 核准後程序中斷 | `approved` 會保留；重試時 settled 在同一條件下執行，已完成的部分不會再次寫入（唯一鍵與條件更新） |
| settled 後程序中斷 | 退款與補送資格已一起提交，不會只有其中之一；補送 DM 之前中斷，補送列仍是 `reserved`，會被滯留判定偵測到，使用者被阻擋，直到補送完成或 Owner 處理 |
| 補送 DM 中斷（sending 過期） | 既有路徑轉為 `uncertain`；不自動重送 |

## 6. 退款後的語意：既有規則是否仍適用

- **released_at / active_slot**：退款後原列不再佔用 `(user, song)` 的有效位置，因此同一首歌可以被新列保留。這正是補送需要的，但也意味著：**若在 settled 與補送列建立之間有任何窗口，使用者可以用新交換（付費）再抽到同一首歌**。第 3.1 節第 3 點的同交易設計可以關閉這個窗口。
- **歌曲持有排除**：`_held_song_ids` 只把未釋放的保留視為持有。退款後原列被釋放，因此原歌曲不再被持有；補送列建立後，它成為未釋放的保留，歌曲重新被持有。這使補送期間的同歌再抽被排除。
- **不得直接套用 `refund_undelivered`**：它要求列為 `failed`，並以唯一扣款作為金額與收款人的唯一來源；零扣款的補送列會觸發 `RefundContradiction`（見 3.1 第 1 點）。補償必須使用同一會計規則，但入口是「已核准的事故」，而不是任意的 `failed` 列。
- **未來同歌再抽**：補送完成後（`redelivery_sent`），該保留持有同一首歌，原本的「同一使用者不得重複持有同歌」規則仍然成立。補送失敗（`redelivery_not_sent`）的列仍是未釋放的 `failed`，因此仍持有該歌曲。這是否合理需要 Owner 決定（見第 9 節）。

## 7. 帳務不變條件（提案，需要實機斷言）

1. 每位使用者：`users.credit_balance = SUM(credit_ledger.credit_delta)`（既有 `lock_reconciled_account`）。
2. 每個事故：`exchange_debit(−1) + delivery_refund(+1) + incident_redelivery(0) = 0`，即事故淨額為零。
3. 每筆原保留：`delivery_refund` 最多一列（DB 唯一鍵）。
4. 每個事故：補送列最多一列（事故表唯一鍵）。
5. 零金額的補送 ledger 列：`credit_delta = 0`，且必須關聯一筆補送保留。
6. 滯留判定與補送：不存在「未 claim 且未釋放且屬於已 settled 事故的原列」（原列必須是 `failed` 且已釋放）。
7. 沒有任何補償路徑增加 Nickel 總量：補償只把既有的 1 點退回原使用者，不產生新點數。

## 8. 經濟影響與濫用防護（誠實說明）

- **退款**：只沖回原扣款，不增加 Nickel 淨供給（點數只是從原先的扣款狀態回到原狀態）。
- **免費補送**：這是一次不扣點的交換，因此使用者實際獲得一首歌而沒有付出點數。這屬於**服務補償的成本**，不是點數供給的增加。**不得宣稱完全沒有經濟影響**：每次免費補送都會讓一首歌的派送不受點數約束。
- **重複索賠**：同一事故只能有一個事故列（唯一鍵）。但使用者可能以不同的事故（不同原列）重複索賠。防護措施：
  - 每次核准必須有 Owner 的理由代碼（例如 `system_fault_confirmed`）。
  - 每位使用者的補償次數上限（需 Owner 決定，例如每 30 天 N 次）。
  - 事故紀錄可統計每位使用者與每個 Owner 的核准次數，供稽核。
- **稽核**：事件表 append-only，記錄核准者、時間、理由代碼、前後狀態、補送結果；不記錄 token、連結或其他使用者的資料。

## 9. 需要 Owner 決定的最少問題

| # | 問題 | 實際影響 | 建議 |
| --- | --- | --- | --- |
| Q1 | 是否接受為補償新增 DDL（事故表、事件表、ledger 新原因、必要時新狀態）？ | 沒有 DDL 就無法準確表達與審計；有 DDL 需要另行授權與可回滾的遷移 | 接受，但先做離線設計與遷移測試，再向你提交遷移請求 |
| Q2 | 補送失敗（未送出）後，是否允許 Owner 核准再補送一次？ | 每次補送都是一次免費派送；允許重試會增加成本 | 預設最多一次補送，失敗後由 Owner 結案 |
| Q3 | 補送結果未知（`redelivery_uncertain`）是否只能人工結案？ | 自動重送可能造成重複派送 | 只能人工結案，不自動重送 |
| Q4 | 補償次數上限與觸發門檻為何？ | 沒有上限會被濫用 | 需要具體數字，建議先設為 1 次 per 使用者 per 30 天，待 Owner 決定 |
| Q5 | 只補償「從未 claim」的列，還是也補償 `failed`（DM 分類為未送出）？ | `failed` 已由既有路徑退款，是否還要補送需要獨立決定 | 第一版只處理從未 claim 的列 |
| Q6 | 補送期間，該使用者是否可以新交換？ | 補送列是滯留的 reserved，依第一階段會阻擋新交換 | 維持阻擋，直到補送完成或結案 |
| Q7 | 核准入口是什麼？ | 目前只有 Owner 專用的 repository 清單，沒有 Discord 指令 | 第一版只提供 repository 介面，Discord 指令需另行核准 |

## 10. 測試矩陣（提案，未實作）

| 編號 | 情境 | 預期 | 層級 |
| --- | --- | --- | --- |
| C1 | 核准 → settled 原子性 | 同一交易：failed 轉換、退款 +1、釋放、補送列、零金額 ledger、事故狀態；中途失敗整筆回滾 | 離線（fake）＋MySQL |
| C2 | 核准重複 | 第二次核准不寫入第二筆 | 離線 |
| C3 | 核准後中斷再重試 | 恰好一次退款與一筆補送列 | 離線＋MySQL |
| C4 | 核准與原請求 claim 競爭 | 已 claim 的列不退款，事故失敗並保留證據 | 離線＋MySQL |
| C5 | 核准與重放競爭 | 重放不派送、不再退款（`already_released`） | 離線＋MySQL |
| C6 | 退款掃描器遇到零金額補送列 | 不拋出 `RefundContradiction`，不退款 | 離線（需修正後） |
| C7 | 補送 DM 未送出 | 不退款，不自動再補送，狀態為 `redelivery_not_sent` | 離線 |
| C8 | 補送 DM 未知 | 狀態為 `redelivery_uncertain`，不自動重送 | 離線 |
| C9 | 補送期間同歌再抽 | 被持有排除，拒絕付費再抽同歌 | 離線（fake）＋MySQL |
| C10 | 非 Owner 核准 | 拒絕，不寫入任何列 | 離線 |
| C11 | 帳務不變條件 | 餘額與 ledger 對帳；事故淨額為零；唯一鍵成立 | MySQL |
| C12 | 稽核事件完整性 | 每個轉換都有一筆 append-only 事件 | 離線＋MySQL |
| C13 | 補償次數上限 | 超過上限拒絕，不寫入 | 離線（需 Owner 數字） |
| C14 | 遷移 | 新表、新原因、新狀態的遷移可回滾或可重建 | MySQL（需 DDL 授權） |

## 11. 下一個最小驗證步驟

1. 由 Owner 回答第 9 節的 Q1、Q2、Q4、Q5（至少四項）。
2. 在離線 fake 中先修正 3.1 第 1 點（零金額補送列不被退款掃描器誤判），並以 C6 的紅綠測試驗證。這不需要 DDL，也不改變財務，只排除一類列，因此可以在 Owner 回答前先處理。
3. 之後才設計事故表與 ledger 新原因的遷移，並提交 DDL 授權請求。
4. 任何實機測試、DDL 或真實 DM 都需要另行授權。
