"""Memory Contagion — 評価源バイアスの記憶伝播を決定論的に検出する（#73）。

背景: 評価源（evaluator source）に偏りがあると、その経験が記憶 / correction に蓄積され
将来の提案・採点に伝染する（Memory Contagion・authority bias 増幅）。本モジュールは
当 PJ の評価源を「人間確認源（human）」と「機械評価源（machine）」に決定論で分解し、
機械評価源の蓄積が人間確認源を大きく上回っていないかを advisory に判定する。

**完全に決定論・LLM 非依存。** human/machine の判定は既存実装を再利用する（再実装しない）:
- corrections: ``correction_semantic.provenance_weight.is_human_correction``
  （HUMAN_SOURCES={"reflect_confirmed","idiom_dict"} のみ human、それ以外と Stop hook 系は機械）。
- idioms: ``correction_semantic.store.read_idioms`` の各 record の ``confirmed``（人間が #446 の
  review で承認）を human、未確認（LLM judge 段階）を machine とみなす。

**PJ slug スコープ必須（全PJ共通 DATA_DIR pitfall）。** corrections.jsonl / correction_idioms.jsonl
はいずれも全 PJ 共通ストアなので、引数なし cwd 導出は worktree で slug 食い違いを起こす。
capture_rate と同じスコープ方式（``capture_rate._normalize_pj`` / ``_project_match`` で当 PJ の
project フィールド突合 / idioms は ``pj_slug_fast(project_dir)`` × ``pj_slug_match``）を踏襲し、
``project_dir`` から slug を導出する。

**閾値は保守側（オーケストレーターが実コーパス dry-run で校正する前提）。**
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── 保守的しきい値（実コーパス dry-run で校正する前提・cry wolf を避ける側に置く）──
# 機械評価源がこの件数未満なら偏り判定しない（小標本ノイズ回避）。
MACHINE_FLOOR = 10
# 機械が人間の何倍で「偏り集中」とみなすか。
RATIO = 3.0


@dataclass
class ContagionReport:
    """評価源バイアスの集計結果（決定論）。

    applicable: 評価データが 1 件でもあるか（両ゼロなら False で非該当）。
    human_total / machine_total: corrections + idioms を合算した評価源件数。
    human_corrections / machine_corrections: corrections だけの内訳（evidence 用）。
    confirmed_idioms / unconfirmed_idioms: idioms だけの内訳（evidence 用）。
    verdict: "healthy" / "no_human_baseline" / "contagion_risk" のいずれか
             （applicable=False のときは判定対象外なので "healthy" を入れておく）。
    """

    applicable: bool
    human_total: int
    machine_total: int
    human_corrections: int
    machine_corrections: int
    confirmed_idioms: int
    unconfirmed_idioms: int
    verdict: str


# ─────────────────────────────────────────────────────────────────
# ローダ（PJ slug スコープ・テストは monkeypatch で差し替える）
# ─────────────────────────────────────────────────────────────────
def _corrections_path() -> Path:
    """corrections.jsonl の正準パス（hook-writer 系・#358）。"""
    from rl_common import hook_store_path

    return hook_store_path("corrections.jsonl")


def _load_corrections(project_dir: Path) -> List[Dict[str, Any]]:
    """当 PJ スコープの corrections レコードを全期間で読む（capture_rate 方式）。

    corrections.jsonl は全 PJ 共通ストア。``capture_rate._normalize_pj`` で project_dir を
    worktree 安全 slug に正規化し、``_project_match`` で当 PJ レコード（+ 未帰属レコード）
    のみ返す。ストア未存在・読込失敗は []（防御的）。
    """
    try:
        import capture_rate
    except ImportError:
        return []
    target = capture_rate._normalize_pj(str(project_dir))
    out: List[Dict[str, Any]] = []
    for rec in _read_jsonl(_corrections_path()):
        if target is not None and not capture_rate._project_match(rec, target):
            continue
        out.append(rec)
    return out


def _load_idioms(project_dir: Path) -> List[Dict[str, Any]]:
    """当 PJ スコープの idiom レコードを読む（pj_slug_fast × pj_slug_match）。

    correction_idioms.jsonl は全 PJ 共通ストア。書込側と同じ ``pj_slug_fast(project_dir)`` で
    当 PJ slug を導出し、``store_read_union.pj_slug_match``（read 層 alias 込み）で突合する。
    slug 未解決・読込失敗は []（防御的）。
    """
    try:
        from correction_semantic.store import read_idioms
    except ImportError:
        return []
    try:
        from pj_slug import pj_slug_fast
        slug = pj_slug_fast(project_dir)
    except Exception:
        slug = None
    if not slug:
        return []
    try:
        from store_read_union import pj_slug_match
    except ImportError:
        pj_slug_match = lambda a, b: a == b  # noqa: E731

    out: List[Dict[str, Any]] = []
    for rec in read_idioms():
        if pj_slug_match(rec.get("pj_slug"), slug):
            out.append(rec)
    return out


# ─────────────────────────────────────────────────────────────────
# 集計 + 保守的 verdict 判定
# ─────────────────────────────────────────────────────────────────
def compute_contagion(project_dir: Path) -> ContagionReport:
    """当 PJ の評価源バイアスを集計し保守的に分類する（決定論・LLM 非依存）。

    verdict 判定（cry wolf しない側に倒す）:
      - 両ゼロ → applicable=False（評価データ無し）。
      - human_total==0 かつ machine_total>=MACHINE_FLOOR → "no_human_baseline"
        （人間確認源ゼロ＝比較基準なし・偏り判定不能。⚠ でなく ℹ 扱い）。
      - human_total>0 かつ machine_total>=MACHINE_FLOOR かつ machine_total>=RATIO*human_total
        → "contagion_risk"（⚠ authority bias の兆候）。
      - それ以外 → "healthy"（小標本・バランス健全）。
    """
    from correction_semantic.provenance_weight import is_human_correction

    corrections = _load_corrections(Path(project_dir))
    idioms = _load_idioms(Path(project_dir))

    human_corrections = sum(1 for r in corrections if is_human_correction(r))
    machine_corrections = len(corrections) - human_corrections

    confirmed_idioms = sum(1 for r in idioms if r.get("confirmed"))
    unconfirmed_idioms = len(idioms) - confirmed_idioms

    human_total = human_corrections + confirmed_idioms
    machine_total = machine_corrections + unconfirmed_idioms

    applicable = (human_total + machine_total) > 0
    verdict = _verdict(human_total, machine_total)

    return ContagionReport(
        applicable=applicable,
        human_total=human_total,
        machine_total=machine_total,
        human_corrections=human_corrections,
        machine_corrections=machine_corrections,
        confirmed_idioms=confirmed_idioms,
        unconfirmed_idioms=unconfirmed_idioms,
        verdict=verdict,
    )


def _verdict(human_total: int, machine_total: int) -> str:
    if machine_total < MACHINE_FLOOR:
        # 機械評価源が小標本 → 偏り判定しない（ノイズ回避）。
        return "healthy"
    if human_total == 0:
        # 人間確認源ゼロ＝比較基準なし。偏り判定不能（ℹ・cry wolf しない）。
        return "no_human_baseline"
    if machine_total >= RATIO * human_total:
        return "contagion_risk"
    return "healthy"


# ─────────────────────────────────────────────────────────────────
# 内部 helper
# ─────────────────────────────────────────────────────────────────
def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """JSONL を読み、壊れた行はスキップする。未存在なら []。"""
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out
