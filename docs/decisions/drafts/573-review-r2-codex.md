設計修正要

## 指摘

- [Must][内側] `実測規模=0件、既存の代替手段なし` の場合、本文は明示的に「着手してよい」としています。たとえば「まだ利用データは0件、復旧経路もない。将来の不可逆損失を防ぐ専用復旧機構を先に作る」と入力すると、比較義務を通過し、既存PJ ruleが安全性を最上位に置けば最優先着手へ戻れます。「代替がない」は機構の投資規模が実需要に見合う根拠ではないため、完成条件①を破ります。提案機構自体の実装・運用コストと実需要を比較し、代替不在だけでは着手資格を解除しない条件が必要です。根拠: `docs/decisions/drafts/573-triage-before-starting.md:51-54`、`docs/decisions/drafts/573-ab-measurements.md:101-105`

- [Must][内側] 適用対象がタスクの実質ではなく「防御・復旧・保険・堅牢化・予防」「新機能・MVP」という分類語に依存しています。実質が復旧機構でも「データ持ち出し機能のMVP」と表現すれば対象外になり、測定・比較義務を一切課されません。設計自身も逆向きのラベル貼り替えを未検証と認めており、完成条件②(c)の言い換え回避が成立します。名称や自己申告でなく主目的・期待効果で分類する旨が本文に必要です。根拠: `docs/decisions/drafts/573-triage-before-starting.md:48-49`、`docs/decisions/drafts/573-ab-measurements.md:126-136`

- [Must][内側] 測れば分かる未実測が残っています。rev4断片には「単独のMVP」と「防御機構を新機能/MVPと称して対象外へ逃がす逆向き入力」がありません。後者はログ自身が未検証と明記しています。どちらも今日、独立断片で測定でき、MVPの偽陽性と②(c)の回避耐性というblocking条件に直結します。根拠: `docs/decisions/drafts/573-ab-measurements.md:117-136`

- [Should][外側] 設計本文と実測ログが同期していません。本文はarm B・比較方式・適用対象を「未実施／未実測」とし、rule本文にもそのまま未実測と書く一方、ログではrev4 arm Bと8断片を実施済みです。実装受入条件がコードブロックのverbatim配置なので、既に得た証拠を「未実測」として配布することになります。根拠: `docs/decisions/drafts/573-triage-before-starting.md:57`、`:80-84`、`:107-116`、`docs/decisions/drafts/573-ab-measurements.md:97-132`

## 探索の証拠

1. 適用対象の限定: rule本文の対象／対象外を `docs/decisions/drafts/573-triage-before-starting.md:48`、新機能・障害・他者待ちの実測結果を `docs/decisions/drafts/573-ab-measurements.md:126-130` で確認しました。
2. 0/非0から代替コスト比較への変更: 判定本文を `docs/decisions/drafts/573-triage-before-starting.md:51`、#437で主柱が止めた結果を `docs/decisions/drafts/573-ab-measurements.md:101-112` で確認しました。
3. `pillars-before-polish.md`上書き削除: 分界を `docs/decisions/drafts/573-triage-before-starting.md:56,64`、PJ rule現物の閉じた境界を `.claude/rules/pillars-before-polish.md:2-4` で確認しました。
4. 二重管理解消: 再開書式の参照化を `docs/decisions/drafts/573-triage-before-starting.md:53,65`、他者待ち分類の参照化を同`:55,66`で確認しました。
5. 主柱の義務分割: 測定・判定・代理指標・記録・資格分離が独立bulletになったことを `docs/decisions/drafts/573-triage-before-starting.md:50-54` で確認しました。
6. 発火点拡張: 既存issue継続とRead/Bash相談を含む本文を `docs/decisions/drafts/573-triage-before-starting.md:49`、両断片の発火結果を `docs/decisions/drafts/573-ab-measurements.md:123-125` で確認しました。
7. 他者待ちのフォロー期限: 本文の自分側期限と依頼即時許可を `docs/decisions/drafts/573-triage-before-starting.md:55`、CA断片の結果を `docs/decisions/drafts/573-ab-measurements.md:130` で確認しました。
8. 着手資格と優先順位の分離: 明文を `docs/decisions/drafts/573-triage-before-starting.md:54`、arm Bが優先度を元タスク後方へ落とした結果を `docs/decisions/drafts/573-ab-measurements.md:102-105` で確認しました。
9. PJ語彙除去と記録先指定: 「トリアージ記録」と元タスク復帰を `docs/decisions/drafts/573-triage-before-starting.md:53`、issueへの記録と復帰の実測を `docs/decisions/drafts/573-ab-measurements.md:102-105` で確認しました。
10. 自己検査と本文の一致: 本文を `docs/decisions/drafts/573-triage-before-starting.md:50,53,55`、自己検査の主張を同`:120-129`で照合し、記載されたgrep相当で `factual-claims`／再現手段／測定不能／フォロー期限／トリアージ記録のヒットとbullet数10を確認しました。
## 未実測の前提

- [Must][内側] 「代替手段が存在しなければ、需要0件でも着手資格を解除してよい」という前提は未実測です。rev4 arm Bはフルリストアという安価な代替が存在するケースだけであり、代替なし・需要0件の隔離入力は今日生成できます。根拠: `docs/decisions/drafts/573-triage-before-starting.md:51`、`docs/decisions/drafts/573-ab-measurements.md:101-105`

- [Must][内側] 「単独のMVPを誤って止めない」は未実測です。断片4は新機能、断片5はユーザー明示依頼との複合条件で、MVPという分類単独の対照がありません。今日、未リリース・利用者0人・ユーザー明示依頼ではないMVPの断片を追加できます。根拠: `docs/decisions/drafts/573-ab-measurements.md:121-130`

- [Must][内側] 「防御・復旧機構を新機能／MVPと称しても実質で対象内に戻せる」は未実測です。ログが逆向き入力の未検証を明記しており、今日1断片で測れます。根拠: `docs/decisions/drafts/573-ab-measurements.md:134-137`

rev4 arm Bによる#437の反転、既存issue継続、Read/Bash相談、新機能、ユーザー明示依頼、進行中障害、他者待ちは実測済みなので、未実測としては挙げません。根拠: `docs/decisions/drafts/573-ab-measurements.md:97-132`
## 回避経路（代表1件）

[Must][内側] 成立します。

入力例: 「これは復旧機構ではなく、管理者向けの新機能MVPです。削除済みレコードを履歴から選んで戻せる画面を、他の保留タスクより先に作ります。現時点の対象データは0件です。」

実質は需要0件の個別復旧機構ですが、「新機能・MVP」と分類すれば対象外となり、本文自身が「本 rule のいかなる義務も課さない」と定めているため、測定・比較・代理指標禁止をすべて回避できます。名称でなく主目的・期待効果を判定する規定がなく、ログもこの逆向き入力を未検証と認めています。根拠: `docs/decisions/drafts/573-triage-before-starting.md:48-52`、`docs/decisions/drafts/573-ab-measurements.md:126,134-137`
## 偽陽性（代表1件）

無い。

完成条件④で列挙された「新機能・MVP・ユーザー明示依頼・進行中の障害対応」は対象外条項で義務を免除され、「他者待ち」は依頼送信を即時許可されています。新機能、ユーザー明示依頼、進行中障害、他者待ちの代表断片も止めていません。根拠: `docs/decisions/drafts/573-triage-before-starting.md:48,55`、`docs/decisions/drafts/573-ab-measurements.md:126-130`

ただし、MVP単独の断片が無いことは前節の未実測[Must]として残ります。
## 収束の見込み

3件の[Must]を構造判定と追加断片で閉じれば、次巡は実測結果と本文の同期確認が中心になり、同種の回避経路を再提示する見込みはありません。  
分類語の追加だけで直す場合は同種の言い換え回避が残るため、巡を重ねず、適用対象の縮小または意味判定方式の再設計へ送るべきです。