"""記憶操作 capability（read/use/write/maintain）の決定論算出テスト（#19, advisory）。

OPD-Evolver（arXiv 2606.17628）由来。記憶を read/use/write/maintain の観点で評価し、
記憶の死蔵・未活用を可視化する advisory observability。fitness の重み軸にはしない。

reason 非永続化（memory_temporal.reinforce_memory の reason はファイルに書かれない）のため
read を独立軸にできず、read/use を統合した3軸算出（write / maintain / use_read）。

HOME 隔離必須（#457）: memory dir は ``Path.home()`` 由来のため、隔離しないと実環境の
memory（≈379 件）を読む。autouse fixture で isolate_home する。
"""
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_SCRIPTS = _PLUGIN_ROOT / "scripts"
for _p in (_LIB, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest  # noqa: E402


import memory_capability  # noqa: E402
from audit.sections_memory import build_memory_capability_section  # noqa: E402
from pj_slug import resolve_cc_memory_dir, resolve_pj_slug  # noqa: E402


def _memory_dir_for(project_dir: Path) -> Path:
    """project_dir の CC memory dir を実 resolver 経由で作って返す。

    実装と同じ ``_resolve_memory_dir`` を使うことで、fixture が誤った slug 名前空間で
    dir を作って実装と握り合う synthetic false confidence（#19 で実際に踏んだ）を防ぐ。
    """
    mem = memory_capability._resolve_memory_dir(project_dir)
    mem.mkdir(parents=True, exist_ok=True)
    return mem


def _write_memory(mem_dir: Path, name: str, fm_lines: list, body: str = "本文") -> Path:
    """temporal frontmatter 付き memory ファイルを書く。fm_lines が空なら frontmatter なし。"""
    path = mem_dir / name
    if fm_lines:
        fm = "\n".join(fm_lines)
        path.write_text(f"---\n{fm}\n---\n\n{body}\n", encoding="utf-8")
    else:
        path.write_text(f"{body}\n", encoding="utf-8")
    return path


# ── compute_memory_capability ─────────────────────────────────────────


def test_no_memory_dir_returns_not_applicable(tmp_path):
    """memory dir 不在（slug ディレクトリ自体が無い）→ applicable=False。"""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    result = memory_capability.compute_memory_capability(project_dir)
    assert result["applicable"] is False


def test_empty_dir_only_index_returns_not_applicable(tmp_path):
    """MEMORY.md のみ（memory 実体 0 件）→ applicable=False（索引は実体でない）。"""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    mem = _memory_dir_for(project_dir)
    (mem / "MEMORY.md").write_text("# index\n", encoding="utf-8")
    result = memory_capability.compute_memory_capability(project_dir)
    assert result["applicable"] is False


def test_mixed_files_axes_rates_correct(tmp_path):
    """update_count / stale / superseded / last_reinforced_at 混在で各率が正しい。"""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    mem = _memory_dir_for(project_dir)
    # MEMORY.md は集計対象外
    (mem / "MEMORY.md").write_text("# index\n", encoding="utf-8")

    # f1: temporal あり / 健全 / reinforced / update_count=4
    _write_memory(
        mem, "f1.md",
        [
            "name: f1",
            "update_count: 4",
            "last_reinforced_at: '2026-06-01T00:00:00+00:00'",
        ],
    )
    # f2: temporal あり / superseded（過去）/ reinforced / update_count=2
    _write_memory(
        mem, "f2.md",
        [
            "name: f2",
            "update_count: 2",
            "superseded_at: '2020-01-01T00:00:00+00:00'",
            "last_reinforced_at: '2026-06-01T00:00:00+00:00'",
        ],
    )
    # f3: temporal あり / stale（valid_from が decay_days より古い）/ 未 reinforced
    _write_memory(
        mem, "f3.md",
        [
            "name: f3",
            "valid_from: '2020-01-01T00:00:00+00:00'",
            "decay_days: 30",
        ],
    )
    # f4: frontmatter なし（temporal なし・健全扱い・未 reinforced・update_count=0）
    _write_memory(mem, "f4.md", [])

    result = memory_capability.compute_memory_capability(project_dir)
    assert result["applicable"] is True
    assert result["total"] == 4

    # write: temporal frontmatter を持つ件数 / 総数 = 3 / 4
    assert result["write"]["value"] == pytest.approx(0.75)
    assert result["write"]["evidence"]["with_frontmatter"] == 3
    assert result["write"]["evidence"]["total"] == 4

    # maintain: 健全率 = (非 stale かつ 非 superseded) / 総数
    # f1 健全, f2 superseded, f3 stale, f4 健全 → 2 / 4
    assert result["maintain"]["value"] == pytest.approx(0.5)
    assert result["maintain"]["evidence"]["stale"] == 1
    assert result["maintain"]["evidence"]["superseded"] == 1

    # use_read: reinforce 活性 = last_reinforced_at を持つ件数 / 総数 = 2 / 4
    assert result["use_read"]["value"] == pytest.approx(0.5)
    assert result["use_read"]["evidence"]["reinforced"] == 2
    # update_count median: [4, 2, 0, 0] → median = 1.0
    assert result["use_read"]["evidence"]["update_count_median"] == pytest.approx(1.0)


def test_memory_dir_uses_cc_path_encoding_not_repo_slug(tmp_path):
    """memory dir は CC パスエンコード（``-`` 連結）で解決し repo-basename slug は使わない。

    #19 回帰: 実装が ``resolve_pj_slug``（repo-basename）を使うと CC の実 memory dir
    （``~/.claude/projects/<path-encoded>/memory``）と名前空間が食い違い section が常に沈黙する。
    repo-basename slug の場所に memory を置いても拾われないことを assert して固定する。
    """
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    # 実 resolver は CC パスエンコード（resolve_cc_memory_dir）と一致する
    assert memory_capability._resolve_memory_dir(project_dir) == resolve_cc_memory_dir(project_dir)
    assert memory_capability._resolve_memory_dir(project_dir).name == "memory"

    # repo-basename slug（旧バグの場所）に memory 実体を置いても applicable=False のまま
    wrong = Path.home() / ".claude" / "projects" / resolve_pj_slug(project_dir) / "memory"
    if wrong != memory_capability._resolve_memory_dir(project_dir):
        wrong.mkdir(parents=True, exist_ok=True)
        _write_memory(wrong, "ghost.md", ["name: ghost", "update_count: 9"])
        assert memory_capability.compute_memory_capability(project_dir)["applicable"] is False


def test_slug_resolved_from_project_dir_not_cwd(tmp_path, monkeypatch):
    """slug 解決は引数 project_dir 由来で cwd 非依存（worktree 安全）。"""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    mem = _memory_dir_for(project_dir)
    _write_memory(mem, "f1.md", ["name: f1", "update_count: 1"])

    # cwd を別ディレクトリに変えても結果は project_dir に従う
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)

    result = memory_capability.compute_memory_capability(project_dir)
    assert result["applicable"] is True
    assert result["total"] == 1


# ── build_memory_capability_section ───────────────────────────────────


def test_section_none_when_no_memory(tmp_path):
    """memory 実体が無い PJ では section は None（沈黙）。"""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    mem = _memory_dir_for(project_dir)
    (mem / "MEMORY.md").write_text("# index\n", encoding="utf-8")
    assert build_memory_capability_section(project_dir) is None


def test_section_returns_lines_when_memory_exists(tmp_path):
    """memory 実体があれば section に3軸の行と限界注記が出る。"""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    mem = _memory_dir_for(project_dir)
    _write_memory(
        mem, "f1.md",
        ["name: f1", "update_count: 3", "last_reinforced_at: '2026-06-01T00:00:00+00:00'"],
    )
    _write_memory(mem, "f2.md", [])

    section = build_memory_capability_section(project_dir)
    assert section is not None
    combined = "\n".join(section)
    assert "Memory Capability" in combined
    assert "advisory" in combined
    # 3軸が surface される
    assert "write" in combined
    assert "maintain" in combined
    # use/read 軸の限界注記（reinforce は全件発火）が1行ある
    assert "SessionStart" in combined
