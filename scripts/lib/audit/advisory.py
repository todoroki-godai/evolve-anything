"""audit の advisory observability section を組み立てる共通枠（#115 Phase 1）。

背景: observability builder（`_OBSERVABILITY_BUILDERS` に 25 個登録）が全て同じ契約
——「import 失敗 or 評価対象0 → None」「`## <title>` + 空行のヘッダ」「本文に ✓/⚠/ℹ の
マーカー」「末尾に空行 1 本の trailer」——を各自再実装していた。ここに集約して単一ソース化する。

本命の価値は行数削減でなく **header/trailer 規約を 1 箇所に固定し、`_OBSERVABILITY_BUILDERS`
横断の契約テスト（test_observability_contract）で回帰フェンスを張ること**。マーカー（✓/⚠/ℹ）
の付与と clean/hit の判断は render 側の責務にして、`sections_summary.classify_section`
（本文のマーカーで critical/watch/clean を分類）と整合させる。helper は header/trailer だけ
規約化し body には触れない（分類に干渉しない）。

2 層構成:
- L1（`advisory_header` / `finalize`）: 全 builder が使える header/trailer 規約の単一ソース。
- L2（`build_advisory_section`）: compute→applicable→render の FITS 形 builder 用オーケストレータ。
"""
from pathlib import Path
from typing import Any, Callable, List, Optional


def advisory_header(title: str, blurb: Optional[List[str]] = None) -> List[str]:
    """section 見出しを組む。`## ` は helper が付与する（呼び出し側は付けない）。

    blurb（節の説明行）があれば見出しの後に挟み、末尾に空行を置く。無ければ見出し + 空行のみ。
    """
    header = [f"## {title}", ""]
    if blurb:
        header += [*blurb, ""]
    return header


def finalize(header: List[str], body: List[str]) -> List[str]:
    """header + body に末尾空行 1 本を足して section 全体を返す。

    body は末尾空行を **含めない** 前提（trailer をここで 1 本だけ足す単一ソース）。
    """
    return header + body + [""]


def build_advisory_section(
    project_dir,
    *,
    title: str,
    compute: Callable[[Path], Any],
    applicable: Callable[[Any], bool],
    render: Callable[[Any], List[str]],
    blurb: Optional[List[str]] = None,
) -> Optional[List[str]]:
    """FITS 形 observability builder のオーケストレータ。

    Args:
        project_dir: 対象 PJ（str/Path どちらでも可、`Path` に正規化して compute へ渡す）。
        title: 見出し文言（`## ` は付けない）。
        compute: import を内包し report/data を返す。import 失敗時は None を返すこと
            （import のみ try/except で包み、検出本体の例外は伝播させる＝既存 builder と同挙動）。
        applicable: data がこの PJ で評価対象か（False → section 全体 None＝沈黙）。
        render: ✓/⚠/ℹ を含む本文行を返す（clean/hit の判断は render 側）。末尾空行は含めない。
        blurb: 見出し直後に置く節の説明行（任意）。

    Returns:
        None（非該当）or `advisory_header(title, blurb) + render(data) + [""]`。
    """
    data = compute(Path(project_dir))
    if data is None or not applicable(data):
        return None
    return finalize(advisory_header(title, blurb), render(data))
