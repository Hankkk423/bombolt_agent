"""隱藏測試：todo count（eval 打分用，agent 看不到）。"""

import contextlib
import io
import json
import os
import tempfile
import unittest

from todo.cli import main

TODOS = [
    {"id": 1, "title": "a", "done": True, "due": None, "created_at": "2026-10-01T09:00:00"},
    {"id": 2, "title": "b", "done": False, "due": "2026-10-20", "created_at": "2026-10-01T10:00:00"},
    {"id": 3, "title": "c", "done": True, "due": None, "created_at": "2026-10-02T09:00:00"},
]


class HiddenCountTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "todos.json")

    def tearDown(self):
        self.tmp.cleanup()

    def count(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--data", self.data, "count"])
        return code, out.getvalue()

    def test_count_excludes_done(self):
        with open(self.data, "w", encoding="utf-8") as f:
            json.dump({"next_id": 4, "todos": TODOS}, f)
        self.assertEqual(self.count(), (0, "共 3 筆，未完成 1 筆\n"))

    def test_count_empty(self):
        self.assertEqual(self.count(), (0, "共 0 筆，未完成 0 筆\n"))


if __name__ == "__main__":
    unittest.main()
