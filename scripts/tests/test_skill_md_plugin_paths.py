"""SKILL.md がプラグイン同梱 scripts/lib を相対参照していないことの回帰テスト。

背景: evolve / spec-keeper の SKILL.md が `python3 scripts/lib/xxx.py` や
`sys.path.insert(0,'scripts/lib')` のように **相対パス**で同梱スクリプトを参照していた。
スキルは対象 PJ の cwd で実行されるため、相対 `scripts/lib/...` は「対象PJ/scripts/lib/...」を
指してしまい、rl-anything 以外の全 PJ で `No such file or directory` になる
（docs-platform の ev-v7 evolve で world_context ロードが毎回失敗し find 迂回していた実害）。

同梱スクリプトは `${CLAUDE_PLUGIN_ROOT}/scripts/lib/...` で絶対参照するのが正準
（audit / cleanup / agent-brushup 等は既にこの形）。本テストは将来の漏れも含めて封じる。

対象外: `scripts/rl/fitness/{name}.py` は generate-fitness が **対象PJに生成する**
プロジェクト固有ファイルなので相対参照が正しい（同梱スクリプトではない）。ここでは
同梱モジュール置き場である `scripts/lib/` への相対参照だけを違反として検出する。
"""
import re
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_SKILLS = _PLUGIN_ROOT / "skills"

# 違反は「同梱スクリプトを実際に実行/import している箇所」に限定する。
# 散文中の `scripts/lib/foo.py` という単なるファイル言及（「foo.py の関数を使う」等の
# 設計説明）は実行されないので対象外。実害があるのは次の2形のみ:
#   - bash 実行:   python3 scripts/lib/foo.py ...
#   - import パス: sys.path.insert(0, 'scripts/lib')
_PY_RUN_REF = re.compile(r"python3?\s+(?:-\S+\s+)*scripts/lib/[A-Za-z_][A-Za-z0-9_]*\.py")
_SYS_PATH_REF = re.compile(r"""sys\.path\.insert\(\s*0\s*,\s*['"]scripts/lib['"]""")


def _iter_skill_md():
    return sorted(_SKILLS.glob("*/SKILL.md"))


def test_skill_md_exist():
    """SKILL.md が1つも無ければ glob のパス誤りなので検出する。"""
    assert _iter_skill_md(), f"SKILL.md が見つからない: {_SKILLS}"


def test_no_relative_plugin_lib_refs_in_skill_md():
    """同梱 scripts/lib を ${CLAUDE_PLUGIN_ROOT} なしで相対参照している行を違反とする。"""
    violations = []
    for skill_md in _iter_skill_md():
        for lineno, line in enumerate(
            skill_md.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if "${CLAUDE_PLUGIN_ROOT}" in line:
                # 同一行で絶対化済み（正準形）。
                continue
            if _PY_RUN_REF.search(line) or _SYS_PATH_REF.search(line):
                rel = skill_md.relative_to(_PLUGIN_ROOT)
                violations.append(f"{rel}:{lineno}: {line.strip()}")

    assert not violations, (
        "プラグイン同梱 scripts/lib を相対参照している SKILL.md があります。"
        "対象PJの cwd で実行すると No such file になるため、"
        "`${CLAUDE_PLUGIN_ROOT}/scripts/lib/...` に統一してください:\n"
        + "\n".join(violations)
    )
