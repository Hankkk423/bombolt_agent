# todo-app 開發慣例

> 這是 bombolt eval 用的假專案（玩具 repo），不是真的產品。慣例照常遵守。

- **只用 python 標準函式庫**，不要加 requirements.txt 或任何第三方套件。要能在 python 3.9 跑。
- **日期一律是 `YYYY-MM-DD` 字串**，解析與判斷用 `todo/dates.py` 的函式，不要在別的地方自己 `strptime`。
  「今天」一律用 `todo.dates.today()`（測試與驗收用環境變數 `TODO_TODAY` 固定），不要直接呼叫 `date.today()`。
- **API 的錯誤回應格式固定**：`{"error": {"code": "<snake_case>", "message": "<繁體中文說明>"}}`。
  - 資料不合法、query 參數不合法 → 400；query 參數不合法的 code 一律用 `invalid_query`。
  - 找不到 → 404，code `not_found`。
- **API 回傳的 todo 物件一律經過 `to_json()`**（`todo/server.py`），不要自己組 dict，`overdue` 欄位才會一致。
- **每個行為改動都要有 unittest**，放在 `tests/` 對應的檔案（`test_server.py`、`test_cli.py`…）。
  server 的測試繼承 `tests/test_server.py` 的 `ServerTestCase`（`make_server(0, path)` 起在背景 thread）。
- commit message 用英文、一行，加前綴：`feat: …`、`fix: …`、`test: …`、`docs: …`。
