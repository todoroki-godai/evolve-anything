# Current Limitations / Known Issues

> このファイルは SPEC.md から分離された詳細仕様です。
> 概要は [SPEC.md](../SPEC.md) を参照してください。

Last updated: 2026-06-05 (ADR-038: SubagentStop additionalContext)

- **episodic 層 (v1.61.0)** — `prune_expired()` は `find_episodic_duplicates` 内で opportunistic 呼び出し済みだが audit 統合は未実装。`--promote-episodic` は `reflect_status == "applied"` の事前検証なし（agent の shell 呼び出しスキップで昇格漏れの可能性）。Concurrent first-write conflict は未対策（単一ユーザー用途で実用上問題なし）
- **subagent token 追跡 v1.5** — `<pj_dir>/<session-uuid>/subagents/*.jsonl` の ingest 対応済み（`isSidechain=True` でマーク）。ただし subagent の token 消費は主セッションの `message.usage` にも内包されるため二重カウントが生じる可能性あり。fleet の CACHE_HIT / REUSE は合算値で表示
- Subagents レイヤー: 乱立検知（SubagentStop hook が閾値超過を systemMessage〔user 向け〕+ additionalContext〔Claude 向け、CC v2.1.163、ADR-038〕の両方で surface し subagent-guard.md を実エンフォース）は実装済み。観測・測定・進化の高度化は未着手（roadmap 参照）
- CLAUDE.md レイヤーの進化は reflect 反映のみ（自動修正なし）
- openspec/specs/ は機能仕様のみ（ADR 変換対象なし）。ディレクトリはアーカイブとして残存
- audit の CLAUDE.md line_limit_violation は warning_only
