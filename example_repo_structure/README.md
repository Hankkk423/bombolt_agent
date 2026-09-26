# 你的 repo 要放哪些檔案

這個資料夾模擬「一個已經接上 bombolt 的專案 repo」。以一個虛構的 Node.js 專案 `myapp` 為例，只列出跟 bombolt 有關的檔案：

```
myapp/
├── .claude/
│   ├── bombolt.md        ← 必要：這個 repo 的 bombolt 設定，要 commit
│   └── settings.json     ← 選用：在這個 repo 指定要用 bombolt，要 commit
└── ...（你原本的程式碼）
```

## `.claude/bombolt.md`（必要）

[範例](.claude/bombolt.md)

不用自己從零寫：在 repo 的主 checkout 執行 `/bombolt:bb-setup`，它會調查 repo、問你幾輪問題，然後產生這份檔案。
之後直接手改就好，改了記得 commit。

分成兩段：

| 段落 | 誰讀 | 內容 |
|---|---|---|
| 最上面 `---` 之間的 frontmatter | bombolt 的腳本 | base branch、PR 開到哪、要整合進哪些 branch、要複製進 worktree 的檔、額外的安全禁區 |
| 下面的內文 | agent | 開工準備、驗證閘門、怎麼啟動 app、怎麼登入、commit 慣例、注意事項 |

內文要寫到「沒參與討論的人照著做就能跑起來」：完整指令、在哪個目錄跑、大概跑多久、成功長什麼樣子。
實作 session 能不能自己驗收到真的完成，就看這份寫得夠不夠具體。

## `.claude/settings.json`（選用）

[範例](.claude/settings.json)

加了之後，同事在這個 repo 開 Claude Code 時會自動加入 bombolt 的 marketplace，並把 bombolt 設成啟用。
還沒安裝的人，`/plugin` 的 **Errors** 分頁會提醒他，執行一次 `claude plugin install bombolt@hankkk423` 就好。
每個人都已經照 bombolt 的 README 自己裝好的話，就不需要這個檔案。

如果你的 repo 本來就有 `.claude/settings.json`，把這兩個 key 合併進去就好，不要整份覆蓋。

## 不用放進 repo、bombolt 會自己處理的

| 東西 | 位置 | 說明 |
|---|---|---|
| worktree | `.claude/worktrees/bb-<issue>-<slug>/` | 每個 issue 一個，實作時自動建立 |
| 截圖、PR 內文、進度檔 | `<worktree>/.bombolt/` | 刪 worktree 時一起消失 |
| 忽略設定 | `.git/info/exclude` | 上面兩個路徑會自動加進去，只影響本機，不用改 `.gitignore` |
| GitHub label | `bombolt`、`bombolt:blocked` | 第一次用到時自動建立 |

⚠️ 如果 repo 裡有會打包整個 repo 根目錄的工具（例如 `.dockerignore`、`.vercelignore`、`.npmignore`），
要在那些檔案加上 `.claude/worktrees/`，不然打包時會把每個 worktree（包含複製過去的 `.env`）一起帶走。
`/bombolt:bb-setup` 會檢查並問你。
