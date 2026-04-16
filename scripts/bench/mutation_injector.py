"""TBench2-rl Week 3: Mutation Injector。

harness（CLAUDE.md + rules）に意図的な劣化を注入し、
benchmark がそれを検出できるか検証する sentinel system。

3パターンの mutation:
  rule_delete     — rules セクションを1つ削除
  trigger_invert  — rules の指示行を否定形に反転
  prompt_truncate — system_context を前半50%に短縮

重要: ライブファイルは書き換えない。system_context 文字列をインメモリで
変換して BenchmarkRunner に渡す（オフライン評価のみ）。
"""
from __future__ import annotations

import json
import random
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

_BENCH_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_BENCH_DIR))
from golden_extractor import GoldenCase
from run_benchmark import BenchmarkRunner, _load_previous_score

# ─────────────────────────────────────────────────
# 定数
# ─────────────────────────────────────────────────

ALL_MUTATION_IDS: frozenset[str] = frozenset({
    "rule_delete",
    "trigger_invert",
    "prompt_truncate",
})

# 反転の対象とする行のパターン（action 系の指示行）
_ACTION_PATTERNS = re.compile(
    r"^- .+(?:する|実行|確認|禁止|使用|呼ぶ|書く|作る|読む|含める|行う|避ける)",
    re.MULTILINE,
)

# sentinel 検出閾値: baseline - mutated > THRESHOLD → detected=True
_DETECTION_THRESHOLD = 0.5  # 0〜10スケールで 0.5点


# ─────────────────────────────────────────────────
# MutationResult
# ─────────────────────────────────────────────────

@dataclass
class MutationResult:
    """1つの mutation 適用結果。"""

    mutation_id: str       # "{type}|{detail}"
    original_length: int   # 元の system_context の文字数
    mutated_length: int    # mutation 後の文字数
    mutated_context: str   # mutation 後の system_context
    description: str       # 人間可読な説明


# ─────────────────────────────────────────────────
# SentinelReport
# ─────────────────────────────────────────────────

@dataclass
class SentinelReport:
    """sentinel テストの1 mutation に対する結果。"""

    mutation_id: str
    baseline_score: float   # mutation なし baseline（0〜10）
    mutated_score: float    # mutation あり（0〜10）
    delta: float            # mutated - baseline（負が理想）
    detected: bool          # delta < -DETECTION_THRESHOLD → True
    description: str


# ─────────────────────────────────────────────────
# MutationInjector
# ─────────────────────────────────────────────────

class MutationInjector:
    """system_context に mutation を適用する。

    Args:
        system_context: GoldenExtractor._load_system_context() の出力
        seed:           乱数シード（再現性確保）
    """

    def __init__(self, system_context: str, seed: int = 42) -> None:
        self.system_context = system_context
        self._rng = random.Random(seed)

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────

    def rule_delete(self) -> MutationResult:
        """rules セクションを1つ削除する。

        system_context を "# rules/" ヘッダで分割し、
        ランダムに1セクションを除いて再構築する。
        """
        sections = self._parse_sections()
        rule_sections = [(i, s) for i, s in enumerate(sections) if s.startswith("# rules/")]

        if not rule_sections:
            return MutationResult(
                mutation_id="rule_delete|none",
                original_length=len(self.system_context),
                mutated_length=len(self.system_context),
                mutated_context=self.system_context,
                description="rules セクションが見つからないため変更なし",
            )

        idx, chosen = self._rng.choice(rule_sections)
        # ファイル名を mutation_id に含める
        match = re.match(r"# rules/(\S+)", chosen)
        fname = match.group(1) if match else "unknown"

        remaining = [s for i, s in enumerate(sections) if i != idx]
        mutated = "\n\n".join(remaining)

        return MutationResult(
            mutation_id=f"rule_delete|{fname}",
            original_length=len(self.system_context),
            mutated_length=len(mutated),
            mutated_context=mutated,
            description=f"削除: rules/{fname}",
        )

    def trigger_invert(self) -> MutationResult:
        """rules 内の指示行を1行否定形に反転する。

        action 系の指示（「〜する」で終わる bullet）を検出し、
        先頭に "[NEGATED] " を付与して意味を反転する。
        """
        matches = list(_ACTION_PATTERNS.finditer(self.system_context))
        if not matches:
            return MutationResult(
                mutation_id="trigger_invert|none",
                original_length=len(self.system_context),
                mutated_length=len(self.system_context),
                mutated_context=self.system_context,
                description="反転対象の行が見つからないため変更なし",
            )

        target = self._rng.choice(matches)
        original_line = target.group(0)
        # "- " の直後に "[NEGATED] " を挿入
        negated_line = original_line.replace("- ", "- [NEGATED] ", 1)
        mutated = self.system_context[:target.start()] + negated_line + self.system_context[target.end():]

        # どのセクションの何行目かを特定
        section_name = self._find_section_name(target.start())
        line_preview = original_line[:40].strip()

        return MutationResult(
            mutation_id=f"trigger_invert|{section_name}:{line_preview}",
            original_length=len(self.system_context),
            mutated_length=len(mutated),
            mutated_context=mutated,
            description=f"反転: {section_name} の「{line_preview}」",
        )

    def prompt_truncate(self, fraction: float = 0.5) -> MutationResult:
        """system_context を先頭 fraction 分に短縮する。

        行境界で切るため実際の fraction は近似値。
        """
        if not self.system_context:
            return MutationResult(
                mutation_id=f"prompt_truncate|{int(fraction*100)}pct",
                original_length=0,
                mutated_length=0,
                mutated_context="",
                description="空の context",
            )

        target_len = int(len(self.system_context) * fraction)
        # 行境界で切る（target_len 以下で最も近い改行位置）
        cut_pos = self.system_context.rfind("\n", 0, target_len + 1)
        if cut_pos == -1:
            cut_pos = target_len
        mutated = self.system_context[:cut_pos]

        pct_label = f"{int(fraction*100)}pct"
        return MutationResult(
            mutation_id=f"prompt_truncate|{pct_label}",
            original_length=len(self.system_context),
            mutated_length=len(mutated),
            mutated_context=mutated,
            description=f"先頭 {int(fraction*100)}% に短縮",
        )

    def apply_all(self, truncate_fraction: float = 0.5) -> list[MutationResult]:
        """3パターン全ての mutation を適用し結果リストを返す。"""
        return [
            self.rule_delete(),
            self.trigger_invert(),
            self.prompt_truncate(fraction=truncate_fraction),
        ]

    # ──────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────

    def _parse_sections(self) -> list[str]:
        """system_context を "# " で始まるセクションに分割する。"""
        # "# CLAUDE.md" や "# rules/xxx" でセクションを区切る
        raw_parts = re.split(r"\n\n(?=# )", self.system_context)
        return [p.strip() for p in raw_parts if p.strip()]

    def _find_section_name(self, char_pos: int) -> str:
        """char_pos より前の最後のセクションヘッダ名を返す。"""
        text_before = self.system_context[:char_pos]
        # 最後の "# rules/..." または "# CLAUDE.md" を探す
        matches = list(re.finditer(r"^# (\S+)", text_before, re.MULTILINE))
        if matches:
            return matches[-1].group(1)
        return "unknown"


# ─────────────────────────────────────────────────
# SentinelRunner
# ─────────────────────────────────────────────────

class SentinelRunner:
    """全 mutation を実行し、benchmark が検出できるか検証する。

    流れ:
    1. baseline スコアを取得（results_file から、なければ API で取得）
    2. 各 mutation を適用した system_context で benchmark を実行
    3. baseline との delta を計算し detected を判定
    4. SentinelReport リストを返す

    Args:
        cases:        評価対象の GoldenCase リスト
        system_context: 現在の harness（変換元）
        results_file: benchmark_results.jsonl のパス（baseline 読み書き）
        max_api_calls: API 呼び出し上限
        dry_run:      True なら API 呼び出しなしで計画表示のみ
        seed:         MutationInjector の乱数シード
        detection_threshold: baseline - mutated > 閾値 → detected=True
    """

    def __init__(
        self,
        cases: list[GoldenCase],
        system_context: str,
        results_file: Path,
        max_api_calls: int = 100,
        dry_run: bool = False,
        seed: int = 42,
        detection_threshold: float = _DETECTION_THRESHOLD,
    ) -> None:
        self.cases = cases
        self.system_context = system_context
        self.results_file = results_file
        self.max_api_calls = max_api_calls
        self.dry_run = dry_run
        self.detection_threshold = detection_threshold
        self._injector = MutationInjector(system_context, seed=seed)

    def run(self) -> list[SentinelReport]:
        """全3 mutation の sentinel テストを実行し SentinelReport リストを返す。"""
        mutations = self._injector.apply_all()

        if self.dry_run:
            return self._dry_run_plan(mutations)

        # baseline スコアを取得
        baseline_scores = self._get_baseline_scores()

        reports: list[SentinelReport] = []
        for mutation in mutations:
            report = self._run_one_mutation(mutation, baseline_scores)
            reports.append(report)

        return reports

    def _get_baseline_scores(self) -> dict[str, float]:
        """cases の baseline スコアを results_file から読む。なければ API で取得。"""
        scores: dict[str, float] = {}
        needs_api: list[GoldenCase] = []

        for case in self.cases:
            key = f"{case.skill_name}:{case.session_id}"
            prev = _load_previous_score(self.results_file, case.skill_name, case.session_id)
            if prev is not None:
                scores[key] = prev
            else:
                needs_api.append(case)

        if needs_api:
            # baseline を API で取得
            runner = BenchmarkRunner(
                output_file=self.results_file,
                system_context=self.system_context,
                max_api_calls=self.max_api_calls,
                mutation_id="null",
            )
            results = runner.run(needs_api)
            for r in results:
                key = f"{r.skill_name}:{r.session_id}"
                scores[key] = r.score

        return scores

    def _run_one_mutation(
        self, mutation: MutationResult, baseline_scores: dict[str, float]
    ) -> SentinelReport:
        """1つの mutation を適用してベンチマークを実行し SentinelReport を返す。"""
        runner = BenchmarkRunner(
            output_file=self.results_file,
            system_context=mutation.mutated_context,
            max_api_calls=self.max_api_calls,
            mutation_id=mutation.mutation_id,
        )
        results = runner.run(self.cases)

        # cases ごとの mutated スコアを平均
        if not results:
            avg_baseline = (
                sum(baseline_scores.values()) / len(baseline_scores)
                if baseline_scores else 0.0
            )
            return SentinelReport(
                mutation_id=mutation.mutation_id,
                baseline_score=avg_baseline,
                mutated_score=0.0,
                delta=-avg_baseline,
                detected=True,  # 実行失敗 = 検出成功とみなす
                description=f"{mutation.description} — 実行失敗",
            )

        mutated_scores = []
        baselines = []
        for r in results:
            key = f"{r.skill_name}:{r.session_id}"
            baseline = baseline_scores.get(key)
            if baseline is not None:
                baselines.append(baseline)
                mutated_scores.append(r.score)

        if not baselines:
            # baseline がない場合は delta 計算不可
            avg_mutated = sum(r.score for r in results) / len(results)
            return SentinelReport(
                mutation_id=mutation.mutation_id,
                baseline_score=0.0,
                mutated_score=round(avg_mutated, 3),
                delta=0.0,
                detected=False,
                description=f"{mutation.description} — baseline なし",
            )

        avg_baseline = sum(baselines) / len(baselines)
        avg_mutated = sum(mutated_scores) / len(mutated_scores)
        delta = round(avg_mutated - avg_baseline, 3)
        detected = delta < -self.detection_threshold

        return SentinelReport(
            mutation_id=mutation.mutation_id,
            baseline_score=round(avg_baseline, 3),
            mutated_score=round(avg_mutated, 3),
            delta=delta,
            detected=detected,
            description=mutation.description,
        )

    def _dry_run_plan(self, mutations: list[MutationResult]) -> list[SentinelReport]:
        print("[DRY RUN] sentinel テスト実行計画")
        print(f"  cases: {len(self.cases)} / mutations: {len(mutations)}")
        print(f"  detection_threshold: {self.detection_threshold}")
        for m in mutations:
            print(f"  ▶ {m.mutation_id}: {m.description}")
        return []


# ─────────────────────────────────────────────────
# CLI (sentinel サブコマンド)
# ─────────────────────────────────────────────────

def sentinel_main(argv: Optional[list[str]] = None) -> int:
    """sentinel サブコマンドのエントリーポイント。"""
    import argparse
    from golden_extractor import DATA_DIR_DEFAULT, GoldenCase, GoldenExtractor

    p = argparse.ArgumentParser(description="sentinel: mutation を注入してベンチマーク検出力を検証する")
    p.add_argument("--input", type=Path, default=DATA_DIR_DEFAULT / "golden_cases.jsonl")
    p.add_argument("--results", type=Path, default=DATA_DIR_DEFAULT / "benchmark_results.jsonl")
    p.add_argument("--skills", nargs="*", metavar="SKILL")
    p.add_argument("--max-api-calls", type=int, default=100, dest="max_api_calls")
    p.add_argument("--dry-run", action="store_true", dest="dry_run")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--threshold", type=float, default=_DETECTION_THRESHOLD)
    args = p.parse_args(argv)

    if not args.input.exists():
        print(f"[sentinel] 入力ファイルが見つかりません: {args.input}", file=sys.stderr)
        return 1

    cases: list[GoldenCase] = []
    for line in args.input.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            cases.append(GoldenCase(**json.loads(line)))
        except (json.JSONDecodeError, TypeError):
            continue

    if args.skills:
        cases = [c for c in cases if c.skill_name in args.skills]

    extractor = GoldenExtractor(system_context=None)
    system_context = extractor.system_context

    runner = SentinelRunner(
        cases=cases,
        system_context=system_context,
        results_file=args.results,
        max_api_calls=args.max_api_calls,
        dry_run=args.dry_run,
        seed=args.seed,
        detection_threshold=args.threshold,
    )

    reports = runner.run()

    if not args.dry_run:
        detected = sum(1 for r in reports if r.detected)
        print(f"\n[sentinel] 結果: {detected}/{len(reports)} mutation を検出")
        for r in reports:
            mark = "✓" if r.detected else "✗"
            print(
                f"  {mark} {r.mutation_id}: baseline={r.baseline_score:.1f} "
                f"→ mutated={r.mutated_score:.1f} (delta={r.delta:+.2f})"
            )
        if reports:
            detection_rate = detected / len(reports)
            print(f"\n  検出率: {detection_rate:.0%}")
            return 0 if detection_rate == 1.0 else 1

    return 0


if __name__ == "__main__":
    sys.exit(sentinel_main())
