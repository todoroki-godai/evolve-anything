"""エージェント品質診断の定数カタログ（アンチパターン・ベストプラクティス定義）。"""
from __future__ import annotations

import re

UPSTREAM_REPO = "msitarzewski/agency-agents"

# 行数閾値
BLOAT_LINE_THRESHOLD = 400
KITCHEN_SINK_HEADING_THRESHOLD = 12

# 曖昧表現キーワード（日英）
VAGUE_KEYWORDS = [
    "anything",
    "everything",
    "whatever",
    "flexible",
    "versatile",
    "any task",
    "なんでも",
    "柔軟に",
    "何でも",
    "すべて対応",
    "あらゆる",
]
VAGUE_KEYWORD_THRESHOLD = 3

# description 品質閾値
MIN_DESCRIPTION_LENGTH = 30
OUTPUT_SPEC_PATTERNS = [
    r"(?i)provide\s+.*(feedback|output|report|summary|results)",
    r"(?i)return\s+.*(result|output|summary|report|list)",
    r"(?i)format\s+.*(as|using|with|into)",
    r"(?i)include\s+.*(specific|concrete|actionable)",
    r"(?i)organized?\s+by",
    r"(?i)(出力|返す|提示|レポート|報告).*形式",
    r"(?i)##\s*(deliverables?|output|成果物|出力)",
    r"```",
    r"(?i)respond\s+with",
    r"(?i)generate\s+.*(report|summary|plan|list)",
    r"(?i)(結果|アクション|プラン)を(提示|出力|表示)",
]
OUTPUT_SPEC_MIN_MATCHES = 2

# 知識ハードコード検出パターン
KNOWLEDGE_HARDCODING_PATTERNS = [
    r"\b(?:v\d+\.\d+|\(\d+[-–]\d+\))",
    r"(?:~\/|https?:\/\/[^\s)]{10,}|\/[a-z][-a-z0-9/]{4,}(?:\.py|\.ts|\.md|\.json))",
    r"^\s*[-*]\s*\*\*[A-Za-z][-A-Za-z0-9_]+\*\*\s*:",
]
KNOWLEDGE_HARDCODING_LOW_THRESHOLD = 3
KNOWLEDGE_HARDCODING_MEDIUM_THRESHOLD = 10

# JIT識別子戦略の検出パターン
JIT_PATTERNS = [
    r"(?i)(read|grep|bash|確認|参照).*(before|前に|必ず|always)",
    r"(?i)(ファイルを|file).*(確認|read|check)",
    r"(?i)dynamic\s*knowledge",
    r"(?i)jit|just.in.time",
    r"(?i)記憶に頼らず",
    r"(?i)実行時に.*確認",
]

ANTI_PATTERNS = {
    "missing_frontmatter": {
        "description": "YAML frontmatter (name, description) が欠落",
        "severity": "high",
    },
    "vague_mission": {
        "description": "曖昧な表現が多く、専門性が不明確",
        "severity": "medium",
    },
    "weak_output_spec": {
        "description": "出力形式・成果物の指示が本文中にない",
        "severity": "medium",
    },
    "weak_trigger_description": {
        "description": "description が短すぎるか曖昧で、委譲判断に不十分",
        "severity": "medium",
    },
    "missing_tools_restriction": {
        "description": "tools フィールド未設定（全ツール継承 → スコープ過大）",
        "severity": "low",
    },
    "no_boundaries": {
        "description": "責任範囲が不明確（何をしないかが書かれていない）",
        "severity": "low",
    },
    "kitchen_sink": {
        "description": "1つのエージェントに過剰な責任（セクション数が多すぎる）",
        "severity": "medium",
    },
    "no_checklist": {
        "description": "手順やチェックリストが定義されていない",
        "severity": "low",
    },
    "bloated_agent": {
        "description": f"定義が {BLOAT_LINE_THRESHOLD} 行を超えて肥大化",
        "severity": "medium",
    },
    "knowledge_hardcoding": {
        "description": "バージョン番号・具体パス・プロジェクト固有名詞をハードコード（陳腐化リスク）",
        "severity": "low",
    },
}

BEST_PRACTICES = {
    "structured_identity": {
        "description": "Identity / Role / Personality セクションで自己定義",
        "detect_patterns": [
            r"(?i)##\s*(your\s+)?identity",
            r"(?i)##\s*(your\s+)?role",
            r"(?i)##\s*personality",
            r"(?i)##\s*あなたの(役割|アイデンティティ)",
        ],
    },
    "success_metrics": {
        "description": "測定可能な成功基準の定義",
        "detect_patterns": [
            r"(?i)##\s*success\s*(metrics|criteria)",
            r"(?i)##\s*成功(基準|指標)",
            r"(?i)##\s*KPI",
        ],
    },
    "communication_style": {
        "description": "コミュニケーションスタイルの明示",
        "detect_patterns": [
            r"(?i)##\s*communication\s*style",
            r"(?i)##\s*コミュニケーション",
            r"(?i)##\s*(tone|voice)",
        ],
    },
    "critical_rules": {
        "description": "絶対守るべきルールの明示",
        "detect_patterns": [
            r"(?i)##\s*critical\s*rules",
            r"(?i)##\s*重要な?ルール",
            r"(?i)##\s*rules",
            r"(?i)##\s*constraints",
        ],
    },
    "deliverable_templates": {
        "description": "具体的な成果物テンプレート / 出力形式の定義",
        "detect_patterns": [
            r"(?i)##\s*deliverables?",
            r"(?i)##\s*output",
            r"(?i)##\s*成果物",
            r"(?i)##\s*出力",
            r"```",
        ],
    },
    "priority_markers": {
        "description": "優先度マーカー（🔴🟡💭 等）による分類",
        "detect_patterns": [
            r"🔴|🟡|💭|🟢",
            r"(?i)\*\*(blocker|critical|suggestion|nit)\*\*",
            r"(?i)(P0|P1|P2|P3)",
        ],
    },
    "jit_file_references": {
        "description": "JIT識別子戦略：回答前にファイルを動的確認する鉄則の明示",
        "detect_patterns": JIT_PATTERNS,
    },
}
