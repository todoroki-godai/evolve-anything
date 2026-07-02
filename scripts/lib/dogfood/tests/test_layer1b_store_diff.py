"""Layer 1b: 非 dry-run store 差分検査のユニットテスト（#518）。

Layer 1a が「dry-run は何も書かない」方向を検査するのに対し、Layer 1b は
「apply 境界（`evolve --drain`）で書かれるべきものが書かれる」方向を検査する。

隔離コピー方式（#515）を流用:
  (a) DATA_DIR を tmp にコピー
  (b) CLAUDE_PLUGIN_DATA=<コピー先> で `evolve --drain --result-json <result>` を実行
      （--result-json 指定により MARKER_ROOT=home 固定マーカーを読まず result から pending を取る
        ＝隔離が完全になる。#402/drain_pending の result_json 優先経路）
  (c) コピー側の store 差分で assert:
      - drain サマリに weak_signals_persisted があり dry_run=False
      - weak_signals.jsonl 等の決定論チャネル書込が isolated copy に現れる

サブプロセス（実 drain）は ``_run_drain`` に閉じ込め、本テストでは monkeypatch で
mock する（LLM 非依存・決定論・実環境非汚染）。実 drain 経路は Layer 1b の
実行可否テストで別途カバーする方針。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


_lib_dir = Path(__file__).resolve().parent.parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))


from dogfood import layer1  # noqa: E402


def _seed_data_dir(tmp_path: Path) -> Path:
    real_data = tmp_path / "real_data"
    real_data.mkdir()
    (real_data / "state.json").write_text("{}", encoding="utf-8")
    return real_data


def _seed_result(tmp_path: Path, pending=None) -> Path:
    """drain が読む dry-run result JSON（evolve_decisions.pending を含む）。"""
    result = {"phases": {}, "evolve_decisions": {"pending": pending or []}}
    p = tmp_path / "result.json"
    p.write_text(json.dumps(result), encoding="utf-8")
    return p


# ─────────────────────────────────────────────────────────────
# check_store_diff_1b — 隔離 drain で store が書かれる方向を検査
# ─────────────────────────────────────────────────────────────


def test_store_diff_passes_claude_plugin_data_to_drain(monkeypatch, tmp_path):
    """drain サブプロセスに CLAUDE_PLUGIN_DATA=<コピー先> が渡る。"""
    real_data = _seed_data_dir(tmp_path)
    result_path = _seed_result(tmp_path)

    received = []

    def fake_drain(repo_root, result_json, env=None):
        received.append(dict(env) if env else {})
        # drain が weak_signals.jsonl を isolated copy に書く（決定論チャネル）
        data = Path(env["CLAUDE_PLUGIN_DATA"])
        (data / "weak_signals.jsonl").write_text(
            '{"channel": "manual_edit_after_ai", "pj_slug": "evolve-anything"}\n',
            encoding="utf-8",
        )
        summary = {
            "accepted": [],
            "weak_signals_persisted": {"written": 1, "dry_run": False, "total": 1},
        }
        return {"returncode": 0, "stdout": json.dumps(summary), "stderr": ""}

    monkeypatch.setattr(layer1, "_run_drain", fake_drain)
    res = layer1.check_store_diff_1b(
        repo_root=tmp_path / "repo",
        data_dir=real_data,
        out_dir=tmp_path / "out",
        result_json=result_path,
    )
    assert res["status"] == "pass", res
    assert len(received) == 1
    isolated = Path(received[0]["CLAUDE_PLUGIN_DATA"])
    assert isolated != real_data
    assert isolated.is_dir()


def test_store_diff_asserts_weak_signals_persisted_not_dry_run(monkeypatch, tmp_path):
    """drain サマリに weak_signals_persisted があり dry_run=False であることを検査。"""
    real_data = _seed_data_dir(tmp_path)
    result_path = _seed_result(tmp_path)

    def fake_drain(repo_root, result_json, env=None):
        data = Path(env["CLAUDE_PLUGIN_DATA"])
        (data / "weak_signals.jsonl").write_text(
            '{"channel": "esc_interrupt"}\n', encoding="utf-8"
        )
        summary = {"weak_signals_persisted": {"written": 1, "dry_run": False}}
        return {"returncode": 0, "stdout": json.dumps(summary), "stderr": ""}

    monkeypatch.setattr(layer1, "_run_drain", fake_drain)
    res = layer1.check_store_diff_1b(
        repo_root=tmp_path / "repo",
        data_dir=real_data,
        out_dir=tmp_path / "out",
        result_json=result_path,
    )
    assert res["status"] == "pass", res
    assert res["weak_signals_persisted"]["dry_run"] is False


def test_store_diff_fails_when_dry_run_true(monkeypatch, tmp_path):
    """weak_signals_persisted.dry_run=True は契約違反として fail。

    apply 境界 drain は必ず非 dry-run で書く（#484 根治の核心）。dry_run=True で
    返るのは配線が dry-run に倒れている回帰なので fail にする。
    """
    real_data = _seed_data_dir(tmp_path)
    result_path = _seed_result(tmp_path)

    def fake_drain(repo_root, result_json, env=None):
        summary = {"weak_signals_persisted": {"written": 0, "dry_run": True}}
        return {"returncode": 0, "stdout": json.dumps(summary), "stderr": ""}

    monkeypatch.setattr(layer1, "_run_drain", fake_drain)
    res = layer1.check_store_diff_1b(
        repo_root=tmp_path / "repo",
        data_dir=real_data,
        out_dir=tmp_path / "out",
        result_json=result_path,
    )
    assert res["status"] == "fail", res


def test_store_diff_fails_when_weak_signals_persisted_missing(monkeypatch, tmp_path):
    """drain サマリに weak_signals_persisted が無い（配線消滅）は fail。"""
    real_data = _seed_data_dir(tmp_path)
    result_path = _seed_result(tmp_path)

    def fake_drain(repo_root, result_json, env=None):
        return {"returncode": 0, "stdout": json.dumps({"accepted": []}), "stderr": ""}

    monkeypatch.setattr(layer1, "_run_drain", fake_drain)
    res = layer1.check_store_diff_1b(
        repo_root=tmp_path / "repo",
        data_dir=real_data,
        out_dir=tmp_path / "out",
        result_json=result_path,
    )
    assert res["status"] == "fail", res


def test_store_diff_fails_when_persist_errors(monkeypatch, tmp_path):
    """weak_signals_persisted が error dict（例外捕捉）なら fail。"""
    real_data = _seed_data_dir(tmp_path)
    result_path = _seed_result(tmp_path)

    def fake_drain(repo_root, result_json, env=None):
        summary = {"weak_signals_persisted": {"error": "boom"}}
        return {"returncode": 0, "stdout": json.dumps(summary), "stderr": ""}

    monkeypatch.setattr(layer1, "_run_drain", fake_drain)
    res = layer1.check_store_diff_1b(
        repo_root=tmp_path / "repo",
        data_dir=real_data,
        out_dir=tmp_path / "out",
        result_json=result_path,
    )
    assert res["status"] == "fail", res


def test_store_diff_error_when_drain_nonzero_exit(monkeypatch, tmp_path):
    """drain が非ゼロ終了したら error（赤でなく実行エラー）。"""
    real_data = _seed_data_dir(tmp_path)
    result_path = _seed_result(tmp_path)

    def fake_drain(repo_root, result_json, env=None):
        return {"returncode": 1, "stdout": "", "stderr": "traceback..."}

    monkeypatch.setattr(layer1, "_run_drain", fake_drain)
    res = layer1.check_store_diff_1b(
        repo_root=tmp_path / "repo",
        data_dir=real_data,
        out_dir=tmp_path / "out",
        result_json=result_path,
    )
    assert res["status"] == "error", res


def test_store_diff_surfaces_store_changes(monkeypatch, tmp_path):
    """isolated copy への weak_signals.jsonl 書込が store_changes に surface される。"""
    real_data = _seed_data_dir(tmp_path)
    result_path = _seed_result(tmp_path)

    def fake_drain(repo_root, result_json, env=None):
        data = Path(env["CLAUDE_PLUGIN_DATA"])
        (data / "weak_signals.jsonl").write_text(
            '{"channel": "rephrase"}\n', encoding="utf-8"
        )
        summary = {"weak_signals_persisted": {"written": 1, "dry_run": False}}
        return {"returncode": 0, "stdout": json.dumps(summary), "stderr": ""}

    monkeypatch.setattr(layer1, "_run_drain", fake_drain)
    res = layer1.check_store_diff_1b(
        repo_root=tmp_path / "repo",
        data_dir=real_data,
        out_dir=tmp_path / "out",
        result_json=result_path,
    )
    assert res["status"] == "pass", res
    assert "weak_signals.jsonl" in res["store_changes"]["added"]


def test_run_layer1_includes_1b_check(monkeypatch, tmp_path):
    """run_layer1 が 1b_store_diff チェックを checks に含める（NotImplemented 枠の解消）。"""
    real_data = _seed_data_dir(tmp_path)

    # Layer 1a（dry-run）と 1b（drain）両方を mock して実 subprocess/LLM を避ける。
    def fake_dry_run(repo_root, output_path, env=None):
        output_path.write_text(
            json.dumps({"phases": {}, "evolve_decisions": {"pending": []}}),
            encoding="utf-8",
        )
        return {"returncode": 0, "stderr": "", "stdout": ""}

    def fake_drain(repo_root, result_json, env=None):
        data = Path(env["CLAUDE_PLUGIN_DATA"])
        (data / "weak_signals.jsonl").write_text('{"channel": "rephrase"}\n', encoding="utf-8")
        summary = {"weak_signals_persisted": {"written": 1, "dry_run": False}}
        return {"returncode": 0, "stdout": json.dumps(summary), "stderr": ""}

    def fake_ingest(db_dir=None):
        return {"status": "pass", "detail": "stub"}

    monkeypatch.setattr(layer1, "_run_evolve_dry_run", fake_dry_run)
    monkeypatch.setattr(layer1, "_run_drain", fake_drain)
    monkeypatch.setattr(layer1.ingest_check, "check_real_pj_ingest", fake_ingest)
    monkeypatch.setattr(layer1, "_default_data_dir", lambda: real_data)

    out = layer1.run_layer1(tmp_path / "repo", out_dir=tmp_path / "out")
    names = [c["name"] for c in out["checks"]]
    assert "1b_store_diff" in names
    b1 = next(c for c in out["checks"] if c["name"] == "1b_store_diff")
    assert b1["status"] == "pass", b1
