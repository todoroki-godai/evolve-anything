"""claude_md_contract（CLAUDE.md 契約不変条件の決定論検査）のテスト（#415）。

決定論・LLM 非依存。合成 fixture に加え、実 repo の CLAUDE.md に対しても検査する
（PR #416 の再発防止＝「圧縮で契約が hot から消えたら赤くなる」ことを保証するテストなので、
実ファイルへの検査を外すと本来の目的を検証できない）。

## 脅威モデル（2026-08-17 #415 で確定・重要）

この検査は「CLAUDE.md 圧縮時のうっかり削除」だけを守る。**改ざん耐性は無い**（意図的に
コードブロック・HTML コメント・字下げ等へ契約語を退避させれば簡単に通る。それは仕様）。
一時期、単位共起・span 級構文除去・否定語検出などの「隠蔽対策」層を積んだが、外部 cold
review の追試のたびに新しい素通りと新しい誤検出（可視の `<strong>` で囲んだ正当な契約文が
false red になる等）と `RecursionError`（`>` の深い入れ子）が交互に発生し続けたため全て
撤去した（詳細は `claude_md_contract.py` モジュール docstring）。したがってこのテストは
**敵対的な回避手段を1つも検証しない**。検証するのは以下の2点だけ:
  1. 契約語・必須見出しを（普通の編集で）うっかり消したら赤くなるか
  2. 圧縮でありがちな正当な言い換え（空白/改行コード/可視装飾/深い引用）で誤検出しないか
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import claude_md_contract  # noqa: E402
from dogfood import cli as dogfood_cli  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[3]

# REQUIRED_INVARIANTS の件数 golden。無断で不変条件を減らす/増やすことを禁止するガード。
# 変更するときは REQUIRED_INVARIANTS 本体のコメントと、この数値の両方を更新すること。
# 27件 + #415 keyset 完全化（全 CLAUDE.md 契約句の網羅洗い出し）で追加した33件 = 60件。
# 60件 + #415 句単位スイープ（1行複数契約句の盲点是正）で追加した15件 = 75件。
# 75件 + #415 句単位スイープ第2巡（汎用句重複8件を句固有トークンで是正・新規1件+既存7件widen）
# = 76件。
# 76件 + PR #495 narrow-deletion 一般化テストで発覚した fleet_plugins の無防備な句を追加
# した1件 = 77件。
REQUIRED_INVARIANTS_COUNT = 77


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _full_claude_md_text() -> str:
    """REQUIRED_INVARIANTS 全件（件数は `REQUIRED_INVARIANTS_COUNT` golden 参照） +
    MUST_STAY_SECTIONS + Agent contract header を満たす
    合成本文を組み立てる。本版は共起（同一行・同一単位）を要求しないため、各語がどこかに
    含まれていればよい（構造は最小限）。
    """
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
        "| optimize_history | fold_effective が単一ソース |",
        "| slug | pj_slug が単一ソース。worktree slug 食い違いを防止。PJ slug 導出の単一ソース |",
        "| lock | file_lock が単一ソース。ファイル単位排他ロック。自己 deadlock を回避 |",
        "| channels | review_channels が単一ソース。weak チャネルの単一ソース |",
        "| raw_history_gate | 許可の単一ソースは production 定数 |",
        "| raw_history | raw history read は allowlist に固定。業務 reader は"
        " `load_effective_history`。 |",
        "| icebox_notice | fail-open で既存ファイル非破壊 |",
        "| cli | scaffold_advisory は配線チェックリスト。CLI は既定 dry-run。builder stub 生成 |",
        "| general | fleet 観測・介入は env_score / 導入状況を一覧表示。"
        "test-guard status。決定論・LLM 非依存 |",
        "| safe_llm | 無人呼び出しは safe_llm_call に一点集約し費用は事前予約 |",
        "| memory | project スコープ4層防御で他PJ混入を reject |",
        "| idiom | #379 Step1 で凍結中、autopromote() は no-op |",
        "| runtime | Codex hook 配線は保留 |",
        "| revert | conflict は上書きせず中止、CLI は既定 dry-run・のみ実書込 |",
        "| memory_guard | prompt_injection/secret_exfil を reject。"
        "同名エントリの上書きは決定論遷移検証でゲート |",
        "| fleet_pr | path allowlist・push account guard で強制、マージは人間 |",
        "| cleanup | 候補提示→個別承認→実行。のみに安全側限定 |",
        "| tier | dry-run diff を全件提示 → 明示承認後にのみ |",
        "",
        "PR2/PR3 を凍結した。採用実績が乏しく投資に見合わないため。",
        "コンポーネント追加・変更時は spec/components.md に書き、動作を縛る語は要約時も必ず残す。",
        "",
        "| コンポーネント2 | 一言サマリ | 実体 |",
        "|---|---|---|",
        "| evolve_decisions | flat `result_path` は run 1件時のみ | evolve_decisions.py |",
        "| triage_ledger | SKIP 判断の状態管理・dry-run 非書込 | triage_ledger.py |",
        "| pitfall | danger 判定は commit をブロック | pitfall_registry.py |",
        "| observability | 必ず surface すべき observability 行の単一ソース | audit/observability.py |",
        "| outcome_attribution | 負の転移は末尾 rollback、dry-run に before/after 順位差分を surface | audit/outcome_attribution.py |",
        "| correction_semantic | フェーズ昇格は human-source のみ駆動 | correction_semantic/ |",
        "| daily_review | promote 成功後のみ既読追記（部分失敗は対象外） | correction_semantic/daily_review.py |",
        "| growth_report | 閾値は growth_engine が単一ソース | growth_report.py |",
        "| correction_rate | カバレッジ100%確定週のみ表示 | correction_rate.py |",
        "| subagent_noise | noise_agent_type_kind が単一ソース | audit/sections_subagent_noise.py |",
        "| verbosity | weak_signals へ emit、auto-apply しない | verbosity/ |",
        "| cross_pj_priority | 提示のみ・自動承認しない | correction_semantic/cross_pj_priority.py |",
        "| plugin_self | auto-apply は人間承認必須に降格 | skill_origin.py |",
        "| dogfood | 3層検査。`--layer light` は pre-push で非ブロッキング自動実行 | scripts/lib/dogfood/ |",
        "| weak_signals_drain | pending marker の dry-run 書込は意図された設計（消さない） | weak_signals/batch.py |",
        "| reconcile_surfaced | phases の dry-run は `persist=False` で非書込 | cli.py |",
        "| idiom_filter | idiom 単位拒否も可能 | correction_semantic/idiom_filter.py |",
        "| recall_ranking | stale/superseded memory を validity metadata で降格（ハード除外はしない） | fleet/recall.py |",
        "| subagents_errors | is_noise_agent_type が単一ソース | rl_common/detection.py |",
        "| memory_capability | memory dir 解決は `resolve_cc_memory_dir` が単一ソース |"
        " scripts/lib/memory_capability.py |",
        "| fleet plugins | version 無しプラグインの silent stale を cache↔marketplace source"
        " の差分で検出 | fleet/plugins.py |",
        "| skill_vuln_scan | combo 必須で検出 | skill_vuln_scan.py |",
        "| daily | 適用は対話で人間承認 | scripts/lib/daily/ |",
        "| memory_hygiene | 重複残骸は手順提案のみで auto-apply しない | memory_dup_residue.py |",
        "| invalid_frontmatter | auto-fix せず人手修正提案 | frontmatter.py |",
        "| evolve_tier | sync は既定 dry-run、`--apply` のみ書込 | bin/evolve-tier |",
        "| evaluation_provenance | 不明値は推測せず None | scripts/lib/evaluation_provenance.py |",
        "| fleet_propose | reject 済み提案は再提示しない | fleet/propose.py |",
        "| codex_usage | advisory 表示（fail-open）。CC 側 token_usage とは合算しない | fleet/codex_usage.py |",
        "| evolve_revert | #402・既定 dry-run。entry_id は戦果ボードか --list が印字する | bin/evolve-revert |",
        "| testpaths | `testpaths` が単一ソース | pytest.ini |",
        "| evolve_keyset_snapshot | 既存キーとの union merge。条件付きキーを golden から消さない | test_evolve_keyset_snapshot.py |",
        "",
        "単一ソースは `scripts/lib/shrink_freeze.py`。"
        "契約テストが CI portable suite で blocking 強制、pre-push light は非ブロッキング advisory として早期警告。"
        "store の runtime 書込みも `store_write_raw` / `append_signals` の凍結ゲートで reject する。"
        "`scaffold_advisory --write` も凍結中は拒否する。",
        "コードは削除しない・builder は `_OBSERVABILITY_BUILDERS` に登録されたまま。"
        "単一ソースは `shrink_freeze.CULLED_OBSERVABILITY_SECTIONS`。",
        "`propose` は llm-batch-guard 承認ゲート付き。"
        "適用そのものは対話 evolve のまま人間が行い、外殻の worktree 準備と push/PR だけを自動化。"
        "マージは常に人間。",
        "ここは 1 行サマリのみ。",
        "`shrink_freeze.assert_no_new_keys` の凍結中新設 reject。降格経路なし。",
        "",
        "| コンポーネント3 | 一言サマリ | 実体 |",
        "|---|---|---|",
        "| review_channels | content-rich チャネルのみ対象 | correction_semantic/review_channels.py |",
        "| pitfall | pitfalls.md の編集時 lint + commit ゲート（オプトイン） | pitfall_registry.py |",
        "| reconcile_surfaced | remediation 連続提示の count marker 書込と閾値到達時の自動却下を"
        " `evolve --drain` の apply 境界へ移設 | cli.py |",
        "| evaluation_provenance | envelope が単一ソース | scripts/lib/evaluation_provenance.py |",
        "| evolve_keyset_snapshot2 | 宣言済み prefix の増減のみ許容する二層 golden 方式 |"
        " test_evolve_keyset_snapshot.py |",
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


def test_row_anchored_invariants_do_not_false_positive_on_real_claude_md() -> None:
    """完了条件3: 一意化のため語を追加した4件（single_source_pj_slug / hook_fail_open /
    cli_dry_run_default / deterministic_zero_llm）が、正常な実 CLAUDE.md で誤検出しないこと
    を明示的に確認する（`test_real_repo_claude_md_has_no_missing_contracts` の一部として
    暗黙にカバーされているが、追加した語自体の誤検出耐性を独立して示すために分離）。
    """
    row_anchored = {
        "single_source_pj_slug",
        "hook_fail_open",
        "cli_dry_run_default",
        "deterministic_zero_llm",
    }
    findings = claude_md_contract.check_claude_md_contracts(_REPO_ROOT)
    flagged = {f["invariant"] for f in findings}
    assert not (row_anchored & flagged), flagged


def test_removing_one_token_flags_only_that_invariant(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    text = _full_claude_md_text().replace("既定 reject", "既定 allow")
    _write(root / "CLAUDE.md", text)
    findings = claude_md_contract.check_claude_md_contracts(root)
    names = {f["invariant"] for f in findings}
    assert names == {"store_write_barrier_core"}
    assert "既定 reject" in findings[0]["missing"]


def test_each_invariant_flagged_independently_when_its_token_removed(tmp_path: Path) -> None:
    """完了条件2-①: REQUIRED_INVARIANTS の全件（件数は `REQUIRED_INVARIANTS_COUNT` golden
    参照。ハードコードした件数をここに書くと更新のたび腐るので書かない）を1つずつ、必須語を
    1つ抜いて欠落させ、その不変条件だけが検出されることを確認する（他の不変条件が巻き添えで
    検出されないこと）。
    """
    base_text = _full_claude_md_text()
    for inv in claude_md_contract.REQUIRED_INVARIANTS:
        token = inv.all_of[0]
        assert token in base_text, f"fixture is missing required token {token!r} for {inv.name}"
        # 全出現を除去する（同名トークンが他の invariant の合成文にも偶然登場することがあり、
        # 先頭1件だけ抜くと別出現が残って検出できないケースがある）。
        mutated = base_text.replace(token, "")
        root = tmp_path / f"repo_{inv.name}"
        _write(root / "CLAUDE.md", mutated)
        findings = claude_md_contract.check_claude_md_contracts(root)
        names = {f["invariant"] for f in findings}
        assert inv.name in names, f"removing {token!r} did not flag {inv.name}"


def test_required_invariants_count_golden() -> None:
    assert len(claude_md_contract.REQUIRED_INVARIANTS) == REQUIRED_INVARIANTS_COUNT


def test_deleting_the_row_each_invariant_protects_flags_it_in_real_claude_md(
    tmp_path: Path,
) -> None:
    """完了条件2（外部 cold review 欠陥1・行単位の変異）: REQUIRED_INVARIANTS の全件について、
    その不変条件を守っている行を**実 CLAUDE.md から丸ごと削除**したら赤くなることを検証する。

    語を1つ壊す変異（`test_each_invariant_flagged_independently_...`）では検出できない欠陥
    （汎用語 `fail-open` が6箇所に出現するため `hook_fail_open` 等が対象行1つの削除だけでは
    検出漏れしていた。2026-08-17 外部レビュー + オーケストレーター実測）を塞ぐための追加検証。
    合成 fixture では発生しない（実 CLAUDE.md 特有の語の重複具合に依存するため）ため、実
    CLAUDE.md に対して直接実行する。
    """
    text = claude_md_contract._read_claude_md(_REPO_ROOT)
    assert text is not None
    lines = text.split("\n")

    missed: list[str] = []
    for inv in claude_md_contract.REQUIRED_INVARIANTS:
        candidate_lines = [i for i, line in enumerate(lines) if all(tok in line for tok in inv.all_of)]
        assert candidate_lines, f"{inv.name}: 全語が共起する行が実 CLAUDE.md に見つからない"
        idx = candidate_lines[0]
        mutated_lines = lines[:idx] + lines[idx + 1 :]
        root = tmp_path / f"row_deleted_{inv.name}"
        _write(root / "CLAUDE.md", "\n".join(mutated_lines))
        findings = claude_md_contract.check_claude_md_contracts(root)
        names = {f["invariant"] for f in findings}
        if inv.name not in names:
            missed.append(inv.name)

    assert missed == [], f"行削除しても検出されなかった invariant: {missed}"


def _row_clauses(row: str) -> list[str]:
    """行を「|」（テーブルセル区切り）→「。」（文区切り）の順で句に分割する。各要素は
    `row` の厳密な部分文字列（前後の空白を含む）として返すため `row.replace(clause, "", 1)`
    でそのまま安全に削除できる。

    セル分割が必要な理由: `cli_dry_run_default` の旧トークン「scaffold_advisory.py」は
    対象句「CLI は既定 dry-run」とは別のセル（実体列）にあった。「。」だけで分割すると
    句の境界が `|` をまたいでしまい（対象句の直後に実体列が「。」無しで続くため）、
    対象句を削除しても実体列の内容が道連れに消えて偶然赤くなる（＝本来検出すべき欠陥を
    見逃す）。セルで先に区切ることでこの偽陰性を防ぐ。テーブル行でない場合（先頭が `|`
    でない prose 段落）はセル分割をスキップする。
    """
    cells = row.split("|") if row.lstrip().startswith("|") else [row]
    clauses: list[str] = []
    for cell in cells:
        for piece in re.split(r"(?<=。)", cell):
            if piece.strip():
                clauses.append(piece)
    return clauses


def _build_row_to_invariants() -> dict[int, frozenset[str]]:
    """REQUIRED_INVARIANTS が実際に対象としている行番号 → その行を守る invariant 名集合。
    モジュール読込時に1回だけ計算し、parametrize のケース列挙に使う。
    """
    text = claude_md_contract._read_claude_md(_REPO_ROOT)
    assert text is not None
    lines = text.split("\n")
    mapping: dict[int, set[str]] = {}
    for inv in claude_md_contract.REQUIRED_INVARIANTS:
        candidates = [i for i, line in enumerate(lines) if all(tok in line for tok in inv.all_of)]
        assert candidates, f"{inv.name}: 全語が共起する行が実 CLAUDE.md に見つからない"
        mapping.setdefault(candidates[0], set()).add(inv.name)
    return {idx: frozenset(names) for idx, names in mapping.items()}


_ROW_TO_INVARIANTS = _build_row_to_invariants()

# 句単位スイープ（narrow-deletion 一般化テスト）で「無防備」と判定されたが、実際には
# 安全と判断した句。理由を1行で明記する（黙って除外しない・PR #495 codex cold review 指摘）。
#
# **キーは句の文言のみ**（行番号は持たない・#415 PR #496 codex [Should] 是正）。行番号を
# キーに含めると、CLAUDE.md の前方を1行編集するだけで対象行がズレて例外が静かに外れ
# （誤って赤くなる）、あるいは別の行の別の句に誤って適用され（誤って過剰除外する）、
# どちらも気づかれにくい。文言そのものをキーにすることで、その句が現存する限り例外は
# 追従し、句ごと消えれば `test_known_safe_undetected_clauses_are_not_stale` が検出する。
#
# 文言のみをキーにする以上、**同じ句が CLAUDE.md 内で複数回出現すると意図しない行にも
# 適用されてしまう**ため `test_known_safe_undetected_clauses_are_unique` で一意性を強制する
# （行番号を落とした分の担保）。
_KNOWN_SAFE_UNDETECTED_CLAUSES: dict[str, str] = {
    # #415 圧縮（横断契約リストへの表→箇条書き変換）で `|` セル区切りが無くなった
    # ため、コンポーネント名と説明文が同一の「。」区切り句に同居するようになった。
    # 名前 `idiom_autopromote` に含まれる token「autopromote」がこの句を token-bearing
    # 扱いにするが、実際の契約語（凍結中/autopromote()/no-op）は同じ行の第2句に別途
    # 存在するため、この句の単独削除は契約内容の消失にならない（実測: 削除後も
    # `idiom_autopromote_frozen` は green のまま）。
    "- `idiom_autopromote`: confirmed idiom の再発 weak_signal を機械昇格。": (
        "`autopromote` は同じ行の第2句（#379 Step1 で凍結中、`autopromote()` は no-op）に再出現"
    ),
}


@pytest.mark.parametrize(
    "row_idx",
    sorted(_ROW_TO_INVARIANTS),
    ids=lambda i: f"L{i + 1}",
)
def test_narrow_clause_deletion_flags_at_least_one_invariant_per_row(
    row_idx: int, tmp_path: Path
) -> None:
    """完了条件（PR #495 codex cold review [Should]3）: narrow-deletion を全 invariant に
    一般化する parameterized mutation test（REQUIRED_INVARIANTS が対象とする行ごとに1
    ケース）。

    **単純に「この invariant の token を削除したら missing になるか」はトートロジーであり
    検証にならない**（`all_of` は「文中にこの文字列があるか」を見るだけなので、token
    自身をどこかで削除すれば `count()==1` の token は定義上つねに missing 判定される。
    最初の実装はこの誤りを犯し、codex cold review で指摘された）。

    代わりに、行を「|」（テーブルセル区切り）→「。」（文区切り）の順で句に分割し（token
    の中身とは無関係にテキスト構造だけから決まる境界。セル分割が要る理由は
    `_row_clauses` の docstring 参照）、**その行に含まれる token を1つでも含む句を単独で
    削除したとき、その行を対象とする invariant のうち少なくとも1つが赤くなること**を
    検証する。1行に複数 invariant が乗ることもあるため「その行を対象とする invariant の
    集合」全体で判定し、個々の invariant を単独では判定しない——そうしないと『別の
    invariant が守っている句』を誤って無防備と判定する偽陽性が出ることを実測で確認した
    （保護対象フレーズを invariant 単位でなく「行が対象とする invariant 集合」単位に
    決めた理由）。

    `cli_dry_run_default`（token「scaffold_advisory.py」が対象句「CLI は既定 dry-run」とは
    別のセル＝実体列に属し、対象句自体を削除しても実体列の token が生き残るため緑のまま
    だった）は、まさにこの検証方法で機械的に再現できることを実測確認済み（是正前の token
    構成に戻して本テストを実行 → red、是正後に戻して再実行 → green）。

    本テストの実装過程で、`fleet 観測・介入` 行（L53）の `plugins` サブコマンドの
    具体的な検出方式（version 無しプラグインの silent stale を cache↔marketplace source
    の差分で検出）がどの invariant にも保護されていないことが新たに発覚し、
    `fleet_plugins_versionless_stale_diff_detection` として追加した。

    **既知の限界（隠さず明記・過大な主張をしない）**: 本テストは `all_of` に**既に登録
    されている** token を1つでも含む句しか対象にしない。`single_source_file_lock` の
    第2契約句「ロック下からは `_locked` 版を使い自己 deadlock を回避」（元の token
    構成ではどの token にも含まれていなかった）は、この検証方法では**再現できないことを
    実測で確認済み**（token が無い句はテストの候補にすら入らず、静かにスキップされる。
    「トークン未登録の独立した句」を機械的に検出するには、all_of と無関係に行の全句を
    洗い出す静的カバレッジ検証が必要だが、素の全句スイープは実測で 45〜138 件の説明文
    （真の契約でないもの）を誤検出しノイズだらけになったため、本 PR では自動テスト化を
    見送り、人手レビュー（team-lead の実測による発見と是正・記録は PR #495 のコメントに
    全文転記）に留めた）。
    """
    text = claude_md_contract._read_claude_md(_REPO_ROOT)
    assert text is not None
    lines = text.split("\n")

    expected_names = _ROW_TO_INVARIANTS[row_idx]
    tokens: set[str] = set()
    for inv in claude_md_contract.REQUIRED_INVARIANTS:
        if inv.name in expected_names:
            tokens.update(inv.all_of)

    row = lines[row_idx]
    token_bearing_clauses = [c for c in _row_clauses(row) if any(tok in c for tok in tokens)]
    assert token_bearing_clauses, f"L{row_idx + 1}: token を含む句が1つも見つからない"

    uncovered: list[str] = []
    for clause in token_bearing_clauses:
        if clause in _KNOWN_SAFE_UNDETECTED_CLAUSES:
            continue
        mutated_row = row.replace(clause, "", 1)
        mutated_lines = lines[:row_idx] + [mutated_row] + lines[row_idx + 1 :]
        root = tmp_path / f"clause_deleted_{abs(hash(clause))}"
        _write(root / "CLAUDE.md", "\n".join(mutated_lines))
        findings = claude_md_contract.check_claude_md_contracts(root)
        flagged = {f["invariant"] for f in findings}
        if not (flagged & expected_names):
            uncovered.append(clause.strip())

    assert uncovered == [], (
        f"L{row_idx + 1}: 以下の句を単独で削除しても、この行を守るはずの invariant "
        f"{sorted(expected_names)} が1つも反応しなかった（無防備な句。安全と判断済みなら"
        f" _KNOWN_SAFE_UNDETECTED_CLAUSES に理由付きで登録すること）:\n"
        + "\n".join(uncovered)
    )


def test_known_safe_undetected_clauses_are_not_stale() -> None:
    """#415 PR #496 是正: `_KNOWN_SAFE_UNDETECTED_CLAUSES` のキー（句の文言）は実 CLAUDE.md
    のどこかに文字通り出現していなければならない。CLAUDE.md 側の編集で句の文言が変わる/
    消えると、対応する例外は「もう存在しない句を守っている」死んだエントリになり、静かに
    腐る（#415 圧縮で13/14件がこの状態になっていたのが実例）。stale なら削除するか句を
    現行の文言に更新することを促す。
    """
    text = claude_md_contract._read_claude_md(_REPO_ROOT)
    assert text is not None
    stale = [clause for clause in _KNOWN_SAFE_UNDETECTED_CLAUSES if clause not in text]
    assert stale == [], (
        "以下の _KNOWN_SAFE_UNDETECTED_CLAUSES エントリは実 CLAUDE.md に出現しない"
        "（削除するか句を更新すること）:\n" + "\n".join(stale)
    )


def test_known_safe_undetected_clauses_are_unique() -> None:
    """#415 PR #496 是正: キーを行番号から句の文言のみへ変更した代償を埋める検査。行番号を
    持たないため、同じ句が CLAUDE.md 内で複数回出現すると例外が意図しない箇所にも適用され
    （過剰除外）、本来検出すべき無防備な句を見逃す。各エントリの句が本文に厳密に1回だけ
    出現することを強制する。
    """
    text = claude_md_contract._read_claude_md(_REPO_ROOT)
    assert text is not None
    non_unique = {
        clause: text.count(clause)
        for clause in _KNOWN_SAFE_UNDETECTED_CLAUSES
        if text.count(clause) != 1
    }
    assert non_unique == {}, (
        "以下の _KNOWN_SAFE_UNDETECTED_CLAUSES エントリは実 CLAUDE.md に複数回（または0回）"
        f"出現し、句の一意性で行番号の代わりを担保できない: {non_unique}"
    )


def test_empty_required_invariants_makes_check_pass_trivially(
    tmp_path: Path, monkeypatch
) -> None:
    """完了条件2-⑤: REQUIRED_INVARIANTS を空にすると、契約検査は（何も要求しないので）
    常に緑になる。この事実そのものを明示的にテストし、golden（件数）テストだけが
    「空にされたこと」を検出する仕組みであることを保証する。"""
    monkeypatch.setattr(claude_md_contract, "REQUIRED_INVARIANTS", tuple())
    root = tmp_path / "repo"
    _write(root / "CLAUDE.md", "空の本文")
    assert claude_md_contract.check_claude_md_contracts(root) == []
    # golden テスト（test_required_invariants_count_golden）は別途、この空化を検出する。


# --- check_must_stay_sections ---------------------------------------------------


def test_must_stay_sections_pass_on_full_fixture(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "CLAUDE.md", _full_claude_md_text())
    assert claude_md_contract.check_must_stay_sections(root) == []


def test_real_repo_must_stay_sections_present() -> None:
    findings = claude_md_contract.check_must_stay_sections(_REPO_ROOT)
    assert findings == [], findings


def test_missing_compaction_instructions_detected(tmp_path: Path) -> None:
    """完了条件2-②: 必須見出しの削除は赤くなる。"""
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
    assert "store_write_barrier_core" in result["failures"][0]["detail"]


def test_layer2_check_real_repo_clean() -> None:
    result = claude_md_contract.layer2_check(_REPO_ROOT)
    assert result == {"check": "claude_md_contract", "failures": []}


def test_layer2_check_missing_file_is_failure(tmp_path: Path) -> None:
    """完了条件2-③: CLAUDE.md がファイルごと削除されていたら Layer2 は失敗として扱う
    （非該当ではない。fail-open バグの修正・敵対性とは無関係な純粋な正しさの修正なので維持）。
    """
    root = tmp_path / "repo"
    root.mkdir()
    result = claude_md_contract.layer2_check(root)
    assert result["failures"], "CLAUDE.md 削除が失敗として検出されなかった"
    assert any("存在しない" in f["detail"] for f in result["failures"])


def test_layer2_check_unreadable_file_is_failure(tmp_path: Path) -> None:
    """完了条件2-④: CLAUDE.md が読取不能（不正バイト列）なら Layer2 は失敗として扱う。"""
    root = tmp_path / "repo"
    root.mkdir()
    # UTF-8 として不正なバイト列を書き込み、read_text(encoding="utf-8") を失敗させる。
    (root / "CLAUDE.md").write_bytes(b"\xff\xfe\x00\x01broken")
    result = claude_md_contract.layer2_check(root)
    assert result["failures"], "CLAUDE.md 読取不能が失敗として検出されなかった"
    assert any("読み取れない" in f["detail"] for f in result["failures"])


def test_check_claude_md_contracts_no_file_still_generic_empty(tmp_path: Path) -> None:
    """汎用ライブラリ関数（`check_claude_md_contracts`）は CLAUDE.md を持たない他 PJ 向けに
    非該当（空リスト）のまま。missing/unreadable の failure 化は `layer2_check`（この repo 専用の
    blocking 経路）だけの責務。"""
    root = tmp_path / "repo"
    root.mkdir()
    assert claude_md_contract.check_claude_md_contracts(root) == []


def test_dogfood_layer2_wires_claude_md_contract(tmp_path: Path, monkeypatch) -> None:
    """#415 keyset 完全化・Step4④の実測で見つかった穴: `REQUIRED_INVARIANTS` を空にする/
    `_missing_tokens` を無効化する、はいずれも既存テストで赤くなるが、`dogfood/cli.py` の
    `_run_layer2` から `checks.append(claude_md_contract.layer2_check(repo_root))` を
    削除しても検知するテストが1つも無かった（`checks.append(...)` 行を実際にコメントアウト
    して `pytest -k dogfood` を実行 → 120件全緑のまま。#415 オーケストレーター実測）。

    最初の版は `inspect.getsource` によるソース文字列の substring 検査だったが、これは
    「呼び出しをコメントアウトしても通る」欠陥がある（部分文字列としてはコメント内に
    残り続けるため。実測: `# checks.append(claude_md_contract.layer2_check(repo_root))`
    のようにコメントアウトすると substring 検査は緑のまま）。codex cold review 指摘
    （2026-08-17・PR #495）を受け、`layer2_check` を monkeypatch で差し替え、
    `_run_layer2` が実行時に**実際に呼ぶこと**を動的に検証する形へ置き換えた。
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "CLAUDE.md").write_text("dummy", encoding="utf-8")
    result_path = tmp_path / "result.json"
    result_path.write_text("{}", encoding="utf-8")
    out_dir = tmp_path / "out"

    calls: list[Path] = []

    def fake_layer2_check(repo_root_arg: Path) -> dict:
        calls.append(repo_root_arg)
        return {"check": "claude_md_contract", "failures": []}

    monkeypatch.setattr(claude_md_contract, "layer2_check", fake_layer2_check)

    result = dogfood_cli._run_layer2(repo_root, out_dir, result_path)

    assert calls == [repo_root], (
        "claude_md_contract.layer2_check が _run_layer2 から呼ばれなかった"
        "（CLAUDE.md 契約検査が dogfood gate の配線から外れている）"
    )
    assert any(c.get("check") == "claude_md_contract" for c in result["checks"]), (
        "claude_md_contract の結果が _run_layer2 の checks に含まれていない"
    )


# --- 完了条件3: 陽性対照（緑のまま） ------------------------------------------------


def test_positive_control_unmodified_stays_green() -> None:
    """①無改変。"""
    assert claude_md_contract.check_claude_md_contracts(_REPO_ROOT) == []
    assert claude_md_contract.check_must_stay_sections(_REPO_ROOT) == []


def test_positive_control_trailing_whitespace_stays_green(tmp_path: Path) -> None:
    """②各行末に半角スペースを付与しても（圧縮 PR の diff ノイズとして典型）緑のまま。"""
    root = tmp_path / "repo"
    text = "\n".join(line + "  " for line in _full_claude_md_text().split("\n"))
    _write(root / "CLAUDE.md", text)
    assert claude_md_contract.check_claude_md_contracts(root) == []
    assert claude_md_contract.check_must_stay_sections(root) == []


def test_positive_control_crlf_stays_green(tmp_path: Path) -> None:
    """③改行コードが CRLF（Windows 由来の圧縮 PR で起こりうる）でも緑のまま。"""
    root = tmp_path / "repo"
    text = _full_claude_md_text().replace("\n", "\r\n")
    _write(root / "CLAUDE.md", text)
    assert claude_md_contract.check_claude_md_contracts(root) == []
    assert claude_md_contract.check_must_stay_sections(root) == []


def test_positive_control_visible_strong_emphasis_stays_green(tmp_path: Path) -> None:
    """④可視の `<strong>`（Markdown の `**...**` 相当を HTML タグで書いた場合）で契約文を
    囲んでも、単なる部分文字列一致なので誤検出しない（前巡で層を積んだ実装ではここが
    false red になっていたことが撤去の決め手の1つだった）。
    """
    root = tmp_path / "repo"
    text = _full_claude_md_text().replace(
        "適用は必ず人間の y/n（無人適用しない）。",
        "<strong>適用は必ず人間の y/n（無人適用しない）。</strong>",
    )
    _write(root / "CLAUDE.md", text)
    assert claude_md_contract.check_claude_md_contracts(root) == []


def test_positive_control_deeply_nested_quote_does_not_crash(tmp_path: Path) -> None:
    """⑤`>` を1000回重ねた深い引用でもクラッシュしない（前巡の引用再帰実装は
    `RecursionError` を起こしていた。本版は部分文字列一致のみなので構造的に発生しない）。
    """
    root = tmp_path / "repo"
    deep_quote = ("> " * 1000) + "契約はここにあります。"
    text = _full_claude_md_text() + "\n\n" + deep_quote + "\n"
    _write(root / "CLAUDE.md", text)
    # クラッシュしないことが主目的。契約は全て満たされているので緑のまま。
    assert claude_md_contract.check_claude_md_contracts(root) == []
    assert claude_md_contract.check_must_stay_sections(root) == []
