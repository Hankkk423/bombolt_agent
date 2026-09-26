<!--
bombolt PR 模板。讀者是要決定能不能 merge 的人：重點、不廢話、證據優先。
沒有內容的段落寫「無」，不要刪掉。
-->

## 一句話

{{這個 PR 做了什麼，一句話}}

## 改了什麼

- {{改動 1（`檔案`）}}
- {{改動 2}}

## 📝 程式碼改動範圍

`git diff --stat origin/{{base_branch}}...HEAD` 的輸出（照實貼，不要摘要）：

```
{{diffstat 輸出}}
```

{{<artifacts>/walkthrough.rendered.md 的內容（照 walkthrough.md 寫、reviewer 對照過、render 轉出來的）。形狀如下：}}

### 🔍 逐段改動

照 Files changed 的順序，一個檔案一個折疊區，點檔名展開；點段落標題的行號會跳到 Files changed 的那幾行。

<details>
<summary><b><code>{{path}}</code></b>　+{{新增}} −{{刪除}}　{{一句話：這個檔案為什麼改}}</summary>

[在 Files changed 看整個檔案 ↗]({{PR_URL}}/changes#diff-…)

##### {{位置}}　[L{{起}}–{{迄}}]({{PR_URL}}/changes#diff-…R{{起}})

```diff
-     {{刪掉的 code}}
+ [1] {{精簡的 code}}
```

> **[1]** {{這一行在做什麼}}

</details>

## 為什麼這樣做

{{關鍵決策與理由，連回 issue；issue 沒寫、實作時自己做的判斷要特別標出來}}

## ✅ 驗收結果（對照 #{{issue}} 的完成定義）

| # | 條件 | 結果 | 證據 |
|---|---|---|---|
| 1 | {{...}} | ✅ | {{指令與關鍵輸出 / 畫面上看到的內容}} |

## 📸 Before / After

{{UI 改動：每個畫面一組 before / after 圖；非 UI 改動寫「非 UI 改動，證據見上表」}}

## 👀 請你重點看

1. {{最需要人判斷的地方，以及為什麼}}

## 🔜 Follow-up

- {{這次刻意不做、但之後可能要做的事；沒有寫「無」}}

{{有設定 integration_branches 時：🧪 已整合進 `<branch>`：`<merge commit 短 sha>`（解了衝突的話附一句說明）}}

## 🔁 要修改的話

實作 session 在 {{owner_text}}上，只有那台電腦能接著修改。

| | |
|---|---|
| worktree | `{{worktree 路徑}}`（那台電腦上） |
| branch | `{{branch}}` |
| session | `{{session 名稱，如 bb-myapp-12-booking-month-default}}` · id `{{session id}}` |

在這個 PR 留 review comment，然後由 {{owner_short}} 在那台電腦的任何目錄執行（會自動回到上面那個 worktree）：

```bash
claude --resume {{session id}}
# 回到 session 之後輸入：/bombolt:bb-fix
```

<details>
<summary>🤖 bombolt 回饋（給 pipeline 迭代用）</summary>

- 驗收迴圈跑了幾輪：{{n}}
- 有沒有用到「停工提問」：{{有／沒有}}
- 實作時需要自己判斷、issue 沒寫清楚的地方：{{...／無}}
- 卡住或浪費時間的地方：{{...／無}}
- 改動規模：{{檔案數}} 個檔、+{{新增}} / -{{刪除}} 行

</details>

Closes #{{issue}}
