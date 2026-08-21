"""correction_detect hooks のユニットテスト。"""
import json
import os
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common
import correction_detect
import rl_common


@pytest.fixture
def tmp_data_dir(tmp_path):
    data_dir = tmp_path / "evolve-anything"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def patch_data_dir(tmp_data_dir):
    # store_write は SoT の rl_common.DATA_DIR を call-time 参照する（ADR-049 / #55）。
    # 移行後 corrections の書込はゲート経由になるため、re-export コピーの
    # common.DATA_DIR に加えて SoT も同じ tmp に向ける（additive・挙動不変）。
    with mock.patch.object(common, "DATA_DIR", tmp_data_dir), \
         mock.patch.object(rl_common, "DATA_DIR", tmp_data_dir):
        yield tmp_data_dir


class TestDetectCorrection:
    """common.detect_correction() のテスト。"""

    def test_iya_pattern(self):
        result = common.detect_correction("いや、そうじゃなくて optimize を使って")
        assert result is not None
        assert result[0] == "iya"
        assert result[1] == 0.85

    def test_chigau_pattern(self):
        result = common.detect_correction("違う、そのアプローチではない")
        assert result is not None
        assert result[0] == "chigau"
        assert result[1] == 0.85

    def test_chigau_pattern_hiragana(self):
        """ひらがな「ちがう」も同義（#305 の実コーパス目視で未検出だった表記）。"""
        result = common.detect_correction("ちがうちがう、最新にしておいてってこと")
        assert result is not None
        assert result[0] == "chigau"
        assert result[1] == 0.85

    def test_chigau_hiragana_requires_sentence_start(self):
        """文中の「ちがう」では発火しない（文頭アンカーで偽陽性を抑える）。"""
        assert common.detect_correction("この2つは実装がちがうので比較したい") is None

    def test_souja_nakute_pattern(self):
        result = common.detect_correction("そうじゃなくてこっちを使う")
        assert result is not None
        assert result[0] == "souja-nakute"
        assert result[1] == 0.80

    def test_no_pattern(self):
        result = common.detect_correction("no, don't use that approach")
        assert result is not None
        assert result[0] == "no"
        assert result[1] == 0.70

    def test_dont_pattern(self):
        result = common.detect_correction("don't do that")
        assert result is not None
        assert result[0] == "dont"
        assert result[1] == 0.70

    def test_stop_pattern(self):
        result = common.detect_correction("stop doing that")
        assert result is not None
        assert result[0] == "stop"
        assert result[1] == 0.70

    def test_no_match(self):
        result = common.detect_correction("ありがとう、完璧です")
        assert result is None

    def test_empty_string(self):
        result = common.detect_correction("")
        assert result is None

    def test_question_excluded(self):
        """疑問文は除外される。"""
        result = common.detect_correction("いや、それでいいの？")
        assert result is None

    def test_question_mark_ascii(self):
        result = common.detect_correction("no, is that right?")
        assert result is None


class TestJapaneseCalibration336:
    """#336: 実コーパス目視で拾えていなかった日本語表現のパターン。"""

    def test_iya_repeated(self):
        """「いやいや」等の反復強調も iya と同義。"""
        result = common.detect_correction("いやいや、そうじゃなくてこっちのアプローチにして")
        assert result is not None
        assert result[0] == "iya"

    def test_itte_noni_quoted(self):
        """引用マーカー付き「って言ったのに」= I-told-you の日本語相当。"""
        result = common.detect_correction("って言ったのに、まだ直っていない")
        assert result is not None
        assert result[0] == "itte-noni"

    def test_itte_noni_ends_with_question_mark(self):
        """詰問形は疑問符終端でも除外されない（#336 の難所）。"""
        result = common.detect_correction("っていったけど、見た目を確認したら全然ダメだったよね？")
        assert result is not None
        assert result[0] == "itte-noni"

    def test_itte_noni_requires_quote_marker(self):
        """引用マーカー無しの「いった」（行った、の意）は誤爆しない。"""
        result = common.detect_correction("うまくいったのに、今回はなぜかダメだった")
        assert result is None

    def test_shiteki_noni(self):
        result = common.detect_correction("さっき指摘したのに、まだ直っていない")
        assert result is not None
        assert result[0] == "shiteki-noni"

    def test_shiteki_kedo_ends_with_question_mark(self):
        """#336 issue 本文の実例相当（末尾疑問符でも除外されない）。"""
        result = common.detect_correction("この設計の説明をわかりやすくしてって指摘あげたけど、何も変更していない？")
        assert result is not None
        assert result[0] == "shiteki-noni"

    def test_itte_nai_yone(self):
        """依頼していないことへの訂正。"""
        result = common.detect_correction("そんなことしてっていってないんだよね")
        assert result is not None
        assert result[0] == "itte-nai-yone"

    def test_naze_dekinakatta(self):
        result = common.detect_correction("これって、なんであなたは実装できてなかったの？")
        assert result is not None
        assert result[0] == "naze-dekinakatta"

    def test_naze_kugurinuketa(self):
        """#336 issue 本文の実例。"""
        result = common.detect_correction("なぜこれが評価をくぐりぬけた？")
        assert result is not None
        assert result[0] == "naze-dekinakatta"

    def test_naze_plain_curiosity_not_matched(self):
        """失敗語を伴わない素朴な「なぜ」は誤爆しない。"""
        result = common.detect_correction("なぜ今このツールが話題なのかを教えて")
        assert result is None

    def test_naze_reversed_word_order(self):
        """「〜できてないのはなんで？」のような語順が逆の詰問形も検出する（実コーパス実例）。"""
        result = common.detect_correction(
            "レポートみたけど、dpp pjの内容の記載がない。まるでキャッチアップできてないのはなんで？"
        )
        assert result is not None
        assert result[0] == "naze-dekinakatta"

    def test_naze_unrelated_words_across_sentence_boundary_not_matched(self):
        """文区切りを跨いだ偶然の語の近接では誤爆しない（実コーパスで検出した誤爆の回帰防止）。

        「各 fix に「なぜ」+ 見落としリスク」は設計テンプレの項目列挙であり、
        「なぜ」と「見落と」の間に閉じ括弧 」 を挟むため詰問形ではない。
        """
        result = common.detect_correction(
            "レビュー観点: 各 fix に「なぜ」+ 見落としリスクを記載してください。"
        )
        assert result is None

    def test_shiteki_kedo_without_negation_not_matched(self):
        """「指摘した + けど」だけでは新規タスク依頼を誤検出しない（実コーパスで検出した誤爆の回帰防止）。

        「けど」は逆接だけでなく話題転換にも使われるため、直後に不履行を示す語
        （何も/まだ/全然/一切 + 否定）を伴わない限り shiteki-noni は発火しない。
        """
        result = common.detect_correction(
            "さっきシステムマネージャーに何度も指摘したけど、その内容をスキルにまとめておきたい"
        )
        assert result is None

    def test_kanchigai_question(self):
        result = common.detect_correction("何か勘違いしている？")
        assert result is not None
        assert result[0] == "kanchigai-question"

    def test_kanchigai_negative_form(self):
        result = common.detect_correction("私の指示を勘違いしていない？")
        assert result is not None
        assert result[0] == "kanchigai-question"

    def test_kanchigai_self_retraction_not_matched(self):
        """過去形の自己撤回（「勘違いだったかも」）は対象外（相手への訂正ではない）。"""
        result = common.detect_correction("勘違いだったかも、無視して。いったんこのまま進めよう")
        assert result is None

    def test_kanchigai_past_tense_statement_not_matched(self):
        """疑問符を伴わない断定形は対象外（過検出を避ける保守的な条件）。"""
        result = common.detect_correction("あれは私の勘違いだね")
        assert result is None

    def test_plain_request_not_matched(self):
        """通常の依頼文（新規タスク指示）は誤検出しない（#323）。"""
        assert common.detect_correction("これを反映してください") is None
        assert common.detect_correction("何ができるかを整理してください") is None


class TestNewPatterns:
    """claude-reflect 由来のパターン検出テスト。"""

    def test_remember_explicit(self):
        result = common.detect_correction("remember: always use bun")
        assert result is not None
        assert result[0] == "remember"
        assert result[1] == 0.90

    def test_guardrail_dont_unless(self):
        result = common.detect_correction("don't add comments unless I ask")
        assert result is not None
        assert result[0] == "dont-unless-asked"
        assert result[1] == 0.90

    def test_guardrail_minimal_changes(self):
        result = common.detect_correction("minimal changes please")
        assert result is not None
        assert result[0] == "minimal-changes"

    def test_positive_perfect(self):
        result = common.detect_correction("perfect! that's what I wanted")
        assert result is not None
        assert result[0] == "perfect"
        assert result[1] == 0.70

    def test_positive_excellent(self):
        result = common.detect_correction("excellent work on this")
        assert result is not None
        assert result[0] == "keep-doing"

    def test_thats_wrong(self):
        result = common.detect_correction("that's wrong, use the other approach")
        assert result is not None
        assert result[0] == "thats-wrong"

    def test_i_told_you(self):
        result = common.detect_correction("I told you to use bun")
        assert result is not None
        assert result[0] == "I-told-you"
        assert result[1] == 0.85

    def test_use_x_not_y(self):
        result = common.detect_correction("use Python not JavaScript")
        assert result is not None
        assert result[0] == "use-X-not-Y"

    def test_actually_weak(self):
        result = common.detect_correction("actually, try the other way")
        assert result is not None
        assert result[0] == "actually"
        assert result[1] == 0.55

    def test_i_meant(self):
        result = common.detect_correction("I meant to use TypeScript")
        assert result is not None
        assert result[0] == "I-meant"


class TestA0CaptureRepairPatterns:
    """ADR-054 A0（#379）: capture_rate census 実測（precision 87.5%）で確認された
    低リスク・低recallの追加語彙（naoshite-request / yamete-request）。
    """

    def test_naoshite_bare(self):
        result = common.detect_correction("直して")
        assert result is not None
        assert result[0] == "naoshite-request"
        assert result[1] == 0.75

    def test_shusei_shite(self):
        result = common.detect_correction("修正して再配信して。")
        assert result is not None
        assert result[0] == "naoshite-request"

    def test_naoshite_hon_pr(self):
        result = common.detect_correction("本PRで直しておいて")
        assert result is not None
        assert result[0] == "naoshite-request"

    def test_naoshite_mitsuketa(self):
        result = common.detect_correction("見つけた2件も直して")
        assert result is not None
        assert result[0] == "naoshite-request"

    def test_teisei_shite_oite(self):
        result = common.detect_correction("訂正しておいて")
        assert result is not None
        assert result[0] == "naoshite-request"

    def test_yamete_hoshii_headline(self):
        """ADR headline 実例（なんで、matsukaze-mindenでコメントしちゃったの、、、やめてほしい）。"""
        result = common.detect_correction(
            "なんで、matsukaze-mindenでコメントしちゃったの、、、やめてほしい"
        )
        assert result is not None
        assert result[0] == "yamete-request"
        assert result[1] == 0.75

    def test_yamete_kudasai(self):
        result = common.detect_correction("それはやめてください")
        assert result is not None
        assert result[0] == "yamete-request"

    def test_yamete_kure(self):
        result = common.detect_correction("もうやめてくれ")
        assert result is not None
        assert result[0] == "yamete-request"

    def test_compound_verbs_not_matched(self):
        """複合動詞は naoshite-request 対象外（#9.2・実コーパスでは0件だが将来のFPを構造で防ぐ）。"""
        for verb in ["作り直して", "書き直して", "考え直して", "やり直して"]:
            assert common.detect_correction(verb) is None, verb

    def test_minaoshite_not_matched(self):
        """既知FP回帰防止: 「見直して」は naoshite-request にマッチしない。"""
        assert common.detect_correction("評価ロジックも見直して") is None


class TestA0CaptureRecallPatterns:
    """#527: 文字列単体で訂正と説明できる日本語の欠陥指摘を捕捉する。"""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("右下のボタンが表示されない。", "observed-defect"),
            ("並び替えできてると思ったら、できてなかった。", "observed-defect"),
            ("説明が改行なくてわかりづらい。", "readability-defect"),
            ("要点を絞って、もっと具体的に提案して。", "reconsider-request"),
            ("その確認はいらないんだよね。", "unnecessary-action"),
            ("そこは聞かなくてもこちらで完結しない？", "unnecessary-action"),
            ("規約を調べれば完結するよね。聞かなくても", "unnecessary-action"),
            ("この案内はわかりづらいから、文言を変えて。", "readability-defect"),
            ("できると説明していたけど、これって本当？", "contradiction-question"),
            ("この論点は前に話しなかったっけ？", "contradiction-question"),
            ("なんで削除しちゃった？", "why-undesired-action"),
            ("なんで社内向けなのに公開確認が必要になってるの？", "why-undesired-action"),
            ("週次を作る時は差分の起点を必ず認識するようにして。", "prospective-guardrail-ja"),
            ("エラーを手動でOKにできる必要があるんじゃない？", "missing-required-capability"),
            ("よいかんじ。あと、このキャラクターをもうちょっと目立たせたい。", "refinement-request"),
            ("APIって言っただけ。", "only-said"),
            ("『正常表示の例』とは違い、右下のボタンが表示されない。", "observed-defect"),
        ],
    )
    def test_explicit_defect_or_reconsideration_is_captured(self, text, expected):
        result = common.detect_correction(text)
        assert result is not None
        assert result[0] == expected

    @pytest.mark.parametrize(
        "text",
        [
            "この画面で未表示の項目を一覧にしてください。",
            "初めて読む人にもわかりやすい説明を作って。",
            "もっと具体的な利用例を新しく作って。",
            "不要なファイルを調査して一覧にして。",
            "このライブラリは本当に高速ですか？",
            "先週どの論点を話したっけ？",
            "なぜ削除機能が必要なのか教えて。",
            "必ず認識される仕組みを設計して。",
            "利用者に必要な機能があるか調査して。",
            "目立たせたい要素を新しく作って。",
        ],
    )
    def test_new_work_and_neutral_questions_are_not_captured(self, text):
        assert common.detect_correction(text) is None

    @pytest.mark.parametrize(
        "text",
        [
            "不具合報告書に『ボタンが表示されない』と記載してください。",
            "ユーザーに聞かなくても済む設計案を作って。",
            "文字が見づらい事例を一覧にしてください。",
            "この仕様説明に『これって本当？』という質問を追加して。",
            "まだ認証ができてないので、ログイン機能を実装して。",
            "ロゴをもうちょっと目立たせたい。",
            "本番前に確認しなくてもよい項目を一覧化して。",
            "この画面ではPDFを表示できるでしょ？",
            "不具合報告書に『ボタンが表示されない。』と記載してください。",
            "FAQに『この案内は見づらい。』という例を追加して。",
            "『APIって言っただけ』という発言を検索して。",
            "テストケース名は『実バグを検出する』にしてください。",
        ],
    )
    def test_trigger_phrases_in_new_work_or_neutral_questions_are_not_captured(self, text):
        assert common.detect_correction(text) is None


class TestFalsePositiveFilters:
    """偽陽性フィルタのテスト。"""

    def test_please_filtered(self):
        result = common.detect_correction("please help me fix this")
        assert result is None

    def test_can_you_filtered(self):
        result = common.detect_correction("can you fix this error?")
        assert result is None

    def test_error_description_filtered(self):
        result = common.detect_correction("error: could not connect to database")
        assert result is None

    def test_bug_report_filtered(self):
        result = common.detect_correction("the test is not passing")
        assert result is None

    def test_i_need_filtered(self):
        result = common.detect_correction("I need you to fix the API")
        assert result is None

    def test_ok_continuation_filtered(self):
        result = common.detect_correction("okay, so let me try again")
        assert result is None

    def test_remember_bypass(self):
        """remember: は偽陽性フィルタをバイパスする。"""
        result = common.detect_correction("remember: always use bun for package management")
        assert result is not None
        assert result[0] == "remember"

    def test_question_mark_filter_still_applies_to_non_reproach_patterns(self):
        """疑問符終端フィルタの一般ケースは #336 の詰問形 override で壊れない。

        「no」パターンにマッチしても詰問形 override 対象外なら従来どおり除外される
        （回帰防止・_REPROACH_OVERRIDE_KEYS を全パターンに広げていないことの固定）。
        """
        result = common.detect_correction("no, is that right?")
        assert result is None


class TestShouldIncludeMessage:
    """should_include_message() のテスト。"""

    def test_normal_text(self):
        assert common.should_include_message("いや、違うよ") is True

    def test_xml_tag(self):
        assert common.should_include_message("<system-reminder>test</system-reminder>") is False

    def test_json(self):
        assert common.should_include_message('{"key": "value"}') is False

    def test_tool_result(self):
        assert common.should_include_message("tool_result: success") is False

    def test_session_continuation(self):
        assert common.should_include_message("This session is being continued from a previous") is False

    def test_empty(self):
        assert common.should_include_message("") is False

    def test_long_text_excluded(self):
        assert common.should_include_message("x" * 501) is False

    def test_remember_bypasses_length(self):
        assert common.should_include_message("remember: " + "x" * 500) is True

    def test_stop_hook_feedback_excluded(self):
        """Stop hook が decision:block で注入した reason 文はユーザー発話でない（#305/#323）。

        実コーパス測定（8日窓・real user messages 4237件）で should_include_message を
        通過して detect_correction にマッチした 6 件は全件この形（"Stop hook feedback:\\n" +
        hook の reason 本文）だった。ユーザー発話 0 件のノイズ源。
        """
        text = (
            "Stop hook feedback:\n"
            "先送り表現を検出しました: 「別途対応」。ルール「no-defer-use-subagent」に従い、"
            "先送りせず background subagent を即座に起動して並行処理してください。"
        )
        assert common.should_include_message(text) is False

    def test_compaction_summary_excluded(self):
        """compaction 要約の再開文は機構ターン（skill_extractor #387 と同一判定基準）。"""
        text = "This session is being continued from a previous conversation that ran out of context."
        assert common.should_include_message(text) is False

    def test_local_command_caveat_marker_excluded(self):
        """<local-command-caveat> タグが剥がれても caveat 文言だけで機構判定できる。"""
        text = "Caveat: The messages below were generated by the user while running local commands."
        assert common.should_include_message(text) is False

    def test_skill_base_directory_marker_excluded(self):
        """SKILL.md 注入の "Base directory for this skill:" も機構ターン。"""
        text = "Base directory for this skill: /Users/x/.claude/skills/foo\n\n実行手順..."
        assert common.should_include_message(text) is False

    def test_real_correction_not_excluded_by_machinery_filter(self):
        """機構判定は本物のユーザー発話を誤って除外しない。"""
        assert common.should_include_message("いや、そうじゃなくて、そっちのアプローチにして") is True

    def test_background_agents_stopped_marker_excluded(self):
        """harness の停止通知本文（ADR-054 A0 census #8）は機構ターン（_MACHINERY_MARKERS 追加）。"""
        text = (
            '4 background agents were stopped by the user: "worker-a: ...修正してください。"'
        )
        assert common.should_include_message(text) is False

    def test_dispatch_working_directory_marker_excluded(self):
        """委譲プロンプトの「作業ディレクトリ:」は機構ターン（ADR-054 A2・#379）。

        weak_signals._DISPATCH_MARKERS から _MACHINERY_MARKERS へ昇格。委譲プロンプトが
        correction 検出（should_include_message）にも紛れ込まないようにする。
        """
        text = (
            "作業ディレクトリ: /Users/x/worktrees/foo\n"
            "ブランチ: `feat/123-foo`\n\n# タスク: 直して欲しい"
        )
        assert common.should_include_message(text) is False

    def test_dispatch_teammate_message_marker_excluded(self):
        """<teammate-message> ラップの idle_notification は機構ターン（ADR-054 A2・#379）。"""
        text = (
            "Another Claude session sent a message:\n"
            '<teammate-message teammate_id="collector-b" color="green">\n'
            '{"type":"idle_notification","agent":"collector-b"}'
        )
        assert common.should_include_message(text) is False

    def test_dispatch_experiment_pattern_marker_excluded(self):
        """「比較実験パターン」は機構ターン（ADR-054 A2・#379）。"""
        text = "比較実験パターンA: subagent として動作確認して"
        assert common.should_include_message(text) is False

    def test_agent_wording_not_excluded(self):
        """『あなたは』『エージェントです』は _MACHINERY_MARKERS に昇格しない（ADR-054 A2・#379）。

        rephrase 検出（隣接する高類似ペアの両方一致でのみ除外する構造的に安全な文脈）専用
        の判定であり、単独メッセージ判定（should_include_message）に混ぜると正規の
        correction 発話（例: 「あなたは間違ったことを言った」）を誤って除外してしまう。
        """
        assert common.should_include_message("あなたは間違ったことを言った") is True


class TestCalculateConfidence:
    """calculate_confidence() のテスト。"""

    def test_short_text_boost(self):
        conf, _ = common.calculate_confidence(0.70, "short")
        assert conf == pytest.approx(0.80)  # +0.10

    def test_long_text_penalty(self):
        conf, _ = common.calculate_confidence(0.70, "x" * 301)
        assert conf == pytest.approx(0.55)  # -0.15

    def test_medium_text_penalty(self):
        conf, _ = common.calculate_confidence(0.70, "x" * 200)
        assert conf == pytest.approx(0.60)  # -0.10

    def test_multiple_patterns_high(self):
        conf, decay = common.calculate_confidence(0.70, "short", matched_count=3)
        assert conf == pytest.approx(0.90)  # 0.85 + 0.10 short boost, capped at 0.90
        assert decay == 120

    def test_i_told_you_flag(self):
        conf, decay = common.calculate_confidence(0.70, "short", has_i_told_you=True)
        assert conf == pytest.approx(0.90)  # 0.85 + 0.10, capped
        assert decay == 120

    def test_single_strong(self):
        conf, decay = common.calculate_confidence(0.55, "short", has_strong=True)
        assert conf == pytest.approx(0.80)  # max(0.55, 0.70) + 0.10
        assert decay == 60


class TestDetectAllPatterns:
    """detect_all_patterns() のテスト。"""

    def test_single_match(self):
        result = common.detect_all_patterns("いや、違うよ")
        assert "iya" in result

    def test_multiple_matches(self):
        result = common.detect_all_patterns("don't use npm, use bun not npm")
        assert "dont" in result
        assert "use-X-not-Y" in result

    def test_no_match(self):
        result = common.detect_all_patterns("ありがとう")
        assert result == []

    def test_question_excluded(self):
        result = common.detect_all_patterns("いや、それでいいの？")
        assert result == []


class TestDetectCorrectionReturnType:
    """detect_correction() 戻り値型の互換テスト (Task 1.6)。"""

    def test_tuple_return(self):
        result = common.detect_correction("いや、違う")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_unpack_backfill_style(self):
        """backfill.py の correction_type, _ = result アンパック。"""
        result = common.detect_correction("いや、違う")
        correction_type, _ = result
        assert correction_type == "iya"

    def test_index_access(self):
        """test_correction_detect.py の result[0] アクセス。"""
        result = common.detect_correction("いや、違う")
        assert result[0] == "iya"
        assert isinstance(result[1], float)


class TestLastSkill:
    """common.write_last_skill / read_last_skill のテスト。"""

    def test_write_and_read(self, tmp_path):
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            common.write_last_skill("sess-ls-001", "commit")
            result = common.read_last_skill("sess-ls-001")
            assert result == "commit"

    def test_read_nonexistent(self, tmp_path):
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            result = common.read_last_skill("sess-ls-none")
            assert result is None

    def test_read_expired(self, tmp_path):
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            common.write_last_skill("sess-ls-exp", "test")
            path = common.last_skill_path("sess-ls-exp")
            old_time = time.time() - (25 * 60 * 60)
            os.utime(path, (old_time, old_time))
            result = common.read_last_skill("sess-ls-exp")
            assert result is None


class TestCorrectionDetectHook:
    """correction_detect.py のフックテスト。"""

    def test_corrections_routed_through_store_write(self, patch_data_dir):
        """corrections 書込は store_write 単一ゲート経由（ADR-049 / #55 wave 1）。

        append_jsonl 直呼びでなく store_write("corrections.jsonl", record) を通る。
        保存先解決と registry guard を単一ゲートに集約したことを構造的に固定し、
        将来 append_jsonl 直呼びに silent revert したらこのテストが落ちる。
        """
        event = {
            "session_id": "sess-cd-sw",
            "message": {"content": "いや、そうじゃなくて optimize を使って"},
        }
        with mock.patch.object(common, "store_write") as m_sw:
            correction_detect.handle_user_prompt_submit(event)
        assert m_sw.call_count == 1
        args = m_sw.call_args.args
        assert args[0] == "corrections.jsonl"
        assert args[1]["correction_type"] == "iya"
        assert args[1]["session_id"] == "sess-cd-sw"

    def test_japanese_correction_detected(self, patch_data_dir):
        event = {
            "session_id": "sess-cd-001",
            "message": {"content": "いや、そうじゃなくて skill-evolve を使って"},
        }
        correction_detect.handle_user_prompt_submit(event)

        corrections_file = patch_data_dir / "corrections.jsonl"
        assert corrections_file.exists()
        record = json.loads(corrections_file.read_text().strip())
        assert record["correction_type"] == "iya"
        assert record["confidence"] == 0.85
        assert record["session_id"] == "sess-cd-001"
        assert record["last_skill"] is None

    def test_real_cc_payload_prompt_field(self, patch_data_dir):
        """CC の実 UserPromptSubmit イベントは発話を top-level `prompt` で渡す。

        既存テストが全て合成の `message` 形だったため、実ペイロードでは
        検出ゼロ（corrections.jsonl 新規記録が 3 週間停止）になっていた回帰を封じる。
        """
        event = {
            "session_id": "sess-cd-prompt",
            "transcript_path": "/tmp/transcript.jsonl",
            "cwd": "/tmp",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "いや、そうじゃなくて skill-evolve を使って",
        }
        correction_detect.handle_user_prompt_submit(event)

        corrections_file = patch_data_dir / "corrections.jsonl"
        assert corrections_file.exists()
        record = json.loads(corrections_file.read_text().strip())
        assert record["correction_type"] == "iya"
        assert record["session_id"] == "sess-cd-prompt"

    def test_prompt_field_takes_precedence_over_empty_message(self, patch_data_dir):
        """prompt と message が両方ある場合も prompt 優先で検出する。"""
        event = {
            "session_id": "sess-cd-prompt2",
            "prompt": "違う、Bでやってって言ったよね",
            "message": {},
        }
        correction_detect.handle_user_prompt_submit(event)

        corrections_file = patch_data_dir / "corrections.jsonl"
        assert corrections_file.exists()
        record = json.loads(corrections_file.read_text().strip())
        assert record["correction_type"] == "chigau"

    def test_english_correction_detected(self, patch_data_dir):
        event = {
            "session_id": "sess-cd-002",
            "message": {"content": "No, don't use that approach"},
        }
        correction_detect.handle_user_prompt_submit(event)

        corrections_file = patch_data_dir / "corrections.jsonl"
        assert corrections_file.exists()
        record = json.loads(corrections_file.read_text().strip())
        assert record["correction_type"] == "no"

    def test_question_not_detected(self, patch_data_dir):
        """疑問文は corrections に追記されない。"""
        event = {
            "session_id": "sess-cd-003",
            "message": {"content": "いや、それでいいの？"},
        }
        correction_detect.handle_user_prompt_submit(event)

        corrections_file = patch_data_dir / "corrections.jsonl"
        assert not corrections_file.exists()

    def test_no_correction_for_normal_text(self, patch_data_dir):
        event = {
            "session_id": "sess-cd-004",
            "message": {"content": "ありがとう、完璧です"},
        }
        correction_detect.handle_user_prompt_submit(event)

        corrections_file = patch_data_dir / "corrections.jsonl"
        assert not corrections_file.exists()

    def test_with_last_skill(self, patch_data_dir, tmp_path):
        """直前スキルが紐付けられる。"""
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            common.write_last_skill("sess-cd-005", "commit")
            event = {
                "session_id": "sess-cd-005",
                "message": {"content": "いや、違うコマンドを使って"},
            }
            correction_detect.handle_user_prompt_submit(event)

        corrections_file = patch_data_dir / "corrections.jsonl"
        record = json.loads(corrections_file.read_text().strip())
        assert record["last_skill"] == "commit"

    def test_schema_compliance(self, patch_data_dir):
        """レコードが拡張スキーマに準拠する。"""
        event = {
            "session_id": "sess-cd-schema",
            "message": {"content": "いや、そうじゃなくて"},
        }
        correction_detect.handle_user_prompt_submit(event)

        corrections_file = patch_data_dir / "corrections.jsonl"
        record = json.loads(corrections_file.read_text().strip())
        # 全必須フィールドの存在チェック
        assert "correction_type" in record
        assert "matched_patterns" in record
        assert "message" in record
        assert "last_skill" in record
        assert "confidence" in record
        assert "sentiment" in record
        assert "decay_days" in record
        assert "guardrail" in record
        assert "reflect_status" in record
        assert "project_path" in record
        assert "timestamp" in record
        assert "session_id" in record
        # source フィールドは "hook" が MUST
        assert record["source"] == "hook"
        assert record["reflect_status"] == "pending"

    def test_silent_failure_on_bad_json(self, patch_data_dir, capsys):
        """不正 JSON でも exit 0（サイレント失敗）。"""
        # main() をテスト
        with mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = "NOT VALID JSON{{{"
            correction_detect.main()
        captured = capsys.readouterr()
        assert "[evolve-anything:correction] parse error" in captured.err

    def test_empty_session_id_noop(self, patch_data_dir):
        event = {
            "session_id": "",
            "message": {"content": "いや、違う"},
        }
        correction_detect.handle_user_prompt_submit(event)
        corrections_file = patch_data_dir / "corrections.jsonl"
        assert not corrections_file.exists()

    def test_pattern_version_recorded(self, patch_data_dir):
        """record に pattern_version が付与される（ADR-054 A0・capture_rate の層分離に使う）。"""
        event = {
            "session_id": "sess-cd-pv",
            "message": {"content": "いや、そうじゃなくて"},
        }
        correction_detect.handle_user_prompt_submit(event)

        corrections_file = patch_data_dir / "corrections.jsonl"
        record = json.loads(corrections_file.read_text().strip())
        assert record["pattern_version"] == 2

    def test_content_as_list(self, patch_data_dir):
        """content がリスト形式でも処理できる。"""
        event = {
            "session_id": "sess-cd-list",
            "message": {
                "content": [
                    {"type": "text", "text": "いや、違うよ"}
                ]
            },
        }
        correction_detect.handle_user_prompt_submit(event)
        corrections_file = patch_data_dir / "corrections.jsonl"
        assert corrections_file.exists()


class TestSessionTitle:
    """hookSpecificOutput.sessionTitle 出力のテスト (CC v2.1.94+)."""

    def test_session_title_on_remember(self, patch_data_dir, capsys):
        """explicit パターン（remember:）で sessionTitle を JSON 出力する。"""
        event = {
            "session_id": "sess-st-001",
            "message": {"content": "remember: always use bun"},
        }
        correction_detect.handle_user_prompt_submit(event)
        captured = capsys.readouterr()
        out = captured.out.strip()
        assert out.startswith("{"), f"expected JSON output, got: {out!r}"
        data = json.loads(out)
        assert "hookSpecificOutput" in data
        assert "sessionTitle" in data["hookSpecificOutput"]
        title = data["hookSpecificOutput"]["sessionTitle"]
        assert "remember" in title

    def test_session_title_on_guardrail(self, patch_data_dir, capsys):
        """guardrail パターンで sessionTitle を出力する。"""
        event = {
            "session_id": "sess-st-002",
            "message": {"content": "don't add comments unless I ask"},
        }
        correction_detect.handle_user_prompt_submit(event)
        captured = capsys.readouterr()
        out = captured.out.strip()
        data = json.loads(out)
        assert "hookSpecificOutput" in data
        assert "sessionTitle" in data["hookSpecificOutput"]

    def test_no_session_title_on_regular_correction(self, patch_data_dir, capsys):
        """通常の correction パターン（iya）では sessionTitle を出力しない。"""
        event = {
            "session_id": "sess-st-003",
            "message": {"content": "いや、そうじゃなくて"},
        }
        correction_detect.handle_user_prompt_submit(event)
        captured = capsys.readouterr()
        out = captured.out.strip()
        # 通常 correction は sessionTitle を emit しない
        # trigger message（plain text）は許容、JSON sessionTitle は不可
        if out.startswith("{"):
            data = json.loads(out)
            assert "sessionTitle" not in data.get("hookSpecificOutput", {})

    def test_no_session_title_on_positive(self, patch_data_dir, capsys):
        """positive パターンでも sessionTitle は出さない（ノイズ防止）。"""
        event = {
            "session_id": "sess-st-pos",
            "message": {"content": "perfect! that's exactly what I wanted"},
        }
        correction_detect.handle_user_prompt_submit(event)
        captured = capsys.readouterr()
        out = captured.out.strip()
        if out.startswith("{"):
            data = json.loads(out)
            assert "sessionTitle" not in data.get("hookSpecificOutput", {})

    def test_no_output_on_no_match(self, patch_data_dir, capsys):
        """correction パターン非該当時は一切出力しない。"""
        event = {
            "session_id": "sess-st-004",
            "message": {"content": "hello world"},
        }
        correction_detect.handle_user_prompt_submit(event)
        captured = capsys.readouterr()
        assert captured.out.strip() == ""

    def test_session_title_length_cap(self, patch_data_dir, capsys):
        """sessionTitle は 80 chars 以内に収まる。"""
        long_message = "remember: " + ("とても長い指示 " * 20)
        event = {
            "session_id": "sess-st-005",
            "message": {"content": long_message},
        }
        correction_detect.handle_user_prompt_submit(event)
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        title = data["hookSpecificOutput"]["sessionTitle"]
        assert len(title) <= 80

    def test_session_title_ascii_json_safe(self, patch_data_dir, capsys):
        """日本語を含む title も UTF-8 JSON として往復できる。"""
        event = {
            "session_id": "sess-st-006",
            "message": {"content": "remember: 常に bun を使う"},
        }
        correction_detect.handle_user_prompt_submit(event)
        captured = capsys.readouterr()
        out = captured.out.strip()
        data = json.loads(out)  # round-trip parse
        title = data["hookSpecificOutput"]["sessionTitle"]
        assert "bun" in title


class TestGetPrecedingToolCalls:
    """get_preceding_tool_calls() のユニットテスト。"""

    def _make_session_jsonl(self, tmp_path: Path, session_id: str, tool_entries: list) -> Path:
        """テスト用セッション JSONL ファイルを生成する。

        tool_entries: [{"name": str, "is_error": bool}, ...]
        各エントリに対して assistant (tool_use) + user (tool_result) の行ペアを書く。
        """
        lines = []
        for i, entry in enumerate(tool_entries):
            tool_id = f"toolu_{i:04d}"
            # assistant: tool_use
            assistant_rec = {
                "type": "assistant",
                "sessionId": session_id,
                "message": {
                    "content": [
                        {"type": "tool_use", "id": tool_id, "name": entry["name"], "input": {}}
                    ]
                },
            }
            lines.append(json.dumps(assistant_rec))
            # user: tool_result
            user_rec = {
                "type": "user",
                "sessionId": session_id,
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "is_error": entry.get("is_error", False),
                            "content": "ok" if not entry.get("is_error") else "error",
                        }
                    ]
                },
            }
            lines.append(json.dumps(user_rec))
        # 本番構造: ~/.claude/projects/<slug>/<session_id>.jsonl（2階層）に合わせる
        slug_dir = tmp_path / "test-slug"
        slug_dir.mkdir(exist_ok=True)
        session_file = slug_dir / f"{session_id}.jsonl"
        session_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return session_file

    def test_returns_last_n_tool_calls(self, tmp_path):
        """直近 N 件のツール呼び出しを正しく返す。"""
        session_id = "test-sess-ptc-001"
        self._make_session_jsonl(tmp_path, session_id, [
            {"name": "Bash", "is_error": False},
            {"name": "Edit", "is_error": False},
            {"name": "Bash", "is_error": True},
            {"name": "Read", "is_error": False},
        ])
        result = common.get_preceding_tool_calls(
            session_id, n=3, projects_dir=tmp_path
        )
        assert len(result) == 3
        # 末尾 3 件: Edit(ok), Bash(err), Read(ok)
        assert result[0] == {"tool": "Edit", "success": True}
        assert result[1] == {"tool": "Bash", "success": False}
        assert result[2] == {"tool": "Read", "success": True}

    def test_returns_all_when_less_than_n(self, tmp_path):
        """N 件未満のツール呼び出しは全件返す。"""
        session_id = "test-sess-ptc-002"
        self._make_session_jsonl(tmp_path, session_id, [
            {"name": "Bash", "is_error": False},
            {"name": "Edit", "is_error": False},
        ])
        result = common.get_preceding_tool_calls(
            session_id, n=5, projects_dir=tmp_path
        )
        assert len(result) == 2

    def test_returns_empty_when_session_not_found(self, tmp_path):
        """該当セッションファイルがない場合は空リストを返す。"""
        result = common.get_preceding_tool_calls(
            "nonexistent-session", n=5, projects_dir=tmp_path
        )
        assert result == []

    def test_returns_empty_when_projects_dir_not_found(self, tmp_path):
        """projects_dir が存在しない場合は空リストを返す（graceful fallback）。"""
        result = common.get_preceding_tool_calls(
            "any-session", n=5, projects_dir=tmp_path / "nonexistent"
        )
        assert result == []

    def test_filters_by_session_id(self, tmp_path):
        """別 sessionId のレコードを混入しない。"""
        session_id = "test-sess-ptc-004"
        other_session = "other-sess-000"
        self._make_session_jsonl(tmp_path, session_id, [
            {"name": "Bash", "is_error": False},
        ])
        self._make_session_jsonl(tmp_path, other_session, [
            {"name": "Edit", "is_error": False},
            {"name": "Write", "is_error": False},
        ])
        result = common.get_preceding_tool_calls(
            session_id, n=5, projects_dir=tmp_path
        )
        assert len(result) == 1
        assert result[0]["tool"] == "Bash"

    def test_is_error_false_when_no_is_error_field(self, tmp_path):
        """tool_result に is_error がない場合は success=True とみなす。"""
        session_id = "test-sess-ptc-005"
        lines = []
        tool_id = "toolu_0001"
        lines.append(json.dumps({
            "type": "assistant",
            "sessionId": session_id,
            "message": {"content": [
                {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {}}
            ]},
        }))
        lines.append(json.dumps({
            "type": "user",
            "sessionId": session_id,
            "message": {"content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}
                # is_error フィールドなし
            ]},
        }))
        # 本番構造: ~/.claude/projects/<slug>/<session_id>.jsonl（2階層）に合わせる
        slug_dir = tmp_path / "test-slug"
        slug_dir.mkdir(exist_ok=True)
        session_file = slug_dir / f"{session_id}.jsonl"
        session_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = common.get_preceding_tool_calls(
            session_id, n=5, projects_dir=tmp_path
        )
        assert len(result) == 1
        assert result[0] == {"tool": "Bash", "success": True}


class TestCorrectionDetectPrecedingToolCalls:
    """correction_detect.py が preceding_tool_calls を記録するテスト。"""

    def test_preceding_tool_calls_in_record(self, patch_data_dir, tmp_path):
        """correction record に preceding_tool_calls フィールドが含まれる。"""
        session_id = "sess-ptc-hook-001"
        # mock で get_preceding_tool_calls を stub する
        mock_calls = [
            {"tool": "Bash", "success": True},
            {"tool": "Edit", "success": False},
        ]
        with mock.patch.object(common, "get_preceding_tool_calls", return_value=mock_calls):
            event = {
                "session_id": session_id,
                "message": {"content": "いや、そうじゃなくて"},
            }
            correction_detect.handle_user_prompt_submit(event)

        corrections_file = patch_data_dir / "corrections.jsonl"
        assert corrections_file.exists()
        record = json.loads(corrections_file.read_text().strip())
        assert "preceding_tool_calls" in record
        assert record["preceding_tool_calls"] == mock_calls

    def test_preceding_tool_calls_empty_when_no_session_data(self, patch_data_dir, tmp_path):
        """get_preceding_tool_calls が空リストを返しても record は壊れない。"""
        with mock.patch.object(common, "get_preceding_tool_calls", return_value=[]):
            event = {
                "session_id": "sess-ptc-hook-002",
                "message": {"content": "いや、違う方向で"},
            }
            correction_detect.handle_user_prompt_submit(event)

        corrections_file = patch_data_dir / "corrections.jsonl"
        record = json.loads(corrections_file.read_text().strip())
        assert "preceding_tool_calls" in record
        assert record["preceding_tool_calls"] == []

    def test_schema_compliance_with_preceding_tool_calls(self, patch_data_dir):
        """拡張スキーマ（preceding_tool_calls 追加後）の全フィールド存在確認。"""
        with mock.patch.object(common, "get_preceding_tool_calls", return_value=[]):
            event = {
                "session_id": "sess-ptc-schema",
                "message": {"content": "いや、そうじゃなくて"},
            }
            correction_detect.handle_user_prompt_submit(event)

        corrections_file = patch_data_dir / "corrections.jsonl"
        record = json.loads(corrections_file.read_text().strip())
        assert "preceding_tool_calls" in record
        assert isinstance(record["preceding_tool_calls"], list)
