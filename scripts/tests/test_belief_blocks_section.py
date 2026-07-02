"""belief_blocks observability builder のテスト（#285・決定論）。

belief_blocks.jsonl の有無・件数で section が None / ✓ / ⚠ を返すことを検証する。
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_SCRIPTS = _PLUGIN_ROOT / "scripts"
for _p in (_LIB, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import belief_entropy  # noqa: E402
import rl_common  # noqa: E402
from audit.observability import _OBSERVABILITY_BUILDERS, collect_observability  # noqa: E402
from audit.sections import build_belief_blocks_section  # noqa: E402


def _write_block(data_dir: Path, summary_head: str, age_days: int = 0) -> None:
    ts = datetime.now(timezone.utc) - timedelta(days=age_days)
    rec = {
        "ts": ts.isoformat(),
        "retention": 0.1,
        "drift": 0.9,
        "summary_head": summary_head,
    }
    with (data_dir / belief_entropy.BLOCKS_FILENAME).open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def test_none_when_no_blocks_log(tmp_path, monkeypatch):
    """belief_blocks.jsonl が無ければ対象外（None）。"""
    monkeypatch.setattr(rl_common, "DATA_DIR", tmp_path)
    assert build_belief_blocks_section(tmp_path) is None


def test_clean_line_when_log_exists_but_no_recent(tmp_path, monkeypatch):
    """ログはあるが直近 block なし → 評価済 ✓ 行を残す（silence != evaluated）。"""
    monkeypatch.setattr(rl_common, "DATA_DIR", tmp_path)
    _write_block(tmp_path, "古い block", age_days=90)  # ウィンドウ外
    section = build_belief_blocks_section(tmp_path)
    assert section is not None
    combined = "\n".join(section)
    assert "Belief Entropy Gate" in combined
    assert "✓" in combined
    assert "block なし" in combined


def test_warn_line_with_recent_blocks(tmp_path, monkeypatch):
    """直近 block があれば件数と head を ⚠ で surface する。"""
    monkeypatch.setattr(rl_common, "DATA_DIR", tmp_path)
    _write_block(tmp_path, "壊れた要約 A", age_days=1)
    _write_block(tmp_path, "壊れた要約 B", age_days=2)
    section = build_belief_blocks_section(tmp_path)
    combined = "\n".join(section)
    assert "⚠" in combined
    assert "2 件" in combined
    assert "壊れた要約 A" in combined


def test_byte_invariance_clean(tmp_path, monkeypatch):
    """#115 advisory 共通枠への載せ替えで clean 出力を 1 バイトも変えない。"""
    monkeypatch.setattr(rl_common, "DATA_DIR", tmp_path)
    _write_block(tmp_path, "古い block", age_days=90)  # ウィンドウ外
    assert build_belief_blocks_section(tmp_path) == [
        "## Belief Entropy Gate（全PJ横断・低信頼 memory ブロック）",
        "",
        "✓ 評価したが直近 30 日の block なし（auto-memory の要約はソース corrections を保持）",
        "",
    ]


def test_byte_invariance_warn(tmp_path, monkeypatch):
    """#115 載せ替えで warn（件数 + head 列挙）出力を 1 バイトも変えない。"""
    monkeypatch.setattr(rl_common, "DATA_DIR", tmp_path)
    _write_block(tmp_path, "壊れた要約 A", age_days=1)
    _write_block(tmp_path, "壊れた要約 B", age_days=2)
    assert build_belief_blocks_section(tmp_path) == [
        "## Belief Entropy Gate（全PJ横断・低信頼 memory ブロック）",
        "",
        "⚠ 直近 30 日で 2 件の低信頼要約を書込前に破棄（retention 低 or drift 過剰）。"
        "頻発する場合は corrections の質か要約プロンプトを点検:",
        "  - 壊れた要約 A",
        "  - 壊れた要約 B",
        "",
    ]


def test_registered_in_observability_contract():
    """belief_blocks が _OBSERVABILITY_BUILDERS に登録されている。"""
    keys = [k for k, _ in _OBSERVABILITY_BUILDERS]
    assert "belief_blocks" in keys


def test_collect_observability_surfaces_belief_blocks(tmp_path, monkeypatch):
    """collect_observability 経由でも belief_blocks key が立つ（両経路伝播）。"""
    monkeypatch.setattr(rl_common, "DATA_DIR", tmp_path)
    _write_block(tmp_path, "壊れた要約", age_days=1)
    result = collect_observability(tmp_path)
    assert "belief_blocks" in result
