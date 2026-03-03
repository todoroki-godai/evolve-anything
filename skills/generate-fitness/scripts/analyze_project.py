#!/usr/bin/env python3
"""プロジェクト分析スクリプト

CLAUDE.md・.claude/rules/・.claude/skills/ を読み取り、
ドメイン特性・キーワード・品質基準を JSON で出力する。

LLM 呼び出しなし（ルールベース分析のみ）。

使用方法:
    python3 analyze-project.py --project-root /path/to/project
    python3 analyze-project.py  # カレントディレクトリを分析
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

WORKFLOW_STATS_PATH = Path.home() / ".claude" / "rl-anything" / "workflow_stats.json"


# --- ドメイン定義 ---

DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    "game": [
        "game", "ゲーム", "narrative", "ナラティブ", "character", "キャラクター",
        "dialogue", "ダイアログ", "quest", "クエスト", "story", "物語",
        "npc", "player", "プレイヤー", "scene", "シーン", "world", "世界観",
        "battle", "バトル", "item", "アイテム", "level", "レベル",
    ],
    "documentation": [
        "document", "ドキュメント", "docs", "documentation", "front matter",
        "frontmatter", "markdown", "page", "ページ", "article", "記事",
        "content", "コンテンツ", "blog", "ブログ", "wiki", "guide", "ガイド",
        "tutorial", "チュートリアル", "api reference", "changelog",
    ],
    "bot": [
        "bot", "ボット", "chat", "チャット", "personality", "パーソナリティ",
        "persona", "ペルソナ", "tone", "トーン", "response", "レスポンス",
        "conversation", "会話", "slack", "discord", "line", "message",
        "メッセージ", "greeting", "あいさつ", "reply", "返信",
    ],
}

DOMAIN_CRITERIA: Dict[str, Dict[str, Any]] = {
    "game": {
        "axes": [
            {"name": "narrative_consistency", "weight": 0.3, "description": "物語・世界観の一貫性"},
            {"name": "character_voice", "weight": 0.25, "description": "キャラクターの声・個性の維持"},
            {"name": "instruction_clarity", "weight": 0.25, "description": "指示の明確さと具体性"},
            {"name": "structure_quality", "weight": 0.2, "description": "スキル構造の品質"},
        ],
        "anti_patterns": [
            "キャラクター設定の矛盾",
            "世界観に合わない表現",
            "曖昧なゲームメカニクスの記述",
        ],
    },
    "documentation": {
        "axes": [
            {"name": "accuracy", "weight": 0.3, "description": "技術的正確性"},
            {"name": "completeness", "weight": 0.25, "description": "必要な情報の網羅性"},
            {"name": "readability", "weight": 0.25, "description": "読みやすさと構造の明確さ"},
            {"name": "consistency", "weight": 0.2, "description": "用語・スタイルの一貫性"},
        ],
        "anti_patterns": [
            "front matter の欠落",
            "リンク切れの記述",
            "古い情報の残存",
        ],
    },
    "bot": {
        "axes": [
            {"name": "personality_adherence", "weight": 0.3, "description": "ペルソナ・トーンの一貫性"},
            {"name": "response_quality", "weight": 0.25, "description": "応答品質と適切性"},
            {"name": "instruction_clarity", "weight": 0.25, "description": "指示の明確さ"},
            {"name": "edge_case_handling", "weight": 0.2, "description": "エッジケースへの対処"},
        ],
        "anti_patterns": [
            "ペルソナからの逸脱",
            "不適切なトーンの混在",
            "曖昧な応答条件",
        ],
    },
    "general": {
        "axes": [
            {"name": "clarity", "weight": 0.3, "description": "指示の明確さ"},
            {"name": "completeness", "weight": 0.25, "description": "必要な情報の網羅性"},
            {"name": "structure", "weight": 0.25, "description": "論理的な構造"},
            {"name": "practicality", "weight": 0.2, "description": "実用性"},
        ],
        "anti_patterns": [
            "曖昧な指示",
            "矛盾する記述",
            "冗長な説明",
        ],
    },
}


class ProjectAnalyzer:
    """プロジェクトのドメイン特性を分析"""

    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()
        self.sources: List[str] = []
        self._all_text = ""

    def analyze(self) -> Dict[str, Any]:
        """分析を実行し、結果を辞書で返す"""
        # 1. ソースファイルを読み込む
        texts = self._load_sources()
        self._all_text = "\n".join(texts)

        # 2. キーワード抽出
        keywords = self._extract_keywords()

        # 3. ドメイン推定
        domain = self._detect_domain(keywords)

        # 4. criteria 構築
        criteria = self._build_criteria(domain)

        # 5. pitfalls.md の検出と統合
        pitfall_patterns = self._load_pitfalls()
        if pitfall_patterns:
            criteria["anti_patterns"].extend(pitfall_patterns)

        # 6. fitness-criteria.md の読み込み（存在時のみ）
        user_criteria = self._load_fitness_criteria()
        if user_criteria:
            criteria["axes"].extend(user_criteria)

        result: Dict[str, Any] = {
            "domain": domain,
            "keywords": keywords,
            "criteria": criteria,
            "sources": self.sources,
        }

        # 7. ワークフロー統計のマージ（存在時のみ）
        workflow_stats = self._load_workflow_stats()
        if workflow_stats:
            result["workflow_stats"] = workflow_stats

        return result

    def _load_sources(self) -> List[str]:
        """CLAUDE.md, rules, skills を読み込む"""
        texts: List[str] = []

        # CLAUDE.md
        claude_md = self.project_root / "CLAUDE.md"
        if claude_md.exists():
            content = claude_md.read_text(encoding="utf-8")
            texts.append(content)
            self.sources.append("CLAUDE.md")

        # .claude/rules/*.md
        rules_dir = self.project_root / ".claude" / "rules"
        if rules_dir.is_dir():
            for rule_file in sorted(rules_dir.glob("*.md")):
                content = rule_file.read_text(encoding="utf-8")
                texts.append(content)
                rel = str(rule_file.relative_to(self.project_root))
                self.sources.append(rel)

        # .claude/skills/**/SKILL.md
        skills_dir = self.project_root / ".claude" / "skills"
        if skills_dir.is_dir():
            for skill_file in sorted(skills_dir.rglob("SKILL.md")):
                content = skill_file.read_text(encoding="utf-8")
                texts.append(content)
                rel = str(skill_file.relative_to(self.project_root))
                self.sources.append(rel)

        return texts

    def _extract_keywords(self) -> List[str]:
        """テキストからキーワード頻度分析を行い、上位キーワードを返す"""
        text_lower = self._all_text.lower()
        counter: Counter = Counter()

        for domain, kw_list in DOMAIN_KEYWORDS.items():
            for kw in kw_list:
                count = text_lower.count(kw.lower())
                if count > 0:
                    counter[kw] = count

        # 上位20キーワードを返す
        return [kw for kw, _ in counter.most_common(20)]

    def _detect_domain(self, keywords: List[str]) -> str:
        """キーワード頻度からドメインを推定"""
        scores: Dict[str, int] = {d: 0 for d in DOMAIN_KEYWORDS}
        text_lower = self._all_text.lower()

        for domain, kw_list in DOMAIN_KEYWORDS.items():
            for kw in kw_list:
                count = text_lower.count(kw.lower())
                scores[domain] += count

        # 最高スコアのドメインを選択（閾値: 3回以上のマッチ）
        best_domain = max(scores, key=lambda d: scores[d])
        if scores[best_domain] < 3:
            return "general"

        return best_domain

    def _build_criteria(self, domain: str) -> Dict[str, Any]:
        """ドメインに基づいて評価基準を構築"""
        base = DOMAIN_CRITERIA.get(domain, DOMAIN_CRITERIA["general"])
        # deep copy to avoid mutation
        return {
            "axes": [dict(a) for a in base["axes"]],
            "anti_patterns": list(base["anti_patterns"]),
        }

    def _load_pitfalls(self) -> List[str]:
        """pitfalls.md からアンチパターンを読み込む"""
        patterns: List[str] = []

        # .claude/skills/*/references/pitfalls.md を検索
        skills_dir = self.project_root / ".claude" / "skills"
        if skills_dir.is_dir():
            for pitfalls_file in skills_dir.rglob("references/pitfalls.md"):
                content = pitfalls_file.read_text(encoding="utf-8")
                extracted = self._parse_pitfalls(content)
                patterns.extend(extracted)
                rel = str(pitfalls_file.relative_to(self.project_root))
                if rel not in self.sources:
                    self.sources.append(rel)

        return patterns

    def _load_workflow_stats(self) -> Optional[Dict[str, Any]]:
        """ワークフロー統計 JSON を読み込む。存在しない場合は None。"""
        if not WORKFLOW_STATS_PATH.exists():
            return None

        try:
            data = json.loads(WORKFLOW_STATS_PATH.read_text(encoding="utf-8"))
            # hints 付きの場合は stats のみ抽出
            if "stats" in data:
                return data["stats"]
            # 直接 stats の場合はそのまま
            if data and isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass

        return None

    def _load_fitness_criteria(self) -> List[Dict[str, Any]]:
        """ユーザー定義の品質基準を .claude/fitness-criteria.md から読み込む。

        存在しない場合は空リスト。存在する場合は axes 形式に変換して返す。
        """
        criteria_path = self.project_root / ".claude" / "fitness-criteria.md"
        if not criteria_path.exists():
            return []

        content = criteria_path.read_text(encoding="utf-8")
        if criteria_path not in [Path(s) for s in self.sources]:
            self.sources.append(str(criteria_path.relative_to(self.project_root)))

        return self._parse_fitness_criteria(content)

    @staticmethod
    def _parse_fitness_criteria(content: str) -> List[Dict[str, Any]]:
        """fitness-criteria.md からユーザー定義の評価軸を抽出する。

        フォーマット例:
            - ゲーマーがワクワクする表現かどうか (weight: 0.4)
            - ブラウザ操作の具体性 (weight: 0.3)
        """
        axes: List[Dict[str, Any]] = []
        for line in content.splitlines():
            stripped = line.strip()
            match = re.match(
                r"^[-*]\s+(.+?)\s*(?:\(weight:\s*([\d.]+)\))?$", stripped
            )
            if match:
                description = match.group(1).strip()
                weight_str = match.group(2)
                weight = float(weight_str) if weight_str else 0.2

                # 見出しっぽいもの（# で始まる）は除外
                if description.startswith("#") or len(description) <= 3:
                    continue

                # name を description から生成
                name = re.sub(r"[^\w]", "_", description)[:30].strip("_").lower()

                axes.append({
                    "name": f"user_{name}",
                    "weight": weight,
                    "description": description,
                })
        return axes

    @staticmethod
    def _parse_pitfalls(content: str) -> List[str]:
        """pitfalls.md からアンチパターン文を抽出

        箇条書き（- や * で始まる行）をアンチパターンとして抽出。
        見出し行は除外。
        """
        patterns: List[str] = []
        for line in content.splitlines():
            stripped = line.strip()
            # 箇条書き行を抽出
            match = re.match(r"^[-*]\s+(.+)$", stripped)
            if match:
                text = match.group(1).strip()
                # 見出しっぽいもの（# で始まる）は除外
                if not text.startswith("#") and len(text) > 3:
                    patterns.append(text)
        return patterns


def main():
    parser = argparse.ArgumentParser(description="プロジェクト分析")
    parser.add_argument(
        "--project-root",
        default=".",
        help="プロジェクトルートディレクトリ（デフォルト: カレントディレクトリ）",
    )

    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    if not project_root.is_dir():
        print(f"エラー: ディレクトリが見つかりません: {project_root}", file=sys.stderr)
        sys.exit(1)

    analyzer = ProjectAnalyzer(str(project_root))
    result = analyzer.analyze()

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
