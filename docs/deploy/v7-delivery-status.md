# v7 Docker 交付狀態（2026-10-30）

對外顯示名稱：**JukeBox101 v1.0.0 (Beta)**；對應本文件的 v7 交付包。此為辨識用名稱，不變更既有包、映像 tag 或程式技術版本。交接清單見 `partner-handoff-v1.0.0-beta.md`。

**COMPLETE：隔離測試與 Docker 交付包完成。正式部署尚未驗收。**

正式交付位置：工作樹 `artifacts/jukebox101-v7/`，主檔 `jukebox101-delivery-v7.tar.gz`，同目錄附 `.sha256`。
包內包含 runtime 映像、目前 bot 原始碼、Dockerfile／Compose、非秘密設定範本、v7 部署手冊、角色設定 SQL、未執行的 MySQL 搬遷方案、第三方授權及確切原始碼、驗證證據、manifest 與校驗碼。

| 驗證 | 結果 |
|---|---|
| 完整離線回歸 | 1747 passed；零 failed／skipped |
| PostgreSQL 18.6 整合 | 35 passed；零 failed／skipped，含多 Guild、TLS、Owner 與 Nickel store |
| 備份／還原 | 各表摘要一致；無 pending migration；還原後繼續投稿成功 |
| 交付映像 CLI | 新建專用拋棄式 DB／角色；status／plan／apply／status／readiness 通過；verify-full TLS |
| 映像身份 | `sha256:655fa3a4efc881218ea8b700823edec6ff973bc34c865c2ed22210065cbd31df`；linux/amd64；10001:10001 |
| 映像逐層稽核 | 8 層；app 與工作樹一致；零 blob／路徑／新增未分類 blocking 問題 |
| Debian 原始碼 | 70 source packages、224 檔、326080701 bytes，簽章鏈離線重驗零問題 |
| psycopg 原始碼 | 3.3.6 sdist 隨包，SHA-256 與 PyPI 相符 |
| 載入與 Compose | docker load 成功；dry-run 不 build／pull；未建立任何容器或網路 |
| 封裝 | 成員安全檢查、獨立解壓、逐檔 SHA-256／manifest、來源簽章、掃描及可重現雜湊比對通過 |

驗證證據：`artifacts/v7/evidence/`；最終封裝驗證：`artifacts/v7/evidence/final-verification.txt`。
`SOURCE-MANIFEST.json` 記錄基準 commit、映像身份及包內逐檔雜湊；`SOURCE-CHANGES.json` 記錄未提交的修改／新增／刪除，以及修改檔 SHA-256。此包不是已提交版本。
測試與完整歷史成果留在工作樹；部署包不包含 legacy/mysql、開發測試套件、秘密或 PDF。

未執行：正式 Bot／Gateway、Discord fetch/apply／REST 同步、真實 Discord 與新 Guild 邀請後人工驗收、正式 PostgreSQL、舊 MySQL 資料搬遷、commit／push。正式 exchange、claim、補償維持關閉。無雲端資源或費用。
原 worktree 與全部 v6 包／映像保留；未 reset 或清理指向 WSL 的 `.git`。

本機測試 TLS 輔助腳本 `scripts/dev/enable-test-tls.sh` 已在另一個全新、隨機命名、無對外連接埠的拋棄式 cluster 實跑，35 項 PostgreSQL 測試再次全過且零 skip。該次專用容器、volume 與網路均已移除；重現入口為 `scripts/dev/verify-fresh-postgres.py`，證據為 `artifacts/v7/evidence/fresh-tls-setup.txt`。
