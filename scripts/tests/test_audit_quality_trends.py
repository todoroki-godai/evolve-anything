#!/usr/bin/env python3
"""audit.py の品質推移セクション生成のユニットテスト。"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# audit.py のパスを通す
_audit_scripts = Path(__file__).resolve().parent.parent.parent / "skills" / "audit" / "scripts"
sys.path.insert(0, str(_audit_scripts))
# quality_monitor.py のパスを通す
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit import (
    build_quality_trends_section,
    generate_report,
    generate_sparkline,
    load_quality_baselines,
)


# ── スパークライン ──────────────────────────────────────


def test_generate_sparkline_basic():
    """基本的なスパークライン生成。"""
    scores = [0.2, 0.4, 0.6, 0.8, 1.0]
    result = generate_sparkline(scores)
    assert len(result) == 5
    # 最低→最高で全ブロック使うはず
    assert result[0] != result[-1]


def test_generate_sparkline_constant():
    """全て同一値のスパークライン。"""
    scores = [0.5, 0.5, 0.5]
    result = generate_sparkline(scores)
    assert len(result) == 3


def test_generate_sparkline_empty():
    """空リストなら空文字。"""
    assert generate_sparkline([]) == ""


def test_generate_sparkline_single():
    """1件でも動作する。"""
    result = generate_sparkline([0.8])
    assert len(result) == 1


# ── build_quality_trends_section ──────────────────────────────


def _make_baselines(skill_name, scores, days_ago_start=10):
    """テスト用ベースラインレコードを生成。"""
    records = []
    for i, score in enumerate(scores):
        ts = (datetime.now(timezone.utc) - timedelta(days=days_ago_start - i)).isoformat()
        records.append({
            "skill_name": skill_name,
            "score": score,
            "timestamp": ts,
            "usage_count_at_measure": (i + 1) * 10,
            "skill_path": f"/fake/{skill_name}/SKILL.md",
        })
    return records


def test_build_quality_trends_empty():
    """ベースラインが空なら空リスト。"""
    assert build_quality_trends_section([], {}) == []


def test_build_quality_trends_degraded():
    """劣化スキルに DEGRADED マーカーが表示される。"""
    baselines = _make_baselines("commit", [0.85, 0.80, 0.74, 0.72])
    usage = {"commit": 200}
    lines = build_quality_trends_section(baselines, usage)
    text = "\n".join(lines)
    assert "## Skill Quality Trends" in text
    assert "DEGRADED" in text
    assert "/evolve-anything:evolve-skill commit" in text


def test_build_quality_trends_normal():
    """劣化なしスキルには DEGRADED マーカーが無い。"""
    baselines = _make_baselines("stable", [0.85, 0.84, 0.83, 0.84])
    usage = {"stable": 200}
    lines = build_quality_trends_section(baselines, usage)
    text = "\n".join(lines)
    assert "DEGRADED" not in text


def test_build_quality_trends_rescore_needed():
    """再スコアリング必要なスキルに RESCORE NEEDED マーカー。"""
    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    baselines = [{
        "skill_name": "old-skill",
        "score": 0.85,
        "timestamp": old_ts,
        "usage_count_at_measure": 50,
        "skill_path": "/fake/old-skill/SKILL.md",
    }]
    usage = {"old-skill": 55}  # 使用差分5, 日数10 >= 7
    lines = build_quality_trends_section(baselines, usage)
    text = "\n".join(lines)
    assert "RESCORE NEEDED" in text


def test_build_quality_trends_single_record():
    """1件のみのスキルにはスパークラインなし、スコアのみ表示。"""
    baselines = _make_baselines("new-skill", [0.80])
    usage = {"new-skill": 200}
    lines = build_quality_trends_section(baselines, usage)
    text = "\n".join(lines)
    assert "new-skill" in text
    assert "0.80" in text


# ── generate_report 統合 ──────────────────────────────────


def test_generate_report_with_quality_baselines():
    """quality_baselines を渡すと Skill Quality Trends セクションが含まれる。"""
    baselines = _make_baselines("commit", [0.85, 0.80, 0.74, 0.72])
    report = generate_report(
        artifacts={"skills": [], "rules": [], "memory": [], "claude_md": []},
        violations=[],
        usage={"commit": 200},
        duplicates=[],
        advisories=[],
        quality_baselines=baselines,
    )
    assert "## Skill Quality Trends" in report


def test_generate_report_without_quality_baselines():
    """quality_baselines が None なら Skill Quality Trends セクションなし。"""
    report = generate_report(
        artifacts={"skills": [], "rules": [], "memory": [], "claude_md": []},
        violations=[],
        usage={},
        duplicates=[],
        advisories=[],
        quality_baselines=None,
    )
    assert "## Skill Quality Trends" not in report


def test_generate_report_empty_quality_baselines():
    """空のベースラインリストなら Skill Quality Trends セクションなし。"""
    report = generate_report(
        artifacts={"skills": [], "rules": [], "memory": [], "claude_md": []},
        violations=[],
        usage={},
        duplicates=[],
        advisories=[],
        quality_baselines=[],
    )
    assert "## Skill Quality Trends" not in report


# ── load_quality_baselines ──────────────────────────────


def test_load_quality_baselines_missing_file(tmp_path):
    """ファイルが無ければ空リスト。"""
    # load_quality_baselines は audit.quality に分離済み (Phase 2 第三弾)
    with patch("audit.quality.DATA_DIR", tmp_path):
        assert load_quality_baselines() == []


def test_load_quality_baselines_valid(tmp_path):
    """正常なJSONLファイルの読み込み。"""
    baselines_file = tmp_path / "quality-baselines.jsonl"
    records = [
        {"skill_name": "commit", "score": 0.85},
        {"skill_name": "commit", "score": 0.80},
    ]
    baselines_file.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )
    with patch("audit.quality.DATA_DIR", tmp_path):
        result = load_quality_baselines()
        assert len(result) == 2


# ── Memory Health ──────────────────────────────────────


def test_memory_health_stale_reference(tmp_path):
    """存在しないパス参照を Stale References として検出する。"""
    from audit import build_memory_health_section

    mem_file = tmp_path / "MEMORY.md"
    mem_file.write_text("# Memory\n\n- skills/update/ は非推奨\n- scripts/lib/ を参照\n")

    # skills/update/ は存在しない、scripts/lib/ も存在しない
    artifacts = {"memory": [mem_file]}
    with patch("audit.read_auto_memory", return_value=[]):
        lines = build_memory_health_section(artifacts, tmp_path)

    text = "\n".join(lines)
    assert "## Memory Health" in text
    assert "Stale References" in text
    assert "skills/update" in text


def test_memory_health_heavy_update_rendered(tmp_path):
    """#104: update_count>=10 かつ 行数>=80 の memory を Heavy Update 節に表示する。

    従来 build_memory_health_section は heavy_update を一切 render せず（collect_issues のみ
    生成し audit テキストに非表示だった）。#104 で advisory 節を追加し可視化する。
    """
    from audit import build_memory_health_section

    mem_file = tmp_path / "churned.md"  # MEMORY.md 以外 → limit=120, near_limit 閾値=96
    body = "\n".join([f"- item {i}" for i in range(84)])  # 総行数 ≈ 89（80<89<96）
    mem_file.write_text(
        f"---\nname: churned\nupdate_count: 15\n---\n# Body\n{body}\n",
        encoding="utf-8",
    )

    artifacts = {"memory": [mem_file]}
    with patch("audit.read_auto_memory", return_value=[]):
        lines = build_memory_health_section(artifacts, tmp_path)

    text = "\n".join(lines)
    assert "Heavy Update" in text
    assert "churned.md" in text
    assert "update_count 15" in text
    assert "dismiss" in text  # 意図的な追記ログの dismiss 導線
    # 行数 89 < near_limit 96 なので Near Limit には出ない（節が独立していることの確認）
    assert "Near Limit" not in text


def test_memory_health_no_heavy_update_when_concise(tmp_path):
    """#104 FP 回帰封じ: 行数<80 なら update_count が高くても Heavy Update 節は出ない。"""
    from audit import build_memory_health_section

    mem_file = tmp_path / "concise.md"
    body = "\n".join([f"- item {i}" for i in range(40)])  # 総行数 ≈ 46 < 80
    mem_file.write_text(
        f"---\nname: concise\nupdate_count: 55\n---\n# Body\n{body}\n",
        encoding="utf-8",
    )

    artifacts = {"memory": [mem_file]}
    with patch("audit.read_auto_memory", return_value=[]):
        lines = build_memory_health_section(artifacts, tmp_path)

    text = "\n".join(lines)
    assert "Heavy Update" not in text


def test_memory_health_near_limit(tmp_path):
    """NEAR_LIMIT_RATIO 超過で Near Limit 警告が表示される。"""
    from audit import build_memory_health_section, NEAR_LIMIT_RATIO

    # 180 行の MEMORY.md（上限200行の90%、NEAR_LIMIT_RATIO=0.8 超過）
    mem_file = tmp_path / "MEMORY.md"
    content = "\n".join([f"line {i}" for i in range(180)])
    mem_file.write_text(content)

    artifacts = {"memory": [mem_file]}
    with patch("audit.read_auto_memory", return_value=[]):
        lines = build_memory_health_section(artifacts, tmp_path)

    text = "\n".join(lines)
    assert "Near Limit" in text
    assert "180/200" in text


def test_memory_health_no_issues(tmp_path):
    """問題なしの場合は空リストを返す。"""
    from audit import build_memory_health_section

    mem_file = tmp_path / "MEMORY.md"
    mem_file.write_text("# Memory\n\nシンプルな内容\n")

    artifacts = {"memory": [mem_file]}
    with patch("audit.read_auto_memory", return_value=[]):
        lines = build_memory_health_section(artifacts, tmp_path)

    assert lines == []


def test_memory_health_codeblock_excluded(tmp_path):
    """コードブロック内のパスは Stale References に含まれない。"""
    from audit import build_memory_health_section

    mem_file = tmp_path / "MEMORY.md"
    mem_file.write_text("# Memory\n\n```\n/fake/example/path\n```\n")

    artifacts = {"memory": [mem_file]}
    with patch("audit.read_auto_memory", return_value=[]):
        lines = build_memory_health_section(artifacts, tmp_path)

    # コードブロック内のパスは検出されないので問題なし→空
    assert lines == []


def test_memory_health_slash_enumeration_not_stale(tmp_path):
    """#252: `A.md/B.md/C.md` のようなスラッシュ区切り列挙を1つのネストパスと誤読しない。

    地の文の並列列挙表現は実在ファイルの列挙であり、
    `project_dir/A.md/B.md/C.md` という単一ネストパスとして不在誤判定してはならない。
    """
    from audit import build_memory_health_section

    (tmp_path / "A.md").write_text("a", encoding="utf-8")
    (tmp_path / "B.md").write_text("b", encoding="utf-8")
    (tmp_path / "C.md").write_text("c", encoding="utf-8")

    mem_file = tmp_path / "MEMORY.md"
    mem_file.write_text("# Memory\n\n- 既存の A.md/B.md/C.md を修正済み\n")

    artifacts = {"memory": [mem_file]}
    with patch("audit.read_auto_memory", return_value=[]):
        lines = build_memory_health_section(artifacts, tmp_path)

    text = "\n".join(lines)
    assert "Stale References" not in text


def test_memory_health_slash_enumeration_partial_missing(tmp_path):
    """#252: 列挙中の一部が本当に不在なら、その項目だけ Stale として検出する。"""
    from audit import build_memory_health_section

    (tmp_path / "A.md").write_text("a", encoding="utf-8")
    (tmp_path / "C.md").write_text("c", encoding="utf-8")
    # B.md は作らない（本当に不在）

    mem_file = tmp_path / "MEMORY.md"
    mem_file.write_text("# Memory\n\n- 既存の A.md/B.md/C.md を修正済み\n")

    artifacts = {"memory": [mem_file]}
    with patch("audit.read_auto_memory", return_value=[]):
        lines = build_memory_health_section(artifacts, tmp_path)

    text = "\n".join(lines)
    assert "Stale References" in text
    assert '"B.md" not found on disk' in text
    assert '"A.md" not found on disk' not in text
    assert '"C.md" not found on disk' not in text


def test_memory_health_slash_enumeration_all_missing_suppressed(tmp_path):
    """#252 較正: 列挙内の全セグメントが不在なら非ファイル列挙とみなしノイズ除外する。

    実コーパス較正で "episodic.db/sessions.db/token_usage.db" のような、実ファイルへの
    参照ではなく DB ストア名を説明的に並べた地の文が見つかった。これらは project_dir にも
    参照元ファイルの隣接にも実在しないため、列挙内の全セグメントが不在＝ファイル列挙で
    ある証拠が無いと判断し、旧来の非ファイル列挙ノイズ除外（top_dir フィルタ）と同様に
    stale として検出しない（少なくとも1セグメントが実在する場合のみ #252 の列挙展開を
    適用する）。
    """
    from audit import build_memory_health_section

    mem_file = tmp_path / "MEMORY.md"
    mem_file.write_text(
        "# Memory\n\n"
        "- `evolve --dry-run` が episodic.db/sessions.db/token_usage.db の byte を書き換えていた\n"
    )

    artifacts = {"memory": [mem_file]}
    with patch("audit.read_auto_memory", return_value=[]):
        lines = build_memory_health_section(artifacts, tmp_path)

    text = "\n".join(lines)
    assert "Stale References" not in text


def test_memory_health_slash_enumeration_real_directory_prefix_not_enumeration(tmp_path):
    """#252 round2: 先頭セグメントが実ディレクトリならネストパスとして扱い列挙化しない。

    "config.d/rules.json" は "config.d" が拡張子付きに見えるが実際は実在ディレクトリ。
    列挙として分解し project_dir 直下の同名ファイル "rules.json" を誤って個別確認して
    はならず（別ファイルの偶然の存在で握りつぶされる）、ネストパス "config.d/rules.json"
    自体が不在として検出されるべき。
    """
    from audit import build_memory_health_section

    (tmp_path / "config.d").mkdir()
    # config.d/rules.json は作らない（真に不在）。project_dir 直下には同名の別ファイルが
    # 存在する（列挙誤判定だと "rules.json" セグメントがこちらにマッチし握りつぶされる罠）
    (tmp_path / "rules.json").write_text("{}", encoding="utf-8")

    mem_file = tmp_path / "MEMORY.md"
    mem_file.write_text("# Memory\n\n- config.d/rules.json を確認\n")

    artifacts = {"memory": [mem_file]}
    with patch("audit.read_auto_memory", return_value=[]):
        lines = build_memory_health_section(artifacts, tmp_path)

    text = "\n".join(lines)
    assert "Stale References" in text
    assert '"config.d/rules.json" not found on disk' in text


def test_memory_health_slash_enumeration_minority_exists_suppressed(tmp_path):
    """#252 round2: 3件中1件だけ実在する列挙は過半数未満のため非ファイル列挙とみなし抑制する。

    偶然の同名ファイル1件だけで残り2件を stale FP 化しないための境界テスト。
    """
    from audit import build_memory_health_section

    (tmp_path / "A.md").write_text("a", encoding="utf-8")
    # B.md, C.md は作らない

    mem_file = tmp_path / "MEMORY.md"
    mem_file.write_text("# Memory\n\n- 既存の A.md/B.md/C.md を修正済み\n")

    artifacts = {"memory": [mem_file]}
    with patch("audit.read_auto_memory", return_value=[]):
        lines = build_memory_health_section(artifacts, tmp_path)

    text = "\n".join(lines)
    assert "Stale References" not in text


def test_memory_health_slash_enumeration_majority_exists_detects_missing(tmp_path):
    """#252 round2: 3件中2件実在する列挙は過半数を満たすため欠損1件のみ検出する。"""
    from audit import build_memory_health_section

    (tmp_path / "A.md").write_text("a", encoding="utf-8")
    (tmp_path / "B.md").write_text("b", encoding="utf-8")
    # C.md は作らない

    mem_file = tmp_path / "MEMORY.md"
    mem_file.write_text("# Memory\n\n- 既存の A.md/B.md/C.md を修正済み\n")

    artifacts = {"memory": [mem_file]}
    with patch("audit.read_auto_memory", return_value=[]):
        lines = build_memory_health_section(artifacts, tmp_path)

    text = "\n".join(lines)
    assert "Stale References" in text
    assert '"C.md" not found on disk' in text
    assert '"A.md" not found on disk' not in text
    assert '"B.md" not found on disk' not in text


def test_memory_health_split_suggestion(tmp_path):
    """Near Limit 時に Suggestions にトピックファイル分割が含まれる。"""
    from audit import build_memory_health_section

    mem_file = tmp_path / "MEMORY.md"
    content = "\n".join([f"line {i}" for i in range(170)])
    mem_file.write_text(content)

    artifacts = {"memory": [mem_file]}
    with patch("audit.read_auto_memory", return_value=[]):
        lines = build_memory_health_section(artifacts, tmp_path)

    text = "\n".join(lines)
    assert "Split large MEMORY.md entries into topic files" in text


def test_generate_report_with_memory_health(tmp_path):
    """Memory Health セクションがレポートに含まれる。"""
    mem_file = tmp_path / "MEMORY.md"
    content = "\n".join([f"line {i}" for i in range(180)])
    mem_file.write_text(content)

    with patch("audit.read_auto_memory", return_value=[]):
        report = generate_report(
            artifacts={"skills": [], "rules": [], "memory": [mem_file], "claude_md": []},
            violations=[],
            usage={},
            duplicates=[],
            advisories=[],
            project_dir=tmp_path,
        )

    assert "## Memory Health" in report
    assert "Near Limit" in report


def test_memory_health_auto_memory(tmp_path):
    """auto-memory ファイルも検査対象になる。"""
    from audit import build_memory_health_section

    # auto-memory エントリ（180行）
    auto_content = "\n".join([f"auto line {i}" for i in range(180)])
    auto_entry = {
        "path": str(tmp_path / "auto-MEMORY.md"),
        "topic": "MEMORY",
        "content": auto_content,
    }

    artifacts = {"memory": []}
    # build_memory_health_section は audit.memory に分離済み (Phase 2)
    with patch("audit.memory.read_auto_memory", return_value=[auto_entry]):
        lines = build_memory_health_section(artifacts, tmp_path)

    text = "\n".join(lines)
    assert "Near Limit" in text


# ── --skip-rescore テスト ──────────────────────────────


def test_run_audit_skip_rescore():
    """--skip-rescore で品質計測がスキップされる。"""
    from audit import run_audit

    with patch("audit.find_artifacts", return_value={"skills": [], "rules": [], "memory": [], "claude_md": []}), \
         patch("audit.check_line_limits", return_value=[]), \
         patch("audit.load_usage_data", return_value=[]), \
         patch("audit.aggregate_usage", return_value={}), \
         patch("audit.detect_duplicates_simple", return_value=[]), \
         patch("audit.load_usage_registry", return_value={}), \
         patch("audit.scope_advisory", return_value=[]), \
         patch("audit.load_quality_baselines", return_value=[]):
        # skip_rescore=True なら quality_monitor の import/呼出しが起きない
        report = run_audit(skip_rescore=True)
        assert "Environment Audit Report" in report
