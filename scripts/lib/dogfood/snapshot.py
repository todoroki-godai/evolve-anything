"""DATA_DIR の SHA256 スナップショット比較（#496 Layer 1a）。

dry-run の evolve が実環境 DATA_DIR を一切書き換えないことを assert するために、
実行前後の全ファイルを SHA256 でスナップショットし差分を取る。

前例: learning_dryrun_verification_blind_spot（#400）「完了基準は store 差分」の体系化。
#491 で dry-run が 4 ファイル（evolve_pending marker / audit-history.jsonl /
skill-evolve-cache.json / evolve-state.json）を書き換えることが実測済みで、本ゲートが
これを赤として検出するのが受け入れ基準。

#496 改善（隔離コピー方式）:
snapshot_dir に exclude_names 引数を追加し、layer1.CACHE_EXCLUDE_NAMES に列挙された
意図された dry-run 書込（cache warm ファイル）を比較対象から恒久除外できるようにした。
さらに exclude_json_keys 引数で「実 state も持つ共有 JSON ファイル」内の cache キーだけを
正規化除外する（layer1.CACHE_EXCLUDE_JSON_KEYS）。ファイル丸ごと除外すると同居する実 state の
dry-run 書込バグを隠すため、JSON トップレベルキー単位で除外し他キーの変更は検出を維持する。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, FrozenSet, List, Mapping, Optional, Set

# スナップショット対象から外すノイズ（環境ごとに揺れる / dry-run と無関係）。
_IGNORE_NAMES: FrozenSet[str] = frozenset({".DS_Store"})
_IGNORE_DIR_PARTS: FrozenSet[str] = frozenset({"__pycache__"})


def _iter_files(
    root: Path,
    exclude_names: Optional[FrozenSet[str]] = None,
    exclude_path_prefixes: Optional[FrozenSet[str]] = None,
):
    """root 配下のファイルを再帰的に yield する。

    exclude_names が指定された場合、そのファイル名（basename）はスキップする。
    exclude_path_prefixes が指定された場合、相対パス（posix）がいずれかの prefix で
    始まるファイルはスキップする（ディレクトリ配下を丸ごと除外する用途）。
    これにより意図された dry-run 書込（cache ファイル / 運用ポインタ dir 等）を diff から除外できる。
    """
    effective_exclude = _IGNORE_NAMES | (exclude_names or frozenset())
    prefixes = exclude_path_prefixes or frozenset()
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.name in effective_exclude:
            continue
        rel = p.relative_to(root).as_posix()
        if any(rel.startswith(prefix) for prefix in prefixes):
            continue
        if any(part in _IGNORE_DIR_PARTS for part in p.relative_to(root).parts):
            continue
        yield p


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _hash_json_excluding_keys(path: Path, exclude_keys: FrozenSet[str]) -> str:
    """JSON ファイルから exclude_keys（トップレベル）を除いて正規化ハッシュを返す。

    パース不能 / トップレベルが dict でない場合は通常の raw ハッシュにフォールバックする
    （正規化を諦めるだけで検出は維持する）。dict の場合は除外キーを抜き、sort_keys で
    正規化した JSON 文字列を SHA256 する（cache キー以外の変更は検出される）。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        return _hash_file(path)
    if not isinstance(data, dict):
        return _hash_file(path)
    normalized = {k: v for k, v in data.items() if k not in exclude_keys}
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def snapshot_dir(
    root: Path,
    exclude_names: Optional[FrozenSet[str]] = None,
    exclude_json_keys: Optional[Mapping[str, FrozenSet[str]]] = None,
    exclude_path_prefixes: Optional[FrozenSet[str]] = None,
) -> Dict[str, str]:
    """``root`` 配下の全ファイルを相対パス→SHA256 の dict で返す。

    存在しない dir は空 dict（差分計算の対称性のため例外にしない）。

    Args:
        root: スナップショット対象ディレクトリ。
        exclude_names: スキップするファイル名（basename）の集合。
            layer1.CACHE_EXCLUDE_NAMES を渡すことで意図された dry-run 書込
            （skill-evolve-cache.json 等）を比較対象から除外できる。
        exclude_json_keys: ファイル名（basename）→ そのファイルの JSON から除外する
            トップレベルキー集合の対応表。layer1.CACHE_EXCLUDE_JSON_KEYS を渡すことで、
            実 state も持つ共有 JSON ファイル（evolve-state.json 等）内の cache キーだけを
            正規化除外できる（ファイル丸ごと除外と違い同居する実 state の変更は検出する）。
        exclude_path_prefixes: 相対パス（posix）がいずれかの prefix で始まるファイルを除外する。
            layer1.CACHE_EXCLUDE_PATH_PREFIXES を渡すことで意図された運用ポインタ dir
            （evolve_pending/ 等）配下を丸ごと比較対象から除外できる。
    """
    root = Path(root)
    if not root.exists() or not root.is_dir():
        return {}
    exclude_json_keys = exclude_json_keys or {}
    snap: Dict[str, str] = {}
    for p in _iter_files(
        root,
        exclude_names=exclude_names,
        exclude_path_prefixes=exclude_path_prefixes,
    ):
        rel = p.relative_to(root).as_posix()
        try:
            json_keys = exclude_json_keys.get(p.name)
            if json_keys:
                snap[rel] = _hash_json_excluding_keys(p, json_keys)
            else:
                snap[rel] = _hash_file(p)
        except OSError:
            # 走査中に消えた一時ファイル等はスキップ（diff の安定性優先）。
            continue
    return snap


def diff_snapshots(before: Dict[str, str], after: Dict[str, str]) -> Dict[str, List[str]]:
    """2 スナップショットの added / removed / modified を返す（相対パスソート済み）。"""
    before_keys = set(before)
    after_keys = set(after)
    added = sorted(after_keys - before_keys)
    removed = sorted(before_keys - after_keys)
    modified = sorted(k for k in before_keys & after_keys if before[k] != after[k])
    return {"added": added, "removed": removed, "modified": modified}


def is_unchanged(diff: Dict[str, List[str]]) -> bool:
    """diff が完全に空（差分ゼロ）なら True。"""
    return not (diff.get("added") or diff.get("removed") or diff.get("modified"))
