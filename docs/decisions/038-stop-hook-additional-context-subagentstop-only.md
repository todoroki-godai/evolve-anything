# ADR-038: Stop/SubagentStop の additionalContext は SubagentStop のみ採用、Stop は HOLD

Date: 2026-06-05
Status: Accepted
Related: release-notes-review (CC v2.1.163), second-opinion レビュー, subagent-guard.md, [ADR-028]（observability contract）

## Context

Claude Code v2.1.163 で `Stop` / `SubagentStop` hook が `hookSpecificOutput.additionalContext` を返せるようになった。これは hook error 扱いされずに Claude のコンテキストへ文字列を注入し「Claude にフィードバックを渡してターンを継続する（keep the turn going）」機能。スキーマは:

```json
{ "hookSpecificOutput": { "hookEventName": "SubagentStop", "additionalContext": "..." } }
```

evolve-anything は 2 つの該当 hook を持つ:

- **Stop = `hooks/session_summary.py`**: セッション終了時に Auto Trigger エンジン（`trigger_engine.py`）が evolve/audit の実行を提案する。提案は `pending-trigger.json` に書き出され、**次セッション開始時**に `instructions_loaded.py` 経由で surface される。介入的でなく「ユーザーが確認してから実行する」非介入方針。
- **SubagentStop = `hooks/subagent_observe.py`**: セッション内 subagent 数が閾値（既定 5）を超えると警告を出す。グローバルルール `subagent-guard.md` は「閾値超過警告が出たら**作業を一時停止してユーザーに現状説明**」を要求している。

「この新機能を採用すべきか」を release-notes-review → second-opinion で検討した。

### 調査で判明した事実

1. **SubagentStop の現状実装は `systemMessage`（top-level）のみを出していた**。`systemMessage` は **user UI 向け**で Claude のコンテキストには入らない（CC docs 確認）。つまり subagent-guard.md の「Claude が作業を止めてユーザー説明」という要件は、**現状の実装ではエンフォースされていなかった** — 警告はユーザーに表示されるが Claude は読めないため、Claude 自身は何も行動を変えられない。これは「install ≠ enforcement」（学習メモ）の再演で、ルールは存在するが強制レバーが繋がっていなかった。

2. **Stop hook の additionalContext は「keep the turn going」セマンティクス**を持つ。これは Auto Trigger の非介入方針と**どちらの解釈でも衝突する**（後述）。

## Decision

ユースケースを分離して判断する。

### 1. SubagentStop → 採用

`subagent_observe.py` の閾値超過出力を、`systemMessage`（user 可視性）に加えて `hookSpecificOutput.additionalContext`（Claude 可視性）を**両方**出す形に変更した。additionalContext には subagent-guard.md の行動指示（実行中の作業を一時停止し、ループ/カスケードでないか確認してユーザーに説明）を明示的に書き込む。

これにより subagent-guard.md が初めて実際にエンフォースされる。SubagentStop の文脈では main agent が既にターン中（subagent を spawn して待っている）であり、additionalContext 注入は「進行中ターンへの文脈追加」であって新規ターンを無から生成しない。よって安全で、ルール要件に正確に一致する。

### 2. Stop → HOLD（採用しない）

`session_summary.py` は変更しない。Auto Trigger 提案は従来どおり `pending-trigger.json` → 次セッション開始時 surface を維持する。

理由 — additionalContext on Stop は**ありうる 2 つのセマンティクスのどちらでも不採用**になるため、実測を待たずに却下できる:

- **解釈A:「keep the turn going」が文字どおりターン継続を強制する**なら、毎セッション終了時に Claude が evolve/audit 提案を読んで継続実行しようとする。これは「ユーザー確認を取る」非介入方針への正面衝突であり、毎セッション末尾の自動 evolve nag になる（介入的）。
- **解釈B: 注入された context は次の user prompt まで idle で待つだけ**なら、現行の「次セッション開始時に surface」と機能的に等価かそれ以下（セッションをまたぐと文脈が失われうる）で、置き換える価値がない。

どちらでも不採用なので、second-opinion が推奨した「空 additionalContext を返す最小 hook での実動作実測」は**判断には不要**（測定結果が A でも B でも結論は HOLD）。実測は将来 Stop で別用途に使いたくなった時点で行えばよく、本 ADR の決定をブロックしない。

## Alternatives Considered

### 代替案A: Stop/SubagentStop を一括で additionalContext 化
release-notes-review の初稿は両者を「中期検討」と一括りにした。second-opinion が「Stop と SubagentStop は届き方も衝突性も異なる」と分離を指摘。Stop は非介入方針と衝突し SubagentStop は整合する、と判明したため一括採用は却下。

### 代替案B: SubagentStop も systemMessage のまま据え置く
現状維持。だが subagent-guard.md が要求する「Claude が止まってユーザー説明」を Claude が警告を読めない限り実現できない。ルールと実装の乖離（install ≠ enforcement）を放置することになり却下。

### 代替案C: SubagentStop で systemMessage を捨て additionalContext のみにする
additionalContext は user transcript に見えない（Claude のみ）。閾値超過はユーザーにも可視であるべき安全シグナル（暴走検知）なので、user UI 通知（systemMessage）も残す。両方出す現案を採用。

## Consequences

- subagent-guard.md が初めて実際にエンフォースされる（閾値超過で Claude が自律的に停止しユーザー説明）。
- Auto Trigger の非介入設計は保たれる（Stop は不変）。
- 将来 Stop で additionalContext を別用途に使う場合は、本 ADR の HOLD 判断とは独立に、その用途で「ターン継続強制」が許容されるかを評価し直す。
- 決定論・LLM 非依存（出力は固定文字列の dict）。`no-llm-in-tests.md` に抵触しない。
