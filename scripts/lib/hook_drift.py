"""hook_drift.py — 他ツール追従 hook の陳腐化（stale_pin）検出。

決定論・LLM 非依存。gstack のような外部ツールのフローを参照する hook が、
ツール本体の進化に追従できているかを検出する。

第一フェーズは **stale_pin** のみを実装する: `~/.gstack/flow-chain.json` が想定する
gstack バージョン（`gstack_version`）と、実環境の gstack バージョン
（`~/.gstack/.last-setup-version`）の乖離。flow-chain.json は **手動メンテされる SoT**
で gstack 本体は一切生成しない（実環境調査 #319 で判明 — gstack の setup/bin に
flow-chain.json への参照はゼロ。`gstack_version` は手書きのピン）。ピンが実環境 version
から取り残されると hook が古いフロー構成を提案し続けるため、`gstack_version` を手で
実環境 version に更新して解消する。

stale_pin を初手に選んだ理由（[ADR-035] / second-opinion レビュー）:
  - version 同士の単純突合で済み、スキル名の **表記ゆれによる false positive が無い**
  - dead_ref（参照先スキルの実在突合）や internal_drift（hook 内ハードコード突合）は
    表記ゆれ正規化の信頼性を固めるまで observability に乗せると audit のノイズ源になる
  - 有用性（follow-through）評価は別フェーズ（hook 側の発火 fire-log が前提）

将来拡張（dead_ref / internal_drift / follow-through）は別 issue。本モジュールは
表記ゆれの無い version 突合だけに責務を限定する。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

_FLOW_CHAIN = "flow-chain.json"
_SETUP_VERSION = ".last-setup-version"


def _default_gstack_dir() -> Path:
    """gstack のグローバル状態ディレクトリ（PJ 非依存）。

    module 定数でなく関数にして **呼び出し時に** HOME を評価する。こうすると
    test が `monkeypatch.setenv("HOME", ...)`（audit snapshot 系）や
    `monkeypatch.setattr(hook_drift, "_default_gstack_dir", ...)`（observability 契約系、
    eval_saturation の `_default_eval_sets_dir` と同じ慣習）で実機の ~/.gstack を
    確実に隔離できる。import 時に固定すると HOME 差し替えに追従できず実環境を読んでしまう。
    """
    return Path.home() / ".gstack"


@dataclass
class HookDriftReport:
    """stale_pin 検出結果。

    applicable: gstack 追従 hook の検査対象がこの環境に存在するか
                （.gstack と flow-chain.json の双方がある）。
    stale_pin:  pinned と actual が読めて、かつ不一致。
    minor_gap:  semantic version の MINOR 桁の差（解析できた場合のみ、絶対値）。
    """

    applicable: bool = False
    pinned_version: Optional[str] = None
    actual_version: Optional[str] = None
    stale_pin: bool = False
    minor_gap: int = 0


def _parse_version(text: str) -> Optional[Tuple[int, ...]]:
    """"1.47.0.0" → (1, 47, 0, 0)。数値以外が混じれば None。"""
    parts = text.strip().split(".")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def read_pinned_gstack_version(gstack_dir: Path) -> Optional[str]:
    """flow-chain.json の gstack_version を読む。不在・不正・キー欠落なら None。"""
    path = gstack_dir / _FLOW_CHAIN
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = data.get("gstack_version")
    return version if isinstance(version, str) and version.strip() else None


def read_actual_gstack_version(gstack_dir: Path) -> Optional[str]:
    """.last-setup-version を読む。不在・空なら None。"""
    path = gstack_dir / _SETUP_VERSION
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def check_hook_drift(gstack_dir: Optional[Path] = None) -> HookDriftReport:
    """gstack 追従 hook の stale_pin を検査する（決定論）。

    - .gstack が無い / flow-chain.json が無い → applicable=False（沈黙対象）
    - flow-chain.json はあるが .last-setup-version が読めない → 判定不能。
      実 version 不明を stale と誤検知しない（stale_pin=False のまま applicable=True）
    - 両 version が読めて不一致 → stale_pin=True、解析できれば minor_gap を算出
    """
    gdir = gstack_dir if gstack_dir is not None else _default_gstack_dir()
    if not gdir.is_dir():
        return HookDriftReport(applicable=False)

    pinned = read_pinned_gstack_version(gdir)
    if pinned is None:
        # flow-chain.json が無い＝追従対象の hook フローが無い → 対象外。
        return HookDriftReport(applicable=False)

    actual = read_actual_gstack_version(gdir)
    report = HookDriftReport(
        applicable=True, pinned_version=pinned, actual_version=actual
    )
    if actual is None:
        return report  # 実 version 不明 → 判定不能（stale 断定しない）

    if pinned == actual:
        return report  # 一致 → drift なし

    report.stale_pin = True
    pv, av = _parse_version(pinned), _parse_version(actual)
    if pv is not None and av is not None and len(pv) >= 2 and len(av) >= 2:
        report.minor_gap = abs(av[1] - pv[1])
    return report
