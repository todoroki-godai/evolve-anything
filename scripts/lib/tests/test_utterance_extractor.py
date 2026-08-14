"""utterance_archive.extractor のテスト（#430）。

決定論・LLM 非依存。transcript jsonl 行（dict）から human 発話のみを抽出し、
harness 注入・tool_result・長文ペースト・非対話 PJ を design doc どおりに分類する。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from utterance_archive import extractor  # noqa: E402
from utterance_archive.extractor import (  # noqa: E402
    EXCLUDED_PJ_SLUGS,
    LONG_PASTE_THRESHOLD,
    extract_utterances,
    pj_slug_from_cwd,
    pj_slug_from_dir_name,
)


def _user_line(text, ts="2026-06-01T00:00:00Z", sid="s1", uuid="u1", cwd=None):
    obj = {
        "type": "user",
        "uuid": uuid,
        "sessionId": sid,
        "timestamp": ts,
        "message": {"role": "user", "content": text},
    }
    if cwd is not None:
        obj["cwd"] = cwd
    return json.dumps(obj)


def _assistant_tooluse_line(names, ts="2026-06-01T00:00:00Z", sid="s1", uuid="a1"):
    blocks = [{"type": "tool_use", "name": n, "id": f"t{i}"} for i, n in enumerate(names)]
    return json.dumps(
        {
            "type": "assistant",
            "uuid": uuid,
            "sessionId": sid,
            "timestamp": ts,
            "message": {"role": "assistant", "content": blocks},
        }
    )


def _tool_result_line(ts="2026-06-01T00:00:00Z", sid="s1", uuid="tr1"):
    """assistant の tool_use に直後で続く tool_result の user 行（発話でない）。

    実 transcript では tool_use の直後に必ずこの形の行が来る。#379 root cause 再現に必要。
    """
    content = [{"type": "tool_result", "tool_use_id": "t1", "content": "結果"}]
    return json.dumps(
        {
            "type": "user",
            "uuid": uuid,
            "sessionId": sid,
            "timestamp": ts,
            "toolUseResult": {"stdout": "..."},
            "message": {"role": "user", "content": content},
        }
    )


def _sidechain_user_line(text, ts="2026-06-01T00:00:00Z", sid="s1", uuid="su1"):
    return json.dumps(
        {
            "type": "user",
            "uuid": uuid,
            "sessionId": sid,
            "timestamp": ts,
            "isSidechain": True,
            "message": {"role": "user", "content": text},
        }
    )


def _sidechain_assistant_tooluse_line(names, ts="2026-06-01T00:00:00Z", sid="s1", uuid="sa1"):
    blocks = [{"type": "tool_use", "name": n, "id": f"st{i}"} for i, n in enumerate(names)]
    return json.dumps(
        {
            "type": "assistant",
            "uuid": uuid,
            "sessionId": sid,
            "timestamp": ts,
            "isSidechain": True,
            "message": {"role": "assistant", "content": blocks},
        }
    )


# --- pj_slug derivation: cwd 由来（encoded dir 名のデコードは諦める）-----------

def test_pj_slug_from_cwd_main_repo() -> None:
    """本体 repo の cwd → basename がそのまま slug（ハイフン入り名も保持）。"""
    cwd = "/Users/todoroki/tools/evolve-anything"
    assert pj_slug_from_cwd(cwd) == "evolve-anything"


def test_pj_slug_from_cwd_worktree_normalizes_to_main() -> None:
    """worktree の cwd → .claude/worktrees/ で切って本体 slug に帰属。"""
    cwd = "/Users/todoroki/tools/evolve-anything/.claude/worktrees/agent-many"
    assert pj_slug_from_cwd(cwd) == "evolve-anything"


def test_pj_slug_from_cwd_hyphenated_name() -> None:
    """ハイフン入り PJ 名（ai-daily-report）が truncate されない。"""
    cwd = "/Users/todoroki/ai-daily-report"
    assert pj_slug_from_cwd(cwd) == "ai-daily-report"


def test_pj_slug_from_cwd_missing_returns_none() -> None:
    """cwd 欠損（None / 空）は None（呼び出し側が encoded dir 名へ fallback）。"""
    assert pj_slug_from_cwd(None) is None
    assert pj_slug_from_cwd("") is None


def test_pj_slug_from_dir_name_fallback() -> None:
    """cwd が無いファイル用の fallback: encoded dir 名をそのまま使う。"""
    name = "-Users-todoroki-tools-evolve-anything"
    assert pj_slug_from_dir_name(name) == name


def test_extractor_uses_cwd_when_present(tmp_path: Path) -> None:
    """transcript に cwd があれば pj_slug 引数より cwd 由来を優先する。"""
    f = tmp_path / "s1.jsonl"
    cwd = "/Users/x/ai-daily-report"
    f.write_text(_user_line("発話", cwd=cwd) + "\n", encoding="utf-8")
    # fallback_slug は encoded dir 名だが cwd があるのでそちらが勝つ
    utts = list(extract_utterances(f, pj_slug="-Users-x-ai-daily-report"))
    assert len(utts) == 1
    assert utts[0].pj_slug == "ai-daily-report"


def test_extractor_falls_back_when_no_cwd(tmp_path: Path) -> None:
    """cwd が無いファイルは pj_slug 引数（encoded dir 名）をそのまま使う。"""
    f = tmp_path / "s1.jsonl"
    f.write_text(_user_line("発話") + "\n", encoding="utf-8")
    utts = list(extract_utterances(f, pj_slug="-Users-x-some-pj"))
    assert len(utts) == 1
    assert utts[0].pj_slug == "-Users-x-some-pj"


def test_bots_is_in_excluded_pj_slugs() -> None:
    """非対話 PJ の初期値に bots を含む（文字起こしノイズ実測）。"""
    assert "bots" in EXCLUDED_PJ_SLUGS


def test_excluded_pj_via_cwd_slug(tmp_path: Path) -> None:
    """cwd 由来 slug が bots なら excluded_pj タグ。"""
    f = tmp_path / "s1.jsonl"
    f.write_text(_user_line("発話", cwd="/Users/x/tools/bots") + "\n", encoding="utf-8")
    utts = list(extract_utterances(f, pj_slug="-Users-x-bots"))
    assert len(utts) == 1
    assert utts[0].pj_slug == "bots"
    assert utts[0].source_kind == "excluded_pj"


# --- basic human utterance extraction ---------------------------------------

def test_extracts_plain_human_string(tmp_path: Path) -> None:
    f = tmp_path / "s1.jsonl"
    f.write_text(_user_line("これは普通の人間の発話です") + "\n", encoding="utf-8")
    utts = list(extract_utterances(f, pj_slug="evolve-anything"))
    assert len(utts) == 1
    u = utts[0]
    assert u.text == "これは普通の人間の発話です"
    assert u.source_kind == "dialogue"
    assert u.session_id == "s1"
    assert u.line_no == 1
    assert u.text_hash  # non-empty hash
    assert u.pj_slug == "evolve-anything"


def test_extracts_human_from_content_list(tmp_path: Path) -> None:
    """content が block list の場合 text block を結合して抽出する。"""
    content = [{"type": "text", "text": "リストブロックの発話"}]
    f = tmp_path / "s1.jsonl"
    f.write_text(_user_line(content) + "\n", encoding="utf-8")
    utts = list(extract_utterances(f, pj_slug="x"))
    assert len(utts) == 1
    assert utts[0].text == "リストブロックの発話"


# --- exclusion rules ---------------------------------------------------------

def test_excludes_tool_result_blocks(tmp_path: Path) -> None:
    """tool_result content の user 行は発話でない。"""
    content = [{"type": "tool_result", "tool_use_id": "t1", "content": "結果"}]
    line = json.dumps({
        "type": "user", "uuid": "u1", "sessionId": "s1",
        "timestamp": "2026-06-01T00:00:00Z",
        "toolUseResult": {"stdout": "..."},
        "message": {"role": "user", "content": content},
    })
    f = tmp_path / "s1.jsonl"
    f.write_text(line + "\n", encoding="utf-8")
    assert list(extract_utterances(f, pj_slug="x")) == []


def test_excludes_ismeta(tmp_path: Path) -> None:
    line = json.dumps({
        "type": "user", "uuid": "u1", "sessionId": "s1",
        "timestamp": "2026-06-01T00:00:00Z", "isMeta": True,
        "message": {"role": "user", "content": "メタ"},
    })
    f = tmp_path / "s1.jsonl"
    f.write_text(line + "\n", encoding="utf-8")
    assert list(extract_utterances(f, pj_slug="x")) == []


def test_excludes_harness_markers(tmp_path: Path) -> None:
    """harness 注入マーカー6種を含む発話は除外する。"""
    markers = [
        "<system-reminder>foo</system-reminder>",
        "<command-name>/model</command-name>",
        "<local-command-stdout>set</local-command-stdout>",
        "Caveat: The messages below were generated",
        "[Request interrupted by user]",
        "This session is being continued from a previous",
    ]
    lines = [_user_line(m, uuid=f"u{i}") for i, m in enumerate(markers)]
    f = tmp_path / "s1.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert list(extract_utterances(f, pj_slug="x")) == []


def test_excludes_stop_hook_self_injection(tmp_path: Path) -> None:
    """Stop hook 自己注入文は _HARNESS_MARKERS になくても除外される（#323）。

    実測: extractor 固有の _HARNESS_MARKERS には無いが rl_common.detection の
    is_machinery_prompt にはある markers（"Stop hook feedback:" 等）が dialogue として
    utterances.db に漏れ、llm_judge チャネルの判定対象に混入していた（実 DB 26 件）。
    """
    text = (
        "Stop hook feedback:\n"
        "先送り表現を検出しました: 「後で対応」。ルールに従い background subagent を"
        "即座に起動してください。"
    )
    f = tmp_path / "s1.jsonl"
    f.write_text(_user_line(text) + "\n", encoding="utf-8")
    assert list(extract_utterances(f, pj_slug="x")) == []


def test_excludes_skill_base_directory_injection(tmp_path: Path) -> None:
    """SKILL.md 注入の "Base directory for this skill:" も機構ターン（rl_common 単一ソース経由）。"""
    text = "Base directory for this skill: /Users/x/.claude/skills/foo\n\n実行手順..."
    f = tmp_path / "s1.jsonl"
    f.write_text(_user_line(text) + "\n", encoding="utf-8")
    assert list(extract_utterances(f, pj_slug="x")) == []


def test_real_correction_not_excluded_by_shared_machinery_filter(tmp_path: Path) -> None:
    """rl_common 単一ソースの追加判定は本物のユーザー発話を誤って除外しない。"""
    f = tmp_path / "s1.jsonl"
    f.write_text(_user_line("いや、そうじゃなくて、そっちのアプローチにして") + "\n", encoding="utf-8")
    utts = list(extract_utterances(f, pj_slug="x"))
    assert len(utts) == 1


# --- #445: 画像添付のプレースホルダ "[Image #N]" は noise なので strip する -----------
#
# 実コーパス実測（corrections.jsonl 37件全件）: `[Image #N]` は Claude Code CLI が画像
# 添付時に text block へ自動挿入する位置マーカーで、bare（画像だけ・実テキスト無し）の
# ケースは 0 件、全件が同じ text block 内に人間の実指摘が続く（例:
# "[Image #1] Codeタブってないよ"）。マーカーだけを除去し人間の実テキストは残す。


def test_strips_leading_image_placeholder_same_line() -> None:
    content = "[Image #1] Codeタブってないよ"
    assert extractor._strip_image_placeholders(content) == "Codeタブってないよ"


def test_strips_leading_image_placeholder_newline_separated() -> None:
    content = "[Image #1]\n\nこんな感じの議論がされている"
    assert extractor._strip_image_placeholders(content) == "こんな感じの議論がされている"


def test_strips_multiple_consecutive_image_placeholders() -> None:
    content = "[Image #1] このrepositoryって特定できる？\n\n[Image #2] [Image #3] [Image #4] [Image #5] \n\nこんな感じ"
    out = extractor._strip_image_placeholders(content)
    assert "[Image" not in out
    assert "このrepositoryって特定できる？" in out
    assert "こんな感じ" in out


def test_image_placeholder_only_no_real_text_returns_empty() -> None:
    assert extractor._strip_image_placeholders("[Image #1]") == ""


def test_image_placeholder_stripping_wired_into_extraction(tmp_path: Path) -> None:
    """extract_utterances 経由でも実際に strip される（extractor 全体への配線確認）。"""
    f = tmp_path / "s1.jsonl"
    f.write_text(_user_line("[Image #1] Codeタブってないよ") + "\n", encoding="utf-8")
    utts = list(extract_utterances(f, pj_slug="x"))
    assert len(utts) == 1
    assert utts[0].text == "Codeタブってないよ"


def test_image_placeholder_only_excluded_as_non_utterance(tmp_path: Path) -> None:
    """マーカーだけで実テキストが無い（bare 添付）行は発話として抽出しない。"""
    f = tmp_path / "s1.jsonl"
    f.write_text(_user_line("[Image #1]") + "\n", encoding="utf-8")
    assert list(extract_utterances(f, pj_slug="x")) == []


def test_image_like_literal_text_not_mangled() -> None:
    # "#N]" 形式に一致しない文字列は誤って触らない（過剰マッチ防止）。
    content = "[Image processing failed] というエラーが出た"
    assert extractor._strip_image_placeholders(content) == content


# --- #445: stats カウンタ（strip で救済 vs strip 後ゼロで除外を別々に集計） -----------


def test_stats_counts_stripped_when_real_text_survives(tmp_path: Path) -> None:
    f = tmp_path / "s1.jsonl"
    f.write_text(_user_line("[Image #1] Codeタブってないよ") + "\n", encoding="utf-8")
    stats: dict = {}
    utts = list(extract_utterances(f, pj_slug="x", stats=stats))
    assert len(utts) == 1
    assert stats == {"image_placeholder_stripped": 1}


def test_stats_counts_only_excluded_when_strip_leaves_nothing(tmp_path: Path) -> None:
    f = tmp_path / "s1.jsonl"
    f.write_text(_user_line("[Image #1]") + "\n", encoding="utf-8")
    stats: dict = {}
    utts = list(extract_utterances(f, pj_slug="x", stats=stats))
    assert utts == []
    assert stats == {"image_placeholder_only_excluded": 1}


def test_stats_untouched_when_no_placeholder_present(tmp_path: Path) -> None:
    f = tmp_path / "s1.jsonl"
    f.write_text(_user_line("普通の発話") + "\n", encoding="utf-8")
    stats: dict = {}
    list(extract_utterances(f, pj_slug="x", stats=stats))
    assert stats == {}


def test_stats_not_double_counted_on_incremental_rescan(tmp_path: Path) -> None:
    """#445 codex round1 [Must]3: 増分 ingest は offset 以前の行も prev_action 文脈復元の
    ために再走査するが、stats はそのたびに再計上してはいけない（複数 PJ 合算でも誤加算が
    積み上がるため）。
    """
    f = tmp_path / "s1.jsonl"
    f.write_text(
        _user_line("[Image #1] 最初の指摘", uuid="u1") + "\n"
        + _user_line("[Image #2] 二番目の指摘", uuid="u2") + "\n",
        encoding="utf-8",
    )
    # 初回 ingest: 全行走査（start_line=0）。
    stats1: dict = {}
    utts1 = list(extract_utterances(f, pj_slug="x", stats=stats1))
    assert len(utts1) == 2
    assert stats1 == {"image_placeholder_stripped": 2}

    # 増分 ingest: 1行目（idx=0）は既処理として再走査だけされる。再計上しないこと。
    stats2: dict = {}
    utts2 = list(extract_utterances(f, pj_slug="x", start_line=1, stats=stats2))
    assert len(utts2) == 1  # 2行目のみ yield
    assert stats2 == {"image_placeholder_stripped": 1}


def test_stats_bare_marker_not_double_counted_on_incremental_rescan(tmp_path: Path) -> None:
    """bare marker（strip 後ゼロで除外）経路も同様に post-offset のみ加算する。"""
    f = tmp_path / "s1.jsonl"
    f.write_text(
        _user_line("[Image #1]", uuid="u1") + "\n"
        + _user_line("普通の発話", uuid="u2") + "\n"
        + _user_line("[Image #2]", uuid="u3") + "\n",
        encoding="utf-8",
    )
    stats2: dict = {}
    # 1行目（bare marker・idx=0）は既処理扱い。3行目（idx=2）だけが新規。
    utts2 = list(extract_utterances(f, pj_slug="x", start_line=1, stats=stats2))
    assert len(utts2) == 1  # 2行目のみ発話として yield
    assert stats2 == {"image_placeholder_only_excluded": 1}


def test_stats_none_is_safe_default(tmp_path: Path) -> None:
    # stats を渡さない既存呼び出し元（ingest.py 更新前の互換性）はそのまま動く。
    f = tmp_path / "s1.jsonl"
    f.write_text(_user_line("[Image #1] 実指摘") + "\n", encoding="utf-8")
    utts = list(extract_utterances(f, pj_slug="x"))
    assert len(utts) == 1
    assert utts[0].text == "実指摘"


def test_sidechain_rows_excluded_from_utterances(tmp_path: Path) -> None:
    """isSidechain:true の user 行は human 発話として拾われない（#379 ADR-054 §5-A1）。"""
    f = tmp_path / "s1.jsonl"
    f.write_text(_sidechain_user_line("サブエージェントへの内部プロンプト") + "\n", encoding="utf-8")
    assert list(extract_utterances(f, pj_slug="x")) == []


def test_sidechain_tool_use_does_not_leak_into_prev_action(tmp_path: Path) -> None:
    """sidechain 内 assistant の tool_use は次の main human 発話の prev_action へ
    持ち越されない（#379 ADR-054 §5-A1 完了条件「除外の粒度」）。
    """
    lines = [
        _user_line("main の質問", uuid="u1", ts="2026-06-01T00:00:00Z"),
        _assistant_tooluse_line(["Read"], uuid="a1", ts="2026-06-01T00:00:01Z"),
        _tool_result_line(uuid="tr1", ts="2026-06-01T00:00:02Z"),
        _sidechain_user_line("サブエージェントへの内部プロンプト", uuid="su1", ts="2026-06-01T00:00:03Z"),
        _sidechain_assistant_tooluse_line(["Grep", "Bash"], uuid="sa1", ts="2026-06-01T00:00:04Z"),
        _user_line("main の次の質問", uuid="u2", ts="2026-06-01T00:00:05Z"),
    ]
    f = tmp_path / "s1.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    utts = list(extract_utterances(f, pj_slug="x"))
    # sidechain user 行は発話として拾われない（main 2件のみ）。
    assert [u.text for u in utts] == ["main の質問", "main の次の質問"]
    # sidechain 内の Grep/Bash は次の main 発話の prev_action に混ざらない。
    assert utts[1].prev_action == "Read"


def test_extractor_version_is_4() -> None:
    """#445 で v3→v4（[Image #N] プレースホルダの strip 追加）。"""
    assert extractor.EXTRACTOR_VERSION == 4


def test_skips_assistant_lines(tmp_path: Path) -> None:
    f = tmp_path / "s1.jsonl"
    f.write_text(_assistant_tooluse_line(["Bash"]) + "\n", encoding="utf-8")
    assert list(extract_utterances(f, pj_slug="x")) == []


def test_skips_blank_and_malformed(tmp_path: Path) -> None:
    f = tmp_path / "s1.jsonl"
    f.write_text("\nnot json\n" + _user_line("") + "\n", encoding="utf-8")
    assert list(extract_utterances(f, pj_slug="x")) == []


# --- source_kind classification ---------------------------------------------

def test_long_paste_tagged(tmp_path: Path) -> None:
    big = "あ" * (LONG_PASTE_THRESHOLD + 1)
    f = tmp_path / "s1.jsonl"
    f.write_text(_user_line(big) + "\n", encoding="utf-8")
    utts = list(extract_utterances(f, pj_slug="x"))
    assert len(utts) == 1
    assert utts[0].source_kind == "long_paste"


def test_excluded_pj_tagged(tmp_path: Path) -> None:
    f = tmp_path / "s1.jsonl"
    f.write_text(_user_line("ふつうの発話") + "\n", encoding="utf-8")
    utts = list(extract_utterances(f, pj_slug="bots"))
    assert len(utts) == 1
    assert utts[0].source_kind == "excluded_pj"


# --- prev_action -------------------------------------------------------------

def test_prev_action_joins_tool_names(tmp_path: Path) -> None:
    """直前 human より後の assistant tool_use 名を出現順に join。"""
    lines = [
        _user_line("最初の質問", uuid="u1", ts="2026-06-01T00:00:00Z"),
        _assistant_tooluse_line(["Read"], uuid="a1", ts="2026-06-01T00:00:01Z"),
        _assistant_tooluse_line(["Bash", "Edit"], uuid="a2", ts="2026-06-01T00:00:02Z"),
        _user_line("次の質問", uuid="u2", ts="2026-06-01T00:00:03Z"),
    ]
    f = tmp_path / "s1.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    utts = list(extract_utterances(f, pj_slug="x"))
    assert len(utts) == 2
    assert utts[0].prev_action is None  # 直前に assistant なし
    assert utts[1].prev_action == "Read,Bash,Edit"


def test_prev_action_caps_at_ten(tmp_path: Path) -> None:
    names = [f"T{i}" for i in range(12)]
    lines = [
        _user_line("q1", uuid="u1", ts="2026-06-01T00:00:00Z"),
        _assistant_tooluse_line(names, uuid="a1", ts="2026-06-01T00:00:01Z"),
        _user_line("q2", uuid="u2", ts="2026-06-01T00:00:02Z"),
    ]
    f = tmp_path / "s1.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    utts = list(extract_utterances(f, pj_slug="x"))
    pa = utts[1].prev_action
    assert pa is not None
    assert pa.endswith(",…")
    # 10 tool 名 + 末尾 … 。それ以上の名前は切られる。
    assert pa.split(",")[:10] == [f"T{i}" for i in range(10)]
    assert "T10" not in pa and "T11" not in pa


def test_prev_action_survives_tool_result_rows(tmp_path: Path) -> None:
    """assistant tool_use の直後に必ず続く tool_result 行があっても prev_action は
    消えない（#379 root cause 修正）。

    以前は tool_result 行到達時に pending_tool_names を誤ってリセットしており、実
    transcript では tool_use の直後に必ず tool_result 行が来るため、extractor_version=2
    の行は実測窓 1,124 件全件で prev_action=null になっていた（test_prev_action_joins_tool_names
    は tool_result 行を含まない非現実的な fixture だったため検出できていなかった）。
    """
    lines = [
        _user_line("最初の質問", uuid="u1", ts="2026-06-01T00:00:00Z"),
        _assistant_tooluse_line(["Read"], uuid="a1", ts="2026-06-01T00:00:01Z"),
        _tool_result_line(uuid="tr1", ts="2026-06-01T00:00:02Z"),
        _assistant_tooluse_line(["Bash"], uuid="a2", ts="2026-06-01T00:00:03Z"),
        _tool_result_line(uuid="tr2", ts="2026-06-01T00:00:04Z"),
        _user_line("次の質問", uuid="u2", ts="2026-06-01T00:00:05Z"),
    ]
    f = tmp_path / "s1.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    utts = list(extract_utterances(f, pj_slug="x"))
    assert len(utts) == 2
    assert utts[1].prev_action == "Read,Bash"


# --- offset (incremental) ----------------------------------------------------

def test_start_line_offset_skips_processed(tmp_path: Path) -> None:
    """start_line で既処理行をスキップしても line_no は実ファイル行番号を保つ。"""
    lines = [
        _user_line("古い", uuid="u1"),
        _user_line("新しい", uuid="u2"),
    ]
    f = tmp_path / "s1.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    utts = list(extract_utterances(f, pj_slug="x", start_line=1))
    assert len(utts) == 1
    assert utts[0].text == "新しい"
    assert utts[0].line_no == 2
