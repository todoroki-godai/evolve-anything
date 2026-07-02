"""optimize_core.py の単体テスト。

extract_markdown / format_gate_reason / determine_strategy など純粋関数を中心に検証する。
LLM 呼び出し（call_llm）は subprocess.run をモック。
"""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

# sys.path は conftest.py が設定済み
# optimize_core は skills/.../scripts/ にあるので手動追加
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_OPTIMIZER_DIR = _SCRIPTS_DIR.parent / "skills" / "genetic-prompt-optimizer" / "scripts"
if str(_OPTIMIZER_DIR) not in sys.path:
    sys.path.insert(0, str(_OPTIMIZER_DIR))

import optimize_core as core

# optimize.py (PopulationBroadcastOptimizer) のために追加
if str(_OPTIMIZER_DIR) not in sys.path:
    sys.path.insert(0, str(_OPTIMIZER_DIR))
import importlib.util as _ilu
_opt_spec = _ilu.spec_from_file_location("optimize", _OPTIMIZER_DIR / "optimize.py")
_opt_mod = _ilu.module_from_spec(_opt_spec)
_opt_spec.loader.exec_module(_opt_mod)
PopulationBroadcastOptimizer = _opt_mod.PopulationBroadcastOptimizer


# ── extract_markdown ──────────────────────────────────────────────────


def test_extract_markdown_code_block():
    text = "some preamble\n```markdown\n# Hello\ncontent\n```\ntrailing"
    result = core.extract_markdown(text)
    assert result == "# Hello\ncontent"


def test_extract_markdown_plain_code_block():
    text = "```\n# Plain block\n```"
    result = core.extract_markdown(text)
    assert result == "# Plain block"


def test_extract_markdown_no_block_falls_back():
    text = "  raw content without fences  "
    result = core.extract_markdown(text)
    assert result == "raw content without fences"


def test_extract_markdown_picks_longest():
    text = "```markdown\nshort\n```\n```markdown\nlonger content here\n```"
    result = core.extract_markdown(text)
    assert result == "longer content here"


def test_extract_markdown_empty_string():
    assert core.extract_markdown("") is None


# ── format_gate_reason ───────────────────────────────────────────────


def test_format_gate_reason_none():
    assert core.format_gate_reason(None) == "不明な理由"


def test_format_gate_reason_empty():
    assert core.format_gate_reason("empty") == "パッチ内容が空です"


def test_format_gate_reason_line_limit():
    reason = "line_limit_exceeded(200/150)"
    result = core.format_gate_reason(reason)
    assert "行数制限超過" in result
    assert "line_limit_exceeded" in result


def test_format_gate_reason_forbidden():
    reason = "forbidden_pattern(TODO)"
    result = core.format_gate_reason(reason)
    assert "禁止パターン" in result


def test_format_gate_reason_frontmatter():
    assert core.format_gate_reason("frontmatter_lost") == "YAML frontmatter が消失しました"


def test_format_gate_reason_char_limit():
    # #120 GEPA: 文字数上限超過の理由がユーザー向けに変換される。
    reason = "char_limit_exceeded(120000/100000)"
    result = core.format_gate_reason(reason)
    assert "文字数制限超過" in result
    assert "char_limit_exceeded" in result


# ── run_regression_gate max_chars 配線（#120）──────────────────────────


def test_run_regression_gate_threads_max_chars():
    # 行数は少なくても char_limit を超えれば block（行内 bloat 捕捉）。
    content = "# Skill\n" + "x" * 500
    passed, reason = core.run_regression_gate(
        content, None, max_lines=500, pitfall_path=None, max_chars=100
    )
    assert passed is False
    assert reason.startswith("char_limit_exceeded")


def test_run_regression_gate_max_chars_none_skips():
    # max_chars 未指定なら char ゲート非適用（後方互換）。
    content = "# Skill\n" + "x" * 500
    passed, reason = core.run_regression_gate(
        content, None, max_lines=500, pitfall_path=None
    )
    assert passed is True


# ── determine_strategy ──────────────────────────────────────────────


def test_determine_strategy_auto_with_corrections():
    corrections = [{"message": "fix this"}]
    assert core.determine_strategy("auto", corrections) == "error_guided"


def test_determine_strategy_auto_no_corrections():
    assert core.determine_strategy("auto", []) == "llm_improve"


def test_determine_strategy_error_guided_fallback(capsys):
    # corrections なしで error_guided を指定 → llm_improve にフォールバック
    result = core.determine_strategy("error_guided", [])
    assert result == "llm_improve"
    captured = capsys.readouterr()
    assert "フォールバック" in captured.out


def test_determine_strategy_error_guided_with_corrections():
    corrections = [{"message": "bug"}]
    assert core.determine_strategy("error_guided", corrections) == "error_guided"


def test_determine_strategy_llm_improve():
    assert core.determine_strategy("llm_improve", []) == "llm_improve"


# ── collect_corrections ─────────────────────────────────────────────


def test_collect_corrections_filters_applied(tmp_path):
    f = tmp_path / "corrections.jsonl"
    records = [
        {"last_skill": "my-skill", "reflect_status": "applied", "message": "skip me"},
        {"last_skill": "my-skill", "message": "keep me"},
        {"last_skill": "other-skill", "message": "irrelevant"},
    ]
    f.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    result = core.collect_corrections("my-skill", f, max_items=10)
    assert len(result) == 1
    assert result[0]["message"] == "keep me"


def test_collect_corrections_max_limit(tmp_path):
    f = tmp_path / "corrections.jsonl"
    records = [{"last_skill": "sk", "message": f"m{i}"} for i in range(20)]
    f.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    result = core.collect_corrections("sk", f, max_items=5)
    assert len(result) == 5
    # 直近 5 件であること
    assert result[-1]["message"] == "m19"


def test_collect_corrections_missing_file(tmp_path):
    result = core.collect_corrections("sk", tmp_path / "nonexistent.jsonl", max_items=10)
    assert result == []


# ── collect_context ─────────────────────────────────────────────────


def test_collect_context_reads_pitfalls(tmp_path):
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    refs = skill_dir / "references"
    refs.mkdir()
    pitfalls = refs / "pitfalls.md"
    pitfalls.write_text("# pitfalls\n## foo\nbar", encoding="utf-8")

    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: my-skill\n---\n", encoding="utf-8")

    # plugin_root を tmp_path にして audit_script が見つからないようにする
    ctx = core.collect_context(skill_file, tmp_path, "my-skill")
    assert "pitfalls" in ctx
    assert "# pitfalls" in ctx["pitfalls"]


def test_collect_context_no_pitfalls(tmp_path):
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("---\nname: sk\n---\n", encoding="utf-8")

    ctx = core.collect_context(skill_file, tmp_path, "sk")
    assert "pitfalls" not in ctx


def test_collect_context_truncates_large_pitfalls(tmp_path):
    # #120 GEPA 入力肥大化ガード: 上限超の pitfalls.md は切り詰めて context 投入する。
    skill_dir = tmp_path / "my-skill"
    (skill_dir / "references").mkdir(parents=True)
    huge = "# pitfalls\n" + "x" * (core.MAX_CONTEXT_PITFALLS_CHARS + 5000)
    (skill_dir / "references" / "pitfalls.md").write_text(huge, encoding="utf-8")
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: my-skill\n---\n", encoding="utf-8")

    ctx = core.collect_context(skill_file, tmp_path, "my-skill")
    assert "pitfalls" in ctx
    # 上限 + 切り詰めマーカー分だけに収まる（原文全長は投入しない）。
    assert len(ctx["pitfalls"]) < len(huge)
    assert len(ctx["pitfalls"]) <= core.MAX_CONTEXT_PITFALLS_CHARS + 50
    assert "切り詰め" in ctx["pitfalls"]


def test_collect_context_keeps_small_pitfalls_intact(tmp_path):
    # 上限内の pitfalls は全文保持（切り詰めない）。
    skill_dir = tmp_path / "sk"
    (skill_dir / "references").mkdir(parents=True)
    body = "# pitfalls\n## foo\nbar"
    (skill_dir / "references" / "pitfalls.md").write_text(body, encoding="utf-8")
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: sk\n---\n", encoding="utf-8")

    ctx = core.collect_context(skill_file, tmp_path, "sk")
    assert ctx["pitfalls"] == body
    assert "切り詰め" not in ctx["pitfalls"]


# ── record_pitfall ───────────────────────────────────────────────────


def test_record_pitfall_creates_file(tmp_path):
    skill_file = tmp_path / "SKILL.md"
    core.record_pitfall(str(skill_file), "gate", "forbidden_pattern(TODO)", 0.5)

    pitfalls_file = tmp_path / "references" / "pitfalls.md"
    assert pitfalls_file.exists()
    content = pitfalls_file.read_text(encoding="utf-8")
    assert "forbidden_pattern(TODO)" in content


def test_record_pitfall_no_duplicate(tmp_path):
    skill_file = tmp_path / "SKILL.md"
    core.record_pitfall(str(skill_file), "gate", "some_pattern", None)
    core.record_pitfall(str(skill_file), "gate", "some_pattern", None)

    pitfalls_file = tmp_path / "references" / "pitfalls.md"
    content = pitfalls_file.read_text(encoding="utf-8")
    assert content.count("some_pattern") == 1


def test_record_pitfall_rotation(tmp_path):
    skill_file = tmp_path / "SKILL.md"
    refs = tmp_path / "references"
    refs.mkdir()
    # 既存の 1001 行分のエントリを作る（ローテーションが起きること）
    rows = [f"| gate | pattern_{i} | - |" for i in range(1001)]
    existing = "| Source | Pattern | Score |\n|--------|---------|-------|\n" + "\n".join(rows) + "\n"
    (refs / "pitfalls.md").write_text(existing, encoding="utf-8")

    core.record_pitfall(str(skill_file), "gate", "new_pattern", None)

    pitfalls_file = refs / "pitfalls.md"
    content = pitfalls_file.read_text(encoding="utf-8")
    data_rows = [l for l in content.strip().split("\n") if l.strip().startswith("|") and "Source" not in l and "---" not in l]
    # 最大 PITFALLS_MAX_ROWS(50) に収まること
    assert len(data_rows) <= core.PITFALLS_MAX_ROWS


# ── call_llm (subprocess mock) ───────────────────────────────────────


def test_call_llm_success():
    fake_output = "```markdown\n# Patched\ncontent\n```\n"
    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(returncode=0, stdout=fake_output)
        result, error = core.call_llm("some prompt", claude_cwd=None)
    assert error is None
    assert result == "# Patched\ncontent"


def test_call_llm_error_returncode():
    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(returncode=1, stdout="", stderr="err")
        result, error = core.call_llm("prompt", claude_cwd=None)
    assert result is None
    assert "エラーコード" in error


def test_call_llm_timeout():
    import subprocess
    with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 180)):
        result, error = core.call_llm("prompt", claude_cwd=None)
    assert result is None
    assert "タイムアウト" in error


def test_call_llm_not_found():
    with mock.patch("subprocess.run", side_effect=FileNotFoundError()):
        result, error = core.call_llm("prompt", claude_cwd=None)
    assert result is None
    assert "見つかりません" in error


# ── build_patch_prompt ───────────────────────────────────────────────


def test_build_patch_prompt_error_guided():
    corrections = [{"message": "fix X", "correction_type": "edit", "extracted_learning": "use Y"}]
    prompt = core.build_patch_prompt(
        skill_content="# skill content",
        corrections=corrections,
        context={},
        strategy="error_guided",
        is_rule_file=False,
        max_lines=500,
    )
    assert "fix X" in prompt
    assert "use Y" in prompt
    assert "500 行以内" in prompt


def test_build_patch_prompt_llm_improve():
    prompt = core.build_patch_prompt(
        skill_content="# skill",
        corrections=[],
        context={"pitfalls": "## known issues\nfoo"},
        strategy="llm_improve",
        is_rule_file=False,
        max_lines=500,
    )
    assert "known issues" in prompt
    assert "汎用改善" in prompt or "改善方針" in prompt


def test_build_patch_prompt_rule_file_constraint():
    prompt = core.build_patch_prompt(
        skill_content="some rule",
        corrections=[],
        context={},
        strategy="llm_improve",
        is_rule_file=True,
        max_lines=10,
    )
    assert "10 行以内" in prompt
    assert "ルール" in prompt


# ── run_regression_gate ──────────────────────────────────────────────


def test_run_regression_gate_passes():
    content = "---\nname: test\n---\n# content"
    original = content
    passed, reason = core.run_regression_gate(content, original, max_lines=500, pitfall_path=None)
    assert passed is True
    assert reason is None


def test_run_regression_gate_fails_empty():
    passed, reason = core.run_regression_gate("", None, max_lines=500, pitfall_path=None)
    assert passed is False
    assert reason is not None


def test_run_regression_gate_standalone_import():
    """optimize_core を単独 import しても regression_gate が解決できること。"""
    # optimize.py を経由せずに optimize_core だけ import した場合も動くことを確認
    # (sys.path 自己設定のテスト)
    import importlib
    import importlib.util
    spec = importlib.util.find_spec("optimize_core")
    assert spec is not None, "optimize_core が単独で見つかること"
    # run_regression_gate を呼び出して ImportError が出ないこと
    passed, reason = core.run_regression_gate("# content", None, max_lines=500, pitfall_path=None)
    # ImportError が起きなければ OK（pass/fail の値は問わない）
    assert isinstance(passed, bool)


# ── run_custom_fitness ───────────────────────────────────────────────


def test_run_custom_fitness_default_returns_none(tmp_path):
    result = core.run_custom_fitness("content", "default", tmp_path)
    assert result is None


def test_run_custom_fitness_missing_file(tmp_path):
    result = core.run_custom_fitness("content", "nonexistent_func", tmp_path)
    assert result is None


def test_run_custom_fitness_success(tmp_path):
    fitness_dir = tmp_path / "scripts" / "rl" / "fitness"
    fitness_dir.mkdir(parents=True)
    (fitness_dir / "my_func.py").write_text(
        "import sys; print('0.75'); sys.exit(0)", encoding="utf-8"
    )
    import os
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = core.run_custom_fitness("content", "my_func", tmp_path)
    finally:
        os.chdir(old_cwd)
    assert result == pytest.approx(0.75)


def test_run_custom_fitness_clamps_to_01(tmp_path):
    fitness_dir = tmp_path / "scripts" / "rl" / "fitness"
    fitness_dir.mkdir(parents=True)
    (fitness_dir / "overflow.py").write_text(
        "import sys; print('999.0'); sys.exit(0)", encoding="utf-8"
    )
    import os
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = core.run_custom_fitness("content", "overflow", tmp_path)
    finally:
        os.chdir(old_cwd)
    assert result == pytest.approx(1.0)


def test_run_custom_fitness_error_exit(tmp_path):
    fitness_dir = tmp_path / "scripts" / "rl" / "fitness"
    fitness_dir.mkdir(parents=True)
    (fitness_dir / "bad.py").write_text(
        "import sys; sys.exit(1)", encoding="utf-8"
    )
    import os
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = core.run_custom_fitness("content", "bad", tmp_path)
    finally:
        os.chdir(old_cwd)
    assert result is None


# ── PopulationBroadcastOptimizer ─────────────────────────────────────


def _make_skill(tmp_path: Path) -> Path:
    """テスト用スキルファイルを作成して返す。"""
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("# My Skill\nSome content.\n", encoding="utf-8")
    return skill_file


def test_population_broadcast_generate_variants(tmp_path):
    """call_llm を mock して n=3 候補が生成されること。"""
    skill_file = _make_skill(tmp_path)
    fake_content = "# Improved Skill\nBetter content.\n"
    fake_output = f"```markdown\n{fake_content}```\n"

    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(returncode=0, stdout=fake_output)
        optimizer = PopulationBroadcastOptimizer(
            skill_path=str(skill_file),
            plugin_root=str(tmp_path),
            target_skill_name="SKILL",
            n=3,
        )
        result = optimizer.run()

    # subprocess.run が n=3 回呼ばれること（各候補で1回）
    assert mock_run.call_count == 3
    assert result["n_candidates"] == 3


def test_population_broadcast_pre_check_warn(tmp_path, capsys):
    """pre_check の warnings が出力されること（passed=True で続行）。

    generate_candidate を直接呼んで pre_check の warn-only 動作を検証する。
    """
    # API シグネチャ消失を誘発: original に def bar があるが、候補には含めない
    original = "def bar():\n    pass\n"
    # 候補は "bar" を含まない
    candidate_content = "# Completely rewritten skill\nNo original functions here.\n"

    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout=f"```markdown\n{candidate_content}```\n",
        )
        result = core.generate_candidate(
            prompt="dummy prompt",
            original_content=original,
            claude_cwd=None,
            max_lines=500,
            pitfall_path=None,
        )

    captured = capsys.readouterr()
    # pre_check warn が出力されること
    assert "[pre_check warn]" in captured.out
    assert "bar" in captured.out
    # passed=True（warn-only なのでゲート通過）
    assert result["passed"] is True
    assert result["content"] is not None


def test_population_broadcast_select_winner(tmp_path):
    """スコアなし（fitness_func=default）時に最初の通過候補が winner となること。"""
    skill_file = _make_skill(tmp_path)
    fake_content = "# Improved\nContent.\n"
    fake_output = f"```markdown\n{fake_content}```\n"

    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(returncode=0, stdout=fake_output)
        optimizer = PopulationBroadcastOptimizer(
            skill_path=str(skill_file),
            plugin_root=str(tmp_path),
            target_skill_name="SKILL",
            n=3,
            fitness_func="default",
        )
        result = optimizer.run()

    # スコアが None のとき winner が選ばれていること
    assert result["winner"] is not None
    assert result["winner"]["fitness"] is None
    # ファイルが上書きされていること
    assert skill_file.read_text(encoding="utf-8") == result["winner"]["content"]


def test_population_broadcast_partial_failure(tmp_path):
    """3候補中1件が regression gate 失敗でも残り2件で続行すること。"""
    skill_file = _make_skill(tmp_path)

    # 1件目は空（gate 失敗）、2件目・3件目は正常
    good_content = "# Good content.\n"
    call_count = [0]

    def fake_subprocess_run(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            # 空コンテンツ → gate 失敗
            return mock.Mock(returncode=0, stdout="```markdown\n\n```\n")
        return mock.Mock(returncode=0, stdout=f"```markdown\n{good_content}```\n")

    with mock.patch("subprocess.run", side_effect=fake_subprocess_run):
        optimizer = PopulationBroadcastOptimizer(
            skill_path=str(skill_file),
            plugin_root=str(tmp_path),
            target_skill_name="SKILL",
            n=3,
        )
        result = optimizer.run()

    # gate 失敗した1件を除いた2件が通過すること
    assert result["passed_count"] >= 1
    # winner が存在すること
    assert result["winner"] is not None
