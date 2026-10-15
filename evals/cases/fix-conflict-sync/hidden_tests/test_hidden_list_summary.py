"""隱藏測試：todo list 的摘要行，加上 main 上 #87 的 --pending（eval 打分用，agent 看不到）。"""

import contextlib
import io
import os
import tempfile
import unittest

from todo.cli import main


class HiddenListSummaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "todos.json")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(["--data", self.data, *argv])
        return code, out.getvalue()

    def seed(self, titles, done=()):
        for title in titles:
            self.run_cli("add", title)
        for n in done:
            self.run_cli("done", str(n))

    def test_list_with_summary(self):
        self.seed(["買牛奶", "繳電費"], done=[2])
        self.assertEqual(self.run_cli("list"), (0, "[ ] 1 買牛奶\n[x] 2 繳電費\n共 2 項，1 項未完成\n"))

    def test_pending_keeps_working_and_counts_all(self):
        self.seed(["買牛奶", "繳電費", "回信"], done=[1])
        self.assertEqual(self.run_cli("list", "--pending"), (0, "[ ] 2 繳電費\n[ ] 3 回信\n共 3 項，2 項未完成\n"))

    def test_all_done(self):
        self.seed(["買牛奶"], done=[1])
        self.assertEqual(self.run_cli("list"), (0, "[x] 1 買牛奶\n共 1 項，全部完成\n"))

    def test_all_done_pending(self):
        self.seed(["買牛奶"], done=[1])
        self.assertEqual(self.run_cli("list", "--pending"), (0, "共 1 項，全部完成\n"))

    def test_empty(self):
        self.assertEqual(self.run_cli("list"), (0, "還沒有任何待辦事項\n"))


if __name__ == "__main__":
    unittest.main()
