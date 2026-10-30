# REST 429 整合核對（工單 26 後續）

日期：2026-10-29　負責人：Teammate `rest-429-review-dev`　範圍：`bot/discord_http.py`、`tests/test_discord_rest_retry.py`

## 結論摘要

| # | 項目 | 結論 | 證據 |
| --- | --- | --- | --- |
| 1 | 不與 discord.py 重試疊加 | 通過。`DiscordRestClient` 直接使用 `aiohttp`，不經過 discord.py 的 HTTP 客戶端 | 見下方第 1 節 |
| 2 | 非冪等操作的重送 | 保留 429 重送；理由與限制見第 2 節 | 見第 2 節 |
| 3 | 最壞總耗時 | 原實作可達約 40 秒，超過 DM 的 20 秒期限；已加入整體預算 15 秒 | 測試 `test_slow_attempts_and_waits_share_one_overall_budget...` |
| 4 | Retry-After 超期即停 | 通過。超過單次上限或超過剩餘預算時立即失敗，不睡眠、不縮短等待 | 測試 `test_a_wait_longer_than_the_budget...`、`test_a_retry_after_that_would_end_past_the_overall_budget...` |
| 5 | 工作樹原有變更保留 | 通過。`RestDirectMessageSender`、`RestEphemeralReplier`、`verify_interaction_signature` 與候選版逐字相同 | 見第 5 節 |

## 1. 與 discord.py 的重試路徑

- `bot/discord_http.py` 只 import `aiohttp`，沒有 import `discord`，也不使用 `discord.http.HTTPClient`。
- discord.py 的內建 HTTP 客戶端只在 `bot/discord_app.py` 的 `JukeBoxClient`（閘道模式，`bot/__main__.py` 與 `bot/gateway_listener.py` 使用）中出現。
- `DiscordRestClient` 只在 `bot/http_interactions.py` 的 `_open_resources` 建立，由 `bot/http_app.py` 啟動的獨立 aiohttp Web 應用程式使用，自有 `aiohttp.ClientSession`。
- 兩者不在同一條 HTTP 路徑上，因此沒有 429 的雙重重試。aiohttp 本身不做自動重試。

## 2. 各方法的 HTTP 方法與重送判斷

| 方法 | HTTP | 冪等性 | 429 時是否重送 |
| --- | --- | --- | --- |
| `create_dm_channel` | `POST /users/@me/channels` | 實務上冪等：回傳既有 DM 頻道，不發送訊息 | 可重送（呼叫端明確宣告 `retry_on_rate_limit=True`） |
| `create_message` | `POST /channels/{id}/messages` | 非冪等：每次成功都會建立一則訊息 | **不重送**（明確宣告 `retry_on_rate_limit=False`；429 直接回報） |
| `edit_original_response` | `PATCH /webhooks/{app}/{token}/messages/@original` | 冪等：以相同內容取代原始回應 | 可重送（明確宣告 `retry_on_rate_limit=True`） |

判斷依據（2026-10-29 Lead 決定，取代先前「POST 429 一律重送」的判斷）：

- 重送是否安全必須由端點明確宣告，不由 HTTP 方法推定。每個端點在程式中以 `retry_on_rate_limit` 關鍵字參數明確標註，沒有預設值。
- `create_message` 不核准 429 重送：Discord 限流被視為「未處理」的前提未在真實 Discord 驗證，而訊息建立一旦被接受就無法安全地再送一次。429 會以 `discord_429` 回報，DM 層歸類為結果不明。
- 傳輸中斷、逾時與結果不明一律不重送，包含已宣告可重送的端點（測試 `test_a_timeout_is_never_retried_even_for_an_endpoint_that_allows_retry`）。
- 取消訊號在重送等待中會立即中止呼叫（測試 `test_cancelling_during_a_retry_wait_stops_the_call_without_another_attempt`）。
- 非 429 的錯誤（4xx 其他狀態、5xx、逾時、傳輸錯誤）一律不重送，維持原行為。
- DM 層 `RestDirectMessageSender.send` 不做任何重送：頻道查詢失敗歸類為 `not_sent`，訊息送出失敗歸類為 `unknown`。

## 3. 總耗時與整體期限

修正前：

- 每次嘗試最長 `REST_TIMEOUT_SECONDS` = 10 秒，最多 3 次。
- 每次等待最長 `MAX_RETRY_WAIT_SECONDS` = 5 秒，最多 2 次。
- 最壞總耗時 = 3 × 10 + 2 × 5 = 40 秒，超過 `DM_TIMEOUT_SECONDS` = 20 秒。
- `bot/exchange_delivery.py` 的 `_attempt` 以 `asyncio.wait_for` 強制 20 秒截止，超時後歸類為 `unknown("timeout")`。也就是說，即使 Discord 已明確以 429 拒絕，只要等待超過 20 秒，結果仍被記為「不確定」。

修正後：

- 新增 `REST_RETRY_BUDGET_SECONDS` = 15.0，為**整個 REST 呼叫**（所有嘗試與等待的加總）的期限，並小於 `DM_TIMEOUT_SECONDS`，讓 REST 層先於 DM 層結束。
- 每次嘗試的逾時為 `min(REST_TIMEOUT_SECONDS, 剩餘預算)`，因此最後一次嘗試不會超出預算。
- 剩餘預算 ≤ 0 時不送出請求（aiohttp 會把 `total=0` 視為不設逾時，必須避免）。
- 最壞總耗時 ≤ 15 秒，距離 DM 的 20 秒截止仍有 5 秒餘裕。

`bot/exchange_delivery.py` 未修改。預算放在 REST 層即可，不需要 DM 層傳遞期限。

## 4. Retry-After 超期即停

- 單次等待 > `MAX_RETRY_WAIT_SECONDS`（5 秒）：立即失敗，不睡眠。
- 已花費時間 + 等待 ≥ 整體預算：立即失敗，不睡眠。
- 等待無法解讀（缺少或非數字）：不猜測，立即失敗。
- 已達 `MAX_REST_ATTEMPTS`（3 次）：立即失敗。
- 所有情況下等待都是 Discord 建議的完整秒數，不會縮短。

## 5. 工作樹原有變更核對

與 `/home/gaston/build/unpack-check/bot/discord_http.py`（候選包中的修改前版本）比對：

- `RestDirectMessageSender`、`RestEphemeralReplier`：逐字相同。
- `verify_interaction_signature`：逐字相同。
- 差異只在：模組匯入（新增 `time`，`asyncio` 移到正確位置）、常數（`REST_RETRY_BUDGET_SECONDS`、`MAX_REST_ATTEMPTS`、`MAX_RETRY_WAIT_SECONDS`）、`DiscordRestClient.__init__` 的 `sleep`／`clock` 注入、`_send` 與新增的 `_attempt`、`_RateLimited`、`_retry_after_seconds`。
- 修正了一個既有問題：模組匯入曾置於模組 docstring 之前，導致 `discord_http.__doc__` 為 `None`。已移回匯入區並以測試鎖定。

## 6. 測試結果

| 檔案 | 結果 |
| --- | --- |
| `tests/test_discord_rest_retry.py` | 19 passed（新增 5 項：`create_message` 不重送、短等待亦不重送、頻道查詢與編輯可重送、逾時不重送、重送等待中取消） |
| `tests/test_discord_http.py` | 15 passed（未修改） |
| `tests/test_interaction_reply_failure.py`（新） | 4 passed（最終回覆失敗僅記錄 reason、不重送、不記錄 token、取消傳遞） |

紅燈記錄：

- 新增的預算測試在實作前有 2 項失敗（`test_slow_attempts...`、`test_a_retry_after_that_would_end...`）。
- 第一次執行時 DM 測試因腳本回應順序錯誤而失敗，已修正測試腳本（非實作問題）。
- 模組 docstring 測試在實作前未觀察到紅燈：該問題在建立測試前的設定步驟已先修正。

執行指令：`/home/gaston/.venvs/jukebox101-py312/bin/python -m pytest tests/test_discord_rest_retry.py tests/test_discord_http.py --basetemp=/home/gaston/.venvs/pt-rest-429`

未執行全量測試；未連接真實 Discord；未 commit、未 push。

## 7. 決議紀錄（2026-10-29）

1. 已決議：`create_message` 不重送 429；`create_dm_channel` 與 `edit_original_response` 可重送。
2. 整體預算 15 秒：依 DM 20 秒期限與 5 秒餘裕推定，未在真實 Discord 驗證。
3. 已修補（經 Lead 同意，範圍限於 `_complete`）：`edit_original_response` 失敗時改為記錄 `DiscordRestError` 的 sanitized reason，不重送、不吞掉取消訊號。詳見 `tests/test_interaction_reply_failure.py`。`_complete_flow` 內的同類呼叫不在本次範圍，仍未修補。
4. 未驗證：真實 Discord 對 429 的回應格式與 `Retry-After` 的實際行為；雲端部署系統的停止行為。
