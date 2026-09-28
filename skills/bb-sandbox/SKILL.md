---
name: bb-sandbox
description: 在某個 PR 的 worktree 把 app 跑起來給使用者親手測：本機（localhost），或外網（ngrok 臨時網址，有帳密保護，在外面用手機也能測）。給網址和測試帳號，帶著 PR 的「📋 人工測試」一步一步測，測到問題當場看 log 找原因。測完關掉，session 結束也會自動關。
when_to_use: 使用者說「開 sandbox」「開環境給我測」「我要測這個 PR」「開外網 sandbox」「給我手機能開的網址」「關掉 sandbox」時；bb-fix 做完一輪、使用者說要測時。
argument-hint: "[remote｜stop] [issue 或 PR 編號]"
---

# bb-sandbox：開環境給使用者親手測 PR

## 現況（自動偵測）

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_context.py" --session-id ${CLAUDE_SESSION_ID}`

使用者說的：$ARGUMENTS

---

## 原則

- sandbox ＝ verifier 驗收時用的**同一套啟動方式**（`.claude/bombolt.md` 的「啟動 app」：同一個 port、同一份測試資料），只是留著給使用者用。不另外發明啟動方式。
- **跟著這個 session 走**：app 和 tunnel 都用 Bash 的 `run_in_background` 啟動。session 結束就一起關，不會留下沒人管的服務或外網網址。
- **外網一定經過 `bb_sandbox.py tunnel`**（每次開都換一組隨機帳密）。直接執行 `ngrok`／`cloudflared` 會被安全守門擋下，不要繞過。
  只在使用者要的時候開外網，而且只開這個 PR 的 app。
- sandbox 開著的時候不改 code。
- 回報要短：使用者可能在手機上看。所有溝通用繁體中文。

## 步驟

### 0. 要做什麼

- `stop`、「關掉」 → 直接到第 5 步。
- `remote`、「外網」「手機」「在外面」 → 本機＋外網（第 1–4 步）。
- 其他 → 只開本機（跳過第 3 步）。

### 1. 找到 PR 的 worktree

- 「現況」顯示目前在 bombolt worktree → 就是它。
- 顯示「這個 session 是 worktree `…` 的實作 session」→ 用 **EnterWorktree** 帶那個 `path` 進去。
- 在主 checkout、使用者有給編號（issue 或 PR）→ 找到或建好這台電腦上的 worktree（PR 是在別台電腦做的、或被 bb-sweep 清掉了，
  會從 PR 的 branch 建一個；已經有就 fast-forward 到 origin，測的才是 PR 最新的樣子；不會改 PR）：
  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_worktree.py" pickup --pr <編號> --session-id ${CLAUDE_SESSION_ID}
  ```
  用 **EnterWorktree** 帶回傳的 `path` 進去。`status` 是 `created` 的話，先照設定檔的「開工準備」安裝依賴。
  `ahead`（這台有沒 push 的 commit）→ 告訴使用者 sandbox 會包含這些還沒進 PR 的改動，問要不要照樣開。失敗 → 把訊息給使用者看，結束。
- 沒給編號、又不在 worktree 裡 → 請使用者說要測哪一個（issue 或 PR 編號），結束。

跑 `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_worktree.py" info`，讀它的 `config_snapshot`（之後說的「設定檔」都指這份）
與 `<artifacts>/test-guide.md`（PR 的「📋 人工測試」）。

### 2. 啟動 app

- 這個 session 已經開著這個 worktree 的 app → 沿用。要開外網、而設定檔的「Sandbox（外網測試）」要求啟動時多加設定的話，重新啟動一次。
- 照設定檔的「開工準備」確認依賴裝好了（worktree 通常已經裝過；這個 PR 改了 lock 檔的話重裝）。
- 照「啟動 app」用 `run_in_background` 啟動（port 照設定檔）；要開外網的話，同時加上「Sandbox（外網測試）」一節要的設定。
  等 log 出現啟動成功的訊息，或用 `curl` 確認服務有回應。
- port 被佔：`lsof -nP -iTCP:<port> -sTCP:LISTEN` 看是誰。是這個 worktree 的 app 就沿用；不是的話告訴使用者，不要砍別人的程序。
- 照「登入與測試資料」準備測試帳號（seed 之類）。

### 3. 開外網（只有 remote）

1. 用 `run_in_background` 執行（`<port>` 是對外的那個：「Sandbox（外網測試）」有寫就照它，沒寫就是 app 的 port）：
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_sandbox.py" tunnel --port <port>
   ```
2. 在前景取得網址與帳密：
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_sandbox.py" url
   ```
   失敗時照訊息處理：
   - 沒安裝 ngrok、`ERR_NGROK_4018`（沒有 authtoken）→ 請使用者照訊息設定（每台電腦一次），外網先不開，本機照常給。
   - 已經有一個 ngrok 在跑（免費方案同時只能一個外網網址）→ 把訊息給使用者看，問要不要關掉它、改開這個（AskUserQuestion）。
     要的話 `pgrep -fl ngrok` 找到它、`kill <pid>`，再從 1 重來。
3. 確認通了：
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' <url>                          # 沒帶帳密：要是 401
   curl -s -o /dev/null -w '%{http_code}\n' -u '<user>:<password>' <url>   # 帶帳密：跟本機打同一個網址的結果一樣
   ```
   外網打不開、本機可以（常見：dev server 擋不認得的網址、前端直接打 `http://localhost:<別的 port>`）→ 看 app 的 log 找原因，照「Sandbox（外網測試）」處理。
   那一節沒寫、原因又在 repo 的設定 → 不要改 repo 的程式碼：告訴使用者原因，並建議補進 `.claude/bombolt.md` 的內容。

### 4. 給使用者網址，帶著測

回報：
- **網址**：本機 `http://localhost:<port>`；外網 `<url>`。外網的瀏覽器會先問帳密（`<user>`／`<password>`，只有這次開的有效），
  第一次開還會看到 ngrok 的提示頁，按 **Visit Site**。
- **登入 app**：照「登入與測試資料」拿到測試帳號與密碼，**直接印出來**（sandbox 只用測試資料；人在外面讀不到 `.env`）。
  只印測試帳號的密碼，`.env` 裡其他的值一律不印。
- **要你測的**：把 `<artifacts>/test-guide.md` 的「要你測的」逐條列出來，網址的主機換成這次的網址。
  沒有這個檔（例如還沒發 PR）→ 列出 issue 的 DoD 裡要人看的部分。
- 測完說「關掉 sandbox」；session 結束也會自動關。

使用者測的時候：
- 回報某一步不對 → 先看 app 的 log（背景工作的輸出），需要的話自己照那一步操作一次，找出原因，
  告訴使用者是 code、測試指南、環境還是操作的問題，附上證據。
- 是 code 的問題 → 請使用者執行 `/bombolt:bb-fix <看到的問題>`（在這個 session 說的也算修改意見；bb-fix 驗收前會先關掉 sandbox）。
- 使用者測完做了不用改 code、但會影響之後怎麼改的決定（例如「這個行為維持原樣」）→ 當下用 `bb_pr.py note` 記在 PR 上
  （見 [bb-fix 的 record.md](../bb-fix/record.md)），換一個 session 或換電腦接手的人才看得到。

### 5. 關掉

- 停掉這個 session 開的 tunnel 與 app 的背景工作（TaskStop）。找不到那個工作的話：tunnel 用 `url` 印出的 `pid` `kill`，app 用 `lsof` 找出佔著 port 的 pid。
- 確認關好了：`lsof -nP -iTCP:<port> -sTCP:LISTEN` 沒有東西；開過外網的話，`pgrep -fl ngrok` 裡沒有這個 worktree 的那一個。
- 告訴使用者關好了。
