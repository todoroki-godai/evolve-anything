"""evolve.run_evolve が idiom_autopromote phase を常時 emit することの保証（ADR-047・#447）。

idiom_autopromote phase の出力（result["idiom_autopromote"]）が、
- 常時 emit される（対象 0 でも result にキーを置く・常時 emit 原則）
- promoted が int で emit される（#448 growth_report の (d.get("promoted") or 0) 契約）
- **最重要の不変条件**: confirmed=True が 1 件も無い現状（全 idiom 未確認）では promoted=0 で、
  一致シグナルがあっても corrections / weak_signals に一切書かない（雪崩防止 + dry-run ゼロ書込）
ことを実 run_evolve 経由で検証する。決定論・LLM 非依存。
"""
import json
import sys
from pathlib import Path

import pytest

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "skills" / "evolve" / "scripts"))
sys.path.insert(0, str(_plugin_root / "skills" / "audit" / "scripts"))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "scripts"))

from correction_semantic import store as cs_store  # noqa: E402
from evolve import run_evolve  # noqa: E402
from weak_signals.store import WeakSignal, append_signals  # noqa: E402


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """テスト用 DATA_DIR を設定（実環境 DATA_DIR を読み書きさせない）。"""
    monkeypatch.setattr("evolve.DATA_DIR", tmp_path)
    monkeypatch.setattr("evolve.EVOLVE_STATE_FILE", tmp_path / "evolve-state.json")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    return tmp_path


def test_idiom_autopromote_phase_always_emitted(data_dir):
    """result["idiom_autopromote"] が常に存在し promoted は int（#448 契約）。"""
    result = run_evolve(dry_run=True)
    assert "idiom_autopromote" in result, "idiom_autopromote phase が emit されていない"
    iap = result["idiom_autopromote"]
    # error でも判定キーは必ず置かれる
    assert "promoted" in iap or "error" in iap
    if "error" not in iap:
        assert isinstance(iap["promoted"], int)


def _resolve_slug():
    """evolve が使う PJ slug を解決（_resolve_pj_slug と同経路）。"""
    from evolve import _resolve_pj_slug
    return _resolve_pj_slug(None)


def test_unconfirmed_idiom_does_not_promote_and_writes_nothing(data_dir):
    """**起動時無発火 E2E**: confirmed 未設定の idiom + 一致シグナルでも promoted=0 + 書き込みゼロ。

    現環境（313 idiom 全件未確認）の構造的安全特性を、実 run_evolve 経由で再現する。
    """
    slug = _resolve_slug()
    prov = {"source_path": "/u.jsonl", "line_no": 1, "session_id": "s1",
            "text": "四国めたんじゃなくて", "reason": "後置型", "judge": "llm_haiku"}
    # 未確認の idiom（confirmed フィールド無し = False 扱い）
    cs_store.append_idioms(
        [cs_store.CorrectionIdiom(idiom="四国めたんじゃなくて", provenance=prov,
                                  detected_at="2026-06-10T00:00:00+00:00", pj_slug=slug)],
        path=data_dir / "correction_idioms.jsonl",
    )
    # 物理キー一致する未昇格 weak_signal
    append_signals(
        [WeakSignal(channel="llm_judge", provenance=prov,
                    detected_at="2026-06-10T00:00:00+00:00", session_id="s1", pj_slug=slug)],
        path=data_dir / "weak_signals.jsonl",
    )
    ws_before = (data_dir / "weak_signals.jsonl").read_text(encoding="utf-8")

    result = run_evolve(dry_run=True)

    iap = result["idiom_autopromote"]
    assert iap.get("promoted") == 0, "未確認 idiom で自動昇格が発火した（雪崩防止違反）"
    # corrections に書かれない / weak_signals は不変（promoted フラグも立たない）
    assert not (data_dir / "corrections.jsonl").exists()
    assert (data_dir / "weak_signals.jsonl").read_text(encoding="utf-8") == ws_before
    # 昇格レコードが weak_signals に紛れていないことの確認
    recs = [json.loads(l) for l in ws_before.splitlines() if l.strip()]
    assert all(not r.get("promoted") for r in recs)
