# 指定 Supabase 環境：初始化完成

日期：2026-10-30。使用者明確要求立即建立資料庫後，已對專案 `gtyvvemrhosfnfigbosn` 完成初始化。這項授權取代先前不修改該專案資料庫的限制；沒有啟動 Bot 或建立付費資源。

## 已建立

| 項目 | 實際設定 |
|---|---|
| PostgreSQL | Supabase 現有 **17.11**，未升級或新建專案 |
| DB host（direct） | `db.gtyvvemrhosfnfigbosn.supabase.co` |
| DB port（direct） | `5432` |
| DB name | `postgres`，使用供應商既有資料庫，未建立另一個 database |
| Schema | `jukebox101`，owner 為 `jukebox101_migrator` |
| app 登入角色 | `jukebox101_app` |
| migrator 登入角色 | `jukebox101_migrator` |
| 權限群組 | `jukebox101_app_role`、`jukebox101_readonly_role`，均 NOLOGIN |
| readonly 角色 | `jukebox101_readonly`，目前 NOLOGIN；沒有替它設定或生成交付密碼 |
| 資料表 | **25 張**，包含投稿、審核、帳務與 migration ledger |
| Migration | 0001、0002、0003 已套用，SHA-256 與現有 v7 原始檔逐一相符 |
| Control | `schema_version=2b2-b1`、snapshot ready、legacy writers quiesced、settlement off |

app／migrator 密碼已使用使用者指定秘密 ZIP 內的對應檔案設定。明文只在本機記憶體中處理，透過 libpq 產生 SCRAM verifier 後送入資料庫；未輸出明文、未放進 Git、未改 ZIP，也未修改 Supabase `postgres` 管理員密碼。Discord Token 未用於資料庫設定。

## 已驗證

- 新的本機 PostgreSQL **17.11** 拋棄式環境：**35 passed、零 skipped**，含實際 TLS、migration、最小權限、並行、Owner review 與失聯復原。該次專用容器、network、volume 已移除。
- 原測試的版本斷言改為 `JB_PG_TEST_EXPECTED_MAJOR`（預設仍為 18）；17 的補測明確指定 17，並未移除版本檢查。
- Supabase 正式庫以 `SET LOCAL ROLE jukebox101_migrator` 核對 migration ledger；三個來源 checksum 相符。
- 以 `SET LOCAL ROLE jukebox101_app` 實際讀取 control，確認就緒旗標及無 CREATE／DELETE／TRUNCATE 權限。
- readonly 可讀取 control，無 INSERT／UPDATE／DELETE 權限；所有 JukeBox101 角色皆無 superuser／CREATEDB／CREATEROLE／replication／BYPASSRLS。
- `anon`／`authenticated` 不能使用專用 schema；未透過 Data API 開放表格。
- Supabase 額外 migration 固定 trigger function 的 `search_path=pg_catalog,jukebox101`，安全 advisors 重查為空。未改動 v7 的三個來源 migration。

證據：`artifacts/v7/evidence/pg17-compatibility.txt`、`supabase-bootstrap-verification.json`、`supabase-migration-expected.json`。

## 仍需部署主機完成

**資料庫已建好，但未驗證部署主機的外部密碼登入／TLS，Bot 尚未接上此庫。** 上述正式庫檢查走的是已授權 Supabase 管理工具，不是 Bot 的外部連線。

- direct endpoint 為 IPv6，目前這台執行環境沒有可用 IPv6 路由。
- 若使用 IPv4，須從專案 Connect → Session pooler 取得實際 host。不能由 region 猜測 pooler host；其登入名稱需使用 `jukebox101_app.gtyvvemrhosfnfigbosn`／`jukebox101_migrator.gtyvvemrhosfnfigbosn`。
- CA 憑證仍須由 Supabase Dashboard 的 Database Settings／SSL Configuration 取得，之後配置 `JB_DB_CA_FILE`；本輪沒有取得或宣稱已驗證該 CA。
- 使用 `DB_NAME=postgres`，不要沿用一般範例的 `jukebox101` database name。必須保持 `DB_SSLMODE=verify-full`。
- 部署者需以正式映像執行 migrate status／readiness，確認外部連線，再安排 Bot 與 Discord 人工驗收。

## 原投稿的狀態

初始化後查詢 `discord:1565661310711234720` 與影片 `cUfDOS2SINM`，筆數皆為 **0**。本輪沒有捏造、補建或核准這筆投稿；若 Discord 已回覆投稿成功，須追查當時 Bot 的實際資料庫，不能把這個新空庫當作原投稿來源。

既有 v7 Docker 壓縮包與校驗碼維持不變；這是封裝完成後的指定環境設定紀錄。
