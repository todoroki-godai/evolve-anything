"""rl_common.detection.strip_image_placeholders のテスト（#445）。

corrections ストア入力衛生: Claude Code CLI が画像添付時に text block へ自動挿入する
``[Image #N]`` 位置マーカーを strip する単一ソース関数。utterance_archive.extractor
（upstream）と corrections 書込パスの両方がこれを共有する。決定論・LLM 非依存。

``_IMAGE_PLACEHOLDER_CASES``（#445 codex round1 [Should]2）は実コーパス
（corrections.jsonl の `[Image` 開始 37 件全件を目視レビュー）で観測した形式の
バリエーションのケース表。内容（PJ 固有の指摘文）は個人情報・PJ 固有情報のため
repo に含められず無害な合成テキストへ置換しているが、マーカーの位置・区切り・
繰り返しパターンは実測どおり保持する（37 件全件そのままの fixture 化はしない）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from rl_common.detection import strip_image_placeholders  # noqa: E402


def test_strips_leading_marker_same_line():
    assert strip_image_placeholders("[Image #1] Codeタブってないよ") == "Codeタブってないよ"


def test_strips_leading_marker_newline_separated():
    assert strip_image_placeholders("[Image #1]\n\n本文") == "本文"


def test_strips_multi_digit_marker():
    # #12 のような複数桁の番号にも一致する。
    assert strip_image_placeholders("[Image #12] これも") == "これも"


def test_strips_multiple_markers():
    out = strip_image_placeholders("[Image #1] [Image #2] [Image #3] 本文")
    assert "[Image" not in out
    assert "本文" in out


def test_marker_only_returns_empty():
    assert strip_image_placeholders("[Image #1]") == ""
    assert strip_image_placeholders("[Image #1]\n\n") == ""


def test_does_not_mangle_similar_but_non_matching_text():
    # "#N]" 形式に一致しない文字列は誤って触らない（過剰マッチ防止）。
    text = "[Image processing failed] というエラーが出た"
    assert strip_image_placeholders(text) == text


def test_does_not_mangle_meta_reference_to_marker_without_number():
    # マーカーへの言及だが "#数字]" が無い（曖昧な参照）は触らない。
    text = "[Image で始まる行を除外して"
    assert strip_image_placeholders(text) == text


def test_empty_and_none_passthrough():
    assert strip_image_placeholders("") == ""
    assert strip_image_placeholders(None) is None


# ── #445 codex round1 [Should]1: 判定不能な意図的言及との区別は原理的に不可能 ────
#
# transcript には「添付そのもの（CLI 自動挿入）」と「マーカー文字列への意図的な言及
# （例: "[Image #3] のスクショの話だけど"）」を区別する構造情報が無い（どちらも同じ
# text block 内の文字列としてしか観測できない）。実コーパス実測（corrections.jsonl の
# `[Image` 開始 37 件全件を目視）では **全件が CLI 自動挿入のマーカーであり、意図的な
# 言及は 0 件**だったため、区別不能な場合は strip 側に倒す設計判断をした
# （`learning_synthetic_fixture_false_confidence` と同型のリスクを避けるため、実コーパスに
# 無い形式に過学習した位置・区切りルールを追加しない。CLI がマーカー形式を変えても黙って
# 壊れない全体置換のままにする）。以下は **現実装の挙動をそのまま固定する**負例テスト。


def test_intentional_meta_reference_is_also_stripped_known_tradeoff():
    # 「[Image #3] のスクショの話だけど」のような意図的な言及も、構造的に区別できず
    # マーカーとして strip される（直感に反するが上記の理由で許容する既知のトレードオフ）。
    text = "[Image #3] のスクショの話だけど"
    assert strip_image_placeholders(text) == "のスクショの話だけど"


def test_marker_without_space_between_image_and_hash_is_also_stripped():
    # "[Image#3]"（Image と # の間に空白なし）も実形式ではないが、``\s*`` により
    # マーカーとして扱う（意図的な設計。実コーパスに現れない形式のために正規表現を
    # 狭めると、実コーパスに存在する空白ありの実形式まで壊すリスクの方が高い）。
    text = "[Image#3] これも消える"
    assert strip_image_placeholders(text) == "これも消える"


# ── #445 codex round1 [Should]2: 「37/37 が救済される」設計根拠のケース表 ──────
#
# corrections.jsonl の `[Image` 開始 37 件全件を目視レビューし（#445 実装完了報告に
# 記載）、そこに現れた**形式のバリエーション**を洗い出した。内容（PJ 固有の指摘文・
# 個人が特定されうる文脈）は repo に入れられないため無害な合成テキストに置換しているが、
# **マーカーの位置・区切り・繰り返しパターンは実データのものをそのまま保つ**（内容は
# 捏造だが形式は実測）。各ケースで「strip 後が非空（＝救済される）」ことを固定する。
_IMAGE_PLACEHOLDER_CASES = [
    # (ケース名, 入力, strip 後に含まれるべき文字列)
    ("single_marker_same_line", "[Image #1] ボタンの色がおかしい", "ボタンの色がおかしい"),
    ("single_marker_blank_line_separated",
     "[Image #1]\n\nまた誤検知が出ている、、、原因を調べて",
     "また誤検知が出ている、、、原因を調べて"),
    ("multiple_consecutive_markers_same_line",
     "[Image #2] [Image #3] [Image #4] こんな感じの見た目になってる",
     "こんな感じの見た目になってる"),
    ("marker_at_start_and_again_mid_text",
     "[Image #1] このへん直せる？\n\n[Image #2] [Image #3] こっちも同じ問題",
     "こっちも同じ問題"),
    ("multi_line_body_after_marker",
     "[Image #3] 見た目が崩れてるんだよね、、、\n一度全体を作り直したい。\n\nあと、別の話だけど処理が長い",
     "あと、別の話だけど処理が長い"),
    ("double_digit_number", "[Image #12] これも直して", "これも直して"),
]


def test_all_observed_image_placeholder_formats_are_rescued():
    """実コーパスで観測した37件の形式バリエーション全てで strip 後が非空になる。

    「37/37 が救済される」という #445 の設計根拠（全文除外でなく strip を選んだ理由）を
    回帰から守る契約テスト。
    """
    for name, text, expected_fragment in _IMAGE_PLACEHOLDER_CASES:
        out = strip_image_placeholders(text)
        assert out, f"case={name}: strip 後が空になってはいけない（救済失敗）"
        assert "[Image" not in out, f"case={name}: マーカーが残存している"
        assert expected_fragment in out, f"case={name}: 期待テキストが失われている"
