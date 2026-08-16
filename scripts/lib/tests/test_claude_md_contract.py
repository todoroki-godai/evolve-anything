"""claude_md_contract（CLAUDE.md 契約不変条件の決定論検査）のテスト（#415）。

決定論・LLM 非依存。合成 fixture に加え、実 repo の CLAUDE.md に対しても検査する
（PR #416 の再発防止＝「圧縮で契約が hot から消えたら赤くなる」ことを保証するテストなので、
実ファイルへの検査を外すと本来の目的を検証できない）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import claude_md_contract  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[3]

# REQUIRED_INVARIANTS の件数 golden。無断で不変条件を減らす/増やすことを禁止するガード。
# 変更するときは REQUIRED_INVARIANTS 本体のコメントと、この数値の両方を更新すること。
REQUIRED_INVARIANTS_COUNT = 18


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _full_claude_md_text() -> str:
    """REQUIRED_INVARIANTS + MUST_STAY_SECTIONS を全て満たす合成 CLAUDE.md 本文を組み立てる。"""
    lines = [
        "# evolve-anything Plugin",
        "",
        "> **Agent contract:** docs/agent-contract/policy.md を全文読むこと。",
        "",
        "## 目指すユーザー体験（全機能の判断基準）",
        "",
        "到達状況の数値をこのファイルに書かない。",
        "適用は必ず人間の y/n（無人適用しない）。",
        "**適用範囲: evolve drain 経由の新規採用のみ**。",
        "淘汰した事実は display_cull として必ず surface する（silence != evaluated）。",
        "",
        "**新設凍結**: 新 store / observability section / advisory proposal adapter /"
        " weak_signal channel の追加は停止する。",
        "",
        "コンポーネント単位でなく不変条件単位で判定する。",
        "",
        "| コンポーネント | 一言サマリ |",
        "|---|---|",
        "| `store_write` | 全ストア書込の単一ゲート。既定 reject、registry 不在は fail-open"
        "（例外口 `store_write_raw`）。env `EVOLVE_WRITE_GUARD=warn` で降格できる。 |",
        "| dry_run | dry-run 純度 |",
        "| `weak_signals` | 45日 TTL は read 時 age 導出で writer-death 非依存 |",
        "| single_source | fold_effective / pj_slug / file_lock / review_channels |",
        "| raw_history | raw history read は allowlist に固定。業務 reader は"
        " `load_effective_history`。 |",
        "| cli | CLI は既定 dry-run |",
        "| general | 決定論・LLM 非依存 |",
        "| safe_llm | 無人呼び出しは safe_llm_call に一点集約し費用は事前予約 |",
        "| memory | project スコープ4層防御で他PJ混入を reject |",
        "| idiom | #379 Step1 で凍結中、autopromote() は no-op |",
        "| runtime | Codex hook 配線は保留 |",
        "",
        "## Superpowers 共存",
        "",
        "メタ操作時はスキルを発火させない。",
        "",
        "## Compaction Instructions",
        "",
        "1. 完了済みタスクと未完了タスクの区別",
        "",
    ]
    return "\n".join(lines)


# --- check_claude_md_contracts -------------------------------------------------


def test_no_claude_md_returns_empty(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert claude_md_contract.check_claude_md_contracts(root) == []


def test_full_synthetic_claude_md_has_no_missing_contracts(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "CLAUDE.md", _full_claude_md_text())
    assert claude_md_contract.check_claude_md_contracts(root) == []


def test_real_repo_claude_md_has_no_missing_contracts() -> None:
    """本 repo 直下の実 CLAUDE.md が現時点で全不変条件を満たすことを確認する（#415 入口条件）。"""
    findings = claude_md_contract.check_claude_md_contracts(_REPO_ROOT)
    assert findings == [], findings


def test_removing_one_token_flags_only_that_invariant(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    text = _full_claude_md_text().replace("既定 reject", "既定 allow")
    _write(root / "CLAUDE.md", text)
    findings = claude_md_contract.check_claude_md_contracts(root)
    names = {f["invariant"] for f in findings}
    assert names == {"store_write_barrier"}
    assert "既定 reject" in findings[0]["missing"]


def test_each_invariant_flagged_independently_when_its_token_removed(tmp_path: Path) -> None:
    """REQUIRED_INVARIANTS を1つずつ、必須語を1つ抜いて欠落させ、その不変条件だけが
    検出されることを確認する（他の不変条件が巻き添えで検出されないこと）。
    """
    base_text = _full_claude_md_text()
    for inv in claude_md_contract.REQUIRED_INVARIANTS:
        token = inv.all_of[0]
        assert token in base_text, f"fixture is missing required token {token!r} for {inv.name}"
        mutated = base_text.replace(token, "", 1)
        root = tmp_path / f"repo_{inv.name}"
        _write(root / "CLAUDE.md", mutated)
        findings = claude_md_contract.check_claude_md_contracts(root)
        names = {f["invariant"] for f in findings}
        assert inv.name in names, f"removing {token!r} did not flag {inv.name}"


def test_required_invariants_count_golden() -> None:
    assert len(claude_md_contract.REQUIRED_INVARIANTS) == REQUIRED_INVARIANTS_COUNT


# --- check_must_stay_sections ---------------------------------------------------


def test_must_stay_sections_pass_on_full_fixture(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "CLAUDE.md", _full_claude_md_text())
    assert claude_md_contract.check_must_stay_sections(root) == []


def test_real_repo_must_stay_sections_present() -> None:
    findings = claude_md_contract.check_must_stay_sections(_REPO_ROOT)
    assert findings == [], findings


def test_missing_compaction_instructions_detected(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    text = _full_claude_md_text().split("## Compaction Instructions")[0]
    _write(root / "CLAUDE.md", text)
    findings = claude_md_contract.check_must_stay_sections(root)
    sections = {f["section"] for f in findings}
    assert "## Compaction Instructions" in sections


def test_missing_agent_contract_header_detected(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    text = _full_claude_md_text().replace("docs/agent-contract/policy.md", "")
    _write(root / "CLAUDE.md", text)
    findings = claude_md_contract.check_must_stay_sections(root)
    sections = {f["section"] for f in findings}
    assert "Agent contract header" in sections


def test_no_claude_md_must_stay_sections_empty(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert claude_md_contract.check_must_stay_sections(root) == []


# --- layer2_check ----------------------------------------------------------------


def test_layer2_check_shape_clean(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "CLAUDE.md", _full_claude_md_text())
    result = claude_md_contract.layer2_check(root)
    assert result == {"check": "claude_md_contract", "failures": []}


def test_layer2_check_reports_failures(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    text = _full_claude_md_text().replace("既定 reject", "既定 allow")
    _write(root / "CLAUDE.md", text)
    result = claude_md_contract.layer2_check(root)
    assert result["check"] == "claude_md_contract"
    assert len(result["failures"]) == 1
    assert "store_write_barrier" in result["failures"][0]["detail"]


def test_layer2_check_real_repo_clean() -> None:
    result = claude_md_contract.layer2_check(_REPO_ROOT)
    assert result == {"check": "claude_md_contract", "failures": []}
