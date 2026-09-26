<!--
「🔍 逐段改動」的寫法。它放在 PR 的「📝 程式碼改動範圍」、diffstat 的下面，是高層次的 Files changed：
讀的人看完這一節就不用打開 Files changed，想看細節時才點段落的行號跳過去。
檔案順序、段落、行號、連結、編號與版面由 scripts/bb_walkthrough.py 處理；這份只管每一段的內容怎麼寫。
bb-work 第 7 步、bb-fix 第 4 步都會寫；bb-reviewer 會逐段對照 code。
-->

# 逐段改動怎麼寫

## 在 PR 上長什麼樣子

- 每個檔案一個**預設收合**的折疊區，標題列是「檔名、行數、一句為什麼改」，全部收合時就是一張目錄。
- 展開之後，每一段是「位置＋連到 Files changed 的行號」，下面是精簡的 code（`+`／`-` 有 diff 的綠紅底色），
  需要說明的行在開頭編上 `[1]` `[2]`，說明放在區塊下面的引用框裡。
- 長的段落切成小塊（通常一個函式一塊），每塊前面一行小標題，說明緊接在那一塊下面。

## 流程

1. **產生骨架**（code 每改一次就重跑）：
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_walkthrough.py" skeleton --base origin/<base_branch> \
     --out <artifacts>/walkthrough.skeleton.md [--pr-url <PR 網址>]
   ```
   PR 還沒建就不給 `--pr-url`，連結會先用 `{{PR_URL}}` 佔位，發 PR 之後再用 `link` 補上。
2. **照骨架寫 `<artifacts>/walkthrough.md`**：
   - 骨架裡的折疊區、段落標題和連結全部保留，不能漏，也不能改行號。
   - `{{…}}` 全部填掉，`{{PR_URL}}` 除外。
   - 每段 ` ```diff ` 裡的原始 code，改寫成下面「每一段的寫法」。
   - code 改了之後：重跑骨架，只改有變的段落，行號和連結一律以新的骨架為準。
3. **轉成 PR 上的版面**（同時做檢查，通過才會寫出檔案）：
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bb_walkthrough.py" render --base origin/<base_branch> \
     --file <artifacts>/walkthrough.md --out <artifacts>/walkthrough.rendered.md
   ```
   回 `problems` 的話，照 `missing`（漏掉的段落）、`stale`（行號過時的連結）、`placeholders`（沒填的 `{{…}}`）、
   `unsafe`（會被 GitHub 誤判的寫法）修到 `ok`。`too_wide` 只是提醒：那幾行 code 在 PR 上要左右捲動，能精簡就精簡。
   **PR 內文用 `walkthrough.rendered.md`；要修改一律改 `walkthrough.md` 再重跑 render**，不要直接改 rendered 檔。

## 每一段的寫法

在 `walkthrough.md` 裡，精簡的 code 後面用 `←` 接一句說明，`[1]` `[2]` 的編號和引用框由 `render` 產生：

```diff
- if (!lineUserId) continue
+ chatId = source.type === 'group' ? groupId : userId  ← 群組訊息改用 `groupId` 當 lock key
+ if (!chatId) continue  ← 取不到就跳過
```

- **每個邏輯步驟寫一行**：一個判斷、一個呼叫、一個 return、一個賦值，各一行。
  跨很多行的東西（物件、陣列、長字串、prompt、JSDoc）收成一行，例如 ``+ RULES = `<規則>…` ← 只根據檔案回答、註明出處``。
- **精簡 code**：保留識別字、條件和關鍵的值；拿掉型別、`await`、樣板和過長的參數，省略的部分用 `…` 代替。
  讀的人要能從這一行認出它對應到哪一行 code。一行盡量在 75 個半形字以內（`render` 會用 `too_wide` 提醒）。
- **說明**：`←` 後面用一句繁體中文，說這一行在做什麼、為什麼。一看 code 就懂的行不用寫說明，
  但分支、提早 return、錯誤處理、副作用（寫 DB、打 API、送訊息）一定要寫。
- 巢狀用縮排表示，縮排放在開頭的 `+`、`-` 加一個空格之後。
- 新增的行用 `+`，刪掉的行用 `-`。改了一行的話，寫一行 `-` 舊的、一行 `+` 新的；
  差別很小時可以只寫 `+` 新的，說明寫「原本是…」。
- 需要定位時，可以加一行沒改的 code，開頭用空格，說明寫「（沒改）」。
  骨架在同一段兩處改動之間放了 ` …`，要保留，表示中間還有沒改的 code。
- **不寫**：空行、只有括號的行、log，以及註解（它的內容已經寫進說明了）。
  註解掉的 code（例如預留的擴充點）寫一行帶過：`+ // case 'x' …（註解掉） ← 預留的擴充點，沒有啟用`。
- 偏離 issue 的地方，以及「👀 請你重點看」提到的地方，在說明後面加 `⚠️ 見重點看第 n 點`。
- 內容本身有 ```` ``` ```` 的話，外層改用更長的 fence（例如 ```` ```` ````）。

### 長的段落切成小塊

一段超過大約 10 行，或包含好幾個函式時，切成小塊：每塊一行粗體小標題，接著一個自己的 ` ```diff ` 區塊。
通常一個函式一塊，小標題寫「函式名＋它在做什麼」，函式那一行就不用再寫 `←` 說明。
一個函式本身很長時，照它的步驟再切（例如「準備」「每一輪呼叫模型」「執行工具」）。編號每塊重新從 `[1]` 開始。

````markdown
##### 新增 PART 4：群組　[L545–670](…)

**`handleGroupEvent()`**　群組訊息的入口：只有被 @ 才回

```diff
+ async function handleGroupEvent(bot, event)
+   if (selfMentions.length === 0) return  ← 沒有 @我（`@All` 沒有 `isSelf`）
+   if (group.enabled === false) return  ← 停用的群組靜默
```

**`sendGroupReply()`**　先用免費的 reply，不行再用付費的 push

```diff
+ async function sendGroupReply({ bot, event, text })
+   …
```
````

### 說明是一般的 markdown

`render` 之後，說明、小標題、位置和檔案的一句話都會變成 PR 上的一般文字，GitHub 會把這幾種寫法當成別的東西：

- `@名字` 會變成 @ 提及，**可能真的通知到別人** → 包成 inline code：`` `@All` ``。
- `#數字` 會變成 issue／PR 的連結 → 改寫成「第 n 點」，或包成 inline code。
- `<名稱>` 會被當成 HTML 標籤吃掉 → 包成 inline code：`` `<botId>` ``。

識別字、常數、值一律包成 inline code，比較好認，也不會踩到上面這些。`render` 會把漏掉的列在 `unsafe`。

## 段落標題的位置

`{{位置}}` 寫這一段在檔案裡的哪裡，例如「`handleAgentEvent()` 開頭」「常數區」「`module.exports`」「新增 PART 4：群組」。
骨架附的 git 提示只能參考：git 抓的是這一段上方最近的一行，常常不準。

## 折疊區標題的一句話

寫這個檔案在這次改動裡的角色，不要重複下面各段的內容。可以用 `` `x` ``，`render` 會轉成 GitHub 看得懂的寫法。

## 不是程式碼的檔案

- 文件（Markdown 之類）：每段寫一兩行 `+`，說加了或改了什麼內容，不用照抄原文，也不用 `←`。
- 「其他檔案」裡的 lock 檔、自動產生的檔案、binary：一句話說它為什麼會變。

## 太長的時候

PR 內文的上限大約是 65000 字，`render` 會回報 `chars`。整份 PR 內文可能超過時，先把文件類的檔案縮成每段一行，
再把說明寫得更精簡。
