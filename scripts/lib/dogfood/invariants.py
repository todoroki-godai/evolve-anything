"""Layer 2: report invariants（#496）。

Layer1 で得た evolve result JSON に対する機械検査。「間違った数字はエラーを出さない」
（#489 #490 #476）を構造的に捕捉する。各 check 関数は failure の list を返す
（空 list = green）。failure は ``{"check": str, "detail": str}`` の dict。

observability contract（ADR-028）の builder key を単一ソースとして import し、
result["observability"] に未知 key が混じる drift（成果物⇔contract 乖離）を検出する。
"""
from __future__ import annotations

from typing import Any, Dict, List

# evolve 成功時に必ず存在すべき top-level キー（#496 設計の (b)）。
# 値が {"error": ...} のみのときは「フェーズが落ちた」とみなし失敗扱いにする。
_REQUIRED_TOP_LEVEL_KEYS = (
    "phases",
    "observability",
    "growth_report",
    "correction_semantic",
    "weak_signals",
)

# 「件数」を表すと判断するキー名の接尾辞/完全一致（非負検査の対象）。
_COUNT_KEY_SUFFIXES = ("_count", "_total", "_num")
_COUNT_KEY_NAMES = {
    "count", "total", "promoted", "expired", "inserted", "applied",
    "skipped", "archived", "created", "updated", "proposals", "candidates",
}

# 「当PJスコープ ≤ 全PJスコープ」の対の命名規約: <base> と <base>_all_pj。
_GLOBAL_SUFFIX = "_all_pj"


# evolve.py が audit/PJ アーティファクト起点でなく実行時状態として直接書き込む observability キー。
# これらは _OBSERVABILITY_BUILDERS（project_dir: Path → section）の builder パターンに馴染まないため
# contract 外に書き込まれるが、既知・意図的なキーなので unknown 判定しない（#504）。
# - constitutional: _surface_constitutional_status()（cache stale/未生成アラート）
# - remediation_batch_skip: build_remediation_batch_skip_observability()（evolve result が引数）
_EVOLVE_ONLY_OBSERVABILITY_KEYS: frozenset = frozenset(
    {"constitutional", "remediation_batch_skip"}
)


def _observability_builder_keys() -> set:
    """observability contract の単一ソースから既知 builder key 集合を得る。

    _OBSERVABILITY_BUILDERS（audit/PJ アーティファクト起点の builder）に加え、
    evolve.py が実行時状態として直接書き込む _EVOLVE_ONLY_OBSERVABILITY_KEYS も
    既知として含める（#504）。
    """
    try:
        from audit.observability import _OBSERVABILITY_BUILDERS

        return {k for k, _ in _OBSERVABILITY_BUILDERS} | _EVOLVE_ONLY_OBSERVABILITY_KEYS
    except Exception:
        # contract が import できない環境では unknown-key 検査を無効化（FP 回避）。
        return set()


def _is_error_only(value: Any) -> bool:
    """値が {"error": ...} だけ（実データなし）の dict なら True。"""
    return isinstance(value, dict) and set(value.keys()) == {"error"}


def check_required_keys(result: Dict[str, Any]) -> List[Dict[str, str]]:
    """evolve 成功時に必須の top-level キーが存在し error-only でないことを検査。"""
    failures: List[Dict[str, str]] = []
    for key in _REQUIRED_TOP_LEVEL_KEYS:
        if key not in result:
            failures.append({"check": "required_keys", "detail": f"missing top-level key: {key}"})
        elif _is_error_only(result[key]):
            failures.append(
                {"check": "required_keys", "detail": f"key {key} is error-only: {result[key].get('error')!r}"}
            )
    return failures


def _looks_like_count_key(key: str) -> bool:
    if key in _COUNT_KEY_NAMES:
        return True
    return any(key.endswith(suf) for suf in _COUNT_KEY_SUFFIXES)


def _walk_counts(obj: Any, path: str = ""):
    """ネストした dict/list を走査し (path, value) で count らしい数値を yield する。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            child = f"{path}.{k}" if path else str(k)
            if isinstance(v, bool):
                continue
            if isinstance(v, int) and _looks_like_count_key(str(k)):
                yield child, v
            else:
                yield from _walk_counts(v, child)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_counts(v, f"{path}[{i}]")


def check_non_negative_counts(result: Dict[str, Any]) -> List[Dict[str, str]]:
    """件数フィールド（*_count / promoted / inserted 等）が負でないことを検査。"""
    failures: List[Dict[str, str]] = []
    for path, value in _walk_counts(result):
        if value < 0:
            failures.append(
                {"check": "non_negative_counts", "detail": f"{path} = {value} (count must be >= 0)"}
            )
    return failures


def _flat_numeric(obj: Any, path: str = "", out: Dict[str, float] | None = None) -> Dict[str, float]:
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            child = f"{path}.{k}" if path else str(k)
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                out[child] = v
            else:
                _flat_numeric(v, child, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _flat_numeric(v, f"{path}[{i}]", out)
    return out


def check_pj_le_global(result: Dict[str, Any]) -> List[Dict[str, str]]:
    """同一指標で当PJ値と全PJ値の両方がある場合、当PJ ≤ 全PJ を検査。

    命名規約: ``<base>`` と ``<base>_all_pj`` が両方存在する数値ペアのみ対象
    （片方しか無いものは検査しない＝FP を出さない）。
    """
    failures: List[Dict[str, str]] = []
    flat = _flat_numeric(result)
    for key, gval in flat.items():
        if not key.endswith(_GLOBAL_SUFFIX):
            continue
        base = key[: -len(_GLOBAL_SUFFIX)]
        if base in flat:
            pj_val = flat[base]
            if pj_val > gval:
                failures.append(
                    {
                        "check": "pj_le_global",
                        "detail": f"{base}={pj_val} > {key}={gval} (当PJ件数が全PJ件数を超過)",
                    }
                )
    return failures


def check_observability_contract(result: Dict[str, Any]) -> List[Dict[str, str]]:
    """result["observability"] の key が contract の既知 builder key の部分集合か検査。

    未知 key = 成果物が contract に無いセクションを出している drift（ADR-028 拡張）。
    """
    failures: List[Dict[str, str]] = []
    obs = result.get("observability")
    if not isinstance(obs, dict):
        return failures
    known = _observability_builder_keys()
    if not known:
        return failures  # contract import 不可なら検査スキップ
    for key in obs.keys():
        if key == "error":
            continue
        if key not in known:
            failures.append(
                {
                    "check": "observability_contract",
                    "detail": f"unknown observability key '{key}' not in contract ({sorted(known)})",
                }
            )
    return failures


# (name, function) の単一ソース。bin/rl-dogfood-gate がこれを回す。
_CHECKS = (
    ("required_keys", check_required_keys),
    ("non_negative_counts", check_non_negative_counts),
    ("pj_le_global", check_pj_le_global),
    ("observability_contract", check_observability_contract),
)


def run_all(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """全 invariant を回し ``[{"check": name, "failures": [...]}]`` を返す。"""
    out: List[Dict[str, Any]] = []
    for name, fn in _CHECKS:
        try:
            failures = fn(result)
        except Exception as e:  # noqa: BLE001
            failures = [{"check": name, "detail": f"invariant raised: {e!r}"}]
        out.append({"check": name, "failures": failures})
    return out
