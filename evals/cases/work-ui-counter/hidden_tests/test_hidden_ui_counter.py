"""隱藏測試：首頁的「還有 N 項未完成」（eval 打分用，agent 看不到）。"""

import os
import re
import tempfile
import threading
import unittest
import urllib.request

from todo.server import make_server
from todo.store import TodoStore

COUNTER_RE = re.compile(r'<p\b[^>]*\bid="remaining"[^>]*>(.*?)</p>', re.S)


class HiddenCounterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "todos.json")
        self.store = TodoStore(self.data)
        self.server = make_server(0, self.data)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def page(self):
        with urllib.request.urlopen(self.base + "/") as resp:
            return resp.read().decode("utf-8")

    def counter(self):
        m = COUNTER_RE.search(self.page())
        return m.group(1).strip() if m else None

    def test_counts_pending(self):
        for title in ("a", "b", "c"):
            self.store.add(title)
        self.store.set_done(1)
        self.assertEqual(self.counter(), "還有 2 項未完成")

    def test_updates_after_done_via_form(self):
        for title in ("a", "b"):
            self.store.add(title)
        req = urllib.request.Request(self.base + "/todos/1/done", data=b"", method="POST")
        urllib.request.urlopen(req).read()
        self.assertEqual(self.counter(), "還有 1 項未完成")

    def test_all_done(self):
        self.store.add("a")
        self.store.set_done(1)
        self.assertEqual(self.counter(), "全部完成了")

    def test_empty_has_no_counter(self):
        page = self.page()
        self.assertNotIn('id="remaining"', page)
        self.assertIn("還沒有任何待辦事項", page)

    def test_counter_above_list(self):
        self.store.add("a")
        page = self.page()
        self.assertLess(page.index('id="remaining"'), page.index("<ul>"))


if __name__ == "__main__":
    unittest.main()
