"""claude_md_contract.py — CLAUDE.md 契約不変条件の決定論検査（#415）。

背景: CLAUDE.md（毎セッション全文がコンテキストに載る hot ドキュメント）を圧縮したい。
だが過去の圧縮（PR #416）で **契約が hot から消える事故** が起き、「契約フラグ6行を復元」
「契約落ち4行を是正」「dry-run 既定を復元」等の追加修正が4本必要になった。

既存の `doc_budget.py` は byte 予算・セクション予算・リンク実在しか検査しておらず、契約
文言が生き残ったかは一切見ない。したがって圧縮で契約が消えても doc_budget は緑のまま通る。

## 脅威モデル（重要・2026-08-17 #415 で明記）

**この検査が守るのは「CLAUDE.md 圧縮時のうっかり削除」だけである。改ざん耐性は無い。**

CLAUDE.md を圧縮するのはオーケストレーター（人間の指示を受けた Claude）で、結果を確認するのは
ユーザーである。この構図に敵対的な第三者は登場しない。守るべきは「圧縮作業中に契約文言を
うっかり消してしまうこと」であって、「意図的に検査をすり抜けて契約を隠すこと」ではない。

PR #415/#492 では一時期、コードブロック・HTML コメント・字下げ・inline 構文などへ契約語を
退避させる「改ざん」を防ぐため、単位分割（表行/リスト項目/段落/引用）・span 級構文除去・
否定語検出などの層を積み重ねた。しかし外部 cold review による追試のたびに新しい素通り
（欠陥数は 5→3→1→4 と減らなかった）と、逆方向の新しい誤検出（可視の `<strong>` で囲んだ
正当な契約文が false red になる等）と、`RecursionError` によるクラッシュ（`>` を1000回
重ねた引用の再帰処理）が交互に生まれ続けた。実質的に手製の Markdown パーサを作り込んで
おり、層を足すたびに「素通り」と「誤検出」の両方が増える悪循環に陥っていた。これは対処
すべき脅威モデルの誤り（存在しない攻撃者を想定していた）であり、実装の巧拙の問題ではない。

したがって本検査は**意図的に単純**にしてある: **`Invariant.all_of` の各語が CLAUDE.md
全文のどこかに部分文字列として存在するかだけを見る。** 行・段落・表・引用ブロックといった
構造上の共起は要求しない。コードブロックや HTML コメントの中に契約語を置いても「存在する」
と判定される（それは仕様であり欠陥ではない — 意図的に隠そうとする人がいない前提のため）。

LLM を使わない。正規表現も使わない（正規表現は書き手が誤り、静かに常時 True になりやすい
ため、素の部分文字列一致にする）。

判定は「その不変条件の必須語（`all_of`）が全て本文に含まれるか」。`REQUIRED_INVARIANTS` の
各語は着手時点（2026-08-17）の CLAUDE.md 本文に実在することを grep で確認済み。

`MUST_STAY_SECTIONS` は圧縮時に別ファイルへ移設してはいけないセクション（例:
`## Compaction Instructions` は harness が compaction 時に読むため、移した瞬間に機能死する）。

**最終的な担保は本検査ではなく、CLAUDE.md 圧縮 PR の人間レビューである。** 本検査は「機械的に
見落としやすい典型的な削除ミス」を早期に赤くするための補助であり、`claude_md_diff_advisory.py`
（CLAUDE.md 変更時に契約語を含む差分行を CI ログへ出力する advisory・判定はしない）がその
レビューを助ける。

## 既知の検出漏れクラス（#415 keyset 完全化・2026-08-17 実測で確認・隠さず明記）

必須語をすべて残したまま、**直後に矛盾する注記を書き足す**編集は検出できない（部分文字列は
消えていないため）。実際に以下3件を real CLAUDE.md に適用し、いずれも `check_claude_md_contracts`
が緑のままであることを実測で確認した:
  - `verbosity_no_auto_apply`: 「auto-apply しない」の直後に「※ただし2026-09-01のロールアウトで
    この制限は撤廃され、実際には auto-apply する」を追記
  - `daily_runner_human_approval`: 「適用は対話で人間承認」の直後に「レガシー仕様。現行版は
    無人で即時適用に変更済み」を追記
  - `evolve_tier_cli_sync_default`: 「sync は既定 dry-run、`--apply` のみ書込」の直後に
    「社内合意により現在は既定 apply に変更中」を追記

これは「圧縮時のうっかり削除」を守る本検査の脅威モデルの外側（意図的な文意の書き換え）であり、
対処は依然として人間レビュー（`claude_md_diff_advisory.py` の差分表示）に委ねる。

一方で、行の**移設**（別セクションへの丸ごと移動）や**入替**（2行の swap）は本検査を回避しない
（部分文字列が文書のどこかに残っている限り検出され続ける。実測: `noise_agent_type_kind` の
行を `## Compaction Instructions` 直前へ移設・`subagent_noise_single_source` と
`memory_capability_single_source` の行を丸ごと swap のいずれも緑のまま＝事実の消失が
起きていないので正しい挙動）。全角ダッシュ・ゼロ幅スペース・巨大空白パディングによる
トークン破壊はいずれも実測で正しく検出された（部分文字列一致は不可視文字の混入にも
過剰検出側に倒れるため、意図しない文字化けの検知としても機能する）。
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
#
# 18件（707f988e 初版）+ codex cold review [Must]4（棚卸し漏れ）で追加した5件 = 27件。
# 27件 + #415 keyset 完全化（全文契約句の網羅洗い出し）で追加した33件 = 60件。
# store_write_barrier / single_source_functions が2件・4件に分割されているのは、以前の
# 単位共起（同一行・同一段落）要求バージョンの名残。本版は共起を要求しない（全文のどこかに
# あればよい）ため分割している必然性は無いが、無用な差分を避けるためそのまま維持している。
REQUIRED_INVARIANTS: Tuple[Invariant, ...] = (
    Invariant(
        "store_write_barrier_core",
        all_of=("単一ゲート", "既定 reject", "fail-open", "store_write_raw"),
    ),
    # EVOLVE_WRITE_GUARD=warn は本文に1回のみ出現（一意）。追加不要。
    Invariant("store_write_barrier_downgrade", all_of=("EVOLVE_WRITE_GUARD=warn",)),
    # dry-run 純度 は本文に1回のみ出現（一意）。追加不要。
    Invariant("dry_run_purity", all_of=("dry-run 純度",)),
    Invariant("ttl_read_time", all_of=("read 時 age", "writer-death")),
    Invariant("single_source_fold_effective", all_of=("fold_effective", "単一ソース")),
    # pj_slug は本文に3回出現（`pj_slug` 行 x2 + 別コンポーネントの
    # `pj_slug.resolve_cc_memory_dir` 参照 x1）、単一ソースは16回出現。どちらも対象行
    # （PJ slug 導出の単一ソース）だけを削除しても他出現が残り検出漏れる（2026-08-17
    # オーケストレーター実測）。行に固有の語を追加して一意化。
    Invariant(
        "single_source_pj_slug",
        all_of=("pj_slug", "単一ソース", "worktree slug 食い違いを防止"),
    ),
    Invariant("single_source_file_lock", all_of=("file_lock", "単一ソース")),
    Invariant("single_source_review_channels", all_of=("review_channels", "単一ソース")),
    Invariant("raw_history_allowlist", all_of=("allowlist", "load_effective_history")),
    # fail-open は本文に6回出現（汎用語）。単独では対象行を1つ消しても他5箇所が残り
    # 検出漏れる（2026-08-17 オーケストレーター実測。team-lead 提示の再現例）。
    # icebox_notice 行（fail-open の具体例）を対象に一意化。
    Invariant("hook_fail_open", all_of=("fail-open", "icebox_notice")),
    # 人間の y/n・無人適用しない はいずれも本文に1回のみ出現（一意）。追加不要。
    Invariant("human_approval", all_of=("人間の y/n", "無人適用しない")),
    # 既定 dry-run は本文に4回出現（複数コンポーネントで独立に再述される汎用語）。
    # 単独では対象行を1つ消しても他3箇所が残り検出漏れる（2026-08-17 実測）。
    # scaffold_advisory 行に固有の語を追加して一意化。
    Invariant("cli_dry_run_default", all_of=("既定 dry-run", "builder stub 生成")),
    # 決定論は本文に30回、LLM 非依存は3回出現（いずれも汎用語）。fleet 観測・介入 行を
    # 単独で消しても両語とも他出現が残り検出漏れる（2026-08-17 実測）。行に固有の語を
    # 追加して一意化。
    Invariant(
        "deterministic_zero_llm",
        all_of=("決定論", "LLM 非依存", "env_score / 導入状況を一覧表示"),
    ),
    # evolve drain 経由の新規採用のみ は本文に1回のみ出現（一意）。追加不要。
    Invariant("revert_scope", all_of=("evolve drain 経由の新規採用のみ",)),
    # 到達状況の数値をこのファイルに書かない は本文に1回のみ出現（一意）。追加不要。
    Invariant("no_status_numbers", all_of=("到達状況の数値をこのファイルに書かない",)),
    Invariant("display_cull_surface", all_of=("display_cull", "silence != evaluated")),
    Invariant("safe_llm_call", all_of=("safe_llm_call", "事前予約")),
    Invariant("memory_project_scope", all_of=("project スコープ", "他PJ混入を reject")),
    Invariant("idiom_autopromote_frozen", all_of=("autopromote", "no-op", "凍結中")),
    # Codex hook 配線は保留 は本文に1回のみ出現（一意）。追加不要。
    Invariant("codex_hook_pending", all_of=("Codex hook 配線は保留",)),
    Invariant(
        "shrink_freeze",
        all_of=("新設凍結", "advisory proposal adapter", "weak_signal channel"),
    ),
    # 不変条件単位 は本文に1回のみ出現（一意）。追加不要。
    Invariant("contract_flag_criterion", all_of=("不変条件単位",)),
    # --- ここから #492 codex cold review [Must]4（棚卸し漏れ5件）の反映 ---------------
    Invariant("revert_conflict_no_overwrite", all_of=("上書きせず中止", "のみ実書込")),
    Invariant(
        "memory_guard_transition_gate",
        all_of=("prompt_injection/secret_exfil を reject", "同名エントリの上書きは決定論遷移検証でゲート"),
    ),
    Invariant(
        "fleet_pr_human_merge_gate",
        all_of=("path allowlist・push account guard で強制", "マージは人間"),
    ),
    Invariant("cleanup_individual_approval", all_of=("候補提示→個別承認→実行", "のみに安全側限定")),
    Invariant("tier_sync_explicit_approval", all_of=("dry-run diff を全件提示", "明示承認後にのみ")),
    # --- #415 keyset 完全化（全 CLAUDE.md 契約句の洗い出し・2026-08-17） -----------------
    # 「## コンポーネント」表 123 行を1行ずつ削除し `check_claude_md_contracts` +
    # `check_must_stay_sections` にかけた実測で「黙って消える + 契約語彙を含む」行が
    # 表以外の節も含め 50 行見つかった。うち raw_history_gate の stale_allowlist fail
    # （scripts/lib/raw_history_gate.py・降格経路なし。production tree AST テスト
    # `scripts/lib/tests/test_raw_history_gate_production.py` が全呼出しを強制検査。
    # `shrink_freeze.assert_no_new_keys` と同じ「テスト時契約」で downgrade env が
    # 存在しない）と、evolve-tier sync の既定 dry-run をクイックスタートの bash コメント
    # （L259/L260 相当）で再述している2行は、上の `tier_sync_explicit_approval` が
    # 既に別の言い回しで hot に保持しているため省略した（詳細は HANDOVER-keyset.md）。
    # 残り 33 行は下記に追加する。
    Invariant(
        "revert_scope_freeze",
        all_of=("PR2/PR3 を凍結した", "採用実績が乏しく"),
    ),
    Invariant(
        "contract_flag_preservation_rule",
        all_of=("動作を縛る語は要約時も必ず残す",),
    ),
    Invariant(
        "evolve_decisions_flat_result_path_scope",
        all_of=("flat `result_path` は run 1件時のみ",),
    ),
    Invariant(
        "triage_ledger_dry_run_no_write",
        all_of=("SKIP 判断の状態管理", "dry-run 非書込"),
    ),
    Invariant(
        "pitfall_enforcement_commit_block",
        all_of=("danger 判定は commit をブロック",),
    ),
    Invariant(
        "observability_contract_single_source",
        all_of=("必ず surface すべき observability 行の単一ソース",),
    ),
    Invariant(
        "outcome_attribution_dry_run_diff_surface",
        all_of=("負の転移は末尾 rollback", "dry-run に before/after 順位差分を surface"),
    ),
    Invariant(
        "correction_semantic_human_source_only_promotion",
        all_of=("フェーズ昇格は human-source のみ駆動",),
    ),
    Invariant(
        "daily_review_success_only_marking",
        all_of=("promote 成功後のみ既読追記", "部分失敗は対象外"),
    ),
    Invariant(
        "growth_report_single_source",
        all_of=("閾値は growth_engine が単一ソース",),
    ),
    Invariant(
        "correction_rate_full_coverage_only",
        all_of=("カバレッジ100%確定週のみ表示",),
    ),
    Invariant(
        "subagent_noise_single_source",
        all_of=("noise_agent_type_kind", "単一ソース"),
    ),
    Invariant(
        "verbosity_no_auto_apply",
        all_of=("weak_signals へ emit", "auto-apply しない"),
    ),
    Invariant(
        "cross_pj_priority_no_auto_approval",
        all_of=("提示のみ・自動承認しない",),
    ),
    Invariant(
        "plugin_self_auto_apply_downgrade",
        all_of=("auto-apply は人間承認必須に降格",),
    ),
    Invariant(
        "dogfood_gate_light_non_blocking",
        all_of=("`--layer light` は pre-push で非ブロッキング自動実行",),
    ),
    Invariant(
        "weak_signals_drain_pending_marker_intentional",
        all_of=("pending marker の dry-run 書込は意図された設計（消さない）",),
    ),
    Invariant(
        "reconcile_surfaced_drain_persist_false",
        all_of=("phases の dry-run は `persist=False` で非書込",),
    ),
    Invariant(
        "idiom_filter_manual_reject_option",
        all_of=("idiom 単位拒否も可能",),
    ),
    Invariant(
        "recall_validity_soft_downgrade",
        all_of=("validity metadata で降格", "ハード除外はしない"),
    ),
    Invariant(
        "subagents_errors_bugfix_single_source",
        all_of=("is_noise_agent_type", "単一ソース"),
    ),
    Invariant(
        "memory_capability_single_source",
        all_of=("resolve_cc_memory_dir", "単一ソース"),
    ),
    Invariant(
        "skill_vuln_scan_combo_required",
        all_of=("combo 必須で検出",),
    ),
    Invariant(
        "daily_runner_human_approval",
        all_of=("適用は対話で人間承認",),
    ),
    Invariant(
        "memory_hygiene_no_auto_apply",
        all_of=("重複残骸は手順提案のみで auto-apply しない",),
    ),
    Invariant(
        "invalid_frontmatter_no_auto_fix",
        all_of=("auto-fix せず人手修正提案",),
    ),
    Invariant(
        "evolve_tier_cli_sync_default",
        all_of=("sync は既定 dry-run、`--apply` のみ書込",),
    ),
    Invariant(
        "evaluation_provenance_no_guessing",
        all_of=("不明値は推測せず None",),
    ),
    Invariant(
        "fleet_propose_no_re_present_rejected",
        all_of=("reject 済み提案は再提示しない",),
    ),
    Invariant(
        "codex_usage_fail_open_no_merge",
        all_of=("advisory 表示（fail-open）", "CC 側 token_usage とは合算しない"),
    ),
    Invariant(
        "evolve_revert_cli_default_dry_run",
        all_of=("entry_id は戦果ボードか --list が印字する",),
    ),
    Invariant(
        "testpaths_single_source",
        all_of=("`testpaths` が単一ソース",),
    ),
    Invariant(
        "evolve_keyset_snapshot_union_merge",
        all_of=("既存キーとの union merge", "条件付きキーを golden から消さない"),
    ),
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
    except (OSError, UnicodeDecodeError):
        return None


def _missing_tokens(invariant: Invariant, text: str) -> List[str]:
    return [tok for tok in invariant.all_of if tok not in text]


def check_claude_md_contracts(repo_root: Path) -> List[Dict[str, Any]]:
    """欠落した不変条件を `[{"invariant": name, "missing": [tok, ...]}]` で返す。

    汎用ライブラリとしての挙動: CLAUDE.md が無い/読めない PJ では非該当（空リスト）。
    **この repo を検査する `layer2_check` は別途 CLAUDE.md の存在を必須にする**（missing/
    unreadable を failure 扱いにする。codex cold review [Must]2）。
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

    汎用関数（`check_claude_md_contracts` 等）と異なり、**ここでは CLAUDE.md の存在・可読性を
    必須とする**（この repo 自身を検査する呼び出し元は dogfood/cli.py のみであり、CLAUDE.md が
    無い状態は「非該当」ではなく圧縮事故そのものだから。codex cold review [Must]2）。
    """
    claude_md_path = Path(repo_root) / "CLAUDE.md"
    if not claude_md_path.is_file():
        return {
            "check": "claude_md_contract",
            "failures": [
                {
                    "check": "claude_md_contract",
                    "detail": "CLAUDE.md が存在しない（削除・改名事故の可能性。圧縮前の安全網が機能していない）",
                }
            ],
        }
    text = _read_claude_md(repo_root)
    if text is None:
        return {
            "check": "claude_md_contract",
            "failures": [
                {
                    "check": "claude_md_contract",
                    "detail": "CLAUDE.md を読み取れない（権限・エンコーディングエラー）",
                }
            ],
        }
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
