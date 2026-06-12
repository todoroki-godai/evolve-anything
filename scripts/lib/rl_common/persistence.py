"""rl-anything プロジェクト識別子 + JSONL 追記。

`project_name_from_dir` / `extract_worktree_info` / `append_jsonl` /
`get_preceding_tool_calls` を提供する。
"""
import json
import sys
from pathlib import Path
from typing import List, Optional

try:
    import fcntl as _fcntl
    _HAVE_FCNTL = True
except ImportError:
    _HAVE_FCNTL = False

# #492: PJ slug 正規化（書込側 basename 固定の根治）の移行日。これより前に書かれた
# sessions.jsonl / usage.jsonl の project は worktree 名（feedback / bots 等）で固定されて
# おり、フルパスが無いため本体 repo 名に遡及復元できない（#489 レビュー）。以後の書込は
# worktree cwd でも本体 repo 名で記録される。#478 の USAGE_RECORDING_FIX_DATE と同型。
PJ_SLUG_NORMALIZATION_DATE = "2026-06-12"


def project_name_from_dir(project_dir: str) -> str:
    """プロジェクトディレクトリパスから worktree 安全な PJ slug を返す（#492）。

    旧実装は素の basename（``Path(project_dir).name``）だったため、worktree cwd
    （``.../.claude/worktrees/<name>``）では worktree 名（feedback / bots 等）が書かれ、
    本体 repo セッションと別 PJ 扱いになっていた（sessions/usage の project 欠落・#489）。

    pj_slug.pj_slug_fast に委譲し、``/.claude/worktrees/`` を切り詰めて本体 repo 名へ
    正規化する。hot path（毎発火 hook）から呼ばれるため subprocess を使わない軽量版を使う。
    解決不能（空 path 等）の場合は素の basename にフォールバックする。
    """
    try:
        _lib = str(Path(__file__).resolve().parent.parent)
        if _lib not in sys.path:
            sys.path.insert(0, _lib)
        from pj_slug import pj_slug_fast

        slug = pj_slug_fast(project_dir)
        if slug:
            return slug
    except Exception:
        pass
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


def get_preceding_tool_calls(
    session_id: str,
    n: int = 5,
    *,
    projects_dir: Optional[Path] = None,
) -> List[dict]:
    """修正直前の同セッション内ツール呼び出し直近 N 件を返す。

    `~/.claude/projects/` 配下の JSONL を走査し、session_id が一致する
    assistant (tool_use) + user (tool_result) のペアを収集する。
    ファイルが存在しない・読み取り失敗・session 未発見の場合は空リストを返す
    （graceful fallback）。

    LLM 呼び出しは行わない（MUST NOT）。

    Args:
        session_id: CC の session_id 文字列。
        n: 最大取得件数（デフォルト 5）。
        projects_dir: テスト用ディレクトリ差し替え用。
            None のとき ~/.claude/projects/ を使用。

    Returns:
        [{"tool": str, "success": bool}, ...] — 時系列昇順（末尾が直前）。
    """
    if projects_dir is None:
        projects_dir = Path.home() / ".claude" / "projects"

    if not projects_dir.is_dir():
        return []

    # session_id が一致するファイルを探す
    # 実際の構造: ~/.claude/projects/<slug>/<session_id>.jsonl（2階層）
    # glob("*/<session_id>.jsonl") で O(slug 数) のスキャンに抑制する
    matches = list(projects_dir.glob(f"*/{session_id}.jsonl"))
    if not matches:
        return []
    target_file = matches[0]

    # tool_use id → tool_name のマッピングと、is_error の収集
    tool_use_map: dict = {}  # tool_use_id -> name
    tool_results: dict = {}  # tool_use_id -> is_error

    try:
        text = target_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    for line in text.splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        rec_session = rec.get("sessionId", "")
        if rec_session != session_id:
            continue

        msg = rec.get("message", {})
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue

        rec_type = rec.get("type", "")
        if rec_type == "assistant":
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_use":
                    tool_id = item.get("id", "")
                    name = item.get("name", "")
                    if tool_id and name:
                        tool_use_map[tool_id] = name
        elif rec_type == "user":
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_result":
                    tool_id = item.get("tool_use_id", "")
                    if tool_id:
                        is_error = bool(item.get("is_error", False))
                        tool_results[tool_id] = is_error

    # tool_use_map のキー順（挿入順 = 時系列）で結果を構築し、末尾 N 件を返す
    entries: List[dict] = []
    for tool_id, tool_name in tool_use_map.items():
        is_error = tool_results.get(tool_id, False)
        entries.append({"tool": tool_name, "success": not is_error})

    return entries[-n:] if len(entries) > n else entries


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
