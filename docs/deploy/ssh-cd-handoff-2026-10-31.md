# SSH 與 CD 接續紀錄（2026-10-31，Asia/Taipei）

## 本次實測

- 儲存庫：`https://github.com/andrewchu0101/JukeBox101.git`；起點 main / dd83004。
- 未找到原始 ZIP；使用已解壓的 `jukebox101-ssh-20261031`，已讀 README。
- 公鑰指紋與交接的 `SHA256:hMN/2Pmiek3pw3T1Zui75uVAU6LqEAcDA+FUvjsqFXU` 一致。
- 首次網路受執行環境限制；取得網路存取後發現 Windows 私鑰 ACL 太寬。依 README 移除繼承並只授予目前使用者讀取，重試成功。
- BatchMode / IdentitiesOnly / StrictHostKeyChecking 均為 yes，使用交付的 known_hosts，結果：`Name=/jukebox101-bot-1 Status=running Health=healthy`。
- 只執行 status；未停止、重啟、部署、遷移或變更遠端設定。維運登入成功不代表 CD 已完成。
- 專案與上層檢查未找到 AGENTS.md。原目錄三項未追蹤資料（PDF、TODO、SSH 資料夾）保留；使用獨立 worktree。

## 本次可審查修改

既有 `.github/workflows/ci.yml` 只在 main push / 對 main 的 PR 執行，沒有正式部署觸發器。保留觸發條件及 CI 不接觸 production secrets 的測試約束。

所有既有驗證通過後，僅 main push 保存同一個 RUNTIME_IMAGE 的 Docker archive（gzip），附 commit、image ID、run ID、attempt 及 SHA256SUMS，保存七天。PR 不產生此交付物，失敗的驗證不會產生候選映像；原有失敗測試證據仍上傳。未重新建置第二個映像。

此產物供下載審查，並非已發布 GHCR 的 digest，也不是密碼學 provenance 證明，不可直接送進現有 receipt verifier 當作部署憑證。未新增 secrets、GHCR push 或 SSH 呼叫；未提交金鑰。忽略規則補上交付資料夾及 ZIP，套用本分支後才生效。

## VPS 擁有者須補上的條件

1. 確認實際部署版本、Compose project/service、固定路徑、架構與資料庫版本；不能從本機 v7 PostgreSQL 文件推定現行正式環境也是 v7。確認既有版本支援 Gateway health，且留存可用的 last-known-good 映像。
2. 提供專用 CI 部署公鑰的 forced-command 入口及精確協定，不提供 shell、SFTP、Docker group 或任意 sudo。現行 `/usr/local/bin/jukebox101-menu` 只有維運功能，不能呼叫 deploy。建議 CI 使用另一組專用金鑰，維運金鑰留作人工維運。
3. 若採用現有 host_ingress/host_worker：由管理者安裝 root-owned 程式與固定 queue、journal、policy、Compose/env、worker 排程，限定 JukeBox101；SSH stdin 接受有長度與逾時限制的 `deploy-v1 <commit> <registry digest> <run> <attempt>` / `status-v1 <run> <attempt>`。不要直接把入口指向永遠拒絕的 `deploy.ssh_request`。需先驗收，不能僅把 enabled 改 true。
4. 完成 GHCR 固定 repository/digest 拉取授權、GitHub receipt 驗證、固定 main/workflow 身分驗證、並行鎖、重複請求、斷線後可查詢狀態、健康失敗回滾，以及單一 Gateway 切換驗收。確認 queue accepted 不等於部署成功。
5. 確認 migration 策略：預設不自動執行正式 migration；需遷移時另定備份、核准、相容性及恢復流程。不得把 DBA 憑證交給維運 SSH。
6. 交付可供 runner 使用且獨立核對的 known_hosts、CI 帳號/port 與入口契約，確認 runner 網路可達。不要以 ssh-keyscan 的未核對結果或關閉 StrictHostKeyChecking 替代。

## GitHub 端仍需約定及驗收

- 與擁有者約定正式發布事件、分支/tag、production environment 與審核人；目前沒有既有正式 CD 觸發條件可沿用，因此本次不自行新增。
- 接續 release workflow 必須測試成功才 publish，同一映像推 GHCR 後取得 registry digest、產生可驗證 provenance，再以專用 key 呼叫固定入口並查詢終態。現有 `deploy/receipt.py` 固定 `.github/workflows/release.yml` 及 main，需保持契約一致。
- SSH 私鑰應在受保護 environment secret 中，known_hosts 應使用已核對版本；Bot token / DB 密碼留在 VPS。PR 不可讀正式 secrets。設定這些值前不宣稱 CD 可用。
- 主機及 GitHub 設定完成後先在隔離環境驗收偽造 receipt、錯誤 digest、舊 run、健康失敗與回滾，再另外約定首次正式部署。本次不啟動正式部署。

## 本機驗證限制

Docker Desktop Linux daemon 未啟動，故本次無法重跑映像建置、PostgreSQL 容器及完整 CI。不得將靜態檢查或 Python 測試描述為完整 Actions 已通過；Actions 成功及新產物下載/校驗仍待推送審查後驗證。

實際檢查：`tests/test_ci_reports.py` 7 passed；既有 CI publication/secret boundary 函式以獨立載入方式執行通過；`git diff --check` 通過。完整 `test_ci_safety_contract.py` 收集階段因現有 venv 缺少 psycopg 而中止，未宣稱通過。
