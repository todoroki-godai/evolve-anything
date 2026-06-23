"""triage_ledger — SKIP 判断に状態（TTL・再発カウンタ）を持たせる正準ストア（Issue #308）。

背景: `meta_quality_check`（meta_quality.py）は `low_reuse AND 重複候補あり → SKIP` を
**ステートレスに毎回ゼロ判定**しており、過去に同じ判断を下したことを覚えていない。その結果
毎日 evolve を回すたびに同じ SKIP 候補がノイズとして surface される。

本モジュールは判断を `DATA_DIR/triage_decisions/<slug>.jsonl` に永続化し、3層の見直し
トリガーで evolve/discover/trigger_engine の挙動を変える:

  ① 抑制（cooldown）        : SKIP 済み & クールダウン内 & 再発閾値未満
                              → 個別表示せず「SKIP 抑制 N件 ✓」の1行に畳む（沈黙≠評価）
  ② 再発エスカレーション      : times_skipped >= ESCALATE_N（窓内、既定3）
                              → SKIP→REVIEW に自動昇格（閾値か採用を見直すシグナル）
  ③ 賞味期限切れ（TTL）       : now > decided_at + ttl_days（既定45日）
                              → 🔄 として1回だけ強制再評価

slug 解決・per-slug 分離・サニタイズは optimize_history_store.py を範に踏襲する
（worktree 安全: `git rev-parse --git-common-dir` の親 basename。show-toplevel は不可。
ADR-031 / pitfall_worktree_slug_show_toplevel）。

レコードは candidate_key 単位の **last-write-wins**。append-only だが load 時に key で
collapse するため肥大化しても読み取り意味は1件に収束する。`compact()` で物理圧縮可能。

決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_PLUGIN_DATA_ENV = os.environ.get("CLAUDE_PLUGIN_DATA", "")
DATA_DIR = Path(_PLUGIN_DATA_ENV) if _PLUGIN_DATA_ENV else Path.home() / ".claude" / "evolve-anything"
LEDGER_ROOT = DATA_DIR / "triage_decisions"

# git repo 外（slug 解決不能）の保全先。
UNATTRIBUTED_SLUG = "_unattributed"

_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

# ── 3層トリガーの既定パラメータ ──────────────────────────────
DAY_SECONDS = 86400.0
# ① 抑制: SKIP を最後に見てからこの日数以内なら個別 surface せず畳む。
DEFAULT_COOLDOWN_DAYS = 7
# ② 再発エスカレーション: TTL 窓内でこの回数以上 SKIP したら REVIEW へ昇格。
ESCALATE_N = 3
# ③ TTL: この日数を過ぎたら判断を1回だけ強制再評価。
DEFAULT_TTL_DAYS = 45


# ─────────────────────────────────────────────────────────────────
# slug 解決（optimize_history_store と同パターン）
# ─────────────────────────────────────────────────────────────────
def _sanitize_slug(slug: str) -> str:
    cleaned = _SLUG_UNSAFE.sub("_", slug)
    return cleaned or UNATTRIBUTED_SLUG


def resolve_slug(cwd: Optional[Path] = None) -> str:
    """current（または指定 cwd の）project slug を返す。

    worktree 安全: `git rev-parse --git-common-dir` で本体 repo の .git を取り、
    その親ディレクトリ名を slug とする。git repo 外なら basename（#47）。

    #47: slug 導出は ``pj_slug.resolve_pj_slug`` に単一ソース化した（従来は本モジュールに
    git subprocess 解決を複製していた）。これで非git PJ の fallback が writer hot-path の
    ``pj_slug_fast``（basename）と一致し、別実装による non-git slug 食い違いを構造的に防ぐ。
    """
    from pj_slug import resolve_pj_slug

    return resolve_pj_slug(cwd)


# ─────────────────────────────────────────────────────────────────
# candidate_key 正規化
# ─────────────────────────────────────────────────────────────────
def candidate_key(skill_name: str) -> str:
    """スキル候補名を正規化したキーに変換する（lower + 空白圧縮）。

    SKIP 判断の同一性は「同じスキル候補か」で決まるので、表記ゆれを吸収する。
    """
    return re.sub(r"\s+", " ", (skill_name or "").strip().lower())


# ─────────────────────────────────────────────────────────────────
# ストア
# ─────────────────────────────────────────────────────────────────
def ledger_path(slug: str) -> Path:
    return LEDGER_ROOT / f"{_sanitize_slug(slug)}.jsonl"


def load_ledger(slug: str) -> Dict[str, Dict[str, Any]]:
    """slug の台帳を candidate_key→record の dict で読む。

    append-only ファイルを last-write-wins で collapse する。空行・壊れた JSON 行は
    スキップ。candidate_key 欠落レコードもスキップ。
    """
    path = ledger_path(slug)
    if not path.exists():
        return {}
    records: Dict[str, Dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = rec.get("candidate_key")
        if not key:
            continue
        records[key] = rec  # last-write-wins
    return records


def upsert_record(record: Dict[str, Any], slug: str) -> None:
    """1 レコードを追記する（last-write-wins、load 時に collapse される）。"""
    path = ledger_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def compact(slug: str) -> None:
    """append 累積を candidate_key ごと1行に物理圧縮する（肥大化対策）。"""
    records = load_ledger(slug)
    path = ledger_path(slug)
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(records[k], ensure_ascii=False) for k in sorted(records)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ─────────────────────────────────────────────────────────────────
# レコード生成
# ─────────────────────────────────────────────────────────────────
def _now() -> float:
    return time.time()


def _new_record(
    key: str,
    recommendation: str,
    reuse_rate: float,
    duplicate_candidates: List[str],
    *,
    now: float,
    ttl_days: int = DEFAULT_TTL_DAYS,
) -> Dict[str, Any]:
    return {
        "candidate_key": key,
        "recommendation": recommendation,
        "reuse_rate": reuse_rate,
        "duplicate_of": list(duplicate_candidates or []),
        "first_seen": now,
        "last_seen": now,
        "times_seen": 1,
        "times_skipped": 1 if recommendation == "SKIP" else 0,
        "decided_at": now,
        "ttl_days": ttl_days,
        "suppressed_until": now + DEFAULT_COOLDOWN_DAYS * DAY_SECONDS if recommendation == "SKIP" else 0.0,
    }


# ─────────────────────────────────────────────────────────────────
# 3層トリガー適用
# ─────────────────────────────────────────────────────────────────
def apply_ledger(
    meta_result: Dict[str, Any],
    *,
    slug: str,
    now: Optional[float] = None,
    cooldown_days: int = DEFAULT_COOLDOWN_DAYS,
    escalate_n: int = ESCALATE_N,
    ttl_days: int = DEFAULT_TTL_DAYS,
    persist: bool = True,
) -> Dict[str, Any]:
    """meta_quality_check 結果に台帳状態を反映した dict を返す（副作用: 台帳更新）。

    SKIP 以外の recommendation も記録するが、抑制・エスカレーションは SKIP のみ対象。

    Args:
        persist: False の場合、台帳への書き込み（upsert_record）を一切行わない。
            3層判定（抑制/再発エスカレーション/TTL 切れ）は既存レコードを読んで
            計算するため戻り値は不変。evolve --dry-run の「変更なし」契約を守るための
            ゲート（#308 dry-run 副作用バグ）。判定は load した既存レコードのみに
            依存し、その回で書く予定だったレコードには依存しないため、書き込みを
            スキップしても観測される decision は persist=True と一致する。

    Returns: meta_result の copy に以下を追加:
        - recommendation: 必要なら REVIEW に昇格
        - suppressed: bool（True なら個別 surface せず畳む）
        - ledger_status: "new" | "suppressed" | "escalated" | "ttl_expired" | "passthrough"
        - ledger_note: 人間向けの1行説明（空文字あり）
        - candidate_key: 正規化キー
    """
    now = _now() if now is None else now
    out = dict(meta_result)
    skill_name = meta_result.get("skill_name", "")
    rec = meta_result.get("recommendation", "")
    key = candidate_key(skill_name)
    out["candidate_key"] = key
    out["suppressed"] = False
    out["ledger_note"] = ""

    def _persist(record: Dict[str, Any]) -> None:
        # persist=False（dry-run 経路）では書き込みを抑止する。
        if persist:
            upsert_record(record, slug)

    ledger = load_ledger(slug)
    existing = ledger.get(key)
    reuse_rate = float(meta_result.get("reuse_rate", 0.0) or 0.0)
    dups = meta_result.get("duplicate_candidates", []) or []

    # SKIP 以外は passthrough（記録のみ・抑制しない）
    if rec != "SKIP":
        record = _new_record(key, rec, reuse_rate, dups, now=now, ttl_days=ttl_days) if existing is None else dict(existing)
        if existing is not None:
            record["recommendation"] = rec
            record["reuse_rate"] = reuse_rate
            record["duplicate_of"] = list(dups)
            record["last_seen"] = now
            record["times_seen"] = int(record.get("times_seen", 0)) + 1
            record["decided_at"] = now
            record["ttl_days"] = ttl_days
            record["suppressed_until"] = 0.0
        _persist(record)
        out["ledger_status"] = "passthrough"
        return out

    # ── ここから rec == "SKIP" ──
    if existing is None:
        # 初回 SKIP: 記録するが抑制しない（最初は必ず1回 surface する）
        record = _new_record(key, "SKIP", reuse_rate, dups, now=now, ttl_days=ttl_days)
        record["suppressed_until"] = now + cooldown_days * DAY_SECONDS
        _persist(record)
        out["ledger_status"] = "new"
        out["ledger_note"] = "初回 SKIP: 記録した"
        return out

    record = dict(existing)
    record["last_seen"] = now
    record["times_seen"] = int(record.get("times_seen", 0)) + 1
    record["reuse_rate"] = reuse_rate
    record["duplicate_of"] = list(dups)
    decided_at = float(record.get("decided_at", record.get("first_seen", now)))
    rec_ttl_days = int(record.get("ttl_days", ttl_days))
    times_skipped = int(record.get("times_skipped", 0)) + 1
    record["times_skipped"] = times_skipped

    # ③ TTL 切れ: 1回だけ強制再評価。decided_at を now に更新して窓をリセット。
    if now > decided_at + rec_ttl_days * DAY_SECONDS:
        record["decided_at"] = now
        record["times_skipped"] = 1  # 窓リセット（新しい判断サイクル）
        record["suppressed_until"] = now + cooldown_days * DAY_SECONDS
        record["recommendation"] = "SKIP"
        _persist(record)
        days = int((now - decided_at) / DAY_SECONDS)
        out["ledger_status"] = "ttl_expired"
        out["ledger_note"] = f"🔄 この判断は {days} 日前。当時 SKIP だが再評価を"
        out["suppressed"] = False
        return out

    # ② 再発エスカレーション: 窓内で ESCALATE_N 回以上 → REVIEW 昇格
    if times_skipped >= escalate_n:
        record["recommendation"] = "REVIEW"
        record["decided_at"] = now
        record["suppressed_until"] = 0.0
        _persist(record)
        out["recommendation"] = "REVIEW"
        out["ledger_status"] = "escalated"
        out["ledger_note"] = f"{times_skipped}回 SKIP: 繰り返し検出。閾値か採用を見直せ"
        out["suppressed"] = False
        return out

    # ① 抑制: クールダウン内なら畳む
    suppressed_until = float(record.get("suppressed_until", 0.0))
    if now <= suppressed_until:
        record["recommendation"] = "SKIP"
        _persist(record)
        out["ledger_status"] = "suppressed"
        out["suppressed"] = True
        out["ledger_note"] = "SKIP（クールダウン内、抑制）"
        return out

    # クールダウン外だが未エスカレーション: 通常 SKIP として surface し直す
    record["recommendation"] = "SKIP"
    record["suppressed_until"] = now + cooldown_days * DAY_SECONDS
    _persist(record)
    out["ledger_status"] = "new"
    out["ledger_note"] = "SKIP（クールダウン経過、再表示）"
    return out


def summarize_suppressed(results: List[Dict[str, Any]]) -> str:
    """抑制された SKIP 件数を1行サマリにする。

    沈黙≠評価（ADR-028 observability contract と同思想）: 0 件でも必ず1行残す。
    """
    n = sum(1 for r in results if r.get("suppressed"))
    return f"SKIP 抑制 {n}件 ✓（前回判断を維持・クールダウン内のため個別表示を省略）"
