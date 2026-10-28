# 21：業務狀態盤點驗收（唯讀）

**What to build:** 依 `docs/stateful-backend-spec.md` 第 2 節盤點結果，確認沒有遺漏的跨請求狀態，並把未決項目交給 Owner。本票不改程式。

**Blocked by:** None（盤點已由 Lead 完成）

**Status:** ready-for-agent（需 Lead 複核）

**File owner:** `docs/stateful-backend-spec.md`（僅勾選驗收結果）

**Acceptance criteria:**
- [ ] 盤點涵蓋全域變數、可變單例、記憶體字典、快取、本機檔案、背景工作、鎖與確認權杖。
- [ ] 每一項有用途、保存位置與重啟影響。
- [ ] 不以「容器可重啟」宣稱無狀態。
