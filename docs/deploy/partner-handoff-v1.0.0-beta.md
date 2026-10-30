# JukeBox101 v1.0.0 (Beta) — 夥伴交接單

日期：2026-10-30。對外顯示名稱 **JukeBox101 v1.0.0 (Beta)** 對應技術交付版 **v7**。
這是使用者辨識名稱；既有壓縮包、內容中的技術版本與映像 tag `jukebox101-bot:v7` 均保持原樣。
本交接單是包外補充文件，不修改既有 tar.gz、manifest 或校驗碼。

## 可交接範圍

**可交給夥伴進行部署準備及 Beta 驗收。** 離線測試 1747 項、PostgreSQL 整合 35 項均通過且零 skip；TLS、備份還原、migration/readiness、映像內容、授權原始碼、封裝與解壓校驗均已有證據。

正式部署尚未驗收：真實 Discord、新 Guild 邀請後使用、正式外部 PostgreSQL 尚待部署者與 Owner 依手冊第 12 節確認。
目前提供 /share、/history、/balance；正式 /exchange、/claim、補償維持關閉。僅允許單一 Gateway 實例。
本版預設使用全新 PostgreSQL；舊 MySQL 資料搬遷方案尚未執行，需要保留舊資料時應先停在規劃階段，不能直接當作已完成升級。

## 提供給夥伴的檔案

1. `jukebox101-delivery-v7.tar.gz`（約 374 MiB）。
2. `jukebox101-delivery-v7.tar.gz.sha256`。
3. 本交接單。
4. 已填妥的非秘密設定資料，以及 PostgreSQL CA 憑證／CA bundle（PEM）。
5. 實際部署前，經獨立安全管道配置三個秘密檔（下節）。

交付壓縮包 SHA-256：

```text
48b4c6bfaadc434ad80220818d37118721137968e9fa9dd1a5c44c08caa4302f
```

包內已有映像、原始碼、Docker Compose、`.env.example`、授權及確切第三方原始碼、驗證證據。
部署入口為包內 `docs/deploy/v7-postgresql-deployment.md`，DBA 設定 SQL 為 `docs/deploy/postgres-setup.sql`；不需要另傳工作樹、`.git`、v6 包或歷史測試資料。

## 必須確認的資訊

| 項目 | 交付內容／負責人 |
|---|---|
| 部署主機 | 夥伴準備 Linux amd64、Docker Engine、Compose v2；確認對外可連 Discord 與 PostgreSQL；不需 Bot 入站埠 |
| PostgreSQL | Owner／DBA 指定 PostgreSQL 18 的 host、port、database、app 與 migrator 使用者名稱；建立角色、schema 與網路限制 |
| TLS | DBA 提供 CA PEM；DB_HOST 必須符合伺服器憑證，使用 verify-full |
| Discord IDs | Owner 提供 Application ID、Owner User ID；Owner Guild ID 選填，留空則不註冊 Guild Owner 審核指令 |
| 主機路徑 | 夥伴確認 JB_SECRET_DIR、JB_DB_CA_FILE；依手冊設定檔案擁有者與權限 |
| Portal 與同步 | Owner 設定 Guild Install／Public Bot、Interactions Endpoint URL 留空；負責全域指令同步及邀請連結 |
| 資料政策 | 確認使用新空庫或另行規劃舊資料搬遷；指定備份負責人、保存期限與還原流程 |
| 驗收與維運 | 約定部署時間、Owner／DBA／夥伴聯絡人；完成手冊第 12 節再宣布正式 Beta 上線 |

可依 `.env.example` 準備非秘密設定：DB_HOST、DB_PORT、DB_NAME、DB_USER、DB_MIGRATION_USER、DB_SSLMODE、JB_DB_CA_FILE、DISCORD_EXPECTED_APPLICATION_ID、DISCORD_OWNER_USER_ID、選填 DISCORD_OWNER_GUILD_ID、JB_SECRET_DIR。兩個功能旗標保持 off。

## 秘密檔獨立配置

| 檔名 | 用途 |
|---|---|
| `discord_bot_token.txt` | Bot Token，由 Owner 配置 |
| `db_password.txt` | app 角色密碼，由 DBA 配置 |
| `db_migrator_password.txt` | migrator 角色密碼，僅供 migration 使用，由 DBA 配置 |

秘密不放進交付壓縮包、Git、一般交接單或非秘密 `.env`。可由 Owner／DBA 直接放上主機，或經核准的安全管道交付；不必在一般聊天中提供值。readonly 密碼留給維護人員，Bot 不需要；也不需要交出 Discord 帳號密碼或資料庫 superuser 密碼。
具有主機管理員或完整 Docker 權限的夥伴可以讀取主機秘密，應由 Owner 確認該權限安排。

## 夥伴接手順序

1. 檢查壓縮包校驗碼，解壓後核對包內 SHA256SUMS，閱讀部署手冊。
2. 確認主機、外部 PostgreSQL、角色、TLS、設定及秘密檔已備妥。
3. 載入映像；依手冊執行 migration status／plan／apply 與 readiness。
4. Owner 完成 Portal、指令同步與邀請；夥伴依手冊啟動單一 Gateway。
5. 完成全新 Guild、一般成員、跨 Guild 共用資料及拒絕情境驗收，記錄結果。

本交接單不代表已啟動 Bot、同步 Discord 或操作正式資料庫；上述實際部署步驟由 Owner／DBA／夥伴另行安排。
