"""utterance_archive.extractor — transcript jsonl → human 発話の抽出（#430）。

決定論・ゼロ LLM。design doc「抽出ロジック」「prev_action の定義」の SoT 実装。

抽出規則:
- human 発話のみ: ``type=user`` かつ ``message.role=user``。
  ``isMeta`` / ``toolUseResult`` / ``tool_result`` content block を除外。
- harness 注入除外（learning_trajectory_mining_machinery_turns 準拠）:
  ``<system-reminder`` / ``<command-name`` / ``<local-command`` / ``Caveat:`` /
  ``[Request interrupted`` / ``This session is being continued``。
- 長文（>2000 字）は ``source_kind='long_paste'`` でタグ保存（除外でなく分類）。
- 非対話 PJ（EXCLUDED_PJ_SLUGS）は ``source_kind='excluded_pj'`` タグ。

prev_action: 当該 human 発話より前で、直前の human 発話より後にある assistant
メッセージ群の tool_use 名を出現順に重複除去せず join、上限 10 個 + 超過時 ``…``。
assistant メッセージが無ければ None。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional

# extractor のバージョン。抽出ロジックを変えたら +1（再 ingest で source_kind 等を更新可能に）。
EXTRACTOR_VERSION = 1

# 長文ペーストの閾値（字数）。これを超えると source_kind='long_paste'。
LONG_PASTE_THRESHOLD = 2000

# 非対話 PJ（文字起こしノイズ等）。発話自体は取り込むが source_kind='excluded_pj' で分類。
# 値でなく文脈で落とす方針（後から判断を変えられる）。初期値は実測ノイズの 'bots'。
EXCLUDED_PJ_SLUGS = {"bots"}

# harness 注入マーカー（このいずれかを含む user 行は機構ターンとして除外）。
_HARNESS_MARKERS = (
    "<system-reminder",
    "<command-name",
    "<local-command",
    "Caveat:",
    "[Request interrupted",
    "This session is being continued",
)


@dataclass(frozen=True)
class Utterance:
    """1 件の human 発話レコード（utterances テーブル 1 行に対応）。"""

    source_path: str
    line_no: int
    pj_slug: str
    session_id: str
    timestamp: str
    text: str
    text_hash: str
    prev_action: Optional[str]
    source_kind: str
    extractor_version: int


# worktree セッションを本体 repo に帰属させるためのマーカー（cwd 中で切る位置）。
# #492: 切り詰めロジックは pj_slug.pj_slug_fast に移動。本定数は後方互換 re-export 用に残す。
_WORKTREE_MARKER = "/.claude/worktrees/"


def pj_slug_from_cwd(cwd: Optional[str]) -> Optional[str]:
    """transcript レコードの ``cwd`` から worktree 安全な pj_slug を導出する（#430）。

    encoded dir 名（``~/.claude/projects/`` 配下）は ``/`` と ``.`` が同じ ``-`` に
    潰れる非可逆エンコードのため ``evolve-anything`` のようなハイフン入り名を復元できない。
    そこで transcript 内の cwd（ファイルシステム非依存・削除済み PJ でも残る）を正に使う:

    1. cwd に ``/.claude/worktrees/`` が含まれればそこで切って本体側パスへ正規化
       （worktree セッションを main repo に帰属させる）
    2. pj_slug = 正規化後パスの basename

    cwd が None / 空なら None（呼び出し側が encoded dir 名へ fallback する）。

    #492: 導出ロジックは ``pj_slug.pj_slug_fast`` に単一ソース化した。本関数は
    後方互換のための thin wrapper（既存呼び出し元の一斉書き換えを避ける段階移行）。
    hot path 互換のため subprocess を呼ばない軽量版へ委譲する。
    """
    import sys as _sys

    _lib = str(Path(__file__).resolve().parent.parent)
    if _lib not in _sys.path:
        _sys.path.insert(0, _lib)
    from pj_slug import pj_slug_fast

    return pj_slug_fast(cwd)


def pj_slug_from_dir_name(dir_name: str) -> str:
    """cwd が一切取れないファイル用の fallback: encoded dir 名をそのまま使う。

    encoded 名のデコードは諦める（非可逆）。起源は source_path で追えるので、
    評価対象から外さず encoded 名のまま pj_slug にする（#430 オーケストレーター判断）。
    """
    return dir_name


def _text_hash(text: str) -> str:
    """重複除去用ハッシュ（sha256 先頭16桁）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _extract_text(content) -> Optional[str]:
    """user message.content から human テキストを取り出す。

    - str: そのまま human テキスト
    - list: text block のみ結合。tool_result block が 1 つでもあれば None（発話でない）
    - それ以外: None
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_result":
                return None  # tool 結果の user 行 = 発話でない
            if btype == "text":
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
        if not parts:
            return None
        return "\n".join(parts)
    return None


def _is_harness(text: str) -> bool:
    return any(marker in text for marker in _HARNESS_MARKERS)


def _tool_names_from_assistant(obj: dict) -> List[str]:
    """assistant メッセージから tool_use 名を出現順に取り出す。"""
    message = obj.get("message") or {}
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    names: List[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            name = block.get("name")
            if isinstance(name, str) and name:
                names.append(name)
    return names


def _format_prev_action(names: List[str]) -> Optional[str]:
    """tool 名列 → prev_action 文字列（上限 10 + 超過時 …）。空なら None。"""
    if not names:
        return None
    if len(names) > 10:
        return ",".join(names[:10]) + ",…"
    return ",".join(names)


def extract_utterances(
    jsonl_path: Path,
    pj_slug: str,
    start_line: int = 0,
) -> Iterator[Utterance]:
    """1 つの transcript jsonl から human 発話を抽出して yield する。

    Args:
        jsonl_path: transcript ファイル（``~/.claude/projects/<pj>/<session>.jsonl``）
        pj_slug:    cwd が取れない行用の fallback slug（通常は encoded dir 名）。
                    各行に cwd があれば ``pj_slug_from_cwd`` 由来が優先される（#430）。
        start_line: これ未満（0-index）の行はスキップ（増分 ingest 用）。
                    スキップしても assistant の prev_action 文脈は維持される。

    line_no は 1-index の実ファイル行番号（物理 PK に使う）。
    pj_slug の確定は EXCLUDED_PJ_SLUGS 判定（source_kind）にも効く。
    """
    jsonl_path = Path(jsonl_path)
    source_path = str(jsonl_path.resolve())

    # ファイル内で一度 cwd 由来 slug を確定したらキャッシュ（行ごとに cwd は通常同一）。
    resolved_slug: Optional[str] = None

    # 直前 human 以降に観測した assistant tool_use 名（次の human 発話の prev_action）。
    pending_tool_names: List[str] = []

    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
            for idx, line in enumerate(f):
                line_no = idx + 1  # 1-index
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(obj, dict):
                    continue

                # cwd 由来 slug を確定（最初に観測した cwd を採用、worktree は本体へ正規化）。
                if resolved_slug is None:
                    from_cwd = pj_slug_from_cwd(obj.get("cwd"))
                    if from_cwd:
                        resolved_slug = from_cwd

                otype = obj.get("type")
                message = obj.get("message")
                role = message.get("role") if isinstance(message, dict) else None

                # assistant メッセージ: prev_action の蓄積（出力はしない）
                if otype == "assistant" or role == "assistant":
                    pending_tool_names.extend(_tool_names_from_assistant(obj))
                    continue

                if otype != "user" or role != "user":
                    continue

                # tool 結果の user 行は発話でない
                if obj.get("toolUseResult") is not None:
                    pending_tool_names = []  # human ターン境界はリセットしない（tool 結果なので）
                    continue
                if obj.get("isMeta"):
                    continue

                text = _extract_text(message.get("content") if isinstance(message, dict) else None)
                if text is None:
                    continue
                text = text.strip()
                if not text:
                    continue
                if _is_harness(text):
                    continue

                # ここまで来たら human 発話確定。prev_action を確定し、蓄積をリセット。
                prev_action = _format_prev_action(pending_tool_names)
                pending_tool_names = []

                if idx < start_line:
                    continue  # 既処理（offset 以前）。文脈は更新済みなのでスキップのみ。

                effective_slug = resolved_slug if resolved_slug is not None else pj_slug
                if effective_slug in EXCLUDED_PJ_SLUGS:
                    kind = "excluded_pj"
                elif len(text) > LONG_PASTE_THRESHOLD:
                    kind = "long_paste"
                else:
                    kind = "dialogue"

                yield Utterance(
                    source_path=source_path,
                    line_no=line_no,
                    pj_slug=effective_slug,
                    session_id=str(obj.get("sessionId") or ""),
                    timestamp=str(obj.get("timestamp") or ""),
                    text=text,
                    text_hash=_text_hash(text),
                    prev_action=prev_action,
                    source_kind=kind,
                    extractor_version=EXTRACTOR_VERSION,
                )
    except OSError:
        return
