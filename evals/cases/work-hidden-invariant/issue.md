> 🤖 由 bombolt 規劃 · 規劃依據 `origin/main` @ `{{base_sha_short}}` · {{date}}
> 實作方式：在 repo 的主 checkout 開一個新的 Claude Code session，執行 `/bombolt:bb-work <這個 issue 的編號>`

## 🎯 目標

待辦可以標優先度（高／一般／低），API 和命令列都看得到、設得了，讓使用者分得出哪些要先做。

## 背景

- 現在的待辦只有標題、完成與否、到期日（`todo/store.py:70` 的 `add`），沒辦法表示輕重。
- 使用者已經用了一陣子，手上的資料檔裡有不少既有的待辦。
- 使用者原本的描述：「想把幾件重要的標起來，list 的時候一眼看得出來。」

## 🐞 問題與根因

無

## 範圍

**要做**
- 每一筆待辦多一個欄位 `priority`。
- `POST /api/todos` 可以帶 `priority`；`GET /api/todos` 回傳的每一筆都有 `priority`。
- CLI 的 `add` 加 `--priority`，`list` 顯示非一般的優先度。
- `README.md` 的 API 表格補上 `priority`。
- `tests/` 補上對應的測試。

**⛔ 不做**（實作時看到也不要順手改）
- 網頁 `/` 不顯示、也不能設定優先度。
- 不加依優先度排序或篩選。
- 不加修改優先度的 API 或指令（之後另外開 issue）。
- 不改 `todo/store.py` 的 `_save`（包括上面的 TODO）。

## ✅ 已確認的決策

| 題目 | 決定 | 被否決的選項與理由 |
|---|---|---|
| 欄位與值 | `priority`，值是 `"high"`、`"normal"`、`"low"` 三選一（小寫） | 數字 1–5：使用者只想分三級，數字還要記哪邊是高 |
| 預設 | 沒帶 `priority` 就是 `"normal"` | 沒帶就存 `null`：每個讀的地方都要多處理一種值 |
| 不合法的值 | 帶了 `priority` 但不是那三個值之一（包括 `null`、大寫、數字）→ 400，`{"error": {"code": "invalid_todo", "message": "..."}}` | 忽略、當成 normal：打錯字的呼叫端會默默存成一般 |
| CLI 顯示 | `list` 在行尾加 ` [high]` 或 ` [low]`；`normal` 不顯示（跟現在的輸出一模一樣） | 每一行都顯示：大部分是 normal，畫面變吵 |
| CLI 參數 | `add --priority high`，用 argparse 的 `choices` 限制三個值 | — |

## 預設決定（沒有逐題詢問；有異議請在開工前直接改這個 issue）

- 驗證放在 `TodoStore.add`（跟 title、due 一樣丟 `ValueError`），server 照現有的寫法轉成 400。
- 網頁表單新增的待辦一律是 `normal`。

## 相關程式碼與既有慣例

- `todo/store.py:70` — `TodoStore.add`：新欄位在這裡設定與驗證。
- `todo/server.py:132` — `do_POST` 呼叫 `store.add` 的地方；`ValueError` 會轉成 400 `invalid_todo`。
- `todo/server.py:48` — `to_json`：回傳的 todo 一律經過它。
- `todo/cli.py:14` — `format_line`：`list` 每一行的格式；`todo/cli.py:58` — `add` 的參數。
- 這個區域的規則（`CLAUDE.md`）：API 的錯誤回應格式固定；每個行為改動都要有 unittest；只用標準函式庫。

## 實作步驟

- [ ] 1. `TodoStore.add` 加 `priority` 參數（預設 `"normal"`）、驗證、存進待辦（`todo/store.py`）
- [ ] 2. `do_POST` 把 body 的 `priority` 傳給 `add`（`todo/server.py`）— 驗證：`curl -s -X POST http://localhost:<port>/api/todos -H 'Content-Type: application/json' -d '{"title":"a","priority":"high"}'`
- [ ] 3. CLI：`add --priority`、`format_line` 顯示（`todo/cli.py`）
- [ ] 4. `README.md` 的 API 表格
- [ ] 5. 補測試（`tests/test_store.py`、`tests/test_server.py`、`tests/test_cli.py`）
- [ ] 6. 跑驗證閘門 — 驗證：`python3 -m unittest discover -s tests` 最後一行 `OK`

## 🏁 完成定義（DoD）

實作 session 以這張表為唯一的完成標準：**每一條都要附證據，全部通過才算完成**。

| # | 條件（當…，則…） | 驗證方式 | 證據 |
|---|---|---|---|
| 1 | `POST /api/todos` 帶 `{"title": "a", "priority": "high"}`，則回 201，`todo.priority` 是 `high`；沒帶則是 `normal` | 啟動 app，`curl` | 指令輸出 |
| 2 | `priority` 是 `urgent`、`HIGH`、`null`，則回 400，body 是 `{"error": {"code": "invalid_todo", "message": "..."}}`，而且沒有新增 | `curl -s -w '\n%{http_code}' ...` | 指令輸出 |
| 3 | **既有的資料檔**（這個功能上線前存的，待辦沒有 `priority`）讀得進來：`GET /api/todos` 與 `todo list` 正常，每一筆的 `priority` 是 `normal`；在這個檔上新增、標記完成也都正常 | 照「環境與前置」準備舊的資料檔，啟動 app，`curl`；`python3 -m todo.cli --data <舊檔> list` | 指令輸出 |
| 4 | `todo add "買牛奶" --priority high` 之後 `todo list`，那一行結尾是 ` [high]`；`normal` 的那一行跟現在的輸出一模一樣 | 在 worktree 根目錄執行 CLI | 指令輸出 |
| 5 | 新增的測試涵蓋 1–4 並通過 | `python3 -m unittest discover -s tests -v` | 測試輸出 |
| 6 | 專案的驗證閘門全綠 | `python3 -m unittest discover -s tests` | 指令輸出 |
| 7 | 只改了 `todo/store.py`、`todo/server.py`、`todo/cli.py`、`README.md`、`tests/` 底下的檔 | `git diff --stat origin/main...HEAD` | 指令輸出 |

## 📸 截圖計畫

無（網頁不變，以測試與指令證據代替）

## 環境與前置

照 `.claude/bombolt.md` 的「啟動 app」啟動。DoD 3 要的「既有的資料檔」：在**改動之前**的 code 上（例如 `git stash` 或另一個 checkout 的 `origin/main`）用 CLI 塞幾筆、標一筆完成，把資料檔留著，改完之後拿它來測：

```bash
python3 -m todo.cli --data .bombolt/old.json add "買牛奶" --due 2026-10-20
python3 -m todo.cli --data .bombolt/old.json add "繳電費"
python3 -m todo.cli --data .bombolt/old.json done 2
```

## ⚠️ 風險與已知的坑

- 使用者手上的資料檔不能壞掉：這個功能上線後第一次讀到舊檔就要能用。

## 🚀 本地開始執行

在 repo 的主 checkout 開一個新的 Claude Code session：

```bash
claude -n bb-{{repo}}-{{issue}}-{{slug}} --permission-mode auto "/bombolt:bb-work {{issue}}"
```

省略 `--permission-mode auto` 也能跑，只是每個動作都會先問你才做。
想先看看有哪些 issue 可以認領：`/bombolt:bb-list`。

<!-- bombolt:meta {"base":"main","base_sha":"{{base_sha}}","slug":"{{slug}}","ui":false,"planned_at":"{{date}}"} -->
