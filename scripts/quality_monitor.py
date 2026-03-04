#!/usr/bin/env python3
"""品質モニタリングスクリプト。

高頻度 global/plugin スキルの品質スコアを定期的に計測し、
劣化を検知して /optimize 推奨を通知する。
"""
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_plugin_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_plugin_root / "skills" / "audit" / "scripts"))

from audit import (
    DATA_DIR,
    aggregate_usage,
    classify_artifact_origin,
    load_usage_data,
)

# ── 定数 ──────────────────────────────────────────────
RESCORE_USAGE_THRESHOLD = 50    # 再スコアリングの使用回数閾値
RESCORE_DAYS_THRESHOLD = 7     # 再スコアリングの経過日数閾値
DEGRADATION_THRESHOLD = 0.10   # 劣化判定の低下率閾値
HIGH_FREQ_THRESHOLD = 10       # 高頻度判定の使用回数閾値
HIGH_FREQ_DAYS = 30            # 高頻度判定の対象期間（日）
MAX_RECORDS_PER_SKILL = 100    # スキルあたりの最大レコード数

BASELINES_FILE = DATA_DIR / "quality-baselines.jsonl"

# CoT 品質評価プロンプト（optimize.py の _llm_evaluate() と同一）
_COT_PROMPT_TEMPLATE = (
    "以下のClaude Codeスキル定義を評価してください。\n\n"
    "各基準について、まず根拠（reason）を述べてから 0.0〜1.0 のスコアを付けてください。\n\n"
    "評価基準:\n"
    "- clarity: 指示が明確で曖昧さがないか (25%)\n"
    "- completeness: 必要な情報が全て含まれているか (25%)\n"
    "- structure: 論理的に整理されているか (25%)\n"
    "- practicality: 実際に使いやすいか (25%)\n\n"
    "スキル:\n```markdown\n{content}\n```\n\n"
    "以下のJSON形式で回答してください:\n"
    '{{"clarity": {{"score": 0.8, "reason": "..."}}, '
    '"completeness": {{"score": 0.7, "reason": "..."}}, '
    '"structure": {{"score": 0.9, "reason": "..."}}, '
    '"practicality": {{"score": 0.75, "reason": "..."}}, '
    '"total": 0.79}}'
)


# ── ベースライン I/O ──────────────────────────────────────

def load_baselines() -> List[Dict[str, Any]]:
    """quality-baselines.jsonl から全レコードを読み込む。"""
    if not BASELINES_FILE.exists():
        return []
    records = []
    for line in BASELINES_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def get_skill_records(records: List[Dict[str, Any]], skill_name: str) -> List[Dict[str, Any]]:
    """指定スキルのレコードをタイムスタンプ順で返す。"""
    skill_recs = [r for r in records if r.get("skill_name") == skill_name]
    skill_recs.sort(key=lambda r: r.get("timestamp", ""))
    return skill_recs


def save_baselines(records: List[Dict[str, Any]]) -> None:
    """quality-baselines.jsonl にレコードを書き出す（上限適用済み）。"""
    BASELINES_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    BASELINES_FILE.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")


def append_record(record: Dict[str, Any]) -> None:
    """レコードを追記し、スキルあたりの上限を適用する。"""
    all_records = load_baselines()
    all_records.append(record)

    # スキルあたりの上限チェック
    skill_name = record["skill_name"]
    skill_recs = [r for r in all_records if r.get("skill_name") == skill_name]
    if len(skill_recs) > MAX_RECORDS_PER_SKILL:
        # 古い順にソートして超過分を削除
        skill_recs.sort(key=lambda r: r.get("timestamp", ""))
        to_remove = len(skill_recs) - MAX_RECORDS_PER_SKILL
        remove_timestamps = {r["timestamp"] for r in skill_recs[:to_remove]}
        all_records = [
            r for r in all_records
            if r.get("skill_name") != skill_name or r.get("timestamp") not in remove_timestamps
        ]

    save_baselines(all_records)


# ── スキル検出 ──────────────────────────────────────────

def resolve_skill_path(skill_name: str) -> Optional[Path]:
    """スキル名から SKILL.md のパスを解決する。"""
    # ~/.claude/skills/{name}/SKILL.md
    global_path = Path.home() / ".claude" / "skills" / skill_name / "SKILL.md"
    if global_path.exists():
        return global_path

    # プラグインキャッシュ配下を探索
    import os
    plugins_dir = os.environ.get("CLAUDE_PLUGINS_DIR")
    if plugins_dir:
        plugins_path = Path(plugins_dir)
    else:
        plugins_path = Path.home() / ".claude" / "plugins" / "cache"

    if plugins_path.exists():
        for skill_md in plugins_path.rglob("SKILL.md"):
            if skill_md.parent.name == skill_name:
                return skill_md

    return None


def find_high_freq_skills(days: int = HIGH_FREQ_DAYS, threshold: int = HIGH_FREQ_THRESHOLD) -> Dict[str, int]:
    """高頻度 global/plugin スキルを検出し、{skill_name: count} を返す。"""
    records = load_usage_data(days=days)
    usage = aggregate_usage(records)

    high_freq = {}
    for skill_name, count in usage.items():
        if count < threshold:
            continue
        path = resolve_skill_path(skill_name)
        if path is None:
            continue
        origin = classify_artifact_origin(path)
        if origin in ("global", "plugin"):
            high_freq[skill_name] = count

    return high_freq


# ── LLM 品質評価 ──────────────────────────────────────────

def _parse_cot_response(text: str) -> Tuple[float, Optional[Dict[str, Any]]]:
    """CoT JSON レスポンスをパース（optimize.py と同一ロジック）。"""
    json_match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    json_str = json_match.group(1).strip() if json_match else text.strip()

    try:
        data = json.loads(json_str)
        if isinstance(data, dict) and "total" in data:
            total = float(data["total"])
            return max(0.0, min(1.0, total)), data
        if isinstance(data, dict):
            criteria = ["clarity", "completeness", "structure", "practicality"]
            scores = []
            for c in criteria:
                if c in data and isinstance(data[c], dict) and "score" in data[c]:
                    scores.append(float(data[c]["score"]))
            if scores:
                total = sum(scores) / len(scores)
                data["total"] = round(total, 2)
                return max(0.0, min(1.0, total)), data
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    match = re.search(r"(0\.\d+|1\.0|0|1)", json_str)
    if match:
        return float(match.group(1)), None

    print("Warning: CoT response parse failed, score defaults to 0.5", file=sys.stderr)
    return 0.5, None


def evaluate_skill(skill_content: str, timeout: int = 60) -> Optional[Tuple[float, Optional[Dict[str, Any]]]]:
    """スキル内容を CoT 付きで品質評価する。

    Returns:
        (score, cot_result) タプル。タイムアウト/エラー時は None。
    """
    prompt = _COT_PROMPT_TEMPLATE.format(content=skill_content)
    try:
        result = subprocess.run(
            ["claude", "-p", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return _parse_cot_response(result.stdout.strip())
        print(f"claude -p failed (rc={result.returncode}): {result.stderr.strip()}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print("claude -p timed out, skipping measurement", file=sys.stderr)
    except FileNotFoundError:
        print("claude command not found, skipping measurement", file=sys.stderr)
    return None


# ── 再スコアリング判定 ──────────────────────────────────────

def needs_rescore(skill_name: str, current_usage_count: int, baselines: Optional[List[Dict[str, Any]]] = None) -> bool:
    """再スコアリングが必要かどうか判定する。"""
    if baselines is None:
        baselines = load_baselines()

    skill_recs = get_skill_records(baselines, skill_name)
    if not skill_recs:
        return True  # 初回計測

    latest = skill_recs[-1]

    # 使用回数トリガー
    last_usage = latest.get("usage_count_at_measure", 0)
    if current_usage_count - last_usage >= RESCORE_USAGE_THRESHOLD:
        return True

    # 期間トリガー
    last_ts = latest.get("timestamp", "")
    if last_ts:
        try:
            last_dt = datetime.fromisoformat(last_ts)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            elapsed = datetime.now(timezone.utc) - last_dt
            if elapsed >= timedelta(days=RESCORE_DAYS_THRESHOLD):
                return True
        except (ValueError, TypeError):
            return True

    return False


# ── 劣化検知 ──────────────────────────────────────────

def compute_baseline_score(skill_recs: List[Dict[str, Any]]) -> float:
    """スキルの最高スコア（ベースライン）を返す。"""
    if not skill_recs:
        return 0.0
    return max(r.get("score", 0.0) for r in skill_recs)


def compute_moving_average(skill_recs: List[Dict[str, Any]], window: int = 3) -> float:
    """直近 N 回の移動平均を算出する。"""
    if not skill_recs:
        return 0.0
    recent = skill_recs[-window:]
    scores = [r.get("score", 0.0) for r in recent]
    return sum(scores) / len(scores)


def detect_degradation(skill_name: str, baselines: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """劣化を検知し、劣化情報を返す。劣化なしなら None。"""
    if baselines is None:
        baselines = load_baselines()

    skill_recs = get_skill_records(baselines, skill_name)
    if len(skill_recs) < 2:
        return None  # 初回またはデータ不足

    baseline = compute_baseline_score(skill_recs)
    if baseline <= 0:
        return None

    avg = compute_moving_average(skill_recs)
    decline_rate = (baseline - avg) / baseline

    if decline_rate >= DEGRADATION_THRESHOLD:
        return {
            "skill_name": skill_name,
            "current_score": round(avg, 4),
            "baseline_score": round(baseline, 4),
            "decline_rate": round(decline_rate * 100, 1),
            "recommended_command": f"/optimize {skill_name}",
        }
    return None


# ── メイン実行 ──────────────────────────────────────────

def run_quality_monitor(dry_run: bool = False) -> Dict[str, Any]:
    """品質モニタリングを実行する。

    Returns:
        {
            "measured": [{"skill_name": ..., "score": ..., ...}],
            "skipped": [{"skill_name": ..., "reason": ...}],
            "degraded": [{"skill_name": ..., "current_score": ..., ...}],
        }
    """
    high_freq = find_high_freq_skills()
    baselines = load_baselines()

    result: Dict[str, Any] = {"measured": [], "skipped": [], "degraded": []}

    for skill_name, usage_count in high_freq.items():
        if not needs_rescore(skill_name, usage_count, baselines):
            result["skipped"].append({"skill_name": skill_name, "reason": "below threshold"})
            continue

        path = resolve_skill_path(skill_name)
        if path is None:
            result["skipped"].append({"skill_name": skill_name, "reason": "SKILL.md not found"})
            continue

        if dry_run:
            result["measured"].append({"skill_name": skill_name, "score": None, "dry_run": True})
            continue

        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            result["skipped"].append({"skill_name": skill_name, "reason": str(e)})
            continue

        eval_result = evaluate_skill(content)
        if eval_result is None:
            result["skipped"].append({"skill_name": skill_name, "reason": "LLM evaluation failed"})
            continue

        score, cot = eval_result
        record = {
            "skill_name": skill_name,
            "score": round(score, 4),
            "criteria": cot if cot else {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "usage_count_at_measure": usage_count,
            "skill_path": str(path),
        }
        append_record(record)
        result["measured"].append(record)

        # ベースラインを再読み込みして劣化検知
        updated_baselines = load_baselines()
        degradation = detect_degradation(skill_name, updated_baselines)
        if degradation:
            result["degraded"].append(degradation)

    return result


def main() -> None:
    """CLI エントリポイント。"""
    import argparse

    parser = argparse.ArgumentParser(description="品質モニタリング: 高頻度スキルの品質スコアを計測")
    parser.add_argument("--dry-run", action="store_true", help="実際の LLM 評価を行わず対象スキルのみ表示")
    args = parser.parse_args()

    result = run_quality_monitor(dry_run=args.dry_run)

    if result["measured"]:
        print(f"\n計測完了: {len(result['measured'])} スキル")
        for m in result["measured"]:
            score_str = f"{m['score']:.2f}" if m.get("score") is not None else "(dry-run)"
            print(f"  - {m['skill_name']}: {score_str}")

    if result["skipped"]:
        print(f"\nスキップ: {len(result['skipped'])} スキル")
        for s in result["skipped"]:
            print(f"  - {s['skill_name']}: {s['reason']}")

    if result["degraded"]:
        print(f"\n⚠ 劣化検知: {len(result['degraded'])} スキル")
        for d in result["degraded"]:
            print(
                f"  - {d['skill_name']}: {d['current_score']:.2f} "
                f"(baseline {d['baseline_score']:.2f}, -{d['decline_rate']:.1f}%) "
                f"→ {d['recommended_command']}"
            )

    if not result["measured"] and not result["skipped"]:
        print("\n対象スキルなし（高頻度 global/plugin スキルが見つかりませんでした）")


if __name__ == "__main__":
    main()
