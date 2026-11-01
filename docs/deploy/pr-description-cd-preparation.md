# PR 標題

fix(cd): preserve host refusals and validate controlled host configuration

# PR 說明草稿

受限主機以 `rejected`／exit 2 拒絕尚未驗收的部署時，Actions 原先會將結果改成 unreachable，輪詢約 16 分鐘才逾時。此變更保留明確拒絕並立即失敗；只有 SSH transport exit 255 繼續有界查詢，回覆遺失時不重新提交。

Controlled Compose 預設沿用已實查的 `jukebox101_egress`／`jukebox101_database` 與 Supabase CA 路徑，DB_NAME／DB_USER 必須明確提供。保留 `bot.healthcheck` 的 DB＋Gateway 檢查及全部既有隔離限制，新增預設與明確覆寫設定的 render 驗證，以及錯誤網路／CA／DB 身分、readiness-only、弱 TLS、root、host mount／port 等拒絕測試。

本輪亦準備四 gate 保持 false 的本機設定草稿及首次 bootstrap 檢查表。草稿不進 Git／Docker build context，不包含正式 env 內容或認證。本 PR 不安裝主機設定、不建立認證／journal、不開 gate、不重跑舊 Deploy、不啟用 units 或業務功能。

## 驗證清單

- [x] 完整離線：2149 passed、0 failed、0 skipped；另有 108 項 PostgreSQL opt-in 測試依 `-m 'not postgres'` 排除，本輪沒有宣稱重跑 PostgreSQL。
- [x] 相關 focused 測試：97 passed、0 failed；保留既有安全斷言並增加拒絕情境。
- [x] 真實 Compose render：base、controlled override、controlled host defaults 通過；缺少 DB_NAME／DB_USER 各自拒絕。
- [x] 固定 published amd64 runtime：無網路 import／closed runtime policy／Compose smoke 通過。
- [x] 使用全新空白 Docker credential config，匿名 pull 固定 public digest 成功；未使用或擴大個人 token 權限。
- [ ] GitHub 最新 PR head 的 CI（此文件只準備 PR 內容，尚未開 PR）。
- [ ] 正式資料相容性、主機 GitHub 機器身分與主機匿名 pull。
- [ ] 首次人工 bootstrap、已接受 LKG、systemd／正式 adapter 整合及正式 Deploy。

先前 `c392c2d` 的真實 SSH／synthetic Gateway 整合與隔離回滾證據見 [診斷紀錄](cd-rejected-2026-11-01.md)。它們不能替代上述尚未完成的正式驗收。

## Reviewer 注意事項

- 主機目前四 gate 仍 false、沒有 journal；可審查 candidate 尚未成為正式 baseline。
- `CD_ENABLED` 最近實查為 true；merge main 可能進入 Release → Deploy，故合併必須另行決定，不要自動合併。
- GHCR 已實測 public 匿名可 pull；不要為缺 read:packages 擴權，也不要僅因 docker/config.json 不存在就建立 registry 秘密。
- 本輪發現 `bot.migrate plan/status` 實作仍執行 ledger DDL 與 commit；沒有對正式庫執行或在此 PR 修改它。首次檢查表改用 read-only SQL。
- 最終合併 commit 必須重新產生／審閱 workflow／schema blobs 與主機來源包，不能沿用工作樹草稿冒充正式批准設定。

首次切換順序與需要另行批准的具體項目見 [bootstrap 檢查表](first-bootstrap-checklist.md)。
