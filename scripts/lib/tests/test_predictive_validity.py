"""予測妥当性（in/out-of-sample 順位相関）テスト — 重み昇格の第4条件（#42）。

ADR-046 の重み昇格レディネスに「予測妥当性」を第4条件として加える。狙いは
「in-sample（古い半分のセッション）で良かった skill 順位が、out-of-sample
（新しい半分 = 未知/配備時）でも当たるか」を Spearman 順位相関で測ること。
集計平均ベースの順位が分布外（新しいセッション）に転移しなければ昇格させない
（誤昇格の抑制）。

決定論・LLM 非依存。tmp の DATA_DIR に疑似 skill_activations.jsonl / sessions.jsonl を
置いて算出する。monkeypatch は文字列ターゲットを避け、import した module オブジェクトを
直接 patch する（order-dependent 失敗の既知 pitfall 準拠）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from audit import predictive_validity as pv  # noqa: E402
from audit import sections_promotion_readiness as sec  # noqa: E402


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + ("\n" if records else ""))


# skill_activations.jsonl: skill / session_id / project / ts / invocation_trigger / parent_skill
def _activation(skill: str, sid: str, dt: datetime, *, project: str = "p") -> dict:
    return {
        "skill": skill,
        "session_id": sid,
        "project": project,
        "ts": _iso(dt),
        "invocation_trigger": "top-level",
        "parent_skill": None,
    }


# sessions.jsonl: session_id / error_count / project / timestamp
def _session(sid: str, dt: datetime, *, error_count: int = 0, project: str = "p") -> dict:
    return {
        "session_id": sid,
        "project": project,
        "timestamp": _iso(dt),
        "error_count": error_count,
    }


# ============================================================================
# Spearman 純実装（タイは平均順位）
# ============================================================================

class TestSpearmanPure:
    def test_perfect_positive(self):
        # 完全一致 → rho = 1.0
        rho = pv._spearman([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0])
        assert rho == pytest.approx(1.0)

    def test_perfect_negative(self):
        # 完全逆転 → rho = -1.0
        rho = pv._spearman([1.0, 2.0, 3.0, 4.0], [4.0, 3.0, 2.0, 1.0])
        assert rho == pytest.approx(-1.0)

    def test_tie_average_rank(self):
        # タイは平均順位で処理する。x の前2つが同値、後2つが同値。
        # ランク: [1.5, 1.5, 3.5, 3.5]。y も同パターンなら完全相関。
        rho = pv._spearman([5.0, 5.0, 9.0, 9.0], [2.0, 2.0, 8.0, 8.0])
        assert rho == pytest.approx(1.0)

    def test_ranks_average_helper(self):
        # _ranks がタイに平均順位を割り当てる単体検証。
        assert pv._ranks([10.0, 10.0, 30.0]) == pytest.approx([1.5, 1.5, 3.0])
        assert pv._ranks([3.0, 1.0, 2.0]) == pytest.approx([3.0, 1.0, 2.0])

    def test_all_tied_returns_zero(self):
        # 全値同一 = 分散ゼロ → 相関定義不能。決定論的に 0.0 を返す（捏造しない）。
        assert pv._spearman([1.0, 1.0, 1.0], [4.0, 5.0, 6.0]) == 0.0


# ============================================================================
# bare skill 名の正規化（#577 の罠: namespace prefix を畳む）
# 正規化ロジック本体は rl_common.bare_skill_name に単一化済み（#145）。
# predictive_validity は re-export したものをそのまま使うので、ここでは
# 同モジュール経由の呼び出しが動くこと（配線）だけを確認する。
# ============================================================================

class TestBareSkillNormalization:
    def test_namespace_prefix_stripped(self):
        assert pv.bare_skill_name("rl-anything:docs-refresh") == "docs-refresh"
        assert pv.bare_skill_name("skill-creator:skill-creator") == "skill-creator"
        assert pv.bare_skill_name("review") == "review"


# ============================================================================
# check_predictive_validity: 高相関 / 低相関 / データ不足
# ============================================================================

class TestPredictiveValidity:
    def _setup_corpus(self, tmp_path, *, reverse_out: bool):
        """in-sample（古い半分）と out-of-sample（新しい半分）の corpus を作る。

        5 skill それぞれ in/out 両半分に十分なセッション数（≥3）で出現させる。
        各 skill の first_try_success（error_count==0 の session 割合）を skill ごとに
        段階的に変え、in-sample で明確な順位を作る。out-of-sample は reverse_out=False
        なら同順位（高相関）、True なら逆順位（低相関）。
        """
        now = _now()
        old = now - timedelta(days=20)   # in-sample（古い半分）
        new = now - timedelta(days=2)    # out-of-sample（新しい半分）
        skills = ["s0", "s1", "s2", "s3", "s4"]
        n_sess = 4  # 1 skill / 1 half あたり 4 session（floor 3 以上）

        acts: list[dict] = []
        sess: list[dict] = []
        sid_counter = 0

        def emit(skill: str, half_dt, clean_frac: float):
            nonlocal sid_counter
            n_clean = round(clean_frac * n_sess)
            for j in range(n_sess):
                sid = f"sid_{sid_counter}"
                sid_counter += 1
                ec = 0 if j < n_clean else 1
                acts.append(_activation(skill, sid, half_dt))
                sess.append(_session(sid, half_dt, error_count=ec))

        # in-sample: clean_frac を skill index 順に増やす → 明確な昇順順位
        for i, sk in enumerate(skills):
            emit(sk, old, clean_frac=i / 4.0)  # 0.0, 0.25, 0.5, 0.75, 1.0

        # out-of-sample: 同順位 or 逆順位
        for i, sk in enumerate(skills):
            frac = (4 - i) / 4.0 if reverse_out else i / 4.0
            emit(sk, new, clean_frac=frac)

        _write_jsonl(tmp_path / "skill_activations.jsonl", acts)
        _write_jsonl(tmp_path / "sessions.jsonl", sess)

    def test_high_correlation_pass(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pv, "DATA_DIR", tmp_path)
        self._setup_corpus(tmp_path, reverse_out=False)
        result = pv.check_predictive_validity(days=60)
        assert result["pass"] is True
        assert result["rho"] is not None and result["rho"] >= pv.PREDICTIVE_VALIDITY_RHO_FLOOR
        assert result["n_skills"] >= pv.MIN_RANKED_SKILLS
        # evidence は上位3件、各エントリに rank/fts を持つ
        assert len(result["evidence"]) <= 3
        for ev in result["evidence"]:
            assert set(ev.keys()) == {"skill", "rank_in", "rank_out", "fts_in", "fts_out"}

    def test_low_correlation_fail(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pv, "DATA_DIR", tmp_path)
        self._setup_corpus(tmp_path, reverse_out=True)
        result = pv.check_predictive_validity(days=60)
        assert result["pass"] is False
        assert result["rho"] is not None and result["rho"] < pv.PREDICTIVE_VALIDITY_RHO_FLOOR
        assert result["reason"] is None  # データはあるが相関が低いだけ

    def test_insufficient_skills(self, tmp_path, monkeypatch):
        # 両半分に出現する skill が MIN_RANKED_SKILLS 未満 → insufficient_data
        monkeypatch.setattr(pv, "DATA_DIR", tmp_path)
        now = _now()
        old = now - timedelta(days=20)
        new = now - timedelta(days=2)
        acts: list[dict] = []
        sess: list[dict] = []
        # 2 skill だけ両半分に出す（floor 5 未満）
        for i, sk in enumerate(["a", "b"]):
            for half_dt in (old, new):
                for j in range(4):
                    sid = f"{sk}_{half_dt.day}_{j}"
                    acts.append(_activation(sk, sid, half_dt))
                    sess.append(_session(sid, half_dt, error_count=j % 2))
        _write_jsonl(tmp_path / "skill_activations.jsonl", acts)
        _write_jsonl(tmp_path / "sessions.jsonl", sess)
        result = pv.check_predictive_validity(days=60)
        assert result["pass"] is False
        assert result["reason"] == "insufficient_data"
        assert result["n_skills"] < pv.MIN_RANKED_SKILLS
        assert result["rho"] is None  # 捏造しない

    def test_no_activations_insufficient(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pv, "DATA_DIR", tmp_path)
        result = pv.check_predictive_validity(days=60)
        assert result["pass"] is False
        assert result["reason"] == "insufficient_data"
        assert result["n_skills"] == 0

    def test_namespace_prefixes_collapsed(self, tmp_path, monkeypatch):
        # 同一 bare skill が異なる namespace prefix で来ても 1 skill に畳まれる。
        monkeypatch.setattr(pv, "DATA_DIR", tmp_path)
        now = _now()
        old = now - timedelta(days=20)
        new = now - timedelta(days=2)
        acts: list[dict] = []
        sess: list[dict] = []
        # 5 skill。s0 は prefix 違い（rl-anything:s0 と s0）で出すが bare では 1 つ。
        for i in range(5):
            for half_dt in (old, new):
                for j in range(4):
                    name = f"plugin:s{i}" if half_dt is old else f"s{i}"
                    sid = f"s{i}_{half_dt.day}_{j}"
                    acts.append(_activation(name, sid, half_dt))
                    sess.append(_session(sid, half_dt, error_count=0 if j < i else 1))
        _write_jsonl(tmp_path / "skill_activations.jsonl", acts)
        _write_jsonl(tmp_path / "sessions.jsonl", sess)
        result = pv.check_predictive_validity(days=60)
        # bare 化されていれば 5 skill 全てが両半分にマッチして ranked される
        assert result["n_skills"] == 5

    def test_dry_run_no_store_write(self, tmp_path, monkeypatch):
        # 読み取りのみ — check は DATA_DIR に何も書かない（byte 不変）。
        monkeypatch.setattr(pv, "DATA_DIR", tmp_path)
        self._setup_corpus(tmp_path, reverse_out=False)

        def _snapshot() -> dict[str, bytes]:
            return {
                str(p.relative_to(tmp_path)): p.read_bytes()
                for p in sorted(tmp_path.rglob("*")) if p.is_file()
            }

        before = _snapshot()
        pv.check_predictive_validity(days=60)
        assert _snapshot() == before

    def test_multi_record_session_folds_not_last_wins(self, tmp_path, monkeypatch):
        """同一 session_id に error_count を持つ行と持たない行が混在しても実測値を保持する（#144）。

        sessions ストアは 1 セッションに session_summary 型（error_count あり）と
        instructions_loaded 型（error_count なし）の複数行が混ざる。read_sessions は
        (session_id, timestamp) で dedup し **timestamp 昇順** で返すため、error_count 欠損行が
        後発 timestamp だと last-wins 上書きで先行の実測 error_count が None に潰れ、当該
        セッションが scored（分母）から丸ごと除外される（#138 と同型 seam）。
        fold_session_error_counts 経由（error_count を持つ行の max・欠損行は既存値を壊さない）で
        構築すれば実測値が保持され、5 skill が ranked される。
        """
        monkeypatch.setattr(pv, "DATA_DIR", tmp_path)
        now = _now()
        old = now - timedelta(days=20)  # in-sample（古い半分）
        new = now - timedelta(days=2)   # out-of-sample（新しい半分）
        acts: list[dict] = []
        sess: list[dict] = []
        for i in range(5):
            for label, half_dt in (("old", old), ("new", new)):
                for j in range(4):
                    sid = f"s{i}_{label}_{j}"
                    ec = 0 if j < i else 1
                    acts.append(_activation(f"s{i}", sid, half_dt))
                    # error_count を持つ session_summary 行（先発 timestamp）
                    sess.append(_session(sid, half_dt, error_count=ec))
                    # error_count を持たない record 行（後発 timestamp → last-wins ならこれが勝つ）
                    sess.append({
                        "session_id": sid,
                        "project": "p",
                        "timestamp": _iso(half_dt + timedelta(seconds=1)),
                    })
        _write_jsonl(tmp_path / "skill_activations.jsonl", acts)
        _write_jsonl(tmp_path / "sessions.jsonl", sess)

        result = pv.check_predictive_validity(days=60)
        # fold されていれば全 session の実測 error_count が保持され 5 skill が ranked される。
        # last-wins だと後発の欠損行が全 session を None 化 → scored=0 → n_skills=0 (insufficient)。
        assert result["reason"] != "insufficient_data"
        assert result["n_skills"] == 5


# ============================================================================
# section 行: pass / insufficient / 低rho の3分岐
# ============================================================================

class TestPredictiveValiditySectionLine:
    def test_pass_line(self):
        lines = sec._predictive_validity_line({
            "pass": True, "reason": None, "rho": 0.83, "n_skills": 6,
            "in_sample_n": 24, "out_sample_n": 24, "evidence": [],
        })
        combined = "\n".join(lines)
        assert "✓" in combined
        assert "条件4 予測妥当性" in combined
        assert "0.83" in combined
        assert "6" in combined  # n_skills

    def test_insufficient_line_says_data_shortage(self):
        lines = sec._predictive_validity_line({
            "pass": False, "reason": "insufficient_data", "rho": None,
            "n_skills": 2, "in_sample_n": 8, "out_sample_n": 8, "evidence": [],
        })
        combined = "\n".join(lines)
        assert "✗" in combined
        assert "条件4 予測妥当性" in combined
        assert "データ不足" in combined  # 捏造せず明示
        assert str(pv.MIN_RANKED_SKILLS) in combined
        assert "2" in combined  # 現在件数

    def test_low_rho_line(self):
        lines = sec._predictive_validity_line({
            "pass": False, "reason": None, "rho": 0.12, "n_skills": 7,
            "in_sample_n": 28, "out_sample_n": 28, "evidence": [],
        })
        combined = "\n".join(lines)
        assert "✗" in combined
        assert "条件4 予測妥当性" in combined
        assert "0.12" in combined
        assert str(pv.PREDICTIVE_VALIDITY_RHO_FLOOR) in combined
