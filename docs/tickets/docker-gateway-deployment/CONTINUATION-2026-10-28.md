# 接續摘要（2026-10-28 檢查點）

## 測試結果
- 最終全量離線回歸：2658 通過、2 失敗、138 略過。
- 2 項失敗為已知限制：`test_isolated_mysql_target`、`test_window_schema_isolation`，回報 `DATADIR_NOT_ISOLATED`。根因是守衛要求本機執行目錄，容器內資料目錄是 `/var/lib/mysql`。守衛未放寬。
- 前一輪一次出現的 `test_lifecycle_sigterm` 資料庫執行緒案例失敗，在本次最終回歸中通過；連續重跑也通過。原因未查明，列為不穩定風險。

## 未驗證
- **MySQL**：驅動腳本（不是正式入口）的未提交回滾、已提交保留、長查詢加 SIGTERM 已在 `jb101-real-mysql`（3309）驗證。正式入口的關閉路徑在有未提交交易的工作執行緒中是否安全，未驗證。
- **os._exit**：未標為最終接受。在正式入口的資料庫實測證據補齊前，維持待定。
- **容器秘密權限案例**：UID、GID、父目錄、開啟探測的容器實測未做，僅有單元測試。
- **真實 Discord**：未連線，`close` 只以替身測試。
- **restart 行為**：本輪維持 `unless-stopped`。秘密錯誤會持續重啟（實測），部署前必須檢查秘密檔（擁有者 10001、0400，父目錄可搜尋）。主機或 daemon 重啟後的行為未驗證。

## 時限量測
- 程式內期限：20 秒（互動等待 15 秒、取消收尾 1 秒、close 至少 4 秒）。
- 外部量測（`docker stop`）：stuck 約 15.2 秒、stuck_db 約 15.2 秒、close_hangs 約 20.3 秒。close_hangs 的外部耗時超過程式內 20 秒的部分來自 Docker 停止的開銷；不宣稱三者都在 20 秒內。

## 映像與候選包
- 映像：`jukebox101-bot:dev` 仍早於本輪變更，**未重建**。本次檢查點未重建映像或候選包。
- 候選包：上一個版本已失效，不得沿用。重建需要在檢查點之後另行執行。

## 保留資源（未清理）
- `jb101-real-mysql`、`jb101-real-data`、`jukebox101_test`（列 …087、…088、…089、…090、…091、…097 等測試列）。
- `jb101-iso-mysql`、`jb101-iso-data`（身分不可追溯，不作為驗收依據）。
- `jb101-lifecycle-bot-1`（exited，屬 entry-owner-dev 的測試容器，網路 none）、`jb101-lifecycle2` 相關容器與網路、映像 `jukebox101-bot:lifecycle-test*`。
- `jb101-mig-mysql`、`jb101-mig-data`、`jukebox101_mig_202610281706`、測試帳號。
- 映像匯出檔：`/home/gaston/build/review/candidate-image.tar`、`/home/gaston/build/review/layers/`。不分享、不提交、不納入交付包。

## Git 檢查點
- 未建立 commit。原因：工作樹同時包含本次之前的混雜修改（例如 `bot/discord_http.py` 是既有未追蹤檔，後來被本次修改），無法安全辨識歸屬，依規則保留未提交。
- 未暫存任何檔案。
- 排除清單：`.env`、私人 PDF（`Re Issue 576880673 ... .pdf`）、秘密檔、映像匯出檔、測試資料。

## 尚未授權
- push、發布、正式服務連線、正式 Migration／發幣、指令同步、交付夥伴、定版。
- 專案 LICENSE：待 Owner 決定。
