# VM 啟用程序紀錄（2026-11-03，實際跑過）

本文件記錄 2026-11-03 為啟用自動審核、正式 `/exchange`、`/daily` 在 VM 上實際做過並成功的步驟與踩到的坑。不含任何秘密或專案代碼；`<…>` 為占位。Agent 不連線 VM，以下均由 Owner 執行。

## 踩到的坑

1. **主機的 `/etc/jukebox101/compose.cd.yaml` 不會隨部署更新**，也不會被 `install-host.sh` 覆寫。舊檔沒有自動審核六個開關、`JUKEBOX_FORMAL_DAILY` 與 YouTube 金鑰，所以只改 `bot.env` 不會生效。換檔前先 `diff` 新舊，確認只有預期差異。
2. **VM 沒有 `jukebox101-bot:v7` 映像，也沒有 migrator 密碼檔**；`/opt/jukebox101-gcp/app` 的 clone 可能是舊分支。Bot 只有應用程式帳號，不能改資料表。
3. **Supabase 連線池的登入名稱要加專案後綴**：應用程式帳號是 `<role>.<project-ref>`（健康檢查顯示的是去掉後綴的 `<role>`）。migrator 的登入名稱也要 `jukebox101_migrator.<project-ref>`，少了後綴會得到 `OperationalError`。
4. 全域指令 `fetch` 的 diff 在 `apply` 前後都顯示 5 個 `changed`，原因未確認（推測為 Discord 回傳的預設欄位）；`remote_digest` 在 `apply` 後有改變。

## 步驟（成功的順序）

1. 部署新映像、開關維持 off，`python -m bot.healthcheck` 回 `ready`。
2. 備份 Supabase。
3. 用**已部署映像**跑 migration（映像工作目錄 `/app`，migration 檔在 `/app/bot/database/postgres`）：暫時建立 `/root/db_migrator_password.txt`（擁有者 10001、0400，用 `read -rs` 輸入，不進指令歷史），以 `docker run --entrypoint python <image> -m bot.migrate plan`，看過待套用清單後 `apply --apply`，再 `status` 確認 `pending_before: []`。環境用 `--env-file /etc/jukebox101/bot.env`，並以 `-e DB_USER=<migrator 登入名稱>`、`-e DB_PASSWORD_FILE=…`、掛載 CA 憑證與密碼檔、`--network jukebox101_egress`。**用完 `shred -u` 刪除密碼檔。**
4. `python -m bot.command_sync preview`（離線）→ `fetch`（讀 Discord）→ `apply --target global --i-understand-this-calls-discord --confirm-overwrite-remote-commands --expected-digest <local_digest>`。Owner 指令內容未變時不需要 `--target owner-guild`。
5. 備份後替換主機 compose：`sudo install -m 0644 -o root -g root <新檔> /etc/jukebox101/compose.cd.yaml`（新檔取自 `deploy/controlled/compose.yaml`）。
6. 編輯 `bot.env`，加入 `JUKEBOX_AUTO_REVIEW`、`JUKEBOX_API_RETENTION`、`JUKEBOX_AUTO_VERSION_CONFIRM`、`JUKEBOX_AUTO_TRUST_MODE=music_category`、`JUKEBOX_FORMAL_EXCHANGE`、`JUKEBOX_FORMAL_DAILY`（先不加 `JUKEBOX_AUTO_SETTLEMENT`）。
7. 推新 commit 讓 CD 重建容器（同一 run 重跑會被拒絕），再 `healthcheck`、`docker logs`。
8. 處理首筆案件後，才開自動發獎（資料庫旗標與 `JUKEBOX_AUTO_SETTLEMENT` 兩道鎖，必須一致）。

## 回滾

- compose：還原備份檔。
- 功能：`bot.env` 改回 off 再觸發 CD；發獎另需 `settlement-switch write --enabled off`。
- migration 0004、0005 不可回滾，只能靠備份。
