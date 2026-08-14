"""utterance_archive.extractor — transcript jsonl → human 発話の抽出（#430）。

決定論・ゼロ LLM。design doc「抽出ロジック」「prev_action の定義」の SoT 実装。

抽出規則:
- human 発話のみ: ``type=user`` かつ ``message.role=user``。
  ``isMeta`` / ``toolUseResult`` / ``tool_result`` content block を除外。
- sidechain 除外（#379 ADR-054 §5-A1）: ``isSidechain: true`` の行は行単位で丸ごと除外
  （human 発話にも prev_action にも寄与しない）。実データでは main-level transcript に
  isSidechain:true は現れず、専ら ``*/subagents/*.jsonl``（ingest.py 側でファイル単位
  除外済み）に限られる。本チェックは第二防御。
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
from typing import Dict, Iterator, List, Optional

# extractor のバージョン。抽出ロジックを変えたら +1（再 ingest で source_kind 等を更新可能に）。
# v2: #323 で harness 注入判定に rl_common.detection.is_machinery_prompt を追加適用
# v3: #379 ADR-054 A1。(a) isSidechain 行単位除外を追加 (b) pending_tool_names を
#     tool_result 行で誤ってリセットしていたバグを修正（prev_action が全件 null だった
#     root cause）。両方とも新規 ingest 行にのみ効き、既存の v2 行は再 ingest migration
#     （次PR）まで prev_action=null のまま残る。
# v4: #445。``[Image #N]`` 画像添付プレースホルダを strip（実コーパス実測: bare 添付
#     0件・全件が同じ text block 内に人間の実テキストを伴う。全文除外でなく strip）。
#     マーカーのみ（strip 後に空）の行は非発話として除外する。
# （既存の書込済み行は増分 ingest（mtime 判定）では再走査されないため遡及修正されない。
# EXTRACTOR_VERSION は将来 version 差分ベースの再抽出/purge を実装する際の準備として
# 記録するのみで、本 PR 時点でそれを読む consumer はまだ無い）。
EXTRACTOR_VERSION = 4

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


def _strip_image_placeholders(text: str) -> str:
    """rl_common.detection.strip_image_placeholders への遅延 import 委譲（#445）。

    ``[Image #N]`` 添付プレースホルダの strip ロジックは単一ソース
    （rl_common.detection）。extractor 側で正規表現を複製すると片側だけ改修して
    desync する（pitfall_copied_parse_convention_partial_fix と同型）ため、ここでは
    委譲するだけに留める（`_is_machinery_prompt_shared` と同じパターン）。
    """
    import sys as _sys

    _lib = str(Path(__file__).resolve().parent.parent)
    if _lib not in _sys.path:
        _sys.path.insert(0, _lib)
    from rl_common.detection import strip_image_placeholders

    return strip_image_placeholders(text)


def _has_image_placeholder(text: str) -> bool:
    """rl_common.detection.has_image_placeholder への遅延 import 委譲（#445）。

    strip 前に判定することで、呼び出し側（``extract_utterances``）が「strip して
    人間の実テキストが残った件数」と「strip したら空になった（bare 添付）件数」を
    別カウンタで observability に surface できるようにする（黙って減らさない）。
    """
    import sys as _sys

    _lib = str(Path(__file__).resolve().parent.parent)
    if _lib not in _sys.path:
        _sys.path.insert(0, _lib)
    from rl_common.detection import has_image_placeholder

    return has_image_placeholder(text)


def _extract_text(content) -> Optional[str]:
    """user message.content から human テキストを取り出す（raw・strip 前）。

    - str: そのまま human テキスト
    - list: text block のみ結合。tool_result block が 1 つでもあれば None（発話でない）
    - それ以外: None

    ``[Image #N]`` プレースホルダの strip は呼び出し側（``extract_utterances``）が
    行う（strip 前後の件数を別カウンタで observability に出すため、ここでは raw の
    まま返す）。
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
    if any(marker in text for marker in _HARNESS_MARKERS):
        return True
    return _is_machinery_prompt_shared(text)


def _is_machinery_prompt_shared(text: str) -> bool:
    """rl_common.detection.is_machinery_prompt への遅延 import 委譲（#323）。

    Stop hook 自己注入（"Stop hook feedback:"）・SKILL.md 本体注入（"Base directory for
    this skill:"）等、extractor 固有の `_HARNESS_MARKERS` がカバーしない機構ターンを
    遮断する。writer 側（`hooks/correction_detect.py` 経由の `should_include_message`）は
    #335 でこの関数に単一ソース化済みだったが、utterance_archive.extractor は独立した
    マーカー一覧を持ち続けていたため漏れていた。この漏れにより Stop hook nag が
    `source_kind='dialogue'` として utterances.db に取り込まれ、llm_judge チャネル
    （correction_semantic/batch.py が utterances.db を判定対象として読む）の判定対象に
    混入していた（実測: 実 DB dialogue 26 件のリーク・5 PJ・#323）。
    """
    import sys as _sys

    _lib = str(Path(__file__).resolve().parent.parent)
    if _lib not in _sys.path:
        _sys.path.insert(0, _lib)
    from rl_common.detection import is_machinery_prompt

    return is_machinery_prompt(text)


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
    stats: Optional[Dict[str, int]] = None,
) -> Iterator[Utterance]:
    """1 つの transcript jsonl から human 発話を抽出して yield する。

    Args:
        jsonl_path: transcript ファイル（``~/.claude/projects/<pj>/<session>.jsonl``）
        pj_slug:    cwd が取れない行用の fallback slug（通常は encoded dir 名）。
                    各行に cwd があれば ``pj_slug_from_cwd`` 由来が優先される（#430）。
        start_line: これ未満（0-index）の行はスキップ（増分 ingest 用）。
                    スキップしても assistant の prev_action 文脈は維持される。
        stats:      呼び出し側が渡す任意の観測カウンタ dict（in-place 加算・#445）。
                    渡された場合、``[Image #N]`` プレースホルダの扱いを2種の別カウンタで
                    黙って減らさず surface する:
                      - ``image_placeholder_stripped``: marker を除去し人間の実テキストが
                        残った件数（救済＝発話として抽出される）
                      - ``image_placeholder_only_excluded``: marker だけで strip 後に空
                        （bare な画像添付のみ）になり非発話として除外した件数
                    2つは意味が違うため混ぜない（前者は救済、後者は除外）。

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

                # sidechain（Task サブエージェント内部会話）の行は human 発話でも main の
                # tool 使用でもないため丸ごと除外する（#379 ADR-054 §5-A1「除外の粒度」）。
                # 実データ検証（2026-08-13）: main-level transcript（``<pj>/*.jsonl``）は
                # 2464 ファイル全数走査で isSidechain:true = 0 件、``*/subagents/*.jsonl``
                # 側は 30 ファイルサンプルで 7323/7323 行が true。一次防御は ingest.py 側の
                # ファイル走査除外（``*/subagents/*.jsonl`` を候補から外す）。ここでの行単位
                # チェックは extract_utterances が sidechain 混在ファイルへ直接呼ばれた場合
                # （将来の harness 変更・別呼び出し元）の第二防御。continue のみで
                # pending_tool_names には一切触れない（sidechain 内 assistant の tool_use を
                # main の prev_action に混ぜない＝持ち越し禁止）。
                if obj.get("isSidechain") is True:
                    continue

                otype = obj.get("type")
                message = obj.get("message")
                role = message.get("role") if isinstance(message, dict) else None

                # assistant メッセージ: prev_action の蓄積（出力はしない）
                if otype == "assistant" or role == "assistant":
                    pending_tool_names.extend(_tool_names_from_assistant(obj))
                    continue

                if otype != "user" or role != "user":
                    continue

                # tool 結果の user 行は発話でない。pending_tool_names は保持する。
                # #379 実測で判明した root cause: 以前はここで pending_tool_names を
                # リセットしており、tool_use の直後に必ず続く tool_result 行で毎回蓄積が
                # 消えるため、extractor_version=2 の行は 100%（実測窓 1,124件全件）
                # prev_action=null になっていた。直前の行コメントは「リセットしない」と
                # 書かれていたが実装が逆だった＝コメント通りに直す。
                if obj.get("toolUseResult") is not None:
                    continue
                if obj.get("isMeta"):
                    continue

                text = _extract_text(message.get("content") if isinstance(message, dict) else None)
                if text is None:
                    continue
                # #445: [Image #N] プレースホルダの strip は marker の有無を先に判定して
                # から行う。strip したら空（bare な画像添付のみ）か、実テキストが残った
                # かで別カウンタに分ける（前者は除外・後者は救済。混ぜない）。
                # stats への加算は below の ``idx < start_line`` 判定より**後**でのみ行う
                # （codex round1 [Must]3）: 増分 ingest は prev_action 文脈復元のため
                # 既処理行（offset 以前）も再走査するので、加算をここで行うと追記のたびに
                # 過去分の画像プレースホルダを再計上してしまう。判定結果（had_image_placeholder
                # / text の空非空）はここで確定するが、``stats`` への書込は保留する。
                had_image_placeholder = _has_image_placeholder(text)
                if had_image_placeholder:
                    text = _strip_image_placeholders(text)
                else:
                    text = text.strip()
                is_pre_offset = idx < start_line  # 判定のみ先出し（continue はまだしない）

                if not text:
                    # bare な画像添付のみ（strip 後ゼロ）は非発話として扱う。既存の空
                    # テキスト除外と同じ経路（prev_action 文脈は更新しない）。stats は
                    # post-offset のみ加算する（bare marker はここで continue するため、
                    # start_line 判定を通過する一般経路とは別に判定する・codex round1 [Must]3）。
                    if had_image_placeholder and stats is not None and not is_pre_offset:
                        key = "image_placeholder_only_excluded"
                        stats[key] = stats.get(key, 0) + 1
                    continue
                if _is_harness(text):
                    continue

                # ここまで来たら human 発話確定。prev_action を確定し、蓄積をリセット。
                prev_action = _format_prev_action(pending_tool_names)
                pending_tool_names = []

                if is_pre_offset:
                    continue  # 既処理（offset 以前）。文脈は更新済みなのでスキップのみ。

                if had_image_placeholder and stats is not None:
                    key = "image_placeholder_stripped"
                    stats[key] = stats.get(key, 0) + 1

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
