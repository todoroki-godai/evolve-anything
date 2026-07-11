"""daily — 毎朝の定期 evolve queue 実行 + SessionStart 通知（#80 Phase 1b）。

Phase 1a の `evolve-fleet queue`（学習素材ベース・ゼロ LLM）を macOS launchd で毎朝 1 回
自動実行し、結果（evolve 待ち PJ 一覧）を対話セッション開始時に systemMessage で通知する。

無人で回せるのは決定論パイプライン（ingest→queue）まで。適用は対話セッションで人間が承認する。

- ``plist``         — launchd plist 生成 + runner コマンド文字列（場所・実行時刻・ラベル）
- ``queue_notice``  — evolve-queue.json reader + SessionStart 通知メッセージ生成（stale 判定）
- ``icebox_notice`` — icebox-status.json reader + SessionStart 通知メッセージ生成（icebox 棚卸しの
  気づきトリガー、#194）

``evolve-queue.json`` / ``icebox-status.json`` は各コマンドの出力をそのまま保存した read 専用の
派生物（SoR ではない）。store_registry には登録しない。
"""
