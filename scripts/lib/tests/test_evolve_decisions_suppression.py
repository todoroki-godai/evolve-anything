"""evolve_decisions._suppression のテスト（#446: reject された提案の再抑制）。

`remediation.suppression_ledger`（TTL 既定45日・store_registry 登録済み）を薄い adapter
経由で流用し、`evolve_decisions` の pending entry（proposal_id ベース）を抑制する。
新規ストアは作らない。すべて LLM-free・決定論。

設計: docs/decisions/drafts/446-reject-resuppression-design.md
"""
import json
import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

import remediation.suppression_ledger as sl  # noqa: E402
from evolve_decisions import _suppression as sup  # noqa: E402


def _entry(id_="evdiff_abc", proposal_type="skill_diff", **extra):
    e = {"id": id_, "proposal_type": proposal_type}
    e.update(extra)
    return e


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")
    return tmp_path


# ── _issue_for: lane 固定 file / proposal_id が一意性を担保（[Must]1） ──────


class TestIssueFor:
    def test_file_is_lane_constant_not_entry_derived(self):
        """[Must]1: file は entry の repo_id/relative_path 等に依存しない固定リテラル。"""
        a = sup._issue_for(_entry(id_="evdiff_1", repo_id="r1", relative_path="a/SKILL.md"))
        b = sup._issue_for(_entry(id_="evdiff_1", repo_id="r2", relative_path="b/SKILL.md"))
        assert a["file"] == b["file"]  # repo_id/relative_path が違っても file は同じ

    @pytest.mark.parametrize("bad_id", [None, "", 123, {"a": 1}])
    def test_missing_or_non_string_id_raises_instead_of_collapsing_keys(self, bad_id):
        """id が使えない entry は ValueError。**None を素通しさせない**。

        `dedup_key()` は detail の値が str/int/float でなければその成分を落とすため、
        `target=None` のまま渡すと **id を持たない entry が全部「type + file だけ」の
        同一キーに潰れる**。その状態で1件 reject を記録すると、同じ lane の id 無し
        entry が丸ごと巻き添えで抑制される（codex 1巡目 [Must]6 の failure mode）。
        """
        entry = {"proposal_type": "skill_diff"}
        if bad_id is not None:
            entry["id"] = bad_id
        with pytest.raises(ValueError):
            sup._issue_for(entry)

    def test_id_less_entries_are_not_suppressed_and_do_not_collapse(self):
        """id 無し entry は fail-open で通し、互いに巻き添えにしない（統合レベルの回帰防止）。

        「1件を reject 記録 → もう1件まで抑制される」が起きないことを、キー計算でなく
        `filter_rejected` / `record_pending_rejection` の外形で固定する。
        """
        a = {"proposal_type": "skill_diff"}
        b = {"proposal_type": "skill_diff"}
        # 記録は失敗（エラー文字列が返るだけで例外は出ない）＝ ledger を汚さない。
        err = sup.record_pending_rejection(a, slug="proj")
        assert err is not None and "ValueError" in err
        # どちらも抑制されず、候補単位のエラーとして surface される。
        kept, stats = sup.filter_rejected([a, b], slug="proj")
        assert kept == [a, b]
        assert stats["suppressed_total"] == 0
        assert len(stats["candidate_errors"]) == 2

    def test_same_proposal_id_yields_same_dedup_key_despite_differing_fields(self):
        """[Must]1 の核心: proposal_id が同じなら、他のフィールドが違っても dedup_key は一致する。"""
        a = sup._issue_for(_entry(id_="evdiff_1", repo_id="r1", relative_path="a/SKILL.md"))
        b = sup._issue_for(_entry(id_="evdiff_1", repo_id="r2", relative_path="b/SKILL.md"))
        assert sl.dedup_key(a) == sl.dedup_key(b)

    def test_different_proposal_id_yields_different_dedup_key(self):
        a = sup._issue_for(_entry(id_="evdiff_1"))
        b = sup._issue_for(_entry(id_="evdiff_2"))
        assert sl.dedup_key(a) != sl.dedup_key(b)

    def test_advisory_and_skill_lane_share_key_shape(self):
        """advisory/skill いずれも type が異なるだけで file は同じ lane 定数を使う。"""
        skill = sup._issue_for(_entry(id_="x", proposal_type="skill_diff"))
        advisory = sup._issue_for(_entry(id_="x", proposal_type="advisory"))
        assert skill["file"] == advisory["file"]
        assert skill["type"] != advisory["type"]

    def test_legacy_schema_missing_repo_id_still_works(self):
        """#402 導入前の旧スキーマ entry（repo_id/relative_path 欠落）でも id さえあれば動く。"""
        legacy = {"id": "evdiff_legacy", "proposal_type": "skill_diff", "skill_path": "x"}
        issue = sup._issue_for(legacy)
        assert issue["detail"]["target"] == "evdiff_legacy"


# ── filter_rejected: 抑制 / TTL / fail-open ────────────────────────────────


class TestFilterRejected:
    def test_no_rejection_recorded_keeps_all(self):
        pending = [_entry(id_="evdiff_1"), _entry(id_="evdiff_2")]
        kept, stats = sup.filter_rejected(pending, slug="proj")
        assert kept == pending
        assert stats["suppressed_total"] == 0
        assert stats["suppressed"] == []
        assert stats["ledger_read_error"] is None

    def test_rejected_proposal_id_is_suppressed(self):
        pending = [_entry(id_="evdiff_1"), _entry(id_="evdiff_2")]
        sl.record_rejection(sup._issue_for(pending[0]), slug="proj")
        kept, stats = sup.filter_rejected(pending, slug="proj")
        assert [e["id"] for e in kept] == ["evdiff_2"]
        assert stats["suppressed_total"] == 1
        assert stats["suppressed"] == [{"id": "evdiff_1", "file": "evolve_decisions"}]

    def test_changed_before_sha_produces_new_id_and_is_not_suppressed(self):
        """proposal_id は before_sha を含むため、内容が変われば新 ID になり抑制対象から外れる。"""
        old = _entry(id_="evdiff_v1")
        sl.record_rejection(sup._issue_for(old), slug="proj")
        new = _entry(id_="evdiff_v2")  # 内容変化後の別 proposal_id（同じ対象・別世代）
        kept, stats = sup.filter_rejected([new], slug="proj")
        assert kept == [new]
        assert stats["suppressed_total"] == 0

    def test_ttl_expiry_resurfaces(self):
        pending = [_entry(id_="evdiff_1")]
        sl.record_rejection(sup._issue_for(pending[0]), slug="proj", now=1000.0, ttl_days=45)
        # TTL 内
        kept, _ = sup.filter_rejected(pending, slug="proj", now=1000.0 + 10 * 86400)
        assert kept == []
        # TTL 超過後は毎回提示（1回だけでなく恒久的に解除される・design §3.2）
        kept, _ = sup.filter_rejected(pending, slug="proj", now=1000.0 + 46 * 86400)
        assert kept == pending
        kept, _ = sup.filter_rejected(pending, slug="proj", now=1000.0 + 100 * 86400)
        assert kept == pending

    def test_per_slug_isolation(self):
        pending = [_entry(id_="evdiff_1")]
        sl.record_rejection(sup._issue_for(pending[0]), slug="proj-a")
        kept_a, _ = sup.filter_rejected(pending, slug="proj-a")
        kept_b, _ = sup.filter_rejected(pending, slug="proj-b")
        assert kept_a == []
        assert kept_b == pending

    def test_order_preserved(self):
        pending = [_entry(id_=f"evdiff_{i}") for i in range(5)]
        sl.record_rejection(sup._issue_for(pending[2]), slug="proj")
        kept, _ = sup.filter_rejected(pending, slug="proj")
        assert [e["id"] for e in kept] == ["evdiff_0", "evdiff_1", "evdiff_3", "evdiff_4"]

    # ── fail-open: 3境界 ──────────────────────────────────────────────

    def test_ledger_read_failure_keeps_all_fail_open(self, tmp_path, monkeypatch):
        """境界①: load_ledger() 自体が失敗したら全件そのまま通す（lane 全体を落とさない）。"""

        def _boom(_slug):
            raise OSError("disk full")

        monkeypatch.setattr(sl, "load_ledger", _boom)
        pending = [_entry(id_="evdiff_1"), _entry(id_="evdiff_2")]
        kept, stats = sup.filter_rejected(pending, slug="proj")
        assert kept == pending
        assert stats["ledger_read_error"] == "OSError: disk full"
        assert stats["suppressed_total"] == 0

    def test_ledger_read_failure_with_unlisted_exception_type_still_fail_opens(
        self, monkeypatch
    ):
        """[Must]1: OSError/UnicodeDecodeError 以外の例外でも境界①は fail-open する。

        load_ledger() は壊れた1行が「非 object の有効 JSON」（例: `[]`/`"x"`/`3`）だと
        `rec.get("dedup_key")` で AttributeError を出す（json.loads 自体は通過するので
        JSONDecodeError には落ちない）。列挙型の except では捕捉漏れになり emit 全体が
        落ちるため、広く Exception で受ける契約を固定する。
        """

        def _boom(_slug):
            raise AttributeError("'list' object has no attribute 'get'")

        monkeypatch.setattr(sl, "load_ledger", _boom)
        pending = [_entry(id_="evdiff_1")]
        kept, stats = sup.filter_rejected(pending, slug="proj")
        assert kept == pending
        assert stats["ledger_read_error"].startswith("AttributeError")

    def test_ledger_with_non_object_json_line_does_not_crash_filter_rejected(self):
        """[Must]1 の実シナリオ再現: ledger ファイルに非 object の有効 JSON 行（`[]`）が
        混ざっていても `filter_rejected` は例外を投げず全件通す（`load_ledger()` 自体は
        壊れた行を静かにスキップする契約なので、通常はここまで到達しないが、
        実装が広く Exception を受けていることの回帰防止として直接 load_ledger を模す）。
        """
        ledger_path = sl.ledger_path("proj")
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text("[]\n", encoding="utf-8")
        pending = [_entry(id_="evdiff_1")]
        # load_ledger() 自体は非 object 行を rec.get() で落ちる前に扱う実装差分がありうる
        # ため、ここでは filter_rejected の外形（例外を漏らさない）だけを固定する。
        kept, stats = sup.filter_rejected(pending, slug="proj")
        assert kept == pending or stats["ledger_read_error"] is not None

    def test_candidate_key_failure_keeps_only_that_candidate_fail_open(self, monkeypatch):
        """境界②: 1候補のキー計算だけ失敗しても他候補の判定は継続し、その候補は抑制しない。"""
        good = _entry(id_="evdiff_good")
        bad = _entry(id_="evdiff_bad")
        sl.record_rejection(sup._issue_for(good), slug="proj")  # good は本来 suppress 対象

        original = sup._issue_for

        def _flaky(entry):
            if entry.get("id") == "evdiff_bad":
                raise AttributeError("boom")
            return original(entry)

        monkeypatch.setattr(sup, "_issue_for", _flaky)
        kept, stats = sup.filter_rejected([good, bad], slug="proj")
        assert kept == [bad]  # good は正常に suppress、bad はキー計算失敗で fail-open
        assert stats["candidate_errors"] == [
            {"id": "evdiff_bad", "boundary": "candidate_key", "error": "AttributeError: boom"}
        ]

    def test_malformed_record_value_does_not_suppress_that_candidate(self):
        """境界③: decided_at/ttl_days が壊れた既存レコードはその候補だけ抑制しない。"""
        pending = [_entry(id_="evdiff_1")]
        issue = sup._issue_for(pending[0])
        key = sl.dedup_key(issue)
        ledger_path = sl.ledger_path("proj")
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(
            json.dumps({"dedup_key": key, "type": issue["type"], "file": issue["file"],
                        "decided_at": "not-a-number", "ttl_days": 45}) + "\n",
            encoding="utf-8",
        )
        kept, _ = sup.filter_rejected(pending, slug="proj")
        assert kept == pending  # 壊れたレコードは抑制しない側に倒す

    def test_malformed_record_value_surfaces_in_candidate_errors(self):
        """[Must]2(a): 境界③の失敗も stats["candidate_errors"] に boundary="record_value"
        として記録され、呼び出し元（_emit.py）まで meta が届く前提を固定する。"""
        pending = [_entry(id_="evdiff_1")]
        issue = sup._issue_for(pending[0])
        key = sl.dedup_key(issue)
        ledger_path = sl.ledger_path("proj")
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(
            json.dumps({"dedup_key": key, "type": issue["type"], "file": issue["file"],
                        "decided_at": "not-a-number", "ttl_days": 45}) + "\n",
            encoding="utf-8",
        )
        _, stats = sup.filter_rejected(pending, slug="proj")
        assert stats["candidate_errors"] == [
            {"id": "evdiff_1", "boundary": "record_value", "error": "ValueError: could not convert string to float: 'not-a-number'"}
        ]

    def test_advisory_merge_failure_does_not_affect_filter_rejected(self):
        """設計 §3.1-b「明示的に禁止する実装」: advisory 収集の失敗と filter_rejected の
        失敗は独立（同じ except に混ぜない）契約自体はここでは呼び出し元 _emit.py が担保する。
        本テストは filter_rejected 単体が advisory 有無に関わらず同じ契約で動くことを固定する。
        """
        pending = [_entry(id_="evdiff_1", proposal_type="skill_diff"),
                   _entry(id_="adv_1", proposal_type="advisory")]
        sl.record_rejection(sup._issue_for(pending[1]), slug="proj")
        kept, stats = sup.filter_rejected(pending, slug="proj")
        assert [e["id"] for e in kept] == ["evdiff_1"]
        assert stats["suppressed_total"] == 1


# ── record_pending_rejection: 記録・fail-open・now 注入 ────────────────────


class TestRecordPendingRejection:
    def test_records_and_is_then_suppressed(self):
        entry = _entry(id_="evdiff_1")
        err = sup.record_pending_rejection(entry, slug="proj")
        assert err is None
        kept, _ = sup.filter_rejected([entry], slug="proj")
        assert kept == []

    def test_advisory_lane_reject_is_recorded(self):
        """advisory レーンの reject も同じ ledger に記録される（[Must]3 の合流点配線を
        _suppression 単体で先取り検証。呼び出し側の配線は test_evolve_decisions.py 側）。"""
        entry = _entry(id_="adv_1", proposal_type="advisory")
        err = sup.record_pending_rejection(entry, slug="proj")
        assert err is None
        kept, _ = sup.filter_rejected([entry], slug="proj")
        assert kept == []

    def test_now_injection_makes_ttl_deterministic(self):
        """[Should]1: now を注入して decided_at を固定でき、TTL 境界をテストで決定論にできる。"""
        entry = _entry(id_="evdiff_1")
        sup.record_pending_rejection(entry, slug="proj", now=1000.0)
        kept, _ = sup.filter_rejected([entry], slug="proj", now=1000.0 + 10 * 86400)
        assert kept == []
        kept, _ = sup.filter_rejected([entry], slug="proj", now=1000.0 + 46 * 86400)
        assert kept == [entry]

    def test_write_failure_returns_error_string_not_exception(self, monkeypatch):
        """呼び出し側フロー（_ingest.py の判断記録・キュー消化）を絶対に止めない契約。
        record_rejection が例外を投げても record_pending_rejection は例外を外に出さない。"""

        def _boom(*_a, **_kw):
            raise OSError("disk full")

        monkeypatch.setattr(sl, "record_rejection", _boom)
        entry = _entry(id_="evdiff_1")
        err = sup.record_pending_rejection(entry, slug="proj")
        assert err == "OSError: disk full"

    def test_write_failure_with_unlisted_exception_type_still_returns_string(
        self, monkeypatch
    ):
        """[Must]3: 列挙型 except の裏を突く未列挙例外（RuntimeError 等）でも
        record_pending_rejection は例外を外に漏らさない（ingest とキュー消化を止めない）。
        record_rejection の先には pj_slug 解決（subprocess）や store barrier があり、
        列挙で想定していない例外が出うる。"""

        def _boom(*_a, **_kw):
            raise RuntimeError("store barrier rejected write")

        monkeypatch.setattr(sl, "record_rejection", _boom)
        entry = _entry(id_="evdiff_1")
        err = sup.record_pending_rejection(entry, slug="proj")
        assert err == "RuntimeError: store barrier rejected write"

    def test_issue_for_failure_inside_record_does_not_raise(self, monkeypatch):
        """[Must]2: _issue_for() 呼び出し自体も try の内側（entry が想定外でも落ちない）。"""

        def _boom(_entry):
            raise KeyError("id")

        monkeypatch.setattr(sup, "_issue_for", _boom)
        err = sup.record_pending_rejection(_entry(id_="evdiff_1"), slug="proj")
        assert err == "KeyError: 'id'"
