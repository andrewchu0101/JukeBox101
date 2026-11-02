# 階段 A：自動審核與版本自動確認的啟用與回滾清單

狀態：**草案，實作未完成、尚未部署、尚未啟用**。所有開關預設 off；合併程式碼本身不會開啟任何功能。每一步標明由誰做、做完會看到什麼。

背景與設計：[自動確認錄音版本設計](../auto-review-version-confirm-design.md)、[測試與部署計畫](../auto-review-test-deploy-plan.md)、[自動審核與 Nickel 自動發放](auto-review-nickel-enable.md)、[持續部署](continuous-deployment.md)。

## 0. 先決條件（缺一不可）

| 條件 | 說明 | 誰確認 |
|---|---|---|
| Google 回覆範圍 | Owner 確認 Google 的回覆涵蓋「用官方 API 做內部比對」的用途；未確認前不得開啟任何會呼叫 YouTube 的開關 | Owner |
| 14 天保存與即時重查設計 | Owner 口頭同意；Sean 核對 `docs/api-retention-release.md` 後才算正式確認 | Sean／Owner |
| migration 0004 | 套用到正式資料庫（`python -m bot.migrate plan`，再 `apply --apply`）**需要 Owner 另行明確授權**；系統不會自動套用，也不會代為決定 | Owner 授權、管理者執行 |
| 資料庫備份 | 開任何開關前先備份 | 管理者 |
| 首筆案件處理 | 首筆歌曲標題是「XG - HYPNOTIZE (Official Music Video)」。Owner 決定「確認版本」，在 Discord 案件卡片操作（見第 3 節步驟 3）；Owner 看過影片後才按。這是官方 MV，MV 與音源在版本類別視為同一錄音，**不需要另標音源版**。**必須在開自動發獎之前** | Owner |
| 認可頻道清單 | 目前有 1 個認可頻道 `UC12HMtO5MYph9dCZZ7yygng`，**尚未寫入 VM 的 `bot.env`**（第 2 節寫入）。安全規則不變：清單空白時**沒有任何歌會自動通過**（全部轉 Owner 審）。新增頻道可用 `python -m bot.channel_id_tool <影片連結>` 取得 ID | Owner |

## 1. 部署新映像（開關仍 off）

1. 管理者在 VM 重跑 `sh scripts/deploy/install-host.sh --pubkey <受限公鑰檔> --apply`，更新主機腳本（部署只換 Bot 映像，不會更新 `/usr/local/libexec/jukebox101-deploy`）。
2. 把新的 `deploy/controlled/compose.yaml` 內容放到 `/etc/jukebox101/compose.cd.yaml`（root 擁有、群組與其他人不可寫）。**`install-host.sh` 不會覆寫既有的 compose.cd.yaml，這一步必須人工做。**
3. 放好 `/etc/jukebox101/secrets/youtube_api_key.txt`（擁有者 `10001:10001`、權限 `0400`）。
   - 新 compose 一律掛載這個秘密，**開關是 off 也必須有這個檔案**，否則 `docker compose up` 會失敗。
   - 還沒決定要用真金鑰時，可先放一個內容為占位字串的檔案；開關 off 時程式不會讀它。
   - 主機腳本會在**停舊 Bot 之前**檢查：compose 掛載了這個秘密但檔案不是一般檔案，就回 `rejected` 並在日誌寫明路徑，舊 Bot 不受影響。
4. 推一個新 commit 讓 CD 部署（同一個 run 不能重送）。成功時 Actions 綠燈、Issue #7 出現 `succeeded`。
5. 驗證：容器 `healthy`；`python -m bot.healthcheck` 顯示 `ready`；`/share` 行為與以前相同（因為所有自動開關仍 off）。

## 2. 只開審核與保存（結算與版本確認仍 off）

在 root 擁有的 `/etc/jukebox101/bot.env`：

```
JUKEBOX_API_RETENTION=on
JUKEBOX_AUTO_REVIEW=on
YOUTUBE_TRUSTED_CHANNEL_IDS=UC12HMtO5MYph9dCZZ7yygng   # 目前唯一的認可頻道；新增時以逗號分隔
```

並把 `youtube_api_key.txt` 換成真金鑰（0400、10001:10001）。再推一個新 commit 重建容器（`bot.env` 只在重建時生效；不要手動重建，會繞過 CD 紀錄）。

驗證：`bot.healthcheck` 為 `ready`；認可頻道的新投稿出現「審核通過」，但 **`/balance` 不變**（結算仍 off）。

## 3. 處理首筆案件、開版本自動確認，最後才開自動發獎

啟用順序（Owner 已定）：**先開自動審核與保存並用真實 API 驗證（第 2 節）→ 處理首筆案件 → 才開自動發獎**。

1. 用真實 API 驗證第 2 節的結果（見測試計畫 5.3 步驟 6-10）：認可頻道的歌被自動核准，其他的進 `/owner list`。
2. `/owner list` 逐筆處理所有版本未確認的既有案件。
3. **首筆案件**（`discord:1565766654502113430`，「XG - HYPNOTIZE (Official Music Video)」）：Owner 在 Discord 開 `/owner list`，找到該案卡片，**看過影片後**按「確認為新的錄音版本」。這不是改資料庫，是卡片上的一般 Owner 操作；影片是官方 MV，MV 與音源在版本類別視為同一錄音，不需要另標音源版。確認後該案會依一般流程等待發獎，**須在步驟 5 開自動發獎之前完成**，否則確認當下就可能領 +1。
4. 需要版本自動比對時，`bot.env` 加 `JUKEBOX_AUTO_VERSION_CONFIRM=on`（第 2 節兩個開關已 on，否則 Bot 拒絕啟動），重建容器。驗證：無疑慮的認可頻道新歌被自動確認版本；同頻道且時長相近、歌名相似、含 remix／live／cover 等關鍵字的新歌進 `/owner list`，卡片寫明原因；清單被清空時什麼都不會自動通過。三個條件（同頻道、時長差 ≤ 3 秒、歌名高度相似）全部成立時，卡片寫「高度相同，建議不發放」，仍由 Owner 決定，不新增狀態。
5. 開自動發獎：停 Bot → `python -m bot.owner_cli settlement-switch preview --enabled on` → 用印出的 digest 執行 `write` → `bot.env` 設 `JUKEBOX_AUTO_SETTLEMENT=on` → 啟動 → `python -m bot.healthcheck`。資料庫旗標與 `JUKEBOX_AUTO_SETTLEMENT` 必須一致，否則健康檢查回報 `SETTLEMENT_*`。

## 4. 回滾（由輕到重）

| 狀況 | 做法 | 會發生什麼 |
|---|---|---|
| 懷疑自動確認有漏洞 | `bot.env` 設 `JUKEBOX_AUTO_VERSION_CONFIRM=off`，推新 commit 重建 | 新投稿都回到 Owner 確認版本；已發出的點數不收回、不移動 |
| 懷疑發獎有問題 | 停 Bot → `settlement-switch write --enabled off` → `JUKEBOX_AUTO_SETTLEMENT=off` → 啟動 | 不再自動發獎，案件停在「符合資格待結算」 |
| API 額度耗盡或金鑰異常 | 不必處理：比對與審核都不寫入、不拒絕，30 分鐘後自動重試，額度恢復後繼續 | 投稿者看到「稍後重試」，不會被拒絕 |
| 整個關閉 | 三個自動開關與 `JUKEBOX_AUTO_REVIEW` 設 off（`JUKEBOX_API_RETENTION` 最後關），重建 | 回到階段 A 之前的行為；`jb2_api_observations` 仍會依排程清除 |
| 要回到**不含 `SYSTEM_JUDGEMENT` 的舊映像**（例如 `51ee43a`） | 先跑唯讀檢查 `SELECT count(*) FROM jb2_evidence WHERE strpos(evidence_json::text, 'system_judgement') > 0;`。為 0 才是完全乾淨的回滾；大於 0 時照下方「已實測」判斷影響後再決定 | 見下方「回滾到舊映像的實測結果」 |
| 新映像不健康 | 主機腳本自動換回前一個映像，回報 `rolled_back` | 看 `docker logs`；`rolled_back` 路徑在真實主機尚未驗證過 |
| `rejected`（缺金鑰檔） | 放好 `youtube_api_key.txt` 後推新 commit | 舊 Bot 一直沒被停 |

### 回滾到舊映像的實測結果（2026-11-02，PostgreSQL 18.6 拋棄式資料庫）

做法：用新程式建立資料庫（1 支被系統自動確認版本的影片＋1 支 Owner 手動確認的影片，結算旗標先關再開），再用**基底提交 `51ee43a` 的 `bot/` 套件**對同一個資料庫操作，看哪些路徑還能用。

| 路徑（舊版程式） | 結果 |
|---|---|
| `owner.view` 有系統證據的那支影片 | **失敗：`ValueError`**（舊版不認得儲存的 `system_judgement`） |
| `owner.view` Owner 手動確認的影片 | 正常 |
| `owner.list_pending`（待處理卡片清單） | 正常 |
| `reward_state`（兩支影片） | 正常 |
| 新增投稿 `accept_pending`、`pending_validation_operations`、`ready_operations` | 正常 |
| `settle_ready`（舊版開啟結算） | 正常，兩支影片各發 1 Nickel，餘額 (1, 1) |

結論：回滾**不會**讓整個系統讀不懂資料；受影響的只有「對有系統證據的影片，舊版 Owner 工具（檢視、確認版本、重審）會 `ValueError`」。投稿、待處理清單、結算與餘額都不受影響。`preview_version_confirmation` 與重審走同一條讀取證據的路徑，依程式閱讀同樣會失敗，未逐一實測。對應自動測試：`tests/test_postgres_system_evidence_rollback.py`（在同一個行程內移除該列舉成員來模擬舊版，5 個）、`tests/test_codec_unknown_enum_value.py`（未知列舉值一律拋 `ValueError`，3 個）。上表的「用真實舊版程式」結果是一次性人工實測，未做成自動測試。

## 5. 本機驗證注意（Windows）

本機 git 設定 `core.autocrlf=true`，會讓 `deploy/host/jukebox101-deploy` 與 `scripts/*.sh` 在工作樹變成 CRLF。直接 `docker build` 會把 CRLF 複製進映像，bash 無法執行，`tests/test_deploy_host_script.py` 會**全部誤失敗**（在未修改的 baseline 上實測 25 個失敗）。CI 是 Linux checkout，不受影響。

在本機 Windows 驗證時，先把工作樹複製到暫存目錄，對文字檔做 `sed -i 's/\r$//'`，再從該目錄建 `test.Dockerfile` 映像並以 `--network none` 執行。不要為此改動 repository 內的檔案或 git 設定。

## 6. 尚未驗證的事

- 真實 YouTube API 的批次回應形狀與額度（只有 mock）。
- 真實 Supabase Session Pooler 下的行為。
- 真實 VM 上的新 compose、秘密檔權限與 `rolled_back` 路徑。
- 真實 Discord 的卡片顯示與按鈕。
- PostgreSQL 17.11：本機沒有，只在 CI 的 `--postgres-major 17` 驗。
