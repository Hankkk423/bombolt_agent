---
name: bb-sweep
description: 清理本機所有用過 bombolt 的 repo 裡已經完成的 worktree（PR 已 merge、沒有未 commit/未 push 的東西、沒有 Claude session 在用）：刪本機 worktree 與 branch、關閉對應的 issue，並在嚴格條件下刪掉遠端 branch；另外也清掉沒有 worktree、PR 已 merge 的本地 branch，以及 bb-plan 中斷留下的規劃快照。先列出、確認後才刪。
when_to_use: 使用者說「清理 worktree」「bb-sweep」「把做完的 worktree 刪掉」「清掉已經 merge 的本地 branch」時。
argument-hint: "[可選：只處理這些 repo 的路徑，空白分隔；不給就掃本機所有用過 bombolt 的 repo]"
disable-model-invocation: true
---

# bb-sweep：安全地清理做完的 worktree 與已 merge 的本地 branch

## 步驟

1. **先列出（不刪）**：

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_sweep.py" $ARGUMENTS
   ```

   沒給路徑時，腳本自動找出本機所有用過 bombolt 的 repo（`~/.claude.json` 記的專案裡，有 `.claude/worktrees/` 的）；
   有給路徑就只處理那些。
   判斷全部由腳本做（規則見腳本開頭的說明），**不要自己另外判斷哪些可以刪**。
   任何一條「保留」的理由成立就保留；查不到狀態（gh 失敗、離線）也一律保留。

2. **把結果給使用者看**：輸出一個 repo 一段（`## <repo 路徑>`），每段再分「worktree」、「沒有 worktree、PR 已 merge 的本地 branch」，有的話再加「規劃快照」。
   - 有可刪項目（🗑️／🧹）的 repo：原樣列出哪些會刪、哪些保留以及理由。
   - 全部保留的 repo：各縮成一行（repo 名＋保留幾個），不列理由。有 ⚠️ 錯誤的 repo 也是一行，附上錯誤訊息。

3. **問使用者要清哪些 repo**（AskUserQuestion）。所有 repo 都沒有可刪的就直接結束，不用問。
   - 有可刪項目的 repo 不超過 3 個：multiSelect，一個 repo 一個選項，再加一個「先不要」。
   - 超過 3 個：「全部清理（N 個 repo）」／「先不要」，只清部分的話使用者會在 Other 寫 repo 名。
   - 每個 repo 的選項說明把 worktree、本地 branch、遠端 branch 三類分開列清楚。
   - 規劃快照**不放在這個問題裡**，照下面「規劃快照」一節另外問。

4. 確認後：

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_sweep.py" <使用者選的 repo 路徑…> --apply
   ```

   路徑照第 1 步輸出的 `## <repo 路徑>` 填，**一定要明確給路徑**：不給路徑會清到所有 repo。
   ⚠️ `--apply` 會重新判斷一次（不沿用剛才的結果），所以兩次之間狀態變了（例如有人又開了 session）也不會誤刪。

5. 回報實際刪了哪些，以及有沒有 issue 沒關成（原因照腳本回報的訊息講）。

## 規劃快照（`.claude/worktrees/_plan-*`）

bb-plan 開始時會把遠端最新的 base 解開成一份唯讀副本給規劃 session 讀，收尾時刪掉；
規劃中途被打斷（session 關掉、沒走到收尾）就會留下來。判斷由腳本做（規則見 `bb_sweep.py` 開頭）：
repo 主目錄或快照裡有活著的 Claude session、或 24 小時內才建立，就保留。

⛔ **`--apply` 不會刪快照，也不要把快照跟 worktree 一起問**。有 🗑️ 可刪的快照時：

1. 先用白話跟使用者說明它是什麼，例如：
   「`<repo>` 的 `_plan-<時間>` 是 `<日期>` 那次 /bb-plan 規劃用的唯讀副本（當時遠端 base 的檔案），
   規劃沒走到收尾所以沒刪掉。裡面沒有任何工作成果，刪掉不會損失東西，只是佔空間、
   搜尋 `.claude/worktrees/` 時可能搜到舊程式碼。」
   保留的快照也列出來，附上保留的理由。
2. 另外用 AskUserQuestion 問：「刪除這 N 個規劃快照」／「先不要」，選項說明逐一列出 repo 與快照名稱。
3. 使用者確認後才跑：

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_sweep.py" <有快照要刪的 repo 路徑…> --remove-snapshots
   ```

   腳本會重新判斷一次，期間有人開始規劃就不會刪。worktree 那邊沒加 `--apply` 就只是 dry-run，不會動。
4. 回報刪了哪些快照。

不要用 `rm -rf` 或 `bb_snapshot.py remove` 自己刪腳本判斷要保留的快照。

## 遠端 branch

worktree 可以刪的時候，腳本會**另外**判斷遠端 branch 刪不刪，每一個候選的理由最後一行就是結論
（「遠端 branch 也會刪」或「遠端不動：…」）。條件很嚴，**任何一條不確定就不刪**（完整條件見 `bb_sweep.py` 開頭）：
bombolt 建的 `bb-<issue>-<slug>`、不在任何受保護名單、遠端現在的 tip == PR merge 時的 head、
沒有其他開著的 PR 用到它，刪除時再用 `--force-with-lease` 鎖住那個 sha。

- 問使用者時要把「哪幾個遠端 branch 會一起刪」講清楚，跟 worktree 分開列。
- 使用者只想清本機、不想動遠端：加 `--keep-remote`。
- 刪掉的 sha 會印在結果裡；要救回：`git push origin <sha>:refs/heads/<branch>`，或 PR 頁面的「Restore branch」。
- ⛔ 不要自己另外跑 `git push --delete`——只透過腳本刪。

## 沒有 worktree 的本地 branch

例如手動建的 `chore/xxx`、`feat/xxx`，PR squash merge 之後本地還留著（`git branch --merged` 認不出來）。
腳本會對每一支**沒有被任何 worktree checkout、不在受保護名單**的本地 branch 查 GitHub PR，
**PR 是 MERGED、沒有還開著的 PR 用它、而且本地 tip == PR merge 時的 head** 才列為可刪。
沒有 PR、PR 還沒 merge 的 branch 不會出現在清單上。

- 只刪本地 branch（`git branch -D`），**不刪遠端、不關 issue**（這些 branch 沒有 bombolt 的 metadata，對不到 issue）。
- `--apply` 刪之前會再比對一次 tip，branch 被動過就不刪。
- 救回：`git branch <branch> <sha>`（sha 會印在結果裡）。

## 為什麼刪 worktree 的同時會關 issue

每個 PR 的內文本來就有 `Closes #<issue>`，GitHub 在偵測到 PR 合併時通常會自動關閉那個 issue——
但那個偵測依賴「GitHub 認得這是同一個 PR 的合併」，如果使用者是用 git 操作手動批次 merge
（例如先把多個 PR 併進一支中繼 branch，再整批上版），不一定會觸發。所以 `bb_sweep.py` 判斷
「這個 worktree 可以刪」的同一刻（＝它查到 PR 狀態是 MERGED），也會明確呼叫 `gh issue close`，
不依賴那個自動偵測。issue 已經是關的就直接跳過（gh 不會報錯）。

## 不要做的事

- 不要用 `git worktree remove --force` 或 `rm -rf` 手動刪腳本判斷要保留的 worktree。
  使用者真的要刪某個被保留的，請他自己執行，並先把保留的理由講清楚。
