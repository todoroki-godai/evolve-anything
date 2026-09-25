"""correction_semantic.prompt のテスト（#431 バッチプロンプト + verdict パース）。

プロンプト組み立てとモデル応答(JSON)のパースを検証する。LLM は呼ばない（文字列のみ）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import prompt as cs_prompt  # noqa: E402


def _utts():
    return [
        {"source_path": "/a.jsonl", "line_no": 1,
         "text": "つむぎにしてほしい、四国めたんじゃなくて", "prev_action": "Edit"},
        {"source_path": "/a.jsonl", "line_no": 2,
         "text": "ありがとう、それで完璧", "prev_action": None},
    ]


def test_build_prompt_contains_all_utterances() -> None:
    p = cs_prompt.build_batch_prompt(_utts())
    assert "四国めたん" in p
    assert "ありがとう" in p
    # 各発話に判定用の index 番号が振られている
    assert "0" in p and "1" in p


def test_build_prompt_asks_for_structured_verdict_result() -> None:
    p = cs_prompt.build_batch_prompt(_utts())
    assert "以下の形式で判定結果を返してください:" in p
    assert '"verdicts"' in p
    # 二値 + 言い回し抽出を要求
    assert "is_correction" in p
    assert "idiom" in p


def _assert_advisory_boundary_contract(prompt: str) -> None:
    """Advisory: known wording only; semantic paraphrases can evade this check."""
    normalized = " ".join(prompt.split())
    clauses = (
        "まず、対象が Claude の既存の成果物・方針・進め方かを確認する。",
        "次に、その対象の変更・不足の解消・制約や約束事の追加を求めているかを確認する。",
        "最後に、修正の要求が引用部分だけでなくユーザー自身の発話にあるかを確認する。",
        "発話が既存の成果物や作業の特定の不備・未完了を指し示している疑問形は修正に含める。状態・進捗・可否を尋ねるだけの質問は含めない。",
        "特定の欠落・不足を指し示す婉曲な発話も修正に含める。",
        "Claude の誤りの指摘に加え、既に出した成果物・方針・進め方を変えさせる要求も修正に含める。",
        "その作業1回に限る条件は含めない。以後の作業にも続けて適用させる約束事は含める。",
        "新しい情報を求める質問は修正に含めない。",
        "既存の成果物・方針に向かわない相談・提案は修正に含めない。",
        "新しい作業の依頼（「次これやって」）と、その作業の初期条件の指定は修正に含めない。",
    )
    positions = []
    for clause in clauses:
        assert normalized.count(clause) == 1, clause
        positions.append(normalized.index(clause))
    assert positions == sorted(positions)


def test_advisory_known_wording_only_boundary_rules_and_order() -> None:
    """Advisory: known wording only; this is not a semantic judge test."""
    _assert_advisory_boundary_contract(cs_prompt.build_batch_prompt([]))


_M1_CLAUSE = "発話が既存の成果物や作業の特定の不備・未完了を指し示している疑問形は修正に含める。状態・進捗・可否を尋ねるだけの質問は含めない。"
_SHOULD_CLAUSE = "その作業1回に限る条件は含めない。以後の作業にも続けて適用させる約束事は含める。"


@pytest.mark.parametrize(
    "clause",
    [
        _M1_CLAUSE,
        "状態・進捗・可否を尋ねるだけの質問は含めない。",
        _SHOULD_CLAUSE,
        "以後の作業にも続けて適用させる約束事は含める。",
    ],
)
def test_advisory_new_boundary_deletion_is_rejected(clause: str) -> None:
    """Advisory: known wording only; deleting either new clause must fail."""
    prompt = cs_prompt.build_batch_prompt([])
    with pytest.raises(AssertionError):
        _assert_advisory_boundary_contract(prompt.replace(clause, "", 1))


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("状態・進捗・可否を尋ねるだけの質問は含めない。", "状態・進捗・可否を尋ねるだけの質問は含める。"),
        ("以後の作業にも続けて適用させる約束事は含める。", "以後の作業にも続けて適用させる約束事は含めない。"),
    ],
)
def test_advisory_new_boundary_inversion_is_rejected(before: str, after: str) -> None:
    """Advisory: known wording only; opposite meanings must fail."""
    prompt = cs_prompt.build_batch_prompt([])
    with pytest.raises(AssertionError):
        _assert_advisory_boundary_contract(prompt.replace(before, after, 1))


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (_M1_CLAUSE, "特定の欠落・不足を指し示す婉曲な発話も修正に含める。"),
        (_SHOULD_CLAUSE, "- 新しい情報を求める質問は修正に含めない。"),
    ],
)
def test_advisory_new_boundary_order_swap_is_rejected(first: str, second: str) -> None:
    """Advisory: known wording only; swapping either new clause must fail."""
    prompt = cs_prompt.build_batch_prompt([])
    swapped = prompt.replace(first, "__FIRST__", 1).replace(
        second, first, 1
    ).replace("__FIRST__", second, 1)
    with pytest.raises(AssertionError):
        _assert_advisory_boundary_contract(swapped)


def test_advisory_exclusion_deletion_is_rejected() -> None:
    """Advisory: known wording only; deleting an exclusion must fail."""
    prompt = cs_prompt.build_batch_prompt([])
    with pytest.raises(AssertionError):
        _assert_advisory_boundary_contract(
            prompt.replace("- 新しい情報を求める質問は修正に含めない。", "", 1)
        )


def test_advisory_format_only_change_is_accepted() -> None:
    """Advisory: known wording only; whitespace changes preserve the contract."""
    prompt = cs_prompt.build_batch_prompt([])
    _assert_advisory_boundary_contract(prompt.replace("\n", "\n  "))


def test_advisory_duplicate_clause_is_rejected() -> None:
    """Advisory: known wording only; duplicating a rule must fail."""
    prompt = cs_prompt.build_batch_prompt([])
    with pytest.raises(AssertionError):
        _assert_advisory_boundary_contract(prompt + _M1_CLAUSE)


def test_advisory_exclusion_moved_before_inclusion_is_rejected() -> None:
    """Advisory: known wording only; moving an exclusion ahead of rules must fail."""
    prompt = cs_prompt.build_batch_prompt([])
    exclusion = "- 新しい情報を求める質問は修正に含めない。"
    moved = prompt.replace(exclusion, "", 1).replace(
        _M1_CLAUSE, exclusion + "\n" + _M1_CLAUSE, 1
    )
    with pytest.raises(AssertionError):
        _assert_advisory_boundary_contract(moved)


# ── #400 A5: category（対象軸 8値 enum）────────────────────────────


def test_build_prompt_contains_category_vocabulary_and_priority_rules() -> None:
    """設計 §2.1: 8カテゴリの語彙表 + 境界優先規則をプロンプトに明記する。"""
    p = cs_prompt.build_batch_prompt(_utts())
    for label in cs_prompt.CATEGORY_ENUM:
        assert label in p
    assert "category" in p
    # 境界優先規則（最頻の揺れとして名指しされた presentation/explanation）
    assert "presentation" in p and "explanation" in p


def test_category_enum_has_eight_labels() -> None:
    assert len(cs_prompt.CATEGORY_ENUM) == 8
    assert set(cs_prompt.CATEGORY_ENUM) == {
        "presentation", "explanation", "factual", "approach",
        "omission", "excess", "process", "other",
    }


def test_prompt_fingerprint_changes_with_template() -> None:
    """設計 §2.4/§2.5: category は producer 時点の測定値。プロンプトが変われば
    fingerprint も変わり、系列断絶を検出できる（utterances 依存部分は対象外）。
    """
    fp1 = cs_prompt.prompt_fingerprint()
    fp2 = cs_prompt.prompt_fingerprint()
    assert fp1 == fp2  # 決定論（同一プロセス内で安定）
    # 発話内容を変えても fingerprint は変わらない（固定テンプレート部分のみ対象）
    assert cs_prompt.prompt_fingerprint() == fp1


def test_prompt_contract_version_and_fingerprint_for_schema_v4() -> None:
    """#682: 判定境界の変更後の系列識別値を固定する。"""
    assert cs_prompt.CATEGORY_SCHEMA_VERSION == 4
    assert cs_prompt.prompt_fingerprint() == "e6a3814e11e7"


@pytest.mark.parametrize(
    ("raw_category", "is_correction", "expected_category"),
    [
        ("not-in-category-enum", True, None),
        ("factual", True, "factual"),
        (None, False, None),
    ],
)
def test_validate_verdict_fail_open_is_independent_of_generation_schema(
    raw_category, is_correction, expected_category
) -> None:
    """#625: schema 違反相当でも受信側は category だけ未判定へ持ち越す。"""
    verdict = cs_prompt._validate_verdict(
        {
            "index": 0,
            "is_correction": is_correction,
            "idiom": "sentinel" if is_correction else None,
            "category": raw_category,
            "reason": "sentinel reason",
        }
    )
    assert verdict is not None
    assert verdict["category"] == expected_category


def test_parse_verdict_captures_valid_category() -> None:
    raw = json.dumps({"verdicts": [
        {"index": 0, "is_correction": True, "idiom": "x", "category": "factual", "reason": "y"},
    ]})
    result = cs_prompt.parse_verdicts_result(raw)
    assert result["ok"] is True
    assert result["verdicts"][0]["category"] == "factual"


def test_parse_verdict_forces_category_none_when_not_correction() -> None:
    """設計 §2.4: is_correction=false のとき category は必ず None（モデルが値を返しても無視）。"""
    raw = json.dumps({"verdicts": [
        {"index": 0, "is_correction": False, "idiom": None, "category": "factual", "reason": ""},
    ]})
    result = cs_prompt.parse_verdicts_result(raw)
    assert result["ok"] is True
    assert result["verdicts"][0]["category"] is None


def test_parse_verdict_normalizes_unknown_category_to_none_without_failing_batch() -> None:
    """設計 §2.4: enum 不正値は verdict 全体を落とさず category=None に正規化する。"""
    raw = json.dumps({"verdicts": [
        {"index": 0, "is_correction": True, "idiom": "x", "category": "not_a_real_category", "reason": "y"},
    ]})
    result = cs_prompt.parse_verdicts_result(raw)
    assert result["ok"] is True
    assert result["verdicts"][0]["category"] is None


def test_parse_verdict_normalizes_missing_category_to_none() -> None:
    """category キー自体が無い応答（旧プロンプト互換）でもバッチを失格にしない。"""
    raw = json.dumps({"verdicts": [
        {"index": 0, "is_correction": True, "idiom": "x", "reason": "y"},
    ]})
    result = cs_prompt.parse_verdicts_result(raw)
    assert result["ok"] is True
    assert result["verdicts"][0]["category"] is None


def test_parse_verdict_normalizes_wrong_type_category_to_none() -> None:
    raw = json.dumps({"verdicts": [
        {"index": 0, "is_correction": True, "idiom": "x", "category": 123, "reason": "y"},
    ]})
    result = cs_prompt.parse_verdicts_result(raw)
    assert result["ok"] is True
    assert result["verdicts"][0]["category"] is None


# ── #410 [Must]C: 極端に長い本文を切り詰める（青天井のトークン消費を防ぐ）──────


def test_build_prompt_truncates_long_utterance_text() -> None:
    from correction_semantic import MAX_CHARS_PER_UTTERANCE

    # "X" は雛形文（日本語の指示文・記号のみ）に出現しないマーカー文字として使う
    # （「あ」等の日本語文字だと雛形自身の地の文に紛れてカウントがずれる）。
    huge = [{"source_path": "/a.jsonl", "line_no": 1,
             "text": "X" * (MAX_CHARS_PER_UTTERANCE * 3), "prev_action": None}]
    p = cs_prompt.build_batch_prompt(huge)
    assert p.count("X") <= MAX_CHARS_PER_UTTERANCE


def test_build_prompt_short_utterance_unaffected_by_truncation() -> None:
    p = cs_prompt.build_batch_prompt(_utts())
    assert "四国めたん" in p
    assert "ありがとう" in p


# ── verdict パース ────────────────────────────────────────────────


def test_parse_verdicts_valid_json() -> None:
    raw = json.dumps({
        "verdicts": [
            {"index": 0, "is_correction": True, "idiom": "四国めたんじゃなくて",
             "reason": "正しい値の後置型"},
            {"index": 1, "is_correction": False, "idiom": None, "reason": ""},
        ]
    }, ensure_ascii=False)
    verdicts = cs_prompt.parse_verdicts(raw)
    assert len(verdicts) == 2
    assert verdicts[0]["is_correction"] is True
    assert verdicts[0]["idiom"] == "四国めたんじゃなくて"
    assert verdicts[1]["is_correction"] is False


def test_parse_verdicts_json_with_codefence() -> None:
    raw = "```json\n" + json.dumps({"verdicts": [
        {"index": 0, "is_correction": True, "idiom": "x", "reason": "y"}]}) + "\n```"
    verdicts = cs_prompt.parse_verdicts(raw)
    assert len(verdicts) == 1
    assert verdicts[0]["is_correction"] is True


def test_parse_verdicts_empty_on_garbage() -> None:
    assert cs_prompt.parse_verdicts("not json at all") == []
    assert cs_prompt.parse_verdicts("") == []
    assert cs_prompt.parse_verdicts(None) == []


def test_parse_verdicts_tolerates_missing_fields() -> None:
    raw = json.dumps({"verdicts": [{"index": 0, "is_correction": True}]})
    verdicts = cs_prompt.parse_verdicts(raw)
    assert verdicts[0]["idiom"] is None  # 欠落 idiom は None に正規化


# ── parse_verdicts_result: 空リストとパース失敗の区別（#273）────────────────


def test_parse_verdicts_result_ok_true_on_valid_json() -> None:
    raw = json.dumps({"verdicts": [
        {"index": 0, "is_correction": True, "idiom": "x", "reason": "y"},
    ]})
    result = cs_prompt.parse_verdicts_result(raw)
    assert result["ok"] is True
    assert len(result["verdicts"]) == 1


def test_parse_verdicts_result_ok_true_on_legitimate_empty_list() -> None:
    """正しい JSON で verdicts が空配列（モデルが「該当なし」と判定）は ok=True。"""
    raw = json.dumps({"verdicts": []})
    result = cs_prompt.parse_verdicts_result(raw)
    assert result["ok"] is True
    assert result["verdicts"] == []


def test_parse_verdicts_result_ok_false_on_malformed_json() -> None:
    """壊れた JSON はパース失敗（ok=False）。空リストと区別できないと #273 の非対称が再発する。"""
    result = cs_prompt.parse_verdicts_result("not json at all {{{")
    assert result["ok"] is False
    assert result["verdicts"] == []


def test_parse_verdicts_result_ok_false_on_missing_response() -> None:
    assert cs_prompt.parse_verdicts_result("")["ok"] is False
    assert cs_prompt.parse_verdicts_result(None)["ok"] is False


def test_parse_verdicts_result_ok_false_on_missing_verdicts_key() -> None:
    """"verdicts" キー自体が無い/リストでない応答は解釈不能として扱う。"""
    assert cs_prompt.parse_verdicts_result(json.dumps({"foo": "bar"}))["ok"] is False
    assert cs_prompt.parse_verdicts_result(json.dumps({"verdicts": "not a list"}))["ok"] is False


def test_parse_verdicts_backward_compat_delegates_to_result() -> None:
    """既存 API `parse_verdicts` は parse_verdicts_result の verdicts をそのまま返す（後方互換）。"""
    raw = json.dumps({"verdicts": [{"index": 0, "is_correction": True, "idiom": "x", "reason": "y"}]})
    assert cs_prompt.parse_verdicts(raw) == cs_prompt.parse_verdicts_result(raw)["verdicts"]


# ── P1-1（codex 指摘）: 意味的に壊れた要素の厳格検証 ────────────────────────


def test_parse_verdicts_result_ok_false_on_string_index() -> None:
    """index が文字列型（"0"）は不正 — 従来は黙って捨てられ ok=True になっていた。"""
    raw = json.dumps({"verdicts": [{"index": "0", "is_correction": True}]})
    result = cs_prompt.parse_verdicts_result(raw)
    assert result["ok"] is False
    assert result["verdicts"] == []


def test_parse_verdicts_result_ok_false_on_string_is_correction() -> None:
    """is_correction が文字列 "false" は bool("false")==True の罠を踏まず不正として弾く。"""
    raw = json.dumps({"verdicts": [{"index": 0, "is_correction": "false"}]})
    result = cs_prompt.parse_verdicts_result(raw)
    assert result["ok"] is False


def test_parse_verdicts_result_ok_false_on_duplicate_index() -> None:
    raw = json.dumps({"verdicts": [
        {"index": 0, "is_correction": True},
        {"index": 0, "is_correction": False},
    ]})
    assert cs_prompt.parse_verdicts_result(raw)["ok"] is False


def test_parse_verdicts_result_ok_false_on_one_invalid_among_valid() -> None:
    """1件でも不正要素があれば正しい要素も含めてバッチ全体を失格にする（部分採用しない）。"""
    raw = json.dumps({"verdicts": [
        {"index": 0, "is_correction": True, "idiom": "四国めたんじゃなくて", "reason": "後置型"},
        {"index": 1, "is_correction": "false"},  # 不正
    ]})
    result = cs_prompt.parse_verdicts_result(raw)
    assert result["ok"] is False
    assert result["verdicts"] == []


def test_parse_verdicts_result_ok_true_when_all_valid() -> None:
    """全要素が正しい型なら ok=True（回帰防止）。"""
    raw = json.dumps({"verdicts": [
        {"index": 0, "is_correction": True, "idiom": "x", "reason": "y"},
        {"index": 1, "is_correction": False, "idiom": None, "reason": ""},
    ]})
    result = cs_prompt.parse_verdicts_result(raw)
    assert result["ok"] is True
    assert len(result["verdicts"]) == 2


# ── #410 round2/round3 [Should]③⑤: 範囲外 index の扱い ──────────────────────
# round2: parser に応答対象の件数を渡し範囲外 index を検出する（黙って捨てない）。
# round3: 有効な全 index に加えて余分な1件を返しただけでバッチ全体を失格にするのは
# 過剰（[Must]2 の billed-but-unconfirmed 予算漏れと組み合わさると「無限再試行×予算漏れ」
# になる）との指摘を受け、**範囲外の要素だけを無視**し件数を surface する方式に変更した
# （round2 時点の「全体失格にする」という設計をこの節で上書きする）。


def test_parse_verdicts_result_ignores_out_of_range_index_but_keeps_valid_ones() -> None:
    """#410 round3 [Should]⑤: 範囲外 index は無視するだけで、範囲内の要素は通常どおり
    処理する（バッチ全体を失格にしない）。
    """
    raw = json.dumps({"verdicts": [
        {"index": 0, "is_correction": True},
        {"index": 99, "is_correction": True},  # バッチ対象外
    ]})
    result = cs_prompt.parse_verdicts_result(raw, expected_len=3)
    assert result["ok"] is True
    assert [v["index"] for v in result["verdicts"]] == [0]
    assert result["out_of_range"] == 1


def test_parse_verdicts_result_negative_index_is_ignored_not_failed() -> None:
    raw = json.dumps({"verdicts": [
        {"index": 0, "is_correction": True},
        {"index": -1, "is_correction": True},
    ]})
    result = cs_prompt.parse_verdicts_result(raw, expected_len=3)
    assert result["ok"] is True
    assert [v["index"] for v in result["verdicts"]] == [0]
    assert result["out_of_range"] == 1


def test_parse_verdicts_result_in_range_index_ok_with_expected_len() -> None:
    raw = json.dumps({"verdicts": [{"index": 2, "is_correction": True}]})
    result = cs_prompt.parse_verdicts_result(raw, expected_len=3)
    assert result["ok"] is True
    assert len(result["verdicts"]) == 1
    assert result["out_of_range"] == 0


def test_parse_verdicts_result_no_range_check_when_expected_len_omitted() -> None:
    """expected_len 未指定（既定 None）は後方互換のため範囲検証しない。"""
    raw = json.dumps({"verdicts": [{"index": 99, "is_correction": True}]})
    result = cs_prompt.parse_verdicts_result(raw)
    assert result["ok"] is True
    assert result["out_of_range"] == 0


def test_parse_verdicts_result_all_out_of_range_is_still_ok_with_empty_verdicts() -> None:
    """全件が範囲外なら verdicts=[] だが ok=True のまま（型不正・重複とは異なる軸）。"""
    raw = json.dumps({"verdicts": [{"index": 99, "is_correction": True}]})
    result = cs_prompt.parse_verdicts_result(raw, expected_len=3)
    assert result["ok"] is True
    assert result["verdicts"] == []
    assert result["out_of_range"] == 1
