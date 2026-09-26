#!/usr/bin/env python3
"""規劃用的唯讀快照：把 origin/<base> 的最新版解開成一個普通目錄，給規劃 session 探索。

用法：
  bb_snapshot.py create [--base <branch>]   → 印出 JSON {"path","base","sha"}
  bb_snapshot.py remove <path>

為什麼不直接看主 checkout：使用者的主 checkout 可能停在別的 branch、或有沒 commit 的改動，
規劃要以「remote 上最新的 base」為準。
為什麼不用 git worktree：快照只拿來讀，用 git archive 解開就好，不會在 `git worktree list`
留下記錄；就算規劃 session 中途掛掉，留下的也只是一個可以直接刪的普通目錄。
快照放在 `.claude/worktrees/_plan-*`（已被 .git/info/exclude 忽略），讀檔不會跳出 repo 範圍。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bb_lib  # noqa: E402


def fail(msg: str) -> None:
    print(f"bombolt: {msg}", file=sys.stderr)
    sys.exit(1)


def cmd_create(args: argparse.Namespace) -> None:
    repo = bb_lib.main_checkout(Path.cwd())
    if repo is None:
        fail("目前目錄不在 git repo 裡。")
    cfg = bb_lib.load_config(repo) or {}
    base = args.base or cfg.get("base_branch")
    if not base:
        fail(f"不知道 base branch：{bb_lib.CONFIG_REL} 不存在或沒有 base_branch，請先執行 /bombolt:bb-setup。")
    bb_lib.ensure_local_exclude(repo, f"/{bb_lib.WORKTREES_REL}/")
    try:
        bb_lib.git(["fetch", "--quiet", "origin", base], repo)
    except RuntimeError as e:
        fail(f"fetch origin/{base} 失敗：{e}")
    sha = bb_lib.git(["rev-parse", f"origin/{base}"], repo)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = repo / bb_lib.WORKTREES_REL / f"_plan-{stamp}"
    dest.mkdir(parents=True)
    archive = subprocess.run(["git", "archive", "--format=tar", sha], cwd=str(repo), capture_output=True)
    if archive.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        fail(f"git archive 失敗：{archive.stderr.decode(errors='replace')}")
    untar = subprocess.run(["tar", "-x", "-C", str(dest)], input=archive.stdout, capture_output=True)
    if untar.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        fail(f"解開快照失敗：{untar.stderr.decode(errors='replace')}")
    print(json.dumps({"path": str(dest), "base": base, "sha": sha, "repo": str(repo)}, ensure_ascii=False))


def cmd_remove(args: argparse.Namespace) -> None:
    target = Path(args.path).resolve()
    if not target.name.startswith("_plan-") or target.parent.name != "worktrees":
        fail(f"{target} 不是 bombolt 的規劃快照，拒絕刪除。")
    shutil.rmtree(target)
    print(json.dumps({"removed": str(target)}, ensure_ascii=False))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--base", default="")
    c.set_defaults(func=cmd_create)
    r = sub.add_parser("remove")
    r.add_argument("path")
    r.set_defaults(func=cmd_remove)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
