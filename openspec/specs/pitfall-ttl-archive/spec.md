## ADDED Requirements

### Requirement: Graduated TTL auto-archive
Graduated 項目は Graduated-date から GRADUATED_TTL_DAYS（30日）経過後に自動削除候補としてレポートする（SHALL）。

#### Scenario: Graduated item past TTL
- **WHEN** Graduated pitfall の Graduated-date が 31 日前である
- **THEN** `archive_candidates` に追加され、evolve Report に「削除候補: {title}（卒業から31日経過）」と表示される

#### Scenario: Graduated item within TTL
- **WHEN** Graduated pitfall の Graduated-date が 15 日前である
- **THEN** 削除候補に含まれない

### Requirement: Stale Active TTL escalation
Last-seen が STALE_KNOWLEDGE_MONTHS + STALE_ESCALATION_MONTHS（6+3=計 STALE_ESCALATION_MONTHS(3) + STALE_KNOWLEDGE_MONTHS(6) = 9ヶ月）未更新の Active/New pitfall を削除候補にエスカレーションする（SHALL）。

#### Scenario: Active pitfall stale escalation
- **WHEN** Active pitfall の Last-seen が 10 ヶ月前である
- **THEN** `archive_candidates` に追加され、「削除候補: {title}（10ヶ月未更新 — 現在も有効か検証を推奨）」と表示される

#### Scenario: Recently seen Active pitfall
- **WHEN** Active pitfall の Last-seen が 2 ヶ月前である
- **THEN** stale 警告も削除候補にも含まれない

### Requirement: Line count guard
pitfalls.md の行数が PITFALL_MAX_LINES（100行）を超過した場合、Cold 層（Graduated→Candidate の古い順）の削除を提案する（SHALL）。

#### Scenario: Line count exceeded
- **WHEN** pitfalls.md が 120 行である
- **THEN** Cold 層の最古項目から順に、PITFALL_MAX_LINES（100行）以下になるまで削除候補を提案する

#### Scenario: Line count within limit
- **WHEN** pitfalls.md が 85 行である
- **THEN** 行数ガードは発火しない

#### Scenario: Insufficient Cold layer
- **WHEN** pitfalls.md が 120 行で、Cold 層（Graduated + Candidate）の削除だけでは PITFALL_MAX_LINES 以下にならない
- **THEN** 削除可能な Cold 層をすべて候補に含めた上で、「Active/New 項目の手動レビューが必要」と警告を表示する

#### Scenario: Oldest item ordering
- **WHEN** Cold 層に複数の削除候補がある
- **THEN** Graduated は Graduated-date の古い順、Candidate は First-seen の古い順でソートし、古いものから優先的に削除候補とする

### Requirement: Archive execution with confirmation
削除候補の実際の削除はユーザー確認後に実行する（SHALL）。dry-run レポートで対象一覧を表示した後、確認を得て削除する。

#### Scenario: Dry-run archive report
- **WHEN** pitfall_hygiene() が archive_candidates を検出した
- **THEN** 削除対象の一覧（タイトル、理由、日数）を表示し、ユーザー確認を求める

#### Scenario: User confirms archive
- **WHEN** ユーザーが削除を承認した
- **THEN** 対象 pitfall が pitfalls.md から削除され、行数が更新される
