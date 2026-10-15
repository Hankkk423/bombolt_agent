"""evals/fake_gh.py 的測試：假 gh 的輸出形狀，以及 bombolt 的腳本接到它上面能不能正常運作。

執行：python3 -m unittest tests.test_fake_gh -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
EVALS = ROOT / "evals"
sys.path.insert(0, str(EVALS))
sys.path.insert(0, str(EVALS.parent / "scripts"))
import fake_gh  # noqa: E402

CONFIG = textwrap.dedent("""\
    ---
    base_branch: main
    copy_files: []
    ---

    # bombolt 專案設定：todo-app
    """)


def sh(cmd, cwd, env=None, check=True):
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=env)
    if check and p.returncode != 0:
        raise AssertionError(f"{cmd} 失敗：{p.stdout}\n{p.stderr}")
    return p


class GhFixture(unittest.TestCase):
    """origin（bare，main 有 bombolt 設定）＋ 主 checkout ＋ 假 gh 的 state 與 shim；issue #101 已經開好。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bombolt-fakegh-")).resolve()
        self._saved_env = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
        gc = self.tmp / "global.gitconfig"
        gc.write_text("[user]\n\temail = dev@example.com\n\tname = Dev\n[init]\n\tdefaultBranch = main\n")
        os.environ["GIT_CONFIG_GLOBAL"] = str(gc)
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        self.origin = self.tmp / "origin.git"
        self.repo = self.tmp / "repo"
        sh(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], self.tmp)
        sh(["git", "clone", "-q", str(self.origin), str(self.repo)], self.tmp)
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "bombolt.md").write_text(CONFIG)
        (self.repo / "README.md").write_text("# todo-app\n")
        (self.repo / "app.py").write_text("x = 1\n")
        sh(["git", "add", "-A"], self.repo)
        sh(["git", "commit", "-qm", "init"], self.repo)
        sh(["git", "push", "-q", "origin", "main"], self.repo)
        sh(["git", "config", "bombolt.machine", "eval-mac"], self.repo)
        sh(["git", "config", "github.user", ""], self.repo)

        self.ghdir = self.tmp / "gh"
        self.ghdir.mkdir()
        self.state_path = self.ghdir / "state.json"
        st = fake_gh.new_state("acme", "todo-app", "eval-bot", str(self.origin), next_number=101)
        meta = json.dumps({"base": "main", "base_sha": "x", "slug": "done-filter", "ui": False})
        fake_gh.add_issue(st, "feat(api): done filter", f"內文\n\n<!-- bombolt:meta {meta} -->")
        fake_gh.save_state(self.state_path, st)
        bindir = self.tmp / "bin"
        bindir.mkdir()
        (bindir / "gh").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{EVALS / "fake_gh.py"}" --state "{self.state_path}" "$@"\n')
        (bindir / "gh").chmod(0o755)
        self.env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    # helpers -------------------------------------------------------------

    def gh(self, *args, cwd=None, check=True):
        return sh(["gh", *args], cwd or self.repo, env=self.env, check=check)

    def ghj(self, *args, cwd=None):
        return json.loads(self.gh(*args, cwd=cwd).stdout)

    def state(self):
        return fake_gh.load_state(self.state_path)

    def calls(self):
        return [json.loads(l) for l in (self.ghdir / "calls.jsonl").read_text().splitlines()]

    def push_branch(self, name, fname="feature.txt", content="feat\n", base="main"):
        sh(["git", "checkout", "-q", "-B", name, f"origin/{base}"], self.repo)
        (self.repo / fname).write_text(content)
        sh(["git", "add", "-A"], self.repo)
        sh(["git", "commit", "-qm", f"change {fname}"], self.repo)
        sh(["git", "push", "-q", "-u", "origin", name], self.repo)
        sh(["git", "checkout", "-q", "main"], self.repo)

    def open_pr(self, branch="bb-101-done-filter", body="## 一句話\n\nx\n"):
        self.push_branch(branch)
        (self.tmp / "body.md").write_text(body)
        url = self.gh("pr", "create", "--base", "main", "--head", branch, "--title", "feat: x (#101)",
                      "--body-file", str(self.tmp / "body.md")).stdout.strip()
        return int(url.rsplit("/", 1)[1])

    def run_script(self, name, *args, cwd=None, check=True):
        return sh([sys.executable, str(SCRIPTS / name), *args], cwd or self.repo, env=self.env, check=check)


class TestBasics(GhFixture):
    def test_version_and_auth(self):
        self.assertTrue(self.gh("--version").stdout.startswith("gh version 2.99.0 "))
        hosts = self.ghj("auth", "status", "--hostname", "github.com", "--json", "hosts")
        self.assertEqual(hosts["hosts"]["github.com"][0]["login"], "eval-bot")
        self.assertEqual(self.gh("api", "user", "--jq", ".login").stdout, "eval-bot\n")
        self.assertEqual(self.gh("api", "user", "--jq", ".email // empty").stdout, "")
        self.assertIn("@", self.gh("api", "user/emails", "--jq", ".[].email").stdout)

    def test_repo_view(self):
        self.assertEqual(self.gh("repo", "view", "--json", "name", "-q", ".name").stdout, "todo-app\n")
        j = self.ghj("repo", "view", "--json", "nameWithOwner,defaultBranchRef,owner")
        self.assertEqual(j, {"nameWithOwner": "acme/todo-app", "defaultBranchRef": {"name": "main"},
                             "owner": {"id": "O_1", "login": "acme"}})
        self.assertIn("# todo-app", self.gh("repo", "view").stdout)

    def test_unsupported_is_logged(self):
        p = self.gh("frobnicate", check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn('unknown command "frobnicate"', p.stderr)
        p = self.gh("issue", "view", "101", "--frob", check=False)
        self.assertIn("unknown flag: --frob", p.stderr)
        p = self.gh("issue", "view", "101", "--json", "closedByPullRequestsReferences")
        self.assertEqual(json.loads(p.stdout), {"closedByPullRequestsReferences": None})
        p = self.gh("issue", "view", "101", "--json", "nope", check=False)
        self.assertIn('Unknown JSON field: "nope"', p.stderr)
        flags = [(c["argv"][:2], c["unsupported"]) for c in self.calls()]
        self.assertEqual([u for _, u in flags], [True, True, True, False])

    def test_no_telltale_words_in_errors(self):
        for args in (["frob"], ["pr", "view", "999"], ["issue", "view", "999"], ["api", "repos/acme/todo-app/nope"],
                     ["pr", "create", "--base", "nope", "--head", "x", "--title", "t", "--body", "b"]):
            p = self.gh(*args, check=False)
            self.assertNotEqual(p.returncode, 0)
            self.assertNotRegex((p.stdout + p.stderr).lower(), r"fake|eval|mock")

    def test_parallel_writes_are_serialized(self):
        errs = []

        def comment(i):
            p = self.gh("issue", "comment", "101", "--body", f"c{i}", check=False)
            if p.returncode != 0:
                errs.append(p.stderr)

        ts = [threading.Thread(target=comment, args=(i,)) for i in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errs, [])
        comments = self.state()["issues"]["101"]["comments"]
        self.assertEqual(sorted(c["body"] for c in comments), [f"c{i}" for i in range(8)])
        self.assertEqual(len({c["id"] for c in comments}), 8)


class TestIssue(GhFixture):
    def test_view_json_shape(self):
        self.gh("issue", "comment", "101", "--body", "hello")
        j = self.ghj("issue", "view", "101", "--json", "number,title,body,state,url,labels,comments,assignees")
        self.assertEqual(j["number"], 101)
        self.assertEqual(j["state"], "OPEN")
        self.assertEqual(j["url"], "https://github.com/acme/todo-app/issues/101")
        self.assertEqual([l["name"] for l in j["labels"]], ["bombolt"])
        c = j["comments"][0]
        self.assertEqual(c["author"], {"login": "eval-bot"})
        self.assertRegex(c["url"], r"/issues/101#issuecomment-\d+$")
        self.assertEqual(c["body"], "hello")

    def test_view_text(self):
        self.gh("issue", "comment", "101", "--body", "第一則")
        out = self.gh("issue", "view", "101").stdout
        self.assertIn("title:\tfeat(api): done filter\n", out)
        self.assertIn("number:\t101\n--\n內文", out)
        self.assertNotIn("第一則", out)
        self.assertIn("第一則", self.gh("issue", "view", "101", "--comments").stdout)

    def test_view_errors(self):
        p = self.gh("issue", "view", "999", check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("Could not resolve", p.stderr)

    def test_comment_and_patch(self):
        (self.tmp / "c.md").write_text("原本")
        url = self.gh("issue", "comment", "101", "--body-file", str(self.tmp / "c.md")).stdout.strip()
        cid = url.rsplit("-", 1)[1]
        (self.tmp / "p.json").write_text(json.dumps({"body": "改過"}))
        res = self.ghj("api", "-X", "PATCH", f"repos/{{owner}}/{{repo}}/issues/comments/{cid}", "--input",
                       str(self.tmp / "p.json"))
        self.assertEqual(res["html_url"], url)
        res = self.ghj("api", "-X", "PATCH", f"repos/acme/todo-app/issues/comments/{cid}", "-f", "body=再改")
        self.assertEqual(res["body"], "再改")
        self.assertEqual(self.state()["issues"]["101"]["comments"][-1]["body"], "再改")
        p = self.gh("api", "-X", "PATCH", "repos/{owner}/{repo}/issues/comments/1", "-f", "body=x", check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("Not Found (HTTP 404)", p.stderr)

    def test_edit_labels_assignees(self):
        p = self.gh("issue", "edit", "101", "--add-label", "bombolt:blocked", check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("not found", p.stderr)
        self.gh("label", "create", "bombolt:blocked", "--color", "D93F0B")
        p = self.gh("label", "create", "bombolt:blocked", "--color", "D93F0B", check=False)
        self.assertIn("already exists", p.stderr)
        self.gh("issue", "edit", "101", "--add-label", "bombolt:blocked", "--add-assignee", "@me")
        i = self.state()["issues"]["101"]
        self.assertEqual(i["labels"], ["bombolt", "bombolt:blocked"])
        self.assertEqual(i["assignees"], ["eval-bot"])
        self.gh("issue", "edit", "101", "--remove-label", "bombolt:blocked")
        self.assertEqual(self.state()["issues"]["101"]["labels"], ["bombolt"])
        self.assertEqual(self.gh("issue", "list", "--label", "bombolt", "--json", "number", "-q", ".[].number").stdout, "101\n")

    def test_close_create_list(self):
        self.gh("issue", "close", "101", "--comment", "done")
        p = self.gh("issue", "close", "101")
        self.assertIn("already closed", p.stderr)
        self.assertEqual(self.ghj("issue", "list", "--json", "number"), [])
        self.assertEqual(self.ghj("issue", "list", "--state", "closed", "--json", "number,state"),
                         [{"number": 101, "state": "CLOSED"}])
        url = self.gh("issue", "create", "--title", "t2", "--body", "b", "--label", "bombolt").stdout.strip()
        self.assertTrue(url.endswith("/issues/102"))


class TestPr(GhFixture):
    def test_create_and_view(self):
        n = self.open_pr()
        self.assertEqual(n, 102)
        tip = sh(["git", "rev-parse", "origin/bb-101-done-filter"], self.repo).stdout.strip()
        fields = "number,url,title,state,isDraft,headRefName,headRefOid,baseRefName,body,comments"
        for arg in ("102", "https://github.com/acme/todo-app/pull/102", "bb-101-done-filter"):
            j = self.ghj("pr", "view", arg, "--json", fields)
            self.assertEqual((j["number"], j["headRefOid"], j["state"], j["isDraft"], j["baseRefName"]),
                             (102, tip, "OPEN", False, "main"))
        # 沒帶編號：用目前的 branch（例如在 worktree 裡）
        sh(["git", "checkout", "-q", "bb-101-done-filter"], self.repo)
        self.assertEqual(self.ghj("pr", "view", "--json", "number")["number"], 102)
        sh(["git", "checkout", "-q", "main"], self.repo)
        p = self.gh("pr", "view", check=False)
        self.assertIn('no pull requests found for branch "main"', p.stderr)
        p = self.gh("pr", "view", "101", "--json", "number", check=False)  # issue 編號
        self.assertEqual(p.returncode, 1)
        self.assertIn("Could not resolve to a PullRequest with the number of 101", p.stderr)
        self.assertEqual(self.gh("pr", "view", "102", "--json", "body", "-q", ".body").stdout, "## 一句話\n\nx\n\n")

    def test_head_oid_is_live(self):
        n = self.open_pr()
        sh(["git", "checkout", "-q", "bb-101-done-filter"], self.repo)
        (self.repo / "more.txt").write_text("m\n")
        sh(["git", "add", "-A"], self.repo)
        sh(["git", "commit", "-qm", "more"], self.repo)
        sh(["git", "push", "-q"], self.repo)
        tip = sh(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()
        self.assertEqual(self.ghj("pr", "view", str(n), "--json", "headRefOid")["headRefOid"], tip)
        j = self.ghj("pr", "view", str(n), "--json", "additions,deletions,changedFiles,files,commits")
        self.assertEqual((j["additions"], j["changedFiles"], len(j["commits"])), (2, 2, 2))

    def test_create_errors(self):
        self.push_branch("bb-101-done-filter")
        p = self.gh("pr", "create", "--base", "release", "--head", "bb-101-done-filter", "--title", "t", "--body", "b",
                    check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("Base ref must be a branch", p.stderr)
        p = self.gh("pr", "create", "--base", "main", "--head", "bb-nope", "--title", "t", "--body", "b", check=False)
        self.assertIn("Head ref must be a branch", p.stderr)
        sh(["git", "checkout", "-q", "-b", "local-only"], self.repo)
        p = self.gh("pr", "create", "--title", "t", "--body", "b", check=False)
        self.assertIn("you must first push the current branch", p.stderr)
        sh(["git", "checkout", "-q", "main"], self.repo)
        sh(["git", "push", "-q", "origin", "main:same-as-main"], self.repo)
        p = self.gh("pr", "create", "--base", "main", "--head", "same-as-main", "--title", "t", "--body", "b", check=False)
        self.assertIn("No commits between main and same-as-main", p.stderr)
        p = self.gh("pr", "create", "--base", "main", "--head", "bb-101-done-filter", "--body", "b", check=False)
        self.assertIn("must provide `--title`", p.stderr)
        self.gh("pr", "create", "--base", "main", "--head", "bb-101-done-filter", "--title", "t", "--body", "b")
        p = self.gh("pr", "create", "--base", "main", "--head", "bb-101-done-filter", "--title", "t", "--body", "b",
                    check=False)
        self.assertIn("already exists", p.stderr)

    def test_attach(self):
        self.push_branch("bb-101-done-filter")
        shots = self.repo / ".bombolt" / "shots"
        shots.mkdir(parents=True)
        (shots / "before-1-list.png").write_bytes(b"\x89PNG before")
        (shots / "after-1-list.png").write_bytes(b"\x89PNG after")
        body = ("![before](.bombolt/shots/before-1-list.png)\n"
                f"![after]({shots / 'after-1-list.png'})\n")
        (self.tmp / "body.md").write_text(body)
        url = self.gh("pr", "create", "--base", "main", "--head", "bb-101-done-filter", "--title", "t",
                      "--body-file", str(self.tmp / "body.md"),
                      "--attach", str(shots / "before-1-list.png"), "--attach", ".bombolt/shots/after-1-list.png").stdout
        n = url.strip().rsplit("/", 1)[1]
        pr = self.state()["prs"][n]
        self.assertNotIn(".bombolt/shots", pr["body"])
        self.assertEqual(pr["body"].count("https://github.com/user-attachments/assets/"), 2)
        self.assertEqual([a["name"] for a in pr["attachments"]], ["before-1-list.png", "after-1-list.png"])
        self.assertTrue(all(a["referenced"] for a in pr["attachments"]))
        p = self.gh("pr", "edit", n, "--attach", "missing.png", check=False)
        self.assertIn("no such file", p.stderr)

    def test_edit_comment_list_diff_checks(self):
        n = str(self.open_pr())
        (self.tmp / "b2.md").write_text("新的內文")
        self.gh("pr", "edit", n, "--body-file", str(self.tmp / "b2.md"))
        pr = self.state()["prs"][n]
        self.assertEqual((pr["body"], pr["edits"]), ("新的內文", 1))
        url = self.gh("pr", "comment", n, "--body", "紀錄").stdout.strip()
        self.assertRegex(url, rf"/pull/{n}#issuecomment-\d+$")
        self.assertEqual(self.ghj("pr", "view", n, "--json", "comments")["comments"][0]["url"], url)
        lst = self.ghj("pr", "list", "--head", "bb-101-done-filter", "--state", "all", "--json",
                       "number,state,url,headRefOid,author", "--limit", "20")
        self.assertEqual(lst[0]["author"]["login"], "eval-bot")
        self.assertEqual(self.ghj("pr", "list", "--base", "main", "--state", "open", "--json", "number,url,isDraft"),
                         [{"number": int(n), "url": f"https://github.com/acme/todo-app/pull/{n}", "isDraft": False}])
        self.assertIn("+feat", self.gh("pr", "diff", n).stdout)
        self.assertEqual(self.gh("pr", "diff", n, "--name-only").stdout, "feature.txt\n")
        p = self.gh("pr", "checks", n, check=False)
        self.assertIn("no checks reported", p.stderr)
        self.gh("pr", "close", n)
        self.assertEqual(self.ghj("pr", "list", "--json", "number"), [])
        st = self.state()
        st["prs"][n]["state"] = "MERGED"
        fake_gh.save_state(self.state_path, st)
        self.assertEqual(self.ghj("pr", "list", "--state", "closed", "--json", "number,state"),
                         [{"number": int(n), "state": "MERGED"}])
        self.assertEqual(self.ghj("pr", "list", "--state", "merged", "--json", "number")[0]["number"], int(n))

    def test_mergeable(self):
        n = str(self.open_pr())
        self.assertEqual(self.ghj("pr", "view", n, "--json", "mergeable,mergeStateStatus"),
                         {"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"})
        (self.repo / "feature.txt").write_text("conflict on main\n")
        sh(["git", "add", "-A"], self.repo)
        sh(["git", "commit", "-qm", "main change"], self.repo)
        sh(["git", "push", "-q", "origin", "main"], self.repo)
        self.assertEqual(self.ghj("pr", "view", n, "--json", "mergeable,mergeStateStatus"),
                         {"mergeable": "CONFLICTING", "mergeStateStatus": "DIRTY"})

    def test_ready_and_draft(self):
        self.push_branch("bb-101-done-filter")
        url = self.gh("pr", "create", "--base", "main", "--head", "bb-101-done-filter", "--title", "🚧 t", "--body", "b",
                      "--draft").stdout.strip()
        n = url.rsplit("/", 1)[1]
        self.assertTrue(self.ghj("pr", "view", n, "--json", "isDraft")["isDraft"])
        self.gh("pr", "ready", n)
        self.assertFalse(self.ghj("pr", "view", n, "--json", "isDraft")["isDraft"])


class TestReview(GhFixture):
    def setUp(self):
        super().setUp()
        self.n = self.open_pr()
        st = self.state()
        self.thread = fake_gh.add_review_thread(st, self.n, "app.py", 1, "amy-reviewer", "沒有 due 的要排最後")
        fake_gh.add_review(st, self.n, "amy-reviewer", "CHANGES_REQUESTED", "請修改")
        fake_gh.add_comment(st, self.n, "順便把錯誤訊息改清楚", author="amy-reviewer")
        fake_gh.save_state(self.state_path, st)

    def threads(self):
        q = ("query($owner:String!,$repo:String!,$n:Int!){ repository(owner:$owner,name:$repo){ pullRequest(number:$n){\n"
             "  reviewThreads(first:100){ nodes{ id isResolved path line comments(first:20){ nodes{ author{login} body createdAt } } } } } } }")
        return self.ghj("api", "graphql", "-f", f"query={q}", "-f", "owner=acme", "-f", "repo=todo-app",
                        "-F", f"n={self.n}")["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]

    def test_view_reviews_and_threads(self):
        j = self.ghj("pr", "view", str(self.n), "--json", "number,reviews,comments,reviewDecision")
        self.assertEqual(j["reviews"][0]["state"], "CHANGES_REQUESTED")
        self.assertEqual(j["reviews"][0]["author"]["login"], "amy-reviewer")
        self.assertEqual(j["comments"][0]["body"], "順便把錯誤訊息改清楚")
        self.assertEqual(j["reviewDecision"], "CHANGES_REQUESTED")
        nodes = self.threads()
        self.assertEqual(len(nodes), 1)
        th = nodes[0]
        self.assertEqual((th["isResolved"], th["path"], th["line"]), (False, "app.py", 1))
        c = th["comments"]["nodes"][0]
        self.assertEqual((c["author"]["login"], c["body"]), ("amy-reviewer", "沒有 due 的要排最後"))
        self.assertEqual(c["databaseId"], self.thread["comments"][0]["id"])

    def test_bb_pr_feedback_query(self):
        # bb_pr.py context 實際用的 query：-F owner={owner} 這種 placeholder 跟真的 gh 一樣會換成目前的 repo
        import bb_pr
        res = self.ghj("api", "graphql", "-f", f"query={bb_pr.FEEDBACK_QUERY}", "-F", "owner={owner}",
                       "-F", "repo={repo}", "-F", f"n={self.n}")
        pr = res["data"]["repository"]["pullRequest"]
        self.assertEqual(pr["mergeable"], "MERGEABLE")
        self.assertEqual([(r["state"], r["body"]) for r in pr["reviews"]["nodes"]], [("CHANGES_REQUESTED", "請修改")])
        self.assertIn("#pullrequestreview-", pr["reviews"]["nodes"][0]["url"])
        th = pr["reviewThreads"]["nodes"][0]
        self.assertEqual(th["comments"]["nodes"][0]["databaseId"], self.thread["comments"][0]["id"])

    def test_rest_replies(self):
        rc = self.ghj("api", f"repos/acme/todo-app/pulls/{self.n}/comments")
        self.assertEqual(len(rc), 1)
        cid = rc[0]["id"]
        res = self.ghj("api", f"repos/{{owner}}/{{repo}}/pulls/{self.n}/comments/{cid}/replies", "-f",
                       "body=已修正於 abc1234")
        self.assertEqual(res["in_reply_to_id"], cid)
        th = self.state()["prs"][str(self.n)]["threads"][0]
        self.assertEqual([c["body"] for c in th["comments"]], ["沒有 due 的要排最後", "已修正於 abc1234"])
        self.assertFalse(th["isResolved"])
        p = self.gh("api", f"repos/acme/todo-app/pulls/{self.n}/comments/1/replies", "-f", "body=x", check=False)
        self.assertIn("Not Found", p.stderr)

    def test_graphql_mutations(self):
        tid = self.thread["id"]
        res = self.ghj("api", "graphql", "-f",
                       "query=mutation($t:ID!,$b:String!){ addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$t, body:$b}){ comment{ id } } }",
                       "-f", f"threadId={tid}", "-f", "body=好")
        self.assertIn("addPullRequestReviewThreadReply", res["data"])
        res = self.ghj("api", "graphql", "-f",
                       f'query=mutation {{ resolveReviewThread(input:{{threadId:"{tid}"}}) {{ thread {{ isResolved }} }} }}')
        self.assertTrue(res["data"]["resolveReviewThread"]["thread"]["isResolved"])
        th = self.state()["prs"][str(self.n)]["threads"][0]
        self.assertTrue(th["isResolved"])
        self.assertEqual(th["comments"][-1]["body"], "好")
        p = self.gh("api", "graphql", "-f", "query=mutation { deleteIssue(input:{issueId:\"x\"}) { clientMutationId } }",
                    check=False)
        self.assertEqual(p.returncode, 1)
        self.assertTrue(self.calls()[-1]["unsupported"])

    def test_issue_rest(self):
        lst = self.ghj("api", "repos/acme/todo-app/issues/101/comments")
        self.assertEqual(lst, [])
        res = self.ghj("api", "repos/acme/todo-app/issues/101/comments", "-f", "body=hi")
        self.assertRegex(res["html_url"], r"#issuecomment-\d+$")
        j = self.ghj("api", f"repos/acme/todo-app/issues/{self.n}")
        self.assertIn("pull_request", j)


class TestJq(unittest.TestCase):
    DATA = [{"number": 1, "state": "OPEN", "author": {"login": "a"}, "labels": [{"name": "x"}], "email": None},
            {"number": 2, "state": "CLOSED", "author": {"login": "b"}, "labels": [], "email": "e@x"}]

    def jq(self, expr, data=None):
        return fake_gh.jq(expr, self.DATA if data is None else data)

    def test_paths(self):
        self.assertEqual(self.jq(".[].number"), "1\n2\n")
        self.assertEqual(self.jq(".[0].author.login"), "a\n")
        self.assertEqual(self.jq(".[1].labels"), "[]\n")
        self.assertEqual(self.jq(".[].labels[].name"), "x\n")
        self.assertEqual(self.jq("."), fake_gh.dumps(self.DATA) + "\n")
        self.assertEqual(self.jq(".body", {"body": "多行\n內文"}), "多行\n內文\n")

    def test_filters(self):
        self.assertEqual(self.jq('.[] | select(.state == "OPEN") | .number'), "1\n")
        self.assertEqual(self.jq('.[] | select(.state != "OPEN" and .number > 1) | .author.login'), "b\n")
        self.assertEqual(self.jq('.[] | select(.number == 1 or .number == 2) | .number'), "1\n2\n")
        self.assertEqual(self.jq("map(.number)"), "[1,2]\n")
        self.assertEqual(self.jq("[.[] | .number]"), "[1,2]\n")
        self.assertEqual(self.jq('map(.author.login) | join(",")'), "a,b\n")
        self.assertEqual(self.jq("length"), "2\n")
        self.assertEqual(self.jq(".[].email // empty"), "e@x\n")
        self.assertEqual(self.jq('.[0].email // "none"'), "none\n")
        self.assertEqual(self.jq(".[] | .number | length", [{"number": [1, 2]}]), "2\n")

    def test_unsupported(self):
        with self.assertRaises(fake_gh.GhError) as cm:
            self.jq("to_entries")
        self.assertTrue(cm.exception.unsupported)


class TestBomboltScripts(GhFixture):
    """bombolt 自己的腳本接在假 gh 上：認領 → 建 worktree → 發 PR → 修改紀錄 → 接手的 context。"""

    def script_json(self, *args, cwd=None):
        return json.loads(self.run_script(*args, cwd=cwd).stdout)

    def test_claim_pr_record_flow(self):
        st = self.script_json("bb_issues.py", "status", "--issue", "101")
        self.assertEqual((st["verdict"], st["branch"]), ("free", "bb-101-done-filter"))
        wt = self.script_json("bb_worktree.py", "create", "--issue", "101", "--slug", "done-filter",
                              "--session-id", "sess-1")
        wt_path = Path(wt["path"])
        res = self.script_json("bb_issues.py", "mark", "--issue", "101", "--event", "claim",
                               "--branch", "bb-101-done-filter", "--session-id", "sess-1")
        self.assertEqual((res["status"], res["state"]), ("ok", "working"))
        issue = self.state()["issues"]["101"]
        self.assertEqual(issue["assignees"], ["eval-bot"])
        self.assertEqual(len(issue["comments"]), 1)
        self.assertEqual(self.script_json("bb_issues.py", "status", "--issue", "101")["verdict"], "mine")

        (wt_path / "app.py").write_text("x = 2\n")
        sh(["git", "add", "-A"], wt_path)
        sh(["git", "commit", "-qm", "feat: done filter"], wt_path)
        sh(["git", "push", "-q", "-u", "origin", "bb-101-done-filter"], wt_path)
        body = "## 一句話\n\nx\n\n## 🔁 要修改的話\n\n<!-- bombolt:handoff -->\n\n[看]({{PR_URL}}/changes)\n\nCloses #101\n"
        (self.tmp / "pr-body.md").write_text(body)
        url = self.gh("pr", "create", "--base", "main", "--head", "bb-101-done-filter", "--title", "feat: x (#101)",
                      "--body-file", str(self.tmp / "pr-body.md"), cwd=wt_path).stdout.strip()
        link = self.script_json("bb_walkthrough.py", "link", "--pr", url, cwd=wt_path)
        self.assertEqual(link["replaced"], 1)
        res = self.script_json("bb_issues.py", "mark", "--issue", "101", "--event", "pr", "--pr", url)
        self.assertEqual(res["state"], "in_review")
        issue = self.state()["issues"]["101"]
        self.assertEqual(len(issue["comments"]), 1)  # 原地改寫，沒有新增
        self.assertIn("bombolt 狀態：", issue["comments"][0]["body"])
        self.assertEqual(self.script_json("bb_issues.py", "status", "--issue", "101")["verdict"], "in_review")

        (self.tmp / "record.md").write_text("**決定**\n- 用 X 不用 Y\n")
        rec = self.script_json("bb_pr.py", "record", "--pr", "102", "--session-id", "sess-1", "--kind", "round",
                               "--summary", "發 PR", "--body-file", str(self.tmp / "record.md"), "--verified", cwd=wt_path)
        self.assertEqual((rec["status"], rec["n"]), ("ok", 1))
        pr = self.state()["prs"]["102"]
        self.assertIn("<!-- bombolt:record", pr["comments"][0]["body"])
        self.assertIn("<!-- bombolt:handoff:start -->", pr["body"])
        self.assertIn("https://github.com/acme/todo-app/pull/102/changes", pr["body"])
        self.assertIn('"/bombolt:bb-fix 102"', pr["body"])

        ctx = self.script_json("bb_pr.py", "context", "--pr", "101", "--session-id", "sess-2")  # 用 issue 編號找到 PR
        self.assertEqual(ctx["pr"]["number"], 102)
        self.assertTrue(ctx["verified_head"])
        self.assertEqual(ctx["unrecorded_commits"], [])
        self.assertEqual(len(ctx["records"]), 1)

        lst = self.script_json("bb_issues.py", "list")
        self.assertEqual([(i["number"], i["verdict"]) for i in lst["issues"]], [(101, "in_review")])
        self.assertFalse(any(c["unsupported"] for c in self.calls()), [c for c in self.calls() if c["unsupported"]])

    def test_context_and_bb_gh(self):
        out = sh([sys.executable, str(SCRIPTS / "bb_context.py")], self.repo, env=self.env).stdout
        self.assertIn("gh：gh version 2.99.0", out)
        self.assertIn("GitHub 帳號：`eval-bot`", out)
        p = sh([str(ROOT / "bin" / "bb-gh"), "issue", "view", "101", "--json", "number", "-q", ".number"], self.repo,
               env=self.env)
        self.assertEqual(p.stdout, "101\n")


if __name__ == "__main__":
    unittest.main()
