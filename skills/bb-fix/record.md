<!--
修改紀錄（PR 留言）的寫法。bb-work 發 PR 時記第 1 輪；bb-fix 每一輪結束時記、接手時補記、發版後同步時記。
讀者是「下一個接手的 agent」：可能是明天新開的 session、另一台電腦、或同事的電腦，都看不到這次的對話。
-->

# 修改紀錄怎麼寫

PR 就是交接本：
- **PR 內文**寫現在的樣子（逐段改動、驗收結果、人工測試、📌 需求調整），每一輪整段覆寫。
- **修改紀錄**寫怎麼走到這裡：一輪一則 PR 留言，只新增、不改別人的。
- **issue 不改**：它是原本的需求。這個 PR 裡人決定的調整，寫在 PR 內文的「📌 需求調整」。

## 只記 code 看不出來的

判斷標準：**下一個 agent 不知道這件事，會不會做錯、或做得不一樣？** 會就寫，不會就不寫。
改了哪幾行 diff 就看得到，不用重寫；下一個 agent 會自己讀 code、跑驗收。它缺的是：

- **為什麼有這一輪**：哪幾則 review（附連結）、使用者在 session 裡說了什麼。照他的意思寫清楚，不要只寫「使用者要求修改」。
- **決定**：選了什麼、否決了什麼、為什麼。特別是「看起來該改、但刻意不改」的地方：
  沒寫下來的話，下一個 agent 最常犯的錯就是把它改回去。
- **試過但不行**：試了什麼、為什麼不行（錯誤訊息、卡在哪裡），讓下一個不用再走一次。
- **環境的坑**：這個 PR 特有的（要先 seed 什麼、哪個測試會 flaky）。整個專案都適用的，寫進 PR 的 Follow-up，建議補進 `.claude/bombolt.md`。
- **紀錄跟 code 不一致的地方**：接手時發現前面的紀錄或 PR 內文跟 code 對不上，以 code 為準，寫出哪裡不一致。
- **還沒做**：沒處理的 review、沒過的完成定義、刻意留到之後的事。

## 格式

`--body-file` 的內文照這個順序寫，沒有內容的標題整段省略：

```markdown
**為什麼有這一輪**
- review：[<一句話>](<留言連結>)
- 使用者在 session 裡說：<…>

**改了什麼**
- <一句話，對應哪一則 review 或需求調整>（`<commit 短 sha>`）

**決定**
- <選了什麼>，不用 <否決的>：<理由>

**試過但不行**
- <…>

**環境的坑**
- <…>

**紀錄跟 code 不一致的地方**
- <…>

**還沒做**
- <…>
```

第一行（第幾輪、誰、哪台電腦、日期、commit 範圍、驗收過沒）和 commit 清單由腳本寫，不要自己寫。
`--summary` 是一句話（例如「回應 3 則 review、預設改成下個月」），會出現在留言的標題和 PR 內文的修改歷程。

## 規則

- **寫給沒有這次對話的人**：不要寫「如討論」「照剛剛說的」，把結論寫出來。
- **不寫秘密**：sandbox 的帳密、測試帳號的密碼、`.env` 的值、token 都不寫。看得到 PR 的人比看得到 session 的人多。
- 不要 @ 人（會通知他）；帳號、指令、識別字包成 inline code。
- 精簡：展開後 10–20 行。細節在 code、diff 和 PR 內文裡。

## 什麼時候記、用哪個指令

一律在推上去之後記（紀錄只記已經 push 的 commit）。

| 時機 | 指令 |
|---|---|
| bb-work 發 PR（第 1 輪） | `bb_pr.py record --kind round`（驗收全過加 `--verified`） |
| bb-fix 每一輪結束（一輪的最後一步） | `bb_pr.py record --kind round`（驗收全過加 `--verified`） |
| 接手時發現沒有紀錄的 commit | `bb_pr.py record --kind catchup`：讀 diff 整理，推論的地方標明是推論 |
| 發版後同步（bb-fix 的 B） | `bb_pr.py record --kind sync` |
| session 裡做了不用改 code、但會影響之後怎麼改的決定（「這條 review 不改」「先不做 X」） | `bb_pr.py note --text "…"`：**當下就記**，不要等收尾，使用者隨時可能關掉 session |

完整的參數：

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_pr.py" record --pr <PR 編號> --session-id ${CLAUDE_SESSION_ID} \
  --kind <round|catchup|sync> --summary "<一句話>" --body-file <artifacts>/record.md [--verified]
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_pr.py" note --pr <PR 編號> --session-id ${CLAUDE_SESSION_ID} --text "<決定與理由>"
```

`--verified` 只在 verifier 這一輪在**這個 commit** 上全部通過時加（交給人測的 ⚠️ 不算沒過）。
沒過、這一輪沒跑驗收、或只跑了閘門，都不加：下一個接手的會重新驗收。
純粹的問答、解釋、看 log 不用記。
