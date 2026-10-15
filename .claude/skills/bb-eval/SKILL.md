---
name: bb-eval
description: 開發 bombolt 本身用：改完 skill、agent、hook 或腳本之後，用固定題目實際跑 bb-work／bb-fix（evals/），打分、跟 baseline 比，找出退步與原因。使用者說「跑 eval」「跑一下評估」「看這次改動有沒有變好／退步」「bb-eval」時用。
argument-hint: "[題目 glob…，例如 'fix-*'；留空跑全部] [這次改了什麼]"
---

# bb-eval：改完 bombolt 之後，量量看有沒有變好

eval 會在 `$TMPDIR` 建拋棄式的 repo（本機 bare repo 當 origin、假的 gh 當 GitHub），
用 headless 的 `claude -p "/bombolt:bb-work <n>"` 載入**這個 repo 目前的工作目錄**（`--plugin-dir`，沒 commit 的改動也算），
實際跑完，再用確定性的 grader 打分。不碰真的 GitHub、不碰使用者的 repo。設計與題目見 [evals/README.md](../../../evals/README.md)。

使用者的參數：$ARGUMENTS

## 1. 確認要量的是什麼

```bash
git status --short
python3 -c "import json;b=json.load(open('evals/baseline.json'));print(b['bombolt']['sha'], b['overall'], b['model'])" 2>/dev/null || echo "還沒有 baseline"
git log --oneline <baseline 的 sha>..HEAD -- skills agents scripts hooks bin   # 有 baseline 才跑
git diff --stat <baseline 的 sha> -- skills agents scripts hooks bin
```

用一兩句話告訴使用者：這次量的是哪些改動（跟 baseline 比多了什麼）。這些改動就是等一下解讀結果的依據。

## 2. 先跑免費的檢查

```bash
python3 -m unittest discover -s tests
python3 evals/run.py selftest
```

任何一個沒過 → 先停下來處理（eval 本身壞了，跑出來的分數沒有意義），不要直接開跑。

## 3. 開跑（會花錢）

- 範圍：使用者有指定題目就照指定；沒指定就跑全部。改動只碰到 bb-fix 的話，可以建議只跑 `--case 'fix-*'` 省錢，但照使用者決定。
- 這個對話裡使用者還沒明確說要跑 eval（例如是你自己判斷該跑）→ 先用 AskUserQuestion 確認，附上預估：
  全部約 US$25–40、30–45 分鐘（實測每個 run 約 US$2、4–7 分鐘）（8 題 × 2 次，同時 3 個）。使用者已經叫你跑，就不用再問。
- 用 Bash 的 `run_in_background` 執行，不要用 sleep 輪詢，跑完會通知你：

  ```bash
  python3 evals/run.py run --label "<這次改了什麼，一句話>" [--case <glob>...]
  ```

  它會一行一行印出每個 run 的分數，最後印出完整報告（也存在 `evals/results/<時間>/report.md`）。

## 4. 解讀結果

讀報告。順序：

1. **eval 本身的問題先排除**：`💥 eval 本身出錯`、「假 gh 不支援的呼叫」→ 是 evals/ 要修，不是 bombolt 退步。分開回報。
2. **每一個沒過的 grader 都要找到原因**，不要只看分數：
   ```bash
   python3 evals/run.py timeline <run 目錄> --main-only    # 主 session 做了什麼
   python3 evals/run.py timeline <run 目錄>                # 連 subagent 一起看
   ```
   需要細節時才 grep `transcript.jsonl`（很大，不要整份讀）；PR 內文、diff 在同一個 run 目錄的 `pr-body.md`、`pr.diff`。
   工作區還留著的話（沒有全過的 run 都會留），可以直接進去看 git 狀態。
3. **把原因連回這次的改動**：哪一段 skill／agent 的文字（`path:line`）讓 agent 這樣做？還是跟這次改動無關、本來就會發生（看 baseline 那一題的分數）？
4. **雜訊還是退步**：每題只跑 2 次。一次失敗、而且原因跟這次改動無關 → 標成「可能是雜訊」，建議只重跑那一題（`--case <題目> --runs 3`），不要直接下結論。
5. 「過程觀察」裡的 agent 自己回報（自己猜的、卡住的）、守門擋下、程序沒關，即使分數沒掉也是改進 skill 的線索。

## 5. 回報

用繁體中文告訴使用者：
- 總分與 baseline 的比較、每題的變化、成本與時間的變化
- 退步：哪一題、哪個 grader、原因、對應到哪段改動，以及建議怎麼改
- 進步與值得注意的觀察
- 結論：這次的改動可以留（建議設成新的 baseline）／要先修什麼／要多跑幾次才知道

然後用 AskUserQuestion 問要不要把這次設成新的 baseline（「設成 baseline」／「先不要」）。要的話：

```bash
python3 evals/run.py accept evals/results/<時間>
```

`evals/baseline.json` 要 commit 才會留下來，使用者要求時才 commit。

## 規矩

- **eval 是量尺**：不要為了讓分數變好去改題目、grader 或 baseline。題目或 grader 真的寫錯了，跟使用者說，另外修，修完跑 `selftest`。
- 只有使用者同意才 `accept`。
- 改了 `evals/` 底下的東西：跑 `python3 -m unittest discover -s tests` 與 `python3 evals/run.py selftest`。
