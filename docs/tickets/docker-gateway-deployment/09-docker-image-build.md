# 09：Docker 映像建置（精簡、非 root、可重現）

**What to build:** 一個可在 Linux amd64 建置的映像，使用 Python 3.12 基底、依既有版本鎖定安裝相依套件、以非 root 使用者執行，且不含測試工具、編譯工具或開發設定。

**Blocked by:** 03（入口需先具備秘密拒絕行為，映像才能正確驗證）

**Status:** ready-for-agent

**File owner:** `Dockerfile`（新）、`.dockerignore`（新）、`requirements-docker.txt`（新，由既有 `dependency-versions.txt` 產生，不手改版本）

## 設計要點

- 基底使用支援原生依賴的精簡版本，不使用 Alpine。
- 建置階段與執行階段分離：執行映像只保留套件與程式碼。
- 執行使用固定的非 root 使用者與群組；群組 ID 需與 02 的場景 B 規則一致，供秘密檔案群組權限使用。
- 不使用 build secrets，不使用 ARG 或 ENV 傳入秘密。
- `.dockerignore` 採允許清單思路：預設排除全部，再列出必要檔案，不只依賴 `.gitignore`。
- 映像標籤不包含真實版本以外的敏感資訊。

## TDD 驗證

- 紅：`.dockerignore` 排除 `.env`、`.local/`、`.git/`、虛擬環境、快取與測試證據（以清單比對驗證，不需要建置）。
- 紅：Dockerfile 不含 `ARG` 或 `ENV` 指向秘密變數（靜態檢查）。
- 紅：依賴安裝來源與 `dependency-versions.txt` 一致（比對版本）。
- 建置後：映像的執行使用者不是 root；入口指令存在；不含測試目錄。

**Acceptance criteria:**
- [ ] `docker build` 在本機 WSL 的 Docker 環境成功，並記錄建置時間與映像大小（不含推送）。
- [ ] 映像執行使用者為非 root。
- [ ] 映像中不含測試目錄、`.git`、`.env` 或 `.local`。
- [ ] 靜態檢查通過：無秘密相關 ARG 或 ENV。
- [ ] 所有 Docker 指令透過 `wsl.exe -d Ubuntu --` 的新工作階段執行，不放寬 Docker socket 權限。
- [ ] 不執行 `prune`，不清理既有映像、容器或 volume。

**Out of scope:** 容器啟動與健康檢查（見 10）；推送到任何映像倉庫。
