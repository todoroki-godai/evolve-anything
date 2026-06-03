# ADR-032: CLAUDE.md Skills パーサの記法カバレッジ拡張と「CLAUDE.md 解決状態の3分類」

Date: 2026-06-03
Status: Accepted
Related: #295

## Context

evolve / audit / discover の複数検出（`detect_untagged_reference_candidates` /
`detect_missed_skills` / `triage_all_skills`）は、CLAUDE.md の Skills セクションに記載された
スキル名を「ユーザー呼び出し型なので除外する」exclusion set として使う。この exclusion は
`skill_triggers.extract_skill_triggers` → `_parse_skills_section` が抽出する。

#295 で「shadow 環境で CLAUDE.md が解決できず誤検知が多発する」と報告されたが、実測で切り分けた
結果、真因は **shadow ではなくパーサの記法カバレッジ不足**だった。リスト行パーサ
`^-\s+/?([a-zA-Z0-9_:-]+)\s*[:：]` は `- /skill:` / `- skill:` 形式しか拾えず、実 PJ
（`sys-bots-main`）で使われる以下の形式を **CLAUDE.md が存在するのに trigger 0 件**しか返せなかった:

```markdown
## Skills
- **AWSデプロイ**: `/aws-deploy` - `.claude/skills/aws-deploy/SKILL.md`
```

ハイフン直後が太字の非ASCIIラベル、肝心の skill 名はコロン後ろのバッククォート内にある。
→ `claudemd_skills` 空集合 → 除外ロジックが全滅 → ユーザー呼び出し型スキルを `type: reference`
付与候補や missed として**誤検出を confident に提案**していた。

ここには2つの異なる失敗が重なっていた:
1. パーサが実記法を読めない（記法カバレッジ）
2. 「読めず 0 件」と「CLAUDE.md が無く 0 件」を区別せず、どちらも沈黙して検出を素通しさせた
   （silence≠evaluated の裏返し: 環境解決失敗を「問題なし」と取り違える）

## Decision

### 1. リスト行パーサに「行内バッククォートコマンド」抽出を追加する

`_extract_list_item_skill` を新設し、(a) 従来のプレーン形式 `- /skill:` / `- skill:` に加えて、
(b) 行内の最初のバッククォートコマンド `` `/skill-name` `` を skill 名として拾う。
正規表現は `` `/([a-zA-Z0-9_:-]+)` `` で、閉じバッククォート直前までの単一トークンのみ一致する
（`` `/path/to/file` `` のようなパスや、先頭スラッシュ無しの `` `code` `` は拾わない）。

過剰捕捉は **exclusion set を広げる方向（= 誤検知を減らす安全側）にしか効かない**ため、Skills
セクション内の `` `/cmd` `` は積極的に拾ってよい（誤って広く除外しても false positive を増やさない）。

### 2. CLAUDE.md を「実体パス基準」で解決する

`resolve_claude_md_path(project_root, claude_md_path=None)` を新設。直下の CLAUDE.md →
git working tree ルートの CLAUDE.md（`git rev-parse --show-toplevel` fallback）の順で解決する。
サブディレクトリや worktree から実行されても本体 repo の CLAUDE.md に到達する。

### 3. CLAUDE.md 解決状態を3分類し、検出側の挙動を分岐する

| 状態 | 判定 | 検出側の挙動 |
|------|------|-------------|
| **不在** | `resolve_claude_md_path` が None | 正規の no-CLAUDE.md PJ。**従来どおり検出を走らせる** |
| **在るが trigger 抽出 0** | CLAUDE.md は在るが extract が空（記法非対応・Skills セクション無し等） | exclusion が空集合で効かない＝誤検知になる。**untagged_reference を suppress しつつ件数を明示 surface** |
| **在って抽出成功** | extract が ≥1 | exclusion 有効。**従来どおり検出 + 除外** |

判定は `audit.issues.claude_md_unparseable(project_dir)` に集約（`resolve_claude_md_path` +
`extract_skill_triggers` の合成）。audit orchestrator / `collect_issues` の両経路が共有する。
`detect_missed_skills` も「No CLAUDE.md found」と「CLAUDE.md present but no skill triggers
extracted」をメッセージで区別し、ミスリードを防ぐ。

## Alternatives Considered

### 寛容パーサを際限なく拡張する

CLAUDE.md の Skills 記法は PJ ごとに自由度が高い。あらゆる bespoke 形式を飲み込むパーサは
[ADR-027](027-pitfall-format-convergence-vs-tolerant-parser.md) が pitfalls.md で否定した
「際限ない寛容パーサ」と同型のアンチパターンになる。本 ADR はこれに該当しないと判断した:
**CLAUDE.md は rl-anything が書式を強制できない外部入力**であり（pitfalls.md のように `seed` +
`normalize` で正準形へ収束させられない）、かつ追加するのは「太字ラベル + バッククォートコマンド」
という**実 PJ で実証された1形式に限定した足切り**である。`normalize` 路線が取れない以上、
頻出記法までのカバレッジ拡張が現実解。さらに過剰捕捉が安全側（exclusion 拡大）なので、寛容化の
リスクが pitfall パーサ（誤って本文を wipe する危険）と非対称に低い。

### CLAUDE.md 不在でも一律に検出を suppress する

「CLAUDE.md が解決できなければ常にスキップ」だと、CLAUDE.md を持たない正規プロジェクトで
reference 検出が永久に無効化される（既存テストが保証する正当なユースケースを壊す）。そこで
suppress は「**在るが trigger 抽出 0**」のみに限定し、不在は従来どおり検出を走らせる3分類にした。

### shadow 環境のパス解決を作り込む

当初の issue 仮説。実測で否定された（`chaos.py:_prepare_shadow_project` の非git一時コピーは
chaos fitness 限定で本誤検知と無関係）。`resolve_claude_md_path` の git fallback は副次的な
robustness として残すが、本誤検知の主因対策ではない。

## Consequences

- **良い影響**: 太字ラベル形式の CLAUDE.md を持つ実 PJ（`sys-bots-main` で before 0 → after 12
  skills を実測）で exclusion が復活し、untagged_reference / missed_skills の誤検知が解消。
  「環境解決失敗」を沈黙させず surface することで、誤検出を confident な提案として出さない。
- **悪い影響 / 制約**: パーサは「太字ラベル + バッククォートコマンド」までで、それ以外の bespoke
  記法（例: テーブル外の任意散文に埋まった skill 参照）は引き続き拾えない。新たな実 PJ 記法が
  出たら都度カバレッジを足切りで拡張する（[ADR-027] の収束原則を CLAUDE.md には適用できない
  ことの裏返し）。`claude_md_unparseable` ゲートは Skills セクションを持たない CLAUDE.md（rl-anything
  自身など）でも True になるが、その場合 untagged 候補が元々 0 件なら surface しない設計のため実害なし。
