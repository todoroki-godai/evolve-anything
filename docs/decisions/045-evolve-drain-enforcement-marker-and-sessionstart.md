# ADR-045: evolve drain の enforcement は CLI 単一コマンド + env 非依存マーカー + SessionStart リマインドで担う（Stop hook auto-drain は不採用）

Date: 2026-06-09
Status: Accepted
Related: #402（#360 / #400 / [ADR-041] の系譜、#358 / [ADR-042] の制約下）

## Context

fitness calibration の母集団 `optimize_history` を育てる `ingest_decisions`（evolve SKILL.md Step 7.8 の drain）は、accept/reject をディスク差分・明示シグナルから決定論で記録する。だが **drain を呼ぶのは SKILL.md の指示文だけ**で、決定論コード（`evolve.py`）からは呼ばれていなかった。

これは `learning_skill_md_must_not_enforcement`（`install ≠ enforcement` の SKILL.md 版、#360 で発覚）の二次再発である。#400 で dry-run 運用経路（emit がキューを書かない問題）は是正したが、「drain が実行されるか」自体は assistant が Step 7.8 を実行するかに依存したままだった。assistant が飛ばすと母集団が再び空＝fitness が `0/30` から動かない。テストは「ingest を呼べば +1」を証明するが「実 run で ingest が呼ばれる」は証明しない。

drain の発火を決定論化したいが、自明な「Stop hook で auto-drain」案は **`pitfall_datadir_hook_tool_split`（#358 / [ADR-042]）** を踏む。`optimize_history_store.DATA_DIR` は import 時に `CLAUDE_PLUGIN_DATA` で分岐し、hook 文脈（env 有）は plugin-data、tool/reader 文脈（env 無）は `~/.claude/evolve-anything` に解決される。hook が drain すると hook 側 DATA_DIR に書き、fitness reader（tool 文脈）は別パスを読むため、**drain 成功でも reader が空のまま＝同症状が別経路で再発**する。[ADR-042] の DATA_DIR 一元化は Phase 2 として未完であり、#402 の文脈で触るのは blast radius が大きすぎる。

## Decision

drain の enforcement を、評価状態を hook 文脈で書かない3点構成（scoped-B）で担う:

1. **`evolve --drain`（`evolve_decisions.drain_pending`）** を追加し、SKILL.md Step 7.8 を inline python から **単一コマンド**へ集約する。drain は **CLI＝tool 文脈**で走り、reader と同一 DATA_DIR に optimize_history を書くため #358 を踏まない。
2. **emit が `--dry-run` でも「未 drain 提案」マーカー**（`before_sha` 付き）を、env 非依存の固定パス `~/.claude/evolve-anything/evolve_pending/<slug>.json` に記録する。マーカーは評価状態（optimize_history / queue）ではなく「apply→drain 待ちの提案ポインタ」という運用状態で、fitness 母集団には入らず drain でクリアされる。パスを env 非依存にすることで、hook（env 有）と tool（env 無）が同一マーカーに合意する。
3. **SessionStart hook**（`restore_state._deliver_evolve_drain_reminder`）が `undrained_applied`（マーカーの `before_sha` と現ディスク sha を突合、`optimize_history` を読まない）で「適用済みなのに未 drain」を検出し、`evolve --drain` を促すリマインドを surface する。

冪等性（`ingest` の `{pid}_{kind}` entry_id dedup）により、「未 apply で空振り→後で apply→再 drain」でも accept は一度だけ記録される。

## Alternatives Considered

### Stop hook で auto-drain

Stop hook が emit 済み pending を検出して自動で ingest する案。**不採用**:
- hook 文脈で optimize_history に書くと #358（DATA_DIR split）を踏み、reader が読めず同症状が別経路で再発する。
- apply は複数ターンに跨りうるため、Stop hook には「apply が完了したか」を判断できない（apply 前に走ると before==after で skip し、以降記録されない懸念）。
- `CLAUDE_PLUGIN_DATA` を子プロセスで unset して回避する案は env 制御が脆弱で fallback が無い。

second-opinion でも「Stop hook auto-drain は drop、Stop はリマインドのみ」と独立に同結論。timing 問題は「次 SessionStart で見る」ことで構造的に消える（apply は前セッションで完了済み）。

### ADR-042 Phase 2（DATA_DIR を hook/tool 不変に統一）してから hook で drain

根本解（#358 の根を断つ）だが、多数の store のパス解決に波及し blast radius・テスト範囲が大きい。#402 のスコープを超えるため別 issue とし、本 ADR は「全て tool 文脈に閉じる」ことで #358 を踏まずに済ませる。

### SKILL.md の MUST を強めるだけ（detect-and-surface のみ）

リマインドは入れるが CLI 化しない案。drain の実行手段が inline python のままで fumble 面が残る。CLI 単一コマンド化と併用する方が確実なため、本決定は両方を採る。

## Consequences

- **良い影響**: drain の実行が単一コマンドに集約され失敗面が縮小。忘れても次回 SessionStart で決定論リマインドが出て自然終息する。全て tool 文脈に閉じるため #358 を踏まない。実 CLI E2E（実マーカー→apply→`evolve --drain`→実 store +1）で outcome を実証済み。
- **悪い影響 / トレードオフ**: 完全な auto-execution ではなく「単一コマンド + リマインド」止まり（assistant がリマインドを無視し続ける余地は残る）。emit が dry-run でもマーカーを書く（評価状態でない運用ポインタに限定し、テストの実 home 汚染は conftest autouse + harness で隔離）。マーカーパスを env 非依存に固定したため、`CLAUDE_PLUGIN_DATA` でカスタム DATA_DIR を使う環境ではマーカーと store の base がずれうるが、マーカーはリマインド専用で store の正確性には影響しない。DATA_DIR の根本統一は [ADR-042] Phase 2 に残る。
