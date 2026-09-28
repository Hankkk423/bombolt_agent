---
name: bb-work
description: bombolt 流程的第二步。認領一個由 bb-plan 開好的 GitHub issue，在新的 worktree 裡自主實作，用獨立的驗收者與 reviewer 反覆驗證直到完成定義全部通過，最後發 PR（附 before/after 截圖，並在 PR 留下第 1 輪的修改紀錄，任何一台電腦都能接手修改）。
when_to_use: 使用者說「bombolt work <issue>」「認領 issue #N 開始實作」「bb-work N」時。
argument-hint: "[issue 編號；留空列出可認領的清單]"
disable-model-invocation: true
---

# bb-work：認領 issue → 自主實作 → 自己驗收到完成 → 發 PR

## 現況（自動偵測）

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_context.py" --session-id ${CLAUDE_SESSION_ID}`

- 要認領的 issue：**#$0**
- 這個 session 的 id：`${CLAUDE_SESSION_ID}`

---

## 原則

- **完全自主**：使用者已經在規劃階段回答完所有問題，這裡不要再問人。
  例外只有：第 1 步發現別人在做、或要重做時，下面的「停工提問」後門，以及安全守門擋下來的動作。
- **完成 ＝ issue 的「🏁 完成定義」每一條都有證據地通過**（AI 不能做的標 ⚠️ 交給人測，見第 7 步），而且獨立 reviewer 沒有 blocking 問題。
  不是「code 寫完了」，也不是「應該沒問題」。
- **只做 issue 範圍內的事**。看到別的問題寫進 PR 的 Follow-up，不要順手改。
- **最小改動、照 coding style**：用最簡單、但能完整達成 issue 的做法。只改需要改的行，
  不重排、不改名、不動無關的註解或格式；新寫、改寫的程式碼照 [coding-style.md](coding-style.md)（issue 另有決定就照 issue）。
  issue 寫出了程式碼就照著寫，不要自己「改良」。不加 issue 沒要求的抽象、參數、設定或防呆。
- **在 worktree 裡，git 指令一條一條單獨執行**：Claude Code 的 worktree 隔離會拒絕它無法確認「git 只作用在 worktree 內」的指令，
  例如用 `&&` 把 `make` 和 `git` 串在一起。被拒絕時拆開重跑就好，不要想辦法繞過。
- 所有溝通、commit 以外的文字（PR、issue 留言）用繁體中文。

## 步驟

### 0. 沒給 issue 編號？

`$0` 是空的話：跑 `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_issues.py" list`，把結果整理成表格
（編號、標題、網址、狀態，狀態的寫法照 bb-list）給使用者看，請使用者重新執行 `/bombolt:bb-work <n>` 指定編號。
**這個 session 到此結束，不要自己選一個開始做**——挑哪個 issue 是使用者的決定。

### 1. 讀 issue、確認沒有人在做、認領

```bash
bb-gh issue view $0 --json number,title,body,state,url,labels,comments
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_issues.py" status --issue $0
```

- issue 不存在 → 停下來告訴使用者。
- 從 body 最後的 `<!-- bombolt:meta {...} -->` 取出 `base`、`base_sha`、`slug`、`ui`。
  沒有這一段 → 這不是 bb-plan 開的 issue，停下來告訴使用者。
- 讀完整個 issue 與所有留言（留言裡可能有使用者後來補充的決定，**以最新的為準**）。
  開頭是「🤖 **bombolt 狀態**」的那一則是認領狀態，不是需求。
- 照 `status` 回傳的 `verdict` 決定（`message` 是寫好給使用者看的說明）：

  | verdict | 意思 | 怎麼做 |
  |---|---|---|
  | `free` | 沒有人在做 | 繼續 |
  | `mine` | 這台電腦之前就在做（例如 session 重開） | 繼續，不用再認領；第 2 步會沿用原本的 worktree |
  | `working`／`blocked` | 別人正在做 | 把 `message` 給使用者看，問要不要接手（AskUserQuestion：「不要，停下來」（建議）／「接手」）。`remote_branch` 不是空的（對方已經 push 過）就不能接手，停下來。 |
  | `in_review` | 已經有開著的 PR | 把 `message` 給使用者看，停下來 |
  | `redo` | 前一次的 PR 被關掉、沒有 merge | 走下面的「重做」 |
  | `merged`／`closed` | 做完了 | 停下來告訴使用者 |

  這幾個停下來、問使用者的地方，是「不要問人」原則的例外：它們發生在動手之前，而且接手、重做都會影響別人或刪東西。
- 認領（在 issue 上建立或更新 bombolt 狀態留言，並把自己加成 assignee）：
  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_issues.py" mark --issue $0 --event claim \
    --branch bb-$0-<slug> --session-id ${CLAUDE_SESSION_ID}
  ```
  接手時 `--event takeover`；`mine` 不用跑。

#### 重做（`verdict` 是 `redo`）

使用者把前一次的 PR 關掉了，要從頭再做一次。

1. **先問使用者**（AskUserQuestion：「重做」／「先不要」），問題裡說清楚：
   - 被關掉的 PR 有哪幾個（`closed_prs`）。
   - 會刪掉前一次的 worktree、本機 branch、遠端 branch；舊的 commit 都還留在被關掉的 PR 裡。
2. **讀前一次為什麼被關掉**：對每個被關掉的 PR，跑 bb-fix 第 2 步的兩個指令（`pr view` 後面帶 PR 編號），讀它的留言、review 和 review thread，
   以及 `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_pr.py" context --pr <被關掉的 PR 編號>` 的修改紀錄（`records`：做過哪些決定、試過什麼不行）。
   把使用者不滿意的地方整理進 `<artifacts>/progress.md`（第 2 步建好 worktree 之後寫），這次實作要避開。
   PR 上沒有留任何說明的話，照 issue 重做，並在 PR 的「為什麼這樣做」註明「前一次的 PR 沒有留下被關掉的原因」。
   **需求調整要不要帶過來**：被關掉的 PR 內文有「📌 需求調整」、而且不是「無」→ 逐條列出來，用 AskUserQuestion（multiSelect）問這次重做要沿用哪幾條。
   沒勾的就回到 issue 原本的需求；勾了的，寫進這次 PR 的「📌 需求調整」（出處寫「沿用被關掉的 #<n>」），
   並跟 issue 的完成定義一起當成這次的完成標準（第 7 步派 verifier 和 reviewer 時一起給它們）。
3. **清掉前一次留下的東西**（在主 checkout 執行）：
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_worktree.py" redo-clean --issue $0 --slug <slug>
   ```
   腳本全部檢查通過才會刪，不通過會說明原因（例如前一次的 session 還開著、有沒 commit 的檔案、遠端又有人推過）。
   照原因告訴使用者、停下來，**不要自己手動刪**。
4. **認領**：`bb_issues.py mark --issue $0 --event redo --closed-prs <被關掉的 PR 編號，逗號分隔> --branch bb-$0-<slug> --session-id ${CLAUDE_SESSION_ID}`
5. 照常從第 2 步繼續。發 PR 時，在「為什麼這樣做」最前面加一段：前一次的 PR（連結）為什麼被關掉，這次哪裡不一樣。

### 2. 建立 worktree 並進入

在**主 checkout** 執行：

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_worktree.py" create --issue $0 --slug <slug> --session-id ${CLAUDE_SESSION_ID}
```

它會 fetch 最新的 `origin/<base>`、建立 `.claude/worktrees/bb-$0-<slug>`（branch 同名）、
複製設定檔指定的 `.env` 類檔案、寫入 metadata，並回傳 `path` 與 `artifacts`（截圖與進度檔的目錄）。
`status` 是 `exists` 代表之前已經建過（例如這是 resume），直接沿用。
`config_source` 說明這個 worktree 用了哪一份設定（通常是 `origin/<base>` 上的）、為什麼，用一句話告訴使用者。

然後用 **EnterWorktree** 工具、帶 `path` 參數進入那個 worktree。之後的所有工作都在 worktree 裡。

### 3. 檢查規劃之後 base 有沒有變

```bash
git diff --stat <meta.base_sha> origin/<base>
```

- 沒變、或變的檔案跟這個 issue 無關 → 繼續。
- 變動碰到 issue 提到的檔案 → 重新讀那些檔案，判斷 issue 的前提是否還成立：
  - 還成立 → 繼續，並在 PR 的「為什麼這樣做」註明。
  - 不成立（例如要改的函式被刪了、行為被別人改了）→ 走「停工提問」。

### 4. 開工準備

- 讀這個 worktree 的設定快照（`bb_worktree.py info` 回傳的 `config_snapshot`），照「開工準備」一節在 worktree 裡安裝依賴等。
  之後提到 `.claude/bombolt.md`（包括給 verifier 的路徑）都指這份快照，不讀主 checkout 那份——使用者可能正在那裡切 branch。
- 在 `<artifacts>/progress.md` 記錄進度（每完成一步就更新）。這是給 resume 用的：
  context 被壓縮或 session 重開之後，先讀這個檔就知道做到哪裡。它只在這台電腦上；
  發 PR 之後換 session、換電腦、換人接手，靠的是 PR 上的修改紀錄（第 10 步）。

### 5. 拍 before 截圖（只有 `ui: true` 才需要）

**在改任何 code 之前**，派 `bombolt:bb-verifier`：
「只執行 issue 的截圖計畫，拍 before 圖，存到 `<artifacts>/shots/`，不用驗 DoD。」
這時 worktree 還是乾淨的 base，拍到的就是改動前的樣子。

### 6. 實作

- 改一個檔案之前，先讀完整個檔案和它的呼叫端、被呼叫端，確定懂了現在的行為再動手；不要只看 issue 引用的那幾行。
- 照 issue 的「實作步驟」逐步做。遵守改動檔案所在目錄的每一層 `AGENTS.md` / `CLAUDE.md`
  （讀那個目錄的任何檔案時會自動載入；compact 之後要再讀一次）。
- 大範圍的搜尋交給 Explore subagent，保持自己的 context 乾淨。
- 這個 repo 有自動測試框架的話，新增或改變的行為要補上對應的測試（issue 沒寫也要補——它是「確保正確性」的一部分）。
- 每完成一個有意義的步驟就 commit。commit message 照這個 repo 既有的風格
  （先看 `git log --oneline -15 origin/<base>`），簡短、說清楚做了什麼。

### 7. 驗收迴圈（最多 3 輪）

每一輪：

1. **自己先跑閘門**（`.claude/bombolt.md` 的「驗證閘門」），有紅燈先修到綠。
2. **寫（或更新）逐段改動與人工測試指南**：照 [walkthrough.md](walkthrough.md) 用腳本產生骨架、寫 `<artifacts>/walkthrough.md`，
   再跑 `render` 到 `ok`（產生 `<artifacts>/walkthrough.rendered.md`）。它是高層次的 Files changed
   （逐檔、逐段的精簡 code ＋ 說明），會放進 PR，使用者靠它決定能不能 merge，所以這一輪 code 改了什麼，它就要跟著改。
   再照 [test-guide.md](test-guide.md) 寫 `<artifacts>/test-guide.md`（PR 的「📋 人工測試」），一樣跟著 code 改。
3. **並行派兩個 subagent**（它們各自有乾淨的 context，互相看不到對方）：
   - `bombolt:bb-verifier`：啟動服務、逐條執行 DoD，再照 `<artifacts>/test-guide.md` 在本機實際操作一遍（呼叫時給它這個路徑）；
     `ui: true` 的話同時拍 after 截圖到 `<artifacts>/shots/`。
   - `bombolt:bb-reviewer`：看 issue ＋ `git diff origin/<base>...HEAD`，找 blocking 問題，
     並逐段對照 `<artifacts>/walkthrough.md` 跟 code、檢查 `<artifacts>/test-guide.md` 有沒有漏測
     （呼叫時給它這兩個路徑，以及 [coding-style.md](coding-style.md) 的完整路徑）。
   - 重做時沿用了被關掉的 PR 的需求調整：兩個都要一起給（完成標準是 issue 的完成定義 ＋ 這些調整）。
4. **不要照單全收**：reviewer 的每一個 blocking 問題你都要自己確認是真的（打開 code、想出失敗情境）。
   確認是真的才修；判斷不是問題的，在 PR 的「請你重點看」說明你為什麼不改。
5. 有 DoD 沒過、照指南操作發現 code 的問題、或有確認過的 blocking 問題 → 修，然後進下一輪。
   如果只有逐段改動跟 code 對不上、code 本身沒問題：改說明就好，再派 reviewer 只對照說明一次，不用重跑 verifier。
   照指南操作只有指南本身的問題（預期寫錯、步驟寫得不清楚）、或有原因不是 code 的 ⚠️ 無法驗證：
   照 [test-guide.md](test-guide.md) 的「照做一遍之後」處理，不用整輪重跑。
   reviewer 只回報指南漏測、code 本身沒問題：補進指南，再派 verifier 只照新加的步驟做一次，不用整輪重跑。

**結束條件**：verifier 回報全部通過 **且** 沒有確認過的 blocking 問題 → 進第 8 步。
- verifier 要有實際啟動服務操作過；沒有的話要寫出不適用或啟動不了的理由（port 被佔、缺依賴這類環境問題先自己排除再重跑）。
- DoD 或指南的步驟標 ⚠️ 無法驗證、原因不是 code（例如操作會真的寄信、扣款，或設定檔沒寫怎麼登入）：
  不算沒過，改 code 也修不好。移進指南的「要你測的」並寫出原因，照常發 PR、照常整合。

**跑滿 3 輪還沒過**：不要無限迴圈。照樣發 PR，但用 `--draft`，標題前加 `🚧`，
在 PR 最上面寫清楚「哪幾條沒過、試過什麼、卡在哪裡」，然後告訴使用者。

### 8. 發 PR

1. `git push -u origin <branch>`
2. 照 [pr-template.md](pr-template.md) 寫 PR 內文到 `<artifacts>/pr-body.md`。
   - 標題：`<type>(<範圍>): <一句話> (#<issue>)`（跟 issue 標題同一個格式，最後加上 issue 編號），簡短清楚。
     編號放在標題是為了在 PR 列表一眼對得到 issue：`Closes #<issue>` 只有 PR 開到 repo 的
     **default branch** 時 GitHub 才會連結與自動關閉，`pr_base` 不是 default branch 時它只剩一個普通的提及。
   - 驗收結果要是**這一輪 verifier 實際跑出來的證據**，不是你的推測。
   - 「📝 程式碼改動範圍」：先貼 `git diff --stat origin/<base_branch>...HEAD` 的**實際輸出**（不要自己重寫數字），
     下面接 `<artifacts>/walkthrough.rendered.md`（最後一輪 reviewer 對照過的 `walkthrough.md` 用 `render` 轉出來的），
     連結裡的 `{{PR_URL}}` 保持原樣。
     這一節是讓使用者不用打開 Files changed 就看懂每一段 code 在做什麼。
   - 「📋 人工測試」：貼最後一輪 verifier 照做過一遍的 `<artifacts>/test-guide.md`。
   - 「✅ 驗收結果」：註明驗的是哪個 commit（最後一輪 verifier 驗收時的 `HEAD` 短 sha）。
   - 「📌 需求調整」：寫「無」；重做時沿用了被關掉的 PR 的調整，就列那幾條。
   - 「🔁 要修改的話」：只照抄 `<!-- bombolt:handoff -->` 這一行，第 10 步的腳本會換成接手的指令、誰在哪台電腦做的。
   - 「🤖 bombolt 回饋」一節誠實填寫——它是整個團隊改進這套流程的唯一資料來源。
3. 建立 PR（UI 改動附上截圖）。**base 讀 `.claude/bombolt.md` 的 `pr_base`；沒設定就用 `base_branch`**
   （兩者不同時，`git diff` 的範圍比對仍然用 `base_branch`，只有這裡的 PR target 用 `pr_base`）：
   ```bash
   bb-gh pr create --base <pr_base> --head <branch> --title "<標題>" --body-file <artifacts>/pr-body.md \
     --attach <artifacts>/shots/before-1-xxx.png --attach <artifacts>/shots/after-1-xxx.png ...
   ```
   建立之後把逐段改動的連結補上（它會從 GitHub 讀回內文、把 `{{PR_URL}}` 換成 PR 網址再寫回，圖片網址不受影響）：
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_walkthrough.py" link --pr <PR 網址>
   ```
   再把 issue 上的狀態改成「審查中」：
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_issues.py" mark --issue $0 --event pr --pr <PR 網址>
   ```
   失敗訊息提到 base branch 不存在的話，是 `pr_base` 還沒 push 到 `origin`——停下來告訴使用者，不要自己 push 一支不屬於這個 worktree 的 branch。
   在 PR 內文的「📸 Before / After」一節，用 `![before](<截圖的本機路徑>)` 引用每一張圖——
   `--attach` 會把內文裡對應的本機路徑換成上傳後的網址。

### 9. 整合進 integration branch（`.claude/bombolt.md` 的 `integration_branches` 有設定才做）

**只有完成定義全部通過（不是 🚧 draft PR；交給人測的 ⚠️ 不影響）才整合。** 對每一個 integration branch，在 feature worktree 裡：

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_integrate.py" start --target <branch>
```

- `merged`：完成（腳本已經 fetch 最新的 origin/<branch>、用 `--no-ff` merge、push）。記下 `merge_commit`。
- `conflict`：到回傳的 `path`（暫存 worktree）裡解衝突：
  1. 每個衝突檔都打開完整內容，搞清楚兩邊各自想做什麼（`git log origin/<branch> -- <檔案>` 看對方的改動）。
  2. **保留雙方的意圖**合併起來——對方的改動之後也會上線，不可以為了讓自己的過而丟掉別人的東西。
  3. `git add` 之後執行 `... bb_integrate.py finish --target <branch>`（在 feature worktree 裡執行）。
  4. 在 PR 留言說明：哪些檔案衝突、跟誰的改動衝突、怎麼解的。
- `error`：照訊息處理；解不了就在 PR 留言說明並告訴使用者（PR 本身不受影響）。

在 PR 內文的「🔁 要修改的話」之前記一行：`🧪 已整合進 <branch>：<merge commit 短 sha>`。

### 10. 收尾

- **留下第 1 輪的修改紀錄**（PR 內文都改完之後，最後才做）：照 [bb-fix 的 record.md](../bb-fix/record.md) 寫 `<artifacts>/record.md`：
  issue 沒寫、實作時自己做的決定與理由，試過但不行的做法，這個 PR 特有的環境的坑，還沒做的事（「為什麼這樣做」已經寫過的不用重複）。然後
  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_pr.py" record --pr <PR 編號> --session-id ${CLAUDE_SESSION_ID} \
    --kind round --summary "發 PR" --body-file <artifacts>/record.md --verified
  ```
  🚧 draft PR（驗收沒過）不加 `--verified`。它也會把 PR 內文的「🔁 要修改的話」換成接手的指令、誰在哪台電腦做的。
- issue 上的狀態留言在第 8 步已經改成「審查中」，不用另外留言。
- **worktree 與 session 都保留**（不要呼叫 ExitWorktree）。
- 告訴使用者：PR 網址、驗收結果摘要、要他特別看的地方、人工測試有幾步（預計幾分鐘），以及修改的方式：
  在 PR 留 review comment → 在任何一台電腦的 repo 主 checkout 開新的 session，執行 `/bombolt:bb-fix <PR 編號>`
  （會從 PR 上的紀錄接著改，同事也可以；在這台電腦也可以 `claude --resume ${CLAUDE_SESSION_ID}` 回到這個對話，再跑 `/bombolt:bb-fix`）。
  想親手測：在這個 session 說「開 sandbox」，或在任何一台電腦的新 session 說「開 sandbox <issue 編號>」
  （在外面說「開外網 sandbox」，會給一個手機也能開的網址）。

---

## 停工提問（後門，目標是永遠不用）

只有在 **issue 沒有答案、而且不同答案會寫出不同 code** 的時候才用。能從 codebase 或專案慣例推得出答案的，自己決定並在 PR 說明。

1. 在 issue 留言：
   ```
   🤖 bombolt 實作暫停：需要決定以下問題才能繼續

   1. <問題>
      - 選項 A：…（我的建議，因為…）
      - 選項 B：…
   ```
2. `bb-gh issue edit $0 --add-label "bombolt:blocked"`（label 不存在就先 `bb-gh label create "bombolt:blocked" --color D93F0B`），
   並把狀態改成停工：`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_issues.py" mark --issue $0 --event blocked`
3. 更新 `<artifacts>/progress.md`，然後停下來告訴使用者。
4. 使用者回答之後（在 issue 留言或直接在 session 裡回答），重新讀 issue 留言，移除 label，
   `bb_issues.py mark --issue $0 --event unblocked`，繼續。
