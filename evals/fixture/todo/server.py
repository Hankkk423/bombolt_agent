"""HTTP 服務：JSON API（/api/todos）＋ 網頁（/）。

啟動：python3 -m todo.server --port 8000 --data data/todos.json
"""

import argparse
import html
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from todo.dates import is_overdue, today
from todo.store import NotFound, TodoStore

PAGE = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>待辦清單</title>
<style>
body {{ font-family: sans-serif; max-width: 640px; margin: 40px auto; color: #222; }}
ul {{ padding-left: 0; list-style: none; }}
li {{ padding: 8px 0; border-bottom: 1px solid #eee; }}
li.done .title {{ text-decoration: line-through; color: #888; }}
.due {{ color: #666; font-size: 0.9em; margin-left: 8px; }}
.overdue {{ color: #c00; font-weight: bold; margin-left: 8px; }}
form.inline {{ display: inline; margin-left: 8px; }}
</style>
</head>
<body>
<h1>待辦清單</h1>
{items}
<form method="post" action="/todos">
  <input name="title" placeholder="要做什麼？" required>
  <input name="due" type="date">
  <button type="submit">新增</button>
</form>
</body>
</html>
"""


def error_body(code, message):
    return {"error": {"code": code, "message": message}}


def to_json(todo, today_str):
    return dict(todo, overdue=is_overdue(todo, today_str))


def render_item(todo, today_str):
    parts = [f'<span class="title">{html.escape(todo["title"])}</span>']
    if todo["due"]:
        parts.append(f'<span class="due">到期 {todo["due"]}</span>')
    if is_overdue(todo, today_str):
        parts.append('<span class="overdue">逾期</span>')
    if not todo["done"]:
        parts.append(f'<form class="inline" method="post" action="/todos/{todo["id"]}/done">'
                     '<button type="submit">完成</button></form>')
    css = "done" if todo["done"] else "pending"
    return f'<li class="{css}">' + "".join(parts) + "</li>"


def render_index(todos, today_str):
    if not todos:
        items = '<p class="empty">還沒有任何待辦事項</p>'
    else:
        items = "<ul>\n" + "\n".join(render_item(t, today_str) for t in todos) + "\n</ul>"
    return PAGE.format(items=items)


class Handler(BaseHTTPRequestHandler):
    store = None  # 由 make_server 設定

    def log_message(self, fmt, *args):
        if os.environ.get("TODO_ACCESS_LOG"):
            super().log_message(fmt, *args)

    def _send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status, payload):
        self._send(status, "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _todo_id(self, raw):
        try:
            return int(raw)
        except ValueError:
            return None

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/":
            self._send(200, "text/html; charset=utf-8", render_index(self.store.list(), today()).encode("utf-8"))
        elif url.path == "/api/todos":
            self.list_todos(parse_qs(url.query))
        else:
            self._send_json(404, error_body("not_found", "找不到這個路徑"))

    def list_todos(self, query):
        today_str = today()
        todos = self.store.list()
        self._send_json(200, {"todos": [to_json(t, today_str) for t in todos]})

    def do_POST(self):
        url = urlsplit(self.path)
        parts = url.path.strip("/").split("/")
        if url.path == "/api/todos":
            try:
                payload = json.loads(self._read_body() or b"{}")
            except ValueError:
                payload = None
            if not isinstance(payload, dict):
                self._send_json(400, error_body("invalid_json", "request body 要是 JSON 物件"))
                return
            try:
                todo = self.store.add(payload.get("title"), payload.get("due"))
            except ValueError as e:
                self._send_json(400, error_body("invalid_todo", str(e)))
                return
            self._send_json(201, {"todo": to_json(todo, today())})
        elif len(parts) == 4 and parts[:2] == ["api", "todos"] and parts[3] == "done":
            todo_id = self._todo_id(parts[2])
            try:
                todo = self.store.set_done(todo_id)
            except NotFound:
                self._send_json(404, error_body("not_found", f"找不到 #{parts[2]}"))
                return
            self._send_json(200, {"todo": to_json(todo, today())})
        elif url.path == "/todos":
            form = {k: v[0] for k, v in parse_qs(self._read_body().decode("utf-8")).items()}
            try:
                self.store.add(form.get("title"), form.get("due"))
            except ValueError:
                pass  # 網頁表單的錯誤先忽略，重新整理頁面即可
            self._redirect("/")
        elif len(parts) == 3 and parts[0] == "todos" and parts[2] == "done":
            try:
                self.store.set_done(self._todo_id(parts[1]))
            except NotFound:
                pass
            self._redirect("/")
        else:
            self._send_json(404, error_body("not_found", "找不到這個路徑"))

    def do_DELETE(self):
        parts = urlsplit(self.path).path.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["api", "todos"]:
            try:
                self.store.delete(self._todo_id(parts[2]))
            except NotFound:
                self._send_json(404, error_body("not_found", f"找不到 #{parts[2]}"))
                return
            self._send(204, "text/plain", b"")
        else:
            self._send_json(404, error_body("not_found", "找不到這個路徑"))


def make_server(port, data_path, host="127.0.0.1"):
    """建立 server（還沒開始服務）。port 給 0 會挑一個空的 port，測試用。"""
    handler = type("BoundHandler", (Handler,), {"store": TodoStore(data_path)})
    return ThreadingHTTPServer((host, port), handler)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="todo.server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--data", default="data/todos.json", help="資料檔路徑")
    args = parser.parse_args(argv)
    server = make_server(args.port, args.data)
    print(f"ready on http://localhost:{server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
