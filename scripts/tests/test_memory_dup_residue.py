"""旧 PJ memory の完全重複残骸検出テスト（#131, advisory・fleet 横断）。

``~/.claude/projects/*/memory/`` を走査し dir ペア間でファイル内容 hash 集合を比較。
ある dir の全ファイルが別 dir に内容一致で包含される（完全重複 subset）場合、残骸候補
として surface する。``pj_slug_aliases_for`` に載る旧 slug の dir は rename 由来ラベル。

検出関数 ``detect_duplicate_memory_dirs`` は projects dir を **引数で受ける**（実
~/.claude を読まない）。transcripts（*.jsonl）は走査・提案対象外（*.md のみ・memory/
サブ dir 限定・非再帰）。削除は提案のみで auto-apply しない。決定論・LLM 非依存。
"""
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_SCRIPTS = _PLUGIN_ROOT / "scripts"
for _p in (_LIB, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import memory_dup_residue  # noqa: E402
from audit.sections_memory import build_memory_dup_residue_section  # noqa: E402


def _make_memory_dir(projects: Path, pj_name: str, files: dict) -> Path:
    """projects/<pj_name>/memory/ に {filename: content} を書く。"""
    mem = projects / pj_name / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (mem / name).write_text(content, encoding="utf-8")
    return mem


# ── detect_duplicate_memory_dirs ──────────────────────────────────────


def test_no_projects_dir_empty(tmp_path):
    """projects dir 不在なら空レポート。"""
    report = memory_dup_residue.detect_duplicate_memory_dirs(tmp_path / "nope")
    assert report.has_findings is False


def test_distinct_dirs_no_findings(tmp_path):
    """内容が異なる memory dir 同士は重複と判定しない。"""
    projects = tmp_path / "projects"
    _make_memory_dir(projects, "pjA", {"a.md": "content A"})
    _make_memory_dir(projects, "pjB", {"b.md": "content B"})
    report = memory_dup_residue.detect_duplicate_memory_dirs(projects)
    assert report.has_findings is False


def test_full_subset_detected(tmp_path):
    """A の全ファイルが B に内容一致で包含されれば A が残骸候補。"""
    projects = tmp_path / "projects"
    shared = {"x.md": "shared x", "y.md": "shared y"}
    _make_memory_dir(projects, "old-pj", dict(shared))  # 残骸（部分集合）
    _make_memory_dir(projects, "new-pj", {**shared, "z.md": "only new"})  # 上位集合
    report = memory_dup_residue.detect_duplicate_memory_dirs(projects)
    assert report.has_findings is True
    assert len(report.pairs) == 1
    pair = report.pairs[0]
    assert pair.residue_dir == "old-pj"
    assert pair.target_dir == "new-pj"
    assert pair.file_count == 2


def test_equal_sets_reported_once(tmp_path):
    """内容集合が完全一致（equal）でもペアは1組だけ報告する（双方向重複を回避）。"""
    projects = tmp_path / "projects"
    same = {"a.md": "same a", "b.md": "same b"}
    _make_memory_dir(projects, "dir-one", dict(same))
    _make_memory_dir(projects, "dir-two", dict(same))
    report = memory_dup_residue.detect_duplicate_memory_dirs(projects)
    assert len(report.pairs) == 1


def test_rename_old_slug_labeled_and_prioritized_as_residue(tmp_path):
    """equal 集合で片方が旧 slug（rename 由来）ならそちらを残骸候補にしラベルする。"""
    projects = tmp_path / "projects"
    same = {"m.md": "shared memory"}
    # 旧 slug（PJ_SLUG_ALIASES のキー rl-anything）を末尾に持つ encoded dir 名
    _make_memory_dir(projects, "-Users-x-matsukaze-utils-rl-anything", dict(same))
    _make_memory_dir(projects, "-Users-x-matsukaze-utils-evolve-anything", dict(same))
    report = memory_dup_residue.detect_duplicate_memory_dirs(projects)
    assert len(report.pairs) == 1
    pair = report.pairs[0]
    assert pair.residue_dir == "-Users-x-matsukaze-utils-rl-anything"
    assert pair.rename_suspected is True


def test_jsonl_transcripts_ignored(tmp_path):
    """*.jsonl（transcripts）は走査対象外（*.md のみで比較）。"""
    projects = tmp_path / "projects"
    _make_memory_dir(projects, "pjA", {"a.md": "same"})
    memB = _make_memory_dir(projects, "pjB", {"a.md": "same"})
    # B に巨大 jsonl を置いても *.md 集合は同一 → equal ペアとして1組
    (memB / "transcript.jsonl").write_text("{}\n" * 100, encoding="utf-8")
    report = memory_dup_residue.detect_duplicate_memory_dirs(projects)
    assert len(report.pairs) == 1


def test_index_file_excluded_from_comparison(tmp_path):
    """MEMORY.md（索引）は比較集合から除外する（memory 実体のみ突合）。"""
    projects = tmp_path / "projects"
    _make_memory_dir(projects, "old", {"e.md": "entity", "MEMORY.md": "old index"})
    _make_memory_dir(projects, "new", {"e.md": "entity", "MEMORY.md": "new index different"})
    report = memory_dup_residue.detect_duplicate_memory_dirs(projects)
    # MEMORY.md が違っても entity 集合は一致 → 残骸検出される
    assert report.has_findings is True


# ── build_memory_dup_residue_section ──────────────────────────────────


def test_section_none_when_no_dup(tmp_path, monkeypatch):
    """完全重複が無ければ section は None（無ければ非表示）。"""
    projects = tmp_path / "projects"
    _make_memory_dir(projects, "pjA", {"a.md": "A"})
    monkeypatch.setattr(memory_dup_residue, "default_projects_dir", lambda: projects)
    assert build_memory_dup_residue_section(tmp_path) is None


def test_section_lists_dup_pairs(tmp_path, monkeypatch):
    """完全重複があれば section に残骸候補 / 重複先 / 件数が列挙される。"""
    projects = tmp_path / "projects"
    same = {"m.md": "shared"}
    _make_memory_dir(projects, "old-pj", dict(same))
    _make_memory_dir(projects, "new-pj", {**same, "extra.md": "x"})
    monkeypatch.setattr(memory_dup_residue, "default_projects_dir", lambda: projects)
    section = build_memory_dup_residue_section(tmp_path)
    assert section is not None
    combined = "\n".join(section)
    assert "old-pj" in combined
    assert "new-pj" in combined


def test_section_heading_marks_cross_pj_scope(tmp_path, monkeypatch):
    """#142-8a: 見出しに「全PJ横断」を明記する（belief_blocks/Token Consumption と同慣習）。

    このセクションは ``~/.claude/projects/*/memory`` を全 PJ 走査するため、当 PJ 別 audit
    でも無関係 PJ の重複が出る。見出しにスコープを明記しないと当 PJ 固有と誤解されるため、
    既存の全PJ横断セクションと同じく見出しに明示する。
    """
    projects = tmp_path / "projects"
    same = {"m.md": "shared"}
    _make_memory_dir(projects, "old-pj", dict(same))
    _make_memory_dir(projects, "new-pj", {**same, "extra.md": "x"})
    monkeypatch.setattr(memory_dup_residue, "default_projects_dir", lambda: projects)
    section = build_memory_dup_residue_section(tmp_path)
    assert section is not None
    heading = section[0]
    assert heading.startswith("## ")
    assert "全PJ横断" in heading
