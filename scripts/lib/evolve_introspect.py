"""evolve_introspect — evolve 実行後の自己解析（#299）。

evolve は Observe → Diagnose → Compile → Housekeeping → Report を回すが、
**evolve 自身の実行結果（提案の質・誤検出・実行時エラー）を振り返る経路が無い**。
本モジュールは evolve の result dict を入力に取り、決定論で 3 カテゴリの
GitHub issue 候補を生成する（「install != enforcement」と同型の配線漏れを塞ぐ）。

3 カテゴリ:
  1. self_detection         — evolve が出した提案・パッチ自体の質の問題
     （split↔archive 矛盾、line budget を悪化させる content 追加提案）
  2. runtime_errors         — 各フェーズで握り潰された例外 / observability の取得失敗
  3. improvement_opportunities — 構造的な改善機会
     （系統的に却下される提案 type、calibration regression）

設計原則:
  - 決定論・LLM 非依存。入力は evolve.run_evolve() の戻り値 dict のみ。
  - 各カテゴリは検出 0 件でも summary_line に「✓ 評価したが該当なし」を残す
    （silence != evaluated。沈黙＝配線漏れ誤認を防ぐ）。
  - 起票は半自動: 本モジュールは候補と dedup までを担い、gh issue create は
    SKILL 側が人間承認の後に行う。dedup_key は root cause 単位で安定させ、
    body に隠しマーカーを埋め込むことで毎 evolve の重複起票を防ぐ。

起票先は常に todoroki-godai/rl-anything（検出対象はパイプライン自身のバグであり、
evolve がどの PJ 上で動いても起票先は固定）。SKILL 側で --repo を固定する。
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

# body に埋め込む隠しマーカー。これがあれば既存 issue と root cause 単位で
# 確実に dedup できる（タイトル類似度より強いシグナル）。
MARKER_PREFIX = "rl-evolve-introspect"
_MARKER_RE = re.compile(r"<!--\s*" + re.escape(MARKER_PREFIX) + r":([^\s>]+)\s*-->")

# タイトル類似度で dup と見なす閾値（marker なしで手動起票された既存 issue 向け）。
_TITLE_SIMILARITY_THRESHOLD = 0.80

# remediation の「content を追加する」種別の fix。これらを line-limit 超過ファイルに
# 当てると budget をさらに悪化させる（自己矛盾した提案）。
_ADDITIVE_FIX_TYPES = frozenset({
    "claudemd_missing_section",
    "skill_evolve_candidate",
    "verification_rule_candidate",
    "tool_usage_rule_candidate",
    "tool_usage_hook_candidate",
})
# line budget に関わる検出 type。
_LINE_LIMIT_TYPES = frozenset({"line_limit_violation", "near_limit"})

# prune のうち「アーカイブ寄り」の候補キー（= 消そうとしている対象）。
_PRUNE_ARCHIVE_KEYS = ("zero_invocations", "retirement_candidates", "decay_candidates")


# ── 公開 API ─────────────────────────────────────────


def analyze_evolve_result(result: Dict[str, Any], project_dir: Optional[str] = None) -> Dict[str, Any]:
    """evolve の result dict を解析し 3 カテゴリの issue 候補を返す。

    Returns:
        {
          "self_detection":          {"candidates": [...], "summary_line": str},
          "runtime_errors":          {"candidates": [...], "summary_line": str},
          "improvement_opportunities": {"candidates": [...], "summary_line": str},
          "total_candidates": int,
        }
    各 candidate: {category, title, body, suggested_label, dedup_key, severity}
    """
    result = result or {}
    self_detection = _detect_self_issues(result)
    runtime_errors = _detect_runtime_errors(result)
    improvement = _detect_improvement_opportunities(result)

    total = sum(len(s["candidates"]) for s in (self_detection, runtime_errors, improvement))
    return {
        "self_detection": self_detection,
        "runtime_errors": runtime_errors,
        "improvement_opportunities": improvement,
        "total_candidates": total,
    }


# ── カテゴリ2: 実行時エラー / 誤検出 ─────────────────


def _detect_runtime_errors(result: Dict[str, Any]) -> Dict[str, Any]:
    """各フェーズで握り潰された例外と observability 取得失敗を候補化する。

    evolve.py は各フェーズを try/except で囲み `{"error": str(e)}` を格納するため、
    フェーズが silent に死んでも result は緑に見える。ここでそれを surface する。
    """
    candidates: List[Dict[str, Any]] = []

    phases = result.get("phases", {})
    if isinstance(phases, dict):
        for name, phase in phases.items():
            if not isinstance(phase, dict):
                continue
            err = phase.get("error")
            if not err:
                continue
            candidates.append(_make_runtime_candidate(name, str(err), source="phase"))

    obs = result.get("observability")
    if isinstance(obs, dict) and obs.get("error"):
        candidates.append(_make_runtime_candidate("observability", str(obs["error"]), source="observability"))

    return _section(
        candidates,
        zero_line="✓ 実行時エラー: フェーズ例外・observability 取得失敗なし",
        hit_template="⚠ 実行時エラー {n} 件: {names}",
        name_of=lambda c: c["dedup_key"].split(":")[1],
    )


def _make_runtime_candidate(phase_name: str, error: str, source: str) -> Dict[str, Any]:
    first_line = error.strip().splitlines()[0] if error.strip() else "(空のエラー)"
    sig = _error_signature(first_line)
    title = f"[evolve introspect] `{phase_name}` フェーズで例外: {first_line[:80]}"
    body = (
        f"## 自己解析: 実行時エラー\n\n"
        f"evolve の `{phase_name}` フェーズ（{source}）で例外が握り潰されていました。"
        f"フェーズは `{{\"error\": ...}}` を格納するだけなので result は緑に見えます。\n\n"
        f"```\n{error}\n```\n\n"
        f"このフェーズの try/except で原因を握り潰さず、root cause を修正してください。"
    )
    return {
        "category": "runtime_error",
        "title": title,
        "body": body,
        "suggested_label": "bug",
        "dedup_key": f"runtime_error:{phase_name}:{sig}",
        "severity": "high",
    }


def _error_signature(text: str) -> str:
    """エラーメッセージを root cause 単位に正規化する。

    行番号・絶対/相対パス・16進/十進の ID を落として、同じ原因の例外が
    場所違いでも同一 dedup_key に潰れるようにする。
    """
    s = text.lower()
    s = re.sub(r"['\"`]?(/|\./)[^\s'\"`]+", " ", s)   # パス
    s = re.sub(r"0x[0-9a-f]+", " ", s)                # 16進アドレス
    s = re.sub(r"\b\d+\b", " ", s)                    # 数値（行番号等）
    s = re.sub(r"[^a-z0-9_]+", " ", s)                # 記号
    s = re.sub(r"\s+", " ", s).strip()
    return "-".join(s.split()[:8]) or "error"


# ── カテゴリ1: 自己検出（提案の質） ──────────────────


def _detect_self_issues(result: Dict[str, Any]) -> Dict[str, Any]:
    """evolve が出した提案・パッチ自体の質の問題を検出する。"""
    candidates: List[Dict[str, Any]] = []
    phases = result.get("phases", {})
    if not isinstance(phases, dict):
        phases = {}

    candidates.extend(_detect_split_archive_contradiction(phases))
    candidates.extend(_detect_line_budget_conflict(phases))

    return _section(
        candidates,
        zero_line="✓ 自己検出: 矛盾する提案・budget 悪化提案なし",
        hit_template="⚠ 自己検出 {n} 件: {names}",
        name_of=lambda c: c.get("subject", c["dedup_key"]),
    )


def _detect_split_archive_contradiction(phases: Dict[str, Any]) -> List[Dict[str, Any]]:
    """同一スキルを「分割せよ」と「アーカイブせよ」が同時に提案する矛盾。"""
    reorganize = phases.get("reorganize", {})
    if not isinstance(reorganize, dict) or reorganize.get("skipped"):
        return []
    split_skills = {
        _skill_name(sc) for sc in reorganize.get("split_candidates", []) if _skill_name(sc)
    }
    if not split_skills:
        return []

    prune = phases.get("prune", {})
    archive_skills: set = set()
    if isinstance(prune, dict):
        for key in _PRUNE_ARCHIVE_KEYS:
            for entry in prune.get(key, []) or []:
                name = _skill_name(entry)
                if name:
                    archive_skills.add(name)

    out: List[Dict[str, Any]] = []
    for skill in sorted(split_skills & archive_skills):
        body = (
            f"## 自己解析: 矛盾する提案\n\n"
            f"evolve が同一スキル `{skill}` に対して **split（分割）** と "
            f"**archive（淘汰）** を同時に提案しています。分割しようとする対象を"
            f"同じ run で消そうとしており、提案ロジックが矛盾しています。\n\n"
            f"reorganize の split 検出と prune の archive 検出のどちらかが誤りか、"
            f"両者の相互排他チェックが欠けています。"
        )
        out.append({
            "category": "self_detection",
            "subject": skill,
            "title": f"[evolve introspect] `{skill}` に split と archive を同時提案（矛盾）",
            "body": body,
            "suggested_label": "bug",
            "dedup_key": f"self:split_archive_contradiction:{skill}",
            "severity": "medium",
        })
    return out


def _detect_line_budget_conflict(phases: Dict[str, Any]) -> List[Dict[str, Any]]:
    """line-limit 超過ファイルに content 追加系の fix を提案している矛盾。"""
    remediation = phases.get("remediation", {})
    if not isinstance(remediation, dict):
        return []
    classified = remediation.get("classified", {})
    if not isinstance(classified, dict):
        return []

    issues: List[Dict[str, Any]] = []
    for bucket in ("auto_fixable", "proposable", "manual_required"):
        items = classified.get(bucket, [])
        if isinstance(items, list):
            issues.extend(i for i in items if isinstance(i, dict))

    line_limited = {
        _issue_file(i) for i in issues if i.get("type") in _LINE_LIMIT_TYPES and _issue_file(i)
    }
    additive = {
        _issue_file(i) for i in issues if i.get("type") in _ADDITIVE_FIX_TYPES and _issue_file(i)
    }

    out: List[Dict[str, Any]] = []
    for path in sorted(line_limited & additive):
        body = (
            f"## 自己解析: budget を悪化させる提案\n\n"
            f"`{path}` は line-limit に抵触している一方で、同じ evolve run が"
            f"このファイルへ content を追加する fix（{', '.join(sorted(_ADDITIVE_FIX_TYPES))} のいずれか）"
            f"を提案しています。適用すると行数バジェットがさらに悪化します。\n\n"
            f"remediation の分類で line-limit 抵触ファイルへの additive fix を抑止するか、"
            f"先に分割を提案する順序制御が必要です。"
        )
        out.append({
            "category": "self_detection",
            "subject": path,
            "title": f"[evolve introspect] line-limit 超過ファイルに追記提案: `{path}`",
            "body": body,
            "suggested_label": "bug",
            "dedup_key": f"self:line_budget_conflict:{path}",
            "severity": "medium",
        })
    return out


# ── カテゴリ3: 改善余地 ─────────────────────────────


def _detect_improvement_opportunities(result: Dict[str, Any]) -> Dict[str, Any]:
    """系統的却下・calibration regression から構造的改善機会を抽出する。"""
    candidates: List[Dict[str, Any]] = []
    phases = result.get("phases", {})
    if not isinstance(phases, dict):
        phases = {}

    se = phases.get("self_evolution", {})
    if isinstance(se, dict) and not se.get("skipped") and not se.get("error"):
        candidates.extend(_detect_systematic_rejections(se))
        candidates.extend(_detect_calibration_regression(se))

    return _section(
        candidates,
        zero_line="✓ 改善余地: 系統的却下・calibration regression なし",
        hit_template="⚠ 改善余地 {n} 件: {names}",
        name_of=lambda c: c.get("subject", c["dedup_key"]),
    )


def _detect_systematic_rejections(se: Dict[str, Any]) -> List[Dict[str, Any]]:
    flags = se.get("false_positives", {}).get("systematic_flags", {})
    if not isinstance(flags, dict):
        return []
    out: List[Dict[str, Any]] = []
    for issue_type, count in sorted(flags.items()):
        body = (
            f"## 自己解析: 系統的に却下される提案\n\n"
            f"提案 type `{issue_type}` が systematic に却下されています"
            f"（シグナル: {count}）。この type の提案ロジックまたは confidence が"
            f"実態と乖離しており、毎 evolve でノイズ提案を出している可能性があります。\n\n"
            f"検出ロジックの精度改善、confidence の引き下げ、または提案自体の廃止を検討してください。"
        )
        out.append({
            "category": "improvement",
            "subject": issue_type,
            "title": f"[evolve introspect] 提案 type `{issue_type}` が系統的に却下されている",
            "body": body,
            "suggested_label": "enhancement",
            "dedup_key": f"improvement:systematic_rejection:{issue_type}",
            "severity": "low",
        })
    return out


def _detect_calibration_regression(se: Dict[str, Any]) -> List[Dict[str, Any]]:
    reg = se.get("regression", {})
    if not isinstance(reg, dict) or not reg.get("has_regression"):
        return []
    regressions = reg.get("regressions", {})
    if not isinstance(regressions, dict) or not regressions:
        # has_regression は立っているが詳細不明 → 単一候補で surface
        regressions = {"_overall": {}}
    out: List[Dict[str, Any]] = []
    for issue_type, info in sorted(regressions.items()):
        delta = info.get("delta") if isinstance(info, dict) else None
        delta_str = f"（delta={delta}）" if delta is not None else ""
        body = (
            f"## 自己解析: calibration regression\n\n"
            f"confidence calibration が `{issue_type}` で劣化しました{delta_str}。"
            f"self_evolution の control chart / regression check が退行を検出しています。\n\n"
            f"calibration の母集団・閾値、または該当 type の fix 結果の記録経路を確認してください。"
        )
        out.append({
            "category": "improvement",
            "subject": issue_type,
            "title": f"[evolve introspect] calibration regression: `{issue_type}`",
            "body": body,
            "suggested_label": "enhancement",
            "dedup_key": f"improvement:calibration_regression:{issue_type}",
            "severity": "low",
        })
    return out


# ── dedup ────────────────────────────────────────────


def render_issue_body(candidate: Dict[str, Any]) -> str:
    """候補本文の末尾に dedup マーカーを埋め込んで返す。"""
    marker = f"<!-- {MARKER_PREFIX}:{candidate['dedup_key']} -->"
    return f"{candidate.get('body', '').rstrip()}\n\n{marker}\n"


def extract_marker(text: str) -> Optional[str]:
    """body から dedup_key を取り出す。無ければ None。"""
    m = _MARKER_RE.search(text or "")
    return m.group(1) if m else None


def filter_duplicates(
    candidates: List[Dict[str, Any]],
    existing_issues: List[Dict[str, Any]],
    title_threshold: float = _TITLE_SIMILARITY_THRESHOLD,
) -> Dict[str, List[Dict[str, Any]]]:
    """既存 open issue と重複する候補を除外する。

    1) body の隠しマーカー（dedup_key）が一致する既存 issue があれば dup（最強シグナル）。
    2) マーカーが無い手動起票でも、タイトル類似度が閾値以上なら dup と見なす。

    Args:
        candidates: analyze_evolve_result が出した候補リスト。
        existing_issues: [{"number": int, "title": str, "body": str}, ...]（gh issue list 由来）。

    Returns:
        {"unique": [...], "duplicates": [{**candidate, "existing_number": int, "reason": str}]}
    """
    marker_index: Dict[str, int] = {}
    for issue in existing_issues or []:
        key = extract_marker(issue.get("body", ""))
        if key:
            marker_index[key] = issue.get("number")

    unique: List[Dict[str, Any]] = []
    duplicates: List[Dict[str, Any]] = []
    for cand in candidates:
        dup_number, reason = _match_existing(cand, existing_issues or [], marker_index, title_threshold)
        if dup_number is not None:
            duplicates.append({**cand, "existing_number": dup_number, "reason": reason})
        else:
            unique.append(cand)
    return {"unique": unique, "duplicates": duplicates}


def _match_existing(
    cand: Dict[str, Any],
    existing_issues: List[Dict[str, Any]],
    marker_index: Dict[str, int],
    title_threshold: float,
) -> tuple:
    if cand["dedup_key"] in marker_index:
        return marker_index[cand["dedup_key"]], "marker"
    cand_title = _normalize_title(cand.get("title", ""))
    best_num, best_ratio = None, 0.0
    for issue in existing_issues:
        ratio = SequenceMatcher(None, cand_title, _normalize_title(issue.get("title", ""))).ratio()
        if ratio > best_ratio:
            best_num, best_ratio = issue.get("number"), ratio
    if best_ratio >= title_threshold:
        return best_num, f"title_similarity={best_ratio:.2f}"
    return None, ""


def _normalize_title(title: str) -> str:
    s = (title or "").lower()
    s = s.replace("`", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ── surface 整形 ─────────────────────────────────────


_CATEGORY_KEYS = ("self_detection", "runtime_errors", "improvement_opportunities")


def flatten_candidates(analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    """analyze_evolve_result の 3 カテゴリの candidates を 1 リストに平坦化する。

    SKILL Step 11 が dedup・起票へ渡す前段。prose で 3 カテゴリを手で集める実装だと
    カテゴリを 1 つ取りこぼす事故が起きるため、決定論ヘルパーに一本化する。
    """
    out: List[Dict[str, Any]] = []
    for key in _CATEGORY_KEYS:
        section = analysis.get(key, {})
        if isinstance(section, dict):
            out.extend(section.get("candidates", []) or [])
    return out


def summary_lines(analysis: Dict[str, Any]) -> List[str]:
    """SKILL がそのまま列挙する surface 行を返す（0 件でも ✓ を残す）。"""
    return [
        f"- 自己検出: {analysis['self_detection']['summary_line']}",
        f"- 実行時エラー: {analysis['runtime_errors']['summary_line']}",
        f"- 改善余地: {analysis['improvement_opportunities']['summary_line']}",
    ]


# ── 内部ヘルパ ───────────────────────────────────────


def _section(candidates, zero_line, hit_template, name_of) -> Dict[str, Any]:
    if not candidates:
        return {"candidates": [], "summary_line": zero_line}
    names = ", ".join(name_of(c) for c in candidates[:5])
    if len(candidates) > 5:
        names += f", 他 {len(candidates) - 5} 件"
    return {
        "candidates": candidates,
        "summary_line": hit_template.format(n=len(candidates), names=names),
    }


def _skill_name(entry: Any) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for key in ("skill_name", "skill", "name"):
            val = entry.get(key)
            if isinstance(val, str) and val:
                return val
    return ""


def _issue_file(issue: Dict[str, Any]) -> str:
    for key in ("file", "filename", "target", "path"):
        val = issue.get(key)
        if isinstance(val, str) and val:
            return val
    return ""
