"""evolve_pj_harness — 非 dry-run の outcome 検証用テスト PJ ビルダー（#400 follow-up）。

背景（learning_dryrun_verification_blind_spot）: evolve の効果（母集団記録・reconcile・
observability）は **apply 後にしか発生しない**。dry-run 検証は apply 境界を越えないため
「dry-run 出力は妥当だが実運用で効かない」バグ（#400 バグ#1 = optimize_history 永久に空）を
構造的に見逃した。本モジュールは「**dry-run 出力でなく正準 store の差分（outcome）を assert する**」
非 dry-run E2E のための再利用可能な PJ ハーネスを提供する。

各テストがバラバラに `tmp_path/.claude/skills/...` を組んでいた重複を1箇所に集約し、
隔離 DATA_DIR（正準 store を temp に向ける）と「assistant の適用」を模す `apply_skill_change` を
標準化する。これにより今後の evolve 変更は emit→**apply**→ingest→reader の実サイクルで
outcome を担保できる（決定論・LLM 非依存の範囲）。

LLM 境界: skill_evolve assessment の LLM 判定（judgment refresh）や apply customization は
LLM を使うため本ハーネスの対象外（それらは RL_ALLOW_LLM_IN_TESTS gated の integration で扱う）。
emit/ingest/reconcile/observability/fitness load の outcome は全て決定論で検証できる。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))


@dataclass
class EvolveTestPJ:
    """隔離されたテスト PJ。正準 store は data_dir 配下に向けてある。"""

    root: Path
    data_dir: Path
    slug: str
    skills: Dict[str, Path] = field(default_factory=dict)

    def skill_md(self, name: str) -> Path:
        return self.skills[name]

    def history_path(self) -> Path:
        """この PJ の optimize_history 正準パス（reader/writer 共有先）。"""
        import optimize_history_store as ohs

        return ohs.history_path(self.slug)


_DEFAULT_SKILL_TEMPLATE = """---
name: {name}
description: {desc}
---

# {name}

トリガー: {name} を使うとき。

手順を踏んで {name} を実行する。
"""


def build_evolve_test_pj(
    tmp_path: Path,
    monkeypatch,
    *,
    slug: str = "evolve-test-pj",
    skills: Optional[Dict[str, str]] = None,
) -> EvolveTestPJ:
    """隔離された evolve テスト PJ を組む。

    - `root/.claude/skills/<name>/SKILL.md` を生成
    - `root/CLAUDE.md` を生成
    - 正準 store（optimize_history / evolve_decisions queue）を `data_dir` 配下へ向ける
      （module-level root を monkeypatch。env だけだと import 済みモジュールに効かないため）

    Args:
        skills: {skill_name: description} の dict。未指定なら used/archive/normal の3スキル。

    Returns:
        EvolveTestPJ（root / data_dir / slug / skills パス）
    """
    if skills is None:
        skills = {
            "skill-used": "Use this skill to build foo from bar. Trigger when user says foo.",
            "skill-archive": "Use this skill for legacy baz handling. Trigger on baz.",
            "skill-normal": "Use this skill to format qux output. Trigger on qux.",
        }

    root = tmp_path / "pj"
    skills_dir = root / ".claude" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (root / "CLAUDE.md").write_text(
        "# Test PJ\n\nevolve 非 dry-run outcome 検証用の隔離 PJ。\n", encoding="utf-8"
    )

    skill_paths: Dict[str, Path] = {}
    for name, desc in skills.items():
        d = skills_dir / name
        d.mkdir(parents=True, exist_ok=True)
        p = d / "SKILL.md"
        p.write_text(_DEFAULT_SKILL_TEMPLATE.format(name=name, desc=desc), encoding="utf-8")
        skill_paths[name] = p

    # 正準 store を隔離 data_dir 配下へ向ける。
    data_dir = tmp_path / "data"
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data_dir))
    import optimize_history_store as ohs
    import evolve_decisions as ed

    monkeypatch.setattr(ohs, "HISTORY_ROOT", data_dir / "optimize_history")
    monkeypatch.setattr(ed, "QUEUE_ROOT", data_dir / "evolve_decisions")

    return EvolveTestPJ(root=root, data_dir=data_dir, slug=slug, skills=skill_paths)


def apply_skill_change(pj: EvolveTestPJ, skill_name: str, new_content: str) -> None:
    """assistant が提案を適用したことを模す（スキル SKILL.md を書き換える）。

    accept は「ディスク差分」から決定論で取られるため、これが evolve の実運用での
    『apply 境界』に相当する。検証はこの後の ingest → reader で outcome を見る。
    """
    pj.skills[skill_name].write_text(new_content, encoding="utf-8")


def make_skill_evolve_result(
    pj: EvolveTestPJ,
    *,
    high: Optional[List[str]] = None,
    medium: Optional[List[str]] = None,
    archive: Optional[List[str]] = None,
    discover_matched: Optional[List[str]] = None,
    batch_skip_count: int = 0,
) -> Dict[str, Any]:
    """evolve.py が組むのと同形の result dict を生成する（post-phase 関数の入力）。

    emit_decisions / reconcile_skill_evolve_archive / build_remediation_batch_skip_observability /
    fitness が消費する phases（skill_evolve / discover / prune / remediation）を埋める。
    """
    high = high or []
    medium = medium or []
    archive = archive or []
    discover_matched = discover_matched or []

    assessments: List[Dict[str, Any]] = []
    for name in high:
        assessments.append({
            "skill_name": name, "skill_dir": str(pj.skills[name].parent), "suitability": "high",
        })
    for name in medium:
        assessments.append({
            "skill_name": name, "skill_dir": str(pj.skills[name].parent), "suitability": "medium",
        })

    classified_individual = [
        {"type": "skill_evolve_candidate", "detail": {"skill_name": n}} for n in (high + medium)
    ]
    # 実 evolve の契約（evolve_result_schema.py:71, evolve.py:705/714）では top-level の
    # proposable_custom_batch_skip(int) == len(classified.proposable_custom_batch_skip[])。
    # fixture でも両者を一致させる（int=N, list=N件）。これを崩すと reconcile の
    # remediation[bucket]=len(kept) と合成したとき count が壊れ false green を生む
    # （learning_synthetic_fixture_false_confidence）。
    batch_skip_items = [
        {"type": "low_confidence", "detail": {"idx": i}} for i in range(batch_skip_count)
    ]

    return {
        "phases": {
            "skill_evolve": {
                "assessments": assessments,
                "high_suitability": len(high),
                "medium_suitability": len(medium),
            },
            "discover": {
                "matched_skills": [
                    {"matched_skill": n, "skill_path": str(pj.skills[n]), "pattern": "p"}
                    for n in discover_matched
                ],
            },
            "prune": {
                "zero_invocations": list(archive),
                "retirement_candidates": [],
                "decay_candidates": [],
            },
            "remediation": {
                "classified": {
                    "proposable_custom": list(classified_individual),
                    "proposable_custom_individual": list(classified_individual),
                    "proposable_custom_batch_skip": list(batch_skip_items),
                    "proposable": [],
                },
                "proposable_custom_batch_skip": len(batch_skip_items),
            },
        },
    }
