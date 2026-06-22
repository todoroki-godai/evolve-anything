"""記憶操作 capability の observability セクション生成（#19, advisory）。

記憶を read/use/write/maintain の観点（OPD-Evolver, arXiv 2606.17628 由来）で評価し、
記憶の死蔵・未活用を audit に advisory 表示する。スコア重みには入れない
（outcome_metrics と同じ advisory レーン, ADR-046 と同方針）。

reason 非永続化のため read/use を統合し3軸算出する（write / maintain / use_read）。
詳細は ``memory_capability.compute_memory_capability`` を参照。

返り値契約は ``sections_outcome.build_outcome_metrics_section`` と同スタイル:
- module import 失敗 → None（沈黙）
- 評価対象なし（memory dir 不在 / 実体 0 件）→ None（沈黙。
  silence != evaluated は評価対象がある場合のみ適用）
- 1 件以上 → ヘッダ + 説明文 + 3軸の行（各軸 evidence 併記）
"""
from pathlib import Path
from typing import Any, Dict, List, Optional


def _axis_line(label: str, axis: Dict[str, Any], direction: str, evidence: str) -> List[str]:
    """1 軸を value + 方向性 + evidence で 2 行にして返す。"""
    value = axis.get("value")
    if value is None:
        return [f"  ・{label}: データ不足"]
    return [
        f"  ・{label}: {value:.2f} — {direction}",
        f"      evidence: {evidence}",
    ]


def build_memory_capability_section(project_dir: Path) -> Optional[List[str]]:
    """記憶操作 capability 3軸を audit に advisory 表示する（決定論・LLM 非依存）。

    観測可能性:
    - memory_capability モジュール未解決 → None（沈黙）
    - 当 PJ に memory 実体が 1 件も無い（dir 不在 / MEMORY.md のみ）→ None（沈黙）
    - 1 件以上 → ヘッダ + 説明文 + 3 軸を出力
    """
    try:
        import memory_capability
    except ImportError:
        return None

    result = memory_capability.compute_memory_capability(Path(project_dir))
    if not result.get("applicable"):
        return None

    total = result["total"]
    write = result["write"]
    maintain = result["maintain"]
    use_read = result["use_read"]

    header = [
        "## Memory Capability (read/use/write/maintain — advisory, スコア重みには未反映)",
        "",
        f"記憶（メモリ）の書き込み・維持・活用を 3 つの観点で評価します"
        f"（当 PJ memory {total} 件）。記憶が死蔵・未活用になっていないかの可視化が目的で、"
        f"スコアの重みには反映しません（参考: OPD-Evolver 論文 arXiv 2606.17628, #19）。"
        f"LLM を使わず決定論で算出。",
        "",
    ]

    we = write["evidence"]
    me = maintain["evidence"]
    ue = use_read["evidence"]
    body: List[str] = []
    body.extend(
        _axis_line(
            "write（記憶量）",
            write,
            "高いほど良い（記憶を構造化して書けている）",
            f"frontmatter あり {we.get('with_frontmatter', 0)} / 総 {we.get('total', 0)} 件",
        )
    )
    body.extend(
        _axis_line(
            "maintain（維持・健全度）",
            maintain,
            "高いほど良い（腐敗を管理できている）",
            f"stale {me.get('stale', 0)} / superseded {me.get('superseded', 0)} "
            f"/ 総 {me.get('total', 0)} 件",
        )
    )
    body.extend(
        _axis_line(
            "use/read（活性）",
            use_read,
            "高いほど良い（書いた記憶が温められている）",
            f"reinforced {ue.get('reinforced', 0)} / 総 {ue.get('total', 0)} 件 "
            f"/ update_count 中央値 {ue.get('update_count_median', 0.0):.1f}",
        )
    )

    # use/read 軸の限界注記（#19 設計・senpai 検証済み）。
    note = [
        "",
        "  ※ use/read 軸の限界: reinforce は SessionStart で有効 memory 全件に発火するため "
        "per-memory の recall 回数ではなく PJ の活性 proxy。recall/注入の内訳は "
        "reason 永続化（別 issue 提案予定）後に分離可能。",
    ]

    return header + body + note + [""]
