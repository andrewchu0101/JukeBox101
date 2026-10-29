# 擁有者秘密配置與輪替手冊

- 適用：Owner 為正式部署準備秘密、資料庫帳號與輪替。
- 原則：**秘密不得進入交付包、映像、Compose 檔、聊天或任何版本控制系統。**

## 1. 需要的秘密與設定

| 名稱 | 種類 | 放置位置 | 用途 |
|---|---|---|---|
| `discord_bot_token.txt` | 秘密 | `JB_SECRET_DIR` 之下 | Discord Bot Token。檔名沿用既有設定；內容即 Owner 手動提供的 `bot_token`（2026-10-29 核准保留檔名） |
| `db_password.txt` | 秘密 | `JB_SECRET_DIR` 之下 | Bot 應用程式資料庫帳號的密碼 |
| `SECRET_EXPECTED_UID` / `SECRET_EXPECTED_GID` | 非秘密設定 | Compose 檔已設為 10001 | 容器內秘密檔案預期的擁有者與群組（數字） |

## 2. 資料庫帳號（外部 MySQL）

1. 以維護帳號（有 DDL 權限，僅維護時使用）建立 Bot 應用程式帳號：`jukebox101_app`（名稱可配置）。
2. 依 `docs/db-least-privilege-grants.sql` 草稿審閱後授權。**不得授予 DELETE、DDL、GRANT、SUPER、FILE、PROCESS。**
3. 另建立唯讀查詢帳號 `jukebox101_readonly`（只有 SELECT），供開發者經 SSH 隧道查詢。
4. 維護帳號不放入 Bot 容器，不提供給夥伴。
5. 授權後以 `SHOW GRANTS FOR` 核對每一張表的實際授權，與草稿比對。

## 3. 建立秘密檔案（在部署主機上，由 Owner 執行）

```bash
sudo install -d -m 0750 -o root -g 10001 /etc/jukebox101/secrets
sudo sh -c 'umask 077; printf %s "$TOKEN" > /etc/jukebox101/secrets/discord_bot_token.txt'
sudo sh -c 'umask 077; printf %s "$DBPW" > /etc/jukebox101/secrets/db_password.txt'
sudo chown 10001:10001 /etc/jukebox101/secrets/*.txt
sudo chmod 0400 /etc/jukebox101/secrets/*.txt
```

- `TOKEN`、`DBPW` 由 Owner 在本機輸入或以安全方式取得，**不要寫進 shell 歷史或文件**。
- 檔案擁有者必須是容器內的數字 UID（10001），群組為 10001，權限 0400。
- 若主機沒有 UID 10001 的帳號，這是正常情況：容器內的 UID 不需要主機帳號存在。以數字設定即可。

## 4. 部署前檢查（主機來源檔）

```bash
SECRET_EXPECTED_UID=10001 SECRET_EXPECTED_GID=10001 \
  scripts/deploy/check-host-secrets.sh /etc/jukebox101/secrets/discord_bot_token.txt
  SECRET_EXPECTED_UID=10001 SECRET_EXPECTED_GID=10001 \
  scripts/deploy/check-host-secrets.sh /etc/jukebox101/secrets/db_password.txt
echo "exit=$?"
```

- 退出碼 0：通過。
- 退出碼 1：拒絕。輸出只含原因代碼與修復說明（數字與指令範本），不含秘密內容。
- 退出碼 2：無法檢查或用法錯誤。
- 檢查腳本**不會**自動修改權限或擁有者；修復必須由 Owner 手動執行。

## 5. 容器內驗證

- 容器啟動時，程式會獨立驗證掛載的秘密檔案（擁有者、群組、權限、非 root 可讀性）。這是主機檢查之外的第二道檢查，不能以主機檢查取代。
- 缺少秘密、檔案不可讀或不合格時，程序在登入前停止，日誌只顯示設定名稱與原因代碼。

## 6. 輪替

1. 在 Discord 開發者入口產生新 Token（或在資料庫以維護帳號更換密碼）。
2. 以新值覆寫秘密檔案（權限與擁有者不變），再次執行第 4 節的檢查。
3. 重新建立容器使其讀取新秘密：`docker compose -f compose.yaml up -d --force-recreate bot`。
4. 確認第 8 節的三層檢查。
5. 舊 Token 或舊密碼在新值生效後立即撤銷。

**注意**：輪替只更新秘密，不還原資料庫結構。若程式版本與結構版本不一致，先依 Migration 文件處理，不要只回滾程式。

## 7. 洩漏時的處置

1. 立即停止 Bot：`docker compose -f compose.yaml stop bot`。
2. 在 Discord 開發者入口重設 Token；在資料庫重設密碼。
3. 覆寫秘密檔案，重新執行檢查，再啟動。
4. 檢查日誌與資料庫紀錄，確認是否有非預期操作；保留證據，不要刪除日誌。

## 8. 備份與還原（責任人待確認）

- 資料庫備份：由外部 MySQL 的管理者執行，範圍包含 `jukebox101` schema 的所有表（含帳本與發放紀錄）。
- 秘密目錄：只備份到 Owner 控制的安全位置；不得與交付包一起保存。
- 還原演練：尚未在部署主機實測。
- **責任人與頻率：待 Owner 確認。**

## 9. 外部 MySQL 的安全連線（待 Owner 確認）

- 位置、TLS 設定、來源網路限制：待確認。
- 不直接公開 3306；若必須使用同機 MySQL，使用選用的同機 profile，並只綁定內部網路。
- 最低版本：MySQL 8.0.16。

## 10. 未驗證項目

- 外部 MySQL 的實際連線與 TLS：未在正式環境驗證。
- 秘密輪替的端到端流程：未在部署主機實測。

## 11. 秘密分類與保管（分三類，不得混用）

| 類別 | 內容 | 誰保管 | 放在哪裡 | 是否進 Bot 容器 |
|---|---|---|---|---|
| **A. Owner 原始秘密** | Discord Token 原始值、資料庫各帳號密碼、同機 root（若使用） | Owner 的密碼管理器 | 只在 Owner 的密碼管理器；不寫入專案、交付包或聊天 | 否 |
| **B. 雲端執行時秘密** | 主機上的秘密檔案 `discord_bot_token.txt`、`db_password.txt`（Bot 應用程式帳號） | 部署主機（由 Owner 配置） | `JB_SECRET_DIR`，專案目錄之外，權限 0400、擁有者 UID 10001 | 只掛載這兩個唯讀檔 |
| **C. 夥伴填寫的一般設定** | `DB_HOST`、`DB_PORT`、`DB_USER`、`DB_NAME`、`DISCORD_DEV_GUILD_ID`、`DISCORD_EXPECTED_APPLICATION_ID`、`DISCORD_OWNER_USER_ID`、`JB_SECRET_DIR`（路徑）、旗標 | 夥伴 | `.env`（不含任何秘密） | 以環境變數進入 |

- Guild、Application、Owner ID、主機名稱、帳號名稱與 UID／GID **不是密碼**，但仍屬於需要核對的設定，不應寫入交付包的實際值。
- 夥伴若擁有主機管理員或完整 Docker 權限，技術上可讀取類別 B 的執行時秘密。這是本設計無法排除的風險。

## 12. Owner CLI 的操作方法（維持環境變數介面）

Owner 命令列工具（`owner_cli`）目前只接受環境變數 `DB_PASSWORD`，本版本**不支援秘密檔案**。使用時請遵守：

1. **不要**把密碼放在命令列參數中（會留在 shell 歷史與程序清單）。
2. **不要**寫入 `.env`、腳本或任何檔案。
3. 這不是第三個秘密：它與 `db_password.txt` 是**同一項資料庫秘密的另一種輸入方式**（環境變數而非秘密檔）。它只存在於 Owner 的工作站單次命令環境中，**不得寫入部署包、`.env.example`、Compose 檔或任何持久保存的設定範例**。只在**單次命令的環境**中設定，命令結束後清除：

   ```bash
   # 密碼從密碼管理器的安全輸入取得，不要 echo、不要 export 到長期 shell
   read -rs -p "DB password: " DB_PASSWORD && echo
   ( export DB_PASSWORD; /path/to/operator-command ... )   # 子殼層，結束即消失
   unset DB_PASSWORD
   ```

4. 執行前後檢查：`history` 不應出現密碼；命令輸出與日誌不應含密碼（若出現，立即停止並輪替該密碼）。
5. 關閉終端或子殼層即清除程序環境；不要使用 `set -x` 或把輸出重新導向到日誌檔。

## 13. 日誌與命令列的禁止事項

- 不得把秘密放在命令列參數、環境變數的 `--env` 參數、Compose 檔、`.env` 或 shell 腳本。
- 不得把 `docker inspect` 或 `docker compose config` 的完整輸出貼到任何地方（可能含環境資訊）；只看需要的欄位。
- 日誌若出現秘密樣式內容，立即停止服務，依第 7 節處置。

## 14. 本機開發檔案（不屬於部署）

- 開發者本機的 `.env` 與 `.local/` 目錄含有測試或本機憑證鍵名與私鑰檔，**不納入交付包**。
- 這些本機檔案是否刪除，由 Owner 另行決定；本文件不要求刪除。


## 15. 本次交付給夥伴的秘密（確切名稱，不含真值）

- 兩個檔案由 Owner **另行**交給夥伴（例如以安全管道），不得進入 Git、Docker 建置內容、映像或部署包，也不得寫入文件、日誌或測試輸出。
- 夥伴收到後，放入主機上的 `JB_SECRET_DIR`，檔名必須與下表完全相同，權限 0400、擁有者 UID 10001。

| 項目 | 內容 |
|---|---|
| 交付給夥伴的秘密 | 只有兩個：Bot 專用資料庫密碼（對應 `DB_PASSWORD`）與 Discord Bot Token（對應 `DISCORD_BOT_TOKEN`） |
| 主機檔名 | `<JB_SECRET_DIR>/db_password.txt`、`<JB_SECRET_DIR>/discord_bot_token.txt` |
| 容器內路徑 | `/run/secrets/db_password`、`/run/secrets/discord_bot_token`（唯讀） |
| 程式讀取的設定 | `DB_PASSWORD_FILE`、`DISCORD_BOT_TOKEN_FILE`（已在 `compose.yaml` 設定，指向上述容器路徑） |
| 不交付的憑證 | 測試、Harness、Migration、root、Owner 個人帳號、唯讀查詢帳號、SSH 私鑰 |

**Owner 保管的備份與本機檔案**
- 本機 `.env` 與其備份：保留於 Owner 本機，**不授權移動、刪除或修改**；它們不得進入 Git、Docker build context、映像或交付包。
- 備份**不代表已加密或安全**；本文件不宣稱已驗證 Owner 的備份保護方式。
- `.local/mysql-test/` 的測試私鑰暫時保留，繼續排除於 Git、建置與交付。

**夥伴的交付前檢查**：交付包不含上述任何真值；`.env.example` 只含一般設定範例與明確假值。
