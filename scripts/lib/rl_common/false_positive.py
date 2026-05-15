"""rl-anything 偽陽性フィードバック管理。

`message_hash` / `load_false_positives` / `add_false_positive` /
`cleanup_false_positives` を提供する。
`FALSE_POSITIVES_FILE` は ``rl_common.__init__`` を SoT として
``hooks/tests/conftest.py`` の ``mock.patch.object(rl_common,
"FALSE_POSITIVES_FILE", ...)`` 経路を維持する（本モジュールは
関数本体内で ``import rl_common`` 経由で動的 lookup する）。
"""
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone

_FALSE_POSITIVE_EXPIRY_DAYS = 180


def message_hash(text: str) -> str:
    """メッセージの SHA-256 ハッシュを返す。"""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def load_false_positives() -> set[str]:
    """false_positives.jsonl から message_hash のセットを読み込む。"""
    import rl_common as _root  # late binding for mock.patch.object(rl_common, "FALSE_POSITIVES_FILE", ...)
    fp_file = _root.FALSE_POSITIVES_FILE
    if not fp_file.exists():
        return set()
    try:
        hashes = set()
        for line in fp_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                h = record.get("message_hash")
                if h:
                    hashes.add(h)
            except json.JSONDecodeError:
                continue
        return hashes
    except OSError as e:
        print(f"[rl-anything] load_false_positives warning: {e}", file=sys.stderr)
        return set()


def add_false_positive(msg: str, correction_type: str) -> None:
    """偽陽性をファイルに追記する。"""
    import rl_common as _root
    _root.ensure_data_dir()
    record = {
        "message_hash": message_hash(msg),
        "original_type": correction_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _root.append_jsonl(_root.FALSE_POSITIVES_FILE, record)


def cleanup_false_positives() -> int:
    """180日超のエントリを false_positives.jsonl から削除する。削除件数を返す。"""
    import rl_common as _root
    fp_file = _root.FALSE_POSITIVES_FILE
    if not fp_file.exists():
        return 0
    try:
        lines = fp_file.read_text(encoding="utf-8").splitlines()
        cutoff = datetime.now(timezone.utc) - timedelta(days=_FALSE_POSITIVE_EXPIRY_DAYS)
        kept = []
        removed = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                ts_str = record.get("timestamp", "")
                if ts_str:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts < cutoff:
                        removed += 1
                        continue
                kept.append(json.dumps(record, ensure_ascii=False))
            except (json.JSONDecodeError, ValueError):
                kept.append(line)
        if removed > 0:
            fp_file.write_text("\n".join(kept) + "\n" if kept else "", encoding="utf-8")
        return removed
    except OSError as e:
        print(f"[rl-anything] cleanup_false_positives warning: {e}", file=sys.stderr)
        return 0
