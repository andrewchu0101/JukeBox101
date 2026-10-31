# Supabase 串接與首筆審核驗證（2026-10-31，Asia/Taipei）

## 已驗證範圍

- Hostinger VPS 的 Discord Bot 已有投稿寫入 Owner 的 Supabase；本次不是完整功能驗收。
- Codex 在 00:38 唯讀查到 users／songs／jb2_events 各 1 筆、accepted_sequence=1，精確操作為 `discord:1565766654502113430`。先前操作 `discord:1565765607276675153` 在該次查詢中不存在，不能宣稱已遷入。
- 使用者確認影片 cUfDOS2SINM 全長 189 秒；管理 CLI duration preview 顯示 PASS，後續 Discord approve 確認畫面顯示已核准、修訂 2。核准結果為使用者畫面證據，本輪未另做核准後 SQL 複驗。
- 錄音版本尚未確認；使用者 /balance 顯示 0。本次未由 Codex 啟用結算或寫入 ledger，不代表自動發獎已完成。
- PR #2 已合併 main（dd83004）；PR hosted CI：1881 離線＋35 PostgreSQL 案例、零失敗／跳過，runtime／TLS 檢查通過。使用者提供夥伴回報 main CI 也通過。CI 不等於 CD，正式自動部署仍未啟用。

## 本次排錯結論

1. 舊本機 `.env` 為 MySQL：127.0.0.1:3306，不能沿用為 Supabase 設定。
2. Supabase database 是 `postgres`，應用 schema 是 `jukebox101`；public 空白不是未建表。
3. 舊 Bot readiness 顯示 PG18.6／database=jukebox101，與 Owner Supabase PG17.11／postgres 不同；當時 Discord 回覆已儲存，但 Supabase 無對應事件。實際舊庫位置未由 Codex 複驗。
4. 夥伴測試回報 VPS 可用 IPv6、Bot 容器不可用；原 CA 無法驗證 Supabase。Owner 提供 Supabase Root 2021 CA 公開憑證，檔案解析及有效期檢查通過。最終容器設定／憑證鏈仍以部署者證據為準。
5. 曾有 postgres 帳號 28P01；不能拿管理帳號失敗推論 app 密碼失效。後續 Supabase 出現 jukebox101_app／Supavisor 連線，且新投稿已落盤。
6. Session Pooler 與直連的使用者格式不同：Pooler 為 `<app-role>.<project-ref>`，資料庫端 current_user 仍是 app role。database=postgres、TLS verify-full。實際主機／帳密／路徑不存入 Git。
7. 平台 /rest-admin/v1/ready 的 HTTP 200 不是 Bot 驗證；Pooler 連線存在也不代表正式 Gateway 已切換。以精確 operation ID 落盤作為端到端證據。
8. PG17／PG18 差異是辨識目標的線索，不是已證實故障原因。現有自動整合測試仍主要使用 PG18.6，不能宣稱 PG17 全面驗收。

## 仍需保留的界線

- 舊庫投稿的保存／搬遷尚未確認；不得刪除或覆蓋舊庫。
- v7 tar 包完整性已檢查：523 個 manifest 檔案雜湊全部相符。它不包含較新 Handoff 的 Gateway 健康模組；不能只換新版 Compose 搭配舊映像。
- 審核補時長目前要用 VPS CLI，影響操作體驗。下一個 MVP 見 [自動審核與 Nickel 規格](../mvp-auto-review-nickel.md)。
- 不提交 .env、密碼、Token、私鑰、資料庫 dump 或正式憑證設定；本文件僅整理既有證據，未執行部署或帳務變更。
