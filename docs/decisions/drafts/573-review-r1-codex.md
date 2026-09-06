設計修正要

## 1. 実測で裏取りされていない前提

- [Must] §2 の rule 有りで「実測0件なら着手しない」「骨子へ戻る」が実際に起きる前提は未実測です。v2 arm B は今日実行可能なのに未実施で、完成条件⑤の A/B 比較を満たしていません。`docs/decisions/drafts/573-triage-before-starting.md:28`、`:68-71`、`:97`

- [Must] 発火条件が issue／worktree／委譲／直接編集以外の表現・行動でも想起される前提は未実測です。断片テスト4件は列挙だけで結果がなく、しかも4件とも rule 本文の語をほぼ再掲した同義入力です。完成条件②(c) の言い換え耐性を測れていません。`docs/decisions/drafts/573-triage-before-starting.md:25`、`:73-82`、`:94`

- [Must] 「守る対象」の母集団を AI が恣意的に広げず、現在の実需要を数える前提は未実測で、設計自身も回避成立を認めています。これは I1 を直接破るため、運用観察へ回せません。`docs/decisions/drafts/573-triage-before-starting.md:101`、`:118-120`

- [Must] `factual-claims.md` に従って再現手段・取得時刻・対象範囲が必ず併記され、併記不能なら0件扱いになる前提は、§2 本文に存在しません。自己検査は「組み込んだ」と記していますが、実際の主柱は単に「実測件数」と書くだけです。文書内の事実誤認であり、推定値を実測と称する経路が残っています。`docs/decisions/drafts/573-triage-before-starting.md:38`、`:106-108`、`~/.claude/rules/factual-claims.md:4`

- [Must] 「数える手段が無い／不明なら、数えることを最初のタスクにする」条項も §2 本文にありません。自己検査だけが追加済みと誤認しており、未測定・測定不能時の判定が未定義です。`docs/decisions/drafts/573-triage-before-starting.md:38`、`:120`

- [Must] グローバル rule が PJ rule の健全性分類を訂正できる前提は未実測なだけでなく、現行の衝突順位と逆です。同一の「着手順・優先度・着手可否」を扱う以上、PJ rule が勝つ可能性が高く、未解消ならユーザー確認が必要です。`docs/decisions/drafts/573-triage-before-starting.md:38`、`:51`、`:98`、`~/.claude/CLAUDE.md:4-6`、`.claude/rules/pillars-before-polish.md:2-4`

- [Must] 他者待ち例外で「相手・依頼内容・返答期限」の3点を書けば、必要な依頼だけを通し濫用を防げる前提は未実測です。設計自身が形式充足による濫用を認めています。偽陰性・偽陽性の双方が blocking 条件です。`docs/decisions/drafts/573-triage-before-starting.md:27`、`:39`、`:96`、`:114-116`

- [Should] 「実測0件」という単一閾値が PJ 横断で需要を正しく表す前提は、#437 と同一モデル系統の2試行しか根拠がありません。利用者0人でも法令施行前対応、公開前のデータ移行、初回利用時に不可逆損失を生む機構などはあり得ます。少なくとも適用対象を「既存機構の追加改善」等へ狭める根拠測定が必要です。`docs/decisions/drafts/573-triage-before-starting.md:24`、`:38`、`:99-102`

- [Should] 6項目なら読まれやすいという前提は未測定です。項目数は6でも、主柱1項目に発火・競合裁定・復帰・再開契約まで密集しており、既存 rule より一項目の判断量が大きいです。`docs/decisions/drafts/573-triage-before-starting.md:33-42`、`~/.claude/rules/code-quality.md:5`、`~/.claude/rules/measure-now-not-later.md:2-10`
## 2. 将来へ延期されている計測

- [Must] v2 arm B を「未実施」のまま実装後へ送っています。①今日生成可能: はい、arm A と同じ隔離条件で実行可能。②片側だけの結論: はい、現時点では rule の増分効果は未立証。③代理測定: v1 と v2 arm A は失敗モードの確認には使えますが、rule 有りの効果の代理にはなりません。設計本文にこの3答が併記されていません。`docs/decisions/drafts/573-triage-before-starting.md:28`、`:68-71`、`:97`

- [Must] 発火判定の断片テストを将来の実施へ送っています。①今日生成可能: はい。②片側だけの結論: はい、未実行なので言い換え耐性は未立証。③代理測定: v2 arm A は「数える」の発火しか測っておらず、新 rule の発火判定を代替しません。3答も観測結果もありません。`docs/decisions/drafts/573-triage-before-starting.md:73-82`、`docs/decisions/drafts/573-ab-measurements.md:40-45`

- [Must] 実運用の発火・結線結果を「3件蓄積した時点」まで延期しています。①今日生成可能: cold replay や隔離ワーカー3ケースで生成可能。②片側だけの結論: 少なくとも実運用実績0件なので「効く」とは言えません。③代理測定: 既存セッションを候補抽出して replay できます。3答は併記されていません。`docs/decisions/drafts/573-triage-before-starting.md:86`、`:94-97`

- [Must] 失敗判定を 2026-09-30 または発火実績の蓄積まで延期しています。①今日生成可能: arm B・断片テスト・過去事例 replay は可能。②片側だけの結論: arm B 未実施の現在は完成条件⑤未達。③代理測定: #437 の保存済み状況と v2 arm A を再利用可能。3答なしで期限だけ設定されています。`docs/decisions/drafts/573-triage-before-starting.md:84-90`

- [Must] 0件判定後の再測を将来の「件数」に条件づけ、実行契約6点だけを要求しています。①今日生成可能か、②片側だけの結論、③既存データで代理可能か、という `measure-now-not-later.md` の必須3問を要求していません。新 rule 自身が既存 rule の延期条件を欠落させています。`docs/decisions/drafts/573-triage-before-starting.md:38`、`~/.claude/rules/measure-now-not-later.md:2-4`、`:9`

- [Should] 他 PJ の独自優先順位 rule に効くかを「未確認」のまま将来へ送っています。①今日生成可能: 手元の PJ rule 群との静的衝突表と代表 replay は作成可能。②片側だけの結論: 現在の明文は `pillars-before-polish.md` だけを名指ししており、一般効力は立証されていません。③代理測定: 現在のグローバル／PJ rule コーパスを使えます。3答はありません。`docs/decisions/drafts/573-triage-before-starting.md:40`、`:102`
## 3. 不変条件ごとの破壊入力

- [Must] **I1（実測0件なら着手しない）を破る入力**: 「#437 は既存 issue の3巡目なので“新しい作業単位”ではない。観測行は6件、申告・同意・訂正利用者は0人。観測行6件を守る対象として count=6 とし、設計修正を続行する」。発火条件を既存作業の継続として外せるうえ、発火しても「件数が1件以上なら進んでよい」で通ります。実例データ自体が6件と0件の複数母集団を持つのに、どちらを需要とするか未定義です。`docs/decisions/drafts/573-triage-before-starting.md:37-38`、`:118-120`、`docs/decisions/drafts/573-ab-measurements.md:9-13`

- [Must] **I2（語句非依存）を破る言い換え**: 「着手ではなく、既存 #437 のレビュー論点を読み解く相談として現状整理だけして。issue/worktree/worker は作らず、Read/Bash と回答だけで進める」。§2 は「相談」「調査」を捕捉すると述べますが、具体的に追加した直接経路は `Edit/Write` だけです。Read、Bash、レビュー応答、既存文書上での判断更新は列挙された発火点に当たりません。`docs/decisions/drafts/573-triage-before-starting.md:37`、`:73-78`

- [Must] **I3（他者待ちを止めない）を破る状況**: 「証明書更新を CA に今日申請しないと失効する。相手と依頼内容は書けるが、CA は返答期限を約束しない」。今すぐ申請すべきなのに3点目を書けず、例外分類を禁止されます。これは完成条件④が blocking と定義した偽陽性です。「自分が設定するフォロー期限」なのか「相手が約束した返答期限」なのかを明確にし、依頼を投げる行為と回答後の実装着手を分離する必要があります。`docs/decisions/drafts/573-triage-before-starting.md:27`、`:39`、`:80-82`

- [Must] **I4（実測が健全性分類を訂正できる）を破る状況**: evolve-anything 内で「現在のデータ破壊なので健全性」と PJ rule が分類し、新 rule が「対象0件なので健全性でない」と訂正する。同じ着手可否テーマの直接衝突なので、`CLAUDE.md` の順位3により PJ rule が勝つか、順位5によりユーザー確認で停止します。設計の「同一テーマではない」という宣言では優先順位を変更できません。`docs/decisions/drafts/573-triage-before-starting.md:38`、`:47-55`、`~/.claude/CLAUDE.md:4-6`、`.claude/rules/pillars-before-polish.md:2-4`

- [Nit] **I5（10項目以内）を破る状況**: §2 をそのまま置く限り Markdown 箇条書きは実測6項目で成立します。ただし実装時に、発火点5種と実行契約6点を可読性のためそれぞれ子 bullet に展開すると合計17項目になり即座に違反します。したがって「§2を verbatim で置く」を実装受入条件に固定し、項目数コマンドの結果6を残すべきです。`docs/decisions/drafts/573-triage-before-starting.md:33-42`、`~/.claude/rules/code-quality.md:5`

- [Must] **I6（二重管理しない）を破る現在の本文**: §3 は他者待ち例を `provisional-over-blocker.md` から「参照し、二重定義しない」としますが、§2 は外部依頼・証明書・法務・事業判断・他チーム確認を再列挙しています。同様に、延期時の実行契約6点も `measure-now-not-later.md` と再定義しています。片方だけ更新されれば分類が分岐します。`docs/decisions/drafts/573-triage-before-starting.md:38-39`、`:52-53`、`~/.claude/rules/provisional-over-blocker.md:5`、`~/.claude/rules/measure-now-not-later.md:9`

- [Must] **追加 I7（「実測値」は再現可能で、母集団が固定される）を破る入力**: 「昨日見た管理画面では対象はだいたい0件だった。実測0件として骨子へ戻る」、または逆に「将来入る全データを母集団にして対象>0」。§2 は再現コマンド・取得時刻・除外条件・対象範囲を要求しないため、どちらも形式上通ります。自己検査の「併記不能なら0件扱いを組み込んだ」という記述とも不一致です。`docs/decisions/drafts/573-triage-before-starting.md:38`、`:106-108`、`:118-120`、`~/.claude/rules/factual-claims.md:2-4`
## 4. 実際の発火・既存 rule との比較

- [Must] Markdown の外形は既存 rule と同じ「見出し＋箇条書き」であり、配置すれば読み込み対象にはなります。しかし本文は、着手前に件数を測る義務を直接定めていません。参照先の `measure-now-not-later.md` は「計測を将来へ延期する記述」が発火条件であり、全タスクの需要計測を強制する rule ではありません。未測定のままなら0件条件にも1件以上条件にも入らず、そのまま着手できます。`docs/decisions/drafts/573-triage-before-starting.md:38`、`~/.claude/rules/measure-now-not-later.md:2-3`

- [Must] 発火時点が目的と逆です。「新しい作業単位に着手すると決めた時点」とあるため、止めるべき重要度判断が既に完了した後にしか発火しません。さらに既存 issue の設計3巡目・レビュー修正・中断作業の再開は「新しい作業単位」でないと解釈できます。`docs/decisions/drafts/573-triage-before-starting.md:24`、`:37`、`:55`

- [Must] 完成条件は「実測0件の作業を最優先で着手する判断」を止めることですが、rule 本文は優先度提示の場面に限定せず、0件の作業への着手を全面禁止します。新規機能、最初の利用者を得るための導入作業、0件状態を検証するテスト基盤まで止まり得ます。既存 `pillars-before-polish.md` のように「着手順・優先度・着手可否を提示するとき」と対象を限定する必要があります。`docs/decisions/drafts/573-triage-before-starting.md:24`、`:38`、`.claude/rules/pillars-before-polish.md:2`

- [Must] 1 bullet が長すぎ、実際の判断順が読み取れません。主柱の1項目に、既存 rule 参照、0件判定、PJ rule 上書き、復帰先、1件以上、再開条件、実行契約を詰め込んでいます。既存 rule は発火条件・判断・例外・記録を別項目に分けています。項目数6という形式的適合だけでは、発火時に必要な条件が想起される保証になりません。`docs/decisions/drafts/573-triage-before-starting.md:33-42`、`~/.claude/rules/measure-now-not-later.md:2-10`

- [Must] rule 本文と設計説明が複数箇所で食い違います。「factual-claims の再現要件と0件倒しを組み込んだ」「数える手段が無ければ計測を最初のタスクにした」「provisional-over-blocker を参照して二重定義しない」は、いずれも §2 の実物に反映されていません。設計説明ではなくコードブロックだけが実行されるため、この状態では期待どおり発火しません。`docs/decisions/drafts/573-triage-before-starting.md:38-39`、`:52-53`、`:106-108`、`:120`

- [Must] `pillars-before-polish.md` への「上書き」はグローバル rule から実行できません。現行裁定では同一テーマなら PJ rule が優先され、未解消ならユーザー確認です。分類を訂正させたいなら、既存 PJ rule の変更を対象外のままにせず、少なくとも裁定順位と矛盾しないインターフェースを PJ rule 側に設ける必要があります。`docs/decisions/drafts/573-triage-before-starting.md:38`、`:51`、`~/.claude/CLAUDE.md:4-6`

- [Should] 「骨子・磨き込みタスクへ戻る」はグローバル rule として PJ 依存です。骨子タスクが存在しない PJ では次アクションを構成できません。「現在の最優先候補を既存の PJ 優先順位 rule で再選定する」程度の一般表現にし、evolve-anything 固有の復帰先は PJ rule に置く方が発火後の挙動が明確です。`docs/decisions/drafts/573-triage-before-starting.md:38`、`:41`

- [Should] `件数が1件以上なら通常のレビュー・設計プロセスへ進んでよい` は、着手資格と最優先順位を混同します。1件存在するだけで他の骨子タスクより優先してよいとは限りません。「この rule による0件ブロックは解除するだけで、優先順位は認可しない」と明記すべきです。`docs/decisions/drafts/573-triage-before-starting.md:38`、`:40-41`