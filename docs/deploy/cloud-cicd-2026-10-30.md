# JukeBox101 雲端 CI/CD 交接（2026-10-30，Asia/Taipei）

本輪是 **工作分支實作與隔離驗證**。正式 VPS、Bot、PostgreSQL、Office、Caddy、DNS、防火牆、SSH、Secrets 與 repo 設定均未修改；沒有發布 GHCR、正式 migration、command sync、正式資料寫入／還原或合併 main。

起點與重新確認的 main：`c5eb7f9ac43a431b5a0a896511314557c9951b2d`。分支：`work/cloud-cicd-health-rollback`。沒有 AGENTS.md；已讀 CLAUDE.md、CODING_STANDARD.md、README、v7 PostgreSQL 手冊。Mac 的本機路徑不參與本輪工作。

## 實作與草案修正

| 檔案／元件 | 本輪行為 |
|---|---|
| `.github/workflows/ci.yml` | PR/main 在 GitHub hosted Ubuntu runner 跑 CI；job 級 contents:read、checkout 不保留 credentials；Actions 固定完整 SHA。沒有 PR secrets、publish、SSH 或 production Environment。沒有 pull_request_target |
| `scripts/ci/check_junit.py`、`offline.py` | JUnit 結構化結果、零 skips/failures/errors、拒絕重複 testcase。沿用最低 1747 離線／35 PG 測試門檻，不比對硬編碼「35 passed」；測試增加可自然通過 |
| `verify-fresh-postgres.py` | 隨機命名 network／volume／containers、internal network、無 host port；檢查 PG 18.6 與 disposable marker；TLS 整合、保留 JUnit、清除僅本次資源 |
| `scripts/ci/runtime_smoke.py`、`verify-runtime-postgres.py` | build 一次後使用 immutable local image ID；imports、linux/amd64、uid 10001、DB verify-full／角色／control、唯讀假秘密 volume metadata、關閉功能、本機 command contract、DB ready 但 Gateway 不在線時拒絕健康 |
| `bot/gateway_health.py`、`gateway_client.py`、`healthcheck.py` | 正式主程序自己的 READY／disconnect／resume、event-loop 新鮮度及 Discord OP 11 ACK；DB-only readiness 保持獨立。production 專屬 client 不改變離線 acceptance client 的 inert 契約 |
| `bot/runtime_policy.py` | 用真實本機 registry 與 connection trap 驗證 claim 不註冊、正式／synthetic exchange／daily／claim／補償關閉；不登入、不寫 DB、不做 command sync |
| `deploy/controller.py`、`journal.py` | fsync／atomic replace 的 last-known-good 與 journal、排他鎖、run+attempt 冪等、晚到拒絕、先確認停止再啟動、一次回滾、失敗鎖定、controller 中斷後恢復 |
| `deploy/lab_*` | 真實本地 fake Gateway 子程序演練；Discord／registry／DB／SSH 都是模擬邊界，不能視為正式整合驗收 |
| `deploy/controlled/compose.yaml` | 尚未安裝的 bot-only 審查包；非 root、read-only、512MiB／1 CPU、30 秒 grace、無公開埠；固定兩個功能 off，無 migrate／DB service／migrator secret |
| `deploy/ssh_request.py` | 嚴格有界協定解析，**即使請求格式正確也拒絕**；receipt verifier／production adapter 未完成，沒有旗標可越過此拒絕 |
| `deploy/release.yml.example` | 工作流程目錄以外的 proposal；publish 與 deploy 均 literal false，且只會 exit 1。未授權發布不會被拿來當測試 |

原草案中的 `on_socket_response` 不能直接用於 pinned discord.py 2.7.1。已閱讀本機實際套件的 `gateway.py`／`client.py`，改用其 `on_socket_raw_receive` 事件辨識 ACK，並以框架 websocket parser＋fake transport 驗證。只讀 opcode，不保留或記錄 payload。

健康訊號每 2 秒由同一事件迴圈更新；sample 最大 10 秒、Discord ACK 最大 90 秒，斷線／shutdown 立即失效。檔案綁定 Linux boot ID、PID、kernel start ticks、application ID 與每次新 instance ID。Compose 的 ENTRYPOINT Python 是 PID 1，healthcheck 固定核對 PID 1，不能另開一個登入程序冒充。沿用 tmpfs，沒有公開健康埠。這些是目前測試門檻，尚未量測真實 Discord 延遲或長期穩定性。

單例目前是 **同一 /tmp 掛載與 Application 的本地 lease** 加控制器 stop-before-start。這不提供跨主機或不同容器 /tmp 的分散式 fencing，也不能阻止外部工具／舊版本自行登入。首次正式啟用仍須盤點所有相同 Application 的 Gateway；DB advisory lock／斷線 fencing 未實作，不能宣稱跨主機單例已驗收。

## 雲端環境與驗證層級

已讀 `cloud-environment-runtime`、networking 與 Docker proxy/CA 指引。此 session 沒有 environment_status 工具、`/etc/codex/network-policy.json`、Docker CLI/socket，且無 Linux capabilities，不能啟動 Docker daemon。未嘗試 SSH 或借用正式環境測試。沒有改 CA 或停用 TLS；Docker pip step 支援選用 BuildKit CA mount，session CA 不會被 COPY 入 layer。

原 Git clone 在等待後成功，實際工作使用完整 Git checkout；額外 connector 下載快照不作提交或遠端基底。Python 3.12.14 venv 依 repo requirements-test.txt 安裝。系統缺 libpq，apt 受 capability 限制，故此 session 額外安裝 **僅本地測試用 psycopg-binary==3.3.6**；沒有修改 repo 的依賴或 runtime 使用方式。這與正式 pure Python＋Debian libpq5 runtime 不同，exact-image CI 才能驗證後者。

| 檢查 | 狀態與證據 |
|---|---|
| 本地 focused Gateway／controller／report／程式規範 | Passed：66 tests；`focused-complete.xml`。實際 fork／lease／atomic journal／SIGTERM controller recovery，Discord 是 fake |
| 既有 Gateway compatibility／SIGTERM shutdown | Passed：127 tests；`health-compat.xml`。真實 bot main 子程序、fake Discord，不是正式登入 |
| 本地完整離線回歸 | Failed（環境限制）：1800 passed、2 failed、0 skipped、35 postgres deselected；`offline-final-serial.xml`。兩個既有 secret 測試拒絕 UID 0；保留斷言，未提高／降低政策以假裝通過 |
| Docker／PG 18.6 TLS／exact runtime／Compose render | Not Run（本 session）：缺 Docker/capabilities。已加入 hosted runner CI；其結果須另外核對，不把 Python mock 稱為容器驗收 |
| YAML parse、權限與 active workflow 靜態檢查 | Passed：已解析 CI、disabled release example、受控 Compose；Action commit SHA 已向來源 repo 確認存在。非完整 actionlint 或 GitHub 執行驗證 |
| GHCR publish／正式 CD／正式 Discord login | Not Run：本輪明確禁止 |
| 公開健康埠、正式資料 restore／downgrade | 不存在於本輪實作或演練 |

若後續新增測試或修改程式，以下面的更新驗證紀錄為準；不要加總重疊測試數成為虛假的總覆蓋。

## 演練涵蓋範圍

| 情境 | 層級 | 預期／已檢查行為 |
|---|---|---|
| 新版本 healthy | 本地 process integration，健康模擬 | 舊程序停止且 lease 釋放，才啟動候選；連續觀察後才更新 LKG |
| 候選 DB ready 但 Gateway 失敗／READY 後斷線 | unit＋本地 process integration，健康模擬 | combined health fail；先停候選，再一次回滾 |
| 候選失敗、舊版回復成功 | 本地 process integration | `rolled_back`，這次部署仍是失敗；LKG 保留 |
| 回滾也失敗／stop 無法確認 | process integration＋unit | `rollback_failed`，不啟動第二份、不反覆切換，後續 request 拒絕 |
| 主程序死亡、陳舊／錯程序／重啟健康檔 | process integration＋unit | retained file 不代表健康；新的 instance 先 starting，需重新 READY＋ACK |
| host lock／重複請求／同鍵換 digest／晚到舊 run | unit＋真實 OS lock | busy／回原結果／拒絕；較舊請求不覆蓋較新 LKG |
| client 先退出、worker 繼續 | 本地 process integration | detached worker 完成並落盤；不依賴 Mac 或 submit client 存活 |
| controller 真實 SIGTERM／恢復 | 本地 process integration | 停舊後中斷，journal 保留 starting intent；下一 controller 回滾至 LKG |
| 每個 durable phase 中斷、回滾中再中斷 | journal simulation＋真實 reconciliation processes | prepared 不停服務；一般中斷回滾；回滾中斷鎖定人工處理 |
| 真實 SSH／systemd／Docker controller 安裝／主機重開機 | Manual Pending | 本輪未建帳號、unit、sudo、key；上述 process 演練不能取代此驗收 |

正式 adapter、host-owned bounded queue/systemd worker、服務／資源 preflight、Office 前後比較、映像 provenance／schema compatibility 驗證、部署總時限與 production 180 秒＋120 秒觀察策略仍是 Manual Pending。lab 的 0.4 秒觀察僅用來驗證狀態機，不代表正式健康門檻可縮短。journal 上限 128 筆，到達時 fail closed；正式保留／歸檔政策未實作。

## 可重現命令（只在測試 checkout／disposable runner）

```bash
docker build --pull -f test.Dockerfile -t jukebox101-test:v7-local .
python3 -m scripts.ci.offline
docker build --pull --platform linux/amd64 -t jukebox101-runtime:ci .
export RUNTIME_IMAGE="$(docker image inspect --format '{{.Id}}' jukebox101-runtime:ci)"
python3 -m scripts.ci.runtime_smoke
python3 scripts/dev/verify-fresh-postgres.py
```

managed Docker 環境若需要 session proxy CA，使用其既有 CA bundle 加 `--secret id=proxy_ca,src=<已配置 CA bundle>`；不替換 registry credential、不停用驗證、不繞過 network policy。此輪未取得可用 daemon，以上 Docker 命令沒有在這個 session 被宣稱為 Passed。

本地程序演練：Python 3.12 venv 執行 `python -m pytest -q tests/test_deploy_controller.py tests/test_gateway_health.py tests/test_ci_reports.py tests/test_ci_safety_contract.py tests/test_phase2_quality.py`。只建立 pytest 的隨機 scope，fixture 停止其自身 fake child。未使用正式 Token、SSH、DB 或 Bot。

## 正式啟用前仍須完成

1. 先審查本分支與實際 hosted CI 證據；review/pin Python／PG base digests、驗證 exact runtime。不以 `vars.CD_ENABLED` 或 OCI labels 取代獨立 trust verifier；本輪 publish/CD 保持關閉。
2. 實作並隔離驗證 receipt/attestation、固定 production adapter／journal recovery／受限持久 worker；由管理員核對 root-owned 路徑／secret metadata、existing network／CA、schema 相容、外部 Gateway 與真正 stop fencing。不得 DB restore/downgrade。
3. 另取得正式安裝／啟用授權後，管理員設定 branch protection、Environment reviewer／main 限制、GHCR 權限、可信 host key／deploy account，再做人工核准的首次部署。這些不能由本輪一般修改授權推定。

兩個功能必須維持 `JUKEBOX_NICKEL_CLAIM=off`、`JUKEBOX_FORMAL_EXCHANGE=off`，`/claim` 不註冊，settlement/control 保持關閉。補償沒有新增或虛構完整 env switch；其既有 repository 關閉路徑仍維持預設 False，沒有加入 scheduler、worker 或自動執行入口。MySQL 舊資料遷移不在範圍內。

## 本輪交付狀態

- 最終 focused 命令：`python -m pytest -q tests/test_gateway_health.py tests/test_lifecycle_sigterm.py tests/test_deploy_controller.py tests/test_ci_reports.py tests/test_ci_safety_contract.py tests/test_phase2_quality.py --junitxml=../focused-complete.xml`：**66 passed／0 failed／0 skipped**。
- 最終完整離線回歸：`python -m pytest -q -p no:cacheprovider -m 'not postgres' --junitxml=../offline-final-serial.xml`：**1800 passed／2 failed／0 skipped／35 deselected**，33.45 秒。這不是整套 Passed；兩項 secret metadata 測試明確回報 RUNS_AS_ROOT，沒有刪測試、豁免或放寬斷言。
- `git diff --check`、Python compile、bot／新增 deploy／CI helper 的 25 行函式與 docstring 檢查 Passed。額外 YAML parse 使用環境既有 PyYAML，沒有加入專案依賴；完整 actionlint／Compose render 是 Not Run。
- 建立遠端 `work/cloud-cicd-health-rollback` 時，GitHub API 回覆 **403 Resource not accessible by integration**。repo read 回報 push=true 不等於此次 App 具有 create-ref 權限。未建立遠端 branch、draft PR，也未觸發任何 hosted CI；未繞過權限或改用他人 credential。改以本地工作分支 commit、Git patch／bundle 與證據包交付，**不合併**。
- 部署／發布：Not Run。production trust verifier、主機安裝、跨主機 singleton、systemd／SSH／Docker controller／重開機演練均 Manual Pending。這些限制必須在正式 CD 之前解除，不應直接啟用此分支。
