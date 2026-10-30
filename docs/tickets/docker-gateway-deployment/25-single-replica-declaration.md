# 25：單一副本限制的部署宣告與驗證

**What to build:** Compose 與部署文件明確宣告 Bot 只能單一副本；Compose 設定不得允許擴充為多副本。

**Blocked by:** 11（Compose 設定）

**Status:** ready-for-agent（Compose 設定完成後執行）

**File owner:** `compose.yaml`（僅副本相關設定），`docs/deploy/partner-deployment.md`（僅副本限制段落；由 13 票建立，若尚未存在則本票不建立，待 13 完成後補入）

**TDD 驗證：**
- 紅：Compose 設定的 Bot 服務副本數為 1，且無擴充設定。
- 紅：部署文件包含「只支援單一副本」與理由的段落。

**Acceptance criteria:**
- [ ] 副本數明確為 1。
- [ ] 文件說明 Gateway 與 sharding 未驗證，因此不支援多副本。
