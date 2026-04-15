#!/usr/bin/env python3
"""philosophy-review: 会話履歴ベースの哲学原則レビュー。

Claude Code native session log を Judge LLM で評価し、philosophy カテゴリ原則の
違反例を corrections.jsonl に注入する。
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

CHARS_PER_TOKEN = 4
DEFAULT_LIMIT = 30
DEFAULT_MAX_TOKENS = 50_000
LLM_TIMEOUT_SEC = 90
MIN_INJECT_CONFIDENCE = 0.85
DEFAULT_INJECT_CONFIDENCE = 0.85


# ---------------------------------------------------------------------------
# Principles
# ---------------------------------------------------------------------------

def _load_seed_philosophy() -> List[Dict[str, Any]]:
    """principles.py の SEED_PRINCIPLES から philosophy カテゴリのみを返す。

    cache が古い（SEED 追加前に生成）場合でも SEED 経由で配布された哲学原則を
    評価対象にできるようにするためのフォールバック。
    """
    try:
        import importlib.util
        fitness_dir = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "rl" / "fitness"
        spec = importlib.util.spec_from_file_location("principles", fitness_dir / "principles.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return [p for p in mod.SEED_PRINCIPLES if p.get("category") == "philosophy"]
    except Exception:
        return []


def load_philosophy_principles(principles_path: Path) -> List[Dict[str, Any]]:
    """principles.json cache + SEED_PRINCIPLES の philosophy を id 重複除去でマージして返す。

    cache に philosophy が無くても SEED 経由の原則が使える。cache にユーザーが
    user_defined: true で追加した philosophy があれば優先される。
    """
    from_cache: List[Dict[str, Any]] = []
    if principles_path.exists():
        try:
            data = json.loads(principles_path.read_text(encoding="utf-8"))
            from_cache = [p for p in data.get("principles", []) if p.get("category") == "philosophy"]
        except (OSError, json.JSONDecodeError):
            pass

    seen_ids = {p.get("id") for p in from_cache}
    from_seed = [p for p in _load_seed_philosophy() if p.get("id") not in seen_ids]
    return from_cache + from_seed


# ---------------------------------------------------------------------------
# Session log
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def find_session_files(sessions_dir: Path, limit: int) -> List[Path]:
    """直近 N セッションファイルを mtime 降順で返す。"""
    if not sessions_dir.exists():
        return []
    files = sorted(
        sessions_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[:limit]


def _extract_text_from_message(msg: Any) -> str:
    """message.content から表示用テキストを取り出す。"""
    if isinstance(msg, str):
        return msg
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text") or block.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def extract_transcript(session_file: Path, max_tokens: int) -> str:
    """session jsonl から user/assistant メッセージを transcript に整形。

    token cap 超過時は先頭+末尾サンプリング（中略マーカーを挟む）。
    """
    blocks: List[str] = []
    try:
        for raw in session_file.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            rtype = rec.get("type")
            if rtype not in ("user", "assistant"):
                continue
            text = _extract_text_from_message(rec.get("message", {}))
            if not text.strip():
                continue
            blocks.append(f"[{rtype}]\n{text}")
    except OSError:
        return ""

    full = "\n\n".join(blocks)
    if estimate_tokens(full) <= max_tokens:
        return full

    half_budget_chars = (max_tokens * CHARS_PER_TOKEN) // 2
    head = full[:half_budget_chars]
    tail = full[-half_budget_chars:]
    return f"{head}\n\n--- TRUNCATED ---\n\n{tail}"


# ---------------------------------------------------------------------------
# Judge LLM
# ---------------------------------------------------------------------------

def _build_judge_prompt(transcript: str, principles: List[Dict[str, Any]]) -> str:
    plist = "\n".join(f"- {p['id']}: {p['text']}" for p in principles)
    return f"""You are evaluating a Claude Code session transcript against philosophy principles.

## Principles (philosophy category)

{plist}

## Transcript

{transcript}

## Instructions

For each clear violation of a principle in the transcript, output an entry.
Only report violations with strong evidence — silent compliance is the default.
Return raw JSON only (no markdown fences):

{{
  "violations": [
    {{
      "principle_id": "<id from list above>",
      "evidence": "<short quote or summary, 1-2 sentences>",
      "confidence": <float 0.0-1.0>
    }}
  ]
}}

Rules:
- Empty violations list is fine if no clear violations.
- confidence < 0.7: do not report.
- evidence must reference specific transcript content.
"""


def _call_judge_llm(prompt: str) -> Optional[str]:
    """claude haiku を呼び出す。失敗時は None。"""
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", "haiku"],
            capture_output=True,
            text=True,
            timeout=LLM_TIMEOUT_SEC,
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _parse_judge_response(raw: str) -> List[Dict[str, Any]]:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    return data.get("violations", []) if isinstance(data, dict) else []


def evaluate_session(
    transcript: str,
    principles: List[Dict[str, Any]],
    session_id: str,
) -> List[Dict[str, Any]]:
    if not transcript.strip() or not principles:
        return []
    prompt = _build_judge_prompt(transcript, principles)
    raw = _call_judge_llm(prompt)
    if raw is None:
        return []
    violations = _parse_judge_response(raw)
    for v in violations:
        v["session_id"] = session_id
    return violations


# ---------------------------------------------------------------------------
# Corrections injection
# ---------------------------------------------------------------------------

def _build_correction_entry(violation: Dict[str, Any], project_path: str) -> Dict[str, Any]:
    confidence = max(float(violation.get("confidence", 0.0)), DEFAULT_INJECT_CONFIDENCE)
    pid = violation.get("principle_id", "unknown")
    evidence = violation.get("evidence", "")
    return {
        "correction_type": "philosophy-violation",
        "matched_patterns": [pid],
        "message": f"哲学原則 '{pid}' の違反が検出されました: {evidence}",
        "last_skill": None,
        "confidence": confidence,
        "decay_days": 60,
        "sentiment": "negative",
        "routing_hint": "correction",
        "guardrail": False,
        "reflect_status": "pending",
        "extracted_learning": "",
        "project_path": project_path,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "session_id": violation.get("session_id", str(uuid.uuid4())),
        "source": "philosophy-review",
    }


def inject_corrections(
    violations: List[Dict[str, Any]],
    corrections_path: Path,
    project_path: Optional[str] = None,
) -> int:
    """confidence >= MIN_INJECT_CONFIDENCE の違反のみ corrections.jsonl に append。

    Returns 注入件数。
    """
    if project_path is None:
        project_path = str(Path.cwd())
    corrections_path.parent.mkdir(parents=True, exist_ok=True)
    if not corrections_path.exists():
        corrections_path.touch()
    written = 0
    with corrections_path.open("a", encoding="utf-8") as f:
        for v in violations:
            if float(v.get("confidence", 0.0)) < MIN_INJECT_CONFIDENCE:
                continue
            entry = _build_correction_entry(v, project_path)
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            written += 1
    return written


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _slug_from_cwd() -> str:
    """Claude Code の projects ディレクトリ slug を cwd から導出する。"""
    return str(Path.cwd()).replace("/", "-")


def _default_sessions_dir() -> Path:
    return Path.home() / ".claude" / "projects" / _slug_from_cwd()


def _default_principles_path() -> Path:
    return Path.cwd() / ".claude" / "principles.json"


def _default_corrections_path() -> Path:
    base = os.environ.get("CLAUDE_PLUGIN_DATA")
    if base:
        return Path(base) / "corrections.jsonl"
    return Path.home() / ".claude" / "rl-anything" / "corrections.jsonl"


def run(
    principles_path: Path,
    sessions_dir: Path,
    corrections_path: Path,
    limit: int,
    max_tokens: int,
    dry_run: bool,
) -> Dict[str, Any]:
    principles = load_philosophy_principles(principles_path)
    if not principles:
        return {
            "status": "no-philosophy-principles",
            "message": f"category=philosophy の原則が {principles_path} にありません",
        }
    sessions = find_session_files(sessions_dir, limit)
    if not sessions:
        return {
            "status": "no-sessions",
            "message": f"セッションログが {sessions_dir} に見つかりません",
        }

    all_violations: List[Dict[str, Any]] = []
    details: List[Dict[str, Any]] = []
    for sess in sessions:
        transcript = extract_transcript(sess, max_tokens)
        violations = evaluate_session(transcript, principles, sess.stem)
        all_violations.extend(violations)
        for v in violations:
            details.append(
                {
                    "session_id": v.get("session_id"),
                    "principle_id": v.get("principle_id"),
                    "evidence": v.get("evidence"),
                    "confidence": v.get("confidence"),
                }
            )

    injected = 0 if dry_run else inject_corrections(all_violations, corrections_path)

    return {
        "status": "ok",
        "sessions_evaluated": len(sessions),
        "violations_found": len(all_violations),
        "violations_injected": injected,
        "dry_run": dry_run,
        "details": details,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="philosophy-review: Judge LLM で session log の哲学原則違反を抽出"
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="評価対象セッション数")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS, help="1セッションの token cap")
    parser.add_argument("--dry-run", action="store_true", help="corrections.jsonl 注入をスキップ")
    parser.add_argument("--principles", type=Path, default=None, help="principles.json パス（省略時 .claude/principles.json）")
    parser.add_argument("--sessions-dir", type=Path, default=None, help="セッション jsonl ディレクトリ")
    parser.add_argument("--corrections", type=Path, default=None, help="corrections.jsonl 出力先")
    args = parser.parse_args(argv)

    result = run(
        principles_path=args.principles or _default_principles_path(),
        sessions_dir=args.sessions_dir or _default_sessions_dir(),
        corrections_path=args.corrections or _default_corrections_path(),
        limit=args.limit,
        max_tokens=args.max_tokens,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
