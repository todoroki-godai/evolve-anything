"""CULLED_OBSERVABILITY_SECTIONS の各キーに非表示 consumer が無いことの静的契約テスト。

背景（#379 レビュー指摘 P1 再発防止）: collect_observability() の淘汰 skip は builder
評価そのものを止めるため、淘汰キーを構造化出力（``result["observability"][key]``）から
読む consumer が存在すると、その consumer が沈黙する（measurement_bug / glossary_drift が
実際にこれで壊れかけた）。淘汰リストに新しいキーを足すたびに人手 grep で確認するのは
再発を防げないため、production コード・スキル文書を静的走査して機械的に検証する。

検出対象パターン（散文/コード両方を拾う）:
  - ``observability.<key>``（散文での言及・dot access 両対応）
  - ``obs.get("<key>")`` / ``obs.get('<key>')``
  - ``observability.get("<key>")`` / ``observability.get('<key>')``
  - ``observability"]["<key>"``（``result["observability"]["<key>"]`` 形の dict access）

除外対象: tests/ 配下・conftest.py・shrink_freeze.py 自身・builder 定義そのものである
``audit/sections*.py``・単一ソース登録元の ``audit/observability.py``・markdown 経由
consumer である ``audit/report.py``・fixtures。

較正: 実コーパスで dry-run した結果、現状の CULLED_OBSERVABILITY_SECTIONS 33 キー全件で
false positive はゼロだった（本テスト実装時点。#379 レビュー指摘対応）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
_root = _lib_dir.parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import shrink_freeze as sf  # noqa: E402

_EXCLUDE_DIR_PARTS = {"tests", "fixtures"}
_EXCLUDE_FILENAMES = {"conftest.py", "shrink_freeze.py"}
_EXCLUDE_PATH_PREFIXES = (
    "scripts/lib/audit/sections",
    "scripts/lib/audit/observability.py",
    "scripts/lib/audit/report.py",
)


def _iter_candidate_files() -> List[Path]:
    files: List[Path] = []
    for pattern in ("scripts/**/*.py", "skills/**/*.py", "skills/**/*.md"):
        for p in _root.glob(pattern):
            if not p.is_file():
                continue
            rel = p.relative_to(_root)
            if set(rel.parts) & _EXCLUDE_DIR_PARTS:
                continue
            if p.name in _EXCLUDE_FILENAMES:
                continue
            rel_str = str(rel)
            if any(rel_str.startswith(prefix) for prefix in _EXCLUDE_PATH_PREFIXES):
                continue
            files.append(p)
    return files


def _patterns_for(key: str) -> List[re.Pattern]:
    k = re.escape(key)
    return [
        re.compile(rf'\bobservability\.{k}\b'),
        re.compile(rf'obs\.get\(\s*["\']{k}["\']\s*\)'),
        re.compile(rf'observability\.get\(\s*["\']{k}["\']\s*\)'),
        re.compile(rf'observability"\]\["{k}"\]'),
    ]


def _hidden_consumers(key: str, files: List[Path]) -> List[str]:
    patterns = _patterns_for(key)
    hits = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if any(pat.search(text) for pat in patterns):
            hits.append(str(f.relative_to(_root)))
    return hits


@pytest.mark.parametrize("key", sorted(sf.CULLED_OBSERVABILITY_SECTIONS))
def test_culled_key_has_no_hidden_consumer(key: str) -> None:
    """淘汰キーを構造化出力から読む非表示 consumer が存在しないこと。

    見つかったら KEEP に再分類するか consumer 自体を削除する必要がある
    （measurement_bug / glossary_drift の再発パターン）。
    """
    files = _iter_candidate_files()
    hits = _hidden_consumers(key, files)
    assert hits == [], (
        f"culled key {key!r} を構造化出力から読む非表示 consumer を検出しました: {hits}。"
        f"KEEP に再分類するか（shrink_freeze.CULLED_OBSERVABILITY_SECTIONS から除去）、"
        f"consumer 側を削除してください。"
    )


def test_patterns_detect_known_keep_consumers() -> None:
    """検出パターン自体が機能していることのサニティチェック（偽陰性ゼロの担保）。

    KEEP に再分類済みの measurement_bug / glossary_drift は実際に非表示 consumer を
    持つ既知ケースなので、パターンがこれらを検出できないと契約テスト自体が無意味になる。
    """
    files = _iter_candidate_files()
    assert _hidden_consumers("measurement_bug", files) != []
    assert _hidden_consumers("glossary_drift", files) != []
