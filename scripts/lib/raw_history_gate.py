"""raw_history_gate.py — optimize_history_store の raw read allowlist gate（#402 PR-2 段階2 §5）。

決定論・LLM 非依存。`optimize_history_store.load_history` / `load_raw_history`（raw view）を
呼んでよい箇所を**閉じた許可リスト**として固定し、それ以外の production 呼び出しを一律
violation として検出する。新規コードは既定で `load_effective_history`（revert 反映後の
判断母集団）に落ちる契約にするための機構（S2: 「raw を呼ばない」列挙式は新規 reader が
素通りするため採らない）。

設計（skill_declaration_reachability.py の AST 静的解析パターンに揃える）:
  - 判定は **bare 名でなく import 元**で行う。`fitness_evolution.load_history()` は
    `optimize_history_store` とは無関係の同名別実装であり、名前一致だけで判定すると
    False Positive になる（trigger_engine/session_corrections.py:53 が実例）。
    `from optimize_history_store import load_history [as X]` と
    `import optimize_history_store [as X]; X.load_history(...)` の両形を、各ファイル内の
    import 文から完全修飾名を解決したうえで扱う。
  - allowlist の粒度は **関数 / callsite 単位**（ファイル単位にしない）。同一ファイル内に
    raw dedup と業務読取が混在するケース（`fitness_evolution.py` 型）でファイル単位
    allowlist にすると業務側の raw 回帰を見逃すため。callsite ID は
    `"<repo相対posixパス>:<qualname>"`（qualname はネストした関数/クラスを `.` で連結、
    関数外のモジュールレベル呼び出しは `<module>`）。
  - **allowlist entry が消失した場合も fail** させる（`stale_allowlist`）。古い許可が
    野放しにならないように、実際に対応する呼び出しが無くなった entry も violation 扱いにする。
  - **段階2 のスコープ**: checker 本体 + 期待違反 fixture のテストまで。production ツリー
    全体（本リポジトリの `scripts/lib` / `skills/*/scripts`）へ実際に gate を有効化するのは
    reader migration（`results_board` / `fleet/queue_verify` / `fleet/propose` /
    `aggregate_runs` 等）が完了する段階4。それまでは既存 reader が raw のままなので
    allowlist 未整備で必ず落ちる。本モジュールは repo_root / allowlist を引数で受け取る
    汎用関数として実装し、production tree への適用はしない（デフォルト allowlist も
    ここでは持たない — 段階4 の wiring 側で定義する）。
  - **AST の限界**: 任意の `_read_jsonl()` / `Path.read_text()` が「raw history 読取」かは
    AST では判定できない。`outcome_promotion_readiness` の glob 直読み等の既知の history
    direct reader は本 gate の対象外（別の明示的な契約テスト / 棚卸し対象・段階4）。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

# 判定対象モジュールと関数名（optimize_history_store の raw view API）。
_TARGET_MODULE = "optimize_history_store"
# ``load_raw_history_with_aliases``（#402 段階3 M-A/M-B）: revert の entry 検索専用に
# 新設した raw reader。新規 raw reader を追加したら**必ずここにも追加する**——さもないと
# 「新規 reader が allowlist 未整備のまま素通りする」という §5 の S2 が防ごうとした失敗
# モードを、追加した当の PR 自身が再生産することになる。
_TARGET_FUNCS = ("load_history", "load_raw_history", "load_raw_history_with_aliases")


def _iter_py_files(repo_root: Path) -> List[Path]:
    """scripts/**.py + skills/**/scripts/**.py を列挙する（相対パスで `.claude` 除外）。

    除外判定は repo_root からの相対パスの parts で行う（skill_declaration_reachability.py の
    `_iter_py_files` と同じ設計判断: worktree（`<repo>/.claude/worktrees/<id>/...`）から
    実行しても repo_root 自身の絶対パスに `.claude` が含まれて誤って全除外されないため）。
    """
    root = Path(repo_root)
    files = set(root.glob("scripts/**/*.py")) | set(root.glob("skills/**/scripts/**/*.py"))
    return sorted(f for f in files if ".claude" not in f.relative_to(root).parts)


def _is_test_path(rel_posix: str) -> bool:
    """相対 posix パスが tests/ 配下 or test_*.py か（tests/fixture setup を対象外にする）。"""
    p = Path(rel_posix)
    return "tests" in p.parts or p.name.startswith("test_")


def _add_parents(tree: ast.AST) -> None:
    """ast には親ポインタが無いため、qualname 解決用に子→親の参照を後付けする。"""
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._raw_gate_parent = node  # type: ignore[attr-defined]


def _enclosing_qualname(node: ast.AST) -> str:
    """node の enclosing function/class scope から qualname を作る。

    関数外（モジュールレベル）の呼び出しは ``"<module>"`` を返す。ネストした関数・
    クラスメソッドは ``outer.inner`` / ``ClassName.method`` の形にする。
    """
    parts: List[str] = []
    cur = getattr(node, "_raw_gate_parent", None)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            parts.append(cur.name)
        cur = getattr(cur, "_raw_gate_parent", None)
    if not parts:
        return "<module>"
    return ".".join(reversed(parts))


@dataclass(frozen=True)
class RawHistoryViolation:
    """allowlist 外の raw read 呼び出し1件。"""

    callsite: str  # "<repo相対posixパス>:<qualname>"
    file: str
    line: int
    called: str  # "load_history" | "load_raw_history"


@dataclass
class RawHistoryGateReport:
    """`check_raw_history_reads` の結果。

    violations:      allowlist 外の raw read 呼び出し。
    stale_allowlist:  allowlist に列挙されているが対応する呼び出しが見つからなかった entry
                       （古い許可の野放し防止）。
    """

    violations: List[RawHistoryViolation] = field(default_factory=list)
    stale_allowlist: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations and not self.stale_allowlist


def _resolve_bindings(tree: ast.AST) -> tuple:
    """ファイル内の import 文から `_TARGET_MODULE` への束縛を解決する。

    Returns:
        (direct_funcs, module_aliases)
        direct_funcs:   bound_name -> target_func_name
                        （`from optimize_history_store import load_history [as X]`）
        module_aliases: モジュール自体を束縛したローカル名の集合
                        （`import optimize_history_store [as X]`）
    """
    direct_funcs: Dict[str, str] = {}
    module_aliases: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _TARGET_MODULE:
            for alias in node.names:
                if alias.name in _TARGET_FUNCS:
                    direct_funcs[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _TARGET_MODULE:
                    module_aliases.add(alias.asname or alias.name)
    return direct_funcs, module_aliases


def check_raw_history_reads(
    repo_root: Path, allowlist: Iterable[str] = ()
) -> RawHistoryGateReport:
    """production コードの raw history read（allowlist 外）を検出する。

    Args:
        repo_root: スキャン対象のリポジトリルート（`scripts/**.py` + `skills/**/scripts/**.py`）。
        allowlist: raw read を許可する callsite ID（`"<repo相対posixパス>:<qualname>"`）の列挙。

    段階2 では本関数を実 repo_root に対して production gate として呼ばない（fixture tree での
    checker 本体テストのみ）。実 tree への適用と allowlist の実データ整備は段階4。
    """
    root = Path(repo_root)
    allowlist_set = set(allowlist)
    matched_allowlist: Set[str] = set()
    violations: List[RawHistoryViolation] = []

    for f in _iter_py_files(root):
        rel = f.relative_to(root).as_posix()
        if _is_test_path(rel):
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        _add_parents(tree)

        direct_funcs, module_aliases = _resolve_bindings(tree)
        if not direct_funcs and not module_aliases:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            target_name: Optional[str] = None
            if isinstance(func, ast.Name) and func.id in direct_funcs:
                target_name = direct_funcs[func.id]
            elif (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id in module_aliases
                and func.attr in _TARGET_FUNCS
            ):
                target_name = func.attr
            if target_name is None:
                continue

            callsite = f"{rel}:{_enclosing_qualname(node)}"
            if callsite in allowlist_set:
                matched_allowlist.add(callsite)
                continue
            violations.append(
                RawHistoryViolation(
                    callsite=callsite, file=rel, line=node.lineno, called=target_name
                )
            )

    stale = sorted(allowlist_set - matched_allowlist)
    return RawHistoryGateReport(violations=violations, stale_allowlist=stale)
