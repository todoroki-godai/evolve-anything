"""icebox 3レーン棚卸しの observability セクション生成（#352, advisory）。

daily runner の icebox 3レーン棚卸しステップが書いた `icebox-verdicts.json` を読むだけの
保険経路（#194/#351「daily runner が壊れていても pull で全景が出る」教訓）。**gh を一切
呼ばない**（決定論・ゼロネットワーク I/O）。レーン1「成立」は SessionStart が名指し通知する
役目のため、ここでは件数のみ添える。レーン2「観測器不在」とレーン3「失効候補」を主対象に
advisory surface する。

観測可能性契約（build_judge_audit_section と同契約）:
- 当PJが evolve-anything 本体でない（`.claude-plugin/plugin.json` 無し）→ None（沈黙）
- icebox-verdicts.json 未生成 / 壊れている → None（沈黙。daily runner 未実行なだけ）
- verdicts が空リスト → None（沈黙。評価対象なし）
- 1件以上 → レーン別件数 + 観測器不在/失効候補の詳細（up to 10件・staleness advisory）
"""
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .advisory import build_advisory_section

# 観測器不在 / 失効候補の詳細列挙をこの件数で打ち切る（audit 本文が長くなりすぎないため）。
MAX_LISTED_ISSUES = 10


def _is_self_repo(project_dir: Path) -> bool:
    return (Path(project_dir) / ".claude-plugin" / "plugin.json").exists()


def _lane_lines(items: List[Dict[str, Any]], *, label: str, suffix: str) -> List[str]:
    """観測器不在（レーン2）/ 失効候補（レーン3）の描画（#352 P7）。

    2レーンの描画がほぼ同一のコピペだった（片側だけ直して desync する典型構造）ため、
    ここへ1本化する。0件なら「✓」の1行、1件以上なら見出し + up to MAX_LISTED_ISSUES 件の
    箇条書き + 残り件数のサマリを返す。
    """
    if not items:
        return [f"  ・{label}: 0件 ✓"]
    lines = [f"  ・⚠ {label}: {len(items)}件 — {suffix}"]
    for v in items[:MAX_LISTED_ISSUES]:
        lines.append(f"      #{v.get('number')}: {v.get('reason')}")
    remaining = len(items) - MAX_LISTED_ISSUES
    if remaining > 0:
        lines.append(f"      ...他 {remaining} 件")
    return lines


def build_icebox_reconcile_section(project_dir: Path) -> Optional[List[str]]:
    """icebox 3レーン棚卸し結果を audit に advisory 表示する（決定論・gh 非呼び出し）。"""

    def compute(proj: Path) -> Optional[Dict[str, Any]]:
        if not _is_self_repo(proj):
            return None
        try:
            from daily import icebox_notice as _ibn
            import rl_common
        except ImportError:
            return None
        env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
        data_dir = rl_common.resolve_data_dir(env)
        payload = _ibn.read_icebox_verdicts(data_dir)
        if payload is None:
            return None
        verdicts = payload.get("verdicts")
        if not isinstance(verdicts, list) or not verdicts:
            return None
        return {"payload": payload, "verdicts": verdicts}

    def render(data: Dict[str, Any]) -> List[str]:
        from daily import icebox_notice as _ibn

        payload = data["payload"]
        # #352 P5: 想定外形状（非 dict 要素）が混入していても audit 全体を巻き込まない。
        verdicts: List[Dict[str, Any]] = [
            v for v in data["verdicts"] if isinstance(v, dict)
        ]
        met = [v for v in verdicts if v.get("lane") == "met"]
        observer_missing = [v for v in verdicts if v.get("lane") == "observer_missing"]
        archive_candidates = [v for v in verdicts if v.get("lane") == "archive_candidate"]

        body: List[str] = []
        stale = _ibn.stale_advisory(payload.get("generated_at"), None)
        if stale:
            body.append(f"  ・⚠ {stale}")
        if payload.get("truncated"):
            # #352 B8: gh --limit に到達＝レーン3「失効候補」の最古集合が欠落している疑い。
            body.append(
                "  ・⚠ daily runner の gh issue list が --limit 上限に到達しています"
                "（実件数がこれを超える場合、レーン3の最古集合を取得できていません）"
            )

        body.append(
            f"  ・成立（レーン1）: {len(met)}件 — SessionStart で名指し通知（詳細はそちら）。"
        )
        body.extend(
            _lane_lines(
                observer_missing,
                label="観測器不在（レーン2）",
                suffix="observer を実装すれば自動判定に乗ります:",
            )
        )
        body.extend(
            _lane_lines(
                archive_candidates,
                label="失効候補（レーン3）",
                suffix="自動 close はしません（棚卸し検討のみ）:",
            )
        )

        return body

    return build_advisory_section(
        project_dir,
        title="Icebox Reconcile (当PJ・advisory — #352)",
        blurb=[
            "icebox（凍結 issue）本文の `## 再開条件` reopen-when ブロックを実ストアと"
            "決定論突合した3レーン判定です（daily runner が毎朝生成・gh 非呼び出し・"
            "ゼロ LLM）。成立は SessionStart が名指し通知し、観測器不在・失効候補をここで surface します。",
        ],
        compute=compute,
        applicable=lambda _data: True,
        render=render,
    )
