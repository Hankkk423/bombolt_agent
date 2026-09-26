#!/usr/bin/env python3
"""印出 skill 開頭需要的環境狀態（給 SKILL.md 的 !`...` 注入用）。

⚠️ 這支永遠 exit 0：skill 的動態注入只要有一個指令失敗，整個 skill 就載入失敗，
所以任何問題都印成文字讓 agent 讀，而不是用 exit code 表達。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bb_lib  # noqa: E402

MIN_GH = (2, 99, 0)


def run(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def main() -> None:
    cwd = Path.cwd()
    lines = []
    sid = sys.argv[sys.argv.index("--session-id") + 1] if "--session-id" in sys.argv[:-1] else ""
    if sid:
        name = bb_lib.session_name(sid)
        lines.append(f"- 這個 session：id `{sid}`，名稱 `{name or '（未命名）'}`")
    repo = bb_lib.main_checkout(cwd)
    if repo is None:
        print(f"- ❌ 目前目錄 {cwd} 不在 git repo 裡。bombolt 需要在專案 repo 裡執行。")
        return
    lines.append(f"- 主 checkout：`{repo}`")
    meta = bb_lib.read_meta(cwd)
    if meta:
        lines.append(f"- 目前在 bombolt worktree：`{meta.get('name')}`（issue #{meta.get('issue')}）")
        lines.append(f"- worktree metadata：`{json.dumps(meta, ensure_ascii=False)}`")
    elif Path(bb_lib.git(["rev-parse", "--show-toplevel"], cwd, check=False) or str(repo)) != repo:
        lines.append(f"- ⚠️ 目前在一個不是 bombolt 建的 worktree 裡：`{cwd}`")
    else:
        lines.append("- 目前在主 checkout（不在任何 worktree 裡）")
        own = bb_lib.worktree_of_session(repo, sid)
        if own is not None:
            lines.append(f"- ⚠️ 這個 session 是 worktree `{own}` 的實作 session，但目前不在那個 worktree 裡")

    code, remote = run(["git", "-C", str(repo), "remote", "get-url", "origin"])
    lines.append(f"- origin：`{remote}`" if code == 0 else "- ❌ 沒有 origin remote")

    snap = bb_lib.config_source(cwd) if meta else ""
    cfg = bb_lib.load_worktree_config(cwd, repo) if meta else bb_lib.load_config(repo)
    if cfg is None:
        lines.append(f"- ❌ 還沒有專案設定 `{bb_lib.CONFIG_REL}` → 需要先跑 bb-setup")
    else:
        where = (f"這個 worktree 建立當下的快照 `{snap}`（主 checkout 之後的改動不會影響它；"
                 f"要套用新設定跑 `bb_worktree.py sync-config`）") if snap else f"`{repo / bb_lib.CONFIG_REL}`"
        lines.append(
            f"- 專案設定：{where}（base_branch=`{cfg.get('base_branch', '')}` · "
            f"pr_base=`{bb_lib.pr_base(cfg)}` · integration_branches=`{cfg.get('integration_branches', [])}`）"
        )

    if shutil.which("gh") is None:
        lines.append("- ❌ 沒有安裝 gh（GitHub CLI）")
    else:
        _, ver = run(["gh", "--version"])
        first = ver.splitlines()[0] if ver else ""
        try:
            nums = tuple(int(x) for x in first.split()[2].split(".")[:3])
        except (IndexError, ValueError):
            nums = (0, 0, 0)
        ok = "✅" if nums >= MIN_GH else f"⚠️ 版本低於 {'.'.join(map(str, MIN_GH))}（PR 附圖需要 --attach）"
        lines.append(f"- gh：{first} {ok}")
        try:
            acct = bb_lib.gh_account_env(cwd)
            p = subprocess.run([acct["gh"], "api", "user", "--jq", ".login"], capture_output=True,
                               text=True, env=acct["env"], timeout=20)
            if p.returncode == 0 and p.stdout.strip():
                lines.append(f"- GitHub 帳號：`{p.stdout.strip()}`（{acct['source']}）")
            else:
                lines.append("- ❌ gh 尚未登入或 token 失效 → 請使用者在提示列輸入 `! gh auth login`")
        except bb_lib.GhAccountError as e:
            lines.append(f"- ❌ GitHub 帳號：{e}")
        lines.append("- ⚠️ 所有 GitHub 操作一律用 `bb-gh`（用法跟 gh 一樣），不要直接呼叫 `gh`")

    lines.append("- playwright-cli（UI 驗收用）：" + ("✅" if shutil.which("playwright-cli") else "⚠️ 沒安裝（`npm i -g @playwright/cli`）"))
    print("\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"- ⚠️ bb_context 出錯：{e!r}")
