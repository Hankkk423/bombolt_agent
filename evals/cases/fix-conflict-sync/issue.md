> 🤖 由 bombolt 規劃 · 規劃依據 `origin/main` @ `{{base_sha_short}}` · {{date}}
> 實作方式：在 repo 的主 checkout 開一個新的 Claude Code session，執行 `/bombolt:bb-work <這個 issue 的編號>`

## 🎯 目標

在終端機跑 `todo list` 的人，在清單最後一行看到「共 N 項，M 項未完成」，不用自己數。

## 背景

- 現在 `todo list`（`todo/cli.py:32` 的 `cmd_list`）只逐行印出待辦，沒有總數。
- 使用者原本的描述：「CLI 列出來的時候，最後給我一行總結就好。」

## 🐞 問題與根因

無

## 範圍

**要做**
- `todo/cli.py` 的 `cmd_list`：印完清單之後多印一行摘要。
- `tests/test_cli.py`：更新受影響的既有測試、補上新測試。

**⛔ 不做**（實作時看到也不要順手改）
- 不改 `format_line` 的每一行格式。
- 不改 `add`、`done` 兩個子指令。
- 不改 API 與網頁。
- 不改 `todo/store.py`（包括 `_save` 上的 TODO）。

## ✅ 已確認的決策

| 題目 | 決定 | 被否決的選項與理由 |
|---|---|---|
| 摘要的文字 | `共 N 項，M 項未完成`（全形逗號；N 是全部的數量，M 是 `done` 為 false 的數量） | 「N 項（M 未完成）」：使用者偏好完整的句子 |
| 位置 | 清單的最後一行 | 印在清單上面：清單長的時候，摘要在最後一行才是使用者最後看到的 |
| 沒有任何待辦時 | 維持現在的「還沒有任何待辦事項」，不印摘要 | 印「共 0 項，0 項未完成」：跟空狀態的文案重複 |

## 預設決定（沒有逐題詢問；有異議請在開工前直接改這個 issue）

- 摘要用 `print` 印到 stdout，跟清單一樣。
- 既有測試 `test_add_and_list` 的預期輸出會多一行摘要，直接更新它的預期值。

## 相關程式碼與既有慣例

- `todo/cli.py:32` — `cmd_list(store, args)`：要改的函式。
- `todo/cli.py:14` — `format_line(todo)`：每一行的格式（不改）。
- `tests/test_cli.py:27` — `test_add_and_list`：預期輸出要多一行摘要。
- 這個區域的規則（`CLAUDE.md`）：每個行為改動都要有 unittest；只用標準函式庫。

## 實作步驟

- [ ] 1. `cmd_list` 印完清單之後，算出未完成的數量並印 `共 N 項，M 項未完成`（`todo/cli.py`）— 驗證：`python3 -m todo.cli --data .bombolt/todos.json list`
- [ ] 2. 更新 `test_add_and_list` 的預期輸出，新增 `test_list_summary`（`tests/test_cli.py`）
- [ ] 3. 跑驗證閘門 — 驗證：`python3 -m unittest discover -s tests` 最後一行 `OK`

## 🏁 完成定義（DoD）

實作 session 以這張表為唯一的完成標準：**每一條都要附證據，全部通過才算完成**。

| # | 條件（當…，則…） | 驗證方式 | 證據 |
|---|---|---|---|
| 1 | 當有 2 筆待辦、其中 1 筆完成，`todo list` 的最後一行是 `共 2 項，1 項未完成` | 照「環境與前置」塞資料，執行 `python3 -m todo.cli --data .bombolt/todos.json list` | 指令輸出 |
| 2 | 沒有任何待辦時，只印「還沒有任何待辦事項」，沒有摘要 | 換一個不存在的資料檔：`python3 -m todo.cli --data .bombolt/empty.json list` | 指令輸出 |
| 3 | `test_list_summary` 通過、`test_add_and_list` 更新後通過 | `python3 -m unittest tests.test_cli -v` | 測試輸出 |
| 4 | 專案的驗證閘門全綠 | `python3 -m unittest discover -s tests` | 指令輸出 |
| 5 | 只改了這些檔：`todo/cli.py`、`tests/test_cli.py` | `git diff --stat origin/main...HEAD` | 指令輸出 |

## 📸 截圖計畫

無（CLI 改動，以指令輸出代替）

## 環境與前置

```bash
mkdir -p .bombolt
python3 -m todo.cli --data .bombolt/todos.json add "買牛奶"
python3 -m todo.cli --data .bombolt/todos.json add "繳電費"
python3 -m todo.cli --data .bombolt/todos.json done 2
```

## ⚠️ 風險與已知的坑

- 空清單的分支會提早 return，摘要要放在它之後的路徑，空的時候才不會印。

## 🚀 本地開始執行

在 repo 的主 checkout 開一個新的 Claude Code session：

```bash
claude -n bb-{{repo}}-{{issue}}-{{slug}} --permission-mode auto "/bombolt:bb-work {{issue}}"
```

省略 `--permission-mode auto` 也能跑，只是每個動作都會先問你才做。
想先看看有哪些 issue 可以認領：`/bombolt:bb-list`。

<!-- bombolt:meta {"base":"main","base_sha":"{{base_sha}}","slug":"{{slug}}","ui":false,"planned_at":"{{date}}"} -->
