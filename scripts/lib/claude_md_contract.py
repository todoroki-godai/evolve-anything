"""claude_md_contract.py — CLAUDE.md 契約不変条件の決定論検査（#415）。

背景: CLAUDE.md（毎セッション全文がコンテキストに載る hot ドキュメント）を圧縮したい。
だが過去の圧縮（PR #416）で **契約が hot から消える事故** が起き、「契約フラグ6行を復元」
「契約落ち4行を是正」「dry-run 既定を復元」等の追加修正が4本必要になった。

既存の `doc_budget.py` は byte 予算・セクション予算・リンク実在しか検査しておらず、契約
文言が生き残ったかは一切見ない。したがって圧縮で契約が消えても doc_budget は緑のまま通る。

本モジュールは「不変条件が CLAUDE.md 本文のどこかに残っているか」を **不変条件単位**
（行単位ではない — 表を1行に畳んでも、不変条件を表す語がどこかに残っていれば緑）で検査する。
LLM を使わない。正規表現も使わない（正規表現は書き手が誤り、静かに常時 True になりやすいため、
素の部分文字列一致にする）。

判定は「その不変条件の必須語（`all_of`）が全て本文に含まれるか」。`REQUIRED_INVARIANTS` の
各語は着手時点（2026-08-17）の CLAUDE.md 本文に実在することを grep で確認済み。

`MUST_STAY_SECTIONS` は圧縮時に別ファイルへ移設してはいけないセクション（例:
`## Compaction Instructions` は harness が compaction 時に読むため、移した瞬間に機能死する）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple


@dataclass(frozen=True)
class Invariant:
    """1つの不変条件。`name` は識別子、`all_of` は本文にすべて含まれるべき語のタプル。"""

    name: str
    all_of: Tuple[str, ...]


# 単一ソース。着手時点の CLAUDE.md 本文に各語が実在することを grep で確認済み（2026-08-17）。
# 件数は golden（test_claude_md_contract.py の REQUIRED_INVARIANTS_COUNT）で守られている。
# 減らす/増やす場合はテスト側の golden も同時に更新すること。
REQUIRED_INVARIANTS: Tuple[Invariant, ...] = (
    Invariant(
        "store_write_barrier",
        all_of=("単一ゲート", "既定 reject", "fail-open", "store_write_raw", "EVOLVE_WRITE_GUARD=warn"),
    ),
    Invariant("dry_run_purity", all_of=("dry-run 純度",)),
    Invariant("ttl_read_time", all_of=("read 時 age", "writer-death")),
    Invariant(
        "single_source_functions",
        all_of=("fold_effective", "pj_slug", "file_lock", "review_channels"),
    ),
    Invariant("raw_history_allowlist", all_of=("allowlist", "load_effective_history")),
    Invariant("hook_fail_open", all_of=("fail-open",)),
    Invariant("human_approval", all_of=("人間の y/n", "無人適用しない")),
    Invariant("cli_dry_run_default", all_of=("既定 dry-run",)),
    Invariant("deterministic_zero_llm", all_of=("決定論", "LLM 非依存")),
    Invariant("revert_scope", all_of=("evolve drain 経由の新規採用のみ",)),
    Invariant("no_status_numbers", all_of=("到達状況の数値をこのファイルに書かない",)),
    Invariant("display_cull_surface", all_of=("display_cull", "silence != evaluated")),
    Invariant("safe_llm_call", all_of=("safe_llm_call", "事前予約")),
    Invariant("memory_project_scope", all_of=("project スコープ", "他PJ混入を reject")),
    Invariant("idiom_autopromote_frozen", all_of=("autopromote", "no-op", "凍結中")),
    Invariant("codex_hook_pending", all_of=("Codex hook 配線は保留",)),
    Invariant(
        "shrink_freeze",
        all_of=("新設凍結", "advisory proposal adapter", "weak_signal channel"),
    ),
    Invariant("contract_flag_criterion", all_of=("不変条件単位",)),
)


# 圧縮時に他ファイルへ移設してはいけない `## ` セクション見出し。
# - Compaction Instructions: harness が compaction 時に読む。移した瞬間に機能死する。
# - Superpowers 共存: メタ操作時のスキル発火抑制の唯一の記述。
# - 目指すユーザー体験: 新機能採否判定の基準そのもの（CLAUDE.md 冒頭で毎回参照される）。
MUST_STAY_SECTIONS: Tuple[str, ...] = (
    "## Compaction Instructions",
    "## Superpowers 共存",
    "## 目指すユーザー体験（全機能の判断基準）",
)

# Agent contract ヘッダ（冒頭の docs/agent-contract/policy.md への参照）は `## ` 見出しを
# 持たないため MUST_STAY_SECTIONS と別に検査する。
_AGENT_CONTRACT_HEADER_TOKEN = "docs/agent-contract/policy.md"


def _read_claude_md(repo_root: Path) -> str | None:
    claude_md = Path(repo_root) / "CLAUDE.md"
    if not claude_md.is_file():
        return None
    try:
        return claude_md.read_text(encoding="utf-8")
    except OSError:
        return None


def _missing_tokens(invariant: Invariant, text: str) -> List[str]:
    return [tok for tok in invariant.all_of if tok not in text]


def check_claude_md_contracts(repo_root: Path) -> List[Dict[str, Any]]:
    """欠落した不変条件を `[{"invariant": name, "missing": [tok, ...]}]` で返す。

    CLAUDE.md が無い PJ では非該当（空リスト）。全て揃っていれば空リスト。
    """
    text = _read_claude_md(repo_root)
    if text is None:
        return []
    findings: List[Dict[str, Any]] = []
    for inv in REQUIRED_INVARIANTS:
        missing = _missing_tokens(inv, text)
        if missing:
            findings.append({"invariant": inv.name, "missing": missing})
    return findings


def check_must_stay_sections(repo_root: Path) -> List[Dict[str, str]]:
    """移設禁止セクションが欠落していないかを検査する。

    見出し文字列そのものの部分文字列一致（正規表現不使用）。CLAUDE.md が無い PJ では
    非該当（空リスト）。
    """
    text = _read_claude_md(repo_root)
    if text is None:
        return []
    findings: List[Dict[str, str]] = []
    for heading in MUST_STAY_SECTIONS:
        if heading not in text:
            findings.append({"section": heading, "reason": "missing_heading"})
    if _AGENT_CONTRACT_HEADER_TOKEN not in text:
        findings.append({"section": "Agent contract header", "reason": "missing_reference"})
    return findings


def layer2_check(repo_root: Path) -> Dict[str, Any]:
    """dogfood Layer2（report invariants）形式で返す。`{"check": name, "failures": [...]}`。

    invariants.run_all() が返す各要素と同じ shape（`_print_layer2` / `_layer2_has_red` が
    そのまま扱える）。blocking 扱い＝欠落があれば dogfood gate の exit code が赤くなる。
    """
    failures: List[Dict[str, str]] = []
    for finding in check_claude_md_contracts(repo_root):
        failures.append(
            {
                "check": "claude_md_contract",
                "detail": f"invariant '{finding['invariant']}' missing tokens: {finding['missing']}",
            }
        )
    for finding in check_must_stay_sections(repo_root):
        failures.append(
            {
                "check": "claude_md_contract",
                "detail": f"must-stay section missing: {finding['section']} ({finding['reason']})",
            }
        )
    return {"check": "claude_md_contract", "failures": failures}
