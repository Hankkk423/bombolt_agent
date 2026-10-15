"""隱藏測試：今天到期的待辦不算逾期（eval 打分用，agent 看不到）。"""

import json
import os
import tempfile
import threading
import unittest
import urllib.request
from unittest import mock

from todo.dates import is_overdue
from todo.server import make_server
from todo.store import TodoStore

TODAY = "2026-10-14"


class HiddenIsOverdueTest(unittest.TestCase):
    def test_due_today_is_not_overdue(self):
        self.assertFalse(is_overdue({"done": False, "due": TODAY}, TODAY))

    def test_due_yesterday_is_overdue(self):
        self.assertTrue(is_overdue({"done": False, "due": "2026-10-13"}, TODAY))

    def test_due_tomorrow_is_not_overdue(self):
        self.assertFalse(is_overdue({"done": False, "due": "2026-10-15"}, TODAY))

    def test_done_never_overdue(self):
        self.assertFalse(is_overdue({"done": True, "due": "2026-10-13"}, TODAY))


class HiddenOverdueEndToEndTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"TODO_TODAY": TODAY})
        self.env.start()
        self.tmp = tempfile.TemporaryDirectory()
        data = os.path.join(self.tmp.name, "todos.json")
        store = TodoStore(data)
        store.add("今天到期", TODAY)
        store.add("昨天到期", "2026-10-13")
        self.server = make_server(0, data)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()
        self.env.stop()

    def fetch(self, path):
        with urllib.request.urlopen(self.base + path) as resp:
            return resp.read().decode("utf-8")

    def test_api_flags(self):
        todos = json.loads(self.fetch("/api/todos"))["todos"]
        self.assertEqual([t["overdue"] for t in todos], [False, True])

    def test_page_marks_only_past_due(self):
        page = self.fetch("/")
        self.assertEqual(page.count('class="overdue"'), 1)
        today_item = [line for line in page.splitlines() if "今天到期" in line][0]
        self.assertNotIn("逾期", today_item)


if __name__ == "__main__":
    unittest.main()
