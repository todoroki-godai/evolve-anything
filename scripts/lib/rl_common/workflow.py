"""rl-anything ワークフロー文脈 + スキルスタック + 直前スキル管理。

`workflow_context_path` / `skill_stack_path` / `read_skill_stack` /
`write_skill_stack` / `read_workflow_context` / `last_skill_path` /
`write_last_skill` / `read_last_skill` を提供する。

TMPDIR 配下の一時ファイルを扱うため DATA_DIR には依存しない。
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ワークフロー文脈ファイルの有効期限（秒）
_WORKFLOW_CONTEXT_EXPIRE_SECONDS = 24 * 60 * 60  # 24時間


def workflow_context_path(session_id: str) -> Path:
    """ワークフロー文脈ファイルのパスを返す。"""
    tmpdir = os.environ.get("TMPDIR", "/tmp")
    return Path(tmpdir) / f"rl-anything-workflow-{session_id}.json"


def skill_stack_path(session_id: str) -> Path:
    """スキル呼び出しスタックファイルのパスを返す。

    スタックは [{skill_name, workflow_id, started_at}, ...] のリスト。
    末尾が現在実行中のスキル。PreToolUse で push、PostToolUse で pop。
    観察対象: Skill ツールのみ（Bash/Read 等は含まない）。
    """
    tmpdir = os.environ.get("TMPDIR", "/tmp")
    return Path(tmpdir) / f"rl-anything-skill-stack-{session_id}.json"


def read_skill_stack(session_id: str) -> list:
    """スキルスタックを読み込む。存在しない・破損時は空リストを返す。"""
    path = skill_stack_path(session_id)
    try:
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def write_skill_stack(session_id: str, stack: list) -> None:
    """スキルスタックをアトミックに書き込む。空の場合はファイルを削除する。"""
    path = skill_stack_path(session_id)
    if not stack:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(stack, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_workflow_context(session_id: str) -> dict:
    """ワークフロー文脈ファイルを読み取り parent_skill/workflow_id を返す。

    文脈ファイルが存在しない、24時間以上経過、破損の場合は
    {"parent_skill": null, "workflow_id": null} を返す。
    セッションをブロックしない（MUST NOT）。
    """
    null_result = {"parent_skill": None, "workflow_id": None}
    try:
        ctx_path = workflow_context_path(session_id)
        if not ctx_path.exists():
            return null_result

        mtime = ctx_path.stat().st_mtime
        age = datetime.now(timezone.utc).timestamp() - mtime
        if age > _WORKFLOW_CONTEXT_EXPIRE_SECONDS:
            return null_result

        ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
        return {
            "parent_skill": ctx.get("skill_name"),
            "workflow_id": ctx.get("workflow_id"),
        }
    except Exception as e:
        print(f"[rl-anything] read_workflow_context error: {e}", file=sys.stderr)
        return null_result


def last_skill_path(session_id: str) -> Path:
    """直前スキル記録ファイルのパスを返す。"""
    tmpdir = os.environ.get("TMPDIR", "/tmp")
    return Path(tmpdir) / f"rl-anything-last-skill-{session_id}.json"


def write_last_skill(session_id: str, skill_name: str) -> None:
    """直前スキル名を一時ファイルに書き出す。"""
    try:
        path = last_skill_path(session_id)
        data = {"skill_name": skill_name, "timestamp": datetime.now(timezone.utc).isoformat()}
        path.write_text(json.dumps(data), encoding="utf-8")
    except Exception as e:
        print(f"[rl-anything] write_last_skill error: {e}", file=sys.stderr)


def read_last_skill(session_id: str) -> str | None:
    """直前スキル名を一時ファイルから読み取る。TTL 24時間。"""
    try:
        path = last_skill_path(session_id)
        if not path.exists():
            return None
        mtime = path.stat().st_mtime
        age = datetime.now(timezone.utc).timestamp() - mtime
        if age > _WORKFLOW_CONTEXT_EXPIRE_SECONDS:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("skill_name")
    except Exception as e:
        print(f"[rl-anything] read_last_skill error: {e}", file=sys.stderr)
        return None
