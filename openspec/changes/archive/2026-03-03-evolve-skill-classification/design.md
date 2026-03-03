## Context

rl-anything の evolve/prune パイプラインは、プロジェクト内の全スキル・ルールを `find_artifacts()` で収集し、
`usage.jsonl` の呼び出し記録と照合して淘汰候補を検出する。

現状の問題:
1. **出自の区別なし**: カスタムスキル（ユーザー手書き）とプラグインスキル（`plugin install` 由来）を同列に扱う
2. **プラグインスキル淘汰の無意味さ**: アーカイブしても `plugin update` で復活する
3. **frontmatter 欠如**: rl-anything 自身の12スキル中9つに frontmatter がなく、Claude の自動起動判定対象外
4. **observe.py の出自記録なし**: Skill 呼び出し時に plugin 由来かどうかを記録していない

## Goals / Non-Goals

**Goals:**
- `find_artifacts()` がスキルの出自（custom / plugin / global）を識別できるようにする
- `detect_zero_invocations()` がプラグイン由来スキルを淘汰対象から除外し、レポートのみ出力する
- rl-anything の全 SKILL.md に適切な YAML frontmatter を追加する
- evolve レポートでスキルの出自別にセクション分けする

**Non-Goals:**
- observe.py の出自記録追加（Phase 2 で対応。現時点ではファイルパスベースの判定で十分）
- commands → skills の統合（`.claude/commands/opsx/` の削除は tasks で対応。design スコープ外）
- スキル推奨メカニズムの実装（既存の discover + prune + frontmatter 自動マッチで十分。不要と判断）
- 他プロジェクトのスキルへの影響

## Decisions

### D1: 出自判定はファイルパスベースで行う

**選択**: ファイルパスのプレフィックスで判定
- `~/.claude/plugins/cache/` 配下 → plugin
- `~/.claude/skills/` 配下 → global
- `<project>/.claude/skills/` 配下 → custom

**代替案**: observe.py で Skill 呼び出し時にメタデータを付与する
→ 却下理由: 過去データに遡及適用できない。ファイルパスなら既存データでも判定可能

### D2: find_artifacts の戻り値に origin フィールドを追加

**選択**: 既存の `Dict[str, List[Path]]` を `Dict[str, List[Dict]]` に変更せず、
別途 `classify_artifact_origin(path: Path) -> str` ユーティリティ関数を追加。
呼び出し側で必要に応じて分類する。

**理由**: 戻り値の型変更は全テスト・全呼び出し元に影響する。ユーティリティ関数なら非破壊的に追加可能。

### D3: プラグインキャッシュパスの検出方法

**選択**: `~/.claude/plugins/cache/` をハードコード + 環境変数 `CLAUDE_PLUGINS_DIR` でオーバーライド可能

**理由**: Claude Code のプラグインキャッシュパスは `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/` で固定。
ただし将来変更に備えて環境変数フォールバックを用意。

### D4: frontmatter は英語 description で統一

**選択**: description は英語で記述。Claude のスキルマッチングは英語の方が安定。
トリガーワードは日英併記。

**代替案**: 日本語 description
→ 却下理由: Claude の内部マッチングは英語ベース。公式ベストプラクティスも英語推奨。

### D5: evolve レポートの出自別セクション

**選択**: prune 結果を3セクションに分割
1. **Custom skills** — 淘汰候補（アーカイブ提案）
2. **Plugin skills** — レポートのみ（「未使用。uninstall を検討？」）
3. **Global skills** — 既存の `safe_global_check` を維持

### D6: `/scripts/prune.py` を削除し `skills/prune/scripts/prune.py` に統一

**選択**: `/scripts/prune.py`（旧・parent_skill 未対応）を削除し、
`skills/prune/scripts/prune.py`（新・parent_skill 対応）に統一する。

**理由**: 旧 prune.py は `classify_artifact_origin` 未対応で DRY 違反。
`skills/prune/scripts/prune.py` が `detect_zero_invocations` と `run_prune` の正式な実装先。
import 元として使われている箇所がないことを確認済み。

## Risks / Trade-offs

- **[Risk] プラグインキャッシュパス変更** → `CLAUDE_PLUGINS_DIR` 環境変数でフォールバック
- **[Risk] find_artifacts がプラグインスキルを重複収集** → プラグインキャッシュ配下はデフォルトで find_artifacts の走査対象外のため問題なし。ただしプラグインが project `.claude/skills/` にもスキルを配置する場合は注意
- **[Trade-off] frontmatter 追加で SKILL.md の行数増加** → 5-8行の増加で影響は軽微
- **[Trade-off] ユーティリティ関数方式は呼び出し側に分類責任** → prune.py と evolve.py の2箇所のみなので許容範囲
- **[Risk] commands 削除で /opsx:apply 等が使えなくなる** → openspec-* SKILL.md 経由で同等機能を提供。プラグイン名前空間で `rl-anything:openspec-apply-change` として呼び出し可能
