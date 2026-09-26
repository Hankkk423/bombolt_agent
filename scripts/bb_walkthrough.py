#!/usr/bin/env python3
"""PR 內文「🔍 逐段改動」的固定步驟：產生骨架、檢查、轉成 PR 上的版面、補上 PR 網址。

「逐段改動」是高層次的 Files changed：照 Files changed 的順序，每個檔案一個（預設收合的）折疊區，
裡面逐段列出精簡的 code，每段的標題連到 Files changed 的那幾行。
agent 用 `code ← 說明` 的寫法寫（寫法見 skills/bb-work/walkthrough.md）；檔案、段落、行號、連結、
編號、對齊與版面都由這支腳本處理。

用法（在 bombolt worktree 裡執行）：
  bb_walkthrough.py skeleton --base origin/main --out <artifacts>/walkthrough.skeleton.md [--pr-url URL]
      → 寫出骨架。沒給 --pr-url 時連結先用 {{PR_URL}} 佔位（PR 還沒建）。
  bb_walkthrough.py check --base origin/main --file <artifacts>/walkthrough.md
      → 檢查漏掉的段落、行號過時的連結、沒填的 {{…}}、會被 GitHub 誤判的寫法（@名字、#數字、<標籤>）。
  bb_walkthrough.py render --base origin/main --file <artifacts>/walkthrough.md --out <artifacts>/walkthrough.rendered.md
      → check 通過才寫出：每段 ```diff 裡有 `← 說明` 的行編上 [1] [2]…、說明移到區塊下方的引用框。
  bb_walkthrough.py link --pr <PR 網址或編號>
      → 從 GitHub 讀回 PR 內文，把 {{PR_URL}} 換成 PR 網址後寫回（不會蓋掉 --attach 上傳的圖片網址）。

輸出一行 JSON。
"""

from __future__ import annotations

import argparse
import codecs
import hashlib
import html
import json
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bb_lib  # noqa: E402

PR_URL = "{{PR_URL}}"
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")
ANCHOR_RE = re.compile(r"#diff-([0-9a-f]{64})([RL]\d+)?")
PLACEHOLDER_RE = re.compile(r"\{\{(?!PR_URL\}\}).*?\}\}")
FENCE_RE = re.compile(r"^(`{3,})diff\s*$")
NOTE_MARK = "←"
LINE_WIDTH = 88  # PR 內文的 code 區塊不用左右捲動就看得到的寬度（半形字）

# 只寫一句話、不逐段說明的檔案：lock 檔與自動產生的檔案。
SUMMARY_ONLY_NAMES = {
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock", "bun.lockb",
    "Cargo.lock", "Gemfile.lock", "poetry.lock", "Pipfile.lock", "composer.lock", "go.sum", "uv.lock",
    "mix.lock", "Podfile.lock", "packages.lock.json",
}
SUMMARY_ONLY_SUFFIXES = (".min.js", ".min.css", ".map", ".snap")

# 說明離開 code 區塊之後就是一般的 markdown，這幾種寫法會被 GitHub 誤判：
UNSAFE_PATTERNS = [
    (re.compile(r"(?<![\w`/.])@[A-Za-z0-9][\w-]*"), "@名字會變成 @ 提及（可能通知到別人），請包成 inline code"),
    (re.compile(r"(?<![\w&/])#\d+\b"), "#數字會變成 issue／PR 連結，請改寫（例如「第 1 點」）或包成 inline code"),
    (re.compile(r"<(?!/?(?:details|summary|b|code|br|a|sub|sup|kbd)\b)[A-Za-z/][^>]*>"),
     "<…> 會被當成 HTML 標籤吃掉，請包成 inline code"),
]
CODE_SPAN_RE = re.compile(r"(`+)(?!`).*?(?<!`)\1(?!`)")


def out(**kw) -> None:
    print(json.dumps(kw, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 解析 git diff
# ---------------------------------------------------------------------------


def unquote_path(p: str) -> str:
    """git 對特殊字元的路徑會加雙引號並跳脫（core.quotepath=false 之後中文不會被跳脫）。"""
    if len(p) >= 2 and p[0] == p[-1] == '"':
        return codecs.escape_decode(p[1:-1].encode("utf-8"))[0].decode("utf-8")
    return p


def _strip_prefix(p: str) -> str:
    p = unquote_path(p)
    return p[2:] if p[:2] in ("a/", "b/") else p


def parse_diff(text: str) -> List[Dict[str, Any]]:
    files: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    hunk: Optional[Dict[str, Any]] = None
    for line in text.split("\n"):
        if line.startswith("diff --git "):
            cur = {"old": None, "new": None, "status": "modified", "binary": False, "hunks": []}
            m = re.match(r"^diff --git (\"?a/.*) (\"?b/.*)$", line)
            if m:
                cur["old"], cur["new"] = _strip_prefix(m.group(1)), _strip_prefix(m.group(2))
            files.append(cur)
            hunk = None
            continue
        if cur is None:
            continue
        m = HUNK_RE.match(line)
        if m:
            hunk = {"old_start": int(m.group(1)), "new_start": int(m.group(3)), "heading": m.group(5).strip(),
                    "lines": []}
            cur["hunks"].append(hunk)
            continue
        if hunk is not None:
            if line[:1] in (" ", "+", "-"):
                hunk["lines"].append((line[0], line[1:]))
            continue  # "\ No newline at end of file" 之類
        if line.startswith("new file mode"):
            cur["status"] = "added"
        elif line.startswith("deleted file mode"):
            cur["status"] = "deleted"
        elif line.startswith("rename from "):
            cur["old"], cur["status"] = unquote_path(line[len("rename from "):]), "renamed"
        elif line.startswith("rename to "):
            cur["new"] = unquote_path(line[len("rename to "):])
        elif line.startswith("Binary files ") or line == "GIT binary patch":
            cur["binary"] = True
        elif line.startswith("--- ") and line[4:] != "/dev/null":
            cur["old"] = _strip_prefix(line[4:])
        elif line.startswith("+++ ") and line[4:] != "/dev/null":
            cur["new"] = _strip_prefix(line[4:])
    for f in files:
        f["path"] = f["old"] if f["status"] == "deleted" else f["new"]
        f["added"] = sum(1 for h in f["hunks"] for k, _ in h["lines"] if k == "+")
        f["deleted"] = sum(1 for h in f["hunks"] for k, _ in h["lines"] if k == "-")
        for h in f["hunks"]:
            h.update(hunk_range(h))
    return files


def hunk_range(h: Dict[str, Any]) -> Dict[str, Any]:
    """這一段實際改動的範圍：有新增就用新檔的行號（Files changed 右邊，R），只有刪除就用舊檔的行號（左邊，L）。"""
    old_n, new_n = h["old_start"], h["new_start"]
    adds: List[int] = []
    dels: List[int] = []
    for kind, _ in h["lines"]:
        if kind == " ":
            old_n += 1
            new_n += 1
        elif kind == "+":
            adds.append(new_n)
            new_n += 1
        else:
            dels.append(old_n)
            old_n += 1
    nums, side = (adds, "R") if adds else (dels, "L")
    return {"side": side, "start": min(nums), "end": max(nums)}


def file_sort_key(path: str) -> Tuple[Tuple[int, str], ...]:
    """Files changed 的順序：同一層先資料夾、再檔案，各自照字母排。"""
    parts = path.split("/")
    return tuple([(0, d.lower()) for d in parts[:-1]] + [(1, parts[-1].lower())])


def is_summary_only(f: Dict[str, Any]) -> bool:
    name = f["path"].rsplit("/", 1)[-1]
    return (f["binary"] or not f["hunks"] or name in SUMMARY_ONLY_NAMES
            or name.endswith(SUMMARY_ONLY_SUFFIXES))


def load_files(base: str, cwd: Path) -> List[Dict[str, Any]]:
    text = bb_lib.git(["-c", "core.quotepath=false", "diff", "--no-color", "--no-ext-diff", "--no-textconv",
                       "-M", "--src-prefix=a/", "--dst-prefix=b/", f"{base}...HEAD"], cwd)
    return sorted(parse_diff(text), key=lambda f: file_sort_key(f["path"]))


# ---------------------------------------------------------------------------
# 骨架
# ---------------------------------------------------------------------------


def file_anchor(path: str) -> str:
    return "diff-" + hashlib.sha256(path.encode("utf-8")).hexdigest()


def hunk_anchor(path: str, h: Dict[str, Any]) -> str:
    return f"{file_anchor(path)}{h['side']}{h['start']}"


def hunk_label(h: Dict[str, Any]) -> str:
    span = f"L{h['start']}" if h["start"] == h["end"] else f"L{h['start']}–{h['end']}"
    return span if h["side"] == "R" else f"原 {span}（刪除）"


def fence_for(lines: List[str]) -> str:
    """內容本身有 ``` 時，外層的 fence 要更長才不會被提早關掉。"""
    longest = max([len(r) for ln in lines for r in re.findall(r"`+", ln)] or [0])
    return "`" * max(3, longest + 1)


def status_note(f: Dict[str, Any]) -> str:
    if f["status"] == "added":
        return "新檔，"
    if f["status"] == "deleted":
        return "刪除，"
    if f["status"] == "renamed":
        return f"改名自 `{f['old']}`，"
    return ""


def counts(f: Dict[str, Any]) -> str:
    return "binary" if f["binary"] else f"+{f['added']} −{f['deleted']}"


def render_skeleton(files: List[Dict[str, Any]], pr_url: str) -> str:
    changes = f"{pr_url}/changes"
    out_lines = ["### 🔍 逐段改動", "",
                 "照 Files changed 的順序，一個檔案一個折疊區，點檔名展開；點段落標題的行號會跳到 Files changed 的那幾行。", ""]
    others = []
    for f in files:
        if is_summary_only(f):
            others.append(f)
            continue
        path = f["path"]
        out_lines += ["<details>",
                      f"<summary><b><code>{html.escape(path)}</code></b>　{status_note(f)}{counts(f)}　"
                      "{{一句話：這個檔案為什麼改}}</summary>", "",
                      f"[在 Files changed 看整個檔案 ↗]({changes}#{file_anchor(path)})", ""]
        for h in f["hunks"]:
            hint = f"（git：{h['heading']}）" if h["heading"] else ""
            out_lines += [f"##### {{{{位置{hint}}}}}　[{hunk_label(h)}]({changes}#{hunk_anchor(path, h)})", ""]
            body: List[str] = []
            prev = None
            for kind, text in h["lines"]:
                if kind == " ":
                    prev = kind
                    continue
                if prev == " " and body:
                    body.append(" …")
                body.append(kind + text)
                prev = kind
            fence = fence_for(body)
            out_lines += [fence + "diff", *body, fence, ""]
        out_lines += ["</details>", ""]
    if others:
        out_lines += ["<details>", f"<summary><b>其他檔案</b>　{len(others)} 個：lock 檔、自動產生的檔案、binary、只改名</summary>", ""]
        for f in others:
            out_lines.append(f"- [`{f['path']}`]({changes}#{file_anchor(f['path'])})"
                             f"（{status_note(f)}{counts(f)}）：{{{{一句話}}}}")
        out_lines += ["", "</details>", ""]
    return "\n".join(out_lines)


def expected_anchors(files: List[Dict[str, Any]]) -> Dict[str, str]:
    """{錨點: 給人看的描述}：每個檔案一個，逐段說明的檔案再加每一段一個。"""
    exp: Dict[str, str] = {}
    for f in files:
        exp[file_anchor(f["path"])] = f"`{f['path']}`"
        if not is_summary_only(f):
            for h in f["hunks"]:
                exp[hunk_anchor(f["path"], h)] = f"`{f['path']}` {hunk_label(h)}"
    return exp


# ---------------------------------------------------------------------------
# 轉成 PR 上的版面
# ---------------------------------------------------------------------------


def display_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def render_block(lines: List[str]) -> Tuple[List[str], List[str]]:
    """一個 ```diff 區塊：有 `← 說明` 的行在 +/- 後面編上 [1] [2]…，其他行補同寬的空白，縮排才對得齊。

    回傳 (code 行, 說明)。區塊裡沒有任何說明就原樣回傳。
    """
    entries = []
    for ln in lines:
        prefix, rest = (ln[:1], ln[1:]) if ln[:1] in ("+", "-", " ") else ("", ln)
        note = ""
        if NOTE_MARK in rest:
            rest, note = rest.split(NOTE_MARK, 1)
            note = note.strip()
        code = rest[1:] if rest.startswith(" ") else rest
        entries.append((prefix, code.rstrip(), note))
    notes = [n for _, _, n in entries if n]
    slot = len(f"[{len(notes)}]") if notes else 0
    result, n = [], 0
    for prefix, code, note in entries:
        if not prefix and not code:
            result.append("")
        elif code.strip() == "…":
            result.append(" …")
        elif note:
            n += 1
            result.append(f"{prefix} {f'[{n}]'.ljust(slot)} {code}")
        else:
            result.append(f"{prefix} {' ' * slot} {code}" if slot else f"{prefix} {code}")
    return result, notes


def summary_to_html(line: str) -> str:
    """<summary> 裡面 GitHub 不處理 markdown：把 `x` 換成 <code>x</code>。"""
    def conv(m: "re.Match[str]") -> str:
        inner = m.group(2)
        return m.group(1) + re.sub(r"`([^`]+)`", lambda c: f"<code>{html.escape(c.group(1))}</code>", inner) + m.group(3)
    return re.sub(r"(<summary>)(.*?)(</summary>)", conv, line)


def render_text(text: str) -> Tuple[str, List[str]]:
    """回傳 (PR 上的版面, 太寬的 code 行)。"""
    lines = text.split("\n")
    result: List[str] = []
    too_wide: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = FENCE_RE.match(line)
        if not m:
            result.append(summary_to_html(line) if "<summary>" in line else line)
            i += 1
            continue
        block: List[str] = []
        i += 1
        while i < len(lines) and lines[i] != m.group(1):
            block.append(lines[i])
            i += 1
        i += 1  # 結尾的 fence
        code, notes = render_block(block)
        too_wide += [c.strip() for c in code if display_width(c) > LINE_WIDTH]
        result += [line, *code, m.group(1)]
        if notes:
            result.append("")
            result += [f"> **[{k}]** {n}" for k, n in enumerate(notes, 1)]
            if i < len(lines) and lines[i].strip():
                result.append("")  # 引用框後面一定要空一行，不然下一行會被併進引用框
    return "\n".join(result), too_wide


def unsafe_snippets(rendered: str) -> List[str]:
    """PR 上會被 GitHub 誤判的寫法（code 區塊、inline code、連結網址裡的不算）。"""
    prose = re.sub(r"^(`{3,}).*?^\1\s*$", "", rendered, flags=re.S | re.M)
    prose = re.sub(r"<code>.*?</code>", "", prose)
    prose = CODE_SPAN_RE.sub("", prose)
    prose = re.sub(r"\]\([^)]*\)", "]", prose)
    found = []
    for pattern, why in UNSAFE_PATTERNS:
        for m in pattern.finditer(prose):
            found.append(f"{m.group(0)}：{why}")
    return sorted(set(found))


def problems_of(text: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    exp = expected_anchors(files)
    found = {f"diff-{m.group(1)}{m.group(2) or ''}" for m in ANCHOR_RE.finditer(text)}
    rendered, too_wide = render_text(text)
    return {
        "missing": [desc for a, desc in exp.items() if a not in found],
        "stale": sorted(a for a in found if a not in exp),
        "placeholders": sorted(set(PLACEHOLDER_RE.findall(text))),
        "unsafe": unsafe_snippets(rendered),
        "too_wide": too_wide,
        "rendered": rendered,
    }


HINT = ("missing＝diff 裡有、說明裡沒有的檔案或段落；stale＝連結的行號已經不在目前的 diff 裡（重跑 skeleton 對照新的行號）；"
        "placeholders＝還沒填的 {{…}}；unsafe＝會被 GitHub 誤判的寫法。too_wide 只是提醒：那幾行 code 太長，"
        "在 PR 上要左右捲動，能精簡就精簡。")


# ---------------------------------------------------------------------------
# 子指令
# ---------------------------------------------------------------------------


def _load(args) -> Tuple[List[Dict[str, Any]], str]:
    try:
        files = load_files(args.base, Path.cwd())
    except RuntimeError as e:
        out(status="error", message=str(e))
        sys.exit(1)
    try:
        text = Path(args.file).read_text(encoding="utf-8")
    except OSError as e:
        out(status="error", message=f"讀不到 {args.file}：{e}")
        sys.exit(1)
    return files, text


def cmd_skeleton(args) -> None:
    try:
        files = load_files(args.base, Path.cwd())
    except RuntimeError as e:
        out(status="error", message=str(e))
        sys.exit(1)
    text = render_skeleton(files, (args.pr_url or PR_URL).rstrip("/"))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text, encoding="utf-8")
    detailed = [f for f in files if not is_summary_only(f)]
    out(status="ok", out=args.out, files=len(files), detailed_files=len(detailed),
        hunks=sum(len(f["hunks"]) for f in detailed), chars=len(text))


def cmd_check(args, write_to: str = "") -> None:
    files, text = _load(args)
    p = problems_of(text, files)
    ok = not (p["missing"] or p["stale"] or p["placeholders"] or p["unsafe"])
    if ok and write_to:
        Path(write_to).write_text(p["rendered"], encoding="utf-8")
    out(status="ok" if ok else "problems", missing=p["missing"], stale=p["stale"],
        placeholders=p["placeholders"], unsafe=p["unsafe"], too_wide=p["too_wide"],
        chars=len(p["rendered"]), **({"out": write_to} if ok and write_to else {}),
        hint="" if ok and not p["too_wide"] else HINT)
    if not ok:
        sys.exit(1)


def cmd_link(args) -> None:
    cwd = Path.cwd()
    try:
        acct = bb_lib.gh_account_env(cwd)
    except bb_lib.GhAccountError as e:
        out(status="error", message=str(e))
        sys.exit(1)

    def gh(*a: str) -> subprocess.CompletedProcess:
        return subprocess.run([acct["gh"], *a], cwd=str(cwd), capture_output=True, text=True, env=acct["env"])

    p = gh("pr", "view", args.pr, "--json", "body,url")
    if p.returncode != 0:
        out(status="error", message=p.stderr.strip() or "gh pr view 失敗")
        sys.exit(1)
    pr = json.loads(p.stdout)
    url = pr["url"].rstrip("/")
    body = pr.get("body") or ""
    n = body.count(PR_URL)
    if n == 0:
        out(status="ok", url=url, replaced=0)
        return
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as tf:
        tf.write(body.replace(PR_URL, url))
    try:
        p = gh("pr", "edit", args.pr, "--body-file", tf.name)
    finally:
        Path(tf.name).unlink(missing_ok=True)
    if p.returncode != 0:
        out(status="error", message=p.stderr.strip() or "gh pr edit 失敗")
        sys.exit(1)
    out(status="ok", url=url, replaced=n)


def main() -> None:
    ap = argparse.ArgumentParser(description="PR 內文「逐段改動」的骨架、檢查、版面與連結")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("skeleton")
    s.add_argument("--base", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--pr-url", default="")
    c = sub.add_parser("check")
    c.add_argument("--base", required=True)
    c.add_argument("--file", required=True)
    r = sub.add_parser("render")
    r.add_argument("--base", required=True)
    r.add_argument("--file", required=True)
    r.add_argument("--out", required=True)
    link = sub.add_parser("link")
    link.add_argument("--pr", required=True)
    args = ap.parse_args()
    if args.cmd == "skeleton":
        cmd_skeleton(args)
    elif args.cmd == "check":
        cmd_check(args)
    elif args.cmd == "render":
        cmd_check(args, write_to=args.out)
    else:
        cmd_link(args)


if __name__ == "__main__":
    main()
