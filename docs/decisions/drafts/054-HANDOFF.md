# ADR-054 実装ハンドオーバー（2026-08-12）

会話文脈なしで実装に入るための入口。**設計3本は確定済み・実装未着手**。

## 状態

| 項目 | 状態 |
|---|---|
| PR #423（ADR-054 本体） | マージ済み（`66c9975a`・check-runs 5件 pass 実測） |
| 設計 Phase 0 | **確定**（rev7。codex 2巡 + tacchi 併走） |
| 設計 A0 | **確定**（codex 2巡 + 実コーパスリプレイで全面再測定） |
| 設計 Phase D | **確定**（codex 2巡 + 未決8点を頭が裁定） |
| ADR-054 本体 | 実測で覆った4点を訂正済み（§3.3 / §5-A0 / §5-A1 / §6 / §7.2 / §7.3 / §9） |
| 実装 | **未着手**（コードは1行も変更していない） |

作業ブランチ: `docs/054-phase-designs`（main から分岐）

## 設計文書（この3本が実装の唯一の入力）

| パス | Phase |
|---|---|
| `054-phase0-notification-routing.md` | Phase 0（SessionStart 通知の1行化） |
| `054-a0-capture-repair.md` | A0（correction capture の修理） |
| `054-phaseD-revert-lane.md` | Phase D（accept を revert 可能 lane へ・PR1〜PR4） |

codex レビューログは session scratchpad にあり**再起動で消える**。要点は各文書の
「codex 対応表」節に転記済み＝**そちらが正典**。

## 実測で判明した重要事実（ADR 本体にも反映済み）

1. **A0 の当初仮説2つは実測で否定**（行頭アンカー・500字除外の見直しは効果ゼロ）。真因は**語彙の欠落**。
2. **A0 の効果は小さい**: precision 77.8%（machinery 除外後 87.5%）だが **recall 約4.5%**。
   → **A0 単独では柱3(a) の分子は作れない**。recall は `correction_semantic`（llm_judge）の役割。
3. **`prev_action` が実測窓 1,124件すべて null**（`extractor_version=2` のデータ欠損）→ A1 で充填する。
4. **Phase 0 の「平常時0〜1件発火」は誤り**: 実測4系統・フル文連結412字。digest 化で96字（導線込み125字）。
5. **`after_sha[:12]` が `_decision_event_id` の内部計算と一致**（after 本文を運ぶ必要がない）。
6. **`optimize.py` の `--auto --dry-run` が `approved=True` の entry を書く**＝柱3(b) の分母汚染。PR1 で修正。

## 実装の順序と分割

```
Phase 0 ────────────┐ 並行可
A0 ──→ A2 ──────────┤（A0 の _MACHINERY_MARKERS 追加を A2 が前提にする）
Phase D: PR1 ───────┘ → PR2 → PR3 → PR4（この順は固定）
```

共通の完了条件: `python3 -m pytest` exit 0 / `bin/evolve-dogfood-gate --layer light` /
`claude plugin validate`。各 Phase 固有の完了条件は各設計文書の「完了条件」節。

**worktree で隔離**して impl-worker（sonnet）へ委譲する（`isolation: "worktree"` オプションは使わない。
頭が `git worktree add` してパスを渡す — `rules/worktree-parallel.md`）。

## データの取り扱い（重要）

A0 の実コーパス成果物4本（`scripts/bench/a0_eval_set.jsonl` / `a0_full_census.json` /
`a0_sample_dump.json` / `a0_population_fingerprint.jsonl`）は**他PJの生の人間発話**を含むため
**`.gitignore` 済み＝commit しない**（本 repo は public 化予定）。commit するのは harness 本体
`a0_capture_replay.py` のみ。再現性は `054-a0-capture-repair.md` §2 の sha256 台帳で担保する。
**ラベルは人手判断なので再生成では復元できない** — ローカルの `scripts/bench/` を消さないこと。

## 起票済み・未着手

- **issue #424**: `run_id` の秒精度衝突（Phase D スコープ外と裁定）
- 未着手 Phase: **B**（朝の提示の質・A 後）/ **E**（correction→accept の変換経路・「作ると決定」済み）/ **C**（週1の数字）
