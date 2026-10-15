"""evals/field.py 的測試：用合成的 PR／issue 資料（不連網）測解析、指標、快取。

執行：python3 -m unittest tests.test_field -v
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "scripts"))
import bb_pr  # noqa: E402
import field  # noqa: E402

OWNER = {"name": "小明", "github": "ming", "machine": "MBP"}


def record(kind: str, n, to: str, verified: bool, text: str, frm: str = "") -> dict:
    """用 bb_pr 真正的格式產生一則修改紀錄留言（格式改了，這裡會跟著變）。"""
    data = {"kind": kind, "n": n, "summary": "s", "date": "2026-10-01", "from": frm, "to": to,
            "verified": verified, "owner": OWNER, "session": "sess"}
    return {"body": bb_pr.render_record(data, text, []), "author": {"login": "ming"},
            "url": f"https://github.com/o/r/pull/5#issuecomment-{abs(hash(to)) % 10000}"}


PR_BODY = """## 一句話

改東西

## 📌 需求調整（相對於 #4）

<!-- 沒有就寫無 -->
無

## ✅ 驗收結果

<details>
<summary>🤖 bombolt 回饋（給 pipeline 迭代用）</summary>

- 驗收迴圈跑了幾輪：3（第 1 輪 reviewer 抓到 blocking）
- 有沒有用到「停工提問」：沒有
- 實作時需要自己判斷、issue 沒寫清楚的地方：DoD 3 的 grep 會比對到註解
- 卡住或浪費時間的地方：無
- 改動規模：2 個檔、+10 / -1 行

</details>

Closes #4
"""


def make_pr(number=5, state="MERGED", body=PR_BODY, comments=None, commits=None, head="c3", **kw) -> dict:
    pr = {"number": number, "title": f"feat: x (#{number - 1})", "url": f"https://github.com/o/r/pull/{number}",
          "state": state, "isDraft": False, "createdAt": "2026-10-01T00:00:00Z",
          "mergedAt": "2026-10-01T06:30:00Z" if state == "MERGED" else None,
          "headRefName": f"bb-{number - 1}-thing", "headRefOid": head, "body": body,
          "comments": comments if comments is not None else [], "reviews": [],
          "commits": commits if commits is not None else [{"oid": "c1"}, {"oid": "c2"}, {"oid": "c3"}],
          "reviewThreads": [], "additions": 10, "deletions": 1, "changedFiles": 2}
    pr.update(kw)
    return pr


class ParseTest(unittest.TestCase):
    def test_issue_of_branch(self):
        self.assertEqual(field.issue_of_branch("bb-12-add-filter"), 12)
        self.assertIsNone(field.issue_of_branch("feature/x"))
        self.assertIsNone(field.issue_of_branch(""))

    def test_section_and_none(self):
        self.assertEqual(field.section(PR_BODY, "📌 需求調整"), "無")
        self.assertTrue(field.is_none(field.section(PR_BODY, "📌 需求調整")))
        self.assertIsNone(field.section(PR_BODY, "不存在的標題"))
        body = "## 📌 需求調整\n\n| # | 調整 |\n|---|---|\n| 1 | 改成 MIT |\n\n## ✅ 驗收結果\n"
        self.assertIn("改成 MIT", field.section(body, "📌 需求調整"))
        self.assertFalse(field.is_none(field.section(body, "📌 需求調整")))

    def test_feedback(self):
        fb = field.parse_feedback(PR_BODY)
        self.assertEqual(fb["verify_loops"], 3)
        self.assertFalse(fb["used_stop"])
        self.assertEqual(fb["judgement"], "DoD 3 的 grep 會比對到註解")
        self.assertTrue(field.is_none(fb["stuck"]))
        self.assertEqual(field.parse_feedback("沒有回饋的 PR"), {})
        used = PR_BODY.replace("停工提問」：沒有", "停工提問」：有（問了 MUI 的接法）")
        self.assertTrue(field.parse_feedback(used)["used_stop"])

    def test_record_sections(self):
        text = "**為什麼有這一輪**\n- 使用者說：手機要全螢幕\n\n**改了什麼**\n- 改 css\n\n**試過但不行**\n- 無\n\n**環境的坑**\n- port 要寫死"
        secs = field.record_sections(record("round", 2, "c3", True, text)["body"])
        self.assertEqual(secs["為什麼有這一輪"], "- 使用者說：手機要全螢幕")
        self.assertEqual(secs["環境的坑"], "- port 要寫死")
        self.assertNotIn("改了什麼", secs)  # diff 看得到，不收
        self.assertNotIn("試過但不行", secs)  # 內容是「無」
        self.assertNotIn("展開紀錄", json.dumps(secs, ensure_ascii=False))

    def test_agent_comment(self):
        self.assertTrue(field.is_agent_comment(record("round", 1, "c1", True, "x")["body"]))
        self.assertTrue(field.is_agent_comment("🧪 **整合進 `stage` 時解了衝突**"))
        self.assertTrue(field.is_agent_comment("🔀 已合入最新 main"))
        self.assertTrue(field.is_agent_comment("已修正於 `abc1234`：改好了"))
        self.assertFalse(field.is_agent_comment("這裡的文案再想一下"))

    def test_commits_after(self):
        cs = [{"oid": "a"}, {"oid": "b"}, {"oid": "c"}]
        self.assertEqual(field.commits_after(cs, "b"), [{"oid": "c"}])
        self.assertEqual(field.commits_after(cs, "c"), [])
        self.assertIsNone(field.commits_after(cs, "zzz"))

    def test_issue_blocked(self):
        self.assertFalse(field.issue_blocked({"labels": [], "comments": [], "events": []}))
        self.assertTrue(field.issue_blocked({"labels": [{"name": "bombolt:blocked"}]}))
        self.assertTrue(field.issue_blocked({"events": [{"event": "labeled", "label": {"name": "bombolt:blocked"}}]}))
        self.assertTrue(field.issue_blocked({"comments": [{"body": "🤖 bombolt 實作暫停：需要決定以下問題"}]}))
        self.assertFalse(field.issue_blocked({"events": [{"event": "unlabeled", "label": {"name": "bombolt:blocked"}}]}))


class FactsTest(unittest.TestCase):
    def test_first_pass_pr(self):
        f = field.pr_facts("r", make_pr(comments=[record("round", 1, "c3", True, "**決定**\n- 不改 X")]), None, 1)
        self.assertEqual((f["ref"], f["issue"], f["rounds"], f["fix_rounds"]), ("r#5", 4, 1, 0))
        self.assertTrue(f["head_verified"])
        self.assertEqual(f["unrecorded_at_end"], [])
        self.assertFalse(f["history_rewritten"])
        self.assertEqual(f["hours_to_merge"], 6.5)
        self.assertEqual(f["adjustment"], "")
        self.assertEqual(f["record_sections"]["決定"][0]["text"], "- 不改 X")

    def test_fix_rounds_and_bypass(self):
        comments = [record("round", 1, "c1", True, "x"), {"body": "文案再想一下", "author": {"login": "ming"}},
                    record("round", 2, "c2", True, "**為什麼有這一輪**\n- review：改成 MIT")]
        commits = [{"oid": "c1"}, {"oid": "c2"}, {"oid": "c3", "messageHeadline": "hotfix by hand",
                                                  "authors": [{"login": "ming"}]}]
        threads = [{"isResolved": False, "path": "a.py", "comments": {"nodes": [{"body": "這裡錯了", "url": "u"}]}}]
        f = field.pr_facts("r", make_pr(comments=comments, commits=commits, reviewThreads=threads), {"labels": []}, 2)
        self.assertEqual((f["rounds"], f["fix_rounds"]), (2, 1))
        self.assertFalse(f["head_verified"])  # merge 的 c3 沒有驗收紀錄
        self.assertEqual([c["sha"] for c in f["unrecorded_at_end"]], ["c3"])
        self.assertEqual(f["unrecorded_at_end"][0]["authors"], ["ming"])
        self.assertEqual(f["threads"], 1)
        self.assertEqual(f["human_comments"], ["文案再想一下"])
        self.assertEqual(f["issue_pr_count"], 2)

    def test_rewritten_and_no_records(self):
        f = field.pr_facts("r", make_pr(comments=[record("round", 1, "gone", True, "x")]), None, 1)
        self.assertTrue(f["history_rewritten"])
        self.assertIsNone(f["unrecorded_at_end"])
        g = field.pr_facts("r", make_pr(), None, 1)
        self.assertFalse(g["has_records"])
        self.assertFalse(g["history_rewritten"])


class AggregateTest(unittest.TestCase):
    def facts(self):
        ok = field.pr_facts("r", make_pr(5, comments=[record("round", 1, "c3", True, "x")]), None, 1)
        fixed = field.pr_facts("r", make_pr(7, comments=[record("round", 1, "c2", True, "x"),
                                                         record("round", 2, "c3", True, "x")]), None, 1)
        bypass = field.pr_facts("r", make_pr(9, comments=[record("round", 1, "c2", True, "x")]), None, 1)
        closed = field.pr_facts("r", make_pr(11, state="CLOSED", title="🚧 feat: y (#10)"),
                                {"labels": [{"name": "bombolt:blocked"}]}, 1)
        return [ok, fixed, bypass, closed]

    def test_metrics_with_refs(self):
        agg = field.aggregate(self.facts())
        m = {x["label"]: x for x in agg["metrics"]}
        first = next(v for k, v in m.items() if k.startswith("第一次發的 PR 就 merge"))
        self.assertEqual((first["refs"], first["of"]), (["r#5"], 3))
        bypass = next(v for k, v in m.items() if k.startswith("有人繞過 bombolt"))
        self.assertEqual(bypass["refs"], ["r#9"])
        self.assertEqual(m["關掉沒 merge"]["refs"], ["r#11"])
        self.assertEqual(next(v for k, v in m.items() if k.startswith("🚧"))["refs"], ["r#11"])
        self.assertEqual(m["issue 出現過停工提問"]["refs"], ["r#11"])
        self.assertEqual(next(v for k, v in m.items() if k.startswith("跑過 bb-fix"))["refs"], ["r#7"])
        self.assertEqual(agg["numbers"]["merge 前平均 bb-fix 輪數"], 0.33)
        self.assertEqual(agg["numbers"]["驗收迴圈平均輪數（🤖 回饋）"], 3)

    def test_render(self):
        facts = self.facts()
        md = field.render(field.aggregate(facts), facts)
        self.assertIn("| 第一次發的 PR 就 merge", md)
        self.assertIn("r#9", md)
        self.assertIn("DoD 3 的 grep 會比對到註解", md)  # 質性原文
        self.assertIn("## merge 時最後一則紀錄之後的 commit", md)
        self.assertNotIn("None", md)


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        patcher = mock.patch.object(field, "CACHE", self.tmp / "cache")
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_gh(self, prs: dict, calls: list):
        def gh_json(repo, args):
            calls.append(args[:2])
            if args[:2] == ["repo", "view"]:
                return {"nameWithOwner": "o/r"}
            if args[:2] == ["pr", "list"]:
                return [{"number": n, "headRefName": p["headRefName"], "state": p["state"]} for n, p in prs.items()]
            if args[:2] == ["pr", "view"]:
                return dict(prs[int(args[2])])
            if args[:2] == ["api", "graphql"]:
                return {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}}
            if args[:2] == ["issue", "view"]:
                return {"number": int(args[2]), "labels": [], "comments": []}
            if args[0] == "api":
                return []
            raise AssertionError(args)
        return gh_json

    def test_collect_reuses_finished_prs(self):
        prs = {5: make_pr(5), 7: make_pr(7, state="OPEN"), 8: dict(make_pr(8), headRefName="not-bombolt")}
        calls: list = []
        with mock.patch.object(field.bb_issues, "gh_json", self.fake_gh(prs, calls)):
            r = field.collect_repo(self.tmp, refresh=False)
            self.assertEqual((r["prs"], r["fetched"]), (2, 2))  # 不是 bombolt branch 的 #8 不收
            calls.clear()
            r = field.collect_repo(self.tmp, refresh=False)
            self.assertEqual(r["fetched"], 1)  # merge 了的 #5 用快取，開著的 #7 重撈
            self.assertEqual([c for c in calls if c == ["pr", "view"]], [["pr", "view"]])
            r = field.collect_repo(self.tmp, refresh=True)
            self.assertEqual(r["fetched"], 2)
        facts = field.load_facts([], "")
        self.assertEqual([f["ref"] for f in facts], ["r#5", "r#7"])
        self.assertEqual(field.load_facts(["other"], ""), [])
        self.assertEqual(field.load_facts(["o/r"], "2026-10-02"), [])

    def test_discover_skips_temp_and_duplicates(self):
        def repo(name: str, origin: str) -> Path:
            p = self.tmp / name
            (p / ".claude").mkdir(parents=True)
            (p / ".claude" / "bombolt.md").write_text("---\n---\n")
            subprocess.run(["git", "init", "-q", str(p)], check=True)
            subprocess.run(["git", "-C", str(p), "remote", "add", "origin", origin], check=True)
            return p
        a, b = repo("a", "git@github.com:o/r.git"), repo("b", "git@github.com:o/r.git")
        c = repo("c", "git@github.com:o/other.git")
        no_cfg = self.tmp / "d"
        no_cfg.mkdir()
        known = [str(a), str(b), str(c), str(no_cfg)]
        with mock.patch.object(field.bb_sweep, "known_repos", return_value=known), \
                mock.patch.object(field.tempfile, "gettempdir", return_value=str(self.tmp / "elsewhere")):
            self.assertEqual([p.name for p in field.discover()], ["a", "c"])
        with mock.patch.object(field.bb_sweep, "known_repos", return_value=known):
            self.assertEqual(field.discover(), [])  # 都在暫存目錄底下（eval 的工作區）


if __name__ == "__main__":
    unittest.main()
