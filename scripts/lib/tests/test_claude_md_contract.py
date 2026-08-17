"""claude_md_contract（CLAUDE.md 契約不変条件の決定論検査）のテスト（#415 / #492）。

決定論・LLM 非依存。合成 fixture に加え、実 repo の CLAUDE.md に対しても検査する
（PR #416 の再発防止＝「圧縮で契約が hot から消えたら赤くなる」ことを保証するテストなので、
実ファイルへの検査を外すと本来の目的を検証できない）。

PR #492 codex cold review [Must]5 を受けて、以下を軸ごとに測定する:
  A. 語を消す（既存） / B. 語を残して意味を壊す（否定・退避・分散・宣言攻撃）/
  C. 守衛そのものを殺す（ファイル削除・読取不能）/ D. 追加した5契約の個別検出力 /
  E. 正当な言い換えで緑のままであること（誤検知較正）。
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
REQUIRED_INVARIANTS_COUNT = 27


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _full_claude_md_text() -> str:
    """REQUIRED_INVARIANTS 全27件 + MUST_STAY_SECTIONS + Agent contract header を、実 CLAUDE.md
    と同じ構造単位（引用ブロック/リスト項目/段落/表行/見出し）で満たす合成本文を組み立てる。

    各 invariant の all_of は、実 CLAUDE.md 本文と同様に**同一の単位内**へ意図的に配置する
    （単位をまたいでは満たされないことを他のテストで検証する）。
    """
    lines = [
        "# evolve-anything Plugin",
        "",
        "> **Agent contract:** 作業開始前に",
        "> [`docs/agent-contract/policy.md`](docs/agent-contract/policy.md) を全文読むこと。",
        "",
        "## 目指すユーザー体験（全機能の判断基準）",
        "",
        "**到達状況の数値をこのファイルに書かない**。日付付きスナップショットを正典に置くと必ず腐る。",
        "",
        "4. **信頼**: 適用は必ず人間の y/n（無人適用しない）/",
        "   skill 採用は1コマンドで戻せる（**適用範囲: evolve drain 経由の新規採用のみ**。optimize.py 経路と",
        "   evolve-loop 経路は revert 対象外。凍結した。",
        "",
        "**新設凍結**: 新 store / observability section / advisory proposal adapter /",
        " weak_signal channel の追加は停止する。",
        "",
        "**表示淘汰**: 淘汰した事実は `display_cull` の 1 行 meta として必ず surface する（silence != evaluated）。",
        "",
        "**契約フラグを省略してよいかの判断基準**（コンポーネント単位でなく不変条件単位で判定する）:",
        " 抜け道が1つでもある不変条件は hot に必ず残す（例: `store_write` barrier 自身の",
        " 未登録ストア reject も env `EVOLVE_WRITE_GUARD=warn` で降格できるため対象外／",
        " 関数の単一ソース・TTL の read 時導出・dry-run 純度）。",
        "",
        "| コンポーネント | 一言サマリ | 実体 |",
        "|---|---|---|",
        "| `store_write` write barrier | 全ストア書込の単一ゲート。既定 reject、registry 不在は fail-open（例外口 `store_write_raw`） | `rl_common/store_write.py` |",
        "| `weak_signals` | 45日 TTL は read 時 age 導出で writer-death 非依存 | `weak_signals/` |",
        "| optimize_history の effective view | revert 済み accept を判断母集団から畳む `fold_effective` が単一ソース。業務 reader は `load_effective_history`、raw は allowlist 3件のみ | `optimize_history_store.py` |",
        "| `file_lock` | ファイル単位排他ロックと atomic write の単一ソース | `rl_common/file_lock.py` |",
        "| `review_channels` | y/n 確認に出す weak チャネルの単一ソース | `correction_semantic/review_channels.py` |",
        "| `pj_slug` | PJ slug 導出の単一ソース | `pj_slug.py` |",
        "| `evolve_revert` | 3分岐（normal/冪等/conflict）で conflict は上書きせず中止、CLI は既定 dry-run・`--apply` のみ実書込 | `evolve_revert/` |",
        "| **fleet 観測・介入** | 全 PJ 横断で env_score / 導入状況を一覧表示。決定論・LLM 非依存 | `bin/evolve-fleet` |",
        "| 後片付け | cleanup | 候補提示→個別承認→実行。tmp dir default prefix は `evolve-anything-` のみに安全側限定 | `cleanup` |",
        "| モデルティア変更 | tier | `sync` の dry-run diff を全件提示 → **明示承認後にのみ** `sync --apply` | `bin/evolve-tier` |",
        "| `judge_runner` / `safe_llm_call` | 無人呼び出しは `safe_llm_call` に一点集約し費用は呼び出し直前に事前予約 | `correction_semantic/judge_runner.py` |",
        "| `auto_memory_runner/broker` | project スコープ4層防御で他PJ混入を reject | `auto_memory_*.py` |",
        "| `idiom_autopromote` | confirmed idiom の再発 weak_signal を機械昇格。**#379 Step1 で凍結中、`autopromote()` は no-op** | `correction_semantic/idiom_autopromote.py` |",
        "| `runtime_telemetry` | usage/sessions/errors の hook record に runtime を較正追加。**Codex hook 配線は保留** | `hooks/common.py` |",
        "| `memory_guard` | prompt_injection/secret_exfil を reject（検査失敗は fail-open）。同名エントリの上書きは決定論遷移検証でゲート | `memory_guard.py` |",
        "| `fleet_pr` | 承認済み evolve 提案を repo 外 worktree で commit→push→PR 化。path allowlist・push account guard で強制、マージは人間 | `fleet/pr.py` |",
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


# --- check_claude_md_contracts: A系（語を消す） -----------------------------------


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
    assert names == {"store_write_barrier_core"}
    assert "既定 reject" in findings[0]["missing"]


def test_each_invariant_flagged_independently_when_its_token_removed(tmp_path: Path) -> None:
    """REQUIRED_INVARIANTS を1つずつ、必須語を1つ抜いて欠落させ、その不変条件が検出される
    ことを確認する（#492 D系: 追加した5契約を含む全27件を1件ずつ検証）。
    """
    base_text = _full_claude_md_text()
    for inv in claude_md_contract.REQUIRED_INVARIANTS:
        token = inv.all_of[0]
        assert token in base_text, f"fixture is missing required token {token!r} for {inv.name}"
        # 出現が複数ある語（例: file_lock は cell1/cell3 双方に現れる）を確実に欠落させるため
        # 全出現を除去する（先頭1件だけ抜くと別出現が残り検出できないケースがある）。
        mutated = base_text.replace(token, "")
        root = tmp_path / f"repo_{inv.name}"
        _write(root / "CLAUDE.md", mutated)
        findings = claude_md_contract.check_claude_md_contracts(root)
        names = {f["invariant"] for f in findings}
        assert inv.name in names, f"removing {token!r} did not flag {inv.name}"


def test_required_invariants_count_golden() -> None:
    assert len(claude_md_contract.REQUIRED_INVARIANTS) == REQUIRED_INVARIANTS_COUNT


# --- B系: 語を残して意味を壊す（否定・退避・分散・宣言攻撃） -----------------------


def test_negation_form_flags_invariant(tmp_path: Path) -> None:
    """B1: 「単一ゲートではない。既定 reject しない。」のような直後否定は満たした扱いにしない
    （codex cold review 実演の再現）。
    """
    root = tmp_path / "repo"
    text = _full_claude_md_text().replace(
        "全ストア書込の単一ゲート。既定 reject、registry 不在は fail-open（例外口 `store_write_raw`）",
        "全ストア書込の単一ゲートではない。既定 reject しない、registry 不在は fail-open（例外口 `store_write_raw`）",
    )
    _write(root / "CLAUDE.md", text)
    findings = claude_md_contract.check_claude_md_contracts(root)
    names = {f["invariant"] for f in findings}
    assert "store_write_barrier_core" in names


def test_token_hidden_in_code_block_flags_invariant(tmp_path: Path) -> None:
    """B2: 語をコードブロックへ退避させても「本文に残っている」扱いにしない。"""
    root = tmp_path / "repo"
    text = _full_claude_md_text().replace("display_cull", "")
    text += "\n\n```\n旧仕様の参考: display_cull は silence != evaluated と組み合わせて使っていた\n```\n"
    _write(root / "CLAUDE.md", text)
    findings = claude_md_contract.check_claude_md_contracts(root)
    names = {f["invariant"] for f in findings}
    assert "display_cull_surface" in names


def test_token_hidden_in_html_comment_flags_invariant(tmp_path: Path) -> None:
    """B3: 語を HTML コメントへ退避させても「本文に残っている」扱いにしない。"""
    root = tmp_path / "repo"
    text = _full_claude_md_text().replace("safe_llm_call", "")
    text += "\n\n<!-- 旧仕様: 無人呼び出しは safe_llm_call に一点集約し事前予約していた -->\n"
    _write(root / "CLAUDE.md", text)
    findings = claude_md_contract.check_claude_md_contracts(root)
    names = {f["invariant"] for f in findings}
    assert "safe_llm_call" in names


def test_negated_quote_block_flags_invariant(tmp_path: Path) -> None:
    """B4: 語を引用ブロック内の「旧仕様」記述へ退避させても満たした扱いにしない。

    引用ブロックは完全除外ではなく（Agent contract header が正規に引用ブロック内にあるため）、
    同じ否定検出を適用する。
    """
    root = tmp_path / "repo"
    text = _full_claude_md_text().replace(
        "project スコープ4層防御で他PJ混入を reject",
        "auto-memory 書込境界のスコープ設計は別紙参照",
    )
    text += "\n\n> 旧仕様: project スコープ4層防御で他PJ混入を reject していた\n"
    _write(root / "CLAUDE.md", text)
    findings = claude_md_contract.check_claude_md_contracts(root)
    names = {f["invariant"] for f in findings}
    assert "memory_project_scope" in names


def test_scattered_tokens_across_rows_flags_invariant(tmp_path: Path) -> None:
    """B5: all_of の語を別々の表行・別々の段落へ分散させると（1つの単位に収まらないため）
    満たした扱いにしない。
    """
    root = tmp_path / "repo"
    text = _full_claude_md_text().replace(
        "| `store_write` write barrier | 全ストア書込の単一ゲート。既定 reject、registry 不在は fail-open（例外口 `store_write_raw`） | `rl_common/store_write.py` |",
        "\n".join(
            [
                "| `store_write` write barrier A | 全ストア書込の単一ゲート | `rl_common/store_write.py` |",
                "| `store_write` write barrier B | 既定 reject する | `rl_common/store_write.py` |",
                "| `store_write` write barrier C | fail-open で降格 | `rl_common/store_write.py` |",
                "| `store_write` write barrier D | 例外口は store_write_raw | `rl_common/store_write.py` |",
            ]
        ),
    )
    _write(root / "CLAUDE.md", text)
    findings = claude_md_contract.check_claude_md_contracts(root)
    names = {f["invariant"] for f in findings}
    assert "store_write_barrier_core" in names
    finding = next(f for f in findings if f["invariant"] == "store_write_barrier_core")
    # 全4語はどこかに存在するので missing は空、reason は "同一単位に揃っていない"。
    assert finding["missing"] == []
    assert finding["reason"] == "not_colocated"


def test_declare_all_contracts_deprecated_and_dump_in_code_block(tmp_path: Path) -> None:
    """B6: 「以下の契約はすべて廃止。以下は無効な旧語」と宣言した上で、全 all_of と見出しを
    コードブロックへ列挙する攻撃（codex cold review の実演そのもの）を再現する。
    """
    root = tmp_path / "repo"
    dumped_tokens = "\n".join(
        tok for inv in claude_md_contract.REQUIRED_INVARIANTS for tok in inv.all_of
    )
    dumped_headings = "\n".join(claude_md_contract.MUST_STAY_SECTIONS)
    text = (
        "# evolve-anything Plugin\n\n"
        "以下の契約はすべて廃止。以下は無効な旧語:\n\n"
        "```\n"
        f"{dumped_tokens}\n{dumped_headings}\n"
        "```\n"
    )
    _write(root / "CLAUDE.md", text)
    contract_findings = claude_md_contract.check_claude_md_contracts(root)
    section_findings = claude_md_contract.check_must_stay_sections(root)
    # 実演どおりであれば両方とも空（=検出漏れ）になるはずの攻撃。ここでは両方とも赤くなること。
    assert len(contract_findings) == len(claude_md_contract.REQUIRED_INVARIANTS)
    assert len(section_findings) >= len(claude_md_contract.MUST_STAY_SECTIONS)


# --- C系: 守衛そのものを殺す ------------------------------------------------------


def test_layer2_check_missing_file_is_failure(tmp_path: Path) -> None:
    """C1: CLAUDE.md がファイルごと削除されていたら Layer2 は失敗として扱う（非該当ではない）。"""
    root = tmp_path / "repo"
    root.mkdir()
    result = claude_md_contract.layer2_check(root)
    assert result["failures"], "CLAUDE.md 削除が失敗として検出されなかった"
    assert any("存在しない" in f["detail"] for f in result["failures"])


def test_layer2_check_unreadable_file_is_failure(tmp_path: Path) -> None:
    """C2: CLAUDE.md が読取不能（不正バイト列）なら Layer2 は失敗として扱う。"""
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


# --- E系: 正当な言い換えは緑のまま（誤検知較正） -----------------------------------


def test_legitimate_reorder_within_unit_stays_green(tmp_path: Path) -> None:
    """E1: 同一単位内で語順を入れ替えるだけの言い換えは緑のまま。"""
    root = tmp_path / "repo"
    text = _full_claude_md_text().replace(
        "| `store_write` write barrier | 全ストア書込の単一ゲート。既定 reject、registry 不在は fail-open（例外口 `store_write_raw`） | `rl_common/store_write.py` |",
        "| `store_write` write barrier | registry 不在は fail-open（例外口 `store_write_raw`）、全ストア書込の単一ゲート。既定 reject | `rl_common/store_write.py` |",
    )
    _write(root / "CLAUDE.md", text)
    assert claude_md_contract.check_claude_md_contracts(root) == []


def test_legitimate_trim_filler_text_stays_green(tmp_path: Path) -> None:
    """E2: 契約語に触れない冗長な修飾の削除（圧縮の典型例）は緑のまま。"""
    root = tmp_path / "repo"
    text = _full_claude_md_text().replace(
        "**到達状況の数値をこのファイルに書かない**。日付付きスナップショットを正典に置くと必ず腐る。",
        "**到達状況の数値をこのファイルに書かない**。",
    )
    _write(root / "CLAUDE.md", text)
    assert claude_md_contract.check_claude_md_contracts(root) == []


def test_legitimate_merge_wrapped_lines_stays_green(tmp_path: Path) -> None:
    """E3: 折り返された2行を1行へ畳む（表現の重複を1つに畳む）圧縮は緑のまま。"""
    root = tmp_path / "repo"
    text = _full_claude_md_text().replace(
        "**新設凍結**: 新 store / observability section / advisory proposal adapter /\n"
        " weak_signal channel の追加は停止する。",
        "**新設凍結**: 新 store / observability section / advisory proposal adapter / weak_signal channel の追加は停止する。",
    )
    _write(root / "CLAUDE.md", text)
    assert claude_md_contract.check_claude_md_contracts(root) == []


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
    assert "store_write_barrier_core" in result["failures"][0]["detail"]


def test_layer2_check_real_repo_clean() -> None:
    result = claude_md_contract.layer2_check(_REPO_ROOT)
    assert result == {"check": "claude_md_contract", "failures": []}
