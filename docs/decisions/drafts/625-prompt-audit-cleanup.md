# #625（仮番）: prompt-audit コード内プロンプト修正（変更2・3・6）— 設計メモ（第2版・巡1反映）

出典: `audit-code`（コード内 LLM 呼出点の監査）Finding 3・4、`audit-skills`（skills/agents 監査）Finding 1。
実装は Codex に委譲する前提。本メモは設計レビュー1巡（別系統）の入口。

## 巡の履歴

- **初版（巡0）**: 変更1〜6の6件（うち採点算術の置換=変更1・4・5、`--json-schema`化=変更3、
  CoT削除=変更4、tier文言=変更6。旧採番）を一括で扱う設計だった
- **巡1（codex・`~/.codex-watch/pa-design-r1-20260905-095136-9788.report`）**: 判定「設計修正要」。
  [Must] 約30件。族タグ: **scope-explosion**（`scorer_prompts.py`が`run_loop.py`/`score_noise.py`
  の単一SoTと自己申告しながら片方の消費者改修を別issueへ延期し、完成条件④(c)と自己矛盾する等、
  算術置換3件が個別ファイル単位に分解されたことで一貫性が崩れる指摘が層Aの大半を占めた）＋
  **検証の穴**（延期した6件の未実測前提はすべて「今日自分の権限で計測可能」と判定され
  `measure-now-not-later.md` 違反、schema実測が実際に追加する形状と違う簡易schemaで代替、
  陰性試験4件中3件が対象コードに実在しない・意図でなく文字列完全一致でしか守らない、
  変更6・その他の変更に変異要求が無い、等）
- **裁定（ユーザー・2026-09-05）**: 変更1・4・5（採点算術の置換＝`scorer_prompts.py`・
  `output_evaluator.py`・`agents/evolve-scorer.md`）は **issue #626 へ切り出し**（scope-explosion
  族の根本原因であるファイル単位分解そのものをやり直すため、本メモでは扱わない）。
  本メモ（#625）は**変更2（`--json-schema` 化）・変更3（CoT 行削除）・変更6（tier SKILL 文言）**
  の3件に縮小する。番号は旧版の変更3→変更2、変更4→変更3、変更6→変更6（据え置き）に振り直した
- **本版（第2版）**: 縮小後3件について、巡1の [Must] のうち該当分をすべて反映。特に
  `--json-schema` の実測を「単純なobject schemaの代用」から「実際に追加する2schema」へ
  差し替え、その過程で**トップレベル配列schemaがAPIに拒否される**という巡0では未検出の
  制約を実測し、`verbosity/judge.py` への schema 適用を本メモの対象外に縮小した（詳細は§1・§2）。
  変更1・4・5に関わる巡1指摘（層A #1-3、層B-1の一部、層B-2の一部、層B-3 #4-6・#8、
  陰性試験①②の指摘）は**すべて #626 へ切り出し**、ここでは扱わない

---

## 0. 完成条件（round 0・3件版）

### ① 守る対象
`safe_llm_call` 経由の `claude -p` 呼出しで、CLIネイティブの構造化出力（`--json-schema`）を
**安全設定を弱めずに**追加できる箇所にだけ追加し、JSON強制の冗長な自然言語指示を短縮すること。
副次的に `critical_instruction_extractor.py` のCoT誘導文と `skills/tier/SKILL.md` の履歴語りを除去する。

### ② 信頼境界
脅威に数えるのは**自分たちの実装ミス**（schema追加が既存の4防御を弱める・パーサが追従漏れする・
既存の安全設定を壊す）のみ。悪意ある入力への耐性は本変更の対象外（`safe_llm_call.py` の
`--tools ""` 等は変更しない）。

### ③ 対象外
- **変更1・4・5（採点算術の置換＝`scorer_prompts.py`・`output_evaluator.py`・
  `agents/evolve-scorer.md`）は issue #626 へ切り出し済み**（裁定・巡の履歴参照）
- `safe_llm_call.py` の `--tools ""` / `--settings` deny / `--strict-mcp-config` / `--safe-mode` 等の
  既存安全設定（`--json-schema` は**追加**であり既存フラグの置換ではない）
- Finding 2（`score_noise.py._run_claude_prompt` の fossil）— 別issue起票を推奨（変更なし）
- `scripts/lib/semantic_detector.py` / `scripts/rl/fitness/constitutional.py` のJSON強制文
  （Phase B がインライン assistant 応答のため `--json-schema` を適用できない）
- **`verbosity/judge.py` への `--json-schema` 適用そのもの**（§1で実測したAPI制約により、
  適用には応答形状の変更＝プロンプト・パーサの一体改修が要るため、本メモのスコープには
  含めない。follow-up issue へ切り出す。詳細は§2 変更2の「verbosity側の扱い」）
- **`verbosity/judge.py` の費用事前予約の欠落**（巡1 [Must]・§4で判定根拠を記す。本メモの
  変更で新規に持ち込む問題ではなく、schema適用そのものを見送ったことで本メモの diff の
  対象からも外れる。follow-up issue へ切り出す）

### ④ blocking
- (a) `--json-schema` 追加が `safe_llm_call.py` の既存4防御（`--tools ""`/`--settings` deny/
  `--strict-mcp-config`/`--safe-mode`）のいずれかを弱める、または schema分岐だけが
  これらを落とすコマンド構築の実装が可能である
- (b) 実際に追加する2つの本番相当schema（`judge_runner` 用・`verbosity/judge` 用）ではなく、
  簡易schemaでの検証だけで「schemaが動く」と結論する
- (c) schema違反応答（enum不正値・型違い）が発生した場合に、既存のフォールバック契約
  （バッチ全体を`ok=False`にする／不正フィールドだけ`None`に正規化する）が壊れる
- (d) プロンプト文言変更（短縮）後、`prompt_fingerprint()` は変わるが `CATEGORY_SCHEMA_VERSION`
  を上げ忘れ、既存の「系列断絶検出」契約と矛盾する
- (e) 変更3（CoT削除）の検査が、削除した2文字列の完全一致 grep だけに依存し、同じ意図の
  言い換え文の再導入を捕まえない
- (f) 変更6（tier SKILL文言）に変異試験が無い

### ⑤ 検証方法
(a)〜(f) 各1件以上の陰性試験（赤になるべき変異）＋陽性対照。委譲側（本メモ）が挙げた回避手段とは
種類の違うものを2件以上、実装者自身が実際に適用して結果を報告する。緑のまま残ったものが1件でも
あれば完了扱いにしない。

### ⑥ 目的文の物差しで削る量
CLAUDE.md の4つの柱のいずれにも直接効かない。**⑥ = 0**。「磨き込み」区分（`pillars-before-polish.md`）。

---

## 1. `--json-schema` 併用の実測（本番相当schema・2回）

**巡1の指摘（[Must]・行22）**: 巡0の probe は `{"is_violation":..., "reason":...}` という
単純object schemaを1回通しただけで、実際に追加する `verdicts` 配列・nullable・enum を含む
本番形状のschemaがCLIと既存パーサに適合すると仮定していた。これは検証になっていない。
本版はこの指摘に従い、**実際に追加する2つの本番相当schemaで**それぞれ1回ずつ実行し直した。

### 1.1 `judge_runner` 用schema（実行1回目）

**実行時刻**: 2026-09-05T01:00:56Z（UTC）

プロンプトは `correction_semantic.prompt.build_batch_prompt()` を実際に import して、
2発話（修正1件・非修正1件）で組み立てた実プロンプトをそのまま使用（読み取り専用・コード変更なし）。

**schema**（`CATEGORY_ENUM` を `correction_semantic/prompt.py` から実際にimportして反映）:
```json
{"type": "object", "properties": {"verdicts": {"type": "array", "items": {"type": "object", "properties": {"index": {"type": "integer"}, "is_correction": {"type": "boolean"}, "idiom": {"type": ["string", "null"]}, "category": {"type": ["string", "null"], "enum": ["factual", "process", "omission", "excess", "presentation", "explanation", "approach", "other", null]}, "reason": {"type": "string"}}, "required": ["index", "is_correction"]}}}, "required": ["verdicts"]}
```

**コマンド**（`safe_llm_call.call_claude_headless` と同じ4防御の引数列 + `--json-schema`）:
```bash
claude -p "$PROMPT" --model haiku \
  --tools "" \
  --settings '{"permissions": {"deny": ["Bash","Read","Write","Edit","Glob","Grep","WebFetch","WebSearch","Task","NotebookEdit","BashOutput","KillBash","TodoWrite","SlashCommand","AskUserQuestion","ExitPlanMode"], "defaultMode": "default"}}' \
  --strict-mcp-config --safe-mode --no-session-persistence \
  --json-schema "$SCHEMA"
```

**結果**: 終了コード `0`。stdout（そのまま）:
```json
{"verdicts":[{"index":0,"is_correction":true,"idiom":"四国めたんじゃなくて","category":"factual","reason":"生成する声の選択を誤っており、正しい値（つむぎ）を後置で指定して修正している"},{"index":1,"is_correction":false,"idiom":null,"category":null,"reason":"感謝の相槌であり、Claude の方向・出力・判断を正そうとしたものではない"}]}
```
→ **nullable（`idiom`/`category` の `null`）は拒否されない**。enum値（`factual`）も正しく選択された。
4防御込みで正常動作を確認（巡1 [Must] 行22の懸念を解消）。

### 1.2 `verbosity/judge` 用schema（実行2回目）— **API制約により失敗、重要な設計変更点**

**実行時刻**: 2026-09-05T01:01:17Z（UTC）

プロンプトは `verbosity.judge.PROMPT_HEAD` を実際に import し、候補2件を付けて組み立てた。
`verbosity/judge.py` の既存契約は「**出力は JSON 配列のみ**」（トップレベルが配列）のため、
schemaもトップレベル `type: array` で構成した:
```json
{"type": "array", "items": {"type": "object", "properties": {"i": {"type": "integer"}, "verbose": {"type": "boolean"}, "patterns": {"type": "array", "items": {"type": "string", "enum": ["preamble", "repetition", "filler", "over_summary", "restate_question", "hedging", "meta"]}}, "note": {"type": "string"}}, "required": ["i", "verbose"]}}
```

**結果**: 終了コード `1`。stdout:
```
API Error: 400 tools.0.custom.input_schema.type: Input should be 'object'
```

**実測事実**: `--json-schema` はAPI側（構造化出力の実装が tool の `input_schema` を経由している
ため）**トップレベルが `type: object` であることを要求する**。トップレベル配列を返す契約の
`verbosity/judge.py` にそのまま適用することはできない。

**設計判断（巡0からの変更点）**: `verbosity/judge.py` に `--json-schema` を適用するには、
(a) schemaを `{"type":"object","properties":{"items":{"type":"array",...}}}` にラップし、
(b) `PROMPT_HEAD` の「出力は JSON 配列のみ」という指示を「以下の形式のJSONオブジェクトのみを
返してください: `{"items":[...]}`」に書き換え、
(c) `parse_json_array_result` を「トップレベル配列を探す」現行ロジックから「トップレベル
オブジェクトの `items` キーを読む」ロジックへ改修する、
という**プロンプト・schema・パーサの三点セット改修**が必要になる。これは「`call_claude_headless`
に `json_schema` 引数を追加し、既存の `call_haiku` から渡すだけ」という変更2の当初想定
（巡0時点）を超える規模であり、`think-before-coding.md`「機構を減らして成立させる」に従い、
**本メモでは `verbosity/judge.py` への `--json-schema` 適用を見送り、`judge_runner.py` のみに
適用する**。§2 変更2で詳述する。LLM呼出は2回で打ち切り（3回目は実施しない。①API制約は
既に確定的に判明しており再試行の余地がない＝object rootへ変えれば通ることは仕様上明らかで
再現実験の必要が薄い、②`measure-now-not-later.md` の「片側だけの結論」を優先し、ここで
判明した制約自体を設計に反映することを優先した）。

---

## 2. 変更内容（3件）

### 変更2 — `safe_llm_call.py` に `json_schema` 引数、`judge_runner.py` に schema 付与、プロンプト文言短縮

**対象**: `scripts/lib/safe_llm_call.py:128-177`（`call_claude_headless`）、
`scripts/lib/correction_semantic/judge_runner.py:84`（`call_haiku`）、
`scripts/lib/correction_semantic/prompt.py:187`（"出力は厳格な JSON のみ（前後に説明文を付けない）。"）

**`verbosity/judge.py` は本メモの対象外**（§1.2の実測により object-wrap の三点改修が要るため）。
follow-up issue へ、以下3点をまとめて切り出す（巡1指摘の該当分・すべて file:line 付き）:
- `--json-schema` 適用のための object-wrap 改修（本メモ§1.2）
- **[Must][根本] 費用事前予約の欠落**（巡1報告 行63-68）: `correction_semantic.judge_runner` は
  `_batch.reserve_batch_cost()`（`scripts/lib/correction_semantic/batch.py:153-185`）を
  `call_haiku` の**呼び出し直前**に呼び、無条件で予約記録する（#410 round4 [Must]1+2）。
  対して `verbosity/judge.py:297-304`（`call_haiku(prompt, model)` 呼出し箇所）には同等の
  予約呼出しが無い（`rg -n "reserve_batch_cost|reserved" scripts/lib/verbosity` はヒット0件、
  2026-09-05 実測）。**本メモでは追加しない**（判定根拠: (i) これは `--json-schema` 追加が
  持ち込む新しい問題ではなく既存の欠落であり、本メモの変更2の diff（`safe_llm_call.py`/
  `judge_runner.py`/`prompt.py` のみ）が触らない `verbosity/judge.py` に手を入れる根拠が
  無くなった（§1.2でverbosity側を対象外にしたため）。(ii) `_batch.reserve_batch_cost` は
  `correction_semantic` 専用の `_batch_cost_tokens`/`_store.record_billed_attempts`
  （`correction_judged.jsonl` への keyless record）に依存しており、`verbosity` 側で同じ関数を
  再利用するには `verbosity_verdicts.jsonl` への同等の keyless record 追加が要る＝#379新設凍結
  下での既存ストア拡張の要否判断を要する設計論点で、本メモの縮小方針（3件に限定）を再度
  拡大させる。follow-up issueで object-wrap 改修と合わせて設計するのが筋が良い）
- **suggestion書込みの不変条件テスト欠如**（巡1報告 行72-78）: 実コードを確認した結果、
  `verbosity/judge.py:359-363` は `print()` のみで `rules/concise.md` への書込みは
  **現状存在しない**（`grep -n "concise\.md|write_text" scripts/lib/verbosity/judge.py` で
  書込み系呼出しは0件、2026-09-05実測）。巡1が指摘した「auto-apply化する変異」は**反例経路の
  提案**であり現状のバグではないが、将来この経路に手が入る際の回帰防止として、
  「suggestion生成後もファイルシステムに書込みが発生しないこと」を確認するテストが
  現状無いことは事実。follow-up issueのスコープに含める

**変更前**（`prompt.py` 該当行）:
```python
        "出力は厳格な JSON のみ（前後に説明文を付けない）。形式:\n"
```
**変更後**:
```python
        "以下の形式で判定結果を返してください:\n"
```

**`safe_llm_call.py`**（変更なし・巡0のまま。§1.1の実測で4防御を保ったまま動作することを確認済み）:
```python
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

**`judge_runner.py`**: `call_haiku` 内部で固定 schema（§1.1で実測した schema そのもの。
`CATEGORY_ENUM` から動的に enum を組み立てる）を `call_claude_headless` に渡す。
`call_haiku(prompt, model="haiku")` という**呼び出し側の引数**は変えない（schemaは
`call_haiku` 内部の定数として持つ）。

**フォールバック設計**: `parse_verdicts_result`/`_validate_verdict` は変更しない（§0 blocking (c)
「既存フォールバック契約を壊さない」の担保）。schema はモデル応答の質を上げる追加防御であり、
既存の「型不正→バッチ全体`ok=False`」「category enum不正→`None`正規化」という**既存の寛容な
フォールバックが最終的な正**であることを維持する（巡1 [Must] 行132-139への回答: schemaが
生成時に enum違反を防いでも、CLIがschemaを無視した場合の受信側フォールバックは変えない。
「schema=追加の安全網、既存パーサ=変わらない最終防衛線」という二層構造を明記する）。

**`CATEGORY_SCHEMA_VERSION` の扱い（巡1 [Must] 行98・103への回答）**: `prompt.py:70` の
docstring契約「プロンプト文面・enum・優先規則を変えたら上げる」に従い、プロンプト文言を
「出力は厳格な JSON のみ（前後に説明文を付けない）。」→「以下の形式で判定結果を返してください:」
へ変更するため、**`CATEGORY_SCHEMA_VERSION` を `1` から `2` へ上げる**。実測（2026-09-05・
現HEAD `c05de074`）:
```
旧プロンプト prompt_fingerprint() = 28c25437f34a
新プロンプト（文言短縮後）        = 53c3982a2738
```
（巡1報告が算出した値をそのまま引用。設計文言を実際に置換した `build_batch_prompt([])` の
hashとして確認済み）。fingerprint は既に自動追従するが、`CATEGORY_SCHEMA_VERSION` は
docstring契約上プロンプト文面変更でも明示的に上げる対象であるため、fingerprintの自動変化
だけに任せず**version番号も手動で2へ上げる**。schemaそのもの（`--json-schema` の値）は
enum・優先規則・taxonomyを変えないため、versionにもfingerprintにも別途反映しない
（生成時制約であって意味論的契約ではないため）。

**追従が要る呼出側・テスト**（`grep -rn` 実測）:
- `scripts/lib/tests/test_safe_llm_call.py`（既存呼出しは `json_schema` 未指定＝デフォルト
  `None` のため緑を維持。`json_schema="..."` 指定時に `--json-schema` が `cmd` に追加される
  新規テストを1件追加）
- `scripts/lib/tests/test_correction_semantic_judge_runner.py`（`call_haiku` 自体を
  monkeypatch しているため影響なし。`call_haiku` の呼び出し側シグネチャは変えない）
- `scripts/lib/correction_semantic/prompt.py` の `CATEGORY_SCHEMA_VERSION` を参照する
  `scripts/lib/correction_semantic/batch.py:359`（producer時点でprovenanceに書き込む箇所。
  変更不要・値の参照元を1つ変えるだけ）とそのテスト（値`1`をハードコードしたassertが
  無いか実装時に確認する）

### 変更3 — `critical_instruction_extractor.py` の CoT 誘導文除去

**対象**: `scripts/lib/critical_instruction_extractor.py:391-392`（`_build_judge_prompt`。
実測行番号を第2版で修正: 巡0は「389-390」と記載していたが現HEADでは391-392）

**変更前**:
```python
        f"direct scoring: 違反していれば is_violation=true、していなければ false。\n"
        f"Chain of Thought: まず理由を考え、次に判定を出してください。\n\n"
```
**変更後**:
```python
        f"違反していれば is_violation=true、していなければ false。\n\n"
```

**確認**（2026-09-05T01:02:39Z 実測。`git rev-parse HEAD` = `c05de074`）:
```
$ grep -rn "Chain of Thought\|direct scoring" scripts
scripts/lib/critical_instruction_extractor.py:391:        f"direct scoring: 違反していれば is_violation=true、していなければ false。\n"
scripts/lib/critical_instruction_extractor.py:392:        f"Chain of Thought: まず理由を考え、次に判定を出してください。\n\n"
```
→ 巡1 [Must] 行43（延期せず今実行せよ）に対応。ヒットはこの2行のみで、他箇所への波及は無い。

**追従が要る呼出側・テスト**: `scripts/tests/test_critical_instruction_extractor.py` を
`grep -n "_build_judge_prompt\|direct scoring\|Chain of Thought"` した結果、既存テストに
ヒット0件（巡0と同じ。第2版で再確認済み）。

### 変更6 — `skills/tier/SKILL.md:17-20` の履歴語り書き換え

（巡0から内容の変更なし。変異要求のみ追加＝§3）

**変更前**:
```
`~/.claude/model-tiers.json`（CLI: `bin/evolve-tier`、#193）が一元管理する。以前は
model-routing rule・各 PJ の agent frontmatter・settings.json に散在し、モデル変更のたびに
手動で全ファイルを追従する必要があった（2026-07-10 opus 4.8 廃止時に HEAD が fable⇄sonnet を
同日中に往来した実例）。**このスキル自体はファイルを直接編集しない**
```
**変更後**:
```
`~/.claude/model-tiers.json`（CLI: `bin/evolve-tier`、#193）が一元管理する。分散管理（rule・
各 PJ の agent frontmatter・settings.json に個別記載）だとモデル変更のたびに全ファイルへの
手動追従が必要になり、取りこぼしによる設定ズレが起きる。**このスキル自体はファイルを直接編集しない**
```

**追従が要る呼出側・テスト**: 参照コード無し（Markdown本文のみ）。

---

## 3. 検査の変異要求（issue #625 の完成条件⑤・巡1 [Must] 行150-160を反映）

巡1が「陰性試験①②は対象コードに実在しない（変更1・4・5＝#626のscope）」「陰性試験③はschema
内容・nullable・required・enum・schema付き経路の4防御を守らない」「陰性試験④はCoTの完全一致
grepしか守らない」「変更6・その他に変異が無い」と指摘したため、3件版として以下に**全面差し替え**する。

1. **陰性試験(a)**: `call_claude_headless` の `if json_schema is not None: cmd += [...]` を
   `cmd = ["claude", "-p", prompt, "--json-schema", json_schema]`（4防御を落として再構築する
   変異）に書き換え、**schema付きの単一呼出しで `--tools`/`--settings`/`--strict-mcp-config`/
   `--safe-mode`/`--no-session-persistence`/`--json-schema` の全てを同時にassertする新規テスト**
   （巡1報告が明示した反例経路そのもの）が赤くなることを確認する。既存テスト
   （`test_command_uses_tools_empty_as_primary_defense` 等）はschema未指定のみを検査しており
   この変異を検出しない＝**新規テストが必須**（既存テストの流用では不可）
2. **陰性試験(b)**: `judge_runner.call_haiku` に渡す固定schemaの `category` の `enum` から
   `null` を除去する変異を適用し、非修正発話（`is_correction=false`）を含む§1.1と同型の
   fixtureを流したときに、**モックではなく`_validate_verdict`の実際の正規化ロジック**が
   `category=None` を許容し続けること（=schemaが仮に壊れてもパーサ側の寛容フォールバックが
   独立して機能すること）を確認する陽性対照、および `_validate_verdict` の `category` enum判定
   ロジック自体を「不正値を素通しする」方向へ書き換える変異で赤くなる陰性試験を追加する
3. **陰性試験(c)**: `CATEGORY_SCHEMA_VERSION` を `2` へ上げずに `1` のまま残す変異を適用し、
   `prompt_fingerprint()` の変化と `CATEGORY_SCHEMA_VERSION` の値がテストで**両方**
   （fingerprintのハッシュ値・versionの整数値）検査されることを確認する
4. **陰性試験(d)**: `critical_instruction_extractor.py` の削除2行を「段階的に理由を考えてから
   判定してください」のような**意図が同じ言い換え文**へ書き換える変異を適用し、
   `Chain of Thought|direct scoring` の完全一致 grep だけに頼るのではなく、
   `_build_judge_prompt` の戻り値が「判定結果を許可する短い定型文」（例:
   `f"違反していれば is_violation=true、していなければ false。\n\n"` で終わる、または
   その直後に即座に `JSON形式で回答:` が続く＝理由生成を促す中間文が挟まらない）ことを
   **positive assert**（許可リスト形式）で固定する新規テストを追加する（巡1 [Must] 行158の指摘）
5. **陰性試験(e)**: `skills/tier/SKILL.md:17-20` に旧履歴語り（`opus 4.8` という具体的な
   撤去済みモデル名、または `2026-07-10` という具体的日付）を書き戻す変異を適用し、
   その文字列が本文に含まれないことをassertする新規テスト（`spec-keeper` 系の既存 lint に
   相当ロジックが無ければ、単純な文字列非包含テストを1件追加する）が赤くなることを確認する
   （巡1 [Must] 行160の指摘。変更6に変異が無かった不備への対応）
6. **陽性対照**: 上記(a)〜(e)それぞれについて、変異を適用しない正常な変更後コード・正常な
   モデル応答（§1.1の実測結果を fixture として使う）を与えた場合にテストが緑のままであることを
   確認する

**委譲側が挙げた回避手段とは種類の違う変異を2件以上、実装者自身が追加で構成し実際に適用して
結果を報告すること**（例: `--json-schema` の値そのものを空文字列 `""` に差し替える・
`judge_runner.call_haiku` のtimeout値だけ変えてschema引数を消し忘れる・
`skills/tier/SKILL.md` の履歴語りを別の撤去済みモデル名で書き戻す、等）。緑のまま残ったものが
1件でもあれば完了扱いにしない。探索した入力クラスと変換も列挙すること。

---

## 4. リスク・未実測

- **未実測**: `--json-schema` 併用時の decisive test（秘密ファイル非漏洩・hook非発火の
  再実測）は、本メモの2回のLLM呼出し予算に含めず**実装PRへ委譲する**。判定根拠
  （`measure-now-not-later.md` の3問）: ①今日実行可能（同じ4防御引数列に`--json-schema`を
  足すだけ）②片側の結論は既にある（§1.1で4防御込みの正常動作は確認済み。`--json-schema`は
  `--tools`/`--settings`/`--strict-mcp-config`/`--safe-mode`のいずれの実装にも触れない**追加**
  引数であるため、これらを弱める経路は§3陰性試験(a)のコード変異でしか作れず、正規実装では
  発生しない）③既存decisive test手順を流用できる。**それでも延期する理由**: 秘密ファイル
  漏洩の実測にはtmpファイル作成・プロンプトインジェクション文言の追加設計が要り、本メモの
  2回のLLM呼出し予算内では本番schema実測（§1）を優先した。実装PRで**必ず**再実測すること
  （§3陰性試験(a)のコードレベル検査だけでは「実際にモデルがツールを使おうとしないか」という
  behavioralな証拠を代替できないため、必須の別工程として残す）
- **未実測**: `verbosity/judge.py` の object-wrap 改修（§1.2）・費用予約欠落（§2変更2）・
  suggestion書込み不変条件（§2変更2）の3点は、follow-up issueでの実測・設計に委ねる
- **リスク**: `CATEGORY_SCHEMA_VERSION` を2へ上げることで、既存の `correction_judged.jsonl` に
  蓄積された `category_schema_version=1` のレコードと新レコードが**系列断絶として扱われる**
  （`prompt.py:68-70` の設計意図どおりの挙動だが、`correction_rate.py` 等の集計側がこの断絶を
  正しく解釈できるかは実装PRで確認すること。docstringは「系列断絶の検出材料にする」としており
  断絶自体は既存契約が想定する正常系のはずだが、消費側の実装を本メモでは未確認）
- **リスク**: `judge_runner.py` のみに `--json-schema` を適用し `verbosity/judge.py` を対象外に
  したことで、Finding 3 の是正が2/2ファイルから1/2ファイルへ縮小する。監査報告時点では
  この非対称性は判明していなかった（§1.2のAPI制約実測で初めて判明した）ため、本メモが
  新たに追加した縮小判断である旨を実装PRのレビューで明示する
