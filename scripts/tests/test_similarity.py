#!/usr/bin/env python3
"""similarity.py のユニットテスト。"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.similarity import (
    build_tfidf_matrix,
    compute_pairwise_similarity,
    filter_merge_group_pairs,
    jaccard_coefficient,
    tokenize,
)


# --- build_tfidf_matrix ---


def test_build_tfidf_matrix_basic():
    """2つのスキルテキストで TF-IDF 行列が返ることを確認。"""
    sklearn = pytest.importorskip("sklearn")

    texts = {
        "skill-a": "python testing framework pytest unittest",
        "skill-b": "javascript react frontend component rendering",
    }
    matrix, features, names = build_tfidf_matrix(texts)

    assert matrix is not None
    assert features is not None
    assert names == ["skill-a", "skill-b"]
    assert matrix.shape[0] == 2


def test_build_tfidf_matrix_sklearn_not_installed():
    """sklearn がない場合に (None, None, None) を返すことを確認。"""
    original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

    def mock_import(name, *args, **kwargs):
        if name == "sklearn.feature_extraction.text":
            raise ImportError("No module named 'sklearn'")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        matrix, features, names = build_tfidf_matrix({"a": "hello world"})

    assert matrix is None
    assert features is None
    assert names is None


# --- compute_pairwise_similarity ---


def test_compute_pairwise_similarity_similar_pair(tmp_path):
    """類似コンテンツのペアが検出されることを確認。"""
    sklearn = pytest.importorskip("sklearn")
    pytest.importorskip("scipy")

    # 非常に類似したコンテンツを作成
    file_a = tmp_path / "skill_a.md"
    file_a.write_text(
        "Deploy application to AWS using CloudFormation templates. "
        "AWS deployment configuration and infrastructure setup. "
        "CloudFormation stack management and deployment pipeline."
    )
    file_b = tmp_path / "skill_b.md"
    file_b.write_text(
        "AWS infrastructure deployment and CloudFormation templates. "
        "Deploy and manage AWS cloud infrastructure resources. "
        "CloudFormation deployment automation and stack updates."
    )

    paths = {
        "deploy-aws": str(file_a),
        "aws-infra": str(file_b),
    }
    results = compute_pairwise_similarity(paths, threshold=0.30)

    assert len(results) >= 1
    assert results[0]["path_a"] == str(file_a)
    assert results[0]["path_b"] == str(file_b)
    assert results[0]["similarity"] >= 0.30


def test_compute_pairwise_similarity_unrelated_pair(tmp_path):
    """無関係なコンテンツが閾値以下でフィルタされることを確認。"""
    sklearn = pytest.importorskip("sklearn")
    pytest.importorskip("scipy")

    file_a = tmp_path / "skill_a.md"
    file_a.write_text(
        "Python testing framework pytest unittest mock assertion. "
        "Test coverage analysis and continuous integration pipeline."
    )
    file_b = tmp_path / "skill_b.md"
    file_b.write_text(
        "Coral reef ecosystems and marine biodiversity conservation. "
        "Ocean currents and deep sea organism adaptation patterns."
    )

    paths = {
        "python-test": str(file_a),
        "marine-bio": str(file_b),
    }
    results = compute_pairwise_similarity(paths, threshold=0.80)

    assert len(results) == 0


def test_compute_pairwise_similarity_empty_input():
    """空辞書で空リスト返却。"""
    results = compute_pairwise_similarity({})
    assert results == []


def test_compute_pairwise_similarity_single_input(tmp_path):
    """1件で空リスト返却。"""
    file_a = tmp_path / "skill_a.md"
    file_a.write_text("Some content here.")

    results = compute_pairwise_similarity({"only-one": str(file_a)})
    assert results == []


def test_compute_pairwise_similarity_file_read_failure(tmp_path, capsys):
    """存在しないファイルパスでスキップ + stderr 警告。"""
    # 1つだけ実在するファイルを用意し、もう1つは存在しないパス
    file_a = tmp_path / "skill_a.md"
    file_a.write_text("Some content for skill A.")

    paths = {
        "real-skill": str(file_a),
        "missing-skill": str(tmp_path / "nonexistent.md"),
    }
    results = compute_pairwise_similarity(paths, threshold=0.50)

    # 読み込み可能なファイルが1件のみなので空リスト
    assert results == []

    captured = capsys.readouterr()
    assert "Warning: failed to read" in captured.err
    assert "nonexistent.md" in captured.err


def test_compute_pairwise_similarity_custom_threshold(tmp_path):
    """threshold=0.90 で結果が変わることを確認。"""
    sklearn = pytest.importorskip("sklearn")
    pytest.importorskip("scipy")

    # ある程度類似しているが完全一致ではないコンテンツ
    file_a = tmp_path / "skill_a.md"
    file_a.write_text(
        "Deploy application to AWS using CloudFormation templates. "
        "AWS deployment configuration and infrastructure setup."
    )
    file_b = tmp_path / "skill_b.md"
    file_b.write_text(
        "AWS infrastructure deployment and CloudFormation templates. "
        "Deploy and manage AWS cloud infrastructure resources."
    )

    paths = {
        "deploy-aws": str(file_a),
        "aws-infra": str(file_b),
    }

    # 低い閾値では検出される
    results_low = compute_pairwise_similarity(paths, threshold=0.30)
    # 高い閾値では検出されない可能性が高い
    results_high = compute_pairwise_similarity(paths, threshold=0.90)

    assert len(results_low) >= len(results_high)


def test_compute_pairwise_similarity_sklearn_not_installed(tmp_path):
    """sklearn 未インストールで空リスト。"""
    file_a = tmp_path / "skill_a.md"
    file_a.write_text("Content A for testing purposes.")
    file_b = tmp_path / "skill_b.md"
    file_b.write_text("Content B for testing purposes.")

    paths = {
        "skill-a": str(file_a),
        "skill-b": str(file_b),
    }

    with patch("lib.similarity.build_tfidf_matrix", return_value=(None, None, None)):
        results = compute_pairwise_similarity(paths, threshold=0.50)

    assert results == []


# --- filter_merge_group_pairs ---


class TestFilterMergeGroupPairs:
    """filter_merge_group_pairs のユニットテスト。"""

    def test_high_similarity_pair_passes(self, tmp_path):
        """類似度が閾値以上のペアがフィルタを通過する。"""
        pytest.importorskip("sklearn")
        pytest.importorskip("scipy")

        # 非常に類似したコンテンツ
        (tmp_path / "skill-a").mkdir()
        file_a = tmp_path / "skill-a" / "SKILL.md"
        file_a.write_text(
            "Deploy application to AWS using CloudFormation templates. "
            "AWS deployment configuration and infrastructure setup. "
            "CloudFormation stack management and deployment pipeline."
        )
        (tmp_path / "skill-b").mkdir()
        file_b = tmp_path / "skill-b" / "SKILL.md"
        file_b.write_text(
            "AWS infrastructure deployment and CloudFormation templates. "
            "Deploy and manage AWS cloud infrastructure resources. "
            "CloudFormation deployment automation and stack updates."
        )

        skill_path_map = {
            "skill-a": str(file_a),
            "skill-b": str(file_b),
        }
        result = filter_merge_group_pairs(
            ["skill-a", "skill-b"], skill_path_map, threshold=0.30
        )
        assert len(result) == 1
        assert frozenset(["skill-a", "skill-b"]) in result

    def test_low_similarity_pair_filtered(self, tmp_path):
        """類似度が閾値未満のペアが除外される。"""
        pytest.importorskip("sklearn")
        pytest.importorskip("scipy")

        (tmp_path / "skill-x").mkdir()
        file_x = tmp_path / "skill-x" / "SKILL.md"
        file_x.write_text(
            "Python testing framework pytest unittest mock assertion. "
            "Test coverage analysis and continuous integration pipeline."
        )
        (tmp_path / "skill-y").mkdir()
        file_y = tmp_path / "skill-y" / "SKILL.md"
        file_y.write_text(
            "Coral reef ecosystems and marine biodiversity conservation. "
            "Ocean currents and deep sea organism adaptation patterns."
        )

        skill_path_map = {
            "skill-x": str(file_x),
            "skill-y": str(file_y),
        }
        result = filter_merge_group_pairs(
            ["skill-x", "skill-y"], skill_path_map, threshold=0.60
        )
        assert len(result) == 0

    def test_large_cluster_filters_false_positives(self, tmp_path):
        """大規模クラスタで偽陽性が削減される。"""
        pytest.importorskip("sklearn")
        pytest.importorskip("scipy")

        # 7つのスキルを作成: skill-a/b は類似、残りは無関係
        contents = {
            "skill-a": "AWS CloudFormation deployment infrastructure templates stack management pipeline.",
            "skill-b": "AWS CloudFormation deployment infrastructure resources automation stack updates.",
            "skill-c": "Python machine learning neural network training model optimization.",
            "skill-d": "React frontend component rendering virtual DOM state management.",
            "skill-e": "PostgreSQL database schema migration query optimization indexing.",
            "skill-f": "Docker container orchestration Kubernetes pod deployment scaling.",
            "skill-g": "GraphQL API schema resolver subscription real-time data fetching.",
        }
        skill_path_map = {}
        for name, text in contents.items():
            (tmp_path / name).mkdir()
            path = tmp_path / name / "SKILL.md"
            path.write_text(text)
            skill_path_map[name] = str(path)

        skills = list(contents.keys())
        result = filter_merge_group_pairs(skills, skill_path_map, threshold=0.60)

        # C(7,2)=21 ペアのうち、閾値通過は少数（a-b ペア程度）
        assert len(result) < 21
        # skill-a と skill-b のペアは通過するはず（類似度が高い）
        # ただし TF-IDF のため確実ではないので、少なくとも大幅削減を確認
        assert len(result) <= 5

    def test_single_skill_returns_empty(self):
        """スキルが1つの場合は空リストを返す。"""
        result = filter_merge_group_pairs(["only-one"], {"only-one": "/path"})
        assert result == []

    def test_sklearn_not_installed_returns_all_pairs(self, tmp_path):
        """sklearn 未インストール時は全ペアを返す（graceful degradation）。"""
        (tmp_path / "a").mkdir()
        file_a = tmp_path / "a" / "SKILL.md"
        file_a.write_text("Content A")
        (tmp_path / "b").mkdir()
        file_b = tmp_path / "b" / "SKILL.md"
        file_b.write_text("Content B")

        skill_path_map = {"a": str(file_a), "b": str(file_b)}

        with patch("lib.similarity.build_tfidf_matrix", return_value=(None, None, None)):
            result = filter_merge_group_pairs(["a", "b"], skill_path_map, threshold=0.60)

        assert len(result) == 1
        assert frozenset(["a", "b"]) in result


# --- tokenize ---


class TestTokenize:
    """tokenize のユニットテスト。"""

    def test_basic_tokenization(self):
        """基本的なトークン化: 空白・句読点で分割、小文字化。"""
        result = tokenize("Hello World, foo-bar_baz")
        assert "hello" in result
        assert "world" in result
        assert "foo" in result
        assert "bar" in result
        assert "baz" in result

    def test_empty_string(self):
        """空文字列は空集合を返す。"""
        assert tokenize("") == set()

    def test_duplicate_words(self):
        """重複ワードは集合なので1つになる。"""
        result = tokenize("test test test")
        assert result == {"test"}


# --- jaccard_coefficient ---


class TestJaccardCoefficient:
    """jaccard_coefficient のユニットテスト。"""

    def test_exact_match(self):
        """完全一致で 1.0 を返す。"""
        assert jaccard_coefficient({"a", "b"}, {"a", "b"}) == 1.0

    def test_partial_overlap(self):
        """部分一致: {a,b} vs {b,c} => 1/3。"""
        score = jaccard_coefficient({"a", "b"}, {"b", "c"})
        assert abs(score - 1 / 3) < 1e-9

    def test_no_overlap(self):
        """重複なしで 0.0 を返す。"""
        assert jaccard_coefficient({"a"}, {"b"}) == 0.0

    def test_both_empty(self):
        """両方空で 0.0 を返す。"""
        assert jaccard_coefficient(set(), set()) == 0.0

    def test_one_empty(self):
        """片方空で 0.0 を返す。"""
        assert jaccard_coefficient({"a"}, set()) == 0.0
