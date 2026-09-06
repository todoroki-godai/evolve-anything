"""judge_eval — 修正発話 judge（correction_semantic）の LLM 判定精度 eval ランナー。

**実 LLM 呼出は既定で禁止**（``--dry-run`` が既定・llm-batch-guard 準拠）。``--run`` で
初めて呼ぶが、その前に harness-integrity gate（後述）を通す必要がある。

対象は「意味判定（Phase B）が is_correction を正しく当てられるか」（#408 経路 B）。
**実エントリポイントをそのまま呼ぶ**（判定器を再実装しない）:
  - プロンプト構築: ``correction_semantic.prompt.build_batch_prompt``（本番と同一関数）
  - LLM 呼び出し: ``correction_semantic.judge_runner.call_haiku``（本番の唯一の集約点。
    単体テストはここを mock する）
  - 応答パース: ``correction_semantic.prompt.parse_verdicts_result``（本番と同一関数）

**本番ストアに一切書かない**（保証方法）: 本ファイルは ``correction_semantic.batch`` /
``correction_semantic.store`` / ``weak_signals.store`` のいずれも import しない
（grep で確認可能: ``grep -n '^import\\|^from' scripts/bench/judge_eval.py``）。
書込先は本 eval 専用の ``.claude/hillclimb/correction-judge/`` 配下のみで、
``correction_judged.jsonl`` 等の DATA_DIR 配下ファイルへは触れない。

**ラベル対応規則**: 凍結コーパス（``scripts/bench/a0_eval_set.jsonl``。無ければ
``~/.claude/evolve-anything/bench/a0_eval_set.jsonl``。SHA/行数は
``capture_recall.load_capture_eval_set`` が検証する単一ソース）の ``label``
（``"TP"``/``"not_TP"``）は judge の ``is_correction`` 二値判定と 1:1 対応する
（``expected_is_correction``）。**コーパスの ``category``（new_task/question/... 14 種、
「発話の種類」を表す）と judge の ``category``（factual/process/... 8 種 enum、
「is_correction=true のときの対象軸」を表す）は別の語彙**であり、本ランナーは
両者の突合をグレーディングに使わない（意味が異なる比較になるため）。

**harness-integrity gate**（``shared/evals/build-eval.md`` 契約）: ``--run`` は
``HARNESS_PATHS``（本ファイル + プロンプト/呼び出し口）の内容ハッシュを
``.claude/hillclimb/correction-judge/_state.json`` の承認済みハッシュと照合し、
不一致・未承認なら exit 2 で拒否する。承認は ``--approve-harness`` を明示的に渡した
ユーザーの操作としてのみ成立する（本ランナーが自動承認しない）。

使い方:
    # 見積もりのみ（LLM 非呼出・非書込）
    python3 scripts/bench/judge_eval.py --dry-run --limit 30

    # 初回承認（内容を読んでから実行者が明示的に渡す）
    python3 scripts/bench/judge_eval.py --run --limit 30 --approve-harness

    # 以後は承認済みハッシュのまま追加実行できる
    python3 scripts/bench/judge_eval.py --run --limit 30
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

_BENCH_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _BENCH_DIR.parent
_PLUGIN_ROOT = _SCRIPTS_DIR.parent
_LIB_DIR = _SCRIPTS_DIR / "lib"
for _p in (_BENCH_DIR, _LIB_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from capture_recall import load_capture_eval_set, wilson_interval  # noqa: E402
from correction_semantic import DEFAULT_BATCH_SIZE  # noqa: E402
from correction_semantic import judge_runner as _judge_runner  # noqa: E402
from correction_semantic import prompt as _prompt  # noqa: E402

# ─────────────────────────────────────────────────────────────────
# 定数・パス
# ─────────────────────────────────────────────────────────────────

FLOW_DIR = _PLUGIN_ROOT / ".claude" / "hillclimb" / "correction-judge"
REPO_EVAL_SET_PATH = _BENCH_DIR / "a0_eval_set.jsonl"
FALLBACK_EVAL_SET_PATH = Path.home() / ".claude" / "evolve-anything" / "bench" / "a0_eval_set.jsonl"

# harness-integrity gate 対象ファイル（相対パス・_PLUGIN_ROOT 基準）。
# プロンプト・パーサ・呼び出し口のいずれかが変わればハッシュも変わる。
HARNESS_PATHS: Tuple[str, ...] = (
    "scripts/bench/judge_eval.py",
    "scripts/lib/correction_semantic/__init__.py",
    "scripts/lib/correction_semantic/judge_runner.py",
    "scripts/lib/correction_semantic/prompt.py",
    "scripts/lib/safe_llm_call.py",
)

# tacchi レビュー Should-2: 凍結コーパス 416 件は全件 prev_action 空（row_to_utterance が
# 常に None を渡すため）。本番母集団・本番判定実績では prev_action が付く行が一定割合
# 存在する（レビュー時点の指摘値: 母集団 12%・本番判定実績 34%。本ランナー自身では
# 未計測のため、この note はレビュー指摘の転記であって独自実測ではない）。
# このスライス（prev_action 付き 0/416）は本 eval の対象外＝未測定である旨を明記する。
PREV_ACTION_COVERAGE_NOTE = (
    "prev_action 付き発話は本 eval コーパスでは 0/416（全件 prev_action 空）。"
    "本番母集団では約12%、本番判定実績では約34%が prev_action 付き"
    "（レビュー指摘値・本ランナーでは未計測）。"
    "このスライスの judge 精度は本 eval では測定していない。"
)

METRICS: List[Dict[str, Any]] = [
    {"id": "accuracy", "label": "accuracy", "kind": "binary"},
    {"id": "precision_hit", "label": "precision", "kind": "binary"},
    {"id": "recall_hit", "label": "recall", "kind": "binary"},
    {"id": "specificity_hit", "label": "specificity", "kind": "binary"},
]
PERF_FIELDS: List[Dict[str, Any]] = [
    {"id": "latency_s", "label": "latency", "unit": "s"},
]

# 429/過負荷の再試行上限（ジッタ付き backoff）。
_MAX_ATTEMPTS = 3


# ─────────────────────────────────────────────────────────────────
# コーパス読み込み・ラベル対応
# ─────────────────────────────────────────────────────────────────

def resolve_eval_set_path(override: Optional[Path] = None) -> Path:
    """凍結コーパスの実パスを解決する（repo 内 → DATA_DIR 配下の順）。"""
    if override is not None:
        return Path(override)
    if REPO_EVAL_SET_PATH.exists():
        return REPO_EVAL_SET_PATH
    return FALLBACK_EVAL_SET_PATH


def load_corpus(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """凍結コーパスを SHA/行数検証つきで読む（``capture_recall`` が単一ソース。再実装しない）。"""
    resolved = resolve_eval_set_path(path)
    return load_capture_eval_set(resolved)


def expected_is_correction(row: Dict[str, Any]) -> bool:
    """eval コーパスの ``label`` → judge ``is_correction`` への期待値変換（唯一の対応規則）。

    モジュール docstring の「ラベル対応規則」参照。コーパスの ``category`` は
    judge の ``category`` enum とは別語彙のため、ここでは使わない。
    """
    label = row.get("label")
    if label not in ("TP", "not_TP"):
        raise ValueError(f"unexpected label: {label!r}")
    return label == "TP"


def row_to_utterance(row: Dict[str, Any]) -> Dict[str, Any]:
    """eval コーパス行 → ``prompt.build_batch_prompt`` が期待する utterance dict への変換。

    **暫定判断（報告に明記済み）**: 本番の ``prev_action`` は「直前 Claude 操作のツール名
    要約」（例: "Read, Bash"）であり、eval コーパスの ``prior_assistant_text``（直前
    assistant 発言の全文）とは意味が異なる別フィールドのため変換しない。``prev_action``
    は欠損時と同じ扱い（プロンプト上 "(なし)"）にする。
    """
    return {
        "text": row.get("text") or "",
        "prev_action": None,
    }


def chunk(items: Sequence[Any], size: int) -> List[List[Any]]:
    size = max(1, int(size))
    return [list(items[i:i + size]) for i in range(0, len(items), size)]


def grade_case(expected: bool, predicted: Optional[bool]) -> Optional[Dict[str, int]]:
    """混同行列の各セルに該当する軸だけを 0/1 で積む（該当しない軸は grade dict に含めない）。

    ``predicted`` が ``None``（verdict 欠落・呼び出し失敗）は採点不能。呼び出し側が
    errors.jsonl に回すこと（None を返すのはその合図）。
    """
    if predicted is None:
        return None
    grade: Dict[str, int] = {"accuracy": int(predicted == expected)}
    if predicted:
        grade["precision_hit"] = int(expected is True)
    if expected:
        grade["recall_hit"] = int(predicted is True)
    if not expected:
        grade["specificity_hit"] = int(predicted is False)
    return grade


# ─────────────────────────────────────────────────────────────────
# harness-integrity gate
# ─────────────────────────────────────────────────────────────────

def compute_harness_sha(paths: Sequence[str] = HARNESS_PATHS, *, base_dir: Path = _PLUGIN_ROOT) -> str:
    """判定に使うファイル群の内容ハッシュ（順序非依存・欠落ファイルも区別する）。"""
    h = hashlib.sha256()
    for rel in sorted(paths):
        p = base_dir / rel
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes() if p.exists() else b"<missing>")
        h.update(b"\0")
    return h.hexdigest()


def load_state(state_path: Path) -> Dict[str, Any]:
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def check_harness_approval(
    state: Dict[str, Any], current_sha: str, *, approve: bool
) -> Tuple[bool, Dict[str, Any]]:
    """harness-integrity gate 本体。

    ``approve=True``（``--approve-harness``。ユーザー自身が渡すフラグであり本ランナーは
    自動承認しない）なら現在のハッシュを承認済みとして記録し常に True を返す。
    それ以外は「記録済みハッシュと現在のハッシュが一致するか」で可否を判定する。
    未承認（記録なし）は不可とする（fail-closed）。

    Returns:
        (ok, new_state) — ok=False のとき呼び出し側は state を書き戻さないこと。
    """
    recorded_block = state.get("harness_paths")
    recorded_sha = recorded_block.get("sha") if isinstance(recorded_block, dict) else None
    if approve:
        new_state = dict(state)
        new_state["harness_paths"] = {"paths": sorted(HARNESS_PATHS), "sha": current_sha}
        return True, new_state
    if recorded_sha is None:
        return False, state
    return recorded_sha == current_sha, state


# ─────────────────────────────────────────────────────────────────
# 実行（dry-run / run）
# ─────────────────────────────────────────────────────────────────

@dataclass
class RunConfig:
    variant: str = "baseline"
    model: str = "haiku"
    reps: int = 1
    batch_size: int = DEFAULT_BATCH_SIZE
    seed: int = 0


def run_dry(cases: List[Dict[str, Any]], cfg: RunConfig) -> Dict[str, Any]:
    """LLM を呼ばず、対象件数・バッチ分割・陽性件数だけを出す（既定・非書込）。"""
    groups = chunk(cases, cfg.batch_size)
    positives = sum(1 for r in cases if expected_is_correction(r))
    return {
        "dry_run": True,
        "cases": len(cases),
        "positives": positives,
        "negatives": len(cases) - positives,
        "batches_per_rep": len(groups),
        "batch_size": cfg.batch_size,
        "reps": cfg.reps,
        "total_llm_calls_if_run": len(groups) * cfg.reps,
    }


def _classify_error(e: Exception) -> str:
    msg = str(e)
    name = type(e).__name__
    if "Timeout" in name:
        return "timeout"
    lowered = msg.lower()
    # tacchi レビュー Should-6: 旧実装の "rate" in lowered は "generate"/"accurate" 等の
    # 無関係な語に誤マッチする。429・レートリミット・過負荷を示す具体的な語句にのみ限定する。
    if "429" in msg or "overloaded" in lowered or "rate limit" in lowered or "rate-limit" in lowered:
        return "rate_limited"
    return "api_error"


def compute_confusion_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """graded 済み行（status="ok"）から混同行列 + Wilson 95% CI を導出する（決定論・IO なし）。

    ``capture_recall.wilson_interval`` を再利用する（独自実装しない・単一ソース）。
    """
    tp = fn = fp = tn = 0
    for r in rows:
        if r.get("status") != "ok":
            continue
        meta = r.get("meta") or {}
        expected = meta.get("expected")
        predicted = meta.get("predicted")
        if expected is None or predicted is None:
            continue
        if expected and predicted:
            tp += 1
        elif expected and not predicted:
            fn += 1
        elif not expected and predicted:
            fp += 1
        else:
            tn += 1

    positives = tp + fn
    negatives = tn + fp
    predicted_positive = tp + fp
    n = positives + negatives

    def _ratio(num: int, den: int) -> Optional[float]:
        return num / den if den else None

    recall_ci = wilson_interval(tp, positives) if positives else None
    precision_ci = wilson_interval(tp, predicted_positive) if predicted_positive else None
    specificity_ci = wilson_interval(tn, negatives) if negatives else None

    return {
        "n": n, "tp": tp, "fn": fn, "fp": fp, "tn": tn,
        "positives": positives, "negatives": negatives,
        "accuracy": _ratio(tp + tn, n),
        "precision": _ratio(tp, predicted_positive),
        "precision_ci": precision_ci,
        "recall": _ratio(tp, positives),
        "recall_ci": recall_ci,
        "specificity": _ratio(tn, negatives),
        "specificity_ci": specificity_ci,
    }


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _retry_call(
    fn: Callable[[str, str], str],
    prompt_text: str,
    model: str,
    *,
    sleep_fn: Callable[[float], None],
    rng: random.Random,
    max_attempts: int = _MAX_ATTEMPTS,
) -> str:
    # Pyright 対策: `raise last_exc` の対象が Optional にならないよう非 None の
    # プレースホルダで初期化する（実際にはループ内で必ず return/raise して抜けるため
    # この初期値が raise されることはない）。
    last_exc: Exception = RuntimeError("unreachable: _retry_call exited loop without return/raise")
    for attempt in range(max_attempts):
        try:
            return fn(prompt_text, model)
        except Exception as e:  # noqa: BLE001 - 分類して再試行するかどうかを決める
            last_exc = e
            if _classify_error(e) != "rate_limited" or attempt == max_attempts - 1:
                raise
            sleep_fn((2 ** attempt) + rng.uniform(0, 1))
    raise last_exc  # pragma: no cover - ループは必ず return/raise で抜ける


def _append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _existing_result_keys(results_path: Path) -> Set[Tuple[Any, int]]:
    """(prompt_id, rep) の完了済み集合（resume 冪等キー）。"""
    keys: Set[Tuple[Any, int]] = set()
    if not results_path.exists():
        return keys
    with results_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rep = (row.get("meta") or {}).get("rep", 0)
            keys.add((row.get("prompt_id"), rep))
    return keys


def run_eval(
    cases: List[Dict[str, Any]],
    cfg: RunConfig,
    *,
    flow_dir: Path,
    call_haiku_fn: Callable[[str, str], str] = _judge_runner.call_haiku,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    sleep_fn: Callable[[float], None] = time.sleep,
    rng: Optional[random.Random] = None,
) -> Dict[str, Any]:
    """1 variant の全 rep を実行し results.jsonl / traces / errors.jsonl へ追記する。

    (case, rep) 冪等: results.jsonl に既に行があるものは再実行しない。1件確定するたびに
    即追記する（バッチ単位の中断でも、確定済みの行は失わない）。

    バッチ単位の usage: ``call_haiku`` は生成テキストのみを返し usage/model を含まない
    （``safe_llm_call.call_claude_headless`` の既知の制約・報告に明記）。usage は
    各行 ``None`` とし、latency のみバッチ単位で計測して按分せず各行にそのまま複製する
    （按分規則: バッチ全体の呼び出し時間をバッチ内の全 case 行にコピーする。トークン
    usage が取れないため按分の必要自体がない）。

    **本番との既知の相違点（未測定・Nit）**:
    - 本番 ``judge_runner.select_daily_batch`` は timestamp 降順（新しい発話優先）で
      バッチを組む（#410 [Must]G）。本ランナーは凍結コーパスの並び順のままバッチ化する
      ため、バッチ内位置効果（先頭/末尾ほど精度が変わる等）があるかは本 eval では
      測定していない。
    - verdict 欠落（``omitted_verdict``）の扱いが本番と異なる: 本番
      ``ingest_judgement_results`` は部分応答の欠落発話を判定済みにせず次回 drain へ残す
      （再判定可能）。本ランナーは欠落を即 ``errors.jsonl`` の1レコードとして確定し、
      自動では再試行しない（resume は「results.jsonl に無い (case,rep)」を対象にするため
      再実行すれば再試行されるが、自動リトライループは無い）。
    """
    rng = rng or random.Random(cfg.seed)
    variant_dir = flow_dir / cfg.variant
    traces_dir = variant_dir / "traces"
    variant_dir.mkdir(parents=True, exist_ok=True)
    traces_dir.mkdir(parents=True, exist_ok=True)
    results_path = variant_dir / "results.jsonl"
    errors_path = variant_dir / "errors.jsonl"

    done = _existing_result_keys(results_path)
    # Nit: 意図した対象総数（case × rep）を summary に併記し、resume でスキップされた
    # 件数を requested/graded/errors の合計との差分から読めるようにする。
    summary = {
        "requested": 0, "graded": 0, "errors": 0, "batches_called": 0,
        "expected_total": len(cases) * cfg.reps,
    }

    for rep in range(cfg.reps):
        pending = [r for r in cases if (r["eval_id"], rep) not in done]
        for group in chunk(pending, cfg.batch_size):
            utterances = [row_to_utterance(r) for r in group]
            batch_prompt = _prompt.build_batch_prompt(utterances)
            batch_id = f"{cfg.variant}:rep{rep}:{group[0]['eval_id']}"
            summary["batches_called"] += 1
            t0 = time.monotonic()
            try:
                raw = _retry_call(call_haiku_fn, batch_prompt, cfg.model, sleep_fn=sleep_fn, rng=rng)
            except Exception as e:  # noqa: BLE001 - バッチ全体を errors.jsonl へ退避
                latency = round(time.monotonic() - t0, 3)
                for r in group:
                    _append_jsonl(errors_path, {
                        "batch_id": batch_id, "rep": rep, "case_id": r["eval_id"],
                        "failure_class": _classify_error(e), "error": str(e)[:500],
                        "model": cfg.model, "usage": None, "latency_s": latency,
                        "timestamp": now_fn().isoformat(),
                    })
                summary["errors"] += len(group)
                continue
            latency = round(time.monotonic() - t0, 3)
            parsed = _prompt.parse_verdicts_result(raw, expected_len=len(group))
            trace = [
                {"role": "user", "content": batch_prompt},
                {"role": "assistant", "content": raw},
            ]
            if not parsed["ok"]:
                for r in group:
                    _append_jsonl(errors_path, {
                        "batch_id": batch_id, "rep": rep, "case_id": r["eval_id"],
                        "failure_class": "parse_failed", "model": cfg.model, "usage": None,
                        "latency_s": latency, "timestamp": now_fn().isoformat(),
                    })
                summary["errors"] += len(group)
                continue

            by_index = {v["index"]: v for v in parsed["verdicts"]}
            fingerprint = _prompt.prompt_fingerprint()
            for local_i, r in enumerate(group):
                summary["requested"] += 1
                v = by_index.get(local_i)
                if v is None:
                    _append_jsonl(errors_path, {
                        "batch_id": batch_id, "rep": rep, "case_id": r["eval_id"],
                        "failure_class": "omitted_verdict", "model": cfg.model, "usage": None,
                        "latency_s": latency, "timestamp": now_fn().isoformat(),
                    })
                    summary["errors"] += 1
                    continue
                trace_path = traces_dir / f"{r['eval_id']}_rep{rep}.json"
                trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
                expected = expected_is_correction(r)
                predicted = bool(v.get("is_correction"))
                grade = grade_case(expected, predicted)
                text = r.get("text", "") or ""
                prompt_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
                # tacchi レビュー [Must]1: 他PJの生発話（本文・idiom・reason の引用/抜粋）を
                # results.jsonl（commit 対象）へ書かない。凍結コーパスは .gitignore で同じ
                # 理由により除外されているため、本文を含む行を commit すると矛盾する。
                # eval_id + prompt_sha256 があれば、手元の凍結コーパス（非commit）から
                # 本文を復元・検証できるため情報は失われない（traces/ は非commit・別途 gitignore
                # 済みなのでローカルデバッグ用に本文を残す）。
                row = {
                    "prompt_id": r["eval_id"],
                    "prompt": f"[redacted other-PJ utterance; sha256={prompt_sha256}]",
                    "tags": [r.get("label"), r.get("category"), r.get("pj_slug")],
                    "status": "ok",
                    "grade": grade,
                    "model": cfg.model,
                    "usage": None,
                    "latency_s": latency,
                    "meta": {
                        "rep": rep,
                        "expected": expected,
                        "predicted": predicted,
                        "judge_category": v.get("category"),
                        "batch_id": batch_id,
                        "batch_size": len(group),
                        "prompt_fingerprint": fingerprint,
                        "prompt_sha256": prompt_sha256,
                    },
                }
                _append_jsonl(results_path, row)
                summary["graded"] += 1
    return summary


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="修正発話 judge（correction_semantic）の LLM 判定精度 eval ランナー"
    )
    ap.add_argument("--variant", default="baseline")
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None, help="先頭 N 件のみ対象にする")
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    ap.add_argument("--run", action="store_true", help="実際に LLM を呼ぶ（既定は dry-run）")
    ap.add_argument("--dry-run", action="store_true", help="明示 dry-run（--run 未指定時の既定と同じ）")
    ap.add_argument(
        "--approve-harness", action="store_true",
        help="harness-integrity gate を現在の内容で承認する（ユーザー自身が判断して渡すフラグ）",
    )
    ap.add_argument("--eval-set", type=Path, default=None, help="凍結コーパスの override パス")
    args = ap.parse_args(argv)

    cases = load_corpus(args.eval_set)
    if args.limit is not None:
        cases = cases[: args.limit]

    cfg = RunConfig(
        variant=args.variant, model=args.model, reps=args.reps,
        batch_size=args.batch_size,
    )

    if not args.run:
        result = run_dry(cases, cfg)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    FLOW_DIR.mkdir(parents=True, exist_ok=True)
    state_path = FLOW_DIR / "_state.json"
    state = load_state(state_path)
    current_sha = compute_harness_sha()
    ok, new_state = check_harness_approval(state, current_sha, approve=args.approve_harness)
    if not ok:
        print(
            "[judge_eval] harness-integrity gate: runner/prompt/judge_runner の内容が"
            "承認済みハッシュと不一致、または未承認です。差分を確認したうえで"
            "--approve-harness を付けて再実行してください（承認は実行者の判断で行うこと）。",
            file=sys.stderr,
        )
        return 2
    new_state.setdefault("metrics", METRICS)
    new_state.setdefault("perf_fields", PERF_FIELDS)
    new_state["metrics_md"] = PREV_ACTION_COVERAGE_NOTE
    state_path.write_text(json.dumps(new_state, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = run_eval(cases, cfg, flow_dir=FLOW_DIR)

    variant_dir = FLOW_DIR / cfg.variant
    rows = _read_jsonl(variant_dir / "results.jsonl")
    confusion = compute_confusion_summary(rows)
    summary["confusion_summary"] = confusion

    # summary（Wilson 95% CI 込み）を _state.json にも残す（tacchi レビュー Should-5）。
    state_after = load_state(state_path)
    state_after["confusion_summary"] = confusion
    state_path.write_text(json.dumps(state_after, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
