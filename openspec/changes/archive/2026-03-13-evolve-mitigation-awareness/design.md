## Context

evolve Step 10.2 はテレメトリから builtin_replaceable / sleep / Bash 割合を検出し、閾値超過時に推奨を出す。`check_hook_installed()` は `check-bash-builtin` hook のみをハードコードで検査しており、sleep 対策や将来の推奨には対応できない。

discover.py には既に `RECOMMENDED_ARTIFACTS` + `detect_recommended_artifacts()` / `detect_installed_artifacts()` が存在し、推奨 artifact の導入/未導入を管理している。この既存構造を拡張して mitigation-awareness を実現する。

## Goals / Non-Goals

**Goals:**
- RECOMMENDED_ARTIFACTS を拡張し、Step 10.2 の推奨アクションとの紐付け（recommendation_id）と対策内容チェック（content_patterns）を追加
- 対策が存在する場合、条件別メトリクスを返す（統一遵守率ではなく condition-specific）
- evolve レポートで「未対策 → 提案」と「対策済み → 検出件数表示」を切り替え
- 閾値をハードコードからモジュール定数に移行

**Non-Goals:**
- 対策の自動生成・自動インストール（既存の remediation が担当）
- hook の中身の AST 解析（パターンマッチで十分）
- skill の内容解析（存在チェックのみ）

## Decisions

### D1: RECOMMENDED_ARTIFACTS 拡張（MITIGATION_REGISTRY 新設を却下）

discover.py の既存 `RECOMMENDED_ARTIFACTS` に `recommendation_id` と `content_patterns` フィールドを追加する。新たな `MITIGATION_REGISTRY` は作らない。

```python
RECOMMENDED_ARTIFACTS = [
    {
        "id": "no-defer-use-subagent",
        "type": "rule",
        "path": Path.home() / ".claude" / "rules" / "no-defer-use-subagent.md",
        "description": "先送り禁止 — background subagent 即時委譲ルール",
        "hook_path": Path.home() / ".claude" / "hooks" / "detect-deferred-task.py",
        "hook_description": "Stop hook: 先送り表現検出 → 会話続行強制",
    },
    {
        "id": "avoid-bash-builtin",
        "type": "rule+hook",
        "path": Path.home() / ".claude" / "rules" / "avoid-bash-builtin.md",
        "description": "Bash Built-in 代替コマンド禁止 — grep/cat/find 等を Built-in ツールに誘導",
        "hook_path": Path.home() / ".claude" / "hooks" / "check-bash-builtin.py",
        "hook_description": "PreToolUse hook: Bash で Built-in 代替可能コマンドを block",
        "data_driven": True,
        # --- 拡張フィールド ---
        "recommendation_id": "builtin_replaceable",
        "content_patterns": ["REPLACEABLE"],  # hook ファイル内のパターン
    },
    {
        "id": "sleep-polling-guard",
        "type": "hook",
        "path": None,  # rule は不要
        "description": "sleep ポーリング検出 — run_in_background + 完了通知待ちを推奨",
        "hook_path": Path.home() / ".claude" / "hooks" / "check-bash-builtin.py",
        "hook_description": "PreToolUse hook: sleep コマンドを検出・警告",
        # --- 拡張フィールド ---
        "recommendation_id": "sleep_polling",
        "content_patterns": [r"\bsleep\b"],
    },
]
```

**理由**: DRY + SSOT。`RECOMMENDED_ARTIFACTS` は既に推奨 artifact の導入状態管理を担っており、同じ概念を `MITIGATION_REGISTRY` として二重管理するのは保守コスト増加。

**代替案比較**:
| 案 | Pros | Cons | 判定 |
|----|------|------|------|
| A: RECOMMENDED_ARTIFACTS 拡張 | SSOT、既存コード再利用 | 1つの構造体の責務が増える | **採用** |
| B: MITIGATION_REGISTRY 新設 | 関心の分離 | 同概念の二重管理、同期コスト | 却下 |
| C: 個別チェック関数 | シンプル | スケールしない、重複ロジック | 却下 |

### D2: check_artifact_installed() — check_hook_installed() の汎用化

`check_hook_installed()` を `check_artifact_installed()` に汎用化し、content_pattern チェックを追加する。既存の `check_hook_installed()` は後方互換のため残す。

```python
def check_artifact_installed(
    artifact: dict,
    *,
    hooks_dir: Path | None = None,
    rules_dir: Path | None = None,
    settings_path: Path | None = None,
) -> dict:
    """推奨 artifact の導入状態を確認する。

    Returns:
        {"installed": bool, "artifacts_found": list[str],
         "content_matched": bool | None}
    """
```

- `artifact["hook_path"]` のファイル存在チェック
- `artifact["path"]` (rule) のファイル存在チェック
- `artifact.get("content_patterns")` がある場合、hook ファイル内容を正規表現でマッチ
- I/O エラー時は `installed=False, content_matched=None` を返す

### D3: 条件別メトリクス（統一遵守率を却下）

統一遵守率 `1 - (違反件数 / 全Bash件数)` は分母が不適切（sleep 5件/Bash 3540件 = 99.9% は無意味）。条件別メトリクスに変更:

| 条件 | メトリクス | 表示 |
|------|-----------|------|
| builtin_replaceable | `mitigated: bool` + `recent_count: int` | 「対策済み — 直近 {N} 件検出」 |
| sleep_polling | `mitigated: bool` + `recent_count: int` | 「対策済み — 直近 {N} 件検出」 |
| bash_ratio | `ratio: float` | 「Bash 割合 {X}%」（比率表示のまま） |

`detect_installed_artifacts()` の返却に `mitigation_metrics` を追加:
```python
{
    "id": "avoid-bash-builtin",
    "description": "...",
    "status": "active",
    "recommendation_id": "builtin_replaceable",
    "mitigation_metrics": {
        "mitigated": True,
        "recent_count": 15,  # 対策後も検出された件数
        "content_matched": True,
    },
}
```

### D4: evolve SKILL.md の Step 10.2 更新

表示切替ロジック。閾値は `tool_usage_analyzer.py` の定数を参照:

```python
# tool_usage_analyzer.py
BUILTIN_THRESHOLD = 10
SLEEP_THRESHOLD = 20
BASH_RATIO_THRESHOLD = 0.40
COMPLIANCE_GOOD_THRESHOLD = 0.90  # 将来の遵守率良好判定用
```

対策済みの場合:
```
"Built-in 代替: 対策済み (hook: check-bash-builtin, rule: avoid-bash-builtin) — 直近 15 件検出"
```

未対策の場合（従来通り）:
```
"Built-in 代替: 243件検出。hook での検出・警告導入を推奨"
```

全対策済みかつ直近検出件数がゼロの場合:
```
"ツール使用: 全対策済み — 検出なし"
```

## Risks / Trade-offs

- **[content_pattern の偽陽性]** → hook に `REPLACEABLE` という変数名があれば対策済みと判定するが、別目的で使われる可能性は極めて低い。パターンは具体的に設定。
- **[RECOMMENDED_ARTIFACTS の肥大化]** → エントリが増えると構造体が複雑に。ただし現時点で 3 エントリ程度であり、10 以下なら問題なし。
- **[条件別メトリクスの表示複雑化]** → 統一指標より表示が長くなるが、正確な情報提供を優先。全対策済み時の 1 行表示で緩和。
