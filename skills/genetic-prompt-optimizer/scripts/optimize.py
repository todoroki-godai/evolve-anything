#!/usr/bin/env python3
"""直接パッチプロンプト最適化スクリプト

corrections/sessions からエラーを分類し、LLM 1パスでスキルを直接パッチする。
corrections がない場合は usage 統計・audit 結果をコンテキストに含めた汎用改善。

使用方法:
    python3 optimize.py --target .claude/skills/my-skill/SKILL.md
    python3 optimize.py --target .claude/skills/my-skill/SKILL.md --mode error_guided
    python3 optimize.py --target .claude/skills/my-skill/SKILL.md --dry-run
    python3 optimize.py --restore --target .claude/skills/my-skill/SKILL.md
"""

import argparse
import concurrent.futures
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --- 設定 ---
GENERATIONS_DIR = Path(__file__).parent / "generations"
BACKUP_SUFFIX = ".backup"
MAX_KEPT_RUNS = 5
MAX_CORRECTIONS_PER_PATCH = 10

# 行数制限は共通モジュールから取得
_plugin_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
from line_limit import MAX_RULE_LINES, MAX_SKILL_LINES, suggest_separation
sys.path.insert(0, str(_plugin_root / "scripts"))
from reflect_utils import suggest_paths_frontmatter

# コアロジック（エラー分類・コンテキスト収集・プロンプト構築・LLM呼び出し）
from optimize_core import (
    build_patch_prompt,
    call_llm,
    collect_context,
    collect_corrections,
    determine_strategy,
    format_gate_reason,
    generate_candidate,
    record_pitfall,
    restore_frontmatter_if_lost,
    run_custom_fitness,
    run_regression_gate,
)
from evolution_memory import save_winner  # type: ignore[import]

# corrections パス
_CORRECTIONS_PATH = Path.home() / ".claude" / "rl-anything" / "corrections.jsonl"

# 廃止オプション
_DEPRECATED_OPTIONS = {
    "--generations": "直接パッチモードでは世代ループは不要です。",
    "--population": "直接パッチモードでは集団サイズは不要です。",
    "--budget": "直接パッチモードではバジェット制御は不要です。",
    "--cascade": "直接パッチモードではモデルカスケードは不要です。",
    "--parallel": "直接パッチモードでは並行最適化は不要です。",
    "--strategy": "直接パッチモードでは --mode を使用してください。",
    "--test-tasks": "直接パッチモードではテストタスクは不要です。",
}


def detect_scope(target_path: Path) -> str:
    """ターゲットスキルの scope を判定する。"""
    resolved = target_path.resolve()
    home = Path.home()
    global_skills_dir = home / ".claude" / "skills"
    if str(resolved).startswith(str(global_skills_dir) + os.sep):
        return "global"
    claude_dir = home / ".claude"
    if str(resolved).startswith(str(claude_dir) + os.sep) and "/skills/" in str(resolved):
        return "global"
    return "project"


class DirectPatchOptimizer:
    """直接パッチ最適化エンジン

    corrections/sessions からエラーを分類し、LLM 1パスでスキルを直接パッチする。
    コアロジックは optimize_core に委譲。
    """

    def __init__(
        self,
        target_path: str,
        mode: str = "auto",
        fitness_func: str = "default",
        dry_run: bool = False,
    ):
        self.target_path = Path(target_path)
        self.scope = detect_scope(self.target_path)
        self.mode = mode
        self.fitness_func = fitness_func
        self.dry_run = dry_run
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = GENERATIONS_DIR / self.run_id

    @property
    def _is_rule_file(self) -> bool:
        return ".claude/rules/" in str(self.target_path)

    @property
    def _max_lines(self) -> int:
        return MAX_RULE_LINES if self._is_rule_file else MAX_SKILL_LINES

    @property
    def _claude_cwd(self) -> Optional[str]:
        if self.scope == "global":
            return str(Path.home())
        return None

    @property
    def _target_skill_name(self) -> str:
        """対象スキルのスキル名を推定する。"""
        name = self.target_path.stem
        if name == "SKILL":
            name = self.target_path.parent.name
        return name

    def run(self) -> Dict[str, Any]:
        """直接パッチ最適化を実行する。"""
        if self.scope == "global":
            print("ℹ️ 汎用評価モードで最適化します（プロジェクト固有のコンテキストは使用しません）")

        self._cleanup_old_runs()
        self.backup_original()

        original_content = self.target_path.read_text(encoding="utf-8")
        self.original_content = original_content

        corrections = collect_corrections(
            self._target_skill_name, _CORRECTIONS_PATH, MAX_CORRECTIONS_PER_PATCH
        )
        context = collect_context(self.target_path, _plugin_root, self._target_skill_name)
        strategy = determine_strategy(self.mode, corrections)
        print(f"Mode: {strategy} (corrections: {len(corrections)}件)")

        if self.dry_run:
            result = {
                "run_id": self.run_id,
                "target": str(self.target_path),
                "strategy": strategy,
                "corrections_used": len(corrections),
                "dry_run": True,
                "fitness_func": self.fitness_func,
                "best_individual": {
                    "content": original_content,
                    "content_length": len(original_content),
                    "fitness": None,
                    "strategy": strategy,
                },
            }
            self.save_result(result)
            self.save_history_entry(result)
            return result

        prompt = build_patch_prompt(
            original_content, corrections, context, strategy, self._is_rule_file, self._max_lines
        )
        patched_content, error = call_llm(prompt, self._claude_cwd)
        if patched_content:
            patched_content = restore_frontmatter_if_lost(patched_content, original_content)

        if error:
            print(f"LLM コール失敗: {error}。元のスキルを維持します。")
            result = {
                "run_id": self.run_id,
                "target": str(self.target_path),
                "strategy": strategy,
                "corrections_used": len(corrections),
                "dry_run": False,
                "fitness_func": self.fitness_func,
                "error": error,
                "best_individual": {
                    "content": original_content,
                    "content_length": len(original_content),
                    "fitness": None,
                    "strategy": strategy,
                },
            }
            self.save_result(result)
            self.save_history_entry(result)
            return result

        pitfall_path = str(self.target_path.parent / "references" / "pitfalls.md")
        pitfall_path = pitfall_path if Path(pitfall_path).exists() else None

        passed, gate_reason = run_regression_gate(
            patched_content, original_content, self._max_lines, pitfall_path
        )
        if not passed:
            reason_msg = format_gate_reason(gate_reason)
            print(f"品質ゲート不合格: {reason_msg}")
            suggestion = None
            if gate_reason and gate_reason.startswith("line_limit_exceeded"):
                proposal = suggest_separation(str(self.target_path), patched_content)
                if proposal:
                    suggestion = (
                        f"提案: 詳細を {proposal.reference_path} に分離し、"
                        f"rule は要約+参照リンクのみにすることで行数制限内に収められます。"
                    )
                    print(suggestion)
            record_pitfall(str(self.target_path), "gate", gate_reason or "unknown", 0.0)
            result = {
                "run_id": self.run_id,
                "target": str(self.target_path),
                "strategy": strategy,
                "corrections_used": len(corrections),
                "dry_run": False,
                "fitness_func": self.fitness_func,
                "gate_rejected": True,
                "gate_reason": gate_reason,
                "suggestion": suggestion,
                "best_individual": {
                    "content": original_content,
                    "content_length": len(original_content),
                    "fitness": None,
                    "strategy": strategy,
                },
            }
            self.save_result(result)
            self.save_history_entry(result)
            return result

        self.target_path.write_text(patched_content, encoding="utf-8")
        ref_score = run_custom_fitness(patched_content, self.fitness_func, _plugin_root)

        result = {
            "run_id": self.run_id,
            "target": str(self.target_path),
            "strategy": strategy,
            "corrections_used": len(corrections),
            "dry_run": False,
            "fitness_func": self.fitness_func,
            "best_individual": {
                "content": patched_content,
                "content_length": len(patched_content),
                "fitness": ref_score,
                "strategy": strategy,
            },
        }

        self.save_result(result)
        self.save_history_entry(result)
        return result

    # --- バックアップ/復元 ---

    def backup_original(self):
        """元のスキルをバックアップ"""
        backup_path = self.target_path.with_suffix(
            self.target_path.suffix + BACKUP_SUFFIX
        )
        if not backup_path.exists():
            shutil.copy2(self.target_path, backup_path)
            print(f"バックアップ作成: {backup_path}")

    @staticmethod
    def restore(target_path: str):
        """バックアップから復元"""
        target = Path(target_path)
        backup = target.with_suffix(target.suffix + BACKUP_SUFFIX)
        if backup.exists():
            shutil.copy2(backup, target)
            backup.unlink()
            print(f"復元完了: {target}")
        else:
            print(f"バックアップが見つかりません: {backup}")
            sys.exit(1)

    # --- 結果保存 ---

    def save_result(self, result: Dict[str, Any]):
        """最終結果を保存"""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        result_file = self.run_dir / "result.json"
        result_file.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _cleanup_old_runs(self):
        """古いランデータを削除し、最新 MAX_KEPT_RUNS 件のみ保持"""
        if not GENERATIONS_DIR.exists():
            return
        run_dirs = sorted(
            [d for d in GENERATIONS_DIR.iterdir() if d.is_dir()],
            key=lambda p: p.name,
        )
        if len(run_dirs) <= MAX_KEPT_RUNS:
            return
        for old_dir in run_dirs[: len(run_dirs) - MAX_KEPT_RUNS]:
            shutil.rmtree(old_dir)
            print(f"  古いランデータを削除: {old_dir.name}")

    def save_history_entry(self, result: Dict[str, Any],
                           human_accepted: Optional[bool] = None,
                           rejection_reason: Optional[str] = None) -> Path:
        """history.jsonl にエントリを追記する。"""
        history_file = self.run_dir.parent / "history.jsonl"
        best = result.get("best_individual", {})
        entry = {
            "run_id": result.get("run_id", self.run_id),
            "target": str(self.target_path),
            "timestamp": datetime.now().isoformat(),
            "strategy": result.get("strategy", "auto"),
            "corrections_used": result.get("corrections_used", 0),
            "fitness_func": result.get("fitness_func", self.fitness_func),
            "best_fitness": best.get("fitness"),
            "human_accepted": human_accepted,
            "rejection_reason": rejection_reason,
        }
        history_file.parent.mkdir(parents=True, exist_ok=True)
        with open(history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return history_file

    @staticmethod
    def record_human_decision(run_dir: str, human_accepted: bool,
                              rejection_reason: Optional[str] = None) -> None:
        """既存の history.jsonl エントリに human decision を記録する。"""
        run_path = Path(run_dir)
        history_file = run_path.parent / "history.jsonl"
        if not history_file.exists():
            print(f"history.jsonl が見つかりません: {history_file}")
            return

        lines = history_file.read_text(encoding="utf-8").strip().split("\n")
        if not lines:
            return

        last_entry = json.loads(lines[-1])
        last_entry["human_accepted"] = human_accepted
        last_entry["rejection_reason"] = rejection_reason
        lines[-1] = json.dumps(last_entry, ensure_ascii=False)

        history_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


class PopulationBroadcastOptimizer:
    """FORGE: n候補を並行生成し、最高スコアを選んでパターン永続化する。"""

    def __init__(
        self,
        skill_path: str,
        plugin_root: str,
        target_skill_name: str,
        n: int = 3,
        fitness_func: str = "default",
        mode: str = "population_broadcast",
    ):
        self.target_path = Path(skill_path)
        self.plugin_root = Path(plugin_root)
        # SKILL.md を直接指定したとき stem が "SKILL" になるため親ディレクトリ名で補正
        if target_skill_name == "SKILL":
            target_skill_name = self.target_path.parent.name
        self.target_skill_name = target_skill_name
        self.n = n
        self.fitness_func = fitness_func
        self.mode = mode
        self.scope = detect_scope(self.target_path)
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    @property
    def _claude_cwd(self) -> Optional[str]:
        if self.scope == "global":
            return str(Path.home())
        return None

    def run(self) -> Dict[str, Any]:
        """population_broadcast フローを実行する。"""
        original_content = self.target_path.read_text(encoding="utf-8")

        corrections = collect_corrections(
            self.target_skill_name, _CORRECTIONS_PATH, MAX_CORRECTIONS_PER_PATCH
        )
        context = collect_context(self.target_path, self.plugin_root, self.target_skill_name)
        strategy = determine_strategy("auto", corrections)
        print(f"[population_broadcast] strategy={strategy}, n={self.n}")

        is_rule_file = ".claude/rules/" in str(self.target_path)
        max_lines = MAX_RULE_LINES if is_rule_file else MAX_SKILL_LINES
        pitfall_path_obj = self.target_path.parent / "references" / "pitfalls.md"
        pitfall_path = str(pitfall_path_obj) if pitfall_path_obj.exists() else None

        prompt = build_patch_prompt(
            original_content, corrections, context, strategy, is_rule_file, max_lines
        )

        # n候補を並行生成
        candidates: List[Dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.n) as executor:
            futures = [
                executor.submit(
                    generate_candidate,
                    prompt,
                    original_content,
                    self._claude_cwd,
                    max_lines,
                    pitfall_path,
                )
                for _ in range(self.n)
            ]
            for f in concurrent.futures.as_completed(futures):
                candidates.append(f.result())

        passed_candidates = [c for c in candidates if c["passed"] and c["content"]]
        passed_count = len(passed_candidates)
        print(f"[population_broadcast] {passed_count}/{self.n} 候補がゲート通過")

        if not passed_candidates:
            return {
                "run_id": self.run_id,
                "mode": self.mode,
                "n_candidates": self.n,
                "passed_count": 0,
                "winner": None,
                "error": "全候補がゲート不合格",
            }

        # スコアリング (fitness_func="default" は None を返す)
        for c in passed_candidates:
            c["fitness"] = run_custom_fitness(c["content"], self.fitness_func, self.plugin_root)

        # winner 選択: スコアが None でなければ最高スコア、Noneなら最初の通過候補
        scored = [c for c in passed_candidates if c["fitness"] is not None]
        if scored:
            winner = max(scored, key=lambda c: c["fitness"])
        else:
            winner = passed_candidates[0]

        # ファイル上書き
        self.target_path.write_text(winner["content"], encoding="utf-8")

        # evolution_memory に記録
        score_before = 0.0
        score_after = float(winner["fitness"]) if winner["fitness"] is not None else 0.0
        patch_summary = f"population_broadcast: {strategy}, {self.n}候補中winner"
        save_winner(
            skill_name=self.target_skill_name,
            strategy=strategy,
            score_before=score_before,
            score_after=score_after,
            patch_summary=patch_summary,
        )

        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "n_candidates": self.n,
            "passed_count": passed_count,
            "winner": winner,
        }


def _check_deprecated_options(argv: List[str]) -> Optional[str]:
    """廃止オプションが使われていないかチェック。使われていたらエラーメッセージを返す。"""
    for arg in argv:
        for dep_opt, msg in _DEPRECATED_OPTIONS.items():
            if arg == dep_opt or arg.startswith(dep_opt + "="):
                return f"{dep_opt} は廃止されました。{msg}"
    return None


def main():
    dep_error = _check_deprecated_options(sys.argv[1:])
    if dep_error:
        print(f"エラー: {dep_error}", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="直接パッチプロンプト最適化")
    parser.add_argument(
        "--target", required=True, help="最適化対象のスキルファイルパス"
    )
    parser.add_argument(
        "--mode", default="auto",
        choices=["auto", "error_guided", "llm_improve", "population_broadcast"],
        help="最適化モード（auto: corrections有無で自動判定、population_broadcast: n候補並行生成）"
    )
    parser.add_argument(
        "--n", type=int, default=3,
        help="population_broadcast モードの候補数（デフォルト: 3）"
    )
    parser.add_argument(
        "--fitness", default="default", help="適応度関数名（参考スコア表示用）"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="構造テスト（LLM呼び出しなし）"
    )
    parser.add_argument(
        "--restore", action="store_true", help="バックアップから復元"
    )
    parser.add_argument(
        "--accept", action="store_true", help="直近の最適化結果を受理する"
    )
    parser.add_argument(
        "--reject", action="store_true", help="直近の最適化結果を却下する"
    )
    parser.add_argument(
        "--reason", default=None, help="却下理由（--reject 時のオプション）"
    )

    args = parser.parse_args()

    if args.accept or args.reject:
        run_dir = str(GENERATIONS_DIR)
        if GENERATIONS_DIR.exists():
            run_dirs = sorted(
                [d for d in GENERATIONS_DIR.iterdir() if d.is_dir()],
                key=lambda p: p.name,
            )
            if run_dirs:
                run_dir = str(run_dirs[-1])
        DirectPatchOptimizer.record_human_decision(
            run_dir,
            human_accepted=args.accept,
            rejection_reason=args.reason if args.reject else None,
        )
        status = "受理" if args.accept else "却下"
        print(f"結果を{status}として記録しました")
        if args.reason:
            print(f"理由: {args.reason}")
        return

    if args.restore:
        DirectPatchOptimizer.restore(args.target)
        return

    if not Path(args.target).exists():
        print(f"エラー: ターゲットファイルが見つかりません: {args.target}")
        sys.exit(1)

    if args.mode == "population_broadcast":
        target_path = Path(args.target)
        skill_name = target_path.stem
        if skill_name == "SKILL":
            skill_name = target_path.parent.name
        optimizer_pb = PopulationBroadcastOptimizer(
            skill_path=args.target,
            plugin_root=str(_plugin_root),
            target_skill_name=skill_name,
            n=args.n,
            fitness_func=args.fitness,
        )
        result = optimizer_pb.run()
        print(f"\n=== population_broadcast 結果 ===")
        print(f"Run ID: {result['run_id']}")
        print(f"候補数: {result['n_candidates']} / 通過: {result['passed_count']}")
        if result.get("winner"):
            w = result["winner"]
            print(f"winner fitness: {w['fitness']}")
        if result.get("error"):
            print(f"エラー: {result['error']}")
        return

    optimizer = DirectPatchOptimizer(
        target_path=args.target,
        mode=args.mode,
        fitness_func=args.fitness,
        dry_run=args.dry_run,
    )

    result = optimizer.run()

    print(f"\n=== 最適化結果 ===")
    print(f"Run ID: {result['run_id']}")
    print(f"モード: {result['strategy']}")
    print(f"corrections使用: {result['corrections_used']}件")
    print(f"dry-run: {result['dry_run']}")

    if result.get("error"):
        print(f"エラー: {result['error']}")

    if result.get("gate_rejected"):
        print(f"品質ゲート不合格: {result.get('gate_reason', '不明')}")

    if result.get("best_individual"):
        best = result["best_individual"]
        if best.get("fitness") is not None:
            print(f"参考スコア: {best['fitness']}")

    target = result.get("target", "")
    if ".claude/rules/" in target and result.get("corrections_used", 0) > 0:
        messages = " ".join(
            c.get("message", "") for c in collect_corrections(
                optimizer._target_skill_name, _CORRECTIONS_PATH, MAX_CORRECTIONS_PER_PATCH
            )
        )
        ps = suggest_paths_frontmatter(messages, Path.cwd())
        if ps is not None:
            print(f"\n💡 paths frontmatter 提案: paths: {ps.patterns}")
            print(f"   (CC バージョンによっては globs: の方が信頼性が高い場合があります)")

    print(f"\n結果保存先: {optimizer.run_dir}")


if __name__ == "__main__":
    main()
