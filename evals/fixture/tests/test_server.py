import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from todo.server import make_server


class ServerTestCase(unittest.TestCase):
    """每個測試起一個 server（port 0、背景 thread），資料檔放暫存目錄。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "todos.json")
        self.server = make_server(0, self.data)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.base + path, data=body, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, raw
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    def json(self, method, path, payload=None):
        status, raw = self.request(method, path, payload)
        return status, (json.loads(raw) if raw else None)


class ApiTest(ServerTestCase):
    def test_list_empty(self):
        self.assertEqual(self.json("GET", "/api/todos"), (200, {"todos": []}))

    def test_create(self):
        status, body = self.json("POST", "/api/todos", {"title": "買牛奶", "due": "2026-10-20"})
        self.assertEqual(status, 201)
        self.assertEqual(body["todo"]["title"], "買牛奶")
        status, body = self.json("GET", "/api/todos")
        self.assertEqual([t["title"] for t in body["todos"]], ["買牛奶"])

    def test_invalid_due(self):
        status, body = self.json("POST", "/api/todos", {"title": "x", "due": "10/20"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_todo")

    def test_done_unknown(self):
        status, body = self.json("POST", "/api/todos/99/done")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")

    def test_done(self):
        self.json("POST", "/api/todos", {"title": "a"})
        status, body = self.json("POST", "/api/todos/1/done")
        self.assertEqual(status, 200)
        self.assertTrue(body["todo"]["done"])


class PageTest(ServerTestCase):
    def test_index_lists_titles(self):
        self.json("POST", "/api/todos", {"title": "<買牛奶>"})
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("&lt;買牛奶&gt;", page)

    def test_index_empty(self):
        status, page = self.request("GET", "/")
        self.assertIn("還沒有任何待辦事項", page)


if __name__ == "__main__":
    unittest.main()
