#!/usr/bin/env python3
"""建立（或找回）某個 issue 的 worktree。

用法：
  bb_worktree.py create --issue 12 --slug bookings-default-month --session-id <uuid>
  bb_worktree.py pickup --pr <PR 或 issue 編號> --session-id <uuid>
      接手一個開著的 PR（bb-fix 開工時）：這台電腦沒有它的 worktree 就從 origin 上的 branch 建一個；
      已經有就 fast-forward 到 origin。本機有沒 push 的 commit（ahead）、或跟 origin 分岔時停下來說明。
  bb_worktree.py info [--path <worktree>]            metadata ＋ artifacts 路徑 ＋ 這台電腦是誰的（owner）
  bb_worktree.py redo-clean --issue 12 --slug bookings-default-month
      重做前清掉前一次的 worktree、本地 branch、遠端 branch（前一次的 PR 必須已經關閉、沒有 merge）
  bb_worktree.py sync-config [--path <worktree>]   fetch 之後把 origin/<base> 上的 .claude/bombolt.md 重新存成這個 worktree 的快照

建 worktree 時會把 origin/<base> 上的 .claude/bombolt.md（還沒 push 過就用主 checkout 那份）存成快照
（放在那個 worktree 的 git dir），之後那個 worktree 裡的腳本與安全守門都只讀快照——
使用者在主 checkout 切 branch、pull 都不影響它。
代價是之後改了設定，要 push 到 origin，已經建好的 worktree 再 `sync-config` 才會套用。

一個名字貫穿全程：worktree 目錄 = branch = `bb-<issue>-<slug>`。
worktree 一律從 `origin/<base_branch>` 的最新版開出來（先 fetch），不帶 upstream，
所以 `git push` 不可能意外推到 base branch。

輸出一行 JSON（給 skill 讀），錯誤時 exit 1 並在 stderr 說明原因與補救方式。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bb_issues  # noqa: E402
import bb_lib  # noqa: E402
import bb_pr  # noqa: E402
import bb_sweep  # noqa: E402

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def fail(msg: str) -> None:
    print(f"bombolt: {msg}", file=sys.stderr)
    sys.exit(1)


def copy_files(repo: Path, path: Path, cfg: dict) -> tuple:
    """把設定檔 copy_files 列的 .env 類檔案從主 checkout 複製到 worktree。回傳 (複製了的, 跳過的＋原因)。"""
    copied, skipped = [], []
    for rel in cfg.get("copy_files", []):
        src = repo / rel
        if not src.is_file():
            skipped.append(f"{rel}（主 checkout 沒有這個檔）")
            continue
        if not bb_lib.git_ok(["check-ignore", "-q", rel], repo):
            skipped.append(f"{rel}（沒被 gitignore，拒絕複製以免被 commit）")
            continue
        dst = path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(rel)
    return copied, skipped


def cmd_create(args: argparse.Namespace) -> None:
    repo = bb_lib.main_checkout(Path.cwd())
    if repo is None:
        fail("目前目錄不在 git repo 裡。")
    first = bb_lib.base_config(repo)  # 還沒 fetch：只用來知道 base_branch
    if first["config"] is None:
        fail(f"{first['source']}，請先執行 /bombolt:bb-setup。")
    base = args.base or first["config"].get("base_branch")
    if not base:
        fail(f"{bb_lib.CONFIG_REL} 沒有設定 base_branch。")
    if not SLUG_RE.match(args.slug) or len(args.slug) > 40:
        fail(f"slug `{args.slug}` 不合法：只能用小寫英數與 -，最長 40 字。")

    name = f"bb-{args.issue}-{args.slug}"
    path = repo / bb_lib.WORKTREES_REL / name

    bb_lib.ensure_local_exclude(repo, f"/{bb_lib.WORKTREES_REL}/")
    bb_lib.ensure_local_exclude(repo, f"/{bb_lib.ARTIFACTS_DIR}/")

    if path.exists():
        # 已經有了（例如 resume 之後重跑）：不重建，只回報現況。
        meta = bb_lib.read_meta(path)
        if meta is None:
            fail(f"{path} 已存在但不是 bombolt 建的 worktree，請手動確認。")
        if args.session_id and meta.get("session_id") != args.session_id:
            meta.setdefault("previous_session_ids", []).append(meta.get("session_id"))
            meta["session_id"] = args.session_id
            bb_lib.write_meta(path, meta)
        print(json.dumps({"status": "exists", "path": str(path), **meta}, ensure_ascii=False))
        return

    if bb_lib.git_ok(["show-ref", "--verify", "--quiet", f"refs/heads/{name}"], repo):
        fail(f"本地已經有 branch `{name}` 但沒有對應的 worktree；請先確認那個 branch 是否還要（`git branch -D {name}`）。")

    try:
        bb_lib.git(["fetch", "--quiet", "origin", base], repo)
    except RuntimeError as e:
        fail(f"fetch origin/{base} 失敗：{e}")
    base_sha = bb_lib.git(["rev-parse", f"origin/{base}"], repo)
    chosen = bb_lib.base_config(repo)  # fetch 之後再讀一次：origin 上最新的設定
    cfg = chosen["config"]

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        bb_lib.git(["worktree", "add", "--no-track", "-b", name, str(path), f"origin/{base}"], repo)
    except RuntimeError as e:
        fail(f"建立 worktree 失敗：{e}")

    copied, skipped = copy_files(repo, path, cfg)
    meta = {
        "tool": "bombolt",
        "issue": args.issue,
        "name": name,
        "branch": name,
        "base": base,
        "base_sha": base_sha,
        "repo": str(repo),
        "path": str(path),
        "session_id": args.session_id,
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
    bb_lib.write_meta(path, meta)
    bb_lib.snapshot_config(path, chosen["text"])  # 之後這個 worktree 只讀這份快照，主 checkout 切 branch 不影響它
    bb_lib.artifacts_dir(path)
    print(json.dumps({"status": "created", **meta, "copied": copied, "skipped": skipped,
                      "artifacts": str(bb_lib.artifacts_dir(path)), "config_source": chosen["source"]},
                     ensure_ascii=False))


def cmd_pickup(args: argparse.Namespace) -> None:
    """接手一個開著的 PR：worktree 跟 branch 同名、放在同一個位置，所以哪一台電腦接手都長一樣。"""
    repo = bb_lib.main_checkout(Path.cwd())
    if repo is None:
        fail("目前目錄不在 git repo 裡。")
    try:
        pr = bb_pr.resolve(repo, args.pr)
    except bb_issues.GhError as e:
        fail(str(e))
    if pr.get("state") != "OPEN":
        fail(f"PR #{pr['number']} 不是開著的（{pr.get('state')}），沒有要接手的。要重做請跑 /bombolt:bb-work {pr['issue']}。")
    first = bb_lib.base_config(repo)
    if first["config"] is None:
        fail(f"{first['source']}，請先執行 /bombolt:bb-setup。")
    base = first["config"].get("base_branch")
    if not base:
        fail(f"{bb_lib.CONFIG_REL} 沒有設定 base_branch。")
    name, remote = pr["headRefName"], f"origin/{pr['headRefName']}"
    path = repo / bb_lib.WORKTREES_REL / name
    try:
        bb_lib.git(["fetch", "--quiet", "origin", base, name], repo)
    except RuntimeError as e:
        fail(f"fetch 失敗：{e}")

    def count(rng: str) -> int:
        return int(bb_lib.git(["rev-list", "--count", rng], repo) or 0)

    local = bb_lib.git(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"], repo, check=False).strip()
    ahead = count(f"{remote}..{name}") if local else 0
    behind = count(f"{name}..{remote}") if local else 0
    if ahead and behind:
        fail(f"本機的 `{name}` 跟 origin 分岔了（本機多 {ahead} 個、origin 多 {behind} 個 commit）。"
             f"不會自動合併：先看兩邊各改了什麼（git log {remote}...{name}），跟使用者確認怎麼處理。")

    bb_lib.ensure_local_exclude(repo, f"/{bb_lib.WORKTREES_REL}/")
    bb_lib.ensure_local_exclude(repo, f"/{bb_lib.ARTIFACTS_DIR}/")
    created, copied, skipped, source = not path.exists(), [], [], ""
    if not created:
        meta = bb_lib.read_meta(path)
        if meta is None:
            fail(f"{path} 已存在但不是 bombolt 建的 worktree，請手動確認。")
        if bb_lib.git(["status", "--porcelain"], path).strip():
            fail(f"{path} 有未 commit 的檔案（之前在這台電腦改到一半？）。先確認要不要留（git -C {path} status），再接手。")
        if args.session_id and meta.get("session_id") != args.session_id:
            meta.setdefault("previous_session_ids", []).append(meta.get("session_id"))
            meta["session_id"] = args.session_id
            bb_lib.write_meta(path, meta)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if local:  # worktree 被刪了、branch 還在（例如之前在這台做過）：沿用 branch，下面再追上 origin
                bb_lib.git(["worktree", "add", str(path), name], repo)
            else:
                bb_lib.git(["worktree", "add", "--track", "-b", name, str(path), remote], repo)
        except RuntimeError as e:
            fail(f"建立 worktree 失敗：{e}")
        chosen = bb_lib.base_config(repo)  # fetch 之後的：origin 上最新的設定
        copied, skipped = copy_files(repo, path, chosen["config"])
        source = chosen["source"]
        meta = {
            "tool": "bombolt",
            "issue": pr["issue"],
            "name": name,
            "branch": name,
            "base": base,
            "base_sha": bb_lib.git(["rev-parse", f"origin/{base}"], repo),
            "repo": str(repo),
            "path": str(path),
            "session_id": args.session_id,
            "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "picked_up_pr": pr["number"],
        }
        bb_lib.write_meta(path, meta)
        bb_lib.snapshot_config(path, chosen["text"])
    if behind:
        try:
            bb_lib.git(["merge", "--ff-only", "--quiet", remote], path)
        except RuntimeError as e:
            fail(f"fast-forward 到 {remote} 失敗：{e}")
    bb_lib.git(["branch", "--quiet", f"--set-upstream-to={remote}", name], repo, check=False)  # 之後 git push 推回同一支

    if ahead:
        status = "ahead"
        message = (f"本機的 `{name}` 有 {ahead} 個還沒 push 的 commit（之前在這台電腦改的，沒有進 PR）："
                   f"給使用者看 `git log {remote}..HEAD`，問要併進這一輪，還是先停下來。")
    elif created:
        status, message = "created", f"從 {remote} 建好 worktree。"
    elif behind:
        status, message = "updated", f"fast-forward 了 {behind} 個 commit（別的電腦或別人 push 的）。"
    else:
        status, message = "exists", "這台電腦的 worktree 已經是最新的。"
    print(json.dumps({"status": status, "message": message, "pr": pr["number"], "issue": pr["issue"],
                      "branch": name, "path": str(path), "pulled": behind, "ahead": ahead,
                      "artifacts": str(bb_lib.artifacts_dir(path)), "copied": copied, "skipped": skipped,
                      "config_source": source}, ensure_ascii=False))


def cmd_info(args: argparse.Namespace) -> None:
    target = Path(args.path) if args.path else Path.cwd()
    meta = bb_lib.read_meta(target)
    if meta is None:
        fail(f"{target} 不是 bombolt 建的 worktree。")
    meta["artifacts"] = str(bb_lib.artifacts_dir(target))
    meta["config_snapshot"] = bb_lib.config_source(target)  # 這個 worktree 讀的設定（空字串：快照功能之前建的）
    meta["session_name"] = bb_lib.session_name(meta.get("session_id", ""))
    owner = bb_lib.owner_info(target)
    meta["owner"] = owner
    meta["owner_text"] = bb_lib.owner_text(owner)    # 給人看的：誰、哪台電腦
    meta["owner_short"] = bb_lib.owner_short(owner)
    print(json.dumps(meta, ensure_ascii=False))


def cmd_redo_clean(args: argparse.Namespace) -> None:
    """前一次的 PR 被關掉（沒 merge）、要用同一個名字重做：先把前一次留下的東西清掉。

    全部檢查通過才動手，任何一條不確定就整個不做：
      - 這個 branch 的 PR 都已經關閉，沒有開著的、沒有 merge 過的
      - 本機的 worktree：是 bombolt 建的、沒有 session 在用、沒被 lock、沒有未 commit 的檔案
      - 本機的 branch 和遠端的 branch：tip 都是某一個被關掉的 PR 的 head（PR 裡看得到，刪掉不會丟東西）
      - 沒有開著的 PR 以它當 base
    刪除：worktree（不加 --force）、本地 branch、遠端 branch（--force-with-lease）。
    舊的 commit 都還留在被關掉的 PR 裡。
    """
    repo = bb_lib.main_checkout(Path.cwd())
    if repo is None:
        fail("目前目錄不在 git repo 裡。")
    if not SLUG_RE.match(args.slug):
        fail(f"slug `{args.slug}` 不合法。")
    name = f"bb-{args.issue}-{args.slug}"
    path = repo / bb_lib.WORKTREES_REL / name

    try:
        prs = bb_issues.prs_for_branch(repo, name)
    except bb_issues.GhError as e:
        fail(f"查不到 `{name}` 的 PR：{e}")
    live = [p for p in prs if p.get("state") in ("OPEN", "MERGED")]
    if live:
        p = live[0]
        fail(f"PR #{p['number']} 的狀態是 {p['state']}，不是重做的情況（要重做請先關掉開著的 PR）。")
    closed = sorted((p for p in prs if p.get("state") == "CLOSED"), key=lambda p: p["number"])
    if not closed:
        fail(f"`{name}` 沒有被關掉的 PR，不是重做的情況。")
    heads = {p.get("headRefOid") for p in closed if p.get("headRefOid")}
    if name in bb_sweep.protected_branches(repo, {}):
        fail(f"`{name}` 在受保護的名單裡，不動。")

    wt = next((w for w in bb_sweep.list_worktrees(repo)
               if Path(w.get("path", "")).resolve() == path.resolve()), None)
    if wt is None and path.exists():
        fail(f"{path} 存在但不是 git worktree，請手動確認。")
    if wt is not None:
        meta = bb_lib.read_meta(path) or {}
        if meta.get("tool") != "bombolt":
            fail(f"{path} 不是 bombolt 建的 worktree，請手動確認。")
        for s in bb_lib.claude_sessions():
            cwd = s.get("cwd", "")
            if cwd == str(path) or cwd.startswith(str(path) + "/") or \
                    (meta.get("session_id") and s.get("sessionId") == meta.get("session_id")):
                fail(f"前一次的 session 還開著（pid {s.get('pid')}）。請先關掉它，再在新的 session 重做。")
        if wt.get("locked"):
            fail(f"{path} 被 git worktree lock 鎖住，請先確認是誰在用。")
        dirty = bb_lib.git(["status", "--porcelain"], path).strip()
        if dirty:
            fail(f"前一次的 worktree 還有 {len(dirty.splitlines())} 個未 commit 的檔案（{path}），不會刪。確認不要了再手動清掉。")
    local = bb_lib.git(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"], repo, check=False).strip()
    if local and local not in heads:
        fail(f"本機的 `{name}`（{local[:8]}）有被關掉的 PR 裡沒有的 commit，不會刪。確認不要了再手動刪。")
    tip = bb_lib.remote_tip(repo, name)
    if tip is None:
        fail(f"查不到遠端 `{name}` 的狀態，先不動。")
    if tip and tip not in heads:
        fail(f"遠端 `{name}`（{tip[:8]}）在 PR 關掉之後又有新的 commit，不會刪。請先確認是誰推的。")
    if tip:
        others = bb_sweep.open_prs_touching(repo, name)
        if others is None:
            fail("查不到有沒有別的 PR 用到這個 branch，先不動。")
        if others:
            fail(f"還有開著的 PR 用到 `{name}`（{', '.join('#' + str(n) for n in others)}），不動。")

    done = []
    if wt is not None:
        try:
            bb_lib.git(["worktree", "remove", str(path)], repo)
        except RuntimeError as e:
            fail(f"刪不掉前一次的 worktree：{e}")
        done.append(f"刪除 worktree {path}")
    if local:
        bb_lib.git(["branch", "-D", name], repo)
        done.append(f"刪除本地 branch `{name}`（原本指向 {local[:8]}）")
    if tip:
        msg = bb_sweep.delete_remote(repo, name, tip)
        if not msg.startswith("已刪除"):
            fail(f"{'；'.join(done)}；但{msg}")
        done.append(msg)
    print(json.dumps({"status": "ok", "branch": name, "closed_prs": [p["number"] for p in closed],
                      "done": done or ["前一次沒有留下 worktree 或 branch，不用清"]}, ensure_ascii=False))


def cmd_sync_config(args: argparse.Namespace) -> None:
    target = Path(args.path) if args.path else Path.cwd()
    meta = bb_lib.read_meta(target)
    if meta is None or meta.get("integration"):
        fail(f"{target} 不是 bombolt 建的 feature worktree。")
    repo = Path(meta["repo"])
    try:
        bb_lib.git(["fetch", "--quiet", "origin", meta["base"]], repo)
    except RuntimeError as e:
        fail(f"fetch origin/{meta['base']} 失敗：{e}，快照維持不變。")
    chosen = bb_lib.base_config(repo)
    if chosen["text"] is None:
        fail(f"{chosen['source']}，快照維持不變。")
    snap = bb_lib.snapshot_config(target, chosen["text"])
    print(json.dumps({"status": "synced", "path": str(target), "snapshot": str(snap),
                      "config_source": chosen["source"]}, ensure_ascii=False))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--issue", required=True, type=int)
    c.add_argument("--slug", required=True)
    c.add_argument("--session-id", default="")
    c.add_argument("--base", default="", help="覆寫設定檔的 base_branch（通常不需要）")
    c.set_defaults(func=cmd_create)
    k = sub.add_parser("pickup")
    k.add_argument("--pr", required=True, type=int, help="PR 或 issue 編號")
    k.add_argument("--session-id", default="")
    k.set_defaults(func=cmd_pickup)
    i = sub.add_parser("info")
    i.add_argument("--path", default="")
    i.set_defaults(func=cmd_info)
    r = sub.add_parser("redo-clean")
    r.add_argument("--issue", required=True, type=int)
    r.add_argument("--slug", required=True)
    r.set_defaults(func=cmd_redo_clean)
    s = sub.add_parser("sync-config")
    s.add_argument("--path", default="")
    s.set_defaults(func=cmd_sync_config)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
