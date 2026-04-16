#!/usr/bin/env python3
"""Week 1 スパイク: rl-scorer の出力評価への転用可否を検証する。

【スパイクの問い】
rl-scorer はスキル定義（SKILL.md）の品質評価用だが、
LLM が生成した考察系スキル（evolve/reflect/optimize/audit）の**出力テキスト**を
同じ3軸（技術/ドメイン/構造）で評価できるか？

【検証方法】
1. evolve スキルの典型的な出力をサンプルとして用意（モック）
2. rl-scorer の各軸プロンプトを出力評価用に微調整
3. haiku で採点 → スコアが意味のある値を返すか確認
4. 結論を findings として出力

【実行】
python3 scripts/bench/spike_rl_scorer_output_eval.py

API コスト: ~3回の haiku call（推定 $0.001 以下）
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

# ──────────────────────────────────────────────
# サンプル: evolve スキルの典型的な出力
# ──────────────────────────────────────────────

# 実際に /evolve を実行したときに Claude が返すような出力のモック。
# 良質な例（correction_count = 0 に対応する golden case の想定出力）。
SAMPLE_EVOLVE_OUTPUT_GOOD = """
## Evolve Report — rl-anything

### Phase 1: Observe
- セッション数: 47件（前回 evolve から）
- 使用スキル: reflect(12), audit(8), evolve(6), optimize(4), その他17
- correction 件数: 3件（confidence high: 2, medium: 1）

### Phase 2: Diagnose
**検出されたパターン:**
1. `reflect` スキルが正常に終了した後、`spec-keeper` を提案し忘れるケースが3件
2. `optimize` 実行前に `research-best-practices` が呼ばれていない（2件）

**根拠:**
- corrections.jsonl の上位パターン: "spec-keeper を忘れていた" (confidence: 0.85)
- usage.jsonl のシーケンス分析: optimize → reflect → spec-keeperが呼ばれず終了

### Phase 3: Compile
**提案する変更:**

#### CLAUDE.md への追記
`spec-keeper-trigger.md` ルールを強化: reflect 完了後のトリガー条件を明示

#### rules/spec-keeper-trigger.md の更新
```
# spec-keeper 実施タイミング（更新）
reflect 完了後も `/rl-anything:spec-keeper` を提案する（実行確認必須）
```

### 実行コマンド
```bash
python3 scripts/rl/evolve.py --apply
```

準拠率: 87% / 変更: 2ファイル / 影響スキル: reflect, optimize
"""

# 低品質な例（correction があった場合に対応する出力のモック）。
SAMPLE_EVOLVE_OUTPUT_POOR = """
evolve を実行しました。

特に問題は見当たりません。

何か変更が必要なら教えてください。
"""

# ──────────────────────────────────────────────
# rl-scorer の軸プロンプト（出力評価用に調整）
# ──────────────────────────────────────────────

TECHNICAL_PROMPT_TEMPLATE = """あなたはLLMが生成したスキル出力の技術品質を評価します。

## 評価対象: evolve スキルの実行出力

{output_text}

## 評価基準（技術品質 — 出力評価用）

| 観点 | 重み | 基準 |
|------|------|------|
| 明確性 | 30% | 出力が曖昧でないか。「適切に」「必要に応じて」等の曖昧語が少ないか |
| 完全性 | 25% | 必要な情報が含まれているか（観察→診断→提案の流れ）|
| 一貫性 | 20% | 内部矛盾がないか。前後の記述が整合しているか |
| エッジケース | 15% | 例外ケースや空振り（問題なし）の場合の扱いが記述されているか |
| 実行可能性 | 10% | 提案が具体的で実際に実行できる形か |

各観点 0.0〜1.0 で採点し、以下の JSON のみを返してください（マークダウン不要）:

{{
  "clarity": <float>,
  "completeness": <float>,
  "consistency": <float>,
  "edge_cases": <float>,
  "testability": <float>,
  "total": <重み付き平均>,
  "rationale": "<50字以内の根拠>"
}}"""

DOMAIN_PROMPT_TEMPLATE = """あなたは rl-anything プラグインの出力品質を評価します。

rl-anything は Claude Code の「自律進化パイプライン」プラグインで、
スキル/ルールの自己改善（evolve）、修正フィードバック収集（reflect）、
最適化（optimize）、環境診断（audit）を行います。

## 評価対象: evolve スキルの実行出力

{output_text}

## 評価基準（ドメイン品質 — rl-anything 固有）

| 観点 | 重み | 基準 |
|------|------|------|
| データ根拠 | 30% | セッション数・correction件数等の定量データに基づいているか |
| 診断精度 | 30% | 検出パターンが実際の問題を指摘しているか（ハルシネーションなしか）|
| 提案実用性 | 20% | 提案が具体的で適用可能か（曖昧な「改善しましょう」でないか）|
| 範囲適切性 | 20% | 変更範囲が適切か（大きすぎず小さすぎず）|

各観点 0.0〜1.0 で採点し、以下の JSON のみを返してください（マークダウン不要）:

{{
  "data_grounding": <float>,
  "diagnostic_accuracy": <float>,
  "proposal_utility": <float>,
  "scope_fit": <float>,
  "total": <重み付き平均>,
  "rationale": "<50字以内の根拠>"
}}"""

STRUCTURE_PROMPT_TEMPLATE = """あなたはLLMが生成したスキル出力の構造品質を評価します。

## 評価対象: evolve スキルの実行出力

{output_text}

## 評価基準（構造品質 — 出力評価用）

| 観点 | 重み | 基準 |
|------|------|------|
| フォーマット | 25% | Markdown が適切に使われているか。見出し・リストが構造化されているか |
| 長さ | 25% | 適切な長さか（短すぎず冗長でもない。100-500行が目安）|
| 具体例 | 25% | 例示・コードブロック・コマンドが含まれているか |
| 完結性 | 25% | 出力が途中で切れていないか。次のアクションが明示されているか |

各観点 0.0〜1.0 で採点し、以下の JSON のみを返してください（マークダウン不要）:

{{
  "format": <float>,
  "length": <float>,
  "examples": <float>,
  "completeness": <float>,
  "total": <重み付き平均>,
  "rationale": "<50字以内の根拠>"
}}"""

# ──────────────────────────────────────────────
# LLM 呼び出し
# ──────────────────────────────────────────────

def _call_haiku(prompt: str, timeout: int = 60) -> Optional[str]:
    """haiku で採点プロンプトを実行し、生の出力を返す。"""
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", "haiku"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            print(f"[spike] haiku error: {result.stderr[:200]}", file=sys.stderr)
            return None
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        print("[spike] haiku timeout", file=sys.stderr)
        return None
    except FileNotFoundError:
        print("[spike] claude CLI not found", file=sys.stderr)
        return None


def _parse_json(raw: str) -> Optional[dict]:
    """JSON をパース。マークダウンコードブロックを除去してから試みる。"""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # JSON 断片を探して抽出
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    return None


# ──────────────────────────────────────────────
# スパイク実行
# ──────────────────────────────────────────────

def evaluate_output(label: str, output_text: str) -> dict:
    """出力テキストを3軸で評価し、統合スコアを返す。"""
    print(f"\n{'='*60}")
    print(f"評価対象: {label}")
    print(f"{'='*60}")

    scores = {}

    for axis_name, template in [
        ("technical", TECHNICAL_PROMPT_TEMPLATE),
        ("domain",    DOMAIN_PROMPT_TEMPLATE),
        ("structure", STRUCTURE_PROMPT_TEMPLATE),
    ]:
        print(f"\n▶ {axis_name} 軸 評価中...", end=" ", flush=True)
        prompt = template.format(output_text=output_text)
        raw = _call_haiku(prompt)
        if raw is None:
            print("FAILED")
            scores[axis_name] = {"total": 0.0, "error": True}
            continue

        parsed = _parse_json(raw)
        if parsed is None:
            print(f"PARSE ERROR: {raw[:100]}")
            scores[axis_name] = {"total": 0.0, "error": True, "raw": raw}
            continue

        total = float(parsed.get("total", 0.0))
        rationale = parsed.get("rationale", "")
        print(f"OK  total={total:.2f}  ({rationale})")
        scores[axis_name] = {**parsed, "error": False}

    # 統合スコア
    tech_total = scores.get("technical", {}).get("total", 0.0)
    domain_total = scores.get("domain", {}).get("total", 0.0)
    struct_total = scores.get("structure", {}).get("total", 0.0)
    integrated = tech_total * 0.4 + domain_total * 0.4 + struct_total * 0.2

    print(f"\n統合スコア: {integrated:.2f}  "
          f"(tech={tech_total:.2f}×40% + domain={domain_total:.2f}×40% + struct={struct_total:.2f}×20%)")

    return {
        "label": label,
        "scores": scores,
        "integrated_score": round(integrated, 3),
    }


def main() -> int:
    print("=" * 60)
    print("rl-scorer 出力評価スパイク")
    print("目的: スキル定義評価の3軸が LLM 出力評価に転用できるか検証")
    print("=" * 60)

    # 良質な出力と低品質な出力を両方評価
    good_result = evaluate_output("良質な evolve 出力（golden 相当）", SAMPLE_EVOLVE_OUTPUT_GOOD)
    poor_result = evaluate_output("低品質な evolve 出力（correction あり相当）", SAMPLE_EVOLVE_OUTPUT_POOR)

    # 判定
    good_score = good_result["integrated_score"]
    poor_score = poor_result["integrated_score"]
    delta = good_score - poor_score

    print("\n" + "=" * 60)
    print("スパイク結論")
    print("=" * 60)
    print(f"良質出力スコア: {good_score:.3f}")
    print(f"低品質出力スコア: {poor_score:.3f}")
    print(f"Delta: {delta:+.3f}")

    # 転用可否判定
    # 条件: delta > 0.15（差異を検出できている） AND good_score > 0.5（絶対値が意味を持つ）
    if delta > 0.15 and good_score > 0.5:
        feasible = True
        conclusion = "✓ 転用可能。rl-scorer の3軸は LLM 出力評価に機能する。"
        recommendation = "Week 2: Approach A 完了 → rl-scorer を golden_cases.jsonl の評価に接続"
    elif delta > 0.05:
        feasible = True  # 部分的
        conclusion = "△ 部分的に転用可能。domain 軸の調整が必要。"
        recommendation = "Week 2: domain 軸ルーブリックを出力評価用に調整してから接続"
    else:
        feasible = False
        conclusion = "✗ 転用困難。代替 LLM judge が必要。"
        recommendation = "Week 2: scripts/bench/judge_prompt.txt に別ルーブリックを記述"

    print(f"\n結論: {conclusion}")
    print(f"推奨: {recommendation}")
    print(f"\n転用可否: {'YES' if feasible else 'NO'}")

    # findings を JSON で保存
    findings_path = Path(__file__).resolve().parent / "spike_rl_scorer_findings.json"
    findings = {
        "spike": "rl-scorer output eval feasibility",
        "date": "2026-04-16",
        "good_score": good_score,
        "poor_score": poor_score,
        "delta": round(delta, 3),
        "feasible": feasible,
        "conclusion": conclusion,
        "recommendation": recommendation,
        "good_result": good_result,
        "poor_result": poor_result,
    }
    findings_path.write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nFindings 保存: {findings_path}")

    return 0 if feasible else 1


if __name__ == "__main__":
    sys.exit(main())
