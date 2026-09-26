#!/usr/bin/env python3
"""bombolt 的安全守門（PreToolUse hook）。

規則分兩種生效範圍：
  A. 任何地方（只要這個資料夾的 git config 設了 github.user）：
     直接呼叫 `gh` 會被擋下，要改用 `bb-gh`——否則 gh 會靜默地用 active 帳號，
     在公司／個人雙帳號的機器上就是用錯帳號開 issue、發 PR。沒設 github.user 就完全不介入。
  B. 只在「bombolt 建的 worktree」裡（worktree 的 git dir 有 bombolt.json）：下面 1–6。
     使用者自己平常的 session 不受 B 影響。

實作 session 被設計成「完全自主」，所以真正危險的動作不能只靠 prompt 自律，
要在這裡硬擋。擋下來時 Claude 會看到理由，然後換一個做法或停下來問人。

⚠️ 這是一道安全網，不是沙箱：它比對的是指令文字，刻意繞過（例如把指令藏在腳本檔裡）
擋不住。它防的是「agent 順手做了不該做的事」，不是惡意行為。

擋的東西（預設 ＋ 專案 .claude/bombolt.md 的 protected_branches / deny_commands / secret_paths）：
  1. git push 到自己以外的 branch、force push、刪遠端 branch
  2. gh pr merge / gh repo delete 等不可逆的 GitHub 操作
  3. 碰正式環境：kubectl、helm、terraform apply、vercel --prod 等
  4. 對外發訊息：Slack webhook、LINE、寄信服務的 API
  5. rm -r 到 worktree 與暫存目錄以外的地方
  6. 讀寫秘密檔（.env* 以外的）：私鑰、雲端憑證、專案自訂的秘密檔
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bb_lib  # noqa: E402

DEFAULT_PROTECTED = ["main", "master", "prod", "production", "release"]

DEFAULT_DENY_COMMANDS = [
    r"\bgh\s+pr\s+merge\b",
    r"\bgh\s+repo\s+(delete|archive|rename|edit)\b",
    r"\bgh\s+release\s+(create|delete|upload|edit)\b",
    r"\bgh\s+secret\b",
    r"\bgh\s+variable\s+(set|delete)\b",
    r"\bgh\s+api\b.*(-X|--method)\s*(DELETE|PUT)\b",
    r"\bkubectl\b",
    r"\bhelm\b",
    r"\bterraform\s+(apply|destroy|import)\b",
    r"\bvercel\b[^|;&]*--prod\b",
    r"\bvercel\s+(promote|rollback|remove|rm|env\s+(add|rm|remove|update))\b",
    r"\b(fly|flyctl)\s+deploy\b",
    r"\bgcloud\b[^|;&]*\bdeploy\b",
    r"\baws\b[^|;&]*\b(deploy|delete|put|create|update)\b",
    r"hooks\.slack\.com",
    r"api\.line\.me",
    r"api\.resend\.com",
    r"api\.sendgrid\.com",
    r"api\.mailgun\.net",
    r"api\.twilio\.com",
    r"\bsendmail\b",
]

# .env* 刻意不在這裡：worktree 需要它們才能跑（使用者 2026-09-23 的裁決）。
DEFAULT_SECRET_GLOBS = [
    "*.pem", "*.key", "*.p12", "*.pfx",
    "id_rsa*", "id_ed25519*", "id_ecdsa*",
    "*/.ssh/*", "*/.aws/credentials", "*/.aws/config", "*/.netrc",
    "*/.config/gh/hosts.yml", "*/.docker/config.json", "*/.kube/config",
    "*/.claude/.credentials.json",
]

SAFE_RM_ROOTS = ["/tmp", "/private/tmp", "/var/folders", "/private/var/folders"]


def deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"bombolt 安全守門：{reason}",
        }
    }, ensure_ascii=False))
    sys.exit(0)


REDIR_ONLY = re.compile(r"^\d*(>>?|<|&>>?|>&|<&)$")          # `>`、`2>`、`&>` 這種：目標在下一個 token
REDIR_ATTACHED = re.compile(r"^\d*(>>?|<|&>>?|>&|<&)\S+$")   # `2>&1`、`>out.log`、`2>/dev/null`


def strip_redirections(tokens: List[str]) -> List[str]:
    """拿掉 shell 重導向（`2>&1`、`> out.log`…），不然它們會被當成指令的參數（例如被誤認成 push 的 refspec）。"""
    out: List[str] = []
    skip_next = False
    for t in tokens:
        if skip_next:
            skip_next = False
            continue
        if REDIR_ONLY.match(t):
            skip_next = True
            continue
        if REDIR_ATTACHED.match(t):
            continue
        out.append(t)
    return out


# 一段 shell 的最小單位：引號字串、跳脫字元、分隔符號、其他字。用來在「引號外」切段（見 split_segments 的 quote_aware）
SHELL_PIECE = re.compile(r"""'[^']*'|"(?:\\.|[^"\\])*"|\\.|&&|\|\||[;|\n]|[^'"\\;|&\n]+|.""", re.S)
SEPARATORS = ("&&", "||", ";", "|", "\n")


def split_outside_quotes(command: str) -> List[str]:
    """用 && || ; | 換行 切，但引號裡的不算（例如 `grep "a\\|gh b"` 的 pattern）。"""
    parts, buf = [], ""
    for piece in SHELL_PIECE.findall(command):
        if piece in SEPARATORS:
            parts.append(buf)
            buf = ""
        else:
            buf += piece
    parts.append(buf)
    return parts


def split_segments(command: str, *, quote_aware: bool = False) -> List[List[str]]:
    """把一行 shell 切成多段（&& || ; | 換行），每段再 shlex 成 tokens。解析失敗就退回空白切。

    quote_aware=False（B 類規則）：不管引號直接切——寧可誤擋，也要切出 `bash -c "cd x && git push …"` 裡的指令。
    quote_aware=True（A 類的 gh 檢查）：引號裡的不算，grep 的 pattern 不會被當成 gh 指令。
    """
    parts = split_outside_quotes(command) if quote_aware else re.split(r"&&|\|\||;|\||\n", command)
    out = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        try:
            tokens = shlex.split(part)
        except ValueError:
            tokens = part.split()
        tokens = strip_redirections(tokens)
        # 去掉前面的環境變數設定（FOO=bar cmd）與 sudo / command / env
        while tokens and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]) or tokens[0] in ("sudo", "command", "env", "exec", "time", "nohup")):
            tokens = tokens[1:]
        if tokens:
            out.append(tokens)
    return out


def check_git_push(tokens: List[str], own_branch: str, protected: List[str]) -> Optional[str]:
    # tokens 形如 ["git", (-C x)..., "push", ...]
    try:
        idx = tokens.index("push")
    except ValueError:
        return None
    args = tokens[idx + 1:]
    positional = []
    for a in args:
        if a in ("-f", "--force", "--force-with-lease", "--force-if-includes", "--mirror", "--all", "--tags", "--prune") \
                or a.startswith("--force-with-lease=") or a.startswith("--force"):
            return f"不允許 `git push {a}`（force push / 大範圍 push）。只能一般 push 自己的 branch `{own_branch}`。"
        if a in ("-d", "--delete"):
            return "不允許刪除遠端 branch。"
        if a.startswith("-"):
            continue
        positional.append(a)
    refspecs = positional[1:]  # 第一個是 remote
    for ref in refspecs:
        if ref.startswith("+"):
            return f"不允許 `+{ref[1:]}`（等同 force push）。"
        if ref.startswith(":"):
            return "不允許刪除遠端 branch（`:branch` 形式）。"
        src, _, dst = ref.partition(":")
        target = (dst or src).replace("refs/heads/", "")
        if target == "HEAD":
            target = own_branch
        if target != own_branch:
            extra = "（它是受保護的 branch）" if target in protected else ""
            return f"只能 push 到這個 worktree 自己的 branch `{own_branch}`，不能 push 到 `{target}`{extra}。"
    return None


def resolve(path_str: str, cwd: str) -> str:
    p = os.path.expanduser(path_str)
    if not os.path.isabs(p):
        p = os.path.join(cwd, p)
    return os.path.normpath(p)


def within(path: str, root: str) -> bool:
    path = os.path.realpath(path) if os.path.exists(path) else path
    root_r = os.path.realpath(root)
    return path == root_r or path.startswith(root_r.rstrip("/") + "/") or \
        path == root or path.startswith(root.rstrip("/") + "/")


def check_rm(tokens: List[str], cwd: str, worktree_root: str, repo_root: str) -> Optional[str]:
    if os.path.basename(tokens[0]) != "rm":
        return None
    flags = [t for t in tokens[1:] if t.startswith("-")]
    recursive = any(("r" in f or "R" in f) and not f.startswith("--") for f in flags) or "--recursive" in flags
    if not recursive:
        return None
    targets = [t for t in tokens[1:] if not t.startswith("-")]
    for t in targets:
        if t in ("/", "~", "*", ".", "..") or t.startswith("$"):
            return f"不允許 `rm -r {t}`。"
        full = resolve(t, cwd)
        # 包住主 checkout 或這個 worktree 本身的目錄，不管在哪裡都不准刪
        if within(repo_root, full) or within(worktree_root, full):
            return f"`rm -r {t}` 會刪到 repo 或這個 worktree 本身。"
        if within(full, worktree_root):
            continue
        if within(full, repo_root):
            return f"`rm -r` 不能刪主 checkout 裡的東西（`{full}`），只能刪這個 worktree 裡的。"
        if any(within(full, r) for r in SAFE_RM_ROOTS):
            continue
        return f"`rm -r` 只能刪 worktree（{worktree_root}）或暫存目錄裡的東西，不能刪 `{full}`。"
    return None


def matches_secret(path_str: str, cwd: str, globs: List[str], repo_globs: List[str],
                   roots: Optional[List[str]] = None) -> Optional[str]:
    """回傳命中的規則；沒命中回 None。

    - 專案設定的 secret_paths **先比對**，而且對 .env* 一樣有效（例如擋 backend/.env.prod）。
      pattern 含 `/` → 比對「相對於 repo（或 worktree）根目錄」的路徑，例如 `/.env.local` 只擋根目錄那個、
      `backend/.env.prod` 只擋那一個；不含 `/` → 比對任何一層的檔名，例如 `*-log.txt`。
    - 內建規則（私鑰、雲端憑證）對 .env* 放行：worktree 需要 .env 才能跑。
    """
    full = resolve(path_str, cwd)
    name = os.path.basename(full)
    real_full = os.path.realpath(full)
    rels = [os.path.relpath(real_full, os.path.realpath(r)) for r in (roots or [])
            if r and within(real_full, os.path.realpath(r))]
    for g in repo_globs:
        if "/" in g.strip("/") or g.startswith("/"):
            pat = g.lstrip("/")
            if any(fnmatch.fnmatch(rel, pat) for rel in rels):
                return g
        elif fnmatch.fnmatch(name, g):
            return g
    if name.startswith(".env"):
        return None
    for g in globs:
        if fnmatch.fnmatch(name, g) or fnmatch.fnmatch(full, g):
            return g
    return None


def evaluate(payload: Dict[str, Any]) -> Optional[str]:
    """回傳擋下的理由；放行就回 None。拆成獨立函式方便測試。"""
    cwd = payload.get("cwd") or os.getcwd()

    # A. 雙帳號保護：任何地方都生效
    if payload.get("tool_name") == "Bash":
        command = (payload.get("tool_input") or {}).get("command", "") or ""
        if any(os.path.basename(t[0]) == "gh" for t in split_segments(command, quote_aware=True)):
            wanted = bb_lib.configured_gh_user(cwd)
            if wanted:
                return (f"這個資料夾用 git config github.user 指定了 GitHub 帳號 `{wanted}`，"
                        f"直接呼叫 `gh` 會用到 gh 的 active 帳號（可能是另一個帳號）。請把 `gh` 換成 `bb-gh`，其他參數不變。")

    # B. 以下只在 bombolt worktree 裡生效
    meta = bb_lib.read_meta(cwd)
    if not meta or meta.get("tool") != "bombolt":
        return None  # 不在 bombolt worktree 裡：完全不管

    repo = Path(meta.get("repo", ""))
    cfg = bb_lib.load_worktree_config(cwd, repo) or {}
    protected = list(dict.fromkeys(DEFAULT_PROTECTED + cfg.get("protected_branches", []) + [meta.get("base", "")]))
    protected = [b for b in protected if b]
    own_branch = meta.get("branch", "")
    worktree_root = meta.get("path") or cwd
    repo_secret_globs = cfg.get("secret_paths", [])

    tool = payload.get("tool_name", "")
    tin = payload.get("tool_input", {}) or {}

    if tool in ("Read", "Edit", "Write", "NotebookEdit"):
        fp = tin.get("file_path") or tin.get("notebook_path") or ""
        if fp:
            hit = matches_secret(fp, cwd, DEFAULT_SECRET_GLOBS, repo_secret_globs, [worktree_root, str(repo)])
            if hit:
                return f"`{fp}` 符合秘密檔規則 `{hit}`，不允許讀寫。需要其中的值請停下來問使用者。"
        return None

    if tool != "Bash":
        return None

    command = tin.get("command", "") or ""
    for pattern in DEFAULT_DENY_COMMANDS + cfg.get("deny_commands", []):
        try:
            if re.search(pattern, command):
                return f"指令符合禁止規則 `{pattern}`（正式環境 / 對外發訊息 / 不可逆的 GitHub 操作）。這類動作要由使用者自己做。"
        except re.error:
            continue

    for tokens in split_segments(command):
        head = os.path.basename(tokens[0])
        if head == "git" and "push" in tokens:
            reason = check_git_push(tokens, own_branch, protected)
            if reason:
                return reason
        reason = check_rm(tokens, cwd, worktree_root, str(repo))
        if reason:
            return reason
        for t in tokens[1:]:
            if t.startswith("-") or "/" not in t and "." not in t:
                continue
            hit = matches_secret(t, cwd, DEFAULT_SECRET_GLOBS, repo_secret_globs, [worktree_root, str(repo)])
            if hit:
                return f"指令碰到秘密檔 `{t}`（規則 `{hit}`）。需要其中的值請停下來問使用者。"
    return None


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)
    try:
        reason = evaluate(payload)
    except Exception as e:  # 守門本身壞掉時不能讓整個 session 卡死，但要留下痕跡
        print(f"bombolt guard error（已放行）：{e!r}", file=sys.stderr)
        sys.exit(1)
    if reason:
        deny(reason)
    sys.exit(0)


if __name__ == "__main__":
    main()
