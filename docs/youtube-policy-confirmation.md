# YouTube 政策範圍確認（Google 正式回覆；有範圍限制）

**性質**：依使用者提供的 Google 正式回覆紀錄，記錄一項有範圍的政策確認。這不是 YouTube API 政策全面核准，也不是產品上線核准。

## 來源

- 來源文件：專案根目錄的 `Re Issue 576880673 Policy Inquiry Internal Community Credit System (Nickel) in Discord Bot (JukeBox101).pdf`。這是使用者提供的郵件備份，目前未納入版本控制（untracked），是否提交由使用者決定；內含郵件地址，建議不要提交。
- 案件：IssueTracker 576880673，「Policy Inquiry: Internal Community Credit System ("Nickel") in Discord Bot (JukeBox101)」，元件為 Public Trackers > YouTube API Service。
- 回覆日期：依 PDF 郵件標頭，為 2026-10-24 凌晨 1:40（第二封回覆，即 comment #2）。

## 已確認的範圍

依 PDF 所載的正式回覆，且僅限於提交給 Google 的架構與流程：

| 項目 | 回覆內容 |
| --- | --- |
| Nickel 獎勵條件 | 僅在 Discord 內分享 URL 時獎勵；不要求、也不獎勵任何 YouTube 互動（觀看、按讚、分享、訂閱、留言）。 |
| Incentivized Engagement（Playback Integrity） | 依回覆，此機制不違反該限制。 |
| YouTube Data API v3 | 後端用於驗證影片 metadata（例如時長），回覆為允許的用途。 |
| 其他政策 | 回覆明確要求：仍須遵守 YouTube API Services Developer Policies 與 Terms of Service 的其他所有條款。 |

## 未確認的範圍（不可擴大解讀）

- **不代表所有政策已核准**。回覆只針對上表的 Incentivized Engagement 與後端 metadata 驗證用途。
- **不代表產品可上線或啟用正式獎勵**。正式 `/exchange`、Nickel 自動獎勵、真實 DM、真實帳務仍依 ROADMAP 與各階段文件的授權程序。
- **不代表 metadata 取得範圍已核准**。回覆未逐項審查 Data API 的配額、資料保存期限、顯示條件與快取方式；這些仍需另行確認。
- 回覆依「提交給 Google 的架構」，若實作改變（例如獎勵與其他 YouTube 互動掛勾），需重新詢問。

## 與其他文件的關係

- `ROADMAP.md` 與 `README.md` 仍寫「YouTube API 政策 UNRESOLVED」。本文件未修改那些狀態列；是否更新其措辭，需由專案負責人決定。
- 本文件不新增任何 API 呼叫、憑證或外部服務。
