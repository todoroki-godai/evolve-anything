"""reward_ema（バッチ跨ぎ符号付き advantage の EMA 累積）テスト — MAA #64。

RODS（#28, outcome_attribution._reward_variance）は単一スナップショットの reward 分散で
「学習余地大」を判定する。1 時点の高分散は偶然の好成績/不成績の混在でも「学習余地大」と
出てしまう。本モジュールは各スキルの advantage を evolve サイクル（バッチ）跨ぎで符号付き
EMA 累積し、「通時で安定して効くか」を RODS と相補的に区別する（advisory のみ・順位不変）。

store を今作れば累積が始まる plant-the-seed 型。最初の数サイクルは「サイクル不足」で graceful、
3-4 サイクルで意味を持つ。決定論・LLM 非依存・読み取りは書込を一切しない（dry-run 純度）。

HOME 隔離（#457）は冒頭の isolate_home を autouse fixture で呼ぶ。さらに各テストで
``monkeypatch.setattr(reward_ema, "DATA_DIR", tmp_path)``（predictive_validity と同型）。
write 経路（store_write）は ``rl_common.DATA_DIR`` を見るので、persist テストでは
そちらも tmp に向ける。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from test_home_isolation import isolate_home  # noqa: E402

from audit import reward_ema as re  # noqa: E402
from audit import outcome_attribution as oa  # noqa: E402


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    isolate_home(monkeypatch, tmp_path)


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    """reward_ema の read 用 DATA_DIR を tmp に固定する。"""
    monkeypatch.setattr(re, "DATA_DIR", tmp_path)
    return tmp_path


# ---------- compute_batch_advantage ----------

class TestComputeBatchAdvantage:
    def test_signed_advantage_against_baseline(self):
        # baseline = (1.0 + 0.0 + 0.5) / 3 = 0.5。各 advantage = fts - baseline。
        attribution = {
            "good": {"first_try_success": 1.0, "degraded": False},
            "bad": {"first_try_success": 0.0, "degraded": False},
            "mid": {"first_try_success": 0.5, "degraded": False},
        }
        adv = re.compute_batch_advantage(attribution)
        assert adv["good"] == pytest.approx(0.5)
        assert adv["bad"] == pytest.approx(-0.5)
        assert adv["mid"] == pytest.approx(0.0)

    def test_excludes_none_and_degraded_from_baseline_and_output(self):
        attribution = {
            "ok": {"first_try_success": 0.8, "degraded": False},
            "ok2": {"first_try_success": 0.4, "degraded": False},
            "no_fts": {"first_try_success": None, "degraded": False},
            "degraded": {"first_try_success": 0.9, "degraded": True},
        }
        adv = re.compute_batch_advantage(attribution)
        # baseline = (0.8 + 0.4)/2 = 0.6。除外スキルは出力にも baseline にも入らない。
        assert set(adv) == {"ok", "ok2"}
        assert adv["ok"] == pytest.approx(0.2)
        assert adv["ok2"] == pytest.approx(-0.2)

    def test_fewer_than_two_valid_skills_returns_empty(self):
        # 有効 1 スキルでは baseline 比較が無意味（comparability 不成立）→ {} で graceful。
        attribution = {
            "only": {"first_try_success": 0.7, "degraded": False},
            "out": {"first_try_success": None, "degraded": False},
        }
        assert re.compute_batch_advantage(attribution) == {}

    def test_empty_attribution_returns_empty(self):
        assert re.compute_batch_advantage({}) == {}

    def test_rounds_to_four_places(self):
        attribution = {
            "a": {"first_try_success": 1.0, "degraded": False},
            "b": {"first_try_success": 0.0, "degraded": False},
            "c": {"first_try_success": 0.3333, "degraded": False},
        }
        adv = re.compute_batch_advantage(attribution)
        # 全 advantage が round 4 桁に収まる
        for v in adv.values():
            assert v == round(v, 4)


# ---------- fold_ema ----------

class TestFoldEma:
    def test_first_fold_uses_advantage(self):
        ema, n = re.fold_ema(None, 0, 0.4)
        assert ema == pytest.approx(0.4)
        assert n == 1

    def test_second_fold_alpha_weighted(self):
        # alpha=0.3: ema = 0.3*adv + 0.7*prev_ema
        ema, n = re.fold_ema(0.4, 1, -0.2)
        assert ema == pytest.approx(0.3 * -0.2 + 0.7 * 0.4)
        assert n == 2

    def test_n_increments(self):
        _, n = re.fold_ema(0.1, 5, 0.2)
        assert n == 6

    def test_alpha_override(self):
        ema, _ = re.fold_ema(1.0, 1, 0.0, alpha=0.5)
        assert ema == pytest.approx(0.5)

    def test_rounds_to_four_places(self):
        ema, _ = re.fold_ema(0.3333, 1, 0.1111)
        assert ema == round(ema, 4)


# ---------- read_reward_ema ----------

class TestReadRewardEma:
    def _write(self, path: Path, records):
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    def test_missing_file_returns_empty_and_creates_nothing(self, data_dir):
        result = re.read_reward_ema("myslug", data_dir=data_dir)
        assert result == {}
        # dry-run 純度: 読みでファイルを作らない
        assert not (data_dir / "reward_ema.jsonl").exists()

    def test_filters_by_slug(self, data_dir):
        self._write(
            data_dir / "reward_ema.jsonl",
            [
                {"pj_slug": "mine", "skill": "a", "ema": 0.2, "n_batches": 1,
                 "advantage": 0.2, "ts": "2026-06-01T00:00:00+00:00"},
                {"pj_slug": "other", "skill": "a", "ema": 0.9, "n_batches": 1,
                 "advantage": 0.9, "ts": "2026-06-01T00:00:00+00:00"},
            ],
        )
        result = re.read_reward_ema("mine", data_dir=data_dir)
        assert set(result) == {"a"}
        assert result["a"]["ema"] == pytest.approx(0.2)

    def test_folds_legacy_slug_alias(self, data_dir):
        # #112 read 層 alias fold: legacy rl-anything も canonical slug の read で拾う。
        self._write(
            data_dir / "reward_ema.jsonl",
            [
                {"pj_slug": "rl-anything", "skill": "legacy", "ema": 0.3, "n_batches": 1,
                 "advantage": 0.3, "ts": "2026-06-01T00:00:00+00:00"},
                {"pj_slug": "evolve-anything", "skill": "current", "ema": 0.1, "n_batches": 1,
                 "advantage": 0.1, "ts": "2026-06-01T00:00:00+00:00"},
            ],
        )
        result = re.read_reward_ema("evolve-anything", data_dir=data_dir)
        assert set(result) == {"legacy", "current"}

    def test_last_append_wins_per_skill(self, data_dir):
        self._write(
            data_dir / "reward_ema.jsonl",
            [
                {"pj_slug": "s", "skill": "a", "ema": 0.1, "n_batches": 1,
                 "advantage": 0.1, "ts": "2026-06-01T00:00:00+00:00"},
                {"pj_slug": "s", "skill": "a", "ema": 0.25, "n_batches": 2,
                 "advantage": 0.4, "ts": "2026-06-02T00:00:00+00:00"},
            ],
        )
        result = re.read_reward_ema("s", data_dir=data_dir)
        # 最後に append された（時系列で新しい）レコードを採用する
        assert result["a"]["ema"] == pytest.approx(0.25)
        assert result["a"]["n_batches"] == 2
        assert result["a"]["last_advantage"] == pytest.approx(0.4)
        assert result["a"]["ts"] == "2026-06-02T00:00:00+00:00"


# ---------- persist_reward_ema_batch ----------

class TestPersistRewardEmaBatch:
    def _attribution_via_real_join(self, monkeypatch, data_dir):
        """load_usage_data / read_sessions を fixture 化し、実 attribute_outcomes を走らせる。

        mock せず実 join を検証する（自作 fixture の取り違えを実コードが拾えるように）。
        """
        # good: clean / bad: dirty。baseline=(1.0+0.0)/2=0.5 → good=+0.5, bad=-0.5
        usage = [
            {"skill_name": "good", "session_id": "g1"},
            {"skill_name": "bad", "session_id": "b1"},
        ]
        sessions = [
            {"session_id": "g1", "error_count": 0, "tool_sequence": []},
            {"session_id": "b1", "error_count": 3, "tool_sequence": []},
        ]
        monkeypatch.setattr(re, "load_usage_data", lambda *a, **k: usage)
        monkeypatch.setattr(re._om, "read_sessions", lambda base, **k: sessions)
        # write barrier は rl_common.DATA_DIR に書く。read と同 dir に向ける。
        import rl_common
        monkeypatch.setattr(rl_common, "DATA_DIR", data_dir)

    def test_persist_writes_via_store_write_and_reads_back(self, monkeypatch, data_dir):
        self._attribution_via_real_join(monkeypatch, data_dir)
        ts = "2026-06-10T00:00:00+00:00"
        summary = re.persist_reward_ema_batch(
            "/tmp/proj", slug="s", data_dir=data_dir, ts=ts
        )
        assert summary["persisted"] == 2
        assert summary["ts"] == ts
        assert set(summary["skills"]) == {"good", "bad"}

        # store_write 経由で正しい record が書かれ、読み戻すと fold 済み（初回 = advantage）。
        prior = re.read_reward_ema("s", data_dir=data_dir)
        assert prior["good"]["ema"] == pytest.approx(0.5)
        assert prior["good"]["n_batches"] == 1
        assert prior["bad"]["ema"] == pytest.approx(-0.5)
        assert prior["bad"]["n_batches"] == 1
        # pj_slug が正しく書かれている（slug スコープ）
        raw = [json.loads(l) for l in (data_dir / "reward_ema.jsonl").read_text().splitlines() if l]
        assert all(r["pj_slug"] == "s" for r in raw)

    def test_second_batch_folds_ema_and_increments_n(self, monkeypatch, data_dir):
        self._attribution_via_real_join(monkeypatch, data_dir)
        re.persist_reward_ema_batch("/tmp/proj", slug="s", data_dir=data_dir,
                                    ts="2026-06-10T00:00:00+00:00")
        re.persist_reward_ema_batch("/tmp/proj", slug="s", data_dir=data_dir,
                                    ts="2026-06-11T00:00:00+00:00")
        prior = re.read_reward_ema("s", data_dir=data_dir)
        # good: 初回 ema=0.5 → 2回目 adv=0.5: ema = 0.3*0.5 + 0.7*0.5 = 0.5（同値だが n 増分）
        assert prior["good"]["n_batches"] == 2
        assert prior["good"]["ema"] == pytest.approx(0.5)

    def test_insufficient_skills_returns_reason(self, monkeypatch, data_dir):
        usage = [{"skill_name": "only", "session_id": "s1"}]
        sessions = [{"session_id": "s1", "error_count": 0, "tool_sequence": []}]
        monkeypatch.setattr(re, "load_usage_data", lambda *a, **k: usage)
        monkeypatch.setattr(re._om, "read_sessions", lambda base, **k: sessions)
        import rl_common
        monkeypatch.setattr(rl_common, "DATA_DIR", data_dir)
        summary = re.persist_reward_ema_batch("/tmp/proj", slug="s", data_dir=data_dir)
        assert summary["persisted"] == 0
        assert summary["reason"] == "insufficient_skills"
        # 書き込みゼロ（store を作らない）
        assert not (data_dir / "reward_ema.jsonl").exists()


# ---------- ema_stability_label ----------

class TestEmaStabilityLabel:
    def test_none_record_is_insufficient_cycles(self):
        lbl = re.ema_stability_label(None)
        assert lbl["stable"] is False
        assert lbl["sign"] == 0
        assert "サイクル不足" in lbl["label"]

    def test_below_min_cycles_is_insufficient(self):
        lbl = re.ema_stability_label({"ema": 0.9, "n_batches": 2})
        assert lbl["stable"] is False
        assert lbl["sign"] == 0
        assert "サイクル不足" in lbl["label"]

    def test_positive_ema_at_min_cycles(self):
        lbl = re.ema_stability_label({"ema": 0.3, "n_batches": 3})
        assert lbl["stable"] is True
        assert lbl["sign"] == 1
        assert "有効寄り" in lbl["label"]

    def test_negative_ema(self):
        lbl = re.ema_stability_label({"ema": -0.2, "n_batches": 5})
        assert lbl["stable"] is True
        assert lbl["sign"] == -1
        assert "低調寄り" in lbl["label"]

    def test_zero_ema_is_neutral(self):
        lbl = re.ema_stability_label({"ema": 0.0, "n_batches": 4})
        assert lbl["stable"] is True
        assert lbl["sign"] == 0
        assert "中立" in lbl["label"]


# ---------- apply_outcome_ranking advisory 配線 ----------

class TestApplyOutcomeRankingRewardEma:
    def _triage(self):
        return {
            "CREATE": [],
            "UPDATE": [
                {"action": "UPDATE", "skill": "good", "confidence": 0.7},
                {"action": "UPDATE", "skill": "bad", "confidence": 0.7},
            ],
            "SPLIT": [], "MERGE": [], "OK": [], "skipped": False,
        }

    def _usage_sessions(self):
        usage = [
            {"skill_name": "good", "session_id": "g1"},
            {"skill_name": "bad", "session_id": "b1"},
        ]
        sessions = [
            {"session_id": "g1", "error_count": 0, "tool_sequence": ["Edit", "Bash"]},
            {"session_id": "b1", "error_count": 4, "tool_sequence": ["Edit", "Edit", "Edit"]},
        ]
        return usage, sessions

    def test_reward_ema_attached_to_outcome(self):
        triage = self._triage()
        usage, sessions = self._usage_sessions()
        rec = {"ema": 0.3, "n_batches": 4, "last_advantage": 0.2, "ts": "x"}
        result = oa.apply_outcome_ranking(
            triage, usage=usage, sessions=sessions, reward_ema={"bad": rec}
        )
        bad = next(c for c in result["UPDATE"] if c["skill"] == "bad")
        good = next(c for c in result["UPDATE"] if c["skill"] == "good")
        assert bad["outcome"]["reward_ema"] == rec
        # マップに無いスキルは None
        assert good["outcome"]["reward_ema"] is None

    def test_reward_ema_evidence_in_ranking(self):
        triage = self._triage()
        usage, sessions = self._usage_sessions()
        rec = {"ema": -0.2, "n_batches": 5}
        result = oa.apply_outcome_ranking(
            triage, usage=usage, sessions=sessions, reward_ema={"bad": rec}
        )
        ev = result["outcome_ranking"]["UPDATE"]["reward_ema"]
        # after の各スキルに stability label が付く
        assert ev["bad"]["sign"] == -1
        assert ev["good"]["sign"] == 0  # マップ無し → サイクル不足

    def test_ranking_order_unchanged_by_reward_ema(self):
        """advisory 証明: reward_ema の有無で順位は同一（順位を動かさない）。"""
        triage = self._triage()
        usage, sessions = self._usage_sessions()
        without = oa.apply_outcome_ranking(triage, usage=usage, sessions=sessions)
        # reward_ema を逆向き（good を強く有効）に与えても順位は変わらない
        with_ema = oa.apply_outcome_ranking(
            triage, usage=usage, sessions=sessions,
            reward_ema={"good": {"ema": 0.9, "n_batches": 9}},
        )
        assert [c["skill"] for c in without["UPDATE"]] == \
               [c["skill"] for c in with_ema["UPDATE"]]

    def test_reward_ema_none_keeps_legacy_behaviour(self):
        """reward_ema=None（既定）で KeyError を出さず従来挙動。"""
        triage = self._triage()
        usage, sessions = self._usage_sessions()
        result = oa.apply_outcome_ranking(triage, usage=usage, sessions=sessions)
        bad = next(c for c in result["UPDATE"] if c["skill"] == "bad")
        # outcome は付くが reward_ema 列は None（未配線時の安全値）
        assert bad["outcome"]["reward_ema"] is None
