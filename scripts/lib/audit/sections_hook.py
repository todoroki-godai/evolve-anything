"""Hook drift（他ツール追従 hook の陳腐化）の observability セクション生成。

sections.py が行数バジェットに迫っているため、eval_saturation（sections_eval.py）と
同様に独立モジュールへ切り出す。observability contract から参照される `build_*_section`
契約（`(project_dir) -> Optional[List[str]]`）は他 builder と同一。

gstack のフロー定義は環境グローバル（~/.gstack 配下）のため project_dir には依存しない
（eval-sets と同じ「環境グローバル系 builder」）。引数は contract 互換のため受け取るだけ。
"""
from pathlib import Path
from typing import List, Optional


def build_hook_drift_section(project_dir: Path) -> Optional[List[str]]:
    """gstack 追従 hook の stale_pin を audit に surface する。

    flow-chain.json は手動メンテされる SoT で gstack 本体は生成しない（#319）。手で書いた
    `gstack_version` ピンが実環境（.last-setup-version）から取り残されると、hook が古い
    フロー構成を提案し続ける。evolve は audit を消費するため、evolve のたびに追従漏れが
    可視化される — 手動の見直しに依存しない配線。

    観測可能性:
    - .gstack / flow-chain.json 不在 → None（gstack 未導入環境は対象外で沈黙）
    - version 一致 → 「評価したが drift なし ✓」（silence != evaluated）
    - 不一致 → ⚠ で両 version と見直し誘導
    """
    try:
        import hook_drift
    except ImportError:
        return None

    report = hook_drift.check_hook_drift()
    if not report.applicable:
        return None  # gstack 未導入環境 → 沈黙

    header = ["## Hook Drift (gstack flow 追従)", ""]
    if not report.stale_pin:
        if report.actual_version is None:
            # flow-chain はあるが実 version が読めない（判定不能）。評価した痕跡は残す。
            return header + [
                f"ℹ flow-chain.json は gstack {report.pinned_version} 想定。"
                " 実環境 version（.last-setup-version）が読めず追従判定は保留。",
                "",
            ]
        return header + [
            f"✓ 評価したが drift なし（flow-chain.json は gstack "
            f"{report.pinned_version} 想定で実環境と一致）",
            "",
        ]

    gap = (
        f"（MINOR {report.minor_gap} 差）" if report.minor_gap else ""
    )
    return header + [
        f"⚠ gstack flow 追従漏れ: flow-chain.json は gstack "
        f"{report.pinned_version} 想定だが実環境は {report.actual_version} "
        f"{gap}。flow-chain.json は手動メンテされる SoT（gstack は生成しない、#319）。"
        f"`gstack_version` を実環境 {report.actual_version} に手で更新し、"
        "フローチェーンが最新スキル構成を反映しているか併せて見直しを推奨。",
        "",
    ]
