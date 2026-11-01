# Hostinger VPS 停用與權限撤銷（2026-11-02）

## 交接狀態

原借用的 Hostinger VPS 自 2026-11-02（Asia/Taipei）起停止提供 JukeBox101 服務。
專案仍由原維護者開發；本次停止的是這台 VPS 的執行與部署存取。
目前不能把過去的 Discord／Supabase 驗收紀錄視為服務仍在線。

操作與主機複核均透過 VPS 擁有人開啟的 Hostinger 網頁 Terminal 執行，未從本機建立 SSH 連線。
這份文件是已執行操作的交接紀錄；合併 PR 不會執行主機清理或恢復服務。

## 已執行的主機變更

| 項目 | 撤銷後狀態 |
| --- | --- |
| `jukebox101-bot-1` | 已停止，Docker restart policy 為 `no` |
| `jukebox101-db-postgres-1` | 已停止，Docker restart policy 為 `no` |
| `jukebox101-cd.path`、`.timer`、`.service` | 全部 inactive／masked，原 unit 檔已隔離 |
| `jukebox-admin`、`jukebox-deploy` | 帳號到期日設為過去，shell 改為 `/usr/sbin/nologin` |
| 專用 SSH 公鑰授權檔 | 已從有效的 `/etc/ssh/authorized_keys/` 撤下 |
| `jukebox101-operator`、`jukebox101-cd` sudoers 規則 | 已從 `/etc/sudoers.d/` 撤下 |
| 管理與 CD 程式、設定、狀態 | 已移離原執行路徑，置於 root 專用隔離備存 |

隔離範圍包含 `/opt/jukebox101`、`/etc/jukebox101`、舊管理／CD helper、安裝的 CD Python 程式、CD 狀態與兩個專用帳號的原 home 目錄。
VPS 上的備存根目錄為 `/root/jukebox101-disabled-20261102`，由 root 擁有，權限 `0700`。
備存不是正在生效的 SSH、sudo 或部署入口。

### 原先的 sudo 範圍

原設定確實允許受限的免密碼 root 執行，不能描述成「從未給予提權」：

- `jukebox-admin`：只可透過固定管理腳本，對指定 Bot 容器執行 `status`、`logs`、`start`、`stop`、`restart`。
- `jukebox-deploy`：只可無參數呼叫固定 CD 接收程式。

這兩份授權現已撤銷。殘留的受限 SSH `Match User` 設定未放寬；帳號已到期、禁止登入，公鑰授權及被強制執行的 helper 也已撤下。
保留 UID／帳號紀錄不代表仍有登入或管理權。

## 主機複核

2026-11-02 00:17（Asia/Taipei）完成最終複核，26／26 項檢查通過。
以下是檢查範圍的彙整；未發現仍生效的專案存取：

| 檢查 | 實際結果 |
| --- | --- |
| 帳號狀態 | 兩帳號皆已到期且 shell 為 `nologin` |
| 管理群組 | primary／supplementary groups 皆無 `sudo`、`admin`、`wheel`、`docker`、`lxd`、`incus-admin` |
| 有效 sudo 規則 | 分別查詢，均明確回覆不允許執行 sudo |
| 實際 sudo 負向測試 | 以兩帳號執行 `sudo -n -u root /usr/bin/true`，皆遭拒絕、exit 1 |
| 殘留程序／個人排程 | 兩帳號均無程序、無 crontab |
| 金鑰是否被其他帳號沿用 | 將已撤下的公鑰與各帳號 home 的 `authorized_keys*`、`/etc/ssh/authorized_keys/` 比對，無重用匹配 |
| 新版簡化 CD 入口 | `/usr/local/libexec/jukebox101-deploy`、對應 sudoers 與 `/var/lib/jukebox101-deploy` 均不存在 |
| 啟動與代理殘留 | 檢查 systemd、系統 cron、root crontab、常見反向代理設定，無有效的 JukeBox101 引用 |
| 容器與自動重啟 | 兩個專用容器均停止，restart policy 均為 `no` |
| 舊 CD 啟動入口 | 三個 systemd unit 均 inactive／masked |
| 原部署路徑 | 程式、設定與狀態已移離原路徑 |
| 共用主機服務 | SSH 仍 active，`sshd -t`、`visudo -c` 通過；防火牆與共用 SSH 服務保留 |

此主機使用 sudo-rs；`sudo -l -U <帳號>` 在輸出「不允許」時仍可能回傳 0。
驗證因此採用明確的拒絕訊息與實際負向測試，沒有只用該列舉指令的 exit code 判斷。
本次未進行本機到 VPS 的 SSH 登入測試，也未將這次有限範圍複核擴張為整台 VPS 的完整資安稽核。

## GitHub 部署交接與舊憑證撤銷

目前的 CD 位於 `.github/workflows/release.yml`，不是獨立 `deploy.yml`。
核對實際設定後，確認 repository variable `CD_ENABLED` 為 `false`，Production 的 `DEPLOY_HOST` 已是另一個部署目標。
因此保留該目標、Production 的 `DEPLOY_SSH_KEY`／`DEPLOY_KNOWN_HOSTS` 及既有 CI／Release 流程，沒有為了退役原 VPS 而全域停用同學的新部署流程。

僅撤銷舊 Hostinger 專用的 repository secret `HOSTINGER_SSH_KEY`，透過 GitHub API 刪除後重新列舉，確認 repository secret 清單已無此項目。
這次只查核 secret 名稱與更新時間，未讀取或輸出任何私鑰內容，也未更換 Production 憑證。
原 VPS 端已撤下的公鑰不再受到信任，兩個原專用帳號亦無法登入或 sudo。

新部署目標的啟用與驗收由專案維護者管理；本次未驗證另一台主機的服務狀態。
這份紀錄與既有安裝手冊不構成重新啟用原 VPS、帳號或 sudo 的授權。

## 保留資料與未變更範圍

- 已停止的容器、映像與 `jukebox101_pg18_data` 資料卷保留，沒有執行永久刪除、資料庫 migration 或測試寫入。
- root 專用隔離備存保留部署檔、原授權檔及部署使用的秘密檔；沒有將內容提交到 repository。
- 未輪替／撤銷外部 Discord Token、Supabase 帳號或其他共用服務憑證。撤銷的是原 VPS 專用授權，不代表這些外部憑證已失效。
- 舊 Hostinger repository secret 已刪除並讀回確認；另一目標的 Production secrets／variable 及 repository `CD_ENABLED=false` 保留。僅查核設定及秘密的 metadata，沒有取出秘密內容。
- 稽核／系統日誌保留。整台 VPS、共用 SSH、共用防火牆及其他專案未停用。

復原資料仍由 VPS 擁有人保管；若日後決定永久銷毀資料或重新提供主機，應另行列出具體資料與權限範圍。
