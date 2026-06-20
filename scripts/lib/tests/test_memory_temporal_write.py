"""memory_temporal.write_temporal_metadata の write 側テスト（#2 provenance 配線）。

valid_from / source_correction_ids を frontmatter に書く writer。reader 側
（parse_memory_temporal / is_stale / is_superseded）は実装済みで、本テストは
write 側休眠配線の活性化を担保する。LLM-free・tmp_path のみ（HOME 隔離不要）。
"""
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

import memory_temporal as mt


def _write_entry(tmp_path, body="entry body", name="auto-test"):
    p = tmp_path / "auto_test.md"
    p.write_text(
        f"---\nname: {name}\ndescription: t\nmetadata:\n  type: feedback\n"
        "importance: medium\n---\n\n" + body,
        encoding="utf-8",
    )
    return p


def test_writes_valid_from_and_source_ids(tmp_path):
    p = _write_entry(tmp_path)
    changed = mt.write_temporal_metadata(
        p,
        valid_from="2026-06-20T00:00:00+00:00",
        source_correction_ids=["s1#t1", "s2#t2"],
    )
    assert changed is True
    parsed = mt.parse_memory_temporal(p)
    assert parsed["valid_from"] == "2026-06-20T00:00:00+00:00"
    assert parsed["source_correction_ids"] == ["s1#t1", "s2#t2"]


def test_does_not_overwrite_existing_valid_from(tmp_path):
    p = tmp_path / "auto.md"
    p.write_text(
        "---\nname: x\ndescription: t\nvalid_from: '2020-01-01T00:00:00+00:00'\n"
        "metadata:\n  type: feedback\nimportance: medium\n---\n\nbody",
        encoding="utf-8",
    )
    mt.write_temporal_metadata(p, valid_from="2026-06-20T00:00:00+00:00")
    parsed = mt.parse_memory_temporal(p)
    assert parsed["valid_from"] == "2020-01-01T00:00:00+00:00"  # 既存を保持


def test_unions_source_correction_ids(tmp_path):
    p = tmp_path / "auto.md"
    p.write_text(
        "---\nname: x\ndescription: t\nsource_correction_ids:\n  - s1#t1\n"
        "metadata:\n  type: feedback\nimportance: medium\n---\n\nbody",
        encoding="utf-8",
    )
    mt.write_temporal_metadata(p, source_correction_ids=["s1#t1", "s2#t2"])
    parsed = mt.parse_memory_temporal(p)
    assert parsed["source_correction_ids"] == ["s1#t1", "s2#t2"]  # 重複排除 union・順序保持


def test_noop_without_frontmatter(tmp_path):
    p = tmp_path / "plain.md"
    p.write_text("no frontmatter here", encoding="utf-8")
    assert mt.write_temporal_metadata(p, valid_from="2026-06-20T00:00:00+00:00") is False
    assert p.read_text(encoding="utf-8") == "no frontmatter here"  # 不変


def test_noop_when_nothing_to_change(tmp_path):
    p = _write_entry(tmp_path)
    assert mt.write_temporal_metadata(p) is False  # 引数なし → no-op


def test_does_not_set_decay_or_superseded(tmp_path):
    """valid_from だけ書いても decay_days / superseded_at は None のまま → stale/superseded 非発火。"""
    p = _write_entry(tmp_path)
    mt.write_temporal_metadata(p, valid_from="2020-01-01T00:00:00+00:00")
    parsed = mt.parse_memory_temporal(p)
    assert parsed["decay_days"] is None
    assert parsed["superseded_at"] is None
    assert mt.is_stale(parsed) is False
    assert mt.is_superseded(parsed) is False


def test_preserves_body_and_other_frontmatter(tmp_path):
    p = _write_entry(tmp_path, body="important body text", name="keepme")
    mt.write_temporal_metadata(p, valid_from="2026-06-20T00:00:00+00:00")
    text = p.read_text(encoding="utf-8")
    assert "important body text" in text
    assert "name: keepme" in text
    assert "type: feedback" in text


def test_empty_source_ids_is_noop(tmp_path):
    p = _write_entry(tmp_path)
    assert mt.write_temporal_metadata(p, source_correction_ids=[]) is False
    parsed = mt.parse_memory_temporal(p)
    assert parsed["source_correction_ids"] == []


def test_malformed_yaml_frontmatter_is_noop(tmp_path):
    """壊れた YAML frontmatter は no-op（False）で原本を破壊しない。"""
    p = tmp_path / "broken.md"
    # `: : :` は yaml.safe_load が YAMLError を投げる不正構文
    original = "---\nname: x\nbad: : : :\n---\n\nbody"
    p.write_text(original, encoding="utf-8")
    assert mt.write_temporal_metadata(p, valid_from="2026-06-20T00:00:00+00:00") is False
    assert p.read_text(encoding="utf-8") == original  # 不変
