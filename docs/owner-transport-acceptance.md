# Owner 審核 Discord transport 驗收（TK-14，A 類）

**狀態**：離線準備完成（2026-10-23，本輪）。**實機尚未執行，也未獲授權。** 離線通過不代表實機驗收通過，更不代表正式功能已啟用。

## 驗收層級（請勿混用）

| 層級 | 內容 | 目前狀態 |
|---|---|---|
| 離線通過 | 本地測試：快照比對、指令樹、合成服務、遠端契約比對（假資料） | 通過（見下方測試命令） |
| A 類：合成服務的 Discord transport | 私人 Guild 中的 `/owner` 指令、Guild／Application 准入、Owner 閘門、按鈕與分頁 | **未執行**，需 PB-ACC 授權 |
| B 類：MySQL 持久化 Owner 審核 | 稽核列、真實資料庫的快照比對、重啟、Recovery／Batch B 前提 | **未開始**，前提見末節 |
| 正式功能啟用 | `bot/__main__.py` 註冊 Owner 群組、正式投稿與審核 | **未接線，不在本文件範圍** |

A 類通過時，只能宣稱「合成資料的 transport 互動通過」，不得宣稱 Owner 審核已可用於真實案件。

## 範圍

- 資料來源：一筆合成案件（視訊 ID `Synth0001X1`，操作 ID `op-1`），只存在於執行程序的記憶體中；**不連接 MySQL，不寫入任何真實資料，確認後不發點**。
- 驗證內容：私人 Guild 中的 `/owner` 群組；Guild 與 Application 准入（`GuildInteractionGuard`，經指令樹 `interaction_check`）；Owner 閘門（Guild 與使用者 ID）；延遲回覆與 ephemeral；預覽→確認按鈕的狀態機。
- 不包含：持久化、重啟後權杖失效、Recovery、Batch B、Ledger、發點、交換與每日派送。

## 操作前需要提供的資訊（F 欄位）

| 欄位 | 內容 | 如何提供 | 能否貼入對話 |
|---|---|---|---|
| F1 | 目標私人 Guild ID 與 Application ID（數字） | 操作者以環境變數 `DISCORD_DEV_GUILD_ID`、`DISCORD_EXPECTED_APPLICATION_ID` 提供 | 可以（非秘密），但仍建議只放在本機環境 |
| F2 | Owner 的 Discord 使用者 ID | 操作者以環境變數 `DISCORD_OWNER_USER_ID` 提供（個人識別，不寫入文件或 repo） | 不建議 |
| F3 | Bot Token | 操作者以環境變數 `DISCORD_BOT_TOKEN` 提供 | **絕對不可**貼入對話、文件或 repo |
| F4 | 目標 Guild 目前是否已有本 Application 的指令 | 由操作者在 Discord 開發者後台或 Discord 客戶端目視確認；本流程不會替你查詢 | 可以描述結果，例如「無指令」 |
| F5 | 執行人員與時段 | 操作者說明 | 可以 |

缺少 F1 至 F4 任一項時，請勿執行 sync 或 listen 模式。

## 指令

所有指令都在專案根目錄執行。預檢不需要任何網路授權。

| 模式 | 指令 | 授權開關 | 網路與寫入 |
|---|---|---|---|
| 盤點（唯讀） | `.\.venv\Scripts\python.exe -X utf8 -m tests.owner_transport_acceptance inspect --authorize-login` | 只能有 `--authorize-login` | 登入並驗證身分；分別列出**全域**與**目標 Guild**的指令名稱。不同步、不刪除、不連線 Gateway |
| 預檢 | `./.venv/Scripts/python.exe -X utf8 -m tests.owner_transport_acceptance preflight` | 不接受任何授權開關 | 無網路，不讀取遠端，不輸出 Token |
| 登入並同步 Owner 群組 | `... -m tests.owner_transport_acceptance sync --authorize-login --authorize-sync` | 必須同時有 `--authorize-login` 與 `--authorize-sync`，且只能有這兩個 | 登入、讀取 Application 與 Guild 身分、讀取本 Application 在 Guild 的指令、寫入 Guild 指令 |
| 監聽 | `... -m tests.owner_transport_acceptance listen --authorize-gateway` | 只能有 `--authorize-gateway`（登入含在內） | 登入、讀取遠端 Owner 群組契約、連線 Gateway |

**授權開關規則**：每個模式只接受它自己的開關集合，多出或缺少任一開關都會在任何網路呼叫前拒絕（`tests/owner_transport_acceptance.py` 的 `MODE_SWITCHES`）。`sync` 的 `--authorize-login` 不會代替 `listen` 的 `--authorize-gateway`，反之亦然。

## 同步語意

- Discord 的 guild 指令同步是**整批覆寫**：發布的集合會取代「本 Application 在該 Guild 的現有指令」。
- 同步前的空 Guild 檢查與確認後的再次檢查，只能**縮短競爭窗口**，不能保證檢查之後到發布之前沒有其他人改動。實機期間請勿讓其他程序同時同步同一個 Guild。
- 指令由 Application 隔離：`fetch_commands(guild=...)` 與 `tree.sync(guild=...)` 只涉及本 Application 的指令，其他 Application 的指令不在範圍內（已由 discord.py 原始碼 `_get_all_commands` 與 REST 語意確認；實機是否如此需 A 類驗證）。
- 因此執行前要求「本 Application 在目標 Guild 無指令」，任何既有指令都會停止流程，不覆寫。
- **撤回**：先前版本曾判斷「看到五個指令即 Guild 非空」。這個判斷未經確認：那五個指令可能是 Guild 專用，也可能是全域指令。實際集合必須先以 `inspect` 唯讀盤點，再決定是否能直接同步。
- 同步只發布 `owner` 群組，不複製全域的五個指令，也不改動既有五指令契約。
- **目前沒有自動回復（rollback）**。若同步後需要撤回，請在 Discord 後台移除該 Guild 指令，或另行取得授權後執行新的操作。

## PB-ACC 授權包（依 `docs/recovery-batchb-private-beta-spec.md` §17.5）

每一項都需由 Owner 逐項批准；本文件本身不構成授權。

- **ticket**：TK-14（Owner 審核 transport）。
- **目標環境**：一個私人 Discord Guild 與一個 Application（F1）。不得是正式環境或 Beta DB。
- **帳號角色**：Owner 的 Discord 帳號（一般使用者，F2）。Bot Token（F3）只由操作者環境提供；**不需要任何資料庫帳號**。
- **操作範圍**：
  1. `preflight`：無網路，可隨時執行。
  2. `sync`：登入一次、驗證身分、確認 Guild 無指令（F4）、輸入確認字串 `SYNC <application_id> <guild_id>`（例如 `SYNC 5005 2002`，數字以 F1 為準）後，再次驗證身分與 Guild 仍為空，才發布 `owner` 群組。
  3. `listen`：當次 Gateway 監聽，供 Owner 在該 Guild 操作。
- **風險**：Guild 會出現 `/owner` 指令；sync 會寫入遠端指令；listen 期間 Bot 在線，可接受該 Guild 的 Owner 互動。合成案件不影響真實資料。
- **停止條件**：任一身分不符、Guild 非空（以 `inspect` 盤點的 Guild 指令為準）、確認字串不符、遠端契約不符、Gateway 或登入異常，立即停止，不自動重試 sync。
- **回復方式**：見上方「同步語意」。
- **輸出範圍**：摘要 JSON。預檢與同步包含 Application ID、Guild ID、指令名稱（本機或已發布）、契約狀態與合成資料聲明。不輸出 Token、Owner ID 或使用者內容。

## A 類實機檢查清單

執行後逐項標記 Passed、Failed 或 Manual Pending。未實際執行的項目不得標為 Passed。

| 編號 | 操作 | 預期 |
|---|---|---|
| A-1 | Owner 在私人 Guild 執行 `/owner list` | 只看到 ephemeral 回覆，列出 `op-1` |
| A-2 | 同一 Guild 的另一位成員執行 `/owner list` | 拒絕，不列出任何案件 |
| A-3 | Owner 在 DM 中嘗試 `/owner list` | 無法執行或被拒絕 |
| A-4 | Owner 執行 `/owner show operation:op-1` | 顯示合成案件的狀態與證據引用 |
| A-5 | Owner 預覽核准，按確認兩次 | 只執行一次；第二次回覆原結果 |
| A-6 | 預覽後等待超過 5 分鐘再按確認 | **不預期 Bot 回覆**：discord.py 的 View 逾時（300 秒）會停止該 View，之後的按鈕互動不被分派，Discord 客戶端通常顯示「互動失敗」。此時沒有寫入。觀察到的實際畫面請記錄於結果欄；需要重新預覽時，重新執行 `/owner` 指令。（服務層的權杖過期檢查仍存在，但與 View 逾時幾乎同時，實機不易觸發。） |
| A-7 | **不列為實機項目**。預覽回覆是 ephemeral，其他成員看不到該按鈕，無法在實機合法取得。跨使用者按鈕拒絕由離線測試覆蓋；實機以 A-2 驗證非 Owner 的指令拒絕。 | 不適用（見左欄） |
| A-8 | 回覆是否包含 @ 提及 | 無提及 |
| A-9 | 取消按鈕 | 取消預覽，不寫入 |

**未涵蓋**：「預覽後由其他路徑改變案件」的 S21 情境，合成入口沒有其他寫入路徑可觸發；此情境由離線測試覆蓋，不屬於 A 類實機項目。

## 框架行為的確認（不是待辦）

- **指令互動**經過指令樹的 `interaction_check`（`bot/discord_app.py` 的 `SafeCommandTree`，discord.py `app_commands/tree.py:1247-1259`），因此 `GuildInteractionGuard` 同時檢查 Guild 與 Application ID。
- **按鈕互動**不經過指令樹；discord.py 依 view store 分派（`discord/state.py:823`，`ui/view.py:1054`），由 `OwnerGate` 檢查 Guild 與使用者。Application ID 不在按鈕的檢查範圍。framework 只把本程序註冊過的 view 所發出的互動分派給它，因此沒有發現可重現的跨應用准入缺口；此結論來自原始碼閱讀，仍以 A-7 實機驗證。

## 已知限制

- **遠端契約沒有審核過的基準**：`bot/development_command_contract.json` 只有五個全域指令，不含 `owner`。listen 的比對是「本機程式碼產生的契約」對「遠端回傳」，本機 Owner 群組的漂移不會被獨立審核。

- **B 類未涵蓋**：稽核列、重啟後權杖失效與真實資料庫的快照比對，需要 Q1 順序下的 Recovery（TK-05、TK-06）、Batch B（TK-07）、測試庫遷移授權與 Control 開關授權。見 `docs/recovery-batchb-private-beta-spec.md` TK-14 區塊。
- **生產入口未接線**：`bot/__main__.py` 不註冊 Owner 群組。
- **repository 層的快照可選（殘留風險）**：內部呼叫 repository 的 `review`、`reexamine`、`confirm_version` 時，快照仍可省略。Discord 的 Owner 路徑只能經由 `OwnerReviewService`，其 `_snapshot_for` 會 fail closed，因此產品入口無法繞過 S21。B 類生產化前，建議把 repository 的快照改為必填，並同步修改約 28 處持久化測試的構造點。
- **S4 已由 Owner 裁決（2026-10-23）**：版本為 CONFLICT 時允許查看與拒絕，禁止核准；拒絕不清除衝突、不發點；本次不開放解除衝突。2026-10-23 之前的人工紀錄中，「衝突後核准」未測；「重審後核准阻擋」的結果已受新規則影響，見下方追加說明。
- **離線測試不等於實機**：合成服務與 fake 通過只代表本地邏輯與 discord.py dispatch 路徑；Discord 實際行為需 A 類驗證。

## 離線測試命令

```
./.venv/Scripts/python.exe -m pytest tests/test_decision_snapshot.py tests/test_owner_transport_acceptance.py -q
./.venv/Scripts/python.exe -m pytest -q
```

完整離線回歸的實際數字見本輪最終回報；本文件不記錄歷史數字。

## 既有 Guild 的更新（`update-guild`，已由操作者於 2026-10-23 執行一次）

目標私人測試伺服器已存在 `balance`、`daily`、`exchange`、`history`、`share` 五個指令（已由 `inspect` 唯讀確認；全域指令為空）。空 Guild 的 `sync` 模式會拒絕覆寫，因此另設一個獨立模式。

### 完整契約（不只比名稱）

契約包含：名稱、類型、描述、預設權限、`dm_permission`、`nsfw`、`contexts`、`integration_types`，以及每個參數的描述、必填、選項、允許值（choices）、型別、長度與數值限制、子選項。Discord 省略的欄位以預設值補齊，省略與預設視為相同。實作：`tests/owner_transport_contract.py`。

- `inspect`：分別比對遠端的五個指令與本機的五個指令；差異會列出欄位路徑，例如 `share.options[0].description`。
- `update-guild` 與 `listen` 使用**同一份**六指令的完整契約（五個全域指令加上 `owner`）。

### 授權與流程

指令：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m tests.owner_transport_acceptance update-guild --authorize-login --authorize-guild-update
```

依序執行，任一步失敗即停止：

1. 登入並驗證 Application 與 Guild 身分。
2. 讀取目標 Guild 中本 Application 的指令，取得完整契約。
3. 若已與審查後的六指令完全相同：回報 `already_matches`，不寫入。
4. 若含有五個以外的指令、已有 `owner` 但不同、或五個指令與本機契約有任何差異：停止，不寫入。
5. 顯示預計集合（五個既有指令加上 `owner`），要求輸入 `UPDATE <application_id> <guild_id>`。
6. 輸入後再次驗證身分，並再次讀取遠端契約，必須與第 2 步相同，否則停止。
7. 把本機五個全域指令複製到該 Guild，發布六指令。
8. 發布後再讀取完整遠端契約，必須與審查後的六指令完全相同；不符或結果未知即停止，**不自動重試**。

### 授權範圍與影響

- 寫入：只在該 Guild 中發布本 Application 的六個指令。既有五個指令以**本機的完整定義**重新發布，其參數、權限與描述必須與遠端先前完全一致，否則第 4 步已停止。
- 不影響：其他 Application 的指令、全域指令、資料庫與任何案件資料。
- 不發點、不接正式入口、不連資料庫。

### 尚未確認與競爭窗口

- 第 6 步的再次檢查只能**縮短**競爭窗口。在第 6 步與第 7 步之間，若有其他程序改動同一個 Guild，程式無法偵測。**實機期間請勿讓其他程序同時同步或修改此 Guild。**
- 「其他 Application 的指令不會被覆寫」依 Discord API 語意判斷，實機未驗證。
- 撤回：目前沒有自動回復。若第 8 步結果不符，需人工檢查 Guild；**不得**依賴 Discord 後台的單一指令刪除功能，因為尚未確認該功能存在。

### 同步與監聽的契約

`listen` 現在要求遠端 Guild 的指令**恰好**等於審查後的六指令完整契約；只有五個指令時會在連線前拒絕。

### 已審閱的預期變更（僅此一項）

`inspect` 在目標 Guild 中只發現一個差異：

| 指令 | 欄位 | 遠端（舊值） | 本機（新值） |
|---|---|---|---|
| `history` | `description` | `查詢最近成功取得的推薦` | `查詢本人投稿及目前審核狀態` |

- 程式只接受這個精確的欄位值（`tests/owner_transport_contract.py` 的 `APPROVED_REMOTE_CHANGES`）。遠端值必須是舊值或已是新值，其他任何值都會停止。
- 這個豁免不擴及參數、其他指令或其他欄位。`history` 若另有任何差異，或 `share` 等其他指令有差異，仍會拒絕。
- 提示畫面會顯示舊值→新值，供你確認後才輸入 `UPDATE` 確認字串。

### 完整更新差異（預計內容；實際結果見「人工測試結果」）

| 項目 | 目前遠端 | 更新後 |
|---|---|---|
| `balance` | 已存在，與本機一致 | 不變（以本機完整定義重新發布，內容相同） |
| `daily` | 已存在，與本機一致 | 不變 |
| `exchange` | 已存在，與本機一致 | 不變 |
| `history` | 描述為舊值 | 描述改為 `查詢本人投稿及目前審核狀態`；其餘欄位不變 |
| `share` | 已存在，與本機一致 | 不變 |
| `owner` | 不存在 | 新增，6 個子指令（list、show、approve、reject、confirm_version、reexamine）及其參數 |

全域指令仍為空，不受影響。

## 人工測試結果（2026-10-23，A 類合成 transport）

**範圍**：Windows PowerShell 執行 `tests.owner_transport_acceptance`；Application ID `<APPLICATION_ID>`、Guild ID `<GUILD_ID>`；合成記憶體案件 `op-1`；不連 MySQL，不發點。Owner 使用者 ID、夥伴的暱稱或頭像、Token 均未記錄於此。

**輪次說明**：每次重新啟動合成入口，案件都會回到初始狀態。不同輪次的結果彼此獨立，不可串成同一份持久化紀錄。

| 項目 | 結果 | 狀態 | 說明 |
|---|---|---|---|
| `update-guild` 同步 | 回傳 `published_matches_expected`，六指令為 balance、daily、exchange、history、owner、share | Passed | 已依審閱的 `history.description` 預期變更執行一次；發布後完整契約核對通過 |
| A-1 `/owner list` | 顯示 `op-1`：待審核、版本未確認、修訂 1、證據 0 筆；畫面顯示只有本人可見 | Passed | 只有 Owner 本人操作 |
| A-2 非 Owner 指令拒絕 | 夥伴在測試 Guild 執行 `/owner list`，回覆「此指令僅限指定私人伺服器中的 Owner 使用。」，沒有顯示案件，畫面只有該使用者可見 | Passed | **只證明此帳號在此 Guild 的 list 操作被拒絕**，不擴大宣稱所有操作或其他 Guild 都已驗證 |
| A-4 `/owner show` | 正確顯示合成案件的來源、狀態與歷程 | Passed | |
| A-5 核准路徑 | 重新啟動後 op-1 為待審核、修訂 1、歷程 2 筆。以 `/owner approve` 送出，citation `synthetic-approval-test`，理由「合成假案件核准測試，非真實歌曲查核」。預覽顯示待審核→已核准，版本仍未確認，不發點。確認後 show 顯示已核准、修訂 2、歷程 3 筆、內容判定 1 筆；判定理由與引用正確 | Passed（核准路徑） | 版本仍未確認，版本證據 0 筆。**不代表真實歌曲資格或版本證據已驗證，也不能僅憑畫面宣稱 Ledger 已驗證未寫入** |
| A-5 變體（拒絕＋雙按確認） | 拒絕預覽確認後，已拒絕、修訂 2、歷程 3 筆，理由「假案件拒絕確認測試」；再按同一確認按鈕回傳相同結果；再次 show 仍為修訂 2、歷程 3 筆 | Passed（拒絕變體） | 重複確認沒有重複執行 |
| A-9 取消 | 拒絕預覽後按取消：仍為待審核、修訂 1、歷程 2 筆，沒有新增決定 | Passed | 取消路徑 |
| A-6 預覽逾時 | 重新啟動後對 op-1 建立拒絕預覽，citation `1`，理由「逾時測試」。到期時間 2026-10-23T13:12:32+00:00（Asia/Taipei 21:12:32）。到期後按確認，Discord 顯示「JukeBox101 未及時回應」。21:14 的 show 仍為待審核、修訂 1、歷程 2 筆、內容判定 0 筆、證據 0 筆 | Passed（UI／transport 層） | 過期按鈕未造成狀態變更。**不能宣稱直接測到服務層的權杖過期檢查**，因為 View 可能已停止分派，服務層檢查未被觸發 |
| A-3 DM 指令可用性 | 在 Bot 私訊中，`/owner` 不出現在指令選單 | 符合預期（僅 UI） | 只記為「DM 無法選用」；**後端拒絕路徑未實測** |
| 證據引用 `1` | 輸入後案件證據仍為 0 筆 | 未驗證 | `evidence_reference` 是決定的引用文字，寫入稽核理由，不是證據記錄；稽核理由是否含該引用未查證 |
| A-8 @ 提及 | — | 未執行 | 專門測試尚未做 |
| 版本確認、重審 | — | 未執行 | |
| 實際多頁分頁 | — | 未執行 | |
| 其他 Guild 的互動拒絕 | — | 未執行 | Application ID 的互動准入不能只憑本次正常操作宣稱完整通過 |
| 監聽程序狀態 | — | **未確認** | Bot 是否仍在線未確認，不得記為已停止。若原 PowerShell 視窗仍在執行，請於該視窗按 Ctrl+C，不要另外啟動程序 |

**本次不宣稱**：TK-14 全部完成；B 類 MySQL 持久化通過；Ledger 驗證；正式功能啟用；真實歌曲資格或版本證據驗證；其他 Guild 的拒絕；@ 提及防護通過。

**離線測試數字**：前述 1965 passed、55 skipped 為歷史回報，本輪沒有重跑。

## 更正：遠端契約盲點（2026-10-23，離線修正）

先前的完整契約比對**沒有讀取遠端指令的頂層欄位**。discord.py 的遠端 `AppCommand.to_dict()` 不含 `default_member_permissions`、`dm_permission`、`nsfw`，但這三個值實際保存在物件屬性上。因此先前的 `inspect` 與 `update-guild` 對這三個欄位是盲目的，無法發現它們的差異。

- 修正：遠端契約改由物件屬性補上這三個欄位（`tests/owner_transport_contract.py` 的 `remote_payload`），並有測試證明 `default_member_permissions` 的遠端差異會被偵測。
- 影響：先前的 `update-guild` 以本機五個全域指令的完整定義重新發布。若遠端的這三個欄位原本與本機不同，已在當時被覆寫，現在無法還原原值。目前的 `inspect` 可以讀出它們的現況，但無法回溯先前的值。
- 已確認的現況（以 `update-guild` 結果為準）：六指令已發布，`owner` 的預設權限為**未限制**，因此一般成員的指令選單可能看得到 `/owner`。這正是下一節要修正的項目。
  - **更正（2026-10-24，操作者回報，保留當時事實）**：上一行的「未限制」是 `owner` 權限變更（commit `8346787`）之前、`update-guild` 之後的遠端狀態描述，當時尚未重新發布新權限。之後操作者已再次執行 `update-guild`，回傳 `published_matches_expected`，並確認 Owner 可執行 `list`、非管理員夥伴只看到五個基本指令。因此遠端 `owner` 權限已更新為本機的 0。本文件不以過時描述推定目前遠端狀態；日後若真實契約檢查不符，才依該次輸出處理。
  - 本次更正未在本機或 Discord 重新核對，僅是操作者回報的結果。

## 指令選單可見性（owner 群組）

**產品方向**：一般成員不應在指令選單看到 `/owner`。後端仍以設定的 Owner 使用者 ID 驗證身分，**不因為選單隱藏而移除檢查**。

**程式變更**（僅 owner 群組，`bot/discord_owner_review.py` 的 `OwnerReviewGroup`）：`default_permissions=discord.Permissions.none()`，本機序列化為 `default_member_permissions = 0`。其他五個指令不變（本機預設權限仍為未限制）。

**審閱的遠端變更**（僅此一項，見 `APPROVED_REMOTE_CHANGES`）：

| 指令 | 欄位 | 遠端（舊值） | 本機（新值） |
|---|---|---|---|
| `owner` | `default_member_permissions` | 未限制（`None`） | `0` |

同一次 `update-guild` 也會套用前述 `history.description` 的審閱變更。其他任何 owner 差異（描述、參數、子指令）仍會拒絕，不會放行整個群組。

**Discord 平台的限制（未在線上核對最新說明頁，以下依 discord.py 原始碼與 Discord 既有行為判斷，需以實機確認）**：
- `default_member_permissions = 0` 的效果是：沒有任何權限的一般成員看不到該指令；**具有管理員權限的成員仍看得到並可使用**。
- 不要求 Bot 取得 Administrator 權限，也不增加 OAuth scope 或邀請權限。
- **Owner 若不是管理員**：需要伺服器管理員在伺服器設定的「整合」中，為該應用程式的 `/owner` 指令設定例外，允許 Owner 的使用者或角色。這是 Discord 伺服器設定，不是本程式可以完成的。
- **不承諾「除了 Owner，任何人都絕對看不到」**。其他管理員即使看得到，也必須被後端拒絕；這是 A 類實機要驗證的項目。

## 人工測試安排（待你確認後才執行）

以下順序都在同一個 Windows PowerShell 視窗執行，每一步結束後回報結果。

1. **唯讀盤點**（登入一次，不寫入）：
   ```powershell
   .\.venv\Scripts\python.exe -X utf8 -m tests.owner_transport_acceptance inspect --authorize-login
   ```
   預期：`guild_unreviewed_differences` 為空；`guild_owner_present` 為 `true`；若 owner 的 `default_member_permissions` 仍為未限制，會列為已審閱的變更。
2. **更新**（寫入 Guild，需要你確認字串 `UPDATE <application_id> <guild_id>`）：
   ```powershell
   .\.venv\Scripts\python.exe -X utf8 -m tests.owner_transport_acceptance update-guild --authorize-login --authorize-guild-update
   ```
   預期：`published_matches_expected`，六指令完整契約一致。
3. **監聽**（連線 Gateway，之後由你在原視窗按 Ctrl+C 停止）：
   ```powershell
   .\.venv\Scripts\python.exe -X utf8 -m tests.owner_transport_acceptance listen --authorize-gateway
   ```
   預期：遠端六指令完整契約與審閱內容一致才連線；否則在連線前停止。

**選單可見性檢查**（需要三種帳號，都必須是真實的測試帳號，不使用 Bot 帳號）：
- 一般成員（非管理員）：指令選單中**不應**出現 `/owner`。
- Owner（非管理員）：若按上述「整合」設定未完成，同樣看不到；完成設定後應可見並可操作。
- 其他管理員（若有）：可能看得到，但執行 `/owner list` 必須被後端拒絕，並回覆僅限 Owner 使用的訊息。

## 版本確認與重審的人工操作順序（假資料）

**前提**：重審只能在案件為**已核准或已拒絕**時執行。直接對初始待審案件重審會被拒絕。因此順序必須是：先核准或拒絕，再重審。版本確認沒有這個限制，但會在已核准案件上較容易觀察。

所有假資料的理由都以「合成假案件」開頭，來源以 `synthetic-` 開頭，以便與真實資料區分。來源文字至少 8 個字元，且不得是純數字或 `discord:`、`interaction:` 開頭。

1. 重新啟動合成入口（案件回到初始狀態，每次啟動都是獨立的測試輪次）。
2. `/owner approve operation:op-1 citation:synthetic-approval-test reason:合成假案件核准測試` → 確認。
3. `/owner confirm_version operation:op-1 reason:合成假案件版本確認 version_name:synthetic-orig-A version_kind:basic source:synthetic-version-source-1` → 預覽 → 確認。
   預期：版本變為已確認，審核狀態仍為已核准，修訂不變。
4. `/owner reexamine operation:op-1 reason:合成假案件重審測試 version_name:synthetic-orig-B version_kind:basic source:synthetic-reexam-source-1` → 預覽 → 確認。
   預期：預覽顯示預計身分（與 `synthetic-orig-A` 衝突時為 CONFLICT）；確認後審核狀態變為待審核，修訂遞增。
5. 每一步之後執行 `/owner show operation:op-1`，核對狀態、修訂、歷程與證據筆數。

**限制**：版本類別的各種選項（LIVE、COVER 等）的人工檢查不在本輪範圍。證據記錄由 `source` 建立；`evidence_id` 只能引用既有證據。

## A-8 提及防護（待實測）

先前只觀察到普通回覆沒有提及，這不足以標為通過。專門測試方法：在 `reason` 中輸入 `@everyone 測試` 或 `<@你的使用者 ID>`（只在本機輸入，不記錄），確認 Bot 的回覆與 Owner 預覽都不會觸發提及通知。離線測試已驗證 `AllowedMentions.none()` 的設定，但實機行為仍需人工確認。

## 尚未驗證與受平台限制的項目

- 指令選單的實際可見性（一般成員、Owner、其他管理員）：需要實機確認，Discord 客戶端的快取可能延遲。
- Owner 非管理員時的「整合」權限設定：需要伺服器管理員操作，本程式無法完成。
- 分頁：目前合成入口只有一筆案件，需要超過 20 筆才能驗證，本輪未實作。
- 其他 Guild 拒絕、DM 後端拒絕、服務層權杖過期（View 逾時先發生）：未驗證。
- B 類 MySQL 持久化、Ledger、Recovery／Batch B、正式功能啟用：未驗證。

### 追加結果：選單可見性與 Owner 可操作（同日，update-guild 之後）

| 項目 | 結果 | 狀態 | 說明 |
|---|---|---|---|
| `owner` 預設權限更新（`update-guild`） | 回傳 `published_matches_expected` | Passed | 同一次發布含 `history.description` 審閱變更與 `owner.default_member_permissions` 未限制→0 |
| Owner 的 `/owner list`（重新啟動後） | 顯示 `op-1`：待審核、修訂 1、版本未確認、證據 0 筆 | Passed | 只有 Owner 本人操作 |
| 一般成員（非管理員）的指令選單 | 只看到五個基本指令，看不到 `/owner` | Passed | 兩個帳號（Owner 與夥伴）的可見性測試通過 |
| Owner 是否可見與操作 | 可見且可操作（Owner 本人是否為管理員、或以何種整合設定取得權限，操作者未回報） | Passed | 權限來源未確認，不得據此推定其他帳號的設定 |
| A-2 非 Owner 指令拒絕 | 先前實測通過（見上文） | Passed（保留） | |
| 非 Owner 管理員 | 未測試 | 未執行 | 需另一個具有管理員權限的非 Owner 帳號才能驗證「後端拒絕」 |

**本次不宣稱**：非 Owner 管理員的後端拒絕已通過；其他 Guild 的可見性；TK-14 整體完成。

### 版本確認與重審人工測試（同日，合成入口 op-1，重新啟動後的單一輪次）

輪次前提：合成入口重新啟動，op-1 回到初始待審核（修訂 1、版本未確認、證據 0 筆、內容判定 0 筆、歷程 2 筆）。以下結果只限這一輪，不與先前輪次串接。

| 步驟 | 操作（合成假資料標記） | 觀察結果 | 狀態 |
|---|---|---|---|
| 0 | `/owner show op-1`（初始） | 待審核、修訂 1、版本未確認、證據 0 筆、內容判定 0 筆、歷程 2 筆 | Passed |
| 1 | `/owner confirm_version`：`version_name=synthetic-orig-A`、`version_kind=basic`、`source=synthetic-version-source-A`，理由標明合成測試 | 預覽：待審核→待審核、版本未確認→已確認 `synthetic-orig-A`、修訂 1。確認後 show：待審核、修訂 1 不變；版本已確認；證據 1 筆（來源類型 OWNER_REVIEW，來源參考為合成字串）；歷程 2 筆不變 | Passed |
| 2 | `/owner approve`：`citation=synthetic-approval-test-2`，理由標明合成測試，非真實歌曲查核 | 預覽：待審核→已核准、版本維持已確認、修訂 1。確認後 show：已核准、修訂 2；內容判定 1 筆（判定：歌曲，影片 Synth0001X1，證據參考 `synthetic-approval-test-2`）；歷程 3 筆 | Passed（審核紀錄；不代表真實歌曲資格或版本證據已驗證，不代表點數發放） |
| 3 | `/owner reexamine`：`version_name=synthetic-orig-A`（與已確認版本相同）、`version_kind=basic`、`source=synthetic-reexam-source-A` | 預覽：已核准→待審核、版本維持已確認 `synthetic-orig-A`（同版本，沒有衝突）、修訂 2、不發點。確認後 show：待審核、修訂 3；版本仍為已確認 `synthetic-orig-A`；證據 2 筆（新增合成重審證據）；內容判定 1 筆；歷程 4 筆 | Passed（同版本重審） |

**未執行（本輪範圍外）**：
- 重審後嘗試核准，預期被阻擋（TK-15 排序事件尚未實作）。
- 重審後的拒絕（解除重審區塊）。
- 會造成 CONFLICT 的版本（例如 `synthetic-orig-B`）。須在另一個獨立輪次中測試，因為衝突會影響後續操作。
- 版本類別 LIVE、COVER 等其他類別。

**本輪不宣稱**：版本證據或真實歌曲資格已驗證；Ledger 未寫入已驗證；點數未發放已驗證（只觀察到預覽與確認文字）；持久化；TK-14 整體完成。

### 追加（同一輪，op-1 重審後的核准阻擋）

| 步驟 | 操作 | 觀察結果 | 狀態 |
|---|---|---|---|
| 5 | `/owner approve operation:op-1 citation:synthetic-approval-test-2 reason:合成假案件：重審後核准阻擋測試`（重審後，待審核、修訂 3） | 直接回覆拒絕，**未出現預覽、確認或取消按鈕**。回覆原文（英文，保留記錄）：`Reexamination pending: approval waits for the TK-15 ordering event` | Passed（阻擋行為）；顯示為英文原文，屬已知的顯示問題，未修改程式 |
| 6 | `/owner show operation:op-1`（核對） | 待審核、修訂 3、版本已確認 `synthetic-orig-A`、證據 2 筆、內容判定 1 筆、歷程 4 筆；被拒絕的核准沒有留下任何紀錄 | Passed（狀態未變） |

**說明**：阻擋發生在建立權杖之前（服務層的 `approve_review` 檢查 `reexamination_pending`）。這是 UI 與服務層都觀察到的結果；因此可以說「UI 層與 show 核對皆通過」，但**不宣稱服務層權杖檢查的獨立驗證**。TK-15 排序事件實作後，這個阻擋需要重新評估。

**本輪仍未執行**：重審後的拒絕（解除重審區塊）、CONFLICT 路徑、其他版本類別、持久化、Ledger、點數。

### 追加（同一輪，op-1 重審後拒絕）

| 步驟 | 操作 | 觀察結果 | 狀態 |
|---|---|---|---|
| 7 | `/owner reject operation:op-1 citation:3f8f248c6547457fbd5b9be3d8711488 reason:合成假案件：重審後拒絕測試`（預覽） | 預覽：待審核→已拒絕、版本維持已確認 `synthetic-orig-A`、修訂 3、不發 Nickel、不寫入 credit_ledger | Passed（預覽） |
| 8 | 確認後 `/owner show operation:op-1` | 已拒絕、修訂 4、版本仍為已確認 `synthetic-orig-A`、證據 2 筆、內容判定 1 筆、歷程 5 筆；新增一筆已拒絕，理由「合成假案件：重審後拒絕測試」，引用 `3f8f248c6547457fbd5b9be3d8711488` | Passed（審核狀態變更）|

**只記錄畫面上觀察到的審核狀態變更**。未驗證內部 `priority_released`、後續投稿解鎖、排序或發點；點數與 Ledger 也未驗證。

**下一輪（獨立 CONFLICT 測試，尚未執行）**：CONFLICT 會凍結身分，必須在獨立的重新啟動輪次中測試，不可接在本輪之後。最短步驟：
1. 重新啟動合成入口（記憶體清空，不重新同步）。`/owner show op-1` 確認初始狀態：待審核、修訂 1、版本未確認、證據 0 筆。
2. `/owner confirm_version operation:op-1 reason:合成假案件：衝突測試第一版 version_name:synthetic-orig-A version_kind:basic source:synthetic-conflict-source-A` → 預覽 → 確認。
3. `/owner confirm_version operation:op-1 reason:合成假案件：衝突測試第二版 version_name:synthetic-orig-B version_kind:basic source:synthetic-conflict-source-B` → 預覽應顯示「版本衝突：維持凍結，此次決定不會清除衝突」→ 確認。
4. `/owner show operation:op-1`：版本應為衝突，證據 2 筆，修訂不變（待審核，修訂 1）。
5. 之後只觀察（不預設結果）：嘗試核准的預覽是否顯示衝突提示，以及是否被阻擋。這一步的預期不定，用來比對 S4「衝突項目只能查看」的解讀，不作為通過或失敗判斷。

### 獨立 CONFLICT 輪次（同日，重新啟動後的單一輪次，合成假案件）

**輪次隔離**：為隔離測試而重新啟動合成入口（以 Ctrl+C 停止原 listen 程序，再以原指令啟動），記憶體假案件被清空，未重新同步。這是本輪的測試安排，不是系統技術上的唯一做法。

| 步驟 | 操作 | 觀察結果 | 狀態 |
|---|---|---|---|
| 0 | `/owner show op-1`（重啟後初始） | 待審核、修訂 1、版本未確認、證據 0 筆、內容判定 0 筆、歷程 2 筆 | Passed |
| 1 | `/owner confirm_version`：`version_name=synthetic-orig-A`、`version_kind=basic`、`source=synthetic-conflict-source-A`（預覽後確認） | 預覽：版本未確認→預計已確認 `synthetic-orig-A`，修訂 1，待審核→待審核。確認後 show：待審核、修訂 1、版本已確認 `synthetic-orig-A`、證據 1 筆 | Passed |
| 2 | `/owner confirm_version`：`version_name=synthetic-orig-B`、`version_kind=basic`、`source=synthetic-conflict-source-B`（預覽） | 預覽：版本目前已確認 `synthetic-orig-A` → 預計衝突；附「版本衝突：維持凍結，此次決定不會清除衝突」；修訂 1；待審核→待審核 | Passed（預覽） |
| 3 | 確認後 `/owner show op-1` | 狀態待審核、修訂 1、版本衝突、版本證據引用為第二版的證據、「衝突原因：版本衝突：引用證據 …」、證據 2 筆（兩版各一筆）、內容判定 0 筆、歷程 2 筆 | Passed（衝突形成與核對） |

**本輪只記錄畫面上觀察到的結果**：
- 衝突形成與 show 核對通過；審核狀態與修訂不變。
- 未驗證：衝突後的核准或拒絕行為（S4 解讀待 Owner 確認，本輪未測）、解除衝突（程式未開放）、Ledger、發點、資料庫凍結（持久化）。
- 不宣稱 TK-14 整體完成。


### 追加：Owner 裁決後的規則變更（2026-10-23，離線）

- **CONFLICT**：核准在任何寫入前被拒絕（預覽與持久層皆檢查）；拒絕仍可執行，衝突保留，不發點。解除衝突未開放。
- **重審與排序**：已核准但未發點的投稿進入重審時立即讓出原受理優先權。重審通過 = 對重審中案件的核准，與系統產生的新排序事件在同一操作完成，新事件排到當時隊尾；原受理序號與歷史保留。已發點的投稿不套用讓位。
- **對先前人工紀錄的影響**：步驟 5「重審後核准被阻擋」是舊規則的結果，**需以新規則重驗**：在重審中的核准現在會成為重審通過（預覽說明含「新排序事件」），不再被阻擋。先前的「阻擋」結果不可再引用為目前行為。
- **未驗證**：新排序事件在實機後續投稿中的實際位置、重審通過後的發點（仍需政策與結算開關）、以及 CONFLICT 後核准被拒絕時的實機畫面（英文原文）。

### 輪次 A／B 的重新驗證（TK-15 第一片缺陷處理之後）

- **輪次 A（CONFLICT 核准被拒絕、拒絕仍可執行、衝突保留）**：步驟與先前相同。核准應在預覽前被拒絕，英文原文預期為 `Version conflict forbids approval; rejection stays allowed and does not clear the conflict`。拒絕應可執行，衝突保留，不發點。這一輪是新規則的實機驗證，之前沒有被實測過。
- **輪次 B（重審後的核准為重審通過）**：先前「重審後核准被阻擋」的結果已受新規則影響，不得再引用。重新驗證時，核准的預覽應附「重審通過：確認時產生新排序事件，排到當時隊尾」，確認後狀態為已核准。畫面不顯示排序序號，本輪只能驗證預覽說明與狀態，無法證明相對隊尾順序；相對順序由離線測試負責。
- 兩輪都需要獨立重啟（清空記憶體假案件）。不需要重新同步：本輪未改變斜線指令契約。

### 人工重驗：輪次 A（CONFLICT 規則，2026-10-23，獨立重啟後的單一輪次）

| 步驟 | 操作 | 觀察結果 | 狀態 |
|---|---|---|---|
| 0 | `show op-1`（重啟後初始） | 待審核、修訂 1、版本未確認、證據 0 筆、內容判定 0 筆、歷程 2 筆 | Passed |
| 1 | `confirm_version` `synthetic-orig-A`（basic，來源 `synthetic-conflict-source-A`）預覽→確認 | 預覽：未確認→預計已確認；確認後 show：已確認、修訂 1、證據 1 筆 | Passed |
| 2 | `confirm_version` `synthetic-orig-B`（basic，來源 `synthetic-conflict-source-B`）預覽→確認 | 預覽：已確認 `synthetic-orig-A`→預計衝突，附「版本衝突：維持凍結，此次決定不會清除衝突」；確認後 show：版本衝突、衝突原因引用第二版證據、證據 2 筆、修訂 1、歷程 2 筆 | Passed |
| 3 | `approve` 核准（citation `synthetic-approval-test-2`） | 直接回覆拒絕，**無預覽、無按鈕**，英文原文：`Version conflict forbids approval; rejection stays allowed and does not clear the conflict` | Passed（新規則；英文原文保留） |
| 4 | `reject` 拒絕（citation `b4cdb4ff1fab4df0abc6b63585d37ad2`）預覽 | 待審核→已拒絕；版本衝突→衝突；修訂 1；不發點 | Passed |
| 5 | 確認後 `show` | 已拒絕、修訂 2、版本仍為衝突（衝突原因不變）、證據 2 筆、內容判定 0 筆、歷程 3 筆；新增拒絕紀錄含理由與引用 | Passed |

**本輪不宣稱**：持久化、Ledger、點數與發點；衝突解除（程式未開放）；相對排序；真實資料庫行為。

### 人工重驗：輪次 B（重審通過，2026-10-23，獨立重啟後的單一輪次）

| 步驟 | 操作 | 觀察結果 | 狀態 |
|---|---|---|---|
| 0 | `show op-1`（重啟後初始） | 待審核、修訂 1、版本未確認、證據 0 筆、內容判定 0 筆、歷程 2 筆 | Passed |
| 1 | `confirm_version` `synthetic-orig-X`（basic，來源 `synthetic-x-source-A`）預覽→確認 | 預覽：未確認→預計已確認；確認後 show：已確認、修訂 1、證據 1 筆 | Passed |
| 2 | 第一次 `approve`（citation `synthetic-approval-test-2`）預覽→確認 | 預覽：待審核→已核准、版本維持、修訂 1；確認後 show：已核准、修訂 2、內容判定 1 筆、歷程 3 筆 | Passed |
| 3 | `reexamine` 同版本 `synthetic-orig-X`（來源 `synthetic-reexam-source-B`）預覽 | 預覽：已核准→待審核、版本維持已確認 `synthetic-orig-X`（同版本，無衝突）、修訂 2、不發點。**不符預期：預覽說明寫「重審期間不得直接核准」，與 Owner 裁決（重審通過的核准會建立新排序事件）相反。** | **停止：待查（文字與裁決不一致）** |

**停止原因**：`bot/owner_review.py` 的 `REEXAMINATION_EFFECT` 仍保留舊規則的說明文字，未隨裁決更新。行為本身（核准在重審後以新排序事件通過）已由離線測試覆蓋，但 Owner 看到的說明與行為不符。

**尚未執行**：重審確認、重審後的核准預覽（重審通過的說明）、重審通過後的 show。
| 4 | 確認重審（選項 2：不修正文字，繼續觀察） | show：待審核、修訂 3、版本仍為已確認 `synthetic-orig-X`（同版本，無衝突）、證據 2 筆（新增合成重審證據 `synthetic-reexam-source-B`）、內容判定 1 筆、歷程 4 筆，新增待審核的重審紀錄 | Passed（同版本重審的狀態與歷程）；說明文字不符仍待修正 |
| 5 | `approve` 重審通過（citation `synthetic-approval-test-3`）預覽 | 待審核→預計已核准；版本維持；修訂 3；財務說明含「重審通過：確認時產生新排序事件，排到當時隊尾；原受理序號與歷史保留」；不發點 | Passed（預覽） |
| 6 | 確認後 show | 已核准、修訂 4、版本仍為已確認、證據 2 筆、內容判定 2 筆、歷程 5 筆；新增已核准紀錄含理由與引用 | Passed（畫面狀態與歷程） |

**輪次 B 結論（僅畫面觀察）**：同版本重審後的重審通過核准，畫面顯示狀態、修訂、歷程與預覽說明均正確。
- **未驗證**：排序序號本身（show 不顯示）；相對隊尾順序（單一假案件無法證明）；持久化；Ledger；點數與發點。
- **已修正（文字，2026-10-23）**：重審預覽原本的「重審期間不得直接核准」已依裁決改為「重審會保留原決策與歷史；重審通過核准時，建立新的排序事件並排到當時隊尾，不恢復原優先權，也不保證發點。」（`bot/owner_review.py`）。輪次 B 的重審預覽是在修正前取得，該說明已被取代。修正後只需重新取得一次重審預覽確認文字，不需要重做整個流程。


### 歷史紀錄的狀態標註（2026-10-23）

- 本文件中「重審後核准被阻擋」（步驟 5 與步驟 6）是**舊規則**的結果，已被 Owner 裁決取代，不得再引用為目前行為。
- 「重審預覽說明：重審期間不得直接核准」是舊文字，已於 2026-10-23 修正，見輪次 B 的追加說明。
- 輪次 A 與輪次 B 的其餘結果，以及先前的非 Owner 拒絕、選單可見性、update-guild 結果，不受本次文字修正影響。

### 人工重驗：重審提示文字（2026-10-23，修正後重啟，單一預覽）

| 步驟 | 操作 | 觀察結果 | 狀態 |
|---|---|---|---|
| 0 | 重啟 listen（沿用相同 Application／Guild，未重新同步） | 重啟後記憶體假案件清空 | — |
| 1 | `approve` `op-1`（citation `synthetic-approval-text-check`）預覽→確認 | 確認後：已核准、修訂 2 | Passed |
| 2 | `reexamine` `op-1`（同版本 `synthetic-orig-R`，來源 `synthetic-reexam-text-check`）預覽 | 說明文字：「重審會保留原決策與歷史；重審通過核准時，建立新的排序事件並排到當時隊尾，不恢復原優先權，也不保證發點。」**不再出現「重審期間不得直接核准」**。預覽：已核准→預計待審核；修訂 2；版本未確認→預計已確認 `synthetic-orig-R`（由重審證據確認，屬預期） | Passed（文字重驗） |
| 3 | 按「取消」 | 「已取消，此預覽不會執行。」 | Passed（未寫入） |

**更正**：重驗前我把版本預期寫成「未確認 → 未確認」，這是錯誤的。重審附帶的新證據會確認版本，因此預覽為「未確認 → 預計已確認」，與程式一致。

**本輪只驗證**：提示文字已更新，預覽與取消不寫入。**不驗證**：重審確認後的狀態、排序位置、持久化與發點。
