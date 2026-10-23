# 合成交換驗收入口（離線；未實際發送 DM）

範圍：`tests/exchange_synthetic_acceptance.py` 的合成 `/exchange` 驗收入口。它使用記憶體假資料與預先排定的 DM 結果，不寫入 MySQL、不動真實點數，也不實際發送 DM。

## 用法

以下指令只在操作者的本機 PowerShell 執行，Discord 設定只放在程序環境變數，不寫入 `.env` 或文件。

```powershell
# 1. 離線場景（不連網，不需要 Discord 設定）
.\.venv\Scripts\python.exe -X utf8 -m tests.exchange_synthetic_acceptance scenario --inject success

# 2. Discord 合成監聽（需要 gateway 授權；連線前會比對六指令契約）
.\.venv\Scripts\python.exe -X utf8 -m tests.exchange_synthetic_acceptance listen --authorize-gateway --inject success
```

`--inject` 只在啟動時選定，Discord 使用者無法切換：

| 模式 | 效果 |
| --- | --- |
| `success` | 預排的成功 DM 結果，不送出任何訊息 |
| `not_sent` | 預排的「未送出」結果，觸發一次退款 |
| `uncertain` | 預排的未知結果，不退款、不重送 |
| `refund_fail` | 預排未送出，且下一次退款交易被注入一次失敗，驗證待退款路徑 |

拒絕的組合：`--authorize-live-dm` 在本輪一律拒絕；`scenario` 不接受 `--authorize-gateway`；`listen` 沒有 `--authorize-gateway` 會直接拒絕。

## 安全邊界

- `/exchange` 的 handler 沒有模式參數；模式只來自啟動參數。
- 合成入口的 DM 一律使用注入結果，`discord.Client.fetch_user` 不會被呼叫（測試以 AssertionError 固定）。
- 每則合成回覆都標示「【合成測試】」與「未實際發送 DM」。
- 正式 `MusicService.exchange()` 的閘門未改變，仍回覆未扣點。
- 六指令契約由既有的 Owner transport 守衛比對（`listen_owner_transport`）；本輪沒有新增或改名任何指令。

## 已驗證（離線、mock、fake）

- 場景：成功交付、零點數使用者、各注入模式的結果與退款狀態。
- handler：/exchange 互動走合成流程，延遲回應後以 ephemeral 回覆，回覆標示且不含連結。
- 建構：客戶端包含 owner 群組（guild 範圍）與 exchange 指令；路由為合成流程。
- listen：透過既有 `listen_owner_transport` 呼叫，並以 `async with` 關閉客戶端（以替身取代實際連線）。
- 參數：各拒絕組合在任何客戶端建立前拒絕。

## 限制（照實）

- **未連 Discord。** 本入口的 listen 尚未在真實 Discord 上執行；六指令契約比對只在實機時有意義。
- **DM 分類保守。** 只有「查找收件人失敗」被歸為未送出（此時 send 尚未呼叫）。發送階段的任何失敗，包括 403／50007，都是未知。原因是 discord.py 可能在同一次呼叫內先重試 5xx，最後一次回應不能證明先前嘗試未被接受。
- **`asyncio.wait_for` 不是絕對上限。** 它取消任務後會等待其結束；若底層不配合取消，逾時會超過 20 秒。測試量測了此行為。實際 discord.py 的 HTTP 取消契約與內部重試可能使單次呼叫超過 20 秒，因此逾時只保證流程不會在我們這一側無限等待。
- **`nonce` 沒有去重保證。** 安裝的 discord.py 2.7.1 的 `send` 只接受 `nonce` 並在成功訊息上回傳，沒有 `enforce_nonce`。因此不能用它來排除 SDK 內部重試造成的重複訊息，也不承諾 exactly-once。
- **429 未逐字核對官方文件。** 目前保守歸為未知。
- **機會式維護。** 過期的 `sending` 與待退款只在下一次交換請求或啟動時處理。沒有新請求時仍會待處理。

## 與 Spec 的關係

進度與規則仍以 `docs/recovery-batchb-private-beta-spec.md` 為準。該檔案目前有未提交的修改，TK-16～18 的說明與 S8～S17 狀態列尚未進入版本庫；本文件只描述入口用法、限制與已驗證範圍，不複製規格內容。
