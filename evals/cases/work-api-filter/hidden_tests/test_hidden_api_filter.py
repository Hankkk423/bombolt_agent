"""隱藏測試：GET /api/todos?done=true|false（eval 打分用，agent 看不到）。"""

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from todo.server import make_server
from todo.store import TodoStore


class HiddenDoneFilterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = os.path.join(self.tmp.name, "todos.json")
        store = TodoStore(data)
        for title in ("買牛奶", "繳電費", "回信", "訂車票"):
            store.add(title)
        store.set_done(2)
        store.set_done(4)
        self.server = make_server(0, data)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def ids(self, path):
        status, body = self.get(path)
        self.assertEqual(status, 200, body)
        return [t["id"] for t in body["todos"]]

    def test_true_returns_only_done(self):
        self.assertEqual(self.ids("/api/todos?done=true"), [2, 4])

    def test_false_returns_only_pending(self):
        self.assertEqual(self.ids("/api/todos?done=false"), [1, 3])

    def test_absent_returns_all(self):
        self.assertEqual(self.ids("/api/todos"), [1, 2, 3, 4])

    def test_blank_treated_as_absent(self):
        self.assertEqual(self.ids("/api/todos?done="), [1, 2, 3, 4])

    def test_invalid_values_rejected(self):
        for value in ("yes", "1", "True", "FALSE"):
            status, body = self.get(f"/api/todos?done={value}")
            self.assertEqual(status, 400, value)
            self.assertEqual(body["error"]["code"], "invalid_query", value)
            self.assertTrue(body["error"]["message"], value)

    def test_filtered_items_keep_overdue_field(self):
        status, body = self.get("/api/todos?done=false")
        self.assertTrue(all("overdue" in t for t in body["todos"]))


if __name__ == "__main__":
    unittest.main()
