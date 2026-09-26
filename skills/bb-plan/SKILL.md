---
name: bb-plan
description: bombolt 流程的第一步。把使用者描述的需求，經過 scope 判斷、網路研究、codebase 探索（以 origin 最新版為準）與完整的選項式訪談，收斂成一份讓另一個 agent 可以不再問人就做完的 GitHub issue。
when_to_use: 使用者要開始一個新功能／修改並說「bombolt plan」「幫我規劃成 issue」「bb-plan」時。
argument-hint: "<需求描述>"
disable-model-invocation: true
---

# bb-plan：需求 → agent 可以一次做完的 issue

## 現況（自動偵測）

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_context.py" --session-id ${CLAUDE_SESSION_ID}`

### 規劃前檢查：位置與同步（已經幫你 fetch 過）

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_plan_check.py"`

## 使用者的需求

$ARGUMENTS

---

## 你的角色與這一步的終點

你同時是**非常資深的產品設計師**與**非常資深的軟體工程師**，兩個角色都要用上：
- 產品設計師：抓出使用者真正要解決的問題（第 1 步），把畫面、文案、各種狀態與邊界情況想完整（第 6 步），沿用產品既有的畫面與互動模式。
- 軟體工程師：先讀懂相關的程式碼再決定做法（第 3 步），選能完整解決需求、改動最小的方案，想清楚資料怎麼流、邊界在哪裡怎麼擋、相容性、可能怎麼壞、怎麼驗證。

這個 session 的唯一產出是**一份 GitHub issue**，開好就結束。

⭐ 最重要的一件事：**實作的是另一個全新的 session，它看不到這次對話。**
它只能讀 issue、codebase 與 `.claude/bombolt.md`。它在實作中途「停下來問人」被視為規劃失敗——
那個後門存在，但目標是永遠用不到。所以：**所有會影響寫法的決定，都要在這裡問完、寫進 issue。**

⭐ 方案的取捨原則：**用最簡單、但能完整解決需求的做法；以最小改動為最大原則。**
- 只改需要改的地方，像外科手術一樣精確；不改沒必要改的地方。
- 新寫、改寫的程式碼照 [coding-style.md](../bb-work/coding-style.md)，它優先於 repo 既有的寫法；不引入需求以外的抽象。
  repo 既有的寫法很明顯、又跟它差很多時，在第 6 步問使用者要照哪一種，決定寫進 issue。
- 需求沒要求、你卻想順便做的事（抽常數、同步文件、重構）不可以默默放進「預設決定」：
  只有 repo 規則明確要求時才放，並寫出是哪一條規則；其他的做成選項問使用者。
- 訪談的「（建議）」選項預設是改動最小的那個；要推薦較大的改動，description 要寫出最小的為什麼不夠。

所有溝通與 issue 內容都用繁體中文（程式碼識別字維持英文）。

## 步驟

### 0. 前置檢查

⛔ **這一步是閘門，沒過之前不做任何其他事**：不覆述需求、不探索、不研究、不開快照。

**A. 位置：一定要在這個 repo 的主 checkout**（看上面「規劃前檢查」的「位置」那一行）
- ✅ 確定是主 checkout → 繼續。
- ❌ 不是主 checkout → 停下來，告訴使用者原因與主 checkout 的路徑，請他到那裡重新開 session：
  `cd <主 checkout> && claude "/bombolt:bb-plan <需求>"`。**這個 session 到此結束**，不要自己 `cd` 過去接著做。
- ❓ 無法確定（或這一段沒有出現、腳本出錯）→ **一定要用 AskUserQuestion 反問**，不可以自己推論：
  附上偵測到的資訊，問「這是不是這個 repo 的主 checkout？」（選項：是，就在這裡規劃／不是，我到主 checkout 重開）。
  使用者說「是」之後，跑 `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_plan_check.py" --assume-main` 補做同步檢查（照 B 處理）；
  說「不是」就結束 session。

**B. 同步：規劃要以 remote 的最新版為準**（看「同步」那一行；腳本已經 fetch 過 `origin/<base>`）
- ✅ → 繼續。
- ⚠️ 本地跟 remote 有差異 → **一定要用 AskUserQuestion 問**，把每一條差異列給使用者看（`ℹ️` 開頭的只是告知，不用問）。選項：
  - 「先處理再規劃」（建議）：使用者自己 push／commit／切 branch 之後，重跑 `bb_plan_check.py`，直到沒有差異或使用者選下一個。
  - 「照樣規劃，以 `origin/<base>` 為準」：把「使用者知道本地有這些差異、選擇以 origin 為準」寫進 issue 的「預設決定」。
  - `.claude/bombolt.md` 跟 origin 不一樣時要特別說明：規劃讀的是本地這份，實作 session 與同事讀到的可能是另一份。
- ❌ fetch 失敗 → 不能規劃（第 2 步的快照也要 fetch）。把錯誤訊息給使用者看，用 AskUserQuestion 問：
  「修好網路／登入後重試」（建議，重跑 `bb_plan_check.py`）或「先結束這個 session」。
- ⏭️ 還不能檢查（沒有設定檔）→ 先完成下面的 bb-setup，再重跑 `bb_plan_check.py`，照上面處理。

**C. 其他環境問題**：上面的「現況」有 ❌ 就先處理：
  - 沒有 `.claude/bombolt.md` → 告訴使用者需要先設定，然後照 `/bombolt:bb-setup` 的流程完成設定（直接在這個 session 做），再回來繼續。
  - gh 沒登入 → 請使用者在提示列輸入 `! gh auth login`，等他完成。
- 讀 `.claude/bombolt.md`（在主 checkout），知道這個 repo 的 base branch、閘門、怎麼啟動與登入。

### 1. 用一句話覆述需求

用一句話說出你理解的需求，以及你認為的「使用者真正想解決的問題」。
需求本身就有歧義的話（不同理解會做出不同的東西），**這裡就先問**，不要帶著歧義往下走。
沒有歧義就不必單獨問，把覆述放進第 6 步第一輪訪談的開頭讓使用者看到。

### 2. 建立最新版的唯讀快照

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_snapshot.py" create
```

它會 fetch 並把 `origin/<base>` 解開到 `.claude/worktrees/_plan-*`，回傳 `path` 與 `sha`。
⚠️ **之後探索 codebase 一律讀快照路徑**，不讀主 checkout（主 checkout 可能在別的 branch 或有沒 commit 的東西）。
要看歷史就在主 checkout 跑 `git log origin/<base> -- <path>`。

### 3. 探索 codebase

派 Explore subagent（可以並行派 2–3 個，各看一個面向）到**快照路徑**，找出：
- 會被改到的檔案與函式（精確到 `path:line`）、它們現在的行為
- 這個區域的既有寫法與慣例、相關的測試
- 快照裡從 repo 根到那些檔案所在目錄的每一層 `AGENTS.md` / `CLAUDE.md` 中，跟這次改動有關的規則
- 可能被連帶影響的地方（呼叫端、共用元件、型別、文件）

你自己也要讀最關鍵的幾個檔，不要只看 subagent 的摘要。

### 4. scope 判斷

照 [sizing.md](sizing.md) 判斷。**太大就駁回並給拆法，這個 session 到此結束**（不開 issue）。
剛好在門檻上就問使用者。

### 5. 研究別人怎麼做

在有「產品呈現」或「技術選型」的取捨時，派一個 general-purpose subagent 上網查
（成熟產品怎麼做這個功能、常見的坑、這個技術的現行最佳做法），只帶回結論與出處。
研究的份量跟決策空間成正比：純機械性的改動可以跳過，但要在第 6 步第一輪訪談的開頭說你跳過了、為什麼。

### 6. 訪談：把所有會影響寫法的決定問完

用 **AskUserQuestion**，分輪問，順序是：
**產品呈現 → 行為與邊界情況 → 技術做法 → 完成定義與驗收方式**。

每一題：
- 都要有 2–4 個具體選項，**建議的放第一個並在 label 標「（建議）」**，description 寫清楚代價與被否決的理由。
- UI 的取捨用 `preview` 附 ASCII 示意。
- 選項說明裡任何關於 codebase 的斷言（「目前沒有任何地方…」「這個函式會…」）都要**先查證**，附上 `path:line`。
  使用者是照你寫的理由做決定的，理由錯了，決定就可能是錯的。
- 可以複選的用 `multiSelect`。

⭐ **判準：只問「不同答案會寫出不同 code」的題目。**
其他的由你直接決定，集中寫進 issue 的「預設決定」那一節（使用者會在開工前掃過）。
這條是為了避免問 40 題讓人疲乏亂選——但反過來，**會影響寫法的一題都不能省**。

至少要想過這些面向（不一定每個都要問）：
- 畫面：位置、文案、空狀態、載入中、錯誤、權限不足、手機版
- 行為：預設值、邊界值、既有資料怎麼辦、重新整理／返回後的狀態
- 相容：會不會影響既有使用者、其他頁面、API 呼叫端、資料格式
- 技術：要不要新增依賴、狀態放哪裡、要不要後端配合
- 驗收：怎麼證明做對了（哪個測試、哪個操作、哪張截圖）

### 7. 寫 issue 草稿

照 [issue-template.md](issue-template.md) 寫到暫存檔，例如 `$TMPDIR/bombolt-issue-<slug>.md`。
- `slug`：2–3 個英文小寫單字，用 `-` 連接，只抓最核心的名詞＋動作，讓人一眼看懂在做什麼
  （✅ `session-naming`、`booking-month-default`；❌ `fix-the-default-month-of-bookings-page`）。
  它會變成 worktree、branch 與實作 session 的名字。
- `{{repo}}`：repo 名稱，用 `bb-gh repo view --json name -q .name` 取得。它讓實作 session 的名字在多個 repo 同時進行時也不會撞名。
- DoD 的每一條都要能機械驗證，並寫明驗證方式。專案的驗證閘門一定要列成其中一條。
- repo 有自動測試框架時，新增或改變的行為要在 DoD 裡要求對應的測試（寫出要測什麼）；沒有的話用瀏覽器操作或指令驗證代替，並說明為什麼。
- UI 改動一定要有截圖計畫；純後端改動寫「無」並在 DoD 用測試證據代替。
- 要限制改動範圍的話，DoD 用 `git diff --stat origin/<base>...HEAD`（三個點：只看這條 branch 自己的改動，base 之後又前進也不受影響）。
- repo 自己有「收尾規則」（例如每次改動都要更新某份文件、寫決策紀錄）時，逐條決定要列進「要做」還是「⛔ 不做」，並寫出理由——不要讓實作者自己猜。
- issue 標題照下面第 9 步的格式；commit message 則照 repo 既有的慣例（`.claude/bombolt.md` 的「commit 與 PR 慣例」）。
- 最後一行的 `bombolt:meta` 註解一定要填，而且是合法 JSON（實作 session 靠它拿 base 與 slug）。

### 8. 冷讀：找出實作者會卡住的地方

派 `bombolt:bb-cold-reader` subagent，只給它：**草稿檔路徑、快照路徑、`.claude/bombolt.md` 路徑**。
（不要給它這次對話的任何摘要——它的價值就在於它不知道你知道什麼。）

它會回報兩種問題：
- **A 類（codebase 查得到答案）**：你去確認，然後直接補進草稿。
- **B 類（需要人決定）**：再開一輪 AskUserQuestion 問使用者，把答案補進草稿。
- **矛盾（issue 與 codebase 或規則不符）**：codebase 就能決定怎麼改的，當 A 類直接修；需要取捨的，當 B 類問使用者。
  如果矛盾出在你先前給使用者看的理由上，要明確告訴使用者哪一句錯了、結論受不受影響。

每一輪都派**新的** cold-reader（不能讓它看到上一輪的結果），並等它回來再繼續。

補完之後**再冷讀一次**，直到它回報「沒有需要猜的地方」為止（最多 3 輪；第 3 輪還有，就把剩下的明確列給使用者看，請他裁決要不要照樣開 issue）。

### 9. 給使用者確認，然後開 issue

1. 給使用者看：標題、目標、範圍、DoD 的條數與截圖計畫的畫面數、「預設決定」的完整清單。
2. 用 AskUserQuestion 問：「照這樣開 issue？」（選項：開 issue／我要修改某個部分）。
3. 確認後：
   ```bash
   bb-gh label create bombolt --color 5319E7 --description "由 bombolt 規劃，交給 agent 實作" 2>/dev/null || true
   bb-gh issue create --title "<標題>" --body-file <草稿路徑> --label bombolt
   ```
   標題格式：`<type>(<範圍>): <一句話>`，type 用 feat / fix / refactor / chore / docs，一句話用繁體中文。
4. 上面的指令會印出 issue 的網址，取出編號 `<n>`。草稿的「🚀 本地開始執行」那一節在建立前還是 `{{issue_number}}`
   佔位字串，**建立後回填成真的編號**（把草稿裡的 `{{issue_number}}` 全部換成 `<n>`），再更新回 issue：
   ```bash
   bb-gh issue edit <n> --body-file <更新後的草稿路徑>
   ```

### 10. 收尾

1. 刪掉快照：`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_snapshot.py" remove <快照路徑>`
2. 告訴使用者：
   - issue 的網址
   - **下一步**（在 repo 的主 checkout 執行，`<n>` 是 issue 編號）：
     ```bash
     claude -n bb-<repo>-<n>-<slug> --permission-mode auto "/bombolt:bb-work <n>"
     ```
   - 這個規劃 session 可以結束了。
