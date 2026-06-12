"""agent-brushup SKILL.md 記載コードブロックのインポートパス回帰テスト (#487)。

背景: agent-brushup/SKILL.md Step1 が以下の2経路とも実行不能だった:
  1. CLI: python3 ${CLAUDE_PLUGIN_ROOT}/scripts/lib/agent_quality.py scan "$(pwd)"
     → agent_quality.py に if __name__ == "__main__" が存在しない幻の CLI
  2. Python フォールバック: from agent_quality import scan_agents, check_quality, check_upstream
     → sys.path 設定なしで ModuleNotFoundError: No module named 'lib'

修正: Step1 を sys.path 設定込み python3 -c ブロックに統一（prune #488 / evolve #479 と同型）

このテストはインポートが実際に成功することを決定論で検証する（LLM 非依存）。
"""
import os
import re
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"

if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))


def test_agent_quality_imports_resolve_with_lib_only():
    """scripts/lib だけを sys.path に追加すれば agent_quality を import できること (#487)。

    conftest なしの素の python3 -c で実行可能であることを保証する。
    """
    import importlib
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                f"import sys; sys.path.insert(0, {str(_LIB)!r}); "
                "from agent_quality import scan_agents, check_quality, check_upstream; "
                "print('ok')"
            ),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": ""},  # 既存 PYTHONPATH を消して素の環境を再現
    )
    assert result.returncode == 0, (
        f"agent_quality の import が失敗:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stdout.strip() == "ok"


def test_agent_brushup_skill_md_uses_plugin_root_path():
    """agent-brushup/SKILL.md の sys.path 設定行が ${CLAUDE_PLUGIN_ROOT} ベースの絶対パスを使っていること。

    `sys.path.insert(0, 'scripts/lib')` のような相対パスが残っていないことを確認する。
    """
    skill_md = _PLUGIN_ROOT / "skills" / "agent-brushup" / "SKILL.md"
    assert skill_md.exists(), f"agent-brushup/SKILL.md が見つからない: {skill_md}"

    content = skill_md.read_text(encoding="utf-8")
    # 相対 sys.path パターン（違反）
    relative_syspath = re.compile(r"""sys\.path\.insert\(\s*0\s*,\s*['"]scripts/lib['"]""")
    violations = [
        line.strip()
        for line in content.splitlines()
        if relative_syspath.search(line)
    ]
    assert not violations, (
        "agent-brushup/SKILL.md に相対 sys.path 参照が残っています (#487):\n"
        + "\n".join(violations)
    )

    # 修正後: sys.path.insert が CLAUDE_PLUGIN_ROOT ベースのパスを使っていること
    syspath_lines = [
        line.strip()
        for line in content.splitlines()
        if "sys.path.insert" in line
    ]
    assert syspath_lines, (
        "agent-brushup/SKILL.md に sys.path.insert 行が見つからない（コードブロックが削除された？）"
    )
    for line in syspath_lines:
        assert "CLAUDE_PLUGIN_ROOT" in line or "_root" in line, (
            f"sys.path.insert 行が _root (CLAUDE_PLUGIN_ROOT 由来) を使っていない: {line!r}"
        )


def test_agent_brushup_skill_md_no_phantom_cli():
    """agent-brushup/SKILL.md が python3 <script> scan ... 形式の幻の CLI を参照していないこと (#487)。

    agent_quality.py に if __name__ == "__main__" が存在しないため、
    python3 scripts/lib/agent_quality.py scan ... は実行不能。
    修正後は python3 -c ブロック形式のみを使う。
    """
    skill_md = _PLUGIN_ROOT / "skills" / "agent-brushup" / "SKILL.md"
    content = skill_md.read_text(encoding="utf-8")

    # 違反パターン: python3 ... agent_quality.py scan ...（直接スクリプト実行）
    phantom_cli = re.compile(r"python3\s+.*agent_quality\.py\s+scan")
    violations = [
        line.strip()
        for line in content.splitlines()
        if phantom_cli.search(line)
    ]
    assert not violations, (
        "agent-brushup/SKILL.md に幻の CLI 呼び出しが残っています (#487):\n"
        + "\n".join(violations)
        + "\nagent_quality.py には if __name__ == '__main__' が存在しません"
    )
