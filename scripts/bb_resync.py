#!/usr/bin/env python3
"""發版之後，把還沒被這波帶走的 feature worktree／PR 追上最新的 base_branch，並重新整合進 integration_branches。
由 bb-fix 使用：在某個 PR 的 session 裡只同步那一個，在主 checkout 則一次同步全部。

適用情境：人手動發完一波版之後，把 `integration_branches`（例如 stage）reset 回最新的 `base_branch`
（`pr_base` 跟 `base_branch` 不同時，`pr_base` 也一起 reset）——這時候**這波沒被帶走**的 PR 還開著，
但已經不在 integration branch 裡了；`pr_base` 跟 `base_branch` 不同的話，它們的 feature branch 也還停在舊的 base。

用法（在哪個目錄執行都可以，不用先進某個 worktree）：
  bb_resync.py list                      → 列出候選（本機有 worktree、有開向 pr_base 的 PR），附上每個需不需要同步
  bb_resync.py check  --path <worktree> [--draft]
                                         → 唯讀：落後 origin/<base_branch> 幾個 commit、是否還在每個 integration_branches 裡，
                                            以及 needs_sync（要不要同步）與理由。draft PR 本來就不整合，加 --draft 只看落後
  bb_resync.py start  --path <worktree> [--no-integrate]
                                         → 把最新的 origin/<base_branch> merge 進這個 feature branch；
                                            乾淨就直接 push，並重新整合進每個 integration_branches（draft PR 加 --no-integrate）
  bb_resync.py finish --path <worktree> [--no-integrate]
                                         → 衝突解完、git add 之後：完成 merge、push、重新整合
  bb_resync.py abort  --path <worktree>  → 放棄這次 merge（git merge --abort）

什麼時候算「需要同步」（needs_sync）：
  - 已經不在某個 integration_branches 裡（通常是發版後被 reset 掉了），或
  - `pr_base` 跟 `base_branch` 不同、而且落後 origin/<base_branch>（PR 開向 base_branch 時，落後是正常的，
    GitHub 會在 merge 時處理，不需要為了這個同步）

跟 bb_integrate.py 的方向相反：bb_integrate 是「feature → 暫存 worktree → target」，用完即丟；
這裡是「最新的 base_branch → feature 自己的 worktree」，結果就是更新後的 PR 本身，
所以直接在 feature worktree 裡做、push 回它自己的 branch（安全守門本來就准許這件事，
不需要像 bb_integrate 那樣為了安全另外借一個暫存 worktree）。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bb_lib  # noqa: E402

CONFLICT_MARK = re.compile(r"^(<{7}|={7}|>{7})( |$)", re.M)


def out(**kw) -> None:
    print(json.dumps(kw, ensure_ascii=False))


def cmd_list(_args: argparse.Namespace) -> None:
    repo = bb_lib.main_checkout(Path.cwd())
    if repo is None:
        out(status="error", message="目前目錄不在 git repo 裡。")
        sys.exit(1)
    cfg = bb_lib.load_config(repo)
    if cfg is None:
        out(status="error", message=f"{repo} 還沒有 {bb_lib.CONFIG_REL}。")
        sys.exit(1)
    base_branch = cfg.get("base_branch", "")
    target = bb_lib.pr_base(cfg)
    if not base_branch or not target:
        out(status="error", message=f"{bb_lib.CONFIG_REL} 沒有設定 base_branch。")
        sys.exit(1)
    try:
        acct = bb_lib.gh_account_env(repo)
    except bb_lib.GhAccountError as e:
        out(status="error", message=str(e))
        sys.exit(1)

    bb_lib.git(["fetch", "--quiet", "origin", base_branch, *cfg.get("integration_branches", [])], repo, check=False)
    wtdir = repo / bb_lib.WORKTREES_REL
    candidates = []
    for p in sorted(wtdir.iterdir()) if wtdir.is_dir() else []:
        meta = bb_lib.read_meta(p)
        if not meta or meta.get("tool") != "bombolt" or meta.get("integration"):
            continue
        branch = meta.get("branch", "")
        proc = subprocess.run(
            [acct["gh"], "pr", "list", "--head", branch, "--base", target, "--state", "open",
             "--json", "number,url,isDraft"],
            cwd=str(repo), capture_output=True, text=True, env=acct["env"],
        )
        if proc.returncode != 0:
            candidates.append({"path": str(p), "branch": branch, "issue": meta.get("issue"),
                              "error": proc.stderr.strip() or "gh 失敗"})
            continue
        prs = json.loads(proc.stdout or "[]")
        if not prs:
            continue  # 沒有開向 pr_base 的 open PR：已經被這波帶走，或不是走這條路徑
        draft = bool(prs[0].get("isDraft"))
        wt_cfg = bb_lib.load_worktree_config(p, repo) or cfg
        candidates.append({"path": str(p), "branch": branch, "issue": meta.get("issue"), "pr": prs[0],
                           "draft": draft, **_sync_state(p, wt_cfg, draft=draft)})
    out(status="ok", base_branch=base_branch, pr_base=target, candidates=candidates)


def _load_ctx(path_str: str):
    path = Path(path_str)
    meta = bb_lib.read_meta(path)
    if not meta or meta.get("tool") != "bombolt" or meta.get("integration"):
        out(status="error", message=f"{path_str} 不是 bombolt 的 feature worktree。")
        sys.exit(1)
    repo = Path(meta["repo"])
    cfg = bb_lib.load_worktree_config(path, repo) or {}
    return meta, repo, cfg, path


def _push(path: Path, branch: str) -> bool:
    return bb_lib.git_ok(["push", "origin", f"HEAD:{branch}"], path)


def _reintegrate(path: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """呼叫 bb_integrate.py 把這個 worktree 現在的內容重新合進每個 integration_branches。"""
    results: Dict[str, Any] = {}
    script = str(Path(__file__).with_name("bb_integrate.py"))
    for target in cfg.get("integration_branches", []):
        p = subprocess.run([sys.executable, script, "start", "--target", target],
                           cwd=str(path), capture_output=True, text=True)
        line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""
        try:
            results[target] = json.loads(line)
        except ValueError:
            results[target] = {"status": "error", "message": (p.stdout + p.stderr)[:500]}
    return results


def _sync_state(path: Path, cfg: Dict[str, Any], draft: bool = False) -> Dict[str, Any]:
    """唯讀：落後多少、還在不在每個 integration branch 裡、要不要同步（呼叫前要先 fetch）。"""
    base_branch = cfg.get("base_branch", "")
    applicable = bb_lib.pr_base(cfg) != base_branch
    behind_s = bb_lib.git(["rev-list", "--count", f"HEAD..origin/{base_branch}"], path, check=False).strip()
    behind = int(behind_s) if behind_s.isdigit() else None
    integrated = {t: bb_lib.git_ok(["merge-base", "--is-ancestor", "HEAD", f"origin/{t}"], path)
                  for t in cfg.get("integration_branches", [])}
    reasons = []
    missing = [t for t, ok in integrated.items() if not ok]
    if missing and not draft:
        reasons.append(f"已經不在 {', '.join(missing)} 裡（發版後被 reset 掉了？）")
    if applicable and behind:
        reasons.append(f"落後 origin/{base_branch} {behind} 個 commit")
    return {"applicable": applicable, "behind": behind, "integrated": integrated,
            "needs_sync": bool(reasons), "reasons": reasons}


def _merge_latest_base(path: Path, base_branch: str, repo: Path):
    bb_lib.git(["fetch", "--quiet", "origin", base_branch], repo)
    msg = f"Merge origin/{base_branch} into {path.name} (bombolt: sync after release)"
    p = subprocess.run(["git", "merge", "--no-ff", "-m", msg, f"origin/{base_branch}"],
                       cwd=str(path), capture_output=True, text=True)
    if p.returncode != 0:
        files = bb_lib.git(["diff", "--name-only", "--diff-filter=U"], path, check=False).split()
        if files:
            return "conflict", files
        raise RuntimeError(f"merge 失敗：{p.stdout}{p.stderr}")
    return "merged", None


def cmd_check(args: argparse.Namespace) -> None:
    _meta, repo, cfg, path = _load_ctx(args.path)
    base_branch = cfg.get("base_branch", "")
    if not base_branch:
        out(status="error", message=f"{bb_lib.CONFIG_REL} 沒有設定 base_branch。")
        sys.exit(1)
    targets = cfg.get("integration_branches", [])
    bb_lib.git(["fetch", "--quiet", "origin", base_branch, *targets], repo, check=False)
    out(status="ok", base_branch=base_branch, pr_base=bb_lib.pr_base(cfg), **_sync_state(path, cfg, draft=args.draft))


def cmd_start(args: argparse.Namespace) -> None:
    meta, repo, cfg, path = _load_ctx(args.path)
    if bb_lib.git(["status", "--porcelain"], path, check=False).strip():
        out(status="error", message="這個 worktree 有未 commit 的改動，請先處理再 resync。", path=str(path))
        sys.exit(1)
    base_branch = cfg.get("base_branch", "")
    if not base_branch:
        out(status="error", message=f"{bb_lib.CONFIG_REL} 沒有設定 base_branch。")
        sys.exit(1)
    try:
        status, files = _merge_latest_base(path, base_branch, repo)
    except RuntimeError as e:
        out(status="error", message=str(e), path=str(path))
        sys.exit(1)
    if status == "conflict":
        out(status="conflict", path=str(path), files=files,
            next="在這個 worktree 裡解完衝突（保留雙方意圖）、git add，然後執行 finish；放棄執行 abort。")
        return
    if not _push(path, meta["branch"]):
        out(status="error", message="push 被拒（別人剛好也推了這個 branch？），請確認後手動處理。", path=str(path))
        sys.exit(1)
    out(status="merged", path=str(path), reintegrated={} if args.no_integrate else _reintegrate(path, cfg))


def cmd_finish(args: argparse.Namespace) -> None:
    meta, repo, cfg, path = _load_ctx(args.path)
    unmerged = bb_lib.git(["diff", "--name-only", "--diff-filter=U"], path, check=False).split()
    if unmerged:
        out(status="conflict", path=str(path), files=unmerged, message="還有檔案沒 git add。")
        sys.exit(1)
    staged = bb_lib.git(["diff", "--cached", "--name-only"], path, check=False).split()
    marked = [f for f in staged if (path / f).is_file() and CONFLICT_MARK.search((path / f).read_text(errors="ignore"))]
    if marked:
        out(status="conflict", path=str(path), files=marked, message="這些檔案還有衝突標記（<<<<<<< / ======= / >>>>>>>）。")
        sys.exit(1)
    if bb_lib.git_ok(["rev-parse", "-q", "--verify", "MERGE_HEAD"], path):
        bb_lib.git(["commit", "--no-edit"], path)
    if not _push(path, meta["branch"]):
        out(status="error", message="push 被拒，請確認後手動處理。", path=str(path))
        sys.exit(1)
    out(status="merged", path=str(path), resolved_conflicts=True,
        reintegrated={} if args.no_integrate else _reintegrate(path, cfg))


def cmd_abort(args: argparse.Namespace) -> None:
    _meta, _repo, _cfg, path = _load_ctx(args.path)
    bb_lib.git(["merge", "--abort"], path, check=False)
    out(status="aborted", path=str(path))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(func=cmd_list)
    for name, fn in (("check", cmd_check), ("start", cmd_start), ("finish", cmd_finish), ("abort", cmd_abort)):
        s = sub.add_parser(name)
        s.add_argument("--path", required=True)
        if name == "check":
            s.add_argument("--draft", action="store_true", help="draft PR：不整合，只看落後")
        if name in ("start", "finish"):
            s.add_argument("--no-integrate", action="store_true", help="只合最新的 base、push，不整合（draft PR 用）")
        s.set_defaults(func=fn)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
