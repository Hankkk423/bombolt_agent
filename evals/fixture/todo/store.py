"""待辦事項的儲存：整份資料是一個 JSON 檔。

檔案格式：
    {"next_id": 3, "todos": [{"id": 1, "title": "...", "done": false, "due": "2026-10-20", "created_at": "2026-10-01T09:00:00"}]}
"""

import json
import os
from datetime import datetime

from todo.dates import parse_due


class NotFound(Exception):
    pass


class TodoStore:
    def __init__(self, path):
        self.path = path

    def _load(self):
        if not os.path.exists(self.path):
            return {"next_id": 1, "todos": []}
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data):
        # TODO: 改成先寫暫存檔再 os.replace，避免寫到一半當掉留下壞掉的檔案
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def list(self):
        return self._load()["todos"]

    def get(self, todo_id):
        for todo in self.list():
            if todo["id"] == todo_id:
                return todo
        raise NotFound(todo_id)

    def add(self, title, due=None):
        title = (title or "").strip()
        if not title:
            raise ValueError("title 不可以是空的")
        data = self._load()
        todo = {
            "id": data["next_id"],
            "title": title,
            "done": False,
            "due": parse_due(due),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        data["todos"].append(todo)
        data["next_id"] += 1
        self._save(data)
        return todo

    def set_done(self, todo_id, done=True):
        data = self._load()
        for todo in data["todos"]:
            if todo["id"] == todo_id:
                todo["done"] = done
                self._save(data)
                return todo
        raise NotFound(todo_id)

    def delete(self, todo_id):
        data = self._load()
        kept = [t for t in data["todos"] if t["id"] != todo_id]
        if len(kept) == len(data["todos"]):
            raise NotFound(todo_id)
        data["todos"] = kept
        self._save(data)
