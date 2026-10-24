# Exchange 隔離 MySQL 實測 — 2026-10-24

> 最新狀態（2026-10-24，S1）：同一隔離 3307 實例完成 **14 passed**（8 項底層／characterization + 6 項完整 repository 案例），離線 **52 passed、14 skipped**。已正常停止並保留資料；現有憑證已透過 Windows current-user DPAPI 加密保存並成功重用。細節及中途 fixture／憑證問題見文末「S1 執行與修正」。較早「尚未授權 S1／完整 exchange 未測／密碼未保存」均為當時歷史狀態；跨程序與 DM 等仍未驗證。

## 範圍與目標

Owner 已授權建立隔離 3307 實例、套用 schema／002／003、執行 exchange MySQL 整合測試，完成後停止並保留資料。未連線既有 3306、未讀取 .env、未操作 Discord、未啟用正式交換，未提交或推送。

- MySQL：8.0.46，127.0.0.1:3307。
- 完成測試的資料目錄：`.local/mysql-test/20261024-122528/data`。
- 以 `--no-defaults`、獨立 data/tmp/log/pid、loopback 綁定與停用 mysqlx 啟動；DDL 前核對 port、datadir、version、UUID、log、socket。UUID 比對新資料目錄的 auto.cnf。
- 新實例僅新增固定兩個專用帳號：jukebox101_test_migrator（測試庫 DDL）與 jukebox101_test_exchange（測試庫 SELECT/INSERT/UPDATE/DELETE）。初始化的 root 用於認證設定與正常停止。
- 隨機產生的正式密碼僅保留在執行程序記憶體及子程序環境，沒有列印或寫入文件；程序結束後不保留可重用的密碼。初始化日誌中的臨時 root 密碼已被更換，請仍將本機資料目錄視為敏感資料。

## DDL 與測試

手動依序套用 `bot/database/schema.sql`、`bot/database/migrations/002_submission_economy.sql`、`bot/database/migrations/003_exchange_delivery.sql`，全部成功。003 前 sending 列數為 0。未透過既有只接受 3306 的 migration runner，因此不宣稱有其版本／校驗和登錄；本機 run-report.json 記錄腳本雜湊。

實際命令（隔離目標環境由執行程序提供）：

```text
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_exchange_mysql_integration.py -m mysql -v -p no:cacheprovider --tb=short
```

結果：**6 passed，1 failed，0 skipped**。

通過：有效保留唯一鍵、退款釋放後重新保留、sending CHECK、release CHECK、失敗交易回滾、退款恰好一次。

失敗：`test_concurrent_reservations_for_one_song_commit_exactly_once`，第 339 行預期結果為 reserved + 1062，實際為 reserved + 1213（死鎖）。測試在該斷言失敗，後續餘額／保留列數／扣款列數斷言未執行，不能宣稱並行一致性已通過。未修改斷言、未重跑測試，尚未判定是測試預期過窄或正式鎖定／重試設計需要調整。此測試直接呼叫要求 writer gate 的 _reserve，未走完整 reserve_exchange／跨程序 writer gate。

## 停止與保留

- 新實例收到 root SHUTDOWN，日誌 2026-10-24 12:27:16（Asia/Taipei）顯示 Shutdown complete，程序退出碼 0。
- 停止後另外查詢 3307，沒有監聽；資料目錄未刪除。
- 本機證據：`.local/mysql-test/20261024-122528/run-report.json`、`pytest.log`、`error.log`。以上由 Git 忽略。
- 前次 `.local/mysql-test/20261024-122205` 在受限環境初始化並啟動後無法連線，未建立測試帳號、未套用 DDL、未跑測試。當次腳本等待停止逾時，當時報告 server_stopped=false；其後在受限環境外確認原 PID 16816 已不存在、3307 無監聽，再啟動第二次實例。兩個目錄都保留，沒有在未完成的目錄重試 DDL。
- 本輪無權查詢 MySQL80 服務狀態，不能宣稱已驗證其 RUNNING；未對其服務發送控制指令。

## 下一步

先分析並行測試的 1213 與正式 writer gate／帳戶鎖順序，保留此失敗證據。釐清安全性斷言及重試契約後，才決定最小修正與下一次明確測試；不直接把 1213 加到允許清單就宣稱通過。既有 Batch B 3306 測試、完整交換流程、真實 DM 與正式部署均不在本次驗證範圍。

## 續測與測試修正（2026-10-24 12:32）

Owner 授權繼續分析及隔離續測，並要求提供 Claude 交接。未修改 bot/ 正式程式，只調整 tests/test_exchange_mysql_integration.py 的並行測試與輔助函式。

### 釐清

正式路徑為 SubmissionRepository.exchange → submission_transaction（READ COMMITTED、jb2_control 單列 FOR UPDATE）→ reserve_exchange（lock_reconciled_account）→ _reserve。原並行測試直接使用普通交易呼叫 _reserve，略過它要求的 writer gate 與帳戶鎖；因此原先預設一定為 1062 的預期不完整。這是已確認的測試前置條件差異，不代表所有正式流程均無死鎖。

- 保留未經鎖定的競爭測試，允許唯一鍵衝突 1062 或死鎖受害者 1213，但必須只有一方成功，且獨立查核只有一筆有效保留、一筆扣款、餘額 0。不是只放寬錯誤碼。
- 新增使用正式 submission_transaction 與 lock_reconciled_account 的競爭測試，嚴格要求 reserved + 1062，再查核相同三項不變條件。暫時建立的 jb2_control 測試列由測試移除；不覆寫既有 control。
- 兩項仍直接呼叫底層 _reserve，不是完整 reserve_exchange／snapshot／選歌／跨程序流程，也沒有測量所有並行排程。

### 本輪實際結果

- 隔離守衛及預設 opt-out：11 passed、8 skipped。8 項整合測試因未開連線開關而跳過，沒有碰資料庫。
- 同一既有隔離實例、相同 UUID／資料目錄、既有兩個帳號，未新增帳號、未重套 DDL：**8 passed，0 failed，0 skipped（5.51 秒）**。
- 真實測試命令同上節，輸出保留為 pytest-retest.log，身分與停止結果保留為 retest-report.json。先前 6 passed／1 failed 的 pytest.log 與 run-report.json 保留，沒有覆蓋成成功。
- 離線回歸：tests/test_exchange_reservation.py、tests/test_exchange_delivery.py、tests/test_exchange_synthetic_acceptance.py、tests/test_isolated_mysql_target.py：**52 passed，1 個既有 audioop 棄用警告（1.05 秒）**。沒有開 MySQL 開關。
- 離線回歸第一次在受限環境進行 9 項後無進度，已中斷；同一命令在受限環境外迅速完成。不能將第一次中斷列為通過，也沒有據此修改產品。

### 帳號重用與停止

前輪密碼未持久保存，因此先用 --skip-grant-tables／--skip-networking 與專用本機 named pipe 重設既有 root 及兩個測試帳號密碼，未新增帳號，未讀取 .env，未連線 3306。第一次重設後，另開連線關閉恢復程序失敗，已核對隔離 datadir、禁用網路及專用管道後，僅強制停止該隔離恢復的 PID 8800／10660。其後重新啟動本機恢復程序，在同一既有管理連線重設密碼並 SHUTDOWN，正常停止後再啟動 3307 續測。此管理操作失敗不是測試重跑；第一次失敗沒有開始 pytest。

續測後透過 SQL SHUTDOWN 正常關閉，日誌 12:32:24（Asia/Taipei）顯示 Shutdown complete，程序退出碼 0；額外程序／監聽檢查確認 3307 無監聽，且沒有命令列指向該隔離目錄的 mysqld。資料目錄均保留未刪除。

現有兩個帳號仍存在，但密碼仍只在程序記憶體中、沒有保存。未來若要頻繁重用，應先規劃使用 Windows 保護的本機憑證保存或由操作者注入密碼，避免每輪走恢復程序；不得捏造已具備可直接重啟的憑證。沒有提交或推送。

### 尚未涵蓋

完整 SubmissionRepository.exchange／snapshot／writer gate 跨程序整合、未知提交結果恢復、全部 Batch B MySQL 矩陣、真實 DM、既有 MySQL80 狀態與正式部署均未由本輪驗證。下一步建議先由 Claude 審核這次測試前置條件與不變條件，再補完整 repository 邊界的隔離測試；不直接啟用正式交換。

## Claude 審核紀錄（2026-10-24）

**性質**：離線審核與文件核對，本段沒有啟動資料庫、沒有重跑 MySQL 測試。8 passed 的結論依 `pytest-retest.log`（`8 passed in 5.51s`）與 `retest-report.json`（`server_stopped=true`、`server_exit_code=0`、`ddl_reapplied=false`、`new_accounts=0`）核對；證據檔案存在且由 Git 忽略。`error.log` 含初始化臨時密碼，本審核未讀取其內容，也不得提交或公開。

### 1. 測試調整是否符合正式鎖定契約

- **方向正確**：保留首次失敗（6 passed／1 failed）的證據，沒有只放寬錯誤碼；新增的 guarded 測試使用正式 `submission_transaction`，符合正式 writer gate。
- **guarded 測試的組合與正式路徑不一致**（主要發現）：正式路徑 `SubmissionRepository.exchange` → `submission_transaction`（取得 `jb2_control` 的 FOR UPDATE）→ `reserve_exchange`。`reserve_exchange` 在 writer gate 之後會呼叫 `lock_reconciled_account`，餘額不足時回傳 `INSUFFICIENT_CREDITS`，**不會執行到 `_reserve` 的 INSERT**。guarded 測試跳過了這個餘額判斷，直接呼叫 `_reserve`，所以它的 `reserved + 1062` 是此組合特有的結果，**不是正式契約**。正式的預期應是「只有一方保留；另一方為 `insufficient_credits` 或重放，且沒有資料庫錯誤」。目前的 1062 斷言應標示為組合測試，或改寫成完整 `exchange` 的測試。
- **unguarded 測試**（允許 1062 或 1213）：只能證明無 gate 的原始 `_reserve` 路徑不會雙扣。它記錄的是非正式路徑的行為，不應被當作正式契約。建議改名或明確標示為 characterization，並在文件中註明它不代表正式支援的路徑。
- **1213 的來源**：原始路徑的死鎖是鎖順序問題（唯一索引的插入與帳戶更新之間），正式路徑經 writer gate 串行化，因此不應出現死鎖。這點需要在下一輪的完整測試中實際確認，本段不宣稱。

### 2. 財務不變條件是否充分

`assert_single_committed_reservation` 目前檢查三項：有效保留恰好一筆、餘額為 0、`exchange_debit` 恰好一筆。**不足之處**：
- 未檢查 `credit_ledger` 的總和等於 `users.credit_balance`。這是退款與正式交換共同的前提（`lock_reconciled_account` 即以此判斷），應加入。
- 未檢查敗方沒有任何殘留的 ledger 或保留列（目前只間接由「有效保留一筆」與「扣款一筆」推得）。
- 未檢查餘額不為負（`chk_users_balance` 已在結構層保證，但測試未明示）。

建議在下一輪的斷言中加入 ledger 對帳，並保留目前三項。

### 3. 交接與進度

- 本文件的 8 passed 只適用於**隔離實例、所列的 8 項測試**，不代表完整 `exchange`、跨程序 writer gate、未知結果恢復、Batch B 全矩陣或真實 DM。
- 高權限操作（`--skip-grant-tables`、`--skip-networking` 重設密碼，以及強制停止隔離恢復程序 PID 8800／10660）已如實記錄。這些操作只限隔離實例，但屬人工介入，後續應改為經審核的腳本，而不是每輪手動處理。
- 密碼目前不持久保存；下一輪重用實例前，需要先決定憑證保存方式（例如 Windows 保護的本機憑證，或由操作者在執行時輸入）。
- 本審核沒有修改任何測試或正式程式。離線回歸在本審核中重跑：`tests/test_exchange_mysql_integration.py`、`tests/test_isolated_mysql_target.py`、`tests/test_exchange_reservation.py`、`tests/test_exchange_delivery.py`、`tests/test_exchange_synthetic_acceptance.py` 共 **52 passed、8 skipped**（skipped 為未開啟隔離開關的真實 MySQL 測試）。

### 4. 下一個最小驗證範圍（建議，尚未授權執行）

**範圍 S1：完整 `SubmissionRepository.exchange` 的隔離整合測試**（只在隔離 3307 實例、`jukebox101_test` 上執行，沿用既有兩個帳號，不新增 DDL）。

- 種子資料：沿用 `tests/intake_fixture.py` 的 `seed_intake`（已經透過 `submission_transaction` 與 `repository._factory`，可接真實工廠）、`tests/test_identity_service_path.py` 的 `confirm_version`、`approve`，以及 `repository.settle`。唯一需要改寫的是 `tests/test_exchange_reservation.py` 的 `verify_all_songs`，它直接操作 FakeDatabase 的 `state`，需改為對 `songs` 執行 UPDATE。
- 案例與預期結果（以 `ExchangeOutcome` 的狀態判定）：
  1. 首次請求且有候選 → `RESERVED`，餘額減 1，ledger 對帳成立。
  2. 同一 `request_key` 重放 → `REPLAYED`，不再扣款。
  3. 餘額為 0 → `INSUFFICIENT_CREDITS`，無新列。
  4. 同一使用者兩個不同請求並行 → 一個 `RESERVED`、一個 `INSUFFICIENT_CREDITS`（或同 key 的 `REPLAYED`），**不得**出現 1062 或 1213。
  5. 無可用候選 → `NO_CANDIDATE`，餘額不變。
- 每個案例都要求 ledger 對帳成立（`SUM(credit_delta) = credit_balance`）與無孤兒列。

**範圍 S2（之後）**：跨程序 writer gate（兩個獨立程序）。
**範圍 S3（之後）**：未知提交結果恢復、Batch B 全矩陣、真實 DM 與正式部署，都需要各自的授權與範圍。

**執行授權需要的資料影響**：只寫入 `jukebox101_test` 的合成資料（既有 `jukebox101_test_exchange` 帳號已具備 DML 權限，不需要新帳號與 DDL）。執行前需要你重新提供憑證，不會保存到檔案。


## S1 執行與修正（2026-10-24）

### 授權與實作

Owner 明確授權 S1 以及 Windows 使用者加密保存隔離憑證，只使用既有 3307／jukebox101_test 與兩個帳號，不新增帳號、不執行 DDL，完成後停止並保留資料。

- 修改 `tests/test_exchange_mysql_integration.py`：兩個直接呼叫 _reserve 的並行測試明確命名為 characterization。原 locked 1062 斷言保留為低層插入的觀察，不當成正式交換預期。共用帳務斷言新增非負餘額、Ledger 總和對帳、無孤兒保留／扣退款列。
- 新增 `tests/test_exchange_repository_mysql.py`：透過 seed_intake → Owner 版本確認 → 核准 → settle 建立真實候選，歌曲驗證以限定影片的 SQL UPDATE 完成，交換呼叫完整 SubmissionRepository.exchange。6 項案例涵蓋首次 RESERVED、同 key REPLAYED、零餘額 INSUFFICIENT_CREDITS、本人版本唯一候選時 NO_CANDIDATE、不同 key 並行 RESERVED + INSUFFICIENT_CREDITS、同 key 並行 RESERVED + REPLAYED。每項驗證 Ledger 對帳、非負餘額及無孤兒財務列；拒絕請求另核對其 ledger key 未寫入。
- 未修改 bot/ 正式程式。兩個並行 worker 是不同連線的執行緒，不是獨立程序。

### 中途問題與保留證據

1. 第一次 S1（`pytest-s1.log`、`s1-report.json`）：**9 passed、6 errors**。第一個 repository 交換的斷言成功，但 teardown DELETE jb2_versions 遇自我參照 FK 的 1217，清理交易回滾；另外五個案例由非空 baseline 守衛拒絕，沒有略過守衛硬跑。該次實例正常停止。
2. 修正清理策略：不更動外鍵，不關閉 FK 檢查，不刪版本根。僅允許兩筆精確的合成根 `(s1-original-a, s1-original-a, s1-proof-1)` 與 `(s1-original-b, s1-original-b, s1-proof-2)` 留存；其餘業務表必須為空。清理只觸及本 fixture 的使用者、影片、操作及 Owner 決策；決策由 result_json 的影片主體與 Owner 核對，避免含引用的 audit reason 導致漏清。第一次修正後清理發現 jb2_decisions 殘留即中止、正常停止，未執行 pytest；詳見 s1-fixed-fixture-report.json。沒有放寬成允許未知殘留。
3. 最初 PowerShell 子程序的加密檔為空，後續嚴格錯誤檢查發現其安全模組無法載入。中途 credential reset 僅改既有帳號，未新增帳號，恢復程序在同一管理連線 SHUTDOWN。失敗的解密嘗試在啟動服務前即停止。最終改用 Windows 原生 CryptProtectData／CryptUnprotectData，以假字串驗證往返後再保存真實憑證，並驗證真實明文往返相等，從未列印明文。最終 S1 使用已保存密文解密成功啟動，沒有再重設帳號。

### 最終實際結果

```text
python -X utf8 -m pytest tests/test_exchange_mysql_integration.py tests/test_exchange_repository_mysql.py -m mysql -v -p no:cacheprovider --tb=short
14 passed in 18.35s
```

- 8 項底層／characterization 與 6 項完整 repository 案例皆成功。pytest-final 證據為 `.local/mysql-test/20261024-122528/pytest-s1-final.log` 及 `s1-final-report.json`（pytest_exit_code=0、server_stopped=true、server_exit_code=0、ddl_reapplied=false、new_accounts=0）。歷史失敗輸出保留，未以成功輸出覆蓋。
- 最終離線回歸：上述兩個 MySQL 檔，加 test_isolated_mysql_target、test_exchange_reservation、test_exchange_delivery、test_exchange_synthetic_acceptance：**52 passed、14 skipped、1 個 audioop 棄用警告，1.15 秒**。14 skipped 全部為沒有開啟連線開關的資料庫案例；不是實機失敗或跳過實機測試。
- 目標仍為原 MySQL 8.0.46、127.0.0.1:3307、相同 UUID／資料目錄。最終測試程序只使用既有 exchange 帳號；無新帳號、無 DDL、無 .env、無 3306 連線。
- 日誌 2026-10-24 **12:45:55 Asia/Taipei** 顯示 Shutdown complete；退出碼 0。另查 3307 無監聽，無命令列指向該隔離資料目錄的 mysqld。
- 最終 fixture 已清理業務資料並核對 baseline；刻意保留上述兩筆未使用的合成版本根，整個資料目錄未刪除，不宣稱全庫零列。
- 憑證保存為同 run 下的 `credentials.dpapi`（Windows current-user DPAPI 原生二進位密文）；已成功解密重用，Git check-ignore 已確認排除。僅同 Windows 使用者及原機器環境可用，不應提供給 Claude／聊天或提交。忽略的本機 DPAPI helper 為 `.local/mysql-test/dpapi_credentials.py`；最終可重用啟動輔助為 `s1_resume_authorized.py`。較早的實驗輔助檔有失敗路徑，不應當作推薦啟動器。

### 下一步與限制

S1 的六個案例已驗證，但不等於交換所有可能路徑或正式上線就緒。下一步是由 Claude 審核 baseline、兩筆版本根留存策略、帳務不變條件與實際結果，再準備 S2 的兩個獨立程序 writer gate 測試。S2 尚未授權或執行；未知提交結果恢復、完整 Batch B 真實矩陣、真實 DM、正式 /exchange 均未由本輪驗證。未提交、推送或修改既有 MySQL80。

## Claude 審核 S1 與 S2 準備紀錄（2026-10-24）

**性質**：本段為離線審核與文件更新，沒有啟動 MySQL、沒有連線資料庫、沒有重跑 S1，也沒有執行 S2。S1 的 14 passed 依 `pytest-s1-final.log`（`14 passed in 18.35s`）與 `s1-final-report.json`（`pytest_exit_code=0`、`server_stopped=true`、`server_exit_code=0`、`ddl_reapplied=false`、`new_accounts=0`）核對。`pytest-s1.log` 的 9 passed、6 errors 保留為失敗證據。證據檔存在且由 Git 忽略；`credentials.dpapi` 未讀取其內容。

### 1. S1 是否符合正式交換契約

**符合之處**
- 完整交換走 `SubmissionRepository.exchange`，即 `submission_transaction` 的 writer gate → `reserve_exchange`，並經過重放檢查、`lock_reconciled_account`、候選抽選與 `_reserve`。案例的狀態判定（RESERVED、REPLAYED、INSUFFICIENT_CREDITS、NO_CANDIDATE）與 `reserve_exchange` 的回傳順序一致。
- 並行案例的期望來自正式路徑：不同 key 時，餘額不足的一方應得 `INSUFFICIENT_CREDITS`；同 key 時，另一方應得同一筆保留的 `REPLAYED`。兩者都不應出現 1062 或 1213，這與 S1 的觀察一致。
- 底層測試已正確改名為 `test_characterization_*`，docstring 明確寫出 1062 不是 `SubmissionRepository.exchange` 的契約。

**需要留意的小點**
- `test_repository_exchange_reserves_other_users_candidate` 的連結斷言（`youtube_url == submission(2).source_url`）是同義驗證：測試本身已把 `youtube_url` 設為 `source_url`。它證明交付連結來自 `songs.youtube_url`，但不證明連結正規化或驗證流程。

### 2. 帳務斷言是否充分

已新增並確認的項目：非負餘額、`credit_ledger` 總和等於 `users.credit_balance`、無孤兒 exchange_debit／delivery_refund 列、無沒有扣款的保留列。

**仍缺的項目（建議下一次授權時補上）**
- **保留與退款的一致性**：目前沒有檢查「`released_at` 非空 ⇔ 存在 delivery_refund」。若只有 release 而沒有退款，現行斷言會通過。
- **敗方殘留**：S1 目前以 `recommendations` 與 `exchange_debit` 的數量推得敗方沒有殘留，沒有直接查詢「被拒絕請求的 recommendation 或 ledger 列為零」，只檢查了 ledger key。

### 3. 清理與保留兩筆版本根的策略

- **做得好的部分**：沒有修改外鍵、沒有關閉 FK 檢查；清理只刪除 fixture 擁有的使用者、影片、操作與決策；決策依 `result_json` 的影片主體與 Owner 核對，而不是依文字理由；基線守衛在每個測試開始前執行，且只接受兩組精確的版本根元組。任何其他殘留都會使測試失敗，而不是被跳過。
- **仍有風險**：保留的 `s1-original-a`、`s1-original-b` 讓後續測試共享前一輪的狀態。第二個測試透過 `confirm_version` 重用同一版本根，這是否掩蓋了缺陷，目前無法排除。這不是放寬到允許未知資料，但它確實讓測試之間不再完全獨立。
- **建議**：由於這是一次性的隔離資料庫，下一次若獲授權，最乾淨的做法是重建 `jukebox101_test`（DROP／CREATE 後重套 schema、002、003）。這只影響隔離庫，但屬於 DDL，需要另行授權。在此之前，應把版本根的殘留視為已知限制，不宣稱資料庫為全新狀態。

### 4. 進度分層（取代「8 passed」的單一敘述）

| 層級 | 內容 | 狀態 | 證據 |
| --- | --- | --- | --- |
| 底層 characterization（8 項） | 保留唯一鍵、釋放後保留、CHECK、回滾、退款一次、底層並行（僅作觀察） | 已在隔離實例通過 | `pytest-retest.log`、`pytest-s1-final.log` |
| 完整 repository S1（6 項） | 首次 RESERVED、REPLAYED、INSUFFICIENT、NO_CANDIDATE、不同 key 並行、同 key 並行 | 已在隔離實例通過（同一程序內的執行緒） | `pytest-s1-final.log`、`s1-final-report.json` |
| S2 跨程序 writer gate（4 項） | 閘門阻擋、閘門超時、不同 key 跨程序、同 key 跨程序 | **已寫、未執行** | 尚無 |
| 尚未驗證 | 未知提交結果恢復、完整 Batch B 矩陣、真實 DM、正式 `/exchange` 啟用、正式部署 | 未驗證 | — |

### 5. S2 準備內容（離線，尚未在資料庫執行）

- `tests/exchange_worker_process.py`：單一請求的子程序入口。啟動前先執行隔離目標檢查（未授權則 fail closed）；寫入 ready 檔後等待 go 檔；完成後以 JSON 輸出業務結果或 MySQL 錯誤碼，不輸出憑證或目標身分。
- `tests/test_exchange_cross_process_mysql.py`（`mysql` 標記，預設跳過）：
  1. `held_writer_gate`：父程序自己以 `SELECT ... FOR UPDATE` 持有 `jb2_control`，子程序在閘門上被阻擋，觀察 1 秒後子程序仍在執行（直接觀察阻擋，而非依賴時間推斷）；釋放後子程序應得 RESERVED。
  2. 閘門持有超過 `LOCK_WAIT_SECONDS`（2 秒）：子程序應得 1205，且沒有任何保留或扣款列。
  3. 不同 key 的兩個獨立程序：一個 RESERVED、一個 INSUFFICIENT_CREDITS，無 SQL 錯誤。
  4. 同 key 的兩個獨立程序：一個 RESERVED、一個 REPLAYED，且 recommendation 相同。
- `tests/test_exchange_worker_process.py`（離線，無資料庫）：子程序在沒有隔離開關時 fail closed，沒有建立 ready 檔，stdout 為空。**已執行並通過**。

**S2 的已知風險**
- 第 1 項的 1 秒觀察窗必須小於 2 秒的鎖等待上限。若機器過慢，子程序會得到 1205，測試會失敗，而不會假通過。
- 子程序會繼承父程序的環境變數（含隔離憑證），但程式不會輸出它們。
- 子程序是獨立 Python 程序，失敗時父程序會強制結束它們，避免殘留。

### 6. 離線檢查（本段實際執行）

`tests/test_exchange_worker_process.py`、`tests/test_exchange_cross_process_mysql.py`、`tests/test_exchange_mysql_integration.py`、`tests/test_exchange_repository_mysql.py`、`tests/test_isolated_mysql_target.py`、`tests/test_exchange_reservation.py`、`tests/test_exchange_delivery.py`、`tests/test_exchange_synthetic_acceptance.py`：**53 passed、18 skipped**。18 項跳過的全部是未開啟隔離開關的資料庫案例。

### 7. 下一步

- **S2 授權請求**：執行上述 4 項跨程序測試，沿用同一隔離實例與兩個既有帳號，不新增帳號、不重套 DDL。需要你在執行時重新提供憑證（或使用 DPAPI 密文，由你在本機確認）。
- **在 S2 之前或同時，建議補充**：「釋放 ⇔ 退款」的一致性斷言，以及敗方殘留的直接查詢。
- **之後的範圍**：持有／已交付歌曲排除、退款後同歌重新可選（repository 層）、pending 或 rejected 歌曲排除、衝突版本排除，目前都沒有覆蓋。
- **仍未驗證**：未知提交結果恢復、完整 Batch B 矩陣、真實 DM、正式 `/exchange`。8 項底層與 6 項 repository 的通過，不代表正式上線就緒。

## S2 執行前測試補強與審核（2026-10-24）

**性質**：本段為程式、測試與文件修改，以及離線測試。**未啟動 MySQL、未解密憑證、未連線資料庫、未執行 S1 或 S2、未執行 DDL、未增權、未碰 Discord 或真實 DM。** 下列 64 passed／18 skipped 是本段離線執行的結果，不是資料庫重跑，也不沿用上一輪的數字。

### 技能使用

- `tdd`：可用，本段依其規則進行垂直切片（先寫測試、確認紅燈、再做最小實作）。
- `start-dev-team`：**本次環境不可用**（不在可用技能清單中），因此未使用，也未委派獨立審核代理人。本段的審核由同一會話完成。

### 1. 帳務斷言（TDD 紀錄）

- **紅燈**：`tests/test_financial_invariants.py` 先於實作撰寫，因模組 `tests/financial_invariants.py` 不存在而收集失敗。
- **綠燈**：實作最小的命名檢查（`account_violations`、`legacy_violations`、`footprint`）。離線測試 11 項通過。
- **舊斷言漏判的離線反例**（SQLite 上執行相同的 SQL 文字）：
  - 只釋放未退款（`RELEASE_WITHOUT_REFUND`）：舊的四類檢查回報無違規，新檢查回報違規。
  - 已退款未釋放（`REFUND_WITHOUT_RELEASE`）：舊檢查靜默，新檢查回報。
  - 退款金額不等於原扣款（`REFUND_AMOUNT_MISMATCH`）：舊檢查靜默，新檢查回報。
  - 退款記入其他收款人（`ORPHAN_LEDGER`）：依收款人關聯回報。
- **舊有缺陷仍被偵測**：負餘額、Ledger 不一致、孤兒列、保留無扣款、帳戶缺失，都由新檢查回報，legacy 子集也保留這些代碼。
- **注意**：這些反例證明的是 SQL 語意在離線引擎上的行為，**尚未在 MySQL 上執行**。
- MySQL 測試的 `assert_account_reconciled` 已改為呼叫 `account_violations`，失敗時列出違規代碼。

### 2. 被拒絕交換的「無新增列」證明

- schema 的 `recommendations` 沒有 request_key 欄位，`credit_ledger` 的 `idempotency_key` 是請求的單向雜湊，只對**成功扣款**的請求有意義。因此「沒有該 ledger key」**不能**直接證明沒有保留，也不能證明沒有任何財務列。
- 改為前後集合差異：`fixture_footprint` 擷取測試使用者範圍內全部 `recommendations.recommendation_id` 與 `credit_ledger.ledger_id`。被拒絕的請求前後集合必須相等；並行案例中，敗方不得寫入任何列（新增保留與新增 ledger 各僅 1 列）。
- 移除了舊的 `assert_request_absent`（ledger key 檢查）。

### 3. S2 四個案例的審核

**同步機制是否代表所需階段**
- 子程序的 ready 檔代表「通過隔離目標檢查、已 import 完成、尚未連線資料庫」。
- go 檔之後才寫 entered 檔，entered 位於 `SubmissionRepository.exchange` 呼叫之前。**本次新增的 entered 標記**讓父程序能確認子程序已進入交換入口，之後才開始觀察窗。
- 「子程序在 writer gate 等待」的證據是推論：entered 已出現、父程序持有閘門、子程序仍在執行。交換在閘門之前沒有其他會阻塞的鎖，因此仍在執行可合理推定為在閘門等待。
- **直接觀察鎖等待需要監控權限**（`performance_schema.data_lock_waits` 或 `PROCESS`），現有 `jukebox101_test_exchange` 帳號**沒有**這些權限，本段不增權、不新增帳號，因此不做直接觀察，並在測試 docstring 與文件中明示。

**1 秒觀察窗與 2 秒鎖等待的競態**
- 觀察窗（1 秒）必須小於 `LOCK_WAIT_SECONDS`（2 秒）。若父程序在子程序開始等待後超過 2 秒才釋放，子程序會得到 1205，測試會失敗，這是失效安全（fail-safe），不會假通過。
- 逾時測試的持有時間為 `LOCK_WAIT_SECONDS + 1` 秒，子程序的等待起點在 entered 之後，因此逾時會先於釋放發生。

**1205 的定位**
- 1205 是受控的鎖逾時，屬於資料庫拒絕，**不是業務結果**，不得與 RESERVED、REPLAYED、INSUFFICIENT_CREDITS 混同。測試以完整結果字典 `{"status": None, "recommendation_id": None, "error_code": 1205}` 斷言，並以前後集合差異證明沒有部分寫入。

**失敗處理**
- 父程序使用 `workers_for` 上下文，無論通過或失敗都會終止所有子程序。
- 閘門上下文在成功時 commit、失敗時 rollback，並且一定 close 連線，因此失敗不會遺留鎖。
- 子程序若在 ready 前失敗，`wait_for_file` 會立即回報其 stderr（不等待逾時）。
- 子程序的 `go` 等待有 30 秒上限，父程序若不釋放，子程序會自行失敗。

**無隔離開關的拒絕**
- 子程序的第一個動作是 `isolated_settings_from_environment()`，在建立 ready 檔與任何資料庫連線之前拒絕。離線測試 `tests/test_exchange_worker_process.py` 已驗證（無 ready 檔、stdout 為空、錯誤代碼為 `ISOLATION_NOT_AUTHORIZED`）。
- 整合測試檔的 `skipif` 會在夾具執行前跳過，因此沒有隔離開關時不會建立任何連線。

**四個案例**
1. **閘門阻擋後完成**：子程序在閘門釋放前仍在執行，釋放後得 RESERVED，且 `assert_repository_state(1, 0)`。
2. **超過上限**：得 1205，前後集合不變，餘額仍為 1。
3. **不同 key**：一個 RESERVED、一個 INSUFFICIENT_CREDITS，敗方不寫入任何列（新增保留 1、新增 ledger 1）。
4. **同 key**：一個 RESERVED、一個 REPLAYED，指向同一筆保留，重放不寫入任何列。

### 4. S2 可審核執行方案（尚未授權執行）

**前置條件**
- 隔離實例已依既定程序啟動（沿用資料目錄 `.local/mysql-test/20261024-122528/data`，127.0.0.1:3307）。
- 目標核對：`python -m tests.isolated_mysql_target verify` 通過（`SERVER_PORT_MISMATCH`、`DATADIR_NOT_ISOLATED` 等任一錯誤即停止）。
- 基線：僅允許兩筆合成版本根 `(s1-original-a, s1-original-a, s1-proof-1)` 與 `(s1-original-b, s1-original-b, s1-proof-2)`；其餘表必須為空。
- 憑證以執行時的既定方式提供（DPAPI 密文由你授權後載入；本段未解密）。

**所需權限（既有帳號，不增權）**
- `jukebox101_test_exchange`：測試庫 SELECT／INSERT／UPDATE／DELETE（已具備）。`SELECT ... FOR UPDATE` 於 `jb2_control` 與 `users` 使用既有 DML 權限。
- **不需要**：PROCESS、performance_schema、information_schema 以外的監控權限、任何新帳號、任何 DDL。

**實際命令（未執行）**
```text
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_exchange_cross_process_mysql.py tests/test_exchange_repository_mysql.py tests/test_exchange_mysql_integration.py -m mysql -v -p no:cacheprovider --tb=short
```
建議**同一次執行**跑 S1 與 S2，因為帳務斷言與完整 repository 案例已改變，不能沿用上一輪的 14 passed。

**資料影響**
- 寫入與刪除限於測試使用者範圍 `900000000000000100–199`、帶有固定標記的合成歌曲，以及測試建立並移除的 `jb2_control` 列。
- 兩筆合成版本根保留（已知限制，不擴大）。
- 不執行 DDL，不關閉外鍵檢查，不刪除資料目錄。

**停止條件**
- 目標核對或基線失敗、migration 003 形狀檢查失敗：停止，不進行測試。
- 任何子程序失敗且不是預期的 1205 或業務結果：保留完整 stderr，停止。
- 任何前後集合差異不符預期：停止，不以重跑覆蓋。
- 測試結束後若仍有子程序或 3307 監聽：停止並回報。

**清理**
- 測試結束後由夾具清除本檔的使用者、影片、操作與決策；兩筆版本根依基線保留。
- 停止實例與清理資料目錄需另行確認，本段不執行。

### 5. 離線檢查結果（本段實際執行）

| 檔案 | 結果 |
| --- | --- |
| `tests/test_financial_invariants.py` | 11 passed |
| `tests/test_exchange_worker_process.py`、`tests/test_isolated_mysql_target.py` | 含於下列合計 |
| 上列加上 `tests/test_exchange_cross_process_mysql.py`、`tests/test_exchange_mysql_integration.py`、`tests/test_exchange_repository_mysql.py`、`tests/test_exchange_reservation.py`、`tests/test_exchange_delivery.py`、`tests/test_exchange_synthetic_acceptance.py` | **64 passed、18 skipped、1 個 audioop 棄用警告** |

- 18 skipped 全部為未開啟隔離開關的資料庫案例，不是實機失敗。
- 失敗（failed）：0。錯誤（error）：0。

### 6. 判定

- **S2 程式面：可執行**。本段沒有發現阻擋 S2 有效性的程式缺口。
- **仍需要的前置**：你的明確授權，以及執行時的憑證載入方式。
- **尚未驗證**（不得擴大通過結論）：
  - 新的帳務檢查在 MySQL 上的實際結果（只在 SQLite 上驗證過 SQL 語意）。
  - S2 四項在 MySQL 上的實際結果。
  - 1205 在 PyMySQL 下的錯誤代碼實際如何呈現（目前依程式碼推定）。
  - 未知提交結果恢復、完整 Batch B 矩陣、真實 DM、正式 `/exchange`。

### 7. 下一個具體步驟

請你授權一次隔離實例上的合併執行（S1 與 S2，同一實例、同一帳號、不增權、不 DDL），並在執行時提供既定的憑證載入方式。授權後我會先執行目標核對，再依上述順序執行，並如實記錄 passed、failed、skipped。

## S2 實機執行前最後同步修正（2026-10-24）

**性質**：本段只修改測試、文件與離線測試。**未啟動或連線 MySQL、未解密憑證、未執行任何 DDL、未增權、未碰 3306、Discord 或真實 DM。** 本段的「14 passed」歷史結果不沿用；新版斷言必須在實機合併執行後才能宣稱通過。

### 技能使用

- `tdd`：可用，已依規則做垂直切片（先寫離線測試、確認紅燈、再做最小實作、確認綠燈）。
- `start-dev-team`：本環境仍不在可用技能清單中，未使用，也未委派獨立審核。

### 修正內容

**1. 入口標記不能證明等待（已修正）**
前一版的 entered 標記寫在 `SubmissionRepository.exchange` 呼叫之前，只能證明子程序到達呼叫點，不能證明連線、交易或閘門等待已開始。已改為**測試專用的連線包裝器**：在送出精確的閘門語句（同時含 `jb2_control` 與 `FOR UPDATE`）之前寫入 gate-sent 檔。包裝器只轉發呼叫，不改動正式業務流程，也不增權。
- gate-sent 只證明「語句已由子程序送出」，**不證明伺服器已進入鎖等待**。
- 因此「阻擋後成功」案例的等待位置仍是推論，本段不宣稱直接觀察 MySQL 鎖。

**2. 1205 案例改為閘門持有至結果（已修正）**
- 父程序持有閘門，放行子程序，等待 gate-sent 後，**不睡固定時間**，直接在閘門仍被持有時等待子程序的結果，上限為 60 秒。
- 子程序必須實際回報 1205。若 60 秒內沒有結果，測試殺掉子程序並明確失敗。
- 取得結果後，於閘門仍持有期間核對：前後集合不變、餘額為 1、Ledger 對帳成立。之後才釋放閘門。
- 1205 是伺服器端的鎖逾時代碼，只有在語句確實等待閘門而逾時時才會出現。因此這個結果本身是伺服器曾等待的證據，比 gate-sent 更強，但它只證明「等待並逾時」，不證明等待的確切位置。

**3. 並行寫入的歸因（已修正）**
前後集合差異只能證明整體新增量，不能歸因。已加入 `attribution_violations`：
- 新增的保留必須**恰好**是贏方回報的 recommendation_id。
- 新增的 ledger 列必須**恰好**是一筆 exchange_debit，其 idempotency key 等於贏方請求的雜湊，且其 recommendation_id 指向該保留。
- 敗方的 key 不得有任何新增列；同 key 重放不得新增任何列。
- 這套檢查與整體總量、無孤兒斷言一起使用，避免把歸因描述得比證據更強。

### 離線測試（TDD 紀錄）

- 歸因函式：先寫 4 項測試，因函式不存在而紅燈，實作後綠燈。
- gate-sent 包裝器：先寫 2 項測試，因類別不存在而紅燈（NameError），實作後綠燈。
- 本段執行的完整離線集合：**70 passed、18 skipped、1 個 audioop 棄用警告**。
- 收集（不連線）的 MySQL 測試：**18 項**（底層 8 項、repository S1 6 項、S2 4 項）。
- 18 skipped 全部是未開啟隔離開關的資料庫案例，不是實機失敗。passed／failed／error 以上述為準，**failed 與 error 皆為 0**。

### 尚未驗證（不得擴大）

- 所有新版 MySQL 斷言與 S2 四項，尚未在實機執行。
- 包裝器在真實 pymysql 連線上的行為（目前只在假連線上驗證）。
- 1205 在實機上的確切回傳路徑（目前依程式碼與 pymysql 慣例推定）。
- 未知提交結果恢復、完整 Batch B 矩陣、真實 DM、正式 `/exchange`。

### 實機授權文字（可直接使用，本段未執行）

> 授權範圍：在既有隔離實例 127.0.0.1:3307（資料目錄 `.local/mysql-test/20261024-122528/data`，MySQL 8.0.46，UUID 沿用既有紀錄）上，**合併執行**新版底層測試 8 項、repository S1 6 項與 S2 4 項，預期收集 18 項。
>
> 允許的操作：
> 1. 以既有 Windows current-user DPAPI 密文 `credentials.dpapi` 載入既有兩個測試帳號的憑證（僅在本機程序記憶體中使用，不列印、不寫入文件、不解密輸出到終端）。本段未閱讀 `s1_resume_authorized.py` 的內容，執行前應由操作者確認其為 S1 最終執行所用的既定啟動輔助。
> 2. 啟動同一隔離 3307 實例（`--no-defaults`、loopback、停用 mysqlx），先核對 port、datadir、version、UUID、log、socket，全部符合才繼續。
> 3. 以既有 `jukebox101_test_exchange` 帳號執行測試，沿用既有資料與兩筆合成版本根。
>
> 禁止的操作：新增任何帳號、任何權限授予或撤銷、任何 DDL、關閉外鍵檢查、連線 3306、讀取 `.env`、提交或推送、Discord 與真實 DM、啟用正式 `/exchange`。
>
> 停止條件：目標核對失敗；基線與 migration 003 形狀檢查失敗；任何子程序失敗且不是預期的 1205 或業務結果；前後集合或歸因檢查不符；測試結束後仍有子程序或 3307 監聽。任一情況下停止，保留完整證據，不以重跑覆蓋。
>
> 執行後：正常停止實例（SHUTDOWN），確認 3307 無監聽、無隔離程序，保留整個資料目錄與證據檔（建議名稱 `pytest-combined.log`、`combined-report.json`）。
>
> 判定方式：只有實機執行的 passed 數量與斷言結果可作為通過依據；離線結果與歷史 14 passed 不得作為本次通過的依據。

### 判定

- **程式與離線面：完成。** 測試與文件之間沒有已知的不一致；離線 70 passed、18 skipped，failed 與 error 為 0。
- **實機面：待授權。** 授權文字已備妥，等待你確認後執行。

## 合併實機驗證結果（2026-10-24，既有隔離實例）

**授權範圍**：在既有隔離實例 127.0.0.1:3307（MySQL 8.0.46、資料目錄 `.local/mysql-test/20261024-122528/data`、既有 UUID、資料庫 `jukebox101_test`）上，合併執行底層 8 項、repository S1 6 項與跨程序 S2 4 項。未建立帳號、未增權或撤權、未重設密碼、未執行 DDL、未關閉外鍵檢查、未連線 3306、未讀取 `.env`、未刪除資料目錄、未提交或推送、未操作 Discord 或真實 DM、未啟用正式 `/exchange`。

### 執行方式

- 執行的程式為 `.local/mysql-test/combined_authorized.py`（新檔）。它在記憶體中解密既有 `credentials.dpapi`，啟動同一實例（`--no-defaults`、loopback、停用 mysqlx、獨立 socket 名稱），核對 port、datadir、version、UUID、log、socket 與資料庫名稱後，才以 `jukebox101_test_exchange` 帳號檢查基線並執行 pytest。
- root 憑證只用於身分讀取與 SHUTDOWN，不作為測試帳號。
- 舊的 `s1_resume_authorized.py` 未執行，也未修改。它有三個問題：只跑 S1、基線檢查使用 root、以覆寫模式寫入證據檔。這些問題在新檔中已修正，舊檔保留作為歷史。
- 等效的 pytest 命令為：`.venv/Scripts/python.exe -X utf8 -m pytest tests/test_exchange_mysql_integration.py tests/test_exchange_repository_mysql.py tests/test_exchange_cross_process_mysql.py -m mysql -v -p no:cacheprovider --tb=short`。

### 實際結果

- **收集數**：18（底層 8、repository S1 6、S2 4）。
- **結果**：**18 passed、0 failed、0 skipped、0 error**，耗時 34.64 秒，退出碼 0。
- 逐項結果見 `pytest-combined.log`，全部為 PASSED。

**帳務與 repository 案例**（全部通過）：底層 8 項、首次 RESERVED、同 key 重放 REPLAYED、零餘額 INSUFFICIENT_CREDITS 且前後集合不變、本人版本唯一時 NO_CANDIDATE 且前後集合不變、不同 key 並行、同 key 並行（兩筆 parametrize）。

**S2 四項**：
1. **阻擋後成功**（通過）：子程序在釋放前仍在執行，釋放後得 RESERVED；歸因檢查通過。
2. **超過上限得 1205**（通過）：父程序持有閘門直到取得子程序結果；子程序回報 `error_code == 1205`，前後集合不變，餘額仍為 1，Ledger 對帳成立；之後才釋放閘門。
3. **不同 key**（通過）：一個 RESERVED、一個 INSUFFICIENT_CREDITS；所有新增列歸因於贏方的保留與唯一扣款。
4. **同 key**（通過）：一個 RESERVED、一個 REPLAYED，指向同一筆保留；重放未新增任何列。

### 1205 的實際呈現與限制

- 1205 在實機上由子程序以 `error_code == 1205` 回報，**是在父程序仍持有閘門時發生的**，因為父程序在取得結果之前不釋放閘門。
- 這是伺服器端的鎖逾時代碼，表示語句確實等待了鎖並在上限內逾時。它不是業務結果，也不是部分寫入。
- **推論限制**：測試未直接讀取 `performance_schema`，因此無法確認等待物件的確切位置。我們推定它是 `jb2_control` 閘門列，因為父程序只持有這一把鎖，而子程序在閘門語句之前不會取得其他鎖。這是推論，不是直接觀察。
- **gate-sent 的精確意思**：它只表示子程序即將呼叫閘門語句，不表示語句已送達伺服器，也不表示伺服器正在等待。阻擋後成功案例中的「仍在執行」也只是推論，不是直接證據。

### 帳務歸因與斷言

- 新版 `account_violations` 在實機上通過，涵蓋 Ledger 對帳、非負餘額、無孤兒列、釋放與退款一致、退款金額與收款人關聯。
- 並行案例以 `attribution_violations` 驗證：新增的保留恰好是贏方的 recommendation_id；新增的 ledger 列恰好是贏方請求 key 的唯一扣款；敗方與重放不新增任何列。
- 這些斷言是歸因的證據，但不是對每個 SQL 語句的直接觀察。

### 停止與程序證據

- 伺服器正常停止：`combined-report.json` 顯示 `server_stopped=true`、`server_exit_code=0`；錯誤日誌在 `05:07:03Z` 顯示 `Shutdown complete`，前一行是 `Normal shutdown`。
- worker 收回：`worker_processes_left_after_run` 為空，沒有殘留的 worker 程序。
- 3307：沒有 Listen 狀態的監聽程序；只有 370 筆 TIME_WAIT，屬於測試大量短連線關閉後的正常狀態，會自行過期。
- 未觸及的程序：MySQL80 服務的 mysqld（PID 5040）及其子程序（PID 4956）持續執行，未被發送任何控制指令。
- 早先一次 `Get-NetTCPConnection` 查詢未限定 Listen 狀態，誤判為仍在監聽；經 Listen 狀態重新確認後，3307 無監聽。

### 合成資料與版本根

- 基線檢查在測試前通過（`baseline_recommendations_before_cleanup = 0`，且除兩筆合成版本根外所有表為空）。
- 測試結束時，夾具會清除自己擁有的合成使用者、影片、操作與決策；外鍵未修改，檢查未關閉。
- 兩筆版本根 `(s1-original-a, s1-original-a, s1-proof-1)` 與 `(s1-original-b, s1-original-b, s1-proof-2)` 留存，屬於已知限制；fixture 不是完全無狀態。

### 證據檔案（均位於 `.local/mysql-test/20261024-122528/`，由 Git 忽略）

- 本輪新增：`pytest-combined.log`（逐項 PASSED 與摘要）、`combined-report.json`（身分、基線、退出碼、停止狀態、worker 殘留）、`combined-console.log`（空，伺服器無主控台輸出）。
- 歷史保留（未覆寫）：`pytest.log`、`run-report.json`（首次 6 passed／1 failed）、`pytest-retest.log`、`retest-report.json`（8 passed）、`pytest-s1.log`、`s1-report.json`（9 passed／6 errors）、`pytest-s1-final.log`、`s1-final-report.json`（14 passed）、`s1-fixed-fixture-report.json`。
- 憑證：`credentials.dpapi` 只讀取、未修改。`error.log` 含初始化相關資訊，不得提交或公開。
- 輸出檢查：`pytest-combined.log` 與 `combined-report.json` 中沒有任何 password 字樣。

### 已知限制

- 阻擋後成功案例的等待位置仍是推論；1205 只證明伺服器曾等待閘門並逾時，不證明等待物件的直接觀察。
- 兩筆版本根留存，測試之間共享版本根狀態。
- root 憑證於本次執行中用於身分讀取與 SHUTDOWN；未用於測試帳號。
- 未知提交結果恢復、完整 Batch B 矩陣、真實 DM、正式 `/exchange` 均未驗證，不在本次範圍。

### 判定與下一個最小步驟

- **本輪實機結果**：18 passed，符合預期收集數 18，所有斷言與歸因通過，伺服器與 worker 已停止並保留證據。
- **不沿用**：歷史 14 passed 與離線 70 passed 不作為本次通過依據，本次通過僅以 `pytest-combined.log` 為準。
- **下一個最小步驟**：若要把 1205 的等待位置從推論提升為直接觀察，需要 `performance_schema` 的讀取權限或 PROCESS 權限，這需要另行授權，不在本次範圍。在此之前，建議保持現有推論的表述，並將 S2 的阻擋結論限定為「在閘門持有期間，子程序未完成，且釋放後完成」。

## 未知提交結果恢復（離線準備，2026-10-24）

**性質**：本段只做程式審核、測試與離線驗證。**未啟動或連線 MySQL、未解密憑證、未執行 DDL、未增權、未碰 3306、未操作 Discord、未發送 DM、未啟用正式 `/exchange`。** 下方的 MySQL 案例已撰寫但未執行。

### 技能使用

- `tdd`：可用，依規則做紅綠循環（先寫失敗測試，確認失敗原因，再做最小實作）。
- `start-dev-team`：本環境不在可用技能清單中，未使用，也未委派獨立審核。

### 1. 正式契約（審核結果）

**交易邊界**（`bot/database/submission_transaction.py`）
- `_commit` 把所有 commit 例外包裝為 `CommitOutcomeUnknown`。這是保守分類：無法證明伺服器是否已提交，因此不能視為明確回滾。
- commit 之前的任何例外都走 `_rollback`，之後原例外會被重新拋出。
- `_rollback` 與 `_close` 失敗時只附加註記，不覆蓋原始錯誤。唯一例外是：沒有主要例外時，`_close` 失敗會回報為 `CommitOutcomeUnknown`，因為提交可能已成功。

**三種情況的分類**
| 情況 | 伺服器狀態 | 客戶端收到的 | 正式處理 |
| --- | --- | --- | --- |
| COMMIT 尚未送出即失敗 | 未提交，連線關閉後回滾 | `CommitOutcomeUnknown` | 以同一 key 重放，會重新保留一次 |
| COMMIT 已完成但確認遺失 | 已提交 | `CommitOutcomeUnknown` | 以同一 key 重放，回傳 `REPLAYED`，不重複扣點 |
| 語句階段的資料庫錯誤（如死鎖） | 回滾 | 原 `pymysql` 錯誤 | 明確失敗，不重放 |

**重放與派送**
- `reserve_exchange` 在餘額檢查之前先以 `ledger key`（由 request_key 雜湊）查找既有保留，所以同 key 重放回傳 `REPLAYED`，不會再扣款。
- 派送只在 `repository.exchange` 回傳 `RESERVED` 或 `REPLAYED` 之後進行。這兩者都代表保留已在資料庫中提交，因此「何時可以開始派送」的條件是：**交易已確認提交**。`CommitOutcomeUnknown` 時不派送。
- `deliver_recommendation` 透過 `claim_delivery` 領取保留。若保留已 `delivered`，不會再次送出。

**不要混為一談的兩種未知**
- **資料庫提交結果未知**：由 `CommitOutcomeUnknown` 表示，靠同 key 重放與 `ledger key` 解決。
- **Discord DM 送達結果未知**：由 `DmResult.unknown` 表示，發生在保留已提交並進入 `sending` 之後，靠 `DELIVERY_LEASE` 與 `recover_interrupted` 轉為 `uncertain`。這不在本段範圍，也不由 request_key 重放解決。

### 2. 審核發現的正式程式缺陷

**D1：未知提交後沒有同 key 重放（已修正）**
- **觸發條件**：`exchange` 的 COMMIT 已成功但確認遺失，客戶端收到 `CommitOutcomeUnknown`。`exchange_and_deliver` 沒有重試，直接把例外往上拋。
- **實際後果**：Discord 端的 request_key 是 `discord:<interaction.id>`，每次 `/exchange` 都是新的 key。使用者依回覆重新執行，會得到新的 key，於是**再次扣點**，並留下第一筆已提交但從未派送的保留。目前沒有任何流程會再領取或派送 `reserved` 狀態的列。
- **修正**：`_exchange_with_one_replay` 在 `CommitOutcomeUnknown` 時，以**同一 request_key** 在新連線上重放一次。若重放仍失敗（任何例外），以新的 `CommitOutcomeUnknown` 報告，原始未知例外作為 cause 保留，並附上重放失敗的類別名稱（不附訊息）。

**D2：回覆文字鼓勵重新操作（已修正）**
- **觸發條件**：同 D1，未知提交被映射為「本次交換未完成，請稍後再試」。
- **實際後果**：使用者會重新執行，造成 D1 的重複扣點。投稿路徑（`/share`）已有正確的文字（勿重新投稿、請查詢），交換路徑未採用。
- **修正**：`MusicService.synthetic_exchange` 將 `CommitOutcomeUnknown` 與一般資料庫錯誤分開。未知結果的回覆為「交換結果未確認，請勿重新執行 /exchange；稍後以 /history 查詢。」一般錯誤維持原文。

**殘餘風險（未修正，需要設計決策）**
- 若兩次嘗試都回報未知，且原提交其實已成功，該保留會停在 `reserved`，沒有自動派送或退款流程。這需要恢復排程或補償設計，屬於後續範圍。目前的回覆已避免使用者主動重新操作，但不能消除此風險。
- 正式 `/exchange` 目前未啟用，因此 D1、D2 的修正在本段只影響合成注入路徑與共用的交換流程；實際影響範圍應以實機驗證確認。

### 3. TDD 紀錄（離線）

| 切片 | 紅燈 | 綠燈 |
| --- | --- | --- |
| D1、D2 的交換呼叫端與回覆（`tests/test_exchange_unknown_commit.py`） | 6 失敗、1 通過（定義性錯誤不重放，本來就成立） | 7 通過 |
| 故障注入器（`tests/test_commit_fault_injector.py`） | 收集錯誤（模組不存在） | 5 通過 |
| MySQL 案例（`tests/test_unknown_commit_mysql.py`） | 收集 4 項，離線全部跳過 | 尚未實機執行 |

- 注入器的每個模式都斷言 `fired == 1`，並在假連線上驗證階段順序：lost-ack 只在真實 commit 之後觸發；before-commit 不呼叫真實 commit；statement 只在第一個符合的語句前觸發，且不影響其他語句。
- 注入器只包裝每個案例的第一條連線，獨立的觀察連線與 fixture 清理不經過注入器。

### 4. MySQL 案例（已撰寫，未執行）

| 案例 | 注入階段 | 預期 | 能證明 | 不能證明 |
| --- | --- | --- | --- | --- |
| lost-ack 後同 key 重放 | 真實 COMMIT 之後丟失確認 | 第一次 `CommitOutcomeUnknown`；重放 `REPLAYED`，同一 rid，餘額 0，歸因通過 | 已提交的結果可被同 key 找回，且不重複扣點 | 真實網路中斷 |
| COMMIT 前失敗後重試 | COMMIT 之前失敗，真實回滾 | 第一次 `CommitOutcomeUnknown`；前後集合不變；重試 `RESERVED`，餘額 0 | 未提交時回滾乾淨，重試只保留一次 | 伺服器端實際看到的斷線時機 |
| 語句失敗（死鎖型）後重試 | 扣款 INSERT 之前 | 原 `pymysql` 錯誤（1213），不是未知；前後集合不變；重試 `RESERVED` | 語句失敗是確定的回滾，部分保留被撤銷 | 死鎖的真實產生 |
| 呼叫端重放並派送 | 真實 COMMIT 後丟失確認（第一次連線） | 一次假 DM；`DELIVERED`；保留 1、餘額 0 | 派送只針對已提交的保留，且只發一次 | 真實 Discord DM |

- 注入器的 `fired` 計數與階段斷言在案例中都會檢查，避免測到不同的故障。
- 所有案例都使用獨立的觀察連線做帳務與歸因檢查；注入器不包裝觀察連線。

### 5. 離線驗證結果（本段實際執行）

- 相關子集（本段新增與既有交換、派送、回覆、持久化與隔離守衛）：**197 passed、22 skipped**。22 項跳過的全部是未開啟隔離開關的 MySQL 案例（原 18 項加新增 4 項）。
- **整個離線測試套件：2143 passed、79 skipped、0 failed、0 error。** 79 項跳過全部是需要隔離開關的資料庫案例。

### 6. 限制

- lost-ack 是**故障注入**：真實 COMMIT 成功，客戶端被告知失敗。這不等於真實網路中斷。
- DM 是假的 `FakeSender`，沒有任何真實 Discord 呼叫。
- 殘餘風險（兩次未知且原提交成功）未修正，需要恢復排程設計。
- 鎖等待位置仍是推論；本段沒有新增權限。
- 兩筆合成版本根仍留存。

### 7. 下一輪實機授權文字（尚未執行）

> 授權範圍：在既有隔離實例 127.0.0.1:3307（同一資料目錄、同一 UUID、資料庫 `jukebox101_test`）上執行 `tests/test_unknown_commit_mysql.py`（4 項），並與既有 18 項合併執行，預期收集 **22 項**。
>
> 允許：以既有 DPAPI 密文 `credentials.dpapi` 在本機程序記憶體載入既有 `jukebox101_test_exchange` 憑證；啟動同一實例並以 `combined_authorized.py` 相同的目標核對流程核對身分與基線；以故障注入包裝器在每個案例的第一條連線上注入一次故障；寫入與刪除限於測試使用者範圍 `900000000000000100–199`、固定標記的合成資料。
>
> 禁止：新增帳號、增權或撤權、密碼重設、DDL、關閉外鍵檢查、連線 3306、讀取 `.env`、刪除資料目錄、提交或推送、真實 Discord 或真實 DM、啟用正式 `/exchange`。
>
> 預期結果：`lost-ack` 案例得 `CommitOutcomeUnknown`，`injector.fired == 1`，重放得 `REPLAYED`，歸因通過；`before-commit` 案例得 `CommitOutcomeUnknown`，前後集合不變，重試得 `RESERVED`；`statement` 案例得 `pymysql` 1213 而非未知，前後集合不變，重試得 `RESERVED`；呼叫端案例只送出一次假 DM。
>
> 停止條件：目標核對或基線失敗；任何注入的 `fired` 不等於 1；任何案例得到非預期的錯誤類別；前後集合或歸因不符；測試結束後仍有子程序或 3307 監聽。任一情況下停止，保留完整證據，不以重跑覆蓋。
>
> 執行後：正常 SHUTDOWN，確認 3307 無 Listen 狀態（TIME_WAIT 不算），保留資料目錄與證據檔，新輸出使用新檔名（建議 `pytest-unknown-commit.log`、`unknown-commit-report.json`）。
>
> 判定：只有實機執行的 passed 與斷言結果可作為通過依據；離線結果不得作為實機通過的依據。
