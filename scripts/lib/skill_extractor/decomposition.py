"""decomposition — 軌跡を Workflow-to-Skill の4軸へ分解する。

Workflow-to-Skill (arXiv 2606.06893) は、エージェントのワークフローを
``routing`` / ``workflow`` / ``semantics`` / ``attachments`` の4要素へ分解して
再利用可能なスキルを生成する。本モジュールは TrajectoryRecord 群から、その4軸を
決定論的に導く（LLM 非依存）。

各軸の意味と、軌跡から取れる近似:

- ``routing``     : いつ/どんな文脈で発火するか
                    → user_prompt の頻出キーワード + 代表プロンプト
- ``workflow``    : どう実行されるか（手順そのものは軌跡に残らないため実行プロファイルで近似）
                    → 呼び出し回数 + outcome 分布
- ``semantics``   : 何をするか
                    → スキル名（namespace / base_name）
- ``attachments`` : どの文脈に anchor されているか（≒ 必要リソースの広がり）
                    → distinct session 数。単一セッション由来なら session_bound=True
                      （= 一過性バーストで reuse 証拠が弱い）。projects は cross-project
                      な直接 API 利用のために残置（wired discover は単一 PJ scope なので
                      projects 自体は弁別しない）

5軸目（#27）:

- ``failure_analysis`` : 失敗の罠（どの文脈で失敗したか）
                    → outcome=="failure" の record 数 / 率 / 代表トリガー。
                    failure producer（trajectory_sampler の未回復エラー判定）を
                    配線したことで活性化した軸。成功基準そのものは workflow.outcomes
                    が担い、こちらは「失敗がどの文脈に集中するか」を surface する。

Issue #381, #27
"""
from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set

from skill_extractor.trajectory_sampler import TrajectoryRecord

# ── 定数 ──────────────────────────────────────────────────

ROUTING_KEYWORD_LIMIT = 5
"""routing.trigger_keywords に残す頻出語の最大数。"""

SAMPLE_TRIGGER_LIMIT = 3
"""routing.sample_triggers に残す代表プロンプトの最大数。"""

CORPUS_DF_MIN_SKILLS = 5
"""corpus document-frequency 減衰を適用する最小スキル数。

少数コーパスでは「全スキルに出る = 遍在語」の判定が不安定（content 語まで
巻き込む）ため、これ未満では DF 減衰しない（#387）。"""

CORPUS_DF_RATIO = 0.8
"""遍在語とみなす document-frequency 比率の閾値。

スキル総数の本比率以上に出現する token は「発火文脈を弁別しない遍在語」
（例: 環境固有のツール名 claude/gstack）とみなし trigger から外す。
content 語（review/spec 等は一部スキルにしか出ない）を巻き込まないよう
高め（0.8）に置く。実 PJ コーパスでドッグフードして調整した（#387）。"""

# agent_team.py と同じトークン規則（英数字 + ひらがな/カタカナ/漢字の連続）
_TOKEN_RE = re.compile(r"[a-z0-9ぁ-んァ-ヶ一-龠]+", re.IGNORECASE)

# 環境非依存の普遍語のみを static stopword に置く。
# 英語の機能語（代名詞/接続詞/前置詞/助動詞/限定詞）+ 日本語の汎用語。
# 環境固有の遍在語（ツール名等）はここに入れず corpus DF で落とす
# （allowlist はモグラ叩き＝learning_detector_fp_context_not_allowlist）。
_STOPWORDS = {
    # 冠詞・限定詞・指示詞
    "use", "this", "the", "to", "a", "an", "for", "of", "and", "or", "is",
    "are", "on", "in", "with", "your", "you", "that", "it", "as", "by", "be",
    "please", "can", "do", "make", "want", "need",
    "these", "those", "there", "here", "all", "any", "some", "each", "every",
    "both", "such", "which", "who", "whom", "whose",
    # 接続詞・関係詞
    "if", "but", "so", "then", "else", "because", "when", "while", "though",
    "although", "than", "whether", "nor", "yet",
    # 前置詞・副詞
    "from", "at", "into", "out", "up", "down", "off", "over", "under",
    "about", "after", "before", "again", "now", "only", "very", "just",
    "also", "too", "more", "most", "not", "no", "how", "what", "why", "where",
    "through", "between", "during", "without", "within",
    # 代名詞
    "i", "me", "my", "we", "us", "our", "they", "them", "their", "he", "she",
    "his", "her", "him", "its",
    # 助動詞・be 動詞・have 系
    "will", "would", "should", "could", "shall", "may", "might", "must",
    "has", "have", "had", "was", "were", "been", "being", "am", "did", "does",
    "get", "got",
    # 日本語の汎用語
    "する", "して", "した", "こと", "ため", "もの", "など", "場合", "とき",
    "ください", "ほしい", "たい", "やって", "お願い",
    "いる", "ある", "です", "ます", "から", "まで", "より", "だけ", "でも",
    "この", "その", "あの", "それ", "これ", "ように", "よう",
}

# ファイル名由来の拡張子 token（"spec.md" → "spec","md" と割れるため "md" 単独で出る）。
# 環境非依存なので static に置く。
_EXTENSIONS = {
    "md", "py", "js", "ts", "tsx", "jsx", "txt", "json", "yaml", "yml",
    "toml", "sh", "bash", "zsh", "rs", "go", "java", "rb", "css", "scss",
    "html", "xml", "csv", "tsv", "ini", "cfg", "conf", "lock", "log",
    "sql", "env", "gitignore", "dockerfile",
}

# trigger 候補から決定論で除外する基本集合（static、環境非依存）。
_BASE_EXCLUDE = _STOPWORDS | _EXTENSIONS


# ── 公開関数 ──────────────────────────────────────────────


def decompose_candidate(
    records: List[TrajectoryRecord],
    corpus_stopwords: Set[str] | None = None,
) -> Dict[str, Any]:
    """TrajectoryRecord 群を Workflow-to-Skill の4軸へ分解する。

    Args:
        records: 同一スキルの TrajectoryRecord リスト。空でも4軸の骨格は返す。
        corpus_stopwords: corpus 全体の document-frequency から導いた遍在語の集合
            （環境固有のツール名など）。trigger_keywords から追加で除外する。
            None なら static stopword のみ（単独呼び出し時の後方互換）。

    Returns:
        ``{"routing": {...}, "workflow": {...}, "semantics": {...},
        "attachments": {...}, "failure_analysis": {...}}`` の dict。
    """
    return {
        "routing": _routing(records, corpus_stopwords),
        "workflow": _workflow(records),
        "semantics": _semantics(records),
        "attachments": _attachments(records),
        "failure_analysis": _failure_analysis(records),
    }


def _iter_prompt_tokens(text: str) -> Iterable[str]:
    """1 プロンプトを正規化トークン列へ。static 除外（stopword/拡張子/1文字）済み。"""
    for tok in _TOKEN_RE.findall(text.lower()):
        if len(tok) > 1 and tok not in _BASE_EXCLUDE:
            yield tok


def corpus_frequent_tokens(
    prompts_by_skill: Mapping[str, Sequence[str]],
    *,
    min_skills: int = CORPUS_DF_MIN_SKILLS,
    df_ratio: float = CORPUS_DF_RATIO,
) -> Set[str]:
    """corpus 全体で「ほぼ全スキルに出る = 弁別しない遍在語」を返す。

    各スキルの distinct token を集め、token の document-frequency（出現スキル数）が
    ``ceil(df_ratio * スキル数)`` 以上のものを遍在語として返す。環境固有のツール名
    （claude/gstack 等）を allowlist せずに決定論で落とすための DF 減衰（#387）。

    Args:
        prompts_by_skill: ``{skill_name: [user_prompt, ...]}``。
        min_skills: これ未満のスキル数では空集合を返す（少数コーパスの過剰除外防止）。
        df_ratio: 遍在語とみなす document-frequency 比率の閾値。

    Returns:
        遍在語 token の集合（static stopword/拡張子は事前に除外済み）。
    """
    skills = [s for s in prompts_by_skill]
    num_skills = len(skills)
    if num_skills < min_skills:
        return set()

    df: Counter = Counter()
    for skill in skills:
        seen: Set[str] = set()
        for prompt in prompts_by_skill[skill]:
            for tok in _iter_prompt_tokens(prompt):
                seen.add(tok)
        for tok in seen:
            df[tok] += 1

    threshold = math.ceil(df_ratio * num_skills)
    return {tok for tok, count in df.items() if count >= threshold}


# ── 各軸 ──────────────────────────────────────────────────


def _routing(
    records: List[TrajectoryRecord],
    corpus_stopwords: Set[str] | None = None,
) -> Dict[str, Any]:
    """いつ/どんな文脈で発火するか（trigger）。"""
    prompts = [r.user_prompt.strip() for r in records if r.user_prompt.strip()]
    corpus_stop = corpus_stopwords or set()

    counter: Counter = Counter()
    for p in prompts:
        for tok in _iter_prompt_tokens(p):
            if tok not in corpus_stop:
                counter[tok] += 1

    trigger_keywords = [w for w, _ in counter.most_common(ROUTING_KEYWORD_LIMIT)]

    sample_triggers: List[str] = []
    seen: set = set()
    for p in prompts:
        if p not in seen:
            sample_triggers.append(p)
            seen.add(p)
        if len(sample_triggers) >= SAMPLE_TRIGGER_LIMIT:
            break

    return {
        "trigger_keywords": trigger_keywords,
        "sample_triggers": sample_triggers,
    }


def _workflow(records: List[TrajectoryRecord]) -> Dict[str, Any]:
    """どう実行されるか。手順は軌跡に残らないため実行プロファイルで近似する。"""
    outcomes = {"success": 0, "failure": 0, "unknown": 0}
    for r in records:
        key = r.outcome if r.outcome in outcomes else "unknown"
        outcomes[key] += 1
    return {
        "invocations": len(records),
        "outcomes": outcomes,
    }


def _semantics(records: List[TrajectoryRecord]) -> Dict[str, Any]:
    """何をするか（スキル identity）。"""
    skill_name = records[0].skill_name if records else ""
    if ":" in skill_name:
        namespace, base_name = skill_name.split(":", 1)
    else:
        namespace, base_name = None, skill_name
    return {
        "base_name": base_name,
        "namespace": namespace,
    }


def _attachments(records: List[TrajectoryRecord]) -> Dict[str, Any]:
    """どの文脈に anchor されているか（≒ 必要リソースの広がり）。

    Workflow-to-Skill の attachments（必要リソース）は軌跡にファイル単位では残らない。
    代わりに「スキルが何件の distinct セッションにまたがって発火したか」を anchor の
    広がりとして測る。実 discover の採掘は単一 PJ scope（`_project_transcript_dir`、
    cross-PJ noise 防止）のため ``projects`` は弁別しないが、``session_count`` は
    wired path でも弁別する: 単一セッション由来（``session_bound=True``）は一過性の
    バーストで skill 化の根拠が弱く、複数セッションにまたがるほど定着パターンとして
    CREATE の根拠が強い。``projects`` は cross-project な直接 API 利用のために残置する。
    """
    projects: List[str] = []
    seen_proj: set = set()
    sessions: set = set()
    for r in records:
        src = r.extra.get("source_file", "") if isinstance(r.extra, dict) else ""
        if src:
            proj = Path(src).parent.name
            if proj and proj not in seen_proj:
                projects.append(proj)
                seen_proj.add(proj)
        sid = (r.session_id or "").strip()
        if sid:
            sessions.add(sid)
    session_count = len(sessions)
    return {
        "projects": projects,
        "session_count": session_count,
        # 単一（または 0）セッション由来 = 一過性で reuse 証拠が弱い
        "session_bound": session_count <= 1,
    }


def _failure_analysis(records: List[TrajectoryRecord]) -> Dict[str, Any]:
    """失敗の罠（どの文脈で失敗したか）を集計する（#27）。

    outcome=="failure" の record を数え、失敗率と代表的な失敗トリガー（user_prompt）を
    返す。failure producer（trajectory_sampler の未回復エラー判定）を配線したことで
    生成される failure record を消費する。failure が 0 件でも軸の骨格は壊さない。
    """
    failures = [r for r in records if r.outcome == "failure"]
    failure_count = len(failures)
    total = len(records)
    failure_rate = failure_count / total if total else 0.0

    sample_failure_triggers: List[str] = []
    seen: set = set()
    for r in failures:
        p = (r.user_prompt or "").strip()
        if not p or p in seen:
            continue
        sample_failure_triggers.append(p)
        seen.add(p)
        if len(sample_failure_triggers) >= SAMPLE_TRIGGER_LIMIT:
            break

    return {
        "failure_count": failure_count,
        "failure_rate": failure_rate,
        "sample_failure_triggers": sample_failure_triggers,
        "is_failure_derived": failure_count > 0,
    }
