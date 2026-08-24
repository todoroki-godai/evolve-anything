"""scripts/phase1_codex_probe.py のテスト（ADR-055 Phase 1・#534）。

決定論・LLM 非依存（LLM は本スクリプトから一切呼ばない設計）。
``verify-checks-by-breaking.md`` に従い、各テストの docstring に
「壊す不変条件」と「通したい検査経路」を明記する。①〜④の変異と、ADR
Test Plan C-2 の追加変異（#5〜#9）を実際に適用して赤くなることを確認済み
（結果は実装完了報告に記載。本ファイルには正実装に対する検査のみを残す）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import unicodedata
from datetime import date
from pathlib import Path
from typing import List, Tuple

import pytest

# #536 round6 I3/I6: pytester fixture（pytest 内蔵プラグイン）を使い、skipif
# decorator の配線そのものを統合テストする。testpaths 側 pytest.ini には
# `-p pytester` を追加せず（全体テストに影響する変更を避ける）、このモジュール
# だけで有効化する。
pytest_plugins = ["pytester"]

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import phase1_codex_probe as p  # noqa: E402


# ─────────────────────────────────────────────────────────────────
# real_home テスト用 skip ガード（#536 team-lead 追加確認・round5 で強化）
#
# real_home マーカーは root conftest の HOME 隔離を opt-out するだけで、対象
# ディレクトリ（実 ~/.codex/sessions）の実在は保証しない。実在チェックを
# 怠ると、実 Codex セッションが無い環境（CI ランナー・別ユーザーの開発機等）
# では target_files=0 のまま _assert_stage_counts_plausible の下限チェック
# （227件）に引っかかり、意味のある失敗ではなく「環境にデータが無い」だけの
# ことで赤くなる。実測（HOME を実 ~/.codex を含まない一時ディレクトリへ
# 差し替えて実行）で target_files=0 → 2/4 の real_home テストが実際に FAILED
# することを確認済み。
#
# round5 codex Must I3: 旧版は「ディレクトリが存在するか」だけを見ていたため、
# 空ディレクトリ（存在するが対象14日窓に JSONL が無い）では skip されず実行を
# 選び、target_files=0 のまま下限チェックで落ちる（Should 指摘）。ディレクトリ
# 存在ではなく、real_home テストが実際に使う固定14日窓（2026-08-23 起点・
# run_probe の呼び出し引数と同一値）に対象 JSONL が実在するかで判定する。
_REAL_CODEX_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"
_REAL_HOME_TEST_BASE_DATE = date(2026, 8, 23)
_REAL_HOME_TEST_DAYS = 14


def _real_codex_sessions_available(
    sessions_root: Path = _REAL_CODEX_SESSIONS_ROOT,
    base_date: date = _REAL_HOME_TEST_BASE_DATE,
    days: int = _REAL_HOME_TEST_DAYS,
) -> bool:
    """real_home テストの skip 条件（#536 round5 Should）。

    ディレクトリの存在だけでなく、固定14日窓に対象 JSONL が実在するかで判定
    する。skip 条件を評価する関数として切り出すのは、real_home マーカーの
    外（通常テスト実行）から直接検査できるようにするため（round5 codex Must
    I3: 「条件自体を real_home でないテストから検証していないため『常に
    skip』変異を検出できない」への対応）。
    """
    return len(p.iter_date_dir_files(sessions_root, base_date, days)) > 0


def _real_home_skip_reason(
    sessions_root: Path, base_date: date, days: int
) -> str:
    """skip 理由文字列の単一ソース（#536 round6 I8）。

    real_home テスト自身が窓（sessions_root/base_date/days）を再度直書きすると、
    guard 側の窓だけを更新したときに両者が乖離する（Should I8）。理由文字列も
    含め _real_home_skip_marker を単一の呼び出し口とし、real_home テスト側は
    個別の窓を書かず _REAL_CODEX_SESSIONS_ROOT / _REAL_HOME_TEST_BASE_DATE /
    _REAL_HOME_TEST_DAYS のみを参照する。
    """
    return (
        f"{sessions_root} の {base_date} 起点 "
        f"{days}日窓に対象 JSONL がありません。real_home テストは実 "
        "Codex セッションが存在する環境でのみ意味を持つ検査のため skip します（#536）。"
    )


def _real_home_skip_marker(
    sessions_root: Path = _REAL_CODEX_SESSIONS_ROOT,
    base_date: date = _REAL_HOME_TEST_BASE_DATE,
    days: int = _REAL_HOME_TEST_DAYS,
):
    """skipif マーカーの構築を関数として切り出す（#536 round6 I3/I6）。

    旧版は `_skip_if_no_real_codex_sessions = pytest.mark.skipif(not
    _real_codex_sessions_available(), ...)` を import 時に固定評価しており、
    「`_real_codex_sessions_available()` の**呼び出し配線**そのもの
    （decorator 式が実際にこの関数の結果を使っているか）」を検査する手段が
    無かった（round5 codex Must I3/I6: `not _real_codex_sessions_available()`
    を `True` に固定する変異は、helper 単体テストや `-ra` 静的テストでは
    検出できない）。関数化することで、
    `test_real_home_skip_wiring_available_vs_unavailable`（pytester 経由）が
    available=True/False それぞれで実際に pytest 収集させ、非skip/理由付きskip
    になることを統合テストできる。
    """
    available = _real_codex_sessions_available(sessions_root, base_date, days)
    reason = _real_home_skip_reason(sessions_root, base_date, days)
    return pytest.mark.skipif(not available, reason=reason)


# skip 理由は必ず出す（silence != evaluated）。黙って skip して緑に見せない。
# pytest.ini の addopts に `-ra` を追加し、通常実行でも skip 理由サマリが
# 必ず表示されるようにする（round5 codex Must I3。設定が消えたら
# test_pytest_ini_reports_skip_reasons が検出する）。
_skip_if_no_real_codex_sessions = _real_home_skip_marker()


def test_real_codex_sessions_available_false_for_empty_window(tmp_path):
    """壊す不変条件: I3 Should（skip 条件が『ディレクトリ存在』でなく『対象14日窓に
    実在 JSONL があるか』で判定される）
    ／通したい検査経路: _real_codex_sessions_available（real_home マーカーの外・
    通常テストから直接検査）。
    ディレクトリは存在するが対象日付ディレクトリが無いケースで False になる
    ことを確認する。旧実装（`sessions_root.exists()` のみ）はこのケースで
    True を返し本テストが落ちる。
    """
    root = tmp_path / "sessions"
    root.mkdir()
    assert _real_codex_sessions_available(root, date(2026, 8, 23), 14) is False


def test_real_codex_sessions_available_true_when_window_has_files(tmp_path):
    """陽性対照: 対象14日窓に JSONL が実在すれば True になる。"""
    root = tmp_path / "sessions"
    date_dir = root / "2026" / "08" / "20"
    date_dir.mkdir(parents=True)
    (date_dir / "f.jsonl").write_text("{}\n", encoding="utf-8")
    assert _real_codex_sessions_available(root, date(2026, 8, 23), 14) is True


def test_real_codex_sessions_available_ignores_files_outside_window(tmp_path):
    """陰性試験: 窓外の日付ディレクトリに JSONL があっても対象窓が空なら False。
    （分散・入替変異相当: ファイルは実在するが対象期間の外という状態を固定する）
    """
    root = tmp_path / "sessions"
    out_of_window_dir = root / "2026" / "07" / "01"  # 基準日8/23から14日窓の外
    out_of_window_dir.mkdir(parents=True)
    (out_of_window_dir / "f.jsonl").write_text("{}\n", encoding="utf-8")
    assert _real_codex_sessions_available(root, date(2026, 8, 23), 14) is False


def test_pytest_ini_reports_skip_reasons():
    """壊す不変条件: I3（skip 時の理由が通常実行で必ず表示される設定が外れて
    いないこと。理由は reason= に保持されるだけでは表示されず、pytest.ini の
    addopts に -ra/-rs が必要）
    ／通したい検査経路: pytest.ini の addopts 行に -ra または -rs が含まれること。
    この設定自体が将来消えても検出できるよう、静的にファイル内容を検査する
    （実行結果の標準出力パースは pytest-xdist 経由の並列実行では取得しづらい
    ため、設定の存在を直接検査する形にする）。
    """
    ini_path = _SCRIPTS_DIR.parent / "pytest.ini"
    content = ini_path.read_text(encoding="utf-8")
    addopts_line = next(
        line for line in content.splitlines() if line.strip().startswith("addopts")
    )
    tokens = addopts_line.split()
    assert "-ra" in tokens or "-rs" in tokens, (
        f"pytest.ini の addopts に -ra/-rs がありません: {addopts_line!r}"
    )


def test_real_home_skip_wiring_available_vs_unavailable(pytester, tmp_path):
    """壊す不変条件: I3/I6（skipif の配線そのもの。`_real_codex_sessions_available()`
    を直接呼ぶ helper テストや `-ra` 静的テストとは独立に、
    `not _real_codex_sessions_available()` を `True` に固定する変異（＝
    real_home 4件が常に skip される）を検出する）
    ／通したい検査経路: `_real_home_skip_marker` を使って構築した decorator を
    実際に pytest 収集にかけ、対象データがある窓では非skip・無い窓では
    理由付き skip になることを、pytester（pytest 内蔵の統合テスト用 fixture）
    経由で実測する。real_home マーカー自体は使わず（HOME 隔離と無関係な検査
    のため）、`_real_home_skip_marker` が返す decorator の実際の効果だけを見る。
    """
    available_root = tmp_path / "available_sessions" / "2026" / "08" / "20"
    available_root.mkdir(parents=True)
    (available_root / "f.jsonl").write_text("{}\n", encoding="utf-8")
    available_root_top = tmp_path / "available_sessions"

    unavailable_root = tmp_path / "unavailable_sessions"
    unavailable_root.mkdir()

    tests_dir = str(_SCRIPTS_DIR / "tests")

    pytester.makepyfile(
        test_wiring_probe=f'''
import sys
sys.path.insert(0, {tests_dir!r})
from datetime import date
from pathlib import Path
import test_phase1_codex_probe as t

_marker_available = t._real_home_skip_marker(
    Path({str(available_root_top)!r}), date(2026, 8, 20), 1
)
_marker_unavailable = t._real_home_skip_marker(
    Path({str(unavailable_root)!r}), date(2026, 8, 20), 1
)


@_marker_available
def test_when_available():
    assert True


@_marker_unavailable
def test_when_unavailable():
    assert True
'''
    )
    result = pytester.runpytest("-v", "-ra")
    result.assert_outcomes(passed=1, skipped=1)
    result.stdout.fnmatch_lines(["*test_when_available PASSED*"])
    result.stdout.fnmatch_lines(["*test_when_unavailable SKIPPED*"])
    # 理由文字列も配線されていること（I3 の「理由が必ず出る」契約と同型の検査）。
    result.stdout.fnmatch_lines([f"*{unavailable_root}*"])


# ─────────────────────────────────────────────────────────────────
# #536 round6 codex Should: PR #537 との複製の乖離検出
#
# #536 は #537（fix/vuln-scan-scope）の Cf/Mn/Me 除去＋NFKC 正規化と同一設計を、
# 未マージ branch への cross-PR import を避けるため独立に複製した（round6 team-lead
# 判断: 「複製する」判断は支持されたが乖離は既に発生していた＝#537 は NFKC を
# 先に追加済みで #536 は追いついていなかった）。乖離を今後も見逃さないよう、
# #537 のファイルを git show で read-only 参照し、共有入力ベクトルを両実装へ
# 通して結果を突合する。branch が既にマージ・削除された後は skip する
# （恒久的な cross-branch 依存にしない。マージ後は #537 のコードが main に
# 入るはずなので、その時点で本テストの当否を再検討すること）。
# ─────────────────────────────────────────────────────────────────
_CROSS_PR_REF = "fix/vuln-scan-scope"
_CROSS_PR_PATH = "scripts/lib/skill_vuln_scan.py"


def _load_module_from_git_ref(ref: str, path: str, module_name: str):
    """指定した git ref のファイル内容を exec してモジュール名前空間を返す。

    read-only（``git show`` のみ。checkout/stash/reset は一切行わない。worktree
    の作業ツリー・index・HEAD には触れない）。ref が存在しない・path が無い場合は
    None を返す（呼び出し側で skip する）。

    ``sys.modules`` へ実体のある ``types.ModuleType`` を一時登録してから exec する
    （dataclass 等、`cls.__module__` を `sys.modules` 経由で解決するデコレータが
    プレーン dict の ``exec(..., ns)`` では ``AttributeError`` になるため。実測で
    判明した罠）。exec 完了後（成否問わず）に登録を解除する。
    """
    import sys as _sys
    import types as _types

    result = subprocess.run(
        ["git", "-C", str(_SCRIPTS_DIR.parent), "show", f"{ref}:{path}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    mod = _types.ModuleType(module_name)
    mod.__file__ = f"<git:{ref}:{path}>"
    _sys.modules[module_name] = mod
    try:
        exec(compile(result.stdout, f"<git:{ref}:{path}>", "exec"), mod.__dict__)
    except Exception:
        return None
    finally:
        _sys.modules.pop(module_name, None)
    return mod.__dict__


def test_cross_pr_normalization_contract_matches_537_when_available():
    """壊す不変条件: Should（#536/#537 独立複製の乖離。#537 が正規化ロジックを
    変更しても #536 が追いつかず気づけない）
    ／通したい検査経路: 共有入力ベクトルを #536 側 (`p._normalize_for_matching`)
    と #537 側（git show で read-only 参照して exec した
    `_normalize_for_matching`）の両方へ通し、結果が一致することを実測する。
    """
    ns = _load_module_from_git_ref(_CROSS_PR_REF, _CROSS_PR_PATH, "skill_vuln_scan_537")
    if ns is None:
        pytest.skip(
            f"{_CROSS_PR_REF}:{_CROSS_PR_PATH} を読めません"
            "（branch が既にマージ・削除された、または未取得の可能性）。"
        )
    their_normalize = ns.get("_normalize_for_matching")
    if their_normalize is None:
        pytest.skip(f"{_CROSS_PR_REF} 側に _normalize_for_matching が見つかりません（設計変更の可能性）。")

    shared_vectors = [
        "<skill>x</skill>",
        "​<skill>x</skill>",
        "<sḱill>x</skill>",
        "<ＳＫＩＬＬ>x</skill>",
        " <skill>x</skill>",
        "普通の発話です。",
        '<not_a_marker attr="x">本文</not_a_marker>',
    ]
    mismatches = [
        (text, p._normalize_for_matching(text), their_normalize(text))
        for text in shared_vectors
        if p._normalize_for_matching(text) != their_normalize(text)
    ]
    assert not mismatches, (
        f"#536/#537 の正規化結果が乖離しています（共通化を検討する Issue を起票すること）: {mismatches!r}"
    )


# ─────────────────────────────────────────────────────────────────
# fixture helpers
# ─────────────────────────────────────────────────────────────────
def _session_meta(sid: str, cwd: str = "/Users/x/matsukaze-utils/evolve-anything", ts="2026-08-20T00:00:00.000Z") -> str:
    return json.dumps({"timestamp": ts, "type": "session_meta", "payload": {"id": sid, "cwd": cwd}})


def _response_user(text: str, ts="2026-08-20T00:00:01.000Z") -> str:
    return json.dumps({
        "timestamp": ts,
        "type": "response_item",
        "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
    })


def _response_role(role: str, text: str, ts="2026-08-20T00:00:01.000Z") -> str:
    return json.dumps({
        "timestamp": ts,
        "type": "response_item",
        "payload": {"type": "message", "role": role, "content": [{"type": "input_text", "text": text}]},
    })


def _event_user_message(text: str, ts="2026-08-20T00:00:01.000Z") -> str:
    return json.dumps({
        "timestamp": ts,
        "type": "event_msg",
        "payload": {"type": "user_message", "message": text},
    })


def _sub_agent_activity(agent_thread_id: str, ts="2026-08-20T00:00:02.000Z") -> str:
    return json.dumps({
        "timestamp": ts,
        "type": "event_msg",
        "payload": {"type": "sub_agent_activity", "agent_thread_id": agent_thread_id, "kind": "started"},
    })


def _inter_agent_marker(ts="2026-08-20T00:00:03.000Z") -> str:
    return json.dumps({"timestamp": ts, "type": "inter_agent_communication_metadata", "payload": {"trigger_turn": False}})


def _unknown_record(ts="2026-08-20T00:00:04.000Z") -> str:
    # tool_search_call は Must1 で既知のツール呼び出し組になったため、ここでは
    # 未知組の代表として genuinely 未知の組を使う。
    return json.dumps({"timestamp": ts, "type": "response_item", "payload": {"type": "totally_unknown_type"}})


def _function_call(name: str, ts="2026-08-20T00:00:04.000Z") -> str:
    return json.dumps({
        "timestamp": ts,
        "type": "response_item",
        "payload": {"type": "function_call", "name": name, "call_id": "c1"},
    })


def _write(tmp_path: Path, name: str, lines) -> Path:
    p_ = tmp_path / name
    p_.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p_


# ─────────────────────────────────────────────────────────────────
# 先頭タグ判定（D3）
# ─────────────────────────────────────────────────────────────────
def test_all_nine_machinery_markers_detected():
    """壊す不変条件: D3（機構9種の網羅）／検査経路: is_machinery_text の marker set。"""
    for marker in sorted(p.MACHINERY_MARKERS):
        assert p.is_machinery_text(f"<{marker}>\nbody") is True
    assert p.is_machinery_text("<not_a_marker>\nbody") is False


# 実装定数 (p.MACHINERY_MARKERS) からも独立オラクル定数 (p._ORACLE_MACHINERY_MARKERS)
# からも一切 import・反復しない、テスト自身がリテラルで書き下した第三の集合
# （#536 round3 codex Must1）。これと両定数を突合することで、どちらか片方だけ
# マーカーが削除・改変されても検出できる（両方が同時に同じ変異を受けない限り）。
_INDEPENDENT_LITERAL_MACHINERY_MARKERS = frozenset(
    {
        "recommended_plugins",
        "task-notification",
        "command-name",
        "local-command-stdout",
        "command-message",
        "skill",
        "environment_context",
        "user_action",
        "image",
    }
)


def test_machinery_marker_set_matches_independent_literal():
    """壊す不変条件: item5（MACHINERY_MARKERS からの要素削除・改変を検出）
    ／通したい検査経路: 実装定数・独立オラクル定数それぞれと、テストが直書きした
    リテラル集合との完全一致比較。
    ``p.MACHINERY_MARKERS`` から要素を1件削っても is_machinery_text 自体は
    変更されないため実装内テストは通ってしまうが、本テストはリテラル集合との
    差分として検出する。
    """
    assert p.MACHINERY_MARKERS == _INDEPENDENT_LITERAL_MACHINERY_MARKERS
    assert p._ORACLE_MACHINERY_MARKERS == _INDEPENDENT_LITERAL_MACHINERY_MARKERS


# #536 round6 codex Must I2: fixture に**文字列リテラルとして書かれた**大小文字
# ゆれ（例: "SKILL"/"Skill"/"TaSk-NoTiFiCaTiOn"）は、何件追加しても「fixture に
# 書かれた表記だけを正規化する辞書」への narrow 化変異を防げない（変異する側が
# ソース中の全リテラルを列挙してハードコードできてしまうため）。ソースコード上に
# 存在しない casing をテスト**実行時に**プログラム的に生成することで、辞書型の
# narrow 化がこれを予測・列挙できないようにする。
def _alternating_case(s: str) -> str:
    """偶数インデックスを大文字・奇数インデックスを小文字にする決定論変換。"""
    return "".join(ch.upper() if i % 2 == 0 else ch.lower() for i, ch in enumerate(s))


def _alternating_case_offset(s: str) -> str:
    """`_alternating_case` と位相を1つずらした版（もう1パターン生成する）。"""
    return "".join(ch.lower() if i % 2 == 0 else ch.upper() for i, ch in enumerate(s))


def test_case_fold_handles_generated_casing_not_hardcodable_literals():
    """壊す不変条件: I2（casing 正規化を『fixture に書かれた特定表記だけを
    正規化する辞書』へ実装・オラクル双方を同時に narrow 化する変異。fixture が
    何表記固定しても、辞書がそれらを丸ごと列挙すれば通ってしまうため、
    ソースに存在しない casing をテスト実行時に生成して当てる）
    ／通したい検査経路: is_machinery_text と _oracle_is_machinery_text の両方。
    9種マーカー全体 × 2種類の生成パターンを総当たりする。
    """
    for marker in sorted(p.MACHINERY_MARKERS):
        for variant in (_alternating_case(marker), _alternating_case_offset(marker)):
            text = f"<{variant}>x</{variant}>"
            assert p.is_machinery_text(text) is True, text
            assert p._oracle_is_machinery_text(text) is True, text


def test_alternating_case_generates_casing_absent_from_source_literals():
    """陽性対照 + fixture 健全性: 生成された casing 文字列が、このテストファイル
    自身のソースコード中にリテラルとして存在しないことを確認する。存在すれば
    「ハードコード辞書でも予測できてしまう」ため、この検査の前提が崩れる。
    """
    own_source = Path(__file__).read_text(encoding="utf-8")
    for marker in sorted(p.MACHINERY_MARKERS):
        for variant in (_alternating_case(marker), _alternating_case_offset(marker)):
            if variant == marker or variant == marker.lower() or variant == marker.upper():
                continue  # casing 変換が無効な語（記号のみ等）は対象外
            assert variant not in own_source, (
                f"生成した casing {variant!r} が既にソース中にリテラルとして存在します"
                "（ハードコード辞書で予測可能になってしまう）"
            )


# 入力テキスト → 期待生存可否 の fixture。ラベルは is_machinery_text /
# _oracle_is_machinery_text のどちらも呼ばずに、人手でテキストを読んで判定した
# 期待値（#536 round3 codex Must1「入力→期待生存発話の fixture を実装定数・
# 判定関数から独立して定義する」）。
_INDEPENDENT_SURVIVAL_FIXTURE: List[Tuple[str, bool]] = [
    ("<recommended_plugins>\n一覧です", False),  # 9種の先頭
    ("<task-notification>\n通知", False),
    ("<command-name>ls</command-name>", False),
    ("<local-command-stdout>...</local-command-stdout>", False),
    ("<command-message>...</command-message>", False),
    ("<skill>evolve</skill>", False),
    ("<environment_context>...</environment_context>", False),
    ("<user_action>click</user_action>", False),
    ("<image>base64...</image>", False),
    ("普通の発話です。よろしくお願いします。", True),  # 通常発話
    ("<not_a_marker>本文", True),  # タグはあるが機構マーカーでない
    ("普通の発話に <recommended_plugins> という単語が混じるだけ", True),  # 先頭でない
    ("﻿  \n<skill>evolve</skill>", False),  # BOM・空白混入後の機構タグ
    ("", True),  # 空文字は機構でない
    ("<recommended_plugins", True),  # 閉じ `>` が無く head_tag 相当は不成立
    # #536 round4 codex Must: 全角空白（U+3000）は Unicode カテゴリ Zs（空白）に
    # 属し、strip_leading_noise の `.lstrip()`（round5 以降: Cf/Mn/Me 除去＋NFKC
    # 正規化の後に適用。round6 で NFKC 追加。Nit: このコメントは round4 時点の
    # 個別文字列挙設計を説明していたが現行実装と食い違っていたため #536 round6 で
    # 現行仕様に合わせて訂正した）が対象とする表現差クラス。ASCII 空白・BOM・改行
    # だけの fixture では、全角空白 strip 対応を削る変異（変異1）を検出できない。
    ("　<skill>evolve</skill>", False),  # 全角空白のみ先頭に混入した機構タグ
    ("　\n<recommended_plugins>\n一覧です", False),  # 全角空白+改行の複合
    # #536 round4 codex Must: head_tag は `<tag ...>` の inner を空白分割し先頭語を
    # タグ名とする（属性付きタグにも対応する契約）。属性なしタグしか無い fixture
    # では、この分割対応を削る変異（変異2）を検出できない。
    ('<skill name="evolve">x</skill>', False),  # 属性付き機構タグ（9種のうち1つを代表）
    ('<command-name value="ls">x</command-name>', False),  # 属性付き・両チャネル共通マーカー
    ('<not_a_marker attr="x">本文</not_a_marker>', True),  # 属性付きだが機構マーカーでない（陽性対照）
    # #536 round5 team-lead 追加確認・自己構成の回避手段5: 属性を単引用符にした場合
    # （二重引用符の fixture しか無いと、二重引用符専用の判定へ narrow 化する変異を
    # 検出できない）。head_tag は引用符種別を一切見ない（inner.split()[0] で先頭
    # トークンを取るだけ）契約なので、単引用符でも機構判定は揺らがない。
    ("<skill name='evolve'>x</skill>", False),  # 属性が単引用符の機構タグ
    # #536 チームリード追加確認: 自己閉じ形（スラッシュの前に空白が無い）は
    # `inner.split()[0]` が "image/" のようにスラッシュ込みで返るため、
    # 正規化を怠ると不一致になる表現差クラス（実データ ~/.codex/sessions 726
    # ファイル・実候補 6467 件を実測し出現数0件を確認済みだが、迷ったら除外
    # せず検査対象に倒す方針で対応する）。
    ("<image/>", False),  # 自己閉じ・スラッシュ直前に空白なし
    ("<skill/>", False),  # 同上
    ("<image />", False),  # 自己閉じ・スラッシュ直前に空白あり（元々検出できていた形）
    ("<not_a_marker/>", True),  # 自己閉じだが機構マーカーでない（陽性対照）
    # タグ名の大小文字ゆれ。実データでは出現0件を実測済みだが、同じ理由で
    # 正規化する（Codex 側の出力ゆれで大文字化される可能性を排除できないため）。
    ("<SKILL>evolve</SKILL>", False),  # 全大文字
    ("<Skill>evolve</Skill>", False),  # 先頭大文字
    ("<COMMAND-NAME>ls</COMMAND-NAME>", False),  # 全大文字・両チャネル共通マーカー
    ("<Not_A_Marker>本文</Not_A_Marker>", True),  # 大文字だが機構マーカーでない（陽性対照）
    # #536 round5 codex Must I2: 大小文字ゆれの正規化を「fixture にある特定の
    # 語だけを小文字化する辞書」へ差し替える変異（実装・オラクル双方を同時に
    # 変異）を適用すると、上の3語（SKILL/Skill/COMMAND-NAME）だけの fixture では
    # 全テストが通ってしまった（実測: <Image/> と <Recommended_Plugins> は双方
    # False で漏れた）。9種の機構マーカー**全体**に対して大小文字ゆれを固定し、
    # 「特定語の辞書」への変異では通らないようにする。
    ("<Image/>", False),  # 自己閉じ・先頭大文字
    ("<IMAGE/>", False),  # 自己閉じ・全大文字
    ("<Recommended_Plugins>\n一覧です", False),  # 先頭大文字・アンダースコア区切り
    ("<RECOMMENDED_PLUGINS>\n一覧です", False),  # 全大文字
    ("<Task-Notification>\n通知", False),  # 先頭大文字・ハイフン区切り
    ("<TASK-NOTIFICATION>\n通知", False),  # 全大文字
    ("<Local-Command-Stdout>...</Local-Command-Stdout>", False),  # 先頭大文字
    ("<Command-Message>...</Command-Message>", False),  # 先頭大文字
    ("<Environment_Context>...</Environment_Context>", False),  # 先頭大文字
    ("<User_Action>click</User_Action>", False),  # 先頭大文字
    ("<Not_Recommended_Plugins>本文", True),  # 似た大文字語だが機構マーカーでない（陽性対照）
]

# #536 round5 codex Must I1: 不可視文字（Cf/Mn/Me、位置不問）が「テキスト先頭」
# にも「タグ名の内側」にも入れられる。個別の1文字だけを fixture に足す方式では
# 次の1文字で破れる（列挙で直さない・design-before-fanout）ため、複数カテゴリ・
# 複数位置の組合せを固定する。値は verify-checks-by-breaking.md の「境界値と
# 表現差」節に従い ZWSP（Cf）・BOM（Cf）・結合文字（Mn）・異体字セレクタ（Mn）を
# それぞれ異なる位置（先頭・タグ開始直後・タグ名内部・タグ名末尾）に配置する。
# #536 round5 codex Must I1: 不可視文字（Cf/Mn/Me、位置不問）が「テキスト先頭」
# にも「タグ名の内側」にも入れられる。個別の1文字だけを fixture に足す方式では
# 次の1文字で破れる（列挙で直さない・design-before-fanout）ため、複数カテゴリ・
# 複数位置の組合せを固定する。値は verify-checks-by-breaking.md の「境界値と
# 表現差」節に従い ZWSP（Cf）・BOM（Cf）・結合文字（Mn）・異体字セレクタ（Mn）を
# それぞれ異なる位置（先頭・タグ開始直後・タグ名内部・タグ名末尾）に配置する。
#
# #536 round6 codex Must I7: 「Cf/Mn/Me のいずれかを含む」という**カテゴリ**だけの
# 健全性検査では、意図した特定の文字が別の Cf/Mn/Me 文字にすり替わっても検出
# できない（保存経路の消失で別文字に化けても気づけない）。各エントリへ**期待する
# 厳密な codepoint 集合**を第3要素として持たせ、健全性検査はカテゴリでなく
# 「この codepoint が実際に含まれるか」を assert する。
#
# #536 round6 codex Must I5: `Me`（Enclosing_Mark）は実装・オラクル双方の
# カテゴリ集合定数に含まれてはいたが、fixture に**実際の Me 文字**が1件も無く、
# `Me` をカテゴリ集合から削る変異が全テスト緑のまま生存した。実 Me 文字
# （U+20DD COMBINING ENCLOSING CIRCLE）を追加する。
_INVISIBLE_CHAR_SURVIVAL_FIXTURE: List[Tuple[str, bool, "frozenset[int]"]] = [
    ("\u200b<skill>x</skill>", False, frozenset({0x200B})),  # 先頭に ZWSP（Cf）
    ("<\u200bskill>x</skill>", False, frozenset({0x200B})),  # `<` 直後に ZWSP
    ("<skill\u200b>x</skill>", False, frozenset({0x200B})),  # タグ名末尾に ZWSP（`>` 直前）
    ("<sk\u200bill>x</skill>", False, frozenset({0x200B})),  # タグ名の途中に ZWSP
    ("<\ufeffskill>x</skill>", False, frozenset({0xFEFF})),  # `<` 直後に BOM（Cf）
    # \uXXXX エスケープで明示する（実測: 結合文字・異体字セレクタをリテラル文字
    # で直書きすると、ファイル保存経路の Unicode 正規化で静かに消え、Mn 経路を
    # 検査していないのに緑になる偽陽性 fixture を作った。#536 round5 自己発見）。
    ("<sk\u0301ill>x</skill>", False, frozenset({0x0301})),  # 結合文字（Mn・acute accent）
    ("<skill\ufe0f>x</skill>", False, frozenset({0xFE0F})),  # 異体字セレクタ（Mn・VS16）
    ("<image\u200b/>", False, frozenset({0x200B})),  # 自己閉じタグのスラッシュ直前に ZWSP
    # #536 round5 team-lead 追加確認・自己構成の回避手段3・4・6:
    # ad-hoc 確認済みだったが恒久化していなかった入力クラスを固定する。
    ("<sk\u200fill>x</skill>", False, frozenset({0x200F})),  # RTL mark（Cf）
    ("\u2060<skill>x</skill>", False, frozenset({0x2060})),  # word joiner（Cf）
    # #536 round6 codex Must I5: 実 Me 文字（COMBINING ENCLOSING CIRCLE）。
    ("<sk\u20ddill>x</skill>", False, frozenset({0x20DD})),  # タグ名内部に Me 文字
    ("普通の発話です\u200b。", True, frozenset({0x200B})),  # ZWSP を含むが機構マーカーでない通常発話（陽性対照）
]


def test_invisible_char_fixture_entries_actually_contain_invisible_chars():
    """壊す不変条件: 不可視文字リテラルがファイル保存経路で静かに消失し、検査して
    いないのに緑になる偽陽性を作る罠（#536 round5 自己発見。結合文字・異体字セレクタを
    リテラル直書きした際に実際に踏んだ）。fixture 定義自体が健全（意図した
    **厳密な codepoint** を実際に含む）であることを検査する（#536 round6 I7:
    カテゴリ一致だけでは「別の Cf/Mn/Me 文字へのすり替え」を見逃すため厳密化）。
    ／通したい検査経路: _INVISIBLE_CHAR_SURVIVAL_FIXTURE の各エントリに対する
    codepoint 集合の包含チェック。
    """
    for text, _expected, expected_codepoints in _INVISIBLE_CHAR_SURVIVAL_FIXTURE:
        actual_codepoints = {ord(ch) for ch in text}
        missing = expected_codepoints - actual_codepoints
        assert not missing, (
            f"fixture entry に期待した codepoint が含まれていません"
            f"（保存経路での消失/すり替えの疑い）: {text!r} missing={sorted(hex(c) for c in missing)}"
        )
        for cp in expected_codepoints:
            assert unicodedata.category(chr(cp)) in {"Cf", "Mn", "Me"}, (
                f"期待 codepoint {hex(cp)} が Cf/Mn/Me カテゴリではありません（fixture定義ミス）"
            )



# #536 round5 team-lead 追加確認・自己構成の回避手段6: タグ名と `>` の間に改行/タブ
# （\t・\n は Unicode カテゴリ Cc（Control）であり Cf/Mn/Me ではないため、
# 上の _INVISIBLE_CHAR_SURVIVAL_FIXTURE とは別カテゴリの表現差として区別する。
# head_tag の `inner.split()[0]`（デフォルト引数の split は全空白種別対応）が
# 対象。ad-hoc 確認済みだったが恒久化していなかったもの）。
_TAG_INTERNAL_WHITESPACE_SURVIVAL_FIXTURE: List[Tuple[str, bool]] = [
    ("<skill\n>x</skill>", False),  # タグ名と `>` の間に改行
    ("<skill\t>x</skill>", False),  # タグ名と `>` の間にタブ
    ("<not_a_marker\n>本文", True),  # 同じ表現差だが機構マーカーでない（陽性対照）
]


def test_tag_internal_whitespace_fixture_entries_actually_contain_control_chars():
    """壊す不変条件: 上と同じ「保存経路での消失」の罠を、改行/タブ（Cc）についても
    fixture 定義自体の健全性検査で防ぐ。
    ／通したい検査経路: _TAG_INTERNAL_WHITESPACE_SURVIVAL_FIXTURE の各エントリに
    タグ名と `>` の間の制御文字（\\n・\\t）が実在すること。
    """
    for text, _expected in _TAG_INTERNAL_WHITESPACE_SURVIVAL_FIXTURE:
        assert ("\n" in text) or ("\t" in text), (
            f"fixture entry に改行/タブが含まれていません（保存経路での消失の疑い）: {text!r}"
        )


def test_filter_machinery_matches_tag_internal_whitespace_fixture():
    """壊す不変条件: I3/#536 round5 自己構成6（タグ名と `>` の間の改行/タブで
    機構判定が回避できない）
    ／通したい検査経路: filter_machinery（head_tag の `inner = t[1:end].strip()` と
    `inner.split()[0]` が両方ともデフォルト＝全空白種別対応であることに依存。実測:
    `.strip()` だけ・`.split()` だけを個別に ASCII 空白限定へ narrow 化しても、
    もう片方が拾うため単独では壊れない＝多重防御になっている。両方を同時に
    narrow 化する変異で初めて赤くなることを確認済み）。
    """
    candidates = [
        p.RawCandidate(
            file="f.jsonl", channel="response_item", line_no=i, timestamp="2026-08-20T00:00:00.000Z",
            ts_ms=0.0, text=text, cwd=None, session_id="s1", prev_action=None,
        )
        for i, (text, _expected) in enumerate(_TAG_INTERNAL_WHITESPACE_SURVIVAL_FIXTURE)
    ]
    expected_survivors = [
        text for text, expected in _TAG_INTERNAL_WHITESPACE_SURVIVAL_FIXTURE if expected
    ]
    actual_survivors = [c.text for c in p.filter_machinery(candidates)]
    assert actual_survivors == expected_survivors


# #536 round6 codex Must I1/I4: NBSP（U+00A0）は Cf/Mn/Me ではなく Zs（空白）
# カテゴリのため、上の _INVISIBLE_CHAR_SURVIVAL_FIXTURE の位置不問除去とは別の
# 仕組み（NFKC 互換分解で半角スペースへ変換 → `.lstrip()`）で処理される。
# 全角英数字（``ＳＫＩＬＬ``）も同じ NFKC 経路（互換等価変換）で正規化される。
# I4: これらは単体だけでなく、不可視文字・大小文字・属性・自己閉じと**組み合わせて**
# 現れても除外から漏れないことを固定する（reviewer 提供の組合せ例とは別に、
# 自分で構成した組合せを2件以上含める）。
_NFKC_NORMALIZED_SURVIVAL_FIXTURE: List[Tuple[str, bool]] = [
    (" <skill>x</skill>", False),  # NBSP 単体（先頭）
    ("<ＳＫＩＬＬ>x</skill>", False),  # 全角英字（ＳＫＩＬＬ）単体
    # 自己構成の組合せ1: ZWSP（Cf・位置不問除去）+ 全角英字 + 属性(二重引用符) + 自己閉じ
    ('​<ＳＫＩＬＬ data-x="1"/>', False),
    # 自己構成の組合せ2: NBSP（NFKC経路）+ 結合文字（Mn・位置不問除去）+ 属性(単引用符)
    (" <sḱill name='x'>x</skill>", False),
    ("普通の発話 です。", True),  # NBSP を含むが機構マーカーでない通常発話（陽性対照）
    ('<not_a_marker  data-x="1"/>', True),  # NBSP+属性+自己閉じだが機構マーカーでない（陽性対照）
]


def test_nfkc_normalized_fixture_entries_actually_require_nfkc_normalization():
    """壊す不変条件: 上と同じ「保存経路での消失」の罠を、NFKC 依存の表現差
    （NBSP・全角英数字）についても fixture 定義自体の健全性検査で防ぐ。
    ／通したい検査経路: 各エントリが実際に NFKC 正規化で変化する文字列である
    こと（`unicodedata.normalize("NFKC", text) != text`）。この条件が成り立たない
    エントリは「NFKC 経路を検査していない」ことを意味する。
    """
    for text, _expected in _NFKC_NORMALIZED_SURVIVAL_FIXTURE:
        normalized = unicodedata.normalize("NFKC", text)
        assert normalized != text, (
            f"fixture entry が NFKC で変化しません（NBSP/全角文字が消失した疑い）: {text!r}"
        )


def test_filter_machinery_matches_nfkc_normalized_fixture():
    """壊す不変条件: I1/I4（NBSP・全角英数字が単体・組合せのいずれでも機構判定を
    回避できない）
    ／通したい検査経路: filter_machinery（is_machinery_text 経由。
    strip_leading_noise 内の NFKC 正規化ステップに依存）。
    """
    candidates = [
        p.RawCandidate(
            file="f.jsonl", channel="response_item", line_no=i, timestamp="2026-08-20T00:00:00.000Z",
            ts_ms=0.0, text=text, cwd=None, session_id="s1", prev_action=None,
        )
        for i, (text, _expected) in enumerate(_NFKC_NORMALIZED_SURVIVAL_FIXTURE)
    ]
    expected_survivors = [text for text, expected in _NFKC_NORMALIZED_SURVIVAL_FIXTURE if expected]
    actual_survivors = [c.text for c in p.filter_machinery(candidates)]
    assert actual_survivors == expected_survivors


def test_oracle_is_machinery_text_matches_nfkc_normalized_fixture():
    """壊す不変条件: I1/I4（独立オラクル側も同じ NFKC 経路で判定が揺らがない）
    ／通したい検査経路: _oracle_is_machinery_text（is_machinery_text から独立）。
    """
    for text, expected_survives in _NFKC_NORMALIZED_SURVIVAL_FIXTURE:
        assert p._oracle_is_machinery_text(text) is (not expected_survives), text


def test_filter_machinery_matches_independently_labeled_fixture():
    """壊す不変条件: item5（マーカー文字列の改変・swap・判定弱体化を、実装からの
    独立算出でなく人手ラベル済み fixture との突合で検出）
    ／通したい検査経路: filter_machinery（run_probe の唯一の適用点と同じ関数）。
    """
    candidates = [
        p.RawCandidate(
            file="f.jsonl", channel="response_item", line_no=i, timestamp="2026-08-20T00:00:00.000Z",
            ts_ms=0.0, text=text, cwd=None, session_id="s1", prev_action=None,
        )
        for i, (text, _expected) in enumerate(_INDEPENDENT_SURVIVAL_FIXTURE)
    ]
    expected_survivors = [text for text, expected in _INDEPENDENT_SURVIVAL_FIXTURE if expected]
    actual_survivors = [c.text for c in p.filter_machinery(candidates)]
    assert actual_survivors == expected_survivors


def test_filter_machinery_matches_invisible_char_fixture():
    """壊す不変条件: I1（不可視文字が先頭・タグ開始直後・タグ名内部・タグ名末尾の
    いずれに入っても機構判定が揺らがない）
    ／通したい検査経路: filter_machinery（is_machinery_text 経由）。
    ZWSP/BOM（Cf）・結合文字/異体字セレクタ（Mn）を複数位置に配置した fixture。
    列挙修正（特定の1文字だけ追加）ではこの fixture の一部しか通らない。
    """
    candidates = [
        p.RawCandidate(
            file="f.jsonl", channel="response_item", line_no=i, timestamp="2026-08-20T00:00:00.000Z",
            ts_ms=0.0, text=text, cwd=None, session_id="s1", prev_action=None,
        )
        for i, (text, _expected, _cp) in enumerate(_INVISIBLE_CHAR_SURVIVAL_FIXTURE)
    ]
    expected_survivors = [text for text, expected, _cp in _INVISIBLE_CHAR_SURVIVAL_FIXTURE if expected]
    actual_survivors = [c.text for c in p.filter_machinery(candidates)]
    assert actual_survivors == expected_survivors


def test_oracle_is_machinery_text_matches_invisible_char_fixture():
    """壊す不変条件: I1（独立オラクル側も同じ不可視文字クラスで判定が揺らがない。
    レビュー指摘は実装・オラクル『双方』が False になったケース）
    ／通したい検査経路: _oracle_is_machinery_text（is_machinery_text から独立）。
    """
    for text, expected_survives, _cp in _INVISIBLE_CHAR_SURVIVAL_FIXTURE:
        assert p._oracle_is_machinery_text(text) is (not expected_survives), text


def test_developer_role_excluded_by_reducer():
    """壊す不変条件: D3（developer role はユーザー発話でない）
    ／通したい検査経路: parse_session_file の role dispatch。
    変異①（役割フィルタの削除）を適用すると本テストが単独で落ちる
    （developer 発話が candidates に混入する）。
    """
    lines = [_session_meta("s1"), _response_role("developer", "system prompt injection")]
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as td:
        path = _write(Path(td), "f.jsonl", lines)
        pf = p.parse_session_file(path)
    assert pf.candidates == []


def test_bom_and_whitespace_before_tag_still_detected():
    """壊す不変条件: D3 表現差（BOM・空白・改行を先頭タグの前に混入させても除外できる）
    ／通したい検査経路: strip_leading_noise → head_tag。
    変異②（strip 呼び出し削除）を適用すると本テストが単独で落ちる。
    """
    text = "﻿  \n<recommended_plugins>\nHere is a list..."
    assert p.is_machinery_text(text) is True


def test_marker_like_text_not_at_head_is_not_excluded():
    """陽性対照: 先頭以外に marker 文字列を含む本文（意味を変えない差分）は除外されない。"""
    text = "普通の発話です。<recommended_plugins> という単語だけ本文中に出てくる。"
    assert p.is_machinery_text(text) is False


# I4（#536 round4 codex Must）: 除外結果は入力チャネルに依存しない。
# ADR（docs/decisions/drafts/055-codex-rollout-ingest.md:281-283）は
# command-name / local-command-stdout / command-message の3種を
# response_item / event_msg 両チャネル共通と実測している。人手ラベル済み
# fixture が全件 channel="response_item" 固定だと、event_msg チャネルだけを
# 無条件生存させる（あるいはその逆）配線の欠陥を検出できない。
_CHANNEL_INDEPENDENT_FIXTURE: List[Tuple[str, str, bool]] = [
    ("<command-name>ls</command-name>", "response_item", False),
    ("<command-name>ls</command-name>", "event_msg", False),
    ("<local-command-stdout>...</local-command-stdout>", "response_item", False),
    ("<local-command-stdout>...</local-command-stdout>", "event_msg", False),
    ("<command-message>...</command-message>", "response_item", False),
    ("<command-message>...</command-message>", "event_msg", False),
    ("普通の発話です。", "response_item", True),
    ("普通の発話です。", "event_msg", True),
]


def test_filter_machinery_is_channel_independent():
    """壊す不変条件: I4（除外結果は入力チャネルに依存しない）
    ／通したい検査経路: filter_machinery（is_machinery_text はテキストのみを見て
    判定し channel を参照しないという設計契約）。
    変異（filter_machinery を channel=="event_msg" のときだけ無条件生存させる等）
    を適用すると、同一マーカーが片方のチャネルでのみ生存し本テストが落ちる。
    """
    candidates = [
        p.RawCandidate(
            file="f.jsonl", channel=channel, line_no=i, timestamp="2026-08-20T00:00:00.000Z",
            ts_ms=0.0, text=text, cwd=None, session_id="s1", prev_action=None,
        )
        for i, (text, channel, _expected) in enumerate(_CHANNEL_INDEPENDENT_FIXTURE)
    ]
    expected_survivors = [
        (text, channel) for text, channel, expected in _CHANNEL_INDEPENDENT_FIXTURE if expected
    ]
    actual_survivors = [(c.text, c.channel) for c in p.filter_machinery(candidates)]
    assert actual_survivors == expected_survivors


# ─────────────────────────────────────────────────────────────────
# D4: 子セッション除外
# ─────────────────────────────────────────────────────────────────
def test_child_session_file_excluded_with_both_markers(tmp_path):
    """壊す不変条件: D4（子セッションのファイル単位除外）
    ／通したい検査経路: split_parent_child が sub_agent_activity.agent_thread_id と
    session_meta.id の一致で判定する。fixture は codex v3 レビュー指摘どおり
    sub_agent_activity と inter_agent_communication_metadata の**両方**を含む
    （変異#6: sub_agent_activity の判定条件をトップレベル type 誤認に差し替えると、
    inter_agent_communication_metadata の存在下でも本テストが単独で落ちる）。
    """
    parent_lines = [
        _session_meta("parent-1"),
        _response_user("親から見た発話"),
        _sub_agent_activity("child-1"),
        _inter_agent_marker(),
    ]
    child_lines = [
        _session_meta("child-1"),
        _response_user("子セッション内の発話（誤って人間発話として拾われてはいけない）"),
    ]
    parent_path = _write(tmp_path, "parent.jsonl", parent_lines)
    child_path = _write(tmp_path, "child.jsonl", child_lines)

    parsed = [p.parse_session_file(parent_path), p.parse_session_file(child_path)]
    ref = p.build_agent_thread_id_set(parsed)
    parents, children = p.split_parent_child(parsed, ref)

    assert {c.path for c in children} == {str(child_path)}
    assert {pf.path for pf in parents} == {str(parent_path)}


@pytest.mark.real_home
@_skip_if_no_real_codex_sessions
def test_child_reference_scope_phase1_vs_full_agreement():
    """壊す不変条件: X2（Phase1限定走査と全走査が一致する）
    ／通したい検査経路: run_probe の child_ref_scope_agreement フラグ。
    """
    result = p.run_probe(
        sessions_root=_REAL_CODEX_SESSIONS_ROOT,
        base_date=_REAL_HOME_TEST_BASE_DATE,
        days=_REAL_HOME_TEST_DAYS,
    )
    assert result.child_ref_scope_agreement is True


# ─────────────────────────────────────────────────────────────────
# D5a: セグメント帰属（最重要の変異#9）
# ─────────────────────────────────────────────────────────────────
def test_segment_attribution_uses_most_recent_preceding_session_meta(tmp_path):
    """壊す不変条件: D5a（発話は自分のセグメントの session_meta に帰属する）
    ／通したい検査経路: parse_session_file の current_session_id 更新ロジック。

    複数 session_meta・各セグメントに user 発話・セグメントごとに異なる期待
    session_id を持つ fixture（codex v3 [Must] 反映）。変異#9（先頭ID一律付与へ
    巻き戻し）を適用すると、2番目のセグメントの発話が誤って "seg-A" に帰属し、
    本テストが単独で落ちる（単一 session_meta の fixture では検出できない）。
    """
    lines = [
        _session_meta("seg-A", ts="2026-08-20T00:00:00.000Z"),
        _response_user("セグメントAの発話", ts="2026-08-20T00:00:01.000Z"),
        _session_meta("seg-B", ts="2026-08-20T00:01:00.000Z"),
        _response_user("セグメントBの発話", ts="2026-08-20T00:01:01.000Z"),
    ]
    path = _write(tmp_path, "resume.jsonl", lines)
    pf = p.parse_session_file(path)

    assert len(pf.candidates) == 2
    by_text = {c.text: c.session_id for c in pf.candidates}
    assert by_text["セグメントAの発話"] == "seg-A"
    assert by_text["セグメントBの発話"] == "seg-B"


def test_utterance_before_any_session_meta_is_dropped_and_counted(tmp_path):
    """壊す不変条件: D5a（未帰属発話は採用せず件数を surface する。黙って落とさない）
    ／通したい検査経路: parse_session_file の unattributed_count。
    """
    lines = [_response_user("session_meta より前の発話")]
    path = _write(tmp_path, "no_meta.jsonl", lines)
    pf = p.parse_session_file(path)

    assert pf.candidates == []
    assert pf.unattributed_count == 1


# ─────────────────────────────────────────────────────────────────
# X1: チャネル制約付き dedup
# ─────────────────────────────────────────────────────────────────
def test_dedup_merges_matching_pair_across_channels():
    """壊す不変条件: X1（二重表現を単一発話へ正規化する）
    ／通したい検査経路: dedup_channel_constrained。
    変異#5（dedup を適用せず両チャネルをそのまま emit）を適用すると、
    このテストは2件のまま単独で落ちる。
    """
    r1 = p.RawCandidate("f1", "response_item", 1, "2026-08-20T00:00:00.010Z", 1000.010 * 1000, "同じ発話", None, "s1", None)
    e1 = p.RawCandidate("f1", "event_msg", 2, "2026-08-20T00:00:00.000Z", 1000.0 * 1000, "同じ発話", None, "s1", None)
    out = p.dedup_channel_constrained([r1, e1], threshold_ms=100)
    assert len(out) == 1
    assert out[0].channel == "response_item"  # response_item 側を代表として残す


def test_dedup_keeps_event_msg_only_utterance_independent():
    """壊す不変条件: M1/X1（event_msg のみに存在する発話が失われないこと）
    ／通したい検査経路: dedup_channel_constrained の未マッチ独立残存。
    変異#7（response_item 側だけを実装し event_msg.user_message を丸ごと落とす）を
    実装レベルで適用すると（parse_session_file の event_msg 分岐を削除）、
    run_probe 経由の統合テストでこの発話が消え、後述の統合テストが単独で落ちる。
    """
    e1 = p.RawCandidate("f1", "event_msg", 1, "2026-08-20T00:00:00.000Z", 1000.0, "event_msgだけの発話", None, "s1", None)
    out = p.dedup_channel_constrained([e1], threshold_ms=100)
    assert len(out) == 1
    assert out[0].channel == "event_msg"


def test_dedup_does_not_merge_beyond_threshold():
    """陽性対照: delta が閾値を超える候補は誤統合されない（意味を変えない差分ではなく
    正しい境界動作の確認）。"""
    r1 = p.RawCandidate("f1", "response_item", 1, "2026-08-20T00:00:00.500Z", 500.0, "text", None, "s1", None)
    e1 = p.RawCandidate("f1", "event_msg", 2, "2026-08-20T00:00:00.000Z", 0.0, "text", None, "s1", None)
    out = p.dedup_channel_constrained([r1, e1], threshold_ms=100)
    assert len(out) == 2


def test_event_msg_channel_survives_in_full_parse(tmp_path):
    """壊す不変条件: M1/X1（event_msg.user_message が parse_session_file で拾われる）
    ／通したい検査経路: parse_session_file の event_msg 分岐。
    変異#7 の直接検査（parse 段階）。event_msg 分岐を削除すると本テストが単独で落ちる。
    """
    lines = [_session_meta("s1"), _event_user_message("event_msg経路だけの発話")]
    path = _write(tmp_path, "evm_only.jsonl", lines)
    pf = p.parse_session_file(path)
    assert [c.text for c in pf.candidates] == ["event_msg経路だけの発話"]
    assert pf.candidates[0].channel == "event_msg"


# ─────────────────────────────────────────────────────────────────
# X4: 未知 type の扱い（version フィルタをしない）
# ─────────────────────────────────────────────────────────────────
def test_unknown_type_pair_is_skipped_and_surfaced(tmp_path):
    """壊す不変条件: X4（未知の (type, payload.type) 組を静かに落とさず件数を出す）
    ／通したい検査経路: parse_session_file の unknown_type_pairs カウンタ。
    """
    lines = [_session_meta("s1"), _unknown_record(), _response_user("正常な発話")]
    path = _write(tmp_path, "unknown.jsonl", lines)
    pf = p.parse_session_file(path)
    assert pf.unknown_type_pairs[("response_item", "totally_unknown_type")] == 1
    assert [c.text for c in pf.candidates] == ["正常な発話"]


def test_no_cli_version_based_filtering_in_source():
    """壊す不変条件: X4（『Phase1観測versionのみ許可』方式は不採用。version 自体を見ない）
    ／通したい検査経路: ソースコードに cli_version 依存の分岐が無いこと（静的検査）。
    """
    src = Path(p.__file__).read_text(encoding="utf-8")
    assert "cli_version" not in src


def test_real_home_tests_do_not_hardcode_window_literals():
    """壊す不変条件: I8（skip 判定窓と実行窓は同一ソースでなければならない。
    real_home テストが `base_date=date(2026, 8, 23)` を再度直書きすると、guard
    側だけ窓を更新したときに両者が乖離する。旧窓だけにデータがある場合は
    実行して target_files=0 で失敗し、新窓だけにある場合は全 skip になる）
    ／通したい検査経路: このテストファイル自身のソースを静的に検査し、
    `run_probe(` 呼び出しが `base_date=date(2026, 8, 23)` を直書きしていない
    （＝共有定数 `_REAL_HOME_TEST_BASE_DATE` を参照している）ことを確認する。
    """
    own_source = Path(__file__).read_text(encoding="utf-8")
    import re as _re

    # 実コード上の kwarg 直書き（末尾カンマ付き）だけを対象にし、このテスト自身の
    # docstring 中の説明文（バッククォート区切り・カンマなし）を誤検出しない。
    hardcoded = _re.findall(r"base_date=date\(2026,\s*8,\s*23\),", own_source)
    assert not hardcoded, (
        f"real_home テストが窓を再度直書きしています（_REAL_HOME_TEST_BASE_DATE を参照すべき）: {hardcoded}"
    )


# ─────────────────────────────────────────────────────────────────
# X3: 日付ディレクトリ方式のファイル選定
# ─────────────────────────────────────────────────────────────────
def test_iter_date_dir_files_respects_window(tmp_path):
    """壊す不変条件: X3（日付ディレクトリ方式・ウィンドウ外を含めない）
    ／通したい検査経路: iter_date_dir_files。
    """
    root = tmp_path / "sessions"
    for y, m, d, fname in [
        ("2026", "08", "23", "in_range.jsonl"),
        ("2026", "08", "09", "out_of_range.jsonl"),  # 14日窓の外（基準日8/23なら8/10が下限）
    ]:
        dpath = root / y / m / d
        dpath.mkdir(parents=True)
        (dpath / fname).write_text("{}\n", encoding="utf-8")

    files = p.iter_date_dir_files(root, date(2026, 8, 23), 14)
    names = {f.name for f in files}
    assert "in_range.jsonl" in names
    assert "out_of_range.jsonl" not in names


# ─────────────────────────────────────────────────────────────────
# 本番ストア非汚染ガード
# ─────────────────────────────────────────────────────────────────
def test_production_guard_detects_new_file_creation():
    """壊す不変条件: C-1（実行前は不在だったが実行後に出現したら失敗）
    ／通したい検査経路: verify_production_unchanged。
    変異④相当（検査を無効化・常に成功扱いにする）を適用すると本テストが単独で落ちる。
    """
    before = {"utterances_db": None}
    after = {"utterances_db": "deadbeef"}
    ok, violations = p.verify_production_unchanged(before, after)
    assert ok is False
    assert "utterances_db" in violations[0]


def test_production_guard_passes_when_both_absent():
    """陽性対照: 実行前後とも不在なら合格（新規作成のみを失敗として扱う）。"""
    ok, violations = p.verify_production_unchanged({"x": None}, {"x": None})
    assert ok is True
    assert violations == []


def test_production_guard_passes_when_unchanged_present_file():
    """陽性対照: 既存ファイルの hash が実行前後で同一なら合格。"""
    ok, violations = p.verify_production_unchanged({"x": "abc"}, {"x": "abc"})
    assert ok is True


# ─────────────────────────────────────────────────────────────────
# 実データ E2E（C-1・実機ベンチ）
# ─────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────
# C-1: 実データ regression の桁チェック。
#
# 旧実装は ADR 実測値（2026-08-23 早朝）に対して厳密一致・狭いレンジで
# assert していたため、実 ~/.codex/sessions が日々増える限り毎日落ちる作りに
# なっていた（テスト名は order_of_magnitude＝桁が合っていることの検査のはず
# なのに、実装は完全一致を要求していて名実が食い違っていた）。
#
# 下限は ADR 基準値（データは単調増加想定。削除運用は無い前提）。
# 上限は基準値のおよそ2.2倍。根拠: 本修正当日（2026-08-23）の実測で
# target_files が早朝227→同日昼231へ半日弱で +4 件増加した実ペースに対し、
# 数ヶ月分の通常運用増加を吸収しつつ、重複カウント・暴走等の異常な増加は
# 検出できる余裕として採用した（厳密な統計的根拠ではなく実測ペースからの
# 経験的マージン。今後乖離が大きくなったら基準値ごと更新すること）。
_ADR_BASELINE_LOWER_COUNTS = {
    "target_files": 227,
    "raw": 769,
    "after_child_exclusion": 503,
    "after_machinery_exclusion": 373,
    "after_dedup": 259,
}
_ADR_BASELINE_UPPER_COUNTS = {
    "target_files": 500,
    "raw": 1700,
    "after_child_exclusion": 1100,
    "after_machinery_exclusion": 820,
    "after_dedup": 570,
}


def _assert_stage_counts_plausible(c: "p.StageCounts") -> None:
    """段階カウントが「桁が合っている」ことを検査する。

    (1) 各段階が ADR 基準値の下限〜上限レンジ内であること（stale exact-match
    対策。レンジは上のモジュール定数が単一ソース）。
    (2) パイプライン構造上必ず成り立つ段階間の非増加関係
    （raw >= after_child_exclusion >= after_machinery_exclusion >= after_dedup）。
    これは実データの増減に関係なく常に成り立つべき不変条件で、絶対値レンジと
    違って将来も陳腐化しない。
    """
    for name, lower in _ADR_BASELINE_LOWER_COUNTS.items():
        value = getattr(c, name)
        upper = _ADR_BASELINE_UPPER_COUNTS[name]
        assert lower <= value <= upper, (
            f"{name}={value} は想定レンジ [{lower}, {upper}] 外です"
        )
    assert c.raw >= c.after_child_exclusion >= c.after_machinery_exclusion >= c.after_dedup >= 0, (
        "段階間の非増加関係が崩れています: "
        f"raw={c.raw} after_child_exclusion={c.after_child_exclusion} "
        f"after_machinery_exclusion={c.after_machinery_exclusion} after_dedup={c.after_dedup}"
    )
    assert c.target_files > 0


def test_assert_stage_counts_plausible_rejects_below_lower_bound():
    """陰性試験: 下限未満（データ欠落・収集退行を模す）は赤くなる。
    ／通したい検査経路: _assert_stage_counts_plausible の下限チェック。
    """
    c = p.StageCounts(
        target_files=1, raw=1, after_child_exclusion=1,
        after_machinery_exclusion=1, after_dedup=1,
        unattributed_dropped=0, child_files=0, parse_error_lines=0,
    )
    with pytest.raises(AssertionError):
        _assert_stage_counts_plausible(c)


def test_assert_stage_counts_plausible_rejects_above_upper_bound():
    """陰性試験: 上限超過（重複カウント・暴走を模す）は赤くなる。
    ／通したい検査経路: _assert_stage_counts_plausible の上限チェック。
    """
    c = p.StageCounts(
        target_files=10_000, raw=10_000, after_child_exclusion=10_000,
        after_machinery_exclusion=10_000, after_dedup=10_000,
        unattributed_dropped=0, child_files=0, parse_error_lines=0,
    )
    with pytest.raises(AssertionError):
        _assert_stage_counts_plausible(c)


def test_assert_stage_counts_plausible_rejects_broken_stage_ordering():
    """陰性試験: 段階間の非増加関係が崩れている（フィルタが効いていない等）
    と、絶対値レンジ内でも赤くなる。
    ／通したい検査経路: _assert_stage_counts_plausible の段階間不変条件チェック。
    """
    c = p.StageCounts(
        target_files=300, raw=800, after_child_exclusion=900,  # raw を超える
        after_machinery_exclusion=400, after_dedup=300,
        unattributed_dropped=0, child_files=0, parse_error_lines=0,
    )
    with pytest.raises(AssertionError):
        _assert_stage_counts_plausible(c)


def test_assert_stage_counts_plausible_accepts_baseline_boundary():
    """陽性対照: ADR 基準値そのもの（下限の境界値）は許容される。"""
    c = p.StageCounts(
        target_files=227, raw=769, after_child_exclusion=503,
        after_machinery_exclusion=373, after_dedup=259,
        unattributed_dropped=0, child_files=0, parse_error_lines=0,
    )
    _assert_stage_counts_plausible(c)  # 例外が出ないこと自体が検査


@pytest.mark.real_home
@_skip_if_no_real_codex_sessions
def test_run_probe_against_real_codex_sessions_matches_expected_order_of_magnitude():
    """壊す不変条件: C-1（実データで完走し段階件数が ADR 実測値と整合する）
    ／通したい検査経路: run_probe のパイプライン全体（実 ~/.codex/sessions を読む）
    + _assert_stage_counts_plausible（桁レンジ・段階間不変条件）
    + assert_machinery_exclusion_matches_oracle（item5・実データでの独立オラクル突合）。
    """
    result = p.run_probe(
        sessions_root=_REAL_CODEX_SESSIONS_ROOT,
        base_date=_REAL_HOME_TEST_BASE_DATE,
        days=_REAL_HOME_TEST_DAYS,
    )
    c = result.counts
    _assert_stage_counts_plausible(c)
    assert c.parse_error_lines == 0
    p.assert_machinery_exclusion_matches_oracle(result)


@pytest.mark.real_home
@_skip_if_no_real_codex_sessions
def test_run_probe_does_not_touch_production_stores():
    """壊す不変条件: C-3（本番ストアに一切書かない）
    ／通したい検査経路: run_probe 実行前後で本番3ストア + utterances.db の byte hash 不変。
    """
    paths = p.production_store_paths()
    before = p.snapshot_production_hashes(paths)
    p.run_probe(
        sessions_root=_REAL_CODEX_SESSIONS_ROOT,
        base_date=_REAL_HOME_TEST_BASE_DATE,
        days=_REAL_HOME_TEST_DAYS,
    )
    after = p.snapshot_production_hashes(paths)
    ok, violations = p.verify_production_unchanged(before, after)
    assert ok is True, violations


# ─────────────────────────────────────────────────────────────────
# Must1: prev_action（ツール呼び出し名の集約）
# ─────────────────────────────────────────────────────────────────
def test_prev_action_collects_only_tool_calls_since_last_user_utterance(tmp_path):
    """壊す不変条件: Must1（prev_action は「直前の human 発話より後・当該発話より前」の
    tool 呼び出しのみを含む。CC 側 extractor.py の定義と同一）
    ／通したい検査経路: parse_session_file の pending_tool_names 集約・リセット。

    変異（直前の user 発話より前の tool 呼び出しまで含めてしまう＝リセットしない）を
    適用すると、2番目の発話の prev_action に "toolA" が二重に混入し本テストが落ちる。
    """
    lines = [
        _session_meta("s1", ts="2026-08-20T00:00:00.000Z"),
        _response_user("最初の発話", ts="2026-08-20T00:00:01.000Z"),
        _function_call("toolA", ts="2026-08-20T00:00:02.000Z"),
        _function_call("toolB", ts="2026-08-20T00:00:03.000Z"),
        _response_user("2番目の発話", ts="2026-08-20T00:00:04.000Z"),
        # 3番目: 直前(2番目)以降に tool 呼び出しが無い。reset が効いていれば None、
        # 効いていなければ toolA/toolB が漏れ出す（mutationA の検出点はここ）。
        _response_user("3番目の発話", ts="2026-08-20T00:00:05.000Z"),
    ]
    path = _write(tmp_path, "prev_action.jsonl", lines)
    pf = p.parse_session_file(path)
    by_text = {c.text: c.prev_action for c in pf.candidates}
    assert by_text["最初の発話"] is None
    assert by_text["2番目の発話"] == "toolA,toolB"
    assert by_text["3番目の発話"] is None


def test_prev_action_caps_at_ten_with_ellipsis(tmp_path):
    """壊す不変条件: Must1（上限10・超過時 "…"。CC 側 _format_prev_action と同一整形）
    ／通したい検査経路: _format_prev_action（extractor.py から re-use、自作しない）。
    変異（上限チェックを外し ",".join のみにする）を適用すると本テストが落ちる。
    """
    lines = [_session_meta("s1", ts="2026-08-20T00:00:00.000Z"),
              _response_user("発話1", ts="2026-08-20T00:00:01.000Z")]
    for i in range(12):
        lines.append(_function_call(f"tool{i}", ts=f"2026-08-20T00:00:0{2 + i % 8}.000Z"))
    lines.append(_response_user("発話2", ts="2026-08-20T00:01:00.000Z"))
    path = _write(tmp_path, "prev_action_cap.jsonl", lines)
    pf = p.parse_session_file(path)
    by_text = {c.text: c.prev_action for c in pf.candidates}
    expected = ",".join(f"tool{i}" for i in range(10)) + ",…"
    assert by_text["発話2"] == expected


def test_prev_action_duplicate_channel_reuses_same_snapshot(tmp_path):
    """壊す不変条件: Must1（response_item/event_msg の重複表現は、どちらが先着でも
    同じ prev_action を持つ。実測: event_msg が先着するケースが多数派 2185/2772）
    ／通したい検査経路: parse_session_file の _next_prev_action の text_hash 判定。
    """
    dup_text = "重複発話"
    lines = [
        _session_meta("s1", ts="2026-08-20T00:00:00.000Z"),
        _function_call("toolA", ts="2026-08-20T00:00:01.000Z"),
        _event_user_message(dup_text, ts="2026-08-20T00:00:02.000Z"),  # 先着（多数派パターン）
        _response_user(dup_text, ts="2026-08-20T00:00:02.010Z"),  # 直後の重複
    ]
    path = _write(tmp_path, "dup_channel_prev_action.jsonl", lines)
    pf = p.parse_session_file(path)
    assert len(pf.candidates) == 2
    assert pf.candidates[0].prev_action == "toolA"
    assert pf.candidates[1].prev_action == "toolA"


@pytest.mark.real_home
@_skip_if_no_real_codex_sessions
def test_prev_action_populated_end_to_end_against_real_data():
    """陽性対照 + 統合検査: 実データで prev_action が非 None の発話が実在すること
    （全件 None のまま構造的に歪んでいないこと・Must1 反映確認）。
    """
    result = p.run_probe(
        sessions_root=_REAL_CODEX_SESSIONS_ROOT,
        base_date=_REAL_HOME_TEST_BASE_DATE,
        days=_REAL_HOME_TEST_DAYS,
    )
    non_none = sum(1 for u in result.utterances if u["prev_action"] is not None)
    assert non_none > 0


# ─────────────────────────────────────────────────────────────────
# Must2: --out-dir 拒否ガード
# ─────────────────────────────────────────────────────────────────
def test_validate_out_dir_rejects_claude_home(tmp_path):
    """壊す不変条件: Must2（~/.claude/ 配下への出力を拒否する）
    ／通したい検査経路: validate_out_dir。
    """
    fake_home = tmp_path / "home"
    (fake_home / ".claude" / "evolve-anything").mkdir(parents=True)
    import unittest.mock as mock
    with mock.patch.object(Path, "home", return_value=fake_home):
        reason = p.validate_out_dir(fake_home / ".claude" / "evolve-anything" / "pwned")
    assert reason is not None


def test_validate_out_dir_rejects_codex_home(tmp_path):
    """壊す不変条件: Must2（~/.codex/ 配下への出力を拒否する）。"""
    fake_home = tmp_path / "home"
    (fake_home / ".codex").mkdir(parents=True)
    import unittest.mock as mock
    with mock.patch.object(Path, "home", return_value=fake_home):
        reason = p.validate_out_dir(fake_home / ".codex" / "pwned")
    assert reason is not None


def test_validate_out_dir_rejects_repo_root():
    """壊す不変条件: Must2（リポジトリ作業ディレクトリ配下への出力を拒否する）。"""
    repo_root = Path(p.__file__).resolve().parent.parent
    reason = p.validate_out_dir(repo_root / "scripts" / "pwned")
    assert reason is not None


def test_validate_out_dir_accepts_isolated_tmp_dir(tmp_path, monkeypatch, tmp_path_factory):
    """陽性対照: 隔離された一時ディレクトリは拒否されない。

    root conftest.py の autouse HOME/DATA_DIR 隔離が全テストで
    ``CLAUDE_PLUGIN_DATA=str(tmp_path)`` を設定するため、DATA_DIR が
    item1 の禁止ルートに加わった今、素の ``tmp_path`` を out_dir に使うと
    「DATA_DIR 配下の出力」として常に拒否されてしまう（本テストが検査したい
    「無関係な隔離ディレクトリは拒否されない」という主張とは別の理由で赤くなる）。
    DATA_DIR を tmp_path と無関係な場所へ明示的にずらして検査する。
    """
    unrelated_data_dir = tmp_path_factory.mktemp("unrelated_data_dir")
    monkeypatch.setattr(p, "resolve_evolve_anything_data_dir", lambda: unrelated_data_dir)
    reason = p.validate_out_dir(tmp_path / "phase1_out")
    assert reason is None


def test_main_exits_nonzero_and_creates_no_files_for_forbidden_out_dir(tmp_path):
    """壊す不変条件: Must2（起動時に拒否し、禁止ディレクトリ配下へ何も作らない）
    ／通したい検査経路: main() の validate_out_dir 呼び出し（mkdir より前）。
    レビュアー提供の再現手順の pytest 版。変異（拒否チェックを外す）を適用すると、
    exit code が 0 になり、かつ禁止ディレクトリ配下にファイルが生成され本テストが落ちる。
    """
    fake_home = tmp_path / "home"
    sessions_root = fake_home / "sessions"
    sessions_root.mkdir(parents=True)
    forbidden = fake_home / ".codex"
    import unittest.mock as mock
    with mock.patch.object(Path, "home", return_value=fake_home):
        rc = p.main([
            "--sessions-root", str(sessions_root),
            "--base-date", "2026-08-23",
            "--out-dir", str(forbidden),
        ])
    assert rc != 0
    assert not forbidden.exists() or list(forbidden.iterdir()) == []


# ─────────────────────────────────────────────────────────────────
# Should: dedup 6則（複数候補群のカバー）
# ─────────────────────────────────────────────────────────────────
def _cand(channel, line_no, ts_ms, text, session_id="s1"):
    return p.RawCandidate("f1", channel, line_no, f"ts{ts_ms}", ts_ms, text, None, session_id, None)


def test_dedup_multi_candidate_group_picks_min_delta_with_line_no_tiebreak():
    """壊す不変条件: X1 マッチング6則（走査順・最小delta・同値時line_no・1:1消費）
    ／通したい検査経路: dedup_channel_constrained。
    実データに53群実在する「同一(file,text_hash)で複数候補を持つケース」を模した
    fixture（複数response_item・複数event_msg・入力順を逆転）で6則を固定する。
    """
    # 2つの異なる発話（text_hash が異なる）それぞれに複数候補群を持たせる。
    # 発話A: response_item(t=100) に対し event_msg 候補が [t=140(delta40,line10), t=90(delta10,line5)]
    #        → 最小delta(10)の line_no=5 を選ぶ（規則2）
    # 発話B: response_item(t=500) に対し event_msg 候補が [t=520(delta20,line20), t=480(delta20,line3)]
    #        → delta 同値(20) → line_no が小さい方(line3)を選ぶ（規則3）
    candidates = [
        # 入力順をわざと逆転させる（response_item 側が先とは限らない現実を模す）
        _cand("event_msg", 10, 140.0, "発話A"),
        _cand("event_msg", 5, 90.0, "発話A"),
        _cand("response_item", 1, 100.0, "発話A"),
        _cand("event_msg", 20, 520.0, "発話B"),
        _cand("event_msg", 3, 480.0, "発話B"),
        _cand("response_item", 2, 500.0, "発話B"),
    ]
    out = p.dedup_channel_constrained(candidates, threshold_ms=100)
    # 1:1消費: 発話A・発話Bとも response_item側1件ずつが残り、event_msg側の
    # 未マッチ1件ずつ（マッチしなかった方）が独立して残る＝各3件中2件がdedupで1件に。
    assert len(out) == 4  # response_item x2（代表） + 未マッチevent_msg x2
    resp_out = [c for c in out if c.channel == "response_item"]
    assert {c.text for c in resp_out} == {"発話A", "発話B"}

    evm_unmatched = [c for c in out if c.channel == "event_msg"]
    # 発話A: line_no=5(delta10)がマッチ済みで消費される→残るのはline_no=10(delta40)
    # 発話B: line_no=3(delta20)がマッチ済みで消費される→残るのはline_no=20(delta20)
    assert {c.line_no for c in evm_unmatched} == {10, 20}


def test_dedup_scan_order_is_timestamp_then_line_no_ascending():
    """壊す不変条件: X1規則1（response_item側は(timestamp,line_no)"昇順"で走査する）
    ／通したい検査経路: dedup_channel_constrained 内の resp ソート。

    昇順走査が貪欲法の結果に実際に影響する fixture（r1 は eA のみ到達可能・r2 は
    eA/eB 両方に到達可能で eA を強く優先）を使う。昇順（r1が先着）なら r1 が eA を
    確保し r2 は eB へフォールバックして両方消費される。降順（r2が先着）なら
    r2 が eA を奪い r1 は行き場を失い、eB は誰にも消費されず生き残る。
    """
    threshold = 1000.0
    r1 = _cand("response_item", 1, 0.0, "同文言")
    r2 = _cand("response_item", 2, 100.0, "同文言")
    eA = _cand("event_msg", 10, 50.0, "同文言")  # delta(r1,eA)=50 / delta(r2,eA)=50
    eB = _cand("event_msg", 20, 1090.0, "同文言")  # delta(r1,eB)=1090(不可) / delta(r2,eB)=990(可・eAより悪い)

    out = p.dedup_channel_constrained([r1, r2, eA, eB], threshold_ms=threshold)
    # 昇順(r1→r2)なら r1がeAを確保・r2はeBへフォールバック=両方消費→残るのはresponse_item 2件のみ。
    assert len(out) == 2
    assert {c.channel for c in out} == {"response_item"}


def test_dedup_one_to_one_consumption_not_shared():
    """壊す不変条件: X1規則5（マッチした対は双方消費済み＝1:1。同じevent_msg候補を
    複数のresponse_item候補が使い回さない）
    ／通したい検査経路: dedup_channel_constrained の matched_evm_ids 消費。
    """
    # 同一text_hashのresponse_item候補が2件、event_msg候補は1件のみ。
    # 1:1なら2件目のresponse_itemは誰にもマッチせず、それぞれ独立して残るため
    # 最終的にresponse_item2件 + event_msg0件（1件はマッチ消費）= 2件のまま。
    r1 = _cand("response_item", 1, 100.0, "同文言")
    r2 = _cand("response_item", 2, 101.0, "同文言")
    e1 = _cand("event_msg", 3, 100.0, "同文言")
    out = p.dedup_channel_constrained([r1, r2, e1], threshold_ms=100)
    assert len(out) == 2
    assert {c.channel for c in out} == {"response_item"}


# ─────────────────────────────────────────────────────────────────
# 本番ストア保護ガードの欠陥修正（team-lead 指摘・#534）
# 欠陥1: hashes_after が最後の書込み（report.json）より前に取られる
# 欠陥2: out_dir 検査がディレクトリにしか掛からず、出力ファイル名の symlink 経由で
#        本番ファイルへ書込める
# ─────────────────────────────────────────────────────────────────
def test_write_json_refuses_symlink_escaping_into_forbidden_root(tmp_path, monkeypatch):
    """壊す不変条件: 欠陥2（out_dir 内の出力ファイル名 symlink が本番ファイルへ
    書込むのを拒否する）
    ／通したい検査経路: _write_json が書込み直前に実体パス（symlink 解決後）を
    forbidden_out_dir_roots() と照合する。

    修正前は _write_json が path.write_text をそのまま呼ぶだけで実体パスの検査が
    無く、symlink を辿って victim（本番ファイル役）を上書きしてしまい本テストが
    落ちる。
    """
    victim_dir = tmp_path / "forbidden_root"
    victim_dir.mkdir()
    victim = victim_dir / "utterances.db"
    victim.write_bytes(b"PRISTINE")
    monkeypatch.setattr(p, "forbidden_out_dir_roots", lambda: [victim_dir])

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    evil_link = out_dir / "report.json"
    evil_link.symlink_to(victim)

    with pytest.raises(p.ProductionPathWriteError):
        p._write_json(evil_link, {"x": 1}, out_dir=out_dir)

    assert victim.read_bytes() == b"PRISTINE"


def test_write_json_allows_normal_path_inside_out_dir(tmp_path, monkeypatch, tmp_path_factory):
    """陽性対照: 禁止ルートに触れない通常の out_dir 書込みは成功する。

    root conftest.py の autouse 隔離が DATA_DIR=tmp_path を強制するため、
    item1 導入後は素の tmp_path 配下の out_dir が DATA_DIR 配下と誤認され拒否
    されてしまう。DATA_DIR を tmp_path と無関係な場所へずらして検査する
    （test_validate_out_dir_accepts_isolated_tmp_dir と同じ理由）。
    """
    unrelated_data_dir = tmp_path_factory.mktemp("unrelated_data_dir")
    monkeypatch.setattr(p, "resolve_evolve_anything_data_dir", lambda: unrelated_data_dir)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    p._write_json(out_dir / "report.json", {"x": 1}, out_dir=out_dir)
    assert json.loads((out_dir / "report.json").read_text(encoding="utf-8")) == {"x": 1}


def test_write_json_refuses_escape_outside_out_dir_even_if_not_forbidden_root(tmp_path, monkeypatch):
    """壊す不変条件: item2（out_dir 内の出力ファイル名が、禁止ルート
    （~/.claude・~/.codex・repo・DATA_DIR）のいずれでもない out_dir 外の場所への
    symlink でも拒否される）
    ／通したい検査経路: _write_json の out_dir 包含チェック（relative_to）。
    修正前（out_dir 引数が無く禁止ルート照合のみだった版）は、この escape先が
    禁止ルート allowlist の外なので検査を素通りし本テストが落ちる。

    root conftest.py の autouse 隔離は DATA_DIR=tmp_path を強制するため、
    ``elsewhere`` を素の tmp_path 配下に置くと（意図せず）禁止ルート照合
    （item1 の DATA_DIR 検査）でも引っかかってしまい、item2 固有の効果を
    検査できない。forbidden_out_dir_roots を無関係な場所に固定し、この escape
    が「禁止ルート照合には掛からないが out_dir 包含チェックでは掛かる」ことを
    確実にする。
    """
    unrelated_root = tmp_path / "unrelated_forbidden_root"
    unrelated_root.mkdir()
    monkeypatch.setattr(p, "forbidden_out_dir_roots", lambda: [unrelated_root])

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    victim = elsewhere / "victim.json"
    victim.write_bytes(b"PRISTINE")
    evil_link = out_dir / "report.json"
    evil_link.symlink_to(victim)

    with pytest.raises(p.ProductionPathWriteError):
        p._write_json(evil_link, {"x": 1}, out_dir=out_dir)

    assert victim.read_bytes() == b"PRISTINE"


def test_main_write_json_containment_closes_the_defect1_escape_route(tmp_path, monkeypatch):
    """壊す不変条件: 旧欠陥1（hashes_after が report.json 書込みより前に取られると、
    symlink 経由の本番汚染が検出漏れになる）の攻撃面が、item2（out_dir 包含チェック）
    導入により **report.json の書込み自体でブロックされ、そもそも hashes_after
    取得順序に依存しなくなった** ことを検査する。

    production_store_paths / resolve_evolve_anything_data_dir を意図的に
    forbidden_out_dir_roots 外（out_dir とも victim とも非重複な第三の場所）へ
    向け、旧テストが利用していた「禁止ルート照合だけでは symlink を検出でき
    ない」状況を再現した上で、out_dir 内の report.json という出力ファイル名を
    経由した symlink が victim（本番ストア役）を指していても、_write_json の
    out_dir 包含チェックにより書込み前に例外で止まり victim が汚染されないこと
    を確認する（victim を DATA_DIR 自体の配下に置かないのは、それだと item1 の
    禁止ルート照合だけで検出できてしまい item2 固有の効果を切り分けられない
    ため）。
    ／通したい検査経路: main() 内 _write_json(report.json, out_dir=out_dir) の
    relative_to(out_dir) 検査。
    修正前（out_dir 引数の無い版）はこの escape を検出できず、hashes_after が
    report.json 書込み前に取られていれば ok=True のまま victim が汚染されて
    返っていた（旧テストが検出していた検出漏れそのもの）。
    """
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()

    victim_dir = tmp_path / "victim_store"
    victim_dir.mkdir()
    victim = victim_dir / "utterances.db"
    victim.write_bytes(b"PRISTINE")

    unrelated_data_dir = tmp_path / "unrelated_data_dir"
    unrelated_data_dir.mkdir()

    monkeypatch.setattr(p, "production_store_paths", lambda: {"utterances_db": victim})
    monkeypatch.setattr(p, "resolve_evolve_anything_data_dir", lambda: unrelated_data_dir)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "report.json").symlink_to(victim)

    with pytest.raises(p.ProductionPathWriteError):
        p.main([
            "--sessions-root", str(sessions_root),
            "--base-date", "2026-08-23",
            "--out-dir", str(out_dir),
        ])

    assert victim.read_bytes() == b"PRISTINE"


def test_main_symlink_into_true_forbidden_root_leaves_victim_untouched(tmp_path, monkeypatch):
    """壊す不変条件: 欠陥2（main() 実行の end-to-end 経路でも、out_dir 内の
    出力ファイル名 symlink が forbidden_out_dir_roots() 配下の実ファイルを
    上書きできない）
    ／通したい検査経路: main() → _write_json の実体パス検査。

    ``_write_json`` 単体テストと異なり、main() を通しで呼び、production_store_paths /
    forbidden_out_dir_roots の双方を同じ偽ルート配下に揃えることで、実際の
    CLI 経路（run_probe→複数ファイル書込→report.json→guard.json）で欠陥2の
    ガードが機能することを検査する。仕様上「例外で止まる」ことも合格
    （委譲プロンプトの (a) 要件）なので、例外送出・非0終了のどちらでも
    victim が変化しないことのみを assert する。
    """
    forbidden_root = tmp_path / "forbidden_root"
    forbidden_root.mkdir()
    victim = forbidden_root / "utterances.db"
    victim.write_bytes(b"PRISTINE")

    monkeypatch.setattr(p, "forbidden_out_dir_roots", lambda: [forbidden_root])
    monkeypatch.setattr(p, "production_store_paths", lambda: {"utterances_db": victim})
    monkeypatch.setattr(p, "resolve_evolve_anything_data_dir", lambda: forbidden_root)

    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "report.json").symlink_to(victim)

    try:
        rc = p.main([
            "--sessions-root", str(sessions_root),
            "--base-date", "2026-08-23",
            "--out-dir", str(out_dir),
        ])
    except p.ProductionPathWriteError:
        pass
    else:
        assert rc != 0

    assert victim.read_bytes() == b"PRISTINE"


def test_main_out_dir_rejection_still_effective(tmp_path):
    """陽性対照 + 回帰検査: 既存の --out-dir 拒否ガード（禁止ルート直指定）が
    欠陥1・欠陥2の修正後も引き続き効くこと。"""
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    repo_root = Path(p.__file__).resolve().parent.parent
    rc = p.main([
        "--sessions-root", str(sessions_root),
        "--base-date", "2026-08-23",
        "--out-dir", str(repo_root / "scripts" / "pwned_by_test"),
    ])
    assert rc != 0
    assert not (repo_root / "scripts" / "pwned_by_test").exists()


def test_main_production_store_guard_ok_true_on_clean_run(tmp_path, monkeypatch):
    """陽性対照: symlink 攻撃や汚染の無い通常実行では production_store_guard.ok
    が True のまま、report.json / guard.json の双方が生成されること。"""
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()

    isolated_store_dir = tmp_path / "isolated_store"
    monkeypatch.setattr(
        p, "production_store_paths",
        lambda: {"utterances_db": isolated_store_dir / "utterances.db"},
    )
    monkeypatch.setattr(p, "resolve_evolve_anything_data_dir", lambda: isolated_store_dir)

    out_dir = tmp_path / "out"
    rc = p.main([
        "--sessions-root", str(sessions_root),
        "--base-date", "2026-08-23",
        "--out-dir", str(out_dir),
    ])

    assert rc == 0
    guard = json.loads((out_dir / "guard.json").read_text(encoding="utf-8"))
    assert guard["ok"] is True
    assert guard["violations"] == []
    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert "counts" in report
    assert "production_store_guard" not in report


# ─────────────────────────────────────────────────────────────────
# team-lead 追加指摘（外部レビュー #536・item1〜5）
# ─────────────────────────────────────────────────────────────────
def test_validate_out_dir_rejects_dynamic_data_dir(tmp_path, monkeypatch):
    """壊す不変条件: item1（DATA_DIR が CLAUDE_PLUGIN_DATA で ~/.claude 配下以外の
    任意の場所を指す custom 構成でも、その配下への --out-dir は禁止される）
    ／通したい検査経路: forbidden_out_dir_roots が resolve_evolve_anything_data_dir()
    を含めること。
    修正前（ハードコード3ルートのみ）は custom DATA_DIR がどの禁止ルートにも
    一致せず reason が None になり本テストが落ちる。
    """
    custom_data_dir = tmp_path / "custom_data"
    custom_data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(custom_data_dir))
    reason = p.validate_out_dir(custom_data_dir / "phase1_out")
    assert reason is not None


def test_validate_out_dir_accepts_dir_outside_dynamic_data_dir(tmp_path, monkeypatch):
    """陽性対照: custom DATA_DIR 構成でも、それと無関係な隔離ディレクトリは拒否
    されない。"""
    custom_data_dir = tmp_path / "custom_data"
    custom_data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(custom_data_dir))
    reason = p.validate_out_dir(tmp_path / "isolated_out")
    assert reason is None


def test_out_dir_cannot_be_nested_inside_data_dir_end_to_end(tmp_path, monkeypatch):
    """壊す不変条件: item3（item1 のガードにより out_dir は DATA_DIR 配下になり
    得ないため、guard.json を事後 hash 採取後に書いても DATA_DIR の観測範囲を
    汚染しないという設計上の主張が、main() の実行経路でも実際に成立する）
    ／通したい検査経路: main() 起動時の validate_out_dir（forbidden_out_dir_roots
    経由で DATA_DIR を検査）。
    修正前は DATA_DIR が禁止ルートに含まれないため、この --out-dir はそのまま
    受理され、DATA_DIR 配下にファイル一式（guard.json 含む）が作成されてしまい
    本テストが落ちる。
    """
    fake_home = tmp_path / "home"
    sessions_root = fake_home / "sessions"
    sessions_root.mkdir(parents=True)
    data_dir = fake_home / "data"
    data_dir.mkdir()
    monkeypatch.setattr(p, "resolve_evolve_anything_data_dir", lambda: data_dir)
    nested_out = data_dir / "phase1_out"

    rc = p.main([
        "--sessions-root", str(sessions_root),
        "--base-date", "2026-08-23",
        "--out-dir", str(nested_out),
    ])

    assert rc != 0
    assert not nested_out.exists()


def test_snapshot_data_dir_listing_detects_same_size_content_change(tmp_path):
    """壊す不変条件: item4（同一 byte 数のまま内容だけ書き換えられた変更を
    size 比較では見逃すが hash 比較なら検出する）
    ／通したい検査経路: snapshot_data_dir_listing + verify_data_dir_unchanged。
    修正前（サイズのみ比較）は "AAAA"→"BBBB"（同じ4byte）を差分として検出できず
    本テストが落ちる。
    """
    d = tmp_path / "data"
    d.mkdir()
    f = d / "utterances.db"
    f.write_bytes(b"AAAA")
    before = p.snapshot_data_dir_listing(d)
    f.write_bytes(b"BBBB")
    after = p.snapshot_data_dir_listing(d)
    ok, violations = p.verify_data_dir_unchanged(before, after)
    assert ok is False
    assert "utterances.db" in violations[0]


def test_snapshot_data_dir_listing_passes_when_unchanged(tmp_path):
    """陽性対照: 内容が変化していなければ hash 比較でも合格する。"""
    d = tmp_path / "data"
    d.mkdir()
    f = d / "x.jsonl"
    f.write_bytes(b"same-content")
    before = p.snapshot_data_dir_listing(d)
    after = p.snapshot_data_dir_listing(d)
    ok, violations = p.verify_data_dir_unchanged(before, after)
    assert ok is True
    assert violations == []


# ─────────────────────────────────────────────────────────────────
# item5: 機構除外の独立オラクル（範囲・単調性チェックだけでは検出できない
# 「同数の別種入替」を検出する）
# ─────────────────────────────────────────────────────────────────
def _small_fixture_for_oracle(sessions_root: Path) -> Path:
    lines = [
        _session_meta("s1"),
        _response_user("<recommended_plugins>\n機構発話", ts="2026-08-20T00:00:01.000Z"),
        _response_user("普通の発話", ts="2026-08-20T00:00:02.000Z"),
    ]
    date_dir = sessions_root / "2026" / "08" / "20"
    date_dir.mkdir(parents=True)
    return _write(date_dir, "oracle.jsonl", lines)


def test_machinery_exclusion_oracle_passes_on_correct_pipeline(tmp_path):
    """陽性対照: filter_machinery が正しく配線されている通常実行では、独立
    オラクルと実際の after_machinery_exclusion が一致し例外が出ない。"""
    _small_fixture_for_oracle(tmp_path)
    result = p.run_probe(sessions_root=tmp_path, base_date=date(2026, 8, 20), days=1)
    p.assert_machinery_exclusion_matches_oracle(result)  # 例外が出ないこと自体が検査
    assert result.counts.after_machinery_exclusion == 1


def test_machinery_exclusion_oracle_detects_disabled_filter(tmp_path, monkeypatch):
    """壊す不変条件: item5（機構除外ステージがパイプラインから外れる＝配線切れ。
    段階間の非増加関係・絶対値レンジだけでは、除外対象が0件になっても
    raw>=after_child_exclusion>=after_machinery_exclusion>=after_dedup と
    レンジ自体は別途破れない限り検出できない）
    ／通したい検査経路: assert_machinery_exclusion_matches_oracle が
    normalized_events から独立に再計算した期待値との不一致を検出する。
    変異④相当（filter_machinery を恒等関数化し検査を無効化する）を run_probe の
    呼び出し点で直接適用する。
    """
    _small_fixture_for_oracle(tmp_path)
    monkeypatch.setattr(p, "filter_machinery", lambda candidates: list(candidates))
    result = p.run_probe(sessions_root=tmp_path, base_date=date(2026, 8, 20), days=1)
    # 配線切れにより機構発話も残ってしまう（本来1件のはずが2件）。
    assert result.counts.after_machinery_exclusion == 2
    with pytest.raises(AssertionError):
        p.assert_machinery_exclusion_matches_oracle(result)


def test_machinery_exclusion_oracle_detects_swap_same_count_different_texts(tmp_path):
    """壊す不変条件: item5 の分散・入替変異（変異③相当）。除外件数
    （after_machinery_exclusion）はレンジ・非増加関係を満たしたまま、除外対象の
    中身だけが入れ替わる（＝機構発話が残り、代わりに関係ない通常発話が誤って
    落とされる）ケースを、独立オラクルの多重集合（Counter）完全一致で検出する
    （件数だけの突合ではこの入替を見逃す）。
    ここでは filter_machinery を「機構発話でなく末尾の候補を1件落とす」偽実装に
    差し替え、除外後件数は正しい（1件）のに中身が入れ替わっていることを示す。
    """
    _small_fixture_for_oracle(tmp_path)

    def _wrong_filter(candidates):
        # 機構マーカーで除外する代わりに、末尾の候補（本来除外されるべきでない
        # 普通の発話）を1件だけ落とす壊れた実装。
        return list(candidates)[:-1]

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(p, "filter_machinery", _wrong_filter)
        result = p.run_probe(sessions_root=tmp_path, base_date=date(2026, 8, 20), days=1)
        # 件数だけ見ればレンジ・非増加関係は満たしうるが、中身は入れ替わっている
        # （残った候補は「機構発話」であり「普通の発話」が誤って落ちている）。
        assert result.counts.after_machinery_exclusion == 1
        surviving_texts = {u["text"] for u in result.utterances}
        assert "普通の発話" not in surviving_texts  # 壊れた実装の症状を確認
        with pytest.raises(AssertionError):
            p.assert_machinery_exclusion_matches_oracle(result)
    finally:
        monkeypatch.undo()
