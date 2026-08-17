"""claude_md_contract.py — CLAUDE.md 契約不変条件の決定論検査（#415）。

背景: CLAUDE.md（毎セッション全文がコンテキストに載る hot ドキュメント）を圧縮したい。
だが過去の圧縮（PR #416）で **契約が hot から消える事故** が起き、「契約フラグ6行を復元」
「契約落ち4行を是正」「dry-run 既定を復元」等の追加修正が4本必要になった。

既存の `doc_budget.py` は byte 予算・セクション予算・リンク実在しか検査しておらず、契約
文言が生き残ったかは一切見ない。したがって圧縮で契約が消えても doc_budget は緑のまま通る。

## 検査モデル（PR #492 codex cold review [Must]1 を受けて全面設計変更）

初版は「全文 substring 一致」だったため、以下の攻撃で全検査を無警告で通過できた
（2026-08-16 codex 実演）:
  - 契約語をコードブロック/HTMLコメントへ退避（本文では死んでいるが文字列としては残る）
  - 「単一ゲートではない。既定 reject しない。」のような否定形への書き換え

これを塞ぐため、以下の**構造的封じ込め**へ変更した:
  1. コードブロック（``` フェンス）・HTMLコメント（`<!-- -->`）は検査対象から**完全除外**
     （中身がどうであれ、これらは CLAUDE.md 本文として実効しないため）。
  2. 本文を「単位」（表の1行 / リスト項目（継続行込み）/ 段落 / 引用ブロック / 見出し行）に
     分割する。`Invariant.all_of` の全語は**同一の単位内**で揃っていなければ満たしたことにならない
     （語を別々の表行・別々の段落に分散させる攻撃を防ぐ）。
  3. 語の直後（空白・句読点等を挟んでよい）に活用形否定（`ではない`/`でない`/`しない`）が
     続く場合、または語の直前にラベル否定（`廃止`/`無効`/`やめた`/`旧仕様`）が置かれている
     場合、その出現は「満たした」扱いにしない（同じ語の**他の出現**が別の場所で有効なら、
     そちらで満たされる）。

引用ブロック（`>`）は完全除外しない — 本 repo の Agent contract ヘッダ（冒頭の
`docs/agent-contract/policy.md` 参照）自体が正規に `>` の中にあるため、除外すると本物の契約が
偽陽性になる。代わりに上記(2)(3)の単位＋否定検査を引用ブロックにも同様に適用することで、
「旧仕様をブロッククォートに隠す」攻撃は（3)の否定語検出で捕捉する。

否定語リストに `対象外` は**含めない**。理由: 本 repo の CLAUDE.md 本文（コンポーネント表の
直前の段落）に「`store_write` barrier 自身の... 対象外」という正当な用法が実在し
（契約テンプレ免除基準の説明であって契約の失効宣言ではない）、これを否定語にすると
`store_write_barrier_downgrade` invariant が実データで恒常的に偽陽性になる（2026-08-17 実測）。
`しない` も同様の理由で「語の直後」限定にしている — `無人適用しない` のように否定形自体が
正しい契約文言であるケースが実在するため、単位全体を否定語の有無で判定すると自己矛盾する。

LLM を使わない。`all_of` の語自体は素の部分文字列一致（正規表現不使用 — 正規表現は書き手が誤り、
静かに常時 True になりやすいため）。単位分割（見出し/表行/リスト/引用の判定）のみ最小限の
`str.startswith` 系判定を使う。

判定は「その不変条件の必須語（`all_of`）が、いずれか1つの単位内に、否定されない形で全て
含まれるか」。`REQUIRED_INVARIANTS` の各語は着手時点（2026-08-17）の CLAUDE.md 本文に実在する
ことを grep で確認済み。

`MUST_STAY_SECTIONS` は圧縮時に別ファイルへ移設してはいけないセクション（例:
`## Compaction Instructions` は harness が compaction 時に読むため、移した瞬間に機能死する）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple


@dataclass(frozen=True)
class Invariant:
    """1つの不変条件。`name` は識別子、`all_of` は同一単位内に全て含まれるべき語のタプル。"""

    name: str
    all_of: Tuple[str, ...]


# 単一ソース。着手時点の CLAUDE.md 本文に各語が実在し、かつ同一単位（表の1行/1段落/1リスト項目）
# 内に共存することを grep + 目視で確認済み（2026-08-17）。
# 件数は golden（test_claude_md_contract.py の REQUIRED_INVARIANTS_COUNT）で守られている。
# 減らす/増やす場合はテスト側の golden も同時に更新すること。
#
# #415 初版の `store_write_barrier`（5語）と `single_source_functions`（4語: fold_effective /
# pj_slug / file_lock / review_channels）は、実際の CLAUDE.md 本文では語が複数の表行・段落に
# **分散**しており、単位ロック方式では同時に満たせない（分散していること自体は正当 — 各コン
# ポーネントの単一ソース性は別々の表行で個別に主張されている）。codex cold review [Must]1 を
# 反映してこの2件は分割した（下記コメント参照）。
REQUIRED_INVARIANTS: Tuple[Invariant, ...] = (
    # store_write_barrier は元は1つだったが、EVOLVE_WRITE_GUARD=warn による降格経路の記述は
    # 別段落（コンポーネント表の直前、契約フラグ省略基準の具体例）にあり同一単位にならないため
    # core（表行本体）と downgrade（降格経路の言及）に分割。
    Invariant(
        "store_write_barrier_core",
        all_of=("単一ゲート", "既定 reject", "fail-open", "store_write_raw"),
    ),
    Invariant("store_write_barrier_downgrade", all_of=("EVOLVE_WRITE_GUARD=warn",)),
    Invariant("dry_run_purity", all_of=("dry-run 純度",)),
    Invariant("ttl_read_time", all_of=("read 時 age", "writer-death")),
    # single_source_functions（元は1つの4語 all_of）は4コンポーネントの単一ソース性を
    # それぞれ別の表行で個別に主張しているため、コンポーネントごとに分割。
    Invariant("single_source_fold_effective", all_of=("fold_effective", "単一ソース")),
    Invariant("single_source_pj_slug", all_of=("pj_slug", "単一ソース")),
    Invariant("single_source_file_lock", all_of=("file_lock", "単一ソース")),
    Invariant("single_source_review_channels", all_of=("review_channels", "単一ソース")),
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
# 持たず引用ブロック（`>`）内にあるため MUST_STAY_SECTIONS と別に検査する。
_AGENT_CONTRACT_HEADER_TOKEN = "docs/agent-contract/policy.md"

# 語の直後（文法的に活用が続く形: 「Xではない」「X しない」）に続くと否定とみなす語。
_NEGATION_SUFFIXES: Tuple[str, ...] = ("ではない", "でない", "しない")

# 語の直前にラベルとして置かれる（「旧仕様: X」「廃止: X」）と否定/失効とみなす語。
# `対象外` は含めない — 本 repo の CLAUDE.md 実文に契約免除基準の説明として正当な用法があり
# 誤検知するため（モジュール docstring 参照）。
_NEGATION_LABELS: Tuple[str, ...] = ("廃止", "無効", "やめた", "旧仕様")

# 否定語チェック前に読み飛ばしてよい区切り文字（空白・句読点・強調記号など）。
_STRIP_CHARS = " 　。、）(*：:・/"

# 直前/直後の否定語探索窓（文字数）。長すぎると単位内の無関係な語まで拾って誤検知するため
# 「Xではない」のような直結表現・「旧仕様: X」のような直前ラベルだけを狙う短い窓にする。
_NEGATION_WINDOW = 16


def _read_claude_md(repo_root: Path) -> str | None:
    claude_md = Path(repo_root) / "CLAUDE.md"
    if not claude_md.is_file():
        return None
    try:
        return claude_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _strip_fenced_and_comments(text: str) -> str:
    """フェンスドコードブロック（``` ）と HTML コメント（<!-- --> ）を完全除外する。

    行単位の状態機械。除外した中身は検査に一切残らない（B2/B3 攻撃対策）。
    """
    lines = text.split("\n")
    out: List[str] = []
    in_fence = False
    in_comment = False
    for line in lines:
        if in_comment:
            if "-->" in line:
                in_comment = False
                after = line.split("-->", 1)[1]
                if after.strip():
                    out.append(after)
            continue
        if in_fence:
            if line.strip().startswith("```"):
                in_fence = False
            continue
        if line.strip().startswith("```"):
            in_fence = True
            continue
        if "<!--" in line:
            before, _, rest = line.partition("<!--")
            if "-->" in rest:
                after = rest.split("-->", 1)[1]
                merged = before + after
                if merged.strip():
                    out.append(merged)
            else:
                in_comment = True
                if before.strip():
                    out.append(before)
            continue
        out.append(line)
    return "\n".join(out)


def _is_list_start(line: str) -> bool:
    s = line.lstrip()
    if s.startswith(("- ", "* ", "+ ")):
        return True
    j = 0
    while j < len(s) and s[j].isdigit():
        j += 1
    return j > 0 and s[j : j + 2] == ". "


def _extract_units(text: str) -> List[str]:
    """本文を「単位」（表行/リスト項目/段落/引用ブロック/見出し行）へ分割する。

    `all_of` は同一の単位内に揃っていることを要求する（語を別の表行・別の段落へ分散させる
    攻撃＝ B5 対策）。リスト項目・段落は継続行（次のブロック開始まで）を1単位にまとめる。
    """
    lines = text.split("\n")
    n = len(lines)
    units: List[str] = []
    i = 0

    def is_block_start(line: str) -> bool:
        s = line.lstrip()
        return s.startswith(("#", "|", ">")) or _is_list_start(line)

    while i < n:
        line = lines[i]
        if line.strip() == "":
            i += 1
            continue
        stripped = line.lstrip()
        if stripped.startswith("#") or stripped.startswith("|"):
            units.append(line)
            i += 1
            continue
        if stripped.startswith(">"):
            buf: List[str] = []
            while i < n and lines[i].lstrip().startswith(">"):
                buf.append(lines[i].lstrip()[1:].strip())
                i += 1
            units.append(" ".join(buf))
            continue
        # リスト項目・段落: 継続行（空行/新ブロック開始まで）を1単位にまとめる。
        buf = [line]
        i += 1
        while i < n and lines[i].strip() != "" and not is_block_start(lines[i]):
            buf.append(lines[i])
            i += 1
        units.append(" ".join(buf))
    return units


def _is_negated(unit: str, start_idx: int, end_idx: int) -> bool:
    """token の出現が否定/失効されているか。直後の活用形否定と直前のラベル否定の両方を見る。"""
    tail = unit[end_idx : end_idx + _NEGATION_WINDOW].lstrip(_STRIP_CHARS)
    if tail.startswith(_NEGATION_SUFFIXES):
        return True
    head = unit[max(0, start_idx - _NEGATION_WINDOW) : start_idx].rstrip(_STRIP_CHARS)
    return head.endswith(_NEGATION_LABELS)


def _token_present_in_unit(unit: str, token: str) -> bool:
    """token が unit 内に、否定されない形で（他の出現も含めて）存在するか。"""
    start = 0
    while True:
        idx = unit.find(token, start)
        if idx == -1:
            return False
        end = idx + len(token)
        if not _is_negated(unit, idx, end):
            return True
        start = end


def _token_present_anywhere(units: List[str], token: str) -> bool:
    return any(_token_present_in_unit(u, token) for u in units)


def _invariant_satisfied(units: List[str], invariant: Invariant) -> bool:
    return any(all(_token_present_in_unit(u, tok) for tok in invariant.all_of) for u in units)


def _check_contracts_in_text(text: str) -> List[Dict[str, Any]]:
    units = _extract_units(_strip_fenced_and_comments(text))
    findings: List[Dict[str, Any]] = []
    for inv in REQUIRED_INVARIANTS:
        if _invariant_satisfied(units, inv):
            continue
        missing = [tok for tok in inv.all_of if not _token_present_anywhere(units, tok)]
        # missing が空 = 全語はどこかに存在するが同一単位に揃っていない（分散攻撃 B5、
        # または正当な理由なく分割されてしまった編集）。
        reason = "missing_tokens" if missing else "not_colocated"
        findings.append({"invariant": inv.name, "missing": missing, "reason": reason})
    return findings


def _check_sections_in_text(text: str) -> List[Dict[str, str]]:
    units = _extract_units(_strip_fenced_and_comments(text))
    findings: List[Dict[str, str]] = []
    for heading in MUST_STAY_SECTIONS:
        if not _token_present_anywhere(units, heading):
            findings.append({"section": heading, "reason": "missing_heading"})
    if not _token_present_anywhere(units, _AGENT_CONTRACT_HEADER_TOKEN):
        findings.append({"section": "Agent contract header", "reason": "missing_reference"})
    return findings


def check_claude_md_contracts(repo_root: Path) -> List[Dict[str, Any]]:
    """欠落した不変条件を `[{"invariant": name, "missing": [tok, ...], "reason": ...}]` で返す。

    汎用ライブラリとしての挙動: CLAUDE.md が無い/読めない PJ では非該当（空リスト）。
    **この repo を検査する `layer2_check` は別途 CLAUDE.md の存在を必須にする**（missing/unreadable
    を failure 扱いにする。codex cold review [Must]2）。
    """
    text = _read_claude_md(repo_root)
    if text is None:
        return []
    return _check_contracts_in_text(text)


def check_must_stay_sections(repo_root: Path) -> List[Dict[str, str]]:
    """移設禁止セクションが欠落していないかを検査する。CLAUDE.md が無い PJ では非該当。"""
    text = _read_claude_md(repo_root)
    if text is None:
        return []
    return _check_sections_in_text(text)


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
    for finding in _check_contracts_in_text(text):
        detail = (
            f"invariant '{finding['invariant']}' ({finding['reason']}): "
            f"missing tokens: {finding['missing']}"
        )
        failures.append({"check": "claude_md_contract", "detail": detail})
    for finding in _check_sections_in_text(text):
        failures.append(
            {
                "check": "claude_md_contract",
                "detail": f"must-stay section missing: {finding['section']} ({finding['reason']})",
            }
        )
    return {"check": "claude_md_contract", "failures": failures}
