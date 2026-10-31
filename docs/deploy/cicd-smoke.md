# CI/CD 安全測試與正式發布分離

PR #9 合併後，PR 與 main push 只執行 **isolated deployment E2E simulation / CI/CD smoke test**。測試用假 commit／digest、假 GitHub API、合成 Discord／DB health，並使用真實且隔離的 Docker Compose 容器。這不是 production E2E，測試綠燈不代表 DB 寫入、正式發布或 VPS 上線。

## 現行流程

| 事件 | 會做什麼 | 不會做什麼 |
| --- | --- | --- |
| PR／main push | 建開發測試映像 → 同一個短入口 → 保存 JUnit／JSON 證據 | 不啟動 DB、不執行 migration、不建置／匯出正式 runtime、不登入／推送 GHCR、不連 VPS |
| CI 完成 | 既有 Notify 向 Issue #7 發送真實 CI 結果 | 不啟動 Release／Deploy，不宣稱已上線；通知送達仍需每人確認 |
| 手動選擇 Release／Deploy | 唯一的 deferred job 固定 skipped，沒有實際發布／部署動作 | 沒有 registry、SSH、production secrets、Environment 或可由 variable 開啟的後門 |

Release／Deploy 沒有 push、workflow_run、workflow_call 或排程觸發。即使設定 CD_ENABLED=true，也不會恢復已移除的正式流程。舊發布程式保留在 Git 歷史；[原部署手冊](continuous-deployment.md) 只供歷史參考，未來正式發布要另開變更重新設計與驗收。這次不修改 VPS、帳號權限、repository variable、Environment 或 secrets。

## 本地執行

需要本地 Linux Docker daemon、Compose 外掛（docker compose）及主機 Python 3 標準庫。支援 Linux Unix socket、macOS Docker Desktop，拒絕 SSH／TCP 遠端 Docker context。測試本體在 Python 3.12、非 root 容器中執行。

從 repository 根目錄建置：

```sh
docker build -f test.Dockerfile -t jukebox101-test:cd-smoke-local .
```

然後執行與 CI 相同的短入口：

```sh
TEST_IMAGE=jukebox101-test:cd-smoke-local python3 -m scripts.ci.cd_smoke
```

更新程式後先重建測試映像。首次建置需要下載公開 base image、系統與釘選測試套件；不登入 registry 或 push。短入口固定本地 image ID，測試時禁止隱式 pull／build。假 digest 是 journal 的 release identity；新舊 release 均使用同一個本地測試映像，以合成 health 區分。

## 驗證範圍

- 檢查真正的 workflow：PR／main 只選 safe CI job，沒有正式流程依賴；Release／Deploy 只保留固定 skipped 的手動佔位，沒有 live capability。CI 執行指令採 allowlist，migration／runtime export／publishing／SSH 回歸會讓測試失敗。
- 用既有 resolver 解碼真正 ZIP／JSON receipt，但 GitHub API、commit、digest 都是假的；舊 receipt／通知分類案例只測隔離或歷史契約，不會呼叫保留的正式 workflow。
- 將 receipt、持久佇列、控制器、假 Gateway 程序及假通知串起來，確認成功、失敗與錯誤分類。
- 真實 Compose 演練 succeeded、rolled_back、rollback_failed，驗證 stop-before-start、重疊啟動被拒絕、回滾失敗鎖住後續請求及狹義清理。
- 同 request 重送回傳既有結果，容器 ID／lifecycle events 不變；衝突內容、過期 run、同 run 加 attempt 均拒絕。回滾後可用較新 run 重試。這保留既有 controller 規則，不是新的正式部署協定。
- 沿用 test_foundation.py、test_repository.py，在 mock 驅動／cursor 邊界核對連線參數、TLS、參數化 SQL、資料值及連線／查詢／commit 失敗；沒有送出真實 DB 請求。

資料庫另列驗證範圍，整體模擬 passed 也不代表真實 DB 成功：

```json
{
  "driver_contract": "passed_mock_boundary",
  "real_connection": "not_run",
  "real_persistence": "not_verified"
}
```

這沒有證明真實網路傳輸、DB 接收、SQL 在實際 schema 可執行或資料落盤。沒有接收端證據的部分保留未驗證，不以呼叫成功或 mock commit 冒充實際寫入。正式 Discord、GHCR push／簽章、SSH／systemd、VPS 新舊版相容性也不在本輪驗證範圍。

## 安全、報告與合併

測試容器 network none，沒有 host mount、Docker socket、真實憑證或主機環境注入。Compose 同樣無網路、read-only、非 root、drop ALL capabilities，只收到 LAB_HEALTH。暫存 Docker client config 不複製登入設定，只在需要時連結已安裝的 Compose 外掛。

每次先移除三份舊報告，避免沿用舊成功：

- ci-evidence/cd-smoke.xml：選定 pytest 案例必須全數 pass，零 failure／error／skip。
- ci-evidence/cd-docker.json：三種情境、replay／retry／overlap 與 cleanup 證據。
- ci-evidence/cd-smoke.json：整體結果、耗時、DB 驗證範圍；失敗或未完成非零退出。

本地報告不入版控，GitHub CI 以 artifact 保存 14 天。同 checkout 請依序執行，避免共用報告互相覆蓋。演練有 90 秒 active-work 期限；cleanup 暫停該期限，另以 30 秒 down＋10 秒殘留查核上限執行。SIGKILL、斷電或 daemon 故障仍可能阻斷 cleanup，此時不得宣稱通過，也不要做廣泛 prune。

本次新提交不再使用略過 CI 標記，讓 PR 真的驗證安全流程。PR 早期提交曾含略過標記；合併時建議 Squash，並使用不含任何略過 CI 指令的提交標題與內容，確保 main CI 會執行。是否略過 CI 不影響已移除的發布／部署能力，不能靠提交訊息重新開啟它們。

必要的 CI 與 reviewer 檢查通過後再由維護者決定合併；本次不代為合併。舊自動發布流程只在 PR 合併後被替換，不能把分支上的改動當成已在 main 生效。

## 歷史證據

分離前：短入口 195 項＋三種 Compose 情境通過，70.90 秒；前一輪完整離線 2,142 項通過。這些不是分離後 hosted CI 的結果。原有工作流 gate 已被移除，因此對舊 gate 的條件測試改成驗證目前更嚴格的觸發與能力限制，不為維持歷史項目數而保留失效測試。

歷史失敗與修正：Docker Desktop 的 Compose 外掛發現已改成只連結外掛；deadline 打斷 cleanup 已改成清理期間暫停期限，並加回歸測試。完整回歸曾在並行負載下出現既有關機計時 7.28 秒超過 7 秒上限，保留失敗報告，未修改該測試或門檻，前輪單獨完整回歸通過。這些均不是正式環境驗收。

## 分離後驗證紀錄（2026-10-31）

四份 YAML 均已通過本地解析：CI 為 pull_request／push，Release／Deploy 僅 workflow_dispatch，Notify 僅追蹤 CI。受影響函式長度、codebase safety strict validation 與 diff 檢查通過。

本地 186 項選定測試通過，零失敗／跳過；整個 Compose 演練在第三種情境碰到既有 active-work 期限，整體正確標為 failed（168.43 秒）。期限未調高，cleanup 後掃描沒有 lab 容器殘留。失敗摘要保存在本地 ci-evidence/cd-smoke-separation-local-timeout.json；不能把這次本地整體結果當成成功。

GitHub hosted CI 將以相同安全入口另行驗證，結果以 PR #9 的 check／artifact 為準。沒有跑完整離線回歸、真實 PostgreSQL／migration、GHCR 發布或 VPS 部署。
