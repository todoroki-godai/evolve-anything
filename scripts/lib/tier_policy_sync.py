"""tier_policy の sync エンジン（正典 → 配布先, #193）。

`model-tiers.json` の ``targets`` に明示列挙されたファイルのみを扱う（自動検出は
実装しない＝設計上の確定事項）。target 種別は3つ:

- **agent**: .md frontmatter の自己申告 ``tier:`` を読み、正典 model/effort を
  ``model:``/``effort:`` 行へ反映（行単位テキスト編集。他行・本文は byte-exact 維持。
  YAML 再シリアライズはコメント・順序を壊すので禁止）。
- **settings**: JSON の ``"model"``/``"effortLevel"`` を正典値へ（他キー・インデント
  ・末尾改行は維持）。
- **routing_rule**: ``<!-- evolve-tier:begin -->``〜``<!-- evolve-tier:end -->`` の
  間だけを正典から生成した1行で置換。

``plan_sync`` は純 read 関数（書込ゼロ）。``apply_sync`` は drift のみ書き込み、
適用直後の再 ``plan_sync`` は全て ``in_sync`` になる（冪等）。
"""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from frontmatter import find_frontmatter_close, parse_frontmatter

from tier_policy import DEFAULT_TIER_POLICY, TIER_ORDER

_BEGIN_MARKER = "<!-- evolve-tier:begin -->"
_END_MARKER = "<!-- evolve-tier:end -->"


# --- 汎用ヘルパー --------------------------------------------------------------


def _resolve(path_str: str) -> Path:
    return Path(path_str).expanduser()


def _unified_diff(old: str, new: str, label: str) -> str:
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=label,
        tofile=label,
    )
    return "".join(diff)


def _tiers_from_config(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    tiers = config.get("tiers")
    if not tiers:
        return {t: dict(p) for t, p in DEFAULT_TIER_POLICY.items()}
    return tiers


# --- agent frontmatter 編集（行単位テキスト編集・YAML 再シリアライズ禁止）--------


def _set_or_insert_line(yaml_block: str, key: str, value: str) -> str:
    """yaml_block 内の ``key:`` 行を value に置換。無ければ末尾に追加する。"""
    pattern = re.compile(rf"(?m)^{re.escape(key)}:.*$")
    new_line = f"{key}: {value}"
    if pattern.search(yaml_block):
        return pattern.sub(lambda _m: new_line, yaml_block, count=1)
    if yaml_block.endswith("\n"):
        return yaml_block[:-1] + f"\n{new_line}\n"
    return yaml_block + f"\n{new_line}"


def _remove_line(yaml_block: str, key: str) -> str:
    pattern = re.compile(rf"(?m)^{re.escape(key)}:.*\n?")
    return pattern.sub("", yaml_block, count=1)


def desired_agent_text(
    text: str, model: Optional[str], effort: Optional[str]
) -> Optional[str]:
    """agent frontmatter の model/effort を正典値へ揃えたテキストを返す。

    frontmatter が無い/閉じていない場合は None（編集不能）。本文・他 frontmatter 行は
    byte-exact に維持する（``text[:3] + 編集済み yaml block + text[end:]``）。
    """
    if not text.startswith("---"):
        return None
    end = find_frontmatter_close(text)
    if end == -1:
        return None
    yaml_block = text[3:end]
    if model is not None:
        yaml_block = _set_or_insert_line(yaml_block, "model", model)
    if effort is None:
        yaml_block = _remove_line(yaml_block, "effort")
    else:
        yaml_block = _set_or_insert_line(yaml_block, "effort", effort)
    return text[:3] + yaml_block + text[end:]


def _agent_target_plan(
    path_str: str, tiers: Dict[str, Dict[str, Any]]
) -> Tuple[Dict[str, Any], Optional[str]]:
    path = _resolve(path_str)
    base = {"path": str(path), "type": "agent"}
    if not path.is_file():
        return {**base, "status": "missing", "diff": None, "reason": "ファイルが存在しません"}, None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return {**base, "status": "missing", "diff": None, "reason": f"読み込みエラー: {e}"}, None

    fm = parse_frontmatter(path)
    tier_raw = fm.get("tier")
    tier = str(tier_raw).strip().upper() if tier_raw not in (None, "") else None
    if tier is None or tier not in tiers:
        reason = "tier 宣言が無い" if tier is None else f"未知の tier: {tier_raw!r}"
        return {**base, "status": "skip", "diff": None, "reason": reason}, None

    policy = tiers[tier]
    desired = desired_agent_text(text, policy.get("model"), policy.get("effort"))
    if desired is None:
        return {**base, "status": "skip", "diff": None, "reason": "frontmatter が閉じていません"}, None
    if desired == text:
        return {**base, "status": "in_sync", "diff": None, "reason": None}, None
    diff = _unified_diff(text, desired, str(path))
    return {**base, "status": "drift", "diff": diff, "reason": f"tier:{tier} と不整合"}, desired


# --- settings JSON 編集 ---------------------------------------------------------


def desired_settings_text(
    text: str, model: Optional[str], effort: Optional[str]
) -> Optional[str]:
    """settings JSON の ``model``/``effortLevel`` を正典値へ揃えたテキストを返す。

    他キー・2スペースインデント・末尾改行の有無は維持する。JSON パース不能は None。
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    new_data = dict(data)
    if model is not None:
        new_data["model"] = model
    if effort is None:
        new_data.pop("effortLevel", None)
    else:
        new_data["effortLevel"] = effort
    new_text = json.dumps(new_data, indent=2, ensure_ascii=False)
    if text.endswith("\n"):
        new_text += "\n"
    return new_text


def _settings_target_plan(
    entry: Dict[str, Any], tiers: Dict[str, Dict[str, Any]]
) -> Tuple[Dict[str, Any], Optional[str]]:
    path_str = entry.get("path")
    tier_raw = entry.get("tier")
    tier = str(tier_raw).strip().upper() if tier_raw not in (None, "") else None
    path = _resolve(path_str) if path_str else None
    base = {"path": str(path) if path else str(path_str), "type": "settings"}

    if path is None or not path.is_file():
        return {**base, "status": "missing", "diff": None, "reason": "ファイルが存在しません"}, None
    if tier is None or tier not in tiers:
        reason = "tier 未指定" if tier is None else f"未知の tier: {tier_raw!r}"
        return {**base, "status": "skip", "diff": None, "reason": reason}, None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return {**base, "status": "missing", "diff": None, "reason": f"読み込みエラー: {e}"}, None

    policy = tiers[tier]
    desired = desired_settings_text(text, policy.get("model"), policy.get("effort"))
    if desired is None:
        return {**base, "status": "skip", "diff": None, "reason": "JSON パースエラー"}, None
    if desired == text:
        return {**base, "status": "in_sync", "diff": None, "reason": None}, None
    diff = _unified_diff(text, desired, str(path))
    return {**base, "status": "drift", "diff": diff, "reason": f"tier:{tier} と不整合"}, desired


# --- routing rule マーカー間置換 -------------------------------------------------


def render_routing_line(tiers: Dict[str, Dict[str, Any]]) -> str:
    """model-routing rule に埋め込む1行を tier 順（HEAD/HARD/NORMAL/MECH/REVIEW）で生成する。"""
    parts = []
    for tier in TIER_ORDER:
        policy = tiers.get(tier) or DEFAULT_TIER_POLICY.get(tier, {})
        model = policy.get("model")
        effort = policy.get("effort")
        desc = policy.get("description", "")
        if effort:
            parts.append(f"{tier}={model}・effort {effort}（{desc}）")
        else:
            parts.append(f"{tier}={model}（{desc}）")
    return "- ティア（モデル×effort）: " + " / ".join(parts)


def desired_routing_text(text: str, tiers: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """マーカー間だけを正典から生成した1行で置換したテキストを返す。マーカー不在は None。"""
    lines = text.splitlines(keepends=True)
    begin_idx = next((i for i, l in enumerate(lines) if l.strip() == _BEGIN_MARKER), None)
    end_idx = next((i for i, l in enumerate(lines) if l.strip() == _END_MARKER), None)
    if begin_idx is None or end_idx is None or end_idx <= begin_idx:
        return None
    new_line = render_routing_line(tiers) + "\n"
    return "".join(lines[: begin_idx + 1] + [new_line] + lines[end_idx:])


def _routing_target_plan(
    path_str: str, tiers: Dict[str, Dict[str, Any]]
) -> Tuple[Dict[str, Any], Optional[str]]:
    path = _resolve(path_str)
    base = {"path": str(path), "type": "routing_rule"}
    if not path.is_file():
        return {**base, "status": "missing", "diff": None, "reason": "ファイルが存在しません"}, None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return {**base, "status": "missing", "diff": None, "reason": f"読み込みエラー: {e}"}, None
    desired = desired_routing_text(text, tiers)
    if desired is None:
        return {**base, "status": "skip", "diff": None, "reason": "マーカー未設置"}, None
    if desired == text:
        return {**base, "status": "in_sync", "diff": None, "reason": None}, None
    diff = _unified_diff(text, desired, str(path))
    return {**base, "status": "drift", "diff": diff, "reason": "正典と不整合"}, desired


# --- plan_sync / apply_sync ------------------------------------------------------


def plan_sync(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """全 target の desired state を計算する純 read 関数（書込ゼロ）。

    各要素: ``{path, type, status(in_sync|drift|skip|missing), diff, reason}``。
    """
    tiers = _tiers_from_config(config)
    targets = config.get("targets") or {}
    plans: List[Dict[str, Any]] = []
    for path_str in targets.get("agents") or []:
        plan, _ = _agent_target_plan(path_str, tiers)
        plans.append(plan)
    for entry in targets.get("settings") or []:
        plan, _ = _settings_target_plan(entry, tiers)
        plans.append(plan)
    for path_str in targets.get("routing_rules") or []:
        plan, _ = _routing_target_plan(path_str, tiers)
        plans.append(plan)
    return plans


def apply_sync(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """plan_sync の drift のみ実際に書き込む。冪等（適用直後の再 plan は全て in_sync）。

    ``config`` dict を受け取る（``plan_sync`` の結果 list からの再構築は非対応 —
    settings target の適用には targets 側の ``tier`` フィールドが必要で、plan 出力
    schema にはそれを含めていないため。config を都度渡す設計に統一する）。
    """
    if isinstance(config, list):
        raise TypeError(
            "apply_sync は config dict を受け取ります"
            "（tier_policy.load_tiers_config() の返り値を渡してください。"
            " plan_sync の結果 list は非対応）"
        )
    tiers = _tiers_from_config(config)
    targets = config.get("targets") or {}
    results: List[Dict[str, Any]] = []

    for path_str in targets.get("agents") or []:
        plan, desired = _agent_target_plan(path_str, tiers)
        results.append(_apply_one(plan, desired))
    for entry in targets.get("settings") or []:
        plan, desired = _settings_target_plan(entry, tiers)
        results.append(_apply_one(plan, desired))
    for path_str in targets.get("routing_rules") or []:
        plan, desired = _routing_target_plan(path_str, tiers)
        results.append(_apply_one(plan, desired))
    return results


def _apply_one(plan: Dict[str, Any], desired_text: Optional[str]) -> Dict[str, Any]:
    if plan["status"] != "drift" or desired_text is None:
        return {**plan, "applied": False}
    path = Path(plan["path"])
    try:
        path.write_text(desired_text, encoding="utf-8")
    except OSError as e:
        return {**plan, "applied": False, "reason": f"書込エラー: {e}"}
    return {**plan, "applied": True}
