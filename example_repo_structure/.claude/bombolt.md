---
# ⚠️ frontmatter 只支援三種寫法：`key: 值`、`key: [a, b]`、`key:` 下面接 `  - 項目`
#    含逗號的值（例如 regex 的 `{1,3}`）一定要用 `- 項目` 的寫法；以 # 開頭的行是註解
# 這一段由 bombolt 的腳本讀（建 worktree、安全守門），下面的內文由 agent 讀。

# feature branch 從 origin/<base_branch> 的最新版長出來
base_branch: main

# PR 開到哪一支；跟 base_branch 不同才需要填（例如 feature 從 prod 長出來，但 PR 開到單獨的
# release branch，上版由人手動把多個 PR 一起併進 prod）。留空就跟 base_branch 一樣。
pr_base:

# PR 開好（而且完成定義全部通過）之後，要用 merge commit（--no-ff）整合進去的 branch，
# 例如測試環境用的 stage。每次 bb-fix 更新 PR 之後也會再整合一次。沒有就留空 []。
integration_branches: []

# 不允許 push 的 branch（main/master/prod/production/release 已內建，這裡是額外的）
protected_branches: []

# 建 worktree 時從主 checkout 複製過去的 gitignored 檔（通常是 .env）
# ⚠️ 只會複製「真的有被 gitignore」的檔，避免它被 commit 進去
copy_files:
  - .env

# 安全守門：除了內建的私鑰／雲端憑證，這個 repo 另外不准讀寫的秘密檔（檔名或路徑 glob）
# 含 `/` 的 pattern 比對相對於 repo 根目錄的路徑（`/.env.local` 只指根目錄那個）；不含 `/` 的比對任何一層的檔名
# 內建規則不擋 .env*（worktree 要靠它們才能跑），要擋特定的 .env（例如正式金鑰）就寫出它的路徑
secret_paths:
  - /.env.production

# 安全守門：除了內建的（kubectl、vercel --prod、Slack/LINE/寄信 API…），這個 repo 另外禁止的指令（regex）
deny_commands:
  - npm run deploy
  - npm run db:migrate:prod
---

# bombolt 專案設定：myapp

> 這份給 bombolt 的 agent 讀（規劃 session 與實作 session 都會讀）。
> 用 `/bombolt:bb-setup` 產生，之後直接手改就好。改了記得 commit。

## 分支流程

- feature branch 從最新的 `origin/main` 長出來，PR 開回 `main`。
- merge 進 `main` 之後，GitHub Actions 會自動部署到正式環境。agent 不碰部署。

## 專案知識在哪

- `README.md`：架構與本機開發方式
- `CLAUDE.md`：程式碼慣例
- `docs/api.md`：API 規格

## 排查資源（bb-plan 排查問題時用；只能唯讀）

- 正式環境的錯誤：Sentry 的 `myapp` 專案。agent 沒有權限，請使用者貼錯誤的連結或內容。
- 正式環境的 log 與資料：沒有給 agent 的唯讀方式，請使用者提供發生的時間點與畫面。

## 開工準備（worktree 建好之後、動手之前）

worktree 是全新的 checkout，沒有 `node_modules`，要自己裝：

```bash
npm ci        # 約 1 分鐘
```

`.env` 已經由 `copy_files` 從主 checkout 複製進來，不用另外處理。

## 發版後同步（發版後會把 integration_branches 或 pr_base reset 回 base_branch 才會用到）

無。這個 repo 沒有 integration branch，發版也不會 reset 任何 branch。

## ✅ 驗證閘門（每次交付前都要全綠）

```bash
npm run lint     # 約 20 秒
npm test         # 約 1 分鐘，最後一行是 "Tests: N passed"
npm run build    # 約 1 分鐘
```

## 啟動 app（實際驗收與截圖用；純後端也要寫）

平行的 worktree 用 issue 編號錯開 port 和資料庫，才不會互相干擾：

```bash
PORT=$((3000 + <issue 編號> % 1000)) DATABASE_URL=file:./.bombolt/dev.db npm run dev
```

成功時 log 會出現 `ready on http://localhost:<port>`。
`.bombolt/` 是每個 worktree 自己的暫存目錄，不會被 commit。
本機沒有串任何對外的服務（寄信、金流），verifier 可以放心操作。

## 登入與測試資料

- 先跑 `DATABASE_URL=file:./.bombolt/dev.db npm run db:seed`，會建立測試帳號 `demo@example.com`。
- 密碼在 `.env` 的 `SEED_PASSWORD`，不要寫進這份檔案。
- 不要用真實客戶的帳號登入。

## 人工測試（PR 的「📋 人工測試」用）

在本機測：到 PR 寫的 worktree，照上面「啟動 app」啟動（同一個 port），打開 `http://localhost:<port>`，用 `demo@example.com` 登入。

## Sandbox（外網測試：使用者在外面用手機測 PR 時用）

- 對外開 app 的 port（同「啟動 app」）。前端呼叫 API 都用相對路徑 `/api/...`，同一個 port，手機上也能用。
- `next.config.js` 已經在 `allowedDevOrigins` 加了 `*.ngrok-free.app`，啟動時不用多加設定。

## 截圖慣例

before／after 同一個畫面、同樣的視窗大小（1280×800）。

## commit 與 PR 慣例

- commit message 用英文、一行，加前綴：`feat: …`、`fix: …`、`docs: …`。
- PR 標題帶 `(#<issue>)`。

## 這個 repo 的禁區與注意事項

- 不要改 `migrations/` 裡已經存在的檔案；要改 schema 就新增一個 migration。
- `.env.production` 是正式環境的金鑰，不准讀。
