"""scaffold_advisory（#118 (b) advisory 3点セット scaffold）のテスト。

新規 advisory コンポーネント追加の多点同時更新（module + store_registry +
observability builder + keyset snapshot + CONTEXT.md + CLAUDE.md）を、テンプレ生成 +
チェックリストで摩擦を下げる。生成は決定論・LLM 非依存。
"""
import ast
import sys
from pathlib import Path

import pytest

_lib = Path(__file__).resolve().parent.parent
if str(_lib) not in sys.path:
    sys.path.insert(0, str(_lib))

import scaffold_advisory as sa  # noqa: E402


class TestScaffoldAdvisory:
    def test_module_path_and_builder_name(self):
        res = sa.scaffold_advisory("my_check")
        path = "scripts/lib/audit/sections_my_check.py"
        assert path in res.files
        content = res.files[path]
        assert "def build_my_check_section(" in content
        # #115 共通枠を必ず使う（3点セット増殖のコストを下げた基盤）。
        assert "from .advisory import build_advisory_section" in content
        assert "build_advisory_section(" in content

    def test_generated_module_is_valid_python(self):
        res = sa.scaffold_advisory("foo_bar")
        content = res.files["scripts/lib/audit/sections_foo_bar.py"]
        ast.parse(content)  # SyntaxError にならないこと

    def test_registration_line(self):
        res = sa.scaffold_advisory("my_check")
        assert res.registration_line == '    ("my_check", build_my_check_section),'

    def test_checklist_covers_all_wiring_points(self):
        res = sa.scaffold_advisory("my_check")
        joined = "\n".join(res.checklist)
        # store 非依存の多点更新ポイントがチェックリストに出る（追従漏れ防止）。
        # observability 既知 key は _OBSERVABILITY_BUILDERS から動的導出のため別 snapshot
        # 追従は不要（keyset snapshot 追従は store 有りのときだけ・下の test 参照）。
        assert "observability.py" in joined
        assert "_OBSERVABILITY_BUILDERS" in joined
        assert "CLAUDE.md" in joined
        assert "CONTEXT.md" in joined
        assert "test" in joined.lower()

    def test_no_store_checklist_omits_store_registry(self):
        res = sa.scaffold_advisory("my_check", has_store=False)
        joined = "\n".join(res.checklist)
        assert "store_registry" not in joined
        # store が無ければ keyset snapshot 追従も不要（誤誘導しない）。
        assert "keyset" not in joined.lower()

    def test_with_store_checklist_includes_store_registry_and_barrier(self):
        res = sa.scaffold_advisory("my_check", has_store=True)
        joined = "\n".join(res.checklist)
        assert "store_registry" in joined
        assert "store_write" in joined
        # store 有りは keyset snapshot（test_write_barrier）追従が必須（#64/#38 追従漏れ多発点）。
        assert "keyset" in joined.lower() or "snapshot" in joined.lower()

    def test_title_override(self):
        res = sa.scaffold_advisory("my_check", title="My Custom Title")
        content = res.files["scripts/lib/audit/sections_my_check.py"]
        assert "My Custom Title" in content

    def test_invalid_name_rejected(self):
        for bad in ("My-Check", "1check", "has space", "Upper", "dash-name", ""):
            with pytest.raises(ValueError):
                sa.scaffold_advisory(bad)

    def test_valid_snake_case_accepted(self):
        # 例外を投げないこと
        sa.scaffold_advisory("abc")
        sa.scaffold_advisory("a_b_c_123")


class TestCli:
    def test_dry_run_prints_and_writes_nothing(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(sa, "_repo_root", lambda: tmp_path)
        rc = sa.main(["my_check"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "sections_my_check.py" in out
        # dry-run はファイルを書かない
        assert not (tmp_path / "scripts/lib/audit/sections_my_check.py").exists()

    def test_write_creates_module(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(sa, "_repo_root", lambda: tmp_path)
        (tmp_path / "scripts/lib/audit").mkdir(parents=True)
        rc = sa.main(["my_check", "--write"])
        assert rc == 0
        target = tmp_path / "scripts/lib/audit/sections_my_check.py"
        assert target.exists()
        ast.parse(target.read_text(encoding="utf-8"))

    def test_write_refuses_to_clobber(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sa, "_repo_root", lambda: tmp_path)
        target = tmp_path / "scripts/lib/audit/sections_my_check.py"
        target.parent.mkdir(parents=True)
        target.write_text("# existing\n", encoding="utf-8")
        rc = sa.main(["my_check", "--write"])
        assert rc != 0
        # 既存を上書きしない
        assert target.read_text(encoding="utf-8") == "# existing\n"
