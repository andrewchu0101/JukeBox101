# 24：交換扣款與派送的重啟恢復驗證

**What to build:** 驗證交換在扣款後、派送前、派送後退款前等時點被中斷，重啟後可辨識狀態，並依既有租約與恢復流程安全處理，不重複扣款或重複派送。

**Blocked by:** 04；隔離資料庫的授權（見下）

**Status:** 離線與專用隔離 MySQL 驗證完成（2026-10-29）；正式環境驗收未執行，待授權

**File owner（實際範圍，2026-10-29 登記）：**
- 本票新增：`tests/test_exchange_restart_recovery.py`（離線，5 項）、`tests/test_exchange_restart_recovery_mysql.py`（專用隔離 MySQL，4 項加參數化）。
- 為讓既有 MySQL 套件在隔離實例上一起通過而改動的測試範圍（只改測試，不改產品程式）：`tests/test_exchange_repository_mysql.py`（期望值改為 `in_progress`，理由為 commit `7990f2d`）、`tests/test_exchange_cross_process_mysql.py`（同上）、`tests/test_compensation_mysql.py`（cleanup 檔名改用 node id）、`tests/conftest.py`（新增 `unix_permission_dir`）、`tests/test_isolated_mysql_target.py`（datadir 改用平台分隔符，比較強度不變）。
- 本票不修改 `bot/` 產品程式。工作樹中的 `bot/` 改動分屬其他工單，見 `docs/tickets/docker-gateway-deployment/README.md`。

**TDD 驗證：**
- 離線（可先做）：以替身資料庫模擬扣款後中斷，重建服務，確認狀態可辨識且不重複扣款。
- 隔離 MySQL（需授權）：並行的兩個程序對同一交換請求，只有一次扣款；租約過期後的恢復不重複派送。

**待驗證的重點：** 租約（目前設為 10 分鐘）是否以資料庫時間戳實作，且在程序重啟後仍有效；此點需實測，不得僅依程式註解判定。
（2026-10-29 答覆：租約以應用程式 UTC 時鐘判定，目前不存在資料庫時鐘實作；詳見下方「租約時鐘（技術結論）」。）

**Acceptance criteria:**
- [x] 離線部分通過，並明確列出未涵蓋的隔離 MySQL 部分（見「限制與尚未涵蓋」）。
- [x] 隔離 MySQL 部分依使用者於本對話的明確授權執行（授權記錄見下方；原文未留存於 repo，不補造）。
- [x] 不連線正式資料庫（依執行結果紀錄：隔離實例 `127.0.0.1:3307`、假值帳號、合成資料；本次未以獨立方式核對連線目標）。

本輪未新增驗收條件。正式環境驗收不在本票範圍內，仍未執行。

## 執行結果（2026-10-29）

**範圍定位**：離線替身與「專用本機隔離 MySQL」（假值帳號、synthetic 資料、`127.0.0.1:3307`、建立時記錄 UUID）上的驗證。**不是正式 MySQL 驗收，也未連接任何正式環境或真實 Discord。**

- **離線（`tests/test_exchange_restart_recovery.py`，5 項）**：扣款後重啟只派送一次；送出中重啟後過租約變 uncertain 且不重送；租約內不被接管；程序時鐘超前或落後時，只改變分類，不重送、不退款、不二次扣款。全部通過。
- **隔離 MySQL（`tests/test_exchange_restart_recovery_mysql.py`，4 項，外加同檔參數化）**：
  - 已扣款並保留的 reservation 在新 repository 重啟後，只派送 1 次，扣款 1 次，退款 0 次。
  - 送出中崩潰後：租約內仍為 `sending`，新程序不重送；過租約後為 `uncertain`，不重送、不退款、不二次扣款。
  - 時鐘超前 2 分鐘：分類為 `uncertain`；落後 3 分鐘：維持 `sending`。兩者都不重送、不退款、扣款仍為 1。
- **隔離套件（`t24e` 實例，全新 schema）**：`tests/test_exchange_restart_recovery_mysql.py`、`test_exchange_mysql_integration.py`、`test_exchange_cross_process_mysql.py`、`test_exchange_repository_mysql.py`、`test_exchange_formal_chain_mysql.py`、`test_unknown_commit_mysql.py`、`test_compensation_mysql.py`、`test_formal_exchange_handler_mysql.py`。**60 passed，0 failed，0 errors，0 skipped，exit 0，291 秒。**
- **證據位置**：`~/.local/share/jb101-iso/run-20261029-t24e/compensation-evidence/`（76 個檔，cleanup plan 與 result；已檢查不含合成憑證）。資料目錄與 `identity-recorded-at-init.txt` 同目錄。

**本輪修正（測試與文件，不改產品行為）**：
- `7990f2d` 之後，「已有未結束交換」先於餘額檢查，相關期望改為 `in_progress`；已確認失敗方沒有重複扣款、派送或補償。
- cleanup plan 檔名改為完整 node id，避免兩個模組的同名測試互相衝突。
- 身分守衛與 `DATADIR_NOT_ISOLATED` 比對強度未放寬。

**限制與尚未涵蓋**：
- 「重啟」以新的 repository 與新連線模擬，不是真正殺掉並重啟獨立程序；真正跨程序的 gate 測試見 `test_exchange_cross_process_mysql.py`。
- 時鐘偏差以注入的 `now` 模擬，不是兩台主機的實際時鐘。
- 未在正式 MySQL、正式 Discord、正式部署環境執行。
- 候選 DDL `docs/compensation-candidate-004.sql` 僅套用於隔離測試 schema（SHA-256 已核對）；它不是已核准的正式 migration。

**狀態**：離線與隔離 MySQL 驗證完成；正式環境驗收未執行，仍待授權。

## 授權記錄（2026-10-29 補記）

- 執行隔離 MySQL（專用實例 `t24e`）、建立乾淨隔離資源、重跑相關套件並完成本工單，是使用者在本對話中的明確授權。
- 授權的原文與確切時間未留存於 repo。本紀錄不補造引文或時間；如需完整留存，可由使用者自行補寫。
- 此補記只是文件缺漏的更正，不代表新增授權，也不涵蓋正式環境、正式 Discord、正式 Migration 或定版。

## 租約時鐘（技術結論，2026-10-29 唯讀核對）

**實際來源（已核對程式）：**
- `bot/database/exchange_delivery_store.py:14`：`DELIVERY_LEASE = 10 分鐘`。
- `bot/database/exchange_delivery_store.py:56-57`：claim 時寫入 `claimed_at`，值為呼叫端傳入的 `now`。
- `bot/database/exchange_delivery_store.py:109`：過期判定為 `claimed_at < now - DELIVERY_LEASE`，`now` 由呼叫端傳入。
- `bot/services.py:152`：生產端 `now` 為 `datetime.now(timezone.utc)`。
- `claimed_at` 欄位為 `DATETIME(6)`（`bot/database/migrations/003_exchange_delivery.sql`）。租約判定不使用 `NOW()`；`UTC_TIMESTAMP(6)` 只出現在 `bot/database/submission_maintenance.py:105`，不屬於租約路徑。

**結論：** 租約以應用程式 UTC 時鐘判定。「資料庫時間戳實測」的前提（資料庫時鐘實作）目前不存在，因此不能用來證明現況。

**注入偏差測試證明了什麼：** `tests/test_exchange_restart_recovery_mysql.py` 的 `test_clock_skew_only_moves_the_classification_on_the_real_database`，在真實隔離 MySQL 上證明：恢復程序的時鐘快 2 分鐘時，列被分類為 `uncertain`；慢 3 分鐘時維持 `sending`。兩者皆不重送、不退款，扣款仍為 1 次。它證明的是「偏差只改變分類」，不證明實際主機時鐘的同步行為。

**偏差會造成的行為：**
- 恢復程序時鐘偏差只改變 `sending` 與 `uncertain` 的分類時點。`uncertain` 沒有自動路徑，因此不重送、不退款，不違反不重複扣款與不重複派送。
- 偏差要造成誤判，必須大到讓仍在進行中的正常嘗試被視為過期。DM 單次嘗試上限為 20 秒（`bot/exchange_delivery.py:19`），因此需要快約 9 分 40 秒以上。
- 遲到結果不會覆寫已是 `uncertain` 的列：`record_result` 以 `status='sending' AND claim_token` 條件更新（`bot/database/exchange_delivery_store.py:94-100`）。`test_a_late_result_never_overwrites_a_newer_terminal_state`（`tests/test_exchange_delivery.py:246`）以 delivered 結果覆蓋此行為。

**是否違反既有資料安全要求：** 不違反「不重複扣款、不重複派送」。但有一個已知的非理想結果，見下方「剩餘風險」。

## 組合案例評估（不新增驗收條件）

工單 TDD 第 13 行將「並行兩程序只扣一次」與「租約過期後恢復不重送」以分號並列。兩者目前分開覆蓋：

- 並行只扣一次：`tests/test_exchange_cross_process_mysql.py` 的兩個獨立 OS 程序案例，以帳本計數斷言只扣一次。此案例只跑到保留與扣款，不含派送與租約。
- 恢復不重送：`tests/test_exchange_restart_recovery_mysql.py` 與離線測試，在同一測試程序內循序模擬重啟。

可能漏掉的交互風險：
1. **另一程序的恢復掃描，在存活中的嘗試期間執行。** 掃描條件只改寫過期的 `sending` 列，且 DM 嘗試上限 20 秒遠小於 10 分鐘租約。只有時鐘偏差大於約 9 分 40 秒時才會誤判。「租約內不被接管」已有離線測試。跨程序版本沒有直接測試，但條件相同，判定為低風險。
2. **遲到的明確 NOT_SENT 結果。** 見下方剩餘風險。此變體與已測的 delivered 變體走同一段 SQL，沒有獨立的失敗變體測試。沒有證據顯示此變體有另外的錯誤。

**結論：** 分開測試沒有發現需要新增組合案例的具體缺口。本輪不新增驗收條件；若 Owner 要求，需另行授權。

## 剩餘風險（2026-10-29 更新）

- **遲到且明確未送出的結果：已修正**（見下方「2026-10-29 修正」）。修正後，同一嘗試的 `NOT_SENT` 可把 `uncertain` 轉為 `failed`，並退回 1 Nickel；其他結果不會改寫已是 `uncertain` 的列。
- **遲到的 `DELIVERED`：已修正**（見下方「2026-10-30 修正」）。同一 claim、帶有效 message_id 的結果，可把 `uncertain` 更新為 `delivered`；不觸發退款或重送。其他 claim 或無 message_id 的結果不改寫 `uncertain`。
- **恢復掃描的誤判來源不只有跨主機時鐘偏差。** 即使單副本，只要某次嘗試（DM 呼叫或整個程序）停滯超過 10 分鐘，恢復掃描仍會把它標為 `uncertain`。跨主機時鐘偏差是另一個來源；目前單副本部署（工單 25）下同一主機時鐘一致，因此該來源不會發生。
- **正式環境時鐘同步與主機行為**未驗證。

## 與授權無關的限制（維持不變）

- 「重啟」以新 repository 與新連線模擬，不是獨立殺掉程序；跨程序行為見上方交叉程序測試。
- 時鐘偏差為注入值，不是兩台主機的實際時鐘。
- 未在正式 MySQL、正式 Discord 或正式部署環境執行。

## 2026-10-29 修正：遲到且明確未送出的退款（2026-10-30 乾淨重跑）

**缺口**：嘗試持有 claim 且 DM 逾時或停滯超過租約期限時，恢復掃描會把列改為 `uncertain`。之後同一嘗試回報明確未送出（`NOT_SENT`）時，舊程式的 `record_result` 只接受 `sending`，結果被丟棄，列留在 `uncertain`，`refund_undelivered` 只接受 `failed`，因此使用者的 1 Nickel 不會退回。離線測試 `test_a_late_explicit_not_sent_after_the_lease_is_refunded_once` 在修正前失敗，流程結果為 `uncertain`、退款為 `None`，重現成立。

**NOT_SENT 是否證明未送出（已對照程式）**：
- 只有兩個產生點：`bot/discord_http.py:223`（建立 DM 頻道失敗）與 `bot/discord_exchange.py:38`（查詢使用者失敗）。兩者都發生在建立訊息之前，因此該嘗試沒有送出任何訊息。
- 結果以 `claim_token` 對應到同一交換與同一嘗試；每筆保留只會被 claim 一次，因此 token 唯一對應一個嘗試。
- 設計文件 `docs/exchange-reserved-recovery-design.md` 的「`failed`：DM 分類為 `NOT_SENT`（證明未送出）」與此相符。

**修正內容**：
- `bot/database/exchange_delivery_store.py` 的 `record_result`：結果為 `failed` 時，可取代同一 `claim_token` 的 `sending` 或 `uncertain`；其他結果（`delivered`、`uncertain`）只可取代 `sending`。條件以常數 SQL 片段組成，參數仍全部綁定。
- `refund_undelivered` 未改動：仍只接受 `failed`，退款仍由 `released_at` 收據保證冪等。
- `UNKNOWN`（逾時、例外）仍為 `uncertain`，不退款、不重送。
- `tests/submission_persistence_fake.py` 同步此條件，使離線假資料庫反映生產語意。

**本輪修正的一項自身回歸**：第一版使用 `status IN %s` 搭配 tuple 參數，`tests/compensation_sqlite.py` 把生產 SQL 轉成 sqlite 執行時報 `near "?": syntax error`，使 `tests/test_compensation_gate.py` 的 3 項失敗。已改為常數 SQL 片段，單獨重跑 `test_compensation_gate.py` 與完整離線套件均通過。此回歸已在修正紀錄中列出，並由後續重跑驗證。

**新增測試**：
- 離線（`tests/test_exchange_delivery.py`，2 項新測試）：
  - `test_a_late_explicit_not_sent_after_the_lease_is_refunded_once`：租約內回收後的遲到 NOT_SENT 退款一次，重複退款不變。
  - `test_a_not_sent_from_a_different_attempt_never_moves_an_uncertain_reservation`：過時 token 的 NOT_SENT 不改寫 `uncertain`，也不退款。
- 專用隔離 MySQL（`tests/test_exchange_restart_recovery_mysql.py`，1 項新測試）：`test_a_late_explicit_not_sent_after_the_lease_is_refunded_once_on_the_real_database`。

**測試結果（2026-10-30，乾淨重跑）**：
- 離線全量：`2673 passed, 143 skipped, 0 failed`，exit 0（2026-10-28 基準為 2671 通過、138 略過，增加的 2 項為新測試）。
- 專用隔離 MySQL（8 檔，`t24e` 實例，證據資料夾 `compensation-evidence-rerun-2026-10-29`）：`61 passed`，exit 0（前次 60 項加上新增 1 項）。

**先前失敗的判斷（目前判斷，不是已證明的根因）**：
- 隔離 MySQL 第一次執行的 39 個錯誤：錯誤訊息為 `COMPENSATION_EVIDENCE_DIR must name an existing evidence folder`，以及 `FileExistsError: .pytest_tmp`。目前判斷是環境設定缺漏與和離線測試並行使用同一暫存目錄。乾淨重跑（獨立證據資料夾、無並行 pytest）通過，支持此判斷；但未分離兩個因素各自的影響。
- 離線全量的 2 項 sqlite `disk I/O error` 與 `readonly database`：目前判斷是當時 MySQL 套件與離線套件同時寫入 `/mnt/d`。乾淨重跑沒有再現，支持此判斷，但未直接證明根因。

**重跑次數**：離線全量共 3 次（第一次 3 項失敗為上述回歸；第二次 2 項 I/O 失敗；第三次乾淨通過）。隔離 MySQL 共 2 次（第一次 39 錯誤；第二次乾淨通過）。未無限重跑，也未擴大修正範圍。

**剩餘風險與限制**：
- 遲到的 `DELIVERED` 結果仍不改寫 `uncertain`（見上方「剩餘風險」），需 Owner 決定。
- `bot/discord_exchange.py` 與 `bot/discord_http.py` 的 NOT_SENT 證明依賴 Discord 回應語意；本輪未在真實 Discord 驗證。
- 隔離驗證不是正式環境驗收；「重啟」以新 repository 模擬。
- 使用者可見的回覆文案（`bot/exchange_delivery.py`）仍有「扣點」字樣，文案變更需 Owner 核准，本輪未修改，並列為待 Owner 文案核准。

## 憑證曝光（2026-10-30 記錄）

- t24e 隔離實例的合成測試憑證，曾在 2026-10-29 的工具輸出中出現於對話紀錄，視為已曝光。
- 該憑證不得重用。未來若重新啟用 t24e，須先更換憑證，且不輸出新值。
- 本紀錄不涉及正式秘密。隔離實例目前已停止。
- 本修正與測試結果僅代表離線及專用隔離範圍通過，不代表正式驗收、新映像驗證或可交付。

## 2026-10-30 修正：遲到的 DELIVERED

**設計依據**：`docs/exchange-reserved-recovery-design.md` 原本只列 `sending → delivered`（僅限持有 claim_token 的 record），`uncertain → delivered` 未定義。經你核准後，設計文件新增此轉移：同一 claim_token、帶有效 Discord `message_id`，才可由 `uncertain` 更新為 `delivered`，寫入 `message_id` 與 `delivered_at`，不觸發退款、不重送、不覆寫其他終態。

**修正內容**：
- `bot/database/exchange_delivery_store.py` 的 `record_result`：結果為 `delivered` 時，與 `failed` 相同，可取代同一 claim 的 `sending` 或 `uncertain`。其他結果仍只能取代 `sending`。常數 SQL 片段命名為 `_OWN_OPEN_ATTEMPT`。
- `DmResult.delivered` 的建構子要求 message_id，因此 DELIVERED 結果必定帶 id。
- `tests/submission_persistence_fake.py` 不需修改（條件字串相同）。

**測試（修正前先失敗）**：
- 離線新增（`tests/test_exchange_delivery.py`）：
  - `test_a_late_delivered_result_from_the_same_claim_records_the_message_and_never_refunds`：修正前失敗（結果為 `uncertain`），修正後通過。
  - `test_a_repeated_delivered_result_changes_nothing_and_keeps_the_first_message`：修正前失敗，修正後通過（冪等）。
  - `test_a_late_delivered_result_from_a_different_claim_never_completes_an_uncertain_reservation`：通過（防護）。
  - `test_a_delivered_result_after_a_refund_never_overwrites_the_released_failure`：通過（退款與送達不能同時成立）。
- 既有測試調整：`test_a_late_result_never_overwrites_a_newer_terminal_state` 原斷言「同一 claim 的遲到 delivered 不得改寫 uncertain」是舊規則，已依核准的新規則改為「不同 claim 不得改寫」，並更名為 `test_a_late_result_from_another_claim_never_overwrites_the_uncertain_state`。原測試的意圖（防止過時或他人結果改寫）保留。
- 專用隔離 MySQL（`tests/test_exchange_restart_recovery_mysql.py`）新增 `test_a_late_delivered_result_from_the_same_claim_is_recorded_once_and_never_refunded_on_the_real_database`。

**測試結果（2026-10-30）**：
- 離線全量（乾淨、單獨執行）：`2677 passed, 144 skipped, 0 failed`，exit 0。
- 專用隔離 MySQL（t24e，8 檔，證據資料夾 `compensation-evidence-rerun-2026-10-30`）：`62 passed`，exit 0。

**憑證**：重用 t24e 前，已更換曝光的合成測試憑證。舊值以 1045 被拒絕，新值可連線；新值只寫入權限 0600 的檔案，未輸出。

**範圍聲明**：本修正與測試結果僅代表離線及專用隔離範圍通過，不代表正式驗收、新映像驗證或可交付。
