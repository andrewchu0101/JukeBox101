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

## 人工互動紀錄（2026-10-23，合成注入 success）

- 日期：**2026-10-23**。使用者確認此為「昨日」的測試；依交接日期 2026-10-24 推算，為 2026-10-23。本紀錄先前誤記為 2026-10-24，已更正。
- 停止狀態：**使用者補充確認**，按 Ctrl+C 正常中斷，沒有異常訊息。使用者未提供逐字的停止輸出，本文件不補寫該輸出。

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

## 人工互動紀錄（not_sent，使用者回報）

**性質**：操作者在私人測試環境、以 Owner 單一帳號，於本機 PowerShell 啟動 `listen --authorize-gateway --inject not_sent`，並在 Discord 逐步輸入指令。這是合成注入的實機互動，**不是真實 DM、真實資料庫、Ledger 或真實退款的驗證**。證據來源為使用者提供的 Discord 回覆文字。

- 啟動命令：`.\.venv\Scripts\python.exe -X utf8 -m tests.exchange_synthetic_acceptance listen --authorize-gateway --inject not_sent`
- 日期與時間：**2026-10-24**，Discord 畫面時間上午 11:52（使用者確認，證據來源為使用者回報）。
- 停止狀態：監聽視窗顯示 `Acceptance interrupted; inspect any in-flight external operation before retrying`，並返回 PowerShell 提示符號。這只代表該次 `not_sent` 監聽已中斷，**不代表所有 Python 程序皆已停止**。

| 步驟 | 指令 | 實際回覆（使用者提供） | 核對 |
| --- | --- | --- | --- |
| 1 | `/balance` | 【合成測試】假餘額：1 點（合成資料，未動真實點數）。 | 符合預期（初始 1 假點） |
| 2 | `/exchange` | 【合成測試】模擬未送出（注入），已退回 1 點；未實際發送 DM。操作 1 | 符合預期；標示未實際發送 DM |
| 3 | `/balance` | 【合成測試】假餘額：1 點（合成資料，未動真實點數）。 | 退款已還原為 1 點 |
| 4 | `/history` | 【合成測試】操作 1｜合成歌曲 #2｜未送出（注入）｜已退回 1 點 | 符合預期；一筆紀錄 |

- 步驟 4 的「合成歌曲 #2」只是合成資料的內部編號，不代表任何真實歌曲或可播放影片。
- 本輪未執行選做的第 5 步（再次 `/exchange`），沒有該觀察結果。

**結論**：`not_sent` 的 Discord 人工互動依上表通過。離線對應測試為 `tests/test_exchange_synthetic_acceptance.py::test_scripted_not_sent_is_refunded_and_visible_in_balance_and_history`，於 2026-10-24 在 Windows 端 `.venv` 執行，`17 passed`（整個檔案的離線測試）。這是離線結果，不是 Discord 驗收。

**未驗證事項**：真實 DM 的送出、MySQL 的交易與唯一鍵、Ledger 對帳、真實退款，都未驗證。

## 人工互動紀錄（uncertain，使用者回報）

**性質**：操作者在私人測試環境、以 Owner 單一帳號，於本機 PowerShell 啟動 `listen --authorize-gateway --inject uncertain`，並在 Discord 逐步輸入指令。這是合成注入的實機互動，**不是真實 DM、真實資料庫、Ledger 或真實退款的驗證**。證據來源為使用者提供的 Discord 回覆原文。

- 啟動命令：`.\.venv\Scripts\python.exe -X utf8 -m tests.exchange_synthetic_acceptance listen --authorize-gateway --inject uncertain`
- 日期與時間：**2026-10-24**，上午 11:56 左右（使用者回報的本機時間）；Discord 回覆畫面顯示上午 11:57。日期依使用者確認的本機時間填寫。

| 步驟 | 指令 | 實際回覆（使用者提供） | 核對 |
| --- | --- | --- | --- |
| 1 | `/balance` | 【合成測試】假餘額：1 點（合成資料，未動真實點數）。 | 符合預期（初始 1 假點） |
| 2 | `/exchange` | 【合成測試】模擬結果未知（注入）：保留紀錄，不退款、不重送；未實際發送 DM。操作 1 | 符合預期；保留紀錄、不退款、不重送 |
| 3 | `/balance` | 【合成測試】假餘額：0 點（合成資料，未動真實點數）。 | 符合預期；扣款保留 |
| 4 | `/history` | 【合成測試】操作 1｜合成歌曲 #2｜結果未知（注入，保留）｜無退款 | 符合預期；保留、無退款 |
| 5 | `/exchange` | 【合成測試】點數不足，交換一首歌曲需要 1 Nickel。 | 符合預期；沒有新操作碼 |
| 6 | `/balance`、`/history` | 假餘額 0 點；歷史仍只有操作 1 | 符合預期；未新增操作 |

- 步驟 4 的「合成歌曲 #2」只是合成資料的內部編號，不代表任何真實歌曲或可播放影片。
- 本輪以 Discord 回覆原文判定通過；未重跑測試，不記為本輪測試通過。

**結論**：`uncertain` 的 Discord 人工互動依上表通過（六步全部符合預期）。

**停止狀態**：`uncertain` 監聽已中斷並返回 PowerShell 提示符號（使用者回報）。訊息為 `Acceptance interrupted; inspect any in-flight external operation before retrying`。這只代表該次 `uncertain` 監聽已中斷，**不代表所有 Python 程序皆已停止**。

**未驗證事項**：真實 DM 的送出、MySQL 的交易與唯一鍵、Ledger 對帳、真實退款、結果未知後的真實重送與對帳，都未驗證。

## 人工互動紀錄（refund_fail，第一次執行，使用者回報）

**性質**：操作者在私人測試環境、以 Owner 單一帳號，於本機 PowerShell 啟動 `listen --authorize-gateway --inject refund_fail`，並在 Discord 逐步輸入指令。這是合成注入的實機互動，**不是真實 DM、真實資料庫、Ledger 或真實退款的驗證**。證據來源為使用者提供的 Discord 回覆原文。

- 日期與時間：**2026-10-24**，本機時間約上午 11:59（使用者回報）。

| 步驟 | 指令 | 實際回覆（使用者提供） | 核對 |
| --- | --- | --- | --- |
| 1 | `/balance` | 【合成測試】假餘額：1 點（合成資料，未動真實點數）。 | 符合預期 |
| 2 | `/exchange` | 【合成測試】模擬未送出（注入），退款待處理；未實際發送 DM。操作 1 | 符合預期；未顯示已退回 |
| 3 | `/balance` | 【合成測試】假餘額：0 點（合成資料，未動真實點數）。 | 符合預期；退款尚未完成 |
| 4 | `/history` | 【合成測試】操作 1｜合成歌曲 #2｜未送出（注入）｜退款待處理 | 符合預期 |
| 5 | `/exchange` | 【合成測試】點數不足，交換一首歌曲需要 1 Nickel。 | **與預期不符**，見下 |
| 6 | `/balance`、`/history` | 假餘額 0 點；歷史仍為操作 1 退款待處理 | 同上 |

**第 5 步差異（已定位原因）**
- 預期：每次 `/exchange` 開始前會重試待處理退款；注入的失敗只應發生一次，因此重試應成功，餘額回到 1 點。
- 實際：餘額仍為 0，退款仍待處理，回覆為點數不足。
- 原因（已於程式碼確認）：`tests/exchange_synthetic_acceptance.py` 原本在 `refund_fail` 模式下，**每次** `/exchange` 都重新設定退款失敗，使重試也被注入失敗。這與程式註解「只失敗一次」不符，屬於合成入口的錯誤，不是退款邏輯錯誤。
- 修正：改為只在程序啟動後的第一次請求設定一次（`_refund_fault_armed`）。新增離線測試 `test_scripted_refund_failure_is_injected_once_so_the_next_request_retries_and_refunds`。
- 離線驗證：`tests/test_exchange_synthetic_acceptance.py` 於 2026-10-24 在 Windows `.venv` 執行，**18 passed**（含新測試）。這是離線結果，不是 Discord 驗收。
- 離線探測（修正後，同一程序內兩次請求）：第一次回覆「退款待處理」、餘額 0；第二次回覆「模擬未送出（注入），已退回 1 點；未實際發送 DM。操作 2」、餘額 1，歷史操作 1 變為「已退回 1 點」，並新增操作 2（同為合成歌曲 #2，已退回）。

**狀態**：第 1～4 步通過；第 5、6 步的缺陷已定位並修正，修正後的重新驗收見下節「refund_fail 修正後重新驗收」。本節不作為最終通過紀錄。

**停止狀態**：第一次 `refund_fail` 監聽已按 Ctrl+C，輸出 `Acceptance interrupted; inspect any in-flight external operation before retrying`（使用者回報）。這只代表該次監聽已中斷。

**未驗證事項**：真實 DM、MySQL、Ledger 對帳、真實退款，都未驗證。

## 缺陷與修正（refund_fail 第 5 步）

- **現象**：第一次 `refund_fail` 驗收中，第 5 步 `/exchange` 回覆「點數不足」，餘額仍為 0，退款未被重試。
- **原因**：`tests/exchange_synthetic_acceptance.py` 的 `SyntheticExchangeService.synthetic_exchange` 在 `refund_fail` 模式下，**每次**請求都重新設定退款 SQL 失敗，使下一次請求的退款重試也被注入失敗。註解與設計意圖是「只失敗一次」。
- **性質**：合成入口的錯誤，不是退款邏輯的錯誤。退款重試流程（每次交換請求開始前重試待處理退款）本身符合設計。
- **修正**：改為只在程序啟動後第一次請求設定一次（`_refund_fault_armed`）。
- **迴歸測試**：新增 `test_scripted_refund_failure_is_injected_once_so_the_next_request_retries_and_refunds`。
- **離線驗證**：修正後，`tests/test_exchange_synthetic_acceptance.py` 於 2026-10-24 在 Windows `.venv` 執行，**18 passed**。這是修正後的離線結果，不是 Discord 驗收。
- **未直接驗證**：未在修正前的程式上重跑迴歸測試以證明它會失敗。判定依據是程式碼比對，不是紅燈證據。

## refund_fail 修正後重新驗收（使用者回報）

**性質**：操作者在私人測試環境、以 Owner 單一帳號，於本機 PowerShell 啟動 `listen --authorize-gateway --inject refund_fail`，並在 Discord 逐步輸入指令。這是合成注入的實機互動，**不是真實 DM、真實資料庫、Ledger 或真實退款的驗證**。證據來源為使用者提供的 Discord 回覆原文與 PowerShell 輸出。

- 日期與時間：**2026-10-24**，畫面時間 12:02～12:03（使用者回報，與目前紀錄的日期一致，無衝突）。
- 啟動前檢查：使用者執行 `Get-Process python -ErrorAction SilentlyContinue`，輸出為空，未列出 Python 程序。
- 啟動指令：`.\.venv\Scripts\python.exe -X utf8 -m tests.exchange_synthetic_acceptance listen --authorize-gateway --inject refund_fail`

| 步驟 | 時間 | 指令 | 實際回覆（使用者提供） | 判定 |
| --- | --- | --- | --- | --- |
| 1 | 12:02 | `/balance` | 【合成測試】假餘額：1 點（合成資料，未動真實點數）。 | 符合 |
| 2 | 12:02 | `/exchange` | 【合成測試】模擬未送出（注入），退款待處理；未實際發送 DM。操作 1 | 符合；未顯示已退回 |
| 3 | 12:02 | `/balance` | 【合成測試】假餘額：0 點（合成資料，未動真實點數）。 | 符合 |
| 4 | 12:02 | `/history` | 【合成測試】操作 1｜合成歌曲 #2｜未送出（注入）｜退款待處理 | 符合 |
| 5 | 12:03 | `/exchange` | 【合成測試】模擬未送出（注入），已退回 1 點；未實際發送 DM。操作 2 | 符合：先補退操作 1，操作 2 也完成退款 |
| 6 | 12:03 | `/balance` | 【合成測試】假餘額：1 點（合成資料，未動真實點數）。 | 符合 |
| 7 | 12:03 | `/history` | 【合成測試】操作 1｜合成歌曲 #2｜未送出（注入）｜已退回 1 點／操作 2｜合成歌曲 #2｜未送出（注入）｜已退回 1 點 | 符合；兩筆均已退款 |

- 步驟 5 之後的兩筆歷史與步驟 7 同為合成歌曲 #2，這是合成入口在 `refund_fail` 模式下的設計結果（每次 DM 皆為注入未送出），不代表真實歌曲。
- 步驟 7 的歷史行為與離線探測一致；離線探測結果見上節「缺陷與修正」。

**判定：通過（僅限合成注入）**。第一次退款待處理；第二次請求補退操作 1，操作 2 也完成退款；最終餘額 1 點，兩筆歷史均已退款。

**停止狀態**：使用者回報 PowerShell 輸出 `Acceptance interrupted; inspect any in-flight external operation before retrying`，並返回提示符號。這只代表該次 `refund_fail` 監聽已中斷；**不代表停止後另外做過程序檢查**，也不代表所有 Python 程序皆已停止。

**未驗證事項**：真實 DM、MySQL、migration 003、Ledger 與真實帳務，都未驗證；正式 `/exchange` 未啟用。

**離線測試**：本輪（12:02～12:03 的 Discord 驗收）未重跑任何測試。最近一次離線結果為 2026-10-24 修正後執行的 `tests/test_exchange_synthetic_acceptance.py` 18 passed，不得寫成本輪重跑通過。

## 收尾狀態與下一步（更新至 refund_fail 修正後重新驗收）

- 分支與 HEAD：`feature/owner-review-offline-checkpoint`。撰寫本段時（尚未提交本文件），HEAD 為 `29cd823`；本文件所在的提交為 `213fb4b`，之後與 `origin` 同步。本輪相關提交：`97fc9dc`（交換流程）、`8346787`（listen 接線）、`29cd823`（參與者、餘額與歷史）。
### 合成注入驗收進度（Discord 人工，合成注入，Owner 一人）

| 注入模式 | 判定 | 日期與時間 | 證據位置 | 監聽停止狀態 |
| --- | --- | --- | --- | --- |
| `success` | 通過（合成） | 2026-10-23（依「昨日」推算，見 success 紀錄） | 本文件 success 紀錄 | 正常 Ctrl+C 中斷，無異常訊息（使用者補充確認；未提供逐字輸出） |
| `not_sent` | 通過（合成） | 2026-10-24 上午 11:52 | 本文件 not_sent 紀錄 | 已中斷，返回提示符號（使用者回報） |
| `uncertain` | 通過（合成） | 2026-10-24 上午 11:56～11:57 | 本文件 uncertain 紀錄（含六步原文） | 已中斷，返回提示符號（使用者回報） |
| `refund_fail` 第一次 | 第 1～4 步通過；第 5、6 步發現缺陷，已修正 | 2026-10-24 約 11:59 | 本文件「refund_fail 第一次執行」與「缺陷與修正」 | 已中斷，輸出 `Acceptance interrupted; ...`（使用者回報） |
| `refund_fail` 修正後重新驗收 | 通過（合成） | 2026-10-24 12:02～12:03 | 本文件「refund_fail 修正後重新驗收」 | 已中斷，返回提示符號（使用者回報） |

- 「已中斷」只代表該次監聽已中斷並返回提示符號，**不代表所有 Python 程序皆已停止**。
- 離線測試最近一次為 2026-10-24 修正後執行，`tests/test_exchange_synthetic_acceptance.py` 18 passed；本輪 Discord 驗收未重跑測試。
- 尚未驗證：真實 DM；正式 MySQL；完整 `SubmissionRepository.exchange` 的隔離整合；Ledger 與真實帳務；正式 `/exchange` 啟用。隔離 MySQL 的結果見 `docs/exchange-isolated-mysql-2026-10-24.md`（14 passed：底層 characterization 8 項與完整 repository 6 項，僅限隔離實例與所列測試）。以上通過結論僅涵蓋合成注入。

### 下一步

- 這四種注入模式的合成 Discord 驗收已有使用者回報的原文證據。
- 本輪審核（2026-10-24）：`tests/exchange_synthetic_acceptance.py`、`tests/test_exchange_synthetic_acceptance.py` 與本文件相對 HEAD（`213fb4b`）的差異，只包含 refund_fail 修正、迴歸測試與本文件更新，沒有混入先前未提交內容。`docs/youtube-policy-confirmation.md` 為未追蹤的新檔。
- 本輪離線檢查：與交換流程相關的 7 個測試檔共 82 passed（於 2026-10-24 在 Windows `.venv` 實際執行）。這是離線結果，不是 Discord 驗收。
- 之後若要提交，需要你明確指示並指定檔案。建議範圍：上述兩個 `tests/` 檔、本文件與 `docs/youtube-policy-confirmation.md`。`README.md`、`ROADMAP.md` 的工作樹中有整檔 CRLF 行尾差異（HEAD 為 LF），需先決定處理方式，才能只提交本輪的兩行政策說明。根目錄含郵件地址的 PDF 不納入提交。
- 真實 DM、正式 MySQL（非隔離實例）與正式 `/exchange` 啟用需要另行授權，本輪不進行。隔離 MySQL 的 exchange 整合測試結果見 `docs/exchange-isolated-mysql-2026-10-24.md`。
- 其他：工作樹仍有大量未提交成果（Owner、Recovery、v2、Batch B、規格草案等），本文件與其他工作分開，未清理或覆寫。Application／Guild ID 的既有文件清理未處理，也未改寫 Git 歷史。
