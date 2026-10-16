# Phase 1D.1：外鍵錯誤調查

使用者回報真實測試 12 Passed、2 Failed、0 Skipped；兩項失敗都在要求錯誤碼 1452 的斷言，實際捕獲 1216。本次沒有連線 MySQL、讀憑證、寫入資料或執行 DDL。

## 錯誤定義與證據限制

1216（ER_NO_REFERENCED_ROW）與 1452（ER_NO_REFERENCED_ROW_2）皆表示新增／更新子列缺少父列，SQLSTATE 皆為 23000。1452 帶有較詳細的父表／外鍵資訊，1216 可省略這些資訊。[MySQL 錯誤定義](https://dev.mysql.com/doc/mysql-errors/8.0/en/server-error-reference.html)。

MySQL 的外鍵錯誤資訊受父表權限檢查控制；不能提供細節時會將詳細錯誤映射成通用錯誤。[官方外鍵文件](https://dev.mysql.com/doc/refman/8.0/en/create-table-foreign-keys.html)、[WL#8910 的設計與實作說明](https://dev.mysql.com/worklog/task/?id=8910)。

已知帳號僅有測試庫 DML 權限，與權限導致的資訊遮蔽一致，因此是目前合理推論。但缺少該次完整錯誤訊息、伺服器版本與實際 grants，不能把該推論宣稱為已確認的單一原因，也不能從錯誤碼 alone 直接辨識具體外鍵。沒有證據要求修改 Schema 或增加權限。

## SQL 與斷言

- recommendations 插入有效收件人但 song_id=0 的資料；先確認 song_id=0 不存在、使用者存在，預期 fk_recommendations_song 拒絕。若零值歌曲已存在仍跳過，不修改任何原列。
- daily_dispatches 插入另一個存在使用者，沿用原推薦與歌曲；歌曲、推薦及使用者存在，只有複合擁有者父列不存在，預期 fk_dispatch_owner 拒絕。sending 狀態的 ID 配對符合 CHECK，該推薦尚未被每日表使用，避免其他約束成為預期的失敗來源。
- fixture 首次 INSERT 前的 Schema preflight 繼續核對外鍵名稱、完整欄位與目標、必要鍵與 CHECK；未變更 Schema。
- 仍使用 pytest.raises(pymysql.IntegrityError)，只允許 PyMySQL 既有常數 1216／1452，並要求外鍵拒絕訊息。1452 必須揭露預期約束；1216 若揭露名称也必須符合。無名稱的 1216 以 Schema preflight 與父列存在／缺失條件辨識預期來源，不假裝錯誤訊息含有名稱。
- 1062、1217、OperationalError、非外鍵訊息、錯誤外鍵及未拋出例外都不接受。

## 自動驗證與殘留狀態

本次 68 Passed、0 Failed、0 Skipped，僅執行 test_foundation.py、test_schema_static.py、test_mysql_safety.py、test_mysql_assertions.py。mock 包含兩個原測試函式的 1216／1452 路徑、錯誤類型／約束／父列拒絕、INSERT 意外被接受，以及失敗斷言後的 fixture cleanup。

fixture 的 finally 在測試斷言失敗時仍會 rollback，且 rollback 失敗也會 close。使用者的 12 Passed 表示原 Rollback 案例通過，但不提供六表前後筆數或每個 teardown 的證據，因此**真實殘留檢查仍為 Pending**。不能宣稱零殘留；不會自動刪除或清理資料。

## 人工重跑

在已設定 TEST_DB_* 的同一 PowerShell 視窗，先保留 MySQL 唯讀資訊，勿包含密碼：

```sql
SELECT VERSION(), CURRENT_USER(), DATABASE();
SHOW GRANTS;
SELECT 'users' AS table_name, COUNT(*) AS row_count FROM jukebox101_test.users
UNION ALL SELECT 'songs', COUNT(*) FROM jukebox101_test.songs
UNION ALL SELECT 'submissions', COUNT(*) FROM jukebox101_test.submissions
UNION ALL SELECT 'recommendations', COUNT(*) FROM jukebox101_test.recommendations
UNION ALL SELECT 'credit_ledger', COUNT(*) FROM jukebox101_test.credit_ledger
UNION ALL SELECT 'daily_dispatches', COUNT(*) FROM jukebox101_test.daily_dispatches;
```

確認身分、Schema 與測試庫專用權限後，人工重跑原有 14 項：

```powershell
$env:JUKEBOX_RUN_MYSQL_READONLY = '0'
$env:JUKEBOX_TEST_SCHEMA_READY = '1'
$env:JUKEBOX_DISPOSABLE_DATABASE = 'jukebox101_test'
$env:JUKEBOX_RUN_MYSQL_TEST = '1'
.\.venv\Scripts\python.exe -m pytest tests/test_mysql.py -m mysql -q --basetemp=.pytest_tmp --tb=short
$env:JUKEBOX_RUN_MYSQL_TEST = '0'
```

重跑後再次以新的查詢快照比較六表筆數，測試期間避免其他程序使用測試庫。如果測試庫原本為空且前後皆空，可確認這次沒有資料列殘留；自增序號間隔仍可能存在。若失敗，保留已遮蔽敏感資訊的完整錯誤訊息與 teardown 結果，不要自行加權限或清空資料。
