# 26：REST 層 429 與 Retry-After 處理（來自政策矩陣 D-08）

**What to build:** REST 呼叫遇到 429 時讀取 Retry-After，有限次數退避，不無限重試；其他 4xx/5xx 維持現有拒絕行為。

**Blocked by:** None（政策矩陣已完成）

**Status:** ready-for-agent（Owner 需確認重試次數上限）

**File owner:** `bot/discord_http.py`、`tests/test_discord_http_retry.py`（新）

**現況證據：** `bot/discord_http.py` 在 HTTP 狀態碼 ≥ 400 時直接拋出錯誤，未讀取 Retry-After。

**TDD 驗證：**
- 紅：429 回應帶 Retry-After 時，等待後重試一次成功。
- 紅：超過重試上限後拋出明確錯誤，不無限重試。
- 紅：非 429 的 4xx/5xx 不重試，維持現有行為。
- 紅：錯誤訊息不含 token 或完整 URL。

**Acceptance criteria:**
- [ ] 重試次數與等待上限為命名常數，且有測試。
- [ ] 不因 429 造成無限迴圈。
- [ ] 離線測試使用替身，不連線 Discord。
