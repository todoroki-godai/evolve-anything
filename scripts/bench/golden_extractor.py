"""TBench2-rl: GoldenCase 抽出モジュール。

usage.jsonl（スキル使用記録）と corrections.jsonl（修正フィードバック）を
結合し、GoldenCase（正例/負例ペア）を生成する。

正例: correction_count == 0 のセッション（修正なし = 成功プロキシ）
負例: correction_count >= 1 のセッション（修正あり）

Data sources:
  - usage.jsonl    : skill_name, session_id, ts, file_path (per-skill 記録)
  - corrections.jsonl : session_id をキーに correction 件数を集計
  - system_context : 現在の CLAUDE.md + rules テキスト（注入またはファイル読み込み）

Note: sessions.jsonl は session サマリ（skill_count/error_count）を記録するが
      skill_name / user_prompt は持たない。per-skill 記録は usage.jsonl を使用する。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

# scripts/bench/ → scripts/ → <plugin_root>/
_BENCH_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _BENCH_DIR.parent
_PLUGIN_ROOT = _SCRIPTS_DIR.parent

import os

_DATA_DIR_ENV = os.environ.get("CLAUDE_PLUGIN_DATA", "")
DATA_DIR_DEFAULT = Path(_DATA_DIR_ENV) if _DATA_DIR_ENV else Path.home() / ".claude" / "rl-anything"

# usage.jsonl の必須フィールド（設計仕様: golden_extractor init 時に assert）
REQUIRED_USAGE_FIELDS = {"skill_name", "session_id", "ts"}


@dataclass
class GoldenCase:
    """golden set の1エントリ。

    Attributes:
        skill_name:       使用されたスキル名
        user_prompt:      ユーザー入力（usage.jsonl の file_path を代用）
        system_context:   セッション時点の CLAUDE.md + rules テキスト
        correction_count: 0 = 正例（golden）, >= 1 = 負例
        session_id:       セッション識別子
    """

    skill_name: str
    user_prompt: str
    system_context: str
    correction_count: int
    session_id: str


class GoldenExtractor:
    """usage.jsonl + corrections.jsonl → GoldenCase リストの抽出器。

    Args:
        usage_file:       usage.jsonl のパス（省略時はデフォルト DATA_DIR）
        corrections_file: corrections.jsonl のパス（省略時はデフォルト DATA_DIR）
        system_context:   CLAUDE.md + rules のテキスト（省略時はファイルから自動読み込み）
    """

    def __init__(
        self,
        usage_file: Optional[Path] = None,
        corrections_file: Optional[Path] = None,
        system_context: Optional[str] = None,
    ) -> None:
        self.usage_file = usage_file or (DATA_DIR_DEFAULT / "usage.jsonl")
        self.corrections_file = corrections_file or (DATA_DIR_DEFAULT / "corrections.jsonl")
        self.system_context = system_context if system_context is not None else self._load_system_context()
        self._validate_usage_fields()

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────

    def extract(self, skill_names: Optional[list[str]] = None) -> list[GoldenCase]:
        """GoldenCase リストを生成する。

        Args:
            skill_names: フィルタするスキル名のリスト。None の場合は全スキル。

        Returns:
            GoldenCase のリスト。(session_id, skill_name) 単位で1エントリ。
        """
        correction_counts = self._count_corrections()

        # (session_id, skill_name) → 最初の usage レコードを保持
        seen: dict[tuple[str, str], dict] = {}
        for rec in self._load_jsonl(self.usage_file):
            sn = rec.get("skill_name", "")
            sid = rec.get("session_id", "")
            if not sn or not sid:
                continue
            if skill_names is not None and sn not in skill_names:
                continue
            key = (sid, sn)
            if key not in seen:
                seen[key] = rec

        cases = []
        for (sid, sn), rec in seen.items():
            cases.append(
                GoldenCase(
                    skill_name=sn,
                    user_prompt=rec.get("file_path", ""),
                    system_context=self.system_context,
                    correction_count=correction_counts.get(sid, 0),
                    session_id=sid,
                )
            )
        return cases

    def save(self, cases: list[GoldenCase], output: Path) -> None:
        """GoldenCase を golden_cases.jsonl に書き出す。

        Args:
            cases:  保存する GoldenCase のリスト
            output: 出力先ファイルパス（親ディレクトリが存在しない場合は作成する）
        """
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", encoding="utf-8") as f:
            for case in cases:
                f.write(json.dumps(asdict(case), ensure_ascii=False) + "\n")

    # ──────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────

    def _validate_usage_fields(self) -> None:
        """usage.jsonl の最初の有効レコードに必須フィールドが存在するか検証する。

        ファイルが存在しない・空の場合はスキップ（抽出時に空を返す）。
        フィールド欠如時は AssertionError を送出する。
        """
        if not self.usage_file.exists():
            return
        for line in self.usage_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            missing = REQUIRED_USAGE_FIELDS - set(rec.keys())
            if missing:
                raise AssertionError(
                    f"usage.jsonl に必須フィールドが見つかりません: {sorted(missing)}"
                )
            return  # 最初の有効レコードのみ検証

    def _count_corrections(self) -> dict[str, int]:
        """corrections.jsonl から session_id → correction_count の辞書を作成する。"""
        counts: dict[str, int] = defaultdict(int)
        for rec in self._load_jsonl(self.corrections_file):
            sid = rec.get("session_id", "")
            if sid:
                counts[sid] += 1
        return dict(counts)

    @staticmethod
    def _load_jsonl(path: Path) -> list[dict]:
        """JSONL ファイルを読み込む。ファイルが存在しない・壊れた行はスキップ。"""
        if not path.exists():
            return []
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    def _load_system_context(self) -> str:
        """CLAUDE.md + rules/ の内容を結合して返す。

        考察系スキル（evolve/reflect/optimize/audit）のオフライン評価に使用する
        system context。歴史的な内容は保存していないため、現時点の内容を使用する。
        """
        parts: list[str] = []

        # CLAUDE.md
        claude_md = _PLUGIN_ROOT / "CLAUDE.md"
        if claude_md.exists():
            parts.append(f"# CLAUDE.md\n{claude_md.read_text(encoding='utf-8')}")

        # .claude/rules/*.md (プロジェクト固有ルール)
        rules_dir = _PLUGIN_ROOT / ".claude" / "rules"
        if rules_dir.is_dir():
            for rule_file in sorted(rules_dir.glob("*.md")):
                parts.append(
                    f"# rules/{rule_file.name}\n{rule_file.read_text(encoding='utf-8')}"
                )

        return "\n\n".join(parts)


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="usage.jsonl + corrections.jsonl から GoldenCase を抽出して保存する。"
    )
    p.add_argument(
        "--usage-file",
        type=Path,
        default=DATA_DIR_DEFAULT / "usage.jsonl",
        help="usage.jsonl のパス（デフォルト: ~/.claude/rl-anything/usage.jsonl）",
    )
    p.add_argument(
        "--corrections-file",
        type=Path,
        default=DATA_DIR_DEFAULT / "corrections.jsonl",
        help="corrections.jsonl のパス（デフォルト: ~/.claude/rl-anything/corrections.jsonl）",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=DATA_DIR_DEFAULT / "golden_cases.jsonl",
        help="出力先 JSONL ファイル（デフォルト: ~/.claude/rl-anything/golden_cases.jsonl）",
    )
    p.add_argument(
        "--skills",
        nargs="*",
        metavar="SKILL",
        help="抽出対象スキル名（省略時は全スキル）。例: --skills evolve reflect",
    )
    p.add_argument(
        "--golden-only",
        action="store_true",
        help="correction_count == 0 の正例のみ出力する",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        extractor = GoldenExtractor(
            usage_file=args.usage_file,
            corrections_file=args.corrections_file,
        )
    except AssertionError as exc:
        print(f"[golden_extractor] 検証エラー: {exc}", file=sys.stderr)
        return 1

    cases = extractor.extract(skill_names=args.skills if args.skills else None)

    if args.golden_only:
        cases = [c for c in cases if c.correction_count == 0]

    extractor.save(cases, args.output)

    positives = sum(1 for c in cases if c.correction_count == 0)
    negatives = len(cases) - positives
    print(
        f"[golden_extractor] {len(cases)} 件保存 → {args.output}"
        f"  (正例: {positives}, 負例: {negatives})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
