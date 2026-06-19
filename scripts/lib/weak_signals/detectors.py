"""weak_signals.detectors — 4 チャネルの決定論検出器（#432）。

ゼロ LLM・静的解析のみ。各検出器は ``WeakSignal`` のリストを返す純関数で、
副作用（store 書き込み）は持たない（batch 側がまとめて書く）。

偽陽性の文脈除去は learning_detector_fp_context_not_allowlist 準拠:
**個別 allowlist でなく「除外理由の直交分離」**。言い直し検出では「機構ターン
（並列 agent 派遣テンプレ等）が utterance extractor をすり抜けたもの」という 1 つの
理由でまとめて落とす（実コーパス dry-run で確認した最大の FP 源）。

データソース:
- ① 直後手編集 / ④ Esc 中断: transcript jsonl 直読（observe hook は永続化しないため）
- ② permission deny: errors.jsonl の permission_denied レコード
- ③ 言い直し: utterances.db（query_utterances。#430 の発話データ基盤を入力に使う）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .store import WeakSignal, now_iso

# ── チャネル ③（言い直し）のしきい値 ──────────────────────────────
# 実コーパス dry-run（evolve-anything 全 PJ utterances.db 3204 発話 / 1289 連続ペア）で
# 分布を確認して決定（ADR-044 準拠・固定値を設計前に決めない）:
#   - 0.6: 162 ペア（うち大半が並列 agent 派遣テンプレの誤検知）
#   - 0.8 + dispatch 除外: 16 ペア（目視 100% が真の言い直し/再送）
# → 0.8 を採用。dispatch 除外を併用して FP 源（テンプレ）を直交分離する。
REPHRASE_JACCARD_THRESHOLD = 0.8

# 言い直し判定の最小トークン数（短すぎる発話は jaccard が不安定で FP になりやすい）。
REPHRASE_MIN_TOKENS = 2

# 機構ターン（並列 agent 派遣テンプレ・採点ジョブ等）のマーカー。utterance extractor の
# _HARNESS_MARKERS をすり抜けて dialogue として保存されるが、言い直しではない。
# 「除外理由 = 機構生成テンプレ」で直交分離する（個別文字列の allowlist ではない）。
_DISPATCH_MARKERS = (
    "<task-notification>",
    "<tool-use-id>",
    "<summary>",
    "作業ディレクトリ:",
    "あなたは",
    "エージェントです",
    "比較実験パターン",
    "experiment ",
)

# ① 直後手編集の transcript シグナル（CC が Edit/Write 試行時に出す tool_use_error）。
# 「user or linter」のどちらかなので provenance に両方の可能性を残す（捏造しない）。
_FILE_MODIFIED_MARKER = "File has been modified since read"

# ④ Esc 中断の transcript シグナル。
_INTERRUPT_MARKER = "[Request interrupted"


def _is_dispatch(text: str) -> bool:
    return any(m in text for m in _DISPATCH_MARKERS)


# ── ② permission deny ──────────────────────────────────────────

def detect_permission_deny(
    error_records: Iterable[Dict[str, Any]],
    pj_slug: str,
) -> List[WeakSignal]:
    """errors.jsonl の permission_denied レコードから弱シグナルを作る。

    Args:
        error_records: errors.jsonl をパースした dict のイテラブル
        pj_slug:       照合用 slug（呼び出し側が確定して渡す）
    """
    out: List[WeakSignal] = []
    for rec in error_records:
        if rec.get("type") != "permission_denied":
            continue
        provenance = {
            "detector": "permission_deny",
            "tool_name": rec.get("tool_name", ""),
            "tool_input_summary": rec.get("tool_input_summary", ""),
            "denial_reason": rec.get("denial_reason", ""),
            "timestamp": rec.get("timestamp", ""),
        }
        out.append(
            WeakSignal(
                channel="permission_deny",
                provenance=provenance,
                detected_at=now_iso(),
                session_id=str(rec.get("session_id") or ""),
                pj_slug=pj_slug,
            )
        )
    return out


# ── ① 直後手編集 / ④ Esc 中断（transcript 直読）────────────────────

def _iter_jsonl(path: Path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for idx, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(obj, dict):
                    yield idx + 1, obj  # 1-index line_no
    except OSError:
        return


def _user_text_blocks(obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _tool_result_text(block: Dict[str, Any]) -> str:
    """tool_result block の text を結合して返す（str / list 両対応）。"""
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(
            b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def detect_transcript_signals(
    transcript_path: Path,
    pj_slug: str,
) -> List[WeakSignal]:
    """1 つの transcript jsonl から ① 直後手編集 と ④ Esc 中断を検出する。

    ① 直後手編集: ``tool_result`` の ``<tool_use_error>File has been modified since
       read...`` を検出。これは Claude が直前に read/write したファイルをユーザー
       （or linter）が外部編集した直後にのみ出る決定論シグナル。provenance に
       「user or linter」両方の可能性を明示する（捏造しない）。
    ④ Esc 中断: user メッセージ content の ``[Request interrupted`` text block。
    """
    out: List[WeakSignal] = []
    session_id = ""
    for line_no, obj in _iter_jsonl(transcript_path):
        if not session_id:
            sid = obj.get("sessionId")
            if sid:
                session_id = str(sid)
        for block in _user_text_blocks(obj):
            btype = block.get("type")
            if btype == "tool_result":
                txt = _tool_result_text(block)
                if _FILE_MODIFIED_MARKER in txt:
                    out.append(
                        WeakSignal(
                            channel="manual_edit_after_ai",
                            provenance={
                                "detector": "manual_edit_after_ai",
                                "source_path": str(transcript_path.resolve()),
                                "line_no": line_no,
                                "evidence": _FILE_MODIFIED_MARKER,
                                "attribution": "user_or_linter",
                            },
                            detected_at=now_iso(),
                            session_id=str(obj.get("sessionId") or session_id),
                            pj_slug=pj_slug,
                        )
                    )
            elif btype == "text":
                txt = block.get("text", "")
                if isinstance(txt, str) and _INTERRUPT_MARKER in txt:
                    out.append(
                        WeakSignal(
                            channel="esc_interrupt",
                            provenance={
                                "detector": "esc_interrupt",
                                "source_path": str(transcript_path.resolve()),
                                "line_no": line_no,
                                "evidence": txt.strip()[:80],
                            },
                            detected_at=now_iso(),
                            session_id=str(obj.get("sessionId") or session_id),
                            pj_slug=pj_slug,
                        )
                    )
    return out


# ── ③ 言い直し（utterances.db ベース）──────────────────────────────

def detect_rephrase(
    utterances: Sequence[Dict[str, Any]],
    pj_slug: str,
    threshold: float = REPHRASE_JACCARD_THRESHOLD,
) -> List[WeakSignal]:
    """連続する human 発話の高類似（言い直し）を検出する。

    入力は #430 の utterances（query_utterances の返り値。session_id, timestamp,
    line_no 順にソート済みであることを前提）。同一セッション内の隣接ペアのみ比較する。

    FP 除去（直交分離）: 機構ターン（dispatch テンプレ）が片方でも含まれるペアは除外。
    並列 agent 派遣の near-identical プロンプトが言い直しに誤検知される最大の FP 源
    （実コーパス dry-run で確認）。
    """
    from collections import defaultdict

    from similarity import jaccard_coefficient, tokenize

    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for u in utterances:
        buckets[str(u.get("session_id") or "")].append(u)

    out: List[WeakSignal] = []
    for sid, us in buckets.items():
        for a, b in zip(us, us[1:]):
            ta_text = str(a.get("text") or "")
            tb_text = str(b.get("text") or "")
            if _is_dispatch(ta_text) or _is_dispatch(tb_text):
                continue
            ta, tb = tokenize(ta_text), tokenize(tb_text)
            if len(ta) < REPHRASE_MIN_TOKENS or len(tb) < REPHRASE_MIN_TOKENS:
                continue
            sim = jaccard_coefficient(ta, tb)
            if sim < threshold:
                continue
            out.append(
                WeakSignal(
                    channel="rephrase",
                    provenance={
                        "detector": "rephrase",
                        "similarity": round(sim, 4),
                        "prev_line_no": a.get("line_no"),
                        "line_no": b.get("line_no"),
                        "prev_text": ta_text[:120],
                        "text": tb_text[:120],
                        "source_path": b.get("source_path", ""),
                    },
                    detected_at=now_iso(),
                    session_id=sid,
                    pj_slug=pj_slug or str(b.get("pj_slug") or ""),
                )
            )
    return out
