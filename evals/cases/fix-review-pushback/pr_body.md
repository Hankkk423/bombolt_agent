## 一句話

新增 `PATCH /api/todos/<id>`，可以修改待辦的標題與到期日；不合法的輸入照固定的錯誤格式回 400／404。

## 改了什麼

- `TodoStore.update`：修改標題與到期日，驗證方式跟 `add` 一樣（`todo/store.py`）
- `Handler.do_PATCH`：`/api/todos/<id>`，只接受 `title`、`due`（`todo/server.py`）
- 新增 `PatchTest`（`tests/test_server.py`）

## 📝 程式碼改動範圍

`git diff --stat origin/main...HEAD` 的輸出（照實貼，不要摘要）：

```
 tests/test_server.py | 26 ++++++++++++++++++++++++++
 todo/server.py       | 25 +++++++++++++++++++++++++
 todo/store.py        | 14 ++++++++++++++
 3 files changed, 65 insertions(+)
```

### 🔍 逐段改動

照 Files changed 的順序，一個檔案一個折疊區，點檔名展開；點段落標題的行號會跳到 Files changed 的那幾行。

<details>
<summary><b><code>tests/test_server.py</code></b>　+26 −0　新增 PATCH 的測試</summary>

[在 Files changed 看整個檔案 ↗](https://github.com/acme/todo-app/pull/{{pr}}/changes#diff-5620588ecb93aec064fe16ec7e5c10f6669041c927717e0f57b3f9a44ffec5ac)

##### `PatchTest`　[L71–L96](https://github.com/acme/todo-app/pull/{{pr}}/changes#diff-5620588ecb93aec064fe16ec7e5c10f6669041c927717e0f57b3f9a44ffec5acR71)

```diff
+ [1] test_patch_title: PATCH {"title": " 買豆漿 "} → 200、標題去掉空白
+     test_patch_due: PATCH {"due": "2026-11-01"} → 到期日更新
+ [2] test_patch_invalid: 空標題、due 格式錯、帶 done → 400 invalid_todo
+     test_patch_unknown: id 9 → 404 not_found
```

> **[1]** DoD 1、2
> **[2]** DoD 3：`done` 不在可以改的欄位裡

</details>

<details>
<summary><b><code>todo/server.py</code></b>　+25 −0　新增 do_PATCH</summary>

[在 Files changed 看整個檔案 ↗](https://github.com/acme/todo-app/pull/{{pr}}/changes#diff-14044ad2542c847c995f8adc1041818895be46fa50e894a520727ac8a980026a)

##### `Handler.do_PATCH`　[L161–L184](https://github.com/acme/todo-app/pull/{{pr}}/changes#diff-14044ad2542c847c995f8adc1041818895be46fa50e894a520727ac8a980026aR161)

```diff
+     路徑不是 /api/todos/<id> → 404 not_found
+     body 不是 JSON 物件 → 400 invalid_json
+ [1] 帶了 title、due 以外的欄位 → 400 invalid_todo
+     store.update(id, title, due)；NotFound → 404、ValueError → 400 invalid_todo
+     → 200 {"todo": to_json(todo)}
```

> **[1]** issue 的決策：只開放 `title`、`due`

</details>

<details>
<summary><b><code>todo/store.py</code></b>　+14 −0　新增 TodoStore.update</summary>

[在 Files changed 看整個檔案 ↗](https://github.com/acme/todo-app/pull/{{pr}}/changes#diff-c7564f4bdfa07c8b5636c79fe9d89296b821f29291202a86bd6bae6f741ccdcf)

##### `TodoStore.update`　[L69–L81](https://github.com/acme/todo-app/pull/{{pr}}/changes#diff-c7564f4bdfa07c8b5636c79fe9d89296b821f29291202a86bd6bae6f741ccdcfR69)

```diff
+     找到那一筆：title 有帶就驗證、去空白後更新
+ [1] todo["due"] = parse_due(due)
+     存檔、回傳；找不到 → NotFound
```

> **[1]** 到期日用 `parse_due` 驗證，`null`／空字串會清除

</details>

## 為什麼這樣做

- 驗證與錯誤回應的寫法照 `add`／`do_POST`（issue 的「相關程式碼」）。

## 📌 需求調整（相對於 #{{issue}}）

無

## ✅ 驗收結果（對照 #{{issue}} 的完成定義 ＋ 📌 需求調整）

驗的是 `{{head_sha_short}}`

| # | 條件 | 結果 | 證據 |
|---|---|---|---|
| 1 | 改標題 | ✅ | `PATCH /api/todos/2 {"title":" 繳水費 "}` → `200`、`"title": "繳水費"` |
| 2 | 改到期日、清除到期日 | ✅ | `{"due":"2026-11-01"}` → `"due": "2026-11-01"`；`{"due":null}` → `"due": null` |
| 3 | 不合法的輸入回 400 | ✅ | `{"done":true}` → `400`、`{"error": {"code": "invalid_todo", "message": "只能修改 title、due"}}` |
| 4 | 404／invalid_json | ✅ | `/api/todos/99` → `404 not_found`；body `[]` → `400 invalid_json` |
| 5 | `PatchTest` 通過 | ✅ | `Ran 4 tests ... OK` |
| 6 | 驗證閘門全綠 | ✅ | `Ran 28 tests ... OK` |
| 7 | 只改了三個檔 | ✅ | 見上面的 diffstat |

## 📸 Before / After

非 UI 改動，證據見上表

## 👀 請你重點看

1. `TodoStore.update` 的參數用 `title=None, due=None`（`todo/store.py:69`）。

## 📋 人工測試（merge 前）

> 預計 3 分鐘。「要你測的」與「AI 已經測過」除了標 ⚠️ 和「只有自動測試」的，每一步 AI 都已經在本機照做過一遍，結果跟預期一樣。

**環境：本機**

1. 在 repo 主 checkout 開一個 Claude Code session，說「開 sandbox {{issue}}」，它會給你 `http://localhost:<port>`。
2. 測試資料：`python3 -m todo.cli --data .bombolt/todos.json add "買牛奶" --due 2026-10-20`、`add "繳電費"`。

**要你測的**

- [ ] 1. `curl -s -X PATCH http://localhost:<port>/api/todos/2 -H 'Content-Type: application/json' -d '{"title":"繳水費"}'` → `200`、標題變成「繳水費」

<details>
<summary>AI 已經測過、你不用重測的（3 項）</summary>

- `PATCH ... {"due":"2026-11-01"}` → 到期日更新（DoD 2）
- `PATCH ... {"done":true}` → `400 invalid_todo`（DoD 3）
- `PATCH /api/todos/99` → `404 not_found`（DoD 4）

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
- 改動規模：3 個檔、+65 / -0 行

</details>

Closes #{{issue}}
