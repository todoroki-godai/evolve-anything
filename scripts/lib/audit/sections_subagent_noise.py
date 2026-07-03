"""subagents.jsonl の agent_type ノイズ内訳を advisory 分解表示する（#142-8b）。

subagents.jsonl は SubagentStop hook が書く生ログで、本物の Task subagent 以外の発火
（compaction 要約 / メインセッション Stop / rate-limit メッセージ / harness の ID 形 agent_type）
でノイズ行が混じる。writer（subagent_observe）と reader（fanout_cost / collectors）は
``is_noise_agent_type`` でこれらを除外するが、除外「率」と内訳（空文字 N / ID 形 N）が誰にも
見えず、旧レコード残存か「空 agent_type を書き続ける writer が現存する」かの切り分けが
できなかった（実測 sys-bots で 43.2% = 640/1481 のノイズ率だが未分解）。

この section が当 PJ スコープでノイズを 2 種（``empty`` / ``id_form``）に分解し件数・率・
最古/最新 timestamp を surface する（決定論・LLM 非依存・advisory のみ・スコア重み非関与）。

live writer 判定: 現行 writer（``subagent_observe.handle_subagent_stop``）は書込前に
``is_noise_agent_type`` を guard するため live writer はノイズを記録しない。よって最新ノイズ
timestamp が古ければ historical residue（ℹ）、直近（``RECENT_NOISE_DAYS`` 以内）なら旧 hook
併走 or リーク疑い（⚠）と切り分けて表示する。集計は reader 側で既に除外済みで表示のみ。

テストは ``monkeypatch.setattr(sections_subagent_noise, "DATA_DIR", tmp_path)`` で module 属性を
差し替える（文字列ターゲット patch を避ける既知 pitfall #96 準拠・fanout_cost と同流儀）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .advisory import build_advisory_section

try:
    from rl_common import DATA_DIR, noise_agent_type_kind
except ImportError:  # pragma: no cover - パス未解決時のフォールバック
    DATA_DIR = Path.home() / ".claude" / "evolve-anything"

    def noise_agent_type_kind(agent_type):
        """rl_common 未解決時のフォールバック（空判定のみ）。"""
        return "empty" if not str(agent_type or "").strip() else None

# 最新ノイズ timestamp がこの日数以内なら live writer 疑いで ⚠、それ以前は residue で ℹ。
RECENT_NOISE_DAYS = 7


def _parse_ts(value) -> Optional[datetime]:
    """ISO8601 timestamp を datetime に。tz suffix 揺れ（Z / +00:00）を吸収する（辞書順比較の罠回避）。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _normalize_pj(value):
    """PJ 識別子を worktree 安全 slug に正規化する（fanout_cost / outcome_metrics と共有・#489）。"""
    try:
        from audit import outcome_metrics
        return outcome_metrics._normalize_pj(value)
    except ImportError:  # pragma: no cover - パス未解決時のフォールバック
        if not value:
            return None
        return Path(str(value)).name or None


def _iter_data_dirs(base: Path) -> List[Path]:
    """DATA_DIR 断片化（canonical / legacy rename / plugins-data split）を cross-dir union read する。"""
    try:
        from rl_common import iter_read_data_dirs
        return list(iter_read_data_dirs(base))
    except ImportError:  # pragma: no cover - パス未解決時のフォールバック
        return [base]


def compute_subagent_noise_breakdown(project_dir) -> Optional[Dict[str, Any]]:
    """当 PJ の subagents.jsonl を全期間走査し agent_type ノイズ内訳を返す。

    窓は掛けない（historical residue は窓外の古いレコードが本体のため）。当 PJ に帰属する
    レコードのみを母数にし、各レコードを clean / empty / id_form に分類する。

    Returns:
        None（当 PJ レコード 0 件 = 評価対象なし）または dict:
          {total, noise, empty, id_form, oldest_ts, newest_ts, rate}
        ノイズ 0 件でも total>0 なら dict を返す（沈黙判定は applicable 側の責務）。
    """
    base = DATA_DIR
    project = _normalize_pj(str(project_dir)) if project_dir is not None else None
    total = 0
    empty = 0
    id_form = 0
    # (datetime, 元 str) で保持し min/max は datetime で判定、表示は元 str（tz suffix 罠回避）。
    noise_ts: List["tuple[datetime, str]"] = []
    for d in _iter_data_dirs(base):
        path = d / "subagents.jsonl"
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            try:
                rec = json.loads(s)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(rec, dict):
                continue
            # 当 PJ スコープ: 帰属フィールドがある行のみ他 PJ を除外（無い行は寛容に include）。
            if project is not None:
                slug = _normalize_pj(rec.get("project") or rec.get("project_path"))
                if slug is not None and slug != project:
                    continue
            total += 1
            kind = noise_agent_type_kind(rec.get("agent_type", ""))
            if kind == "empty":
                empty += 1
            elif kind == "id_form":
                id_form += 1
            if kind is not None:
                dt = _parse_ts(rec.get("timestamp", ""))
                if dt is not None:
                    noise_ts.append((dt, str(rec.get("timestamp", ""))))
    if total == 0:
        return None
    noise = empty + id_form
    oldest = min(noise_ts, key=lambda t: t[0])[1] if noise_ts else None
    newest = max(noise_ts, key=lambda t: t[0])[1] if noise_ts else None
    return {
        "total": total,
        "noise": noise,
        "empty": empty,
        "id_form": id_form,
        "oldest_ts": oldest,
        "newest_ts": newest,
        "rate": noise / total if total else 0.0,
    }


def build_subagent_noise_section(project_dir: Path) -> Optional[List[str]]:
    """subagents.jsonl の agent_type ノイズ内訳を audit に advisory 表示する。

    - subagents レコードが当 PJ に 0 件 → None（沈黙・評価対象なし）
    - ノイズ 0 件 → None（無ければ非表示・memory_dup_residue と同慣習）
    - ノイズあり → 空文字 / ID 形の内訳 + 率 + 最古/最新 timestamp を surface。
      最新が直近なら ⚠（live writer 疑い）、古ければ ℹ（historical residue）。
    """

    def compute(proj: Path) -> Optional[Dict[str, Any]]:
        return compute_subagent_noise_breakdown(proj)

    def render(data: Dict[str, Any]) -> List[str]:
        empty, id_form = data["empty"], data["id_form"]
        noise, total, rate = data["noise"], data["total"], data["rate"]
        newest_dt = _parse_ts(data["newest_ts"])
        recent = newest_dt is not None and newest_dt >= (
            datetime.now(timezone.utc) - timedelta(days=RECENT_NOISE_DAYS)
        )
        marker = "⚠" if recent else "ℹ"
        lines = [
            f"{marker} agent_type ノイズ {noise} 件 / 全 {total} 件（{rate:.1%}）— "
            f"内訳: 空文字 {empty} 件 / ID 形 {id_form} 件。",
            f"  期間: 最古 {data['oldest_ts']} 〜 最新 {data['newest_ts']}。",
        ]
        if recent:
            lines.append(
                f"  → 直近 {RECENT_NOISE_DAYS} 日以内にノイズ行が追記されています。現行 writer"
                "（subagent_observe）は #36/#44 でノイズを guard するため、旧バージョン hook の"
                " 併走 or writer リークの可能性があります。writer を特定して遮断してください。"
            )
        else:
            lines.append(
                "  → 現行 writer（subagent_observe）は #36/#44 でノイズを記録しません。"
                "これらは guard 導入前の historical residue で、reader（fanout_cost / collectors）は"
                " 既に除外済みです（集計に影響なし・表示のみ）。"
            )
        return lines

    return build_advisory_section(
        project_dir,
        title="Subagent agent_type Noise (当PJ・advisory — 表示のみ・スコア重み非関与)",
        blurb=[
            "subagents.jsonl（SubagentStop hook の生ログ）に混じる非 Task ノイズ行を、空文字 /"
            " ID 形に分けて可視化します（#142-8b）。reader/writer は既に除外済みで集計には"
            "影響しません。旧レコード残存か live writer 故障かの切り分け用。",
        ],
        compute=compute,
        applicable=lambda data: data["noise"] > 0,
        render=render,
    )
