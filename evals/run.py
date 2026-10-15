#!/usr/bin/env python3
"""bombolt 的 eval：用固定的題目實際跑 bb-work / bb-fix，打分、收集過程指標、跟 baseline 比較。

每個 run 都在 $TMPDIR 底下建一個全新的工作區：本機 bare repo 當 origin、假的 gh（evals/fake_gh.py）當 GitHub，
再用 headless 的 `claude -p "/bombolt:bb-work <n>"` 載入這個 repo 的 bombolt（--plugin-dir）實際跑完。
完全不碰真的 GitHub、不碰使用者的 repo。

  python3 evals/run.py selftest                 # 免費：確認每一題的參考解能過 grader、沒改的版本會失敗
  python3 evals/run.py run [--case 'work-*']    # 花錢：實際跑，最後印出報告（也寫進 results/<時間>/report.md）
  python3 evals/run.py report <results 目錄>    # 重印報告（跟 baseline 比）
  python3 evals/run.py accept <results 目錄>    # 把這次結果設成新的 baseline（evals/baseline.json）
  python3 evals/run.py timeline <run 目錄>      # 把一個 run 的 transcript 壓成時間線（查失敗原因先看這個）
  python3 evals/run.py prepare <題目>           # 只建工作區、印出要怎麼手動跑（除錯用）

只用 python 標準函式庫，要能在 python 3.9 跑。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as _dt
import fnmatch
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

EVALS = Path(__file__).resolve().parent
ROOT = EVALS.parent
CASES = EVALS / "cases"
FIXTURE = EVALS / "fixture"
RESULTS = EVALS / "results"
BASELINE = EVALS / "baseline.json"
sys.path.insert(0, str(EVALS))
sys.path.insert(0, str(ROOT / "scripts"))
import fake_gh  # noqa: E402
from bb_issues import STATUS_RE, find_status  # noqa: E402
from bb_pr import RECORD_RE, HANDOFF_START  # noqa: E402

OWNER, REPO, LOGIN = "acme", "todo-app", "eval-bot"
DEV_NAME, DEV_EMAIL = "Eval Dev", "eval-dev@example.com"
COWORKER = ("Amy Lin", "amy@example.com")
PORT_BASE = 20000  # 要跟 fixture/.claude/bombolt.md 的「啟動 app」一致
DEFAULTS = {"runs": 2, "jobs": 3, "model": "opus", "budget_usd": 15.0, "timeout_min": 45}

# 退步的門檻（每題只跑幾次，雜訊大：門檻刻意抓寬，報告裡也會提醒）
REGRESS_SCORE = 0.10
REGRESS_GRADER = 0.5
REGRESS_COST = 0.40


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def sh(cmd: List[str], cwd: Path, env: Optional[Dict[str, str]] = None, check: bool = True,
       timeout: int = 120, stdin: Optional[str] = None) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout, input=stdin)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} 失敗（{cwd}）：{(p.stderr or p.stdout).strip()[-800:]}")
    return p


def git(args: List[str], cwd: Path, env: Dict[str, str], check: bool = True) -> str:
    return sh(["git", *args], cwd, env=env, check=check).stdout.strip()


def load_case(name: str) -> Dict[str, Any]:
    d = CASES / name
    case = json.loads((d / "case.json").read_text(encoding="utf-8"))
    case["name"], case["dir"] = name, d
    return case


def case_names(patterns: Optional[List[str]] = None) -> List[str]:
    names = sorted(p.name for p in CASES.iterdir() if (p / "case.json").is_file())
    if patterns:
        names = [n for n in names if any(fnmatch.fnmatch(n, pat) for pat in patterns)]
    return names


def render(text: str, ctx: Dict[str, Any]) -> str:
    for k, v in ctx.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def bombolt_version() -> Dict[str, Any]:
    env = dict(os.environ)
    sha = sh(["git", "rev-parse", "--short", "HEAD"], ROOT, env=env, check=False).stdout.strip()
    dirty = bool(sh(["git", "status", "--porcelain", "--", "skills", "agents", "scripts", "hooks", "bin"],
                    ROOT, env=env, check=False).stdout.strip())
    return {"sha": sha, "dirty": dirty}


# ---------------------------------------------------------------------------
# 工作區
# ---------------------------------------------------------------------------

class Workspace:
    """一個 run 的工作區：<ws>/origin.git、<ws>/repo（主 checkout，agent 的 cwd）、<ws>/bin/gh、<ws>/gh/state.json。"""

    def __init__(self, path: Path):
        self.path = path
        self.origin = path / "origin.git"
        self.repo = path / "repo"
        self.bin = path / "bin"
        self.state_path = path / "gh" / "state.json"
        self.calls_path = path / "gh" / "calls.jsonl"
        self.gitconfig = path / "gitconfig"

    @property
    def env(self) -> Dict[str, str]:
        """agent 與 grader 都用這組環境：PATH 最前面是假 gh；不吃使用者自己的 git global config 與 GitHub token。"""
        env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST")}
        env["PATH"] = f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}"
        env["GIT_CONFIG_GLOBAL"] = str(self.gitconfig)
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        return env

    def state(self) -> Dict[str, Any]:
        return fake_gh.load_state(str(self.state_path))

    def remote_sha(self, branch: str) -> str:
        p = sh(["git", "--git-dir", str(self.origin), "rev-parse", "--verify", "-q", f"refs/heads/{branch}"],
               self.path, env=self.env, check=False)
        return p.stdout.strip()


def _commit_all(where: Path, env: Dict[str, str], message: str, author: Optional[Tuple[str, str]] = None) -> None:
    git(["add", "-A"], where, env)
    who = ["-c", f"user.name={author[0]}", "-c", f"user.email={author[1]}"] if author else []
    git([*who, "commit", "-q", "-m", message], where, env)


def _apply(where: Path, env: Dict[str, str], diff: Path) -> None:
    git(["apply", "--whitespace=nowarn", str(diff)], where, env)


def prepare_workspace(case: Dict[str, Any], ws_path: Path, number: int) -> Dict[str, Any]:
    """建好一個 run 的工作區，回傳這個 run 的 context（issue / PR 編號、sha…），也寫進 <ws>/eval.json。"""
    ws = Workspace(ws_path)
    ws_path.mkdir(parents=True)
    ws.gitconfig.write_text(f"[user]\n\tname = {DEV_NAME}\n\temail = {DEV_EMAIL}\n"
                            "[init]\n\tdefaultBranch = main\n[advice]\n\tdetachedHead = false\n")
    env = ws.env
    ws.bin.mkdir()
    shim = ws.bin / "gh"
    shim.write_text("#!/bin/sh\n"
                    f'exec "{sys.executable}" "{EVALS / "fake_gh.py"}" --state "{ws.state_path}" "$@"\n')
    shim.chmod(0o755)

    # origin：fixture 當成 main 的第一個 commit
    sh(["git", "init", "-q", "--bare", "-b", "main", str(ws.origin)], ws_path, env=env)
    seed = ws_path / "_seed"
    shutil.copytree(str(FIXTURE), str(seed))
    git(["init", "-q", "-b", "main"], seed, env)
    _commit_all(seed, env, "chore: todo-app 初始版本")
    # 題目專屬的 main 歷史（規劃之前就在了，例如同事加的資料格式版本）：共用的 fixture 不變，其他題目不受影響
    if (case["dir"] / "base.diff").is_file():
        _apply(seed, env, case["dir"] / "base.diff")
        _commit_all(seed, env, case.get("base_message") or "chore: update", author=_author(case.get("base_author")))
    git(["remote", "add", "origin", str(ws.origin)], seed, env)
    git(["push", "-q", "origin", "main"], seed, env)
    shutil.rmtree(str(seed))
    # 這台電腦的主 checkout（之後 main 往前走了它也不會自己更新，跟真實情況一樣）
    sh(["git", "clone", "-q", str(ws.origin), str(ws.repo)], ws_path, env=env)

    base_sha = ws.remote_sha("main")
    today = _dt.date.today().isoformat()
    ctx: Dict[str, Any] = {"case": case["name"], "kind": case["kind"], "issue": number, "slug": case["slug"],
                           "branch": f"bb-{number}-{case['slug']}", "base_sha": base_sha,
                           "base_sha_short": base_sha[:7], "date": today, "repo": REPO, "ui": bool(case.get("ui")),
                           "port": PORT_BASE + number}

    ws.state_path.parent.mkdir()
    state = fake_gh.new_state(OWNER, REPO, LOGIN, str(ws.origin), number)
    issue_body = render((case["dir"] / "issue.md").read_text(encoding="utf-8"), ctx)
    n = fake_gh.add_issue(state, render(case["title"], ctx), issue_body, author=case.get("issue_author"))
    if n != number:
        raise RuntimeError(f"issue 編號是 {n}，預期 {number}")
    ctx["issue_body_sha"] = hashlib.sha256(issue_body.encode("utf-8")).hexdigest()
    fake_gh.save_state(str(ws.state_path), state)

    if case["kind"] == "fix":
        _prepare_open_pr(case, ws, ctx)
    else:
        _advance_main(case, ws)  # 規劃之後、開工之前 main 又往前走
        ctx["main_sha"] = ws.remote_sha("main")
    (ws_path / "eval.json").write_text(json.dumps(ctx, ensure_ascii=False, indent=2))
    return ctx


def _author(text: Optional[str]) -> Tuple[str, str]:
    m = re.match(r"\s*(.+?)\s*<(.+)>", text or "")
    return (m.group(1), m.group(2)) if m else COWORKER


def _advance_main(case: Dict[str, Any], ws: Workspace) -> None:
    """main 往前走（同事 merge 了別的 PR）：題目有 main_advance.diff 才做。"""
    advance = case["dir"] / "main_advance.diff"
    if not advance.is_file():
        return
    env = ws.env
    co = ws.path / "_coworker"
    sh(["git", "clone", "-q", str(ws.origin), str(co)], ws.path, env=env)
    _apply(co, env, advance)
    _commit_all(co, env, case.get("main_advance_message") or "chore: update", author=_author(case.get("main_advance_author")))
    git(["push", "-q", "origin", "main"], co, env)
    shutil.rmtree(str(co))


def _prepare_open_pr(case: Dict[str, Any], ws: Workspace, ctx: Dict[str, Any]) -> None:
    """fix 題：模擬「另一台電腦已經用 bb-work 發了 PR、reviewer 留了意見」。

    用 bombolt 自己的腳本（bb_worktree create、bb_issues mark、bb_pr record）產生 issue 狀態留言與 PR 紀錄，
    格式就永遠跟目前的腳本一致。
    """
    env, scripts = ws.env, ROOT / "scripts"
    other = ws.path / "_other-machine"
    sh(["git", "clone", "-q", str(ws.origin), str(other)], ws.path, env=env)
    sid = str(uuid.uuid4())
    py = sys.executable

    def run_script(name: str, *args: str, cwd: Path) -> Dict[str, Any]:
        p = sh([py, str(scripts / name), *args], cwd, env=env)
        return json.loads(p.stdout or "{}")

    issue, slug = str(ctx["issue"]), ctx["slug"]
    info = run_script("bb_worktree.py", "create", "--issue", issue, "--slug", slug, "--session-id", sid, cwd=other)
    wt = Path(info["path"])
    run_script("bb_issues.py", "mark", "--issue", issue, "--event", "claim", "--branch", ctx["branch"],
               "--session-id", sid, cwd=wt)
    _apply(wt, env, case["dir"] / "pr.diff")
    _commit_all(wt, env, render(case.get("pr_commit_message") or case["pr_title"], ctx))
    git(["push", "-q", "-u", "origin", ctx["branch"]], wt, env)

    head = ws.remote_sha(ctx["branch"])
    ctx.update(pr=ctx["issue"] + 1, head_sha_short=head[:7], initial_head=head)
    body = render((case["dir"] / "pr_body.md").read_text(encoding="utf-8"), ctx)
    body_file = ws.path / "_pr-body.md"
    body_file.write_text(body, encoding="utf-8")
    p = sh(["gh", "pr", "create", "--base", "main", "--head", ctx["branch"], "--title", render(case["pr_title"], ctx),
            "--body-file", str(body_file)], wt, env=env)
    pr_url = p.stdout.strip().splitlines()[-1]
    if not pr_url.endswith(f"/pull/{ctx['pr']}"):
        raise RuntimeError(f"PR 網址是 {pr_url}，預期編號 {ctx['pr']}")
    run_script("bb_issues.py", "mark", "--issue", issue, "--event", "pr", "--pr", pr_url, cwd=wt)
    record = case["dir"] / "record.md"
    rec_file = ws.path / "_record.md"
    rec_file.write_text(record.read_text(encoding="utf-8") if record.is_file()
                        else "**決定**\n- 照 issue 的實作步驟做，沒有另外的決定。\n", encoding="utf-8")
    run_script("bb_pr.py", "record", "--pr", str(ctx["pr"]), "--session-id", sid, "--kind", "round",
               "--summary", "發 PR", "--body-file", str(rec_file), "--verified", cwd=wt)

    review = json.loads((case["dir"] / "review.json").read_text(encoding="utf-8"))
    state = ws.state()
    for r in review.get("reviews", []):
        fake_gh.add_review(state, ctx["pr"], r["author"], r["state"], r["body"])
    for t in review.get("threads", []):
        fake_gh.add_review_thread(state, ctx["pr"], t["path"], t["line"], t["author"], t["body"])
    for c in review.get("comments", []):
        fake_gh.add_comment(state, ctx["pr"], c["body"], author=c["author"])
    fake_gh.save_state(str(ws.state_path), state)

    # PR 開好之後 main 又往前走（同事 merge 了別的 PR）
    _advance_main(case, ws)
    ctx["main_sha"] = ws.remote_sha("main")
    shutil.rmtree(str(other))
    for f in (body_file, rec_file):
        f.unlink()


def prompt_for(ctx: Dict[str, Any]) -> str:
    return f"/bombolt:bb-fix {ctx['pr']}" if ctx["kind"] == "fix" else f"/bombolt:bb-work {ctx['issue']}"


def claude_cmd(ctx: Dict[str, Any], model: str, budget: float) -> List[str]:
    return [shutil.which("claude") or "claude", "-p", prompt_for(ctx),
            "--plugin-dir", str(ROOT),
            "--permission-mode", "auto", "--permission-prompts", "none",
            # 不吃使用者自己的設定（另外裝的 bombolt、自己的 hook、預設模型），每次跑的條件才一樣
            "--setting-sources", "project,local",
            "--model", model, "--max-budget-usd", f"{budget:g}",
            "--output-format", "stream-json", "--verbose"]


# ---------------------------------------------------------------------------
# 執行
# ---------------------------------------------------------------------------

def run_agent(ws: Workspace, ctx: Dict[str, Any], out_dir: Path, model: str, budget: float,
              timeout_min: float) -> Dict[str, Any]:
    cmd = claude_cmd(ctx, model, budget)
    (out_dir / "command.txt").write_text(" ".join(cmd) + "\n")
    started = time.time()
    timed_out = False
    with open(out_dir / "transcript.jsonl", "w") as out, open(out_dir / "stderr.txt", "w") as err:
        proc = subprocess.Popen(cmd, cwd=str(ws.repo), env=ws.env, stdout=out, stderr=err,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            proc.wait(timeout=timeout_min * 60)
        except subprocess.TimeoutExpired:
            timed_out = True
            _killpg(proc.pid)
            proc.wait()
    leftovers = cleanup_processes(ws, ctx)
    return {"exit_code": proc.returncode, "timed_out": timed_out, "wall_s": round(time.time() - started),
            "leftover_processes": leftovers}


def _killpg(pid: int) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(3)


def cleanup_processes(ws: Workspace, ctx: Dict[str, Any]) -> List[str]:
    """run 結束後還活著的 dev server 等（verifier 應該自己關掉）：記下來當指標，然後清掉。"""
    found: List[str] = []
    pids = set()
    if shutil.which("lsof"):
        p = sh(["lsof", "-ti", f"tcp:{ctx['port']}", "-sTCP:LISTEN"], ws.path, check=False)
        pids.update(x for x in p.stdout.split() if x.isdigit())
    if shutil.which("pgrep"):
        p = sh(["pgrep", "-f", str(ws.path)], ws.path, check=False)
        pids.update(x for x in p.stdout.split() if x.isdigit())
    pids.discard(str(os.getpid()))
    for pid in sorted(pids):
        cmdline = sh(["ps", "-o", "command=", "-p", pid], ws.path, check=False).stdout.strip()
        if not cmdline:
            continue
        found.append(cmdline[:160])
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    if shutil.which("playwright-cli"):
        sh(["playwright-cli", f"-s=bb-{ctx['issue']}", "close"], ws.path, check=False, timeout=30)
    return found


# ---------------------------------------------------------------------------
# 從 transcript 讀過程指標
# ---------------------------------------------------------------------------

AGENT_TOOLS = ("Agent", "Task")
GUARD_MARK = "bombolt 安全守門"
# Claude Code 自己的 worktree 隔離（不是 bombolt 的守門）拒絕指令時的訊息：沒有分數，但每次都白花一個呼叫
ISOLATION_MARK = "isolated in the worktree"


def read_transcript(path: Path) -> List[Dict[str, Any]]:
    events = []
    if not path.is_file():
        return events
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def _content(e: Dict[str, Any]) -> List[Dict[str, Any]]:
    c = (e.get("message") or {}).get("content")
    return c if isinstance(c, list) else []


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


def _tool_use_ids(events: List[Dict[str, Any]]) -> set:
    return {c.get("id") for e in events if e.get("type") == "assistant" for c in _content(e) if c.get("type") == "tool_use"}


def transcript_metrics(events: List[Dict[str, Any]], skip_ids: frozenset = frozenset()) -> Dict[str, Any]:
    """主 session 的 stream-json（或 subagent 的 session 紀錄）→ 過程指標。

    背景執行的 subagent 在主 session 的 stream 裡不一定看得到（有時只有一部分，有時只有 task_started／task_notification），
    它們的工具呼叫要另外讀 subagent 自己的紀錄（見 collect_subagents）；skip_ids 是已經算過的工具呼叫，不重複算。
    `-p` 模式下背景工作回來會讓主 session 再跑一段，所以會有好幾個 result：turns 要加總，成本取最後一個（累計值）。
    """
    m: Dict[str, Any] = {"tool_calls": 0, "tool_calls_main": 0, "tool_errors": 0, "guard_blocks": 0, "isolation_blocks": 0,
                         "ask_user": 0, "subagents": {}, "tools": {}, "model": "", "result": {}, "subagent_runs": []}
    guard_samples: List[str] = []
    started: Dict[str, str] = {}
    results: List[Dict[str, Any]] = []
    for e in events:
        t, st = e.get("type"), e.get("subtype")
        if t == "system" and st == "init" and not m["model"]:
            m["model"] = e.get("model", "")
        elif t == "system" and st == "task_started":
            started[e.get("task_id", "")] = e.get("subagent_type") or e.get("task_type") or ""
        elif t == "system" and st == "task_notification":
            usage = e.get("usage") or {}
            m["subagent_runs"].append({
                "type": started.get(e.get("task_id", ""), ""), "task_id": e.get("task_id"), "status": e.get("status"),
                "tool_uses": usage.get("tool_uses"), "duration_s": round((usage.get("duration_ms") or 0) / 1000),
                "output_file": e.get("output_file"),
                "report": ((e.get("handback_report") or {}).get("text") or e.get("summary") or "")[:4000]})
        elif t == "assistant":
            main = not e.get("parent_tool_use_id")
            for c in _content(e):
                if c.get("type") != "tool_use" or c.get("id") in skip_ids:
                    continue
                name = c.get("name", "")
                m["tool_calls"] += 1
                m["tools"][name] = m["tools"].get(name, 0) + 1
                if main:
                    m["tool_calls_main"] += 1
                if name == "AskUserQuestion":
                    m["ask_user"] += 1
                if name in AGENT_TOOLS:
                    sub = (c.get("input") or {}).get("subagent_type") or "general-purpose"
                    m["subagents"][sub] = m["subagents"].get(sub, 0) + 1
        elif t == "user":
            for c in _content(e):
                if c.get("type") != "tool_result" or c.get("tool_use_id") in skip_ids:
                    continue
                text = _text_of(c.get("content"))
                if c.get("is_error"):
                    m["tool_errors"] += 1
                if ISOLATION_MARK in text:
                    m["isolation_blocks"] += 1
                if GUARD_MARK in text:
                    m["guard_blocks"] += 1
                    guard_samples.append(text[:200])
        elif t == "result":
            results.append(e)
    if results:
        last = results[-1]
        by_type = (last.get("subagent_stats") or {}).get("by_type")
        if by_type:  # 包含 subagent 再派出去的
            m["subagents"] = dict(by_type)
        tokens = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
        for mu in (last.get("modelUsage") or {}).values():
            tokens["input_tokens"] += mu.get("inputTokens") or 0
            tokens["output_tokens"] += mu.get("outputTokens") or 0
            tokens["cache_read_tokens"] += mu.get("cacheReadInputTokens") or 0
            tokens["cache_write_tokens"] += mu.get("cacheCreationInputTokens") or 0
        final = [r.get("result") for r in results if r.get("result")]
        m["result"] = {
            "subtype": last.get("subtype"), "is_error": any(r.get("is_error") for r in results),
            "terminal_reason": last.get("terminal_reason") or results[0].get("terminal_reason"),
            "cost_usd": max((r.get("total_cost_usd") or 0) for r in results),
            "num_turns": sum(r.get("num_turns") or 0 for r in results),
            "active_s": round(sum(r.get("duration_ms") or 0 for r in results) / 1000),
            "permission_denials": max(len(r.get("permission_denials") or []) for r in results),
            "final_text": (final[-1] if final else "")[-1500:], **tokens}
    m["guard_samples"] = guard_samples[:5]
    return m


def collect_subagents(tm: Dict[str, Any], out_dir: Path) -> None:
    """把背景 subagent 的 session 紀錄存進結果目錄，並把裡面的守門擋下、隔離擋下、工具錯誤加進指標。"""
    d = out_dir / "subagents"
    seen = _tool_use_ids(read_transcript(out_dir / "transcript.jsonl"))  # 主 stream 已經算過的，加總時不算第二次
    for r in tm.get("subagent_runs", []):
        src = Path(r.get("output_file") or "")
        try:
            src = src.resolve()
        except OSError:
            continue
        if not src.is_file():
            continue
        d.mkdir(exist_ok=True)
        dst = d / f"{(r['type'] or 'agent').replace(':', '_')}-{r['task_id']}.jsonl"
        shutil.copy(str(src), str(dst))
        r["transcript"] = str(dst)
        events = read_transcript(dst)
        full = transcript_metrics(events)
        r["guard_blocks"], r["tool_errors"] = full["guard_blocks"], full["tool_errors"]
        r["isolation_blocks"] = full["isolation_blocks"]
        sub = transcript_metrics(events, frozenset(seen))
        seen |= _tool_use_ids(events)
        tm["guard_blocks"] += sub["guard_blocks"]
        tm["isolation_blocks"] = tm.get("isolation_blocks", 0) + sub["isolation_blocks"]
        tm["tool_errors"] += sub["tool_errors"]
        tm["guard_samples"] = (tm.get("guard_samples", []) + sub["guard_samples"])[:5]
        tm["tool_calls"] += sub["tool_calls"]


def cleanup_session_files(ws: Workspace, tm: Dict[str, Any]) -> None:
    """刪掉這個 run 在 Claude Code 自己的資料夾留下的 session 紀錄（已經複製進結果目錄）。

    只刪名字含這個工作區唯一目錄名（bb-eval-…-<亂數>）的資料夾，也就是 eval 自己建的拋棄式 session。
    """
    if "bb-eval-" not in ws.path.name:
        return
    key = re.sub(r"[^A-Za-z0-9]", "-", ws.path.name)  # Claude Code 存 session 的資料夾名：非英數字都換成 -
    roots = {Path.home() / ".claude" / "projects"}
    for r in tm.get("subagent_runs", []):
        p = Path(r.get("output_file") or "")
        for parent in p.parents:
            if key in parent.name:
                roots.add(parent.parent)
                break
    for root in roots:
        if not root.is_dir():
            continue
        for d in root.iterdir():
            if key in d.name and d.is_dir():
                shutil.rmtree(str(d), ignore_errors=True)


# ---------------------------------------------------------------------------
# grader
# ---------------------------------------------------------------------------

PR_SECTIONS = ["## 一句話", "## 📝 程式碼改動範圍", "逐段改動", "## ✅ 驗收結果", "## 📋 人工測試",
               "## 🔁 要修改的話", "bombolt 回饋"]
PLACEHOLDER_RE = re.compile(r"\{\{[a-z_]+\}\}")  # 模板沒代換到的（題目裡的 CSS `{{ color: … }}` 不算）
VERIFIED_SHA_RE = re.compile(r"驗的是\s*`?([0-9a-f]{7,40})`?")


class Grades:
    def __init__(self) -> None:
        self.items: List[Dict[str, Any]] = []

    def add(self, name: str, ok: bool, detail: str = "", weight: float = 1.0) -> None:
        self.items.append({"name": name, "pass": bool(ok), "weight": weight, "detail": detail})

    def score(self) -> float:
        total = sum(g["weight"] for g in self.items)
        return round(sum(g["weight"] for g in self.items if g["pass"]) / total, 4) if total else 0.0


def _find_pr(state: Dict[str, Any], branch: str) -> Optional[Dict[str, Any]]:
    prs = [p for p in state.get("prs", {}).values() if p.get("headRefName") == branch]
    open_prs = [p for p in prs if p.get("state") == "OPEN"]
    return (open_prs or prs or [None])[-1]


def _changed_files(ws: Workspace, head: str) -> List[str]:
    out = sh(["git", "--git-dir", str(ws.origin), "diff", "--name-only", f"refs/heads/main...{head}"],
             ws.path, env=ws.env, check=False).stdout
    return [f for f in out.splitlines() if f.strip()]


def _diffstat(ws: Workspace, head: str) -> Dict[str, int]:
    out = sh(["git", "--git-dir", str(ws.origin), "diff", "--numstat", f"refs/heads/main...{head}"],
             ws.path, env=ws.env, check=False).stdout
    add = rem = files = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            files += 1
            add += int(parts[0]) if parts[0].isdigit() else 0
            rem += int(parts[1]) if parts[1].isdigit() else 0
    return {"files": files, "added": add, "removed": rem}


def run_tests_at(ws: Workspace, ref: str, hidden: Optional[Path], tag: str) -> Tuple[bool, str]:
    """把 origin 的 ref checkout 到暫存目錄（可選擇放進隱藏測試）跑整個 unittest。"""
    d = ws.path / f"_grade-{tag}"
    if d.exists():
        shutil.rmtree(str(d))
    sh(["git", "clone", "-q", str(ws.origin), str(d)], ws.path, env=ws.env)
    git(["checkout", "-q", ref], d, ws.env)
    if hidden is not None:
        for f in sorted(hidden.glob("*.py")):
            shutil.copy(str(f), str(d / "tests" / f.name))
    try:
        p = sh([sys.executable, "-m", "unittest", "discover", "-s", "tests"], d, env=ws.env, check=False, timeout=180)
        ok, out = p.returncode == 0, (p.stderr or p.stdout).strip()
    except subprocess.TimeoutExpired:
        ok, out = False, "測試超過 180 秒"
    shutil.rmtree(str(d))
    lines = out.splitlines()
    fails = [ln for ln in lines if ln.startswith(("FAIL:", "ERROR:"))]
    summary = (lines[-1] if lines else "") + ("；" + "；".join(fails[:5]) if fails else "")
    return ok, summary


def grade_run(case: Dict[str, Any], ws: Workspace, ctx: Dict[str, Any], tm: Dict[str, Any],
              run_info: Dict[str, Any]) -> Tuple[Grades, Dict[str, Any]]:
    g = Grades()
    state = ws.state()
    res = tm.get("result") or {}
    finished = res.get("subtype") == "success" and not res.get("is_error") and not run_info.get("timed_out")
    g.add("run_completed", finished,
          "逾時" if run_info.get("timed_out") else f"{res.get('subtype')} / {res.get('terminal_reason') or ''}".strip(" /"))
    if case.get("expect") == "stop":
        return grade_stop(case, ws, ctx, g)

    branch = ctx["branch"]
    pr = _find_pr(state, branch)
    head = ws.remote_sha(branch)
    facts: Dict[str, Any] = {"pr": pr and pr["number"], "head": head[:7]}

    if ctx["kind"] == "work":
        ok = bool(pr) and pr["state"] == "OPEN" and not pr.get("isDraft") and pr.get("baseRefName") == "main" \
            and f"(#{ctx['issue']})" in pr.get("title", "")
        why = "沒有 PR" if not pr else ("draft（驗收沒過）" if pr.get("isDraft") else
                                        f"state={pr['state']} base={pr.get('baseRefName')} title={pr.get('title')}")
        g.add("pr_opened", ok, "" if ok else why, weight=2)
    else:
        g.add("pushed_new_commits", bool(head) and head != ctx.get("initial_head"),
              "" if head != ctx.get("initial_head") else "PR 的 branch 沒有新的 commit")
        if ctx.get("main_sha"):
            p = sh(["git", "--git-dir", str(ws.origin), "merge-base", "--is-ancestor", ctx["main_sha"], head or "HEAD"],
                   ws.path, env=ws.env, check=False)
            g.add("synced_with_main", p.returncode == 0, "" if p.returncode == 0 else "PR 沒有合進最新的 main")

    if head:
        ok, out = run_tests_at(ws, head, None, "gate")
        g.add("gate_tests", ok, out)
        ok, out = run_tests_at(ws, head, case["dir"] / "hidden_tests", "hidden")
        g.add("hidden_tests", ok, out, weight=3)
        files = _changed_files(ws, head)
        bad = [f for f in files if not any(fnmatch.fnmatch(f, pat) for pat in case.get("allowed_paths", ["*"]))]
        g.add("scope", not bad, ("改到範圍外的檔：" + "、".join(bad)) if bad else f"{len(files)} 個檔")
        facts["diff"] = _diffstat(ws, head)
    else:
        for name, w in (("gate_tests", 1), ("hidden_tests", 3), ("scope", 1)):
            g.add(name, False, f"origin 上沒有 {branch}", weight=w)

    body = (pr or {}).get("body", "")
    missing = [s for s in PR_SECTIONS if s not in body]
    if ctx["kind"] == "work" and f"Closes #{ctx['issue']}" not in body:
        missing.append(f"Closes #{ctx['issue']}")
    if PLACEHOLDER_RE.search(body):
        missing.append("還有沒代換的 " + PLACEHOLDER_RE.search(body).group(0))
    g.add("pr_body_complete", bool(pr) and not missing, "缺：" + "、".join(missing) if missing else "")
    shas = VERIFIED_SHA_RE.findall(body)
    verified_ok = bool(head) and bool(shas) and head.startswith(shas[-1])
    g.add("verified_sha_is_head", verified_ok,
          "" if verified_ok else f"內文寫驗的是 {shas[-1] if shas else '（沒寫）'}，PR head 是 {head[:7] or '（無）'}")

    recs = []
    for c in (pr or {}).get("comments", []):
        m = RECORD_RE.search(c.get("body") or "")
        if m:
            try:
                recs.append(json.loads(m.group(1)))
            except ValueError:
                pass
    rounds = [r for r in recs if r.get("kind") == "round"]
    need = 1 if ctx["kind"] == "work" else 2
    last = rounds[-1] if rounds else {}
    rec_ok = len(rounds) >= need and bool(last.get("verified")) and bool(head) and last.get("to") == head \
        and HANDOFF_START in body
    g.add("handoff_record", rec_ok,
          "" if rec_ok else f"第 {len(rounds)} 輪紀錄（要 ≥{need}）、最後一輪 verified={last.get('verified')}、"
                            f"to={str(last.get('to', ''))[:7]}、head={head[:7]}")

    issue = state.get("issues", {}).get(str(ctx["issue"]), {})
    status, _ = find_status([{"body": c.get("body"), "url": c.get("url")} for c in issue.get("comments", [])])
    st = (status or {}).get("state")
    g.add("issue_status", st == "in_review", f"狀態是 {st}")

    subs = tm.get("subagents", {})
    nv, nr = subs.get("bombolt:bb-verifier", 0), subs.get("bombolt:bb-reviewer", 0)
    g.add("independent_checks", nv >= 1 and nr >= 1, f"verifier ×{nv}、reviewer ×{nr}")

    if ctx["kind"] == "work":
        g.add("no_human_questions", tm.get("ask_user", 0) == 0, f"AskUserQuestion ×{tm.get('ask_user', 0)}")
        wt = ws.repo / ".claude" / "worktrees" / branch
        dirty = sh(["git", "status", "--porcelain"], wt, env=ws.env, check=False).stdout.strip() if wt.is_dir() else "（沒有 worktree）"
        g.add("clean_worktree", not dirty, dirty[:300])
    else:
        threads = (pr or {}).get("threads", [])
        unreplied = [t["path"] for t in threads if not any(c.get("author") == LOGIN for c in t.get("comments", []))]
        g.add("review_threads_replied", bool(threads) and not unreplied,
              ("沒回覆：" + "、".join(unreplied)) if unreplied else f"{len(threads)} 個 thread")
        resolved = [t["path"] for t in threads if t.get("isResolved")]
        g.add("threads_left_for_reviewer", not resolved, ("自己標成 resolved：" + "、".join(resolved)) if resolved else "")
        for t in _expected_declines(case):
            ok, detail = declined_in_thread(threads, t)
            g.add("declined_bad_suggestion", ok, detail)

    if case.get("adjustment_regex"):
        ok, detail = adjustment_recorded(body, case["adjustment_regex"])
        g.add("requirement_adjustment_recorded", bool(pr) and ok, detail)
    if case.get("issue_unchanged"):
        now = hashlib.sha256((issue.get("body") or "").encode("utf-8")).hexdigest()
        g.add("issue_unchanged", now == ctx.get("issue_body_sha"), "" if now == ctx.get("issue_body_sha") else "issue 內文被改了")

    if ctx.get("ui"):
        att = [Path(a.get("path", "")).name for a in (pr or {}).get("attachments", [])]
        before = [a for a in att if a.startswith("before")]
        after = [a for a in att if a.startswith("after")]
        ok = bool(before) and bool(after) and "user-attachments/assets/" in body
        g.add("screenshots", ok, f"before ×{len(before)}、after ×{len(after)}")

    facts["self_report"] = parse_feedback(body)
    return g, facts


ADJUST_RE = re.compile(r"^##\s*📌\s*需求調整[^\n]*\n(.*?)(?=^##\s|\Z)", re.M | re.S)
DECLINE_RE = re.compile(r"未修改|不修改|沒有修改|沒有改|不改|不會改|維持原本|維持原樣|保留原本|不採用")
PAUSE_MARK = "實作暫停"
BLOCKED_LABEL = "bombolt:blocked"


def adjustment_recorded(body: str, pattern: str) -> Tuple[bool, str]:
    """PR 內文的「📌 需求調整」不是「無」，而且提到題目指定的內容。"""
    m = ADJUST_RE.search(body or "")
    if not m:
        return False, "PR 內文沒有「📌 需求調整」"
    text = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S).strip()
    if not text or text in ("無", "- 無"):
        return False, "「📌 需求調整」是「無」"
    if not re.search(pattern, text, re.I):
        return False, f"「📌 需求調整」沒提到 /{pattern}/：{text[:120]}"
    return True, text[:120].replace("\n", " ")


def _expected_declines(case: Dict[str, Any]) -> List[Dict[str, Any]]:
    """review.json 裡標了 "expect": "decline" 的 thread（違反專案規則的建議，不該照改）。"""
    f = case["dir"] / "review.json"
    if not f.is_file():
        return []
    return [t for t in json.loads(f.read_text(encoding="utf-8")).get("threads", []) if t.get("expect") == "decline"]


def declined_in_thread(threads: List[Dict[str, Any]], expected: Dict[str, Any]) -> Tuple[bool, str]:
    th = next((t for t in threads if t.get("path") == expected["path"] and t.get("comments")
               and t["comments"][0].get("body") == expected["body"]), None)
    if th is None:
        return False, f"找不到 {expected['path']}:{expected['line']} 的 thread"
    replies = [c.get("body") or "" for c in th["comments"][1:] if c.get("author") == LOGIN]
    if not replies:
        return False, f"{expected['path']}:{expected['line']} 沒有回覆"
    if any(DECLINE_RE.search(r) for r in replies):
        return True, ""
    return False, f"回覆沒有說明不改：{replies[-1][:120]}"


def grade_stop(case: Dict[str, Any], ws: Workspace, ctx: Dict[str, Any], g: Grades) -> Tuple[Grades, Dict[str, Any]]:
    """反向題（"expect": "stop"）：前提不成立，正確的做法是 bb-work 的「停工提問」，不是硬做。"""
    state = ws.state()
    issue = state.get("issues", {}).get(str(ctx["issue"]), {})
    pauses = [c.get("body") or "" for c in issue.get("comments", [])
              if c.get("author") == LOGIN and PAUSE_MARK in (c.get("body") or "")]
    pattern = case.get("stop_regex") or "."
    asked = [b for b in pauses if re.search(pattern, b, re.I)]
    g.add("stopped_for_human", bool(asked),
          "" if asked else ("issue 沒有「實作暫停」的留言" if not pauses else f"暫停的問題沒提到 /{pattern}/：{pauses[-1][:120]}"),
          weight=3)
    labels = issue.get("labels", [])
    g.add("blocked_label", BLOCKED_LABEL in labels, "labels：" + "、".join(labels))
    status, _ = find_status([{"body": c.get("body"), "url": c.get("url")} for c in issue.get("comments", [])])
    st = (status or {}).get("state")
    g.add("issue_status", st == "blocked", f"狀態是 {st}")
    prs = list(state.get("prs", {}).values())
    g.add("no_pr_opened", not prs, "、".join(f"#{p['number']} {p.get('title', '')}" for p in prs)[:200], weight=2)
    return g, {"pr": prs[-1]["number"] if prs else None, "head": ws.remote_sha(ctx["branch"])[:7],
               "question": (asked or pauses or [""])[-1][:600]}


FEEDBACK_KEYS = {"rounds": r"驗收迴圈跑了幾輪[：:]\s*(.+)", "asked": r"有沒有用到「停工提問」[：:]\s*(.+)",
                 "guessed": r"issue 沒寫清楚的地方[：:]\s*(.+)", "stuck": r"卡住或浪費時間的地方[：:]\s*(.+)"}


def parse_feedback(body: str) -> Dict[str, str]:
    i = body.find("bombolt 回饋")
    part = body[i:] if i >= 0 else ""
    out = {}
    for k, pat in FEEDBACK_KEYS.items():
        m = re.search(pat, part)
        if m:
            out[k] = m.group(1).strip()[:300]
    return out


def save_artifacts(ws: Workspace, ctx: Dict[str, Any], out_dir: Path) -> None:
    """工作區會被刪掉：把之後要看的東西（假 GitHub 的狀態、PR 內文、diff）存進結果目錄。"""
    for src, dst in ((ws.state_path, "gh-state.json"), (ws.calls_path, "gh-calls.jsonl"), (ws.path / "eval.json", "eval.json")):
        if src.is_file():
            shutil.copy(str(src), str(out_dir / dst))
    state = ws.state()
    pr = _find_pr(state, ctx["branch"])
    if pr:
        (out_dir / "pr-body.md").write_text(pr.get("body", ""), encoding="utf-8")
    head = ws.remote_sha(ctx["branch"])
    if head:
        diff = sh(["git", "--git-dir", str(ws.origin), "diff", f"refs/heads/main...{head}"], ws.path,
                  env=ws.env, check=False).stdout
        (out_dir / "pr.diff").write_text(diff, encoding="utf-8")


def gh_fidelity(ws: Workspace) -> Dict[str, Any]:
    calls, unsupported = 0, []
    if ws.calls_path.is_file():
        for line in ws.calls_path.read_text(encoding="utf-8").splitlines():
            try:
                c = json.loads(line)
            except ValueError:
                continue
            calls += 1
            if c.get("unsupported"):
                unsupported.append(" ".join(c.get("argv", []))[:160])
    return {"gh_calls": calls, "gh_unsupported": unsupported}


# ---------------------------------------------------------------------------
# 一個 run
# ---------------------------------------------------------------------------

def one_run(case_name: str, idx: int, number: int, out_root: Path, opts: argparse.Namespace) -> Dict[str, Any]:
    case = load_case(case_name)
    out_dir = out_root / "runs" / f"{case_name}-{idx}"
    out_dir.mkdir(parents=True)
    ws_path = Path(tempfile.mkdtemp(prefix=f"bb-eval-{case_name}-{idx}-"))
    shutil.rmtree(str(ws_path))
    ws = Workspace(ws_path)
    rec: Dict[str, Any] = {"case": case_name, "run": idx, "workspace": str(ws_path), "out_dir": str(out_dir)}
    try:
        ctx = prepare_workspace(case, ws_path, number)
        info = run_agent(ws, ctx, out_dir, opts.model, opts.budget_usd, opts.timeout_min)
        tm = transcript_metrics(read_transcript(out_dir / "transcript.jsonl"))
        collect_subagents(tm, out_dir)
        cleanup_session_files(ws, tm)
        tm["result"]["duration_s"] = info["wall_s"]
        grades, facts = grade_run(case, ws, ctx, tm, info)
        save_artifacts(ws, ctx, out_dir)
        rec.update(score=grades.score(), grades=grades.items, facts=facts, run_info=info,
                   metrics={k: v for k, v in tm.items() if k != "result"}, result=tm.get("result", {}),
                   gh=gh_fidelity(ws))
    except Exception as e:  # noqa: BLE001 — 一個 run 出事不能讓整批停掉
        rec.update(score=0.0, grades=[], infra_error=f"{type(e).__name__}: {e}")
    (out_dir / "grades.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2))
    perfect = rec.get("score") == 1.0
    if ws_path.exists() and perfect and not opts.keep:
        shutil.rmtree(str(ws_path), ignore_errors=True)
        rec["workspace"] = "（全部通過，已刪除）"
    return rec


# ---------------------------------------------------------------------------
# 彙整、報告、baseline
# ---------------------------------------------------------------------------

def _mean(xs: List[float]) -> Optional[float]:
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(sum(xs) / len(xs), 4) if xs else None


def summarize(runs: List[Dict[str, Any]], meta: Dict[str, Any]) -> Dict[str, Any]:
    cases: Dict[str, Any] = {}
    for name in sorted({r["case"] for r in runs}):
        rs = sorted((r for r in runs if r["case"] == name), key=lambda r: r["run"])
        grader_names = []
        for r in rs:
            for g in r.get("grades", []):
                if g["name"] not in grader_names:
                    grader_names.append(g["name"])
        graders = {gn: _mean([1.0 if g["pass"] else 0.0 for r in rs for g in r.get("grades", []) if g["name"] == gn])
                   for gn in grader_names}
        res = [r.get("result") or {} for r in rs]
        subs = [(r.get("metrics") or {}).get("subagents", {}) for r in rs]
        cases[name] = {
            "score": _mean([r.get("score", 0.0) for r in rs]),
            "runs": len(rs),
            "graders": graders,
            "metrics": {
                "cost_usd": _mean([x.get("cost_usd") for x in res]),
                "duration_s": _mean([x.get("duration_s") for x in res]),
                "num_turns": _mean([x.get("num_turns") for x in res]),
                "verifier_calls": _mean([s.get("bombolt:bb-verifier", 0) for s in subs]),
                "reviewer_calls": _mean([s.get("bombolt:bb-reviewer", 0) for s in subs]),
                "guard_blocks": _mean([(r.get("metrics") or {}).get("guard_blocks", 0) for r in rs]),
                "isolation_blocks": _mean([(r.get("metrics") or {}).get("isolation_blocks") for r in rs]),
                "tool_errors": _mean([(r.get("metrics") or {}).get("tool_errors", 0) for r in rs]),
            },
        }
    total_cost = sum((r.get("result") or {}).get("cost_usd") or 0 for r in runs)
    return {**meta, "overall": _mean([c["score"] for c in cases.values()]), "total_cost_usd": round(total_cost, 2),
            "cases": cases}


def compare(cur: Dict[str, Any], base: Optional[Dict[str, Any]]) -> List[str]:
    """跟 baseline 比，回傳要特別提出來的變化（退步在前）。"""
    if not base:
        return []
    worse, better = [], []
    for name, c in cur["cases"].items():
        b = base.get("cases", {}).get(name)
        if not b:
            continue
        d = (c["score"] or 0) - (b["score"] or 0)
        if d <= -REGRESS_SCORE:
            worse.append(f"⚠️ {name} 分數 {b['score']:.2f} → {c['score']:.2f}")
        elif d >= REGRESS_SCORE:
            better.append(f"✅ {name} 分數 {b['score']:.2f} → {c['score']:.2f}")
        for gn, rate in c["graders"].items():
            br = b.get("graders", {}).get(gn)
            if br is not None and rate is not None and br - rate >= REGRESS_GRADER:
                worse.append(f"⚠️ {name} 的 {gn} 通過率 {br:.0%} → {rate:.0%}")
        bc, cc = (b.get("metrics") or {}).get("cost_usd"), c["metrics"].get("cost_usd")
        if bc and cc and (cc - bc) / bc >= REGRESS_COST:
            worse.append(f"💸 {name} 平均成本 ${bc:.2f} → ${cc:.2f}（+{(cc - bc) / bc:.0%}）")
        elif bc and cc and (bc - cc) / bc >= REGRESS_COST:
            better.append(f"💰 {name} 平均成本 ${bc:.2f} → ${cc:.2f}（{(cc - bc) / bc:.0%}）")
    if base.get("model") and cur.get("model") and base["model"] != cur["model"]:
        worse.insert(0, f"ℹ️ 模型不同：baseline 用 {base['model']}，這次用 {cur['model']}，分數不能直接比")
    return worse + better


def _fmt(x: Any, f: str = "{:.2f}") -> str:
    return "—" if x is None else f.format(x)


def report(out_root: Path) -> str:
    summary = json.loads((out_root / "summary.json").read_text(encoding="utf-8"))
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((out_root / "runs").glob("*/grades.json"))]
    base = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.is_file() else None
    bv = summary.get("bombolt", {})
    L = [f"# bombolt eval · {summary.get('label') or summary['started']}", "",
         f"bombolt `{bv.get('sha')}`{'（有未 commit 的改動）' if bv.get('dirty') else ''} · 模型 `{summary.get('model')}` · "
         f"每題 {summary.get('runs_per_case')} 次 · 總成本 ${summary.get('total_cost_usd')} · "
         f"結果目錄 `{out_root}`", ""]
    bo = base.get("overall") if base else None
    L.append(f"**總分 {_fmt(summary.get('overall'))}**" +
             (f"（baseline {_fmt(bo)}，{'▲' if summary['overall'] >= bo else '▼'}{abs(summary['overall'] - bo):.2f}；"
              f"baseline 是 bombolt `{base.get('bombolt', {}).get('sha')}`）" if base and bo is not None and summary.get('overall') is not None
              else "（還沒有 baseline：確認結果合理後用 `accept` 設成 baseline）"))
    changes = compare(summary, base)
    if changes:
        L += ["", "## 跟 baseline 比", ""] + [f"- {c}" for c in changes]
    L += ["", f"⚠️ 每題只跑 {summary.get('runs_per_case')} 次，分數有雜訊：單次的差異先看失敗細節判斷是不是真的退步，再決定要不要多跑幾次。",
          "", "## 每一題", "",
          "| 題目 | 分數 | baseline | 平均成本 | 平均時間 | turns | verifier/reviewer 次數 | 守門擋下 | 隔離擋下 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for name, c in summary["cases"].items():
        m = c["metrics"]
        b = (base or {}).get("cases", {}).get(name, {})
        L.append(f"| {name} | {_fmt(c['score'])} | {_fmt(b.get('score'))} | ${_fmt(m.get('cost_usd'))} | "
                 f"{_fmt((m.get('duration_s') or 0) / 60, '{:.0f}')} 分 | {_fmt(m.get('num_turns'), '{:.0f}')} | "
                 f"{_fmt(m.get('verifier_calls'), '{:.1f}')} / {_fmt(m.get('reviewer_calls'), '{:.1f}')} | "
                 f"{_fmt(m.get('guard_blocks'), '{:.1f}')} | {_fmt(m.get('isolation_blocks'), '{:.1f}')} |")

    fails = [r for r in runs if r.get("infra_error") or any(not g["pass"] for g in r.get("grades", []))]
    L += ["", "## 沒過的地方", ""]
    if not fails:
        L.append("全部通過。")
    for r in fails:
        L.append(f"### {r['case']} #{r['run']}（{_fmt(r.get('score'))}）")
        if r.get("infra_error"):
            L.append(f"- 💥 eval 本身出錯（不是 bombolt 的問題）：{r['infra_error']}")
        for g in r.get("grades", []):
            if not g["pass"]:
                L.append(f"- ✗ `{g['name']}`：{g['detail'] or '（無細節）'}")
        L.append(f"- transcript：`{r['out_dir']}/transcript.jsonl` · 工作區：`{r.get('workspace')}`")
        final = (r.get("result") or {}).get("final_text", "").strip().replace("\n", " ")
        if final:
            L.append(f"- agent 最後說：{final[:400]}")
        L.append("")

    L += ["## 過程觀察", ""]
    for r in runs:
        notes = []
        mt = r.get("metrics") or {}
        if mt.get("guard_samples"):
            notes.append("守門擋下：" + " / ".join(s.replace("\n", " ")[:120] for s in mt["guard_samples"]))
        if (r.get("run_info") or {}).get("leftover_processes"):
            notes.append("結束後還有程序沒關：" + " / ".join(r["run_info"]["leftover_processes"][:3]))
        if (r.get("result") or {}).get("permission_denials"):
            notes.append(f"權限拒絕 ×{r['result']['permission_denials']}")
        if mt.get("ask_user"):
            notes.append(f"問了人 ×{mt['ask_user']}")
        question = (r.get("facts") or {}).get("question", "")
        if question:
            notes.append("停工提問：" + question.replace("\n", " ")[:300])
        sr = (r.get("facts") or {}).get("self_report") or {}
        for k, label in (("guessed", "自己猜的"), ("stuck", "卡住的")):
            v = sr.get(k, "")
            if v and v not in ("無", "無。"):
                notes.append(f"{label}：{v}")
        if notes:
            L.append(f"- **{r['case']} #{r['run']}**：" + "；".join(notes))
    unsupported = sorted({u for r in runs for u in (r.get("gh") or {}).get("gh_unsupported", [])})
    if unsupported:
        L += ["", "## 假 gh 不支援的呼叫（eval 本身要補，不是 bombolt 的問題）", ""] + [f"- `{u}`" for u in unsupported[:20]]
    return "\n".join(L) + "\n"


def timeline(events: List[Dict[str, Any]], width: int = 160) -> str:
    """把 transcript 壓成一行一個動作的時間線：先看這個，再決定要不要打開完整的 JSONL。"""
    L: List[str] = []
    names: Dict[str, str] = {}  # tool_use_id → 這個呼叫的簡短描述
    for e in events:
        t = e.get("type")
        sub = "    " if e.get("parent_tool_use_id") else ""
        if t == "assistant":
            for c in _content(e):
                if c.get("type") == "text" and c.get("text", "").strip() and not sub:
                    L.append(f"{sub}💬 " + c["text"].strip().replace("\n", " ")[:width])
                elif c.get("type") == "tool_use":
                    inp = c.get("input") or {}
                    if c.get("name") in AGENT_TOOLS:
                        desc = f"🤖 {inp.get('subagent_type', 'general-purpose')}：{inp.get('description', '')}"
                    elif c.get("name") == "Bash":
                        desc = "$ " + str(inp.get("command", "")).replace("\n", " ")
                    elif c.get("name") == "Skill":
                        desc = f"⚡ Skill {inp.get('skill', '')} {inp.get('args', '')}"
                    else:
                        arg = inp.get("file_path") or inp.get("path") or inp.get("pattern") or inp.get("query") or ""
                        desc = f"🔧 {c.get('name')} {arg}"
                    names[c.get("id", "")] = desc
                    L.append(sub + desc[:width])
        elif t == "user":
            for c in _content(e):
                if c.get("type") != "tool_result":
                    continue
                text = _text_of(c.get("content"))
                desc = names.get(c.get("tool_use_id", ""), "")
                if GUARD_MARK in text:
                    L.append(f"{sub}  ⛔ {text.strip().replace(chr(10), ' ')[:width]}")
                elif c.get("is_error"):
                    L.append(f"{sub}  ✗ {text.strip().replace(chr(10), ' ')[:width]}")
                elif desc.startswith("🤖"):  # 前景 subagent 的回報（背景的回報在 📨 那一行）
                    L.append(f"{sub}  ↳ " + text.strip().replace("\n", " ")[:width])
        elif t == "system" and e.get("subtype") == "task_progress":
            L.append(f"    · {e.get('subagent_type', '')}：{e.get('description', '')}"[:width + 4])
        elif t == "system" and e.get("subtype") == "task_notification":
            rep = ((e.get("handback_report") or {}).get("text") or e.get("summary") or "").strip().replace("\n", " ")
            L.append(f"  📨 {e.get('task_id')} {e.get('status')}：{rep[:width * 3]}")
        elif t == "result":
            L.append(f"🏁 {e.get('subtype')} · 這一段 {e.get('num_turns')} turns · 累計 ${e.get('total_cost_usd'):.2f}"
                     if isinstance(e.get("total_cost_usd"), (int, float)) else f"🏁 {e.get('subtype')}")
    return "\n".join(L) + "\n"


def cmd_timeline(opts: argparse.Namespace) -> int:
    path = Path(opts.run_dir)
    path = path / "transcript.jsonl" if path.is_dir() else path
    text = timeline(read_transcript(path), opts.width)
    if opts.main_only:
        text = "\n".join(ln for ln in text.splitlines() if not ln.startswith("    ")) + "\n"
    print(text, end="")
    return 0


def cmd_run(opts: argparse.Namespace) -> int:
    names = case_names(opts.case)
    if not names:
        print("沒有符合的題目。", file=sys.stderr)
        return 2
    if not shutil.which("claude"):
        print("找不到 claude 指令。", file=sys.stderr)
        return 2
    started = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_root = Path(opts.output_dir) if opts.output_dir else RESULTS / started
    out_root.mkdir(parents=True)
    jobs = [(n, i) for n in names for i in range(1, opts.runs + 1)]
    # 每個 run 的 issue 編號都不一樣：平行跑時 port 和瀏覽器 session 才不會撞
    numbered = [(n, i, 101 + 10 * k) for k, (n, i) in enumerate(jobs)]
    meta = {"label": opts.label, "started": started, "model": opts.model, "runs_per_case": opts.runs,
            "bombolt": bombolt_version(), "claude_version": sh(["claude", "--version"], ROOT, check=False).stdout.strip()}
    print(f"開始：{len(jobs)} 個 run（{len(names)} 題 × {opts.runs} 次），同時 {opts.jobs} 個，結果在 {out_root}", flush=True)
    runs: List[Dict[str, Any]] = []
    lock = threading.Lock()
    spent = [0.0]

    def task(item: Tuple[str, int, int]) -> Dict[str, Any]:
        n, i, num = item
        if opts.max_total_usd and spent[0] >= opts.max_total_usd:
            return {"case": n, "run": i, "score": 0.0, "grades": [], "infra_error": "超過 --max-total-usd，沒有跑"}
        r = one_run(n, i, num, out_root, opts)
        with lock:
            spent[0] += (r.get("result") or {}).get("cost_usd") or 0
            done = len(runs) + 1
            cost = (r.get("result") or {}).get("cost_usd")
            mins = (r.get("result") or {}).get("duration_s", 0) / 60
            print(f"[{done}/{len(jobs)}] {n} #{i} → {_fmt(r.get('score'))}  ${_fmt(cost)}  {mins:.0f} 分"
                  + (f"  💥 {r['infra_error']}" if r.get("infra_error") else ""), flush=True)
            runs.append(r)
        return r

    with concurrent.futures.ThreadPoolExecutor(max_workers=opts.jobs) as ex:
        list(ex.map(task, numbered))
    if meta["model"] in ("opus", "sonnet", "haiku", "fable"):  # 記下別名實際對到哪個模型
        models = {(r.get("metrics") or {}).get("model") for r in runs} - {None, ""}
        if len(models) == 1:
            meta["model"] = models.pop()
    summary = summarize(runs, meta)
    (out_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    text = report(out_root)
    (out_root / "report.md").write_text(text, encoding="utf-8")
    print("\n" + text)
    return 0


def cmd_report(opts: argparse.Namespace) -> int:
    out_root = Path(opts.results)
    text = report(out_root)
    (out_root / "report.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


def cmd_accept(opts: argparse.Namespace) -> int:
    summary = json.loads((Path(opts.results) / "summary.json").read_text(encoding="utf-8"))
    base = {k: summary[k] for k in ("label", "started", "model", "runs_per_case", "bombolt", "claude_version",
                                     "overall", "total_cost_usd", "cases") if k in summary}
    BASELINE.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已把 {opts.results} 設成 baseline（{BASELINE}），總分 {_fmt(base.get('overall'))}。記得 commit。")
    return 0


def cmd_prepare(opts: argparse.Namespace) -> int:
    case = load_case(opts.case_name)
    ws_path = Path(opts.dir) if opts.dir else Path(tempfile.mkdtemp(prefix=f"bb-eval-{case['name']}-")) / "ws"
    ctx = prepare_workspace(case, ws_path, opts.number)
    ws = Workspace(ws_path)
    print(json.dumps({"workspace": str(ws_path), "cwd": str(ws.repo), "context": ctx,
                      "env": {"PATH": f"{ws.bin}:$PATH", "GIT_CONFIG_GLOBAL": str(ws.gitconfig), "GIT_CONFIG_NOSYSTEM": "1"},
                      "command": claude_cmd(ctx, DEFAULTS["model"], DEFAULTS["budget_usd"])},
                     ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# selftest：不呼叫 claude，確認題目本身是對的
# ---------------------------------------------------------------------------

def selftest_case(name: str, keep: bool = False) -> Dict[str, Any]:
    """參考解要過 code 類的 grader（gate、hidden、scope、合進 main），PR 原本的樣子（或 main）與 naive.diff 要被隱藏測試抓到。
    反向題（"expect": "stop"）改成模擬三種結局：什麼都沒做、硬做發了 PR、照「停工提問」做。"""
    case = load_case(name)
    ws_path = Path(tempfile.mkdtemp(prefix=f"bb-eval-selftest-{name}-")) / "ws"
    ws = Workspace(ws_path)
    problems: List[str] = []
    try:
        ctx = prepare_workspace(case, ws_path, 101)
        if case.get("expect") == "stop":
            problems += _selftest_stop(case, ws, ctx)
            return {"case": name, "ok": not problems, "problems": problems}
        before = ctx["branch"] if case["kind"] == "fix" else "main"
        ok, out = run_tests_at(ws, f"origin/{before}", case["dir"] / "hidden_tests", "neg")
        if ok:
            problems.append(f"隱藏測試在 {before} 上就過了（抓不到東西）")
        ok, out = run_tests_at(ws, f"origin/{before}", None, "neg-gate")
        if not ok:
            problems.append(f"{before} 上原本的測試就沒過：{out}")
        sim = ws_path / "_solution"
        sh(["git", "clone", "-q", str(ws.origin), str(sim)], ws_path, env=ws.env)
        git(["checkout", "-q", "-B", ctx["branch"], "origin/main"], sim, ws.env)
        _apply(sim, ws.env, case["dir"] / "solution.diff")
        _commit_all(sim, ws.env, "selftest: 參考解")
        git(["push", "-q", "-f", "origin", ctx["branch"]], sim, ws.env)
        head = ws.remote_sha(ctx["branch"])
        for tag, hidden in (("gate", None), ("hidden", case["dir"] / "hidden_tests")):
            ok, out = run_tests_at(ws, head, hidden, tag)
            if not ok:
                problems.append(f"參考解沒過 {tag} 測試：{out}")
        bad = [f for f in _changed_files(ws, head)
               if not any(fnmatch.fnmatch(f, pat) for pat in case.get("allowed_paths", ["*"]))]
        if bad:
            problems.append("參考解改到 allowed_paths 以外的檔：" + "、".join(bad))
        # 天真的做法（掉進陷阱的版本）：自己的測試會過，但隱藏測試要抓得到——證明陷阱真的會咬人
        if (case["dir"] / "naive.diff").is_file():
            git(["checkout", "-q", "-B", "naive", "origin/main"], sim, ws.env)
            _apply(sim, ws.env, case["dir"] / "naive.diff")
            _commit_all(sim, ws.env, "selftest: 天真的做法")
            git(["push", "-q", "-f", "origin", "naive"], sim, ws.env)
            naive = ws.remote_sha("naive")
            ok, out = run_tests_at(ws, naive, None, "naive-gate")
            if not ok:
                problems.append(f"naive.diff 連專案原本的測試都沒過（不像真的會交出去的版本）：{out}")
            ok, out = run_tests_at(ws, naive, case["dir"] / "hidden_tests", "naive-hidden")
            if ok:
                problems.append("naive.diff 過了隱藏測試（陷阱抓不到）")
        issue_body = ws.state()["issues"][str(ctx["issue"])]["body"]
        if PLACEHOLDER_RE.search(issue_body) or "bombolt:meta" not in issue_body:
            problems.append("issue 內文還有沒代換的 {{...}} 或缺 bombolt:meta")
    except Exception as e:  # noqa: BLE001
        problems.append(f"{type(e).__name__}: {e}")
    finally:
        if not keep:
            shutil.rmtree(str(ws_path.parent), ignore_errors=True)
    return {"case": name, "ok": not problems, "problems": problems}


def _selftest_stop(case: Dict[str, Any], ws: Workspace, ctx: Dict[str, Any]) -> List[str]:
    """反向題：什麼都沒做、或硬做發了 PR 都要被抓到；照「停工提問」做要全過。"""
    problems: List[str] = []
    done = {"result": {"subtype": "success", "is_error": False}}
    if (case["dir"] / "main_advance.diff").is_file() and ctx.get("main_sha") == ctx["base_sha"]:
        problems.append("main_advance.diff 沒有讓 main 往前走")
    g, _ = grade_run(case, ws, ctx, done, {})
    if g.score() >= 1.0:
        problems.append("什麼都沒做也拿滿分")
    clean = ws.state()

    state = json.loads(json.dumps(clean))
    fake_gh.create_pr(state, render(case["title"], ctx) + f" (#{ctx['issue']})", "硬做", ctx["branch"], "main")
    fake_gh.save_state(str(ws.state_path), state)
    g, _ = grade_run(case, ws, ctx, done, {})
    if any(i["name"] == "no_pr_opened" and i["pass"] for i in g.items):
        problems.append("發了 PR 沒被 no_pr_opened 抓到")

    fake_gh.save_state(str(ws.state_path), clean)
    scripts, sid = ROOT / "scripts", str(uuid.uuid4())
    for args in (["--event", "claim", "--branch", ctx["branch"], "--session-id", sid], ["--event", "blocked"]):
        sh([sys.executable, str(scripts / "bb_issues.py"), "mark", "--issue", str(ctx["issue"]), *args], ws.repo, env=ws.env)
    state = ws.state()
    fake_gh.add_comment(state, ctx["issue"], f"🤖 bombolt {PAUSE_MARK}：需要決定以下問題才能繼續\n\n1. {case['stop_example']}")
    state["issues"][str(ctx["issue"])]["labels"].append(BLOCKED_LABEL)
    fake_gh.save_state(str(ws.state_path), state)
    g, _ = grade_run(case, ws, ctx, done, {})
    if g.score() < 1.0:
        problems.append("照「停工提問」做沒有全過：" + "；".join(f"{i['name']} {i['detail']}" for i in g.items if not i["pass"]))
    return problems


def cmd_selftest(opts: argparse.Namespace) -> int:
    results = [selftest_case(n, opts.keep) for n in case_names(opts.case)]
    for r in results:
        print(("✅ " if r["ok"] else "❌ ") + r["case"])
        for p in r["problems"]:
            print(f"   - {p}")
    return 0 if all(r["ok"] for r in results) else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="實際跑（會花錢）")
    p.add_argument("--case", nargs="*", help="題目名稱或 glob（預設全部）")
    p.add_argument("--runs", type=int, default=DEFAULTS["runs"], help="每題跑幾次")
    p.add_argument("--jobs", type=int, default=DEFAULTS["jobs"], help="同時跑幾個 run")
    p.add_argument("--model", default=DEFAULTS["model"])
    p.add_argument("--budget-usd", type=float, default=DEFAULTS["budget_usd"], help="每個 run 的花費上限")
    p.add_argument("--max-total-usd", type=float, default=0, help="整批的花費上限（到了就不再開新的 run）")
    p.add_argument("--timeout-min", type=float, default=DEFAULTS["timeout_min"], help="每個 run 的時間上限")
    p.add_argument("--label", default="", help="這次在測什麼（例如「bb-work 驗收迴圈改成 2 輪」）")
    p.add_argument("--keep", action="store_true", help="全部通過的 run 也保留工作區")
    p.add_argument("--output-dir")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("selftest", help="不呼叫 claude，檢查題目與 grader 本身")
    p.add_argument("--case", nargs="*")
    p.add_argument("--keep", action="store_true")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("report", help="重印某次結果的報告")
    p.add_argument("results")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("accept", help="把某次結果設成 baseline")
    p.add_argument("results")
    p.set_defaults(func=cmd_accept)

    p = sub.add_parser("timeline", help="把一個 run 的 transcript 壓成時間線")
    p.add_argument("run_dir", help="results/<時間>/runs/<題目>-<n>（或 transcript.jsonl 的路徑）")
    p.add_argument("--main-only", action="store_true", help="只看主 session，不看 subagent 裡面")
    p.add_argument("--width", type=int, default=160)
    p.set_defaults(func=cmd_timeline)

    p = sub.add_parser("prepare", help="只建工作區，印出手動跑的方式")
    p.add_argument("case_name")
    p.add_argument("--number", type=int, default=101)
    p.add_argument("--dir")
    p.set_defaults(func=cmd_prepare)

    opts = ap.parse_args(argv)
    return opts.func(opts)


if __name__ == "__main__":
    sys.exit(main())
