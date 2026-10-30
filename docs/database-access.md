# 開發者如何安全查看資料庫

- 適用：開發者需要查詢正式或測試資料庫的內容，但不應取得寫入或管理權限。
- 日期：2026-10-29（工單 08）
- 原則：文件只使用佔位符（`<...>`）。帳號密碼、主機位址、IP、Guild／Owner ID 等實際值不得寫入本文件、程式、Compose 檔、交付包或聊天。

## 1. 三類帳號比較

| 帳號（佔位符） | 誰使用 | 允許的權限 | 不得擁有的權限 | 存取位置 |
|---|---|---|---|---|
| `<READONLY_USER>`（唯讀查詢帳號） | 開發者，查詢資料 | 業務表的 `SELECT` | 任何 `INSERT`、`UPDATE`、`DELETE`、DDL、`GRANT` | 經 SSH 隧道，或主機內部連線 |
| `<APP_USER>`（Bot 執行帳號） | 只有 Bot 容器 | 業務表的 `SELECT`、`INSERT`、`UPDATE`；`nickel_grants`、`nickel_claim_requests` 等依 Migration 授權的表 | `DELETE`、DDL、`GRANT`、`SUPER`、`FILE`、`PROCESS`、root 權限 | Bot 容器，密碼只掛載為 `db_password` 秘密檔 |
| `<MIGRATOR_USER>`（維護帳號） | Owner，只在經核准的 Migration 或結構維護時段 | 結構變更（DDL）與必要的讀取 | 不放入 Bot 容器，不提供給夥伴，不作為日常帳號 | Owner 本機或維護主機 |

權限來源：
- Bot 帳號的表格與權限，依 `bot/` 內實際 SQL 盤點，見 [db-least-privilege.md](db-least-privilege.md) 第 2 節。
- 授權範本見 [db-least-privilege-grants.sql](db-least-privilege-grants.sql)。這是**草稿**，只能在經授權的隔離資料庫中由 Owner 審閱後執行，不得直接在正式資料庫執行。

## 2. 唯讀查詢帳號

- 只授予業務表的 `SELECT`。
- 連線後先執行唯讀 session 設定，再執行查詢。這與既有唯讀檢查的做法一致（`tests/readonly_precheck.py` 使用 `SET SESSION TRANSACTION READ ONLY`）：

  ```sql
  SET SESSION TRANSACTION READ ONLY;
  ```

- 查詢工具（MySQL 用戶端、GUI 工具）只能用來執行 `SELECT`、`SHOW` 與 `DESCRIBE`。不得在查詢工具中執行任何寫入語句，包括 `UPDATE`、`INSERT`、`DELETE`、`REPLACE`、`ALTER`、`DROP`、`TRUNCATE`、`GRANT`。
- 即使誤送出寫入語句，伺服器也應因唯讀 session 或缺少權限而拒絕；這只是第二道防線，不得依賴它代替「不寫」的紀律。

## 3. 存取方式：不開放 3306

- MySQL 的 3306 埠**不對公網開放**。外部主機不得直接連到資料庫。
- 開發者透過 SSH 隧道連線，隧道只轉送到資料庫主機的內部位址：

  ```bash
  ssh -N -L <LOCAL_PORT>:<DB_INTERNAL_HOST>:3306 <SSH_USER>@<BASTION_HOST>
  ```

- 隧道建立後，用戶端連到本機的 `127.0.0.1:<LOCAL_PORT>`，使用唯讀帳號登入：

  ```bash
  mysql --host=127.0.0.1 --port=<LOCAL_PORT> --user=<READONLY_USER> --database=<APP_SCHEMA> -p
  ```

- 密碼由開發者從密碼管理器取得，以互動方式輸入，不得寫入 shell 指令、`.env`、腳本或文件。
- 若資料庫與查詢者位於同一主機，可改用主機內部連線，仍然只使用唯讀帳號。
- SSH 私鑰、主機位址與帳號名稱屬於私人資訊，不得放入專案。

## 4. 帳號建立責任

- 帳號由 **Owner** 依本文件與 [db-least-privilege-grants.sql](db-least-privilege-grants.sql) 建立，**程式不會自動建立任何帳號**。
- 建立後以 `SHOW GRANTS FOR '<ACCOUNT>'@'<HOST>';` 核對實際授權，並與草稿比對。
- 唯讀帳號的建立與授權，同樣由 Owner 執行，並在授權後確認它沒有寫入權限。
- 本文件不包含建立帳號的實際指令值；部署前的步驟見 [owner-secrets.md](deploy/owner-secrets.md)。

## 5. Bot 執行帳號

- 只由 Bot 容器使用，密碼以 `db_password` 秘密檔掛載，不以環境變數傳遞。
- 權限為應用程式所需的最小集合；不得授予 `DELETE`、DDL、`GRANT`、`SUPER`、`FILE`、`PROCESS`。
- `jb2_migrations` 只由維護路徑使用，**不授予** Bot 帳號。
- 開發者不得使用 Bot 帳號查詢資料；查詢一律使用唯讀帳號。

## 6. 維護帳號

- 只在 Migration 或結構維護時由 Owner 手動使用，並限定在核准的維護時段。
- 不放入 Bot 容器，不掛載到任何 Compose 服務，不提供給夥伴或開發者的日常查詢。
- 使用後立即結束該 session，不保留在 shell 歷史中。

## 7. 禁止事項

- **不得以 root 或管理員帳號查詢。**
- **不得把帳號、密碼、主機位址或 IP 寫入本文件、程式、Compose 檔、交付包、日誌或聊天。**
- **不得直接修改餘額或帳本來補發 Nickel。** 所有 Nickel 變動必須經過應用程式的帳本路徑，並與 `users.credit_balance` 同交易寫入；直接改表會破壞帳本一致性（見工單 07 與規格的 Nickel 一致性規則）。
- 不得把 3306 對公網開放，也不得以 `0.0.0.0` 綁定資料庫埠。
- 不得把查詢結果（含使用者的 Discord ID 或投稿內容）貼到公開位置。

## 8. 查詢範例（只讀）

以下範例只使用 `SELECT`，表名與欄位以實際 schema 為準：

```sql
SET SESSION TRANSACTION READ ONLY;

-- 查看某位使用者的 Nickel 餘額快取（以 <DISCORD_USER_ID> 佔位）
SELECT discord_user_id, credit_balance
FROM users
WHERE discord_user_id = <DISCORD_USER_ID>;

-- 查看該使用者最近的帳本列，用於與餘額快取核對
SELECT ledger_id, credit_delta, reason, created_at
FROM credit_ledger
WHERE discord_user_id = <DISCORD_USER_ID>
ORDER BY ledger_id DESC
LIMIT 20;
```

- 欄位名稱已依 `bot/database/schema.sql` 核對（`users.discord_user_id`、`users.credit_balance`；`credit_ledger.ledger_id`、`credit_delta`、`reason`、`created_at`）。結構若經 migration 變更，以當時的 schema 為準。
- 不提供任何 `INSERT`、`UPDATE`、`DELETE` 範例。

## 9. 待 Owner 確認

- `<READONLY_USER>`、`<APP_USER>`、`<MIGRATOR_USER>` 的實際名稱（文件中的 `jukebox101_readonly`、`jukebox101_app` 為草稿名稱）。
- 外部 MySQL 的 TLS 設定與來源網路限制（見 [owner-secrets.md](deploy/owner-secrets.md) 第 9 節）。
- 唯讀帳號的使用者名單與存取期限。

## 10. 驗證狀態

- 本文件為**文件工作**，不包含連線測試。
- 人工審閱：待 Owner 審閱。
- 文件內容已與 `docs/db-least-privilege.md`、`docs/db-least-privilege-grants.sql` 與 `tests/readonly_precheck.py` 的實際做法核對（僅核對文字，未連線資料庫）。
