# Pitfalls


## Active Pitfalls


### Bash 連続実行後に先送り表現が出やすい
- **Status**: Active
- **Last-seen**: 2026-05-19
- **Root-cause**: behavioral — Bash を 3 回以上連続で実行すると「後で」「別途」「しましょうか？」などの先送り表現を伴う応答が出やすい（rl-anything/docs-platform/sys-bots の3PJ横断セッションログ解析より）。stop hook がブロックするが根本は「タスクが長くなりすぎている」サイン。対処: タスク分割 or subagent 即時委譲。
- **Pre-flight対応**: No
- **Avoidance-count**: 0

## Candidate Pitfalls


## Graduated Pitfalls

