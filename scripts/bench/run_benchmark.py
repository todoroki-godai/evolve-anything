"""TBench2-rl: Harness Quality Benchmark エントリーポイント。

golden_cases.jsonl を読み込み、各 GoldenCase に対して:
1. haiku でスキル出力を生成（考察系スキルのみ）
2. OutputEvaluator で3軸採点
3. benchmark_results.jsonl に保存

実行例:
  python3 scripts/bench/run_benchmark.py
  python3 scripts/bench/run_benchmark.py --skills evolve reflect --max-api-calls 40
  python3 scripts/bench/run_benchmark.py --dry-run

NOTE: benchmark テストは pytest -m bench で実行。PR CI には含めない。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_BENCH_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _BENCH_DIR.parent
_PLUGIN_ROOT = _SCRIPTS_DIR.parent

sys.path.insert(0, str(_BENCH_DIR))
from golden_extractor import DATA_DIR_DEFAULT, GoldenCase, GoldenExtractor
from output_evaluator import OutputEvaluator

# ─────────────────────────────────────────────────
# 定数
# ─────────────────────────────────────────────────

# オフライン評価対象の考察系スキル（ツール呼び出しなし）
CONSIDERATION_SKILLS: frozenset[str] = frozenset({
    "evolve",
    "reflect",
    "optimize",
    "audit",
    "discover",
    "prune",
    "reorganize",
    "genetic-prompt-optimizer",
    "rl-loop-orchestrator",
})

# haiku 1 call で生成する出力の最大文字数
_MAX_OUTPUT_CHARS = 1500
# 生成プロンプトに渡す system_context の最大文字数
_MAX_SYSTEM_CTX_CHARS = 2000
# 生成プロンプトに渡す SKILL.md の最大文字数
_MAX_SKILL_PROMPT_CHARS = 1500

MODEL = "haiku"


# ─────────────────────────────────────────────────
# BenchmarkResult dataclass
# ─────────────────────────────────────────────────

@dataclass
class BenchmarkResult:
    """benchmark_results.jsonl の1エントリ。

    score は 0〜10 スケール。score_pre / delta は前回実行との比較。
    """

    skill_name: str
    session_id: str
    score: float            # 0〜10
    score_pre: Optional[float]  # 前回スコア。初回は None
    delta: Optional[float]      # score - score_pre。初回は None
    harness_hash: str       # sha256:<hex> — system_context のハッシュ
    mutation_id: str        # "null" = 通常実行、それ以外は mutation テスト
    timestamp: str          # ISO 8601 UTC
    model: str              # 採点・生成に使ったモデル


# ─────────────────────────────────────────────────
# ユーティリティ
# ─────────────────────────────────────────────────

def _compute_harness_hash(system_context: str) -> str:
    """system_context の SHA-256 ハッシュを返す。"""
    digest = hashlib.sha256(system_context.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _load_previous_score(
    results_file: Path,
    skill_name: str,
    session_id: str,
) -> Optional[float]:
    """benchmark_results.jsonl から (skill_name, session_id) の直近スコアを返す。"""
    if not results_file.exists():
        return None
    last: Optional[float] = None
    for line in results_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("skill_name") == skill_name and rec.get("session_id") == session_id:
            try:
                last = float(rec["score"])
            except (KeyError, TypeError, ValueError):
                pass
    return last


def _load_skill_prompt(skill_name: str, plugin_root: Path = _PLUGIN_ROOT) -> str:
    """スキルの SKILL.md を読み込み、最大文字数に切り詰めて返す。"""
    skill_md = plugin_root / "skills" / skill_name / "SKILL.md"
    if not skill_md.exists():
        return f"# {skill_name} スキル\n（SKILL.md が見つかりません）"
    text = skill_md.read_text(encoding="utf-8")
    if len(text) > _MAX_SKILL_PROMPT_CHARS:
        text = text[:_MAX_SKILL_PROMPT_CHARS] + "\n... (truncated)"
    return text


def _build_generation_prompt(
    skill_name: str,
    user_prompt: str,
    system_context: str,
    skill_prompt: str,
) -> str:
    """考察系スキルの出力を生成する haiku 向けプロンプトを構築する。"""
    ctx = system_context[:_MAX_SYSTEM_CTX_CHARS]
    skill_def = skill_prompt  # already truncated
    user_req = user_prompt.strip() or "実行してください"
    return (
        f"あなたは Claude Code の {skill_name} スキルとして動作します。\n"
        f"以下の環境設定と定義に基づいて、実際のスキル出力を生成してください。\n\n"
        f"## 環境設定 (CLAUDE.md + rules — 抜粋)\n\n{ctx}\n\n"
        f"## スキル定義 (SKILL.md — 抜粋)\n\n{skill_def}\n\n"
        f"## ユーザーリクエスト\n\n{user_req}\n\n"
        f"上記に基づいてスキルの出力を生成してください。"
        f"（実際のセッションと同様に Markdown 形式で。最大 {_MAX_OUTPUT_CHARS} 文字。）"
    )


def _call_haiku(prompt: str, timeout: int = 90) -> Optional[str]:
    """haiku でプロンプトを実行し stdout を返す。失敗時は None。"""
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", MODEL],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


# ─────────────────────────────────────────────────
# BenchmarkRunner
# ─────────────────────────────────────────────────

class BenchmarkRunner:
    """GoldenCase リストに対してベンチマークを実行する。

    Args:
        output_file:    benchmark_results.jsonl の書き出し先
        system_context: CLAUDE.md + rules テキスト（harness_hash 計算・プロンプト注入用）
        max_api_calls:  API 呼び出し上限（コスト制御）
        mutation_id:    mutation テスト時の識別子（通常は "null"）
        dry_run:        True なら API 呼び出しをせず実行計画のみ表示
        plugin_root:    SKILL.md の検索ルート
    """

    def __init__(
        self,
        output_file: Path,
        system_context: str = "",
        max_api_calls: int = 100,
        mutation_id: str = "null",
        dry_run: bool = False,
        plugin_root: Path = _PLUGIN_ROOT,
    ) -> None:
        self.output_file = output_file
        self.system_context = system_context
        self.max_api_calls = max_api_calls
        self.mutation_id = mutation_id
        self.dry_run = dry_run
        self.plugin_root = plugin_root
        self._harness_hash = _compute_harness_hash(system_context)
        self._evaluator = OutputEvaluator(system_context=system_context, model=MODEL)
        self._api_calls_used = 0

    def run(self, cases: list[GoldenCase]) -> list[BenchmarkResult]:
        """全ケースを評価し BenchmarkResult のリストを返す。"""
        # 考察系スキルのみフィルタ
        eligible = [c for c in cases if c.skill_name in CONSIDERATION_SKILLS]

        if self.dry_run:
            self._print_dry_run_plan(eligible, cases)
            return []

        results: list[BenchmarkResult] = []
        for case in eligible:
            if self._api_calls_used >= self.max_api_calls:
                print(
                    f"[benchmark] max_api_calls={self.max_api_calls} に達しました。"
                    f" {len(eligible) - len(results)} ケースをスキップ。",
                    file=sys.stderr,
                )
                break

            result = self._evaluate_case(case)
            if result is not None:
                results.append(result)
                self._append_result(result)

        return results

    def _evaluate_case(self, case: GoldenCase) -> Optional[BenchmarkResult]:
        """1ケースを評価して BenchmarkResult を返す。失敗時は None。"""
        # Step 1: スキル出力を生成
        skill_prompt = _load_skill_prompt(case.skill_name, self.plugin_root)
        gen_prompt = _build_generation_prompt(
            case.skill_name, case.user_prompt, self.system_context, skill_prompt
        )
        self._api_calls_used += 1
        output_text = _call_haiku(gen_prompt)
        if not output_text:
            print(
                f"[benchmark] 出力生成失敗: {case.skill_name} / {case.session_id}",
                file=sys.stderr,
            )
            return None

        # Step 2: 3軸採点（3 API calls）
        self._api_calls_used += 3
        scores = self._evaluator.evaluate(case.skill_name, output_text)

        # Step 3: score_pre / delta 計算
        score = scores.to_score_10()
        score_pre = _load_previous_score(self.output_file, case.skill_name, case.session_id)
        delta = round(score - score_pre, 3) if score_pre is not None else None

        return BenchmarkResult(
            skill_name=case.skill_name,
            session_id=case.session_id,
            score=score,
            score_pre=score_pre,
            delta=delta,
            harness_hash=self._harness_hash,
            mutation_id=self.mutation_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            model=MODEL,
        )

    def _append_result(self, result: BenchmarkResult) -> None:
        """benchmark_results.jsonl に1行追記する。"""
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.output_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")

    def _print_dry_run_plan(
        self, eligible: list[GoldenCase], all_cases: list[GoldenCase]
    ) -> None:
        skipped = len(all_cases) - len(eligible)
        api_calls_needed = len(eligible) * 4  # 1 gen + 3 eval
        print(f"[DRY RUN] benchmark 実行計画")
        print(f"  総ケース: {len(all_cases)} / 評価対象: {len(eligible)} / スキップ: {skipped}")
        print(f"  推定 API 呼び出し: {api_calls_needed} (上限: {self.max_api_calls})")
        print(f"  出力先: {self.output_file}")
        for i, c in enumerate(eligible, 1):
            marker = "  ▶" if self._api_calls_used + i * 4 <= self.max_api_calls else "  ✗"
            print(f"  {marker} [{i}] {c.skill_name} / {c.session_id}")


# ─────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="golden_cases.jsonl を評価して benchmark_results.jsonl に書き出す。"
    )
    p.add_argument(
        "--input",
        type=Path,
        default=DATA_DIR_DEFAULT / "golden_cases.jsonl",
        help="入力 golden_cases.jsonl のパス",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=DATA_DIR_DEFAULT / "benchmark_results.jsonl",
        help="出力 benchmark_results.jsonl のパス",
    )
    p.add_argument(
        "--skills",
        nargs="*",
        metavar="SKILL",
        help="評価するスキル名（省略時は全考察系スキル）",
    )
    p.add_argument(
        "--max-api-calls",
        type=int,
        default=100,
        dest="max_api_calls",
        help="API 呼び出し上限（デフォルト: 100）",
    )
    p.add_argument(
        "--mutation-id",
        default="null",
        dest="mutation_id",
        help="mutation テスト識別子（通常は null）",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="API 呼び出しなしで実行計画を表示する",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    # golden_cases.jsonl を読み込む
    if not args.input.exists():
        print(f"[benchmark] 入力ファイルが見つかりません: {args.input}", file=sys.stderr)
        print(
            "[benchmark] 先に golden_extractor.py を実行してください:",
            file=sys.stderr,
        )
        print(
            f"  python3 scripts/bench/golden_extractor.py --output {args.input}",
            file=sys.stderr,
        )
        return 1

    cases: list[GoldenCase] = []
    for line in args.input.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            cases.append(GoldenCase(**d))
        except (json.JSONDecodeError, TypeError):
            continue

    # スキルフィルタ
    if args.skills:
        cases = [c for c in cases if c.skill_name in args.skills]

    # system_context を読み込む
    extractor = GoldenExtractor(system_context=None)  # 現在の CLAUDE.md + rules を自動ロード
    system_context = extractor.system_context

    runner = BenchmarkRunner(
        output_file=args.output,
        system_context=system_context,
        max_api_calls=args.max_api_calls,
        mutation_id=args.mutation_id,
        dry_run=args.dry_run,
    )

    results = runner.run(cases)

    if not args.dry_run:
        positives = sum(1 for r in results if r.score_pre is None or r.score >= r.score_pre)
        print(
            f"[benchmark] 完了: {len(results)} 件 → {args.output}"
            f"  (API 呼び出し: {runner._api_calls_used})"
        )
        if results:
            avg = sum(r.score for r in results) / len(results)
            print(f"[benchmark] 平均スコア: {avg:.2f}/10.0")
            deltas = [r.delta for r in results if r.delta is not None]
            if deltas:
                avg_delta = sum(deltas) / len(deltas)
                print(f"[benchmark] 平均 delta: {avg_delta:+.3f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
