# 階段 B：正式開放 `/exchange` 的啟用與回滾清單

狀態：**草案，實作與測試進行中、尚未部署、尚未啟用**。`JUKEBOX_FORMAL_EXCHANGE` 預設 off；合併程式碼本身不會開放 `/exchange`。本文件所有指令都在 VM 上以 `sudo docker exec jukebox101-bot-1 python -m bot.…` 執行，文中不放任何秘密。

背景：[測試與部署計畫 §7.2](../auto-review-test-deploy-plan.md)、[階段 A 啟用清單](stage-a-enable-rollback.md)、[持續部署](continuous-deployment.md)。對應 issue #20。

## 0. 這份文件要你決定什麼

要決定的只有一件事：**什麼時候把 `/exchange` 從「未開放」改成「正式開放」**。開放後，使用者可用 1 Nickel 換一首別人投稿的歌，歌曲連結以私訊（DM）送達。以下是已核定的規則（不是本文件新定的）：

| 情況 | 系統怎麼做 | 使用者看到什麼 |
|---|---|---|
| 正常 | 扣 1 點、私訊送出 1 次、歌曲仍留在歌池給其他人 | 「已將推薦私訊給你」 |
| 點數不足、沒有符合條件的歌 | 不扣點、不送私訊 | 說明原因 |
| 私訊**明確**沒送出（例如對方關閉私訊） | 恰好退 1 點，歌曲釋回可再抽 | 「已退還 1 Nickel」 |
| 私訊**結果不明**（逾時、斷線） | **不退點、不自動重送**，列入 Owner 可見的卡住清單 | 「結果未知，不會自動重送」，不能再發新的交換 |
| 結果不明之後才取得證明 | 只有「證明已送達」或「證明未送出」能改寫；已送達不退款，未送出才退 1 點 | 依證明結果 |

取捨說明：結果不明時我們選擇「寧可使用者暫時沒點也不送兩次」。代價是這類案件需要 Owner 看見並跟進；本期 Owner 只能**唯讀查看**，不能在系統內直接處理（處理方式見第 4 節）。

## 1. 先決條件（缺一不可）

| 條件 | 說明 | 誰確認 |
|---|---|---|
| 階段 B 程式碼已合併並部署 | 部署後開關仍 off；`python -m bot.healthcheck` 回 `ready` | 管理者 |
| 受控 compose 與 runtime policy 允許開啟 | 目前 `deploy/controlled/compose.yaml` 與 `bot/runtime_policy.py` 仍強制 `JUKEBOX_FORMAL_EXCHANGE=off`；開放前須有已審查的變更讓**只有此開關**可為 on（`JUKEBOX_NICKEL_CLAIM` 仍強制 off）。**尚未完成，待確認** | Sean／實作者 |
| 資料庫備份 | 開開關前先備份 | 管理者 |
| 歌池至少有 2 位不同使用者的已結算歌曲 | 否則所有人都會看到「目前沒有符合條件的歌曲」 | Owner |
| 驗收用的兩個真實 Discord 帳號 | A（投稿者）與 B（交換者），B 的私訊設定可切換 | Owner |

## 2. 啟用順序

1. **部署新映像，開關維持 off。** 推 commit 讓 CD 部署（`bot.env` 只在重建容器時生效，不要手動重建）。驗證：
   - `sudo docker exec jukebox101-bot-1 python -m bot.healthcheck` 顯示 `"result": "ready"`。
   - 在 Discord 執行 `/exchange`，仍回「交換功能尚未啟用」，且 `/balance` 不變。
2. **確認卡住清單可用（唯讀）。** `sudo docker exec jukebox101-bot-1 python -m bot.owner_cli exchange-stuck`，預期印出沒有卡住案件。此指令不寫入資料庫。
3. **確認帳本一致。** 開放前跑一次既有的帳本完整性檢查（沿用 `docs/` 內的唯讀對帳 SQL，結果須為 PASS）；開放後每次驗收結束再跑一次。
4. **開開關。** 在 root 擁有的 `/etc/jukebox101/bot.env` 設 `JUKEBOX_FORMAL_EXCHANGE=on`，推新 commit 重建容器。`JUKEBOX_NICKEL_CLAIM` 維持 `off`。
5. **驗證健康。** `python -m bot.healthcheck` 仍為 `ready`（開關 on 不再算 `FEATURE_POLICY_ENABLED`；若 `JUKEBOX_NICKEL_CLAIM` 被誤開，仍會報此錯）。
6. **跑第 3 節的真實 Discord 驗收。** 通過前不對一般使用者宣布開放。

## 3. 真實 Discord 驗收清單（Manual Pending，尚未執行）

自動測試（PostgreSQL 拋棄式資料庫、DM 為腳本化假件）不能證明 Discord 真實行為。以下需人工逐項確認並記錄結果；每項做完用 `/balance` 與 `exchange-stuck` 核對。

| # | 動作 | 預期 | 結果 |
|---|---|---|---|
| 1 | B 有 1 Nickel，執行 `/exchange` | 收到 1 則私訊（含歌曲連結），頻道內回覆**不含連結**，`/balance` 少 1 | 待測 |
| 2 | B 立刻再次 `/exchange`（無餘額） | 「Nickel 不足」，不扣點、無新私訊 | 待測 |
| 3 | 歌池只剩 B 自己投稿或已收過的歌時 `/exchange` | 「目前沒有符合條件的歌曲」，點數不變 | 待測 |
| 4 | B 關閉「允許來自伺服器成員的私訊」後 `/exchange` | 私訊未送出；回覆「已退還 1 Nickel」；`/balance` 回到原值；帳本只多一筆退款 | 待測 |
| 5 | 重新開啟私訊後再 `/exchange` | 可再抽到同一首（釋回）；正常送達並扣 1 點 | 待測 |
| 6 | 在 Discord Interaction 重複送出（快速連按） | 只扣 1 點、只收到 1 則私訊 | 待測 |
| 7 | 非 Owner 帳號嘗試查看卡住案件 | 無任何入口；Discord 內沒有此功能，CLI 僅 Owner 於 VM 上可執行 | 待測 |
| 8 | 重啟容器（`sudo docker restart jukebox101-bot-1`）發生在交換之間 | 重啟後不重送、不重扣；已送達者仍為已送達 | 待測 |

結果不明（逾時／斷線）無法在真實 Discord 上安全重現，**不列入人工驗收**；它由自動測試以腳本化 DM 結果覆蓋，真實環境發生時依第 4 節處理。

## 4. 卡住案件：Owner 如何看與如何跟進

查看（唯讀、不改任何資料）：

```
sudo docker exec jukebox101-bot-1 python -m bot.owner_cli exchange-stuck
```

每列只有：操作代號、請求者、交付狀態、原因、已等待秒數；**不含歌曲連結、token、餘額**。原因代碼：

| 原因代碼 | 意思 | 何時出現 |
|---|---|---|
| `reserved_not_sent` | 已預留、已扣點，但超過 10 分鐘仍沒開始送 | 程序在預留之後、送出之前中斷 |
| `sending_lease_expired` | 標記為送出中已超過 10 分鐘，尚未被整理為結果不明 | 程序在送出途中中斷 |
| `delivery_result_unknown` | 私訊結果不明，已保留不退點 | 逾時、斷線、無法判斷 |
| `refund_pending` | 已證明沒送出，但退款交易尚未提交 | 退款交易失敗；下一次交換請求或重啟後的整理會再試一次，仍只退一次 |

跟進原則：

- 本期系統**不提供** Owner 在系統內改寫狀態的指令。要處理 `delivery_result_unknown`，由 Owner 先向使用者確認是否收到私訊，再依確認結果決定是否由工程人員在核准下補記證明；**不得直接改資料庫餘額或帳本**。
- `refund_pending` 通常不需人工；若同一筆久未消失，查容器日誌（只含錯誤類別，不含內容），回報工程人員。
- 一般使用者不能查看此清單。使用者只能在自己的回覆中看到自己的操作代號。

## 5. 回滾（由輕到重）

| 狀況 | 做法 | 會發生什麼 |
|---|---|---|
| 懷疑 `/exchange` 有問題 | `/etc/jukebox101/bot.env` 設 `JUKEBOX_FORMAL_EXCHANGE=off`，推新 commit 重建 | `/exchange` 回到「尚未啟用」。**已扣的點、已送出的私訊不收回、不移動**；進行中或結果不明的案件保留原狀，仍列在 `exchange-stuck` |
| 回滾後使用者說被扣點 | 先跑 `exchange-stuck` 與帳本對帳，確認是否為已保留案件 | 已保留案件的退款或補記證明依第 4 節；不得直接改資料庫 |
| 新映像不健康 | 主機腳本自動換回前一個映像，回報 `rolled_back` | 看 `docker logs`；`rolled_back` 路徑在真實主機尚未驗證過 |
| 要回到階段 B 之前的映像 | 先把開關設 off 並確認沒有 `sending` 或 `reserved` 的案件（`exchange-stuck` 為空，且最近無進行中的交換） | 舊映像沒有 `exchange-stuck`，也不認得階段 B 的處理規則；有未了案件時不要回滾映像 |

不需要資料庫 migration：階段 B 使用既有的 `recommendations` 與 `credit_ledger` 結構，回滾不涉及 schema 變更。

## 6. 已知限制與待確認

- 真實 Discord 驗收（第 3 節）尚未執行。
- 受控 compose／runtime policy 放寬（第 1 節）尚未完成。
- Owner 在系統內處理卡住案件的指令不在本期範圍；目前只有唯讀查看。
- 帳本完整性檢查的實際執行位置與指令，上線前由管理者依 `bot/ledger_integrity.py` 的唯讀 SQL 確認並記錄。
