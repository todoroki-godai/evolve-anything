"""correction_semantic.bootstrap_backlog — 初回バックログ bootstrap モード（#443）。

既存の weak_signals バックログ（channel=llm_judge・未昇格）を初回 evolve 時にまとめて
確認する入口を提供する。設計（daily-evolve-reward-loop-design.md §機能#3 ハイブリッド方式）:

- アクティブ PJ のみ初回 bootstrap でまとめて確認（per-PJ 15-30 分）。残り PJ は日次 5 件 +
  TTL 45 日の自然失効に任せる。**bootstrap 対象 PJ の選択は実行時に AskUserQuestion で人間が
  選ぶ**（機械が「アクティブ」を判定しない）。本モジュールは決定論で「未消化 backlog の有無・
  PJ 別件数・group 化」を**常時 emit**し、判断材料を SKILL.md に渡すだけ。

build() は marker（bootstrap_done-<slug>.marker）未設定なら当該 PJ の未昇格 backlog を
内容キーワード jaccard≥0.5 で group 化して返す。marker が立っていたら is_bootstrap=False で
即返す（重い group 化をしない早期 return）。

実データ知見（設計 §機能#3）: idiom は正規化済み修正パターンでなく**生のユーザー発話断片**
（median 10 文字）なので文字列類似では圧縮がほとんど効かない。内容キーワード（漢字/カタカナ
2 字以上）jaccard≥0.5 が最も効くが圧縮率 15%（313→267・31 group が 77 件吸収）。

bootstrap は **現在 cwd の PJ slug の backlog のみ**を対象にする（DATA_DIR 全PJ共通 pitfall）。
marker は slug スコープ（bootstrap_done-<slug>.marker）+ writer_locus=batch で store_registry
宣言済み（#434）。dry-run ゼロ書込（pitfall_dryrun_stateful_store_write）: mark_done は
dry_run=True なら一切ファイルに触れない。build は読み取りのみ（marker を書かない）。

#442（weak_signals TTL）が並行実装中。expired 除外の reader API はまだ無い前提で、read 時に
レコードへ ``expired`` フィールドがあれば防御的に除外する（深い依存を作らない浅い連携）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from correction_semantic.idiom_filter import idiom_eligible
from correction_semantic.representative import user_only_text
from weak_signals.store import default_store_path, read_signals

MARKER_PREFIX = "bootstrap_done-"
MARKER_SUFFIX = ".marker"

# backlog の対象チャネル。#431 のバッチ LLM 意味判定レーン（313 件はこのチャネル）。
BACKLOG_CHANNEL = "llm_judge"

# 内容キーワードの jaccard 類似度しきい値（設計 §機能#3・実データで最も効いた値）。
JACCARD_THRESHOLD = 0.5

# 漢字 / カタカナ 2 字以上の連続を「内容キーワード」として抽出する。
# 助詞・1 字漢字・ひらがな語尾はノイズになるため拾わない（圧縮の効きを担保）。
_KEYWORD_RE = re.compile(r"[一-龥々]{2,}|[ァ-ヴー]{2,}")


# ─────────────────────────────────────────────────────────────────
# keyword 抽出 / jaccard grouping（決定論・LLM 非依存）
# ─────────────────────────────────────────────────────────────────
def extract_keywords(text: str) -> Set[str]:
    """発話断片から内容キーワード（漢字/カタカナ 2 字以上）の集合を返す。"""
    if not text:
        return set()
    return set(_KEYWORD_RE.findall(text))


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _idiom_text(rec: Dict[str, Any]) -> str:
    """weak_signal レコードから idiom 本文（発話断片）を取り出す。

    #528-3: assistant の過去レポート引用混入（「ℹ️ データ蓄積待ち…」等）を strip し
    user 発話のみを representative にする。
    """
    prov = rec.get("provenance") or {}
    return user_only_text(prov.get("text") or "")


def group_signals(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """未昇格 backlog レコードを内容キーワード jaccard≥0.5 で group 化する。

    各 group は ``{"representative": str, "signal_keys": [...], "size": int,
    "confirmable_idiom": str | None}``。keyword が抽出できない断片は単独 group のまま
    （圧縮されない＝実データの現実的下限）。取りこぼし防止: 全レコードがいずれかの group の
    signal_keys に必ず 1 回入る。

    #527-4: confirmable_idiom = この group を「はい」確定すると confirmed になる idiom テキスト。
    representative が idiom_filter の floor/stopword/context token を通る場合のみその text、
    過汎用なら None（AskUserQuestion で「confirmed になる idiom 無し」と提示できる）。

    決定論: 入力順を保った逐次 single-pass グルーピング（同じ入力で同じ出力）。
    """
    groups: List[Dict[str, Any]] = []
    group_kws: List[Set[str]] = []
    for rec in records:
        text = _idiom_text(rec)
        kws = extract_keywords(text)
        key = rec.get("signal_key", "")
        placed = False
        if kws:
            for gi, gk in enumerate(group_kws):
                if gk and _jaccard(kws, gk) >= JACCARD_THRESHOLD:
                    groups[gi]["signal_keys"].append(key)
                    group_kws[gi] = gk | kws
                    placed = True
                    break
        if not placed:
            groups.append({
                "representative": text,
                "signal_keys": [key],
                # #527-4: 過汎用 representative は confirmed 化対象にしない（None で提示）。
                "confirmable_idiom": text if idiom_eligible(text) else None,
            })
            group_kws.append(kws)
    for g in groups:
        g["size"] = len(g["signal_keys"])
    return groups


# ─────────────────────────────────────────────────────────────────
# backlog 読み取り（slug スコープ + 未昇格 + 防御的 expired 除外）
# ─────────────────────────────────────────────────────────────────
def _read_backlog(
    pj_slug: str,
    weak_signals_path: Optional[Path],
) -> List[Dict[str, Any]]:
    """当該 PJ slug の未昇格 llm_judge backlog を返す（expired は防御的に除外）。"""
    recs = read_signals(weak_signals_path)
    out: List[Dict[str, Any]] = []
    for r in recs:
        if r.get("pj_slug") != pj_slug:
            continue
        if r.get("channel") != BACKLOG_CHANNEL:
            continue
        if r.get("promoted"):
            continue
        # #442 TTL 連携（浅い防御的読み）: expired フィールドがあれば除外する。
        if r.get("expired"):
            continue
        out.append(r)
    return out


# ─────────────────────────────────────────────────────────────────
# marker（bootstrap_done-<slug>.marker）
# ─────────────────────────────────────────────────────────────────
def default_marker_path(pj_slug: str, base: Optional[Path] = None) -> Path:
    """bootstrap_done-<slug>.marker の正準パス（ADR-042 resolver 経由 / base 優先）。"""
    name = f"{MARKER_PREFIX}{pj_slug}{MARKER_SUFFIX}"
    if base is not None:
        return Path(base) / name
    import os

    import rl_common  # 遅延 import（hook/tool 文脈の patch 追従）

    env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    data_dir = rl_common.resolve_data_dir(env)
    return Path(data_dir) / name


def is_done(pj_slug: str, marker_path: Optional[Path] = None) -> bool:
    """当該 PJ の bootstrap が既に完了（marker 設定済み）かを返す。"""
    marker = marker_path if marker_path is not None else default_marker_path(pj_slug)
    return marker.exists()


def mark_done(
    pj_slug: str,
    marker_path: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """bootstrap 完了 marker を立てる（dry-run ゼロ書込ゲートを最下層まで貫通）。

    「まとめて確認」完了時・「TTL 失効に任せる」選択時のどちらでも呼ばれる。
    Returns: {"written": bool, "dry_run": bool, "path": str}
    """
    marker = marker_path if marker_path is not None else default_marker_path(pj_slug)
    if dry_run:
        return {"written": False, "dry_run": True, "path": str(marker)}
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("", encoding="utf-8")
    return {"written": True, "dry_run": False, "path": str(marker)}


# ─────────────────────────────────────────────────────────────────
# build: phase の本体（常時 emit）
# ─────────────────────────────────────────────────────────────────
def build(
    pj_slug: str,
    *,
    weak_signals_path: Optional[Path] = None,
    marker_path: Optional[Path] = None,
    idioms_path: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """初回（marker 未設定）のみ、当該 PJ の未昇格 backlog を group 化して返す。

    Returns:
      {"is_bootstrap": bool,   # marker 未設定なら True
       "pj_total": int,        # 当該 PJ の未昇格 backlog 件数
       "groups_total": int,    # 内容キーワード jaccard≥0.5 圧縮後の group 数
       "groups": [...],        # bootstrap 選択時に使う全 group（代表 idiom + signal_keys +
                               #   cross_pj_confirmed: [<他slug>, ...]・#462）
       "slug": str,
       "dry_run": bool}

    marker（bootstrap_done-<slug>.marker）が立っていたら is_bootstrap=False で即返す
    （重い group 化をしない早期 return）。build 自体は marker を書かない（読み取りのみ）。

    他 PJ で confirmed 済みの idiom と正規化テキスト一致する group は先頭へ優先表示し、
    cross_pj_confirmed ラベルを付与する（#462）。idioms_path は read 専用（書込なし）。
    """
    marker = marker_path if marker_path is not None else default_marker_path(pj_slug)

    if marker.exists():
        # 早期 return: 既消化。pj_total/groups は計算しない（重い group 化を回避）。
        return {
            "is_bootstrap": False,
            "pj_total": 0,
            "groups_total": 0,
            "groups": [],
            "slug": pj_slug,
            "dry_run": dry_run,
        }

    backlog = _read_backlog(pj_slug, weak_signals_path)
    groups = group_signals(backlog)
    # 他 PJ confirmed 一致 group を先頭へ + cross_pj_confirmed 付与（#462・read 専用）。
    from correction_semantic.cross_pj_priority import prioritize as _prioritize

    groups = _prioritize(groups, pj_slug, idioms_path=idioms_path)
    return {
        "is_bootstrap": True,
        "pj_total": len(backlog),
        "groups_total": len(groups),
        "groups": groups,
        "slug": pj_slug,
        "dry_run": dry_run,
    }
