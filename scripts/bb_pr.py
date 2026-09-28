#!/usr/bin/env python3
"""PR 就是交接本：換一個新的 session、換一台電腦、換一個人，都靠 PR 上寫的東西接著改。

- PR 內文：現在的樣子，每一輪整段覆寫。最下面「🔁 要修改的話」那一段由這支腳本產生。
- PR 留言：修改紀錄，一輪一則，只新增、不改別人的。同一個 session 之後補充的決定，附加在自己最新的那一則。
  第一行（第幾輪、誰、哪台電腦、日期、commit 範圍、驗收過沒）由腳本寫，內文由 agent 寫。
- 程式碼才是事實：最後一則紀錄之後才出現的 commit（例如同事沒用 bombolt 直接 push），
  `context` 會列出來，接手的 agent 要補記，而且要重新驗收。

用法（N 可以是 PR 編號，也可以是 issue 編號）：
  bb_pr.py context --pr N [--session-id S]
      接手需要的：PR、所有修改紀錄、沒有紀錄的 commit、最新的 commit 驗收過沒、有沒有人正在改、本機有沒有 worktree。
  bb_pr.py record --pr N --session-id S --kind round|catchup|sync --summary "一句話" --body-file F [--verified]
      貼一則修改紀錄（要先 push），然後更新 PR 內文的「🔁 要修改的話」。
      round：一輪修改（bb-work 發 PR 是第 1 輪）；catchup：補記沒有經過 bombolt 的 commit；sync：發版後同步。
      --verified：verifier 在這個 commit 上全部通過（交給人測的 ⚠️ 不算沒過）。
  bb_pr.py note --pr N --session-id S --text "..." [--summary "一句話"]
      不用改 code、但會影響之後怎麼改的決定。最新那則紀錄是這個 session 寫的就附加在它後面，否則新貼一則「備註」。

輸出一行 JSON；失敗時 exit 1，stderr 說明原因。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bb_issues  # noqa: E402
import bb_lib  # noqa: E402
from bb_issues import GhError  # noqa: E402

RECORD_RE = re.compile(r"<!-- bombolt:record (\{.*?\}) -->", re.S)
BRANCH_RE = re.compile(r"^bb-(\d+)-([a-z0-9]+(?:-[a-z0-9]+)*)$")
PR_FIELDS = "number,url,title,state,isDraft,headRefName,headRefOid,baseRefName,body,comments"
HANDOFF_HEADING = "## 🔁 要修改的話"
HANDOFF_PLACEHOLDER = "<!-- bombolt:handoff -->"  # PR 模板裡的位置，第一次 record 時換成下面那一段
HANDOFF_START = "<!-- bombolt:handoff:start -->"
HANDOFF_END = "<!-- bombolt:handoff:end -->"
KINDS = ("round", "catchup", "sync")
MAX_COMMITS = 30
NOTES_HEADING = "**之後補充**"


def fail(msg: str) -> None:
    print(f"bombolt: {msg}", file=sys.stderr)
    sys.exit(1)


def out(**kw: Any) -> None:
    print(json.dumps(kw, ensure_ascii=False))


def repo_or_fail() -> Path:
    repo = bb_lib.main_checkout(Path.cwd())
    if repo is None:
        fail("目前目錄不在 git repo 裡。")
    return repo


# ---------------------------------------------------------------------------
# 讀：PR、紀錄、commit
# ---------------------------------------------------------------------------

def resolve(repo: Path, number: int) -> Dict[str, Any]:
    """PR 或 issue 編號 → PR（含內文與留言），多一個 `issue` 欄位。
    issue 編號：找它的 bombolt branch 上最新開著的 PR。"""
    try:
        pr = bb_issues.gh_json(repo, ["pr", "view", str(number), "--json", PR_FIELDS])
    except GhError:
        pr = None
    if not pr:
        try:
            issue = bb_issues.gh_json(repo, ["issue", "view", str(number), "--json", "body,comments"]) or {}
        except GhError as e:
            raise GhError(f"#{number} 不是 PR，也查不到這個編號的 issue（{e}）。")
        status, _ = bb_issues.find_status(issue.get("comments", []))
        branch = bb_issues.branch_of(number, bb_issues.issue_meta(issue.get("body", "")), status)
        if not branch:
            raise GhError(f"issue #{number} 不是 bombolt 開的（沒有 bombolt:meta），找不到它的 PR。")
        prs = bb_issues.prs_for_branch(repo, branch)
        live = sorted((p for p in prs if p.get("state") == "OPEN"), key=lambda p: p["number"])
        if not live:
            seen = "、".join(f"#{p['number']}（{p.get('state')}）" for p in prs) or "一個都沒有"
            raise GhError(f"issue #{number} 沒有開著的 PR（這個 issue 的 PR：{seen}）。")
        pr = bb_issues.gh_json(repo, ["pr", "view", str(live[-1]["number"]), "--json", PR_FIELDS])
    m = BRANCH_RE.match(pr.get("headRefName", ""))
    if not m:
        raise GhError(f"PR #{pr.get('number')} 的 branch `{pr.get('headRefName')}` 不是 bombolt 建的（bb-<issue>-<slug>）。")
    pr["issue"], pr["slug"] = int(m.group(1)), m.group(2)
    return pr


def parse_records(comments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """PR 留言裡的修改紀錄，照時間排（gh 回傳的順序）。"""
    recs = []
    for c in comments or []:
        m = RECORD_RE.search(c.get("body") or "")
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        cid = bb_issues.COMMENT_ID_RE.search(c.get("url") or "")
        recs.append({**data, "url": c.get("url", ""), "comment_id": cid.group(1) if cid else "",
                     "comment_body": c.get("body") or ""})
    return recs


def last_with_commit(recs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return next((r for r in reversed(recs) if r.get("to")), None)


def fetch(repo: Path, *refs: str) -> bool:
    return bb_lib.git_ok(["fetch", "--quiet", "origin", *refs], repo)


def has_commit(repo: Path, sha: str) -> bool:
    return bool(sha) and bb_lib.git_ok(["cat-file", "-e", f"{sha}^{{commit}}"], repo)


def is_ancestor(repo: Path, a: str, b: str) -> bool:
    return bb_lib.git_ok(["merge-base", "--is-ancestor", a, b], repo)


def commits(repo: Path, rng: str) -> List[Dict[str, str]]:
    text = bb_lib.git(["log", "--reverse", "--date=short", "--format=%H%x1f%an%x1f%ad%x1f%s", rng], repo, check=False)
    result = []
    for line in text.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            result.append({"sha": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]})
    return result


def short(sha: str) -> str:
    return (sha or "")[:7]


def title_of(r: Dict[str, Any]) -> str:
    kind = r.get("kind")
    return f"第 {r.get('n')} 輪" if kind == "round" else {"catchup": "補記", "sync": "同步", "note": "備註"}.get(kind, "紀錄")


# ---------------------------------------------------------------------------
# 寫：紀錄留言、PR 內文的「🔁 要修改的話」
# ---------------------------------------------------------------------------

def _safe(text: str) -> str:
    """commit 訊息放進留言：不要 @ 到人、不要被當成 HTML。"""
    return text.replace("@", "@​").replace("<", "&lt;")


def render_record(data: Dict[str, Any], text: str, commit_list: List[Dict[str, str]]) -> str:
    kind = data["kind"]
    who = bb_lib.owner_text(data.get("owner") or {})
    frm, to = data.get("from", ""), data.get("to", "")
    rng = f"`{short(frm)}` → `{short(to)}`" if frm and frm != to else f"`{short(to)}`"
    parts = [data["date"], rng]
    if kind != "note":
        parts.append("✅ 驗收過這個 commit" if data.get("verified") else "⚠️ 這個 commit 還沒驗收")
    if kind == "catchup":
        authors = "、".join(_safe(a) for a in data.get("authors") or []) or "（不明）"
        byline = f"沒有經過 bombolt 的改動（{authors}），由 {who}從 diff 整理 · " + " · ".join(parts)
    else:
        byline = f"{who} · " + " · ".join(parts)
    lines = [f"🤖 **bombolt 紀錄 · {title_of(data)}** — {data.get('summary', '')}", "", byline, "",
             "<details>", "<summary>展開紀錄（接手的人和 agent 看這裡）</summary>", "", text.strip(), ""]
    if commit_list:
        lines.append("**這段範圍的 commit**")
        lines += [f"- `{short(c['sha'])}` {_safe(c['subject'])} — {_safe(c['author'])}" for c in commit_list[:MAX_COMMITS]]
        if len(commit_list) > MAX_COMMITS:
            lines.append(f"- …另外 {len(commit_list) - MAX_COMMITS} 個")
        lines.append("")
    payload = json.dumps(data, ensure_ascii=False).replace("-->", "--\\u003e")
    lines += ["</details>", "", f"<!-- bombolt:record {payload} -->"]
    return "\n".join(lines) + "\n"


def add_note(comment_body: str, line: str) -> str:
    """把一行補充的決定加在紀錄的折疊區最後面（`</details>` 前）。"""
    i = comment_body.rfind("</details>")
    if i < 0:
        i = comment_body.find("<!-- bombolt:record")
    head, tail = comment_body[:i].rstrip("\n"), comment_body[i:]
    if NOTES_HEADING not in head:
        head += f"\n\n{NOTES_HEADING}"
    return f"{head}\n{line}\n\n{tail}"


def _tmp(content: str) -> str:
    fd, path = tempfile.mkstemp(prefix="bombolt-pr-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def put_comment(repo: Path, number: int, comment_id: str, body: str) -> str:
    """改寫既有的紀錄留言；沒有、或改不了就新增一則。回傳留言網址。"""
    if comment_id:
        tmp = _tmp(json.dumps({"body": body}, ensure_ascii=False))
        try:
            res = bb_issues.gh_json(repo, ["api", "-X", "PATCH", f"repos/{{owner}}/{{repo}}/issues/comments/{comment_id}",
                                           "--input", tmp])
            return (res or {}).get("html_url", "")
        except GhError:
            pass
        finally:
            os.unlink(tmp)
    tmp = _tmp(body)
    try:
        return bb_issues.gh(repo, ["pr", "comment", str(number), "--body-file", tmp]).strip()
    finally:
        os.unlink(tmp)


def handoff_block(pr: Dict[str, Any], recs: List[Dict[str, Any]]) -> str:
    n, branch = pr["number"], pr["headRefName"]
    repo_name = (pr.get("url", "").split("/") + [""] * 5)[4] or "repo"
    lines = [HANDOFF_START,
             "任何人、任何一台電腦都可以接著改：在這個 PR 留 review comment（或直接在 session 裡說），"
             "然後在 repo 的主 checkout 開一個新的 Claude Code session：", "",
             "```bash", f'claude -n bb-{repo_name}-{pr["issue"]}-{pr["slug"]} --permission-mode auto "/bombolt:bb-fix {n}"',
             "```", "",
             "它會讀 issue、這個 PR 的「📌 需求調整」、下面的修改歷程和還沒解決的 review，從上一次停下的地方接著改。"]
    if recs:
        last = recs[-1]
        lines += ["", f"最近一次：{title_of(last)} · {bb_lib.owner_text(last.get('owner') or {})} · "
                      f"{last.get('date', '')} · branch `{branch}`"]
        if last.get("session"):
            lines.append(f"想接回那次的對話（只有那台電腦可以）：`claude --resume {last['session']}`")
        lines += ["", "**🔄 修改歷程**（細節在每一則 PR 留言裡）"]
        for r in recs:
            link = f"[{title_of(r)}]({r['url']})" if r.get("url") else title_of(r)
            lines.append(f"- {link} · {r.get('date', '')} · {bb_issues._who(r.get('owner') or {})} · {r.get('summary', '')}")
    lines.append(HANDOFF_END)
    return "\n".join(lines)


def put_handoff(body: str, block: str) -> str:
    if HANDOFF_START in body and HANDOFF_END in body:
        s = body.index(HANDOFF_START)
        e = body.index(HANDOFF_END, s) + len(HANDOFF_END)
        return body[:s] + block + body[e:]
    if HANDOFF_PLACEHOLDER in body:
        return body.replace(HANDOFF_PLACEHOLDER, block, 1)
    if HANDOFF_HEADING in body:  # 這個功能之前開的 PR：整段換掉（到下一個折疊區、Closes 或結尾）
        s = body.index(HANDOFF_HEADING) + len(HANDOFF_HEADING)
        rest = body[s:]
        ends = [i for i in (rest.find("\n<details>"), rest.find("\nCloses #")) if i >= 0]
        e = min(ends) if ends else len(rest)
        return body[:s] + "\n\n" + block + "\n" + rest[e:]
    return body.rstrip() + f"\n\n{HANDOFF_HEADING}\n\n{block}\n"


def sync_body(repo: Path, pr: Dict[str, Any], recs: List[Dict[str, Any]]) -> None:
    body = pr.get("body") or ""
    new = put_handoff(body, handoff_block(pr, recs))
    if new == body:
        return
    tmp = _tmp(new)
    try:
        bb_issues.gh(repo, ["pr", "edit", str(pr["number"]), "--body-file", tmp])
    finally:
        os.unlink(tmp)


def _open_pr_or_fail(repo: Path, number: int) -> Dict[str, Any]:
    try:
        pr = resolve(repo, number)
    except GhError as e:
        fail(str(e))
    if pr.get("state") != "OPEN":
        fail(f"PR #{pr['number']} 不是開著的（{pr.get('state')}），不用記。")
    return pr


def _pushed_tip_or_fail(repo: Path, branch: str) -> str:
    tip = bb_lib.remote_tip(repo, branch)
    if not tip:
        fail(f"查不到遠端的 `{branch}`（網路？還是 branch 被刪了？）。")
    wt = repo / bb_lib.WORKTREES_REL / branch
    if wt.is_dir():
        local = bb_lib.git(["rev-parse", "HEAD"], wt, check=False).strip()
        if local and local != tip:
            fail(f"本機的 `{branch}`（{short(local)}）跟 origin（{short(tip)}）不一樣。"
                 f"紀錄只記已經 push 的 commit：先 `git push`（或先把 origin 的拉下來），再記。")
    return tip


# ---------------------------------------------------------------------------
# 指令
# ---------------------------------------------------------------------------

def cmd_context(args: argparse.Namespace) -> None:
    repo = repo_or_fail()
    try:
        pr = resolve(repo, args.pr)
        issue = bb_issues.gh_json(repo, ["issue", "view", str(pr["issue"]), "--json", "comments"]) or {}
    except GhError as e:
        fail(str(e))
    branch, base = pr["headRefName"], pr.get("baseRefName", "")
    recs = parse_records(pr.get("comments"))
    fetched = fetch(repo, branch, base)
    head = bb_lib.remote_tip(repo, branch) or pr.get("headRefOid", "")
    msgs: List[str] = []

    last = last_with_commit(recs)
    unrecorded: List[Dict[str, str]] = []
    rewritten = False
    if last is None:
        msgs.append("這個 PR 還沒有 bombolt 修改紀錄（這個功能之前開的）：以 PR 內文和 diff 為準，這一輪要重新驗收。")
    elif not fetched or not has_commit(repo, head):
        msgs.append(f"fetch `{branch}` 失敗，沒辦法比對紀錄之後有沒有新的 commit：先查清楚再動手。")
    elif last["to"] != head:
        if has_commit(repo, last["to"]) and is_ancestor(repo, last["to"], head):
            unrecorded = commits(repo, f"{last['to']}..{head}")
        else:
            rewritten = True
            unrecorded = commits(repo, f"origin/{base}..{head}")
            msgs.append(f"⚠️ 最後一則紀錄的 commit `{short(last['to'])}` 已經不在 branch 上了（有人 force push、改寫了歷史）："
                        "以現在的 branch 為準，補記要涵蓋整個 PR 的 diff。")
        if unrecorded and not rewritten:
            who = "、".join(sorted({c["author"] for c in unrecorded}))
            msgs.append(f"最後一則紀錄之後有 {len(unrecorded)} 個沒有紀錄的 commit（{who}）："
                        "讀 diff、補記（--kind catchup），這一輪要重新驗收。")
    verified_head = any(r.get("verified") and r.get("to") == head for r in recs)
    if last is not None and not verified_head and not unrecorded:
        msgs.append(f"最新的 commit `{short(head)}` 還沒有驗收過：這一輪要重新驗收。")

    me = bb_lib.owner_info(repo)
    status, _ = bb_issues.find_status(issue.get("comments", []))
    lock = None
    if status and status.get("state") == "fixing":
        owner, sid = status.get("owner") or {}, status.get("session_id", "")
        lock = {"owner_text": bb_lib.owner_text(owner), "since": status.get("since", ""), "session_id": sid,
                "mine": bb_lib.same_owner(owner, me), "this_session": bool(args.session_id) and sid == args.session_id,
                "session_alive_here": bool(sid) and any(s.get("sessionId") == sid for s in bb_lib.claude_sessions())}
        if not lock["this_session"]:
            msgs.append(f"{lock['owner_text']}上從 {lock['since']} 開始在修改這個 PR（還沒有改回「審查中」）。")

    wt = repo / bb_lib.WORKTREES_REL / branch
    out(status="ok",
        pr={"number": pr["number"], "url": pr.get("url", ""), "title": pr.get("title", ""), "state": pr.get("state", ""),
            "draft": bool(pr.get("isDraft")), "branch": branch, "base": base, "head": head},
        issue=pr["issue"],
        records=[{k: r.get(k) for k in ("kind", "n", "summary", "date", "from", "to", "verified", "session", "url")}
                 | {"who": bb_lib.owner_text(r.get("owner") or {}), "comment": r["comment_body"]} for r in recs],
        unrecorded_commits=unrecorded, history_rewritten=rewritten, verified_head=verified_head,
        lock=lock, local_worktree=str(wt) if wt.is_dir() else None, messages=msgs)


def cmd_record(args: argparse.Namespace) -> None:
    repo = repo_or_fail()
    text = Path(args.body_file).read_text(encoding="utf-8").strip()
    if not text:
        fail(f"{args.body_file} 是空的。")
    pr = _open_pr_or_fail(repo, args.pr)
    branch = pr["headRefName"]
    tip = _pushed_tip_or_fail(repo, branch)
    fetch(repo, branch, pr.get("baseRefName", ""))
    recs = parse_records(pr.get("comments"))

    # 同一個 session 重跑（沒有新的 commit）：改寫那一則，不重複貼
    same = recs[-1] if recs and recs[-1].get("session") == args.session_id and \
        recs[-1].get("kind") == args.kind and recs[-1].get("to") == tip else None
    if same:
        frm, n = same.get("from", ""), same.get("n")
    else:
        last = last_with_commit(recs)
        frm = last["to"] if last else bb_lib.git(["merge-base", f"origin/{pr.get('baseRefName', '')}", tip], repo,
                                                check=False).strip()
        n = sum(1 for r in recs if r.get("kind") == "round") + 1 if args.kind == "round" else None
    rng = f"{frm}..{tip}" if frm and has_commit(repo, frm) and is_ancestor(repo, frm, tip) else ""
    commit_list = commits(repo, rng) if rng else []
    data = {"kind": args.kind, "n": n, "summary": args.summary, "date": _dt.date.today().isoformat(),
            "from": frm, "to": tip, "verified": bool(args.verified), "owner": bb_lib.owner_info(repo),
            "session": args.session_id}
    if args.kind == "catchup":
        data["authors"] = sorted({c["author"] for c in commit_list})
    try:
        url = put_comment(repo, pr["number"], same["comment_id"] if same else "", render_record(data, text, commit_list))
        rec = {**data, "url": url}
        recs = recs[:-1] + [rec] if same else recs + [rec]
        sync_body(repo, pr, recs)
    except GhError as e:
        fail(str(e))
    out(status="ok", url=url, kind=args.kind, n=n, updated=bool(same), commits=len(commit_list))


def cmd_note(args: argparse.Namespace) -> None:
    repo = repo_or_fail()
    pr = _open_pr_or_fail(repo, args.pr)
    recs = parse_records(pr.get("comments"))
    me = bb_lib.owner_info(repo)
    today = _dt.date.today().isoformat()
    line = f"- {today} {bb_issues._who(me)}：{args.text.strip()}"
    last = recs[-1] if recs else None
    try:
        if last and last.get("session") == args.session_id and last.get("comment_id"):
            url = put_comment(repo, pr["number"], last["comment_id"], add_note(last["comment_body"], line))
            if url != last.get("url"):  # 改不了（例如改別人的留言被拒）而新增了一則
                recs.append({**last, "url": url})
            appended = True
        else:
            tip = bb_lib.remote_tip(repo, pr["headRefName"]) or pr.get("headRefOid", "")
            data = {"kind": "note", "n": None, "summary": args.summary or args.text.strip()[:40], "date": today,
                    "from": tip, "to": tip, "verified": False, "owner": me, "session": args.session_id}
            url = put_comment(repo, pr["number"], "", render_record(data, f"{NOTES_HEADING}\n{line}", []))
            recs.append({**data, "url": url})
            appended = False
        sync_body(repo, pr, recs)
    except GhError as e:
        fail(str(e))
    out(status="ok", url=url, appended=appended)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("context")
    c.add_argument("--pr", required=True, type=int, help="PR 或 issue 編號")
    c.add_argument("--session-id", default="")
    c.set_defaults(func=cmd_context)
    r = sub.add_parser("record")
    r.add_argument("--pr", required=True, type=int, help="PR 或 issue 編號")
    r.add_argument("--session-id", required=True)
    r.add_argument("--kind", required=True, choices=KINDS)
    r.add_argument("--summary", required=True, help="一句話，會出現在折疊的標題和 PR 內文的修改歷程")
    r.add_argument("--body-file", required=True)
    r.add_argument("--verified", action="store_true")
    r.set_defaults(func=cmd_record)
    n = sub.add_parser("note")
    n.add_argument("--pr", required=True, type=int, help="PR 或 issue 編號")
    n.add_argument("--session-id", required=True)
    n.add_argument("--text", required=True)
    n.add_argument("--summary", default="")
    n.set_defaults(func=cmd_note)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
