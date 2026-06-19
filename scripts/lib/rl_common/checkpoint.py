"""evolve-anything checkpoint 管理。

`find_latest_checkpoint` / `_load_legacy_checkpoint` / `cleanup_old_checkpoints`
を提供する。`DATA_DIR` / `CHECKPOINTS_DIR` / `CHECKPOINT_TTL_HOURS` は
``rl_common.__init__`` を SoT として保持する（``hooks/tests/conftest.py`` の
``mock.patch.object(rl_common, "DATA_DIR", ...)`` などテストパッチ追従のため
本モジュールは関数本体内で ``import rl_common`` 経由で動的 lookup する）。
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path


def find_latest_checkpoint(project_dir: str | None = None) -> dict | None:
    """checkpoints/ から project_dir に一致する最新の checkpoint を返す。

    project_dir が指定されている場合、一致する checkpoint のみを候補にする。
    候補がなければ旧 DATA_DIR/checkpoint.json にフォールバック（後方互換）。
    """
    import rl_common as _root  # late binding to honor mock.patch.object(rl_common, "CHECKPOINTS_DIR", ...)
    checkpoints_dir = _root.CHECKPOINTS_DIR
    if not checkpoints_dir.exists():
        return None if project_dir else _load_legacy_checkpoint()
    candidates = []
    for f in checkpoints_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if project_dir:
            cp_project = data.get("project_dir", "")
            if not cp_project:
                continue
            try:
                if str(Path(cp_project).resolve()) != str(Path(project_dir).resolve()):
                    continue
            except OSError:
                continue
        candidates.append(data)
    if not candidates:
        return None if project_dir else _load_legacy_checkpoint()

    def _parse_ts(ts: str) -> "datetime":
        try:
            return datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            return datetime.min.replace(tzinfo=timezone.utc)

    candidates.sort(key=lambda d: _parse_ts(d.get("timestamp", "")), reverse=True)
    return candidates[0]


def _load_legacy_checkpoint() -> dict | None:
    """旧 DATA_DIR/checkpoint.json を読み込む（後方互換）。"""
    import rl_common as _root
    legacy = _root.DATA_DIR / "checkpoint.json"
    if not legacy.exists():
        return None
    try:
        return json.loads(legacy.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def cleanup_old_checkpoints() -> None:
    """TTL 超過の checkpoint ファイルを削除する。"""
    import rl_common as _root
    checkpoints_dir = _root.CHECKPOINTS_DIR
    ttl_hours = _root.CHECKPOINT_TTL_HOURS
    if not checkpoints_dir.exists():
        return
    cutoff = time.time() - ttl_hours * 3600
    for f in checkpoints_dir.glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            continue
