# Docker Gateway 部署工作票索引

- 來源規格：[docker-deployment-spec.md](../../docker-deployment-spec.md)
- 需求輸入：[docker-deployment-requirements-input.md](../../docker-deployment-requirements-input.md)
- 日期：2026-10-28
- 狀態：工作票已拆分並保存，尚未開始實作。
- 問題追蹤器：未設定，本目錄即為本機工作票（取代 `.scratch/`，因為需求要求持久保存於專案內）。
- 注意：工作票內的檔案路徑與「單一檔案負責人」是為了讓實作分工明確，屬於工作票範圍，不違反規格原則。

## 主要測試邊界與輔助驗證

| 類別 | 工作票 | 說明 |
|---|---|---|
| 主要邊界 1：設定與秘密讀取 | 01、02、03 | 秘密來源、權限判定、啟動拒絕 |
| 主要邊界 2：正式 Gateway 用戶端建構與 Owner 接線 | 03、04、05 | 正式入口是否註冊 `/owner`、非 Owner 拒絕、`/daily` 行為 |
| 輔助：容器離線驗證 | 09、10、11 | 建置、非 root、SIGTERM、資源限制、Compose 掛載權限 |
| 輔助：帳本一致性 | 07 | 禁止直接改表補發 Nickel |

## 依賴順序

```
01 秘密讀取 ─┐
             ├─> 03 啟動秘密拒絕 ─> 04 Owner 接線 ─┐
02 權限判定 ─┘                                     │
                 03 ─> 09 映像建置 ─> 10 容器離線驗證 ─> 11 Compose 與 MySQL profile ─┬─> 12 交付包與掃描 ─> 13 部署文件
                                                                                  ├─> 14 資源量測
08 唯讀查詢文件（獨立）                                                              │
07 帳本寫入守衛（獨立）                                                              │
16 Discord 政策矩陣（獨立）                                                          │
05 /daily 正式入口說明（待文案核准） ───────────────────────────────────────────────┘
06 Nickel 文案統一（待逐句文案核准）

15 正式連線驗收（需另行授權，不執行）：依賴 04、10、11、12、13、14
```

## 可立即開始的工作票（frontier）

- 01、02、07、08、16：無阻塞，可平行。
- 06、05：需先取得 Owner 逐句文案核准，但不阻擋其他工作。

## 單一檔案負責人一覽

| 工作票 | 負責檔案（只有該票可修改） |
|---|---|
| 01 | `bot/config.py`、`tests/test_secret_file_reading.py`（新） |
| 02 | `bot/secret_file_policy.py`（新）、`tests/test_secret_file_policy.py`（新） |
| 03 | `bot/__main__.py`、`tests/test_entry_secret_refusal.py`（新） |
| 04 | `bot/__main__.py`（在 03 之後）、`tests/test_gateway_entry_owner_wiring.py`（新） |
| 05 | `bot/discord_app.py`（僅 `/daily` 描述字串）、`tests/test_daily_formal_entry.py`（新） |
| 06 | `bot/services.py`、`bot/exchange_delivery.py`、`bot/discord_owner_review.py`（僅 Nickel 字樣）、相關測試（新增，不改既有斷言含義） |
| 07 | `tests/test_ledger_write_guard.py`（新） |
| 08 | `docs/database-access.md`（新） |
| 09 | `Dockerfile`（新）、`.dockerignore`（新）、`requirements-docker.txt`（新，由既有鎖定產生） |
| 10 | `scripts/docker-verify.sh`（新）、`docs/docker-verification.md`（新） |
| 11 | `compose.yaml`（新）、`compose.mysql.yaml`（新，選用 profile） |
| 12 | `scripts/delivery/build-package.sh`（新）、`scripts/delivery/scan-package.sh`（新） |
| 13 | `docs/deploy/partner-deployment.md`（新）、`docs/deploy/owner-secrets.md`（新）、`deploy/RUNBOOK.md`（僅在最上方加入「未採用」標示，內文不動） |
| 14 | `docs/resource-measurement.md`（新） |
| 15 | 無（不執行） |
| 16 | `docs/discord-policy-matrix.md`（新） |
| 24 | 本票新增 `tests/test_exchange_restart_recovery.py`、`tests/test_exchange_restart_recovery_mysql.py`；另改既有測試期望值與夾具（實際範圍見工單 24 File owner，2026-10-29 登記）。不改 `bot/` 產品程式 |

## 文件差異記錄（本次工作票依此採用）

| 項目 | 舊文件 | 目前程式 | 本次採用 |
|---|---|---|---|
| Spotify 投稿 | MVP 規格要求接受並以 pending 處理 | `bot/urls.py` 只接受 YouTube，拒絕 Spotify | **YouTube-only**，不擴大範圍；差異記錄於此，待 Owner 日後決定 |
| `/daily` 偏好 | MVP 規格要求 opt-in 每日 DM | 正式入口只回覆訊息，不保存偏好 | 依程式行為；指令描述文字需修正（見 05） |
| 貨幣用詞 | 文件與程式混用「點數」與「Nickel」 | 回覆中同時存在 | **統一 Nickel**（Owner 已決定；逐句文案待核准，見 06） |
| 正式入口 Owner 接線 | 規格要求 `/owner` 可用 | `bot/__main__.py` 未傳入 Owner 接線 | 補上（見 04） |
| HTTP 部署文件 | `deploy/RUNBOOK.md` 等為 HTTP 方案 | 本次為 Gateway-only | 方案 A：保留並標示未採用（見 13） |
| 秘密讀取 | 文件假設可由 `_FILE` 自動生效 | 程式只讀環境變數 | 新增明確檔案讀取（見 01） |

## 「已開放」狀態三層區分

| 功能 | 程式已接線（正式入口） | 部署設定允許 | 已通過真實驗收 |
|---|---|---|---|
| `/share`（YouTube） | 是 | 部署後預設可用 | 否 |
| `/balance` | 是 | 是 | 否 |
| `/history` | 是 | 是 | 否 |
| `/daily` | 是（只回覆，不保存） | 是 | 否 |
| `/exchange` | 是（由旗標控制） | 否（`JUKEBOX_FORMAL_EXCHANGE` 預設關閉，本次不開） | 否 |
| `/owner` | 否（待 04 完成） | 需 Owner 設定私人伺服器與 ID | 否 |
| 每日 DM 派送 | 否 | 否 | 否 |

## 待 Owner 決定（正式部署前，不阻擋離線開發）

- 秘密傳輸方式、主機實際秘密路徑、備份責任人（見 13、15）。
- 秘密權限過寬的嚴格程度（見 02，建議值已列出）。
- 逐句回覆文案核准（見 05、06）。
- 是否授權正式 Discord 與 MySQL 連線驗收（見 15）。

## 工單實際狀態（2026-10-28 檢查點，依實測證據，不代表定版）

| 狀態 | 數量 | 工單 |
|---|---|---|
| 已完成（離線或隔離實測） | 13 | 01、02、03、04、05、06、07、16、17、21、23、26、27 |
| 部分完成 | 9 | 09、10、11、12、13、14、18、19、25 |
| 未完成 | 3 | 08（docs/database-access.md 未建立）、22（owner 權杖安全失效測試未寫）、24（交換恢復的真實 MySQL 驗證未做） |

> **2026-10-29 更正**：上表為 2026-10-28 檢查點紀錄，已過時。工單 24 已完成離線與專用隔離 MySQL 驗證（隔離套件 60 項通過），仍未執行正式環境驗收。08 與 22 的狀態以各自工單為準，本輪未核對。

> **2026-10-29 工作樹與工單對應**：工作樹中的 `bot/secret_file_policy.py` 屬於工單 02，`bot/__main__.py` 屬於工單 03 與 27。它們不是工單 24 的產出。`bot/__main__.py` 的行尾由 CRLF 整檔改為 LF，待處理，見 `CONTINUATION-2026-10-28.md` 文末。
| 待授權或待決定 | 2 | 15（正式連線，未授權）、20（/claim 文案，待 Owner 核准） |

合計 27。「已完成」指離線或隔離實測通過；不代表正式環境已驗證。
