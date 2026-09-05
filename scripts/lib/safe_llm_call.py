"""safe_llm_call — 無人 daily runner から claude -p を安全に呼ぶ単一ソース（#410 [Must]A/[Must]F）。

無人で毎朝走るバッチ意味判定（``correction_semantic.judge_runner`` / ``verbosity.judge``）は、
判定対象の**生の対話ログ本文**を prompt に埋め込んで Haiku に渡す。発話に prompt injection が
混入していると、ツールを封じたつもりの CLI フラグが実は効かず実行されてしまう危険がある。

**実測結果（#410 codex round1 レビュー・2026-08-10）**: このマシンの
``~/.claude/settings.json`` は ``permissions.defaultMode = "bypassPermissions"``。以下は
単体では**いずれもこの既定を上書きできず、実際にツールが実行された**（``/tmp`` に置いた
ランダム秘密文字列を Read/Bash で読ませる decisive test で、応答に実際の秘密値が漏洩する
ことで確認。テキスト応答が「実行できません」と言うだけでは判断材料にならない —
モデルが「実行したふり」をする／逆に実行したのに謙遜する、どちらも起こりうるため、
**実際に秘密値が漏れるか**という behavioral な証拠だけを信頼する）:

  - ``--allowedTools ""``
  - ``--disallowedTools <tool名>``（bare 名・``ToolName(*)`` パターン名の両方を個別に確認）
  - ``--permission-mode manual``

これら3つはこのマシンの ``bypassPermissions`` 既定を上書きできなかったが、
``--settings`` に JSON で ``permissions.deny``（全ビルトインツール名）+
``permissions.defaultMode="default"`` を明示する組み合わせは 5 回以上の再試行で毎回
確実に阻止できた（bypassPermissions を明示的に上書きする）。

**実測結果（#410 codex round2 レビュー・2026-08-11、``--tools`` を主防御に格上げ）**:
round1 では ``--tools <tools...>``（"" で built-in セット全体を無効化。``claude --help``
実測: "Use "" to disable all tools"）の存在を見落としていた。codex 指摘のとおり
``--settings`` の deny 列挙方式は**将来ツールが増えたとき fail-open する**（新ツールが
``BUILTIN_TOOL_NAMES`` に追記されるまで使用可能なままになる）。``--tools ""`` は
列挙に依存せず built-in セットそのものを空にするため、これを**主防御**に格上げする。

  - ``--tools ""`` 単体（+ ``--strict-mcp-config``）でも decisive test（秘密ファイル
    非漏洩・プロンプトインジェクション風の指示への耐性）を通過することを確認した
  - ただし ``--tools ""`` は「built-in セット」だけを対象とし、**MCP サーバ由来のツールは
    対象外**（実測: ``--tools ""`` 単体だと応答が Google Drive 系 MCP ツールに言及した）。
    そのため ``--strict-mcp-config``（``--mcp-config`` を渡さず MCP サーバを一切ロードしない）
    と組み合わせて初めて MCP 経路も塞げる
  - ``claude --help`` 実測: ``-p``/``--print`` の説明に **"Settings files that fail
    validation are silently ignored in this mode (no error dialog is shown)."**
    と明記されている。つまり ``--settings`` の中身に将来の CLI バージョンとの非互換等で
    妥当性検証エラーが起きても、無人実行（``-p``）では**エラーも出さず黙って無視される**。
    ``--settings`` deny を単独の砦にできない実測上の根拠がこれで、``--tools ""`` を主防御・
    ``--settings`` deny を **defense-in-depth**（片方が万一無効化されても他方が効く）として
    残す設計にした

補足（``num_turns`` / ``permission_denials`` について）: 同一の安全な組み合わせで複数回
実行しても ``num_turns`` は 1〜4 の間でばらつき、``permission_denials`` は空配列のままだった
（モデルが「ツールが無効」と事前に認識して**そもそも呼び出しを試みない**ため、denial
イベント自体が発生しないと考えられる）。したがってこの2指標は判定の**唯一の**根拠にできず、
本モジュールの安全性は「実際に秘密の中身が応答に現れるか」という決定論的な behavioral test
（``/tmp`` の乱数秘密ファイルを読ませて中身が漏れないことを確認）で担保した。

``--bare`` は ``ANTHROPIC_API_KEY`` 直書き専用（OAuth/subscription 認証非対応）でこの PJ の
想定運用と非互換のため使わない。

**実測結果（#410 codex round3 レビュー・2026-08-11、``--safe-mode`` を追加）**:
``--tools ""`` と ``--strict-mcp-config`` は built-in ツールと MCP サーバを塞ぐが、
**hooks・plugins・CLAUDE.md 等の customization は素通し**だった（``--settings`` は既存設定
への additional settings であり hooks を無効化しない）。生ログを受けた ``UserPromptSubmit``
hook が外部コマンドを実行する経路が三重防御の外に残っていた。

``claude --help`` 実測: ``--safe-mode`` は "Start with all customizations (CLAUDE.md,
skills, plugins, hooks, MCP servers, custom commands and agents, output styles,
workflows, custom themes, keybindings, and more) disabled ... Admin-managed (policy)
settings still apply. Auth, model selection, built-in tools, and permissions work
normally. Sets CLAUDE_CODE_SAFE_MODE=1." — built-in tools・permissions・auth・model 選択は
normal に動作するため、``--tools ""`` / ``--settings`` deny とは独立に積み上げられる。

  - hook 経路が実際に止まることを実測: 無害な ``UserPromptSubmit`` hook
    （マーカーファイルへ1行 echo するだけ）を ``--settings`` 経由で仕込み、
    ``--safe-mode`` 無しでは発火（マーカー生成）・``--safe-mode`` 有りでは発火しない
    （マーカー未生成）ことを確認した
  - 全防御（``--tools ""`` + ``--safe-mode`` + ``--strict-mcp-config`` +
    ``--settings`` deny）を組み合わせた状態でも decisive test（秘密ファイル非漏洩・
    プロンプトインジェクション風の指示への耐性）を再実測し通過を確認した
  - judge が壊れないことも実測: 実際のバッチ判定プロンプト（1発話・修正判定を促す形式）を
    全防御込みで送り、期待どおりの厳格 JSON verdict（``{"verdicts": [{"index": 0, ...}]}``）
    が返ることを確認した（``parse_verdicts_result`` が既に対応する code fence 付き応答）

[Must]F: ``returncode`` を検査する。非ゼロ終了時に ``stdout``（valid/partial な JSON を含み
うる）をそのまま呼び出し側へ渡すと誤って永続化されうるため、``ClaudeCallError`` を送出し
呼び出し側の既存の「呼び出し失敗 → 未判定のまま次回に残す」経路（try/except）に合流させる。

``correction_semantic.judge_runner.call_haiku`` / ``verbosity.judge.call_haiku`` の両方が
本モジュールを経由する（片方だけ直すと pitfall_copied_parse_convention_partial_fix と同型の
desync を招く）。
"""
from __future__ import annotations

import json
import subprocess
from typing import List, Optional

# claude --help 実測時点（2.1.226）のビルトインツール名の網羅。新ツールが追加されたら
# 追従が必要（このモジュールが唯一の deny リスト定義なので更新箇所は1箇所で済む）。
BUILTIN_TOOL_NAMES: List[str] = [
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "Task",
    "NotebookEdit",
    "BashOutput",
    "KillBash",
    "TodoWrite",
    "SlashCommand",
    "AskUserQuestion",
    "ExitPlanMode",
]

DEFAULT_TIMEOUT_SECONDS = 180


def _safe_settings_json() -> str:
    """全ビルトインツールを deny し、bypassPermissions 等の既定を上書きする settings JSON。"""
    return json.dumps(
        {"permissions": {"deny": list(BUILTIN_TOOL_NAMES), "defaultMode": "default"}}
    )


class ClaudeCallError(RuntimeError):
    """claude -p 呼び出しが非ゼロ終了した（#410 [Must]F）。"""


def call_claude_headless(
    prompt: str,
    *,
    model: str = "haiku",
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    json_schema: Optional[str] = None,
) -> str:
    """無人・ツール封じ済みの claude -p 呼び出し（subprocess の唯一の集約点）。

    Args:
        prompt:  ユーザー発話等の untrusted 本文を含みうるプロンプト全文。
        model:   モデルエイリアス（既定 "haiku"）。
        timeout: subprocess timeout 秒（既定 180）。
        json_schema: 構造化出力の JSON schema。指定時だけ CLI へ渡す。

    Returns:
        stdout を strip した文字列。

    Raises:
        ClaudeCallError: プロセスが非ゼロ終了した場合。
        subprocess.TimeoutExpired: タイムアウトした場合（呼び出し側の except で処理する）。
    """
    cmd = [
        "claude",
        "-p",
        prompt,
        "--model",
        model,
        # 主防御（#410 round2 [Must]A）: built-in ツールセット全体を無効化する。
        # 列挙非依存のため将来ツールが増えても fail-open しない。
        "--tools",
        "",
        # defense-in-depth（-p モードは不正な settings を無言で無視するため単独の砦に
        # しない。モジュール docstring 参照）。
        "--settings",
        _safe_settings_json(),
        # --tools "" は built-in セットのみが対象で MCP サーバ由来のツールは塞がないため
        # 必須（モジュール docstring 参照）。
        "--strict-mcp-config",
        # #410 round3 [Must]1: hooks/plugins/CLAUDE.md 等の customization 経路を塞ぐ
        # （--tools ""/--strict-mcp-config/--settings deny のどれも hooks は無効化
        # しない。実測で hook 発火の阻止を確認済み・モジュール docstring 参照）。
        "--safe-mode",
        "--no-session-persistence",
    ]
    if json_schema is not None:
        cmd += ["--json-schema", json_schema]
    out = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if out.returncode != 0:
        raise ClaudeCallError(
            f"claude -p exited {out.returncode}: {(out.stderr or '').strip()[:500]}"
        )
    return out.stdout.strip()
