# todo-app

一個用 JSON 檔存資料的小型待辦清單：JSON API、網頁、命令列都有。只用 python 標準函式庫（python ≥ 3.9）。

## 結構

```
todo/dates.py    日期工具（YYYY-MM-DD 字串、今天、逾期判斷）
todo/store.py    資料存取（整份資料是一個 JSON 檔）
todo/server.py   HTTP 服務：JSON API ＋ 網頁
todo/cli.py      命令列
tests/           unittest
```

## 本機開發

```bash
python3 -m unittest discover -s tests                       # 測試
python3 -m todo.server --port 8000 --data data/todos.json  # 啟動，看到 ready on http://localhost:8000 就好了
python3 -m todo.cli --data data/todos.json add "買牛奶" --due 2026-10-20
python3 -m todo.cli --data data/todos.json list
```

`data/` 被 gitignore。設環境變數 `TODO_TODAY=2026-10-14` 可以固定「今天」（測試與驗收用）。

## API

| Method | Path | 說明 |
|---|---|---|
| GET | `/api/todos` | 列出全部：`{"todos": [{"id", "title", "done", "due", "created_at", "overdue"}]}` |
| POST | `/api/todos` | 新增：body `{"title": "...", "due": "YYYY-MM-DD"}`（due 可省略），回 201 `{"todo": {...}}` |
| POST | `/api/todos/<id>/done` | 標記完成，回 `{"todo": {...}}` |
| DELETE | `/api/todos/<id>` | 刪除，回 204 |

錯誤一律回 `{"error": {"code": "...", "message": "..."}}`，見 `CLAUDE.md`。

## 網頁

`/` 列出全部待辦（到期日、逾期標記、「完成」按鈕），下面有新增的表單。
