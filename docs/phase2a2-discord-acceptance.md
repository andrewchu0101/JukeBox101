# Phase 2A.2 — Discord Development Guild 驗收準備

收尾更新：Phase 2A.2 Registration 的 Offline Preflight、REST Login、Development Guild Sync、Guild 顯示五組指令均人工 Passed；Phase 2A.3 DB-disabled Interaction Transport 已另行 Accepted — User Verified。只收到五組互動整體確認，沒有逐字回覆、第二帳號隱私或異常情境的逐項獨立證據；真實 Discord＋MySQL 持久化互動仍 Pending。Guild 現有五指令，**不要重跑本文件的 sync 流程**。最新矩陣見 [Closeout](phase2a-closeout.md)，下列「Pending／listener 未提供／未執行」等敘述是 Phase 2A.2 原準備階段歷史，不是目前狀態；本次沒有重新連線或測試。

原準備狀態（歷史）：**Implemented、Unit Tested、Discord Manual Acceptance Pending**，當時不是 Accepted。依據 [使用者規格](phase2a2-specification.md)，該次準備沒有真實 Discord 登入、Gateway、遠端 sync、DM、MySQL、DDL、套件安裝或 Git Commit／Push／Merge／Tag。後續人工結果以前述收尾更新為準；Phase 2B 未開始。

已知歷史：Phase 1A～1D Accepted。使用者在本次規格回報 Phase 2A.1 **Integration Accepted**：13 Passed、0 Failed、0 Skipped，7.19 秒；測試前後六表全為 0，無資料列殘留。這是使用者人工結果，本次沒有重跑。Phase 2A Foundation 已 Implemented／Unit Tested，Discord 人工驗收仍 Pending。

## 架構與安全邊界

重用既有 `JukeBoxClient`、五組 CommandHandlers、MusicService、指令註冊與 `synchronize_commands`。沒有第二套 Bot，也沒有新依賴。

- `bot/acceptance_config.py`：只讀 process environment 的 Token、明確 Guild ID、預期 Application ID；不載入 `.env`、DB_* 或 TEST_DB_*。
- `bot/guild_acceptance.py`：實際本機 CommandTree preflight；明確授權的 REST login；獨立授權與人工輸入確認的 Guild sync。
- `bot/discord_acceptance.py`：預設 preflight；`async with client` 管理 HTTP 資源；固定的安全錯誤類別輸出；執行期間停用平台 logs，結束恢復原 logging disable level。
- `DisabledAcceptanceRepository`：沒有資料庫設定或連線；任何 persistence attribute 存取直接拒絕。即使環境中 DB_NAME=jukebox101 或有 TEST_DB_*，三種入口都不會讀取或使用它們。

不覆寫 `on_ready` 或 `setup_hook`；建構及 import 不登入／同步。登入模式只呼叫 `client.login()`，不呼叫 `connect()`、`start()` 或 `run()`，所以**不是 Gateway ready／Interaction listener 測試**。不接收指令、不啟動 Daily、不發 DM。每個 REST 操作使用 30 秒 asyncio timeout；人工確認不設定自動批准或逾時批准。取消、EOF、中斷、身分錯誤及 API 失敗都沿 async context 關閉 Client；OS 強制 kill 仍不能保證 finally。

既有正式 `python -m bot` 仍是正式執行入口，可能採用正式 DB_*，**不要用它進行本次開發 Guild 驗收**。本次最小修正僅將該入口預期的 Discord 登入／HTTP／網路錯誤轉為安全類別訊息，避免原始例外內容出現在自訂 traceback。共用 Token loader 加入 printable ASCII／無內部空白及控制字元檢查；不假裝離線能驗證 Token 真偽，不將憑證寫入 repr。

## 三個明確入口

| 模式 | 網路 | 行為與限制 |
|---|---|---|
| `preflight`（預設） | 完全離線 | 必要設定、數字 ID、五指令清單、資料庫停用；建構後關閉 Client；不 login／fetch／sync |
| `login --authorize-login` | 日後授權才可 | REST 登入、查 Application 與指定 Guild，核對 expected ID 後關閉；不 sync、不連 Gateway |
| `guild-sync --authorize-login --authorize-guild-sync` | 日後另行授權才可 | 獨立登入，檢查與二次確認後，僅發布指定開發 Guild；結束關閉 |

直接對 helper 呼叫也須 `authorized is True`，不是只有 CLI 有防護。驗收 CLI 沒有 global-sync、Token 值、Guild 值或 DB override 參數；ID 由明確環境設定提供，不硬編碼。runtime ID 拒絕字串、布林、0、負數或超過 64 位；環境設定僅接受 ASCII 十進位正整數。未知 mode／缺少授權先於讀憑證及建構 Client 被拒絕。

必要設定只有：

| 變數 | 來源 |
|---|---|
| DISCORD_BOT_TOKEN | 使用者本機私下提供；不貼聊天、不放命令列、不寫 shell history |
| DISCORD_DEV_GUILD_ID | 使用者確認的專用開發 Guild，不使用未知 ID |
| DISCORD_EXPECTED_APPLICATION_ID | 使用者從所選 Application 確認的 ID；不能根據登入結果自動接受 |

不修改 `.env`、IDE settings 或 `python.terminal.useEnvFile`。DB_HOST、DB_PORT、DB_USER、DB_PASSWORD、DB_NAME 仍屬正式應用程式設定；TEST_DB_* 仍屬既有隔離測試設定，兩者都不混入此驗收工具。

## Guild Sync 防護與限制

既有 `copy_global_to` 只複製本機定義；`tree.sync(guild=...)` 才將該範圍的整份命令集合送至 Discord，可能更新／移除此 Application 在該 Guild 的指令。原 helper 的 global opt-in 仍留在原架構供未來另行批准用途，但**本次驗收入口完全沒有通往 global_sync=True 的選項或呼叫**。

同步前流程：

1. 核對本機五指令清單與 expected Application／Guild。
2. 查目前登入 Application，必須同時符合 expected ID 與 client cached application ID。
3. `fetch_guild` 確認 Token 能存取明確指定 Guild，回傳 ID 必須相符。
4. 讀取此 Application 在該 Guild 的遠端指令；**只接受完全空集合**。任何既有指令（包含同名或上次已同步的五組）都停止，不覆寫、不清理。
5. 顯示 Application ID、Guild ID、五指令名稱，要求使用者確認沒有其他操作者變更目標，並逐字輸入 `SYNC <Application ID> <Guild ID>`。其他輸入、空白、EOF 或中斷都不批准。
6. 確認後重查本機 registry、遠端身分與空集合，再呼叫既有 helper，僅傳指定 `guild_id`。
7. 檢查回傳指令清單；不同或 HTTP／timeout 失敗則停止，不自動重試或撤回。

讀取遠端指令只涵蓋此 Application 的 Guild scope，不代表已盤點其他 Application。API 沒有在此流程使用 compare-and-swap：最後一次讀取與 bulk sync 之間仍有競態窗口，必須確保專用 Guild／Application 的指令沒有其他部署、管理工具或操作者同時修改。若無法確認獨占，**不要執行 sync**。

首次成功後重新執行會因非空而停止，這是保護行為。後續更新、既有指令比較／遷移或清理需另行審查，本工具不自動刪除。sync timeout／中斷可能發生在 Discord 已接受請求之後，不能宣稱遠端未變更；先人工查清狀態，勿盲目重跑。

## PowerShell 人工流程

目前只執行過離線 synthetic 設定測試，沒有使用真實 Token。下列 function 不會在定義時執行任何登入。Token 以遮罩輸入，不出現在 shell history，使用完會從本程序環境移除；安全字串在送入 Discord 前仍必須轉成記憶體中的字串，不能宣稱零記憶體殘留。

先在專案根目錄定義：

```powershell
function Invoke-JukeBoxAcceptance {
    param(
        [ValidateSet('preflight', 'login', 'guild-sync')]
        [string]$Mode = 'preflight',
        [switch]$AuthorizeLogin,
        [switch]$AuthorizeGuildSync
    )

    $acceptanceToken = $null
    try {
        Set-Location -LiteralPath 'D:\Project\MusicBased\discordBot_JukeBox101' -ErrorAction Stop
        $env:DISCORD_DEV_GUILD_ID = Read-Host '輸入已確認的開發 Guild ID'
        $env:DISCORD_EXPECTED_APPLICATION_ID = Read-Host '輸入預期 Application ID'
        $acceptanceToken = Read-Host '本機輸入 Bot Token（不貼聊天）' -AsSecureString
        $acceptancePointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($acceptanceToken)
        try {
            $env:DISCORD_BOT_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($acceptancePointer)
        }
        finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($acceptancePointer)
        }

        $acceptanceArguments = @('-m', 'bot.discord_acceptance', $Mode)
        if ($AuthorizeLogin) { $acceptanceArguments += '--authorize-login' }
        if ($AuthorizeGuildSync) { $acceptanceArguments += '--authorize-guild-sync' }

        & .\.venv\Scripts\python.exe @acceptanceArguments
        if ($LASTEXITCODE -ne 0) {
            throw '驗收入口停止；確認原因與遠端狀態，不自動重試。'
        }
    }
    finally {
        Remove-Item Env:DISCORD_BOT_TOKEN -ErrorAction SilentlyContinue
        Remove-Item Env:DISCORD_DEV_GUILD_ID -ErrorAction SilentlyContinue
        Remove-Item Env:DISCORD_EXPECTED_APPLICATION_ID -ErrorAction SilentlyContinue
        if ($null -ne $acceptanceToken) { $acceptanceToken.Dispose() }
    }
}
```

**A：可先做 Offline Preflight**（無網路）：

```powershell
Invoke-JukeBoxAcceptance -Mode preflight
```

預期輸出 public JSON：指定 Application／Guild、`balance/daily/exchange/history/share`、database=disabled、automatic_sync=false。這只檢查 Token 存在／基本格式，不證明 Token 正確或登入成功。此 function 最後清除三個 DISCORD_* 變數；不變更既有 DB_*／TEST_DB_*，也不寫 `.env`。

**B：待使用者另行授權 Login Smoke Test**，確認 Developer Portal 所選 Application 與 Token 配對；Bot 已由管理者安裝至專用開發 Guild，採 `bot`／`applications.commands` 的必要 scope，避免 Administrator 或與本功能無關的權限。未完成環境確認不要執行：

```powershell
# 只有另行授權登入後才執行：
Invoke-JukeBoxAcceptance -Mode login -AuthorizeLogin
```

記錄顯示的 Application／Guild，確認符合人工預期，程式正常結束。這只確認 REST 登入及 Guild access，沒有 Gateway Ready、線上指令接收或 DM 驗證。

**C：待使用者另外授權指定 Guild 同步**，先確認此 Application 在該 Guild 沒有既有指令、沒有其他操作者／部署同步；核對終端顯示再輸入指定確認字串：

```powershell
# 只有另行授權 Guild Sync 後才執行：
Invoke-JukeBoxAcceptance -Mode guild-sync -AuthorizeLogin -AuthorizeGuildSync
```

不提供 Global Sync 命令，也不提供刪除遠端指令的快捷方式。不要將 B、C 加入 A 的自動後續步驟、排程、on_ready 或 setup_hook。

## 五組 Slash Commands 人工驗收表

本工具三模式均不啟動 Gateway，因此目前不能直接透過它在 Discord 操作下表；不能把「遠端指令出現」視為 Interaction 驗收成功。後續需另行審查並授權限定 Guild 的 Gateway listener；涉及資料的案例還須先批准隔離資料生命週期。既有 mock 測試已驗證 callback 私密 defer／reply、錯誤及長度，不能取代真人觀察。

| 指令／案例 | 日後真人預期 | 目前狀態 |
|---|---|---|
| `/share` 有效 YouTube URL | 私密 pending 提示；不發點；非真實驗證歌不進交換池 | DB／Interaction Pending |
| `/share` 非法 URL | 私密拒絕，不寫 DB、不回顯惡意輸入 | Gateway／Interaction Pending，離線已測 |
| `/share` 同影片重複 | 私密 duplicate 提示；同 Video ID 不重複發點 | DB／Interaction Pending |
| `/exchange` | 指令存在、私密回覆未開放；不扣點、不建推薦 | Gateway／Interaction Pending，離線已測 |
| `/balance` 新／既有帳號 | 分別顯示 0／正確儲存餘額 | DB／Interaction Pending |
| `/balance` DB 故障 | 私密服務錯誤，不偽裝 0、不洩密 | 本入口 DB-disabled 可供日後 listener 故障驗收；目前 Interaction Pending |
| `/history` 空／多筆 | 私密空提示；穩定排序；最多 10；少於 1900 字元；不洩漏別人資訊 | DB／Interaction Pending |
| `/daily on/off` 及重複 | 私密確認最終偏好；重啟查詢仍保存；無 Daily task／DM | DB／Interaction Pending |

真人需由第二個測試帳號確認 ephemeral 訊息不對其他使用者顯示，並核對同一 Interaction 無公開 fallback。現在沒有登入或送出任何真人回覆。

## DB 隔離及資料準備方案

Phase 2A.1 的 Integration Accepted 證明其 rollback-only 測試範圍，不代表真實 Bot 指令能自動回復資料。正式 Repository 每個操作成功會 Commit，不能把其 connection 放入 Phase 2A.1 外層交易後跨 Discord worker 共用。

因此本次**不提供 DB-enabled Discord listener 或資料 seed／清除腳本**。需要資料的人工案例均 Pending。後續方案必須獨立審查：

1. 使用既有專用 `jukebox101_test`／`jukebox101_test_app@127.0.0.1`，重用 TEST_DB 固定目的地、實際身分／Schema preflight，不將 TEST_DB 映射成正式 Bot 啟動環境。
2. 人工確認帳號 DML 權限、無正式庫存取／額外 triggers，停止其他測試，使用專用測試使用者與唯一 run marker，先記錄六表 baseline。
3. 明確批准 seed、正式 Commit 與資料生命週期；只建立最小 synthetic 餘額／歷史資料，標記人工測試 provenance，不假稱真實驗證或派送成功。
4. 真人測試期間資料會持久化，結束後先停止 listener、確認 worker／連線結束，再比較筆數與所屬 IDs。
5. 清理必須以審查過的精確 fixture ID、FK 順序和 transactions 限縮；沒有獲准前不自動 DELETE、TRUNCATE 或恢復，不能僅比較總筆數就宣告還原。

在上述方案未批准前，使用 DB-disabled 工具，不使用正式 `python -m bot` 代替驗收，不將 DB 故障測試連向正式庫。未解決的 seed／cleanup 屬於待決方案，不是已完成的自動還原功能。

## 離線結果與檔案

本次主回歸 **238 Passed、0 Failed、0 Skipped、34 Deselected**；另補跑 2 項既有純文字 CHECK normalization，**2 Passed、0 Failed、0 Skipped**。合計 **240 個不同離線案例通過**，包括原 182 項及新增 58 項；34 項排除為 32 項真實 MySQL 與另行補跑的 2 項純文字案例。先前人工 13 Passed 只是歷史，未在此重跑。

preflight 已用 synthetic environment、實際 JukeBoxClient 及真正 CLI `main()` 離線執行；輸出 public JSON 且 Client 已關閉；沒有宣稱檢查使用者真實 Token／ID。新測試所有 HTTP request／login／Gateway／DM／MySQL 入口皆 fail-fast 或 mock，涵蓋 missing Token／ID、非法 ID 型別、指令清單、無授權、無 global 選項、取消／EOF／中斷、App 不符、遠端非空／確認期間變更、API／登入失敗、close、不洩密、不接資料庫與無自動 hook。新增 AST 品質檢查沿用標準庫，bot 與新測試函式皆在 25 行／3 層限制內，沒有新依賴。

```powershell
$env:JUKEBOX_RUN_MYSQL_TEST = '0'
$env:JUKEBOX_RUN_REPOSITORY_MYSQL = '0'
$env:JUKEBOX_RUN_MYSQL_READONLY = '0'
.\.venv\Scripts\python.exe -m pytest -m 'not mysql and not mysql_readonly' -q --tb=short
.\.venv\Scripts\python.exe -m pytest tests/test_mysql_readonly.py::test_check_rendering_normalization -q --tb=short
```

Windows asyncio 仍需在經自動審查允許的沙箱外建立本機 socket pair；所有外部平台操作保持 mock。已安裝 discord.py 的 audioop 棄用 warning 不影響 Python 3.12 測試，不以安裝或升級依賴處理。

新增：`bot/acceptance_config.py`、`bot/guild_acceptance.py`、`bot/discord_acceptance.py`、`tests/test_guild_acceptance.py`、本報告與規格存檔。修改：共用 Token loader、既有主入口的錯誤邊界、import 安全測試新增三個模組、README／ROADMAP／既有驗收文件的歷史狀態。未修改 Repository、Schema、MySQL connection、安全 fixture、依賴或 `.env`。

Git Status 仍為全案既有 `??` 未追蹤狀態，未 Commit／Push／Merge／Tag。完成本地準備後停止，等待人工回報與另行授權，不開始 Phase 2B。
