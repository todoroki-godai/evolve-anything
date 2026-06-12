"""dogfood — 通し評価ゲート（#496）のロジックパッケージ。

「テスト緑・evolve 無エラー・でも成果物がバグだらけ」を構造的に防ぐ 3 層ゲートの
実装ロジックを置く。pytest 非依存（Layer3 は「ユーザーと同じ素の起動経路」を再現する
ため conftest の sys.path 補完 / HOME 隔離の下駄を意図的に避ける）。

エントリポイントは ``bin/rl-dogfood-gate`` → ``dogfood.cli.main``。

層構成:
  - Layer 1: dogfood E2E（dry-run 不変 SHA256 + 実 PJ ingest E2E）
  - Layer 2: report invariants（result JSON の機械検査）
  - Layer 3: SKILL.md コードブロック抽出実行（安全分類つき）
"""
