---
# ⚠️ frontmatter 只支援三種寫法：`key: 值`、`key: [a, b]`、`key:` 下面接 `  - 項目`
#    含逗號的值（例如 regex 的 `{1,3}`）一定要用 `- 項目` 的寫法；以 # 開頭的行是註解
# 這一段由 bombolt 的腳本讀（建 worktree、安全守門），下面的內文由 agent 讀。

# feature branch 從 origin/<base_branch> 的最新版長出來
base_branch: main

# PR 開到哪一支；跟 base_branch 不同才需要填。留空就跟 base_branch 一樣。
pr_base:

# PR 開好（而且完成定義全部通過）之後，要用 merge commit（--no-ff）整合進去的 branch。沒有就留空 []。
integration_branches: []

# 不允許 push 的 branch（main/master/prod/production/release 已內建，這裡是額外的）
protected_branches: []

# 建 worktree 時從主 checkout 複製過去的 gitignored 檔（通常是 .env）
copy_files: []

# 安全守門：除了內建的私鑰／雲端憑證，這個 repo 另外不准讀寫的秘密檔（檔名或路徑 glob）
secret_paths: []

# 安全守門：除了內建的，這個 repo 另外禁止的指令（regex）
deny_commands: []
---

# bombolt 專案設定：todo-app

> 這份給 bombolt 的 agent 讀（規劃 session 與實作 session 都會讀）。
> 用 `/bombolt:bb-setup` 產生，之後直接手改就好。改了記得 commit。

## 分支流程

- feature branch 從最新的 `origin/main` 長出來，PR 開回 `main`。
- merge 進 `main` 就算上線（沒有部署流程），agent 不碰 `main`。

## 專案知識在哪

- `README.md`：結構、API、怎麼在本機跑
- `CLAUDE.md`：程式碼慣例（錯誤回應格式、日期處理、測試寫法）

## 排查資源（bb-plan 排查問題時用；只能唯讀）

無。排查時只看 code 和使用者給的資訊。

## 開工準備（worktree 建好之後、動手之前）

不用安裝任何東西（只用 python 標準函式庫）。確認 `python3 --version` ≥ 3.9 就好。

## 發版後同步（發版後會把 integration_branches 或 pr_base reset 回 base_branch 才會用到）

無。這個 repo 沒有 integration branch，發版也不會 reset 任何 branch。

## ✅ 驗證閘門（每次交付前都要全綠）

```bash
python3 -m unittest discover -s tests    # 幾秒內跑完，最後一行是 OK
```

## 啟動 app（實際驗收與截圖用；純後端也要寫）

平行的 worktree 用 issue 編號錯開 port 和資料檔，才不會互相干擾。在 worktree 根目錄：

```bash
mkdir -p .bombolt
python3 -m todo.server --port $((20000 + <issue 編號>)) --data .bombolt/todos.json
```

- 成功時印出 `ready on http://localhost:<port>`。
- 確認服務起來了：`curl -s http://localhost:<port>/api/todos` 回 `{"todos": [...]}`。
- server 每次 request 都重新讀資料檔，所以用 CLI 塞的資料（同一個 `--data`）重新整理就看得到。
- `.bombolt/` 是每個 worktree 自己的暫存目錄，不會被 commit。
- 本機沒有串任何對外的服務（寄信、金流、通知都沒有），verifier 可以放心操作所有功能。

## 登入與測試資料

- 沒有登入，打開就能用。
- 測試資料用 CLI 塞進同一個資料檔：
  ```bash
  python3 -m todo.cli --data .bombolt/todos.json add "買牛奶" --due 2026-10-20
  python3 -m todo.cli --data .bombolt/todos.json done 1
  ```
  或直接用 API：`curl -s -X POST http://localhost:<port>/api/todos -H 'Content-Type: application/json' -d '{"title":"繳電費"}'`
- 跟日期有關的行為（逾期）啟動時加 `TODO_TODAY=YYYY-MM-DD` 固定「今天」，例如 `TODO_TODAY=2026-10-14 python3 -m todo.server ...`。

## 人工測試（PR 的「📋 人工測試」用）

在本機測：到 PR 寫的 worktree，照上面「啟動 app」啟動（同一個 port），打開 `http://localhost:<port>`；
API 用 `curl`，CLI 直接在 worktree 根目錄執行 `python3 -m todo.cli --data .bombolt/todos.json ...`。

## Sandbox（外網測試：使用者在外面用手機測 PR 時用）

- 對外開「啟動 app」的那個 port 就好：網頁和 API 都在同一個 port，網頁沒有打別的 port。
- 啟動時不用多加設定。

## 截圖慣例

before／after 同一個畫面、同樣的資料、同樣的視窗大小（1280×800）。

## commit 與 PR 慣例

- commit message 用英文、一行，加前綴：`feat: …`、`fix: …`、`test: …`、`docs: …`。
- PR 標題帶 `(#<issue>)`。

## 這個 repo 的禁區與注意事項

- 不要引入第三方套件（見 `CLAUDE.md`）。
- `data/` 是開發者自己的資料，不要讀寫；驗收一律用 `.bombolt/` 底下的資料檔。
