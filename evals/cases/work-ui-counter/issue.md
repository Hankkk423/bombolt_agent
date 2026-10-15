> 🤖 由 bombolt 規劃 · 規劃依據 `origin/main` @ `{{base_sha_short}}` · {{date}}
> 實作方式：在 repo 的主 checkout 開一個新的 Claude Code session，執行 `/bombolt:bb-work <這個 issue 的編號>`

## 🎯 目標

打開首頁 `/` 的人，在清單上方一眼看到還有幾項沒做完（「還有 2 項未完成」）；全部做完時看到「全部完成了」。

## 背景

- 現在首頁（`todo/server.py:65` 的 `render_index`）只有一條清單，要自己數有幾項沒打勾。
- 使用者原本的描述：「清單一長就要一直往下數，上面直接告訴我還剩幾項就好。」

## 🐞 問題與根因

無

## 範圍

**要做**
- `todo/server.py`：`render_index` 在清單上方加計數；`PAGE` 的 `<style>` 加一條 `.remaining` 的樣式。
- `tests/test_server.py` 的 `PageTest` 補測試。

**⛔ 不做**（實作時看到也不要順手改）
- 不改 API（`/api/todos` 不加計數欄位）、不改 CLI。
- 不改清單本身、表單、其他樣式。
- 不加 JavaScript（頁面重新載入時重算就好；按「完成」本來就會重新導回 `/`）。
- 不改 `todo/store.py`（包括 `_save` 上的 TODO）。

## ✅ 已確認的決策

| 題目 | 決定 | 被否決的選項與理由 |
|---|---|---|
| 文案 | 有未完成的：`還有 N 項未完成`（N 是阿拉伯數字，前後各一個半形空白）；有待辦但全部完成：`全部完成了` | 「剩 N 項」：跟產品其他文案的語氣不一致 |
| HTML 結構 | `<p id="remaining" class="remaining">還有 N 項未完成</p>`，放在 `<h1>` 之後、`<ul>` 之前（也就是 `{items}` 的最前面） | 放在清單下方：清單長的時候看不到 |
| 完全沒有待辦時 | 不顯示計數（不輸出 `id="remaining"` 這個元素），維持現在的「還沒有任何待辦事項」 | 顯示「還有 0 項未完成」：跟空狀態的文案重複 |
| 樣式 | `PAGE` 的 `<style>` 加 `.remaining {{ color: #555; margin: 0 0 12px; }}`（`PAGE` 會經過 `str.format`，大括號要雙寫） | 粗體或醒目的顏色：計數是輔助資訊，不該比清單搶眼 |

## 預設決定（沒有逐題詢問；有異議請在開工前直接改這個 issue）

- N 只算 `done` 是 `false` 的待辦，跟到期日、逾期無關。
- 計數在伺服器端算（`render_index` 裡），跟清單用同一份資料。

## 相關程式碼與既有慣例

- `todo/server.py:65` — `render_index(todos, today_str)`：要改的函式；`todos` 是全部待辦（`self.store.list()`）。
- `todo/server.py:16` — `PAGE`：頁面模板，用 `str.format(items=...)` 填入，CSS 的大括號都是雙寫（`{{ }}`）。
- `todo/server.py:28` — `form.inline` 那一行樣式：新的 `.remaining` 加在它下面。
- `tests/test_server.py:71` — `PageTest`：首頁的測試放這裡（`self.request("GET", "/")` 回 `(status, html)`）。
- 這個區域的規則（`CLAUDE.md`）：每個行為改動都要有 unittest；只用標準函式庫。

## 實作步驟

- [ ] 1. 先照「📸 截圖計畫」拍 before（改 code 之前）
- [ ] 2. `render_index`：有待辦時，算出未完成的數量，在 `<ul>` 前面加上 `<p id="remaining" class="remaining">…</p>`（`todo/server.py`）— 驗證：`curl -s http://localhost:<port>/ | grep remaining`
- [ ] 3. `PAGE` 加 `.remaining` 樣式（`todo/server.py`）
- [ ] 4. `PageTest` 新增：有未完成時的文案、全部完成時的文案、沒有待辦時不輸出計數（`tests/test_server.py`）
- [ ] 5. 跑驗證閘門 — 驗證：`python3 -m unittest discover -s tests` 最後一行 `OK`

## 🏁 完成定義（DoD）

實作 session 以這張表為唯一的完成標準：**每一條都要附證據，全部通過才算完成**。

| # | 條件（當…，則…） | 驗證方式 | 證據 |
|---|---|---|---|
| 1 | 當有 3 筆待辦、其中 1 筆完成，則首頁清單上方顯示「還有 2 項未完成」 | 照「環境與前置」塞資料，playwright-cli 打開 `http://localhost:<port>/`，`snapshot` 讀畫面 | 畫面上看到的內容 ＋ after 截圖 |
| 2 | 在首頁按其中一筆未完成的「完成」按鈕，則頁面重新載入後顯示「還有 1 項未完成」 | playwright-cli 點「完成」，再 `snapshot` | 畫面上看到的內容 |
| 3 | 全部完成時，顯示「全部完成了」 | 把剩下的也按完成，`snapshot` | 畫面上看到的內容 |
| 4 | 完全沒有待辦時，不出現計數，維持「還沒有任何待辦事項」 | 換一個空的資料檔啟動（或 `curl -s http://localhost:<port>/` 用空資料），確認沒有 `id="remaining"` | 指令輸出或畫面內容 |
| 5 | 新增的 `PageTest` 測試涵蓋 1、3、4 並通過 | `python3 -m unittest tests.test_server -v` | 測試輸出 |
| 6 | 專案的驗證閘門全綠 | `python3 -m unittest discover -s tests` | 指令輸出 |
| 7 | 只改了這些檔：`todo/server.py`、`tests/test_server.py` | `git diff --stat origin/main...HEAD` | 指令輸出 |

## 📸 截圖計畫

before 在改 code 之前拍，after 在實作完成後拍：**同一份資料、同一個畫面、同樣的視窗大小（1280×800）**。

| # | 畫面 | 怎麼到達 | 要呈現的狀態 |
|---|---|---|---|
| 1 | 首頁 `/` | 照「環境與前置」塞好 3 筆資料（1 筆完成）後，打開 `http://localhost:<port>/` | 清單有 3 筆、其中 1 筆劃掉；after 圖要看得到清單上方的「還有 2 項未完成」 |

## 環境與前置

照 `.claude/bombolt.md` 的「啟動 app」啟動，測試資料用 CLI 塞進同一個資料檔（before 與 after 都用這一份，拍完 before 不要清掉）：

```bash
python3 -m todo.cli --data .bombolt/todos.json add "買牛奶" --due 2026-10-20
python3 -m todo.cli --data .bombolt/todos.json add "繳電費"
python3 -m todo.cli --data .bombolt/todos.json add "回信"
python3 -m todo.cli --data .bombolt/todos.json done 2
```

## ⚠️ 風險與已知的坑

- `PAGE` 經過 `str.format`：新的 CSS 規則大括號要雙寫，不然會 `KeyError`／`ValueError`，整個首頁 500。
- DoD 2 按「完成」之後資料會變；要拍 after 截圖的話，在按之前先拍（跟 before 同樣的資料狀態）。

## 🚀 本地開始執行

在 repo 的主 checkout 開一個新的 Claude Code session：

```bash
claude -n bb-{{repo}}-{{issue}}-{{slug}} --permission-mode auto "/bombolt:bb-work {{issue}}"
```

省略 `--permission-mode auto` 也能跑，只是每個動作都會先問你才做。
想先看看有哪些 issue 可以認領：`/bombolt:bb-list`。

<!-- bombolt:meta {"base":"main","base_sha":"{{base_sha}}","slug":"{{slug}}","ui":true,"planned_at":"{{date}}"} -->
