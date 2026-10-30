# JukeBox101 v7 部署手冊（PostgreSQL 18 + 多 Discord 伺服器，Gateway-only）

> 這是 v7 唯一的部署入口。`docs/deploy/partner-deployment.md`、`owner-secrets.md`、`handoff-2026-10-30.md` 是 v6／MySQL 時期的文件，**不適用 v7**。
>
> 驗證程度（2026-10-30）：
> - **已實測**：程式在離線測試，以及 PostgreSQL 18.6 拋棄式資料庫（含 TLS verify-full、migration、最小權限、備份與還原）上通過；正式映像對測試庫的 migrate 與 readiness 也已執行；Compose 以 `--dry-run` 驗證。
> - **未實測**：真實 Discord 互動、全新伺服器邀請後實際使用，以及正式外部 PostgreSQL。未實測的項目不得視為已驗收。

## 1. 分工

| 角色 | 負責 |
|---|---|
| Owner | Discord Developer Portal 設定、Bot Token、全域指令同步、邀請連結、Owner 審核 |
| DBA（可能就是 Owner） | 建立外部 PostgreSQL、TLS 憑證、角色與密碼、網路限制、備份 |
| 夥伴（部署者） | 主機、Docker Compose、放置秘密檔、migration 指令、啟動、監看、更新 |

秘密只用獨立安全管道交付，不放進聊天、Git、映像或交付包。若夥伴擁有主機管理員或完整 Docker 權限，技術上就能讀取主機上的秘密檔；這是本設計無法排除的風險。

## 2. 需求

| 項目 | 需求 |
|---|---|
| 主機 | Linux amd64、Docker Engine 加 Compose v2；只需要對外連線（Discord 與 PostgreSQL），不需要開放任何入站連接埠 |
| PostgreSQL | **18.x**（實測 18.6）。其他版本未驗證 |
| TLS | 伺服器憑證的主機名稱必須等於 `DB_HOST`（`verify-full` 會驗證）；另外要有簽發該憑證的 CA 檔（PEM） |
| 網路 | PostgreSQL 只允許 Bot 主機連線（防火牆或私有網路），不要對整個網際網路開放 5432 |
| 實例 | **只能單一 Gateway 實例**；換主機時，先停舊的再啟新的 |

映像內的 libpq 是 Debian bookworm 的 15.x 版，可以連線 PostgreSQL 18，但不支援 `sslrootcert=system`。使用公開 CA 時，把 `JB_DB_CA_FILE` 指向主機上的系統 CA bundle 檔案即可。

## 3. Discord Developer Portal（Owner 手動完成，程式無法代勞）

1. **Installation**：開啟 **Guild Install**，關閉 User Install；Install Link 可以留空，改用第 9 節產生的連結。
2. **Bot**：開啟 **Public Bot**，否則只有 Owner 能把 Bot 加入伺服器。Privileged Gateway Intents 全部保持關閉。
3. **General Information**：**Interactions Endpoint URL 必須留空**（Bot 走 Gateway）。記下 **Application ID**。
4. **Bot Token**：Reset Token 後，直接寫入主機上的 `discord_bot_token.txt`（第 5 節）。不要貼到任何地方，也不要寫進邀請連結。

## 4. PostgreSQL 準備（DBA）

1. 以管理者在維護資料庫執行 `docs/deploy/postgres-setup.sql`。它會建立：
   - 群組角色 `jukebox101_app_role`、`jukebox101_readonly_role`。
   - 登入角色 `jukebox101_migrator`、`jukebox101_app`、`jukebox101_readonly`。
   - 資料庫 `jukebox101`，以及由 migrator 擁有的 schema `jukebox101`。
   - 撤銷 PUBLIC 的預設權限。
2. 用 `\password` 互動設定三個登入角色的密碼，不要寫在指令或檔案中：
   - app 的密碼放進 `db_password.txt`。
   - migrator 的密碼放進 `db_migrator_password.txt`。
   - readonly 的密碼只給維護人員，不交給 Bot。
3. 開啟 TLS（`ssl=on`），並在 `pg_hba.conf` 只允許 Bot 主機以 `hostssl` 和 `scram-sha-256` 連線。
4. Bot 只使用 `jukebox101_app`。它不是 superuser，也不能 DELETE、TRUNCATE 或修改 schema（已實測）。

## 5. 秘密檔與設定（主機）

| 主機檔案（`JB_SECRET_DIR` 之下） | 內容 | 使用者 |
|---|---|---|
| `discord_bot_token.txt` | Bot Token | bot |
| `db_password.txt` | `jukebox101_app` 密碼 | bot |
| `db_migrator_password.txt` | `jukebox101_migrator` 密碼 | 只給 `migrate` 服務使用 |

```bash
sudo install -d -m 0750 -o root -g 10001 /etc/jukebox101/secrets
# 三個檔案都設為 0400、擁有者 10001:10001；內容結尾的換行會被去除，前後空白會被拒絕
sudo chown 10001:10001 /etc/jukebox101/secrets/*.txt && sudo chmod 0400 /etc/jukebox101/secrets/*.txt
SECRET_EXPECTED_UID=10001 SECRET_EXPECTED_GID=10001 scripts/deploy/check-host-secrets.sh /etc/jukebox101/secrets/discord_bot_token.txt
SECRET_EXPECTED_UID=10001 SECRET_EXPECTED_GID=10001 scripts/deploy/check-host-secrets.sh /etc/jukebox101/secrets/db_password.txt
```

- CA 檔（`JB_DB_CA_FILE`）不是秘密，但必須讓 uid 10001 可以讀取（例如 0444）。
- 依 `.env.example` 建立 `.env`，裡面只放非秘密設定。`DISCORD_OWNER_GUILD_ID` 是選用設定：
  - 有設定時，Owner 審核指令只會出現在那個伺服器。
  - 留空時，不註冊 Owner 審核指令，改用 Owner CLI。

## 6. 載入映像（不要在主機上建置）

```bash
sha256sum -c SHA256SUMS
docker load -i image/<v7 映像檔>.tar
docker image inspect jukebox101-bot:v7 --format '{{.Os}}/{{.Architecture}} {{.Config.User}}'   # linux/amd64 10001:10001
docker compose -f compose.yaml config --images                                                  # jukebox101-bot:v7
```

## 7. Schema migration（夥伴執行，使用 migrator 身分）

```bash
docker compose -f compose.yaml --profile migrate run --rm migrate status   # 唯讀
docker compose -f compose.yaml --profile migrate run --rm migrate plan     # 唯讀，列出待套用的版本
docker compose -f compose.yaml --profile migrate run --rm migrate apply --apply
```

| 結果 | 意義 |
|---|---|
| 結束碼 0 | 成功 |
| 結束碼 2 | 拒絕，且沒有任何變更。可能原因：superuser、非 schema 擁有者、已套用的檔案被修改、未知版本、另一個 migration 正在執行 |
| 結束碼 3 | 某個版本失敗，該版本已整筆回復；之前的版本保留 |

- 重跑是安全的：已套用的版本會跳過。
- `0003` 只能套用在全新的空資料庫。若資料庫已有資料，它會拒絕執行，不會偽造就緒狀態。舊資料的處理方式見第 13 節。

## 8. 啟動與健康檢查

```bash
docker compose -f compose.yaml up -d --no-build      # 一律加上 --no-build
docker compose -f compose.yaml ps                    # 每 5 分鐘以 python -m bot.readiness 做唯讀檢查
docker compose -f compose.yaml logs --tail=100 bot
```

`python -m bot.readiness` 會回報 `ready`，或列出問題代碼：

| 問題代碼 | 意義 |
|---|---|
| `ROLE_IS_SUPERUSER` | 連線角色是 superuser |
| `CONTROL_ROW_MISSING` | 缺少 control 列 |
| `SCHEMA_VERSION_MISMATCH` | schema 版本不符 |
| `INTAKE_NOT_CERTIFIED` | 投稿受理尚未通過認證 |
| `SETTLEMENT_ENABLED` | 結算被開啟 |
| `DATABASE_UNREACHABLE_OR_DENIED` | 無法連線或被拒絕 |
| `CONFIGURATION` | 設定錯誤 |

輸出不含任何秘密。容器顯示健康，不代表 Discord 連線正常；Discord 部分要依第 12 節人工確認。

## 9. 邀請連結（Owner 產生後交給各伺服器管理員）

```bash
docker run --rm --network none --entrypoint python jukebox101-bot:v7 -m bot.invite_link --application-id <Application ID>
```

- 連結內容：scope 為 `bot applications.commands`，`permissions=0`（不要求 Administrator，也不要求任何頻道權限，因為所有回覆都是互動回應），`integration_type=0`（安裝到伺服器）。
- 連結可以重現，而且不含 Token。

## 10. 全域指令同步（Owner；只在指令定義改變時執行一次，所有伺服器共用）

Bot 啟動時**不會**自動同步、刪除或覆寫遠端指令。

```bash
docker run --rm --network none --entrypoint python jukebox101-bot:v7 -m bot.command_sync preview      # 離線，記下 local_digest
# 需要 Token 與網路；以 0400 秘密檔掛載 DISCORD_BOT_TOKEN_FILE，並設定 DISCORD_EXPECTED_APPLICATION_ID
... -m bot.command_sync fetch --remote-file remote.json --i-understand-this-calls-discord              # 唯讀
... -m bot.command_sync apply --expected-digest <local_digest> --i-understand-this-calls-discord --confirm-overwrite-remote-commands
```

- `apply` 會**整批覆寫**全域指令。digest 不符，或 Token 屬於別的 Application 時，會拒絕而且不做任何變更。
- 全域指令可能要一段時間才會出現在所有伺服器的用戶端。新加入的伺服器不需要另外同步。
- `--target dev-guild --guild-id <id>` 只用於開發伺服器的測試。

## 11. 新伺服器加入後的行為（程式已實作；真實 Discord 尚未驗收）

- 管理員用邀請連結加入後，成員就能在允許「使用應用程式指令」的頻道使用 `/share`、`/history`、`/balance`，不需要 Owner 設定任何東西。
- 新使用者第一次 `/share` 時，系統會自動建立帳號，可以安全重複執行，並行時也不會產生重複資料。`/balance` 對新使用者回覆 0，而且不會寫入資料庫。
- 以下情況只會私密回覆說明，不寫入、不發點、不扣點：
  - 在私訊中使用指令。
  - Application 不符。
  - ID 超出範圍。
  - 資料庫未就緒（`投稿資料庫尚未通過啟用檢查…`）。
  - 資料庫暫時無法使用（`資料服務暫時無法使用…`）。
- 提交結果未知時，回覆會請使用者「勿重新投稿」並聯絡 Owner。系統不會自動重試；以同一個操作鍵再查詢一次，就能辨識原本的結果。

## 12. 人工驗收（未執行；完成前不得宣稱已驗收）

1. **Portal 設定**：依第 3 節完成，並確認 Interactions Endpoint URL 是空的。
2. **資料庫與啟動**：
   - 對正式 PostgreSQL 執行 `migrate status`，結果為 `pending_before: []`，`readiness` 回報 `ready`，而且 `tls_mode` 是 `verify-full`。
   - `up -d --no-build` 後，日誌出現登入成功，且沒有 `secret file refused`。
3. **指令同步**：preview 之後 apply 全域指令，在主要伺服器確認看得到 `/share`、`/history`、`/balance`。
4. **全新伺服器驗收**：
   - 在一個**全新的測試伺服器**，用邀請連結加入 Bot，不做其他任何設定。
   - 由**一般成員**（沒有管理權限）執行 `/balance`，應回覆 0；執行 `/share <YouTube 網址>`，應回覆已儲存；執行 `/history`，應看得到剛才的投稿。
   - 同一人到原本的伺服器執行 `/history`，也應看得到同一筆投稿。
5. **拒絕情境**：
   - 私訊 Bot 使用指令，應被拒絕。
   - 非 Owner 使用 Owner 指令（若有啟用），應得到相同的拒絕訊息。
6. **時間記錄**：記錄指令出現在用戶端所花的時間（Discord 傳播延遲）。

## 13. 備份、還原與舊資料

- **備份**：以 schema 擁有者身分執行，例如 `pg_dump -U jukebox101_migrator -Fc -d jukebox101 -f jukebox101-YYYYMMDD.dump`。
  - 不能用 Bot 的 app 角色備份，因為它沒有 identity 序列的權限。
  - 備份檔要加密並存放在主機以外的地方；保存期限與負責人由 Owner 決定。
- **還原**（DBA）：
  1. 建立空資料庫。
  2. 執行 `REVOKE ALL ON DATABASE ... FROM PUBLIC`。
  3. `GRANT CONNECT` 給三個登入角色。
  4. 執行 `pg_restore --exit-on-error -d <db> <dump>`。
  5. 執行 `migrate status` 確認沒有待套用的版本，並執行 `readiness`。
  - 以上流程已在拋棄式 PostgreSQL 18.6 上實測：還原後各表內容摘要完全相同，也能繼續投稿（`scripts/dev/verify-backup-restore.sh`）。
- **舊 MySQL 資料**：v7 預設使用**全新的空資料庫**。需要保留舊資料時，見 `docs/deploy/mysql-to-postgresql-migration-plan.md`（**未執行**的方案）。

## 14. 更新與回滾

- **更新**：
  1. 核對新包的校驗碼後，執行 `docker load`。
  2. 若有新的 migration，先執行 `migrate plan`，再執行 `migrate apply --apply`。
  3. 執行 `up -d --no-build`。
- **回滾**：
  - 可以 `docker load` 上一版映像，再執行 `up -d --no-build`。
  - migration 只能往前，不能退回。如果新版已套用新的 schema，就不能只把程式退回舊版，應改用備份還原。

## 15. 仍關閉或未驗證

- `/claim`、正式 `/exchange` 與補償功能保持關閉（兩個旗標都是 off）。
- 補償功能的資料表沒有建立在 PostgreSQL schema 中。
- 交換與補償的程式已完成 PostgreSQL 方言移植，SQL 也通過 PostgreSQL 的 PREPARE 檢查（補償資料表除外），但**沒有做 PostgreSQL 端到端驗證**，啟用前必須另外驗收。
