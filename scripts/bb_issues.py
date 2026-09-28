#!/usr/bin/env python3
"""issue 的認領狀態：誰在做、在哪台電腦、做到哪。

用法：
  bb_issues.py list                  列出還開著、標了 bombolt 的 issue 和各自的狀態（bb-list 動態注入用）
  bb_issues.py status --issue N      這個 issue 現在能不能做（bb-work 第 1 步）
  bb_issues.py mark --issue N --event <事件> [--branch B] [--session-id S] [--pr <網址或編號>] [--closed-prs 7,9]
                                     更新 issue 上的 bombolt 狀態留言
    事件：claim（開始實作）、redo（前一次的 PR 被關掉，重新實作）、takeover（接手別人做到一半的）、
          blocked（停工提問）、unblocked（問題回答了，繼續）、pr（發了 PR）、merged（PR merge 了）、
          fixing（bb-fix 開始修改 PR，要帶 --pr）、fixed（這一輪修改完成，回到審查中）

狀態記在 issue 上**一則**留言裡，每次原地改寫；結尾的 `<!-- bombolt:status {...} -->` 是給腳本讀的。
PR 的狀態（開著／關掉／merge）一律即時問 GitHub：用 branch 名 `bb-<issue>-<slug>` 對應，
slug 來自 issue 內文的 `<!-- bombolt:meta -->`，不看留言。所以留言沒更新到（例如 PR 是在網頁上被關掉的），
判斷也不會錯。

輸出一行 JSON。`list` 會被 skill 動態注入，所以永遠 exit 0，問題寫在 `message`；其他指令失敗時 exit 1。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bb_lib  # noqa: E402

STATUS_RE = re.compile(r"<!-- bombolt:status (\{.*?\}) -->", re.S)
META_RE = re.compile(r"<!-- bombolt:meta (\{.*?\}) -->", re.S)
COMMENT_ID_RE = re.compile(r"#issuecomment-(\d+)")
PR_NUMBER_RE = re.compile(r"(?:/pull/|^#?)(\d+)/?$")

STATE_LABEL = {
    "working": "🟡 實作中",
    "blocked": "⛔ 停工提問中",
    "in_review": "🔵 審查中",
    "fixing": "🟠 修改中",
    "merged": "✅ 已 merge",
}
EVENTS = ("claim", "redo", "takeover", "blocked", "unblocked", "pr", "merged", "fixing", "fixed")


class GhError(RuntimeError):
    pass


def out(**kw: Any) -> None:
    print(json.dumps(kw, ensure_ascii=False))


_ACCOUNTS: Dict[str, Dict[str, Any]] = {}


def gh(repo: Path, args: List[str]) -> str:
    key = str(repo)
    if key not in _ACCOUNTS:
        try:
            _ACCOUNTS[key] = bb_lib.gh_account_env(repo)
        except bb_lib.GhAccountError as e:
            raise GhError(str(e))
    acct = _ACCOUNTS[key]
    p = subprocess.run([acct["gh"], *args], cwd=str(repo), capture_output=True, text=True, env=acct["env"])
    if p.returncode != 0:
        raise GhError((p.stderr or p.stdout).strip()[:300] or f"gh {' '.join(args[:2])} 失敗")
    return p.stdout


def gh_json(repo: Path, args: List[str]) -> Any:
    try:
        return json.loads(gh(repo, args) or "null")
    except ValueError as e:
        raise GhError(f"gh 回傳的不是 JSON：{e}")


# ---------------------------------------------------------------------------
# 讀：issue 內文的 meta、狀態留言、PR
# ---------------------------------------------------------------------------

def issue_meta(body: str) -> Dict[str, Any]:
    m = META_RE.search(body or "")
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


def find_status(comments: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], str]:
    """最新的一則狀態留言：(資料, 留言 id)。沒有的話 (None, "")。"""
    found: Tuple[Optional[Dict[str, Any]], str] = (None, "")
    for c in comments or []:
        m = STATUS_RE.search(c.get("body") or "")
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        cid = COMMENT_ID_RE.search(c.get("url") or "")
        found = (data, cid.group(1) if cid else "")
    return found


def branch_of(number: int, meta: Dict[str, Any], status: Optional[Dict[str, Any]]) -> str:
    if meta.get("slug"):
        return f"bb-{number}-{meta['slug']}"
    return (status or {}).get("branch", "")


def prs_for_branch(repo: Path, branch: str) -> List[Dict[str, Any]]:
    return gh_json(repo, ["pr", "list", "--head", branch, "--state", "all",
                          "--json", "number,state,url,headRefOid,author", "--limit", "20"]) or []


# ---------------------------------------------------------------------------
# 判斷：這個 issue 現在是什麼狀況
# ---------------------------------------------------------------------------

def _nums(prs: List[Dict[str, Any]]) -> str:
    return "、".join(f"#{p['number']}" for p in prs)


def evaluate(number: int, issue_state: str, status: Optional[Dict[str, Any]],
             prs: List[Dict[str, Any]], me: Dict[str, str], blocked_label: bool = False) -> Dict[str, Any]:
    """回傳 verdict 與給人看的 message。verdict：
    free（沒人在做）、mine（這台電腦之前就在做）、working／blocked（別人在做）、
    in_review（有開著的 PR）、redo（只有被關掉、沒 merge 的 PR）、merged、closed。"""
    by: Dict[str, List[Dict[str, Any]]] = {"OPEN": [], "CLOSED": [], "MERGED": []}
    for p in sorted(prs, key=lambda p: p.get("number", 0)):
        by.setdefault(p.get("state", ""), []).append(p)
    status = status or {}
    owner = status.get("owner") or {}
    state = status.get("state", "")
    since = status.get("since", "")
    mine = bool(owner) and bb_lib.same_owner(owner, me)
    res: Dict[str, Any] = {
        "owner": owner or None,
        "owner_text": bb_lib.owner_text(owner) if owner else "",
        "mine": mine,
        "since": since,
        "fixing": False,
        "open_prs": [{"number": p["number"], "url": p.get("url", "")} for p in by["OPEN"]],
        "closed_prs": [{"number": p["number"], "url": p.get("url", ""), "head": p.get("headRefOid", "")}
                       for p in by["CLOSED"]],
        "merged_prs": [{"number": p["number"], "url": p.get("url", "")} for p in by["MERGED"]],
    }

    if issue_state.upper() == "CLOSED":
        return {**res, "verdict": "closed", "message": f"issue #{number} 已經關閉。"}
    if by["MERGED"]:
        return {**res, "verdict": "merged", "message": f"PR {_nums(by['MERGED'])} 已經 merge，這個 issue 做完了。"}
    if by["OPEN"]:
        pr = by["OPEN"][-1]
        if not owner:  # 舊的 issue 沒有狀態留言：只知道 PR 是誰開的，不知道哪台電腦
            login = (pr.get("author") or {}).get("login", "")
            owner = {"github": login} if login else {}
            res.update(owner=owner or None, owner_text=bb_lib.owner_text(owner) if owner else "", mine=False)
        where = "這台電腦" if res["mine"] else (res["owner_text"] or "開 PR 的人的電腦")
        fixing = state == "fixing"
        doing = f"{where}上從 {since} 開始在修改" if fixing else f"最近一次在{where}上修改"
        return {**res, "verdict": "in_review", "fixing": fixing, "message": (
            f"PR #{pr['number']} 還開著，{doing}。"
            f"要修改：在任何一台電腦的主 checkout 執行 `/bombolt:bb-fix {pr['number']}`（會讀 PR 上的紀錄接著改）。"
            f"要重做：先關掉 PR #{pr['number']}。")}
    if state in ("working", "blocked"):
        if mine:
            return {**res, "verdict": "mine", "message": f"這台電腦從 {since} 開始在做這個 issue，接著做。"}
        doing = "，卡在停工提問" if state == "blocked" else "（還沒發 PR）"
        return {**res, "verdict": state, "message": f"{res['owner_text']}上從 {since} 開始在做這個 issue{doing}。"}
    if by["CLOSED"]:
        return {**res, "verdict": "redo", "message": f"前一次的 PR {_nums(by['CLOSED'])} 已經關閉、沒有 merge，可以重做。"}
    if blocked_label:
        return {**res, "verdict": "blocked", "message": "卡在停工提問（有 `bombolt:blocked` label）。"}
    return {**res, "verdict": "free", "message": "沒有人在做，可以認領。"}


# ---------------------------------------------------------------------------
# 寫：狀態留言
# ---------------------------------------------------------------------------

def _who(owner: Dict[str, str]) -> str:
    machine = bb_lib._plain(owner.get("machine", ""))
    return f"{bb_lib.owner_short(owner)}「{machine}」" if machine else bb_lib.owner_short(owner)


def render_status(data: Dict[str, Any]) -> str:
    state = data["state"]
    owner = data.get("owner") or {}
    branch = data.get("branch", "")
    head = f"🤖 **bombolt 狀態：{STATE_LABEL[state]}**"
    if state in ("in_review", "fixing", "merged") and data.get("pr"):
        head += f" — PR #{data['pr']}"
    at = f"實作 session 在 {bb_lib.owner_text(owner)}上"
    detail = {
        "working": f"{at}，正在實作",
        "blocked": f"{at}，等有人回答 issue 留言裡的問題",
        "in_review": (f"最近一次在 {bb_lib.owner_text(owner)}上修改。任何一台電腦都能接著改："
                      f"在主 checkout 執行 `/bombolt:bb-fix {data.get('pr')}`"),
        "fixing": f"{bb_lib.owner_text(owner)}上從 {data.get('since', '')} 開始在修改",
        "merged": f"PR #{data.get('pr')} 已經 merge",
    }[state]
    if branch and state != "merged":
        detail += f" · branch `{branch}`"
    lines = [head, "", detail, ""]
    lines += [f"- {h}" for h in data.get("history", [])]
    payload = json.dumps(data, ensure_ascii=False).replace("-->", "--\\u003e")
    lines += ["", f"<!-- bombolt:status {payload} -->"]
    return "\n".join(lines) + "\n"


def pr_number(value: str) -> Optional[int]:
    m = PR_NUMBER_RE.search((value or "").strip())
    return int(m.group(1)) if m else None


def apply_event(prev: Optional[Dict[str, Any]], event: str, me: Dict[str, str], today: str,
                branch: str = "", session_id: str = "", pr: Optional[int] = None,
                closed_prs: Optional[List[int]] = None) -> Optional[Dict[str, Any]]:
    """算出新的狀態資料；不需要改留言時回 None（例如舊 issue 沒有狀態留言、PR 已經 merge）。"""
    history = list((prev or {}).get("history", []))
    if event in ("claim", "redo", "takeover"):
        if event == "claim":
            line = f"{today} 🟡 開始實作 — {_who(me)}"
        elif event == "redo":
            prs = "、".join(f"#{n}" for n in (closed_prs or [])) or "（編號不明）"
            line = f"{today} 🔁 前一次的 PR {prs} 已關閉（沒有 merge），重新實作 — {_who(me)}"
        else:
            before = bb_lib.owner_short((prev or {}).get("owner") or {})
            line = f"{today} 🔀 接手實作（原本是 {before}）— {_who(me)}"
        return {"state": "working", "owner": me, "branch": branch or (prev or {}).get("branch", ""),
                "session_id": session_id, "since": today, "pr": None, "history": history + [line]}

    if prev is None:
        if event == "merged":
            return None
        prev = {"owner": me, "branch": branch, "session_id": session_id, "since": today, "pr": None}
    data = dict(prev)
    if branch:
        data["branch"] = branch
    if session_id:
        data["session_id"] = session_id
    if event == "blocked":
        data["state"], line = "blocked", f"{today} ⛔ 停工提問，等人回答"
    elif event == "unblocked":
        data["state"], line = "working", f"{today} 🟡 問題已回答，繼續實作"
    elif event == "pr":
        data["state"], data["pr"], line = "in_review", pr, f"{today} 🔵 發 PR #{pr}"
    elif event == "fixing":  # 誰正在改：只是提醒別人，不是鎖（session 當掉就不會清）
        data["state"], data["owner"], data["since"], data["pr"] = "fixing", me, today, pr or data.get("pr")
        line = f"{today} 🟠 開始修改 PR #{data['pr']} — {_who(me)}"
    elif event == "fixed":
        data["state"], data["owner"], data["pr"] = "in_review", me, pr or data.get("pr")
        line = f"{today} 🔵 修改完成，等 review — {_who(me)}"
    else:  # merged
        data["state"], data["pr"], line = "merged", pr or data.get("pr"), f"{today} ✅ PR #{pr or data.get('pr')} 已 merge"
    data["history"] = history + [line]
    return data


def write_status(repo: Path, number: int, comment_id: str, body: str) -> str:
    """改寫既有的狀態留言；沒有、或改不了（例如沒有權限改別人的留言）就新增一則。回傳留言網址。"""
    fd, tmp = tempfile.mkstemp(prefix="bombolt-status-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"body": body}, f, ensure_ascii=False)
        if comment_id:
            try:
                res = gh_json(repo, ["api", "-X", "PATCH", f"repos/{{owner}}/{{repo}}/issues/comments/{comment_id}",
                                     "--input", tmp])
                return (res or {}).get("html_url", "")
            except GhError:
                pass
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(body)
        return gh(repo, ["issue", "comment", str(number), "--body-file", tmp]).strip()
    finally:
        os.unlink(tmp)


def mark(repo: Path, number: int, event: str, branch: str = "", session_id: str = "", pr: str = "",
         closed_prs: Optional[List[int]] = None) -> Dict[str, Any]:
    issue = gh_json(repo, ["issue", "view", str(number), "--json", "comments"]) or {}
    prev, comment_id = find_status(issue.get("comments", []))
    me = bb_lib.owner_info(repo)
    today = _dt.date.today().isoformat()
    data = apply_event(prev, event, me, today, branch=branch, session_id=session_id,
                       pr=pr_number(pr) if pr else None, closed_prs=closed_prs)
    if data is None:
        return {"status": "skipped", "message": "這個 issue 沒有 bombolt 狀態留言，不另外新增。"}
    url = write_status(repo, number, comment_id, render_status(data))
    notes = []
    if event in ("claim", "redo", "takeover"):
        try:
            gh(repo, ["issue", "edit", str(number), "--add-assignee", "@me"])
        except GhError as e:
            notes.append(f"沒有把自己加成 assignee：{e}")
    return {"status": "ok", "state": data["state"], "comment_url": url, "notes": notes}


# ---------------------------------------------------------------------------
# 指令
# ---------------------------------------------------------------------------

def _repo_or_exit(exit_code: int) -> Path:
    repo = bb_lib.main_checkout(Path.cwd())
    if repo is None:
        out(status="error", message="目前目錄不在 git repo 裡。")
        sys.exit(exit_code)
    return repo


def cmd_list(args: argparse.Namespace) -> None:
    repo = _repo_or_exit(0)
    try:
        issues = gh_json(repo, ["issue", "list", "--label", "bombolt", "--state", "open", "--json",
                                "number,title,url,assignees,labels,body,comments", "--limit", "100"]) or []
        prs = gh_json(repo, ["pr", "list", "--state", "all", "--json",
                             "number,state,url,headRefName,headRefOid,author", "--limit", "300"]) or []
    except GhError as e:
        out(status="error", message=str(e))
        return
    by_head: Dict[str, List[Dict[str, Any]]] = {}
    for p in prs:
        by_head.setdefault(p.get("headRefName", ""), []).append(p)
    me = bb_lib.owner_info(repo)

    wtdir = repo / bb_lib.WORKTREES_REL
    local_names = []
    if wtdir.is_dir():
        local_names = [d.name for d in wtdir.iterdir() if d.name.startswith("bb-") and not d.name.startswith("_")]

    result = []
    for i in issues:
        n = i["number"]
        status, _ = find_status(i.get("comments", []))
        branch = branch_of(n, issue_meta(i.get("body", "")), status)
        labels = [l["name"] for l in i.get("labels", [])]
        ev = evaluate(n, "OPEN", status, by_head.get(branch, []) if branch else [], me,
                      blocked_label="bombolt:blocked" in labels)
        matches = [name for name in local_names if name.startswith(f"bb-{n}-")]
        result.append({
            "number": n,
            "title": i["title"],
            "url": i["url"],
            "verdict": ev["verdict"],
            "message": ev["message"],
            "owner_text": ev["owner_text"],
            "mine": ev["mine"],
            "open_prs": ev["open_prs"],
            "closed_prs": ev["closed_prs"],
            "assigned": bool(i.get("assignees")),
            "blocked": "bombolt:blocked" in labels,
            "local_worktree": matches[0] if matches else None,
        })
    out(status="ok", issues=result)


def cmd_status(args: argparse.Namespace) -> None:
    repo = _repo_or_exit(1)
    try:
        issue = gh_json(repo, ["issue", "view", str(args.issue), "--json",
                               "number,title,url,state,body,labels,comments"]) or {}
        status, _ = find_status(issue.get("comments", []))
        branch = branch_of(args.issue, issue_meta(issue.get("body", "")), status)
        prs = prs_for_branch(repo, branch) if branch else []
    except GhError as e:
        out(status="error", message=str(e))
        sys.exit(1)
    labels = [l["name"] for l in issue.get("labels", [])]
    ev = evaluate(args.issue, issue.get("state", "OPEN"), status, prs, bb_lib.owner_info(repo),
                  blocked_label="bombolt:blocked" in labels)
    # 接手別人做到一半的 issue 時，遠端不能已經有同名的 branch（對方推過），否則最後 push 不上去
    remote = bb_lib.remote_tip(repo, branch) if branch else ""
    out(status="ok", issue=args.issue, title=issue.get("title", ""), branch=branch,
        remote_branch=remote, **ev)


def cmd_mark(args: argparse.Namespace) -> None:
    repo = _repo_or_exit(1)
    closed = [int(x) for x in re.findall(r"\d+", args.closed_prs or "")]
    try:
        res = mark(repo, args.issue, args.event, branch=args.branch, session_id=args.session_id,
                   pr=args.pr, closed_prs=closed)
    except GhError as e:
        out(status="error", message=str(e))
        sys.exit(1)
    out(**res)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(func=cmd_list)
    s = sub.add_parser("status")
    s.add_argument("--issue", required=True, type=int)
    s.set_defaults(func=cmd_status)
    m = sub.add_parser("mark")
    m.add_argument("--issue", required=True, type=int)
    m.add_argument("--event", required=True, choices=EVENTS)
    m.add_argument("--branch", default="")
    m.add_argument("--session-id", default="")
    m.add_argument("--pr", default="", help="PR 網址或編號（event 是 pr、merged、fixing、fixed 時）")
    m.add_argument("--closed-prs", default="", help="被關掉的 PR 編號，逗號分隔（event 是 redo 時）")
    m.set_defaults(func=cmd_mark)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
