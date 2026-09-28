# 改 bombolt 本身時的規矩

bombolt 是給**所有專案**用的 Claude Code plugin，使用說明見 `README.md`。

- **通用性**：skill、agent、腳本裡不可以出現任何單一專案的東西（路徑、指令、branch 名、公司內部服務）。
  專案專屬的內容一律放在該專案的 `.claude/bombolt.md`。
  `skills/bb-work/coding-style.md` 是使用者本人的寫法，刻意當成所有專案的預設，不算單一專案的東西。
- **固定步驟寫成腳本，判斷留給 LLM**：
  - 腳本（`scripts/`）只用 python 標準函式庫，要能在 python 3.9 上跑。
  - 改腳本就要補測試，並跑 `python3 -m unittest discover -s tests`。
- **skill 裡的動態注入（`` !`...` ``）失敗會讓整個 skill 載入失敗**。
  被注入的腳本要永遠 exit 0，把問題印成文字（見 `scripts/bb_context.py`）。
- **安全守門（`scripts/bb_guard.py`）只在 bombolt worktree 裡生效**。新增規則時兩個方向都要補測試：
  - 該擋的有擋
  - 正常工作用到的指令沒被誤擋
- **測試防呆、刪除、關閉類的功能，只對自己建的測試對象做**（暫存 repo、拋棄式 session、假程序），
  不要對使用者正在用的 session、branch、檔案執行「看它會不會擋」——判斷寫錯的話，損失是真的。
- 改完 manifest、skill 或 agent 之後跑 `claude plugin validate .`。
  唯一可以接受的 warning 是「沒有 version」：刻意不釘版號，讓同事跟著 commit 自動更新。
- 所有給使用者看的文字用繁體中文，程式碼識別字用英文。
