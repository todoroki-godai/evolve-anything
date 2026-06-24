"""verbosity.judge — 溜まった応答候補を Haiku で「無駄に冗長か」判定する（#75）。

standalone ``~/.claude/verbosity/judge.py`` を移植し、evolve-anything の慣習に統合する:

- **dry-run 既定**（llm-batch-guard 準拠）: 未判定件数 + 推定 Haiku 呼び出し回数 + 推定
  トークンを print して終わる。**実 LLM を呼ばない・1 バイトも書かない**。
- ``--run`` で実判定。subprocess.run(["claude",...]) は **call_haiku 1 箇所に集約**
  （単体テストはここを mock する。no-llm-in-tests 完全整合）。
- 判定結果を verbosity_verdicts.jsonl（store_write barrier 経由）に永続化。
- verbose=True を weak_signals に ``channel="verbosity"`` で emit（append_signals・
  reflect 昇格フローに相乗り）。
- 多発パターンから rules/concise.md 追記案（suggestion）を生成して **出力**する
  （auto-apply しない・protected。``~/.claude/output-styles/concise.md`` は CC グローバル
  機能で evolve-anything 管理外＝書き換えない）。

PJ スコープ: 候補/判定は当 PJ slug でのみ読み書きする（subagent_traces #38 と同型）。
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

# 絶対 import: スクリプト直起動（__main__・audit が案内する `judge.py --run`）でも、
# パッケージ import（verbosity.judge）でも解決する。相対 import は __main__ で壊れる。
from verbosity import PATTERNS, VERBOSITY_CHANNEL
from verbosity import store as _store

# 1 候補あたりの概算トークン（プロンプト雛形 + 応答本文）。correction_semantic.batch の
# 係数思想に倣う粗い見積もり（llm-batch-guard 用）。
_TOKENS_PER_CANDIDATE = 600
_PROMPT_OVERHEAD_TOKENS = 400

PROMPT_HEAD = """あなたは日本語の文章を厳しく評価する編集者。各候補のアシスタント応答について、\
「無駄に冗長か（needlessly verbose）」だけを判定する。長いこと自体は減点しない。\
情報密度が高ければ長くても verbose=false。短くても水増しがあれば verbose=true。

冗長パターンの語彙（該当するものを patterns に列挙）:
- preamble: 前置き・「承知しました」等のメタ文
- repetition: 同じ主張の繰り返し・言い換え重複
- filler: 水増し・情報を増やさない冗長な接続/修飾
- over_summary: 過剰なまとめ・締めの繰り返し
- restate_question: 質問・依頼文の不要な言い直し
- hedging: 過剰な前置き・保険・自己弁護
- meta: 不要な自己言及

出力は JSON 配列のみ。各要素は {"i": <番号>, "verbose": <true/false>, \
"patterns": [<上記キー>], "note": "<10〜30字で最も無駄な点>"}。\
マークダウンや説明文は一切付けない。

候補:
"""

# rules/concise.md 追記案のパターン別文言（standalone judge.py と単一ソース）。
_RULES_FOR = {
    "preamble": "- 「承知しました」「了解です」等の応答冒頭メタ文を書かない",
    "repetition": "- 同じ主張を言い換えて2回書かない。1主張1回",
    "filler": "- 情報を増やさない修飾・接続を削る（「基本的に」「という形で」等）",
    "over_summary": "- 末尾の「まとめると〜」を、本文と重複するなら書かない",
    "restate_question": "- ユーザーの質問文を引用・言い直さずに本題から入る",
    "hedging": "- 過剰な保険・前置きを削り、断定できることは断定する",
    "meta": "- 「〜について説明します」等の自己言及を削り、内容から始める",
}


def call_haiku(prompt: str, model: str = "haiku") -> str:
    """Haiku を 1 回呼ぶ（subprocess の唯一の集約点・単体テストはここを mock する）。"""
    out = subprocess.run(
        ["claude", "-p", prompt, "--model", model],
        capture_output=True,
        text=True,
        timeout=180,
    )
    return out.stdout.strip()


def parse_json_array(s: str) -> List[dict]:
    """LLM 応答（前後にマークダウン混入しうる）から JSON 配列を抽出する。"""
    s = (s or "").strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if "```" in s[3:] else s
        s = s.lstrip("json").strip("`\n ")
    start, end = s.find("["), s.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        arr = json.loads(s[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    return [x for x in arr if isinstance(x, dict)]


def estimate_cost(pending: int, batch_size: int) -> Dict:
    """未判定件数からバッチ数・推定トークンを見積もる（llm-batch-guard 用・決定論）。"""
    bs = max(1, int(batch_size))
    batches = (pending + bs - 1) // bs
    est = pending * _TOKENS_PER_CANDIDATE + batches * _PROMPT_OVERHEAD_TOKENS
    return {"pending": pending, "batches": batches, "batch_size": bs, "est_total_tokens": est}


def build_batch_prompt(batch: List[dict], max_chars: int) -> str:
    """1 バッチ分のプロンプトを組み立てる（決定論・LLM 非依存）。"""
    body = PROMPT_HEAD
    for i, c in enumerate(batch):
        txt = (c.get("text") or "")[:max_chars]
        body += f"\n--- 候補 {i} (project={c.get('project')}, {c.get('char_len')}字) ---\n{txt}\n"
    return body


def build_suggestion(pat_counter: collections.Counter) -> Optional[str]:
    """多発した冗長パターンから rules/concise.md 追記案を組み立てる（auto-apply しない）。"""
    if not pat_counter:
        return None
    lines = ["# rules/concise.md 追記案（多発した冗長パターンの抑制・人間確認後に取り込む）", ""]
    for p, _ in pat_counter.most_common():
        if p in _RULES_FOR:
            lines.append(_RULES_FOR[p])
    lines.append("")
    lines.append(
        f"<!-- suggestion generated {datetime.datetime.now().isoformat(timespec='minutes')}"
        " by verbosity.judge (#75) -->"
    )
    return "\n".join(lines) + "\n"


def run_judge(
    slug: str,
    *,
    run: bool = False,
    limit: int = 30,
    batch_size: int = 6,
    model: str = "haiku",
    max_chars: int = 4000,
    data_dir: Optional[Path] = None,
    weak_signals_path: Optional[Path] = None,
    out=None,
) -> Dict:
    """当 PJ の未判定候補を Haiku で判定する（dry-run 既定）。

    Args:
        slug:  対象 PJ slug。
        run:   True で実判定。False（既定）は dry-run（コスト先出しのみ・LLM 非呼出・非書込）。
        out:   出力先（既定 stdout）。テストで差し替え可能。

    Returns:
        dry-run: {"dry_run": True, "candidates", "judged", "pending", "cost": {...}}
        run:     {"dry_run": False, "judged_now", "verbose", "verbose_rate",
                  "patterns": {pat: count}, "weak_written", "verdicts_written",
                  "suggestion": str|None}
    """
    out = out if out is not None else sys.stdout

    candidates = _store.read_candidates(slug, data_dir=data_dir)
    judged = _store.read_judged_hashes(slug, data_dir=data_dir)
    pending = [c for c in candidates if c.get("hash") not in judged]

    print(
        f"候補総数: {len(candidates)} / 判定済み: {len(judged)} / 未判定: {len(pending)}（PJ: {slug}）",
        file=out,
    )

    target = pending[:limit]

    if not run:
        cost = estimate_cost(len(target), batch_size)
        print(
            f"\n[dry-run] 実判定すると Haiku を約 {cost['batches']} 回呼びます"
            f"（{cost['pending']} 件 / batch {cost['batch_size']} / 推定 ~{cost['est_total_tokens']} トークン）。",
            file=out,
        )
        print(
            "Haiku は最安ティア・オンデマンド（毎ターン課金ではない）。実行は --run を付けてください。",
            file=out,
        )
        if cost["pending"] == 0:
            print("→ 未判定の候補がありません。数セッション使ってから再実行してください。", file=out)
        return {
            "dry_run": True,
            "candidates": len(candidates),
            "judged": len(judged),
            "pending": len(target),
            "cost": cost,
        }

    if not target:
        print("未判定の候補がありません。", file=out)
        return {
            "dry_run": False,
            "judged_now": 0,
            "verbose": 0,
            "verbose_rate": None,
            "patterns": {},
            "weak_written": 0,
            "verdicts_written": 0,
            "suggestion": None,
        }

    pat_counter: collections.Counter = collections.Counter()
    verbose_count = 0
    new_verdicts: List[dict] = []
    verbose_records: List[dict] = []

    for bi in range(0, len(target), batch_size):
        batch = target[bi: bi + batch_size]
        prompt = build_batch_prompt(batch, max_chars)
        try:
            raw = call_haiku(prompt, model)
        except Exception as e:  # noqa: BLE001 - バッチ失敗は次バッチへ継続
            print(f"  batch {bi // batch_size}: 呼び出し失敗 ({e})", file=sys.stderr)
            continue
        by_i = {v.get("i"): v for v in parse_json_array(raw)}
        for i, c in enumerate(batch):
            v = by_i.get(i, {})
            is_verbose = bool(v.get("verbose"))
            pats = [p for p in (v.get("patterns") or []) if p in PATTERNS]
            rec = {
                "hash": c.get("hash"),
                "pj_slug": slug,
                "project": c.get("project"),
                "verbose": is_verbose,
                "patterns": pats,
                "note": v.get("note", ""),
                "char_len": c.get("char_len"),
                "judged_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            new_verdicts.append(rec)
            if is_verbose:
                verbose_count += 1
                pat_counter.update(pats)
                verbose_records.append((c, pats, v.get("note", "")))

    # 永続化（store_write barrier 経由）。
    for rec in new_verdicts:
        _store.write_verdict(rec)

    # verbose=True を weak_signals に channel="verbosity" で emit（reflect 昇格フローに相乗り）。
    weak_written = _emit_weak_signals(slug, verbose_records, weak_signals_path)

    # レポート出力。
    n = len(new_verdicts)
    rate = round(verbose_count / n, 4) if n else None
    print(f"\n=== 判定結果（今回 {n} 件 / PJ: {slug}）===", file=out)
    print(f"無駄に冗長と判定: {verbose_count} 件 ({(rate or 0) * 100:.0f}%)", file=out)
    if pat_counter:
        print("\n冗長パターン（多い順）:", file=out)
        for p, c in pat_counter.most_common():
            print(f"  - {p}: {c} 件  … {PATTERNS[p]}", file=out)

    suggestion = build_suggestion(pat_counter)
    if suggestion:
        print("\n--- rules/concise.md 追記案（提示のみ・自動適用しない）---", file=out)
        print(suggestion, file=out)
        print(
            "↑ 内容を確認のうえ rules/concise.md へ手で取り込んでください"
            "（output-styles/concise.md は CC グローバル機能のため自動編集しません）。",
            file=out,
        )

    return {
        "dry_run": False,
        "judged_now": n,
        "verbose": verbose_count,
        "verbose_rate": rate,
        "patterns": dict(pat_counter),
        "weak_written": weak_written,
        "verdicts_written": n,
        "suggestion": suggestion,
    }


def _emit_weak_signals(
    slug: str, verbose_records: List, weak_signals_path: Optional[Path]
) -> int:
    """verbose=True を weak_signals レーン（channel="verbosity"）へ emit する。"""
    if not verbose_records:
        return 0
    from weak_signals.store import WeakSignal, append_signals, now_iso

    signals: List[WeakSignal] = []
    detected_at = now_iso()
    for c, pats, note in verbose_records:
        prov = {
            "hash": c.get("hash"),
            "project": c.get("project"),
            "patterns": pats,
            "note": note,
            "char_len": c.get("char_len"),
        }
        signals.append(
            WeakSignal(
                channel=VERBOSITY_CHANNEL,
                provenance=prov,
                detected_at=detected_at,
                session_id=str(c.get("session_id") or ""),
                pj_slug=slug,
            )
        )
    res = append_signals(signals, path=weak_signals_path)
    return int(res.get("written", 0))


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
    ap = argparse.ArgumentParser(description="回答冗長性の Haiku バッチ判定（#75）")
    ap.add_argument("--run", action="store_true", help="実際に Haiku を呼ぶ（既定は dry-run）")
    ap.add_argument("--limit", type=int, default=30, help="1回で判定する最大件数")
    ap.add_argument("--batch-size", type=int, default=6)
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--max-chars", type=int, default=4000, help="候補1件あたり送る最大文字数")
    ap.add_argument("--slug", default=None, help="対象 PJ slug（既定は cwd から解決）")
    args = ap.parse_args(argv)

    slug = args.slug or _slug_for_cwd()
    run_judge(
        slug,
        run=args.run,
        limit=args.limit,
        batch_size=args.batch_size,
        model=args.model,
        max_chars=args.max_chars,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
