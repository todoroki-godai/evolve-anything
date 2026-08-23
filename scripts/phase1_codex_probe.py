#!/usr/bin/env python3
"""ADR-055 Phase 1 使い捨てプローブ（#534）。

``~/.codex/sessions/YYYY/MM/DD/*.jsonl`` から Phase 1 スコープ（1PJ・直近N日・
親セッションのみ）の user 発話を抽出し、隔離された一時ディレクトリへ出力する。

**既存リポジトリのコードは一切変更しない使い捨てスクリプトである**（裁定A）。
**LLM は一切呼ばない**（判定実行は別途オーケストレーターが行う）。
**本番ストア（utterances.db / correction_judged.jsonl / correction_idioms.jsonl /
weak_signals.jsonl）には一切書き込まない**。実行前後で byte hash 不変を検査する。

設計の SoT は ``docs/decisions/drafts/055-codex-rollout-ingest.md``。
本スクリプトが実装する Decision: D3（機構マーカー除外・9種）/ D4（子セッション
ファイル単位除外）/ D5a（識別子とセグメント帰属・チャネル制約付き dedup）。

``prev_action``（Must1・レビュー反映）: 判定プロンプトが実際に文脈として使う
（correction_semantic/prompt.py）ため、Codex ログでも CC 側と同じ定義・同じ整形
（``utterance_archive.extractor._format_prev_action``。上限10・超過時 "…"）で
実データから集約する。D2（parser/reducer 分離。既存コードへの反映）自体は
Phase 2 スコープのままだが、Phase 1 の使い捨てスクリプト内で同等の集約を行う。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

_LIB_DIR = Path(__file__).resolve().parent / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))


# ─────────────────────────────────────────────────────────────────
# 定数（D3: 機構マーカー9種。単一定数として集約し2箇所に書かない）
# ─────────────────────────────────────────────────────────────────
MACHINERY_MARKERS = frozenset(
    {
        "recommended_plugins",
        "task-notification",
        "command-name",
        "local-command-stdout",
        "command-message",
        "skill",
        "environment_context",
        "user_action",
        "image",
    }
)

# Codex 側の「AI のツール操作」に該当する (type, payload.type) の単一ソース（Must1）。
# CC 側の tool_use 名収集（extractor.py の _tool_use_names 相当）と同じ役割を果たす。
# 実データ全数走査（X4 と同じ方法論）で列挙した組。name フィールドを持つ組と、
# 固定名で表す組（apply_patch 等）に分ける。
TOOL_CALL_NAME_FIELD_PAIRS = frozenset(
    {
        ("response_item", "function_call"),
        ("response_item", "custom_tool_call"),
    }
)
TOOL_CALL_FIXED_NAME_PAIRS = {
    ("response_item", "tool_search_call"): "tool_search",
    ("event_msg", "patch_apply_end"): "apply_patch",
    ("event_msg", "web_search_end"): "web_search",
}
# 呼び出しの「結果」であり操作そのものではない組。prev_action には含めないが、
# 既知の組として扱い X4 の unknown_type_pairs へは計上しない。
TOOL_CALL_OUTPUT_TYPE_PAIRS = frozenset(
    {
        ("response_item", "function_call_output"),
        ("response_item", "custom_tool_call_output"),
        ("response_item", "tool_search_output"),
    }
)


def extract_tool_name(rtype: Optional[str], ptype: Optional[str], payload: Dict[str, Any]) -> Optional[str]:
    """ツール呼び出しレコードから tool 名を1つ取り出す。非対象なら None。"""
    key = (rtype, ptype)
    if key in TOOL_CALL_FIXED_NAME_PAIRS:
        return TOOL_CALL_FIXED_NAME_PAIRS[key]
    if key in TOOL_CALL_NAME_FIELD_PAIRS:
        name = payload.get("name")
        return name if isinstance(name, str) and name else None
    if key == ("event_msg", "mcp_tool_call_end"):
        invocation = payload.get("invocation") or {}
        tool = invocation.get("tool") if isinstance(invocation, dict) else None
        return f"mcp:{tool}" if isinstance(tool, str) and tool else "mcp_tool_call"
    return None


DEFAULT_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"
DEFAULT_PJ_FILTER = "evolve-anything"
DEFAULT_DAYS = 14
DEDUP_THRESHOLD_MS = 100.0
EXTRACTOR_VERSION = "phase1-probe-v1"

# 本番ストア相当（本番実 utterances テーブルと同じキー構成。#430 SoT を参照）。
UTTERANCE_COLUMNS = [
    "source_path", "line_no", "pj_slug", "session_id", "timestamp",
    "text", "text_hash", "prev_action", "source_kind", "extractor_version",
]


# ─────────────────────────────────────────────────────────────────
# 先頭タグ判定（D3）
# ─────────────────────────────────────────────────────────────────
def strip_leading_noise(text: str) -> str:
    """先頭の BOM・空白・改行を除去する（D3 表現差変異対策・codex v3 反映）。"""
    return (text or "").lstrip("﻿ \t\n\r　")


def head_tag(text: str) -> Optional[str]:
    """先頭タグ（``<tag ...>``）の tag 名を返す。無ければ None。"""
    t = strip_leading_noise(text)
    if not t.startswith("<"):
        return None
    end = t.find(">")
    if end == -1:
        return None
    inner = t[1:end].strip()
    if not inner:
        return None
    return inner.split()[0]


def is_machinery_text(text: str) -> bool:
    """機構マーカー（D3・9種）で先頭タグ判定する。"""
    return head_tag(text) in MACHINERY_MARKERS


def text_hash(text: str) -> str:
    """dedup 用ハッシュ（sha256 先頭16桁）。既存 extractor._text_hash と同型。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def parse_iso_ms(ts: str) -> Optional[float]:
    """ISO8601 timestamp を epoch ms に変換する。パース不能なら None。"""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.timestamp() * 1000.0


# ─────────────────────────────────────────────────────────────────
# X3: 対象ファイル選定（日付ディレクトリ方式・ファイルを開かない）
# ─────────────────────────────────────────────────────────────────
def iter_date_dir_files(root: Path, base_date: date, days: int) -> List[Path]:
    """``YYYY/MM/DD`` ディレクトリ名だけで対象ファイルを選ぶ（X3・ファイル非開封）。"""
    date_min = base_date - timedelta(days=days - 1)
    out: List[Path] = []
    if not root.exists():
        return out
    for y_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for m_dir in sorted(p for p in y_dir.iterdir() if p.is_dir()):
            for d_dir in sorted(p for p in m_dir.iterdir() if p.is_dir()):
                try:
                    dt = date(int(y_dir.name), int(m_dir.name), int(d_dir.name))
                except ValueError:
                    continue
                if not (date_min <= dt <= base_date):
                    continue
                out.extend(sorted(d_dir.glob("*.jsonl")))
    return out


# ─────────────────────────────────────────────────────────────────
# ファイル単位パース（session_meta 順序・候補発話・sub-agent マーカー・未知組）
# ─────────────────────────────────────────────────────────────────
@dataclass
class RawCandidate:
    """1件の user 発話候補（機構マーカー除外・dedup 前）。"""

    file: str
    channel: str  # "response_item" | "event_msg"
    line_no: int
    timestamp: str
    ts_ms: Optional[float]
    text: str
    cwd: Optional[str]
    session_id: Optional[str]  # D5a: レコード順で直前の session_meta.id（無ければ None）
    prev_action: Optional[str]  # Must1: 直前 user 発話以降の tool 呼び出し名（CC extractor と同型整形）


@dataclass
class ParsedFile:
    path: str
    first_session_meta_id: Optional[str]
    first_session_meta_cwd: Optional[str]
    candidates: List[RawCandidate]
    agent_thread_ids: Set[str]
    unattributed_count: int  # D5a: 直前 session_meta が無い発話（採用せず件数だけ計上）
    unknown_type_pairs: Counter
    parse_error_lines: int


def parse_session_file(path: Path) -> ParsedFile:
    """1ファイルを単一パスで走査し、D4/D5a/X4/Must1(prev_action) に必要な情報を抽出する。

    prev_action の集約（Must1）: CC 側（extractor.py の pending_tool_names）と同じ
    「直前の human 発話より後・当該 human 発話より前」の tool 呼び出し名を集める。
    Codex は同一の論理発話が response_item / event_msg の2チャネルに重複出現する
    （X1・M2）ため、単純に「発話を emit するたびに pending をリセット」すると、
    2チャネル目（dedup 前提で捨てられない方＝先着順が response_item とは限らない。
    実測: event_msg が先に出現するケースが 2185/2772 件で多数派）が空の
    prev_action を得てしまう。そこで「直前に emit した候補と同一 text_hash なら
    同一論理発話の重複とみなし、pending をリセットせず同じ prev_action を再利用する」
    方式で、どちらのチャネルが先着でも一致した prev_action を持たせる。
    """
    first_id: Optional[str] = None
    first_cwd: Optional[str] = None
    current_session_id: Optional[str] = None
    current_cwd: Optional[str] = None
    candidates: List[RawCandidate] = []
    agent_thread_ids: Set[str] = set()
    unattributed = 0
    unknown_type_pairs: Counter = Counter()
    parse_error_lines = 0

    from utterance_archive.extractor import _format_prev_action  # 整形の単一ソースを再利用（自作しない）

    pending_tool_names: List[str] = []
    last_emitted_text_hash: Optional[str] = None
    last_emitted_prev_action: Optional[str] = None

    def _next_prev_action(text: str) -> Optional[str]:
        nonlocal pending_tool_names, last_emitted_text_hash, last_emitted_prev_action
        h = text_hash(text)
        if h == last_emitted_text_hash:
            # 直前に emit した候補と同一発話（チャネル重複）。pending は消費済みのまま
            # 触らず、同じ prev_action を再利用する（上記 docstring 参照）。
            return last_emitted_prev_action
        action = _format_prev_action(pending_tool_names)
        pending_tool_names = []
        last_emitted_text_hash = h
        last_emitted_prev_action = action
        return action

    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw_line in enumerate(fh, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                parse_error_lines += 1
                continue

            rtype = rec.get("type")
            payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
            ptype = payload.get("type")
            ts = rec.get("timestamp", "") or ""

            if rtype == "session_meta":
                sid = payload.get("id")
                cwd = payload.get("cwd")
                if sid:
                    current_session_id = sid
                    current_cwd = cwd
                    if first_id is None:
                        first_id = sid
                        first_cwd = cwd
                continue

            # inter_agent_communication_metadata はトップレベル type（event_msg の
            # payload.type ではない）。M3 実測に基づき top-level のまま判定する。
            if rtype == "inter_agent_communication_metadata":
                continue

            if rtype == "response_item" and ptype == "message" and payload.get("role") == "user":
                content = payload.get("content") or []
                text = "".join(
                    c.get("text", "") for c in content if isinstance(c, dict)
                )
                if current_session_id is None:
                    unattributed += 1
                    continue
                prev_action = _next_prev_action(text)
                candidates.append(
                    RawCandidate(
                        str(path), "response_item", line_no, ts, parse_iso_ms(ts),
                        text, current_cwd, current_session_id, prev_action,
                    )
                )
                continue

            if rtype == "event_msg" and ptype == "sub_agent_activity":
                tid = payload.get("agent_thread_id")
                if tid:
                    agent_thread_ids.add(tid)
                continue

            if rtype == "event_msg" and ptype == "user_message":
                text = payload.get("message", "") or ""
                if current_session_id is None:
                    unattributed += 1
                    continue
                prev_action = _next_prev_action(text)
                candidates.append(
                    RawCandidate(
                        str(path), "event_msg", line_no, ts, parse_iso_ms(ts),
                        text, current_cwd, current_session_id, prev_action,
                    )
                )
                continue

            # 未知 role（response_item.message で role != user）は user 扱いしない
            # （D3 Should反映）。developer role も同経路で自然に除外される。
            if rtype == "response_item" and ptype == "message":
                continue

            # Must1: ツール呼び出し（AI のツール操作）を pending_tool_names へ集約する。
            tool_name = extract_tool_name(rtype, ptype, payload)
            if tool_name is not None:
                pending_tool_names.append(tool_name)
                continue

            # 呼び出しの結果（*_output）は既知の組として扱い、unknown への計上から除く。
            if (rtype, ptype) in TOOL_CALL_OUTPUT_TYPE_PAIRS:
                continue

            # X4: 未知の (type, payload.type) 組は安全にスキップし件数を surface する。
            unknown_type_pairs[(rtype, ptype)] += 1

    return ParsedFile(
        str(path), first_id, first_cwd, candidates, agent_thread_ids,
        unattributed, unknown_type_pairs, parse_error_lines,
    )


# ─────────────────────────────────────────────────────────────────
# D4: 子セッションのファイル単位除外
# ─────────────────────────────────────────────────────────────────
def build_agent_thread_id_set(parsed_files: Sequence[ParsedFile]) -> Set[str]:
    out: Set[str] = set()
    for pf in parsed_files:
        out |= pf.agent_thread_ids
    return out


def split_parent_child(
    parsed_files: Sequence[ParsedFile], agent_thread_ids: Set[str]
) -> Tuple[List[ParsedFile], List[ParsedFile]]:
    """session_meta.id（先頭）が agent_thread_ids に含まれるファイルを子として分離する。"""
    parents: List[ParsedFile] = []
    children: List[ParsedFile] = []
    for pf in parsed_files:
        if pf.first_session_meta_id and pf.first_session_meta_id in agent_thread_ids:
            children.append(pf)
        else:
            parents.append(pf)
    return parents, children


# ─────────────────────────────────────────────────────────────────
# D5a: チャネル制約付き 1:1 貪欲マッチング（X1 のマッチング規則1〜6）
# ─────────────────────────────────────────────────────────────────
def dedup_channel_constrained(
    candidates: Sequence[RawCandidate], threshold_ms: float = DEDUP_THRESHOLD_MS
) -> List[RawCandidate]:
    """同一ファイル・同一 text_hash・異チャネル間のみの 1:1 貪欲マッチングで dedup する。

    規則（X1・実装者が一意に実装できる形で ADR に確定済み）:
    1. response_item 側を基準集合とし (timestamp, line_no) 昇順で走査
    2. 同一 file・同一 text_hash・未マッチの event_msg 側候補のうち |delta| 最小を選ぶ
    3. 同値なら line_no が小さい方
    4. |delta| > threshold_ms は選ばない
    5. マッチした対は双方消費済み（1:1）。**残す代表は response_item 側**
    6. 未マッチはそれぞれ独立した発話として残す
    """
    resp = sorted(
        (c for c in candidates if c.channel == "response_item"),
        key=lambda c: ((c.ts_ms if c.ts_ms is not None else 0.0), c.line_no),
    )
    evm = [c for c in candidates if c.channel == "event_msg"]
    by_key: Dict[Tuple[str, str], List[RawCandidate]] = {}
    for c in evm:
        by_key.setdefault((c.file, text_hash(c.text)), []).append(c)

    matched_evm_ids: Set[int] = set()
    result: List[RawCandidate] = []

    for r in resp:
        key = (r.file, text_hash(r.text))
        best: Optional[RawCandidate] = None
        best_delta: Optional[float] = None
        for cand in by_key.get(key, []):
            if id(cand) in matched_evm_ids:
                continue
            if r.ts_ms is None or cand.ts_ms is None:
                continue
            delta = abs(cand.ts_ms - r.ts_ms)
            if delta > threshold_ms:
                continue
            if (
                best is None
                or delta < best_delta
                or (delta == best_delta and cand.line_no < best.line_no)
            ):
                best = cand
                best_delta = delta
        if best is not None:
            matched_evm_ids.add(id(best))
        result.append(r)

    unmatched_evm = [c for c in evm if id(c) not in matched_evm_ids]
    result.extend(unmatched_evm)
    return result


# ─────────────────────────────────────────────────────────────────
# パイプライン統括
# ─────────────────────────────────────────────────────────────────
@dataclass
class StageCounts:
    target_files: int = 0
    raw: int = 0
    after_child_exclusion: int = 0
    after_machinery_exclusion: int = 0
    after_dedup: int = 0
    unattributed_dropped: int = 0
    child_files: int = 0
    parse_error_lines: int = 0


@dataclass
class ProbeResult:
    counts: StageCounts
    utterances: List[Dict[str, Any]]
    target_file_hashes: Dict[str, str]
    normalized_events: List[Dict[str, Any]]
    unique_keys: List[List[str]]
    unknown_type_pairs: Dict[str, int]
    child_ref_scope_agreement: bool
    child_ref_scope_detail: Dict[str, int]


def _pj_slug_from_cwd(cwd: Optional[str], pj_filter: str) -> str:
    if not cwd:
        return pj_filter
    return Path(cwd).name or pj_filter


def run_probe(
    *,
    sessions_root: Path = DEFAULT_SESSIONS_ROOT,
    base_date: Optional[date] = None,
    days: int = DEFAULT_DAYS,
    pj_filter: str = DEFAULT_PJ_FILTER,
    child_ref_scope: str = "phase1",
    dedup_threshold_ms: float = DEDUP_THRESHOLD_MS,
) -> ProbeResult:
    """Phase 1 抽出パイプライン全体を実行する（決定論・LLM 非呼出・IO は read-only）。"""
    base_date = base_date or datetime.now(timezone.utc).date()
    counts = StageCounts()

    all_files = iter_date_dir_files(sessions_root, base_date, days)
    parsed_all = [parse_session_file(p) for p in all_files]
    pj_parsed = [
        pf for pf in parsed_all
        if pf.first_session_meta_cwd and pj_filter in pf.first_session_meta_cwd
    ]
    counts.target_files = len(pj_parsed)
    counts.raw = sum(len(pf.candidates) for pf in pj_parsed)
    counts.unattributed_dropped = sum(pf.unattributed_count for pf in pj_parsed)
    counts.parse_error_lines = sum(pf.parse_error_lines for pf in pj_parsed)

    unknown_type_pairs: Counter = Counter()
    for pf in pj_parsed:
        unknown_type_pairs.update(pf.unknown_type_pairs)

    # X2: 参照集合の走査範囲。既定は Phase1 限定（方式B）。全ファイル走査（方式A）と
    # 一致することをスクリプト自身が検証する。
    ref_phase1 = build_agent_thread_id_set(pj_parsed)
    ref_full = build_agent_thread_id_set(parsed_all)
    parents_b, children_b = split_parent_child(pj_parsed, ref_phase1)
    parents_a, children_a = split_parent_child(pj_parsed, ref_full)
    agreement = {f.path for f in children_a} == {f.path for f in children_b}

    # 既定（方式B・Phase1限定走査）を採用。
    parents, children = (
        (parents_b, children_b) if child_ref_scope == "phase1" else (parents_a, children_a)
    )
    counts.child_files = len(children)
    counts.after_child_exclusion = sum(len(pf.candidates) for pf in parents)

    # D3: 機構マーカー除外
    kept_candidates: List[RawCandidate] = []
    for pf in parents:
        for c in pf.candidates:
            if not is_machinery_text(c.text):
                kept_candidates.append(c)
    counts.after_machinery_exclusion = len(kept_candidates)

    # X1: チャネル制約付き dedup
    deduped = dedup_channel_constrained(kept_candidates, threshold_ms=dedup_threshold_ms)
    counts.after_dedup = len(deduped)

    utterances: List[Dict[str, Any]] = []
    unique_keys: List[List[str]] = []
    for c in deduped:
        pj_slug = _pj_slug_from_cwd(c.cwd, pj_filter)
        th = text_hash(c.text)
        utterances.append(
            {
                "source_path": c.file,
                "line_no": c.line_no,
                "pj_slug": pj_slug,
                "session_id": c.session_id,
                "timestamp": c.timestamp,
                "text": c.text,
                "text_hash": th,
                "prev_action": c.prev_action,  # Must1: 実データから集約（D2 の整形は _format_prev_action を再利用）
                "source_kind": "dialogue",
                "extractor_version": EXTRACTOR_VERSION,
            }
        )
        unique_keys.append([c.session_id or "", c.timestamp, th])

    normalized_events: List[Dict[str, Any]] = []
    for pf in parents:
        for c in pf.candidates:
            normalized_events.append(
                {
                    "role": "user",
                    "text": c.text,
                    "tool_names": [],
                    "timestamp": c.timestamp,
                    "cwd": c.cwd,
                    "session_id": c.session_id,
                    "raw_type": c.channel,
                    "source_path": c.file,
                    "line_no": c.line_no,
                }
            )

    target_file_hashes = {
        pf.path: hashlib.sha256(Path(pf.path).read_bytes()).hexdigest()
        for pf in pj_parsed
    }

    return ProbeResult(
        counts=counts,
        utterances=utterances,
        target_file_hashes=target_file_hashes,
        normalized_events=normalized_events,
        unique_keys=unique_keys,
        unknown_type_pairs={f"{k[0]}|{k[1]}": v for k, v in unknown_type_pairs.items()},
        child_ref_scope_agreement=agreement,
        child_ref_scope_detail={"phase1_scope": len(children_b), "full_scope": len(children_a)},
    )


# ─────────────────────────────────────────────────────────────────
# 本番ストア非汚染ガード（実行前後の byte hash 不変検査）
# ─────────────────────────────────────────────────────────────────
def _hash_file_or_none(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def production_store_paths() -> Dict[str, Path]:
    """本番ストアの正典パスを解決する（read-only。書込は一切しない）。"""
    from correction_semantic.store import default_idioms_path, default_judged_path
    from utterance_archive.ingest import default_db_path
    from weak_signals.store import default_store_path as default_weak_signals_path

    return {
        "utterances_db": default_db_path(),
        "correction_judged": default_judged_path(),
        "correction_idioms": default_idioms_path(),
        "weak_signals": default_weak_signals_path(),
    }


def snapshot_production_hashes(paths: Dict[str, Path]) -> Dict[str, Optional[str]]:
    return {name: _hash_file_or_none(p) for name, p in paths.items()}


def verify_production_unchanged(
    before: Dict[str, Optional[str]], after: Dict[str, Optional[str]]
) -> Tuple[bool, List[str]]:
    """実行前後の hash を比較する。不一致は違反として列挙する。

    「実行前も実行後も不在（None==None）」は合格。「実行前は不在だが実行後に出現」
    （新規作成）は失敗として扱う（C-1 実行契約）。
    """
    violations: List[str] = []
    for name in before:
        b, a = before.get(name), after.get(name)
        if b != a:
            violations.append(f"{name}: before={b!r} after={a!r}")
    return (not violations), violations


def resolve_evolve_anything_data_dir() -> Path:
    """evolve-anything DATA_DIR を解決する（Must2: 個別4ファイルだけでなくディレクトリ
    全体の新規ファイル出現も検査するため）。"""
    import rl_common  # 遅延 import

    env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    return Path(rl_common.resolve_data_dir(env))


def snapshot_data_dir_listing(data_dir: Path) -> Dict[str, int]:
    """DATA_DIR 配下の全ファイルの {相対パス: サイズ} を返す（不在なら空 dict）。

    個別ファイル名を列挙する方式（4ファイルの hash だけ）だと、新種のファイルが
    増えたときに素通りする（Must2 レビュー指摘）。ディレクトリ全体の一覧比較で
    「ファイルが増えていないこと」まで検査する。
    """
    if not data_dir.exists():
        return {}
    out: Dict[str, int] = {}
    for f in sorted(data_dir.rglob("*")):
        if f.is_file():
            out[str(f.relative_to(data_dir))] = f.stat().st_size
    return out


def verify_data_dir_unchanged(
    before: Dict[str, int], after: Dict[str, int]
) -> Tuple[bool, List[str]]:
    """DATA_DIR 一覧の実行前後比較。新規ファイル・サイズ変化を違反として列挙する。"""
    violations: List[str] = []
    for name in sorted(set(before) | set(after)):
        b, a = before.get(name), after.get(name)
        if b != a:
            violations.append(f"{name}: before_size={b!r} after_size={a!r}")
    return (not violations), violations


# ─────────────────────────────────────────────────────────────────
# --out-dir 拒否ガード（Must2: 本番配下への書込を起動時に拒否する）
# ─────────────────────────────────────────────────────────────────
class ProductionPathWriteError(RuntimeError):
    """書込み先の実体パス（symlink 解決後）が禁止ルート配下だった場合に送出する。"""


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def forbidden_out_dir_roots() -> List[Path]:
    """--out-dir が指してはいけないルート（Must2）。

    ``~/.claude/``（``~/.claude/evolve-anything/`` を含む）/ ``~/.codex/`` /
    本スクリプトのリポジトリ作業ディレクトリ配下。
    """
    repo_root = Path(__file__).resolve().parent.parent
    return [Path.home() / ".claude", Path.home() / ".codex", repo_root]


def validate_out_dir(out_dir: Path) -> Optional[str]:
    """out_dir が禁止ルート配下なら理由文字列を返す。問題無ければ None。"""
    for root in forbidden_out_dir_roots():
        if _is_within(out_dir, root):
            return f"--out-dir は本番/リポジトリ配下を指せません: {out_dir} は {root} 配下です"
    return None


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────
def _write_json(path: Path, obj: Any) -> None:
    """out_dir 配下の出力ファイルへ書込む。

    欠陥2対応: validate_out_dir は out_dir 自体（ディレクトリ）にしか掛からず、
    out_dir 内に出力ファイル名（例: report.json）で本番ファイルへの symlink を
    置かれると、そのまま辿って本番ストアを上書きしてしまう。書込み直前に
    ``path`` の実体パス（symlink 解決後）を forbidden_out_dir_roots() と照合し、
    禁止ルート配下なら書かずに例外で停止する。呼び出し側が個別にチェックし
    忘れないよう、本関数に内蔵する（呼び出し箇所を経由すれば必ず効く）。
    """
    real = Path(os.path.realpath(path))
    for root in forbidden_out_dir_roots():
        if _is_within(real, root):
            raise ProductionPathWriteError(
                f"書込み先が禁止ルート配下を指しています（symlink 経由の可能性）: "
                f"{path} の実体は {real} で {root} 配下です"
            )
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions-root", type=Path, default=DEFAULT_SESSIONS_ROOT)
    parser.add_argument("--base-date", type=str, default=None, help="YYYY-MM-DD（既定: 実行日）")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--pj-filter", type=str, default=DEFAULT_PJ_FILTER)
    parser.add_argument(
        "--child-ref-scope", choices=["phase1", "full"], default="phase1",
        help="D4 参照集合の走査範囲（既定: phase1 限定・X2 実測に基づく）",
    )
    parser.add_argument("--out-dir", type=Path, default=None, help="既定: tempfile.mkdtemp()")
    args = parser.parse_args(argv)

    base_date = date.fromisoformat(args.base_date) if args.base_date else None
    out_dir = args.out_dir or Path(tempfile.mkdtemp(prefix="phase1_codex_probe_"))

    # Must2: 本番/リポジトリ配下への書込を、ディレクトリ作成より前に拒否する。
    forbid_reason = validate_out_dir(out_dir)
    if forbid_reason is not None:
        print(f"[phase1_codex_probe] FATAL: {forbid_reason}", file=sys.stderr)
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)

    store_paths = production_store_paths()
    data_dir = resolve_evolve_anything_data_dir()
    hashes_before = snapshot_production_hashes(store_paths)
    listing_before = snapshot_data_dir_listing(data_dir)

    result = run_probe(
        sessions_root=args.sessions_root,
        base_date=base_date,
        days=args.days,
        pj_filter=args.pj_filter,
        child_ref_scope=args.child_ref_scope,
    )

    from correction_semantic.batch import estimate_tokens

    token_estimate = estimate_tokens(result.utterances)

    # 成果物の書込（out_dir は Must2 で本番配下でないことを検証済み。個々の
    # 出力ファイル名が symlink 経由で本番へ逃げていないかは _write_json が
    # 書込み直前に実体パスで検査する＝欠陥2対応）。
    # report.json は「検査結果（production_store_guard）」を含まない業務成果物
    # として先に書く。hashes_after / listing_after は report.json を含む
    # **全ての** out_dir 書込みが完了した後に取る（欠陥1対応）。検査結果自体は
    # 別ファイル guard.json へ最後に書く（report.json の内容に検査結果を混ぜると
    # 「検査結果が検査対象の後に決まる」循環になり、報告書自身の書込みを
    # after スナップショットの観測範囲から外さざるを得なくなるため）。
    _write_json(out_dir / "target_files.json", result.target_file_hashes)
    _write_json(out_dir / "normalized_events.json", result.normalized_events)
    _write_json(out_dir / "unique_keys.json", result.unique_keys)
    _write_json(out_dir / "utterances.json", result.utterances)
    _write_json(out_dir / "token_estimate.json", token_estimate)

    report = {
        "counts": {
            "target_files": result.counts.target_files,
            "raw_user_utterances": result.counts.raw,
            "after_child_exclusion": result.counts.after_child_exclusion,
            "after_machinery_exclusion": result.counts.after_machinery_exclusion,
            "after_dedup": result.counts.after_dedup,
            "unattributed_dropped": result.counts.unattributed_dropped,
            "child_files": result.counts.child_files,
            "parse_error_lines": result.counts.parse_error_lines,
        },
        "unknown_type_pairs": result.unknown_type_pairs,
        "child_ref_scope_agreement": result.child_ref_scope_agreement,
        "child_ref_scope_detail": result.child_ref_scope_detail,
        "token_estimate": token_estimate,
        "out_dir": str(out_dir),
    }
    _write_json(out_dir / "report.json", report)

    # 欠陥1対応: report.json を含む全ての out_dir 書込みが完了した後に after を
    # 取る。ここより後に out_dir への書込みを追加しない（追加するなら guard.json
    # の書込みも観測対象へ含める設計へ組み直すこと）。
    hashes_after = snapshot_production_hashes(store_paths)
    listing_after = snapshot_data_dir_listing(data_dir)
    hash_ok, hash_violations = verify_production_unchanged(hashes_before, hashes_after)
    listing_ok, listing_violations = verify_data_dir_unchanged(listing_before, listing_after)
    ok = hash_ok and listing_ok
    violations = hash_violations + listing_violations

    guard = {
        "ok": ok,
        "violations": violations,
        "hashes_before": hashes_before,
        "hashes_after": hashes_after,
        "data_dir": str(data_dir),
        "data_dir_file_count_before": len(listing_before),
        "data_dir_file_count_after": len(listing_after),
    }
    _write_json(out_dir / "guard.json", guard)

    print(json.dumps({**report, "production_store_guard": guard}, ensure_ascii=False, indent=2))
    if not ok:
        print("[phase1_codex_probe] FATAL: 本番ストアが変更された", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
