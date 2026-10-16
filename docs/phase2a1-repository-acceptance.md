# Phase 2A.1 — Repository 整合驗收準備

最新狀態：**Phase 2A.1 Integration Accepted**。使用者在 Phase 2A.2 規格回報：13 Passed、0 Failed、0 Skipped、7.19 秒，前後六表全部 0，無資料列殘留。這是已完成的人工驗收歷史，Codex 本次未重跑；Accepted 範圍仍限下述 rollback-only SQL／鎖測試，不包含正式 Commit 或 Discord。

以下是 2026-10-14（Asia/Taipei）的原始準備紀錄；其中「未執行／Pending」描述當時狀態，已由上述使用者人工結果更新。原準備工作沒有連線真實 MySQL 或執行真實 SQL。Phase 1A～1D 保持 Accepted；Phase 2A 整體仍待 Discord 人工驗收，Phase 2B 未開始。

## SQL 審查結果

Repository 未修改。新增 SQL 與既有六表約束相容，未發現需要 DDL／Migration 的事項，但相容性仍須真實整合驗收：

| SQL 行為 | 對應既有約束與驗收重點 |
|---|---|
| users INSERT／主鍵 no-op upsert | 保留 credit_balance、daily_enabled；不重置既有帳號 |
| songs INSERT／song_id no-op upsert | `(platform, platform_track_id)` 大小寫敏感唯一鍵；只新增 pending defaults；保留 source_url、youtube_url、resolution_status、provenance |
| canonical song `SELECT ... FOR UPDATE` | 投稿在歌曲鎖下操作；使用者先於歌曲取得鎖；同 Video ID 全域唯一 |
| submissions SELECT／INSERT | 使用者／歌曲唯一；FK 指向已存在使用者與 canonical song；代表投稿嘗試，不代表授點 |
| balance SELECT | 缺少使用者才回傳 0；不存在帳號的查詢不新增列；DB 失敗仍傳播 |
| Daily UPDATE | BOOLEAN CHECK 相容；保存明確最終值；不變更額度或建立派送列 |
| history JOIN／ORDER BY／LIMIT | 僅本人 delivered；`delivered_at DESC, recommendation_id DESC`；上限 10 筆；不回傳其他投稿者資料 |

所有業務值以 `%s` 綁定，不插入動態 SQL identifiers。沒有 ledger 寫入、發點、扣點或外部送歌。verified 測試資料的網址／provenance 明確為 synthetic fixture，不是真實歌曲驗證。

## 13 項待執行整合案例

| 案例 | 數量 | 預期驗證 |
|---|---:|---|
| `test_pending_submission_sql` | 1 | pending、原始來源、11 字元 ID、NULL delivery URL／證據；一首歌／一次投稿／0 ledger／0 點數 |
| `test_repeated_submission_sql` | 2 | 同使用者重試為 duplicate；不同使用者可留各自嘗試；同影片只有一首歌且來源不變，均不發點 |
| `test_existing_resolution_is_preserved` | 2 | 已 verified 或 rejected 的歌曲 identity、來源、目標、狀態、provenance 全部保留 |
| `test_submission_sql_error_rolls_back_operation` | 1 | user／song INSERT 後注入負餘額 UPDATE，既有 CHECK 預期拒絕（3819）；savepoint 回滾消除整個操作的部分寫入 |
| `test_daily_preference_sql` | 2 | 新／既有帳號反覆 on、on、off、off；最終值正確；既有 synthetic balance 保留；無派送／ledger 列 |
| `test_balance_and_empty_history_sql` | 1 | 不存在帳號餘額 0、歷史空且不建列；現存 synthetic balance 正確 |
| `test_history_sql_sort_scope_and_limit` | 1 | 同時間以推薦 ID 降冪；不同時間亦正確；排除 reserved 與其他收件人；超過 10 筆截至 10 |
| `test_explicit_outer_rollback_removes_all_rows` | 1 | successful operation 不提交；外層 rollback 後六表均為 0 |
| `test_parallel_pending_lock_contention` | 2 | 同／不同使用者對相同 Video ID 的未提交競爭；1205 timeout；holder rollback 後另一獨立連線重試成功再 rollback |

前 11 項位於 `tests/test_repository_mysql.py`；並行 2 項位於 `tests/test_repository_mysql_concurrent.py`。本次只 collect-only：**13 collected，未執行**。

## 安全隔離與 Rollback 設計

沿用 Phase 1 `tests/mysql_safety.py`，原檔及安全測試均未修改：只使用五個完整的 TEST_DB_* 環境值，不讀 `.env`、不回退 DB_*。目的地固定 `127.0.0.1:3306 / jukebox101_test / jukebox101_test_app`；登入身分必須為 `jukebox101_test_app@127.0.0.1`。

新增入口同時要求 `JUKEBOX_RUN_MYSQL_TEST=1`、`JUKEBOX_RUN_REPOSITORY_MYSQL=1`，另沿用 `JUKEBOX_TEST_SCHEMA_READY=1` 與 `JUKEBOX_DISPOSABLE_DATABASE=jukebox101_test`。每個連線寫入前重新驗證實際帳號、資料庫、六表 InnoDB、精確 keys／FK targets、CHECK 文字及 enforced 狀態。每個 Repository 操作再檢查目前授權、設定一致及 worker thread ownership。

Repository 的正式 transaction 成功會 Commit，因此不能直接使用後再要求測試資料全部 Rollback。測試專用 `RollbackRepositorySession` 只在目前測試／子程序範圍注入 `bot.database.repository.transaction`：

1. 建立及驗證連線，要求六表皆 0；有既存資料立即停止，不自動刪除。
2. 清除 preflight read snapshot，begin 外層未提交交易。
3. 每個原 Repository 操作以固定 savepoint 包住，成功只 RELEASE；失敗 ROLLBACK TO，再 RELEASE。執行原 SQL，不重寫業務語句。
4. fixture／worker 結束必定外層 rollback；即使 rollback 失敗也 finally close，且不能算驗收通過。
5. 單連線案例及並行父程序以新連線比較測試前後六表的 committed row counts。

不呼叫真實 connection.commit、不 DELETE／TRUNCATE、不更改 Schema。測試前後零筆是本階段的硬性前置與後置條件。synthetic balance／delivered fixture 只是查詢測試輸入，不代表 ledger 業務一致性或真實 Discord 成功派送。

**驗證範圍限制**：這些案例驗證真實 SQL／約束／鎖與 savepoint rollback；刻意替換正式 transaction ownership，不能證明正式 Commit、跨連線提交可見性或跨提交重複偵測。原 transaction 的 commit／rollback／close 控制流程維持既有 mock 覆蓋。要驗收真正成功 Commit 並保留後續觀察，需要另行授權非 rollback-only 測試與專門資料生命週期政策，本次不提供此入口。

## 並行期限與清理

採 `multiprocessing.get_context('spawn')`，避免共享或繼承資料庫連線。每個 worker 在自己的程序／thread 建立、使用、rollback、close 連線；父程序只傳 synthetic recipient／video URL，不傳 credentials。worker 從自己的 TEST_DB 環境重新驗證授權。

- 既有連線 timeout：connect 5 秒、read/write 10 秒。
- 測試連線 `SET SESSION innodb_lock_wait_timeout = 2`；只修改該連線，不修改全域伺服器設定。
- holder 最多等 release 20 秒；整個情境 IPC 使用同一 monotonic 30 秒期限。
- 只有 contender 操作得到 `pymysql.OperationalError(1205)`，且外層 rollback／close 已完成後，才回報 lock_timeout；其他錯誤使情境失敗。
- 退出清理每個 worker 最多 join 3 秒，必要時 terminate／再 join 3 秒，仍不退出才 kill／再 join 3 秒。強制終止、非正常 exit、缺少 cleanup report、無法 reap 都失敗；不使用無期限的 future.result、thread.join 或 IPC recv。
- 三個 worker 的最壞額外 join 期限共 27 秒；正常情境上限為 30 秒 IPC deadline 加最多 27 秒程序清理，不將 OS 啟動／排程視為硬即時保證。單連線查詢與驗證亦受 driver I/O timeout 控制。

holder 成功但未提交時，contender 不可能看見同一提交結果；它被鎖阻擋，timeout 後完整回滾。holder 回滾後，retry 再新增一份 pending 並回滾。**這不是兩個成功 Commit 後只剩一份的競態測試**；在「所有資料 rollback」限制下不宣稱該行為通過。

savepoint cleanup 需要 `innodb_rollback_on_timeout=OFF`；preflight 只讀該全域設定，不符合就拒絕寫入，不能擅自改設定。1213 deadlock 或 savepoint 消失不接受為 1205 的替代成功結果；整合失敗需停止調查。

## 離線驗證

完整離線回歸：**180 Passed、0 Failed、0 Skipped、34 Deselected**；另補跑既有 2 項純文字 normalization，**2 Passed、0 Failed、0 Skipped**。合計 **182 個不同離線案例通過**，包含既有 149 項與新增 33 項。34 項排除為原本 19 項真實 MySQL、13 項新增整合及另行補跑的 2 項純文字案例。真實案例沒有運行，不把 deselected 算成 skipped 或 passed。

新安全測試涵蓋 opt-in、正式庫阻擋、TEST_DB 不回退、身份／非空／begin 失敗、savepoint success／failure、outer rollback 失敗仍 close、操作授權／設定／thread 重查、六表比較、殘留 fail、worker IPC deadline／EOF、join／terminate／kill 及 supervisor release、清理失敗不能回報 timeout 成功、人工 count entry 不洩密，以及新檔函式長度／docstring／巢狀限制。全部一次通過，沒有修改或刪除既有測試；既有 discord.py 的 audioop 棄用 warning 不影響 Python 3.12 結果。

```powershell
$env:JUKEBOX_RUN_MYSQL_TEST = '0'
$env:JUKEBOX_RUN_REPOSITORY_MYSQL = '0'
$env:JUKEBOX_RUN_MYSQL_READONLY = '0'
.\.venv\Scripts\python.exe -m pytest -m 'not mysql and not mysql_readonly' -q --tb=short
.\.venv\Scripts\python.exe -m pytest tests/test_mysql_readonly.py::test_check_rendering_normalization -q --tb=short
.\.venv\Scripts\python.exe -m pytest tests/test_repository_mysql.py tests/test_repository_mysql_concurrent.py --collect-only -q
```

collect-only 只收集，不觸發 fixture、連線或寫入。Windows asyncio 離線測試沿用 Phase 2A 沙箱外本機 socket pair 的執行方式，所有平台互動仍 mock；未安裝依賴。

## 等待另行授權的人工 PowerShell 流程

**現在只審查，不執行。** 以下命令會連線測試庫，pytest 會執行 INSERT／UPDATE／rollback；必須使用者另行授權真實 MySQL 驗收後才執行。測試庫／帳號已存在，不需重跑 setup DDL。不得使用正式庫或管理帳號。

先以 Phase 1 已批准方式私下提供 TEST_DB_HOST、TEST_DB_PORT、TEST_DB_USER、TEST_DB_PASSWORD、TEST_DB_NAME；不要把密碼打入回報或存入 `.env`。確認沒有其他測試、Bot 或人工寫入同時使用測試庫。管理帳號另外確認仍無額外 triggers／非交易副作用；應用帳號看不到 triggers 不能证明不存在。

在已設定 TEST_DB_* 的同一 PowerShell、專案根目錄執行：

```powershell
$env:JUKEBOX_RUN_MYSQL_READONLY = '0'
$env:JUKEBOX_TEST_SCHEMA_READY = '1'
$env:JUKEBOX_DISPOSABLE_DATABASE = 'jukebox101_test'
$env:JUKEBOX_RUN_MYSQL_TEST = '1'
$env:JUKEBOX_RUN_REPOSITORY_MYSQL = '1'

try {
    $phase2aBeforeCounts = & .\.venv\Scripts\python.exe -m tests.repository_mysql_counts
    if ($LASTEXITCODE -ne 0) { throw 'Before-count preflight failed; stop without clearing tables.' }
    Write-Output $phase2aBeforeCounts

    & .\.venv\Scripts\python.exe -m pytest tests/test_repository_mysql.py tests/test_repository_mysql_concurrent.py -m mysql -q --basetemp=.pytest_tmp --tb=short -x
    $phase2aTestExitCode = $LASTEXITCODE

    $phase2aAfterCounts = & .\.venv\Scripts\python.exe -m tests.repository_mysql_counts
    if ($LASTEXITCODE -ne 0) { throw 'After-count verification failed; stop and investigate cleanup.' }
    Write-Output $phase2aAfterCounts
    if ($phase2aBeforeCounts -ne $phase2aAfterCounts) { throw 'Six-table row counts changed; stop.' }
    if ($phase2aTestExitCode -ne 0) { throw 'Repository acceptance failed; investigate before rerunning.' }
}
finally {
    $env:JUKEBOX_RUN_REPOSITORY_MYSQL = '0'
    $env:JUKEBOX_RUN_MYSQL_TEST = '0'
}
```

`tests.repository_mysql_counts` 僅固定 SELECT／metadata preflight，再 rollback／close；輸出排序穩定的 JSON 六表筆數。非空會輸出筆數並非零退出，不清理資料。執行前後應完全相同且全部 0。新 pytest fixture 還會對每個案例比較前後筆數，外層 PowerShell 比較是額外人工紀錄。

預期真實測試 **13 Passed、0 Failed、0 Skipped**；這是預期，尚未取得結果。不要用 `--showlocals`，不要以沒有資料殘留掩蓋失敗或 skipped。保留 pytest 結果、兩份六表 count JSON 及是否曾發生強制終止的紀錄。

## 異常停止與未解決風險

任何 preflight 不符、SQL error 非預期、timeout、進程非正常 exit、rollback／close 失敗、前後筆數改變，均停止；不自動重跑、退款、刪除或清表。`-x` 於首個失敗停止測試，但 pytest 正常 teardown 仍會嘗試 rollback。Ctrl+C／程序 crash 不能保證 Python finally 全部執行；MySQL 斷線後的回滾與鎖釋放需實際確認。強制 kill 即使 row count 為 0，也不能算安全清理已驗證，因為未提交交易不會出現在其他連線 committed counts。

發生異常時先將兩個執行開關設回 0，確認 worker 及 DB session 是否仍存在，由管理者調查測試庫交易／鎖；不要修改正式庫或自動刪除殘留。程式可在正常與已處理錯誤路徑 rollback，但 OS／服務中斷的無條件清理保證不存在。AUTO_INCREMENT 可有間隔，Rollback 不恢復序號；比較的是資料列筆數，不是序號。

本次未驗證真實 MySQL SQL、權限、session 設定、savepoint、鎖等待或資料殘留。額度授予、實際交換、排程與 Discord 驗收仍是 Phase 2A 既有 Pending；沒有擴大至 Phase 2B。

新增檔案：`tests/repository_mysql_support.py`、`tests/repository_mysql_concurrency.py`、`tests/repository_mysql_counts.py`、`tests/test_repository_mysql.py`、`tests/test_repository_mysql_concurrent.py`、`tests/test_repository_acceptance_safety.py` 與本文件。更新 README、ROADMAP、Phase 2A 驗收報告。Repository、connection、Schema、原安全 helper／測試、依賴與 `.env` 均未修改。

Git Status 維持全案既有未追蹤 `??`：`.env.example`、`.gitignore`、`AGENTS.md`、`README.md`、`ROADMAP.md`、`bot/`、`dependency-versions.txt`、`docs/`、`pyproject.toml`、`tests/`。未 Commit／Push／Merge。完成準備後停止，等待人工授權真實 MySQL 測試。
