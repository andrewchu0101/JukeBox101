# 首次人工 bootstrap 檢查表（待核准計畫）

本文件只準備順序與驗收條件，**不是執行授權**。本輪沒有安裝主機檔案、建立認證／journal、開 gate、啟用 units、送 deploy-v1、重跑舊 Deploy、修改資料庫或功能開關。

現行部署為 Gateway＋PostgreSQL。`deploy/RUNBOOK.md` 是舊 HTTP／MySQL 候選規劃，不能用它的 Portal 切換、MySQL、Oracle 或 HTTP service 步驟操作這台 VPS。現行契約以 [continuous-deployment.md](continuous-deployment.md)、[故障診斷](cd-rejected-2026-11-01.md) 及已安裝程式為準。

## 0. 先確認授權與版本

- [ ] 指定主機 `31.97.108.187`、Compose project `jukebox101`、service `bot`，以及具名操作者、維護起訖時間與聯絡人。
- [ ] 本輪分支 `work/cd-rejected-fix-20261101` 的 PR 說明與完整 diff 經審閱；不得直接 merge。`c392c2d` 只修正拒絕回覆判讀，尚不代表主機初始化完成。
- [ ] 決定維護期間如何處理 repository `CD_ENABLED`。最近實查為 true；若要暫改 false 或恢復 true，必須另行明確批准，本輪不改。合併 main 會觸發 Release，不能把合併當成無正式影響的動作。
- [ ] 不碰其他工作樹或 437 項 CRLF-only 差異；本輪 Mac checkout 開工時為乾淨狀態。

## 1. 選定 candidate 與 rollback，不能先填 journal

| 身分 | 目前可供審查的目標 | 尚缺證據 |
|---|---|---|
| registry baseline candidate | `ghcr.io/andrewchu0101/jukebox101@sha256:01c5ef8a73dafac87b3fba56f94b106201109e9e9eb18211416706aacc9c4601`；來源 `7a8435f`／Release `41284353385/1` | 已有本機真實簽章驗證、匿名 pull、amd64／非 root／health 模組檢查；未在正式資料與 Gateway 上接受 |
| 已接受的 registry rollback／LKG | **目前沒有** | 必須先有經核准、實際健康且資料相容的 registry baseline，不能用另一個只通過 CI 的映像冒充 |
| 現行人工救援版本 | local `jukebox101-bot:v7`，image ID `sha256:655fa3a4efc881218ea8b700823edec6ff973bc34c865c2ed22210065cbd31df` | 只支援 `bot.readiness`；不是 registry digest，也不是 CD LKG。能否在新版寫入後安全退回，尚未驗證 |

- [ ] candidate 固定 digest、source commit、Release run／attempt、來源簽章與被測 image 身分一致，不使用 tag 或 local image ID 代替 registry digest。
- [ ] 如果 main 已因本 PR 合併而前進，先重新選定該 main 的成功 Release；舊 `41284353385/1` 不再符合 current-main 規則，不能重跑 `41285228656` 代替新驗收。
- [ ] 確認 candidate 包含 `bot.healthcheck`／`bot.gateway_health`，且 healthcheck 同時驗證 DB、TLS、Application、Gateway READY 與新鮮心跳。不得降級為 readiness-only 以求綠燈。
- [ ] 寫明**可執行且已驗證相容的** rollback 目標與復原命令；目前此欄仍未滿足。若現有 v7 的回退相容性無法證明，維護計畫必須明確選擇其他已驗證復原方案，不能假設回退一定安全。
- [ ] 在確認復原方案以前不開始首次切換；不得預先建立假的 LKG journal。

## 2. 資料庫與功能相容性（本輪未對正式庫執行）

- [ ] 用原本的受限唯讀連線，在正式庫確認實際 database、app role、PostgreSQL 版本、TLS verify-full。歷史 Supabase 17.11 記錄與拋棄式 17／18 測試都不能代替當輪正式證據。
- [ ] 比對固定 candidate 的 `bot/database/postgres/*.sql` SHA-256 與已套用 ledger；只讀，若缺版本、checksum 不符或權限不足就停止。**CD 不補 migration、不 GRANT、不還原 DB。**
- [ ] 確認目前 snapshot／receipt／Owner review 表示法能被 candidate 及 rollback 版本讀取。若資料含 `duration_verdict`，回滾最低版本須符合 [API 保存回滾限制](../api-retention-release.md)。不得靠關閉開關假裝舊資料已相容。
- [ ] 本輪唯讀觀察：exchange、claim、auto review、auto settlement、API retention 均未啟用；正式新設定必須逐項保留。若之後有任何一項改變，這份關閉功能的準備方案須重審。
- [ ] 保持既有 secret files 與權限；不拷出 `.env`、不把 token／密碼放進命令列或紀錄。

可由具權限操作者另外核准後執行的唯讀 SQL 範例：

```sql
BEGIN READ ONLY;
SELECT current_database(), current_user, current_setting('server_version');
SELECT version, name, sha256
FROM jukebox101.jb_schema_migrations ORDER BY version;
SELECT schema_version, snapshot_ready, legacy_writers_quiesced, settlement_enabled
FROM jukebox101.jb2_control WHERE singleton_id = 1;
SELECT to_regclass('jukebox101.jb2_api_observations'),
       to_regclass('jukebox101.jb2_retention_runs');
ROLLBACK;
```

表不存在或角色沒有 SELECT 權限時，記錄失敗並停止，不建立表、不擴權。`0004` 是否必要取決於已核准的功能模式與相容性證據，不能為了 CD 順手套用。

**不要把 `python -m bot.migrate plan/status` 當成純唯讀檢查。** 雖然 CLI docstring 如此描述，現行 `bot/database/migration_runner.py#run_migrations` 在 `apply=False` 時仍呼叫 `LEDGER_DDL` 的 `CREATE TABLE IF NOT EXISTS` 並 commit。本輪未執行或修改它；這是後續需另案處理的已確認文件／實作落差。

## 3. 設定與認證審查（只準備，不安裝）

- [ ] 審閱 `deploy/controlled/compose.yaml`：預設網路 `jukebox101_egress`／`jukebox101_database`、Supabase CA 路徑、明確 DB_NAME／DB_USER；保留單一 Bot、非 root、唯讀、drop ALL、no-new-privileges、資源上限、無 host ports、TLS 與 combined health。
- [ ] 本機 `configuration-drafts/` 僅有草稿。`bot.env.template` 是非秘密占位值，policy 的 env hash 對應該占位草稿，**不是正式 env hash**；不要安裝或啟動它。
- [ ] 在最終 commit 合併後重新計算 reviewed workflow／schema blobs；CI workflow 的 blob 以及主機審查包中的 `runtime_smoke.py` 都須與最終審閱來源一致。保留 provenance、strict known_hosts 及固定 SSH 協定。
- [ ] 管理者在主機本地計算實際 Compose／env SHA-256 與 metadata，核對 bytes，不輸出內容。若要安裝或更新正式檔案，先列出確切檔案、備份與 rollback 方法，再取得批准；既有首次安裝器會拒絕覆寫，不能拿它當更新器硬跑。
- [ ] GHCR 此固定映像已在本機用空白 Docker credential config 匿名 pull 成功。**public pull 不需要為本機 token 加 read:packages，也不需要先建 registry 登入憑證。** 主機匿名 pull／實際網路路徑仍待另行驗證。
- [ ] `GithubReceiptVerifier` 固定使用 `/etc/jukebox101/gh`；目前沒有已驗收的主機 GitHub 認證。由管理者選定專用最小權限機器身分，逐項驗證 repo／main／git tree／Release attempt／attestation 所需的唯讀能力；不採個人全權 token。建立、輪替、儲存認證或增權都必須先取得明確批准，不能把秘密貼回聊天。
- [ ] `/etc/jukebox101/docker/config.json` 不存在本身不代表 public pull 不能用；不可把它列為必須建立秘密檔的理由。

本機草稿雜湊驗證：

```sh
cd configuration-drafts
shasum -a 256 -c SHA256SUMS
```

主機唯讀計算方式（本文件不執行）：

```sh
sudo sha256sum /etc/jukebox101/compose.cd.yaml /etc/jukebox101/bot.env
sudo stat -c '%U:%G %a %n' /etc/jukebox101/compose.cd.yaml /etc/jukebox101/bot.env
```

## 4. 維護窗口與首次手動切換（每項均未授權／未執行）

- [ ] 先取得包含影響範圍、時間、candidate、相容 rollback、資料相容性與健康條件的明確批准。首次切換會短暫停止唯一 Bot；不能把「準備與驗證」解讀成此授權。
- [ ] 保存現行 container／image ID、Compose 路徑與 env hash、StartedAt、restart count、其他服務／容器／port 的唯讀基線；不輸出秘密。
- [ ] 保留原有設定與映像，以及已批准的復原入口。candidate 的任何可能資料寫入與回退限制須先說清楚；不能把維護通知當成已阻止新寫入的證據。
- [ ] 只在批准後於主機匿名拉取並驗證固定 candidate；拉取本身會寫 Docker cache，屬於本輪未執行的主機狀態變更。
- [ ] 以現行原始 Compose 設定停止 `jukebox101/bot`；不要 `down` 整個 stack、不要碰 DB／Office／其他服務。
- [ ] 確認舊容器／程序已退出，並盤點同 Application 的其他 Gateway。舊 v7 沒有新 label，不能只看新 label filter 就宣稱沒有重疊。
- [ ] 以已審查且批准安裝的 controlled Compose、相同秘密與固定 digest 啟動唯一 candidate；不 build、不 migrate、不 command sync、不執行業務指令。
- [ ] 最多觀察啟動 180 秒；combined health 成功後，再連續觀察 120 秒。全程要求同一容器／image、restart count 不增加、Gateway READY／新鮮 ACK、DB／TLS／身分正確，且沒有第二個 Gateway。

以下任一條件成立即停止驗收：啟動超時、Gateway 失聯／心跳過期、DB／TLS／身分不符、重啟或替換容器、雙 Gateway、設定 drift、功能開關與核准狀態不符。

## 5. 回滾條件與順序

- [ ] 先停止 candidate 並確認退出，才啟動**前面已接受且資料相容的** rollback 目標；不得一邊查錯一邊啟動第二個 Gateway。
- [ ] 不做資料庫還原、不回退 schema、不切換業務開關來偽裝相容。
- [ ] 回滾只嘗試一次；恢復健康仍算 candidate 部署失敗。無法確認退出、回滾也不健康或資料相容性不明時，停止自動嘗試並交由具名操作者處理。
- [ ] SSH timeout 不是取消；若已有受理回執，先查 durable 狀態及獨立 worker，不盲目重送或殺 worker。

## 6. baseline 接受後的 CD 驗收順序

1. [ ] 記錄實際健康的 registry baseline digest、來源證明、資料契約與 120 秒觀察證據。只有此時才能請求批准建立真實 0600 LKG journal；本文件不提供假 digest／假 journal。
2. [ ] 在一次性環境完成 systemd path／timer、service SIGKILL、boot recovery 及正式 Docker adapter 的整合驗收；本機手動 fixture worker 不算 systemd 通過。靜態 unit 檢查也不算 runtime／reboot 證據。
3. [ ] 逐項核對 crypto／Docker／baseline acceptance 證據，再向使用者提出 gate 與 units 的**具體變更清單**，等明確批准後才執行；不能全改 true 當作驗收。
4. [ ] 以最終 main 的新 Release 驗證來源與 current-main，核對成功的持久 `succeeded` 回執、實際唯一 Gateway 與 Actions `deploy` job；開 gate、queue pending、Release 綠燈或 Notify 成功都不是正式部署成功。

本輪只完成計畫與本機驗證，上述正式步驟全部維持待核准／待驗收。
