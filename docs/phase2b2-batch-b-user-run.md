# Batch B — 使用者本機 PowerShell 驗收流程

**現況更新（User Reported）**：兩組 Preflight 與 Migration 2b2-b1 已通過，23 表已建立，Migrator 鎖定、control OFF。以下部署段落保留為歷史操作說明，請勿重跑 Migration。最新 [Harness／真實整合測試流程](batch-b-real-mysql-tests.md)僅待新 harness 帳號的一次性 DBA 操作及另行測試授權，本輪未連線或寫入資料庫。

本輪完成雙帳號離線安全改造，尚未授權執行資料庫操作。Codex 準備／維護程式，使用者在自己的 PowerShell 提供 TEST_DB_* 並執行；不用把密碼傳入 Codex App，也不讀 .env。真實 Migration／InnoDB／帳務整合套件仍待依本輪實際庫盤點結果補齊，現有 Fake／Mock 案例不能當成真實整合指令。

## 第一輪命令

以下命令只執行 `tests.batch_b_preflight`，不會呼叫 Migration Runner、測試寫入或資料清理。密碼僅在使用者本機程序內供 PyMySQL 認證使用；不放命令列、報告或聊天。預設只接受 127.0.0.1:3306、jukebox101_test、jukebox101_test_app，另核對實際 CURRENT_USER() 為 jukebox101_test_app@127.0.0.1。

```powershell
Set-Location -LiteralPath 'D:\Project\MusicBased\discordBot_JukeBox101'

$env:TEST_DB_HOST = '127.0.0.1'
$env:TEST_DB_PORT = '3306'
$env:TEST_DB_USER = 'jukebox101_test_app'
$env:TEST_DB_NAME = 'jukebox101_test'

$preflightSecretCreated = $false
$preflightSecureInput = $null
$preflightReportPath = Join-Path ([System.IO.Path]::GetTempPath()) ('jukebox101-batch-b-preflight-{0}.json' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
$writeSwitches = @('JUKEBOX_RUN_MYSQL_TEST', 'JUKEBOX_RUN_REPOSITORY_MYSQL', 'JUKEBOX_RUN_MYSQL_READONLY')

try {
    foreach ($switchName in $writeSwitches) {
        Set-Item -LiteralPath ('Env:' + $switchName) -Value '0'
    }
    if ([string]::IsNullOrWhiteSpace($env:TEST_DB_PASSWORD)) {
        $preflightSecureInput = Read-Host '專用測試帳號密碼（不回顯）' -AsSecureString
        $env:TEST_DB_PASSWORD = [System.Net.NetworkCredential]::new('', $preflightSecureInput).Password
        $preflightSecretCreated = $true
    }
    $env:JUKEBOX_RUN_BATCH_B_PREFLIGHT = '1'
    & .\.venv\Scripts\python.exe -B -m tests.batch_b_preflight | Tee-Object -FilePath $preflightReportPath
    $preflightExitCode = $LASTEXITCODE
    Write-Host ('Preflight exit code: ' + $preflightExitCode)
    Write-Host ('Report: ' + $preflightReportPath)
}
finally {
    $env:JUKEBOX_RUN_BATCH_B_PREFLIGHT = '0'
    foreach ($switchName in $writeSwitches) {
        Set-Item -LiteralPath ('Env:' + $switchName) -Value '0'
    }
    if ($preflightSecretCreated) {
        Remove-Item -LiteralPath 'Env:TEST_DB_PASSWORD' -ErrorAction SilentlyContinue
    }
    if ($null -ne $preflightSecureInput) {
        $preflightSecureInput.Dispose()
    }
}
```

若 TEST_DB_PASSWORD 原已存在，保留使用者原有設定；若是本段遮蔽輸入新增，finally 移除。命令不安裝套件、不更動資料庫權限。報告寫至使用者的暫存目錄，避免混入 Git 工作區。

## 回報與停止條件

請貼回 JSON 報告與 exit code，不要貼密碼、環境變數全清單或原始認證錯誤。

- `READONLY_EMPTY_BASELINE_REVIEW_REQUIRED`／exit 0：目前檢查到六表空白且沒有已知 blocker，仍須等報告審核，**不代表已授權執行 Migration**。
- `STOP`／exit 1：停止，不執行 Migration 或自行清資料／擴權。
- 1049：資料庫不存在；1045：認證未通過；1142：必要查詢權限不足。只輸出錯誤編號與類別，不輸出原 driver 訊息。
- 任何非空資料、既有 jb2_*、不明物件／view、非 InnoDB、跨 schema／全域權限、未審查 active roles、缺少六表約束或必要權限，都須先確認來源與安全性。Application 不要求 CREATE／REFERENCES；Application 若有 DDL 權限反而 STOP。Migration 另以獨立帳號檢查，不自動 GRANT。

入口使用 READ ONLY transaction，最後 Rollback／close，沒有 Commit、DDL、INSERT、UPDATE、DELETE 或 Migration 呼叫。報告包含固定實際身分、版本、權限名稱、所有物件清單、已知基礎表精確筆數、實際 SHOW CREATE fingerprint、欄位／索引 metadata 與既有 Migration marker；不讀取 view 內容或未知物件資料。

這是盤點報告，不是資料備份。只有確認空白隔離庫，才可在後續審核中認定無需資料備份；若已有資料或既有 Batch B 結構，必須先核准保留／備份與精確清理策略，不得套用空白庫假設。後續真正自行 Commit 的 Repository 測試不能用外層 Rollback 假裝清理完成。

## 本次 Codex 離線證據

新增 `tests/batch_b_preflight.py` 與 `tests/test_batch_b_preflight.py`。只以合成環境及 Mock Connection 驗證，Codex 未執行任何真實連線入口。

定向：23 Passed（含既有品質檢查）；明列安全離線回歸：514 Passed／0 Failed／0 Skipped，包含原有 492 與新增 22。沿用 Batch B 驗收文件的無 socket／無 cache／無 .env 防護命令，在原測試清單加上 `tests/test_batch_b_preflight.py`；最後防護事件清單為空。未宣稱真實 Preflight、Migration 或帳務驗收通過。

本輪雙帳號入口與操作順序見下方；Migration 命令僅供後續人工核准後使用，不開始 Batch C。


## 雙帳號操作順序（本輪僅離線驗證）

1. 使用者先前回報 MySQL 8.0.46、六表 InnoDB 且筆數全零；原 STOP 原因只有缺 DDL。這是 User Reported，修改後仍需重新取得新報告。
2. DBA 人工審查 `docs/batch-b-minimum-grants.sql`。它是草案，不由任何 Python 入口執行。本輪不得執行。帳號先 ACCOUNT LOCK，既有帳號須停止查明來源，勿用 IF NOT EXISTS 掩蓋不明帳號。正式核准後才由本機 DBA 設定密碼、GRANT、解鎖。Application 不加 DDL。
3. 停止所有測試庫寫入者。依序執行下列兩個**唯讀**檢查並回報 sanitized JSON、exit code；不得因 exit 0 就執行 Migration。
4. 只有重新確認六表空白、無不明物件、隔離安全及另獲 Migration 核准後，才使用最後的執行命令。空白報告不是備份；非空或既有 jb2_* 均 STOP，需要另行保留／備份與復原計畫。
5. 完成 Migration 後立即關閉開關並鎖回／撤除 migrator 存取（需 DBA 另行授權操作）。Application 改採逐表最小權限；schema-level 寫入會被新 Preflight 阻擋。必須等表建立後按 SQL 草案補 app 新表權限，不給 CREATE，registry 僅 SELECT。後續 InnoDB 並行／Nickel 結算驗收仍待專用真實整合套件與另行授權。

### 共用本機函式：不回顯密碼，finally 關閉所有入口

在專案根目錄貼上以下函式。五個欄位只寫入所選程序環境，不讀 .env、不讀另一組密碼。不要開啟 PowerShell transcript、debug trace 或輸出環境全清單。

```powershell
Set-Location -LiteralPath 'D:\Project\MusicBased\discordBot_JukeBox101'
function Invoke-JukeBoxDatabaseStep {
    param(
        [ValidateSet('ApplicationPreflight','MigrationPreflight','MigrationExecute')]
        [string]$Step
    )
    $stepIsMigration = $Step -ne 'ApplicationPreflight'
    $stepPrefix = if ($stepIsMigration) { 'TEST_MIGRATION_DB_' } else { 'TEST_DB_' }
    $stepUser = if ($stepIsMigration) { 'jukebox101_test_migrator' } else { 'jukebox101_test_app' }
    $stepEntry = switch ($Step) {
        'ApplicationPreflight' { 'tests.batch_b_preflight' }
        'MigrationPreflight' { 'tests.batch_b_migration_preflight' }
        'MigrationExecute' { 'tests.batch_b_migrate' }
    }
    $stepSwitch = switch ($Step) {
        'ApplicationPreflight' { 'JUKEBOX_RUN_BATCH_B_PREFLIGHT' }
        'MigrationPreflight' { 'JUKEBOX_RUN_BATCH_B_MIGRATION_PREFLIGHT' }
        'MigrationExecute' { 'JUKEBOX_RUN_BATCH_B_MIGRATION' }
    }
    $stepSwitches = @('JUKEBOX_RUN_BATCH_B_PREFLIGHT','JUKEBOX_RUN_BATCH_B_MIGRATION_PREFLIGHT',
        'JUKEBOX_RUN_BATCH_B_MIGRATION','JUKEBOX_RUN_MYSQL_TEST','JUKEBOX_RUN_REPOSITORY_MYSQL',
        'JUKEBOX_RUN_MYSQL_READONLY')
    $stepSecret = $null
    try {
        foreach ($entrySwitch in $stepSwitches) { Set-Item -LiteralPath ('Env:' + $entrySwitch) -Value '0' }
        Set-Item -LiteralPath ('Env:' + $stepPrefix + 'HOST') -Value '127.0.0.1'
        Set-Item -LiteralPath ('Env:' + $stepPrefix + 'PORT') -Value '3306'
        Set-Item -LiteralPath ('Env:' + $stepPrefix + 'USER') -Value $stepUser
        Set-Item -LiteralPath ('Env:' + $stepPrefix + 'NAME') -Value 'jukebox101_test'
        $stepSecret = Read-Host ($stepUser + ' 密碼（不回顯）') -AsSecureString
        Set-Item -LiteralPath ('Env:' + $stepPrefix + 'PASSWORD') -Value ([System.Net.NetworkCredential]::new('', $stepSecret).Password)
        Set-Item -LiteralPath ('Env:' + $stepSwitch) -Value '1'
        & .\.venv\Scripts\python.exe -B -m $stepEntry
        $stepExitCode = $LASTEXITCODE
        if ($stepExitCode -ne 0) { throw ('STOP: exit code ' + $stepExitCode) }
    }
    finally {
        foreach ($entrySwitch in $stepSwitches) { Set-Item -LiteralPath ('Env:' + $entrySwitch) -Value '0' }
        Remove-Item -LiteralPath ('Env:' + $stepPrefix + 'PASSWORD') -ErrorAction SilentlyContinue
        if ($null -ne $stepSecret) { $stepSecret.Dispose() }
    }
}
```

此函式會覆寫並於 finally 移除所選 namespace 的 PASSWORD；其他 namespace 不會被讀取。請在專用 PowerShell 視窗使用。

只做 Application 唯讀檢查：

```powershell
Invoke-JukeBoxDatabaseStep -Step ApplicationPreflight
```

取得第一份報告並確認後，再做 Migration 唯讀檢查（即使 execution 開關未設，也能獨立檢查）：

```powershell
Invoke-JukeBoxDatabaseStep -Step MigrationPreflight
```

**以下會執行 DDL 與 bootstrap 寫入，僅在另獲人工授權後執行，本輪不執行：**

```powershell
Invoke-JukeBoxDatabaseStep -Step MigrationExecute
```

兩個 readonly report 分別包含 `application_readiness`／`migration_readiness`；另一帳號一律 `NOT_CHECKED_SEPARATE_ACCOUNT`，`migration_authorized=false`。空白庫通過仍只表示 READY／READY_FOR_MANUAL_REVIEW。執行入口會再次讀取完整唯讀 inventory，遇非空、未知、既有 Batch B 結構或不足權限即停止；不會清表。

## 權限與復原界線

- 最終最小權限草案：`docs/batch-b-minimum-grants.sql`。Migration 逐表 SELECT；新表 CREATE；FK 父表 REFERENCES；僅 jb2_migrations／jb2_control／jb2_legacy 有 INSERT，只有 registry 有 UPDATE。無 DELETE、ALTER、DROP、INDEX、全域權限或 GRANT OPTION。
- CREATE TABLE 內建索引使用 CREATE；FK 另要求父表 REFERENCES。未建立表的 GRANT 必須包含 CREATE，因此 app 的逐表 DML 不可提前授權。MySQL 官方依據：[GRANT](https://dev.mysql.com/doc/refman/8.0/en/grant.html)、[Privileges](https://dev.mysql.com/doc/refman/8.0/en/privileges-provided.html)。
- 權限檢查合併 schema-level／table-level，但 migrator 若對既有表多出寫入權限也會 STOP。拒絕全域／跨庫權限、GRANT OPTION 與 active roles。欄位授權不作為本階段必要權限的替代。
- MySQL schema grant 的 `_` 可能具有 pattern 意義；新草案採完全限定逐表 grant。既有 Application 的 schema DML 必須由 DBA 另行授權收斂；新 Preflight 會拒絕它，較窄的 table GRANT 不會撤除舊權限。不可自行 REVOKE。
- runner 連線前要求固定 migrator profile、execution switch=1 及明確 invocation token；連線後再次核對實際帳號／庫／port／MySQL 8／無 active role。注入 connection factory 是內部測試接縫，不是任意連線的公開配置入口。
- DDL 非原子；`applying` marker 會先 Commit。中途失敗只 rollback 尚未提交的 bootstrap，已成功 DDL 與 marker 保留，必須人工盤點，不自動 DROP／重跑。CLI 保守拒絕已存在 jb2_*，包括成功後再次執行；底層 runner 的相同 digest noop 仍有 Mock 覆蓋，但 CLI 不把它當作自動復原。
- Preflight 與 DDL 是不同 session，advisory lock 只協調配合此 runner 的 migration，不會阻止任意 application／DBA 寫入。**人工維持測試庫停寫**是本輪安全前提；尚未實作全庫鎖定或跨 session 快照保留。
- `SHOW CREATE`／engine 驗證不是完整欄位、FK 與實際交易行為驗收。所有 MySQL 權限可用性、DDL 語法及真實並行仍待使用者實測。

## 本輪離線結果

雙帳號定向與品質：51 Passed；Batch A／Batch B 安全離線回歸：532 Passed、0 Failed、0 Skipped。使用既有 Python 加 `-B`、pytest 停用 cache／plugin autoload，audit hook 禁止所有 socket、.env 讀取及檔案寫入；防護事件為空。沒有執行上述真實連線入口，未標記人工 Accepted。


另有 metadata 可見性界線：逐表最小 grants 的 migrator 可能看不到未獲授權的不明表，因此 `unknown_tables=[]` 只代表此帳號可見範圍。DBA 必須比對具有完整 schema SELECT 的 Application 報告及自己完整 inventory，證明無隱藏物件；無法確認時不得執行 Migration。不得因 migrator 報告空白就自認完成全庫隔離證明。


## 最終逐表權限覆核

最新規範、完整 23 表對照與限制見 [權限核對報告](batch-b-privilege-audit.md)。Application 不授予 DELETE，jb2_migrations 僅 SELECT；舊 schema 寫權須另經 DBA 核准移除。新 SQL 草案不是既有 ACL 的自動收斂腳本。


## 單一命令 Application 唯讀啟動器（建議）

在本機 PowerShell 執行以下唯一命令；不需要把密碼提供給 Codex，也不需要事先設定環境變數。腳本依自身位置決定專案根目錄，固定使用該專案 `.venv` Python。

```powershell
& 'D:\Project\MusicBased\discordBot_JukeBox101\scripts\run-batch-b-app-preflight.ps1'
```

若組織 Execution Policy 阻擋腳本，停止並依本機規範處理；本文件不要求關閉或繞過政策。

- `Read-Host -AsSecureString` 僅在本機互動輸入。密碼不寫進父程序環境、檔案或命令列；只於 ProcessStartInfo 的子程序環境提供，結束時移除並釋放 SecureString。既有父程序 TEST_DB_PASSWORD 會被清除，不予還原。環境認證仍會在子程序記憶體中短暫存在，SecureString 不是對本機管理員的隔離機制。
- 僅執行 `python -B -m tests.batch_b_preflight`。所有已知及繼承的 JUKEBOX_RUN_* 開關預設關閉，只有子程序的 Application readonly Preflight 開關設為 1；Migrator 環境設定不傳入子程序。
- stdout 必須是既有 Preflight 的 JSON 且根欄位符合預期；raw stderr、traceback 或啟動例外不輸出。僅顯示 JSON、exit code 及暫存報告路徑。報告以隨機名稱存入系統暫存目錄，不寫入專案；檔案只有解析後的非機密 JSON。
- 正常完成保留 Python 原始 exit code（含非零）。Python 尚未產生 exit code 的啟動失敗／120 秒逾時使用 launcher code 2；逾時會終止擁有的子程序並最多等待 5 秒清理。若 JSON 或報告寫入失敗，顯示 sanitized STOP，已取得的 Python exit code 仍保留；因此還須檢查 JSON status，不能單靠 exit 0。
- finally 清除父程序 TEST_DB_PASSWORD、子程序設定中的密碼、關閉開關並 Dispose SecureString；不讀 .env、Credential Manager 或任何其他 credential store，不執行 Migration 或權限操作。

離線驗證命令（不會啟動 Python Preflight）：

```powershell
& .\tests\test_app_preflight_launcher.ps1
```

PowerShell 7.6.5 與 Windows PowerShell 5.1 各 12 Passed／0 Failed／0 Skipped。案例覆蓋固定目標、child-only password、禁用開關、SecureString Dispose、正常 exit 0／1／17、例外、非法輸出、報告失敗、缺少 Python、Process 啟動失敗／逾時清理。Process、密碼輸入與報告儲存皆由本機 Fake／Mock 取代；沒有 MySQL 連線或真實 credential 使用。
