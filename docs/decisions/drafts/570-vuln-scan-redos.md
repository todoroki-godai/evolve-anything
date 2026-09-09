# 570: skill_vuln_scan の ReDoS（O(n²)化）を打ち切りで止血する（縮小版）

- issue: #570（refs #566）
- 状態: ドラフト（round2・線形化から打ち切りへ縮小。ユーザー裁定 2026-09-09）
- 作成日: 2026-09-09（R2）
- 前身: R1（`docs/decisions/drafts/570-vuln-scan-redos.md` 初版、commit `c37c7f9b`、
  branch `fix/570-redos-scan`）は codex 設計レビュー巡1 で **設計修正要・[Must] 5件**。
  中核の [Must] は「推奨案（トークン境界へのアンカー付き `match()`）は線形にならない」。
  **本設計は前身の設計レビュー1巡を継承する**（`review.md`: 変更系列はリセットできない）。

## R1 からの変更点（縮小の経緯）

R1 は `SHELL_EXEC_SUBJECT` を線形時間で評価する方式を追求したが、codex のレビューで
以下の反例が出た（自分でも再現・確認済み。取得日 2026-09-09T09:18:51Z）:

```python
import sys,time,re
sys.path.insert(0,'scripts/lib')
import skill_vuln_shell as sh
rx = re.compile(sh.SHELL_EXEC_SUBJECT, re.IGNORECASE)
for n in (400, 800, 1600, 3200):
    s = ("env " * n) + "noshell"
    t0 = time.time(); pos = 0
    while True:
        rx.match(s, pos)
        nxt = s.find(" ", pos)
        if nxt < 0:
            break
        pos = nxt + 1
    print(n, round(time.time() - t0, 4))
# 400 0.1185 / 800 0.3656 / 1600 1.3137 / 3200 7.7152
```

`_WRAPPER_STEP`（`env`/`sudo`/`nice`/`busybox`/`xargs`/`timeout` の連鎖、
`skill_vuln_shell.py:47-50`）を反復させると、候補開始位置ごとに `match()` しても
各候補が残りの入力を毎回読み直すため重複走査が消えない。**入力4倍で時間65倍**
（400→3200）であり線形ではない。R1 で不採用にした3案（所有格量指定子エミュレーション・
atomic group・単語境界アンカー）に続く**4件目の反例**であり、「量指定子や呼び出し方法の
部分修正で線形化する」というアプローチ自体が同一の欠陥族を繰り返し生んでいる
（`no-denylist-checks.md` 系の教訓と同型: 個別の反例を1つずつ塞ぐのは軍拡競争になる）。

**ユーザー裁定（2026-09-09）**: 線形化は追わない。「1論理行が長すぎる場合は検査を打ち切り、
検査できなかったことを表示する（黙って飛ばさない）」へ縮小する。正規表現の線形時間化そのものは
別 issue へ切り出す（下書き: scratchpad `issue_570_linearize.md`）。

## 完成条件（round 0）

① **守る対象**: `skill_vuln_scan` / `memory_guard.scan_text` が、悪意ある1行によって
数秒〜十数秒にわたり可用性を奪われないこと。

② **信頼境界**: 「取り込みスキルの作者が悪意ある static な文字列を書く」ケースを脅威に数える
（#566 の設計 §0② を継承）。動的展開・実行権限は要らない。

③ **対象外**:
- **正規表現の線形時間化そのもの**（R1 の設計1巡を継承して別 issue へ切り出す。
  下書き: scratchpad `issue_570_linearize.md`。起票はユーザーが判断する）
- 打ち切りを迂回する入力の網羅的な対策。**打ち切りは論理行の「長さ」だけで判定し、
  名前・文字列・構文形では判定しない**ため、`no-denylist-checks.md` が禁じる
  「破れる4種の識別」に該当しない（「判定に使う識別」節で詳述）
- `_PATTERNS` の検出ロジック自体の拡張・FP 較正の見直し

④ **blocking の定義**: 以下のいずれかが1件でもあれば着手不可・マージ不可
- 性能予算テストが赤
- 打ち切り閾値の実データ影響（下記「閾値の実測」節）が0件と申告した集合に実際は1件以上ある
- 打ち切りが発生したときに `evaluated`/`scan_errors` 相当の既存契約が「危険なし」の根拠として
  誤読される経路が残っている
- mutation test（陰性試験、下記4型）のいずれかが緑のまま残る

⑤ **検証方法**: 「検証方法」節（性能予算・不変再確認・陰性試験4型＋証跡3点・陽性対照）

⑥ **この成果物が目的文の物差しで削る量**:
目的の物差しは「悪意ある1行を含むスキルを検査したときの所要秒数」。
- **現状**（R1 から引き継ぎ、再実測。取得日 2026-09-09T08:58:58Z、HEAD `74d9f850`）:
  5000 segment（10027文字）の不一致絶対パス1行で `_scan_line()` が **16.31秒**
- **打ち切り後の実測**（試作コードによる計測。コードは変更せず外部ラッパーで `_scan_line` を
  ラップして計測。閾値候補は「閾値の実測」節を参照）:
  - 閾値 4000文字案: 5000 segment（10027文字）入力は即座に打ち切られ **0.00秒**。
    ただし**閾値ちょうど直下**（3907文字、閾値未満で打ち切られない最大級の入力）は
    **5.53秒**残存する（取得日 2026-09-09T09:24:29Z）
  - **削る量**: 「5000 segment 攻撃」に対しては 16.31秒 → 0.00秒（**16.31秒**削減）。
    ただし「閾値直下の攻撃」に対しては 16.31秒 → 5.53秒（**10.78秒**削減、根治ではない）。
    **単一の代表値に丸めない**（攻撃者は閾値を知っていれば直下を選べるため、
    保証できる上限は 5.53秒 であり、0.00秒ではない）

## 前提の evidence

| 前提 | 値 | 取得コマンド | 取得日 |
|---|---|---|---|
| `_scan_line` は3箇所から呼ばれる唯一の入口（物理行スキャン・論理行結合スキャン・memory_guard 経由） | `skill_vuln_scan.py:679` `skill_vuln_scan.py:702` `memory_guard.py:77`（`from skill_vuln_scan import _scan_line as _vuln_scan_line`） | `grep -n "_scan_line(" scripts/lib/skill_vuln_scan.py scripts/lib/memory_guard.py` | 2026-09-09 |
| `SkillVulnReport` に `scan_errors`（`List[str]`）と `evaluated`（`applicable and scanned_files>0 and not scan_errors`）が既存 | `skill_vuln_scan.py:246-271` | `sed -n '246,271p' scripts/lib/skill_vuln_scan.py` | 2026-09-09 |
| `memory_guard.scan_text`/`inspect_content` には scan_errors 相当の器が無い（`List[ContaminationHit]` と `{"hits","block","mode"}` のみ） | `memory_guard.py:178-197,237-260` | `sed -n '178,260p' scripts/lib/memory_guard.py` | 2026-09-09 |
| `skill_vuln_flow.py` は `SHELL_EXEC_SUBJECT` 非依存（`effective_shell_text` のみ使用） | `from skill_vuln_shell import effective_shell_text` | `grep -n "SHELL_EXEC_SUBJECT\|skill_vuln_shell" scripts/lib/skill_vuln_flow.py` | 2026-09-09 |
| 実データ `~/.claude/skills` の finding 6件・flow_findings 157件・scanned_files 1356件 | `scan_skills(os.path.expanduser('~/.claude'))` の結果 | 下記スクリプト参照 | 2026-09-09T08:55:50Z |
| 実データ物理行の最長は 3393文字 | `gstack/ship/sections/apple-release.md:50` | 下記スクリプト参照 | 2026-09-09T09:20:40Z |
| 実データ論理行（`_join_logical_lines` 適用後）の最長は 1851文字 | `hallmark/references/verbs/redesign.md:61` | 下記スクリプト参照 | 2026-09-09T09:21:03Z |

不変条件の再現コマンド:
```python
import sys, os
sys.path.insert(0, 'scripts/lib')
import skill_vuln_scan as s
report = s.scan_skills(os.path.expanduser('~/.claude'))
print(report.scanned_files, len(report.findings), len(report.flow_findings))
# => 1356 6 157
```

物理行最長の再現コマンド:
```python
import sys, os
sys.path.insert(0, 'scripts/lib')
import skill_vuln_scan as s
from pathlib import Path
root = Path(os.path.expanduser('~/.claude'))
files = s._iter_target_files(root / 'skills')
max_len, loc = 0, None
for p in files:
    text = p.read_text(encoding='utf-8-sig', errors='replace')
    for i, line in enumerate(text.splitlines(), 1):
        if len(line) > max_len:
            max_len, loc = len(line), f'{p}:{i}'
print(max_len, loc)
```

論理行最長の再現コマンド:
```python
import sys, os
sys.path.insert(0, 'scripts/lib')
import skill_vuln_scan as s
from pathlib import Path
root = Path(os.path.expanduser('~/.claude'))
files = s._iter_target_files(root / 'skills')
max_len, loc = 0, None
for p in files:
    text = p.read_text(encoding='utf-8-sig', errors='replace')
    lines = text.splitlines()
    shell_scope = (
        set(range(1, len(lines) + 1)) if p.suffix.lower() in ('.sh', '.bash')
        else s._compute_shell_scope_lines_impl(lines, s._match_fence_opener, s._match_fence_closer)
    )
    _, heredoc = s._compute_heredoc_zones(lines, shell_scope)
    for start, joined in s._join_logical_lines(lines, shell_scope - heredoc):
        if len(joined) > max_len:
            max_len, loc = len(joined), f'{p}:{start}'
print(max_len, loc)
```

## 判定に使う識別（no-denylist-checks.md）

打ち切り判定は**候補の文字列長（`len(text)` または `len(norm)`）という単一の観測可能な整数**で行う。
名前・文字列・構文形・sink の種類のいずれでもないため、`no-denylist-checks.md` が挙げる
「破れる4種の識別」に該当しない。**打ち切りを回避する入力は原理的に存在しない**
（閾値以下に収める＝攻撃力を弱めることと同義であり、issue の脅威（可用性の一時停止）を
閾値以下の入力に制限できる。閾値直下でも数秒残ることは④blocking の定義・⑥削る量で
正直に扱う）。したがって本検査は blocking として扱ってよい。

## 閾値の実測（母集団定義つき）

**母集団**: `~/.claude/skills/` 配下（実データ、当PJ管理外）の対象拡張子ファイル1356件、
物理行 860,750行・論理行（結合対象のみ）3,863行。取得日は上記「前提の evidence」表のとおり。

### 悪意入力での所要時間（閾値候補ごと）

再現コマンド:
```python
import sys, time
sys.path.insert(0, 'scripts/lib')
import skill_vuln_scan as s
for L in (1200, 1900, 2500, 3393, 3900, 3999):
    n = (L - 19) // 2
    evil = 'curl https://x | ' + '/a' * n + '/notashell'
    t0 = time.time(); s._scan_line('evil.md', 1, evil); print(len(evil), time.time() - t0)
```
取得日 2026-09-09T09:21:51Z〜09:24:29Z:

| 入力長 | `_scan_line()` |
|---|---|
| 1227 | 0.2958s |
| 1927 | 0.7298s |
| 2527 | 2.1017s |
| 3401（実データ物理行最長相当） | 3.9127s |
| 3907（4000文字閾値の直下） | 5.5258s |

### 実データへの影響（閾値候補ごとに切られる行数）

再現コマンド（閾値1200文字の場合）:
```python
import sys, os
sys.path.insert(0, 'scripts/lib')
import skill_vuln_scan as s
from pathlib import Path
root = Path(os.path.expanduser('~/.claude'))
files = s._iter_target_files(root / 'skills')
over = []
for p in files:
    text = p.read_text(encoding='utf-8-sig', errors='replace')
    for i, line in enumerate(text.splitlines(), 1):
        if len(line) > 1200:
            over.append((len(line), str(p), i))
print(len(over))
```
取得日 2026-09-09T09:22:51Z:

| 閾値候補 | 実データで切られる物理行数（母集団 860,750行中） | 悪意入力・閾値直下での残存時間 |
|---|---|---|
| 1200文字 | **26件**（0.003%） | 約0.3秒未満（実測は閾値1227で0.2958s） |
| 4000文字 | **0件** | **5.53秒**（実測、閾値直下3907文字） |

### トレードオフの明示と暫定判断

**「正常なスキルの最長論理行を切らない」という制約と、「悪意入力での残存時間を短くする」という
制約は両立しない**（実測で確認）。閾値を実データ最長（物理行3393文字）以上に設定すると
実データは1件も切らないが、悪意入力は閾値直下で5.5秒前後残る。閾値を1200文字程度まで
下げれば残存時間は1秒未満に収まるが、実データの outlier 26件（既知の正当なファイル:
`gstack/ship/sections/apple-release.md` 等の長い1行）を「検査不能」として扱うことになる。

**暫定判断（進行に影響なし・確認事項として残す）**: team-lead 指示「正常なスキルの最長論理行を
測り、それを切らない値にする」を優先し、**閾値 4000文字**（実データ最長3393文字に対し
約18%のマージン）を採用する。**残存する最大5.53秒の遅延は「守る対象①」を完全には満たさない**
ことを明記する。これは③対象外に記載した「線形化そのもの」を追わない裁定の直接の帰結であり、
根治は別 issue（scratchpad `issue_570_linearize.md`）に委ねる。issue #570 のタイトルにも
「打ち切りで止血する（縮小版）」と明記し、完全解決でないことを成果物名からも読み取れるようにする。

**閾値の単一ソース**: `skill_vuln_shell.py` に `LOGICAL_LINE_LENGTH_BUDGET = 4000` のような
モジュール定数を1つ置き、`_scan_line` 冒頭（正規化処理より前）でこの定数のみを参照する。
他のファイルは import してこの値を参照するだけで、独自の閾値を持たない。

## 変更案（縮小版）

### 検査経路の入口を1箇所にする（team-lead 指示③の実コード確認）

`_scan_line`（`skill_vuln_scan.py:582`）は、実コードで確認したとおり**既に単一の入口**である:
- `scan_skills` の物理行スキャン（`skill_vuln_scan.py:679`）
- `scan_skills` の論理行結合スキャン（`skill_vuln_scan.py:702`）
- `memory_guard.scan_text`（`memory_guard.py:77` の `_vuln_scan_line` 経由）

の3箇所すべてが `_scan_line` を呼ぶ。**打ち切り判定を `_scan_line` の冒頭
（`_normalize_for_matching`/`_strip_leading_decoration` を呼ぶ前）に1箇所書けば、
3箇所すべてに自動的に適用される**。「`_scan_line` と `memory_guard.scan_text` の両方に
別々の打ち切りを書かない」という team-lead 指示③は、`memory_guard.scan_text` が独自の
判定を持たず `_vuln_scan_line`（`_scan_line` の別名）を再利用しているため、
実装上は自然に満たされる。

### 打ち切りを表に出す設計（既存の器に乗せる）

`scan_skills` 経路（監査表示）と `memory_guard` 経路（記憶書込みゲート）で、
可視化の受け皿が非対称であることを実コードで確認した（「前提の evidence」表）。
**新しいストア・新しい章は作らない**（#379 新設凍結）。既存の器へ以下のように乗せる:

1. `_scan_line` の戻り値を `List[Finding]` から `(List[Finding], Optional[str])`
   （第2要素=打ち切り理由。打ち切りが起きなければ `None`）へ変更する
2. `scan_skills` 側の2呼び出し箇所は、打ち切り理由が非 `None` のとき
   `SkillVulnReport.scan_errors`（既存フィールド、`str` のリスト）へ
   `f"{rel}:{lineno}: 検査打ち切り(論理行長 {L} > 上限 {budget})"` を追記する。
   これにより `evaluated` プロパティ（`applicable and scanned_files>0 and not scan_errors`、
   既存ロジック）が自動的に `False` になり、「findings/flow_findings を危険なしの根拠に
   しない」契約がそのまま働く。**新しいフィールドは増やさない**
3. `memory_guard.scan_text` は戻り値を `List[ContaminationHit]` から
   `(List[ContaminationHit], List[str])`（第2要素=打ち切り理由のリスト）へ変更する。
   `inspect_content`（`memory_guard.py:237-260`）の戻り値 dict に既存の
   `"hits"`/`"block"`/`"mode"` へ **`"truncated": List[str]`** を1キー追加する。
   これは新しい永続化ストア・新しい観測セクションの新設ではなく、**既存の関数戻り値への
   フィールド追加**であり #379 の新設凍結（新 store / observability section /
   advisory proposal adapter / weak_signal channel）には該当しないと判定する
   （根拠: 追加されるのはプロセス内の一時的な dict キーであり、どこにも永続化されない。
   永続化・提示経路が増えるわけではなく、既存の "hits" と同じ寿命・同じ可視化責務を
   呼び出し元(`auto_memory_broker`)に委ねる形は "hits" 自体の設計と対称）
4. `scan_memory_dir`（audit 用、`MemoryContaminationReport`）にも同様に、
   打ち切りが起きたことを示す情報を既存の `hits` とは別に持たせる必要があるかは
   実装フェーズで判断する（「未実測」節に記載）

### 対象外にした代替案（不採用の理由）

- **打ち切り時に findings 配列へ「検査不能」を示す特別な Finding を混ぜる案**:
  `category`/`severity` の既存語彙（HIGH/MEDIUM/LOW、4 family + secret_exfil）に
  「検査不能」を表す新しい値が必要になり、finding の意味論（危険検出）と
  打ち切り通知（メタ情報）が同じ配列に混在してソート・件数カウントに影響する。
  既存の `scan_errors`/`evaluated` という「危険検出とは別枠の器」が既にあるため、
  そちらを使う方が変更が小さく安全

## 検証方法

### 性能予算テスト

`scripts/lib/tests/test_skill_vuln_shell.py` または新規 `test_skill_vuln_perf.py` に追加:
- 閾値ちょうど（4000文字）を境に、閾値未満の入力は通常どおり `_scan_line` が評価され、
  閾値超過の入力は即座に（0.1秒未満で）打ち切られることをアサートする
- 閾値超過の入力に対し `_scan_line` の実行時間が **0.1秒未満**であることを直接アサートする
  （現状は閾値直下で5.53秒残るため、この予算は「閾値を超えたら即座に打ち切る」ことだけを
  保証する。閾値未満の入力の予算は本設計のスコープ外＝根治は別 issue）

### 不変の再確認（[Must]4 対応: 実 home 依存を止め、固定 fixture へ移す）

**R1 の「`~/.claude` を対象にした golden 値」は実 home の増減で変わるため blocking gate に
使わない**（codex 指摘 [Must]4 を反映）。代わりに:

1. **固定 fixture を追加する**: `scripts/lib/tests/fixtures/` 配下に、閾値超過しない通常の
   スキル群（複数ファイル・複数 finding パターンを含む小規模セット）を新規に用意し、
   `scan_skills(fixture_root)` の結果（`applicable`/`scanned_files`/`findings` 全フィールド/
   `flow_findings` 全フィールド/`scan_errors`/`evaluated`）を golden として固定し、
   変更前後で完全一致することを確認する（新規 fixture の内容は実装フェーズで具体化する）
2. **実データでの確認は補助的な健全性チェックに格下げする**: `~/.claude` に対する
   `scan_skills` 実行は、CI の blocking gate ではなく、ローカルでの手動確認手順として
   設計書に記載するに留める（このタスクで実測した 1356/6/157 は「この設計を書いた時点の
   参考値」であり、次回実行時に変わりうることを明記する）

### 陰性試験（`verify-checks-by-breaking.md` + team-lead [Must]5 対応）

各変異について **(a) baseline との差分/hash (b) 変異行が実行されたことの機械的な証跡
(c) 対象テストだけが期待どおり赤化** の3点を別々の成果物として要求する（「赤くなった」の
申告だけでは受理しない）。最低4型＋陽性対照:

1. **型①（判定ロジックの削除）**: `_scan_line` 冒頭の閾値判定 if 文を削除する変異
   → (a) 削除前後の diff（1行）を PR に貼る (b) `coverage.py` で該当 if 文の行が
   ベースライン版のテスト実行時にヒットしていたこと（`coverage run` の該当行カバレッジ）を
   確認する (c) 性能予算テストのみ赤化し、機能テスト（finding 検出系）は影響を受けないこと
2. **型②（閾値を大きくずらす）**: 閾値定数を `4000` から `1_000_000` に変える変異
   → (a) diff（1行） (b) coverage で if 文自体は実行されるが分岐が false 側に倒れることを
   確認 (c) 性能予算テストが赤化すること
3. **型③（対象外の経路にだけ判定を残す）**: `scan_skills` 側の呼び出しにだけ閾値判定を残し、
   `memory_guard.scan_text` 側の経路（`_vuln_scan_line` 呼び出し）を素の `_scan_line`
   ではなく判定なしの複製関数に差し替える変異 → (a) diff (b) memory_guard 経路のテストで
   複製関数が呼ばれたことをモックの呼び出し記録で確認 (c) memory_guard 側の性能テストのみ
   赤化し、scan_skills 側は緑のままであること（＝「入口が1箇所」の契約テストとして機能する）
4. **型④（打ち切り理由を握りつぶす）**: `scan_errors`/`"truncated"` への追記部分を削除し、
   `_scan_line` は打ち切るが呼び出し元へ理由を伝えない変異 → (a) diff (b) 打ち切り分岐が
   実行されたことをカバレッジで確認 (c) 「打ち切り時に scan_errors/truncated が非空になる」
   ことを直接アサートするテストのみ赤化し、性能予算テスト自体は緑のまま
   （＝性能と可視化は別のテストで守られていることを示す）

**陽性対照**: 意味を変えない書き換え（変数名変更・コメント追加・閾値定数を実装どおりの値に
戻す）で、性能予算テスト・不変再確認テスト・機能テストのすべてが緑のままであることを確認する。

## 実装対象ファイル

この集合を越えない:
- `scripts/lib/skill_vuln_shell.py`（閾値定数の単一ソース）
- `scripts/lib/skill_vuln_scan.py`（`_scan_line` の戻り値変更・`scan_skills` 2箇所の
  呼び出し更新・`scan_errors` への追記）
- `scripts/lib/memory_guard.py`（`scan_text`/`inspect_content` の戻り値変更）
- `scripts/lib/tests/test_skill_vuln_shell.py` / `test_skill_vuln_scan.py` /
  `test_memory_guard.py`（存在すれば。性能予算・不変再確認・mutation test 追加）
- 新規: `scripts/lib/tests/fixtures/`（固定 golden fixture、ファイル名は実装フェーズで決定）
- 新規テストファイルを追加する場合: `scripts/lib/tests/test_skill_vuln_perf.py`

**対象外（変更しない）**:
- `scripts/lib/skill_vuln_flow.py`（`SHELL_EXEC_SUBJECT` 非依存と確認済み）
- `scripts/lib/audit/sections_skill_vuln.py`（呼び出し側。`scan_errors`/`evaluated` 経由で
  既存の表示ロジックがそのまま打ち切りを反映するため、変更不要と見込む。実装フェーズで
  `SkillVulnReport` の呼び出し側すべてを再確認する）
- `_PATTERNS` の内容・FP 較正ロジック（issue の対象外どおり）

## 未実測

- `scan_memory_dir`（audit 用、`MemoryContaminationReport`）に打ち切り情報を持たせる
  具体的な実装（フィールド追加か、既存 `hits` への統合か）は未確定。実装フェーズで判断する
- 閾値 4000文字が「実データ最長を切らない」を将来にわたって保証するか（`~/.claude/skills`
  は増減するため、次に長い正当な行が現れる可能性）は未実測。閾値を固定値でなく
  相対的な余裕（例: 観測された最長の1.5倍を定期的に再計測して更新する運用）にするかは
  対象外とし、今回は固定値 4000 を暫定値として採用する
- `_WRAPPER_STEP`/`_SUDO_STEP` 等、`SHELL_EXEC_SUBJECT` 内の他の繰り返し構造が
  閾値直下でどこまで悪化しうるかの網羅的な実測はしていない（`_scan_line` 全体としての
  時間のみ実測。個別の内部構造ごとの寄与分解は行っていない）
- 性能予算テストで採用する 0.1秒という数値の妥当性（CI 実行環境の速度差によるノイズ耐性）は
  実装フェーズでの複数回実行によるばらつき確認が必要

## 判定に使う識別・裁定者（`no-denylist-checks.md` 手続き）

打ち切り判定は「判定に使う識別」節のとおり文字列長という単一の観測可能な整数で行うため
blocking として扱う。本設計書自体の裁定は `review.md` の系統独立レビュー（別系統1本、
Codex 系統を推奨）に委ねる。R1 の巡数（設計レビュー1巡・[Must] 5件）を継承しているため、
本 R2 が2巡目の発注に当たる（`review.md`: 通常は2巡で修正を止める）。
