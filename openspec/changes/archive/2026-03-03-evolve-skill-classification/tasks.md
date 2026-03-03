## 1. classify_artifact_origin ユーティリティ追加

- [x] 1.1 `scripts/audit.py` に `classify_artifact_origin(path: Path) -> str` 関数を追加。判定ロジック: plugins/cache/ → plugin, ~/.claude/skills/ → global, その他 → custom
- [x] 1.2 `CLAUDE_PLUGINS_DIR` 環境変数フォールバックを実装
- [x] 1.3 `classify_artifact_origin` のユニットテストを追加（plugin / global / custom / 環境変数の4パターン）

## 1.5. `/scripts/prune.py` を削除し統一

- [x] 1.5.1 `/scripts/prune.py`（旧・parent_skill 未対応）を削除し、`skills/prune/scripts/prune.py` に統一
- [x] 1.5.2 `/scripts/prune.py` が他ファイルから import されていないことを確認（grep で検証）→ `scripts/evolve.py` と `skills/evolve/scripts/evolve.py` の sys.path を修正

## 2. prune.py のプラグインスキル除外

- [x] 2.1 `detect_zero_invocations()` で `classify_artifact_origin` を呼び出し、origin == "plugin" のスキルを `zero_invocations` から除外
- [x] 2.2 `run_prune()` の戻り値に `plugin_unused` キーを追加し、プラグイン由来の未使用スキルを格納
- [x] 2.3 prune のテストを修正・追加（プラグインスキルが zero_invocations に含まれないことを検証）

## 3. evolve レポートの出自別セクション

- [x] 3.1 `skills/evolve/SKILL.md` のレポート出力テンプレートに Custom / Plugin / Global の3セクションを追加
- [x] 3.2 `skills/prune/SKILL.md` に `plugin_unused` の表示ロジックを追加

## 4. frontmatter 標準化

- [x] 4.1 `skills/evolve/SKILL.md` に YAML frontmatter 追加（name, description, disable-model-invocation: true）
- [x] 4.2 `skills/prune/SKILL.md` に YAML frontmatter 追加
- [x] 4.3 `skills/discover/SKILL.md` に YAML frontmatter 追加
- [x] 4.4 `skills/audit/SKILL.md` に YAML frontmatter 追加
- [x] 4.5 `skills/feedback/SKILL.md` に YAML frontmatter 追加
- [x] 4.6 `skills/backfill/SKILL.md` に YAML frontmatter 追加
- [x] 4.7 `skills/update/SKILL.md` に YAML frontmatter 追加
- [x] 4.8 `skills/version/SKILL.md` に YAML frontmatter 追加
- [x] 4.9 `skills/evolve-fitness/SKILL.md` に YAML frontmatter 追加

## 5. commands 削除（SKILL.md に一本化）

- [x] 5.1 `.claude/commands/opsx/` ディレクトリを削除（apply.md, archive.md, explore.md, propose.md, verify.md）
- [x] 5.2 openspec-* SKILL.md はインストールスキルのため変更対象外と確認（自発的に呼ぶスキルのため description 補強不要）

## 6. 検証

- [x] 6.1 `python3 -m pytest skills/ -v` で既存テスト全パス確認（161 passed）
- [ ] 6.2 `--dry-run` で evolve を実行し、出自別セクションが正しく表示されることを確認
- [ ] 6.3 `/rl-anything:openspec-apply-change` 等で SKILL.md 経由の呼び出しが動作することを確認
