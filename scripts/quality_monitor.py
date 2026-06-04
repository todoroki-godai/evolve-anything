#!/usr/bin/env python3
"""品質モニタリングスクリプト。

高頻度 global/plugin スキルの品質スコアを定期的に計測し、
劣化を検知して /optimize 推奨を通知する。

claude -p 全廃（[ADR-037]）に伴い、品質評価は llm_broker のファイルベース2相に分離した:
  Phase A: emit_rescore_requests が「再スコアすべきスキル」と CoT プロンプトを JSON 化（LLM ゼロ）
  Phase B: assistant が各 prompt を CoT 採点（claude -p なし＝subscription 課金）
  Phase C: ingest_responses が応答をパースし baselines 追記・劣化検知（LLM ゼロ）
audit パイプライン（run_audit）は LLM を呼ばず既存 baselines を読むのみ。再スコアは
audit SKILL.md が2相でオーケストレーションする。
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_plugin_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_plugin_root / "skills" / "audit" / "scripts"))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

from audit import (
    DATA_DIR,
    aggregate_usage,
    classify_artifact_origin,
    load_usage_data,
)
from llm_broker import build_requests, parse_responses, passthrough

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
    if not skill_name:
        return None
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


def build_cot_prompt(skill_content: str) -> str:
    """スキル内容から CoT 品質評価プロンプトを組み立てる（Phase A・LLM ゼロ）。"""
    return _COT_PROMPT_TEMPLATE.format(content=skill_content)


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
    """直近 N 回の移動平均を算出する。criteria キーが存在して空のレコード（採点失敗）は除外する。"""
    if not skill_recs:
        return 0.0
    # criteria キーがある場合のみ空チェック。キー自体がない旧フォーマットは有効扱い
    valid = [r for r in skill_recs if not ("criteria" in r and not r["criteria"])]
    if not valid:
        return 0.0
    recent = valid[-window:]
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

def emit_rescore_requests() -> Dict[str, Any]:
    """再スコアすべきスキルの採点リクエストを生成する（Phase A・LLM ゼロ）。

    claude -p を呼ばず、「どのスキルを採点すべきか」と CoT プロンプトだけを JSON 化可能な
    形で返す。assistant（Phase B）が各 prompt を CoT 採点して responses（id→生テキスト）を作り、
    ingest_responses（Phase C）が集約する。

    Returns:
        {
            "requests": [{"id": skill_name, "prompt": str, "meta": {"skill_path", "usage_count"}}],
            "skipped": [{"skill_name": ..., "reason": ...}],
        }
    """
    high_freq = find_high_freq_skills()
    baselines = load_baselines()

    items: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for skill_name, usage_count in high_freq.items():
        if not needs_rescore(skill_name, usage_count, baselines):
            skipped.append({"skill_name": skill_name, "reason": "below threshold"})
            continue
        path = resolve_skill_path(skill_name)
        if path is None:
            skipped.append({"skill_name": skill_name, "reason": "SKILL.md not found"})
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            skipped.append({"skill_name": skill_name, "reason": str(e)})
            continue
        items.append({
            "id": skill_name,
            "skill_path": str(path),
            "usage_count": usage_count,
            "_content": content,
        })

    requests = build_requests(items, lambda it: build_cot_prompt(it["_content"]))
    # _content はプロンプトに埋め込み済みなので meta から除く（responses JSON の肥大化防止）
    for r in requests:
        r["meta"].pop("_content", None)

    return {"requests": requests, "skipped": skipped}


def ingest_responses(
    requests: List[Dict[str, Any]], responses: Dict[str, Any]
) -> Dict[str, Any]:
    """Phase B の CoT 採点応答を集約し baselines 追記・劣化検知する（Phase C・LLM ゼロ）。

    Args:
        requests: emit_rescore_requests の出力（id→skill_path/usage_count）
        responses: {skill_name: CoT 生テキスト}。欠損 id は採点漏れとして skip する。

    Returns:
        {"measured": [...], "degraded": [...], "skipped": [...]}
    """
    parsed = parse_responses(requests, responses, parser=passthrough)
    result: Dict[str, Any] = {"measured": [], "degraded": [], "skipped": []}

    for req in requests:
        skill_name = req["id"]
        text = parsed.get(skill_name)
        if not text or not str(text).strip():
            result["skipped"].append({"skill_name": skill_name, "reason": "no response"})
            continue

        score, cot = _parse_cot_response(str(text))
        record = {
            "skill_name": skill_name,
            "score": round(score, 4),
            "criteria": cot if cot else {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "usage_count_at_measure": req.get("meta", {}).get("usage_count"),
            "skill_path": req.get("meta", {}).get("skill_path"),
        }
        append_record(record)
        result["measured"].append(record)

        degradation = detect_degradation(skill_name, load_baselines())
        if degradation:
            result["degraded"].append(degradation)

    return result


def main(argv: Optional[List[str]] = None) -> int:
    """ファイルベース2相 CLI（[ADR-037]）。claude -p は呼ばない。

    Phase A: quality_monitor.py --emit-requests
        → 再スコア対象と CoT プロンプトの JSON を stdout に出力。assistant がこれを読み、
          各 prompt を CoT 採点して responses.json（{skill_name: 生テキスト}）を作る。
    Phase C: quality_monitor.py --ingest --requests <requests.json> --responses <responses.json>
        → 応答を集約し baselines 追記・劣化検知。結果サマリを表示。
    """
    import argparse

    parser = argparse.ArgumentParser(description="品質モニタリング: 高頻度スキルの品質スコア計測（2相）")
    parser.add_argument("--emit-requests", action="store_true", help="Phase A: 再スコア対象と CoT プロンプトを JSON 出力")
    parser.add_argument("--ingest", action="store_true", help="Phase C: 採点応答を集約して baselines 更新")
    parser.add_argument("--requests", metavar="PATH", help="--ingest 用 requests JSON")
    parser.add_argument("--responses", metavar="PATH", help="--ingest 用 responses JSON")
    parser.add_argument("--dry-run", action="store_true", help="再スコア対象スキルのみ表示（LLM・書き込みなし）")
    args = parser.parse_args(argv)

    if args.emit_requests or args.dry_run:
        emitted = emit_rescore_requests()
        if args.dry_run:
            if emitted["requests"]:
                print(f"\n再スコア対象: {len(emitted['requests'])} スキル")
                for r in emitted["requests"]:
                    print(f"  - {r['id']}")
            if emitted["skipped"]:
                print(f"\nスキップ: {len(emitted['skipped'])} スキル")
                for s in emitted["skipped"]:
                    print(f"  - {s['skill_name']}: {s['reason']}")
            if not emitted["requests"] and not emitted["skipped"]:
                print("\n対象スキルなし（高頻度 global/plugin スキルが見つかりませんでした）")
        else:
            print(json.dumps(emitted, ensure_ascii=False, indent=2))
        return 0

    if args.ingest:
        if not args.requests or not args.responses:
            parser.error("--ingest には --requests と --responses が必要です")
        req_doc = json.loads(Path(args.requests).read_text(encoding="utf-8"))
        requests = req_doc["requests"] if isinstance(req_doc, dict) else req_doc
        responses = json.loads(Path(args.responses).read_text(encoding="utf-8"))
        result = ingest_responses(requests, responses)

        if result["measured"]:
            print(f"\n計測完了: {len(result['measured'])} スキル")
            for m in result["measured"]:
                print(f"  - {m['skill_name']}: {m['score']:.2f}")
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
        return 0

    parser.error("--emit-requests / --ingest / --dry-run のいずれかを指定してください")
    return 2


if __name__ == "__main__":
    sys.exit(main())
