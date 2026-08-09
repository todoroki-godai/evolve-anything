"""status=dead ストアへの store_write barrier 非経由 raw writer 不在の静的契約テスト
（#379 Step 3 レビュー対応）。

背景: PR #389 で status=dead に降格した quality-scores.jsonl / growth-journal.jsonl に、
store_write barrier を経由しない直接 open()/write_text() writer が live のまま残っていた
（quality_engine.record_quality_score / growth_journal.emit_crystallization）。status=dead は
「write barrier の write 許可は active のみ」という契約だが、barrier 非経由の直接書込は
この契約をすり抜ける。本テスト実装時の較正で、当初報告されていなかった第3の writer
（skills/implement/scripts/telemetry.py の record_growth_journal・SKILL.md の完了時記録
手順から呼ばれる）も growth-journal.jsonl に直接書いていることが判明し、同様にゲートした
（implement_backfill.py の一時 backfill writer も同様）。

以後、dead ストアに新たな未ゲート直接 writer が増えないことを静的に固定する。

検出対象: production コード（scripts/**/*.py + skills/**/*.py + hooks/**/*.py、tests/ 除外）で
dead ストアのファイル名を open(.../"a") または .write_text( で直接書く箇所。
ゲート済み writer 自身（allowlist）は除外する。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List

_lib_dir = Path(__file__).resolve().parent.parent
_root = _lib_dir.parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import store_registry  # noqa: E402

_EXCLUDE_DIR_PARTS = {"tests", "fixtures"}

# 未ゲート raw writer が既知で「有りうる」ことを許容するファイル（basename → 理由）。
# 新規追加時は理由を明記すること（モグラ叩き allowlist にしない）。
_ALLOWED_RAW_WRITER_FILES = {
    # ゲート済み writer 自身（is_dead_store チェックを冒頭に持つ・#379 Step 3 レビュー修正）。
    "scripts/lib/quality_engine.py",
    "scripts/lib/growth_journal.py",
    "skills/implement/scripts/telemetry.py",
    "skills/implement/scripts/implement_backfill.py",
}


def _dead_store_names() -> List[str]:
    return sorted(d.name for d in store_registry.declarations() if d.status == "dead")


def _iter_candidate_files() -> List[Path]:
    files: List[Path] = []
    for pattern in ("scripts/**/*.py", "skills/**/*.py", "hooks/**/*.py"):
        for p in _root.glob(pattern):
            if not p.is_file():
                continue
            rel = p.relative_to(_root)
            if set(rel.parts) & _EXCLUDE_DIR_PARTS:
                continue
            files.append(p)
    return files


def _write_pattern_for(name: str) -> re.Pattern:
    """basename を参照した append-mode open() または write_text() のゆるい近接検出。

    変数を経由した open(filepath, "a") 形が主流のため、ファイル名リテラルと write 呼び出しの
    両方が同一ファイルに存在するかで判定する（同ファイル内の他ストアと誤結合しないよう、
    リテラル自体を先に絞り込んでから write パターンの有無を見る2段判定は _has_live_writer 側）。
    """
    k = re.escape(name)
    return re.compile(rf'["\']({k})["\']')


_WRITE_CALL_RE = re.compile(r'open\([^)]*["\']a["\']|\.write_text\(')


def _has_live_writer(name: str, text: str) -> bool:
    if not _write_pattern_for(name).search(text):
        return False
    return bool(_WRITE_CALL_RE.search(text))


def _live_writers(name: str, files: List[Path]) -> List[str]:
    hits = []
    for f in files:
        rel_str = str(f.relative_to(_root))
        if rel_str in _ALLOWED_RAW_WRITER_FILES:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if _has_live_writer(name, text):
            hits.append(rel_str)
    return hits


def test_has_live_writer_detects_known_write_shapes() -> None:
    """検出パターンのサニティチェック（偽陰性ゼロの担保）。

    実較正で捕捉した write 形（open(path, "a") / write_text）を合成文字列で再現し、
    検出ロジック自体が機能していることを固定する（allowlist 側の変化に非依存）。
    """
    name = "example-dead-store.jsonl"
    assert _has_live_writer(
        name, 'path = dd / "example-dead-store.jsonl"\nwith open(path, "a") as f:\n    pass'
    )
    assert _has_live_writer(
        name, 'p = dd / "example-dead-store.jsonl"\np.write_text("x")'
    )
    # ファイル名の言及のみ（read や docstring）は検出しない。
    assert not _has_live_writer(name, '"""example-dead-store.jsonl の読み取りテスト"""')
    assert not _has_live_writer(
        name, 'with open("example-dead-store.jsonl") as f:\n    f.read()'
    )


def test_dead_store_population_is_nonempty() -> None:
    """較正の前提: status=dead ストアが少なくとも1件は存在する（population が空だと空振り）。"""
    assert _dead_store_names() != []


def test_dead_stores_have_no_unguarded_raw_writer() -> None:
    """status=dead の全ストアについて、allowlist 外の未ゲート直接 writer が存在しないこと。"""
    files = _iter_candidate_files()
    problems = {}
    for name in _dead_store_names():
        hits = _live_writers(name, files)
        if hits:
            problems[name] = hits
    assert problems == {}, (
        f"status=dead ストアへの allowlist 外 raw writer を検出しました: {problems}。"
        f"writer 関数冒頭に store_registry.is_dead_store ゲートを追加するか、"
        f"_ALLOWED_RAW_WRITER_FILES に理由付きで追加してください。"
    )


def test_gated_writer_functions_actually_check_is_dead_store() -> None:
    """allowlist の主要ゲート済み writer が is_dead_store 呼び出しを実際に含む（形骸化防止）。

    allowlist にファイルを足すだけでゲート実装を忘れる回帰を防ぐため、ゲート済みと
    主張する writer ファイルが is_dead_store 参照を実際に持つことを確認する。
    """
    gated_files = {
        "scripts/lib/quality_engine.py",
        "scripts/lib/growth_journal.py",
        "skills/implement/scripts/telemetry.py",
        "skills/implement/scripts/implement_backfill.py",
    }
    for rel in gated_files:
        text = (_root / rel).read_text(encoding="utf-8")
        assert "is_dead_store" in text, f"{rel} は is_dead_store ゲートを含んでいません"
