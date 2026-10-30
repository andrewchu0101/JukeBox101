# 20：Nickel 發放的使用者文案與 /balance 資訊

**What to build:** `/balance` 顯示本週定期額度是否可領與下次可領時間；新增 `/claim` 的回覆文案（若 Owner 核准該指令）。文案在功能實作前不宣稱可領取。

**Blocked by:** 19，以及 Owner 逐句核准文案

**Status:** needs-owner-copy-approval

**File owner:** `bot/discord_app.py`（指令註冊）、`bot/services.py`（僅 `/balance` 與發放回覆文字），與對應測試

**TDD 驗證：**
- 紅：功能關閉時，`/balance` 不顯示可領字樣。
- 紅：功能開啟且本週已領時，顯示下次可領時間（Asia/Taipei）。
- 紅：發放成功、已領、未開放三種回覆各自只在對應條件出現。

**Acceptance criteria:**
- [ ] 文案只在事實成立的分支出現。
- [ ] 不宣稱功能未開放時可領取。
- [ ] 不混入 `/daily`；`/daily` 維持「每日推薦尚未開放」。
