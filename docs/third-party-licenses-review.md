# 第三方套件授權查核紀錄

- 查核日期：2026-10-29
- 查核對象：`docs/THIRD-PARTY-LICENSES.md`（中繼資料摘要）
- 查核環境：隔離環境 `/home/gaston/.venvs/jukebox101-py312/lib/python3.12/site-packages`（唯讀，只讀取 `*.dist-info` 內的 `METADATA` 與授權檔）
- 性質：**查核紀錄，不是專案授權**。本文件只描述各套件隨附的授權檔與中繼資料，不改動任何授權宣告。
- **專案 LICENSE：待 Owner 決定**。本次未新增、未修改任何 LICENSE 檔。

## 1. 主要發現

1. **`THIRD-PARTY-LICENSES.md` 的「（未提供）」有誤**。產生器只讀 `License` 與 `Classifier` 欄位，漏掉了 PEP 639 的 `License-Expression` 欄位。以下 8 個套件實際上有明確授權欄位，但原清單標示為未提供：`aiohttp`、`attrs`、`cffi`、`idna`、`multidict`、`pycparser`、`PyMySQL`、`typing_extensions`。
2. **真正缺少授權欄位的只有 `colorama`**（`License` 與 `License-Expression` 皆無，只有 `Classifier: BSD License`）。
3. **所有查核範圍內的套件都找得到授權檔**，「授權未找到」的套件數為 **0**。
4. 有 3 個套件的授權檔內含額外授權：`aiohttp`（內含 `vendor/llhttp` 的 MIT）、`PyNaCl`（內含 `libsodium` 的 ISC）、`tzdata`（另附 `LICENSE_APACHE`）。其中 `PyNaCl` 的中繼資料只寫 `Apache-2.0`，**未提及內含的 ISC 授權**，需於發布時一併保留該授權檔。
5. 清單缺少 6 個開發／測試用套件（`pytest`、`pluggy`、`iniconfig`、`pygments`、`packaging`、`pip`）。這些是否隨發布附上，屬 Owner 決定。

## 2. 逐套件查核（清單內缺欄位者）

判定僅依授權檔首段與關鍵字，**不推定**。判定欄標示「與中繼資料一致」表示首段與 `METADATA` 宣告相符。

| 套件 | 版本 | 原清單 License 欄 | `METADATA` 實際欄位 | 授權檔 | 授權檔判定 | 結論 |
| --- | --- | --- | --- | --- | --- | --- |
| aiohttp | 3.14.5 | 未提供 | `License-Expression: Apache-2.0 AND MIT` | `aiohttp-3.14.5.dist-info/licenses/LICENSE.txt`；`.../licenses/vendor/llhttp/LICENSE` | Apache License 2.0；MIT（Fedor Indutny，llhttp） | 與中繼資料一致。原清單誤標未提供 |
| attrs | 26.1.0 | 未提供 | `License-Expression: MIT` | `.../licenses/LICENSE` | The MIT License (MIT)，Hynek Schlawack 與 attrs contributors | 與中繼資料一致 |
| cffi | 2.1.1 | 未提供 | `License-Expression: MIT-0` | `.../licenses/LICENSE` | 首段為 cffi 通用說明；內文含「MIT No Attribution」與 MIT 許可文字 | 與中繼資料一致（MIT-0，免署名） |
| colorama | 0.4.6 | 未提供 | 無 `License`，僅 `Classifier: License :: OSI Approved :: BSD License` | `colorama-0.4.6.dist-info/licenses/LICENSE.txt` | BSD 類（Jonathan Hartley，含 Redistribution 條款） | **中繼資料缺授權欄位**，以授權檔判定為 BSD 類。條款數以檔案為準 |
| idna | 3.20 | 未提供 | `License-Expression: BSD-3-Clause` | `.../licenses/LICENSE.md` | BSD 3-Clause License（Kim Davies and contributors） | 與中繼資料一致 |
| multidict | 7.1.0 | 未提供 | `License-Expression: Apache-2.0` | `.../licenses/LICENSE` | Apache License 2.0 | 與中繼資料一致 |
| pycparser | 3.12 | 未提供 | `License-Expression: BSD-3-Clause` | `.../licenses/LICENSE` | BSD 類（Eli Bendersky，首行 pycparser -- A C parser in Python） | 與中繼資料一致。版權年份寫作 2008-2027，依檔案原文記錄 |
| PyMySQL | 1.2.3 | 未提供 | `License-Expression: MIT` | `.../licenses/LICENSE` | MIT（PyMySQL contributors） | 與中繼資料一致 |
| typing_extensions | 4.16.0 | 未提供 | `License-Expression: PSF-2.0` | `.../licenses/LICENSE` | PSF 授權文字（首段為 "A. HISTORY OF THE SOFTWARE"） | 與中繼資料一致 |

## 3. 其餘查核套件（原清單有欄位，或本次一併核對）

| 套件 | 版本 | 中繼資料 | 授權檔 | 授權檔判定 | 備註 |
| --- | --- | --- | --- | --- | --- |
| discord.py | 2.7.1 | `License: The MIT License (MIT)`；Classifier MIT | `.../licenses/LICENSE` | MIT（Rapptz） | 與中繼資料一致 |
| aiohappyeyeballs | 2.7.1 | `License: PSF-2.0` | `.../licenses/LICENSE` | PSF 授權文字 | 與中繼資料一致 |
| aiosignal | 1.4.0 | `License: Apache 2.0` | `.../licenses/LICENSE` | Apache License 2.0 | 與中繼資料一致 |
| frozenlist | 1.8.0 | `License: Apache-2.0`（無 Classifier） | `.../licenses/LICENSE` | Apache License 2.0 | 原清單 Classifier 欄未提供，屬正常，因套件未宣告 Classifier |
| propcache | 0.5.4 | `License: Apache-2.0` | `.../licenses/LICENSE`；`.../licenses/NOTICE` | Apache License 2.0；NOTICE（aio-libs，2016-2021） | 附 NOTICE 檔，發布時應一併保留 |
| yarl | 1.25.1 | `License: Apache-2.0` | `.../licenses/LICENSE`；`.../licenses/NOTICE` | Apache License 2.0；NOTICE（aio-libs，2016-2021） | 附 NOTICE 檔，發布時應一併保留 |
| PyNaCl | 1.6.2 | `License: Apache-2.0`；Classifier Apache | `.../licenses/LICENSE`；`.../licenses/licenses/LICENSE.libsodium.txt` | Apache License 2.0；libsodium 內含 **ISC License** | **內含 ISC 授權**，中繼資料未提及，需保留 `LICENSE.libsodium.txt` |
| python-dotenv | 1.2.4 | `License: BSD-3-Clause` | `.../licenses/LICENSE` | BSD 3-Clause（首段版權列為 Saurabh Kumar、Ted Tieken 等，行尾截斷，全文以檔案為準） | 與中繼資料一致 |
| tzdata | 2026.6 | `License: Apache-2.0` | `.../licenses/LICENSE`；`.../licenses/licenses/LICENSE_APACHE` | Apache Software License 2.0 | 與中繼資料一致；`LICENSE_APACHE` 為重複的授權全文 |

## 4. 清單外套件（本次一併列出）

這些套件存在於隔離環境，但未列入 `THIRD-PARTY-LICENSES.md`。多為開發與測試用途，是否隨發布附上屬 Owner 決定。

| 套件 | 版本 | `METADATA` 授權欄位 | 授權檔 | 用途判斷 |
| --- | --- | --- | --- | --- |
| pytest | 8.4.2 | `License: MIT` | `.../licenses/LICENSE` | 測試 |
| pluggy | 1.6.0 | `License: MIT` | `.../licenses/LICENSE` | pytest 相依 |
| iniconfig | 2.3.1 | `License-Expression: MIT` | `.../licenses/LICENSE` | pytest 相依 |
| pygments | 2.21.0 | `License-Expression: BSD-2-Clause` | `.../licenses/LICENSE`；`AUTHORS` | pytest 相依 |
| packaging | 26.3 | `License-Expression: Apache-2.0 OR BSD-2-Clause` | `.../licenses/LICENSE`；`LICENSE.APACHE` | pytest 相依 |
| pip | 25.0.1 | `License: MIT` | `pip-25.0.1.dist-info/LICENSE.txt`；`AUTHORS.txt` | 環境工具 |

## 5. 未找到授權檔的套件

**0 個**。上列所有套件都在各自 `*.dist-info/licenses/` 或根目錄找到授權檔。

## 6. 建議（供 Owner 決定，本次未執行）

1. 修正 `THIRD-PARTY-LICENSES.md` 的產生器，讀取 `License-Expression`，並將 `colorama` 的授權改為以 Classifier 或授權檔補上。本次依指示未改動該檔。
2. 決定發布時是否隨包附上完整授權檔。`PyNaCl`（ISC）、`aiohttp`（MIT）、`propcache`／`yarl`（NOTICE）、`tzdata` 的附加授權檔應一併保留。
3. 決定開發與測試套件（第 4 節）是否列入清單。
4. 專案本身的 LICENSE：待 Owner 決定，本紀錄不代為選擇。

## 7. 限制

- 判定僅依授權檔首段與關鍵字，**未逐字比對**官方授權全文；條款細節以各套件發行的檔案為準。
- 本次只讀取 `dist-info`，未驗證 wheel 的實際內容與發行來源。
- 未連線網路，未查詢套件的上游授權變更。
