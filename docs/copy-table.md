# 正式可達路徑完整文案表

- 性質：**唯讀盤點紀錄**。本文件只描述 2026-10-29 當下工作樹中的程式文字，不代表 Owner 已核准任何句子，也不代表行為已驗證。
- 盤點範圍：`bot/services.py`、`bot/exchange_delivery.py`、`bot/discord_owner_review.py`、`bot/discord_app.py`、`bot/nickel_claim_replies.py`、`bot/gateway_listener.py`、`bot/share_replies.py`。另因正式 `/share` 會呼叫而列入：`bot/urls.py`（URL 驗證錯誤）、`bot/owner_review.py`（Owner 預覽與錯誤原文）、`bot/gateway_service.py`（驗收入口，非正式）、`bot/database/review_projection.py`（案件不存在時的原文）。
- 未讀取任何正式使用者資料、正式 MySQL 或正式 Discord。
- 動態資料以佔位符表示：`<操作編號>`、`<N>`、`<狀態>`、`<網址>`、`<原因>`、`<ID>`、`<週期>`。

## 0. 分類規則

**可見性**

| 標記 | 意思 |
| --- | --- |
| 私人 | `ephemeral` 回覆，只有操作者看得到 |
| 私訊 | Discord DM，只送給交換接收者 |
| 公開 | 指令選單中的指令名稱與說明，所有可見該伺服器的人都能看到 |
| Owner 私人 | 同為 `ephemeral`，但僅 Owner 可觸發，內容含審核理由與證據 |

**敏感資料**：「無」；「技術識別碼」指 `discord:<雪花 ID>`、操作編號；「私人網址」指使用者本人投稿的 YouTube 網址；「推薦連結」指交換推薦的 YouTube 連結；「Owner 理由／證據」指審核理由、證據來源與判定者 ID。

**核准狀態**（依 `docs/tickets/docker-gateway-deployment/05、06、20` 的記載判定）

| 狀態 | 判定依據 |
| --- | --- |
| 已核准 | 僅 `bot/share_replies.py` 的 5 句，註解與 `docs/tickets/.../06` 均寫明為 Owner 逐字核准台詞；但**目前未接線**，見第 1.4 節 |
| 待核准 | 其餘所有文字。`docs/tickets/.../06` 明載「逐句文案在 Owner 核准前不修改」；`05`、`20` 亦標示待 Owner 核准。程式註解的 `reviewed wording` 不等於 Owner 核准紀錄，因此不列為已核准 |
| 提案 | 第 1.3 節的狀態映射句子，由本盤點提出，尚未核准 |

**正式可達判定**：「正式可達」指 `bot/__main__.py` 的正式啟動路徑在預設設定（`JUKEBOX_FORMAL_EXCHANGE` 未設、`/claim` 開關未設）下，且該分支不需要 `synthetic_exchange=True` 才會執行。`__main__.py` 從不傳入 `synthetic_exchange`。正式啟動時 `submission_repository` 必定存在（`__main__.py:119`），因此 `MusicService` 的 `submission_repository is None` 分支對正式入口不可達。

---

## 1. `/share`

### 1.1 URL 驗證錯誤（正式可達；來源 `bot/urls.py`，`bot/discord_app.py` 的 `_service_reply` 原樣回傳）

| ID | 觸發情境 | 檔案:行號 | 完整文案 | 可見性 | 敏感 | 狀態 |
| --- | --- | --- | --- | --- | --- | --- |
| S-01 | 網址為空或超過長度上限 | `bot/urls.py:34`、`:44` | 請提供有效的 YouTube 影片網址。 | 私人 | 無 | 待核准 |
| S-02 | 網址含空白或控制字元 | `bot/urls.py:46` | 網址不得包含空白或控制字元。 | 私人 | 無 | 待核准 |
| S-03 | `urlsplit` 解析失敗 | `bot/urls.py:51` | 網址格式不正確。 | 私人 | 無 | 待核准 |
| S-04 | 含帳密或連接埠，或非 http/https | `bot/urls.py:53` | 請使用不含帳密或連接埠的 YouTube 網址。 | 私人 | 無 | 待核准 |
| S-05 | 影片 ID 不是 11 字元有效格式 | `bot/urls.py:57` | YouTube 影片 ID 必須為 11 個有效字元。 | 私人 | 無 | 待核准 |
| S-06 | `/watch` 的 `v` 參數不唯一 | `bot/urls.py:73` | 網址必須指定唯一的 YouTube 影片。 | 私人 | 無 | 待核准 |
| S-07 | Spotify、播放清單或非 YouTube 網址 | `bot/urls.py:80` | 只支援 YouTube 影片網址，不支援 Spotify 或播放清單網址。 | 私人 | 無 | 待核准 |
| S-08 | 同時有 `v=` 與路徑影片 ID | `bot/urls.py:82` | 網址含有多個影片識別來源。 | 私人 | 無 | 待核准 |

注意：S-07 明確拒絕 Spotify。這與 `docs/private-beta-mvp-specification.md` 的 Spotify 接受要求不一致，已列在第 10 節。

### 1.2 正式收據與服務錯誤（`MusicService._share_pending` 路徑）

| ID | 觸發情境 | 檔案:行號 | 完整文案 | 可見性 | 敏感 | 狀態 |
| --- | --- | --- | --- | --- | --- | --- |
| S-09 | 資料庫啟用檢查未通過（`PersistenceNotReady`） | `bot/services.py:307` | 投稿資料庫尚未通過啟用檢查；本次未受理，請聯絡 Owner。 | 私人 | 無 | 待核准 |
| S-10 | MySQL 錯誤（`pymysql.MySQLError`，經 `_database_operation`） | `bot/services.py:310` | 資料服務暫時無法使用，請稍後再試。 | 私人 | 無 | 待核准 |
| S-11 | 提交結果未知（`CommitOutcomeUnknown`） | `bot/services.py:193` | 投稿提交結果未知；操作 `<操作編號>`。請先用 /history 查詢並聯絡 Owner 核對，勿重新投稿。 | 私人 | 技術識別碼 | 待核准 |
| S-12 | 同一互動重送，取回既有收據（`create_share_event` 為假） | `bot/services.py:195` | 重複操作，已取回原投稿回執；操作 `<操作編號>`。本次沒有新增投稿或發放 Nickel。 | 私人 | 技術識別碼 | 待核准 |
| S-13 | 新投稿已儲存 | `bot/services.py:197` | 已儲存投稿；審核狀態：`<狀態>`（Pending 表示待驗證／審核）；操作 `<操作編號>`。本次不發放 Nickel。 | 私人 | 技術識別碼 | 待核准 |
| S-14 | `operation_id` 為空 | `bot/services.py:188` | 缺少投稿操作識別碼；本次未嘗試寫入。 | 私人 | 無 | **正式不可達**（`discord_app.share` 必定傳入 `discord:<interaction.id>`） |

說明：

- `<操作編號>` 的實際值為 `discord:<互動 ID>`（`bot/discord_app.py:112`），因此使用者看到的是帶 `discord:` 前綴的原始字串。
- S-13 的 `<狀態>` 直接輸出 `ReviewStatus` 的英文程式值（`bot/services.py:196`，`.value`），例如 `pending_validation`，**不是中文**。此為現行文字的問題，見第 10 節。
- 收據狀態在正式路徑的實際可能值：依 `bot/database/submission_repository.py:584-596` 的 `_pending_request`，新投稿的審核快照以 `PENDING_VALIDATION` 建立，因此新投稿收據通常只會是 `pending_validation`。`pending_review`、`approved`、`rejected`、`validation_failed` 目前不會出現在收據中，**除非既有影片的 `accept_submission` 回傳既有審核狀態，此點待查**。
- 收據不會出現「已核准」「已拒絕」「驗證未通過」字樣；這些狀態只會在 `/history` 出現（第 4 節）。

### 1.3 狀態映射（提案，未核准）

依 `bot/review.py` 的 `ReviewStatus` 與 `bot/discord_owner_review.py:48-52` 的既有 Owner 標籤，比對 Lead 提出的映射：

| 程式值 | Lead 提案標籤 | Owner 介面既有標籤（`REVIEW_LABELS`） | 衝突 |
| --- | --- | --- | --- |
| `pending_validation` | 待驗證 | 待驗證 | 無 |
| `pending_review` | 待審核 | 待審核 | 無 |
| `approved` | 已核准 | 已核准 | 無 |
| `rejected` | 已拒絕 | 已拒絕 | 無 |
| `validation_failed` | 驗證未通過 | **驗證失敗** | **不一致**，需 Owner 決定採用哪一個 |

提案句（僅範本，未核准）：

| ID | 狀態 | 提案完整句 | 可見性 | 敏感 | 狀態 |
| --- | --- | --- | --- | --- | --- |
| P-01 | `pending_validation` | 已儲存投稿；目前狀態：待驗證；操作 `<操作編號>`。本次不發放 Nickel。 | 私人 | 技術識別碼 | 提案 |
| P-02 | `pending_review` | 已儲存投稿；目前狀態：待審核；操作 `<操作編號>`。本次不發放 Nickel。 | 私人 | 技術識別碼 | 提案 |
| P-03 | `approved` | 已儲存投稿；目前狀態：已核准；操作 `<操作編號>`。本次不發放 Nickel。 | 私人 | 技術識別碼 | 提案（**不可達於收據**，見上） |
| P-04 | `rejected` | 已儲存投稿；目前狀態：已拒絕；操作 `<操作編號>`。本次不發放 Nickel。 | 私人 | 技術識別碼 | 提案（**不可達於收據**） |
| P-05 | `validation_failed` | 已儲存投稿；目前狀態：驗證未通過；操作 `<操作編號>`。本次不發放 Nickel。 | 私人 | 技術識別碼 | 提案（**不可達於收據**；「未通過」與「失敗」待 Owner 決定） |

提案的硬性限制（Lead 指示）：

- `CommitOutcomeUnknown` 的結果（S-11）**不得**套用「投稿已收到」或任何表示已儲存的句子。現行 S-11 文字已正確避免此問題，後續改寫需保持。
- 「（Pending 表示待驗證／審核）」是內部用語，對使用者不清楚，P-01 至 P-05 已移除此註解，需 Owner 確認。

### 1.4 `/share` 不可達或舊路徑文案

| ID | 位置 | 完整文案 | 不可達原因 | 狀態 |
| --- | --- | --- | --- | --- |
| X-01 | `bot/services.py:174` | 你已投稿過這部影片，本次投稿不會重複發放 Nickel。 | 僅在 `submission_repository is None` 時執行；正式啟動必定有提交儲存庫 | 正式不可達（舊路徑） |
| X-02 | `bot/services.py:176` | 這部影片已存在於目錄，已記錄你的投稿；本次投稿不發放 Nickel。 | 同上 | 正式不可達（舊路徑） |
| X-03 | `bot/services.py:177` | 已記錄投稿，待歌曲資格驗證；目前不發放 Nickel，也不加入交換池。 | 同上 | 正式不可達（舊路徑） |
| X-04 | `bot/share_replies.py:26` | 又是這首？看來你真的很喜歡它！💿 | 已核准台詞，但 `select_share_reply` 的結果只在 `bot/submission_workflow.py` 產生，`services._share_pending` 只讀 `candidate` 與 `create_share_event`，不讀 `result.reply`；全程式碼無其他 `.reply` 消費者 | 已核准；**正式不可達（未接線）** |
| X-05 | `bot/share_replies.py:27` | 有人搶先一步把這首放進點唱機了！💿 | 同 X-04 | 已核准；正式不可達 |
| X-06 | `bot/share_replies.py:28` | 收到收到！不用連按兩次，我聽見了！🎵 | 同 X-04 | 已核准；正式不可達 |
| X-07 | `bot/share_replies.py:29` | 換了張封面，我還是認得這首歌！💿 | 同 X-04 | 已核准；正式不可達 |
| X-08 | `bot/share_replies.py:30` | 這首歌好像有點耳熟……讓我確認一下！🔍 | 同 X-04 | 已核准；正式不可達 |

`docs/tickets/docker-gateway-deployment/06` 明載「是否將 5 句已核准台詞接線到 `/share`：本票不接線，屬行為變更，另案決定」。

---

## 2. `/exchange`

### 2.1 入口路由（`bot/discord_app.py:114-132`）

| ID | 觸發情境 | 檔案:行號 | 完整文案 | 可見性 | 敏感 | 狀態 |
| --- | --- | --- | --- | --- | --- | --- |
| E-01 | 正式開關未開（**預設**）；`/exchange` 固定回覆 | `bot/services.py:205` | 交換功能待歌曲資格驗證與派送恢復流程完成後開放；本次未扣除 Nickel。 | 私人 | 無 | 待核准 |
| E-02 | 正式開關開啟但服務未就緒（`formal_exchange` 直接呼叫時）| `bot/services.py:137` | 交換功能尚未開放。 | 私人 | 無 | **正式不可達**（入口已先檢查 `formal_exchange_enabled`） |

### 2.2 正式交換流程（`JUKEBOX_FORMAL_EXCHANGE` 開啟、提交儲存庫存在）

`bot/exchange_delivery.py` 的 `_delivery_text` 與 `_finish` 產生以下回覆，`<N>` 為推薦紀錄編號，`bot/exchange_delivery.py:241` 的 `code` 為 `操作 <N>`。

| ID | 觸發情境 | 檔案:行號 | 完整文案 | 可見性 | 敏感 | 狀態 |
| --- | --- | --- | --- | --- | --- | --- |
| E-04 | 無可用歌曲（`ExchangeStatus.NO_CANDIDATE`） | `bot/exchange_delivery.py:21` | 目前沒有符合條件的歌曲 | 私人 | 無 | 待核准（程式註解 `reviewed wording`，無 Owner 核准紀錄） |
| E-05 | 額度不足（`INSUFFICIENT_CREDITS`） | `bot/exchange_delivery.py:23` | Nickel 不足，交換一首歌曲需要 1 Nickel。 | 私人 | 無 | 待核准（程式註解 `reviewed wording`） |
| E-06 | 同一請求者已有交換進行中（`IN_PROGRESS`，保留階段） | `bot/exchange_delivery.py:22` | 你的交換仍在處理中或結果待核對，請稍候再試；處理完成前不會重複扣點或派送。 | 私人 | 無 | 待核准 |
| E-07 | 有未確認的保留紀錄阻擋新交換（`PENDING_REVIEW`） | `bot/exchange_delivery.py:20` | 你有一筆交換尚待確認（已保留但尚未派送），目前暫停新的交換。請聯絡 Owner 處理。 | 私人 | 無 | 待核准 |
| E-08 | 結果未知（`CommitOutcomeUnknown` 經 `_run_exchange`） | `bot/services.py:156` | 交換結果未確認，請勿重新執行 /exchange；稍後以 /history 查詢。 | 私人 | 無 | 待核准（**注意**：`/history` 只列投稿，不列交換結果，見第 10 節） |
| E-09 | 資料服務錯誤（`pymysql.MySQLError` 或 `PersistenceNotReady`） | `bot/services.py:159` | 資料服務暫時無法使用，本次交換未完成，請稍後再試。 | 私人 | 無 | 待核准 |
| E-10 | DM 已送出（`DELIVERED`） | `bot/exchange_delivery.py:243` | 已將推薦私訊給你，請查看私訊。操作 `<N>` | 私人 | 技術識別碼 | 待核准 |
| E-11 | DM 確定未送出，已退款（`FAILED` 且退款為 `refunded` 或 `already_released`） | `bot/exchange_delivery.py:246` | 私訊未能送出，已退還 1 Nickel。操作 `<N>` | 私人 | 技術識別碼 | 待核准 |
| E-12 | DM 確定未送出，退款紀錄需人工查核（`contradiction`） | `bot/exchange_delivery.py:248` | 私訊未能送出，此筆紀錄需人工查核，尚未退款。操作 `<N>` | 私人 | 技術識別碼 | 待核准 |
| E-13 | DM 確定未送出，退款待處理（其他退款狀態或退款例外） | `bot/exchange_delivery.py:249` | 私訊未能送出，退款待處理，請稍後查詢。操作 `<N>` | 私人 | 技術識別碼 | 待核准 |
| E-14 | 訂單被 Owner 留待處理（`HELD`，補償事件） | `bot/exchange_delivery.py:251` | 此訂單待 Owner 處理，目前未派送，也未重複扣點；請聯絡 Owner。操作 `<N>` | 私人 | 技術識別碼 | 待核准 |
| E-15 | DM 結果未知（逾時、連線中斷或例外），保留紀錄不自動重送 | `bot/exchange_delivery.py:253` | 私訊送出結果未知，系統已保留此筆紀錄，不會自動重送；扣點與交付待確認。操作 `<N>` | 私人 | 技術識別碼 | 待核准 |
| E-16 | 交付仍在進行中（`sending` 狀態，他方持有） | `bot/exchange_delivery.py:254` | 此交換仍在交付中，請稍後查詢，不會重複送出。操作 `<N>` | 私人 | 技術識別碼 | 待核准 |
| E-17 | DM 本文（送至接收者） | `bot/exchange_delivery.py:157` | 你的交換推薦：`<推薦連結>` | 私訊 | 推薦連結 | 待核准 |

補充：

- 以上 E-10 至 E-16 文案**不含連結**（`exchange_delivery.py:20-22` 與 `:157` 的註解要求）。
- 回覆傳送失敗（`reply_failed=True`）不會改變文案，也不會對使用者顯示任何額外文字（`bot/exchange_delivery.py:264-267`）。
- E-10 的 `已將推薦私訊給你`：DM 送出後才寫入「已送出」狀態，但 DM 內容與回覆的一致性依 `DiscordDirectMessageSender`（`bot/discord_exchange.py`，本次未逐行核對）。

### 2.3 合成驗收路徑（正式不可達，僅 `synthetic_exchange=True` 時）

| ID | 位置 | 完整文案 | 不可達原因 | 狀態 |
| --- | --- | --- | --- | --- |
| X-09 | `bot/services.py:27` | 【合成測試】（前綴） | `__main__.py` 從不傳入 `synthetic_exchange`；只有驗收程式會設為 `True` | 正式不可達 |
| X-10 | `bot/services.py:124` | 交換功能尚未開放。 | `synthetic_exchange_enabled` 為假時拋出，入口已先檢查 | 正式不可達 |

---

## 3. `/balance`

| ID | 觸發情境 | 檔案:行號 | 完整文案 | 可見性 | 敏感 | 狀態 |
| --- | --- | --- | --- | --- | --- |
| B-01 | 查詢成功 | `bot/services.py:245` | 目前可用 Nickel：`<N>`。 | 私人 | 無 | 待核准 |
| B-02 | 資料服務錯誤（`_database_operation` 轉換） | `bot/services.py:310` | 資料服務暫時無法使用，請稍後再試。 | 私人 | 無 | 待核准（同 S-10） |

注意：`/balance` 固定使用 `self.repository`（舊版 `MusicRepository`），而非提交儲存庫。它讀取哪個資料來源（`credit_ledger` 或 `users.credit_balance` 快取）待查。`docs/tickets/docker-gateway-deployment/06` 所列的舊草稿為「目前可用交換點數：N 點。」，目前程式已改為 Nickel，兩者並存的狀態需 Owner 確認。

---

## 4. `/history`（正式路徑：提交儲存庫存在）

| ID | 觸發情境 | 檔案:行號 | 完整文案 | 可見性 | 敏感 | 狀態 |
| --- | --- | --- | --- | --- | --- | --- |
| H-01 | 沒有投稿紀錄 | `bot/services.py:288` | 目前沒有你的投稿紀錄。 | 私人 | 無 | 待核准 |
| H-02 | 有紀錄（標題列） | `bot/services.py:289` | 最近本人投稿與目前審核狀態： | 私人 | 無 | 待核准 |
| H-03 | 每列格式 | `bot/services.py:338` | `<操作編號>` · `<網址>` · `<狀態>` | 私人 | 技術識別碼、私人網址 | 待核准 |
| H-04 | 操作編號無效時的替代文字 | `bot/services.py:337` | 歷史操作 | 私人 | 無 | 待核准 |
| H-05 | 狀態不在允許清單時的替代文字 | `bot/services.py:334` | 狀態待確認 | 私人 | 無 | 待核准 |

說明：

- H-03 的 `<狀態>` 為英文程式值（`pending_validation`、`pending_review`、`approved`、`rejected`、`validation_failed`），**不是中文標籤**。Owner 介面有中文標籤，但 `/history` 未套用。
- H-03 的 `<操作編號>` 保留 `discord:` 前綴。
- 列數上限為 `HISTORY_LIMIT`，且總長不得超過 `MAX_REPLY_LENGTH`（`bot/services.py:292`）。

以下為**正式不可達**（僅在無提交儲存庫時執行，`bot/services.py:266-277`）：

| ID | 位置 | 完整文案 | 狀態 |
| --- | --- | --- | --- |
| X-11 | `bot/services.py:270` | 目前沒有成功取得的推薦紀錄。 | 正式不可達 |
| X-12 | `bot/services.py:271` | 最近成功取得的推薦（UTC）： | 正式不可達 |

---

## 5. `/daily`

| ID | 觸發情境 | 檔案:行號 | 完整文案 | 可見性 | 敏感 | 狀態 |
| --- | --- | --- | --- | --- | --- | --- |
| D-01 | 正式路徑任何 `on`／`off`（提交儲存庫存在，**必定**） | `bot/services.py:255` | 私人 Beta 尚未開放每日推薦；本次未保存偏好，也不會發送 DM。 | 私人 | 無 | 待核准（`docs/tickets/.../05` 建議新說明待 Owner 核准） |
| D-02 | 指令選單說明 | `bot/discord_app.py:183` | 每日推薦尚未開放 | 公開 | 無 | 待核准（`05` 建議改為「查詢或設定每日推薦（尚未開放）」，待 Owner 核准） |
| X-13 | 偏好已保存（只在無提交儲存庫時） | `bot/services.py:258` | 已開啟每日推薦偏好。每日排程尚未啟用，目前不會自動發送 DM。／已關閉每日推薦偏好。每日排程尚未啟用，目前不會自動發送 DM。 | 私人 | 無 | **正式不可達** |
| X-14 | 非 `on`／`off` 的選項（Discord 已限制選項，正常不可達） | `bot/discord_app.py:240` | 每日推薦設定只接受 on 或 off。 | 私人 | 無 | 正式不可達（實務上不會出現） |

---

## 6. `/claim`（旗標關閉時不註冊）

`bot/discord_app.py:184-185`：只有 `nickel_claim_enabled is True` 時才註冊 `/claim`。`bot/__main__.py` 的預設為關閉（`load_nickel_claim_enabled` 未設即 False，`bot/config.py:160-175`）。**因此在預設設定下，`/claim` 不會出現在指令選單，以下文案全部未註冊、不可由使用者觸發。**

| ID | 情境（僅旗標開啟時） | 檔案:行號 | 完整文案 | 可見性 | 敏感 | 狀態 |
| --- | --- | --- | --- | --- | --- | --- |
| C-01 | 指令說明 | `bot/discord_app.py:185` | 領取本週與入門 Nickel | 公開 | 無 | 未註冊（旗標關閉）；待核准 |
| C-02 | 旗標關閉（服務層仍可直接呼叫） | `bot/nickel_claim_replies.py:18` | Nickel 領取功能尚未開放，本次未發放。 | 私人 | 無 | 未註冊；待核准 |
| C-03 | 本週定期額度與入門額度都已領取 | `bot/nickel_claim_replies.py:20` | 本週可領取的 Nickel 都已領取，本次未發放。 | 私人 | 無 | 未註冊；待核准。**注意**：入門額度為一次性，「本週可領取」的說法不完全涵蓋 |
| C-04 | 同一請求鍵重送 | `bot/nickel_claim_replies.py:22` | 此領取請求已處理（週期 `<週期>`），本次未重複發放。 | 私人 | 無 | 未註冊；待核准。`<週期>` 為週一的日期（`YYYY-MM-DD`） |
| C-05 | 成功發放（每行 `<來源>` `<數量>` Nickel，以 `；` 串接） | `bot/nickel_claim_replies.py:25` | 已領取：`<來源>` `<N>` Nickel[；`<來源>` `<N>` Nickel]，共 `<總數>` Nickel。 | 私人 | 無 | 未註冊；待核准 |
| C-06 | 來源標籤（入門） | `bot/nickel_claim_replies.py:9` | 入門額度（`<N>` = 2） | 私人 | 無 | 未註冊；待核准 |
| C-07 | 來源標籤（每週） | `bot/nickel_claim_replies.py:9` | 本週定期額度（`<N>` = 1） | 私人 | 無 | 未註冊；待核准 |

C-05 的完整範例（若兩種額度都成功）：`已領取：入門額度 2 Nickel；本週定期額度 1 Nickel，共 3 Nickel。`

---

## 7. `/owner`（僅私人伺服器註冊；全部為 Owner 私人）

### 7.1 指令說明與參數（公開指令選單）

| ID | 位置 | 文案 | 可見性 | 狀態 |
| --- | --- | --- | --- | --- |
| O-01 | `bot/discord_owner_review.py:363` | 群組說明：Owner 專用案件審核指令 | 公開 | 待核准 |
| O-02 | `:369` | `list`：列出待處理案件 | 公開 | 待核准 |
| O-03 | `:378` | `show`：查看案件審核與證據 | 公開 | 待核准 |
| O-04 | `:389` | `approve`：預覽核准為歌曲（需確認） | 公開 | 待核准 |
| O-05 | `:402` | `reject`：預覽拒絕（需確認） | 公開 | 待核准 |
| O-06 | `:415` | `confirm_version`：預覽確認錄音版本（需確認） | 公開 | 待核准 |
| O-07 | `:437` | `reexamine`：預覽正式重審（需確認） | 公開 | 待核准 |
| O-08 | `:379` `:390` `:416` `:417` `:438` `:439` | 參數說明：案件 ID、證據引用、決定理由、版本參考（新證據時必填）、版本類別、既有證據 ID、新證據來源（可追溯） | 公開 | 待核准 |

### 7.2 權限閘門（每個指令與每個按鈕都套用）

| ID | 觸發情境 | 檔案:行號 | 完整文案 | 可見性 | 敏感 | 狀態 |
| --- | --- | --- | --- | --- | --- | --- |
| O-09 | 非 Owner，或不在指定伺服器（兩者回覆相同） | `bot/discord_owner_review.py:29`、`:107` | 此指令僅限指定私人伺服器中的 Owner 使用。 | Owner 私人 | 無 | 待核准 |

### 7.3 `list`（分頁）

| ID | 觸發情境 | 檔案:行號 | 完整文案 | 可見性 | 敏感 | 狀態 |
| --- | --- | --- | --- | --- | --- | --- |
| O-10 | 沒有待處理案件 | `:725` | 目前沒有待處理案件。 | Owner 私人 | 無 | 待核准 |
| O-11 | 有案件（標題列） | `:727` | 待處理案件： | Owner 私人 | 無 | 待核准 |
| O-12 | 每列格式 | `:776-777` | ・`<操作編號>`｜`<審核狀態標籤>`｜版本 `<版本標籤>`｜修訂 `<N>`｜證據 `<N>` 筆 | Owner 私人 | 技術識別碼 | 待核准 |
| O-13 | 每列有衝突原因時加上 | `:778` | ｜衝突：`<原因>` | Owner 私人 | Owner 理由 | 待核准 |
| O-14 | 分頁指標失效或超出 | `:43` | 此分頁已過期，請重新執行 /owner list。 | Owner 私人 | 無 | 待核准 |
| O-15 | 上一頁按鈕 | `:249` | 上一頁（按鈕標籤） | Owner 私人 | 無 | 待核准 |
| O-16 | 下一頁按鈕 | `:251` | 下一頁（按鈕標籤） | Owner 私人 | 無 | 待核准 |

審核狀態標籤（`REVIEW_LABELS`，`:48-52`）：待驗證、待審核、已核准、已拒絕、驗證失敗。
版本標籤（`IDENTITY_LABELS`，`:54-58`）：未確認、已確認、衝突。

### 7.4 `show`（完整審核依據）

| ID | 觸發情境 | 檔案:行號 | 完整文案（模板） | 可見性 | 敏感 | 狀態 |
| --- | --- | --- | --- | --- | --- | --- |
| O-17 | 每個案件的第一行 | `:787` | 案件 `<操作編號>`｜受理順序 `<N>` | Owner 私人 | 技術識別碼 | 待核准 |
| O-18 | 來源網址 | `:788` | 來源連結：`<網址>`（超過 `MAX_VALUE_CHARS` 會截斷並加「…」） | Owner 私人 | 私人網址 | 待核准 |
| O-19 | 狀態列 | `:789` | 狀態：`<審核狀態標籤>`｜修訂 `<N>` | Owner 私人 | 無 | 待核准 |
| O-20 | 版本狀態 | `:807` | 版本：`<版本標籤>` | Owner 私人 | 無 | 待核准 |
| O-21 | 版本參考（有值時） | `:809` | 版本參考：`<值>` | Owner 私人 | 無 | 待核准 |
| O-22 | 版本證據（有值時） | `:811` | 版本證據：`<證據參考>` | Owner 私人 | Owner 證據 | 待核准 |
| O-23 | 版本衝突（狀態為衝突時） | `bot/owner_review.py:37` | 版本衝突：維持凍結，此次決定不會清除衝突。 | Owner 私人 | 無 | 待核准 |
| O-24 | 衝突原因（有值時） | `:815` | 衝突原因：`<原因>` | Owner 私人 | Owner 理由 | 待核准 |
| O-25 | 證據數量標題 | `:791` | 證據 `<N>` 筆： | Owner 私人 | 無 | 待核准 |
| O-26 | 證據四行（每筆） | `:829-832` | ・證據 `<ID>`｜來源類型 `<英文 enum 名稱>`；來源參考：`<值>`｜取得時間：`<ISO>`；判定者：`<ID>`｜理由：`<理由>`；聲稱版本：`<值或無>`｜版本類別：`<英文 enum 名稱或無>` | Owner 私人 | Owner 證據、判定者 ID | 待核准（**注意**：來源類型與版本類別以英文 enum 名稱顯示） |
| O-27 | 內容判定標題 | `:793` | 內容判定 `<N>` 筆： | Owner 私人 | 無 | 待核准 |
| O-28 | 內容判定三行（每筆） | `:846-848` | ・判定：歌曲／非歌曲｜影片 `<影片 ID>`；判定者：`<ID>`｜取得時間：`<ISO>`；證據參考：`<值>`｜理由：`<理由>` | Owner 私人 | Owner 理由 | 待核准 |
| O-29 | 決定歷程標題 | `:795` | 決定歷程 `<N>` 筆： | Owner 私人 | 無 | 待核准 |
| O-30 | 決定歷程每列 | `:858` | ・修訂 `<N>`｜`<審核狀態標籤>`｜判定者 `<ID>`｜理由：`<理由>` | Owner 私人 | Owner 理由 | 待核准 |

### 7.5 預覽（`approve`、`reject`、`confirm_version`、`reexamine`）

| ID | 觸發情境 | 檔案:行號 | 完整文案（模板） | 可見性 | 敏感 | 狀態 |
| --- | --- | --- | --- | --- | --- | --- |
| O-31 | 預覽首行 | `:868` | 請確認：`<動作標籤>`，案件 `<操作編號>` | Owner 私人 | 技術識別碼 | 待核准 |
| O-32 | 理由行 | `:869` | 理由：`<理由>` | Owner 私人 | Owner 理由 | 待核准 |
| O-33 | 證據引用行（核准／拒絕，有值時） | `:872` | 證據引用：`<值>` | Owner 私人 | Owner 證據 | 待核准 |
| O-34 | 來源行（版本確認／重審，有值時） | `:874` | 來源：`<值>` | Owner 私人 | Owner 證據 | 待核准 |
| O-35 | 狀態行 | `:876` | 目前狀態：`<標籤>`，預計變為：`<標籤>` | Owner 私人 | 無 | 待核准 |
| O-36 | 版本行（無版本資料） | `:892` | 版本：未提供 | Owner 私人 | 無 | 待核准 |
| O-37 | 版本行（確認後重算） | `:895` | 版本：目前 `<版本狀態>`（`<版本參考>`）；確認後依全部可靠證據重算 | Owner 私人 | 無 | 待核准 |
| O-38 | 版本行（有預計結果） | `:896` | 版本：目前 `<before>` → 預計 `<after>` | Owner 私人 | 無 | 待核准 |
| O-39 | 修訂行 | `:878` | 修訂：`<N>` | Owner 私人 | 無 | 待核准 |
| O-40 | 財務效果（所有預覽共同） | `bot/owner_review.py:33` | 不發 Nickel，不寫入 credit_ledger，點數餘額不變。 | Owner 私人 | 無 | 待核准（注意：內文用「點數」而非「Nickel」，與 06 草案衝突） |
| O-41 | 未確認版本（附加於 O-40） | `bot/owner_review.py:36` | 版本未確認：此次決定不會確認錄音版本。 | Owner 私人 | 無 | 待核准 |
| O-42 | 版本衝突（附加於 O-40，見 O-23） | `bot/owner_review.py:37` | 版本衝突：維持凍結，此次決定不會清除衝突。 | Owner 私人 | 無 | 待核准 |
| O-43 | 重審效果（重審預覽） | `bot/owner_review.py:34` | 重審會保留原決策與歷史；重審通過核准時，建立新的排序事件並排到當時隊尾，不恢復原優先權，也不保證發點。不發 Nickel，不寫入 credit_ledger，點數餘額不變。 | Owner 私人 | 無 | 待核准 |
| O-44 | 重審通過核准的附註（只在核准重審時） | `bot/owner_review.py:32` | 重審通過：確認時產生新排序事件，排到當時隊尾；原受理序號與歷史保留。 | Owner 私人 | 無 | 待核准 |
| O-45 | 版本確認效果（版本確認預覽） | `bot/owner_review.py:35` | 版本確認不重開審核，不改變受理順序。不發 Nickel，不寫入 credit_ledger，點數餘額不變。（若有衝突，附加 O-42） | Owner 私人 | 無 | 待核准 |
| O-46 | 預覽有效期與一次性 | `:880` | 預覽有效至 `<ISO 時間>`；確認只能使用一次。 | Owner 私人 | 無 | 待核准 |
| O-47 | 動作標籤 | `:59-64` | 核准為歌曲、拒絕、重審、確認版本 | Owner 私人 | 無 | 待核准 |

**按鈕**（`:144`、`:146`）：確認（危險樣式）、取消（次要樣式）。

### 7.6 按鈕回覆（確認、取消）

| ID | 狀態 | 檔案:行號 | 完整文案 | 可見性 | 敏感 | 狀態 |
| --- | --- | --- | --- | --- | --- |
| O-48 | 取消成功（`OPEN` → `CANCELLED`） | `:182` | 已取消，此預覽不會執行。 | Owner 私人 | 無 | 待核准 |
| O-49 | 取消被拒：確認已開始（`IN_FLIGHT`） | `:191-192` | 案件 `<操作編號>` 的確認已開始，結果尚未確定，無法取消。請重新按「確認」，以同一預覽與同一決定鍵取得結果；或使用 /owner show 查看此案件。 | Owner 私人 | 技術識別碼 | 待核准 |
| O-50 | 取消被拒：結果未知（`UNKNOWN`） | `:194` | 案件 `<操作編號>` 的結果未知，無法取消，驗證資訊已保留。請重新按「確認」以取得原結果。 | Owner 私人 | 技術識別碼 | 待核准 |
| O-51 | 取消被拒：已確認（`DONE`） | `:196` | 此預覽已確認執行，無法取消。 | Owner 私人 | 無 | 待核准 |
| O-52 | 重複取消（`CANCELLED`） | `:197` | 此預覽已取消。 | Owner 私人 | 無 | 待核准 |
| O-53 | 取消後再按確認 | `:44`、`:207` | 此預覽已取消，請重新預覽。 | Owner 私人 | 無 | 待核准 |
| O-54 | 確認成功 | `:217`（`_confirmed_text`，`:913`） | 已確認：目前狀態 `<審核狀態標籤>`，修訂 `<N>`。 | Owner 私人 | 無 | 待核准 |
| O-55 | 確認後重按（`DONE`，重播原結果） | `:205` | 同 O-54 | Owner 私人 | 無 | 待核准 |

### 7.7 錯誤文案（`_guarded`、`_error_text`）

| ID | 觸發情境 | 檔案:行號 | 完整文案 | 可見性 | 敏感 | 狀態 |
| --- | --- | --- | --- | --- | --- | --- |
| O-56 | 未預期例外（一般 `Exception`），或 `KeyError` | `:41`、`:570-577` | 系統內部錯誤，操作未完成。請先使用 /owner show 確認案件狀態，再決定是否重試；若持續發生，請回報開發者。 | Owner 私人 | 無 | 待核准 |
| O-57 | 預覽已過期（`ExpiredReviewToken`） | `:587` | 預覽已過期，請重新預覽。 | Owner 私人 | 無 | 待核准 |
| O-58 | 結果未知（`ReviewOutcomeUnknown`） | `:589` | 結果未知，請重新確認同一筆預覽以取得原結果。 | Owner 私人 | 無 | 待核准 |
| O-59 | 確認憑證無效（`UnknownReviewToken`） | `:591` | 確認憑證無效，請重新預覽。 | Owner 私人 | 無 | 待核准 |
| O-60 | 案件已變更（`StaleReviewVersion`） | `:593` | 案件已變更（審核版本、身分、證據或版本對應），預覽已失效，請重新預覽。 | Owner 私人 | 無 | 待核准 |
| O-61 | 找不到案件（非 `KeyError` 的 `LookupError`） | `:595` | 找不到指定案件。 | Owner 私人 | 無 | 待核准（**觸發情境不明**；案件不存在的實際路徑見 EN-14） |
| O-62 | 預覽已發出但無法渲染（不提供確認按鈕） | `:42` | 預覽內容無法顯示，未建立確認按鈕，此次決定未送出。請回報開發者。 | Owner 私人 | 無 | 待核准 |
| O-63 | 證據引用同時填或都沒填 | `:45` | evidence_id 與 source 必須二擇一：既有證據填 evidence_id，新證據填 source。 | Owner 私人 | 無 | 待核准 |
| O-64 | 新證據沒有版本參考 | `bot/discord_owner_review.py:620` | 新證據需要提供版本參考 version_name。 | Owner 私人 | 無 | 待核准 |
| O-65 | 版本類別不在清單內（選項限制下正常不可達） | `:631` | 未知的版本類別：`<值>` | Owner 私人 | 無 | 正式不可達（Discord 選項已限制） |

### 7.8 **以英文原文直接呈現給 Owner 的錯誤**（`_error_text` 回傳 `str(error)`，`:596`）

這些句子不是中文，而且沒有經過 `_error_text` 的中文化。全部為現行程式原文，**狀態均為待核准，且屬現行問題**。

| ID | 觸發情境 | 檔案:行號 | 原文 | 狀態 |
| --- | --- | --- | --- | --- |
| EN-01 | 來源引用不是未補空白的文字 | `bot/owner_review.py:92` | A source reference must be unpadded text | 待核准／需中文化 |
| EN-02 | 來源引用長度不在 8 至 512 字 | `:94` | A source reference must be 8 to 512 characters | 同上 |
| EN-03 | 來源引用是純數字（Discord 雪花） | `:96` | A bare numeric id is not a traceable source | 同上 |
| EN-04 | 來源引用是互動 ID 格式 | `:98` | An interaction-style id is not a traceable source | 同上 |
| EN-05 | 重審預覽引用的不是完整證據紀錄 | `:167` | Reexamination previews must cite a full evidence record | 同上 |
| EN-06 | 版本確認預覽引用的不是完整證據紀錄 | `:169` | Version confirmations must cite a full evidence record | 同上 |
| EN-07 | 只有核准可以引用內容判定 | `:171` | Only an approval may cite a content judgement | 同上 |
| EN-08 | 核准缺少 Owner 內容判定 | `:331` | Approval requires the Owner's content judgement | 同上 |
| EN-09 | 內容判定不是 Owner 本人判定 | `:333` | Content judgement must be the Owner's own verdict | 同上 |
| EN-10 | 版本衝突時禁止核准 | `:335` | Version conflict forbids approval; rejection stays allowed and does not clear the conflict | 同上 |
| EN-11 | 歌曲閘門拒絕核准（`{gate.reason}` 另行附加） | `:338` | Approval refused by the song gate: `<reason>` | 同上（`<reason>` 的可能值待查） |
| EN-12 | 重審需要可靠的 Owner 確認證據 | `:277` | Reexamination requires reliable Owner-confirmed evidence | 同上 |
| EN-13 | 確認時缺少預覽所讀取的快照 | `:381` | Every Owner confirmation must carry the snapshot its preview read | 同上（內部不變式，正常不可達） |
| EN-14 | 案件不存在（`review_projection` 的 `ValueError`） | `bot/database/review_projection.py:58` | Unknown review operation | **重要**：`/owner show`、`approve` 等對不存在案件的查詢，會顯示此英文原文，而非 O-61 的「找不到指定案件。」 |
| EN-15 | 操作編號或理由為空白 | `bot/version_identity.py:18` | A nonempty reference without surrounding whitespace is required | 同上（觸發條件待查） |
| EN-16 | 理由超過 1024 字 | `bot/database/review_persistence.py:132`、`bot/evidence_record.py:114` | Audit reason is too long | 同上（觸發點待查） |
| EN-17 | 決定識別碼超過儲存上限 | `bot/database/review_persistence.py:118`、`:62`；`bot/database/submission_repository.py:542` | Decision identifiers exceed storage limits | 同上（正常不可達） |
| EN-18 | 非 Owner（閘門已先擋，正常不可達） | `bot/database/review_persistence.py:129` | Owner identity is required | 同上 |

**待查**：EN-14 的 `ValueError` 是否在 `review_projection` 之前就被其他檢查攔截，需以實際測試確認。

### 7.9 其他 Owner 介面文字

| ID | 位置 | 文案 | 狀態 |
| --- | --- | --- | --- |
| O-66 | `:725` | 目前沒有待處理案件。 | 待核准（同 O-10） |
| O-67 | `bot/discord_owner_review.py:741` | `split_messages` 單行超過上限時拋出 "An audit line exceeds the message limit"（英文，經 `_guarded` 原樣呈現） | 正常不可達（`_clip` 已限制單行長度） |

---

## 8. 指令層通用文案（`bot/discord_app.py`）

| ID | 觸發情境 | 檔案:行號 | 完整文案 | 可見性 | 敏感 | 狀態 |
| --- | --- | --- | --- | --- | --- | --- |
| G-01 | 任何指令的未預期錯誤，且尚未回覆 | `:56` | 指令暫時無法完成，請稍後再試。 | 私人 | 無 | 待核准 |
| G-02 | 已回覆後的未預期錯誤 | `:58` | 指令暫時無法完成，請稍後再試。 | 私人 | 無 | 待核准 |
| G-03 | `/share` 指令說明 | `:179` | 投稿 YouTube 影片供資格驗證 | 公開 | 無 | 待核准 |
| G-04 | `/exchange` 指令說明 | `:180` | 查詢交換功能開放狀態 | 公開 | 無 | 待核准（說明與實際行為「交換」不一致，見第 10 節） |
| G-05 | `/balance` 指令說明 | `:181` | 查詢可用 Nickel | 公開 | 無 | 待核准 |
| G-06 | `/history` 指令說明 | `:182` | 查詢本人投稿及目前審核狀態 | 公開 | 無 | 待核准 |
| G-07 | 所有回覆的 mention 政策 | — | 不提及任何人（`AllowedMentions.none()`） | — | — | 程式設定 |

注意：`bot/discord_app.py:112`、`:132`、`:140`、`:148`、`:156`、`:169` 的回覆全部為 `ephemeral=True`（`_respond`，`:216`）。**正式路徑中沒有公開回覆。**

---

## 9. 驗收入口（非正式；`bot/gateway_listener.py`、`bot/gateway_service.py`）

以下文字只在 `build_gateway_client`（開發用 Discord 伺服器，DB 停用）下可見，**不屬於正式可達路徑**，列出供核對。

| ID | 位置 | 完整文案 | 可見性 | 狀態 |
| --- | --- | --- | --- | --- |
| V-01 | `bot/gateway_listener.py:55` | 此入口只接受指定私人伺服器與 Application 的指令。 | 私人 | 驗收專用；待核准 |
| V-02 | `bot/gateway_service.py:23` | Development / DB-disabled：網址格式有效；沒有儲存歌曲或投稿，也沒有發放點數。 | 私人 | 驗收專用 |
| V-03 | `:31` | Development / DB-disabled：交換功能尚未啟用；未扣除 Nickel，也未建立推薦紀錄。 | 私人 | 驗收專用 |
| V-04 | `:39` | Development / DB-disabled：資料庫停用，無法查詢餘額。 | 私人 | 驗收專用 |
| V-05 | `:47` | Development / DB-disabled：資料庫停用，無法查詢歷史紀錄。 | 私人 | 驗收專用 |
| V-06 | `:57` | Development / DB-disabled：收到 `<on／off>`，但設定未保存；不啟動排程，也不發送 DM。 | 私人 | 驗收專用 |

`MODE_LABEL` 為 `Development / DB-disabled`（`bot/gateway_service.py:9`）。

---

## 10. 待 Owner 決定與發現的問題

| # | 項目 | 說明 | 阻擋哪個階段 |
| --- | --- | --- | --- |
| 1 | 審核狀態中文標籤 | `validation_failed` 在 Lead 映射為「驗證未通過」，Owner 介面既有為「驗證失敗」。需決定統一用哪一個 | 接線 `/share` 收據前 |
| 2 | 收據內的狀態顯示 | 現行 S-13 直接輸出英文程式值（`pending_validation`），應改為中文標籤 | 上線前 |
| 3 | 收據內的「（Pending 表示待驗證／審核）」 | 內部用語，對使用者不清楚 | 上線前 |
| 4 | `/history` 的狀態顯示 | H-03 仍顯示英文程式值，與 Owner 介面中文不一致 | 上線前 |
| 5 | `/history` 的操作編號 | 保留 `discord:` 前綴，應改為使用者可讀的編號或隱藏 | 上線前 |
| 6 | `/exchange` 結果未知的指引 | E-08 指引使用者以 `/history` 查詢，但 `/history` 只列投稿，不列交換結果 | 上線前 |
| 7 | `/exchange` 指令說明 | G-04「查詢交換功能開放狀態」與實際「交換」行為不一致（`docs/tickets/.../05` 同類問題） | 上線前 |
| 8 | Spotify 投稿 | S-07 拒絕 Spotify，與 `docs/private-beta-mvp-specification.md` 要求的「接受並 pending」不一致。此為產品決策，本盤點不判定對錯 | 產品決策 |
| 9 | 5 句已核准台詞 | 已核准但未接線（X-04 至 X-08）。是否接線至 `/share` 另案決定（`docs/tickets/.../06`） | 產品決策 |
| 10 | 「點數」與「Nickel」混用 | O-40 至 O-45 仍使用「點數餘額」；`docs/tickets/.../06` 要求統一用 Nickel。文案統一需 Owner 逐句核准 | 逐句核准前 |
| 11 | 英文原文外露 | EN-01 至 EN-18 直接顯示給 Owner，需逐句中文化並核准 | 上線前 |
| 12 | 「本週可領取」 | C-03 對入門額度（一次性）用詞不完全精確；但 `/claim` 目前未註冊 | 啟用 `/claim` 前 |
| 13 | `/daily` 文案 | D-01、D-02 等待 Owner 核准（`docs/tickets/.../05` 建議文字） | 上線前 |
| 14 | `/balance` 來源 | B-01 讀取舊版 `MusicRepository`，來源與「點數」「Nickel」用詞需確認 | 上線前 |
| 15 | 「（待查）」項目 | 收據在既有影片重複投稿時的狀態（第 1.2 節）；EN-14 的觸發路徑；EN-16 的觸發點；O-61 的觸發來源 | 上線前核對 |
| 16 | 無「已收到」字樣 | 任何結果不明情境不得使用「已收到」或「已儲存」，目前 S-11 符合 | 持續遵守 |

---

## 11. 統計

依第 0 節的規則，對全文 163 列（每列為一句或一組分支模板）以狀態欄自動分類：

| 分類 | 列數 | 說明 |
| --- | --- | --- |
| 正式可達、待核准 | 124 | 含 `/share`、`/exchange`、`/balance`、`/history`、`/daily`、`/owner` 與指令層文字 |
| 提案 | 2 | P-01、P-02（狀態映射範本，未核准） |
| 未註冊（`/claim` 旗標關閉） | 7 | C-01 至 C-07 |
| 不可達（舊路徑、未接線、驗收專用、正式入口不會觸發） | 30 | 含已核准但未接線的 5 句（X-04 至 X-08）與 P-03 至 P-05 |

- 英文原文外露給 Owner：18 列（EN-01 至 EN-18）。
- 已核准：5 句（`bot/share_replies.py`），目前未接線。其餘全部待 Owner 逐句核准。

## 12. 未完成事項

- 本盤點為靜態讀取，**未在執行環境中觸發任何指令**。觸發條件依程式碼推論，標示「待查」者需以測試或實機確認。
- 未讀取 `bot/discord_exchange.py` 的 DM 送出細節（僅依 `bot/exchange_delivery.py` 的呼叫確認文字）。
- `docs/tickets/`、`docs/exchange-synthetic-acceptance.md` 中的同句字樣，本次只確認了存在，未逐句比對其核准性質。
