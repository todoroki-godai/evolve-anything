# 570: skill_vuln_scan の ReDoS（O(n²)化）を根治する

- issue: #570（refs #566）
- 状態: ドラフト（未レビュー）
- 作成日: 2026-09-09

## 完成条件（round 0）

① **守る対象**: `skill_vuln_scan` / `memory_guard.scan_text` の実行時間が、検査対象1行の長さに対して
線形（O(n)）であること。現状は特定の入力形で O(n²) になり、悪意ある1行でスキャナを長時間停止できる。

② **信頼境界**: 脅威に数えるのは「取り込みスキルの作者が悪意ある static な文字列を書く」こと
（#566 の設計 §0② を継承）。動的展開・実行権限は要らない。攻撃者は対象拡張子のファイルに長い1行を
書くだけで到達できる。

③ **対象外**:
- 緩和策（1論理行の長さ／segment数での打ち切り・検査不能の surface）。issue が明示するとおり
  「#566 側で入れるかは別途判断」であり、本 issue のスコープではない
- `_PATTERNS` の検出ロジック自体の拡張・FP 較正の見直し
- `skill_vuln_flow.py`（flow 検出）の書き換え。後述のとおり本変更の対象範囲外と判定した

④ **blocking の定義**: 以下のいずれかが1件でもあれば着手不可・マージ不可
- 性能予算テスト（下記）が赤
- 既存の finding 6件・flow_findings 157件（母集団定義は「現状（実測）」節）が1件でも変わる
- mutation test（性能対策を外す変異）で回帰検査が緑のまま

⑤ **検証方法**: 「検証方法」節を参照（性能予算・不変再確認・陰性試験・陽性対照の4点）

⑥ **この成果物が目的文の物差しで削る量**:
目的の物差しは「長大な1行を含むスキルを検査したときの所要秒数」。
- **現状**（実測、値は下記「現状（実測）」節）: 5000 segment の不一致絶対パス1行で `_scan_line()` が
  16.21秒（2026-09-09T08:58:58Z 実測）
- **削る量**: 未実装のため 0（review.md のルールに従い、未達成の削減は推定として書かない）。
  実装後に達成すべき上限は「検証方法」節の性能予算テストが定める契約であり、実測ではなく合格基準として
  別立てで書く（issue blocking 条件3「5000 segment で秒単位なら blocking」を継承し、
  本設計では「5000 segment で 0.1 秒未満」を候補の実装目標とする。根拠は「変更案」節の実測）

## 前提の evidence

| 前提 | 値 | 取得コマンド | 取得日 |
|---|---|---|---|
| `_COMMAND_PATH` のネスト量指定子がO(n²)要因 | `_COMMAND_PATH = r"(?:\.{1,2}/\|/(?:[A-Za-z0-9._+-]+/)*)?"` | `sed -n '16p' scripts/lib/skill_vuln_shell.py` | 2026-09-09 |
| `_scan_line` が5パターンを順に `.search()` | `for pattern_id, category, severity, regex in _PATTERNS: if regex.search(norm):` | `sed -n '598,599p' scripts/lib/skill_vuln_scan.py` | 2026-09-09 |
| `memory_guard.scan_text` は `skill_vuln_scan._scan_line` を再利用（別実装ではない） | `from skill_vuln_scan import _scan_line as _vuln_scan_line` | `sed -n '77p' scripts/lib/memory_guard.py` | 2026-09-09 |
| `skill_vuln_flow.py` は `SHELL_EXEC_SUBJECT` を使わず `effective_shell_text`（コメント除去用）のみ使用 | `from skill_vuln_shell import effective_shell_text` | `grep -n "SHELL_EXEC_SUBJECT\|skill_vuln_shell" scripts/lib/skill_vuln_flow.py` | 2026-09-09 |
| `_PATTERNS` は4 family（`_SECRET_SOURCE`/`_NET_SINK` の別枠1と合わせ issue の「5 family」と一致） | `remote_exec, destructive, overbroad_tools, prompt_injection`（+ `_scan_line` 内の `secret_exfil` 別枠） | 下記スクリプト参照 | 2026-09-09 |

`_PATTERNS` family 確認コマンド:
```python
import sys; sys.path.insert(0, 'scripts/lib')
import skill_vuln_scan as s
sorted(set(c for _, c, _, _ in s._PATTERNS))
# => ['destructive', 'overbroad_tools', 'prompt_injection', 'remote_exec']  (4)
# + _scan_line 内の secret_exfil 別枠1 = issue の「5 family」
```

## 判定に使う識別（no-denylist-checks.md）

この変更は検査器そのものを触るが、追加する検査（性能予算テスト・mutation test）は
**名前・文字列・構文形・sink の種類による denylist ではない**。判定に使う識別は
「実測した wall-clock 時間、または入力長に対する時間の増加率」という**観測可能な実行時挙動**であり、
`no-denylist-checks.md` が禁じる「破れる4種の識別」のいずれにも該当しない。したがって性能予算テストは
blocking のままでよい。

## 現状（実測）

### 対象コードの特定

`scripts/lib/skill_vuln_shell.py:16`:
```python
_COMMAND_PATH = r"(?:\.{1,2}/|/(?:[A-Za-z0-9._+-]+/)*)?"
```
`(?:[A-Za-z0-9._+-]+/)*` は「1文字クラスの1回以上の繰り返し」を `/` 区切りで0回以上繰り返す
ネスト量指定子。この文字列は `_SHELL_COMMAND`（`skill_vuln_shell.py:37-40`）・`SHELL_EXEC_SUBJECT`
（`skill_vuln_shell.py:71-75`）に埋め込まれ、`build_remote_exec_patterns()` が生成する5パターン
（`skill_vuln_shell.py:88-112`）すべてに含まれる。`_scan_line`（`skill_vuln_scan.py:598-599`）が
この5パターンを含む `_PATTERNS`（13エントリ、うち remote_exec 5件）へ順に `.search()` を呼ぶ。

`_WRAPPER_STEP`（`skill_vuln_shell.py:47-50`）・`_SUDO_STEP`（`67-70`）等の外側繰り返しも
`(?:...)*` 型で同型のリスクを持つ可能性があるが、本設計の実測範囲では `_COMMAND_PATH` 単体が
主要因であることを確認済み（下記の変異1〜3実験）。外側構造の寄与は「未実測」節に記載。

### 本番経路での実測（production 経路・blocking条件1）

再現コマンド（`_scan_line` 単体、絶対パス不一致）:
```python
import sys, time
sys.path.insert(0, 'scripts/lib')
import skill_vuln_scan as s
for n in (40, 200, 2000, 5000):
    evil = 'curl https://x | ' + '/a' * n + '/notashell'
    t0 = time.time(); s._scan_line('evil.md', 1, evil); print(n, time.time() - t0)
```
取得日 2026-09-09T08:58:58Z（HEAD `74d9f850`）:

| n | `_scan_line()` |
|---|---|
| 200 | 0.0264s |
| 2000 | 2.5759s |
| 5000 | 16.3122s |

### 同一入力クラスの before/after 比較（blocking条件2: 異なる入力クラスを混ぜない）

4クラス（絶対/相対 × 一致/不一致）で計測。取得日 2026-09-09T08:58:58Z（HEAD `74d9f850`）:

| クラス | n=200 | n=2000 | n=5000 | findings |
|---|---|---|---|---|
| 絶対パス・不一致 | 0.0264s | 2.5759s | 16.3122s | 0 |
| 絶対パス・一致（`/bash` で終端） | 0.0265s | 2.6471s | 16.7921s | 1 |
| 相対パス・不一致 | 0.0265s | 2.5881s | 16.2081s | 0 |
| 相対パス・一致（`bash` で終端） | 0.0265s | 2.6126s | 16.1803s | 1 |

**4クラスとも同オーダー**（一致・不一致、絶対・相対いずれも O(n²)）。マッチ成立の有無は
バックトラック量に影響しない — `.search()` の複数開始位置が支配的要因であることを示す。

### `memory_guard.scan_text` での実測

```python
import sys, time
sys.path.insert(0, 'scripts/lib')
import memory_guard as mg
evil = 'curl https://x | ' + '/a' * 5000 + '/notashell'
t0 = time.time(); mg.scan_text(evil); print(time.time() - t0)
```
取得日 2026-09-09T08:59:22Z: **16.55秒**（`_scan_line` を再利用しているため同型・同オーダー。想定通り）。

### 既存の finding 6件・flow_findings 157件の母集団定義（blocking条件5）

issue に記載の値は `root=~/.claude` を対象にした `scan_skills()` の結果と一致することを特定した
（issue 本文には母集団の定義が明記されていなかったため、今回突き止めた）。

再現コマンド:
```python
import sys, os
sys.path.insert(0, 'scripts/lib')
import skill_vuln_scan as s
report = s.scan_skills(os.path.expanduser('~/.claude'))
print(report.scanned_files, len(report.findings), len(report.flow_findings))
```
取得日 2026-09-09T08:55:50Z: `scanned_files=1356, findings=6, flow_findings=157`。

**母集団**: `~/.claude/skills/` 配下（`.gitignore` の対象外・当PJの管理外ディレクトリ）にある
全 `SKILL.md` 等の対象拡張子ファイル、1356件。findings/flow_findings は1行ごと・1系列ごとの
検出結果のリストで、1件=1 `Finding`/`FlowFinding` レコード。

**このディレクトリは実データであり、本設計・実装で書き込みは行わない**（読み取り専用の回帰確認にのみ使う）。

所要時間（本番相当 root での全体実行時間・blocking条件1参考値）:
取得日 2026-09-09T08:55:50Z: 70.78秒。ただし内訳を計測すると outlier は最大1.45秒
（`~/.claude/skills/gstack/CHANGELOG.md`, 1,020,733 文字）で、70秒の大半はファイル数
（1356件）に対する定数コストの総和であり、本 issue が扱う「1行の ReDoS」の寄与は現状データでは
小さい（実データにまだ極端に長い1行が存在しないため）。**リスクは潜在的**（issue の脅威モデルどおり、
悪意ある作者が意図的に書けば即座に顕在化する）。

再現コマンド（ファイル別内訳）:
```python
import sys, time, os
sys.path.insert(0, 'scripts/lib')
import skill_vuln_scan as s
from pathlib import Path
root = Path(os.path.expanduser('~/.claude'))
files = s._iter_target_files(root / 'skills')
timings = []
for p in files:
    t0 = time.time()
    text = p.read_text(encoding='utf-8-sig', errors='replace')
    for i, line in enumerate(text.splitlines(), 1):
        s._scan_line(str(p), i, line)
    timings.append((time.time() - t0, str(p), len(text)))
timings.sort(reverse=True)
print(timings[:10])
```
取得日 2026-09-09T08:57:04Z。

## 変更案

issue blocking条件4「regex の枝を足すだけでなく、backtracking に依存しない実装へ直す」に従う。
**枝を足して特定の入力を回避する案（緩和策としての長さ打ち切り含む）は本 issue のスコープでは採らない**
（`no-denylist-checks.md`: 次の1つで破れる。緩和策自体は③対象外で述べたとおり#566側の判断）。

候補を実装する前に、3つの部分修正案を最小実験で検証した（すべて `_COMMAND_PATH` 相当の
最小パターン `(?:[A-Za-z0-9._+-]+/)*notfound` で `n=100000` の不一致入力に対する `.search()`/`.match()` 時間を計測）。

### 検証済みで不採用の案

| 案 | 内容 | n=100000 実測 | 判定 |
|---|---|---|---|
| A: 所有格量指定子エミュレーション | `(?:(?=([A-Za-z0-9._+-]+/))\1)*` （先読み+バックリファレンス） | 未計測（n=20000で9.96s、n=2000比で悪化傾向を確認し打ち切り） | **不採用** |
| B: atomic group | `(?>[A-Za-z0-9._+-]+/)*`（Python 3.11+ で `re` が対応） | 79.84s | **不採用** |
| C: 単語境界アンカー走査 | `re.finditer(r'\b', line)` で境界候補を求め各候補で `match()` | 140.87s | **不採用** |

いずれも「量指定子内部のバックトラックを減らす」対策であり、**`.search()` が全開始位置を
試みる構造そのものは変わらない**ため、O(n²) は解消しなかった（実測でむしろ悪化するケースもあった —
オーバーヘッドが増えただけで漸近的な計算量は変化しないため）。特に候補Cは、`/` が正規表現の
`\b`（単語境界）に該当しないため境界候補の絞り込みとして機能せず、事実上ほぼ全位置を試すのと
変わらなかった。

### 検証済みで採用する方式（線形時間を実証）

`.match()`（開始位置を固定したアンカー付きマッチ）は同じ最小パターンで:

| n | `.match()`（開始位置0固定） |
|---|---|
| 2000 | 0.00005s |
| 5000 | 0.00009s |
| 20000 | 0.00053s |
| 100000 | 0.00258s |

**開始位置を固定すれば線形**であることを実証した（取得日 2026-09-09T09:03:49Z）。

### 推奨: 開始位置候補をトークン境界に絞り、各候補へアンカー付き `match()` を適用する

`SHELL_EXEC_SUBJECT` の評価を `.search()` から、**シェルの区切り文字**
（空白・`|`・`;`・`&&`・`||`・行頭）で行を分割して得た**トークン開始位置候補**（数は行の
トークン数に比例しO(n)、各位置での `match()` はそのトークン長に比例しO(k)、合計でO(n)）へ
限定した `match()` に置き換える。

- **推奨理由**: `_COMMAND_PATH`（`skill_vuln_shell.py:16`）だけでなく `_WRAPPER_STEP`/`_SUDO_STEP`
  等の外側繰り返し構造も温存したまま、**呼び出し方法だけを変更**すればよい可能性が高い
  （`SHELL_EXEC_SUBJECT` 自体の正規表現ソースは変えず、`.search(norm)` を
  `候補開始位置ごとの match(norm, pos)` に置き換える）。既存の finding 6件・flow_findings 157件を
  壊すリスクが、issue が示す「anchored token parsing への全面書き直し」より小さい
- **選ばなかった場合に起きること**（＝候補D「正規表現を使わない手書きトークンパーサへの全面移行」を
  選んだ場合）: issue が最も直接的に指す方向だが、5つの remote_exec パターン（`curl_pipe_sh` /
  `base64_pipe_sh` / `download_and_run` / `shell_c_command_substitution` / `process_substitution`）
  はいずれもパイプ・`&&`・`$()`・`<()` を含む複雑な文脈依存構造を持ち、正規表現を全廃すると
  実装量・レビュー巡数が大きく増え、finding 6件・flow_findings 157件の不変性を保証する回帰リスクが
  上がる。開始位置絞り込み方式で目標（5000 segment で 0.1秒未満）を満たせるなら、全面移行は
  過剰設計（`think-before-coding.md`: 削減候補は新規要件候補・解決機構、最小の1つだけ）
- **未実測**: 実際の `SHELL_EXEC_SUBJECT`（`_WRAPPER_STEP`/`_SUDO_STEP`等を含む完全体）に
  トークン境界絞り込みを適用したときの正確な区切り文字集合・実装コード・finding再現性は
  実装フェーズで確定する（本設計は最小パターンでの原理実証まで）

## 検証方法

### 性能予算テスト（issue blocking条件3）

`scripts/lib/tests/test_skill_vuln_shell.py`（または新規 `test_skill_vuln_perf.py`）に追加:
- 入力長 n=200/2000/5000/20000 の不一致絶対パス・相対パスそれぞれで `_scan_line()` の実行時間を計測
- **予算**: 5000 segment で 0.1秒未満（現行 16.31秒からの実装目標。根拠は「変更案」節の match() 実測）
- 入力長を2倍にしたときの実行時間増加率が2倍程度（線形）に収まることをアサート
  （O(n²)への回帰なら4倍以上になるため検出できる）

### 不変の再確認（issue blocking条件5）

修正前後で `scan_skills(os.path.expanduser('~/.claude'))` を実行し、
`scanned_files=1356, findings=6, flow_findings=157` に加え、**全フィールド**
（`rel_path`/`line`/`category`/`severity`/`pattern_id`/`snippet` 及び `FlowFinding` の全フィールド）が
一致することを比較する。実データを対象にするため、テストは `@pytest.mark.real_home` 相当の
opt-out マーカーを付け、通常の CI 実行では実行しない（`spec/testing.md` の既存慣習に従う）。
**このテストは実データを読むだけで書き込まない**。

### 陰性試験（`verify-checks-by-breaking.md`）

性能対策を外す変異で性能予算テストが赤くなることを確認する。最低4型＋陽性対照:

1. **型①（機能的に等価だが検査対象の判定コードを削る）**: トークン境界絞り込みロジックを削除し
   `.search()` へ戻す変異 → 性能予算テストが赤くなること
2. **型②（境界条件をずらす）**: トークン分割の区切り文字集合から `|` を落とす変異
   （`curl x|bash` のような空白なしパイプを見逃す）→ 性能予算テストではなく機能テスト
   （`test_remote_exec_curl_pipe_sh_detected` 等）が赤くなることを確認し、性能側と機能側の
   テストが異なる欠陥を分担して検出することを示す
3. **型③（量だけ変えて質は変えない）**: 予算値を意図的に緩める変異（0.1秒→100秒）→
   予算テスト自体は緑のままだが、レビューで「予算が事実上無効化されている」ことを検出できるか
   確認する（テストでは検出できない型であることを明示し、レビュー側の責務として記録する）
4. **型④（別経路から同じ脆弱性を再導入）**: `memory_guard.scan_text` 側に独自の `.search()` 直呼びを
   追加する変異 → `_scan_line` 再利用の契約テスト（`memory_guard.py:77` の import が
   `skill_vuln_scan._scan_line` を指すことを確認する既存/新規テスト）が赤くなることを確認する

**陽性対照**: 意味を変えない書き換え（変数名変更・コメント追加・トークン分割の区切り文字集合を
仕様どおりに実装したコード）で性能予算テスト・機能テストともに緑のままであることを確認する。

## 実装対象ファイル

この集合を越えない:
- `scripts/lib/skill_vuln_shell.py`（`SHELL_EXEC_SUBJECT` の評価方式変更の中心）
- `scripts/lib/skill_vuln_scan.py`（`_scan_line` からの呼び出し方法変更、必要な場合のみ）
- `scripts/lib/tests/test_skill_vuln_shell.py` / `scripts/lib/tests/test_skill_vuln_scan.py`
  （性能予算テスト・不変再確認テスト・mutation test 追加）
- 新規テストファイルを追加する場合: `scripts/lib/tests/test_skill_vuln_perf.py`

**対象外（変更しない）**:
- `scripts/lib/memory_guard.py`（`_scan_line` 再利用のみ。根本修正で自動的に恩恵を受ける）
- `scripts/lib/skill_vuln_flow.py`（`SHELL_EXEC_SUBJECT` 非依存と確認済み）
- `scripts/lib/audit/sections_skill_vuln.py`（呼び出し側、インターフェース変更なし）

## 未実測

- `_WRAPPER_STEP`/`_SUDO_STEP`/`_NICE_STEP`/`_BUSYBOX_STEP`/`_XARGS_STEP`/`_TIMEOUT_STEP` 等、
  `_COMMAND_PATH` 以外の外側 `(?:...)*` 構造が単独でどれだけ O(n²) に寄与するかは切り分けていない
  （`_COMMAND_PATH` 単体の最小実験のみ実施）。実装フェーズで `_COMMAND_PATH` 修正後に
  性能予算テストを回し、残存する寄与があれば追加で切り分ける
- 実際の `SHELL_EXEC_SUBJECT` 完全体（ラッパー・sudo等を含む）へトークン境界絞り込みを適用したときの
  正確な実装コード・区切り文字集合・finding再現性
- この設計変更後、`~/.claude` 全体スキャン（70.78秒）のうち ReDoS 由来でない残り時間
  （ファイル数1356件に対する定数コストの総和）を短縮する余地があるかは対象外・未検討
  （本 issue は ReDoS の根治のみを扱う）

## 判定に使う識別・裁定者（`no-denylist-checks.md` 手続き）

本設計が追加する検査（性能予算テスト・不変再確認・mutation test）は blocking として扱う。
判定に使った識別（wall-clock 時間・件数一致）は上記「判定に使う識別」節のとおり。
本設計書自体の裁定は、`review.md` の系統独立レビュー（別系統1本）に委ねる（本設計の著者は
Claude 系のため、レビューは Codex 系統で行うことを推奨）。
