# 自動審核與 Nickel 自動發放：部署與啟用

狀態：程式已完成（待 PR 審查）；**尚未部署、尚未啟用、真實 YouTube API／Session Pooler／Discord 都未驗收**。合併本 PR 不會開啟任何功能：兩個開關預設 off。

## 何時自動發放，何時仍待審

自動核准須**全部**符合（政策版本 `trusted-channel-v1`）：

1. 官方 Data API v3 `videos.list` 回應該影片；`uploadStatus=processed`、`privacyStatus=public`。
2. 不是直播或預告（`liveBroadcastContent=none`、沒有 `liveStreamingDetails`）。
3. 完整時長 60～720 秒（含邊界）。
4. `channelId` 在 Owner 名單 `YOUTUBE_TRUSTED_CHANNEL_IDS`（名單空白時一律不自動核准）。
5. `categoryId=10`（Music）。
6. 標題不含預告、片段、剪輯等排除字詞。標題**只能撤回信任**，不會拒絕，也不用來判斷版本，且不保存。

| 情況 | 結果 |
|---|---|
| 全部符合 | 記錄時長、以系統身分（actor 0，不是 Owner）自動核准 |
| 2、3 以外的信任條件不符 | 記錄時長 → 待 Owner `approve`／`reject` |
| 官方時長超出 60～720 | 審核未通過（可正式重審） |
| 影片不存在、私人、已刪除 | 驗證失敗 → 待 Owner |
| 直播、預告、處理中、無法判定時長 | 不寫入，稍後重試，Owner 可補時長 |
| API 失敗、限流、配額用盡 | 不寫入，不視為拒絕，每 5 分鐘的巡檢重試 |

**發獎**另外要求：審核通過、**錄音版本已確認**、該版本首位、沒有更早的未解決投稿。官方資料與認可頻道都不能證明版本，所以目前**新歌的版本都要 Owner 確認一次**；確認後立即自動結算。

> 既有規則：版本未知的較早投稿會擋住**所有**較晚的投稿（不只同版本）。任何一筆卡住的案件都會暫停後面的發獎，需由 Owner 確認版本或拒絕。首筆案件 `discord:1565766654502113430` 一旦開啟就屬於這種情況。

## Owner 在 Discord 的最短流程

1. `/owner list`：開啟最早一筆待處理案件的卡片（不需要案件編號）。
2. 待審核：按「確認通過」（只核准歌曲資格），或按「拒絕」並填理由；沒有時長時按「補時長」。
3. 按「下一筆」只切換顯示。操作後卡片會換成實際結果（待版本、待前序、已發 +1 或結果未知）。
4. 版本未確認時，卡片會明確顯示；目前仍用 `/owner confirm_version` → 按確認。卡片內的版本確認操作尚待 Owner 決定語意，見 [Owner 待審案件卡片](../owner-case-cards.md)。

## 設定名稱

| 名稱 | 說明 |
|---|---|
| `JUKEBOX_AUTO_REVIEW` | `on`／`off`；on 時必須有 API 金鑰 |
| `JUKEBOX_AUTO_SETTLEMENT` | `on`／`off`；必須與資料庫開關一致 |
| `JUKEBOX_API_RETENTION` | `on`／`off`；on 才把官方 API 數值只存短期表並執行清除（需先套用 migration 0004，見 [API 資料保存發行說明](../api-retention-release.md)）。**`JUKEBOX_AUTO_REVIEW=on` 必須搭配 `on`，否則拒絕啟動** |
| `YOUTUBE_TRUSTED_CHANNEL_IDS` | 逗號分隔的 `UC...` 頻道 ID，不接受名稱或 handle |
| `${JB_SECRET_DIR}/youtube_api_key.txt` | API 金鑰秘密檔（uid/gid 10001、0400），由 `YOUTUBE_API_KEY_FILE` 指向；不進 Git 或聊天 |

## 健康政策

`bot.readiness`／`bot.healthcheck` 要求資料庫 `settlement_enabled` 與 `JUKEBOX_AUTO_SETTLEMENT` 一致：資料庫開、模式關回報 `SETTLEMENT_ENABLED`；模式開、資料庫關回報 `SETTLEMENT_SWITCH_OFF`。開關值無效、名單格式錯誤或缺金鑰時，Bot 拒絕啟動且健康檢查失敗。

## 啟用順序（由 Owner 與夥伴安排，本 PR 未執行）

1. 先備份資料庫；依部署手冊更新映像。啟用自動審核前需要 migration 0004 與 `JUKEBOX_API_RETENTION=on`（順序見 [部署前清單](partner-predeploy-api-retention.md)）。
2. 只開自動審核（保存開關必須已開，否則 Bot 拒絕啟動）：放置秘密檔、填名單，`JUKEBOX_AUTO_REVIEW=on`，以 `compose.yaml` 加 `compose.auto-review.yaml` 啟動；驗收真實 API 回應。
3. 處理所有版本未確認的既有案件（包含首筆），否則後面的發獎會等待。**開啟前先決定首筆案件的處理**：開啟後若確認其版本，它會走一般流程領 +1。
4. 開發獎：停 Bot → `python -m bot.owner_cli settlement-switch preview --enabled on` → 用印出的 digest 執行 `write` → 設 `JUKEBOX_AUTO_SETTLEMENT=on` → 啟動 → `python -m bot.healthcheck`。
5. 同步 Owner 指令（`/owner duration` 是新指令）：Owner 指令只註冊在 `DISCORD_OWNER_GUILD_ID` 伺服器，**全域 `apply` 不會同步它**。由 Owner 依序執行：
   - `python -m bot.command_sync preview`：確認輸出的 `owner_guild.commands` 含 `owner`，並記下 `owner_guild.digest`。全域 `local_digest` 若與上次相同，表示一般指令未變。
   - 只在需要更新 Owner 指令時，`apply --target owner-guild --i-understand-this-calls-discord --confirm-overwrite-remote-commands --expected-digest <owner_guild.digest>`。此指令會覆寫 Owner 伺服器的指令集，請先確認該伺服器沒有其他手動建立的指令。
   - 一般指令未變時不需要 `--target global`。
   - 預覽與發佈都需要 `DISCORD_OWNER_GUILD_ID`、`DISCORD_OWNER_USER_ID` 與 `DISCORD_EXPECTED_APPLICATION_ID`；本文件不含真實值。

**關閉**：停 Bot → 模式改 off → `settlement-switch` 設 off → 啟動。

## YouTube 資料政策待辦（啟用前需確認）

- Google 回覆（Issue 576880673）只涵蓋 Discord 內分享獎勵與後端 metadata 驗證用途，並要求遵守其他全部條款；見 `docs/youtube-policy-confirmation.md`（PR #3）。
- 開發者政策 III.E.4 對存放的 API 資料設有 30 日刷新或刪除的要求。本版把**官方時長、channel ID、取得時間**寫入審核歷程，且歷程目前是只增不改，**尚未實作 30 日刷新或刪除**。啟用前須由 Owner 決定保存方案。政策頁面（2026-10-15 版）文字中未見影片／頻道 ID 的豁免，但內部衍生結果是否適用仍未決。保存位置清單、設計與待決問題見 [API 資料生命週期設計](../api-data-lifecycle-design.md)。
- 本版不保存標題；API 預設配額 10,000 單位／日，每次 `videos.list` 1 單位，每輪巡檢最多 20 次。
