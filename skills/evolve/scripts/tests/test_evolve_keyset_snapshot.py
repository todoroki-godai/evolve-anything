"""#531 キー集合 snapshot — フェーズ分割が result の構造を変えないことを保証する。

純リファクタ（evolve.py → evolve/ パッケージ分割）の各 PR で、実 `run_evolve(dry_run=True)`
出力の **dict キー集合**が抽出前後で bit-identical であることを assert する。値は timestamp /
generated_at / 件数で揺れるため **dict のキーのみ再帰**（list 要素はスキップ）し golden 化する。

golden は `fixtures/evolve_keyset_snapshot.txt`（sorted・1 行 1 key）。drift（キー増減）時は
意図した変更なら `UPDATE_SNAPSHOTS=1 pytest ...` で再生成する（API surface snapshot と同流儀）。
CANONICAL 契約（test_evolve_result_schema.py）が型を、本 snapshot がキー集合の全体像を守る。

HOME 隔離は conftest autouse（#457）。実 ~/.claude/projects を走査させない。
"""
import os
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
_PLUGIN_ROOT = _SCRIPTS.parent.parent.parent
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_PLUGIN_ROOT / "scripts" / "lib"))
sys.path.insert(0, str(_PLUGIN_ROOT / "scripts" / "rl"))

import evolve  # noqa: E402

_GOLDEN = Path(__file__).resolve().parent / "fixtures" / "evolve_keyset_snapshot.txt"


def _walk_keys(obj, prefix=""):
    """dict のキーのみを dotted path で再帰収集する（list はスキップ＝要素数で割れない）。"""
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}{k}"
            out.add(key)
            out |= _walk_keys(v, f"{key}.")
    return out


def test_evolve_result_keyset_matches_snapshot():
    """実 dry-run result のキー集合が golden と一致する（リファクタで構造が変わらない）。"""
    result = evolve.run_evolve(project_dir=str(_PLUGIN_ROOT), dry_run=True)
    keys = sorted(_walk_keys(result))

    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        _GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        _GOLDEN.write_text("\n".join(keys) + "\n", encoding="utf-8")

    assert _GOLDEN.exists(), (
        f"golden 未生成: UPDATE_SNAPSHOTS=1 で {_GOLDEN.name} を再生成してください"
    )
    golden = [ln for ln in _GOLDEN.read_text(encoding="utf-8").splitlines() if ln.strip()]
    missing = sorted(set(golden) - set(keys))
    added = sorted(set(keys) - set(golden))
    assert not missing and not added, (
        f"result キー集合が drift しました（純リファクタでは不変のはず）。\n"
        f"  欠落: {missing}\n  追加: {added}\n"
        f"意図した変更なら UPDATE_SNAPSHOTS=1 で {_GOLDEN.name} を再生成。"
    )
