# 階段 A（自動審核含時長與自動版本確認）測試與部署計畫

作者：Ken（測試與部署）　對象：Emily（PM）、Owner　日期：2026-11-02
性質：**唯讀盤點與計畫，尚未改任何程式、測試或設定，也尚未執行任何測試、docker 或連線**。

標示規則：**[事實]** 是這次實際讀到檔案確認的內容（附 file:line）；**[推測]** 是我的判斷或尚未驗證；**[未讀]** 是沒讀到的檔案。

## 0. 白話摘要與需要先決定的事

階段 A 要讓 Bot 在有人投稿時，自己查 YouTube 官方資料、自己判斷「這首是不是可疑」，沒問題就自動核准並確認版本，Owner 只處理可疑的案件。這會讓**系統自己動點數（Nickel）的前置條件**，所以測試的重點不是「功能能跑」，而是「出錯時一律退回人工，不會誤發點數」。

以下幾件事會影響測試能不能寫得出來，建議先請 Emily 或 Owner 裁決（詳見第 6 節）：

| 編號 | 要決定什麼 | 為什麼擋住測試 | 我的建議 |
|---|---|---|---|
| D1 | 「歌名相似度」用什麼演算法、門檻多少 | 沒有固定門檻，測試無法寫出明確的「剛好過／剛好不過」案例 | 先定門檻與正規化規則（大小寫、全半形、括號內容），再寫測試 |
| D2 | 「額度用完→待審」是進 Owner 待審卡片，還是維持目前的「稍後重試」 | 目前程式是後者（`bot/auto_review.py:52-` 的 `RETRY`，什麼都不寫入）；需求寫的是前者，兩者行為不同 | 建議前者，但要明講「待審」是 `PENDING_REVIEW` 並帶原因；否則 Owner 看不到案件 |
| D3 | 「自動確認版本」是否可以由系統身分（actor 0）寫入版本確認 | 版本確認是發獎的必要條件之一，現行文件明說官方資料與認可頻道都不能證明版本（`docs/deploy/auto-review-nickel-enable.md:25`） | 這是金錢風險最高的一步，建議 Owner 明確簽核，並先以 `JUKEBOX_AUTO_SETTLEMENT=off` 觀察一段時間 |
| D4 | 每次投稿的 YouTube 配額上限與每輪上限 | 每筆新投稿要 1 + 10 次 API（500 首 / 50 = 10 批）；目前每輪巡檢上限 20 次（`bot/auto_pipeline.py:22`） | 先決定「超過上限時是排隊還是轉待審」（與 D2 連動） |

## 1. 現況盤點（全為事實，除非標 [推測]）

### 1.1 測試目錄、fixture 與 mock 慣例

- `tests/` 有 159 個 `*.py`（含 `test_*.py` 與輔助模組，如 `tests/postgres_support.py`、`tests/submission_persistence_fake.py`、`tests/intake_fixture.py`）。[事實：`ls tests`]
- pytest 設定：`pyproject.toml:11-17`，`testpaths=["tests"]`、`pythonpath=["."]`、`--basetemp=.pytest_tmp`；唯一自訂標記是 `postgres`（`pyproject.toml:15-17`）。
- `tests/conftest.py`：
  - 啟動時若 `python -O` 就中止（避免 assert 被拿掉造成假通過）。
  - `database_settings` 用 monkeypatch 設合成憑證。
  - `pg_cluster`（session 級）：沒設 `JB_PG_TEST_HOST` 與管理密碼就 `pytest.skip`；有設就先 `prepare_cluster`（見下）。
  - `pg_database`（每測試一個）：建立新資料庫、套用所有 migration（含 0004）、測完 drop。
- `tests/postgres_support.py`：只連 `cluster_name=jukebox101-disposable-test` 的伺服器，否則 `pytest.exit(returncode=4)`；資料庫名稱前綴 `jb_t_`，只 drop 自己建的；三個角色 `jukebox101_app / migrator / readonly`，密碼隨機只存記憶體；連線 `sslmode=disable`（隔離網路內）。
- mock 慣例：
  - 官方 API 在 **HTTP transport 邊界**用腳本化 fake（`ScriptedTransport`，`tests/test_youtube_metadata.py:~30-60`），重試與配額行為從公開的 `lookup()` 結果觀察。
  - 管線層用 `FakeMetadata`（每支影片一段腳本，未腳本化的影片預設為「認可頻道、189 秒音樂」，`tests/test_postgres_auto_pipeline.py:~50-75`）。
  - 離線資料庫用 SQL 協定 fake `FakeDatabase`（`tests/submission_persistence_fake.py`，由 `tests/test_owner_cli.py` 使用）。
  - 時間以注入的 `clock`／`monotonic` 控制，不用真實 sleep。
  - docstring 與 `[purpose]` 註解格式依 `CODING_STANDARD.md` §9，新測試要遵守。
- 離線測試名稱慣例：`test_<完整句子>`，例如 `test_an_empty_allowlist_never_auto_approves`（`tests/test_youtube_metadata.py:225`）。

### 1.2 現有自動審核實作（階段 A 的起點）

- 政策版本 `trusted-channel-v1`（`bot/auto_review.py:14`）。決策函式 `decide_auto_review`（`bot/auto_review.py:52`）：API 暫時失敗／配額用盡 → `RETRY`（不寫入）；找不到 → `UNAVAILABLE`；直播／未處理完／無時長 → `HOLD`（不寫入）；時長超出 60-720 秒 → `DURATION_REJECT`；否則由 `_trust_withheld`（`bot/auto_review.py:86`）逐項檢查：頻道不在清單、非音樂分類、非公開、有直播歷史、標題含排除字 → `DURATION_ONLY`（記錄時長、待 Owner）；全過 → `AUTO_APPROVE`。
- **目前沒有**「與既有歌曲比對」的任何程式：`decide_auto_review` 只收單一 `MetadataLookup` 與允許清單。同頻道時長差、歌名相似度、版本關鍵字、批次重查**全部是新增**。
- 標題目前只在記憶體中使用、不保存（`bot/youtube_metadata.py` 的 `VideoMetadata.title` 註解）。稽核文字 `_audit` 會寫入 `channel=...` 與取得時間，retention 模式下則改用不含頻道的 `public_reason`（`bot/auto_review.py` 的 `_verdict`／`_audit`）。**[推測]** 因此「日誌不含標題／頻道」需要和「稽核歷程」分開定義：本計畫只對應用程式**日誌（logging）**做斷言，稽核列的內容是否可含頻道 ID 要 Owner 決定（見第 6 節 R6）。
- 單支查詢 `YouTubeMetadataClient.lookup(video_id)`（`bot/youtube_metadata.py:157`）一次只查一個 ID；`http_transport`（`bot/youtube_metadata.py:99`）也是單一 ID。**批次 50 首是新增能力**。
- 管線 `AutoPipeline`：`process`（`bot/auto_pipeline.py:67`）、`sweep`（`:77`）、`_review_step`（`:197`）；每輪最多 20 次 provider 呼叫（`:22` `SWEEP_LOOKUP_LIMIT`）。
- 啟動設定 `load_auto_settings`（`bot/auto_startup.py:39`）：`JUKEBOX_AUTO_REVIEW=on` 但 `JUKEBOX_API_RETENTION` 不是 on → `ConfigurationError(RETENTION_REQUIRED_MESSAGE)`，且在讀任何憑證之前拒絕（`:47`）。開關值非法 → 拒絕啟動（`bot/config.py` 的 `_strict_switch`）。允許清單空白合法但「不自動核准任何歌」（`bot/config.py` 的 `load_trusted_channel_ids`）；清單格式需 `UC` + 22 字元。

### 1.3 開關如何被現有保護機制限制

| 機制 | 位置 | 現在的限制 | 對階段 A 的影響 |
|---|---|---|---|
| 正式 compose（審閱用） | `deploy/controlled/compose.yaml:1`（檔頭「REVIEW ONLY」）、`:47-48`（`JUKEBOX_FORMAL_EXCHANGE`／`JUKEBOX_NICKEL_CLAIM` 固定 `'off'`）、`:49`（秘密只有 3 個）、`:27-32`（healthcheck） | **沒有任何** `JUKEBOX_AUTO_*`、`YOUTUBE_*` 設定；秘密只掛 discord_bot_token、db_password、db_ca.crt | 缺開關 = 目前 Bot 在這份 compose 下一定是自動模式 off |
| 疊加檔 | `compose.auto-review.yaml:5-17` | 已有 `JUKEBOX_AUTO_REVIEW`、`JUKEBOX_AUTO_SETTLEMENT`、`JUKEBOX_API_RETENTION`、`YOUTUBE_TRUSTED_CHANNEL_IDS`、`YOUTUBE_API_KEY_FILE`，預設 off，秘密 `${JB_SECRET_DIR}/youtube_api_key.txt` | 這個疊加檔**不在 CD 路徑上**：主機腳本只用單一檔案 `/etc/jukebox101/compose.cd.yaml`（`deploy/host/jukebox101-deploy:14, 65`） |
| CI 契約 | `scripts/ci/runtime_smoke.py:26-41`（`assert_bot_policy`） | `:35-36` 兩個舊開關必須 `off`；**`:40-41` 秘密集合必須剛好等於 3 個** | 新增第 4 個秘密會讓這個斷言失敗，必須同步改 |
| CI 契約（controlled） | `scripts/ci/runtime_smoke.py:44-64` | 服務只能有 `bot`、不可有 `command`／`volumes`、網路固定 | 不影響，但新增環境變數的預設值要被渲染驗證 |
| CI 契約（離線測試） | `tests/test_controlled_compose_policy.py:12-33`（合成設定，秘密清單寫死 3 個）、`:45-57`（21 種故障參數化）、`:60-88`（`mutate_profile`） | 沒有任何自動模式的案例 | 要加案例（見 3.3） |
| 執行期政策 | `bot/runtime_policy.py:31-32`（只檢查 `JUKEBOX_FORMAL_EXCHANGE`、`JUKEBOX_NICKEL_CLAIM`）、`:44`（`service.auto_pipeline is not None` 即拋錯） | 沒有檢查 `JUKEBOX_AUTO_*` 環境變數；是用「不傳 pipeline」來證明關閉 | CI 映像天然是自動模式 off。**[推測]** 要加明確檢查：若 CI 環境誤設 `JUKEBOX_AUTO_REVIEW=on`，runtime_smoke 必須失敗 |
| 健康檢查 | `bot/healthcheck.py:25-26`（舊開關為 on → `FEATURE_POLICY_ENABLED`）、`:26`（呼叫 `load_auto_settings()`）、`:30-31`（設定例外 → `not_ready`、`HEALTH_DEPENDENCY_FAILED`） | **[事實] 自動審核與保存開啟並沒有被當成問題**；非法設定或缺金鑰已經會 `not_ready`。也就是說「合法開啟」在程式上大致已成立，缺的是**測試**：`tests/test_gateway_health.py:92-127` 只在自動模式未設定的情況下測，沒有任何測試證明「on + 完整設定 = ready」或「on + 缺金鑰 = not_ready」 | 見 3.3 的 HC 系列 |
| 就緒檢查 | `bot/readiness.py:18-36` | 資料庫 `settlement_enabled` 與 `JUKEBOX_AUTO_SETTLEMENT` 必須一致（雙向） | 階段 A 若動到發獎開關，這條仍是最後防線 |
| 主機腳本 | `deploy/host/jukebox101-deploy:65`（`--env-file $ENVFILE`）、`:98-109`（`admit` 先檢查再停舊 Bot） | 腳本完全不認識自動審核開關；只要 compose 與 bot.env 擁有者正確即可 | **[推測]** 不必改也能運作，但缺秘密檔時會「先停舊 Bot 再啟動失敗再回滾」（見 4.5） |
| 主機 compose 更新 | `scripts/deploy/install-host.sh:30` | `test -e /etc/jukebox101/compose.cd.yaml \|\| install ...`：**已存在就不覆寫** | 合併 PR 之後，VM 上的 compose 不會自動拿到新設定，需要人工更新（見 4.5） |

### 1.4 CI 與測試的執行路徑

- 離線：`docker build -f test.Dockerfile`（`ci.yml:32-34`）→ `scripts/ci/offline.py`：`--network none`、`-m "not postgres"`、JUnit 計數下限 **1747**（`scripts/ci/offline.py:29`）。`test.Dockerfile`：python:3.12-slim-bookworm、`pip install -r requirements-test.txt`、非 root uid 10002、`ENTRYPOINT python -m pytest`（`test.Dockerfile:3, 15, 17`）。
- PostgreSQL：`scripts/dev/verify-fresh-postgres.py`，`POSTGRES_BUILDS`（`:20`）= 18.6 與 17.11；預設 18，`--postgres-major 17`；計數下限 **35**（`:114`）；CI 兩個都跑（`ci.yml:48-57`）。本機另有 `compose.test.yaml`（PG 18.6，`cluster_name` 安全標記在 `:11`，`tests` 服務指令 `-m postgres`）——**只有 18，沒有 17**。
- 執行期映像：`scripts/ci/runtime_smoke.py`（`docker run --network none --read-only ...`，再 `docker compose config` 渲染三種設定：`compose.yaml`、`deploy/controlled/compose.yaml` 帶環境、同檔用預設值，`:86-104`）。
- **[事實] 行為提醒**：計數下限（1747 與 35）是「不下降的下限」（`scripts/ci/check_junit.py` 檔頭註解），新增測試不會讓它失敗；但若階段 A 新增了 postgres 測試，建議同步調高 35，否則 CI 不會發現「有人悄悄把新測試跳過」。
- **[事實] Session Pooler 目前沒有任何自動化 PostgreSQL 測試**：搜尋 "pooler" 只出現在帳號後綴處理（`bot/config.py:23-33`，測試在 `tests/test_foundation.py:117-`、`tests/test_gateway_health.py:109-127`），那是「登入名稱後綴」的邏輯，不是對真正連線池的測試。拋棄式 PostgreSQL 的 17／18 測試連的是直連的 Postgres，不經過 Supavisor。見 3.5 與第 4 節。

### 1.5 沒讀到的檔案（明確列出）

- **[未讀]** `bot/database/submission_repository.py` 的 `apply_auto_review`／`pending_validation_operations`／`revalidation_operations`（只用 grep 確認存在，未讀實作）、`bot/database/postgres/0004_api_observations.sql`（短期觀測表欄位與清除機制）、`bot/owner_cli.py` 全文（只看到子命令名稱）、`bot/discord_owner_cards.py`、`docs/api-data-lifecycle-design.md`、`.github/workflows/release.yml` 與 `notify.yml`（僅透過 `tests/test_ci_safety_contract.py` 間接知道內容）、`compose.yaml`（根目錄）、`Dockerfile`、`tests/test_postgres_api_retention.py` 的細節、`deploy/RUNBOOK.md`。
- 這些檔案在 TDD 實作時，負責實作的人必須先讀；我的計畫中凡涉及它們的測試名稱，都是 [推測] 的命名，不是現有符號。

## 2. 測試策略總則

1. **紅綠順序**：每個項目先寫失敗測試、確認它因為「功能不存在」而失敗（不是因為拼字錯誤），再實作到轉綠。
2. **離線優先**：所有判斷邏輯（疑慮規則、批次、額度、清單為空）用純函式或 fake transport 測，放在 `-m "not postgres"`，可在 `--network none` 容器內跑。只有「真實資料庫交易／不保存／並行」才用 postgres 標記。
3. **fail closed 是第一原則**：任何測試只要出現「不確定」，預期結果永遠是「不自動核准、不發點數」。
4. **不用真實 API 與真實時間**：不連 YouTube、不依賴系統時鐘（注入 `clock`），不使用 `sleep`。
5. **不改既有斷言**：`runtime_smoke.py:41` 的「秘密剛好 3 個」需要變更時，視為契約變更，在 PR 說明理由並以新測試鎖住「剛好 4 個、且第 4 個只能是 youtube_api_key」。
6. **名稱與格式**：沿用 `CODING_STANDARD.md` §9 的 docstring（`@description`／`@param`／`@return`）與 `[purpose]` 註解。

## 3. TDD 測試清單（依實作順序）

下表「檔名」都是**新檔**（除標明「擴充」者）。測試函式名稱是建議，實作者可微調，但要保留語意。每個群組先寫、先紅、再實作。

### 3.1 第 1 組：疑慮規則（純函式，無 I/O）——最先寫

檔案：`tests/test_suspicion_rules.py`（模組名稱 [推測]：`bot/suspicion_rules.py`）。
共同輸入：兩個 `VideoMetadata`（新投稿 `candidate`、既有歌曲 `existing`）與固定常數；預設值：同頻道、時長差 10 秒、標題完全不同、無版本關鍵字（即「無疑慮」）。

| # | 測試名 | 輸入 | 預期 |
|---|---|---|---|
| S1 | `test_a_candidate_with_no_signal_has_no_suspicion` | 預設值 | 疑慮清單為空 |
| S2 | `test_same_channel_and_duration_difference_of_exactly_three_seconds_is_suspicious` | 同頻道、180 與 183 秒 | 有「時長相近」疑慮（邊界含） |
| S3 | `test_same_channel_and_duration_difference_of_four_seconds_is_not_suspicious` | 同頻道、180 與 184 秒 | 無疑慮 |
| S4 | `test_same_channel_and_identical_duration_is_suspicious` | 同頻道、差 0 秒 | 有疑慮 |
| S5 | `test_different_channel_with_identical_duration_is_not_suspicious_by_duration_alone` | 不同頻道、差 0 秒、標題不同 | 無「時長」疑慮 |
| S6 | `test_duration_missing_on_either_side_is_unknown_not_suspicious_but_blocks_auto_approval` | 任一方時長為 None | 結果為「無法判定」，**不得**當成「無疑慮」（fail closed）。依 D2 之前先以「無法判定 = 不自動通過」寫死 |
| S7 | `test_title_similarity_at_the_threshold_is_suspicious` | 標題相似度剛好等於門檻（D1 定案後填數字） | 有「歌名相似」疑慮 |
| S8 | `test_title_similarity_just_below_the_threshold_is_not_suspicious` | 門檻減一個最小單位 | 無疑慮 |
| S9 | `test_title_similarity_ignores_case_width_and_surrounding_punctuation` | `"Song"` vs `"ＳＯＮＧ"`、`"Song (Official)"` | 視為相似（具體規則依 D1） |
| S10 | `test_title_similarity_handles_cjk_titles` | 兩個中日文標題 | 不拋例外；結果可重現 |
| S11 | `test_empty_or_missing_title_is_unknown_and_blocks_auto_approval` | title 為 None 或空字串 | 不自動通過（沿用 `bot/auto_review.py:_trust_withheld` 對空標題的作法） |
| S12 | `test_each_version_keyword_is_a_suspicion` | 參數化：live、remix、cover、acoustic、instrumental、sped up、slowed、nightcore、reverb、piano、karaoke、ver.、現場、翻唱、改編（**清單以 Owner 核定為準**） | 每個關鍵字都有「版本疑慮」 |
| S13 | `test_version_keywords_are_matched_case_insensitively_and_by_word` | `"LIVE"`、`"Live"`、以及 `"Olive"`（不應命中） | 前兩者命中，`Olive` 不命中（避免子字串誤判） |
| S14 | `test_a_title_without_any_keyword_has_no_version_suspicion` | 一般標題 | 無疑慮 |
| S15 | `test_signals_are_independent_and_all_are_reported` | 同時觸發 S2 + S7 + S12 | 回報三個疑慮（供 Owner 卡片顯示原因） |
| S16 | `test_the_result_carries_reason_codes_only_not_titles_or_channel_ids` | 觸發任一疑慮 | 回傳物件、`repr`、`str` 都不含標題與頻道 ID（日誌／原因字串用的是代碼） |
| S17 | `test_the_function_is_pure_and_does_not_read_the_clock_or_environment` | monkeypatch 禁用 `os.environ` 與時間 | 通過 |

### 3.2 第 2 組：YouTube 批次查詢（fake transport）

檔案：`tests/test_youtube_metadata_batch.py`（擴充現有 `tests/test_youtube_metadata.py` 的 `ScriptedTransport` 慣例；新方法名稱 [推測]：`lookup_many`）。

| # | 測試名 | 輸入 | 預期 |
|---|---|---|---|
| B1 | `test_ids_are_sent_in_batches_of_at_most_fifty` | 120 個 ID | 3 次 transport 呼叫，大小 50/50/20 |
| B2 | `test_exactly_fifty_ids_use_one_call` | 50 個 | 1 次呼叫 |
| B3 | `test_an_empty_id_list_makes_no_call` | 空 | 0 次呼叫、空結果 |
| B4 | `test_duplicate_ids_are_asked_once` | 重複 ID | 只問一次 |
| B5 | `test_ids_missing_from_the_response_are_reported_not_found_individually` | 50 個中 3 個沒回 | 這 3 個為 `NOT_FOUND`，其餘為 `FOUND`，不影響整批 |
| B6 | `test_a_response_item_for_an_unrequested_id_is_ignored` | 回應多出一個未要求的 ID | 忽略（沿用單支查詢「別人的回應不可信」，`tests/test_youtube_metadata.py:111`） |
| B7 | `test_quota_exhaustion_mid_batch_stops_further_calls_and_marks_the_rest_quota_exhausted` | 第 2 批回 `quotaExceeded` | 第 3 批不送出；第 2、3 批 ID 為 `QUOTA_EXHAUSTED`；第 1 批結果保留 |
| B8 | `test_a_temporary_failure_retries_a_bounded_number_of_times_per_batch` | 腳本化暫時錯誤 | 重試次數同單支（`MAX_ATTEMPTS`）、有退避 |
| B9 | `test_the_api_key_is_sent_in_a_header_and_never_in_a_url_or_message` | 檢查 transport 收到的參數與例外訊息 | 金鑰不在 URL／例外文字 |
| B10 | `test_batch_results_are_not_written_anywhere` | 傳入一個會記錄寫入的 repository spy | 批次查詢本身零寫入（保存屬於另一層，見 3.5） |
| B11 | `test_the_rate_limit_applies_between_batches` | 注入 clock／sleep | 批次之間遵守既有最小間隔 |

### 3.3 第 3 組：自動決策與管線（離線，fake repository + fake metadata）

檔案：`tests/test_auto_review_stage_a.py`（擴充 `decide_auto_review` 的行為）與 `tests/test_auto_pipeline_stage_a.py`（管線）。

決策層（`tests/test_auto_review_stage_a.py`）：

| # | 測試名 | 輸入 | 預期 |
|---|---|---|---|
| A1 | `test_every_check_passing_auto_approves_and_confirms_the_version_by_the_system_actor` | 認可頻道、音樂分類、時長在 60-720、無任何疑慮、既有歌曲批次查詢完整 | `AUTO_APPROVE`；版本確認以系統身分（actor 0，非 Owner）寫入；政策版本字串已升版（`trusted-channel-v1` → 新值，**D3 與 Owner 簽核後定**） |
| A2 | `test_an_empty_allowlist_never_approves_anything_automatically` | 允許清單空；其餘全過 | **不**自動核准；結果 `DURATION_ONLY`（轉待審）；對每一種歌都成立（參數化 5 種輸入）。既有類似測試：`tests/test_youtube_metadata.py:225` |
| A3 | `test_an_untrusted_channel_waits_for_the_owner` | 頻道不在清單 | 待審，原因代碼 `channel_not_trusted` |
| A4 | `test_each_suspicion_signal_alone_prevents_auto_approval` | 參數化 S2／S7／S12 各一次 | 每個單獨都 → 待審，**不**核准、**不**確認版本 |
| A5 | `test_a_suspicion_sends_the_case_to_review_and_never_rejects` | 觸發疑慮 | 結果是待審，不是 `REJECTED`、不是 `DURATION_REJECT`（只有時長超出 60-720 才會拒絕） |
| A6 | `test_official_duration_out_of_range_still_rejects_even_when_nothing_else_is_suspicious` | 59 秒、721 秒 | `DURATION_REJECT`（維持既有規則，`bot/auto_review.py:63-` 邊界 60/720 含） |
| A7 | `test_quota_exhausted_during_the_existing_song_recheck_sends_the_case_to_review_not_rejection` | 新投稿本身查得到，但重查既有歌曲時 `QUOTA_EXHAUSTED` | 待審（**D2 定案後確認**是 `PENDING_REVIEW` 帶原因），絕不是 `REJECTED`，絕不自動通過 |
| A8 | `test_quota_exhausted_when_looking_up_the_new_submission_itself_writes_nothing_and_retries_later` | 新投稿的查詢就 `QUOTA_EXHAUSTED` | 維持既有行為：不寫入、稍後重試（既有：`tests/test_youtube_metadata.py:256`） |
| A9 | `test_an_api_failure_during_the_recheck_never_auto_approves` | 重查回 `TEMPORARY_FAILURE` | 不自動核准；是否待審或重試依 D2；**一定不是核准** |
| A10 | `test_an_incomplete_recheck_is_treated_as_unknown_not_as_no_conflict` | 500 首只回來 300 首 | 不自動通過（缺資料不等於沒有相似歌曲） |
| A11 | `test_missing_trusted_channel_setting_and_missing_key_cannot_reach_the_decision` | 設定層（見 3.4） | 決策函式不被呼叫 |
| A12 | `test_the_reason_text_names_the_signal_codes_for_the_owner_card` | 觸發 S2 | 原因字串含代碼（如 `similar_duration_same_channel`），不含標題與頻道名稱 |

管線層（`tests/test_auto_pipeline_stage_a.py`）：

| # | 測試名 | 輸入 | 預期 |
|---|---|---|---|
| P1 | `test_a_new_share_rechecks_at_most_the_most_recent_five_hundred_existing_songs` | repository 回傳 800 個既有影片 ID | 只取最近 500 個送去批次查詢（10 批）；總 provider 呼叫 = 1（新投稿）+ 10 |
| P2 | `test_the_recheck_runs_only_for_a_new_submission_not_for_every_sweep` | 連續 3 輪巡檢無新投稿 | 重查呼叫為 0 |
| P3 | `test_a_sweep_never_exceeds_its_provider_call_budget_including_batches` | 一輪內有 3 筆待查投稿（共需 33 次） | 總呼叫不超過上限（上限值 D4 定案；目前 `SWEEP_LOOKUP_LIMIT=20`，`bot/auto_pipeline.py:22`）；超出的案件留待下一輪，**不**轉成核准 |
| P4 | `test_the_recheck_happens_with_no_transaction_open` | repository spy 記錄交易開關 | 呼叫 provider 時沒有開啟的交易（沿用 `bot/auto_pipeline.py` 模組說明的既有不變條件） |
| P5 | `test_an_empty_allowlist_calls_no_recheck_at_all` | 清單空 | 不花任何額度做重查（直接轉待審即可）。**[推測]** 這是節省額度的最佳化，是否採用先問 Emily |
| P6 | `test_auto_review_off_makes_no_provider_call` | `JUKEBOX_AUTO_REVIEW=off` | provider 為 None、呼叫 0 次（既有：`tests/test_auto_settings_and_replies.py:27`） |
| P7 | `test_a_lookup_pause_prevents_asking_about_the_same_video_again_within_the_backoff` | 既有行為回歸 | 暫停期內不再問（`RETRY_BACKOFF_SECONDS`，`bot/auto_pipeline.py`） |
| P8 | `test_a_provider_exception_for_one_case_does_not_stop_other_cases` | 一件拋例外 | 其餘照常（沿用 `SWEEP_ERRORS`，`bot/auto_pipeline.py` 隔離步驟） |
| P9 | `test_the_share_reply_says_waiting_for_the_owner_when_a_suspicion_was_found_and_never_plus_one` | 疑慮案件 | 回覆是「待審」，不含 `+1` |
| P10 | `test_the_share_reply_says_plus_one_only_after_a_committed_award` | 全過案件，且發獎已開 | 只有已提交的獎勵收據才出現 `+1`（既有：`tests/test_auto_settings_and_replies.py:113`） |

### 3.4 第 4 組：開關設定錯誤一律 fail closed（離線）

檔案：擴充 `tests/test_auto_settings_and_replies.py`（現有 `:27-64` 已有部分），新增 `tests/test_auto_settings_failclosed.py`。

| # | 測試名 | 輸入 | 預期 |
|---|---|---|---|
| F1 | `test_every_automatic_switch_defaults_to_off_when_unset` | 空環境 | 四個開關皆 off、不建管線（既有，維持） |
| F2 | `test_an_unrecognised_value_of_each_switch_refuses_to_start` | 對 `JUKEBOX_AUTO_REVIEW`／`_SETTLEMENT`／`_RETENTION` 各給 `maybe`、`ON `（含空白的非法值）、`2` | `ConfigurationError`（`ON ` 因 `.strip().lower()` 其實合法，需確認：`bot/config.py` 的 `_strict_switch` 有 strip，**測試要依實際行為寫**） |
| F3 | `test_review_on_without_retention_refuses_before_reading_any_credential` | 既有（`tests/test_api_retention_offline.py:203`），維持回歸 | 拒絕、未讀金鑰檔 |
| F4 | `test_review_on_without_an_api_key_refuses_to_start` | 金鑰檔不存在／空檔 | `ConfigurationError`，訊息不含路徑以外的資訊 |
| F5 | `test_review_on_with_a_group_or_world_readable_key_file_is_refused` | 權限 0644 | 拒絕（沿用秘密檔政策，`tests/test_secret_file_policy.py`） |
| F6 | `test_a_malformed_channel_id_in_the_allowlist_refuses_to_start` | `"@handle"`、`"UCshort"`、中文名稱 | `ConfigurationError`（既有 `tests/test_auto_settings_and_replies.py:55`，維持） |
| F7 | `test_an_empty_allowlist_with_review_on_starts_but_approves_nothing` | `JUKEBOX_AUTO_REVIEW=on`、清單空 | 可以啟動（不是設定錯誤），但 A2 保證不自動核准；**這是產品決策：建議啟動時印出一行警示（不含頻道 ID）**，需 Emily 確認 |
| F8 | `test_settlement_on_without_the_database_flag_is_reported_by_readiness` | 既有（`tests/test_auto_settings_and_replies.py:75`），維持 | 回報 `SETTLEMENT_SWITCH_OFF` |
| F9 | `test_auto_review_on_with_settlement_on_but_version_confirmation_disabled_refuses`（**視 D3 而定**） | 若 D3 要求版本自動確認要有獨立開關 | 組合不合法時拒絕啟動 |
| F10 | `test_no_switch_value_is_ever_echoed_in_a_startup_error` | 非法值含看似秘密的字串 | 錯誤訊息只含變數名稱，不含值 |

### 3.5 第 5 組：健康檢查（HC 系列，離線）

檔案：新增 `tests/test_healthcheck_auto_modes.py`（沿用 `tests/test_gateway_health.py:92-127` 的 monkeypatch 手法）。

| # | 測試名 | 輸入 | 預期 |
|---|---|---|---|
| HC1 | `test_review_and_retention_on_with_a_valid_configuration_is_ready` | 環境：review/retention on、有效清單、金鑰讀取成功；`check_readiness`、`check_gateway` 回 ready | `result == 'ready'`，`problems == []` |
| HC2 | `test_review_on_without_the_api_key_is_not_ready` | 金鑰缺 | `not_ready`，`problems == ['HEALTH_DEPENDENCY_FAILED']`，`error_class == 'ConfigurationError'`（目前行為，`bot/healthcheck.py:30-31`） |
| HC3 | `test_review_on_without_retention_is_not_ready` | 缺保存開關 | `not_ready` |
| HC4 | `test_an_invalid_switch_value_is_not_ready` | 非法值 | `not_ready` |
| HC5 | `test_an_invalid_allowlist_is_not_ready` | 非法頻道 ID | `not_ready` |
| HC6 | `test_review_on_with_an_empty_allowlist_is_ready_because_it_is_safe` | 清單空 | `ready`（空清單是安全狀態，只是不自動核准）；若 Emily 希望視為不健康則改為 `not_ready`，**需決定** |
| HC7 | `test_formal_exchange_or_claim_on_is_still_a_policy_violation_even_when_auto_review_is_on` | 舊開關 on | 仍回報 `FEATURE_POLICY_ENABLED`（`bot/healthcheck.py:25`）。確保放寬自動模式時**沒有**順手放寬舊限制 |
| HC8 | `test_settlement_mode_and_database_flag_must_still_agree_when_review_is_on` | 一致／不一致各一次 | 不一致 → 問題碼同 `bot/readiness.py:34-36` |
| HC9 | `test_health_output_never_contains_the_key_the_allowlist_or_a_title` | 檢查 `check_health()` 輸出 JSON 全文 | 不含金鑰、`UC…` 頻道 ID、標題 |
| HC10 | `test_the_health_check_makes_no_youtube_call` | transport spy | 零網路呼叫、零額度 |

### 3.6 第 6 組：Owner CLI 頻道 ID 小工具（離線）

檔案：`tests/test_owner_cli_channel_ids.py`（子命令名稱 [推測]：`owner_cli channel-id`；`bot/owner_cli.py` 全文**未讀**）。

| # | 測試名 | 輸入 | 預期 |
|---|---|---|---|
| C1 | `test_a_watch_url_is_resolved_to_its_channel_id_using_one_official_lookup` | 影片網址 + fake provider | 印出 `UC…` ID；僅 1 次呼叫（1 單位額度） |
| C2 | `test_the_tool_prints_only_the_channel_id_and_not_the_title_or_channel_name` | 同上 | 標準輸出只含 ID（可加「請貼到 YOUTUBE_TRUSTED_CHANNEL_IDS」固定句子） |
| C3 | `test_the_tool_never_modifies_the_allowlist_or_any_file` | 檔案系統／repo spy | 零寫入；Owner 自己貼進 `bot.env` |
| C4 | `test_an_invalid_or_non_youtube_url_is_refused_without_a_call` | 垃圾網址、Spotify 網址 | 非零退出碼、未呼叫 provider |
| C5 | `test_missing_api_key_refuses_with_an_actionable_message_without_printing_the_key` | 無金鑰 | 非零退出碼 |
| C6 | `test_only_the_configured_owner_environment_can_run_it` | 沿用 `tests/test_owner_cli.py` 的 Owner 設定檢查手法 | 與現有 CLI 一致 |
| C7 | `test_the_tool_uses_no_database_connection` | `forbid_connection` trap（模式同 `bot/runtime_policy.py:18-23`） | 通過 |

### 3.7 第 7 組：日誌不含標題／頻道（離線，`caplog`）

檔案：`tests/test_stage_a_logging.py`（慣例：`tests/test_postgres_api_retention.py:299` 的 `caplog` 用法）。

| # | 測試名 | 輸入 | 預期 |
|---|---|---|---|
| L1 | `test_no_log_record_contains_a_title_or_channel_id_across_every_verdict_kind` | 對每個 `AutoReviewKind`（含疑慮）各跑一次管線，`caplog.set_level(DEBUG)`，標題用不可能巧合的標記字串（如 `TITLE-CANARY-xyz`）、頻道用 `UC` + 可辨識字元 | 所有 `caplog.text`／`record.getMessage()`／`record.args`／`exc_info` 都不含標記字串與頻道 ID |
| L2 | `test_log_records_for_failures_contain_only_the_exception_class_name` | provider 拋 `TransportFailure` | 只有類別名稱（沿用 `bot/auto_pipeline.py` 的 `type(error).__name__` 慣例） |
| L3 | `test_the_api_key_never_appears_in_any_log_record_or_exception_message` | 以金鑰標記字串跑完整流程 | 不含 |
| L4 | `test_the_discord_reply_and_owner_card_text_do_not_echo_the_title_of_other_users_songs` | 疑慮案件的 Owner 卡片文字 | 只含原因代碼的白話說明；**是否可顯示「疑似與哪首歌相似」**是產品決策（D1 的延伸），若要顯示則需新定義，測試先鎖「不含既有歌曲標題」 |
| L5 | `test_the_debug_level_does_not_add_titles` | `caplog` 在 DEBUG | 同 L1 |

### 3.8 第 8 組：部署與保護機制測試（離線，不需 docker 的部分）

檔案：擴充 `tests/test_controlled_compose_policy.py`，新增 `tests/test_runtime_policy_auto_modes.py`，擴充 `tests/test_ci_safety_contract.py` 與 `tests/test_deploy_host_script.py`。細節與理由見第 4 節。

| # | 測試名 | 預期 |
|---|---|---|
| D-1 | `test_controlled_compose_declares_each_auto_review_setting_with_an_off_or_empty_default`（擴充 `tests/test_controlled_compose_policy.py`） | 渲染結果 `JUKEBOX_AUTO_REVIEW`、`JUKEBOX_API_RETENTION`、`JUKEBOX_AUTO_SETTLEMENT` 預設 `off`；`YOUTUBE_TRUSTED_CHANNEL_IDS` 預設空；`YOUTUBE_API_KEY_FILE` 指向 `/run/secrets/youtube_api_key` |
| D-2 | `test_secret_set_is_exactly_the_three_base_secrets_plus_youtube_api_key`（擴充 `tests/test_controlled_compose_policy.py`，同步改 `scripts/ci/runtime_smoke.py:40-41`） | 剛好 4 個，第 4 個名稱固定；少或多（例如多掛一個任意秘密）都失敗 |
| D-3 | `test_the_youtube_key_file_must_be_mounted_read_only_from_the_secret_dir`（參數化故障：`mutate_profile` 新增 `youtube_secret_path`） | 掛載來源不在預期位置 → `AssertionError` |
| D-4 | `test_legacy_feature_switches_stay_off_regardless_of_auto_review_settings`（參數化 `mutate_profile`：`claim`、`exchange` 已有，維持，並在「auto on」環境下重跑） | 仍被拒絕 |
| D-5 | `test_the_compose_never_forces_an_auto_switch_on` | 渲染結果沒有任何預設為 `on` 的自動開關 |
| D-6 | `test_compose_policy_accepts_review_on_only_together_with_retention_on`（若 `assert_controlled_policy` 要檢查有效組合） | 單獨 review on 的渲染環境 → 失敗 |
| RP-1 | `test_runtime_policy_refuses_when_an_auto_switch_is_on_in_the_ci_image`（`tests/test_runtime_policy_auto_modes.py`） | `JUKEBOX_AUTO_REVIEW=on` 的環境下 `check_policy()` 拋錯；預設環境仍通過（既有 `tests/test_ci_safety_contract.py:155-162` 維持） |
| RP-2 | `test_runtime_policy_receipt_reports_auto_modes_as_off` | 收據含 `auto_review: off` 等欄位（供 CI 證據留存） |
| H-1 | `test_auto_review_on_with_a_missing_key_file_is_refused_before_the_old_bot_is_stopped`（擴充 `tests/test_deploy_host_script.py`，**可選**，見 4.5） | 回覆 `rejected`、stderr 有固定句子、`docker stop` 零次 |
| H-2 | `test_host_script_never_prints_or_logs_any_secret_or_env_value`（回歸） | stderr／stdout 不含 bot.env 內容 |

### 3.9 第 9 組：PostgreSQL 17 與 18（`postgres` 標記，拋棄式伺服器）

檔案：`tests/test_postgres_stage_a.py`（沿用 `pytestmark = pytest.mark.postgres`、`world` fixture 手法，`tests/test_postgres_auto_pipeline.py:27, 147`）。

| # | 測試名 | 預期 |
|---|---|---|
| G1 | `test_a_fully_clean_trusted_submission_is_approved_by_the_system_actor_and_the_version_is_confirmed` | 審核表為已核准（來源為自動來源、非 Owner）、版本確認列存在、`settlement` 關閉時餘額不變；`ledger_integrity` 通過（`bot/ledger_integrity.py`，沿用 `tests/test_postgres_auto_pipeline.py` 的 `evaluate_ledger_integrity`） |
| G2 | `test_the_same_clean_submission_with_settlement_on_pays_exactly_once_and_balance_increases_by_one` | 帳本 +1 一筆、`/balance` 對應值 +1；重放不重複發 |
| G3 | `test_a_suspicious_submission_stays_pending_review_with_the_reason_visible_to_the_owner_card` | 審核表為待審、原因代碼存在；餘額不變 |
| G4 | `test_an_empty_allowlist_leaves_every_submission_pending_and_pays_nothing` | 所有投稿待審、帳本零筆 |
| G5 | `test_quota_exhaustion_leaves_the_case_pending_not_rejected` | 審核狀態不是 REJECTED；帳本零筆 |
| G6 | `test_the_recheck_values_of_existing_songs_are_not_stored_anywhere` | 重查前後，對所有含官方數值的表（`0004` 短期觀測表，**[未讀]** 欄位與表名需實作者確認）做列數／內容比對，既有歌曲的時長、頻道、標題**沒有**新增列 |
| G7 | `test_only_the_new_submissions_own_observation_is_stored_and_it_is_purged_on_schedule` | 新投稿觀測值只存短期表，沿用既有清除測試（`tests/test_postgres_api_retention.py`）的時間推進手法 |
| G8 | `test_two_concurrent_submissions_of_the_same_new_song_produce_one_automatic_decision` | 沿用既有並行測試（`tests/test_postgres_auto_pipeline.py:265`），確認加入重查後仍只有一筆自動審核 |
| G9 | `test_a_similar_pair_submitted_concurrently_does_not_both_auto_approve` | 兩首互相相似的新歌同時投稿 → **不得兩首都被自動核准**（若實作只比對「已存在」而忽略「同時在途」就會出錯；需實作者決定並鎖定語意） |
| G10 | `test_a_lost_commit_acknowledgement_on_the_auto_decision_replays_without_double_effect` | 沿用 `tests/test_postgres_auto_pipeline.py:324`，確認新決策路徑也可冪等重放 |
| G11 | `test_no_sql_written_for_stage_a_requires_features_missing_on_postgres_17` | 在 17.11 上整組跑過（由 CI 矩陣保證，不是單一測試） |
| G12 | `test_session_pooler_compatible_session_state` | **離線 + PG 組合**：確認新增 SQL 只使用交易內狀態（`SET LOCAL`，現有 `bot/database/submission_transaction.py:54`），沒有新增 session 級 `SET`、`LISTEN`、advisory lock、prepared statement 依賴。作法：掃描新增的 SQL 常數 + 在拋棄式 PG 上以「每個交易都換一條連線」的方式跑 G1-G5。**注意：這只能證明不依賴 session 狀態，不能代替真實 Supavisor 驗證（見第 4 節）** |

> 備註：現有 PG 計數下限是 35（`scripts/dev/verify-fresh-postgres.py:114`）。階段 A 的 postgres 測試加入後，下限建議同步調高到「現有數 + 新增數」，避免新測試被悄悄跳過。

### 3.10 實作順序（建議）

1. 3.1（純函式規則；D1 決定後才能寫 S7-S9 的數字）
2. 3.2（批次查詢）
3. 3.3 決策層，再管線層（D2、D4 決定後）
4. 3.4 設定 fail closed
5. 3.5 健康檢查（多數是只補測試，程式可能不用改）
6. 3.6 Owner CLI 小工具
7. 3.7 日誌（建議在每一組完成後都跑一次，最後再統一收斂）
8. 3.8 部署契約與 CI 契約
9. 3.9 PostgreSQL（最後，因為依賴前面的 repository 介面）

## 4. 部署與保護機制改動計畫

### 4.1 `deploy/controlled/compose.yaml`

- **改什麼**：在 `environment` 區加入 `JUKEBOX_AUTO_REVIEW`、`JUKEBOX_API_RETENTION`、`JUKEBOX_AUTO_SETTLEMENT`（預設 `${...:-off}`）、`YOUTUBE_TRUSTED_CHANNEL_IDS`（預設空）、`YOUTUBE_API_KEY_FILE: /run/secrets/youtube_api_key`；`secrets` 區加入 `youtube_api_key`，來源為 `/etc/jukebox101/secrets/youtube_api_key.txt`（與其他秘密同目錄，沿用檔案現有寫法 `:49, :51-57`）。可以參考 `compose.auto-review.yaml:5-17` 的內容，但**不用 `JB_SECRET_DIR`**，因為 CD 路徑的秘密目錄是固定的 `/etc/jukebox101/secrets/`（`docs/deploy/continuous-deployment.md` 第 47 行附近）。
- **為什麼**：CD 只用單一 compose 檔（`deploy/host/jukebox101-deploy:14, 65`），疊加檔進不了 CD 路徑。
- **對應測試**：3.8 的 D-1 ~ D-6；`scripts/ci/runtime_smoke.py` 的渲染檢查（需 docker，屬 Linux 驗證）。
- **[推測] 重要風險**：`docker compose up` 遇到不存在的秘密檔會失敗（我認為是這樣，**尚未驗證**）。因此「預設 off」並不表示不需要檔案：主機上必須先有 `youtube_api_key.txt`。選項：

| 選項 | 做法 | 好處 | 代價 |
|---|---|---|---|
| X1 | 秘密永遠掛載，主機先放一個內容為占位字串的檔案（0400、10001:10001） | 開關由 bot.env 單點控制，不必換 compose | 占位檔是一個「看起來像金鑰」的假檔，需註明；off 時程式不讀它（`bot/auto_startup.py:48-49` 只在 review 開時讀金鑰） |
| X2 | 用 compose profile 或第二個 compose 檔，只在開啟時掛載 | 關閉時完全不碰金鑰檔 | 主機腳本要改成支援多檔（目前只有 `--file "$COMPOSE"`），改動面大 |
| X3 | 把金鑰改成放在 bot.env | 不需要新秘密掛載 | 違反「秘密用檔案、0400」的既有政策（`docs/deploy/owner-secrets.md`），**不建議** |

我的建議：X1，並在 H-1（4.5）加一道部署前檢查。**這是技術方案，需 Emily 確認；不是我可自行決定的事。**

### 4.2 `bot/healthcheck.py`

- **現況事實**：`load_auto_settings()` 已在 `:26` 被呼叫，合法的 on 狀態不會被誤判為問題；非法設定回 `HEALTH_DEPENDENCY_FAILED`（`:30-31`）。
- **改什麼**：**可能不需要改程式**；必要時只做兩件小事：(a) 讓設定錯誤有獨立的問題碼（例如 `AUTO_REVIEW_CONFIG_INVALID`），而不是和資料庫／Gateway 失敗共用 `HEALTH_DEPENDENCY_FAILED`，Owner 看輸出就知道是設定問題（**這是我的 [推測] 改善，不是需求，需 Emily 決定要不要**）；(b) 確保輸出不含金鑰與頻道（HC9）。
- **為什麼**：需求要求「設定錯誤／缺金鑰仍須回報不健康」，現有行為已滿足但沒有測試保護。
- **對應測試**：3.5 全部（HC1-HC10）。

### 4.3 `bot/runtime_policy.py`

- **改什麼**：目前只檢查舊的兩個開關（`:30-32`）。新增：CI 映像執行時若環境有任一 `JUKEBOX_AUTO_*` 或 `JUKEBOX_API_RETENTION` 被設為 on，視為違規；收據加入三個欄位。
- **為什麼**：CI 契約的承諾是「發佈的映像在預設設定下不會自行動點數」。把這個承諾寫成明確檢查，比只靠「沒傳 pipeline」更不容易被日後改動繞過。
- **對應測試**：RP-1、RP-2，以及既有 `tests/test_ci_safety_contract.py:155-162` 維持通過。

### 4.4 CI 契約（`scripts/ci/runtime_smoke.py`、`tests/test_controlled_compose_policy.py`、`ci.yml`）

- **`runtime_smoke.py:40-41`**：秘密集合由「剛好 3 個」改成「剛好 3 個 + `youtube_api_key`」；新增斷言：渲染後三個自動開關預設是 `off`、`YOUTUBE_TRUSTED_CHANNEL_IDS` 預設空。
- **`synthetic_compose_environment`（`:67-84`）**：要多建立假的 `youtube_api_key.txt`（若 compose 使用 `JB_SECRET_DIR` 路徑才需要；若用固定路徑 `/etc/jukebox101/secrets/…`，`docker compose config` 不檢查檔案存在，**這點尚未驗證**）。
- **`tests/test_controlled_compose_policy.py:12-33`** 的合成設定同步加入第 4 個秘密與新環境變數；`mutate_profile`（`:60-88`）新增故障案例（D-3、D-5）。
- **`ci.yml`**：不需要改步驟。若 postgres 測試數增加，調高 `verify-fresh-postgres.py:114` 的下限；離線下限 `scripts/ci/offline.py:29` 同理（兩者都是 `.github` 以外的程式檔，可由實作者一併調）。
- **為什麼**：維持 CI 契約「改弱就失敗」的性質；契約變動要有明確測試證明「放寬的只有這一項」。

### 4.5 主機腳本 `deploy/host/jukebox101-deploy` 與主機 compose 更新

- **事實**：腳本不認識這些開關，也不管理 compose 內容（`docs/deploy/continuous-deployment.md` 第 37 行「不做」清單）；`install-host.sh:30` 不覆寫既有 `/etc/jukebox101/compose.cd.yaml`。
- **結論（[推測]）**：**部署一次只換映像，不會讓 VM 取得新 compose**。階段 A 上線需要一個人工步驟：把 `deploy/controlled/compose.yaml` 的新內容放到 VM 的 `compose.cd.yaml`（root 擁有、群組與其他人不可寫，`jukebox101-deploy:53-55, 101`），再放入 `youtube_api_key.txt`，最後在 `bot.env` 設定開關。順序見第 5 節。
- **可選改動 H-1**：在 `admit()`（`:98-109`）加一條：若 `bot.env` 設 `JUKEBOX_AUTO_REVIEW` 為 on，則 `YOUTUBE_API_KEY` 秘密檔必須存在且為 root 可見的正確權限，否則在**停舊 Bot 之前**就回 `rejected` 並用固定句子說明。理由：沒有這道檢查時，缺檔會在「停舊 Bot 之後」才失敗，雖會自動回滾，但有數十秒～數分鐘的停機（`START_TIMEOUT` 預設 180 秒，`:19`）。可用既有 helper `setting()`（`:51`）讀 bot.env。
- **不建議的改動**：不要讓腳本讀取或驗證金鑰內容；不要讓腳本自動修改 compose 或 bot.env（違反「不管理設定」的設計取捨）。
- **對應測試**：H-1、H-2；既有 `tests/test_deploy_host_script.py` 全部維持通過（共用的 fake docker 慣例見其 `:29, :65-69`）。

### 4.6 `docs/` 同步

- `docs/deploy/auto-review-nickel-enable.md`（第 1-68 行）：此文件描述 `trusted-channel-v1` 政策與「新歌版本都要 Owner 確認一次」（第 25 行）。階段 A 會讓這兩點失效，**文件必須與程式同一個 PR 更新**，否則人工驗收腳本會依過時說明進行。
- `docs/deploy/continuous-deployment.md`（第 37 行「formal exchange 與 claim 由 compose 固定 off」與「主機需要的東西」表）：加入 `youtube_api_key.txt`，並說明 compose 更新是人工步驟。

## 5. 驗證矩陣與 Owner 人工驗收腳本

### 5.1 狀態定義（依 `CLAUDE.md` §6）

| 狀態 | 在本計畫中的意思 | 誰能宣告 |
|---|---|---|
| Passed | 本輪**實際執行**的命令，退出碼 0，且 JUnit 沒有非預期 skip | 執行者，附命令與環境 |
| Failed | 執行了、有測試失敗或退出碼非 0 | 執行者，附失敗輸出 |
| Skipped | 測試被 skip（postgres 沒設定伺服器時的 `pytest.skip`）。**CI 的離線關卡要求零非預期 skip** | 執行者，列出原因 |
| Blocked | 缺工具、缺硬體、缺授權（例如本機沒有 docker）而沒辦法執行 | 執行者，說明缺什麼 |
| Not Run | 沒執行過（包含「歷史上跑過但本輪沒跑」——歷史結果不算） | 預設狀態 |
| Manual Pending | 只能由人在真實環境確認，尚未確認 | Owner／管理者，附日期與觀察 |

**這份計畫本身所有項目目前都是 Not Run**：我沒有執行任何測試，也沒有 docker 可用性的證據。

### 5.2 矩陣

| 驗證項目 | 方式 | 環境 | 目前狀態 |
|---|---|---|---|
| 3.1-3.8 離線測試（疑慮規則、批次、決策、管線、設定、健康、CLI、日誌、部署契約） | `python3 -m scripts.ci.offline`（容器內、`--network none`、`-m "not postgres"`） | Linux 容器（CI 或本機 Docker） | Not Run |
| 3.9 PostgreSQL 18.6 | `python3 scripts/dev/verify-fresh-postgres.py` | 拋棄式 Docker | Not Run |
| 3.9 PostgreSQL 17.11 | `python3 scripts/dev/verify-fresh-postgres.py --postgres-major 17` | 拋棄式 Docker | Not Run |
| 本機單一 PG 18 迴圈（開發用） | `compose.test.yaml`（只有 18，**沒有 17**） | 本機 Docker | Not Run |
| Compose 渲染與 CI 契約 | `python3 -m scripts.ci.runtime_smoke` | Linux、需 `docker compose` | Not Run |
| 完整離線套件在 Linux 容器 | 同第一列；Windows 原生跑 pytest **不算**（`tests/conftest.py` 已說明 Windows 不支援權限位元語意） | Linux 容器 | Not Run |
| 主機腳本（假 docker） | `tests/test_deploy_host_script.py`（含 H-1、H-2） | Linux／Git Bash；需要 bash 與 flock | Not Run |
| **真實 YouTube Data API** 回應形狀（批次 50、`quotaExceeded`、`videoNotFound`） | 只能用真實金鑰 | Google Cloud | Manual Pending（Owner 提供金鑰；本計畫不碰） |
| **Google Cloud 配額**（預設 10,000 單位／日是否仍然成立、是否已有配額警示） | Google Cloud Console | Google Cloud | Manual Pending |
| **Supabase Session Pooler** 對新增 SQL 的相容（交易行為、連線數、statement_timeout、`SET LOCAL`） | 在真實 Supabase 專案以 Session Pooler 跑一次投稿 | Supabase | Manual Pending；拋棄式 PG 17／18 **不能代替** |
| **VM 部署**：新 compose 放置、秘密檔權限、`docker compose up`、healthcheck | 真實主機（GCP VM） | VM | Manual Pending |
| **回滾路徑**（新版不健康 → 換回舊映像） | 真實主機 | VM | Manual Pending（`docs/deploy/continuous-deployment.md` 第 133 行已明說尚未實測） |
| **Discord** `/share`、`/balance`、`/owner list` 卡片顯示原因 | 真實 Discord 伺服器 | Discord | Manual Pending |
| 指令同步（`/owner duration` 等）是否需要 | `python -m bot.command_sync preview`（見 `docs/deploy/auto-review-nickel-enable.md:56-60`） | 真實 Discord | Manual Pending，**若階段 A 沒有新增或改簽名的指令則不需要** |

### 5.3 Owner 人工驗收腳本（草案）

前提：測試帳號兩個（A 為投稿者、B 為對照）；預備一個**自己擁有的**認可頻道影片、一個非認可頻道影片、一個與前者同頻道且長度差 ≤ 3 秒的影片（或同一首歌的 live 版本）。所有步驟都建議在**私人測試伺服器**進行，不用正式社群。

| 步驟 | Owner 做什麼 | 應該看到什麼 | 若不是 |
|---|---|---|---|
| 0 | 確認 `JUKEBOX_AUTO_REVIEW`、`JUKEBOX_AUTO_SETTLEMENT` 都是 off，備份資料庫 | 沒有變化 | 停止 |
| 1 | 用 CLI 小工具取得自己頻道的 ID：`python -m bot.owner_cli channel-id <影片網址>`（命令名稱待定） | 只印出一行 `UC…` | 回報給 Ken／Emily |
| 2 | 把該 ID 貼進 `/etc/jukebox101/bot.env` 的 `YOUTUBE_TRUSTED_CHANNEL_IDS`，放入 `youtube_api_key.txt`（0400、10001:10001），更新 `compose.cd.yaml` | 檔案就位 | 見 4.5 |
| 3 | 推一個空 commit 讓 CD 部署（同一 run 不能重送，見 `docs/deploy/continuous-deployment.md` 第 120 行） | Actions 的 Release 綠燈；Issue #7 通知 `succeeded` | `rolled_back` 或 `rejected`：看通知與固定句子 |
| 4 | 在主機執行 `docker inspect` 看 Health | `healthy` | 看 `python -m bot.healthcheck` 輸出的問題碼 |
| 5 | **只開審核**：`JUKEBOX_API_RETENTION=on`、`JUKEBOX_AUTO_REVIEW=on`，結算維持 off，再部署 | healthcheck 仍 `ready`（不會因為「開了自動審核」就變不健康） | 若 not_ready，把輸出給 Ken |
| 5.5 | **既有首筆案件（`discord:1565766654502113430`，「XG - HYPNOTIZE (Official Music Video)」）的版本處理**：Owner 決定「確認版本」，**在 Discord 操作，不是改資料庫**：`/owner list` 開啟該案卡片 → 看過影片 → 按「確認為新的錄音版本」。這是官方 MV，MV 與音源在版本類別視為同一錄音，不需要另標音源版 | 卡片換成確認結果；該案不再擋住後續投稿（版本未知的較早投稿會擋住所有較晚投稿）。確認後該案依一般流程等待發獎，所以**必須在步驟 11 開自動發獎之前完成** | 若影片內容與案件不符，不要按，回報 Emily。本步驟實際結果為 Manual Pending，我不能替 Owner 執行 |
| 6 | 帳號 A 在 Discord 投稿「認可頻道、無疑慮」的影片 | 私訊回覆「審核通過／待 Owner 確認版本」或（若階段 A 版本也自動確認）「符合首次獎勵資格」；`/balance` **仍是 0**（結算還沒開） | 若 `/balance` 已經 +1，立刻關閉開關並回報（這是嚴重問題） |
| 7 | 帳號 A 投稿「與上一首同頻道、長度相近」的影片 | 回覆「待審」；`/owner list` 出現一張卡片，**顯示疑慮原因**（例如「與既有歌曲同頻道且長度相近」），不顯示其他人的歌名；A 的 `/balance` 不變 | 卡片沒有原因、或顯示他人歌名：回報 |
| 8 | 帳號 A 投稿 live／remix 版本的影片 | 回覆「待審」，卡片原因為版本疑慮 | 同上 |
| 9 | 帳號 B 投稿「非認可頻道」影片 | 待審，原因為頻道未認可 | 同上 |
| 10 | 暫時把 `YOUTUBE_TRUSTED_CHANNEL_IDS` 清空，再部署，A 再投稿一首新歌 | **不自動通過**，進入待審 | 若自動通過：嚴重問題，立刻關閉 |
| 11 | 開結算：照 `docs/deploy/auto-review-nickel-enable.md:55`（停 Bot → `settlement-switch preview` → `write` → 設 `JUKEBOX_AUTO_SETTLEMENT=on` → 啟動 → healthcheck） | `bot.healthcheck` 為 ready | 若 `SETTLEMENT_*` 問題碼，依手冊處理 |
| 12 | A 投稿全新、無疑慮的歌曲 | 回覆「已發放 +1 Nickel」；`/balance` +1；`/history` 顯示「已發放 +1」；重複按同一個投稿不會再發 | 若沒發、或發兩次：立刻關結算並回報 |
| 13 | 把 Google Cloud 配額暫時調到很低（或用已知用盡的金鑰）再投稿 | 回覆「待審」或「稍後重試」（依 D2），**不是被拒絕**，`/balance` 不變 | 若被拒絕或被通過：回報 |
| 14 | 關閉：停 Bot → 開關改 off → `settlement-switch` off → 啟動 | healthcheck ready；投稿回到不自動處理 | |

Owner 一眼可確認的「成功畫面」：`/owner list` 的卡片、`/balance` 的 +1、`/history` 的「已發放 +1」、Issue #7 的 `succeeded` 通知。

## 6. 風險與待確認事項

### 6.1 風險（依嚴重度）

| 編號 | 風險 | 類型 | 緩解 |
|---|---|---|---|
| R1（**最大**） | 版本自動確認 = 系統自己滿足發獎前提之一。官方資料與認可頻道都不能證明錄音版本（`docs/deploy/auto-review-nickel-enable.md:25`）。若疑慮規則有漏洞（例如 cover 沒帶關鍵字、同一首歌換頻道上傳），系統會自動發點數給重複或錯誤的版本，且已發出的點數要走補償流程才能追回 | 事實（既有文件）+ 推測（漏洞形態） | 先 `AUTO_SETTLEMENT=off` 只開審核觀察一段時間；Owner 對「自動確認的版本」抽查；G9 與 A10 鎖住 fail closed；D3 取得明確簽核 |
| R2 | 「不確定一律不通過」的實作要靠大量邊界測試；任何一條路徑回傳 `None`／空清單被誤當成「無疑慮」都會自動通過（S6、S11、A9、A10） | 推測 | 測試一律以「缺資料 = 不通過」寫；加屬性測試（隨機刪除輸入欄位，結果不得變成核准） |
| R3 | 配額：每筆新投稿 11 單位；以預設 10,000 單位／日估算，約 900 筆投稿即耗盡（**單位數是依 1 單位／次與 500÷50=10 批推算，未用真實 API 驗證**）；每輪 20 次上限（`bot/auto_pipeline.py:22`）可能讓重查在一輪內做不完 | 推測 | P3 鎖住不超額；D4 決定排隊或轉待審；Manual Pending 驗證真實配額 |
| R4 | `compose.cd.yaml` 不會自動更新（`install-host.sh:30`）；缺秘密檔可能造成「停舊 Bot 後才失敗」的短暫停機 | 事實 + 推測 | 4.5 的人工步驟順序與 H-1 |
| R5 | `runtime_smoke.py:41` 的「剛好 3 個秘密」契約被改動，若寫成放寬（例如「至少 3 個」）會讓日後任何秘密都能被掛進去 | 推測 | D-2 要求「剛好 4 個且名稱固定」 |
| R6 | 「日誌不含標題／頻道」與「稽核歷程目前寫入 `channel=<ID>`」（`bot/auto_review.py` 的 `_audit`）的範圍不明確 | 事實 + 待決 | 本計畫的測試只涵蓋日誌與健康輸出、回覆文字；稽核列是否可含頻道 ID 需 Owner 依 YouTube 政策（`docs/deploy/auto-review-nickel-enable.md:67` 提到 30 日刷新／刪除）決定 |
| R7 | 重查既有歌曲的官方數值是否算「保存」：需求寫「不保存」，但 API 呼叫過程中數值必然進入記憶體。G6 只能證明沒寫入資料庫，不能證明記憶體中不殘留 | 事實 + 推測 | 記憶體處理期限定在單次函式範圍，不放進模組級快取（可加測試 `test_no_module_level_cache_of_recheck_results`） |
| R8 | PostgreSQL 17 與 18 的測試通過，不代表 Supabase Session Pooler 通過（拋棄式伺服器為直連；本機 `compose.test.yaml` 只有 18） | 事實 | 第 5.2 節標為 Manual Pending；G12 只能降低風險 |
| R9 | 離線計數下限（1747，`scripts/ci/offline.py:29`）與 PG 下限（35，`verify-fresh-postgres.py:114`）不會自動跟著新增測試調高，新測試可能被悄悄跳過 | 事實 | 實作 PR 同步調高下限並說明 |
| R10 | 本機是 Windows，**未必有 docker**；我沒有驗證。完整離線套件與 PG 測試必須在 Linux 容器跑，Windows 原生 pytest 的結果不算驗收（權限位元測試在 Windows 無意義） | 事實 + 推測 | 在 CI 或有 Docker 的 Linux 環境執行 |
| R11 | 目前 CD 對應的 VM 與 GitHub 設定（`CD_ENABLED=true`、舊 Hostinger 已撤銷）在文件中有記載，但**我未實際確認**這些外部狀態；階段 A 合併後 CD 會立刻對 VM 部署新映像（雖然自動開關預設 off） | 事實（文件記載）+ 未驗證 | 合併前先確認 `CD_ENABLED` 狀態，必要時暫停（`docs/deploy/continuous-deployment.md:114`） |

### 6.2 待確認事項（需 Emily／Owner 回覆）

1. **D1**：歌名相似度的演算法與門檻，以及中日文標題的處理。
2. **D2**：額度用完／API 失敗時，是進「待審」還是「稍後重試」？兩種情況（新投稿本身查不到 vs. 重查既有歌曲查不到）是否分開處理？
3. **D3**：版本自動確認是否需要獨立開關？系統身分（actor 0）是否可寫版本確認？抽查機制由誰負責？
4. **D4**：配額與每輪上限；未做完的重查要排隊還是轉待審。
5. **版本關鍵字清單**：S12 的詞表由 Owner 核定（建議含中日文），需要版本控管（改動清單 = 改動政策版本）。
6. **允許清單為空時是否應視為不健康**（HC6）：我建議視為健康但啟動時警示。
7. **YouTube 秘密檔方案**：X1／X2／X3（4.1）。
8. **H-1**：是否要在主機腳本加「缺金鑰先拒絕、不停舊 Bot」的檢查。
9. **PostgreSQL 17 本機迴圈**：`compose.test.yaml` 只有 18；是否要加 17 的本機選項，或僅靠 CI 的 `--postgres-major 17`。
10. **稽核歷程可否寫入頻道 ID**（R6）。

### 6.3 我沒有做的事

沒有改任何既有檔案；沒有寫實作或測試程式；沒有執行 pytest、docker、compose；沒有連線 VM、資料庫、Discord、YouTube；沒有安裝套件；沒有 commit 或 push。唯一的產出是這份文件。

## 7. Owner 決策更新（2026-11-02）與階段 B、C 測試草案

仍只是文件。以下測試與模組名稱皆為 [推測]，實作者可調整，語意不變。

### 7.1 已納入的 Owner 決定與其狀態

| 決定 | 狀態 | 在本計畫的位置 |
|---|---|---|
| 首筆案件（官方 MV「XG - HYPNOTIZE (Official Music Video)」）：Owner 決定「確認版本」，在 Discord 案件卡片（`/owner list` →「確認為新的錄音版本」）操作；MV 與音源視為同一錄音，不需另標音源版；須在開自動發獎之前完成 | Owner 已決定；實際執行為 Manual Pending | 5.3 步驟 5.5 |
| 14 天保存與 API 即時重查 | **Owner 已正式核可（B1）** | 3.9 的 G6、G7；天數用具名常數 `RETENTION_DAYS = 14` |
| 階段 B：`/exchange` 失敗與 DM 失敗都退點；卡住案件 Owner 自己看 | Owner 已決定 | 7.2 |
| 階段 C：`/daily` 使用者自訂送達時間；整點、預設 20:00 Asia/Taipei；Nickel 不足發私訊不扣點；推薦者半遮蔽 | Owner 已決定 | 7.3 |

### 7.2 階段 B 測試草案：失敗退點

**[事實] 既有基礎**：`tests/test_exchange_refund.py` 已涵蓋「證明未送出才退、只退一次、重複退點不變、缺原始扣款即停、失敗整筆回滾、退後歌曲可再預留、已送達不退」（`:46-149`）；`tests/test_exchange_restart_recovery.py` 涵蓋重啟後單次送達與不確定狀態不重送（`:47-142`）。目前規則是「**未證明未送出就不退**」（`:78`）。Owner 新決定「DM 失敗也退點」會改變這個契約：Discord 回報失敗但訊息其實已送達時，使用者會同時拿到歌與點數。這是需要 Emily／Owner 明確承擔的取捨，測試要把語意寫清楚，不能悄悄放寬 `:78` 的既有測試。

| # | 檔案::測試名 | 輸入 | 預期 |
|---|---|---|---|
| X1 | `tests/test_exchange_refund_policy.py::test_an_exchange_that_fails_before_any_song_is_reserved_charges_nothing` | 預留階段失敗 | 帳本無扣款、餘額不變，回覆說明「失敗、未扣點」 |
| X2 | `::test_an_exchange_that_fails_after_the_debit_refunds_exactly_one_point` | 已扣 1 點後內部失敗 | 帳本剛好一筆退款指向原扣款；餘額回到原值 |
| X3 | `::test_a_dm_failure_refunds_exactly_one_point_and_releases_the_song` | DM 回 403 或無法私訊 | 剛好退 1 點、歌曲釋回，回覆說明「無法私訊，已退點」 |
| X4 | `::test_resending_the_failure_does_not_refund_twice` | 同一失敗事件重放 3 次（重送、重啟、確認遺失） | 退款列恆為 1；`ledger_integrity` 通過（`bot/ledger_integrity.py`） |
| X5 | `::test_concurrent_failure_handlers_refund_once` [postgres] | 兩執行緒同時處理同一失敗（手法同 `tests/test_postgres_auto_pipeline.py:237`） | 退款剛好 1 筆 |
| X6 | `::test_a_delivered_exchange_is_never_refunded` | 已成功送達 | 不退（既有 `tests/test_exchange_refund.py:149` 維持） |
| X7 | `::test_an_uncertain_send_is_never_resent_never_refunded_twice_and_is_visible_to_the_owner` | 傳送中重啟、結果不確定 | **待決**：不確定時退或不退。決定前只鎖「不重送、不雙退、Owner 可見」 |
| X8 | `::test_a_refund_with_a_missing_or_foreign_original_debit_stops_and_is_visible_to_the_owner` | 既有 `:93`、`:108` 情境 | 不退、不修補，進 Owner 可見清單 |
| X9 | `::test_refund_failure_rolls_back_point_ledger_and_release_together` [postgres] | 退款交易中途故障 | 全部回滾（既有 `:122`），案件標為卡住 |
| X10 | `::test_a_stuck_exchange_is_visible_to_the_owner_with_reason_and_age` | 製造三種卡住案件（預留未完成、退款失敗、送出不確定） | Owner 檢視看得到，含原因代碼與時間；Owner 以外的人看不到；不含無關的他人資料 |
| X11 | `::test_the_stuck_view_is_read_only_and_changes_no_balance` | 開啟檢視 | 零寫入 |
| X12 | `::test_user_reply_claims_a_refund_only_after_a_committed_refund_row` | 退款提交前後各取回覆 | 只有已提交的退款才寫「已退點」（同 `+1` 規則，`tests/test_auto_settings_and_replies.py:113`） |

「失敗退點恰好一次、重送不重複退點、卡住案件 Owner 可見」分別對應 X2-X5、X4、X10-X11。X1-X4、X6-X8、X10-X12 離線（`FakeDatabase`，慣例同既有退點測試）；X5、X9 與帳本完整性用 PG 17.11 與 18.6。

### 7.3 階段 C 測試草案：`/daily` 自訂送達時間

**[事實]** 目前 `daily` 只是開關（`bot/services.py:264`）；`daily_dispatches.dispatch_date` 註明使用 Asia/Taipei 日曆日，且「daily delivery is not implemented」（`bot/database/postgres/0001_initial_schema.sql:113-118`）；`users.daily_enabled` 是布林（`:17`），**沒有送達時間欄位**，所以階段 C 需要新的 migration（受保護變更，需另行核准與備份）。時間一律注入 `clock`，時區用標準庫 `zoneinfo`（`tzdata` 已在 `pyproject.toml:6` 釘選）。

**排程與時間輸入**（`tests/test_daily_schedule.py`）

| # | 測試名 | 輸入 | 預期 |
|---|---|---|---|
| Y1 | `test_the_default_delivery_time_is_20_00_asia_taipei` | 啟用但未指定時間 | 排程 20:00 Asia/Taipei（UTC 12:00） |
| Y2 | `test_each_user_keeps_their_own_hour` | 三人分別 08、20、23 時 | 各自只在自己的整點被選中，不誤送別人 |
| Y3 | `test_only_whole_hours_are_accepted` | 參數化：`20:30`、`20:01`、`20:00:30`、`24:00`、`-1`、`abc`、空字串 | 一律拒絕，回覆說明可用格式，不寫入、不改原設定 |
| Y4 | `test_every_whole_hour_from_00_to_23_is_accepted` | 0-23 | 全接受 |
| Y5 | `test_changing_the_time_does_not_enable_daily_by_itself` | 已關閉者改時間 | 先鎖「不默默開啟」，Owner 若另有規定再改 |

**日界線 Asia/Taipei**

| # | 測試名 | 輸入 | 預期 |
|---|---|---|---|
| Y6 | `test_23_59_and_00_00_belong_to_different_taipei_days` | 2026-11-02 23:59 與 11-03 00:00 Asia/Taipei（UTC 15:59 與 16:00） | `dispatch_date` 分別為 11-02、11-03；00:00 的派送不被「今天已派送」擋住 |
| Y7 | `test_a_user_at_hour_00_is_dispatched_on_the_new_taipei_day_not_the_utc_day` | 設 00:00 | 以 Taipei 日期計 |
| Y8 | `test_a_server_in_another_timezone_does_not_change_the_boundary` | 環境 `TZ` 設 UTC 與 America/New_York | 結果相同 |
| Y9 | `test_every_hour_of_the_year_maps_to_a_fixed_utc_hour` | 掃整年每個整點 | 無夏令時間偏移 |

**每日一次與改時間**

| # | 測試名 | 輸入 | 預期 |
|---|---|---|---|
| Y10 | `test_at_most_one_dispatch_per_user_per_taipei_day` | 同日 sweep 5 次、重啟 2 次 | 該人該日 `daily_dispatches` 僅 1 列，不重送 |
| Y11 | `test_changing_the_time_after_todays_delivery_does_not_deliver_again_today` | 20:00 已送達，20:30 改成 21:00 | 今天不再派送，明天 21:00 派送（**預設語意，需 Owner 確認**） |
| Y12 | `test_changing_the_time_before_todays_delivery_moves_it_to_the_new_hour_today` | 08:00 改成 20:00，當天尚未送 | 今天 20:00 送一次 |
| Y13 | `test_moving_the_time_to_an_hour_that_has_already_passed_today` | 現在 15:00，原 20:00，改成 09:00 | **待決**：今天補送還是明天才送。先寫成「不補送、明天送」並標待確認 |
| Y14 | `test_a_failed_dm_follows_the_stage_b_refund_rule_and_is_visible` | DM 失敗 | 與 X3 一致：不損失點數，該日狀態可被 Owner 看到；當天是否重試待決 |
| Y15 | `test_disabling_daily_stops_future_dispatch_and_keeps_history` | 關閉 | 之後不派送，歷史保留 |
| Y16 | `test_concurrent_sweeps_dispatch_once` [postgres] | 兩行程同時 sweep | 唯一約束保證 1 列 |
| Y17 | `test_a_restart_between_reservation_and_dm_resumes_without_a_second_debit` | 重啟 | 比照 `tests/test_exchange_restart_recovery.py:47` |

**Nickel 不足**

| # | 測試名 | 輸入 | 預期 |
|---|---|---|---|
| Y18 | `test_a_user_with_no_credit_receives_the_not_enough_message_and_is_not_charged` | 餘額 0 | 發出私訊，內容與 Owner 核定文案相同（「Nickel不夠了…快去分享歌曲，賺點Nickel！」，完整文案請 Owner 提供，測試比對常數）；帳本零筆、餘額不變 |
| Y19 | `test_the_not_enough_message_is_sent_at_most_once_per_user_per_day` | 同日多次 sweep | 只發 1 次；是否每日都發待 Owner 決定 |
| Y20 | `test_a_failed_not_enough_dm_is_not_retried_in_a_loop_and_charges_nothing` | DM 403 | 不重試風暴、不扣點、不影響他人 |
| Y21 | `test_no_candidate_song_keeps_the_credit_and_explains` | 有點但無可推薦歌曲 | 保留點數並說明（`CLAUDE.md` §9 §2） |
| Y22 | `test_a_user_who_cannot_receive_dms_is_skipped_without_blocking_others` | 一人關閉私訊 | 其餘人照常 |

**推薦者半遮蔽**（`tests/test_recommender_masking.py`、`tests/test_daily_dm_content.py`）

遮蔽規則的精確定義 Owner 尚未提供。以下先固定一個**提案規則**供確認：保留首個字素，其餘以 `*` 取代；1 字元全遮蔽；2 字元至多顯示 1 字元；以 Unicode 字素計算，不以位元組或碼位計算；遮蔽段長度固定上限，避免洩露真實長度。

| # | 測試名 | 輸入 | 預期（依提案，待確認） |
|---|---|---|---|
| M1 | `test_a_long_name_keeps_only_its_first_character` | `Alexander` | 只含首字元與遮蔽字元，不含完整名稱 |
| M2 | `test_a_one_character_name_is_fully_masked` | `A` | 全遮蔽 |
| M3 | `test_a_two_character_name_never_reveals_both_characters` | `Al` | 至多顯示一字元 |
| M4 | `test_an_empty_or_missing_name_has_a_safe_placeholder` | 空字串、None | 固定占位文字，不拋例外 |
| M5 | `test_an_emoji_is_never_split_into_broken_code_points` | 開頭為笑臉 emoji 的名稱、ZWJ 家庭序列、旗幟序列、帶膚色修飾 | 輸出為合法 Unicode，無孤立代理字元或半截 ZWJ 序列 |
| M6 | `test_cjk_and_mixed_width_names_are_masked_by_character` | `小明`、`王大明`、全形英文 | 依字素遮蔽，全半形一致 |
| M7 | `test_combining_characters_stay_with_their_base` | e 加組合重音 | 不被拆開 |
| M8 | `test_masking_is_deterministic_and_does_not_leak_length` | 同名多次；`Al` 與 `Alexandria` | 輸出可重現，遮蔽段長度不隨真實長度線性洩露 |
| M9 | `test_mention_syntax_and_newlines_are_neutralised` | `<@123456789012345678>`、`@everyone`、含換行 | 不產生可點擊提及，不含原字串 |
| M10 | `test_the_dm_text_never_contains_the_full_recommender_name` | 以不可能巧合的名稱標記跑完整 daily 與 `/exchange` 路徑 | DM 文字、embed、`repr`、日誌（`caplog`）都不含完整名稱，也不含 Discord user id |
| M11 | `test_the_dm_contains_a_verified_youtube_url_only` | Spotify 來源歌曲 | 只送出已驗證的 YouTube 連結（`CLAUDE.md` §9 §2） |

### 7.4 驗證矩陣補充與新增風險

| 驗證項目 | 方式 | 狀態 |
|---|---|---|
| 7.2、7.3 離線測試 | Linux 容器 `-m "not postgres"` | Not Run |
| X5、X9、Y16 並行與回滾 | PG 17.11 與 18.6 | Not Run |
| 真實 Discord DM（成功、403、關閉私訊）、整點是否準時、emoji 實際顯示 | 真實 Discord | Manual Pending |
| 使用者設定時間的指令簽名變更與指令同步 | `python -m bot.command_sync preview` | Manual Pending；簽名變更會改指令 digest，依 `docs/deploy/auto-review-nickel-enable.md:56-60` |
| 14 天保存與 API 即時重查 | Owner 已核可；真實 API 與真實保存行為仍須人工驗證 | Manual Pending |

新增風險：

- **R12（高）**：「DM 失敗也退點」可能在 Discord 實際已送達時造成雙得，與既有「未證明未送出就不退」衝突。建議 Owner 以文字明確接受，並由 X7 限定不確定情境。
- **R13**：送達時間欄位需要 migration（受保護變更），需獨立核准與備份；migration 測試要在 17 與 18 都跑。
- **R14**：預設都是 20:00，會有尖峰；Discord 速率限制可能延遲送達（`docs/rest-429-review.md` 未讀）。需要批次與間隔，並補「大量同時」的有界行為測試（容量待決）。
- **R15**：半遮蔽規則未經 Owner 定義，M1-M9 的預期值是提案，核定前不是通過標準。

### 7.5 待確認事項（補充）

11. 退點：DM 結果不確定時退或不退（X7）；Discord 已送達卻回報失敗的雙得風險是否接受（R12）。
12. 改時間後當天行為（Y11、Y13）；改時間是否隱含開啟 daily（Y5）。
13. 不足 Nickel 私訊的完整文案，以及是否每日只發一次（Y18、Y19）。
14. 半遮蔽的精確規則與長度策略（M1-M9）。
15. （已決）14 天保存已核可；文件中的 30 日是政策上限與資料庫護欄，不是保存天數。

### 7.6 後續決定與特徵測試（Owner 決定彙整）

| 項目 | 決定／狀態 | 對測試與文件的影響 |
|---|---|---|
| 啟用順序 | 先開自動審核與保存並用真實 API 驗證 → 處理首筆案件 → 才開自動發獎 | `docs/deploy/stage-a-enable-rollback.md` 第 3 節已改；驗收腳本 5.3 的步驟順序相同 |
| 認可頻道清單 | 目前 1 個：`UC12HMtO5MYph9dCZZ7yygng`，尚未寫入 VM 的 `bot.env`；空白時沒有歌自動通過的安全規則不變 | 既有 A2、`tests/test_auto_pipeline_version_confirm.py` 空清單案例鎖定；健康檢查視為 ready |
| migration 0004 套用到正式庫 | **需 Owner 另行明確授權** | 啟用清單先決條件已註明；本計畫不執行 |
| 「高度相同」 | 採方案 X：同頻道、時長差 ≤ 3 秒、歌名高度相似**三者全成立** → 轉 Owner 審，卡片寫「高度相同，建議不發放」，**不新增狀態**（不是拒絕，也不是新的審核狀態）。方案 Y（新增「重複不發放」狀態）的測試草案已移除，不再追蹤 | 卡片文字的測試待 Sean 實作後補：三條件全成立時文案出現，只成立一或兩條時不出現，且文案不含其他歌曲的標題 |
| 資料只存影片 ID 與原始連結 | Owner 要求 | 新增 `tests/test_postgres_auto_version_confirm.py::test_new_data_holds_only_ids_and_links_never_a_title_a_channel_or_an_official_duration`：短期觀測表以外的所有表，不含標題、頻道 ID、官方時長 |
| 既有裁決的特徵測試 | Sean 的 (b)(d) | 新增 `tests/test_postgres_owner_rulings.py`（5 個，PostgreSQL 18.6 已通過）：確認不同版本 → CONFLICT 凍結且 Owner 動作清單中沒有解除動作；已拒絕案件經重審 → 核准 → 版本確認 → 結算，恰好發一次、重放不重複；重審核准的案件排在已在等待的投稿之後 |
| 交換語意（階段 B） | Owner 澄清：「交換」是給對方資料庫中隨機一首歌的連結，**不是把歌移出資料庫**；仍排除自己的投稿與已收過的歌 | 7.2 的 X 系列與選曲測試：交換前後歌曲仍在資料庫、他人仍可收到同一首；同一人不會收到自己投稿或已收過的歌；歌曲池不因交換變小 |

**已知待處理（不在階段 A 範圍）**：`bot/share_replies.py:59` 在重審核准過的案件重放同一 operation 時，因 accepted_sequence 已變為新的尾端序號而拋 `ValueError`。發獎不受影響，建議另開票。

### 7.7 回滾單向門與文案測試（2026-11-02）

- 回滾實測結果與唯讀檢查 SQL 寫在 [階段 A 啟用與回滾清單](deploy/stage-a-enable-rollback.md)：以基底提交 `51ee43a` 的程式讀取含 `system_judgement` 證據的資料庫，只有「對該影片的 Owner 檢視／確認版本／重審」會 `ValueError`，投稿、待處理清單、結算與餘額不受影響。自動測試：`tests/test_postgres_system_evidence_rollback.py`（5）、`tests/test_codec_unknown_enum_value.py`（3）。
- 卡片與回覆文案的紅燈測試：`tests/test_postgres_concern_wording.py`（10）。文案是具名常數 `NEAR_DUPLICATE_NOTE`（「高度相同，建議不發放」）、`SIMILAR_REVIEW_REPLY`（「已轉交審核，高度相似，尚未發放」），Owner 要改字只改常數與對應測試的常數值。
