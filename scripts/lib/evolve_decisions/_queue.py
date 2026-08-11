"""pending キュー（emit/ingest 共有）helper（`evolve_decisions` パッケージ分割・#383）。

`resolve_slug` / `queue_path_for` / `read_queue` / `_write_queue` / `_queue_lock` を束ねる。
振る舞いはゼロ変更で、`evolve_decisions/__init__.py` が全名前を re-export し
`from evolve_decisions import X` の後方互換と `setattr(evolve_decisions, ...)` 束縛を保つ。

⚠️ 束縛フェンス（`evolve_decisions/__init__.py` docstring 参照）: `QUEUE_ROOT` は
`__init__.py`（パッケージ namespace）が正典。test の
`monkeypatch.setattr(evolve_decisions, "QUEUE_ROOT", ...)` を確実に効かせるため、本 module の
関数は呼び出し時に `import evolve_decisions as _ed; _ed.QUEUE_ROOT` で参照する
（module-top で `from evolve_decisions import QUEUE_ROOT` すると import 時点の値で凍結し
差し替えがすり抜ける）。
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import optimize_history_store as _store
from rl_common.file_lock import atomic_write_text, file_lock


def resolve_slug(cwd: Optional[Path] = None) -> str:
    """optimize_history_store と同じ worktree 安全 slug（書き込み先を一致させる）。"""
    return _store.resolve_slug(cwd)


def queue_path_for(slug: str) -> Path:
    import evolve_decisions as _ed

    return _ed.QUEUE_ROOT / f"{_store._sanitize_slug(slug)}.jsonl"


def read_queue(slug: str) -> List[Dict[str, Any]]:
    """slug の pending decisions を読む。未存在なら []。壊れた行はスキップ。"""
    path = queue_path_for(slug)
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _write_queue(slug: str, records: List[Dict[str, Any]]) -> None:
    """slug のキューを records で**上書き**する（emit は毎 run 現在バッチで置換）。"""
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    atomic_write_text(queue_path_for(slug), body)


@contextmanager
def _queue_lock(slug: str) -> Iterator[None]:
    """キューの RMW を直列化する（#287-1）。非ロックだと交差時に最後の上書きが勝ち、別 run の
    追加や drain 済み除去が消える。marker とは別ファイル・別ロックなので入れ子にできる。"""
    import evolve_decisions as _ed

    with file_lock(_ed.QUEUE_ROOT / f"{_store._sanitize_slug(slug)}.lock"):
        yield
