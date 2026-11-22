> 🤖 由 bombolt 規劃 · 規劃依據 `origin/main` @ `{{base_sha_short}}` · {{date}}
> 實作方式：在 repo 的主 checkout 開一個新的 Claude Code session，執行 `/bombolt:bb-work <這個 issue 的編號>`

## 🎯 目標

在終端機一眼看到還有幾件事沒做完，不用自己數 `list` 的輸出。

## 背景

- 現在 CLI 只有 `add`、`list`、`done`（`todo/cli.py`），要知道還剩幾件沒做，只能 `list` 之後自己數 `[ ]`。
- 使用者原本的描述：「想要一個指令直接告訴我總共幾件、還有幾件沒做完。」

## 🐞 問題與根因

無

## 範圍

**要做**
- `todo/cli.py` 新增子指令 `count`。
- `tests/test_cli.py` 補上對應的測試。

**⛔ 不做**（實作時看到也不要順手改）
- 不加參數（例如只算某個到期日之前的）。
- 不改 `list` 的輸出、不改 API 與網頁。
- 不改 `todo/store.py`（包括 `_save` 上的 TODO）。

## ✅ 已確認的決策

| 題目 | 決定 | 被否決的選項與理由 |
|---|---|---|
| 輸出 | 一行：`共 {全部筆數} 筆，未完成 {未完成筆數} 筆`；未完成＝`done` 是 false 的筆數 | 分兩行：使用者要「一眼看到」，一行就夠 |
| 沒有任何待辦 | 照樣印 `共 0 筆，未完成 0 筆`，exit 0 | 印「還沒有任何待辦事項」：跟 `list` 一樣，但 `count` 的呼叫端（例如 shell 腳本）會想要固定格式 |

## 預設決定（沒有逐題詢問；有異議請在開工前直接改這個 issue）

- 統計在 `cmd_count` 裡做（`store.list()` 之後自己算），不改 `TodoStore`。
- 子指令的 help 文字：`統計待辦數量`。

## 相關程式碼與既有慣例

- `todo/cli.py:32` — `cmd_list(store, args)`：新指令照它的寫法（讀 `store.list()`、`print`、回傳 exit code）。
- `todo/cli.py:52` — `build_parser()`：在這裡加子指令；`COMMANDS` 要加上對應。
- `tests/test_cli.py:10` — `CliTest`：`run_cli(*argv)` 回傳 `(exit code, stdout, stderr)`。
- 這個區域的規則（`CLAUDE.md`）：每個行為改動都要有 unittest。

## 實作步驟

- [ ] 1. 新增 `cmd_count`：全部筆數與未完成筆數，照「輸出」的格式印一行（`todo/cli.py`）
- [ ] 2. `build_parser` 加 `count` 子指令、`COMMANDS` 加上 `cmd_count`（`todo/cli.py`）— 驗證：`python3 -m todo.cli --data /tmp/x.json count`
- [ ] 3. 在 `tests/test_cli.py` 的 `CliTest` 新增 `count` 的測試
- [ ] 4. 跑驗證閘門 — 驗證：`python3 -m unittest discover -s tests` 最後一行 `OK`

## 🏁 完成定義（DoD）

實作 session 以這張表為唯一的完成標準：**每一條都要附證據，全部通過才算完成**。

| # | 條件（當…，則…） | 驗證方式 | 證據 |
|---|---|---|---|
| 1 | 沒有任何待辦時，`count` 印 `共 0 筆，未完成 0 筆`、exit 0 | `python3 -m todo.cli --data <空的路徑> count` | 指令輸出 |
| 2 | 有待辦時，`count` 印 `共 N 筆，未完成 M 筆`：N 是全部的筆數，M 是 `done` 是 false 的筆數（已完成的不算） | 照「環境與前置」塞資料（含一筆已完成），執行 `count` | 指令輸出 |
| 3 | 新增的測試涵蓋 1、2 並通過 | `python3 -m unittest tests.test_cli -v` | 測試輸出 |
| 4 | 專案的驗證閘門全綠 | `python3 -m unittest discover -s tests` | 指令輸出 |
| 5 | 只改了這些檔：`todo/cli.py`、`tests/test_cli.py` | `git diff --stat origin/main...HEAD` | 指令輸出 |

## 📸 截圖計畫

無（非 UI 改動，以測試證據代替）

## 環境與前置

CLI，不用啟動 app。測試資料：

```bash
python3 -m todo.cli --data .bombolt/todos.json add "買牛奶"
python3 -m todo.cli --data .bombolt/todos.json add "繳電費"
python3 -m todo.cli --data .bombolt/todos.json done 1
```

## ⚠️ 風險與已知的坑

- 資料檔不存在時 `TodoStore` 當成空的（`list` 也是這樣），不用另外處理。

## 🚀 本地開始執行

在 repo 的主 checkout 開一個新的 Claude Code session：

```bash
claude -n bb-{{repo}}-{{issue}}-{{slug}} --permission-mode auto "/bombolt:bb-work {{issue}}"
```

省略 `--permission-mode auto` 也能跑，只是每個動作都會先問你才做。
想先看看有哪些 issue 可以認領：`/bombolt:bb-list`。

<!-- bombolt:meta {"base":"main","base_sha":"{{base_sha}}","slug":"{{slug}}","ui":false,"planned_at":"{{date}}"} -->
