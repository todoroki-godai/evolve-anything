"""world_context.py — evolve 物語ナレーション用のPJ固有世界観管理。

PJ ごとに架空の世界観を生成・永続化し、evolve の各ステップで参照する。
初回 evolve 時に CLAUDE.md から LLM で世界設定を生成して保存、
以降は同じ world-context.json を参照することで物語の継続性を保つ。

CLI:
  python3 world_context.py --load
      JSON が存在すれば stdout に出力して exit 0。なければ exit 1。
  python3 world_context.py --generate --claude-md CLAUDE.md --slug <slug>
      CLAUDE.md を読んで LLM で世界設定を生成 → 保存 → stdout に出力。

SKILL.md での典型的な使い方:
  python3 scripts/lib/world_context.py --load 2>/dev/null || \\
    python3 scripts/lib/world_context.py --generate --claude-md CLAUDE.md \\
      --slug "$(basename $(git rev-parse --show-toplevel))"
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

_PLUGIN_DATA_ENV = os.environ.get("CLAUDE_PLUGIN_DATA", "")
DATA_DIR = Path(_PLUGIN_DATA_ENV) if _PLUGIN_DATA_ENV else Path.home() / ".claude" / "rl-anything"

try:
    from growth_level import compute_level as _compute_level  # type: ignore[import]
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from growth_level import compute_level as _compute_level  # type: ignore[import]
    except ImportError:
        _compute_level = None  # type: ignore[assignment]

WORLD_CONTEXT_FILE = "world-context.json"

DEFAULT_WORLD_CONTEXT: dict = {
    "setting": "古い知識の図書館。埃を纏った書架に無数のスキルが眠り、司書が毎夜それらを磨いている。",
    "protagonist_title": "司書",
    "environment_name": "書架",
    "issue_name": "歪み",
    "improvement_name": "修復",
}

_LLM_PROMPT_TEMPLATE = """\
以下のプロジェクト説明から、Claude Code の環境を舞台にした架空の世界観を JSON で生成してください。

キー（必須）:
- setting: このプロジェクトの世界を描写する2文（場所・雰囲気）
- protagonist_title: evolve を実行する者の呼称（例: 魔法使い、錬金術師、番人）
- environment_name: スキル環境の呼称（例: 知識の塔、試験場、魔法書院）
- issue_name: バグ・問題の呼称（例: 歪みの影、エントロピーの蟲、混沌の残滓）
- improvement_name: 改善・変更の呼称（例: 輝く刻印、知恵の結晶、錬金成果）

プロジェクト説明:
{description}

JSON のみ返す。説明・前置き不要。
"""


def _slug_filename(slug: str) -> str:
    """slug を安全なファイル名に変換する（path 区切り・空白を `_` へ）。

    DATA_DIR はプラグイン全体で 1 つ（CLAUDE_PLUGIN_DATA 未設定時は全 PJ 共通）の
    ため、world-context.json を単一ファイルで持つと先に evolve した別 PJ の世界観が
    流用される（cross-project 汚染）。slug 別ファイルにスコープして分離する。
    """
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", slug.strip()) or "default"
    return f"world-context-{safe}.json"


def _world_path(data_dir: Path, slug: str = "") -> Path:
    """world-context の保存パスを返す。

    slug 指定時は `world-contexts/<slug>.json`（PJ 別スコープ）、
    slug 未指定時は従来の `world-context.json`（後方互換のグローバルパス）。
    """
    if slug and slug.strip():
        return data_dir / "world-contexts" / _slug_filename(slug)
    return data_dir / WORLD_CONTEXT_FILE


def load_world_context(data_dir: Path = DATA_DIR, slug: str = "") -> Optional[dict]:
    """キャッシュ済み world-context を読む。なければ None。

    slug 指定時は per-slug パスのみを参照する（グローバルへフォールバックしない＝
    別 PJ の世界観を拾わない）。
    """
    path = _world_path(data_dir, slug)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def generate_world_context(claude_md_text: str, project_slug: str) -> dict:
    """CLAUDE.md テキストから LLM で世界設定を生成する。

    失敗時（LLM エラー・パース失敗）は DEFAULT_WORLD_CONTEXT を返す。
    LLM 呼び出しは subprocess.run(["claude", ...]) で行う。
    テストでは subprocess.run をモック対象とする。

    戻り値には total_evolve_count=0 / last_evolve_date=None /
    current_level=None / previous_level=None / generated_at / project_slug
    が含まれる。
    """
    description = claude_md_text[:600].strip()
    prompt = _LLM_PROMPT_TEMPLATE.format(description=description)

    world: dict = {}
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            raw = result.stdout.strip()
            parsed = json.loads(raw)
            # LLM が {"result": {...}} や {"content": "..."} を返す場合に対応
            if isinstance(parsed, dict):
                # トップレベルに必須キーがあればそのまま使う
                required = {"setting", "protagonist_title", "environment_name",
                            "issue_name", "improvement_name"}
                if required.issubset(parsed.keys()):
                    world = parsed
                else:
                    # ネストされた dict を探す
                    for v in parsed.values():
                        if isinstance(v, dict) and required.issubset(v.keys()):
                            world = v
                            break
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, KeyError):
        pass

    if not world:
        world = dict(DEFAULT_WORLD_CONTEXT)

    return {
        **{k: world.get(k, DEFAULT_WORLD_CONTEXT[k]) for k in DEFAULT_WORLD_CONTEXT},
        "generated_at": datetime.date.today().isoformat(),
        "project_slug": project_slug,
        "total_evolve_count": 0,
        "last_evolve_date": None,
        "current_level": None,
        "previous_level": None,
    }


def save_world_context(
    data_dir: Path = DATA_DIR,
    ctx: Optional[dict] = None,
    env_score: Optional[float] = None,
    slug: str = "",
) -> dict:
    """world-context を保存する。

    保存時に以下を自動更新する:
    - total_evolve_count: += 1
    - last_evolve_date: 今日の ISO 日付 (YYYY-MM-DD)
    - current_level / previous_level: env_score が渡された場合のみ更新
      (previous_level ← current_level, current_level ← compute_level(env_score).level)

    保存先は slug でスコープする。slug 未指定時は ctx["project_slug"] から導出し、
    それも無ければ従来のグローバルパスに書く（後方互換）。

    戻り値: 保存後の ctx dict。
    """
    if ctx is None:
        ctx = {}

    ctx = dict(ctx)

    ctx["total_evolve_count"] = ctx.get("total_evolve_count", 0) + 1
    ctx["last_evolve_date"] = datetime.date.today().isoformat()

    if env_score is not None and _compute_level is not None:
        try:
            info = _compute_level(float(env_score))
            ctx["previous_level"] = ctx.get("current_level")
            ctx["current_level"] = info.level
        except Exception:
            pass

    effective_slug = slug.strip() if slug else str(ctx.get("project_slug", "") or "").strip()
    path = _world_path(data_dir, effective_slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ctx, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ctx


# ── CLI ──────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="evolve 物語ナレーション用の世界観管理ツール",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--load",
        action="store_true",
        help="world-context.json が存在すれば stdout に出力して exit 0。なければ exit 1。",
    )
    group.add_argument(
        "--generate",
        action="store_true",
        help="CLAUDE.md から LLM で世界観を生成して保存し、stdout に出力する。",
    )
    parser.add_argument(
        "--claude-md",
        metavar="PATH",
        default="CLAUDE.md",
        help="--generate 時に読む CLAUDE.md のパス（デフォルト: ./CLAUDE.md）",
    )
    parser.add_argument(
        "--slug",
        metavar="SLUG",
        default="",
        help="PJ 別スコープ用の slug。--load では参照先 PJ の特定に、"
             "--generate では project_slug フィールドに使う（PJ 間の世界観汚染を防ぐ）",
    )
    parser.add_argument(
        "--data-dir",
        metavar="DIR",
        default=str(DATA_DIR),
        help=f"world-context.json の保存先ディレクトリ（デフォルト: {DATA_DIR}）",
    )
    return parser


def _print_ctx(ctx: dict) -> None:
    """コンソール向けサマリと JSON を出力する。"""
    env_name = ctx.get("environment_name", "?")
    title = ctx.get("protagonist_title", "?")
    count = ctx.get("total_evolve_count", 0)
    last = ctx.get("last_evolve_date")

    if count == 0:
        label = "初回"
    elif last:
        today = datetime.date.today()
        try:
            delta = (today - datetime.date.fromisoformat(last)).days
            label = f"{delta}日ぶり" if delta > 0 else "今日"
        except ValueError:
            label = "継続"
    else:
        label = "継続"

    print(f"世界: {env_name}（{label}）| {title} #{count + 1} の冒険が始まる…", file=sys.stderr)
    print(json.dumps(ctx, ensure_ascii=False))


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir)

    if args.load:
        ctx = load_world_context(data_dir, args.slug)
        if ctx is None:
            return 1
        _print_ctx(ctx)
        return 0

    # --generate
    claude_md_path = Path(args.claude_md)
    claude_md_text = ""
    if claude_md_path.exists():
        try:
            claude_md_text = claude_md_path.read_text(encoding="utf-8")
        except OSError:
            pass

    ctx = generate_world_context(claude_md_text, args.slug)
    ctx = save_world_context(data_dir, ctx, slug=args.slug)
    _print_ctx(ctx)
    return 0


if __name__ == "__main__":
    sys.exit(main())
