"""evals/run.py 的測試：transcript 指標、打分、跟 baseline 比、報告，以及每一題的 selftest（不呼叫 claude）。

執行：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
import run as ev  # noqa: E402


def ev_assistant(tool, inp, parent=None, tid="t1"):
    return {"type": "assistant", "parent_tool_use_id": parent,
            "message": {"content": [{"type": "tool_use", "id": tid, "name": tool, "input": inp}]}}


def ev_result(text, tid="t1", is_error=False, parent=None):
    return {"type": "user", "parent_tool_use_id": parent,
            "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": is_error}]}}


TRANSCRIPT = [
    {"type": "system", "subtype": "init", "model": "claude-opus-5-5"},
    ev_assistant("Skill", {"skill": "bombolt:bb-work"}, tid="s"),
    ev_assistant("Bash", {"command": "git push origin HEAD:main"}, tid="b1"),
    ev_result("bombolt 安全守門：不可以 push 到 main", tid="b1", is_error=True),
    ev_assistant("Agent", {"subagent_type": "bombolt:bb-verifier", "description": "驗收"}, tid="a1"),
    ev_assistant("Bash", {"command": "curl localhost:20101"}, parent="a1", tid="b2"),
    ev_result("ok", tid="b2", parent="a1"),
    ev_result([{"type": "text", "text": "全部通過"}], tid="a1"),
    ev_assistant("Agent", {"subagent_type": "bombolt:bb-reviewer", "description": "review"}, tid="a2"),
    ev_assistant("AskUserQuestion", {"questions": []}, tid="q"),
    {"type": "system", "subtype": "task_started", "task_id": "bg1", "subagent_type": "bombolt:bb-reviewer"},
    {"type": "system", "subtype": "task_progress", "task_id": "bg1", "subagent_type": "bombolt:bb-reviewer",
     "description": "Read diff"},
    {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 4.2, "num_turns": 50,
     "duration_ms": 600000, "result": "等 reviewer", "permission_denials": [{}]},
    {"type": "system", "subtype": "task_notification", "task_id": "bg1", "status": "completed",
     "output_file": "/nowhere/bg1.output", "usage": {"tool_uses": 5, "duration_ms": 30000},
     "handback_report": {"text": "## 結論\n沒有 blocking 問題"}},
    {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 5.0, "num_turns": 7,
     "duration_ms": 60000, "result": "PR 已發", "permission_denials": [{}],
     "subagent_stats": {"by_type": {"bombolt:bb-verifier": 1, "bombolt:bb-reviewer": 2}},
     "modelUsage": {"m": {"inputTokens": 10, "outputTokens": 20, "cacheReadInputTokens": 30, "cacheCreationInputTokens": 5}}},
]


class TestTranscript(unittest.TestCase):
    def test_metrics(self):
        m = ev.transcript_metrics(TRANSCRIPT)
        self.assertEqual(m["model"], "claude-opus-5-5")
        self.assertEqual(m["subagents"], {"bombolt:bb-verifier": 1, "bombolt:bb-reviewer": 2})  # 以 subagent_stats 為準
        self.assertEqual(m["guard_blocks"], 1)
        self.assertEqual(m["isolation_blocks"], 0)
        self.assertEqual(m["tool_errors"], 1)
        self.assertEqual(m["ask_user"], 1)
        self.assertEqual(m["tool_calls"], 6)
        self.assertEqual(m["tool_calls_main"], 5)  # subagent 裡的 curl 不算主 session
        r = m["result"]
        self.assertEqual(r["cost_usd"], 5.0)  # 累計值取最大
        self.assertEqual(r["num_turns"], 57)  # 每一段加總
        self.assertEqual(r["active_s"], 660)
        self.assertEqual(r["final_text"], "PR 已發")
        self.assertEqual(r["permission_denials"], 1)
        self.assertEqual((r["output_tokens"], r["cache_read_tokens"], r["cache_write_tokens"]), (20, 30, 5))
        runs = m["subagent_runs"]
        self.assertEqual([(x["type"], x["tool_uses"], x["duration_s"]) for x in runs], [("bombolt:bb-reviewer", 5, 30)])
        self.assertIn("沒有 blocking", runs[0]["report"])

    def test_collect_subagents_merges_guard_blocks(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(d))
        sub = d / "agent-bg1.jsonl"
        sub.write_text("\n".join(json.dumps(e) for e in [
            ev_assistant("Bash", {"command": "ngrok http 3000"}, tid="x"),
            ev_result("bombolt 安全守門：不可以直接開 tunnel", tid="x", is_error=True),
            ev_assistant("Bash", {"command": "kill $(cat /tmp/x.pid)"}, tid="y"),
            ev_result("This session is isolated in the worktree /w but this command is too complex to verify", tid="y",
                      is_error=True),
            # 主 stream 也帶著的同一個呼叫：不要算兩次
            ev_assistant("Bash", {"command": "git push origin HEAD:main"}, tid="b1"),
            ev_result("bombolt 安全守門：不可以 push 到 main", tid="b1", is_error=True)]))
        link = d / "bg1.output"
        link.symlink_to(sub)
        m = ev.transcript_metrics(TRANSCRIPT)
        m["subagent_runs"][0]["output_file"] = str(link)
        out = d / "out"
        out.mkdir()
        (out / "transcript.jsonl").write_text("\n".join(json.dumps(e) for e in TRANSCRIPT))
        ev.collect_subagents(m, out)
        self.assertEqual(m["guard_blocks"], 2)
        self.assertEqual(m["isolation_blocks"], 1)  # Claude Code 的隔離，跟 bombolt 守門分開算
        self.assertEqual(m["tool_errors"], 3)
        self.assertEqual(m["tool_calls"], 8)
        self.assertEqual(m["subagent_runs"][0]["guard_blocks"], 2)  # 這個 subagent 自己的數字照實記
        self.assertTrue((out / "subagents" / "bombolt_bb-reviewer-bg1.jsonl").is_file())

    def test_cleanup_only_touches_this_workspace(self):
        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(home))
        projects = home / ".claude" / "projects"
        mine = projects / "-private-var-T-bb-eval-work-a-1-ab-c123-repo"
        other = projects / "-Users-me-myrepo"
        for p in (mine, other):
            p.mkdir(parents=True)
        ws = ev.Workspace(Path("/private/var/T/bb-eval-work-a-1-ab_c123"))  # 底線在資料夾名裡會變成 -
        old = ev.Path.home
        ev.Path.home = staticmethod(lambda: home)
        try:
            ev.cleanup_session_files(ws, {"subagent_runs": []})
            ev.cleanup_session_files(ev.Workspace(Path("/tmp/not-eval")), {"subagent_runs": []})
        finally:
            ev.Path.home = old
        self.assertFalse(mine.exists())
        self.assertTrue(other.exists())

    def test_read_transcript_skips_garbage(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(d))
        p = d / "t.jsonl"
        p.write_text("not json\n" + json.dumps(TRANSCRIPT[0]) + "\n{broken\n")
        self.assertEqual(len(ev.read_transcript(p)), 1)
        self.assertEqual(ev.read_transcript(d / "missing.jsonl"), [])

    def test_timeline(self):
        text = ev.timeline(TRANSCRIPT)
        self.assertIn("$ git push origin HEAD:main", text)
        self.assertIn("⛔ bombolt 安全守門", text)
        self.assertIn("🤖 bombolt:bb-verifier：驗收", text)
        self.assertIn("    $ curl localhost:20101", text)  # subagent 裡的動作縮排
        self.assertIn("↳ 全部通過", text)
        self.assertIn("    · bombolt:bb-reviewer：Read diff", text)
        self.assertIn("📨 bg1 completed：## 結論 沒有 blocking 問題", text)
        self.assertIn("🏁 success · 這一段 7 turns · 累計 $5.00", text)


class TestScoring(unittest.TestCase):
    def test_weighted_score(self):
        g = ev.Grades()
        g.add("a", True, weight=3)
        g.add("b", False)
        self.assertEqual(g.score(), 0.75)
        self.assertEqual(ev.Grades().score(), 0.0)

    def test_parse_feedback(self):
        body = ("## 一句話\n驗收迴圈跑了幾輪：不該被讀到\n<summary>🤖 bombolt 回饋（給 pipeline 迭代用）</summary>\n\n"
                "- 驗收迴圈跑了幾輪：2\n- 有沒有用到「停工提問」：沒有\n"
                "- 實作時需要自己判斷、issue 沒寫清楚的地方：400 的錯誤訊息文字\n- 卡住或浪費時間的地方：無\n")
        self.assertEqual(ev.parse_feedback(body),
                         {"rounds": "2", "asked": "沒有", "guessed": "400 的錯誤訊息文字", "stuck": "無"})
        self.assertEqual(ev.parse_feedback("沒有回饋"), {})

    def run_rec(self, case, n, score, passes, cost):
        return {"case": case, "run": n, "score": score,
                "grades": [{"name": k, "pass": v, "weight": 1, "detail": ""} for k, v in passes.items()],
                "result": {"cost_usd": cost, "duration_s": 600, "num_turns": 40},
                "metrics": {"subagents": {"bombolt:bb-verifier": 2, "bombolt:bb-reviewer": 1}, "guard_blocks": 0,
                            "isolation_blocks": 3, "tool_errors": 1}}

    def test_summarize_and_compare(self):
        runs = [self.run_rec("a", 1, 1.0, {"x": True, "y": True}, 4),
                self.run_rec("a", 2, 0.5, {"x": True, "y": False}, 6),
                self.run_rec("b", 1, 1.0, {"x": True}, 2)]
        s = ev.summarize(runs, {"model": "m1"})
        self.assertEqual(s["cases"]["a"]["score"], 0.75)
        self.assertEqual(s["cases"]["a"]["graders"], {"x": 1.0, "y": 0.5})
        self.assertEqual(s["cases"]["a"]["metrics"]["cost_usd"], 5.0)
        self.assertEqual(s["overall"], 0.875)
        self.assertEqual(s["total_cost_usd"], 12)

        base = {"model": "m1", "cases": {"a": {"score": 1.0, "graders": {"x": 1.0, "y": 1.0}, "metrics": {"cost_usd": 3}},
                                         "b": {"score": 0.8, "graders": {"x": 1.0}, "metrics": {"cost_usd": 2}}}}
        changes = ev.compare(s, base)
        self.assertTrue(any("a 分數 1.00 → 0.75" in c for c in changes))
        self.assertTrue(any("a 的 y 通過率 100% → 50%" in c for c in changes))
        self.assertTrue(any("a 平均成本 $3.00 → $5.00" in c for c in changes))
        self.assertTrue(any(c.startswith("✅ b 分數") for c in changes))
        self.assertTrue(changes[0].startswith("⚠️"), "退步要排在前面")
        self.assertEqual(ev.compare(s, None), [])
        self.assertIn("模型不同", ev.compare(s, {**base, "model": "m2"})[0])

    def test_report_without_baseline(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(d))
        runs = [self.run_rec("a", 1, 0.5, {"x": True, "hidden_tests": False}, 4)]
        runs[0]["out_dir"], runs[0]["workspace"] = str(d / "runs" / "a-1"), "/tmp/ws"
        runs[0]["grades"][1]["detail"] = "FAIL: test_filter"
        (d / "runs" / "a-1").mkdir(parents=True)
        (d / "runs" / "a-1" / "grades.json").write_text(json.dumps(runs[0]))
        (d / "summary.json").write_text(json.dumps(ev.summarize(runs, {"started": "t", "model": "m", "runs_per_case": 1,
                                                                       "bombolt": {"sha": "abc", "dirty": True}})))
        old = ev.BASELINE
        ev.BASELINE = d / "no-baseline.json"
        try:
            text = ev.report(d)
        finally:
            ev.BASELINE = old
        self.assertIn("還沒有 baseline", text)
        self.assertIn("有未 commit 的改動", text)
        self.assertIn("✗ `hidden_tests`：FAIL: test_filter", text)
        self.assertIn("| 守門擋下 | 隔離擋下 |", text)
        self.assertIn("| 0.0 | 3.0 |", text)


class TestCases(unittest.TestCase):
    """每一題：參考解要過、沒改的版本要被隱藏測試抓到（不呼叫 claude，只用 git 與假 gh）。"""

    def test_every_case_selftest(self):
        names = ev.case_names()
        self.assertGreaterEqual(len(names), 1)
        for name in names:
            with self.subTest(case=name):
                r = ev.selftest_case(name)
                self.assertTrue(r["ok"], "\n".join(r["problems"]))

    def test_case_files_complete(self):
        for name in ev.case_names():
            with self.subTest(case=name):
                c = ev.load_case(name)
                for key in ("kind", "title", "slug", "allowed_paths", "description"):
                    self.assertIn(key, c)
                self.assertIn((c["kind"], c.get("expect", "done")), {("work", "done"), ("fix", "done"), ("work", "stop")})
                self.assertIn("<!-- bombolt:meta", (c["dir"] / "issue.md").read_text(encoding="utf-8"))
                if c.get("expect") == "stop":  # 反向題：沒有參考解，正確的做法是停下來問
                    for key in ("stop_regex", "stop_example"):
                        self.assertIn(key, c)
                    self.assertRegex(c["stop_example"], c["stop_regex"])
                    continue
                need = ["issue.md", "solution.diff"] + (["pr.diff", "pr_body.md", "review.json"] if c["kind"] == "fix" else [])
                for f in need:
                    self.assertTrue((c["dir"] / f).is_file(), f)
                self.assertTrue(list((c["dir"] / "hidden_tests").glob("test_*.py")))

    def test_fix_case_workspace_matches_bombolt_scripts(self):
        """fix 題的 PR 是用 bombolt 自己的腳本建的：issue 是審查中、PR 有第 1 輪紀錄、review 有種進去。"""
        fixes = [n for n in ev.case_names() if ev.load_case(n)["kind"] == "fix"]
        if not fixes:
            self.skipTest("沒有 fix 題")
        case = ev.load_case(fixes[0])
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(tmp))
        ctx = ev.prepare_workspace(case, tmp / "ws", 131)
        ws = ev.Workspace(tmp / "ws")
        state = ws.state()
        pr = state["prs"][str(ctx["pr"])]
        self.assertEqual(pr["headRefName"], ctx["branch"])
        self.assertIn(f"(#{ctx['issue']})", pr["title"])
        self.assertNotIn("{{", pr["title"] + state["issues"][str(ctx["issue"])]["title"])
        self.assertTrue(any(ev.RECORD_RE.search(c["body"]) for c in pr["comments"]))
        self.assertIn(ev.HANDOFF_START, pr["body"])
        self.assertTrue(pr["threads"])
        status, _ = ev.find_status(state["issues"][str(ctx["issue"])]["comments"])
        self.assertEqual(status["state"], "in_review")
        self.assertFalse((tmp / "ws" / "_other-machine").exists())
        self.assertFalse((ws.repo / ".claude" / "worktrees").exists(), "這台電腦還沒有 worktree（要測接手）")

    def test_work_case_history(self):
        """work 題的 base.diff 在規劃之前（base_sha 包含它），main_advance.diff 在規劃之後（base_sha 不包含）。"""
        for name, want_advance in (("work-hidden-invariant", False), ("work-premise-broken", True)):
            with self.subTest(case=name):
                tmp = Path(tempfile.mkdtemp())
                self.addCleanup(shutil.rmtree, str(tmp))
                case = ev.load_case(name)
                ctx = ev.prepare_workspace(case, tmp / "ws", 101)
                ws = ev.Workspace(tmp / "ws")
                log = ev.git(["--git-dir", str(ws.origin), "log", "--format=%an|%s", "main"], ws.path, ws.env).splitlines()
                self.assertEqual(ctx["main_sha"] != ctx["base_sha"], want_advance)
                if want_advance:
                    self.assertEqual(log[0], f"Amy Lin|{case['main_advance_message']}")
                    local = ev.git(["rev-parse", "HEAD"], ws.repo, ws.env)
                    self.assertEqual(local, ctx["base_sha"], "主 checkout 停在規劃時的 main（跟真實情況一樣）")
                else:
                    self.assertEqual(log[0], f"Amy Lin|{case['base_message']}")
                    self.assertTrue((ws.repo / "todo" / "CLAUDE.md").is_file())


class TestExtraGraders(unittest.TestCase):
    BODY = "## 一句話\n改了\n\n## 📌 需求調整（相對於 #101）\n\n{adj}\n\n## ✅ 驗收結果\n驗的是 `abc1234`\n"

    def test_adjustment_recorded(self):
        ok, _ = ev.adjustment_recorded(self.BODY.format(adj="- PATCH 也能改 `done`（amy-pm 在 review 決定，2026-10-14）"), "done")
        self.assertTrue(ok)
        for adj, why in (("無", "是「無」"), ("<!-- 沒有 -->\n無", "是「無」"), ("- 標題上限 50 字", "沒提到")):
            ok, detail = ev.adjustment_recorded(self.BODY.format(adj=adj), "done")
            self.assertFalse(ok, adj)
            self.assertIn(why, detail)
        self.assertFalse(ev.adjustment_recorded("## 一句話\n沒有這一節", "done")[0])

    def test_declined_in_thread(self):
        exp = {"path": "todo/server.py", "line": 182, "body": "改成扁平的 {message} 吧"}

        def thread(*replies):
            cs = [{"author": "bob-dev", "body": exp["body"]}] + [{"author": a, "body": b} for a, b in replies]
            return [{"path": "todo/server.py", "comments": cs}, {"path": "todo/store.py", "comments": [{"body": "x"}]}]
        self.assertTrue(ev.declined_in_thread(thread((ev.LOGIN, "未修改：CLAUDE.md 規定錯誤格式固定")), exp)[0])
        self.assertFalse(ev.declined_in_thread(thread((ev.LOGIN, "已修正於 `abc1234`：改成扁平的")), exp)[0])
        self.assertFalse(ev.declined_in_thread(thread(("bob-dev", "不改也可以")), exp)[0], "要是 agent 自己回的")
        self.assertFalse(ev.declined_in_thread(thread(), exp)[0])
        self.assertFalse(ev.declined_in_thread([], exp)[0])

    def test_fix_extra_graders_both_ways(self):
        """fix-review-pushback：沒做事時三個額外 grader 的判斷，與照正確做法之後的判斷。"""
        case = ev.load_case("fix-review-pushback")
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(tmp))
        ctx = ev.prepare_workspace(case, tmp / "ws", 101)
        ws = ev.Workspace(tmp / "ws")
        self.assertEqual(ws.state()["issues"]["101"]["author"], "amy-pm")
        done = {"result": {"subtype": "success", "is_error": False}}

        def grades():
            g, _ = ev.grade_run(case, ws, ctx, done, {})
            return {i["name"]: i["pass"] for i in g.items}
        g = grades()
        self.assertEqual((g["requirement_adjustment_recorded"], g["declined_bad_suggestion"], g["issue_unchanged"]),
                         (False, False, True))

        state = ws.state()
        pr = state["prs"][str(ctx["pr"])]
        pr["body"] = pr["body"].replace("（相對於 #101）\n\n無", "（相對於 #101）\n\n- PATCH 也能改 `done`（amy-pm 在 review 決定）")
        bad = next(t for t in pr["threads"] if t["path"] == "todo/server.py" and t["line"] == 182)
        bad["comments"].append({"author": ev.LOGIN, "body": "未修改：CLAUDE.md 規定錯誤回應格式固定"})
        state["issues"]["101"]["body"] += "\n（agent 改了 issue）"
        ev.fake_gh.save_state(str(ws.state_path), state)
        g = grades()
        self.assertEqual((g["requirement_adjustment_recorded"], g["declined_bad_suggestion"], g["issue_unchanged"]),
                         (True, True, False))

    def test_stop_grader_needs_the_real_question(self):
        """「實作暫停」的留言要提到前提為什麼不成立；只說暫停、問別的不算。"""
        case = ev.load_case("work-premise-broken")
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(tmp))
        ctx = ev.prepare_workspace(case, tmp / "ws", 101)
        ws = ev.Workspace(tmp / "ws")
        state = ws.state()
        ev.fake_gh.add_comment(state, 101, f"🤖 bombolt {ev.PAUSE_MARK}：\n\n1. 回傳要用 200 還是 204？")
        ev.fake_gh.save_state(str(ws.state_path), state)
        g = ev.Grades()
        ev.grade_stop(case, ws, ctx, g)
        item = next(i for i in g.items if i["name"] == "stopped_for_human")
        self.assertFalse(item["pass"])
        self.assertIn("沒提到", item["detail"])


if __name__ == "__main__":
    unittest.main()
