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

## 人工互動紀錄（2026-10-24，合成注入 success）

**性質**：操作者在私人測試 Guild、以 Owner 單一帳號，於本機 PowerShell 啟動 `listen --authorize-gateway --inject success`，並在 Discord 逐步輸入指令。這是合成注入的實機互動，**不是真實 DM、真實資料庫、Ledger 或正式交換的驗證**。

- 啟動前：操作者確認四個程序環境變數存在（只回報 True／False，未顯示值）。
- 啟動命令：如上節「操作」所列。監聽程序由操作者啟動，未提交或同步任何 Discord 指令。
- 操作順序與結果：

| 步驟 | 指令 | 實際回覆（摘錄） | 核對 |
| --- | --- | --- | --- |
| 1 | `/balance` | 【合成測試】假餘額：1 點（合成資料，未動真實點數）。 | 符合預期 |
| 2 | `/exchange` | 【合成測試】合成交換成功；未實際發送 DM。操作 1 | 符合預期；未見連結 |
| 3 | `/balance` | 【合成測試】假餘額：0 點（合成資料，未動真實點數）。 | 扣 1 假點 |
| 4 | `/history` | 【合成測試】操作 1｜合成歌曲 #2｜已交付（合成）｜無退款 | 一筆；合成歌曲 #2 為另一個合成版本（未設定夥伴時的內部種子持有），不是 Owner 自己的版本 |
| 5 | `/exchange` | 【合成測試】點數不足，交換一首歌曲需要 1 Nickel。 | 拒絕；沒有新操作碼 |
| 6 | `/balance` | 【合成測試】假餘額：0 點（合成資料，未動真實點數）。 | 未增加 |
| 7 | `/history` | 【合成測試】操作 1｜合成歌曲 #2｜已交付（合成）｜無退款 | 未增加 |

- 步驟 4 的「合成歌曲 #2」只是合成資料的內部編號，不代表任何真實歌曲或可播放影片。
- 步驟 2 的回覆說明未實際發送 DM；這次操作沒有任何真實 DM 或 Discord 訊息送往其他使用者。
- 本次未設定夥伴與零點數參與者，只有 Owner 一人參與。

**未驗證事項**：真實 DM 的送出、MySQL 的交易與唯一鍵、Ledger 對帳、退款的實機行為、正式 `/exchange` 閘門的切換，都未驗證。

**停止方式**：監聽程序仍由操作者的 PowerShell 視窗執行，停止時請在該視窗按 Ctrl+C。

## 收尾狀態與下一步（2026-10-24）

- 分支與 HEAD：`feature/owner-review-offline-checkpoint`，HEAD 為 `29cd823`，與 `origin` 同步。本輪相關提交：`97fc9dc`（交換流程）、`8346787`（listen 接線）、`29cd823`（參與者、餘額與歷史）。
- 已完成的人工驗收：僅 success 合成注入、Owner 一人。初始 1 假點；`/exchange` 成功、標示未實際發送 DM；假餘額降為 0；`/history` 只有操作 1；再次 `/exchange` 點數不足，餘額與歷史未增加。
- 監聽停止狀態：**未確認停止**。除非操作者明確確認，否則不得記為已停止。
- 尚未驗證：`not_sent` 合成退款、`uncertain`、`refund_fail` 的 Discord 合成互動；真實 DM；MySQL；migration 003；真實帳務；正式 `/exchange` 啟用。
- 下一步優先序：（1）`not_sent` 合成退款的 Discord 互動；（2）`uncertain` 與 `refund_fail` 的互動。
- 其他：工作樹仍有大量未提交成果（Owner、Recovery、v2、Batch B、規格草案等），本文件與其他工作分開，未清理或覆寫。Application／Guild ID 的既有文件清理未處理，也未改寫 Git 歷史。
