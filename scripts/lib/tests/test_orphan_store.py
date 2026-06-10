"""orphan_store（writer あり reader なしの jsonl ストア）検出のテスト。

決定論・LLM 非依存。tmp_path に疑似プラグインツリー（hooks/ + hooks.json + scripts/ + skills/）
を作って静的突合する。実プラグインツリーに依存しないため、別の真の orphan が増減しても
このテストは安定する（#422）。

突合方針:
- writer  = hooks.json に登録された hook の本体ソースが書く jsonl ファイル名
- reader  = scripts/ ・ skills/（テスト除外）のソースに現れる jsonl ファイル名
- orphan  = writer にあって reader に無い
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import orphan_store  # noqa: E402
from audit.sections_orphan import build_orphan_store_section  # noqa: E402


def _make_plugin(
    tmp_path: Path,
    *,
    hook_files: dict[str, str],
    registered: list[str],
    scripts_files: dict[str, str] | None = None,
    skills_files: dict[str, str] | None = None,
) -> Path:
    """疑似プラグインツリーを作る。

    hook_files:    {ファイル名: 本文} を hooks/ に置く
    registered:    hooks.json の command で参照する hook ファイル名のリスト
    scripts_files: {相対パス: 本文} を scripts/ に置く
    skills_files:  {相対パス: 本文} を skills/ に置く
    """
    root = tmp_path / "plugin"
    hooks = root / "hooks"
    hooks.mkdir(parents=True)
    for name, body in hook_files.items():
        (hooks / name).write_text(body, encoding="utf-8")

    hooks_json = {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f'python3 "${{CLAUDE_PLUGIN_ROOT}}/hooks/{name}"',
                        }
                        for name in registered
                    ],
                }
            ]
        }
    }
    (hooks / "hooks.json").write_text(json.dumps(hooks_json), encoding="utf-8")

    scripts = root / "scripts"
    scripts.mkdir()
    for rel, body in (scripts_files or {}).items():
        p = scripts / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    skills = root / "skills"
    skills.mkdir()
    for rel, body in (skills_files or {}).items():
        p = skills / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    return root


# --- find_store_writers -----------------------------------------------------

def test_writers_only_count_registered_hooks(tmp_path: Path) -> None:
    """hooks.json に登録された hook の writer だけを拾う（未登録 hook は発火しない）。"""
    root = _make_plugin(
        tmp_path,
        hook_files={
            "a.py": 'common.append_jsonl(DATA_DIR / "alpha.jsonl", r)',
            "b.py": 'common.append_jsonl(DATA_DIR / "beta.jsonl", r)',  # 未登録
        },
        registered=["a.py"],
    )
    writers = orphan_store.find_store_writers(root)
    assert "alpha.jsonl" in writers
    assert "beta.jsonl" not in writers


# --- find_store_readers -----------------------------------------------------

def test_readers_scan_scripts_and_skills_excluding_tests(tmp_path: Path) -> None:
    """reader は scripts/ ・ skills/ から拾い、tests 配下は除外する。"""
    root = _make_plugin(
        tmp_path,
        hook_files={"a.py": 'append_jsonl(DATA_DIR / "alpha.jsonl", r)'},
        registered=["a.py"],
        scripts_files={
            "lib/reader.py": 'open(DATA_DIR / "alpha.jsonl")',
            "tests/test_x.py": 'open(DATA_DIR / "gamma.jsonl")',
        },
        skills_files={"foo/scripts/s.py": 'read(DATA_DIR / "delta.jsonl")'},
    )
    readers = orphan_store.find_store_readers(root)
    assert "alpha.jsonl" in readers
    assert "delta.jsonl" in readers
    # tests 配下の参照は reader にカウントしない
    assert "gamma.jsonl" not in readers


# --- detect_orphan_stores ---------------------------------------------------

def test_detects_orphan_when_writer_without_reader(tmp_path: Path) -> None:
    """登録 writer はあるが reader が無いストアを orphan として検出する。"""
    root = _make_plugin(
        tmp_path,
        hook_files={"orphan.py": 'append_jsonl(DATA_DIR / "lonely.jsonl", r)'},
        registered=["orphan.py"],
        scripts_files={"lib/x.py": "# nothing reads lonely"},
    )
    report = orphan_store.detect_orphan_stores(root)
    assert "lonely.jsonl" in report.orphans


def test_no_orphan_when_reader_exists(tmp_path: Path) -> None:
    """reader が存在すれば orphan ではない（誤検知しない）。"""
    root = _make_plugin(
        tmp_path,
        hook_files={"w.py": 'append_jsonl(DATA_DIR / "used.jsonl", r)'},
        registered=["w.py"],
        scripts_files={"lib/consumer.py": 'open(DATA_DIR / "used.jsonl")'},
    )
    report = orphan_store.detect_orphan_stores(root)
    assert "used.jsonl" not in report.orphans
    assert report.orphans == []


def test_unregistered_writer_not_flagged(tmp_path: Path) -> None:
    """未登録 hook が書くストアは（発火しないので）orphan に含めない。"""
    root = _make_plugin(
        tmp_path,
        hook_files={
            "reg.py": 'append_jsonl(DATA_DIR / "active.jsonl", r)',
            "unreg.py": 'append_jsonl(DATA_DIR / "dormant.jsonl", r)',
        },
        registered=["reg.py"],
        scripts_files={"lib/c.py": 'open(DATA_DIR / "active.jsonl")'},
    )
    report = orphan_store.detect_orphan_stores(root)
    # active は reader あり、dormant は未登録 → どちらも orphan ではない
    assert report.orphans == []


def test_report_carries_writer_evidence(tmp_path: Path) -> None:
    """orphan ごとに、どの hook が書いているか（evidence）を持つ。"""
    root = _make_plugin(
        tmp_path,
        hook_files={"orphan.py": 'append_jsonl(DATA_DIR / "lonely.jsonl", r)'},
        registered=["orphan.py"],
    )
    report = orphan_store.detect_orphan_stores(root)
    assert "lonely.jsonl" in report.orphans
    assert "orphan.py" in report.writer_files["lonely.jsonl"]


# --- live plugin tree: tool_durations が removed 後は出ないこと ----------------

def test_live_tree_has_no_tool_durations_orphan() -> None:
    """実プラグインツリーで tool_durations.jsonl は（hook 削除後）orphan に現れない。

    tool_duration hook を削除したので writer が消え、orphan リストに載らないことを保証する。
    """
    report = orphan_store.detect_orphan_stores()  # PLUGIN_ROOT を使う
    assert "tool_durations.jsonl" not in report.orphans

    # reader が確実にある主要ストアは orphan に出ない（誤検知ガード）
    for store in ("corrections.jsonl", "usage.jsonl", "sessions.jsonl", "errors.jsonl"):
        assert store not in report.orphans, f"{store} が誤検知された"


# --- hooks.json: Bash PostToolUse から tool_duration が消えていること --------

def test_bash_posttooluse_no_tool_duration_hook() -> None:
    """Bash PostToolUse の hook 群に tool_duration.py が登録されていない（#422）。"""
    from plugin_root import PLUGIN_ROOT

    hooks_json = json.loads(
        (PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
    )
    bash_groups = [
        g
        for g in hooks_json["hooks"].get("PostToolUse", [])
        if g.get("matcher") == "Bash"
    ]
    commands = [
        h.get("command", "") for g in bash_groups for h in g.get("hooks", [])
    ]
    assert not any("tool_duration" in c for c in commands), (
        "Bash PostToolUse に tool_duration hook が残っている"
    )


# --- build_orphan_store_section (observability builder) ---------------------

def test_builder_emits_warning_when_orphan_present(tmp_path: Path, monkeypatch) -> None:
    """orphan があれば ⚠ と該当ストア名・writer を surface する。"""
    root = _make_plugin(
        tmp_path,
        hook_files={"orphan.py": 'append_jsonl(DATA_DIR / "lonely.jsonl", r)'},
        registered=["orphan.py"],
    )
    monkeypatch.setattr(orphan_store, "_default_plugin_root", lambda: root)
    section = build_orphan_store_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "⚠" in body
    assert "lonely.jsonl" in body
    assert "orphan.py" in body


def test_builder_emits_ok_line_when_clean(tmp_path: Path, monkeypatch) -> None:
    """orphan が無くても『評価したが該当なし ✓』を残す（silence != evaluated）。"""
    root = _make_plugin(
        tmp_path,
        hook_files={"w.py": 'append_jsonl(DATA_DIR / "used.jsonl", r)'},
        registered=["w.py"],
        scripts_files={"lib/c.py": 'open(DATA_DIR / "used.jsonl")'},
    )
    monkeypatch.setattr(orphan_store, "_default_plugin_root", lambda: root)
    section = build_orphan_store_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "✓" in body


def test_builder_registered_in_observability_contract() -> None:
    """observability contract に orphan_store builder が登録されていること。"""
    from audit.observability import _OBSERVABILITY_BUILDERS

    keys = [k for k, _ in _OBSERVABILITY_BUILDERS]
    assert "orphan_store" in keys
