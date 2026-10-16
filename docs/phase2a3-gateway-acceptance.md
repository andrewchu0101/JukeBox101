# Phase 2A.3 — Gateway Interaction 驗收準備

最新限定狀態：**DB-disabled Gateway Interaction Transport Accepted — User Verified**。使用者確認 Offline Preflight、Application／Guild 核對、Gateway Ready Passed，Remote Command Contract Matched，且五組指令真人互動完成。這不將整個 Phase 2A 或 Phase 2B 標為全面完成；真實 Discord＋MySQL 持久化互動仍 Pending。

**證據限制**：只有使用者的整體確認，沒有逐指令實際回覆、第二帳號 ephemeral 可見性或異常情境的逐項獨立證據。下方回覆文字與人工表仍是程式預期／待補證據，不一律改成 Passed。詳見 [收尾矩陣與 Phase 2B 前盤點](phase2a-closeout.md)。本次只文件更新與唯讀審查，不重跑離線測試、真實 Discord／Gateway／REST／MySQL，不讀 Token、不修改 `.env`。

原準備依據 [使用者規格](phase2a3-specification.md)。Phase 1A～1D、Phase 2A.1 Repository Integration 保持 Accepted；13 Passed／0 Failed／0 Skipped、前後六表全 0 是既有人工歷史。Phase 2A.2 Registration 四項均人工 Passed。下列「未來授權／未執行／Pending」等字樣保留準備與操作邊界，不推翻上述後續人工結果，也不授權本次重新啟動。背景 ID 保留在原始規格供人工核對，程式仍從明確設定讀取。

## 實作與安全邊界

重用 `JukeBoxClient`、`SafeCommandTree`、`CommandHandlers`、既有 `_respond` 私密回覆及 URL parser。沒有另一套正式 Bot 或另一份指令註冊，也沒有修改歌曲／額度／Daily 業務。

新增：

- `bot/gateway_acceptance.py`：預設完全離線的 `preflight`；只有 `listen --authorize-gateway` 可進行未來真人登入及連線。
- `bot/gateway_listener.py`：專用服務注入、Guild／Application admission、登入與遠端契約唯讀檢查、Gateway 生命週期。
- `bot/gateway_service.py`：五組 acceptance-only 操作，無 repository／database／ledger／scheduler 依賴。
- `bot/command_contract.py`、`bot/development_command_contract.json`：本地參數契約與遠端 payload 比對。
- `bot/acceptance_runtime.py`：REST 與 Gateway CLI 共用 logging 隔離、asyncio runner 及安全例外邊界。
- `tests/test_gateway_acceptance.py`、本報告、原規格存檔。

修改：`bot/discord_app.py` 只加入可選的 CommandTree admission policy，正式流程預設 None；既有 `bot/discord_acceptance.py` 改用共享 CLI 執行邊界；import 副作用測試納入新模組；README／ROADMAP／Phase 2A.2 文件更新。沒有修改 Repository、MySQL connection、Schema、資料庫安全 fixture、依賴或 `.env`。

Gateway 驗收服務沒有 repository 欄位，不建構正式 MusicRepository，也不呼叫 DB_*／TEST_DB_* loader。即使 process environment 有正式 DB_NAME=jukebox101 或測試帳號憑證，都不會讀取或使用。不提供資料庫參數、發點、扣點、seed、清理或 migration 功能；本階段不能宣稱已驗證持久化。

## 五組實際預期回覆

| 已發布指令 | Acceptance-only 預期 |
|---|---|
| `/share url:<有效 YouTube URL>` | `Development / DB-disabled：網址格式有效；沒有儲存歌曲或投稿，也沒有發放點數。` |
| `/share url:<非法 URL>` | 沿用明確 URL 格式拒絕，不回顯原始輸入或 claim 已保存 pending |
| `/exchange` | `Development / DB-disabled：交換功能尚未啟用；未扣點，也未建立推薦紀錄。` |
| `/balance` | `Development / DB-disabled：資料庫停用，無法查詢餘額。`；不顯示假的 0 |
| `/history` | `Development / DB-disabled：資料庫停用，無法查詢歷史紀錄。`；不顯示假的空歷史 |
| `/daily mode:on`、`off` | `Development / DB-disabled：收到 on/off，但設定未保存；不啟動排程，也不發送 DM。` |

所有正常指令先以 `defer(ephemeral=True, thinking=True)` acknowledge，再 `edit_original_response`，沒有公開 fallback。mentions 由既有 Client 及回覆層停用，不回顯使用者 URL／mention／其他成員。回覆 HTTP 失敗只記錄安全類別，不重新執行 service 或重送公開訊息。非法 mode 沿用既有私密拒絕，不保存。

## Guild／Application 核對

建立 listener 前須有：

- `DISCORD_BOT_TOKEN`：既有 secret-safe loader；僅 process environment，不讀 `.env`。
- `DISCORD_DEV_GUILD_ID`：使用者明確提供的開發 Guild。
- `DISCORD_EXPECTED_APPLICATION_ID`：使用者獨立核對的 Application，不根據 Token 自動接受。

ID 採既有正整數／64 位與 ASCII 環境格式檢查，拒絕 0、負數、布林或不正確型別。未授權 listen、preflight 帶 Gateway 開關、任何 sync mode 都先於讀設定與建構 Client 被拒絕。

未來經授權的啟動順序：

1. 本地 preflight：五組名稱及完整參數契約。
2. 重用 Phase 2A.2 REST login smoke，核對 Application ID／cached application ID，並唯讀確認指定 Guild 可存取。
3. 再確認 Application 身分，且未配置外部 Interaction Endpoint URL；外部 endpoint 會使 Gateway routing 不符合本次驗收，必須停止。
4. **唯讀** fetch 此 Application 在指定 Guild 已發布的指令，與本地參數契約比較；有差異、缺指令或讀取失敗立即停止，不 sync。
5. 印出不含 Token 的本地／遠端核對摘要，才呼叫 `client.connect(reconnect=False)`。

Guild-scoped 發布不是權限邊界。因此每一筆 Interaction 先經 `SafeCommandTree.interaction_check` 的 `GuildInteractionGuard`，比對 `interaction.guild_id`、`interaction.application_id` 與 application-command type；**此判斷位於 SDK 選項解析及 callback 前**。DM、其他 Guild、其他 Application 一律私密拒絕，且不呼叫 service。已 acknowledge 的非法請求不補送公開或重複回覆；拒絕回覆失敗仍然拒絕 dispatch。

這限制的是命令處理範圍，不能聲稱 Discord Gateway 只傳送該 Guild 的事件。Client 不使用 Message Content、members、presences；保持 `Intents.none()`，不要求 Administrator。既有應用程式需已安裝在人工核對的開發 Guild，沒有新邀請／權限變更操作。

## Command Registry 契約

本地 JSON 是 **Phase 2A.2 本地註冊基線，不是 Discord 匯出**。本次沒有可離線讀取的已發布完整 payload，不能由五個名稱及人工成功顯示就聲稱遠端參數完全一致。

| 指令 | command type | 選項 | option type | 必填 | choices |
|---|---:|---|---:|---|---|
| share | 1（Chat Input） | url | 3（String） | true | 無 |
| exchange | 1 | 無 | — | — | — |
| balance | 1 | 無 | — | — | — |
| history | 1 | 無 | — | — | — |
| daily | 1 | mode | 3（String） | true | on、off |

比對還保留參數順序、額外參數／nested options、autocomplete、min/max 值／長度、channel types 及 choices 的名稱／值。以 SDK `to_dict()` 正規化合理缺省值；忽略 generated IDs、描述與其他非 invocation metadata，不宣稱完整權限／localization 結構一致。

最新人工結果：使用者確認 **Remote Command Contract Matched**；本次未重新查詢遠端，也沒有取得完整遠端 payload 存檔。離線 preflight 的真實程式行為仍為 `remote_contract=not_checked_offline`，本地 JSON 仍不是 remote export，兩者不矛盾。每次後續授權啟動的 `--authorize-gateway` 包含必要登入與唯讀 REST，當次比對完成才可 connect；若不願授權讀取就不能啟動。此路徑沒有 Guild／Global sync 或 `copy_global_to`，不刪除遠端指令；Guild 已有五指令，不重跑 Phase 2A.2 sync。

遠端比對後仍可能被其他操作者修改，沒有原子版本鎖；請停止其他部署／sync 操作，發現 signature mismatch 就停機回報，不能自動修正。listener 仍依每筆 Interaction 的 Guild／Application guard 及本地 SDK 參數契約執行。

## Offline Mock 結果

準備階段的歷史主回歸：**296 Passed、0 Failed、0 Skipped、34 Deselected**。另補跑 2 項既有純文字 CHECK normalization：**2 Passed、0 Failed、0 Skipped**。合計 **298 個不同離線案例通過**，包含當時既有 240 項及新增 58 項；本次文件收尾沒有重跑。34 項排除為 32 項真實 MySQL 案例及另外補跑的 2 項純文字案例，不算 skipped 或 passed。

新測試使用真實本地 JukeBoxClient／CommandTree 與 SDK `_call` dispatch，而非只直接呼叫 service；涵蓋 admission 先於 callback、五指令與 valid／invalid URL／on／off 的私密回覆、無假餘額／空歷史／成功保存、回覆失敗不重跑、契約欄位 drift、身分／Guild／Endpoint 不符、遠端讀取錯誤、登入／API／斷線錯誤、Ctrl+C／coroutine cancellation 的 close、無 sync／DM／Daily、正常關閉、Token 不進輸出。所有外部 HTTP／login／Gateway／DM 都 mock，正式 repository constructor 與 DB_*／TEST_DB_* 環境讀取有 fail-fast trap。

第一輪的 Interaction fixture 少了 SDK Namespace 需要的 `_state`／guild 欄位，9 項 mock 測試失敗；補齊真實 SDK 所需狀態後重跑通過，沒有放寬 assertion、跳過或刪除測試。舊 import 副作用檢查涵蓋全部新模組。函式長度 ≤25、控制巢狀 ≤3、labeled docstrings 靜態檢查通過。

Windows asyncio 依既有環境需在經自動審查准許的沙箱外建立本機 socket pair；仍沒有平台連線。既有 discord.py audioop 棄用 warning 不影響 Python 3.12，未安裝／升級套件。共享 CLI 邊界亦處理 discord.py 已有 runtime 依賴 aiohttp 的 ClientError／ServerDisconnectedError；沒有新增或修改依賴清單。

```powershell
$env:JUKEBOX_RUN_MYSQL_TEST = '0'
$env:JUKEBOX_RUN_REPOSITORY_MYSQL = '0'
$env:JUKEBOX_RUN_MYSQL_READONLY = '0'
.\.venv\Scripts\python.exe -m pytest -m 'not mysql and not mysql_readonly' -q --tb=short
.\.venv\Scripts\python.exe -m pytest tests/test_mysql_readonly.py::test_check_rendering_normalization -q --tb=short
```

## PowerShell 安全流程

以下只準備未來人工步驟，本次未使用真實 Token。不要用 `python -m bot` 或 Phase 2A.2 sync 代替 listener。先在專案根目錄定義 function；定義本身不登入，不需要 DB_*／TEST_DB_*，也不修改 `.env`：

```powershell
function Invoke-JukeBoxGatewayAcceptance {
    param([switch]$AuthorizeGateway)

    $gatewayToken = $null
    try {
        Set-Location -LiteralPath 'D:\Project\MusicBased\discordBot_JukeBox101' -ErrorAction Stop
        $env:DISCORD_DEV_GUILD_ID = Read-Host '輸入已核對的 Development Guild ID'
        $env:DISCORD_EXPECTED_APPLICATION_ID = Read-Host '輸入已核對的 Application ID'
        $gatewayToken = Read-Host '本機輸入 Bot Token（不貼聊天）' -AsSecureString
        $gatewayPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($gatewayToken)
        try {
            $env:DISCORD_BOT_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($gatewayPointer)
        }
        finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($gatewayPointer)
        }

        $gatewayArguments = @('-m', 'bot.gateway_acceptance', 'preflight')
        if ($AuthorizeGateway) {
            $gatewayArguments = @('-m', 'bot.gateway_acceptance', 'listen', '--authorize-gateway')
        }
        & .\.venv\Scripts\python.exe @gatewayArguments
        if ($LASTEXITCODE -ne 0) {
            throw '驗收入口已停止或中斷；核對訊息及 Client 結束狀態，不自動重試。'
        }
    }
    finally {
        Remove-Item Env:DISCORD_BOT_TOKEN -ErrorAction SilentlyContinue
        Remove-Item Env:DISCORD_DEV_GUILD_ID -ErrorAction SilentlyContinue
        Remove-Item Env:DISCORD_EXPECTED_APPLICATION_ID -ErrorAction SilentlyContinue
        if ($null -ne $gatewayToken) { $gatewayToken.Dispose() }
    }
}
```

先執行完全離線的 preflight：

```powershell
Invoke-JukeBoxGatewayAcceptance
```

輸出 parameter_contract、本次手動提供的公開 IDs、database=disabled、automatic_sync=false、gateway_connected=false、remote_contract=not_checked_offline。Token 存在／基本格式通過不代表登入有效。此 function 最後移除三個 DISCORD_* 變數，不改 DB_*／TEST_DB_*；Token 不放命令列或 shell history，但執行所需記憶體／process environment 中仍會短暫存在明文，不能宣稱零殘留。

**只有使用者另行授權 Gateway 真人驗收後**，才執行：

```powershell
Invoke-JukeBoxGatewayAcceptance -AuthorizeGateway
```

等候 `Development / DB-disabled Gateway ready` 後，才操作已發布五組指令。也可在已安全設定三個 DISCORD_* 的 process environment 中使用原生入口：

```powershell
# 完全離線：
.\.venv\Scripts\python.exe -m bot.gateway_acceptance preflight
# 僅在另行授權後：
.\.venv\Scripts\python.exe -m bot.gateway_acceptance listen --authorize-gateway
```

不得把真實 Token 貼到聊天、命令列、截圖或回報。此階段沒有 sync 選項；不需要重新發布指令、不變更 intents／Administrator 權限。

## 真人驗收表與證據粒度

使用者已確認五組互動整體完成，記為 User Verified；沒有提供其所有選項／輸入組合或逐字回覆，不把下列每個預期文字、私密可見性與異常情境標記成獨立 Passed。

| 人工動作 | 預期結果 | 現有真人證據 |
|---|---|---|
| 核對啟動摘要與 Ready | Application／Guild 符合人工預期；遠端契約 matched | 身分／Guild／Gateway Ready Passed、contract Matched（使用者回報） |
| `/share` 有效／重複／非法 URL | 格式驗證、未儲存未發點、不回顯惡意輸入 | 指令互動整體 User Verified；各輸入分支、逐字內容未獨立對照 |
| `/exchange` | 未啟用、未扣點、未建推薦 | 指令互動整體 User Verified；逐字內容未獨立對照 |
| `/balance` | 資料庫停用，無假的 0 | 指令互動整體 User Verified；逐字內容未獨立對照 |
| `/history` | 資料庫停用，無假的空歷史 | 指令互動整體 User Verified；逐字內容未獨立對照 |
| `/daily on/off`、重複設定 | 明確未保存、不排程／不 DM | 指令互動整體 User Verified；各選項／重複情境未逐項獨立對照 |
| 第二個測試帳號觀察 | 看不到別人的 ephemeral 回覆；無公開 fallback／mentions | 未提供真人證據，不宣稱 Passed |
| DM／其他 Guild | admission 拒絕，不執行 service | 有離線 mock 證據，未提供逐項真人證據 |
| API 失敗／斷線／取消／Ctrl+C | 不重跑 service、close、安全訊息與環境清理 | 有離線 mock 證據，未提供逐項真人證據 |

若 DM／其他 Guild 沒有可觸發指令，記錄為真人未觸發，不宣稱該真人案例通過；離線真實 SDK dispatch 的拒絕測試已覆蓋此邊界。不要為製造事件修改遠端指令、授予額外權限或開始正式資料庫業務。

## 異常與剩餘風險

`async with client` 管理 preflight、正常返回、登入／API／contract 失敗及取消的 close。CLI 使用共享安全邊界，平台 logs 在其執行期間停用，原始 Token／exception body 不輸出；因此 SDK 的詳細斷線診斷不在此工具顯示。互動回覆失敗可能沒有可送出的安全訊息，使用者可能只看到 Discord 的錯誤提示；不公開補送、不重試 service。停止並回報類別與可公開 IDs，不附憑證或私密 URL。

`reconnect=False` 關閉一般錯誤的自動重連；SDK 仍可處理其 Gateway protocol 要求的 resume／reconnect，不能把它描述為對所有內部協定重連的全面禁止。斷線／正常 remote close 會結束 listener，不代表人工驗收 Passed。外部 endpoint 設定、權限不足、契約不一致時應停止，沒有自動修復。

Ctrl+C 的一般 cancellation 已離線驗證；第二次強制中斷、終端關閉、OS kill 仍不能保證 Python finally 全部執行。必要時在終端手動移除 DISCORD_*，確認程序已結束。本工具不做任何資料持久化，因此沒有測試資料 seed／清理或「資料庫自動還原」承諾；完整歌曲投稿、點數、歷史、Daily 持久化／排程／DM 仍未真人驗證。

Git 維持既有全案 `??` 未追蹤：`.env.example`、`.gitignore`、`AGENTS.md`、`README.md`、`ROADMAP.md`、`bot/`、`dependency-versions.txt`、`docs/`、`pyproject.toml`、`tests/`。沒有 Commit／Push／Merge／Tag。完成本地準備後停止，等待人工授權與回報，不開始 Phase 2B。
