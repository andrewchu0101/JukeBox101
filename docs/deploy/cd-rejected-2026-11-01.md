# Release 41284353385/1 部署拒絕診斷與修正

日期：2026-11-01，Asia/Taipei。基準 main：`7a8435f4360b2e9ce007ba0745a7909fc64cd151`。獨立分支：`work/cd-rejected-fix-20261101`。先前 worktrees 與未提交工作保留；本次交付依後續授權 commit／push 此獨立分支，不包含 main merge 或正式部署重試。

## 根因

主機仍處於安裝後、驗收前的停用狀態。透過既有、已登入的 Hostinger Web console，以 root 執行已安裝程式的唯讀檢查，`load_policy()` 驗證 metadata／格式成功，`require_enabled(policy)` 明確回報 **`ReceiptRefused: CD_NOT_ACCEPTED`**。沒有送出 deploy-v1，也沒有啟動 worker 或 Bot。

四個 gate 均為 false；此條件在 queue 寫入之前就拒絕請求。故指定 run 沒有持久回執，並非已成功部署後回覆遺失。獨立存在的 Actions 判讀缺陷又把 rejected/exit-2 覆蓋成 unreachable，造成約 16 分鐘輪詢後只顯示無成功回執。

## 四項主機查核

| 查核 | 實際結果 |
|---|---|
| forced command | sshd `Match User jukebox-deploy` 與 `/etc/ssh/authorized_keys/jukebox-deploy` 都指向 `/usr/local/libexec/jukebox101-cd-ssh`；該程式 sudo 到 `jukebox101-cd-ingress`，再執行 `deploy.host_ingress`，不是舊 `deploy.ssh_request` |
| 已安裝來源 | ingress、worker、host_policy、receipt 四個 Python 模組 SHA-256 均與目前 main 相同 |
| 專用公鑰 | 指紋 `SHA256:aD+zlKsq08zrPvUlekBmCVhMX96NTLHa74iSziVGeTw`；與人工維運金鑰不同；未讀取私鑰 |
| 四 gate | `enabled=false`、`crypto_acceptance_verified=false`、`docker_acceptance_verified=false`、`baseline_supports_gateway_health=false` |
| metadata／路徑 | policy root:root／0600，state 與 requests root:root／0700，非 symlink；實際固定 queue 為 `/var/lib/jukebox101-cd/requests`；這些權限本身符合要求 |
| 持久回執 | `requests/41284353385-1.json` 不存在；`journal.json` 也不存在 |
| worker | service loaded／inactive／dead，path 與 timer loaded／disabled／inactive，未見 ExecMainStartTimestamp |
| worker 日誌 | root 執行 `journalctl -u jukebox101-cd.service -n 100 -o json` 成功，0 entries；沒有可供追查的 worker 執行紀錄，不能把沒有錯誤日誌當成健康證據 |

受限維運 SSH 不足以讀取上述 root-owned 檔案。先以既有 neighbor 帳號讀取公開設定，確認無 sudo 後，改用 Chrome 中已登入的 hPanel Web console 完成 root 唯讀診斷；沒有新增 SSH key、放寬權限或變更登入方式。

## 即使開 gate 仍會失敗的其他條件

- policy 的 Compose／env hash 都還是全零占位。主機實際執行 `require_file_hash` 對兩者都回報 `CONFIG_DRIFT`；沒有修改檔案。
- policy 的 network_names 還是 `REVIEW_EXISTING_EGRESS`、`REVIEW_EXISTING_DATABASE_NETWORK`。目前 Bot 實際使用 `jukebox101_egress`、`jukebox101_database`。
- `/etc/jukebox101/compose.cd.yaml` 還是 repo 原範本，SHA-256 與 `deploy/controlled/compose.yaml` 相同。範本 CA 路徑為 `db_ca.crt`，現有 Bot 掛載的是 `supabase-ca-2021.crt`，須審查適配。
- `/usr/bin/gh` 已存在，但固定位置的 `/etc/jukebox101/docker/config.json`、`/etc/jukebox101/gh/hosts.yml` 都不存在。尚無這套 adapter 的專用主機認證驗收證據；未使用個人全權 token 代替。
- 目前 Bot 是 local `jukebox101-bot:v7`，image ID `sha256:655fa3a4efc881218ea8b700823edec6ff973bc34c865c2ed22210065cbd31df`，healthcheck 僅為 `bot.readiness`，尚非已接受的 registry LKG。StartedAt 仍為 `2026-10-30T16:35:18.298185854Z`，running／healthy。

因此不能只把 gate 改 true、補一份假 journal，或啟動 units 後重跑 Actions。

## 已完成的程式修正與驗證

`.github/workflows/deploy.yml` 現在同時判讀 stdout 與 SSH exit code：

- `rejected`／exit 2 保留為明確終止失敗，不再轉成 unreachable。
- exit 255 才視為 SSH transport 失敗，維持原本有界、同 run／attempt 的 status 查詢。
- 成功只接受 `succeeded`／exit 0；其他不相容輸出與退出碼拒絕。
- SSH 提交回覆遺失時仍只查詢持久狀態，不重新提交。rolled_back／rollback_failed 仍算部署失敗。
- 觸發條件、secrets、environment、併發鎖、來源驗證、主機 gate 與 strict known_hosts 規則均保留。

新增測試直接執行 workflow 的 Bash 內容，只替換 SSH／sleep 為本機假工具。修正前重現 **2 failed、6 passed**；修正後連同 CI 邊界、queue、release resolver 與 Notify 回歸 **94 passed、0 failed**，只有既有 audioop 棄用警告。測試使用既有 Python 3.12 非 root／無網路容器；未使用正式金鑰、VPS、資料庫或 Discord。

依後續「請測試」追加執行：完整離線回歸 **2105 passed、0 skipped、0 failures、0 errors**；真實本機 SSH/sudo 的 **20 項**情境與真實 Compose 的成功／回滾／回滾失敗 **3 項**情境通過。

另以修改後的實際 workflow Bash 搭配真實 loopback OpenSSH、sudo、已出貨 ingress／queue／controller，完成成功回執與閉鎖拒絕 **2 項整合測試**。只將 port 22 對應至一次性容器的隨機 loopback port；授權、Gateway 為 synthetic，worker 手動驅動，沒有驗證 systemd。成功情境收到 `succeeded` 並 exit 0；閉鎖情境在 **0.49 秒**內回報 `Deployment rejected` 並 exit 1，沒有寫入 queue。

Compose 初次執行曾在 image inspect 階段失敗；確認既有映像存在後，以明確 `TEST_IMAGE=jukebox101-test:cd-acceptance-20261031` 重跑三情境通過。第一次失敗的確切環境原因未確定，已與成功結果一併保存在 `/Users/panh.c/Documents/Codex/2026-11-01/jukebox-cd-test-evidence/`，未放寬斷言。追加測試時唯讀確認正式 path／timer 仍停用；正式部署、systemd 與真實 Gateway 驗收仍未執行。

[Release](https://github.com/andrewchu0101/JukeBox101/actions/runs/41284353385) 的 resolver 與官方 gh attestation 也在本機重新通過，固定候選 digest 為 `sha256:01c5ef8a73dafac87b3fba56f94b106201109e9e9eb18211416706aacc9c4601`；與 [Deploy](https://github.com/andrewchu0101/JukeBox101/actions/runs/41285228656) 使用的值相同。這不代表主機的機器憑證或 baseline 已驗收。

## 交付與剩餘工作

本機證據位於 `/Users/panh.c/Documents/Codex/2026-11-01/jukebox-cd-rejected-evidence/`。其中 root inventory 的選定欄位抄錄自本輪 Web console 截圖；GitHub 日誌、receipt、來源證明另保存原始檔。另有不讀取秘密值的診斷工具與 `configuration-drafts/`：依本輪實查填入網路／CA 路徑、env 雜湊與本機 reviewed blobs；**四 gate 全 false、未安裝、不建立 journal**。

真正可成功部署仍需依原部署文件完成：

1. 審查並安裝與現行設定／功能相容的 Compose／policy，配置主機專用最小權限 GitHub／GHCR 認證。診斷確認了問題位置，不能取代機器帳號授權。
2. 完成 systemd／正式 adapter 隔離整合與資料相容性檢查，準備已驗證 registry candidate 及相容 rollback，再於獲准的維護時段做首次人工 bootstrap。現有 local v7 不能冒充 registry baseline；本輪沒有改資料庫或功能開關。
3. baseline 實際健康後才建立真實 LKG journal，逐項接受四 gate，最後啟用 units 並驗收正式 CD。本輪依「不重試部署、不強開 gate」要求未執行這一階段。

合併本修正後，需依最終 commit 重新產生 reviewed blobs／審查包；Deploy workflow 本身也在 approved_blobs 中。舊 run `41284353385/1` 屆時不再代表 current main，不應用重跑舊版本代替新的 Release 驗收。
