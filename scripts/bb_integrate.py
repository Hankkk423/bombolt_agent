#!/usr/bin/env python3
"""把 feature branch 用 merge commit（--no-ff）整合進 integration branch（例如 dev＝stage）。

用法（在 bombolt worktree 裡執行）：
  bb_integrate.py start  --target dev    → fetch 最新 origin/dev，在暫存 worktree 裡 merge，成功就 push
  bb_integrate.py finish --target dev    → 衝突解完之後：檢查沒有衝突標記、完成 merge commit、push
  bb_integrate.py abort  --target dev    → 放棄這次整合，刪掉暫存 worktree（dev 不受影響）

設計：
- target 必須列在 `.claude/bombolt.md` 的 integration_branches，否則拒絕。
- merge 在獨立的暫存 worktree（`.claude/worktrees/_integrate-<feature>-<target>`）裡做，
  完全不動 feature worktree 與使用者的主 checkout。
- 只做一般 push（不 force）。push 被拒（別人剛好也推了 target）會重新 fetch、重做一次。
- 有衝突時暫存 worktree 會留著給 agent 解：它的 metadata 讓安全守門只允許 push 到 target。

輸出一行 JSON：status = merged / conflict / error。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bb_lib  # noqa: E402

CONFLICT_MARK = re.compile(r"^(<{7}|={7}|>{7})( |$)", re.M)


def out(**kw) -> None:
    print(json.dumps(kw, ensure_ascii=False))


def context(target: str):
    meta = bb_lib.read_meta(Path.cwd())
    if not meta or meta.get("tool") != "bombolt" or meta.get("integration"):
        out(status="error", message="請在 bombolt 建的 feature worktree 裡執行。")
        sys.exit(1)
    repo = Path(meta["repo"])
    cfg = bb_lib.load_worktree_config(Path.cwd(), repo) or {}
    allowed = cfg.get("integration_branches", [])
    if target not in allowed:
        out(status="error", message=f"`{target}` 不在 {bb_lib.CONFIG_REL} 的 integration_branches（目前：{allowed}）。")
        sys.exit(1)
    tmp = repo / bb_lib.WORKTREES_REL / f"_integrate-{meta['branch']}-{target}"
    return meta, repo, tmp


def remove_tmp(repo: Path, tmp: Path) -> None:
    if tmp.exists():
        subprocess.run(["git", "worktree", "remove", "--force", str(tmp)], cwd=str(repo), capture_output=True)
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    subprocess.run(["git", "worktree", "prune"], cwd=str(repo), capture_output=True)


def push(tmp: Path, target: str) -> bool:
    p = subprocess.run(["git", "push", "origin", f"HEAD:{target}"], cwd=str(tmp), capture_output=True, text=True)
    return p.returncode == 0


def attempt(meta, repo: Path, tmp: Path, target: str):
    """做一次完整的整合嘗試。回傳 ('merged', sha) / ('conflict', files) / ('push_rejected', None)。"""
    remove_tmp(repo, tmp)
    bb_lib.git(["fetch", "--quiet", "origin", target], repo)
    bb_lib.git(["worktree", "add", "--detach", str(tmp), f"origin/{target}"], repo)
    bb_lib.write_meta(tmp, {"tool": "bombolt", "integration": True, "branch": target, "base": target,
                            "repo": str(repo), "path": str(tmp), "feature": meta["branch"],
                            "issue": meta.get("issue")})
    bb_lib.copy_config_snapshot(meta.get("path") or Path.cwd(), tmp)  # 暫存 worktree 的守門沿用 feature 的設定
    msg =f"Merge branch '{meta['branch']}' into {target}"
    p = subprocess.run(["git", "merge", "--no-ff", "-m", msg, meta["branch"]], cwd=str(tmp),
                       capture_output=True, text=True)
    if p.returncode != 0:
        files = bb_lib.git(["diff", "--name-only", "--diff-filter=U"], tmp, check=False).split()
        if files:
            return "conflict", files
        raise RuntimeError(f"merge 失敗：{p.stdout}{p.stderr}")
    if not push(tmp, target):
        return "push_rejected", None
    return "merged", bb_lib.git(["rev-parse", "HEAD"], tmp)


def cmd_start(args) -> None:
    meta, repo, tmp = context(args.target)
    try:
        for _ in range(3):
            status, data = attempt(meta, repo, tmp, args.target)
            if status == "merged":
                remove_tmp(repo, tmp)
                out(status="merged", target=args.target, merge_commit=data)
                return
            if status == "conflict":
                out(status="conflict", target=args.target, path=str(tmp), files=data,
                    next="到 path 裡解完衝突（保留雙方的意圖）並 git add，然後執行 finish；放棄就執行 abort。")
                return
        remove_tmp(repo, tmp)
        out(status="error", message=f"push 到 {args.target} 連續被拒（別人一直在推？），請稍後再試。")
        sys.exit(1)
    except RuntimeError as e:
        remove_tmp(repo, tmp)
        out(status="error", message=str(e))
        sys.exit(1)


def cmd_finish(args) -> None:
    meta, repo, tmp = context(args.target)
    if not tmp.exists():
        out(status="error", message="沒有進行中的整合（先執行 start）。")
        sys.exit(1)
    unmerged = bb_lib.git(["diff", "--name-only", "--diff-filter=U"], tmp, check=False).split()
    if unmerged:
        out(status="conflict", path=str(tmp), files=unmerged, message="還有檔案沒 git add。")
        sys.exit(1)
    staged = bb_lib.git(["diff", "--cached", "--name-only"], tmp, check=False).split()
    marked = [f for f in staged if (tmp / f).is_file() and CONFLICT_MARK.search((tmp / f).read_text(errors="ignore"))]
    if marked:
        out(status="conflict", path=str(tmp), files=marked, message="這些檔案還有衝突標記（<<<<<<< / ======= / >>>>>>>）。")
        sys.exit(1)
    if bb_lib.git_ok(["rev-parse", "-q", "--verify", "MERGE_HEAD"], tmp):
        bb_lib.git(["commit", "--no-edit"], tmp)
    if not push(tmp, args.target):
        out(status="error", message=f"push 到 {args.target} 被拒（解衝突期間別人推了新的 commit）。"
                                    f"請執行 abort 再重新 start（會拿最新的 {args.target} 重做）。", path=str(tmp))
        sys.exit(1)
    sha = bb_lib.git(["rev-parse", "HEAD"], tmp)
    remove_tmp(repo, tmp)
    out(status="merged", target=args.target, merge_commit=sha, resolved_conflicts=True)


def cmd_abort(args) -> None:
    meta, repo, tmp = context(args.target)
    remove_tmp(repo, tmp)
    out(status="aborted", target=args.target)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("start", cmd_start), ("finish", cmd_finish), ("abort", cmd_abort)):
        s = sub.add_parser(name)
        s.add_argument("--target", required=True)
        s.set_defaults(func=fn)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
