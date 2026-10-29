# 夥伴部署與更新手冊（Gateway-only，Docker Compose）

- 適用：夥伴在自己管理的 Linux amd64 主機上部署 Bot。
- 狀態：**離線交付文件**。在部署主機實測前，以下步驟只在本機 WSL 與隔離容器驗證過。

## 1. 權限與責任邊界（必讀）

- 夥伴負責：準備 Docker 與 Compose、部署與更新、檢查運行狀況。
- Owner 負責：建立 Discord 應用程式與秘密、建立資料庫帳號、輪替秘密、核准正式資料庫變更。
- **夥伴不需要、也不應接收真實秘密。** 秘密由 Owner 透過獨立安全管道配置到主機，不經交付包或聊天。
- **技術上的限制**：若夥伴擁有主機管理員或完整 Docker 權限，即可讀取容器使用的秘密。Docker secrets 不保證對主機管理員保密，也不是自動加密保管庫。
- 雲端主機歸屬、主機權限與備份責任人：部署前另行確認（見第 9 節）。

## 2. 前置需求

| 項目 | 要求 | 驗證方式 |
|---|---|---|
| 作業系統 | Linux amd64 | `uname -m` 應為 `x86_64` |
| Docker Engine | 支援 Compose v2 | `docker version` 與 `docker compose version` |
| 外部 MySQL | 最低需求 **MySQL 8.0.16**（依程式 CHECK 約束推論）；本次新增的 SQL 語法與權限在 8.0.16 **尚未實測**，目前實測版本為 8.0.46 | 由 Owner 提供連線資訊後，於部署前確認版本 |
| 秘密檔案 | 由 Owner 配置到 `JB_SECRET_DIR` 之下的兩個檔案 | 見 owner-secrets.md |
| 網路 | 主機可連到 Discord 與外部 MySQL；不需要開放任何入站連接埠 | 防火牆由主機管理者設定 |

## 3. 取得與核對交付包

1. 取得精簡交付包與其 SHA-256 值（由 Owner 提供）。
2. 核對 SHA-256：

   ```bash
   sha256sum jukebox101-delivery.tar.gz
   ```

   結果必須與 Owner 提供的值完全相同；不符則停止，不要解壓。

3. 解壓到一個專用目錄，例如 `/opt/jukebox101`。

## 4. 設定（非秘密）

1. 在解壓目錄建立 `.env`（**只放非秘密設定**，不得放入 Token 或密碼）：

   ```bash
   DB_HOST=<外部 MySQL 主機名稱或位址>
   DB_PORT=3306
   DB_USER=<Owner 提供的應用程式帳號名稱>
   DB_NAME=<Owner 提供的應用程式 schema 名稱>
   DISCORD_DEV_GUILD_ID=<私人伺服器 ID>
   DISCORD_EXPECTED_APPLICATION_ID=<Discord 應用程式 ID>
   DISCORD_OWNER_USER_ID=<Owner 的 Discord 使用者 ID>
   JB_SECRET_DIR=<Owner 配置秘密的主機目錄，例如 /etc/jukebox101/secrets>
   JUKEBOX_FORMAL_EXCHANGE=off
   JUKEBOX_NICKEL_CLAIM=off
   ```

2. `JUKEBOX_FORMAL_EXCHANGE` 與 `JUKEBOX_NICKEL_CLAIM` 預設為 `off`。**本次部署不開啟**，除非 Owner 另行核准。
   - 正式 `/exchange`、`/claim` 與補償功能**目前尚不可啟用**：交換的 COMMIT 未知恢復與補償路徑仍有已知缺陷，未在真實 Discord 或正式 MySQL 驗收。啟用前須另行修正、驗收並取得 Owner 核准。詳見 `PACK-README.md`「尚不可啟用的功能」。
   - `JUKEBOX_NICKEL_CLAIM=off` 時，`/claim` 指令**不會註冊**，Discord 中看不到該指令；它不是可直接使用的功能。
   - 開啟 `/claim` 需要 Owner 另行核准，並需要指令同步（本手冊不包含指令同步步驟）。
3. `.env` 不得提交到任何版本控制系統。

## 4A. 秘密配置（由 Owner 另行提供，夥伴不接收真值）

Owner 會以獨立安全管道另外提供兩個執行時秘密，**夥伴只負責把它們放到指定位置並設定權限**。文件與交付包不含任何真值。

| 項目 | 內容 |
|---|---|
| 原始設定名稱 | `DISCORD_BOT_TOKEN`、`DB_PASSWORD`（僅 Owner 側使用，夥伴不需要設定這兩個名稱） |
| Compose 使用的檔案型設定 | `DISCORD_BOT_TOKEN_FILE=/run/secrets/discord_bot_token`、`DB_PASSWORD_FILE=/run/secrets/db_password`（已寫在 `compose.yaml`） |
| 主機檔名（在 `JB_SECRET_DIR` 之下） | `discord_bot_token.txt`、`db_password.txt` |
| 容器內掛載路徑 | `/run/secrets/discord_bot_token`、`/run/secrets/db_password`（唯讀） |
| 主機檔案權限 | 0400，擁有者 UID 10001（數字），群組 10001 |
| 設定方式 | 在 `.env` 設定 `JB_SECRET_DIR=<主機秘密目錄>`；`compose.yaml` 會把上述兩個檔案掛載為容器內唯讀秘密 |

**不屬於夥伴的憑證（不得提供、不得使用）**
- 測試、Harness、Migration、root 憑證。
- Owner 個人帳號憑證。
- 唯讀查詢帳號與維護帳號的密碼。

**注意**
- `.env` 只放一般設定（主機、帳號名稱、ID、路徑、旗標）。**不得放 Token 或密碼。**
- 夥伴不得把秘密值貼到聊天、Git、映像、Docker build context 或交付包中。
- 夥伴若擁有主機管理員或完整 Docker 權限，技術上可讀取這兩個執行時秘密（不宣稱 Docker secrets 對主機管理員保密）。

## 5. 建置映像

```bash
cd /opt/jukebox101
docker compose -f compose.yaml build
```

建置失敗時，保留完整輸出並回報，不要修改 Dockerfile 以繞過錯誤。

## 6. 啟動前檢查（不啟動服務）

```bash
docker compose -f compose.yaml config --quiet && echo "設定有效"
docker compose -f compose.yaml create
```

檢查輸出：設定有效；不應出現任何真實秘密值。

## 7. 啟動

```bash
docker compose -f compose.yaml up -d
```

啟動後，**容器已建立不等於 Bot 已可用**。依第 8 節逐項確認。

## 8. 運行狀況檢查（分三層）

| 層次 | 檢查方式 | 通過的意義 | 不代表 |
|---|---|---|---|
| 1. 容器存活 | `docker compose -f compose.yaml ps` 狀態為 running | 程序仍在執行 | 設定正確、Discord 已連線 |
| 2. 設定與秘密 | `docker compose -f compose.yaml logs --tail=50 bot` | 沒有 `secret file refused` 或 `Missing required setting` 訊息 | Discord 已連線 |
| 3. Discord 連線 | 日誌出現登入成功的記錄，且私人伺服器中 `/balance` 有回覆 | Bot 已可用 | 功能全部正確（需依驗收清單） |

- 健康檢查在本次交付中**只涵蓋第 1、2 層**；第 3 層需要人工在私人伺服器確認。
- 日誌不會輸出秘密值；若日誌中出現疑似秘密的內容，立即停止服務並通知 Owner。

## 9. 日常操作

| 操作 | 指令 |
|---|---|
| 查看日誌 | `docker compose -f compose.yaml logs -f --tail=200 bot` |
| 停止 | `docker compose -f compose.yaml stop bot`（給予 30 秒優雅關閉時間） |
| 重新啟動 | `docker compose -f compose.yaml restart bot` |
| 更新程式 | 取得新版交付包 → 核對 SHA-256 → 解壓覆蓋程式（**不覆蓋 `.env` 與秘密目錄**）→ `build` → `up -d` |
| 回滾 | 保留上一版交付包與映像；解壓上一版程式並 `up -d`。注意：資料庫結構若已升級，回滾程式不會還原結構，見 owner-secrets.md 第 6 節 |

## 10. 資源與副本

- Compose 已設定每個 Bot 容器 1 CPU、512 MiB 記憶體上限（已在本機以 `docker inspect` 確認設定寫入容器）。
- **只能單一副本。** 業務狀態可持久化，但 Gateway 與背景工作的多副本協調未驗證。不要執行 `docker compose up --scale bot=2`。

## 11. 未驗證與待確認項目

- 部署主機上的實際行為未實測（本文件的驗證來自本機 WSL 與隔離容器）。
- 秘密傳輸管道、主機實際秘密目錄、備份與還原責任人：部署前由 Owner 確認。
- 外部 MySQL 的位置、TLS 設定、網路限制：部署前由 Owner 確認。
- SIGTERM 關閉（2026-10-30 整合版，內部網路無外連，假值，正式入口）：斜線指令的資料庫工作會被等待最多 15 秒；未完成時取消並以退出碼 3 結束（三次實測約 15.06 秒）；指令已完成時退出碼 0。未提交交易會回滾（實測）。COMMIT 結果未知的端到端關閉情境未涵蓋（見 `VERIFICATION-2026-10-30.md` 第 10 節）。


## 12. 授權與第三方聲明

- 專案本身的開源授權（LICENSE）**尚未決定**，待 Owner 確認；本交付包不包含專案授權檔。
- 第三方依賴的授權資訊見 `docs/THIRD-PARTY-LICENSES.md`，由已安裝套件的中繼資料產生；**這不是授權文字的完整副本**。
