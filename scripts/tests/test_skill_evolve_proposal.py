#!/usr/bin/env python3
"""skill_evolve の proposal 生成・適用・diff・customization テスト。

test_skill_evolve.py から分離（コアの assess/scoring とは別テーマ）。
- evolve_skill_proposal のテンプレート読み込み
- apply_evolve_proposal（適用 / references 作成 / error / backup / reason_refs / skipped guard）
- _count_diff_lines（差分行数）
- _parse_customization_response（バジェット / コードフェンス除去 / None フォールバック）
- emit_customize_request / ingest_customized_proposal（ファイルベース2相 + Path/str 契約）
"""
import json
import sys
from pathlib import Path
from unittest import mock

_lib_dir = Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"
sys.path.insert(0, str(_lib_dir))

from skill_evolve.proposal import count_diff_lines as _count_diff_lines
from skill_evolve import apply_evolve_proposal, evolve_skill_proposal


def _make_skill(tmp_path, name, content="# S\n\nif x: ...\n"):
    d = tmp_path / name
    d.mkdir()
    (d / "SKILL.md").write_text(content)
    return d


def _setup_templates(tmp_path):
    templates_dir = tmp_path / "skills" / "evolve" / "templates"
    templates_dir.mkdir(parents=True)
    (templates_dir / "self-evolve-sections.md").write_text(
        "## Pre-flight Check\n## Failure-triggered Learning\n"
    )
    (templates_dir / "pitfalls.md").write_text("## Active Pitfalls\n")


# --- evolve_skill_proposal（テンプレート読み込み） ---


def test_proposal_template_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "skill_evolve._plugin_root",
        tmp_path / "nonexistent",
    )
    result = evolve_skill_proposal("test-skill", tmp_path / "test-skill")
    assert result["error"] is not None
    assert "テンプレートファイルが見つかりません" in result["error"]


def test_proposal_with_templates(tmp_path, monkeypatch):
    # テンプレートディレクトリを作成
    templates_dir = tmp_path / "skills" / "evolve" / "templates"
    templates_dir.mkdir(parents=True)
    (templates_dir / "self-evolve-sections.md").write_text(
        "## Pre-flight Check\n\nCheck pitfalls.\n\n"
        "## Failure-triggered Learning\n\n| Trigger | Action |\n"
    )
    (templates_dir / "pitfalls.md").write_text(
        "## Active Pitfalls\n\n## Candidate Pitfalls\n\n## Graduated Pitfalls\n"
    )

    # スキルディレクトリ
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Test Skill\n")

    monkeypatch.setattr("skill_evolve._plugin_root", tmp_path)

    # [ADR-037] Phase 1c: LLM-free。決定論フォールバック（テンプレそのまま）を返す
    result = evolve_skill_proposal("test-skill", skill_dir)

    assert result["error"] is None
    assert "Pre-flight" in result["sections_to_add"]
    assert "Active Pitfalls" in result["pitfalls_template"]


# --- apply_evolve_proposal ---


def test_apply_evolve_proposal_success(tmp_path, monkeypatch):
    """正常適用: SKILL.md にセクション追記、references/pitfalls.md 作成、バックアップ作成。"""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    original_content = "# My Skill\n\nOriginal content.\n"
    (skill_dir / "SKILL.md").write_text(original_content)

    proposal = {
        "skill_name": "my-skill",
        "sections_to_add": "## Pre-flight Check\n\n## Failure-triggered Learning\n",
        "pitfalls_template": "## Active Pitfalls\n\n## Graduated Pitfalls\n",
        "skill_md_path": str(skill_dir / "SKILL.md"),
        "pitfalls_path": str(skill_dir / "references" / "pitfalls.md"),
        "error": None,
    }

    result = apply_evolve_proposal(proposal)
    assert result["applied"] is True
    assert result["error"] is None

    # SKILL.md にセクション追記されている
    updated = (skill_dir / "SKILL.md").read_text()
    assert "Pre-flight Check" in updated
    assert "Original content" in updated

    # references/pitfalls.md が作成されている
    pitfalls = skill_dir / "references" / "pitfalls.md"
    assert pitfalls.exists()
    assert "Active Pitfalls" in pitfalls.read_text()

    # バックアップが作成されている
    backup = skill_dir / "SKILL.md.pre-evolve-backup"
    assert backup.exists()
    assert backup.read_text() == original_content


def test_apply_evolve_proposal_creates_references_dir(tmp_path):
    """references/ ディレクトリがなくても自動作成される。"""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill\n")

    proposal = {
        "skill_name": "my-skill",
        "sections_to_add": "## Failure-triggered Learning\n",
        "pitfalls_template": "## Active Pitfalls\n",
        "skill_md_path": str(skill_dir / "SKILL.md"),
        "pitfalls_path": str(skill_dir / "references" / "pitfalls.md"),
        "error": None,
    }

    result = apply_evolve_proposal(proposal)
    assert result["applied"] is True
    assert (skill_dir / "references").is_dir()
    assert (skill_dir / "references" / "pitfalls.md").exists()


def test_apply_evolve_proposal_with_error():
    """proposal にエラーがある場合は適用しない。"""
    proposal = {
        "skill_name": "my-skill",
        "error": "テンプレートが見つかりません",
    }

    result = apply_evolve_proposal(proposal)
    assert result["applied"] is False
    assert result["error"] == "テンプレートが見つかりません"


def test_apply_evolve_proposal_backup_path_in_result(tmp_path):
    """結果に backup_path が含まれる。"""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill\n")

    proposal = {
        "skill_name": "my-skill",
        "sections_to_add": "## Failure-triggered Learning\n",
        "pitfalls_template": "## Active Pitfalls\n",
        "skill_md_path": str(skill_dir / "SKILL.md"),
        "pitfalls_path": str(skill_dir / "references" / "pitfalls.md"),
        "error": None,
    }

    result = apply_evolve_proposal(proposal)
    assert "backup_path" in result
    assert result["backup_path"].endswith(".pre-evolve-backup")


# --- reason_refs in apply_evolve_proposal (#201) ---


def test_apply_evolve_proposal_reason_refs_in_frontmatter(tmp_path, monkeypatch):
    """apply_evolve_proposal 後の SKILL.md frontmatter に reason_refs が含まれる (#201)。"""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill\n\nOriginal content.\n")

    corrections_file = tmp_path / "corrections.jsonl"
    corrections_file.write_text(
        json.dumps({"id": "corr-001", "last_skill": "my-skill", "timestamp": "2026-05-01T00:00:00+00:00"}) + "\n" +
        json.dumps({"id": "corr-002", "last_skill": "my-skill", "timestamp": "2026-05-01T01:00:00+00:00"}) + "\n"
    )

    proposal = {
        "skill_name": "my-skill",
        "sections_to_add": "## Pre-flight Check\n\n## Failure-triggered Learning\n",
        "pitfalls_template": "## Active Pitfalls\n",
        "skill_md_path": str(skill_dir / "SKILL.md"),
        "pitfalls_path": str(skill_dir / "references" / "pitfalls.md"),
        "error": None,
        "correction_ids": ["corr-001", "corr-002"],
    }

    result = apply_evolve_proposal(proposal)
    assert result["applied"] is True

    updated = (skill_dir / "SKILL.md").read_text()
    assert "reason_refs" in updated
    assert "corr-001" in updated
    assert "corr-002" in updated


def test_apply_evolve_proposal_no_reason_refs_when_empty(tmp_path):
    """correction_ids が空またはない場合は reason_refs なしでも正常適用できる (#201)。"""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill\n\nOriginal content.\n")

    proposal = {
        "skill_name": "my-skill",
        "sections_to_add": "## Pre-flight Check\n\n## Failure-triggered Learning\n",
        "pitfalls_template": "## Active Pitfalls\n",
        "skill_md_path": str(skill_dir / "SKILL.md"),
        "pitfalls_path": str(skill_dir / "references" / "pitfalls.md"),
        "error": None,
        # correction_ids なし
    }

    result = apply_evolve_proposal(proposal)
    assert result["applied"] is True


# --- apply_evolve_proposal skipped guard (#P1) ---


def test_apply_evolve_proposal_skipped_returns_early():
    """proposal が status:skipped の場合は KeyError なく早期リターンする。"""
    result = apply_evolve_proposal({"status": "skipped", "reason": "rejected_rate=35%"})
    assert result["applied"] is False
    assert result["skipped"] is True
    assert result["reason"] == "rejected_rate=35%"


# --- _count_diff_lines (#196) ---


def test_count_diff_lines_small_change():
    """少ない変更行数は正確にカウントされる。"""
    original = "line1\nline2\nline3\n"
    modified = "line1\nline2_changed\nline3\n"
    count = _count_diff_lines(original, modified)
    assert count == 2  # 1 removed + 1 added


def test_count_diff_lines_no_change():
    """変更なしは 0 を返す。"""
    text = "line1\nline2\nline3\n"
    assert _count_diff_lines(text, text) == 0


def test_count_diff_lines_many_changes():
    """多数の変更行数が正確にカウントされる。"""
    original = "\n".join(f"line{i}" for i in range(40))
    modified = "\n".join(f"changed{i}" for i in range(40))
    count = _count_diff_lines(original, modified)
    # 40 removed + 40 added = 80
    assert count == 80


# --- difflib bounded edit gate in _parse_customization_response (#196, #199, [ADR-037] Phase 1c) ---


def test_parse_customization_within_budget(tmp_path, monkeypatch):
    """diff 行数がバジェット以内なら Phase B 出力をそのまま返す。"""
    template = "## Pre-flight Check\n\n## Failure-triggered Learning\n"
    # Phase B が 2 行変更した出力（budget=30 以内）
    customized_output = "## Pre-flight Check (custom)\n\n## Failure-triggered Learning\n"

    from skill_evolve.proposal import _parse_customization_response
    result = _parse_customization_response(customized_output, template, budget=30)

    assert "custom" in result


def test_parse_customization_exceeds_budget_fallback(tmp_path, monkeypatch):
    """diff 行数がバジェットを超えた場合はテンプレートにフォールバックする (#196)。"""
    template = "## Pre-flight Check\n\n## Failure-triggered Learning\n"
    # Phase B が多数の行を変更した出力（budget=5 を超える）
    many_lines = "\n".join(f"changed line {i}" for i in range(20))

    from skill_evolve.proposal import _parse_customization_response
    result = _parse_customization_response(many_lines, template, budget=5)

    # フォールバックでテンプレートがそのまま返る
    assert result == template


def test_parse_customization_budget_override(tmp_path):
    """budget=10 が正しく判定に使われる (#199)。"""
    template = "original line 1\noriginal line 2\noriginal line 3\n"
    # 11 行変更 (budget=10 を 1 超)
    changed_lines = "\n".join(f"changed {i}" for i in range(11))

    from skill_evolve.proposal import _parse_customization_response
    result = _parse_customization_response(changed_lines, template, budget=10)

    assert result == template  # fallback


# --- _parse_customization_response の信頼境界 ---


def test_parse_customization_none_returns_template():
    from skill_evolve.proposal import _parse_customization_response
    template = "## Pre-flight Check\n## Failure-triggered Learning\n"
    assert _parse_customization_response(None, template) == template


def test_parse_customization_strips_code_fence():
    from skill_evolve.proposal import _parse_customization_response
    template = "## Pre-flight Check\n## Failure-triggered Learning\n"
    raw = "```\n## Pre-flight Check (X)\n## Failure-triggered Learning\n```"
    result = _parse_customization_response(raw, template, budget=30)
    assert "```" not in result
    assert "(X)" in result


# --- emit_customize_request / ingest_customized_proposal ---


def test_emit_customize_request_shape(tmp_path, monkeypatch):
    from skill_evolve import emit_customize_request
    _setup_templates(tmp_path)
    monkeypatch.setattr("skill_evolve._plugin_root", tmp_path)
    sd = _make_skill(tmp_path, "delta", content="# Delta Skill\n")

    out = emit_customize_request("delta", sd)
    assert len(out["requests"]) == 1
    assert out["requests"][0]["id"] == "delta"
    assert "カスタマイズ" in out["requests"][0]["prompt"]
    assert "_template" not in out["requests"][0]["meta"]


def test_emit_customize_request_template_missing(tmp_path, monkeypatch):
    from skill_evolve import emit_customize_request
    monkeypatch.setattr("skill_evolve._plugin_root", tmp_path / "nope")
    sd = _make_skill(tmp_path, "eps")
    out = emit_customize_request("eps", sd)
    assert out["requests"] == []
    assert "error" in out


def test_ingest_customized_proposal_builds_proposal(tmp_path, monkeypatch):
    from skill_evolve import emit_customize_request, ingest_customized_proposal
    _setup_templates(tmp_path)
    monkeypatch.setattr("skill_evolve._plugin_root", tmp_path)
    sd = _make_skill(tmp_path, "zeta", content="# Zeta\n")

    with mock.patch("skill_evolve.proposal.get_rejected_stats",
                    return_value={"rejected_rate": 0.0}):
        out = emit_customize_request("zeta", sd)
        responses = {"zeta": "## Pre-flight Check (zeta)\n## Failure-triggered Learning\n"}
        proposal = ingest_customized_proposal("zeta", sd, out["requests"], responses)

    assert proposal["error"] is None
    assert "(zeta)" in proposal["sections_to_add"]
    assert "Active Pitfalls" in proposal["pitfalls_template"]


def test_ingest_customized_proposal_fallback_on_missing_response(tmp_path, monkeypatch):
    from skill_evolve import emit_customize_request, ingest_customized_proposal
    _setup_templates(tmp_path)
    monkeypatch.setattr("skill_evolve._plugin_root", tmp_path)
    sd = _make_skill(tmp_path, "eta", content="# Eta\n")

    with mock.patch("skill_evolve.proposal.get_rejected_stats",
                    return_value={"rejected_rate": 0.0}):
        out = emit_customize_request("eta", sd)
        proposal = ingest_customized_proposal("eta", sd, out["requests"], {})

    # 応答欠損 → テンプレそのままにフォールバック
    assert proposal["error"] is None
    assert "Pre-flight Check" in proposal["sections_to_add"]


# --- #336: skill_dir は str で渡しても TypeError にならない（Path/str 契約統一） ---


def test_emit_customize_request_accepts_str_dir(tmp_path, monkeypatch):
    """skill_dir を str で渡しても `skill_dir / "SKILL.md"` で落ちない（#336）。

    assess_single_skill は str を受け入れるのに emit_* が Path 前提で TypeError に
    なる契約不整合を塞ぐ。入口で Path() 正規化されていれば str でも動く。
    """
    from skill_evolve import emit_customize_request
    _setup_templates(tmp_path)
    monkeypatch.setattr("skill_evolve._plugin_root", tmp_path)
    sd = _make_skill(tmp_path, "theta", content="# Theta Skill\n")

    out = emit_customize_request("theta", str(sd))  # str で渡す
    assert len(out["requests"]) == 1
    assert out["requests"][0]["id"] == "theta"


def test_ingest_customized_proposal_accepts_str_dir(tmp_path, monkeypatch):
    """ingest_customized_proposal も str の skill_dir を受け入れる（#336）。"""
    from skill_evolve import emit_customize_request, ingest_customized_proposal
    _setup_templates(tmp_path)
    monkeypatch.setattr("skill_evolve._plugin_root", tmp_path)
    sd = _make_skill(tmp_path, "iota", content="# Iota\n")

    with mock.patch("skill_evolve.proposal.get_rejected_stats",
                    return_value={"rejected_rate": 0.0}):
        out = emit_customize_request("iota", str(sd))
        responses = {"iota": "## Pre-flight Check (iota)\n## Failure-triggered Learning\n"}
        proposal = ingest_customized_proposal("iota", str(sd), out["requests"], responses)

    assert proposal["error"] is None
    assert "(iota)" in proposal["sections_to_add"]


# --- #350: apply_evolve_proposal が既存 pitfalls.md を上書きしないガード ---


def test_apply_preserves_existing_pitfalls(tmp_path):
    """既存 pitfalls.md がある場合、apply_evolve_proposal はその内容を上書きしない（#350）。"""
    skill_dir = tmp_path / "guarded-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Guarded Skill\n\nOriginal content.\n")

    # 既存の pitfalls.md に実エントリを書き込む
    refs_dir = skill_dir / "references"
    refs_dir.mkdir()
    existing_pitfalls = refs_dir / "pitfalls.md"
    existing_content = (
        "## Active Pitfalls\n\n"
        "- **pitfall-001**: 実運用で蓄積したエントリ\n"
        "- **pitfall-002**: 消えてはいけない知見\n\n"
        "## Graduated Pitfalls\n\n"
        "- **pitfall-old**: 卒業済み\n"
    )
    existing_pitfalls.write_text(existing_content)

    proposal = {
        "skill_name": "guarded-skill",
        "sections_to_add": "## Pre-flight Check\n\n## Failure-triggered Learning\n",
        "pitfalls_template": "## Active Pitfalls\n\n## Graduated Pitfalls\n",
        "skill_md_path": str(skill_dir / "SKILL.md"),
        "pitfalls_path": str(refs_dir / "pitfalls.md"),
        "error": None,
    }

    result = apply_evolve_proposal(proposal)
    assert result["applied"] is True

    # 既存エントリが保持されている（テンプレートで上書きされていない）
    actual = existing_pitfalls.read_text()
    assert "pitfall-001" in actual, "既存エントリが消えた（上書きバグ）"
    assert "pitfall-002" in actual, "既存エントリが消えた（上書きバグ）"
    assert "pitfall-old" in actual, "既存 Graduated エントリが消えた（上書きバグ）"


def test_apply_creates_pitfalls_when_not_exists(tmp_path):
    """pitfalls.md が存在しない場合は新規作成する（正常系）（#350）。"""
    skill_dir = tmp_path / "new-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# New Skill\n")

    pitfalls_path = skill_dir / "references" / "pitfalls.md"
    assert not pitfalls_path.exists()

    proposal = {
        "skill_name": "new-skill",
        "sections_to_add": "## Pre-flight Check\n\n## Failure-triggered Learning\n",
        "pitfalls_template": "## Active Pitfalls\n\n## Graduated Pitfalls\n",
        "skill_md_path": str(skill_dir / "SKILL.md"),
        "pitfalls_path": str(pitfalls_path),
        "error": None,
    }

    result = apply_evolve_proposal(proposal)
    assert result["applied"] is True
    assert pitfalls_path.exists()
    assert "Active Pitfalls" in pitfalls_path.read_text()


# --- #353⑨: reason_refs を correction 非由来時に非表示 ---


def test_rubric_checkpoint_no_reason_refs_without_correction_ids():
    """correction_ids が空/None のとき reason_refs 項目が出力に含まれない（#353）。"""
    from skill_evolve.rubric import rubric_checkpoint

    proposal_no_corrections = {
        "skill_name": "some-skill",
        "sections_to_add": "## Pre-flight Check\n\n## Failure-triggered Learning\n",
        "pitfalls_template": "## Active Pitfalls\n",
        "diff_lines": 5,
        # correction_ids キー自体なし
    }

    result = rubric_checkpoint("apply", proposal_no_corrections)
    # reason_refs 行が stdout_lines に存在しないこと
    lines_text = "\n".join(result["stdout_lines"])
    assert "reason_refs" not in lines_text, (
        f"correction なしなのに reason_refs が出力された: {result['stdout_lines']}"
    )


def test_rubric_checkpoint_no_reason_refs_with_empty_correction_ids():
    """correction_ids が空リストのとき reason_refs 項目が出力に含まれない（#353）。"""
    from skill_evolve.rubric import rubric_checkpoint

    proposal_empty = {
        "skill_name": "some-skill",
        "sections_to_add": "## Pre-flight Check\n\n## Failure-triggered Learning\n",
        "pitfalls_template": "## Active Pitfalls\n",
        "diff_lines": 5,
        "correction_ids": [],
    }

    result = rubric_checkpoint("apply", proposal_empty)
    lines_text = "\n".join(result["stdout_lines"])
    assert "reason_refs" not in lines_text, (
        f"correction_ids=[] なのに reason_refs が出力された: {result['stdout_lines']}"
    )


def test_rubric_checkpoint_reason_refs_shown_with_correction_ids():
    """correction_ids がある場合は reason_refs が評価される（従来通り）（#353）。"""
    from skill_evolve.rubric import rubric_checkpoint

    proposal_with_corrections = {
        "skill_name": "some-skill",
        "sections_to_add": "## Pre-flight Check\n\n## Failure-triggered Learning\n",
        "pitfalls_template": "## Active Pitfalls\n",
        "diff_lines": 5,
        "correction_ids": ["corr-001", "corr-002"],
    }

    result = rubric_checkpoint("apply", proposal_with_corrections)
    lines_text = "\n".join(result["stdout_lines"])
    assert "reason_refs" in lines_text, (
        f"correction_ids あるのに reason_refs が出力されない: {result['stdout_lines']}"
    )
