"""correction_semantic.daily_review — evolve 内「今日の修正確認」phase（#446）。

毎日叩かれる evolve に決定論 phase として「前回以降の新規 weak_signal を idiom 単位で
group 化し最大 5 件確認」を移植する。昇格経路が reflect SKILL Step 7.7 の散文ステップのみ
だった頃は昇格 0 件だった（learning_skill_md_must_not_enforcement）ため、確認入口を毎日叩かれる
evolve の決定論 phase に降ろす。

build_review() は当該 PJ slug の未昇格（channel ∈ REVIEW_CHANNELS=content-rich・非expired）
weak_signal のうち、**既読集合（correction_review_seen.jsonl）に含まれない signal_key**（= 前回以降の新規）だけを
idiom 単位で group 化し、頻度（同 idiom の再発回数）降順・上位 max_groups を返す。残りは
remaining。新規 0 件でも eligible=False / groups=[] を**常時 emit**する（SKILL.md は eligible
で AskUserQuestion 出力を分岐する）。判断は SKILL.md（AskUserQuestion）が担い、本モジュールは
決定論で判断材料を出すだけ（LLM 非依存）。

既読ストア（correction_review_seen.jsonl・論点2）: correction_judged.jsonl と同方式の物理キー
集合（append-only・1 行 ``{"key": signal_key, "pj_slug": ..., "decision": "promoted"|"rejected",
"reviewed_at": ...}``）。detected_at 時刻 cursor 案は却下（同時刻シグナルの取りこぼし境界バグ）。
read 側で set 化するので重複追記は無害（冪等）。既読追記は **apply 時のみ**（dry_run は読むだけ）。

PJ slug スコープ（DATA_DIR 全PJ共通 pitfall）: 当該 cwd の PJ slug の weak_signal のみ対象にし、
seen レコードにも pj_slug を残す。DATA_DIR は ADR-042 resolver 経由（store 既定パスに委譲）。
dry-run ゼロ書込（pitfall_dryrun_stateful_store_write）: build_review は読み取りのみ・record_reviewed
は dry_run=True なら一切ファイルに触れない。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from correction_semantic.bootstrap_backlog import (
    BACKLOG_CHANNEL,
    JACCARD_THRESHOLD,
)
from correction_semantic.idiom_filter import idiom_eligible
from correction_semantic.representative import prev_action_summary
from correction_semantic.review_channels import (
    REVIEW_CHANNELS,
    grouping_keywords,
    signal_text,
)
from correction_semantic.store import read_idioms
from weak_signals.store import read_signals

# #46 read 層拡張: 既読 union + slug alias は共有モジュール（idioms/weak_signals と単一ソース）。
from store_read_union import (  # noqa: E402
    iter_read_store_paths as _iter_read_store_paths,
    pj_slug_match as _pj_slug_match,
)

SEEN_STORE_NAME = "correction_review_seen.jsonl"


# ─────────────────────────────────────────────────────────────────
# 既読ストア（correction_review_seen.jsonl・物理キー集合）
# ─────────────────────────────────────────────────────────────────
def default_seen_path(base: Optional[Path] = None) -> Path:
    """correction_review_seen.jsonl の正準パスを ADR-042 resolver 経由で解決する。

    base を渡せばそれを優先（テスト isolation 用）。未指定なら resolve_data_dir。
    """
    if base is not None:
        return Path(base) / SEEN_STORE_NAME
    import os

    import rl_common  # 遅延 import（hook/tool 文脈の patch 追従）

    env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    data_dir = rl_common.resolve_data_dir(env)
    return Path(data_dir) / SEEN_STORE_NAME


def _read_seen_one(store: Path) -> Set[str]:
    """単一 correction_review_seen.jsonl の key 集合（ファイル無し → 空 set）。"""
    import json

    out: Set[str] = set()
    if not store.exists():
        return out
    try:
        with open(store, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                k = rec.get("key")
                if k:
                    out.add(k)
    except OSError:
        return out
    return out


def read_reviewed_keys(path: Optional[Path] = None) -> Set[str]:
    """既読集合の signal_key を返す（ファイル無し → 空 set。重複は set 化で無害）。

    path 未指定（production 既定）は #46 read 層拡張で canonical + legacy を union read する
    （key は集合なので自然 dedup）。**flooding 防止の要**: weak_signals を union しても legacy で
    確認済みのシグナルを既読に含めないと daily_review に再噴出するため、既読も対で union する。
    明示 path 指定時はそのファイルのみ（hermetic）。
    """
    if path is not None:
        return _read_seen_one(Path(path))
    out: Set[str] = set()
    for p in _iter_read_store_paths(SEEN_STORE_NAME):
        out |= _read_seen_one(p)
    return out


def record_reviewed(
    signal_keys: List[str],
    pj_slug: str,
    *,
    decision: str,
    path: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """確認済み signal_key を既読集合に追記する（dedup + dry-run ゲート貫通）。

    decision は "promoted"（はい）/ "rejected"（いいえ）。「Skip」は呼ばない（再提示）。
    追記は apply 時のみ。dry_run=True なら **一切ファイルに触れない**（最下層 write ゲート）。
    重複追記は read 側 set 化で無害だが、ここでも既存キーは skip して肥大化を抑える。

    Returns: {"written": int, "dry_run": bool}
    """
    store = path if path is not None else default_seen_path()
    existing = read_reviewed_keys(store)

    to_write: List[str] = []
    seen = set(existing)
    for k in signal_keys:
        if not k or k in seen:
            continue
        seen.add(k)
        to_write.append(k)

    if dry_run:
        # 最下層: dry-run は store に一切書かない。件数だけ返す。
        return {"written": len(to_write), "dry_run": True}

    if to_write:
        # ADR-049 / #55: production（path 無し）は単一書込ゲート store_write、
        # 明示 path は store_write_raw でそのパスを尊重する。
        from rl_common import store_write, store_write_raw

        reviewed_at = datetime.now(timezone.utc).isoformat()
        store.parent.mkdir(parents=True, exist_ok=True)
        for k in to_write:
            rec = {
                "key": k,
                "pj_slug": pj_slug,
                "decision": decision,
                "reviewed_at": reviewed_at,
            }
            if path is None:
                store_write(SEEN_STORE_NAME, rec)
            else:
                store_write_raw(store, rec)

    return {"written": len(to_write), "dry_run": False}


# ─────────────────────────────────────────────────────────────────
# 新規 weak_signal の読み取り（slug + 未昇格 + channel + 非expired + 未既読）
# ─────────────────────────────────────────────────────────────────
def _read_new(
    pj_slug: str,
    *,
    weak_signals_path: Optional[Path],
    seen_keys: Set[str],
) -> List[Dict[str, Any]]:
    """当該 PJ slug の「新規」未昇格 content-rich weak_signal を返す（#99）。

    対象 channel は REVIEW_CHANNELS（llm_judge / rephrase / permission_deny）。content-poor
    チャネル（esc_interrupt / manual_edit_after_ai）は detector が文脈未保存ゆえ除外する。
    新規 = 既読集合（seen_keys）に signal_key が無いもの。promoted / expired は除外。
    """
    recs = read_signals(weak_signals_path)
    out: List[Dict[str, Any]] = []
    for r in recs:
        # #46 read 層拡張: legacy weak_signal（旧 slug タグ）も alias で当 PJ として拾う。
        if not _pj_slug_match(r.get("pj_slug"), pj_slug):
            continue
        if r.get("channel") not in REVIEW_CHANNELS:
            continue
        if r.get("promoted"):
            continue
        if r.get("expired"):
            continue
        if r.get("signal_key") in seen_keys:
            continue
        out.append(r)
    return out


# ─────────────────────────────────────────────────────────────────
# idiom 突合（個人辞書の idiom を物理キーで照合し代表 idiom を付ける）
# ─────────────────────────────────────────────────────────────────
def _idiom_by_phys(idioms: List[Dict[str, Any]], pj_slug: str) -> Dict[str, str]:
    """物理キー（source_path:line_no）→ idiom 本文の対応表（当該 PJ slug のみ）。"""
    out: Dict[str, str] = {}
    for it in idioms:
        # #46 read 層拡張: legacy idiom（旧 slug タグ）も alias で当 PJ として拾う。
        if not _pj_slug_match(it.get("pj_slug"), pj_slug):
            continue
        prov = it.get("provenance") or {}
        phys = f"{prov.get('source_path', '')}:{prov.get('line_no', '')}"
        idiom = it.get("idiom")
        if phys and idiom:
            out.setdefault(phys, idiom)
    return out


def _phys_key(rec: Dict[str, Any]) -> str:
    prov = rec.get("provenance") or {}
    return f"{prov.get('source_path', '')}:{prov.get('line_no', '')}"


def _idiom_text(rec: Dict[str, Any]) -> str:
    # #99: channel 別の actionable 代表テキスト（llm_judge/rephrase=user 発話・#528-3 /
    # permission_deny=拒否コマンド合成）を単一ソース signal_text から取る。
    return signal_text(rec)


def _prev_action(rec: Dict[str, Any]) -> str:
    # #528-3: 直前 AI 行動の 1 行要約（一行 representative の判読補助に evidence へ添える）。
    prov = rec.get("provenance") or {}
    return prev_action_summary(prov.get("prev_action") or "")


def _group_new(
    records: List[Dict[str, Any]],
    phys_to_idiom: Dict[str, str],
) -> List[Dict[str, Any]]:
    """新規 weak_signal を idiom 単位で group 化する（決定論・LLM 非依存）。

    各レコードはまず個人辞書の idiom（物理キー突合）で group キーを決め、辞書に無い場合は
    内容キーワード jaccard≥0.5 のクラスタへ寄せる（bootstrap_backlog と同方針）。group は
    入力順保存の single-pass。各 group は build_review が消費する形に整形する。
    """
    groups: List[Dict[str, Any]] = []
    group_kws: List[Set[str]] = []
    idiom_index: Dict[str, int] = {}

    for rec in records:
        key = rec.get("signal_key", "")
        text = _idiom_text(rec)
        # #99 F1: group 化キーワードは channel 別（permission_deny は拒否コマンドで分離）。
        # representative 表示は text（signal_text）のまま。
        kws = grouping_keywords(rec)
        phys = _phys_key(rec)
        matched_idiom = phys_to_idiom.get(phys)

        gi: Optional[int] = None
        if matched_idiom is not None:
            # 同一 idiom 本文は 1 group に集約する
            if matched_idiom in idiom_index:
                gi = idiom_index[matched_idiom]
        if gi is None and matched_idiom is None:
            if kws:
                for i, gk in enumerate(group_kws):
                    if gk and _jaccard(kws, gk) >= JACCARD_THRESHOLD:
                        gi = i
                        group_kws[i] = gk | kws
                        break

        if gi is None:
            new = {
                "idiom": matched_idiom,
                "representative": text,
                "channel": rec.get("channel", BACKLOG_CHANNEL),
                "signal_keys": [key],
                # #527-4: この group を「はい」確定すると confirmed になる idiom テキスト。
                # eligible（floor/stopword/context token を通る）な matched idiom のみ提示し、
                # AskUserQuestion で idiom 単位の拒否を可能にする。過汎用 idiom は None で
                # 「confirmed になる idiom 無し（promote のみ）」と表示できる。
                "confirmable_idiom": (
                    matched_idiom
                    if matched_idiom and idiom_eligible(matched_idiom)
                    else None
                ),
                "evidence": {
                    "text": text,
                    "prev_action": _prev_action(rec),  # #528-3: 直前 AI 行動の 1 行要約
                    "reason": (rec.get("provenance") or {}).get("reason", ""),
                    "session_id": rec.get("session_id", ""),
                    "count": 1,
                },
            }
            groups.append(new)
            group_kws.append(kws)
            if matched_idiom is not None:
                idiom_index[matched_idiom] = len(groups) - 1
        else:
            groups[gi]["signal_keys"].append(key)
            groups[gi]["evidence"]["count"] += 1

    return groups


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# ─────────────────────────────────────────────────────────────────
# build_review: phase 本体（常時 emit）
# ─────────────────────────────────────────────────────────────────
def build_review(
    pj_slug: str,
    *,
    weak_signals_path: Optional[Path] = None,
    idioms_path: Optional[Path] = None,
    seen_path: Optional[Path] = None,
    max_groups: int = 5,
    exclude_signal_keys: Optional[Set[str]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """前回 evolve 以降の新規 unpromoted weak_signal を idiom 単位 group 化して返す。

    Returns（常時 emit。eligible でなくても groups=[] で返す）:
      {
        "eligible": bool,                 # groups が 1 件以上あるか
        "groups": [                       # 最大 max_groups 件（頻度降順）
          {"idiom": str | None,           # 代表 idiom（個人辞書から照合・無ければ None）
           "representative": str,         # 代表発話断片（user 発話のみ・assistant 引用除去・#528-3）
           "confirmable_idiom": str|None, # 「はい」確定で confirmed になる idiom（eligible 時のみ・#527-4）
           "channel": str,                # llm_judge / rephrase / permission_deny（#99）
           "signal_keys": [str, ...],     # この group に属する weak_signal の signal_key
           "evidence": {"text": str, "prev_action": str,  # prev_action=直前 AI 行動の 1 行要約（#528-3）
                        "reason": str, "session_id": str, "count": int},
          }, ...
        ],
        "remaining": int,                 # max_groups を超えて未提示の group 数
        "reviewed_keys_count": int,       # 既読集合（correction_review_seen）の現在サイズ
        "slug": str,
        "dry_run": bool,
      }

    build_review は読み取りのみ（既読集合に書かない）。追記は SKILL.md が apply 時に
    record_reviewed を呼ぶ（promote 確定後）。

    exclude_signal_keys（#476-3 二重提示の解消）: bootstrap が is_bootstrap=True で発火する
    run では、bootstrap の groups が daily の対象シグナルを signal_key 単位で全包含している。
    SKILL.md 手順通り Step 6.1（bootstrap まとめて確認）→ Step 6.2（daily）を実行すると同じ
    シグナルを 2 回質問することになるため、bootstrap-pending の signal_key をここで除外する。
    None / 空 set（非 bootstrap run）なら従来通り全件提示する。
    """
    seen_keys = read_reviewed_keys(seen_path)
    new_records = _read_new(
        pj_slug, weak_signals_path=weak_signals_path, seen_keys=seen_keys
    )
    # #476-3: bootstrap-pending の signal_key を daily から除外し二重提示を防ぐ。
    if exclude_signal_keys:
        new_records = [
            r for r in new_records if r.get("signal_key") not in exclude_signal_keys
        ]
    idioms = read_idioms(idioms_path)
    phys_to_idiom = _idiom_by_phys(idioms, pj_slug)

    groups = _group_new(new_records, phys_to_idiom)
    # 頻度（再発回数）降順。安定ソートで同頻度は入力順を保つ。
    groups.sort(key=lambda g: g["evidence"]["count"], reverse=True)

    # 他 PJ で confirmed 済みの idiom と正規化テキスト一致する group を先頭へ優先表示し、
    # cross_pj_confirmed ラベルを常時付与する（#462）。ストアへの書込は無い（read 専用）。
    # 頻度ソート後に適用するので「cross-PJ 一致（頻度順）→ 非一致（頻度順）」になる。
    from correction_semantic.cross_pj_priority import prioritize as _prioritize

    groups = _prioritize(groups, pj_slug, idioms_path=idioms_path)

    top = groups[:max_groups]
    remaining = max(0, len(groups) - len(top))

    return {
        "eligible": len(top) > 0,
        "groups": top,
        "remaining": remaining,
        "reviewed_keys_count": len(seen_keys),
        "slug": pj_slug,
        "dry_run": dry_run,
    }
