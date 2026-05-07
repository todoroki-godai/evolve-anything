"""scorer_prompts モジュールのテスト。"""

import os

import pytest
from scorer_prompts import (
    DEFAULT_AXIS_PROMPTS,
    DEFAULT_AXIS_WEIGHTS,
    get_axis_prompts,
    write_override,
)


def test_default_prompts_have_three_axes():
    assert set(DEFAULT_AXIS_PROMPTS.keys()) == {"technical", "domain", "structure"}


def test_default_weights_sum_to_one():
    total = sum(DEFAULT_AXIS_WEIGHTS.values())
    assert total == pytest.approx(1.0, abs=0.001)


def test_default_prompts_contain_content_placeholder():
    for axis, prompt in DEFAULT_AXIS_PROMPTS.items():
        assert "{content}" in prompt, f"{axis} prompt missing {{content}}"


def test_get_axis_prompts_returns_defaults_when_no_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    # オーバーライドディレクトリが存在しない場合はデフォルトを返す
    prompts = get_axis_prompts()
    assert prompts == DEFAULT_AXIS_PROMPTS


def test_get_axis_prompts_uses_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    override_dir = tmp_path / "scorer_prompts"
    override_dir.mkdir()
    custom = "Custom technical prompt for {content}"
    (override_dir / "technical.txt").write_text(custom, encoding="utf-8")

    prompts = get_axis_prompts()
    assert prompts["technical"] == custom
    # 上書きしていない軸はデフォルトのまま
    assert prompts["domain"] == DEFAULT_AXIS_PROMPTS["domain"]


def test_write_override_creates_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    custom = "Custom domain prompt: {content}"
    out_file = write_override("domain", custom)
    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8") == custom


def test_write_override_rejects_unknown_axis(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    with pytest.raises(ValueError, match="未知の軸"):
        write_override("unknown_axis", "prompt {content}")


def test_write_override_requires_content_placeholder(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    with pytest.raises(ValueError, match="content"):
        write_override("technical", "prompt without placeholder")


def test_write_override_requires_data_dir():
    # 環境変数未設定時はエラー
    if "CLAUDE_PLUGIN_DATA" in os.environ:
        del os.environ["CLAUDE_PLUGIN_DATA"]
    with pytest.raises(RuntimeError, match="CLAUDE_PLUGIN_DATA"):
        write_override("technical", "prompt {content}")
