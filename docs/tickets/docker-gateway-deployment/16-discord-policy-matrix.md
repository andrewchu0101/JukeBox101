# 16：Discord 政策矩陣與人工 Portal 清單

**What to build:** 依執行當日的官方文件建立政策檢查矩陣，並列出需人工在 Developer Portal 完成的項目。狀態分為：已驗證、需修正、需人工設定、待確認、不適用。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

**File owner:** `docs/discord-policy-matrix.md`（新）

## 必須覆蓋的項目

Developer Policy 與 Developer Terms 的適用條款；Bot 與 OAuth2 的合法流程（不使用 self-bot 或一般使用者 Token）；OAuth2 scopes、Bot 權限與 Gateway intents 的最小化；privileged intents 的申請、門檻與續審（以當日官方文件為準）；Rate limit、429、Retry-After、斷線重連與退避；Interactions 回應時限與重複處理；防止非預期 @everyone、@here 與 mention；使用者資料的蒐集、儲存、保存期限與刪除；DM 與派送的同意、停用與防騷擾；隱私政策與支援管道；語音與 DAVE（本專案只分享連結，預期為不適用，需提出證據）；第三方音樂與 YouTube 的政策相依性（保留既有 UNRESOLVED 狀態）。

## 規則

- 每一項記錄查核日期、來源連結、適用條件、程式證據與驗證方式。
- 官方明文要求、工程建議、Portal 人工設定與無法驗證事項分開列出。
- 不得聲稱取得 Discord 官方認證或保證全面合規。
- 第三方授權不以 Discord 查核代替。

## TDD 驗證

- 紅：每一項都有狀態，且狀態只能是五種之一（以文件格式檢查）。
- 紅：每一項「已驗證」都附來源連結與查核日期。

**Acceptance criteria:**
- [ ] 矩陣涵蓋上述所有項目。
- [ ] 所有「已驗證」項目附官方來源連結與查核日期。
- [ ] 需人工設定的 Portal 項目獨立列出。
- [ ] 無法於本機驗證的項目標為「待確認」，不假設答案。
- [ ] 不宣稱完整合規。

**Out of scope:** 實際修改 Developer Portal 設定。
