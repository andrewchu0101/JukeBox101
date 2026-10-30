# 正式 /exchange 真實 Discord 小範圍驗收計畫（規劃，2026-10-24）

**性質**：唯讀審閱與計畫文件。**本文件不授權執行。** 本輪沒有修改功能或測試，沒有執行測試，沒有啟動 bot 或 MySQL，沒有連線 Discord 或資料庫，沒有讀取 `.env`，沒有解密憑證，沒有變更部署設定，沒有 commit 或 push。承接 checkpoint `32a0307`。

**定位**：本計畫描述的是**開發伺服器內的小範圍真實驗收**，只允許一名明確同意的測試者，正式入口由測試程序的環境變數臨時開啟。它**不是 Beta 對外開放**，Beta 範圍與補償開關都不因此改變。

## 1. 真實啟動流程（已從程式確認）

| 項目 | 程式事實 | 來源 |
| --- | --- | --- |
| 入口 | `python -m bot`，`bot/__main__.py:main()` | `bot/__main__.py` |
| 必要環境變數 | `DISCORD_DEV_GUILD_ID`、`DISCORD_EXPECTED_APPLICATION_ID`、`DISCORD_OWNER_USER_ID`、`DISCORD_BOT_TOKEN`、`DB_HOST`、`DB_PORT`（缺省 3306）、`DB_USER`、`DB_PASSWORD`、`DB_NAME` | `bot/acceptance_config.py`、`bot/config.py`、`.env.example` 的鍵名 |
| 正式開關 | `JUKEBOX_FORMAL_EXCHANGE`，缺省關閉；只接受 `1/true/yes/on` 開啟 | `bot/config.py::load_formal_exchange_enabled` |
| synthetic 路由 | 啟動時**不傳入** `synthetic_exchange`，因此關閉 | `bot/__main__.py` |
| 補償開關 | `SubmissionRepository` 預設 `compensation_enabled=False`，啟動流程不傳入 | `bot/__main__.py`、`submission_repository.py` |
| 守衛 | `GuildInteractionGuard`：只接受指定伺服器與應用程式的應用程式指令，其他一律拒絕 | `bot/gateway_listener.py` |
| 啟動 | `client.run(token)`，登入後等待 Discord 互動 | `bot/__main__.py` |
| 啟動時自動同步指令 | **沒有**。`JukeBoxClient` 沒有 `setup_hook`，啟動流程不同步指令 | `bot/discord_app.py` |
| 啟動時 migration | **沒有**。啟動流程不建立或修改任何資料表 | `bot/__main__.py`、`bot/database/connection.py` |
| 背景工作 | **沒有**。啟動後沒有排程、recovery 或 worker；每次操作各自開啟連線 | `bot/database/connection.py`（`autocommit=False`，無連線池） |

**入口確認**：`/exchange` 指令在 `bot/discord_app.py` 註冊；處理器先判斷 synthetic（預設關），再判斷正式開關（預設關），皆關時回覆固定文字。

## 2. 測試資源與缺少的資訊

程式與文件中**找不到**下列資訊，以下只列需求，不記錄任何值：

| 資源 | 狀態 | 需要 Owner 提供 |
| --- | --- | --- |
| 測試用 Discord 應用程式與 bot token | 不在 repo，`credentials.dpapi` 只有 root、exchange、migrator 三個鍵 | 確認應用程式已建立，並決定 token 如何在程序啟動時注入（只放程序環境，不寫檔） |
| 開發伺服器（guild）ID | 未記錄於 repo | 提供 ID；確認該伺服器目前是否已有指令 |
| 應用程式 ID | 未記錄於 repo | 提供 ID |
| Owner 的 Discord 使用者 ID | 未記錄於 repo | 提供 ID |
| 測試者的 Discord 使用者 ID 與同意 | 無 | 測試者明確同意接收一則測試 DM，並提供 ID |
| 伺服器成員範圍 | 未知 | 確認開發伺服器是否為私人伺服器，成員只有 Owner、測試者與 bot |
| 合成歌曲的真實連結 | 無；資料庫中的 `Synth0002X2` 是測試標記，**不是真實 YouTube 連結，不能送給真人** | 由 Owner 指定一個允許使用的真實 YouTube 影片，並人工確認可用 |

## 3. 測試 bot 與隔離資料庫

**可行性**：程序可以使用既有 3307 隔離資料庫，但必須**明確設定** `DB_PORT=3307`、`DB_HOST=127.0.0.1`、`DB_NAME=jukebox101_test`、`DB_USER=jukebox101_test_exchange`。

**風險（需由啟動器強制）**：
- `DB_PORT` 缺省為 3306，而 bot 程式**沒有**拒絕 3306 的守衛。只要環境漏設，程序會連到既有的 MySQL80 服務。
- `DB_USER` 只禁止 `root`。
- 因此本次啟動器必須在啟動前斷言 `DB_PORT=3307`、`DB_NAME=jukebox101_test`、`DB_USER=jukebox101_test_exchange`，並以身分檢查確認連線目標（沿用既有 `check_server_identity`）。

**資料庫狀態**：
- 目前 3307 的 `jukebox101_test` 在上一輪清理後沒有合成營運列（只剩兩個固定版本根）。
- 因此**沒有可交換的候選歌曲**，任何測試者都會得到 `NO_CANDIDATE`，無法完成正常交換。
- 需要先以**既有生產邊界**準備候選與點數：送入投稿、Owner 確認版本、核准、結算，並將候選歌曲標為已驗證。這需要 Owner 提供的真實連結與測試者 ID。

## 4. 指令註冊、同步範圍與權限

- **同步**：只有 `bot/guild_acceptance.py::guild_sync` 會同步指令，且需要 `authorized is True`、獨立的登入授權，以及人工輸入確認字串。
- **範圍**：只同步到指定的開發伺服器（guild），**不做全域同步**。CLI 沒有 global-sync 選項。
- **前置條件**：`require_empty_remote_guild` 要求該伺服器**沒有既有遠端指令**。若已有指令，同步會拒絕，這是安全設計，但需要 Owner 確認現況。
- **可見性**：`/exchange` 回覆為 ephemeral，只有使用者本人看得到；DM 只送到測試者帳號。
- **對其他伺服器的影響**：不會影響其他伺服器，因為沒有全域同步，且守衛拒絕其他伺服器的互動。
- **未知**：開發伺服器目前是否已有指令。**需要 Owner 確認，我無法在不登入的情況下查詢。**

## 5. 限制只有批准的測試帳號能執行交換（缺口）

**目前只有伺服器限制，沒有帳號限制。** `GuildInteractionGuard` 只比對 `guild_id` 與 `application_id`，任何在該伺服器的成員都能執行 `/exchange`。

目前可行的緩解：
- 使用**私人開發伺服器**，成員只有 Owner、測試者與 bot。
- 測試期間由 Owner 監看，並在測試者完成一次後立即停止程序。

**缺口**：這不是程式層級的帳號限制。若伺服器不是私人的，任何成員都能觸發真實交換與真實 DM。**本輪不自行實作帳號白名單**，需要 Owner 決定是否接受私人伺服器作為唯一緩解，或另行授權實作白名單（見第 8 節）。

## 6. 合成資料與點數的準備與限定

- **不可使用真實營運資料**：本次只使用 3307 隔離資料庫。
- **不可套用會刪除測試以外資料的夾具**：既有的共用夾具會刪除 `jukebox101_test` 的範圍資料，本次不得使用。若需要清理，只能使用**本次列出的識別**，並沿用已驗證的清理前證據機制（計畫、讀回、限定刪除、結果核對）。
- **候選歌曲**：由 Owner 指定**一個真實、允許使用、已人工驗證的 YouTube 影片**。測試者會收到這個連結，因此不能使用測試標記。
- **候選來源身分**：投稿者可以使用一個**合成的內部身分**（不是真人的 Discord ID），以避免與測試者重疊。
- **測試者點數**：測試者需要一點。可以透過以下方式之一取得：
  - (a) 由測試者真實 `/share` 投稿，再由 Owner 核准與結算（需要 Owner 在 Discord 內操作 Owner 指令）。
  - (b) 以生產邊界的 Python 腳本代為投稿與結算（需要測試者 ID 與同意）。
- **排除**：測試者投稿的歌曲不得成為自己的候選，這由既有程式排除。

## 7. 停止、關閉、清理與外部效果

**停止方式**：
- 正常停止：終止 bot 程序（Ctrl+C 或程序結束）。啟動流程沒有需要等待的背景工作，每次操作的連線都會關閉。
- 關閉開關：不需要額外步驟，開關只存在於測試程序的環境變數中，程序結束即失效。

**資料清理**：
- 只清理本次列出的識別：測試者的投稿與保留、合成投稿者、候選歌曲、點數列。
- 清理前保存計畫與讀回，成功後才刪除，並核對列數。
- 測試者的 Discord 使用者 ID 列在清理範圍內，因此清理需要明確授權。

**不能保證回復的外部效果**：
- **已送出的 DM 無法撤回**。測試者會收到一則含真實連結的 DM。
- **已顯示的 ephemeral 回覆無法收回**。
- **若同步指令**，指令會留在開發伺服器中。移除指令需要另行授權，本工具沒有移除功能。
- 資料庫中的列可以清理，但需要另行授權與證據。

## 8. 真正需要 Owner 決定的事項

| 編號 | 事項 | 選項 | Claude 建議 |
| --- | --- | --- | --- |
| A | 帳號層級限制 | (a) 只使用私人開發伺服器作為緩解；(b) 另行授權實作使用者白名單 | (a) 先執行本次驗收；正式開放前再決定 (b) |
| B | 測試 bot token 與應用程式 | 由 Owner 在程序啟動時注入 | 不寫檔，不進入 repo |
| C | 候選連結 | 由 Owner 指定並人工驗證一個真實 YouTube 影片 | 必須是 Owner 允許的影片，測試者會收到它 |
| D | 測試者與同意 | 測試者同意接收一則 DM，並提供 ID | 必須有明確同意 |
| E | 伺服器現況 | 確認是否已有遠端指令；是否為私人伺服器 | 若已有指令，先停止，另行決定 |
| F | 點數來源 | (a) 測試者真實 `/share`；(b) 腳本代為投稿與結算 | (a) 更接近真實流程，但需要 Owner 操作 |

除上表外，其餘事項都有既有決定，不重列為待決。**Beta 範圍與補償開關不在本表中，它們不因本次驗收改變。**

## 9. 最小驗收目標與不做的事

**目標**：
1. 真實啟動成功，只在本次測試程序中設定 `JUKEBOX_FORMAL_EXCHANGE=1`。
2. 開發伺服器內 `/exchange` 的註冊、權限、defer 與 ephemeral 回覆正常。
3. 一名明確同意的測試者完成一次正常交換，核對**一次扣點**與**一則真實 DM**。
4. 驗收後正常停止，確認程序與資料庫無殘留。

**不做**：
- 不刻意製造送達未知或網路故障。
- 不測 403／50007 的退款語意。
- 不重跑既有故障矩陣。
- 不開放 Beta，不啟用補償，不改部署設定。

## 10. 可行性結論與前置修正

**結論**：在 Owner 提供第 2 節與第 8 節的資訊後，本次驗收**可行**。程式的啟動路徑、守衛、正式分支與派送程式都已就緒。

**前置修正**：
- **不需要功能修正**即可進行本次小範圍驗收，因為正式入口已在離線與隔離 MySQL 驗證。
- 啟動器需要新增「啟動前斷言 DB 目標」的檢查，這是驗收工具，不是功能修正。
- 帳號白名單**不是**本次的前置條件，若 Owner 選擇私人伺服器緩解（8-A 的 a），則不需實作。

**缺口**（已明確列出，不自行實作）：
1. 只有伺服器層級的限制，沒有帳號層級的限制。
2. bot 程式對 DB 埠沒有 3306 守衛，依賴啟動器的斷言。
3. 候選歌曲與測試點數需要 Owner 提供的真實資料，目前不存在。

## 11. 一份可一次批准的有限執行授權（草案，尚未批准）

**範圍**
- **bot**：只啟動一個測試程序，`python -m bot`，由專用啟動器呼叫。環境變數只存在於程序中，不寫檔。
- **伺服器**：Owner 指定的開發伺服器（guild ID 由 Owner 提供），且須為私人伺服器（或 Owner 接受 8-A 的緩解）。
- **應用程式**：Owner 指定的測試應用程式（ID 由 Owner 提供）。
- **測試帳號**：只有 Owner 指定的一名已同意測試者。
- **指令同步**：僅限該伺服器，需要獨立授權與人工確認字串，且伺服器必須沒有既有遠端指令。
- **資料庫**：只使用 `127.0.0.1:3307`、`jukebox101_test`、`jukebox101_test_exchange`。啟動器必須斷言這些值，並確認身分。
- **資料寫入**：只限本次列出的合成投稿者、候選歌曲、測試者的投稿與點數、一次交換的保留與扣點。
- **真實 DM 數量**：**恰好 1 則**，送到測試者帳號，內容為 Owner 指定的真實連結。
- **候選連結**：Owner 指定的真實 YouTube 影片，已人工驗證。

**允許**：啟動流程、一次 `/exchange`、一則 DM、ephemeral 回覆、受限清理（計畫與讀回）、正常停機、證據保存（新的證據目錄）。

**禁止**：DDL、DROP、TRUNCATE、增權、建立或修改帳號、3306、`.env`、解密超出既有 DPAPI 的內容、輸出 token 或密碼、全域指令同步、補償啟用、Beta 開放、真實 DM 超過 1 則、commit、push、修改功能程式、用測試標記的連結送給真人。

**停止條件**（任一發生即停止，保留證據，正常停機）
- DB 目標不符（埠、主機、資料庫或帳號不是本次批准的值）。
- 任何 DM 送到非同意的帳號，或 DM 數量超過 1。
- 回覆含連結，或回覆含 synthetic 標籤。
- 出現非預期的第二次扣點或保留。
- 同步前發現伺服器已有遠端指令。
- 非本次批准的成員觸發 `/exchange`。
- 任何非預期錯誤。

**停機與殘留檢查**
- 終止 bot 程序，確認程序已結束。
- 確認 3307 沒有非預期的連線與監聽。
- 確認沒有其他 bot 或 worker 程序。
- 證據保存在新的證據目錄。

**證據**：啟動紀錄（不含 token）、DB 身分與基線、交換前後的列數、DM 數量（由 Owner 或測試者確認）、清理計畫與結果、停機檢查。

**外部效果告知**：DM 無法撤回；指令同步後會留在伺服器中。

## 12. Beta 與本次驗收的區分

- **本次驗收**：只在開發伺服器內，只對一名測試者，只在測試程序中開啟正式開關。
- **Beta 對外開放**：不在本次範圍。Beta 規格中 `/exchange` 仍為「未開放、不納入」。
- **補償功能**：仍關閉。
- **真實 DM 的分類語意**（403／50007）：本次不測，沿用 D2 的既有行為。

## 13. 後續實作（若需要）

若 Owner 選擇 8-A(b) 帳號白名單，需要另行批准實作，並使用可用且適合的 tdd skill。本輪不建立團隊，start-dev-team 未使用。

---

## 14. 具體執行方案與最後授權草案（2026-10-24，尚未批准，尚未執行）

**狀態**：本節是執行方案。**沒有執行任何動作。** 列有 `[待填]` 的項目是 Owner 尚未提供的資訊，**不得由 Claude 猜測**。本節不改變第 1–13 節的結論，也不改變 Beta 範圍與補償開關。

### 14.1 Owner 回覆的落實

| 項目 | 回覆 | 落實方式 | 尚缺 |
| --- | --- | --- | --- |
| A 私人伺服器 | 只有 Owner 與測試 bot | 不做白名單。依 Owner 的環境設定，伺服器成員即為 Owner 與 bot | 無（但需 E 的伺服器 ID） |
| B token | 本機隱藏輸入，只存程序記憶體 | **現有啟動方式做不到**：`python -m bot` 只讀環境變數，把 token 設為環境變數會留在 shell 歷史或程序環境。需要本地啟動器：以 `getpass` 隱藏輸入，再於**同一程序內**呼叫 `bot.__main__.main()`。不寫檔、不進命令列 | 無（實作啟動器需授權，見第 15 節） |
| C 連結 | 允許發送的 YouTube 連結 | 作為候選歌曲的唯一來源 | **`[待填：已人工確認可用的連結]`** |
| D 測試者 | Owner 本人，同意一則測試 DM | Owner 是測試者與 Owner，兩者為同一帳號 | Owner 的 Discord ID **`[待填]`** |
| E 伺服器與應用程式 | — | 指令同步與守衛只使用這兩個 ID | 伺服器 ID **`[待填]`**、應用程式 ID **`[待填]`**、遠端指令現況 **`[待填：無／已有／不知道]`** |
| F 點數與候選 | 優先真實 /share 與 Owner 核准；不直接改餘額 | 見 14.3 | 需要第二個 YouTube 連結（見 14.3） |

### 14.2 指令同步

- **同步範圍**：只同步到 E 的開發伺服器（guild），**不做全域同步**。
- **完整清單**：`balance`、`daily`、`exchange`、`history`、`share`（共 5 個，與 `EXPECTED_COMMAND_NAMES` 完全一致）。
- **不包含** `/owner` 審核指令組：正式啟動流程不傳入 Owner 審核設定，因此 Discord 內不會出現審核指令。這也是 Owner 核准必須走腳本路徑的原因（見 14.3）。
- **遠端現況**：不知道時，先以**唯讀**方式查詢（`fetch_commands`，同一次獨立授權的登入內完成）。若伺服器已有遠端指令，同步會拒絕，**不覆蓋**，停止並回報。
- **已有指令的處理**：若 E 回覆「已有」，本計畫不執行同步，需另行決定。

### 14.3 候選歌曲與點數（F）

**程式事實**：正式程式沒有任何路徑把歌曲設為 `verified`。`songs.resolution_status` 由 `pending` 開始，沒有驗證步驟。交換只會提供 `verified` 的歌曲，因此**在不改資料的情況下，正式流程沒有可交換的候選**。這是產品缺口（ROADMAP 標示為未解決的驗證來源），不是測試問題。

**需要 Owner 決定 G（候選歌曲標為已驗證的方式）**（**已取代**：改由 §20 的核准發布產生 verified 連結，不再使用單列 SQL 更新）：
- (a)（建議，僅限本次驗收）：由 Owner 批准後，對**本次建立的一列候選歌曲**執行一次明確的 SQL 更新（`resolution_status='verified'`），並在證據中記錄。這不是正式流程，只用於本次驗收。
- (b) 另行批准實作正式驗證步驟（超出本次範圍，需 ROADMAP 決定）。
- (c) 暫停本次驗收。

**準備方式（沿用正式 repository 邊界）**：
1. **候選歌曲（連結 C）**：由**合成的內部投稿者身分**（不是真人，不是 Owner）透過正式 intake 送入 C，再由 Owner 身分的正式審核服務確認版本、核准、結算。最後依 G 的方式標為已驗證。這樣 Owner 自己的投稿不會排除此候選。
2. **Owner 的點數（連結 D）**：Owner 以真實 `/share` 投稿 D。D 不能與 C 相同，因為同一影片不能重複投稿。之後由 Owner 身分的正式審核服務完成確認版本、核准、結算，使 Owner 得到一點。
3. 交換時，Owner 自己的 D 會被既有程式排除，唯一候選為 C。
4. **需要 Owner 提供連結 D**（`[待填]`，同樣需要人工確認可用）。

**不做**：不直接修改餘額；不使用真人第二帳號；不把合成身分假裝成真人操作。

### 14.4 DM 與送出語意（依 Owner 指定的表述）

- 本次 `/exchange` 的 DM 送出**最多一次**呼叫，**預期收到一則**。
- 若送達結果未知，**不重送**，也不自動退款。
- **不保證外部服務恰好送達一次**。
- 已送出的 DM **不自動刪除**。是否可由 Owner 手動刪除取決於 Discord 的客戶端行為，本計畫不宣稱技術上絕對無法刪除。

### 14.5 資料清理與保留

- 清理**不得破壞** append-only 審計或其他資料約束。`credit_ledger`、`exchange_incident_events` 等表有審計或約束，不適合刪除。
- **預設：保留**本次隔離資料，並在證據中記錄識別（列 ID、使用者 ID、連結、時間）。
- 若 Owner 希望清理，需另行批准，且只限本次列出的列，並採計畫、讀回、限定刪除與列數核對。
- 指令同步**保留**在伺服器中。本工具沒有移除功能，移除需另行授權。

### 14.6 停止與關閉

- **bot**：在啟動器的主控台按 Ctrl+C，由 discord.py 的 `client.run` 結束連線；確認程序已不存在。啟動流程沒有背景工作，每次操作的連線已關閉。
- **MySQL 3307**：以 root 的 SHUTDOWN 正常停止，與既有方式相同；確認 3307 無 Listen，無 mysqld 指向本 datadir。**不碰 MySQL80（3306）**。
- **正式開關**：只在 bot 程序內設定，程序結束即失效。

### 14.7 最後授權草案（可一次批准，尚未批准）

**範圍**
1. **程序**：一個本地啟動器，用隱藏輸入取得 token，設定環境變數後於同一程序呼叫 `bot.__main__.main()`。`JUKEBOX_FORMAL_EXCHANGE=1`、`DB_PORT=3307`、`DB_HOST=127.0.0.1`、`DB_NAME=jukebox101_test`、`DB_USER=jukebox101_test_exchange`、`DB_PASSWORD` 由既有 DPAPI 在記憶體解密。**啟動前斷言所有值。**
2. **伺服器與應用程式**：E 的 ID `[待填]`。
3. **指令同步**：僅 guild，5 個指令，獨立授權與人工確認字串；先唯讀確認遠端無指令。
4. **資料庫**：只使用 3307、`jukebox101_test`、`jukebox101_test_exchange`；既有 datadir 與 UUID；身分與基線檢查。
5. **資料寫入**：Owner 的 `/share` 與審核結算產生的列（D）、合成內部投稿者的 intake 與審核結算列（C）、候選歌曲的一次 `verified` 更新（僅 G 選 a 時）、一次交換的保留與扣點與派送結果。**不寫入其他列。**
6. **真實 DM**：**最多一次呼叫，預期一則**，送到 Owner 帳號，內容為 C 的連結。
7. **真實 Discord 互動**：Owner 的 `/share`（D）、`/exchange` 一次、必要的 `/balance` 查詢。

**允許**：啟動、指令同步（僅 E 的伺服器，且遠端無既有指令）、上述寫入、一次 DM、正常停機、證據保存（新的證據目錄）。

**禁止**：DDL、DROP、TRUNCATE、增權、建立或修改帳號、3306、`.env`、輸出或寫入 token 與密碼、全域指令同步、補償啟用、Beta 開放、第二次以上的 DM、非本次列出的資料寫入、清理（除非另行批准）、commit、push。

**停止條件**：DB 目標不符；伺服器已有遠端指令；DM 數量或收件人不符；回覆含連結或標示；非預期的第二次扣點；Owner 審核或結算步驟失敗；任何非預期錯誤。停止後保留證據，正常停機。

**證據**：新的證據目錄（不含 token 與密碼）、DB 身分與基線、各階段列數、DM 數量（由 Owner 確認）、停機檢查。

**外部效果告知**：DM 送出後不保證恰好送達一次，且不自動刪除；指令同步後保留在伺服器中。

**需要 Owner 先回覆的項目**：`[待填]` 的 C、D、E（伺服器與應用程式 ID、遠端指令現況）、Owner 與測試者 ID，以及 G 的選擇（a／b／c）。

## 15. 實作前置（若批准，需另行授權）

- **本地啟動器**：隱藏輸入 token 並於同一程序啟動 bot，預計位於 `.local/mysql-test/`，不提交。
- **準備腳本**：以正式 intake、審核與結算邊界準備 C 與 Owner 的點數，預計位於 `.local/`，不提交。
- **不需要功能修正**即可進行本次驗收。
- 若需要，實作前使用可用且適合的 tdd skill；本輪不建立團隊，start-dev-team 未使用。

---

## 16. 執行前阻塞點（2026-10-24，尚未執行任何寫入）

**狀態**：Owner 已批准選項 1（控制列建立與結算期間暫時開啟 `settlement_enabled`）。執行前核對 `jb2_control` 結構與候選條件後，發現下列一項**超出已批准的 G 範圍**，因此停止，**未對資料庫做任何寫入**。

### 16.1 控制列（已從程式確認）

- 結構（`bot/database/migrations/002_submission_economy.sql`）：`singleton_id`（必須為 1）、`schema_version`、`accepted_sequence`（預設 0）、`revision`（預設 0）、`snapshot_ready`、`legacy_writers_quiesced`、`settlement_enabled`（三個旗標皆為 0／1）。
- `/share`（pending intake）要求 `(snapshot_ready, legacy_writers_quiesced, settlement_enabled) = (1, 1, 0)`（`submission_repository.py:99`）。
- 點數發放要求 `settlement_enabled = 1`（`reward_settlement.py:57`）。
- 交換要求 `jb2_control` 存在且 `schema_version = '2b2-b1'`（`submission_transaction.py`）。
- 規格 `docs/recovery-batchb-private-beta-spec.md` §4.4 對 `snapshot_ready` 與 `legacy_writers_quiesced` 要求證據（歷史快照與對帳、寫入者停止證據）。在本次隔離環境，對應證據為：基線列數與定義（T0）、寫入者停止證據（唯一會話為本次程序）、`ledger` 對帳（`account_violations`，空基線）。這些須在建立控制列之前收集並保存。

### 16.2 阻塞：G 的更新範圍不足以讓 C 可交換

- 交換只提供同時滿足 `verified` 與 `youtube_url` 非空的候選（`bot/exchange_selection.py:30`）。
- 正式 intake 只寫入 `source_url`，`youtube_url` 為 NULL（`bot/database/repository.py:106`）。
- 整個 `bot/database` 中**沒有任何寫入會設定 `youtube_url`**。
- 因此，已批准的「只更新 `pending → verified`」即使成功，C 仍不可交換。

**需要 Owner 明確批准的最小擴充**：同一列 C（單列、以 `platform_track_id = 'cUfDOS2SINM'` 限定）同時設定
- `resolution_status = 'verified'`
- `youtube_url = 'https://youtu.be/cUfDOS2SINM'`（與 Owner 指定的 C 相同）
- `verification_provenance = 'acceptance-owner-decision-2026-10-24'`（僅作標記）

更新前後會以 `assert_single_candidate_update` 核對列數，並保存更新前後狀態。

### 16.3 其他已確認的事項（不阻塞）

- `settle_reward` 以 `operation_id` 結算，但會對全部目前決定做持久化（`persist_decisions`）。在指定資料範圍內（只有 C 與 D 兩筆待決定）可接受，並會在證據中記錄 pending 清單。
- 指令同步只有 `balance`、`daily`、`exchange`、`history`、`share`，與 `/owner` 審核指令組無關（正式啟動流程不傳入審核設定）。

### 16.4 尚未進行的步驟

- 控制列建立（含證據收集）、C 的 intake、token 啟動器、遠端指令唯讀查詢、`/share` D、結算窗口、`/exchange`、DM 驗證、停機與殘留檢查。
- 在 16.2 獲得批准前，不進行任何寫入。

### 16.5 已完成（唯讀）

- 基線：身分、schema、授權快照、空基線與控制列數（0）通過；3307 正常停機，無殘留。

---

## 17. 結算前的新阻塞：全片時長證據（2026-10-24，已停止，未寫入資料庫）

**狀態**：授權內的局部準備已完成；控制列、intake、G 更新、結算、bot 與 DM 均**未執行**。資料庫仍為空基線（`jb2_control` 0 列）。

### 17.1 已完成（授權內）

- 本地程序與監聽盤點（唯讀）：3307 未監聽；無 Python 或 bot 程序；僅有 MySQL80（3306，不屬於本次範圍，未觸碰）。3307 只綁定 127.0.0.1，因此寫入者只能是本機程序。
- 守衛與測試：`tests/live_acceptance_guard.py`、`tests/test_live_acceptance_guard.py`（12 項，事後驗證）、`tests/test_live_acceptance_steps.py`（10 項；先寫測試，首次因守衛函式不存在而 ImportError 紅燈，再實作後 22 項全綠）。

### 17.2 阻塞 1：審核核准需要全片時長證據，正式路徑沒有來源

- 核准的閘門要求全片時長在允許範圍內（`bot/review.py::_duration_passes`，`bot/owner_review.py` 核准路徑）。
- 時長證據由 `DurationEvidence` 提供，正式程式中只有 `record_validation` 會消費它；**沒有任何正式路徑會產生它**。
- 正式 `/share` 經 `accept_pending`，其請求建構（`_pending_request`）不帶時長證據，因此審核會停在 `pending_validation`，無法核准。
- 測試中的時長（180 秒）是合成值。**不得用於真實影片**（這會是捏造的元資料）。

### 17.3 阻塞 2：C 與 D 都無法經正式路徑進入核准

- C 的 intake 若走 `accept_pending`，同樣沒有時長證據，無法核准。
- 要完成 C 與 D 的核准、結算，必須有一條能附上**Owner 提供的全片時長**的 intake 路徑。現有的 `accept_submission` 只能由內部程式呼叫（測試 fixture 即如此），不是公開的 repository 邊界。
- 因此「D 以真實 `/share` 投稿後取得點數」在現有程式中**不可行**。

### 17.4 需要 Owner 決定（集中）

1. **提供 C 與 D 的全片時長（秒）**：由 Owner 親自確認並保存來源。這不是自動驗證，也不是由工程推定。
2. **選擇處理方式**：
   - (a) 另行批准：以內部 `accept_submission` 搭配 Owner 提供的時長，寫入 C（合成投稿者）與 D（Owner）。D 的 `/share` 仍會實機執行，但點數來源不是 `/share`，需在文件中明確記錄。
   - (b) 另行批准實作正式的「Owner 時長證據」輸入路徑（產品變更，超出本次範圍）。
   - (c) 暫停本次驗收。
3. 若選 (a)，C 與 D 的 intake 與控制列寫入仍需按原授權執行；結算窗口與 G 更新維持原範圍。

### 17.5 收尾

- 目前沒有任何寫入、沒有 bot、沒有 DM、沒有同步。
- 本節不宣稱正式結算、核准或交換已驗證。Beta 範圍與補償開關未改變。

---

## 18. 正式 Owner 時長證據與完整路徑設計（唯讀審閱，2026-10-24，尚未實作）

**方向 (b)**：補正式的 Owner 時長證據輸入流程，不採用內部 `accept_submission` 代替真實 `/share`，不捏造時長。本節只做設計，不寫入資料庫，不啟動任何程序。

### 18.1 完整路徑：現況與缺口

| 步驟 | 現況（程式） | 狀態 |
| --- | --- | --- |
| 1. `/share` → pending | `MusicService._share_pending` → `SubmissionRepository.accept_pending`；要求 `(snapshot_ready, legacy_writers_quiesced, settlement_enabled) = (1,1,0)`（`submission_repository.py:99`）；請求不帶時長證據，審核停在 `pending_validation`（`_pending_request`） | 既有 |
| 2. Owner 提供全片時長 | `SubmissionRepository.review(ReviewCommand(duration=...))` 會執行驗證（`review_persistence.py:726`），結果為 `PENDING_REVIEW` 或 `REJECTED`；**但 `OwnerReviewService` 沒有任何時長方法**，Owner Discord 組也沒有時長子命令 | 核心邏輯既有，**Owner 入口缺** |
| 3. Owner 內容判定與核准 | `OwnerReviewService.preview(APPROVE, content=ContentJudgement)` → `confirm`；核准閘門需要已通過的時長（`owner_review.py:336`） | 既有（服務）；Beta 無入口 |
| 4. 結算（READY → award） | `settle(op)`；`settlement_enabled` 為 0 時拒絕（`reward_settlement.py:57`） | 既有；**政策閘門** |
| 5. 點數／claims／ledger | `_award` 寫入 `jb2_claims`、`credit_ledger`、餘額 | 既有 |
| 6. 候選可交換 | 交換要求 `resolution_status='verified'` 且 `youtube_url` 非空（`exchange_selection.py:30`）；**目前沒有任何寫入會設定這兩欄** | **缺** |
| 7. `/exchange` | 已在隔離環境以 synthetic 與 formal handler 驗證（不代表真實 Discord） | 既有（驗證範圍內） |

### 18.2 DurationEvidence：欄位、來源、綁定與有效性

| 欄位 | 必要性 | 來源 | 綁定與驗證 |
| --- | --- | --- | --- |
| `video` | 必要 | 由 pending 投稿的 URL 經 `parse_youtube_url` 得到的 `VideoIdentity` | 必須等於審核快照的 `video`，否則拒絕（`record_validation`） |
| `seconds` | 必要 | Owner 親自觀看全片後回報的整數秒數 | 只接受非布林 `int` 或有限 `Decimal`，且 ≥ 0 |
| `source` | 必要 | Owner 寫明的來源字串，例如 `owner-observed-full-playback:<日期>:<方法>` | 不得為空白；這是 Owner 的陳述，**不是平台 metadata 驗證** |
| `reliable` | 必要 | Owner 的明確斷言 | 必須為 `True`，否則結果為 `PENDING_VALIDATION` |
| `full_video` | 必要 | Owner 的明確斷言（已觀看全片，非片段） | 必須為 `True`，否則結果為 `PENDING_VALIDATION` |

**有效性規則（既有）**：`MIN_VIDEO_DURATION_SECONDS = 60`，`MAX_VIDEO_DURATION_SECONDS = 720`，邊界包含。秒數在範圍內為 `PASS`（進入待審核）；超出範圍為 `REJECT`（**最終拒絕**，且結束重審，見 `review.py::record_validation`）。因此時長輸入前必須有預覽與二次確認，避免誤輸入造成不可逆的拒絕。

### 18.3 Owner 如何提供真實全片時長

- Owner 親自播放整支影片，記錄全片長度（秒），並寫明來源與方法（`source`）。
- 系統**不**抓取平台 metadata，**不**宣稱時長已由平台驗證。文件、審計與 Owner 的陳述都標示為 Owner 提供。
- 若 Owner 無法確認全片，應保持 `PENDING_VALIDATION`（`reliable` 或 `full_video` 為假），不得推定。

### 18.4 pending 補證據與重審，避免重複投稿

- 補證據以**同一 `operation_id`** 進行：呼叫 `repository.review(ReviewCommand(decision_id, operation_id, actor, reason, expected_revision=目前版本, duration=...))`。不需要重新 `/share`。
- `expected_revision` 必須等於 Owner 檢視時的版本，過期即拒絕（樂觀並行控制）。
- `decision_id` 是冪等鍵：相同意圖的重送回傳原結果，不重複寫入。
- 重複投稿：同一 interaction 的重送回傳原回執；同一使用者對同一歌曲的重複投稿由 `submissions` 唯一鍵限制（`uq_submissions_user_song`，`schema.sql`）。不同使用者投稿同一影片會共用同一 `songs` 列。實作前需以測試確認這三種情況。
- 重新審核：時長通過後回到 `PENDING_REVIEW`，之後 Owner 才能核准；時長拒絕即最終拒絕（若需重開，既有的 `reexamine` 需要可靠的證據紀錄，見 18.6）。

### 18.5 resolution_status、youtube_url、verification_provenance 如何與審核銜接

**現況**：審核（時長與核准）與 `songs` 的交換欄位完全脫鉤，核准後歌曲仍是 `pending` 且無連結，因此不會進入交換池。

**建議（需 Owner 決定 N2）**：Owner 核准（`APPROVE`，且時長已通過、內容判定為歌曲）時，**同一交易**內設定：
- `songs.resolution_status = 'verified'`
- `songs.youtube_url = 規範化的 watch URL`（由已解析的 `video_id` 組成，不接受使用者任意字串）
- `songs.verification_provenance = 'owner_approval:<decision_id>'`（記錄核准來源，不宣稱自動驗證）

拒絕、時長拒絕或版本衝突時，歌曲保持不可交換。

### 18.6 既有規則與需要新決策

**既有規則（不需決定）**：時長 60–720 秒、全片要求、跨影片綁定、Owner 閘門、核准需時長通過與內容判定、結算需 `settlement_enabled=1`、`/share` 需 `settlement_enabled=0`、交換需 `verified` 與 `youtube_url`、每次 1 點、回覆 ephemeral、DM 最多一次。

**需要 Owner 決定的新事項**：

| 編號 | 事項 | 選項 | Claude 建議 |
| --- | --- | --- | --- |
| N1 | Owner 時長輸入入口 | (a) 本地 operator CLI，呼叫既有 `repository.review` 與 `OwnerReviewService`，不接 Discord；(b) 在 Discord Owner 組新增 `duration` 子命令，需要把 `OwnerReviewWiring` 接到啟動流程 | (a)。不改變 Beta 的 Discord 表面與啟動流程，最小 |
| N2 | 核准是否同時發布為可交換連結 | (a) 核准即設定 verified + youtube_url（同交易）；(b) 另設獨立的發布步驟 | (a)。核准本身就是 CLAUDE.md 所要求的 Owner 人工驗證，且不引入外部 metadata |
| N3 | 真實結算的政策與控制開關管理 | 程式標示「需要獨立政策核准」；目前沒有 Owner 工具切換 `settlement_enabled` | 另行政策核准；建議同批以 CLI 管理，預設關閉，每次切換審計 |
| N4 | `/share` 與結算的相位 | `accept_pending` 要求結算關閉，發幣後無法再投稿 | 這是營運相位的產品決策；本次驗收維持「投稿相位」與「結算窗口」分開 |

### 18.7 Beta 目前的 Owner 管理入口

- `OwnerReviewWiring` 只在測試中建構（`tests/` 三處）；`bot/__main__.py` **沒有**傳入 `owner_review`，因此 Owner Discord 組在 Beta 啟動中**不存在**。
- Discord Owner 組的子命令為 list、show、approve、reject、confirm_version、reexamine，**沒有時長**。
- 結論：Beta 目前沒有可用的 Owner 管理入口。repository 的 Owner 閘門方法（`review`、`reexamine`、`confirm_version`）與 `OwnerReviewService` 存在，但沒有可操作的入口。

### 18.8 最小方案（不新增 Discord 指令）

- **新增 operator CLI**（預計 `bot/owner_cli.py`）：以 `DISCORD_OWNER_USER_ID` 為 actor，DB 設定沿用既有 `load_database_settings`（不讀 `.env`）。子命令：
  - `duration preview --operation <id> --seconds <n> --source <text> [--full-video --reliable]`：顯示目前審核版本、時長判定結果（PASS／REJECT／PENDING），並產生確認 token。
  - `duration confirm --token <token>`：以相同意圖寫入一次（`decision_id` 冪等）。
  - `approve preview|confirm`：內容判定與核准，沿用 `OwnerReviewService` 的 preview／confirm。
- **不接線** Discord 啟動流程；**不修改** `bot/__main__.py` 的 Owner 接線。
- 結算開關與 `settle` 的入口不在本方案內（N3）。

### 18.9 權限與審計

- actor 必須等於設定的 Owner ID（repository 的 `require_owner`，每個方法都檢查）。
- 每個寫入需要 `reason`；`decision_id` 冪等；`expected_revision` 防止舊審核覆寫。
- 每次時長與核准寫入既有審計表（`jb2_review_history`、`jb2_decisions`）。
- preview／confirm 使用既有 token 機制（TTL 5 分鐘）；確認前顯示秒數與判定，避免誤拒絕。
- 不記錄 token、密碼或憑證；CLI 只輸出 operation、判定與審計 ID。

### 18.10 測試計畫（後續實作，tdd）

**離線（先紅後綠）**：
- 時長輸入：拒絕布林秒數、負數、空白來源、`reliable=False`、`full_video=False`、跨影片綁定。
- 判定：60–720 為 PASS 進入待審核；範圍外為 REJECT 且最終；`PENDING_VALIDATION` 不改變審核狀態。
- 冪等：相同 `decision_id` 重送回傳原結果；過期 `expected_revision` 拒絕且無寫入。
- 核准與銜接（N2 採用時）：核准同交易設定 verified、youtube_url、provenance；拒絕不設定。
- 重複投稿：同一 interaction 重送回原回執；同使用者同歌曲重複投稿被唯一鍵擋下。

**MySQL（後續，需授權）**：時長 → 核准 → 結算 → 交換候選的整條路徑，在隔離環境以 `repository` 與 CLI 執行。

### 18.11 有限實作授權草案（尚未批准）

- **新增**：`bot/owner_cli.py`（operator CLI）、`bot/owner_duration.py`（若需要的服務輔助）、`tests/test_owner_duration.py`、`tests/test_owner_cli.py`。
- **修改**（僅 N2 採用時）：`bot/database/review_persistence.py` 的核准交易，加入 verified、youtube_url 與 provenance 的同交易寫入。
- **不修改**：Discord 指令表、`bot/__main__.py` 的 Owner 接線、結算開關、`/share` 的相位規則。
- **不執行**：任何真實 Discord 操作、MySQL 寫入、控制列變更、DM。
- **停止條件**：測試非預期失敗；CLI 可在非 Owner 身分下寫入；時長輸入未經確認即寫入。

### 18.12 前置缺口總表

| 缺口 | 類型 | 是否阻塞本次真實驗收 |
| --- | --- | --- |
| Owner 時長入口 | 缺功能（N1） | 是 |
| 核准與候選銜接 | 缺功能（N2） | 是 |
| 真實結算政策與開關管理 | 政策（N3） | 是（結算步驟） |
| `/share` 與結算相位 | 產品決策（N4） | 否（本次以窗口分開處理，但正式營運需決定） |
| Beta Owner Discord 組未接線 | 既有設計 | 否（採 CLI 則不需） |

### 18.13 收尾

- 本節為唯讀設計，沒有任何實作、測試執行、資料寫入、程序啟動或 Discord 操作。
- Beta 範圍與補償開關未改變；正式 `/exchange` 仍預設關閉。
- 真人驗收暫停，等待 N1、N2、N3 的決定與實作授權。

---

## 19. Owner 時長證據與核准發布的實作（2026-10-24，已完成離線，未寫入真實資料，未 commit）

**範圍**：依 N1 至 N4 的決定實作：operator CLI（N1）、核准同交易發布已驗證連結（N2）；N3 不批准正式結算；N4 沿用投稿與結算分窗口。**本節不代表真實驗收已完成，不代表正式結算或正式開放。** 控制列、intake、結算窗口與真人驗收仍暫停。

### 19.1 修改檔案

| 檔案 | 狀態 | 內容 |
| --- | --- | --- |
| `bot/owner_cli.py` | 新增 | operator CLI：`duration preview|write`、`approve preview|write`。無子命令時不讀設定、不連線、不寫入。 |
| `bot/database/review_persistence.py` | 修改（最小） | 新增 `ApprovalLinkRefused` 與 `_publish_owner_approved_link`，於 `apply_review` 的同一交易中、核准後、保存決定前呼叫。 |
| `bot/urls.py` | 修改（最小） | 新增 `canonical_watch_url(video_id)`，作為發布連結的單一來源。 |
| `tests/test_owner_cli.py` | 新增 | 17 項（含補證據不重複投稿、明確確認兩項）。 |
| `tests/test_owner_approval_link.py` | 新增 | 7 項（發布、待驗證、範圍外與拒絕、重放、回滾、過期版本、已連到他處拒絕）。 |
| `tests/submission_persistence_fake.py` | 修改（測試替身） | songs 的 schema 預設值、條件式 `UPDATE songs`、發布狀態讀取。 |

**未修改**：Discord 指令表、`bot/__main__.py`、Owner 接線、結算開關、`/share` 相位規則、`bot/exchange_delivery.py`、補償程式。

### 19.2 CLI 行為（N1）

- **Owner 來源**：只來自 `DISCORD_OWNER_USER_ID`；沒有 `--actor` 參數。缺少或格式錯誤即拒絕，在連線之前。
- **資料庫目標**：`DB_HOST`、`DB_PORT`、`DB_NAME`、`DB_USER`、`DB_PASSWORD` 必須全部明確提供；缺少 `DB_PORT` 即拒絕，**沒有預設 3306 路徑**；`root` 帳號拒絕。錯誤訊息只列出缺少的鍵名。
- **時長輸入**：必須明確指定 `--seconds`（整數）、非空 `--source`、`--full-video-confirmed` 與 `--reliable-confirmed`（兩者缺一即拒絕，不預設為真）。**沒有 `--video` 參數**，影片一律取自已儲存的操作。
- **預覽**：只讀取，顯示影片、目前版本、秒數、判定（PASS／REJECT／PENDING_VALIDATION）與摘要。範圍外時顯示中文警告：「確認寫入後此影片將被最終拒絕，本工具無法撤回」。
- **寫入**：必須帶 `--digest`，其值綁定操作、預期版本、影片、秒數、來源、決定識別、理由與 Owner。摘要不符即拒絕，不寫入。版本過期由既有的 repository 拒絕。
- **冪等**：相同 `--decision-id` 的重放由既有 repository 回傳原結果，不重複寫入。
- **核准**：沿用既有 `OwnerReviewService` 的預覽與確認（同一程序內），內容判定必須明確聲明 `--is-song-confirmed`；寫入摘要綁定操作、版本、影片、理由與證據參照。
- **輸出**：只印出識別、判定、摘要與審核狀態；失敗只印出原因代碼或例外類別名稱，不印出記錄內容或憑證。

### 19.3 核准發布（N2）

- 條件：核准來源為 Owner，且審核結果為 `APPROVED`。拒絕、待驗證、單純補時長都不發布。
- 動作（同一交易）：`UPDATE songs SET resolution_status='verified', youtube_url=<canonical>, verification_provenance='owner_approval:<decision_id>' WHERE platform_track_id=<video> AND platform='youtube' AND resolution_status='pending' AND youtube_url IS NULL`。
- 若影響列數為 1：完成發布。
- 若為 0：讀回同一歌曲；若已是 verified 且連結相同，視為重放，不寫入；否則拒絕核准（`ApprovalLinkRefused`），整個交易回滾。
- **誠實標示**：`verification_provenance` 記錄的是 Owner 的人工核准決定，**不是平台 metadata 或政策認證**。

### 19.4 紅綠證據（tdd，分開記錄）

| 階段 | 結果 | 性質 |
| --- | --- | --- |
| 核准發布測試（實作前） | 夾具先修正（控制列狀態、替身 songs 預設值與條件式更新）後，主流程失敗 `'pending' == 'verified'`，回滾測試失敗 `DID NOT RAISE` | **行為紅燈** |
| 過期版本測試 | 第一版測試順序錯誤（先預覽時缺時長），已修正為「先預覽、再推進版本」後拒絕為 `StaleReviewVersion` | 測試順序修正（記錄） |
| 重放、待驗證、拒絕、範圍外時長測試 | 實作前即通過（它們要求「不發布」，實作前本來就不發布） | 回歸（如實記錄） |
| 核准發布實作後 | 7 項全綠 | 綠燈 |
| CLI 測試（實作前） | `ImportError: cannot import name ... bot.owner_cli`（模組尚不存在） | **符號紅燈** |
| CLI 實作後 | 首次 4 項失敗，原因是測試參數遺漏 `--reason`（預覽的摘要本來就綁定理由）；已修正測試參數；REJECT 警告原為英文，改為中文（與操作者語言一致）後全綠 | 測試參數修正與文字修正（記錄） |
| 替身修正 | 讀取分支誤放在另一函式（已移到正確位置）；種入的列缺少 `platform`／`resolution_status`（改為與 schema 預設一致的讀取） | 替身缺陷修正（記錄） |

**誠實說明**：CLI 的行為斷言在首次紅燈時，有部分是參數錯誤造成的失敗，不是行為紅燈；行為紅燈以核准發布的測試為準。

### 19.5 離線結果

- **最終局部離線套件**（審核、持久化、替身、URL、交換保留、補償守衛、formal 入口、實機守衛）：**569 passed、0 failed、1 warning**（`audioop` 棄用警告，與本輪無關）。
- 未執行完整套件。未執行任何 MySQL、Discord、DM 或真實資料寫入。

### 19.6 仍需實機驗證的部分

1. `UPDATE songs` 的條件式更新在真實 MySQL 上的影響列數語意（InnoDB 預設回報變更列數；本設計以「0 列後讀回比對」處理，因此兩種語意都安全，但需實機確認）。
2. 真實 `jb2_*` 審核、核准與結算的整條路徑（時長 → 核准 → 發布 → 結算 → 候選 → `/exchange`）。
3. 控制列、結算窗口、真人 `/share` 與 DM 仍暫停。

### 19.7 提交注意

- `bot/database/review_persistence.py` 在工作樹中另有開場即存在的其他 review 修改（忽略行尾後約 185 行差異）。**提交時只能逐區塊暫存本輪的三處**：`from bot.urls import canonical_watch_url`、`apply_review` 中的發布呼叫、`ApprovalLinkRefused` 與 `_publish_owner_approved_link` 區塊。
- `tests/submission_persistence_fake.py` 的既有差異需要同樣的檢查；本輪只增加了替身的 songs 行為。
- 新增檔案：`bot/owner_cli.py`、`tests/test_owner_cli.py`、`tests/test_owner_approval_link.py`。
- `bot/urls.py` 的差異只有新函式（10 行）。
- 不提交 `.local/`、證據與憑證。

## 20. 離線實作與真人前修正（2026-10-25，離線，未 commit，未實機）

**範圍**：本節記錄離線實作的結果與真人驗收前的修正。**本節不代表真人 DM 驗收已完成，也不代表正式交換或結算已開放。** 所有命令與測試只在離線 fake 與工作樹上執行，未連線 MySQL、Discord 或環境檔。

### 20.1 開關時序（以程式確認）

| 入口 | `settlement_enabled` 要求 | 依據 |
| --- | --- | --- |
| `/share` 與 pending intake（`accept_pending`，含合成 C） | 必須為 **0** | `submission_repository.py` pending intake 守衛 |
| 時長、核准、版本確認 | 不讀取 | owner review 與 review persistence 無此欄位 |
| `settle()` | READY 時必須為 **1**；非 READY 不讀取 | `reward_settlement.py` |
| `/exchange`（`reserve_exchange`） | **不讀取，任何值皆可** | `exchange_reservation.py` |

**流程時序**：執行前原值必須為 0（不為 0 即停止）。投稿與審核期間保持 0；只在 `settle` 前後切為 1；結算後立即恢復為執行前原值。`/exchange` 不讀取開關，因此恢復後不影響交換。

### 20.2 候選池與餘額的兩階段檢查（唯讀，不清理）

1. **投稿前**：唯讀查詢既有「已結算、已驗證連結、投稿者非 Owner」的候選。若數量不為 0，**停止**並說明；不刪除、不消耗舊候選、不新增 schema。
2. **結算後、交換前**：唯讀確認候選池只含 C（D 屬 Owner 自己，既有程式排除）。不符即停止。
3. **餘額以執行前基準核對**：記錄 Owner 執行前餘額 *B*。D 結算後應為 *B*+1；交換後應為 *B*。**不假設 *B* 為 0，不調整舊餘額。**

### 20.3 同一使用者的交換去重與處理中防重入

**既有保障（直接沿用）**：

- 同一 request key 的重放：`_replayed_outcome` 以 ledger 冪等鍵找回原預留，不重複扣點。
- 派送狀態的獨占認領：`claim_reservation` 只允許 `reserved → sending` 一次；重複請求不會再送 DM。
- 結果未知：`sending` 逾時後為 `uncertain`，沿用既有核對流程，不退款、不重送。
- 退款只限證明未送出的情況；`delivered` 與 `failed`（已退款）不阻擋新交換。

**本次補的實際缺口**：

- 同一使用者只要有 `reserved`、`sending` 或 `uncertain` 的預留，**新的交換請求一律回傳 `IN_PROGRESS`**，不扣點、不預留、不送 DM，即使使用不同 interaction ID。檢查在寫入交易的全域 gate 內進行，因此不會與另一個交易交錯。
- 既有的 stranded 保護保留在前：舊的、未認領的預留（且補償表存在）仍回傳 `PENDING_REVIEW`，維持 Owner 處理流程。
- `IN_PROGRESS` 的回覆為「仍在處理中或結果待核對，請稍候再試」，不附連結、不送 DM、不退款。
- 正常完成後（`delivered`），使用者可依餘額再次交換，不新增每日或 DM 額度。

**已修改的既有測試（契約變更，須審閱）**：

- `test_exchange_reservation.py`：兩個測試在第二次交換前，把第一筆標為 `delivered`（代表完成交付）；斷言不變。
- `test_reserved_protection.py`：較新或剛好在門檻的預留，斷言由「不阻擋」改為 `IN_PROGRESS`；`sending` 由「不阻擋」改為 `IN_PROGRESS`；`delivered` 與已退款的 `failed` 仍不阻擋。

### 20.4 版本確認與指定結算（owner CLI，薄包裝既有服務）

- `version preview|write`：呼叫既有 `OwnerReviewService.preview_version_confirmation` 與 `confirm`。必要參數為 operation、expected revision、版本參考、版本類別、可追溯來源與理由；write 必須帶 preview 印出的摘要。核准與版本確認是**兩個獨立步驟**。
- `settle preview|write --operation <id> [--operation <id>...]`：只接受明確、不重複的 operation 清單；write 前要求開關為 0；開關只在 `settle()` 呼叫期間為 1；無論成功或失敗都會恢復，並以**獨立讀取**確認恢復後的值。恢復失敗會在單獨一行明確回報並以非零結束，不會隱藏。
- 兩者都沿用可信 Owner（`DISCORD_OWNER_USER_ID`）、摘要綁定與既有服務的 revision、證據與衝突檢查。

### 20.5 安全啟動器與合成 C 投稿（本機，不提交）

- `.local/live-run/start_bot.py`：以隱藏輸入取得 Discord token 與 DB 密碼，只在程序內設定環境變數，然後呼叫既有的 `bot.__main__.main()`。不讀 `.env`、不解密、不寫入秘密、不同步遠端指令。`JUKEBOX_FORMAL_EXCHANGE=1` 只在本程序內生效。
- `.local/live-run/synthetic_intake.py`：以隱藏輸入取得 DB 密碼，經既有 `SubmissionRepository.accept_pending` 投稿合成 C；使用者列由既有 `_ensure_user` 建立，**不直接 INSERT、不依賴未提交的測試腳本**。合成投稿者 ID 須由 Owner 批准。
- 兩者都只做語法檢查（編譯），**未執行**。

### 20.6 遠端指令（只讀比對）

- 新增 `remote_command_report`：比對遠端既有指令的名稱與選項。批准值取自本地樹（`approved_option_names`，離線建立，不登入），回報缺少、未批准與選項不同的項目，**不寫入**。另有 `read_only_remote_report`，只呼叫 `fetch_commands`，不呼叫任何同步。
- 既有 `require_empty_remote_guild` 在遠端已有指令時仍停止、不覆蓋。**本次未查詢真實遠端。** 任何同步或覆蓋仍需另行授權。

### 20.7 離線測試結果（工作樹，本次執行）

- 直接相關的離線測試（交換預留、派送、退款、未知結果、合成路徑、正式入口、保護、補償守衛、owner CLI、結算與版本 CLI、遠端比對、guild 驗收、投稿持久化）：**258 passed，0 failed**（`-m "not mysql"`）。
- 未執行 MySQL 測試，未連 Discord，未操作資料庫或環境，未啟動真人驗收。
- 本次未另外執行「修改前紅燈」的獨立記錄；新測試的斷言已對照舊行為的推論（例如舊程式會對第二筆新金鑰回傳 `RESERVED`）。

### 20.8 真人驗收前仍需批准的具體操作

1. **實機確認**（需另行批准）：交換帳號對 `jb2_control.settlement_enabled` 的 UPDATE 權限（唯讀查詢 `SHOW GRANTS`）；若沒有，停止，不增權。
2. **資料基準**（唯讀）：投稿前的候選池為空；Owner 執行前餘額 *B*；控制列 `snapshot_ready=1`、`legacy_writers_quiesced=1`、`settlement_enabled=0`。
3. **遠端指令**：唯讀查詢 guild 指令；若已有指令，比對後回報，**不同步、不覆蓋**。
4. **C、D 的資料**：全片時長（秒）與人工確認說明；D 的連結人工確認；C 的版本參考、版本類別與來源；合成投稿者 ID。這些不阻擋離線實作，但真人驗收前必須補齊。
5. **執行順序**：依 §20.1 的開關時序與 §20.2 的兩階段檢查；DM 只在 `/exchange` 一次，結果未知即停止。

**本節未驗證**：實機 MySQL 上的 `UPDATE` 權限與影響列數；真實 Discord 的 DM 分類；`/exchange` 在真實流程中的完整端到端行為。

### 20.9 解除路徑的唯讀追查，以及剩餘缺口

**(1) `uncertain` 目前的既有處理**

- 產生：`sending` 逾時由 `recover_interrupted`（每次交換開頭執行）改為 `uncertain`；DM 結果未知或拋出例外時，`record_result` 寫入 `uncertain`。
- 離開 `uncertain`：正式程式中**沒有任何路徑**把它改為其他狀態。`record_result` 只接受 `sending`；`refund_undelivered` 只接受 `failed`；`recover_interrupted` 只把 `sending` 改為 `uncertain`。
- Owner 的事件處理方法（`approve_incident`、`record_not_system_fault`、`settle_incident`、`close_non_fault_refund` 等）存在於 repository，但**正式程式、Discord 與 CLI 都沒有呼叫者**。`approve_incident` 也只接受「舊的未認領 `reserved`」，不適用於 `uncertain`。
- 因此：`uncertain` 的預留目前**沒有既有的解除入口**。依新原則，它會持續阻擋該使用者的新交換，直到另行設計核對與解除規則。本次不自行發明狀態轉移、不退款、不重送、不以時間解鎖。

**(2) 沒有補償表時，舊 `reserved` 的處理入口**

- 判定：`has_stranded_reservation` 與補償 `pending_block` 只在補償表存在或補償開關開啟時啟用，回傳 `PENDING_REVIEW`。
- 無補償表時，舊 `reserved` 現在會阻擋該使用者（回傳 `IN_PROGRESS`），但**沒有可操作的解除入口**：補償的 `approve_incident` 與 `record_not_system_fault` 只在 repository 中，正式程式沒有入口。
- 不因時間到了便視為完成，也不自動續送或退款。

**(3) 同一 request key 重放與不同 request key 的順序**

1. `_replayed_outcome`：以 ledger 冪等鍵找回原預留，回傳 `REPLAYED`，**不做任何寫入**。這一步在新檢查之前執行。
2. 無 ledger 列（新請求）：先經補償的 `PENDING_REVIEW` 檢查（僅補償啟用時），再經 `IN_PROGRESS` 檢查，之後才查餘額與候選並扣點。
3. 重放之後由派送狀態機決定：`reserved` 認領送出一次；`sending`、`uncertain`、`delivered`、`failed` 都不再送出，回傳既有狀態。

因此 `IN_PROGRESS` 不影響重放語意：同一 key 永遠先得到原結果；不同 key 才會在有未完成預留時被拒絕。重放相關測試（`test_the_same_request_key_replays_without_a_second_debit_while_reserved`、`test_retry_after_delivery_replays_without_a_second_message_or_debit`）已通過。

**已知缺口（本輪決定保留，不實作）**：`uncertain` 與無補償表時的舊 `reserved` 沒有解除入口。本輪不新增解除狀態轉移、不接線或啟用補償、不退款、不重送、不以逾時解鎖。這是實作範圍決定，**不代表批准真人驗收、接受真人執行風險或開放 Beta 交換**。

**(4) 剩餘缺口（需要你的決定，本次不自行實作）**

| 缺口 | 影響 | 需要決定 |
| --- | --- | --- |
| `uncertain` 無解除路徑 | 結果未知的使用者會被永久阻擋新交換 | 核對依據（例如 Owner 以可證明的派送事實標記結果）與允許的狀態轉移 |
| 舊 `reserved` 無正式解除入口（無補償時） | 該使用者被阻擋，無可操作的處理 | 是否接線補償的 Owner 流程，或另定規則 |
| 補償的 Owner 方法未接到正式入口 | 補償路徑無法於真人驗收中操作 | 是否在真人前啟用補償（目前關閉） |

**真人驗收的影響**：成功路徑（DM 送達並回報成功）不會產生 `uncertain` 或舊 `reserved`。但正常操作也可能因外部故障（例如 Discord 逾時、連線中斷）進入 `uncertain`。遇到結果未知時**停止**，不重送、不退款、不以時間解鎖；現階段**沒有解除入口**，該使用者在缺口解決前不能再交換，且本次驗收**不包含**解除操作。

### 20.10 本輪補改（離線）

- 遠端報告補上選項比對（見 §20.6）。
- 啟動器與合成投稿的邏輯移入 `bot/live_run_entry.py`，`.local` 腳本只保留隱藏輸入與固定參數。
- 離線測試：`tests/test_live_run_entry.py`（6 項）與 `tests/test_remote_command_report.py`（4 項）及既有 `tests/test_guild_acceptance.py`（58 項），共 **68 項通過**（工作樹，`-m "not mysql"`）。
- 未啟動任何真實程序，未連線 MySQL 或 Discord，未讀取 `.env`，未同步或覆蓋遠端指令。

## 21. 專用隔離 schema 真人驗收結果（2026-10-25，已完成並正常收尾）

**範圍與限定**：本節只記錄 **專用 schema `jukebox101_live_20261025`** 上的真人驗收結果。它**不代表** Beta 交換已開放、補償已啟用、正式環境已驗收，也**不代表**提交快照 `7990f2d` 已獨立完成實機驗證。

**實機使用的版本**：實機步驟使用的是當時工作樹與本機包裝（`.local/live-run/`、`.local/readonly-precheck/`，不提交），其中 `bot/live_run_entry.py` 與 `tests/readonly_precheck.py` 的內容在 `7990f2d` 之後仍有修改。提交前需以本節清單重新核對。

### 21.1 建置

- Schema：`jukebox101_live_20261025`，位於既有隔離 3307 實例（UUID `f138d7c6-cf62-11f1-87be-c025a5a12877`，datadir 為 `.local/mysql-test/20261024-122528/data`）。
- 來源：`bot/database/schema.sql`（SHA-256 `99861a62…`）、`002_submission_economy.sql`（`ad9794a3…`）、`003_exchange_delivery.sql`（`0ffce1bb…`），與既有建置器記錄的雜湊一致，依序套用。
- 表：22 張（基底 6 張＋002 的 16 張），表清單精確相符，沒有補償三表。
- 授權：`jukebox101_test_exchange` 只對新 schema 有 SELECT、INSERT、UPDATE、DELETE；無 GRANT OPTION，無其他變更。migrator 未被授權或使用。
- 控制列：`schema_version=2b2-b1`，`snapshot_ready=1`、`legacy_writers_quiesced=1`（依隔離證據初始化，**不是** Beta 歷史快照證據），`settlement_enabled=0`。
- 空白基準：建置前所有表 0 列；就緒證據記錄於 `.local/live-run-evidence/20261025-01/`。

### 21.2 驗收流程與結果

| 步驟 | 結果 |
| --- | --- |
| 合成 C 投稿（`accept_pending`，合成 ID `9000000000000000001`） | 受理序號 1 |
| Owner `/share` D（操作 `discord:1563787609656926209`） | 受理序號 2；回覆為 pending_validation，本次不發放 |
| C、D 時長（189、176 秒）、核准、版本（basic） | 寫入完成；C、D 的連結為 verified |
| 指定結算（C、D） | 兩者皆 `awarded`；結算開關恢復為 0 |
| 交換前核對 | 候選池僅含 C（D 屬 Owner，依既有規則排除）；Owner 餘額 1；無未完成交換 |
| 交換（一次 `/exchange`） | 預留 1 筆，狀態 `delivered`，Discord 訊息 ID 已記錄，未釋放 |
| DM | 收到一則，文字為「你的交換推薦：https://www.youtube.com/watch?v=cUfDOS2SINM」，對應 C（XG - HYPNOTIZE） |
| 交換後核對 | 帳本共 3 筆：C 的 `submission_award +1`（合成者）、D 的 `submission_award +1`（Owner）、交換的 `exchange_debit −1`（Owner）；Owner 餘額 0；合成者餘額 1；無退款、無 reserved／sending／uncertain |

### 21.3 收尾

- bot 兩次啟動（formal 關閉用於 `/share`，formal 開啟用於 `/exchange`），每次都在終端機隱藏輸入 token，並以 Ctrl+C 正常停止；收尾時沒有殘留 Python 程序。
- 3307 以 `close_isolated.py` 正常 SHUTDOWN：`shutdown_sent: true`、埠關閉、`error.log` 有 `Shutdown complete`、pid 檔已移除。
- 3306（MySQL80）程序未被碰觸。
- 證據位於 `.local/live-run-evidence/20261025-01/`：`pre-db.json`、`post-db.json`、`p5-check.txt`、`post-exchange.json`、`schema-build-resume.json`。不含秘密。
- 資料保留，未清理。

### 21.4 過程紀錄（照實）

- 第一次嘗試在舊 schema `jukebox101_test` 中，C 與 D 因為舊操作（受理序號 1–10）的優先權阻擋而停在 `waiting_predecessor`。依規定停止，未處理舊資料，未發放點數，未交換。
- 新 schema 的建置過程中，有兩次檢查錯誤：第一次是 root 連線未設 DictCursor；第二次是就緒證據的 Python 程序計數把自己的啟動器算為其他寫入者。兩者都是檢查邏輯錯誤，不是資料問題。修正後以 `--resume` 續跑，續跑只確認本次建立的 schema、授權與表清單完全相符，未 DROP、未重建。
- 啟動時 bot 出現 `Guilds intent disabled` 與 `PyNaCl／davey 未安裝` 警告，保留在終端機紀錄，未修改環境。

### 21.5 已知限制與待辦（不因本次驗收而解除）

- **已知缺口**：`uncertain` 與無補償表時的舊 `reserved` 沒有解除入口。本次未出現這兩種狀態，但缺口仍在。
- **補償**：關閉。補償三表不存在於新 schema，本次未測試補償路徑。
- **Beta**：未開放交換。
- **就緒旗標**：新 schema 的 `snapshot_ready` 與 `legacy_writers_quiesced` 依隔離證據設為 1，不等於 Beta 歷史快照證據。
- **未跑**：完整測試套件與 MySQL 測試。本輪只跑了直接相關的離線測試。
- **文案待辦**：使用者回覆中的內部操作編號（例如指令回覆末尾的「操作 1」）應於後續文案修正，本輪不修改。
- **Owner 的 DM 截圖**：本記錄以使用者回報為準，截圖未保存為檔案。
