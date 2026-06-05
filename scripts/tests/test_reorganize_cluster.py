#!/usr/bin/env python3
"""reorganize.cluster_skills のゼロノルムガード回帰テスト（#340）。

退化スキル（TF-IDF 全ゼロ行）が pdist(metric='cosine') に渡ると NaN が混入し、
linkage の結果が非決定的・不正になる。NaN を最大距離 1.0 にフォールバックし
警告も出さないことを assert する。
"""
import sys
import warnings
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "lib"))

from lib.reorganize import cluster_skills  # noqa: E402
from lib.similarity import build_tfidf_matrix  # noqa: E402


def _build_matrix_with_zero_row():
    """1行が全ゼロ（stop word のみ）になる TF-IDF 行列を構築する。"""
    skill_texts = {
        "degenerate": "the the and or but if then",  # stop_words で全除去 → ゼロ行
        "aws": "AWS CloudFormation deployment infrastructure templates stack pipeline",
        "python": "Python testing framework pytest unittest mock coverage assertion",
    }
    matrix, _features, names = build_tfidf_matrix(skill_texts)
    return matrix, names


def test_cluster_skills_zero_norm_no_nan_no_warning():
    """ゼロノルム行を含む行列で NaN/RuntimeWarning を出さずクラスタリングする。"""
    pytest.importorskip("sklearn")
    pytest.importorskip("scipy")
    import numpy as np

    matrix, names = _build_matrix_with_zero_row()
    assert matrix is not None
    # 退化行が実際に全ゼロであることを確認（前提が崩れたらテスト意義なし）
    norms = np.sqrt((matrix.toarray() ** 2).sum(axis=1))
    assert (norms == 0).any(), "前提: ゼロノルム行が存在すること"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        labels = cluster_skills(matrix, threshold=0.7)

    runtime_warnings = [w for w in caught if issubclass(w.category, RuntimeWarning)]
    assert runtime_warnings == [], (
        f"想定外の RuntimeWarning: {[str(w.message) for w in runtime_warnings]}"
    )
    assert len(labels) == len(names)
    # ラベルはすべて有限な整数
    assert all(isinstance(lbl, int) for lbl in labels)


def test_cluster_skills_deterministic_with_zero_norm():
    """ゼロノルム行があっても結果が決定論的（複数回実行で同一）。"""
    pytest.importorskip("sklearn")
    pytest.importorskip("scipy")

    matrix, _names = _build_matrix_with_zero_row()
    first = cluster_skills(matrix, threshold=0.7)
    second = cluster_skills(matrix, threshold=0.7)
    assert first == second


def test_cluster_skills_normal_unchanged():
    """非ゼロベクトルのみのクラスタリングは回帰しない（類似ペアが同一クラスタ）。"""
    pytest.importorskip("sklearn")
    pytest.importorskip("scipy")

    skill_texts = {
        "aws-a": "AWS CloudFormation deployment infrastructure templates stack management pipeline",
        "aws-b": "AWS CloudFormation deployment infrastructure resources automation stack updates",
        "python": "Python machine learning neural network training model optimization gradient",
    }
    matrix, _features, names = build_tfidf_matrix(skill_texts)
    labels = cluster_skills(matrix, threshold=0.7)
    label_map = dict(zip(names, labels))
    # 類似する aws-a / aws-b は同一クラスタ、python は別クラスタ
    assert label_map["aws-a"] == label_map["aws-b"]
    assert label_map["python"] != label_map["aws-a"]
