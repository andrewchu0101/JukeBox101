# JukeBox101 持續部署（2026-11-01 精簡版）

> 原 Hostinger VPS 狀態（2026-11-02）：已停止 JukeBox101 服務並撤銷專用帳號存取，舊的 repository secret `HOSTINGER_SSH_KEY` 也已移除。GitHub 實際設定的 `CD_ENABLED`（2026-11-02 以 `gh variable list` 實查）為 `true`，Production 的 `DEPLOY_HOST` 指向 GCP VM；推送 main 會自動部署到該 VM，該目標、環境憑證與本文件的部署流程由專案維護者管理。此手冊不構成重新連回原 VPS 的授權。驗證與交接見 [VPS 停用紀錄](vps-retirement-2026-11-02.md)。

> 取代先前的 queue／worker／journal／四道 gate 架構。舊架構的診斷留在 [cd-rejected-2026-11-01.md](cd-rejected-2026-11-01.md) 供追溯，不再是現行做法。

## 合併 main 之後會發生什麼

```
push main → CI 測試 → 發布同一個已測映像到 GHCR（附 GitHub 來源證明）
          → deploy job：SSH 登入主機，送出一行「請換成這個 digest」
          → 主機腳本：拉映像 → 核對標籤 → 停舊 Bot → 啟動新 Bot → 健康觀察
          → 成功：Actions 綠燈　失敗：自動換回舊映像，Actions 紅燈
```

全部在 `.github/workflows/release.yml`（一條流水線，不再有獨立的 Deploy workflow，也不靠 artifact 傳遞 digest）。主機端只有一支腳本：`deploy/host/jukebox101-deploy`。

## 主機腳本做的事（與不做的事）

**接受的唯一輸入**：`deploy-v2 <commit> <digest> <run> <attempt>`，一行，其他一律 `rejected`。

**停任何東西之前，先全部檢查**：

| 檢查 | 目的 |
|---|---|
| 沒有 `locked` 檔 | 上次回滾失敗時，要有人看過才能再部署 |
| compose／bot.env 為 root 擁有、群組與其他人不可寫 | 不信任別人改得動的設定 |
| run 編號大於已部署過的最高值 | 舊的 workflow 不能把主機倒退成舊版本 |
| 映像只來自 `ghcr.io/andrewchu0101/jukebox101@sha256:…` | 金鑰外洩時，只能部署我們自己命名空間的映像 |
| 映像標籤 `revision` = 請求的 commit、`source` = 本 repo、使用者 = 10001 | 把 digest 綁回 CI 測過的那個 commit |
| 同一個 Discord 應用程式最多只有一個 Bot 在跑 | 避免兩個 Gateway 同時連線 |

**切換**：記下目前執行中的映像並標為 `jukebox101-rollback:previous`（即使它從未進過 registry，例如本機 v7）→ 停舊 Bot 並確認已停 → 啟動新 Bot，等 Docker healthcheck 通過（最多 180 秒）→ 再觀察 60 秒，同一個容器、不重啟、持續 healthy。失敗只回滾一次；回滾也失敗就建立 `locked`。

**回覆**：`succeeded`、`rolled_back`（新版不健康，已換回舊版，Actions 仍然紅燈）、`rollback_failed`、`rejected`、`running`。同一個 run／attempt 重送會得到同一個答案，不會重複部署，所以 SSH 斷線後 workflow 只是再問一次。

**不做**：不執行資料庫 migration、不還原資料、不同步指令、不管理 PostgreSQL／Caddy／DNS、不讀取或印出秘密。formal exchange 與 claim 由 compose 固定 off。

## 主機需要的東西

| 路徑 | 內容 | 誰準備 |
|---|---|---|
| `/usr/local/libexec/jukebox101-deploy` | 部署腳本 | `install-host.sh` |
| `/etc/sudoers.d/jukebox101-deploy` | 只允許 `jukebox-deploy` 無參數執行上面那支腳本 | `install-host.sh` |
| `/etc/ssh/authorized_keys/jukebox-deploy` | 一行受限公鑰（`restrict,command="sudo -n …"`） | `install-host.sh` |
| `/etc/jukebox101/compose.cd.yaml` | 單一 `bot` 服務；已存在就保留，不覆寫 | 既有或安裝器放範本 |
| `/etc/jukebox101/bot.env` | `DB_HOST`、`DB_NAME`、`DB_USER`、`DISCORD_EXPECTED_APPLICATION_ID`、`DISCORD_OWNER_USER_ID` 等；選用 `DISCORD_OWNER_GUILD_ID`（未設時程式刻意不註冊 `/owner` 指令） | 管理者（既有） |
| `/etc/jukebox101/secrets/*.txt`、CA 檔 | Discord token、DB 密碼、TLS CA（0400，uid/gid 10001） | 管理者（既有） |
| `/etc/jukebox101/deploy.conf`（選用） | 只有一行 `LEGACY_CONTAINER=<容器名稱>`：第一次接手一個沒有 application 標籤的舊 Bot 時使用 | 管理者 |
| `/var/lib/jukebox101-deploy/` | 腳本自己的狀態（最高 run、結果、`locked`） | 腳本 |

沒有 policy 雜湊、`approved_blobs`、journal、systemd unit、gate 或 GitHub 機器身分。修改 compose 不需要重算任何雜湊。

## 一次性設定（維護者一個人，約 15 分鐘）

**1. 產生新的專用 SSH key（在你自己的電腦，不是主機；私鑰不要傳給任何人，包括 Claude）**

```sh
ssh-keygen -t ed25519 -N '' -C jukebox101-deploy-2026-11 -f ./jukebox101-deploy-key
```

得到 `jukebox101-deploy-key`（私鑰）與 `jukebox101-deploy-key.pub`（公鑰）。不要重用個人、Office 或 root 的 key。

**2. 在主機安裝（以 root；只需要公鑰檔）。Google Cloud 的 Debian／Ubuntu 映像已有 `sudo`、`useradd`、`flock`**

```sh
git clone https://github.com/andrewchu0101/JukeBox101.git && cd JukeBox101   # 或更新既有 clone 到 main
sh scripts/deploy/install-host.sh --pubkey /path/to/jukebox101-deploy-key.pub            # 先看計畫
sh scripts/deploy/install-host.sh --pubkey /path/to/jukebox101-deploy-key.pub --apply    # 實際安裝
```

可重複執行；**輪替金鑰就是用新公鑰再跑一次 `--apply`**，舊 key 立刻失效。安裝器不會動執行中的 Bot、秘密、bot.env 或既有的 compose.cd.yaml，結尾會印出新 key 的指紋，請與你產生的核對。

**3. 取得主機指紋（在主機上執行）**

```sh
awk -v h=<DEPLOY_HOST 的值> '{print h, $1, $2}' /etc/ssh/ssh_host_ed25519_key.pub
```

輸出一行即為 `DEPLOY_KNOWN_HOSTS`。不要在 Actions 裡臨時 `ssh-keyscan`。

**4. 設定 GitHub**

- Environment `production`（限制只有 main 可部署）→ Secrets：`DEPLOY_SSH_KEY`（私鑰全文）、`DEPLOY_KNOWN_HOSTS`（上一步那行）。
- Variables：`DEPLOY_HOST`（主機名稱或 IP；目前設在 Environment `production`，設在 Repository 層也讀得到）、Repository variable `CD_ENABLED=true`（總開關，必須在 Repository 層，否則 job 的 `if` 讀不到）。
- 設完後刪掉本機的私鑰檔：`shred -u jukebox101-deploy-key` 或安全刪除。

**5. 放好設定與秘密（第一次部署前）**

- `/etc/jukebox101/bot.env`：root 擁有、0600 或 0644，群組與其他人不可寫。至少含 `DB_HOST`、`DB_NAME`、`DB_USER`、`DISCORD_EXPECTED_APPLICATION_ID`、`DISCORD_OWNER_USER_ID`。
- 秘密檔放 `/etc/jukebox101/secrets/`（安裝器已建好目錄）：`discord_bot_token.txt`、`db_password.txt`，**擁有者 `10001:10001`、權限 `0400`**。Bot 自己的檢查只接受這個組合，`chmod 600` 或 andrew 擁有都會被拒絕。
  ```sh
  sudo install -o 10001 -g 10001 -m 0400 /dev/stdin /etc/jukebox101/secrets/discord_bot_token.txt   # 貼上後按 Ctrl-D
  ```
- Supabase CA 檔放 `/etc/jukebox101/supabase-ca-2021.crt`（compose 預設路徑；放別處就設 `JB_DB_CA_FILE`）。
- 這是全新主機、沒有舊 Bot，所以不需要 `deploy.conf`，第一次部署也沒有回滾目標：失敗時回報 `rollback_failed` 並鎖住，代表主機上沒有任何 Bot 在跑，修好後刪除 `locked` 再重推即可。
- **先停掉其他地方的 Bot**（例如舊的 Hostinger）。同一個 Discord token 不能兩處同時連線。

## Google Cloud 注意事項

| 項目 | 做法 |
|---|---|
| VM | e2-micro（1GB、amd64）可跑；compose 上限 512MB，建議保留 2GB swap。映像為 linux/amd64，**不要選 ARM 機型** |
| 外部 IP | 預設是暫時 IP，VM 停機再開機可能變，`DEPLOY_HOST` 一變部署就失敗。請保留靜態 IP 或使用網域；外部 IPv4 是否收費請看帳單頁與預算警示 |
| 防火牆 | GitHub Actions 的來源 IP 不固定，port 22 必須允許任意來源。安全性靠「只有受限的專用 key 能登入、不能開 shell」，個人登入用的 key 請另外保管，並關閉密碼登入 |
| SSH key | 用途分開，各一把：登入 VM、VM 讀 GitHub（唯讀 deploy key，只在 clone repo 跑 `install-host.sh` 時用）、CD（`DEPLOY_SSH_KEY`）。**CD 這把不要與前兩把共用** |
| 映像來源 | 部署不需要在 VM 上 clone repo，Docker 直接從 GHCR 拉映像（package 為 public 時免認證；改為 private 時把憑證放在 `/var/lib/jukebox101-deploy/docker/config.json`） |
| Bot 網路 | 安裝器會建立 `jukebox101_egress`、`jukebox101_database`（兩個一般 bridge 網路，沒有額外限制）；日後想收緊出站規則再另行設計 |

## 日常操作

| 想做的事 | 做法 |
|---|---|
| 暫停自動部署 | Repository variable `CD_ENABLED` 改為 `false`（程式不用動） |
| 看結果 | Actions 的 `Release` run；`Notify` 會把結果（含部署 job）貼到 [Issue #7](https://github.com/andrewchu0101/JukeBox101/issues/7) |
| `rolled_back` | 新版在主機上不健康、已換回舊版。看 `docker logs` 找原因，修正後再 push |
| `rollback_failed` | 主機鎖住。確認 Bot 只有一個容器且健康後，刪除 `/var/lib/jukebox101-deploy/locked` |
| `rejected` | Actions 日誌會有 `jukebox101-deploy:` 開頭的一行原因（主機腳本寫到 stderr）：run 編號過舊、映像標籤不符、`locked` 存在、compose／bot.env 權限被改動、`DISCORD_EXPECTED_APPLICATION_ID` 缺漏、已有多個 Gateway 等 |
| 部署失敗但原因不明 | 日誌只放固定句子（repository 是 public，日誌任何人看得到）。`docker compose up` 的原始輸出存在主機 `/var/lib/jukebox101-deploy/last-compose-failure.log`（root 才讀得到），登入後 `sudo cat` 查看 |
| 只改了 `bot.env` | 主機要求 run 編號嚴格遞增，**同一個 run 在成功部署後按 Re-run 會被拒絕**。請推一個新 commit 讓 CD 重建容器；不要手動重建，會繞過 CD 的狀態紀錄 |
| 更新主機腳本 | 部署只換 Bot 映像，不會更新 `/usr/local/libexec/jukebox101-deploy`。修改 `deploy/host/jukebox101-deploy` 後，要在 VM 更新 clone 並重跑 `install-host.sh --pubkey <受限公鑰檔> --apply` 才生效 |
| 看主機最後狀態 | `cat /var/lib/jukebox101-deploy/{current,highwater}`、`ls /var/lib/jukebox101-deploy/results` |
| 換 SSH key | 步驟 1、2、4 重做一次 |

## 通知

在 Issue #7 按 **Subscribe**，並到 [個人通知設定](https://github.com/settings/notifications) 開啟 **On GitHub** 與 **Email**。`Notify` 只讀取固定 repo／workflow／run，張貼狀態、commit 與連結，不含日誌或秘密；通知失敗不影響部署。

## 驗證與限制

- 自動測試：主機腳本以假 Docker 驗證成功、回滾、回滾失敗鎖定、首次安裝、標籤不符、過期 run、冪等重送、格式錯誤、雙 Gateway、舊容器接手、設定檔權限（`tests/test_deploy_host_script.py`）；workflow 的 Bash 以假 SSH 驗證輪詢與結果判讀（`tests/test_deploy_workflow_polling.py`）；workflow 邊界與通知有文字契約測試。
- **已實測（2026-11-02，GCP VM）**：真實 SSH／sudo 路徑、GHCR 拉取、Docker／Compose 切換與 `bot.healthcheck` 對真實 Gateway，兩次 Release（`aa0b48d`、`0ec72f4`）部署成功並回報 healthy。這是人工觀察到的結果，不是自動測試。
- **尚未實測**：回滾路徑（新版不健康時換回舊版）在真實主機上尚未驗證，只有假 Docker 的自動測試涵蓋。
- 取捨：主機不再自行重驗 GitHub 簽章。防護來自：受限 key 只能觸發此腳本、映像限定在本 repo 的 GHCR 命名空間、標籤須符合請求的 commit、run 單調遞增、`production` Environment 限制 main。若 GitHub 帳號或 main 本身被入侵，攻擊者能改 workflow，這個風險與舊架構相同。
