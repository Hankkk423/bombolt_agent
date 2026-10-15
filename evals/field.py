#!/usr/bin/env python3
"""從真實專案的 bombolt PR 撈指標：bombolt 實際用起來，哪裡要人補、哪裡卡住。

evals/run.py 用固定的題目量「改動有沒有改壞」；這支看的是真實的使用情況。
bombolt 把狀態都留在 GitHub 上（PR 的修改紀錄、PR 內文的「🤖 bombolt 回饋」、issue 的狀態），
所以不用另外埋點，讀回來就有資料。

  python3 evals/field.py collect [repo 路徑…] [--refresh]
      撈資料，快取在 evals/results/field-cache/。沒給路徑：~/.claude.json 記的專案裡有 .claude/bombolt.md 的 repo。
      已經 merge／關掉的 PR 不會再變，下次直接用快取；開著的 PR 每次重撈。--refresh 全部重撈。
  python3 evals/field.py report [--since YYYY-MM-DD] [--repo 名字]…
      讀快取，算指標，寫 evals/results/field-<時間>/report.md（數字表格 ＋ 質性原文摘錄）與 metrics.json，也印到 stdout。

對 GitHub 只讀（view／list／api GET／graphql query）。撈到的是專案內容，只放在 gitignore 的 evals/results/ 底下。
只用 python 標準函式庫，要能在 python 3.9 跑。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

EVALS = Path(__file__).resolve().parent
ROOT = EVALS.parent
RESULTS = EVALS / "results"
CACHE = RESULTS / "field-cache"
sys.path.insert(0, str(ROOT / "scripts"))
import bb_issues  # noqa: E402
import bb_lib  # noqa: E402
import bb_sweep  # noqa: E402
from bb_issues import GhError, issue_meta  # noqa: E402
from bb_pr import BRANCH_RE, parse_records  # noqa: E402

PR_FIELDS = ("number,title,url,state,isDraft,createdAt,mergedAt,closedAt,headRefName,headRefOid,baseRefName,"
             "body,comments,reviews,commits,additions,deletions,changedFiles")
ISSUE_FIELDS = "number,title,url,state,createdAt,closedAt,labels,body,comments"
THREADS_QUERY = """
query($owner:String!,$name:String!,$n:Int!){ repository(owner:$owner,name:$name){ pullRequest(number:$n){
  reviewThreads(first:100){ nodes{ isResolved isOutdated path line
    comments(first:50){ nodes{ author{login} body createdAt url } } } } } } }"""
BLOCKED_LABEL = "bombolt:blocked"
STOP_MARK = "bombolt 實作暫停"
FEEDBACK_SUMMARY = "🤖 bombolt 回饋"
FEEDBACK_KEYS = {  # PR 模板「🤖 bombolt 回饋」每一行的開頭 → 欄位
    "驗收迴圈跑了幾輪": "verify_loops",
    "有沒有用到「停工提問」": "used_stop",
    "實作時需要自己判斷": "judgement",
    "卡住或浪費時間的地方": "stuck",
    "改動規模": "size",
}
NONE_WORDS = {"", "無", "沒有", "none", "n/a", "-", "—"}
# 修改紀錄裡值得給人（或 LLM）讀的段落；「改了什麼」diff 就看得到，不收
RECORD_SECTIONS = ("為什麼有這一輪", "決定", "試過但不行", "環境的坑", "紀錄跟 code 不一致的地方", "還沒做", "之後補充")
AGENT_MARKS = ("🤖", "🔀", "🧪", "已修正於", "未修改")  # agent 留言的開頭（紀錄、整合、解衝突、回覆 thread）；含 bombolt 標記的也算
QUOTE_LIMIT = 300


# ---------------------------------------------------------------------------
# 解析（純函式，測試用合成資料）
# ---------------------------------------------------------------------------

def issue_of_branch(branch: str) -> Optional[int]:
    m = BRANCH_RE.match(branch or "")
    return int(m.group(1)) if m else None


def _strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text or "", flags=re.S)


def section(body: str, heading: str) -> Optional[str]:
    """`## <heading>…` 那一節的內文（到下一個 `## ` 或 `<details>`／`Closes` 為止）。沒有這一節回 None。"""
    lines = (body or "").splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## ") and heading in line:
            out = []
            for nxt in lines[i + 1:]:
                if nxt.startswith("## ") or nxt.startswith("<details>") or nxt.startswith("Closes #"):
                    break
                out.append(nxt)
            return _strip_comments("\n".join(out)).strip()
    return None


def is_none(text: Optional[str]) -> bool:
    return re.sub(r"^[-*]\s*", "", (text or "").strip()).strip().strip("。.").lower() in NONE_WORDS


def parse_feedback(body: str) -> Dict[str, Any]:
    """PR 內文的「🤖 bombolt 回饋」→ {verify_loops, used_stop, judgement, stuck, size}；沒有這一節回 {}。"""
    i = (body or "").find(FEEDBACK_SUMMARY)
    if i < 0:
        return {}
    end = body.find("</details>", i)
    block = body[i:end if end >= 0 else len(body)]
    fb: Dict[str, Any] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        for prefix, key in FEEDBACK_KEYS.items():
            if line[2:].startswith(prefix):
                value = re.split(r"[：:]", line[2 + len(prefix):], maxsplit=1)
                fb[key] = (value[1] if len(value) > 1 else value[0]).strip()
    if "verify_loops" in fb:
        m = re.match(r"\d+", fb["verify_loops"])
        fb["verify_loops"] = int(m.group()) if m else None
    if "used_stop" in fb:
        v = fb["used_stop"]
        fb["used_stop"] = not (v.startswith("沒") or v.startswith("無") or v.lower().startswith("no"))
    return fb


def record_sections(comment_body: str) -> Dict[str, str]:
    """修改紀錄留言裡 `**標題**` 開頭的段落 → {標題: 內文}。"""
    text = _strip_comments(comment_body)
    found: Dict[str, str] = {}
    current, buf = None, []  # type: Optional[str], List[str]
    for line in text.splitlines():
        m = re.match(r"^\*\*(.+?)\*\*\s*$", line.strip())
        if m:
            if current:
                found[current] = "\n".join(buf).strip()
            current, buf = m.group(1).strip(), []
        elif line.strip().startswith("</details>"):
            if current:
                found[current] = "\n".join(buf).strip()
            current, buf = None, []
        elif current:
            buf.append(line)
    if current:
        found[current] = "\n".join(buf).strip()
    return {k: v for k, v in found.items() if k in RECORD_SECTIONS and not is_none(v)}


def is_agent_comment(body: str) -> bool:
    b = (body or "").lstrip()
    return "<!-- bombolt:" in b or any(b.startswith(m) for m in AGENT_MARKS)


def commits_after(commits: List[Dict[str, Any]], sha: str) -> Optional[List[Dict[str, Any]]]:
    """PR 的 commit 清單裡，`sha` 之後的 commit；`sha` 不在清單裡（歷史被改寫）回 None。"""
    oids = [c.get("oid", "") for c in commits]
    if sha not in oids:
        return None
    return commits[oids.index(sha) + 1:]


def _hours(start: str, end: str) -> Optional[float]:
    if not start or not end:
        return None
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    try:
        return round((_dt.datetime.strptime(end, fmt) - _dt.datetime.strptime(start, fmt)).total_seconds() / 3600, 1)
    except ValueError:
        return None


def _clip(text: str, limit: int = QUOTE_LIMIT) -> str:
    text = re.sub(r"\s+", " ", _strip_comments(text)).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def issue_blocked(issue: Dict[str, Any]) -> bool:
    """issue 是否出現過停工提問：現在有 blocked label、有過加 label 的事件，或有「實作暫停」留言。"""
    if any(l.get("name") == BLOCKED_LABEL for l in issue.get("labels") or []):
        return True
    if any(e.get("event") == "labeled" and (e.get("label") or {}).get("name") == BLOCKED_LABEL
           for e in issue.get("events") or []):
        return True
    return any(STOP_MARK in (c.get("body") or "") for c in issue.get("comments") or [])


def pr_facts(repo_name: str, pr: Dict[str, Any], issue: Optional[Dict[str, Any]], issue_pr_count: int) -> Dict[str, Any]:
    """一個 PR 的原始資料 → 算指標要用的事實。"""
    recs = parse_records(pr.get("comments"))
    rounds = [r for r in recs if r.get("kind") == "round"]
    with_commit = [r for r in recs if r.get("to")]
    head = pr.get("headRefOid", "")
    commits = pr.get("commits") or []
    unrecorded: Optional[List[Dict[str, Any]]] = []
    if with_commit:
        unrecorded = commits_after(commits, with_commit[-1]["to"])
    threads = pr.get("reviewThreads") or []
    asks = []
    for t in threads:
        nodes = (t.get("comments") or {}).get("nodes") or []
        if nodes:
            asks.append({"path": t.get("path") or "", "body": _clip(nodes[0].get("body", "")),
                         "resolved": bool(t.get("isResolved")), "url": nodes[0].get("url", "")})
    reviews = [r for r in pr.get("reviews") or [] if (r.get("body") or "").strip() or r.get("state") == "CHANGES_REQUESTED"]
    human = [c for c in pr.get("comments") or [] if not is_agent_comment(c.get("body", ""))]
    sections: Dict[str, List[Dict[str, Any]]] = {}
    for r in recs:
        for k, v in record_sections(r.get("comment_body", "")).items():
            sections.setdefault(k, []).append({"kind": r.get("kind"), "n": r.get("n"), "text": _clip(v, 600)})
    adj = section(pr.get("body", ""), "📌 需求調整")
    title = pr.get("title", "")
    return {
        "ref": f"{repo_name}#{pr['number']}", "repo": repo_name, "number": pr["number"], "url": pr.get("url", ""),
        "title": title, "issue": issue_of_branch(pr.get("headRefName", "")),
        "state": pr.get("state", ""), "draft": bool(pr.get("isDraft")), "wip": "🚧" in title,
        "created": pr.get("createdAt", ""), "merged": pr.get("mergedAt") or "",
        "hours_to_merge": _hours(pr.get("createdAt", ""), pr.get("mergedAt") or ""),
        "has_records": bool(recs), "rounds": len(rounds), "fix_rounds": max(len(rounds) - 1, 0),
        "catchups": sum(1 for r in recs if r.get("kind") == "catchup"),
        "syncs": sum(1 for r in recs if r.get("kind") == "sync"),
        "notes": sum(1 for r in recs if r.get("kind") == "note"),
        "round_verified": [bool(r.get("verified")) for r in rounds],
        "head_verified": any(r.get("verified") and r.get("to") == head for r in recs),
        "unrecorded_at_end": None if unrecorded is None else [
            {"sha": c.get("oid", "")[:7], "subject": _clip(c.get("messageHeadline", ""), 120),
             "authors": [a.get("login") or a.get("name") or "" for a in c.get("authors") or []]} for c in unrecorded],
        "history_rewritten": with_commit != [] and unrecorded is None,
        "threads": len(asks), "thread_asks": asks,
        "reviews": [{"state": r.get("state", ""), "body": _clip(r.get("body", ""))} for r in reviews],
        "human_comments": [_clip(c.get("body", "")) for c in human],
        "feedback": parse_feedback(pr.get("body", "")),
        "adjustment": "" if adj is None or is_none(adj) else _clip(adj, 600),
        "record_sections": sections,
        "issue_pr_count": issue_pr_count,
        "issue_blocked": issue_blocked(issue) if issue else False,
        "size": {"files": pr.get("changedFiles"), "additions": pr.get("additions"), "deletions": pr.get("deletions")},
    }


# ---------------------------------------------------------------------------
# 指標
# ---------------------------------------------------------------------------

def _metric(label: str, refs: List[str], denom: List[str], note: str = "") -> Dict[str, Any]:
    return {"label": label, "count": len(refs), "of": len(denom),
            "rate": round(len(refs) / len(denom), 2) if denom else None, "refs": refs, "note": note}


def aggregate(facts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """每一個比例都附上分子是哪幾個 PR（refs），數字能追回原始資料。"""
    allp = [f["ref"] for f in facts]
    merged = [f for f in facts if f["state"] == "MERGED"]
    mrefs = [f["ref"] for f in merged]
    recorded = [f for f in facts if f["has_records"]]
    rrefs = [f["ref"] for f in recorded]
    loops = [f["feedback"].get("verify_loops") for f in facts if f["feedback"].get("verify_loops") is not None]
    hours = [f["hours_to_merge"] for f in merged if f["hours_to_merge"] is not None]
    issues: Dict[str, List[Dict[str, Any]]] = {}
    for f in facts:
        issues.setdefault(f"{f['repo']}#{f['issue']}", []).append(f)
    metrics = [
        _metric("merge 了", mrefs, allp),
        _metric("關掉沒 merge", [f["ref"] for f in facts if f["state"] == "CLOSED"], allp),
        _metric("還開著", [f["ref"] for f in facts if f["state"] == "OPEN"], allp),
        _metric("第一次發的 PR 就 merge（沒跑 bb-fix、沒人手動補 commit）",
                [f["ref"] for f in merged if f["fix_rounds"] == 0 and f["catchups"] == 0
                 and not f["unrecorded_at_end"] and not f["history_rewritten"]], mrefs,
                "分母是 merge 了的 PR"),
        _metric("跑過 bb-fix（第 2 輪以上）", [f["ref"] for f in facts if f["fix_rounds"] > 0], allp),
        _metric("🚧 draft（驗收 3 輪沒過）", [f["ref"] for f in facts if f["wip"] or f["draft"]], allp),
        _metric("同一個 issue 開過不只一個 PR（重做）",
                sorted({f["ref"] for fs in issues.values() if len(fs) > 1 for f in fs}), allp),
        _metric("有人繞過 bombolt 直接 push（catchup 紀錄，或 merge 時最後一則紀錄之後還有 commit）",
                [f["ref"] for f in recorded if f["catchups"] or f["unrecorded_at_end"] or f["history_rewritten"]],
                rrefs, "分母是有修改紀錄的 PR"),
        _metric("merge 的那個 commit 沒有驗收紀錄",
                [f["ref"] for f in merged if f["has_records"] and not f["head_verified"]],
                [f["ref"] for f in merged if f["has_records"]], "分母是有修改紀錄、merge 了的 PR"),
        _metric("issue 出現過停工提問", sorted({f["ref"] for f in facts if f["issue_blocked"]
                                            or f["feedback"].get("used_stop")}), allp),
        _metric("有 review thread（人在 code 上挑了問題）", [f["ref"] for f in facts if f["threads"]], allp),
        _metric("有需求調整（📌 不是「無」）", [f["ref"] for f in facts if f["adjustment"]], allp),
        _metric("驗收迴圈跑滿 3 輪", [f["ref"] for f in facts if (f["feedback"].get("verify_loops") or 0) >= 3],
                [f["ref"] for f in facts if f["feedback"].get("verify_loops") is not None]),
        _metric("沒有任何修改紀錄（舊版 bombolt 開的）", [f["ref"] for f in facts if not f["has_records"]], allp),
    ]
    numbers = {
        "merge 前平均 bb-fix 輪數": round(statistics.mean(f["fix_rounds"] for f in merged), 2) if merged else None,
        "驗收迴圈平均輪數（🤖 回饋）": round(statistics.mean(loops), 2) if loops else None,
        "每個 PR 平均 review thread 數": round(statistics.mean(f["threads"] for f in facts), 2) if facts else None,
        "開到 merge 的中位數（小時）": statistics.median(hours) if hours else None,
    }
    dates = sorted(f["created"][:10] for f in facts if f["created"])
    return {"prs": len(facts), "repos": sorted({f["repo"] for f in facts}), "issues": len(issues),
            "from": dates[0] if dates else "", "to": dates[-1] if dates else "",
            "metrics": metrics, "numbers": numbers}


# ---------------------------------------------------------------------------
# 報告
# ---------------------------------------------------------------------------

def _or_dash(v: Any) -> str:
    return "—" if v is None else str(v)


def _refs(refs: List[str], limit: int = 12) -> str:
    shown = "、".join(refs[:limit])
    return shown + (f"…（另 {len(refs) - limit} 個）" if len(refs) > limit else "")


def render(agg: Dict[str, Any], facts: List[Dict[str, Any]]) -> str:
    L = [f"# bombolt 實戰指標 · {_dt.date.today().isoformat()}", "",
         f"{len(agg['repos'])} 個 repo（{'、'.join(agg['repos'])}）· {agg['prs']} 個 PR · {agg['issues']} 個 issue · "
         f"PR 開在 {agg['from']} ～ {agg['to']}", "",
         "> 怎麼讀：上面的比例看「哪裡要人補」，每一列都附上是哪幾個 PR；下面的原文是 agent 自己回報（🤖 回饋、修改紀錄）"
         "和人留下的意見，用來找原因。樣本小的時候，原文比比例有用。", "",
         "## 數字", "", "| 指標 | 數量 | 比例 | 哪幾個 PR |", "|---|---|---|---|"]
    for m in agg["metrics"]:
        rate = "—" if m["rate"] is None else f"{m['rate']:.0%}"
        note = f"（{m['note']}）" if m["note"] else ""
        L.append(f"| {m['label']}{note} | {m['count']} / {m['of']} | {rate} | {_refs(m['refs'])} |")
    L += [""] + [f"- {k}：{_or_dash(v)}" for k, v in agg["numbers"].items()]

    L += ["", "## 每個 PR", "",
          "| PR | 狀態 | 輪數 | 驗收迴圈 | thread | catchup／沒紀錄的 commit | 停工 | 規模 | 標題 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for f in facts:
        st = f["state"].lower() + (" 🚧" if f["wip"] else "")
        unrec = len(f["unrecorded_at_end"] or []) if not f["history_rewritten"] else "改寫"
        sz = f["size"]
        L.append(f"| [{f['ref']}]({f['url']}) | {st} | {f['rounds']} | {_or_dash(f['feedback'].get('verify_loops'))} | "
                 f"{f['threads']} | {f['catchups']}／{unrec} | {'⛔' if f['issue_blocked'] or f['feedback'].get('used_stop') else ''} | "
                 f"{sz.get('files')} 檔 +{sz.get('additions')}/-{sz.get('deletions')} | {_clip(f['title'], 60)} |")

    def quotes(title: str, items: List[str]) -> None:
        L.extend(["", f"## {title}", ""] + (items or ["（沒有）"]))

    quotes("🤖 回饋：需要自己判斷、issue 沒寫清楚的地方",
           [f"- **{f['ref']}**：{_clip(f['feedback']['judgement'])}" for f in facts
            if not is_none(f["feedback"].get("judgement"))])
    quotes("🤖 回饋：卡住或浪費時間的地方",
           [f"- **{f['ref']}**：{_clip(f['feedback']['stuck'])}" for f in facts if not is_none(f["feedback"].get("stuck"))])
    for name in RECORD_SECTIONS:
        items = []
        for f in facts:
            for s in f["record_sections"].get(name, []):
                if name == "為什麼有這一輪" and s["kind"] == "round" and s["n"] == 1:
                    continue  # 第 1 輪是發 PR，不是人要求修改
                tag = f"第 {s['n']} 輪" if s["kind"] == "round" else s["kind"]
                items.append(f"- **{f['ref']}**（{tag}）：{_clip(s['text'])}")
        quotes(f"修改紀錄：{name}" + ("（bb-fix 的輪次，人要求改什麼）" if name == "為什麼有這一輪" else ""), items)
    quotes("review thread：人在 code 上挑的問題",
           [f"- **{f['ref']}** `{a['path']}`{' （已 resolve）' if a['resolved'] else ''}：{a['body']}"
            for f in facts for a in f["thread_asks"]])
    quotes("review 總評與人留的一般留言",
           [f"- **{f['ref']}**（review {r['state']}）：{r['body']}" for f in facts for r in f["reviews"] if r["body"]]
           + [f"- **{f['ref']}**（留言）：{c}" for f in facts for c in f["human_comments"]])
    quotes("merge 時最後一則紀錄之後的 commit（人手動補的）",
           [f"- **{f['ref']}** `{c['sha']}` {c['subject']}（{'、'.join(c['authors'])}）"
            for f in facts for c in f["unrecorded_at_end"] or []])
    quotes("📌 需求調整", [f"- **{f['ref']}**：{f['adjustment']}" for f in facts if f["adjustment"]])
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# 撈資料（只讀）
# ---------------------------------------------------------------------------

def discover() -> List[Path]:
    """~/.claude.json 記的 repo 裡（bb-sweep 的 known_repos），有 .claude/bombolt.md、不在暫存目錄（eval 的工作區）的；
    同一個 origin 只留一個。"""
    tmp = Path(tempfile.gettempdir()).resolve()
    seen, repos = set(), []
    for r in bb_sweep.known_repos():
        p = Path(r).resolve()
        if p == tmp or tmp in p.parents or not (p / bb_lib.CONFIG_REL).is_file():
            continue
        origin = bb_lib.git(["remote", "get-url", "origin"], p, check=False).strip()
        if origin and origin not in seen:
            seen.add(origin)
            repos.append(p)
    return repos


def _cache_path(name_with_owner: str) -> Path:
    return CACHE / (name_with_owner.replace("/", "__") + ".json")


def collect_repo(repo: Path, refresh: bool) -> Dict[str, Any]:
    info = bb_issues.gh_json(repo, ["repo", "view", "--json", "nameWithOwner"]) or {}
    nwo = info.get("nameWithOwner") or repo.name
    owner, name = nwo.split("/", 1) if "/" in nwo else ("", nwo)
    path = _cache_path(nwo)
    cache: Dict[str, Any] = {}
    if path.is_file() and not refresh:
        try:
            cache = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            cache = {}
    old_prs, old_issues = cache.get("prs") or {}, cache.get("issues") or {}
    listed = bb_issues.gh_json(repo, ["pr", "list", "--state", "all", "--limit", "500",
                                      "--json", "number,headRefName,state"]) or []
    bb = [p for p in listed if issue_of_branch(p.get("headRefName", "")) is not None]
    prs, issues, fetched = {}, {}, 0
    stale_issues = set()
    for p in bb:
        key = str(p["number"])
        old = old_prs.get(key)
        if old and old.get("state") in ("MERGED", "CLOSED") and old.get("state") == p.get("state"):
            prs[key] = old
            continue
        pr = bb_issues.gh_json(repo, ["pr", "view", key, "--json", PR_FIELDS]) or {}
        try:
            data = bb_issues.gh_json(repo, ["api", "graphql", "-f", f"query={THREADS_QUERY}",
                                            "-f", f"owner={owner}", "-f", f"name={name}", "-F", f"n={key}"]) or {}
            pr["reviewThreads"] = (((data.get("data") or {}).get("repository") or {}).get("pullRequest") or {}) \
                .get("reviewThreads", {}).get("nodes") or []
        except GhError as e:
            pr["reviewThreads"], pr["threads_error"] = [], str(e)
        prs[key] = pr
        fetched += 1
        stale_issues.add(str(issue_of_branch(p["headRefName"])))
    for n in sorted({str(issue_of_branch(p["headRefName"])) for p in bb}, key=int):
        if n in old_issues and n not in stale_issues:
            issues[n] = old_issues[n]
            continue
        try:
            issue = bb_issues.gh_json(repo, ["issue", "view", n, "--json", ISSUE_FIELDS]) or {}
            issue["events"] = bb_issues.gh_json(repo, ["api", f"repos/{nwo}/issues/{n}/events?per_page=100"]) or []
        except GhError as e:
            issue = {"error": str(e)}
        issues[n] = issue
    data = {"repo": nwo, "name": name, "fetched_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "prs": prs, "issues": issues}
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"repo": nwo, "prs": len(prs), "fetched": fetched, "cache": str(path)}


def load_facts(repos: List[str], since: str) -> List[Dict[str, Any]]:
    facts = []
    for path in sorted(CACHE.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        name = data.get("name") or path.stem
        if repos and name not in repos and data.get("repo") not in repos:
            continue
        prs = data.get("prs") or {}
        per_issue: Dict[Optional[int], int] = {}
        for pr in prs.values():
            n = issue_of_branch(pr.get("headRefName", ""))
            per_issue[n] = per_issue.get(n, 0) + 1
        for key in sorted(prs, key=int):
            pr = prs[key]
            if since and (pr.get("createdAt") or "")[:10] < since:
                continue
            n = issue_of_branch(pr.get("headRefName", ""))
            issue = (data.get("issues") or {}).get(str(n))
            facts.append(pr_facts(name, pr, issue, per_issue.get(n, 1)))
    return facts


# ---------------------------------------------------------------------------
# 指令
# ---------------------------------------------------------------------------

def cmd_collect(args: argparse.Namespace) -> None:
    repos = [Path(r).resolve() for r in args.repos] if args.repos else discover()
    if not repos:
        sys.exit("找不到用 bombolt 的 repo（~/.claude.json 記的專案裡沒有 .claude/bombolt.md）：直接給路徑。")
    for repo in repos:
        try:
            r = collect_repo(repo, args.refresh)
            print(f"✅ {r['repo']}：{r['prs']} 個 bombolt PR（這次撈了 {r['fetched']} 個，其餘用快取）")
        except (GhError, bb_lib.GhAccountError, subprocess.SubprocessError) as e:
            print(f"❌ {repo.name}：{e}")
    print(f"快取在 {CACHE}。接著跑：python3 evals/field.py report")


def cmd_report(args: argparse.Namespace) -> None:
    if not CACHE.is_dir():
        sys.exit("還沒有資料：先跑 python3 evals/field.py collect")
    facts = load_facts(args.repo or [], args.since or "")
    if not facts:
        sys.exit("快取裡沒有符合條件的 PR。")
    agg = aggregate(facts)
    md = render(agg, facts)
    out = RESULTS / f"field-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(md, encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps({"aggregate": agg, "prs": facts}, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    print(md)
    print(f"寫在 {out}/report.md（每個 PR 的完整事實在 metrics.json）")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="撈資料（只讀 GitHub），快取在 evals/results/field-cache/")
    c.add_argument("repos", nargs="*", help="repo 路徑；不給就自動找")
    c.add_argument("--refresh", action="store_true", help="不用快取，全部重撈")
    c.set_defaults(func=cmd_collect)
    r = sub.add_parser("report", help="讀快取、算指標、寫報告")
    r.add_argument("--since", help="只看這天（YYYY-MM-DD）之後開的 PR")
    r.add_argument("--repo", action="append", help="只看這個 repo（名字或 owner/name），可以給多次")
    r.set_defaults(func=cmd_report)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
