"""hook_drift.py — 他ツール追従 hook の陳腐化検出（stale_pin / dead_ref）。

決定論・LLM 非依存。gstack のような外部ツールのフローを参照する hook が、
ツール本体の進化に追従できているかを検出する。

**stale_pin**（第一フェーズ・[ADR-035] / PR #315）: `~/.gstack/flow-chain.json` が想定する
gstack バージョン（`gstack_version`）と、実環境の gstack バージョン
（`~/.gstack/.last-setup-version`）の乖離。flow-chain.json は **手動メンテされる SoT**
で gstack 本体は一切生成しない（実環境調査 #319 で判明 — gstack の setup/bin に
flow-chain.json への参照はゼロ。`gstack_version` は手書きのピン）。ピンが実環境 version
から取り残されると hook が古いフロー構成を提案し続けるため、`gstack_version` を手で
実環境 version に更新して解消する。

**dead_ref**（第二フェーズ・#316）: flow-chain.json が参照する skill 名（chain の
ソースキー + 各 `next` の遷移先）が、どの live registry（~/.claude/skills /
rl-anything 本体 skills / インストール済みプラグイン skills）にも実在しないケース。
スキルが rename/削除されたのに flow-chain.json が古い名前を指し続けると、hook が
存在しないコマンドを提案する。

dead_ref を stale_pin より後回しにした理由（[ADR-035]）は **表記ゆれによる
false positive** だった。本実装はその核心を `normalize_skill_ref` に閉じ込め、
変換を契約テストで先に固定したうえで突合する。FP 厳禁の原則（precision 優先・
glossary_drift が undefined_terms を gate しない教訓と同様）として:
  - 正規化不能（空・記号のみ）の参照は dead_ref にしない
  - live registry が空（skill 列挙そのものに失敗）なら何も flag しない
    （検出器側の不備で全参照を dead に見せない）

internal_drift（hook 内ハードコード突合）/ follow-through（発火 fire-log）は別 issue。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

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

    applicable:    gstack 追従 hook の検査対象がこの環境に存在するか
                   （.gstack と flow-chain.json の双方がある）。
    stale_pin:     pinned と actual が読めて、かつ不一致。
    minor_gap:     semantic version の MINOR 桁の差（解析できた場合のみ、絶対値）。
    pinned_source: pinned_version をどのファイルから読んだか（evidence, #394）。
    actual_source: actual_version をどのファイルから読んだか（evidence, #394）。
                   assistant/ユーザーが独自検証する際、誤った fallback（flow-chain.json を
                   読み戻す等）を避けるため検出元パスを明示する。
    """

    applicable: bool = False
    pinned_version: Optional[str] = None
    actual_version: Optional[str] = None
    stale_pin: bool = False
    minor_gap: int = 0
    pinned_source: Optional[str] = None
    actual_source: Optional[str] = None


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
        applicable=True,
        pinned_version=pinned,
        actual_version=actual,
        # evidence（#394）: どのファイルから読んだかを明示する。actual は
        # `.last-setup-version` 由来であって `gstack --version` の PATH 解決ではない。
        pinned_source=str(gdir / _FLOW_CHAIN),
        actual_source=str(gdir / _SETUP_VERSION) if actual is not None else None,
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


# --- dead_ref（参照先スキルの実在突合・#316）---------------------------------


@dataclass(frozen=True)
class DeadRef:
    """flow-chain.json が参照する skill 名で live registry に存在しないもの。

    ref:        flow-chain.json に書かれた生の参照表記（evidence）。
    normalized: normalize_skill_ref で正規化した skill 名。
    source:     この参照が現れた chain のソースキー（どの遷移定義か）。
                chain のソースキー自体が dead な場合は source == normalized。
    """

    ref: str
    normalized: str
    source: str


def normalize_skill_ref(raw: str) -> Optional[str]:
    """参照表記を skill 名に正規化する（表記ゆれ吸収・#316 の核心）。

    変換規則（契約テスト test_normalize_skill_ref で固定）:
      - 前後空白を除去
      - 先頭の `/`（コマンド表記）を除去
      - `plugin:skill` 形式は `:` 以降の skill 名を採用
      - 引数（最初の空白以降）を除去 → skill 名のみ
    正規化できない（空・記号のみで残らない）場合は None を返し、呼び出し側で
    dead_ref 判定から除外する（FP 厳禁）。
    """
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    # 引数を落とす: 最初の空白までを参照トークンとする。
    token = text.split()[0] if text.split() else ""
    # 先頭の `/`（コマンド表記）を除去。
    token = token.lstrip("/")
    # `plugin:skill` の名前空間を剥がす（最後の `:` 以降を skill 名とする）。
    if ":" in token:
        token = token.rsplit(":", 1)[-1]
    token = token.strip()
    return token or None


def _user_skills_dir() -> Path:
    """ユーザー自作スキルのディレクトリ（~/.claude/skills）。

    _default_gstack_dir と同様、呼び出し時に HOME を評価して test 隔離を効かせる。
    """
    return Path.home() / ".claude" / "skills"


def _plugin_skill_names() -> frozenset:
    """インストール済みプラグインの skill 名集合（skill_origin に委譲）。

    skill_origin が import 不能（疎結合・テスト環境）でも壊れないよう握り潰す。
    """
    try:
        import skill_origin

        return skill_origin.get_plugin_skill_names()
    except Exception:
        return frozenset()


def _repo_self_skill_names() -> frozenset:
    """rl-anything 本体リポジトリの skills/ 直下の skill 名集合。

    `/rl-anything:implement` 等のプラグイン参照は通常 _plugin_skill_names でも拾えるが、
    開発中（未インストール）の本体 repo を実行している場合のフォールバックとして、
    このモジュールから見た repo ルートの skills/ も registry に併合する。
    """
    # hook_drift.py は scripts/lib/ 配下 → 親の親の親が repo ルート。
    repo_skills = Path(__file__).resolve().parent.parent.parent / "skills"
    if not repo_skills.is_dir():
        return frozenset()
    return frozenset(c.name for c in repo_skills.iterdir() if c.is_dir())


def build_live_skill_registry() -> frozenset:
    """実在する skill 名集合を構築する（live registry）。

    情報源を併合する:
      - ~/.claude/skills/ のサブディレクトリ名（ユーザー自作・gstack 配布スキル）
      - rl-anything 本体 repo の skills/（開発中フォールバック）
      - インストール済みプラグインの skill 名（skill_origin 経由）
    """
    names: set = set()
    user_dir = _user_skills_dir()
    if user_dir.is_dir():
        names.update(c.name for c in user_dir.iterdir() if c.is_dir())
    names.update(_repo_self_skill_names())
    names.update(_plugin_skill_names())
    return frozenset(names)


def detect_dead_refs(gstack_dir: Optional[Path] = None) -> List[DeadRef]:
    """flow-chain.json の参照スキルで live registry に無いものを返す（決定論）。

    FP 厳禁（precision 優先）:
      - flow-chain.json が無ければ空（沈黙対象）
      - live registry が空（skill 列挙に失敗）なら何も flag しない
      - 正規化不能の参照は除外する
    """
    gdir = gstack_dir if gstack_dir is not None else _default_gstack_dir()
    path = gdir / _FLOW_CHAIN
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    chain = data.get("chain")
    if not isinstance(chain, dict):
        return []

    registry = build_live_skill_registry()
    if not registry:
        # skill 列挙そのものに失敗 → 全参照を dead に見せないため沈黙する。
        return []

    dead: List[DeadRef] = []
    seen: set = set()  # (normalized, source) 重複排除

    def _check(raw_ref: str, source: str) -> None:
        normalized = normalize_skill_ref(raw_ref)
        if normalized is None:
            return  # 正規化不能 → FP を避けて除外
        if normalized in registry:
            return
        key = (normalized, source)
        if key in seen:
            return
        seen.add(key)
        dead.append(DeadRef(ref=raw_ref, normalized=normalized, source=source))

    for source_key, node in chain.items():
        # chain のソースキー自体も skill 名（参照する側）。実在突合する。
        if isinstance(source_key, str):
            _check(source_key, source_key)
        if not isinstance(node, dict):
            continue
        nexts = node.get("next")
        if not isinstance(nexts, list):
            continue
        for ref in nexts:
            if isinstance(ref, str):
                _check(ref, source_key)

    return dead
