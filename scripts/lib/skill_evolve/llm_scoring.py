"""LLM 2軸スコアリング (external_dependency / judgment_complexity)。

Phase 8 / Slice 2 で `skill_evolve.py` から切り出し。
[ADR-037] Phase 1c: judgment_complexity の claude -p をファイルベース2相へ移行。
`compute_llm_scores` は LLM-free（cache-read + 決定論フォールバック）になり、
LLM 採点は `emit_judgment_requests`（Phase A）→ assistant inline（Phase B）→
`ingest_judgment_scores`（Phase C）で後追い更新する。external_dependency は
元々静的解析なので常に確定保存し、judgment は source フラグ（"static"|"llm"）で
refresh 対象を区別する。
"""
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# judgment_complexity の ask_user 軸の重み（#354 review fix）。
# AskUserQuestion はユーザーへの判断委譲の最強シグナルなので steps/branches より重く扱う。
ASK_USER_WEIGHT = 2

# steps 軸の上限（#354 follow-up）。番号付きリストが長いだけの線形チェックリスト
# （agent-brushup/spec-keeper 等、steps 20-26）が complexity=3 に張り付く問題への対処。
# 手順が ~5 を超えたら、それ以上の項目数は「判断の複雑さ」でなく「文書の長さ」を表すため
# steps の寄与を頭打ちにする。これにより steps 単独では 3（>=8）に到達できなくなり、
# 高複雑度の判定は branches / ask_user（実際の分岐・判断委譲）が駆動する。
# 実 SKILL.md 21件の分布が {1:4,2:8,3:9} に正規化される値（cap 4-6 で安定）。
STEPS_SIGNAL_CAP = 5

# 外部依存キーワード（静的解析用）
_EXTERNAL_DEPENDENCY_KEYWORDS = [
    r"\bAPI\b", r"\baws\b", r"\bs3\b", r"\blambda\b", r"\bcdk\b",
    r"\bcloudformation\b", r"\bdocker\b", r"\bkubernetes\b", r"\bk8s\b",
    r"\bhttp[s]?\b", r"\bfetch\b", r"\bcurl\b", r"\bwebsearch\b",
    r"\bwebfetch\b", r"\bmcp\b", r"\bslack\b", r"\bgithub\b",
    r"\bdeploy\b", r"\bremote\b", r"\bcloud\b", r"\bsns\b",
    r"\bsqs\b", r"\bdynamodb\b", r"\bbedrock\b",
]


def _count_external_keywords(content: str) -> int:
    """外部依存キーワードの出現数を数える。"""
    count = 0
    for pattern in _EXTERNAL_DEPENDENCY_KEYWORDS:
        count += len(re.findall(pattern, content, re.IGNORECASE))
    return count


def _score_external_dependency(content: str) -> int:
    """外部依存度スコア (1-3)。静的解析。"""
    count = _count_external_keywords(content)
    if count >= 10:
        return 3  # 外部依存多数
    if count >= 3:
        return 2  # 一部外部連携
    return 1  # ローカル完結


def _score_judgment_complexity_static(content: str) -> int:
    """判断複雑さスコア (1-3) の決定論近似。3軸の静的指標で推定 (#354)。

    軸:
    - branches: 条件分岐語（if/else/elif/when/unless/場合/条件/判断）の出現数
    - steps:    番号付きリスト手順数（"1. " 等の行頭番号）。markdown 見出し番号
                （"### 1." 等）は文書構造でありステップ数を過大評価するため除外する。
                さらに STEPS_SIGNAL_CAP で頭打ちにする（長い線形チェックリスト対策）。
    - ask_user: AskUserQuestion の出現数 × ASK_USER_WEIGHT。ユーザーへの判断委譲は
                判断複雑さの最強シグナルなので重み付けする。

    低/中/高 の閾値:
    - signal_total < 3   → 1（決定論的）
    - 3 <= signal_total < 8 → 2（数箇所の条件分岐）
    - signal_total >= 8  → 3（判断・ヒューリスティクスが多数）

    steps は STEPS_SIGNAL_CAP で頭打ちのため steps 単独では 3 に到達できない。
    高複雑度の判定は branches / ask_user（実際の分岐・判断委譲）が駆動する。

    決定論・LLM 非依存。LLM 品質が必要なら emit_judgment_requests を使う。

    注意: branches の `場合/条件/判断` は連続日本語中では `\b` 境界が立たず
    ほぼマッチしない（散文の英語分岐語も稀）。判断委譲の主信号は ask_user。
    """
    branches = len(re.findall(
        r"\b(if|else|elif|when|unless|場合|条件|判断)\b", content, re.IGNORECASE
    ))
    # 番号付きリスト手順のみ数える（行頭が数字）。"### 1." 等の見出し番号は
    # 文書構造でステップ数を過大評価するため除外する（#354 review fix）。
    # さらに STEPS_SIGNAL_CAP で頭打ちにし、長いだけのチェックリストの張り付きを防ぐ。
    steps = min(len(re.findall(r"(?m)^\d+\.", content)), STEPS_SIGNAL_CAP)
    # AskUserQuestion = ユーザーへの判断委譲の最強シグナルなので重み付けする（#354 review fix）。
    ask_user = len(re.findall(r"AskUserQuestion", content, re.IGNORECASE)) * ASK_USER_WEIGHT

    signal_total = branches + steps + ask_user
    if signal_total >= 8:
        return 3
    if signal_total >= 3:
        return 2
    return 1


def build_judgment_prompt(skill_name: str, content: str) -> str:
    """判断複雑さ採点の Phase B プロンプトを生成する（決定論）。"""
    return (
        f"以下のスキル定義の「判断の複雑さ」を1-3で評価してください。\n"
        f"1 = 決定論的（手順が固定、分岐なし）\n"
        f"2 = 数箇所の条件分岐あり\n"
        f"3 = 判断・ヒューリスティクスが多数\n\n"
        f"スキル名: {skill_name}\n"
        f"内容（先頭2000文字）:\n```\n{content[:2000]}\n```\n\n"
        f"数字のみ（1, 2, 3のいずれか）で回答してください。"
    )


def _parse_judgment_response(raw: Optional[Any]) -> Optional[int]:
    """Phase B 応答から判断複雑さ 1-3 を抽出する（[ADR-037] Phase C のパーサ）。

    Phase B の書き手は assistant（非決定論プロデューサ）なので、JSON 文字列だけでなく
    既に parse 済みの int / dict も寛容に受ける（信頼境界）。抽出不能なら None。
    bool は数値扱いしない（True/False を 1/0 に化けさせないため）。
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        score = raw
    elif isinstance(raw, float):
        score = int(raw)
    elif isinstance(raw, dict):
        return _parse_judgment_response(
            raw.get("judgment_complexity", raw.get("score"))
        )
    elif isinstance(raw, str):
        match = re.search(r"[1-3]", raw)
        if not match:
            return None
        score = int(match.group(0))
    else:
        return None
    return score if score in (1, 2, 3) else None


def compute_llm_scores(
    skill_name: str,
    skill_dir: Path,
) -> Dict[str, Any]:
    """LLM 2軸のスコアを計算する（キャッシュ付き・LLM-free）。

    cache-hit（hash 一致）はキャッシュ値を返す。cache-miss は external を静的算出し、
    judgment は決定論フォールバック（`judgment_source="static"`）で確定保存する。
    LLM 品質の judgment は `emit_judgment_requests`→`ingest_judgment_scores` の
    2相が後追いで上書きする（[ADR-037] Phase 1c）。

    Returns:
        {"external_dependency": int, "judgment_complexity": int,
         "cached": bool, "judgment_source": "static"|"llm"}
    """
    # キャッシュヘルパは __init__.py に残存。
    # mock.patch("skill_evolve.CACHE_FILE", ...) 互換のため関数内 lazy import。
    from . import _file_hash, _load_cache, _save_cache

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return {
            "external_dependency": 1,
            "judgment_complexity": 1,
            "cached": False,
            "judgment_source": "static",
        }

    content = skill_md.read_text(encoding="utf-8")
    current_hash = _file_hash(skill_md)

    cache = _load_cache()
    cached = cache.get(skill_name, {})

    if cached.get("hash") == current_hash:
        return {
            "external_dependency": cached["external_dependency"],
            "judgment_complexity": cached["judgment_complexity"],
            "cached": True,
            # 旧キャッシュ（フラグ無し）は "static" 扱い → 次の refresh で LLM 値に昇格
            "judgment_source": cached.get("judgment_source", "static"),
        }

    # cache-miss: LLM-free で決定論算出（external は静的、judgment はフォールバック）
    ext_score = _score_external_dependency(content)
    judge_score = _score_judgment_complexity_static(content)

    cache[skill_name] = {
        "hash": current_hash,
        "external_dependency": ext_score,
        "judgment_complexity": judge_score,
        "judgment_source": "static",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_cache(cache)

    return {
        "external_dependency": ext_score,
        "judgment_complexity": judge_score,
        "cached": False,
        "judgment_source": "static",
    }


def emit_judgment_requests(
    project_dir: Path,
    skill_dirs: List[Path],
    refresh: bool = False,
) -> Dict[str, Any]:
    """Phase A: judgment_complexity を LLM 採点すべきスキルの request を生成する。

    refresh=False: cached judgment が LLM 由来かつ hash 一致のスキルは除外（static / 欠落 /
    hash 不一致のみ emit）。refresh=True: SKILL.md を持つ全スキルを emit。

    external_dependency は静的確定値を meta に同梱し、ingest が LLM 採点漏れの
    スキルでもキャッシュ整合を取れるようにする。

    Returns:
        {"requests": [{"id": skill_name, "prompt": str,
                       "meta": {"hash": str, "external_dependency": int}}]}
    """
    from . import _file_hash, _load_cache
    from llm_broker import build_requests

    cache = _load_cache()
    items: List[Dict[str, Any]] = []
    for skill_dir in skill_dirs:
        skill_dir = Path(skill_dir)
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        skill_name = skill_dir.name
        current_hash = _file_hash(skill_md)
        cached = cache.get(skill_name, {})
        is_fresh_llm = (
            cached.get("hash") == current_hash
            and cached.get("judgment_source") == "llm"
        )
        if not refresh and is_fresh_llm:
            continue
        content = skill_md.read_text(encoding="utf-8")
        items.append({
            "id": skill_name,
            "_content": content,
            "hash": current_hash,
            "external_dependency": _score_external_dependency(content),
        })

    requests = build_requests(
        items, lambda it: build_judgment_prompt(it["id"], it["_content"])
    )
    for r in requests:
        r["meta"].pop("_content", None)
    return {"requests": requests}


def ingest_judgment_scores(
    project_dir: Path,
    requests: List[Dict[str, Any]],
    responses: Dict[str, Any],
) -> Dict[str, int]:
    """Phase C: Phase B 応答を回収し judgment_complexity をキャッシュ更新する。

    requests を単一ソースに全 id を走査（llm_broker.parse_responses）。抽出不能（None）の
    スキルは static のまま据え置き、上書きしない。LLM 採点できたものは
    `judgment_source="llm"` で確定保存する。

    Returns:
        {skill_name: judgment_complexity}（LLM 採点に成功したスキルのみ）
    """
    from . import _load_cache, _save_cache
    from llm_broker import parse_responses

    parsed = parse_responses(requests, responses, parser=_parse_judgment_response)
    cache = _load_cache()
    result: Dict[str, int] = {}
    for req in requests:
        skill_name = req["id"]
        score = parsed.get(skill_name)
        if score is None:
            continue  # 抽出不能 → static のまま据え置き
        meta = req.get("meta", {})
        entry = cache.setdefault(skill_name, {})
        entry["judgment_complexity"] = score
        entry["judgment_source"] = "llm"
        if meta.get("hash"):
            entry["hash"] = meta["hash"]
        # external_dependency が未保存なら emit が同梱した静的値で補完
        if "external_dependency" not in entry:
            entry["external_dependency"] = meta.get("external_dependency", 1)
        entry["updated_at"] = datetime.now(timezone.utc).isoformat()
        result[skill_name] = score

    if result:
        _save_cache(cache)
    return result
