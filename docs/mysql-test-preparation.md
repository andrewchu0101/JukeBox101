# Phase 1D：隔離 MySQL 測試準備

狀態：**使用者已人工確認測試庫、帳號、六表及約束；本次最終安全審查僅靜態／mock 驗證，未執行真實寫入測試**。正式 jukebox101 的 Phase 1C 結果仍有效；Phase 1 維持 Pending。

本次安全驗證：46 Passed、0 Failed、0 Skipped，全部為 mock／單元／靜態測試；14 項整合案例只收集、不執行。測試 fixture 的拒絕正式庫、拒絕伺服器身分不符、Rollback 與關閉路徑以 mock 驗證，不能宣稱已通過真實 MySQL 寫入驗收。

## 人工建立流程

1. 使用管理帳號在 Workbench 檢閱 `docs/mysql-test-setup.sql`。測試庫固定為 `jukebox101_test`；若已存在，CREATE DATABASE 會失敗，請停止核對來源，不要自動清除或重用。
2. 人工執行 CREATE DATABASE 與 USE jukebox101_test，緊接著在**同一編輯器／連線**貼上已審查的 `bot/database/schema.sql` 六表定義。執行前再次確認 DATABASE() 是 jukebox101_test；不要修改正式 Schema。此處不複製 Schema，避免兩份定義漂移。
3. 在 Workbench 使用者管理中人工建立 `jukebox101_test_app@127.0.0.1`，在本機設定私人密碼。不要把密碼加入 SQL 文件、程式、Git 或回報。
4. 人工審查並執行 setup.sql 中的 GRANT，只授予測試庫 SELECT、INSERT、UPDATE、DELETE。帳號不能有正式庫存取、DDL、全域或額外角色權限。不要修改 jukebox101_app 的既有權限。
5. 管理帳號檢查 SHOW CREATE TABLE 與 SHOW INDEX，核對六表、六個主鍵、十個唯一鍵、九個外鍵、八個啟用 CHECK、InnoDB 及 utf8mb4，外鍵全部指向 jukebox101_test。管理帳號另以 SHOW TRIGGERS FROM jukebox101_test 確认沒有額外觸發器；應用帳號沒有 TRIGGER 權限，不能靠它看到的空清單證明不存在。
6. 建立測試帳號的 Workbench 連線，預設資料庫設為 jukebox101_test，執行下列唯讀檢查。

```sql
SELECT CURRENT_USER(), DATABASE();
SHOW GRANTS;
SELECT TABLE_NAME, ENGINE
FROM information_schema.TABLES WHERE TABLE_SCHEMA = 'jukebox101_test';
SELECT TABLE_NAME, CONSTRAINT_NAME, CONSTRAINT_TYPE, ENFORCED
FROM information_schema.TABLE_CONSTRAINTS
WHERE CONSTRAINT_SCHEMA = 'jukebox101_test';
SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME,
       REFERENCED_TABLE_SCHEMA, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE CONSTRAINT_SCHEMA = 'jukebox101_test'
ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION;
SELECT CONSTRAINT_NAME, CHECK_CLAUSE
FROM information_schema.CHECK_CONSTRAINTS
WHERE CONSTRAINT_SCHEMA = 'jukebox101_test';
```

預期 CURRENT_USER() = jukebox101_test_app@127.0.0.1、DATABASE() = jukebox101_test。禁止使用 root 執行應用測試；管理帳號只供人工建庫、建表及權限審查。

## 專用設定與開關

測試不讀 `.env`，不沿用 DB_*。由使用者在私人 PowerShell session 設定以下環境變數；TEST_DB_PASSWORD 由本機私人方式注入，不提供含密碼的命令範本。沒有新增憑證檔。

| 變數 | 必須值 |
|---|---|
| TEST_DB_HOST | 127.0.0.1 |
| TEST_DB_PORT | 3306 |
| TEST_DB_USER | jukebox101_test_app |
| TEST_DB_NAME | jukebox101_test |
| TEST_DB_PASSWORD | 使用者本機私下設定 |
| JUKEBOX_TEST_SCHEMA_READY | 1，僅在人工確認測試庫結構後 |
| JUKEBOX_DISPOSABLE_DATABASE | jukebox101_test，舊值 1 不再有效 |
| JUKEBOX_RUN_MYSQL_TEST | 1，僅在另外核准執行整合測試後 |

舊 JUKEBOX_RUN_MYSQL / JUKEBOX_SCHEMA_READY 不再啟用 tests/test_mysql.py。正式庫獨立唯讀入口仍用 JUKEBOX_RUN_MYSQL_READONLY。

## 未來測試指令（本次未執行）

人工確認建庫、帳號與權限並另外核准後，先只選兩項不寫入案例：

```powershell
$env:JUKEBOX_RUN_MYSQL_READONLY = '0'
$env:JUKEBOX_TEST_SCHEMA_READY = '1'
$env:JUKEBOX_DISPOSABLE_DATABASE = 'jukebox101_test'
$env:JUKEBOX_RUN_MYSQL_TEST = '1'
.\.venv\Scripts\python.exe -m pytest tests/test_mysql.py::test_mysql_identity tests/test_mysql.py::test_required_tables -q --basetemp=.pytest_tmp --tb=short
```

寫入測試獲得額外批准後，才執行完整 14 項：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_mysql.py -m mysql -q --basetemp=.pytest_tmp --tb=short
```

完成後將 JUKEBOX_RUN_MYSQL_TEST 設回 0；保留正式 `.env` 原內容。正式 jukebox101 不允許作為任何寫入測試目的地。

## 14 項案例安全審查

| 案例 | 數量 | 行為與期待 |
|---|---:|---|
| 身分／表存在 | 2 | SELECT，檢查測試帳號、測試庫及六表 |
| 歌曲／推薦唯一 | 2 | 隔離列 INSERT，重複應回傳 1062 |
| 推薦歌曲外鍵 | 1 | 先確認 song_id=0 不存在，再期待 1452；若占用則跳過 |
| Rollback | 1 | 插入隔離列後 rollback，查詢使用者列已不存在 |
| 推薦成功時間 CHECK | 2 | 不一致狀態 INSERT，期待 3819 |
| 每日預約配對 CHECK | 5 | pending 帶 ID、其他狀態缺 ID 應回傳 3819 |
| 每日收件人複合外鍵 | 1 | 另一個存在使用者借用推薦應回傳 1452 |

每個寫入 fixture 都再次確認固定 TEST_DB 目的地與 disposable 授權，再 SELECT 驗證伺服器身分、六表 InnoDB、必要鍵名稱/型別、主鍵／唯一鍵欄位順序、複合外鍵完整目標、CHECK 文字與 ENFORCED、外鍵不跨庫；通過後才 begin/INSERT。隨機測試識別避免碰觸原列，碰撞會失敗而不更新原資料。不執行 UPDATE、DELETE、DDL 或清空資料。沒有成功 commit；finally rollback，且 rollback 本身失敗也會 close。

目前 preflight 已核對必要鍵的精確欄位／順序，但不取代完整 Schema diff、帳號權限與觸發器的人工審查。建立名為測試庫的資料庫不代表它可以任意修改；應保持專供測試，不能存放真實資料。

最終靜態安全審查：52 Passed、0 Failed、0 Skipped，僅執行 test_foundation.py、test_schema_static.py、test_mysql_safety.py。新增同名但錯誤欄位／複合外鍵，以及 fixture 授權再次確認的 mock 回歸案例；未連線 MySQL、未檢視真實環境值或執行 14 項整合案例。應從已設定 TEST_DB_* 的同一 PowerShell 執行上述人工測試命令，其他獨立程序不一定能取得該視窗的環境變數。

Rollback 不會回復 AUTO_INCREMENT 計數，因此序號可留下間隔。斷線或 rollback 失敗會讓測試失敗，不能宣稱一定已清理；需在測試庫人工調查。現有 Rollback 案例只直接確認 users 列消失，其餘列由同交易 InnoDB 回滾，尚未獨立查驗所有列；ledger 額度、並行與每日業務冪等尚未涵蓋。
