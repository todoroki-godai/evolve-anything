#!/usr/bin/env python3
"""hook-writer 系ストアの dir 解決（#358）。

PostToolUse 等の hook は CC が設定する ``CLAUDE_PLUGIN_DATA`` 配下
(``~/.claude/plugins/data/<marketplace>-<plugin>``、plugin-data dir) に
usage.jsonl / skill_activations.jsonl 等を書き込む。しかし standalone な
tool/skill（prune・audit・discover 等）実行時は env が未設定で、
``rl_common.DATA_DIR`` が既定 fallback ``~/.claude/evolve-anything`` に解決される。

結果、hook が書いた live テレメトリと、tool が読む fallback dir が食い違い、
prune が「全スキル zero_invocation」と誤判定する（#358）。tool/skill 系ストア
（corrections / evolve-state / eval-sets 等）は逆に fallback が正準なので、
DATA_DIR を一斉にスイッチすると今度はそれらが空に見える。よって本 resolver は
**hook-writer 系ストア限定** で「hook が書く dir」を解決する。

解決順（決定論）:
  1. base が既定 fallback **以外** に明示設定されていればそれを尊重（最優先）。
     hook 実行時の凍結 DATA_DIR(=plugin-data)・custom 環境・テストの DATA_DIR
     patch がここで効く。env より優先することで、テスト isolation（conftest が
     CLAUDE_PLUGIN_DATA=tmp_path を強制）下でも個別テストの DATA_DIR patch を壊さない。
  2. base が既定 fallback のとき ``CLAUDE_PLUGIN_DATA`` env があればそこ（hook 実行）
  3. base が既定 fallback かつ env も無いとき install レイアウト
     ``~/.claude/plugins/data/<*evolve-anything*>`` を探索（tool/skill 実行で
     hook の書いた dir を回収）
  4. 探索失敗時は base（既定 fallback）を返す（後方互換・graceful degrade）

LLM 非依存・決定論・副作用なし（dir を作らない／読むだけ）。
DATA_DIR / PLUGIN_DATA_BASE は ``rl_common`` 経由で call-time 参照し、テストの
``mock.patch.object`` に追従する（eval_saturation._default_eval_sets_dir と同様）。

注意: 全体一元化（DATA_DIR を一本化し migration）は別 issue（Phase 2）。本 fix は
読み取り経路のみを正準化する最小修正（#358）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# plugin-data dir の親（install レイアウト）。テストが mock.patch.object で差し替える。
PLUGIN_DATA_BASE = Path.home() / ".claude" / "plugins" / "data"

# DATA_DIR の既定 fallback（env 未設定時の rl_common.DATA_DIR）。
# base がこれと一致するときだけ install レイアウトを probe する。
_REAL_DEFAULT_FALLBACK = Path.home() / ".claude" / "evolve-anything"
# symlink / 相対パスの差で `!=` 比較が崩れないよう resolve() 済みも保持して突合する。
_REAL_DEFAULT_FALLBACK_RESOLVED = _REAL_DEFAULT_FALLBACK.resolve()

# install dir 名 ``<marketplace>-<plugin>`` の plugin 名部分（固定）。
_PLUGIN_NAME = "evolve-anything"


def hook_store_dir(base: Optional[Path] = None) -> Path:
    """hook（PostToolUse 等）が書き込む plugin-data dir を解決する。

    Args:
        base: 既定の解決基点。省略時は ``rl_common.DATA_DIR``（call-time）。
              呼び出し側が自前の DATA_DIR binding（例: ``audit.DATA_DIR``）を
              持つ場合はそれを渡す（テストの module 別 patch に追従するため）。

    env → 明示 base 尊重 → install レイアウト探索 → base の順。副作用なし。
    """
    import rl_common  # patch 追従のため遅延参照

    if base is None:
        base = rl_common.DATA_DIR
    base = Path(base)

    # 1. base が既定 fallback 以外 = 明示指定（hook の凍結 DATA_DIR / custom 環境 /
    #    テストの DATA_DIR patch）。env より優先して尊重する（probe しない）。
    #    symlink / 相対パス差で比較が崩れないよう resolve() 済みで突合する。
    if base.resolve() != _REAL_DEFAULT_FALLBACK_RESOLVED:
        return base

    # 1.5 一元化 marker（#364 Phase 2）: migration 済み環境では hook も正準 dir に
    #     書くため、env/probe で旧 plugin-data dir を返すと「migration 済みの空ストア」
    #     を読んでしまう。marker があれば base（=正準）をそのまま返す。
    marker_name = getattr(rl_common, "DATA_DIR_UNIFIED_MARKER", ".data-dir-unified")
    try:
        if (base / marker_name).exists():
            return base
    except OSError:
        pass

    # 2. env（hook が設定する plugin-data）。base が既定 fallback のときのみ。
    env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    if env:
        return Path(env)

    # 3. install レイアウト探索（tool/skill 実行で hook の書いた dir を回収）。
    probed = _probe_install_layout(getattr(rl_common, "PLUGIN_DATA_BASE", PLUGIN_DATA_BASE))
    if probed is not None:
        return probed

    # 4. 後方互換: 既定 fallback。
    return base


def hook_store_path(filename: str, base: Optional[Path] = None) -> Path:
    """hook-writer 系ストア ``filename`` の実体パスを返す（dir は作らない）。"""
    return hook_store_dir(base) / filename


def _probe_install_layout(base: Path) -> Optional[Path]:
    """``base`` 配下から evolve-anything の plugin-data dir を決定論で 1 つ選ぶ。

    候補が複数あれば mtime 降順 → 名前昇順で最も新しいものを採る。無ければ None。
    """
    try:
        if not Path(base).is_dir():
            return None
        candidates = [
            d for d in Path(base).iterdir()
            if d.is_dir() and _PLUGIN_NAME in d.name
        ]
    except OSError:
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda d: (-_safe_mtime(d), d.name))
    # 候補が複数 = 命名規約変更 or 複数 marketplace 併存の兆候。誤選択の可視化のため警告。
    if len(candidates) > 1:
        print(
            "[evolve-anything] multiple plugin-data dirs matched "
            f"{[c.name for c in candidates]}; using {candidates[0].name}",
            file=sys.stderr,
        )
    return candidates[0]


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
