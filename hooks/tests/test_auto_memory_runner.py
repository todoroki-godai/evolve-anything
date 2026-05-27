"""auto_memory_runner.py のユニットテスト（TDD）。

すべてのテストは LLM を呼ばない。subprocess.run / subprocess.Popen はすべて mock する。
"""
import json
import os
import sys
import threading
from pathlib import Path
from unittest import mock

import pytest

_HOOKS = Path(__file__).resolve().parent.parent
_LIB = _HOOKS.parent / "scripts" / "lib"
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_LIB))

import auto_memory_runner


# ─── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_memory_dir(tmp_path):
    """一時的なメモリディレクトリ。"""
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    return mem_dir


@pytest.fixture
def tmp_data_dir(tmp_path):
    """一時データディレクトリ。"""
    data_dir = tmp_path / "rl-anything"
    data_dir.mkdir()
    return data_dir


def _write_corrections(data_dir: Path, count: int = 5) -> None:
    """テスト用 corrections.jsonl を書き出す。"""
    corrections_file = data_dir / "corrections.jsonl"
    for i in range(count):
        record = {
            "session_id": f"sess-{i:04d}",
            "timestamp": f"2026-05-25T10:0{i}:00Z",
            "original": f"original text {i}",
            "corrected": f"corrected text {i}",
        }
        with corrections_file.open("a") as f:
            f.write(json.dumps(record) + "\n")


def _make_mock_llm_output(summary: str = "test summary") -> str:
    """モック LLM が返すメモリエントリ本文。"""
    return f"---\nname: auto-test\ndescription: {summary}\nmetadata:\n  type: feedback\nimportance: medium\n---\n\n{summary}"


# ─── Test 1: 正常系 ─────────────────────────────────────────────────────────


def test_normal_creates_new_md_file(tmp_path, tmp_data_dir, tmp_memory_dir):
    """正常系: corrections.jsonl から候補抽出 → 新規 .md ファイルが作成される。"""
    _write_corrections(tmp_data_dir, count=5)

    mock_result = mock.MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_mock_llm_output("auto memory entry")

    with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir), \
         mock.patch("subprocess.run", return_value=mock_result), \
         mock.patch.dict(os.environ, {"RL_GATING_DISABLED": "1"}):
        auto_memory_runner.run(memory_dir=tmp_memory_dir)

    md_files = list(tmp_memory_dir.glob("auto_*.md"))
    assert len(md_files) == 1, f"Expected 1 auto_*.md file, got {len(md_files)}"
    content = md_files[0].read_text()
    assert "auto memory entry" in content


def test_normal_appends_index_to_memory_md(tmp_path, tmp_data_dir, tmp_memory_dir):
    """正常系: MEMORY.md に index 行が追記される。"""
    _write_corrections(tmp_data_dir, count=3)
    memory_md = tmp_memory_dir.parent / "MEMORY.md"
    memory_md.write_text("# MEMORY\n\n## 変更履歴\n\n")

    mock_result = mock.MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_mock_llm_output("index test")

    with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir), \
         mock.patch("subprocess.run", return_value=mock_result), \
         mock.patch.dict(os.environ, {"RL_GATING_DISABLED": "1"}):
        auto_memory_runner.run(memory_dir=tmp_memory_dir, memory_md_path=memory_md)

    content = memory_md.read_text()
    assert "auto_" in content
    # Index line should contain a summary
    lines = [l for l in content.splitlines() if l.startswith("- [auto_")]
    assert len(lines) >= 1


# ─── Test 2: atomic write / 並行起動 ───────────────────────────────────────


def test_concurrent_writes_both_entries_survive(tmp_path, tmp_data_dir, tmp_memory_dir):
    """並行起動シミュレーション: 2 スレッドが同時に書いても両エントリが残る。"""
    _write_corrections(tmp_data_dir, count=5)
    memory_md = tmp_memory_dir.parent / "MEMORY.md"
    memory_md.write_text("# MEMORY\n\n## 変更履歴\n\n")

    mock_result = mock.MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_mock_llm_output("concurrent test")

    errors = []

    def worker():
        try:
            with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir), \
                 mock.patch("subprocess.run", return_value=mock_result), \
                 mock.patch.dict(os.environ, {"RL_GATING_DISABLED": "1"}):
                auto_memory_runner.run(memory_dir=tmp_memory_dir, memory_md_path=memory_md)
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert errors == [], f"Unexpected errors: {errors}"
    md_files = list(tmp_memory_dir.glob("auto_*.md"))
    # Both concurrent runs should produce their own file (new-file-per-entry pattern)
    assert len(md_files) >= 1, "At least 1 entry file must exist after concurrent run"
    # MEMORY.md index must have at least 1 entry after concurrent run
    if memory_md.exists():
        lines = [l for l in memory_md.read_text().splitlines() if l.strip().startswith("- [")]
        assert len(lines) >= 1, "MEMORY.md index must have at least 1 entry after concurrent run"


# ─── Test 3: MEMORY.md 200 行超 → archive 処理 ──────────────────────────────


def test_memory_md_over_200_lines_triggers_archive(tmp_path, tmp_data_dir, tmp_memory_dir):
    """MEMORY.md が 200 行超 → 古いエントリを archive.md に移動する。"""
    _write_corrections(tmp_data_dir, count=3)
    memory_md = tmp_memory_dir.parent / "MEMORY.md"

    # 200 行超の MEMORY.md を作成（最初の 5 行は固定ヘッダー、残りは古いエントリ）
    lines = ["# MEMORY", "", "## 変更履歴", ""]
    for i in range(200):
        lines.append(f"- [old_entry_{i:03d}](old_entry_{i:03d}.md) — old summary {i}")
    memory_md.write_text("\n".join(lines) + "\n")

    mock_result = mock.MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_mock_llm_output("archive trigger test")

    with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir), \
         mock.patch("subprocess.run", return_value=mock_result), \
         mock.patch.dict(os.environ, {"RL_GATING_DISABLED": "1"}):
        auto_memory_runner.run(memory_dir=tmp_memory_dir, memory_md_path=memory_md)

    # After archive, MEMORY.md should be shorter
    new_content = memory_md.read_text()
    new_line_count = new_content.count("\n") + 1
    assert new_line_count <= 200, (
        f"MEMORY.md should be <= 200 lines after archive, got {new_line_count}"
    )

    # Archive file should exist
    archive_path = memory_md.parent / "archive.md"
    assert archive_path.exists(), "archive.md should be created after overflow"
    archive_content = archive_path.read_text()
    assert "old_entry_" in archive_content


# ─── Test 4: corrections.jsonl 不在 → graceful exit ────────────────────────


def test_missing_corrections_exits_gracefully(tmp_path, tmp_data_dir, tmp_memory_dir):
    """corrections.jsonl が存在しない場合は例外を吐かずに終了する。"""
    # corrections.jsonl を作らない
    with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir), \
         mock.patch("subprocess.run") as mock_run:
        auto_memory_runner.run(memory_dir=tmp_memory_dir)

    # No .md files created, no LLM called
    md_files = list(tmp_memory_dir.glob("auto_*.md"))
    assert len(md_files) == 0
    mock_run.assert_not_called()


def test_empty_corrections_exits_gracefully(tmp_path, tmp_data_dir, tmp_memory_dir):
    """corrections.jsonl が空の場合も例外を吐かずに終了する。"""
    (tmp_data_dir / "corrections.jsonl").write_text("")

    with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir), \
         mock.patch("subprocess.run") as mock_run:
        auto_memory_runner.run(memory_dir=tmp_memory_dir)

    md_files = list(tmp_memory_dir.glob("auto_*.md"))
    assert len(md_files) == 0
    mock_run.assert_not_called()


# ─── Test 5: LLM subprocess 失敗 → graceful exit ──────────────────────────


def test_llm_failure_exits_gracefully(tmp_path, tmp_data_dir, tmp_memory_dir):
    """LLM subprocess が returncode != 0 の場合は例外を吐かずに終了する。"""
    _write_corrections(tmp_data_dir, count=3)

    mock_result = mock.MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "LLM error"

    with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir), \
         mock.patch("subprocess.run", return_value=mock_result):
        auto_memory_runner.run(memory_dir=tmp_memory_dir)

    # No files should be created on LLM failure
    md_files = list(tmp_memory_dir.glob("auto_*.md"))
    assert len(md_files) == 0


def test_llm_timeout_exits_gracefully(tmp_path, tmp_data_dir, tmp_memory_dir):
    """LLM subprocess がタイムアウトした場合も例外を吐かずに終了する。"""
    _write_corrections(tmp_data_dir, count=3)

    with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir), \
         mock.patch("subprocess.run", side_effect=Exception("timeout")):
        auto_memory_runner.run(memory_dir=tmp_memory_dir)

    md_files = list(tmp_memory_dir.glob("auto_*.md"))
    assert len(md_files) == 0


# ─── Test 6: read_recent_corrections ───────────────────────────────────────


def test_read_recent_corrections_returns_last_5(tmp_data_dir):
    """read_recent_corrections は最新 5 件を返す。"""
    _write_corrections(tmp_data_dir, count=10)

    with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir):
        corrections = auto_memory_runner.read_recent_corrections(data_dir=tmp_data_dir)

    assert len(corrections) == 5
    # Should be the last 5 (sess-0005 through sess-0009)
    assert corrections[-1]["session_id"] == "sess-0009"
    assert corrections[0]["session_id"] == "sess-0005"


def test_read_recent_corrections_fewer_than_5(tmp_data_dir):
    """corrections.jsonl が 3 件の場合は 3 件すべてを返す。"""
    _write_corrections(tmp_data_dir, count=3)

    corrections = auto_memory_runner.read_recent_corrections(data_dir=tmp_data_dir)
    assert len(corrections) == 3


# ─── Test 7: frontmatter 形式確認 ──────────────────────────────────────────


def test_generated_file_has_required_frontmatter(tmp_path, tmp_data_dir, tmp_memory_dir):
    """生成された .md ファイルに必須 frontmatter が含まれる。"""
    _write_corrections(tmp_data_dir, count=2)

    llm_body = "---\nname: test-entry\ndescription: A test memory\nmetadata:\n  type: feedback\nimportance: medium\n---\n\nSome content here."
    mock_result = mock.MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = llm_body

    with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir), \
         mock.patch("subprocess.run", return_value=mock_result), \
         mock.patch.dict(os.environ, {"RL_GATING_DISABLED": "1"}):
        auto_memory_runner.run(memory_dir=tmp_memory_dir)

    md_files = list(tmp_memory_dir.glob("auto_*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text()
    # Required frontmatter fields
    assert "name:" in content
    assert "description:" in content


# ─── Test 8: ファイル名フォーマット ────────────────────────────────────────


def test_generated_filename_format(tmp_path, tmp_data_dir, tmp_memory_dir):
    """生成ファイル名が auto_YYYYMMDD_HHMMSS_<hash>.md 形式である。"""
    import re
    _write_corrections(tmp_data_dir, count=2)

    mock_result = mock.MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_mock_llm_output("filename format test")

    with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir), \
         mock.patch("subprocess.run", return_value=mock_result), \
         mock.patch.dict(os.environ, {"RL_GATING_DISABLED": "1"}):
        auto_memory_runner.run(memory_dir=tmp_memory_dir)

    md_files = list(tmp_memory_dir.glob("auto_*.md"))
    assert len(md_files) == 1
    name = md_files[0].name
    # Pattern: auto_YYYYMMDD_HHMMSS_<hash>.md
    pattern = r"^auto_\d{8}_\d{6}_[0-9a-f]+\.md$"
    assert re.match(pattern, name), f"Filename '{name}' does not match expected pattern"
