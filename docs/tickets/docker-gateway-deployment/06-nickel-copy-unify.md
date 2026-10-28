# 06：回覆文案統一使用 Nickel

**What to build:** 面向使用者的回覆統一使用「Nickel」，取代「點數」與目前的混用。逐句文案在 Owner 核准前不修改。

**Blocked by:** Owner 逐句文案核准（非程式阻塞）

**Status:** needs-owner-copy-approval

**File owner:** `bot/services.py`、`bot/exchange_delivery.py`、`bot/discord_owner_review.py`（僅 Nickel 相關字樣）、相關測試（新增，不改既有斷言的含義）

## 已確認的對照（待核准的草案）

| 位置 | 現有文字（摘要） | 草案 |
|---|---|---|
| 餘額 | 目前可用交換點數：N 點。 | 目前可用 Nickel：N。 |
| 點數不足 | 交換一首歌曲需要 1 Nickel。（已是 Nickel） | 維持 |
| 私訊失敗退款 | 已退回 1 點。 | 已退還 1 Nickel。 |
| 重複投稿 | 本次沒有新增投稿或發放 Nickel。（已是 Nickel） | 維持 |
| 已儲存投稿 | 本次不發放 Nickel。（已是 Nickel） | 維持 |

完整對照表見規格與先前的回覆文案對照。

## TDD 驗證

- 紅：餘額回覆包含「Nickel」，且不包含「點數」。
- 紅：私訊失敗退款文字使用 Nickel。
- 紅：所有面向使用者的回覆（抽查清單）不含「點數」字樣。
- 紅：既有 5 句逐字核准台詞（`bot/share_replies.py`）保持完全相同。

**Acceptance criteria:**
- [ ] Owner 逐句核准後，才修改對應文字。
- [ ] 逐字核准台詞完全不變，並有測試鎖定。
- [ ] 不改變指令名稱、資料契約、獎勵、審核與交易規則。
- [ ] 離線測試以 Python 3.12 執行通過，並列出通過、失敗、略過數量。

**待 Owner 核准：** 上表草案，以及是否將 5 句已核准台詞接線到 `/share`（本票不接線，屬行為變更，另案決定）。
