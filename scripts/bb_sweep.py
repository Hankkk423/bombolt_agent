#!/usr/bin/env python3
"""清理已經完成的 worktree。預設只列出（dry-run），加 --apply 才真的刪。

用法：
  bb_sweep.py [repo 路徑 ...] [--apply] [--json]
  （不給路徑：~/.claude.json 記的專案裡，有 .claude/worktrees/ 的 repo 全部處理；一個都找不到才用目前所在的 repo）

只處理 `<repo>/.claude/worktrees/` 底下的 worktree。**任何一條成立就保留**：
  1. 有活著的 Claude session 在用它（cwd 在裡面，或 metadata 記的 session 還開著）
  2. 被 git worktree lock 鎖住（Claude Code 在跑 agent 時會鎖）
  3. 有未 commit 或 untracked 的檔案
  4. 有還沒 push 的 commit
  5. 找不到對應的 PR，或 PR 還沒 merge（OPEN / CLOSED 都保留，交給人判斷）
  6. 本地 branch 的 HEAD 跟 PR merge 時的 head commit 不一樣（merge 之後又多了東西）
  7. 查不到上面任何一項（gh 失敗、git 失敗）——查不到就當成「不能刪」

判斷「merge 了沒」一律看 GitHub 的 PR 狀態，不用 `git branch --merged`：
squash merge 之後那個 branch 在 git 眼裡永遠是沒 merge。

刪的時候做：`git worktree remove`（不加 --force）＋ 刪本地 branch ＋ 關 issue ＋ **刪遠端 branch**。

遠端 branch 比本地更難救回，所以在「這個 worktree 可以刪」之外，**另外全部成立**才刪（`--keep-remote` 可整個關掉）：
  a. 它是 bombolt 建的 feature branch：metadata 的 branch 就是它，而且名字是 `bb-<issue>-<slug>`
  b. 不在任何受保護的名單裡（內建的 main/master/prod/production/release、設定檔的 protected_branches、
     base_branch、pr_base、integration_branches）——雙重保險
  c. 遠端**現在**的 tip（`git ls-remote` 即時查，不看本機快取）== PR merge 時的 head
     ⇒ merge 之後沒有任何人再推東西上去
  d. 沒有任何「還開著」的 PR 以它當 head 或當 base
  e. 刪除用 `--force-with-lease=<branch>:<c 查到的 sha>`：推的那一刻遠端若已經變了，git 會拒絕
遠端早就不在了（例如 GitHub 設定了 merge 後自動刪）就直接略過。
刪掉的 sha 會印出來；GitHub 的 PR 頁面也有「Restore branch」可以救回。

另外也掃**沒有 worktree 的本地 branch**（例如手動建的 feature branch，PR merge 後本地還留著）。
只刪本地 branch（`git branch -D`），不碰遠端、不關 issue。**全部成立**才刪：
  i.   沒有被任何 worktree（含主 checkout）checkout
  ii.  不在受保護名單（內建＋設定檔，同上）
  iii. GitHub 上有以它當 head、狀態 MERGED 的 PR，而且沒有還開著的 PR 用它當 head
  iv.  本地 tip == PR merge 時的 head（merge 之後本地沒有再多東西）
沒有 PR 的 branch 不列出（那不是「已經 merge 的 branch」）；查不到就保留。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bb_issues  # noqa: E402
import bb_lib  # noqa: E402


def list_worktrees(repo: Path) -> List[Dict[str, Any]]:
    out = bb_lib.git(["worktree", "list", "--porcelain"], repo)
    items: List[Dict[str, Any]] = []
    cur: Dict[str, Any] = {}
    for line in out.splitlines() + [""]:
        if not line.strip():
            if cur:
                items.append(cur)
            cur = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            cur["path"] = value
        elif key == "branch":
            cur["branch"] = value.replace("refs/heads/", "")
        elif key == "HEAD":
            cur["head"] = value
        elif key in ("locked", "prunable", "detached", "bare"):
            cur[key] = value or True
    return items


def gh_pr_for_branch(repo: Path, branch: str) -> Dict[str, Any]:
    try:
        acct = bb_lib.gh_account_env(repo)  # 雙帳號：用這個 repo 指定的帳號查
    except bb_lib.GhAccountError as e:
        return {"error": str(e)}
    proc = subprocess.run(
        [acct["gh"], "pr", "list", "--head", branch, "--state", "all",
         "--json", "number,state,url,headRefOid,mergedAt", "--limit", "5"],
        cwd=str(repo), capture_output=True, text=True, env=acct["env"],
    )
    if proc.returncode != 0:
        return {"error": proc.stderr.strip() or "gh 失敗"}
    prs = json.loads(proc.stdout or "[]")
    if not prs:
        return {"none": True}
    merged = [p for p in prs if p.get("state") == "MERGED"]
    return {"pr": (merged or prs)[0], "open": [p["number"] for p in prs if p.get("state") == "OPEN"]}


FEATURE_BRANCH_RE = re.compile(r"^bb-\d+-[a-z0-9]+(?:-[a-z0-9]+)*$")
BUILTIN_PROTECTED = ["main", "master", "prod", "production", "release"]


def protected_branches(repo: Path, meta: Dict[str, Any]) -> List[str]:
    cfg = bb_lib.load_config(repo) or {}
    names = (BUILTIN_PROTECTED + cfg.get("protected_branches", []) + cfg.get("integration_branches", [])
             + [cfg.get("base_branch", ""), bb_lib.pr_base(cfg) if cfg else "", meta.get("base", "")])
    return [n for n in dict.fromkeys(names) if n]


def open_prs_touching(repo: Path, branch: str) -> Optional[List[int]]:
    """還開著、以這個 branch 當 head 或 base 的 PR 編號；查不到回 None（當成不能刪）。"""
    try:
        acct = bb_lib.gh_account_env(repo)
    except bb_lib.GhAccountError:
        return None
    found: List[int] = []
    for flag in ("--head", "--base"):
        p = subprocess.run([acct["gh"], "pr", "list", flag, branch, "--state", "open", "--json", "number"],
                           cwd=str(repo), capture_output=True, text=True, env=acct["env"])
        if p.returncode != 0:
            return None
        try:
            found += [x["number"] for x in json.loads(p.stdout or "[]")]
        except (ValueError, KeyError, TypeError):
            return None
    return found


remote_tip = bb_lib.remote_tip


def assess_remote(repo: Path, branch: str, meta: Dict[str, Any], pr: Dict[str, Any]) -> Dict[str, Any]:
    """決定遠端 branch 刪不刪。回傳 {"delete": bool, "sha": ..., "reason": ...}；任何一條不確定就不刪。"""
    if not meta or meta.get("tool") != "bombolt" or meta.get("integration") or meta.get("branch") != branch:
        return {"delete": False, "reason": "不是 bombolt 建的 feature branch，遠端不動"}
    if not FEATURE_BRANCH_RE.match(branch):
        return {"delete": False, "reason": f"名字不是 bb-<issue>-<slug> 的形狀，遠端不動"}
    if branch in protected_branches(repo, meta):
        return {"delete": False, "reason": "在受保護的名單裡，遠端不動"}
    tip = remote_tip(repo, branch)
    if tip is None:
        return {"delete": False, "reason": "查不到遠端狀態，遠端不動"}
    if tip == "":
        return {"delete": False, "reason": "遠端已經沒有這個 branch", "gone": True}
    if not pr.get("headRefOid") or tip != pr["headRefOid"]:
        return {"delete": False, "reason": f"遠端 tip {tip[:8]} 跟 PR merge 時的 head {str(pr.get('headRefOid'))[:8]} 不同（merge 後又有人推？），遠端不動"}
    others = open_prs_touching(repo, branch)
    if others is None:
        return {"delete": False, "reason": "查不到有沒有別的 PR 在用它，遠端不動"}
    if others:
        return {"delete": False, "reason": f"還有開著的 PR 用到它（{', '.join('#' + str(n) for n in others)}），遠端不動"}
    return {"delete": True, "sha": tip, "reason": f"遠端 branch 也會刪（tip {tip[:8]} == PR merge 時的 head）"}


def delete_remote(repo: Path, branch: str, sha: str) -> str:
    p = subprocess.run(["git", "push", "--delete", f"--force-with-lease=refs/heads/{branch}:{sha}",
                        "origin", f"refs/heads/{branch}"], cwd=str(repo), capture_output=True, text=True)
    if p.returncode != 0:
        return f"遠端 branch 沒刪（{(p.stderr or p.stdout).strip()[:200]}）"
    return f"已刪除遠端 branch `{branch}`（原本指向 {sha}；要救回：`git push origin {sha}:refs/heads/{branch}`，或 PR 頁面的 Restore branch）"


def assess(repo: Path, wt: Dict[str, Any], sessions: List[Dict[str, Any]]) -> Dict[str, Any]:
    path = wt["path"]
    branch = wt.get("branch", "")
    meta = bb_lib.read_meta(path) or {}
    keep: List[str] = []
    info: Dict[str, Any] = {"path": path, "branch": branch, "issue": meta.get("issue"),
                            "session_id": meta.get("session_id"), "managed": bool(meta)}

    if not Path(path).exists():
        return {**info, "action": "prune", "reasons": ["目錄已經不在了（git worktree prune 會清掉記錄）"]}

    for s in sessions:
        cwd = s.get("cwd", "")
        if cwd == path or cwd.startswith(path.rstrip("/") + "/"):
            keep.append(f"有 Claude session 正在這裡工作（pid {s.get('pid')}，{s.get('name', '')}）")
        elif meta.get("session_id") and s.get("sessionId") == meta.get("session_id"):
            keep.append(f"建立它的 session 還開著（pid {s.get('pid')}，{s.get('name', '')}）")

    if wt.get("locked"):
        keep.append(f"被 git worktree lock 鎖住（{wt['locked'] if wt['locked'] is not True else '無說明'}）")

    if wt.get("detached") or not branch:
        keep.append("detached HEAD，不是 bombolt 的工作 branch")

    try:
        status = bb_lib.git(["status", "--porcelain"], path)
        if status.strip():
            n = len(status.strip().splitlines())
            keep.append(f"有 {n} 個未 commit / untracked 的檔案")
    except RuntimeError as e:
        keep.append(f"讀不到 git status：{e}")

    pr_info: Dict[str, Any] = {}
    if branch:
        remote_ref = f"refs/remotes/origin/{branch}"
        has_remote = bb_lib.git_ok(["show-ref", "--verify", "--quiet", remote_ref], repo)
        pr_info = gh_pr_for_branch(repo, branch)
        pr = pr_info.get("pr")
        info["pr"] = pr
        local_head = wt.get("head", "")

        if "error" in pr_info:
            keep.append(f"查不到 PR 狀態：{pr_info['error']}")
        elif pr_info.get("none"):
            keep.append("還沒有 PR（工作可能還沒做完）")
        elif pr.get("state") != "MERGED":
            keep.append(f"PR #{pr['number']} 狀態是 {pr['state']}，還沒 merge")
        else:
            if pr.get("headRefOid") and local_head and pr["headRefOid"] != local_head:
                keep.append(f"本地 HEAD {local_head[:8]} 跟 PR merge 時的 head {pr['headRefOid'][:8]} 不同（merge 之後本地又有新 commit？）")

        if has_remote and local_head:
            ahead = bb_lib.git(["rev-list", "--count", f"origin/{branch}..{local_head}"], repo, check=False)
            if ahead.strip() not in ("", "0"):
                keep.append(f"有 {ahead.strip()} 個 commit 還沒 push")
        elif not has_remote and not (pr and pr.get("state") == "MERGED"):
            keep.append("branch 從來沒有 push 過")

    reasons = keep or ["PR 已 merge、工作目錄乾淨、沒有 session 在用（--apply 時會一併關閉對應的 issue）"]
    if not keep:
        info["remote"] = assess_remote(repo, branch, meta, pr_info.get("pr") or {})
        reasons = reasons + [info["remote"]["reason"]]
    return {**info, "action": "keep" if keep else "remove", "reasons": reasons}


def close_issue(repo: Path, issue_number: Any, pr_number: Any) -> str:
    """PR 已經 merge 了：把它對應的 issue 關掉，保持乾淨。

    ⚠️ PR 內文本來就有 `Closes #<issue>`，GitHub 在偵測到 PR 合併時通常會自動關閉——
    但那個偵測依賴「GitHub 認得這是同一個 PR 的合併」，使用者手動用 git 操作（例如批次把
    多個 PR merge 進一支中繼 branch）時不一定會觸發。這裡改成明確關閉，不依賴那個自動偵測，
    對已經關閉的 issue 呼叫也安全（gh 會直接說已經是關的，不算錯誤）。
    """
    try:
        acct = bb_lib.gh_account_env(repo)
    except bb_lib.GhAccountError as e:
        return f"issue #{issue_number} 沒關成：{e}"
    p = subprocess.run(
        [acct["gh"], "issue", "close", str(issue_number),
         "--comment", f"✅ PR #{pr_number} 已經 merge，由 `bombolt:bb-sweep` 清理 worktree 時一併關閉。"],
        cwd=str(repo), capture_output=True, text=True, env=acct["env"],
    )
    if p.returncode != 0:
        return f"issue #{issue_number} 沒關成：{(p.stderr or p.stdout).strip()[:200]}"
    msg = f"issue #{issue_number} 已關閉"
    try:  # issue 上的 bombolt 狀態留言改成「已 merge」；更新失敗不影響清理
        bb_issues.mark(repo, int(issue_number), "merged", pr=str(pr_number))
    except (bb_issues.GhError, ValueError) as e:
        msg += f"（狀態留言沒更新：{e}）"
    return msg


def remove(repo: Path, item: Dict[str, Any], keep_remote: bool = False) -> str:
    if item["action"] == "prune":
        bb_lib.git(["worktree", "prune"], repo)
        return "已 prune"
    try:
        bb_lib.git(["worktree", "remove", item["path"]], repo)
    except RuntimeError as e:
        return f"❌ 沒刪成：{e}（遠端 branch 也不動）"
    msg = "已刪除 worktree"
    branch = item.get("branch")
    if branch:
        # squash merge 之後 `branch -d` 一定拒絕，所以用 -D；
        # 安全性來自上面已經確認過「本地 HEAD == PR merge 時的 head」。
        try:
            bb_lib.git(["branch", "-D", branch], repo)
            msg += f"與本地 branch `{branch}`"
        except RuntimeError as e:
            msg += f"（branch 沒刪成：{e}）"
    issue_number = item.get("issue")
    pr = item.get("pr") or {}
    if issue_number and pr.get("number"):
        msg += "；" + close_issue(repo, issue_number, pr["number"])
    remote = item.get("remote") or {}
    if branch and remote.get("delete") and not keep_remote:
        msg += "；" + delete_remote(repo, branch, remote["sha"])
    return msg


def local_branches(repo: Path) -> List[Dict[str, str]]:
    out = bb_lib.git(["for-each-ref", "--format=%(refname:short)%09%(objectname)", "refs/heads/"], repo)
    return [dict(zip(("branch", "head"), line.split("\t"))) for line in out.splitlines() if line.strip()]


def assess_branch(repo: Path, branch: str, head: str, protected: List[str]) -> Optional[Dict[str, Any]]:
    """沒有 worktree 的本地 branch：PR 已 merge 且本地 tip == merge 時的 head 才刪。沒有 PR 回 None（不列出）。"""
    info: Dict[str, Any] = {"branch": branch, "head": head}
    if branch in protected:
        return None
    pr_info = gh_pr_for_branch(repo, branch)
    if "error" in pr_info:
        return {**info, "action": "keep", "reasons": [f"查不到 PR 狀態：{pr_info['error']}"]}
    if pr_info.get("none"):
        return None
    pr = pr_info["pr"]
    if pr.get("state") != "MERGED":
        return None  # 還沒 merge 的 branch 不是這裡要清的東西
    info["pr"] = pr
    keep: List[str] = []
    if pr_info.get("open"):
        keep.append(f"還有開著的 PR 用它當 head（{', '.join('#' + str(n) for n in pr_info['open'])}）")
    if pr.get("headRefOid") != head:
        keep.append(f"本地 tip {head[:8]} 跟 PR merge 時的 head {str(pr.get('headRefOid'))[:8]} 不同（merge 之後本地又有新 commit？）")
    reasons = keep or [f"PR 已 merge、本地 tip == merge 時的 head（只刪本地 branch；要救回：`git branch {branch} {head}`）"]
    return {**info, "action": "keep" if keep else "remove", "reasons": reasons}


def delete_branch(repo: Path, item: Dict[str, Any]) -> str:
    # 再確認一次 tip 沒變（dry-run 與 --apply 之間、或同一次執行中有人動了它）
    now = bb_lib.git(["rev-parse", "--verify", "--quiet", f"refs/heads/{item['branch']}"], repo, check=False).strip()
    if now != item["head"]:
        return f"❌ 沒刪：branch 已經變動（現在指向 {now[:8] or '不存在'}）"
    try:
        # squash merge 之後 `branch -d` 一定拒絕，所以用 -D；安全性來自「本地 tip == PR merge 時的 head」。
        bb_lib.git(["branch", "-D", item["branch"]], repo)
    except RuntimeError as e:
        return f"❌ 沒刪成：{e}"
    return f"已刪除本地 branch `{item['branch']}`（原本指向 {item['head']}）"


def sweep_branches(repo: Path, apply: bool) -> List[Dict[str, Any]]:
    checked_out = {wt.get("branch") for wt in list_worktrees(repo) if wt.get("branch")}
    protected = protected_branches(repo, {})
    results = []
    for b in local_branches(repo):
        if b["branch"] in checked_out:
            continue
        item = assess_branch(repo, b["branch"], b["head"], protected)
        if item is None:
            continue
        if apply and item["action"] == "remove":
            item["result"] = delete_branch(repo, item)
        results.append(item)
    return results


def sweep_repo(repo_arg: str, apply: bool, keep_remote: bool = False) -> Dict[str, Any]:
    repo = bb_lib.main_checkout(Path(repo_arg).expanduser())
    if repo is None:
        return {"repo": repo_arg, "error": "不是 git repo"}
    managed_root = str(repo / bb_lib.WORKTREES_REL)
    try:
        bb_lib.git(["fetch", "--quiet", "--prune", "origin"], repo)
    except RuntimeError as e:
        return {"repo": str(repo), "error": f"git fetch 失敗（離線？）：{e}。不 fetch 就判斷不準，這次不處理。"}
    sessions = bb_lib.claude_sessions()
    results = []
    for wt in list_worktrees(repo):
        if not wt["path"].startswith(managed_root + "/"):
            continue
        item = assess(repo, wt, sessions)
        if keep_remote and item.get("remote", {}).get("delete"):
            item["reasons"] = item["reasons"][:-1] + ["遠端 branch 不動（--keep-remote）"]
        if apply and item["action"] in ("remove", "prune"):
            item["result"] = remove(repo, item, keep_remote)
        results.append(item)
    # worktree 先處理完：刪掉的 worktree 其 branch 也已經刪了，不會在這裡重複出現
    return {"repo": str(repo), "worktrees": results, "branches": sweep_branches(repo, apply)}


def render(report: Dict[str, Any], apply: bool) -> str:
    lines = [f"## {report['repo']}"]
    if report.get("error"):
        return "\n".join(lines + [f"⚠️ {report['error']}", ""])
    wts = report["worktrees"]
    icon = {"keep": "🟢 保留", "remove": "🗑️ 可刪" if not apply else "🗑️ 刪除", "prune": "🧹 prune"}
    lines.append("### worktree")
    if not wts:
        lines.append("（`.claude/worktrees/` 底下沒有 worktree）")
    for it in wts:
        name = Path(it["path"]).name
        pr = it.get("pr") or {}
        pr_s = f" · PR #{pr['number']} {pr['state']}" if pr else ""
        lines.append(f"- {icon[it['action']]} `{name}`{pr_s}")
        for r in it["reasons"]:
            lines.append(f"    - {r}")
        if it.get("result"):
            lines.append(f"    - ➜ {it['result']}")
    lines.append("### 沒有 worktree、PR 已 merge 的本地 branch")
    brs = report.get("branches", [])
    if not brs:
        lines.append("（沒有）")
    for it in brs:
        pr = it.get("pr") or {}
        pr_s = f" · PR #{pr['number']} {pr['state']}" if pr else ""
        lines.append(f"- {icon[it['action']]} `{it['branch']}`{pr_s}")
        for r in it["reasons"]:
            lines.append(f"    - {r}")
        if it.get("result"):
            lines.append(f"    - ➜ {it['result']}")
    return "\n".join(lines + [""])


def known_repos() -> List[str]:
    """~/.claude.json 記的每個專案目錄 → 主 checkout，只留有 .claude/worktrees/ 的；讀不到回空清單。

    ⚠️ ~/.claude.json 不是官方文件記載的介面，格式可能改變；找不到時使用者可以自己給路徑。
    """
    try:
        data = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8"))
        projects = list(data.get("projects") or {})
    except (OSError, ValueError, AttributeError, TypeError):
        return []
    repos = []
    for p in projects:
        repo = bb_lib.main_checkout(p) if Path(p).is_dir() else None
        if repo and (repo / bb_lib.WORKTREES_REL).is_dir():
            repos.append(str(repo))
    return sorted(set(repos))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("repos", nargs="*")
    p.add_argument("--apply", action="store_true", help="真的刪除（預設只列出）")
    p.add_argument("--json", action="store_true")
    p.add_argument("--keep-remote", action="store_true", help="遠端 branch 一律不刪")
    args = p.parse_args()
    repos = args.repos or known_repos() or ["."]
    reports = [sweep_repo(r, args.apply, args.keep_remote) for r in repos]
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        print("\n".join(render(r, args.apply) for r in reports))
        if not args.apply and any(i["action"] != "keep" for r in reports
                                  for i in r.get("worktrees", []) + r.get("branches", [])):
            print("（這是 dry-run。確認後加 --apply 才會真的刪。）")


if __name__ == "__main__":
    main()
