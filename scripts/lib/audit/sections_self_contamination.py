"""自己汚染ハルシネーション指紋の observability セクション生成（Layer 2・advisory・read-only）。

opus 1M の長大セッションで、assistant が tool_result 原文に存在しない偽指示を自己生成し、それを
「外部汚染」と誤認してラッチする現象（生タグ漏出 / 偽 system-reminder / 汚染宣言×原文非在）を、
既存 transcript を audit 実行時に走査して **指紋率として恒久計測** する。live hook（Layer 1）を
作る前に、まず audit で価値と検出精度を証明するのが狙い。hook / store は新設せず read-only。

検出コアは ``self_contamination_scan``（純関数・ゼロ LLM）。ここは per-PJ の transcript dir を
解決して mtime 窓で走査し、3 Family の件数 + period-over-period + 代表例（作話 vs 直前 tool_result
原文の対比）を advisory surface する。

観測可能性契約（build_verbosity_section / build_subagent_traces_section と同契約）:
- transcript dir 不在 / 走査不能 → None（沈黙）
- 指紋ゼロ（clean）→ None（沈黙・低ノイズ優先の advisory レーン）
- 指紋あり → ⚠ + 件数 + period 推移 + 代表例（#394: 数字だけでなく根拠まで）
"""
from pathlib import Path
from typing import List, Optional

from self_contamination_scan import (
    ProjectScanReport,
    resolve_cc_transcript_dir,
    scan_project_transcripts,
)

from .advisory import build_advisory_section

_MAX_EXAMPLES = 3


def build_self_contamination_section(project_dir: Path) -> Optional[List[str]]:
    """自己汚染指紋を audit に advisory surface する（決定論・LLM 非依存・read-only）。"""

    def compute(proj: Path) -> Optional[ProjectScanReport]:
        tdir = resolve_cc_transcript_dir(proj)
        if not Path(tdir).is_dir():
            return None
        result = scan_project_transcripts(tdir)
        if result is None or result.report.total == 0:
            # 評価対象なし（transcript 不在）or 指紋ゼロ（clean）→ 沈黙。
            return None
        return result

    def render(result: ProjectScanReport) -> List[str]:
        rep = result.report
        c = rep.counts()
        arrow = "→"
        body: List[str] = [
            f"⚠ 自己汚染指紋 {rep.total} 件を検出（走査 {result.files_scanned} セッション）。"
            "tool_result 原文に無い偽指示を assistant が自己生成した痕跡です。",
            f"  ・生タグ漏出 (A): {c['A']} 件 — 生の invoke/function_calls タグが text に漏出",
            f"  ・偽 system-reminder (B): {c['B']} 件 — harness 注入のはずのタグを自己出力",
            f"  ・汚染宣言×原文非在 (C): {c['C']} 件 — 引用リテラルが直前 tool_result 原文に不在",
        ]

        # period-over-period（mtime 窓 baseline vs recent の推移）。
        rc, bc = result.recent_counts, result.baseline_counts
        rc_total = rc["A"] + rc["B"] + rc["C"]
        bc_total = bc["A"] + bc["B"] + bc["C"]
        body.append(
            f"  ・推移（baseline {bc_total} 件 {arrow} 直近 {rc_total} 件・mtime 窓）: "
            f"A {bc['A']}{arrow}{rc['A']} / B {bc['B']}{arrow}{rc['B']} / C {bc['C']}{arrow}{rc['C']}"
        )

        if result.is_topic:
            body.append(
                "  ・注記: この PJ はこの現象を扱う **話題 PJ** です。Family C は文脈固有語彙が多く "
                "FP を含みうるので、operational PJ より慎重に解釈してください。"
            )

        # 代表例（作話テキスト vs 直前 tool_result 原文の対比）。
        examples = _pick_examples(rep)
        if examples:
            body.append("  ・代表例:")
            for h in examples:
                body.append(
                    f"      [{h.family}] {h.session_id}:{h.line} 作話: {_clip(h.confab_text)}"
                )
                if h.reference_text:
                    body.append(f"          ↔ 直前 tool_result 原文: {_clip(h.reference_text)}")

        body.append(
            "      → 恒久計測（Layer 2）。live 抑止（Layer 1 hook）は別タスク。"
            "頻発なら長大セッションの /clear・effort 調整を検討。"
        )
        return body

    return build_advisory_section(
        project_dir,
        title="Self-Contamination Fingerprints (当PJ・advisory — スコア重みには未反映)",
        blurb=[
            "opus 長大セッションで assistant が tool_result 原文に無い偽指示を自己生成し、それを"
            "「外部汚染」と誤認してラッチする現象の指紋率を、既存 transcript から決定論・ゼロ LLM で"
            "計測します（read-only・hook/store 新設なし）。",
        ],
        compute=compute,
        applicable=lambda result: result is not None,
        render=render,
    )


def _pick_examples(report) -> list:
    """各 Family から代表例を最大 _MAX_EXAMPLES 件集める（精度の高い C を優先）。"""
    picked = []
    for lane in (report.family_c, report.family_a, report.family_b):
        for h in lane:
            if len(picked) >= _MAX_EXAMPLES:
                return picked
            picked.append(h)
    return picked


def _clip(text: str, width: int = 120) -> str:
    s = " ".join((text or "").split())
    return s if len(s) <= width else s[:width] + "…"
