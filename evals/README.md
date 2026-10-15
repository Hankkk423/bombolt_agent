# bombolt eval

改完 skill、agent、hook 或腳本之後，用固定的題目**實際跑一次** bb-work／bb-fix，打分、收集過程指標、跟 baseline 比。
目的是回答「這次的改動讓 pipeline 變好還是變差」，而不是憑感覺。

平常不用直接打指令：在這個 repo 開 Claude Code，說「跑 eval」或 `/bb-eval`（[skill](../.claude/skills/bb-eval/SKILL.md)），
它會先跑免費的檢查、再跑 eval、讀報告、找出每個失敗的原因、連回你這次改的地方，最後問你要不要設成新的 baseline。

## 它怎麼跑

```
每個 run（在 $TMPDIR 底下，跑完就刪；沒全過的會留著給你查）
├── origin.git        本機 bare repo，當成 GitHub 上的 origin
├── repo/             主 checkout（agent 的 cwd），內容是 fixture/ 的玩具專案 todo-app
├── bin/gh            假的 gh（fake_gh.py）：issue、PR、留言、review 都存在 gh/state.json
└── gitconfig         取代你自己的 git global config
```

1. 建好工作區，把題目的 issue 種進假 GitHub（fix 題還會用 bombolt 自己的腳本模擬「另一台電腦已經發了 PR、reviewer 留了意見」）。
2. `claude -p "/bombolt:bb-work <n>" --plugin-dir <這個 repo> --permission-mode auto`：跟你平常跑的一樣，
   載入的是這個 repo **目前工作目錄**的 bombolt（沒 commit 的改動也算）。不讀你自己的 settings（另外裝的 bombolt、hook、預設模型），每次條件一樣。
3. 跑完用確定性的 grader 打分（不靠 LLM 評分），並從 transcript 收集成本、時間、turns、subagent 次數、守門擋下幾次、被 Claude Code 的 worktree 隔離擋下幾次（每次都白花一個呼叫）。

完全不碰真的 GitHub 與你的 repo。agent 能碰到的外部只有本機：它跑在 auto mode ＋ bombolt 的安全守門下，跟你平常用一樣。

## 指令

| 指令 | 做什麼 | 花錢 |
|---|---|---|
| `python3 evals/run.py selftest` | 每一題：參考解要過 grader、沒改的版本與 `naive.diff` 要被隱藏測試抓到；反向題：什麼都沒做、硬做發 PR 要被抓到，照停工提問做要全過 | 免費，約 40 秒 |
| `python3 evals/run.py run [--case 'fix-*'] [--runs 2] [--jobs 3] [--label "…"]` | 實際跑，最後印出報告 | 全部約 US$25–40、30–45 分鐘（8 題 × 2 次；實測每個 run 約 US$2、4–7 分鐘，停工題較便宜） |
| `python3 evals/run.py report evals/results/<時間>` | 重印報告（跟目前的 baseline 比） | 免費 |
| `python3 evals/run.py timeline evals/results/<時間>/runs/<題目>-<n> [--main-only]` | 把一個 run 的 transcript 壓成一行一個動作 | 免費 |
| `python3 evals/run.py accept evals/results/<時間>` | 把這次設成 baseline（`evals/baseline.json`，要 commit） | 免費 |
| `python3 evals/run.py prepare <題目>` | 只建工作區、印出手動跑的指令（想親眼看 agent 怎麼做時用） | 免費 |

其他參數：`--model`（預設 opus）、`--budget-usd`（每個 run 的上限，預設 15）、`--max-total-usd`、`--timeout-min`（預設 45）、`--keep`。

## 結果

`evals/results/<時間>/`（gitignore）：
- `report.md`：總分與 baseline 的比較、每題的分數與成本、每個沒過的 grader 與細節、過程觀察（守門擋下、程序沒關、agent 自己回報哪裡得猜）
- `summary.json`：給程式讀的彙整（`accept` 就是把它存成 baseline）
- `runs/<題目>-<n>/`：`transcript.jsonl`（stream-json）、`grades.json`、`pr-body.md`、`pr.diff`、`gh-state.json`、`gh-calls.jsonl`

每題只跑 2 次，分數有雜訊。一次失敗先看原因，再決定要不要 `--case <題目> --runs 3` 重跑確認。

## grader

| grader | 權重 | 看什麼 |
|---|---|---|
| `run_completed` | 1 | session 正常結束（沒逾時、沒超過預算） |
| `pr_opened`（work） | 2 | 開了一個 PR：不是 draft、base 是 main、標題帶 `(#issue)` |
| `pushed_new_commits`、`synced_with_main`（fix） | 1 | 有推新的 commit；有 `main_advance` 的題目要合進最新的 main |
| `gate_tests` | 1 | PR head 上專案原本的測試全過 |
| `hidden_tests` | 3 | 放進題目的隱藏測試全過（只透過 issue 寫死的介面驗行為） |
| `scope` | 1 | 改到的檔都在 `allowed_paths` 裡（題目的「⛔ 不做」陷阱） |
| `pr_body_complete` | 1 | PR 內文有模板的必要段落、`Closes #n`、沒有沒代換的 `{{…}}` |
| `verified_sha_is_head` | 1 | 「驗的是 `<sha>`」就是 PR 最後的 commit（驗收的是真正要 merge 的東西） |
| `handoff_record` | 1 | PR 上有驗收過的修改紀錄（fix 題要第 2 輪），而且指向 PR head |
| `issue_status` | 1 | issue 的 bombolt 狀態是「審查中」 |
| `independent_checks` | 1 | 有派 bb-verifier 與 bb-reviewer |
| `no_human_questions`、`clean_worktree`（work） | 1 | 沒有停下來問人；worktree 沒留下沒 commit 的檔 |
| `review_threads_replied`、`threads_left_for_reviewer`（fix） | 1 | 每個 review thread 都有回覆；沒有自己標成 resolved |
| `screenshots`（ui 題） | 1 | PR 附了 before 與 after 截圖 |
| `requirement_adjustment_recorded`（case 有 `adjustment_regex`） | 1 | PR 內文的「📌 需求調整」不是「無」，而且提到那條調整 |
| `issue_unchanged`（case 有 `issue_unchanged`） | 1 | issue 內文跟種進去的一模一樣（需求調整寫在 PR，不改 issue） |
| `declined_bad_suggestion`（review.json 的 thread 標 `"expect": "decline"`） | 1 | 那個 thread 有 agent 的回覆，而且說明不改（「未修改：理由」） |

**反向題**（`"expect": "stop"`：正確的做法是停下來問，不是做完）只用這幾個：

| grader | 權重 | 看什麼 |
|---|---|---|
| `run_completed` | 1 | 同上 |
| `stopped_for_human` | 3 | issue 有 agent 留的「🤖 bombolt 實作暫停」，而且問題提到前提為什麼不成立（case 的 `stop_regex`） |
| `blocked_label`、`issue_status` | 1 | issue 有 `bombolt:blocked`；bombolt 狀態是「停工提問」 |
| `no_pr_opened` | 2 | 沒有發任何 PR |

## 題目

| 題目 | 測什麼 |
|---|---|
| `work-api-filter` | 一般功能（API 篩選）＋ 範圍陷阱：store.py 有個看起來可以順手修的 TODO，issue 說不要動 |
| `work-bugfix-overdue` | bb-plan 排查後開的 fix issue：先寫會失敗的重現測試、修邊界條件 |
| `work-ui-counter` | UI 改動：before/after 截圖、verifier 用瀏覽器實際操作 |
| `fix-review-sort` | 換一台電腦接手 PR、照 review thread 修改、回覆 thread、留第 2 輪紀錄 |
| `fix-conflict-sync` | PR 開了之後 main 有衝突的改動：合進最新 main、保留雙方意圖，再照 review 修改 |
| `fix-review-pushback` | review 不能照單全收：同事抓到的真 bug 要修；需求負責人（issue 作者）改需求要照改並記進「📌 需求調整」、issue 不改；同事違反 CLAUDE.md 的建議要拒絕並回覆理由 |
| `work-hidden-invariant` | 要動資料格式的功能：規則只寫在 `todo/CLAUDE.md`（改格式要 bump version、補 migration），issue 只說舊檔要能讀；只在讀取處 `.get()` 補洞的版本會被抓到（`naive.diff`） |
| `work-premise-broken` | 反向題：規劃之後同事把刪除改成封存（法遵），跟 issue 的「真的刪除（隱私）」衝突。要「停工提問」，不能自己挑一邊做 |

### 加一題

1. 在 `cases/<名字>/` 放：`case.json`、`issue.md`（照 [issue 模板](../skills/bb-plan/issue-template.md) 寫完整，最後一行是 `bombolt:meta`）、
   `hidden_tests/test_hidden_*.py`、`solution.diff`。fix 題另外要 `pr.diff`、`pr_body.md`、`review.json`。格式照現有的題目抄。
   可選的檔案：
   - `base.diff`（＋ `base_message`、`base_author`）：題目專屬的 main 歷史，在**規劃之前**就 commit 了（`base_sha` 包含它）。
     需要不同的起點時用它，不要改共用的 `fixture/`（會讓其他題目的 baseline 失去可比性）。
   - `main_advance.diff`（＋ `main_advance_message`、`main_advance_author`）：**規劃之後**同事 push 到 main 的改動。
     fix 題在 PR 開好之後、work 題在 issue 開好之後。
   - `naive.diff`：天真的做法（掉進陷阱的版本，跟 `solution.diff` 一樣從 main 產生）。selftest 會確認它過得了專案原本的測試、
     但過不了隱藏測試——證明陷阱真的會咬人。
   - `case.json` 的 `issue_author`：issue 的作者（預設是 agent 自己的帳號 `eval-bot`）。
   反向題（`"expect": "stop"`）不用 `solution.diff` 和 `hidden_tests/`，要 `stop_regex`（停工提問要提到什麼）和 `stop_example`
   （一個合格的問題，selftest 用它模擬「正確地停下來」）。
2. 模板可以用 `{{issue}}` `{{pr}}` `{{slug}}` `{{base_sha}}` `{{base_sha_short}}` `{{head_sha_short}}` `{{date}}` `{{repo}}`。
3. diff 一律用 `git diff` 從 fixture 產生，不要手寫。
4. `python3 evals/run.py selftest --case <名字>` 要過；新題目沒有 baseline，下一次 `accept` 才會有。

改了 fixture 會讓 baseline 失去可比性：改完要重跑一次全部、`accept` 新的 baseline。
