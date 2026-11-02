# 自動確認錄音版本：A0 唯讀設計

作者：Sean（後端）　日期：2026-11-02　狀態：**設計草案，待 PM 與 Owner 審閱；未寫任何實作、未改任何既有檔案、未連線 VM／正式資料庫／Discord、未安裝套件。**

標記說明：**[事實]** 是我讀到程式或文件後確認的內容（附 file:line）；**[推測]** 是我的判斷或估算，尚未驗證；**[未讀]** 是我沒有讀到或只讀了一部分的檔案。

## 0. 先講白話

現在的規則是：就算歌曲被自動核准，「這是哪個錄音版本」仍然一律要 Owner 手動按一次，之後才會發 Nickel（`docs/deploy/auto-review-nickel-enable.md` 的「發獎另外要求…」一節）。Owner 已核可改成：系統先自己比對，沒疑慮就自動確認版本並發獎；有疑慮才把案件送給 Owner，並在卡片上寫明原因與既有歌曲連結。

比對的方法是「新投稿時，再向 YouTube 查一次最近 500 首既有歌曲」，比完立刻丟掉，不存任何 API 資料。**標題只用來產生疑慮，永遠不證明「同一首」或「不同首」。**

## 1. 現況盤點

### 1.1 前置檔案確認

| 項目 | 結果 |
| --- | --- |
| migration 0004 | **[事實]** 存在：`bot/database/postgres/0004_api_observations.sql`。建立 `jb2_api_observations`（第 7–20 行）、`jb2_retention_runs`（第 23–30 行），只新增、不改既有表。 |
| 保存機制檔案 | **[事實]** 存在：`bot/api_retention.py`、`bot/database/api_observations.py`、`bot/database/api_retention_job.py`、`docs/api-retention-release.md`、`docs/api-data-lifecycle-design.md`。**[未讀]** `api_observations.py`、`api_retention_job.py` 內文（只確認存在，並由 `auto_review_store.py` 的 import 看到 `basis_expired`、`purge_video`、`record_observation`）。 |
| 設定名 | **[事實]** `JUKEBOX_AUTO_REVIEW`、`JUKEBOX_API_RETENTION`、`YOUTUBE_TRUSTED_CHANNEL_IDS`（`bot/config.py:221-224`）。`docs/auto-review-nickel-enable.md` 實際路徑是 `docs/deploy/auto-review-nickel-enable.md`（我只讀了前 70 行）。 |

### 1.2 各模組現有責任與可重用函式

| 模組 | 現有責任（[事實]） | 可重用 |
| --- | --- | --- |
| `bot/auto_review.py` | 把一次 YouTube 查詢結果轉成 verdict。政策版本 `trusted-channel-v1`（:14）。`decide_auto_review`（:52-67）：API 失敗→RETRY；不存在→UNAVAILABLE；私人／已刪→UNAVAILABLE；直播／處理中／無時長→HOLD；其餘進 `_duration_verdict`（:70-83）：時長不在範圍→DURATION_REJECT，信任條件不符→DURATION_ONLY，全符合→AUTO_APPROVE。`_trust_withheld`（:86-103）依序檢查頻道在名單、分類 10、公開、無直播歷程、標題不含排除字。`NON_SONG_TITLE_HINTS`（:16-18）已有「標題只能撤回信任」的先例。`_audit`（:119-126）理由字串含 channel 與 fetched_at。`ObservedValues`（:34-38）承載只能存短期表的值。 | `decide_auto_review` 原樣保留；`ObservedValues`；`_verdict` 的雙理由（完整／retention 公開）模式。 |
| `bot/youtube_metadata.py` | 官方 `videos.list` 客戶端。`lookup(video_id)`（:157-175）：冷卻中直接回 QUOTA_EXHAUSTED；最多 3 次重試；`_terminal_outcome`（:197-210）遇 `quotaExceeded`／`dailyLimitExceeded` 啟動 1 小時冷卻（`QUOTA_COOLDOWN`，:27）。`VideoMetadata`（:60-73）含 `title`、`channel_id`、`duration_seconds`，註解寫明標題「never stored」。`_interpret`（:212-226）**只接受單一項目**：`len(items) > 1` 視為 TEMPORARY_FAILURE。`http_transport`（:99-114）一次一個 id。每 5 次／秒的本機限速（:26）。 | 冷卻、限速、重試、`parse_iso8601_duration`、`_section`、`_metadata`。**不能直接批次查**，需新增批次入口（見 2.2）。 |
| `bot/auto_pipeline.py` | 流程協調，在 worker thread、不開交易時呼叫 API。`_review_step`（:197-213）：取 subject→查詢→`decide_auto_review`→RETRY／HOLD 則暫停（`_pause`，:235-243）→否則 `_apply`（:215-225）。`sweep`（:77-94）每輪最多 `SWEEP_LOOKUP_LIMIT=20`（:22）。`_replaying`（:245-256）處理 commit 未知。`settle_ready`（:159-175）逐一結算 READY 的案件。`process`（:67-75）= 審核步驟 + 立即結算 + 讀回結果。`_revalidate_expired`（:127-157）對過期的待審案件重查，只刷新數值、不核准。 | 全部流程骨架；新增一個「比對」步驟插在 :208 與 :209 之間。 |
| `bot/database/auto_review_store.py` | `SYSTEM_ACTOR_ID = 0`（:20）。`apply_verdict`（:51-75）：先檢查 revision 沒被動過（:64-65）；`approve` 只在 AUTO_APPROVE 且身分不是 CONFLICT（:68）；retention 模式走 `_apply_retained`（:90-112），通過時只把數值寫進觀測表、核准或駁回後 `purge_video(..., "completed")`。`refresh_observation`（:115-127）只認 AUTO_APPROVE 與 DURATION_ONLY 兩種 kind。 | 同一個交易寫入的模式；系統身分 0。 |
| `bot/version_identity.py` | `RecordingVersionIdentity`、`VersionAssignment`（:179）、`compare_version_assignments`（:223）。 | 原樣使用。 |
| `bot/version_evidence.py` | `resolve_video_version`（:253-279）：只有 RELIABLE 的證據算數；同影片多版本→CONFLICT；有確認證據→CONFIRMED。 | 原樣使用；系統證據要靠 `assess_evidence` 變成 RELIABLE。 |
| `bot/evidence_record.py` | `EvidenceSourceType` 只有 OWNER_REVIEW／RECORDING_REFERENCE／PLATFORM_METADATA（:16-21）。`assess_evidence`（:87-96）：PLATFORM_METADATA 或沒版本→PENDING；`owner_confirmed` 不是 True→PENDING；其餘 RELIABLE。`_validate_provenance`（:119-120）**要求 `judged_by` 為正整數**。 | `record_evidence`。**目前沒有任何「系統判定」的位置**：系統的 actor 是 0，會被 :119 拒絕；且 RELIABLE 一定要 `owner_confirmed=True`。 |
| `bot/owner_card_versions.py` | 卡片版本動作的文字與證據。`watch_url`（:308-313）、`new_version_identity`（:329-334，`rv-` + 隨機 hex，不由影片或標題推導）、`manual_check_source`（:337-345）、`version_evidence`（:348-361，`OWNER_REVIEW` 來源）。 | `watch_url`、`new_version_identity`；新增「疑慮說明」文字函式。 |
| `bot/database/review_persistence.py` | `apply_version_confirmation`（:367-390）：寫證據、建立版本、持久化身分、`jb2_control.revision + 1`、重算 decisions。入口 `SubmissionRepository.confirm_version`（`submission_repository.py:561-569`）呼叫 `require_owner`（review_persistence.py:124-132）。`_require_reliable_evidence`（:536-546）要求 `judged_by == Owner` 且證據 RELIABLE。 | `apply_version_confirmation` 的內部步驟；但**入口與檢查都綁死 Owner**，系統需另開入口。 |
| `bot/services.py` / `bot/discord_app.py` | `/share` 先 `defer`（`discord_app.py:226-236`），再在 `asyncio.to_thread` 跑 `auto_pipeline.process`（`services.py:196-215`）。Spotify 在 `bot/urls.py:80` 就被拒。 | 不需修改。 |
| `bot/api_retention.py` | 維運指令（status／purge）。沒有可在比對流程中重用的函式。 | 無（只需遵守其保存規則）。 |

## 2. 擬新增／修改的函式與模組（Rule One：函式 ≤25 行、巢狀 ≤3）

### 2.1 新增 `bot/version_concerns.py`（純函式，無 I/O，可離線完整測試）

```python
class ConcernCode(Enum):
    SAME_CHANNEL_CLOSE_DURATION = "same_channel_close_duration"
    SIMILAR_TITLE = "similar_title"
    VERSION_KEYWORD = "version_keyword"
    TITLE_UNJUDGEABLE = "title_unjudgeable"      # 標題空白或正規化後太短
    COMPARISON_INCOMPLETE = "comparison_incomplete"  # 比對沒做完（API 失敗等）

@dataclass(frozen=True)
class VersionConcern:
    code: ConcernCode
    other_video_id: str | None   # 疑慮對象的既有影片；關鍵字／不完整時為 None

@dataclass(frozen=True)
class ConcernThresholds:
    max_duration_gap_seconds: int = 3
    min_title_similarity: float = 0.65
    min_judgeable_title_length: int = 3

def normalize_title(title: str | None) -> str
def title_similarity(first: str, second: str) -> float          # difflib.SequenceMatcher
def has_version_keyword(title: str | None) -> bool
def concerns_against(candidate: VideoMetadata, other: VideoMetadata,
                     thresholds: ConcernThresholds) -> tuple[VersionConcern, ...]
def assess_concerns(candidate: VideoMetadata, others: Iterable[VideoMetadata],
                    thresholds: ConcernThresholds = ConcernThresholds()) -> tuple[VersionConcern, ...]
```

只用標準函式庫（`re`、`unicodedata`、`difflib`）；不引入新套件。標題在函式內使用後不寫入任何回傳值（`VersionConcern` 沒有標題欄位，這是刻意的型別保證）。

### 2.2 修改 `bot/youtube_metadata.py`

```python
BATCH_LOOKUP_SIZE = 50   # videos.list 的 id 上限，1 單位

class BatchStatus(Enum): COMPLETE, INCOMPLETE, QUOTA_EXHAUSTED

@dataclass(frozen=True)
class BatchLookup:
    status: BatchStatus
    found: tuple[VideoMetadata, ...]     # 已刪除／私人的舊影片不在其中（略過，不算失敗）

class YouTubeMetadataClient:
    def lookup_many(self, video_ids: Sequence[str]) -> BatchLookup   # 分塊 50 個，共用冷卻、限速、重試
    def _interpret_many(self, requested: frozenset[str], response: object) -> tuple[VideoMetadata, ...] | None
```

`lookup_many` 重用 `_cooling_down`、`_respect_rate_limit`、`_terminal_outcome`，所以「額度用完沿用 1 小時暫停」自動成立。`_interpret_many` 驗證回傳的 id 都屬於請求集合、無重複；回傳比請求多的 id 或形狀不對→視為 INCOMPLETE。`lookup`／`_interpret` 不動。

### 2.3 新增 `bot/auto_version_review.py`（組合層，純函式 + 一個小型協調函式）

```python
@dataclass(frozen=True)
class ComparisonBasis:        # 比對時看到的世界，寫入交易時再驗證
    compared_video_ids: tuple[str, ...]
    last_accepted_sequence: int          # 比對時最新的投稿序號

def refine_verdict(verdict: AutoReviewVerdict, candidate: VideoMetadata,
                   batch: BatchLookup, compared_count: int,
                   thresholds: ConcernThresholds) -> AutoReviewVerdict
def concern_verdict(base: AutoReviewVerdict, concerns: tuple[VersionConcern, ...]) -> AutoReviewVerdict
def confirm_verdict(base: AutoReviewVerdict, basis: ComparisonBasis) -> AutoReviewVerdict
```

`refine_verdict` 只處理 `AUTO_APPROVE`：有疑慮→`concern_verdict`（把 kind 改成 `DURATION_ONLY`，理由 `version_concern_*`，仍記錄時長，案件進待審）；批次不完整→疑慮 `COMPARISON_INCOMPLETE`；全部通過→`confirm_verdict`。其他 kind 原封不動。`AutoReviewVerdict`（`auto_review.py:41-49`）新增兩個選填欄位：`concerns: tuple[VersionConcern, ...] = ()`、`version_basis: ComparisonBasis | None = None`。政策版本升為 `trusted-channel-v2`。

### 2.4 修改 `bot/evidence_record.py` 與版本確認的系統入口

```python
class EvidenceSourceType(Enum):
    ...
    SYSTEM_JUDGEMENT = "system_judgement"     # 新增：系統自動比對後的判定，不是 Owner

SYSTEM_JUDGE_ID = 0   # 與 auto_review_store.SYSTEM_ACTOR_ID 同值，建議由後者 import 以避免兩份
```

- `_validate_provenance`（:119-120）：`judged_by` 只在來源為 `SYSTEM_JUDGEMENT` 時允許 0。
- `assess_evidence`（:87-96）：`SYSTEM_JUDGEMENT` 且有版本、`judged_by == 0` 且 `owner_confirmed is False` → RELIABLE（**證據不偽造成 Owner**：`owner_confirmed` 仍是 False）。
- `bot/owner_card_versions.py` 新增 `system_version_evidence(video, identity, retrieved_at, basis) -> EvidenceRecord`：來源文字固定為「系統判定：最近 N 首比對無疑慮（policy=trusted-channel-v2）」，**不含標題、不含頻道**。
- `bot/database/review_persistence.py` 新增 `apply_system_version_confirmation(cursor, control, command)`：重用 `apply_version_confirmation` 的內部步驟（`_append_evidence_record`、`ensure_version`、`persist_review`、`_mirror_legacy_identity`、`persist_decisions`），但略過 `require_owner` 與 `_require_reliable_evidence` 的 Owner 檢查，改驗「來源必須是 SYSTEM_JUDGEMENT、actor 必須是 0」。應從 `apply_version_confirmation` 中抽出共用的 `_stamp_version`，避免兩份複製（Rule One：現有函式已 24 行左右，不可再往裡加）。
- `bot/database/auto_review_store.py`：`apply_verdict` 在 `verdict.version_basis` 存在時，於**同一個交易**內：重驗比對基礎（見第 3 節）→ 核准 → 系統版本確認。
- `bot/database/submission_repository.py`：`recent_comparison_video_ids(limit: int) -> ComparisonBasis`（唯讀）；`apply_auto_review` 增加一個傳遞 verdict 的路徑（verdict 已帶 `version_basis`，簽名可不變）。

### 2.5 修改 `bot/auto_pipeline.py`

```python
def _review_step(self, operation_id)          # 在 :208 後多一步，仍須 ≤25 行
def _compare_recent(self, subject, verdict) -> AutoReviewVerdict   # 只在 verdict.kind is AUTO_APPROVE 且開關開時執行
```

`AutoPipeline.__init__`（:45）新增選填參數 `version_confirm: bool = False`，只有精確 `True` 才啟用（沿用 :60-61 的 `is True` 寫法）。

### 2.6 修改 `bot/config.py`、`bot/auto_startup.py`

新增 `JUKEBOX_AUTO_VERSION_CONFIRM`（名稱待 PM 確認），用既有 `_strict_switch`（config.py 約 :239）：預設 off、值無效→`ConfigurationError`（拒絕啟動）。`load_auto_settings` 規則：開啟此開關必須同時 `JUKEBOX_AUTO_REVIEW=on` 與 `JUKEBOX_API_RETENTION=on`，否則拒絕啟動。

### 2.7 修改 `bot/owner_card_versions.py`、`bot/discord_owner_cards.py`

新增 `concern_lines(concerns) -> str`：每個疑慮一行，白話原因 + `watch_url(other_video_id)`。**不顯示、不保存標題**。**[未讀]** `discord_owner_cards.py` 內文，卡片實際版面與按鈕狀態未確認。

## 3. 資料流（/share 到發獎或轉待審）

```
/share  (defer, discord_app.py:234)
 └ services._share_pending → accept_pending 提交投稿回執
 └ auto_pipeline.process  (worker thread，無交易)
    1. auto_review_subject            讀 revision，確認仍待驗證
    2. client.lookup(video)           1 單位 → decide_auto_review (原樣)
       ├ RETRY/HOLD → 暫停，回覆「系統會自動重試」          (不變)
       ├ UNAVAILABLE/DURATION_REJECT/DURATION_ONLY → 照舊套用  (清單空白也走這裡，不再比對，省額度)
       └ AUTO_APPROVE（暫定）
    3. [新] 開關關 → 照舊套用 AUTO_APPROVE，版本仍待 Owner（現行為）
    4. [新] repository.recent_comparison_video_ids(500)  → ComparisonBasis（短交易，結束即關）
    5. [新] client.lookup_many(ids)   ≤10 次呼叫 = ≤10 單位
    6. [新] refine_verdict
       ├ 有疑慮 / 比對不完整 → DURATION_ONLY（記時長，待審；卡片寫原因 + 連結）
       └ 全通過 → AUTO_APPROVE + version_basis
    7. apply_auto_review  (單一交易，寫入閘門內)
       a. revision 沒變？否→什麼都不寫
       b. [新] 重驗：比對之後有沒有新的投稿序號？有→降級為待審（comparison_stale）
       c. 核准 (ApprovalSource.AUTO, actor 0)；觀測表記時長／頻道，核准後 purge
       d. [新] 系統版本確認 (SYSTEM_JUDGEMENT, actor 0) → 建立新版本
    8. settle_ready → 既有發獎規則（先投稿者優先、同版本只發一次、settlement 開關）
    9. 讀回 durable 狀態，回覆使用者
```

重點：步驟 5 的舊影片資料（標題、頻道、時長）只在記憶體的區域變數，函式結束即回收；`ComparisonBasis` 只含影片 id 與序號。步驟 7b 解決並行問題（見第 5 節）。

## 4. 疑慮訊號的判定規則與門檻

共通原則：寧可多送 Owner，也不要放過。**[推測]** 理由：誤送一件只多花 Owner 一次點擊；誤放一件會立刻發 Nickel，而 `audit_reward_history` 的說明是「不移動、不收回任何 Nickel」（review_persistence 引用處為 `reward_history.py:23-26`，我只讀了簽名與說明），事後補救成本高。

| 訊號 | 判定規則 | 門檻與理由 |
| --- | --- | --- |
| 同頻道且時長接近 | 候選與某既有影片 `channel_id` 相同，且 `abs(時長差) <= 3` 秒 | 3 秒由 Owner 指定。單看「同頻道」不構成疑慮，因為認可頻道本來就會一直發新歌。 |
| 標題相似 | 兩邊各自 `normalize_title` 後：`SequenceMatcher.ratio() >= 0.65`，**或**較短者長度 ≥3 且被較長者完整包含 | **建議 0.65（比常見的 0.8 保守）**。理由：正規化已去掉括號標籤，剩下的差異多半是真正的歌名；同歌不同上傳的標題通常高度重疊，但中日韓短標題一個字就會讓 ratio 大幅波動，所以門檻要低、再加「包含」規則。**[推測]**，沒有真實資料驗證，請 Ken 用樣本集校準。 |
| 版本關鍵字 | 候選標題（正規化前、casefold 後）含關鍵字 | 英文用字邊界比對：`remix`、`cover`、`live`、`acoustic`、`instrumental`、`sped up`、`slowed`、`nightcore`、`remaster`、`ver.`、`version`、`mix`、`edit`、`demo`、`unplugged`、`karaoke`、`8d`、`lo-fi`／`lofi`、`bass boosted`；中文用子字串：翻唱、現場、混音、純音樂、伴奏、加速、降速、重製、重新錄製。**一定要用字邊界**，否則 `live` 會誤中 `deliver`／`alive`。 |
| 標題無法判斷 | 標題為空，或正規化後長度 `< 3` | 無法比對就不能說沒疑慮，直接視為疑慮。 |
| 比對不完整 | 任何一個批次失敗、被截斷或回傳形狀不合 | 見第 5 節。 |

**正規化規則**：Unicode NFKC → casefold → 刪除 `()（）[]【】「」『』<>《》` 括號內全部文字 → 刪除 `feat.`／`ft.`／`featuring`／`with` 之後到結尾的藝人字樣 → 去掉標點與空白。

**不採用的訊號**：只比較舊影片的標題是否含關鍵字（舊的是 live、新的是原版也是不同版本）會有漏洞，列入 Owner 決定事項 D2。標題相似度只產生疑慮，**沒有疑慮不等於不同首**，這一點必須同樣寫在卡片文案與程式註解。

**[事實，既有缺陷，順帶提出]** `NON_SONG_TITLE_HINTS`（auto_review.py:16-18）用子字串比對，`"clip"` 會誤中 `eclipse`、`"preview"` 會誤中 `previews`。誤傷方向是多送 Owner，不影響本設計的安全性，本案不改，但建議另立小票。

## 5. 失敗模式表

| 情境 | 偵測方式 | 結果 | 說明 |
| --- | --- | --- | --- |
| 主查詢（新影片）API 失敗／額度用完 | `LookupStatus.TEMPORARY_FAILURE`／`QUOTA_EXHAUSTED` | RETRY：不寫入、暫停 30 分鐘後自動重試 | **現況不變**（auto_review.py:58-59）。 |
| 比對批次 API 暫時失敗（重試 3 次仍失敗） | `BatchStatus.INCOMPLETE` | **待審**，疑慮 `COMPARISON_INCOMPLETE`，**不拒絕、不發獎** | 依 Owner 核可的「結果為待審而非拒絕」。代價：API 故障期間新案件全進 Owner 佇列且不會自動重試（因案件已離開待驗證）。見 D1。 |
| 比對時額度用完 | `QUOTA_EXHAUSTED`；客戶端進入 1 小時冷卻（youtube_metadata.py:27, 203-206） | 同上，待審 | 沿用既有「暫停 1 小時」。冷卻期間後續新投稿的主查詢也會得到 `QUOTA_EXHAUSTED`→RETRY，所以冷卻期間其實多半走 RETRY 而不是待審。 |
| 部分批次成功、部分失敗 | 任一批次非 COMPLETE | 待審 | 只比對一部分不能宣稱「無疑慮」。 |
| `YOUTUBE_TRUSTED_CHANNEL_IDS` 為空 | `_trust_withheld` 回 `channel_not_trusted`（auto_review.py:92-93） | DURATION_ONLY 待審，**根本不進比對** | 不花額度。測試要鎖死：清單空時 `lookup_many` 呼叫次數為 0。 |
| 既有影片已刪除／私人化 | 批次回應不含該 id | **略過**，不算失敗、不算疑慮 | 與 Owner 規則一致。若 500 首中大部分已消失，比對範圍實質縮小，這是已知的覆蓋縮水（見第 6 節）。 |
| 並行：同時有兩個新投稿 A、B | 7b 重驗：比對之後出現新的 accepted sequence | 後寫入者降級為待審（`comparison_stale`），不自動確認 | 寫入閘門已經序列化交易（`auto_review_store.py:51` 的 docstring：Cursor holding the writer gate），但查詢發生在交易之外，所以必須在交易內重驗。 |
| 同一影片重複觸發（重播／重啟） | 決定鍵 `auto-review:{video_id}:{revision}`（auto_review_store.py:71, 105） | 冪等：回放原結果 | 系統版本確認也要使用確定性的 decision_id，例如 `auto-version:{video_id}:{revision}`。 |
| 候選影片在兩次查詢之間被改成私人 | 本流程只查一次候選 | 不影響 | 比對用的候選資料與主查詢是同一次結果，不再查。 |
| 提交後 commit 結果未知 | `CommitOutcomeUnknown` | 沿用 `_replaying`（auto_pipeline.py:245-256） | 不變。 |
| 開關值無效／缺依賴開關 | 啟動設定檢查 | 拒絕啟動（fail closed） | 預設 off。 |
| 觀測表缺失（0004 未套用） | 既有 `auto_startup` 檢查 | 拒絕啟動 | 不變。 |

## 6. 與規格、現況的衝突與風險

1. **[事實] 目前系統完全沒有「系統判定」的版本證據。** 版本確認入口綁死 Owner（`submission_repository.py:567`、`review_persistence.py:124-132, 543`），證據要 RELIABLE 必須 `owner_confirmed=True`（evidence_record.py:94-95），且 `judged_by` 必須 >0（:119-120）。要做到「證據記為系統判定，不偽造成 Owner 手動紀錄」，**必須新增證據來源類型並放寬三處檢查**，這是核心模組的修改，不是接線而已。
2. **[事實→推測] 回滾相容性。** `docs/api-retention-release.md` 第六節記錄：舊版（R0）讀到不認識的持久化表示會失敗（`ValueError: Unknown persisted representation`，submission_codec.py:121 有同樣訊息）。新增 `EvidenceSourceType.SYSTEM_JUDGEMENT` 並寫入 `jb2_evidence.evidence_json` 後，**很可能**讓不含此枚舉的舊版程式無法讀取。**[推測]**：我沒讀 codec 的枚舉解碼細節（`ENUMS` 在 submission_codec.py:67 附近），需要 Ken 以測試驗證。結論：第一筆系統證據寫入後，回滾下限會是新版，與 0004 的情形相同。
3. **[事實] 不保存標題與現有 schema 沒有衝突。** `jb2_videos`、`jb2_evidence`、`jb2_review_history`、`jb2_decisions` 的欄位都沒有標題欄；`VideoMetadata.title` 註解寫明只在記憶體（youtube_metadata.py:67）；觀測表只有 `duration_seconds`、`channel_id`（0004:7-14）。新設計**不需要新的 migration**。
4. **[推測] 疑慮理由裡的「既有影片 id」是否屬於需限期保存的 API 資料？** 影片 id 是使用者投稿時提供的識別碼，不是 API 回傳值；但「這兩支是同頻道、時長接近」是由 API 資料推導的結論。現有政策把結論（通過／未通過）視為可永久保存（api-retention-release.md 第二節「永久歷程只存結論」）。我建議只存「疑慮代碼 + 對方影片 id」，不存頻道、時長、標題。是否接受見 D3。
5. **[事實] Spotify 仍被拒**（`bot/urls.py:80`）。本設計只處理 YouTube 影片，與 Spotify 規格的不一致（CLAUDE.md 9.3 第 1 項）**不因本案改變、也不惡化**。
6. **覆蓋範圍的盲區（我認為的最大風險，見 D6）：** 只比對最近 500 首，更舊的歌看不到；已刪除或私人化的舊影片被略過；跨語言標題（原文與羅馬拼音、中文與英文名）相似度會偏低；MV 與官方音源由不同頻道發佈且時長差 >3 秒時，只能靠標題相似度攔截。以上任一情況都可能讓「同一首的重複上傳」被自動確認為「新版本」並立刻發獎。**[推測]** 只有認可頻道會進入這條路徑，風險受名單限制，但沒有實測數據。
7. **[事實] 發獎是立即的**（`process` 在審核步驟後直接 `settle_ready`，auto_pipeline.py:73-75）。自動確認錯了，Nickel 當下就發出。事後可用既有 `merge`／`exclude`（submission_repository.py:666, 680）修正版本歸屬，但**不會收回已發的 Nickel**（reward_history.py:23-26 的 docstring）。
8. **[事實] 額度估算：** 每次新投稿 1 + ⌈500/50⌉ = **11 單位**；預設日額度 10,000 → 約 900 件新投稿／日（只計這條路徑）。`SWEEP_LOOKUP_LIMIT=20`（auto_pipeline.py:22）以「案件」計，每件現在最多會呼叫 11 次 API，一輪最壞 220 單位且被 0.2 秒限速拖成約 45 秒，仍在背景巡檢內可接受。互動路徑（`/share`）已 defer，最壞多出約 2 秒（10 次 × 0.2 秒限速，不含網路延遲）。**[推測]** 實際延遲未量測。
9. **[事實] 重驗路徑不受影響：** `_guarded_revalidation`（auto_pipeline.py:150-155）直接呼叫 `decide_auto_review` 並只刷新觀測值，不核准；新 kind 沿用 DURATION_ONLY／AUTO_APPROVE，所以 `refresh_observation`（auto_review_store.py:123）的白名單不用改。
10. **[未讀]** `bot/database/submission_codec.py` 全文、`bot/reward_candidates.py` 全文、`review_persistence.py` 全文（只讀到 :120-135、340-430、536-546、730-770 附近）、`bot/version_categories.py`、`bot/discord_owner_cards.py`、`docs/owner-case-cards.md`、各測試檔內容。「apply_review 與 apply_version_confirmation 能否在同一交易中連續呼叫」我只由 :386-389 的 `control = dict(control, revision=+1)` 寫法**推測**可行，需要 Ken 在可丟棄 PostgreSQL 上以第一個整合測試證明。

## 7. 建議的 TDD 順序

原則：每一步先寫一個會失敗的測試（紅），再寫最小實作（綠），再重構。離線測試（`-m "not postgres"`）優先，整合測試用 `compose.test.yaml` 的拋棄式 PostgreSQL。與 Ken 的測試計畫對接點標為「對接」。

| # | 先寫的失敗測試 | 檔案 | 讓它變綠的最小實作 |
| --- | --- | --- | --- |
| 1 | `normalize_title("Song (Official MV) [4K] feat. X")` 等於 `normalize_title("SONG")`；全形／大小寫／括號／feat 各一案例 | 新 `tests/test_version_concerns.py` | `normalize_title` |
| 2 | 同頻道時長差 3 秒→疑慮；差 4 秒→無；不同頻道差 0 秒→無（本訊號）；邊界值 | 同上 | `concerns_against` 的時長規則 |
| 3 | 標題相似度門檻邊界（0.64／0.65）、包含規則、短標題（<3）→`TITLE_UNJUDGEABLE`、空標題 | 同上 | `title_similarity`、其餘規則 |
| 4 | 關鍵字：`Remix`、`LIVE`、`sped up`、`翻唱` 命中；`deliver`、`alive` 不命中 | 同上 | `has_version_keyword` |
| 5 | `VersionConcern` 與 repr／日誌不含標題（以特徵字串斷言） | 同上 | 資料結構無標題欄位 |
| 6 | `lookup_many`：130 個 id→分 3 次請求（50/50/30）；回傳缺少的 id 被略過；重複或多餘 id→INCOMPLETE | 擴充 `tests/test_youtube_metadata.py` | `lookup_many`、`_interpret_many` |
| 7 | 額度用完→`QUOTA_EXHAUSTED` 且進入 1 小時冷卻，之後不再發出網路請求（共用既有冷卻） | 同上 | 共用 `_terminal_outcome` |
| 8 | `refine_verdict`：非 AUTO_APPROVE 原樣返回；有疑慮→DURATION_ONLY；批次不完整→待審；全通過→帶 `version_basis` | 新 `tests/test_auto_version_review.py` | `refine_verdict` 系列 |
| 9 | **清單空白時 `lookup_many` 呼叫次數 = 0** | 擴充 `tests/test_postgres_auto_pipeline.py`（離線假 repository 版本先寫） | `_compare_recent` 的守衛 |
| 10 | 開關：預設 off 行為與現況完全相同（既有測試不得改動）；值無效→`ConfigurationError`；缺 AUTO_REVIEW 或 API_RETENTION→拒絕啟動 | 擴充 `tests/test_auto_settings_and_replies.py` | config／auto_startup |
| 11 | `EvidenceSourceType.SYSTEM_JUDGEMENT`：`judged_by=0` 僅對該來源合法；`assess_evidence` 回 RELIABLE 且 `owner_confirmed` 仍為 False；Owner 來源 `judged_by=0` 仍被拒 | 擴充 `tests/test_version_evidence.py` | evidence_record 修改 |
| 12 | 整合（PostgreSQL）：全通過→同一交易內「核准 + 系統版本確認」→settle→+1；證據列的 actor_id=0、來源=system_judgement、不含標題 | 新 `tests/test_postgres_auto_version_confirm.py`（**對接 Ken**：確認案例名稱與資料集） | `apply_system_version_confirmation` |
| 13 | 整合：比對後插入另一筆投稿→重驗失敗→降級待審，不發獎 | 同上 | 7b 重驗 |
| 14 | 整合：重放同一 verdict（模擬 commit 未知）→不重複發獎、不重複建立版本 | 同上 | 確定性 decision_id |
| 15 | 整合：疑慮案件→待審；卡片文字含既有影片連結、不含標題；Owner 之後 `confirm_version` 仍可照舊運作 | 擴充 `tests/test_postgres_owner_cards.py`、`test_postgres_owner_versions.py` | `concern_lines` |
| 16 | 整合：Owner 對已被系統確認的影片再給不同版本→CONFLICT（凍結獎勵），不靜默覆蓋 | `tests/test_postgres_owner_versions.py` | 驗證既有 `_reconciled_identity` 行為，預期不需改碼 |
| 17 | 整合：retention 開啟時，比對後觀測表只有新影片一列、核准後被 purge；比對的舊影片資料不在任何表 | `tests/test_postgres_api_retention.py` | — |
| 18 | 日誌：只出現用量與結果類別（斷言 caplog 不含標題、頻道 id） | 新案例 | 日誌格式 |

## 8. 需要 Owner 決定的事項

說明格式：先講「要決定什麼、為什麼」，再給選項、代價與我的建議。**以下全部是建議，不是已核准決策。**

### 現在必須決定（擋住開工寫程式的）

**D4　要不要新增一種「系統判定」的證據來源？**
白話：目前帳上的版本證據只有「Owner 親手確認」這一種。要讓系統自動確認又不假裝成 Owner，必須在資料裡新增一種證據身分「系統判定」。這會動到證據檢查規則（核心模組），而且一旦寫入第一筆，舊版程式可能讀不懂（回滾下限變成新版，與 0004 的情況相同）。
選項：(a) 新增「系統判定」類型，帳面清楚可查；(b) 不新增，用 Owner 身分代簽——**違反「不偽造成 Owner」，不建議**。
建議：(a)，並把它視為與 0004 同等級的上線步驟，部署前清單要寫明「寫入第一筆系統證據後不可回滾到舊版」。

**D3　卡片和永久紀錄可以寫「與哪一支既有影片有疑慮」嗎？**
白話：Owner 要求卡片附上既有歌曲的連結。連結本身只是影片 id（使用者自己投稿的資料）；但「它們同頻道、時長接近」這個結論來自 YouTube，是否算「需限期保存的 API 資料」是政策解讀題。
選項：(a) 永久紀錄只存「疑慮代碼 + 對方影片 id」，不存頻道／時長／標題；(b) 紀錄只存代碼，卡片開啟時才即時重查再顯示連結（多花額度，且舊影片可能已被刪）。
建議：(a)。與現有「永久只存結論」的做法一致。若 Owner 或法務對此有疑慮，改選 (b)。

### 上線前要決定（可先寫程式，不擋 A0 之後的開發）

**D1　比對失敗（API 掛了、額度用完）時，新案件怎麼辦？**
白話：Owner 原則是「結果為待審，不是拒絕」。但案件一旦進待審就不會自動重試，API 故障期間所有新歌都會堆到 Owner 手上。
選項：(a) 照 Owner 原則，進待審；(b) 比對失敗時當作暫時無法判定，沿用現有「不寫入、30 分鐘後自動重試」。
建議：(b) 對 Owner 負擔較小，且同樣不拒絕、不發獎、安全性一樣；但這偏離你已核可的字面說法，所以需要你明確選擇。若維持 (a)，我照 Owner 原文實作。

**D2　版本關鍵字與相似度門檻**
白話：門檻越低，送到你手上的案件越多，但漏掉重複歌曲的機會越小。建議起始值：相似度 0.65、時長差 3 秒、關鍵字只看新投稿的標題。
選項：是否同時檢查「舊影片標題含關鍵字」；是否接受 0.65 起始，待 Ken 用樣本校準後再調。
建議：先用 0.65，上線初期（見 D7）觀察送審比例後再調；舊標題關鍵字暫不檢查（會讓所有與 live 版本相似的新歌都被攔，雜訊偏高）。

**D5　自動確認錯了之後怎麼補救？**
白話：自動確認為「新版本」並發獎後，如果事後發現其實是重複，Owner 可以用現有的合併功能把版本歸到同一首，但**已發的 Nickel 不會收回**。
選項：(a) 接受這個殘餘風險；(b) 另外加每日自動確認上限（例如 20 件），超過的一律送審；(c) 先不開自動發獎，只開自動確認，讓 Owner 抽查幾天。
建議：先 (c) 再 (a)；(b) 是新增需求，需另行核可，我暫不實作。

**D6　比對範圍只有最近 500 首，更舊的歌看不到，可以嗎？**
白話：這是我認為最大的風險來源（見第 6 節第 6 點）。500 首是你定的上限，也是為了控制額度；但歌曲庫變大後，舊歌的重複上傳會被自動放行。
選項：(a) 接受，並在 Owner 文件記錄；(b) 認可頻道的歌另外永久保存「頻道 id + 時長」索引做無限期比對（**與「比對完不寫表」衝突，需重新走保存政策核可，不建議**）。
建議：(a)，並在歌曲庫超過 500 首時重新評估。

**D7　要不要先跑「影子模式」？**
白話：先讓系統算出「如果開了會怎麼判」，只記錄用量與結果類別（不記標題、頻道），但仍把所有案件送 Owner。幾天後看送審比例合不合理，再真的開啟自動確認。
建議：要。這需要多一個模式值（例如 `shadow`），屬於新增範圍，請 PM 決定是否納入 A 階段。

**D8　認可頻道清單空白時，要拒絕啟動，還是只是不自動通過？**
建議：只是不自動通過（與現況一致，且不阻擋其他功能），啟動時記一行警告。

**D9　開關名稱**：暫定 `JUKEBOX_AUTO_VERSION_CONFIRM`，請 PM 確認。

## 9. 摘要與下一步

- 本文件盤點了七個相關模組，確認 0004 與保存機制檔案已存在；設計的主體是新增純函式模組 `version_concerns.py`、批次查詢 `lookup_many`、一個系統判定證據類型，以及在寫入交易內重驗比對基礎。
- 不需要新的 migration，不新增套件，不保存任何標題；比對的舊影片資料只在記憶體。
- A0 之後建議順序：先走 D3／D4 決定，再依第 7 節從第 1 步開始（純函式、離線測試），整合測試與 Ken 對接。

---

# 追加：Owner 第二輪決定的設計影響（2026-11-02）

本節為追加。仍是唯讀設計，沒有改任何既有檔案或資料。以下「Owner 已決定」只指 team-lead 轉述的內容；口頭「可以」尚非正式核准，會標明。

## 10. 既有首筆案件標為「音源版」

**Owner 決定（2026-11-02 更新）**：首筆案件決定「確認版本」，不拒絕。由 Owner 在 Discord 案件卡片操作（`/owner list` →「確認為新的錄音版本」），**不是改資料庫**，也不由工程師執行。該歌是否為 Hypnotize **尚未驗證**，由 Owner 看過影片後再按。「音源版」只是 Owner 判斷依據文字中的說明，系統沒有這個類別。

**查核結果**

- **[事實] 沒有名為「音源版」的版本類別。** `VersionClaimKind`（`bot/evidence_record.py:24-33`）只有 `SAME_RECORDING_AUDIO_MV`、`REMIX`、`LIVE`、`COVER`、`RE_RECORDING`、`REMASTER`、`EDIT`。`bot/version_categories.py:54-70` 說明 `SAME_RECORDING_AUDIO_MV` 的意思是「MV 與音源屬於同一個錄音」，要有證據並對應到另一支影片，**不是**「這支是音源版」的標籤。
- **[事實] 沒有類別的「基本錄音版本」是合法的。** `resolve_video_version` 規則（`bot/version_evidence.py` 的 docstring）：可靠證據若沒有宣告類別（`version_kind=None`），就確認它所主張的基本錄音版本。卡片文字也明說基本確認「不宣稱特定類別（MV、純音源、重製等）」（`bot/owner_card_versions.py` 的 `PROMISE_LINES`）。
- **[事實] 沒有地方記錄「這是音源版」這個標籤。** 版本只有不透明的 `rv-…` 識別碼（`new_version_identity`）；人類可讀的說明只能放在證據的 `source_reference` 與 `reason` 自由文字裡。
- **[未驗證] 該案件是不是 Hypnotize。** 系統刻意不存標題，我也沒有連線，無法確認。CLAUDE.md 只提到首筆案件 `discord:1565766654502113430`；請執行的人在 Discord 看影片本身確認。

**需 Owner 決定（我沒有新增任何類別）**

| 選項 | 效果 | 代價 |
| --- | --- | --- |
| A. 用現有「基本確認」（無類別），在判斷依據文字寫「Owner 判斷為音源版（Hypnotize）」 | 立刻可行，不改程式；版本成立，之後同版本不再重複發獎 | 「音源版」只是一段文字，系統不會理解它；之後若有人投該歌的 MV，是否算同一個錄音仍要 Owner 另外判斷 |
| B. 新增一個「音源版」類別 | 之後可機器辨識 | 要改核心列舉與規則（影響所有人的版本判斷，也有回滾相容問題）。**我不建議，也不會自行做。** |

建議 A。實際寫入由 Owner 用卡片流程（或 `/owner confirm_version`）執行，不由工程師執行。

**與自動確認的關係**：自動流程只會建立「無類別的基本版本」，不宣稱音源版、MV 或任何類別。這與本案一致。

## 11. 14 天清除與 API 即時重查：逐項核對 `docs/api-retention-release.md`

**B1（14 天保存）已獲 Owner 正式核可（2026-11-02 更新）。** API 即時重查的政策範圍（下表第 1、2 列）仍待 Owner 另行確認。下表列出與本設計不一致或需要正式確認的地方。**結論：沒有發現我們的設計違反 14 天清除機制本身，但第 1、2、3 列（Google 回覆範圍、衍生資料條款、「標題不用來判斷版本」的措辭）需要 Owner 正式確認。**

| # | 對照項目 | 現有文件／事實 | 本設計 | 判定 |
| --- | --- | --- | --- | --- |
| 1 | **Google 回覆的範圍** | `docs/youtube-policy-confirmation.md`：回覆只涵蓋「後端用於驗證影片 metadata（例如時長）」與獎勵條件；明寫「若實作改變需重新詢問」、「未逐項審查資料保存與快取方式」 | 用標題與頻道跨影片比對來判斷版本疑慮 | **[不一致風險]** 比對標題、頻道不是「驗證時長」。是否仍在已確認範圍，**我不能判斷**。需 Owner 決定是否再向 Google 詢問。 |
| 2 | **衍生資料條款** | `docs/api-data-lifecycle-design.md:23`：III.E.4.h「不得使用 API 資料建立新的衍生資料或指標」 | 疑慮旗標（同頻道、相似度）是由 API 資料算出的衍生結果；只存在記憶體並立即丟棄時沒有保存衍生資料，但**仍是使用 API 資料建立衍生判斷**。若永久保存疑慮代碼與對方影片 id（D3），則保存了一小部分衍生結論 | **[待確認]** 需 Owner（或 Google）判斷。 |
| 3 | **「標題不用來判斷版本」** | `docs/deploy/auto-review-nickel-enable.md` 寫「標題只能撤回信任…也不用來判斷版本」，並寫「官方資料與認可頻道都不能證明版本」 | 標題只產生疑慮（撤回信任），這部分一致。**但「沒有疑慮 → 自動確認為新版本」等於把『無疑慮』當成版本為新的正面依據**，與「官方資料不能證明版本」的字面不一致 | **[措辭不一致]** 需 Owner 確認改寫成：「系統判定（無疑慮）不是證明，而是 Owner 授權的自動判斷，證據記為系統判定」。 |
| 4 | 14 天保存 | `RETENTION_DAYS = 14`（`bot/database/api_observations.py:14`）；0004 CHECK 上限 30 天（0004:15） | 新影片的時長與頻道仍只進觀測表；自動核准的案件在同一交易清空（`auto_review_store.py:110-111`）；有疑慮的待審案件保留至到期，由既有重驗路徑處理 | 一致 |
| 5 | 「永久歷程只存結論」 | release 第二、三節：快照／歷史／證據／決定／回執只含 `DurationVerdict`；理由文字不含頻道與取得時間 | 新增永久內容：疑慮代碼、（D3）對方影片 id、系統版本證據（來源文字不含標題與頻道） | **新類別的永久資料**，需 Owner 確認（對應 D3） |
| 6 | 記憶體中的資料 | `api-data-lifecycle-design.md:47`：記憶體「一次巡檢中的查詢結果」列為不適用 | 每件新投稿把最多 500 首的標題、頻道、時長暫存在記憶體 | 範圍變大但仍是記憶體；**實作必須保證：不快取（無 `lru_cache`）、不寫日誌、不放進例外訊息、函式結束即釋放**（對應 TDD 第 5、18 步） |
| 7 | 備份 | release 第九節：備份期限只是估算，非官方保證 | 沒有新增任何寫入 API 值的表 | **[推測]** 不增加備份暴露面；未驗證 |
| 8 | 開關預設與依賴 | release 第三節：開關關閉＝舊行為 | 新開關預設 off；開啟須先開 AUTO_REVIEW 與 API_RETENTION，否則拒絕啟動 | 一致 |
| 9 | 回滾 | release 第六節：寫入含結論的快照後最低可回滾版本是 R1 | 再加一道單向門：寫入第一筆 `system_judgement` 證據後，舊版程式可能讀不懂（**[推測]**） | **新增一項回滾限制**，部署前清單要補（對應 D4） |
| 10 | API 用量 | enable 文件：每輪最多 20 次、每次 1 單位 | 每件新投稿最多 11 單位 | 文件數字需更新（本文第 6 節第 8 點） |
| 11 | 已刪除／私人化舊影片 | release 沒有涵蓋此情境 | 略過，不算失敗 | 新規則，需寫進文件 |

## 12. 階段 B：/exchange 失敗與 DM 失敗都退點；卡住案件由 Owner 自己看

**Owner 決定（轉述）**：/exchange 失敗與 DM 失敗都退點；卡住案件由 Owner 自己看（唯讀偵測，Owner 可見）；不做自動重送。

**[事實] 現況已有大部分機制，只有一處與「都退點」字面不同。**

| 情況 | 現有行為 | 位置 |
| --- | --- | --- |
| 沒有符合條件的歌／Nickel 不足／前一筆仍處理中／待確認 | **根本沒有扣點**，也不派送，因此沒有東西可退 | `bot/exchange_delivery.py:179-186` |
| DM 被證明「未送出」（`NOT_SENT`） | 同一流程立即退 1 點，`credit_ledger` 寫 `delivery_refund`；退款失敗會留在待退清單，下一次 /exchange 或巡檢重試，恰好一次 | `exchange_delivery.py:214-231`；`submission_repository.py:484-503`；`exchange_delivery_store.py:179-186`；ledger 唯一鍵 `uq_ledger_recommendation_reason`（0001:101） |
| DM 結果不明（逾時、斷線、例外） | **不退點、不重送**，標為 `uncertain`，回覆「扣點與交付待確認」 | `exchange_delivery.py:30, 151-164, 248-249` |
| 退款資料矛盾 | 停止，不猜測修補，回報需人工 | `exchange_delivery.py:223-225` |
| 被補償事件擋住 | 標為 `held_for_review`，不派送不退款 | `exchange_delivery.py:243-244` |

**不一致點（需 Owner 決定）**：Owner 說「DM 失敗都退點」。如果「失敗」包含「結果不明」，就與現有「只有證明未送出才退」的規則相反（程式註解 `exchange_delivery.py:219` 稱為 proof gate）。結果不明時 Discord 可能其實已送出，自動退點會讓使用者「拿到歌又拿回點」。

| 選項 | 效果 | 代價 |
| --- | --- | --- |
| A（建議） | 維持現狀：證明未送出→自動退；結果不明→不自動退，進 Owner 可見的卡住清單，由 Owner 判斷後用既有補償功能處理 | 結果不明的使用者要等 Owner；但不會多退 |
| B | 結果不明也自動退 | 可能多退；違反現有帳務「無證據不動帳」原則；需改核心規則 |
| C | 結果不明超過 N 小時仍無人處理才退 | 需要新規則與新期限，屬新需求 |

**Owner 可見的唯讀偵測：[事實] 資料層已有，但我沒讀到 Discord 端是否已經呈現。**

- 有：`owner_exchange_summary`（`submission_repository.py:523`，各狀態計數與 `refund_pending`）、`owner_pending_reservations`（:285）、`owner_pending_incidents`（:405）。
- **[未讀]** `bot/discord_owner_review.py` 與 `owner_cli.py` 是否把這些接成 Owner 指令；請 PM 或 Ken 確認。
- 缺口（**[推測]**）：`owner_summary` 只回計數，不告訴 Owner「卡了多久」。若要做到「Owner 一眼看到卡住」，建議新增唯讀查詢 `stuck_exchanges(older_than)`：列出 `reserved`／`sending`／`uncertain`／`failed 且未退` 超過期限的操作編號與時間（不含連結、不含標題）。純讀取，不需 schema 變更。
- 「不做自動重送」與現況一致（`uncertain` 明確寫「不會自動重送」）。

**Owner 澄清（2026-11-02）：「交換」的語意。** 交換不是把歌從資料庫移走，而是給對方資料庫中隨機一首歌的連結；仍排除自己的投稿與已收過的歌（與現有 `recommendations` 的「每人每首只收一次」唯一索引、歌曲保留在目錄中的做法一致，不需改動）。歌曲池不會因交換而減少。

**另需注意**：目前 /exchange 預設回覆「未開放」，正式流程由 `formal_exchange_enabled` 開關控制（`bot/discord_app.py:127-131`）。上述退點機制在開關關閉時不會被觸發。

## 13. 對 C 階段的影響：/daily 自訂送達時間

**Owner 決定（轉述）**：使用者自行安排送達時間；粒度為整點；預設 20:00（Asia/Taipei）。Nickel 不足時要發私訊「Nickel不夠了…快去分享歌曲，賺點Nickel！」。推薦者在私訊中半遮蔽顯示。歌曲池為空但 Nickel 足夠時的訊息尚未決定。

### 13.1 現有 daily SQL 能否支援每人自訂時間

**結論：不能直接支援，但只需加法式變更；目前沒有任何排程程式。**

| 項目 | 現況（[事實]） | 缺口 |
| --- | --- | --- |
| 偏好欄位 | `users.daily_enabled BOOLEAN`（0001:17）；`set_daily` 只更新這一欄（`bot/database/repository.py:56-66`） | **沒有時間欄位** |
| 每日一筆限制 | `daily_dispatches` 有 `UNIQUE (discord_user_id, dispatch_date)`（0001:124），`dispatch_date` 註解為 Asia/Taipei 日曆日 | 這個限制與「幾點送」無關，**可直接沿用** |
| 狀態 | `status IN ('pending','sending','delivered','failed','uncertain')`；`pending` 時必須沒有 song／recommendation，其餘狀態必須都有（0001:128-135） | **沒有「Nickel 不足」或「歌曲池空」的狀態**。要記錄，需放寬這兩個 CHECK（不是純加法），或另開表 |
| 權限 | `0002_application_privileges.sql:19-28` 的授權清單**沒有 `daily_dispatches`**，應用程式角色對它沒有權限 | 需新增 migration 授權 |
| 排程 | `bot/` 內沒有任何程式讀寫 `daily_dispatches`（grep 只有 `set_daily` 使用 `users`）；表註解寫「daily delivery is not implemented」 | 整個派送迴圈都是新的 |
| 指令 | `/daily` 目前是 on／off，說明「每日推薦尚未開放」（`bot/discord_app.py:162-172, 201`）；契約在 `bot/development_command_contract.json` | 加時間參數會改指令契約，指令同步需 Owner 另行授權（`bot.command_sync` 需明確 opt-in） |

**建議的加法式 schema（草案，不是已核准）**

- 新表 `daily_preferences (discord_user_id PK → users, delivery_hour SMALLINT NOT NULL CHECK (delivery_hour BETWEEN 0 AND 23), updated_at)`。未設定的人視為預設 20。選新表而非改 `users` 的理由：`users` 含 `credit_balance`（受保護的帳務表），雖然 `ADD COLUMN` 不動餘額，但避免碰它比較省事。**[推測]** 兩種做法技術上都可行。
- 新表 `daily_notices (discord_user_id, dispatch_date, notice_kind, status, created_at, UNIQUE (discord_user_id, dispatch_date, notice_kind))`，記錄「Nickel 不足」通知，讓重啟不重發，也不必動 `daily_dispatches` 的 CHECK。
- 新 migration `0005`：上述兩表與 `GRANT`（`daily_dispatches` 的 SELECT／INSERT／UPDATE，新表的相應權限，唯讀角色 SELECT）。依 CLAUDE.md，schema 變更須 Owner 核准後才能套用。
- 現有 `users.daily_enabled` 保留，不遷移資料。

### 13.2 需 Owner 決定的細節

| # | 要決定什麼 | 白話影響 | 選項與建議 |
| --- | --- | --- | --- |
| C1 | 時間粒度 | 已決定整點 | 已決定。注意：大家都用預設 20:00 時，會同時發很多私訊，Discord 有速率限制 |
| C2 | 預設值 | 已決定 20:00 | 已決定。未設定的人是「開了 /daily on 就是 20:00」，還是「一定要先選時間」？**建議前者** |
| C3 | 時區 | 固定 Asia/Taipei | 已決定。台灣無夏令時間，沒有跳一小時的問題；海外使用者看到的是台北時間，指令說明要寫明 |
| C4 | 同一小時內的發送順序 | 同一小時所有人一起發會撞速率限制 | **建議**在整點後的 60 分鐘內依使用者編號錯開 |
| C5 | 停機後補發 | 預計 20:00 發，但 20:30 才重啟 | (a) 同一天內補發；(b) 錯過就跳過。**建議 (a)**；每人每天最多一則由 `UNIQUE (使用者, 日期)` 保證 |
| C6 | 改時間的生效 | 當天已發過再改時間 | **建議**：當天已發就不再補發，改時間從隔天開始；當天尚未發則依新時間 |
| C7 | 時間參數怎麼輸入 | 影響指令契約與全域指令同步 | 例如 `/daily on hour:20`，整數 0–23 並提示台北時間（**建議**） |
| C8 | 關閉 daily 時 | 時間設定是否保留 | **建議**保留，重新開啟沿用 |

### 13.3 Nickel 不足要發私訊：與原計畫的差異

**變更（明確標出）**：原計畫（`CLAUDE.md` §9 §2：「只有在有點數且有可用歌曲時才自動派送」；該節是歷史參考，產品決策仍待 Owner 確認）是「點數不足就不派送、不通知」。Owner 新決定是點數不足時要發私訊，內容「Nickel不夠了…快去分享歌曲，賺點Nickel！」。這是**新增主動私訊**，也增加使用者被打擾的機會。

| 問題 | 影響 | 建議 |
| --- | --- | --- |
| 每天重複發送 | Nickel 為 0 又沒開始投稿的人，每天都會收到同一則提醒，容易被視為騷擾，也可能讓使用者封鎖 Bot（私訊從此失敗） | **建議**：每人每天最多 1 則；連續「不足」提醒只在前 N 天（建議 3）發，之後靜默，直到 Nickel 回到足夠再重新計。N 與是否自動關閉 daily 請 Owner 決定 |
| 是否動帳 | 提醒不應動帳 | 不扣、不加；只寫通知紀錄 |
| 私訊被拒（使用者關閉私訊） | 每天嘗試會一直失敗 | 失敗不重試，記失敗次數；連續失敗 N 次後停止嘗試。**建議**不自動關閉 daily，只停止通知 |
| 重啟重複 | 一天可能發兩則 | 由 `daily_notices` 的唯一鍵保證 |
| 判斷順序 | 先看點數再看歌曲池 | 點數不足→發提醒；足夠→檢查歌曲池 |

### 13.4 推薦者半遮蔽顯示

**[事實] 系統目前沒有儲存任何顯示名稱**（grep `display_name`／`global_name`／`nick` 在 `bot/` 沒有結果；`users` 只有 `discord_user_id`、餘額、`daily_enabled`、`created_at`）。**[事實]** `history` 目前刻意「不含寄件者身分」（`bot/database/repository.py` 的 `history` docstring）。所以在私訊裡顯示推薦者是**第一次**讓一位使用者的身分傳給另一位。

**名稱來源（需 Owner 決定）**

| 選項 | 效果 | 隱私與維運 |
| --- | --- | --- |
| A（建議） | 送出前用 Discord API 即時取得（例如 `fetch_user` 的 `display_name`），用完即丟，**不保存** | 不增加資料庫個資；多一次 Discord 呼叫；取不到（帳號已刪、速率限制）時退回「某位使用者」 |
| B | 投稿時存一份名稱 | 需新欄位、要處理改名與刪除，個資保存期限要另定；**不建議** |

**遮蔽規則草案**（純函式，可離線完整測試）

1. 以 `display_name` 去除前後空白後處理，單位為 Python 字元（code point）。
2. 先移除控制字元與零寬字元（`unicodedata.category` 為 `Cc`／`Cf`）。
3. 若剩 0 個字元，或第一個字元不是文字或數字（`str.isalnum()` 為 False，例如以表情符號開頭）：顯示固定的「＊＊＊」。
4. 有效長度 1：「＊」；2–3：第 1 個字 + 「＊＊」；≥4：第 1 個字 + 「＊＊」 + 最後 1 個字。星號數固定為 2，**不隨名稱長度變化**，避免洩漏長度。
5. 輸出前跳脫 Discord 標記與提及（`discord.utils.escape_markdown`、`escape_mentions`），並以 `AllowedMentions.none()` 發送，避免名稱含 `@everyone`、連結或樣式字元。
6. 絕不附上使用者編號或可點擊的提及。

**已知限制與隱私風險**

- 標準函式庫沒有字形群集切分；組合表情（ZWJ 序列）若落在最後一個字，可能被切半。規則 3 只擋掉表情開頭的名稱。**[推測]** 影響只是顯示怪異，不是洩漏。
- 半遮蔽不是匿名：名稱很獨特或伺服器很小時，「首字 + 末字」仍可能被猜出是誰。若 Owner 要真正匿名，應改為只顯示「某位成員」。
- 推薦者與接收者可能在不同伺服器；使用全域名稱而非伺服器暱稱，可降低跨伺服器關聯，但仍可辨識。
- 這是**資料揭露方向的變更**：推薦者並不知道自己的名稱（半遮蔽）會被顯示。是否需要在 `/share` 說明或取得同意，請 Owner 決定。

### 13.5 歌曲池為空但 Nickel 足夠

**尚未決定**。選項：(a) 不發任何私訊（最安靜，使用者可能以為壞了）；(b) 發「目前沒有可推薦的新歌，稍後再試」，頻率上限比照 13.3；(c) 保留 Nickel，隔天再嘗試。無論選哪個，**都不扣點**（與 /exchange 的 `NO_CANDIDATE` 一致）。建議 (b)。此項不阻擋資料表設計，只阻擋該情境的文案與測試。

### 13.6 C 階段的決定清單與阻擋關係

| 項目 | 需要決定 | 未決會擋住 |
| --- | --- | --- |
| 新增 `0005` migration（偏好表、通知表、權限） | Owner 核准 schema 變更 | 全部 C 階段實作 |
| 名稱來源 A／B 與遮蔽規則 | Owner 確認（13.4） | 派送訊息文案與測試 |
| 不足提醒的頻率上限（13.3） | Owner 決定天數 N | 提醒程式與測試 |
| 歌曲池為空的訊息（13.5） | Owner 決定 | 該情境的文案與測試 |
| 補發策略 C5、錯開 C4 | Owner 或 PM 確認 | 排程迴圈 |
| 全域指令同步（`/daily` 新參數） | Owner 另行授權 | 使用者實際看到新指令（不阻擋開發與測試） |


## 14. 「高度相同」規則：採 X（轉 Owner 審）與待 Owner 正式確認事項（2026-11-02）

### 14.1 已決定：採 X

Owner 規則是「驗證不過就改用 X」。第 5 項前提驗證不通過（見回報：沒有專屬「重複不發放」狀態；若以對應到既有版本標示，Owner 事後無法改判），所以：

- 同頻道 **且** 時長差 ≤3 秒 **且** 歌名高度相似，三者全成立時，案件轉 Owner 審，卡片註明「高度相同，建議不發放」。
- **不寫版本身分、不 reject、不新增狀態。** Y（以未通過表示）、Z（新增狀態）都不做。
- 使用者回覆文字只說明「已轉交審核，高度相似，尚未發放」，**不宣稱已重複不發放**。
- 實作上這是第 4 節兩個疑慮同時出現於同一支既有影片的情形：`SAME_CHANNEL_CLOSE_DURATION` 與 `SIMILAR_TITLE` 指向同一個 `other_video_id`。卡片文字由 `concern_lines` 產生；不新增 `ConcernCode`。
- 特徵測試（影片已確認版本後再確認不同版本會變 CONFLICT；REJECTED 經重審補發恰好一次）由 Ken 補，優先序低於主線；在那之前，上述結論是**靜態閱讀**，不是測試證明。

### 14.2 待 Owner 正式確認（我不替 Owner 做決定）

| # | 要確認什麼 | 白話影響 | 我的建議 |
| --- | --- | --- | --- |
| P1 | B1 例外與「資料庫只存影片 ID 與原始連結」的字面差異 | 0004 的 `jb2_api_observations` 存官方時長與頻道 ID（最多 14 天，決定後清空）。字面上多於「只存影片 ID 與連結」 | 確認 B1 例外仍成立；若不成立，自動核准流程無法使用時長與頻道，需整個重新設計 |
| P2 | 疑慮代碼的衍生結論 | 理由文字會寫「same_channel_close_duration」與對方影片 id，隱含「兩支同頻道」這個由 API 資料導出的結論（D3 已同意，這裡正式列為待確認） | 同意只存代碼與影片 id，不存頻道、時長、標題 |
| P3 | 儲存前整個不受理 | `PersistenceNotReady`（`bot/database/submission_repository.py:109-112`）時，資料庫未就緒或結算開關不一致，整筆投稿不受理、不留紀錄，因為連回執都寫不進去 | 接受：這是系統條件而非歌曲判斷；使用者會看到「資料服務暫時無法使用」類訊息。若 Owner 要求這種情況也必須留紀錄，需要另一個不依賴資料庫的紀錄機制，屬新需求 |
