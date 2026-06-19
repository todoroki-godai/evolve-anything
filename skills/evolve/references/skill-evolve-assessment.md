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
   - 推定トークンは **worst-case（`estimated_tokens`）と cache 反映後の実見込み
     （`estimated_tokens_cache_aware`）を併記**する（#377-1）。例:
     「最悪 ~Nk / cache 反映後 ~Mk tokens（cache fresh `cache_fresh_count` 件は ≈0、
     refresh 必要 `refresh_needed_count` 件のみ Phase B で課金）」。
   - ⚠ **`--confirmed-batch` 再実行そのものは LLM-free**（[ADR-037] で assessment ループは
     cache-read）。これは sentinel 直下の **`rerun_llm_free: true`** フラグで機械可読に明示される（#394）。
     `estimated_tokens*` は後段の Phase B（judgment refresh の emit→inline）+ apply で発生しうる
     **繰り延べコスト**（`estimate_meaning` フィールドが意味を明文化）であって**再実行自体の課金ではない**。
     とくに `cache_fresh_count == 0` のとき `estimated_tokens_cache_aware` は worst-case と同値になるが、
     これは「再実行に Nk かかる」という意味ではない（再実行は `rerun_llm_free` のとおり課金ゼロ）。
     cache-aware の数字だけを「≈0 の根拠」にしない — 再実行ゼロの根拠は `rerun_llm_free` フラグである。
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
4. 「今回のみスキップ」と「永続スキップ」の両方のスキル名を `--skip-skills` に渡し、**必ず `--confirmed-batch` を付けて** 再実行する（`--confirmed-batch` がないと guard が再発火する）。**インストール時に PATH に入る `rl-evolve` ラッパーを使う**（`evolve.py` の実パスを glob 探索しない — #395）:
   ```
   rl-evolve --confirmed-batch [--skip-skills=skill-a,skill-b] --output "$OUT" [既存の引数]
   ```
   （`rl-evolve` は `skills/evolve/scripts/evolve/`（パッケージ）の `main` を呼ぶ薄いラッパー。PATH に無い特殊環境でのみ
   `PYTHONPATH=<plugin_root>/scripts/lib:<plugin_root>/skills/evolve/scripts python3 -m evolve ...` を直接叩く（#531 でパッケージ化したため旧 `evolve.py` 直叩きは不可）。Step 1 同様 `--output` 必須で、
   `$OUT` は Step 1 と同じ PJ 別パス `/tmp/rl_evolve_<slug>.json`（共有固定パスは別 PJ の stale 誤読源, #408-A）。
   新しい full result は `$OUT` に上書きされ stdout は1行サマリのみ）
5. 新しい result（`$OUT`）を Read し、トップレベル `slug` を対象 PJ と照合してから以降のステップを継続する

## batch_guard_trigger が null の場合（通常）

### 出力構造（正準・#395）

`phases.skill_evolve` は **集計（件数）と詳細（配列）の2層**を持つ。スキル名が欲しいときに
集計キーを配列展開して空振りしないよう、どちらが何かを明示する:

- **`assessments[]` が正準の詳細配列**。各要素は `.skill_name`（フィールド名は `skill` ではない）+
  `.suitability`（`high`/`medium`/`already_evolved`/`insufficient_usage`/`rejected`）+ `.scores` 等を持つ。
  **個別スキルを引きたいときは必ず `assessments[]` を見る**。例: high なスキル名 =
  `[a.skill_name for a in assessments if a.suitability == "high"]`。
- **`high_suitability` / `medium_suitability` / `already_evolved` / `insufficient_usage` は件数（int）**で
  あって配列ではない。`high_suitability[].skill` のような展開はできない（#379 の result-schema 契約でも
  これらは int と定義済み）。一目で母数を掴むための集計値。

### サマリの読み方

以下の件数（int）を確認する:

- **already_evolved**: 既に自己進化パターンが組み込まれたスキル数
- **high_suitability**: 適性高（12-15点）のスキル数 → Compile で変換を推奨
- **medium_suitability**: 適性中（8-11点）のスキル数 → ユーザー判断に委ねる
- **insufficient_usage**: 使用実績ゼロ（`usage_count==0`）で保留した件数（#376）
- **rejected**: アンチパターン2件以上該当で変換非推奨（集計は assessments の suitability から算出）

適性高/中のスキルがあれば `skill_evolve_candidate` issue として Remediation パイプラインに注入され、Step 5.5 で変換提案が生成される。
