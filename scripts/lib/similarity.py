#!/usr/bin/env python3
"""共通類似度エンジン。

TF-IDF ベクトル化とコサイン類似度計算を提供する。
reorganize / audit / prune / enrich など複数モジュールから利用される Single Source of Truth。
"""
import re
import sys
from itertools import combinations
from typing import Dict, FrozenSet, List, Set


def build_tfidf_matrix(skill_texts: dict) -> tuple:
    """スキルテキストから TF-IDF 行列を構築する。

    Args:
        skill_texts: {skill_name: text_content} の辞書

    Returns:
        (tfidf_matrix, feature_names, skill_names) のタプル。
        sklearn がインストールされていない場合は (None, None, None)。
    """
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
    except ImportError:
        return None, None, None

    skill_names = list(skill_texts.keys())
    texts = [skill_texts[name] for name in skill_names]

    vectorizer = TfidfVectorizer(max_features=200, stop_words='english')
    matrix = vectorizer.fit_transform(texts)
    return matrix, vectorizer.get_feature_names_out(), skill_names


def cosine_similarity_safe(vec_a, vec_b) -> float:
    """ゼロノルムガード付き cosine 類似度を計算する（#340）。

    scipy.spatial.distance.cosine は ``uu == 0`` または ``vv == 0``（ゼロノルム
    ベクトル）で ``1.0 - uv / sqrt(uu*vv)`` の 0 除算により NaN + RuntimeWarning
    を出す。退化スキル（stop word のみ等で TF-IDF が全ゼロになる文書）が混入すると
    類似度行列が NaN に汚染され、クラスタリング結果が歪む。

    どちらかがゼロノルムなら「共有する情報なし」とみなし類似度 0.0
    （= cosine 距離 1.0、最大距離）にフォールバックする。
    ``warnings.filterwarnings`` で握り潰さず、根本原因（ゼロベクトル）を計算前に
    分岐して除去するため決定論的。

    Args:
        vec_a, vec_b: 1次元の numpy 配列（TF-IDF 行ベクトル）。

    Returns:
        cosine 類似度（0.0〜1.0）。ゼロノルム入力時は 0.0。
    """
    import numpy as np

    a = np.asarray(vec_a, dtype=float)
    b = np.asarray(vec_b, dtype=float)
    norm_a = float(np.dot(a, a))
    norm_b = float(np.dot(b, b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(a, b) / np.sqrt(norm_a * norm_b))


def compute_pairwise_similarity(
    paths: Dict[str, str], threshold: float = 0.80
) -> List[Dict[str, object]]:
    """ファイル群のペアワイズ コサイン類似度を計算し、閾値以上のペアを返す。

    Args:
        paths: {skill_name: file_path} の辞書
        threshold: 類似度の閾値（デフォルト 0.80）

    Returns:
        [{"path_a": str, "path_b": str, "similarity": float}, ...]
        sklearn 未インストール時は空リスト。
    """
    if len(paths) < 2:
        return []

    # ファイル読み込み
    skill_texts: dict = {}
    path_map: dict = {}  # skill_name -> file_path
    for name, file_path in paths.items():
        try:
            with open(file_path, encoding="utf-8") as f:
                skill_texts[name] = f.read()
            path_map[name] = file_path
        except OSError:
            print(f"Warning: failed to read {file_path}, skipping", file=sys.stderr)

    if len(skill_texts) < 2:
        return []

    # TF-IDF 行列を構築
    matrix, _feature_names, skill_names = build_tfidf_matrix(skill_texts)
    if matrix is None:
        return []

    # ペアワイズ コサイン類似度を計算（ゼロノルムガード付き、#340）
    results: List[Dict[str, object]] = []
    n = len(skill_names)
    for i in range(n):
        for j in range(i + 1, n):
            vec_a = matrix[i].toarray().flatten()
            vec_b = matrix[j].toarray().flatten()
            similarity = cosine_similarity_safe(vec_a, vec_b)
            if similarity >= threshold:
                results.append({
                    "path_a": path_map[skill_names[i]],
                    "path_b": path_map[skill_names[j]],
                    "similarity": round(similarity, 4),
                })

    return results


def filter_merge_group_pairs(
    skills: List[str],
    skill_path_map: Dict[str, str],
    threshold: float = 0.60,
    interactive_threshold: float = 0.40,
) -> tuple:
    """merge_group のスキルリストからペア単位の類似度フィルタを適用する。

    Args:
        skills: merge_group 内のスキル名リスト
        skill_path_map: {skill_name: file_path} のマッピング
        threshold: コサイン類似度の閾値（デフォルト 0.60）
        interactive_threshold: interactive candidate の下限閾値（デフォルト 0.40）

    Returns:
        (passed, interactive) のタプル。
        passed: 閾値以上のペアの frozenset リスト。
        interactive: interactive 閾値以上かつ merge 閾値未満のペアと類似度スコアのタプルリスト。
        sklearn 未インストール時は passed に全ペア、interactive は空リストを返す（graceful degradation）。
    """
    if len(skills) < 2:
        return [], []

    # パスが存在するスキルのみ対象
    paths = {s: skill_path_map[s] for s in skills if s in skill_path_map}
    if len(paths) < 2:
        return [frozenset(pair) for pair in combinations(skills, 2)], []

    # TF-IDF 行列を構築
    skill_texts: dict = {}
    for name, file_path in paths.items():
        try:
            with open(file_path, encoding="utf-8") as f:
                skill_texts[name] = f.read()
        except OSError:
            print(f"Warning: failed to read {file_path}, skipping", file=sys.stderr)

    if len(skill_texts) < 2:
        return [frozenset(pair) for pair in combinations(skills, 2)], []

    matrix, _feature_names, skill_names = build_tfidf_matrix(skill_texts)
    if matrix is None:
        # sklearn 未インストール: graceful degradation — 全ペアを返す、interactive は空
        return [frozenset(pair) for pair in combinations(skills, 2)], []

    # ペアワイズ類似度を計算し、閾値以上と interactive 範囲を分類
    # （ゼロノルムガード付き、#340）
    passed: List[FrozenSet[str]] = []
    interactive: list = []
    n = len(skill_names)
    for i in range(n):
        for j in range(i + 1, n):
            vec_a = matrix[i].toarray().flatten()
            vec_b = matrix[j].toarray().flatten()
            similarity = cosine_similarity_safe(vec_a, vec_b)
            pair = frozenset([skill_names[i], skill_names[j]])
            if similarity >= threshold:
                passed.append(pair)
            elif similarity >= interactive_threshold:
                interactive.append((pair, round(similarity, 4)))

    return passed, interactive


# --- Jaccard 類似度 ---


def tokenize(text: str) -> Set[str]:
    """テキストを空白・句読点で分割し、小文字トークンの集合を返す。"""
    return set(re.split(r"[\s\W_]+", text.lower())) - {""}


def jaccard_coefficient(set_a: Set[str], set_b: Set[str]) -> float:
    """Jaccard 類似度係数を計算する。

    両方が空集合の場合は 0.0 を返す。
    """
    if not set_a and not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)
