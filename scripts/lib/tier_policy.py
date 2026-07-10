"""モデルティア（HEAD/HARD/NORMAL/MECH/REVIEW ↔ model/effort）正典の一元管理（#193）。

ティア↔model/effort の正典は従来 model-routing rule・`agent_tier.TIER_POLICY`・
各 PJ の agent frontmatter・settings.json に散在し、モデル変更のたびに全ファイル
手動追従が必要だった（2026-07-10 opus 4.8 廃止時に HEAD が fable⇄sonnet を往来した
実例）。本モジュールは `~/.claude/model-tiers.json` を外部 config の正典 SoT とし、
config load/set のコア機能を提供する（sync エンジンは `tier_policy_sync.py`、
stale-mention advisory は `tier_policy_drift.py` に分割・800行分割方針）。

**config パスは call-time 解決**（module-level にパスや読込結果をキャッシュしない）。
`Path.home()` 由来パスを import 時に固定すると env/monkeypatch に非追従となり
テスト汚染を起こす既知 pitfall（`pitfall_module_level_datadir_import_copy`）を踏む。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from agent_quality_catalog import EXACT_MODEL_ID_PATTERN, MODEL_ALIASES

# ティア↔model/effort の正典デフォルト。既存 agent_tier.TIER_POLICY と model/effort は
# 同値（2026-07-10 opus 4.8 廃止改定を反映済みの現行値）+ 各ティアに役割説明を追加。
DEFAULT_TIER_POLICY: Dict[str, Dict[str, Any]] = {
    "HEAD": {
        "model": "sonnet",
        "effort": "max",
        "description": "頭: 設計判断・diff レビュー・マージ・実環境検証",
    },
    "HARD": {
        "model": "sonnet",
        "effort": "xhigh",
        "description": "多ファイル実装・根因不明デバッグ・設計相談",
    },
    "NORMAL": {
        "model": "sonnet",
        "effort": "medium",
        "description": "仕様明確な実装・doc・調査まとめ",
    },
    "MECH": {
        "model": "haiku",
        "effort": None,
        "description": "検索掃き出し・変換・機械的編集、effort 非対応",
    },
    "REVIEW": {
        "model": "fable",
        "effort": "high",
        "description": (
            "設計&レビュー顧問: cold-read/セカンドオピニオン/設計相談/"
            "アドバーサリアル。頭が難所だけ呼ぶ非常駐"
        ),
    },
}

TIER_ORDER = ("HEAD", "HARD", "NORMAL", "MECH", "REVIEW")

CONFIG_VERSION = 1

EFFORT_VALUES = frozenset({"low", "medium", "high", "xhigh", "max"})

# tier の model として許容するエイリアス（"inherit" は tier の model として不可）。
ALLOWED_TIER_MODELS = tuple(sorted(MODEL_ALIASES - {"inherit"}))

_DEFAULT_TARGETS: Dict[str, Any] = {"agents": [], "settings": [], "routing_rules": []}


def tiers_config_path() -> Path:
    """model-tiers.json のパス。呼び出しのたびに ``Path.home()`` を解決する（call-time）。"""
    return Path.home() / ".claude" / "model-tiers.json"


def _default_config() -> Dict[str, Any]:
    return {
        "version": CONFIG_VERSION,
        "tiers": {tier: dict(policy) for tier, policy in DEFAULT_TIER_POLICY.items()},
        "targets": {k: list(v) for k, v in _DEFAULT_TARGETS.items()},
        "advisory_scan": [],
        "_source": "defaults",
    }


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """config を atomic に書き込む（tmp+rename）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tmp_path.replace(path)


def load_tiers_config(
    strict: bool = False, *, config_path: Optional[Path] = None
) -> Dict[str, Any]:
    """config 全体を返す。

    - ファイル不在 → DEFAULT ベースの dict（``targets`` は空、``_source="defaults"``）。
    - JSON 破損/スキーマ不正（``tiers`` 欠落/非 dict）:
      ``strict=True`` はパス入りメッセージで ``ValueError`` を raise。
      ``strict=False`` は DEFAULT へ fail-open し ``_load_error`` に理由を格納する
      （audit gate 等の呼び出し元を絶対に落とさないため）。
    """
    path = config_path if config_path is not None else tiers_config_path()

    if not path.is_file():
        return _default_config()

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        if strict:
            raise ValueError(f"model-tiers.json を読み込めません: {path}: {e}") from e
        cfg = _default_config()
        cfg["_load_error"] = f"read error: {e}"
        return cfg

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        if strict:
            raise ValueError(
                f"model-tiers.json の JSON が破損しています: {path}: {e}"
            ) from e
        cfg = _default_config()
        cfg["_load_error"] = f"json decode error: {e}"
        return cfg

    if not isinstance(data, dict) or not isinstance(data.get("tiers"), dict):
        if strict:
            raise ValueError(
                f"model-tiers.json のスキーマが不正です（'tiers' 欠落/不正）: {path}"
            )
        cfg = _default_config()
        cfg["_load_error"] = "invalid schema: 'tiers' missing or not an object"
        return cfg

    data = dict(data)
    data.setdefault("version", CONFIG_VERSION)
    data.setdefault("targets", {k: list(v) for k, v in _DEFAULT_TARGETS.items()})
    data.setdefault("advisory_scan", [])
    data["_source"] = "file"
    data.pop("_load_error", None)
    return data


def load_tier_policy(
    strict: bool = False, *, config_path: Optional[Path] = None
) -> Dict[str, Dict[str, Optional[str]]]:
    """gate 互換形（description を落とした {model, effort}）を返す。"""
    cfg = load_tiers_config(strict=strict, config_path=config_path)
    tiers = cfg.get("tiers") or {}
    return {
        tier: {"model": policy.get("model"), "effort": policy.get("effort")}
        for tier, policy in tiers.items()
    }


def set_tier(
    tier: str,
    model: str,
    effort: Optional[str],
    *,
    config_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """config の該当 tier を更新する（不在なら DEFAULT から生成して作成）。

    バリデーション:
    - model は既知エイリアスのみ（``agent_quality_catalog.MODEL_ALIASES``）。
      "inherit" は tier の model として不可。exact ID（claude-sonnet-5 等）は拒否。
    - effort は ``low``/``medium``/``high``/``xhigh``/``max`` または None。
    - model=haiku で effort が非 None は拒否（haiku は effort 非対応）。

    Returns:
        ``{"tier", "old", "new", "path"}``。atomic write（tmp+rename）。
    """
    tier_key = str(tier).strip().upper()
    if tier_key not in TIER_ORDER:
        raise ValueError(f"未知の tier: {tier!r}（有効: {', '.join(TIER_ORDER)}）")

    model_key = str(model).strip().lower()
    if model_key == "inherit" or model_key not in MODEL_ALIASES:
        if EXACT_MODEL_ID_PATTERN.match(str(model).strip()):
            raise ValueError(
                f"model に exact ID は指定できません: {model!r}。"
                f"エイリアスを使ってください（{', '.join(ALLOWED_TIER_MODELS)}）"
            )
        raise ValueError(
            f"model は既知エイリアスのみ指定できます"
            f"（{', '.join(ALLOWED_TIER_MODELS)}）: {model!r}"
        )

    effort_key: Optional[str]
    if effort is None:
        effort_key = None
    else:
        effort_key = str(effort).strip().lower()
        if effort_key not in EFFORT_VALUES:
            raise ValueError(
                f"effort は {', '.join(sorted(EFFORT_VALUES))} のいずれかである必要があります: "
                f"{effort!r}"
            )

    if model_key == "haiku" and effort_key is not None:
        raise ValueError("haiku は effort 非対応です（--effort を指定しないでください）")

    path = config_path if config_path is not None else tiers_config_path()
    # strict=True: 破損 config を fail-open の defaults で黙って上書きすると
    # targets manifest が silent 消失するため、書込系は明示エラーで止める
    # （ファイル不在は strict でも defaults 生成なので新規作成フローは不変）。
    config = load_tiers_config(strict=True, config_path=path)
    config.pop("_source", None)
    config.pop("_load_error", None)
    config.setdefault("version", CONFIG_VERSION)
    if not config.get("tiers"):
        config["tiers"] = {t: dict(p) for t, p in DEFAULT_TIER_POLICY.items()}
    config.setdefault("targets", {k: list(v) for k, v in _DEFAULT_TARGETS.items()})
    config.setdefault("advisory_scan", [])

    old_entry = dict(config["tiers"].get(tier_key) or DEFAULT_TIER_POLICY[tier_key])
    new_entry = dict(config["tiers"].get(tier_key) or {})
    new_entry["model"] = model_key
    new_entry["effort"] = effort_key
    new_entry.setdefault("description", DEFAULT_TIER_POLICY[tier_key]["description"])
    config["tiers"][tier_key] = new_entry

    _atomic_write_json(path, config)

    return {"tier": tier_key, "old": old_entry, "new": dict(new_entry), "path": str(path)}


def init_config(*, config_path: Optional[Path] = None) -> Path:
    """config 不在時に DEFAULT + 空 targets の雛形を書く（既存なら拒否）。"""
    path = config_path if config_path is not None else tiers_config_path()
    if path.is_file():
        raise FileExistsError(f"既に存在します: {path}")
    config = _default_config()
    config.pop("_source", None)
    _atomic_write_json(path, config)
    return path
