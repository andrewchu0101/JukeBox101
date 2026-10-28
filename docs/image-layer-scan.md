# 映像各層掃描紀錄（`jukebox101-bot:candidate`）

- 紀錄日期：2026-10-29
- 映像：`jukebox101-bot:candidate`（id 前綴 `sha256:f71772e1`）
- 狀態：**已完成掃描，命中已分類為公開值**（config 中的 `generic-api-key`，2 處；Lead 於 2026-10-29 核對，判定非憑證）。
- 性質：唯讀掃描。未修改映像、未 prune、未發布、未重建、未使用 sudo、未改權限。
- 執行方式：`/mnt/c/WINDOWS/system32/wsl.exe -d Ubuntu -- bash <腳本>`，腳本位於 scratchpad（不在專案內）。
- 本紀錄**不含任何匹配內容**，只列規則、數量與位置。

## 1. 結果摘要

| 項目 | 結果 |
| --- | --- |
| 匯出 | `docker save jukebox101-bot:candidate -o /home/gaston/build/review/candidate-image.tar`，成功 |
| 映像層數（manifest `Layers`） | 8 |
| 映像 config（獨立掃描） | 2 命中，規則 `generic-api-key` |
| 第 1 至 8 層 | 第 1 至 8 層全部掃描完成（gitleaks exit 0，0 命中） |
| 第 4、7 層 | 無一般檔案內容（第 4 層為 3 個目錄與 5 個符號連結；第 7 層為 1 個目錄），無內容可掃 |
| 映像環境變數名稱 | 8 個：`PATH`、`LANG`、`GPG_KEY`、`PYTHON_VERSION`、`PYTHON_SHA256`、`PYTHONDONTWRITEBYTECODE`、`PYTHONUNBUFFERED`、`TZ` |
| history 中含 `secret`／`token`／`password` 字樣的行數 | 0 |

## 2. 命中明細與分類（不含匹配內容）

分類依據（Lead 唯讀核對：只比對長度與是否與環境值相等，未輸出值）：

| 命中 | 位置類別 | 比對結果 | 分類 |
| --- | --- | --- | --- |
| (a) | `config.Env` 環境變數 `GPG_KEY` | 40 位十六進位，與 `GPG_KEY` 環境值相等 | Python 基礎映像的發行簽章金鑰指紋（公開值） |
| (a) | `config.Env` 環境變數 `PYTHON_SHA256` | 64 位十六進位，與 `PYTHON_SHA256` 環境值相等 | Python tarball 雜湊（公開值） |
| (b) | `history` 第 11 步 | 64 位十六進位，與 `PYTHON_SHA256` 相同 | 基礎映像建置步驟內容 |
| (b) | `history` 第 13 步 | 40 位十六進位，與 `GPG_KEY` 相同 | 基礎映像建置步驟內容 |

結論：**2 處命中皆為 Python 基礎映像的公開發行資訊，不是憑證**。本專案自行加入的 Dockerfile 步驟均未命中。

| 來源 | 規則 | 命中數 | 位置（config JSON 內） |
| --- | --- | --- | --- |
| 映像 config（`config.json`，gitleaks 報告 `layers/config.json`） | `generic-api-key` | 2 | (a) `config.Env` 陣列中的一個環境變數值；(b) `history` 中一筆 `created_by` 指令字串 |

說明：

- 兩處命中都位於映像的 **config JSON**，不在任何一層的檔案系統內。
- 分類結果見第 2 節開頭表格。
- 第 1 至 8 層的所有解開檔案，gitleaks 皆 0 命中。
- 命中 (b) 的 `history` 行**不含** `secret`、`token`、`password` 字樣（第 1 節的關鍵字計數為 0），所以關鍵字搜尋沒有抓到，但 gitleaks 的 `generic-api-key` 規則抓到。這表示 `history` 中可能有一段看起來像金鑰的字串。
- 兩處的實際內容**未輸出**，分類依據僅為長度與環境值比對。

## 3. 產出檔案

| 檔案 | 內容 | 注意 |
| --- | --- | --- |
| `/home/gaston/build/review/candidate-image.tar` | 完整映像匯出（約 54 MB） | **含 config 內的命中內容**，不得分享或提交 |
| `/home/gaston/build/review/layers/config.json` | config 的 gitleaks 報告（含 `--redact`） | 報告已遮罩匹配值，但仍屬敏感檔，不得提交 |
| `/home/gaston/build/review/layers/layer-1.json` … `layer-8.json` | 各層報告，皆為空陣列（0 命中） | 無敏感內容 |

解開的工作目錄（`image-work.*`）已於掃描後刪除，不留下解開的層內容。

## 4. 核對方法

- 以 `docker save` 匯出後，用 `tar` 解開到獨立的空目錄（`--no-same-owner`），每層一個目錄。
- 依 `manifest.json` 的 `Layers` 順序對應：`layer-n` 對應 `Layers[n-1]`。
- config 另外單獨解出掃描。
- gitleaks 指令：`gitleaks dir <目錄> --redact --no-banner --report-format json --report-path <報告>`，exit 1 代表有命中、0 代表無命中。
- 環境變數只輸出名稱（`split("=")[0]`）。history 關鍵字計數使用 `grep -icE 'secret|token|password'`。

## 5. 校驗工具（核對結果）

- gitleaks 版本：`8.30.1`。
- `checksums.txt` 第 7 行含 `gitleaks_8.30.1_linux_x64.tar.gz`，雜湊 `551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb`，與本機壓縮檔的 sha256 相符。
- 更正：本次較早的唯讀檢查曾回報 `checksums.txt` 無 `linux_x64` 條目，該結論有誤。重新讀取後確認第 7 行存在，Lead 所述 OK 正確。

## 6. 後續事項

1. `candidate-image.tar` 與 `layers/config.json` 保留在本機審查資料夾 `/home/gaston/build/review/`，**不分享、不納入交付包、不提交到 Git**。清理由 Owner 決定。
2. 掃描只涵蓋 gitleaks 的預設規則，不代表映像內沒有其他類型的敏感資料。
3. 本次沒有需要輪替的憑證；映像未重建。

## 7. 限制

- 第 4、7 層無可掃描的一般檔案，掃描結論只涵蓋實際存在的檔案內容；符號連結不被 gitleaks 追蹤。
- gitleaks 只做規則比對；2 處命中的分類依據為 Lead 的長度與環境值比對，未逐字人工檢視內容。
- 未驗證映像的建置來源或 Dockerfile，只掃描了映像的現況。
