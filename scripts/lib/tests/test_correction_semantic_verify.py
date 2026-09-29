"""correction_semantic.verify（2段目・確かめ直し）のユニットテスト（#682・LLM 非呼出）。

LLM 呼び出しは ``call_fn`` DI で完全に mock する（no-llm-in-tests）。実プロダクトの
呼び出し集約点 ``judge_runner.call_haiku_verify`` は本ファイルでは一度も呼ばれない。

設計書 §5 のテスト1〜6・9 と陽性対照に対応する。
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import prompt as cs_prompt  # noqa: E402
from correction_semantic import verify  # noqa: E402


def _utt(text: str, prior: Optional[str] = "直前のClaudeの発言です") -> Dict[str, Any]:
    return {"text": text, "prior_assistant_text": prior}


def _v(index: int, is_correction: bool, *, idiom: str = "IDIOM_X", reason: str = "REASON_X",
       category: str = "omission") -> Dict[str, Any]:
    return {
        "index": index,
        "is_correction": is_correction,
        "idiom": idiom if is_correction else None,
        "reason": reason if is_correction else "",
        "category": category if is_correction else None,
    }


def _resp(pairs: List[tuple]) -> str:
    return json.dumps(
        {"verdicts": [{"index": i, "verdict": verdict} for i, verdict in pairs]}
    )


class _Spy:
    """call_fn の呼び出し記録（プロンプトと呼び出し順）。"""

    def __init__(self, responder):
        self.prompts: List[str] = []
        self._responder = responder

    def __call__(self, prompt: str, model: str) -> str:
        self.prompts.append(prompt)
        return self._responder(prompt, len(self.prompts) - 1)


def _count_items(prompt: str) -> int:
    """2段目プロンプトに載った対象件数（`[N] 直前のClaudeの発言:` 行の数）。"""
    return prompt.count("直前のClaudeの発言:") - prompt.split("判定対象:")[0].count("直前のClaudeの発言:")


# ─────────────────────────────────────────────────────────────────
# 正常系 E2E: 1段目陽性 → 2段目 reject で最終陰性 / keep で維持
# ─────────────────────────────────────────────────────────────────
def test_e2e_reject_flips_positive_to_negative_and_keep_preserves():
    groups = {"k": [_utt("新しい依頼A"), _utt("直前の成果物を直して"), _utt("ただの相槌")]}
    stage1 = {"k": [_v(0, True), _v(1, True), _v(2, False)]}
    spy = _Spy(lambda p, n: _resp([(0, "reject"), (1, "keep")]))
    res = verify.apply_verification(groups, stage1, call_fn=spy)

    final = res["verdicts"]["k"]
    assert final[0]["is_correction"] is False and final[0]["idiom"] is None and final[0]["category"] is None
    assert final[1] == stage1["k"][1]  # keep: 1段目のまま
    assert final[2] == stage1["k"][2]  # 陰性は不変
    assert (res["calls"], res["rejected"], res["kept"], res["failed"], res["no_context"]) == (1, 1, 1, 0, 0)
    # 入力（1段目 verdict）を破壊しない
    assert stage1["k"][0]["is_correction"] is True


def test_only_stage1_positives_are_sent_and_no_positive_means_no_call():
    groups = {"k": [_utt("a"), _utt("b"), _utt("c")]}
    spy = _Spy(lambda p, n: _resp([(0, "keep")]))
    res = verify.apply_verification(groups, {"k": [_v(0, False), _v(1, True), _v(2, False)]}, call_fn=spy)
    assert len(spy.prompts) == 1
    assert _count_items(spy.prompts[0]) == 1
    assert "b" in spy.prompts[0]

    spy0 = _Spy(lambda p, n: pytest.fail("陽性0件なのに呼ばれた"))
    res0 = verify.apply_verification(groups, {"k": [_v(i, False) for i in range(3)]}, call_fn=spy0)
    assert res0["calls"] == 0 and res0["verdicts"] == {}
    assert res["calls"] == 1


def test_positives_are_aggregated_across_stage1_batches_and_rechunked():
    groups = {"k1": [_utt("a1"), _utt("a2")], "k2": [_utt("b1"), _utt("b2")]}
    stage1 = {"k1": [_v(0, True), _v(1, False)], "k2": [_v(0, True), _v(1, True)]}
    spy = _Spy(lambda p, n: _resp([(i, "reject") for i in range(_count_items(p))]))
    res = verify.apply_verification(groups, stage1, call_fn=spy, batch_size=10)
    assert res["calls"] == 1  # 3件の陽性は1バッチに集約
    assert res["rejected"] == 3
    assert res["verdicts"]["k1"][1] == stage1["k1"][1]  # 陰性は不変
    assert [d["is_correction"] for d in res["verdicts"]["k2"]] == [False, False]


# ─────────────────────────────────────────────────────────────────
# 2段目は陽性→陰性にしか動かせない（陽性対照つき）
# ─────────────────────────────────────────────────────────────────
def test_negative_rows_stay_negative_whatever_stage2_answers():
    """1段目陰性は2段目に送られず、応答（範囲外 index・keep の羅列）でも陽性化しない。"""
    groups = {"k": [_utt("n0"), _utt("p1"), _utt("n2")]}
    stage1 = {"k": [_v(0, False), _v(1, True), _v(2, False)]}
    # 送られるのは1件だけだが、応答は index 0,1,2 全部 keep（範囲外を含む）
    spy = _Spy(lambda p, n: _resp([(0, "keep"), (1, "keep"), (2, "keep")]))
    res = verify.apply_verification(groups, stage1, call_fn=spy)
    final = res["verdicts"].get("k", stage1["k"])
    assert [d["is_correction"] for d in final] == [False, True, False]


def test_all_keep_leaves_result_identical_to_stage1():
    groups = {"k": [_utt("a"), _utt("b")]}
    stage1 = {"k": [_v(0, True), _v(1, True)]}
    spy = _Spy(lambda p, n: _resp([(0, "keep"), (1, "keep")]))
    res = verify.apply_verification(groups, stage1, call_fn=spy)
    assert res["verdicts"] == {}  # 書き換え無し（呼び出し側は元の応答をそのまま使う）
    assert res["kept"] == 2 and res["rejected"] == 0


@pytest.mark.parametrize("seed", range(8))
def test_property_final_is_never_more_positive_than_stage1(seed):
    """ランダムな1段目/2段目応答の組合せで is_correction が False→True に動かない。"""
    rng = random.Random(seed)
    n = 7
    groups = {"k": [_utt(f"t{i}", prior=rng.choice(["p", None])) for i in range(n)]}
    stage1 = {"k": [_v(i, rng.random() < 0.5) for i in range(n)]}

    def responder(prompt, _n):
        cnt = _count_items(prompt)
        return _resp([(i, rng.choice(["keep", "reject"])) for i in range(cnt + 2)])

    res = verify.apply_verification(groups, stage1, call_fn=_Spy(responder), batch_size=3)
    final = res["verdicts"].get("k", stage1["k"])
    for before, after in zip(stage1["k"], final):
        assert not (after["is_correction"] and not before["is_correction"])
        if after["is_correction"]:
            assert after == before


def test_rewrite_targets_verdict_by_index_not_list_position():
    """1段目 verdicts が index 順・全件でなくても、書き換えは verdict["index"] に従う。"""
    groups = {"k": [_utt("g0"), _utt("g1"), _utt("g2")]}
    # 並びが逆順・index 1 は欠落（部分応答）
    stage1 = {"k": [_v(2, True, idiom="IDIOM_2"), _v(0, True, idiom="IDIOM_0")]}
    spy = _Spy(lambda p, n: _resp([(0, "reject"), (1, "keep")]))  # 送信順 = 出現順(2→0)
    res = verify.apply_verification(groups, stage1, call_fn=spy)
    by_index = {d["index"]: d for d in res["verdicts"]["k"]}
    assert by_index[2]["is_correction"] is False   # 先頭に送られた g2 が reject
    assert by_index[0]["is_correction"] is True    # g0 は keep
    assert by_index[0]["idiom"] == "IDIOM_0"


# ─────────────────────────────────────────────────────────────────
# 失敗・文脈欠落は 1段目を残す（黙って消さない・理由を件数で記録）
# ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "responder",
    [
        lambda p, n: (_ for _ in ()).throw(RuntimeError("boom")),   # 呼び出し例外
        lambda p, n: "これは JSON ではない",                          # パース不能
        lambda p, n: "",                                              # 空応答
        lambda p, n: json.dumps({"verdicts": "x"}),                   # 構造違い
        lambda p, n: _resp([(1, "reject")]),                          # 該当 index 欠落
        lambda p, n: json.dumps({"verdicts": [{"index": 0, "verdict": "maybe"}]}),  # 不正値
    ],
)
def test_stage2_failure_keeps_stage1_positive_and_records_reason(responder):
    groups = {"k": [_utt("a")]}
    stage1 = {"k": [_v(0, True)]}
    res = verify.apply_verification(groups, stage1, call_fn=_Spy(responder))
    final = res["verdicts"].get("k", stage1["k"])
    assert final[0]["is_correction"] is True
    assert res["failed"] == 1 and res["rejected"] == 0


def test_missing_prior_text_skips_call_keeps_positive_and_records_no_context():
    groups = {"k": [_utt("a", prior=None), _utt("b", prior="")]}
    stage1 = {"k": [_v(0, True), _v(1, True)]}
    spy = _Spy(lambda p, n: pytest.fail("直前発言が無いのに2段目を呼んだ"))
    res = verify.apply_verification(groups, stage1, call_fn=spy)
    assert res["calls"] == 0 and res["no_context"] == 2
    assert res["verdicts"] == {}


def test_prior_fn_exception_is_treated_as_no_context_not_crash():
    groups = {"k": [_utt("a")]}

    def boom(_u):
        raise ValueError("bad transcript")

    res = verify.apply_verification(
        groups, {"k": [_v(0, True)]}, prior_fn=boom,
        call_fn=_Spy(lambda p, n: pytest.fail("呼ばれない")),
    )
    assert res["no_context"] == 1 and res["calls"] == 0


def test_partial_failure_only_affects_failed_chunk():
    groups = {"k": [_utt("a"), _utt("b")]}
    stage1 = {"k": [_v(0, True), _v(1, True)]}

    def responder(prompt, n):
        if n == 0:
            raise RuntimeError("first chunk fails")
        return _resp([(0, "reject")])

    res = verify.apply_verification(groups, stage1, call_fn=_Spy(responder), batch_size=1)
    final = res["verdicts"]["k"]
    assert final[0]["is_correction"] is True    # 失敗した chunk の陽性は残る
    assert final[1]["is_correction"] is False   # 成功した chunk の reject は反映
    assert (res["failed"], res["rejected"]) == (1, 1)


# ─────────────────────────────────────────────────────────────────
# 2段目に1段目の理由・分類を渡さない
# ─────────────────────────────────────────────────────────────────
def test_stage2_prompt_excludes_stage1_reason_idiom_category():
    groups = {"k": [_utt("何か依頼")]}
    stage1 = {"k": [_v(0, True, idiom="SECRET_IDIOM", reason="SECRET_REASON", category="omission")]}
    spy = _Spy(lambda p, n: _resp([(0, "keep")]))
    verify.apply_verification(groups, stage1, call_fn=spy)
    p = spy.prompts[0]
    assert "SECRET_IDIOM" not in p and "SECRET_REASON" not in p
    assert "omission" not in p
    assert "直前のClaudeの発言です" in p and "何か依頼" in p  # 渡すもの


# ─────────────────────────────────────────────────────────────────
# 費用の事前予約（呼び出しより前）
# ─────────────────────────────────────────────────────────────────
def test_cost_is_reserved_before_each_call():
    events: List[str] = []
    groups = {"k": [_utt("a"), _utt("b")]}
    stage1 = {"k": [_v(0, True), _v(1, True)]}

    def responder(p, n):
        events.append(f"call{n}")
        return _resp([(0, "keep")])

    spy = _Spy(responder)
    verify.apply_verification(
        groups, stage1, call_fn=spy,
        reserve_fn=lambda items: events.append(f"reserve{len(events) // 2}:{len(items)}"),
        batch_size=1,
    )
    assert events == ["reserve0:1", "call0", "reserve1:1", "call1"]


def test_reserve_failure_blocks_call_and_keeps_stage1():
    """予約を記録できないときは呼ばない（予算の歯止めを外さない）。1段目は残す。"""
    groups = {"k": [_utt("a")]}

    def bad_reserve(_items):
        raise OSError("disk full")

    res = verify.apply_verification(
        groups, {"k": [_v(0, True)]}, call_fn=_Spy(lambda p, n: pytest.fail("予約前に呼んだ")),
        reserve_fn=bad_reserve,
    )
    assert res["calls"] == 0 and res["failed"] == 1 and res["verdicts"] == {}


def test_reserve_verify_cost_appends_est_tokens_record_without_key(tmp_path):
    from correction_semantic import store as cs_store

    judged = tmp_path / "correction_judged.jsonl"
    items = [{"text": "abc", "prior_assistant_text": "def"}]
    out = verify.reserve_verify_cost(items, judged_path=judged)
    assert out["written"] == 1
    lines = [json.loads(l) for l in judged.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1 and "key" not in lines[0] and lines[0]["est_tokens"] > 0
    assert cs_store.read_judged_keys(judged) == set()  # 発話を「判定済み」にはしない


# ─────────────────────────────────────────────────────────────────
# 直前発言の取得（評価=凍結値 / 本番=元ログ直読み）
# ─────────────────────────────────────────────────────────────────
def _write_transcript(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def test_fetch_prior_assistant_text_matches_eval_set_shape(tmp_path):
    p = tmp_path / "s.jsonl"
    long_text = "あ" * 500
    _write_transcript(p, [
        {"type": "user", "message": {"content": "最初"}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": long_text},
            {"type": "tool_use", "name": "Bash"},
        ]}},
        {"type": "attachment"},  # メタデータ行が挟まっても遡る
        {"type": "user", "message": {"content": "これを直して"}},
    ])
    got = verify.fetch_prior_assistant_text(str(p), 4)
    assert got == ("あ" * 400)  # 先頭400字
    _write_transcript(p, [
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "hello"}, {"type": "tool_use", "name": "Bash"}]}},
        {"type": "user", "message": {"content": "x"}},
    ])
    assert verify.fetch_prior_assistant_text(str(p), 2) == "hello [tool_use:Bash]"
    assert verify.fetch_prior_assistant_text(str(tmp_path / "none.jsonl"), 2) is None


def test_fetch_prior_assistant_text_tolerates_non_dict_lines(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text('[1,2]\n"str"\n{"type":"user","message":{"content":"x"}}\n', encoding="utf-8")
    assert verify.fetch_prior_assistant_text(str(p), 3) is None


def test_default_prior_fn_uses_frozen_value_for_eval_rows_and_fetches_for_production(tmp_path):
    # 評価行: キーがあれば（None でも）凍結値を使い、元ログは読まない
    assert verify._default_prior_fn({"text": "x", "prior_assistant_text": "凍結"}) == "凍結"
    assert verify._default_prior_fn(
        {"text": "x", "prior_assistant_text": None, "source_path": "/nonexistent", "line_no": 3}
    ) is None
    # 本番: キー無し → source_path/line_no から取得
    p = tmp_path / "s.jsonl"
    _write_transcript(p, [
        {"type": "assistant", "message": {"content": "元ログの発言"}},
        {"type": "user", "message": {"content": "x"}},
    ])
    assert verify._default_prior_fn({"text": "x", "source_path": str(p), "line_no": 2}) == "元ログの発言"
    assert verify._default_prior_fn({"text": "x"}) is None


def test_stage2_prompt_is_built_by_prompt_module_single_source():
    """見積もり用の行整形と実送信が同じ関数を通る（単一ソース）。"""
    items = [{"text": "本文", "prior_assistant_text": "P" * 999}]
    line = cs_prompt.format_verify_line(0, items[0])
    assert line in cs_prompt.build_verify_prompt(items)
    assert "P" * 401 not in line  # 400字上限
