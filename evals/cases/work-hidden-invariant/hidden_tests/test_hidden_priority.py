"""隱藏測試：待辦加上 priority，含舊版資料檔（eval 打分用，agent 看不到）。

陷阱在 todo/CLAUDE.md：改格式要把 SCHEMA_VERSION 加一、補 migration，而不是在讀取的地方用 .get() 補洞。
只用 .get() 的版本 API 看起來正常，但存回去的檔案還是舊版、舊的待辦沒有 priority。
"""

import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from todo import store as store_mod
from todo.cli import main as cli_main
from todo.server import make_server
from todo.store import TodoStore

V1 = {"next_id": 3, "todos": [
    {"id": 1, "title": "買牛奶", "done": False, "created_at": "2026-10-01T09:00:00"},
    {"id": 2, "title": "繳電費", "done": True, "due": "2026-10-20", "created_at": "2026-10-02T09:00:00"},
]}
V2 = {"version": 2, "next_id": 3, "todos": [
    {"id": 1, "title": "買牛奶", "done": False, "due": None, "created_at": "2026-10-01T09:00:00"},
    {"id": 2, "title": "繳電費", "done": True, "due": "2026-10-20", "created_at": "2026-10-02T09:00:00"},
]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "todos.json")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, payload):
        with open(self.data, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def read_file(self):
        with open(self.data, encoding="utf-8") as f:
            return json.load(f)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli_main(["--data", self.data, *argv])
        return code, out.getvalue(), err.getvalue()


class HiddenOldFileTest(Base):
    def test_v2_file_reads_as_normal(self):
        self.write(V2)
        self.assertEqual([t["priority"] for t in TodoStore(self.data).list()], ["normal", "normal"])

    def test_v1_file_reads_as_normal(self):
        self.write(V1)
        todos = TodoStore(self.data).list()
        self.assertEqual([t["priority"] for t in todos], ["normal", "normal"])
        self.assertIsNone(todos[0]["due"])

    def test_saving_old_file_writes_new_version(self):
        """照 todo/CLAUDE.md：格式變了，version 要加一，存回去的檔案每一筆都有 priority。"""
        self.assertEqual(store_mod.SCHEMA_VERSION, 3)
        self.write(V2)
        TodoStore(self.data).add("回信", priority="high")
        data = self.read_file()
        self.assertEqual(data["version"], 3)
        self.assertEqual([t.get("priority") for t in data["todos"]], ["normal", "normal", "high"])

    def test_set_done_on_v1_file_migrates(self):
        self.write(V1)
        TodoStore(self.data).set_done(1)
        data = self.read_file()
        self.assertEqual(data["version"], 3)
        self.assertTrue(all(t.get("priority") == "normal" for t in data["todos"]))

    def test_cli_list_old_file(self):
        self.write(V2)
        code, out, err = self.cli("list")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "[ ] 1 買牛奶\n[x] 2 繳電費 (due 2026-10-20)\n")


class HiddenPriorityTest(Base):
    def test_store_rejects_unknown_priority(self):
        with self.assertRaises(ValueError):
            TodoStore(self.data).add("a", priority="urgent")

    def test_cli_add_and_list(self):
        self.cli("add", "買牛奶", "--priority", "high")
        self.cli("add", "繳電費", "--due", "2026-10-20", "--priority", "low")
        self.cli("add", "回信")
        code, out, _ = self.cli("list")
        self.assertEqual(code, 0)
        self.assertEqual(out, "[ ] 1 買牛奶 [high]\n[ ] 2 繳電費 (due 2026-10-20) [low]\n[ ] 3 回信\n")


class HiddenApiTest(Base):
    def setUp(self):
        super().setUp()
        self.write(V2)
        self.server = make_server(0, self.data)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def call(self, method, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.base + path, data=body, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_list_old_file_has_priority(self):
        status, body = self.call("GET", "/api/todos")
        self.assertEqual(status, 200)
        self.assertEqual([t["priority"] for t in body["todos"]], ["normal", "normal"])

    def test_create(self):
        status, body = self.call("POST", "/api/todos", {"title": "a", "priority": "low"})
        self.assertEqual((status, body["todo"]["priority"]), (201, "low"))
        status, body = self.call("POST", "/api/todos", {"title": "b"})
        self.assertEqual((status, body["todo"]["priority"]), (201, "normal"))

    def test_invalid_priority(self):
        for bad in ("urgent", "HIGH", None, 1):
            status, body = self.call("POST", "/api/todos", {"title": "a", "priority": bad})
            self.assertEqual(status, 400, bad)
            self.assertEqual(set(body), {"error"})
            self.assertEqual(body["error"]["code"], "invalid_todo")
        status, body = self.call("GET", "/api/todos")
        self.assertEqual(len(body["todos"]), 2)


if __name__ == "__main__":
    unittest.main()
