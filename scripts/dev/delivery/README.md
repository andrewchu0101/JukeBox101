# v7 打包工具

所有輸入的 v6-r4 包、工作樹與 Git metadata 只讀；輸出必須是新目錄。無 commit/push、正式 Bot 或正式資料庫操作。

1. 從目前工作目錄建置 test.Dockerfile；離線跑 `-m 'not postgres'`，再對有 `jukebox101-disposable-test` 標記的專用庫跑 `-m postgres -rfEs`。
2. 使用 `scripts/dev/enable-test-tls.sh` 可替 compose.test.yaml 的拋棄式庫設定短效測試憑證，之後 TLS 測試須零 skip。`scripts/dev/verify-fresh-postgres.py` 可自動建立全新專用容器／網路／volume、設定 TLS 並跑 35 項測試，最後只移除該次資源；本輪已實跑通過。不要將其用於正式資料庫。
3. 執行 `scripts/dev/verify-backup-restore.sh`。以正式映像的 Python stdin 執行 `scripts/dev/verify-runtime-postgres.py`，測試容器需獨立網路與 `/tmp` tmpfs。
4. `docker save`，用 `image_audit.py` 逐層檢查、抽出授權；其 ls-tree 格式輸入必須由**目前 bot/ 檔案內容**計算 Git blob SHA-1，不能沿用基準 commit。
5. `prepare_sources.py <v6-r4 delivery> <work>` 複用舊簽章驗證原始碼，下載新增元件的確切版本與 psycopg sdist。需公開網路。原始碼盤點由實際映像 dpkg 與 copyright 產生。
6. `verify_debian_sources.py` 離線重驗簽章鏈。從映像抽出 `/usr/share/common-licenses/`，複用未改版本 pip 25.0.1 的已驗證 vendor notices/provenance，產生新授權索引。
7. 交付映像 `docker load`，Compose `--dry-run up -d --no-build`；確認無 build/pull 且沒有建立任何容器或網路。
8. `package_v7.py <work> <fresh-output>` 產生逐檔 manifest、未提交修改清單、固定時間的 tar.gz 與校驗碼。
9. 對 stage 與重新解壓的包執行 `scan_pack.py`、`check_members.py`、校驗碼、manifest、Debian 簽章鏈。重建第二份 tar.gz，SHA-256 必須相同。

`classified-pack-baseline.json` 分類理由：授權與 Debian 原始碼中的 email 為上游作者/維護者歸屬；9 個程式碼命中均已人工核對，分別為讀取環境秘密的函式呼叫、連線設定欄位、互動 token 的 docstring 及 Owner 預覽回傳值，皆不含憑證。blocking 命中不豁免。

`classified-image-baseline.json` 沿用 v6-r4 人工確認的上游測試資料（逐層、逐路徑、逐規則），本次映像零新增 blocking 命中。
