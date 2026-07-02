"""fan-out 費用対効果ロジックのテスト（#14・決定論・LLM 非依存）。

fanout_cost は subagents.jsonl（fan-out 実態）と sessions（一発成功率）を当 PJ スコープで
join し、cost（常に算出可能・非スパース）と advantage（floor ゲート付き）を返す。

HOME 隔離（#457）+ DATA_DIR monkeypatch で subagents.jsonl / sessions を tmp に向ける。
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_LIB = _PLUGIN_ROOT / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import fanout_cost  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """実 DATA_DIR を読まないよう隔離する（HOME 隔離は root conftest の autouse・#119）。"""
    monkeypatch.setattr(fanout_cost, "DATA_DIR", tmp_path)
    return tmp_path


# 当 PJ の正規化 slug = project_dir basename（worktree でない素のパスを使う）。
# 各レコードの project は ``project_dir`` の basename に合わせて当 PJ 帰属させる。
_PJ_DIR = Path("/x/evolve-anything")
_PJ = "evolve-anything"


def _iso(days_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _write_subagents(base: Path, records) -> None:
    with (base / "subagents.jsonl").open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _write_sessions(base: Path, records) -> None:
    with (base / "sessions.jsonl").open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _sub(session_id, agent_type="general-purpose", project="evolve-anything", days_ago=1.0):
    return {
        "session_id": session_id,
        "agent_type": agent_type,
        "project": project,
        "timestamp": _iso(days_ago),
    }


def _sess(session_id, error_count=0, project="evolve-anything", days_ago=1.0):
    return {
        "session_id": session_id,
        "error_count": error_count,
        "project": project,
        "timestamp": _iso(days_ago),
    }


# --- ① subagents 0件 → applicable False / builder None ------------------------

def test_no_subagents_not_applicable(tmp_path):
    out = fanout_cost.compute_fanout_metrics(_PJ_DIR, days=30)
    assert out.get("applicable") is False


def test_builder_none_when_no_subagents(tmp_path):
    from audit.sections_fanout import build_fanout_cost_section
    assert build_fanout_cost_section(tmp_path) is None


# --- ② fan-out 率の算出 -------------------------------------------------------

def test_fanout_rate_one_of_two(tmp_path):
    """session A=3 subagent（fan-out）, session B=1 subagent（単一）→ fan-out 率 1/2。"""
    _write_subagents(tmp_path, [
        _sub("A"), _sub("A"), _sub("A"),
        _sub("B"),
    ])
    out = fanout_cost.compute_fanout_metrics(_PJ_DIR, days=30)
    assert out["applicable"] is True
    cost = out["cost"]
    assert cost["value"]["fanout_session_rate"] == pytest.approx(0.5)
    ev = cost["evidence"]
    assert ev["spawning_sessions"] == 2
    assert ev["fanout_sessions"] == 1


def test_avg_subagents_and_agent_type_breakdown(tmp_path):
    """fan-out session あたり平均 subagent 数 + agent_type 内訳。"""
    _write_subagents(tmp_path, [
        _sub("A", agent_type="general-purpose"),
        _sub("A", agent_type="impl-worker"),
        _sub("A", agent_type="impl-worker"),
        _sub("B", agent_type="Explore"),
    ])
    out = fanout_cost.compute_fanout_metrics(_PJ_DIR, days=30)
    cost = out["cost"]
    # fan-out session は A のみ（3体）→ 平均 3.0
    assert cost["value"]["avg_subagents_per_fanout_session"] == pytest.approx(3.0)
    bd = cost["evidence"]["agent_type_breakdown"]
    assert bd["impl-worker"] == 2
    assert bd["general-purpose"] == 1
    assert bd["Explore"] == 1


# --- ③ agent_type 空レコード除外（#36） --------------------------------------

def test_empty_agent_type_excluded(tmp_path):
    """agent_type が空 / 空白のレコードは本物の Task subagent でないので除外（#36）。"""
    _write_subagents(tmp_path, [
        _sub("A"), _sub("A"),
        _sub("A", agent_type=""),      # 除外
        _sub("A", agent_type="   "),   # 除外
    ])
    out = fanout_cost.compute_fanout_metrics(_PJ_DIR, days=30)
    # 有効 subagent は A×2 のみ → fan-out session (≥2) 1 / spawning 1
    cost = out["cost"]
    assert cost["evidence"]["spawning_sessions"] == 1
    assert cost["evidence"]["fanout_sessions"] == 1
    assert cost["evidence"]["total_subagents"] == 2


def test_id_shaped_agent_type_excluded(tmp_path):
    """ID 形（pure hex / UUID）の agent_type は cost breakdown から除外する。

    実観測（kazevolve）で agent_type=pure hex が内訳を汚していた回帰防止。
    """
    _write_subagents(tmp_path, [
        _sub("A"), _sub("A"),
        _sub("A", agent_type="aab2173eb119c5b91"),                     # 除外（pure hex）
        _sub("A", agent_type="77037416-f452-4241-a414-4eb497336e71"),  # 除外（UUID）
        _sub("A", agent_type="build-a1"),                              # 保持（カスタム名）
    ])
    out = fanout_cost.compute_fanout_metrics(_PJ_DIR, days=30)
    cost = out["cost"]
    # 有効 = general-purpose×2 + build-a1×1 = 3、ID 形 2 件は除外
    assert cost["evidence"]["total_subagents"] == 3
    bd = cost["evidence"]["agent_type_breakdown"]
    assert bd == {"general-purpose": 2, "build-a1": 1}
    assert "aab2173eb119c5b91" not in bd


# --- ④ advantage 分母 < floor → データ不足明示 ------------------------------

def test_advantage_insufficient_sample(tmp_path):
    """各群の session 数が floor 未満 → advantage 値なし + insufficient_sample。"""
    _write_subagents(tmp_path, [
        _sub("A"), _sub("A"),  # fan-out 1 session
        _sub("B"),             # single 1 session
    ])
    _write_sessions(tmp_path, [_sess("A"), _sess("B")])
    out = fanout_cost.compute_fanout_metrics(_PJ_DIR, days=30)
    adv = out["advantage"]
    assert adv["value"] is None
    assert adv["evidence"]["reason"] == "insufficient_sample"
    assert adv["evidence"]["floor"] == fanout_cost.MIN_GROUP_SESSIONS_FLOOR


# --- ⑤ advantage 分母 ≥ floor → delta 算出 ----------------------------------

def test_advantage_delta_computed(tmp_path):
    """両群 ≥ floor のとき fan-out 群 - single 群 の一発成功率 delta を算出する。"""
    floor = fanout_cost.MIN_GROUP_SESSIONS_FLOOR
    subs = []
    sessions = []
    # fan-out 群: floor 個の session、各 2 subagent。全て error_count=0（成功率 1.0）。
    for i in range(floor):
        sid = f"F{i}"
        subs += [_sub(sid), _sub(sid)]
        sessions.append(_sess(sid, error_count=0))
    # single 群: floor 個の session、各 1 subagent。半数が失敗（成功率 0.5 付近）。
    for i in range(floor):
        sid = f"S{i}"
        subs += [_sub(sid)]
        sessions.append(_sess(sid, error_count=0 if i % 2 == 0 else 2))
    _write_subagents(tmp_path, subs)
    _write_sessions(tmp_path, sessions)
    out = fanout_cost.compute_fanout_metrics(_PJ_DIR, days=30)
    adv = out["advantage"]
    assert adv["value"] is not None
    assert adv["evidence"]["fanout_group_sessions"] == floor
    assert adv["evidence"]["single_group_sessions"] == floor
    # fan-out 成功率 1.0、single 成功率 = ceil(floor/2)/floor。delta は正。
    assert adv["value"] > 0
    assert adv["evidence"]["fanout_success_rate"] == pytest.approx(1.0)


# --- ⑥ PJ スコープ（別 PJ の subagents を混ぜない） -------------------------

def test_pj_scope_excludes_other_pj(tmp_path):
    """別 PJ の subagents は当 PJ 集計に混ぜない（#489）。"""
    _write_subagents(tmp_path, [
        _sub("A", project="evolve-anything"),
        _sub("A", project="evolve-anything"),
        _sub("X", project="some-other-pj"),
        _sub("X", project="some-other-pj"),
        _sub("X", project="some-other-pj"),
    ])
    out = fanout_cost.compute_fanout_metrics(Path("/whatever/evolve-anything"), days=30)
    # 当 PJ = evolve-anything のみ: spawning 1 session、subagent 2 体
    cost = out["cost"]
    assert cost["evidence"]["spawning_sessions"] == 1
    assert cost["evidence"]["total_subagents"] == 2


# --- window フィルタ ----------------------------------------------------------

def test_window_filter_excludes_old(tmp_path):
    """days 窓外の subagents は除外する。"""
    _write_subagents(tmp_path, [
        _sub("A", days_ago=1.0), _sub("A", days_ago=1.0),
        _sub("OLD", days_ago=40.0), _sub("OLD", days_ago=40.0),
    ])
    out = fanout_cost.compute_fanout_metrics(Path("/x/evolve-anything"), days=30)
    cost = out["cost"]
    assert cost["evidence"]["spawning_sessions"] == 1
    assert cost["evidence"]["total_subagents"] == 2


def test_token_join_noted_as_proxy(tmp_path):
    """token 直接 join 未対応 → cost evidence に proxy 注記を含める（捏造しない）。"""
    _write_subagents(tmp_path, [_sub("A"), _sub("A")])
    out = fanout_cost.compute_fanout_metrics(Path("/x/evolve-anything"), days=30)
    assert out["cost"]["evidence"].get("token_join") == "unsupported_proxy_count"


# --- ⑦ cross-dir read union（#45 ① read 統一）-------------------------------
# DATA_DIR 断片化の移行期に subagents.jsonl が canonical / legacy / plugins-data に
# 分裂しているため、cold-path 集計 reader は全候補を union read する（ADR-049 ①）。


def _canonical_layout(root: Path) -> Path:
    """canonical = root/evolve-anything を作る。

    iter_read_data_dirs は候補を canonical.parent（= root）から導出するため、
    canonical を tmp の素直な子ではなく ``root/evolve-anything`` にすると
    兄弟 legacy(rl-anything) / plugins-data も root 配下に作れる（hermetic）。
    """
    canonical = root / "evolve-anything"
    canonical.mkdir(parents=True, exist_ok=True)
    return canonical


def test_subagents_unioned_across_canonical_and_legacy(tmp_path, monkeypatch):
    """canonical と legacy(rl-anything) に分裂した subagents を cross-dir union する（#45）。"""
    canonical = _canonical_layout(tmp_path)
    legacy = tmp_path / "rl-anything"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fanout_cost, "DATA_DIR", canonical)
    # canonical に fan-out session A（2体）、legacy に fan-out session B（2体）
    _write_subagents(canonical, [_sub("A"), _sub("A")])
    _write_subagents(legacy, [_sub("B"), _sub("B")])
    out = fanout_cost.compute_fanout_metrics(_PJ_DIR, days=30)
    cost = out["cost"]
    # 両 dir 合算: spawning 2 session、subagent 4 体（legacy を取り逃さない）
    assert cost["evidence"]["spawning_sessions"] == 2
    assert cost["evidence"]["total_subagents"] == 4


def test_subagents_unioned_includes_plugins_data(tmp_path, monkeypatch):
    """plugins-data hook split 先の subagents も union に含める（現行 hook 書込先・#45）。"""
    canonical = _canonical_layout(tmp_path)
    plugins_data = tmp_path / "plugins" / "data" / "evolve-anything-evolve-anything"
    plugins_data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fanout_cost, "DATA_DIR", canonical)
    _write_subagents(canonical, [_sub("A"), _sub("A")])
    _write_subagents(plugins_data, [_sub("P"), _sub("P"), _sub("P")])
    out = fanout_cost.compute_fanout_metrics(_PJ_DIR, days=30)
    cost = out["cost"]
    assert cost["evidence"]["spawning_sessions"] == 2
    assert cost["evidence"]["total_subagents"] == 5


def test_subagents_hermetic_tmp_only_reads_canonical(tmp_path, monkeypatch):
    """canonical が tmp の素直な子のとき、兄弟 dir は存在せず canonical のみ読む（実 home 非参照）。"""
    monkeypatch.setattr(fanout_cost, "DATA_DIR", tmp_path)
    _write_subagents(tmp_path, [_sub("A"), _sub("A")])
    out = fanout_cost.compute_fanout_metrics(_PJ_DIR, days=30)
    # 実 ~/.claude/rl-anything 等を読まない＝canonical の 2 体のみ
    assert out["cost"]["evidence"]["total_subagents"] == 2


def test_legacy_rl_anything_attributed_to_evolve_anything(tmp_path, monkeypatch):
    """旧 slug project='rl-anything' の legacy subagent を当 PJ(evolve-anything) に帰属する（#45/#47）。

    PJ rename（rl-anything→evolve-anything）後、legacy は旧 slug でタグ付けされている。
    read 層 slug 別名 + cross-dir union の両方が効いて初めて self-audit が回収できる。
    """
    canonical = _canonical_layout(tmp_path)
    legacy = tmp_path / "rl-anything"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fanout_cost, "DATA_DIR", canonical)
    # canonical に現 slug の fan-out session、legacy に旧 slug の fan-out session
    _write_subagents(canonical, [_sub("A", project="evolve-anything"), _sub("A", project="evolve-anything")])
    _write_subagents(legacy, [_sub("B", project="rl-anything"), _sub("B", project="rl-anything")])
    out = fanout_cost.compute_fanout_metrics(_PJ_DIR, days=30)
    cost = out["cost"]
    # 旧 slug B も当 PJ に畳まれて合算: spawning 2 / subagent 4
    assert cost["evidence"]["spawning_sessions"] == 2
    assert cost["evidence"]["total_subagents"] == 4


def test_other_pj_legacy_not_attributed(tmp_path, monkeypatch):
    """rename されていない他 PJ(bots) の legacy は当 PJ に混ぜない（別名は当 PJ 限定）。"""
    canonical = _canonical_layout(tmp_path)
    legacy = tmp_path / "rl-anything"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fanout_cost, "DATA_DIR", canonical)
    _write_subagents(canonical, [_sub("A", project="evolve-anything"), _sub("A", project="evolve-anything")])
    _write_subagents(legacy, [_sub("X", project="bots"), _sub("X", project="bots"), _sub("X", project="bots")])
    out = fanout_cost.compute_fanout_metrics(_PJ_DIR, days=30)
    cost = out["cost"]
    # bots は別名対象外 → 当 PJ 集計は canonical の 2 体のみ
    assert cost["evidence"]["spawning_sessions"] == 1
    assert cost["evidence"]["total_subagents"] == 2
