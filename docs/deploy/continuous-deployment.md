# JukeBox101 持續部署（歷史設計，現已停用）

## 目前生效的規則（PR #9 分支）

PR #9 將測試與正式發布分離：PR／main 只執行安全的假資料 CI。Release、Deploy 已改成 `workflow_dispatch` 的未啟用佔位 job，沒有任何 GHCR 發布、SSH 或 production secret 使用。Notify 只追蹤 CI。設定 `CD_ENABLED=true` 不會恢復已移除的發布／部署流程。

下面保留原本的架構與安裝方案供未來另案審閱，**不是目前可執行的上線步驟**。不要依此安裝主機服務、改開關或假設合併 main 會發布。未來正式發布須另開變更，重新安排 runtime／DB 相容性、來源驗證與 VPS 驗收；本次沒有修改主機、外部設定或 production secrets。

現行操作與證據見 [CI/CD 快速隔離演練](cicd-smoke.md)。

## 以下為原設計的歷史參考

## 原設計執行後會發生什麼

PR 只測試，不取用部署秘密。合併 main 後，`Release` 呼叫同一個 CI，建立一次 runtime，對同一 image ID 執行離線、Compose、PostgreSQL 18.6／17.11 與 TLS 驗證。成功後才把這個映像推送 GHCR，產生 GitHub OIDC 來源證明與 `release.json`。不在測試後重建部署映像。

`Release` 完成成功後，另一個 `Deploy` workflow 才可能執行。它要求 repo variable `CD_ENABLED=true`，先核對目前 main、固定 repo、成功的 push run／attempt，再讀取該次唯一的 receipt。依 2026-10-31 使用者選擇，正式驗收與 CD 啟用後採全自動部署；`production` Environment 保留 main 限制及專用 secrets，日常部署不設 required reviewers／wait timer。首次主機安裝、baseline 與來源證明仍須完成實際驗收，不能跳過驗收直接開 gate。部署 job 不 checkout、不持有 GitHub Token，只能用專用 SSH key 送固定協定。

主機獨立驗證來源證明、核准的 workflow／schema blob、固定設定雜湊與秘密檔 metadata。先下載新版，確認上一穩定版健康，再停舊 Bot、確認退出、啟動唯一新版。啟動上限 180 秒，再連續觀察 120 秒。Discord 有短暫維護中斷。新版失敗只嘗試回滾一次；`rolled_back` 也會讓 Actions 失敗，`rollback_failed` 鎖住後續部署並要求維運介入。

CD 不執行 migration、資料還原、command sync、補償或業務指令，不管理 PostgreSQL／Office／Caddy／DNS／防火牆。formal exchange 與 claim 固定 off。自動審核／API 保存及其他新功能的現行設定，須在 bootstrap 前逐項盤點，不能用範本默默覆寫。

## 元件與權限

| 元件 | 權限與用途 |
|---|---|
| `.github/workflows/ci.yml` | PR／main／reusable CI，只有 contents:read。只有 release caller 要求時，輸出該 run/attempt 的已測映像 artifact |
| `.github/workflows/release.yml` | main push；測試 job 無秘密，publish job 才有 packages／attestations／id-token:write。GHCR 初次發布預設私有；不得自行改為公開 |
| `.github/workflows/deploy.yml` | 成功 Release 的 workflow_run；resolve 只有 contents/actions:read。SSH job 使用 production Environment、同 repo 固定併發鎖，不取消進行中的部署 |
| `deploy/release_request.py` | 嚴格核對 receipt/run/attempt，拒絕過期版本、fork、PR、模糊或過期 artifact、ZIP 路徑與輸出注入；不把 artifact 當成主機授權 |
| `deploy/host_ingress.py` | root 固定 stdin 協定、有界持久佇列。SSH 中斷不會取消已受理的工作 |
| `deploy/host_worker.py` | systemd 執行的獨立 root worker，使用固定 Docker socket／Compose／project，驗證簽章與 journal，再執行部署／回滾 |

CI 另執行 `scripts/dev/verify-cd-docker.py`：用隨機 project、無網路、無秘密的真實 Compose 容器，演練成功、回滾及回滾失敗；health 與 release identity 是 synthetic。此演練不能取代正式 adapter、真實 Gateway、GHCR 簽章或 SSH/systemd 驗收。

GitHub `workflow_run` 有獨立的權限邊界，不能接受 PR artifacts。來源查核與環境 gate 是必要條件。來源：[GitHub workflow_run](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run)、[gh attestation verify](https://cli.github.com/manual/gh_attestation_verify)。

## 首次安裝與啟用

這是分開的階段；合併 workflow 不代表正式 CD 已啟用。沒有完成的驗收不得透過把 policy boolean 設為 true 來代替。

1. Repo 管理員保護 main：要求 PR、必要 CI、審查，禁止 force push／刪除。建立 `production` Environment，限制 main 並保存專用 secrets；確認 release job 的 GHCR／OIDC／attestation 權限。依已選定的全自動模式，驗收完成啟用時不設定 required reviewers 或 wait timer。main 的 PR／CI／審查保護仍保留；合併後不再逐次核准部署。
2. 先在隔離環境驗證 Release 的實際映像、GHCR push、`gh attestation verify` 真實輸出與 verifier 的精確欄位契約。目前 verifier 的單元測試使用 synthetic fixture，不能當作密碼學驗收。主機需要固定 `/usr/bin/gh`、`/usr/bin/docker`、`/usr/bin/python3`；controller 僅用標準庫及包內 runtime policy helper。
3. 重新盤點正式環境：Bot image ID／command／labels、實際 Compose/env、功能開關、秘密檔 uid/gid/mode、TLS CA、database 名稱／使用者／埠、網路及所有相同 Application 的 Gateway。Supabase 可能使用 `postgres` 與帶專案 suffix 的角色，不能沿用歷史 `jukebox101` 假設。root Compose 的 DB_NAME／DB_USER／DB_PORT 由核准的 env 提供。
4. 在 repo 根目錄執行 `sh scripts/deploy/package-cd.sh`，產生 `artifacts/cd-host.<random>.tar.gz` 與 SHA-256。包內只有明列的 controller、policy helper、units、Compose 及文件；不含 secrets、映像、測試資料。`approved_blobs` 由目前工作樹計算，須確認與實際合併 commit 相同；若合併時有修改，重新封裝與審查。
5. 管理員建立專用 `jukebox-deploy` 系統帳號，不給 Docker group 或一般 sudo。在 root 管理且 deploy 帳號不可寫的 home／`.ssh`／`authorized_keys` 安裝專用公鑰。SSH 登入只使用這一行格式：

   ```text
   restrict,command="/usr/local/libexec/jukebox101-cd-ssh" ssh-ed25519 <專用公鑰>
   ```

   `authorized_keys` 與父目錄必須是 root 所有且 deploy 帳號可讀／可通行（例如目錄 0755、檔案 0644；公鑰不是秘密）；帳號不能修改它們。停用該帳號的密碼登入，且不可另放未受限 key。不要重用 Office／root／個人 SSH key。私鑰只放 GitHub production Environment。
6. 解開審查包，先執行 `sh scripts/deploy/install-cd-host.sh` 看計畫，再以 root 執行 `sh scripts/deploy/install-cd-host.sh --apply`。安裝器只接受首次安裝，拒絕覆寫既有 controller／policy／Compose／journal。它不安裝帳號/key、啟動 units、啟停 Bot 或讀取秘密。安裝後所有 gate 維持 false。
7. 把 root Compose 與既有實際設定比對，保留已核准業務行為與網路。把實際非秘密 `/etc/jukebox101/compose.cd.yaml`、`/etc/jukebox101/bot.env` 的 SHA-256、metadata 與 approved blob 清單填入 policy；填入固定 Application ID、network names、秘密檔路徑。不要把展開的 env/config 或秘密印到日誌。GHCR 私有拉取與 gh 驗證使用主機端獨立唯讀機器帳號憑證，分別存在 `/etc/jukebox101/docker`、`/etc/jukebox101/gh`，不使用個人全權 PAT。
8. **人工 bootstrap**：先保存當前真正 image ID／相容設定及復原入口。舊 local v7 不能冒充 GHCR digest，也可能不支援新 `bot.healthcheck`。取得一份經驗證、與現有資料／設定相容且支援 Gateway health 的 GHCR baseline，依既有維護手冊手動完成 stop-before-start 切換及完整健康觀察。需要寫入新資料格式的版本，其回滾最低版本以 `api-retention-release.md` 為準；不要還原資料庫來完成 Bot 回滾。
9. baseline 真實健康後，才以 root 建立 `/var/lib/jukebox101-cd/journal.json`（0600），內容如下；digest 必須是實測的 registry digest，不能是 local config/image ID。

   ```json
   {"version":1,"last_known_good":"sha256:<已驗證 baseline registry digest>","highwater":0,"requests":{}}
   ```

10. 在隔離 Docker 與 SSH/systemd 環境實測成功、新版失聯回滾、SSH 中斷、worker 中斷、重啟恢復與回滾失敗；核對受限帳號不能取得 shell／執行任意 sudo、舊版健康訊號相容且無雙 Gateway。真實 CLI provenance、Docker 與 baseline 接受後，才把四個 gate（`enabled`、`crypto_acceptance_verified`、`docker_acceptance_verified`、`baseline_supports_gateway_health`）設為 true。
11. `systemctl daemon-reload`，再 `systemctl enable --now jukebox101-cd.path jukebox101-cd.timer`。path 接收新 request，timer 在開機或 worker 退出後重新處理未完成 journal；不依賴 SSH session 存活。這兩個 unit 只啟動 JukeBox worker。
12. 設 GitHub Environment secrets `DEPLOY_SSH_KEY`、`DEPLOY_KNOWN_HOSTS`（透過可信既有通道核對 host key，不能臨場 ssh-keyscan 後直接信任），以及 Environment variable `DEPLOY_HOST`。最後設定 **repository variable** `CD_ENABLED=true`；它必須在 repo 層，resolve job 才看得到。之後一次 main push 會進入 Release → Deploy 自動部署。尚未完成或已過期的先前 release 不會因開 gate 自動補部署。

## 兩位維護者的 GitHub／Email 通知

通知集中在 [CI/CD 執行通知 Issue #7](https://github.com/andrewchu0101/JukeBox101/issues/7)。兩人各用自己的帳號按 **Subscribe（訂閱）**，再到 [個人通知設定](https://github.com/settings/notifications) 開啟參與／訂閱對話的 **On GitHub** 與 **Email**，並確認接收地址已驗證。這些是每人帳號的個別設定，專案無法替另一個帳號開啟或確認 Email 送達。

原生 Actions 通知主要發送給觸發該 run 的使用者；只開啟個人 Actions 通知，無法保證兩位每次都收到。`Notify` 改用 `workflow_run`，在 CI、Release、Deploy 完成後向固定 Issue 新增結果。通知 workflow 必須先合併到 main 才會生效；本次首次建立 Issue 不等於通知已自動啟用。

通知程式只 checkout 通知 workflow 本身的可信預設分支 snapshot，不 checkout 觸發的 PR、不下載 PR artifacts、不讀取部署 secrets。每次重新查核固定 repo／workflow／run／attempt，只張貼固定狀態、commit 與 Actions 連結，沒有 PR 標題、日誌、秘密或使用者內容。GitHub Actions bot 的同一 run／attempt 不重複貼文，不同 run 不共用會取代排隊工作的 concurrency group。

Deploy 的訊息讀取實際 `deploy` job 結果；被跳過時明確顯示未部署，不會因其他 job 綠燈宣稱上線。失敗／中斷時請到該 Actions 連結確認主機狀態與回滾結果。通知與部署獨立：Email 的傳遞或 Notify job 的失敗都不會觸發、阻止或重跑 CD。關閉 Issue #7 會讓後續通知 job 拒絕寫入。

官方說明：[Actions 通知對象](https://docs.github.com/en/actions/concepts/workflows-and-actions/notifications-for-workflow-runs)、[通知與訂閱](https://docs.github.com/en/subscriptions-and-notifications/concepts/about-notifications)、[個人通知設定](https://docs.github.com/en/subscriptions-and-notifications/get-started/configuring-notifications)。

## 停用與故障

- 要停止未來部署，先把 repository variable `CD_ENABLED` 改為 false，再將 root policy `enabled` 設為 false；保留正在執行 transaction 的 journal，先查狀態再決定維護操作。不要直接中斷正在切換的 worker。
- 查看 Actions 的 Release／Deploy 結果及 root journal 的安全狀態欄位。查詢 `status-v1 <release-run> <attempt>` 只回一個狀態字，不回秘密或完整 journal。
- GitHub timeout 不代表主機停止處理；先查 receipt，不能因此手動啟動第二個 Gateway。
- worker `rollback_failed` 時不再自動重試部署或反覆回滾。由維運核對實際唯一容器、資料相容性與健康，再另行復原，不改寫 journal 來偽裝成功。
- Queue／journal 有 128 requests 上限；目前沒有自動清理歷史。接近上限時須在 worker 閒置、保留安全稽核紀錄與 highwater 的前提下另行維護，不執行廣泛 Docker 清理。

## 本次交付狀態（2026-10-31）

本文件與 workflow 是可執行的設定與首次安裝包。測試證據另記本次 PR；未完成的 GHCR／主機／真實 Discord 驗收不算通過。現有管理連線 `neighbor-vps` 可 SSH，但無 Docker 管理權；連線帳號名稱與 sudo 拒絕已核對，未取得或變更正式秘密。Repo 連線有 push、無 admin，不能替管理員設定 branch protection 或 Environment。
