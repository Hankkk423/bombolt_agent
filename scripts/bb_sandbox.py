#!/usr/bin/env python3
"""把 PR 的 worktree 裡跑著的 app 暫時開到外網（ngrok），給使用者在外面用手機測。bb-sandbox 用。

用法（在 bombolt worktree 裡）：
  bb_sandbox.py tunnel --port 3012   用 run_in_background 執行：產生這次的帳密，然後直接變成 ngrok（exec）
                                     ——背景工作被停掉、或 session 結束，tunnel 就跟著關，不會留下沒人管的外網網址
  bb_sandbox.py url [--timeout 30]   等 ngrok 連上，印出 {"url","user","password","port","pid"}

外網一定帶 basic-auth（ngrok 的 traffic policy，每次開都換一組隨機密碼），不會把 dev server 不設防地公開。
安全守門在 bombolt worktree 裡擋直接執行 ngrok／cloudflared，所以外網只能從這裡開。
ngrok 免費方案每個帳號只有一個固定網址、同時只能開一個：已經有 ngrok 在跑就不開，回報它轉到哪裡。

輸出一行 JSON（給 skill 讀），錯誤時 exit 1 並在 stderr 說明原因與補救方式。
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bb_lib  # noqa: E402

NGROK_API = os.environ.get("BB_NGROK_API", "http://127.0.0.1:4040/api")  # ngrok agent 的本機 API；測試時換成假的
USER = "bombolt"
STATE_NAME = "sandbox.json"          # <artifacts>/ 裡：這次 tunnel 的 port、帳密、pid
POLICY_NAME = "sandbox-policy.json"  # ngrok 的 traffic policy（YAML 或 JSON 都吃）
LOG_NAME = "sandbox-tunnel.log"      # 檔名刻意不含 ngrok：安全守門擋的是 ngrok 指令，讀這個 log 不該被擋
STARTUP_GRACE = 5                    # 秒


def fail(msg: str) -> None:
    print(f"bombolt: {msg}", file=sys.stderr)
    sys.exit(1)


def artifacts() -> Path:
    meta = bb_lib.read_meta(Path.cwd())
    if not meta or meta.get("tool") != "bombolt":
        fail("目前不在 bombolt 建的 worktree 裡。sandbox 開的是某個 PR 的 worktree，先進到那個 worktree。")
    return bb_lib.artifacts_dir(Path.cwd())


def endpoints() -> Optional[List[Dict[str, str]]]:
    """正在跑的 ngrok 開了哪些網址：[{"url", "upstream"}]。沒有 ngrok 在跑（API 連不上）回 None。"""
    for path in ("endpoints", "tunnels"):  # tunnels 是舊版 ngrok 的 API
        try:
            with urllib.request.urlopen(f"{NGROK_API}/{path}", timeout=2) as r:
                data = json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError:
            continue
        except (OSError, ValueError):
            return None
        return [{"url": i.get("url") or i.get("public_url", ""),
                 "upstream": (i.get("upstream") or {}).get("url") or (i.get("config") or {}).get("addr", "")}
                for i in data.get(path) or []]
    return None


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def last_error(log: Path) -> str:
    """ngrok 的 JSON log 裡最後一個錯誤；沒有回空字串。

    只看 lvl 是 eror／crit 的：正常關閉、檢查更新失敗也會在 info／warn 的行帶 err。
    """
    found = ""
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        try:
            rec: Dict[str, Any] = json.loads(line)
        except ValueError:
            continue
        if rec.get("lvl") in ("eror", "crit"):
            found = str(rec.get("err") or rec.get("msg", "")).strip()
    return found[:500]


def cmd_tunnel(args: argparse.Namespace) -> None:
    art = artifacts()
    ngrok = shutil.which("ngrok")
    if ngrok is None:
        fail("沒有安裝 ngrok。請使用者（每台電腦一次）：`brew install ngrok`，到 https://dashboard.ngrok.com 註冊、"
             "複製 authtoken，再執行 `ngrok config add-authtoken <token>`。")
    running = endpoints()
    if running is not None:
        where = "、".join(f"{e['url']} → {e['upstream']}" for e in running) or "還沒連上"
        fail(f"已經有一個 ngrok 在跑（{where}）。ngrok 免費方案同時只能開一個外網網址："
             f"在開它的 session 說「關掉 sandbox」，或確認不要了再關掉那個 ngrok，然後重來。")

    # 1. 這次的帳密：每次開都換一組，關掉之後就沒用了
    password = secrets.token_urlsafe(12)
    policy = {"on_http_request": [{"actions": [{"type": "basic-auth",
                                                 "config": {"credentials": [f"{USER}:{password}"]}}]}]}
    (art / POLICY_NAME).write_text(json.dumps(policy), encoding="utf-8")
    log = art / LOG_NAME
    log.write_text("", encoding="utf-8")  # 清掉上一次的 log，url 讀到的錯誤才是這一次的

    # 2. exec 之後 pid 不變：記下來給 url 判斷 ngrok 還在不在
    state = {"port": args.port, "user": USER, "password": password, "pid": os.getpid()}
    (art / STATE_NAME).write_text(json.dumps(state), encoding="utf-8")
    print(json.dumps({"status": "starting", "port": args.port, "log": str(log)}, ensure_ascii=False), flush=True)

    # 3. 變成 ngrok 本身：背景工作停掉就是 ngrok 停掉
    os.execv(ngrok, [ngrok, "http", str(args.port), "--traffic-policy-file", str(art / POLICY_NAME),
                     "--log", str(log), "--log-format", "json"])


def read_state(art: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads((art / STATE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def cmd_url(args: argparse.Namespace) -> None:
    art = artifacts()
    log = art / LOG_NAME
    deadline = time.time() + args.timeout
    # tunnel 剛在背景啟動時可能還沒寫好狀態檔（或還是上一次留下的）：寬限一下再下結論
    grace_end = min(time.time() + STARTUP_GRACE, deadline)
    while True:
        state = read_state(art)
        if state and alive(state["pid"]):
            mine = [e for e in endpoints() or [] if e["upstream"].rstrip("/").endswith(f":{state['port']}")]
            if mine:
                print(json.dumps({"status": "ok", "url": mine[0]["url"], "user": state["user"],
                                  "password": state["password"], "port": state["port"], "pid": state["pid"]},
                                 ensure_ascii=False))
                return
        elif time.time() >= grace_end:
            if state is None:
                fail("這個 worktree 還沒開過 tunnel：先用 run_in_background 執行 `bb_sandbox.py tunnel --port <port>`。")
            fail(f"ngrok 沒有在跑：{last_error(log) or '看 tunnel 那個背景工作的輸出'}")
        if time.time() >= deadline:
            fail(f"等了 {args.timeout} 秒 ngrok 還沒連上：{last_error(log) or '看 tunnel 那個背景工作的輸出'}")
        time.sleep(0.5)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tunnel")
    t.add_argument("--port", required=True, type=int)
    t.set_defaults(func=cmd_tunnel)
    u = sub.add_parser("url")
    u.add_argument("--timeout", default=30, type=int)
    u.set_defaults(func=cmd_url)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
