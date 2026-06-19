# ADR-040: handover スキルを廃止し checkpoint 機構へ統合する

- Status: Accepted
- Date: 2026-06-05
- Related: [ADR-019](019-plugin-bin-directory-migration.md)（bin/ CLI 分離、当時 `handover.py` を importable module 化）

## Context

`handover` スキルは「セッションの作業状態を構造化ノート（`.claude/handovers/*.md`）に書き出し、次セッションへ手動で引き継ぐ」ためのスキルだった。`restore_state.py`（SessionStart hook）の `_detect_handover()` が次セッション冒頭で最新ノートを検出し、Deploy State / Next Actions をプレビュー表示する連携になっていた。

しかし運用実態として handover は使われなくなっていた。理由は **同じ restore_state hook の checkpoint 復元機構が、作業文脈（git_branch / recent_commits / uncommitted_files / evolve_state）を SessionStart で自動復元するようになった**ため。手動でノートを書く動機が checkpoint 自動復元に吸収された。残るのは「人が読む引き継ぎ文・次の判断意図」を手書きする用途だけだったが、それも `/compact`（同一セッション継続）と checkpoint（セッション跨ぎ自動復元）でほぼ代替できており、手間に見合わなくなっていた。

機能を3層で整理すると役割は明確に分かれている:

| 層 | 役割 | トリガー | handover 依存 |
|----|------|----------|--------------|
| compact | 同一セッションのコンテキスト圧縮 | 自動/手動 | なし |
| checkpoint 復元（restore_state コア） | branch/commit/evolve_state を自動復元 | SessionStart | **なし** |
| handover skill + `_detect_handover` | 手書きノートを書き出し→次セッションでプレビュー | 手動 | あり |

checkpoint 復元のコア（`common.find_latest_checkpoint`）は handover に一切依存しておらず、handover を消しても無傷である点が決め手になった。

## Decision

handover スキルを廃止し、作業文脈の引き継ぎは checkpoint 機構へ一本化する。

1. `skills/handover/`（SKILL.md / scripts / tests）と `bin/rl-handover` を削除。
2. `restore_state.py` から handover 依存（`_detect_handover` / `_extract_section` / handover.py の import / 関連定数）を削除し、`handle_session_start` から呼び出しを外す。**checkpoint 復元・work_context サマリ・session title 生成は温存**。
3. `ctx_guard.py` の context 逼迫警告から「/handover で引き継ぎ」案内を削除し、「作業文脈は checkpoint が次セッションに自動復元」へ置き換え。
4. ドキュメント（README(.ja).md / SPEC.md / spec/api.md / spec/architecture.md / evolve-anything-advisor.md）から handover 行を除去。
5. 過去の ADR-019 は歴史記録として変更しない。

## Consequences

- `.claude/handovers/*.md` の自動プレビュー表示は無くなる。既存ノートが残っていても読まれないだけで害はない。
- セッション跨ぎの作業文脈引き継ぎは checkpoint 機構が継続して担う（むしろ手動より確実）。
- 公開コマンド `/evolve-anything:handover` が消えるため利用者影響あり → MINOR bump 相当。
- 「人が読む引き継ぎ文・次の判断意図」を明示的に残したい場合は `/compact` 前の手動メモか、必要なら将来 checkpoint 側に "next intent" フィールドを足す余地を残す（YAGNI で今は入れない）。
