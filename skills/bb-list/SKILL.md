---
name: bb-list
description: 列出這個 repo 還開著、標了 bombolt 的 issue，以及每一個現在的狀態：可認領、誰在哪台電腦上做、PR 審查中、前一次的 PR 被關掉可以重做。唯讀，不會動任何東西。
when_to_use: 使用者想看有哪些 issue 可以認領、或想確認某個 issue 有沒有人在做時；也用在一次 bb-plan 規劃了多個 issue、之後要分批開 session 實作的情況。
argument-hint: ""
---

# bb-list：有哪些 issue 可以認領

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_issues.py" list`

把上面的 JSON 整理成表格給使用者看：編號、標題、網址、狀態。`status` 是 `error` 的話，照 `message` 告訴使用者哪裡出錯。

狀態照 `verdict` 寫：
- `free` → 「🟢 可認領」
- `working` → 「🟡 實作中」＋ `owner_text`（誰、哪台電腦）；`mine: true` 寫「🟡 這台電腦在做」
- `blocked` → 「⛔ 卡在停工提問，需要先回答 issue 上的問題」
- `in_review` → 「🔵 PR #n 審查中」＋ `owner_text`
- `redo` → 「🔁 前一次的 PR #n 已關閉，可以重做」
- `merged` → 「✅ PR 已 merge，等 `/bombolt:bb-sweep` 關閉 issue」

`local_worktree` 有值的話，在狀態後面加上「（本機 worktree：`<名稱>`）」。

**只列出、不要自動開始做任何一個。** 要認領（或重做）某個 issue，請使用者另外開一個新 session 執行
`/bombolt:bb-work <n>`——每個 issue 一個獨立的 session／worktree，可以同時開好幾個平行做。
