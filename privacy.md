---
title: JukeBox101 隱私權政策 Privacy Policy
---

# JukeBox101 隱私權政策 / Privacy Policy

最後更新 / Last updated：2026-11-03（rev. 2）

本政策說明 Discord Bot「JukeBox101」（以下稱「本服務」）如何蒐集、使用與保存資料。使用本服務即表示你同意本政策。

This policy explains how the Discord bot "JukeBox101" (the "Service") collects, uses, and stores data. By using the Service, you agree to this policy.

---

## 1. 我們蒐集的資料 / Data we collect

本服務只在你主動使用斜線指令（例如 `/share`、`/balance`、`/history`）時處理資料：

- **Discord 使用者 ID**：用來識別你的投稿與 Nickel 餘額。
- **投稿內容**：你提交的 YouTube 影片連結、投稿時間、審核狀態與操作紀錄。
- **YouTube 影片公開資訊**：透過 YouTube Data API 取得影片標題、頻道、長度與公開狀態，用於審核。
- **Nickel 紀錄**：餘額與發放／異動紀錄。
- **交換紀錄**：你用 `/exchange` 交換的歌曲、扣除的 Nickel 與時間。
- **每日推薦設定**：你是否開啟 `/daily`，以及已傳送給你的推薦紀錄（用來避免重複推薦）。
- **互動來源驗證**：處理指令時會檢查請求來自哪個伺服器（不接受私訊），但不會在資料庫中保存伺服器名稱、頻道或成員資料。

The Service only processes data when you use its slash commands (e.g. `/share`, `/balance`, `/history`):

- **Discord user ID** — to link your submissions and Nickel balance to you.
- **Submissions** — the YouTube link you submit, submission time, review status, and operation records.
- **Public YouTube video information** — title, channel, duration and availability, fetched via the YouTube Data API for review.
- **Nickel records** — balance and transaction history.
- **Exchange records** — songs you obtain with `/exchange`, Nickel spent, and timestamps.
- **Daily settings** — whether `/daily` is on, and which recommendations were sent to you (to avoid repeats).
- **Interaction checks** — the Service checks that a command comes from a server (DMs are rejected), but does not store server names, channels, or member lists.

## 2. 我們「不」蒐集的資料 / Data we do NOT collect

- 本服務**不讀取訊息內容**（包括私訊），也不需要任何頻道權限。
- 不蒐集你的電子郵件、密碼、IP 位址、付款資訊或語音內容。
- 不追蹤你在 YouTube 上的觀看、按讚、訂閱或留言。

- The Service does **not read message content** (including DMs) and requests no channel permissions.
- We do not collect your email, password, IP address, payment information, or voice data.
- We do not track your YouTube views, likes, subscriptions, or comments.

## 3. 資料用途 / How we use data

資料僅用於提供本服務功能：記錄投稿、審核歌曲、處理交換、傳送每日推薦私訊、計算與顯示 Nickel 餘額、顯示你的投稿紀錄，以及防止重複投稿與濫用。我們**不會出售、出租資料，也不用於廣告**。

Data is used only to operate the Service: recording submissions, reviewing songs, processing exchanges, sending daily recommendation DMs, calculating and showing Nickel balances, showing your submission history, and preventing duplicates and abuse. We **do not sell or rent data, or use it for advertising**.

## 4. 資料可見範圍 / Who can see your data

- 指令回覆僅你本人可見（ephemeral）；每日推薦以私訊傳送給你本人。
- 交換到的歌曲可能是其他使用者投稿的；我們不會向你顯示投稿者的身分，也不會向投稿者顯示你的身分。
- 本服務維護者（Owner）可在審核時查看投稿與相關紀錄。
- 你的 Nickel 餘額與投稿紀錄跨伺服器共用，但不會向其他使用者顯示。

- Command replies are visible only to you (ephemeral); daily recommendations are sent to you by DM.
- Songs you get through exchange may come from other users' submissions; we do not reveal submitters to you or you to submitters.
- The Service maintainers can view submissions and related records for review.
- Your balance and history are shared across servers but never shown to other users.

## 5. 第三方服務 / Third-party services

本服務使用以下第三方服務，它們各自依其政策處理資料：

The Service relies on the following third parties, which handle data under their own policies:

- Discord — [Privacy Policy](https://discord.com/privacy)
- YouTube API Services — [YouTube Terms of Service](https://www.youtube.com/t/terms)、[Google Privacy Policy](https://policies.google.com/privacy)
- 資料庫與主機（Supabase、Google Cloud）/ Database and hosting (Supabase, Google Cloud)

## 6. 資料保存與刪除 / Retention and deletion

資料會在本服務營運期間保存，以維持餘額與投稿紀錄的正確性。若你希望刪除自己的資料，請依第 8 節與我們聯絡；確認身分後，我們會在合理期間內刪除或匿名化與你相關的資料。若本服務終止營運，資料將在合理期間內刪除。

Data is kept while the Service operates so that balances and history stay accurate. To request deletion, contact us as described in Section 8; after verifying your identity, we will delete or anonymize your data within a reasonable time. If the Service shuts down, data will be deleted within a reasonable time.

## 7. 資料安全 / Security

資料庫連線使用 TLS 加密，並以最小權限的帳號存取。沒有任何系統能保證絕對安全，但我們會採取合理措施保護你的資料。

Database connections use TLS and least-privilege accounts. No system is perfectly secure, but we take reasonable measures to protect your data.

## 8. 聯絡我們 / Contact

如有任何問題或刪除請求，請在 GitHub 開立 Issue：
<https://github.com/andrewchu0101/JukeBox101/issues>
請**不要**在公開 Issue 中貼出個人資料；維護者會再與你確認身分。

For questions or deletion requests, open an issue at
<https://github.com/andrewchu0101/JukeBox101/issues>.
Please **do not** post personal information in a public issue; a maintainer will follow up to verify your identity.

## 9. 政策變更 / Changes

我們可能更新本政策，更新後會修改本頁的「最後更新」日期。更新後繼續使用本服務即視為同意新政策。

We may update this policy; the "Last updated" date will change accordingly. Continued use after an update means you accept the revised policy.
