"""`scripts/bench/measure_467_proposal_kinds.py` の read-only / network guard の発火テスト
（2026-08-16 codex cold review 4巡目 [Must]5 是正）。

対象: `WriteGuardViolation` / `NetworkGuardViolation` が実際に発火すること、
guard 違反が §1.5.3 の個別 kind 隔離（try/except Exception）に飲まれず伝播すること
（[Must]4 の回帰）。

conftest の autouse `_isolate_home_default`（#471）により `Path.home()` は本テストでも
既に空の tmp dir に隔離済み。`Path.home() / ".claude"` をそのまま guard の対象
（`home_claude`）として使えば、実 `~/.claude/` には一切触れない。
"""
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

_BENCH_DIR = Path(__file__).resolve().parent.parent.parent / "bench"
if str(_BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCH_DIR))

import measure_467_proposal_kinds as m  # noqa: E402


@pytest.fixture
def home_claude() -> Path:
    hc = Path.home() / ".claude"
    hc.mkdir(parents=True, exist_ok=True)
    return hc


# ── write guard ──────────────────────────────────────────────────────────


def test_path_write_text_under_home_claude_blocked(home_claude):
    """`Path.write_text` は `io.open` 経由。builtins.open だけの差し替えでは素通りする
    （2026-08-16 実測で確認した回帰。旧実装はこのテストで green のまま検出できなかった）。"""
    target = home_claude / "guard-test-write-text.txt"
    with pytest.raises(m.WriteGuardViolation):
        with m.guard_no_home_claude_writes(home_claude):
            target.write_text("nope", encoding="utf-8")
    assert not target.exists()


def test_path_write_bytes_under_home_claude_blocked(home_claude):
    target = home_claude / "guard-test-write-bytes.bin"
    with pytest.raises(m.WriteGuardViolation):
        with m.guard_no_home_claude_writes(home_claude):
            target.write_bytes(b"nope")
    assert not target.exists()


def test_builtin_open_write_mode_under_home_claude_blocked(home_claude):
    target = home_claude / "guard-test-builtin-open.txt"
    with pytest.raises(m.WriteGuardViolation):
        with m.guard_no_home_claude_writes(home_claude):
            open(target, "w", encoding="utf-8")  # noqa: SIM115
    assert not target.exists()


def test_os_open_write_mode_under_home_claude_blocked(home_claude):
    target = home_claude / "guard-test-os-open.txt"
    with pytest.raises(m.WriteGuardViolation):
        with m.guard_no_home_claude_writes(home_claude):
            fd = os.open(str(target), os.O_WRONLY | os.O_CREAT)
            os.close(fd)
    assert not target.exists()


def test_os_rename_under_home_claude_blocked(home_claude):
    src = home_claude / "guard-test-rename-src.txt"
    src.write_text("x", encoding="utf-8")  # guard 適用外でのセットアップ
    dst = home_claude / "guard-test-rename-dst.txt"
    with pytest.raises(m.WriteGuardViolation):
        with m.guard_no_home_claude_writes(home_claude):
            os.rename(str(src), str(dst))
    assert src.exists()
    assert not dst.exists()


def test_os_unlink_under_home_claude_blocked(home_claude):
    target = home_claude / "guard-test-unlink.txt"
    target.write_text("x", encoding="utf-8")  # guard 適用外でのセットアップ
    with pytest.raises(m.WriteGuardViolation):
        with m.guard_no_home_claude_writes(home_claude):
            os.unlink(str(target))
    assert target.exists()


def test_read_only_open_under_home_claude_not_blocked(home_claude):
    """read モードは対象外（測定本体の read-only アクセスまで壊さないことの対照）。"""
    target = home_claude / "guard-test-readonly.txt"
    target.write_text("hello", encoding="utf-8")
    with m.guard_no_home_claude_writes(home_claude):
        assert target.read_text(encoding="utf-8") == "hello"


def test_write_outside_home_claude_not_blocked(tmp_path, home_claude):
    """`~/.claude/` 配下でなければ書込みは通る（narrow しすぎていないことの対照）。"""
    outside = tmp_path / "outside.txt"
    with m.guard_no_home_claude_writes(home_claude):
        outside.write_text("fine", encoding="utf-8")
    assert outside.read_text(encoding="utf-8") == "fine"


# ── network guard ────────────────────────────────────────────────────────


def test_socket_socket_blocked():
    with pytest.raises(m.NetworkGuardViolation):
        with m.guard_no_network():
            socket.socket(socket.AF_INET, socket.SOCK_STREAM)


def test_subprocess_popen_blocked():
    with pytest.raises(m.NetworkGuardViolation):
        with m.guard_no_network():
            subprocess.Popen(["true"])


def test_os_system_blocked():
    with pytest.raises(m.NetworkGuardViolation):
        with m.guard_no_network():
            os.system("true")


def test_os_execv_blocked():
    with pytest.raises(m.NetworkGuardViolation):
        with m.guard_no_network():
            os.execv("/bin/true", ["/bin/true"])


def test_network_guard_restores_originals_after_context_exit():
    """guard を抜けたら実物（この repo では root conftest の LLM guard 関数）に戻ること
    （他テストへの汚染防止の対照）。"""
    real_socket_cls = socket.socket
    real_popen = subprocess.Popen
    with m.guard_no_network():
        pass
    assert socket.socket is real_socket_cls
    assert subprocess.Popen is real_popen


# ── Must-4 回帰: guard 違反は個別 kind 隔離（except Exception）に飲まれない ─────────


def test_guard_violation_propagates_through_measure_1_5_3_kind_isolation(
    monkeypatch, tmp_path,
):
    """§1.5.3 の個別 kind 用 try/except は kind ごとのエラーを隔離するが、guard 違反
    （WriteGuardViolation/NetworkGuardViolation）はそこで握り潰さず外側へ伝播すること。

    `_measure_stall_recovery` が実際に依存する `tool_usage_analyzer.extract_tool_calls_by_session`
    を差し替えて guard 違反を注入する（call-time local import なので module 属性の
    monkeypatch がそのまま効く）。この修正前は broad `except Exception` が
    WriteGuardViolation も通常の kind エラーとして飲み込み、`errors["stall_recovery_patterns"]`
    に格納するだけで測定全体は正常終了してしまっていた。
    """
    import tool_usage_analyzer  # noqa: PLC0415

    def _raise_write_guard_violation(*_a, **_kw):
        raise m.WriteGuardViolation("synthetic violation injected by test")

    monkeypatch.setattr(
        tool_usage_analyzer, "extract_tool_calls_by_session", _raise_write_guard_violation,
    )

    project_root = tmp_path / "project"
    project_root.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    with pytest.raises(m.WriteGuardViolation):
        m.measure_1_5_3(project_root, data_dir)


def test_guard_violation_leaves_no_kind_error_entry_when_propagated(monkeypatch, tmp_path):
    """上記と対照: 修正前の挙動（`errors` に格納されて正常 return する）が再発していないこと。"""
    import tool_usage_analyzer  # noqa: PLC0415

    def _raise_write_guard_violation(*_a, **_kw):
        raise m.WriteGuardViolation("synthetic violation injected by test")

    monkeypatch.setattr(
        tool_usage_analyzer, "extract_tool_calls_by_session", _raise_write_guard_violation,
    )

    project_root = tmp_path / "project"
    project_root.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    try:
        m.measure_1_5_3(project_root, data_dir)
    except m.WriteGuardViolation:
        pass
    else:
        pytest.fail(
            "measure_1_5_3 が guard 違反を吸収して正常終了した"
            "（stall_recovery_patterns が errors に格納されただけの旧挙動に回帰している）"
        )


# ── Must-5 回帰: safety_verification が違反時に "passed" を報告しない ─────────────


def test_run_guarded_measurement_raises_and_reports_violation(monkeypatch, tmp_path, home_claude):
    """`run_guarded_measurement` は違反時に例外を re-raise し、呼び出し元
    （`main()`）が戻り値を受け取れないこと。main() は正常戻り値が無ければ
    safety_verification を含む出力 JSON を一切書かないため、これにより
    「guard 違反があったのに artifact が passed を報告する」を構造的に防ぐ。
    """
    def _write_under_home_claude(*_a, **_kw):
        (home_claude / "should-not-exist.jsonl").write_text("x", encoding="utf-8")
        return []

    # measure_1_5_1 が最初に呼ぶ corrections/usage の読み取りをすり替えて、guard 配下で
    # 実際に禁止された書込みを1回発生させる（合成違反ではなく実際の write-mode open）。
    monkeypatch.setattr(m, "measure_1_5_1", lambda *_a, **_kw: _write_under_home_claude())

    project_root = tmp_path / "project"
    project_root.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    with pytest.raises(m.WriteGuardViolation):
        m.run_guarded_measurement(data_dir, project_root, home_claude)

    assert not (home_claude / "should-not-exist.jsonl").exists()
