## 一句話

`todo list` 印完清單之後，最後一行多印「共 N 項，M 項未完成」。

## 改了什麼

- `cmd_list` 邊印邊數未完成的數量，最後印摘要（`todo/cli.py`）
- `test_add_and_list` 的預期輸出多一行摘要；新增 `test_list_summary`（`tests/test_cli.py`）

## 📝 程式碼改動範圍

`git diff --stat origin/main...HEAD` 的輸出（照實貼，不要摘要）：

```
 tests/test_cli.py | 8 +++++++-
 todo/cli.py       | 4 ++++
 2 files changed, 11 insertions(+), 1 deletion(-)
```

### 🔍 逐段改動

照 Files changed 的順序，一個檔案一個折疊區，點檔名展開；點段落標題的行號會跳到 Files changed 的那幾行。

<details>
<summary><b><code>tests/test_cli.py</code></b>　+7 −1　摘要行的測試</summary>

[在 Files changed 看整個檔案 ↗](https://github.com/acme/todo-app/pull/{{pr}}/changes#diff-4e8715c7a425ee52e74b7df4d34efd32e8c92f3e60bd51bc2e1ad5943b82032e)

##### `CliTest.test_add_and_list`、`test_list_summary`　[L33–L39](https://github.com/acme/todo-app/pull/{{pr}}/changes#diff-4e8715c7a425ee52e74b7df4d34efd32e8c92f3e60bd51bc2e1ad5943b82032eR33)

```diff
-     assertEqual(out, "[ ] 1 買牛奶 (due …)\n[x] 2 繳電費\n")
+ [1] assertEqual(out, "…[x] 2 繳電費\n共 2 項，1 項未完成\n")
+ [2] test_list_summary: add a、b → list 最後一行 "共 2 項，2 項未完成"
```

> **[1]** 既有測試的預期多一行摘要
> **[2]** 全部未完成時的摘要

</details>

<details>
<summary><b><code>todo/cli.py</code></b>　+4 −0　list 結尾印摘要</summary>

[在 Files changed 看整個檔案 ↗](https://github.com/acme/todo-app/pull/{{pr}}/changes#diff-66ca6b1b3ce0ce9622a7f5e5bb0ec10d15c0b582687a63e9d17bacf393d063fa)

##### `cmd_list`　[L37–L42](https://github.com/acme/todo-app/pull/{{pr}}/changes#diff-66ca6b1b3ce0ce9622a7f5e5bb0ec10d15c0b582687a63e9d17bacf393d063faR37)

```diff
+     pending = 0
      for todo in todos: print(format_line(todo))  （沒改）
+ [1]     if not todo["done"]: pending += 1
+ [2] print(f"共 {len(todos)} 項，{pending} 項未完成")
```

> **[1]** 邊印邊數未完成的
> **[2]** 清單最後一行印摘要；空清單在前面就 return 了，不會印

</details>

## 為什麼這樣做

- 摘要放在空清單提早 return 之後，沒有待辦時維持原本的「還沒有任何待辦事項」（issue 的決策）。
- 在同一個迴圈裡數未完成的，不另外再走一次清單。

## 📌 需求調整（相對於 #{{issue}}）

無

## ✅ 驗收結果（對照 #{{issue}} 的完成定義 ＋ 📌 需求調整）

驗的是 `{{head_sha_short}}`

| # | 條件 | 結果 | 證據 |
|---|---|---|---|
| 1 | 2 筆、1 筆完成時最後一行是 `共 2 項，1 項未完成` | ✅ | `python3 -m todo.cli --data .bombolt/todos.json list` → `[ ] 1 買牛奶`、`[x] 2 繳電費`、`共 2 項，1 項未完成` |
| 2 | 沒有待辦時沒有摘要 | ✅ | `--data .bombolt/empty.json list` → `還沒有任何待辦事項` |
| 3 | `test_list_summary`、`test_add_and_list` 通過 | ✅ | `test_add_and_list ... ok`、`test_list_summary ... ok` |
| 4 | 驗證閘門全綠 | ✅ | `Ran 25 tests ... OK` |
| 5 | 只改了 `todo/cli.py`、`tests/test_cli.py` | ✅ | 見上面的 diffstat |

## 📸 Before / After

非 UI 改動，證據見上表

## 👀 請你重點看

1. 摘要的文字（`todo/cli.py:42`）是照 issue 的決策寫的，全形逗號。

## 📋 人工測試（merge 前）

> 預計 2 分鐘。「要你測的」與「AI 已經測過」除了標 ⚠️ 和「只有自動測試」的，每一步 AI 都已經在本機照做過一遍，結果跟預期一樣。

**環境：本機**

1. 在這個 PR 的 worktree 根目錄執行（CLI 不用啟動服務）。
2. 測試資料：`python3 -m todo.cli --data .bombolt/todos.json add "買牛奶"`、`add "繳電費"`、`done 2`。

**要你測的**

- [ ] 1. `python3 -m todo.cli --data .bombolt/todos.json list` → 最後一行是 `共 2 項，1 項未完成`（文案要你看一眼）

<details>
<summary>AI 已經測過、你不用重測的（1 項）</summary>

- `python3 -m todo.cli --data .bombolt/empty.json list` → 只有 `還沒有任何待辦事項`（DoD 2）

</details>

有一步不對：在 PR 留言寫「要你測的第 n 步：做了什麼、看到什麼」，再照「🔁 要修改的話」。

## 🔜 Follow-up

- 無

## 🔁 要修改的話

<!-- bombolt:handoff -->

<details>
<summary>🤖 bombolt 回饋（給 pipeline 迭代用）</summary>

- 驗收迴圈跑了幾輪：1
- 有沒有用到「停工提問」：沒有
- 實作時需要自己判斷、issue 沒寫清楚的地方：無
- 卡住或浪費時間的地方：無
- 改動規模：2 個檔、+11 / -1 行

</details>

Closes #{{issue}}
