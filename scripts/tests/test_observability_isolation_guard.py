"""観測ビルダーの隔離漏れ構造ガード（#471）。

背景（#464 同型バグ・同型4回目）:
``scripts/tests/test_audit_snapshot.py`` の ``_isolate_env`` は、observability ビルダーが
import 時に ``Path.home()`` 由来で固定する module-level 定数（``DATA_DIR`` /
``CORRECTIONS_FILE`` 等）を **手動列挙** で隔離している。これらは ``from rl_common import
DATA_DIR`` のような **bound copy**（import 時に値を凍結）なので、``setenv("HOME")`` や
``rl_common`` の reload だけでは置き換わらず、各モジュールを明示 setattr / reload しないと
snapshot テストが実データを読む（#464 で実発生）。

新しい observability ビルダーを ``_OBSERVABILITY_BUILDERS`` に足したとき、その供給モジュールが
``~/.claude`` 配下を指す module-level ``Path`` 定数を持つのに ``_isolate_env`` を更新し忘れると、
同じ事故が再発する。このガードは:

  1. ``_OBSERVABILITY_BUILDERS`` の各 builder が属するモジュール（``audit.sections*``）を起点に、
     そのモジュールが import する（module-level / 関数内 lazy 双方）供給モジュールを AST で走査。
  2. 各供給モジュールの module-level 属性のうち、実 ``~/.claude`` 配下を指す ``pathlib.Path``
     インスタンスを列挙。
  3. ``_isolate_env`` が中和する既知リスト（``_KNOWN_ISOLATED``）に含まれない属性があれば fail し、
     「どのモジュールのどの属性を ``_isolate_env`` に追加すべきか」を指示する。

``_KNOWN_ISOLATED`` は ``_isolate_env`` が実際に中和する (module, attr) の単一管理リスト。
``setattr`` で直接差し替えるもの・``reload`` 後に env/HOME 基準で再解決されるもの両方を含む。
"""
import ast
import importlib
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_SCRIPTS = _PLUGIN_ROOT / "scripts"
_FE_SCRIPTS = _PLUGIN_ROOT / "skills" / "evolve-fitness" / "scripts"
for _p in (_LIB, _SCRIPTS, _FE_SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from audit import observability as _obs  # noqa: E402


# 実 ~/.claude（データ home）。Path.home() 全体ではなく ~/.claude に絞ることで、
# worktree が偶々 ~ 配下にあるための plugin-source パス（PLUGIN_ROOT 等）を除外する。
_CLAUDE_HOME = (Path.home() / ".claude").resolve()


# ``_isolate_env`` が中和する (module, attr) の既知リスト（テスト側で明示管理）。
# - setattr 直接差し替え:
#     corrections_insights.CORRECTIONS_FILE / audit.outcome_metrics.DATA_DIR /
#     audit.outcome_promotion_readiness.DATA_DIR / audit.measurement_bug.DATA_DIR /
#     telemetry_query.DATA_DIR（sections_multiview builder #564 が query_sessions を使う）
# - HOME setenv + reload で env/HOME 基準に再解決:
#     rl_common.* / token_usage_store.*
_KNOWN_ISOLATED: Set[Tuple[str, str]] = {
    ("corrections_insights", "CORRECTIONS_FILE"),
    ("audit.outcome_metrics", "DATA_DIR"),
    ("audit.outcome_promotion_readiness", "DATA_DIR"),
    ("audit.measurement_bug", "DATA_DIR"),
    ("telemetry_query", "DATA_DIR"),
    ("rl_common", "DATA_DIR"),
    ("rl_common", "CHECKPOINTS_DIR"),
    ("rl_common", "FALSE_POSITIVES_FILE"),
    ("rl_common", "PLUGIN_DATA_BASE"),
    ("rl_common", "_CC_PLUGIN_DATA_BASE"),
    ("rl_common", "_DEFAULT_DATA_DIR"),
    ("token_usage_store", "DATA_DIR"),
    ("token_usage_store", "USAGE_DB"),
    ("token_usage_store", "USAGE_JSONL"),
}


def _imported_module_names(modname: str) -> Set[str]:
    """``modname`` のソースが import する module 名を AST で抽出する。

    module-level import と関数内 lazy import の双方を拾う（observability builder は
    供給モジュールを関数内で lazy import するため）。相対 import（``from . import X``）は
    audit パッケージ配下と解釈して ``audit.X`` に正規化する。
    """
    mod = importlib.import_module(modname)
    src = Path(mod.__file__).read_text()
    tree = ast.parse(src)
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module)
            elif node.level > 0:
                # from . import X / from .sub import Y → audit パッケージ配下
                if node.module:
                    names.add(f"audit.{node.module}")
                for alias in node.names:
                    names.add(f"audit.{alias.name}")
    return names


def _builder_modules() -> List[str]:
    return sorted({builder.__module__ for _, builder in _obs._OBSERVABILITY_BUILDERS})


def _candidate_modules() -> Set[str]:
    """builder モジュール本体 + それらが import する供給モジュール群。"""
    candidates: Set[str] = set(_builder_modules())
    for bm in _builder_modules():
        candidates |= _imported_module_names(bm)
    return candidates


def _scan_home_derived_path_constants() -> Dict[str, List[str]]:
    """供給モジュール群の module-level ``Path`` 定数のうち実 ~/.claude 配下を指すものを返す。

    {module_name: [attr, ...]} 形式。候補モジュールを明示 import して走査する。

    注意: この関数は **module import 時に1回だけ** 呼び、結果を ``_HOME_DERIVED_SNAPSHOT`` に
    凍結する。pytest はテストモジュールを collection 段階（=どの snapshot テストの
    ``_isolate_env`` が走るより前）で import するため、ここで読む定数値は実環境基準の pristine
    値になる。reload で巻き戻すと pytest の assertion-rewrite / path machinery を壊して
    INTERNALERROR を起こすため、reload はしない（先行テストが live 値を tmp へ書き換える前に
    一度だけ読む戦略を採る）。
    """
    found: Dict[str, List[str]] = {}
    for name in sorted(_candidate_modules()):
        try:
            mod = importlib.import_module(name)
        except Exception:
            continue
        for attr in dir(mod):
            if attr.startswith("__"):
                continue
            try:
                val = getattr(mod, attr)
            except Exception:
                continue
            if isinstance(val, Path):
                try:
                    resolved = val.resolve()
                except Exception:
                    continue
                if str(resolved).startswith(str(_CLAUDE_HOME)):
                    found.setdefault(name, []).append(attr)
    return found


# import 時（collection 段階・実環境基準）に1回だけ凍結する。後続の snapshot テストが
# live module 定数を tmp 値へ書き換えても、このスナップショットは pristine 値を保持する。
_HOME_DERIVED_SNAPSHOT: Dict[str, List[str]] = _scan_home_derived_path_constants()


def _home_derived_path_constants() -> Dict[str, List[str]]:
    return _HOME_DERIVED_SNAPSHOT


def _missing_against(known: Set[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """``known`` で中和されていない (module, attr) の一覧を返す。"""
    reachable = _home_derived_path_constants()
    missing: List[Tuple[str, str]] = []
    for module, attrs in reachable.items():
        for attr in attrs:
            if (module, attr) not in known:
                missing.append((module, attr))
    return sorted(missing)


def test_all_observability_home_constants_are_isolated():
    """各 observability builder の供給モジュールが ~/.claude 配下の module-level Path 定数を
    持つなら、それは必ず ``_isolate_env`` の中和対象（``_KNOWN_ISOLATED``）に登録されていること。

    新規 builder 追加で隔離漏れが起きると fail し、追加すべき (module, attr) を指示する。
    """
    missing = _missing_against(_KNOWN_ISOLATED)
    assert not missing, (
        "observability ビルダーの供給モジュールに、_isolate_env で隔離されていない "
        "~/.claude 配下の module-level Path 定数があります。"
        "scripts/tests/test_audit_snapshot.py の _isolate_env で setattr または reload で "
        "中和し、本テストの _KNOWN_ISOLATED にも追加してください:\n"
        + "\n".join(f"  - {m}.{a}" for m, a in missing)
    )


def test_guard_has_detection_power():
    """ガード自体の検出力を保証する（メタテスト）。

    既知リストから corrections_insights.CORRECTIONS_FILE（#464 の実犯人）を抜くと、
    ガードロジックがそれを「未隔離」として検出することを確認する。これが落ちると、
    上のガードが常に空集合を見て無条件 pass している（=形骸化）ことを意味する。
    """
    weakened = set(_KNOWN_ISOLATED)
    weakened.discard(("corrections_insights", "CORRECTIONS_FILE"))
    missing = _missing_against(weakened)
    assert ("corrections_insights", "CORRECTIONS_FILE") in missing, (
        "ガードが corrections_insights.CORRECTIONS_FILE の隔離漏れを検出できていません。"
        "走査経路（_candidate_modules / _home_derived_path_constants）が壊れている可能性があります。"
    )
