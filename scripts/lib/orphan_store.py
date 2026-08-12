"""orphan_store.py — writer あり reader なしの jsonl ストアを決定論検出する（#422）。

LLM 非依存・静的解析のみ。背景:

主要ストアの producer→consumer 突合（手動）で「書きっぱなしで誰も読まない」観測を特定した。
代表例が `tool_durations.jsonl`（実環境 5.1MB）— `hooks/tool_duration.py` が全 Bash 実行ごとに
python3 を起動して書き込むが reader が 0 で、純粋なレイテンシ + ディスクコストだった。
この手動突合を決定論化して audit の observability に常設する。

定義:
- writer = **hooks.json に登録された** hook の本体ソースが書き込む jsonl ファイル名。
           未登録 hook は発火しないので writer に数えない（false positive 防止）。
- reader = scripts/ ・ skills/（tests 配下を除く）のソースに現れる jsonl ファイル名。
- orphan = writer にあって reader に無いストア。

突合はファイル名文字列（`"foo.jsonl"` / `foo.jsonl`）の出現で行う。スキーマ的に厳密ではないが、
本 PJ のストアは全て `DATA_DIR / "<name>.jsonl"` 形式で扱われるため、ファイル名突合で十分。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

# jsonl ファイル名の抽出パターン（"foo.jsonl" / foo.jsonl いずれも拾う）。
# ファイル名は英数 + _ + - を許容（実ストア名は snake_case のみだが緩めに取る）。
_JSONL_RE = re.compile(r"([A-Za-z0-9_\-]+\.jsonl)")
# hooks.json の command から hook ファイル名（xxx.py）を取り出す。
_HOOK_PY_RE = re.compile(r"hooks/([A-Za-z0-9_\-]+\.py)")
# ローカル import 名を拾う（stdlib/third-party も拾うが、後段で scripts/lib 配下の実体と
# 突合するため無害）。関数内 lazy import（インデント有り）にも対応するため行頭空白を許容する。
_LOCAL_IMPORT_RE = re.compile(
    r"^[ \t]*(?:from ([A-Za-z_][A-Za-z0-9_]*) import|import ([A-Za-z_][A-Za-z0-9_]*)\b)",
    re.MULTILINE,
)


def _default_plugin_root() -> Path:
    """evolve-anything 自身のプラグインルート。

    module 定数でなく関数にして呼び出し時に解決する（hook_drift の `_default_gstack_dir`
    と同じ慣習）。テストは `monkeypatch.setattr(orphan_store, "_default_plugin_root", ...)`
    で疑似ツリーに差し替えられる。
    """
    from plugin_root import PLUGIN_ROOT

    return PLUGIN_ROOT


@dataclass
class OrphanStoreReport:
    """orphan store 検出結果。

    orphans:       writer はあるが reader が無い jsonl ファイル名（ソート済み）。
    writer_files:  ストア名 → それを書く hook ファイル名のリスト（evidence）。
    reader_count:  ストア名 → reader として現れたソースファイル数（参考）。
    """

    orphans: List[str] = field(default_factory=list)
    writer_files: Dict[str, List[str]] = field(default_factory=dict)
    reader_count: Dict[str, int] = field(default_factory=dict)


@dataclass
class StoreContractDriftReport:
    """宣言（store_registry）と実体（hook writer）の drift 検出結果（#434）。

    undeclared:   実際に登録 hook が書くが store_registry に宣言が無いストア名（ソート済み）。
                  → 「宣言なしの新規 writer」。これが事前ゲートの主検出対象。
    declared_writer_files: undeclared ストア名 → それを書く hook ファイル名（evidence）。
    stale:        宣言はあるが実 writer が見当たらないストア名（writer が消えた等）。advisory。
    declaration_problems: store_registry 宣言自身の整合性問題（retention 不整合など）。
    """

    undeclared: List[str] = field(default_factory=list)
    declared_writer_files: Dict[str, List[str]] = field(default_factory=dict)
    stale: List[str] = field(default_factory=list)
    declaration_problems: List[str] = field(default_factory=list)


def _registered_hook_files(plugin_root: Path) -> List[str]:
    """hooks.json の command で参照される hook ファイル名（xxx.py）一覧を返す。"""
    hooks_json = plugin_root / "hooks" / "hooks.json"
    try:
        data = json.loads(hooks_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    files: List[str] = []
    for groups in (data.get("hooks") or {}).values():
        for group in groups or []:
            for hook in group.get("hooks", []) or []:
                cmd = hook.get("command", "")
                files.extend(_HOOK_PY_RE.findall(cmd))
    return files


def _jsonl_names_in_text(text: str) -> Set[str]:
    return set(_JSONL_RE.findall(text))


def _local_lib_modules(plugin_root: Path) -> Dict[str, List[Path]]:
    """scripts/lib 直下のローカル top-level import 名 → 実体ソースファイル群。

    単一ファイルモジュール（``foo.py``）は ``[foo.py]``、パッケージ（``foo/__init__.py``
    あり）は tests を除く配下 ``*.py`` 全部を返す。
    """
    lib_dir = plugin_root / "scripts" / "lib"
    mapping: Dict[str, List[Path]] = {}
    if not lib_dir.is_dir():
        return mapping
    for entry in sorted(lib_dir.iterdir()):
        if entry.is_dir():
            if entry.name == "tests" or not (entry / "__init__.py").exists():
                continue
            files = [
                f
                for f in sorted(entry.rglob("*.py"))
                if "tests" not in f.parts and not f.name.startswith("test_")
            ]
            if files:
                mapping[entry.name] = files
        elif entry.suffix == ".py" and not entry.name.startswith("test_"):
            mapping[entry.stem] = [entry]
    return mapping


def _local_import_names(text: str, known: Set[str]) -> Set[str]:
    """ソーステキストから、``known``（ローカルモジュール名集合）に含まれる import 名を抽出する。"""
    names: Set[str] = set()
    for group1, group2 in _LOCAL_IMPORT_RE.findall(text):
        name = group1 or group2
        if name in known:
            names.add(name)
    return names


def _all_local_source_files(plugin_root: Path) -> List[Path]:
    """scripts/ ・ skills/ ・ hooks/（tests 除く）の全 *.py。import グラフ構築用。"""
    files: List[Path] = []
    for base in (plugin_root / "scripts", plugin_root / "skills", plugin_root / "hooks"):
        if not base.is_dir():
            continue
        for py in base.rglob("*.py"):
            if "tests" in py.parts or py.name.startswith("test_"):
                continue
            files.append(py)
    return files


def _module_reachable_from_hooks(
    plugin_root: Path,
    module_name: str,
    hook_files: List[str],
    module_files: Dict[str, List[Path]],
) -> bool:
    """``module_name``（scripts/lib 直下の basename）が登録 hook のいずれかから import
    チェーンを辿って到達可能かを判定する（純粋な reachability。exclusivity は問わない）。

    ``StoreDeclaration.writer_module`` 宣言の検証専用、狭くスコープされたチェック。
    ``_hook_delegate_files`` と違い汎用ライブラリも辿るが、宣言側で明示 opt-in された
    モジュール名 1 件の存否を判定するだけなので writer 集合の自動展開（誤検出のリスク）
    には繋がらない。
    """
    if module_name not in module_files:
        return False
    known = set(module_files)
    hooks_dir = plugin_root / "hooks"
    visited_modules: Set[str] = set()
    seen_files: Set[Path] = {hooks_dir / f for f in hook_files}
    frontier: List[Path] = list(seen_files)
    while frontier:
        current = frontier.pop()
        try:
            text = current.read_text(encoding="utf-8")
        except OSError:
            continue
        for name in _local_import_names(text, known):
            if name == module_name:
                return True
            if name in visited_modules:
                continue
            visited_modules.add(name)
            for f in module_files.get(name, []):
                if f not in seen_files:
                    seen_files.add(f)
                    frontier.append(f)
    return False


def _hook_delegate_files(
    plugin_root: Path, hook_file: str, module_files: Dict[str, List[Path]]
) -> List[Path]:
    """``hook_file`` に排他的に委譲されたローカルモジュール/パッケージのソース一覧（BFS）。

    ADR-054 Phase 0（file-size-budget 800行分割）以降、hook 本体が ``scripts/lib/<pkg>/`` へ
    分割されるケースがある（例: ``hooks/restore_state.py`` → ``scripts/lib/session_notify/`` →
    ``scripts/lib/icebox_verdict_seen.py``）。分割先は ``hooks/`` 配下に無いため素朴な単一
    ファイル走査では writer を検出できず、orphan/drift 誤検知の原因になる（#434 回帰）。

    「PJ 全体で唯一の importer が閉包内のファイル」であるモジュールだけを hook 本体の一部と
    みなして辿る（owners が閉包の外に 1 つでもあれば展開を止める厳格な基準）。scripts/lib
    には非 hook からも広く共有される汎用ライブラリ（``rl_common`` 等）が大量にあり、
    「他の hook からは import されない」程度の緩い基準では汎用ライブラリまで芋づる式に
    巻き込んで無関係な jsonl 名を writer 扱いしてしまう（実測で undeclared 誤検出 7 件・
    #434 回帰調査時に発覚）。厳格な基準はそれを踏まない安全側の歯止めであり、2 hop 以上
    離れた委譲（例: ``icebox_verdict_seen.jsonl``）は個別に ``StoreDeclaration.writer_module``
    宣言 + ``detect_store_contract_drift`` の reachability チェックで別途救済する。
    """
    known = set(module_files)
    importers: Dict[str, Set[Path]] = {name: set() for name in known}
    for py in _all_local_source_files(plugin_root):
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        for name in _local_import_names(text, known):
            importers[name].add(py)

    hook_path = plugin_root / "hooks" / hook_file
    closure: Set[Path] = {hook_path}
    delegated: List[Path] = []
    frontier = [hook_path]
    visited_modules: Set[str] = set()
    while frontier:
        current = frontier.pop()
        try:
            text = current.read_text(encoding="utf-8")
        except OSError:
            continue
        for name in _local_import_names(text, known):
            if name in visited_modules:
                continue
            owners = importers.get(name, set())
            if not owners or not owners <= closure:
                continue  # 閉包外からも import される共有モジュール → 展開しない
            visited_modules.add(name)
            for f in module_files.get(name, []):
                if f not in closure:
                    closure.add(f)
                    delegated.append(f)
                    frontier.append(f)
    return delegated


def find_store_writers(plugin_root: Optional[Path] = None) -> Dict[str, List[str]]:
    """登録済み hook が書く jsonl ストア名 → 書いている hook ファイル名のリスト。

    hooks.json に登録された hook の本体ソースに加え、その hook に排他的に委譲された
    scripts/lib パッケージ/モジュール（``_hook_delegate_files``）も走査する。未登録 hook は
    発火しないため対象外（orphan の false positive を避ける）。
    """
    root = plugin_root if plugin_root is not None else _default_plugin_root()
    hooks_dir = root / "hooks"
    module_files = _local_lib_modules(root)
    writers: Dict[str, List[str]] = {}
    for hook_file in _registered_hook_files(root):
        src = hooks_dir / hook_file
        try:
            text = src.read_text(encoding="utf-8")
        except OSError:
            continue
        texts = [text]
        for delegate in _hook_delegate_files(root, hook_file, module_files):
            try:
                texts.append(delegate.read_text(encoding="utf-8"))
            except OSError:
                continue
        for name in _jsonl_names_in_text("\n".join(texts)):
            writers.setdefault(name, [])
            if hook_file not in writers[name]:
                writers[name].append(hook_file)
    return writers


def find_store_readers(plugin_root: Optional[Path] = None) -> Dict[str, int]:
    """scripts/ ・ skills/（tests 除外）に現れる jsonl ストア名 → 出現ソース数。

    reader は「ストアを消費する側」を表す。tests 配下は fixture・mock でストア名を書くため
    reader として数えない（実コードの consumer のみを対象にする）。
    """
    root = plugin_root if plugin_root is not None else _default_plugin_root()
    readers: Dict[str, int] = {}
    for base in (root / "scripts", root / "skills"):
        if not base.is_dir():
            continue
        for py in base.rglob("*.py"):
            parts = py.parts
            if "tests" in parts or py.name.startswith("test_"):
                continue
            try:
                text = py.read_text(encoding="utf-8")
            except OSError:
                continue
            for name in _jsonl_names_in_text(text):
                readers[name] = readers.get(name, 0) + 1
    return readers


def detect_orphan_stores(plugin_root: Optional[Path] = None) -> OrphanStoreReport:
    """writer あり reader なしの jsonl ストアを検出する（決定論）。"""
    root = plugin_root if plugin_root is not None else _default_plugin_root()
    writers = find_store_writers(root)
    readers = find_store_readers(root)

    orphans = sorted(name for name in writers if readers.get(name, 0) == 0)
    return OrphanStoreReport(
        orphans=orphans,
        writer_files={name: sorted(writers[name]) for name in orphans},
        reader_count={name: readers.get(name, 0) for name in writers},
    )


def detect_store_contract_drift(
    plugin_root: Optional[Path] = None,
) -> StoreContractDriftReport:
    """宣言（store_registry）と実体（登録 hook の writer）の drift を検出する（#434）。

    事前契約ゲート: 新ストアを追加するとき store_registry に宣言を足さずに hook が書くと
    `undeclared` に載る。orphan_store の事後検出（reader 0）と違い、reader の有無に関わらず
    「宣言なしの新規 writer」を検出するのが目的（モグラ叩きの解消）。

    store_registry が解決できない環境（別 PJ 等）では空レポートを返す（沈黙）。
    """
    root = plugin_root if plugin_root is not None else _default_plugin_root()
    writers = find_store_writers(root)

    try:
        import store_registry
    except ImportError:
        return StoreContractDriftReport()

    declared = set(store_registry.declared_store_names())

    # hook-writer 突合（find_store_writers）に現れない writer を持つストアは、宣言が
    # あっても「実 writer 見当たらず」で stale 誤検知になる。対象は db（batch ingest, #430）
    # と writer_locus="batch" の jsonl（weak_signals.jsonl, #432）。両者を stale_exempt_names
    # に集約して減算する（declared_store_names が monkeypatch される経路でも patch を壊さない）。
    try:
        exempt = set(store_registry.stale_exempt_names())
    except AttributeError:  # 古い store_registry 互換（db のみ除外にフォールバック）
        try:
            exempt = {d.name for d in store_registry.declarations_by_kind("db")}
        except AttributeError:
            exempt = set()

    undeclared = sorted(name for name in writers if name not in declared)
    stale_candidates = sorted(
        name for name in declared if name not in writers and name not in exempt
    )

    # writer_module 宣言（find_store_writers の 1 hop 排他委譲追跡を超える委譲。#434）が
    # あるストアは、宣言モジュールが実際に hook から到達可能なら stale から除外する。
    # stale 候補のみ（少数）を対象にする狭いチェックなので、全 writer 展開のような
    # 誤検出リスクは生まない。
    try:
        by_name = store_registry.declarations_by_name()
    except AttributeError:  # 古い store_registry 互換
        by_name = {}
    module_files = _local_lib_modules(root)
    hook_files = _registered_hook_files(root)
    stale = []
    for name in stale_candidates:
        decl = by_name.get(name)
        writer_module = getattr(decl, "writer_module", None) if decl else None
        if writer_module and _module_reachable_from_hooks(
            root, writer_module, hook_files, module_files
        ):
            continue
        stale.append(name)

    return StoreContractDriftReport(
        undeclared=undeclared,
        declared_writer_files={name: sorted(writers[name]) for name in undeclared},
        stale=stale,
        declaration_problems=store_registry.validate_declarations(),
    )
