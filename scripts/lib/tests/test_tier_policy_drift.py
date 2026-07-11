#!/usr/bin/env python3
"""tier_policy_drift.py のテスト — stale-mention advisory（#193）。

正典が使わなくなったモデルエイリアス（例: opus 撤去後の "opus" 残存言及）を
advisory_scan 配下の散文から決定論検出する。書換は一切しない（advisory のみ）。
"""
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import tier_policy  # noqa: E402
import tier_policy_drift as drift  # noqa: E402

_TIERS = {t: dict(p) for t, p in tier_policy.DEFAULT_TIER_POLICY.items()}


def _config(tmp_path, *, scan_dirs=None, agents=None, tiers=None):
    return {
        "tiers": tiers or _TIERS,
        "targets": {"agents": agents or [], "settings": [], "routing_rules": []},
        "advisory_scan": scan_dirs or [],
    }


class TestScanStaleMentions:
    def test_detects_opus_mention(self, tmp_path):
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        f = rules_dir / "model-routing.md"
        f.write_text("HEAD tier uses opus for hard tasks.\n", encoding="utf-8")
        config = _config(tmp_path, scan_dirs=[str(rules_dir)])

        findings = drift.scan_stale_mentions(config)
        assert len(findings) == 1
        assert findings[0]["alias"] == "opus"
        assert findings[0]["path"] == str(f)
        assert findings[0]["line_no"] == 1

    def test_sonnet_fable_haiku_not_flagged(self, tmp_path):
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        f = rules_dir / "note.md"
        f.write_text("sonnet, fable, haiku are all current.\n", encoding="utf-8")
        config = _config(tmp_path, scan_dirs=[str(rules_dir)])
        assert drift.scan_stale_mentions(config) == []

    def test_case_insensitive_and_word_boundary(self, tmp_path):
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        f = rules_dir / "note.md"
        f.write_text("Opus was retired. opusculent is unrelated.\n", encoding="utf-8")
        config = _config(tmp_path, scan_dirs=[str(rules_dir)])
        findings = drift.scan_stale_mentions(config)
        # "Opus" は語境界一致で検出、"opusculent" は語境界不一致で非検出（1件のみ）
        assert len(findings) == 1
        assert findings[0]["alias"] == "opus"

    def test_scans_agents_targets_too(self, tmp_path):
        agent = tmp_path / "agent.md"
        agent.write_text("---\nname: a\n---\nUses opus historically.\n", encoding="utf-8")
        config = _config(tmp_path, agents=[str(agent)])
        findings = drift.scan_stale_mentions(config)
        assert len(findings) == 1
        assert findings[0]["path"] == str(agent)

    def test_no_stale_alias_when_all_used(self, tmp_path):
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        f = rules_dir / "note.md"
        f.write_text("opus everywhere\n", encoding="utf-8")
        tiers_using_all = {
            "HEAD": {"model": "opus", "effort": "xhigh", "description": "d"},
            "HARD": {"model": "sonnet", "effort": "xhigh", "description": "d"},
            "NORMAL": {"model": "sonnet", "effort": "medium", "description": "d"},
            "MECH": {"model": "haiku", "effort": None, "description": "d"},
            "REVIEW": {"model": "fable", "effort": "high", "description": "d"},
        }
        config = _config(tmp_path, scan_dirs=[str(rules_dir)], tiers=tiers_using_all)
        assert drift.scan_stale_mentions(config) == []

    def test_no_scan_dirs_returns_empty(self, tmp_path):
        config = _config(tmp_path)
        assert drift.scan_stale_mentions(config) == []

    def test_does_not_write_anything(self, tmp_path):
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        f = rules_dir / "note.md"
        original = "opus lives here\n"
        f.write_text(original, encoding="utf-8")
        config = _config(tmp_path, scan_dirs=[str(rules_dir)])
        drift.scan_stale_mentions(config)
        assert f.read_text(encoding="utf-8") == original
