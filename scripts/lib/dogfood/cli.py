"""bin/rl-dogfood-gate のロジック本体（#496）。

通し評価ゲート CLI。``--layer 1|2|3|all``、``--json``、``--output <path>``。
exit 0=全緑 / 1=赤あり / 2=実行エラー。

Layer 2 は Layer 1 の dry-run result JSON を入力にするため、``--layer 2`` 単独でも
内部で Layer 1a の dry-run を 1 回回して result を得る（または ``--result <path>`` 指定）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from . import invariants, layer1, layer3


def _repo_root() -> Path:
    """このパッケージから plugin root を解決する（scripts/lib/dogfood → root）。"""
    return Path(__file__).resolve().parents[3]


def _run_layer1(repo_root: Path, out_dir: Path) -> Dict[str, Any]:
    return layer1.run_layer1(repo_root, out_dir=out_dir)


def _run_layer2(repo_root: Path, out_dir: Path, result_path: Path | None) -> Dict[str, Any]:
    """Layer 2: result JSON に invariants を適用。result が無ければ dry-run で生成。"""
    if result_path is None or not Path(result_path).exists():
        l1 = layer1.check_dry_run_invariance(repo_root, out_dir=out_dir)
        result_path = l1.get("result_path")
    if not result_path or not Path(result_path).exists():
        return {"error": "result JSON を取得できず Layer 2 を実行不能", "checks": []}
    result = json.loads(Path(result_path).read_text(encoding="utf-8"))
    return {"result_path": str(result_path), "checks": invariants.run_all(result)}


def _layer1_has_red(l1: Dict[str, Any]) -> tuple[bool, bool]:
    """(has_fail, has_error) を返す。"""
    has_fail = any(c["status"] == "fail" for c in l1.get("checks", []))
    has_error = any(c["status"] == "error" for c in l1.get("checks", []))
    return has_fail, has_error


def _print_layer1(l1: Dict[str, Any]) -> None:
    print("=== Layer 1: dogfood E2E ===")
    for c in l1.get("checks", []):
        mark = {"pass": "✓", "fail": "✗", "skip": "—", "error": "‼"}.get(c["status"], "?")
        print(f"  {mark} {c['name']}: {c['status']} — {c.get('detail', '')}")
        diff = c.get("diff") or c.get("store_changes")
        if diff and not (c["status"] == "pass"):
            for kind in ("added", "removed", "modified"):
                for p in diff.get(kind, []):
                    print(f"       {kind}: {p}")


def _print_layer2(l2: Dict[str, Any]) -> None:
    print("=== Layer 2: report invariants ===")
    if l2.get("error"):
        print(f"  ‼ {l2['error']}")
        return
    for chk in l2.get("checks", []):
        if chk["failures"]:
            print(f"  ✗ {chk['check']}: {len(chk['failures'])} 件")
            for f in chk["failures"]:
                print(f"       {f['detail']}")
        else:
            print(f"  ✓ {chk['check']}")


def _print_layer3(l3: Dict[str, Any]) -> None:
    print("=== Layer 3: SKILL.md code blocks ===")
    s = l3.get("summary", {})
    print(f"  summary: pass={s.get('pass', 0)} fail={s.get('fail', 0)} skip={s.get('skip', 0)}")
    for skill in l3.get("skills", []):
        fails = [b for b in skill["blocks"] if b["status"] == "fail"]
        if fails:
            print(f"  ✗ {skill['skill']}: {len(fails)} 件の赤")
            for b in fails:
                src = Path(b.get("source", "")).parent.name + "/SKILL.md"
                print(f"       {src}:{b['line']} [{b['mode']}] {b['detail']}")


def _layer2_has_red(l2: Dict[str, Any]) -> bool:
    if l2.get("error"):
        return True
    return any(chk["failures"] for chk in l2.get("checks", []))


def _layer3_has_red(l3: Dict[str, Any]) -> bool:
    return l3.get("summary", {}).get("fail", 0) > 0


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rl-dogfood-gate",
        description="通し評価ゲート（#496）— dry-run 不変 / report invariants / SKILL.md コードブロック検証",
    )
    parser.add_argument("--layer", choices=["1", "2", "3", "all"], default="all")
    parser.add_argument("--json", action="store_true", help="JSON 出力")
    parser.add_argument("--output", type=Path, default=None, help="結果 JSON の保存先")
    parser.add_argument("--result", type=Path, default=None, help="Layer 2 が読む既存 result JSON（省略時は dry-run で生成）")
    parser.add_argument("--out-dir", type=Path, default=Path("/tmp") / "rl-dogfood-gate", help="一時出力ディレクトリ")
    args = parser.parse_args(argv)

    repo_root = _repo_root()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    report: Dict[str, Any] = {"layer": args.layer, "repo_root": str(repo_root)}
    exit_error = False
    exit_red = False
    result_path = args.result

    if args.layer in ("1", "all"):
        l1 = _run_layer1(repo_root, out_dir)
        report["layer1"] = l1
        has_fail, has_error = _layer1_has_red(l1)
        exit_red = exit_red or has_fail
        exit_error = exit_error or has_error
        result_path = l1.get("result_path") or result_path

    if args.layer in ("2", "all"):
        l2 = _run_layer2(repo_root, out_dir, result_path)
        report["layer2"] = l2
        if l2.get("error"):
            exit_error = True
        exit_red = exit_red or _layer2_has_red(l2)

    if args.layer in ("3", "all"):
        l3 = layer3.run_layer3(repo_root)
        report["layer3"] = l3
        exit_red = exit_red or _layer3_has_red(l3)

    if args.json:
        out = json.dumps(report, ensure_ascii=False, indent=2)
        print(out)
    else:
        if "layer1" in report:
            _print_layer1(report["layer1"])
        if "layer2" in report:
            _print_layer2(report["layer2"])
        if "layer3" in report:
            _print_layer3(report["layer3"])

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if exit_error:
        return 2
    if exit_red:
        return 1
    return 0
