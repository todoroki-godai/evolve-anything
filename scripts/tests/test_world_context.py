"""tests/test_world_context.py — world_context モジュールの単体テスト。

claude -p 全廃（[ADR-037]）後はファイルベース2相のため LLM を一切呼ばない（mock 不要）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

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
    _slug_filename,
    _world_path,
    build_world_context_from_response,
    build_world_prompt,
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


# ── build_world_prompt / build_world_context_from_response（ファイルベース2相）──

_VALID_WORLD = {
    "setting": "魔法使いの塔。石版が光る。",
    "protagonist_title": "魔法使い",
    "environment_name": "魔法の塔",
    "issue_name": "歪みの影",
    "improvement_name": "輝く刻印",
}
_VALID_WORLD_JSON = json.dumps(_VALID_WORLD)


def test_build_world_prompt_embeds_description() -> None:
    prompt = build_world_prompt("# rl-anything\nTest project description.")
    assert "Test project description." in prompt
    assert "setting" in prompt  # テンプレートのキー説明が含まれる


def test_build_world_prompt_truncates_long_input() -> None:
    prompt = build_world_prompt("x" * 5000)
    # description は 600 文字に切り詰められる（テンプレート分は上乗せ）
    assert prompt.count("x") == 600


def test_build_context_uses_world_dict(tmp_data_dir: Path) -> None:
    result = build_world_context_from_response(_VALID_WORLD, "test-proj")
    assert result["protagonist_title"] == "魔法使い"
    assert result["environment_name"] == "魔法の塔"
    assert result["total_evolve_count"] == 0
    assert result["last_evolve_date"] is None
    assert result["current_level"] is None
    assert result["project_slug"] == "test-proj"


def test_build_context_accepts_json_string() -> None:
    result = build_world_context_from_response(_VALID_WORLD_JSON, "slug")
    assert result["protagonist_title"] == "魔法使い"


def test_build_context_extracts_nested_world() -> None:
    nested = json.dumps({"result": _VALID_WORLD})
    result = build_world_context_from_response(nested, "slug")
    assert result["environment_name"] == "魔法の塔"


def test_build_context_falls_back_on_empty() -> None:
    result = build_world_context_from_response("", "slug")
    assert result["protagonist_title"] == DEFAULT_WORLD_CONTEXT["protagonist_title"]
    assert result["environment_name"] == DEFAULT_WORLD_CONTEXT["environment_name"]


def test_build_context_falls_back_on_malformed_json() -> None:
    result = build_world_context_from_response("not json", "slug")
    assert result["setting"] == DEFAULT_WORLD_CONTEXT["setting"]


def test_build_context_falls_back_on_missing_keys() -> None:
    result = build_world_context_from_response({"setting": "partial only"}, "slug")
    assert result["setting"] == DEFAULT_WORLD_CONTEXT["setting"]  # 5キー未満は採用しない


def test_build_context_includes_all_required_fields() -> None:
    result = build_world_context_from_response(_VALID_WORLD, "my-project")
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

    # ファイルにも反映（sample_world は project_slug を持つので per-slug パスに書かれる）
    on_disk = json.loads(_world_path(tmp_data_dir, "test-proj").read_text())
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


# ── PJ 別スコープ（cross-project 汚染防止 / 案A） ─────────────────────────────


def test_slug_filename_sanitizes_unsafe_chars() -> None:
    # path 区切りや空白は安全文字に置換される（ディレクトリトラバーサル防止）
    assert _slug_filename("atlas-breeders") == "world-context-atlas-breeders.json"
    assert _slug_filename("a/b c") == "world-context-a_b_c.json"
    assert _slug_filename("../etc/passwd") == "world-context-.._etc_passwd.json"
    assert _slug_filename("") == "world-context-default.json"


def test_world_path_legacy_when_no_slug(tmp_data_dir: Path) -> None:
    assert _world_path(tmp_data_dir, "") == tmp_data_dir / WORLD_CONTEXT_FILE


def test_world_path_per_slug_when_slug_given(tmp_data_dir: Path) -> None:
    p = _world_path(tmp_data_dir, "atlas-breeders")
    assert p == tmp_data_dir / "world-contexts" / "world-context-atlas-breeders.json"


def test_load_with_slug_isolates_projects(tmp_data_dir: Path) -> None:
    """先に別PJの世界観を保存しても、異なる slug の load は None を返す（汚染しない）。"""
    ctx_docs = {
        **DEFAULT_WORLD_CONTEXT,
        "project_slug": "docs-platform",
        "environment_name": "書架DOCS",
        "total_evolve_count": 2,
    }
    save_world_context(tmp_data_dir, ctx_docs, slug="docs-platform")

    # 別PJ atlas-breeders で load → 既存ファイルがあっても None（再生成に流れる）
    assert load_world_context(tmp_data_dir, slug="atlas-breeders") is None

    # docs-platform 自身で load → 自分の世界観が返る
    got = load_world_context(tmp_data_dir, slug="docs-platform")
    assert got is not None
    assert got["environment_name"] == "書架DOCS"
    assert got["total_evolve_count"] == 3  # save が +1 する（2 → 3）


def test_save_derives_slug_from_ctx_project_slug(tmp_data_dir: Path) -> None:
    """slug を明示しなくても ctx.project_slug から per-slug パスを導出する。"""
    ctx = {**DEFAULT_WORLD_CONTEXT, "project_slug": "proj-x", "environment_name": "X"}
    save_world_context(tmp_data_dir, ctx)
    assert _world_path(tmp_data_dir, "proj-x").exists()
    assert load_world_context(tmp_data_dir, slug="proj-x")["environment_name"] == "X"


def test_save_explicit_slug_overrides_ctx(tmp_data_dir: Path) -> None:
    ctx = {**DEFAULT_WORLD_CONTEXT, "project_slug": "in-ctx", "environment_name": "Y"}
    save_world_context(tmp_data_dir, ctx, slug="explicit")
    assert _world_path(tmp_data_dir, "explicit").exists()
    assert load_world_context(tmp_data_dir, slug="explicit") is not None


def test_two_projects_keep_independent_worlds(tmp_data_dir: Path) -> None:
    save_world_context(
        tmp_data_dir,
        {**DEFAULT_WORLD_CONTEXT, "project_slug": "proj-a", "environment_name": "A"},
        slug="proj-a",
    )
    save_world_context(
        tmp_data_dir,
        {**DEFAULT_WORLD_CONTEXT, "project_slug": "proj-b", "environment_name": "B"},
        slug="proj-b",
    )
    assert load_world_context(tmp_data_dir, slug="proj-a")["environment_name"] == "A"
    assert load_world_context(tmp_data_dir, slug="proj-b")["environment_name"] == "B"


def test_legacy_global_file_not_returned_for_slugged_load(tmp_data_dir: Path) -> None:
    """旧来のグローバル world-context.json が残っていても slug 付き load は拾わない。"""
    tmp_data_dir.mkdir(parents=True, exist_ok=True)
    legacy = {**DEFAULT_WORLD_CONTEXT, "project_slug": "old-global"}
    (tmp_data_dir / WORLD_CONTEXT_FILE).write_text(
        json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
    )
    assert load_world_context(tmp_data_dir, slug="any-project") is None


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


def _write_response(tmp_path: Path, world: object, name: str = "responses.json") -> Path:
    """assistant が書いたと想定する responses.json を作る（Phase B 相当）。"""
    p = tmp_path / name
    p.write_text(json.dumps({"world": world}, ensure_ascii=False), encoding="utf-8")
    return p


def test_cli_emit_request_outputs_prompt_no_llm(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """--emit-request は生成リクエスト JSON を吐くだけ（LLM を呼ばない・Phase A）。"""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# proj\nA test project for prompts.", encoding="utf-8")
    ret = main([
        "--emit-request",
        "--claude-md", str(claude_md),
        "--slug", "test-slug",
        "--data-dir", str(tmp_path / "rl-anything"),
    ])
    assert ret == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["slug"] == "test-slug"
    assert len(doc["requests"]) == 1
    req = doc["requests"][0]
    assert req["id"] == "world"
    assert "A test project for prompts." in req["prompt"]


def test_cli_save_from_response_saves_and_prints(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    data_dir = tmp_path / "rl-anything"
    resp = _write_response(tmp_path, _VALID_WORLD)
    ret = main([
        "--save-from-response",
        "--response", str(resp),
        "--slug", "test-slug",
        "--data-dir", str(data_dir),
    ])
    assert ret == 0
    assert _world_path(data_dir, "test-slug").exists()
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["project_slug"] == "test-slug"
    assert parsed["protagonist_title"] == "魔法使い"


def test_cli_save_from_response_missing_world_uses_default(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """responses に world が欠損していても DEFAULT で保存され壊れない。"""
    data_dir = tmp_path / "rl-anything"
    resp = tmp_path / "empty.json"
    resp.write_text("{}", encoding="utf-8")
    ret = main([
        "--save-from-response",
        "--response", str(resp),
        "--slug", "slug-x",
        "--data-dir", str(data_dir),
    ])
    assert ret == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["protagonist_title"] == DEFAULT_WORLD_CONTEXT["protagonist_title"]


def test_cli_save_then_load_other_slug_isolated(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """--save-from-response --slug A の後、--load --slug B は exit 1（別PJの世界観を拾わない）。"""
    data_dir = tmp_path / "rl-anything"
    resp = _write_response(tmp_path, _VALID_WORLD)
    assert main([
        "--save-from-response",
        "--response", str(resp),
        "--slug", "proj-a",
        "--data-dir", str(data_dir),
    ]) == 0
    capsys.readouterr()  # drain

    # 別 slug で load → 見つからない
    assert main(["--load", "--slug", "proj-b", "--data-dir", str(data_dir)]) == 1
    # 同じ slug で load → 見つかる
    assert main(["--load", "--slug", "proj-a", "--data-dir", str(data_dir)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["project_slug"] == "proj-a"
