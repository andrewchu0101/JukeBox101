# HTTP Interactions 部署手冊（候選，尚未執行）

**狀態**：本文件供部署前審閱。目前沒有任何雲端資源、服務、Portal 設定、資料庫結構或 Discord 指令已因本文件建立。所有 `<PLACEHOLDER>` 由部署者替換。

**兩道不同的門檻（不可混用）**

| 門檻 | 條件 | 本手冊的範圍 |
|---|---|---|
| 交換關閉的 HTTP 首次部署 | `JUKEBOX_FORMAL_EXCHANGE` 未設定；`/exchange` 維持固定關閉回覆；不執行真實交換、不發送真實 DM；首次檢查不執行會寫入資料的指令 | **本手冊涵蓋** |
| 開放真人交換 | 需另行核准附錄所列缺口的處理流程，並需使用者明確批准開放；Beta 交換與補償目前皆關閉 | **不在本手冊範圍**，需另案決定 |

**授權方式**：採分階段、整批明確授權。使用者於每個階段開始時一次批准該階段內的全部命令與目標；執行中不逐條重複確認。遇到以下情況才停止並回報：費用超出 Always Free 範圍、目標（帳號、區域、主機、網域、資料庫）與批准不符、權限需要超過批准範圍，或任何步驟的結果與預期不符。

---

## A. 雲端資源與環境準備

每一項都需要使用者於 Oracle 主控台確認，或另行授權後才執行。

| 項目 | 具體設定 | 來源與狀態 |
|---|---|---|
| 帳號與區域 | 註冊時選定 home region。運算實例與 Always Free 資料庫只能建在 home region | 官方：[免費方案說明](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm)。**home region 不可更改**：依使用者確認（本輪未於官方頁面重新查證）。註冊前必須確認區域 |
| 付款驗證 | 大多數使用者需要手機與信用卡；不升級就不收費 | 官方同上。**本輪未取得最新的主控台畫面，需註冊時確認** |
| VM（A1） | `VM.Standard.A1.Flex`，合計不超過 2 OCPU、12 GB；試用期間超出會被停用，30 天後刪除 | 官方資源表與免費說明。**本輪未確認目前配額是否已對您的帳號生效** |
| VM（AMD 替代） | `VM.Standard.E2.1.Micro`，最多 2 台，每台 1 GB | 官方資源表。記憶體偏小，需實測 |
| 儲存 | 區塊儲存合計 200 GB、5 個備份 | 官方資源表。開機卷最小值官方頁有 47 GB 與 50 GB 兩種說法，**以主控台顯示為準** |
| HeatWave | `MySQL.Free` shape，1 台，固定 50 GiB，InnoDB、MySQL EE 8.0 以上 | 官方：[HeatWave 說明](https://docs.oracle.com/en-us/iaas/mysql-database/doc/features-mysql-heatwave-service.html)。**不保證 HA、讀取副本；本輪未取得其他細節** |
| 容量不足 | 若 A1 建立失敗，可能需換可用區、改用 AMD 替代或稍後重試 | **官方頁未取得，未知** |
| 閒置回收 | 7 天內 95 分位 CPU、網路，A1 另加記憶體皆低於 20% 時，實例可能被回收 | 官方資源表。**對低流量 bot 有實際風險，本輪未決定如何處理** |

### 網路（VCN）

- **公開子網（VM）**
  - 入站 TCP 443：對網際網路開放，因為 Discord 會從網際網路呼叫端點。**Discord 來源 IP 未確認，不做白名單。**
  - 入站 TCP 80：若反向代理使用 HTTP 驗證取得憑證，需要開放。**未在本輪官方來源確認，需依所選反向代理的文件核對。**
  - 入站 TCP 22：只允許操作者的來源 IP，並僅限金鑰登入。
  - 出站：需能連到 `discord.com`（REST）。**預設路由與出站流量免費額度未查證。**
- **私有子網（HeatWave）**
  - 入站 TCP 3306：只允許 VM 所在子網的 CIDR。
  - 官方說明：DB 端點不能從網際網路直接連線，必須經由同一 VCN 內的運算實例、VPN、Bastion 或負載平衡器。[連線說明](https://docs.oracle.com/en-us/iaas/mysql-database/doc/connecting-db-system1.html)
  - **是否可指定私有 IP、安全清單的實際設定畫面：未在官方頁取得，需於主控台確認。**

### HTTPS 網域

- 需一個可控制的網域，A 紀錄指向 VM 公開 IP。
- 反向代理（Caddy 或同等工具）負責 TLS，應用程式只聽 `127.0.0.1:8080`。
- **Discord 允許的埠與 TLS 版本要求：本輪未逐項查證，以主控台設定與實測為準。**

### 秘密注入

- 環境檔：`/etc/jukebox101/bot.env`，擁有者 root，權限 0600，由 systemd `EnvironmentFile=` 讀取。
- 欄位：`DISCORD_PUBLIC_KEY`、`DISCORD_BOT_TOKEN`、`DISCORD_DEV_GUILD_ID`、`DISCORD_EXPECTED_APPLICATION_ID`、`DISCORD_OWNER_USER_ID`、`DB_HOST`、`DB_PORT=3306`、`DB_USER`（應用程式帳號）、`DB_PASSWORD`、`DB_NAME`。**不設定 `JUKEBOX_FORMAL_EXCHANGE`。**
- 秘密不寫入版本庫、不貼入聊天、不放在命令列參數。
- 替代方案 OCI Vault：**未查證，本手冊不採用。**

### 主機與程序身分

- Linux 主機，Python 3.12（`pyproject.toml` 要求 `>=3.12,<3.13`）。
- 系統使用者 `jukebox101`，無登入 shell；程式目錄由 root 擁有，服務帳號只讀。
- 單一 systemd 單元，單一實例。

---

## B. 部署前最終檢查（尚不連 DB、Discord 或 Portal）

1. **部署來源**：必須是已審閱並提交的 commit，不得使用工作樹。目前 HEAD `92f2fd6` 不含本輪 HTTP 變更；**是否提交 HTTP 變更到分支，需使用者決定。**
2. **依賴安裝（Linux）**：不得直接使用 `dependency-versions.txt` 整包安裝，理由見下方。
3. **離線測試**：在 Linux 3.12 的 venv 內執行三個 HTTP 測試檔（見「Linux 驗證」）。
4. **啟動拒絕檢查**（不開 socket、不連 DB）：
   - `python -c "import bot.http_app"` 應成功匯入。
   - 在沒有 `DISCORD_PUBLIC_KEY` 的環境中執行 `python -m bot.http_app`，應立即以 `ConfigurationError` 結束，且訊息只含變數名稱。
5. **版本庫衛生**：`git status` 不得含 `.env`、`.local/`、虛擬環境或快取；`.gitignore` 已含 `/.local/`。
6. **Owner 與 Gateway**：Owner CLI（`bot/owner_cli.py`）與 Gateway 入口（`bot/__main__.py`）不屬於本次部署變更。

### 依賴說明（Linux 安裝清單）

| 套件 | 版本範圍／釘選 | 平台與相容性 |
|---|---|---|
| discord.py | `>=2.4,<3`；清單釘選 2.7.1 | 純 Python 為主；`audioop-lts` 只在 Python 3.13 以上才需要，3.12 不需要 |
| aiohttp | `>=3.14,<4`；釘選 3.14.5（需 Python 3.10 以上） | 需確認 Linux 的 wheel，**本輪未在 Linux 驗證** |
| PyNaCl | `>=1.5,<2`；候選釘選 1.6.2（需 Python 3.8 以上） | 使用 abi3 wheel；**Linux wheel 未在本輪驗證** |
| cffi | PyNaCl 1.6.2 的傳遞依賴；釘選 2.1.1（需 Python 3.10 以上） | 需確認 Linux wheel |
| pycparser | cffi 的傳遞依賴；釘選 3.12 | 純 Python |
| PyMySQL | `>=1.1,<2`；釘選 1.2.3 | 純 Python |
| python-dotenv | `>=1,<2`；釘選 1.2.4 | 純 Python；應用程式只在明確傳入路徑時載入，正式入口不讀取 `.env` |
| colorama | 清單中的 0.4.6 | **只因 pytest 在 Windows 才需要**（官方 METADATA 的 `sys_platform == "win32"` 條件）。Linux 不安裝 |
| pytest | 8.4.2 | 只屬測試依賴（`[test]` extra），不屬於執行時依賴 |

**打包注意**：`pyproject.toml` 沒有 `[build-system]` 或 `[tool.setuptools]` 段落。`pip install .` 的結果**未驗證**，平面佈局可能誤判 `deploy/`、`docs/`、`scripts/` 為套件。建議部署時在 venv 內直接安裝上表的執行時依賴，待使用者核准後再補打包設定。

---

## C. 經使用者確認後的部署與資料庫建置

每一步需使用者分別確認，不得連續執行。

1. **建立 HeatWave**：`MySQL.Free`、home region、私有子網、admin 帳號只給 DBA 使用，不給應用程式。
2. **建立應用程式帳號**：僅有必要的 DML 權限，不含 DDL 與 GRANT。migrator 另建一個帳號，只在 C3 期間使用，之後停用。
3. **套用結構**：人工審查後，依序套用 `bot/database/schema.sql`、`bot/database/migrations/002_submission_economy.sql`、`bot/database/migrations/003_exchange_delivery.sql`。先確認 MySQL 版本 ≥ 8.0.16（CHECK 限制）。**CHECK 與 `SELECT ... FOR UPDATE` 在 HeatWave 上的行為本輪未驗證，需要在一次性 schema 中單獨確認，且需另行授權。**
4. **部署程式**：從已審閱的 commit 取得程式碼到 `/opt/jukebox101/releases/<commit>`，建立 venv，安裝 B 節的執行時依賴。
5. **寫入環境檔**：依 A 節的欄位，不在聊天中貼出任何值。
6. **檢查**：執行 B 節的啟動拒絕檢查與三個離線測試（在目標主機上）。

---

## D. Portal 切換與最小啟動確認

Discord 官方說明：應用程式只能二選一接收互動，Gateway 與 Interactions Endpoint URL **互斥**。儲存 Endpoint URL 時，Discord 會對該 URL 送出 PING，驗證簽章通過才會儲存。[概覽](https://docs.discord.com/developers/interactions/overview)、[接收與回應](https://docs.discord.com/developers/interactions/receiving-and-responding)

### D1. 切換前準備（Gateway 仍在運作）

1. HTTP 服務已啟動，且**無簽章的 POST** 得到 401。注意：路由只接受 POST，**不可用 GET 測試**。
   ```
   curl -sS -X POST -o /dev/null -w "%{http_code}\n" https://<DOMAIN>/interactions
   ```
   預期輸出 `401`。
2. TLS 有效：`curl -vI https://<DOMAIN>/interactions` 能完成握手。
3. 記下目前 Portal 的 Interactions Endpoint URL（通常為空）。**Portal 清除此欄位的實際行為本輪未驗證，需於本階段人工確認。**

### D2. 停止 Gateway，再儲存端點

1. 停止 Gateway 程序，並確認已無 `python -m bot` 程序。
2. 在 Developer Portal 儲存 Interactions Endpoint URL 為 `https://<DOMAIN>/interactions`。
3. 若儲存成功，表示 PING 已通過驗證。若失敗，**保持 Gateway 停止**，依 Portal 的錯誤訊息與 HTTP 服務日誌排查，不同時啟動兩者。

### D3. 最小啟動確認（只執行無副作用或唯讀的指令）

1. `/exchange`：應回覆固定關閉訊息，不扣點、不發 DM。
2. `/balance`：唯讀，應先出現 Discord 載入狀態，再收到私人回覆。
3. `/history`：唯讀，同上。
4. **不執行** `/share`（會寫入真實資料庫）與 `/daily`（會寫入偏好）。若要執行，需另行決定。
5. 檢查 `journalctl -u jukebox101-http`：只出現類別名稱等脫敏訊息，不含 token、URL 或 payload。

### D4. 回復到 Gateway

1. 先停止 HTTP 服務：`systemctl stop jukebox101-http`，確認 inactive。
2. 在 Portal 清除 Interactions Endpoint URL（依 D1 第 3 步人工確認的方式）。
3. 啟動 Gateway 程序，依 Gateway 既有的啟動檢查確認連線。
4. **不得**在 HTTP 服務仍運作時啟動 Gateway，也不得在 Gateway 仍運作時保留 Endpoint URL。

---

## E. 更新與回復

### E1. 更新

1. `systemctl stop jukebox101-http`，確認 `systemctl is-active` 回 `inactive`，且 `pgrep -af bot.http_app` 無結果。
2. **若 stop 逾時或回報錯誤，不啟動新版**，先查明仍在運作的程序。
3. 將新的已審閱 commit 放到新的 release 目錄，再切換 `current` 符號連結指向它。
4. 啟動服務，重複 D1 第 1 步的 POST 401 檢查。

### E2. 回復

1. 保留上一版的 release 目錄。
2. 先停止服務並確認程序已結束。
3. 將 `current` 指回上一版，啟動服務，重複 401 檢查。
4. **不修改資料庫，不重跑 migration，不手動改寫 `reserved`、`sending`、`uncertain` 狀態。** 這些狀態依附錄的既有規則人工查核。

---

## 附錄：交換開放前仍待處理的既有缺口

以下為既有程式與文件已記錄的缺口。本手冊**不設計**退款、解鎖或恢復流程，只列出阻擋開放的項目。

1. **`reserved` 滯留**：已扣點並保留推薦、但未被領取的列，超過 10 分鐘（`DELIVERY_LEASE`）即列為滯留（`bot/database/pending_review.py:13`）。此後同一使用者的新 `/exchange` 會被拒絕，**本服務沒有解除路徑，需要另行核准的 Owner 流程**。
2. **`sending` 回收只在下次請求觸發**：`recover_interrupted_deliveries` 只在任何使用者的下一次 `/exchange` 開頭執行（`bot/exchange_delivery.py:179`）。停止服務後若無人發出 `/exchange`，狀態會維持 `sending`。
3. **`uncertain` 無解除入口**：結果未知的交換不解鎖、不退款、不重送；目前沒有人工解除的程式路徑。
4. **中斷的影響**：SIGTERM 或程序崩潰可能留下上述 1 與 2 的狀態。這是資料庫狀態問題，不只是使用者看到的載入失敗。
5. **補償關閉**：補償三表不在 2b2 新 schema 中，補償路徑未測試（驗收文件 §21.5）。
6. **Beta 交換未開放**：`MusicService.exchange` 回覆固定文字；`JUKEBOX_FORMAL_EXCHANGE` 預設關閉（`bot/services.py` 第 77–105 行）。
7. **功能範圍限制：每日派送未啟用**：`/daily` 只保存偏好或回覆說明，不排程、不發 DM。這是範圍限制，不是本次修復項目。
8. **功能範圍限制：Spotify 投稿**：目前被拒絕，與產品文件 §9 的要求不一致（ROADMAP 已記錄）。這是範圍限制，不是本次修復項目。
9. **功能範圍限制：Discord 內 Owner 審核**：不在目前範圍。Owner 審核只能以 CLI 執行，需要 SSH 到 VM。HTTP 路徑沒有 Discord 內的 Owner 審核。
10. **HTTP 路徑未在真實 Discord 驗證**：簽章、PING、DM 與私人回覆都只有離線替身測試。既有真人驗收（驗收文件 §21）是 Gateway 路徑在隔離 schema 上完成，**不能視為 HTTP 路徑的驗收**。
11. **文案待辦**：使用者回覆末尾的內部「操作 N」文案尚未修改（驗收文件 §21.5）。

---

## Linux 驗證（只做離線部分）

**安裝方式（已採用）**：在專用 venv 內，以 `pip install --only-binary=:all: --no-cache-dir` 安裝直接依賴並釘選版本：`discord.py==2.7.1`、`aiohttp==3.14.5`、`PyNaCl==1.6.2`、`PyMySQL==1.2.3`、`python-dotenv==1.2.4`；測試另加 `pytest==8.4.2`。傳遞依賴由 pip 解析。不使用 Windows freeze 清單，不包含 colorama。`--only-binary` 使安裝在缺少 wheel 時直接失敗，而不是悄悄編譯。

**本輪實際驗證（WSL Ubuntu 26.04，x86_64，Python 3.12.15，全部 wheel）**：
- 三個 HTTP 測試檔：44 passed，0 failed，0 skipped。
- 啟動拒絕檢查（隔離子程序，只帶測試用假值，不載入 `.env`）：缺 `DISCORD_PUBLIC_KEY` 時輸出 `Missing required setting: DISCORD_PUBLIC_KEY`，退出碼 1；空環境時輸出 `Missing required setting: DISCORD_BOT_TOKEN`，退出碼 1。兩者都在任何連線或監聽之前結束，8080 埠沒有監聽。

**驗證限制**
- 本輪只在 **x86_64** 上驗證。Oracle A1 是 **ARM64（aarch64）**，ARM64 的 wheel 可得性與執行結果**未驗證**。本輪不建立模擬環境。
- 只跑三個 HTTP 測試檔，不做全套回歸，不做真人驗證。
- 所有測試以替身驗證，沒有連線真實 DB、Discord、Oracle 或 Portal。

不安排整套回歸，不安排真人重驗，不啟動服務。
