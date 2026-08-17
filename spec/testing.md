# テスト詳細

CLAUDE.md の `## テスト` から移設（#415 圧縮）。運用に必要な3項目（opt-out マーカー / `-n 0` /
`UPDATE_SNAPSHOTS=1` の union merge）は hot に残してある。ここは経緯・数値・issue 番号。

フルスイートはデフォルトで全件実行する（slow マーカーによる deselect は無し）。
収集パスは `pytest.ini` の `testpaths` が単一ソース。新しい tests/ を足したら testpaths に追記する
（漏れは audit の Testpaths Coverage チェック = `scripts/lib/testpaths_coverage.py` が検出する。#468）。
pytest-xdist `-n auto` で並列実行（`pytest.ini` の `addopts` に設定済み）、2026-06-12 時点で約 32 秒・4972件（直列だと約 135 秒）。#457 で run_evolve 系の実環境ストア読みを隔離し直列 32 分→1 分→xdist で約 32 秒に短縮。**並行 worker に回させるときは `-n 0` で直列**（targeted テストまで多プロセス化し CPU 飢餓するため）。

`test_evolve_keyset_snapshot.py` は evolve-anything 自身の実スキル構成に dry-run するため、計測窓 suppress の暦日境界等で regression でないのに出たり消えたりするキーがある。`fixtures/evolve_keyset_optional.txt` に条件付き透明化キーの prefix を宣言し、宣言済み prefix の増減のみ許容する二層 golden 方式（#209）。`UPDATE_SNAPSHOTS=1` は golden 上書きでなく既存キーとの union merge（条件付きキーを golden から消さない）。

リリース前は `bin/evolve-dogfood-gate --layer all` も全緑を確認する（pytest が掬えない実環境の繋ぎ目
— dry-run 不変 / report invariants / SKILL.md コードブロック — を検査する。#496）。フル `all` は
Layer1b の drain が重く約3.5分かかる。日常 push は **`--layer light`**（Layer1a 不変 + Layer2 +
Layer3、約十数秒。重い Layer1b drain と ingest E2E を除外）が `pre-push` hook 経由で**非ブロッキング
警告**として自動実行される。hook ソースは `scripts/git-hooks/pre-push.local`、導入は
`bash scripts/git-hooks/install.sh`（gstack-redact の managed pre-push が chain する `pre-push.local`
へコピー。共有 hooks なので worktree 横断で1回でよい）。

**HOME 隔離は root conftest の autouse が全テストへ自動適用する（#119・旧 #457）。** `run_evolve` は
`project_dir=tmp_path` でも後段フェーズ（utterance ingest / prune global check /
weak_signals / correction_semantic）が `Path.home()/.claude/projects`（実環境 ≈9925 jsonl /
1.9GB）を default 走査するため、未隔離だと 1 件数十秒に膨張する。以前は
`skills/evolve/scripts/tests/` の conftest autouse と各テストの手動
`from test_home_isolation import isolate_home` 頼みで「隔離を知らないと膨張する罠」が残っていた
（#457）。#119 で root `conftest.py` の autouse（`isolate_home` を single source から import）へ
昇格し、**全 testpath を一律に隔離する**（新規テストは何もしなくても隔離される）。隔離 HOME は
test の `tmp_path` の外（`tmp_path_factory` 側）に作る（`tmp_path` を列挙する fleet
enumerate / does-not-write 系を汚染しないため）。実 `~/.claude` を読む必要があるテスト
（live API bench / 実 PJ ingest）は `@pytest.mark.real_home`（または `bench` / `bench_ingest`）で
opt-out する。ルート conftest の `CLAUDE_PLUGIN_DATA`(=DATA_DIR) 隔離は `Path.home()` 由来パスには
効かないため、HOME 隔離はこの autouse が担う。
