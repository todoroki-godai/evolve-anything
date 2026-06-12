"""run_evolve 系テストの HOME 隔離 helper（#457）。

複数の test ディレクトリ（``skills/evolve/scripts/tests/`` と ``scripts/tests/``）が
共有するため、``conftest`` 名を避けた専用モジュールに置く。conftest 名で共有すると
別ディレクトリの同名 ``conftest`` を ``sys.path`` 経由で shadow し、
``from conftest import ...`` が壊れる（pkg 名衝突 pitfall）。

【根因（実測 2026-06-12）】
``run_evolve(project_dir=tmp_path)`` でも、後段 post-processing フェーズが
``Path.home() / ".claude" / "projects"`` を default 走査先に取り、実環境の
~/.claude/projects（≈9925 jsonl / 1.9GB）を読んでいた。具体的には:

  - ``utterance_archive.ingest.ingest_all_projects`` … projects_root 既定 = ~/.claude/projects
  - ``prune`` の global skill check（``safe_global_check`` / ``_get_created_at_from_git``）
    … ~/.claude/skills + git subprocess
  - ``weak_signals`` 言い直し検出 / ``correction_semantic`` … HOME 派生の utterances.db

cProfile 実測（単独・fresh process）: HOME 非隔離 8.69s → HOME 隔離 0.32s。
ルート conftest の DATA_DIR(=CLAUDE_PLUGIN_DATA) 隔離は ``Path.home()`` 由来パスには
効かないため、本 helper が ``HOME`` を空 tmp dir へ隔離して実 store 走査を断つ。
"""
from pathlib import Path


def isolate_home(monkeypatch, tmp_path) -> Path:
    """``HOME`` を tmp dir へ隔離し、空の ``~/.claude/projects`` を用意する。

    ``Path.home()`` は call-time に ``HOME`` を読む（import 時凍結ではない）ので、
    import 後の ``monkeypatch.setenv`` で run_evolve の全フェーズに効く。空 dir を
    指すことで utterance ingest / prune global check / weak_signals 言い直し検出が
    実 store を走査せず即 early-return する。検証ロジック（Phase 3.4 等）には触れない
    ので、テスト意図は不変（変えるのは I/O 先のみ）。
    """
    home = tmp_path / "isolated-home"
    (home / ".claude" / "projects").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    # 一部コードは USERPROFILE / pwd を見るため両方塞ぐ（Path.home() の解決順に追従）。
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home
