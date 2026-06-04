#!/usr/bin/env python3
"""Constitutional Evaluation モジュール。

原則リスト × 4レイヤー（CLAUDE.md/Rules/Skills/Memory）を LLM Judge で評価し、
Constitutional Score（0.0〜1.0）を算出する。[ADR-037] により claude -p を全廃し、
レイヤー評価は emit_layer_requests（Phase A）→ SKILL のインライン採点（Phase B）→
ingest_layer_responses（Phase C）のファイルベース2相で行う。本モジュールは LLM を呼ばない。
"""
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent
_fitness_dir = Path(__file__).resolve().parent

try:
    from .config import CONSTITUTIONAL_THRESHOLDS as THRESHOLDS
except ImportError:
    THRESHOLDS = {
        "min_coverage_for_eval": 0.5,
        "llm_timeout_sec": 60,
    }

CSO_SIGNAL_WEIGHT = 0.1
SLOP_PENALTY_WEIGHT = 0.1  # slop 減点の overall スコアへの影響度


def _ensure_paths():
    paths = [
        str(_plugin_root / "scripts" / "rl"),
        str(_plugin_root / "scripts" / "lib"),
        str(_plugin_root / "scripts"),
        str(_plugin_root / "skills" / "audit" / "scripts"),
    ]
    for p in paths:
        if p not in sys.path:
            sys.path.insert(0, p)


def _load_sibling(name: str):
    """同ディレクトリのモジュール（ファイルまたはパッケージ）を importlib で安全にロードする。

    coherence は #143 で coherence/ パッケージへ分割された。`{name}.py` 固定だと
    パッケージを見つけられず FileNotFoundError → silent skip するため、environment.py
    と同じ package 対応分岐を持つ（#277）。
    """
    import importlib.util

    pkg_init = _fitness_dir / name / "__init__.py"
    if pkg_init.exists():
        # パッケージの場合: fitness_dir を sys.path に一時追加して通常 import
        _fitness_dir_str = str(_fitness_dir)
        _added = _fitness_dir_str not in sys.path
        if _added:
            sys.path.insert(0, _fitness_dir_str)
        try:
            return importlib.import_module(name)
        finally:
            if _added:
                sys.path.remove(_fitness_dir_str)
    spec = importlib.util.spec_from_file_location(f"fitness_{name}", _fitness_dir / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Layer content helpers
# ---------------------------------------------------------------------------

_LAYER_NAMES = ["claude_md", "rules", "skills", "memory"]


def _collect_layer_contents(project_dir: Path) -> Dict[str, str]:
    """各レイヤーのコンテンツを文字列として収集する。"""
    _ensure_paths()
    coherence_mod = _load_sibling("coherence")
    artifacts = coherence_mod._find_artifacts_local(project_dir)

    contents: Dict[str, str] = {}
    for layer in _LAYER_NAMES:
        paths = artifacts.get(layer, [])
        parts: List[str] = []
        for p in paths:
            try:
                text = p.read_text(encoding="utf-8")
                try:
                    label = str(p.relative_to(project_dir))
                except ValueError:
                    label = p.name
                parts.append(f"--- {label} ---\n{text}")
            except (OSError, UnicodeDecodeError):
                continue
        if parts:
            contents[layer] = "\n\n".join(parts)
    return contents


def _content_hash(content: str) -> str:
    """SHA-256 ハッシュを返す。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_path(project_dir: Path) -> Path:
    return project_dir / ".claude" / "constitutional_cache.json"


def _load_cache(project_dir: Path) -> Optional[Dict[str, Any]]:
    path = _cache_path(project_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(project_dir: Path, data: Dict[str, Any]) -> None:
    path = _cache_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Layer evaluation (Phase B response parsing)
# ---------------------------------------------------------------------------

def _build_eval_prompt(layer_name: str, layer_content: str, principles: List[Dict[str, Any]]) -> str:
    """レイヤー評価用プロンプトを構築する。"""
    principles_text = "\n".join(
        f"  {i+1}. id={p['id']}: {p['text']}"
        for i, p in enumerate(principles)
    )
    return f"""You are evaluating a Claude Code environment layer against a set of principles.

## Layer: {layer_name}

{layer_content}

## Principles to evaluate

{principles_text}

## Instructions

For each principle, evaluate how well this layer adheres to it.
Return a JSON object with the following structure (no markdown fences, raw JSON only):

{{
  "evaluations": [
    {{
      "principle_id": "<id>",
      "score": <float 0.0-1.0>,
      "rationale": "<brief explanation>",
      "violations": ["<violation description>", ...]
    }}
  ]
}}

Rules:
- score 1.0 = fully compliant, 0.0 = completely non-compliant
- If the layer content is not relevant to a principle, score 0.8 (neutral-positive)
- violations should be empty list if score >= 0.8
- Be concise in rationale (1-2 sentences)
"""


def _parse_llm_response(raw: str) -> Optional[Dict[str, Any]]:
    """LLM レスポンスから JSON をパースする。"""
    text = raw.strip()
    # マークダウンコードブロック除去
    if text.startswith("```"):
        lines = text.split("\n")
        # 先頭と末尾の ``` 行を除去
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _parse_layer_response(raw: Optional[Any]) -> Optional[Dict[str, Any]]:
    """1レイヤーの LLM レスポンスをパースする。None / 不正 / 空評価時は None。

    [ADR-037] Phase C のパーサ。llm_broker.parse_responses から呼ばれる。Phase B の書き手は
    assistant（非決定論プロデューサ）のため、JSON 文字列だけでなく parse 済み dict で来ても受ける
    （world_context._extract_world_dict と同じ寛容性。str 専用だと ingest がクラッシュする）。

    Returns:
        {"evaluations": [{"principle_id", "score", "rationale", "violations"}, ...]} or None
    """
    if not raw:
        return None
    if isinstance(raw, dict):
        parsed = raw
    elif isinstance(raw, str):
        parsed = _parse_llm_response(raw)
    else:
        return None
    if parsed is None:
        return None
    evaluations = parsed.get("evaluations", [])
    if not evaluations:
        return None
    # スコア clamp
    for ev in evaluations:
        if "score" in ev:
            try:
                ev["score"] = _clamp(float(ev["score"]))
            except (TypeError, ValueError):
                ev["score"] = 0.0
    return {"evaluations": evaluations}


# ---------------------------------------------------------------------------
# CSO signal integration
# ---------------------------------------------------------------------------

def _load_cso_signal(data_dir: Path) -> Optional[Dict[str, Any]]:
    """~/.gstack/analytics/skill-usage.jsonl から最新の cso 実行結果を取得。

    Args:
        data_dir: gstack データディレクトリ（通常 ~/.gstack）

    Returns:
        {"outcome": str, "ts": str} or None (graceful degradation)
    """
    jsonl_path = data_dir / "analytics" / "skill-usage.jsonl"
    if not jsonl_path.exists():
        return None
    try:
        latest = None
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("skill") == "cso":
                latest = {"outcome": entry.get("outcome", ""), "ts": entry.get("ts", "")}
        return latest
    except (OSError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _aggregate_constitutional(
    project_dir: Path,
    principles: List[Dict[str, Any]],
    layer_contents: Dict[str, str],
    layer_results: Dict[str, Dict[str, Any]],
    layer_hashes: Dict[str, str],
    *,
    llm_calls: int = 0,
    from_cache_count: int = 0,
    estimated_cost: float = 0.0,
) -> Dict[str, Any]:
    """layer_results を集約して Constitutional Score を算出し、cache を保存する。

    [ADR-037] compute_constitutional_score（cache-only）と ingest_layer_responses（2相）が共有する。
    LLM は呼ばない。
    """
    # principle_id → list of scores across layers
    principle_scores: Dict[str, List[float]] = {}
    # layer_name → list of scores across principles
    layer_scores_map: Dict[str, List[float]] = {}
    all_violations: List[Dict[str, Any]] = []

    for layer_name, lr in layer_results.items():
        scores_for_layer: List[float] = []
        for ev in lr["evaluations"]:
            pid = ev.get("principle_id", "unknown")
            score = ev.get("score", 0.0)
            scores_for_layer.append(score)

            if pid not in principle_scores:
                principle_scores[pid] = []
            principle_scores[pid].append(score)

            for v in ev.get("violations", []):
                all_violations.append({
                    "principle_id": pid,
                    "layer": layer_name,
                    "description": v,
                })

        if scores_for_layer:
            layer_scores_map[layer_name] = scores_for_layer

    # per_principle
    per_principle: List[Dict[str, Any]] = []
    principle_means: List[float] = []
    for p in principles:
        pid = p["id"]
        scores = principle_scores.get(pid, [])
        mean_score = sum(scores) / len(scores) if scores else 0.0
        per_principle.append({
            "id": pid,
            "text": p["text"],
            "score": round(mean_score, 4),
        })
        if scores:
            principle_means.append(mean_score)

    # per_layer
    per_layer: Dict[str, float] = {}
    for layer_name, scores in layer_scores_map.items():
        per_layer[layer_name] = round(sum(scores) / len(scores), 4) if scores else 0.0

    overall = round(sum(principle_means) / len(principle_means), 4) if principle_means else 0.0

    result = {
        "overall": overall,
        "per_principle": per_principle,
        "per_layer": per_layer,
        "violations": all_violations,
        "evaluated_layers": len(layer_results),
        "total_layers": len(layer_contents),
        "estimated_cost_usd": round(estimated_cost, 6),
        "llm_calls_count": llm_calls,
        "from_cache": from_cache_count > 0,
    }

    # --- Slop detection (deterministic, no LLM) ---
    try:
        _ensure_paths()
        import importlib.util as _ilu
        _slop_path = _plugin_root / "scripts" / "lib" / "slop_detector.py"
        _slop_spec = _ilu.spec_from_file_location("slop_detector", _slop_path)
        _slop_mod = _ilu.module_from_spec(_slop_spec)
        _slop_spec.loader.exec_module(_slop_mod)

        all_layer_text = "\n\n".join(layer_contents.values())
        slop_result = _slop_mod.detect_slop(all_layer_text)
        slop_score = slop_result.slop_score  # 1.0=良い, 0.0=悪い
        slop_hits = slop_result.hits

        for hit in slop_hits:
            all_violations.append({
                "principle_id": "anti-slop",
                "layer": "all",
                "description": f"slop pattern '{hit['pattern_id']}': {hit['snippet']!r}",
            })

        result["slop_score"] = slop_score
        result["slop_hits_count"] = len(slop_hits)

        overall = round(overall * (1 - SLOP_PENALTY_WEIGHT) + slop_score * SLOP_PENALTY_WEIGHT, 4)
        result["overall"] = overall

    except Exception as e:
        print(f"[constitutional] slop 検出スキップ: {e}", file=sys.stderr)
        result["slop_score"] = None
        result["slop_hits_count"] = 0

    # --- CSO signal integration ---
    gstack_dir = Path.home() / ".gstack"
    cso = _load_cso_signal(gstack_dir)
    if cso is not None:
        result["cso_signal"] = cso
        cso_score = 1.0 if cso.get("outcome") == "pass" else 0.0
        result["overall"] = round(
            overall * (1 - CSO_SIGNAL_WEIGHT) + cso_score * CSO_SIGNAL_WEIGHT, 4
        )

    # --- Save cache ---
    cache_data = {
        "layer_results": layer_results,
        "layer_hashes": layer_hashes,
    }
    try:
        _save_cache(project_dir, cache_data)
    except OSError:
        pass

    return result


def _coverage_gate(project_dir: Path) -> Optional[float]:
    """coverage が閾値未満なら coverage 値を、十分なら None を返す。"""
    coherence_mod = _load_sibling("coherence")
    try:
        coverage = coherence_mod.compute_coherence_score(project_dir)["coverage"]
    except Exception:
        coverage = 0.0
    if coverage < THRESHOLDS["min_coverage_for_eval"]:
        return coverage
    return None


def compute_constitutional_score(
    project_dir: Path,
    refresh: bool = False,
) -> Optional[Dict[str, Any]]:
    """Constitutional Score を算出する（LLM-free; cache 済みレイヤーのみ集約）。

    [ADR-037] claude -p を全廃。cache 命中レイヤーだけを集約する。cache 未生成/全 miss なら
    None（refresh は emit_layer_requests →（SKILL Phase B）→ ingest_layer_responses で行う）。

    Returns:
        成功時: {"overall", "per_principle", "per_layer", "violations", ...}
        スキップ時: {"overall": None, "skip_reason", "coverage_value"}
        cache 無/全 miss: None
    """
    _ensure_paths()
    project_dir = Path(project_dir)

    low_coverage = _coverage_gate(project_dir)
    if low_coverage is not None:
        return {"overall": None, "skip_reason": "low_coverage", "coverage_value": low_coverage}

    principles = _load_sibling("principles").extract_principles(project_dir)["principles"]
    if not principles:
        return None

    layer_contents = _collect_layer_contents(project_dir)
    if not layer_contents:
        return None

    cache = _load_cache(project_dir) if not refresh else None
    cached_layers = cache.get("layer_results", {}) if cache else {}
    cached_hashes = cache.get("layer_hashes", {}) if cache else {}

    # cache 命中レイヤーのみ採用（miss は LLM を呼ばずスキップ）
    layer_results: Dict[str, Dict[str, Any]] = {}
    layer_hashes: Dict[str, str] = {}
    for layer_name, content in layer_contents.items():
        content_hash = _content_hash(content)
        layer_hashes[layer_name] = content_hash
        if layer_name in cached_layers and cached_hashes.get(layer_name) == content_hash:
            layer_results[layer_name] = cached_layers[layer_name]

    if not layer_results:
        return None

    return _aggregate_constitutional(
        project_dir, principles, layer_contents, layer_results, layer_hashes,
        from_cache_count=len(layer_results),
    )


def emit_layer_requests(
    project_dir: str | Path, refresh: bool = False
) -> Dict[str, Any]:
    """Phase A: cache-miss レイヤーの評価リクエストを生成する（決定論・LLM 非依存）。

    principles が cache 由来でない場合 principles_missing=True を返す（SKILL は principles round
    を先に回す）。プロンプトには現時点で取得できる principles（seed fallback 含む）を埋め込む。

    Returns:
        {"requests": [{"id": <layer>, "prompt": str, "meta": {"content_hash"}}],
         "skipped": [<cache hit layer>...], "principles_missing": bool, "skip_reason"?: str}
    """
    _ensure_paths()
    from llm_broker import build_requests

    project_dir = Path(project_dir)

    low_coverage = _coverage_gate(project_dir)
    if low_coverage is not None:
        return {"requests": [], "skipped": [], "principles_missing": False,
                "skip_reason": "low_coverage"}

    pres = _load_sibling("principles").extract_principles(project_dir)
    principles = pres["principles"]
    principles_missing = not pres.get("from_cache", False)
    if not principles:
        return {"requests": [], "skipped": [], "principles_missing": principles_missing}

    layer_contents = _collect_layer_contents(project_dir)
    if not layer_contents:
        return {"requests": [], "skipped": [], "principles_missing": principles_missing}

    cache = _load_cache(project_dir) if not refresh else None
    cached_layers = cache.get("layer_results", {}) if cache else {}
    cached_hashes = cache.get("layer_hashes", {}) if cache else {}

    items: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for layer_name, content in layer_contents.items():
        content_hash = _content_hash(content)
        if (
            not refresh
            and layer_name in cached_layers
            and cached_hashes.get(layer_name) == content_hash
        ):
            skipped.append(layer_name)
            continue
        items.append({"id": layer_name, "_content": content, "content_hash": content_hash})

    requests = build_requests(
        items, lambda it: _build_eval_prompt(it["id"], it["_content"], principles)
    )
    for r in requests:  # 巨大なレイヤー本文を meta から落とす
        r["meta"].pop("_content", None)
    return {"requests": requests, "skipped": skipped, "principles_missing": principles_missing}


def ingest_layer_responses(
    project_dir: str | Path,
    requests: List[Dict[str, Any]],
    responses: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Phase C: レイヤー応答をパースし cache 命中分とマージして集約・保存する（決定論・LLM 非依存）。

    cache 済みレイヤー + 今回 ingest したレイヤーを合わせて集約する。評価が1つも無ければ None。
    """
    _ensure_paths()
    from llm_broker import parse_responses

    project_dir = Path(project_dir)
    principles = _load_sibling("principles").extract_principles(project_dir)["principles"]
    layer_contents = _collect_layer_contents(project_dir)
    if not layer_contents:
        return None

    cache = _load_cache(project_dir)
    cached_layers = cache.get("layer_results", {}) if cache else {}
    cached_hashes = cache.get("layer_hashes", {}) if cache else {}

    parsed_map = parse_responses(requests, responses, parser=_parse_layer_response)

    layer_results: Dict[str, Dict[str, Any]] = {}
    layer_hashes: Dict[str, str] = {}
    # まだ内容が一致する cache 済みレイヤーを引き継ぐ
    for layer_name, content in layer_contents.items():
        content_hash = _content_hash(content)
        layer_hashes[layer_name] = content_hash
        if layer_name in cached_layers and cached_hashes.get(layer_name) == content_hash:
            layer_results[layer_name] = cached_layers[layer_name]

    # 今回 ingest したレイヤーを上書き
    fresh = 0
    for req in requests:
        lid = req["id"]
        parsed = parsed_map.get(lid)
        if parsed and parsed.get("evaluations"):
            layer_results[lid] = {"evaluations": parsed["evaluations"]}
            fresh += 1

    if not layer_results:
        return None

    return _aggregate_constitutional(
        project_dir, principles, layer_contents, layer_results, layer_hashes,
        llm_calls=fresh, from_cache_count=len(layer_results) - fresh,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Constitutional Evaluation（[ADR-037] 2相）")
    parser.add_argument("project_dir", help="プロジェクトディレクトリ")
    parser.add_argument("--refresh", action="store_true", help="cache を無視（emit 時は全レイヤー再評価）")
    parser.add_argument(
        "--emit-requests", action="store_true",
        help="Phase A: cache-miss レイヤーの評価リクエスト JSON を出力",
    )
    parser.add_argument(
        "--ingest", action="store_true",
        help="Phase C: --requests と --responses をパース・集約してキャッシュ保存",
    )
    parser.add_argument("--requests", help="Phase C: emit-requests の出力 JSON ファイル")
    parser.add_argument("--responses", help="Phase C: assistant 応答 JSON ファイル")
    args = parser.parse_args()

    if args.emit_requests:
        out = emit_layer_requests(Path(args.project_dir), refresh=args.refresh)
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif args.ingest:
        with open(args.requests, encoding="utf-8") as f:
            req_payload = json.load(f)
        requests = req_payload.get("requests", req_payload)
        with open(args.responses, encoding="utf-8") as f:
            responses = json.load(f)
        result = ingest_layer_responses(Path(args.project_dir), requests, responses)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        result = compute_constitutional_score(Path(args.project_dir), refresh=args.refresh)
        print(json.dumps(result, ensure_ascii=False, indent=2))
