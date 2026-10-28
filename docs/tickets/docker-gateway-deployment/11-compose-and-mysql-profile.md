# 11：Compose 設定與選用的同機 MySQL profile

**What to build:** 一份 Compose 設定，預設連接外部 MySQL；另提供一個選用 profile，在同一主機啟動 MySQL，且 3306 不對公網開放。秘密以 Compose secrets 掛載檔案，不含任何真實值。

**Blocked by:** 10（需先確認映像與掛載行為）

**Status:** ready-for-agent

**File owner:** `compose.yaml`（新）、`compose.mysql.yaml`（新，選用 profile）

## 設計要點

- 預設服務：只有 Bot，連接外部 MySQL；不發布任何主機連接埠。
- 秘密：以檔案型 Compose secrets 掛載；路徑由部署者以環境變數指定，不寫死在 Compose 中。
- 資源限制：設定 CPU 與記憶體上限；限制是否生效需由 10 的實測確認。
- 同機 MySQL profile：只在內部網路中提供服務，不發布 3306 到主機或公網。
- 不自動執行 Migration；不掛載資料庫 dump 或 volume 作為初始化資料。
- 不使用 `latest` 標籤；映像版本需固定。

## TDD 驗證

- 紅：Compose 設定中不存在任何發布 3306 的設定（靜態檢查）。
- 紅：Compose 中不存在任何真實秘密值（靜態檢查，含模式比對）。
- 紅：預設服務不包含 Migration 指令或初始化腳本。
- 紅：`docker compose config` 可成功解析，且兩種模式（外部 MySQL、同機 profile）都能輸出設定。

**Acceptance criteria:**
- [ ] `docker compose config` 在兩種模式下都成功。
- [ ] 沒有任何服務發布 3306；預設模式不發布任何連接埠。
- [ ] 秘密來源為 Compose secrets 檔案，且文件說明 uid、gid、mode 在本版本的實際行為（以 10 的實測為準）。
- [ ] 資源限制已設定，實際生效狀況記錄於 10 或 14。
- [ ] 不含真實秘密、真實主機位址或真實資料。

**Out of scope:** 正式部署主機上的實際秘密路徑（待 Owner 確認，見 13）。
