"""addyosmani skill anatomy カタログ収録（Issue #63）のテスト。

決定論・LLM 非依存・純粋関数。実 ~/.claude / 実ファイルに触れない。
sys.path への scripts/lib 追加は conftest.py が行う。
"""
from pathlib import Path

from agent_quality import AgentInfo, check_quality
from agent_quality_catalog import SKILL_ANATOMY, missing_anatomy_sections


def _mk(content: str) -> AgentInfo:
    """anatomy テスト用 AgentInfo を構築（content セット済みで path は読まれない）。"""
    return AgentInfo(
        name="x",
        path=Path("/tmp/nonexistent-anatomy-x.md"),
        scope="global",
        frontmatter={"name": "x", "description": "x" * 40, "tools": "Read"},
        content=content,
    )


# 完全な anatomy 5 節を含む content（測定可能成功基準・出力形式も含み、既存
# BEST_PRACTICES を満たすことでこのテストでは skill_anatomy のみに焦点を当てる）。
_FULL_ANATOMY = """\
## Triggering conditions
発動するのはこのケース。

## Step-by-step workflow
1. まず読む
2. 次に書く
3. 最後に確認する

## Anti-rationalization table
| 言い訳 | 反論 |

## Red flags
危険信号の一覧。

## Verification requirements
verify する方法。
"""


def test_skill_anatomy_structure_invariant():
    assert SKILL_ANATOMY["source"] == "addyosmani/agent-skills"
    sections = SKILL_ANATOMY["sections"]
    assert isinstance(sections, list)
    assert len(sections) > 0
    for sec in sections:
        assert "key" in sec and isinstance(sec["key"], str) and sec["key"]
        assert "label" in sec and isinstance(sec["label"], str) and sec["label"]
        assert "detect_patterns" in sec
        assert isinstance(sec["detect_patterns"], list)
        assert len(sec["detect_patterns"]) > 0


def test_missing_anatomy_sections_all_missing_on_plain_text():
    missing = missing_anatomy_sections("")
    expected_keys = [s["key"] for s in SKILL_ANATOMY["sections"]]
    assert [m["key"] for m in missing] == expected_keys
    assert len(missing) == 5
    # 各 missing は key/label のみ（順序維持）
    for m in missing:
        assert set(m.keys()) == {"key", "label"}


def test_missing_anatomy_sections_empty_when_all_present():
    assert missing_anatomy_sections(_FULL_ANATOMY) == []


def test_check_quality_emits_single_skill_anatomy_suggestion_when_missing():
    agent = _mk("## Overview\nsome agent without anatomy sections at all.")
    result = check_quality(agent)
    anatomy = [s for s in result["suggestions"] if s["pattern"] == "skill_anatomy"]
    assert len(anatomy) == 1
    assert anatomy[0]["source"] == "addyosmani/agent-skills"
    assert "欠落:" in anatomy[0]["description"]


def test_check_quality_no_skill_anatomy_suggestion_when_present():
    agent = _mk(_FULL_ANATOMY)
    result = check_quality(agent)
    assert all(s["pattern"] != "skill_anatomy" for s in result["suggestions"])


def test_existing_best_practices_suggestions_unaffected_by_anatomy():
    # anatomy 充足だが success_metrics など既存 BEST_PRACTICES 節は欠く content。
    # 既存 BEST_PRACTICES の suggestion は従来通り出続ける（skill_anatomy 追加で壊れない）。
    agent = _mk(_FULL_ANATOMY)
    result = check_quality(agent)
    patterns = {s["pattern"] for s in result["suggestions"]}
    # success_metrics は _FULL_ANATOMY に存在しないので suggestion として出るはず
    assert "success_metrics" in patterns
    # かつ anatomy は出ない
    assert "skill_anatomy" not in patterns
