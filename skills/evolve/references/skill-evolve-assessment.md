# スキル自己進化適性判定（Step 3.6 詳細）

`skill_evolve_assessment()` は全カスタムスキルの自己進化適性を5項目（各1-3点、15点満点）でスコアリングする。

## 判断複雑さ cache の最新化（任意・通常は 0 コール）

[ADR-037] Phase 1c により判断複雑さ（judgment_complexity）軸の claude -p を全廃した。`compute_llm_scores`
は LLM-free（cache-read + 静的フォールバック）になり、evolve バッチはキャッシュ値で完走する。LLM 品質の
判断複雑さで採点したい場合のみ、assessment の前にファイルベース2相で cache を最新化する（任意・cache が
新しければ 0 コール）:

```python
import os, sys
sys.path.insert(0, os.path.join(os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd(), "scripts", "lib"))
from pathlib import Path
from skill_evolve import emit_judgment_requests, ingest_judgment_scores
proj = Path.cwd()
skill_dirs = [...]  # 評価対象スキルの SKILL.md 親ディレクトリ群
emit = emit_judgment_requests(proj, skill_dirs)  # static / 欠落のみ emit
# emit["requests"] の各 prompt を Phase B でインライン採点（claude -p なし＝subscription 課金）し、
# {skill_name: "<1-3>"} を作って:
ingest_judgment_scores(proj, emit["requests"], responses)
```

> 件数が多い場合は batch_guard と同じく LLM_BATCH_GUARD ルールに従い、件数・推定トークンを先に提示して承認を取る。

## batch_guard_trigger 検出（優先処理）

`result.phases.skill_evolve.batch_guard_trigger` が `null` でない場合、LLM 評価対象スキルが多すぎるため
以下のインタラクティブフローを実行してから evolve を再実行する:

1. グループ表を表示する（origin / スキル数 / 推定トークン / スキル名一覧）
   `already_denied` に含まれるスキルは「今回自動スキップ済み」と明示する
2. AskUserQuestion でグループごとに選択させる:
   - 「評価する（このまま続行）」
   - 「今回のみスキップ」
   - 「永続スキップ（denylist に追加）」
3. 永続スキップを選んだスキルがある場合（`_plugin_root` は `~/.claude/rl-anything` または `plugin_root.py` で解決できる実際のパス）:
   ```python
   python3 -c "
   import os, sys; sys.path.insert(0, os.path.join(os.environ.get('CLAUDE_PLUGIN_ROOT') or os.getcwd(), 'scripts', 'lib'))
   from skill_evolve.denylist import add_to_denylist
   add_to_denylist(['skill-a', 'skill-b'])
   print('denylist に追加しました')
   "
   ```
4. 「今回のみスキップ」と「永続スキップ」の両方のスキル名を `--skip-skills` に渡し、**必ず `--confirmed-batch` を付けて** evolve.py を再実行する（`--confirmed-batch` がないと guard が再発火する）:
   ```
   python3 evolve.py --confirmed-batch [--skip-skills=skill-a,skill-b] --output /tmp/rl_evolve_out.json [既存の引数]
   ```
   （Step 1 同様 `--output` 必須。新しい full result は `/tmp/rl_evolve_out.json` に上書きされ、stdout は1行サマリのみ）
5. 新しい result（`/tmp/rl_evolve_out.json`）で以降のステップを継続する

## batch_guard_trigger が null の場合（通常）

以下のサマリを確認する:

- **already_evolved**: 既に自己進化パターンが組み込まれたスキル数
- **high_suitability**: 適性高（12-15点）のスキル数 → Compile で変換を推奨
- **medium_suitability**: 適性中（8-11点）のスキル数 → ユーザー判断に委ねる
- **rejected**: アンチパターン2件以上該当で変換非推奨

適性高/中のスキルがあれば `skill_evolve_candidate` issue として Remediation パイプラインに注入され、Step 5.5 で変換提案が生成される。
