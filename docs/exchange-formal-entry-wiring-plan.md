# 正式 /exchange 入口接線計畫（規劃，2026-10-24）

**性質**：唯讀審閱與計畫文件。**本文件不授權實作、不授權任何執行。** 本輪沒有修改功能或測試，沒有執行測試，沒有啟動或連線 MySQL，沒有操作 Discord，沒有解密憑證，沒有 commit 或 push。承接的 checkpoint 為 `c972f54`。

**結論先行**：正式 `/exchange` 的交換與派送程式已在隔離環境驗證（synthetic 路由，C1–C5 共 9 passed）。接線缺口集中在 **handler 與服務層的一條分支**，以及 **DM 失敗分類的一項既有決定**。產品上，`/exchange` 在目前 Beta 規格中被明確列為「未開放、不納入」，因此**開放本身需要 Owner 決定**；技術接線可以在預設關閉下完成。

## 1. 正式路由：已存在與未實作

| 項目 | 狀態 | 位置 |
| --- | --- | --- |
| Discord 指令註冊 `/exchange` | 已存在 | `bot/discord_app.py:165` |
| handler 分流（strict True 才進 synthetic） | 已存在 | `bot/discord_app.py:114–127` |
| 正式路徑：固定回覆「未開放、未扣點」 | 已存在（現行正式行為） | `bot/services.py` `MusicService.exchange` |
| synthetic 服務入口 `synthetic_exchange` | 已存在（明確 flag 才可用） | `bot/services.py` |
| 交換與派送程式 `exchange_and_deliver` | 已存在並已隔離驗證 | `bot/exchange_delivery.py` |
| 真實 DM adapter `DiscordDirectMessageSender` | 已存在，僅有 mock 測試 | `bot/discord_exchange.py:16` |
| 真實 ephemeral 回覆 `DiscordEphemeralReplier` | 已存在，僅有 mock 測試 | `bot/discord_exchange.py:49` |
| 正式 repository 工廠 | 已存在 | `bot/__main__.py:33–34` |
| **正式交換的服務入口（非 synthetic）** | **未實作** | 缺 |
| **正式路由的獨立開關** | **未實作** | 缺 |
| **handler 的正式分支（defer、交換、回覆）** | **未實作** | 缺 |
| **正式路由的標示與文字（不帶 synthetic 標籤）** | **未實作** | 缺 |

啟動流程（`bot/__main__.py`）只建立 `MusicService(MusicRepository, SubmissionRepository(..., owner_id))`，沒有傳入 synthetic 旗標，因此 synthetic 路由在正式啟動時不會開啟。這是正確的現行狀態。

## 2. synthetic 專用項目：哪些不能帶入正式入口

| synthetic 項目 | 用途 | 正式入口處理 |
| --- | --- | --- |
| `SYNTHETIC_LABEL` 包裝（`_LabelledSender`、`_LabelledReplier`） | 標示測試訊息 | **不帶入**。正式訊息不得含標示 |
| `synthetic_exchange_enabled` 的 strict True 閘門 | 只允許明確 synthetic 路由 | **不沿用**。正式需獨立的開關（見第 4 節） |
| 合成使用者範圍與合成歌曲 | 測試資料 | **不適用**。正式候選來自真實已驗證歌曲 |
| 測試用 `FakeSender`、`RecordingReplier` | 外部替身 | **不適用**。正式使用真實 adapter |
| `CommitOutcomeUnknown`、`MySQLError`、`PersistenceNotReady` 的回覆映射 | 錯誤回覆 | **保留邏輯，改用正式文字** |

**正式路由應保留的檢查**（不得因入口不同而省略）：
- 同 key 重放（`discord:{interaction.id}`，與 synthetic 相同）。
- 提交未知時同 key 只重試一次（`_exchange_with_one_replay`）。
- 恢復中斷派送與退款重試（`recover_interrupted_deliveries`、`retry_pending_refunds`）。
- 派送前 claim，DM 不在交易內，送出後記錄結果。
- 只有明確未送才退款，且一次。
- 回覆不含連結，DM 才含連結。
- 補償守衛（claim 與交換端），與交換開關無關（見第 4 節）。

## 3. 使用者範圍、點數、候選來源、權限、可見性

| 項目 | 目前狀態 | 來源 | 是否需要 Owner 決定 |
| --- | --- | --- | --- |
| 點數成本 | 1 點 | `EXCHANGE_COST`、CLAUDE.md §9 第 2 點 | 否，已定 |
| 候選來源 | 已驗證的 YouTube 歌曲，來自已結算版本；全域池 | CLAUDE.md §9 第 2、3 點；`exchange_selection.py` | 否，已定 |
| 自己的投稿與衝突版本 | 排除 | `reserve_exchange` 的 `blocked` | 否，已定 |
| 使用者範圍 | 目前只限**開發伺服器**（GuildInteractionGuard） | `bot/guild_acceptance.py`、`bot/__main__.py` | **是，需要決定正式開放的範圍**（見 D1 的一部分） |
| 權限 | 一般使用者，只能操作自己的點數 | `docs/private-beta-mvp-specification.md:25`（`/exchange` 角色為「一般使用者」） | 否 |
| 回覆可見性 | ephemeral（僅本人可見）；DM 含連結 | `bot/discord_exchange.py`、`exchange_delivery.py` | 否，已定 |
| 開關 | 目前只有 synthetic flag；正式開關未定義 | 程式 | 否（技術細節，見第 4 節） |

**Beta 範圍的既有決定**：`docs/private-beta-mvp-specification.md:25` 寫明 `/exchange` 目前「僅回覆未開放、未扣點；保留停用說明，真實交換暫不納入」。`ROADMAP.md:29` 也寫明 `/exchange` 維持 Pending。**這與 CLAUDE.md §9 的 MVP 行為描述不同，哪一份決定生效需要 Owner 確認**（見 D1）。

## 4. 正式交換與補償開關的分離

建議兩個獨立開關，預設皆關閉：

| 開關 | 控制 | 預設 | 與另一個的關係 |
| --- | --- | --- | --- |
| **正式交換開關**（建議名 `formal_exchange`，由部署設定傳入） | `/exchange` 是否走交換與派送程式 | 關 | 只控制交換入口，**不**啟用任何補償業務 |
| **補償開關**（既有 `compensation_enabled`） | owner 補償操作（核准、結算、補送、結案） | 關 | 只控制補償業務，**不**改變交換入口 |

**既有守衛不因補償關閉而失效**（已由前幾階段驗證）：
- 三表存在時，claim 守衛與交換端的阻擋、歌曲排除依舊生效（`guarded = compensation_enabled or compensation_tables_present(cursor)`）。
- 補償表不存在且補償關閉時，交換正常，不查不存在的表。
- 補償表部分缺漏、schema 不相容、探測失敗時，明確失敗，不吞錯放行。

因此正式交換開啟時，補償守衛的行為**不會改變**。反過來，補償開關關閉也不會關閉正式交換。

## 5. 真實 DM adapter 的分類能力

| 情況 | 目前分類 | 理由（程式註解與測試） | 是否可安全退款 |
| --- | --- | --- | --- |
| 送出成功，取得 message id | `delivered` | 測試：`test_an_accepted_dm_is_delivered_with_discord_message_identifier` | 不退款（已送達） |
| 查詢收件人失敗（`fetch_user` 例外） | `not_sent` | 只做 GET，尚未嘗試送訊息 | **可退款**（已證明未送） |
| 送訊息時 403，error code 50007 | `unknown` | 測試：`test_a_refusal_at_send_time_is_uncertain_even_with_error_code_50007` | **不退款**（既有決定，見 D2） |
| 送訊息時其他 403 | `unknown` | 測試：`test_other_forbidden_errors_are_uncertain_and_never_refunded` | 不退款 |
| 速率限制、逾時、連線中斷、其他例外 | `unknown` | 測試：`test_server_rate_limit_timeout_and_connection_failures_are_uncertain` | 不退款 |
| 逾時（`DM_TIMEOUT_SECONDS=20`） | `unknown` | `bot/exchange_delivery.py::_attempt` | 不退款 |
| 同一次呼叫只送一次 | 是 | 測試：`test_the_adapter_never_sends_twice_for_one_call` | — |

**可區分的情況**：成功、查詢失敗（明確未送）、送出失敗（未知）。**不能區分**：送出時的明確拒絕（403／50007）與送達未知。

**缺口**：
1. **沒有經真實 Discord 驗證**，目前只有 mock 測試。
2. 50007 與其他 403 被歸為 `unknown`，因此不退款。這對使用者不友善，但符合「不把無法證明的情況當成可退款」的原則。是否改為 `not_sent`，屬於 D2，**不自行改變**。
3. 其他例外一律為 `unknown`，這是保守分類，不是缺口，但需要在正式回覆中說明「結果未確認、請勿重新執行」。

## 6. 需要 Owner 決定的事項（只列真的缺少且影響行為的項目）

**D1：`/exchange` 是否納入 Beta，以及開放時程。**
- **現況**：`docs/private-beta-mvp-specification.md:25` 與 `ROADMAP.md:29` 寫明未開放、不納入 Beta；CLAUDE.md §9 描述的 MVP 行為則包含交換。兩者不一致。
- **選項**：
  - (a) 維持不納入，程式只接線到預設關閉的開關，直到 Owner 另行決定。
  - (b) 開放給開發伺服器作驗收。
  - (c) 開放給正式 Beta 伺服器。
- **建議**：(a) 先完成接線與離線驗證，開放時再決定 (b) 或 (c)。
- **影響**：只影響開關何時打開，不影響接線程式。

**D2：送出時明確拒絕（403／50007）是否可以退款。**
- **現況**：程式與測試把它歸為 `unknown`，不退款。這是一項已寫入程式的決定，若要改變，影響使用者點數。
- **選項**：
  - (a) 維持 `unknown`，不退款，使用者需聯絡 Owner（目前實作）。
  - (b) 改為 `not_sent` 並退款。需要先確認 Discord 對 50007 的語意，這是外部事實，Claude 無法單獨證明。
- **建議**：(a) 先維持，直到 Owner 確認 (b) 的依據。這是財務規則，不應由工程自行改變。
- **影響**：只影響 DM 失敗後的退款行為。

除 D1 與 D2 外，其餘事項都有明確設定或既有決定，**本計畫不重新列為待決**。

## 7. 最小實作範圍與測試（待批准後執行）

**程式（預計）**
- `bot/services.py`：
  - 在 `MusicService` 增加 `formal_exchange` 建構參數（預設關）與 `formal_exchange_enabled` 屬性（需同時有 `submission_repository`）。
  - 將 `synthetic_exchange` 的主體抽成共用私有方法，帶入「是否標示」參數，正式與 synthetic 共用同一條 `exchange_and_deliver` 路徑。**這是抽取，不是複製流程。**
  - 正式路徑不包裝標示。
- `bot/discord_app.py` `handlers.exchange`：
  - 順序：先看 synthetic（既有，strict True）；再看正式開關；兩者皆關時維持既有固定回覆（不變）。
  - 正式分支：`defer(ephemeral=True)`，然後以 `DiscordDirectMessageSender(interaction.client)` 與 `DiscordEphemeralReplier(interaction)` 呼叫正式方法。
- `bot/__main__.py`：從部署設定讀取正式開關，**預設關閉**，解析失敗即明確失敗。
- 不修改 `bot/exchange_delivery.py`、`bot/database/*`（已驗證）。

**測試（離線，外部替身）**
- 預設關閉：`/exchange` 回覆既有固定文字，不呼叫 repository，不送 DM（既有測試應維持通過）。
- 正式開關開、synthetic 關：呼叫正式方法，回覆**不含** `SYNTHETIC_LABEL`，DM 不含標示。
- 正式路徑的 DM 與回覆：DM 含連結，回覆不含連結。
- 正式路徑的錯誤映射：提交未知、MySQL 錯誤、PersistenceNotReady 各自有正式文字（不含標示）。
- 補償開關與正式開關互不影響：正式開、補償關時，owner 補償操作仍拒絕（`CompensationDisabled`）；正式關、補償開時，交換入口仍為固定文字。
- 既有守衛：正式路徑在三表存在時仍受阻擋（可由既有離線測試覆蓋，不重做）。

**tdd 紀錄**：先寫上列測試，取得行為紅燈（正式分支尚不存在），再實作至綠燈。若既有覆蓋已足夠，不新增。

**實機**：若只改服務與 handler，且交換程式本身不變，**不需要新的 MySQL 執行**。若抽取共用方法改動了 `synthetic_exchange`，應重跑 `test_exchange_formal_chain_mysql.py`（C1–C5）作為回歸，這需要另行授權。

## 8. 有限授權草案（可一次批准，尚未批准）

**範圍**
1. 修改 `bot/services.py`、`bot/discord_app.py`、`bot/__main__.py`，依第 7 節。
2. 新增或更新離線測試，依第 7 節；不新增 MySQL 測試檔。
3. 可選：若抽取改動了 synthetic 路由，重跑 `tests/test_exchange_formal_chain_mysql.py` 於既有 3307 隔離實例（沿用 `exchange_chain_authorized.py`，新證據目錄）。**此項需要另行勾選。**

**禁止**
- 啟用正式開關於任何部署設定（預設必須為關）。
- 真實 DM、Discord 操作、正式 `/exchange` 實際開放。
- 補償功能啟用、DDL、DROP、增權、3306、`.env`、解密。
- 修改 DM 分類（D2）與 Beta 範圍（D1）。
- commit、push、merge、force push。

**停止條件**
- 任何離線測試非預期失敗：保留輸出，停止，不改斷言。
- 既有固定回覆在預設關閉時改變。
- 任何行為改動超出第 7 節範圍。

**收尾點**
1. 離線測試綠燈，文件與交接更新。
2. 精準 commit 與正常 push 另行批准。
3. 正式開啟（D1）另行批准，不屬於本授權。

## 9. Skill 與配置

- **tdd**：可用，後續實作時使用。
- **start-dev-team**：可用，但本輪不建立團隊。若實作時需要，先說明配置並取得核准，否則不使用。

## 10. 剩餘限制

- 真實 Discord DM 未驗證（adapter 只有 mock 測試）。
- 正式 `/exchange` 未開放，本計畫不宣稱已驗證。
- D1、D2 尚待 Owner 決定。
- 補償功能仍關閉。

## 9. 實作結果：正式 /exchange 技術接線（2026-10-24，已完成，未 commit）

**狀態區分**
- **已實作**：正式入口已接到既有交換與派送程式，程式與離線測試完成。
- **已開放**：**否**。正式開關預設關閉，且部署設定未啟用。Beta 規格中 `/exchange` 仍為「未開放、不納入」（D1）。本節不宣稱已開放。
- **未驗證**：真實 Discord 指令、真實 DM、啟動後的實際 Discord 互動。
- **預設關閉**：正式入口已實作，預設關閉，未於任何部署啟用。
- **離線結果**：427 passed、47 skipped、0 failed（受影響的離線套件）。
- **MySQL 結果**：5 passed 是 synthetic 共用流程的 C1–C5 回歸，**不代表正式 handler 已實機驗證**。
- **未驗證**：真實啟動流程、Discord 指令與真實 DM 尚未驗證。
- **未改變**：Beta 開放範圍（`/exchange` 仍未開放）與補償開關（仍為關閉）未改變。

### 9.1 修改檔案

| 檔案 | 變更 |
| --- | --- |
| `bot/config.py` | 新增 `FORMAL_EXCHANGE_ENV = "JUKEBOX_FORMAL_EXCHANGE"` 與 `load_formal_exchange_enabled(environ)`：只接受 `1/true/yes/on` 為開、`0/false/no/off` 為關，缺省為關，其他值（包含空字串）拋出 `ConfigurationError`。不讀 `.env`。 |
| `bot/services.py` | `MusicService` 新增 `formal_exchange` 參數（預設 False）與 `formal_exchange_enabled` 屬性（必須是 `is True` 且有 repository）；新增 `formal_exchange()`；把 synthetic 的業務主體抽成共用的 `_run_exchange(..., label)`。synthetic 仍包裝標示，正式路徑不包裝，回覆前綴為空字串。 |
| `bot/discord_app.py` | `handlers.exchange`：先判斷 `synthetic_exchange_enabled is True`（既有優先），再判斷 `formal_exchange_enabled is True`（defer ephemeral，使用既有 `DiscordDirectMessageSender` 與 `DiscordEphemeralReplier`），皆不成立時維持既有固定回覆。 |
| `bot/__main__.py` | 在既有 try 中讀取 `load_formal_exchange_enabled()`（無效值即明確停止），傳入 `MusicService(..., formal_exchange=...)`。 |
| `tests/test_formal_exchange_entry.py`（新增） | 離線測試，見 9.3。 |
| `docs/exchange-formal-entry-wiring-plan.md` | 本節。 |
| `.local/mysql-test/exchange_chain_authorized.py` | 本地啟動器：預設只跑 C1–C5，`--include-unknown` 才加入既有 4 項（本輪未加）。不提交。 |

**未修改**：`bot/exchange_delivery.py`、`bot/database/*`、`bot/discord_exchange.py`（DM 分類維持 D2 現行行為）、補償相關程式、Owner 規則。

### 9.2 紅綠證據（tdd）

| 階段 | 結果 | 性質 |
| --- | --- | --- |
| 配置函式尚未存在 | `ImportError: cannot import name 'FORMAL_EXCHANGE_ENV'` | 符號紅燈（已如實記錄） |
| 加入配置函式後，服務與 handler 尚未實作 | **5 失敗**：`TypeError: ... unexpected keyword argument 'formal_exchange'`（3 項）、`AttributeError: 'MusicService' object has no attribute 'formal_exchange_enabled'`（1 項）、`AssertionError: Expected defer to have been awaited once. Awaited 0 times.`（1 項） | **行為紅燈** |
| 服務層實作後 | 服務與 synthetic 相關 69 項通過；handler 正式分支仍失敗（1 項） | 中間狀態 |
| handler 實作後 | `test_formal_exchange_entry.py`、`test_discord_exchange_adapter.py`、`test_discord_app.py`：**62 passed** | 綠燈 |

**誠實說明**：配置函式的測試在函式實作後首次即通過，其行為紅燈沒有獨立觀察到（前置的符號紅燈存在）。

**既有測試回歸（首次即通過，如實記錄）**：
- `test_with_both_switches_on_the_synthetic_route_is_chosen_first`：synthetic 優先在實作前即成立。
- `test_the_exchange_route_is_selected_only_by_the_explicit_synthetic_flag`（既有）：通過。
- `test_the_formal_switch_is_off_...`、非 True 值與缺屬性情境：實作前即由既有固定回覆路徑成立，屬回歸。

### 9.3 離線實際結果

- **受影響的離線套件**：`test_formal_exchange_entry.py`、`test_exchange_*.py`、`test_compensation_*.py`、`test_reserved_protection.py`、`test_identity_service_path.py`、`test_services.py`、`test_discord_app.py`、`test_discord_exchange_adapter.py`、`test_gateway_acceptance.py`、`test_guild_acceptance.py`、`test_private_beta.py`。
- **結果**：**427 passed、47 skipped、0 failed**。47 項跳過皆為未開隔離開關的 MySQL 案例。
- **未跑完整套件**。

### 9.4 預設關閉證據

- `load_formal_exchange_enabled({})` 回傳 False（測試：`test_the_formal_switch_is_off_when_the_setting_is_absent`）。
- `"false"`、`""`、`"maybe"` 等非明確啟用值不會開啟（參數化測試）。
- 不帶正式旗標建立的 `MusicService`，`formal_exchange_enabled` 為 False，且 `formal_exchange()` 在任何資料庫或 DM 工作之前拒絕（`ServiceUnavailable`）。
- 不帶正式旗標時，`/exchange` handler 維持既有固定回覆，不 defer、不呼叫 repository（既有與新增測試）。
- `__main__` 缺省讀到 False。**啟動程式本身未執行**，此項由配置函式測試覆蓋。

### 9.5 MySQL 回歸（C1–C5，共用流程抽取後）

- **證據目錄**：`.local\mysql-test\exchange-chain-chain3\`（新目錄，未覆寫）。
- **基線**：身分一致；三表存在且定義一致；exchange 授權快照與 A 階段一致；三表列數為 0。
- **結果**：**5 passed、0 failed、0 error、0 skipped**（20.93 秒）。
  - C1 正常交換、C2 補償關閉三表存在的阻擋、C3 同 key 重放、C4 明確未送退款與釋放、C5 DM 未知持有。
- **範圍**：只跑 C1–C5。既有 unknown-commit 4 項**未納入**（本輪未直接改動其邏輯）。
- **清理**：5 組計畫與結果，計畫列數等於刪除數，清理後剩餘 0，被刪列都在 `900000000000000100–199`。
- **停機**：正常 SHUTDOWN，退出碼 0；3307 無 Listen；無 mysqld 指向本 datadir；無 worker 殘留。
- **測試檔雜湊**：`tests/test_exchange_formal_chain_mysql.py` 未變（`8dd3379c…9589`），沿用既有 C1–C5。

### 9.6 行為保留檢查

- **重放與提交未知重試**：沿用 `exchange_and_deliver` 的 `_exchange_with_one_replay`，正式與 synthetic 共用。
- **派送恢復與退款重試**：沿用 `recover_interrupted_deliveries` 與 `retry_pending_refunds`。
- **交易外 DM**：沿用 `deliver_recommendation`，claim → DM → record。
- **補償守衛**：沿用既有 `guarded` 判斷，與正式開關獨立（`compensation_enabled` 未被改動）。
- **Owner 規則**：未改動。

### 9.7 剩餘限制與收尾

1. **正式 `/exchange` 尚未開放**（D1）。接線完成、預設關閉，開放對象與時程另行決定。
2. **真實 DM 未驗證**。DM adapter 沿用既有行為（D2）：403／50007 與其他例外為 unknown，不自動退款。
3. **真實 Discord 指令與啟動流程未驗證**。`__main__` 與 handler 只有離線測試。
4. **正式路徑的實機驗證**：本輪只做 synthetic 的 C1–C5 回歸；正式路徑在離線測試中以假 repository 驗證，未在實機 MySQL 上直接走正式 handler。
5. **補償功能仍關閉**。

**收尾**：本階段實作與離線／實機回歸完成。精準 commit 與正常 push 需另行批准。

### 9.8 交接與精準提交候選

- **受追蹤修改**：`bot/config.py`、`bot/services.py`、`bot/discord_app.py`、`bot/__main__.py`、`docs/exchange-formal-entry-wiring-plan.md`。
- **新增**：`tests/test_formal_exchange_entry.py`。
- **不提交**：`.local/` 內所有內容（本地啟動器與證據）；batch-B、review、decision_snapshot、ledger_integrity、私人 PDF、CRLF-only 的 compensation 測試檔等既有無關工作。
- **行尾**：`bot/services.py` 與 `bot/__main__.py` 工作樹為 CRLF，HEAD 為 LF；`bot/discord_app.py`、`bot/config.py` 為 LF。提交前用 `git diff --ignore-cr-at-eol` 核對，只納入本次區塊，不整檔提交行尾變更。
- **雜湊**（提交前核對）：
  - `bot/services.py`：`e515024943b318370387ab161d382e4163a7b527ed878dc326597181d62449fd`
  - `bot/discord_app.py`：`c3bd7b4e46b5075c07d770ad918650707ddee75596b2dc2faf721e04bc6d07bd`
  - `bot/__main__.py`：`c902a326f3b92a3b0f3a3347d077aa2ea0a441382d0d61f8bcdb1494a42c4f5d`
  - `bot/config.py`：`8f2943f71c7983a2b6eee3a91f78e6ebf784c151813af286dbbbdad06e401537`
  - `tests/test_formal_exchange_entry.py`：`e2fac4ff231dc2af2b2624d70234050d6af1f304f69a8de63b332d5f2679cb0a`
