---
name: bb-setup
description: 第一次在某個 repo 使用 bombolt 時，訪談使用者並產生這個 repo 的設定檔 .claude/bombolt.md（base branch、測試指令、怎麼啟動 app、怎麼登入、安全禁區）。bb-plan / bb-work 發現設定檔不存在時也會引導到這裡。
when_to_use: 使用者說「設定 bombolt」「bombolt setup」「幫這個 repo 接上 bombolt」，或其他 bb-* skill 回報缺少 .claude/bombolt.md 時。
argument-hint: "[可選：補充說明]"
---

# bb-setup：讓一個 repo 可以用 bombolt

## 現況（自動偵測）

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_context.py" --session-id ${CLAUDE_SESSION_ID}`

## 你的任務

產生 `<主 checkout>/.claude/bombolt.md`，格式照 [config-template.md](config-template.md)。
⭐ 這份設定決定了後面「實作 session 能不能自己驗收到真的完成」，所以**寧可多問一題，不要猜**。
所有溝通用繁體中文。

### 0. 先處理 gh（bombolt 所有的 GitHub 操作都靠它）

上面「現況」裡 gh 相關的行有 ❌ 或 ⚠️ 就先處理，等使用者做完再繼續：
- 沒裝或版本太舊 → 請使用者在提示列輸入 `! brew install gh` 或 `! brew upgrade gh`。
- 沒登入 → 請使用者在提示列輸入 `! gh auth login`。
- 其他（例如指定的帳號沒登入）→ 照那一行的說明請使用者處理。

### 1. 先自己調查，再問人（不要問你查得到的東西）

在主 checkout 裡調查，並把結果當成問題的「建議選項」：

- **remote branch**：`git branch -r`、`git symbolic-ref refs/remotes/origin/HEAD`、最近的 merge 流向（`git log --merges --oneline -20 origin/<各分支>`）→ 推測 PR 應該開到哪一支。
- **專案知識**：有沒有 `AGENTS.md` / `CLAUDE.md` / `CONTRIBUTING.md` / `docs/`，以及它們寫的測試、lint、build 指令。
- **閘門指令**：`Makefile`、`package.json` 的 scripts、`pyproject.toml`、CI 設定（`.github/workflows/`）裡實際跑的指令。
- **環境檔**：`git ls-files --others --ignored --exclude-standard` 裡看起來是設定檔的（`.env*` 等）→ 候選 `copy_files`。**只看檔名，不要讀內容。**
- **可能是秘密的非 `.env` 檔**（`*-log.txt`、`*.env`、`credentials*`、`secrets*`…）→ 候選 `secret_paths`。
- **正式環境相關指令**（deploy、推環境變數、改正式資料庫的 make target）→ 候選 `deny_commands`。
- **怎麼啟動服務**：有前端的話怎麼啟動、dev server 的 port、登入流程；純後端的話 API 的 port、怎麼確認服務起來了；
  有 stage 的話，CI/CD 把哪一支部署到哪個網址。
- **worktree 怎麼裝依賴**：worktree 是全新的 checkout，沒有 `node_modules`、`.venv` 之類。找出在 worktree 裡安裝依賴的指令。
  ⚠️ 不要借用主 checkout 的虛擬環境：editable install（`pip install -e`、workspace link）會讓 worktree 跑到主 checkout 的程式碼。
- **commit 慣例**：`git log --oneline -30 origin/<base>`，歸納 commit message 的格式（前綴、語言、長度）。
- **截圖慣例**：repo 裡有沒有既定的做法；沒有就採用 bombolt 的預設（before/after 同畫面、同視窗大小）。

### 2. 問使用者（用 AskUserQuestion，每輪最多 4 題，每題都給選項，建議的放第一個並標「（建議）」）

必問（除非調查結果已經毫無疑義）。題目超過 4 題就分輪問，**依下面的順序**（前面的答案常會改變後面的題目）：

1. **分支流程**（每個 repo 規則都不一樣，一定要問清楚）：
   - feature branch 從哪一支的最新版長出來？（→ `base_branch`）
   - PR 開到哪一支？跟上面同一支就不用另外記；不同的話（例如 feature 從 `prod` 長出來，
     但 PR 開到單獨的 `release` branch，上版由人手動決定何時把 `release` 併進 `base_branch`）→ `pr_base`。
     ⚠️ `pr_base` 若尚未推上 `origin`，開 PR 會失敗——提醒使用者先 `git push -u origin <pr_base>`。
   - PR 開好之後，要不要先整合進某一支（例如測試環境用的 stage）讓人測試？（→ `integration_branches`；
     bombolt 固定用 merge commit `--no-ff`、只在完成定義全過時整合、每次 bb-fix 後再整合一次、衝突由 agent 解）
   - 人上版之後會不會把 `integration_branches`（`pr_base` ≠ `base_branch` 時還有 `pr_base`）reset 回
     `base_branch`（例如 `git reset --hard origin/prod`）？會的話，發版後這波沒上的 PR 要重新整合回去
     （→ 寫進「發版後同步」一節；實作交給在主 checkout 執行的 `/bombolt:bb-fix`，不需要另外設定欄位）
   - 有哪些 branch 絕對不能 push？（→ `protected_branches`；`release`／`prod`／`main` 等已經內建）
2. **驗證閘門**：哪些指令必須全綠才算完成？（列出你找到的，讓使用者勾選 multiSelect）
   跑很久的（> 5 分鐘）問有沒有比較快的替代方式。會碰到真實資料或外部服務的指令要標出來、不建議放進閘門。
3. **複製哪些 gitignored 檔到 worktree**（multiSelect）。
4. **驗收怎麼登入**：實作 session 要自己啟動服務、開瀏覽器或呼叫 API 驗收，它要怎麼拿到一個可以登入（純後端：測試用的 token）、
   **又不會碰到真實客戶資料**的帳號？
   （例如：註冊一個新的測試帳號、seed script、專用的測試帳號）⚠️ 不要叫使用者把密碼寫進設定檔。
   另外問：本機啟動的服務會不會真的對外發訊息、寄信、扣款（`.env` 是 sandbox 還是正式的金鑰）？
   問使用者，不要自己讀 `.env`（→「啟動 app」一節）。
5. **並行時的衝突**：兩個 worktree 同時啟動 app 時，port / 資料庫 / 快取會不會打架？要怎麼隔開？
6. **額外禁區**：你找到的候選 `secret_paths` / `deny_commands` 要不要擋？還有沒有別的？
7. **人工測試在哪裡做**：PR 開好之後，使用者 merge 前自己在哪裡測？stage（有 `integration_branches` 時建議：
   網址、部署要多久、怎麼確認 stage 上已經是某個 commit、stage 的測試帳號）還是本機？有沒有每次都要人看的項目？（→「人工測試」一節）

第 3 步的「忽略清單」與第 4 步的「commit／settings.json」問題，併進最後一輪一起問。
模板裡其他欄位（專案知識、commit 慣例、截圖慣例）用第 1 步的調查結果填；調查不出來、又會影響實作的，才加進題目問。

### 3. 本機的準備（直接做，不用問）

- 把 `/.claude/worktrees/` 加進 `.git/info/exclude`（只影響本機，不改 repo 的 `.gitignore`）：
  `python3 -c "import sys; sys.path.insert(0,'${CLAUDE_PLUGIN_ROOT}/scripts'); import bb_lib, pathlib; print(bb_lib.ensure_local_exclude(pathlib.Path('<主 checkout>'), '/.claude/worktrees/'))"`
- 檢查 repo 裡有沒有**別的工具的忽略清單**也需要擋 `.claude/worktrees/`（例如 `.vercelignore`、`.dockerignore`、`.npmignore`）。
  worktree 在 repo 目錄裡面，這些工具打包時可能把每個 worktree（含複製過去的 `.env`）整份帶走。
  **判準：那個工具打包的根目錄有沒有包含 repo 根目錄。** 例如 build context 是 `frontend/` 的 `.dockerignore` 碰不到 `.claude/`，就不用問。
  ⚠️ **找到了就停下來問使用者要不要補**（那是 repo 的檔案，要 commit）。

### 4. 寫檔與收尾

1. 依 [config-template.md](config-template.md) 寫出 `.claude/bombolt.md`，把 `{{...}}` 全部換成真的內容。
   - 內文要**具體到可以照著做**：完整指令、在哪個目錄跑、大概跑多久、成功長什麼樣子。
   - 查不到、使用者也說不知道的，寫「未知」並說明後果，不要編。
   - ⚠️ frontmatter 的清單**一律用 `- 項目` 的寫法**（`[a, b]` 的寫法用逗號切，regex 裡的 `{1,3}` 會被切壞）；以 `#` 開頭的行會被當成註解。
2. **實際跑一次**「開工準備」「啟動 app」「登入」的步驟（在一個暫時的 worktree 或主 checkout，照使用者允許的隔離方式）。
   跑不了的（例如需要使用者的帳號）在那一節標「⚠️ 未實際驗證」——實作 session 看到就知道要特別小心。
3. **驗證 deny_commands**：列一組「該擋」與一組「不該擋」（日常開發一定會用到的指令）各 5 條以上，
   用 `re.search` 逐條確認。它比對的是整行指令文字，所以 `grep "make deploy" docs/` 這類也會被擋——regex 盡量寫精準，無法避免的誤擋告訴使用者。
4. 驗證 frontmatter 讀得到：
   `python3 -c "import sys; sys.path.insert(0,'${CLAUDE_PLUGIN_ROOT}/scripts'); import bb_lib, pathlib, json; print(json.dumps(bb_lib.load_config(pathlib.Path('<主 checkout>')), ensure_ascii=False, indent=2))"`
5. 告訴使用者：
   - 設定檔的位置與重點摘要，以及哪幾節「未實際驗證」
   - ⚠️ **它要 commit 進 repo** 同事才用得到。進 repo 的方式要符合剛問到的分支流程（例如 PR 一律開到 base 的 repo，設定檔也要走 PR）——問使用者要不要由你代為處理。
   - 如果還想讓同事 clone 之後自動被提示安裝 bombolt，可以在 repo 的 `.claude/settings.json` 加上
     `extraKnownMarketplaces` 與 `enabledPlugins`（見 bombolt 的 README）——**問過使用者才加**。
     先用 `git ls-remote <marketplace repo 的網址>` 確認那個 repo 存在；不存在就告訴使用者，同事會被提示安裝一個抓不到的 marketplace。
