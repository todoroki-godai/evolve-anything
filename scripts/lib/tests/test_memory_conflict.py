"""memory_conflict ロジックの境界テスト（#83・決定論・LLM 非依存）。

同一 PJ 内の active memory fact から「同一 specific key を肯定 / 否定で言及する非両立ペア」
を保守的に検出する compute_conflicts の境界を固定する。実 ~/.claude を読まないよう
scripts/lib/tests/conftest.py の autouse isolate_home で HOME を隔離し、実装と同じ resolver
（``_resolve_memory_dir``）で memory dir を作る（synthetic false confidence 回避・#19）。
"""
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from audit import memory_conflict as mcf  # noqa: E402


def _memory_dir_for(project_dir: Path) -> Path:
    """project_dir の CC memory dir を実 resolver 経由で作って返す。"""
    mem = mcf._resolve_memory_dir(project_dir)
    mem.mkdir(parents=True, exist_ok=True)
    return mem


def _write(mem_dir: Path, name: str, body: str, *, superseded_at: str = "") -> Path:
    """memory fact を書く（superseded_at を渡すと時間降格扱いにする）。"""
    fm = ["name: " + name.removesuffix(".md"), "type: feedback"]
    if superseded_at:
        fm.append(f"superseded_at: '{superseded_at}'")
    path = mem_dir / name
    block = "\n".join(fm)
    path.write_text(f"---\n{block}\n---\n\n{body}\n", encoding="utf-8")
    return path


# ── applicable / floor ────────────────────────────────────────────
def test_no_memory_dir_returns_not_applicable(tmp_path):
    """memory dir 不在 → applicable=False。"""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    report = mcf.compute_conflicts(project_dir)
    assert report.applicable is False
    assert report.conflicts == []


def test_below_floor_gates(tmp_path):
    """active fact が FLOOR(3) 未満 → applicable=False（評価しない）。"""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    mem = _memory_dir_for(project_dir)
    _write(mem, "a.md", "新規開発は `gstack` フローで進める。")
    _write(mem, "b.md", "`gstack` を使わない。")
    report = mcf.compute_conflicts(project_dir)
    assert report.applicable is False
    assert report.total_facts == 2


# ── 矛盾なし ──────────────────────────────────────────────────────
def test_no_conflict_when_polarities_agree(tmp_path):
    """3 fact・対立極性なし → applicable=True・conflicts 空。"""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    mem = _memory_dir_for(project_dir)
    _write(mem, "a.md", "新規開発は `gstack` フローで進める。")
    _write(mem, "b.md", "`git diff` で確認する。")
    _write(mem, "c.md", "テスト本文のみ。")
    report = mcf.compute_conflicts(project_dir)
    assert report.applicable is True
    assert report.total_facts == 3
    assert report.conflicts == []


def test_contrast_pattern_not_self_conflict(tmp_path):
    """「A ではなく B」型は B=肯定 / A=否定 に割り当て、B の自己矛盾を作らない。"""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    mem = _memory_dir_for(project_dir)
    # file1 内で git diff は肯定（ではなく の後）。git status は否定（前）。
    _write(mem, "a.md", "確認は `git status` ではなく `git diff` を使う。")
    _write(mem, "b.md", "変更は `git diff` で見る。")
    _write(mem, "c.md", "無関係な本文。")
    report = mcf.compute_conflicts(project_dir)
    assert report.applicable is True
    # git diff は両ファイルで肯定、git status は否定のみ → 対立極性ペアなし。
    assert report.conflicts == []


# ── 矛盾あり ──────────────────────────────────────────────────────
def test_detects_conflict_pair_with_evidence(tmp_path):
    """同一 key を肯定 / 否定する 2 fact → 矛盾ペア（2 パス + 対立値）を検出。"""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    mem = _memory_dir_for(project_dir)
    _write(mem, "use_gstack.md", "新規開発は `gstack` フローで進める。")
    _write(mem, "avoid_gstack.md", "`gstack` を使わない。")
    _write(mem, "neutral.md", "無関係な本文。")
    report = mcf.compute_conflicts(project_dir)
    assert report.applicable is True
    assert report.total_facts == 3
    assert len(report.conflicts) == 1
    pair = report.conflicts[0]
    assert pair.key == "gstack"
    assert pair.pos_path.name == "use_gstack.md"
    assert pair.neg_path.name == "avoid_gstack.md"
    assert "gstack" in pair.pos_value
    assert "使わない" in pair.neg_value


def test_superseded_fact_excluded_from_conflict(tmp_path):
    """supersede 済み（時間降格＝解決済み矛盾）の fact は矛盾判定から除外する。"""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    mem = _memory_dir_for(project_dir)
    _write(mem, "use_gstack.md", "新規開発は `gstack` フローで進める。")
    _write(mem, "avoid_gstack.md", "`gstack` を使わない。",
           superseded_at="2000-01-01T00:00:00+00:00")
    _write(mem, "c.md", "無関係な本文 1。")
    _write(mem, "d.md", "無関係な本文 2。")
    report = mcf.compute_conflicts(project_dir)
    assert report.applicable is True
    # active = use_gstack, c, d（avoid_gstack は superseded で除外）→ 3 件・矛盾なし。
    assert report.total_facts == 3
    assert report.conflicts == []


def test_ambiguous_within_file_dropped(tmp_path):
    """同一ファイル内で同 key が肯定・否定両方 → ambiguous で drop（cross-file 矛盾を作らない）。"""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    mem = _memory_dir_for(project_dir)
    _write(mem, "a.md", "`foo-bar` を使う。`foo-bar` は禁止。")
    _write(mem, "b.md", "`foo-bar` を使う。")
    _write(mem, "c.md", "無関係な本文。")
    report = mcf.compute_conflicts(project_dir)
    assert report.applicable is True
    # a.md の foo-bar は ambiguous で drop → 否定極性が存在しない → 矛盾なし。
    assert report.conflicts == []
