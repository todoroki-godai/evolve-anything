# #625（仮番）: prompt-audit で決まった5件のコード内プロンプト修正 — 設計メモ（巡0）

出典: `audit-code`（コード内 LLM 呼出点の監査）Finding 1・1b-bench・3・4、`audit-skills`（skills/agents 監査）Finding 1・2。
実装は Codex に委譲する前提。本メモは設計レビュー1巡（別系統）の入口。

---

## 0. 完成条件（round 0）

### ① 守る対象
evolve-scorer 系（`scorer_prompts.py` / `output_evaluator.py` / `agents/evolve-scorer.md`）と
`safe_llm_call` 系（`judge_runner.py` / `verbosity/judge.py`）の、**算術をモデルにやらせている
箇所とJSON強制の自然言語scaffoldを、コード側の決定論計算とCLIネイティブ構造化出力に置き換える**こと。
副次的に `critical_instruction_extractor.py` のCoT誘導文と `skills/tier/SKILL.md` の履歴語りを除去する。

### ② 信頼境界
脅威に数えるのは**自分たちの実装ミス**（置換後にスコア分布が意図せず変わる・パーサが追従漏れする・
既存の安全設定を弱める）のみ。悪意ある入力への耐性は本変更の対象外（`safe_llm_call.py` の安全設定は
変更しない＝下記③）。

### ③ 対象外
- `safe_llm_call.py` の `--tools ""` / `--settings` deny / `--strict-mcp-config` / `--safe-mode` 等の
  安全設定（`live-checkout-guard.md` 同様、実測に基づく防御コードのため変更しない。`--json-schema` は
  **追加**であり既存フラグの置換ではない）
- Finding 2（`score_noise.py._run_claude_prompt` の fossil）— 別issue起票を推奨（監査報告どおり）
- `scripts/lib/semantic_detector.py` / `scripts/rl/fitness/constitutional.py` のJSON強制文（Phase B が
  インライン assistant 応答のため `--json-schema` を適用できない。監査報告の Low confidence flag のまま）
- `skills/evolve-loop-orchestrator/scripts/run_loop.py` のパーサ改修（scope外。ただし Finding 1 の
  影響が及ぶため、本メモの変更5には**含めず**、実装完了後に別途フォローアップとして issue へ切り出す
  ことを [Must] とする＝下記④）
- `agents/evolve-scorer.md` の「その他のプロジェクト」ドメインでの自由記述ウェイト設計（下記④の
  blocking b で扱う）

### ④ blocking
- (a) 変更後、評価スコアの意味（0.0〜1.0スケール・軸の相対重み）が旧版と食い違う
- (b) `agents/evolve-scorer.md` の4ドメイン別重みテーブルのうち「その他のプロジェクト」（モデルが
  自分で重みを設計する自由記述枠）は、weightをコード側で固定できない。**この1ドメインだけは
  Finding 2（agents/evolve-scorer.md）の対象から明示的に除外し、audit report に記載のない新たな
  制約（固定4項目への強制）を持ち込まない**（think-before-coding.md：合意済み要件を勝手に変えない）
- (c) `scorer_prompts.py` の応答形式変更後、`score_noise.py` と `run_loop.py` の両方の消費者が
  追従できていない
- (d) `--json-schema` 追加後、`safe_llm_call.py` の既存安全設定（decisive test）が退行する
- (e) 5件のうち1件でも、変更前の文字列を直接assertするテストの追従漏れがある

### ⑤ 検証方法
各変更に陰性試験1件以上＋陽性対照1件（下記2.末尾）。`safe_llm_call.py` は decisive test
（`/tmp` 乱数秘密ファイル非漏洩）を **schema追加後に再実行**して(d)を確認する。

### ⑥ 目的文の物差しで削る量
CLAUDE.md の4つの柱（自律進化・フィードバック・直接パッチ最適化・fleet観測）のいずれにも
**直接**寄与する定量指標は無い。evolve-scorer の採点精度・`safe_llm_call` 経路のコスト効率は
柱の測定式（柱1〜4）に組み込まれていない代理指標であり、目的指標と同じ単位の直接観測値・
再現可能な算式のいずれも用意できない。**⑥ = 0**。よって本作業は「磨き込み」区分
（`pillars-before-polish.md`）に属する。着手判断は柱の完通を妨げないこと（ファイル数5・
既存テストの追従のみで新規機構を増やさない）を根拠にユーザーへ確認済みの前提で進める。

---

## 1. タスク1（実測）: `--json-schema` 併用の probe 結果

**実行時刻**: 2026-09-05T00:45:33Z（UTC、`date -u` 実測）

**コマンド全文**（`safe_llm_call.call_claude_headless` が実際に組む引数列に `--json-schema` を追加）:

```bash
claude -p '「テスト」という1語について {"is_test": true} 形式で判定してください' \
  --model haiku \
  --tools "" \
  --settings '{"permissions": {"deny": ["Bash","Read","Write","Edit","Glob","Grep","WebFetch","WebSearch","Task","NotebookEdit","BashOutput","KillBash","TodoWrite","SlashCommand","AskUserQuestion","ExitPlanMode"], "defaultMode": "default"}}' \
  --strict-mcp-config \
  --safe-mode \
  --no-session-persistence \
  --json-schema '{"type":"object","properties":{"is_violation":{"type":"boolean"},"reason":{"type":"string"}},"required":["is_violation","reason"]}'
```

**結果**（1回目で成功。再試行不要）:
- (a) 終了コード: `0`
- (b) stdout: `{"is_violation":false,"reason":"「テスト」は単なる日本語の一般的な単語であり、試験・検査・テストを意味する通常の表現です。セキュリティ違反ではありません。"}`
  → schema どおりの JSON がそのまま stdout に出た（前後の説明文・コードフェンス無し）
- (c) `--output-format json` は不要。`claude --help` 実測（"JSON Schema for structured output
  validation"）どおり、`-p` の既定 `--output-format text` のまま `--json-schema` を渡すだけで
  stdout に生の JSON 文字列が返る。`structured_output` フィールド等のエンベロープは**発生しない**
  （`--output-format json` を付けた場合の挙動は本 probe では検証していない＝未実測。付ける必要が
  ないため検証対象から外した）
- 副次的に観測した warning（本題と無関係、無視してよい）:
  `Permission deny rule "SlashCommand" matches no known tool — check for typos.`
  （既存 `BUILTIN_TOOL_NAMES` の `SlashCommand` エントリに対する CLI 側の既知の noise。
  `--settings` deny 自体は無効化されていない＝終了コード0・secretリークなしで確認）

**設計への反映**: `call_claude_headless` の戻り値（`out.stdout.strip()`）は json_schema 指定時も
**そのまま JSON 文字列**になるため、呼び出し側（`judge_runner.py` / `verbosity/judge.py`）の
既存パーサ（`parse_verdicts_result` / `_parse_json` 等の code-fence 剥がし処理）は**そのまま動作する**
（schema違反時のフォールバック経路として、パーサ自体は削除しない＝下記2-2）。

---

## 2. 変更5件

### 変更1 — `scorer_prompts.py`: 算術ルーブリック→項目JSON返却＋Python側 `compute_axis_total`

**対象**: `scripts/lib/scorer_prompts.py:22-67`（`DEFAULT_AXIS_PROMPTS`）

**変更前**（例: technical、`scorer_prompts.py:37`）:
```
5項目の平均を total として、数値のみ回答してください（例: 0.75）
```

**変更後**:
```
以下の JSON のみで、各項目のスコアを返してください（total は算出不要）:
{{"clarity": <float>, "completeness": <float>, "consistency": <float>, "edge_cases": <float>, "testability": <float>}}
```
（domain/structure も同型。3軸とも `{content}` placeholder は維持）

**重み**: 監査報告 diff の重みをそのまま採用する根拠は「現行プロンプトが単純平均だから」だが、
実装前提を確認した結果、**現行プロンプトは単純平均**（`scorer_prompts.py:37,51,66` すべて
「N項目の**平均**を total」であり重み付けの言及なし）。よって
`AXIS_ITEM_WEIGHTS` は**等分**にする（technical/structure は各0.20×5項目、domainは各0.25×4項目）。
監査報告 diff 案の 30/25/20/15/10% 等の重みは `agents/evolve-scorer.md`（変更4）の値であり、
`scorer_prompts.py` 側には存在しないため転用しない。

**追加**:
```python
AXIS_ITEM_WEIGHTS: Dict[str, Dict[str, float]] = {
    "technical": {"clarity": 0.20, "completeness": 0.20, "consistency": 0.20, "edge_cases": 0.20, "testability": 0.20},
    "domain": {"accuracy": 0.25, "utility": 0.25, "maintainability": 0.25, "coverage": 0.25},
    "structure": {"format": 0.20, "length": 0.20, "examples": 0.20, "references": 0.20, "convention": 0.20},
}

def compute_axis_total(axis: str, item_scores: Dict[str, float]) -> float:
    weights = AXIS_ITEM_WEIGHTS[axis]
    return round(sum(item_scores.get(k, 0.0) * w for k, w in weights.items()), 4)
```

**追従が要る呼出側・テスト**（`grep -rn` 実測。下記コマンドで確認済み）:
- `scripts/lib/score_noise.py:130`（`parse_responses(requests, requests, parser=parse_score)`）と
  `score_noise.py:82`（`build_scoring_requests`）／`115`（`aggregate_from_responses`）— 応答が
  「1軸=1スカラー文字列」から「1軸=項目JSON」に変わるため、`parse_score`（`llm_broker` から
  re-export）ではなく `compute_axis_total` を通す新しい parser に差し替える必要がある
  （監査報告の diff 2 案どおり `_parse_axis_response` ヘルパーを追加）
- `scripts/lib/tests/test_score_noise.py`（`test_parse_score_extracts_float` 系・
  `test_aggregate_from_responses_roundtrip` 系・`test_build_scoring_requests_shape` 系）—
  fixture の `responses` 値をスカラー文字列から項目JSON文字列へ更新する必要がある
- `scripts/lib/tests/test_scorer_prompts.py`（`test_default_prompts_have_three_axes` 等）—
  `AXIS_ITEM_WEIGHTS` の新規契約テスト（各軸の重み合計が1.0）を追加する
- **scope外だが追従が必要**: `skills/evolve-loop-orchestrator/scripts/run_loop.py:186`
  （`from scorer_prompts import DEFAULT_AXIS_WEIGHTS as AXIS_WEIGHTS, get_axis_prompts`）は
  `get_axis_prompts()` の返り値をそのままモデルに渡してプロンプトを組んでいるため、
  プロンプト文言が変われば同ファイルが期待する応答形式（スカラー `total`）も変える必要がある。
  ただし `run_loop.py:155-179` を読む限り `axis_scores.get("technical", FALLBACK_SCORE)` という
  **既に集約済みのスカラー**を受け取る箇所しかなく、`run_loop.py` 自身が LLM 応答を直接パースする
  コードは今回の grep 範囲では確認できなかった（`skills/` はスコープ外のため深追いしていない）。
  **[Must] 本変更を実装するPRでは `run_loop.py` を直接改修しないが、`scorer_prompts.py` の
  応答契約を変えた事実と、`run_loop.py` が同じ `DEFAULT_AXIS_PROMPTS` を消費している事実を
  明記したフォローアップ issue を実装完了時に必ず起票する**（起票のみ・着手は別途ユーザー確認）

### 変更2 — `output_evaluator.py`: 3テンプレートから `total` 除去・`_score_axis` で Python 計算

**対象**: `scripts/bench/output_evaluator.py:78-124`（`_TECHNICAL_TEMPLATE`/`_DOMAIN_TEMPLATE`/`_STRUCTURE_TEMPLATE`）、`198-211`（`_score_axis`/`evaluate`）

**変更前**（例: `_TECHNICAL_TEMPLATE`、`output_evaluator.py:78-101`）: JSON に
`"total": <重み付き平均>` を含めさせ、重み表（30/25/20/15/10%等）をプロンプト中の Markdown表として明示。

**変更後**:
- 3テンプレートとも `"total": <重み付き平均>` の除去（`rationale` は残す）
- `_score_axis`（`output_evaluator.py:181-197`）の `key="total"` 前提を廃し、
  各テンプレートの重み表を `_AXIS_WEIGHTS` 定数として Python 側に複製し、`_score_axis` 内で
  `sum(parsed.get(k, 0.0) * w for k, w in weights.items())` を計算してから 0.0〜1.0 にクランプする
  （現行の `parsed[key]` を `float()` する箇所を置換。`key not in parsed` の早期 return は
  「必須サブ項目が1つでも欠けたら None」に変える＝クランプ前の欠損を潰さない）

**重み**: このテンプレートは監査報告どおり**明示的に重み表を持つ**ため、監査報告 diff 案の
重みをそのまま Python 側 `_AXIS_WEIGHTS` に転記する（technical: 30/25/20/15/10、
domain: 30/30/20/20、structure: 25/25/25/25。プロンプト文中の表記と1対1で一致させる）。

**追従が要る呼出側・テスト**（`grep -rn` 実測）:
- `scripts/bench/run_benchmark.py:156` 付近が `OutputEvaluator.evaluate` を呼ぶ唯一の生きた
  呼出元（監査報告の棚卸し#10）。`evaluate()` のシグネチャ・戻り値型（`AxisScores`）は変更しない
  ため `run_benchmark.py` 自体の改修は不要と見立てるが、**実装時に `AxisScores` の生成箇所
  （`evaluate()` 内の `technical[0]` 等）が変わらないことを diff で確認すること**
- 専用テストファイルは監査報告のとおり見当たらない（`scripts/tests/test_run_benchmark.py` に
  bench マーカーテストがあるか要確認。`test_run_benchmark.py` は上記 grep で
  `output_evaluator`/`call_haiku` を参照している旨ヒットしたため、実装時に該当箇所を
  Read し、`total` を直接assertしていないか確認する）

### 変更3 — `safe_llm_call.py` に `json_schema` 引数、`judge_runner.py`/`verbosity/judge.py` に schema 付与、プロンプト文言短縮

**対象**: `scripts/lib/safe_llm_call.py:128-177`（`call_claude_headless`）、
`scripts/lib/correction_semantic/judge_runner.py:84`（`call_haiku`）、
`scripts/lib/verbosity/judge.py:76`（`call_haiku`）、
`scripts/lib/correction_semantic/prompt.py:150`付近（"出力は厳格な JSON のみ..."）、
`scripts/lib/verbosity/judge.py:46-58`（`PROMPT_HEAD` の "マークダウンや説明文は一切付けない。"）

**タスク1の結果を反映した確定設計**（`--output-format json` 不要。監査報告の未実測事項が解消）:

```python
# safe_llm_call.py（call_claude_headless のシグネチャ変更。Optional の import 追加が必要）
def call_claude_headless(
    prompt: str, *, model: str = "haiku", timeout: int = DEFAULT_TIMEOUT_SECONDS,
    json_schema: Optional[str] = None,
) -> str:
    ...
    cmd = [
        "claude", "-p", prompt, "--model", model,
        "--tools", "",
        "--settings", _safe_settings_json(),
        "--strict-mcp-config",
        "--safe-mode",
        "--no-session-persistence",
    ]
    if json_schema is not None:
        cmd += ["--json-schema", json_schema]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    ...
```

`judge_runner.py` は `call_haiku` に verdicts 配列の schema（`index`/`is_correction`/`idiom`/
`category`（`CATEGORY_ENUM` を `enum` に反映）/`reason`）を追加。`verbosity/judge.py` は
`i`/`verbose`/`patterns`/`note` の schema を追加（監査報告 diff 2 のとおり）。

**プロンプト文言短縮**: `prompt.py` の「出力は厳格な JSON のみ（前後に説明文を付けない）」、
`verbosity/judge.py` の「マークダウンや説明文は一切付けない。」を
「以下の形式で判定結果を返してください:」程度へ短縮する。**完全削除はしない**（監査報告どおり
形式契約の言及自体は残す）。

**フォールバック設計（[Must]）**: schema 強制が効かない・CLIバージョン差異で `--json-schema` が
無視される可能性に備え、**パーサ（`parse_verdicts_result`/`_validate_item` 等）は変更しない**。
schema はモデル応答の質を上げる追加防御であり、パース失敗時の「未判定のまま次回に残す」既存契約
（`ok=False` → drain 持ち越し）は温存する。

**追従が要る呼出側・テスト**（`grep -rn` 実測）:
- `scripts/lib/tests/test_safe_llm_call.py`（`call_claude_headless("prompt", model="haiku")` 等の
  既存呼出しは `json_schema` 未指定＝デフォルト `None` なので**そのまま緑を維持できる**。
  加えて `json_schema="..."` を渡した際に `cmd` へ `--json-schema` が追加されることを確認する
  新規テストを1件追加する）
- `scripts/lib/tests/test_correction_semantic_judge_runner.py` — `call_haiku` の呼出しは
  `judge_runner.call_haiku` 自体を monkeypatch しているため（`_boom`/`_fake_call_haiku` 系）、
  `call_haiku` のシグネチャや内部実装変更の影響を受けない（`call_haiku(prompt, model="haiku")`
  という**呼び出し側**の形は変えない設計にする＝schema は `call_haiku` 内部で固定 schema を
  `call_claude_headless` に渡すだけで、`call_haiku` 自体の引数は増やさない）
- `scripts/lib/tests/test_verbosity.py:505-521`
  （`test_call_haiku_delegates_to_safe_llm_call`）— `_fake(prompt, *, model="haiku", **kwargs)`
  と `**kwargs` を受けるため、`call_claude_headless` に `json_schema=` が渡っても**そのまま緑**。
  ただし `judge.call_haiku` 内部で schema を固定引数として渡すよう変更するため、
  `captured` に `json_schema` キーを追加して schema が実際に渡っていることを検証する新規
  assertion を1件追加する
- **decisive test の再実行（(d) 対応）**: `safe_llm_call.py` docstring が記録する安全性実測
  （秘密ファイル非漏洩・hook 非発火）を、`json_schema` 付きの呼び出しでも再実施し、
  結果をモジュール docstring に追記する（本メモ §1 の probe は decisive test そのものではない
  ため、実装 PR で別途実施すること＝[Must]）

### 変更4 — `critical_instruction_extractor.py` の CoT 誘導文除去

**対象**: `scripts/lib/critical_instruction_extractor.py:389-390`（`_build_judge_prompt`、
実測行番号。監査報告の「380-394」はブロック全体の行範囲、削除対象2行は下記）

**変更前**:
```python
        f"direct scoring: 違反していれば is_violation=true、していなければ false。\n"
        f"Chain of Thought: まず理由を考え、次に判定を出してください。\n\n"
```

**変更後**:
```python
        f"違反していれば is_violation=true、していなければ false。\n\n"
```

**追従が要る呼出側・テスト**: `scripts/tests/test_critical_instruction_extractor.py` を grep した
結果、`_build_judge_prompt` の出力文字列を直接 assert する既存テストは**確認できなかった**
（`grep -n "_build_judge_prompt\|direct scoring\|Chain of Thought"` がヒット0件）。よって
既存テストの追従は不要と見立てるが、実装 PR では最終確認として同じ grep を再実行することを
[Must] とする（このメモの grep 実行時刻以降にテストが追加されている可能性を排除するため）。
`emit_violation_judge_requests`/`ingest_violation_judges` のロジック自体は変更しない。

### 変更5 — `agents/evolve-scorer.md`: 返却JSONから `total` 除去・合成をStep4の決定論処理へ

**対象**: `agents/evolve-scorer.md:87-96` 相当（technical-scorer の返却JSON例）と、
domain-scorer（`{ "criterion_1": N, ..., "total": N }`）・structural-scorer
（`{ "format": N, ..., "total": N }`）の返却JSON例（それぞれ Step 3 内の該当ブロック）

**変更前**（technical-scorerの例。実測箇所）:
```
{ "clarity": N, "completeness": N, "consistency": N, "edge_cases": N, "testability": N, "total": N }
各値は 0.0〜1.0。total は重み付き平均。
```

**変更後**:
```
{ "clarity": N, "completeness": N, "consistency": N, "edge_cases": N, "testability": N }
各値は 0.0〜1.0（各観点の質的判断のみを返す。重み付き平均はオーケストレーターが Step 4 で計算する）。
```
domain-scorer・structural-scorer も同型（`total`除去）。**ただし blocking (b) のとおり、
domain-scorerの「その他のプロジェクト」枠（`agents/evolve-scorer.md` の「#### その他のプロジェクト」
節、モデルが自由に4項目・重みを設計する）は、重み自体がモデル生成物であるため、Step 4 の
決定論計算に必要な重みをコード側に固定できない。この枠を選んだ場合の domain-scorer だけは
`total` を返させたまま残す**（4種の固定ドメイン=game/api/bot/docsは`total`除去対象、
「その他」だけ例外として明記する）。

Step 4（`agents/evolve-scorer.md` の「### Step 4: 統合スコア算出」節）は変更しない
（既に `integrated_score = technical.total * 0.4 + domain.total * 0.4 + structure.total * 0.2`
という決定論記述だが、technical/structural の `total` は各サブエージェントが返す値から
「オーケストレーターが3項目の重み付き平均から算出した値」に文言を差し替える必要がある。
domain は上記例外のため既存記述のまま「domain-scorerが返したtotal」を使う旨を明記する）。

**追従が要る呼出側・テスト**: `agents/evolve-scorer.md` は Markdown 指示書でありコードから
直接消費されない（`evolve-anything:evolve-scorer` エージェント定義そのもの）。テストは無い
（`grep -rn "evolve-scorer.md" scripts hooks skills` で参照コードが無いことを実装時に再確認する）。
skill-vuln-scan・invalid_frontmatter 等の frontmatter 検査には抵触しない変更（本文のみ）。

### 変更6 — `skills/tier/SKILL.md:17-20` の履歴語り書き換え

**対象**: `skills/tier/SKILL.md:17-20`

**変更前**:
```
`~/.claude/model-tiers.json`（CLI: `bin/evolve-tier`、#193）が一元管理する。以前は
model-routing rule・各 PJ の agent frontmatter・settings.json に散在し、モデル変更のたびに
手動で全ファイルを追従する必要があった（2026-07-10 opus 4.8 廃止時に HEAD が fable⇄sonnet を
同日中に往来した実例）。**このスキル自体はファイルを直接編集しない**
```

**変更後**（監査報告の diff どおり）:
```
`~/.claude/model-tiers.json`（CLI: `bin/evolve-tier`、#193）が一元管理する。分散管理（rule・
各 PJ の agent frontmatter・settings.json に個別記載）だとモデル変更のたびに全ファイルへの
手動追従が必要になり、取りこぼしによる設定ズレが起きる。**このスキル自体はファイルを直接編集しない**
```

**追従が要る呼出側・テスト**: SKILL.md 本文のfrontmatter・トリガー文言は変更しないため
`invalid_frontmatter` 等の検査には抵触しない。参照コード無し（Markdown本文のみ）。

---

## 3. 検査の変異要求（実装者に課す。issue #625 の完成条件⑤に対応）

各変更にテストを追加し、以下の**陰性試験4件＋陽性対照1件以上**を実際に適用して結果を報告すること
（`verify-checks-by-breaking.md`: 緑のまま残ったものが1件でもあれば完了扱いにしない）。

1. **陰性試験①**: `scorer_prompts.py`/`output_evaluator.py` のプロンプト文字列に
   「5項目の平均を total として、数値のみ回答してください」を書き戻す変異を適用し、
   `compute_axis_total`/`_AXIS_WEIGHTS` を使う新テスト（項目JSONのみを渡して期待totalと
   一致することを assert するテスト）が**赤くなる**ことを確認する
   （プロンプト文言自体を直接assertするテストが無ければ、代わりに「モデル応答に`total`
   キーが含まれていても無視されコード側の値が優先される」ことを確認するテストで代替可）
2. **陰性試験②**: `judge_runner.py`/`verbosity/judge.py`/`agents/evolve-scorer.md` いずれかで
   `total` をモデル応答（fixture）に含めた状態で、Python側計算値が優先されることを確認する
   テストを書き、意図的に「モデルの`total`をそのまま採用する」変異を適用して赤くなることを確認する
3. **陰性試験③**: `call_claude_headless` に `json_schema` を渡した際、`--json-schema`
   引数がコマンドラインに追加されないよう変異させ（例: `if json_schema is not None:` の
   条件を `if False:` にする）、新規テスト（`--json-schema` が `cmd` に含まれることを assert）が
   赤くなることを確認する。あわせて **schema 不正JSON応答時に fail-open で誤った値を
   永続化しない**ことを確認する陰性試験（`call_haiku` のfakeが壊れたJSON文字列を返した際、
   既存の `ok=False` → drain 持ち越し経路に合流することを確認）も1件追加する
4. **陰性試験④**: `critical_instruction_extractor.py` の削除2行を書き戻す変異を適用し、
   その2行の不在を確認する新規テスト（`_build_judge_prompt` の戻り値に
   `"Chain of Thought"` / `"direct scoring"` の文字列が含まれないことを assert）が
   赤くなることを確認する
5. **陽性対照（緑のままであるべき）**: 各変更につき、正常な項目スコア（0.0〜1.0の範囲内）を
   与えた場合に既存の集約値（`compute_axis_total`／`_score_axis`／`integrated_score`）が
   従来の重み付き平均と数値的に一致することを確認する（重みを等分/明示のどちらに変えたかで
   期待値が変わるため、変更1は等分重み・変更2は監査報告記載の重みで期待値を計算すること）

**委譲側（本メモ）が挙げた回避手段（項目JSON化・schema化・CoT削除）とは種類の違う変異を
2件以上、実装者自身が追加で構成し実際に適用して結果を報告すること**（例: 空文字列/None項目・
enum不正値・JSON構文エラー応答・`--json-schema`未対応の古いCLIバージョンを模した`ClaudeCallError`
発生時の挙動、等）。緑のまま残ったものが1件でもあれば完了扱いにしない。探索した入力クラスと
変換も列挙すること。

---

## 4. リスク・未実測

- **未実測**: `--output-format json` を併用した場合の `--json-schema` の挙動（本メモ§1のprobeでは
  未検証。不要と判断したため検証対象から外したが、将来 CLI の既定挙動が変わった場合の
  再検証条件は「`call_claude_headless` の戻り値が生JSON文字列でなくなった」ことをテストで検知
  できるようにする＝既存パーサ経路を維持する設計（§2 変更3）自体がこのフォールバックになっている）
- **未実測**: `--json-schema` 併用時の decisive test（秘密ファイル非漏洩・hook非発火）の再実測
  （§2 変更3の [Must] として実装PRへ委譲。本メモの1回のprobeは機能確認のみで安全性再実測ではない）
- **未実測**: `output_evaluator.py` の変更後、`run_benchmark.py` の bench マーカーテストが
  実際にどのアサーションを持つか（実装時に Read して確認する）
- **リスク**: `scorer_prompts.py` の重みを「等分」に決めたことで、旧プロンプトが暗黙に
  モデルへ委ねていた採点傾向（モデルが単純平均以外の重みを無意識に適用していた可能性）と
  数値が変わりうる。ただしプロンプト文言自体が「平均」を明示していたため、意図と実装を
  一致させる修正であり後退ではないと判断する
- **リスク**: `agents/evolve-scorer.md` の「その他のプロジェクト」枠を例外として残したことで、
  Finding 2 の是正が4/5ドメインにしか及ばない。監査報告にはこの区別が無かったため、
  本メモが新たに追加した縮小判断（`think-before-coding.md` の「機構を減らして成立させる」）
  である旨を実装PRのレビューで明示する
