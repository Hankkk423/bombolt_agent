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
- `in_review` → 「🔵 PR #n 審查中」；`fixing: true` 的寫「🟠 PR #n 修改中」＋ `owner_text`（誰、在哪台電腦改）
- `redo` → 「🔁 前一次的 PR #n 已關閉，可以重做」
- `merged` → 「✅ PR 已 merge，等 `/bombolt:bb-sweep` 關閉 issue」

`local_worktree` 有值的話，在狀態後面加上「（本機 worktree：`<名稱>`）」。

**跨 repo 的成對 issue**（`pair` 不是 null）：在那一列**下面插一列**。
- 編號、標題欄留空，網址欄放 `pair.url`，狀態欄填 `🔗 <角色> <pair.repo>#<pair.number>（<對方狀態>）`。
- 角色指**對方**：`pair.relation` 是 `depends_on` → 「上游」；`blocks` → 「下游」；`parallel` → 「平行」；null → 「配對」。
- `pair.repo` 是 null（配對資料格式不對）時，狀態欄改寫「🔗 配對資料格式不對：`<pair.url>`」。

對方狀態照順序判斷，用第一個符合的：
1. `pair.verdict` 是 `unknown` → 「❓ 查不到：<pair.message>」
2. `pair.merged_pr` 有值：
   - `pair.in_base` 是 true → 「✅ 已進 `<pair.base>`」
   - `pair.in_base` 是 false → 「✅ PR #<merged_pr> 已 merge，還沒進 `<pair.base>`」
   - `pair.in_base` 是 null → 「✅ PR #<merged_pr> 已 merge」
3. `pair.verdict` 是 `closed` → 「issue 已關閉（沒有 merge 的 PR）」
4. `pair.verdict` 是 `mine` → 「🟡 這台電腦在做」
5. 其他 → 照上面的 `verdict` 對照表，用 `pair` 裡的 `open_prs`、`closed_prs`、`owner_text`、`fixing`

自己的 `waiting` 是 true、而且 `verdict` 是 `free` → 自己那一列的狀態改寫成：
- 「⏳ 可認領，但要等上游 `<pair.repo>#<pair.number>` 進 `<pair.base>`」（`pair.base` 是 null 時省略「進 `<pair.base>`」）。
- `pair.repo` 是 null 時，改寫成「⏳ 可認領，但配對資料格式不對，要先確認」。

範例：

| # | 標題 | 網址 | 狀態 |
|---|---|---|---|
| 41 | feat(ui): 匯出按鈕 | https://github.com/acme/web/issues/41 | ⏳ 可認領，但要等上游 `acme/api#12` 進 `main` |
| | | https://github.com/acme/api/issues/12 | 🔗 上游 acme/api#12（🔵 PR #30 審查中） |

**只列出、不要自動開始做任何一個。** 要認領（或重做）某個 issue，請使用者另外開一個新 session 執行
`/bombolt:bb-work <n>`——每個 issue 一個獨立的 session／worktree，可以同時開好幾個平行做。
要修改已經開好的 PR：在任何一台電腦開一個新 session 執行 `/bombolt:bb-fix <PR 或 issue 編號>`。
