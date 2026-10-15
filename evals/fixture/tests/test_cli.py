import contextlib
import io
import os
import tempfile
import unittest

from todo.cli import main


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "todos.json")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--data", self.data, *argv])
        return code, out.getvalue(), err.getvalue()

    def test_list_empty(self):
        self.assertEqual(self.run_cli("list"), (0, "還沒有任何待辦事項\n", ""))

    def test_add_and_list(self):
        self.run_cli("add", "買牛奶", "--due", "2026-10-20")
        self.run_cli("add", "繳電費")
        self.run_cli("done", "2")
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertEqual(out, "[ ] 1 買牛奶 (due 2026-10-20)\n[x] 2 繳電費\n")

    def test_done_unknown(self):
        code, _, err = self.run_cli("done", "9")
        self.assertEqual(code, 1)
        self.assertIn("找不到 #9", err)


if __name__ == "__main__":
    unittest.main()
