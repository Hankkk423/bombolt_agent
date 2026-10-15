"""隱藏測試：GET /api/todos?sort=due，含 review 補的規則（eval 打分用，agent 看不到）。"""

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from todo.server import make_server

TODOS = [
    {"id": 1, "title": "a", "done": False, "due": "2026-10-20", "created_at": "2026-10-01T09:00:00"},
    {"id": 2, "title": "b", "done": False, "due": None, "created_at": "2026-10-01T08:00:00"},
    {"id": 3, "title": "c", "done": True, "due": "2026-10-15", "created_at": "2026-10-02T09:00:00"},
    {"id": 4, "title": "d", "done": False, "due": "2026-10-20", "created_at": "2026-09-30T09:00:00"},
    {"id": 5, "title": "e", "done": False, "due": None, "created_at": "2026-10-03T09:00:00"},
]


class HiddenSortTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = os.path.join(self.tmp.name, "todos.json")
        with open(data, "w", encoding="utf-8") as f:
            json.dump({"next_id": 6, "todos": TODOS}, f)
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

    def test_sort_due_nulls_last_ties_by_created_at(self):
        self.assertEqual(self.ids("/api/todos?sort=due"), [3, 4, 1, 2, 5])

    def test_default_order_unchanged(self):
        self.assertEqual(self.ids("/api/todos"), [1, 2, 3, 4, 5])

    def test_invalid_sort(self):
        status, body = self.get("/api/todos?sort=title")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_query")


if __name__ == "__main__":
    unittest.main()
