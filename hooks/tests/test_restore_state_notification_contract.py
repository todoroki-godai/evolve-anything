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
# #503 §3.0/§4/§5: decision_text は digest 化・予算・overflow の対象外。
# I1（全体等値）/ I2（segment 等値）/ I3（prefix 一意）を守る。
# ─────────────────────────────────────────────────────────────────
class TestDecisionTextPriority:
    # --- 陽性対照（緑のままであるべき。陰性試験と混ぜて数えない） ---

    def test_positive_no_decision_text_items_result_byte_identical_to_legacy(self):
        """陽性対照1: decision_text を持たない item だけの結合結果は現行と一字も変わらない
        （§503 実装前の golden をそのまま literal で再現）。"""
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
        assert text == (
            "[evolve-anything] 記録待ち提案1件（evolve --drain） / "
            "judge持ち越し10311件（自動） / evolve待ち1PJ / icebox58件・最古31日"
            " → /evolve-anything:queue で開始"
        )

    def test_positive_label_change_does_not_affect_merged_body(self):
        """陽性対照2: item の label だけを変えても結合結果の本文は変わらない（意味を
        変えない書き換えで誤検出しない）。"""
        a = NotificationItem(label="datadir", tier=1, text="A full", digest="A digest")
        b = NotificationItem(label="utterance", tier=1, text="B full", digest="B digest")
        text_original = restore_state._merge_notification_text([a, b])

        a2 = NotificationItem(label="renamed-a", tier=1, text="A full", digest="A digest")
        b2 = NotificationItem(label="renamed-b", tier=1, text="B full", digest="B digest")
        text_renamed = restore_state._merge_notification_text([a2, b2])
        assert text_original == text_renamed

    def test_positive_single_item_full_text_unaffected_by_decision_text_field(self):
        """陽性対照3（§5 既存陽性対照の再確認）: 発火1件のときは decision_text の有無に
        関係なく text がそのまま返る。"""
        item = NotificationItem(
            label="proposal", tier=2, text="フル文だよ", digest="改善案1件",
            decision_text="フル文だよ",
        )
        assert restore_state._merge_notification_text([item]) == "フル文だよ"

    # --- 陰性試験（設計 §5 指定7件。各件「壊す不変条件」「通したい検査経路」を明記） ---

    def test_negative_1_missing_decision_text_key_reverts_to_digest(self):
        """N1（要素を消す）: decision_text が渡らない（collectors.py がキーを落とした状態を
        model 層で再現）と、結合結果は改善案の digest に戻る。壊す不変条件=I1／経路=本テスト。
        本文長に依存せず赤くなることを、長い本文でも確認する。"""
        tier1 = NotificationItem(label="drain", tier=1, text="T1full", digest="T1digest")
        proposal = NotificationItem(
            label="proposal", tier=2, text="長い本文" * 100, digest="改善案2件",
            decision_text=None,
        )
        text = restore_state._merge_notification_text([tier1, proposal])
        assert text == "[evolve-anything] T1digest / 改善案2件"
        assert "長い本文" not in text

    def test_negative_2_decision_text_excluded_from_tier2_budget(self):
        """N2（意味を壊す・予算）: decision item は Tier2 予算計算に含まれない。Tier1 だけで
        400字を超える fixture でも、decision item の digest は overflow に落ちず、
        decision_text は全文がそのまま付く。壊す不変条件=I1（予算非依存）／経路=本テスト。"""
        tier1 = NotificationItem(label="drain", tier=1, text="t1", digest="D" * 410)
        proposal = NotificationItem(
            label="proposal", tier=2, text="t", digest="改善案1件", decision_text="本文",
        )
        text = restore_state._merge_notification_text([tier1, proposal])
        assert text == "[evolve-anything] " + ("D" * 410) + " 本文"
        assert "（ほか:" not in text
        assert "改善案1件" not in text

    def test_negative_3_decision_text_not_truncated(self):
        """N3（意味を壊す・切り詰め）: decision_text は文字数に関係なく完全な形で結合される
        （`[:80] + "…"` のような部分切り詰めをしない）。壊す不変条件=I2／経路=本テスト。"""
        long_text = "あ" * 150
        tier1 = NotificationItem(label="drain", tier=1, text="t1", digest="D")
        proposal = NotificationItem(
            label="proposal", tier=2, text="t", digest="改善案1件", decision_text=long_text,
        )
        text = restore_state._merge_notification_text([tier1, proposal])
        assert text == f"[evolve-anything] D {long_text}"
        assert text.endswith(long_text)
        assert "…" not in text

    def test_negative_4_decision_text_excluded_from_digest_join_entirely(self):
        """N4（配線を外す）: decision item は「digest で結合する」経路（従来の tier1/tier2
        classification）から完全に除外される。digest 分岐へ戻す実装だと `改善案1件` が
        本文中に混入する。壊す不変条件=I1／経路=本テスト。"""
        tier1 = NotificationItem(label="drain", tier=1, text="t1", digest="Ddigest")
        proposal = NotificationItem(
            label="proposal", tier=2, text="t", digest="改善案1件", decision_text="本文です",
        )
        text = restore_state._merge_notification_text([tier1, proposal])
        assert text == "[evolve-anything] Ddigest 本文です"
        assert "改善案1件" not in text

    def test_negative_5_multiple_decision_items_all_included_in_fire_order(self):
        """N5（意味を壊す・複数件）: decision item を2件以上持つとき、先頭の1件だけでなく
        全件が発火順で結合される。壊す不変条件=I1／経路=本テスト。"""
        tier1 = NotificationItem(label="drain", tier=1, text="t1", digest="Ddigest")
        d1 = NotificationItem(label="p1", tier=2, text="t", digest="改善案1件", decision_text="本文A")
        d2 = NotificationItem(label="p2", tier=2, text="t", digest="改善案1件", decision_text="本文B")
        text = restore_state._merge_notification_text([tier1, d1, d2])
        assert text == "[evolve-anything] Ddigest 本文A 本文B"

    def test_negative_6_prefix_removed_end_to_end(self, monkeypatch):
        """N6（prefix 除去を外す）: collectors.py の removeprefix が外れて decision_text が
        自分で "[evolve-anything] " を保持したまま返ってくると、最終文字列に prefix が
        2回現れる。壊す不変条件=I3（および I1）／経路=本テスト。"""
        tier1 = NotificationItem(label="drain", tier=1, text="t1", digest="Ddigest")
        proposal_with_embedded_prefix = NotificationItem(
            label="proposal", tier=2, text="t", digest="改善案1件",
            decision_text="[evolve-anything] 改善案があります: 「rep1」。",
        )
        text = restore_state._merge_notification_text([tier1, proposal_with_embedded_prefix])
        assert text.count("[evolve-anything] ") == 2  # removeprefix が効いていれば1回のはず

    def test_negative_7_extra_wording_mixed_into_decision_text(self):
        """N7（混入）: collectors.py が `decision_text = "【要確認】" + body` のような文言を
        混入させると、機械可読な等値比較（I2）でしか検出できない。壊す不変条件=I2／
        経路=本テスト（包含検査 `"本文" in text` だけなら緑のまま通ってしまう例も併記）。"""
        tier1 = NotificationItem(label="drain", tier=1, text="t1", digest="Ddigest")
        body = "本文です"
        contaminated = NotificationItem(
            label="proposal", tier=2, text="t", digest="改善案1件",
            decision_text=f"【要確認】{body}",
        )
        text = restore_state._merge_notification_text([tier1, contaminated])
        # 包含検査だけなら "混入あり" でも通ってしまう例（対照として明示）
        assert body in text
        # I2 相当（segment 等値）で検出する: 実装が意図した decision segment はこの exact
        # 値のはず。混入があるとこの等値は成立しない。
        assert text != f"[evolve-anything] Ddigest {body}"
        assert text == f"[evolve-anything] Ddigest 【要確認】{body}"

    # --- 追加の陰性試験（自分で選ぶ・上記7件と種類が異なる2件以上） ---

    def test_extra_1_unicode_and_newline_preserved_exactly(self):
        """追加1（表現差: Unicode・改行）: decision_text に絵文字・サロゲートペア外文字・
        改行が含まれても、正規化や除去をせず境界込みで完全一致する。壊す不変条件=I2／
        経路=本テスト。想定 mutation: `decision_texts` を `.replace("\\n", " ")` する実装。"""
        tricky = "改行あり\n行2・絵文字🎉・特殊文字「」『』"
        tier1 = NotificationItem(label="drain", tier=1, text="t1", digest="Ddigest")
        proposal = NotificationItem(
            label="proposal", tier=2, text="t", digest="改善案1件", decision_text=tricky,
        )
        text = restore_state._merge_notification_text([tier1, proposal])
        assert text == f"[evolve-anything] Ddigest {tricky}"

    def test_extra_2_huge_decision_text_not_capped_by_tier2_budget_constant(self):
        """追加2（境界値: 巨大入力）: decision_text が TIER2_BUDGET_CHARS（400字）を大幅に
        超えても切り詰められない。壊す不変条件=I1・I2／経路=本テスト。想定 mutation:
        `decision_texts` の各要素を `t[:TIER2_BUDGET_CHARS]` する実装（N3 の固定 80 字切り詰め
        とは別の、予算定数に連動した切り詰めミス）。"""
        huge = "巨" * 5000
        tier1 = NotificationItem(label="drain", tier=1, text="t1", digest="Ddigest")
        proposal = NotificationItem(
            label="proposal", tier=2, text="t", digest="改善案1件", decision_text=huge,
        )
        text = restore_state._merge_notification_text([tier1, proposal])
        assert text == f"[evolve-anything] Ddigest {huge}"
        assert len(huge) == 5000  # fixture 自体の前提確認

    def test_extra_3_empty_string_decision_text_falls_back_to_digest_no_trailing_space(self):
        """追加3（境界値: 空文字列）: decision_text が "" のとき（None ではない）は判断材料
        無しとみなし digest item として扱う。末尾に余計な空白を残さない。壊す不変条件=I1／
        経路=本テスト。想定 mutation: `it.decision_text is not None` で判定する実装（空文字列を
        誤って decision item 扱いし、末尾に trailing space が残る）。"""
        tier1 = NotificationItem(label="drain", tier=1, text="t1", digest="Ddigest")
        proposal = NotificationItem(
            label="proposal", tier=2, text="t", digest="改善案1件", decision_text="",
        )
        text = restore_state._merge_notification_text([tier1, proposal])
        assert text == "[evolve-anything] Ddigest / 改善案1件"
        assert not text.endswith(" ")


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


# ─────────────────────────────────────────────────────────────────
# #503: restore_state.py の NotificationItem 構築（decision_text 配線）を
# collectors.py の実出力に近い形で通す E2E（2件以上発火で digest 化される経路）
# ─────────────────────────────────────────────────────────────────
def test_e2e_decision_text_survives_multi_system_merge_with_prefix_once(tmp_path, monkeypatch, capsys):
    """§3.1-3 の配線（restore_state.py:213-219）を通した E2E。session_proposal の本文
    （collectors.py の decision_text 形式を模した dict）が、他系統と同時発火する digest 化
    経路でも一字も削られず・prefix 重複せずに最終 systemMessage に現れる。"""
    monkeypatch.setattr(restore_state, "_build_pending_trigger_output", lambda stack: None)
    monkeypatch.setattr(restore_state, "_build_spec_drift_output", lambda: None)
    monkeypatch.setattr(
        restore_state, "_build_evolve_drain_output",
        lambda: NotificationItem(label="drain", tier=1, text="適用済み提案", digest="記録待ち1件"),
    )
    monkeypatch.setattr(restore_state, "_build_data_dir_migration_output", lambda: None)
    monkeypatch.setattr(restore_state, "_build_utterance_staleness_output", lambda: None)
    monkeypatch.setattr(restore_state, "_build_evolve_queue_output", lambda *a, **k: None)
    monkeypatch.setattr(
        restore_state, "_build_session_proposal_output",
        lambda *a, **k: {
            "systemMessage": "[evolve-anything] 改善案があります: 「rep1」。応答のあとで採否をお聞きします。",
            "digest": "改善案1件",
            "decision_text": "改善案があります: 「rep1」。応答のあとで採否をお聞きします。",
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "AskUserQuestion で確認してください",
            },
        },
    )
    monkeypatch.setattr(restore_state, "_build_judge_cap_output", lambda *a, **k: None)
    monkeypatch.setattr(restore_state, "_build_icebox_output", lambda stack: None)
    monkeypatch.setattr("common.find_latest_checkpoint", lambda _: None)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    restore_state.handle_session_start({})

    out = capsys.readouterr().out
    payload = json.loads(out.strip().splitlines()[0])
    msg = payload["systemMessage"]
    assert "改善案があります: 「rep1」。応答のあとで採否をお聞きします。" in msg
    assert "記録待ち1件" in msg  # 他系統は digest のまま同居
    assert msg.count("[evolve-anything] ") == 1  # I3
