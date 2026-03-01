#!/usr/bin/env python3
"""analyze-project.py のユニットテスト"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# テスト対象のモジュールをインポートできるようにパスを追加
sys.path.insert(
    0, str(Path(__file__).parent.parent / "scripts")
)

from analyze_project import ProjectAnalyzer, DOMAIN_KEYWORDS, DOMAIN_CRITERIA


# --- テスト用フィクスチャ ---

@pytest.fixture
def temp_dir():
    """一時ディレクトリを作成"""
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def game_project(temp_dir):
    """ゲームプロジェクトのサンプルを作成"""
    # CLAUDE.md
    (temp_dir / "CLAUDE.md").write_text(
        "# Atlas Breeaders\n\n"
        "ナラティブゲームのシナリオ生成プロジェクト。\n"
        "キャラクターの性格と世界観を維持しつつ、\n"
        "プレイヤーの選択に応じたダイアログを生成する。\n"
        "NPCとの会話、クエストの進行、バトルシーンの描写を担当。\n"
        "キャラクターの声を一貫させることが重要。\n",
        encoding="utf-8",
    )
    # rules
    rules_dir = temp_dir / ".claude" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "tone.md").write_text(
        "キャラクターのトーンをシーンに合わせて調整する。\n",
        encoding="utf-8",
    )
    # skills
    skills_dir = temp_dir / ".claude" / "skills" / "narrative"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: narrative\ndescription: ナラティブ生成\n---\n\n"
        "# Narrative Skill\n\n物語のシーンを生成する。\n",
        encoding="utf-8",
    )
    return temp_dir


@pytest.fixture
def documentation_project(temp_dir):
    """ドキュメントプロジェクトのサンプルを作成"""
    (temp_dir / "CLAUDE.md").write_text(
        "# Docs Platform\n\n"
        "技術ドキュメントの管理プラットフォーム。\n"
        "front matter 必須のMarkdownページを生成。\n"
        "APIリファレンスやチュートリアルを含む。\n"
        "ドキュメントのコンテンツ品質とガイドの一貫性を重視。\n"
        "記事の構造とページ間のリンクを管理する。\n",
        encoding="utf-8",
    )
    return temp_dir


@pytest.fixture
def bot_project(temp_dir):
    """Botプロジェクトのサンプルを作成"""
    (temp_dir / "CLAUDE.md").write_text(
        "# Ooishi-kun Bot\n\n"
        "Slackボットのパーソナリティ定義。\n"
        "チャットでの会話トーンとペルソナを維持する。\n"
        "メッセージへの返信パターンとレスポンス品質を管理。\n"
        "ボットのあいさつと会話フローを設計する。\n",
        encoding="utf-8",
    )
    return temp_dir


@pytest.fixture
def minimal_project(temp_dir):
    """最小構成のプロジェクト（CLAUDE.md のみ、ドメインキーワードなし）"""
    (temp_dir / "CLAUDE.md").write_text(
        "# My Project\n\nシンプルなプロジェクト。\n",
        encoding="utf-8",
    )
    return temp_dir


@pytest.fixture
def project_with_pitfalls(game_project):
    """pitfalls.md を含むゲームプロジェクト"""
    pitfalls_dir = game_project / ".claude" / "skills" / "narrative" / "references"
    pitfalls_dir.mkdir(parents=True)
    (pitfalls_dir / "pitfalls.md").write_text(
        "# 既知の落とし穴\n\n"
        "- セリフが長すぎるとプレイヤーが離脱する\n"
        "- 選択肢が3つ以上だと複雑になりすぎる\n"
        "- 伏線を張りすぎると回収困難になる\n",
        encoding="utf-8",
    )
    return game_project


@pytest.fixture
def empty_project(temp_dir):
    """ソースファイルがないプロジェクト"""
    return temp_dir


# --- ドメイン推定テスト ---

class TestDomainDetection:
    """ドメイン推定のテスト"""

    def test_ゲームドメイン推定(self, game_project):
        analyzer = ProjectAnalyzer(str(game_project))
        result = analyzer.analyze()
        assert result["domain"] == "game"

    def test_ドキュメントドメイン推定(self, documentation_project):
        analyzer = ProjectAnalyzer(str(documentation_project))
        result = analyzer.analyze()
        assert result["domain"] == "documentation"

    def test_ボットドメイン推定(self, bot_project):
        analyzer = ProjectAnalyzer(str(bot_project))
        result = analyzer.analyze()
        assert result["domain"] == "bot"

    def test_汎用ドメインフォールバック(self, minimal_project):
        analyzer = ProjectAnalyzer(str(minimal_project))
        result = analyzer.analyze()
        assert result["domain"] == "general"

    def test_空プロジェクト(self, empty_project):
        analyzer = ProjectAnalyzer(str(empty_project))
        result = analyzer.analyze()
        assert result["domain"] == "general"
        assert result["keywords"] == []
        assert result["sources"] == []


# --- キーワード抽出テスト ---

class TestKeywordExtraction:
    """キーワード抽出のテスト"""

    def test_ゲームキーワード抽出(self, game_project):
        analyzer = ProjectAnalyzer(str(game_project))
        result = analyzer.analyze()
        assert len(result["keywords"]) > 0
        # ゲーム関連のキーワードが含まれる
        kw_lower = [k.lower() for k in result["keywords"]]
        assert any(
            k in kw_lower
            for k in ["キャラクター", "ナラティブ", "プレイヤー", "クエスト", "バトル"]
        )

    def test_キーワード上限(self, game_project):
        analyzer = ProjectAnalyzer(str(game_project))
        result = analyzer.analyze()
        assert len(result["keywords"]) <= 20


# --- criteria 構築テスト ---

class TestCriteriaBuild:
    """criteria 構築のテスト"""

    def test_ゲームcriteria(self, game_project):
        analyzer = ProjectAnalyzer(str(game_project))
        result = analyzer.analyze()
        criteria = result["criteria"]
        assert "axes" in criteria
        assert "anti_patterns" in criteria
        # ゲームドメインの軸が含まれる
        axis_names = [a["name"] for a in criteria["axes"]]
        assert "narrative_consistency" in axis_names
        assert "character_voice" in axis_names

    def test_重みの合計が1(self, game_project):
        analyzer = ProjectAnalyzer(str(game_project))
        result = analyzer.analyze()
        total_weight = sum(a["weight"] for a in result["criteria"]["axes"])
        assert abs(total_weight - 1.0) < 0.01

    def test_汎用criteria(self, minimal_project):
        analyzer = ProjectAnalyzer(str(minimal_project))
        result = analyzer.analyze()
        criteria = result["criteria"]
        axis_names = [a["name"] for a in criteria["axes"]]
        assert "clarity" in axis_names


# --- JSON 出力テスト ---

class TestJsonOutput:
    """JSON出力の構造テスト"""

    def test_出力構造(self, game_project):
        analyzer = ProjectAnalyzer(str(game_project))
        result = analyzer.analyze()
        # 必須フィールドの存在確認
        assert "domain" in result
        assert "keywords" in result
        assert "criteria" in result
        assert "sources" in result
        # JSON シリアライズ可能
        json_str = json.dumps(result, ensure_ascii=False)
        parsed = json.loads(json_str)
        assert parsed == result

    def test_sourcesにCLAUDE_mdが含まれる(self, game_project):
        analyzer = ProjectAnalyzer(str(game_project))
        result = analyzer.analyze()
        assert "CLAUDE.md" in result["sources"]

    def test_sourcesにruleが含まれる(self, game_project):
        analyzer = ProjectAnalyzer(str(game_project))
        result = analyzer.analyze()
        rule_sources = [s for s in result["sources"] if "rules/" in s]
        assert len(rule_sources) > 0

    def test_sourcesにskillが含まれる(self, game_project):
        analyzer = ProjectAnalyzer(str(game_project))
        result = analyzer.analyze()
        skill_sources = [s for s in result["sources"] if "SKILL.md" in s]
        assert len(skill_sources) > 0


# --- pitfalls.md テスト ---

class TestPitfalls:
    """pitfalls.md の検出と統合テスト"""

    def test_pitfallsありの場合(self, project_with_pitfalls):
        analyzer = ProjectAnalyzer(str(project_with_pitfalls))
        result = analyzer.analyze()
        anti_patterns = result["criteria"]["anti_patterns"]
        # pitfalls.md のパターンが追加されている
        assert any("セリフが長すぎる" in p for p in anti_patterns)
        assert any("選択肢が3つ以上" in p for p in anti_patterns)
        assert any("伏線を張りすぎる" in p for p in anti_patterns)

    def test_pitfallsなしの場合(self, game_project):
        analyzer = ProjectAnalyzer(str(game_project))
        result = analyzer.analyze()
        # 基本のanti_patternsのみ（pitfalls由来のものがない）
        base_count = len(DOMAIN_CRITERIA["game"]["anti_patterns"])
        assert len(result["criteria"]["anti_patterns"]) == base_count

    def test_pitfallsのsource追加(self, project_with_pitfalls):
        analyzer = ProjectAnalyzer(str(project_with_pitfalls))
        result = analyzer.analyze()
        pitfall_sources = [s for s in result["sources"] if "pitfalls.md" in s]
        assert len(pitfall_sources) > 0


# --- _parse_pitfalls テスト ---

class TestParsePitfalls:
    """pitfalls.md パース処理のテスト"""

    def test_箇条書き抽出(self):
        content = "# 見出し\n\n- パターンA\n- パターンB\n* パターンC\n"
        result = ProjectAnalyzer._parse_pitfalls(content)
        assert result == ["パターンA", "パターンB", "パターンC"]

    def test_見出し行は除外(self):
        content = "- # これは除外\n- 通常のパターン\n"
        result = ProjectAnalyzer._parse_pitfalls(content)
        assert result == ["通常のパターン"]

    def test_短すぎる行は除外(self):
        content = "- ab\n- 十分な長さのパターン\n"
        result = ProjectAnalyzer._parse_pitfalls(content)
        assert result == ["十分な長さのパターン"]

    def test_空の場合(self):
        result = ProjectAnalyzer._parse_pitfalls("")
        assert result == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
