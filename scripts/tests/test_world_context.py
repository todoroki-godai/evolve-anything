"""tests/test_world_context.py — world_context モジュールの単体テスト。

LLM を直接呼ばない（subprocess.run をモック）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
_LIB_DIR = _SCRIPTS_DIR / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from world_context import (
    DEFAULT_WORLD_CONTEXT,
    WORLD_CONTEXT_FILE,
    generate_world_context,
    load_world_context,
    main,
    save_world_context,
)


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    return tmp_path / "rl-anything"


@pytest.fixture
def sample_world(tmp_data_dir: Path) -> dict:
    ctx = {
        "setting": "テスト用の塔。",
        "protagonist_title": "テスト使い",
        "environment_name": "テスト書架",
        "issue_name": "テスト歪み",
        "improvement_name": "テスト刻印",
        "generated_at": "2026-01-01",
        "project_slug": "test-proj",
        "total_evolve_count": 3,
        "last_evolve_date": "2026-01-01",
        "current_level": 5,
        "previous_level": 4,
    }
    tmp_data_dir.mkdir(parents=True, exist_ok=True)
    (tmp_data_dir / WORLD_CONTEXT_FILE).write_text(
        json.dumps(ctx, ensure_ascii=False), encoding="utf-8"
    )
    return ctx


# ── load_world_context ────────────────────────────────────────────────────────


def test_load_returns_none_when_file_missing(tmp_data_dir: Path) -> None:
    assert load_world_context(tmp_data_dir) is None


def test_load_returns_dict_when_file_exists(tmp_data_dir: Path, sample_world: dict) -> None:
    result = load_world_context(tmp_data_dir)
    assert result is not None
    assert result["environment_name"] == "テスト書架"
    assert result["total_evolve_count"] == 3


def test_load_returns_none_on_malformed_json(tmp_data_dir: Path) -> None:
    tmp_data_dir.mkdir(parents=True, exist_ok=True)
    (tmp_data_dir / WORLD_CONTEXT_FILE).write_text("not valid json", encoding="utf-8")
    assert load_world_context(tmp_data_dir) is None


# ── generate_world_context ────────────────────────────────────────────────────

_VALID_LLM_RESPONSE = json.dumps(
    {
        "setting": "魔法使いの塔。石版が光る。",
        "protagonist_title": "魔法使い",
        "environment_name": "魔法の塔",
        "issue_name": "歪みの影",
        "improvement_name": "輝く刻印",
    }
)


def _make_subprocess_mock(stdout: str, returncode: int = 0) -> MagicMock:
    mock = MagicMock()
    mock.returncode = returncode
    mock.stdout = stdout
    mock.stderr = ""
    return mock


def test_generate_uses_llm_response(tmp_data_dir: Path) -> None:
    with patch("world_context.subprocess.run") as mock_run:
        mock_run.return_value = _make_subprocess_mock(_VALID_LLM_RESPONSE)
        result = generate_world_context("# rl-anything\nTest project.", "test-proj")

    assert result["protagonist_title"] == "魔法使い"
    assert result["environment_name"] == "魔法の塔"
    assert result["total_evolve_count"] == 0
    assert result["last_evolve_date"] is None
    assert result["current_level"] is None
    assert result["project_slug"] == "test-proj"


def test_generate_falls_back_to_default_on_llm_error() -> None:
    with patch("world_context.subprocess.run") as mock_run:
        mock_run.return_value = _make_subprocess_mock("", returncode=1)
        result = generate_world_context("desc", "slug")

    assert result["protagonist_title"] == DEFAULT_WORLD_CONTEXT["protagonist_title"]
    assert result["environment_name"] == DEFAULT_WORLD_CONTEXT["environment_name"]


def test_generate_falls_back_to_default_on_malformed_json() -> None:
    with patch("world_context.subprocess.run") as mock_run:
        mock_run.return_value = _make_subprocess_mock("not json")
        result = generate_world_context("desc", "slug")

    assert result["setting"] == DEFAULT_WORLD_CONTEXT["setting"]


def test_generate_falls_back_to_default_on_timeout() -> None:
    import subprocess as _sp

    with patch("world_context.subprocess.run", side_effect=_sp.TimeoutExpired("claude", 60)):
        result = generate_world_context("desc", "slug")

    assert result["environment_name"] == DEFAULT_WORLD_CONTEXT["environment_name"]


def test_generate_includes_all_required_fields() -> None:
    with patch("world_context.subprocess.run") as mock_run:
        mock_run.return_value = _make_subprocess_mock(_VALID_LLM_RESPONSE)
        result = generate_world_context("desc", "my-project")

    required = {
        "setting", "protagonist_title", "environment_name",
        "issue_name", "improvement_name",
        "generated_at", "project_slug",
        "total_evolve_count", "last_evolve_date",
        "current_level", "previous_level",
    }
    assert required.issubset(result.keys())


# ── save_world_context ────────────────────────────────────────────────────────


def test_save_increments_total_evolve_count(tmp_data_dir: Path, sample_world: dict) -> None:
    saved = save_world_context(tmp_data_dir, sample_world)
    assert saved["total_evolve_count"] == 4

    # ファイルにも反映
    on_disk = json.loads((tmp_data_dir / WORLD_CONTEXT_FILE).read_text())
    assert on_disk["total_evolve_count"] == 4


def test_save_updates_last_evolve_date(tmp_data_dir: Path, sample_world: dict) -> None:
    import datetime

    saved = save_world_context(tmp_data_dir, sample_world)
    assert saved["last_evolve_date"] == datetime.date.today().isoformat()


def test_save_updates_level_when_env_score_given(tmp_data_dir: Path, sample_world: dict) -> None:
    # env_score=0.65 → Lv.7 (Experienced)
    saved = save_world_context(tmp_data_dir, sample_world, env_score=0.65)
    assert saved["current_level"] == 7
    assert saved["previous_level"] == 5  # sample_world["current_level"] == 5


def test_save_does_not_update_level_without_env_score(
    tmp_data_dir: Path, sample_world: dict
) -> None:
    saved = save_world_context(tmp_data_dir, sample_world)
    assert saved["current_level"] == 5  # unchanged
    assert saved["previous_level"] == 4  # unchanged


def test_save_creates_data_dir_if_missing(tmp_path: Path) -> None:
    new_dir = tmp_path / "new" / "nested"
    ctx = dict(DEFAULT_WORLD_CONTEXT)
    save_world_context(new_dir, ctx)
    assert (new_dir / WORLD_CONTEXT_FILE).exists()


def test_save_does_not_mutate_input_ctx(tmp_data_dir: Path) -> None:
    ctx = {"total_evolve_count": 0}
    original_count = ctx["total_evolve_count"]
    save_world_context(tmp_data_dir, ctx)
    assert ctx["total_evolve_count"] == original_count  # 入力は変更されない


# ── load → generate の再生成禁止（継続性保証） ────────────────────────────────


def test_second_load_returns_same_world_not_regenerated(
    tmp_data_dir: Path, sample_world: dict
) -> None:
    """JSON 既存時に generate を呼ばず load が同じ dict を返すこと（継続性保証）。"""
    first = load_world_context(tmp_data_dir)
    second = load_world_context(tmp_data_dir)

    assert first is not None
    assert second is not None
    assert first["environment_name"] == second["environment_name"]
    assert first["protagonist_title"] == second["protagonist_title"]


# ── CLI ──────────────────────────────────────────────────────────────────────


def test_cli_load_exits_1_when_no_file(tmp_data_dir: Path) -> None:
    ret = main(["--load", "--data-dir", str(tmp_data_dir)])
    assert ret == 1


def test_cli_load_exits_0_when_file_exists(
    tmp_data_dir: Path, sample_world: dict, capsys: pytest.CaptureFixture
) -> None:
    ret = main(["--load", "--data-dir", str(tmp_data_dir)])
    assert ret == 0
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed["environment_name"] == "テスト書架"


def test_cli_generate_saves_and_prints(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    data_dir = tmp_path / "rl-anything"
    with patch("world_context.subprocess.run") as mock_run:
        mock_run.return_value = _make_subprocess_mock(_VALID_LLM_RESPONSE)
        ret = main([
            "--generate",
            "--claude-md", str(tmp_path / "CLAUDE.md"),  # 存在しないが OK（空文字列になる）
            "--slug", "test-slug",
            "--data-dir", str(data_dir),
        ])

    assert ret == 0
    assert (data_dir / WORLD_CONTEXT_FILE).exists()
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed["project_slug"] == "test-slug"
