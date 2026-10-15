> 🤖 由 bombolt 規劃 · 規劃依據 `origin/main` @ `{{base_sha_short}}` · {{date}}
> 實作方式：在 repo 的主 checkout 開一個新的 Claude Code session，執行 `/bombolt:bb-work <這個 issue 的編號>`

## 🎯 目標

呼叫 `GET /api/todos?sort=due` 的前端，拿到依到期日由近到遠排好的待辦，不用自己排序。

## 背景

- 現在 `GET /api/todos` 一律照 id（建立順序）回傳（`todo/server.py:115` 的 `list_todos`）。
- 前端的「依到期日」檢視現在是抓全部之後自己排，要改成交給 API。
- 使用者原本的描述：「想要一個依到期日排好的清單，最快到期的在最上面。」

## 🐞 問題與根因

無

## 範圍

**要做**
- `todo/server.py` 的 `list_todos` 支援 query 參數 `sort`，目前只有一個值 `due`。
- `tests/test_server.py` 補上對應的測試。

**⛔ 不做**（實作時看到也不要順手改）
- 不加其他排序方式（title、created_at…），也不加反向排序。
- 不改網頁 `/` 的順序、不改 CLI。
- 不改 `todo/store.py`（包括 `_save` 上的 TODO）。

## ✅ 已確認的決策

| 題目 | 決定 | 被否決的選項與理由 |
|---|---|---|
| 參數 | `sort=due`；沒帶 `sort` 時照 id 由小到大（跟現在一樣） | `order_by=due_date`：跟之後其他 query 參數的命名風格不一致 |
| 方向 | 到期日由近到遠（字串由小到大，日期是 `YYYY-MM-DD` 所以字串順序等於日期順序） | 由遠到近：使用者要的是「最快到期的在最上面」 |
| 不合法的值 | 回 400，body `{"error": {"code": "invalid_query", "message": "sort 只能是 due"}}`（照 `CLAUDE.md` 的錯誤格式） | 忽略、照預設排序：打錯字的呼叫端會默默拿到錯的順序 |

## 預設決定（沒有逐題詢問；有異議請在開工前直接改這個 issue）

- 排序在 `list_todos` 裡做，不改 `TodoStore`。
- 每個 todo 一樣經過 `to_json()`（保留 `overdue` 欄位）。

## 相關程式碼與既有慣例

- `todo/server.py:115` — `list_todos(self, query)`：要改的地方。`query` 是 `parse_qs` 的結果（`{"sort": ["due"]}`）。
- `todo/server.py:44` — `error_body(code, message)`：組錯誤回應用這個。
- `tests/test_server.py:12` — `ServerTestCase`：server 測試的基底類別。
- 這個區域的規則（`CLAUDE.md`）：query 參數不合法一律 400 ＋ code `invalid_query`；每個行為改動都要有 unittest。

## 實作步驟

- [ ] 1. `list_todos` 讀 `query.get("sort", [None])[0]`；不是 `None`／`"due"` 就回 400（`invalid_query`）並 return（`todo/server.py`）
- [ ] 2. 是 `"due"` 時依到期日排序（`todo/server.py`）— 驗證：`curl -s 'http://localhost:<port>/api/todos?sort=due'`
- [ ] 3. 在 `tests/test_server.py` 新增 `SortTest(ServerTestCase)`
- [ ] 4. 跑驗證閘門 — 驗證：`python3 -m unittest discover -s tests` 最後一行 `OK`

## 🏁 完成定義（DoD）

實作 session 以這張表為唯一的完成標準：**每一條都要附證據，全部通過才算完成**。

| # | 條件（當…，則…） | 驗證方式 | 證據 |
|---|---|---|---|
| 1 | 當有到期日 2026-10-20 與 2026-10-15 的兩筆待辦，`GET /api/todos?sort=due`，則 2026-10-15 那筆在前 | 啟動 app，照「環境與前置」塞資料，`curl -s 'http://localhost:<port>/api/todos?sort=due'` | 指令輸出 |
| 2 | 沒帶 `sort`，則照 id 由小到大，跟改之前一樣 | `curl -s 'http://localhost:<port>/api/todos'` | 指令輸出 |
| 3 | `sort` 是 `due` 以外的值，則回 400，body 是 `{"error": {"code": "invalid_query", "message": "sort 只能是 due"}}` | `curl -s -w '\n%{http_code}' 'http://localhost:<port>/api/todos?sort=title'` | 指令輸出 |
| 4 | 新增的測試 `SortTest` 涵蓋 1、3 並通過 | `python3 -m unittest tests.test_server -v` | 測試輸出 |
| 5 | 專案的驗證閘門全綠 | `python3 -m unittest discover -s tests` | 指令輸出 |
| 6 | 只改了這些檔：`todo/server.py`、`tests/test_server.py` | `git diff --stat origin/main...HEAD` | 指令輸出 |

## 📸 截圖計畫

無（非 UI 改動，以測試證據代替）

## 環境與前置

照 `.claude/bombolt.md` 的「啟動 app」啟動，測試資料：

```bash
python3 -m todo.cli --data .bombolt/todos.json add "買牛奶" --due 2026-10-20
python3 -m todo.cli --data .bombolt/todos.json add "繳電費" --due 2026-10-15
python3 -m todo.cli --data .bombolt/todos.json add "回信"
```

## ⚠️ 風險與已知的坑

- `parse_qs` 的值是 list（`{"sort": ["due"]}`）。
- 不合法的值回 400 之後要 `return`，不然會接著再送一次 200 的回應。

## 🚀 本地開始執行

在 repo 的主 checkout 開一個新的 Claude Code session：

```bash
claude -n bb-{{repo}}-{{issue}}-{{slug}} --permission-mode auto "/bombolt:bb-work {{issue}}"
```

省略 `--permission-mode auto` 也能跑，只是每個動作都會先問你才做。
想先看看有哪些 issue 可以認領：`/bombolt:bb-list`。

<!-- bombolt:meta {"base":"main","base_sha":"{{base_sha}}","slug":"{{slug}}","ui":false,"planned_at":"{{date}}"} -->
