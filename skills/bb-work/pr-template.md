<!--
bombolt PR 模板。讀者是要決定能不能 merge 的人：重點、不廢話、證據優先。
沒有內容的段落寫「無」，不要刪掉。
「🔁 要修改的話」只照抄 `<!-- bombolt:handoff -->` 這一行，不要自己寫：`bb_pr.py record` 會把它換成接手的指令、
最近一次是誰在哪台電腦改的、每一輪的修改歷程。
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

## 📌 需求調整（相對於 #{{issue}}）

{{review 時由人決定、跟 issue 不一樣的地方（bb-fix 會寫）。issue 保持原樣：PR 砍掉重做時從原本的需求重來。
驗收以「issue 的完成定義 ＋ 這裡的調整」為準。bb-work 發 PR 時寫「無」（重做時沿用舊 PR 的調整除外）。同一件事再調整時改那一條，不要疊新的。}}

| # | 調整 | 影響的完成定義 | 誰決定、出處 |
|---|---|---|---|
| 1 | {{預設顯示下個月（原本：這個月）}} | {{DoD 2 改為：…／新增：…／拿掉 DoD 4}} | {{名字，YYYY-MM-DD，[review](留言連結)／在 session 裡說的／沿用被關掉的 #n}} |

## ✅ 驗收結果（對照 #{{issue}} 的完成定義 ＋ 📌 需求調整）

驗的是 `{{這一輪 verifier 驗收時的 commit 短 sha}}`

| # | 條件 | 結果 | 證據 |
|---|---|---|---|
| 1 | {{...}} | ✅ | {{指令與關鍵輸出 / 畫面上看到的內容}} |

## 📸 Before / After

{{UI 改動：每個畫面一組 before / after 圖；非 UI 改動寫「非 UI 改動，證據見上表」}}

## 👀 請你重點看

1. {{最需要人判斷的地方，以及為什麼}}

## 📋 人工測試（merge 前）

{{<artifacts>/test-guide.md 的內容（照 test-guide.md 寫、最後一輪 verifier 照做過一遍的）}}

## 🔜 Follow-up

- {{這次刻意不做、但之後可能要做的事；沒有寫「無」}}

{{有設定 integration_branches 時：🧪 已整合進 `<branch>`：`<merge commit 短 sha>`（解了衝突的話附一句說明）}}

## 🔁 要修改的話

<!-- bombolt:handoff -->

<details>
<summary>🤖 bombolt 回饋（給 pipeline 迭代用）</summary>

- 驗收迴圈跑了幾輪：{{n}}
- 有沒有用到「停工提問」：{{有／沒有}}
- 實作時需要自己判斷、issue 沒寫清楚的地方：{{...／無}}
- 卡住或浪費時間的地方：{{...／無}}
- 改動規模：{{檔案數}} 個檔、+{{新增}} / -{{刪除}} 行

</details>

Closes #{{issue}}
