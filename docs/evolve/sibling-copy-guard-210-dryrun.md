# sibling_copy_guard 実コーパス dry-run 較正メモ（#210）

diff-scoped 兄弟コピー検出（`scripts/lib/sibling_copy_guard.py`）の pre-push 配線前に、
evolve-anything 自身の実 git 履歴に対して dry-run した結果と、その結果から導いた配線設計
（大規模 merge push の noise ガード）の記録。再現可能な形で残し、issue #210 チェックリスト
「実コーパス dry-run で FP 率を計測してから配線を有効化」の証跡とする。

## 1. 個別コミット単位（想定どおりの通常 push スケール）

main 直近の非merge commit 15件それぞれに対し、コミット単体の diff（`<sha>~1...<sha>`）を
CLI に流した。

再現コマンド（このリポジトリの main 上で実行可能）:

```bash
for sha in $(git log --no-merges --format=%h main -15); do
  echo "=== ${sha} ==="
  python3 scripts/lib/sibling_copy_guard.py "${sha}~1...${sha}"
done
```

結果: 15件中 13件は該当なし、2件（`46d8c5a`: 3グループ、`aa7cf75`: 9グループ）で検出。
両方とも目視確認の結果、既知の FP パターン（docstring 記載の「汎用的な短い定型句が
`min_tokens` 閾値ちょうどで #40 型モチーフと同じトークン数帯に入る」、および削除行の
line_no アンカーが「目安値」であるための表示行ズレ）に該当し、新規の真陽性は見つからな
かった。個別コミット単位のスケールでは、報告件数は数件〜十数グループ程度に収まり、
非ブロッキング警告として読める分量だった。

## 2. 大規模 merge push（今回 worktree で実際に発生したケース）

本 worktree（`feat/210-diff-scoped-sibling-copy-detection`, 元 base はほぼ1週間前の
main）で `git merge origin/main` を実行した直後、push 前提の diff
（`@{u}...HEAD`、merge-base からの累積差分）に対して CLI を実行した。

再現コマンド:

```bash
python3 scripts/lib/sibling_copy_guard.py "@{u}...HEAD"
```

結果: **14 グループ・のべ 100 箇所超**の検出。individual-commit 較正（上記1.）の
数件〜十数件から一桁跳ね上がった。原因は、merge が持ち込む main 側の
非merge commit（この worktree の場合 32 件）すべてが対象範囲に入り、その中の
汎用的な短いイディオム（`if X is None:` 等）が、変更行と repo 全体の間で大量に
偶然一致したこと。1〜数コミット規模の通常 push では起きない、merge 特有のスケール
問題であることを実測で確認した。

## 3. 配線設計への反映

上記2.の実測を踏まえ、`scripts/git-hooks/pre-push.local` の配線に
**非merge commit 数の上限ガード（既定 `_SIB_MAX_COMMITS=15`）** を追加した。
push 対象範囲（`@{u}..HEAD` または `origin/main..HEAD`）の非merge commit 数が
上限を超える場合はチェック自体を skip し、「対象 N commit のため skip」の1行のみ
表示する（dogfood-gate の「light 非対応は soft skip」と同じ、狼少年回避の思想）。
15 という値は、上記1.の個別コミットサンプル（1コミットあたり最大9グループ）を
すべて許容しつつ、2.のような merge 由来の桁違いノイズは弾ける水準として選んだ
実測ベースの初期値であり、運用上ノイズ/取りこぼしが目立てば調整可能なパラメータ
として扱う。

## 4. 再評価条件（issue #210 本文）に対する判断

issue 本文の再評価条件は「実コーパス dry-run の FP 率が高く、trivial 行除外床・
最小トークン長の調整で収まらなければ icebox に戻す」。個別コミット単位（通常 push
スケール）では新規の真陽性は無かったが FP も皆無ではなく、`min_tokens` 調整では
（docstring に記載の通り）本来の検出対象である #40 型モチーフごと失われるため、
トークン数のみでの追加チューニングは見送る。一方で merge push という運用上ごく
普通に起きるシナリオでの桁違いノイズは、閾値調整ではなく「対象外スケールでは
チェックしない」という skip ガードで解決した。icebox に戻すほどの機能不全ではなく、
現状の設計（trivial 行除外 + import 文除外 + 大規模 push skip）で運用開始する。
