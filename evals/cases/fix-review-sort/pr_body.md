## 一句話

`GET /api/todos` 加上 `?sort=due`，依到期日由近到遠排序；不合法的 `sort` 回 400。

## 改了什麼

- `list_todos` 讀 `sort` 參數：`due` 時依到期日排序，其他值回 400 `invalid_query`（`todo/server.py`）
- 新增 `SortTest`：依到期日排序、不合法的值（`tests/test_server.py`）

## 📝 程式碼改動範圍

`git diff --stat origin/main...HEAD` 的輸出（照實貼，不要摘要）：

```
 tests/test_server.py | 14 ++++++++++++++
 todo/server.py       |  6 ++++++
 2 files changed, 20 insertions(+)
```

### 🔍 逐段改動

照 Files changed 的順序，一個檔案一個折疊區，點檔名展開；點段落標題的行號會跳到 Files changed 的那幾行。

<details>
<summary><b><code>tests/test_server.py</code></b>　+14 −0　新增 sort=due 的測試</summary>

[在 Files changed 看整個檔案 ↗](https://github.com/acme/todo-app/pull/{{pr}}/changes#diff-5620588ecb93aec064fe16ec7e5c10f6669041c927717e0f57b3f9a44ffec5ac)

##### `SortTest`　[L71–L83](https://github.com/acme/todo-app/pull/{{pr}}/changes#diff-5620588ecb93aec064fe16ec7e5c10f6669041c927717e0f57b3f9a44ffec5acR71)

```diff
+ [1] test_sort_by_due: 新增 due 10-20、10-15 → GET ?sort=due → ["b", "a"]
+ [2] test_invalid_sort: GET ?sort=title → 400、code invalid_query
```

> **[1]** 到期日近的排前面
> **[2]** 不合法的值照 CLAUDE.md 的錯誤格式

</details>

<details>
<summary><b><code>todo/server.py</code></b>　+6 −0　list_todos 支援 sort=due</summary>

[在 Files changed 看整個檔案 ↗](https://github.com/acme/todo-app/pull/{{pr}}/changes#diff-14044ad2542c847c995f8adc1041818895be46fa50e894a520727ac8a980026a)

##### `Handler.list_todos`　[L118–L123](https://github.com/acme/todo-app/pull/{{pr}}/changes#diff-14044ad2542c847c995f8adc1041818895be46fa50e894a520727ac8a980026aR118)

```diff
+     sort = query.get("sort", [None])[0]
+ [1] if sort not in (None, "due"): 400 invalid_query; return
+ [2] if sort == "due": todos = sorted(todos, key=due or "")
```

> **[1]** 不合法的值提早 return，不會再送 200
> **[2]** 依到期日字串排序（`YYYY-MM-DD` 字串順序等於日期順序）

</details>

## 為什麼這樣做

- 排序放在 `list_todos`，不動 `TodoStore`（issue 的預設決定）。
- 沒有到期日的待辦：issue 沒有規定排在哪裡，實作時用 `due or ""` 當排序鍵，結果會排在最前面。⚠️ 這是實作時自己做的判斷，請確認。

## 📌 需求調整（相對於 #{{issue}}）

無

## ✅ 驗收結果（對照 #{{issue}} 的完成定義 ＋ 📌 需求調整）

驗的是 `{{head_sha_short}}`

| # | 條件 | 結果 | 證據 |
|---|---|---|---|
| 1 | `?sort=due` 時 2026-10-15 那筆在前 | ✅ | `curl -s 'http://localhost:<port>/api/todos?sort=due'` → 順序 `回信`、`繳電費`、`買牛奶` |
| 2 | 沒帶 `sort` 照 id 排 | ✅ | `curl -s 'http://localhost:<port>/api/todos'` → id `1, 2, 3` |
| 3 | 不合法的 `sort` 回 400 | ✅ | `?sort=title` → `400`、`{"error": {"code": "invalid_query", "message": "sort 只能是 due"}}` |
| 4 | `SortTest` 通過 | ✅ | `test_sort_by_due ... ok`、`test_invalid_sort ... ok` |
| 5 | 驗證閘門全綠 | ✅ | `Ran 26 tests ... OK` |
| 6 | 只改了 `todo/server.py`、`tests/test_server.py` | ✅ | 見上面的 diffstat |

## 📸 Before / After

非 UI 改動，證據見上表

## 👀 請你重點看

1. 沒有到期日的待辦排在最前面（`todo/server.py:123`）。issue 沒有規定，這是實作時的判斷。

## 📋 人工測試（merge 前）

> 預計 3 分鐘。「要你測的」與「AI 已經測過」除了標 ⚠️ 和「只有自動測試」的，每一步 AI 都已經在本機照做過一遍，結果跟預期一樣。

**環境：本機**

1. 在 repo 主 checkout 開一個 Claude Code session，說「開 sandbox {{issue}}」，它會給你 `http://localhost:<port>`。
2. 測試資料：`python3 -m todo.cli --data .bombolt/todos.json add "買牛奶" --due 2026-10-20`、`add "繳電費" --due 2026-10-15`、`add "回信"`。

**要你測的**

- [ ] 1. `curl -s 'http://localhost:<port>/api/todos?sort=due'` → 沒有到期日的「回信」排在最前面，接著「繳電費」、「買牛奶」（沒有到期日的排法是實作時自己決定的，請確認）

<details>
<summary>AI 已經測過、你不用重測的（2 項）</summary>

- `curl -s 'http://localhost:<port>/api/todos'` → id `1, 2, 3`（DoD 2）
- `curl -s 'http://localhost:<port>/api/todos?sort=title'` → `400`、code `invalid_query`（DoD 3）

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
- 實作時需要自己判斷、issue 沒寫清楚的地方：沒有到期日的待辦要排在哪裡
- 卡住或浪費時間的地方：無
- 改動規模：2 個檔、+20 / -0 行

</details>

Closes #{{issue}}
