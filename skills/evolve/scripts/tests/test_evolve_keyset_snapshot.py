"""#531 キー集合 snapshot — フェーズ分割が result の構造を変えないことを保証する。

純リファクタ（evolve.py → evolve/ パッケージ分割）の各 PR で、実 `run_evolve(dry_run=True)`
出力の **dict キー集合**が抽出前後で bit-identical であることを assert する。値は timestamp /
generated_at / 件数で揺れるため **dict のキーのみ再帰**（list 要素はスキップ）し golden 化する。

golden は `fixtures/evolve_keyset_snapshot.txt`（sorted・1 行 1 key）。drift（キー増減）時は
意図した変更なら `UPDATE_SNAPSHOTS=1 pytest ...` で再生成する（API surface snapshot と同流儀）。
CANONICAL 契約（test_evolve_result_schema.py）が型を、本 snapshot がキー集合の全体像を守る。

HOME 隔離は conftest autouse（#457）。実 ~/.claude/projects を走査させない。

【本テストは本 PJ 実データ対象】``run_evolve(project_dir=_PLUGIN_ROOT)`` は合成 fixture でなく
evolve-anything 自身の実スキル構成に対して実行する。そのため一部のキーは「非空時のみ追加」
（例: archive 候補との衝突で抑制されたスキルがある時だけ追加される
``split_suppressed_by_archive`` / ``evolve_suppressed_by_archive``）または「条件成立時のみ
サブキーが展開される」（例: 計測窓 suppress が有効な間だけ中身を持つ
``zero_invocations_suppressed``）設計になっており、実行時刻や本 PJ 自身のスキル増減で
出たり消えたりする。これは regression ではなく **意図された条件付き透明化キー**。

こうした「条件付き透明化キー」は `fixtures/evolve_keyset_optional.txt` に prefix 宣言する
（1 行 1 prefix、dotted path の完全一致 or `<prefix>.` で始まるサブキーにマッチ）。宣言済み
prefix に一致するキーの増減は許容し、それ以外の増減のみ regression として fail する
（構造 drift 検知の実効性は維持）。

**新しいキーを追加する開発者向けの運用**:
  - 常に出るキー（条件なし）: 通常通り `UPDATE_SNAPSHOTS=1` で golden に追加すればよい。
  - 「非空時のみ追加」等の条件付きキーを増やす場合: (1) `evolve_keyset_optional.txt` に
    prefix を追記し、(2) `UPDATE_SNAPSHOTS=1` で golden を再生成する。
  - `UPDATE_SNAPSHOTS=1` は golden を実測キーで **上書きしない**。既存 golden との
    **union（和集合）で merge** する。条件付きキーは実行時にたまたま出ない回もあるため、
    上書きすると golden からキーが消えてしまい optional 管理が壊れる。golden からキーを
    完全に退役させたい場合は golden ファイルを手で編集する。
"""
import os
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Set

_SCRIPTS = Path(__file__).resolve().parent.parent
_PLUGIN_ROOT = _SCRIPTS.parent.parent.parent
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_PLUGIN_ROOT / "scripts" / "lib"))
sys.path.insert(0, str(_PLUGIN_ROOT / "scripts" / "rl"))

import evolve  # noqa: E402

_GOLDEN = Path(__file__).resolve().parent / "fixtures" / "evolve_keyset_snapshot.txt"
_OPTIONAL = Path(__file__).resolve().parent / "fixtures" / "evolve_keyset_optional.txt"


def _walk_keys(obj, prefix=""):
    """dict のキーのみを dotted path で再帰収集する（list はスキップ＝要素数で割れない）。"""
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}{k}"
            out.add(key)
            out |= _walk_keys(v, f"{key}.")
    return out


def _read_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _is_optional_key(key: str, optional_prefixes: Iterable[str]) -> bool:
    """key が宣言済み optional prefix に一致するか（完全一致 or サブキー）を判定する。"""
    for prefix in optional_prefixes:
        if key == prefix or key.startswith(f"{prefix}."):
            return True
    return False


def _classify_drift(
    golden: Set[str], keys: Set[str], optional_prefixes: Iterable[str]
) -> Dict[str, List[str]]:
    """golden と実測 keys の差分を「optional 宣言済み」「それ以外（hard）」の4象限に分類する。

    hard な missing/added のみが regression 扱い（テスト fail 対象）。optional 一致分は
    条件付きキーの自然な出入りとして許容し、observability として件数だけ残す。
    """
    optional_prefixes = list(optional_prefixes)
    missing = golden - keys
    added = keys - golden
    return {
        "missing_hard": sorted(k for k in missing if not _is_optional_key(k, optional_prefixes)),
        "added_hard": sorted(k for k in added if not _is_optional_key(k, optional_prefixes)),
        "missing_optional": sorted(k for k in missing if _is_optional_key(k, optional_prefixes)),
        "added_optional": sorted(k for k in added if _is_optional_key(k, optional_prefixes)),
    }


def test_classify_drift_four_quadrants():
    """optional 一致/不一致 × 追加/欠落 の4象限を純関数で検証する（実 evolve run 不要）。"""
    golden = {
        "a.stable",
        "a.optional_thing",
        "a.optional_thing.detail",
        "b.will_vanish",
    }
    keys = {
        "a.stable",
        "a.new_hard_key",
        "a.optional_thing2",
    }
    optional_prefixes = ["a.optional_thing", "a.optional_thing2"]

    result = _classify_drift(golden, keys, optional_prefixes)

    # 象限1: hard 追加（optional 未宣言の新規キー）→ regression として検知されるべき
    assert result["added_hard"] == ["a.new_hard_key"]
    # 象限2: hard 欠落（optional 未宣言の消失キー）→ regression として検知されるべき
    assert result["missing_hard"] == ["b.will_vanish"]
    # 象限3: optional 一致の追加（宣言済み prefix の新規サブキー）→ 許容
    assert result["added_optional"] == ["a.optional_thing2"]
    # 象限4: optional 一致の欠落（宣言済み prefix が今回出なかった）→ 許容
    assert result["missing_optional"] == ["a.optional_thing", "a.optional_thing.detail"]


def test_is_optional_key_matches_exact_and_subkeys_only():
    """prefix は完全一致 or `<prefix>.` で始まるサブキーにのみマッチし、文字列部分一致はしない。"""
    prefixes = ["phases.prune.zero_invocations_suppressed"]
    assert _is_optional_key("phases.prune.zero_invocations_suppressed", prefixes)
    assert _is_optional_key("phases.prune.zero_invocations_suppressed.auto_reeval", prefixes)
    # 文字列としては前方一致するが別キーである他キーを誤マッチしない
    assert not _is_optional_key("phases.prune.zero_invocations_suppressed_v2", prefixes)
    assert not _is_optional_key("phases.prune.zero_invocations", prefixes)


def test_evolve_result_keyset_matches_snapshot():
    """実 dry-run result のキー集合が golden と一致する（条件付きキーは optional 宣言分だけ許容）。"""
    result = evolve.run_evolve(project_dir=str(_PLUGIN_ROOT), dry_run=True)
    keys = sorted(_walk_keys(result))

    assert _GOLDEN.exists(), (
        f"golden 未生成: UPDATE_SNAPSHOTS=1 で {_GOLDEN.name} を再生成してください"
    )
    golden = set(_read_lines(_GOLDEN))
    optional_prefixes = _read_lines(_OPTIONAL)

    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        # 上書きでなく union merge。条件付きキーは今回たまたま出ない回もあるため、
        # 上書きすると golden から消えて optional 管理が壊れる（設計判断・要確認済み）。
        merged = sorted(golden | set(keys))
        _GOLDEN.write_text("\n".join(merged) + "\n", encoding="utf-8")
        golden = set(merged)

    drift = _classify_drift(golden, set(keys), optional_prefixes)
    assert not drift["missing_hard"] and not drift["added_hard"], (
        f"result キー集合が drift しました（純リファクタでは不変のはず）。\n"
        f"  欠落(hard): {drift['missing_hard']}\n  追加(hard): {drift['added_hard']}\n"
        f"  欠落(optional・許容): {drift['missing_optional']}\n"
        f"  追加(optional・許容): {drift['added_optional']}\n"
        f"意図した hard な変更なら UPDATE_SNAPSHOTS=1 で {_GOLDEN.name} を再生成。\n"
        f"条件付きキーを新設する場合は {_OPTIONAL.name} に prefix を追記してから再生成。"
    )
