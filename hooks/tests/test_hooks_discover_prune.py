"""discover.py / prune.py のワークフロートレーシング関連テスト。

PR-A: hooks/tests/test_hooks.py から機能別に分割。
共有 fixture (tmp_data_dir, patch_data_dir) は conftest.py を参照。
discover/prune スクリプトのパスは本ファイル先頭で sys.path に追加する。
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

import common
import rl_common

# discover/prune のインポートパスを追加
_plugin_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_plugin_root / "skills" / "prune" / "scripts"))
sys.path.insert(0, str(_plugin_root / "skills" / "discover" / "scripts"))

import discover as _discover_mod


def _load_skills_discover():
    """skills/discover/scripts/discover.py をロードする。"""
    return _discover_mod


class TestDiscoverContextualization:
    """discover.py の contextualized/ad-hoc 分類テスト。"""

    def test_ad_hoc_only_counted(self, patch_data_dir):
        """ad-hoc レコードのみがスキル候補としてカウントされる。

        Agent:Explore は組み込み Agent のため agent_usage_summary に分類される。
        """
        discover = _load_skills_discover()

        usage_file = patch_data_dir / "usage.jsonl"
        records = []
        # contextualized: 15回（parent_skill あり）
        for i in range(15):
            records.append({
                "skill_name": "Agent:Explore",
                "parent_skill": "opsx:refine",
                "workflow_id": f"wf-ctx{i:04d}",
                "prompt": "explore",
            })
        # ad-hoc: 6回（parent_skill なし、backfill でない）
        for i in range(6):
            records.append({
                "skill_name": "Agent:Explore",
                "prompt": "explore manually",
            })
        usage_file.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n"
        )

        with mock.patch.object(discover, "DATA_DIR", patch_data_dir):
            with mock.patch.object(discover, "SUPPRESSION_FILE", patch_data_dir / "suppress.jsonl"):
                patterns = discover.detect_behavior_patterns(threshold=5)

        # Agent:Explore は組み込み Agent → agent_usage_summary に分離
        summary = [p for p in patterns if p["type"] == "agent_usage_summary"]
        assert len(summary) == 1
        assert summary[0]["count"] == 6  # ad-hoc のみ

    def test_backfill_excluded_as_unknown(self, patch_data_dir):
        """backfill データは unknown として除外される。"""
        discover = _load_skills_discover()

        usage_file = patch_data_dir / "usage.jsonl"
        records = []
        # backfill: 10回
        for i in range(10):
            records.append({
                "skill_name": "Agent:Explore",
                "source": "backfill",
                "prompt": "explore",
            })
        # ad-hoc: 3回（閾値未満）
        for i in range(3):
            records.append({
                "skill_name": "Agent:Explore",
                "prompt": "explore",
            })
        usage_file.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n"
        )

        with mock.patch.object(discover, "DATA_DIR", patch_data_dir):
            with mock.patch.object(discover, "SUPPRESSION_FILE", patch_data_dir / "suppress.jsonl"):
                patterns = discover.detect_behavior_patterns(threshold=5)

        # ad-hoc 3回は閾値5未満なので候補なし
        assert len(patterns) == 0


class TestPruneParentSkill:
    """prune.py の parent_skill 経由カウントテスト。"""

    def test_parent_skill_prevents_zero_invocation(self, patch_data_dir):
        """parent_skill 経由で使用されているスキルは淘汰候補にならない。"""
        import prune
        import audit

        usage_file = patch_data_dir / "usage.jsonl"
        # opsx:refine を直接呼んだ記録はないが、parent_skill として参照されている
        records = [
            {
                "skill_name": "Agent:Explore",
                "parent_skill": "opsx:refine",
                "workflow_id": "wf-prune001",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        ]
        usage_file.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n"
        )

        with mock.patch.object(audit, "DATA_DIR", patch_data_dir):
            usage_records = audit.load_usage_data(days=30)

        used_skills = set()
        for rec in usage_records:
            used_skills.add(rec.get("skill_name", ""))
            parent = rec.get("parent_skill")
            if parent:
                used_skills.add(parent)

        assert "opsx:refine" in used_skills

    def test_no_usage_detected(self, patch_data_dir):
        """直接呼び出しも parent_skill 参照もないスキルは淘汰候補。"""
        import audit

        usage_file = patch_data_dir / "usage.jsonl"
        records = [
            {
                "skill_name": "Agent:Explore",
                "ts": "2026-03-03T10:00:00+00:00",
            },
        ]
        usage_file.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n"
        )

        with mock.patch.object(audit, "DATA_DIR", patch_data_dir):
            usage_records = audit.load_usage_data(days=30)

        used_skills = set()
        for rec in usage_records:
            used_skills.add(rec.get("skill_name", ""))
            parent = rec.get("parent_skill")
            if parent:
                used_skills.add(parent)

        assert "some-unused-skill" not in used_skills


