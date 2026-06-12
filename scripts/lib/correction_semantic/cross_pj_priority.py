"""correction_semantic.cross_pj_priority — confirmed idiom の PJ 横断優先提示（#462）。

ある PJ で人間が承認（confirmed=True）した idiom と**正規化テキスト一致**する他 PJ の
未確認 idiom group を、daily_review（#446）/ bootstrap_backlog（#443）の提示順で**先頭に
優先表示**し、「他 PJ（<slug>）で承認済み」を示す機械可読フィールド `cross_pj_confirmed`
（[<slug>, ...]）を各 group に付与する。

設計の核（issue #462）:
- idiom_autopromote（#447）の照合は pj_slug × idiom テキスト単位。「git status じゃなくて
  git diff」のような全 PJ 共通の修正癖でも PJ ごとに別々の y/n 承認が要る。人間確認の帯域
  （daily_review 最大5件/日）が律速なので、同義 idiom の PJ 数ぶんの重複確認はスループットを
  直接削る。confirmed 済みの他 PJ idiom と一致する group を**先頭に出して判断材料を足す**。
- **自動 confirmed 化・自動昇格はしない**（ADR-047 不変条件「人間が承認していないパターンは
  絶対に自動昇格しない」を維持）。本モジュールは提示順とラベルだけ変える純関数で、ストアに
  一切書かない（read 専用）。承認経路は #463 の通常フロー（rl-reflect --promote-weak）が担う。

正規化の流用（Success Criteria #462）: テキスト一致は store.normalize_idiom_text を通す。
idiom_autopromote と**同じ 1 関数**を共有し、正規化ロジックを二重実装しない。

決定論・LLM 非依存。correction_idioms.jsonl は全 PJ 共通の単一ストア（レコードに pj_slug あり）
なので、cross-PJ 照合は store.read_cross_pj_confirmed_idiom_texts で「他 slug の confirmed
テキスト集合」を読むだけで決定論にできる。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from correction_semantic.store import (
    normalize_idiom_text,
    read_cross_pj_confirmed_idiom_texts,
)

CROSS_PJ_FIELD = "cross_pj_confirmed"


def _group_texts(group: Dict[str, Any]) -> List[str]:
    """group から照合対象の正規化テキスト候補を取り出す（daily_review / bootstrap 両形対応）。

    daily_review group: ``idiom``（個人辞書照合済み・None あり）/ ``representative``。
    bootstrap group: ``representative`` のみ。idiom が None でも representative で照合できる。
    """
    out: List[str] = []
    for key in ("idiom", "representative"):
        norm = normalize_idiom_text(group.get(key))
        if norm and norm not in out:
            out.append(norm)
    return out


def prioritize(
    groups: List[Dict[str, Any]],
    pj_slug: str,
    *,
    idioms_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """group 群に cross_pj_confirmed ラベルを付け、他 PJ confirmed 一致 group を先頭へ並べ替える。

    各 group（dict）には ``cross_pj_confirmed``: [<他slug>, ...] を**常時付与**する
    （一致なしは []）。並びは「一致 group（入力順保存）→ 非一致 group（入力順保存）」の
    安定 partition。入力 group dict はその場で変更しつつ、同じ参照を含む新リストを返す
    （呼び出し側が頻度ソート済みの順を渡す前提なので、一致内・非一致内の相対順は維持する）。

    **read 専用**: confirmed 化・昇格・ストア書込は一切しない（ADR-047 不変条件）。
    idioms_path が None でも default resolver 経由で他 PJ confirmed を読む（実運用経路）。
    confirmed が他 PJ に 1 件も無ければ全 group が cross_pj_confirmed=[] で並びは不変。
    """
    cross = read_cross_pj_confirmed_idiom_texts(pj_slug, idioms_path)

    matched: List[Dict[str, Any]] = []
    unmatched: List[Dict[str, Any]] = []
    for g in groups:
        slugs: List[str] = []
        for text in _group_texts(g):
            for s in cross.get(text, []):
                if s not in slugs:
                    slugs.append(s)
        g[CROSS_PJ_FIELD] = slugs
        if slugs:
            matched.append(g)
        else:
            unmatched.append(g)

    return matched + unmatched
