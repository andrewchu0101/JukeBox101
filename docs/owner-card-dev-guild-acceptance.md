# Owner 案件卡片：Discord 限制核對與開發伺服器驗收清單

狀態：**離線與可丟棄 PostgreSQL 測試已通過，真實 Discord 完全未驗證。** 本文件只列清單；由 Owner 或夥伴在**開發伺服器**執行，不使用正式 Discord、不使用正式資料庫、不啟用發獎。

## 一、平台限制核對（官方文件，2026-10-31 查詢）

| 限制 | 官方數值 | 本實作的最壞情況 | 結論 |
| --- | --- | --- | --- |
| 每則訊息的元件列數 | 舊版訊息最多 5 列 | 主卡片 4 列、其他情況選單 3 列 | 符合；測試 `test_every_card_variant_fits_discord_component_and_message_limits` 檢查 |
| 每列按鈕數 | 5 個 | 最多 5 個（已知版本按鈕） | 符合 |
| 按鈕 `custom_id` | 1–100 字元 | 最壞約 64 字元（`ocard:remaster:` 加 BIGINT 的 19 位受理順序、9 位修訂、19 位選擇） | 符合；測試逐一檢查 ≤ 100 |
| 按鈕標籤 | 80 字元 | 最長「確認為新的錄音版本」等，遠小於 80 | 符合 |
| 訊息內容 | 2000 字元 | 主卡片含 5 個版本與承諾文字；投稿連結超過 300 字元時，改顯示同一支影片的標準連結（`https://www.youtube.com/watch?v=<影片ID>`），不是截斷網址 | 符合；**原先沒有處理，過長的網址會讓訊息超過上限；一度只截斷，截斷後的連結會失效，現已改為標準連結並用 2000+ 字元的連結測試** |
| Modal 標題 | 45 字元 | 最長「重製（同一原始演出）：判斷依據」約 17 字元 | 符合 |
| Modal 元件數 | 1–5 個 | 最多 2 個 | 符合 |
| 文字輸入長度 | 最多 4000 | 最大 400 | 符合 |
| 初始回應時限 | 3 秒，逾時 token 失效 | 按鈕：先 `defer()` 再做服務呼叫；表單按鈕：通過 Owner 檢查後直接開 modal，不做任何資料庫讀取 | 設計符合；**需實測** |
| 互動 token 有效期 | 15 分鐘（用於後續編輯回覆） | 每次按鈕或表單送出都是新的互動、新的 token；一次處理遠小於 15 分鐘 | 符合 |
| Modal 必須是初始回應 | 官方搜尋結果沒有直接確認；依回應類型推論 modal 與 `defer` 互斥 | 表單按鈕只做 Owner 檢查就送出 modal | **需實測** |
| Modal 內文字輸入的包裝 | 官方文件：文字輸入現在應放在 Label 元件內，舊的 ActionRow 包裝已棄用 | 使用 discord.py 2.7.1 的 `TextInput(label=…)` | **需實測 modal 是否正常開啟與送出** |

來源：[Component Reference](https://docs.discord.com/developers/components/reference)、[Using Message Components](https://docs.discord.com/developers/components/using-message-components)、[Using Modal Components](https://docs.discord.com/developers/components/using-modal-components)、[Receiving and Responding to Interactions](https://docs.discord.com/developers/interactions/receiving-and-responding)。文件可能更新，以實測為準。

## 二、最短驗收清單（開發伺服器）

前置：開發應用程式與開發伺服器、可丟棄資料庫（不是正式的）、`DISCORD_OWNER_GUILD_ID` 指向開發伺服器、自動審核與發獎開關維持預設或僅在可丟棄資料庫開啟。**指令同步由 Owner 親自執行**（`python -m bot.command_sync preview`，再依第 4.4 節 `apply` **兩個範圍**：`global` 與 `owner-guild`），本文件不代為操作。準備至少 3 筆案件：一筆歌曲待審核、一筆已核准但版本未確認、一筆缺時長。

| # | 動作 | 預期結果 | 通過 |
| --- | --- | --- | --- |
| 1 | 輸入 `/owner list` | 出現**只有自己看得到**的卡片；`<@…>` 不會通知投稿者；按鈕排成預期的列 | ☐ |
| 2 | 按「確認通過」 | **同一則訊息**被換成結果（不是新訊息）；顯示已確認的狀態、獎勵或版本說明 | ☐ |
| 3 | 對同一張舊卡片再按一次「確認通過」 | 顯示「此案件已變更或已處理…請按重新載入」，沒有再寫入 | ☐ |
| 4 | 快速連按兩次同一個按鈕 | 只產生一個決定；另一次顯示已處理 | ☐ |
| 5 | 按「拒絕」 | **3 秒內**彈出表單；送出後**同一則訊息**被更新 | ☐ |
| 6 | 缺時長的案件按「補時長」，填秒數與來源 | 表單開啟、送出後卡片更新；填「abc」顯示格式錯誤且不寫入 | ☐ |
| 7 | 版本未確認的案件按「確認為新的錄音版本」 | 卡片更新為結果；依開關顯示發獎狀態；資料庫中有一個 `rv-` 開頭的版本 | ☐ |
| 8 | 另一筆不同影片，按「確認為同一錄音 1」 | 併入所選版本；顯示不重複發獎或前序狀態 | ☐ |
| 9 | 建立 6 個以上已知版本後開卡片 | 顯示前 5 個與「更多已知版本」；翻頁只換顯示 | ☐ |
| 10 | 按「其他情況…」 | 顯示類別選單（Live、Cover、Remix、重新錄製、重製：同一錄音 N、返回） | ☐ |
| 11 | 選「Live 現場」，來源填 `1234` | 顯示來源不符合既有規則，沒有建立版本，案件維持待處理 | ☐ |
| 12 | 選「Live 現場」，來源填一個連結 | 建立新版本並記錄類別；卡片更新為結果 | ☐ |
| 13 | 選「重製：同一錄音 N」，填來源 | 併入所選版本，不重複發獎 | ☐ |
| 14 | 用**非 Owner** 帳號按任一按鈕 | 只看到統一的拒絕訊息，沒有任何資料變動 | ☐ |
| 15 | 在**另一個伺服器**使用同一張卡片的按鈕（若可行） | 拒絕 | ☐ |
| 16 | 投稿一支網址超過 300 字元（含長參數）的影片後開卡片 | 卡片仍能顯示，連結改為簡短的標準連結且點開是同一支影片，不出現「內容過長」錯誤 | ☐ |
| 17 | **重啟開發用 Bot**，再按重啟前的舊卡片按鈕 | 顯示「已失效…請按重新載入」，不是靜默失敗；按「重新載入」回到最新案件 | ☐ |
| 18 | 開著表單超過 5 分鐘再送出 | 預期 Discord 顯示失敗或表單逾時；重新按按鈕即可，沒有寫入 | ☐ |
| 19 | 重新整理 Discord 用戶端後回到頻道 | 暫時性（ephemeral）訊息可能消失，屬平台行為；重新輸入 `/owner list` | ☐ |

對照需求情境：一般通過＝2；新版本＝7；同錄音＝8；特殊類別＝10–13；拒絕＝5；補時長＝6；過期＝3、17、18（舊卡片／重啟後／表單逾時）；雙擊＝4。

判定：1–17 全部通過才算卡片可進入下一階段；18、19 是確認平台行為，結果記錄即可。任何一項失敗，請附上畫面與當時時間，我再依 Bot 日誌排查（日誌只含類別名稱與計數，不含秘密）。

## 三、離線已驗證與無法驗證的分界

- 已驗證（離線與可丟棄 PostgreSQL）：權限、舊修訂、雙擊與並行、未知結果重試、版本與類別規則、獎勵不重複、元件與訊息大小。
- **無法離線驗證**：真實的 3 秒回應時限、modal 的開啟與就地更新、重啟後 `DynamicItem` 的實際分派、Discord 對元件排版的實際呈現、暫時性訊息的壽命。這些只能在上表實測。

## 四、最短操作順序、環境與分工（不含任何秘密；全部在開發環境）

### 4.1 需要的環境

| 項目 | 內容 |
| --- | --- |
| 開發 Application 與 Bot | **獨立於正式 Bot** 的 Discord 應用程式；token 只放在本機未提交的秘密檔，不貼進對話、不提交 |
| 開發 Guild | 1 個專用伺服器（步驟 15 若要驗證「另一個伺服器」才需要第 2 個，可略過） |
| 測試帳號 | A＝Owner（`DISCORD_OWNER_USER_ID`）；B＝投稿者（非 Owner，也用於步驟 14）。兩個帳號即可 |
| 資料庫 | 可丟棄的 PostgreSQL（與正式庫完全無關），套用既有三個 migration |
| 設定名稱 | `DISCORD_BOT_TOKEN`、`DISCORD_EXPECTED_APPLICATION_ID`（填**開發**應用程式 ID，作為防呆）、`DISCORD_OWNER_GUILD_ID`＝開發 Guild、`DISCORD_OWNER_USER_ID`＝A、`DISCORD_DEV_GUILD_ID`（如需）、資料庫連線設定；`JUKEBOX_AUTO_REVIEW=off`、`JUKEBOX_FORMAL_EXCHANGE` 維持關閉 |
| 驗證 +1 時 | **只在可丟棄資料庫**：`settlement-switch` 設 on（`python -m bot.owner_cli settlement-switch preview` 再 `write`）且 `JUKEBOX_AUTO_SETTLEMENT=on`。其餘步驟不開 |

自動審核保持關閉，所以不需要 YouTube API 金鑰；案件靠 B 用 `/share` 投稿，A 在卡片上補時長後處理。

### 4.2 順序與分工

| 順序 | 誰 | 做什麼 | 對應清單項目 |
| --- | --- | --- | --- |
| 0 | **我（授權後）** | 建立可丟棄資料庫、套用 migration、執行資料庫端的離線驗證 | — |
| 1 | **Owner（一次性）** | 建立開發應用程式與 Bot、邀請進開發 Guild、把 token 放進本機秘密檔 | 前置 |
| 2 | **Owner 或夥伴** | 依第 4.4 節預覽並同步**兩個範圍**（開發應用程式的全域指令＋開發 Guild 的 Owner 指令），再啟動開發 Bot | 前置 |
| 3 | B 點擊 | `/share` 投稿 5 支不同影片（其中 1 支網址含超長參數）；再多 6 支以上已知版本可由前幾筆確認後產生 | 16 的準備 |
| 4 | A 點擊 | `/owner list`：確認私密、版面；補時長（6）→ 確認通過（2）→ 新版本（7）→ 另一支同錄音（8）→ 其他情況（10–13）→ 拒絕（5） | 1、2、5–13、16 |
| 5 | A 點擊 | 同一張舊卡片再按（3）；快速連按兩次（4） | 3、4 |
| 6 | B 點擊 | 用 B 帳號按卡片按鈕（14） | 14 |
| 7 | **Owner 或夥伴** | 重啟開發 Bot（17）；開著表單超過 5 分鐘再送出（18）；重整 Discord 用戶端（19） | 17–19 |
| 8 | **我（授權後）** | 唯讀檢查可丟棄資料庫：版本列、獎勵筆數、是否有重複寫入；對照畫面結果 | 7、8、13 的資料確認 |

Owner 的操作只有第 1、2 步（一次性）與第 4 到 7 步的點擊；資料庫與稽核由我處理，避免 Owner 執行零碎指令。第 15 項（另一個伺服器）只有第 2 個 Guild 時才做。

### 4.3 若需要另建開發 Bot／同步測試 Guild：具體操作與影響（**等待授權，尚未執行**）

| 操作 | 影響 |
| --- | --- |
| 在 Discord Developer Portal 建立新的開發應用程式與 Bot（Owner 操作） | 只產生新的 token 與應用程式 ID；不影響正式 Bot |
| 以 `bot` 與 `applications.commands` 範圍邀請至開發 Guild | 只加入開發 Guild |
| `command_sync apply`（`global` 與 `owner-guild` 各一次，見 4.4） | 兩次都只作用於**開發**應用程式：全域指令是該應用程式的，Owner 指令只在開發 Guild；正式指令不會被改動。每次執行前以 `preview` 的 digest 確認 |
| 設 `DISCORD_EXPECTED_APPLICATION_ID` 為開發應用程式 ID | 若誤用正式 token，同步會因 ID 不符而拒絕 |
| 可丟棄資料庫開啟結算（僅驗證 +1） | 只影響可丟棄資料庫；驗收完成即丟棄 |

### 4.4 開發 Guild 如何同時取得一般指令與 Owner 指令（離線核對 `bot/command_sync.py`，未連線）

**先前清單只寫 `apply --target owner-guild` 是錯的：** 該目標只發布 Owner 群組，**不包含 `/share`**。三個目標的實際範圍：

| 目標 | 發布內容 | 作用範圍 | 備註 |
| --- | --- | --- | --- |
| `global` | `balance`、`daily`、`exchange`、`history`、`share`（`JUKEBOX_NICKEL_CLAIM=on` 時再加 `claim`） | 該應用程式的全域指令 | 全域更新可能需要時間才出現在用戶端 |
| `owner-guild` | 只有 `owner`（子指令 `list`、`show`、`duration`、`approve`、`reject`、`confirm_version`、`reexamine`） | 僅 `DISCORD_OWNER_GUILD_ID` | 全域同步不會包含它 |
| `dev-guild`（`--guild-id`） | 與 `global` 相同的指令集，複製為該 Guild 的指令 | 單一 Guild | **不含 Owner 群組** |

兩個結論：
1. **Discord 的同步是整批覆寫（bulk overwrite）。** `dev-guild` 與 `owner-guild` 若指向同一個 Guild，後執行者會覆蓋前者：先 `dev-guild` 再 `owner-guild` 會讓 `/share` 等從該 Guild 消失，反之會讓 `/owner` 消失。**不要對同一個 Guild 同時使用這兩個目標。**
2. **可行的組合：`global`（開發應用程式）＋ `owner-guild`（開發 Guild）。** 兩者作用範圍不同（全域 vs. Guild），互不覆蓋，開發 Guild 因此同時有 `/share` 與 `/owner`。因為使用開發應用程式的 token 與 `DISCORD_EXPECTED_APPLICATION_ID`，正式應用程式的指令不受影響。代價：全域指令出現的時間不受控。以上覆蓋行為是依程式註解載明的 Discord 整批覆寫語意推論，**未實測**。

**離線預覽結果**（以虛構的 ID 執行 `python -m bot.command_sync preview`，未連線、無 token；摘要與 ID 無關，因為 digest 只含指令契約）：

| 項目 | `JUKEBOX_NICKEL_CLAIM` 關 | `JUKEBOX_NICKEL_CLAIM` 開 |
| --- | --- | --- |
| 全域指令 | `balance daily exchange history share` | 加上 `claim` |
| 全域 `local_digest` | `119c5bc1a3b0999c9dca4649704c221fc179d4f5b19b9ca2989abc2dd9f24c86` | `7fae610e58e21dbe041ce75f69ac06dd15f1ec87d6f13bc78828a02009f4d9d5` |
| Owner Guild 指令 | `owner` | `owner` |
| Owner Guild digest | `26a2d81480daffe0a5ff0dc6f9d631a4eefbf5f831c802eb5178ea989f0d4dcf` | 同左 |

驗收建議 `JUKEBOX_NICKEL_CLAIM` 保持關閉（用左欄 digest）。實際同步時 `apply` 的 `--expected-digest` 必須取自**當時**重新執行的 `preview`；這些 digest 只用來事先審查指令集合。`apply` 還需要 `--i-understand-this-calls-discord` 與 `--confirm-overwrite-remote-commands`。

**尚未驗證：** 實際同步後 Discord 的呈現、全域指令出現的延遲、`/share` 在開發應用程式下能否使用。

### 4.5 Owner 一次性操作清單：指令預覽與同步（**尚未執行；未建立開發 Application、未呼叫 apply**）

前提：已有開發 Application（Developer Portal 由 Owner 建立）。以下都在本機終端機、使用**開發**環境變數；token 放在本機未提交的秘密檔，不貼進對話。

| 步驟 | 指令（設定名稱，不含值） | 作用 |
| --- | --- | --- |
| 1 | `python -m bot.command_sync preview`（需要 `DISCORD_OWNER_GUILD_ID`、`DISCORD_OWNER_USER_ID`、`DISCORD_EXPECTED_APPLICATION_ID`） | 離線，不連線。輸出全域指令與 Owner Guild 指令各自的 digest；與 4.4 的數值比對 |
| 2 | `python -m bot.command_sync fetch --target global --remote-file <檔案> --i-understand-this-calls-discord` | 唯讀取得開發 Application 目前的全域指令，用來看差異；不改任何東西 |
| 3 | `python -m bot.command_sync apply --target global --expected-digest <步驟 1 的 local_digest> --i-understand-this-calls-discord --confirm-overwrite-remote-commands` | **覆寫開發 Application 的全域指令**（整批覆寫） |
| 4 | `python -m bot.command_sync apply --target owner-guild --expected-digest <步驟 1 的 owner_guild.digest> --i-understand-this-calls-discord --confirm-overwrite-remote-commands` | 覆寫開發 Guild 的指令集（只含 `/owner`）；先確認該 Guild 沒有手動建立的指令 |

四步都不會連到正式 Application：`DISCORD_EXPECTED_APPLICATION_ID` 與 token 不屬於同一個 Application 時，工具會在讀取或修改之前拒絕。我不會執行步驟 2 到 4。
