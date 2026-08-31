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
from reflect_apply_match import check_line_applied
from reflect_status_store import (
    is_valid_correction_record,
    update_status_at_logical_indices,
)
from reflect_utils import (
    read_all_memory_entries,
    read_auto_memory,
    suggest_auto_memory_topic,
    suggest_claude_file,
    suggest_paths_frontmatter,
)
from line_limit import check_line_limit, suggest_separation
from semantic_detector import detect_contradictions, validate_corrections
from similarity import jaccard_coefficient, tokenize

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
CORRECTIONS_FILE = Path.home() / ".claude" / "evolve-anything" / "corrections.jsonl"
ERRORS_FILE = Path.home() / ".claude" / "evolve-anything" / "errors.jsonl"

# promotion 閾値
PROMOTION_MIN_OCCURRENCES = 2
PROMOTION_MIN_AGE_DAYS = 14
# #184: reoccurrence / occurrences は correction_type バケット総数でなく
# message/extracted_learning の意味的類似度クラスタで数える。この閾値以上の
# Jaccard 係数で「同じ趣旨の指摘」とみなしクラスタにまとめる（layer_diagnose の
# MEMORY_DUPLICATE_JACCARD_THRESHOLD / regression_gate の _JACCARD_WARN_THRESHOLD と同値）。
PROMOTION_SIMILARITY_THRESHOLD = 0.5

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
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if is_valid_correction_record(record):
            records.append(record)
    return records


def extract_pending(records: list[dict]) -> list[dict]:
    """reflect_status が pending / promoted のレコードを抽出する。

    #475 §5.1: promoted（昇格済み・反映先未定＝「いまは反映しない」を選んだ保留）を
    落とすと、朝の設問からも reflect のバッチレビューからも永久に消える（P3/P4 の
    穴が形を変えて再発する）。
    """
    return [
        r for r in records
        if r.get("reflect_status", "pending") in ("pending", "promoted")
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


def _promotion_signal_text(record: dict) -> str:
    """昇格判定に使う代表テキスト（extracted_learning 優先・無ければ message）。"""
    return (record.get("extracted_learning") or record.get("message") or "").strip()


def _cluster_applied_by_similarity(
    applied: list[dict],
) -> list[list[dict]]:
    """applied な correction を message/extracted_learning の Jaccard 類似度で
    クラスタ化する（#184）。

    correction_type バケット総数でなく「同じ趣旨の指摘」単位で再発回数を数えるための
    決定論クラスタリング。各 record を既存クラスタ代表との Jaccard 係数が
    ``PROMOTION_SIMILARITY_THRESHOLD`` 以上なら合流、無ければ新規クラスタを作る
    （単一パス greedy・入力順で決定論）。
    """
    clusters: list[list[dict]] = []
    cluster_tokens: list[set] = []
    for r in applied:
        tokens = tokenize(_promotion_signal_text(r))
        placed = False
        for i, rep_tokens in enumerate(cluster_tokens):
            if jaccard_coefficient(tokens, rep_tokens) >= PROMOTION_SIMILARITY_THRESHOLD:
                clusters[i].append(r)
                placed = True
                break
        if not placed:
            clusters.append([r])
            cluster_tokens.append(tokens)
    return clusters


def find_promotion_candidates(
    all_records: list[dict],
    project_root: Path | None = None,
) -> list[dict]:
    """auto-memory 昇格候補を検出する。

    同じ趣旨の指摘（message/extracted_learning の類似度クラスタ）が2回以上再発、
    または14日以上経過で未矛盾 → 候補（#184）。correction_type バケット総数では数えない。
    """
    auto_memory = read_auto_memory()
    auto_content = "\n".join(e.get("content", "") for e in auto_memory).lower()

    # applied のみを message/extracted_learning の類似度でクラスタ化し、
    # occurrences = クラスタ内の重複除去件数（= 同じ趣旨の指摘の再発回数）とする。
    applied = [r for r in all_records if r.get("reflect_status") == "applied"]
    clusters = _cluster_applied_by_similarity(applied)

    candidates = []
    seen_messages = set()

    for cluster in clusters:
        # クラスタ内の一意 message 数を再発回数とする（同一 message の重複は数えない）
        cluster_messages = {r.get("message", "") for r in cluster}
        occurrences = len(cluster_messages)
        reoccurrence = occurrences >= PROMOTION_MIN_OCCURRENCES

        # 代表 record（クラスタ先頭）で message/type/age を表現する
        rep = cluster[0]
        msg = rep.get("message", "")
        if msg in seen_messages:
            continue
        seen_messages.add(msg)

        ctype = rep.get("correction_type", "")
        timestamp_str = rep.get("timestamp", "")

        # 経過日数チェック（クラスタ内で最も古い timestamp を採用）
        age_qualified = False
        oldest_ts = None
        for r in cluster:
            ts_str = r.get("timestamp", "")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            if oldest_ts is None or ts < oldest_ts:
                oldest_ts = ts
        if oldest_ts is not None:
            age_days = (datetime.now(timezone.utc) - oldest_ts).days
            age_qualified = age_days >= PROMOTION_MIN_AGE_DAYS

        if not (reoccurrence or age_qualified):
            continue

        # auto-memory に既に含まれていないか
        learning = (rep.get("extracted_learning") or msg).lower()
        if learning and learning in auto_content:
            continue

        candidates.append({
            "message": msg,
            "correction_type": ctype,
            "occurrences": occurrences,
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


def _rule_scope_identity(target_path: str) -> dict | None:
    """反映先ファイルの scope（"global_rule" / "project_rule"）を判定する（#475 §8.2）。

    `~/.claude/rules/` 配下 → "global_rule"、`<repo>/.claude/rules/` 配下 → "project_rule"。
    どちらでもなければ None（revert 記録の対象外 — 新規スキル/hook 等は既存 §8.2 の
    スコープ外）。

    **root 解決は B レーンの `evolve_revert._target.resolve_target` と同じ単一ソースを使う**
    （`global_rules_root()` を直接 import）。`relative_path` は両 scope とも **rules root
    からの相対パス**で持つ（`resolve_target` は `root / relative_path` で解決するため — かつて
    project_rule 側だけ誤ってリポジトリルート相対の `.claude/rules/foo.md` を入れており、
    resolve 時に `<repo>/.claude/rules/.claude/rules/foo.md` という二重パスになって
    `REASON_NOT_FOUND` で必ず失敗していた。#475 rev2 で是正）。
    `repo_id` の解決は skill revert と同じ `evolve_decision_ids.repo_identity` を再利用する
    （新しい git 解決ロジックを増やさない）。
    """
    from evolve_revert._target import global_rules_root as _global_rules_root

    p = Path(target_path).expanduser()
    global_root = _global_rules_root()
    try:
        rel_to_global = p.resolve().relative_to(global_root.resolve())
        return {"scope": "global_rule", "repo_id": None, "relative_path": str(rel_to_global)}
    except (ValueError, OSError):
        pass

    from evolve_decision_ids import repo_identity as _repo_identity

    identity = _repo_identity(str(p))
    repo_id = identity.get("repo_id")
    rel = identity.get("relative_path") or ""
    rel_posix = rel.replace("\\", "/")
    prefix = ".claude/rules/"
    if repo_id and rel_posix.startswith(prefix):
        return {
            "scope": "project_rule",
            "repo_id": repo_id,
            # resolve_target 側は root=<repo_id>/.claude/rules として root/relative_path
            # で解決するため、prefix を落として rules root からの相対にする。
            "relative_path": rel_posix[len(prefix):],
        }
    return None


def record_rule_revert_entry(
    target_path: str,
    before_content: str,
    after_content: str,
    *,
    pj_slug: str | None = None,
) -> dict:
    """既存 rule ファイルへの追記を optimize_history へ記録する（#475 §8.2・決定4）。

    `bin/evolve-revert`（B レーン）が読む既存フォーマット（`revert_schema_version` /
    `revert_before_b64` / `relative_path` / `scope`）に合わせて1件 append する。scope が
    "global_rule"/"project_rule" と判定できない対象（新規スキル/hook 等）は記録しない。
    冪等性（同一 id の二重記録防止）は `append_history_entry_deduped` に委譲する。

    `before_content == ""` は新規ファイル作成として扱う（§8.2「やらないこと」— 新規ファイル
    作成の revert は実装しない。before 本文が存在しないため「不在」sentinel + schema version 2
    が要り、#467 §1.4 と同じ穴を開けることになる）。この場合 optimize_history へは書かず、
    **黙らせず** `{"recorded": False, "reason": "new_file_not_revertible"}` を返す。

    Returns:
        {"recorded": bool, "reason": str | None, "id": str | None, "written": bool | None}
    """
    identity = _rule_scope_identity(target_path)
    if identity is None:
        return {"recorded": False, "reason": "not_rule_scope", "id": None, "written": None}

    if before_content == "":
        return {
            "recorded": False,
            "reason": "new_file_not_revertible",
            "id": None,
            "written": None,
        }

    from evolve_decision_ids import (
        REVERT_ENCODING,
        REVERT_SCHEMA_VERSION,
        compress_before_for_revert,
        sha256 as _sha256,
    )
    from optimize_history_store import append_history_entry_deduped
    from pj_slug import resolve_pj_slug as _resolve_pj_slug

    before_sha = _sha256(before_content)
    after_sha = _sha256(after_content)
    before_b64, unavailable_reason = compress_before_for_revert(before_content)

    entry_id = "rule_apply_" + _sha256(
        f"{identity['scope']}\n{identity['relative_path']}\n{before_sha}"
    )[:16]
    entry = {
        "id": entry_id,
        # #512: `results_board.classify_decision` は「フィールドの実在と bool 型を優先」で
        # 正規化する（source 文字列では判定しない）。この writer は「利用者が 4 択で 1)/2) を
        # 選び、rule ファイルへの追記が実際に行われた後」にのみ 1 件 append されるため、
        # 人間が明示承認した採用そのもの。キーを持たないと pending に落ち、
        # `bin/evolve-revert --list`（entry_id を知る唯一の導線）から脱落する。
        "human_accepted": True,
        "skill_name": identity["relative_path"],
        "scope": identity["scope"],
        "relative_path": identity["relative_path"],
        "repo_id": identity["repo_id"],
        "after_sha": after_sha,
        "revert_schema_version": REVERT_SCHEMA_VERSION,
        "revert_encoding": REVERT_ENCODING,
        "revert_generation": 0,
    }
    if before_b64 is not None:
        entry["revert_before_b64"] = before_b64
    else:
        entry["revert_unavailable_reason"] = unavailable_reason

    slug = pj_slug or _resolve_pj_slug(str(Path.cwd()))
    written_entry, written = append_history_entry_deduped(entry, slug)
    return {
        "recorded": True,
        "reason": None,
        "id": written_entry.get("id", entry_id),
        "written": written,
    }


def update_reflect_status(
    filepath: Path,
    indices: list[int],
    status: str,
    *,
    target_path: str | None = None,
    draft_line: str | None = None,
) -> dict:
    """corrections.jsonl の指定行の reflect_status を更新する。

    status="applied" のときのみ target_path/draft_line が必須（無ければ ValueError）。
    §6.2 の正規化規則（reflect_apply_match.check_line_applied）で target_path を読み、
    draft_line の完全一致を確認してから書く。不一致なら reflect_status は変更せず
    {"status": "apply_unverified", ...} を返す（#475 §6.1 — 黙って成功にしない）。
    status="skipped" 等は従来どおり target_path/draft_line 不要（既存 --skip-all は
    無改修で動く）。

    Args:
        filepath: corrections.jsonl のパス。
        indices: 更新対象の行インデックス（0始まり、全レコード中の位置）。
        status: 新しい reflect_status 値。
        target_path: status="applied" のときのみ必須。反映先ファイルのパス。
        draft_line: status="applied" のときのみ必須。起草行の全文（照合用）。

    Returns:
        {"status": "applied" | "apply_unverified" | <status>, "target": str | None,
         "reason": str | None}
    """
    if status == "applied":
        if target_path is None or draft_line is None:
            raise ValueError(
                "update_reflect_status(status='applied') には target_path と "
                "draft_line が必須です（#475 §6.1）"
            )
        match = check_line_applied(Path(target_path), draft_line)
        if not match["matched"]:
            return {
                "status": "apply_unverified",
                "target": target_path,
                "reason": match["reason"],
            }

    if not indices:
        return {"status": status, "target": target_path, "reason": None}

    updated = update_status_at_logical_indices(filepath, indices, status)
    if updated != len(indices):
        return {
            "status": "error",
            "target": target_path,
            "reason": (
                "reflect_status update count mismatch: "
                f"expected {len(indices)}, updated {updated}"
            ),
        }
    return {"status": status, "target": target_path, "reason": None}


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
    parser.add_argument("--apply", type=str, default=None, metavar="SOURCE_CORRECTION_ID",
                        help="#475 §6.1: 指定 correction（make_source_correction_id 形式の"
                             "source_correction_id）を、--target-path に該当行が実在するか"
                             "確認してから applied にする。1呼び出し=1 correction 固定")
    parser.add_argument("--skip", type=str, default=None, metavar="SOURCE_CORRECTION_ID",
                        help="#514: 指定 correction（make_source_correction_id 形式の"
                             "source_correction_id）を skipped にする（修正在庫の『もう出さない』）。"
                             "--apply と同じ --dry-run 規約。既に applied 済みのレコードは上書きしない")
    parser.add_argument("--target-path", type=str, default=None,
                        help="--apply: 反映先ファイル（絶対 or リポジトリ相対）")
    parser.add_argument("--draft-line-file", type=str, default=None,
                        help="--apply: 起草行の全文を含む一時ファイルのパス（シェル引数の"
                             "クォート/エスケープ事故を避けるため、テキストを直接渡さない）")
    parser.add_argument("--before-content-file", type=str, default=None,
                        help="--apply: Edit 前に読んだ --target-path の全文を含むファイル"
                             "（任意）。渡すと #475 §8.2 の取り消し記録（optimize_history）を"
                             "残す。省略時は applied 判定のみ行い記録は残さない")
    parser.add_argument("--show-weak-signals", action="store_true",
                        help="weak_signals レーンの未昇格レコードを view-only 表示（診断・#431/#432 二層化。"
                             "確認/昇格は evolve の今日の修正確認 phase へ・#117）")
    parser.add_argument("--weak-channel", type=str, default=None,
                        help="--show-weak-signals/--promote-weak: チャネルで絞る（例: llm_judge）")
    parser.add_argument("--context", type=str, default=None,
                        help="--show-weak-signals: 現在の文脈（自由文）。関連度ゲートで無関係な"
                             "過去経験を suppressed に分離し、関連度スコア付きで提示する（#565）")
    parser.add_argument("--relevance-threshold", type=float, default=None,
                        help="--context: 関連度ゲートの閾値（既定は校正済み 0.5・#565）")
    # #541 codex M1: --promote-weak/--reject-weak/--already-reflected-weak は同一
    # signal_key に対する三値択一の decision（promoted/rejected/already_reflected）であり、
    # 同時指定は意味が矛盾する。旧実装は if 分岐順（promote が最優先）で decide していたため、
    # `--promote-weak K --already-reflected-weak K` のように両方渡すと黙って promote が勝ち、
    # 「既に反映済みは promote を呼ばない」という設計の中核を破っていた（corrections.jsonl に
    # reflect_status=promoted のレコードが作られ、#514 在庫レーンへ再提示バグが引っ越す）。
    # argparse の mutually exclusive group で同時指定自体を拒否する（分岐順に依存しない）。
    _weak_decision_group = parser.add_mutually_exclusive_group()
    _weak_decision_group.add_argument("--promote-weak", type=str, default=None,
                        help="指定 signal_key（カンマ区切り）の weak_signal を corrections へ昇格")
    _weak_decision_group.add_argument("--reject-weak", type=str, default=None,
                        help="指定 signal_key（カンマ区切り）を『いいえ』として既読化する"
                             "（#409・weak_signal 自体は昇格しない）")
    _weak_decision_group.add_argument("--already-reflected-weak", type=str, default=None,
                        help="指定 signal_key（カンマ区切り）を『既に反映済み』として既読化する"
                             "（#541 D-2・weak_signal 自体は昇格しない。--promote-weak は"
                             "corrections.jsonl に reflect_status=promoted の新規レコードを"
                             "作るため、#514 修正在庫レーンに再提示バグが引っ越す。ここでは"
                             "record_reviewed のみ呼ぶ）")
    parser.add_argument("--pj", type=str, default=None,
                        help="--promote-weak/--reject-weak/--already-reflected-weak: "
                             "既読記録の pj_slug（未指定は現在の PJ を"
                             " pj_slug.resolve_pj_slug で解決）")
    parser.add_argument("--weak-signals-file", type=str, default=None,
                        help="weak_signals.jsonl のパス（テスト用）")
    parser.add_argument("--revoke-idiom", type=str, default=None,
                        help="指定 idiom_key の自動昇格を取り消す（ADR-047 安全弁③・confirmed=False + corrections invalidate）")
    parser.add_argument("--idioms-file", type=str, default=None,
                        help="correction_idioms.jsonl のパス（テスト用）")
    parser.add_argument("--project-dir", type=str, default=None,
                        help="対象プロジェクトディレクトリ（明示指定。優先順位: 本フラグ > "
                             "env CLAUDE_PROJECT_DIR > cwd。単一 cwd から他 PJ の project_dir を"
                             "渡すバッチ経路 — evolve-fleet propose 等 — で project_path の混入を防ぐ・#400）")
    parser.add_argument("--project-path", type=str, default=None,
                        help="--promote-weak: 昇格レコードの project_path を明示指定（未指定は"
                             "従来どおり現在 PJ = --project-dir/env CLAUDE_PROJECT_DIR/cwd）。"
                             "SessionStart の global 改善案（複数 PJ 由来の signal を束ねた提案）は"
                             "単一 cwd から他 PJ の signal_key を昇格するため、現在 PJ への"
                             "固定帰属を避けて origin PJ の絶対パスを渡す用途（#412 [Must]4）")
    args = parser.parse_args()

    corrections_file = Path(args.corrections_file) if args.corrections_file else CORRECTIONS_FILE
    current_project = args.project_dir or os.environ.get("CLAUDE_PROJECT_DIR")
    project_root = Path(current_project) if current_project else Path.cwd()
    weak_signals_file = Path(args.weak_signals_file) if args.weak_signals_file else None
    idioms_file = Path(args.idioms_file) if args.idioms_file else None

    # --show-weak-signals: weak_signals レーンの未昇格レコードを表示（#431/#432 二層化）
    # --context が渡されたら relevance_gate（#565）で「現在の文脈」と無関係な過去経験を
    # suppressed に分離し、関連度スコア付きで提示する。FinAcumen 流の「校正済み閾値を
    # 超えた経験だけ提案根拠に出し、無関係メモリは明示抑制（黙って消さない）」を実現する。
    if args.show_weak_signals:
        from correction_semantic import promote as _cs_promote
        unp = _cs_promote.read_unpromoted(
            weak_signals_path=weak_signals_file, channel=args.weak_channel
        )
        # #117: reflect の weak レーンは view-only 診断。日次の確認・昇格の主入口は
        # evolve の「今日の修正確認」phase（daily_review・Step 6.2）に一本化されており、
        # reflect はここから昇格をドライブしない。どこで昇格するかを出力自体が示すよう、
        # 両出力経路（plain / --context）に昇格入口 hint を機械可読に添える（散文 SKILL.md
        # だけに頼らない）。手動昇格の低レベルプリミティブ --promote-weak は残す（evolve の
        # daily_review / bootstrap も内部でこの CLI を使う共有プリミティブ）。
        _promotion_hint = (
            "確認・昇格は /evolve-anything:evolve の『今日の修正確認』phase（Step 6.2）に一本化"
            "（reflect は view-only 診断・#117）。full-backlog の手動/スクリプト昇格は "
            "evolve-reflect --promote-weak <signal_key,...>（evolve も内部で使う共有プリミティブ）。"
        )
        if args.context is not None:
            from correction_semantic import relevance_gate as _rg
            thr = (
                args.relevance_threshold
                if args.relevance_threshold is not None
                else _rg.RELEVANCE_THRESHOLD
            )
            gated = _rg.gate_candidates(args.context, unp, threshold=thr)
            print(json.dumps({
                "status": "weak_signals",
                # 関連度ゲート適用後: 閾値を超えた経験だけが提案根拠（kept）に出る。
                "unpromoted": gated["kept"],
                "count": len(gated["kept"]),
                # 無関係な経験は黙って消さず suppressed に分離して理由を残す。
                "suppressed": gated["suppressed"],
                "channel": args.weak_channel,
                "relevance_gate": _rg.summarize_gate(gated),
                "promotion_hint": _promotion_hint,
            }, ensure_ascii=False, indent=2))
            return
        print(json.dumps({
            "status": "weak_signals",
            "unpromoted": unp,
            "count": len(unp),
            "channel": args.weak_channel,
            "promotion_hint": _promotion_hint,
        }, ensure_ascii=False, indent=2))
        return

    # --promote-weak: 指定 signal_key の weak_signal を corrections へ昇格（人間確認後）
    # 昇格成功後、当該シグナルに対応する idiom を confirmed=True にマークする（#463 配線漏れ修正）。
    # この confirmed 化が idiom_autopromote の発火ゲート（ADR-047 雪崩防止不変条件）を満たす。
    # CLI に閉じる（ADR-045）: SKILL.md の散文に手順を足すのでなく、--promote-weak が confirmed まで
    # 一気通貫で行う。dry_run はどのストアにも書かない（最下層 write ゲート）。
    if args.promote_weak:
        from correction_semantic import promote as _cs_promote
        from correction_semantic import store as _cs_store
        from correction_semantic.daily_review import record_reviewed as _record_reviewed
        from pj_slug import resolve_pj_slug as _resolve_pj_slug
        keys = [k.strip() for k in args.promote_weak.split(",") if k.strip()]
        # #412 [Must]4: --project-path が明示されればそれを優先する。global 改善案（複数 PJ
        # 由来の signal を束ねた提案）は単一 cwd（現在 PJ）から他PJの signal_key を昇格するため、
        # --project-dir/env/cwd による現在 PJ への固定帰属をここだけ迂回できるようにする。
        promote_project_path = args.project_path or current_project or str(project_root)
        res = _cs_promote.promote_signals(
            keys,
            weak_signals_path=weak_signals_file,
            corrections_path=corrections_file if args.corrections_file else None,
            project_path=promote_project_path,
            dry_run=args.dry_run,
        )
        # 承認したシグナルに対応する idiom を confirmed 化（signal→idiom は provenance 突合）。
        # #412 round2 [Must]B: 渡すのは実際に昇格できた key（res["promoted_keys"]）だけに
        # 限定する。要求 keys 全件を渡すと expired 等で昇格に失敗した key の idiom まで
        # confirmed 化され、idiom_autopromote（ADR-047）の発火ゲートを誤って開いてしまう。
        idiom_key_map = _cs_promote.resolve_idiom_keys_for_signals(
            res["promoted_keys"], weak_signals_path=weak_signals_file, idioms_path=idioms_file,
        )
        confirm_res = _cs_store.confirm_idioms(
            list(set(idiom_key_map.values())),
            path=idioms_file,
            confirmed_by="reflect_promote_weak",
            dry_run=args.dry_run,
        )
        # #409: 「はい」を既読ストアへ記録する。SessionStart の改善案提示
        # （daily.proposal_digest.build_session_proposals）はこの既読集合を見て再提示を止める
        # （promoted フラグだけでは同日中の digest 再提示を防げない — digest は日次生成のスナップ
        # ショットで再生成まで promoted 反映を待つため）。
        # #412 [Must]5: 既読化するのは実際に昇格できた key（res["promoted_keys"]）だけにする。
        # 従来は要求 keys 全件を既読化していたため、日次スナップショットの stale・キー不在・
        # TTL 失効等で promoted=0 になっても既読化され、以後の digest から永久に外れる
        # silent failure だった（「採用したつもりが何も昇格せず二度と出ない」）。
        pj_slug_value = args.pj or _resolve_pj_slug(current_project or str(project_root))
        _record_reviewed(res["promoted_keys"], pj_slug_value, decision="promoted", dry_run=args.dry_run)
        # #476-4: 昇格後の corrections_human_allpj を返す（growth_report の promoted_today は対話前
        # スナップショットで固定されるため、CLI が更新後カウントを返し assistant が最新値を
        # 表示できるようにする）。dry_run は corrections に書かないため pre-promotion 値のまま。
        # 同じ corrections ファイル（CLI 指定 or DATA_DIR fallback）を読んで human-source を数える。
        # #557: キー名を corrections_human_allpj に変更 — これは全PJ集計値（DATA_DIR 内の
        # corrections.jsonl 全件対象）であり、per-PJ の growth_report["corrections_human"] とは
        # 別物。混同すると 41/10 のような不整合表示になる（#526-1 の事故再発防止）。
        from correction_semantic.provenance_weight import count_human_corrections
        if args.corrections_file:
            _corr_path = corrections_file
        else:
            import rl_common as _rc
            _corr_path = Path(_rc.DATA_DIR) / "corrections.jsonl"
        _human = count_human_corrections(load_corrections(_corr_path))
        print(json.dumps({
            "status": "promoted_weak",
            **res,
            "confirmed_idioms": confirm_res.get("confirmed", 0),
            "corrections_human_allpj": _human,  # 全PJ集計値（per-PJの growth_report.corrections_human とは別物 — #557）
        }, ensure_ascii=False, indent=2))
        return

    # --reject-weak: 指定 signal_key を「いいえ」として既読化する（#409）。weak_signal 自体は
    # promoted にしない（却下＝昇格しない、が正しい仕様）。record_reviewed が
    # correction_review_seen.jsonl へ decision="rejected" を追記し、以後 daily_review /
    # SessionStart 提示（exclude_reviewed・#185 と同じ predicate）から除外する。
    if args.reject_weak is not None:
        from correction_semantic.daily_review import record_reviewed as _record_reviewed
        from pj_slug import resolve_pj_slug as _resolve_pj_slug
        keys = [k.strip() for k in args.reject_weak.split(",") if k.strip()]
        pj_slug_value = args.pj or _resolve_pj_slug(current_project or str(project_root))
        res = _record_reviewed(keys, pj_slug_value, decision="rejected", dry_run=args.dry_run)
        print(json.dumps({
            "status": "rejected_weak",
            "pj_slug": pj_slug_value,
            **res,
        }, ensure_ascii=False, indent=2))
        return

    # --already-reflected-weak: 指定 signal_key を「既に反映済み」として既読化する（#541 D-2）。
    # weak_signal 自体は昇格しない（corrections.jsonl へは何も書かない）。今回の反映は reflect
    # フローの外（対話中の手書き Edit/Write 等）で起きたため、フロー内の promote 記録は実体を
    # 伴わない。--promote-weak は reflect_status="promoted" の correction を新規作成し、
    # #514 の修正在庫レーンが「まだ反映されていません」と蒸し返す（再提示バグの引っ越し）ため
    # 使わない。record_reviewed のみが実体。
    if args.already_reflected_weak is not None:
        from correction_semantic.daily_review import record_reviewed as _record_reviewed
        from pj_slug import resolve_pj_slug as _resolve_pj_slug
        keys = [k.strip() for k in args.already_reflected_weak.split(",") if k.strip()]
        pj_slug_value = args.pj or _resolve_pj_slug(current_project or str(project_root))
        res = _record_reviewed(keys, pj_slug_value, decision="already_reflected", dry_run=args.dry_run)
        print(json.dumps({
            "status": "already_reflected_weak",
            "pj_slug": pj_slug_value,
            **res,
        }, ensure_ascii=False, indent=2))
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

    # --apply: 指定 correction を反映先ファイルへの実在確認後に applied にする（#475 §6.1）。
    # promote.py / SKILL.md 手順が直接 "applied" を書く迂回口を塞ぐ唯一の入口。
    if args.apply:
        if not args.target_path or not args.draft_line_file:
            print(json.dumps({
                "status": "error",
                "message": "--target-path と --draft-line-file が必要です",
            }, ensure_ascii=False))
            sys.exit(1)
        # #475 rev2: 反映先が rules 配下（global_rule/project_rule）なら
        # --before-content-file を必須にする。省略を許すと revert 記録が黙って
        # スキップされ、「1コマンドで戻せる」という約束が画面上は成功したまま破られる
        # （§6.1 が塞ごうとしている失敗そのもの）。rules 配下でない target（skill 等）は
        # revert 記録の対象外なので従来どおり不要。
        rule_identity = _rule_scope_identity(args.target_path)
        if rule_identity is not None and not args.before_content_file:
            print(json.dumps({
                "status": "error",
                "message": (
                    "--before-content-file が必要です（反映先が rules 配下のため"
                    "取り消し記録に必須・#475 §8.2）"
                ),
            }, ensure_ascii=False))
            sys.exit(1)
        draft_line_path = Path(args.draft_line_file)
        if not draft_line_path.exists():
            print(json.dumps({
                "status": "error",
                "message": f"--draft-line-file が見つかりません: {draft_line_path}",
            }, ensure_ascii=False))
            sys.exit(1)
        draft_line = draft_line_path.read_text(encoding="utf-8").rstrip("\n")

        all_records = load_corrections(corrections_file)
        target_index = None
        for i, r in enumerate(all_records):
            sid = r.get("session_id", "")
            ts = r.get("timestamp", "")
            if sid and ts and make_source_correction_id(sid, ts) == args.apply:
                target_index = i
                break
        if target_index is None:
            print(json.dumps({
                "status": "not_found",
                "message": "対象 correction が見つかりません",
            }, ensure_ascii=False))
            sys.exit(1)

        if args.dry_run:
            # --dry-run では一切書かない（既存 dry-run ゲート貫通規約）。
            print(json.dumps({
                "status": "dry_run",
                "target": args.target_path,
                "source_correction_id": args.apply,
            }, ensure_ascii=False, indent=2))
            return

        result = update_reflect_status(
            corrections_file, [target_index], "applied",
            target_path=args.target_path, draft_line=draft_line,
        )
        # #475 §8.2/rev2: 反映先が rules 配下（= rule_identity is not None）で applied に
        # なったときは、必ず revert 記録を試みる（--before-content-file は上で必須化済み）。
        # 新規ファイル作成（before が空）は §8.2「やらないこと」どおり revert 未対応を
        # 黙らせず明示する。rules 配下でない target は revert 記録の対象外。
        if result.get("status") == "applied" and rule_identity is not None:
            before_path = Path(args.before_content_file)
            if not before_path.exists():
                print(json.dumps({
                    "status": "error",
                    "message": f"--before-content-file が見つかりません: {before_path}",
                }, ensure_ascii=False))
                sys.exit(1)
            before_content = before_path.read_text(encoding="utf-8")
            after_content = Path(args.target_path).read_text(encoding="utf-8")
            from pj_slug import resolve_pj_slug as _resolve_pj_slug_cli
            revert_res = record_rule_revert_entry(
                args.target_path, before_content, after_content,
                pj_slug=_resolve_pj_slug_cli(current_project or str(project_root)),
            )
            result["revert_recorded"] = revert_res["recorded"]
            if not revert_res["recorded"]:
                result["revert_reason"] = revert_res["reason"]
        print(json.dumps({
            "source_correction_id": args.apply,
            **result,
        }, ensure_ascii=False, indent=2))
        return

    # --skip: 指定 correction を skipped にする（#514・修正在庫の『もう出さない』）。
    # --apply と対称の同定（make_source_correction_id）・--dry-run 規約に合わせる。
    # 既に applied 済みのレコードは上書きしない（在庫UI経由で反映済みを誤って
    # skipped に巻き戻さない安全弁）。
    if args.skip:
        all_records = load_corrections(corrections_file)
        target_index = None
        for i, r in enumerate(all_records):
            sid = r.get("session_id", "")
            ts = r.get("timestamp", "")
            if sid and ts and make_source_correction_id(sid, ts) == args.skip:
                target_index = i
                break
        if target_index is None:
            print(json.dumps({
                "status": "not_found",
                "message": "対象 correction が見つかりません",
            }, ensure_ascii=False))
            sys.exit(1)

        if all_records[target_index].get("reflect_status") == "applied":
            print(json.dumps({
                "status": "already_applied",
                "source_correction_id": args.skip,
                "message": "既に applied 済みのため skipped で上書きしません",
            }, ensure_ascii=False, indent=2))
            return

        if args.dry_run:
            # --dry-run では一切書かない（--apply と同じ dry-run ゲート貫通規約）。
            print(json.dumps({
                "status": "dry_run",
                "source_correction_id": args.skip,
            }, ensure_ascii=False, indent=2))
            return

        result = update_reflect_status(corrections_file, [target_index], "skipped")
        print(json.dumps({
            "source_correction_id": args.skip,
            **result,
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
        # pending + promoted のインデックスを特定（全レコード中の位置。#475 §5.1）
        pending_indices = [
            i for i, r in enumerate(all_records)
            if r.get("reflect_status", "pending") in ("pending", "promoted")
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
