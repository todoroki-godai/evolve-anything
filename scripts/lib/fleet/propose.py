"""fleet.propose — queue の待ち PJ に evolve --dry-run 提案をバッチ生成する（#81 Phase 2）。

Phase 1（#79/#80）が挙げた「evolve 待ち PJ」（``evolve-queue.json``）に対し、各 PJ で
``run_evolve(project_dir, dry_run=True)`` を**順次**実行し、提案（skill_evolve / remediation /
skill_triage / reorganize）を 1 本の集約レポート（``evolve-proposals-<date>.md`` + ``.json``）に
束ねる。適用は一切しない（dry-run のみ）。

コスト承認ゲート（llm-batch-guard）: 実行前に対象 PJ 件数・PJ 一覧・使用モデル・LLM 呼び出し上限を
表示し y/n 確認を取る。トークン概算は実測手段が無いため提示しない（factual-claims 準拠）。
``run_evolve(dry_run=True)`` は評価系を skip/cache 参照するため通常 LLM 呼び出しはゼロ
（``dogfood/layer1.py`` の設計コメントと一致）。稀に audit の constitutional score が Haiku を
呼ぶことがあるが、レイヤ単位キャッシュにより通常 0〜1 コール/PJ（上限 4 コール/PJ）。

重複・ノイズ抑制: 既に reject 済み（optimize_history の human_accepted=False）の skill_evolve /
discover 提案は既存 API（``evolve_decisions._extract_candidates`` / ``optimize_history_store.load_history``）
を再利用して除外する（再実装しない）。remediation / skill_triage は run_evolve 自体が
triage_ledger ベースの suppression を既に内蔵しているため対象外。

``evolve-proposals-<date>.md`` / ``.json`` は ``evolve-queue.json`` と同様の read 専用派生物
（SoR でない）ため store_registry には登録しない。

決定論・LLM 非依存（テストは常に run_evolve をスタブ差し替え可能にする DI で LLM を呼ばない）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# 提案レポートのファイル名接頭辞（read 専用派生物・store_registry 非登録）。
PROPOSALS_FILE_PREFIX = "evolve-proposals-"

_TRIAGE_ACTION_KEYS = ("CREATE", "UPDATE", "SPLIT", "MERGE")


# --- run_evolve の遅延解決（skills/evolve/scripts を sys.path に追加）---------


def _default_run_evolve() -> Callable[..., Dict[str, Any]]:
    """``evolve.run_evolve`` を遅延 import する（DI 未指定時の既定）。

    ``bin/evolve-fleet`` は ``scripts/lib`` のみを sys.path に載せるため、
    ``skills/evolve/scripts``（evolve パッケージの所在）を呼び出し時に追加する
    （``dogfood/layer1.py._sys_path_dirs`` と同じ構成、``plugin_root.PLUGIN_ROOT`` を単一ソースに）。
    """
    from plugin_root import PLUGIN_ROOT

    p = PLUGIN_ROOT / "skills" / "evolve" / "scripts"
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
    from evolve import run_evolve

    return run_evolve


# --- queue → 対象 PJ 選定 -----------------------------------------------------


def select_targets(
    queue_data: Optional[Dict[str, Any]], *, max_pj: int
) -> List[Dict[str, Any]]:
    """queue result（``evolve-queue.json`` schema）から対象 PJ を最大 ``max_pj`` 件選ぶ。

    ``queue["queue"]`` は既に material_count 降順（``select_evolve_queue``）なので、
    先頭から取るだけで「最も待ちが大きい PJ から」になる。壊れた/非 dict 要素は無視する。
    """
    if not isinstance(queue_data, dict):
        return []
    queue = queue_data.get("queue") or []
    if not isinstance(queue, list):
        return []
    limit = max(0, int(max_pj))
    out: List[Dict[str, Any]] = []
    for item in queue[:limit]:
        if not isinstance(item, dict) or not item.get("pj_slug"):
            continue
        out.append(
            {
                "pj_slug": item["pj_slug"],
                "project_path": item.get("project_path"),
                "material_count": int(item.get("material_count", 0) or 0),
            }
        )
    return out


# --- コスト承認ゲート（llm-batch-guard）--------------------------------------


def estimate_cost(targets: List[Dict[str, Any]]) -> Dict[str, Any]:
    """対象 PJ のコスト見積もり（proxy）を組み立てる。

    ``material_count`` は「evolve 待ち度合い」の proxy であり実測トークン数ではない。
    トークン概算は確認手段が無いため提示しない（factual-claims 準拠 — それらしい数字を
    捏造しない）。``run_evolve(dry_run=True)`` は評価系を skip/cache 参照するため通常
    LLM 呼び出しはゼロ（根拠: ``dogfood/layer1.py`` 実装コメント）。稀に audit の
    constitutional score が Haiku を呼ぶが、レイヤ単位キャッシュで通常 0〜1 コール/PJ・
    上限 4 コール/PJ（根拠: ``skills/evolve/scripts/evolve/phases_diagnose.py`` コメント）。
    """
    per_pj = {t["pj_slug"]: int(t.get("material_count", 0) or 0) for t in targets}
    return {
        "pj_count": len(targets),
        "pjs": [t["pj_slug"] for t in targets],
        "per_pj_material_count": per_pj,
        "total_material_count": sum(per_pj.values()),
        "model": "Haiku（audit の constitutional score のみ。skill_evolve 本体は ADR-037 により LLM 非依存）",
        "llm_call_bound": "通常 0 コール（dry-run は評価系を skip/cache 参照）。稀に constitutional score で 0〜4 コール/PJ（レイヤキャッシュにより通常 0〜1 コール）",
        "proxy_note": (
            "material_count は評価対象範囲・複雑度の proxy であり実測トークン数ではありません。"
            "トークン概算は確認手段が無いため提示しません（factual-claims 準拠）。"
        ),
    }


def format_cost_confirmation(cost: Dict[str, Any]) -> str:
    """コスト見積もりを承認プロンプト用の人間可読テキストに整形する。"""
    per_pj_str = ", ".join(f"{k}={v}" for k, v in cost["per_pj_material_count"].items())
    lines = [
        f"[fleet:propose] 対象 {cost['pj_count']} PJ: {', '.join(cost['pjs'])}",
        f"  material_count 合計: {cost['total_material_count']}（PJ別: {per_pj_str}）",
        f"  使用モデル: {cost['model']}",
        f"  LLM 呼び出し: {cost['llm_call_bound']}",
        f"  {cost['proxy_note']}",
    ]
    return "\n".join(lines)


def confirm_batch(*, yes: bool, input_func: Callable[[str], str] = input) -> bool:
    """llm-batch-guard の y/n 確認。``yes=True`` ならプロンプトをスキップして即承認する。"""
    if yes:
        return True
    try:
        ans = input_func("実行しますか？ [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("y", "yes")


# --- reject 済み提案の再提示抑制（既存 API 再利用）----------------------------


def filter_previously_rejected_candidates(
    result: Dict[str, Any],
    slug: str,
    *,
    history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """discover matched_skills + skill_evolve high/medium のうち、直近判定が reject の候補を除外する。

    候補抽出は ``evolve_decisions._extract_candidates``（既存 API）を再利用する。各候補の
    skill_name について ``optimize_history_store.load_history(slug)``（``source=evolve_diff``）から
    timestamp 最大のレコードを引き、``human_accepted is False``（最新判定が reject）なら
    ``suppressed`` に分離する（silent に消さない — 件数を報告側で transparency として出す）。

    ``history`` を明示渡しするとテスト用に hermetic（load_history を呼ばない）。

    Returns:
        ``{"kept": [...], "suppressed": [...]}``。要素は ``_extract_candidates`` と同じ
        dict（``skill_name``/``skill_path``/``pattern``/``proposal_type``）。suppressed 側は
        ``rejected_at``/``rejection_reason`` を追加で持つ。
    """
    import evolve_decisions as _ed

    candidates = _ed._extract_candidates(result)
    if not candidates:
        return {"kept": [], "suppressed": []}

    if history is None:
        import optimize_history_store as _store

        history = _store.load_history(slug)

    latest_by_name: Dict[str, Dict[str, Any]] = {}
    for rec in history:
        if not isinstance(rec, dict) or rec.get("source") != "evolve_diff":
            continue
        name = rec.get("skill_name")
        if not name:
            continue
        ts = rec.get("timestamp") or ""
        cur = latest_by_name.get(name)
        if cur is None or ts >= (cur.get("timestamp") or ""):
            latest_by_name[name] = rec

    kept: List[Dict[str, Any]] = []
    suppressed: List[Dict[str, Any]] = []
    for c in candidates:
        rec = latest_by_name.get(c["skill_name"])
        if rec is not None and rec.get("human_accepted") is False:
            suppressed.append(
                {
                    **c,
                    "rejected_at": rec.get("timestamp"),
                    "rejection_reason": rec.get("rejection_reason"),
                }
            )
        else:
            kept.append(c)
    return {"kept": kept, "suppressed": suppressed}


# --- 1 PJ の evolve result → 提案件数サマリ -----------------------------------


def summarize_pj_result(
    result: Dict[str, Any], *, suppressed_rejected_count: int = 0
) -> Dict[str, Any]:
    """evolve result（canonical schema, ``evolve_result_schema.py`` 参照）から提案件数を集計する。

    ``skill_evolve`` の high/medium 件数は既に reject 済みで再提示を抑制した件数
    （``suppressed_rejected_count``）を差し引いた「実効提案数」を total に算入する
    （生の high/medium 件数自体は transparency のためそのまま保持する）。
    """
    phases = result.get("phases") or {}
    remediation = phases.get("remediation") or {}
    skill_evolve = phases.get("skill_evolve") or {}
    skill_triage = phases.get("skill_triage") or {}
    reorganize = phases.get("reorganize") or {}

    remediation_proposable = int(remediation.get("proposable") or 0)
    se_high = int(skill_evolve.get("high_suitability") or 0)
    se_medium = int(skill_evolve.get("medium_suitability") or 0)
    se_effective = max(0, se_high + se_medium - suppressed_rejected_count)

    triage_counts = {
        k: len(skill_triage.get(k) or []) for k in _TRIAGE_ACTION_KEYS
    }
    split_candidates = len(reorganize.get("split_candidates") or [])

    total_proposals = (
        remediation_proposable
        + se_effective
        + sum(triage_counts.values())
        + split_candidates
    )

    return {
        "total_proposals": total_proposals,
        "remediation_proposable": remediation_proposable,
        "skill_evolve_high": se_high,
        "skill_evolve_medium": se_medium,
        "skill_evolve_suppressed_rejected": suppressed_rejected_count,
        "skill_triage": triage_counts,
        "reorganize_split_candidates": split_candidates,
    }


# --- バッチ実行（順次・1 PJ 失敗は他を止めない）-------------------------------


def run_propose_batch(
    targets: List[Dict[str, Any]],
    *,
    run_evolve_fn: Optional[Callable[..., Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """対象 PJ を順次 ``run_evolve(dry_run=True)`` する（並列しない）。

    1 PJ の例外・project_path 不在は ``status=error`` として記録し、他 PJ の実行は継続する。
    テストは ``run_evolve_fn`` に stub を注入して LLM 呼び出しをゼロにできる（DI・no-llm-in-tests）。
    """
    if run_evolve_fn is None:
        run_evolve_fn = _default_run_evolve()

    out: List[Dict[str, Any]] = []
    for t in targets:
        pj_slug = t.get("pj_slug")
        project_path = t.get("project_path")
        entry: Dict[str, Any] = {
            "pj_slug": pj_slug,
            "project_path": project_path,
            "material_count": int(t.get("material_count", 0) or 0),
        }
        if not pj_slug:
            entry["status"] = "error"
            entry["error"] = "pj_slug が空です"
            out.append(entry)
            continue
        if not project_path or not Path(project_path).is_dir():
            entry["status"] = "error"
            entry["error"] = f"project_path が不明または不在: {project_path!r}"
            out.append(entry)
            continue

        try:
            result = run_evolve_fn(project_dir=project_path, dry_run=True)
        except Exception as e:  # 1 PJ の失敗は他 PJ を止めない
            entry["status"] = "error"
            entry["error"] = f"{type(e).__name__}: {e}"
            out.append(entry)
            continue

        filtered = filter_previously_rejected_candidates(result, pj_slug)
        entry["status"] = "ok"
        entry["result"] = result
        entry["summary"] = summarize_pj_result(
            result, suppressed_rejected_count=len(filtered["suppressed"])
        )
        entry["suppressed_candidates"] = filtered["suppressed"]
        out.append(entry)
    return out


# --- 集約レポート組み立て + 出力 ----------------------------------------------


def _distill_pj_entry(e: Dict[str, Any]) -> Dict[str, Any]:
    """batch entry を永続レポート向けに絞り込む（巨大な生 result 全体は持ち出さない）。

    ``phases.audit.report`` 等の巨大 markdown を丸ごと JSON に埋めると
    「巨大 JSON 切断」を再演するため、件数サマリ + suppressed 候補の要点だけを残す。
    """
    base: Dict[str, Any] = {
        "pj_slug": e.get("pj_slug"),
        "project_path": e.get("project_path"),
        "material_count": e.get("material_count", 0),
        "status": e["status"],
    }
    if e["status"] == "error":
        base["error"] = e.get("error")
        return base
    base["summary"] = e["summary"]
    base["suppressed_candidates"] = [
        {
            "skill_name": c.get("skill_name"),
            "pattern": c.get("pattern"),
            "rejected_at": c.get("rejected_at"),
        }
        for c in e.get("suppressed_candidates", [])
    ]
    result = e.get("result") or {}
    base["slug"] = result.get("slug")
    base["env_tier"] = result.get("env_tier")
    return base


def build_batch_report(
    batch: List[Dict[str, Any]], *, generated_at: str, cost: Dict[str, Any]
) -> Dict[str, Any]:
    """batch 実行結果（``run_propose_batch`` の出力）から集約レポート dict を組み立てる。"""
    ok = [e for e in batch if e.get("status") == "ok"]
    errors = [e for e in batch if e.get("status") == "error"]
    total_proposals = sum(e["summary"]["total_proposals"] for e in ok)
    return {
        "generated_at": generated_at,
        "pj_count": len(batch),
        "ok_count": len(ok),
        "error_count": len(errors),
        "total_proposals": total_proposals,
        "cost_estimate": cost,
        "pjs": [_distill_pj_entry(e) for e in batch],
    }


def render_markdown_report(report: Dict[str, Any]) -> str:
    """集約レポートを PJ 別サマリ + 詳細の markdown に整形する。"""
    lines = [
        f"# evolve 提案バッチ（{report['generated_at']}）",
        "",
        f"対象 {report['pj_count']} PJ / 成功 {report['ok_count']} / "
        f"失敗 {report['error_count']} / 提案合計 {report['total_proposals']} 件",
        "",
        "## PJ 別サマリ",
        "",
    ]
    for e in report["pjs"]:
        pj = e.get("pj_slug") or "(unknown)"
        if e["status"] == "error":
            lines.append(f"- **{pj}**: エラー — {e.get('error')}")
            continue
        s = e["summary"]
        suppressed_note = (
            f"/suppressed{s['skill_evolve_suppressed_rejected']}"
            if s["skill_evolve_suppressed_rejected"]
            else ""
        )
        lines.append(
            f"- **{pj}**（material={e.get('material_count', 0)}）: "
            f"提案 {s['total_proposals']} 件 "
            f"(remediation={s['remediation_proposable']}, "
            f"skill_evolve=high{s['skill_evolve_high']}/medium{s['skill_evolve_medium']}"
            f"{suppressed_note}, "
            f"skill_triage={sum(s['skill_triage'].values())}, "
            f"reorganize_split={s['reorganize_split_candidates']})"
        )
    lines.append("")
    lines.append("## 詳細")
    for e in report["pjs"]:
        if e["status"] != "ok":
            continue
        s = e["summary"]
        lines.append("")
        lines.append(f"### {e.get('pj_slug')}")
        lines.append(f"- remediation.proposable: {s['remediation_proposable']}")
        se_line = f"- skill_evolve: high={s['skill_evolve_high']}, medium={s['skill_evolve_medium']}"
        if s["skill_evolve_suppressed_rejected"]:
            se_line += (
                f"（既に reject 済みのため再提示を抑制: "
                f"{s['skill_evolve_suppressed_rejected']} 件）"
            )
        lines.append(se_line)
        for k, v in s["skill_triage"].items():
            if v:
                lines.append(f"- skill_triage.{k}: {v}")
        if s["reorganize_split_candidates"]:
            lines.append(f"- reorganize.split_candidates: {s['reorganize_split_candidates']}")
    lines.append("")
    return "\n".join(lines)


def write_reports(
    report: Dict[str, Any], *, data_dir: Path, date_str: Optional[str] = None
) -> "tuple[Path, Path]":
    """集約レポートを ``DATA_DIR/evolve-proposals-<date>.md`` + ``.json`` に書き出す。

    ``evolve-queue.json`` と同じ read 専用派生物（SoR でない）。同日に複数回実行すると
    上書きする（evolve-queue.json の日次上書きと同じ運用）。
    """
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    md_path = data_dir / f"{PROPOSALS_FILE_PREFIX}{date_str}.md"
    json_path = data_dir / f"{PROPOSALS_FILE_PREFIX}{date_str}.json"
    md_path.write_text(render_markdown_report(report), encoding="utf-8")
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return md_path, json_path


def render_cli_summary(report: Dict[str, Any], md_path: Path, json_path: Path) -> str:
    """propose CLI の最終標準出力（1行サマリ + レポートパス）。"""
    return (
        f"[fleet:propose] 対象 {report['pj_count']} PJ / 成功 {report['ok_count']} / "
        f"失敗 {report['error_count']} / 提案合計 {report['total_proposals']} 件\n"
        f"[fleet:propose] レポート: {md_path}\n"
        f"[fleet:propose]           {json_path}"
    )
