"""命令列介面。

    python3 -m todo.cli --data data/todos.json add "買牛奶" --due 2026-10-20
    python3 -m todo.cli --data data/todos.json list
    python3 -m todo.cli --data data/todos.json done 1
"""

import argparse
import sys

from todo.store import NotFound, TodoStore


def format_line(todo):
    mark = "x" if todo["done"] else " "
    line = f"[{mark}] {todo['id']} {todo['title']}"
    if todo["due"]:
        line += f" (due {todo['due']})"
    return line


def cmd_add(store, args):
    try:
        todo = store.add(args.title, args.due)
    except ValueError as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 1
    print(f"已新增 #{todo['id']}")
    return 0


def cmd_list(store, args):
    todos = store.list()
    if not todos:
        print("還沒有任何待辦事項")
        return 0
    for todo in todos:
        print(format_line(todo))
    return 0


def cmd_done(store, args):
    try:
        store.set_done(args.id)
    except NotFound:
        print(f"錯誤：找不到 #{args.id}", file=sys.stderr)
        return 1
    print(f"已完成 #{args.id}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="todo")
    parser.add_argument("--data", default="data/todos.json", help="資料檔路徑")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="新增待辦")
    add.add_argument("title")
    add.add_argument("--due", help="到期日 YYYY-MM-DD")
    sub.add_parser("list", help="列出全部待辦")
    done = sub.add_parser("done", help="標記完成")
    done.add_argument("id", type=int)
    return parser


COMMANDS = {"add": cmd_add, "list": cmd_list, "done": cmd_done}


def main(argv=None):
    args = build_parser().parse_args(argv)
    return COMMANDS[args.command](TodoStore(args.data), args)


if __name__ == "__main__":
    sys.exit(main())
