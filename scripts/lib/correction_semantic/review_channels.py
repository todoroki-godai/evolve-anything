"""correction_semantic.review_channels — y/n 確認に出す weak チャネルの単一ソース（#99）。

evolve の対話昇格 phase（bootstrap_backlog / daily_review）はかつて channel="llm_judge"
固定で、決定論チャネル（#432）は ``evolve-reflect --promote-weak`` でしか昇格できなかった。
その結果「evolve をフル実行しても決定論 weak が queue から減らない」非対称が生じていた
（learning_weak_promotion_channel_asymmetry）。本モジュールは「**どのチャネルを y/n 確認に
出すか**」を 1 箇所に集約し、bootstrap_backlog / daily_review / promote が同じ集合を参照する
（コピー慣習の partial fix を避ける・pitfall_copied_parse_convention_partial_fix）。

チャネルは保存されている中身の濃さで 2 層に分かれる（実データ実測）:

- **content-rich（``REVIEW_CHANNELS``）** = y/n 確認に出して昇格に意味がある:
  - ``llm_judge``: provenance.text=ユーザー発話 + reason=Haiku 判定（#431）
  - ``rephrase``: provenance.text=言い直し後の発話（detector が保存・#432③）
  - ``permission_deny``: provenance.tool_name + tool_input_summary=拒否されたコマンド（#432②）
- **content-poor（``CONTENT_POOR_CHANNELS``）** = 個別昇格に出さない:
  - ``esc_interrupt``: provenance.evidence="[Request interrupted…]" のみ
  - ``manual_edit_after_ai``: provenance.evidence="File has been modified…" のみ
  detector（weak_signals/detectors.py）が「何が起きたか」の周辺文脈（ユーザー発話・直前 AI
  行動）を保存しないため、y/n 確認に出しても判断材料が無く、昇格しても message=channel 名の
  空 correction にしかならない。これらは observability の weak_signals matrix に件数として残り、
  集計シグナル（「N 回中断した」等）として surface する（個別 correction にはしない）。

決定論・LLM 非依存。
"""
from __future__ import annotations

import re
from typing import Any, Dict, FrozenSet, Set

from correction_semantic.representative import user_only_text

# y/n 確認に出す content-rich チャネル（単一ソース）。
REVIEW_CHANNELS: FrozenSet[str] = frozenset(
    {"llm_judge", "rephrase", "permission_deny"}
)

# 個別昇格に出さない content-poor チャネル（observability 集計のみ・#99）。
# 参照用に明示しておき「除外は意図的（detector が文脈未保存）」を機械可読に残す。
CONTENT_POOR_CHANNELS: FrozenSet[str] = frozenset(
    {"esc_interrupt", "manual_edit_after_ai"}
)

# permission_deny の representative に添える拒否コマンドの最大文字数。
_DENY_SUMMARY_TRUNC = 120


def is_review_channel(channel: Any) -> bool:
    """当該 channel が y/n 確認対象（content-rich）かを返す。"""
    return channel in REVIEW_CHANNELS


def signal_text(rec: Dict[str, Any]) -> str:
    """weak_signal レコードから channel 別の actionable 代表テキストを返す（決定論）。

    representative（確認 group の代表発話）と corrections の message 本文の両方がこの
    単一ソースを通す（チャネル別抽出を二重実装しない）:

    - ``llm_judge`` / ``rephrase``: provenance.text を user_only_text で user 発話のみ抽出
      （assistant 引用ブロック混入の除去・#528-3）
    - ``permission_deny``: tool_name + tool_input_summary（拒否されたコマンド）を合成。
      denial_reason は実データで "unknown" が大半なので、意味があるときだけ添える
    - content-poor / 未知 channel: "" を返す（呼び出し側が channel 名へ fallback）
    """
    prov = rec.get("provenance") or {}
    channel = rec.get("channel")

    if channel == "permission_deny":
        tool = str(prov.get("tool_name") or "").strip()
        summary = str(prov.get("tool_input_summary") or "").strip()
        reason = str(prov.get("denial_reason") or "").strip()
        head = f"{tool} の実行を拒否" if tool else "ツール実行を拒否"
        if summary:
            summary = " ".join(summary.split())
            if len(summary) > _DENY_SUMMARY_TRUNC:
                # 単語途中で切らず最後の空白境界で切って省略記号を添える（判読性）。
                cut = summary[:_DENY_SUMMARY_TRUNC]
                if " " in cut:
                    cut = cut.rsplit(" ", 1)[0]
                summary = cut + "…"
            head = f"{head}: {summary}"
        if reason and reason.lower() != "unknown":
            head = f"{head}（{reason}）"
        return head

    # llm_judge / rephrase（および text を持つ将来チャネル）は user 発話のみ抽出。
    return user_only_text(prov.get("text") or "")


# 拒否コマンドの latin トークン抽出（permission_deny の group 化用・#99 F1）。
_CMD_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")


def _strip_path_words(text: str) -> str:
    """空白区切りで '/' を含む語（絶対/相対パス）を落とす（#99 F1 follow）。

    実 dogfood: 拒否コマンドの作業ディレクトリ（例 ``/Users/.../docs-platform-drift-semantic``）
    から ``users/matsukaze/updater/docs/...`` 等の path segment が毎回 token 化され、別コマンド
    （push vs checkout）が共通パスの jaccard で同一 group に collapse していた。grouping は
    「何を拒否されたか（コマンド種別）」で決めるべきで、作業パスは分離軸から外す。
    """
    return " ".join(w for w in text.split() if "/" not in w)


def grouping_keywords(rec: Dict[str, Any]) -> Set[str]:
    """group 化に使うキーワード集合を channel 別に返す（決定論）。

    representative の表示テキスト（signal_text）とは**分離**する。permission_deny の
    signal_text は固定 head「<tool> の実行を拒否」しか漢字を持たず、extract_keywords が
    全件 {実行, 拒否} に潰れて**異なる拒否コマンドが 1 group に collapse する**（#99 F1）。
    そこで permission_deny だけは拒否コマンド（tool_name + tool_input_summary）の latin
    トークンで group 化し、別コマンド→別 group・同一コマンド→同 group にする。他チャネル
    （llm_judge / rephrase）は従来どおり signal_text の漢字/カタカナ keyword（挙動不変）。

    パス様トークン（'/' を含む語）は除外する（#99 F1 follow）。作業ディレクトリの segment が
    grouping を支配して別コマンドが collapse する over-merge を実 dogfood で発見したため。

    extract_keywords は bootstrap_backlog 側にあり、そちらが本モジュールを import するため、
    循環回避に関数内 import を使う（promote._correction_message と同じ確立パターン）。
    """
    if rec.get("channel") == "permission_deny":
        prov = rec.get("provenance") or {}
        raw = "{} {}".format(
            prov.get("tool_name") or "", prov.get("tool_input_summary") or ""
        )
        return set(_CMD_TOKEN_RE.findall(_strip_path_words(raw).lower()))

    from correction_semantic.bootstrap_backlog import extract_keywords

    return extract_keywords(signal_text(rec))
