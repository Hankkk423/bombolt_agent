#!/usr/bin/env python3
"""bb-plan 開工前的兩項檢查（給 SKILL.md 的 !`...` 注入用）：

1. 位置：目前是不是在這個 repo 的**主 checkout**。判斷不出來就回報「無法確定」，由 skill 反問使用者。
2. 同步：fetch origin/<base_branch>。規劃一律以 origin/<base_branch> 為準（程式碼與設定檔都是），
   不管主 checkout 目前在哪個 branch。只有兩種差異要問使用者（通常代表忘了 push）：
   本地 base 沒 push 的 commit、.claude/bombolt.md 還不在 origin 上。
   其他（目前 branch 自己的 commit、沒 commit 的改動、設定檔用了哪一份）只告知。

⚠️ 這支永遠 exit 0：skill 的動態注入只要有一個指令失敗，整個 skill 就載入失敗，
所以任何問題都印成文字讓 agent 讀。fetch 不會卡在要密碼的提示上（關掉互動、有 timeout）。

用法：bb_plan_check.py [--assume-main]
  --assume-main：位置「無法確定」而使用者已經確認這裡是主 checkout 時用，補做同步檢查。
  只放寬「無法確定」；確定不是主 checkout（linked worktree、repo 外）照樣擋。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bb_lib  # noqa: E402

FETCH_TIMEOUT = 45

LOC_OK, LOC_NOT_MAIN, LOC_UNKNOWN = "ok", "not_main", "unknown"
SYNC_OK, SYNC_DIFF, SYNC_FETCH_FAILED, SYNC_NO_BASE = "ok", "diff", "fetch_failed", "no_base"


def _git(args: List[str], cwd: Path, timeout: int = 20, env: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, (p.stdout if p.returncode == 0 else p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def _resolve(p: str) -> Path:
    return Path(p).resolve()


def check_location(cwd: Path) -> Dict[str, Any]:
    """回傳 {"status", "detail", "main_checkout"}；main_checkout 只在推得出來時才有值。"""
    code, top = _git(["rev-parse", "--show-toplevel"], cwd)
    if code != 0:
        return {"status": LOC_NOT_MAIN, "detail": f"目前目錄 `{cwd}` 不在 git repo 裡", "main_checkout": None}
    _, git_dir = _git(["rev-parse", "--path-format=absolute", "--git-dir"], cwd)
    _, common = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)
    top_p, git_dir_p, common_p = _resolve(top), _resolve(git_dir), _resolve(common)
    if common_p.name != ".git":
        return {"status": LOC_UNKNOWN, "main_checkout": None,
                "detail": f"git 目錄是 `{common_p}`，不是一般的 `<repo>/.git`（可能是 submodule、bare repo 或特殊配置），推不出主 checkout"}
    main = common_p.parent
    if git_dir_p != common_p:
        return {"status": LOC_NOT_MAIN, "main_checkout": str(main),
                "detail": f"目前在 linked worktree `{top_p}` 裡，不是主 checkout"}
    if top_p != main:
        return {"status": LOC_UNKNOWN, "main_checkout": str(main),
                "detail": f"工作目錄根 `{top_p}` 跟 git 目錄所在的 `{main}` 對不上（可能設了 GIT_DIR 或 core.worktree）"}
    worktrees = main / bb_lib.WORKTREES_REL
    cwd_p = cwd.resolve()
    if cwd_p == worktrees or worktrees in cwd_p.parents:
        return {"status": LOC_NOT_MAIN, "main_checkout": str(main),
                "detail": f"目前目錄 `{cwd_p}` 在 `{bb_lib.WORKTREES_REL}/` 底下（worktree 或規劃快照），不是主 checkout"}
    detail = f"主 checkout `{main}`"
    if cwd_p != main:
        detail += f"（目前在子目錄 `{cwd_p.relative_to(main)}`）"
    return {"status": LOC_OK, "main_checkout": str(main), "detail": detail}


def _fetch_env() -> Dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # https 要密碼時直接失敗，不要卡住
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    return env


def check_sync(repo: Path) -> Dict[str, Any]:
    """fetch origin/<base> 並列出差異。回傳 {"status", "base", "origin_sha", "diffs", "notes", "detail"}。

    diffs：本地跟 remote 會讓規劃不準、要問使用者怎麼處理的差異。
    notes：不影響規劃、只是讓使用者知道的狀況。
    """
    out: Dict[str, Any] = {"status": SYNC_OK, "base": "", "origin_sha": "", "diffs": [], "notes": [], "detail": ""}
    chosen = bb_lib.base_config(repo)
    base = (chosen["config"] or {}).get("base_branch") or ""
    out["base"] = base
    if not base:
        out.update(status=SYNC_NO_BASE, detail=f"{chosen['source']}，無法比對")
        return out

    code, msg = _git(["fetch", "--quiet", "origin", base], repo, timeout=FETCH_TIMEOUT, env=_fetch_env())
    if code != 0:
        out.update(status=SYNC_FETCH_FAILED, detail=f"`git fetch origin {base}` 失敗：{msg or '（沒有訊息）'}")
        return out
    code, sha = _git(["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{base}"], repo)
    if code != 0 or not sha:
        out.update(status=SYNC_FETCH_FAILED, detail=f"fetch 完還是找不到 `origin/{base}`")
        return out
    out["origin_sha"] = sha
    origin_ref = f"origin/{base}"
    diffs: List[str] = out["diffs"]
    notes: List[str] = out["notes"]

    # 本地的 base branch
    if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{base}"], repo)[0] == 0:
        code, counts = _git(["rev-list", "--left-right", "--count", f"refs/heads/{base}...{origin_ref}"], repo)
        if code == 0:
            ahead, behind = (int(x) for x in counts.split())
            if ahead:
                diffs.append(f"本地 `{base}` 有 {ahead} 個 commit 還沒 push 到 origin（規劃以 `{origin_ref}` 為準，看不到它們）")
            if behind:
                notes.append(f"本地 `{base}` 落後 `{origin_ref}` {behind} 個 commit（不影響規劃：探索讀的是 origin 最新版）")

    # 目前所在的 branch
    code, branch = _git(["symbolic-ref", "--quiet", "--short", "HEAD"], repo)
    if code != 0:
        notes.append("目前是 detached HEAD")
    elif branch != base:
        notes.append(f"目前在 branch `{branch}`（不是 `{base}`）")
        code, n = _git(["rev-list", "--count", f"{origin_ref}..HEAD"], repo)
        if code == 0 and int(n or 0):
            notes.append(f"目前的 branch `{branch}` 有 {n} 個 commit 不在 `{origin_ref}` 裡（規劃以 origin 為準，看不到它們）")

    # 沒 commit 的改動（.claude/worktrees 已被 .git/info/exclude 忽略；設定檔另外看）
    code, status = _git(["status", "--porcelain", "--untracked-files=all", "--", ".", f":(exclude){bb_lib.CONFIG_REL}"], repo)
    if code == 0 and status:
        files = [line[3:] for line in status.splitlines()]
        shown = "、".join(f"`{f}`" for f in files[:5]) + ("…" if len(files) > 5 else "")
        notes.append(f"主 checkout 有 {len(files)} 個沒 commit 的改動（規劃以 origin 為準，看不到它們）：{shown}")

    # 專案設定：規劃與實作都用 origin 上那份；還不在 origin 上時只好先用本機的，同事拿不到
    if (repo / bb_lib.CONFIG_REL).is_file() and bb_lib.config_text_at(repo, origin_ref) is None:
        diffs.append(f"`{bb_lib.CONFIG_REL}` 還不在 `{origin_ref}` 上（這台電腦先用本機那份，同事拿不到）")
    notes.append(f"設定檔：{bb_lib.base_config(repo)['source']}")

    if diffs:
        out["status"] = SYNC_DIFF
    return out


def render(loc: Dict[str, Any], sync: Optional[Dict[str, Any]]) -> str:
    icon = {LOC_OK: "✅", LOC_NOT_MAIN: "❌", LOC_UNKNOWN: "❓"}[loc["status"]]
    word = {LOC_OK: "確定是主 checkout", LOC_NOT_MAIN: "不是主 checkout", LOC_UNKNOWN: "無法確定是不是主 checkout"}
    lines = [f"- 位置：{icon} {word[loc['status']]} —— {loc['detail']}"]
    if loc["status"] == LOC_NOT_MAIN and loc.get("main_checkout"):
        lines.append(f"  - 這個 repo 的主 checkout 是 `{loc['main_checkout']}`")
    if sync is None:
        lines.append("- 同步：⏭️ 沒有檢查（位置不對，先處理位置）")
        return "\n".join(lines)
    st = sync["status"]
    if st == SYNC_OK:
        lines.append(f"- 同步：✅ 已 fetch `origin/{sync['base']}`（`{sync['origin_sha'][:10]}`），沒有會影響規劃的本地差異")
    elif st == SYNC_DIFF:
        lines.append(f"- 同步：⚠️ 已 fetch `origin/{sync['base']}`（`{sync['origin_sha'][:10]}`），但本地跟 remote 有差異：")
        lines += [f"  - {d}" for d in sync["diffs"]]
    elif st == SYNC_FETCH_FAILED:
        lines.append(f"- 同步：❌ 無法確認是最新版 —— {sync['detail']}")
    else:
        lines.append(f"- 同步：⏭️ 還不能檢查 —— {sync['detail']}（完成 bb-setup 之後重跑這支腳本）")
    lines += [f"  - ℹ️ {n}" for n in sync.get("notes", [])]
    return "\n".join(lines)


def main() -> None:
    cwd = Path.cwd()
    loc = check_location(cwd)
    if "--assume-main" in sys.argv[1:] and loc["status"] == LOC_UNKNOWN:
        # 使用者已經親口確認「這裡就是主 checkout」：以目前的工作目錄根為準補做同步檢查
        code, top = _git(["rev-parse", "--show-toplevel"], cwd)
        loc = {"status": LOC_OK, "main_checkout": str(_resolve(top)),
               "detail": f"使用者確認 `{_resolve(top)}` 是主 checkout（偵測結果：{loc['detail']}）"}
    sync = check_sync(Path(loc["main_checkout"])) if loc["status"] == LOC_OK else None
    print(render(loc, sync))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"- ❓ bb_plan_check 出錯：{e!r} —— 位置與同步都視為「無法確定」")
