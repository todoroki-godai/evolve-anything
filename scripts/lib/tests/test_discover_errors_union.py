"""discover.detect_error_patterns の cross-dir union + read 層 slug 別名（#45/#47・ADR-049 ①）。

DATA_DIR 断片化（canonical / legacy rename / plugins-data hook split）の移行期に
errors.jsonl が複数 dir に分裂し、かつ PJ rename（rl-anything→evolve-anything）で legacy が
旧 slug タグのまま残るため、self-audit が母集団を取り逃す。本テストは:
  - cross-dir union: canonical だけでなく legacy / plugins-data の errors.jsonl も読む
  - read 層 slug 別名: 旧 slug project='rl-anything' を当 PJ(evolve-anything) に畳む
  - 他 PJ(bots) の legacy は混ぜない / canonical が tmp 素直な子のとき hermetic

決定論・LLM 非依存。HOME 隔離は scripts/lib/tests/conftest の autouse で済む。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discover  # noqa: E402


def _write_errors(d: Path, records) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "errors.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )


def _err(text: str, project: str, n: int = 1):
    return [{"type": "api_error", "error": text, "project": project} for _ in range(n)]


@pytest.fixture(autouse=True)
def _no_suppression(monkeypatch):
    """suppression list を空に固定（パターンが抑制で消えないように）。"""
    monkeypatch.setattr(discover, "load_suppression_list", lambda: set())


def _patterns_by_text(patterns):
    return {p["pattern"]: p["count"] for p in patterns}


def test_unions_canonical_and_legacy_with_alias(tmp_path, monkeypatch):
    """canonical(現slug) + legacy(旧slug) の errors を union + 別名で当 PJ に合算する。"""
    canonical = tmp_path / "evolve-anything"
    legacy = tmp_path / "rl-anything"
    _write_errors(canonical, _err("canon-boom", "evolve-anything", 3))
    _write_errors(legacy, _err("legacy-boom", "rl-anything", 3))
    monkeypatch.setattr(discover, "DATA_DIR", canonical)
    patterns = discover.detect_error_patterns(
        threshold=3, project_root=Path("/x/evolve-anything")
    )
    by = _patterns_by_text(patterns)
    # 旧 slug legacy-boom も当 PJ に畳まれて閾値到達 → 両方 surface
    assert by.get("canon-boom") == 3
    assert by.get("legacy-boom") == 3


def test_unions_plugins_data(tmp_path, monkeypatch):
    """plugins-data hook split 先の errors も union に含める。"""
    canonical = tmp_path / "evolve-anything"
    plugins_data = tmp_path / "plugins" / "data" / "evolve-anything-evolve-anything"
    _write_errors(canonical, _err("canon-boom", "evolve-anything", 3))
    _write_errors(plugins_data, _err("hook-boom", "evolve-anything", 3))
    monkeypatch.setattr(discover, "DATA_DIR", canonical)
    patterns = discover.detect_error_patterns(
        threshold=3, project_root=Path("/x/evolve-anything")
    )
    by = _patterns_by_text(patterns)
    assert by.get("canon-boom") == 3
    assert by.get("hook-boom") == 3


def test_other_pj_legacy_not_attributed(tmp_path, monkeypatch):
    """rename されていない他 PJ(bots) の legacy errors は当 PJ に混ぜない（別名は当 PJ 限定）。"""
    canonical = tmp_path / "evolve-anything"
    legacy = tmp_path / "rl-anything"
    _write_errors(canonical, _err("canon-boom", "evolve-anything", 3))
    _write_errors(legacy, _err("bots-boom", "bots", 5))
    monkeypatch.setattr(discover, "DATA_DIR", canonical)
    patterns = discover.detect_error_patterns(
        threshold=3, project_root=Path("/x/evolve-anything")
    )
    by = _patterns_by_text(patterns)
    assert by.get("canon-boom") == 3
    assert "bots-boom" not in by


def test_hermetic_tmp_only_reads_canonical(tmp_path, monkeypatch):
    """canonical が tmp の素直な子のとき兄弟 dir は存在せず canonical のみ読む（実 home 非参照）。"""
    canonical = tmp_path / "evolve-anything"
    _write_errors(canonical, _err("canon-boom", "evolve-anything", 3))
    monkeypatch.setattr(discover, "DATA_DIR", canonical)
    patterns = discover.detect_error_patterns(
        threshold=3, project_root=Path("/x/evolve-anything")
    )
    by = _patterns_by_text(patterns)
    assert by.get("canon-boom") == 3
    assert len(by) == 1
