"""evolution_memory モジュールのテスト。

TDD: テストを先に書く（tdd-first.md）。
tmp_path fixture を使い、実ファイルシステムを汚染しない。
"""
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_LIB_DIR = _SCRIPTS_DIR / "lib"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

import evolution_memory as em


@pytest.fixture(autouse=True)
def patch_data_dir(tmp_path, monkeypatch):
    """DATA_DIR を tmp_path に差し替えてテスト間の汚染を防ぐ。"""
    monkeypatch.setattr(em, "DATA_DIR", tmp_path)
    return tmp_path


# ──────────────────────────────────────────────────────────────────────────────
# save_winner テスト
# ──────────────────────────────────────────────────────────────────────────────

def test_save_winner_creates_file(tmp_path):
    """save_winner を呼ぶとファイルが作られること。"""
    em.save_winner(
        skill_name="my-skill",
        strategy="error_guided",
        score_before=0.6,
        score_after=0.8,
        patch_summary="Fix prompt wording",
    )
    memory_file = tmp_path / "evolution_memory.jsonl"
    assert memory_file.exists()


def test_save_winner_appends(tmp_path):
    """2回呼ぶと2レコードが追記されること。"""
    em.save_winner("skill-a", "error_guided", 0.5, 0.7, "first change")
    em.save_winner("skill-b", "llm_improve", 0.4, 0.9, "second change")
    memory_file = tmp_path / "evolution_memory.jsonl"
    lines = [l for l in memory_file.read_text().splitlines() if l.strip()]
    assert len(lines) == 2


def test_save_winner_record_fields(tmp_path):
    """保存レコードに必須フィールドが含まれること。"""
    em.save_winner("test-skill", "llm_improve", 0.3, 0.75, "patch summary here")
    memory_file = tmp_path / "evolution_memory.jsonl"
    line = memory_file.read_text().strip()
    record = json.loads(line)
    assert record["skill_name"] == "test-skill"
    assert record["strategy"] == "llm_improve"
    assert record["score_before"] == 0.3
    assert record["score_after"] == 0.75
    assert record["patch_summary"] == "patch summary here"
    assert "ts" in record


def test_save_winner_truncates_patch_summary(tmp_path):
    """patch_summary が 200 文字を超える場合に切り詰められること。"""
    long_summary = "x" * 300
    em.save_winner("skill", "error_guided", 0.1, 0.9, long_summary)
    memory_file = tmp_path / "evolution_memory.jsonl"
    record = json.loads(memory_file.read_text().strip())
    assert len(record["patch_summary"]) <= 200


# ──────────────────────────────────────────────────────────────────────────────
# load_patterns テスト
# ──────────────────────────────────────────────────────────────────────────────

def test_load_patterns_returns_all(tmp_path):
    """保存した件数だけ返ること。"""
    for i in range(5):
        em.save_winner(f"skill-{i}", "error_guided", 0.5, 0.8, f"change {i}")
    patterns = em.load_patterns()
    assert len(patterns) == 5


def test_load_patterns_empty_file(tmp_path):
    """ファイルが存在しないときに空リストを返すこと。"""
    patterns = em.load_patterns()
    assert patterns == []


def test_load_patterns_filter_by_skill(tmp_path):
    """skill_name 指定時に該当スキルだけ返ること。"""
    em.save_winner("alpha", "error_guided", 0.5, 0.8, "change alpha")
    em.save_winner("beta", "llm_improve", 0.4, 0.9, "change beta")
    em.save_winner("alpha", "error_guided", 0.6, 0.85, "change alpha 2")
    patterns = em.load_patterns(skill_name="alpha")
    assert len(patterns) == 2
    assert all(p["skill_name"] == "alpha" for p in patterns)


def test_load_patterns_limit(tmp_path):
    """limit 件数で切り詰められること。"""
    for i in range(8):
        em.save_winner("skill", "error_guided", 0.5, 0.8, f"change {i}")
    patterns = em.load_patterns(limit=3)
    assert len(patterns) == 3


def test_load_patterns_newest_first(tmp_path):
    """新しい順（降順）で返ること。"""
    import time
    for i in range(3):
        em.save_winner("skill", "error_guided", float(i) * 0.1, float(i) * 0.2 + 0.1, f"change {i}")
        time.sleep(0.01)  # タイムスタンプを確実に異なる値にする
    patterns = em.load_patterns()
    ts_list = [p["ts"] for p in patterns]
    assert ts_list == sorted(ts_list, reverse=True)


# ──────────────────────────────────────────────────────────────────────────────
# ローテーションテスト
# ──────────────────────────────────────────────────────────────────────────────

def test_rotation_max_1000(tmp_path):
    """1001件保存すると1000件に丸まること（古いものが削除される）。"""
    # 1001件保存
    for i in range(1001):
        em.save_winner("skill", "error_guided", 0.5, 0.8, f"change {i}")
    patterns = em.load_patterns(limit=2000)
    assert len(patterns) == 1000


def test_rotation_keeps_newest(tmp_path):
    """ローテーション後、最新 1000 件が残ること（最初に書いたものが消える）。"""
    for i in range(1001):
        em.save_winner("skill", "error_guided", 0.5, 0.8, f"change {i}")
    patterns = em.load_patterns(limit=2000)
    # 最新のパッチサマリは "change 1000" のはず
    newest_summaries = {p["patch_summary"] for p in patterns}
    assert "change 1000" in newest_summaries
    assert "change 0" not in newest_summaries
