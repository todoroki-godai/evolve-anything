"""known_fp_patterns のテスト（決定論・LLM 非依存 / #341）。

remediation の FP_EXCLUSIONS を通り抜けて auto_fixable に landing してしまう
「既知 FP パターン」（SSM 風論理パス・/tmp・拡張子なし論理パス・.archive/_archived・
英大文字の汎用略語）を決定論で照合する自己完結カタログ。self_analysis（#341）と
remediation（#337）の両方から参照できるよう scripts/lib に小さく独立させる。
"""
import sys
from pathlib import Path

_lib = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_lib))

import known_fp_patterns as kfp


# ── SSM 風論理パス（/<service>/<param> で拡張子なし） ──


def test_ssm_style_path_matched():
    assert kfp.match_known_fp("/myapp/db/password") == "ssm_style_path"
    assert kfp.match_known_fp("/service/config/token") == "ssm_style_path"


def test_real_file_path_not_ssm():
    # 拡張子がある実ファイルは SSM 扱いしない
    assert kfp.match_known_fp("scripts/lib/foo.py") != "ssm_style_path"


# ── /tmp パス ────────────────────────────────────────


def test_tmp_path_matched():
    assert kfp.match_known_fp("/tmp/abc/output.json") == "tmp_path"
    assert kfp.match_known_fp("/tmp/scratch") == "tmp_path"
    assert kfp.match_known_fp("/var/tmp/x") == "tmp_path"


# ── .archive / _archived ─────────────────────────────


def test_archive_path_matched():
    assert kfp.match_known_fp("openspec/changes/archive/2026-01-01-old/tasks.md") == "archive_path"
    assert kfp.match_known_fp("skills/_archived/old-skill/SKILL.md") == "archive_path"
    assert kfp.match_known_fp("data/.archive/snapshot.json") == "archive_path"


# ── 拡張子なし論理パス ───────────────────────────────


def test_extensionless_logical_path():
    # スラッシュ区切りだが拡張子も先頭スラッシュもない論理識別子
    assert kfp.match_known_fp("some/logical/identifier") == "extensionless_logical_path"


# ── 英大文字の汎用略語 ───────────────────────────────


def test_generic_uppercase_abbreviation():
    assert kfp.match_known_fp("SSM") == "generic_abbreviation"
    assert kfp.match_known_fp("API") == "generic_abbreviation"
    assert kfp.match_known_fp("TODO") == "generic_abbreviation"


# ── 真の検出対象（FP でない） ────────────────────────


def test_normal_module_ref_not_fp():
    assert kfp.match_known_fp("scripts/lib/missing_module.py") is None


def test_empty_and_none_safe():
    assert kfp.match_known_fp("") is None
    assert kfp.match_known_fp(None) is None


# ── issue dict 経由の照合（self_analysis 用） ────────


def test_match_known_fp_in_issue_reads_detail_path():
    issue = {"type": "stale_ref", "file": "CLAUDE.md", "detail": {"path": "/tmp/x/y"}}
    name = kfp.match_known_fp_in_issue(issue)
    assert name == "tmp_path"


def test_match_known_fp_in_issue_reads_matched():
    issue = {"type": "hardcoded", "file": "r.md", "detail": {"matched": "SSM"}}
    assert kfp.match_known_fp_in_issue(issue) == "generic_abbreviation"


def test_match_known_fp_in_issue_clean_returns_none():
    issue = {"type": "stale_ref", "file": "CLAUDE.md", "detail": {"path": "scripts/lib/real.py"}}
    assert kfp.match_known_fp_in_issue(issue) is None


def test_pattern_names_are_stable_set():
    # カタログの公開キーが固定（self_analysis の dedup_key 安定性のため）
    assert kfp.KNOWN_FP_PATTERN_NAMES == {
        "ssm_style_path",
        "tmp_path",
        "archive_path",
        "extensionless_logical_path",
        "generic_abbreviation",
    }
