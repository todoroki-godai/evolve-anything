"""推奨 artifact (rule / hook / skill / config) 一覧と導入状態判定。

discover/__init__.py から re-export される（後方互換）。
PLUGIN_ROOT は package 経由で遅延参照する。
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import PLUGIN_ROOT


# 推奨 artifact 一覧 — `path` / `hook_path` の有無で導入状態を判定する。
# `data_driven=True` の artifact は tool_usage_patterns を evidence として付加。
# `recommendation_id` 付きエントリは _compute_mitigation_metrics で削減効果を計測。
RECOMMENDED_ARTIFACTS = [
    {
        "id": "avoid-bash-builtin",
        "type": "rule+hook",
        "path": Path.home() / ".claude" / "rules" / "avoid-bash-builtin.md",
        "description": "Bash Built-in 代替コマンド禁止 — grep/cat/find 等を Built-in ツールに誘導",
        "hook_path": Path.home() / ".claude" / "hooks" / "check-bash-builtin.py",
        "hook_description": "PreToolUse hook: Bash で Built-in 代替可能コマンドを block",
        "data_driven": True,  # tool_usage_patterns のデータで提案根拠を補強
        "recommendation_id": "builtin_replaceable",
        "content_patterns": ["REPLACEABLE"],
    },
    {
        "id": "sleep-polling-guard",
        "type": "hook",
        "path": None,
        "description": "sleep ポーリング検出 — run_in_background + 完了通知待ちを推奨",
        "hook_path": Path.home() / ".claude" / "hooks" / "check-bash-builtin.py",
        "hook_description": "PreToolUse hook: sleep コマンドを検出・警告",
        "recommendation_id": "sleep_polling",
        "content_patterns": [r"\bsleep\b"],
    },
    {
        "id": "test-happy-path-first",
        "equivalent_markers": ["正常系E2Eテスト"],
        "type": "rule",
        "path": Path.home() / ".claude" / "rules" / "test-happy-path-first.md",
        "description": "テストはハッピーパスから書く — パイプラインの正常系E2Eテストを最初に書くルール",
        "hook_path": None,
    },
    {
        "id": "commit-version",
        "type": "rule",
        "path": Path.home() / ".claude" / "rules" / "commit-version.md",
        "description": "コミット時バージョン管理 — feat!=major, feat=minor, fix=patch の自動判定提案",
        "hook_path": None,
    },
    {
        "id": "commit-skill",
        "type": "skill",
        "path": Path.home() / ".claude" / "skills" / "commit" / "SKILL.md",
        "description": "Conventional Commits + CHANGELOG 自動追記スキル — feat→Added, fix→Fixed, BREAKING CHANGE 対応",
        "hook_path": None,
    },
    {
        "id": "claude-md-style",
        "equivalent_markers": ["CLAUDE.mdは現在有効な動作情報"],
        "type": "rule",
        "path": Path.home() / ".claude" / "rules" / "claude-md-style.md",
        "description": "CLAUDE.md vs MEMORY.md の使い分け — 動作情報/経緯/コードから分かる情報の記載先判断",
        "hook_path": None,
    },
    {
        "id": "process-stall-guard",
        "type": "rule",
        "path": Path.home() / ".claude" / "rules" / "process-stall-guard.md",
        "description": "長時間プロセス実行前に既存プロセスを確認 — CDK/Docker/npm 等の停滞→kill→リトライ防止",
        "hook_path": None,
        "recommendation_id": "process_stall_guard",
        "content_patterns": [r"\bpgrep\b", r"\bkill\b", r"stall|停滞"],
    },
    {
        "id": "evidence-before-claims",
        "equivalent_markers": ["検証コマンドの実行結果を提示"],
        "type": "rule",
        "path": Path.home() / ".claude" / "rules" / "verify-before-claim.md",
        "description": "証拠提示義務 — 完了主張の前に検証コマンドの実行結果を提示する",
        "hook_path": None,
        "recommendation_id": "evidence_before_claims",
        "content_patterns": ["verify-before-claim", "evidence", "証拠", r"完了.*確認"],
    },
    # --- 構造化実装 ---
    {
        "id": "implement-skill",
        "type": "skill",
        "path": PLUGIN_ROOT / "skills" / "implement" / "SKILL.md",
        "description": "implement — plan artifact → タスク分解 → 実装（Standard/Parallel）→ 計画準拠チェック → テレメトリ記録",
        "hook_path": None,
    },
    {
        "id": "suggest-implement-skill",
        "equivalent_markers": ["/evolve-anything:implement` を提案"],
        "type": "rule",
        "path": Path.home() / ".claude" / "rules" / "suggest-implement-skill.md",
        "description": "実装タスク時の implement スキル提案 — 「実装して」等で /evolve-anything:implement を提案",
        "hook_path": None,
    },
    {
        "id": "implement-flow-chain",
        "type": "config",
        "path": Path.home() / ".gstack" / "flow-chain.json",
        "description": "gstack フローチェーンに implement を追加 — plan-eng-review → /evolve-anything:implement → /review",
        "hook_path": None,
        "content_patterns": ["implement"],
    },
    # --- gstack ワークフローツール ---
    {
        "id": "gstack-flow-chain",
        "equivalent_markers": ["flow-chain.json"],
        "type": "rule",
        "path": Path.home() / ".claude" / "rules" / "gstack-flow-chain.md",
        "description": "gstack フローチェーン — /ship→/document-release→/spec-keeper update→/retro の実装後ワークフロー",
        "hook_path": None,
    },
    {
        "id": "living-spec-awareness",
        "equivalent_markers": ["spec-keeper init` を提案"],
        "type": "rule",
        "path": Path.home() / ".claude" / "rules" / "living-spec-awareness.md",
        "description": "Living Spec 意識 — SPEC.md 未存在 PJ で /spec-keeper init を提案、存在 PJ では最初に読む",
        "hook_path": None,
    },
    {
        "id": "spec-keeper",
        "type": "skill",
        "path": PLUGIN_ROOT / "skills" / "spec-keeper" / "SKILL.md",
        "description": "spec-keeper — SPEC.md + ADR 管理スキル（init/update/adr/status）",
        "hook_path": None,
    },
    {
        "id": "ship",
        "type": "skill",
        "path": Path.home() / ".claude" / "skills" / "ship" / "SKILL.md",
        "description": "ship — 実装→テスト→bump→CHANGELOG→PR の出荷ワークフロー",
        "hook_path": None,
    },
    {
        "id": "continuation-check",
        "equivalent_markers": ["handoverファイルとSPEC.md"],
        "type": "rule",
        "path": Path.home() / ".claude" / "rules" / "continuation-check.md",
        "description": "前回の続き判定 — 引き継ぎ/ロードマップ言及時に引き継ぎファイルとSPEC.mdを自動確認",
        "hook_path": None,
    },
    # --- 並行開発パターン ---
    {
        "id": "worktree-parallel-work",
        "equivalent_markers": ["worktree で隔離する"],
        "type": "rule+hook",
        "path": Path.home() / ".claude" / "rules" / "worktree-parallel-work.md",
        "description": "worktree 並行開発 — ブランチ作成時に worktree を使い、stash+checkout 事故と同一ディレクトリ並行作業を防止。feature-branch rule の PJ 上書きが必要",
        "hook_path": Path.home() / ".claude" / "hooks" / "check-worktree.py",
        "hook_description": "PreToolUse hook: git stash+checkout および git checkout -b を検出し worktree を提案",
    },
    {
        "id": "deploy-lock",
        "type": "hook",
        "path": None,
        "description": "デプロイ排他制御 — 同一環境への並行デプロイを lock ファイルで防止（PreToolUse で取得 + PostToolUse で解放）",
        "hook_path": Path.home() / ".claude" / "hooks" / "deploy-lock.py",
        "hook_description": "PreToolUse hook: deploy 時に環境別 lock 取得。PostToolUse hook: deploy 完了時に lock 解放",
    },
    {
        "id": "kill-guard",
        "type": "hook",
        "path": None,
        "description": "プロセス kill ガード — deploy-lock 保持中のプロセス kill をブロック",
        "hook_path": Path.home() / ".claude" / "hooks" / "kill-guard.py",
        "hook_description": "PreToolUse hook: kill/pkill/pgrep+kill が deploy 関連プロセスを対象とする場合、active lock があればブロック",
    },
]


def _search_roots(project_root: Optional[Path] = None) -> List[Path]:
    """既存 artifact を探すディレクトリ群（グローバル + PJ）。

    決め打ちパスだけを見ると、**同じ内容が別名・別ディレクトリにあっても
    「未導入」と判定する**（実測 2026-09-05: 12 件の提案のうち 10 件が
    既存 rules に書かれていた）。ここは探索範囲の単一ソース。
    """
    roots = [
        Path.home() / ".claude" / "rules",
        Path.home() / ".claude" / "hooks",
        Path.home() / ".claude" / "skills",
    ]
    if project_root:
        roots.append(Path(project_root) / ".claude" / "rules")
        roots.append(Path(project_root) / ".claude" / "hooks")
    return roots


def _find_same_name(path: Optional[Path], roots: List[Path]) -> Optional[Path]:
    """決め打ちパスと同じファイル名を、探索範囲から再帰的に探す。"""
    if not path:
        return None
    name = path.name
    for root in roots:
        if not root.is_dir():
            continue
        for found in root.rglob(name):
            if found.is_file():
                return found
    return None


def _find_marker(markers: List[str], roots: List[Path]) -> Optional[str]:
    """本文の特徴語で既存 artifact を探し、最初のヒットを `file:line` で返す。

    **判定に使う識別**: 本文に現れる文字列。名前でも実体でもない。
    既知の言い回しのみ検出でき、書き換えられれば当たらなくなる。
    ゆえに**提案を消さず「既に書かれている可能性」として下げるだけ**に使い、
    下げた件数と内訳は必ず surface する（`.claude/rules/no-denylist-checks.md`）。
    """
    if not markers:
        return None
    for root in roots:
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*.md")):
            try:
                lines = f.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                if any(m in line for m in markers):
                    return f"{f}:{i}"
    return None


def detect_recommended_artifacts(
    tool_usage_patterns: Optional[Dict[str, Any]] = None,
    *,
    now: Optional[float] = None,
    project_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """推奨ルール/hook の導入状態をチェックする。

    Returns:
        未導入 artifact のリスト。各エントリに evidence (検出データ) を含む。

    #26: ユーザーが導入を見送った artifact は suppression 窓（TTL）内は畳む。
    記録は discover-suppression.jsonl の type:"artifact" エントリ。窓を過ぎたら
    1 回だけ再提示して再評価を促す（is_artifact_suppressed が TTL を判定）。
    """
    missing = []
    # package 経由で参照することで `mock.patch("discover.RECOMMENDED_ARTIFACTS", ...)` 既存テストに追従
    from . import RECOMMENDED_ARTIFACTS as _ARTIFACTS  # noqa: PLC0415
    from .suppression import is_artifact_suppressed  # noqa: PLC0415
    roots = _search_roots(project_root)
    for artifact in _ARTIFACTS:
        rule_path = artifact.get("path")
        rule_exists = rule_path.exists() if rule_path else True
        hook_path = artifact.get("hook_path")
        hook_exists = hook_path.exists() if hook_path else True

        # 決め打ちパスに無くても、同じ内容が別名・別ディレクトリにあることがある。
        # 見つけた根拠は covered_by に残し、提案は消さず下げる（呼び出し側が分離）。
        covered_by: Optional[str] = None
        if not rule_exists:
            same = _find_same_name(rule_path, roots)
            if same:
                covered_by = str(same)
            else:
                covered_by = _find_marker(artifact.get("equivalent_markers", []), roots)
        if not hook_exists:
            same_hook = _find_same_name(hook_path, roots)
            if same_hook:
                hook_exists = True

        if rule_exists and hook_exists:
            continue

        # #26: 見送り済み & クールダウン窓内なら再提示しない
        if is_artifact_suppressed(artifact["id"], now=now):
            continue

        entry: Dict[str, Any] = {
            "id": artifact["id"],
            "description": artifact["description"],
            "missing": [],
        }
        if covered_by:
            # 「既に書かれている可能性」の根拠。呼び出し側はこれを持つ提案を
            # 既定で提示から下げ、下げた件数と内訳を必ず 1 行 surface する。
            entry["covered_by"] = covered_by
        if not rule_exists and rule_path:
            entry["missing"].append({"type": "rule", "path": str(rule_path)})
        if not hook_exists and hook_path:
            entry["missing"].append({
                "type": "hook",
                "path": str(hook_path),
                "description": artifact.get("hook_description", ""),
            })

        # data_driven な artifact には tool_usage データを証拠として付加
        if artifact.get("data_driven") and tool_usage_patterns:
            builtin = tool_usage_patterns.get("builtin_replaceable", [])
            if builtin:
                entry["evidence"] = {
                    "builtin_replaceable_count": sum(
                        item.get("count", 0) for item in builtin
                    ),
                    "top_patterns": builtin[:5],
                }
                # rule/hook 候補も付加
                if "rule_candidates" in tool_usage_patterns:
                    entry["rule_candidates"] = tool_usage_patterns["rule_candidates"]
                if "hook_candidate" in tool_usage_patterns:
                    entry["hook_candidate"] = tool_usage_patterns["hook_candidate"]

        missing.append(entry)
    return missing


def detect_installed_artifacts(
    tool_usage_patterns: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """導入済み推奨 artifact の状態を返す。

    Returns:
        導入済み artifact のリスト。hook_status 含む。
        recommendation_id 付きエントリには mitigation_metrics を付加。
    """
    from tool_usage_analyzer import check_artifact_installed

    installed = []
    # package 経由で参照することで `mock.patch("discover.RECOMMENDED_ARTIFACTS", ...)` 既存テストに追従
    from . import RECOMMENDED_ARTIFACTS as _ARTIFACTS  # noqa: PLC0415
    for artifact in _ARTIFACTS:
        check_result = check_artifact_installed(artifact)
        if not check_result["installed"]:
            continue

        entry: Dict[str, Any] = {
            "id": artifact["id"],
            "description": artifact["description"],
            "status": "active",
        }

        # data_driven な artifact には hook_status を付加
        if artifact.get("data_driven") and tool_usage_patterns:
            hook_status = tool_usage_patterns.get("hook_status")
            if hook_status:
                entry["hook_status"] = hook_status

        # recommendation_id 付きエントリには mitigation_metrics を付加
        rec_id = artifact.get("recommendation_id")
        if rec_id:
            entry["recommendation_id"] = rec_id
            metrics = _compute_mitigation_metrics(
                rec_id, tool_usage_patterns, check_result,
            )
            entry["mitigation_metrics"] = metrics

        installed.append(entry)
    return installed


def _compute_mitigation_metrics(
    recommendation_id: str,
    tool_usage_patterns: Optional[Dict[str, Any]],
    check_result: Dict[str, Any],
) -> Dict[str, Any]:
    """recommendation_id に応じた条件別メトリクスを算出する。"""
    metrics: Dict[str, Any] = {
        "mitigated": True,
        "recent_count": 0,
        "content_matched": check_result.get("content_matched"),
    }

    if not tool_usage_patterns:
        return metrics

    if recommendation_id == "builtin_replaceable":
        builtin = tool_usage_patterns.get("builtin_replaceable", [])
        metrics["recent_count"] = sum(
            item.get("count", 0) for item in builtin
        )
    elif recommendation_id == "sleep_polling":
        repeating = tool_usage_patterns.get("repeating_patterns", [])
        metrics["recent_count"] = sum(
            item.get("count", 0) for item in repeating
            if "sleep" in item.get("pattern", "").lower()
        )

    return metrics
