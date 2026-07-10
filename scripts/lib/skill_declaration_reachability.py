"""skill_declaration_reachability.py — SKILL.md 宣言↔実装 到達可能性チェック（#191）。

決定論・LLM 非依存。#170（`intention_check` が SKILL.md 散文で「実行する」と宣言されていたが
定義モジュール自身と test 以外どこからも呼ばれていなかった＝ゾンビ宣言）の再発防止として、
SKILL.md が「`func(args)` を実行する」と宣言している callable が、実コードのどこからも
呼ばれていない（到達不能）状態を検出する。

設計（#191 issue の3ステップ）:
  1. `extract_declared_calls`: SKILL.md 散文からバッククォート inline code span
     （`` `func(args)` ``）形の宣言 callable を抽出する。
  2. `build_call_graph_index`: scripts/**.py + skills/**/scripts/**.py を AST 走査し、
     candidate 名の定義位置と参照位置（caller）の索引を作る。
  3. `detect_unreachable_declarations`: 定義モジュール自身と `tests/` 配下のみが caller なら
     「到達不能」と判定する。

FP 較正（実 SKILL.md 全件・skills/*/SKILL.md 25 個・#191 dry-run）で確定した設計判断:

  a) **抽出はバッククォート inline code span のみ**。裸の prose（バッククォート無しの
     `func(args)` 風の記述）は抽出しない。実コーパスでは擬似コード的な言及
     （例: `gstack(340) / evolve-anything(30)` という使用回数サマリの記法）もバッククォート内に
     混在するが、いずれも「自コードベースに定義が無い」フィルタ（下記 c）で自然に除外される。

  b) **コードブロック内の宣言は抽出対象外**（dogfood gate Layer 3 = `dogfood/skill_blocks.py`
     の管轄。同じ block を二重に検査しない）。ただし到達可能性の **caller 判定側**では
     SKILL.md の fenced code block（python/bash）本文も参照元として評価する
     （下記 e）。抽出除外と caller 評価は独立している。

  c) **自コードベースに定義が無い candidate は対象外**（Python 標準関数・外部ライブラリ関数・
     CLI コマンド形）。`Path(__file__)` や `bin/evolve-tier show` のような記述は
     scripts/**.py に `def` が無いため自然に unresolved 扱いになる（allowlist 不要）。

  d) **複数モジュールに同名定義がある candidate は判定対象から除外する**（ambiguous）。
     汎用名（`run` 等）が複数箇所に定義されている場合、caller が「どの定義への参照か」を
     静的に一意特定できないため、誤帰属（本来無関係な定義を reachable と誤認 / zombie と
     誤認）を避けて precision を優先する。

  e) **caller 判定は scripts/**.py の AST 参照 + SKILL.md の fenced code block テキスト一致の
     両方を見る**。当初 scripts/**.py の Name/Attribute 参照のみで設計したところ、
     agent-brushup の `check_quality()`（SKILL.md の python code block から実際に呼ばれている）
     や prune の `archive_file(...)`（同様）が誤って「到達不能」と判定された。SKILL.md の
     code block はユーザー（エージェント）が実際に実行する起動経路であり、Layer 3 が
     その block 自体の実行可否を検証するため、ここでは静的テキスト一致で「呼ばれている
     ことになっている」ことだけ確認すれば十分（実行はしない）。

  f) **`import X as Y` エイリアス越しの呼び出しも caller として解決する**。
     `from suppression_ledger import reconcile_surfaced as _reconcile` の後 `_reconcile(...)`
     で呼ぶパターン（実コーパスで確認）は、素の identifier 一致だけでは検出できない。
     ファイル単位でエイリアスマップを構築し、エイリアス名への Name/Attribute 参照を
     元の名前への参照として解決する。

  g) **`__init__.py` 等の re-export（`from X import Y  # noqa: F401`、エイリアス無し）だけでは
     到達可能と判定しない**。re-export はシンボルを import 可能にするだけで、実際に呼び出す
     わけではない。これを caller とみなすと再輸出パターンが多用されるこのリポジトリでは
     precision が崩壊する（`check_upstream` 型の真の zombie を隠してしまう）。

  h) **動的呼び出し（`getattr(mod, "name")` 等の文字列キー dispatch）は本チェックの守備範囲外**。
     identifier ベースの静的参照のみを対象とする。実コーパス較正（skills/*/SKILL.md 25 個 +
     scripts/**.py 全体）ではこのパターンによる誤検出は 0 件だった（dispatch dict は
     `{"key": func_name}` の形でリテラル identifier を値に置くのが実装規約であり、文字列
     キー越しの getattr 呼び出しは見つからなかった）。将来必要になれば文字列リテラル一致の
     追加を検討する（YAGNI — 実例が無いものは作らない）。

  i) **`tests/` 配下にしか定義が無い candidate は「自コードベースの実関数」と認めない**
     （unresolved 扱い）。production 関数の宣言チェックであり、test helper の偶然の名前衝突を
     拾わないため。
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# fence 開始行（`` ``` `` + 任意の言語ラベル）。skill_blocks.py と同じパターン。
_FENCE_OPEN_RE = re.compile(r"^(\s*)```([A-Za-z0-9_+-]*)\s*$")
_FENCE_CLOSE_RE = re.compile(r"^\s*```\s*$")

# バッククォート inline code span 内の `ident(args)` 形（dotted 可）。
_INLINE_CALL_RE = re.compile(
    r"`([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\(([^`\n]*)\)`"
)

# caller 評価で見る SKILL.md fenced code block の言語（Layer 3 と同じ正規化対象）。
_MD_FENCE_LANGS = {"python", "py", "python3", "bash", "sh", "shell"}


def _default_repo_root() -> Path:
    """evolve-anything 自身のリポジトリ（= プラグイン）ルート。

    module 定数でなく関数にして呼び出し時に解決する（testpaths_coverage の
    `_default_repo_root` と同じ慣習）。テストは `repo_root` 引数で疑似ツリーに差し替える。
    """
    from plugin_root import PLUGIN_ROOT

    return PLUGIN_ROOT


def _is_test_path(rel_posix: str) -> bool:
    """相対 posix パスが tests/ 配下 or test_*.py か。"""
    p = Path(rel_posix)
    return "tests" in p.parts or p.name.startswith("test_")


# --- 1. extraction ----------------------------------------------------------


@dataclass(frozen=True)
class DeclaredCall:
    """SKILL.md 散文から抽出した宣言 callable。

    name:   バッククォート内の dotted 名の最終セグメント（bare 関数名）。
    raw:    抽出元の生テキスト（`func(args)`、バッククォート込み）。
    source: 抽出元 SKILL.md のパス（呼び出し側が渡した Path をそのまま文字列化）。
    line:   1-origin 行番号（元ファイルの行に対応。コードブロック除去後もずれない）。
    """

    name: str
    raw: str
    source: str
    line: int


def _strip_fenced_code_blocks(text: str) -> str:
    """fenced code block の中身を空行に置換し、行番号を保ったまま prose だけ残す。"""
    lines = text.splitlines()
    out: List[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not _FENCE_OPEN_RE.match(line):
            out.append(line)
            i += 1
            continue
        out.append("")  # フェンス開始行
        i += 1
        while i < n and not _FENCE_CLOSE_RE.match(lines[i]):
            out.append("")
            i += 1
        if i < n:
            out.append("")  # フェンス終了行
            i += 1
    return "\n".join(out)


def extract_declared_calls(skill_md: Path) -> List[DeclaredCall]:
    """SKILL.md の散文（コードブロック外）からバッククォート付き `func(args)` 宣言を抽出する。

    コードブロック内の同種の記述は Layer 3（dogfood/skill_blocks.py）の管轄なので除外する。
    """
    skill_md = Path(skill_md)
    text = skill_md.read_text(encoding="utf-8")
    prose = _strip_fenced_code_blocks(text)
    out: List[DeclaredCall] = []
    for lineno, line in enumerate(prose.splitlines(), start=1):
        for m in _INLINE_CALL_RE.finditer(line):
            dotted = m.group(1)
            name = dotted.rsplit(".", 1)[-1]
            out.append(
                DeclaredCall(name=name, raw=m.group(0).strip("`"), source=str(skill_md), line=lineno)
            )
    return out


# --- 2. call graph index ------------------------------------------------------


def _iter_py_files(repo_root: Path) -> List[Path]:
    """scripts/**.py + skills/**/scripts/**.py を列挙する（相対パスで `.claude` 除外）。

    除外判定は **repo_root からの相対パス**の parts で行う（testpaths_coverage.py の
    `_EXCLUDE_DIRS` 突合と同じ慣習）。glob 結果の絶対パス全体（`f.parts`）で判定すると、
    worktree（`<repo>/.claude/worktrees/<id>/...`）から実行した場合に repo_root 自身の
    絶対パスに `.claude` が含まれるため **全ファイルが誤って除外される**（#191 で実機発見。
    worktree 隔離で作業する impl-worker が自分自身の worktree を検査対象にすると常に
    evaluated_count=0 になる致命的な false negative だった）。
    """
    root = Path(repo_root)
    files = set(root.glob("scripts/**/*.py")) | set(root.glob("skills/**/scripts/**/*.py"))
    return sorted(f for f in files if ".claude" not in f.relative_to(root).parts)


@dataclass
class CallGraphIndex:
    """candidate 名 → 相対 posix パス集合の索引。"""

    definitions: Dict[str, Set[str]] = field(default_factory=dict)
    references: Dict[str, Set[str]] = field(default_factory=dict)


def build_call_graph_index(repo_root: Path, names: Set[str]) -> CallGraphIndex:
    """scripts/**.py + skills/**/scripts/**.py を AST 走査し定義/参照索引を作る。

    tests/ 配下の `def` は「自コードベースの実関数」と認めないため definitions に含めない
    （i の設計判断）。参照（Name/Attribute）は tests/ 配下も含めて記録し、呼び出し側
    （`detect_unreachable_declarations`）で「定義モジュール自身 + tests/ 以外」を判定する。
    """
    root = Path(repo_root)
    idx = CallGraphIndex()

    for f in _iter_py_files(root):
        rel = f.relative_to(root).as_posix()
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue

        if not _is_test_path(rel):
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
                    idx.definitions.setdefault(node.name, set()).add(rel)

        # `from mod import name as alias` のエイリアスマップ（f の設計判断）。
        alias_map: Dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    if a.name in names and a.asname:
                        alias_map[a.asname] = a.name

        for node in ast.walk(tree):
            target: Optional[str] = None
            if isinstance(node, ast.Name):
                if node.id in names:
                    target = node.id
                elif node.id in alias_map:
                    target = alias_map[node.id]
            elif isinstance(node, ast.Attribute):
                if node.attr in names:
                    target = node.attr
                elif node.attr in alias_map:
                    target = alias_map[node.attr]
            if target:
                idx.references.setdefault(target, set()).add(rel)

    return idx


def _skill_md_code_block_callers(repo_root: Path, names: Set[str]) -> Dict[str, Set[str]]:
    """skills/*/SKILL.md の fenced code block（python/bash）本文の candidate 名一致を集める。

    Layer 3 が同じ block の実行検証を担うため、ここでは静的テキスト一致のみ（e の設計判断）。
    """
    root = Path(repo_root)
    out: Dict[str, Set[str]] = {}

    from dogfood import skill_blocks  # 遅延 import（循環回避・dogfood 未解決環境への耐性）

    for md in sorted(root.glob("skills/*/SKILL.md")):
        rel = md.relative_to(root).as_posix()
        for block in skill_blocks.extract_code_blocks(md):
            if block["lang"] not in _MD_FENCE_LANGS:
                continue
            body = block["code"]
            for name in names:
                if re.search(r"\b" + re.escape(name) + r"\s*\(", body):
                    out.setdefault(name, set()).add(rel)
    return out


# --- 3. reachability ----------------------------------------------------------


@dataclass(frozen=True)
class UnreachableDeclaration:
    """到達不能と判定された宣言 callable。"""

    name: str
    source: str
    line: int
    def_files: Tuple[str, ...]


@dataclass
class ReachabilityReport:
    """`detect_unreachable_declarations` の結果。

    has_skills:        リポジトリに skills/*/SKILL.md が1件以上あるか（無ければ非該当）。
    unreachable:       到達不能と判定された宣言（skill_md 内の重複は1件に畳む）。
    evaluated_count:    定義が一意に解決できた宣言の延べ件数（判定対象）。
    ambiguous_count:    複数モジュールに同名定義があり判定を skip した件数。
    unresolved_count:   自コードベースに定義が見つからず対象外にした件数。
    """

    has_skills: bool = False
    unreachable: List[UnreachableDeclaration] = field(default_factory=list)
    evaluated_count: int = 0
    ambiguous_count: int = 0
    unresolved_count: int = 0


def detect_unreachable_declarations(repo_root: Optional[Path] = None) -> ReachabilityReport:
    """SKILL.md が宣言する callable のうち production コードから到達不能なものを検出する。"""
    root = Path(repo_root) if repo_root is not None else _default_repo_root()
    skill_mds = sorted(root.glob("skills/*/SKILL.md"))
    if not skill_mds:
        return ReachabilityReport(has_skills=False)

    declared: List[DeclaredCall] = []
    for md in skill_mds:
        declared.extend(extract_declared_calls(md))

    report = ReachabilityReport(has_skills=True)
    if not declared:
        return report

    names = {d.name for d in declared}
    idx = build_call_graph_index(root, names)
    md_callers = _skill_md_code_block_callers(root, names)

    seen_pairs: Set[Tuple[str, str]] = set()

    for d in declared:
        def_files = idx.definitions.get(d.name, set())
        if not def_files:
            report.unresolved_count += 1
            continue
        if len(def_files) > 1:
            report.ambiguous_count += 1
            continue

        report.evaluated_count += 1
        refs = idx.references.get(d.name, set()) | md_callers.get(d.name, set())
        external = {r for r in refs if r not in def_files and not _is_test_path(r)}
        if external:
            continue

        rel_source = Path(d.source).resolve().relative_to(root.resolve()).as_posix()
        key = (rel_source, d.name)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        report.unreachable.append(
            UnreachableDeclaration(
                name=d.name, source=rel_source, line=d.line, def_files=tuple(sorted(def_files))
            )
        )

    return report
