# Discord 政策矩陣與人工 Portal 清單

**查核日期**：2026-10-28
**票號**：Docker／Gateway 部署 16（`docs/tickets/docker-gateway-deployment/16-discord-policy-matrix.md`）
**性質**：唯讀文字查核。本文件**不是** Discord 官方認證，也**不保證全面合規**。它只記錄這次能取得的官方文件、既有程式證據與尚未解決的缺口。
**本票未做的事**：未修改程式碼、未修改 Developer Portal、未執行任何 Discord 連線、未 commit／push。

## 1. 查核方法與限制

| 來源 | 結果 |
| --- | --- |
| Developer Policy：`https://support-dev.discord.com/hc/en-us/articles/8563934450327-Discord-Developer-Policy` | **HTTP 403，無法取得原文**。`docs.discord.com/developers/policies/developer-policy.md` 會 307 轉址到同一頁，同樣無法讀取。 |
| Developer Terms：`https://support-dev.discord.com/hc/en-us/articles/8562894815383-Discord-Developer-Terms-of-Service` | **HTTP 403，無法取得原文**。mirror 同樣轉址失敗。 |
| Developer Documentation：`https://docs.discord.com/developers/intro` | 可讀。索引位於 `https://docs.discord.com/llms.txt`。 |
| Gateway（privileged intents、reconnect）：`https://docs.discord.com/developers/topics/gateway.md` | 已讀取。 |
| Privileged intent 審查：`https://docs.discord.com/developers/gateway/getting-started-with-privileged-intent-review.md` | 已讀取。 |
| Rate limits：`https://docs.discord.com/developers/topics/rate-limits.md` | 已讀取。 |
| Interactions 回應：`https://docs.discord.com/developers/interactions/receiving-and-responding.md` | 已讀取。 |
| Message `allowed_mentions`：`https://docs.discord.com/developers/resources/message.md` | 已讀取。 |
| 社群搜尋摘要（僅輔助，非原文）：Developer Policy 標示最後更新 2024-06-06、生效 2024-07-08；Terms 要求開發者保有準確描述資料蒐集與刪除方式的隱私政策 | 搜尋摘要，**未取得原文**，不得視為已驗證。 |

**限制**：Developer Policy 與 Developer Terms 原文無法取得，因此所有依賴這兩份文件的條款一律標為「待確認」，不得以記憶或摘要補成已驗證。Discord 的 DAVE、OAuth2 scopes、Bot 權限的官方頁面也未於本票逐頁讀取（見第 5 節）。

## 2. 狀態定義

| 狀態 | 意義 |
| --- | --- |
| 已驗證 | 有官方來源（已讀取原文段落）且有程式證據，並說明驗證方式。 |
| 需修正 | 程式與官方要求或工程建議不符，需改程式。 |
| 需人工設定 | 必須在 Developer Portal 或人工流程完成，程式無法確認。 |
| 待確認 | 無法於本機驗證、官方原文未取得，或需產品／法務決策。不假設答案。 |
| 不適用 | 專案目前沒有對應功能，並附證據。 |

## 3. 政策矩陣

各列的「類別」欄區分：**官方明文**（官方文件寫明的要求）、**工程建議**（官方文件建議、非強制）、**Portal 人工**（需在 Developer Portal 完成）、**無法驗證**。

| 編號 | 項目 | 類別 | 狀態 | 來源連結 | 適用條件 | 程式證據（路徑:行） | 驗證方式 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| D-01 | Developer Policy 適用條款（含使用者資料、DM、濫用等） | 官方明文 | 待確認 | support-dev 連結（403） | 所有 Bot 與 OAuth2 應用 | 不適用於程式碼本身 | 無法取得原文。需人工在瀏覽器開啟官方頁面，逐條對照本矩陣各列。 |
| D-02 | Developer Terms of Service（含隱私政策要求、Bot 與 OAuth2 合法流程） | 官方明文 | 待確認 | support-dev 連結（403） | 同上 | 不適用 | 同 D-01。社群摘要指出 Terms 要求隱私政策與刪除方式，但未經原文確認。 |
| D-03 | Bot 使用合法 Bot 授權，不使用 self-bot 或一般使用者 Token | 官方明文 | 待確認 | D-01、D-02 | Bot 應用 | `bot/config.py:114` 讀取 `DISCORD_BOT_TOKEN`；`bot/discord_http.py:67` 僅以 `Bot {token}` 授權；未見使用者 Token 路徑 | 程式面已讀取；官方條款原文待 D-01／D-02 確認後才能定為已驗證。 |
| D-04 | OAuth2 scopes 最小化 | Portal 人工 | 需人工設定 | 官方 OAuth2 scopes 頁未於本票讀取 | 邀請連結或安裝設定 | repo 中未見 scope 設定（`grep scope` 僅命中 Python 內部名稱） | 人工在 Developer Portal 的 OAuth2 頁面檢查 scopes，只保留 `bot` 與 `applications.commands`（是否還需其他 scope，待確認）。 |
| D-05 | Bot 權限（permissions）最小化 | Portal 人工 | 需人工設定 | 官方 permissions 頁未於本票讀取 | 伺服器邀請時的權限 | repo 中未見權限整數或邀請設定 | 人工檢查邀請連結的權限位元，只保留實際需要的權限。 |
| D-06 | Gateway intents 最小化 | 官方明文（最小化為工程建議） | 已驗證 | `https://docs.discord.com/developers/topics/gateway.md`（2026-10-28 讀取） | 使用 Gateway 的 Bot | `bot/discord_app.py:75` 使用 `discord.Intents.none()`；`bot/gateway_listener.py:121` 以 `reconnect=False` 連線（見 D-09） | 讀程式碼確認；未實際連線。 |
| D-07 | Privileged intents 申請、門檻與續審 | 官方明文 | 不適用 | Gateway 頁（列出 `GUILD_PRESENCES`、`GUILD_MEMBERS`、`MESSAGE_CONTENT` 為 privileged）；審查頁：100+ 伺服器需經驗證後才能使用，超過 10,000 位可見使用者需審查 | 目前程式不請求任何 privileged intent | `bot/discord_app.py:75` 為 `Intents.none()` | 讀程式碼確認。若未來需要 `MESSAGE_CONTENT` 等，必須先申請；Portal 上的實際開關見第 4 節 M-1。 |
| D-08 | Rate limit、429、`Retry-After` 與 `retry_after` | 官方明文（Discord 要求依標頭等待） | 需修正 | `https://docs.discord.com/developers/topics/rate-limits.md`（2026-10-28 讀取） | 所有 REST 呼叫，含 DM 與 interaction 回應 | `bot/discord_http.py:113-114`：`status >= 400` 直接丟出 `DiscordRestError`，未讀取 `Retry-After` 或 `retry_after`，也沒有等待後重試。DM 派送經 `RestDirectMessageSender` 使用此路徑 | 讀程式碼確認。修正方向：遇 429 讀取 `retry_after`（或 `Retry-After`）並等待後重試，同時設定上限避免無限等待；不得在 bucket 耗盡時繼續送出。 |
| D-09 | 斷線重連、Resume 與退避 | 官方明文（Resume 與 close code 規則）＋工程建議 | 待確認 | Gateway 頁的 Reconnect、Invalid Session、close code 段落（2026-10-28 讀取） | Gateway 連線 | `bot/gateway_listener.py:121` 的 `client.connect(reconnect=False)`（開發用 listener，不自動重連）；正式入口 `bot/__main__.py:39` 使用 `client.run(...)`，其重連行為依 discord.py 預設，本票未驗證 | 讀程式碼確認。需實際連線測試重連與 Resume 行為；未執行。 |
| D-10 | Interaction 3 秒內初次回應、defer 與 15 分鐘 token | 官方明文 | 已驗證 | `https://docs.discord.com/developers/interactions/receiving-and-responding.md`（2026-10-28 讀取） | Slash command 等 interaction | `bot/discord_app.py:121`、`:127`、`:205` 使用 `defer(ephemeral=True)`；`bot/discord_http.py:85-98` 以 15 分鐘內的 interaction token 編輯原始回應 | 讀程式碼與官方頁比對；未實際連線測試。 |
| D-11 | 重複 interaction 的處理 | 工程建議 | 待確認 | 官方頁未描述重複投遞的偵測方式（2026-10-28 讀取） | 同一 interaction 被重送時 | 未於本票確認。交換流程以狀態與資料庫保護（`bot/exchange_delivery.py`），但未以測試驗證重送情境 | 需設計或測試重送情境；本票未執行。 |
| D-12 | 防止非預期 `@everyone`、`@here` 與 mention | 工程建議（官方允許以 `allowed_mentions` 控制） | 已驗證 | `https://docs.discord.com/developers/resources/message.md`（2026-10-28 讀取）；官方預設 interaction 只解析 user mention | 所有發出的訊息 | `bot/discord_app.py:75` 設 `AllowedMentions.none()`；`bot/discord_app.py:207`、`bot/discord_http.py:85`、`:98`、`bot/discord_owner_review.py:40`、`:107`、`:173`、`bot/gateway_listener.py:56` 皆傳入不 mention 設定；`NO_MENTIONS` 常數值本票未另行讀取 | 讀程式碼確認；未實際發送訊息測試。 |
| D-13 | 使用者資料蒐集最小化 | 官方明文（Terms 要求隱私政策說明蒐集內容）＋工程建議 | 待確認 | 原文待 D-02 確認 | 所有儲存的使用者資料 | `exchange_delivery.py` 處理 `discord_user_id`；MySQL 儲存投稿 URL、交換紀錄（見 `bot/database/schema.sql`） | 需列出完整欄位清單，並決定哪些必要；本票未做。 |
| D-14 | 資料保存期限與刪除機制 | 官方明文（Terms 要求說明刪除方式）＋產品決策 | 待確認 | 原文待 D-02 確認 | 所有使用者資料 | `bot/` 中未見 retention、purge 或使用者刪除流程（`grep` 無命中） | 需產品決策保存期限與刪除流程，並寫入隱私政策；目前無程式實作。 |
| D-15 | DM 與派送的同意、停用與防騷擾 | 官方明文（原文待確認）＋產品決策 | 待確認 | Social SDK 的 DM 頁表示「只能回應使用者動作，不得自動發送」，但該頁限 Social SDK，不直接適用於 Bot；Bot DM 規則待 D-01 確認 | `/exchange` 與未來的每日派送 | 目前 `/exchange` 的 DM 發送於使用者動作後才進行（`bot/exchange_delivery.py:157`）；每日派送 `bot/gateway_service.py:49-57` 僅回傳「未保存」，不啟動排程、不發 DM | 目前行為與「回應使用者動作」一致（讀程式碼確認）；每日派送的 opt-in、停用與頻率限制尚未實作，需另行設計。 |
| D-16 | 隱私政策 URL 與支援管道 | Portal 人工（Terms 要求，原文待確認） | 需人工設定 | D-02（待確認） | 所有公開 Bot | repo 中未見隱私政策文件；`ROADMAP.md`、`README.md` 僅提及 UNRESOLVED 與隱私一詞 | 人工撰寫隱私政策，填入 Developer Portal 的 Privacy Policy URL 欄位，並提供支援管道。 |
| D-17 | 語音與 DAVE（語音端到端加密） | 官方明文（僅適用於語音） | 不適用 | DAVE 官方頁未於本票讀取（待補） | 本專案只分享連結，不加入語音頻道 | `grep` 於 `bot/*.py` 未見 voice、DAVE 相關程式；`bot/gateway_listener.py:121` 的 `connect` 為 Gateway 連線，非語音 | 讀程式碼與 grep 確認。若未來加入語音功能，需重新查核。 |
| D-18 | 第三方音樂與 YouTube 政策依賴 | 無法驗證（Discord 查核不涵蓋） | 待確認 | `docs/youtube-policy-confirmation.md`（Google 2026-10-24 有範圍回覆） | YouTube 驗證與 Nickel 獎勵 | 同文件：回覆僅涵蓋 Nickel 的 Incentivized Engagement 與後端 metadata 驗證用途；其餘政策仍 UNRESOLVED | 保留既有 UNRESOLVED 狀態。Discord 查核不代替第三方授權。 |
| D-19 | Spotify 元資料與來源政策依賴 | 無法驗證 | 待確認 | 未於本票查核 Spotify 條款 | Spotify 投稿相關 | `bot/urls.py:70` 目前拒絕 Spotify（與 CLAUDE.md 與 §9 §2 的既定描述不一致，見既有 `9.3` 清單） | 保留 UNRESOLVED，需另行確認 Spotify 條款與元資料來源。 |

## 4. 需要人工在 Developer Portal 或人工流程完成的項目

這些項目程式無法確認，必須由專案負責人人工完成。本票**不修改** Developer Portal。

| 編號 | 人工項目 | 原因 | 完成後如何確認 |
| --- | --- | --- | --- |
| M-1 | 確認 Privileged Gateway Intents（Presence、Server Members、Message Content）皆為關閉 | D-07：目前程式不需要，開啟會增加審查負擔 | 截圖存證或人工勾選，並記錄日期 |
| M-2 | 檢查 OAuth2 scopes 只含必要項目 | D-04 | 記錄實際使用的邀請連結 scope |
| M-3 | 檢查邀請權限位元只含必要權限 | D-05 | 記錄權限整數與對應功能 |
| M-4 | 填寫 Privacy Policy URL 與支援管道 | D-16 | 填寫後記錄 URL（不記錄秘密） |
| M-5 | 人工讀取 Developer Policy 與 Developer Terms 原文，逐條對照本矩陣 | D-01、D-02 無法由本機取得 | 紀錄閱讀日期與原文最後更新日，並更新本文件狀態 |
| M-6 | 若未來要用 `MESSAGE_CONTENT` 或其他 privileged intent，先提出申請 | D-07 | 申請前更新本文件 |

## 5. 無法於本機查核的項目

1. **Developer Policy 與 Developer Terms 原文**（D-01、D-02）：support-dev 回傳 HTTP 403，mirror 轉址也無法取得。本矩陣所有依賴這兩份文件的條款均為「待確認」。
2. **OAuth2 scopes 與 Bot 權限的官方頁面**（D-04、D-05）：本票未逐頁讀取。
3. **DAVE 官方頁面**（D-17）：本票未讀取；判定依據為程式碼搜尋。
4. **實際連線行為**（D-09、D-10、D-12）：未執行 Discord 連線，行為判定僅依程式碼與官方文件。
5. **第三方政策**（D-18、D-19）：Discord 查核不涵蓋，保留 UNRESOLVED。

## 6. 統計

| 狀態 | 數量 | 項目 |
| --- | --- | --- |
| 已驗證 | 3 | D-06、D-10、D-12 |
| 需修正 | 1 | D-08 |
| 需人工設定 | 3 | D-04、D-05、D-16 |
| 待確認 | 10 | D-01、D-02、D-03、D-09、D-11、D-13、D-14、D-15、D-18、D-19 |
| 不適用 | 2 | D-07、D-17 |
| **合計** | **19** | |

## 7. 結論與限制

- 本矩陣**不是**完整合規聲明，也不代表 Discord 官方審查通過。
- 已驗證的 3 項僅代表「已讀官方段落並讀過程式碼」，不代表實際連線或發送測試已通過。
- 本票發現 1 項需修正：D-08（REST 層 429 未處理），建議另開票處理，不在本票修改。
- 第三方授權（YouTube、Spotify）維持 UNRESOLVED，不以 Discord 查核代替。
