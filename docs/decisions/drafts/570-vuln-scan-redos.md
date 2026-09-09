# 570: skill_vuln_scan の ReDoS（O(n²)化）を「合計時間の予算」で止血する（R3）

- issue: #570（refs #566）
- 状態: ドラフト（R3・行長閾値から時間予算方式へ変更。ユーザー裁定 2026-09-09）
- 作成日: 2026-09-09（R3）
- 前身:
  - R1（`fix/570-redos-scan` commit `c37c7f9b`）: 正規表現の線形時間化を推奨したが
    codex 設計レビュー巡1 で **設計修正要・[Must] 5件**（推奨案が反例で線形にならない）
  - R2（同 branch commit `08f2546e`）: 「1論理行の長さ」で打ち切る方式へ縮小したが、
    team-lead の実測により**行長の閾値はそもそも「ファイル/実行全体の停止時間」の
    上限にならない**ことが判明（下記「R2 からの変更点」節）
  - **本 R3 は R1 の設計レビュー1巡を継続して引き継ぐ**（`review.md`: 変更系列はリセット
    できない。R1→R2→R3 は同一変更系列であり、R1 の巡数を通算する）

## R2 からの変更点（行長閾値の破棄）

team-lead の実測（2026-09-09、私も再現・確認済み。取得日 2026-09-09T09:33:26Z）:

```python
import sys,time
sys.path.insert(0,'scripts/lib')
from skill_vuln_scan import _scan_line
evil = "/a"*1950+"/notashell"  # len=3910（R2 の閾値4000の直下）
for n in (1, 3, 10):
    t0 = time.time()
    for _ in range(n):
        _scan_line("x.md", 1, evil)
    print(n, round(time.time() - t0, 2))
# （自環境の実測）1行 4.52s / 3行 13.73s / 10行 53.66s
```

**攻撃者は閾値直下の行を並べるだけで合計時間をいくらでも伸ばせる。1行あたりの上限（R2 の行長
閾値）は、ファイル全体・実行全体の停止時間の上限にならない。** R2 は破棄する。

## さらに判明した限界（行単位の時間チェックでは不十分）

「1行あたりの時間ではなく合計時間を予算にする」方式を最初に検討する際、
**「各行の処理を始める前に経過時間を確認し、超えていれば打ち切る」**という素朴な実装を
試作したところ、新たな限界を実測で発見した:

```python
def scan_lines_with_time_budget(lines, budget_seconds):
    t_start = time.monotonic()
    for i, line in enumerate(lines, 1):
        if time.monotonic() - t_start > budget_seconds:
            return i  # truncated
        _scan_line('x.md', i, line)
```
取得日 2026-09-09T09:35:12Z:

| 予算 | 1行が16秒かかる入力（5000 segment）を3行 | 実測結果 |
|---|---|---|
| 5秒 | huge_evil × 3 | **17.14秒**（予算の3.4倍） |
| 10秒 | huge_evil × 3 | **19.04秒**（予算の1.9倍） |

**予算チェックが「次の行を始める前」にしか行われないため、1行自体の処理に極端に時間がかかる
場合、その1行の完了を待ってしまい、予算は上限として機能しない。** これは R2 で扱った
「行長閾値では上限にならない」問題が、形を変えて残ったもの。

**解決策として signal ベースのハードタイムアウトを実測で検証した**（下記「変更案」節）。
`signal.setitimer(signal.ITIMER_REAL, ...)` は `re.search()`/`re.match()` の実行中でも
正確に割り込めることを実測で確認した:

```python
import re, time, signal
class Timeout(Exception): pass
signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(Timeout()))
rx = re.compile(r'(?:[A-Za-z0-9._+-]+/)*notfound')
evil = 'a/' * 100000 + 'XXXX'
signal.setitimer(signal.ITIMER_REAL, 2.0)
t0 = time.time()
try:
    rx.search(evil)
except Timeout:
    print('TIMEOUT fired, dt=', time.time() - t0)
# TIMEOUT fired, dt= 2.0012240409851074
```
取得日 2026-09-09T09:35:46Z。**予算 2.0秒に対し実測 2.0012秒で割り込みが発火**し、
行単位チェック方式に見られた「1行分の超過」が起きないことを確認した。

## 完成条件（round 0）

① **守る対象**: `skill_vuln_scan` / `memory_guard.scan_text` の**1回の検査呼び出し**
（`scan_skills` の1実行、または `scan_text` の1呼び出し）が、悪意ある入力（1行が極端に長い・
悪意ある行を大量に並べる、のいずれか、または両方の組み合わせ）によって、**あらかじめ決めた
合計時間を超えて停止し続けないこと**。

② **信頼境界**: 「取り込みスキルの作者が悪意ある static な文字列を書く」ケースを脅威に数える
（#566 の設計 §0② を継承）。1行の内容だけでなく、**1ファイル内の行数・複数ファイルの合計**も
攻撃者が制御できる（R2 で見落としていた点）。

③ **対象外**:
- **正規表現の線形時間化そのもの**（R1 の設計1巡を継承して別 issue へ切り出す。
  下書き: scratchpad `issue_570_linearize.md`、今回追記）
- **行長による打ち切り（今回は採らない）**。理由: 上記「R2 からの変更点」節の実測どおり、
  行長の閾値は合計時間の上限にならない
- ファイル単位の追加予算（下記「変更案」節で不採用の理由を述べる）
- `_PATTERNS` の検出ロジック自体の拡張・FP 較正の見直し

④ **blocking の定義**: 以下のいずれかが1件でもあれば着手不可・マージ不可
- 性能予算テスト（注入した小さい予算値での timeout 発火確認）が赤
- 陽性対照（正常データでの誤打ち切り）が発生する
- 打ち切り発生時に `scan_errors`/`evaluated`/`inspect_content["truncated"]` のいずれの
  既存契約からも見えない経路が残っている
- mutation test（陰性試験、下記4型）のいずれかが緑のまま残る

⑤ **検証方法**: 「検証方法」節（性能予算・不変再確認・陰性試験4型＋証跡3点・陽性対照）

⑥ **この成果物が目的文の物差しで削る量**:
目的の物差しは「悪意ある入力を含むスキルを検査したときの、1回の検査呼び出し全体の
最大停止時間」。
- **現状 — 単発攻撃**（R1 から引き継ぎ再実測。取得日 2026-09-09T08:58:58Z、HEAD `74d9f850`）:
  5000 segment（10027文字）の不一致絶対パス1行で `_scan_line()` が **16.31秒**
- **現状 — 累積攻撃**（team-lead 実測を自環境で再現。取得日 2026-09-09T09:33:26Z）:
  閾値直下相当（3910文字）の行を10行並べると **53.66秒**。**行数に比例して合計時間は
  無制限に伸びる**（上限が無いことそのものが「現状」）
- **打ち切り後の実測**（signal ベースのハードタイムアウトを試作。コードは変更せず外部関数として
  実験、計測後にプロセス終了で自動的に破棄）:
  - 240秒相当の攻撃（16秒かかる行 × 15行相当を5ファイルに分散）に対し、実行全体予算5秒設定で
    **実測5.00秒で打ち切り**（取得日 2026-09-09T09:36:31Z）
  - **削る量**: 「合計時間の上限が無い」状態から「予算値ちょうどで頭打ちになる」状態への変化。
    予算値を実運用の値（下記「予算の値」節、暫定300秒）に設定した場合、**保証できる上限は
    300秒**（現状は無制限）。単発の16秒攻撃に対しては削る量は実質0秒（予算内に収まるため）だが、
    **これは意図どおり**（単発の16秒は「秒単位で止まる」問題としては軽微で、真に守るべきは
    無制限に伸びる累積攻撃への上限であるため）

## 前提の evidence

| 前提 | 値 | 取得コマンド | 取得日 |
|---|---|---|---|
| `scan_skills(~/.claude)` の正常な所要時間（1回目） | 70.78秒（1356ファイル） | `scan_skills(os.path.expanduser('~/.claude'))` の実行時間計測 | 2026-09-09T08:55:50Z |
| `scan_skills(~/.claude)` の正常な所要時間（2回目・再実測） | 63.68秒（1356ファイル、打ち切りなしを確認） | 同上（signal 予算90秒下で実行、`truncated=False` を確認） | 2026-09-09T09:37:45Z |
| 実データ1ファイルあたりの最大所要時間 | 1.446秒（`gstack/CHANGELOG.md`, 1,020,733文字） | ファイル別内訳の再現コマンドは R1/R2 と同じ（下記に再掲） | 2026-09-09T08:57:04Z |
| `signal.setitimer(ITIMER_REAL, ...)` は `re.search`/`_scan_line` の実行中に正確に割り込める | 予算2.0秒で実測2.0012秒 | 「さらに判明した限界」節のコード | 2026-09-09T09:35:46Z |
| 行単位チェック方式は1行が極端に長い場合に予算超過する | 予算5秒で実測17.14秒（3.4倍） | 「さらに判明した限界」節のコード | 2026-09-09T09:35:12Z |
| `_scan_line` は3箇所から呼ばれる唯一の入口 | `skill_vuln_scan.py:679,702` / `memory_guard.py:77` | R1/R2 と同じ、変更なし | 2026-09-09 |
| `SkillVulnReport` に `scan_errors`/`evaluated` が既存 | `skill_vuln_scan.py:246-271` | R1/R2 と同じ | 2026-09-09 |
| `memory_guard.scan_text`/`inspect_content` に打ち切り可視化の器が無い | `memory_guard.py:178-197,237-260` | R1/R2 と同じ | 2026-09-09 |

正常な所要時間の再実測コマンド（陽性対照を兼ねる。90秒予算で timeout しないことを確認）:
```python
import sys, time, signal, os
sys.path.insert(0, 'scripts/lib')
import skill_vuln_scan as s
from pathlib import Path

class Budget(Exception): pass
signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(Budget()))
home = Path(os.path.expanduser('~/.claude'))
files = s._iter_target_files(home / 'skills')
signal.setitimer(signal.ITIMER_REAL, 90.0)
scanned, truncated = 0, False
try:
    for p in files:
        text = p.read_text(encoding='utf-8-sig', errors='replace')
        for i, line in enumerate(text.splitlines(), 1):
            s._scan_line(str(p), i, line)
        scanned += 1
except Budget:
    truncated = True
finally:
    signal.setitimer(signal.ITIMER_REAL, 0)
print(scanned, len(files), truncated)
# => 1356 1356 False（63.68秒で完走、打ち切りなし）
```

## 判定に使う識別（no-denylist-checks.md）

打ち切り判定は**経過時間（wall clock、`ITIMER_REAL`）という単一の観測可能な量**で行う。
名前・文字列・構文形・sink の種類のいずれでもないため `no-denylist-checks.md` の
「破れる4種の識別」に該当せず、blocking として扱ってよい。**行長閾値（R2）と異なり、
攻撃者が「合計時間」を予算以下に収める入力を作った場合、それは攻撃力そのものが弱まっている
ことと同義**（合計処理時間を予算未満に抑えられる入力は、定義上、可用性を脅かす攻撃として
成立しない）。

## 変更案

### 採用: signal ベースのハードタイムアウトを、検査呼び出し全体に1つだけ設ける

- **単位**: `scan_skills` は**1実行全体**、`memory_guard.scan_text` は**1呼び出し全体**に対して
  予算を1つ設ける。「1回の検査呼び出しに対して予算を1つ設ける」という**同一の考え方**の
  適用であり、team-lead 指示④「仕組みは1つだけにする」に沿う（ファイル単位の追加予算は
  設けない。理由は次項）
- **実装方式**: `signal.signal(signal.SIGALRM, ...)` でカスタム例外ハンドラを登録し、
  検査呼び出しの先頭で `signal.setitimer(signal.ITIMER_REAL, budget_seconds)` を設定する。
  例外は呼び出し元でキャッチし、そこまでの結果を保持したまま「予算超過につき以降未走査」を
  `scan_errors`/`inspect_content["truncated"]` へ記録する。**タイマーは `finally` で必ず解除する**
  （後続の別呼び出しに影響を残さないため）

### ファイル単位の追加予算は採らない（不採用の理由）

試作実測（ファイルごとにタイマーを再設定する方式、取得日 2026-09-09T09:38:25Z）:
```
huge_evil(16秒/行) × 3行 のファイルを3つ、ファイル予算10秒で走査
per_file: [(10.0, True), (10.0, True), (10.01, True)]
total 30.01
```
ファイル単位の予算は各ファイルの停止時間には上限をかけられるが、**ファイル数が増えれば
実行全体の合計時間は無制限に伸びる**（守る対象①を満たさない）。**実行全体予算だけで
守る対象①を完全に満たせる**ため、ファイル単位予算を追加する便益（1ファイルが実行全体予算を
独占することへの公平性配慮）は、追加の複雑さ（ネストしたタイマー管理・シグナルの再入問題）に
見合わないと判断する。

### 打ち切りを表に出す設計（R2 から維持）

- `scan_skills`: 打ち切り発生時、`SkillVulnReport.scan_errors`（既存フィールド）へ
  `f"予算超過（{budget}秒）につき {rel} 以降 {n}件を未走査"` のような1行を追記する。
  これにより既存の `evaluated` プロパティ（`applicable and scanned_files>0 and not scan_errors`）
  が自動的に `False` になる。**新しいフィールドは増やさない**（R2 から継続）
- `memory_guard.scan_text`/`inspect_content`: 戻り値に **`"truncated": bool`**
  （または理由文字列）を1キー追加する。R2 で検討したとおり、これは新しい永続化ストア・
  観測セクションの新設ではなく既存の関数戻り値へのフィールド追加であり、#379 の新設凍結には
  非該当と判定する（根拠は R2 のまま維持: プロセス内の一時的な dict キーであり、どこにも
  永続化されない）

## 予算の値（実測から決定）

| 対象 | 正常な実測所要時間 | 採用する予算 | マージン | 根拠 |
|---|---|---|---|---|
| `scan_skills`（実行全体） | 63.68〜70.78秒（`~/.claude`、1356ファイル） | **暫定300秒** | 約4.2〜4.7倍 | 正常データの増加（PJ・スキル数の今後の成長）とマシン速度差（CI環境が手元より遅い可能性）の両方を吸収する余裕を持たせた。根拠となる倍率の一般則は無く、実測値に対する暫定マージンとして採用する |
| `memory_guard.scan_text`（1呼び出し） | 1.446秒（実データ1ファイル最大） | **暫定10秒** | 約7倍 | 1回の記憶書込みは通常1ファイルよりずっと小さいため、実データの1ファイル最大値を安全側の代理指標として使う |

**マシン依存性の明記**: 予算は wall clock（実時間）に基づくため、**同じ入力でも実行環境の
速度によって「検査される範囲」が変わりうる**。遅い環境では正常なデータでも打ち切りが
発生する可能性があり、速い環境では悪意ある入力でもより多くの行が検査されてから打ち切られる。
**この非決定性は許容する**（打ち切りは必ず `scan_errors`/`truncated` に表示され黙って消えない
ため、結果が変動しても「検査できなかった」という事実は必ず利用者に伝わる。これが受け入れの
根拠）。

## 検証方法

### 性能予算テスト（決定論的にする方法）

**wall clock そのものをモックすることはできない**（`signal.ITIMER_REAL` は OS レベルの実時間
機構）。代わりに、**予算値を関数の引数として注入可能にし、テストでは極端に小さい予算
（例: 0.05秒）と、確実にそれを超える処理（5000 segment の悪意入力、または明示的な
`time.sleep`）を組み合わせることで、実行環境の速度に関わらず決定論的に打ち切りを発火させる**。
これは「時計を差し替える」というteam-lead指示の目的（環境速度非依存のテスト）を、
シグナルベースの実装で実現可能な形に翻訳したものである。

- 予算0.05秒・悪意入力（数秒かかることが確実な入力）で `scan_skills`/`scan_text` を実行し、
  `scan_errors`/`truncated` に打ち切りの記録が現れることをアサートする
- 予算90秒・正常 fixture（下記 golden fixture）で実行し、打ち切りが**発生しない**ことを
  アサートする（陽性対照）

### 不変の再確認（R2 の [Must]4 対応を維持）

R2 と同じ方針を維持する: `~/.claude` への依存を blocking gate にせず、**固定 fixture での
golden test**を追加する。`scan_skills(fixture_root)` の結果（`applicable`/`scanned_files`/
`findings` 全フィールド/`flow_findings` 全フィールド/`scan_errors`/`evaluated`）が変更前後で
完全一致することを確認する。実データでの確認（本設計で実測した 1356/6/157 等）は「この設計を
書いた時点の参考値」として記載するに留め、blocking gate には使わない。

### 陰性試験（`verify-checks-by-breaking.md` + R2 の [Must]5 対応を維持）

各変異について **(a) baseline との差分 (b) 変異行が実行で読まれたことの機械的な証跡
（`coverage.py` 等） (c) 対象テストだけが期待どおり赤化** の3点を要求する。最低4型＋陽性対照:

1. **型①（signal ハンドラの登録自体を削除する変異）** → (a) diff（数行） (b) coverage で
   ハンドラ登録行がベースライン版のテスト実行時にヒットしていたことを確認 (c) 性能予算テストの
   みが赤化し、機能テスト（finding 検出系）は影響を受けないこと
2. **型②（予算値を極端に大きくする変異、例: 300秒→1兆秒）** → (a) diff（1行） (b) coverage で
   `setitimer` 呼び出し自体は実行されるが実質的に無効化されることを確認 (c) 性能予算テストが
   赤化すること
3. **型③（`finally` でのタイマー解除を削除する変異）** → (a) diff（1行） (b) 後続呼び出しに
   前回のタイマーが残留し、意図しないタイミングで打ち切りが発生することをテストで検出できるか
   確認 (c) 「連続呼び出しで2回目が正常完了する」ことを直接アサートするテストが赤化すること
4. **型④（打ち切り理由を握りつぶす変異、`scan_errors`/`truncated` への追記を削除）** →
   (a) diff (b) 打ち切り分岐（例外キャッチ節）が実行されたことをカバレッジで確認 (c) 「打ち切り時に
   scan_errors/truncated が非空になる」ことを直接アサートするテストのみ赤化し、性能予算テスト
   自体は緑のまま（＝性能と可視化は別のテストで守られていることを示す）

**陽性対照**: 意味を変えない書き換え（変数名変更・コメント追加・予算値を実装どおりの値に戻す）で、
性能予算テスト・不変再確認テスト・機能テストのすべてが緑のままであることを確認する。
加えて、上記「予算の値」節の90秒予算での正常データ完走（`truncated=False`）を陽性対照の
実測として明記済み。

## 実装対象ファイル

この集合を越えない:
- `scripts/lib/skill_vuln_shell.py` または `skill_vuln_scan.py`（予算値の単一ソース定数）
- `scripts/lib/skill_vuln_scan.py`（`scan_skills` への signal ベースタイマーの導入・
  `scan_errors` への追記）
- `scripts/lib/memory_guard.py`（`scan_text`/`inspect_content` への signal ベースタイマー導入・
  戻り値への `"truncated"` 追加）
- `scripts/lib/tests/test_skill_vuln_shell.py` / `test_skill_vuln_scan.py` /
  `test_memory_guard.py`（性能予算・不変再確認・mutation test 追加）
- 新規: `scripts/lib/tests/fixtures/`（固定 golden fixture）
- 新規テストファイルを追加する場合: `scripts/lib/tests/test_skill_vuln_perf.py`

**対象外（変更しない）**:
- `scripts/lib/skill_vuln_flow.py`（`SHELL_EXEC_SUBJECT` 非依存と確認済み）
- `scripts/lib/audit/sections_skill_vuln.py`（呼び出し側。実装フェーズで再確認）
- `_PATTERNS` の内容・FP 較正ロジック

## 未実測

- **signal ベースの実装がマルチスレッド/pytest-xdist 並列実行環境で安全に動作するか**は
  未実測（`signal.signal`/`setitimer` はメインスレッドでのみ有効という Python の制約がある）。
  本 PJ のテスト運用は `-n 0`（直列）を要求しており、通常の pytest 実行では単一スレッド前提だが、
  `audit`/hook からの呼び出しがスレッドプール等を使っていないかは実装フェーズで確認が必要
- Windows 環境での動作（`SIGALRM` は Unix 限定）。本 PJ は darwin 前提であり、CI が Linux なら
  問題ないが、明示的な対応方針は未確定
- `scan_skills` が `memory_guard` を呼ぶ、またはその逆のようなネストした呼び出しが実在するかは
  確認していない（実在すれば signal タイマーの入れ子・上書きに注意が必要）
- 300秒/10秒という予算の暫定値が実運用（audit 実行・hook 実行のタイムアウト設定等、上位の
  呼び出し元が持つ別のタイムアウト）と整合するかは未確認。実装フェーズで上位呼び出し元の
  タイムアウト値を確認し、矛盾があれば調整する

## 判定に使う識別・裁定者（`no-denylist-checks.md` 手続き）

打ち切り判定は「判定に使う識別」節のとおり経過時間という単一の観測可能な量で行うため
blocking として扱う。本設計書自体の裁定は `review.md` の系統独立レビューに委ねる。
R1 からの累計巡数（設計レビュー巡1・[Must] 5件）を継承しており、本 R3 も同一変更系列として
巡数を通算する。
