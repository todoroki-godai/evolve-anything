"""advisory 3点セット scaffold（#118 (b)）。

新規 advisory コンポーネント（module + store + observability section）を追加するたびに
「新モジュール + store_registry 登録 + observability builder + keyset snapshot +
CONTEXT.md + CLAUDE.md」の多点同時更新が要求され、keyset snapshot 追従漏れ等のミス面が
比例拡大する（#118）。ここは #115 の advisory 共通枠（`build_advisory_section`）を使った
builder stub を **テンプレ生成** し、残りの手動配線を **チェックリスト** で明示することで
その摩擦と追従漏れを下げる（決定論・LLM 非依存）。

観測可能性の既知 key は `_OBSERVABILITY_BUILDERS` から動的導出されるため、observability
契約側には別途 snapshot 追従は要らない（`dogfood/invariants.py` 参照）。keyset snapshot
（`test_write_barrier` の active store keyset）追従が要るのは **ストアを持つ** 場合だけ。
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

# snake_case 識別子（先頭は英小文字、以降 [a-z0-9_]）。observability key / module 名に使う。
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass
class ScaffoldResult:
    """scaffold の生成物。files は repo 相対パス→内容。"""

    files: Dict[str, str]
    checklist: List[str]
    registration_line: str


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError(
            f"advisory 名は snake_case 識別子（^[a-z][a-z0-9_]*$）で指定してください: {name!r}"
        )


def section_module_template(name: str, title: str, has_store: bool) -> str:
    """sections_<name>.py の builder stub を #115 共通枠で生成する。"""
    if '"""' in title:
        raise ValueError("title に三重引用符は使えません")
    store_hint = (
        "        # ストア読みは store_read_union / pj_slug_match 経由（read は寛容 union）。\n"
        if has_store
        else ""
    )
    return f'''"""{title} の observability セクション生成（TODO: #issue を記入）。

TODO: このセクションが何を検出し、なぜ audit に常設するかを1段落で書く。
observability contract 互換 ``(project_dir) -> Optional[List[str]]``。#118 scaffold 生成。
"""
from pathlib import Path
from typing import List, Optional

from .advisory import build_advisory_section


def build_{name}_section(project_dir: Path) -> Optional[List[str]]:
    """{title} を audit に surface する。

    観測可能性:
    - モジュール未解決 / 非該当 PJ → None（沈黙）
    - 該当なし → 「評価したが該当なし ✓」（silence != evaluated）
    - 該当あり → ⚠ + evidence（#394: 数字だけでなく根拠まで）
    """

    def compute(proj: Path):
        # TODO: 決定論の検出ロジックを呼ぶ。import 失敗や非該当は None を返す（沈黙）。
{store_hint}        try:
            import {name}  # noqa: F401  # TODO: 実モジュール名に置換
        except ImportError:
            return None
        # TODO: report オブジェクト（render が読む属性を持つ）を返す。
        return None

    def render(report) -> List[str]:
        # TODO: report から ✓/⚠/ℹ 行を組み立てる。floor（件数下限）較正が要るなら維持する。
        return ["✓ 評価したが該当なし（TODO: 実装）"]

    return build_advisory_section(
        project_dir,
        title={title!r},
        compute=compute,
        applicable=lambda report: report is not None,  # TODO: 沈黙条件を調整
        render=render,
    )
'''


def build_checklist(name: str, has_store: bool) -> List[str]:
    """多点同時更新の手動配線チェックリスト（追従漏れ防止）。"""
    reg = f'    ("{name}", build_{name}_section),'
    items = [
        "以下を手で配線してください（scaffold はモジュール stub のみ生成）:",
        f"1. observability.py の import に追加: from .sections_{name} import build_{name}_section",
        f"2. observability.py の _OBSERVABILITY_BUILDERS に登録: {reg.strip()}",
        "3. per-section テストを追加（clean / ⚠ / floor 不足の各シナリオを byte で assert）。"
        "observability 契約は test_observability_contract が builder を動的に走査して自動カバー"
        "（既知 key は _OBSERVABILITY_BUILDERS 由来で別 snapshot 追従は不要）",
        "4. CLAUDE.md コンポーネント表に 1 行サマリを追記（詳細は spec/components.md が SoT）",
        "5. CONTEXT.md に新用語（jargon）があれば 1 行追記（glossary_drift 検出対策）",
    ]
    if has_store:
        items += [
            "6. [store] store_registry に新ストアを status=active で宣言"
            "（writer_locus / retention / writer・reader の根拠 note）。"
            "active store keyset snapshot（test_write_barrier）に追従必須"
            "＝ここが #64/#38 で追従漏れ多発ポイント",
            "7. [store] 書込は store_write barrier 経由（store_write(name, record)）。"
            "例外口のみ store_write_raw。read は store_read_union で寛容 union",
        ]
    return items


def scaffold_advisory(
    name: str,
    *,
    title: Optional[str] = None,
    has_store: bool = False,
) -> ScaffoldResult:
    """advisory コンポーネントのテンプレ + チェックリストを生成する。"""
    _validate_name(name)
    resolved_title = title or f"{name.replace('_', ' ').title()}"
    module_path = f"scripts/lib/audit/sections_{name}.py"
    content = section_module_template(name, resolved_title, has_store)
    return ScaffoldResult(
        files={module_path: content},
        checklist=build_checklist(name, has_store),
        registration_line=f'    ("{name}", build_{name}_section),',
    )


def _repo_root() -> Path:
    """リポジトリルート（scripts/lib の 2 つ上）。テストで monkeypatch する。"""
    return Path(__file__).resolve().parent.parent.parent


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evolve-scaffold-advisory",
        description="advisory 3点セット（module+store+section）の scaffold（#118）",
    )
    parser.add_argument("name", help="advisory 名（snake_case・observability key / module 名）")
    parser.add_argument("--title", default=None, help="セクション見出し（省略時は名前から生成）")
    parser.add_argument(
        "--with-store", action="store_true", help="ストアを持つ場合（store_registry/keyset 追従を checklist に追加）"
    )
    parser.add_argument(
        "--write", action="store_true", help="モジュール stub を実際に書き出す（既定 dry-run）"
    )
    args = parser.parse_args(argv)

    try:
        res = scaffold_advisory(args.name, title=args.title, has_store=args.with_store)
    except ValueError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 2

    root = _repo_root()
    for rel, content in res.files.items():
        target = root / rel
        print(f"# {'書き出し' if args.write else 'dry-run（--write で書き出し）'}: {rel}")
        if args.write:
            if target.exists():
                print(f"エラー: 既存ファイルを上書きしません: {rel}", file=sys.stderr)
                return 1
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        else:
            print(content)

    print("\n=== 手動配線チェックリスト ===")
    for line in res.checklist:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
