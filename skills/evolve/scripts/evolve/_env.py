#!/usr/bin/env python3
"""env / slug / tier 系 helper（#531 PR 2/8 で evolve/__init__.py から抽出）。

DATA_DIR / EVOLVE_STATE_FILE / ENV_TIER_THRESHOLDS の定数と、env score・tier 判定・
PJ slug 解決のための末端 helper をまとめる。振る舞いはゼロ変更で、__init__.py が
全名前を re-export して `from evolve import X` の後方互換を保つ。

import 時点で `DATA_DIR = _resolve_data_dir()` を計算するため、本 module を import する側
（__init__.py）は **sys.path.insert(scripts/lib) を行った後で** `from ._env import ...` する
（_resolve_data_dir が `from rl_common import resolve_data_dir` するため）。

PLUGIN_ROOT は `from plugin_root import PLUGIN_ROOT`（skills/evolve/scripts が sys.path に
ある前提で __init__.py が先頭で解決済み）。本 module も冒頭で同じ import を行う。
"""
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from plugin_root import PLUGIN_ROOT
_plugin_root = PLUGIN_ROOT


def _resolve_data_dir() -> Path:
    """DATA_DIR を解決（CLAUDE_PLUGIN_DATA 優先、未設定は ~/.claude/evolve-anything）。

    rl_common.resolve_data_dir（#364 Phase 2 の marker ゲート redirect 含む）に揃え、
    reader（hooks / scripts.lib）と同一 DATA_DIR に解決する。従来は env を無視して
    home 固定だったため、CLAUDE_PLUGIN_DATA で隔離した dogfood gate / テストが
    evolve.py の書込・読込だけ実環境 DATA_DIR に漏れていた（#517）。import 失敗時は
    従来 fallback。MARKER_ROOT（evolve_decisions.py）の home 固定は別契約なので不変。
    """
    import os

    env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    try:
        from rl_common import resolve_data_dir

        return Path(resolve_data_dir(env))
    except Exception:
        if env:
            return Path(env)
        return Path.home() / ".claude" / "evolve-anything"


DATA_DIR = _resolve_data_dir()
EVOLVE_STATE_FILE = DATA_DIR / "evolve-state.json"

ENV_TIER_THRESHOLDS = {"medium": 20, "large": 50}


def _resolve_evolve_slug(project_root: Path) -> str:
    """worktree 安全な PJ slug を返す（#408-C）。

    optimize_history_store.resolve_slug（git-common-dir 親で正規化、ADR-031）を
    再利用する。worktree から呼んでも本体 repo 名に正規化される。解決不能なら
    "_unattributed"。result metadata 用なので import 失敗時も例外を投げない。
    """
    try:
        from optimize_history_store import resolve_slug

        return resolve_slug(project_root)
    except Exception:
        return "_unattributed"


def _resolve_pj_slug(project_dir: Optional[str]) -> str:
    """utterances.db の pj_slug と**同じ導出**で PJ slug を返す（#431/#440 修正）。

    weak_signals / correction_semantic は utterances.db の pj_slug と突合するため、
    optimize_history_store の git-common-dir 方式ではなく utterance_archive と同型の
    「``/.claude/worktrees/`` で切って本体 repo basename」を使う（worktree 内実行で
    worktree 名になり utterances.db の pj_slug と食い違う PR #440 の既知課題を解消）。
    """
    base = project_dir or str(Path.cwd())
    try:
        from utterance_archive.extractor import pj_slug_from_cwd

        slug = pj_slug_from_cwd(base)
        if slug:
            return slug
    except Exception:
        pass
    return Path(base).name


def _compute_env_score_struct(
    project_dir: Optional[str], *, dry_run: bool = False
) -> Dict[str, Any]:
    """構造化 env_score を算出して result トップレベルに surface するための dict を返す（#523-2/#526-2）。

    run_audit は markdown レポート文字列だけを返し構造化 env_score を捨てるため、
    SKILL.md / references/report-narration.md が読む `result["env_score"]` が常に欠落し、
    成長レベル演出（compute_level）が一度も発火しなかった。本関数が同じ権威ソース
    （`compute_environment_fitness` — audit の Growth Report と同一の算出関数）から
    構造化スコアを取り直し、`compute_level` でレベル・称号まで解決して dict を返す。

    silence != evaluated 原則の自己適用: 算出失敗時は黙らず degraded=True で
    「取得失敗 + 前回 level（world-context.json フォールバック）」を surface する。

    dry_run=True 時は fitness 履歴ストアへの書き込みを抑止する（record=False）。

    Returns:
        成功: {"score": float, "level": int, "title_ja": str, "title_en": str,
               "sources": [...], "degraded": False}
        失敗: {"score": None, "degraded": True, "reason": str,
               "previous_level": int|None, "previous_title_ja": str|None}
    """
    proj = Path(project_dir).resolve() if project_dir else Path.cwd()
    try:
        _fitness_dir = PLUGIN_ROOT / "scripts" / "rl" / "fitness"
        if str(_fitness_dir) not in sys.path:
            sys.path.insert(0, str(_fitness_dir))
        from environment import compute_environment_fitness
        from growth_level import compute_level

        env_result = compute_environment_fitness(proj, record=not dry_run)
        score = (
            env_result.get("overall")
            if isinstance(env_result, dict)
            else None
        )
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise ValueError(f"env fitness overall が数値でない: {score!r}")
        info = compute_level(float(score))
        return {
            "score": round(float(score), 4),
            "level": info.level,
            "title_ja": info.title_ja,
            "title_en": info.title_en,
            "sources": env_result.get("sources", []) if isinstance(env_result, dict) else [],
            "degraded": False,
        }
    except Exception as e:
        return _env_score_degraded(proj, reason=str(e))


def _env_score_degraded(proj: Path, *, reason: str) -> Dict[str, Any]:
    """env_score 算出失敗時の degraded dict を構築する（前回 level フォールバック）。

    world-context.json に前回の current_level / protagonist_title が残っていれば
    それを surface し、読み手が「取得失敗だが前回 Lv.N」と判断できるようにする。
    """
    previous_level: Optional[int] = None
    previous_title_ja: Optional[str] = None
    try:
        from world_context import load_world_context

        slug = _resolve_evolve_slug(proj)
        ctx = load_world_context(DATA_DIR, slug) or {}
        lvl = ctx.get("current_level")
        if isinstance(lvl, int):
            previous_level = lvl
        title = ctx.get("protagonist_title")
        if isinstance(title, str) and title:
            previous_title_ja = title
    except Exception:
        pass
    return {
        "score": None,
        "degraded": True,
        "reason": reason,
        "previous_level": previous_level,
        "previous_title_ja": previous_title_ja,
    }


def _apply_remediation_suppression(proposable, slug, now=None):
    """却下済み提案を suppression ledger で除外する（#477-2 配線）。

    remediation の proposable 候補から、過去にユーザーが却下/スキップして
    suppression_ledger に記録された提案（dedup_key 一致・TTL 内）を除外する。
    べき等性原則（重複提案 MUST NOT）の実装。filter_suppressed は **読み取りのみ**で
    副作用がないため dry-run でも安全に適用できる（書き込みは SKILL.md 側の
    record_rejection が担い、dry-run では呼ばない）。

    suppression_ledger が import できない場合は全件 surface（フェーズを壊さない）。

    Returns:
        (surviving, suppressed_count) のタプル。
    """
    try:
        from remediation.suppression_ledger import filter_suppressed
    except Exception:
        return list(proposable), 0
    try:
        out = filter_suppressed(proposable, slug=slug, now=now)
    except Exception:
        return list(proposable), 0
    return out["surface"], len(out["suppressed"])


def _surface_constitutional_status(
    project_dir: Path,
    warning_sink: List[Dict[str, Any]],
    observability: Optional[Dict[str, Any]],
) -> Optional[str]:
    """constitutional の cache 状態を warnings/observability に昇格する（#408-D）。

    constitutional は [ADR-037] で LLM 全廃済み。cache 未生成/全 miss だと None を返すが、
    これは「評価失敗」ではなく「stale → refresh 必要」。従来は warnings にも observability にも
    乗らず、レポート本文だけが（誤って）「LLM 評価に失敗しました」と出して取り違えを招いていた。
    ここで cache-only 再集約（LLM 非依存・安価）して状態を昇格する。

    Returns: 追加した surface 行（None=正常算出で surface 不要、または import 失敗）。
    """
    try:
        sys.path.insert(0, str(_plugin_root / "scripts" / "rl"))
        from fitness.constitutional import compute_constitutional_score

        con = compute_constitutional_score(project_dir)
    except Exception:
        # 状態 surface は best-effort。本流（audit/discover 等）には影響させない。
        return None

    if not (con is None or (isinstance(con, dict) and con.get("overall") is None)):
        return None  # 正常算出 — surface 不要

    skip_reason = con.get("skip_reason") if isinstance(con, dict) else None
    if skip_reason == "low_coverage":
        line = "Constitutional: coverage 不足でスキップ（評価対象が閾値未満）"
    else:
        line = (
            "Constitutional: cache stale/全 miss で未算出（失敗ではない）。"
            "audit Step 3.5 の 2 相 refresh で cache 再生成を推奨"
        )
    # #561: 良性 advisory は warning_sink に積まない。
    # warning_sink → result["warnings"] → evolve_introspect._detect_captured_warnings で
    # bug 候補として拾われるため（scipy RuntimeWarning 等の真の警告用のパス）。
    # observability のみに surface する。
    if isinstance(observability, dict):
        observability["constitutional"] = [line]
    return line


def _count_env_artifacts(project_root: Path) -> Dict[str, int]:
    """tier 判定に使うスキル数・ルール数の内訳を返す（決定論）。

    env_tier の決定根拠（#408-E）を出力に含めるため、tier 計算と内訳算出を共有する。
    """
    skills_dir_count = 0
    skills_dir = project_root / ".claude" / "skills"
    if skills_dir.is_dir():
        skills_dir_count = sum(1 for d in skills_dir.iterdir() if d.is_dir())

    claude_md_skills = 0
    claude_md = project_root / "CLAUDE.md"
    if claude_md.is_file():
        try:
            content = claude_md.read_text(encoding="utf-8")
            in_skills = False
            for line in content.splitlines():
                # Skills セクション開始
                if re.match(r"^#{1,3}\s+.*[Ss]kills?\b|^#{1,3}\s+.*スキル", line):
                    in_skills = True
                    continue
                # 別のセクション開始で終了
                if in_skills and re.match(r"^#{1,3}\s+", line):
                    in_skills = False
                    continue
                # リスト項目をカウント
                if in_skills and re.match(r"^\s*[-*]\s+", line):
                    claude_md_skills += 1
        except (OSError, UnicodeDecodeError):
            pass

    rules_count = 0
    rules_dir = project_root / ".claude" / "rules"
    if rules_dir.is_dir():
        rules_count = sum(1 for f in rules_dir.iterdir() if f.is_file())

    total = skills_dir_count + claude_md_skills + rules_count
    return {
        "skills_dir": skills_dir_count,
        "claude_md_skills": claude_md_skills,
        "rules": rules_count,
        "total": total,
    }


def _tier_from_count(count: int) -> str:
    if count >= ENV_TIER_THRESHOLDS["large"]:
        return "large"
    if count >= ENV_TIER_THRESHOLDS["medium"]:
        return "medium"
    return "small"


def _compute_env_tier(project_root: Path) -> str:
    """環境のスキル数+ルール数からtierを判定。

    スキル数: .claude/skills/ 配下のディレクトリ数 + CLAUDE.md の Skills セクション記載数
    ルール数: .claude/rules/ 配下のファイル数

    Returns: "small" | "medium" | "large"
    """
    return _tier_from_count(_count_env_artifacts(project_root)["total"])
