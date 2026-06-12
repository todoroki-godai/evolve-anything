#!/usr/bin/env python3
"""reflect スキルのメインスクリプト。

corrections.jsonl から pending corrections を抽出し、
プロジェクトフィルタ・重複検出・ルーティング提案を行い JSON を出力する。
"""
import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# プラグインルートを解決して import パスに追加
from plugin_root import PLUGIN_ROOT
_plugin_root = PLUGIN_ROOT
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))

from memory_temporal import make_source_correction_id
from reflect_utils import (
    read_all_memory_entries,
    read_auto_memory,
    suggest_auto_memory_topic,
    suggest_claude_file,
    suggest_paths_frontmatter,
)
from line_limit import check_line_limit, suggest_separation
from semantic_detector import detect_contradictions, validate_corrections
from similarity import tokenize

from rl_common import cleanup_false_positives

try:
    from episodic_retriever import find_episodic_duplicates, promote_to_episodic
    _HAS_EPISODIC = True
except ImportError:
    _HAS_EPISODIC = False

    def find_episodic_duplicates(*_, **__):  # type: ignore[misc]
        return []

    def promote_to_episodic(*_, **__) -> bool:  # type: ignore[misc]
        return False

# corrections.jsonl / errors.jsonl のデフォルトパス
CORRECTIONS_FILE = Path.home() / ".claude" / "rl-anything" / "corrections.jsonl"
ERRORS_FILE = Path.home() / ".claude" / "rl-anything" / "errors.jsonl"

# promotion 閾値
PROMOTION_MIN_OCCURRENCES = 2
PROMOTION_MIN_AGE_DAYS = 14

# memory update candidates 閾値
MIN_KEYWORD_MATCH = 3
_MEMORY_STOP_WORDS = frozenset({
    # 英語一般語
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "to", "of", "in", "on", "at", "for", "with", "and", "or",
    "not", "no", "it", "its", "that", "this", "these", "those",
    "from", "by", "as", "if", "but", "so", "do", "does", "did",
    "has", "have", "had", "will", "would", "can", "could",
    "should", "may", "might", "shall",
    # 短い技術汎用語
    "file", "code", "run", "set", "get", "add", "use", "new",
})


def calculate_importance_score(correction: dict) -> float:
    """correction レコードの重要度スコアを計算する。

    heuristic: confidence × max(0, 1 - elapsed_days / decay_days)
    結果は [0.0, 1.0] に clamp する。

    LLM 呼び出しなしの純粋関数。外部依存なし。

    Args:
        correction: corrections.jsonl の1件のレコード
    Returns:
        float: 重要度スコア [0.0, 1.0]
    """
    try:
        confidence = float(correction.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    decay_days = correction.get("decay_days", 90)
    timestamp_str = correction.get("timestamp", "")

    if not timestamp_str or decay_days <= 0:
        return min(1.0, max(0.0, confidence))

    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        elapsed_days = (now - ts).total_seconds() / 86400
        decay_factor = max(0.0, 1.0 - elapsed_days / decay_days)
        score = float(confidence) * decay_factor
        return min(1.0, max(0.0, score))
    except (ValueError, TypeError):
        return float(confidence)


def load_corrections(filepath: Path = CORRECTIONS_FILE) -> list[dict]:
    """corrections.jsonl を読み込む。"""
    if not filepath.exists():
        return []
    records = []
    for line in filepath.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def extract_pending(records: list[dict]) -> list[dict]:
    """reflect_status が pending のレコードのみ抽出する。"""
    return [
        r for r in records
        if r.get("reflect_status", "pending") == "pending"
    ]


def classify_project_scope(
    correction: dict,
    current_project: str | None = None,
) -> str:
    """correction のプロジェクトスコープを分類する。

    Returns:
        "same-project", "global-looking", "project-specific-other" のいずれか。
    """
    project_path = correction.get("project_path")

    # project_path が null → global-looking
    if project_path is None:
        return "global-looking"

    # 同一プロジェクト
    if current_project and _normalize_path(project_path) == _normalize_path(current_project):
        return "same-project"

    # "always"/"never"/"model名" → global-looking
    message = correction.get("message", "").lower()
    if re.search(r"\b(always|never)\b", message):
        return "global-looking"
    model_keywords = ["sonnet", "opus", "haiku", "claude", "gpt", "gemini"]
    if any(kw in message for kw in model_keywords):
        return "global-looking"

    # DB名やファイルパス含む → project-specific-other
    if _has_project_specific_content(correction.get("message", "")):
        return "project-specific-other"

    # デフォルト: 異なるプロジェクトだが汎用的 → global-looking
    return "global-looking"


def _normalize_path(path: str) -> str:
    """パスを正規化する。"""
    return os.path.normpath(os.path.expanduser(path))


def _has_project_specific_content(text: str) -> bool:
    """プロジェクト固有のコンテンツ（DB名、ファイルパス等）を含むか判定する。"""
    patterns = [
        r"\b\w+\.(db|sqlite|sqlite3|sql)\b",  # DB ファイル
        r"(/[a-zA-Z0-9_.-]+){3,}",  # 3階層以上のファイルパス
        r"\b(localhost|127\.0\.0\.1):\d+\b",  # ローカルサーバー
        r"\b\w+_(table|collection|bucket|queue)\b",  # リソース名
    ]
    for p in patterns:
        if re.search(p, text, re.IGNORECASE):
            return True
    return False


def detect_duplicates(
    corrections: list[dict],
    project_root: Path | None = None,
) -> list[dict]:
    """既存メモリエントリとの重複を検出する。

    各 correction に duplicate_found, duplicate_in を付与して返す。
    """
    memory_entries = read_all_memory_entries(project_root)
    all_content = "\n".join(e.get("content", "") for e in memory_entries).lower()

    result = []
    for c in corrections:
        msg = c.get("message", "").lower().strip()
        learning = (c.get("extracted_learning") or "").lower().strip()

        # 重複チェック: メッセージまたは学習内容がメモリに含まれるか
        dup_found = False
        dup_in = None

        check_texts = [t for t in [learning, msg] if t and len(t) > 10]
        for check in check_texts:
            if check in all_content:
                # どのファイルに含まれるか特定
                for entry in memory_entries:
                    if check in entry.get("content", "").lower():
                        dup_found = True
                        dup_in = entry.get("path")
                        break
            if dup_found:
                break

        updated = dict(c)
        updated["duplicate_found"] = dup_found
        updated["duplicate_in"] = dup_in
        result.append(updated)

    return result


def route_corrections(
    corrections: list[dict],
    project_root: Path | None = None,
) -> list[dict]:
    """各 correction にルーティング提案を付与する。"""
    result = []
    for c in corrections:
        suggestion = suggest_claude_file(c, project_root)
        updated = dict(c)
        if suggestion:
            suggested_file, _ = suggestion
            updated["suggested_file"] = suggested_file
        else:
            updated["suggested_file"] = None

        # routing_hint を設定
        scope = c.get("_scope", "same-project")
        if scope == "global-looking":
            updated["routing_hint"] = "global"
        elif scope == "project-specific-other":
            updated["routing_hint"] = "skip"
        else:
            updated["routing_hint"] = "project"

        # rule ファイルへの反映先の行数チェック
        sf = updated.get("suggested_file")
        if sf and ".claude/rules/" in sf and Path(sf).exists():
            current = Path(sf).read_text(encoding="utf-8")
            if not check_line_limit(sf, current):
                proposal = suggest_separation(sf, current)
                if proposal:
                    updated["line_limit_warning"] = (
                        f"反映先 {sf} は既に行数制限を超過しています。"
                        f"詳細を {proposal.reference_path} に分離することを検討してください。"
                    )

        # paths frontmatter 提案
        sf = updated.get("suggested_file")
        if sf and ".claude/rules/" in sf:
            paths_suggestion = suggest_paths_frontmatter(
                c.get("message", ""), project_root or Path.cwd()
            )
            if paths_suggestion is not None:
                updated["paths_suggestion"] = {
                    "patterns": paths_suggestion.patterns,
                    "confidence": paths_suggestion.confidence,
                    "note": "CC バージョンによっては globs: の方が信頼性が高い場合があります",
                }

        result.append(updated)
    return result


def find_promotion_candidates(
    all_records: list[dict],
    project_root: Path | None = None,
) -> list[dict]:
    """auto-memory 昇格候補を検出する。

    同一 correction_type が2回以上出現、または14日以上経過で未矛盾 → 候補。
    """
    auto_memory = read_auto_memory()
    auto_content = "\n".join(e.get("content", "") for e in auto_memory).lower()

    # correction_type ごとの出現回数
    type_counts: Counter = Counter()
    for r in all_records:
        ctype = r.get("correction_type")
        if ctype:
            type_counts[ctype] += 1

    candidates = []
    seen_messages = set()

    for r in all_records:
        if r.get("reflect_status") != "applied":
            continue

        msg = r.get("message", "")
        if msg in seen_messages:
            continue
        seen_messages.add(msg)

        ctype = r.get("correction_type", "")
        timestamp_str = r.get("timestamp", "")

        # 出現回数チェック
        reoccurrence = type_counts.get(ctype, 0) >= PROMOTION_MIN_OCCURRENCES

        # 経過日数チェック
        age_qualified = False
        if timestamp_str:
            try:
                ts = datetime.fromisoformat(timestamp_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - ts).days
                age_qualified = age_days >= PROMOTION_MIN_AGE_DAYS
            except (ValueError, TypeError):
                pass

        if not (reoccurrence or age_qualified):
            continue

        # auto-memory に既に含まれていないか
        learning = (r.get("extracted_learning") or msg).lower()
        if learning and learning in auto_content:
            continue

        candidates.append({
            "message": msg,
            "correction_type": ctype,
            "occurrences": type_counts.get(ctype, 0),
            "age_qualified": age_qualified,
            "suggested_topic": suggest_auto_memory_topic(msg),
        })

    return candidates


def find_memory_update_candidates(
    corrections: list[dict],
    project_root: Path | None = None,
) -> list[dict]:
    """corrections と既存 MEMORY エントリを照合し、更新候補を返す。

    duplicate_found=True の correction は除外。
    共通キーワード数が MIN_KEYWORD_MATCH 以上のペアを候補とする。
    """
    memory_entries = read_all_memory_entries(project_root)
    if not memory_entries or not corrections:
        return []

    # MEMORY エントリごとにトークン集合を事前計算
    memory_tokens = []
    for entry in memory_entries:
        content = entry.get("content", "")
        # 行ごとにトークン化して保持（マッチした行を特定するため）
        lines = content.splitlines()
        for i, line in enumerate(lines):
            tokens = tokenize(line) - _MEMORY_STOP_WORDS
            if len(tokens) >= 2:  # 短すぎる行は対象外
                memory_tokens.append({
                    "tokens": tokens,
                    "file": entry.get("path", ""),
                    "line_num": i + 1,
                    "line_text": line.strip(),
                })

    candidates = []
    seen = set()  # (correction_message, memory_file, line_num) で重複排除

    for c in corrections:
        # duplicate_found は除外
        if c.get("duplicate_found"):
            continue

        msg = c.get("message", "")
        learning = c.get("extracted_learning") or msg
        correction_tokens = tokenize(learning) - _MEMORY_STOP_WORDS

        if len(correction_tokens) < MIN_KEYWORD_MATCH:
            continue

        for mt in memory_tokens:
            common = correction_tokens & mt["tokens"]
            if len(common) >= MIN_KEYWORD_MATCH:
                key = (msg, mt["file"], mt["line_num"])
                if key in seen:
                    continue
                seen.add(key)
                candidates.append({
                    "correction_message": msg,
                    "memory_file": mt["file"],
                    "memory_line": mt["line_text"],
                    "memory_line_num": mt["line_num"],
                    "common_keywords": sorted(common),
                    "suggested_action": "update",
                })

    return candidates


def apply_semantic_validation(
    corrections: list[dict],
    model: str = "sonnet",
) -> list[dict]:
    """セマンティック検証を適用する。"""
    if not corrections:
        return corrections

    results = validate_corrections(corrections, model=model)

    validated = []
    for c, r in zip(corrections, results):
        updated = dict(c)
        updated["is_learning"] = r.get("is_learning", True)
        if r.get("extracted_learning"):
            updated["extracted_learning"] = r["extracted_learning"]
        validated.append(updated)
    return validated


def update_reflect_status(
    filepath: Path,
    indices: list[int],
    status: str,
) -> None:
    """corrections.jsonl の指定行の reflect_status を更新する。

    Args:
        filepath: corrections.jsonl のパス。
        indices: 更新対象の行インデックス（0始まり、全レコード中の位置）。
        status: 新しい reflect_status 値。
    """
    if not filepath.exists() or not indices:
        return

    lines = filepath.read_text(encoding="utf-8").splitlines()
    index_set = set(indices)

    updated_lines = []
    for i, line in enumerate(lines):
        if i in index_set and line.strip():
            try:
                record = json.loads(line)
                record["reflect_status"] = status
                updated_lines.append(json.dumps(record, ensure_ascii=False))
            except json.JSONDecodeError:
                updated_lines.append(line)
        else:
            updated_lines.append(line)

    filepath.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")


def load_recent_error_classes(
    errors_file: Path = ERRORS_FILE,
    session_ids: list[str] | None = None,
) -> dict:
    """errors.jsonl から error_class サマリを返す。

    corrections の session_ids と突合して関連エラーのみ抽出する。
    pitfall 生成プロンプトの behavioral コンテキストとして使用する。

    Returns:
        {"by_class": {"tech": 5, ...}, "by_type": {"rate_limit": 2, ...}}
    """
    from collections import defaultdict

    if not errors_file.exists():
        return {"by_class": {}, "by_type": {}}

    by_class: dict[str, int] = defaultdict(int)
    by_type: dict[str, int] = defaultdict(int)
    session_set = set(session_ids) if session_ids else None

    for line in errors_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        # session_ids フィルタ（指定時のみ）
        if session_set is not None:
            sid = rec.get("session_id", "")
            if sid not in session_set:
                continue

        ec = rec.get("error_class", "")
        et = rec.get("error_type", "")
        if ec:
            by_class[ec] += 1
        if et:
            by_type[et] += 1

    return {"by_class": dict(by_class), "by_type": dict(by_type)}


def analyze_tool_call_patterns(corrections: list[dict]) -> dict:
    """preceding_tool_calls から failure_patterns と preceding_sequences を集計する。

    failure_patterns: ツール失敗（success=False）→次ツールのシーケンス
    preceding_sequences: correction 前に頻出する 2-gram（success 問わず、count>=1）
    """
    from collections import Counter

    tool_total: Counter = Counter()
    tool_fail: Counter = Counter()
    fail_seq: Counter = Counter()   # 失敗→次ツール
    all_seq: Counter = Counter()    # correction 直前の 2-gram

    for c in corrections:
        calls = c.get("preceding_tool_calls") or []
        if not calls:
            continue
        for call in calls:
            tool = call.get("tool", "")
            if tool:
                tool_total[tool] += 1
                if not call.get("success", True):
                    tool_fail[tool] += 1

        for i in range(len(calls) - 1):
            if not calls[i].get("success", True):
                fail_seq[f"{calls[i]['tool']}(fail) → {calls[i+1]['tool']}"] += 1
            all_seq[f"{calls[i]['tool']} → {calls[i+1]['tool']}"] += 1

    failure_rate = {
        tool: round(tool_fail[tool] / total, 2)
        for tool, total in tool_total.items() if total > 0
    }

    return {
        "failure_patterns": [
            {"sequence": seq, "count": cnt}
            for seq, cnt in fail_seq.most_common()
        ],
        "preceding_sequences": [
            {"sequence": seq, "count": cnt}
            for seq, cnt in all_seq.most_common(10)
            if cnt >= 1
        ],
        "failure_rate_by_tool": failure_rate,
    }


def build_output(
    pending: list[dict],
    all_records: list[dict],
    project_root: Path | None = None,
    min_confidence: float = 0.85,
    apply_all: bool = False,
    contradictions: list[dict] | None = None,
) -> dict:
    """最終出力 JSON を構築する。"""
    if not pending:
        return {"status": "empty", "message": "未処理の修正はありません"}

    # episodic 層の重複候補を事前取得（3層メモリ: working → episodic → semantic）
    project_path = str(project_root) if project_root else None
    episodic_matches: dict[int, dict] = {}
    if _HAS_EPISODIC:
        for m in find_episodic_duplicates(pending, project_path):
            episodic_matches[m["correction_index"]] = m

    corrections_out = []
    for i, c in enumerate(pending):
        entry = {
            "index": i,
            "message": c.get("message", ""),
            "correction_type": c.get("correction_type", ""),
            "confidence": c.get("confidence", 0.5),
            "importance_score": calculate_importance_score(c),
            "routing_hint": c.get("routing_hint", "project"),
            "suggested_file": c.get("suggested_file"),
            "duplicate_found": c.get("duplicate_found", False),
            "duplicate_in": c.get("duplicate_in"),
            "extracted_learning": c.get("extracted_learning"),
        }

        # preceding_tool_calls: pitfall 生成に使う直前ツール呼び出し履歴
        preceding = c.get("preceding_tool_calls")
        if preceding:
            entry["preceding_tool_calls"] = preceding

        if c.get("line_limit_warning"):
            entry["line_limit_warning"] = c["line_limit_warning"]

        # provenance: session_id#timestamp 複合キー（memory 書き込み時に source_correction_ids に使う）
        sid = c.get("session_id", "")
        ts = c.get("timestamp", "")
        if sid and ts:
            entry["source_correction_id"] = make_source_correction_id(sid, ts)

        if apply_all:
            entry["apply"] = c.get("confidence", 0.5) >= min_confidence

        # episodic_context: 直近セッションで同様の修正が適用済みか
        if i in episodic_matches:
            em = episodic_matches[i]
            entry["episodic_context"] = {
                "id": em["episodic_id"],
                "content": em["episodic_content"],
                "days_ago": em["days_ago"],
                "score": em["score"],
            }
            # episodic で既出の場合は duplicate_in を上書き
            if not entry.get("duplicate_found"):
                entry["duplicate_in"] = "episodic"

        corrections_out.append(entry)

    # サマリ
    by_type: Counter = Counter()
    duplicates = 0
    for c in corrections_out:
        ctype = c.get("correction_type", "other")
        # type を大分類に
        from rl_common import CORRECTION_PATTERNS
        pattern_info = CORRECTION_PATTERNS.get(ctype, {})
        broad_type = pattern_info.get("type", "correction")
        by_type[broad_type] += 1
        if c.get("duplicate_found"):
            duplicates += 1

    # promotion candidates
    promotion = find_promotion_candidates(all_records, project_root)

    # memory update candidates
    memory_updates = find_memory_update_candidates(pending, project_root)

    # preceding_tool_calls パターン分析（pitfall 生成コンテキスト用）
    tool_call_analysis = analyze_tool_call_patterns(pending)

    # error_class サマリ（errors.jsonl から、同セッションの API エラー文脈を提供）
    session_ids = [c.get("session_id", "") for c in pending if c.get("session_id")]
    error_class_summary = load_recent_error_classes(session_ids=session_ids or None)

    output = {
        "status": "has_pending",
        "corrections": corrections_out,
        "promotion_candidates": promotion,
        "memory_update_candidates": memory_updates,
        "tool_call_analysis": tool_call_analysis,
        "error_class_summary": error_class_summary,
        "summary": {
            "total": len(corrections_out),
            "by_type": dict(by_type),
            "duplicates": duplicates,
        },
    }

    if contradictions:
        output["contradictions"] = contradictions

    return output


def build_view_output(pending: list[dict], all_records: list[dict]) -> dict:
    """--view モードの出力を構築する。"""
    if not pending:
        return {"status": "empty", "message": "未処理の修正はありません"}

    items = []
    now = datetime.now(timezone.utc)
    for i, c in enumerate(pending):
        ts_str = c.get("timestamp", "")
        age_days = None
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_days = (now - ts).days
            except (ValueError, TypeError):
                pass

        items.append({
            "index": i,
            "message": c.get("message", ""),
            "correction_type": c.get("correction_type", ""),
            "confidence": c.get("confidence", 0.5),
            "age_days": age_days,
        })

    return {
        "status": "view",
        "corrections": items,
        "total": len(items),
    }


def main():
    parser = argparse.ArgumentParser(description="reflect: corrections を分析・ルーティングする")
    parser.add_argument("--dry-run", action="store_true", help="分析のみ、更新しない")
    parser.add_argument("--view", action="store_true", help="pending 一覧を表示")
    parser.add_argument("--skip-all", action="store_true", help="全 pending を skipped に更新")
    parser.add_argument("--apply-all", action="store_true", help="高信頼度を自動 apply")
    parser.add_argument("--min-confidence", type=float, default=0.85, help="apply 閾値")
    parser.add_argument("--skip-semantic", action="store_true", help="セマンティック検証をスキップ")
    parser.add_argument("--model", default="sonnet", help="セマンティック検証のモデル")
    parser.add_argument("--corrections-file", type=str, default=None, help="corrections.jsonl のパス（テスト用）")
    parser.add_argument("--promote-episodic", action="store_true", help="指定 correction を episodic 層に昇格")
    parser.add_argument("--session-id", type=str, default=None, help="--promote-episodic: 昇格する correction の session_id")
    parser.add_argument("--timestamp", type=str, default=None, help="--promote-episodic: 昇格する correction の timestamp")
    parser.add_argument("--show-weak-signals", action="store_true",
                        help="weak_signals レーンの未昇格レコードを表示（#431/#432 の二層化）")
    parser.add_argument("--weak-channel", type=str, default=None,
                        help="--show-weak-signals/--promote-weak: チャネルで絞る（例: llm_judge）")
    parser.add_argument("--promote-weak", type=str, default=None,
                        help="指定 signal_key（カンマ区切り）の weak_signal を corrections へ昇格")
    parser.add_argument("--weak-signals-file", type=str, default=None,
                        help="weak_signals.jsonl のパス（テスト用）")
    parser.add_argument("--revoke-idiom", type=str, default=None,
                        help="指定 idiom_key の自動昇格を取り消す（ADR-047 安全弁③・confirmed=False + corrections invalidate）")
    parser.add_argument("--idioms-file", type=str, default=None,
                        help="correction_idioms.jsonl のパス（テスト用）")
    args = parser.parse_args()

    corrections_file = Path(args.corrections_file) if args.corrections_file else CORRECTIONS_FILE
    current_project = os.environ.get("CLAUDE_PROJECT_DIR")
    project_root = Path(current_project) if current_project else Path.cwd()
    weak_signals_file = Path(args.weak_signals_file) if args.weak_signals_file else None
    idioms_file = Path(args.idioms_file) if args.idioms_file else None

    # --show-weak-signals: weak_signals レーンの未昇格レコードを表示（#431/#432 二層化）
    if args.show_weak_signals:
        from correction_semantic import promote as _cs_promote
        unp = _cs_promote.read_unpromoted(
            weak_signals_path=weak_signals_file, channel=args.weak_channel
        )
        print(json.dumps({
            "status": "weak_signals",
            "unpromoted": unp,
            "count": len(unp),
            "channel": args.weak_channel,
        }, ensure_ascii=False, indent=2))
        return

    # --promote-weak: 指定 signal_key の weak_signal を corrections へ昇格（人間確認後）
    if args.promote_weak:
        from correction_semantic import promote as _cs_promote
        keys = [k.strip() for k in args.promote_weak.split(",") if k.strip()]
        res = _cs_promote.promote_signals(
            keys,
            weak_signals_path=weak_signals_file,
            corrections_path=corrections_file if args.corrections_file else None,
            project_path=current_project or str(project_root),
            dry_run=args.dry_run,
        )
        print(json.dumps({"status": "promoted_weak", **res}, ensure_ascii=False, indent=2))
        return

    # --revoke-idiom: 自動昇格の取り消し（ADR-047 安全弁③）
    # idiom を confirmed=False + revoked_at に戻し（テキスト単位で全 record）、その idiom テキスト
    # 由来の promoted_by="idiom_dict" corrections を invalidated=True に巻き戻す（同テキストの別
    # idiom_key 由来も含む）。count_human_corrections は invalidated を除外するためフェーズ進捗が
    # 正しく戻る。weak_signals の promoted=True は維持（再提示しない）。dry_run はファイル不変。
    if args.revoke_idiom:
        from correction_semantic import promote as _cs_promote
        from correction_semantic import store as _cs_store
        # 同テキストの全 idiom_key を先に解決（revoke で confirmed が消える前に取る）
        same_text_keys = _cs_store.idiom_keys_for_same_text(
            args.revoke_idiom, path=idioms_file
        )
        revoke_res = _cs_store.revoke_idiom(
            args.revoke_idiom, path=idioms_file, dry_run=args.dry_run
        )
        inval_res = _cs_promote.invalidate_idiom_corrections(
            same_text_keys or {args.revoke_idiom},
            corrections_path=corrections_file if args.corrections_file else None,
            dry_run=args.dry_run,
        )
        print(json.dumps({
            "status": "revoked_idiom",
            "idiom_key": args.revoke_idiom,
            "revoked": revoke_res.get("revoked", 0),
            "invalidated": inval_res.get("invalidated", 0),
            "dry_run": bool(args.dry_run),
        }, ensure_ascii=False, indent=2))
        return

    # --promote-episodic: 指定 session_id + timestamp の correction を episodic に昇格
    if args.promote_episodic:
        if not args.session_id or not args.timestamp:
            print(json.dumps({"status": "error", "message": "--session-id と --timestamp が必要です"}, ensure_ascii=False))
            sys.exit(1)
        all_records = load_corrections(corrections_file)
        matched = [
            r for r in all_records
            if r.get("session_id") == args.session_id and r.get("timestamp") == args.timestamp
        ]
        if not matched:
            print(json.dumps({"status": "not_found", "message": "対象 correction が見つかりません"}, ensure_ascii=False))
            sys.exit(1)
        corr = matched[0]
        corr.setdefault("project_path", current_project)
        ok = promote_to_episodic(corr)
        if ok:
            print(json.dumps({"status": "promoted", "session_id": args.session_id, "timestamp": args.timestamp}, ensure_ascii=False))
        else:
            print(json.dumps({"status": "error", "message": "episodic 昇格に失敗しました（DuckDB 未インストールまたは DB エラー）"}, ensure_ascii=False))
            sys.exit(1)
        return

    # 偽陽性の自動クリーンアップ（180日超）
    cleaned = cleanup_false_positives()
    if cleaned > 0:
        print(json.dumps({"cleanup": f"{cleaned} expired false positives removed"}, ensure_ascii=False), file=sys.stderr)

    # 全レコード読み込み
    all_records = load_corrections(corrections_file)
    pending = extract_pending(all_records)

    # --view モード
    if args.view:
        output = build_view_output(pending, all_records)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    # --skip-all モード
    if args.skip_all:
        if not pending:
            print(json.dumps({"status": "empty", "message": "未処理の修正はありません"}, ensure_ascii=False, indent=2))
            return
        # pending のインデックスを特定（全レコード中の位置）
        pending_indices = [
            i for i, r in enumerate(all_records)
            if r.get("reflect_status", "pending") == "pending"
        ]
        if not args.dry_run:
            update_reflect_status(corrections_file, pending_indices, "skipped")
        print(json.dumps({
            "status": "skipped_all",
            "count": len(pending_indices),
            "dry_run": args.dry_run,
        }, ensure_ascii=False, indent=2))
        return

    # セマンティック検証
    if not args.skip_semantic and pending:
        pending = apply_semantic_validation(pending, model=args.model)
        # is_learning=False を除外
        pending = [c for c in pending if c.get("is_learning", True)]

    # 矛盾検出
    contradictions = []
    if not args.skip_semantic and pending:
        contradictions = detect_contradictions(pending, model=args.model)
        if contradictions:
            print(json.dumps({"contradictions_warning": contradictions}, ensure_ascii=False), file=sys.stderr)

    # プロジェクトフィルタリング
    filtered = []
    for c in pending:
        scope = classify_project_scope(c, current_project)
        c["_scope"] = scope
        if scope == "project-specific-other":
            continue  # 他プロジェクト固有 → スキップ
        filtered.append(c)
    pending = filtered

    # 重複検出
    pending = detect_duplicates(pending, project_root)

    # ルーティング提案
    pending = route_corrections(pending, project_root)

    # 信頼度フィルタ
    pending = [c for c in pending if c.get("confidence", 0) >= args.min_confidence or args.apply_all]

    # 出力構築
    output = build_output(
        pending,
        all_records,
        project_root=project_root,
        min_confidence=args.min_confidence,
        apply_all=args.apply_all,
        contradictions=contradictions,
    )

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
