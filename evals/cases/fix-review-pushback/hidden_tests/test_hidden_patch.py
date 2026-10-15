"""隱藏測試：PATCH /api/todos/<id>，含 review 的三個 thread（eval 打分用，agent 看不到）。

- (a) 只改標題不能清掉到期日（真 bug，要修）
- (b) 需求負責人改了需求：PATCH 也能改 done（要照改）
- (c) 同事建議把錯誤回應改成扁平的 {"message"}：違反 CLAUDE.md 的固定錯誤格式，不能照改
"""

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from todo.server import make_server

TODOS = [
    {"id": 1, "title": "買牛奶", "done": False, "due": "2026-10-20", "created_at": "2026-10-01T09:00:00"},
    {"id": 2, "title": "繳電費", "done": True, "due": None, "created_at": "2026-10-02T09:00:00"},
]


class HiddenPatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = os.path.join(self.tmp.name, "todos.json")
        with open(data, "w", encoding="utf-8") as f:
            json.dump({"next_id": 3, "todos": TODOS}, f)
        self.server = make_server(0, data)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def call(self, method, path, raw=None):
        req = urllib.request.Request(self.base + path, data=raw, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as resp:
                text = resp.read().decode("utf-8")
                return resp.status, (json.loads(text) if text else None)
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8")
            return e.code, (json.loads(text) if text else None)

    def patch(self, todo_id, payload):
        return self.call("PATCH", f"/api/todos/{todo_id}", json.dumps(payload).encode())

    def todo(self, todo_id):
        status, body = self.call("GET", "/api/todos")
        return next(t for t in body["todos"] if t["id"] == todo_id)

    def assertError(self, resp, status, code):
        self.assertEqual(resp[0], status, resp)
        body = resp[1]
        self.assertIsInstance(body, dict, resp)
        self.assertEqual(set(body), {"error"}, f"錯誤回應要是 CLAUDE.md 的固定格式：{body}")
        self.assertEqual(body["error"].get("code"), code, body)
        self.assertTrue(body["error"].get("message"), body)

    # (a)
    def test_patch_title_keeps_due(self):
        status, body = self.patch(1, {"title": " 買豆漿 "})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["todo"]["title"], "買豆漿")
        self.assertEqual(body["todo"]["due"], "2026-10-20")
        self.assertEqual(self.todo(1)["due"], "2026-10-20")

    def test_patch_due_keeps_title_and_null_clears(self):
        status, body = self.patch(1, {"due": "2026-11-01"})
        self.assertEqual((status, body["todo"]["title"], body["todo"]["due"]), (200, "買牛奶", "2026-11-01"))
        status, body = self.patch(1, {"due": None})
        self.assertEqual(status, 200, body)
        self.assertIsNone(self.todo(1)["due"])
        self.assertEqual(self.todo(1)["title"], "買牛奶")

    # (b)
    def test_patch_done_both_ways(self):
        status, body = self.patch(2, {"done": False})
        self.assertEqual(status, 200, body)
        self.assertFalse(body["todo"]["done"])
        self.assertFalse(self.todo(2)["done"])
        status, body = self.patch(2, {"done": True})
        self.assertEqual(status, 200, body)
        self.assertTrue(self.todo(2)["done"])

    def test_patch_done_must_be_bool(self):
        self.assertError(self.patch(2, {"done": "no"}), 400, "invalid_todo")
        self.assertTrue(self.todo(2)["done"])

    def test_other_fields_still_rejected(self):
        self.assertError(self.patch(1, {"id": 9}), 400, "invalid_todo")
        self.assertError(self.patch(1, {"created_at": "2020-01-01T00:00:00"}), 400, "invalid_todo")

    # (c)：錯誤格式維持 {"error": {"code", "message"}}
    def test_invalid_input_keeps_error_format(self):
        self.assertError(self.patch(1, {"title": "  "}), 400, "invalid_todo")
        self.assertError(self.patch(1, {"due": "11/01"}), 400, "invalid_todo")
        self.assertError(self.call("PATCH", "/api/todos/1", b"[]"), 400, "invalid_json")
        self.assertError(self.patch(99, {"title": "x"}), 404, "not_found")
        self.assertEqual(self.todo(1)["title"], "買牛奶")


if __name__ == "__main__":
    unittest.main()
