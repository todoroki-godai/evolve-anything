"""`_OBSERVABILITY_BUILDERS` 横断の契約テスト（#115 Phase 1）。

audit の observability builder は全て同じ contract に従う（observability.py / ADR-028）:
- その PJ に非該当なら None を返す（silence）。
- 該当する場合は `## <title>` で始まり末尾が空行の行リストを返す
  （header/trailer 規約 — advisory_header / finalize が単一ソース）。

このテストは `_OBSERVABILITY_BUILDERS` を registry から **動的に** 取り回して全 builder を
パラメトリックに回す。builder を 1 個足しても自動でカバーされ、Phase 2-5 で FITS 形へ
載せ替える際の回帰フェンスになる（header/trailer 規約を破ったら即赤）。

隔離: builder の多くは DATA_DIR / session_store / `Path.home()/.claude/projects` を走査する。
本テストは `scripts/lib/tests/` 配下に置くことで、root conftest の CLAUDE_PLUGIN_DATA 隔離と
`scripts/lib/tests/conftest.py` の autouse HOME 隔離（`_isolate_home_default`）が効き、
空の tmp 環境を走査して速く（多くが None）返る。plugin ソースを走査する builder
（orphan_store / testpaths 等）は当リポジトリ配下で bounded。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from audit.observability import _OBSERVABILITY_BUILDERS  # noqa: E402


def test_registry_is_non_empty() -> None:
    # フェンスが空回りしないことの自己検査。
    assert len(_OBSERVABILITY_BUILDERS) >= 20


def test_registry_keys_are_unique() -> None:
    keys = [key for key, _ in _OBSERVABILITY_BUILDERS]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize(
    "key,builder",
    _OBSERVABILITY_BUILDERS,
    ids=[key for key, _ in _OBSERVABILITY_BUILDERS],
)
def test_builder_honors_header_trailer_contract(key, builder, tmp_path: Path) -> None:
    """全 builder は None か、`## ` 始まり + 末尾空行の行リストを返す。"""
    result = builder(tmp_path)
    if result is None:
        return
    assert isinstance(result, list), f"{key}: 非 None は list を返すこと"
    assert result, f"{key}: 空リストは不可（None で沈黙すること）"
    assert result[0].startswith("## "), (
        f"{key}: 先頭は '## ' 見出しで始めること（実際: {result[0]!r}）"
    )
    assert result[-1] == "", (
        f"{key}: 末尾は空行で締めること（実際: {result[-1]!r}）"
    )
