<!--
bb-plan 規劃跨 repo 成對 issue 的做法。一個需求要同時改兩個 repo（例如後端加 API、前端用它）時，
在一個 session 裡規劃兩邊、開兩個 issue（各在自己的 repo），用 meta 的 `pair` 互相連結。
bb-work 做下游時會先檢查上游進了 base_branch 沒；bb-list 會顯示配對與對方的狀態。
-->

# 跨 repo 的成對 issue

下面把目前這個 repo 叫 **A**、另一個叫 **B**。

## 規則

- 一個 issue 最多配一個別的 repo 的 issue。三個 repo 以上的需求通常已經超過 scope，照 sizing.md 駁回並給拆法。
- 關係（寫在 meta 的 `pair.relation`，指**這個 issue** 對對方）：
  - `depends_on`：這個是下游，要等對方的 PR 進到對方的 `base_branch` 才算依賴滿足（例如前端等後端的 API）。
  - `blocks`：這個是上游，對方在等它。
  - `parallel`：沒有先後，可以任意順序 merge（例如兩邊各自做 parallel change）。
- `related_repos` 是候選清單，讓你想到要不要跨 repo；使用者指定清單以外的 `owner/name` 也可以配對。

## 1. 要不要跨 repo（第 3 步探索完、第 4 步 scope 判斷前）

設定檔有 `related_repos` 時，根據探索結果判斷需求要不要也改其中某個 repo；使用者在需求裡就說了也算。
要的話用 AskUserQuestion 確認兩件事：「也在 `<owner/name>` 開配對 issue？」與關係（哪邊是上游、或平行）。
沒有 `related_repos`、使用者也沒提，就不考慮跨 repo。

## 2. 準備 B

1. **找 B 在本機的主 checkout**：
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_snapshot.py" locate --repo <owner/name>
   ```
   - `found` → 用 `path`。
   - `multiple` → 用 AskUserQuestion 把 `paths` 列成選項問使用者。
   - `not_found` → 用 AskUserQuestion 問主 checkout 的路徑（本機還沒有的話，請使用者先 clone）。

   使用者給的路徑要先驗證，下面的輸出要等於小寫的 `<owner/name>`：
   ```bash
   python3 -c "import sys; sys.path.insert(0,'${CLAUDE_PLUGIN_ROOT}/scripts'); import bb_lib; print(bb_lib.repo_slug(sys.argv[1]))" "$(git -C <路徑> remote get-url origin)"
   ```
2. **前置檢查**：`cd <B> && python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_plan_check.py"`，結果照第 0 步 A、B 的規則處理。
   - B 沒有 `.claude/bombolt.md` → 告訴使用者先到 B 的主 checkout 跑 `/bombolt:bb-setup` 並 push，再重新規劃。
     刪掉 A 的快照（第 10 步的指令），**這個 session 到此結束**。不要只規劃 A。
3. **讓這個 session 讀得到 B**：B 在這個 session 的工作目錄之外。請使用者在提示列輸入 `/add-dir <B 的主 checkout>`，等他做完再繼續。
   不加的話，你、Explore 和 cold-reader 讀 B 都會一直跳權限確認。
4. **B 的快照**：`cd <B> && python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_snapshot.py" create`（放在 B 的 `.claude/worktrees/_plan-*`）。
   之後探索 B 一律讀這份快照，B 的設定檔讀 `<B 快照>/.claude/bombolt.md`。照第 3 步探索 B。

## 3. scope 與訪談

- scope：兩個 issue **各自**照 sizing.md 判斷，任一個太大就照駁回流程處理整組。
- 訪談多一個面向「**跨 repo 介面契約**」：欄位、路徑、錯誤格式，以及版本相容。
  上游先上線、下游還沒上線時不能壞；需要的話用 parallel change：先加新的，下游改完再拿掉舊的。

## 4. 兩份草稿

- 兩份都照 [issue-template.md](issue-template.md)，各寫到自己的暫存檔，`{{repo}}`、`base`、DoD 的閘門各用自己 repo 的設定。
  B 的 `{{repo}}` 在 B 裡取：`cd <B> && bb-gh repo view --json name -q .name`。
- 兩份的「🔗 配對 issue」寫**同一段**介面契約，加上關係與 merge 順序。對方的網址先寫 `{{pair_url}}`。
- meta 加 `"pair":{"url":"{{pair_url}}","relation":"<這份對對方的關係>"}`。兩份的關係要對得上：一份 `depends_on`、另一份就是 `blocks`；平行的兩份都是 `parallel`。
- 「🚀 本地開始執行」各寫自己 repo 的主 checkout。下游那份加一句：上游的 PR 進了 base 再開工，或開工時選「依契約先開工」。

## 5. 冷讀

每份草稿各派**新的** cold-reader，給它四個路徑：自己的草稿、自己的快照、自己的設定檔，以及對方的草稿（只當參考）。
兩份都要冷讀到沒有需要猜的地方。對方草稿要一起改的話，改完那份也要重新冷讀。

## 6. 開 issue（第 9 步）

兩份一起給使用者看，一次確認。對 B 的 gh 操作一律在 **B 的主 checkout 執行並帶 `-R`**：`bb-gh` 依目前目錄選 GitHub 帳號，
A、B 分屬不同帳號（例如公司／個人）時，在 A 的目錄跑會失敗或用錯身分。

1. 先開**上游**（`parallel` 時先開 A 的）。這時它的 `{{pair_url}}` 還沒有值，先保留不動。
   上游在 B 的話，`label create` 和 `issue create` 都要 `cd <B> && bb-gh … -R <owner/name>`。
2. 再開下游：先把它草稿裡的 `{{pair_url}}`（meta 與「🔗 配對 issue」）換成上游的網址再開。
3. 回填兩份：
   - 上游草稿的 `{{pair_url}}` 換成下游的網址。
   - 兩份的 `{{issue_number}}` 換成各自的編號。
   - 各自 `issue edit <n> --body-file`。B 那份一樣在 B 裡、帶 `-R`。
   回填失敗的話，上游 issue 會停在無效配對（bb-list 顯示「配對資料格式不對」）。
   這時把錯誤和要手動執行的 `issue edit` 指令給使用者。

## 7. 收尾（第 10 步）

- 兩份快照都刪掉，B 的在 B 裡刪：`cd <B> && python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_snapshot.py" remove <B 快照路徑>`。
- 下一步給兩條指令，寫明各在哪個 repo 的主 checkout 執行，以及哪個先做。
  平行的兩個可以同時做；有先後的先做上游，下游等上游的 PR 進了 base 再做（或依契約先開工）。
