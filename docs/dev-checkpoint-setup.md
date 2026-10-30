# 開發檢查點：新電腦接續說明

本文件說明如何在另一台電腦接續 JukeBox101 的開發檢查點。它不代表定版、交付或正式驗收。

所有範例都不含舊的 server UUID、私人路徑或憑證。新電腦上的值都要重新建立，不沿用任何舊值。

## 1. 取得程式

- 遠端：`origin`，分支 `feature/owner-review-offline-checkpoint`。
- Repository 為私人（PRIVATE），需要有存取權限的 GitHub 帳號。
- 取得後確認分支與 HEAD 與交接文件一致，再開始工作。

## 2. 安裝依賴

前置條件：Python 3.12（`pyproject.toml` 要求 `>=3.12,<3.13`）。

```bash
python3.12 -m venv .venv
source .venv/bin/activate          # Windows PowerShell：.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
```

- 執行期依賴列於 `pyproject.toml`。版本釘選見 `dependency-versions.txt`。
- Docker 用的依賴另見 `requirements-docker.txt`，與本地測試無關。
- 虛擬環境 `.venv/` 已列入 `.gitignore`，不要提交。

## 3. 執行離線測試

```bash
python -m pytest
```

- 離線測試不需要 MySQL、Discord 或任何環境變數。
- `mysql` 標記的測試在未設定隔離開關時會略過，屬預期行為。
- 測試暫存目錄由 `pyproject.toml` 的 `addopts` 固定為 `--basetemp=.pytest_tmp`。因此：
  - pytest 必須單獨執行，同一 repository 不要並行兩個 pytest；
  - 若必須並行，每次執行都要明確指定獨立且為新建的 `--basetemp=<新目錄>`，該目錄須位於專案之外。
  - 本檔不修改全域測試設定。固定暫存目錄只能解釋「目錄衝突」（例如 `FileExistsError`），不能解釋其他錯誤。

## 4. 建立專用隔離 MySQL（可選，用於 `mysql` 標記的測試）

隔離實例只供測試使用，不是正式資料庫。

1. **伺服器版本**：MySQL 8.0.16 以上（CHECK 約束自此版本起才會強制）。
2. **連接埠**：使用 `127.0.0.1:3307`。不要使用既有的 3306 服務。
3. **資料目錄**：建立在專案之外的新目錄。
4. **身分記錄**：初始化後，以 `SELECT @@server_uuid` 取得新實例的 UUID，記錄在專案外的檔案中。舊的 UUID 不得沿用。
5. **資料庫**：建立 `jukebox101_test`，套用 `bot/database/schema.sql` 與 `bot/database/migrations/` 中的 002 至 005。`003_exchange_delivery.sql` 檔頭標示為候選、未註冊的 DDL；套用前須確認既有核准流程允許此步驟。
6. **候選 DDL**：`docs/compensation-candidate-004.sql` 只能套用於隔離測試 schema，且須依既有核准流程。
7. **測試帳號**：依 `tests/isolated_mysql_target.py` 的設定建立隔離帳號（`jukebox101_test_exchange`），密碼必須是新產生的值。

### 必要環境變數（只列名稱，值不寫入文件）

| 名稱 | 說明 |
|---|---|
| `JUKEBOX_RUN_EXCHANGE_MYSQL_ISOLATED` | 必須為 `1` 才會連線 |
| `TEST_ISOLATED_DB_HOST` | `127.0.0.1` |
| `TEST_ISOLATED_DB_PORT` | `3307` |
| `TEST_ISOLATED_DB_USER` | 隔離測試帳號 |
| `TEST_ISOLATED_DB_PASSWORD` | 新產生的密碼，存於專案外的檔案，權限 0600，不輸出到終端或紀錄 |
| `TEST_ISOLATED_DB_NAME` | `jukebox101_test` |
| `TEST_ISOLATED_SERVER_UUID` | 新實例的 `@@server_uuid` |
| `TEST_ISOLATED_RUN_DIR` | 專案外的實例目錄 |
| `COMPENSATION_EVIDENCE_DIR` | 每次執行前建立的新空資料夾，位於專案之外 |

### 執行

```bash
python -m pytest tests/test_exchange_restart_recovery_mysql.py tests/test_exchange_mysql_integration.py tests/test_exchange_cross_process_mysql.py tests/test_exchange_repository_mysql.py tests/test_exchange_formal_chain_mysql.py tests/test_unknown_commit_mysql.py tests/test_compensation_mysql.py tests/test_formal_exchange_handler_mysql.py
```

- 隔離 MySQL 測試須單獨執行，不要與其他 pytest 並行（見上方 `--basetemp` 說明）。
- 每次執行都要使用新的 `COMPENSATION_EVIDENCE_DIR`，因為測試會把清理計畫寫入該資料夾。
- 結束後以 `docker stop` 或同等方式正常關閉實例，並保留資料目錄。

## 5. 不要做的事

- 不要把任何密碼、token 或 UUID 寫入 repository 文件或程式碼。
- 不要把 `.env` 複製或提交。`.env` 已列入忽略清單，本文件不讀取其內容。
- 不要沿用舊實例的資料目錄、證據資料夾或 UUID。
- 不要使用正式環境資料庫或正式 Discord token 執行測試。

## 6. 目前狀態

- 本檢查點的 HEAD 與待提交內容以 `git status` 與交接文件為準。
- 離線與隔離 MySQL 結果代表離線及專用隔離範圍，不代表正式驗收、新映像驗證或可交付。
- 尚未核准事項：基底映像拉取、UNKNOWN 產品行為、`os._exit` 最終接受、LICENSE、正式環境操作、定版交付。
