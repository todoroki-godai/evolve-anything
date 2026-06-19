"""skill_triggers.py のユニットテスト。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from skill_triggers import (
    extract_skill_triggers,
    normalize_skill_name,
    resolve_claude_md_path,
)


@pytest.fixture
def claude_md_with_triggers(tmp_path):
    """トリガーワード付きの CLAUDE.md。"""
    content = """\
# My Project

## Skills

- /channel-routing: チャンネルごとのBot設定を管理。トリガー: channel routing, チャンネルマッピング, bot追加
- /deploy-check: デプロイ前の確認チェック。Trigger: deploy check, デプロイ確認
- /my-skill: 説明文のみ

## Other Section

Something else.
"""
    path = tmp_path / "CLAUDE.md"
    path.write_text(content)
    return path


@pytest.fixture
def claude_md_trigger_variations(tmp_path):
    """トリガーワード記法バリエーション。"""
    content = """\
# Project

## Skills

- /skill-a: 説明。トリガー: word1, word2
- /skill-b: 説明。トリガーワード: word3, word4
- /skill-c: 説明。Trigger: word5, word6
- /skill-d: 説明。triggers: word7, word8
"""
    path = tmp_path / "CLAUDE.md"
    path.write_text(content)
    return path


def test_extract_with_triggers(claude_md_with_triggers):
    result = extract_skill_triggers(claude_md_with_triggers)
    assert len(result) == 3

    by_skill = {r["skill"]: r for r in result}
    assert set(by_skill["channel-routing"]["triggers"]) == {"channel routing", "チャンネルマッピング", "bot追加"}
    assert set(by_skill["deploy-check"]["triggers"]) == {"deploy check", "デプロイ確認"}


def test_fallback_when_no_triggers(claude_md_with_triggers):
    result = extract_skill_triggers(claude_md_with_triggers)
    by_skill = {r["skill"]: r for r in result}
    assert by_skill["my-skill"]["triggers"] == ["my-skill"]


def test_trigger_format_variations(claude_md_trigger_variations):
    result = extract_skill_triggers(claude_md_trigger_variations)
    assert len(result) == 4

    by_skill = {r["skill"]: r for r in result}
    assert by_skill["skill-a"]["triggers"] == ["word1", "word2"]
    assert by_skill["skill-b"]["triggers"] == ["word3", "word4"]
    assert by_skill["skill-c"]["triggers"] == ["word5", "word6"]
    assert by_skill["skill-d"]["triggers"] == ["word7", "word8"]


def test_claude_md_not_found(tmp_path):
    result = extract_skill_triggers(tmp_path / "nonexistent.md")
    assert result == []


def test_extract_from_project_root(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("## Skills\n\n- /test-skill: test. Trigger: testing\n")
    result = extract_skill_triggers(project_root=tmp_path)
    assert len(result) == 1
    assert result[0]["skill"] == "test-skill"
    assert result[0]["triggers"] == ["testing"]


def test_normalize_skill_name():
    assert normalize_skill_name("/channel-routing") == "channel-routing"
    assert normalize_skill_name("evolve-anything:channel-routing") == "channel-routing"
    assert normalize_skill_name("channel-routing") == "channel-routing"
    assert normalize_skill_name("/plugin:skill") == "skill"


def test_no_skills_section(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# Project\n\nNo skills here.\n")
    result = extract_skill_triggers(tmp_path / "CLAUDE.md")
    assert result == []


def test_key_skills_heading(tmp_path):
    """## Key Skills のような 'Skills' を含む複合見出しも認識する。"""
    content = "# Project\n\n## Key Skills\n\n- /docs-qa: QA workflow. Trigger: docs qa\n\n## Other\n\nstuff\n"
    (tmp_path / "CLAUDE.md").write_text(content)
    result = extract_skill_triggers(tmp_path / "CLAUDE.md")
    assert len(result) == 1
    assert result[0]["skill"] == "docs-qa"


def test_table_format_skills(tmp_path):
    """テーブル形式で記載されたスキルも認識する。"""
    content = (
        "# Project\n\n## Skills\n\n"
        "| スキル | 用途 |\n"
        "|--------|------|\n"
        "| `/generate-docs` | ドキュメント生成 |\n"
        "| `/docs-qa` | QA |\n"
        "| `/manage-repo` | リポジトリ管理 |\n\n"
        "## Other\n\nstuff\n"
    )
    (tmp_path / "CLAUDE.md").write_text(content)
    result = extract_skill_triggers(tmp_path / "CLAUDE.md")
    skill_names = [r["skill"] for r in result]
    assert "generate-docs" in skill_names
    assert "docs-qa" in skill_names
    assert "manage-repo" in skill_names


def test_bold_label_backtick_command_format(tmp_path):
    """`- **太字ラベル**: `/skill-name` - path` 形式を読める (#295 真因)。

    ハイフン直後が太字の非ASCIIラベルで、skill 名はコロン後ろのバッククォート内。
    旧パーサ `^-\\s+/?([a-zA-Z0-9_:-]+)\\s*[:：]` では 0 件しか拾えなかった。
    """
    content = (
        "# Project\n\n## Skills\n\n"
        "- **AWSデプロイ**: `/aws-deploy` - `.claude/skills/aws-deploy/SKILL.md`\n"
        "- **RAGデータ投入**: `/rag-ingest` - `.claude/skills/rag-ingest/SKILL.md`\n\n"
        "## Other\n\nstuff\n"
    )
    (tmp_path / "CLAUDE.md").write_text(content)
    result = extract_skill_triggers(tmp_path / "CLAUDE.md")
    skill_names = [r["skill"] for r in result]
    assert "aws-deploy" in skill_names
    assert "rag-ingest" in skill_names
    assert len(result) == 2


def test_bold_label_with_trigger(tmp_path):
    """太字ラベル形式でも行内 Trigger: を拾う。"""
    content = (
        "# Project\n\n## Skills\n\n"
        "- **デプロイ確認**: `/deploy-check` - チェック。Trigger: deploy check, デプロイ確認\n"
    )
    (tmp_path / "CLAUDE.md").write_text(content)
    result = extract_skill_triggers(tmp_path / "CLAUDE.md")
    assert len(result) == 1
    assert result[0]["skill"] == "deploy-check"
    assert set(result[0]["triggers"]) == {"deploy check", "デプロイ確認"}


def test_plain_list_format_still_works(tmp_path):
    """従来の `- /skill: ...` / `- skill: ...` 形式を退行させない。"""
    content = (
        "# Project\n\n## Skills\n\n"
        "- /plain-skill: 説明。Trigger: plain\n"
        "- bare-skill: 説明のみ\n"
    )
    (tmp_path / "CLAUDE.md").write_text(content)
    result = extract_skill_triggers(tmp_path / "CLAUDE.md")
    by_skill = {r["skill"]: r for r in result}
    assert by_skill["plain-skill"]["triggers"] == ["plain"]
    assert by_skill["bare-skill"]["triggers"] == ["bare-skill"]


def test_table_format_english_header_not_captured(tmp_path):
    """英語ヘッダ行（| Skill | Description |）はスキルとして誤抽出されない。"""
    content = (
        "# Project\n\n## Skills\n\n"
        "| Skill | Description |\n"
        "|-------|-------------|\n"
        "| `/generate-docs` | doc gen |\n"
        "| `/docs-qa` | QA |\n\n"
        "## Other\n\nstuff\n"
    )
    (tmp_path / "CLAUDE.md").write_text(content)
    result = extract_skill_triggers(tmp_path / "CLAUDE.md")
    skill_names = [r["skill"] for r in result]
    assert "generate-docs" in skill_names
    assert "docs-qa" in skill_names
    # ヘッダ行 "Skill" が phantom skill として混入していないこと
    assert "skill" not in [s.lower() for s in skill_names]
    assert len(result) == 2


# --- resolve_claude_md_path: shadow 環境での実体パス解決 (#295) ---


def test_resolve_direct(tmp_path):
    """project_root/CLAUDE.md が存在すればそれを返す。"""
    cm = tmp_path / "CLAUDE.md"
    cm.write_text("# Project\n")
    assert resolve_claude_md_path(project_root=tmp_path) == cm


def test_resolve_explicit_path(tmp_path):
    """claude_md_path を明示指定したらそれを優先する。"""
    cm = tmp_path / "CLAUDE.md"
    cm.write_text("# Project\n")
    assert resolve_claude_md_path(claude_md_path=cm) == cm


def test_resolve_none_when_absent_and_not_git(tmp_path):
    """CLAUDE.md が無く git repo でもない（非git shadow コピー）なら None。"""
    assert resolve_claude_md_path(project_root=tmp_path) is None


def test_resolve_git_root_fallback(tmp_path):
    """project_root が CLAUDE.md を持つ git repo のサブディレクトリでも、
    repo ルートの CLAUDE.md を実体パス基準で解決する（#295 shadow 対策）。"""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    (repo / "CLAUDE.md").write_text("# Repo\n\n## Skills\n\n- /s: x. Trigger: t\n")
    subdir = repo / "sub" / "dir"
    subdir.mkdir(parents=True)
    # subdir 自身は CLAUDE.md を持たないが、git ルートの CLAUDE.md を解決する
    resolved = resolve_claude_md_path(project_root=subdir)
    assert resolved == repo / "CLAUDE.md"


def test_extract_skill_triggers_uses_git_fallback(tmp_path):
    """extract_skill_triggers も git fallback で repo ルート CLAUDE.md を読む。"""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    (repo / "CLAUDE.md").write_text("# Repo\n\n## Skills\n\n- /my-skill: x. Trigger: t\n")
    subdir = repo / "sub"
    subdir.mkdir()
    result = extract_skill_triggers(project_root=subdir)
    assert [r["skill"] for r in result] == ["my-skill"]
