"""judge_audit.harness — judge false-pass 欠陥注入ハーネス（opt-in CLI・#188）。

standalone verbosity/judge.py（#75）と同型の分離:

- **dry-run 既定**（llm-batch-guard 準拠）: 未判定 fixture 数 + 推定 LLM 呼び出し回数 +
  推定トークンを print して終わる。**実 LLM を呼ばない・1 バイトも書かない**。
- ``--run`` で実行。subprocess.run(["claude",...]) は **call_judge_llm 1 箇所に集約**
  （単体テストはここを mock する。no-llm-in-tests 完全整合）。
- 判定に使うプロンプト/パーサは ``scripts/rl/fitness/constitutional.py`` の
  ``_build_eval_prompt`` / ``_parse_layer_response`` を再利用する（judge の実プロンプトに
  流す＝issue #188 の「judge 経路に流し」要件）。
- 判定結果を judge_audit_verdicts.jsonl（store_write barrier 経由）に永続化する。

PJ スコープ: 判定は当 PJ slug でのみ読み書きする（verbosity/subagent_traces と同型）。
"""
from __future__ import annotations

import argparse
import datetime
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

# 絶対 import: スクリプト直起動（__main__）でも、パッケージ import（judge_audit.harness）
# でも解決する（verbosity/judge.py と同型・相対 import は __main__ で壊れる既知 pitfall）。
from judge_audit import PASS_THRESHOLD
from judge_audit.fixtures import FIXTURES
from judge_audit import store as _store

# 1 fixture あたりの概算トークン（プロンプト + 応答）。llm-batch-guard 用の粗い見積もり
# （verbosity.judge._TOKENS_PER_CANDIDATE と同思想）。
_TOKENS_PER_FIXTURE = 500
_PROMPT_OVERHEAD_TOKENS = 300


def _constitutional_mod():
    """scripts/rl/fitness/constitutional を遅延 import する（実際の judge プロンプト/パーサを再利用）。

    audit/orchestrator.py と同じ ``PLUGIN_ROOT / "scripts" / "rl"`` を sys.path に追加し
    ``fitness.constitutional`` パッケージ経由で import する（constitutional.py の
    ``from .config import ...`` 相対 import を壊さないため）。
    """
    from plugin_root import PLUGIN_ROOT

    fitness_dir = PLUGIN_ROOT / "scripts" / "rl"
    if str(fitness_dir) not in sys.path:
        sys.path.insert(0, str(fitness_dir))
    from fitness import constitutional as _c

    return _c


def call_judge_llm(prompt: str, model: str) -> str:
    """judge を 1 回呼ぶ（subprocess の唯一の集約点・単体テストはここを mock する）。"""
    out = subprocess.run(
        ["claude", "-p", prompt, "--model", model],
        capture_output=True,
        text=True,
        timeout=180,
    )
    return out.stdout.strip()


def estimate_cost(n: int) -> Dict[str, int]:
    """未判定件数から推定 LLM 呼び出し回数・トークンを見積もる（llm-batch-guard 用・決定論）。"""
    est = n * _TOKENS_PER_FIXTURE + n * _PROMPT_OVERHEAD_TOKENS
    return {"fixtures": n, "est_total_tokens": est}


def build_fixture_prompt(fixture: Dict[str, Any]) -> str:
    """1 fixture の判定プロンプトを組み立てる（constitutional._build_eval_prompt を再利用）。"""
    c = _constitutional_mod()
    principle = {"id": fixture["principle_id"], "text": fixture["principle_text"]}
    return c._build_eval_prompt(fixture["layer_name"], fixture["content"], [principle])


def _extract_score(fixture: Dict[str, Any], raw: str) -> Optional[float]:
    """judge の生レスポンスから対象 principle のスコアを取り出す（constitutional の実パーサ経由）。"""
    c = _constitutional_mod()
    parsed = c._parse_layer_response(raw)
    if not parsed or not parsed.get("evaluations"):
        return None
    for ev in parsed["evaluations"]:
        if ev.get("principle_id") == fixture["principle_id"]:
            return ev.get("score")
    # principle_id が一致しない応答でも先頭要素を採用する（judge が id を書き漏らす場合の救済）。
    return parsed["evaluations"][0].get("score")


def run_audit(
    slug: str,
    *,
    run: bool = False,
    limit: Optional[int] = None,
    model: str = "sonnet",
    data_dir: Optional[Path] = None,
    out=None,
) -> Dict[str, Any]:
    """当 PJ の未判定 fixture を judge に流し false-pass を計測する（dry-run 既定）。

    Args:
        slug:  対象 PJ slug。
        run:   True で実行。False（既定）は dry-run（コスト先出しのみ・LLM 非呼出・非書込）。
        limit: 1 回で判定する最大件数（既定 None = 全未判定）。
        out:   出力先（既定 stdout）。テストで差し替え可能。

    Returns:
        dry-run: {"dry_run": True, "total_fixtures", "judged", "pending", "cost": {...}}
        run:     {"dry_run": False, "judged_now", "false_pass", "false_pass_rate",
                  "verdicts_written"}
    """
    out = out if out is not None else sys.stdout

    judged = _store.read_verdicts(slug, data_dir=data_dir)
    pending = [f for f in FIXTURES if f["id"] not in judged]
    target = pending[:limit] if limit is not None else pending

    print(
        f"欠陥 fixture 総数: {len(FIXTURES)} / 判定済み: {len(judged)} / 未判定: {len(pending)}"
        f"（PJ: {slug}）",
        file=out,
    )

    if not run:
        cost = estimate_cost(len(target))
        print(
            f"\n[dry-run] 実行すると judge を約 {cost['fixtures']} 回呼びます"
            f"（推定 ~{cost['est_total_tokens']} トークン）。",
            file=out,
        )
        print("実行は --run を付けてください。", file=out)
        if cost["fixtures"] == 0:
            print("→ 未判定の fixture がありません。", file=out)
        return {
            "dry_run": True,
            "total_fixtures": len(FIXTURES),
            "judged": len(judged),
            "pending": len(target),
            "cost": cost,
        }

    if not target:
        print("未判定の fixture がありません。", file=out)
        return {
            "dry_run": False,
            "judged_now": 0,
            "false_pass": 0,
            "false_pass_rate": None,
            "verdicts_written": 0,
        }

    new_verdicts: List[Dict[str, Any]] = []
    false_pass_count = 0

    for fixture in target:
        prompt = build_fixture_prompt(fixture)
        try:
            raw = call_judge_llm(prompt, model)
        except Exception as e:  # noqa: BLE001 - 1件の失敗は次の fixture へ継続
            print(f"  {fixture['id']}: 呼び出し失敗 ({e})", file=sys.stderr)
            continue

        score = _extract_score(fixture, raw)
        # fixture は既知の違反（正解=失敗）なので、judge が合格(score>=閾値)と判定したら false-pass。
        judge_passed = bool(score is not None and score >= PASS_THRESHOLD)
        rec = {
            "id": fixture["id"],
            "pj_slug": slug,
            "principle_id": fixture["principle_id"],
            "score": score,
            "judge_passed": judge_passed,
            "false_pass": judge_passed,
            "judged_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        new_verdicts.append(rec)
        if judge_passed:
            false_pass_count += 1

    # 永続化（store_write barrier 経由）。
    for rec in new_verdicts:
        _store.write_verdict(rec)

    n = len(new_verdicts)
    rate = round(false_pass_count / n, 4) if n else None
    print(f"\n=== 判定結果（今回 {n} 件 / PJ: {slug}）===", file=out)
    print(f"false-pass: {false_pass_count} 件 ({(rate or 0) * 100:.0f}%)", file=out)

    return {
        "dry_run": False,
        "judged_now": n,
        "false_pass": false_pass_count,
        "false_pass_rate": rate,
        "verdicts_written": n,
    }


def _slug_for_cwd() -> str:
    """CLAUDE_PROJECT_DIR / cwd から当 PJ slug を解決する（reader 側・authoritative）。"""
    try:
        from pj_slug import resolve_pj_slug

        cwd = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        return resolve_pj_slug(cwd)
    except Exception:  # noqa: BLE001
        cwd = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        return Path(cwd).name or "unknown"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="judge false-pass 欠陥注入監査（The Blind Curator arXiv 2607.07436・#188）"
    )
    ap.add_argument("--run", action="store_true", help="実際に judge を呼ぶ（既定は dry-run）")
    ap.add_argument("--limit", type=int, default=None, help="1回で判定する最大件数（既定 全未判定）")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--slug", default=None, help="対象 PJ slug（既定は cwd から解決）")
    args = ap.parse_args(argv)

    slug = args.slug or _slug_for_cwd()
    run_audit(slug, run=args.run, limit=args.limit, model=args.model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
