"""pitfall-curate の管理対象レジストリ — どの pitfalls.md を hook が監視するか。

install で lint hook はプラグインに同梱・配布されるが、各 PJ で `enable` を 1 回叩いて
対象ファイルを登録するまで hook は何もしない（オプトイン設計）。これにより「最新版を入れて、
コマンドを 1 回打つと、以後 pitfalls の追加/修正/削除に自動でルールが当たる」を実現する。

レジストリは PJ 直下の `.claude/rl-anything/pitfall-managed.json` に、project_dir からの
相対パス（プロジェクト外は絶対パス）で保存する。LLM は呼ばない・決定論。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Union

_REGISTRY_REL = ".claude/rl-anything/pitfall-managed.json"

# 探索時に降りない重いディレクトリ。pitfalls.md がここに紛れても監視対象ではない。
# worktrees: `.claude/worktrees/<name>/...` は一時的な作業コピー（git worktree）であり、
# 本体スキルの pitfalls.md と同一内容のコピーを「未登録」と誤検知する源（#393）。
# 恒久管理対象は本体のみなので探索から除外する。
_DISCOVERY_IGNORE = {
    ".git", "node_modules", "dist", "build", "target",
    ".venv", "venv", "__pycache__", ".next", ".cache",
    "worktrees",
}

PathLike = Union[str, Path]


def _registry_path(project_dir: PathLike) -> Path:
    return Path(project_dir) / _REGISTRY_REL


def _to_key(project_dir: PathLike, pitfalls_path: PathLike) -> str:
    """pitfalls_path を台帳キー（project_dir 相対、外部は絶対）へ正規化する。"""
    pd = Path(project_dir).resolve()
    pp = Path(pitfalls_path).resolve()
    try:
        return str(pp.relative_to(pd))
    except ValueError:
        return str(pp)


def load_managed(project_dir: PathLike) -> List[str]:
    """管理対象キーの一覧を返す。未登録/破損時は空（例外を投げない）。"""
    p = _registry_path(project_dir)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    managed = data.get("managed", []) if isinstance(data, dict) else []
    return [str(x) for x in managed if isinstance(x, str)]


def is_managed(project_dir: PathLike, pitfalls_path: PathLike) -> bool:
    return _to_key(project_dir, pitfalls_path) in load_managed(project_dir)


def _write(project_dir: PathLike, keys: List[str]) -> None:
    p = _registry_path(project_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"managed": sorted(set(keys))}, ensure_ascii=False, indent=2)
    p.write_text(body + "\n", encoding="utf-8")


def add_managed(project_dir: PathLike, pitfalls_path: PathLike) -> bool:
    """登録する。新規なら True、既に登録済みなら False（no-op）。"""
    key = _to_key(project_dir, pitfalls_path)
    current = load_managed(project_dir)
    if key in current:
        return False
    _write(project_dir, current + [key])
    return True


def remove_managed(project_dir: PathLike, pitfalls_path: PathLike) -> bool:
    """登録解除する。存在すれば True、無ければ False（no-op）。"""
    key = _to_key(project_dir, pitfalls_path)
    current = load_managed(project_dir)
    if key not in current:
        return False
    _write(project_dir, [k for k in current if k != key])
    return True


def discover_pitfalls(project_dir: PathLike) -> List[str]:
    """PJ 内の `pitfalls.md` 候補を project 相対パスでソートして返す（決定論）。

    skill が `enable` 対象を自動発見するための入口。`_DISCOVERY_IGNORE` 配下は
    監視対象になり得ないため降りない。配布版（pitfalls-top*.md）は別ファイル名なので
    自然に除外される。
    """
    pd = Path(project_dir).resolve()
    found: List[str] = []
    for p in pd.rglob("pitfalls.md"):
        rel = p.relative_to(pd)
        if any(part in _DISCOVERY_IGNORE for part in rel.parts):
            continue
        found.append(str(rel))
    return sorted(found)


def unmanaged_candidates(project_dir: PathLike) -> List[str]:
    """発見済み pitfalls.md のうち、まだ管理対象に未登録のものを返す（決定論）。

    `discover_pitfalls`（全候補）から `load_managed`（登録済み）を引いた集合差。
    audit が「自動強制の対象になり得るが未登録」のファイルを可視化するための入口。
    キーは discover_pitfalls と同じ project 相対パスなので集合差が一致する。
    エントリ数による liveness 判定は呼び出し側（audit）が行う — レジストリは
    フォーマットパーサに依存せず stdlib のみで完結させる。
    """
    managed = set(load_managed(project_dir))
    return [k for k in discover_pitfalls(project_dir) if k not in managed]
