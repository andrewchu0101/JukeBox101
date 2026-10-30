# Owner 審核的 Migration 容器驗證（2026-10-28 UTC 實機；2026-10-29 台北）

- 路線：A。所有資料庫連線都在 `jb101-mig-mysql` 的網路命名空間內，目標為容器內 `127.0.0.1:3306`。不發布新埠，不使用主機 3306、3307、3308 連線。
- 未執行：正式 Migration、正式發幣、指令同步、正式 Discord 連線、commit、push、映像發布、prune。
- 資源保留：本次資源依 Lead 指示保留，未清理。

## 1. 離線（可重現，不需容器）

| 項目 | 位置 | 結果 |
|---|---|---|
| 容器模式目標驗證 | `bot/database/migration_container_target.py` | opt-in `JUKEBOX_RUN_MIGRATION_CONTAINER_TARGET=1`；連線端點 `127.0.0.1:3306`（容器內）；發布埠 `3308` 作為容器事實檢查；schema 須符合 `jukebox101_mig_<12 位數字>`；帳號 `jukebox101_test_migrator@127.0.0.1`；無啟用角色；容器名稱、volume、伺服器 UUID |
| 守衛擴充 | `bot/database/submission_migration.py` | `apply_migration(..., guards=None)`；預設 `None` 時行為不變，既有 `_require_isolated_destination` 與 `validate_profile` 常數未改 |
| 既有容器檢查擴充 | `bot/isolated_container_target.py` | `verify_container_target` 具名參數，預設值不變 |
| 測試 | `tests/test_owner_container_target.py`、`tests/test_owner_service_container_rw.py` | 紅→綠：端點常數拆分後先紅（端點與發布埠不符），再綠 |

聚焦測試（2026-10-29）：`test_owner_container_target`、`test_isolated_container_target`、`test_submission_migration`、`test_dual_account_safety` 共 80 passed。`test_owner_service_container_rw` 的 live 測試為 skip（需 opt-in），其餘 5 項 passed（含非 Owner 的 preview、confirm 離線替身測試）。全套回歸由 Lead 統一執行，本成員未完成全套。

## 2. 隔離容器實測（路線 A）

### 2.1 資源（只列名稱）

| 類型 | 名稱 | 狀態 |
|---|---|---|
| 容器 | `jb101-mig-mysql`（`mysql:8.0`，8.0.46，發布 `127.0.0.1:3308->3306`） | 運作中 |
| volume | `jb101-mig-data`，掛載於 `/var/lib/mysql` | 存在 |
| schema | `jukebox101_mig_202610281706` | 存在 |
| 帳號 | `jukebox101_test_migrator@127.0.0.1`（僅授權該 schema） | 存在 |
| 帳號 | `jukebox101_app@127.0.0.1`（僅 `docs/db-least-privilege-grants.sql` 中可套用的授權） | 存在 |

伺服器 UUID 只在執行時讀取，未寫入本文件。

### 2.2 步驟與結果

| 步驟 | 結果 |
|---|---|
| 清點基準 | 執行前 schema 內 0 個資料表 |
| 基準 schema（`bot/database/schema.sql` 的 6 個 `CREATE TABLE`，非 Migration） | 6 個表建立 |
| 受控 Migration（`apply_migration` + `container_guards`，`--network container:jb101-mig-mysql`，`--user 1000:1000`） | 輸出 `migration_applied` |
| 身分檢查（守衛於 DDL 前執行） | 通過：`CURRENT_USER()`=`jukebox101_test_migrator@127.0.0.1`、`CURRENT_ROLE()`=`NONE`、schema 與 UUID 一致 |
| `jb2_migrations` | `2b2-b1` 狀態 `applied`（由 Migration 寫入） |
| `jb2_control` | 單例 1，`schema_version=2b2-b1`，三個就緒旗標皆 0（Migration 預設值） |
| 最低權限授權（`docs/db-least-privilege-grants.sql`，移除 CREATE USER） | 002 所建表的授權已套用；`nickel_grants`、`nickel_claim_requests` 因 004、005 未套用而失敗（已記錄）。`jukebox101_app` 目前 19 條表級授權（另有 USAGE），無 DELETE、無 DDL |
| Owner 讀取 `list_pending`（應用帳號，`OwnerReviewService`） | 成功，回傳 `PendingPage` |
| 非 Owner `list_pending` | `PermissionError: Owner identity is required` |
| 非 Owner `view` | `PermissionError: Owner identity is required` |
| Owner `view` 不存在的操作 | `ValueError: Unknown review operation`（預期） |
| Owner 寫入：建立待審案件（`accept_pending`） | 拒絕：`PersistenceNotReady: Pending intake requires reviewed isolation and disabled settlement`（就緒旗標為 0） |

### 2.3 守衛逐項結果（活體、唯讀；秘密遮罩，名稱可見）

兩個端點不得混用：主機發布埠 `127.0.0.1:3308` 只用於 docker 狀態檢查與容器健康，不作為 Migration 連線；容器內部端點 `127.0.0.1:3306` 是 Migration 與 Bot 帳號讀寫的唯一連線端點。

| 檢查項 | 實際值（遮罩） | 結果 |
|---|---|---|
| opt-in | `JUKEBOX_RUN_MIGRATION_CONTAINER_TARGET` 未設定 | 拒絕 `MIGRATION_CONTAINER_NOT_AUTHORIZED` |
| 端點（連線） | `127.0.0.1:3306`（容器內） | 通過 |
| 端點（誤用發布埠） | `127.0.0.1:3308` | 拒絕 `ENDPOINT_MISMATCH` |
| 身分（帳號） | `jukebox101_test_migrator`；應用帳號 `jukebox101_app` 誤用 | 通過；誤用拒絕 `MIGRATOR_ACCOUNT_MISMATCH` |
| 角色 | `CURRENT_ROLE()`=`NONE` | 通過；有啟用角色的情境僅離線測試 `ACTIVE_ROLE_PRESENT` |
| UUID | 實際值 `bd85…`（遮罩）；錯誤 pin `0000…` | 通過；不符拒絕 `SERVER_UUID_MISMATCH` |
| 容器名稱 | `jb101-mig-mysql`；誤用 `jb101-iso-mysql` | 通過；誤用拒絕 `CONTAINER_NAME_MISMATCH` |
| volume | `jb101-mig-data`；bind mount 與 `jb101-iso-data` | 通過；誤用拒絕 `VOLUME_MISMATCH` |
| 資料庫 | `jukebox101_mig_202610281706`；誤用 `jukebox101_test` | 通過；誤用拒絕 `DATABASE_MISMATCH` |

所有活體拒絕都發生在 DDL 之前；通過的情境只執行身分 SELECT。

### 2.4 限制（已實測）

- **寫入路徑的閘門拒絕已驗證；寫入通過未驗證**：`accept_pending` 被 `PersistenceNotReady` 拒絕，證明就緒閘門正確地阻擋 Owner 寫入路徑。`approve`、`reject`、`confirm` 的實際寫入路徑未驗證，因為案件無法建立。
- **未執行任何就緒旗標操作**：依 Lead 決定，未執行 `set_control` 或任何直接寫入 `jb2_control` 的操作。`snapshot_ready`、`legacy_writers_quiesced`、`settlement_enabled` 維持 Migration 寫入的 0。
- **Owner 寫入需要正式的就緒設定程序（目前專案中不存在），這是需要 Owner 決定的事項。** 在決定前，Owner 寫入的實機驗證無法進行。
- Owner 的 `DISCORD_OWNER_USER_ID` 為合成值（`900000000000000001`），僅用於此一次性 schema，非正式 Owner。
- 驅動使用映像 `jukebox101-bot:dev` 內建的 Python 3.12.16，其中 pymysql 為 2.2.8；專案釘選為 1.2.3。pymysql 版本差異未驗證。
- 映像內另有一份 `/app/bot` 副本，本次未使用；程式碼來源為工作目錄（唯讀掛載 `/src`）。

## 3. 通過與未驗證清單

**通過（活體）**
1. 容器模式守衛逐項（第 2.3 節）：端點、身分、UUID、容器名稱、volume、資料庫的通過與拒絕。
2. 受控 Migration：`apply_migration` + `container_guards` 套用 002 到 `jukebox101_mig_202610281706`，`jb2_migrations` 為 `2b2-b1 / applied`。
3. 最低權限授權：`jukebox101_app` 的 19 條表級 SELECT/INSERT/UPDATE（另有 USAGE），無 DELETE、DDL、GRANT。
4. Owner 讀取：`list_pending`（Owner）成功；非 Owner 的 `list_pending`、`view` 被應用程式的 `PermissionError` 拒絕。
5. Owner 寫入路徑的閘門拒絕：`accept_pending` 被 `PersistenceNotReady` 拒絕（閘門拒絕已驗證，寫入未通過）。

**未驗證**
1. `approve`、`reject`、`confirm` 的實際寫入與回讀，以及冪等回放。
2. 實機同帳號的非 Owner 寫入拒絕（離線替身已驗證，實機尚未）。
3. 角色守衛的活體拒絕（`ACTIVE_ROLE_PRESENT` 僅離線測試）。
4. 003、004、005 Migration 在此受控路徑上的套用（目前 runner 僅涵蓋 002）。
5. 以正式版本 pymysql 1.2.3 重跑實機步驟（目前用映像內的 2.2.8）。
6. 全套回歸測試（Lead 統一執行；本成員未取得全套結果）。
7. 正式的就緒設定程序（目前不存在，需 Owner 決定）。
8. 清理（資源保留中，未清理）。

## 4. 不得作為驗證證據的事項

- `jb101-iso-mysql` 與其 `jukebox101_test` 曾被直接套用 SQL，不是乾淨基準，未用於本次驗收，亦未手動補寫 `jb2_migrations` 或 `jb2_control`。
- 離線測試通過不代表容器或 Owner 實機行為已驗證。

## 5. 新的專用隔離資源 `jb101-real-mysql`（2026-10-28 UTC；Lead 核准）

本節為獨立資源，與第 2 節的 `jb101-mig-mysql` 分開。`jb101-iso-mysql` 與 `jb101-lifecycle-bot-1` 未處理。

### 5.1 建置命令（不含密碼）

- volume：`docker volume create --label purpose=isolated-test-real jb101-real-data`
- 容器：`docker run -d --name jb101-real-mysql --label purpose=isolated-test-real -p 127.0.0.1:3309:3306 -v jb101-real-data:/var/lib/mysql -e MYSQL_ROOT_PASSWORD mysql:8.0`（root 密碼只在執行程序記憶體中產生）
- 建立 `jukebox101_test`；帳號 `jukebox101_test_migrator@127.0.0.1`（僅 `jukebox101_test` 的 ALL）與 `jukebox101_test_app@127.0.0.1`。兩者都是假值帳號，用途為「隔離測試，假值，僅此容器」，主機限定 127.0.0.1，密碼只經環境變數傳入。

### 5.2 身分紀錄（遮罩）

| 項目 | 值 |
|---|---|
| 映像 | `mysql:8.0`，8.0.46 |
| 發布埠 | `127.0.0.1:3309 -> 3306/tcp`（只作容器事實） |
| volume | `jb101-real-data` → `/var/lib/mysql` |
| server_uuid 前綴 | `a9216e09-…`（完整值只在 `.local/real-build-record.json`，該路徑由 .gitignore 忽略；此 pin 只適用於 `jb101-real-mysql`） |
| 建立時間 | 2026-10-28T17:40:25Z |

建立後以 `docker inspect` 與 `SELECT @@server_uuid` 核對，與建置紀錄一致（pin match）。

### 5.3 守衛：新的具名 profile

- 模組：`bot/database/real_container_target.py`（新檔）；測試：`tests/test_real_container_target.py`（新檔）。
- 開關：`JUKEBOX_RUN_REAL_CONTAINER_TARGET=1`（與 migration 開關互不啟用）。
- 端點：連線 `127.0.0.1:3306`（容器內）；schema `jukebox101_test`；帳號 `jukebox101_test_migrator@127.0.0.1`；無啟用角色；容器名 `jb101-real-mysql`；volume `jb101-real-data`；發布埠 `3309`；UUID 比對建置 pin。
- 既有守衛常數、`jb101-iso`（3307）守衛與 migration profile 未改動。

紅→綠：`tests/test_real_container_target.py` 先因 `ModuleNotFoundError` 收集失敗；實作後 72 passed（含 real、migration container、isolated container、submission migration 四組）。

### 5.4 活體守衛逐項（唯讀）

| 檢查項 | 結果 |
|---|---|
| 基線（所有事實一致） | 通過 |
| 未設 real 開關 | 拒絕 `REAL_CONTAINER_NOT_AUTHORIZED` |
| 誤用 migration 開關 | 拒絕 `REAL_CONTAINER_NOT_AUTHORIZED` |
| 端點 `127.0.0.1:3309`（發布埠） | 拒絕 `ENDPOINT_MISMATCH` |
| 端點 `127.0.0.1:3307`（隔離埠） | 拒絕 `ENDPOINT_MISMATCH` |
| pin 錯誤（遮罩） | 拒絕 `SERVER_UUID_MISMATCH` |
| 誤用應用帳號 `jukebox101_test_app` 為 migrator | 拒絕 `MIGRATOR_ACCOUNT_MISMATCH` |
| 容器名 `jb101-iso-mysql` | 拒絕 `CONTAINER_NAME_MISMATCH` |
| volume `jb101-iso-data` | 拒絕 `VOLUME_MISMATCH` |

所有拒絕都發生在 DDL 之前。發布埠 3306、3307、3308 的容器事實拒絕由離線測試覆蓋。

### 5.5 受控 Migration 與應用帳號

- 受控 Migration：`apply_migration` + `real_guards` 套用 002 到 `jukebox101_test`，輸出 `migration_applied`；`jb2_migrations` 為 `2b2-b1 / applied`。
- 就緒旗標：`jb2_control` 的 `snapshot_ready`、`legacy_writers_quiesced`、`settlement_enabled` 仍為 0（未操作）。
- 授權：`jukebox101_test_app` 有 19 條表級 SELECT/INSERT/UPDATE，另有 USAGE；無 DELETE、DDL、GRANT。`nickel_grants`、`nickel_claim_requests` 因 004、005 未套用而失敗（已記錄）。

### 5.6 Owner 服務（應用帳號，實機）

| 操作 | 結果 | 開啟的 repository 連線 |
|---|---|---|
| Owner `list_pending` | 成功，`PendingPage` | 1 |
| 非 Owner `list_pending` | 拒絕 `PermissionError`（應用程式授權） | 0 |
| 非 Owner `view` | 拒絕 `PermissionError` | 0 |
| 非 Owner `preview` | 拒絕 `PermissionError` | 0 |
| Owner `accept_pending`（寫入路徑） | 閘門拒絕 `PersistenceNotReady`（就緒旗標為 0） | 1 |

非 Owner 的拒絕在 repository 層之前發生，以開啟連線次數為證（0）。這證明拒絕來自應用程式授權檢查，不是 MySQL 帳號權限。

### 5.7 未驗證

1. `approve`、`reject`、`confirm` 的寫入與回讀（案件無法建立，因就緒旗標為 0；需要 Owner 決定正式的就緒程序）。
2. 應用帳號本身的身分守衛（本 profile 只檢查 migrator；應用帳號的 profile 未建立）。
3. 角色守衛的活體拒絕（僅離線測試）。
4. 003–005 的受控 Migration。
5. 正式 pymysql 1.2.3 的實機重跑（目前映像內為 2.2.8）。
6. 全套回歸（Lead 統一執行）。
7. 清理（資源保留，未清理）。

## 6. 真實 MySQL 的回滾與提交保留驗證（`jb101-real-mysql`，2026-10-28 UTC）

- 身分：real 守衛 profile 通過（`REAL_CONNECT_PORT` 3306 容器內、`jukebox101_test`、`jukebox101_test_migrator@127.0.0.1`、無啟用角色、server_uuid 前綴 `a9216e09-…` 與建置紀錄 match）。守衛常數未修改。
- 帳號：`jukebox101_test_app` 的假值密碼以 ALTER USER 更換（只經環境變數）；`CURRENT_USER()` 為 `jukebox101_test_app@127.0.0.1`。
- 測試資料：`users` 表，明確 `discord_user_id` 900000000000000095–099；測試前後未刪除既有資料。
- 子程序在容器網路命名空間內連 `127.0.0.1:3306`（容器內端點）。

| 情境 | 子程序結束 | 另一連線查證 | server 端 |
|---|---|---|---|
| a1 未提交 INSERT（…099），子程序 SIGKILL | 退出碼 −9 | 列不存在（0） | 無開啟交易（INNODB_TRX=0） |
| a2 未提交 INSERT（…098），`os._exit(0)` 不 close、不 commit | 退出碼 0 | 列不存在（0） | 無開啟交易 |
| b 已提交 INSERT（…097），commit 後 `os._exit(0)` | 退出碼 0 | 列存在（1） | 列保留 |
| c 未提交 INSERT（…095）後 `SELECT SLEEP(30)`，3 秒後 SIGTERM | 退出碼 −15（子程序已輸出 holding 標記後才送訊號） | 列不存在（0）；`FOR UPDATE` 探測無鎖等待錯誤 | 無殘留交易或鎖等待 |
| d 觀察：`SELECT SLEEP(30)` 無訊號 | 退出碼 1，約 10.3 秒，`Lost connection … read operation timed out` | 未檢查列（無寫入） | 客戶端 read_timeout 10 秒先於 SLEEP(30) 結束 |

**os._exit 相關的資料庫證據結論**：`os._exit` 不會提交或回滾交易，也不會關閉連線。本次證據顯示：未提交的交易在子程序以 `os._exit` 或 SIGKILL 結束後，server 會回滾，列不可見，且沒有殘留交易。已提交的列在 `os._exit` 後仍保留。持久性取決於 commit 的確認，而非行程結束方式。

**限制**
- 情境 c 的 `SLEEP(30)` 會被專案的 `read_timeout`（10 秒）先切斷；本次以 SIGTERM 在 3 秒時送出，未測到 30 秒完整睡眠。情境 d 記錄了 10 秒的客戶端逾時。
- 重跑時驅動程式整體執行，a1、a2、b 會再次執行。b 的 097 已存在，第二次 INSERT 預期因重複主鍵失敗；本輸出已過濾，未逐行確認。
- 本驗證只連容器內 3306（經同一網路命名空間），未以主機發布埠連線。
- `performance_schema.data_lock_waits` 與 `INNODB_TRX` 的查詢以 root 唯讀執行；應用帳號無 PROCESS 權限，無法自行查看。
- 測試列 …095–…099 中仍有 …097（已提交）保留；依指示未刪除，需 Lead 決定是否清理。

### 6.1 全新 id 重跑（a1、a2、b，未過濾輸出）

測試列改用 900000000000000089–091；未刪除任何既有列（…095–…099 的殘留，…097 保留）。

| 情境 | 退出碼 | 另一連線查詢 |
|---|---|---|
| a1 未提交 INSERT（…089），SIGKILL | −9 | 0（不存在） |
| a2 未提交 INSERT（…090），`os._exit(0)` | 0 | 0（不存在） |
| b 已提交 INSERT（…091），commit 後 `os._exit(0)` | 0 | 1（存在） |

重跑後 `information_schema.INNODB_TRX` 為 0（root 唯讀查詢）。…097 保留。

### 6.2 限制與邊界（Lead 要求明確記錄）

1. **c 未測到完整長查詢**：`SELECT SLEEP(30)` 會被 `bot/database/connection.py` 的 `read_timeout`（10 秒，`QUERY_TIMEOUT_SECONDS`）先切斷。c 的 SIGTERM 在 3 秒時送出，因此只證明「長查詢期間收到 SIGTERM」的情境，未測到 30 秒完整睡眠。d 只記錄了 10 秒逾時。
2. **若要測完整長查詢的方案（尚未實作，需 Lead 決定）**：
   - 方案 A（建議）：只在測試驅動中自行建立 pymysql 連線並把 `read_timeout` 設為 40 秒，參數與正式連線其他設定相同。`bot/` 不變，正式的 10 秒保持不變。
   - 方案 B：在 `open_connection` 增加可選的 `read_timeout` 參數（預設仍為 10 秒）。這需修改 `bot/database/connection.py`，依指示未做。
3. **os._exit 結論的邊界**：本次的 `os._exit` 是獨立驅動腳本的子程序，並非正式入口的 SIGTERM 清理路徑。**正式入口的 `os._exit` 是否會在有未提交交易的 worker 執行緒中被呼叫，本次未以正式入口驗證。** 因此本節的結論只適用於：單一子程序、直接以 pymysql 連線、未提交交易中的 `os._exit`/SIGKILL。它不代表正式 bot 在收到 SIGTERM 或例外時的清理行為。
4. 其餘限制不變：應用帳號無 PROCESS 權限（以 root 唯讀查詢 INNODB_TRX 與 data_lock_waits）；只連容器內 3306；未以主機發布埠連線。

### 6.3 長查詢完整版（read_timeout=40 只屬於測試驅動）

- **範圍**：`read_timeout=40` 只存在於測試驅動（`open_long_test_connection`，與正式 `open_connection` 相同參數，只改 read_timeout）。**正式路徑仍為 10 秒**（`bot/database/connection.py` 的 `QUERY_TIMEOUT_SECONDS`），本次未修改 `bot/`。
- 測試 id：全新的 900000000000000087（完整睡眠版）與 900000000000000088（SIGTERM 版）；未重用 089–099，未刪除任何列。

| 情境 | 子程序退出碼 | 長查詢是否完成（SLEEP 回傳值） | 另一連線查詢該列 | INNODB_TRX |
|---|---|---|---|---|
| c-full：未提交 INSERT（…087）後 `SELECT SLEEP(30)`，不送訊號，等待完整 30 秒，然後 `os._exit(0)` 不 commit | 0（耗時 30.3 秒） | 完成，回傳 0 | 不存在（0） | 0 |
| c-SIGTERM：未提交 INSERT（…088）後 `SELECT SLEEP(30)`，收到 holding 後 3 秒送 SIGTERM | −15（耗時 3.4 秒） | 未完成（被 SIGTERM 中止，無回傳值） | 不存在（0） | 0 |

- 完整版的 30 秒睡眠未被 read_timeout 切斷（40 秒 > 30 秒）。
- 兩者收尾時 root 唯讀查詢 INNODB_TRX 均為 0；列 …091 與 …097 保留。
- 本節的完整版證明：長查詢正常完成時，未提交的列在子程序以 `os._exit` 結束後不可見。它不證明正式入口的行為（見 6.2）。
