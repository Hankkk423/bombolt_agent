---
# ⚠️ frontmatter 只支援三種寫法：`key: 值`、`key: [a, b]`、`key:` 下面接 `  - 項目`
#    含逗號的值（例如 regex 的 `{1,3}`）一定要用 `- 項目` 的寫法；以 # 開頭的行是註解
# 這一段由 bombolt 的腳本讀（建 worktree、安全守門），下面的內文由 agent 讀。

# feature branch 從 origin/<base_branch> 的最新版長出來
base_branch: {{base_branch}}

# PR 開到哪一支；跟 base_branch 不同才需要填（例如 feature 從 prod 長出來，但 PR 開到單獨的
# release branch，上版由人手動把多個 PR 一起併進 prod）。留空就跟 base_branch 一樣。
pr_base: {{pr_base}}

# PR 開好（而且完成定義全部通過）之後，要用 merge commit（--no-ff）整合進去的 branch，
# 例如 stage 用的 dev。每次 bb-fix 更新 PR 之後也會再整合一次。沒有就留空 []。
integration_branches: [{{integration_branches}}]

# 不允許 push 的 branch（main/master/prod/production/release 已內建，這裡是額外的）
protected_branches: [{{protected_branches}}]

# 建 worktree 時從主 checkout 複製過去的 gitignored 檔（通常是 .env）
# ⚠️ 只會複製「真的有被 gitignore」的檔，避免它被 commit 進去
copy_files:
{{copy_files}}

# 安全守門：除了內建的私鑰／雲端憑證，這個 repo 另外不准讀寫的秘密檔（檔名或路徑 glob）
# 含 `/` 的 pattern 比對相對於 repo 根目錄的路徑（`/.env.local` 只指根目錄那個）；不含 `/` 的比對任何一層的檔名
# 內建規則不擋 .env*（worktree 要靠它們才能跑），要擋特定的 .env（例如正式金鑰）就寫出它的路徑
secret_paths:
{{secret_paths}}

# 安全守門：除了內建的（kubectl、vercel --prod、Slack/LINE/寄信 API…），這個 repo 另外禁止的指令（regex）
deny_commands:
{{deny_commands}}
---

# bombolt 專案設定：{{repo_name}}

> 這份給 bombolt 的 agent 讀（規劃 session 與實作 session 都會讀）。
> 用 `/bombolt:bb-setup` 產生，之後直接手改就好。改了記得 commit。

## 分支流程

{{branch_flow}}

## 專案知識在哪

{{knowledge}}

## 開工準備（worktree 建好之後、動手之前）

{{setup}}

## 發版後同步（發版後會把 integration_branches 或 pr_base reset 回 base_branch 才會用到）

<!-- 人怎麼發版：PR 怎麼進 base_branch（pr_base 不同時，pr_base 的內容怎麼送進 base_branch）？怎麼 push？
     部署怎麼觸發（CI/CD 認哪一支）？發完之後 integration_branches（與 pr_base）會不會被 reset 回 base_branch？
     這些決定了發版後何時該在主 checkout 跑 /bombolt:bb-fix 同步、跑完要不要重新整合。沒有這個流程就寫「無」。 -->

{{release_sync}}

## ✅ 驗證閘門（每次交付前都要全綠）

{{gates}}

## 啟動 app（UI 驗收與截圖用）

<!-- 並行的 worktree 之間怎麼隔開（port、資料庫、cookie…）。需要編號的話用 issue 編號（`bb_worktree.py info` 印得出來）。 -->

{{run_app}}

## 登入與測試資料

{{login}}

## 截圖慣例

{{screenshots}}

## commit 與 PR 慣例

{{conventions}}

## 這個 repo 的禁區與注意事項

{{cautions}}
