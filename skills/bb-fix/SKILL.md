---
name: bb-fix
description: bombolt 流程的第三步，更新已經開好的 PR。三種用法：（接手）在任何一台電腦的新 session 帶 PR 或 issue 編號執行：照 PR 上的修改紀錄接著改，不需要原本的對話，同事也能接手；（A）在實作 session 或 PR 的 worktree 裡：讀取 PR 上尚未解決的 review comment（加上使用者在 session 裡的說明），修改、重新驗收到全部通過，然後 push、更新 PR、在 PR 留下這一輪的修改紀錄；開工前會先解掉 PR 的衝突，並把這個 PR 追上最新的 base_branch、重新整合進 integration_branches。（B）在主 checkout、不帶編號執行：發版之後（integration_branches 被 reset 回 base_branch），把本機所有還開著的 PR 一次追上最新的 base_branch、解衝突，並重新整合進 integration_branches；不處理 review。
when_to_use: 使用者 review 完 PR，說「bb-fix <n>」「接手 PR #n」「照 PR 的 review 修改」「PR 有 conflict 幫我解」時；或發完版、reset 完 integration branch 之後，說「bb-fix」「resync」「同步一下」「這波沒上的 PR 幫我追上最新的 base」「把還開著的 PR 重新整合回去」時。
argument-hint: "[PR 或 issue 編號] [可選：額外的修改說明]"
disable-model-invocation: true
---

# bb-fix：更新已經開好的 PR

## 現況（自動偵測）

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_context.py" --session-id ${CLAUDE_SESSION_ID}`

- 帶的參數：$ARGUMENTS
- 第一個參數：`$0`。是數字的話，它是 PR 或 issue 編號，其餘的是額外的修改說明；不是數字的話，全部都是修改說明。

---

## 原則

- **PR 就是交接本**：這一輪改了什麼、為什麼、做了哪些決定，都要留在 PR 上（寫法見 [record.md](record.md)）。
  下一個接手的可能是明天新開的 session、另一台電腦或同事，他們都看不到這次的對話。
- **issue 不改**：它是原本的需求，PR 砍掉重做時要從它重來。這個 PR 裡人決定的調整，寫在 PR 內文的「📌 需求調整」。
- **以 code 為準**：PR 上的紀錄和內文是前面的 session 寫的，可能漏寫、可能過時。跟 code 或實際執行的結果不一致時，
  相信 code 和你自己驗收的結果，並在這一輪的紀錄寫出哪裡不一致。最新的 commit 沒有驗收過，這一輪就一定要重新驗收。
- **有結論就記**：這個 session 裡做了不用改 code、但會影響之後怎麼改的決定（「這條 review 不改」「先不做 X」），
  **當下**就用 `bb_pr.py note` 記下來（見 [record.md](record.md)），不要等收尾，使用者隨時可能關掉 session。純問答不用記。

## 先決定是哪一種

| 參數與上面的「現況」 | 做什麼 |
|---|---|
| 帶了編號，目前在主 checkout，或在這個 PR 的 worktree（issue 編號對得上） | **接手**，然後做 **A** |
| 帶了編號，但目前在**別的** PR 的 worktree | 告訴使用者：一個 session 只處理一個 PR，請另外開一個 session 執行 `/bombolt:bb-fix <n>`。結束 |
| 沒帶編號，目前在 bombolt worktree | **A**（PR 編號用 worktree 的 issue 編號，腳本會找到它的 PR） |
| 沒帶編號，在主 checkout，而且「這個 session 是 worktree `…` 的實作 session」（resume 沒有自動回到 worktree） | 用 **EnterWorktree** 帶那個 `path` 進去，然後做 **A** |
| 沒帶編號，在主 checkout（一般的新 session） | **B. 發版後同步全部 PR** |

---

## 接手：準備這台電腦的 worktree

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_worktree.py" pickup --pr $0 --session-id ${CLAUDE_SESSION_ID}
```

這台電腦沒有這個 PR 的 worktree，就從 origin 上的 branch 建一個：位置、名字都跟原本那台一樣，`.env` 類的檔案從這台的主 checkout 複製。
已經有了，就 fast-forward 到 origin（別台電腦或別人 push 的）。照 `status` 處理：

- `created`／`updated`／`exists` → 用 **EnterWorktree** 帶 `path` 進去（已經在裡面就省略），做 A。
- `ahead` → 這台有沒 push 的 commit（之前在這台改到一半、沒進 PR）。把 `git log origin/<branch>..HEAD` 給使用者看，
  用 AskUserQuestion 問：「併進這一輪」／「先停下來」。
- 失敗（跟 origin 分岔、有未 commit 的檔案、PR 已經不是開著的…）→ 把訊息給使用者看，停下來。**不要自己 reset、stash 或 force push。**

---

## A. 修改這個 PR

### 1. 讀交接資料、確認位置

`<PR>` 是 PR 或 issue 編號：接手時用 `$0`，否則用 worktree 的 issue 編號。

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_pr.py" context --pr <PR> --session-id ${CLAUDE_SESSION_ID}
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_worktree.py" info
bb-gh pr view <PR 編號> --json body -q .body > <artifacts>/pr-body.md
```

`context` 收齊接手要知道的事，`messages` 是寫好給你看的重點。照順序處理：

1. **有沒有人正在改**：`lock` 不是空的、而且 `this_session` 是 `false` → 把誰、從哪天開始改給使用者看，
   用 AskUserQuestion 問：「先停下來」（建議）／「繼續，由我接手」。`session_alive_here` 是 `true`，代表那個 session 還開在這台電腦上，要特別講。
   要繼續的話，把狀態標成「修改中」（只是讓別人知道，不會擋任何人）：
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_issues.py" mark --issue <issue> --event fixing --pr <PR 編號> --session-id ${CLAUDE_SESSION_ID}
   ```
2. **完成標準**：重新讀一次 issue（`bb-gh issue view <issue> --json body,comments`），再讀 `<artifacts>/pr-body.md` 的「📌 需求調整」。
   驗收以「issue 的完成定義 ＋ 需求調整」為準；調整改掉的那幾條，以調整後的為準。
3. **前面的經過**：照順序讀 `records` 每一則的 `comment`：為什麼有那一輪、做了哪些決定、試過什麼不行、還有什麼沒做。
   前面的決定不要推翻，除非這一輪使用者或 review 明確要改。這台電腦上有 `<artifacts>/progress.md` 的話也讀。
4. **沒有紀錄的改動**：`unrecorded_commits` 不是空的（有人沒用 bombolt、直接 push）→ 讀那幾個 commit 的 diff，
   照 [record.md](record.md) 寫補記：改了什麼、推測的原因（標明是推論）、有沒有違反需求調整或前面的決定。
   然後 `bb_pr.py record --kind catchup`（指令見 record.md，不加 `--verified`）。
   `history_rewritten` 是 `true`（有人 force push、改寫了歷史）→ 告訴使用者，以現在的 branch 為準，補記要涵蓋整個 PR 的 diff。
5. **驗收過時**：`verified_head` 是 `false` → 這一輪一定要跑第 4 步重新驗收，就算沒有要改的東西。
6. **這台電腦剛接手**：接手的 `status` 是 `created` → 照設定快照（`info` 的 `config_snapshot`）的「開工準備」安裝依賴（worktree 是全新的）。
   `<artifacts>/test-guide.md` 不存在 → 從 `pr-body.md` 的「📋 人工測試（merge 前）」一節抄回來。
   `<artifacts>/walkthrough.md` 不存在 → 第 4 步重寫時，拿 `pr-body.md` 的「🔍 逐段改動」當參考，還對得上 code 的說明就沿用。
- **先確認這個 PR 需不需要同步**（使用者上過一波版、這個 PR 沒被帶走時會需要）：
  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_resync.py" check --path "$(git rev-parse --show-toplevel)"
  ```
  PR 是 draft（標題有 `🚧`）的話加 `--draft`：draft 本來就不整合進 integration branch。
  `needs_sync` 是 `true`（`reasons` 會說明：已經不在某個 integration branch 裡，或落後 `base_branch`）→
  先照下面「B」第 2 步處理這一個 worktree（draft 的話 `start`／`finish` 加 `--no-integrate`），跑完閘門再開始修改——
  否則修改是疊在舊的 base 上，驗收的也不是之後真正會上線的組合。
  告訴使用者你做了這一步（有解衝突就說明解了什麼）。
- **再確認 PR 跟它的目標 branch 有沒有衝突**（典型情境：使用者先把別的 PR 合進 `pr_base`，這個 PR 就出現 conflict）：
  ```bash
  bb-gh pr view --json number,baseRefName,mergeable,mergeStateStatus
  ```
  `mergeable` 是 `UNKNOWN` 就等幾秒再查（GitHub 在背景算）。是 `CONFLICTING` 的話，在這一步先解掉：
  1. `git fetch origin <baseRefName>`，然後 `git merge --no-ff origin/<baseRefName>`。
  2. 解衝突跟解任何整合衝突一樣：打開完整檔案，用 `git log origin/<baseRefName> -- <檔案>` 看對方改了什麼，
     **保留雙方的意圖**。文件裡的編號（例如決策編號）撞號時，**這個 PR 改用下一個沒被用過的號碼**，
     不要改動已經在 `baseRefName` 上的那一個。
  3. 跑一次驗證閘門（`.claude/bombolt.md`），`git push`。
  4. ⚠️ **這個 PR 從此包含了別的 PR 的改動**（合進來的那些）。在 PR 留言寫清楚：
     「🔀 已合入最新 `<baseRefName>` 解衝突（衝突檔案：…；怎麼解的：…）。⚠️ 這個 PR 現在也包含：#A、#B」——
     列出 `git log --oneline <合併前的 HEAD>..origin/<baseRefName>` 裡看得到的 PR 編號。
     理由：使用者之後若決定「這一波不上 #A」，要知道這個 PR 已經帶著它。
  5. 告訴使用者你做了這一步。之後照第 5 步重新整合進 integration branch 時，一樣會帶著這些改動。
  沒有 review 意見、只是來解衝突或同步的話，做完這一步就直接跳到第 4 步（重新驗收）與第 5 步。

### 2. 收集要改的東西

```bash
bb-gh pr view --json number,url,title,body,reviews,comments,headRefName
bb-gh api graphql -f query='
  query($owner:String!,$repo:String!,$n:Int!){ repository(owner:$owner,name:$repo){ pullRequest(number:$n){
    reviewThreads(first:100){ nodes{ id isResolved path line comments(first:20){ nodes{ author{login} body createdAt } } } } } } }' \
  -f owner=<owner> -f repo=<repo> -F n=<PR 編號>
```

要處理的是：**所有 `isResolved: false` 的 review thread**、review 的總評、最後一則修改紀錄之後新增的一般留言
（bombolt 自己的紀錄與狀態留言除外），加上使用者在這個 session 裡直接說的內容。

每一條先分清楚是哪一種：
- **怎麼做**（實作上的修改）→ 照常改。
- **做什麼**（會改變完成定義：DoD 要加、要改、要拿掉，或範圍變了）→ 這是**需求調整**，只有人能決定（使用者在 session 裡說的，或 review 裡寫的）：
  - 寫進 `<artifacts>/pr-body.md` 的「📌 需求調整」（格式見 [bb-work 的 pr-template.md](../bb-work/pr-template.md)），寫出誰決定的、哪天、出處。**不要改 issue。**
  - 同一件事前面調整過 → 改那一條，不要疊一條新的。
  - 會改掉 issue 的「🎯 目標」→ 那已經是另一個需求：用 AskUserQuestion 問「關掉這個 PR，用 `/bombolt:bb-plan` 開新的 issue」（建議）／「在這個 PR 做，記進需求調整」。
  - review 的要求跟 issue 或前面的需求調整衝突、又看不出誰說了算 → 問使用者。

把它們整理成一張清單給使用者看一眼（每一條：你理解的要求 → 你打算怎麼改；需求調整標出來）。
⚠️ 某一條的意思有兩種以上合理的解讀、而且會改出不同的東西 → **這裡問使用者**（AskUserQuestion），
不要猜。使用者就在旁邊（是他叫你來改的）。

### 3. 修改

- 只改 review 要求的東西，不順手重構。動手前一樣先讀完要改的檔案與呼叫端，寫法照 bb-work 原則的「最小改動、照 coding style」，看截圖、圖片照 bb-work 第 6 步。
- 某一條你認為不該照改（會破壞 DoD、違反專案規則）→ 不要改，在回覆裡說明理由，留給使用者裁決。這也是一個決定，記進這一輪的紀錄。
- 每處理一組就 commit。

### 4. 重新驗收

跟 bb-work 的第 7 步完全一樣（閘門 → 更新逐段改動與人工測試指南 → 並行派 `bombolt:bb-verifier` 與 `bombolt:bb-reviewer` → 確認 → 修，最多 3 輪）。
派 verifier 和 reviewer 時多給 `<artifacts>/pr-body.md` 的路徑：它的「📌 需求調整」會改寫 issue 的某幾條完成定義。
這個 session 開著 sandbox（`bombolt:bb-sandbox`）的話，派 verifier 之前先照它的第 5 步關掉：verifier 會用同一個 port 啟動 app。
reviewer 要看的是**整個 PR 的 diff**（`git diff origin/<base>...HEAD`），不只這一輪的改動。
逐段改動（`<artifacts>/walkthrough.md`，寫法見 [bb-work 的 walkthrough.md](../bb-work/walkthrough.md)）也一樣要涵蓋整個 PR 的 diff。
PR 已經存在，所以產生骨架時帶 `--pr-url <PR 網址>`，連結直接就是正確的。還沒有這個檔的舊 PR，這次補寫。
寫完一樣跑 `render` 到 `ok`。
人工測試指南（`<artifacts>/test-guide.md`，寫法見 [bb-work 的 test-guide.md](../bb-work/test-guide.md)）也一樣涵蓋整個 PR，
並照它的「bb-fix 更新時」標出這一輪要重測的步驟。還沒有這個檔的舊 PR，這次補寫。
bb-fix 不為了指南另外補測試：review 沒要求的測試照「寫不了測試」處理。
UI 有變的話，verifier 重拍 after 截圖（檔名加上輪次，例如 `after-r2-1-xxx.png`）。

### 5. 更新 PR

1. `git push`
2. 改 `<artifacts>/pr-body.md`（第 1 步從 GitHub 讀回來的最新版，別人可能改過）：
   「📌 需求調整」寫進這一輪人決定的調整（沒有就維持原樣）；
   「✅ 驗收結果」表格換成這一輪的結果，註明驗的是哪個 commit（`驗的是 <短 sha>`）；
   **不要動「🔁 要修改的話」那一段**（`<!-- bombolt:handoff:start -->` 到 `end`），它由第 5 點的腳本產生。
   舊 PR 內文裡的「🔄 第 N 輪修改」表格保留原樣、不再新增，每一輪的細節改記在修改紀錄。
   「📝 程式碼改動範圍」：重新跑一次
   `git diff --stat origin/<base_branch>...HEAD`，反映累積到現在的完整改動，不是只有這一輪；
   逐段改動換成這一輪 reviewer 對照過、`render` 轉出來的 `<artifacts>/walkthrough.rendered.md`
   （舊 PR 原本的「逐檔案說明」或舊版的逐段改動都由它取代）；
   「📋 人工測試」換成這一輪 verifier 照做過一遍的 `<artifacts>/test-guide.md`（舊 PR 沒有這一節，就照 [bb-work 的 pr-template.md](../bb-work/pr-template.md) 的位置加上）。
   用 `bb-gh pr edit <PR> --body-file <artifacts>/pr-body.md`（有新截圖就加 `--attach`）。
3. 對每一個處理過的 review thread 回覆一則「已修正於 `<commit 短 sha>`：<一句話說明>」或「未修改：<理由>」：
   `bb-gh api repos/<owner>/<repo>/pulls/<PR>/comments/<comment id>/replies -f body='...'`
   **不要**把 thread 標成 resolved——那由 reviewer（使用者）決定。
4. 驗收全部通過的話，照 bb-work 第 9 步**再整合一次**進每個 integration branch（衝突同樣自己解），
   並更新 PR 內文的「🧪 已整合進…」那一行。
5. **留下這一輪的修改紀錄**（一輪的最後一步，它會一起更新 PR 內文的「🔁 要修改的話」和修改歷程）：
   照 [record.md](record.md) 寫 `<artifacts>/record.md`，然後
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_pr.py" record --pr <PR 編號> --session-id ${CLAUDE_SESSION_ID} \
     --kind round --summary "<一句話>" --body-file <artifacts>/record.md [--verified]
   ```
   `--verified`：這一輪 verifier 在剛 push 的這個 commit 上全部通過（交給人測的 ⚠️ 不算沒過）。沒過就不加。
6. 狀態改回審查中：`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_issues.py" mark --issue <issue> --event fixed --pr <PR 編號>`
7. 更新 `<artifacts>/progress.md`。

### 6. 回報

告訴使用者：這一輪改了什麼、驗收結果、有哪幾條沒照改以及理由、記進了哪些需求調整。worktree 與 session 繼續保留。
之後要再改：在這個 session 繼續說，或在任何一台電腦執行 `/bombolt:bb-fix <PR 編號>`（會從 PR 上的紀錄接著改）。

然後用 AskUserQuestion 問要不要開 sandbox 親手測這一輪（選項：「本機」／「外網（手機也能開）」／「先不用」）。
要的話用 Skill 工具執行 `bombolt:bb-sandbox`（外網帶 `remote`）。

---

## B. 發版後同步全部 PR

**什麼時候用**：人手動發完一波版之後，會把 `integration_branches`（`pr_base` 跟 `base_branch` 不同時，`pr_base` 也一起）
reset 回最新的 `base_branch`。這波沒上版的 PR 還開著，但已經不在 integration branch 裡了；
`pr_base` 跟 `base_branch` 不同的話，它們的 feature branch 也還停在舊的 base。
這裡把它們一次追上、重新整合回去。

**只處理同步，不處理 review**：某個 PR 有 review 要改，請使用者另外開一個 session 執行 `/bombolt:bb-fix <那個 PR 的編號>`。

### 1. 列出候選

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_resync.py" list
```

回傳每一個「本機還有 worktree、而且有開向 `pr_base` 的 PR」的 feature branch，
以及 `needs_sync`（要不要同步）、`reasons`（為什麼）、`draft`。
- `needs_sync` 是 `false` 的跳過（已經對齊了）。
- 有 `error` 的記下來，最後一起回報。
- 沒有要同步的就告訴使用者，結束。

### 2. 逐一處理

對每一個要同步的候選：

1. 用 **EnterWorktree** 工具、帶 `path` 進入它（從 A 過來的話已經在裡面，省略）。
2. ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_resync.py" start --path <path>
   ```
   `draft` 是 `true` 的話加 `--no-integrate`（draft PR 還沒通過驗收，不整合）。
   - `merged`：已經 push、也已經重新整合進每個 `integration_branches`。
     看 `reintegrated` 裡每個 target 的結果；若某個 target 回報 `conflict`，
     代表整合到那個 branch 時也有衝突，照下面同樣的方式處理，只是改跑
     `bb_integrate.py finish --target <target>`（見 `bb-work` 第 9 步）。
   - `conflict`：到 worktree 裡解決——**跟解任何整合衝突一樣**：打開完整檔案，
     用 `git log origin/<base_branch> -- <檔案>` 看對方改了什麼，**保留雙方的意圖**合併起來
     （對方的改動也是要上線的，不是雜訊）。`git add` 之後（draft 一樣加 `--no-integrate`）：
     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_resync.py" finish --path <path>
     ```
     想放棄這次同步：`bb_resync.py abort --path <path>`。
   - `error`：照訊息處理；解不了就跳過，記下來最後一起回報。
3. **合併後的程式碼變了，重新跑一次 `.claude/bombolt.md` 的驗證閘門**（跟 bb-work 第 7 步一樣）。
   有問題就修、commit、`git push origin HEAD:<branch>`，然後對每個 `integration_branches`
   再跑一次 `bb_integrate.py start --target <target>`（修正也要進 integration branch；draft 不用）。
4. 在對應的 PR 留下同步紀錄：`<artifacts>/record.md` 寫同步了最新的 `<base_branch>`、有沒有解衝突（解了什麼）、
   閘門的結果、重新整合進哪幾支（draft 不整合）。然後（PR 編號是候選的 `pr.number`；只跑了閘門、沒有重新驗收，所以不加 `--verified`）：
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_pr.py" record --pr <PR 編號> --session-id ${CLAUDE_SESSION_ID} \
     --kind sync --summary "同步最新 <base_branch>" --body-file <artifacts>/record.md
   ```
5. 用 **ExitWorktree**（`action: keep`）離開，處理下一個候選（從 A 過來的話省略，留在 worktree 裡繼續 A）。

### 3. 收尾

跟使用者總結：處理了幾個、幾個乾淨合併、幾個解了衝突、跳過了哪幾個（已經對齊、draft、錯誤），
以及有沒有卡住需要人處理的（附 worktree 路徑）。

### 已知限制

- 只處理**本機還有 worktree** 的 PR。PR 還開著、但本機的 worktree 已經被 `bb-sweep` 清掉，
  或 PR 是在別台電腦做的，這裡看不到：在這台執行 `/bombolt:bb-fix <PR 編號>` 接手，它開工前會自己檢查要不要同步。
