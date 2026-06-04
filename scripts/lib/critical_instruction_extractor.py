"""Critical Instruction Extractor — スキル指示の遵守保証サイクルのコアモジュール。

Phase 1 (EXTRACT): extract_critical_lines() — MUST/禁止等のキーワードで critical 行を抽出
Phase 1 (REPHRASE): rephrase_to_calm() — 決定論フォールバック（常に reject）。
  LLM 品質は emit_rephrase_request / ingest_rephrase の2相で回復する。
Phase 3 (DETECT): detect_instruction_violation() — LLM-free。対立動詞検出 + keyword_overlap。
  LLM Judge は emit_violation_judge_requests / ingest_violation_judges の2相で後追い補完する。

[ADR-037] Phase 1d-i: subprocess 経由の claude -p を全廃し、ファイルベース2相へ変換。
LLM を呼ばないため no-llm-in-tests と完全整合（mock 不要）。

Related: issue #39
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# llm_broker を import（Phase 1a 共通基盤）
_lib_dir = Path(__file__).resolve().parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from llm_broker import build_requests, parse_responses, passthrough

# ── 定数 ────────────────────────────────────────────────

CRITICAL_KEYWORDS = [
    "MUST", "must not", "必須", "禁止", "絶対に", "決して",
    "never", "always", "required", "forbidden", "mandatory",
    "必ず", "してはいけない", "厳禁",
]

CRITICAL_SECTION_HEADERS = [
    "Important", "Critical", "注意", "Warning", "禁止事項",
    "Must", "Required", "必須事項",
]

# 対立動詞ペア（矛盾検出用）— キーはタプル (en, ja)
OPPOSING_VERBS: Dict[Tuple[str, ...], Tuple[str, ...]] = {
    ("move", "移動"): ("delete", "削除"),
    ("add", "追加"): ("remove", "除去"),
    ("create", "作成"): ("delete", "削除"),
    ("keep", "保持"): ("remove", "除去"),
    ("archive", "アーカイブ"): ("delete", "削除"),
    ("update", "更新"): ("skip", "スキップ"),
    ("copy", "コピー"): ("delete", "削除"),
}

# 同義動詞ペア（誤検出防止用）— キーはタプル、値は同義語タプル
SYNONYM_VERBS: Dict[Tuple[str, ...], Tuple[str, ...]] = {
    ("move", "移動"): ("transfer", "移管", "migrate"),
    ("delete", "削除"): ("remove", "除去", "drop"),
    ("create", "作成"): ("generate", "生成", "add", "追加"),
    ("update", "更新"): ("modify", "変更", "edit", "編集"),
}

REPHRASE_CONFIDENCE_MIN = 0.80
REPHRASE_HUMAN_REVIEW_MIN = 0.60
LLM_JUDGE_TIMEOUT_SECONDS = 30
KEYWORD_OVERLAP_FALLBACK_MIN = 3
_MAX_INSTRUCTIONS = 15


# ── データクラス ────────────────────────────────────────

@dataclass
class CriticalInstruction:
    """スキルから抽出された critical 指示。"""
    original: str
    rephrased: str = ""
    language: str = "en"
    source_line: int = 0


@dataclass
class Violation:
    """検出された指示違反。"""
    instruction: CriticalInstruction
    correction_message: str
    match_type: str = ""  # "opposing_verb" | "llm_judge" | "keyword_overlap"
    confidence: float = 0.0
    reason: str = ""
    needs_review: bool = False


# ── Phase 1: EXTRACT ────────────────────────────────────


def _detect_language(text: str) -> str:
    """テキストの主要言語を推定する。"""
    ja_chars = sum(1 for c in text if ord(c) > 0x3000)
    return "ja" if ja_chars > len(text) * 0.1 else "en"


def _build_keyword_pattern() -> re.Pattern:
    """CRITICAL_KEYWORDS から正規表現パターンを構築する。"""
    escaped = [re.escape(kw) for kw in CRITICAL_KEYWORDS]
    return re.compile(r"(?i)\b(?:" + "|".join(escaped) + r")\b|" +
                      "|".join(re.escape(kw) for kw in CRITICAL_KEYWORDS if any(ord(c) > 0x3000 for c in kw)))


def _build_section_pattern() -> re.Pattern:
    """CRITICAL_SECTION_HEADERS からセクション見出しパターンを構築する。"""
    escaped = [re.escape(h) for h in CRITICAL_SECTION_HEADERS]
    return re.compile(r"^#{1,3}\s+.*(?:" + "|".join(escaped) + r")", re.IGNORECASE | re.MULTILINE)


_KEYWORD_RE = _build_keyword_pattern()
_SECTION_RE = _build_section_pattern()
_CONDITIONAL_RE = re.compile(
    r"(?:の場合は?必ず|場合は?絶対|if.*(?:must|always|never)|when.*(?:must|always|never))",
    re.IGNORECASE,
)


def extract_critical_lines(skill_content: str) -> List[CriticalInstruction]:
    """スキルコンテンツから critical 指示を抽出する。

    3つのソースから検出:
    1. CRITICAL_KEYWORDS を含む行
    2. CRITICAL_SECTION_HEADERS 配下の行
    3. 条件付き指示パターン
    """
    if not skill_content.strip():
        return []

    results: List[CriticalInstruction] = []
    seen: Set[str] = set()
    lines = skill_content.split("\n")

    # 1. キーワードマッチング
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _KEYWORD_RE.search(stripped) and stripped not in seen:
            seen.add(stripped)
            results.append(CriticalInstruction(
                original=stripped,
                language=_detect_language(stripped),
                source_line=i,
            ))

    # 2. セクション見出し配下の行
    in_critical_section = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if re.match(r"^#{1,3}\s+", stripped):
            in_critical_section = bool(_SECTION_RE.match(stripped))
            continue
        if in_critical_section and stripped and not stripped.startswith("#"):
            if stripped not in seen:
                seen.add(stripped)
                results.append(CriticalInstruction(
                    original=stripped,
                    language=_detect_language(stripped),
                    source_line=i,
                ))

    # 3. 条件付き指示パターン
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if _CONDITIONAL_RE.search(stripped) and stripped not in seen:
            seen.add(stripped)
            results.append(CriticalInstruction(
                original=stripped,
                language=_detect_language(stripped),
                source_line=i,
            ))

    return results


# ── Phase 1: REPHRASE (決定論フォールバック + 2相API) ──────────────────────


def rephrase_to_calm(
    instruction: str, *, language: str = "en"
) -> Tuple[str, float, str]:
    """攻撃的表現を calm/direct に変換する（決定論フォールバック）。

    常に (instruction, 0.0, "reject") を返す。
    LLM 品質は emit_rephrase_request / ingest_rephrase の2相で回復する。

    Returns:
        (rephrased_text, confidence, action)
        action: "auto" | "human_review" | "reject"
    """
    return instruction, 0.0, "reject"


def _build_rephrase_prompt(item: Dict[str, Any]) -> str:
    """rephrase リクエスト用プロンプトを生成する（決定論）。

    build_requests から渡される item は元の dict（id/instruction/language を持つ）。
    """
    instruction = item["instruction"]
    language = item.get("language", "en")
    lang_hint = "日本語" if language == "ja" else "English"
    return (
        f"以下の指示を、攻撃的な表現（MUST/NEVER/禁止等）を使わずに、"
        f"穏やかで直接的な表現にリフレーズしてください。"
        f"元の意味を正確に保持してください。{lang_hint}で回答してください。\n\n"
        f"元の指示: {instruction}\n\n"
        f'JSON形式で回答: {{"rephrased": "...", "confidence": 0.0-1.0}}'
    )


def emit_rephrase_request(
    instruction: str, *, language: str = "en"
) -> Dict[str, Any]:
    """rephrase LLM リクエストを生成する（Phase A）。

    LLM・subprocess を一切呼ばない。

    Returns:
        {"requests": [{"id": "rephrase", "prompt": str, "meta": dict}]}
    """
    items = [{"id": "rephrase", "instruction": instruction, "language": language}]
    requests = build_requests(items, _build_rephrase_prompt)
    return {"requests": requests}


def ingest_rephrase(
    instruction: str,
    requests: List[Dict[str, Any]],
    responses: Dict[str, Any],
) -> Tuple[str, float, str]:
    """Phase B 応答から rephrase 結果を回収する（Phase C）。

    LLM・subprocess を一切呼ばない。None/パース失敗 → (instruction, 0.0, "reject")。

    Returns:
        (rephrased_text, confidence, action)
        action: "auto" | "human_review" | "reject"
    """
    parsed = parse_responses(requests, responses, passthrough)
    raw = parsed.get("rephrase")
    if not raw:
        return instruction, 0.0, "reject"
    try:
        json_match = re.search(r"\{[^}]+\}", str(raw))
        if not json_match:
            return instruction, 0.0, "reject"
        data = json.loads(json_match.group())
        rephrased = data.get("rephrased", instruction)
        confidence = float(data.get("confidence", 0.0))
        if confidence >= REPHRASE_CONFIDENCE_MIN:
            return rephrased, confidence, "auto"
        elif confidence >= REPHRASE_HUMAN_REVIEW_MIN:
            return rephrased, confidence, "human_review"
        else:
            return instruction, confidence, "reject"
    except (json.JSONDecodeError, ValueError, TypeError):
        return instruction, 0.0, "reject"


# ── Phase 3: DETECT (LLM-free + 2相API) ────────────────


def _flatten_verb_group(group: Tuple[str, ...]) -> Set[str]:
    """動詞グループをフラットな小文字セットに変換する。"""
    return {v.lower() for v in group}


def _get_all_synonyms(verb: str) -> Set[str]:
    """指定動詞の全同義語を返す。"""
    verb_lower = verb.lower()
    result = {verb_lower}
    for key_group, syn_group in SYNONYM_VERBS.items():
        all_in_group = _flatten_verb_group(key_group) | _flatten_verb_group(syn_group)
        if verb_lower in all_in_group:
            result |= all_in_group
    return result


def _extract_verbs_from_text(text: str) -> Set[str]:
    """テキストから OPPOSING_VERBS / SYNONYM_VERBS に含まれる動詞を抽出する。"""
    all_verbs: Set[str] = set()
    for key_group, val_group in OPPOSING_VERBS.items():
        all_verbs |= _flatten_verb_group(key_group)
        all_verbs |= _flatten_verb_group(val_group)
    for key_group, syn_group in SYNONYM_VERBS.items():
        all_verbs |= _flatten_verb_group(key_group)
        all_verbs |= _flatten_verb_group(syn_group)

    text_lower = text.lower()
    return {v for v in all_verbs if v in text_lower}


def _check_opposing_verbs(
    instruction_verbs: Set[str], correction_verbs: Set[str]
) -> Optional[Tuple[str, str]]:
    """対立動詞ペアが instruction と correction の間に存在するか確認する。"""
    for key_group, val_group in OPPOSING_VERBS.items():
        key_set = _flatten_verb_group(key_group)
        val_set = _flatten_verb_group(val_group)
        # instruction に key があり correction に val がある（またはその逆）
        if (instruction_verbs & key_set and correction_verbs & val_set):
            return (
                next(iter(instruction_verbs & key_set)),
                next(iter(correction_verbs & val_set)),
            )
        if (instruction_verbs & val_set and correction_verbs & key_set):
            return (
                next(iter(instruction_verbs & val_set)),
                next(iter(correction_verbs & key_set)),
            )
    return None


def _are_synonyms(verb1: str, verb2: str) -> bool:
    """2つの動詞が同義かどうか判定する。"""
    syns1 = _get_all_synonyms(verb1)
    return verb2.lower() in syns1


def _keyword_overlap(text1: str, text2: str) -> int:
    """2つのテキストの共通キーワード数を返す（小文字化、3文字以上）。"""
    words1 = {w.lower() for w in re.findall(r"\w{3,}", text1)}
    words2 = {w.lower() for w in re.findall(r"\w{3,}", text2)}
    return len(words1 & words2)


def detect_instruction_violation(
    correction: Dict[str, Any],
    instructions: List[CriticalInstruction],
) -> Optional[Violation]:
    """correction が instructions のいずれかに違反しているか検出する（LLM-free）。

    2段階マッチング:
    1. 対立動詞検出 (deterministic) → 確定違反
    2. keyword overlap >= KEYWORD_OVERLAP_FALLBACK_MIN → 「要確認」
       （LLM Judge は emit_violation_judge_requests / ingest_violation_judges の2相で補完）
    """
    message = correction.get("message", "")
    if not message or not instructions:
        return None

    correction_verbs = _extract_verbs_from_text(message)
    limited = instructions[:_MAX_INSTRUCTIONS]

    for instr in limited:
        instruction_text = instr.original
        instruction_verbs = _extract_verbs_from_text(instruction_text)

        # Stage 1: 対立動詞検出
        opposing = _check_opposing_verbs(instruction_verbs, correction_verbs)
        if opposing:
            v1, v2 = opposing
            if not _are_synonyms(v1, v2):
                return Violation(
                    instruction=instr,
                    correction_message=message,
                    match_type="opposing_verb",
                    confidence=0.95,
                    reason=f"対立動詞検出: instruction={v1}, correction={v2}",
                )

        # Stage 2: keyword overlap fallback（LLM が常に失敗した場合の既存挙動と一致）
        overlap = _keyword_overlap(message, instruction_text)
        if overlap >= KEYWORD_OVERLAP_FALLBACK_MIN:
            return Violation(
                instruction=instr,
                correction_message=message,
                match_type="keyword_overlap",
                confidence=0.50,
                reason=f"keyword overlap={overlap} (LLM Judge 失敗時 fallback)",
                needs_review=True,
            )

    return None


def _build_judge_prompt(item: Dict[str, Any]) -> str:
    """violation judge リクエスト用プロンプトを生成する（決定論）。

    build_requests から渡される item は元の dict（id/idx/correction_message/instruction_text を持つ）。
    """
    correction_message = item["correction_message"]
    instruction_text = item["instruction_text"]
    return (
        f"以下のユーザー修正が、スキル指示への違反を示しているか判定してください。\n\n"
        f"スキル指示: {instruction_text}\n"
        f"ユーザー修正: {correction_message}\n\n"
        f"direct scoring: 違反していれば is_violation=true、していなければ false。\n"
        f"Chain of Thought: まず理由を考え、次に判定を出してください。\n\n"
        f'JSON形式で回答: {{"is_violation": true/false, "confidence": 0.0-1.0, "reason": "..."}}'
    )


def emit_violation_judge_requests(
    correction: Dict[str, Any],
    instructions: List[CriticalInstruction],
) -> Dict[str, Any]:
    """violation judge LLM リクエストを生成する（Phase A）。

    message 空 or instructions 空 → {"requests": []}。
    先頭15件に制限。Stage1 で確定しそうな instruction も含め全件 emit する（ingest 側が短絡）。
    LLM・subprocess を一切呼ばない。

    Returns:
        {"requests": [{"id": "judge:{idx}", "prompt": str, "meta": dict}]}
    """
    message = correction.get("message", "")
    if not message or not instructions:
        return {"requests": []}

    limited = instructions[:_MAX_INSTRUCTIONS]
    items = [
        {
            "id": f"judge:{idx}",
            "idx": idx,
            "correction_message": message,
            "instruction_text": instr.original,
        }
        for idx, instr in enumerate(limited)
    ]
    requests = build_requests(items, _build_judge_prompt)
    return {"requests": requests}


def ingest_violation_judges(
    correction: Dict[str, Any],
    instructions: List[CriticalInstruction],
    requests: List[Dict[str, Any]],
    responses: Dict[str, Any],
) -> Optional[Violation]:
    """Phase B 応答から violation 判定を回収する（Phase C）。

    元の detect_instruction_violation ループを順序通りに再生する（先頭15件）。
    各 instruction について:
      - Stage1 opposing 非同義 → Violation(opposing_verb, 0.95) return
      - else: responses から judge:{idx} の raw を回収
        - verdict 取得 & is_violation 真 → Violation(llm_judge, ...) return
        - verdict 取得 & 偽 → continue
        - verdict 取得失敗 → keyword_overlap fallback
    LLM・subprocess を一切呼ばない。

    Returns:
        Violation | None
    """
    message = correction.get("message", "")
    if not message or not instructions:
        return None

    correction_verbs = _extract_verbs_from_text(message)
    limited = instructions[:_MAX_INSTRUCTIONS]
    parsed = parse_responses(requests, responses, passthrough)

    for idx, instr in enumerate(limited):
        instruction_text = instr.original
        instruction_verbs = _extract_verbs_from_text(instruction_text)

        # Stage 1: 対立動詞検出（LLM より優先）
        opposing = _check_opposing_verbs(instruction_verbs, correction_verbs)
        if opposing:
            v1, v2 = opposing
            if not _are_synonyms(v1, v2):
                return Violation(
                    instruction=instr,
                    correction_message=message,
                    match_type="opposing_verb",
                    confidence=0.95,
                    reason=f"対立動詞検出: instruction={v1}, correction={v2}",
                )

        # Stage 2: LLM Judge 応答を回収
        raw = parsed.get(f"judge:{idx}")
        verdict = None
        if raw:
            try:
                json_match = re.search(r"\{[^}]+\}", str(raw))
                if json_match:
                    verdict = json.loads(json_match.group())
            except (json.JSONDecodeError, ValueError, TypeError):
                verdict = None

        if verdict is not None:
            if verdict.get("is_violation"):
                return Violation(
                    instruction=instr,
                    correction_message=message,
                    match_type="llm_judge",
                    confidence=verdict.get("confidence", 0.0),
                    reason=verdict.get("reason", ""),
                )
            # LLM が非違反と判定 → この instruction についてはスキップ
            continue

        # verdict 取得失敗 → keyword_overlap fallback
        overlap = _keyword_overlap(message, instruction_text)
        if overlap >= KEYWORD_OVERLAP_FALLBACK_MIN:
            return Violation(
                instruction=instr,
                correction_message=message,
                match_type="keyword_overlap",
                confidence=0.50,
                reason=f"keyword overlap={overlap} (LLM Judge 失敗時 fallback)",
                needs_review=True,
            )

    return None
