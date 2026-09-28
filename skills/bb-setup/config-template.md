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
# 例如測試環境用的 stage。每次 bb-fix 更新 PR 之後也會再整合一次。沒有就留空 []。
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

## 排查資源（bb-plan 排查問題時用；只能唯讀）

<!-- 排查使用者回報的問題時去哪裡找證據：log 在哪裡、怎麼查（寫得出指令就寫指令）、錯誤追蹤（Sentry 之類）、
     資料有沒有唯讀的查法、用到的第三方服務的狀態頁。只寫唯讀的方式，並寫明要什麼權限。
     沒有就寫「無」（排查時只看 code 和使用者給的資訊）。 -->

{{debug_resources}}

## 開工準備（worktree 建好之後、動手之前）

{{setup}}

## 發版後同步（發版後會把 integration_branches 或 pr_base reset 回 base_branch 才會用到）

<!-- 人怎麼發版：PR 怎麼進 base_branch（pr_base 不同時，pr_base 的內容怎麼送進 base_branch）？怎麼 push？
     部署怎麼觸發（CI/CD 認哪一支）？發完之後 integration_branches（與 pr_base）會不會被 reset 回 base_branch？
     這些決定了發版後何時該在主 checkout 跑 /bombolt:bb-fix 同步、跑完要不要重新整合。沒有這個流程就寫「無」。 -->

{{release_sync}}

## ✅ 驗證閘門（每次交付前都要全綠）

{{gates}}

## 啟動 app（實際驗收與截圖用；純後端也要寫）

<!-- 並行的 worktree 之間怎麼隔開（port、資料庫、cookie…）。需要編號的話用 issue 編號（`bb_worktree.py info` 印得出來）。
     純後端：怎麼確認服務起來了（例如打哪個端點會回什麼）。
     本機啟動的服務會不會真的對外發訊息、寄信、扣款（.env 用的是 sandbox 還是正式的金鑰）：
     這裡寫明是 sandbox 或 mock 的，verifier 才會操作會觸發它們的功能。 -->

{{run_app}}

## 登入與測試資料

{{login}}

## 人工測試（PR 的「📋 人工測試」用）

<!-- 人 review 完 PR、按 merge 之前自己在哪裡測：
     stage：哪一支（通常是 integration_branches）部署到哪個網址、要多久、
     怎麼確認 stage 上已經是某個 commit（例如部署紀錄、版本頁）、stage 上用哪個測試帳號。
     本機：在哪個目錄（通常是 PR 的 worktree）、用什麼指令啟動、開哪個網址。
     每次都要人看的項目（例如手機版）也寫在這裡。密碼不要寫進來，寫去哪裡拿。
     沒有這一節的話，PR 的人工測試以本機、照「啟動 app」寫。 -->

{{manual_test}}

## Sandbox（外網測試：使用者在外面用手機測 PR 時用）

<!-- bb-sandbox 用 ngrok 把本機跑起來的 app 開到外網（只有一個網址、一個 port）時要注意什麼：
     對外開哪個 port：前端透過 dev proxy 呼叫後端（相對路徑 `/api`）的話就是前端的 port；
     前端直接打 `http://localhost:<後端 port>` 的話，手機上一定壞，寫出來。
     啟動時要多加的設定，例如 Vite 要允許 tunnel 的網址（`__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS=.ngrok-free.app`）；
     Next.js 16 以上要在 next.config 的 `allowedDevOrigins` 加 `*.ngrok-free.app`（repo 還沒加的話寫出來，外網會被擋）。
     外網測不了的功能（例如 OAuth 登入的 callback 只登記了 localhost）。
     沒有可以從外面操作的東西（函式庫、CLI）寫「不適用」。 -->

{{sandbox}}

## 截圖慣例

{{screenshots}}

## commit 與 PR 慣例

{{conventions}}

## 這個 repo 的禁區與注意事項

{{cautions}}
