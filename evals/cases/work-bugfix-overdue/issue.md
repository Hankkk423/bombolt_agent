> 🤖 由 bombolt 規劃 · 規劃依據 `origin/main` @ `{{base_sha_short}}` · {{date}}
> 實作方式：在 repo 的主 checkout 開一個新的 Claude Code session，執行 `/bombolt:bb-work <這個 issue 的編號>`

## 🎯 目標

到期日是今天的待辦，在 API（`overdue` 欄位）和網頁上都不再顯示成逾期；過了到期日（昨天以前）的才算逾期。

## 背景

- 使用者回報：「早上剛加的『今天要繳電費』，一打開就紅字寫逾期，可是今天還沒過完。」
- 逾期判斷只有一個地方：`todo/dates.py:22` 的 `is_overdue(todo, today_str)`。
  API 的 `overdue` 欄位（`todo/server.py:49` 的 `to_json`）和網頁的「逾期」紅字（`todo/server.py:56` 的 `render_item`）都呼叫它。
- 產品上「逾期」的定義：**到期日已經過去**（due 早於今天）。到期日當天整天都還不算逾期。

## 🐞 問題與根因

**重現步驟**
1. 照 `.claude/bombolt.md` 啟動 app，啟動時加 `TODO_TODAY=2026-10-14` 固定今天。
2. `python3 -m todo.cli --data .bombolt/todos.json add "繳電費" --due 2026-10-14`
3. `curl -s http://localhost:<port>/api/todos`
- 預期：這筆的 `"overdue": false`；網頁 `http://localhost:<port>/` 上這筆沒有紅色的「逾期」。
- 實際：`"overdue": true`；網頁上顯示紅色的「逾期」。

**根因**：`todo/dates.py:26` 用 `todo["due"] <= today_str` 判斷，把「due 等於今天」也算成逾期。應該是 `<`。
證據：日期一律是 `YYYY-MM-DD` 字串（`CLAUDE.md`），字串比較等同日期比較；`is_overdue({"done": False, "due": "2026-10-14"}, "2026-10-14")` 回 `True`（已在規劃時用快照實際執行確認）。

**為什麼之前沒被抓到**：`tests/test_dates.py` 的 `IsOverdueTest` 只測了過去與未來，沒有測「剛好今天」這個邊界。

## 範圍

**要做**
- 修正 `todo/dates.py` 的 `is_overdue` 邊界。
- 在 `tests/test_dates.py` 的 `IsOverdueTest` 補上重現這個 bug 的測試。

**⛔ 不做**（實作時看到也不要順手改）
- 不改 `today()`、`parse_due()`，也不改時區處理（全部以 `today()` 的日期字串為準）。
- 不改網頁的樣式、文案，也不改 API 回應的欄位。
- 不改 `todo/store.py`（包括 `_save` 上的 TODO）。

## ✅ 已確認的決策

| 題目 | 決定 | 被否決的選項與理由 |
|---|---|---|
| 逾期的定義 | due **早於**今天才算逾期；今天到期不算 | 「今天到期也提醒」：那是另一種提示（例如「今天到期」標籤），要另外開需求，不該混在「逾期」裡 |
| 修在哪裡 | 只改 `is_overdue` 的比較運算子 | 在 `render_item`／`to_json` 各自加條件：兩邊會不一致，而且根因在 `is_overdue` |

## 預設決定（沒有逐題詢問；有異議請在開工前直接改這個 issue）

- 已完成的待辦維持「永遠不算逾期」（現有行為，`todo/dates.py:24`）。
- 沒有到期日的待辦維持「不算逾期」（現有行為）。

## 相關程式碼與既有慣例

- `todo/dates.py:22` — `is_overdue(todo, today_str)`：要改的函式，錯在第 26 行。
- `todo/dates.py:7` — `today()`：讀環境變數 `TODO_TODAY`，驗收用它固定今天。
- `todo/server.py:49`、`todo/server.py:56` — 呼叫 `is_overdue` 的兩個地方（不用改，修好 `is_overdue` 兩邊就一起對）。
- `tests/test_dates.py:19` — `IsOverdueTest`：新測試加在這裡。
- 這個區域的規則（`CLAUDE.md`）：日期一律 `YYYY-MM-DD` 字串、用 `todo/dates.py` 的函式處理；每個行為改動都要有 unittest。

## 實作步驟

- [ ] 1. **先寫重現測試**：在 `IsOverdueTest` 新增 `test_due_today_is_not_overdue`（`is_overdue({"done": False, "due": "2026-10-14"}, "2026-10-14")` 要是 `False`），確認**修改前會失敗**（`tests/test_dates.py`）。修改前沒有失敗 → 根因不對，走停工提問。
- [ ] 2. 把 `todo/dates.py:26` 的 `<=` 改成 `<` — 驗證：步驟 1 的測試通過
- [ ] 3. 跑驗證閘門 — 驗證：`python3 -m unittest discover -s tests` 最後一行 `OK`
- [ ] 4. 照「🐞 重現步驟」實際啟動 app 確認 API 與網頁

## 🏁 完成定義（DoD）

實作 session 以這張表為唯一的完成標準：**每一條都要附證據，全部通過才算完成**。

| # | 條件（當…，則…） | 驗證方式 | 證據 |
|---|---|---|---|
| 1 | 新增的測試 `test_due_today_is_not_overdue` 重現這個問題（修改前失敗），修改後通過 | 修改前後各跑一次 `python3 -m unittest tests.test_dates -v` | 兩次的測試輸出 |
| 2 | 當今天是 2026-10-14、有一筆 due 2026-10-14 的待辦，則 `GET /api/todos` 回的那筆 `"overdue": false` | `TODO_TODAY=2026-10-14` 啟動 app，照重現步驟 `curl` | 指令輸出 |
| 3 | 同上，再加一筆 due 2026-10-13 的待辦，則那筆 `"overdue": true` | 同上 | 指令輸出 |
| 4 | 網頁 `/` 上，2026-10-14 那筆沒有「逾期」，2026-10-13 那筆有紅色的「逾期」 | 用 playwright-cli 打開 `http://localhost:<port>/`，`snapshot` 讀畫面 | 畫面上看到的內容 |
| 5 | 專案的驗證閘門全綠 | `python3 -m unittest discover -s tests` | 指令輸出 |
| 6 | 只改了這些檔：`todo/dates.py`、`tests/test_dates.py` | `git diff --stat origin/main...HEAD` | 指令輸出 |

## 📸 截圖計畫

無（邏輯修正，網頁的變化用 DoD 4 的畫面內容當證據）

## 環境與前置

- 啟動 app 時一定要加 `TODO_TODAY=2026-10-14`，否則結果會隨實際日期變。
- 測試資料：
  ```bash
  python3 -m todo.cli --data .bombolt/todos.json add "繳電費" --due 2026-10-14
  python3 -m todo.cli --data .bombolt/todos.json add "寄回郵件" --due 2026-10-13
  ```

## ⚠️ 風險與已知的坑

- 改的是一個字元，但 `is_overdue` 同時影響 API 與網頁，兩邊都要實際看過。
- 不要用 `date.today()` 寫測試，測試要固定日期字串（`CLAUDE.md`）。

## 🚀 本地開始執行

在 repo 的主 checkout 開一個新的 Claude Code session：

```bash
claude -n bb-{{repo}}-{{issue}}-{{slug}} --permission-mode auto "/bombolt:bb-work {{issue}}"
```

省略 `--permission-mode auto` 也能跑，只是每個動作都會先問你才做。
想先看看有哪些 issue 可以認領：`/bombolt:bb-list`。

<!-- bombolt:meta {"base":"main","base_sha":"{{base_sha}}","slug":"{{slug}}","ui":false,"planned_at":"{{date}}"} -->
