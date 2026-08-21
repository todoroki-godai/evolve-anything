#!/usr/bin/env python3
"""A0 (correction capture 修理) — 実コーパスリプレイ harness（repo 内固定版）。

codex レビュー（設計修正要・[Must] 9件）への対応として scratchpad 版を全面書き直し。
scratchpad 版との違い:
  1. 候補パターンは ``rl_common.detection.CORRECTION_PATTERNS`` を monkeypatch し、
     判定は本番と同一の ``detect_correction`` / ``should_include_message`` をそのまま
     呼ぶ（FALSE_POSITIVE_FILTERS・false_positives.jsonl のハッシュ除外・辞書全走査を
     含む単一実装。候補専用の重複ロジックを持たない）。
  2. コーパス窓は [SINCE, UNTIL) で両端固定し、DB スナップショット識別情報
     （sha256 / size / mtime / 行数）を記録する。
  3. recall/precision は独立ラベル評価集合（層化ランダム抽出・seed 固定、候補語彙とは
     無関係に抽出）で測る。ラベルは ``a0_eval_set.jsonl``（本ファイルと同じ dir）に
     保存し、rationale を必須にすることで単一レビュアーでも監査可能にする。
  4. Wilson 95% 信頼区間を precision/recall に付す。

read-only 保証: utterances.db は ``duckdb.connect(..., read_only=True)`` でのみ開く。
このスクリプトは utterances.db に一切書き込まない（eval_set.jsonl と結果 JSON への
書込みのみ、いずれも repo 内の本 harness 専用ファイル）。

使い方:
    # 1. 母集団のスナップショット確認 + サンプル（単純無作為n件 + machinery全数）の文脈ダンプ
    python3 scripts/bench/a0_capture_replay.py dump-sample --n 80 --seed 20260812 \
        --out scripts/bench/a0_sample_dump.json

    # 2. （手動で a0_eval_set.jsonl にラベルを追記した後）候補パターンの評価
    python3 scripts/bench/a0_capture_replay.py evaluate

    # 3. 全量の候補ヒット census（短文側のみ。長文側は本設計で対象外と結論済み）
    python3 scripts/bench/a0_capture_replay.py census

    # 4. 固定窓母集団の識別情報（本文なし・sha256のみ）を repo に固定
    python3 scripts/bench/a0_capture_replay.py population-fingerprint

round2 codex [Must-3] 対応: dump-sample のサンプリングは「母集団全体から単純無作為 n 件」
+「machinery-suspect 全件（ランダム抽出と重複しない分）」を単一関数
（``sample_random_plus_machinery_oversample``）で生成する。ドキュメント記載の抽出方法と
実装が別ファイルに分裂しない。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import duckdb  # noqa: E402

import rl_common.detection as det  # noqa: E402
from capture_recall import evaluate_capture_recall, load_capture_eval_set  # noqa: E402

import os  # noqa: E402

# 実行者のローカル utterances.db。個人ディレクトリを repo に焼き込まない（public 化予定のため）。
DB_PATH = Path(
    os.environ.get("A0_UTTERANCES_DB")
    or (Path(os.environ.get("CLAUDE_PLUGIN_DATA") or (Path.home() / ".claude" / "evolve-anything")) / "utterances.db")
)
HERE = Path(__file__).resolve().parent
EVAL_SET_PATH = HERE / "a0_eval_set.jsonl"

# 固定コーパス窓（両端固定・codex [Must]4 対応）。UNTIL は「今日 2026-08-12 の 00:00 UTC
# より前」に固定し、以降 DB が増えても本 harness の対象母集団は変化しない。
SINCE = "2026-07-27T00:00:00Z"
UNTIL = "2026-08-12T00:00:00Z"

MAX_CAPTURE_PROMPT_LENGTH = 500

# machinery-suspect の広義マーカー（評価専用・production の is_machinery_prompt を
# 置き換えるものではない）。委譲プロンプト・エージェント間 handoff 文が文中に埋め込まれた
# ケースを評価上「machinery 疑い」として層別するための緩いシグナル（codex [Must]5）。
_MACHINERY_SUSPECT_MARKERS = (
    "teammate-message",
    "another claude session sent a message",
    "background agents were stopped by the user",
    "=== report end ===",
    "=== impl complete ===",
)


def machinery_suspect(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _MACHINERY_SUSPECT_MARKERS)


# ---------------------------------------------------------------------------
# スナップショット識別（codex [Must]4）
# ---------------------------------------------------------------------------

def snapshot_identity() -> Dict[str, Any]:
    stat = DB_PATH.stat()
    h = hashlib.sha256()
    with open(DB_PATH, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        total_rows = con.execute("SELECT COUNT(*) FROM utterances").fetchone()[0]
        window_rows = con.execute(
            "SELECT COUNT(*) FROM utterances WHERE source_kind='dialogue' "
            "AND timestamp >= ? AND timestamp < ?",
            [SINCE, UNTIL],
        ).fetchone()[0]
    finally:
        con.close()
    return {
        "db_path": str(DB_PATH),
        "size_bytes": stat.st_size,
        "mtime": stat.st_mtime,
        "sha256": h.hexdigest(),
        "total_rows": total_rows,
        "window_since": SINCE,
        "window_until": UNTIL,
        "window_dialogue_rows": window_rows,
    }


# ---------------------------------------------------------------------------
# 母集団ロード
# ---------------------------------------------------------------------------

def load_window_rows() -> List[Dict[str, Any]]:
    print(f"[harness] connecting read-only: {DB_PATH}", flush=True)
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = con.execute(
            "SELECT source_path, line_no, pj_slug, session_id, timestamp, text, prev_action "
            "FROM utterances WHERE source_kind='dialogue' AND timestamp >= ? AND timestamp < ? "
            "ORDER BY timestamp",
            [SINCE, UNTIL],
        ).fetchall()
    finally:
        con.close()
    cols = ["source_path", "line_no", "pj_slug", "session_id", "timestamp", "text", "prev_action"]
    out = [dict(zip(cols, r)) for r in rows]
    print(f"[harness] loaded {len(out)} dialogue rows in [{SINCE}, {UNTIL})", flush=True)
    return out


def classify_should_include_reason(text: str) -> str:
    """should_include_message の除外理由を分解する（本番ロジックの順序をそのまま踏襲）。

    reason 分解は分析専用（本番の bool 判定そのものは常に det.should_include_message を
    呼んで二重チェックする。detect_correction のトリガー判定自体は本番関数のみに依存し、
    この関数は「なぜ除外されたか」の内訳表示にのみ使う）。
    """
    t = text.strip()
    if not t:
        return "empty"
    if det.is_machinery_prompt(t):
        return "machinery"
    if re.search(r"(?i)^remember:", t):
        return "include_remember"
    if len(t) > MAX_CAPTURE_PROMPT_LENGTH:
        return "too_long"
    skip_patterns = [
        r"^<", r"^\[", r"^\{",
        r"tool_result", r"tool_use_id",
        r"<command-", r"<task-notification>", r"<system-reminder>",
        r"This session is being continued",
        r"^Analysis:", r"^\*\*", r"^   -",
    ]
    for pattern in skip_patterns:
        if re.search(pattern, t):
            return "skip_pattern"
    return "include"


def eligible_population(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """should_include_message 通過（本番関数そのものを呼んで二重確認）の行のみ返す。"""
    out = []
    for r in rows:
        reason = classify_should_include_reason(r["text"])
        included_by_reason = reason in ("include", "include_remember")
        included_by_prod = det.should_include_message(r["text"])
        if included_by_reason != included_by_prod:
            print(f"[harness][WARN] reason/prod mismatch: reason={reason} prod={included_by_prod} "
                  f"text={r['text'][:60]!r}", flush=True)
        if included_by_prod:
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# 候補パターン定義（本番 CORRECTION_PATTERNS を monkeypatch で差し替えて評価する）
# ---------------------------------------------------------------------------

def candidate_baseline() -> Dict[str, Any]:
    return dict(det.CORRECTION_PATTERNS)


def candidate_vocab_addition() -> Dict[str, Any]:
    """推奨案: 既存28パターン + 新規2パターン（直して/修正して/訂正して・やめて系）。

    round2 codex [Should-3] + 頭の裁定: 複合動詞（作り直して/書き直して/考え直して/やり直して）
    は「見直して」と同様に新規作業指示（AIの誤りへの訂正ではない）である可能性が高いため、
    固定長 negative lookbehind を連ねて除外する。現コーパスでは実測0件だが、将来の FP を
    構造で防ぐ（P7: 未決のまま残さない）。
    """
    patterns = dict(det.CORRECTION_PATTERNS)
    patterns["naoshite-request"] = {
        "pattern": r"(?<!見)(?<!作り)(?<!書き)(?<!考え)(?<!やり)直して|修正して|訂正して",
        "confidence": 0.75, "type": "correction", "decay_days": 90,
    }
    patterns["yamete-request"] = {
        "pattern": r"やめて(ほしい|ください|くれ)",
        "confidence": 0.75, "type": "correction", "decay_days": 90,
    }
    return patterns


def run_production_detect(patterns: Dict[str, Any], text: str):
    """本番 detect_correction を候補パターンで monkeypatch して呼ぶ（単一実装・codex [Must]2）。"""
    original = det.CORRECTION_PATTERNS
    det.CORRECTION_PATTERNS = patterns
    try:
        return det._detect_correction(text, false_positive_hashes=())
    finally:
        det.CORRECTION_PATTERNS = original


# ---------------------------------------------------------------------------
# ランダムサンプリング + machinery 全数オーバーサンプル
# （codex round2 [Must-3] 対応: 「n=80 単純無作為 + machinery 全数」を harness 単体で
# 再生成できる単一関数にする。ドキュメント記載と実装を別々に持たない）
# ---------------------------------------------------------------------------

def sample_random_plus_machinery_oversample(
    pop: List[Dict[str, Any]], n: int, seed: int
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """母集団全体から seed 固定の単純無作為抽出で n 件を抜き、machinery-suspect 全件を
    （ランダム抽出と重複しない分だけ）追加する。

    Returns:
        (random_sample, machinery_oversample_extra) — 2つは互いに排他（重複キーは
        random_sample 側にのみ残す）。呼び出し側は両方を合算して使う。
    """
    rng = random.Random(seed)
    random_sample = rng.sample(pop, min(n, len(pop)))
    random_keys = {(r["source_path"], r["line_no"]) for r in random_sample}
    machinery_all = [r for r in pop if machinery_suspect(r["text"])]
    oversample_extra = [r for r in machinery_all if (r["source_path"], r["line_no"]) not in random_keys]
    return random_sample, oversample_extra


_PRIOR_LOOKBACK_LINES = 150
"""assistant テキストを遡って探す行数。round2 codex [Must-6] で判明: IDE メタデータ行
（attachment/last-prompt/ai-title/mode/permission-mode/pr-link/file-history-snapshot 等、
type が user/assistant でない行）が直前の実 assistant ターンとの間に 30 行を超えて挟まる
ケースが実測で複数見つかった（例: 44行・36行離れていた）。30→150 に拡張。"""


def fetch_prior_assistant_text(source_path: str, line_no: int, max_chars: int = 400) -> Optional[str]:
    """raw transcript から、当該行より前の直近 assistant テキストを取得する。

    utterances.db の prev_action 列はツール名列のみ（実内容なし）で、かつ実測で
    extractor_version=2（2026-07-14 以降の再抽出分）は 0/1971 件が非 null という
    データ欠損があり、本コーパス窓（07-27以降）では使用不能と判明した
    （codex [Must]5 への回答は本関数による raw transcript 直読みで代替する）。
    """
    p = Path(source_path)
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None
    # line_no は 1-indexed（utterances.db 保存規約に合わせる）
    idx = line_no - 1
    for i in range(idx - 1, max(-1, idx - _PRIOR_LOOKBACK_LINES), -1):
        if i < 0 or i >= len(lines):
            continue
        try:
            obj = json.loads(lines[i])
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "assistant":
            continue
        msg = obj.get("message", {})
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif isinstance(block, dict) and block.get("type") == "tool_use":
                    parts.append(f"[tool_use:{block.get('name')}]")
            text = " ".join(parts)
        if text.strip():
            return text.strip()[:max_chars]
    return None


# ---------------------------------------------------------------------------
# CLI: dump-sample
# ---------------------------------------------------------------------------

def _keys_from_artifact(path: Path) -> set:
    """既出サンプルの (source_path, line_no) 集合を成果物から読む。

    受け付ける形式: ラベル JSONL（1行1レコード）と dump-sample の JSON
    （``{"samples": [...]}``）。存在しない/壊れた行は黙って読み飛ばす（抽出の前段で
    落とすためのもので、ここで落ちると拡充作業自体が止まるため）。
    """
    keys = set()
    if not path.exists():
        return keys
    text = path.read_text(encoding="utf-8", errors="replace")
    stripped = text.lstrip()
    recs: List[Dict[str, Any]] = []
    if stripped.startswith("{") and '"samples"' in stripped[:2000]:
        try:
            recs = json.loads(text).get("samples", [])
        except (json.JSONDecodeError, ValueError):
            recs = []
    else:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue
    for r in recs:
        if isinstance(r, dict):
            keys.add((r.get("source_path"), r.get("line_no")))
    return keys


def cmd_dump_sample(args: argparse.Namespace) -> None:
    snap = snapshot_identity()
    print(f"[harness] snapshot: {json.dumps(snap, ensure_ascii=False)}", flush=True)

    rows = load_window_rows()
    pop = eligible_population(rows)
    print(f"[harness] eligible (should_include_message 通過) population: {len(pop)}", flush=True)

    # 評価セット拡充（柱1 G2・条件2）: 既にラベル済みの行を母集団から除いてから抽出する。
    # 既存 86 件を引き直さないためであり、除外後の残余に対する単純無作為抽出は保たれる
    # （既存分も同じ窓・同じ eligible 判定から無作為に引かれているため、和集合も同窓の標本）。
    if getattr(args, "exclude_labeled", False):
        labeled_keys = {(e.get("source_path"), e.get("line_no")) for e in load_eval_set()}
        for extra in (getattr(args, "exclude_from", None) or []):
            labeled_keys |= _keys_from_artifact(Path(extra))
        before = len(pop)
        pop = [r for r in pop if (r["source_path"], r["line_no"]) not in labeled_keys]
        print(f"[harness] exclude-labeled: {before} -> {len(pop)} "
              f"(既出 {len(labeled_keys)} 件を除外)", flush=True)

    random_sample, machinery_extra = sample_random_plus_machinery_oversample(pop, args.n, args.seed)
    print(f"[harness] random_sample={len(random_sample)}  machinery_oversample_extra={len(machinery_extra)} "
          f"(seed={args.seed})", flush=True)

    combined = [(r, "random") for r in random_sample] + [(r, "machinery_oversample") for r in machinery_extra]

    dump = []
    for i, (r, src) in enumerate(combined):
        prior = fetch_prior_assistant_text(r["source_path"], r["line_no"])
        dump.append({
            "sample_id": i,
            "sample_source": src,
            "pj_slug": r["pj_slug"],
            "session_id": r["session_id"],
            "timestamp": r["timestamp"],
            "source_path": r["source_path"],
            "line_no": r["line_no"],
            "text": r["text"],
            "machinery_suspect": machinery_suspect(r["text"]),
            "prior_assistant_text": prior,
        })
        if (i + 1) % 20 == 0:
            print(f"[harness]   ...context fetched {i+1}/{len(combined)}", flush=True)

    out = {
        "snapshot": snap,
        "population_size": len(pop),
        "sample_seed": args.seed,
        "sample_n_requested": args.n,
        "exclude_labeled": bool(getattr(args, "exclude_labeled", False)),
        "random_sample_n": len(random_sample),
        "machinery_oversample_n": len(machinery_extra),
        "samples": dump,
    }
    out_path = Path(args.out)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[harness] wrote sample dump to {out_path}", flush=True)


# ---------------------------------------------------------------------------
# CLI: evaluate（ラベル済み a0_eval_set.jsonl を使って recall/precision を計算）
# ---------------------------------------------------------------------------

def load_eval_set() -> List[Dict[str, Any]]:
    if not EVAL_SET_PATH.exists():
        return []
    return load_capture_eval_set(EVAL_SET_PATH)


def cmd_evaluate(args: argparse.Namespace) -> None:
    eval_set = load_eval_set()
    if not eval_set:
        print(f"[harness] {EVAL_SET_PATH} が空です。先に dump-sample → ラベル付け → 本コマンドの順で実行してください。", flush=True)
        return
    print(f"[harness] loaded {len(eval_set)} labeled examples from {EVAL_SET_PATH}", flush=True)

    candidates = {
        "baseline": candidate_baseline(),
        "d_vocab_addition": candidate_vocab_addition(),
    }

    for cand_name, patterns in candidates.items():
        print(f"\n=== 候補: {cand_name} ===", flush=True)
        # gated=True が本番経路（should_include_message → detect_correction の2段）。
        # gated=False は検出器単体。本番は前さばきで machinery 等を落としてから
        # detect_correction を呼ぶため、単体だけを測ると「本番では到達しない行」を
        # 誤検知に数えて precision を過小評価する（2026-08-18 実測: 唯一の FP が
        # machinery 行で、本番経路では precision 1/1 なのに単体では 1/2 に見えた）。
        for gated in (True, False):
            metrics = evaluate_capture_recall(
                eval_set,
                lambda text: run_production_detect(patterns, text),
                det.should_include_message if gated else None,
            )
            tp_caught, gt_positive = metrics["caught"], metrics["positives"]
            hits = metrics["hits"]
            recall, precision = metrics["recall"], metrics["precision"]
            recall_ci, precision_ci = metrics["recall_ci"], metrics["precision_ci"]

            tag = "本番経路(should_include_message→detect)" if gated else "検出器単体(参考)"
            print(f"  [{tag}] eval_set n={len(eval_set)}  評価対象 TP={gt_positive}", flush=True)
            print(f"    捕捉率 recall = {tp_caught}/{gt_positive} = {recall:.3f}  "
                  f"Wilson95%=({recall_ci[0]:.3f}, {recall_ci[1]:.3f})  "
                  f"[取りこぼし {1 - recall:.3f}]", flush=True)
            print(f"    精度 precision = {tp_caught}/{hits} = {precision:.3f}  "
                  f"Wilson95%=({precision_ci[0]:.3f}, {precision_ci[1]:.3f})", flush=True)

    # machinery-suspect 層別内訳
    print("\n=== machinery_suspect 層別（eval_set 内） ===", flush=True)
    n_suspect = sum(1 for e in eval_set if e.get("machinery_suspect"))
    n_genuine = len(eval_set) - n_suspect
    print(f"  machinery_suspect={n_suspect}  genuine={n_genuine}", flush=True)
    tp_suspect = sum(1 for e in eval_set if e.get("machinery_suspect") and e["label"] == "TP")
    tp_genuine = sum(1 for e in eval_set if not e.get("machinery_suspect") and e["label"] == "TP")
    print(f"  TP within machinery_suspect={tp_suspect}/{n_suspect}  TP within genuine={tp_genuine}/{n_genuine}", flush=True)


# ---------------------------------------------------------------------------
# CLI: census（固定窓・全量に対する候補ヒット census。個別に目視ラベルを付す対象の列挙用）
# ---------------------------------------------------------------------------

def cmd_census(args: argparse.Namespace) -> None:
    snap = snapshot_identity()
    print(f"[harness] snapshot: {json.dumps(snap, ensure_ascii=False)}", flush=True)
    rows = load_window_rows()
    pop = eligible_population(rows)
    print(f"[harness] eligible population: {len(pop)}", flush=True)

    patterns = candidate_vocab_addition()
    hits = []
    for r in pop:
        result = run_production_detect(patterns, r["text"])
        if result is not None:
            prior = fetch_prior_assistant_text(r["source_path"], r["line_no"])
            hits.append({**r, "match": result, "machinery_suspect": machinery_suspect(r["text"]),
                         "prior_assistant_text": prior})
    print(f"[harness] candidate d_vocab_addition total hits over full window population: {len(hits)}", flush=True)
    for h in hits:
        print(f"  [{h['pj_slug']}] key={h['match'][0]!r} machinery_suspect={h['machinery_suspect']} "
              f"text={h['text'][:100]!r}", flush=True)

    out_path = Path(args.out) if args.out else HERE / "a0_full_census.json"
    Path(out_path).write_text(
        json.dumps({"snapshot": snap, "population_size": len(pop), "hits": hits}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[harness] wrote census to {out_path}", flush=True)


# ---------------------------------------------------------------------------
# CLI: population-fingerprint（codex round2 [Must-4] 軽量版）
#
# DB 本体のスナップショットは取らない（93MB・他PJ実発話の重複保存が見合わない・頭の裁定）。
# 代わりに固定窓母集団（1,124件）の識別情報だけを軽量 JSONL で固定する:
# (source_path, line_no, timestamp, pj_slug, text の sha256)。本文は含めない
# （本文を保存するのは a0_eval_set.jsonl の86件と a0_full_census.json の9件のみでよい）。
# 制約: DB の再抽出・backfill 後にこの fingerprint を完全再生成できる保証はない
# （constraint として明記するのみ・silence != evaluated）。
# ---------------------------------------------------------------------------

def cmd_population_fingerprint(args: argparse.Namespace) -> None:
    snap = snapshot_identity()
    rows = load_window_rows()
    pop = eligible_population(rows)
    print(f"[harness] eligible population: {len(pop)}", flush=True)

    out_path = Path(args.out) if args.out else HERE / "a0_population_fingerprint.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": "population_fingerprint", "snapshot": snap,
                             "population_size": len(pop)}, ensure_ascii=False) + "\n")
        for r in pop:
            text_sha256 = hashlib.sha256(r["text"].encode("utf-8")).hexdigest()
            f.write(json.dumps({
                "source_path": r["source_path"],
                "line_no": r["line_no"],
                "timestamp": r["timestamp"],
                "pj_slug": r["pj_slug"],
                "text_sha256": text_sha256,
            }, ensure_ascii=False) + "\n")
    print(f"[harness] wrote population fingerprint ({len(pop)} rows) to {out_path}", flush=True)
    print("[harness] CONSTRAINT: DB の再抽出/backfill 後にこの fingerprint と完全一致する census を"
          " 再生成できる保証はない（本文非保存のため）。この harness は sha256 の不一致検知にのみ使う。",
          flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_dump = sub.add_parser("dump-sample")
    p_dump.add_argument("--n", type=int, default=80)
    p_dump.add_argument("--seed", type=int, default=20260812)
    p_dump.add_argument("--out", type=str, default=str(HERE / "a0_sample_dump.json"))
    p_dump.add_argument(
        "--exclude-labeled", action="store_true",
        help="a0_eval_set.jsonl に既出の (source_path, line_no) を母集団から除いてから抽出する（評価セット拡充用）",
    )
    p_dump.add_argument(
        "--exclude-from", action="append", default=None,
        help="追加で除外する既出サンプルの成果物（ラベル JSONL / dump-sample の JSON）。複数指定可",
    )
    p_dump.set_defaults(func=cmd_dump_sample)

    p_eval = sub.add_parser("evaluate")
    p_eval.set_defaults(func=cmd_evaluate)

    p_census = sub.add_parser("census")
    p_census.add_argument("--out", type=str, default=None)
    p_census.set_defaults(func=cmd_census)

    p_fp = sub.add_parser("population-fingerprint")
    p_fp.add_argument("--out", type=str, default=None)
    p_fp.set_defaults(func=cmd_population_fingerprint)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
