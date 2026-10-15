#!/usr/bin/env python3
"""eval 用的假 gh：有狀態的 GitHub 替身，狀態存在一個 JSON 檔，origin 是本機的 bare repo。

用法（runner 產生的 shim 會這樣呼叫）：
    python3 fake_gh.py --state <state.json> <gh 的參數...>

只模擬 bombolt 的 skill 與 scripts/ 用到的指令，加上 agent 常順手打的。輸出形狀照真的 gh
（JSON 欄位名稱、非 TTY 的文字格式、錯誤訊息），因為 bombolt 的腳本會 parse 它。
不支援的指令、旗標、欄位、jq 運算式：照 gh 的樣子報錯，並在 calls.jsonl 標 "unsupported": true，
讓 runner 知道「假 gh 不夠像」，而不是誤判成 pipeline 的問題。

⚠️ 錯誤訊息不可以出現「假的」相關字樣：agent 看到會改變行為。

另外提供 python API 給 runner 種資料：new_state、load_state、save_state、add_issue、create_pr、
add_comment、add_review、add_review_thread、pr_head_oid。
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

VERSION = "2.99.0"
HOST = "github.com"


# ---------------------------------------------------------------------------
# 錯誤與共用
# ---------------------------------------------------------------------------

class GhError(Exception):
    def __init__(self, msg: str, code: int = 1, unsupported: bool = False, stdout: str = ""):
        super().__init__(msg)
        self.msg, self.code, self.unsupported, self.stdout = msg, code, unsupported, stdout


def unsupported(msg: str) -> GhError:
    return GhError(msg, unsupported=True)


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


class Ctx:
    def __init__(self, state_path: Path, cwd: str):
        self.state_path, self.cwd = state_path, cwd
        self.out: List[str] = []
        self.err: List[str] = []
        self.dirty = False
        self.unsupported = False

    def print(self, s: str = "") -> None:
        self.out.append(s if s.endswith("\n") else s + "\n")

    def eprint(self, s: str) -> None:
        self.err.append(s if s.endswith("\n") else s + "\n")


# ---------------------------------------------------------------------------
# state：讀寫與 python API
# ---------------------------------------------------------------------------

def new_state(owner: str, repo: str, login: str, origin_path: str, next_number: int = 1) -> Dict[str, Any]:
    return {"owner": owner, "repo": repo, "login": login, "origin": str(origin_path), "default_branch": "main",
            "next_number": next_number, "next_id": 9000, "labels": {}, "issues": {}, "prs": {}}


def load_state(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_state(path: str | Path, state: Dict[str, Any]) -> None:
    p = Path(path)
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


@contextlib.contextmanager
def locked(path: str | Path) -> Iterator[None]:
    """agent 和 subagent 可能同時呼叫 gh：整個 load→改→save 包在檔案鎖裡。"""
    with open(str(path) + ".lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _next_id(state: Dict[str, Any]) -> int:
    i = state["next_id"]
    state["next_id"] = i + 1
    return i


def _next_number(state: Dict[str, Any]) -> int:
    n = state["next_number"]
    state["next_number"] = n + 1
    return n


def _base_url(state: Dict[str, Any]) -> str:
    return f"https://{HOST}/{state['owner']}/{state['repo']}"


def _ensure_label(state: Dict[str, Any], name: str) -> None:
    state["labels"].setdefault(name, {"color": "ededed", "description": ""})


def add_issue(state: Dict[str, Any], title: str, body: str, labels=("bombolt",), author: Optional[str] = None) -> int:
    n = _next_number(state)
    for l in labels:
        _ensure_label(state, l)
    t = now()
    state["issues"][str(n)] = {"number": n, "title": title, "body": body, "state": "OPEN", "labels": list(labels),
                               "assignees": [], "author": author or state["login"], "createdAt": t, "updatedAt": t,
                               "comments": []}
    return n


def create_pr(state: Dict[str, Any], title: str, body: str, head: str, base: str, author: Optional[str] = None,
              draft: bool = False) -> int:
    n = _next_number(state)
    t = now()
    state["prs"][str(n)] = {"number": n, "title": title, "body": body, "state": "OPEN", "isDraft": bool(draft),
                            "headRefName": head, "baseRefName": base, "author": author or state["login"],
                            "createdAt": t, "updatedAt": t, "comments": [], "reviews": [], "threads": [],
                            "attachments": [], "labels": [], "assignees": [], "edits": 0,
                            "lastHeadRefOid": _rev(state, head) or ""}
    return n


def _item(state: Dict[str, Any], number: int) -> Tuple[str, Dict[str, Any]]:
    key = str(number)
    if key in state["issues"]:
        return "issue", state["issues"][key]
    if key in state["prs"]:
        return "pr", state["prs"][key]
    raise KeyError(number)


def add_comment(state: Dict[str, Any], number: int, body: str, author: Optional[str] = None) -> Dict[str, Any]:
    kind, item = _item(state, number)
    cid = _next_id(state)
    t = now()
    path = "issues" if kind == "issue" else "pull"
    c = {"id": cid, "url": f"{_base_url(state)}/{path}/{number}#issuecomment-{cid}", "body": body,
         "author": author or state["login"], "createdAt": t, "updatedAt": t}
    item["comments"].append(c)
    item["updatedAt"] = t
    return c


def add_review(state: Dict[str, Any], pr: int, author: str, review_state: str, body: str) -> Dict[str, Any]:
    p = state["prs"][str(pr)]
    rid = _next_id(state)
    r = {"id": rid, "author": author, "state": review_state, "body": body, "submittedAt": now(),
         "commit": pr_head_oid(state, pr)}
    p["reviews"].append(r)
    return r


def add_review_thread(state: Dict[str, Any], pr: int, path: str, line: int, author: str, body: str) -> Dict[str, Any]:
    p = state["prs"][str(pr)]
    tid = _next_id(state)
    cid = _next_id(state)
    th = {"id": f"PRRT_{tid}", "isResolved": False, "path": path, "line": line,
          "comments": [{"id": cid, "author": author, "body": body, "createdAt": now(),
                        "url": f"{_base_url(state)}/pull/{pr}#discussion_r{cid}", "in_reply_to_id": None}]}
    p["threads"].append(th)
    return th


def _git(state: Dict[str, Any], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "--git-dir", state["origin"], *args], capture_output=True, text=True)


def _rev(state: Dict[str, Any], branch: str) -> str:
    p = _git(state, "rev-parse", "--verify", "-q", f"refs/heads/{branch}^{{commit}}")
    return p.stdout.strip() if p.returncode == 0 else ""


def pr_head_oid(state: Dict[str, Any], pr: int) -> str:
    p = state["prs"][str(pr)]
    return _rev(state, p["headRefName"]) or p.get("lastHeadRefOid", "")


# ---------------------------------------------------------------------------
# 旗標解析
# ---------------------------------------------------------------------------

def parse(args: List[str], spec: Dict[str, Tuple[str, str]], usage: str) -> Tuple[Dict[str, Any], List[str]]:
    """spec：旗標 → (名稱, 種類)，種類是 bool / str / list。支援 --flag=value 與 --。"""
    opts: Dict[str, Any] = {}
    pos: List[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            pos += args[i + 1:]
            break
        if a.startswith("-") and a != "-":
            flag, val = (a.split("=", 1) + [None])[:2] if a.startswith("--") else (a, None)
            if flag not in spec:
                what = f"flag: {flag}" if a.startswith("--") else f"shorthand flag: '{a[1:2]}' in {flag}"
                raise GhError(f"unknown {what}\n\nUsage:  {usage}", unsupported=True)
            name, typ = spec[flag]
            if typ == "bool":
                opts[name] = True if val is None else val not in ("false", "0")
            else:
                if val is None:
                    i += 1
                    if i >= len(args):
                        raise GhError(f"flag needs an argument: {flag}\n\nUsage:  {usage}")
                    val = args[i]
                if typ == "list":
                    opts.setdefault(name, []).append(val)
                else:
                    opts[name] = val
        else:
            pos.append(a)
        i += 1
    return opts, pos


JSON_FLAGS = {"--json": ("json", "str"), "-q": ("jq", "str"), "--jq": ("jq", "str"),
              "-t": ("template", "str"), "--template": ("template", "str")}


def _list_values(vals: Optional[List[str]]) -> List[str]:
    out: List[str] = []
    for v in vals or []:
        out += [x.strip() for x in v.split(",") if x.strip()]
    return out


def _read_body(ctx: Ctx, opts: Dict[str, Any]) -> Optional[str]:
    if "body_file" in opts:
        f = opts["body_file"]
        try:
            if f == "-":
                return sys.stdin.read()
            return Path(f if os.path.isabs(f) else os.path.join(ctx.cwd, f)).read_text(encoding="utf-8")
        except OSError as e:
            raise GhError(f"open {f}: {e.strerror.lower() if e.strerror else 'no such file or directory'}")
    return opts.get("body")


# ---------------------------------------------------------------------------
# jq 子集
# ---------------------------------------------------------------------------

class JqError(Exception):
    pass


def _split_top(expr: str, sep: str) -> List[str]:
    parts, depth, cur, i, q = [], 0, "", 0, False
    while i < len(expr):
        ch = expr[i]
        if ch == '"' and (i == 0 or expr[i - 1] != "\\"):
            q = not q
        if not q:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif depth == 0 and expr.startswith(sep, i):
                parts.append(cur)
                cur, i = "", i + len(sep)
                continue
        cur += ch
        i += 1
    parts.append(cur)
    return parts


_PATH_TOKEN = re.compile(r'\.([A-Za-z_][A-Za-z0-9_]*)|\.?\[(\d*|"[^"]*")\]')


def _literal(s: str) -> Any:
    s = s.strip()
    try:
        return json.loads(s)
    except ValueError:
        raise JqError(s)


def _path(expr: str, values: List[Any]) -> List[Any]:
    expr = expr.strip()
    if not expr.startswith("."):
        raise JqError(expr)
    if expr == ".":
        return values
    pos, out = 0, values
    while pos < len(expr):
        m = _PATH_TOKEN.match(expr, pos)
        if not m or m.end() == pos:
            raise JqError(expr)
        name, idx = m.group(1), m.group(2)
        nxt: List[Any] = []
        for v in out:
            if name is not None:
                if v is None:
                    nxt.append(None)
                elif isinstance(v, dict):
                    nxt.append(v.get(name))
                else:
                    raise JqError(f"cannot index {type(v).__name__} with \"{name}\"")
            elif idx == "":
                if isinstance(v, list):
                    nxt += v
                elif isinstance(v, dict):
                    nxt += list(v.values())
                elif v is not None:
                    raise JqError(f"cannot iterate over {type(v).__name__}")
            elif idx.startswith('"'):
                nxt.append((v or {}).get(json.loads(idx)) if isinstance(v, (dict, type(None))) else None)
            else:
                nxt.append(v[int(idx)] if isinstance(v, list) and int(idx) < len(v) else None)
        out = nxt
        pos = m.end()
    return out


_CMP = re.compile(r"^(.*?)\s*(==|!=|>=|<=|>|<)\s*(.*)$")


def _cond(expr: str, v: Any) -> bool:
    expr = expr.strip()
    for op, comb in ((" or ", any), (" and ", all)):
        parts = _split_top(expr, op)
        if len(parts) > 1:
            return comb(_cond(x, v) for x in parts)
    m = _CMP.match(expr)
    if not m:
        res = _eval(expr, [v])
        return bool(res) and res[0] not in (None, False)
    left = _eval(m.group(1), [v])
    lv = left[0] if left else None
    right = _eval(m.group(3), [v]) if m.group(3).strip().startswith(".") else [_literal(m.group(3))]
    rv = right[0] if right else None
    op = m.group(2)
    try:
        return {"==": lv == rv, "!=": lv != rv, ">": lv is not None and lv > rv, "<": lv is not None and lv < rv,
                ">=": lv is not None and lv >= rv, "<=": lv is not None and lv <= rv}[op]
    except TypeError:
        return False


def _eval(expr: str, values: List[Any]) -> List[Any]:
    expr = expr.strip()
    stages = _split_top(expr, "|")
    if len(stages) > 1:
        for st in stages:
            values = _eval(st, values)
        return values
    alts = _split_top(expr, "//")
    if len(alts) > 1:
        out: List[Any] = []
        for v in values:
            got = [x for x in _eval(alts[0], [v]) if x not in (None, False)]
            if got:
                out += got
            elif alts[1].strip() != "empty":
                out += _eval(alts[1], [v])
        return out
    if expr == "empty":
        return []
    if expr == "length":
        return [len(v) if v is not None else 0 for v in values]
    if expr == "keys":
        return [sorted(v.keys()) if isinstance(v, dict) else list(range(len(v))) for v in values]
    if expr in ("first", "last"):
        return [(v[0] if expr == "first" else v[-1]) if v else None for v in values]
    if expr.startswith('"') or expr in ("null", "true", "false") or re.match(r"^-?\d+(\.\d+)?$", expr):
        return [_literal(expr) for _ in values]
    m = re.match(r"^select\((.*)\)$", expr, re.S)
    if m:
        return [v for v in values if _cond(m.group(1), v)]
    m = re.match(r"^map\((.*)\)$", expr, re.S)
    if m:
        return [[x for item in (v or []) for x in _eval(m.group(1), [item])] for v in values]
    m = re.match(r'^join\(("(?:[^"\\]|\\.)*")\)$', expr)
    if m:
        sep = json.loads(m.group(1))
        return [sep.join("" if x is None else str(x) for x in (v or [])) for v in values]
    m = re.match(r"^\[(.*)\]$", expr, re.S)
    if m:
        return [[x for v in values for x in _eval(m.group(1), [v])]]
    if expr.startswith("."):
        return _path(expr, values)
    raise JqError(expr)


def jq(expr: str, data: Any) -> str:
    try:
        results = _eval(expr, [data])
    except (JqError, ValueError, IndexError, AttributeError) as e:
        raise GhError(f"failed to parse jq expression (line 1, column 1)\n    {expr}\n    ^  {e}", unsupported=True)
    lines = []
    for r in results:
        lines.append(r if isinstance(r, str) else dumps(r))
    return "".join(x + "\n" for x in lines)


def emit(ctx: Ctx, data: Any, opts: Dict[str, Any]) -> None:
    if opts.get("template"):
        raise unsupported("template: unsupported in this environment")
    if opts.get("jq"):
        ctx.out.append(jq(opts["jq"], data))
    else:
        ctx.print(dumps(data))


# ---------------------------------------------------------------------------
# JSON 欄位
# ---------------------------------------------------------------------------

ISSUE_FIELDS = ["assignees", "author", "body", "closed", "closedAt", "closedByPullRequestsReferences", "comments",
                "createdAt", "id", "isPinned", "labels", "milestone", "number", "projectCards", "projectItems",
                "reactionGroups", "state", "stateReason", "title", "updatedAt", "url"]
PR_FIELDS = ["additions", "assignees", "author", "autoMergeRequest", "baseRefName", "baseRefOid", "body",
             "changedFiles", "closed", "closedAt", "closingIssuesReferences", "comments", "commits", "createdAt",
             "deletions", "files", "fullDatabaseId", "headRefName", "headRefOid", "headRepository",
             "headRepositoryOwner", "id", "isCrossRepository", "isDraft", "labels", "latestReviews",
             "maintainerCanModify", "mergeCommit", "mergeStateStatus", "mergeable", "mergedAt", "mergedBy",
             "milestone", "number", "potentialMergeCommit", "projectCards", "projectItems", "reactionGroups",
             "reviewDecision", "reviewRequests", "reviews", "state", "statusCheckRollup", "title", "updatedAt", "url"]
REPO_FIELDS = ["defaultBranchRef", "description", "id", "isPrivate", "name", "nameWithOwner", "owner", "sshUrl",
               "url", "visibility", "viewerPermission", "isFork", "isArchived", "homepageUrl", "createdAt",
               "pushedAt", "updatedAt", "parent", "languages", "primaryLanguage", "repositoryTopics", "stargazerCount",
               "forkCount", "hasIssuesEnabled", "hasWikiEnabled", "isTemplate", "licenseInfo", "mergeCommitAllowed",
               "rebaseMergeAllowed", "squashMergeAllowed", "deleteBranchOnMerge", "diskUsage", "isEmpty", "isInOrganization",
               "isMirror", "isSecurityPolicyEnabled", "issues", "labels", "latestRelease", "mirrorUrl", "openGraphImageUrl",
               "pullRequests", "securityPolicyUrl", "templateRepository", "usesCustomOpenGraphImage", "viewerCanAdminister",
               "viewerHasStarred", "viewerSubscription", "visibility", "watchers", "codeOfConduct", "contactLinks",
               "fundingLinks", "issueTemplates", "pullRequestTemplates", "milestones", "projects", "projectsV2",
               "assignableUsers", "mentionableUsers", "autoMergeAllowed", "forkingAllowed", "hasDiscussionsEnabled",
               "hasProjectsEnabled", "hasVulnerabilityAlertsEnabled", "isBlankIssuesEnabled", "isUserConfigurationRepository",
               "stargazers", "sshUrl"]


def user(login: Optional[str]) -> Optional[Dict[str, Any]]:
    if not login:
        return None
    return {"id": f"U_{login}", "is_bot": False, "login": login, "name": ""}


def _assoc(state: Dict[str, Any], login: str) -> str:
    return "OWNER" if login == state["login"] else "CONTRIBUTOR"


def comment_json(state: Dict[str, Any], c: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": f"IC_kwDO{c['id']}", "author": {"login": c["author"]}, "authorAssociation": _assoc(state, c["author"]),
            "body": c["body"], "createdAt": c["createdAt"],
            "includesCreatedEdit": c.get("updatedAt", c["createdAt"]) != c["createdAt"], "isMinimized": False,
            "minimizedReason": "", "reactionGroups": [], "url": c["url"], "viewerDidAuthor": c["author"] == state["login"]}


def review_json(state: Dict[str, Any], pr: int, r: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": f"PRR_kwDO{r['id']}", "author": {"login": r["author"]}, "authorAssociation": _assoc(state, r["author"]),
            "body": r["body"], "submittedAt": r["submittedAt"], "includesCreatedEdit": False, "reactionGroups": [],
            "state": r["state"], "commit": {"oid": r.get("commit", "")}}


def _labels(state: Dict[str, Any], names: List[str]) -> List[Dict[str, Any]]:
    return [{"id": f"LA_{n}", "name": n, "description": state["labels"].get(n, {}).get("description", ""),
             "color": state["labels"].get(n, {}).get("color", "ededed")} for n in names]


def issue_json(state: Dict[str, Any], i: Dict[str, Any], fields: List[str], ctx: Ctx) -> Dict[str, Any]:
    n = i["number"]
    closed = i["state"] == "CLOSED"
    vals: Dict[str, Callable[[], Any]] = {
        "assignees": lambda: [{"id": f"U_{a}", "login": a, "name": ""} for a in i["assignees"]],
        "author": lambda: user(i["author"]),
        "body": lambda: i["body"],
        "closed": lambda: closed,
        "closedAt": lambda: i.get("closedAt") if closed else None,
        "comments": lambda: [comment_json(state, c) for c in i["comments"]],
        "createdAt": lambda: i["createdAt"],
        "id": lambda: f"I_kwDO{n}",
        "isPinned": lambda: False,
        "labels": lambda: _labels(state, i["labels"]),
        "milestone": lambda: None,
        "number": lambda: n,
        "projectCards": lambda: [],
        "projectItems": lambda: [],
        "reactionGroups": lambda: [],
        "state": lambda: i["state"],
        "stateReason": lambda: i.get("stateReason", ""),
        "title": lambda: i["title"],
        "updatedAt": lambda: i.get("updatedAt", i["createdAt"]),
        "url": lambda: f"{_base_url(state)}/issues/{n}",
    }
    return _fields(vals, fields, ISSUE_FIELDS, ctx)


def _fields(vals: Dict[str, Callable[[], Any]], fields: List[str], known: List[str], ctx: Ctx) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for f in fields:
        if f in vals:
            out[f] = vals[f]()
        elif f in known:
            ctx.unsupported = True
            out[f] = None
        else:
            raise GhError(f'Unknown JSON field: "{f}"\nAvailable fields:\n' + "".join(f"  {k}\n" for k in sorted(set(known))))
    return out


def _numstat(state: Dict[str, Any], base: str, head: str) -> List[Dict[str, Any]]:
    if not base or not head:
        return []
    p = _git(state, "diff", "--numstat", f"{base}...{head}")
    files = []
    for line in p.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            a, d = (int(parts[0]) if parts[0].isdigit() else 0), (int(parts[1]) if parts[1].isdigit() else 0)
            files.append({"path": parts[2], "additions": a, "deletions": d, "changeType": "MODIFIED"})
    return files


def _mergeable(state: Dict[str, Any], base: str, head: str) -> str:
    if not base or not head:
        return "UNKNOWN"
    p = _git(state, "merge-tree", "--write-tree", "--no-messages", base, head)
    return {0: "MERGEABLE", 1: "CONFLICTING"}.get(p.returncode, "UNKNOWN")


def _commits(state: Dict[str, Any], base: str, head: str) -> List[Dict[str, Any]]:
    if not base or not head:
        return []
    p = _git(state, "log", "--reverse", "--format=%H%x1f%s%x1f%b%x1f%an%x1f%ae%x1f%aI%x1e", f"{base}..{head}")
    out = []
    for rec in p.stdout.split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) == 6:
            out.append({"oid": parts[0], "messageHeadline": parts[1], "messageBody": parts[2].strip(),
                        "authoredDate": parts[5], "committedDate": parts[5],
                        "authors": [{"email": parts[4], "id": "", "login": "", "name": parts[3]}]})
    return out


def _review_decision(p: Dict[str, Any]) -> str:
    latest: Dict[str, str] = {}
    for r in p["reviews"]:
        if r["state"] in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[r["author"]] = r["state"]
    if "CHANGES_REQUESTED" in latest.values():
        return "CHANGES_REQUESTED"
    if "APPROVED" in latest.values():
        return "APPROVED"
    return "REVIEW_REQUIRED" if p["reviews"] else ""


def pr_json(state: Dict[str, Any], p: Dict[str, Any], fields: List[str], ctx: Ctx) -> Dict[str, Any]:
    n = p["number"]
    cache: Dict[str, Any] = {}

    def head() -> str:
        if "head" not in cache:
            cache["head"] = pr_head_oid(state, n)
        return cache["head"]

    def base() -> str:
        if "base" not in cache:
            cache["base"] = _rev(state, p["baseRefName"])
        return cache["base"]

    def files() -> List[Dict[str, Any]]:
        if "files" not in cache:
            cache["files"] = _numstat(state, base(), head())
        return cache["files"]

    st = p["state"]

    def merge_state() -> str:
        if st != "OPEN":
            return "UNKNOWN"
        if p["isDraft"]:
            return "DRAFT"
        return {"MERGEABLE": "CLEAN", "CONFLICTING": "DIRTY"}.get(_mergeable(state, base(), head()), "UNKNOWN")

    latest: Dict[str, Dict[str, Any]] = {}
    for r in p["reviews"]:
        latest[r["author"]] = r
    vals: Dict[str, Callable[[], Any]] = {
        "additions": lambda: sum(f["additions"] for f in files()),
        "assignees": lambda: [{"id": f"U_{a}", "login": a, "name": ""} for a in p.get("assignees", [])],
        "author": lambda: user(p["author"]),
        "autoMergeRequest": lambda: None,
        "baseRefName": lambda: p["baseRefName"],
        "baseRefOid": base,
        "body": lambda: p["body"],
        "changedFiles": lambda: len(files()),
        "closed": lambda: st != "OPEN",
        "closedAt": lambda: p.get("closedAt"),
        "closingIssuesReferences": lambda: [],
        "comments": lambda: [comment_json(state, c) for c in p["comments"]],
        "commits": lambda: _commits(state, base(), head()),
        "createdAt": lambda: p["createdAt"],
        "deletions": lambda: sum(f["deletions"] for f in files()),
        "files": lambda: [{"path": f["path"], "additions": f["additions"], "deletions": f["deletions"]} for f in files()],
        "fullDatabaseId": lambda: str(n * 1000),
        "headRefName": lambda: p["headRefName"],
        "headRefOid": head,
        "headRepository": lambda: {"id": "R_1", "name": state["repo"]},
        "headRepositoryOwner": lambda: {"id": "U_1", "login": state["owner"]},
        "id": lambda: f"PR_kwDO{n}",
        "isCrossRepository": lambda: False,
        "isDraft": lambda: p["isDraft"],
        "labels": lambda: _labels(state, p.get("labels", [])),
        "latestReviews": lambda: [review_json(state, n, r) for r in latest.values()],
        "maintainerCanModify": lambda: False,
        "mergeCommit": lambda: {"oid": p["mergeCommit"]} if p.get("mergeCommit") else None,
        "mergeStateStatus": merge_state,
        "mergeable": lambda: _mergeable(state, base(), head()) if st == "OPEN" else "UNKNOWN",
        "mergedAt": lambda: p.get("mergedAt"),
        "mergedBy": lambda: user(p.get("mergedBy")),
        "milestone": lambda: None,
        "number": lambda: n,
        "potentialMergeCommit": lambda: None,
        "projectCards": lambda: [],
        "projectItems": lambda: [],
        "reactionGroups": lambda: [],
        "reviewDecision": lambda: _review_decision(p),
        "reviewRequests": lambda: [],
        "reviews": lambda: [review_json(state, n, r) for r in p["reviews"]],
        "state": lambda: st,
        "statusCheckRollup": lambda: [],
        "title": lambda: p["title"],
        "updatedAt": lambda: p.get("updatedAt", p["createdAt"]),
        "url": lambda: f"{_base_url(state)}/pull/{n}",
    }
    return _fields(vals, fields, PR_FIELDS, ctx)


def _json_fields(opts: Dict[str, Any], known: List[str]) -> Optional[List[str]]:
    if "json" not in opts:
        if opts.get("jq"):
            raise GhError("cannot use `--jq` without specifying `--json`")
        return None
    fields = [f.strip() for f in opts["json"].split(",") if f.strip()]
    if not fields:
        raise GhError("Specify one or more comma-separated fields for `--json`:\n" + "".join(f"  {k}\n" for k in sorted(set(known))))
    return fields


# ---------------------------------------------------------------------------
# 找 issue / PR
# ---------------------------------------------------------------------------

def _num(arg: str) -> Optional[int]:
    m = re.match(r"^#?(\d+)$", arg) or re.search(r"/(?:pull|issues)/(\d+)", arg)
    return int(m.group(1)) if m else None


def get_issue(state: Dict[str, Any], arg: str) -> Dict[str, Any]:
    n = _num(arg)
    if n is None:
        raise GhError(f'invalid issue format: "{arg}"')
    i = state["issues"].get(str(n))
    if i is None:
        raise GhError(f"GraphQL: Could not resolve to an issue or pull request with the number of {n}. (repository.issue)")
    return i


def current_branch(cwd: str) -> str:
    p = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise GhError("could not determine current branch: " + (p.stderr.strip() or "not a git repository"))
    b = p.stdout.strip()
    if b == "HEAD":
        raise GhError("could not determine current branch: not on any branch")
    return b


def get_pr(state: Dict[str, Any], arg: Optional[str], cwd: str) -> Dict[str, Any]:
    if not arg:
        branch = current_branch(cwd)
        prs = [p for p in state["prs"].values() if p["headRefName"] == branch]
        if not prs:
            raise GhError(f'no pull requests found for branch "{branch}"')
        open_ = [p for p in prs if p["state"] == "OPEN"]
        return max(open_ or prs, key=lambda p: p["number"])
    n = _num(arg)
    if n is not None:
        p = state["prs"].get(str(n))
        if p is None:
            raise GhError(f"GraphQL: Could not resolve to a PullRequest with the number of {n}. (repository.pullRequest)")
        return p
    prs = [p for p in state["prs"].values() if p["headRefName"] == arg]
    if not prs:
        raise GhError(f'no pull requests found for branch "{arg}"')
    open_ = [p for p in prs if p["state"] == "OPEN"]
    return max(open_ or prs, key=lambda p: p["number"])


def _resolve_login(state: Dict[str, Any], v: str) -> str:
    return state["login"] if v == "@me" else v


def _touch(ctx: Ctx, item: Dict[str, Any]) -> None:
    item["updatedAt"] = now()
    ctx.dirty = True


# ---------------------------------------------------------------------------
# --attach：上傳本機圖片，把內文裡的路徑換成網址
# ---------------------------------------------------------------------------

def apply_attachments(ctx: Ctx, body: str, files: List[str], pr: Dict[str, Any]) -> str:
    for f in files:
        path = f if os.path.isabs(f) else os.path.normpath(os.path.join(ctx.cwd, f))
        if not os.path.isfile(path):
            raise GhError(f"failed to attach {f}: open {f}: no such file or directory")
        digest = hashlib.sha1(Path(path).read_bytes()).hexdigest()
        url = f"https://{HOST}/user-attachments/assets/{uuid.UUID(digest[:32])}"
        cands = {f, path, os.path.relpath(path, ctx.cwd)}
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=ctx.cwd, capture_output=True, text=True)
        if top.returncode == 0 and top.stdout.strip():
            cands.add(os.path.relpath(path, top.stdout.strip()))
        cands |= {"./" + c for c in list(cands) if not c.startswith(("/", "./", "../"))}
        referenced = False
        for c in sorted(cands, key=len, reverse=True):
            if c in body:
                body = body.replace(c, url)
                referenced = True
        pr.setdefault("attachments", []).append({"path": path, "name": os.path.basename(path), "url": url,
                                                 "referenced": referenced})
    return body


# ---------------------------------------------------------------------------
# issue
# ---------------------------------------------------------------------------

def _issue_text(state: Dict[str, Any], i: Dict[str, Any], with_comments: bool) -> str:
    lines = [f"title:\t{i['title']}", f"state:\t{i['state']}", f"author:\t{i['author']}",
             f"labels:\t{', '.join(i['labels'])}", f"comments:\t{len(i['comments'])}",
             f"assignees:\t{', '.join(i['assignees'])}", "projects:\t", "milestone:\t", f"number:\t{i['number']}",
             "--", i["body"]]
    text = "\n".join(lines).rstrip("\n") + "\n"
    if with_comments:
        text += _comments_text(state, i["comments"])
    return text


def _comments_text(state: Dict[str, Any], comments: List[Dict[str, Any]]) -> str:
    out = ""
    for c in comments:
        out += (f"author:\t{c['author']}\nassociation:\t{_assoc(state, c['author']).lower()}\n"
                f"edited:\t{str(c.get('updatedAt') != c['createdAt']).lower()}\nstatus:\tnone\n--\n{c['body'].rstrip()}\n--\n")
    return out


def cmd_issue(ctx: Ctx, state: Dict[str, Any], args: List[str]) -> None:
    sub, rest = (args[0], args[1:]) if args else ("", [])
    if sub == "view":
        opts, pos = parse(rest, {**JSON_FLAGS, "--comments": ("comments", "bool"), "-c": ("comments", "bool"),
                                 "-w": ("web", "bool"), "--web": ("web", "bool")}, "gh issue view {<number> | <url>} [flags]")
        if opts.get("web"):
            raise unsupported("--web is not supported in this environment")
        if not pos:
            raise GhError("accepts 1 arg(s), received 0")
        i = get_issue(state, pos[0])
        fields = _json_fields(opts, ISSUE_FIELDS)
        if fields is None:
            ctx.out.append(_issue_text(state, i, bool(opts.get("comments"))))
        else:
            emit(ctx, issue_json(state, i, fields, ctx), opts)
    elif sub == "list":
        opts, pos = parse(rest, {**JSON_FLAGS, "-l": ("label", "list"), "--label": ("label", "list"),
                                 "-s": ("state", "str"), "--state": ("state", "str"), "-L": ("limit", "str"),
                                 "--limit": ("limit", "str"), "-a": ("assignee", "str"), "--assignee": ("assignee", "str"),
                                 "-A": ("author", "str"), "--author": ("author", "str"), "-S": ("search", "str"),
                                 "--search": ("search", "str")}, "gh issue list [flags]")
        if opts.get("search"):
            raise unsupported("--search is not supported in this environment")
        want = opts.get("state", "open").lower()
        labels = _list_values(opts.get("label"))
        items = sorted(state["issues"].values(), key=lambda i: -i["number"])
        items = [i for i in items if want == "all" or i["state"].lower() == want]
        items = [i for i in items if all(l in i["labels"] for l in labels)]
        if opts.get("assignee"):
            items = [i for i in items if _resolve_login(state, opts["assignee"]) in i["assignees"]]
        if opts.get("author"):
            items = [i for i in items if i["author"] == _resolve_login(state, opts["author"])]
        items = items[:int(opts.get("limit", 30))]
        fields = _json_fields(opts, ISSUE_FIELDS)
        if fields is None:
            for i in items:
                ctx.print(f"{i['number']}\t{i['state']}\t{i['title']}\t{', '.join(i['labels'])}\t{i.get('updatedAt', '')}")
        else:
            emit(ctx, [issue_json(state, i, fields, ctx) for i in items], opts)
    elif sub == "comment":
        opts, pos = parse(rest, {"-b": ("body", "str"), "--body": ("body", "str"), "-F": ("body_file", "str"),
                                 "--body-file": ("body_file", "str"), "--edit-last": ("edit_last", "bool")},
                          "gh issue comment {<number> | <url>} [flags]")
        if not pos:
            raise GhError("accepts 1 arg(s), received 0")
        i = get_issue(state, pos[0])
        ctx.print(_comment(ctx, state, i, opts))
    elif sub == "edit":
        opts, pos = parse(rest, {"--add-label": ("add_label", "list"), "--remove-label": ("remove_label", "list"),
                                 "--add-assignee": ("add_assignee", "list"), "--remove-assignee": ("remove_assignee", "list"),
                                 "-b": ("body", "str"), "--body": ("body", "str"), "-F": ("body_file", "str"),
                                 "--body-file": ("body_file", "str"), "-t": ("title", "str"), "--title": ("title", "str")},
                          "gh issue edit {<numbers> | <urls>} [flags]")
        if not pos:
            raise GhError("accepts 1 arg(s), received 0")
        i = get_issue(state, pos[0])
        _edit_common(ctx, state, i, opts)
        ctx.print(f"{_base_url(state)}/issues/{i['number']}")
    elif sub in ("close", "reopen"):
        opts, pos = parse(rest, {"-c": ("comment", "str"), "--comment": ("comment", "str"), "-r": ("reason", "str"),
                                 "--reason": ("reason", "str")}, f"gh issue {sub} {{<number> | <url>}} [flags]")
        if not pos:
            raise GhError("accepts 1 arg(s), received 0")
        i = get_issue(state, pos[0])
        target = "CLOSED" if sub == "close" else "OPEN"
        ref = f"{state['owner']}/{state['repo']}#{i['number']} ({i['title']})"
        if i["state"] == target:
            ctx.eprint(f"! Issue {ref} is already {'closed' if sub == 'close' else 'open'}")
            return
        if opts.get("comment"):
            add_comment(state, i["number"], opts["comment"])
        i["state"] = target
        if target == "CLOSED":
            i["closedAt"] = now()
            i["stateReason"] = "NOT_PLANNED" if opts.get("reason", "").replace(" ", "_").lower() == "not_planned" else "COMPLETED"
        _touch(ctx, i)
        ctx.eprint(f"✓ {'Closed' if sub == 'close' else 'Reopened'} issue {ref}")
    elif sub == "create":
        opts, pos = parse(rest, {"-t": ("title", "str"), "--title": ("title", "str"), "-b": ("body", "str"),
                                 "--body": ("body", "str"), "-F": ("body_file", "str"), "--body-file": ("body_file", "str"),
                                 "-l": ("label", "list"), "--label": ("label", "list"), "-a": ("assignee", "list"),
                                 "--assignee": ("assignee", "list")}, "gh issue create [flags]")
        body = _read_body(ctx, opts)
        if not opts.get("title") or body is None:
            raise GhError("must provide `--title` and `--body` when not running interactively")
        labels = _list_values(opts.get("label"))
        missing = [l for l in labels if l not in state["labels"]]
        if missing:
            raise GhError(f"could not add label: '{missing[0]}' not found")
        n = add_issue(state, opts["title"], body, labels=labels)
        state["issues"][str(n)]["assignees"] = [_resolve_login(state, a) for a in _list_values(opts.get("assignee"))]
        ctx.dirty = True
        ctx.eprint(f"\nCreating issue in {state['owner']}/{state['repo']}\n")
        ctx.print(f"{_base_url(state)}/issues/{n}")
    else:
        raise unsupported(f'unknown command "{sub}" for "gh issue"')


def _comment(ctx: Ctx, state: Dict[str, Any], item: Dict[str, Any], opts: Dict[str, Any]) -> str:
    body = _read_body(ctx, opts)
    if body is None:
        raise GhError("`--body` or `--body-file` required when not running interactively")
    if opts.get("edit_last"):
        mine = [c for c in item["comments"] if c["author"] == state["login"]]
        if not mine:
            raise GhError("no comments found for current user")
        c = mine[-1]
        c["body"], c["updatedAt"] = body, now()
        _touch(ctx, item)
        return c["url"]
    c = add_comment(state, item["number"], body)
    ctx.dirty = True
    return c["url"]


def _edit_common(ctx: Ctx, state: Dict[str, Any], item: Dict[str, Any], opts: Dict[str, Any]) -> None:
    body = _read_body(ctx, opts)
    if body is not None:
        item["body"] = body
        item["edits"] = item.get("edits", 0) + 1
    if opts.get("title"):
        item["title"] = opts["title"]
    for l in _list_values(opts.get("add_label")):
        if l not in state["labels"]:
            raise GhError(f"'{l}' not found")
        if l not in item["labels"]:
            item["labels"].append(l)
    for l in _list_values(opts.get("remove_label")):
        if l in item["labels"]:
            item["labels"].remove(l)
    for a in _list_values(opts.get("add_assignee")):
        a = _resolve_login(state, a)
        if a not in item.setdefault("assignees", []):
            item["assignees"].append(a)
    for a in _list_values(opts.get("remove_assignee")):
        a = _resolve_login(state, a)
        if a in item.get("assignees", []):
            item["assignees"].remove(a)
    _touch(ctx, item)


# ---------------------------------------------------------------------------
# pr
# ---------------------------------------------------------------------------

def _pr_text(state: Dict[str, Any], p: Dict[str, Any], with_comments: bool, ctx: Ctx) -> str:
    j = pr_json(state, p, ["additions", "deletions", "url"], ctx)
    reviewers = ", ".join(sorted({f"{r['author']} ({r['state'].replace('_', ' ').title()})" for r in p["reviews"]}))
    lines = [f"title:\t{p['title']}", f"state:\t{'DRAFT' if p['isDraft'] and p['state'] == 'OPEN' else p['state']}",
             f"author:\t{p['author']}", f"labels:\t{', '.join(p.get('labels', []))}",
             f"assignees:\t{', '.join(p.get('assignees', []))}", f"reviewers:\t{reviewers}", "projects:\t", "milestone:\t",
             f"number:\t{p['number']}", f"url:\t{j['url']}", f"additions:\t{j['additions']}",
             f"deletions:\t{j['deletions']}", "auto-merge:\tdisabled", "--", p["body"]]
    text = "\n".join(lines).rstrip("\n") + "\n"
    if with_comments:
        text += _comments_text(state, p["comments"])
    return text


def cmd_pr(ctx: Ctx, state: Dict[str, Any], args: List[str]) -> None:
    sub, rest = (args[0], args[1:]) if args else ("", [])
    if sub == "view":
        opts, pos = parse(rest, {**JSON_FLAGS, "--comments": ("comments", "bool"), "-c": ("comments", "bool"),
                                 "-w": ("web", "bool"), "--web": ("web", "bool")},
                          "gh pr view [<number> | <url> | <branch>] [flags]")
        if opts.get("web"):
            raise unsupported("--web is not supported in this environment")
        p = get_pr(state, pos[0] if pos else None, ctx.cwd)
        fields = _json_fields(opts, PR_FIELDS)
        if fields is None:
            ctx.out.append(_pr_text(state, p, bool(opts.get("comments")), ctx))
        else:
            emit(ctx, pr_json(state, p, fields, ctx), opts)
    elif sub == "list":
        opts, pos = parse(rest, {**JSON_FLAGS, "-H": ("head", "str"), "--head": ("head", "str"), "-B": ("base", "str"),
                                 "--base": ("base", "str"), "-s": ("state", "str"), "--state": ("state", "str"),
                                 "-L": ("limit", "str"), "--limit": ("limit", "str"), "-A": ("author", "str"),
                                 "--author": ("author", "str"), "-l": ("label", "list"), "--label": ("label", "list"),
                                 "-d": ("draft", "bool"), "--draft": ("draft", "bool"), "-S": ("search", "str"),
                                 "--search": ("search", "str")}, "gh pr list [flags]")
        if opts.get("search"):
            raise unsupported("--search is not supported in this environment")
        want = opts.get("state", "open").lower()
        if want not in ("open", "closed", "merged", "all"):
            raise GhError(f'invalid argument "{want}" for "-s, --state" flag: valid values are {{open|closed|merged|all}}')
        items = sorted(state["prs"].values(), key=lambda p: -p["number"])
        if want == "closed":
            items = [p for p in items if p["state"] in ("CLOSED", "MERGED")]
        elif want != "all":
            items = [p for p in items if p["state"].lower() == want]
        if opts.get("head"):
            items = [p for p in items if p["headRefName"] == opts["head"]]
        if opts.get("base"):
            items = [p for p in items if p["baseRefName"] == opts["base"]]
        if opts.get("author"):
            items = [p for p in items if p["author"] == _resolve_login(state, opts["author"])]
        for l in _list_values(opts.get("label")):
            items = [p for p in items if l in p.get("labels", [])]
        if opts.get("draft"):
            items = [p for p in items if p["isDraft"]]
        items = items[:int(opts.get("limit", 30))]
        fields = _json_fields(opts, PR_FIELDS)
        if fields is None:
            for p in items:
                st = "DRAFT" if p["isDraft"] and p["state"] == "OPEN" else p["state"]
                ctx.print(f"{p['number']}\t{p['title']}\t{p['headRefName']}\t{st}\t{p['createdAt']}")
        else:
            emit(ctx, [pr_json(state, p, fields, ctx) for p in items], opts)
    elif sub == "create":
        _pr_create(ctx, state, rest)
    elif sub == "edit":
        opts, pos = parse(rest, {"-b": ("body", "str"), "--body": ("body", "str"), "-F": ("body_file", "str"),
                                 "--body-file": ("body_file", "str"), "-t": ("title", "str"), "--title": ("title", "str"),
                                 "--add-label": ("add_label", "list"), "--remove-label": ("remove_label", "list"),
                                 "--add-assignee": ("add_assignee", "list"), "--remove-assignee": ("remove_assignee", "list"),
                                 "--add-reviewer": ("add_reviewer", "list"), "--remove-reviewer": ("remove_reviewer", "list"),
                                 "-B": ("base", "str"), "--base": ("base", "str"), "--attach": ("attach", "list")},
                          "gh pr edit [<number> | <url> | <branch>] [flags]")
        p = get_pr(state, pos[0] if pos else None, ctx.cwd)
        if opts.get("base"):
            if not _rev(state, opts["base"]):
                raise GhError(f"GraphQL: Proposed base branch '{opts['base']}' was not found (updatePullRequest)")
            p["baseRefName"] = opts["base"]
        if opts.get("attach"):
            body = _read_body(ctx, opts)
            opts.pop("body_file", None)
            opts["body"] = apply_attachments(ctx, body if body is not None else p["body"], opts["attach"], p)
        _edit_common(ctx, state, p, opts)
        ctx.print(f"{_base_url(state)}/pull/{p['number']}")
    elif sub == "comment":
        opts, pos = parse(rest, {"-b": ("body", "str"), "--body": ("body", "str"), "-F": ("body_file", "str"),
                                 "--body-file": ("body_file", "str"), "--edit-last": ("edit_last", "bool")},
                          "gh pr comment [<number> | <url> | <branch>] [flags]")
        p = get_pr(state, pos[0] if pos else None, ctx.cwd)
        ctx.print(_comment(ctx, state, p, opts))
    elif sub == "diff":
        opts, pos = parse(rest, {"--name-only": ("name_only", "bool"), "--patch": ("patch", "bool"),
                                 "--color": ("color", "str")}, "gh pr diff [<number> | <url> | <branch>] [flags]")
        p = get_pr(state, pos[0] if pos else None, ctx.cwd)
        base, head = _rev(state, p["baseRefName"]), pr_head_oid(state, p["number"])
        extra = ["--name-only"] if opts.get("name_only") else []
        ctx.out.append(_git(state, "diff", *extra, f"{base}...{head}").stdout)
    elif sub == "checks":
        opts, pos = parse(rest, {**JSON_FLAGS, "--watch": ("watch", "bool"), "--required": ("required", "bool")},
                          "gh pr checks [<number> | <url> | <branch>] [flags]")
        p = get_pr(state, pos[0] if pos else None, ctx.cwd)
        raise GhError(f"no checks reported on the '{p['headRefName']}' branch")
    elif sub in ("ready", "close", "reopen"):
        opts, pos = parse(rest, {"--undo": ("undo", "bool"), "-c": ("comment", "str"), "--comment": ("comment", "str"),
                                 "-d": ("delete_branch", "bool"), "--delete-branch": ("delete_branch", "bool")},
                          f"gh pr {sub} [<number> | <url> | <branch>] [flags]")
        p = get_pr(state, pos[0] if pos else None, ctx.cwd)
        ref = f"{state['owner']}/{state['repo']}#{p['number']} ({p['title']})"
        if opts.get("delete_branch"):
            raise unsupported("--delete-branch is not supported in this environment")
        if sub == "ready":
            p["isDraft"] = bool(opts.get("undo"))
            ctx.eprint(f"✓ Pull request {ref} is {'converted to draft' if opts.get('undo') else 'marked as ready for review'}")
        else:
            if opts.get("comment"):
                add_comment(state, p["number"], opts["comment"])
            if p["state"] == "MERGED":
                raise GhError(f"Pull request {ref} can't be {sub}d because it was already merged")
            p["state"] = "CLOSED" if sub == "close" else "OPEN"
            if sub == "close":
                p["closedAt"] = now()
            ctx.eprint(f"✓ {'Closed' if sub == 'close' else 'Reopened'} pull request {ref}")
        _touch(ctx, p)
    else:
        raise unsupported(f'unknown command "{sub}" for "gh pr"')


def _pr_create(ctx: Ctx, state: Dict[str, Any], rest: List[str]) -> None:
    opts, pos = parse(rest, {"-B": ("base", "str"), "--base": ("base", "str"), "-H": ("head", "str"),
                             "--head": ("head", "str"), "-t": ("title", "str"), "--title": ("title", "str"),
                             "-b": ("body", "str"), "--body": ("body", "str"), "-F": ("body_file", "str"),
                             "--body-file": ("body_file", "str"), "-d": ("draft", "bool"), "--draft": ("draft", "bool"),
                             "--attach": ("attach", "list"), "-l": ("label", "list"), "--label": ("label", "list"),
                             "-a": ("assignee", "list"), "--assignee": ("assignee", "list"), "-r": ("reviewer", "list"),
                             "--reviewer": ("reviewer", "list"), "-f": ("fill", "bool"), "--fill": ("fill", "bool")},
                      "gh pr create [flags]")
    if opts.get("fill"):
        raise unsupported("--fill is not supported in this environment")
    head = opts.get("head") or current_branch(ctx.cwd)
    base = opts.get("base") or state["default_branch"]
    body = _read_body(ctx, opts)
    if not opts.get("title") or body is None:
        raise GhError("must provide `--title` and `--body` (or `--fill` or `fill-first` or `--fillverbose`) "
                      "when not running interactively")
    head_oid = _rev(state, head)
    if not head_oid and not opts.get("head"):
        raise GhError("aborted: you must first push the current branch to a remote, or use the --head flag")
    ctx.eprint(f"\nCreating pull request for {head} into {base} in {state['owner']}/{state['repo']}\n")
    base_oid = _rev(state, base)
    if not base_oid:
        raise GhError("pull request create failed: GraphQL: Base ref must be a branch (createPullRequest)")
    if not head_oid:
        raise GhError(f"pull request create failed: GraphQL: Head sha can't be blank, No commits between {base} and {head}, "
                      "Head ref must be a branch (createPullRequest)")
    ahead = _git(state, "rev-list", "--count", f"{base_oid}..{head_oid}").stdout.strip()
    if not ahead.isdigit() or int(ahead) == 0:
        raise GhError(f"pull request create failed: GraphQL: No commits between {base} and {head} (createPullRequest)")
    for p in state["prs"].values():
        if p["headRefName"] == head and p["baseRefName"] == base and p["state"] == "OPEN":
            raise GhError(f'a pull request for branch "{head}" into branch "{base}" already exists:\n'
                          f"{_base_url(state)}/pull/{p['number']}")
    labels = _list_values(opts.get("label"))
    missing = [l for l in labels if l not in state["labels"]]
    if missing:
        raise GhError(f"could not add label: '{missing[0]}' not found")
    n = create_pr(state, opts["title"], body, head, base, draft=bool(opts.get("draft")))
    p = state["prs"][str(n)]
    if opts.get("attach"):
        p["body"] = apply_attachments(ctx, body, opts["attach"], p)
    p["labels"] = labels
    p["assignees"] = [_resolve_login(state, a) for a in _list_values(opts.get("assignee"))]
    ctx.dirty = True
    ctx.print(f"{_base_url(state)}/pull/{n}")


# ---------------------------------------------------------------------------
# label / repo / auth
# ---------------------------------------------------------------------------

def cmd_label(ctx: Ctx, state: Dict[str, Any], args: List[str]) -> None:
    sub, rest = (args[0], args[1:]) if args else ("", [])
    if sub == "create":
        opts, pos = parse(rest, {"-c": ("color", "str"), "--color": ("color", "str"), "-d": ("description", "str"),
                                 "--description": ("description", "str"), "-f": ("force", "bool"), "--force": ("force", "bool")},
                          "gh label create <name> [flags]")
        if not pos:
            raise GhError("cannot create label: name argument required")
        name = pos[0]
        if name in state["labels"] and not opts.get("force"):
            raise GhError(f'label with name "{name}" already exists; use `--force` to update its color and description')
        state["labels"][name] = {"color": (opts.get("color") or "ededed").lstrip("#"), "description": opts.get("description", "")}
        ctx.dirty = True
        ctx.eprint(f'✓ Label "{name}" created in {state["owner"]}/{state["repo"]}')
    elif sub == "list":
        opts, pos = parse(rest, {**JSON_FLAGS, "-L": ("limit", "str"), "--limit": ("limit", "str")}, "gh label list [flags]")
        items = [{"name": k, "color": v["color"], "description": v["description"], "id": f"LA_{k}", "isDefault": False,
                  "createdAt": "", "updatedAt": "", "url": f"{_base_url(state)}/labels/{k}"}
                 for k, v in sorted(state["labels"].items())]
        if "json" in opts:
            fields = _json_fields(opts, ["color", "createdAt", "description", "id", "isDefault", "name", "updatedAt", "url"])
            emit(ctx, [{f: i[f] for f in fields} for i in items], opts)
        else:
            for i in items:
                ctx.print(f"{i['name']}\t{i['description']}\t#{i['color']}")
    else:
        raise unsupported(f'unknown command "{sub}" for "gh label"')


def _repo_obj(state: Dict[str, Any]) -> Dict[str, Any]:
    return {"defaultBranchRef": {"name": state["default_branch"]}, "description": "", "id": "R_kgDO1",
            "isPrivate": True, "name": state["repo"], "nameWithOwner": f"{state['owner']}/{state['repo']}",
            "owner": {"id": "O_1", "login": state["owner"]}, "sshUrl": f"git@{HOST}:{state['owner']}/{state['repo']}.git",
            "url": _base_url(state), "visibility": "PRIVATE", "viewerPermission": "ADMIN", "isFork": False,
            "isArchived": False}


def cmd_repo(ctx: Ctx, state: Dict[str, Any], args: List[str]) -> None:
    sub, rest = (args[0], args[1:]) if args else ("", [])
    if sub != "view":
        raise unsupported(f'unknown command "{sub}" for "gh repo"')
    opts, pos = parse(rest, {**JSON_FLAGS, "-w": ("web", "bool"), "--web": ("web", "bool"),
                             "-b": ("branch", "str"), "--branch": ("branch", "str")}, "gh repo view [<repository>] [flags]")
    if pos and pos[0] not in (state["repo"], f"{state['owner']}/{state['repo']}", _base_url(state)):
        raise GhError(f"GraphQL: Could not resolve to a Repository with the name '{pos[0]}'. (repository)")
    obj = _repo_obj(state)
    fields = _json_fields(opts, REPO_FIELDS)
    if fields is None:
        readme = _git(state, "show", f"{state['default_branch']}:README.md").stdout
        ctx.out.append(f"name:\t{obj['nameWithOwner']}\ndescription:\t\n--\n{readme}")
    else:
        emit(ctx, _fields({k: (lambda v=v: v) for k, v in obj.items()}, fields, REPO_FIELDS, ctx), opts)


def cmd_auth(ctx: Ctx, state: Dict[str, Any], args: List[str]) -> None:
    sub, rest = (args[0], args[1:]) if args else ("", [])
    token = "gho_" + hashlib.sha1(state["login"].encode()).hexdigest()[:36]
    if sub == "status":
        opts, _ = parse(rest, {**JSON_FLAGS, "-h": ("hostname", "str"), "--hostname": ("hostname", "str"),
                               "-t": ("show_token", "bool"), "--show-token": ("show_token", "bool"),
                               "-a": ("active", "bool"), "--active": ("active", "bool")}, "gh auth status [flags]")
        if "json" in opts:
            host = {"state": "success", "active": True, "host": HOST, "login": state["login"], "tokenSource": "keyring",
                    "scopes": "gist, read:org, repo, workflow", "gitProtocol": "ssh"}
            emit(ctx, {"hosts": {HOST: [host]}}, opts)
            return
        ctx.print(f"{HOST}\n  ✓ Logged in to {HOST} account {state['login']} (keyring)\n  - Active account: true\n"
                  f"  - Git operations protocol: ssh\n  - Token: gho_************************************\n"
                  f"  - Token scopes: 'gist', 'read:org', 'repo', 'workflow'")
    elif sub == "token":
        opts, _ = parse(rest, {"-h": ("hostname", "str"), "--hostname": ("hostname", "str"), "-u": ("user", "str"),
                               "--user": ("user", "str")}, "gh auth token [flags]")
        if opts.get("user") and opts["user"] != state["login"]:
            raise GhError(f"no oauth token found for {HOST} account {opts['user']}")
        ctx.print(token)
    else:
        raise unsupported(f'unknown command "{sub}" for "gh auth"')


# ---------------------------------------------------------------------------
# api
# ---------------------------------------------------------------------------

def _typed(v: str, ctx: Ctx) -> Any:
    if v in ("true", "false"):
        return v == "true"
    if v == "null":
        return None
    if re.match(r"^-?\d+$", v):
        return int(v)
    if v.startswith("@"):
        f = v[1:]
        return Path(f if os.path.isabs(f) else os.path.join(ctx.cwd, f)).read_text(encoding="utf-8")
    return v


def _rest_user(login: str) -> Dict[str, Any]:
    return {"login": login, "id": int(hashlib.sha1(login.encode()).hexdigest()[:6], 16), "type": "User",
            "html_url": f"https://{HOST}/{login}", "site_admin": False}


def _rest_comment(state: Dict[str, Any], c: Dict[str, Any], number: int) -> Dict[str, Any]:
    return {"id": c["id"], "node_id": f"IC_kwDO{c['id']}", "html_url": c["url"], "body": c["body"],
            "user": _rest_user(c["author"]), "created_at": c["createdAt"], "updated_at": c.get("updatedAt", c["createdAt"]),
            "issue_url": f"https://api.{HOST}/repos/{state['owner']}/{state['repo']}/issues/{number}",
            "author_association": _assoc(state, c["author"])}


def _rest_review_comment(state: Dict[str, Any], pr: int, th: Dict[str, Any], c: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": c["id"], "node_id": f"PRRC_kwDO{c['id']}", "pull_request_review_id": None,
            "in_reply_to_id": c.get("in_reply_to_id"), "path": th["path"], "line": th["line"],
            "original_line": th["line"], "side": "RIGHT", "body": c["body"], "user": _rest_user(c["author"]),
            "created_at": c["createdAt"], "updated_at": c["createdAt"], "html_url": c["url"], "diff_hunk": "",
            "commit_id": pr_head_oid(state, pr), "author_association": _assoc(state, c["author"])}


def _find_comment(state: Dict[str, Any], cid: int) -> Tuple[Optional[Dict[str, Any]], int]:
    for coll in ("issues", "prs"):
        for item in state[coll].values():
            for c in item["comments"]:
                if c["id"] == cid:
                    return c, item["number"]
    return None, 0


def _find_thread(state: Dict[str, Any], pred: Callable[[Dict[str, Any]], bool]) -> Tuple[Optional[Dict[str, Any]], int]:
    for p in state["prs"].values():
        for th in p.get("threads", []):
            if pred(th):
                return th, p["number"]
    return None, 0


def _reply(state: Dict[str, Any], pr: int, th: Dict[str, Any], body: str, reply_to: int) -> Dict[str, Any]:
    cid = _next_id(state)
    c = {"id": cid, "author": state["login"], "body": body, "createdAt": now(),
         "url": f"{_base_url(state)}/pull/{pr}#discussion_r{cid}", "in_reply_to_id": reply_to}
    th["comments"].append(c)
    return c


def not_found(path: str) -> GhError:
    body = dumps({"message": "Not Found", "documentation_url": "https://docs.github.com/rest", "status": "404"})
    return GhError("gh: Not Found (HTTP 404)", stdout=body + "\n")


def cmd_api(ctx: Ctx, state: Dict[str, Any], args: List[str]) -> None:
    opts, pos = parse(args, {"-X": ("method", "str"), "--method": ("method", "str"), "-f": ("raw", "list"),
                             "--raw-field": ("raw", "list"), "-F": ("field", "list"), "--field": ("field", "list"),
                             "--input": ("input", "str"), "-q": ("jq", "str"), "--jq": ("jq", "str"),
                             "-H": ("header", "list"), "--header": ("header", "list"), "--paginate": ("paginate", "bool"),
                             "--slurp": ("slurp", "bool"), "--silent": ("silent", "bool"), "-t": ("template", "str"),
                             "--template": ("template", "str"), "--hostname": ("hostname", "str"),
                             "-i": ("include", "bool"), "--include": ("include", "bool"),
                             "--cache": ("cache", "str"), "-p": ("preview", "list"), "--preview": ("preview", "list"),
                             "--verbose": ("verbose", "bool")}, "gh api <endpoint> [flags]")
    if not pos:
        raise GhError("accepts 1 arg(s), received 0")
    if opts.get("include") or opts.get("verbose"):
        raise unsupported("--include/--verbose is not supported in this environment")
    endpoint = pos[0].lstrip("/").replace("{owner}", state["owner"]).replace("{repo}", state["repo"])
    params: Dict[str, Any] = {}
    for kv in opts.get("raw", []):
        k, _, v = kv.partition("=")
        params[k] = v
    for kv in opts.get("field", []):
        k, _, v = kv.partition("=")
        # 跟真的 gh 一樣：-F 的值剛好是 {owner}／{repo} 時換成目前的 repo
        params[k] = {"{owner}": state["owner"], "{repo}": state["repo"]}.get(v) or _typed(v, ctx)
    if opts.get("input"):
        f = opts["input"]
        try:
            text = sys.stdin.read() if f == "-" else Path(f if os.path.isabs(f) else os.path.join(ctx.cwd, f)).read_text(encoding="utf-8")
            params.update(json.loads(text or "{}"))
        except (OSError, ValueError) as e:
            raise GhError(f"failed to read input: {e}")
    method = (opts.get("method") or ("POST" if params else "GET")).upper()
    if endpoint == "graphql":
        res = _graphql(ctx, state, params)
    else:
        res = _rest(ctx, state, method, endpoint.split("?")[0], params)
    if opts.get("silent"):
        return
    emit(ctx, res, opts)


def _rest(ctx: Ctx, state: Dict[str, Any], method: str, path: str, params: Dict[str, Any]) -> Any:
    if path == "user" and method == "GET":
        return {**_rest_user(state["login"]), "name": "", "email": None}
    if path == "user/emails" and method == "GET":
        return [{"email": f"{state['login']}@users.noreply.{HOST}", "primary": True, "verified": True, "visibility": None}]
    full = f"repos/{state['owner']}/{state['repo']}"
    if not (path == full or path.startswith(full + "/")):
        if path.startswith("repos/"):
            raise not_found(path)
        raise unsupported(f"gh: Not Found (HTTP 404) — unsupported endpoint {path}")
    rest = path[len(full):].strip("/")
    parts = rest.split("/") if rest else []
    if not parts and method == "GET":
        return {"id": 1, "name": state["repo"], "full_name": f"{state['owner']}/{state['repo']}",
                "owner": _rest_user(state["owner"]), "private": True, "html_url": _base_url(state),
                "default_branch": state["default_branch"]}
    # issues/comments/<id>
    if parts[:2] == ["issues", "comments"] and len(parts) == 3 and parts[2].isdigit():
        c, num = _find_comment(state, int(parts[2]))
        if c is None:
            raise not_found(path)
        if method == "PATCH":
            if "body" not in params:
                raise GhError("gh: Invalid request.\n\nNo subschema in \"anyOf\" matched. (HTTP 422)")
            c["body"], c["updatedAt"] = params["body"], now()
            ctx.dirty = True
        elif method == "DELETE":
            raise unsupported("DELETE is not supported in this environment")
        elif method != "GET":
            raise not_found(path)
        return _rest_comment(state, c, num)
    if parts and parts[0] in ("issues", "pulls") and len(parts) >= 2 and parts[1].isdigit():
        n = int(parts[1])
        if parts[0] == "pulls":
            p = state["prs"].get(str(n))
            if p is None:
                raise not_found(path)
            if len(parts) == 2 and method == "GET":
                head, base = pr_head_oid(state, n), _rev(state, p["baseRefName"])
                return {"number": n, "title": p["title"], "body": p["body"], "state": "open" if p["state"] == "OPEN" else "closed",
                        "draft": p["isDraft"], "merged": p["state"] == "MERGED", "html_url": f"{_base_url(state)}/pull/{n}",
                        "head": {"ref": p["headRefName"], "sha": head}, "base": {"ref": p["baseRefName"], "sha": base},
                        "user": _rest_user(p["author"]), "mergeable": _mergeable(state, base, head) == "MERGEABLE"}
            if parts[2:] == ["comments"]:
                if method == "GET":
                    return [_rest_review_comment(state, n, th, c) for th in p.get("threads", []) for c in th["comments"]]
                if method == "POST":
                    if params.get("in_reply_to"):
                        th, _ = _find_thread(state, lambda t: any(c["id"] == int(params["in_reply_to"]) for c in t["comments"]))
                        if th is None:
                            raise not_found(path)
                        c = _reply(state, n, th, params.get("body", ""), int(params["in_reply_to"]))
                    else:
                        if not params.get("path") or not params.get("body"):
                            raise GhError("gh: Validation Failed (HTTP 422)")
                        th = add_review_thread(state, n, params["path"], int(params.get("line") or 1), state["login"], params["body"])
                        c = th["comments"][0]
                    ctx.dirty = True
                    return _rest_review_comment(state, n, th, c)
            if len(parts) == 5 and parts[2] == "comments" and parts[3].isdigit() and parts[4] == "replies" and method == "POST":
                cid = int(parts[3])
                th, _ = _find_thread(state, lambda t: any(c["id"] == cid for c in t["comments"]))
                if th is None:
                    raise not_found(path)
                if not params.get("body"):
                    raise GhError("gh: Validation Failed (HTTP 422)")
                c = _reply(state, n, th, params["body"], th["comments"][0]["id"])
                ctx.dirty = True
                return _rest_review_comment(state, n, th, c)
            if parts[2:] == ["reviews"] and method == "GET":
                return [{"id": r["id"], "user": _rest_user(r["author"]), "body": r["body"], "state": r["state"],
                         "submitted_at": r["submittedAt"], "commit_id": r.get("commit", ""),
                         "html_url": f"{_base_url(state)}/pull/{n}#pullrequestreview-{r['id']}"} for r in p["reviews"]]
            if parts[2:] == ["files"] and method == "GET":
                return [{"filename": f["path"], "additions": f["additions"], "deletions": f["deletions"], "status": "modified"}
                        for f in _numstat(state, _rev(state, p["baseRefName"]), pr_head_oid(state, n))]
        # issues/<n>（PR 也是 issue）
        try:
            kind, item = _item(state, n)
        except KeyError:
            raise not_found(path)
        if parts[0] == "issues" and len(parts) == 2 and method == "GET":
            out = {"number": n, "title": item["title"], "body": item["body"], "state": item["state"].lower()
                   if item["state"] != "MERGED" else "closed", "labels": _labels(state, item.get("labels", [])),
                   "user": _rest_user(item["author"]), "assignees": [_rest_user(a) for a in item.get("assignees", [])],
                   "html_url": f"{_base_url(state)}/{'issues' if kind == 'issue' else 'pull'}/{n}",
                   "comments": len(item["comments"])}
            if kind == "pr":
                out["pull_request"] = {"html_url": f"{_base_url(state)}/pull/{n}"}
            return out
        if parts[0] == "issues" and parts[2:] == ["comments"]:
            if method == "GET":
                return [_rest_comment(state, c, n) for c in item["comments"]]
            if method == "POST":
                if not params.get("body"):
                    raise GhError("gh: Validation Failed (HTTP 422)")
                c = add_comment(state, n, params["body"])
                ctx.dirty = True
                return _rest_comment(state, c, n)
        if parts[0] == "issues" and parts[2:] == ["labels"] and method == "POST":
            for l in params.get("labels", []):
                if l not in item["labels"]:
                    _ensure_label(state, l)
                    item["labels"].append(l)
            ctx.dirty = True
            return _labels(state, item["labels"])
    if parts == ["labels"] and method == "GET":
        return [{"name": k, "color": v["color"], "description": v["description"]} for k, v in sorted(state["labels"].items())]
    if method in ("DELETE", "PUT"):
        raise unsupported(f"{method} is not supported in this environment")
    raise unsupported(f"gh: Not Found (HTTP 404) — unsupported endpoint {method} {path}")


def _gql_thread(state: Dict[str, Any], pr: int, th: Dict[str, Any]) -> Dict[str, Any]:
    nodes = []
    for c in th["comments"]:
        nodes.append({"id": f"PRRC_kwDO{c['id']}", "databaseId": c["id"], "fullDatabaseId": str(c["id"]),
                      "url": c["url"], "author": {"login": c["author"]}, "body": c["body"], "bodyText": c["body"],
                      "createdAt": c["createdAt"], "path": th["path"], "line": th["line"], "originalLine": th["line"],
                      "outdated": False, "replyTo": {"databaseId": c["in_reply_to_id"]} if c.get("in_reply_to_id") else None})
    return {"id": th["id"], "isResolved": th["isResolved"], "isOutdated": False, "isCollapsed": th["isResolved"],
            "path": th["path"], "line": th["line"], "originalLine": th["line"], "startLine": None, "diffSide": "RIGHT",
            "resolvedBy": {"login": th.get("resolvedBy")} if th.get("resolvedBy") else None,
            "viewerCanResolve": True, "viewerCanReply": True,
            "comments": {"totalCount": len(nodes), "nodes": nodes, "pageInfo": {"hasNextPage": False, "endCursor": None}}}


def _gql_pr(state: Dict[str, Any], p: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    n = p["number"]
    base = pr_json(state, p, ["number", "title", "url", "body", "state", "isDraft", "headRefName", "headRefOid",
                              "baseRefName", "mergeable", "reviewDecision", "id"], ctx)
    threads = [_gql_thread(state, n, th) for th in p.get("threads", [])]
    page = {"hasNextPage": False, "endCursor": None}
    base.update({
        "reviewThreads": {"totalCount": len(threads), "nodes": threads, "pageInfo": page},
        "reviews": {"totalCount": len(p["reviews"]), "pageInfo": page,
                    "nodes": [{**review_json(state, n, r), "comments": {"nodes": []},
                               "url": f"{_base_url(state)}/pull/{n}#pullrequestreview-{r['id']}"} for r in p["reviews"]]},
        "comments": {"totalCount": len(p["comments"]), "pageInfo": page,
                     "nodes": [comment_json(state, c) for c in p["comments"]]},
    })
    return base


def _gql_var(query: str, params: Dict[str, Any], names: Tuple[str, ...], inline: str) -> Any:
    for k in names:
        if k in params:
            return params[k]
    m = re.search(inline, query)
    return m.group(1) if m else None


def _graphql(ctx: Ctx, state: Dict[str, Any], params: Dict[str, Any]) -> Any:
    query = params.get("query", "")
    if not query:
        raise GhError("gh: A query attribute must be specified and must be a string. (HTTP 400)")
    q = re.sub(r"\s+", " ", query)
    if re.search(r"\b(resolveReviewThread|unresolveReviewThread)\b", q):
        unresolve = "unresolveReviewThread" in q
        tid = _gql_var(q, params, ("threadId", "id", "thread"), r'threadId:\s*"([^"]+)"')
        th, _ = _find_thread(state, lambda t: t["id"] == tid)
        if th is None:
            raise GhError(f"GraphQL: Could not resolve to a node with the global id of '{tid}' (resolveReviewThread)")
        th["isResolved"] = not unresolve
        th["resolvedBy"] = None if unresolve else state["login"]
        ctx.dirty = True
        key = "unresolveReviewThread" if unresolve else "resolveReviewThread"
        return {"data": {key: {"thread": {"id": th["id"], "isResolved": th["isResolved"]}}}}
    if "addPullRequestReviewThreadReply" in q:
        tid = _gql_var(q, params, ("threadId", "pullRequestReviewThreadId", "id"), r'pullRequestReviewThreadId:\s*"([^"]+)"')
        body = _gql_var(q, params, ("body",), r'body:\s*"((?:[^"\\]|\\.)*)"')
        th, pr = _find_thread(state, lambda t: t["id"] == tid)
        if th is None or not body:
            raise GhError(f"GraphQL: Could not resolve to a node with the global id of '{tid}' (addPullRequestReviewThreadReply)")
        c = _reply(state, pr, th, body, th["comments"][0]["id"])
        ctx.dirty = True
        return {"data": {"addPullRequestReviewThreadReply": {"comment": {"id": f"PRRC_kwDO{c['id']}", "databaseId": c["id"],
                                                                         "url": c["url"], "body": c["body"]}}}}
    if q.strip().startswith("mutation"):
        raise unsupported("GraphQL: mutation is not supported in this environment")
    data: Dict[str, Any] = {}
    if re.search(r"\bviewer\b", q):
        data["viewer"] = {"login": state["login"], "id": f"U_{state['login']}", "name": ""}
    if re.search(r"\brepository\b", q):
        repo: Dict[str, Any] = {**_repo_obj(state)}
        if re.search(r"\bpullRequest\b", q):
            n = _gql_var(q, params, ("n", "number", "pr", "prNumber", "pull", "pullNumber", "num"),
                         r"pullRequest\(\s*number:\s*(\d+)")
            p = state["prs"].get(str(n)) if n is not None else None
            if p is None:
                raise GhError(f"GraphQL: Could not resolve to a PullRequest with the number of {n}. (repository.pullRequest)")
            repo["pullRequest"] = _gql_pr(state, p, ctx)
        elif re.search(r"\b(issue|issueOrPullRequest)\b", q):
            n = _gql_var(q, params, ("n", "number", "issue", "num"), r"issue(?:OrPullRequest)?\(\s*number:\s*(\d+)")
            i = state["issues"].get(str(n)) if n is not None else None
            if i is None:
                raise GhError(f"GraphQL: Could not resolve to an Issue with the number of {n}. (repository.issue)")
            key = "issueOrPullRequest" if "issueOrPullRequest" in q else "issue"
            repo[key] = issue_json(state, i, ["number", "title", "body", "state", "url", "id"], ctx)
            repo[key]["comments"] = {"nodes": [comment_json(state, c) for c in i["comments"]]}
        data["repository"] = repo
    if not data:
        raise unsupported("GraphQL: query is not supported in this environment")
    return {"data": data}


# ---------------------------------------------------------------------------
# 進入點
# ---------------------------------------------------------------------------

COMMANDS: Dict[str, Callable[[Ctx, Dict[str, Any], List[str]], None]] = {
    "issue": cmd_issue, "pr": cmd_pr, "label": cmd_label, "repo": cmd_repo, "auth": cmd_auth, "api": cmd_api,
}


def run(state_path: Path, args: List[str], cwd: str) -> Tuple[int, str, str, bool]:
    """執行一次假 gh，回傳 (exit code, stdout, stderr, unsupported)。"""
    ctx = Ctx(state_path, cwd)
    clean = list(args)
    code = 0
    with locked(state_path):
        state = load_state(state_path)
        try:
            if "-R" in clean or "--repo" in clean:
                k = clean.index("-R") if "-R" in clean else clean.index("--repo")
                want = clean[k + 1]
                if want not in (f"{state['owner']}/{state['repo']}", _base_url(state)):
                    raise GhError(f"GraphQL: Could not resolve to a Repository with the name '{want}'. (repository)")
                del clean[k:k + 2]
            if not clean or clean[0] in ("--version", "version"):
                ctx.print(f"gh version {VERSION} (2026-05-12)\nhttps://github.com/cli/cli/releases/tag/v{VERSION}")
            elif clean[0] in ("-h", "--help", "help") or "--help" in clean:
                ctx.unsupported = True
                ctx.print("Work seamlessly with GitHub from the command line.")
            elif clean[0] in COMMANDS:
                COMMANDS[clean[0]](ctx, state, clean[1:])
            else:
                raise unsupported(f'unknown command "{clean[0]}" for "gh"\n\nUsage:  gh <command> <subcommand> [flags]')
        except GhError as e:
            code = e.code
            ctx.out.append(e.stdout)
            ctx.eprint(e.msg)
            ctx.unsupported = ctx.unsupported or e.unsupported
        if ctx.dirty and code == 0:
            save_state(state_path, state)
    return code, "".join(ctx.out), "".join(ctx.err), ctx.unsupported


def log_call(state_path: Path, args: List[str], cwd: str, code: int, unsup: bool, stderr: str) -> None:
    rec = {"ts": now(), "argv": args, "cwd": cwd, "exit": code, "unsupported": unsup}
    if code != 0:
        rec["stderr"] = stderr[:500]
    try:
        with open(state_path.parent / "calls.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0] != "--state":
        print("usage: fake_gh.py --state <state.json> <gh args...>", file=sys.stderr)
        return 2
    state_path, args = Path(argv[1]).resolve(), argv[2:]
    cwd = os.getcwd()
    code, out, err, unsup = run(state_path, args, cwd)
    sys.stdout.write(out)
    sys.stderr.write(err)
    log_call(state_path, args, cwd, code, unsup, err)
    return code


if __name__ == "__main__":
    sys.exit(main())
