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

import sys
from pathlib import Path

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
REQUIRED_INVARIANTS_COUNT = 75


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _full_claude_md_text() -> str:
    """REQUIRED_INVARIANTS 全27件 + MUST_STAY_SECTIONS + Agent contract header を満たす
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
        "| slug | pj_slug が単一ソース。worktree slug 食い違いを防止 |",
        "| lock | file_lock が単一ソース |",
        "| channels | review_channels が単一ソース |",
        "| raw_history | raw history read は allowlist に固定。業務 reader は"
        " `load_effective_history`。 |",
        "| icebox_notice | fail-open で既存ファイル非破壊 |",
        "| cli | CLI は既定 dry-run。scaffold_advisory は builder stub 生成 |",
        "| general | fleet 観測・介入は env_score / 導入状況を一覧表示。決定論・LLM 非依存 |",
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
        "| dogfood | `--layer light` は pre-push で非ブロッキング自動実行 | scripts/lib/dogfood/ |",
        "| weak_signals_drain | pending marker の dry-run 書込は意図された設計（消さない） | weak_signals/batch.py |",
        "| reconcile_surfaced | phases の dry-run は `persist=False` で非書込 | cli.py |",
        "| idiom_filter | idiom 単位拒否も可能 | correction_semantic/idiom_filter.py |",
        "| recall_ranking | stale/superseded memory を validity metadata で降格（ハード除外はしない） | fleet/recall.py |",
        "| subagents_errors | is_noise_agent_type が単一ソース | rl_common/detection.py |",
        "| memory_capability | resolve_cc_memory_dir が単一ソース | scripts/lib/memory_capability.py |",
        "| skill_vuln_scan | combo 必須で検出 | skill_vuln_scan.py |",
        "| daily | 適用は対話で人間承認 | scripts/lib/daily/ |",
        "| memory_hygiene | 重複残骸は手順提案のみで auto-apply しない | memory_dup_residue.py |",
        "| invalid_frontmatter | auto-fix せず人手修正提案 | frontmatter.py |",
        "| evolve_tier | sync は既定 dry-run、`--apply` のみ書込 | bin/evolve-tier |",
        "| evaluation_provenance | 不明値は推測せず None | scripts/lib/evaluation_provenance.py |",
        "| fleet_propose | reject 済み提案は再提示しない | fleet/propose.py |",
        "| codex_usage | advisory 表示（fail-open）。CC 側 token_usage とは合算しない | fleet/codex_usage.py |",
        "| evolve_revert | entry_id は戦果ボードか --list が印字する | bin/evolve-revert |",
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
    """完了条件2-①: REQUIRED_INVARIANTS 全27件を1つずつ、必須語を1つ抜いて欠落させ、
    その不変条件だけが検出されることを確認する（他の不変条件が巻き添えで検出されないこと）。
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
    """完了条件2（外部 cold review 欠陥1・行単位の変異）: REQUIRED_INVARIANTS 全27件について、
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


def test_dogfood_layer2_wires_claude_md_contract() -> None:
    """#415 keyset 完全化・Step4④の実測で見つかった穴: `REQUIRED_INVARIANTS` を空にする/
    `_missing_tokens` を無効化する、はいずれも既存テストで赤くなるが、`dogfood/cli.py` の
    `_run_layer2` から `checks.append(claude_md_contract.layer2_check(repo_root))` を
    削除しても検知するテストが1つも無かった（`checks.append(...)` 行を実際にコメントアウト
    して `pytest -k dogfood` を実行 → 120件全緑のまま。#415 オーケストレーター実測）。
    静的ソース検査で配線の消失を検出する（`_run_layer2` の実行を伴わない軽量ガード）。
    """
    import inspect

    src = inspect.getsource(dogfood_cli._run_layer2)
    assert "claude_md_contract.layer2_check(repo_root)" in src, (
        "dogfood/cli.py の _run_layer2 から claude_md_contract.layer2_check の呼び出しが"
        "消えている（CLAUDE.md 契約検査が dogfood gate から外れた）"
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
