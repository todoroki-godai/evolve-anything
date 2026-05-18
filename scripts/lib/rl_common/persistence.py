"""rl-anything プロジェクト識別子 + JSONL 追記。

`project_name_from_dir` / `extract_worktree_info` / `append_jsonl` を提供する。
"""
import json
import sys
from pathlib import Path

try:
    import fcntl as _fcntl
    _HAVE_FCNTL = True
except ImportError:
    _HAVE_FCNTL = False


def project_name_from_dir(project_dir: str) -> str:
    """プロジェクトディレクトリパスから末尾のディレクトリ名を返す。"""
    return Path(project_dir).name


def extract_worktree_info(event: dict) -> dict | None:
    """hook event payload から worktree 情報を抽出する。"""
    wt = event.get("worktree")
    if not isinstance(wt, dict):
        return None
    name = wt.get("name")
    branch = wt.get("branch")
    if not name and not branch:
        return None
    return {"name": name or "", "branch": branch or ""}


def append_jsonl(filepath: Path, record: dict) -> None:
    """JSONL ファイルに1行追記する。新規作成時はパーミッション 600 を設定。失敗時はサイレント。"""
    is_new = False
    try:
        with open(filepath, "a", encoding="utf-8") as f:
            if _HAVE_FCNTL:
                _fcntl.flock(f, _fcntl.LOCK_EX)  # ブロッキング取得（意図的）
            try:
                is_new = f.tell() == 0  # flock 取得後に判定し TOCTOU を回避
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            finally:
                if _HAVE_FCNTL:
                    _fcntl.flock(f, _fcntl.LOCK_UN)
        if is_new:
            try:
                filepath.chmod(0o600)
            except OSError as e:
                print(f"[rl-anything] chmod file warning: {e}", file=sys.stderr)
    except OSError as e:
        print(f"[rl-anything] write failed: {e}", file=sys.stderr)
