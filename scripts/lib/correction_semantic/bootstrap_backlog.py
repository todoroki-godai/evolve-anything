"""correction_semantic.bootstrap_backlog — 初回バックログ bootstrap モード（#443）。

既存の weak_signals バックログ（channel=llm_judge・未昇格）を初回 evolve 時にまとめて
確認する入口を提供する。設計（daily-evolve-reward-loop-design.md §機能#3 ハイブリッド方式）:

- アクティブ PJ のみ初回 bootstrap でまとめて確認（per-PJ 15-30 分）。残り PJ は日次 5 件 +
  TTL 45 日の自然失効に任せる。**bootstrap 対象 PJ の選択は実行時に AskUserQuestion で人間が
  選ぶ**（機械が「アクティブ」を判定しない）。本モジュールは決定論で「未消化 backlog の有無・
  PJ 別件数・group 化」を**常時 emit**し、判断材料を SKILL.md に渡すだけ。

build() は marker（bootstrap_done-<slug>.marker）未設定なら当該 PJ の未昇格 backlog を
内容キーワード jaccard≥0.5 で group 化して返す。marker が立っていたら is_bootstrap=False で
即返す（重い group 化をしない早期 return）。

実データ知見（設計 §機能#3）: idiom は正規化済み修正パターンでなく**生のユーザー発話断片**
（median 10 文字）なので文字列類似では圧縮がほとんど効かない。内容キーワード（漢字/カタカナ
2 字以上）jaccard≥0.5 が最も効くが圧縮率 15%（313→267・31 group が 77 件吸収）。

bootstrap は **現在 cwd の PJ slug の backlog のみ**を対象にする（DATA_DIR 全PJ共通 pitfall）。
marker は slug スコープ（bootstrap_done-<slug>.marker）+ writer_locus=batch で store_registry
宣言済み（#434）。dry-run ゼロ書込（pitfall_dryrun_stateful_store_write）: mark_done は
dry_run=True なら一切ファイルに触れない。build は読み取りのみ（marker を書かない）。

#442（weak_signals TTL）が並行実装中。expired 除外の reader API はまだ無い前提で、read 時に
レコードへ ``expired`` フィールドがあれば防御的に除外する（深い依存を作らない浅い連携）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from correction_semantic.idiom_filter import idiom_eligible
from correction_semantic.representative import user_only_text
from weak_signals.store import default_store_path, read_signals

MARKER_PREFIX = "bootstrap_done-"
MARKER_SUFFIX = ".marker"

# backlog の対象チャネル。#431 のバッチ LLM 意味判定レーン（313 件はこのチャネル）。
BACKLOG_CHANNEL = "llm_judge"

# 内容キーワードの jaccard 類似度しきい値（設計 §機能#3・実データで最も効いた値）。
JACCARD_THRESHOLD = 0.5

# 漢字 / カタカナ 2 字以上の連続を「内容キーワード」として抽出する。
# 助詞・1 字漢字・ひらがな語尾はノイズになるため拾わない（圧縮の効きを担保）。
_KEYWORD_RE = re.compile(r"[一-龥々]{2,}|[ァ-ヴー]{2,}")

# テーマクラスタ提示への切り替え閾値（#558）。
# 根拠: 初回 bootstrap で当 PJ 未昇格シグナルが多数（amamo PJ で 48 件 / 45 グループ）
# 出ると Step 6.1「各 group を AskUserQuestion で順に確認」が質問マラソンになり、
# explain-clearly（質問を畳む）ルールと衝突する。group 数が本閾値を超えたときだけ
# テーマ別バケットの multiSelect 1 問に畳む。閾値以下は従来 per-group フロー（挙動不変）。
# 12 は「per-group で出しても許容できる現実的上限（数問程度）」の経験則。
THEME_CLUSTER_THRESHOLD = 12

# テーマクラスタの TF-IDF コサイン距離しきい値（reorganize.cluster_skills と同流儀・
# これ以下の距離が同一クラスタ）。bootstrap の発話断片はスキル本文より短くテーマ間の
# 距離が開きやすいため、char n-gram（後述）で語彙を成立させたうえで初期距離をやや緩め
# の 0.90 で粗いテーマ束にする。バケット数が上限を超える場合は _MAX_THEME_BUCKETS の
# ガードが距離を段階的に上げて再クラスタする。
_CLUSTER_DISTANCE_THRESHOLD = 0.90

# テーマバケット数の上限ガード（#568）。
# 根拠: #558 の狙いは「45+ group の per-group AskUserQuestion 質問マラソンを 1 問の
# multiSelect に畳む」こと。だが word-level TF-IDF（build_tfidf_matrix）は日本語の
# 短い発話断片を共通語彙で束ねられず、実コーパス（figma-to-code 108 group）で
# 108→48 バケットにしか畳めなかった（root cause: 各発話が固有名詞中心で TF-IDF の
# 語彙が共有されない）。char n-gram で語彙を成立させ、さらにバケット数がこの上限を
# 超えるなら距離閾値を段階的に上げて再クラスタし、AskUserQuestion の 1 問で扱える
# 規模（実測 figma 108→10 / receipt 20→9 / atlas 23→6）に必ず収める。
MAX_THEME_BUCKETS = 10

# 上限ガードで距離を緩めるステップ幅と上限（決定論・有限回で必ず停止）。
_CLUSTER_DISTANCE_STEP = 0.02
_CLUSTER_DISTANCE_MAX = 0.999

# char n-gram の語数範囲（#568）。短い日本語断片は単語境界が曖昧なため、文字 bi/tri-gram
# で部分文字列の共有を捉える（"フッターを直して" と "余白を直して" が「を直して」で近づく）。
_CHAR_NGRAM_RANGE = (2, 3)

# char n-gram の語彙上限（語彙爆発を抑える・決定論には影響しない）。
_CHAR_MAX_FEATURES = 400


# ─────────────────────────────────────────────────────────────────
# keyword 抽出 / jaccard grouping（決定論・LLM 非依存）
# ─────────────────────────────────────────────────────────────────
def extract_keywords(text: str) -> Set[str]:
    """発話断片から内容キーワード（漢字/カタカナ 2 字以上）の集合を返す。"""
    if not text:
        return set()
    return set(_KEYWORD_RE.findall(text))


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _idiom_text(rec: Dict[str, Any]) -> str:
    """weak_signal レコードから idiom 本文（発話断片）を取り出す。

    #528-3: assistant の過去レポート引用混入（「ℹ️ データ蓄積待ち…」等）を strip し
    user 発話のみを representative にする。
    """
    prov = rec.get("provenance") or {}
    return user_only_text(prov.get("text") or "")


def group_signals(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """未昇格 backlog レコードを内容キーワード jaccard≥0.5 で group 化する。

    各 group は ``{"representative": str, "signal_keys": [...], "size": int,
    "confirmable_idiom": str | None}``。keyword が抽出できない断片は単独 group のまま
    （圧縮されない＝実データの現実的下限）。取りこぼし防止: 全レコードがいずれかの group の
    signal_keys に必ず 1 回入る。

    #527-4: confirmable_idiom = この group を「はい」確定すると confirmed になる idiom テキスト。
    representative が idiom_filter の floor/stopword/context token を通る場合のみその text、
    過汎用なら None（AskUserQuestion で「confirmed になる idiom 無し」と提示できる）。

    決定論: 入力順を保った逐次 single-pass グルーピング（同じ入力で同じ出力）。
    """
    groups: List[Dict[str, Any]] = []
    group_kws: List[Set[str]] = []
    for rec in records:
        text = _idiom_text(rec)
        kws = extract_keywords(text)
        key = rec.get("signal_key", "")
        placed = False
        if kws:
            for gi, gk in enumerate(group_kws):
                if gk and _jaccard(kws, gk) >= JACCARD_THRESHOLD:
                    groups[gi]["signal_keys"].append(key)
                    group_kws[gi] = gk | kws
                    placed = True
                    break
        if not placed:
            groups.append({
                "representative": text,
                "signal_keys": [key],
                # #527-4: 過汎用 representative は confirmed 化対象にしない（None で提示）。
                "confirmable_idiom": text if idiom_eligible(text) else None,
            })
            group_kws.append(kws)
    for g in groups:
        g["size"] = len(g["signal_keys"])
    return groups


# ─────────────────────────────────────────────────────────────────
# テーマクラスタリング（#558・決定論 TF-IDF・LLM 非依存）
# ─────────────────────────────────────────────────────────────────
# theme_label に併記する代表シグナル抜粋の文字数（#21）。
_THEME_EXCERPT_CHARS = 24


def _theme_label(tokens: List[str]) -> str:
    """クラスタの代表トークン列からテーマラベルを決定論で生成する。"""
    picked = [t for t in tokens if t][:3]
    return " / ".join(picked) if picked else "その他"


def _representative_excerpt(reps: List[str]) -> str:
    """バケット代表シグナルの冒頭抜粋を決定論で返す（#21・AskUserQuestion 用）。

    日本語シグナルでは char n-gram の centroid 上位が「、、/って/んだ」のような意味を
    なさない断片列になり theme_label 単独では選択の手がかりにならない。代表シグナル
    （呼び出し側が group_index 順に渡した先頭非空テキスト）の冒頭
    ``_THEME_EXCERPT_CHARS`` 文字を併記して人間が選べるラベルにする
    （issue #21 option b・外部形態素解析に依存しない）。

    決定論: 渡された reps の順序どおりに最初の非空テキストを採用する。
    """
    for rep in reps:
        text = (rep or "").strip()
        if text:
            head = text[:_THEME_EXCERPT_CHARS]
            return head + ("…" if len(text) > _THEME_EXCERPT_CHARS else "")
    return ""


def _theme_label_with_excerpt(tokens: List[str], reps: List[str]) -> str:
    """n-gram テーマラベルに代表シグナル抜粋を併記する（#21）。

    抜粋が取れなければ n-gram ラベル単独（後方互換）。抜粋があれば
    ``「<代表抜粋>」 (<n-gram label>)`` 形式で人間可読にする。代表抜粋は label の
    冒頭に置く（AskUserQuestion の選択肢でまず意味が読めるようにするため）。
    """
    excerpt = _representative_excerpt(reps)
    label = _theme_label(tokens)
    if not excerpt:
        return label
    return f"「{excerpt}」 ({label})"


def _build_char_tfidf(docs: List[str]):
    """発話断片群を char n-gram TF-IDF 行列に変換する（#568）。

    word-level の ``similarity.build_tfidf_matrix`` は日本語の短い発話断片を共通語彙で
    束ねられない（各発話が固有名詞中心で TF-IDF の語彙が共有されず、108 group が
    48 バケットにしか畳めなかった）。char n-gram（``analyzer='char_wb'`` +
    ``ngram_range``）なら部分文字列の共有（"を直して" 等の述部・共通語幹）を捉えられる。

    Returns: ``(matrix, feature_names)``。sklearn 未インストール時は ImportError を
    送出する（呼び出し側が graceful degradation する）。
    """
    from sklearn.feature_extraction.text import TfidfVectorizer

    vectorizer = TfidfVectorizer(
        analyzer="char_wb",
        ngram_range=_CHAR_NGRAM_RANGE,
        max_features=_CHAR_MAX_FEATURES,
    )
    matrix = vectorizer.fit_transform(docs)
    return matrix, vectorizer.get_feature_names_out()


def cluster_groups(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """bootstrap group 群をテーマ別バケットに決定論クラスタリングする（#558, #568）。

    各 group の representative（発話断片）を 1 文書とし、char n-gram TF-IDF
    （``_build_char_tfidf``）でベクトル化、``reorganize.cluster_skills`` の階層
    クラスタリングで束ねる。word-level TF-IDF は日本語の短文を共通語彙で束ねられず
    実コーパスで 108→48 にしか畳めなかったため、char n-gram に差し替えた（#568）。

    さらにバケット数が ``MAX_THEME_BUCKETS`` を超える場合は距離閾値を段階的に上げて
    再クラスタし、AskUserQuestion 1 問で扱える規模（実測 figma 108→10）に必ず収める。

    Returns: 各バケット = ``{"theme_label": str, "group_indices": [int...],
    "groups": [<group dict>...]}``。theme_label はクラスタ代表シグナルの冒頭抜粋 +
    centroid 上位文字 n-gram から決定論生成（#21: 日本語では n-gram 単独が無意味な
    断片列になるため代表抜粋を併記）。取りこぼし無し（全 group がいずれかのバケットに
    1 回入る）。

    sklearn/scipy 未インストール or 文書が少ない場合は単一バケットに全 group を入れて
    graceful degradation する（決定論・例外を投げない）。
    """
    if not groups:
        return []

    # 各 group の文書 = representative 全文（char n-gram は単語境界に依存しないため、
    # word-level の keyword 連結より生文の方が部分文字列の共有を多く拾える）。
    docs: List[str] = [(g.get("representative") or "") for g in groups]

    # 単一テーマに畳む graceful degradation（クラスタリング不能/不要時）。
    def _single_bucket() -> List[Dict[str, Any]]:
        # 最頻の内容キーワード上位を label に（決定論: 出現数降順 → トークン昇順）。
        from collections import Counter

        all_tokens: List[str] = []
        for d in docs:
            all_tokens.extend(sorted(extract_keywords(d)))
        counter = Counter(t for t in all_tokens if t)
        top = [t for t, _ in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]
        return [{
            # #21: n-gram ラベル単独でなく代表シグナル抜粋を併記して人間可読にする。
            "theme_label": _theme_label_with_excerpt(top, docs),
            "group_indices": list(range(len(groups))),
            "groups": list(groups),
        }]

    if len(groups) < 3:
        return _single_bucket()

    try:
        matrix, feature_names = _build_char_tfidf(docs)
    except Exception:
        return _single_bucket()
    if matrix is None or feature_names is None:
        return _single_bucket()

    try:
        from reorganize import cluster_skills
    except Exception:
        return _single_bucket()

    # 上限ガード（#568）: 距離閾値を上げながらバケット数が MAX_THEME_BUCKETS 以下に
    # 収まるまで再クラスタする。決定論（同入力で同じ閾値列・同じ labels）。距離が
    # _CLUSTER_DISTANCE_MAX に達したら、それ以上は束ねられないので打ち切る（有限回停止）。
    threshold = _CLUSTER_DISTANCE_THRESHOLD
    labels: List[int] = []
    while True:
        try:
            labels = cluster_skills(matrix, threshold=threshold)
        except Exception:
            return _single_bucket()
        n_buckets = len(set(labels))
        if n_buckets <= MAX_THEME_BUCKETS or threshold >= _CLUSTER_DISTANCE_MAX:
            break
        threshold = round(min(threshold + _CLUSTER_DISTANCE_STEP, _CLUSTER_DISTANCE_MAX), 4)
    if not labels:
        return _single_bucket()

    # label → group index 群へ集約する（labels の位置 = docs/groups の index）。
    cluster_map: Dict[int, List[int]] = {}
    for gi, label in enumerate(labels):
        cluster_map.setdefault(int(label), []).append(gi)

    # centroid 上位 n-gram で theme_label を決定論生成する。
    import numpy as np

    dense = matrix.toarray()
    buckets: List[Dict[str, Any]] = []
    # クラスタ順は「最小 group_index 昇順」で決定論的に並べる。
    ordered = sorted(cluster_map.values(), key=lambda inds: min(inds))
    for indices in ordered:
        indices = sorted(indices)
        centroid = np.mean(dense[indices], axis=0)
        top_feat_idx = list(np.argsort(centroid)[::-1])
        # char n-gram の label は空白・記号を含むためトリムして見やすくする。
        top_tokens = [
            feature_names[i].strip()
            for i in top_feat_idx
            if centroid[i] > 0 and feature_names[i].strip()
        ][:3]
        # #21: バケット代表シグナル（最小 group_index = 入力順先頭）の抜粋を併記する。
        bucket_reps = [docs[i] for i in indices]
        buckets.append({
            "theme_label": _theme_label_with_excerpt(list(top_tokens), bucket_reps),
            "group_indices": indices,
            "groups": [groups[i] for i in indices],
        })
    return buckets


# ─────────────────────────────────────────────────────────────────
# backlog 読み取り（slug スコープ + 未昇格 + 防御的 expired 除外）
# ─────────────────────────────────────────────────────────────────
def _read_backlog(
    pj_slug: str,
    weak_signals_path: Optional[Path],
) -> List[Dict[str, Any]]:
    """当該 PJ slug の未昇格 llm_judge backlog を返す（expired は防御的に除外）。"""
    recs = read_signals(weak_signals_path)
    out: List[Dict[str, Any]] = []
    for r in recs:
        if r.get("pj_slug") != pj_slug:
            continue
        if r.get("channel") != BACKLOG_CHANNEL:
            continue
        if r.get("promoted"):
            continue
        # #442 TTL 連携（浅い防御的読み）: expired フィールドがあれば除外する。
        if r.get("expired"):
            continue
        out.append(r)
    return out


# ─────────────────────────────────────────────────────────────────
# marker（bootstrap_done-<slug>.marker）
# ─────────────────────────────────────────────────────────────────
def default_marker_path(pj_slug: str, base: Optional[Path] = None) -> Path:
    """bootstrap_done-<slug>.marker の正準パス（ADR-042 resolver 経由 / base 優先）。"""
    name = f"{MARKER_PREFIX}{pj_slug}{MARKER_SUFFIX}"
    if base is not None:
        return Path(base) / name
    import os

    import rl_common  # 遅延 import（hook/tool 文脈の patch 追従）

    env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    data_dir = rl_common.resolve_data_dir(env)
    return Path(data_dir) / name


def is_done(pj_slug: str, marker_path: Optional[Path] = None) -> bool:
    """当該 PJ の bootstrap が既に完了（marker 設定済み）かを返す。"""
    marker = marker_path if marker_path is not None else default_marker_path(pj_slug)
    return marker.exists()


def mark_done(
    pj_slug: str,
    marker_path: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """bootstrap 完了 marker を立てる（dry-run ゼロ書込ゲートを最下層まで貫通）。

    「まとめて確認」完了時・「TTL 失効に任せる」選択時のどちらでも呼ばれる。
    Returns: {"written": bool, "dry_run": bool, "path": str}
    """
    marker = marker_path if marker_path is not None else default_marker_path(pj_slug)
    if dry_run:
        return {"written": False, "dry_run": True, "path": str(marker)}
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("", encoding="utf-8")
    return {"written": True, "dry_run": False, "path": str(marker)}


# ─────────────────────────────────────────────────────────────────
# build: phase の本体（常時 emit）
# ─────────────────────────────────────────────────────────────────
def build(
    pj_slug: str,
    *,
    weak_signals_path: Optional[Path] = None,
    marker_path: Optional[Path] = None,
    idioms_path: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """初回（marker 未設定）のみ、当該 PJ の未昇格 backlog を group 化して返す。

    Returns:
      {"is_bootstrap": bool,   # marker 未設定なら True
       "pj_total": int,        # 当該 PJ の未昇格 backlog 件数
       "groups_total": int,    # 内容キーワード jaccard≥0.5 圧縮後の group 数
       "groups": [...],        # bootstrap 選択時に使う全 group（代表 idiom + signal_keys +
                               #   cross_pj_confirmed: [<他slug>, ...]・#462）
       "theme_buckets": [...] | None,  # #558: group 数が THEME_CLUSTER_THRESHOLD 超の
                               #   ときだけ TF-IDF テーマ別バケット
                               #   [{theme_label, group_indices, groups}]。閾値以下は None
                               #   （従来 per-group フロー・挙動不変）
       "slug": str,
       "dry_run": bool}

    marker（bootstrap_done-<slug>.marker）が立っていたら is_bootstrap=False で即返す
    （重い group 化をしない早期 return）。build 自体は marker を書かない（読み取りのみ）。

    他 PJ で confirmed 済みの idiom と正規化テキスト一致する group は先頭へ優先表示し、
    cross_pj_confirmed ラベルを付与する（#462）。idioms_path は read 専用（書込なし）。
    """
    marker = marker_path if marker_path is not None else default_marker_path(pj_slug)

    if marker.exists():
        # 早期 return: 既消化。pj_total/groups は計算しない（重い group 化を回避）。
        return {
            "is_bootstrap": False,
            "pj_total": 0,
            "groups_total": 0,
            "groups": [],
            "theme_buckets": None,
            "slug": pj_slug,
            "dry_run": dry_run,
        }

    backlog = _read_backlog(pj_slug, weak_signals_path)
    groups = group_signals(backlog)
    # 他 PJ confirmed 一致 group を先頭へ + cross_pj_confirmed 付与（#462・read 専用）。
    from correction_semantic.cross_pj_priority import prioritize as _prioritize

    groups = _prioritize(groups, pj_slug, idioms_path=idioms_path)
    # #558: group 数が閾値超のときだけテーマ別バケットを emit（質問マラソン回避）。
    # 閾値以下は theme_buckets=None で従来 per-group フローのまま（挙動不変）。
    theme_buckets = (
        cluster_groups(groups) if len(groups) >= THEME_CLUSTER_THRESHOLD else None
    )
    return {
        "is_bootstrap": True,
        "pj_total": len(backlog),
        "groups_total": len(groups),
        "groups": groups,
        "theme_buckets": theme_buckets,
        "slug": pj_slug,
        "dry_run": dry_run,
    }
