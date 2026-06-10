"""#419: orchestrator 経路と issues.py 経路が同一の hardcoded 検出関数を共有する回帰テスト。

二重実装の divergence が根因だった: issues.py には global/plugin origin 除外があるが
orchestrator.py の同型ループには無く、除外なし経路で gstack スキル散文の `sk-` 部分一致
552 件が混入していた。検出ループを 1 箇所の共通関数 `collect_hardcoded_value_issues` に
集約し、両 call site がそれを呼ぶことで構造的に根治する。
"""
import sys
from pathlib import Path

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

from audit.issues import collect_hardcoded_value_issues  # noqa: E402


class TestSharedCollectionFunction:
    def test_両経路が共通関数を呼ぶ(self):
        """orchestrator / issues.py の両モジュールが共通関数を参照していること（#419）。"""
        from audit import issues as issues_mod
        from audit import orchestrator as orch_mod

        # issues.collect_issues は同モジュールの関数を呼ぶ
        assert hasattr(issues_mod, "collect_hardcoded_value_issues")
        # orchestrator は issues 経由でこの関数を import している
        assert orch_mod.collect_hardcoded_value_issues is issues_mod.collect_hardcoded_value_issues

    def test_global_plugin_origin_は除外される(self, tmp_path, monkeypatch):
        """global / plugin origin のスキルは共通関数で除外される（#419）。"""
        # api_key を含むファイルを 2 つ作る
        custom_md = tmp_path / "custom.md"
        global_md = tmp_path / "global.md"
        fake_key = "sk" + "-" + "a" * 20
        custom_md.write_text(f"key: {fake_key}", encoding="utf-8")
        global_md.write_text(f"key: {fake_key}", encoding="utf-8")

        # classify_artifact_origin を origin 判定で patch
        def fake_classify(path):
            return "global" if path == global_md else "custom"

        monkeypatch.setattr(
            "audit.issues.classify_artifact_origin", fake_classify, raising=False
        )

        artifacts = {"skills": [custom_md, global_md], "rules": []}
        issues = collect_hardcoded_value_issues(artifacts)
        files = {i["file"] for i in issues}
        assert str(custom_md) in files
        assert str(global_md) not in files

    def test_検出結果のスキーマ(self, tmp_path, monkeypatch):
        """共通関数は collect_issues / orchestrator が期待する dict 形を返す（#419）。"""
        md = tmp_path / "x.md"
        fake_key = "sk" + "-" + "b" * 20
        md.write_text(f"key: {fake_key}", encoding="utf-8")
        monkeypatch.setattr(
            "audit.issues.classify_artifact_origin", lambda p: "custom", raising=False
        )
        issues = collect_hardcoded_value_issues({"skills": [md], "rules": []})
        assert len(issues) == 1
        it = issues[0]
        assert it["type"] == "hardcoded_value"
        assert it["file"] == str(md)
        assert it["source"] == "detect_hardcoded_values"
        assert "detail" in it
