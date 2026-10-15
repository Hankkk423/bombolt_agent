> 🤖 由 bombolt 規劃 · 規劃依據 `origin/main` @ `{{base_sha_short}}` · {{date}}
> 實作方式：在 repo 的主 checkout 開一個新的 Claude Code session，執行 `/bombolt:bb-work <這個 issue 的編號>`

## 🎯 目標

使用者可以一次清掉所有已完成的待辦，而且清掉的資料真的從資料檔移除，不留在電腦上。

## 背景

- 現在只能一筆一筆刪（`DELETE /api/todos/<id>`，`todo/server.py:161`），已完成的累積幾百筆之後很難清。
- 刪除現在是真的把待辦從資料檔移除（`todo/store.py:69` 的 `TodoStore.delete`）。
- 使用者原本的描述：「做完的我不想再看到，也不想留在檔案裡，裡面有些是私事。一鍵全部清掉。」

## 🐞 問題與根因

無

## 範圍

**要做**
- `todo/store.py` 加 `TodoStore.clear_done()`。
- `todo/server.py` 加 `DELETE /api/todos/done`。
- `tests/` 補上對應的測試。

**⛔ 不做**（實作時看到也不要順手改）
- 網頁 `/` 不加按鈕，CLI 不加指令（之後另外開 issue）。
- 不改單筆刪除 `DELETE /api/todos/<id>` 的行為。
- 不改 `todo/store.py` 的 `_save`（包括上面的 TODO）。

## ✅ 已確認的決策

| 題目 | 決定 | 被否決的選項與理由 |
|---|---|---|
| 清掉是什麼意思 | **真的從資料檔刪除**，跟現在的單筆刪除一樣 | 加 `archived` 旗標只是隱藏：使用者明確說「不想留在檔案裡，裡面有些是私事」 |
| API | `DELETE /api/todos/done`，回 200 `{"deleted": <刪掉幾筆>}` | 回 204：前端要顯示「已清除 N 筆」 |
| 沒有已完成的 | 一樣回 200 `{"deleted": 0}` | 404：沒東西可清不是錯誤 |

## 預設決定（沒有逐題詢問；有異議請在開工前直接改這個 issue）

- 實作照 `TodoStore.delete` 的方式：讀檔、過濾、存檔一次。
- `next_id` 不變（刪掉的 id 不重用）。

## 相關程式碼與既有慣例

- `todo/store.py:69` — `TodoStore.delete`：真的從資料檔移除的寫法，`clear_done` 照它。
- `todo/server.py:161` — `do_DELETE`：路徑判斷與錯誤回應的寫法。注意 `/api/todos/done` 要在 `/api/todos/<id>` 之前判斷。
- `tests/test_store.py`、`tests/test_server.py`：對應的測試檔。
- 這個區域的規則（`CLAUDE.md`）：API 的錯誤回應格式固定；每個行為改動都要有 unittest。

## 實作步驟

- [ ] 1. `TodoStore.clear_done()`：把 `done` 是 true 的待辦從資料檔移除，回傳刪掉幾筆（`todo/store.py`）
- [ ] 2. `do_DELETE` 處理 `/api/todos/done`（`todo/server.py`）— 驗證：`curl -s -X DELETE http://localhost:<port>/api/todos/done`
- [ ] 3. 補測試（`tests/test_store.py`、`tests/test_server.py`）
- [ ] 4. 跑驗證閘門 — 驗證：`python3 -m unittest discover -s tests` 最後一行 `OK`

## 🏁 完成定義（DoD）

實作 session 以這張表為唯一的完成標準：**每一條都要附證據，全部通過才算完成**。

| # | 條件（當…，則…） | 驗證方式 | 證據 |
|---|---|---|---|
| 1 | 當有 3 筆待辦、其中 2 筆已完成，`DELETE /api/todos/done`，則回 200 `{"deleted": 2}`，`GET /api/todos` 只剩未完成的那筆 | 啟動 app，照「環境與前置」塞資料，`curl` | 指令輸出 |
| 2 | 清除之後，**資料檔裡也沒有**那 2 筆（不是隱藏） | `cat .bombolt/todos.json` | 檔案內容 |
| 3 | 沒有已完成的待辦時，回 200 `{"deleted": 0}` | `curl -s -X DELETE http://localhost:<port>/api/todos/done` | 指令輸出 |
| 4 | `DELETE /api/todos/<id>` 的行為不變 | 既有的測試 | 測試輸出 |
| 5 | 新增的測試涵蓋 1–3 並通過 | `python3 -m unittest discover -s tests -v` | 測試輸出 |
| 6 | 專案的驗證閘門全綠 | `python3 -m unittest discover -s tests` | 指令輸出 |
| 7 | 只改了 `todo/store.py`、`todo/server.py`、`tests/` 底下的檔 | `git diff --stat origin/main...HEAD` | 指令輸出 |

## 📸 截圖計畫

無（非 UI 改動，以測試證據代替）

## 環境與前置

照 `.claude/bombolt.md` 的「啟動 app」啟動，測試資料：

```bash
python3 -m todo.cli --data .bombolt/todos.json add "買牛奶"
python3 -m todo.cli --data .bombolt/todos.json add "繳電費"
python3 -m todo.cli --data .bombolt/todos.json add "回信"
python3 -m todo.cli --data .bombolt/todos.json done 1
python3 -m todo.cli --data .bombolt/todos.json done 3
```

## ⚠️ 風險與已知的坑

- 路徑 `/api/todos/done` 會被 `/api/todos/<id>` 的判斷吃掉（`int("done")` 失敗變成 `None`），要先判斷。

## 🚀 本地開始執行

在 repo 的主 checkout 開一個新的 Claude Code session：

```bash
claude -n bb-{{repo}}-{{issue}}-{{slug}} --permission-mode auto "/bombolt:bb-work {{issue}}"
```

省略 `--permission-mode auto` 也能跑，只是每個動作都會先問你才做。
想先看看有哪些 issue 可以認領：`/bombolt:bb-list`。

<!-- bombolt:meta {"base":"main","base_sha":"{{base_sha}}","slug":"{{slug}}","ui":false,"planned_at":"{{date}}"} -->
