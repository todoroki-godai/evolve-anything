# Python source 行数バジェット
- `scripts/**.py` / `hooks/**.py` は **500行で分割検討**、**800行で分割必須**（`__init__.py`/`conftest.py`/`tests/`配下を除く）
- 800超で着手したら必ず分割計画を SPEC か design doc に書いてからコード追加（audit.py 2046行肥大化の反省、PR #51-#61）
- 上限は `scripts/lib/line_limit.py` の `MAX_PYTHON_SOURCE_LINES` / `MAX_PYTHON_SOURCE_HARD`、検出は `audit.check_python_source_budgets`
- evolve.py 1739行 → `evolve/__init__.py` 156行 + 7 sub-module（#531/ADR-048）: 8 PR 連続 squash merge・keyset snapshot 不変で振る舞い担保（audit.py 2046→178 と同手法）
