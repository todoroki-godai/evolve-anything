"""Orphan store（writer あり reader なし jsonl）の observability セクション生成（#422）。

sections_hook.py / sections_eval.py と同じ「環境グローバル系 builder」。検査対象は
rl-anything 自身のプラグインソース（hooks/ + scripts/ + skills/）であり、project_dir には
依存しない。引数は observability contract 互換（`(project_dir) -> Optional[List[str]]`）の
ため受け取るだけ。

「書きっぱなしで誰も読まない」ストアは、毎発火 hook の場合は純粋なレイテンシ + ディスクコスト
になる（tool_durations.jsonl が実環境 5.1MB で reader 0 だった #422 の発端）。evolve は audit を
消費するため、新たな orphan ストアが生まれるたびに可視化される — 手動突合に依存しない配線。
"""
from pathlib import Path
from typing import List, Optional


def build_orphan_store_section(project_dir: Path) -> Optional[List[str]]:
    """orphan store（登録 hook が書くが reader 不在の jsonl）を audit に surface する。

    観測可能性:
    - orphan_store モジュール未解決 → None（沈黙）
    - 登録 hook の writer が 1 件も無い（hooks.json 不在等、評価対象が無い環境）→ None（沈黙）
    - orphan なし → 「評価したが該当なし ✓」（silence != evaluated）
    - orphan あり → ⚠ で該当ストアと writer(hook) を併記（evidence, #394）
    """
    try:
        import orphan_store
    except ImportError:
        return None

    report = orphan_store.detect_orphan_stores()
    # 登録 hook の writer が 1 件も無い環境は評価対象が無い → 沈黙（hook_drift の applicable=False 相当）。
    if not report.reader_count:
        return None

    header = ["## Orphan Stores (writer あり / reader なし)", ""]
    if not report.orphans:
        return header + [
            "✓ 評価したが該当なし（hooks/ が書く全 jsonl ストアに "
            "scripts/skills 側の reader が存在）",
            "",
        ]

    lines = header + [
        f"⚠ 書きっぱなしで誰も読まない jsonl ストアが {len(report.orphans)} 件。"
        "毎発火 hook の場合は純粋なレイテンシ + ディスクコストになる。reader を足すか、"
        "hook 登録・本体・テストを削除するか判断を推奨。",
    ]
    for name in report.orphans:
        writers = ", ".join(report.writer_files.get(name, [])) or "(不明)"
        lines.append(f"  ・{name} ← writer: {writers}（reader 0）")
    lines.append("")
    return lines


def build_store_contract_section(project_dir: Path) -> Optional[List[str]]:
    """ストア契約ゲート: 宣言（store_registry）と実体の drift を audit に surface する（#434）。

    orphan_store（事後・reader 0 検出）とは別軸の事前ゲート。新ストアを追加するとき
    store_registry に宣言を足さずに hook が書くと `undeclared` として検出する（モグラ叩き解消）。

    観測可能性:
    - orphan_store / store_registry 未解決 → None（沈黙）
    - 登録 hook の writer が 1 件も無い環境 → None（沈黙）
    - drift なし → 「評価したが該当なし ✓」（silence != evaluated）
    - undeclared / stale / 宣言不整合あり → ⚠ で evidence 併記
    """
    try:
        import orphan_store
    except ImportError:
        return None

    # writer が 1 件も無い環境（hooks.json 不在等）は評価対象が無い → 沈黙。
    if not orphan_store.find_store_writers():
        return None

    drift = orphan_store.detect_store_contract_drift()

    header = ["## Store Contract Gate (宣言と実体の drift)", ""]
    clean = not (drift.undeclared or drift.stale or drift.declaration_problems)
    if clean:
        return header + [
            "✓ 評価したが該当なし（登録 hook が書く全 jsonl ストアが store_registry "
            "に writer/reader/retention 宣言済み）",
            "",
        ]

    lines = list(header)
    if drift.undeclared:
        lines.append(
            f"⚠ store_registry に宣言が無い新規 writer が {len(drift.undeclared)} 件。"
            "新ストアは scripts/lib/store_registry.py に writer/reader/retention を宣言すること（#434）。",
        )
        for name in drift.undeclared:
            writers = ", ".join(drift.declared_writer_files.get(name, [])) or "(不明)"
            lines.append(f"  ・{name} ← writer: {writers}（宣言なし）")
    if drift.stale:
        lines.append(
            f"⚠ 宣言はあるが実 writer が見当たらないストアが {len(drift.stale)} 件"
            "（writer 削除済み？ 宣言を見直すこと）: " + ", ".join(drift.stale),
        )
    if drift.declaration_problems:
        lines.append("⚠ store_registry 宣言自身の不整合:")
        for p in drift.declaration_problems:
            lines.append(f"  ・{p}")
    lines.append("")
    return lines
