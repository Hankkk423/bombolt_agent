"""bombolt 腳本的測試。用本機的 bare repo 當 origin、用假的 gh 取代 GitHub，不碰任何真實的 repo。

執行：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import bb_guard  # noqa: E402
import bb_issues  # noqa: E402
import bb_lib  # noqa: E402
import bb_pr  # noqa: E402
import bb_walkthrough  # noqa: E402

CONFIG = textwrap.dedent("""\
    ---
    # 註解要被忽略
    base_branch: prod
    protected_branches: [dev, staging]
    copy_files:
      - backend/.env.local
      - backend/.env.missing
      - tracked.env
    secret_paths:
      - hank-log.txt
      - "*-log.txt"
    deny_commands:
      - 'make\\s+vercel-env'
    ---

    # 內文
    """)


def sh(cmd, cwd, env=None, check=True):
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=env)
    if check and p.returncode != 0:
        raise AssertionError(f"{cmd} 失敗：{p.stdout}\n{p.stderr}")
    return p


class RepoFixture(unittest.TestCase):
    """每個測試一套：origin（bare）＋ 主 checkout，origin 有 main 與 prod 兩支。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bombolt-test-")).resolve()
        # 隔離：不讀使用者真實的 ~/.gitconfig（裡面可能有 github.user、includeIf）
        self._saved_env = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
        self.global_gitconfig = self.tmp / "global.gitconfig"
        self.global_gitconfig.write_text("[user]\n\temail = t@example.com\n\tname = t\n[init]\n\tdefaultBranch = main\n")
        os.environ["GIT_CONFIG_GLOBAL"] = str(self.global_gitconfig)
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        self.origin = self.tmp / "origin.git"
        self.repo = self.tmp / "repo"
        sh(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], self.tmp)
        sh(["git", "clone", "-q", str(self.origin), str(self.repo)], self.tmp)
        for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
            sh(["git", "config", k, v], self.repo)
        (self.repo / ".gitignore").write_text(".env*\n*-log.txt\n")
        (self.repo / "app.txt").write_text("v1\n")
        (self.repo / "tracked.env").write_text("tracked\n")
        sh(["git", "add", "-A"], self.repo)
        sh(["git", "add", "-f", "tracked.env"], self.repo)
        sh(["git", "commit", "-qm", "init"], self.repo)
        sh(["git", "push", "-q", "origin", "main"], self.repo)
        sh(["git", "push", "-q", "origin", "main:prod"], self.repo)
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "bombolt.md").write_text(CONFIG)
        (self.repo / "backend").mkdir()
        (self.repo / "backend" / ".env.local").write_text("SECRET=1\n")
        (self.repo / "hank-log.txt").write_text("password\n")
        sh(["git", "config", "bombolt.machine", "test-mac"], self.repo)
        # 預設的假 gh：只回答「我是誰」，其他一律失敗——測試絕不能打到真的 GitHub
        stub = self.tmp / "stub-bin"
        stub.mkdir()
        (stub / "gh").write_text('#!/bin/sh\nif [ "$1" = api ] && [ "$2" = user ]; then echo tester; exit 0; fi\nexit 1\n')
        (stub / "gh").chmod(0o755)
        self.env = {**os.environ, "PATH": f"{stub}:{os.environ['PATH']}"}

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, name, *args, cwd=None, check=True):
        return sh([sys.executable, str(SCRIPTS / name), *args], cwd or self.repo, env=self.env, check=check)

    def create_wt(self, issue=12, slug="bookings-default-month", sid="sess-1"):
        out = self.run_script("bb_worktree.py", "create", "--issue", str(issue), "--slug", slug, "--session-id", sid)
        return json.loads(out.stdout)


class TestConfig(RepoFixture):
    def test_parse(self):
        cfg = bb_lib.load_config(self.repo)
        self.assertEqual(cfg["base_branch"], "prod")
        self.assertEqual(cfg["protected_branches"], ["dev", "staging"])
        self.assertEqual(cfg["copy_files"], ["backend/.env.local", "backend/.env.missing", "tracked.env"])
        self.assertEqual(cfg["secret_paths"], ["hank-log.txt", "*-log.txt"])
        self.assertEqual(cfg["deny_commands"], ["make\\s+vercel-env"])

    def test_missing(self):
        (self.repo / ".claude" / "bombolt.md").unlink()
        self.assertIsNone(bb_lib.load_config(self.repo))


class TestWorktree(RepoFixture):
    def test_create_from_latest_origin_base(self):
        # origin/prod 往前走一個 commit（主 checkout 沒有 pull）→ worktree 要從最新的開
        other = self.tmp / "other"
        sh(["git", "clone", "-q", "-b", "prod", str(self.origin), str(other)], self.tmp)
        sh(["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "newer"], other)
        sh(["git", "push", "-q", "origin", "prod"], other)
        newest = sh(["git", "rev-parse", "HEAD"], other).stdout.strip()

        info = self.create_wt()
        self.assertEqual(info["status"], "created")
        wt = Path(info["path"])
        self.assertEqual(wt, self.repo / ".claude/worktrees/bb-12-bookings-default-month")
        self.assertEqual(info["branch"], "bb-12-bookings-default-month")
        self.assertEqual(info["base_sha"], newest)
        self.assertEqual(sh(["git", "rev-parse", "HEAD"], wt).stdout.strip(), newest)
        # 不帶 upstream：git push 不可能意外推到 prod
        up = sh(["git", "rev-parse", "--abbrev-ref", "@{u}"], wt, check=False)
        self.assertNotEqual(up.returncode, 0)
        # 只複製「存在且被 gitignore」的檔
        self.assertEqual(info["copied"], ["backend/.env.local"])
        self.assertTrue((wt / "backend/.env.local").is_file())
        self.assertEqual(len(info["skipped"]), 2)
        self.assertIn("還沒有", info["config_source"])  # 設定還沒 push：先用主 checkout 那份，並說明
        # metadata 在 git dir 裡，不出現在 git status
        self.assertEqual(sh(["git", "status", "--porcelain"], wt).stdout.strip(), "")
        self.assertEqual(bb_lib.read_meta(wt)["issue"], 12)
        # 主 checkout 看不到 .claude/worktrees（寫進了 .git/info/exclude）
        st = sh(["git", "status", "--porcelain"], self.repo).stdout
        self.assertNotIn("worktrees", st)

    def test_create_is_idempotent_and_tracks_new_session(self):
        self.create_wt(sid="sess-1")
        again = self.create_wt(sid="sess-2")
        self.assertEqual(again["status"], "exists")
        self.assertEqual(again["session_id"], "sess-2")
        self.assertEqual(again["previous_session_ids"], ["sess-1"])

    def test_bad_slug(self):
        p = self.run_script("bb_worktree.py", "create", "--issue", "1", "--slug", "Bad Slug", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("slug", p.stderr)

    def test_requires_config(self):
        (self.repo / ".claude" / "bombolt.md").unlink()
        p = self.run_script("bb_worktree.py", "create", "--issue", "1", "--slug", "x", check=False)
        self.assertIn("bb-setup", p.stderr)

    def test_auth_failure_hands_command_to_user(self):
        # agent 的執行環境拿不到 SSH key：印出一行請使用者用 ! 重跑的指令
        fake_ssh = self.tmp / "fake-ssh"
        fake_ssh.write_text('#!/bin/sh\necho "git@github.com: Permission denied (publickey)." >&2\nexit 255\n')
        fake_ssh.chmod(0o755)
        sh(["git", "remote", "set-url", "origin", "git@github.invalid:o/r.git"], self.repo)
        self.env["GIT_SSH_COMMAND"] = str(fake_ssh)
        p = self.run_script("bb_worktree.py", "create", "--issue", "1", "--slug", "x", check=False)
        self.assertEqual(p.returncode, 1)
        cmd = shlex.join(["python3", str(SCRIPTS / "bb_worktree.py"), "create", "--issue", "1", "--slug", "x"])
        self.assertIn(f"! cd {shlex.quote(str(self.repo))} && {cmd}", p.stderr)

    def test_other_fetch_failure_has_no_auth_hint(self):
        p = self.run_script("bb_worktree.py", "create", "--issue", "1", "--slug", "x", "--base", "nope", check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("fetch origin/nope 失敗", p.stderr)
        self.assertNotIn("提示列", p.stderr)

    def test_info_from_inside_worktree(self):
        info = self.create_wt()
        out = json.loads(self.run_script("bb_worktree.py", "info", cwd=info["path"]).stdout)
        self.assertEqual(out["name"], "bb-12-bookings-default-month")
        self.assertTrue(out["artifacts"].endswith("/.bombolt"))
        # 產物目錄在 worktree 裡，但被忽略：不出現在 git status，也不擋 git worktree remove
        (Path(out["artifacts"]) / "progress.md").write_text("x\n")
        self.assertEqual(sh(["git", "status", "--porcelain"], info["path"]).stdout.strip(), "")
        self.assertEqual(out["config_snapshot"], str(bb_lib.config_snapshot_path(info["path"])))

    def commit_config_to_origin(self):
        sh(["git", "checkout", "-q", "-b", "prod", "origin/prod"], self.repo)
        sh(["git", "add", ".claude/bombolt.md"], self.repo)
        sh(["git", "commit", "-qm", "config"], self.repo)
        sh(["git", "push", "-q", "origin", "prod"], self.repo)

    def test_create_uses_origin_config_not_main_checkout(self):
        self.commit_config_to_origin()
        cfg = self.repo / ".claude/bombolt.md"
        cfg.write_text(CONFIG.replace("'make\\s+vercel-env'", "'npm\\s+publish'"))  # 只有本機改過、沒 push
        info = self.create_wt()
        self.assertIn("origin/prod", info["config_source"])
        self.assertIn("沒有採用", info["config_source"])
        self.assertEqual(bb_lib.config_snapshot_path(info["path"]).read_text().strip(), CONFIG.strip())
        wt_cfg = bb_lib.load_worktree_config(info["path"], self.repo)
        self.assertEqual(wt_cfg["deny_commands"], ["make\\s+vercel-env"])

    def test_create_from_branch_without_config(self):
        # IDE 切到還沒有 bombolt 設定的舊 branch：照樣從 origin/prod 建、拿 origin 上的設定
        self.commit_config_to_origin()
        sh(["git", "remote", "set-head", "origin", "prod"], self.repo)
        sh(["git", "checkout", "-q", "main"], self.repo)
        info = self.create_wt()
        self.assertEqual(info["status"], "created")
        self.assertEqual(info["base"], "prod")
        self.assertIn("預設 branch", info["config_source"])
        self.assertEqual(bb_lib.config_snapshot_path(info["path"]).read_text().strip(), CONFIG.strip())

    def test_sync_config_takes_latest_origin_version(self):
        self.commit_config_to_origin()
        wt = self.create_wt()["path"]
        other = self.tmp / "other"
        sh(["git", "clone", "-q", "-b", "prod", str(self.origin), str(other)], self.tmp)
        (other / ".claude/bombolt.md").write_text(CONFIG.replace("'make\\s+vercel-env'", "'npm\\s+publish'"))
        sh(["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qam", "new rule"], other)
        sh(["git", "push", "-q", "origin", "prod"], other)
        out = json.loads(self.run_script("bb_worktree.py", "sync-config", cwd=wt).stdout)
        self.assertIn("origin/prod", out["config_source"])
        self.assertEqual(bb_lib.load_worktree_config(wt, self.repo)["deny_commands"], ["npm\\s+publish"])


class TestSnapshot(RepoFixture):
    def test_create_and_remove(self):
        (self.repo / "app.txt").write_text("本地沒 commit 的改動\n")
        out = json.loads(self.run_script("bb_snapshot.py", "create").stdout)
        snap = Path(out["path"])
        self.assertEqual((snap / "app.txt").read_text(), "v1\n")  # 是 origin/prod，不是本地
        self.assertFalse((snap / ".git").exists())
        self.assertNotIn("_plan", sh(["git", "worktree", "list"], self.repo).stdout)
        self.run_script("bb_snapshot.py", "remove", str(snap))
        self.assertFalse(snap.exists())

    def test_two_snapshots_in_the_same_second_get_different_paths(self):
        a = json.loads(self.run_script("bb_snapshot.py", "create").stdout)["path"]
        b = json.loads(self.run_script("bb_snapshot.py", "create").stdout)["path"]
        self.assertNotEqual(a, b)
        for snap in (a, b):
            self.assertEqual((Path(snap) / "app.txt").read_text(), "v1\n")
            self.run_script("bb_snapshot.py", "remove", snap)

    def test_remove_refuses_other_paths(self):
        p = self.run_script("bb_snapshot.py", "remove", str(self.repo), check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertTrue(self.repo.exists())


class TestGuard(RepoFixture):
    def setUp(self):
        super().setUp()
        self.wt = self.create_wt()["path"]

    def bash(self, command, cwd=None):
        return bb_guard.evaluate({"cwd": cwd or self.wt, "tool_name": "Bash", "tool_input": {"command": command}})

    def file(self, tool, path, cwd=None):
        return bb_guard.evaluate({"cwd": cwd or self.wt, "tool_name": tool, "tool_input": {"file_path": path}})

    def test_inactive_outside_bombolt_worktree(self):
        self.assertIsNone(self.bash("git push --force origin main", cwd=str(self.repo)))
        self.assertIsNone(self.bash("kubectl delete pod x", cwd=str(self.tmp)))

    def test_push_rules(self):
        allowed = [
            "git push -u origin bb-12-bookings-default-month",
            "git push",
            "git push origin HEAD",
            "git push origin HEAD:bb-12-bookings-default-month",
            "git add -A && git commit -m 'x' && git push -u origin bb-12-bookings-default-month",
            "git push -u origin bb-12-bookings-default-month 2>&1 | tail -3",
            "git push origin bb-12-bookings-default-month > /tmp/push.log 2>/dev/null",
            "git push -u origin HEAD &>/dev/null",
        ]
        for c in allowed:
            self.assertIsNone(self.bash(c), c)
        denied = [
            "git push origin prod",
            "git push origin HEAD:main",
            "git push origin HEAD:refs/heads/dev",
            "git push --force origin bb-12-bookings-default-month",
            "git push -f",
            "git push --force-with-lease",
            "git push origin +bb-12-bookings-default-month",
            "git push origin :old-branch",
            "git push --delete origin x",
            "git -C . push origin staging",
            "cd x && git push origin other-branch",
            "git push origin main 2>&1",
            "git push origin > /dev/null main",
            'bash -c "cd x && git push origin HEAD:main"',  # B 類規則不管引號：藏在引號裡的也擋
        ]
        for c in denied:
            self.assertIsNotNone(self.bash(c), c)

    def test_config_snapshot_survives_main_checkout_changes(self):
        """使用者在主 checkout 切到沒有 .claude/bombolt.md 的 branch：已經在跑的 worktree 不受影響。"""
        cfg_path = self.repo / ".claude" / "bombolt.md"
        v1 = CONFIG.replace("'make\\s+vercel-env'", "'npm\\s+publish'")  # 只有專案設定才有的規則
        v2 = CONFIG.replace("'make\\s+vercel-env'", "'yarn\\s+publish'")
        self.assertNotEqual(v1, CONFIG)
        cfg_path.write_text(v1)
        self.run_script("bb_worktree.py", "sync-config", cwd=self.wt)
        self.assertIsNotNone(self.bash("npm publish"))

        cfg_path.unlink()  # 使用者在主 checkout 切到沒有設定檔的 branch
        self.assertIsNotNone(self.bash("npm publish"))  # 專案自己的 deny_commands 還在
        self.assertIsNotNone(self.file("Read", str(self.repo / "hank-log.txt")))  # 專案自己的 secret_paths 還在
        self.assertEqual(bb_lib.load_worktree_config(self.wt, self.repo)["base_branch"], "prod")
        # 主 checkout 沒有設定時 sync-config 拒絕，快照維持不變
        p = self.run_script("bb_worktree.py", "sync-config", cwd=self.wt, check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIsNotNone(self.bash("npm publish"))
        # 改了設定：sync-config 之前不套用，之後才套用
        cfg_path.write_text(v2)
        self.assertIsNotNone(self.bash("npm publish"))
        self.assertIsNone(self.bash("yarn publish"))
        self.run_script("bb_worktree.py", "sync-config", cwd=self.wt)
        self.assertIsNone(self.bash("npm publish"))
        self.assertIsNotNone(self.bash("yarn publish"))

    def test_worktree_without_snapshot_falls_back_to_main_checkout(self):
        bb_lib.config_snapshot_path(self.wt).unlink()  # 快照功能之前建的 worktree
        self.assertEqual(bb_lib.config_source(self.wt), "")
        (self.repo / ".claude" / "bombolt.md").write_text(CONFIG.replace("'make\\s+vercel-env'", "'npm\\s+publish'"))
        self.assertIsNotNone(self.bash("npm publish"))  # 讀的是主 checkout 當下的設定

    def test_deny_commands(self):
        for c in ["gh pr merge 3 --squash", "kubectl get pods", "vercel deploy --prod", "vercel --prod",
                  "curl -X POST https://hooks.slack.com/services/x", "curl https://api.line.me/v2/bot/message/push",
                  "helm upgrade x", "terraform apply", "make vercel-env", "gh repo delete x"]:
            self.assertIsNotNone(self.bash(c), c)
        for c in ["gh pr create --title x", "gh issue view 3", "npm test", "vercel dev", "make test",
                  "curl http://localhost:8000/health", "gh pr view --json number"]:
            self.assertIsNone(self.bash(c), c)

    def test_tunnel(self):
        """外網只能經過 bb_sandbox.py：直接開 tunnel 擋下，安裝、查狀態、讀 log 放行。"""
        for c in ["ngrok http 3012", "/opt/homebrew/bin/ngrok http 3012", "nohup ngrok http 3012 &",
                  "npm run dev & ngrok http 3012", "ngrok --config x.yml http 3012", 'bash -c "ngrok tcp 22"',
                  "cloudflared tunnel --url http://localhost:3012"]:
            self.assertIsNotNone(self.bash(c), c)
        for c in ['python3 "/x/scripts/bb_sandbox.py" tunnel --port 3012', "python3 /x/scripts/bb_sandbox.py url",
                  "brew install ngrok", "which ngrok", "pgrep -fl ngrok", "ngrok config add-authtoken abc",
                  "cat .bombolt/sandbox-tunnel.log", "curl -s http://127.0.0.1:4040/api/endpoints",
                  "lsof -nP -iTCP:3012 -sTCP:LISTEN", "cloudflared --version",
                  "curl -s -o /dev/null -w '%{http_code}\\n' -u 'bombolt:pw' https://abc.ngrok-free.dev"]:
            self.assertIsNone(self.bash(c), c)
        self.assertIsNone(self.bash("ngrok http 3012", cwd=str(self.repo)))  # worktree 外不管

    def test_rm(self):
        self.assertIsNone(self.bash("rm -rf node_modules dist"))
        self.assertIsNone(self.bash("rm -rf /tmp/bombolt-shots"))
        self.assertIsNone(self.bash("rm file.txt"))  # 非遞迴不管
        for c in ["rm -rf /", "rm -rf ~", "rm -rf ..", f"rm -rf {self.repo}", "rm -r ../../x", "rm -rf $HOME/x", "rm -rf ."]:
            self.assertIsNotNone(self.bash(c), c)

    def test_secrets(self):
        self.assertIsNotNone(self.file("Read", str(self.repo / "hank-log.txt")))
        self.assertIsNotNone(self.file("Read", "other-log.txt"))
        self.assertIsNotNone(self.file("Read", os.path.expanduser("~/.ssh/id_ed25519")))
        self.assertIsNotNone(self.file("Write", "server.pem"))
        self.assertIsNotNone(self.bash(f"cat {self.repo}/hank-log.txt"))
        self.assertIsNotNone(self.bash("cat ~/.aws/credentials"))
        # .env* 預設允許（使用者裁決）
        self.assertIsNone(self.file("Read", "backend/.env.local"))
        self.assertIsNone(self.file("Read", ".env.local"))
        self.assertIsNone(self.bash("cat backend/.env.local"))
        self.assertIsNone(self.file("Edit", "src/app.ts"))
        self.assertIsNone(self.bash("git log --oneline -5"))

    def test_data_heredoc_body_is_not_checked(self):
        # 寫進檔案的內文只是文字：JS 的 e.key、文件裡寫的指令都不算
        allowed = [
            "cat > .bombolt/walkthrough.md <<'EOF'\nif (e.key === 'Enter') save()\nEOF",
            "cat <<EOF > notes.md\n執行 git push origin main 與 vercel --prod\nEOF",
            "tee docs/x.md <<-EOF\n\tcat server.key\n\tEOF",
        ]
        for c in allowed:
            self.assertIsNone(self.bash(c), c)
        denied = [
            "bash <<'EOF'\ncat server.key\nEOF",
            "cat <<'EOF' | sh\ncat ~/.aws/credentials\nEOF",
            "cat > a.md <<'EOF'\nok\nEOF\ncat server.key",
            "cat > a.md <<'EOF' && cat server.key\nok\nEOF",
        ]
        for c in denied:
            self.assertIsNotNone(self.bash(c), c)

    def test_hook_protocol(self):
        payload = {"cwd": self.wt, "tool_name": "Bash", "tool_input": {"command": "git push origin main"}}
        p = subprocess.run([sys.executable, str(SCRIPTS / "bb_guard.py")], input=json.dumps(payload),
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        out = json.loads(p.stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        payload["tool_input"]["command"] = "git status"
        p = subprocess.run([sys.executable, str(SCRIPTS / "bb_guard.py")], input=json.dumps(payload),
                           capture_output=True, text=True)
        self.assertEqual((p.returncode, p.stdout), (0, ""))


FAKE_GH = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    data = json.load(open(os.environ["FAKE_GH_DATA"]))
    args = sys.argv[1:]
    if data.get("_fail"):
        print("fake gh: 失敗", file=sys.stderr)
        sys.exit(1)
    def save():
        json.dump(data, open(os.environ["FAKE_GH_DATA"], "w"), ensure_ascii=False)
    if args[:2] == ["pr", "view"]:
        pr = data.get("_pr:" + args[2])
        if pr is None:
            print("no pull requests found", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(pr))
        sys.exit(0)
    if args[:2] == ["pr", "comment"]:
        pr = data["_pr:" + args[2]]
        n = data.get("_next_comment_id", 1000)
        url = f"https://github.com/o/r/pull/{args[2]}#issuecomment-{n}"
        pr.setdefault("comments", []).append({"url": url, "body": open(args[args.index("--body-file") + 1]).read()})
        data["_next_comment_id"] = n + 1
        save()
        print(url)
        sys.exit(0)
    if args[:2] == ["pr", "edit"]:
        data["_pr:" + args[2]]["body"] = open(args[args.index("--body-file") + 1]).read()
        save()
        sys.exit(0)
    if args[:2] == ["pr", "list"]:
        if "--head" in args:
            prs = data.get(args[args.index("--head") + 1], [])
        elif "--base" in args:  # 以這個 branch 當 base 的 PR
            prs = data.get("_base:" + args[args.index("--base") + 1], [])
        else:  # 全部：每個 key（branch 名）底下的 PR
            prs = [dict(p, headRefName=k) for k, v in data.items() if not k.startswith("_") for p in v]
        if "--state" in args and args[args.index("--state") + 1] == "open":
            prs = [p for p in prs if p.get("state", "OPEN") == "OPEN"]
        print(json.dumps(prs))
        sys.exit(0)
    if args[:2] == ["issue", "list"]:
        print(json.dumps(data.get("_issues", [])))
        sys.exit(0)
    if args[:2] == ["issue", "view"]:
        issue = data.get("_issue:" + args[2])
        if issue is None:
            sys.exit(1)
        print(json.dumps(issue))
        sys.exit(0)
    if args[:2] == ["issue", "comment"]:
        issue = data["_issue:" + args[2]]
        n = data.get("_next_comment_id", 1000)
        url = f"https://github.com/o/r/issues/{args[2]}#issuecomment-{n}"
        body = open(args[args.index("--body-file") + 1]).read()
        issue.setdefault("comments", []).append({"url": url, "body": body})
        data["_next_comment_id"] = n + 1
        save()
        print(url)
        sys.exit(0)
    if args[:1] == ["api"] and "PATCH" in args:
        if data.get("_patch_fails"):
            sys.exit(1)
        cid = [a for a in args if a.startswith("repos/")][0].rsplit("/", 1)[1]
        body = json.load(open(args[args.index("--input") + 1]))["body"]
        for k, issue in data.items():
            for c in (issue.get("comments", []) if k.startswith(("_issue:", "_pr:")) else []):
                if c["url"].endswith("#issuecomment-" + cid):
                    c["body"] = body
                    save()
                    print(json.dumps({"html_url": c["url"]}))
                    sys.exit(0)
        sys.exit(1)
    if args[:2] == ["api", "user"]:
        print(data.get("_login", "tester"))
        sys.exit(0)
    if args[:2] == ["issue", "edit"]:
        data.setdefault("_edits", []).append(args[2:])
        save()
        sys.exit(0)
    if args[:2] == ["issue", "close"]:
        log = os.environ.get("FAKE_GH_CLOSE_LOG")
        if log:
            with open(log, "a") as f:
                f.write(json.dumps(args) + "\\n")
        sys.exit(0)
    sys.exit(1)
    """)


class TestSweep(RepoFixture):
    def setUp(self):
        super().setUp()
        bindir = self.tmp / "bin"
        bindir.mkdir()
        (bindir / "gh").write_text(FAKE_GH)
        (bindir / "gh").chmod(0o755)
        self.gh_data = self.tmp / "gh.json"
        self.gh_data.write_text("{}")
        home = self.tmp / "home"
        (home / ".claude" / "sessions").mkdir(parents=True)
        self.sessions = home / ".claude" / "sessions"
        self.close_log = self.tmp / "closed-issues.jsonl"
        self.env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_GH_DATA": str(self.gh_data),
                    "HOME": str(home), "FAKE_GH_CLOSE_LOG": str(self.close_log)}

    def closed_issue_numbers(self):
        if not self.close_log.exists():
            return []
        return [json.loads(line)[2] for line in self.close_log.read_text().splitlines()]

    def set_prs(self, mapping):
        self.gh_data.write_text(json.dumps(mapping))

    def work(self, issue, slug, push=True):
        info = self.create_wt(issue=issue, slug=slug, sid=f"sess-{issue}")
        wt = Path(info["path"])
        (wt / f"f{issue}.txt").write_text("x\n")
        sh(["git", "add", "-A"], wt)
        sh(["git", "commit", "-qm", f"work {issue}"], wt)
        if push:
            sh(["git", "push", "-q", "-u", "origin", info["branch"]], wt)
        head = sh(["git", "rev-parse", "HEAD"], wt).stdout.strip()
        return info, wt, head

    def sweep(self, *extra):
        out = self.run_script("bb_sweep.py", str(self.repo), "--json", *extra)
        return {Path(i["path"]).name: i for i in json.loads(out.stdout)[0]["worktrees"]}

    def test_decisions(self):
        a, _, head_a = self.work(1, "merged")
        b, _, _ = self.work(2, "open-pr")
        c, _, _ = self.work(3, "no-pr")
        d, wt_d, head_d = self.work(4, "dirty")
        (wt_d / "wip.txt").write_text("wip\n")
        e, wt_e, head_e = self.work(5, "unpushed")
        (wt_e / "more.txt").write_text("more\n")
        sh(["git", "add", "-A"], wt_e)
        sh(["git", "commit", "-qm", "more"], wt_e)
        f, _, head_f = self.work(6, "active")
        g, _, _ = self.work(7, "moved-after-merge")
        self.set_prs({
            a["branch"]: [{"number": 1, "state": "MERGED", "url": "u", "headRefOid": head_a, "mergedAt": "x"}],
            b["branch"]: [{"number": 2, "state": "OPEN", "url": "u", "headRefOid": "x", "mergedAt": None}],
            d["branch"]: [{"number": 4, "state": "MERGED", "url": "u", "headRefOid": head_d, "mergedAt": "x"}],
            e["branch"]: [{"number": 5, "state": "MERGED", "url": "u", "headRefOid": head_e, "mergedAt": "x"}],
            f["branch"]: [{"number": 6, "state": "MERGED", "url": "u", "headRefOid": head_f, "mergedAt": "x"}],
            g["branch"]: [{"number": 7, "state": "MERGED", "url": "u", "headRefOid": "0" * 40, "mergedAt": "x"}],
        })
        # 一個活著的 session（用這個測試程序自己的 pid）正在用 #6
        (self.sessions / "1.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "sess-6", "cwd": str(self.repo)}))
        # 一個已經死掉的 session 指向 #1 —— 不應該擋住刪除
        (self.sessions / "2.json").write_text(json.dumps({"pid": 999999, "sessionId": "sess-1", "cwd": a["path"]}))

        r = self.sweep()
        self.assertEqual(r["bb-1-merged"]["action"], "remove")
        self.assertEqual(r["bb-2-open-pr"]["action"], "keep")
        self.assertEqual(r["bb-3-no-pr"]["action"], "keep")
        self.assertEqual(r["bb-4-dirty"]["action"], "keep")
        self.assertEqual(r["bb-5-unpushed"]["action"], "keep")
        self.assertEqual(r["bb-6-active"]["action"], "keep")
        self.assertEqual(r["bb-7-moved-after-merge"]["action"], "keep")
        self.assertTrue(any("session" in x for x in r["bb-6-active"]["reasons"]))

        # dry-run 不刪任何東西
        self.assertTrue(Path(a["path"]).exists())

        r = self.sweep("--apply")
        self.assertFalse(Path(a["path"]).exists())
        branches = sh(["git", "branch", "--list", "bb-*"], self.repo).stdout
        self.assertNotIn("bb-1-merged", branches)
        for keep in (b, c, d, e, f, g):
            self.assertTrue(Path(keep["path"]).exists(), keep["name"])
            self.assertIn(keep["branch"], branches)
        # 刪 worktree 的同時把對應的 issue 關掉；保留的那些完全不碰 issue
        self.assertIn("已關閉", r["bb-1-merged"]["result"])
        self.assertEqual(self.closed_issue_numbers(), ["1"])

    def remote_has(self, branch):
        return sh(["git", "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}"], self.repo, check=False).returncode == 0

    def merged(self, info, head, number=1):
        return {info["branch"]: [{"number": number, "state": "MERGED", "url": "u", "headRefOid": head, "mergedAt": "x"}]}

    def test_remote_branch_deleted_when_everything_matches(self):
        a, _, head = self.work(1, "merged")
        self.set_prs(self.merged(a, head))
        r = self.sweep()
        self.assertTrue(r["bb-1-merged"]["remote"]["delete"])
        self.assertTrue(self.remote_has(a["branch"]))  # dry-run 不刪
        r = self.sweep("--apply")
        self.assertIn("已刪除遠端 branch", r["bb-1-merged"]["result"])
        self.assertIn(head, r["bb-1-merged"]["result"])  # 印出 sha，救得回來
        self.assertFalse(self.remote_has(a["branch"]))
        # 其他遠端 branch 一個都沒動
        for b in ("main", "prod"):
            self.assertTrue(self.remote_has(b), b)

    def test_remote_kept_when_someone_pushed_after_merge(self):
        a, wt, head = self.work(1, "merged")
        self.set_prs(self.merged(a, head))
        # 別人在 merge 之後又推了一個 commit 到遠端（本地沒有）
        other = self.tmp / "other"
        sh(["git", "clone", "-q", "-b", a["branch"], str(self.origin), str(other)], self.tmp)
        sh(["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "late"], other)
        sh(["git", "push", "-q", "origin", a["branch"]], other)
        r = self.sweep("--apply")
        self.assertFalse(r["bb-1-merged"].get("remote", {}).get("delete", False))
        self.assertTrue(self.remote_has(a["branch"]))

    def test_lease_refuses_if_remote_moves_between_check_and_push(self):
        a, _, head = self.work(1, "merged")
        import bb_sweep  # noqa: E402
        other = self.tmp / "other"
        sh(["git", "clone", "-q", "-b", a["branch"], str(self.origin), str(other)], self.tmp)
        sh(["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "race"], other)
        sh(["git", "push", "-q", "origin", a["branch"]], other)
        msg = bb_sweep.delete_remote(self.repo, a["branch"], head)  # 拿舊的 sha 去刪
        self.assertIn("沒刪", msg)
        self.assertTrue(self.remote_has(a["branch"]))

    def test_remote_kept_when_another_open_pr_uses_it(self):
        a, _, head = self.work(1, "merged")
        data = self.merged(a, head)
        data["_base:" + a["branch"]] = [{"number": 9, "state": "OPEN", "url": "u"}]  # 有人把 PR 開到它上面
        self.set_prs(data)
        r = self.sweep("--apply")
        self.assertIn("#9", r["bb-1-merged"]["remote"]["reason"])
        self.assertTrue(self.remote_has(a["branch"]))
        self.assertFalse(Path(a["path"]).exists())  # worktree 照樣清掉，只有遠端保留

    def test_remote_kept_for_protected_or_foreign_names(self):
        import bb_sweep  # noqa: E402
        a, _, head = self.work(1, "merged")
        meta = bb_lib.read_meta(a["path"])
        for name in ("prod", "release", "main", "dev", "staging"):
            r = bb_sweep.assess_remote(self.repo, name, {**meta, "branch": name}, {"headRefOid": head})
            self.assertFalse(r["delete"], name)
        r = bb_sweep.assess_remote(self.repo, a["branch"], {}, {"headRefOid": head})  # 不是 bombolt 建的
        self.assertFalse(r["delete"])
        r = bb_sweep.assess_remote(self.repo, "feature/x", {**meta, "branch": "feature/x"}, {"headRefOid": head})
        self.assertFalse(r["delete"])

    def test_keep_remote_flag_and_already_gone(self):
        a, _, head = self.work(1, "merged")
        self.set_prs(self.merged(a, head))
        r = self.sweep("--apply", "--keep-remote")
        self.assertNotIn("遠端 branch", r["bb-1-merged"]["result"])
        self.assertTrue(self.remote_has(a["branch"]))
        b, _, head_b = self.work(2, "gone")
        self.set_prs(self.merged(b, head_b, 2))
        sh(["git", "push", "-q", "origin", "--delete", b["branch"]], self.repo)  # GitHub 自動刪了
        r = self.sweep("--apply")
        self.assertTrue(r["bb-2-gone"]["remote"].get("gone"))
        self.assertFalse(Path(b["path"]).exists())

    def test_gh_failure_keeps_everything(self):
        self.work(1, "merged")
        (self.tmp / "bin" / "gh").write_text("#!/bin/sh\necho 'auth required' >&2\nexit 1\n")
        r = self.sweep("--apply")
        self.assertEqual(r["bb-1-merged"]["action"], "keep")
        self.assertTrue(any("PR" in x for x in r["bb-1-merged"]["reasons"]))

    def test_locked_worktree_kept(self):
        a, _, head = self.work(1, "merged")
        self.set_prs({a["branch"]: [{"number": 1, "state": "MERGED", "url": "u", "headRefOid": head, "mergedAt": "x"}]})
        sh(["git", "worktree", "lock", a["path"], "--reason", "claude agent"], self.repo)
        r = self.sweep("--apply")
        self.assertEqual(r["bb-1-merged"]["action"], "keep")
        self.assertTrue(Path(a["path"]).exists())

    def local_branch(self, name, push=True):
        sh(["git", "checkout", "-q", "-b", name, "main"], self.repo)
        sh(["git", "commit", "-q", "--allow-empty", "-m", name], self.repo)
        head = sh(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()
        if push:
            sh(["git", "push", "-q", "origin", name], self.repo)
        sh(["git", "checkout", "-q", "main"], self.repo)
        return head

    def sweep_branches(self, *extra):
        out = self.run_script("bb_sweep.py", str(self.repo), "--json", *extra)
        return {i["branch"]: i for i in json.loads(out.stdout)[0]["branches"]}

    def has_branch(self, name):
        return sh(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{name}"], self.repo, check=False).returncode == 0

    def test_local_branches_without_worktree(self):
        h_merged = self.local_branch("chore/merged")
        h_gone = self.local_branch("feat/remote-gone")
        sh(["git", "push", "-q", "origin", "--delete", "feat/remote-gone"], self.repo)  # GitHub merge 後自動刪
        self.local_branch("feat/moved")
        self.local_branch("feat/open")
        self.local_branch("feat/no-pr")
        h_reopened = self.local_branch("feat/reopened")
        # 受保護的 branch 即使有 merged PR 也不列出
        h_dev = self.local_branch("dev")
        pr = lambda n, st, h: {"number": n, "state": st, "url": "u", "headRefOid": h, "mergedAt": "x"}
        self.set_prs({
            "chore/merged": [pr(1, "MERGED", h_merged)],
            "feat/remote-gone": [pr(2, "MERGED", h_gone)],
            "feat/moved": [pr(3, "MERGED", "0" * 40)],
            "feat/open": [pr(4, "OPEN", "x")],
            "feat/reopened": [pr(5, "MERGED", h_reopened), pr(6, "OPEN", h_reopened)],
            "dev": [pr(7, "MERGED", h_dev)],
            "main": [pr(8, "MERGED", "x")],
        })
        # 被某個 worktree checkout 的 branch 不在這裡處理
        h_wt = self.local_branch("feat/in-wt")
        self.set_prs({**json.loads(self.gh_data.read_text()), "feat/in-wt": [pr(9, "MERGED", h_wt)]})
        sh(["git", "worktree", "add", "-q", str(self.tmp / "wt"), "feat/in-wt"], self.repo)

        r = self.sweep_branches()
        self.assertEqual(set(r), {"chore/merged", "feat/remote-gone", "feat/moved", "feat/reopened"})
        self.assertEqual(r["chore/merged"]["action"], "remove")
        self.assertEqual(r["feat/remote-gone"]["action"], "remove")
        self.assertEqual(r["feat/moved"]["action"], "keep")
        self.assertEqual(r["feat/reopened"]["action"], "keep")
        self.assertTrue(self.has_branch("chore/merged"))  # dry-run 不刪

        r = self.sweep_branches("--apply")
        self.assertIn("已刪除", r["chore/merged"]["result"])
        for gone in ("chore/merged", "feat/remote-gone"):
            self.assertFalse(self.has_branch(gone), gone)
        for kept in ("feat/moved", "feat/open", "feat/no-pr", "feat/reopened", "dev", "main", "feat/in-wt"):
            self.assertTrue(self.has_branch(kept), kept)
        # 只刪本地：遠端不動、issue 不關
        self.assertTrue(self.remote_has("chore/merged"))
        self.assertEqual(self.closed_issue_numbers(), [])

    def test_local_branch_gh_failure_keeps(self):
        self.local_branch("chore/merged")
        (self.tmp / "bin" / "gh").write_text("#!/bin/sh\necho 'auth required' >&2\nexit 1\n")
        r = self.sweep_branches("--apply")
        self.assertEqual(r["chore/merged"]["action"], "keep")
        self.assertTrue(self.has_branch("chore/merged"))

    def test_no_path_sweeps_known_repos(self):
        _, wt, _ = self.work(1, "merged")
        plain = self.tmp / "plain"  # 沒用過 bombolt 的 repo
        sh(["git", "init", "-q", str(plain)], self.tmp)
        (self.tmp / "not-repo").mkdir()
        claude_json = self.sessions.parent.parent / ".claude.json"
        claude_json.write_text(json.dumps({"projects": {
            str(wt): {}, str(self.repo): {}, str(plain): {}, str(self.tmp / "not-repo"): {}, str(self.tmp / "gone"): {}}}))
        out = self.run_script("bb_sweep.py", "--json", cwd=self.tmp)  # 在 repo 外面執行
        self.assertEqual([r["repo"] for r in json.loads(out.stdout)], [str(self.repo)])
        self.assertTrue(wt.exists())  # 沒加 --apply 不刪
        # 有給路徑就只處理那些
        out = self.run_script("bb_sweep.py", str(plain), "--json", cwd=self.tmp)
        self.assertEqual([r["repo"] for r in json.loads(out.stdout)], [str(plain)])
        # 讀不到 ~/.claude.json：退回目前所在的 repo
        claude_json.unlink()
        out = self.run_script("bb_sweep.py", "--json", cwd=wt)
        self.assertEqual([r["repo"] for r in json.loads(out.stdout)], [str(self.repo)])

    def test_local_branch_lease(self):
        import bb_sweep  # noqa: E402
        head = self.local_branch("chore/merged")
        sh(["git", "branch", "-f", "chore/merged", "main"], self.repo)  # 判斷之後 branch 被移動了
        msg = bb_sweep.delete_branch(self.repo, {"branch": "chore/merged", "head": head})
        self.assertIn("沒刪", msg)
        self.assertTrue(self.has_branch("chore/merged"))


FAKE_GH_PR = textwrap.dedent("""\
    #!/usr/bin/env python3
    # 假的 gh：PR 內文存在 FAKE_GH_STATE 這個 JSON 檔，pr edit 會把 --body-file 的內容寫回去
    import json, os, sys
    args = sys.argv[1:]
    state = os.environ["FAKE_GH_STATE"]
    data = json.load(open(state))
    if args[:2] == ["pr", "view"]:
        print(json.dumps({"body": data["body"], "url": data["url"]}))
    elif args[:2] == ["pr", "edit"]:
        data["body"] = open(args[args.index("--body-file") + 1], encoding="utf-8").read()
        data["edits"] = data.get("edits", 0) + 1
        json.dump(data, open(state, "w"))
    else:
        sys.exit(1)
    """)


def anchor(path, suffix=""):
    return "#diff-" + hashlib.sha256(path.encode("utf-8")).hexdigest() + suffix


class TestWalkthrough(RepoFixture):
    """bb_walkthrough.py：PR 內文「逐段改動」的骨架、檢查、版面與連結。"""

    def setUp(self):
        super().setUp()
        (self.repo / "app.txt").write_text("".join(f"line{i}\n" for i in range(1, 31)))
        (self.repo / "old.txt").write_text("rename me\n")
        (self.repo / "gone.txt").write_text("a\nb\n")
        sh(["git", "add", "-A", "app.txt", "old.txt", "gone.txt"], self.repo)
        sh(["git", "commit", "-qm", "base"], self.repo)
        sh(["git", "push", "-q", "origin", "main"], self.repo)
        sh(["git", "checkout", "-qb", "feat"], self.repo)
        lines = [f"line{i}\n" for i in range(1, 31)]
        lines[2] = "LINE3\n"  # 第 3 行改掉
        del lines[27]         # 第 28 行刪掉（這一段只有刪除）
        (self.repo / "app.txt").write_text("".join(lines))
        sh(["git", "rm", "-q", "gone.txt"], self.repo)
        sh(["git", "mv", "old.txt", "renamed.txt"], self.repo)
        (self.repo / "src").mkdir()
        (self.repo / "src" / "b.js").write_text("const b = 1;\nconst c = '```';\n")
        (self.repo / "package-lock.json").write_text("{}\n")
        (self.repo / "Z.md").write_text("# z\n")
        sh(["git", "add", "-A", "app.txt", "src", "package-lock.json", "Z.md"], self.repo)
        sh(["git", "commit", "-qm", "feat"], self.repo)
        self.skel = self.tmp / "skel.md"

    def skeleton(self, *extra):
        out = self.run_script("bb_walkthrough.py", "skeleton", "--base", "origin/main", "--out", str(self.skel), *extra)
        return json.loads(out.stdout), self.skel.read_text(encoding="utf-8")

    def run_cmd(self, cmd, text, *extra):
        f = self.tmp / "walk.md"
        f.write_text(text, encoding="utf-8")
        p = self.run_script("bb_walkthrough.py", cmd, "--base", "origin/main", "--file", str(f), *extra, check=False)
        return p.returncode, json.loads(p.stdout)

    @staticmethod
    def fill(text):
        return bb_walkthrough.PLACEHOLDER_RE.sub("說明", text)

    def test_skeleton_order_like_files_changed(self):
        info, text = self.skeleton()
        self.assertEqual(info["status"], "ok")
        # 同一層先資料夾、再檔案（不分大小寫照字母）；lock 檔與純改名放在最後的「其他檔案」
        order = re.findall(r"<summary><b><code>(.*?)</code>", text)
        self.assertEqual(order, ["src/b.js", "app.txt", "gone.txt", "Z.md"])
        others = text.split("<summary><b>其他檔案</b>", 1)[1]
        self.assertIn("`package-lock.json`", others)
        self.assertIn("改名自 `old.txt`", others)
        self.assertEqual(text.count("<details>"), text.count("</details>"))
        self.assertNotIn("<details open", text)  # 預設收合

    def test_skeleton_anchors_and_labels(self):
        _, text = self.skeleton()
        self.assertIn("[L3]({{PR_URL}}/changes" + anchor("app.txt", "R3") + ")", text)
        self.assertIn("[原 L28（刪除）]({{PR_URL}}/changes" + anchor("app.txt", "L28") + ")", text)
        self.assertIn(anchor("gone.txt", "L1"), text)  # 刪掉的檔案用舊檔的行號
        self.assertIn("-line3\n+LINE3", text)
        self.assertIn("新檔，+2 −0", text)
        self.assertIn("````diff\n+const b = 1;", text)  # 內容有 ``` → fence 自動加長

    def test_skeleton_with_pr_url(self):
        _, text = self.skeleton("--pr-url", "https://github.com/o/r/pull/9/")
        self.assertNotIn("{{PR_URL}}", text)
        self.assertIn("(https://github.com/o/r/pull/9/changes" + anchor("app.txt", "R3") + ")", text)

    def test_check_ok_after_filling(self):
        _, text = self.skeleton()
        code, res = self.run_cmd("check", self.fill(text))
        self.assertEqual((code, res["status"]), (0, "ok"), res)

    def test_check_reports_placeholders_missing_and_stale(self):
        _, text = self.skeleton()
        code, res = self.run_cmd("check", text)
        self.assertEqual((code, res["status"]), (1, "problems"))
        self.assertIn("{{一句話：這個檔案為什麼改}}", res["placeholders"])
        self.assertNotIn("{{PR_URL}}", res["placeholders"])  # PR_URL 留給 link 補

        filled = self.fill(text)
        _, res = self.run_cmd("check", filled.replace(anchor("app.txt", "L28"), ""))
        self.assertEqual(res["missing"], ["`app.txt` 原 L28（刪除）"])

        # 新的 commit 讓第一段往下移 → 舊的行號變成 stale，新的行號變成 missing
        (self.repo / "app.txt").write_text("new0\n" + (self.repo / "app.txt").read_text())
        sh(["git", "commit", "-qam", "shift"], self.repo)
        _, res = self.run_cmd("check", filled)
        self.assertIn("`app.txt` L1–4", res["missing"])
        self.assertIn(anchor("app.txt", "R3")[1:], res["stale"])

    def test_render_block_numbers_and_aligns(self):
        code, notes = bb_walkthrough.render_block([
            "- old()",
            "+ a = 1 ← 第一",
            "+   nested()",
            " …",
            "+ b ←  第二 `x` ",
        ])
        self.assertEqual(code, ["-     old()", "+ [1] a = 1", "+       nested()", " …", "+ [2] b"])
        self.assertEqual(notes, ["第一", "第二 `x`"])
        # 沒有說明的區塊不加編號欄
        self.assertEqual(bb_walkthrough.render_block(["+ 加了一段文件", " …"]), (["+ 加了一段文件", " …"], []))
        # 超過 9 個說明：編號欄變寬，沒編號的行一樣對齊
        code, _ = bb_walkthrough.render_block([f"+ s{i} ← n{i}" for i in range(10)] + ["+ tail"])
        self.assertEqual(code[0], "+ [1]  s0")
        self.assertEqual(code[9], "+ [10] s9")
        self.assertEqual(code[10], "+      tail")

    def test_render_text_quotes_notes_and_summary(self):
        text = ("<summary><b><code>a.js</code></b>　+1 −0　用 `x<y` 做事</summary>\n\n"
                "```diff\n+ a ← 說明\n```\n下一行\n")
        rendered, too_wide = bb_walkthrough.render_text(text)
        self.assertIn("用 <code>x&lt;y</code> 做事</summary>", rendered)
        self.assertIn("```diff\n+ [1] a\n```\n\n> **[1]** 說明\n\n下一行", rendered)  # 引用框後補空行
        self.assertEqual(too_wide, [])
        _, too_wide = bb_walkthrough.render_text("```diff\n+ " + "x" * 90 + " ← 太長\n```\n")
        self.assertEqual(len(too_wide), 1)

    def test_unsafe_snippets(self):
        bad = bb_walkthrough.unsafe_snippets("> **[1]** 通知 @someone、見 #12、key 是 <botId>\n")
        self.assertEqual(len(bad), 3, bad)
        ok = bb_walkthrough.unsafe_snippets(
            "##### 位置　[L3](https://x/pull/9/changes#diff-abcR3)\n"
            "> **[1]** `@someone`、第 12 點、`<botId>`、@我、a@b.com\n"
            "<details>\n<summary><b><code>@x</code></b></summary>\n</details>\n"
            "```diff\n+ #1 <tag> @user\n```\n")
        self.assertEqual(ok, [])

    def test_render_writes_only_when_ok(self):
        _, text = self.skeleton()
        out = self.tmp / "rendered.md"
        filled = self.fill(text).replace("+LINE3", "+LINE3 ← 改成大寫")
        code, res = self.run_cmd("render", filled, "--out", str(out))
        self.assertEqual((code, res["status"]), (0, "ok"), res)
        rendered = out.read_text(encoding="utf-8")
        self.assertIn("+ [1] LINE3", rendered)
        self.assertIn("> **[1]** 改成大寫", rendered)
        out.unlink()
        code, res = self.run_cmd("render", filled.replace("改成大寫", "見 #1"), "--out", str(out))
        self.assertEqual((code, res["status"]), (1, "problems"))
        self.assertTrue(res["unsafe"])
        self.assertFalse(out.exists())

    def test_unquote_path(self):
        self.assertEqual(bb_walkthrough.unquote_path('"a/\\346\\226\\207 \\"x\\".txt"'), 'a/文 "x".txt')
        self.assertEqual(bb_walkthrough.unquote_path("plain.txt"), "plain.txt")

    def test_link_replaces_placeholder_from_github_body(self):
        bindir = self.tmp / "bin"
        bindir.mkdir()
        (bindir / "gh").write_text(FAKE_GH_PR)
        (bindir / "gh").chmod(0o755)
        state = self.tmp / "pr.json"
        # 內文是從 GitHub 讀回來的：--attach 上傳後的圖片網址要原樣保留
        body = "![after](https://github.com/user-attachments/assets/x)\n[L3]({{PR_URL}}/changes#diff-1R3)"
        state.write_text(json.dumps({"body": body, "url": "https://github.com/o/r/pull/9"}))
        self.env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_GH_STATE": str(state)}
        out = json.loads(self.run_script("bb_walkthrough.py", "link", "--pr", "9").stdout)
        self.assertEqual((out["status"], out["replaced"]), ("ok", 1))
        data = json.loads(state.read_text())
        self.assertEqual(data["body"], body.replace("{{PR_URL}}", "https://github.com/o/r/pull/9"))
        # 沒有佔位符就不寫回
        out = json.loads(self.run_script("bb_walkthrough.py", "link", "--pr", "9").stdout)
        self.assertEqual(out["replaced"], 0)
        self.assertEqual(json.loads(state.read_text())["edits"], 1)


if __name__ == "__main__":
    unittest.main()


FAKE_GH_MULTI = textwrap.dedent("""\
    #!/usr/bin/env python3
    # 假的 gh：兩個帳號 company-user（email 只能從 /user/emails 查到）與 personal-user（公開 email）
    import json, os, sys
    args = sys.argv[1:]
    tokens = {"company-user": "tok-company", "personal-user": "tok-personal"}
    who = {v: k for k, v in tokens.items()}.get(os.environ.get("GH_TOKEN", ""), "personal-user")  # active=personal
    log = os.environ.get("FAKE_GH_LOG")
    if log:
        open(log, "a").write(json.dumps({"args": args, "as": who}) + "\\n")
    if args[:2] == ["auth", "status"]:
        print(json.dumps({"hosts": {"github.com": [{"login": "personal-user", "active": True},
                                                   {"login": "company-user", "active": False}]}}))
    elif args[:2] == ["auth", "token"]:
        u = args[args.index("--user") + 1]
        if u not in tokens:
            print("no account", file=sys.stderr); sys.exit(1)
        print(tokens[u])
    elif args[:2] == ["api", "user"]:
        print({"company-user": "", "personal-user": "me@gmail.com"}[who] if "--jq" in args else "{}")
    elif args[:2] == ["api", "user/emails"]:
        print({"company-user": "hank@corp.example", "personal-user": "me@gmail.com"}[who])
    else:
        print("AS=" + who)
    """)


class TestGhAccount(RepoFixture):
    def setUp(self):
        super().setUp()
        bindir = self.tmp / "bin"
        bindir.mkdir()
        (bindir / "gh").write_text(FAKE_GH_MULTI)
        (bindir / "gh").chmod(0o755)
        self.log = self.tmp / "gh.log"
        self.env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "XDG_CACHE_HOME": str(self.tmp / "cache"),
                    "FAKE_GH_LOG": str(self.log)}
        self.env.pop("GH_TOKEN", None)
        self.env.pop("GITHUB_TOKEN", None)
        self.bbgh = str(ROOT / "bin" / "bb-gh")

    def bb_gh(self, *args, check=True):
        return sh([self.bbgh, *args], self.repo, env=self.env, check=check)

    def test_unset_passes_through_to_active(self):
        self.assertEqual(self.bb_gh("pr", "list").stdout.strip(), "AS=personal-user")

    def test_username(self):
        sh(["git", "config", "github.user", "company-user"], self.repo)
        self.assertEqual(self.bb_gh("pr", "list").stdout.strip(), "AS=company-user")

    def test_email_resolution_and_cache(self):
        sh(["git", "config", "github.user", "HANK@corp.example"], self.repo)
        self.assertEqual(self.bb_gh("pr", "list").stdout.strip(), "AS=company-user")
        cache = json.loads((self.tmp / "cache" / "bombolt" / "gh-account-emails.json").read_text())
        self.assertIn("hank@corp.example", cache["company-user"])
        # 第二次走快取：不再呼叫 api user/emails
        self.log.write_text("")
        self.assertEqual(self.bb_gh("issue", "list").stdout.strip(), "AS=company-user")
        self.assertNotIn("user/emails", self.log.read_text())

    def test_unknown_account_fails_loudly(self):
        sh(["git", "config", "github.user", "someone-else"], self.repo)
        p = self.bb_gh("pr", "list", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("someone-else", p.stderr)
        self.assertNotIn("AS=", p.stdout)  # 絕不退回 active 帳號
        sh(["git", "config", "github.user", "nobody@nowhere.example"], self.repo)
        p = self.bb_gh("pr", "list", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn("AS=", p.stdout)

    def test_includeif_folder_mapping(self):
        # 模擬使用者的做法：某個資料夾底下的 repo 透過 includeIf 套用另一份 config
        extra = self.tmp / "gitconfig-company"
        extra.write_text("[github]\n\tuser = company-user\n")
        with open(self.global_gitconfig, "a") as f:
            f.write(f'[github]\n\tuser = personal-user\n[includeIf "gitdir:{self.tmp}/repo/"]\n\tpath = {extra}\n')
        self.assertEqual(self.bb_gh("pr", "list").stdout.strip(), "AS=company-user")
        wt = self.create_wt()["path"]  # worktree 也要吃到同一個 includeIf
        p = sh([self.bbgh, "pr", "list"], wt, env=self.env)
        self.assertEqual(p.stdout.strip(), "AS=company-user")
        outside = Path(tempfile.mkdtemp()).resolve()
        try:
            sh(["git", "init", "-q"], outside)
            p = sh([self.bbgh, "pr", "list"], outside, env=self.env)
            self.assertEqual(p.stdout.strip(), "AS=personal-user")
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_guard_blocks_plain_gh_only_when_configured(self):
        cmd = {"cwd": str(self.repo), "tool_name": "Bash", "tool_input": {"command": "gh pr create --title x"}}
        self.assertIsNone(bb_guard.evaluate(cmd))  # 沒設定：不介入
        sh(["git", "config", "github.user", "company-user"], self.repo)
        self.assertIn("bb-gh", bb_guard.evaluate(cmd))
        cmd["tool_input"]["command"] = "cd x && gh issue view 3"
        self.assertIsNotNone(bb_guard.evaluate(cmd))
        for ok in ("bb-gh pr create --title x", "git log", "echo gh is great",
                   r'grep -n "gh auth\|gh 沒" skills/*/SKILL.md', "grep -e 'x|gh pr' notes.md", 'echo "a; gh pr create"'):
            cmd["tool_input"]["command"] = ok
            self.assertIsNone(bb_guard.evaluate(cmd), ok)
        # 引號外的分隔符號照樣切：真的 gh 指令還是擋
        for bad in ('echo "a|b" | gh pr create --body-file -', 'grep "x" notes.md; gh pr view', "echo hi\ngh pr view 3"):
            cmd["tool_input"]["command"] = bad
            self.assertIsNotNone(bb_guard.evaluate(cmd), bad)
        # bb-gh 也逃不過不可逆操作的禁令（在 bombolt worktree 裡）
        wt = self.create_wt()["path"]
        self.assertIsNotNone(bb_guard.evaluate({"cwd": wt, "tool_name": "Bash",
                                                "tool_input": {"command": "bb-gh pr merge 3"}}))


class TestIntegrate(RepoFixture):
    def setUp(self):
        super().setUp()
        cfg = (self.repo / ".claude" / "bombolt.md").read_text().replace(
            "protected_branches: [dev, staging]", "protected_branches: [staging]\nintegration_branches: [dev]")
        (self.repo / ".claude" / "bombolt.md").write_text(cfg)
        sh(["git", "push", "-q", "origin", "main:dev"], self.repo)
        self.info = self.create_wt()
        self.wt = Path(self.info["path"])

    def commit_in(self, path, fname, content, msg):
        (Path(path) / fname).write_text(content)
        sh(["git", "add", "-A"], path)
        sh(["git", "commit", "-qm", msg], path)

    def other_pushes_dev(self, fname, content):
        other = self.tmp / "other"
        if not other.exists():
            sh(["git", "clone", "-q", "-b", "dev", str(self.origin), str(other)], self.tmp)
        sh(["git", "pull", "-q", "origin", "dev"], other)
        (other / fname).write_text(content)
        sh(["git", "add", "-A"], other)
        sh(["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "other work"], other)
        sh(["git", "push", "-q", "origin", "HEAD:dev"], other)

    def integrate(self, cmd, target="dev", check=True):
        p = self.run_script("bb_integrate.py", cmd, "--target", target, cwd=self.wt, check=check)
        return json.loads(p.stdout.strip().splitlines()[-1])

    def test_clean_merge_is_no_ff_and_pushed(self):
        self.other_pushes_dev("theirs.txt", "x\n")  # dev 上有別人的改動
        self.commit_in(self.wt, "feature.txt", "f\n", "feat")
        r = self.integrate("start")
        self.assertEqual(r["status"], "merged")
        dev = sh(["git", "rev-parse", "dev"], self.origin).stdout.strip()
        self.assertEqual(dev, r["merge_commit"])
        parents = sh(["git", "rev-list", "--parents", "-n1", dev], self.origin).stdout.split()
        self.assertEqual(len(parents), 3)  # merge commit（--no-ff）
        self.assertIn("theirs.txt", sh(["git", "ls-tree", "--name-only", dev], self.origin).stdout)
        self.assertNotIn("_integrate", sh(["git", "worktree", "list"], self.repo).stdout)
        # feature worktree 不受影響
        self.assertEqual(sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], self.wt).stdout.strip(), self.info["branch"])

    def test_fast_forwardable_still_creates_merge_commit(self):
        self.commit_in(self.wt, "feature.txt", "f\n", "feat")
        r = self.integrate("start")
        parents = sh(["git", "rev-list", "--parents", "-n1", r["merge_commit"]], self.origin).stdout.split()
        self.assertEqual(len(parents), 3)

    def test_conflict_then_finish(self):
        self.other_pushes_dev("app.txt", "theirs\n")
        self.commit_in(self.wt, "app.txt", "mine\n", "feat")
        r = self.integrate("start")
        self.assertEqual(r["status"], "conflict")
        self.assertEqual(r["files"], ["app.txt"])
        tmp = Path(r["path"])
        # 守門：暫存 worktree 裡只能 push 到 dev
        g = lambda c: bb_guard.evaluate({"cwd": str(tmp), "tool_name": "Bash", "tool_input": {"command": c}})
        self.assertIsNone(g("git push origin HEAD:dev"))
        self.assertIsNotNone(g("git push origin HEAD:main"))
        self.assertIsNotNone(g("git push --force origin HEAD:dev"))
        # 還沒解就 finish → 拒絕
        self.assertEqual(self.integrate("finish", check=False)["status"], "conflict")
        # 解了但留著標記 → 拒絕
        (tmp / "app.txt").write_text("<<<<<<< HEAD\ntheirs\n=======\nmine\n>>>>>>> x\n")
        sh(["git", "add", "app.txt"], tmp)
        self.assertEqual(self.integrate("finish", check=False)["status"], "conflict")
        (tmp / "app.txt").write_text("theirs\nmine\n")
        sh(["git", "add", "app.txt"], tmp)
        r = self.integrate("finish")
        self.assertEqual(r["status"], "merged")
        self.assertEqual(sh(["git", "show", "dev:app.txt"], self.origin).stdout, "theirs\nmine\n")
        self.assertFalse(tmp.exists())

    def test_abort_leaves_dev_untouched(self):
        self.other_pushes_dev("app.txt", "theirs\n")
        before = sh(["git", "rev-parse", "dev"], self.origin).stdout
        self.commit_in(self.wt, "app.txt", "mine\n", "feat")
        self.assertEqual(self.integrate("start")["status"], "conflict")
        self.assertEqual(self.integrate("abort")["status"], "aborted")
        self.assertEqual(sh(["git", "rev-parse", "dev"], self.origin).stdout, before)

    def test_target_must_be_configured(self):
        r = self.integrate("start", target="prod", check=False)
        self.assertEqual(r["status"], "error")
        self.assertEqual(sh(["git", "rev-parse", "prod"], self.origin).stdout,
                         sh(["git", "rev-parse", "origin/prod"], self.repo).stdout)


class TestGuardRepoSecretEnv(RepoFixture):
    """專案的 secret_paths 可以擋特定的 .env 檔，而且用路徑錨定，不會誤擋同名的其他檔。"""

    def setUp(self):
        super().setUp()
        cfg = (self.repo / ".claude" / "bombolt.md").read_text().replace(
            '  - "*-log.txt"', '  - "*-log.txt"\n  - backend/.env.prod\n  - /.env.local')
        assert "backend/.env.prod" in cfg
        (self.repo / ".claude" / "bombolt.md").write_text(cfg)
        self.wt = self.create_wt()["path"]

    def file(self, path):
        return bb_guard.evaluate({"cwd": self.wt, "tool_name": "Read", "tool_input": {"file_path": path}})

    def bash(self, c):
        return bb_guard.evaluate({"cwd": self.wt, "tool_name": "Bash", "tool_input": {"command": c}})

    def test_anchored_env_rules(self):
        self.assertIsNotNone(self.file("backend/.env.prod"))
        self.assertIsNotNone(self.file(str(self.repo / "backend" / ".env.prod")))  # 主 checkout 的那份也擋
        self.assertIsNotNone(self.file(".env.local"))                              # 根目錄的 .env.local
        self.assertIsNotNone(self.bash("cat backend/.env.prod"))
        self.assertIsNone(self.file("backend/.env.local"))                         # 同名但不同層：放行
        self.assertIsNone(self.file("backend/.env.dev"))
        self.assertIsNone(self.bash("cat backend/.env.local"))


class TestResync(RepoFixture):
    """bb_resync.py：pr_base（release）跟 base_branch（prod）不同時，發版後把還沒上版的
    feature branch 追上最新的 prod，並重新整合進 dev。"""

    def setUp(self):
        super().setUp()
        cfg = (self.repo / ".claude" / "bombolt.md").read_text().replace(
            "protected_branches: [dev, staging]",
            "protected_branches: [staging]\npr_base: release\nintegration_branches: [dev]")
        (self.repo / ".claude" / "bombolt.md").write_text(cfg)
        sh(["git", "push", "-q", "origin", "main:dev"], self.repo)
        sh(["git", "push", "-q", "origin", "main:release"], self.repo)
        bindir = self.tmp / "bin"
        bindir.mkdir()
        (bindir / "gh").write_text(FAKE_GH)
        (bindir / "gh").chmod(0o755)
        self.gh_data = self.tmp / "gh.json"
        self.gh_data.write_text("{}")
        self.env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_GH_DATA": str(self.gh_data)}
        self.info = self.create_wt()  # issue 12, branch bb-12-bookings-default-month
        self.wt = Path(self.info["path"])
        (self.wt / "feature.txt").write_text("feat\n")
        sh(["git", "add", "-A"], self.wt)
        sh(["git", "commit", "-qm", "feat work"], self.wt)
        sh(["git", "push", "-q", "-u", "origin", self.info["branch"]], self.wt)

    def set_prs(self, mapping):
        self.gh_data.write_text(json.dumps(mapping))

    def push_to_prod(self, fname, content, msg="prod update"):
        other = self.tmp / "other-prod"
        if not other.exists():
            sh(["git", "clone", "-q", "-b", "prod", str(self.origin), str(other)], self.tmp)
        sh(["git", "pull", "-q", "origin", "prod"], other)
        (other / fname).write_text(content)
        sh(["git", "add", "-A"], other)
        sh(["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", msg], other)
        sh(["git", "push", "-q", "origin", "HEAD:prod"], other)

    def resync(self, *args, check=True):
        p = self.run_script("bb_resync.py", *args, check=check)
        return json.loads(p.stdout.strip().splitlines()[-1])

    def test_list_only_shows_open_prs_against_pr_base(self):
        self.set_prs({self.info["branch"]: [{"number": 9, "url": "u"}]})
        out = self.resync("list")
        self.assertEqual(out["base_branch"], "prod")
        self.assertEqual(out["pr_base"], "release")
        self.assertEqual(len(out["candidates"]), 1)
        self.assertEqual(out["candidates"][0]["branch"], self.info["branch"])
        # 沒有開向 pr_base 的 PR：不列入
        self.set_prs({})
        out = self.resync("list")
        self.assertEqual(out["candidates"], [])

    def test_clean_resync_pulls_prod_and_reintegrates_dev(self):
        self.push_to_prod("prodfile.txt", "from prod\n")
        r = self.resync("start", "--path", str(self.wt))
        self.assertEqual(r["status"], "merged")
        self.assertEqual(r["reintegrated"]["dev"]["status"], "merged")
        # feature branch 現在同時有自己的與 prod 的新內容
        self.assertEqual(sh(["git", "show", f"origin/{self.info['branch']}:prodfile.txt"], self.repo).stdout,
                         "from prod\n")
        self.assertEqual(sh(["git", "show", "origin/dev:feature.txt"], self.repo).stdout, "feat\n")
        self.assertEqual(sh(["git", "show", "origin/dev:prodfile.txt"], self.repo).stdout, "from prod\n")

    def test_conflict_then_finish(self):
        sh(["git", "-C", str(self.wt), "rm", "-q", "--ignore-unmatch", "nonexist"], self.repo, check=False)
        (self.wt / "app.txt").write_text("mine\n")
        sh(["git", "add", "-A"], self.wt)
        sh(["git", "commit", "-qm", "mine"], self.wt)
        sh(["git", "push", "-q", "origin", f"HEAD:{self.info['branch']}"], self.wt)
        self.push_to_prod("app.txt", "theirs\n")

        r = self.resync("start", "--path", str(self.wt))
        self.assertEqual(r["status"], "conflict")
        self.assertEqual(r["files"], ["app.txt"])

        # 還沒 add 就 finish：拒絕
        self.assertEqual(self.resync("finish", "--path", str(self.wt), check=False)["status"], "conflict")

        (self.wt / "app.txt").write_text("theirs\nmine\n")
        sh(["git", "add", "app.txt"], self.wt)
        r = self.resync("finish", "--path", str(self.wt))
        self.assertEqual(r["status"], "merged")
        self.assertTrue(r["resolved_conflicts"])
        self.assertEqual(r["reintegrated"]["dev"]["status"], "merged")
        self.assertEqual(sh(["git", "show", f"origin/{self.info['branch']}:app.txt"], self.repo).stdout,
                         "theirs\nmine\n")
        self.assertEqual(sh(["git", "show", "origin/dev:app.txt"], self.repo).stdout, "theirs\nmine\n")
        parents = sh(["git", "rev-list", "--parents", "-n1", f"origin/{self.info['branch']}"], self.repo).stdout.split()
        self.assertEqual(len(parents), 3)  # merge commit：自己的 tip ＋ origin/prod 的新 tip

    def test_abort_leaves_branch_and_dev_untouched(self):
        (self.wt / "app.txt").write_text("mine\n")
        sh(["git", "add", "-A"], self.wt)
        sh(["git", "commit", "-qm", "mine"], self.wt)
        sh(["git", "push", "-q", "origin", f"HEAD:{self.info['branch']}"], self.wt)
        self.push_to_prod("app.txt", "theirs\n")
        before_branch = sh(["git", "rev-parse", f"origin/{self.info['branch']}"], self.repo).stdout
        before_dev = sh(["git", "rev-parse", "origin/dev"], self.repo).stdout

        self.assertEqual(self.resync("start", "--path", str(self.wt))["status"], "conflict")
        self.assertEqual(self.resync("abort", "--path", str(self.wt))["status"], "aborted")

        self.assertEqual(sh(["git", "status", "--porcelain"], self.wt).stdout.strip(), "")
        self.assertEqual(sh(["git", "rev-parse", f"origin/{self.info['branch']}"], self.repo).stdout, before_branch)
        self.assertEqual(sh(["git", "rev-parse", "origin/dev"], self.repo).stdout, before_dev)

    def test_check_reports_behind_and_integration(self):
        r = self.resync("check", "--path", str(self.wt))
        self.assertTrue(r["applicable"])
        self.assertEqual(r["behind"], 0)
        self.assertEqual(r["integrated"], {"dev": False})
        self.push_to_prod("prodfile.txt", "from prod\n")
        self.push_to_prod("prodfile2.txt", "again\n")
        r = self.resync("check", "--path", str(self.wt))
        self.assertEqual(r["behind"], 2)
        # check 是唯讀的：branch 與 dev 都沒動
        self.assertEqual(sh(["git", "status", "--porcelain"], self.wt).stdout.strip(), "")
        self.resync("start", "--path", str(self.wt))
        r = self.resync("check", "--path", str(self.wt))
        self.assertEqual(r["behind"], 0)
        self.assertEqual(r["integrated"], {"dev": True})

    def test_refuses_when_worktree_dirty(self):
        (self.wt / "wip.txt").write_text("wip\n")
        r = self.resync("start", "--path", str(self.wt), check=False)
        self.assertEqual(r["status"], "error")
        self.assertIn("未 commit", r["message"])

    def test_needs_sync(self):
        # 還沒整合進 dev → 需要同步
        r = self.resync("check", "--path", str(self.wt))
        self.assertTrue(r["needs_sync"])
        self.assertIn("dev", r["reasons"][0])
        # draft 本來就不整合：只看落後
        self.assertFalse(self.resync("check", "--path", str(self.wt), "--draft")["needs_sync"])
        self.push_to_prod("prodfile.txt", "from prod\n")
        r = self.resync("check", "--path", str(self.wt), "--draft")
        self.assertTrue(r["needs_sync"])
        self.assertIn("落後", r["reasons"][0])
        self.resync("start", "--path", str(self.wt))
        self.assertFalse(self.resync("check", "--path", str(self.wt))["needs_sync"])

    def test_needs_sync_when_pr_base_is_base_branch(self):
        # pr_base == base_branch：落後是正常的（GitHub 會處理），只有「不在 dev 裡了」才要同步
        cfg = (self.repo / ".claude" / "bombolt.md").read_text().replace("pr_base: release\n", "")
        (self.repo / ".claude" / "bombolt.md").write_text(cfg)
        self.run_script("bb_worktree.py", "sync-config", "--path", str(self.wt))
        self.push_to_prod("prodfile.txt", "from prod\n")
        r = self.resync("check", "--path", str(self.wt))
        self.assertFalse(r["applicable"])
        self.assertEqual(r["behind"], 1)
        self.assertTrue(r["needs_sync"])  # 不在 dev 裡
        self.assertEqual(len(r["reasons"]), 1)
        self.resync("start", "--path", str(self.wt))
        self.push_to_prod("prodfile2.txt", "again\n")
        r = self.resync("check", "--path", str(self.wt))
        self.assertEqual(r["behind"], 1)
        self.assertFalse(r["needs_sync"])

    def test_list_reports_sync_state_and_draft(self):
        self.set_prs({self.info["branch"]: [{"number": 9, "url": "u", "isDraft": True}]})
        c = self.resync("list")["candidates"][0]
        self.assertTrue(c["draft"])
        self.assertFalse(c["needs_sync"])  # draft：不在 dev 裡是正常的
        self.set_prs({self.info["branch"]: [{"number": 9, "url": "u", "isDraft": False}]})
        c = self.resync("list")["candidates"][0]
        self.assertTrue(c["needs_sync"])

    def test_no_integrate_leaves_dev_alone(self):
        self.push_to_prod("prodfile.txt", "from prod\n")
        before_dev = sh(["git", "rev-parse", "origin/dev"], self.repo).stdout
        r = self.resync("start", "--path", str(self.wt), "--no-integrate")
        self.assertEqual((r["status"], r["reintegrated"]), ("merged", {}))
        sh(["git", "fetch", "-q", "origin"], self.repo)
        self.assertEqual(sh(["git", "rev-parse", "origin/dev"], self.repo).stdout, before_dev)
        self.assertEqual(sh(["git", "show", f"origin/{self.info['branch']}:prodfile.txt"], self.repo).stdout,
                         "from prod\n")

    def test_context_tells_which_worktree_a_session_owns(self):
        # resume 沒有自動回到 worktree 時，bb-fix 要知道這個 session 屬於哪個 worktree（不能當成「同步全部」）
        self.assertEqual(bb_lib.worktree_of_session(self.repo, "sess-1"), self.wt)
        self.assertIsNone(bb_lib.worktree_of_session(self.repo, "other"))
        out = self.run_script("bb_context.py", "--session-id", "sess-1").stdout
        self.assertIn(f"worktree `{self.wt}` 的實作 session", out)
        out = self.run_script("bb_context.py", "--session-id", "other").stdout
        self.assertNotIn("的實作 session", out)


class TestIssuesList(RepoFixture):
    """bb_issues.py list：列出 open 的 bombolt issue，標出本機有沒有 worktree（已認領）。"""

    def setUp(self):
        super().setUp()
        bindir = self.tmp / "bin"
        bindir.mkdir()
        (bindir / "gh").write_text(FAKE_GH)
        (bindir / "gh").chmod(0o755)
        self.gh_data = self.tmp / "gh.json"
        self.gh_data.write_text("{}")
        self.env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_GH_DATA": str(self.gh_data)}

    def set_issues(self, issues):
        self.gh_data.write_text(json.dumps({"_issues": issues}))

    def test_marks_local_worktree_assigned_blocked(self):
        self.create_wt(issue=12, slug="bookings-default-month", sid="s")
        self.set_issues([
            {"number": 12, "title": "A", "url": "https://x/12", "assignees": [{"login": "me"}],
             "labels": [{"name": "bombolt"}]},
            {"number": 13, "title": "B", "url": "https://x/13", "assignees": [],
             "labels": [{"name": "bombolt"}, {"name": "bombolt:blocked"}]},
            {"number": 14, "title": "C", "url": "https://x/14", "assignees": [], "labels": [{"name": "bombolt"}]},
        ])
        out = json.loads(self.run_script("bb_issues.py", "list").stdout)
        self.assertEqual(out["status"], "ok")
        by_n = {i["number"]: i for i in out["issues"]}
        self.assertEqual(by_n[12]["local_worktree"], "bb-12-bookings-default-month")
        self.assertTrue(by_n[12]["assigned"])
        self.assertFalse(by_n[12]["blocked"])
        self.assertIsNone(by_n[13]["local_worktree"])
        self.assertTrue(by_n[13]["blocked"])
        self.assertIsNone(by_n[14]["local_worktree"])
        self.assertFalse(by_n[14]["blocked"])

    def test_reports_fixing(self):
        branch = "bb-15-footer"
        fixing = status_comment({"state": "fixing", "owner": OTHER, "branch": branch, "since": "2026-09-01",
                                 "session_id": "x", "pr": 7, "history": []}, issue=15)
        self.gh_data.write_text(json.dumps({
            "_issues": [{"number": 15, "title": "D", "url": "https://x/15", "assignees": [],
                         "labels": [{"name": "bombolt"}], "comments": [fixing]}],
            branch: [{"number": 7, "state": "OPEN", "url": "https://x/pull/7"}],
        }))
        out = json.loads(self.run_script("bb_issues.py", "list").stdout)
        issue = out["issues"][0]
        self.assertEqual((issue["verdict"], issue["fixing"]), ("in_review", True))


ME = {"name": "t", "github": "tester", "machine": "test-mac"}
OTHER = {"name": "Amy", "github": "amy-gh", "machine": "Amy 的 MacBook"}


def status_comment(data, cid=500, issue=3):
    return {"url": f"https://github.com/o/r/issues/{issue}#issuecomment-{cid}",
            "body": bb_issues.render_status(data)}


def bb_issue(n, slug, comments=None, state="OPEN", labels=("bombolt",)):
    meta = json.dumps({"base": "prod", "slug": slug})
    return {"number": n, "title": f"issue {n}", "url": f"https://x/{n}", "state": state,
            "body": f"內文\n\n<!-- bombolt:meta {meta} -->", "comments": comments or [],
            "assignees": [], "labels": [{"name": l} for l in labels]}


class TestOwner(RepoFixture):
    def test_machine_name_from_git_config(self):
        self.assertEqual(bb_lib.machine_name(self.repo), "test-mac")

    def test_owner_text(self):
        self.assertEqual(bb_lib.owner_text({"name": "Hank", "github": "hank-gh", "machine": "Hank’s Mac"}),
                         "**Hank**（`hank-gh`）的電腦「Hank’s Mac」")
        self.assertEqual(bb_lib.owner_text({"github": "hank-gh", "machine": "m"}), "`hank-gh`的電腦「m」")
        self.assertEqual(bb_lib.owner_text({"name": "Hank"}), "**Hank**的電腦")
        # 會被當成 markdown／HTML 的字元拿掉
        self.assertEqual(bb_lib.owner_text({"name": "<b>@x", "machine": "a|b"}), "**bx**的電腦「ab」")
        self.assertEqual(bb_lib.owner_short({"github": "hank-gh"}), "hank-gh")

    def test_same_owner_needs_same_machine(self):
        self.assertTrue(bb_lib.same_owner(ME, dict(ME)))
        self.assertTrue(bb_lib.same_owner(ME, {**ME, "github": "TESTER"}))
        self.assertFalse(bb_lib.same_owner(ME, {**ME, "machine": "other"}))
        self.assertFalse(bb_lib.same_owner(ME, {**ME, "github": "someone"}))
        self.assertFalse(bb_lib.same_owner({"github": "tester"}, ME))  # 不知道哪台電腦就不算

    def test_info_has_owner(self):
        info = self.create_wt()
        out = json.loads(self.run_script("bb_worktree.py", "info", cwd=info["path"]).stdout)
        self.assertEqual(out["owner"], ME)
        self.assertEqual(out["owner_text"], "**t**（`tester`）的電腦「test-mac」")
        self.assertEqual(out["owner_short"], "t")


class TestIssueStatus(RepoFixture):
    """bb_issues.py：issue 上的狀態留言（原地更新）和「這個 issue 現在能不能做」的判斷。"""

    def setUp(self):
        super().setUp()
        bindir = self.tmp / "bin"
        bindir.mkdir()
        (bindir / "gh").write_text(FAKE_GH)
        (bindir / "gh").chmod(0o755)
        self.gh_data = self.tmp / "gh.json"
        self.gh_data.write_text("{}")
        self.env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_GH_DATA": str(self.gh_data)}

    def data(self):
        return json.loads(self.gh_data.read_text())

    def set_data(self, d):
        self.gh_data.write_text(json.dumps(d, ensure_ascii=False))

    def update_data(self, **kw):
        d = self.data()
        d.update(kw)
        self.set_data(d)

    def issues(self, *args):
        return json.loads(self.run_script("bb_issues.py", *args).stdout)

    def pr(self, n, state, head="h" * 40, author="tester"):
        return {"number": n, "state": state, "url": f"https://github.com/o/r/pull/{n}",
                "headRefOid": head, "author": {"login": author}}

    def test_evaluate(self):
        ev = bb_issues.evaluate
        working = {"state": "working", "owner": OTHER, "since": "2026-09-01"}
        self.assertEqual(ev(3, "OPEN", None, [], ME)["verdict"], "free")
        self.assertEqual(ev(3, "OPEN", {**working, "owner": ME}, [], ME)["verdict"], "mine")
        r = ev(3, "OPEN", working, [], ME)
        self.assertEqual(r["verdict"], "working")
        self.assertIn("Amy 的 MacBook", r["message"])
        self.assertIn("2026-09-01", r["message"])
        self.assertEqual(ev(3, "OPEN", {**working, "state": "blocked"}, [], ME)["verdict"], "blocked")
        self.assertEqual(ev(3, "OPEN", None, [], ME, blocked_label=True)["verdict"], "blocked")
        # 有開著的 PR：沒有狀態留言時只知道 PR 是誰開的
        r = ev(3, "OPEN", None, [self.pr(7, "OPEN", author="amy-gh")], ME)
        self.assertEqual(r["verdict"], "in_review")
        self.assertEqual(r["owner"], {"github": "amy-gh"})
        self.assertIn("bb-fix", r["message"])
        r = ev(3, "OPEN", {"state": "in_review", "owner": ME, "pr": 7}, [self.pr(7, "OPEN")], ME)
        self.assertTrue(r["mine"])
        self.assertIn("這台電腦", r["message"])
        # 只有被關掉的 PR → 可以重做；重做到一半（狀態是自己在做）→ 接著做
        r = ev(3, "OPEN", {"state": "in_review", "owner": ME, "pr": 7}, [self.pr(7, "CLOSED")], ME)
        self.assertEqual(r["verdict"], "redo")
        self.assertEqual([p["number"] for p in r["closed_prs"]], [7])
        self.assertEqual(ev(3, "OPEN", {**working, "owner": ME}, [self.pr(7, "CLOSED")], ME)["verdict"], "mine")
        self.assertEqual(ev(3, "OPEN", None, [self.pr(7, "CLOSED"), self.pr(9, "MERGED")], ME)["verdict"], "merged")
        self.assertEqual(ev(3, "CLOSED", None, [], ME)["verdict"], "closed")

    def test_render_and_parse_roundtrip(self):
        data = {"state": "in_review", "owner": {"name": "a-->b", "github": "g", "machine": "m"},
                "branch": "bb-3-x", "session_id": "s", "since": "2026-09-26", "pr": 7, "history": ["x"]}
        body = bb_issues.render_status(data)
        self.assertIn("🔵 審查中** — PR #7", body)
        self.assertEqual(body.count("-->"), 1)  # 名字裡的 --> 不會提早結束 HTML 註解
        parsed, cid = bb_issues.find_status([{"url": "https://x#issuecomment-42", "body": body}])
        self.assertEqual(parsed, data)
        self.assertEqual(cid, "42")

    def test_mark_updates_one_comment_in_place(self):
        self.set_data({"_issue:3": bb_issue(3, "x")})
        r = self.issues("mark", "--issue", "3", "--event", "claim", "--branch", "bb-3-x", "--session-id", "s1")
        self.assertEqual((r["status"], r["state"]), ("ok", "working"))
        comments = self.data()["_issue:3"]["comments"]
        self.assertEqual(len(comments), 1)
        self.assertIn("🟡 實作中", comments[0]["body"])
        self.assertIn("**t**（`tester`）的電腦「test-mac」", comments[0]["body"])
        self.assertIn("`bb-3-x`", comments[0]["body"])
        self.assertIn(["3", "--add-assignee", "@me"], self.data()["_edits"])

        self.issues("mark", "--issue", "3", "--event", "pr", "--pr", "https://github.com/o/r/pull/7")
        comments = self.data()["_issue:3"]["comments"]
        self.assertEqual(len(comments), 1)  # 原地改寫，不是新增
        data, _ = bb_issues.find_status(comments)
        self.assertEqual((data["state"], data["pr"], data["session_id"]), ("in_review", 7, "s1"))
        self.assertEqual(len(data["history"]), 2)
        self.assertIn("發 PR #7", data["history"][1])

        # PR 被關掉 → status 判斷為可重做 → 重做之後狀態回到實作中，紀錄留著
        self.update_data(**{"bb-3-x": [self.pr(7, "CLOSED")]})
        st = self.issues("status", "--issue", "3")
        self.assertEqual((st["verdict"], st["branch"]), ("redo", "bb-3-x"))
        self.issues("mark", "--issue", "3", "--event", "redo", "--branch", "bb-3-x", "--session-id", "s2",
                    "--closed-prs", "7")
        data, _ = bb_issues.find_status(self.data()["_issue:3"]["comments"])
        self.assertEqual((data["state"], data["session_id"], data["pr"]), ("working", "s2", None))
        self.assertEqual(len(data["history"]), 3)
        self.assertIn("PR #7 已關閉（沒有 merge），重新實作", data["history"][2])
        self.assertEqual(self.issues("status", "--issue", "3")["verdict"], "mine")

    def test_fixing_and_fixed(self):
        # bb-fix 開工：狀態改成「修改中」（只是提醒，不擋）；做完一輪回到「審查中」，PR 編號留著
        self.set_data({"_issue:3": bb_issue(3, "x"), "bb-3-x": [self.pr(7, "OPEN")]})
        self.issues("mark", "--issue", "3", "--event", "claim", "--branch", "bb-3-x", "--session-id", "s1")
        self.issues("mark", "--issue", "3", "--event", "pr", "--pr", "7")
        self.issues("mark", "--issue", "3", "--event", "fixing", "--pr", "7", "--session-id", "s2")
        comments = self.data()["_issue:3"]["comments"]
        self.assertEqual(len(comments), 1)
        self.assertIn("🟠 修改中** — PR #7", comments[0]["body"])
        data, _ = bb_issues.find_status(comments)
        self.assertEqual((data["state"], data["pr"], data["session_id"]), ("fixing", 7, "s2"))
        self.assertIn("開始修改 PR #7", data["history"][-1])
        st = self.issues("status", "--issue", "3")
        self.assertEqual((st["verdict"], st["fixing"]), ("in_review", True))  # bb-work 一樣停下來
        self.assertIn("開始在修改", st["message"])

        self.issues("mark", "--issue", "3", "--event", "fixed", "--pr", "7")
        data, _ = bb_issues.find_status(self.data()["_issue:3"]["comments"])
        self.assertEqual((data["state"], data["pr"]), ("in_review", 7))
        self.assertIn("修改完成", data["history"][-1])
        st = self.issues("status", "--issue", "3")
        self.assertEqual((st["verdict"], st["fixing"]), ("in_review", False))
        self.assertIn("最近一次在這台電腦上修改", st["message"])
        self.assertIn("/bombolt:bb-fix 7", st["message"])
        body = self.data()["_issue:3"]["comments"][0]["body"]
        self.assertIn("任何一台電腦都能接著改", body)
        self.assertNotIn("resume", body)

    def test_patch_failure_adds_new_comment(self):
        old = status_comment({"state": "working", "owner": OTHER, "branch": "bb-3-x", "since": "d", "history": []})
        self.set_data({"_issue:3": bb_issue(3, "x", comments=[old]), "_patch_fails": True})
        self.issues("mark", "--issue", "3", "--event", "takeover", "--branch", "bb-3-x")
        comments = self.data()["_issue:3"]["comments"]
        self.assertEqual(len(comments), 2)
        data, _ = bb_issues.find_status(comments)  # 讀最新的那一則
        self.assertEqual(data["owner"], ME)
        self.assertIn("接手實作（原本是 Amy）", data["history"][-1])

    def test_merged_without_status_comment_is_skipped(self):
        self.set_data({"_issue:3": bb_issue(3, "x")})
        self.assertEqual(self.issues("mark", "--issue", "3", "--event", "merged", "--pr", "7")["status"], "skipped")
        self.assertEqual(self.data()["_issue:3"]["comments"], [])

    def test_list(self):
        self.create_wt(issue=12, slug="free", sid="s")
        working = status_comment({"state": "working", "owner": OTHER, "branch": "bb-13-busy",
                                  "since": "2026-09-01", "history": []}, issue=13)
        self.set_data({
            "_issues": [bb_issue(12, "free"), bb_issue(13, "busy", comments=[working]), bb_issue(14, "review"),
                        bb_issue(15, "again"), bb_issue(16, "stuck", labels=("bombolt", "bombolt:blocked"))],
            "bb-14-review": [self.pr(20, "OPEN", author="amy-gh")],
            "bb-15-again": [self.pr(21, "CLOSED")],
        })
        out = self.issues("list")
        by_n = {i["number"]: i for i in out["issues"]}
        self.assertEqual({n: i["verdict"] for n, i in by_n.items()},
                         {12: "free", 13: "working", 14: "in_review", 15: "redo", 16: "blocked"})
        self.assertEqual(by_n[12]["local_worktree"], "bb-12-free")
        self.assertIn("Amy", by_n[13]["owner_text"])
        self.assertEqual(by_n[14]["open_prs"][0]["number"], 20)
        self.assertEqual(by_n[15]["closed_prs"][0]["number"], 21)

    def test_list_never_fails(self):
        # 會被 skill 動態注入：gh 失敗也要 exit 0，問題寫在 message
        self.set_data({"_fail": True})
        p = self.run_script("bb_issues.py", "list")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["status"], "error")


PR_BODY = textwrap.dedent("""\
    ## 一句話

    改了預設月份

    ## 🔁 要修改的話

    <!-- bombolt:handoff -->

    <details>
    <summary>🤖 bombolt 回饋（給 pipeline 迭代用）</summary>

    - 驗收迴圈跑了幾輪：1

    </details>

    Closes #12
    """)


class HandoffFixture(RepoFixture):
    """一個開著的 PR #7（issue 12、branch bb-12-bookings-default-month），主 checkout 那台叫 test-mac。"""

    def setUp(self):
        super().setUp()
        bindir = self.tmp / "bin"
        bindir.mkdir()
        (bindir / "gh").write_text(FAKE_GH)
        (bindir / "gh").chmod(0o755)
        self.gh_data = self.tmp / "gh.json"
        self.gh_data.write_text("{}")
        self.env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_GH_DATA": str(self.gh_data)}
        self.info = self.create_wt(sid="sess-1")
        self.branch = self.info["branch"]
        self.wt = Path(self.info["path"])
        self.commit(self.wt, "feature.txt", "feat\n", "feat work")
        sh(["git", "push", "-q", "-u", "origin", self.branch], self.wt)
        pr = {"number": 7, "url": "https://github.com/o/r/pull/7", "title": "feat: x (#12)", "state": "OPEN",
              "isDraft": False, "headRefName": self.branch, "headRefOid": self.tip(), "baseRefName": "prod",
              "body": PR_BODY, "comments": []}
        self.gh_data.write_text(json.dumps({
            "_pr:7": pr, "_issue:12": bb_issue(12, "bookings-default-month"),
            self.branch: [{"number": 7, "state": "OPEN", "url": pr["url"], "headRefOid": pr["headRefOid"]}],
        }, ensure_ascii=False))

    def data(self):
        return json.loads(self.gh_data.read_text())

    def update(self, key, **kw):
        d = self.data()
        d[key].update(kw)
        self.gh_data.write_text(json.dumps(d, ensure_ascii=False))

    def commit(self, where, fname, content, msg, author=None):
        (Path(where) / fname).write_text(content)
        sh(["git", "add", "-A"], where)
        who = ["-c", f"user.name={author}", "-c", "user.email=a@b"] if author else []
        sh(["git", *who, "commit", "-qm", msg], where)

    def tip(self):
        return sh(["git", "ls-remote", "origin", f"refs/heads/{self.branch}"], self.repo).stdout.split()[0]

    def bbpr(self, *args, cwd=None, check=True):
        p = self.run_script("bb_pr.py", *args, cwd=cwd or self.wt, check=check)
        return json.loads(p.stdout) if p.returncode == 0 else p

    def record(self, sid, kind="round", summary="發 PR", text="**決定**\n- 用 X 不用 Y", verified=True, cwd=None,
               check=True):
        f = self.tmp / f"record-{sid}-{kind}.md"
        f.write_text(text)
        return self.bbpr("record", "--pr", "7", "--session-id", sid, "--kind", kind, "--summary", summary,
                         "--body-file", str(f), *(["--verified"] if verified else []), cwd=cwd, check=check)

    def comments(self):
        return self.data()["_pr:7"]["comments"]

    def body(self):
        return self.data()["_pr:7"]["body"]

    def coworker_push(self, fname="co.txt", author="Amy"):
        other = self.tmp / "coworker"
        if not other.exists():
            sh(["git", "clone", "-q", "-b", self.branch, str(self.origin), str(other)], self.tmp)
        sh(["git", "pull", "-q", "origin", self.branch], other)
        self.commit(other, fname, "co\n", "coworker tweak", author=author)
        sh(["git", "push", "-q", "origin", f"HEAD:{self.branch}"], other)


class TestPrHandoff(HandoffFixture):
    """bb_pr.py：PR 是交接本——修改紀錄（PR 留言）、「🔁 要修改的話」、接手時要知道的事。"""

    def test_first_record_and_handoff_section(self):
        r = self.record("sess-1")
        self.assertEqual((r["status"], r["n"], r["updated"], r["commits"]), ("ok", 1, False, 1))
        [c] = self.comments()
        self.assertTrue(c["body"].startswith("🤖 **bombolt 紀錄 · 第 1 輪** — 發 PR"))
        self.assertIn("**t**（`tester`）的電腦「test-mac」", c["body"])  # 誰、哪台電腦：腳本一定會寫
        self.assertIn("✅ 驗收過這個 commit", c["body"])
        self.assertIn("feat work — t", c["body"])
        rec = bb_pr.parse_records(self.comments())[0]
        base = sh(["git", "rev-parse", "origin/prod"], self.repo).stdout.strip()
        self.assertEqual((rec["kind"], rec["n"], rec["to"], rec["from"]), ("round", 1, self.tip(), base))
        self.assertEqual(rec["owner"], ME)

        body = self.body()
        self.assertNotIn(bb_pr.HANDOFF_PLACEHOLDER, body)
        self.assertEqual(body.count(bb_pr.HANDOFF_START), 1)
        self.assertIn('claude -n bb-r-12-bookings-default-month --permission-mode auto "/bombolt:bb-fix 7"', body)
        self.assertIn("claude --resume sess-1", body)
        self.assertIn(f"[第 1 輪]({c['url']})", body)
        self.assertIn("<summary>🤖 bombolt 回饋", body)
        self.assertTrue(body.rstrip().endswith("Closes #12"))

        # 同一個 session、沒有新的 commit 再跑一次：改寫那一則，不重複貼
        r = self.record("sess-1", summary="發 PR（補充）")
        self.assertTrue(r["updated"])
        self.assertEqual(len(self.comments()), 1)
        self.assertIn("發 PR（補充）", self.comments()[0]["body"])
        self.assertEqual(self.body().count(bb_pr.HANDOFF_START), 1)

    def test_record_only_pushed_commits(self):
        self.commit(self.wt, "wip.txt", "wip\n", "not pushed")
        p = self.record("sess-1", verified=False, check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("push", p.stderr)
        self.assertEqual(self.comments(), [])

    def test_context_finds_unrecorded_commits(self):
        self.record("sess-1")
        for n in ("12", "7"):  # issue 編號、PR 編號都可以
            ctx = self.bbpr("context", "--pr", n)
            self.assertEqual(ctx["pr"]["number"], 7)
        self.assertEqual((ctx["issue"], len(ctx["records"]), ctx["unrecorded_commits"]), (12, 1, []))
        self.assertTrue(ctx["verified_head"])
        self.assertEqual(ctx["local_worktree"], str(self.wt))
        self.assertEqual(ctx["records"][0]["who"], "**t**（`tester`）的電腦「test-mac」")

        # 同事沒用 bombolt，直接 push：程式碼才是事實
        self.coworker_push()
        ctx = self.bbpr("context", "--pr", "7")
        self.assertEqual([c["author"] for c in ctx["unrecorded_commits"]], ["Amy"])
        self.assertFalse(ctx["verified_head"])
        self.assertIn("沒有紀錄的 commit（Amy）", " ".join(ctx["messages"]))

        # 補記：要先把 origin 的拉下來（本機比 origin 舊就不給記）
        self.assertEqual(self.record("sess-2", kind="catchup", summary="Amy 的調整", verified=False,
                                     check=False).returncode, 1)
        sh(["git", "pull", "-q", "--ff-only", "origin", self.branch], self.wt)
        self.record("sess-2", kind="catchup", summary="Amy 的調整", verified=False)
        c = self.comments()[-1]["body"]
        self.assertTrue(c.startswith("🤖 **bombolt 紀錄 · 補記** — Amy 的調整"))
        self.assertIn("沒有經過 bombolt 的改動（Amy），由 **t**（`tester`）的電腦「test-mac」從 diff 整理", c)
        self.assertIn("⚠️ 這個 commit 還沒驗收", c)
        ctx = self.bbpr("context", "--pr", "7")
        self.assertEqual(ctx["unrecorded_commits"], [])
        self.assertFalse(ctx["verified_head"])  # 補記不等於驗收
        self.assertIn("還沒有驗收過", " ".join(ctx["messages"]))

        # 下一輪：補記不算一輪
        self.commit(self.wt, "fix.txt", "fix\n", "round two")
        sh(["git", "push", "-q", "origin", self.branch], self.wt)
        r = self.record("sess-2", summary="回應 review")
        self.assertEqual(r["n"], 2)
        self.assertTrue(self.bbpr("context", "--pr", "7")["verified_head"])
        body = self.body()
        self.assertEqual(body.count("\n- ["), 3)  # 修改歷程：第 1 輪、補記、第 2 輪
        self.assertIn("claude --resume sess-2", body)
        self.assertNotIn("claude --resume sess-1", body)

    def test_note_appends_to_own_record(self):
        self.record("sess-1")
        r = self.bbpr("note", "--pr", "7", "--session-id", "sess-1", "--text", "review 第 2 條不改：會破壞 DoD 3")
        self.assertTrue(r["appended"])
        self.bbpr("note", "--pr", "7", "--session-id", "sess-1", "--text", "按鈕文案維持原樣")
        [c] = self.comments()
        self.assertEqual(c["body"].count(bb_pr.NOTES_HEADING), 1)
        self.assertIn("t「test-mac」：review 第 2 條不改：會破壞 DoD 3", c["body"])
        self.assertLess(c["body"].index("按鈕文案"), c["body"].index("</details>"))
        self.assertEqual(len(bb_pr.parse_records(self.comments())), 1)  # 附加之後還讀得到紀錄

        # 別的 session（例如新開的）：新貼一則「備註」
        r = self.bbpr("note", "--pr", "7", "--session-id", "sess-2", "--text", "先不做深色模式", "--summary", "不做深色模式")
        self.assertFalse(r["appended"])
        recs = bb_pr.parse_records(self.comments())
        self.assertEqual([x["kind"] for x in recs], ["round", "note"])
        self.assertTrue(self.comments()[-1]["body"].startswith("🤖 **bombolt 紀錄 · 備註** — 不做深色模式"))
        self.assertIn("claude --resume sess-2", self.body())
        self.assertTrue(self.bbpr("context", "--pr", "7")["verified_head"])  # 備註沒有動 code

    def test_history_rewritten(self):
        self.record("sess-1")
        (self.wt / "feature.txt").write_text("rewritten\n")
        sh(["git", "commit", "-qa", "--amend", "-m", "feat work v2"], self.wt)
        sh(["git", "push", "-q", "--force", "origin", self.branch], self.wt)
        ctx = self.bbpr("context", "--pr", "7")
        self.assertTrue(ctx["history_rewritten"])
        self.assertEqual([c["subject"] for c in ctx["unrecorded_commits"]], ["feat work v2"])
        self.assertIn("force push", " ".join(ctx["messages"]))

    def test_lock(self):
        working = status_comment({"state": "fixing", "owner": OTHER, "branch": self.branch, "since": "2026-09-01",
                                  "session_id": "x", "pr": 7, "history": []}, issue=12)
        self.update("_issue:12", comments=[working])
        ctx = self.bbpr("context", "--pr", "7", "--session-id", "mine")
        self.assertEqual((ctx["lock"]["mine"], ctx["lock"]["this_session"]), (False, False))
        self.assertIn("Amy 的 MacBook", " ".join(ctx["messages"]))
        self.run_script("bb_issues.py", "mark", "--issue", "12", "--event", "fixing", "--pr", "7", "--session-id", "mine")
        ctx = self.bbpr("context", "--pr", "7", "--session-id", "mine")
        self.assertEqual((ctx["lock"]["mine"], ctx["lock"]["this_session"]), (True, True))
        self.assertEqual(ctx["messages"], ["這個 PR 還沒有 bombolt 修改紀錄（這個功能之前開的）：以 PR 內文和 diff 為準，這一輪要重新驗收。"])

    def test_legacy_body_section_is_replaced(self):
        old = PR_BODY.replace("<!-- bombolt:handoff -->",
                              "實作 session 在 **t** 的電腦上，只有那台電腦能接著修改。\n\n```bash\nclaude --resume old\n```")
        self.update("_pr:7", body=old)
        self.record("sess-1")
        body = self.body()
        self.assertNotIn("只有那台電腦能接著修改", body)
        self.assertNotIn("claude --resume old", body)
        self.assertEqual(body.count("## 🔁 要修改的話"), 1)
        self.assertIn(bb_pr.HANDOFF_END, body)
        self.assertIn("<summary>🤖 bombolt 回饋", body)
        self.assertTrue(body.rstrip().endswith("Closes #12"))

    def test_resolve_errors(self):
        self.update("_pr:7", state="CLOSED")
        p = self.record("sess-1", check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("不是開著的", p.stderr)
        d = self.data()
        d[self.branch][0]["state"] = "CLOSED"
        self.gh_data.write_text(json.dumps(d, ensure_ascii=False))
        p = self.bbpr("context", "--pr", "12", check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("沒有開著的 PR", p.stderr)
        self.update("_pr:7", headRefName="feature/x", state="OPEN")
        p = self.bbpr("context", "--pr", "7", check=False)
        self.assertIn("不是 bombolt 建的", p.stderr)


class TestPickup(HandoffFixture):
    """bb_worktree.py pickup：另一台電腦（或同事）接手開著的 PR；worktree 的位置、名字都跟原本那台一樣。"""

    def setUp(self):
        super().setUp()
        self.repo2 = self.tmp / "machine2" / "repo"  # 另一台電腦的主 checkout
        sh(["git", "clone", "-q", str(self.origin), str(self.repo2)], self.tmp)
        for k, v in (("user.email", "b@example.com"), ("user.name", "t"), ("bombolt.machine", "intel-mac")):
            sh(["git", "config", k, v], self.repo2)
        (self.repo2 / ".claude").mkdir()
        (self.repo2 / ".claude" / "bombolt.md").write_text(CONFIG)
        (self.repo2 / "backend").mkdir()
        (self.repo2 / "backend" / ".env.local").write_text("SECRET=2\n")

    def pickup(self, cwd, n="7", sid="sess-b", check=True):
        p = self.run_script("bb_worktree.py", "pickup", "--pr", n, "--session-id", sid, cwd=cwd, check=check)
        return json.loads(p.stdout) if p.returncode == 0 else p

    def head(self, where):
        return sh(["git", "rev-parse", "HEAD"], where).stdout.strip()

    def test_other_machine_then_back(self):
        self.record("sess-1")
        r = self.pickup(self.repo2, n="12")  # issue 編號也可以
        wt2 = self.repo2 / ".claude" / "worktrees" / self.branch
        self.assertEqual((r["status"], r["path"], r["pr"], r["issue"]), ("created", str(wt2), 7, 12))
        self.assertEqual(self.head(wt2), self.tip())
        self.assertEqual(sh(["git", "rev-parse", "--abbrev-ref", "@{u}"], wt2).stdout.strip(), f"origin/{self.branch}")
        self.assertEqual((wt2 / "backend" / ".env.local").read_text(), "SECRET=2\n")  # 這台自己的 .env
        info = json.loads(self.run_script("bb_worktree.py", "info", cwd=wt2).stdout)
        self.assertEqual((info["session_id"], info["issue"], info["owner"]["machine"]), ("sess-b", 12, "intel-mac"))
        self.assertTrue(info["config_snapshot"])

        # 接手的那台改完、push（upstream 設好了，git push 就推回同一支）、記一輪
        ctx = self.bbpr("context", "--pr", "7", cwd=wt2)
        self.assertEqual((len(ctx["records"]), ctx["unrecorded_commits"], ctx["verified_head"]), (1, [], True))
        self.commit(wt2, "fix.txt", "fix\n", "fix from intel")
        sh(["git", "push", "-q"], wt2)
        self.record("sess-b", summary="回應 review", cwd=wt2)
        self.assertIn("**t**（`tester`）的電腦「intel-mac」", self.comments()[-1]["body"])

        # 回到原本那台：fast-forward、紀錄接得上
        r = self.pickup(self.repo, sid="sess-c")
        self.assertEqual((r["status"], r["pulled"]), ("updated", 1))
        self.assertEqual(self.head(self.wt), self.tip())
        self.assertEqual(self.bbpr("context", "--pr", "7")["unrecorded_commits"], [])
        self.assertEqual(self.pickup(self.repo, sid="sess-c")["status"], "exists")
        meta = bb_lib.read_meta(self.wt)
        self.assertEqual((meta["session_id"], meta["previous_session_ids"]), ("sess-c", ["sess-1"]))

    def test_ahead_diverged_dirty(self):
        self.commit(self.wt, "local.txt", "x\n", "local only")
        r = self.pickup(self.repo)
        self.assertEqual((r["status"], r["ahead"]), ("ahead", 1))
        self.assertIn("還沒 push", r["message"])
        self.coworker_push()
        p = self.pickup(self.repo, check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("分岔", p.stderr)
        sh(["git", "reset", "-q", "--hard", "HEAD~1"], self.wt)
        (self.wt / "wip.txt").write_text("wip\n")
        p = self.pickup(self.repo, check=False)
        self.assertIn("未 commit", p.stderr)

    def test_leftover_branch_without_worktree(self):
        sh(["git", "worktree", "remove", str(self.wt)], self.repo)
        self.coworker_push()
        r = self.pickup(self.repo)
        self.assertEqual((r["status"], r["pulled"]), ("created", 1))
        self.assertEqual(self.head(self.wt), self.tip())

    def test_closed_pr(self):
        self.update("_pr:7", state="MERGED")
        p = self.pickup(self.repo2, check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("不是開著的", p.stderr)
        self.assertFalse((self.repo2 / ".claude" / "worktrees").exists())


class TestRedoClean(RepoFixture):
    """bb_worktree.py redo-clean：前一次的 PR 被關掉、要用同一個名字重做時，安全地清掉前一次留下的東西。"""

    def setUp(self):
        super().setUp()
        bindir = self.tmp / "bin"
        bindir.mkdir()
        (bindir / "gh").write_text(FAKE_GH)
        (bindir / "gh").chmod(0o755)
        self.gh_data = self.tmp / "gh.json"
        self.gh_data.write_text("{}")
        home = self.tmp / "home"
        (home / ".claude" / "sessions").mkdir(parents=True)
        self.sessions = home / ".claude" / "sessions"
        self.env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_GH_DATA": str(self.gh_data),
                    "HOME": str(home)}
        info = self.create_wt(issue=3, slug="x", sid="old-session")
        self.wt = Path(info["path"])
        (self.wt / "f.txt").write_text("first try\n")
        sh(["git", "add", "-A"], self.wt)
        sh(["git", "commit", "-qm", "first try"], self.wt)
        sh(["git", "push", "-q", "-u", "origin", "bb-3-x"], self.wt)
        self.head = sh(["git", "rev-parse", "HEAD"], self.wt).stdout.strip()

    def set_pr(self, state, head=None):
        self.gh_data.write_text(json.dumps({"bb-3-x": [
            {"number": 7, "state": state, "url": "u", "headRefOid": head or self.head, "author": {"login": "t"}}]}))

    def redo_clean(self, check=True):
        return self.run_script("bb_worktree.py", "redo-clean", "--issue", "3", "--slug", "x", check=check)

    def remote_has_branch(self):
        return sh(["git", "ls-remote", "origin", "refs/heads/bb-3-x"], self.repo).stdout.strip() != ""

    def assert_untouched(self):
        self.assertTrue(self.wt.exists())
        self.assertTrue(self.remote_has_branch())

    def test_cleans_closed_attempt_then_create_starts_fresh(self):
        self.set_pr("CLOSED")
        out = json.loads(self.redo_clean().stdout)
        self.assertEqual((out["status"], out["closed_prs"]), ("ok", [7]))
        self.assertFalse(self.wt.exists())
        self.assertEqual(sh(["git", "branch", "--list", "bb-3-x"], self.repo).stdout.strip(), "")
        self.assertFalse(self.remote_has_branch())
        again = self.create_wt(issue=3, slug="x", sid="new-session")
        self.assertEqual(again["status"], "created")
        self.assertFalse((Path(again["path"]) / "f.txt").exists())  # 從最新的 base 重新開始

    def test_refuses_open_or_merged_pr(self):
        for state in ("OPEN", "MERGED"):
            self.set_pr(state)
            p = self.redo_clean(check=False)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn(state, p.stderr)
            self.assert_untouched()

    def test_refuses_without_closed_pr(self):
        self.gh_data.write_text("{}")
        p = self.redo_clean(check=False)
        self.assertIn("沒有被關掉的 PR", p.stderr)
        self.assert_untouched()

    def test_refuses_dirty_or_extra_local_commit(self):
        self.set_pr("CLOSED")
        (self.wt / "wip.txt").write_text("wip\n")
        p = self.redo_clean(check=False)
        self.assertIn("未 commit", p.stderr)
        self.assert_untouched()
        sh(["git", "add", "-A"], self.wt)
        sh(["git", "commit", "-qm", "after close"], self.wt)
        p = self.redo_clean(check=False)
        self.assertIn("沒有的 commit", p.stderr)
        self.assert_untouched()

    def test_refuses_remote_moved_after_close(self):
        self.set_pr("CLOSED")
        other = self.tmp / "other"
        sh(["git", "clone", "-q", "-b", "bb-3-x", str(self.origin), str(other)], self.tmp)
        sh(["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "later"], other)
        sh(["git", "push", "-q", "origin", "bb-3-x"], other)
        p = self.redo_clean(check=False)
        self.assertIn("又有新的 commit", p.stderr)
        self.assert_untouched()

    def test_refuses_while_old_session_alive(self):
        self.set_pr("CLOSED")
        (self.sessions / "1.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "old-session",
                                                          "cwd": str(self.repo)}))
        p = self.redo_clean(check=False)
        self.assertIn("session 還開著", p.stderr)
        self.assert_untouched()

    def test_only_remote_left(self):
        # 在另一台電腦重做：本機沒有 worktree 也沒有 branch，只要清遠端
        sh(["git", "worktree", "remove", str(self.wt)], self.repo)
        sh(["git", "branch", "-D", "bb-3-x"], self.repo)
        self.set_pr("CLOSED")
        out = json.loads(self.redo_clean().stdout)
        self.assertEqual(len(out["done"]), 1)
        self.assertFalse(self.remote_has_branch())

    def test_branch_already_gone(self):
        sh(["git", "worktree", "remove", str(self.wt)], self.repo)
        sh(["git", "branch", "-D", "bb-3-x"], self.repo)
        sh(["git", "push", "-q", "origin", "--delete", "bb-3-x"], self.repo)
        self.set_pr("CLOSED")
        out = json.loads(self.redo_clean().stdout)
        self.assertIn("不用清", out["done"][0])


class TestPlanCheck(RepoFixture):
    """bb_plan_check.py：bb-plan 開工前確認「在主 checkout」與「本地跟 origin 沒有會讓規劃不準的差異」。"""

    def setUp(self):
        super().setUp()
        import bb_plan_check
        self.pc = bb_plan_check
        # 設定檔已經 commit 並 push 到 origin/prod：乾淨的起點
        sh(["git", "checkout", "-q", "-b", "prod", "origin/prod"], self.repo)
        sh(["git", "add", ".claude/bombolt.md"], self.repo)
        sh(["git", "commit", "-qm", "config"], self.repo)
        sh(["git", "push", "-q", "origin", "prod"], self.repo)

    def check(self, cwd=None):
        out = self.run_script("bb_plan_check.py", cwd=cwd).stdout
        return out

    def test_clean_main_checkout(self):
        loc = self.pc.check_location(self.repo)
        self.assertEqual(loc["status"], "ok")
        self.assertEqual(Path(loc["main_checkout"]), self.repo)
        sync = self.pc.check_sync(self.repo)
        self.assertEqual(sync["status"], "ok", sync)
        self.assertEqual(sync["diffs"], [])
        out = self.check()
        self.assertIn("✅ 確定是主 checkout", out)
        self.assertIn("同步：✅", out)

    def test_subdirectory_is_still_main_checkout(self):
        self.assertEqual(self.pc.check_location(self.repo / "backend")["status"], "ok")

    def test_linked_worktree_is_not_main(self):
        wt = self.create_wt()["path"]
        loc = self.pc.check_location(Path(wt))
        self.assertEqual(loc["status"], "not_main")
        self.assertEqual(Path(loc["main_checkout"]), self.repo)
        out = self.check(cwd=wt)
        self.assertIn("❌ 不是主 checkout", out)
        self.assertIn(str(self.repo), out)
        self.assertIn("沒有檢查", out)

    def test_plan_snapshot_dir_is_not_main(self):
        snap = self.repo / ".claude/worktrees/_plan-x"
        snap.mkdir(parents=True)
        bb_lib.ensure_local_exclude(self.repo, f"/{bb_lib.WORKTREES_REL}/")
        self.assertEqual(self.pc.check_location(snap)["status"], "not_main")

    def test_outside_repo(self):
        out = self.check(cwd=self.tmp)
        self.assertIn("不在 git repo 裡", out)

    def test_submodule_like_git_dir_is_unknown(self):
        # git 目錄不是 <repo>/.git（submodule、--separate-git-dir 等）→ 推不出主 checkout，要反問
        sep = self.tmp / "sep"
        sh(["git", "init", "-q", "--separate-git-dir", str(self.tmp / "sep.gitdir"), str(sep)], self.tmp)
        loc = self.pc.check_location(sep)
        self.assertEqual(loc["status"], "unknown")
        self.assertIn("❓", self.check(cwd=sep))
        # 使用者確認之後用 --assume-main 補做同步檢查（這個 repo 沒有設定檔 → 回報要先 bb-setup）
        out = self.run_script("bb_plan_check.py", "--assume-main", cwd=sep).stdout
        self.assertIn("使用者確認", out)
        self.assertIn("bb-setup", out)

    def test_assume_main_does_not_override_linked_worktree(self):
        wt = self.create_wt()["path"]
        out = self.run_script("bb_plan_check.py", "--assume-main", cwd=wt).stdout
        self.assertIn("❌ 不是主 checkout", out)

    def test_origin_moved_ahead_is_fetched_not_a_diff(self):
        other = self.tmp / "other"
        sh(["git", "clone", "-q", "-b", "prod", str(self.origin), str(other)], self.tmp)
        sh(["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "newer"], other)
        sh(["git", "push", "-q", "origin", "prod"], other)
        newest = sh(["git", "rev-parse", "HEAD"], other).stdout.strip()
        sync = self.pc.check_sync(self.repo)
        self.assertEqual(sync["origin_sha"], newest)  # 真的 fetch 到了最新
        self.assertEqual(sync["status"], "ok")
        self.assertTrue(any("落後" in n for n in sync["notes"]))

    def test_unpushed_base_commit(self):
        sh(["git", "commit", "-q", "--allow-empty", "-m", "local only"], self.repo)
        sync = self.pc.check_sync(self.repo)
        self.assertEqual(sync["status"], "diff")
        self.assertTrue(any("還沒 push" in d for d in sync["diffs"]))

    def test_feature_branch_with_own_commits_is_only_a_note(self):
        # 規劃一律以 origin/prod 為準：IDE 開在哪個 branch 只告知，不問
        sh(["git", "checkout", "-q", "-b", "feat-x"], self.repo)
        (self.repo / "app.txt").write_text("v2\n")
        sh(["git", "commit", "-qam", "wip"], self.repo)
        sync = self.pc.check_sync(self.repo)
        self.assertEqual(sync["status"], "ok", sync)
        self.assertTrue(any("feat-x" in n and "1 個 commit" in n for n in sync["notes"]), sync["notes"])

    def test_branch_without_config_uses_origin_default_branch(self):
        # IDE 切到還沒有 bombolt 設定的舊 branch：不會誤判成「還沒設定」
        sh(["git", "remote", "set-head", "origin", "prod"], self.repo)
        sh(["git", "checkout", "-q", "main"], self.repo)
        self.assertFalse((self.repo / ".claude/bombolt.md").exists())
        sync = self.pc.check_sync(self.repo)
        self.assertEqual((sync["status"], sync["base"]), ("ok", "prod"), sync)
        self.assertTrue(any("預設 branch" in n for n in sync["notes"]), sync["notes"])

    def test_other_branch_without_own_commits_is_only_a_note(self):
        sh(["git", "checkout", "-q", "-b", "other", "origin/prod"], self.repo)
        sync = self.pc.check_sync(self.repo)
        self.assertEqual(sync["status"], "ok", sync)
        self.assertTrue(any("`other`" in n for n in sync["notes"]))

    def test_uncommitted_changes_are_only_a_note(self):
        (self.repo / "app.txt").write_text("dirty\n")
        (self.repo / "new.txt").write_text("new\n")
        sync = self.pc.check_sync(self.repo)
        self.assertEqual(sync["status"], "ok", sync)
        self.assertTrue(any("2 個沒 commit" in n for n in sync["notes"]), sync["notes"])

    def test_ignored_files_and_worktrees_are_not_diffs(self):
        # .env、log 被 gitignore；bombolt 的 worktree 在 .git/info/exclude → 都不算差異
        self.create_wt()
        self.assertEqual(self.pc.check_sync(self.repo)["status"], "ok")

    def test_config_differs_from_origin_uses_origin_and_says_so(self):
        cfg = self.repo / ".claude/bombolt.md"
        cfg.write_text(cfg.read_text() + "\n本地改過\n")
        sync = self.pc.check_sync(self.repo)
        self.assertEqual(sync["status"], "ok", sync)
        self.assertFalse(any("沒 commit" in n for n in sync["notes"]), sync["notes"])  # 設定檔另外說明
        self.assertTrue(any("origin/prod" in n and "沒有採用" in n for n in sync["notes"]), sync["notes"])

    def test_config_not_on_origin(self):
        sh(["git", "checkout", "-q", "main"], self.repo)  # main 上沒有 commit 設定檔（checkout 會把它移走）
        (self.repo / ".claude").mkdir(exist_ok=True)
        (self.repo / ".claude/bombolt.md").write_text(CONFIG.replace("base_branch: prod", "base_branch: main"))
        sync = self.pc.check_sync(self.repo)
        self.assertEqual(sync["status"], "diff")
        self.assertTrue(any("還不在" in d for d in sync["diffs"]), sync["diffs"])

    def test_fetch_failure_is_reported_and_exit_zero(self):
        sh(["git", "remote", "set-url", "origin", str(self.tmp / "gone.git")], self.repo)
        sync = self.pc.check_sync(self.repo)
        self.assertEqual(sync["status"], "fetch_failed")
        p = self.run_script("bb_plan_check.py")
        self.assertEqual(p.returncode, 0)
        self.assertIn("無法確認是最新版", p.stdout)

    def test_no_config(self):
        (self.repo / ".claude/bombolt.md").unlink()
        self.assertEqual(self.pc.check_sync(self.repo)["status"], "no_base")
        self.assertIn("bb-setup", self.check())


FAKE_NGROK = textwrap.dedent("""\
    #!{python}
    # 假的 ngrok：ok 模式在 BB_NGROK_API 開一個假的本機 API；fail 模式寫一行錯誤 log 就結束
    import http.server, json, os, sys, urllib.parse
    args = sys.argv[1:]
    with open(os.environ["FAKE_NGROK_ARGS"], "w") as f:
        json.dump(args, f)
    log = args[args.index("--log") + 1]
    if os.environ.get("FAKE_NGROK_MODE") == "fail":
        with open(log, "a") as f:
            f.write(json.dumps({{"lvl": "info", "msg": "ignoring default config path", "err": "stat x: no such file"}}) + "\\n")
            f.write(json.dumps({{"lvl": "crit", "msg": "command failed", "err": "authentication failed: ERR_NGROK_4018\\r\\n"}}) + "\\n")
        sys.exit(1)
    api = urllib.parse.urlparse(os.environ["BB_NGROK_API"])
    body = json.dumps({{"endpoints": [{{"url": "https://fake.ngrok-free.dev", "upstream": {{"url": "http://localhost:" + args[1]}}}}]}}).encode()
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == api.path + "/endpoints" else 404)
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a):
            pass
    http.server.HTTPServer((api.hostname, api.port), H).serve_forever()
    """)


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestSandbox(RepoFixture):
    def setUp(self):
        super().setUp()
        self.wt = self.create_wt()["path"]
        stub = self.tmp / "stub-bin"
        (stub / "ngrok").write_text(FAKE_NGROK.format(python=sys.executable))
        (stub / "ngrok").chmod(0o755)
        self.api_port = free_port()
        self.args_file = self.tmp / "ngrok-args.json"
        self.env.update(BB_NGROK_API=f"http://127.0.0.1:{self.api_port}/api", FAKE_NGROK_ARGS=str(self.args_file))
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            p.kill()
            p.communicate()  # 順便關掉 pipe
        super().tearDown()

    def start_tunnel(self, port=3012, mode="ok"):
        p = subprocess.Popen([sys.executable, str(SCRIPTS / "bb_sandbox.py"), "tunnel", "--port", str(port)],
                             cwd=self.wt, env={**self.env, "FAKE_NGROK_MODE": mode},
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.procs.append(p)
        return p

    def test_tunnel_then_url(self):
        p = self.start_tunnel()
        out = json.loads(self.run_script("bb_sandbox.py", "url", "--timeout", "10", cwd=self.wt).stdout)
        self.assertEqual(out["url"], "https://fake.ngrok-free.dev")
        self.assertEqual((out["user"], out["port"], out["pid"]), ("bombolt", 3012, p.pid))  # exec 之後 pid 不變
        self.assertGreaterEqual(len(out["password"]), 16)
        # ngrok 拿到的 traffic policy 帶著同一組帳密
        args = json.loads(self.args_file.read_text())
        self.assertEqual(args[:2], ["http", "3012"])
        policy = json.loads(Path(args[args.index("--traffic-policy-file") + 1]).read_text())
        action = policy["on_http_request"][0]["actions"][0]
        self.assertEqual(action["type"], "basic-auth")
        self.assertEqual(action["config"]["credentials"], [f"bombolt:{out['password']}"])
        # 停掉背景工作 ＝ 停掉 ngrok：之後 url 回報沒有在跑
        p.kill()
        p.wait()
        r = self.run_script("bb_sandbox.py", "url", "--timeout", "1", cwd=self.wt, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ngrok 沒有在跑", r.stderr)

    def test_new_password_each_time(self):
        passwords = []
        for _ in range(2):
            p = self.start_tunnel()
            passwords.append(json.loads(self.run_script("bb_sandbox.py", "url", "--timeout", "10", cwd=self.wt).stdout)["password"])
            p.kill()
            p.wait()
        self.assertNotEqual(passwords[0], passwords[1])

    def test_url_reports_ngrok_error(self):
        p = self.start_tunnel(mode="fail")
        p.wait()
        r = self.run_script("bb_sandbox.py", "url", "--timeout", "5", cwd=self.wt, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ERR_NGROK_4018", r.stderr)
        self.assertNotIn("no such file", r.stderr)  # info 行帶的 err 不算錯誤

    def test_refuses_when_ngrok_already_running(self):
        first = self.start_tunnel(port=3001)
        state = json.loads(self.run_script("bb_sandbox.py", "url", "--timeout", "10", cwd=self.wt).stdout)
        second = self.start_tunnel(port=3012)
        second.wait(timeout=10)
        self.assertNotEqual(second.returncode, 0)
        err = second.stderr.read()
        self.assertIn("已經有一個 ngrok 在跑", err)
        self.assertIn("https://fake.ngrok-free.dev → http://localhost:3001", err)
        # 沒有覆寫正在跑的那一個的帳密
        self.assertEqual(json.loads(self.run_script("bb_sandbox.py", "url", cwd=self.wt).stdout), state)
        self.assertIsNone(first.poll())

    def test_needs_ngrok_and_worktree(self):
        no_ngrok = self.tmp / "no-ngrok-bin"
        no_ngrok.mkdir()
        (no_ngrok / "git").symlink_to(shutil.which("git"))
        r = sh([sys.executable, str(SCRIPTS / "bb_sandbox.py"), "tunnel", "--port", "3012"], self.wt,
               env={**self.env, "PATH": str(no_ngrok)}, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("brew install ngrok", r.stderr)
        r = self.run_script("bb_sandbox.py", "tunnel", "--port", "3012", check=False)  # 在主 checkout
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("不在 bombolt 建的 worktree", r.stderr)
        r = self.run_script("bb_sandbox.py", "url", "--timeout", "1", cwd=self.wt, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("還沒開過 tunnel", r.stderr)
