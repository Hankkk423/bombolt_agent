---
name: bb-fix
description: bombolt 流程的第三步，更新已經開好的 PR。兩種用法：（A）在 resume 回來的實作 session 裡：讀取 PR 上尚未解決的 review comment（加上使用者在 session 裡的說明），修改、重新驗收到全部通過，然後 push 並更新 PR；開工前會先解掉 PR 的衝突，並把這個 PR 追上最新的 base_branch、重新整合進 integration_branches。（B）在主 checkout 執行：發版之後（integration_branches 被 reset 回 base_branch），把本機所有還開著的 PR 一次追上最新的 base_branch、解衝突，並重新整合進 integration_branches；不處理 review。
when_to_use: 使用者 review 完 PR、resume 回實作 session，說「bb-fix」「照 PR 的 review 修改」「PR 有 conflict 幫我解」時；或發完版、reset 完 integration branch 之後，說「bb-fix」「resync」「同步一下」「這波沒上的 PR 幫我追上最新的 base」「把還開著的 PR 重新整合回去」時。
argument-hint: "[可選：額外的修改說明]"
disable-model-invocation: true
---

# bb-fix：更新已經開好的 PR

## 現況（自動偵測）

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_context.py" --session-id ${CLAUDE_SESSION_ID}`

使用者在 session 裡額外說明的修改：$ARGUMENTS

---

## 先決定是哪一種

| 上面的「現況」顯示 | 做什麼 |
|---|---|
| 目前在 bombolt worktree | **A. 修改這個 PR** |
| 目前在主 checkout，而且「這個 session 是 worktree `…` 的實作 session」（resume 沒有自動回到 worktree） | 用 **EnterWorktree** 帶那個 `path` 進去，然後做 **A** |
| 目前在主 checkout（一般的新 session） | **B. 發版後同步全部 PR** |

---

## A. 修改這個 PR

### 1. 確認位置
- 讀 `<artifacts>/progress.md`（`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_worktree.py" info` 會印出 artifacts 路徑），
  回想這個 issue 做到哪裡。
- 重新讀一次 issue（`bb-gh issue view <issue> --json body,comments`）——它仍然是完成標準。
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

要處理的是：**所有 `isResolved: false` 的 review thread**、review 的總評、上次 push 之後新增的一般留言，
加上使用者在這個 session 裡直接說的內容。

把它們整理成一張清單給使用者看一眼（每一條：你理解的要求 → 你打算怎麼改）。
⚠️ 某一條的意思有兩種以上合理的解讀、而且會改出不同的東西 → **這裡問使用者**（AskUserQuestion），
不要猜。使用者就在旁邊（他剛 resume 這個 session）。

### 3. 修改

- 只改 review 要求的東西，不順手重構。
- 某一條你認為不該照改（會破壞 DoD、違反專案規則）→ 不要改，在回覆裡說明理由，留給使用者裁決。
- 每處理一組就 commit。

### 4. 重新驗收

跟 bb-work 的第 7 步完全一樣（閘門 → 更新逐段改動 → 並行派 `bombolt:bb-verifier` 與 `bombolt:bb-reviewer` → 確認 → 修，最多 3 輪）。
reviewer 要看的是**整個 PR 的 diff**（`git diff origin/<base>...HEAD`），不只這一輪的改動。
逐段改動（`<artifacts>/walkthrough.md`，寫法見 [bb-work 的 walkthrough.md](../bb-work/walkthrough.md)）也一樣要涵蓋整個 PR 的 diff。
PR 已經存在，所以產生骨架時帶 `--pr-url <PR 網址>`，連結直接就是正確的。還沒有這個檔的舊 PR，這次補寫。
寫完一樣跑 `render` 到 `ok`。
UI 有變的話，verifier 重拍 after 截圖（檔名加上輪次，例如 `after-r2-1-xxx.png`）。

### 5. 更新 PR

1. `git push`
2. 在 PR 內文的「🔁 要修改的話」**之前**插入（或追加）一節：
   ```
   ## 🔄 第 N 輪修改（YYYY-MM-DD）
   | review 意見 | 處理方式 | commit |
   ```
   並同步更新「✅ 驗收結果」表格為這一輪的結果，以及「📝 程式碼改動範圍」：重新跑一次
   `git diff --stat origin/<base_branch>...HEAD`，反映累積到現在的完整改動，不是只有這一輪；
   逐段改動換成這一輪 reviewer 對照過、`render` 轉出來的 `<artifacts>/walkthrough.rendered.md`
   （舊 PR 原本的「逐檔案說明」或舊版的逐段改動都由它取代）。
   用 `bb-gh pr edit <PR> --body-file <artifacts>/pr-body.md`（有新截圖就加 `--attach`）。
3. 對每一個處理過的 review thread 回覆一則「已修正於 `<commit 短 sha>`：<一句話說明>」或「未修改：<理由>」：
   `bb-gh api repos/<owner>/<repo>/pulls/<PR>/comments/<comment id>/replies -f body='...'`
   **不要**把 thread 標成 resolved——那由 reviewer（使用者）決定。
4. 驗收全部通過的話，照 bb-work 第 9 步**再整合一次**進每個 integration branch（衝突同樣自己解），
   並更新 PR 內文的「🧪 已整合進…」那一行。
5. 更新 `<artifacts>/progress.md`。

### 6. 回報

告訴使用者：這一輪改了什麼、驗收結果、有哪幾條沒照改以及理由。worktree 與 session 繼續保留。

---

## B. 發版後同步全部 PR

**什麼時候用**：人手動發完一波版之後，會把 `integration_branches`（`pr_base` 跟 `base_branch` 不同時，`pr_base` 也一起）
reset 回最新的 `base_branch`。這波沒上版的 PR 還開著，但已經不在 integration branch 裡了；
`pr_base` 跟 `base_branch` 不同的話，它們的 feature branch 也還停在舊的 base。
這裡把它們一次追上、重新整合回去。

**只處理同步，不處理 review**：某個 PR 有 review 要改，請使用者 resume 那個 PR 的 session 再跑一次 bb-fix（A）。

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
4. 用 `bb-gh` 在對應的 PR 留言：「🔄 已同步最新 `<base_branch>`（有解衝突的話說明解了什麼），
   並重新整合進 `<每個 integration_branches>`。」（draft 不寫「重新整合」）
5. 用 **ExitWorktree**（`action: keep`）離開，處理下一個候選（從 A 過來的話省略，留在 worktree 裡繼續 A）。

### 3. 收尾

跟使用者總結：處理了幾個、幾個乾淨合併、幾個解了衝突、跳過了哪幾個（已經對齊、draft、錯誤），
以及有沒有卡住需要人處理的（附 worktree 路徑）。

### 已知限制

- 只處理**本機還有 worktree** 的 PR。PR 還開著、但本機的 worktree 已經被 `bb-sweep` 清掉，
  或 PR 是在別人的電腦上開的，這裡看不到——請那台電腦的人跑，或請使用者說明要怎麼處理。
