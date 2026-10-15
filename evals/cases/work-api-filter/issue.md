> 🤖 由 bombolt 規劃 · 規劃依據 `origin/main` @ `{{base_sha_short}}` · {{date}}
> 實作方式：在 repo 的主 checkout 開一個新的 Claude Code session，執行 `/bombolt:bb-work <這個 issue 的編號>`

## 🎯 目標

呼叫 `GET /api/todos` 的前端與腳本，可以用 `?done=true` 只拿已完成的、`?done=false` 只拿未完成的待辦，不用自己在 client 端過濾。

## 背景

- 現在 `GET /api/todos` 一律回傳全部待辦（`todo/server.py:115` 的 `list_todos`），前端要顯示「未完成」分頁時得自己過濾。
- `list_todos` 已經收到解析好的 query（`todo/server.py:111`：`self.list_todos(parse_qs(url.query))`），只是還沒用。
- 使用者原本的描述：「API 能不能直接幫我篩出還沒做完的？前端每次都抓全部再自己濾。」

## 🐞 問題與根因

無

## 範圍

**要做**
- `todo/server.py` 的 `list_todos` 支援 query 參數 `done`。
- `tests/test_server.py` 補上對應的測試。

**⛔ 不做**（實作時看到也不要順手改）
- `todo/store.py` 的寫檔方式（`_save` 上面那個「改成先寫暫存檔再 os.replace」的 TODO）——另外排，不在這個 issue。
- 不改 `README.md`、`CLAUDE.md`。
- 不加其他篩選條件（due、關鍵字…），也不加排序或分頁。
- 網頁 `/` 不變。

## ✅ 已確認的決策

| 題目 | 決定 | 被否決的選項與理由 |
|---|---|---|
| 參數的值 | 只接受小寫的 `true`、`false`（大小寫敏感） | 接受 `1/0`、`yes/no`、大小寫不拘：多一種寫法就多一種要維護的行為，前端只會送 `true/false` |
| 不合法的值 | 回 400，body `{"error": {"code": "invalid_query", "message": "done 只能是 true 或 false"}}`（照 `CLAUDE.md` 的錯誤格式） | 忽略不合法的值、當成沒帶：會讓打錯字的呼叫端默默拿到全部資料 |
| 篩選在哪裡做 | 在 `list_todos` 裡過濾 `self.store.list()` 的結果 | 在 `TodoStore` 加新方法：store 不該知道 HTTP 的參數，而且資料量小 |

## 預設決定（沒有逐題詢問；有異議請在開工前直接改這個 issue）

- `?done=`（空值）視同沒帶參數，回傳全部（`parse_qs` 預設就會丟掉空值，不用特別處理）。
- 參數重複（`?done=true&done=false`）取第一個。
- 回傳的順序跟現在一樣（id 由小到大），每個 todo 一樣經過 `to_json()`（保留 `overdue` 欄位）。

## 相關程式碼與既有慣例

- `todo/server.py:115` — `list_todos(self, query)`：要改的地方。`query` 是 `parse_qs` 的結果（`{"done": ["true"]}`）。
- `todo/server.py:44` — `error_body(code, message)`：組錯誤回應用這個。
- `todo/server.py:48` — `to_json(todo, today_str)`：回傳的 todo 一律經過它。
- `tests/test_server.py:12` — `ServerTestCase`：server 測試的基底類別（`self.json(method, path)` 回 `(status, body)`）。
- 這個區域的規則（`CLAUDE.md`）：query 參數不合法一律 400 ＋ code `invalid_query`；每個行為改動都要有 unittest；只用標準函式庫。

## 實作步驟

- [ ] 1. 在 `list_todos` 讀 `query.get("done", [None])[0]`；不是 `None`／`"true"`／`"false"` 就回 400（`invalid_query`）並 return（`todo/server.py`）— 驗證：`curl -s -o /dev/null -w '%{http_code}' 'http://localhost:<port>/api/todos?done=yes'` 印 `400`
- [ ] 2. 是 `"true"`／`"false"` 時，只留下 `done` 相符的待辦（`todo/server.py`）— 驗證：步驟 4 的測試
- [ ] 3. 在 `tests/test_server.py` 新增 `DoneFilterTest(ServerTestCase)`：`done=true`、`done=false`、沒帶參數、不合法的值各一個測試
- [ ] 4. 跑驗證閘門 — 驗證：`python3 -m unittest discover -s tests` 最後一行 `OK`

## 🏁 完成定義（DoD）

實作 session 以這張表為唯一的完成標準：**每一條都要附證據，全部通過才算完成**。

| # | 條件（當…，則…） | 驗證方式 | 證據 |
|---|---|---|---|
| 1 | 當資料裡有已完成與未完成的待辦，`GET /api/todos?done=true`，則只回已完成的（id 由小到大） | 啟動 app，用 CLI 塞 3 筆、把第 2 筆標完成，`curl -s 'http://localhost:<port>/api/todos?done=true'` | 指令輸出 |
| 2 | 同上資料，`GET /api/todos?done=false`，則只回未完成的 | `curl -s 'http://localhost:<port>/api/todos?done=false'` | 指令輸出 |
| 3 | 沒帶 `done`（或 `done=` 空值），則回全部，跟改之前一樣 | `curl -s 'http://localhost:<port>/api/todos'` 與 `...?done=` | 指令輸出 |
| 4 | `done` 是 `yes`、`1`、`True` 這類不合法的值，則回 400，body 是 `{"error": {"code": "invalid_query", "message": "done 只能是 true 或 false"}}` | `curl -s -w '\n%{http_code}' 'http://localhost:<port>/api/todos?done=yes'` | 指令輸出 |
| 5 | 新增的測試 `DoneFilterTest` 涵蓋 1–4 並通過 | `python3 -m unittest tests.test_server -v` | 測試輸出 |
| 6 | 專案的驗證閘門全綠 | `python3 -m unittest discover -s tests` | 指令輸出 |
| 7 | 只改了這些檔：`todo/server.py`、`tests/test_server.py` | `git diff --stat origin/main...HEAD` | 指令輸出 |

## 📸 截圖計畫

無（非 UI 改動，以測試證據代替）

## 環境與前置

無。照 `.claude/bombolt.md` 的「啟動 app」啟動，測試資料用 CLI 塞進同一個資料檔：

```bash
python3 -m todo.cli --data .bombolt/todos.json add "買牛奶"
python3 -m todo.cli --data .bombolt/todos.json add "繳電費"
python3 -m todo.cli --data .bombolt/todos.json add "回信"
python3 -m todo.cli --data .bombolt/todos.json done 2
```

## ⚠️ 風險與已知的坑

- `parse_qs` 的值是 list（`{"done": ["true"]}`），直接拿 `query["done"] == "true"` 永遠是 False。
- 不合法的值回 400 之後要 `return`，不然會接著再送一次 200 的回應。
- `store.py` 的 TODO 看起來很好改，但不在這個 issue 的範圍（見「⛔ 不做」）。

## 🚀 本地開始執行

在 repo 的主 checkout 開一個新的 Claude Code session：

```bash
claude -n bb-{{repo}}-{{issue}}-{{slug}} --permission-mode auto "/bombolt:bb-work {{issue}}"
```

省略 `--permission-mode auto` 也能跑，只是每個動作都會先問你才做。
想先看看有哪些 issue 可以認領：`/bombolt:bb-list`。

<!-- bombolt:meta {"base":"main","base_sha":"{{base_sha}}","slug":"{{slug}}","ui":false,"planned_at":"{{date}}"} -->
