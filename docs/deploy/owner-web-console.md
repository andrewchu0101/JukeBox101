# Owner 審核網頁（按需啟動、只供 Owner 一人使用）

狀態：**已實作並通過離線自動測試；網路選項 A 已由 Owner 核定；尚未在 VM 或瀏覽器上實測**（見最後一節「需要 Owner 人工確認」）。

## 這是什麼

一個臨時的審核網頁，取代在 Discord 一筆筆按按鈕。你要審案時才啟動，用完就關；沒有常駐程式、沒有公開埠。所有答案都交給與 Discord 卡片相同的共同核心（`bot/owner_decisions.py`）處理，網頁本身不含任何審核規則；操作者身分只取自環境變數 `DISCORD_OWNER_USER_ID`，不接受來自請求的任何身分。

怎麼保護自己（白話）：

| 防護 | 做法 |
|---|---|
| 只在 VM 內部聽 | 只接受 `127.0.0.1` 或 `::1`，其他位址（含 `0.0.0.0`）直接拒絕啟動 |
| 一次性存取碼 | 每次啟動隨機產生，印在終端機；網頁用網址 `#token=…` 取得，存在記憶體並用自訂標頭 `X-Console-Token` 送出，不進網址列、不進日誌 |
| 防偽造來源 | Host 必須是 `127.0.0.1:<埠>` 或 `localhost:<埠>`；有 Origin 時必須同源；會改資料的操作一律 POST |
| 嚴格瀏覽器規則 | CSP（只允許自己的腳本與樣式、縮圖只允許 `https://i.ytimg.com`）、`nosniff`、`no-referrer`、`no-store`、禁止被嵌入 |
| 防暴力與濫用 | 每分鐘請求上限、錯誤存取碼累積鎖定、請求大小上限 64 KiB、單批最多 100 筆 |
| 閒置自動結束 | 30 分鐘沒有任何通過驗證的請求就自己關閉（常數 `IDLE_TIMEOUT_SECONDS`） |
| 不洩漏 | 錯誤只回固定句子與錯誤類別；日誌只記類別；存取碼、密碼、資料庫位址不寫入日誌，也關閉了存取日誌 |

## 啟動

### 容器網路：採用 `--network host`（Owner 已核定）

應用程式刻意只綁容器內的 `127.0.0.1`。Docker 的 `-p 127.0.0.1:8765:8765` 只會把流量轉到容器的**對外網卡**，到不了容器內部的回送位址，所以本做法**不使用 `-p`**，改讓容器與 VM 共用網路：程式的 `127.0.0.1` 就是 VM 的回送位址，SSH 通道直接接得到，仍然沒有任何公開埠。

代價與限制（請知悉）：

- 這個一次性容器**不經** `jukebox101_egress` / `jukebox101_database` 兩個受管網路，連資料庫走 VM 本身的網路。
- 若 VM 防火牆或網路規則依這兩個網路限制外連，可能擋住資料庫連線。**啟動前先確認**（見下方「啟動前確認」）。
- 容器共用 VM 網路，因此用唯讀檔案系統、不提權、移除所有 capability、只綁回送位址、用完即刪來降低影響。
- 放寬程式去綁容器網卡的做法**不採用**（會違反「只綁 127.0.0.1／::1」的核定要求）。

### 啟動前確認

1. 確認 Bot 正在運作、沒有其他程序佔用 8765：`sudo ss -ltnp | grep 8765` 應無輸出。
2. 確認 VM 能直接連到資料庫主機與連接埠（以 `DB_HOST`、`DB_PORT` 為準），例如 `timeout 5 bash -c '</dev/tcp/<DB_HOST>/<DB_PORT>' && echo reachable`。不通就先不要啟動，請 Owner 處理防火牆，不要為此改動 Bot 的網路。
3. 確認要用的映像摘要與 Bot 目前部署的相同。

### 一行指令（選項 A）

在 VM 上（互動式，這樣才看得到存取碼；不要加 `-d`，也不要把輸出導到會留存的日誌）：

```bash
IMAGE="<Bot 現行映像摘要，例如 ghcr.io/…@sha256:…>"   # 取得方式見下方
docker run --rm -it --name jukebox101-owner-web \
  --network host \
  --user 10001:10001 --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --memory 256m --cpus 0.5 \
  --env-file /etc/jukebox101/bot.env \
  -e DB_SSLMODE=verify-full -e DB_SSLROOTCERT=/run/secrets/db_ca.crt \
  -e DB_PASSWORD_FILE=/run/secrets/db_password \
  -e YOUTUBE_API_KEY_FILE=/run/secrets/youtube_api_key \
  -e SECRET_EXPECTED_UID=10001 -e SECRET_EXPECTED_GID=10001 \
  -v /etc/jukebox101/secrets/db_password.txt:/run/secrets/db_password:ro \
  -v /etc/jukebox101/secrets/youtube_api_key.txt:/run/secrets/youtube_api_key:ro \
  -v /etc/jukebox101/supabase-ca-2021.crt:/run/secrets/db_ca.crt:ro \
  --entrypoint python "$IMAGE" -m bot.owner_web --port 8765
```

說明：

- 環境與秘密沿用 `deploy/controlled/compose.yaml` 的 Bot 設定（`DB_*`、`DISCORD_OWNER_USER_ID`、自動審核各開關都來自 `bot.env`）。**這支網頁不需要 Discord Token**，所以沒有掛載它。
- 自動發獎開關（`JUKEBOX_AUTO_SETTLEMENT`）沿用 `bot.env`；開著時，按「是，發獎」與 Discord 卡片一樣會接著結算。
- 若 `bot.env` 的 `DB_HOST` 等欄位沒有放在該檔案裡，請依主機實際 compose 補上 `-e`（compose 以 `${DB_HOST}` 等插值，不一定在 env 檔內）。**以上欄位組合尚未在 VM 實跑，屬待驗證。**
- 取得映像摘要：`docker inspect --format '{{.Image}}' <Bot 容器名稱>`（例如以 `docker ps --format '{{.Names}}'` 查名稱），或沿用 CD 目前部署的 digest。

啟動後終端機會印出：

```
Owner 審核網頁已啟動（閒置 30 分鐘自動關閉，Ctrl+C 可立即關閉）。
請在 SSH 通道另一端的瀏覽器開啟：http://localhost:8765/#token=<一次性存取碼>
```

### SSH 通道（在你自己的電腦）

另開一個終端機：

```bash
ssh -L 8765:127.0.0.1:8765 <vm>
```

然後用瀏覽器開啟終端機印出的**完整網址**（含 `#token=…`）。網頁會立刻把存取碼從網址列移除，只留在頁面記憶體；重新整理頁面就需要再貼一次完整網址。

## 使用方式

1. 上方可篩選：全部／有疑慮／等時長／等確認是否重複／凍結衝突。
2. 每列選「是，發獎」或「不是」（選了就自動勾選）；也可用「全選」「全部選是」「全部選不是」。
3. 「等時長」與「凍結衝突」的案件不能在網頁處理，列上會寫原因，請改用 Discord 或指令列。
4. 已通過、要確認是否重複的案件，選「不是」時要從下拉選單指定重複的是哪一首。
5. 按「送出」：系統**逐筆依序**處理，每筆各自回報成功或失敗原因；不是一次全成功或全失敗，失敗不影響其他筆。處理完會自動重新讀取清單。
6. 任何錯誤都會顯示在頁面上；隨時可按「重新載入」。若送出途中連線中斷，請重新載入確認哪些筆已處理。

## 關閉

- 在啟動的終端機按 `Ctrl+C`；或閒置 30 分鐘自動結束。
- 容器使用 `--rm`，結束即消失；沒有留下任何檔案（唯讀檔案系統，只有 `/tmp` 暫存記憶體）。
- 強制關閉：`docker stop jukebox101-owner-web`。

## 如何確認沒有監聽公開位址

啟動後在 VM 上：

```bash
sudo ss -ltnp | grep 8765
```

應該只看到 `127.0.0.1:8765`（或 `[::1]:8765`），**不應出現** `0.0.0.0:8765` 或 `*:8765` 或 `[::]:8765`。也可在另一台電腦連 `http://<VM 公開位址>:8765`，應連不上。關閉後再查一次應完全沒有輸出。

## 風險與回滾

- 回滾：關閉容器即可（`--rm` 自動刪除）；不改 compose、不改防火牆、不改資料庫結構。若 `--network host` 連不上資料庫，也只是啟動失敗，Bot 不受影響。
- 不啟動就沒有任何東西在跑；沒有新增常駐服務、沒有改 compose、沒有開任何防火牆埠，也沒有資料庫結構變更，因此**不需要回滾步驟**：關掉容器即可。
- 風險一：存取碼印在終端機，任何能看到你終端機畫面或 `docker logs`（因為用 `-it` 前景執行，不會寫進容器日誌）的人都能用。請在自己控制的 SSH 連線中操作，用完關閉。
- 風險二：`--network host` 讓這個一次性容器共用 VM 網路；已用唯讀檔案系統、不提權、移除所有 capability 與只綁回送位址降低影響。
- 風險三：批次操作可能一次發出多筆獎勵。每筆都是與 Discord 卡片相同的核心流程，沒有額外繞過，但按「送出」前請確認勾選與答案。
- 已寫入的決定（含發獎）與 Discord 卡片處理的結果相同，不能靠關閉網頁取消；要撤回須走既有的正式重審程序。

## 需要 Owner 人工確認的項目（尚未驗證）

1. `--network host` 在 VM 上能否連上資料庫（見「啟動前確認」）。
2. SSH 通道實際連線，並能用完整網址開啟頁面、重新整理後的行為。
3. 手機寬度與桌面寬度的排版、深色與淺色模式。
4. 縮圖是否正常載入（瀏覽器直接向 `i.ytimg.com` 取圖，伺服器不下載不保存；公司或瀏覽器擴充功能可能擋下）。
5. 批次操作：全選、全部選是／不是、重複下拉選單、逐筆結果顯示，以及單筆失敗時其他筆繼續。
6. 閒置 30 分鐘自動關閉、`Ctrl+C` 關閉，以及 `ss -ltnp` 只顯示回送位址。

## 查兌換問題（搜尋欄）

頁面頂部可輸入使用者回報的編號（`/exchange` 回覆裡的操作編號，正整數），顯示該筆兌換的狀態、提出者編號、建立時間、是否已退點、卡住原因與已等待秒數。**不顯示連結、token、餘額**（後端只輸出白名單欄位）。資料來自 `repository.owner_exchange_lookup`，非 Owner 呼叫會被拒；Repository 沒有該方法時搜尋會回「找不到」錯誤句。尚未對真實資料庫驗證。
