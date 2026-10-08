# bombolt ⚡

一套 **coding agent pipeline**，是一個 Claude Code plugin。適用於任何 git ＋ GitHub 的專案。

第一次用：照順序做完 [安裝](#安裝每台電腦一次) 和 [在你的 repo 設定](#在你的-repo-設定每個-repo-一次)，再從 [使用](#使用) 的第 1 步開始。

```
你描述需求
   │
   ▼
/bombolt:bb-plan  ── 規劃 session ─────────────────────────────────────────────
   scope 判斷（太大就駁回並給拆法）→ 以 origin 最新版探索 codebase → 上網研究
   → 選項式訪談（把會影響寫法的決定全部問完）→ 冷讀 agent 找出「實作者得猜的地方」
   → 開出一份 agent 可以不問人就做完的 GitHub issue          （session 結束）
   （回報的是 bug：先排查原因，是程式碼的問題才開 fix issue，不是的話給排查報告）
   │
   ▼
/bombolt:bb-list  ── 隨時查 ──────────────────────────────────────────────────
   列出還開著的 issue 和狀態：可認領／誰在哪台電腦上做／PR 審查中／前一次的 PR 被關掉、可以重做
   │
   ▼
/bombolt:bb-work <n>  ── 實作 session（新開的） ────────────────────────────────
   確認沒有人在做 → 認領（issue 上的狀態留言：誰、哪台電腦）
   → 從 origin/<base_branch> 最新版開 worktree → 拍 before 截圖 → 實作
   → 驗收迴圈：獨立 verifier 啟動服務、逐條跑完成定義、照人工測試步驟實際操作一遍
     ＋ 獨立 reviewer 看 diff（最多 3 輪）
   → 發 PR 到 pr_base（逐段改動：高層次的 Files changed、驗收證據、before/after 截圖、
     merge 前的人工測試步驟），並在 PR 留下第 1 輪的修改紀錄（誰、哪台電腦、做了哪些決定）
   → 完成定義全過就整合進 stage 這類 integration_branches （worktree 與 session 保留）
   │
   ▼
你在 GitHub 上 review、照 PR 的「📋 人工測試」測 ── 沒問題 → merge
（任何一台電腦開 session 說「開 sandbox <n>」：本機或外網網址、測試帳號，帶著你一步一步測）
   │ 有問題                          │ 整個不行
   ▼                                 ▼
在 PR 留 review comment             關掉 PR（留一句為什麼）→ /bombolt:bb-work <n> 重做
→ 任何一台電腦、新的 session           （清掉前一次的 worktree／branch，從原本的 issue 重來）
  /bombolt:bb-fix <PR>（同事也可以）
   → 讀 PR 上的修改紀錄 → 修改 → 重新驗收 → 更新 PR、留下這一輪的紀錄、再整合一次（回到上一步）
   │
   ▼
/bombolt:bb-sweep  ── 每週一次 ──────────────────────────────────────────────
   只刪「PR 已 merge ＋ 乾淨 ＋ 沒 push 的都 push 了 ＋ 沒有 session 在用」的 worktree

（發版後會把 integration_branches（例如 stage）reset 回 base_branch 的話：人發完版、reset 完之後，
 在主 checkout 執行 /bombolt:bb-fix，把這波沒上版的 PR 一次追上最新 base_branch 並重新整合回去）
```

## 安裝（每台電腦一次）

### 步驟 1：準備工具

| 工具 | 用途 | 安裝 | 確認 |
|---|---|---|---|
| Claude Code CLI | 本體 | https://code.claude.com | `claude --version` |
| `git` | worktree | — | `git --version` |
| `gh` **≥ 2.99**，並已登入 | issue、PR、PR 附圖（`--attach`） | `brew install gh`，再 `gh auth login` | `gh --version`、`gh auth status` |
| `python3` ≥ 3.9 | bombolt 的腳本（只用標準函式庫） | macOS 內建 | `python3 --version` |
| `playwright-cli` | UI 驗收與截圖（專案沒有 UI 可以不裝） | `npm i -g @playwright/cli` | `playwright-cli --version` |
| `ngrok`，並設好 authtoken | 外網 sandbox（在外面用手機測 PR；不需要可以不裝） | `brew install ngrok`，到 https://dashboard.ngrok.com 註冊，再 `ngrok config add-authtoken <token>` | `ngrok version` |

公司和個人用不同的 GitHub 帳號的話，另外看 [兩個 GitHub 帳號](#公司個人兩個-github-帳號bb-gh)。

### 步驟 2：安裝 plugin

```bash
claude plugin marketplace add Hankkk423/bombolt_agent
claude plugin install bombolt@hankkk423
```

### 步驟 3：確認裝好了

重開 Claude Code，輸入 `/bombolt:`，應該看得到 `bb-setup`、`bb-plan`、`bb-work`、`bb-fix`、`bb-sandbox`、`bb-list`、`bb-sweep`。
也可以用 `claude plugin list` 確認 `bombolt@hankkk423` 在清單裡。

### 更新與移除

bombolt 沒有釘版號，每個新的 commit 就是一個新版本。但從 GitHub 加的 marketplace **預設不會自動更新**，二選一：

- **開自動更新（建議）**：在 Claude Code 裡輸入 `/plugin` → **Marketplaces** 分頁 → 選 `hankkk423` → **Enable auto-update**。
- **手動更新**：

  ```bash
  claude plugin marketplace update hankkk423
  claude plugin update bombolt@hankkk423
  ```

新版本在下一個 session 生效；已經開著的 session 可以執行 `/reload-plugins` 立刻套用。

移除：

```bash
claude plugin uninstall bombolt@hankkk423
claude plugin marketplace remove hankkk423
```

## 在你的 repo 設定（每個 repo 一次）

設定完之後 repo 裡會多哪些檔案、各自是什麼，見 [`example_repo_structure/`](example_repo_structure/)。

### 步驟 1：在 repo 的主 checkout 執行 `/bombolt:bb-setup`

```bash
cd ~/path/to/your-repo      # 主 checkout，不是 worktree
claude
> /bombolt:bb-setup
```

它會先自己調查 repo（branch、測試指令、CI、環境檔），再用選項問你幾輪問題：
- feature branch 從哪一支長出來、PR 開到哪一支
- 哪些指令全綠才算完成（驗證閘門）
- 要複製進 worktree 的 `.env`
- 驗收怎麼登入（不碰真實資料）、本機啟動的服務會不會真的對外發訊息
- 平行做多個 issue 時，port／資料庫怎麼隔開
- 這個 repo 額外的安全禁區
- merge 前你在哪裡做人工測試（stage 或本機）
- PR 不是開到 default branch 的話，要不要裝自動關 issue 的 GitHub workflow

問完之後，它會產生 `.claude/bombolt.md`，並實際跑一次啟動和登入的步驟確認寫得對。
`.claude/worktrees/` 會自動加進本機的 `.git/info/exclude`，不用改 `.gitignore`。

跳過這一步也沒關係：第一次跑 `bb-plan` 時如果還沒有這份設定，也會先帶你做完。

### 步驟 2：commit 設定檔

**`.claude/bombolt.md` 要 commit 進 repo**，同事才用得到。之後想調整，直接手改再 commit。

### 步驟 3（選用）：在 repo 裡指定要用 bombolt

在 repo 的 `.claude/settings.json` 加上這兩個 key（原本就有這個檔案的話，合併進去，不要整份覆蓋）。
加了之後，同事在這個 repo 開 Claude Code 時會自動加入 bombolt 的 marketplace，並把 bombolt 設成啟用。
還沒安裝的人，`/plugin` 的 **Errors** 分頁會提醒他，執行一次 `claude plugin install bombolt@hankkk423` 就好。

```json
{
  "extraKnownMarketplaces": {
    "hankkk423": { "source": { "source": "github", "repo": "Hankkk423/bombolt_agent" } }
  },
  "enabledPlugins": { "bombolt@hankkk423": true }
}
```

每個人都已經照上面的「安裝」自己裝好的話，就不需要這一步。

## 使用

### 1. 規劃：`/bombolt:bb-plan <需求描述>`

在專案 repo 的主 checkout 開一個 session 執行。它會一直問到沒有模糊的地方為止，最後開出 issue。

開工前一定先過兩道檢查（`scripts/bb_plan_check.py`）：
- **位置**：不在主 checkout（例如在 worktree 裡）就停下來，告訴你主 checkout 的路徑；判斷不出來就一定會問你。
- **同步**：先 fetch `origin/<base_branch>`。規劃一律以它為準（程式碼與 `.claude/bombolt.md` 都是），
  不管主 checkout 目前在哪個 branch。只有兩種情況會問你要先處理、還是照樣規劃（通常代表忘了 push）：
  - `base_branch` 有沒 push 的 commit
  - `.claude/bombolt.md` 還不在 origin 上

  其他（目前 branch 自己的 commit、沒 commit 的改動、用了哪一份設定）只告知你。fetch 失敗就不規劃。
issue 開好之後，這個 session 就可以結束了。**可以連續規劃多個需求**，每次各開一個 issue；
之後有空再依序開 session 實作（下一步）。

**排查 bug 也用它**：`/bombolt:bb-plan 客戶說訂單頁看不到上個月的訂單`。它會先補齊症狀、收集證據（code、最近的改動，
以及 `.claude/bombolt.md`「排查資源」寫的唯讀 log／錯誤追蹤），再判斷原因：
- 程式碼的 bug → 照一般流程開 `fix` issue（附重現步驟、根因、會先失敗的重現測試），交給 `bb-work` 修。
- 操作錯誤、第三方服務或網路、資料或設定錯了 → 不開 fix issue，給你排查報告（證據、建議處置、可以回給回報者的話）；
  程式可以做得更好的話（例如錯誤訊息講清楚），會問你要不要另外開一個改善的 issue。

排查只讀不寫：不改 code、不改資料、不碰正式環境。

想看目前有哪些 issue 可以認領、誰在做：`/bombolt:bb-list`（唯讀）。

### 2. 實作：開一個**新的** session

```bash
claude -n bb-<repo>-<n>-<slug> --permission-mode auto "/bombolt:bb-work <n>"
```

- `-n bb-<repo>-<n>-<slug>`：幫 session 取名字（例如 `bb-myapp-12-booking-month-default`），之後在 `/resume` 清單裡找得到；
  帶上 repo 名稱與 slug，同時跑多個 repo 的 issue 也不會撞名。issue 最下面會附上填好的完整指令。
- `--permission-mode auto`：讓它自主執行，由 Claude Code 的分類器擋高風險動作，外加 bombolt 的安全守門（見下方）。
  不加這個參數也能跑，只是每個動作都會停下來問你。

它會自己一路做到發 PR 為止，然後告訴你 PR 的網址。**省略 `<n>`** 會改成列出可認領的 issue、
請你重新指定編號（不會自己選一個開始做）。

開工前它會先看這個 issue 的狀態：別人正在做會問你要不要接手，已經有開著的 PR 就停下來（要修改請用 `/bombolt:bb-fix <PR>`）。
認領之後，issue 上會有**一則** bombolt 狀態留言，記錄誰在哪台電腦上做、做到哪（實作中 → 審查中 ⇄ 修改中 → 已 merge），
每次都是原地改寫，不會洗版。

**平行做多個 issue**：對每個 issue 各開一個終端機、各跑一次上面的指令（不同的 `<n>`）。
每個 worktree 完全隔離，不會互相干擾；`.claude/bombolt.md` 若有寫怎麼隔開 port／資料庫
（通常用 issue 編號錯開），照著做就不會撞。

### 親手測 PR：開 sandbox

不用自己打指令。在這個 PR 的 session 裡，或在任何一台電腦的主 checkout 開一個新的 session（這時要帶編號：「開 sandbox 12」；
那台電腦還沒有這個 PR 的 worktree 會先從 PR 建一個），然後用說的：

| 你說 | 它做 |
|---|---|
| 「開 sandbox」 | 在 PR 的 worktree 照 `.claude/bombolt.md` 啟動 app，給你 `http://localhost:<port>` 和測試帳號，列出 PR「📋 人工測試」要你測的步驟 |
| 「開外網 sandbox」 | 再用 ngrok 開一個臨時網址（每次一組新的帳密），在外面用手機也能測 |
| 「第 3 步看到 500」 | 當場看 app 的 log 找原因；是 code 的問題就請你跑 `/bombolt:bb-fix` |
| 「關掉 sandbox」 | 關掉 app 和 tunnel。session 結束也會自動關 |

- 也可以直接打 `/bombolt:bb-sandbox`（外網：`/bombolt:bb-sandbox remote`）。在主 checkout 的 session 帶 issue 編號：`/bombolt:bb-sandbox 12`。
- `bb-fix` 修完一輪也會問你要不要開 sandbox 測。
- **在外面**：電腦保持開機、連網、不睡眠，用手機的 Claude app 透過 Remote Control 連回那台電腦上的 session，再說「開外網 sandbox」。
- 外網一定帶帳密；直接執行 `ngrok`／`cloudflared` 會被安全守門擋下。ngrok 免費方案每個帳號只有一個固定網址，同時只能開一個外網 sandbox。
- dev server 擋 tunnel 的網址（Vite、Next.js）、前端直接打 `http://localhost:<別的 port>` 這類專案自己的事，寫在 `.claude/bombolt.md` 的「Sandbox（外網測試）」一節（`bb-setup` 會問）。

### 3. 修改：在 PR 留 review comment，然後在任何一台電腦

```bash
# 在 repo 的主 checkout（PR 最下面的「🔁 要修改的話」有填好的指令）
claude -n bb-<repo>-<n>-<slug> --permission-mode auto "/bombolt:bb-fix <PR 編號>"
```

不需要原本的 session，也不需要是原本那台電腦——同事也可以接手，改完再換回你：

- **PR 就是交接本**：每一輪結束時，bb-fix 會在 PR 留一則「🤖 bombolt 紀錄」（預設收合）：為什麼有這一輪、
  做了哪些決定、試過什麼不行、還有什麼沒做。誰、在哪台電腦、哪段 commit、驗收過沒由腳本自動寫
  （電腦名稱是 macOS「關於本機」裡的名稱，不用設定）。PR 內文最下面有每一輪一行的修改歷程。
- **接手**：它會在這台電腦建好（或更新）同一個 worktree，讀 issue、PR 內文、所有修改紀錄和還沒解決的 review，從上一次停下的地方接著改。
- **需求調整**：review 時決定跟 issue 不一樣的地方（例如「預設改成下個月」）寫在 PR 內文的「📌 需求調整」，**issue 不會被改**：
  PR 砍掉重做時從原本的需求重來，並問你要沿用哪幾條調整。目標整個變了的話，它會建議你開新的 issue。
- **以 code 為準**：有人沒用 bombolt、直接 push 的 commit，下一次接手時會補記、重新驗收；紀錄跟 code 對不上時相信 code。
- **有人正在改**：issue 的狀態會顯示「🟠 修改中」（誰、在哪台電腦）；另一個人再跑 bb-fix 會先問要不要繼續。
- 在同一台電腦想接回原本的對話也可以：`claude --resume <PR 裡寫的 session id>`，再跑 `/bombolt:bb-fix`。

### 重做：PR 整個不行，想從頭再來

1. 在 GitHub 上**關掉**那個 PR，最好留一句為什麼（重做時會讀）。
2. 照第 2 步開一個**新的** session，跑同一個 `/bombolt:bb-work <n>`。

它會發現前一次的 PR 被關掉了，先問你確認，然後：
- 讀被關掉的 PR 上的留言和 review，當成這次要避開的問題。
- 清掉前一次的 worktree、本機 branch、遠端 branch，用同一個名字從最新的 base 重新開始。
  舊的 commit 都還留在被關掉的 PR 裡。前一次的 session 還開著、有沒 commit 的東西、或遠端在 PR 關掉後又有人推過，
  它都不會刪，會停下來告訴你原因。
- 新 PR 的「為什麼這樣做」會寫前一次為什麼被關、這次哪裡不一樣；issue 的狀態留言會記一筆「重新實作」。

### 4. 清理：`/bombolt:bb-sweep`

先列出每個 worktree 會被刪除還是保留、以及理由，你確認之後才會真的刪。
也可以直接跑腳本：`python3 <bombolt>/scripts/bb_sweep.py <repo 路徑...>`（加 `--apply` 才會刪）。

會一起處理：本機 branch、對應的 issue（關閉，狀態留言改成「已 merge」）、**遠端 branch**（GitHub 沒辦法在 merge 時自動刪、又沒有 admin
權限開那個設定時很有用）。遠端只在全部條件成立時才刪：bombolt 建的 `bb-*` branch、不在受保護名單、
遠端 tip 等於 PR merge 時的 head、沒有別的開著的 PR 用到它，而且用 `--force-with-lease` 鎖住那個 sha。
不想動遠端就加 `--keep-remote`。⚠️ 建立那個 worktree 的 session 還開著的話，整個 worktree 都會保留。

另外也會列出**沒有 worktree、PR 已 merge 的本地 branch**（例如手動建的 branch 在 squash merge 後還留在本機）：
PR 是 MERGED、本地 tip 等於 PR merge 時的 head、不在受保護名單、沒被任何 worktree checkout 才刪，只刪本地、不動遠端與 issue。

### 發版後同步：在主 checkout 執行 `/bombolt:bb-fix`

有些 repo 上完版之後，會把 `integration_branches`（例如測試環境用的 `stage`）`reset --hard` 回最新的 `base_branch`。
這波沒上版的 PR 還開著，卻已經不在 `stage` 裡了。

**reset 做完之後**，在主 checkout 開一個 session 執行 `/bombolt:bb-fix`：把這些 PR（本機還有 worktree 的）
一次追上最新的 `base_branch`、解決衝突，並重新整合進每個 `integration_branches`。
已經對齊的會跳過，draft PR 不會被整合。這個模式只做同步，不處理 review。
- 用 `/bombolt:bb-fix <PR>` 修改某個 PR 時，開工前也會自己檢查一次：
  這個 PR 不在 integration branch 裡、或落後 `base_branch`（`pr_base` 不同時），就先同步再修改。

#### PR 開的支跟實際上版的支不同：`pr_base`

有些 repo 的節奏是：feature 從 `base_branch`（例如 `prod`）長出來，但 PR 開到單獨的
`pr_base`（例如 `release`），上版由人自己手動控制——挑幾個 PR 併進 `release`、
再把 `release` 併進 `prod`、`push`，讓部署流程接手；上完之後把 `release` 與
`integration_branches`（例如 `stage`）都 `reset --hard` 回最新的 `prod`。

`.claude/bombolt.md` 設定 `pr_base`（不設就跟 `base_branch` 一樣）即可啟用這個模式。
發版後一樣在主 checkout 執行 `/bombolt:bb-fix`；這個模式下它還會把最新的 `base_branch` 合進每個沒上版的 PR。
- **先合了一個 PR，另一個 PR 就出現 conflict**：`/bombolt:bb-fix <那個 PR>`，
  它會把最新的 `pr_base` 合進來、解衝突、跑閘門、push，並在 PR 留言列出「這個 PR 現在也包含了哪些 PR」
  （之後若決定某個 PR 這波不上，就知道哪些 PR 已經帶著它）。

⚠️ **issue 怎麼關**：bombolt 發 PR 時內文固定寫 `Closes #N`，GitHub 在 PR merge 時會自己關掉對應的 issue——
但這只在 PR 開到 repo 的 **default branch** 時生效。PR 開到別支（例如 `pr_base`）時，
bombolt 在 PR 標題帶 `(#N)`（在 PR 列表就對得到 issue），並由 `/bombolt:bb-sweep` 在查到 PR 已 merge 時明確關掉 issue。
想要「merge 就自動關」，`/bombolt:bb-setup` 會問要不要幫你裝一支 GitHub workflow
（[範本](skills/bb-setup/close-issues-workflow.yml)：PR merge 進那一支時，解析內文的 `Closes #N` 並關閉）。

## 命名

一個名字貫穿全程：

| 東西 | 名字 |
|---|---|
| worktree | `<repo>/.claude/worktrees/bb-<issue>-<slug>` |
| branch | `bb-<issue>-<slug>` |
| session | `bb-<issue>`（你用 `-n` 取的） |
| 截圖、PR 內文、進度檔 | `<worktree>/.bombolt/`（被本機的 `.git/info/exclude` 忽略，不會被 commit，刪 worktree 時一起消失） |

`.claude/worktrees/` 與 `.bombolt/` 會被自動加進本機的 `.git/info/exclude`，不需要改專案的 `.gitignore`。

**你在主 checkout（IDE 開的那個 repo 資料夾）切 branch、pull，不會影響 bombolt**：
plan 和 work 一律從最新的 `origin/<base_branch>` 開始，`.claude/bombolt.md` 也讀 origin 上的那份
（還沒 push 過才用本機的），用了哪一份會告訴你。建 worktree 時會把這份設定存成快照（放在那個 worktree 的 git dir），
之後那個 worktree 裡的腳本與安全守門只讀快照。代價：改了設定要先 push，已經建好的 worktree 再在裡面跑
`python3 <bombolt>/scripts/bb_worktree.py sync-config` 才會套用（新建的 worktree 自動是新的）。

## 公司／個人兩個 GitHub 帳號（`bb-gh`）

bombolt 所有的 GitHub 操作（開 issue、發 PR、留言、附圖）都經過 `bb-gh`。它的用法跟 `gh` 完全一樣，差別只在**會依資料夾選帳號**。

帳號對應寫在你自己的 git config，通常跟 `includeIf` 放在一起。例如公司專案都放在 `~/work/`：

```ini
# ~/.gitconfig
[github]
    user = my-personal-account             # 預設（個人）
[includeIf "gitdir/i:~/work/"]
    path = ~/.gitconfig-work

# ~/.gitconfig-work
[github]
    user = my-company-account              # 公司（建議填 GitHub 使用者名稱；填 email 也可以）
```

兩個帳號都要在 gh 裡登入過（`gh auth login` 各跑一次，protocol 選 SSH）。

- **沒設定 `github.user`**：`bb-gh` 等於 `gh`，只有一個帳號的人不用做任何事。
- **有設定**：只在那一次執行時帶入那個帳號的 token，**不切換 gh 的 active 帳號**，所以不影響你同時開著的其他 session。
- **有設定但 gh 沒登入那個帳號**：直接報錯停止，絕不退回 active 帳號。
- **用 email 設定時**：`bb-gh` 會查每個已登入帳號的 email 來對應。如果查不到（email 沒公開、而且 token 沒有 `user:email` 權限），錯誤訊息會教你補權限，或改填使用者名稱。
- **忘了用 `bb-gh` 也沒關係**：在設定了 `github.user` 的資料夾裡，Claude 直接呼叫 `gh` 會被守門擋下，並提示改用 `bb-gh`。

`bb-gh` 由 plugin 的 `bin/` 提供，在載入 bombolt 的 Claude Code session 裡可以直接呼叫。
你在自己的終端機打 `gh`，一樣照舊。

## 模型與 effort

所有 subagent 都跟主 session 用同一個模型（你自己選的）。effort 照工作性質固定：

| subagent | effort |
|---|---|
| `bb-reviewer`、`bb-cold-reader` | high |
| `bb-verifier` | medium |
| bb-plan、bb-work 派的 Explore 與上網研究 | 跟主 session 一樣（`/effort`） |

需要判斷力的工作（找 bug、找出 issue 裡得猜的地方）用 high；照表執行的驗收用 medium，省 usage 也比較快。

- 想讓 subagent 用跟主 session 不同的模型：設環境變數 `CLAUDE_CODE_SUBAGENT_MODEL=<模型>`（內建的 Explore 不受影響）。
- 用環境變數 `CLAUDE_CODE_EFFORT_LEVEL` 設的 effort 會蓋掉上面的設定；用 `/effort` 設的不會。

## 安全守門

實作 session 是完全自主的，所以真正危險的動作不靠 prompt 自律，而是由 PreToolUse hook（`scripts/bb_guard.py`）硬擋。
這個 hook **只在 bombolt 建的 worktree 裡生效**，你平常的 session 不受影響。

| 擋什麼 | 例子 |
|---|---|
| push 到自己以外的 branch、force push、刪遠端 branch | `git push origin HEAD:main`、`git push -f` |
| 不可逆的 GitHub 操作 | `gh pr merge`、`gh repo delete` |
| 正式環境 | `kubectl`、`helm`、`terraform apply`、`vercel --prod` |
| 對外發訊息 | Slack webhook、LINE、Resend / SendGrid / Mailgun / Twilio |
| `rm -r` 到 worktree 與暫存目錄以外的地方 | `rm -rf ~`、刪主 checkout 的檔 |
| 秘密檔（`.env*` 以外） | 私鑰、`~/.aws/credentials`，以及專案設定的 `secret_paths` |
| 直接開 tunnel（外網只能經過 `bb-sandbox`，一定帶帳密） | `ngrok http 3000`、`cloudflared tunnel --url …` |

每個專案可以在 `.claude/bombolt.md` 追加 `protected_branches`、`deny_commands`、`secret_paths`。

⚠️ 它比對的是指令文字，是一道安全網，不是沙箱：防得住 agent 順手做了不該做的事，防不住刻意的繞過。

## 一起改進 bombolt

- **每個 PR 最下面都有一節「🤖 bombolt 回饋」**，由 agent 誠實填寫：
  - 驗收跑了幾輪
  - 有沒有停工提問
  - 哪裡得自己猜
  - 卡在哪裡

  這是改進這套流程的主要資料來源。定期翻一翻，把反覆出現的問題改進對應的 skill。
- 改動的方式：對這個 repo 發 PR。merge 之後，開了自動更新的人會在背景拿到新版
  （plugin 沒有釘版號，版本就是 commit SHA）；沒開的人照 [更新與移除](#更新與移除) 手動更新。
- **開發 bombolt 本身時**，直接載入本機這份，改了立刻生效：`claude --plugin-dir ~/path/to/bombolt`
- 結構：

  ```
  .claude-plugin/          plugin 與 marketplace 的 manifest
  skills/bb-*/             七個 skill（SKILL.md ＋ 模板）：setup / plan / work / fix / sandbox / sweep / list
  agents/                  bb-cold-reader、bb-reviewer、bb-verifier
  hooks/hooks.json         安全守門
  bin/bb-gh                依資料夾選 GitHub 帳號的 gh wrapper
  scripts/                 固定步驟的腳本（worktree、快照、守門、清理、外網 tunnel、PR 修改紀錄），只用 python 標準函式庫
  tests/                   腳本的測試：python3 -m unittest discover -s tests
  example_repo_structure/  使用者的 repo 接上 bombolt 之後會有的檔案（範例）
  ```

- 原則：**固定的步驟寫成腳本（可以測），只把判斷留給 LLM**。改腳本一定要補測試。

## 已知限制

- `bb_sweep.py` 判斷「有沒有 session 在用」時讀的是 `~/.claude/sessions/*.json`，這不是 Claude Code 官方文件記載的介面。
  讀不到時這一項會失效，但另外的保留條件（未 commit、未 push、PR 未 merge、worktree 被 lock）仍然有效。
- `gh --attach` 上傳的圖片在私有 repo 裡誰看得到，以 GitHub 的權限為準。
- UI 驗收需要專案有「不碰真實資料就能登入」的方法（寫在 `.claude/bombolt.md`）。沒有的話，UI 類的完成條件會被標成「無法驗證」。
- 在 Claude Code 的 session 裡，腳本連 GitHub 有時會拿不到你的 SSH key（`Permission denied (publickey)`，自己在終端機跑卻正常）。
  這時腳本會印出一行 `!` 開頭的指令，照 agent 的指示貼到提示列執行，它會接著做下去。

## 授權

[MIT](LICENSE)
