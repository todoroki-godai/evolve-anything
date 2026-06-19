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
