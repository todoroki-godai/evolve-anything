"""ADR-054 Phase 0（B1）: SessionStart 通知の1行化・merge/digest/Tier 契約テスト。

設計: docs/decisions/drafts/054-phase0-notification-routing.md §4/§6/§7.1

- 契約テスト（§7.1-1）: stdout は「0行」か「厳密に1行の JSON dict」の二値
- Tier1 は Tier2 の truncate に巻き込まれない（§7.1-3）
- spec_drift の two-phase 化（§7.1-4）
- producer 破損判定（§7.1-6・evolve-queue.json）
- judge_cap 全分岐 Tier1（§7.1-7・単体は test_restore_state_judge_cap_notice.py も参照）
- stdout/stderr の切り分け（§7.1-9）
- digest/full 切り替え（§7.1-10）
- work_context 圧縮は test_hooks_session.py 側で担当
- digest 行の末尾導線（§7.1-12）
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_HOOKS.parent / "scripts" / "lib"))

import data_dir_migration as ddm  # noqa: E402
import restore_state  # noqa: E402
import spec_trigger  # noqa: E402
from restore_state import NotificationItem  # noqa: E402


def _fresh_generated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────
# _merge_notification_text 単体（合成 NotificationItem・env 非依存）
# ─────────────────────────────────────────────────────────────────
class TestMergeNotificationText:
    def test_empty_returns_none(self):
        assert restore_state._merge_notification_text([]) is None

    def test_single_item_uses_full_text(self):
        item = NotificationItem(label="drain", tier=1, text="フル文だよ", digest="短縮")
        assert restore_state._merge_notification_text([item]) == "フル文だよ"

    def test_single_item_no_tail_link_even_if_flagged(self):
        """§7.1-12: 発火系統が0〜1件のときは末尾導線を付けない（digest 行専用）。"""
        item = NotificationItem(
            label="drain", tier=1, text="フル文", digest="短縮", tail_link=True,
        )
        assert "→" not in restore_state._merge_notification_text([item])

    def test_two_items_use_digest_and_prefix(self):
        a = NotificationItem(label="datadir", tier=1, text="A full", digest="A digest")
        b = NotificationItem(label="utterance", tier=1, text="B full", digest="B digest")
        text = restore_state._merge_notification_text([a, b])
        assert text == "[evolve-anything] A digest / B digest"

    def test_tier1_never_truncated_even_with_many_tier2(self):
        """§7.1-3: Tier1 は上限に関係なく必ず全量載る。"""
        tier1 = NotificationItem(label="drain", tier=1, text="T1 full", digest="T1digest")
        tier2_items = [
            NotificationItem(label=f"t2-{i}", tier=2, text=f"full{i}", digest="x" * 50)
            for i in range(20)
        ]
        text = restore_state._merge_notification_text([tier1, *tier2_items])
        assert "T1digest" in text

    def test_tier2_overflow_uses_label_suffix_not_count(self):
        """§4.4 ルール3: 超過分は「（ほか: 系統名）」。件数のみの「ほかN件」は禁止。"""
        tier1 = NotificationItem(label="drain", tier=1, text="T1", digest="T" * 390)
        tier2_a = NotificationItem(label="queue", tier=2, text="qfull", digest="qdigest")
        tier2_b = NotificationItem(label="judge", tier=2, text="jfull", digest="jdigest")
        text = restore_state._merge_notification_text([tier1, tier2_a, tier2_b])
        assert "qdigest" in text  # ちょうど収まる分は含まれる
        assert "jdigest" not in text  # 予算超過分は含まれない
        assert "（ほか: judge）" in text
        assert "ほか2件" not in text  # 件数のみは禁止

    def test_tier2_overflow_is_all_or_nothing_per_digest(self):
        """§4.4 ルール4: 切り詰めは digest 単位。文字列途中で切らない。"""
        tier1 = NotificationItem(label="drain", tier=1, text="T1", digest="T" * 395)
        tier2 = NotificationItem(label="queue", tier=2, text="qfull", digest="qdigest12345")
        text = restore_state._merge_notification_text([tier1, tier2])
        assert "qdigest12345" not in text  # 部分文字列で紛れ込んでいないこと
        assert "qdig" not in text  # 途中切断されていないこと

    def test_tail_link_appended_when_any_item_flagged(self):
        a = NotificationItem(label="queue", tier=2, text="A", digest="Adigest", tail_link=True)
        b = NotificationItem(label="judge", tier=1, text="B", digest="Bdigest", tail_link=False)
        text = restore_state._merge_notification_text([a, b])
        assert text.endswith("→ /evolve-anything:queue で開始")

    def test_tail_link_not_appended_when_no_item_flagged(self):
        a = NotificationItem(label="datadir", tier=1, text="A", digest="Adigest", tail_link=False)
        b = NotificationItem(label="utterance", tier=1, text="B", digest="Bdigest", tail_link=False)
        text = restore_state._merge_notification_text([a, b])
        assert "→" not in text

    def test_pending_trigger_and_icebox_lane1_both_stay_full_when_mixed(self):
        """codex round2 [Must-new] の直接的な回帰防止: pending_trigger・icebox レーン1は
        混在時も digest 化されない（pending_trigger は digest==text、icebox は
        独自短縮フレームだが body は不変）。"""
        trigger_text = "[evolve-anything:auto-trigger] 破壊的読み取り済みの本文"
        trigger = NotificationItem(
            label="trigger", tier=1, text=trigger_text, digest=trigger_text,
        )
        icebox_text = "[evolve-anything] icebox 再開条件が成立しました: #205（reason）"
        icebox_digest = "icebox成立: #205（reason）"
        icebox = NotificationItem(label="icebox", tier=1, text=icebox_text, digest=icebox_digest)
        text = restore_state._merge_notification_text([trigger, icebox])
        assert trigger_text in text  # pending_trigger は完全不変
        assert "#205（reason）" in text  # icebox body は不変

    def test_today_realistic_four_system_fixture_stays_short(self):
        """tacchi 実測（今朝4系統・digest化後79字目安）を fixture 化した回帰テスト。
        rev7 確定文言で結合しても十分に短い（目安200字未満）ことを確認する。"""
        drain = NotificationItem(
            label="drain", tier=1, text="適用済みの evolve 提案が 1 件あります。",
            digest="記録待ち提案1件（evolve --drain）", tail_link=True,
        )
        queue = NotificationItem(
            label="queue", tier=2, text="evolve 待ち: figma-to-code（1 件）",
            digest="evolve待ち1PJ", tail_link=True,
        )
        judge = NotificationItem(
            label="judge", tier=1, text="llm_judge 日次上限に到達",
            digest="judge持ち越し10311件（自動）",
        )
        icebox = NotificationItem(
            label="icebox", tier=2, text="icebox 58件・最古31日",
            digest="icebox58件・最古31日", tail_link=True,
        )
        text = restore_state._merge_notification_text([drain, queue, judge, icebox])
        assert len(text) < 200


# ─────────────────────────────────────────────────────────────────
# spec_drift の two-phase 化（§5.2/§7.1-4）
# ─────────────────────────────────────────────────────────────────
def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, subject: str, files: dict) -> str:
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        _git(repo, "add", rel)
    _git(repo, "commit", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def spec_repo(tmp_path: Path, monkeypatch) -> Path:
    r = tmp_path / "myproj"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    _git(r, "checkout", "-q", "-b", "main")
    _commit(r, "chore: init", {"README.md": "init"})
    monkeypatch.setattr(spec_trigger, "_DATA_DIR_OVERRIDE", tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(r))
    return r


class TestSpecDriftTwoPhase:
    def test_build_returns_none_before_first_run_marker_set(self, spec_repo, capsys):
        """初回セットアップ分岐: message は無いが、表示に紐づかない副作用のみの分岐なので
        restore_state 側が明示的に save_marker() を呼び即時保存する（spec_trigger 自体は
        persist=False で書き込みゼロ・dry-run 純度契約）。"""
        item = restore_state._build_spec_drift_output()
        assert item is None
        slug = spec_trigger.resolve_slug(spec_repo)
        assert (spec_trigger.MARKER_ROOT / f"{slug}.json").exists()

    def test_build_fires_with_digest_and_commit(self, spec_repo, capsys):
        restore_state._build_spec_drift_output()  # 初回マーカー
        _commit(spec_repo, "feat(remediation): 挙動変更", {"scripts/lib/remediation.py": "v2"})
        item = restore_state._build_spec_drift_output()
        assert item is not None
        assert item.tier == 2
        assert "remediation" in item.text
        assert item.digest == "spec-keeper提案1件"
        assert item.commit is not None

    def test_commit_not_called_means_marker_not_saved(self, spec_repo, capsys):
        """defer 契約: commit を呼ばない限り marker は更新されない
        （表示できなければ次回同じ内容が再現する）。"""
        restore_state._build_spec_drift_output()  # 初回マーカー
        slug = spec_trigger.resolve_slug(spec_repo)
        marker_file = spec_trigger.MARKER_ROOT / f"{slug}.json"
        before = marker_file.read_text()

        _commit(spec_repo, "feat(remediation): 挙動変更", {"scripts/lib/remediation.py": "v2"})
        item = restore_state._build_spec_drift_output()
        assert item is not None
        assert marker_file.read_text() == before  # commit しない限り不変

        item.commit()
        assert marker_file.read_text() != before  # commit すると保存される

    def test_next_call_reproduces_same_surfaced_when_not_committed(self, spec_repo, capsys):
        """defer した（commit しなかった）場合、次回呼び出しでも同じ surfaced が再現する。"""
        restore_state._build_spec_drift_output()  # 初回マーカー
        _commit(spec_repo, "feat(remediation): 挙動変更", {"scripts/lib/remediation.py": "v2"})
        item1 = restore_state._build_spec_drift_output()  # commit しない
        item2 = restore_state._build_spec_drift_output()  # 再度呼んでも同じ内容
        assert item1.text == item2.text


# ─────────────────────────────────────────────────────────────────
# evolve-queue.json 破損（§4.6/§5.4/§7.1-6）
# ─────────────────────────────────────────────────────────────────
def _install_env(tmp_path, monkeypatch):
    source = tmp_path / "plugins" / "data" / "evolve-anything-evolve-anything"
    source.mkdir(parents=True)
    monkeypatch.setattr(ddm, "is_cc_install_layout", lambda p: Path(p) == source)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(source))
    return source


class TestEvolveQueueCorruption:
    def test_corrupt_file_fires_tier1_health_notice(self, tmp_path, monkeypatch, capsys):
        source = _install_env(tmp_path, monkeypatch)
        (source / "evolve-queue.json").write_text("{not valid json", encoding="utf-8")
        item = restore_state._build_evolve_queue_output()
        assert item is not None
        assert item.tier == 1
        assert item.digest == "evolve-queue破損"
        assert capsys.readouterr().out == ""  # 収集関数は印字しない

    def test_absent_file_is_silent_not_corrupt(self, tmp_path, monkeypatch):
        _install_env(tmp_path, monkeypatch)
        assert restore_state._build_evolve_queue_output() is None

    def test_judge_cap_stays_silent_on_corrupt_queue(self, tmp_path, monkeypatch):
        """corrupt 判定は evolve_queue_notice の Tier1 に昇格するだけで、他2系統
        （session_proposal/judge_cap）は queue_data=None のまま黙って沈黙する
        （§4.6 適用範囲は evolve_queue_notice の収集関数内に限定）。"""
        source = _install_env(tmp_path, monkeypatch)
        (source / "evolve-queue.json").write_text("{not valid json", encoding="utf-8")
        assert restore_state._build_judge_cap_output() is None


# ─────────────────────────────────────────────────────────────────
# stdout/stderr の切り分け（§4.3/§7.1-9）
# ─────────────────────────────────────────────────────────────────
def test_partial_builder_failure_keeps_others_in_single_stdout_line(tmp_path, monkeypatch, capsys):
    """1系統が内部例外を出しても、他系統の内容は stdout 1行に残り、stderr にエラーが出る。"""
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

    def _ok():
        return NotificationItem(label="drain", tier=1, text="生きてる系統", digest="digest")

    def _boom():
        raise RuntimeError("evolve-drain boom")

    monkeypatch.setattr(restore_state, "_build_pending_trigger_output", lambda stack: None)
    monkeypatch.setattr(restore_state, "_build_spec_drift_output", _boom)
    monkeypatch.setattr(restore_state, "_build_evolve_drain_output", _ok)
    monkeypatch.setattr(restore_state, "_build_data_dir_migration_output", lambda: None)
    monkeypatch.setattr(restore_state, "_build_utterance_staleness_output", lambda: None)
    monkeypatch.setattr(restore_state, "_build_evolve_queue_output", lambda *a, **k: None)
    monkeypatch.setattr(restore_state, "_build_session_proposal_output", lambda *a, **k: None)
    monkeypatch.setattr(restore_state, "_build_judge_cap_output", lambda *a, **k: None)
    monkeypatch.setattr(restore_state, "_build_icebox_output", lambda stack: None)

    restore_state.handle_session_start({})

    captured = capsys.readouterr()
    lines = captured.out.strip().splitlines()
    assert len(lines) == 1
    assert "生きてる系統" in lines[0]


# ─────────────────────────────────────────────────────────────────
# 契約テスト（§7.1-1）: stdout 非空なら splitlines() が厳密に1・json.loads 可・
# 期待キーが同一 dict に共存
# ─────────────────────────────────────────────────────────────────
def test_contract_single_json_dict_with_all_expected_keys(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(restore_state, "_build_pending_trigger_output", lambda stack: None)
    monkeypatch.setattr(restore_state, "_build_spec_drift_output", lambda: None)
    monkeypatch.setattr(restore_state, "_build_evolve_drain_output", lambda: None)
    monkeypatch.setattr(restore_state, "_build_data_dir_migration_output", lambda: None)
    monkeypatch.setattr(restore_state, "_build_utterance_staleness_output", lambda: None)
    monkeypatch.setattr(restore_state, "_build_evolve_queue_output", lambda *a, **k: None)
    monkeypatch.setattr(
        restore_state, "_build_session_proposal_output",
        lambda *a, **k: {
            "systemMessage": "改善案",
            "digest": "改善案1件",
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "AskUserQuestion で確認してください",
            },
        },
    )
    monkeypatch.setattr(restore_state, "_build_judge_cap_output", lambda *a, **k: None)
    monkeypatch.setattr(restore_state, "_build_icebox_output", lambda stack: None)
    monkeypatch.setattr(
        "common.find_latest_checkpoint",
        lambda _: {"work_context": {"git_branch": "main"}},
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    restore_state.handle_session_start({})

    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert "systemMessage" in payload
    assert "hookSpecificOutput" in payload
    assert payload["hookSpecificOutput"]["additionalContext"]
    assert payload["hookSpecificOutput"]["sessionTitle"]
    assert payload["restored"] is True
    assert "checkpoint" in payload
