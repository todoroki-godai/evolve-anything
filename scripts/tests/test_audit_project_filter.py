"""audit.py の project フィルタリングテスト。"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

_plugin_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_plugin_root / "skills" / "audit" / "scripts"))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

import audit


@pytest.fixture(autouse=True)
def reset_cache():
    audit.classification._plugin_skill_map_cache = None
    yield
    audit.classification._plugin_skill_map_cache = None


@pytest.fixture
def usage_data_dir(tmp_path):
    """プロジェクトフィールド付き usage.jsonl を含むデータディレクトリ。"""
    data_dir = tmp_path / "evolve-anything"
    data_dir.mkdir()
    now = datetime.now(timezone.utc).isoformat()
    records = [
        {"skill_name": "my-skill", "project": "atlas", "timestamp": now},
        {"skill_name": "my-skill", "project": "atlas", "timestamp": now},
        {"skill_name": "other-skill", "project": "beta", "timestamp": now},
        {"skill_name": "my-skill", "project": None, "timestamp": now},
    ]
    (data_dir / "usage.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n"
    )
    return data_dir


class TestLoadUsageDataProject:
    """load_usage_data の project フィルタテスト。"""

    def test_project_scoped(self, usage_data_dir):
        with mock.patch.object(audit, "DATA_DIR", usage_data_dir):
            records = audit.load_usage_data(project_root=Path("/Users/foo/atlas"))
        assert len(records) == 2
        assert all(r["project"] == "atlas" for r in records)

    def test_global_includes_all(self, usage_data_dir):
        """project_root 未指定時は全レコードを対象。"""
        with mock.patch.object(audit, "DATA_DIR", usage_data_dir):
            records = audit.load_usage_data()
        assert len(records) == 4

    def test_project_excludes_null(self, usage_data_dir):
        """project フィルタ指定時、null レコードは除外される。"""
        with mock.patch.object(audit, "DATA_DIR", usage_data_dir):
            records = audit.load_usage_data(project_root=Path("/Users/foo/atlas"))
        projects = [r.get("project") for r in records]
        assert None not in projects


class TestLoadUsageDataCrossDirUnion:
    """#45 ① read 統一: DATA_DIR 断片化の移行期に usage.jsonl を cross-dir union read する。

    canonical = ``tmp/evolve-anything`` にすると iter_read_data_dirs が兄弟
    legacy(rl-anything) / plugins-data を tmp 配下から導出できる（hermetic）。
    """

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _write(self, d: Path, records) -> None:
        d.mkdir(parents=True, exist_ok=True)
        (d / "usage.jsonl").write_text(
            "\n".join(json.dumps(r) for r in records) + "\n"
        )

    def test_unions_canonical_and_legacy(self, tmp_path):
        """canonical と legacy(rl-anything) の usage を合算する（legacy を取り逃さない）。"""
        canonical = tmp_path / "evolve-anything"
        legacy = tmp_path / "rl-anything"
        now = self._now()
        self._write(canonical, [{"skill_name": "canon-skill", "project": "atlas", "ts": now}])
        self._write(legacy, [{"skill_name": "legacy-skill", "project": "atlas", "ts": now}])
        with mock.patch.object(audit, "DATA_DIR", canonical):
            records = audit.load_usage_data(project_root=Path("/Users/foo/atlas"))
        assert {r.get("skill_name") for r in records} == {"canon-skill", "legacy-skill"}

    def test_unions_plugins_data(self, tmp_path):
        """plugins-data hook split 先の usage も union に含める（#45）。"""
        canonical = tmp_path / "evolve-anything"
        plugins_data = tmp_path / "plugins" / "data" / "evolve-anything-evolve-anything"
        now = self._now()
        self._write(canonical, [{"skill_name": "canon-skill", "project": "atlas", "ts": now}])
        self._write(plugins_data, [{"skill_name": "hook-skill", "project": "atlas", "ts": now}])
        with mock.patch.object(audit, "DATA_DIR", canonical):
            records = audit.load_usage_data(project_root=Path("/Users/foo/atlas"))
        assert {r.get("skill_name") for r in records} == {"canon-skill", "hook-skill"}

    def test_hermetic_tmp_only_reads_canonical(self, tmp_path):
        """canonical が tmp の素直な子のとき兄弟 dir は存在せず canonical のみ読む（実 home 非参照）。"""
        canonical = tmp_path / "evolve-anything"
        now = self._now()
        self._write(canonical, [{"skill_name": "canon-skill", "project": "atlas", "ts": now}])
        with mock.patch.object(audit, "DATA_DIR", canonical):
            records = audit.load_usage_data(project_root=Path("/Users/foo/atlas"))
        assert {r.get("skill_name") for r in records} == {"canon-skill"}

    def test_legacy_rl_anything_attributed_to_evolve_anything(self, tmp_path):
        """旧 slug project='rl-anything' の legacy usage を当 PJ(evolve-anything) に帰属（#45/#47）。"""
        canonical = tmp_path / "evolve-anything"
        legacy = tmp_path / "rl-anything"
        now = self._now()
        self._write(canonical, [{"skill_name": "canon-skill", "project": "evolve-anything", "ts": now}])
        self._write(legacy, [{"skill_name": "legacy-skill", "project": "rl-anything", "ts": now}])
        with mock.patch.object(audit, "DATA_DIR", canonical):
            records = audit.load_usage_data(project_root=Path("/Users/foo/evolve-anything"))
        # 旧 slug rl-anything も当 PJ に畳まれて回収される
        assert {r.get("skill_name") for r in records} == {"canon-skill", "legacy-skill"}

    def test_other_pj_legacy_not_attributed(self, tmp_path):
        """rename されていない他 PJ(bots) の legacy は当 PJ(evolve-anything) に混ぜない。"""
        canonical = tmp_path / "evolve-anything"
        legacy = tmp_path / "rl-anything"
        now = self._now()
        self._write(canonical, [{"skill_name": "canon-skill", "project": "evolve-anything", "ts": now}])
        self._write(legacy, [{"skill_name": "bots-skill", "project": "bots", "ts": now}])
        with mock.patch.object(audit, "DATA_DIR", canonical):
            records = audit.load_usage_data(project_root=Path("/Users/foo/evolve-anything"))
        assert {r.get("skill_name") for r in records} == {"canon-skill"}
