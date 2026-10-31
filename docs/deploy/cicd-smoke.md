# CI/CD 快速隔離演練

這是 **isolated deployment E2E simulation / CI/CD smoke test**。Smoke test 快速檢查關鍵規則與失敗路徑；此處的隔離 E2E 串起真實 receipt 解碼、持久佇列、控制器、假 Gateway 程序及通知分類，另以真實 Docker Compose 驗證容器切換與回滾。GitHub API、commit、digest、Discord／DB health 全是合成資料，不能視為真正 production E2E。

## 執行

需要本地 Linux Docker daemon、Compose 外掛（`docker compose`）、主機 Python 3（標準庫即可）。支援 Linux 本地 Unix socket 與 macOS Docker Desktop；拒絕 SSH／TCP 遠端 Docker context。測試本體固定在既有 `test.Dockerfile` 的 Python 3.12、非 root 容器中執行。

從 repository 根目錄先建立測試映像（首次建置需要下載公開 base image、系統與測試套件；不登入 registry、不 push）：

```sh
docker build -f test.Dockerfile -t jukebox101-test:cd-smoke-local .
```

之後一個指令完成短測試及 Compose 演練；每次更新程式後先重建測試映像：

```sh
TEST_IMAGE=jukebox101-test:cd-smoke-local python3 -m scripts.ci.cd_smoke
```

映像必須已存在；執行器固定其本地 image ID，禁止隱式 pull／build。兩個假 digest 只是 journal 的 release identity，並非 GHCR digest；舊／新 release 都使用同一個本地測試映像，靠合成 health 區分。

既有 PR CI 已在離線回歸收集新增測試，並呼叫同一個 `verify-cd-docker.py`；無須增加另一套 workflow 或重複跑完整 smoke 入口。PR 的原有 CI 另有 runtime 與拋棄式 PostgreSQL 測試；這個短入口不執行它們，也不執行任何 migration。Release／Deploy／Notify workflow、正式 gate 與外部設定均不因本變更而修改。

## 驗證範圍

| 檢查 | 實際證據與限制 |
| --- | --- |
| PR CI、Release 邊界 | 讀取已提交 workflow 的 trigger／權限／checkout 契約；Release 僅 main push、publish 依賴 verify。不是 GitHub scheduler 的完整模擬 |
| Deploy gate | 使用 workflow 裡真正的 `resolve.if`，以假 context 驗證 CD=false／未設定、fork、失敗／取消的 Release、PR 來源都跳過。只支援目前使用的字串相等與 AND 語法；新語法會使測試失敗，需人工更新 reader |
| skipped 通知 | 用合成 skipped jobs 經真正 publisher，確認顯示「未執行部署」，不宣稱上線；沒有讀寫 GitHub issue |
| Release request | 真實 ZIP／JSON 解碼，加上假的 API endpoint；既有案例拒絕過期 source、錯誤 attempt、模糊／過期 artifact、路徑與輸出注入 |
| 通知分類 | CI／Release 各種 conclusion、Deploy 成功／skipped／失敗／取消／逾時、缺少／重複／不完整 jobs；既有測試確認通知失敗與去重。不能證明 Email 送達 |
| 成功切換 | 真實持久 journal、假程序與真實 Compose service，確認 stop-before-start、唯一容器及合成 healthy |
| 失敗回滾 | candidate unhealthy → rolled_back；舊版也 unhealthy → rollback_failed，後續新請求被鎖住；回滾不等於部署成功 |
| 重複與重試 | 同一 request 回傳既有結果，容器 ID／lifecycle events 不變；同 key 換內容、過期 run、同 run 加 attempt 都拒絕。回滾後改用較新 run 可重試成功；這保留現有 highwater 規則 |
| 單一 Gateway | 真程序 lease、控制器鎖、Compose 啟動前確認停止；主動嘗試重疊啟動並確認被拒絕。只證明隔離 lab 內的單例，不盤點正式 Discord Application |
| 清理與失敗 | 每次隨機 container／project／temporary directory；finally 只移除該資源，Compose down 後確認該 project 無剩餘容器。失敗／逾時／缺報告／skip／清理失敗皆非成功 |

字串比較遵循 [GitHub 官方 expression 規則](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions#operators)，不分大小寫：合成的 `true`、`TRUE`、`True` 都通過 gate；`false` 與未設定均跳過。本 reader 不支援數值／布林型別的寬鬆轉型，遇到它們會明確失敗。

## 安全與報告

離線測試容器 `--network none`，沒有 host mount、Docker socket、憑證或主機環境注入。Compose service 同樣無網路、read-only、非 root、drop ALL capabilities，僅收到 `LAB_HEALTH`。Docker client 只允許本地 socket，使用暫存且不含 registry 登入設定的 client configuration；需要時只連結已安裝的 Compose 外掛，不複製原本的 `config.json`，不繼承任意 Compose／Bot／DB 設定。

沒有 Hostinger／SSH／systemd／真實 Discord／Supabase／GHCR publication／production secrets／migration。沒有啟用 CD、修改主機權限或外部設定。既有 GitHub `Notify` 對真實 PR CI 的自動通知仍由原 workflow 處理，與測試裡的假通知不同。

每次執行先移除這三份舊報告，避免失敗時沿用上次成功：

- `ci-evidence/cd-smoke.xml`：實際 pytest JUnit；全數須 pass、零 skip／failure／error。
- `ci-evidence/cd-docker.json`：三個 Compose 情境、replay／retry／overlap 與 cleanup 證據；任一情境失敗不產生成功報告。
- `ci-evidence/cd-smoke.json`：整體結果與耗時；即使未完成也記為 failed，程序非零退出。

報告不入版控。CI 既有 artifact 步驟保存離線 JUnit 與 Compose JSON。這些結果不能證明實際 GitHub 權限／環境保護、GHCR 簽章、SSH 或 production adapter 可運作，也不代表正式部署已驗收。

同一 checkout 共用報告路徑，請依序執行；不同 checkout 可獨立演練。Compose 情境共用 90 秒 active-work 期限，逾時以取消方式進入清理，不把取消當作 candidate failure 重試。清理暫停這個期限，另以 30 秒 down＋10 秒殘留查核的上限執行，再恢復未用完的期限。SIGKILL、主機斷電或 daemon 無回應仍可能阻斷清理；此時不會宣稱通過，應依日誌中的隨機 project 名稱核對殘留，不執行廣泛 prune。

## 本次 PR 的執行邊界

本次要求禁止 DB migration；既有完整 PR CI 會套用拋棄式 PostgreSQL schema，因此本次交付使用 draft PR，HEAD commit 加上 `[skip ci]`，只執行本地無網路測試與無 DB 的 Compose 演練。這是 [GitHub 支援的單次跳過指令](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/skip-workflow-runs)，不修改 workflow、repository variable 或 Environment。Hosted CI 是 Not Run／可能 Pending，不是 Passed；必要 checks 未通過時不能合併。

未來若另有授權允許完整 CI 的拋棄式 DB 測試，可用不含 skip 指令的新 commit 走原有 PR CI。不要直接把本 PR merge main：現行 Release 會在 main push 發布 GHCR，這次未授權該動作。

## 本地實測紀錄（2026-10-31，Asia/Taipei）

環境：macOS Docker Desktop、本地 Unix socket、Python 3.12 非 root 測試容器。建置使用公開 Python base image 與既有釘選測試依賴；沒有 DB service。

- `TEST_IMAGE=jukebox101-test:cd-smoke-local python3 -m scripts.ci.cd_smoke`：**153 Passed、零 failure／error／skip，整體 69.04 秒**，不含映像建置。
- `TEST_IMAGE=jukebox101-test:cd-smoke-local python3 -m scripts.ci.offline`：最終 **2,142 Passed、108 個 postgres 案例 deselected、零 failure／error／skip，59.65 秒**；沒有執行 DB migration。
- 真實 Compose：`succeeded`、`rolled_back`、`rollback_failed` 全部符合預期；三個 project 的 `cleanup=verified`，額外掃描 lab 標籤也沒有殘留。
- 回滾後的新 run 重試成功；回滾失敗後的新 run 被拒絕；重複／衝突／過期／同 run 新 attempt 不改變容器 ID 與 lifecycle events；重疊啟動被拒絕。
- `git diff --check`、新增／修改函式長度與 docstring 檢查、codebase safety config strict validation 通過。四份 workflow、`deploy/`、Bot、schema／migration、依賴檔均無差異。

本輪發現並保留的失敗：隔離 Docker 設定曾讓 macOS Compose 外掛無法被發現，已改為只連結外掛；90 秒期限曾打斷 cleanup，留下本輪一個已停止的容器，已按精確名稱移除，並修正為 cleanup 暫停 active-work 期限。兩者皆有新增回歸測試；失敗沒有改寫成成功，期限失敗 JSON 保存在本地 `ci-evidence/cd-smoke-deadline-failure.json`。

較早並行執行完整回歸時，既有 `test_lifecycle_sigterm.py` 一項關機計時為 7.28 秒，超過 7 秒上限。該失敗 JUnit 保存在 `ci-evidence/offline-concurrent-failure.xml`；沒有修改 Bot、該測試或其時間門檻，最終單獨執行的 2,142 項包含此案例且全數通過。這仍不能推定正式關機行為已驗收。
