## 一句話

CLI 新增 `count` 指令，印出「共 N 筆，未完成 M 筆」。

## 改了什麼

- 新增 `cmd_count` 與 `count` 子指令（`todo/cli.py`）
- 新增 `test_count`（`tests/test_cli.py`）

## 📝 程式碼改動範圍

`git diff --stat origin/main...HEAD` 的輸出（照實貼，不要摘要）：

```
 tests/test_cli.py |  5 +++++
 todo/cli.py       | 11 ++++++++++-
 2 files changed, 15 insertions(+), 1 deletion(-)
```

### 🔍 逐段改動

照 Files changed 的順序，一個檔案一個折疊區，點檔名展開；點段落標題的行號會跳到 Files changed 的那幾行。

<details>
<summary><b><code>tests/test_cli.py</code></b>　+5 −0　新增 count 的測試</summary>

[在 Files changed 看整個檔案 ↗](https://github.com/acme/todo-app/pull/{{pr}}/changes#diff-4e8715c7a425ee52e74b7df4d34efd32e8c92f3e60bd51bc2e1ad5943b82032e)

##### `CliTest.test_count`　[L35–L38](https://github.com/acme/todo-app/pull/{{pr}}/changes#diff-4e8715c7a425ee52e74b7df4d34efd32e8c92f3e60bd51bc2e1ad5943b82032eR35)

```diff
+ [1] add 買牛奶、繳電費 → count → "共 2 筆，未完成 2 筆"
```

> **[1]** 兩筆都還沒完成時的輸出

</details>

<details>
<summary><b><code>todo/cli.py</code></b>　+10 −1　新增 count 指令</summary>

[在 Files changed 看整個檔案 ↗](https://github.com/acme/todo-app/pull/{{pr}}/changes#diff-66ca6b1b3ce0ce9622a7f5e5bb0ec10d15c0b582687a63e9d17bacf393d063fa)

##### `cmd_count`　[L43–L47](https://github.com/acme/todo-app/pull/{{pr}}/changes#diff-66ca6b1b3ce0ce9622a7f5e5bb0ec10d15c0b582687a63e9d17bacf393d063faR43)

```diff
+     todos = store.list()
+ [1] pending = 未完成的筆數
+     print(f"共 {len(todos)} 筆，未完成 {pending} 筆")
```

> **[1]** 未完成的筆數

##### `build_parser`、`COMMANDS`　[L68–L74](https://github.com/acme/todo-app/pull/{{pr}}/changes#diff-66ca6b1b3ce0ce9622a7f5e5bb0ec10d15c0b582687a63e9d17bacf393d063faR68)

```diff
+     sub.add_parser("count", help="統計待辦數量")
+ COMMANDS 加上 "count": cmd_count
```

</details>

## 為什麼這樣做

- 統計放在 `cmd_count`，不改 `TodoStore`（issue 的預設決定）。

## 📌 需求調整（相對於 #{{issue}}）

無

## ✅ 驗收結果（對照 #{{issue}} 的完成定義 ＋ 📌 需求調整）

驗的是 `{{head_sha_short}}`

| # | 條件 | 結果 | 證據 |
|---|---|---|---|
| 1 | 沒有任何待辦時印 `共 0 筆，未完成 0 筆` | ✅ | `python3 -m todo.cli --data /tmp/empty.json count` → `共 0 筆，未完成 0 筆` |
| 2 | 有待辦時印 `共 N 筆，未完成 M 筆`，M 是還沒完成的筆數 | ✅ | add 兩筆 → `count` → `共 2 筆，未完成 2 筆` |
| 3 | `test_count` 通過 | ✅ | `test_count ... ok` |
| 4 | 驗證閘門全綠 | ✅ | `Ran 25 tests ... OK` |
| 5 | 只改了 `todo/cli.py`、`tests/test_cli.py` | ✅ | 見上面的 diffstat |

## 📸 Before / After

非 UI 改動，證據見上表

## 👀 請你重點看

1. 無

## 📋 人工測試（merge 前）

> 預計 2 分鐘。「要你測的」與「AI 已經測過」除了標 ⚠️ 和「只有自動測試」的，每一步 AI 都已經在本機照做過一遍，結果跟預期一樣。

**環境：本機**

1. 在 worktree 裡執行（不用開 sandbox，這是 CLI）。
2. 測試資料：`python3 -m todo.cli --data .bombolt/todos.json add "買牛奶"`、`add "繳電費"`。

**要你測的**

- [ ] 1. `python3 -m todo.cli --data .bombolt/todos.json count` → `共 2 筆，未完成 2 筆`（看文案順不順）

<details>
<summary>AI 已經測過、你不用重測的（1 項）</summary>

- `python3 -m todo.cli --data /tmp/empty.json count` → `共 0 筆，未完成 0 筆`（DoD 1）

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
- 改動規模：2 個檔、+15 / -1 行

</details>

Closes #{{issue}}
