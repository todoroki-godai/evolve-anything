"""evolve.py の skill triage 統合テスト + discover eval_set フィールドテスト。"""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
# 後の `import discover` が必ず shim 経由になるよう、shim ディレクトリを先頭へ。
# Why: shim 未経由で scripts/lib/discover.py を直接 import すると、後続テストの
# `from discover import ...` と spec が分裂し、reload が dict を入れ替えた瞬間に
# 関数の __globals__ と sys.modules["discover"].__dict__ が乖離する（patch が効かない）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "skills" / "discover" / "scripts"))


@pytest.fixture
def discover_module():
    """テスト独立性を保つため毎回 sys.modules から discover を退避→復元する。"""
    saved = sys.modules.pop("discover", None)
    import discover as mod
    yield mod
    sys.modules.pop("discover", None)
    if saved is not None:
        sys.modules["discover"] = saved


class TestDiscoverEvalSetFields:
    """Task 5.2: discover の eval_set_path/eval_set_status フィールド検証。"""

    def test_missed_skill_has_eval_set_fields(self, tmp_path, discover_module):
        """eval set 未生成の missed skill にフィールドが付与される。"""
        discover = discover_module

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "## Skills\n"
            "- /test-skill: テスト。Trigger: テスト, test\n"
        )

        sessions = [
            {"session_id": f"s{i}", "user_prompts": ["テストを実行したい"], "project": tmp_path.name}
            for i in range(3)
        ]
        usage = []

        with mock.patch("telemetry_query.query_sessions", return_value=sessions), \
             mock.patch("telemetry_query.query_usage", return_value=usage):
            result = discover.detect_missed_skills(project_root=tmp_path)

        assert len(result["missed"]) > 0
        entry = result["missed"][0]
        assert "eval_set_path" in entry
        assert "eval_set_status" in entry

    def test_eval_set_status_available_when_file_exists(self, tmp_path, discover_module):
        """eval set ファイルが存在する場合 status=available。"""
        discover = discover_module

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "## Skills\n"
            "- /test-skill: テスト。Trigger: テスト, test\n"
        )

        # eval set ファイルを作成
        eval_dir = Path.home() / ".claude" / "rl-anything" / "eval-sets"
        eval_dir.mkdir(parents=True, exist_ok=True)
        eval_file = eval_dir / "test-skill.json"
        eval_file.write_text("[]")

        sessions = [
            {"session_id": f"s{i}", "user_prompts": ["テストを実行したい"], "project": tmp_path.name}
            for i in range(3)
        ]
        usage = []

        try:
            with mock.patch("telemetry_query.query_sessions", return_value=sessions), \
                 mock.patch("telemetry_query.query_usage", return_value=usage):
                result = discover.detect_missed_skills(project_root=tmp_path)

            if result["missed"]:
                entry = result["missed"][0]
                assert entry["eval_set_status"] == "available"
                assert entry["eval_set_path"] is not None
        finally:
            if eval_file.exists():
                eval_file.unlink()


class TestEvolveTriageIntegration:
    """Task 4.5 + 6.1: evolve 統合テスト。"""

    @pytest.fixture(autouse=True)
    def _isolate_ledger(self, tmp_path, monkeypatch):
        """#308: triage_all_skills が実 home の台帳に書き込むのを防ぐ（hermetic）。"""
        import triage_ledger
        monkeypatch.setattr(triage_ledger, "LEDGER_ROOT", tmp_path / "triage_decisions")

    def test_triage_phase_included_in_evolve(self):
        """triage 結果が phases["skill_triage"] に含まれる。"""
        from skill_triage import triage_all_skills

        result = triage_all_skills(
            sessions=[],
            usage=[],
            missed_skills=[],
            project_root=None,
        )
        # スキルなしの場合は skipped
        assert result["skipped"]

    def test_triage_issues_converted_to_schema(self):
        """triage 結果が issue_schema に変換できる。"""
        from issue_schema import make_skill_triage_issue, SKILL_TRIAGE_CREATE, SKILL_TRIAGE_UPDATE

        create_result = {
            "action": "CREATE",
            "skill": "new-skill",
            "confidence": 0.85,
            "evidence": {"missed_sessions": 5},
        }
        issue = make_skill_triage_issue(create_result)
        assert issue["type"] == SKILL_TRIAGE_CREATE
        assert issue["source"] == "skill_triage"

        update_result = {
            "action": "UPDATE",
            "skill": "existing-skill",
            "confidence": 0.75,
            "evidence": {"missed_sessions": 3, "near_miss_count": 2},
        }
        issue = make_skill_triage_issue(update_result)
        assert issue["type"] == SKILL_TRIAGE_UPDATE

    def test_ok_result_not_converted_to_issue(self):
        """OK 判定は issue に変換されない。"""
        from issue_schema import make_skill_triage_issue

        ok_result = {"action": "OK", "skill": "commit", "confidence": 0.90}
        issue = make_skill_triage_issue(ok_result)
        assert issue == {}

    def test_graceful_degradation_no_sessions(self, tmp_path):
        """データ不足時は triage がスキップされる。"""
        from skill_triage import triage_all_skills

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "## Skills\n"
            "- /my-skill: テスト。Trigger: test\n"
        )

        result = triage_all_skills(
            sessions=[],
            usage=[],
            missed_skills=[],
            project_root=tmp_path,
        )
        # スキルはあるがデータなし → skipped=False, OK に分類
        assert not result["skipped"]
        assert len(result["OK"]) > 0

    def test_e2e_create_update_flow(self, tmp_path):
        """E2E: discover missed → triage → issue 変換フロー。"""
        from skill_triage import triage_all_skills
        from issue_schema import make_skill_triage_issue

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "## Skills\n"
            "- /existing-skill: 既存スキル。Trigger: existing, 既存\n"
        )

        sessions = [
            {"session_id": f"s{i}", "user_prompts": [f"既存のスキルを使いたい {i}"]}
            for i in range(5)
        ]
        usage = [
            {"session_id": "s0", "skill_name": "existing-skill"},
        ]
        missed = [
            {"skill": "existing-skill", "triggers_matched": ["既存"], "session_count": 3},
            {"skill": "new-skill", "triggers_matched": ["new"], "session_count": 4},
        ]

        result = triage_all_skills(
            sessions=sessions,
            usage=usage,
            missed_skills=missed,
            project_root=tmp_path,
        )

        # CREATE/UPDATE/OK のいずれかに分類される
        all_actions = result["CREATE"] + result["UPDATE"] + result["OK"]
        assert len(all_actions) > 0

        # issue 変換
        issues = []
        for action in ("CREATE", "UPDATE", "SPLIT", "MERGE"):
            for triage in result.get(action, []):
                issue = make_skill_triage_issue(triage)
                if issue:
                    issues.append(issue)
        # CREATE の new-skill がある
        create_issues = [i for i in issues if i["type"] == "skill_triage_create"]
        assert len(create_issues) > 0


class TestTriageLedgerIntegration:
    """#308: triage_skill→台帳の配線、連続 evolve の抑制、再発昇格、TTL を E2E で検証。

    meta_quality の SKIP は「CREATE 候補 & 名前 Jaccard>0.6 の既存スキルあり & 低頻度」で
    発火する。slug は単一トークン化されるため、ここでは triage_skill に existing_skills /
    missed_skills を直接注入してスペース込みの近接名を与え、SKIP を自然に発火させる。
    """

    @pytest.fixture(autouse=True)
    def _isolate_ledger(self, tmp_path, monkeypatch):
        """副作用隔離: 台帳を実 home でなく tmp に逃がす（別 PJ slug 混入も防ぐ）。"""
        import triage_ledger
        monkeypatch.setattr(triage_ledger, "LEDGER_ROOT", tmp_path / "triage_decisions")

    def _run(self, now):
        """SKIP が発火する triage_skill を1回回す。"""
        from skill_triage import triage_skill
        return triage_skill(
            "deploy skill clone",
            sessions=[],
            usage=[],
            missed_skills=[
                {"skill": "deploy skill clone", "triggers_matched": ["deploy"], "session_count": 5},
            ],
            existing_skills={"deploy skill"},  # Jaccard("deploy skill clone","deploy skill")=0.667>0.6
            ledger_slug="proj",
            ledger_now=now,
        )

    def test_first_run_skip_then_second_run_suppressed(self):
        """① 初回 SKIP は surface、2回目はクールダウン内で抑制される（連続 evolve 冪等性）。"""
        DAY = 86400.0
        r1 = self._run(now=1000.0)
        assert r1["action"] == "SKIP"
        assert r1["suppressed"] is False
        assert r1["ledger_status"] == "new"

        r2 = self._run(now=1000.0 + DAY)
        assert r2["suppressed"] is True
        assert r2["ledger_status"] == "suppressed"

    def test_repeated_skip_escalates_to_review(self):
        """② 窓内で ESCALATE_N 回 SKIP → REVIEW 昇格。"""
        import triage_ledger
        DAY = 86400.0
        out = None
        now = 1000.0
        for _ in range(triage_ledger.ESCALATE_N):
            out = self._run(now=now)
            now += DAY
        assert out["action"] == "REVIEW"
        assert out["ledger_status"] == "escalated"

    def test_ttl_expiry_forces_reeval_once(self):
        """③ TTL 超過で 🔄 強制再評価が1回だけ出る。"""
        import triage_ledger
        DAY = 86400.0
        self._run(now=1000.0)
        expired = 1000.0 + (triage_ledger.DEFAULT_TTL_DAYS + 1) * DAY
        out = self._run(now=expired)
        assert out["ledger_status"] == "ttl_expired"
        assert "🔄" in out["ledger_note"]

    def test_all_skills_collapses_suppressed_and_keeps_summary(self):
        """triage_all_skills が抑制を SKIP_SUPPRESSED に畳み summary 行を常に持つ。"""
        from skill_triage import triage_all_skills
        # スキルが無い PJ でも summary は出る（沈黙≠評価）
        r = triage_all_skills(sessions=[], usage=[], missed_skills=[], project_root=None)
        # project_root=None → no_skills_found で skipped だが空 summary キーは存在
        assert "skip_suppressed_summary" in r
