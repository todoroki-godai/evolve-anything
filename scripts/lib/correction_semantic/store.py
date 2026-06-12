"""correction_semantic.store — 個人辞書 + 判定進捗の append/read（#431）。

2 つの jsonl ストアを扱う:
- ``correction_idioms.jsonl`` — 検出した修正言い回し（イディオム）の個人辞書。
  provenance（元発話の物理キー・判定理由）付き。dedup キー = idiom + 元発話の物理キー。
- ``correction_judged.jsonl`` — LLM 判定済み発話の物理キー進捗。再判定（無駄な LLM call）を
  防ぐために utterance の物理 PK（source_path:line_no）で突合する。

dry-run ゼロ書込（pitfall_dryrun_stateful_store_write）: append 系は ``dry_run`` を受け、
True なら **一切ファイルに触れない**（ディレクトリ作成も append も行わない）。

DATA_DIR は ADR-042 resolver（rl_common.resolve_data_dir）経由（hook/tool 統一）。
jsonl で十分（DuckDB checkpoint pitfall 回避）。両ストアとも writer は batch（evolve 同居）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

IDIOMS_STORE_NAME = "correction_idioms.jsonl"
JUDGED_STORE_NAME = "correction_judged.jsonl"


# ─────────────────────────────────────────────────────────────────
# 物理キー（判定進捗の突合）
# ─────────────────────────────────────────────────────────────────
def utterance_key(utterance: Dict[str, Any]) -> str:
    """utterance の物理 PK（source_path:line_no）を返す（utterances.db の PK と同型）。

    判定済み突合・provenance のどちらにも使う安定キー。
    """
    return f"{utterance.get('source_path', '')}:{utterance.get('line_no', '')}"


# ─────────────────────────────────────────────────────────────────
# 個人辞書（correction_idioms.jsonl）
# ─────────────────────────────────────────────────────────────────
@dataclass
class CorrectionIdiom:
    """1 件の修正言い回しレコード（correction_idioms.jsonl 1 行に対応）。

    idiom:        抽出された修正の言い回し（例: "四国めたんじゃなくて"）
    provenance:   検出根拠（source_path / line_no / session_id / reason 等の evidence dict）
    detected_at:  検出時刻（ISO8601 UTC）
    pj_slug:      ADR-031 準拠 slug（read 側照合の強制・全PJ共通 DATA_DIR 単一ファイル pitfall）
    idiom_key:    dedup キー（idiom + provenance の物理キーの安定ハッシュ）
    confirmed:    人間が #446 の review で「はい」を選んだか（初期 False・ADR-047）。
                  confirmed=True が立つまで idiom_autopromote は一切発動しない（雪崩防止）。
    confirmed_at: 確認時刻（ISO8601 / None）
    confirmed_by: 確認 source（"daily_review" / None）
    revoked_at:   安全弁③で取り消した時刻（ISO8601 / None。取り消し時に confirmed=False へ戻す）
    """

    idiom: str
    provenance: Dict[str, Any]
    detected_at: str
    pj_slug: str
    idiom_key: str = ""
    confirmed: bool = False
    confirmed_at: Optional[str] = None
    confirmed_by: Optional[str] = None
    revoked_at: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.idiom_key:
            self.idiom_key = compute_idiom_key(self.idiom, self.provenance)

    def to_record(self) -> Dict[str, Any]:
        return asdict(self)


def compute_idiom_key(idiom: str, provenance: Dict[str, Any]) -> str:
    """idiom + 元発話の物理キーの安定ハッシュ（再判定時の dedup キー）。

    同じ発話から同じ言い回しを抽出したら同じキーになるので、バッチ再実行で
    二重記録しない。physical key（source_path:line_no）を含めることで、同一言い回しを
    別発話から拾った場合は別レコードとして残す（provenance を潰さない）。
    """
    phys = f"{provenance.get('source_path', '')}:{provenance.get('line_no', '')}"
    payload = json.dumps(
        {"idiom": idiom, "phys": phys}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _resolve_store(name: str, base: Optional[Path]) -> Path:
    if base is not None:
        return Path(base) / name
    import os

    import rl_common  # 遅延 import（hook/tool 文脈の patch 追従）

    env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    data_dir = rl_common.resolve_data_dir(env)
    return Path(data_dir) / name


def default_idioms_path(base: Optional[Path] = None) -> Path:
    return _resolve_store(IDIOMS_STORE_NAME, base)


def default_judged_path(base: Optional[Path] = None) -> Path:
    return _resolve_store(JUDGED_STORE_NAME, base)


def read_idioms(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """既存の個人辞書レコードを読む（ファイル無し → 空リスト）。"""
    store = path if path is not None else default_idioms_path()
    return _read_jsonl(store)


def existing_idiom_keys(path: Optional[Path] = None) -> Set[str]:
    return {r.get("idiom_key") for r in read_idioms(path) if r.get("idiom_key")}


# ─────────────────────────────────────────────────────────────────
# human-confirmed idiom（ADR-047・自動昇格の発火ゲート + 安全弁③）
# ─────────────────────────────────────────────────────────────────
# confirmed の単位は「pj_slug × idiom テキスト」。idiom_key（idiom + 物理キーの安定ハッシュ）は
# 出現ごとに別値（dedup 用）なので、これを confirmed のキーにすると **同じ言い回しの新規発話**が
# 別 idiom_key の新 record（unconfirmed）になり永遠にマッチしない（同 phys のシグナルは #446 の
# 「はい」時点で promoted 済み）→ 構造的 no-op になる。承認済みパターンの新規再発を機械再適用する
# のが本機能の目的なので、テキスト一致まで一般化する。FP は安全弁3点（daily_cap / surface / revoke）
# で吸収する（ADR-047 採用案 C）。idiom_key は corrections への provenance 追跡用に維持。
def normalize_idiom_text(text: Optional[str]) -> str:
    """idiom テキスト一致判定の正準正規化（決定論・LLM 非依存）。

    idiom_autopromote（ADR-047・#447）と cross_pj_priority（#462）の**両方**がこの 1 関数を
    通してテキストを照合する（正規化ロジックを二重実装しない・Success Criteria #462）。
    正規化は周囲空白の strip のみ — 既存 autopromote は厳密 exact-match だったので、
    strip は exact-match の superset であり既存 confirmed 照合を壊さない（接地維持）。
    日本語 idiom は median 10 文字の生発話断片（bootstrap_backlog の実データ知見）であり、
    casefold/全半角統一は過剰一般化（別意味の取り違え）を招くため意図的に入れない。
    """
    if not text:
        return ""
    return text.strip()


def read_confirmed_idiom_texts(
    pj_slug: str, path: Optional[Path] = None
) -> Set[str]:
    """当該 PJ slug の confirmed=True かつ未 revoke な idiom の **テキスト集合**を返す。

    idiom_autopromote はこのテキスト集合に一致する新規 weak_signal を自動昇格する。
    **confirmed=True が 1 件も無ければ空集合** → 自動昇格は一切発動しない（雪崩防止）。
    revoked_at が立った idiom テキストは除外する（安全弁③で巻き戻る）。
    テキストは normalize_idiom_text で正準化して返す（autopromote 照合と正規化を共有）。
    """
    out: Set[str] = set()
    for r in read_idioms(path):
        if r.get("pj_slug") != pj_slug:
            continue
        if not r.get("confirmed"):
            continue
        if r.get("revoked_at"):
            continue
        idiom = normalize_idiom_text(r.get("idiom"))
        if idiom:
            out.add(idiom)
    return out


def read_cross_pj_confirmed_idiom_texts(
    pj_slug: str, path: Optional[Path] = None
) -> Dict[str, List[str]]:
    """**他 PJ slug** の confirmed=True かつ未 revoke な idiom の {正規化テキスト: [他slug, ...]}。

    #462: ある PJ で人間が承認した idiom と正規化テキスト一致する他 PJ の未確認 idiom を
    daily_review / bootstrap_backlog の提示で先頭に優先表示するための照合素材。
    correction_idioms.jsonl は全 PJ 共通の単一ストア（レコードに pj_slug あり）なので、
    cross-PJ 照合は「自 slug を除く confirmed テキスト集合」を読むだけで決定論になる。

    自 slug の confirmed は cross シグナルにしない（自 PJ 内は通常 confirmed/autopromote 経路）。
    revoked_at が立った idiom は除外（安全弁③で巻き戻る）。テキストは normalize_idiom_text で
    正準化したキーで集約し、値は重複排除した他 slug 一覧（出現順保存）。**確認状態を変えたり
    昇格したりはしない**（読み取り専用・ADR-047 不変条件）。
    """
    out: Dict[str, List[str]] = {}
    for r in read_idioms(path):
        slug = r.get("pj_slug")
        if not slug or slug == pj_slug:
            continue
        if not r.get("confirmed"):
            continue
        if r.get("revoked_at"):
            continue
        idiom = normalize_idiom_text(r.get("idiom"))
        if not idiom:
            continue
        slugs = out.setdefault(idiom, [])
        if slug not in slugs:
            slugs.append(slug)
    return out


def _resolve_idiom_texts_for_keys(
    recs: List[Dict[str, Any]], idiom_keys: Set[str]
) -> Set[tuple]:
    """idiom_key 集合 → 対応する (pj_slug, idiom テキスト) 集合を解決する。

    confirm / revoke はテキスト単位で全 record に効かせるため、まず引数の idiom_key から
    (slug, text) を解決し、その (slug, text) を持つ全 record をマッチさせる。
    """
    out: Set[tuple] = set()
    for r in recs:
        if r.get("idiom_key") in idiom_keys:
            out.add((r.get("pj_slug"), r.get("idiom")))
    return out


def idiom_keys_for_same_text(
    idiom_key: str, path: Optional[Path] = None
) -> Set[str]:
    """指定 idiom_key と同じ (pj_slug, idiom テキスト) を持つ全 idiom record の idiom_key 集合。

    revoke（安全弁③）の corrections 巻き戻しで使う: confirmed はテキスト単位なので、
    同テキストの別 phys から昇格した corrections（別 idiom_key）も invalidate 対象にする。
    引数 idiom_key 自身も含む。該当無し（未知 key）なら空集合。
    """
    recs = read_idioms(path)
    target_texts = _resolve_idiom_texts_for_keys(recs, {idiom_key})
    if not target_texts:
        return set()
    return {
        r.get("idiom_key")
        for r in recs
        if (r.get("pj_slug"), r.get("idiom")) in target_texts and r.get("idiom_key")
    }


def _rewrite_idioms(path: Path, recs: List[Dict[str, Any]]) -> None:
    """correction_idioms.jsonl を原子的に書き直す（promote._rewrite_promoted と同型）。"""
    import os
    import tempfile

    new_content = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def confirm_idioms(
    idiom_keys: List[str],
    *,
    path: Optional[Path] = None,
    confirmed_by: str = "daily_review",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """指定 idiom_key の idiom を **テキスト単位**で confirmed=True にマークする。

    #446 の review で「はい」確定時に呼ばれる。引数 idiom_key から (pj_slug, idiom テキスト) を
    解決し、その (slug, text) を持つ **当該 slug の全 record** に confirmed=True を立てる
    （将来発話の新 record はテキスト照合で自動的に効くので、ここで追記する必要はない）。
    confirmed_at / confirmed_by を立て、revoked_at は None にリセット（再確認で復活）。

    返り値の "confirmed" は **書き換えた record 件数**（テキスト一致で複数になりうる）。

    dry-run ゼロ書込: dry_run=True なら一切ファイルに触れず「確認するはずだった record 件数」を返す。

    Returns: {"confirmed": int, "dry_run": bool}
    """
    from datetime import datetime, timezone

    store = path if path is not None else default_idioms_path()
    target_keys = set(k for k in (idiom_keys or []) if k)
    if not target_keys:
        return {"confirmed": 0, "dry_run": dry_run}

    recs = _read_jsonl(store)
    target_texts = _resolve_idiom_texts_for_keys(recs, target_keys)
    if not target_texts:
        return {"confirmed": 0, "dry_run": dry_run}

    matched = 0
    now = datetime.now(timezone.utc).isoformat()
    for r in recs:
        if (r.get("pj_slug"), r.get("idiom")) in target_texts:
            matched += 1
            if not dry_run:
                r["confirmed"] = True
                r["confirmed_at"] = now
                r["confirmed_by"] = confirmed_by
                r["revoked_at"] = None

    if dry_run:
        return {"confirmed": matched, "dry_run": True}

    if matched:
        _rewrite_idioms(store, recs)
    return {"confirmed": matched, "dry_run": False}


def revoke_idiom(
    idiom_key: str,
    *,
    path: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """指定 idiom_key の idiom を **テキスト単位**で confirmed=False + revoked_at に戻す（安全弁③）。

    引数 idiom_key から (pj_slug, idiom テキスト) を解決し、その (slug, text) を持つ
    当該 slug の全 record に revoked_at を立てる（テキスト単位の取り消し）。取り消し後
    read_confirmed_idiom_texts から外れ、autopromote の対象から除外される。
    corrections レコードの invalidated 化は呼び出し側（reflect --revoke-idiom）が行う。

    返り値の "revoked" は **書き換えた record 件数**。

    dry-run ゼロ書込: dry_run=True なら一切ファイルに触れず「取り消すはずだった record 件数」を返す。

    Returns: {"revoked": int, "dry_run": bool}
    """
    from datetime import datetime, timezone

    store = path if path is not None else default_idioms_path()
    recs = _read_jsonl(store)
    target_texts = _resolve_idiom_texts_for_keys(recs, {idiom_key})
    if not target_texts:
        return {"revoked": 0, "dry_run": dry_run}

    matched = 0
    now = datetime.now(timezone.utc).isoformat()
    for r in recs:
        if (r.get("pj_slug"), r.get("idiom")) in target_texts:
            matched += 1
            if not dry_run:
                r["confirmed"] = False
                r["revoked_at"] = now

    if dry_run:
        return {"revoked": matched, "dry_run": True}

    if matched:
        _rewrite_idioms(store, recs)
    return {"revoked": matched, "dry_run": False}


def append_idioms(
    idioms: List[CorrectionIdiom],
    path: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """新規イディオムを correction_idioms.jsonl に追記する（dedup + dry-run ゲート）。

    Returns:
        {"written": int, "skipped_dup": int, "dry_run": bool}
    """
    store = path if path is not None else default_idioms_path()
    seen = existing_idiom_keys(store)

    to_write: List[CorrectionIdiom] = []
    skipped = 0
    batch_keys = set(seen)
    for it in idioms:
        if it.idiom_key in batch_keys:
            skipped += 1
            continue
        batch_keys.add(it.idiom_key)
        to_write.append(it)

    if dry_run:
        return {"written": len(to_write), "skipped_dup": skipped, "dry_run": True}

    if to_write:
        from rl_common import append_jsonl

        store.parent.mkdir(parents=True, exist_ok=True)
        for it in to_write:
            append_jsonl(store, it.to_record())

    return {"written": len(to_write), "skipped_dup": skipped, "dry_run": False}


# ─────────────────────────────────────────────────────────────────
# 判定進捗（correction_judged.jsonl）
# ─────────────────────────────────────────────────────────────────
def read_judged_keys(path: Optional[Path] = None) -> Set[str]:
    """判定済み発話の物理キー集合を返す（ファイル無し → 空 set）。

    各行は {"key": "<source_path>:<line_no>", ...}。"""
    store = path if path is not None else default_judged_path()
    out: Set[str] = set()
    for rec in _read_jsonl(store):
        k = rec.get("key")
        if k:
            out.add(k)
    return out


def record_judged(
    keys: List[str],
    path: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """判定済み発話の物理キーを追記する（dedup + dry-run ゲート）。

    Returns:
        {"written": int, "dry_run": bool}
    """
    store = path if path is not None else default_judged_path()
    existing = read_judged_keys(store)

    to_write: List[str] = []
    seen = set(existing)
    for k in keys:
        if not k or k in seen:
            continue
        seen.add(k)
        to_write.append(k)

    if dry_run:
        return {"written": len(to_write), "dry_run": True}

    if to_write:
        from rl_common import append_jsonl

        store.parent.mkdir(parents=True, exist_ok=True)
        for k in to_write:
            append_jsonl(store, {"key": k})

    return {"written": len(to_write), "dry_run": False}


def filter_unjudged(
    utterances: List[Dict[str, Any]],
    judged_keys: Set[str],
) -> List[Dict[str, Any]]:
    """判定済みでない発話だけを返す（物理キーで突合）。"""
    return [u for u in utterances if utterance_key(u) not in judged_keys]


# ─────────────────────────────────────────────────────────────────
# 内部 helper
# ─────────────────────────────────────────────────────────────────
def _read_jsonl(store: Path) -> List[Dict[str, Any]]:
    if not store.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(store, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        return []
    return out
