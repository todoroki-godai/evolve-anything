"""evaluation_provenance — 評価実行条件（harness）の記録契約テスト（#309）。

契約の要点（codex cold-read の設計判断）:
  - 不明値を推測しない（None のまま記録する）
  - 「非該当」（決定論評価に model は無い）と「観測不能」（判定したが記録が取れない）を区別する
  - model alias は渡された値を verbatim で残す（`sonnet` を具体バージョンへ展開しない）
  - 集約は単一 model へ潰さず judge_models + mixed_provenance で表現する
決定論・ゼロ LLM。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import evaluation_provenance as ep  # noqa: E402


# --- envelope ---------------------------------------------------------------

def test_envelope_has_required_common_fields():
    prov = ep.build_provenance(
        evaluation_kind=ep.KIND_DETERMINISTIC, producer="chaos.compute_chaos_score"
    )
    assert prov["schema_version"] == ep.SCHEMA_VERSION
    assert prov["evaluation_kind"] == ep.KIND_DETERMINISTIC
    assert prov["producer"] == "chaos.compute_chaos_score"
    assert set(prov["runtime"]) == {"name", "session_id"}
    assert prov["plugin"]["name"] == "evolve-anything"
    assert prov["recorded_at"]


def test_plugin_version_is_read_from_running_code():
    """実行中コードの .claude-plugin/plugin.json を読む（ハードコードしない）。"""
    import json

    from plugin_root import PLUGIN_ROOT

    expected = json.loads(
        (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )["version"]
    assert ep.plugin_version() == expected


def test_unobserved_runtime_is_none_not_guessed():
    """runtime を渡さなければ None。歴史的既定の 'claude' に倒さない。"""
    prov = ep.build_provenance(evaluation_kind=ep.KIND_DETERMINISTIC, producer="x")
    assert prov["runtime"]["name"] is None
    assert prov["runtime"]["session_id"] is None


def test_deterministic_kind_has_no_judge_key():
    """非該当は null を撒かずキー自体を持たない（kind で判別できる）。"""
    prov = ep.build_provenance(evaluation_kind=ep.KIND_DETERMINISTIC, producer="x")
    assert "judge" not in prov


def test_deterministic_carries_config_and_inputs_when_given():
    prov = ep.build_provenance(
        evaluation_kind=ep.KIND_DETERMINISTIC,
        producer="telemetry",
        config={"fingerprint": "sha256:abc"},
        inputs={"window_days": 30, "rows": 120},
    )
    assert prov["config"] == {"fingerprint": "sha256:abc"}
    assert prov["inputs"] == {"window_days": 30, "rows": 120}


def test_unknown_evaluation_kind_is_rejected():
    with pytest.raises(ValueError):
        ep.build_provenance(evaluation_kind="vibes", producer="x")


# --- judge context ----------------------------------------------------------

def test_judge_model_alias_is_kept_verbatim():
    ctx = ep.build_judge_context(model="sonnet")
    assert ctx["model"] == "sonnet"


def test_judge_context_defaults_are_unknown_not_fabricated():
    ctx = ep.build_judge_context(model="sonnet")
    assert ctx["effort"] is None
    assert ctx["tool_policy"]["mode"] == ep.TOOL_POLICY_UNKNOWN
    assert ctx["tool_policy"]["allowed_tools"] is None


def test_judge_kind_embeds_judge_context():
    prov = ep.build_provenance(
        evaluation_kind=ep.KIND_LLM_JUDGE,
        producer="judge_audit",
        judge=ep.build_judge_context(model="haiku", tool_policy_mode=ep.TOOL_POLICY_CLI_DEFAULT),
        runtime_name="claude",
    )
    assert prov["judge"]["model"] == "haiku"
    assert prov["judge"]["tool_policy"]["mode"] == ep.TOOL_POLICY_CLI_DEFAULT
    assert prov["runtime"]["name"] == "claude"


def test_deterministic_kind_rejects_judge_context():
    """決定論評価に judge を渡すのは契約違反（非該当と観測不能の区別が壊れる）。"""
    with pytest.raises(ValueError):
        ep.build_provenance(
            evaluation_kind=ep.KIND_DETERMINISTIC,
            producer="p",
            judge=ep.build_judge_context(model="sonnet"),
        )


def test_judge_kind_without_context_records_unobserved_judge():
    """判定は LLM だが条件を観測できなかった場合も、judge キー自体は残す。"""
    prov = ep.build_provenance(evaluation_kind=ep.KIND_LLM_JUDGE, producer="p")
    assert "judge" in prov
    assert prov["judge"]["model"] is None


# --- aggregate --------------------------------------------------------------

def test_aggregate_single_model_is_not_mixed():
    layer_provs = [
        ep.build_provenance(
            evaluation_kind=ep.KIND_LLM_JUDGE, producer="p", judge=ep.build_judge_context(model="a")
        )
        for _ in range(3)
    ]
    agg = ep.aggregate_provenance("constitutional", layer_provs, layers_total=3)
    assert agg["evaluation_kind"] == ep.KIND_LLM_JUDGE_AGGREGATE
    assert agg["judge_models"] == ["a"]
    assert agg["mixed_provenance"] is False
    assert agg["layers_with_provenance"] == 3
    assert agg["layers_total"] == 3


def test_aggregate_multiple_models_is_mixed_and_sorted():
    layer_provs = [
        ep.build_provenance(
            evaluation_kind=ep.KIND_LLM_JUDGE, producer="p", judge=ep.build_judge_context(model=m)
        )
        for m in ("opus", "haiku", "opus")
    ]
    agg = ep.aggregate_provenance("constitutional", layer_provs, layers_total=3)
    assert agg["judge_models"] == ["haiku", "opus"]
    assert agg["mixed_provenance"] is True


def test_aggregate_tolerates_layers_without_provenance():
    """旧 cache（provenance 無し）が混ざっても壊れず、欠損として数える。"""
    provs = [
        ep.build_provenance(
            evaluation_kind=ep.KIND_LLM_JUDGE, producer="p", judge=ep.build_judge_context(model="a")
        ),
        None,
    ]
    agg = ep.aggregate_provenance("constitutional", provs, layers_total=2)
    assert agg["judge_models"] == ["a"]
    assert agg["layers_with_provenance"] == 1
    assert agg["layers_total"] == 2
    # 一部でも欠けていれば「揃っていない」ことを surface する
    assert agg["mixed_provenance"] is True


def test_aggregate_with_no_provenance_at_all_does_not_fabricate_models():
    agg = ep.aggregate_provenance("constitutional", [None, None], layers_total=2)
    assert agg["judge_models"] == []
    assert agg["layers_with_provenance"] == 0


def test_aggregate_detects_mixture_in_axes_other_than_model():
    """model が同じでも effort / tool policy が違えば混在（交絡は model 軸だけではない）。"""
    provs = [
        ep.build_provenance(
            evaluation_kind=ep.KIND_LLM_JUDGE,
            producer="p",
            judge=ep.build_judge_context(model="sonnet", effort=e),
        )
        for e in ("high", "max")
    ]
    agg = ep.aggregate_provenance("constitutional", provs, layers_total=2)
    assert agg["judge_models"] == ["sonnet"]  # model 軸では見分けがつかない
    assert agg["mixed_provenance"] is True
    assert len(agg["harness_variants"]) == 2


def test_aggregate_keeps_layer_plugin_versions_separate_from_envelope():
    """envelope の plugin.version は「集約した時点」。layer 由来の版は別に保持する。"""
    old = ep.build_provenance(
        evaluation_kind=ep.KIND_LLM_JUDGE, producer="p", judge=ep.build_judge_context(model="a")
    )
    old["plugin"] = {"name": ep.PLUGIN_NAME, "version": "0.0.1-old"}
    new = ep.build_provenance(
        evaluation_kind=ep.KIND_LLM_JUDGE, producer="p", judge=ep.build_judge_context(model="a")
    )
    agg = ep.aggregate_provenance("constitutional", [old, new], layers_total=2)
    assert "0.0.1-old" in agg["plugin_versions"]
    assert agg["plugin"]["version"] == ep.plugin_version()
    # 版が跨っていれば同一 model でも混在扱い
    assert agg["mixed_provenance"] is True


# --- writer 境界 -------------------------------------------------------------

def test_finalize_fills_missing_common_fields():
    partial = {"evaluation_kind": ep.KIND_DETERMINISTIC, "producer": "p"}
    done = ep.finalize_provenance(partial)
    assert done["schema_version"] == ep.SCHEMA_VERSION
    assert done["plugin"]["version"] == ep.plugin_version()
    assert done["recorded_at"]


def test_finalize_does_not_overwrite_producer_supplied_values():
    prov = ep.build_provenance(
        evaluation_kind=ep.KIND_LLM_JUDGE,
        producer="judge_audit",
        judge=ep.build_judge_context(model="sonnet"),
        recorded_at="2026-01-01T00:00:00+00:00",
    )
    done = ep.finalize_provenance(prov)
    assert done["recorded_at"] == "2026-01-01T00:00:00+00:00"
    assert done["judge"]["model"] == "sonnet"


def test_finalize_is_idempotent():
    once = ep.finalize_provenance({"evaluation_kind": ep.KIND_DETERMINISTIC, "producer": "p"})
    twice = ep.finalize_provenance(dict(once))
    assert twice == once


def test_finalize_none_records_explicit_unknown_without_fabricating():
    done = ep.finalize_provenance(None)
    assert done["evaluation_kind"] == ep.KIND_UNKNOWN
    assert done["producer"] is None
    assert "judge" not in done


def test_attach_provenance_sets_key_on_record():
    rec = {"id": "x"}
    out = ep.attach_provenance(rec, ep.build_provenance(
        evaluation_kind=ep.KIND_DETERMINISTIC, producer="p"
    ))
    assert out is rec  # in-place（呼び出し側の rec をそのまま書き込む）
    assert rec["provenance"]["producer"] == "p"


def test_attach_provenance_with_none_still_marks_unknown():
    rec = {"id": "x"}
    ep.attach_provenance(rec, None)
    assert rec["provenance"]["evaluation_kind"] == ep.KIND_UNKNOWN


# --- 後方互換 ----------------------------------------------------------------

def test_read_side_of_old_record_without_provenance_is_none():
    """旧レコードは provenance 欠損のまま読める（遡及埋めをしない契約）。"""
    old = {"id": "x", "score": 0.2}
    assert ep.read_provenance(old) is None


def test_read_side_returns_envelope_when_present():
    rec = {"id": "x"}
    ep.attach_provenance(rec, ep.build_provenance(
        evaluation_kind=ep.KIND_DETERMINISTIC, producer="p"
    ))
    assert ep.read_provenance(rec)["producer"] == "p"
