> 🤖 由 bombolt 規劃 · 規劃依據 `origin/main` @ `{{base_sha_short}}` · {{date}}
> 實作方式：在 repo 的主 checkout 開一個新的 Claude Code session，執行 `/bombolt:bb-work <這個 issue 的編號>`

## 🎯 目標

前端和手機 app 可以修改已經存在的待辦（打錯字的標題、改期），不用刪掉重建。

## 背景

- 現在只能新增、標記完成、刪除（`todo/server.py` 的 `do_POST`、`do_DELETE`），沒有修改的 API。
- 使用者原本的描述：「標題打錯只能刪掉重打，到期日延後也一樣，很麻煩。」

## 🐞 問題與根因

無

## 範圍

**要做**
- `todo/store.py` 加 `TodoStore.update`。
- `todo/server.py` 加 `PATCH /api/todos/<id>`。
- `tests/test_server.py` 補上對應的測試。

**⛔ 不做**（實作時看到也不要順手改）
- 網頁 `/` 不加編輯功能，CLI 不加 `edit` 指令。
- 不改 `todo/store.py` 的 `_save`（包括上面的 TODO）。
- 不改 `README.md`、`CLAUDE.md`。

## ✅ 已確認的決策

| 題目 | 決定 | 被否決的選項與理由 |
|---|---|---|
| 方法與路徑 | `PATCH /api/todos/<id>`，body 是要改的欄位，成功回 200 `{"todo": {...}}`（經過 `to_json()`） | `PUT`（整筆覆寫）：前端只想改一個欄位，不想先 GET 再整筆送回來 |
| 可以改的欄位 | 只有 `title`、`due`；**沒帶的欄位保持原樣** | 開放全部欄位：`id`、`created_at` 不該被改；`done` 已經有 `POST /api/todos/<id>/done` |
| 清除到期日 | `{"due": null}` 或 `{"due": ""}` | 另外開一個 API：多一個端點，前端也比較麻煩 |
| 不合法的輸入 | 照 `CLAUDE.md` 的錯誤格式：空標題、到期日格式錯、帶了 `title`／`due` 以外的欄位 → 400 `invalid_todo`；body 不是 JSON 物件 → 400 `invalid_json`；找不到 → 404 `not_found` | — |

## 預設決定（沒有逐題詢問；有異議請在開工前直接改這個 issue）

- 標題一樣去掉頭尾空白（跟新增一致）。
- 一次可以同時改 `title` 和 `due`。

## 相關程式碼與既有慣例

- `todo/store.py:43` — `TodoStore.add`：標題與到期日的驗證方式（`parse_due` 丟 `ValueError`），`update` 照同樣的方式。
- `todo/server.py:120` — `do_POST`：讀 JSON body、組錯誤回應的寫法，`do_PATCH` 照抄。
- `todo/server.py:44` — `error_body(code, message)`：錯誤回應一律用它。
- `todo/server.py:48` — `to_json(todo, today_str)`：回傳的 todo 一律經過它。
- `tests/test_server.py:12` — `ServerTestCase`：server 測試的基底類別（`self.json(method, path, payload)` 回 `(status, body)`）。
- 這個區域的規則（`CLAUDE.md`）：API 的錯誤回應格式固定是 `{"error": {"code", "message"}}`；每個行為改動都要有 unittest。

## 實作步驟

- [ ] 1. `TodoStore.update`：找到那一筆、只改有帶的欄位、驗證方式跟 `add` 一樣、找不到丟 `NotFound`（`todo/store.py`）
- [ ] 2. `Handler.do_PATCH`：路徑 `/api/todos/<id>`，讀 body、檢查欄位、呼叫 `update`、照錯誤格式回應（`todo/server.py`）— 驗證：`curl -s -X PATCH http://localhost:<port>/api/todos/1 -H 'Content-Type: application/json' -d '{"title":"買豆漿"}'`
- [ ] 3. 在 `tests/test_server.py` 新增 `PatchTest(ServerTestCase)`
- [ ] 4. 跑驗證閘門 — 驗證：`python3 -m unittest discover -s tests` 最後一行 `OK`

## 🏁 完成定義（DoD）

實作 session 以這張表為唯一的完成標準：**每一條都要附證據，全部通過才算完成**。

| # | 條件（當…，則…） | 驗證方式 | 證據 |
|---|---|---|---|
| 1 | 當 `PATCH /api/todos/1` 帶 `{"title": " 買豆漿 "}`，則回 200，標題變成 `買豆漿`，**到期日不變** | 啟動 app，照「環境與前置」塞資料，`curl -s -X PATCH .../api/todos/1 -d '{"title":" 買豆漿 "}'`，再 `GET /api/todos` | 指令輸出 |
| 2 | 帶 `{"due": "2026-11-01"}`，則到期日改成 2026-11-01，標題不變；帶 `{"due": null}`，則到期日清除 | 同上 | 指令輸出 |
| 3 | 空標題、到期日格式錯、帶了 `title`／`due` 以外的欄位（例如 `done`、`id`），則回 400，body 是 `{"error": {"code": "invalid_todo", "message": "..."}}` | `curl -s -w '\n%{http_code}' -X PATCH ... -d '{"done":true}'` 等 | 指令輸出 |
| 4 | id 不存在，則回 404 `not_found`；body 不是 JSON 物件，則回 400 `invalid_json` | `curl -s -w '\n%{http_code}' -X PATCH .../api/todos/99 -d '{"title":"x"}'` | 指令輸出 |
| 5 | 新增的測試 `PatchTest` 涵蓋 1–4 並通過 | `python3 -m unittest tests.test_server -v` | 測試輸出 |
| 6 | 專案的驗證閘門全綠 | `python3 -m unittest discover -s tests` | 指令輸出 |
| 7 | 只改了這些檔：`todo/store.py`、`todo/server.py`、`tests/test_server.py` | `git diff --stat origin/main...HEAD` | 指令輸出 |

## 📸 截圖計畫

無（非 UI 改動，以測試證據代替）

## 環境與前置

照 `.claude/bombolt.md` 的「啟動 app」啟動，測試資料：

```bash
python3 -m todo.cli --data .bombolt/todos.json add "買牛奶" --due 2026-10-20
python3 -m todo.cli --data .bombolt/todos.json add "繳電費"
```

## ⚠️ 風險與已知的坑

- 「沒帶」跟「帶了 `null`」意思不同：`{"due": null}` 是清除，沒帶 `due` 是不動。
- 錯誤回 400／404 之後要 `return`，不然會接著再送一次 200 的回應。

## 🚀 本地開始執行

在 repo 的主 checkout 開一個新的 Claude Code session：

```bash
claude -n bb-{{repo}}-{{issue}}-{{slug}} --permission-mode auto "/bombolt:bb-work {{issue}}"
```

省略 `--permission-mode auto` 也能跑，只是每個動作都會先問你才做。
想先看看有哪些 issue 可以認領：`/bombolt:bb-list`。

<!-- bombolt:meta {"base":"main","base_sha":"{{base_sha}}","slug":"{{slug}}","ui":false,"planned_at":"{{date}}"} -->
