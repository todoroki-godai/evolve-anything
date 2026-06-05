"""remediation LLM ブローカ — ファイルベース2相パターン ([ADR-037] Phase 1d-ii)。

fixers_rules / fixers_quality の3つの claude -p サイトを emit/ingest の2相化する。
Python から LLM 呼び出しを完全に追い出し、no-llm-in-tests と完全整合（mock 不要）。

3つの変換対象:
  1. 非 rule ファイル圧縮 (fix_line_limit_violation の非 rule パス)
     emit_compression_request / ingest_compression
  2. rule ファイル分離 (_fix_rule_by_separation)
     emit_separation_request / ingest_separation
  3. スキル分割提案テキスト生成 (fix_split_candidate)
     emit_split_request / ingest_split

決定論フォールバック保証:
  - batch 経路（evolve.py が呼ぶ fix 関数）は pause して Task を呼べない。
  - ingest が失敗（行数超過/空/missing）しても proposable 降格か決定論 fallback で完走する。
  - LLM 品質は SKILL の2相（emit→assistant インライン→ingest）で回復する。
"""
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# llm_broker を import（scripts/lib が sys.path 上にある前提）
_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from llm_broker import build_requests, parse_responses, passthrough


def _count_lines(s: str) -> int:
    """現行実装と同じ行数カウント式。"""
    return s.count("\n") + (1 if s and not s.endswith("\n") else 0)


def _strip_fence(text: str) -> str:
    """code fence（```）で囲まれているなら中身を取り出す。"""
    if text.startswith("```") and text.endswith("```"):
        lines = text.split("\n")
        return "\n".join(lines[1:-1])
    return text


# ---------------------------------------------------------------------------
# 1. 非 rule ファイル圧縮
# ---------------------------------------------------------------------------


def emit_compression_request(
    issue: Dict[str, Any],
    original_content: str,
    limit: int,
) -> Dict[str, Any]:
    """Phase A: 非 rule ファイル圧縮リクエストを生成する（決定論・LLM 非依存・IO なし）。

    Returns:
        {"requests": [{"id": "compress", "prompt": str, "meta": {...}}]}
    """
    prompt = (
        f"以下のファイル内容を {limit} 行以内に圧縮してください。"
        f"意味と構造を保ちつつ、冗長な表現を削除して簡潔にしてください。"
        f"出力は圧縮後のファイル内容のみ（説明不要）。\n\n"
        f"```\n{original_content}```"
    )
    items = [{"id": "compress", "limit": limit}]
    requests = build_requests(items, lambda _: prompt)
    return {"requests": requests}


def ingest_compression(
    issue: Dict[str, Any],
    path: Path,
    original_content: str,
    limit: int,
    requests: List[Dict[str, Any]],
    responses: Dict[str, Any],
) -> Dict[str, Any]:
    """Phase C: 圧縮結果を回収し path に書き込む（決定論・LLM 非依存。書き込みは本関数のみ）。

    失敗（行数超過/空/missing）時: issue["category"]="proposable", fixed=False, error 付き。

    Returns:
        fix 関数と同形の result dict。
    """
    if not requests:
        issue["category"] = "proposable"
        return {
            "issue": issue, "original_content": original_content,
            "fixed": False, "error": "no_requests",
        }

    parsed = parse_responses(requests, responses, parser=passthrough)
    raw = parsed.get("compress")

    if not raw:
        issue["category"] = "proposable"
        return {
            "issue": issue, "original_content": original_content,
            "fixed": False, "error": "llm_response_missing",
        }

    compressed = _strip_fence(raw.strip())

    compressed_lines = _count_lines(compressed)
    if compressed_lines > limit:
        issue["category"] = "proposable"
        return {
            "issue": issue, "original_content": original_content,
            "fixed": False, "error": "compression_insufficient",
        }

    if not compressed.endswith("\n"):
        compressed += "\n"

    try:
        path.write_text(compressed, encoding="utf-8")
    except OSError as e:
        issue["category"] = "proposable"
        return {
            "issue": issue, "original_content": original_content,
            "fixed": False, "error": str(e),
        }

    return {
        "issue": issue, "original_content": original_content,
        "fixed": True, "error": None,
    }


# ---------------------------------------------------------------------------
# 2. rule ファイル分離
# ---------------------------------------------------------------------------


def emit_separation_request(
    issue: Dict[str, Any],
    path: Path,
    original_content: str,
    limit: int,
) -> Dict[str, Any]:
    """Phase A: rule ファイル分離リクエストを生成する（決定論・LLM 非依存・IO なし）。

    内部で line_limit.suggest_separation を呼ぶ。
    proposal が None なら {"requests": []}（非適用）。

    Returns:
        {"requests": [{"id": "separate", "prompt": str,
                        "meta": {"reference_path": str}}, ...]}
        or {"requests": []} if not applicable.
    """
    try:
        _lib_dir_local = Path(__file__).resolve().parent.parent
        if str(_lib_dir_local) not in sys.path:
            sys.path.insert(0, str(_lib_dir_local))
        from line_limit import suggest_separation
        proposal = suggest_separation(str(path), original_content)
    except Exception:
        return {"requests": []}

    if not proposal:
        return {"requests": []}

    ref_path_str = proposal.reference_path
    prompt = (
        f"以下の rule ファイルの内容を {limit} 行以内の要約+参照リンクに書き換えてください。\n"
        f"詳細は別ファイルに分離されるので、rule には核心の1行ルールと参照リンクのみ残してください。\n"
        f"参照リンク: `{ref_path_str}`\n"
        f"出力は書き換え後の rule 内容のみ（説明不要）。\n\n"
        f"```\n{original_content}```"
    )
    items = [{"id": "separate", "reference_path": ref_path_str}]
    requests = build_requests(items, lambda _: prompt)
    return {"requests": requests}


def ingest_separation(
    issue: Dict[str, Any],
    path: Path,
    original_content: str,
    limit: int,
    requests: List[Dict[str, Any]],
    responses: Dict[str, Any],
) -> Dict[str, Any]:
    """Phase C: 分離結果を回収し ref_path に原文、path に要約を書き込む。

    requests 空（非適用）時: error="separation_not_applicable", fixed=False。
    失敗（行数超過/空/missing）時: proposable 降格, fixed=False。

    Returns:
        fix 関数と同形の result dict（成功時: "separation": {...} を含む）。
    """
    if not requests:
        return {
            "issue": issue, "original_content": original_content,
            "fixed": False, "error": "separation_not_applicable",
        }

    ref_path_str: Optional[str] = None
    for req in requests:
        ref_path_str = req.get("meta", {}).get("reference_path")
        if ref_path_str:
            break

    if not ref_path_str:
        issue["category"] = "proposable"
        return {
            "issue": issue, "original_content": original_content,
            "fixed": False, "error": "reference_path_missing",
        }

    parsed = parse_responses(requests, responses, parser=passthrough)
    raw = parsed.get("separate")

    if not raw:
        issue["category"] = "proposable"
        return {
            "issue": issue, "original_content": original_content,
            "fixed": False, "error": "llm_response_missing",
        }

    summary = _strip_fence(raw.strip())

    summary_lines = _count_lines(summary)
    if summary_lines > limit:
        issue["category"] = "proposable"
        return {
            "issue": issue, "original_content": original_content,
            "fixed": False, "error": "separation_summary_too_long",
        }

    if not summary.endswith("\n"):
        summary += "\n"

    ref_path = Path(ref_path_str)
    try:
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        ref_path.write_text(original_content, encoding="utf-8")
        path.write_text(summary, encoding="utf-8")
    except OSError as e:
        issue["category"] = "proposable"
        return {
            "issue": issue, "original_content": original_content,
            "fixed": False, "error": str(e),
        }

    return {
        "issue": issue, "original_content": original_content,
        "fixed": True, "error": None,
        "separation": {
            "reference_path": str(ref_path),
            "summary": summary,
        },
    }


# ---------------------------------------------------------------------------
# 3. スキル分割提案テキスト生成（ファイル書き込みなし）
# ---------------------------------------------------------------------------


def emit_split_request(
    issue: Dict[str, Any],
    content: str,
) -> Dict[str, Any]:
    """Phase A: スキル分割提案リクエストを生成する（決定論・LLM 非依存・IO なし）。

    Returns:
        {"requests": [{"id": "split", "prompt": str, "meta": {...}}]}
    """
    detail = issue.get("detail", {})
    line_count = detail.get("line_count", len(content.splitlines()))
    threshold = detail.get("threshold", 300)

    prompt = (
        f"以下のスキル SKILL.md ({line_count}行、閾値{threshold}行) を分析し、"
        f"references/ に切り出すべきセクションを特定してください。\n"
        f"出力形式:\n"
        f"- 分割先ファイル名と各ファイルの概要\n"
        f"- 推定削減行数\n"
        f"- SKILL.md に残す内容の概要\n\n"
        f"```\n{content[:3000]}```"
    )
    items = [{"id": "split", "line_count": line_count, "threshold": threshold}]
    requests = build_requests(items, lambda _: prompt)
    return {"requests": requests}


def ingest_split(
    issue: Dict[str, Any],
    requests: List[Dict[str, Any]],
    responses: Dict[str, Any],
) -> str:
    """Phase C: 分割提案テキストを回収する（ファイル書き込みなし）。

    空/missing なら決定論フォールバック文（現コードの fallback と同一）を返す。

    Returns:
        proposal_text (str)
    """
    detail = issue.get("detail", {})
    skill_name = detail.get("skill_name", "unknown")
    line_count = detail.get("line_count", 0)
    threshold = detail.get("threshold", 300)

    fallback = (
        f"スキル「{skill_name}」({line_count}行) の分割を検討してください。"
        f"references/ にセクションを切り出し、SKILL.md を {threshold}行以下に削減することを推奨します。"
    )

    if not requests:
        return fallback

    parsed = parse_responses(requests, responses, parser=passthrough)
    raw = parsed.get("split")

    if not raw:
        return fallback

    return raw.strip() if raw.strip() else fallback
