"""critical_instruction_extractor のテスト。

[ADR-037] Phase 1d-i: subprocess / LLM mock を全廃し、2相 API と決定論経路でカバー。

テスト構成:
- extract_critical_lines: 5パス（変更なし）
- rephrase_to_calm: LLM-free（常に reject）3パス
- emit_rephrase_request / ingest_rephrase: 2相 API 6パス
- detect_instruction_violation: LLM-free 6パス
- emit_violation_judge_requests / ingest_violation_judges: 2相 API 7パス
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/lib を import パスに追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from critical_instruction_extractor import (
    CRITICAL_KEYWORDS,
    CRITICAL_SECTION_HEADERS,
    KEYWORD_OVERLAP_FALLBACK_MIN,
    LLM_JUDGE_TIMEOUT_SECONDS,
    OPPOSING_VERBS,
    REPHRASE_CONFIDENCE_MIN,
    REPHRASE_HUMAN_REVIEW_MIN,
    SYNONYM_VERBS,
    CriticalInstruction,
    Violation,
    detect_instruction_violation,
    emit_rephrase_request,
    emit_violation_judge_requests,
    extract_critical_lines,
    ingest_rephrase,
    ingest_violation_judges,
    rephrase_to_calm,
)

# ── extract_critical_lines ──────────────────────────────


class TestExtractCriticalLines:
    """extract_critical_lines の5パス。"""

    def test_keyword_match(self):
        """MUST/禁止等のキーワードを含む行を抽出する。"""
        content = """\
## Usage
Run the skill.

## Important Rules
- You MUST move old items to CHANGELOG.md
- Regular instruction here
- 古い項目は禁止事項として削除してはいけない
"""
        result = extract_critical_lines(content)
        assert len(result) >= 2
        assert all(isinstance(r, CriticalInstruction) for r in result)
        # MUST を含む行が抽出される
        must_lines = [r for r in result if "MUST" in r.original or "禁止" in r.original]
        assert len(must_lines) >= 1

    def test_section_header_match(self):
        """## Important, ## 注意 等のセクション見出し配下の行を抽出する。"""
        content = """\
## 注意事項
- old items は CHANGELOG に移動すること
- 削除は行わないこと

## Usage
Regular content here.
"""
        result = extract_critical_lines(content)
        assert len(result) >= 1
        # 「注意事項」セクション配下の行が抽出される
        assert any("移動" in r.original or "削除" in r.original for r in result)

    def test_conditional_instruction(self):
        """「〜の場合は必ず〜」のような条件付き指示を抽出する。"""
        content = """\
## Rules
- エラーの場合は必ずログを記録すること
- 通常の処理を実行する
"""
        result = extract_critical_lines(content)
        assert len(result) >= 1
        assert any("必ず" in r.original for r in result)

    def test_empty_when_no_critical(self):
        """critical キーワードがないスキルでは空リストを返す。"""
        content = """\
## Usage
Run the command and check the output.
Regular instructions only.
"""
        result = extract_critical_lines(content)
        assert result == []

    def test_bilingual_content(self):
        """日英混在のスキルで両言語の critical 行を抽出する。"""
        content = """\
## Rules
- You must always check the output
- 結果は必ず確認すること
- This is a regular line
"""
        result = extract_critical_lines(content)
        assert len(result) >= 2
        langs = {r.language for r in result}
        assert "en" in langs or "ja" in langs


# ── rephrase_to_calm (LLM-free) ────────────────────────


class TestRephraseToCalm:
    """rephrase_to_calm: LLM-free 化。常に (instruction, 0.0, "reject") を返す。"""

    def test_always_returns_reject(self):
        """rephrase_to_calm は常に reject を返す（LLM-free フォールバック）。"""
        text, confidence, action = rephrase_to_calm(
            "You MUST NOT delete old items", language="en"
        )
        assert action == "reject"
        assert confidence == 0.0
        assert text == "You MUST NOT delete old items"

    def test_returns_original_instruction_ja(self):
        """日本語の指示でも元の指示をそのまま返す。"""
        instruction = "禁止: 削除してはいけない"
        text, confidence, action = rephrase_to_calm(instruction, language="ja")
        assert action == "reject"
        assert text == instruction
        assert confidence == 0.0

    def test_signature_preserved(self):
        """シグネチャ（language キーワード引数）が維持されている。"""
        text, confidence, action = rephrase_to_calm("NEVER skip tests")
        assert isinstance(text, str)
        assert isinstance(confidence, float)
        assert isinstance(action, str)


# ── emit_rephrase_request / ingest_rephrase ────────────


class TestEmitIngestRephrase:
    """emit_rephrase_request / ingest_rephrase の2相 API テスト。"""

    def test_emit_returns_single_request(self):
        """emit_rephrase_request は id="rephrase" の1件リクエストを返す。"""
        result = emit_rephrase_request("You MUST NOT delete items", language="en")
        assert "requests" in result
        assert len(result["requests"]) == 1
        req = result["requests"][0]
        assert req["id"] == "rephrase"
        assert "prompt" in req
        assert "meta" in req
        assert req["meta"]["instruction"] == "You MUST NOT delete items"
        assert req["meta"]["language"] == "en"

    def test_emit_prompt_contains_instruction(self):
        """emit のプロンプトに元の指示が含まれる。"""
        instruction = "NEVER skip the tests"
        result = emit_rephrase_request(instruction)
        prompt = result["requests"][0]["prompt"]
        assert instruction in prompt

    def test_ingest_auto_high_confidence(self):
        """confidence >= 0.80 → auto。"""
        out = emit_rephrase_request("You MUST check output")
        requests = out["requests"]
        responses = {"rephrase": '{"rephrased": "Please check output", "confidence": 0.90}'}
        text, confidence, action = ingest_rephrase("You MUST check output", requests, responses)
        assert action == "auto"
        assert confidence >= REPHRASE_CONFIDENCE_MIN
        assert text == "Please check output"

    def test_ingest_human_review_medium_confidence(self):
        """confidence 0.60-0.80 → human_review。"""
        out = emit_rephrase_request("NEVER delete items")
        requests = out["requests"]
        responses = {"rephrase": '{"rephrased": "items should not be deleted", "confidence": 0.70}'}
        text, confidence, action = ingest_rephrase("NEVER delete items", requests, responses)
        assert action == "human_review"
        assert REPHRASE_HUMAN_REVIEW_MIN <= confidence < REPHRASE_CONFIDENCE_MIN

    def test_ingest_reject_low_confidence(self):
        """confidence < 0.60 → reject、元の指示を返す。"""
        instruction = "You MUST check output"
        out = emit_rephrase_request(instruction)
        requests = out["requests"]
        responses = {"rephrase": '{"rephrased": "something wrong", "confidence": 0.40}'}
        text, confidence, action = ingest_rephrase(instruction, requests, responses)
        assert action == "reject"
        assert text == instruction

    def test_ingest_parse_failure_returns_reject(self):
        """パース失敗 → reject、元の指示を返す。"""
        instruction = "禁止: 削除してはいけない"
        out = emit_rephrase_request(instruction, language="ja")
        requests = out["requests"]
        responses = {"rephrase": "not valid json at all"}
        text, confidence, action = ingest_rephrase(instruction, requests, responses)
        assert action == "reject"
        assert text == instruction

    def test_ingest_missing_response_returns_reject(self):
        """response が欠損（空 dict）→ reject。"""
        instruction = "You MUST verify"
        out = emit_rephrase_request(instruction)
        requests = out["requests"]
        text, confidence, action = ingest_rephrase(instruction, requests, {})
        assert action == "reject"
        assert text == instruction


# ── detect_instruction_violation (LLM-free) ────────────


class TestDetectInstructionViolation:
    """detect_instruction_violation: LLM-free の6パス。"""

    def test_opposing_verb_match(self):
        """対立動詞検出 (move vs delete) → 確定違反。"""
        correction = {
            "message": "削除じゃなくて移動して",
            "correction_type": "stop",
            "last_skill": "commit",
        }
        instructions = [
            CriticalInstruction(
                original="古い項目は CHANGELOG.md へ移動すること",
                rephrased="古い項目は CHANGELOG.md へ移動する",
                language="ja",
                source_line=10,
            )
        ]
        result = detect_instruction_violation(correction, instructions)
        assert result is not None
        assert isinstance(result, Violation)
        assert result.match_type == "opposing_verb"

    def test_synonym_verb_no_false_positive(self):
        """同義動詞 (create vs generate) → 対立動詞誤検出なし。keyword_overlap も低ければ None。"""
        correction = {
            "message": "generate ではなく create にして",
            "correction_type": "stop",
            "last_skill": "scaffold",
        }
        instructions = [
            CriticalInstruction(
                original="You must create the file first",
                rephrased="Create the file first",
                language="en",
                source_line=3,
            )
        ]
        # create と generate は同義なので opposing_verb では検出されない
        # keyword overlap も低いので None
        result = detect_instruction_violation(correction, instructions)
        # opposing_verb は出ないことを確認（None or keyword_overlap のみ）
        if result is not None:
            assert result.match_type != "opposing_verb"

    def test_keyword_overlap_flagged(self):
        """keyword overlap >= 3 → 「要確認」フラグ。"""
        correction = {
            "message": "CHANGELOG file output items format wrong",
            "correction_type": "stop",
            "last_skill": "commit",
        }
        instructions = [
            CriticalInstruction(
                original="output items into CHANGELOG file correctly",
                rephrased="output items into CHANGELOG file",
                language="en",
                source_line=5,
            )
        ]
        result = detect_instruction_violation(correction, instructions)
        assert result is not None
        assert result.match_type == "keyword_overlap"
        assert result.needs_review is True

    def test_low_keyword_overlap_no_violation(self):
        """keyword overlap < 3 → 非違反。"""
        correction = {
            "message": "もっと丁寧にして",
            "correction_type": "stop",
            "last_skill": "commit",
        }
        instructions = [
            CriticalInstruction(
                original="You must move items to CHANGELOG",
                rephrased="Items should be moved to CHANGELOG",
                language="en",
                source_line=5,
            )
        ]
        result = detect_instruction_violation(correction, instructions)
        assert result is None

    def test_opposing_verb_bidirectional(self):
        """対立動詞ペアは双方向で検出される。"""
        correction = {
            "message": "移動して、削除しないで",
            "correction_type": "stop",
            "last_skill": "cleanup",
        }
        instructions = [
            CriticalInstruction(
                original="不要なファイルは削除すること",
                rephrased="不要なファイルは削除する",
                language="ja",
                source_line=20,
            )
        ]
        result = detect_instruction_violation(correction, instructions)
        assert result is not None
        assert result.match_type == "opposing_verb"

    def test_empty_inputs_return_none(self):
        """message 空 or instructions 空 → None。"""
        assert detect_instruction_violation({"message": ""}, [
            CriticalInstruction(original="MUST do X", language="en", source_line=1)
        ]) is None
        assert detect_instruction_violation({"message": "some text"}, []) is None


# ── emit_violation_judge_requests / ingest_violation_judges ──


class TestEmitIngestViolationJudges:
    """2相 API テスト: emit_violation_judge_requests / ingest_violation_judges。"""

    def _make_correction(self, message: str) -> dict:
        return {"message": message, "correction_type": "stop", "last_skill": "commit"}

    def _make_instr(self, original: str) -> CriticalInstruction:
        return CriticalInstruction(original=original, language="en", source_line=1)

    def test_emit_empty_message_returns_empty(self):
        """message 空 → {"requests": []}。"""
        result = emit_violation_judge_requests({"message": ""}, [
            self._make_instr("You must move items")
        ])
        assert result == {"requests": []}

    def test_emit_empty_instructions_returns_empty(self):
        """instructions 空 → {"requests": []}。"""
        result = emit_violation_judge_requests(self._make_correction("delete it"), [])
        assert result == {"requests": []}

    def test_emit_creates_judge_requests_per_instruction(self):
        """instructions 2件 → judge:0, judge:1 の2リクエスト。"""
        correction = self._make_correction("delete the file")
        instructions = [
            self._make_instr("You must move the file"),
            self._make_instr("Always keep a backup"),
        ]
        result = emit_violation_judge_requests(correction, instructions)
        ids = [r["id"] for r in result["requests"]]
        assert "judge:0" in ids
        assert "judge:1" in ids

    def test_ingest_llm_judge_violation(self):
        """LLM Judge が is_violation=true → Violation(llm_judge) を返す。"""
        correction = self._make_correction("CHANGELOG を上書きしないで")
        instructions = [
            self._make_instr("CHANGELOG への追記のみ行うこと"),
        ]
        out = emit_violation_judge_requests(correction, instructions)
        requests = out["requests"]
        responses = {
            "judge:0": '{"is_violation": true, "confidence": 0.85, "reason": "指示に反している"}'
        }
        result = ingest_violation_judges(correction, instructions, requests, responses)
        assert result is not None
        assert result.match_type == "llm_judge"
        assert result.confidence == 0.85
        assert result.reason == "指示に反している"

    def test_ingest_llm_judge_no_violation_returns_none(self):
        """LLM Judge が is_violation=false → None。"""
        correction = self._make_correction("フォーマットを変えて")
        instructions = [
            self._make_instr("You must move items to CHANGELOG"),
        ]
        out = emit_violation_judge_requests(correction, instructions)
        requests = out["requests"]
        responses = {
            "judge:0": '{"is_violation": false, "confidence": 0.80, "reason": "関連なし"}'
        }
        result = ingest_violation_judges(correction, instructions, requests, responses)
        assert result is None

    def test_ingest_missing_response_falls_back_to_keyword_overlap(self):
        """judge response 欠損 → keyword_overlap fallback。"""
        correction = self._make_correction("CHANGELOG file output items format wrong")
        instructions = [
            self._make_instr("output items into CHANGELOG file correctly"),
        ]
        out = emit_violation_judge_requests(correction, instructions)
        requests = out["requests"]
        # 空レスポンス → fallback
        result = ingest_violation_judges(correction, instructions, requests, {})
        assert result is not None
        assert result.match_type == "keyword_overlap"
        assert result.needs_review is True

    def test_ingest_stage1_opposing_takes_priority_over_llm(self):
        """Stage1 対立動詞が LLM Judge より優先される。"""
        correction = self._make_correction("削除じゃなくて移動して")
        instructions = [
            self._make_instr("古い項目は CHANGELOG.md へ移動すること"),
        ]
        out = emit_violation_judge_requests(correction, instructions)
        requests = out["requests"]
        # LLM が「非違反」と返しても Stage1 opposing が先に返るはず
        responses = {
            "judge:0": '{"is_violation": false, "confidence": 0.90, "reason": "LLM says no"}'
        }
        result = ingest_violation_judges(correction, instructions, requests, responses)
        # Stage1 で opposing_verb を検出 → LLM 応答より優先
        assert result is not None
        assert result.match_type == "opposing_verb"
