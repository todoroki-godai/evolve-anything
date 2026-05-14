"""discover の抑制リスト / JSONL ローダ / バリデータ / トークン抽出ヘルパ。

discover/__init__.py から re-export される（後方互換）。
SUPPRESSION_FILE は DATA_DIR を遅延参照（テスト patch 追従）。
"""
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from line_limit import MAX_RULE_LINES, MAX_SKILL_LINES
from similarity import tokenize


def load_jsonl(filepath: Path) -> List[Dict[str, Any]]:
    """JSONL ファイルを読み込む。"""
    if not filepath.exists():
        return []
    records = []
    for line in filepath.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _suppression_file() -> Path:
    """SUPPRESSION_FILE を package attribute 経由で遅延参照する。

    既存テストは `mock.patch.object(discover, "SUPPRESSION_FILE", ...)` で
    パッケージ属性そのものを差し替える。Bare import では import-time に値が
    固定されてしまうため、毎回パッケージモジュールから取り出す。
    """
    from . import SUPPRESSION_FILE as _f  # noqa: PLC0415
    return _f


def load_suppression_list() -> set:
    """抑制リスト（2回 reject されたパターン）を読み込む。

    type: "merge" エントリは除外し、type 未指定エントリのみを返す。
    """
    records = load_jsonl(_suppression_file())
    return set(r.get("pattern", "") for r in records if r.get("type") != "merge")


def load_merge_suppression() -> set:
    """merge suppression リスト（type: "merge" エントリ）を読み込み、ペアキーの set を返す。"""
    records = load_jsonl(_suppression_file())
    return set(r.get("pattern", "") for r in records if r.get("type") == "merge")


def add_merge_suppression(skill_a: str, skill_b: str) -> None:
    """merge suppression エントリを追加する。スキル名をソートし :: 結合で正規化。

    書き込み失敗時は stderr にエラー出力し、例外を送出しない。
    """
    from . import DATA_DIR
    key = "::".join(sorted([skill_a, skill_b]))
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(_suppression_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps({"pattern": key, "type": "merge"}, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[rl-anything] merge suppression write failed: {e}", file=sys.stderr)


def add_to_suppression_list(pattern: str) -> None:
    """抑制リストにパターンを追加する。"""
    from . import DATA_DIR
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_suppression_file(), "a", encoding="utf-8") as f:
        f.write(json.dumps({"pattern": pattern}, ensure_ascii=False) + "\n")


def validate_skill_content(content: str) -> bool:
    """スキル候補の構造バリデーション（MUST 500行以下）。"""
    lines = content.count("\n") + 1
    return lines <= MAX_SKILL_LINES


def validate_rule_content(content: str) -> bool:
    """ルール候補の構造バリデーション（MUST 3行以内）。"""
    lines = content.count("\n") + 1
    return lines <= MAX_RULE_LINES


def load_claude_reflect_data() -> List[Dict[str, Any]]:
    """corrections.jsonl から pending の修正データのみ取り込む。未生成時はスキップ。

    reflect が処理するのは pending のみであるため、
    evolve の reflect_data_count と reflect の認識を一致させる。
    """
    from . import DATA_DIR
    corrections_file = DATA_DIR / "corrections.jsonl"

    if not corrections_file.exists():
        return []

    records = load_jsonl(corrections_file)
    return [r for r in records if r.get("reflect_status", "pending") == "pending"]


def _load_skill_tokens(skill_path: Path) -> Dict[str, Any]:
    """SKILL.md の先頭 50 行 + スキル名からトークン集合を生成する。"""
    from typing import Set as _Set

    tokens: _Set[str] = set()
    skill_name = skill_path.parent.name
    tokens |= tokenize(skill_name)

    try:
        lines = skill_path.read_text(encoding="utf-8").splitlines()[:50]
        for line in lines:
            tokens |= tokenize(line)
    except OSError:
        pass

    return {"path": skill_path, "name": skill_name, "tokens": tokens}


def _load_classify_usage_skill():
    """audit.py の _is_plugin_skill と classify_usage_skill を遅延インポートで取得する。

    Returns:
        _is_plugin_skill 関数（classify_usage_skill + _is_gstack_skill + _is_openspec_skill の併用）
    """
    import sys as _sys
    from plugin_root import PLUGIN_ROOT
    _audit_scripts = PLUGIN_ROOT / "skills" / "audit" / "scripts"
    if str(_audit_scripts) not in _sys.path:
        _sys.path.insert(0, str(_audit_scripts))
    from audit import _is_plugin_skill, classify_usage_skill
    return _is_plugin_skill, classify_usage_skill
