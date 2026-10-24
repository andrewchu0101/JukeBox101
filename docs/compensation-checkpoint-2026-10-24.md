# 補償功能離線 checkpoint 收尾交接（2026-10-24）

**性質**：本階段收尾紀錄，取代「下一步」的現行指引；原交接 `docs/compensation-handoff-2026-10-24.md` 保留為歷史紀錄，不改寫。本文件不記錄自身的 commit SHA，SHA 以 `git log` 為準。本文件不是產品規格，也不代表功能已上線。

**狀態邊界（重要）**
- `compensation_enabled` 預設為 `False`。本 checkpoint 未啟用補償，也未啟用正式 `/exchange`。
- 真實 DM 未驗證；`RecordingSender` 與協定替身不是 Discord。
- 候選 DDL `docs/compensation-candidate-004.sql` **未執行、未註冊**。MySQL、InnoDB 鎖、schema guard 與跨程序競爭**尚未實機驗證**。

## 1. 最終規則（D1／D2／D3）

| 編號 | 規則 | 來源 | 實作與驗證 |
| --- | --- | --- | --- |
| D1 | `not_system_fault`（非系統故障）：**確認原訂單未交付**時，退回原扣款一次、不提供免費補送、完成結案（`non_fault_refunded`）。未確認未交付者不適用，仍留在待決清單。 | Owner 於 2026-10-24 明確決定 | `close_non_fault_refund`：先驗證（`confirmed_undelivered` 必須明確為 `True`，保留須為 `reserved`、未 claim、未釋放且超過門檻），再退款與結案，同一交易。退款失敗整體回滾，可安全重試。重放回傳 `already_closed`。不產生補送資格，不寫零金額 Ledger。 |
| D2 | `uncertain` 結案後**仍保留歌曲持有**（不論 `confirmed_delivered` 或 `confirmed_not_delivered`）；解除帳號整體阻擋；保留 `unknown` 交付事實不改寫。`not_sent` 經 `no_further_action` 結案才釋放歌曲。 | 上一輪 Owner 指定規則 | 依指定規則修正。原先 `confirmed_not_delivered` 會釋放，已改為保留。Owner 若要改為釋放，需另行指示（Q-B 維持保留）。 |
| D3 | 24 小時逾期涵蓋**未結案補送**與**待處理事故**（已核准待結算、非系統故障待決）。只在 Owner 清單標示 `overdue`，不自動退款、解鎖、重送或通知。 | 上一輪 Owner 指定規則 | `open_grants` 與 `pending_incident_rows` 標示逾期；測試確認不改變阻擋或金額。 |

**其他既定規則（未變）**：補償按事故計算，無每人 30 天配額；每事故最多一次免費補送資格；婉拒補送不取消應退原扣款；`not_sent` 結案後釋放歌曲；原訂單 claim 在事故存在時回傳 `held_for_review`，不派送、不重複扣點。

**D1 與 D2 的邊界**：D1 路徑只適用於「非系統故障且確認未交付」的 `reserved`、未 claim、未釋放保留。`claimed`、`uncertain`、`sending` 的保留**不**適用，因此不能藉此退款。D1 不建立補送資格，所以沒有 `holds_song`，也不繞過 D2。既有的定時退款掃描 `retry_pending_refunds` 只掃 `failed` 且未釋放的保留，不會選到 D1 的保留。

## 2. 本階段範圍（已提交的檔案類別）

- **補償程式**：`bot/compensation_delivery.py`、`bot/database/compensation_store.py`、`bot/database/compensation_schema.py`、`bot/database/pending_review.py`；並改動 `bot/database/exchange_delivery_store.py`（claim 守衛 `held_for_review`）、`bot/database/exchange_reservation.py`（保留前的封鎖與功能開關）、`bot/exchange_delivery.py`（`PENDING_REVIEW`、`HELD` 的回覆文字）、`bot/database/submission_repository.py`（Owner 方法與功能開關，只含補償相關區塊）。
- **OperationRouter 測試修正**：`tests/commit_fault_injector.py`（新增 `OperationRouter`）、`tests/test_commit_fault_injector.py`（離線路由測試）、`tests/test_unknown_commit_mysql.py`（只讓保留提交帶故障）。
- **補償測試與替身**：`tests/compensation_sqlite.py`、`tests/test_compensation_{store,gate,repository,schema,disposition}.py`、`tests/test_reserved_protection.py`、`tests/submission_persistence_fake.py`、`tests/exchange_synthetic_acceptance.py`。
- **文件**：`docs/compensation-candidate-004.sql`（候選，未註冊）、`docs/exchange-compensation-model.md`（現行模型）、`docs/compensation-mysql-verification-plan.md`（計畫，未執行）、`docs/exchange-compensation-design.md`（舊設計，已標示失效）、`docs/exchange-reserved-recovery-design.md`（滯留保留的離線分析）、`docs/exchange-isolated-mysql-2026-10-24.md`（既有隔離 MySQL 證據紀錄）、本文件，以及保留的 `docs/compensation-handoff-2026-10-24.md`。

**未提交（刻意排除，維持原樣）**：`CLAUDE.md`、`README.md`、`ROADMAP.md`（另有 CRLF 與內容差異，非本階段）；`.gitignore`、`.env.example`；review 與 reexamination 相關（`bot/review.py`、`bot/owner_review.py`、`bot/database/review_*.py`、`persisted_snapshot.py`、`submission_workflow.py`、`tests/test_review.py` 等）；batch-B 相關（`tests/batch_b_*`、`scripts/*batch-b*`、`docs/batch-b-*`、`tests/conftest.py` 的 assertion guard）；`docs/grilling-decisions.md`、`docs/recovery-batchb-private-beta-spec.md`；僅 CRLF 差異的檔案；個人資訊 PDF（`Re Issue 576880673 ...pdf`，**不得提交**）；`bot/decision_snapshot.py`、`bot/ledger_integrity.py` 及其測試。

## 3. 驗證證據（區分來源與時間）

| 證據 | 結果 | 性質 |
| --- | --- | --- |
| 工作樹完整離線回歸（2026-10-24，Windows `.venv`，`python -m pytest`） | **2228 passed、79 skipped、0 failed** | 本輪實際執行，程式與測試在此之後未再改 |
| 已提交內容的隔離匯出（只含 index，即 HEAD＋本次提交） | 1871 passed、78 skipped、1 failed | 本輪實際執行；數量較少是因為匯出不含未追蹤的 batch-B 與 review 測試 |
| 匯出中唯一失敗：`test_batch_b_recovery_write.py::test_contract_pins_journal_and_manifest[6a75a0…]` | 在乾淨 HEAD 的歸檔中同樣失敗（1 failed，35 passed） | **既有基線問題**，與本次提交無關。清單雜湊與 CRLF／LF 有關，依既有紀錄不盲目改 hash，需獨立調查 |
| MySQL 既有隔離實例（`127.0.0.1:3307`，`jukebox101_test`）exchange 案例 | **分批驗證**：checkpoint 時 21 passed、1 failed；路由修正後單項重驗 1 passed。另有先前 S1 的 14 passed 紀錄 | 分批證據，**不是同一快照一次 22 案例全通過** |
| 補償候選 DDL、新 SQL、schema guard、InnoDB 鎖、跨程序競爭 | **尚未實機驗證** | 僅有 SQLite 鏡像與協定替身的離線結果 |

**TDD 使用範圍（誠實紀錄）**：D1 的測試是在載入 `tdd` skill 後，依紅燈→綠燈循環進行：先寫 5 項 store 測試與 1 項 repository 測試，確認紅燈（`close_non_fault_refund` 不存在），再實作。D2、D3 的測試在本輪前段**未載入 skill**，是依同樣順序手動做的紅燈確認（D2 為「仍釋放」，D3 為「函式不存在」）。本次收尾沒有新增行為，因此沒有新測試。**start-dev-team 在本階段不可用，未使用，沒有獨立團隊審核。**

## 4. 尚未驗證項目

- 候選 DDL 在 MySQL 上的執行、三張表的 CHECK 與 enum 是否被引擎強制。
- `information_schema.STATISTICS` 與 `TABLE_CONSTRAINTS` 的 schema guard 查詢。
- InnoDB 的鎖行為，以及 M1 至 M12 的跨程序競爭（見 `docs/compensation-mysql-verification-plan.md`）。
- 真實 Discord DM 的送出、未知結果與退款；正式 `/exchange` 的啟用。
- 既有 batch-B 清單雜湊基線問題。

## 5. SQLite 鏡像與 MySQL DDL 的型別差異（下一階段核對）

測試鏡像 `tests/compensation_sqlite.py` 已對齊 `approved_at` 與 `settled_at` 為 `DATETIME`。下列欄位在鏡像中仍為 `TEXT`，與候選 DDL 的 `DATETIME(6)` 不同，需在 MySQL 階段人工核對：`exchange_incidents.closed_at`，以及 `exchange_redelivery_grants` 的 `granted_at`、`claimed_at`、`delivered_at`、`disposition_at`、`closed_at`。`SCHEMA_SPEC` 與協定替身仍依規格回答，因此 schema 本身只由鏡像與實機驗證。

## 6. 下一階段唯一入口

**審閱並授權候選 DDL 與隔離 MySQL 驗證。** 順序：

1. 唯讀核對既有隔離基線（`127.0.0.1:3307`、`jukebox101_test`、`schema_version`、既有表與資料量）。
2. 審閱候選 DDL 的 D1 變更（`non_fault_refunded`、`non_fault_refund`、`chk_incidents_non_fault`）與 `closed_at` 等型別差異。
3. 取得有限範圍授權（只限三張新表的 `CREATE TABLE`、既有帳號、既有 DPAPI 憑證重用、啟動器審核、停止與殘留檢查），再依計畫執行 M1 至 M12。

本階段未包含 DDL 執行、MySQL 連線、憑證解密、3306、Discord、真實 DM，也未啟用補償或正式交換。
