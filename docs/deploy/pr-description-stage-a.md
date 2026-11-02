# PR 說明草稿：階段 A 自動審核含時長與自動版本確認

狀態：**已推送並開 PR，未部署、未啟用**。分支 `feature/auto-review-version-confirm`，基底 `main`。已把 `origin/main`（含 PR #16、#17）合併進分支；唯一衝突是 `deploy/host/jukebox101-deploy`（main 以壓縮合併收進了本分支原本就有的 CD 診斷提交，內容相同），解法是保留本分支版本，等於 main 的版本加上 H-1 檢查。合併 main 會觸發自動部署，由 Owner 按。

## 這個 PR 做什麼

合併後**不會開啟任何功能**：所有自動開關預設 off。它加入「新投稿通過自動核准前，向 YouTube 重查最近 500 首既有歌曲、比對疑慮、全部沒有疑慮才由系統確認錄音版本」的能力，並補齊讓它能安全上線的部署保護。

- 後端（Sean）：`bot/version_concerns.py`（疑慮規則）、`bot/auto_version_review.py`、`bot/youtube_metadata.py`（批次 50 首）、`bot/auto_pipeline.py`（比對步驟與配額預算）、`bot/database/auto_version_store.py`（同交易核准＋系統版本確認＋陳舊檢查）、`bot/evidence_record.py`（`SYSTEM_JUDGEMENT`）、`bot/channel_id_tool.py`（Owner 取頻道 ID）、`bot/config.py`／`bot/auto_startup.py`（開關）。
- 部署與保護（Ken）：見下表。
- 規則：額度用完與 API 故障一律稍後重試（30 分鐘），不寫待審、不拒絕；清單空白時沒有歌自動通過；資料庫只存影片 ID 與原始連結，不存歌名、頻道或官方時長（短期觀測表除外，14 天內清除）。

## 保護機制：改了什麼、為什麼、對應測試

| 保護 | 改了什麼 | 為什麼 | 對應測試 |
|---|---|---|---|
| 健康檢查 `bot/healthcheck.py` | **程式不變**，只補測試 | 它早已呼叫 `load_auto_settings()`，所以「設定完整的自動模式」本來就是 ready；缺的是沒有測試證明這點，也沒有證明設定錯誤、缺金鑰、缺前提仍 not_ready | `tests/test_healthcheck_auto_modes.py`（on 且完整 = ready；10 種錯誤設定 = not_ready；舊的 exchange／claim 開啟仍不健康；輸出不含金鑰與頻道） |
| 執行期政策 `bot/runtime_policy.py` | 任一自動開關（審核、保存、結算、版本確認）出現 on 或無效值就拋 `ConfigurationError`；收據加 `auto_modes` | CI 的承諾是「發布的映像預設不會自己動點數」；以前只靠「沒傳 pipeline」證明，改成明確檢查更不容易被日後繞過 | `tests/test_runtime_policy_auto_modes.py` |
| CI 契約 `scripts/ci/runtime_smoke.py` | controlled 的秘密集合由「剛好 3 個」改成「剛好 4 個、名稱鎖死為 `youtube_api_key`」；自動開關渲染結果必須全是 off、清單必須為空；金鑰檔路徑固定。base `compose.yaml` 仍是 3 個 | 新增秘密是必要的，但不能放寬成「至少 3 個」讓日後任何秘密都能被掛進去 | `tests/test_controlled_compose_policy.py`（新增 11 種故障：額外秘密、缺秘密、被換名、開關被設成 on、清單預設有值、金鑰路徑被改） |
| 部署 compose `deploy/controlled/compose.yaml` | 新增六個設定（預設 `${...:-off}` 或空）與秘密 `youtube_api_key`（固定路徑 `/etc/jukebox101/secrets/youtube_api_key.txt`） | CD 只用單一 compose 檔，疊加檔進不了 CD 路徑 | 上列兩個測試，加真實 `docker compose config` 渲染三種設定 |
| 主機腳本 `deploy/host/jukebox101-deploy`（H-1） | compose 若提到 `youtube_api_key` 而金鑰檔不是一般檔案，在**停舊 Bot 之前**回 `rejected` | 新 compose 一律掛載這個秘密；沒有這道檢查時，缺檔會在停舊 Bot 之後才讓 `docker compose up` 失敗，雖會自動回滾但有停機 | `tests/test_deploy_host_script.py`（缺檔拒絕且舊 Bot 不停、檔案存在可部署且內容不外洩、舊 compose 不受影響、目錄不是檔案則拒絕） |
| CI 數量下限 | 離線 1747 → 2264；PostgreSQL 35 → 141 | 下限不會隨新增測試自動調高，新測試可能被悄悄跳過 | `scripts/ci/offline.py`、`scripts/dev/verify-fresh-postgres.py` |

## 上線時必須注意（不會自動發生）

- **主機腳本不會隨部署更新**：部署只換 Bot 映像。改了 `deploy/host/jukebox101-deploy` 之後，必須在 VM 更新 clone 並重跑 `sh scripts/deploy/install-host.sh --pubkey <受限公鑰檔> --apply`，H-1 才會生效。
- **主機的 `compose.cd.yaml` 也不會自動更新**（`install-host.sh` 不覆寫既有檔案）：要採用新 compose，必須人工替換，並先放好 `youtube_api_key.txt`（0400、`10001:10001`；開關 off 時內容可先放占位字串）。
- migration 0004 套用到正式庫需 Owner 另行明確授權；系統不會自動套用。
- 啟用順序與回滾：[階段 A 啟用與回滾清單](stage-a-enable-rollback.md)。

## 驗證（2026-11-02，Linux 容器，本機；於合併 `origin/main` 之後的最終樹重跑）

最終樹上的實測：離線 2264 passed；PostgreSQL 18.6 為 139 passed、2 skipped。第一次 PostgreSQL 重跑曾因另一個同時使用同名測試資源的執行而連線認證失敗（138 errors），確認沒有其他測試容器後重跑通過。

| 狀態 | 項目 |
|---|---|
| Passed | 離線全套 2264（`--network none`）；PostgreSQL 18.6 全套 139（另 2 個 skip）；階段 A 的 PG 整合測試 13、文案測試 10、回滾測試 5；既有 Owner 裁決特徵測試 5；`docker compose config` 渲染三種設定；執行期映像 `python -m bot.runtime_policy` 與匯入檢查 |
| Skipped | 2 個 TLS 測試（本機拋棄式伺服器未開 TLS；CI 不允許 skip） |
| Not Run | PostgreSQL 17.11（只在 CI）；`scripts.ci.runtime_smoke.main` 與 `verify-fresh-postgres.py` 完整流程（只在 CI） |
| Manual Pending | 真實 YouTube API 批次回應與額度；Supabase Session Pooler；真實 VM 的新 compose、金鑰檔權限、`rolled_back` 路徑；Discord 卡片顯示 |

## 已知限制與未做

- 回滾到不含 `SYSTEM_JUDGEMENT` 的舊映像：已用基底提交 `51ee43a` 的程式實測，只有「對有系統證據的影片，舊版 Owner 檢視／確認版本／重審」會 `ValueError`；投稿、待處理清單、結算與餘額正常。**為 0 才是乾淨回滾**；移除證據列需 Owner 授權（見啟用與回滾清單）。
- 卡片與回覆文案（`NEAR_DUPLICATE_NOTE`、`SIMILAR_REVIEW_REPLY`）由 PM 在 Sean 達使用上限後完成，需 Sean 回來審查。
- 圈複雜度：`bot/runtime_policy.py::check_policy` 原粗估約 11，已由 Sean 拆成小函式（`a6115fd`），行為不變；圈複雜度目標 ≤10 沒有被測試強制，函式長度與巢狀深度由 `tests/test_phase2_quality.py` 強制，全數通過。
- 階段 B（失敗退點）、階段 C（`/daily` 自訂時間、半遮蔽）不在本 PR。
- 既有問題，非本 PR 引入：`bot/share_replies.py:59` 在重審核准過的案件重放同一 operation 時拋 `ValueError`（發獎不受影響）。
- Google 回覆範圍待 Owner 確認；所有 API 比對測試都是 mock，沒有實際呼叫 YouTube。
