"""Claude Code / Codex 共通契約の配線テスト（#268）。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_runtime_entrypoints_require_shared_policy() -> None:
    for name in ("CLAUDE.md", "AGENTS.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "docs/agent-contract/policy.md" in text
        assert "作業開始前" in text


def test_shared_policy_defines_primary_and_lane_invariants() -> None:
    text = (ROOT / "docs/agent-contract/policy.md").read_text(encoding="utf-8")
    for invariant in (
        "実装の既定は Codex",
        "1 lane = 1 owner = 1 writer",
        "<issue>-<slug>",
        "自動 checkout",
        "git add -A",
        "merge、release、Issue close は人間",
    ):
        assert invariant in text


def test_known_stale_statements_stay_corrected_advisory() -> None:
    """既知の種別のみ検出・迂回可能。2026-11-01 の #268 レビューで見つけた旧記述の戻りだけを見る。

    毎朝の処理を本人の指示で再開するときは、停止の記述と一緒にこのテストの該当行も消す。
    """
    fleet = (ROOT / "spec/components-fleet.md").read_text(encoding="utf-8")
    assert "Claude Code 主担当" not in fleet
    for name in ("CLAUDE.md", "SPEC.md", "spec/quickstart.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "2026-10-31" in text, name
        assert "再登録しない" in text or "実行しない" in text, name


def test_codex_adapter_has_no_known_machine_replacement_fingerprints() -> None:
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for forbidden in (
        ".Codex-plugin",
        "~/.Codex/",
        "Codex plugin validate",
        "Codex Code",
    ):
        assert forbidden not in text


def test_capability_matrix_keeps_runtime_identifiers_explicit() -> None:
    text = (ROOT / "docs/agent-contract/capability-matrix.md").read_text(encoding="utf-8")
    assert "claude plugin validate" in text
    assert "codex plugin add/list/marketplace/remove" in text
    assert "同名subcommandなし" in text


def test_pr_template_contains_sha_fixed_handoff_fields() -> None:
    text = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
    for field in ("Task ID", "Base SHA", "Head SHA", "Owned paths", "Open risks"):
        assert field in text
    assert "単独ではenforcementではありません" in text
