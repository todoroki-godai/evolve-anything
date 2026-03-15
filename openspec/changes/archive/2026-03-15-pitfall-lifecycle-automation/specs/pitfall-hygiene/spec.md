## MODIFIED Requirements

### Requirement: Avoidance-count based graduation
pitfall_hygiene() は回避回数ベースの卒業候補判定に加え、SKILL.md/references/ 統合済み検出と TTL ベースの削除候補を統合する（SHALL）。

#### Scenario: High frequency skill graduation
- **WHEN** 日常的に使用されるスキル（頻度スコア3）の Active pitfall が10回連続でトリガーされなかった
- **THEN** 卒業候補として表示される

#### Scenario: Low frequency skill graduation
- **WHEN** 月数回使用のスキル（頻度スコア1）の Active pitfall が3回連続でトリガーされなかった
- **THEN** 卒業候補として表示される

#### Scenario: Avoidance count reset on trigger
- **WHEN** pitfall が回避カウント中にトリガーされた
- **THEN** Avoidance-count がリセットされ、Last-seen が更新される

#### Scenario: Integration-based graduation proposal
- **WHEN** Active pitfall の Root-cause が SKILL.md に統合済みと判定された
- **THEN** 回避回数に関わらず `graduation_proposals` に追加される

#### Scenario: TTL archive candidates in hygiene result
- **WHEN** Graduated 項目が TTL を超過している
- **THEN** `archive_candidates` フィールドに削除候補として含まれる

#### Scenario: Line count guard triggered
- **WHEN** pitfalls.md が 100 行を超過している
- **THEN** Cold 層の古い項目から削除候補が提案される

### Requirement: Stale knowledge guard
Last-seen が6ヶ月以上前の Active pitfall に警告を付与する（SHALL）。9ヶ月以上前の場合は削除候補にエスカレーションする。

#### Scenario: Stale pitfall warning
- **WHEN** Active pitfall の Last-seen が6ヶ月以上前である
- **THEN** 「Stale: 最終確認から6ヶ月超 — 現在も有効か検証を推奨」マーカーを付与する

#### Scenario: Stale pitfall escalation to archive
- **WHEN** Active pitfall の Last-seen が9ヶ月以上前である
- **THEN** stale 警告に加えて `archive_candidates` にも追加される

## ADDED Requirements

### Requirement: Hygiene result extended fields
pitfall_hygiene() の返却値に `graduation_proposals`, `archive_candidates`, `codegen_proposals`, `line_count` フィールドを追加する（SHALL）。

#### Scenario: Extended result structure
- **WHEN** pitfall_hygiene() が実行された
- **THEN** 従来の `graduation_candidates`, `cap_exceeded`, `stale_warnings`, `cross_skill_analysis` に加え、`graduation_proposals`, `archive_candidates`, `codegen_proposals`, `line_count` が返却される
