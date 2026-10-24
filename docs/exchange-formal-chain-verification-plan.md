# 正式 /exchange 呼叫鏈隔離驗證計畫（規劃，2026-10-24）

**性質**：唯讀審閱與計畫文件。**本文件不授權任何執行。** 本輪沒有修改功能或測試，沒有執行測試，沒有啟動或連線 MySQL，沒有解密憑證，沒有操作 Discord，沒有 commit 或 push。承接的 checkpoint 為 `0909ebf`（`exchange: preserve compensation guards when disabled`）。

**重要結論**：目前程式中**沒有**可供使用者呼叫的正式 `/exchange` 交換流程。以下第 1 節說明實際狀態，第 5 節列出需要 Owner 決定的一個問題。

## 1. 實際呼叫鏈

### 1.1 正式路由（目前的 production 行為）

```
Discord /exchange
  -> bot/discord_app.py  handlers.exchange(interaction)                     [line 114]
       if service.synthetic_exchange_enabled is not True:
           -> bot/services.py  MusicService.exchange(discord_user_id)       [line 157]
              回覆固定文字「交換功能待歌曲資格驗證與派送恢復流程完成後開放；本次未扣點。」
              不扣點、不建立保留、不派送 DM。
```

`MusicService.exchange` 是一個固定回覆，沒有呼叫 repository，也沒有 DM。**這就是正式 `/exchange` 目前實際做的事。**

### 1.2 Synthetic 驗收路由（明確開啟才會進入，非正式產品）

```
Discord /exchange（只有 MusicService(synthetic_exchange=True) 且 submission_repository 存在才進入）
  -> handlers.exchange
       interaction.response.defer(ephemeral=True)
       DiscordDirectMessageSender(interaction.client)   [Discord 外部邊界]
       DiscordEphemeralReplier(interaction)              [Discord 外部邊界]
  -> bot/services.py  MusicService.synthetic_exchange(uid, request_key, sender, replier)
       -> bot/exchange_delivery.py  exchange_and_deliver(submission_repository, labelled sender, labelled replier, uid, key, Random(), now)
            1. repository.recover_interrupted_deliveries(now)     [租約回收]
            2. repository.retry_pending_refunds(now)              [退款重試]
            3. _exchange_with_one_replay:
                 repository.exchange(uid, key, rng)  [asyncio.to_thread]
                   -> bot/database/submission_repository.py  SubmissionRepository.exchange
                   -> submission_transaction (writer gate, jb2_control FOR UPDATE)
                   -> bot/database/exchange_reservation.py  reserve_exchange
                        replay -> guarded 判斷（開關或三表存在）-> require_schema
                        -> pending_block -> lock_reconciled_account -> 候選抽選 -> _reserve（一次扣點 + 保留）
                 CommitOutcomeUnknown 時，同 key 只重試一次
            4. 依 status 分流：NO_CANDIDATE / INSUFFICIENT_CREDITS / PENDING_REVIEW 直接回覆，不送 DM
            5. deliver_recommendation(repository, sender, recommendation_id, now, token)
                 -> repository.claim_delivery   （守衛：開關或三表存在）
                 -> _attempt: sender.send(uid, "你的交換推薦：<link>")，逾時或例外 -> DmResult.unknown
                 -> repository.record_delivery  （delivered / failed / uncertain）
            6. _refund_if_unsent：只有明確 not_sent 才退款
            7. replier 回覆（不含連結）
       -> 回傳時加上 SYNTHETIC_LABEL
  -> 例外映射：CommitOutcomeUnknown、MySQLError、PersistenceNotReady 各自有固定回覆文字
```

### 1.3 外部邊界

| 邊界 | 實際位置 | 隔離時的替身 |
| --- | --- | --- |
| Discord interaction（defer、response） | `bot/discord_app.py` | 不進入 `handlers`，直接呼叫 `MusicService.synthetic_exchange` 時不需要 |
| DM adapter | `DiscordDirectMessageSender` | `FakeSender`（`tests/test_exchange_unknown_commit.py`），回傳 DmResult |
| Ephemeral 回覆 | `DiscordEphemeralReplier` | 記錄型替身 |
| 資料庫 | `SubmissionRepository` 與 PyMySQL | 隔離 MySQL 3307 真實連線 |

## 2. 現有覆蓋對照

| 層級 | 已有測試 | 環境 | 涵蓋內容 | 缺口 |
| --- | --- | --- | --- | --- |
| Discord 指令註冊與路由 | `test_discord_app.py`、`test_discord_exchange_adapter.py` | 離線，mock interaction | 路由只在明確 True 時進入 synthetic；分類 Discord 例外 | 不涵蓋真實 Discord |
| 服務層 wrapper | `test_services.py`、`test_exchange_synthetic_acceptance.py` | 離線，fake repo | 正式路由固定回覆；synthetic 映射與標示 | 不涵蓋真實 DB |
| 派送程式（caller） | `test_exchange_delivery.py` | 離線，fake repo | 成功、明確 not_sent 退款一次、unknown 不退款不重送、adapter 例外為 unknown、租約回收、重放不再送、回覆不含連結 | **只在離線 fake** |
| 派送 + 未知提交 | `test_exchange_unknown_commit.py` | 離線 | 未知提交同 key 重放一次、不二次扣點 | 只在離線 |
| 派送 + 未知提交（實機） | `test_unknown_commit_mysql.py`（4 項） | **MySQL 3307** | lost ack、commit 前失敗、語句失敗、caller 重放一次並送一則 DM（fake adapter） | 不含 DM 失敗、DM unknown、正常路徑、PENDING 回覆 |
| Repository 交換（實機） | `test_exchange_repository_mysql.py`（S1 6 項）、`test_exchange_cross_process_mysql.py`（S2 4 項） | **MySQL 3307** | 首次 RESERVED、同 key 重放、零餘額、NO_CANDIDATE、並行、跨程序閘門 | 不經 caller、不經 DM |
| 補償守衛（實機） | `test_compensation_mysql.py`（23 項，含 m13–m15） | **MySQL 3307** | 阻擋、同 key 重放、uncertain 持有、not_sent 釋放、關閉開關後保護 | 只到 repository，不經 caller 與 DM |
| 補償守衛（離線） | `test_compensation_exchange_guard.py`、`test_compensation_claim_guard.py` 等 | 離線 | 缺表、部分缺表、schema 不相容、探測失敗 | 表不存在的 MySQL 情境未實機（需 DROP，未授權） |

**小結**：**派送邏輯的各種結果（成功、明確未送、未知、重放）目前只在離線 fake 層完整覆蓋。** 資料庫層的交換與補償守衛已在實機驗證。**兩者之間（caller 經真實資料庫、接真實 DM 替身）只有 unknown-commit 的 4 項實機案例，缺少 DM 失敗與 DM 未知的實機版本。**

## 3. 能否在隔離環境直接走正式流程

- **正式 `/exchange`：不能。** 正式路由回覆固定文字，沒有呼叫交換鏈。
- **Synthetic 路由：可以，且不需要另寫簡化流程。** 測試可直接建立 `MusicService(repository, submission_repository=SubmissionRepository(真實工廠), synthetic_exchange=True)`，然後呼叫 `synthetic_exchange(uid, key, fake_sender, fake_replier)`。這會走 `exchange_and_deliver` 的完整路徑，只替換 DM 與回覆兩個外部邊界。
- **需要確認的實作細節**：`MusicService.__init__` 需要 `repository: MusicRepository` 參數，synthetic 路徑不使用它。實作時可傳入與既有測試相同的替身，但不得讓 synthetic 路徑依賴它。
- **Discord handler 本身**：可用既有的 `test_discord_exchange_adapter.py` 離線覆蓋路由選擇。隔離實機不需要重做，除非 Owner 要求。

## 4. 最小必要案例（只補實際缺口）

以下案例都走 `exchange_and_deliver`（synthetic 路由的核心），資料庫為真實 MySQL，DM 與回覆為替身。**資料庫提交未知（已由 `test_unknown_commit_mysql.py` 覆蓋）與 DM 送達未知是不同的事件，本計畫分開記錄，不合併判定。**

| 編號 | 案例 | 預期 | 目前缺口 |
| --- | --- | --- | --- |
| C1 | 正常交換，經 `MusicService.synthetic_exchange` | 一次扣點、一則 DM（連結正確）、回覆有 `SYNTHETIC_LABEL` 且不含連結、保留為 delivered、餘額 0 | 服務層與 caller 在實機 DB 上都沒有覆蓋 |
| C2 | 未結案事故阻擋（開關關閉、三表存在） | 回覆 PENDING_REVIEW 文字、**不送 DM**、**不扣點**、保留數與餘額不變 | caller 層與服務層的實機案例缺；repository 層已由 m13 覆蓋 |
| C3 | 同 key 重放（經 caller，無未知提交） | 第二次回覆與第一次相同保留、**不送第二則 DM**、不二次扣點 | 只有 lost-ack 路徑（未知提交）覆蓋過重放，正常重放在 caller 層缺 |
| C4 | DM 明確失敗（`DmResult.not_sent`） | 狀態 failed、**退款一次**、歌曲釋放、回覆提示未送出 | 只在離線 fake 覆蓋 |
| C5 | DM 送達未知（adapter 例外或逾時） | 狀態 uncertain、**不退款**、**不重送**、餘額維持扣點後狀態、歌曲持有、回覆不要求重新執行 | 只在離線 fake 覆蓋 |

**不需要新增的案例**（已有實機或離線覆蓋）：資料庫提交未知與同 key 重放（`test_unknown_commit_mysql.py` 4 項）、補償阻擋與釋放的 repository 層（m13–m15）、閘門並行（S1、S2）。

**關閉開關後既有保護**：由 C2 的「開關關閉、三表存在」情境涵蓋，不另開案例。

## 5. 需要 Owner 決定的一個問題

**是否以 synthetic 路由作為「呼叫鏈」的實機代理驗證？**

- **現況**：正式 `/exchange` 沒有可執行的交換流程，只有明確標示為 synthetic 的驗收路由會走完整鏈。
- **選項 A（建議）**：以 synthetic 路由完成 C1–C5 的實機驗證，並在文件中清楚標示「正式 `/exchange` 尚未開放，本驗證不代表正式指令已驗證」。這能先把派送與資料庫的連接確認下來。
- **選項 B**：等 Owner 決定正式 `/exchange` 的開放時程與產品規則後，再做正式入口的驗證。這不需要任何實機操作，但會延後驗證。
- **Claude 不會**自行新增指令、開放正式路由或改變 `MusicService.exchange` 的固定回覆。

這是產品範圍的決定，不是技術細節，需要 Owner 回覆 A 或 B。

## 6. 本階段檔案範圍（待批准後實作）

- **新增** `tests/test_exchange_formal_chain_mysql.py`：C1–C5 的 MySQL 案例，標記 `mysql`，無隔離開關時跳過。夾具沿用 `test_compensation_mysql.py` 的 `guarded_steps`（清理前保存證據、只清理合成範圍）。
- **可能新增** 離線測試 `tests/test_exchange_formal_chain_offline.py`：只在 C1–C5 的離線版本尚未覆蓋時補。預設先不新增，避免重複。
- **不修改** `bot/` 功能程式。若 C1–C5 揭露實質問題，停止並回報，不自行修正。
- **新增本地啟動器**（`.local/mysql-test/`，已被 git 排除）：以 `compensation_d2_authorized.py` 為藍本，改為 `exchange_chain_authorized.py`，只跑新測試檔與既有 `test_unknown_commit_mysql.py`。
- **文件**：本檔與 `docs/compensation-mysql-verification-plan.md` 的交接段。

## 7. 一份可一次批准的有限授權草案（尚未批准，不得執行）

**範圍**
1. 在既有隔離實例 `127.0.0.1:3307`、資料庫 `jukebox101_test`、既有 datadir `…\.local\mysql-test\20261024-122528\data`、UUID `f138d7c6-cf62-11f1-87be-c025a5a12877`，以既有帳號 `jukebox101_test_exchange`（DML）執行。
2. 執行新測試檔的 C1–C5，以及既有 `test_unknown_commit_mysql.py`（4 項）作為回歸。
3. 所有 DM 與回覆為替身，不發送真實 DM，不操作 Discord。

**允許**
- 讀取：身分、三表定義（與 B 階段 `schema-after` 比對）、exchange 授權快照（與 A 階段比對）、合成範圍內列數。
- 寫入：只限合成使用者範圍 `900000000000000100–199`、固定標記合成歌曲、以及測試自己建立的保留與派送列。
- 清理：沿用 `guarded_cleanup`（先保存計畫 JSON 並讀回，成功後才刪除，每步計畫列數須等於刪除列數，清理後剩餘 0）。

**禁止**
- DDL、DROP、TRUNCATE、ALTER、增權、新增帳號、GRANT、REVOKE、改密碼、root 作為測試帳號。
- 連線 3306、讀取 `.env`、解密超出既有 `credentials.dpapi` 的內容、列印密碼或憑證。
- 真實 DM、Discord 操作、啟用正式 `/exchange` 或補償功能。
- 修改功能程式。
- commit、push、merge、force push。
- 表不存在的情境（需要 DROP，維持離線驗證）。

**停止條件**（任一發生即停止，保留證據，正常關閉實例）
- 身分、datadir、UUID、版本或 database 不符。
- 三表不存在、部分存在、定義與 B 階段不符，或 exchange 授權快照與 A 階段不符。
- 清理前合成範圍內已有非本輪的列。
- 任何寫入超出合成範圍，或任何 DM 替身以外的外部呼叫。
- 任一案例非預期失敗（不以重跑覆蓋，不改測試斷言）。
- 清理計畫與刪除結果不符。

**證據**
- 新證據目錄 `.local\mysql-test\exchange-chain-<run>\`（不得覆寫既有證據）。
- 內容：身分與基線 JSON、pytest 日誌、清理計畫與結果 JSON、停機報告。
- 證據不提交 git；受追蹤文件只摘要位置與結果。

**停機與殘留檢查**（成功與失敗都執行）
1. root SHUTDOWN，記錄退出碼；只有逾時才強制結束並記錄。
2. 3307 無 Listen。
3. 無 mysqld 指向本 datadir。
4. 無 `tests.exchange_worker_process` 殘留。

**預期結果**：新測試 5 項通過，unknown-commit 4 項通過，合計 9 項。任何 failed 或 error 如實回報，不得修改斷言。

**測試方式**：先離線撰寫 C1–C5 並以 tdd 取得行為紅燈，然後實機紅燈（若行為尚未存在），最後驗證綠燈。**實機紅燈只在行為確實缺失時才需要；若現有程式已正確處理，應直接實機驗證，不得為取得紅燈而破壞功能。**

**Owner 決定**：第 5 節的 A 或 B。選 B 時，本授權草案不執行。

## 8. Skill 與配置

- **tdd**：可用，後續實作時使用（先取行為紅燈，再驗證綠燈）。
- **start-dev-team**：可用，但本輪不建立團隊。若後續需要，先說明配置並取得核准，否則不使用。

## 9. 收尾點

1. 本計畫與授權草案完成（本輪）。
2. Owner 回覆第 5 節 A 或 B。
3. 若 A 且批准授權：離線撰寫 C1–C5 → 實機驗證 → 更新本文件與交接 → 收尾。
4. 收尾後，**精準 commit 與正常 push 需另行批准**，本計畫不授權。

## 10. 剩餘限制

- 正式 `/exchange` 與真實 DM 未驗證，本階段不宣稱。
- 表不存在的實機情境未驗證（需 DROP）。
- 真實網路中斷未驗證（只驗證注入式故障）。
- 補償功能仍關閉；啟用前仍需 Owner 另行決定。

## 8. 實機執行結果：synthetic 呼叫鏈隔離驗證（2026-10-24，已完成，未 commit）

**結論範圍**：本節驗證的是 **synthetic /exchange 呼叫鏈的隔離整合**，即 `MusicService.synthetic_exchange` → `exchange_and_deliver` → 真實 `SubmissionRepository` → 派送程式，只替換 DM 與回覆兩個外部邊界。**不宣稱正式 /exchange 已驗證**（正式指令仍回覆固定「未開放」文字），**也不宣稱真實 DM 已驗證**。

### 8.1 檔案與雜湊

| 檔案 | 狀態 | SHA-256 |
| --- | --- | --- |
| `tests/test_exchange_formal_chain_mysql.py` | 新增（C1–C5） | `8dd3379c10f706513fdcfd8fb9bfa5912e2eeb622b724bf03f2fc389e1d99589` |
| `.local/mysql-test/exchange_chain_authorized.py` | 新增（本地，已排除） | `1d61888294697a5a764bab05e0adccd4083d0d0a2e575cc594948b89e40b0af9` |
| `tests/test_unknown_commit_mysql.py` | 未改，沿用既有 4 項 | `b096bfca…ec8d2` |
| `tests/test_compensation_mysql.py`（夾具來源） | 未改 | `41ac7090…b652b` |

### 8.2 測試替身（外部邊界）

- **DM adapter**：`RecordingSender`，依腳本回傳 `DmResult.delivered / not_sent / unknown`，不接觸 Discord。
- **回覆 adapter**：`RecordingReplier`，只記錄文字。
- 其餘皆為正式物件：`MusicService.synthetic_exchange`、`exchange_and_deliver`、`SubmissionRepository`（真實 MySQL）。

### 8.3 案例與結果（chain2，最終有效結果）

| 案例 | 內容 | 結果 | 性質 |
| --- | --- | --- | --- |
| C1 | 正常交換：一次扣點、一則 DM（含連結與標示）、回覆有標示且不含連結、保留 delivered | **PASSED** | 新增覆蓋（首次即通過） |
| C2 | 補償關閉、三表存在，未結案事故阻擋：回覆 PENDING、不扣點、不送 DM | **PASSED** | 新增覆蓋（首次即通過） |
| C3 | 同 key 重放：不重複扣點、不重送 DM | **PASSED** | 新增覆蓋（首次即通過） |
| C4 | DM 明確未送：退款一次、保留釋放、重放不再退款；釋放後同歌可再保留 | **PASSED** | 新增覆蓋（首次即通過） |
| C5 | DM 送達未知：不退款、不重送、保留歌曲持有（released_at 為空） | **PASSED** | 新增覆蓋（首次即通過） |
| 既有 | `test_unknown_commit_mysql.py` 4 項（資料庫提交未知） | **4 PASSED** | 既有覆蓋，本次實際再執行 |

**合計：9 passed、0 failed、0 error、0 skipped**（26.65 秒，`chain2`）。

**TDD 紀錄（如實）**：C1–C5 在第一次實機執行即全部通過，沒有發現缺陷，因此不製造紅燈。本輪的紅燈僅有一次 setup 錯誤（`fixture 'isolated' not found`），它是測試檔漏了夾具匯入，屬測試基礎設施錯誤，在任何行為斷言執行前即已出現。修正方式：補上 `isolated` 匯入，不改斷言。該次失敗的證據保存於 `exchange-chain-chain1`，不與 `chain2` 合併。

**與歷史結果的區分**：
- `chain2` 的 4 項 unknown-commit 是本輪的實際再執行；與歷史分批結果（第 `4f1e3f1` 的 21/1、路由修正後單項 1 passed）**不合併**。
- 資料庫提交未知（unknown-commit 4 項）與 DM 送達未知（C5）**分開驗證、分開記錄**。

### 8.4 基線、清理與停機

- **基線**：身分一致（port、版本 8.0.46、UUID `f138d7c6…`、datadir、log、database）；三表存在且定義與 B 階段 `schema-after` 一致；exchange 授權快照與 A 階段一致；三表列數為 0；合成範圍外無殘留。未執行 DDL、未增權。
- **清理**：9 組計畫與結果，每步計畫列數等於刪除列數，清理後剩餘 0，所有被刪列都在 `900000000000000100–199`。
- **backdate**：只有 C2 會 backdate：將自己建立的保留 `created_at` 改為一小時前，使其在真實時鐘下成為未結案阻擋。其他案例不改動任何時間欄位。被改動的列由夾具清理。
- **停機**：正常 SHUTDOWN，退出碼 0，未強制；3307 無 Listen；無 mysqld 指向本 datadir；無 worker 殘留。
- **證據位置**：`.local\mysql-test\exchange-chain-chain2\`（`chain-report.json`、`d2-baseline.json`、`pytest-chain.log`、`cleanup\`）。`exchange-chain-chain1` 為 setup 錯誤的歷史證據。

### 8.5 剩餘限制（不得省略）

1. **正式 /exchange 未驗證**：正式指令仍只回覆固定文字；本次只驗證 synthetic 路由。
2. **真實 DM 未驗證**：DM 為替身，沒有任何 Discord 呼叫。
3. **真實網路中斷未驗證**：C5 與 unknown-commit 皆為注入或腳本化結果。
4. **表不存在的實機情境未驗證**：需要 DROP，維持離線。
5. **補償功能仍關閉**：啟用前仍需 Owner 另行決定。
6. C5 未在實機證明「歌曲持有」的重抽結果（餘額為 0，無法再抽）；持有由保留的 `released_at` 為空與狀態 uncertain 證明，重抽行為由離線 `test_exchange_delivery` 與補償 MySQL m14 覆蓋。

### 8.6 交接與精準提交候選

- **新增**：`tests/test_exchange_formal_chain_mysql.py`、`docs/exchange-formal-chain-verification-plan.md`（本檔）。
- **本地（不提交）**：`.local/mysql-test/exchange_chain_authorized.py`、證據目錄。
- **既有工作樹（不屬本階段）**：batch-B、review、decision_snapshot、ledger_integrity、私人 PDF，以及 CRLF-only 的 `test_compensation_repository.py`、`test_compensation_store.py`。不得夾帶。
- **下一步**：另行批准精準 commit 與正常 push。本階段不授權。
