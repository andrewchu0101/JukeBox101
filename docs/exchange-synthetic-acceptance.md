# 合成交換驗收入口（Discord 合成測試；不送真實 DM）

範圍：`tests/exchange_synthetic_acceptance.py`。使用記憶體假資料、預排的 DM 結果與假餘額。不寫入 MySQL，不動真實點數，不實際發送 DM。合成測試用的 Discord 使用者 ID 只由操作者的程序環境提供，不寫入本文件或 repo。

## 參與者

| 角色 | 來源 | 假資料 |
| --- | --- | --- |
| Owner | `DISCORD_OWNER_USER_ID`（既有設定） | 1 點，持有合成版本 A |
| 夥伴（可選） | `JUKEBOX_SYNTHETIC_PARTNER_USER_ID` | 1 點，持有合成版本 B；未設定時由內部種子持有，無法被任何呼叫者使用 |
| 零點數（可選） | `JUKEBOX_SYNTHETIC_ZERO_USER_ID` | 0 點；未設定則不存在此參與者 |

- 未設定的使用者不會自動取得假點數。`/exchange`、`/balance`、`/history` 對他們只回覆「此帳號未設定為合成測試者；未扣點、未建立紀錄。」
- 本人投稿排除維持正式規則：Owner 持有版本 A，因此 Owner 只能抽到夥伴的版本 B；夥伴只能抽到版本 A。
- 環境值必須是正整數十進位字串；重複或格式錯誤會在啟動前拒絕。

## 操作

以下指令只在操作者的本機 PowerShell 執行。Discord 設定沿用既有程序環境變數，不寫入 `.env` 或文件。

```powershell
# 離線場景（不連網）
.\.venv\Scripts\python.exe -X utf8 -m tests.exchange_synthetic_acceptance scenario --inject success

# Discord 合成監聽（需要 gateway 授權；連線前比對六指令契約）
.\.venv\Scripts\python.exe -X utf8 -m tests.exchange_synthetic_acceptance listen --authorize-gateway --inject success
```

`--inject` 只在啟動時選定：`success`、`not_sent`、`uncertain`、`refund_fail`。Discord 使用者無法切換。`--authorize-live-dm` 在本輪一律拒絕。

## 指令與回覆

- `/exchange`：每次呼叫都是新的 Discord 互動，因此是新請求，不是重播。只有 Discord 對同一互動的重試才會重播，回覆原結果且不再扣點。
- 成功（注入 success）：`【合成測試】合成交換成功；未實際發送 DM。操作 N`
- 未送出（注入）：`模擬未送出（注入），已退回 1 點；未實際發送 DM。`，退款失敗時為「退款待處理」。
- 未知（注入）：`模擬結果未知（注入）：保留紀錄，不退款、不重送；未實際發送 DM。`
- 點數不足：`點數不足，交換一首歌曲需要 1 Nickel。`；無候選：`目前沒有符合條件的歌曲`。
- `/balance`：`假餘額：N 點`，只顯示呼叫者自己的假餘額。
- `/history`：每筆顯示操作碼、合成歌曲編號、交付狀態與財務狀態，不顯示連結。

## 如何確認沒有重複扣假點、沒有實際 DM

1. 成功後 `/balance` 應從 1 變為 0，`/history` 新增一行「操作 N｜已交付（合成）」。
2. 再輸入 `/exchange` 應回覆點數不足，`/balance` 仍為 0。它是新請求，不會改變餘額。
3. 不會發生真實 DM：注入路徑不呼叫 Discord 的 DM 適配器（測試以 AssertionError 固定）。回覆與歷史都標示「未實際發送 DM」。

## 已驗證（離線、mock、fake）

- 參與者與本人排除、零點數與未設定使用者、重複或格式錯誤的環境值。
- 成功、未送出退款、退款失敗待處理、未知不退款，以及 /balance、/history 的顯示。
- 同一互動的重試是重播，不重複扣點。
- 注入模式只在啟動時選定；Discord handler 沒有模式參數。
- listen 透過既有的 Owner transport 守衛與六指令契約比對；測試以替身取代實際連線。

## 未驗證與限制

- 本入口在真實 Discord 上的執行，只有操作者實際啟動並操作後才會有結果。
- 只有「查找收件人失敗」才歸為未送出；發送階段的任何失敗都是未知（包括 403 與 50007），因為 discord.py 可能在同一次呼叫內先重試 5xx。
- `asyncio.wait_for` 不是絕對上限：取消後會等待任務結束。測試量測了此行為。
- 已安裝的 discord.py 2.7.1 只把 `nonce` 回傳在成功訊息上，沒有 `enforce_nonce`，因此不能用它排除重複訊息，也不承諾 exactly-once。
- 429 歸為未知，未逐字核對官方文件。
- 過期的 `sending` 與待退款只在下一次交換請求或啟動時處理，沒有新請求時仍會待處理。

## 與 Spec 的關係

進度與規則以 `docs/recovery-batchb-private-beta-spec.md` 為準。該檔案目前有未提交的修改，TK-16～18 的文字與 S8～S17 狀態列尚未進入版本庫；本文件只描述入口用法與已驗證範圍，不複製規格。
