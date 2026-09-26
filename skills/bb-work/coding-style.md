# coding style

bombolt 寫 code 的預設寫法。規劃（bb-plan）、實作（bb-work、bb-fix）、review（bb-reviewer）都照這份。
目標：**agent 最好讀、最好改，人看了也舒服**。適用任何語言、框架、專案大小。

## 什麼時候用

- **新寫、改寫的程式碼照這份**，優先於 repo 既有的寫法。
- **沒動到的程式碼不改**：最小改動優先，不為了符合這份去重排、改名、改格式。
- repo 既有的寫法很明顯、又跟這份差很多時，由 bb-plan 問使用者要照哪一種，決定寫進 issue；issue 有寫就照 issue。
- 這份管「怎麼組織與表達」。排版與命名的大小寫（縮排、引號、camelCase／snake_case）照那個語言的標準與 repo 的 formatter。
- 例子多用 JavaScript；其他語言用對應的寫法。

## 規則

1. **分層，依賴只往下**：進入點（endpoint、CLI、main）只接線和接錯誤；流程編排放一層；可重用的工具放一層，無狀態、不 import 上層。
   小專案可以放同一個檔案，用第 9 條的分段隔開。——agent 看位置就知道職責，改一層不會牽動另一層。
2. **資料流看得見**：輸入從參數來、輸出用回傳值給；不偷改傳進來的資料，不靠隱藏的全域狀態。
   兩個以上的參數、或有選填參數，用具名參數：JS `getUsers({ limit = 20 } = {})`、Python `def get_users(*, limit=20)`。
3. **邊界擋、裡面信任**：外部進來的資料（使用者輸入、API、檔案、DB、LLM 輸出）在進入點驗證——guard clause 先擋、早點 return，
   每種失敗一句明確的訊息。通過驗證之後的內部程式碼不重複防呆。
4. **錯誤要大聲、帶位置**：log 寫 `<module>.<function>: start`，錯誤寫 `<module>.<function>.error: <訊息>`；
   try/catch 放在最外層的進入點，裡面直接丟錯。不吞錯；真的要 fallback（回空值、用預設值）時，寫一行註解說為什麼。
   ——看到 log 就 grep 得到那一行。
5. **名字 grep 得到**：名字把意思說完整（`getPrivateKeyFromPublicKey`）；同一個概念全專案用同一個字；
   不用字串拼出函式或欄位名稱（`obj['get' + type]`）；自己的模組整個 import 成一個名稱，呼叫時帶上它（`cache.get()`）。
6. **同一種東西用同一個骨架**：例如每個 endpoint 都是「log start → 檢查輸入 → 編號步驟 → 回應 → catch」。
   ——agent 看過一個就懂全部。
7. **直白、少一層**：控制流程保持扁平；用語言最直接的寫法（`for`、`switch`、`?.`、`??`）；
   只有一個地方用到的邏輯不抽 helper；不為了「以後可能用到」加抽象、class、設定。——每多一層間接，就要多開一個檔案才看得懂。
8. **常數和限制只定義一次**：magic number／字串抽成 `UPPER_SNAKE_CASE` 常數，放在第一個用到它的地方附近；
   同一個限制（長度上限、TTL、重試次數）全專案只有一個定義。
9. **檔案自己說明自己**：檔頭一行寫路徑與用途（`// lib/cache.js: in-memory cache with TTL`；Python 用 module docstring）；
   import 集中在最上面；長檔案用框線註解分段；對外公開的東西集中在一處（例如 JS 檔尾的 `module.exports`）。
   ```js
   /**********
   * Helpers *
   ***********/
   ```
10. **註解短，只寫 code 看不出來的**：一行為主，寫為什麼、限制、例子（`// e.g. '20251027'`），不重述 code、不寫長篇說明。
    - 長一點的函式用編號步驟註解：`// 1. derive private key`、`// 2. fetch cache`。
    - 公開函式用語言標準的文件註解（JSDoc、docstring），簡短寫用途、參數、回傳值，加一行 `Example:`。
    - HTTP endpoint 上方寫 `// POST /users/create`、`// Body: { email }`、`// Response: { user }`。
