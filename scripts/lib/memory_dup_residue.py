"""旧 PJ memory の完全重複残骸検出（#131, advisory・fleet 横断）。

``~/.claude/projects/*/memory/`` を走査し、dir ペア間で ``*.md`` の内容 hash 集合を比較する。
ある dir の全ファイルが別 dir に内容一致で包含される（完全重複 subset）場合、その dir を
**残骸候補** として surface する。``pj_slug`` の ``PJ_SLUG_ALIASES``（read 層別名 SoT）に載る
旧 slug に対応する dir は「rename 由来の可能性大」とラベルする。

削除は **提案のみ**（tar 退避 + rm の手順提示）で auto-apply しない。

【走査スコープの安全設計（#19 / transcript-store-bench の教訓）】
- memory dir 解決は CC パスエンコード（``resolve_cc_memory_dir`` 相当）と同じ ``projects/*/memory``。
  repo-basename slug（``resolve_pj_slug``）とは名前空間が別物なので混同しない。
- 走査は各 memory dir 直下の ``*.md`` のみ・**非再帰**。transcripts（``*.jsonl``・実環境 1.9GB /
  9925 files）には一切触れない。
- ``MEMORY.md``（索引）は PJ ごとに内容が異なり得るため比較集合から除外し、memory 実体のみ突合する
  （rename 直後は index が同一でも以後 divergence するため、含めると真の残骸を取り逃す）。
- ``default_projects_dir`` は module-level 定数にせず **call-time 関数** で ``Path.home()`` を解決する
  （observability 隔離ガード ``test_observability_isolation_guard`` が module-level の ~/.claude Path
  定数を検出して fail するのを避けるため）。

Phase 1 は完全重複（subset 包含）のみ報告し、部分重複は FP 抑制のため報告しない。決定論・LLM 非依存。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

try:
    from pj_slug import PJ_SLUG_ALIASES
except ImportError:  # pragma: no cover - pj_slug は同 lib 内に常在
    PJ_SLUG_ALIASES = {}

# 索引ファイル（memory 実体でないので比較集合から除外する）。
_INDEX_FILENAME = "MEMORY.md"


def default_projects_dir() -> Path:
    """CC projects dir を call-time で解決する（module-level 定数にしない・隔離ガード対策）。"""
    return Path.home() / ".claude" / "projects"


@dataclass
class DupPair:
    """完全重複ペア（residue が target に包含される）。"""

    residue_dir: str
    target_dir: str
    file_count: int
    rename_suspected: bool
    residue_path: str


@dataclass
class DupReport:
    """重複残骸レポート。"""

    pairs: List[DupPair] = field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        return bool(self.pairs)


def _hash_file(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _collect_memory_dirs(projects_dir: Path) -> List[Tuple[str, Path, Set[str]]]:
    """各 projects/<pj>/memory の ``*.md``（MEMORY.md 除く・非再帰）内容 hash 集合を集める。

    Returns:
        ``[(dir_name, memory_path, hashset)]``。hashset が空の dir は除外。
    """
    out: List[Tuple[str, Path, Set[str]]] = []
    if not projects_dir.is_dir():
        return out
    for pj_dir in sorted(projects_dir.iterdir()):
        if not pj_dir.is_dir():
            continue
        mem = pj_dir / "memory"
        if not mem.is_dir():
            continue
        hashes: Set[str] = set()
        for md in mem.glob("*.md"):  # *.md のみ・非再帰・jsonl 非対象
            if md.name == _INDEX_FILENAME:
                continue
            digest = _hash_file(md)
            if digest:
                hashes.add(digest)
        if hashes:
            out.append((pj_dir.name, mem, hashes))
    return out


def _matches_old_slug(dir_name: str, old_slugs: Set[str]) -> bool:
    """encoded dir 名が旧 slug に対応するか（末尾 ``-<old>`` または完全一致）。

    projects dir 名は cwd 絶対パスの ``/`` → ``-`` 置換なので、repo 名（旧 slug）は
    末尾セグメントとして現れる。``rl-anything`` のような ``-`` 込み slug も末尾一致で拾う。
    """
    for old in old_slugs:
        if dir_name == old or dir_name.endswith("-" + old):
            return True
    return False


def _is_residue_over(
    a_name: str,
    a_hashes: Set[str],
    b_name: str,
    b_hashes: Set[str],
    old_slugs: Set[str],
) -> bool:
    """a が b の残骸か（a の内容が b に完全包含される）。

    - 真部分集合（a ⊂ b）: a が残骸で確定。
    - 完全一致（a == b）: 双方向に成立してしまうため片側のみ True にする。rename 旧 slug の側を
      residue に倒し、判別できなければ dir 名の決定論タイブレークで片方に固定する。
    """
    if not a_hashes <= b_hashes:
        return False
    if a_hashes < b_hashes:
        return True
    # equal 集合: 双方向重複を避けて片側のみ residue にする。
    a_old = _matches_old_slug(a_name, old_slugs)
    b_old = _matches_old_slug(b_name, old_slugs)
    if a_old and not b_old:
        return True
    if b_old and not a_old:
        return False
    return a_name > b_name  # 決定論タイブレーク（equal でどちらも旧/新でない場合）


def detect_duplicate_memory_dirs(
    projects_dir: Path,
    old_slugs: Optional[Iterable[str]] = None,
) -> DupReport:
    """projects dir 配下の memory dir 完全重複ペアを検出する。

    Args:
        projects_dir: ``~/.claude/projects`` 相当（テストは tmp を渡す）。
        old_slugs: rename 旧 slug 集合（省略時は ``PJ_SLUG_ALIASES`` のキー）。
    """
    projects_dir = Path(projects_dir)
    slugs = set(old_slugs) if old_slugs is not None else set(PJ_SLUG_ALIASES.keys())
    entries = _collect_memory_dirs(projects_dir)

    report = DupReport()
    for a_name, a_path, a_hashes in entries:
        targets: List[Tuple[str, int]] = []
        for b_name, _b_path, b_hashes in entries:
            if b_name == a_name:
                continue
            if _is_residue_over(a_name, a_hashes, b_name, b_hashes, slugs):
                targets.append((b_name, len(b_hashes)))
        if not targets:
            continue
        # 最も完全な上位集合を代表 target に（file 数 desc, name asc の決定論順）。
        targets.sort(key=lambda t: (-t[1], t[0]))
        report.pairs.append(
            DupPair(
                residue_dir=a_name,
                target_dir=targets[0][0],
                file_count=len(a_hashes),
                rename_suspected=_matches_old_slug(a_name, slugs),
                residue_path=str(a_path),
            )
        )
    # rename 疑い→先頭、その後 dir 名で決定論ソート。
    report.pairs.sort(key=lambda p: (not p.rename_suspected, p.residue_dir))
    return report
