# 世界観ロード／初回生成（Step 0.5 詳細）

`--load` が exit 0 で JSON を出した場合はそれを使う（既存世界観・継続）。
**exit 1（初回＝未生成）の場合のみ**、ここのファイルベース2相で生成する（[ADR-037]）。
bash は呼び出しごとに独立プロセスのため、`$SLUG`（と `$PJ`）は SKILL.md Step 0.5 の値を
前提にせず、下記の各ブロックで自前に再導出する（プロセスをまたいだ前提は置かない）。

## exit 1（初回）の生成 — claude -p を呼ばないファイルベース2相

1. **Phase A — 生成リクエストを得る（LLM ゼロ）**:
   ```bash
   PJ="${PJ:-$(pwd)}"  # 対象 PJ の絶対パス（Step 0.5 と同一の束縛パターン。各ブロックで再束縛する）
   SLUG="$(python3 -c "import sys; sys.path.insert(0,'${CLAUDE_PLUGIN_ROOT}/scripts/lib'); from optimize_history_store import resolve_slug; print(resolve_slug(cwd='$PJ'))" 2>/dev/null || echo unknown)"
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lib/world_context.py" --emit-request \
     --claude-md "$PJ/CLAUDE.md" --slug "$SLUG"
   ```
   `--claude-md` は対象 PJ の絶対パスを渡す（相対パス `CLAUDE.md` だと cwd 側の CLAUDE.md を
   誤読する・単一 cwd から他 PJ の project_dir を渡すバッチ経路 #400 で顕在化）。
   stdout は `{"slug":..., "requests":[{"id":"world","prompt":"...","meta":{...}}]}`。
2. **Phase B — Claude（あなた）がインラインで生成**: `requests[0].prompt` を読み、指示どおり
   世界観 JSON（`setting`/`protagonist_title`/`environment_name`/`issue_name`/`improvement_name` の5キー）を
   **インラインで生成**する（claude -p は呼ばない＝interactive subscription 課金）。生成結果を
   `{"world": <生成した world dict>}` の形で `world-resp.json` に Write する。
3. **Phase C — 保存（LLM ゼロ）**:
   ```bash
   PJ="${PJ:-$(pwd)}"  # 同上（各ブロックで再束縛する）
   SLUG="$(python3 -c "import sys; sys.path.insert(0,'${CLAUDE_PLUGIN_ROOT}/scripts/lib'); from optimize_history_store import resolve_slug; print(resolve_slug(cwd='$PJ'))" 2>/dev/null || echo unknown)"
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lib/world_context.py" --save-from-response \
     --response world-resp.json --slug "$SLUG"
   ```
   stdout は保存後の ctx JSON（`--load` と同形）。

## stdout フォーマット（`--load` も `--save-from-response` も同じ）

JSON 1行。例:
`{"setting":"...","protagonist_title":"知識の番人","environment_name":"知識の塔","issue_name":"歪みの影","improvement_name":"輝く刻印","total_evolve_count":42,...}`

Claude はこの JSON を読んで各変数（`environment_name` / `protagonist_title` / `issue_name` / `improvement_name`）を
以降のナレーション指示に展開すること。スクリプトが利用できない場合はナレーション指示をスキップする（evolve の主機能に影響しない）。
