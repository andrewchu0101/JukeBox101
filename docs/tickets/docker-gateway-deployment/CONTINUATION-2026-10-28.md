# 接續摘要（2026-10-28 檢查點）

> **2026-10-29 文件整理更正**：下方正文保留原紀錄。凡標示「更正」或「取代」者，以更正為準；最新的工單 24 結論見 `24-exchange-restart-recovery.md`，本輪唯讀核對結果見文末「2026-10-29 文件整理」。

## 測試結果
- 最終全量離線回歸：2658 通過、2 失敗、138 略過。
- 2 項失敗為已知限制：`test_isolated_mysql_target`、`test_window_schema_isolation`，回報 `DATADIR_NOT_ISOLATED`。（2026-10-29 更正：根因已重現為測試夾具的平台相依路徑，見下方「2026-10-29 更新」；原先推定的容器資料目錄情境未重現。）守衛未放寬。
- 前一輪一次出現的 `test_lifecycle_sigterm` 資料庫執行緒案例失敗，在本次最終回歸中通過；連續重跑也通過。原因未查明，列為不穩定風險。

## 未驗證
- **MySQL**：驅動腳本（不是正式入口）的未提交回滾、已提交保留、長查詢加 SIGTERM 已在 `jb101-real-mysql`（3309）驗證。正式入口的關閉路徑在有未提交交易的工作執行緒中是否安全，未驗證。
- **os._exit**：未標為最終接受。在正式入口的資料庫實測證據補齊前，維持待定。
- **容器秘密權限案例**：UID、GID、父目錄、開啟探測的容器實測未做，僅有單元測試。
- **真實 Discord**：未連線，`close` 只以替身測試。
- **restart 行為**：本輪維持 `unless-stopped`。秘密錯誤會持續重啟（實測），部署前必須檢查秘密檔（擁有者 10001、0400，父目錄可搜尋）。主機或 daemon 重啟後的行為未驗證。

## 時限量測
- 程式內期限：20 秒（互動等待 15 秒、取消收尾 1 秒、close 至少 4 秒）。
- 外部量測（`docker stop`）：stuck 約 15.2 秒、stuck_db 約 15.2 秒、close_hangs 約 20.3 秒。close_hangs 的外部耗時超過程式內 20 秒的部分來自 Docker 停止的開銷；不宣稱三者都在 20 秒內。

## 映像與候選包
- 映像：`jukebox101-bot:dev` 仍早於本輪變更，**未重建**。本次檢查點未重建映像或候選包。
- 候選包：上一個版本已失效，不得沿用。重建需要在檢查點之後另行執行。

## 保留資源（未清理）
- `jb101-real-mysql`、`jb101-real-data`、`jukebox101_test`（列 …087、…088、…089、…090、…091、…097 等測試列）。
- `jb101-iso-mysql`、`jb101-iso-data`（身分不可追溯，不作為驗收依據）。
- `jb101-lifecycle-bot-1`（exited，屬 entry-owner-dev 的測試容器，網路 none）、`jb101-lifecycle2` 相關容器與網路、映像 `jukebox101-bot:lifecycle-test*`。
- `jb101-mig-mysql`、`jb101-mig-data`、`jukebox101_mig_202610281706`、測試帳號。
- 映像匯出檔：`<HOME>/build/review/candidate-image.tar`、`<HOME>/build/review/layers/`（`<HOME>` 為本機使用者主目錄的佔位符，原始證據位置不變）。不分享、不提交、不納入交付包。

## Git 檢查點
- **更正（2026-10-29）**：原本寫「未建立 commit」已過時。開發檢查點 commit `3cc12bc2131539aeb3dff072f702cc4e67c6e27c` 已存在（分支 `feature/owner-review-offline-checkpoint`，時間 2026-10-29 02:02 +0800，198 個檔案）。
- 此 commit 是整體開發狀態的保存，**不代表定版或全部驗證通過**；未 push，遠端 `origin` 未操作。
- 檢查點之後的工作樹變更見下方「2026-10-29 更新」。
- 排除清單：`.env`、私人 PDF（`Re Issue 576880673 ... .pdf`，仍為未追蹤）、秘密檔、映像匯出檔、測試資料。

## 2026-10-29 更新（未提交，待審）
- 新增 `docs/database-access.md`（工單 08，文件，使用佔位符）。
- 新增 `tests/test_owner_restart_safety.py`（工單 22 離線部分：重啟後舊權杖失效且不寫入、過期不寫入、重啟後重新預覽可一次確認）。3 通過。
- 新增 `tests/test_exchange_restart_recovery.py`（工單 24 離線部分：扣款後重啟只派送一次且不二次扣款、送出中重啟後過租約變 uncertain 且不重送、租約內不被接管）。5 項通過（2026-10-29 更正：原文「3 通過」為過時數字，工單與測試檔皆為 5 項）。
- `.pytest_cache`、`.pytest_tmp` 未使用（pytest 以 `-p no:cacheprovider` 執行）。
- 2 項失敗的實際節點（WSL/Linux Python 3.12.15 執行，未修改測試）：
  - `tests/test_isolated_mysql_target.py::test_matching_identity_passes_and_each_mismatch_is_named`
  - `tests/test_window_schema_isolation.py::test_the_window_identity_passes_only_for_the_window_pin`
  - 兩者都在第一次合法身分檢查就拋出 `DATADIR_NOT_ISOLATED`。根因是測試夾具 `GOOD_IDENTITY["datadir"]` 以 `str(RUN_DIR / "data") + "\\"` 組出（尾端為 Windows 反斜線）。在 POSIX 上 `normpath` 不會移除這個字元，與守衛正規化後的路徑不相等（已用獨立計算重現）。
  - 同一組測試在 Windows 原生 `.venv` 執行：27 通過。守衛本身沒有被放寬，也未修改測試。
  - 交接文件先前寫的「容器內資料目錄 `/var/lib/mysql`」是另一個情境的推定，本次未重現。
  - 修正夾具需要改測試檔（去除平台相依的分隔符），屬於測試契約變更，待核准。（2026-10-29 取代：此修正已於第二輪更新完成，見「跨平台夾具」；repo 內未留存核准紀錄，範圍已登記於工單 24 File owner。）

## 尚未授權
- push、發布、正式服務連線、正式 Migration／發幣、指令同步、交付夥伴、定版。
- 專案 LICENSE：待 Owner 決定。

## 2026-10-29 第二輪更新（未提交，未定版）
- **0400 統一**：主機檢查只接受 0400（`NOT_SANCTIONED_MODE`），修復指引改為 `chmod 0400`。0440 相關的舊測試與文件已依核准規則改寫。容器內 `evaluate_sanctioned_mount` 原本即只接受 0400。
- **stat 失敗原因碼**：`EACCES` 對應 `PATH_NOT_SEARCHABLE`（路徑上某個目錄不可搜尋，不一定是直接父目錄），`ENOENT` 對應 `FILE_MISSING`，其餘（包含 `EPERM`）對應 `OPEN_FAILED`。仍為 fail-closed，不輸出路徑或秘密內容。
- **跨平台夾具**：身分比對的 `datadir` 改為平台原生分隔符，比較強度不變；秘密權限測試使用 Linux 暫存目錄（`unix_permission_dir`）。
- **IN_PROGRESS 期望值**：`7990f2d` 讓「已有未結束交換」先於餘額檢查。repository 的零餘額與併發不同 key 案例，失敗方現在期望 `in_progress`。已確認交易結果：沒有第二次扣款、沒有第二筆 reservation，footprint 與帳本斷言仍保留。
- **隔離 MySQL（專用，假值帳號，3307，UUID 於建立時記錄）**：
  - 資料目錄 `~/.local/share/jb101-iso/run-20261029-t24c`（不在專案內）。前兩次初始化失敗（磁碟 fsync 慢），已棄用，其中一個目錄已移除。
  - 套用 `schema.sql` 與 migration 002–005。`docs/compensation-candidate-004.sql` 經 SHA-256 `e1804816…36651` 核對後套用到隔離 schema（不是正式 migration）。
  - 結果：整合測試與跨程序測試 11 通過（第一次）；第二次完整隔離套件 54 通過、1 略過、3 錯誤。錯誤原因是測試殘留：evidence 資料夾重複使用造成 `FileExistsError`，以及前次殘留的合成列導致 empty-baseline 守衛拒絕啟動。
  - **未完成**：清除殘留列的動作被自動分類器以 mass delete 拒絕，因此沒有重跑。下一步需要你決定：授權清除隔離 schema 內的合成殘留（或重新建立隔離 schema），才能完成租約過期、重啟不重送、時鐘偏差的 MySQL 測試。離線版本的這些測試已通過（`tests/test_exchange_restart_recovery.py`，5 項）。
  - 實例已正常 SHUTDOWN（exit 0），3307 無監聽，資料目錄保留。容器 `jb101-iso24-mysql` 為停止狀態，保留。
  - 測試證據：`compensation-evidence/` 內含 run1 的 cleanup plan（已存在，未刪除）；run2 資料夾未建立。
- **容器秘密探測**（舊映像 `jukebox101-bot:dev` 掛載目前 `bot/`，合成假值，`--network none`）：A PASS、B 0440 `NOT_SANCTIONED_MODE`、C `GROUP_UNEXPECTED`、D `PATH_NOT_SEARCHABLE`、E `NOT_READABLE,UNEXPECTED_OWNER,OPEN_FAILED`、F `PATH_NOT_SEARCHABLE`、G（文件建議配置）PASS、H `PATH_NOT_SEARCHABLE`。**這不是新映像的驗證**；新映像需待基底映像拉取授權後重建。
- **UNKNOWN（未決，未實作）**：`jb2_decisions` 只在提交落地時寫入（含 decision_id、actor、reason）。重啟後程序不知道先前的決定鍵，因此無法辨識先前的 UNKNOWN。方案 A 只能顯示既有資料事實，不能宣稱先前嘗試成功。
- **Owner CLI**：`DB_PASSWORD` 是同一項資料庫秘密的另一種輸入方式，不寫入部署包或範例設定（`docs/deploy/owner-secrets.md` 第 12 節）。
- **基底映像拉取**：仍待決定；未拉取、未重建。

## 2026-10-29 收尾更新：工單 24 隔離 MySQL 結果
- **隔離實例 `t24e`**（全新，建立時記錄 UUID，`127.0.0.1:3307`，假值帳號，schema 與 migration 002–005 加候選 DDL `docs/compensation-candidate-004.sql`，SHA-256 已核對）。
- **結果**：隔離套件 **60 passed，0 failed，0 errors，0 skipped，exit 0，291 秒**（含 `tests/test_exchange_restart_recovery_mysql.py` 新增的租約、重啟不重送與時鐘偏差案例）。
- **證據**：`~/.local/share/jb101-iso/run-20261029-t24e/compensation-evidence/`（76 個檔，無合成憑證）；資料目錄與 UUID 記錄同目錄。
- **實例關閉**：`jb101-iso24-mysql-e` 已正常 SHUTDOWN（exit 0，`Shutdown complete`），3307 無監聽，資料目錄保留。
- **之前的實例**：`jb101-iso24-mysql`（`t24c`）與 `jb101-iso24-mysql-d`（`t24d`）為停止狀態，資料與證據保留。`t24c` 含殘留列，`t24d` 含 c4 清理失敗的殘留，兩者都不作為驗收依據。
- **失敗歷程（已排除）**：`t24c` 的殘留列造成 empty-baseline 守衛拒絕；清除被自動審查拒絕，未重試。`t24d` 的同名測試 plan 檔衝突已修正（改用 node id）。
- **限制**：隔離測試，不是正式 MySQL 或正式 Discord 驗收；「重啟」為新 repository 模擬；時鐘偏差為注入值。
- **工單 24 狀態**：離線與隔離 MySQL 驗證完成；正式環境驗收未執行，待授權。
- **仍待決定（本輪未處理）**：基底映像拉取；UNKNOWN 產品行為；新映像重建後的容器驗證（目前容器結果來自舊映像掛載目前程式）。

## 2026-10-30 收尾（遲到且明確未送出的退款）

- **修正**：`bot/database/exchange_delivery_store.py` 的 `record_result`，同一 claim 的明確 NOT_SENT 可把 `uncertain` 轉為 `failed`，之後由既有退款流程退 1 Nickel。`UNKNOWN` 仍不退款。詳見工單 24「2026-10-29 修正」。
- **離線全量（乾淨重跑）**：2673 passed、143 skipped、0 failed，exit 0。
- **專用隔離 MySQL（`t24e`，8 檔）**：61 passed，exit 0。證據資料夾 `compensation-evidence-rerun-2026-10-29`，資料目錄與先前證據保留。
- **先前失敗**：tuple 參數回歸（已修正）；隔離 MySQL 39 錯誤與離線 sqlite I/O 錯誤均為目前判斷（環境與並行），乾淨重跑通過，但根因未單獨證明。
- **隔離實例**：`jb101-iso24-mysql-e` 以 `docker stop`（SIGTERM，mysqld 正常關閉）停止，exit 0，埠 3307 已關閉，資料目錄保留。未使用 `.local/live-run/close_isolated.py`，因其依賴 10/25 的 DPAPI 憑證，不適用於 t24e。
- **背景程序**：本輪啟動的 pytest 與 Monitor 均已結束。
- **Git**：未 stage、未 commit、未操作遠端。
- **未核准事項（維持不變）**：基底映像拉取、UNKNOWN 產品行為、`os._exit` 最終接受、LICENSE、正式環境操作、定版交付；新增的「遲到的 DELIVERED 不改寫 uncertain」與「使用者文案中的『扣點』」需 Owner 決定。

## 2026-10-30 遲到 DELIVERED 修正（已完成，離線與隔離範圍）

- **修正**：同一 claim、帶有效 message_id 的 DELIVERED 結果，可把 `uncertain` 更新為 `delivered`，不退款、不重送。詳見工單 24「2026-10-30 修正」。
- **離線全量（乾淨）**：2677 passed、144 skipped、0 failed，exit 0。
- **專用隔離 MySQL（t24e，8 檔）**：62 passed，exit 0；證據資料夾 `compensation-evidence-rerun-2026-10-30`。
- **憑證**：已更換曝光的合成憑證，舊值被拒絕，新值只存於 0600 檔案，未輸出。
- **隔離實例**：`jb101-iso24-mysql-e` 以 `docker stop` 正常關閉，exit 0，埠 3307 已關閉，資料與證據保留。
- **背景程序**：本輪所有 pytest 與 Monitor 均已結束。
- **Git**：未 stage、未 commit、未操作遠端。
- **設計文件**：`uncertain → delivered` 已由核准規則寫入，`uncertain → failed` 的 NOT_SENT 條件已對齊。
- **範圍聲明**：僅代表離線及專用隔離範圍通過，不代表正式驗收、新映像驗證或可交付。
- **未核准事項（維持不變）**：基底映像拉取、UNKNOWN 產品行為、`os._exit` 最終接受、LICENSE、正式環境操作、定版交付。

## 2026-10-30 後續（憑證與遲到 DELIVERED）

- **憑證曝光**：t24e 隔離實例的合成測試憑證曾於 2026-10-29 輸出到對話紀錄，視為已曝光，不得重用。未來重新啟用 t24e 前須先更換憑證，且不輸出新值。不涉及正式秘密。
- **遲到的 DELIVERED**：設計未定義 `uncertain → delivered`，屬未定義轉移，不是實作違反既有規定。目前行為為不改寫、不退款、不重送。候選修正：僅限同一 claim_token 且帶 message_id 的 record 才可轉為 delivered。未實作，待下一輪授權。
- **設計文件更正**：上一輪誤將 `uncertain → failed` 放入禁止區塊，已還原為原文結構，並在允許區塊補上有證明的 NOT_SENT 條件。
- **範圍聲明**：修正與測試結果僅代表離線及專用隔離範圍通過，不代表正式驗收、新映像驗證或可交付。
- **未核准事項（維持不變）**：基底映像拉取、UNKNOWN 產品行為、`os._exit` 最終接受、LICENSE、正式環境操作、定版交付。

## 2026-10-29 文件整理（唯讀審查後；未 stage、未 commit、未新增測試、未啟動實例）

- **授權**：使用者於本對話明確授權專用隔離 MySQL、乾淨隔離資源、重跑相關套件與完成工單 24。repo 缺授權紀錄屬文件缺漏，已於工單 24「授權記錄」補記，未補造引文或時間。
- **工單 24 驗收**：三項均有證據，已勾選；未新增驗收條件。
- **租約時鐘**：以應用程式 UTC 時鐘判定，不是資料庫時間戳。詳見工單 24「租約時鐘（技術結論）」。
- **組合案例**：未新增。理由與剩餘風險見工單 24「組合案例評估」與「剩餘風險」。
- **工作樹行尾（唯讀確認，未轉換）**：`bot/__main__.py` 在 HEAD 為 318 行全部 CRLF，工作樹為 335 行、0 行 CRLF。653 行差異即整檔行尾變更；忽略行尾後，實質改動為 19 行新增、2 行刪除。`bot/secret_file_policy.py` 實質改動 6 行。建議：整檔轉回 CRLF 前，先與工單 02、03 的負責人確認，避免夾帶無關變更。此為建議，本輪未執行。
- **產品程式登記**：`bot/secret_file_policy.py` 屬工單 02；`bot/__main__.py` 屬工單 03 與 27。兩者都不是工單 24 的產出。
- **舊數字與敘述**：離線測試數字「3 通過」已更正為 5 項；「待核准」的跨平台夾具修正已標示為已完成並取代；文首加入更正說明。
- **Teammate**：兩位唯讀 Teammate（`lease-clock-auditor`、`ticket-scope-auditor`）已完成範圍，關閉結果見對話回報。
- **未核准事項（維持不變）**：基底映像拉取、UNKNOWN 產品行為、`os._exit` 最終接受、LICENSE、正式環境操作、定版交付。
