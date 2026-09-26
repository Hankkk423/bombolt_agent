"""bombolt 腳本共用的小工具：找主 checkout、讀專案設定、讀寫 worktree 的 metadata。

只用標準函式庫（python3 >= 3.9），因為這個 plugin 要能直接跑在任何同事的機器上。
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

CONFIG_REL = ".claude/bombolt.md"
WORKTREES_REL = ".claude/worktrees"
META_NAME = "bombolt.json"
ARTIFACTS_DIR = ".bombolt"  # worktree 裡的產物目錄（截圖、進度檔、PR 內文），被 .git/info/exclude 忽略


def git(args: List[str], cwd: str | Path, check: bool = True) -> str:
    """跑一個 git 指令並回傳 stdout（去掉結尾換行）。"""
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失敗：{proc.stderr.strip()}")
    return proc.stdout.rstrip("\n")


def git_ok(args: List[str], cwd: str | Path) -> bool:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True).returncode == 0


def remote_tip(repo: str | Path, branch: str) -> Optional[str]:
    """遠端現在的 tip（即時 ls-remote，不看本機快取）。遠端沒有這個 branch 回 ""；查不到回 None。"""
    p = subprocess.run(["git", "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}"],
                       cwd=str(repo), capture_output=True, text=True)
    if p.returncode == 2:
        return ""
    if p.returncode != 0:
        return None
    return p.stdout.split()[0] if p.stdout.strip() else None


def main_checkout(cwd: str | Path) -> Optional[Path]:
    """回傳這個 repo 的主 checkout 路徑；不在 git repo 裡就回 None。

    在 worktree 裡呼叫也會回到主 checkout（靠 --git-common-dir）。
    """
    try:
        common = git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)
    except (RuntimeError, FileNotFoundError, NotADirectoryError):
        return None
    common_path = Path(common)
    if common_path.name != ".git":
        return None  # bare repo 或特殊配置，不支援
    return common_path.parent


def worktree_git_dir(cwd: str | Path) -> Optional[Path]:
    """回傳目前這個 worktree 自己的 git dir（linked worktree 是 .git/worktrees/<name>）。"""
    try:
        return Path(git(["rev-parse", "--path-format=absolute", "--git-dir"], cwd))
    except (RuntimeError, FileNotFoundError, NotADirectoryError):
        return None


# ---------------------------------------------------------------------------
# 專案設定：<repo>/.claude/bombolt.md 的 frontmatter
# ---------------------------------------------------------------------------


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1]
    return s


def parse_frontmatter(text: str) -> Dict[str, Any]:
    """極簡 frontmatter parser：只支援 `key: value`、`key: [a, b]`、`key:` 加 `- item` 清單。

    刻意不引入 PyYAML（同事的機器不一定有）。設定檔的格式也因此只用這三種形狀。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    data: Dict[str, Any] = {}
    current_list: Optional[List[str]] = None
    for raw in lines[1:]:
        line = raw.rstrip()
        if line.strip() == "---":
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and current_list is not None:
            current_list.append(_strip_quotes(stripped[2:]))
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value == "":
            current_list = []
            data[key] = current_list
        elif value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            data[key] = [_strip_quotes(v) for v in inner.split(",") if v.strip()] if inner else []
            current_list = None
        else:
            data[key] = _strip_quotes(value)
            current_list = None
    return data


def load_config(repo: Path) -> Optional[Dict[str, Any]]:
    """讀主 checkout 的 .claude/bombolt.md；沒有就回 None（代表要先跑 bb-setup）。

    ⚠️ 在 bombolt worktree 裡要用 `load_worktree_config()`：主 checkout 的這個檔會跟著使用者
    切 branch 而變（切到沒有它的 branch 時連安全守門的秘密檔清單都會消失）。
    """
    return _read_config_file(repo / CONFIG_REL)


def _read_config_file(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    cfg = parse_frontmatter(path.read_text(encoding="utf-8"))
    for key in ("protected_branches", "integration_branches", "copy_files", "secret_paths", "deny_commands"):
        value = cfg.get(key)
        if value is None:
            cfg[key] = []
        elif isinstance(value, str):
            cfg[key] = [value]
    return cfg


# 建 worktree 當下的設定快照：放在那個 worktree 自己的 git dir（跟 bombolt.json 同一層，
# 不在工作目錄裡——安全守門讀它，所以不可以放在 agent 能順手改到的地方）。
CONFIG_SNAPSHOT_NAME = "bombolt.config.md"


def config_snapshot_path(worktree: str | Path) -> Optional[Path]:
    gd = worktree_git_dir(worktree)
    return gd / CONFIG_SNAPSHOT_NAME if gd is not None else None


def snapshot_config(repo: Path, worktree: str | Path) -> Optional[Path]:
    """把主 checkout 目前的 .claude/bombolt.md 存成這個 worktree 的設定快照；主 checkout 沒有就回 None。"""
    src, dst = repo / CONFIG_REL, config_snapshot_path(worktree)
    if dst is None or not src.is_file():
        return None
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def copy_config_snapshot(src_worktree: str | Path, dst_worktree: str | Path) -> None:
    """把一個 worktree 的設定快照複製給另一個（整合用的暫存 worktree 沿用 feature 的設定）。"""
    src, dst = config_snapshot_path(src_worktree), config_snapshot_path(dst_worktree)
    if src is not None and dst is not None and src.is_file():
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def load_worktree_config(worktree: str | Path, repo: Path) -> Optional[Dict[str, Any]]:
    """bombolt worktree 用的設定：有快照就讀快照，沒有（快照功能之前建的 worktree）才退回主 checkout。"""
    snap = config_snapshot_path(worktree)
    if snap is not None and snap.is_file():
        return _read_config_file(snap)
    return load_config(repo)


def config_source(worktree: str | Path) -> str:
    snap = config_snapshot_path(worktree)
    return str(snap) if snap is not None and snap.is_file() else ""


# ---------------------------------------------------------------------------
# worktree metadata：放在 linked worktree 自己的 git dir（.git/worktrees/<name>/）
# 不會出現在 git status，worktree 被刪時會跟著一起消失。
# ---------------------------------------------------------------------------


def read_meta(worktree: str | Path) -> Optional[Dict[str, Any]]:
    gd = worktree_git_dir(worktree)
    if gd is None:
        return None
    path = gd / META_NAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_meta(worktree: str | Path, meta: Dict[str, Any]) -> Path:
    gd = worktree_git_dir(worktree)
    if gd is None:
        raise RuntimeError(f"{worktree} 不是 git worktree")
    path = gd / META_NAME
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def worktree_of_session(repo: Path, session_id: str) -> Optional[Path]:
    """哪個 bombolt worktree 是這個 session 建立（或後來接手）的；沒有回 None。"""
    wtdir = repo / WORKTREES_REL
    if not session_id or not wtdir.is_dir():
        return None
    for p in sorted(wtdir.iterdir()):
        meta = read_meta(p) if p.is_dir() else None
        if meta and (meta.get("session_id") == session_id or session_id in meta.get("previous_session_ids", [])):
            return p
    return None


def artifacts_dir(worktree: str | Path) -> Path:
    """截圖、進度檔、PR 內文的位置：`<worktree>/.bombolt/`。

    ⚠️ 不能放在 `.git/worktrees/<name>/` 裡：那是主 checkout 的 .git，Claude Code 的 worktree
    隔離會擋掉對它的寫入（2026-09-23 試跑踩到）。放在 worktree 裡、再用 .git/info/exclude
    忽略，就不會出現在 git status，也不會擋住 `git worktree remove`。
    """
    top = git(["rev-parse", "--show-toplevel"], worktree)
    repo = main_checkout(worktree)
    if repo is not None:
        ensure_local_exclude(repo, f"/{ARTIFACTS_DIR}/")  # 先忽略、再建目錄，避免它變成 untracked
    d = Path(top) / ARTIFACTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def session_name(session_id: str) -> str:
    """從 Claude Code 的本機 session 登記表查 session 名稱（`claude -n` 取的）；查不到回空字串。"""
    for s in claude_sessions():
        if session_id and s.get("sessionId") == session_id and s.get("nameSource") != "derived":
            return s.get("name", "")
    return ""


def ensure_local_exclude(repo: Path, pattern: str) -> bool:
    """把 pattern 加進 .git/info/exclude（只影響本機，不動 repo 的 .gitignore）。回傳是否有新增。"""
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if any(line.strip() == pattern for line in existing.splitlines()):
        return False
    with exclude.open("a", encoding="utf-8") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(f"# bombolt：Claude Code 的 worktree 放這裡\n{pattern}\n")
    return True


# ---------------------------------------------------------------------------
# GitHub 帳號：同一台機器有公司／個人兩個帳號時，依資料夾決定 gh 用哪一個
#
# 對應關係寫在使用者自己的 git config（通常跟 includeIf 放在一起）：
#     [github]
#         user = <GitHub 使用者名稱或 email>
# 沒設定 → 不介入，gh 照常用 active 帳號。
# 有設定但拿不到那個帳號的 token → 報錯，**絕不退回 active 帳號**
# （退回去就是靜默地用錯帳號，正是這個機制要防的事）。
# ---------------------------------------------------------------------------

GH_USER_KEY = "github.user"


class GhAccountError(RuntimeError):
    pass


def real_gh() -> Optional[str]:
    """PATH 上真正的 gh（跳過 bombolt 自己的 bin/）。"""
    own_bin = str(Path(__file__).resolve().parent.parent / "bin")
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d or os.path.realpath(d) == os.path.realpath(own_bin):
            continue
        cand = os.path.join(d, "gh")
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def configured_gh_user(cwd: str | Path) -> str:
    """這個資料夾的 git config 指定的 GitHub 帳號（使用者名稱或 email）；沒設定回空字串。"""
    try:
        return git(["config", "--get", GH_USER_KEY], cwd, check=False).strip()
    except (FileNotFoundError, NotADirectoryError):
        return ""


def _gh_run(gh: str, args: List[str], token: str = "") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("GITHUB_TOKEN", None)
    if token:
        env["GH_TOKEN"] = token
    else:
        env.pop("GH_TOKEN", None)
    return subprocess.run([gh, *args], capture_output=True, text=True, env=env, timeout=30)


def gh_logged_in_users(gh: str) -> List[str]:
    p = _gh_run(gh, ["auth", "status", "--hostname", "github.com", "--json", "hosts"])
    if p.returncode != 0:
        return []
    try:
        hosts = json.loads(p.stdout).get("hosts", {})
    except ValueError:
        return []
    return [a.get("login", "") for a in hosts.get("github.com", []) if a.get("login")]


def gh_token_for(gh: str, login: str) -> str:
    p = _gh_run(gh, ["auth", "token", "--hostname", "github.com", "--user", login])
    token = p.stdout.strip()
    if p.returncode != 0 or not token:
        raise GhAccountError(
            f"gh 裡沒有登入帳號 `{login}`。請在提示列輸入 `! gh auth login`（protocol 選 SSH）登入它。"
        )
    return token


def _emails_cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "bombolt" / "gh-account-emails.json"


def _emails_of(gh: str, login: str, token: str) -> List[str]:
    """查某個帳號的 email：公開 email（/user）＋ 帳號設定裡的 email（/user/emails，需要 user:email scope）。"""
    emails: List[str] = []
    p = _gh_run(gh, ["api", "user", "--jq", ".email // empty"], token)
    if p.returncode == 0 and p.stdout.strip():
        emails.append(p.stdout.strip())
    p = _gh_run(gh, ["api", "user/emails", "--jq", ".[].email"], token)
    if p.returncode == 0:
        emails += [e.strip() for e in p.stdout.splitlines() if e.strip()]
    return sorted({e.lower() for e in emails})


def resolve_gh_login(gh: str, wanted: str) -> str:
    """把 git config 的值（使用者名稱或 email）換成 gh 的帳號名稱。"""
    users = gh_logged_in_users(gh)
    if "@" not in wanted:
        for u in users:
            if u.lower() == wanted.lower():
                return u
        raise GhAccountError(
            f"這個資料夾指定用 GitHub 帳號 `{wanted}`（git config {GH_USER_KEY}），但 gh 裡沒有登入它。"
            f"目前登入的：{', '.join(users) or '（無）'}。請在提示列輸入 `! gh auth login` 登入 `{wanted}`。"
        )
    cache_path = _emails_cache_path()
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    want = wanted.lower()
    for u in users:  # 先看快取
        if want in cache.get(u, []):
            return u
    unresolved = []
    for u in users:
        emails = _emails_of(gh, u, gh_token_for(gh, u))
        if emails:
            cache[u] = emails
        else:
            unresolved.append(u)
        if want in emails:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass
            return u
    hint = ""
    if unresolved:
        hint = (f" 查不到 email 的帳號：{', '.join(unresolved)}——它們的 email 沒有公開，而且 gh 的權限不含 user:email。"
                f"可以執行 `! gh auth refresh -h github.com -s user:email -u <帳號>` 補權限，"
                f"或把 git config {GH_USER_KEY} 改成 GitHub 使用者名稱。")
    raise GhAccountError(f"找不到 email 是 `{wanted}` 的 gh 帳號（目前登入的：{', '.join(users) or '（無）'}）。{hint}")


def gh_account_env(cwd: str | Path) -> Dict[str, Any]:
    """回傳 {'gh': 真 gh 路徑, 'login': 帳號或 '', 'env': 要傳給 gh 的環境變數, 'source': 說明}。

    沒設定 github.user → login 為空、env 就是目前環境（不介入）。
    有設定但解析失敗 → 丟 GhAccountError。
    """
    gh = real_gh()
    if gh is None:
        raise GhAccountError("找不到 gh（GitHub CLI）。請先安裝：`brew install gh`。")
    env = dict(os.environ)
    wanted = configured_gh_user(cwd)
    if not wanted:
        return {"gh": gh, "login": "", "env": env, "source": f"沒有設定 {GH_USER_KEY}，使用 gh 的 active 帳號"}
    login = resolve_gh_login(gh, wanted)
    env.pop("GITHUB_TOKEN", None)
    env["GH_TOKEN"] = gh_token_for(gh, login)
    return {"gh": gh, "login": login, "env": env, "source": f"git config {GH_USER_KEY} = {wanted}"}


# ---------------------------------------------------------------------------
# 這台電腦上是誰在做：Claude Code 的 session 只存在本機，PR 和 issue 要寫清楚它在誰的哪台電腦上
# ---------------------------------------------------------------------------

MACHINE_KEY = "bombolt.machine"


def machine_name(cwd: str | Path) -> str:
    """電腦名稱：git config `bombolt.machine` 優先（讓人取好認的名字），
    否則 macOS 用「關於本機」裡的名稱，其他系統用 hostname。查不到回空字串。"""
    try:
        name = git(["config", "--get", MACHINE_KEY], cwd, check=False).strip()
    except (FileNotFoundError, NotADirectoryError):
        name = ""
    if name:
        return name
    if sys.platform == "darwin":
        try:
            p = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=5)
            if p.returncode == 0 and p.stdout.strip():
                return p.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return platform.node()


def gh_login(cwd: str | Path) -> str:
    """bombolt 在這個資料夾用的 GitHub 帳號；查不到回空字串（不 throw）。"""
    try:
        acct = gh_account_env(cwd)
    except GhAccountError:
        return ""
    if acct["login"]:
        return acct["login"]
    try:
        p = subprocess.run([acct["gh"], "api", "user", "--jq", ".login"], cwd=str(cwd), capture_output=True,
                           text=True, env=acct["env"], timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


def owner_info(cwd: str | Path) -> Dict[str, str]:
    """{'name': git user.name, 'github': GitHub 帳號, 'machine': 電腦名稱}；查不到的欄位是空字串。"""
    try:
        name = git(["config", "--get", "user.name"], cwd, check=False).strip()
    except (FileNotFoundError, NotADirectoryError):
        name = ""
    return {"name": name, "github": gh_login(cwd), "machine": machine_name(cwd)}


def _plain(s: str) -> str:
    """放進 markdown 的名字：拿掉會被當成語法的字元。"""
    return "".join(ch for ch in s if ch not in "<>`|*_[]#@\n").strip()


def owner_text(owner: Dict[str, str]) -> str:
    """PR、issue 上的寫法：**小明**（`ming-gh`）的電腦「小明的 MacBook」。GitHub 帳號包成 inline code，不會 @ 到人。"""
    name, github, machine = _plain(owner.get("name", "")), _plain(owner.get("github", "")), _plain(owner.get("machine", ""))
    who = f"**{name}**" if name else ""
    if github:
        who = f"{who}（`{github}`）" if who else f"`{github}`"
    who = who or "（不知道是誰）"
    return f"{who}的電腦「{machine}」" if machine else f"{who}的電腦"


def owner_short(owner: Dict[str, str]) -> str:
    """一般文字裡稱呼這個人：名字，沒有名字就用 GitHub 帳號。"""
    return _plain(owner.get("name", "")) or _plain(owner.get("github", "")) or "（不知道是誰）"


def same_owner(a: Dict[str, str], b: Dict[str, str]) -> bool:
    """是不是同一台電腦上的同一個人（session 只能在同一台電腦 resume）。"""
    if not a.get("machine") or a.get("machine") != b.get("machine"):
        return False
    if a.get("github") and b.get("github"):
        return a["github"].lower() == b["github"].lower()
    return a.get("name", "") == b.get("name", "")


def pr_base(cfg: Dict[str, Any]) -> str:
    """PR 要開到哪一支：`pr_base` 沒設定就退回 `base_branch`。"""
    return cfg.get("pr_base") or cfg.get("base_branch", "")


def claude_sessions() -> List[Dict[str, Any]]:
    """讀 ~/.claude/sessions/*.json（Claude Code 的本機 session 登記表），只回傳 pid 還活著的。

    ⚠️ 這個目錄不是官方文件記載的介面，格式可能改變；讀不到時回空清單，
    呼叫端要把它當成「補充訊號」，不能當成唯一依據。
    """
    base = Path(os.path.expanduser("~/.claude/sessions"))
    out: List[Dict[str, Any]] = []
    if not base.is_dir():
        return out
    for f in base.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
        except (OSError, ValueError, TypeError):
            continue
        if pid <= 0:
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass  # 程序存在但不是我們的
        out.append(data)
    return out
