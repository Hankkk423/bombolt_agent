"""日期工具。整個專案的日期一律是 `YYYY-MM-DD` 字串。"""

import os
from datetime import date, datetime


def today() -> str:
    """今天的日期。設了環境變數 TODO_TODAY（YYYY-MM-DD）就用它，測試與驗收靠它固定「今天」。"""
    return os.environ.get("TODO_TODAY") or date.today().isoformat()


def parse_due(value):
    """把使用者給的 due 轉成 `YYYY-MM-DD`；空值回 None，格式不對丟 ValueError。"""
    if value is None or value == "":
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError):
        raise ValueError(f"due 的格式要是 YYYY-MM-DD：{value!r}")


def is_overdue(todo, today_str):
    """還沒完成、而且已經過了到期日。"""
    if todo.get("done") or not todo.get("due"):
        return False
    return todo["due"] <= today_str
