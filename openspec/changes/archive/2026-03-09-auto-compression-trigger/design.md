Related: #21

## Context

audit スキルは bloat check レポート（CLAUDE.md/MEMORY.md 行数、rules/skills 総数）を出力し、`scripts/bloat_control.py` の `bloat_check()` がプログラマティックに肥大化を検出できる。auto-evolve-trigger（Gap 4）により session_end 時に evolve/audit の実行提案が可能になった。しかし bloat 検出は `/audit` 手動実行時のみで、セッション間の自動検出はない。

既存のトリガーエンジン（`trigger_engine.py`）は session_end / corrections / audit_overdue の3条件を評価し、pending-trigger.json 経由で次回 SessionStart に提案を配信する。同じパターンに bloat 条件を追加する。

## Goals / Non-Goals

**Goals:**
- セッション終了時に bloat_check() を呼び出し、肥大化を自動検出して圧縮アクションを提案
- 既存の trigger_engine パターン（クールダウン・設定カスタマイズ・履歴記録）を再利用
- bloat 種別（memory/rules/skills/claude_md）に応じた適切なアクション提案

**Non-Goals:**
- 圧縮アクションの自動実行（提案のみ。自動実行は Gap 2 Ph5 Graduated Autonomy で対応）
- bloat_check() 自体の改善・新しい bloat 検出パターンの追加
- Scope Advisor / Plugin Bundling（roadmap の別 Gap）

## Decisions

### D1: evaluate_session_end() に bloat 条件を統合 vs 別関数

**選択: `evaluate_session_end()` 内に統合**

理由: session_end 評価は Stop hook から1回だけ呼ばれ、結果は1つの pending-trigger.json に書き出される。bloat を別関数にすると呼び出し側で結果をマージする必要がある。既存の reasons/actions リストに追加するのが最もシンプル。

ただし bloat_check() の呼び出しは条件分岐の末尾に配置し、他のトリガーが既に発火している場合もメッセージに bloat 情報を追加する。

### D2: bloat_check() の project_dir 引数

**選択: `CLAUDE_PROJECT_DIR` 環境変数から取得**

理由: Stop hook は `CLAUDE_PROJECT_DIR` を受け取る。trigger_engine は直接呼び出されるため、session_summary.py が project_dir を渡す。

**補足: keyword-only パラメータ**
既存の呼び出し元を破壊しないよう、`project_dir` は keyword-only パラメータとして追加する:
```python
def evaluate_session_end(state=None, *, project_dir=None):
```
`project_dir=None`（未設定）の場合は bloat 評価をスキップする。

### D3: bloat トリガーの閾値

**選択: `bloat_control.BLOAT_THRESHOLDS` を single source of truth とし、trigger_config には `enabled` のみ**

```python
DEFAULT_TRIGGER_CONFIG["triggers"]["bloat"] = {
    "enabled": True,
}
```

閾値を trigger_config に複製すると `bloat_control.BLOAT_THRESHOLDS` との DRY 違反になる。`evaluate_bloat()` は `bloat_control.bloat_check()` をそのまま呼び出し、閾値管理は bloat_control.py に委譲する。

### D4: bloat トリガーのアクション

**選択: 全種別で `/rl-anything:evolve` を提案**

理由: evolve の Compile ステージが全レイヤーの問題（bloat 含む）に対応し、remediation で修正アクションを生成する。prune も evolve 内で呼ばれる。ユーザーが1コマンドで対処できる。

### D5: bloat トリガーのクールダウン

**選択: 専用 reason `"bloat"` で既存のクールダウン機構を利用**

bloat は短期間で解消されにくいため、毎セッションで警告が出ると煩わしい。クールダウン（デフォルト24h）で重複を防止。

**トレードオフ**: reason `"bloat"` は全 bloat サブタイプ（memory/rules/skills/claude_md）で共有される。あるサブタイプで発火後、別サブタイプが閾値超過してもクールダウン内は抑制される。これは意図的な設計判断で、bloat 警告の頻度を抑えてユーザー体験を優先する。

### D6: bloat_check() の import パターン

**選択: lazy import（関数内 try/except ImportError）**

trigger_engine.py → bloat_control.py → audit.py の transitive import チェーンが発生しうる。モジュールレベル import だと循環や起動時エラーのリスクがある。`evaluate_bloat()` 内で lazy import し、ImportError 時は bloat 評価をスキップする:

```python
def evaluate_bloat(project_dir, config):
    try:
        from scripts.bloat_control import bloat_check
    except ImportError:
        return None  # bloat 評価スキップ
    ...
```

## Risks / Trade-offs

- **[bloat_check() の実行コスト]** → ファイルシステム走査のみ。LLM コストゼロ（ファイルシステム走査のみ）。セッション終了時の追加遅延は無視できるレベル
- **[false positive: 意図的な大量ルール]** → `triggers.bloat.enabled: false` で無効化可能
- **[bloat_check() の import パス]** → D6 の lazy import パターンで ImportError 時はサイレントスキップ
