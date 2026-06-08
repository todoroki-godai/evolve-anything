"""known_fp_patterns — 既知の偽陽性（FP）論理パス／識別子カタログ（#341, #337/#339）。

remediation の `FP_EXCLUSIONS`（`_should_exclude_fp`）を通り抜けて
`auto_fixable`（confidence>=0.9）に landing してしまう「いかにも FP な」文字列を
**決定論で**照合する自己完結カタログ。self_analysis（evolve_introspect, #341）が
「高 confidence バケットに FP が入る」パターンを safety guard として検出するために
使う。さらに remediation 本体（#357）も `_should_exclude_fp` の最終段でこの matcher を
**相対 subject に限定して**参照し、FP を auto_fixable 手前で除外する（自己解析が
出していた「auto_fixable に FP landing」は 0 件へ収束し regression guard として残る）。
絶対パスは remediation 側の tmp_path / logical_path と #339 実 FS ルート除外が専管する
ため呼び出し側でスキップする（本カタログの ssm_style_path は /Users 等の実ルートも
拾うため）。scripts/lib に小さく独立させ両者から共通参照する。

設計原則:
  - LLM 非依存・決定論・副作用なし（純関数）。
  - 入力は文字列 / issue dict のみ。外部 IO を持たない。
  - 検出キー（pattern 名）は dedup_key 安定性のため固定集合（`KNOWN_FP_PATTERN_NAMES`）。

照合する FP パターン:
  ssm_style_path            SSM 風論理パス `/<service>/<param>...`（先頭 / + 拡張子なし）
  tmp_path                  `/tmp/...` `/var/tmp/...`（一時ファイル）
  archive_path              `.archive` / `_archived` / `/archive/` 配下（淘汰済み）
  extensionless_logical_path スラッシュ区切りだが拡張子も先頭 / もない論理識別子
  generic_abbreviation       英大文字のみ 2-5 文字の汎用略語（SSM/API/TODO 等）

注意: ssm_style_path / tmp_path は先頭スラッシュ起点で先に判定し、
extensionless_logical_path は「先頭スラッシュなし」のフォールバックに留める
（順序で相互排他にし、同じ文字列が複数キーに化けないようにする）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

# dedup_key 安定性のための固定集合（テストが集合一致を assert）。
KNOWN_FP_PATTERN_NAMES = frozenset({
    "ssm_style_path",
    "tmp_path",
    "archive_path",
    "extensionless_logical_path",
    "generic_abbreviation",
})

# `/tmp/...` `/var/tmp/...` 等の一時パス。
_TMP_RE = re.compile(r"^(?:/var)?/tmp(?:/|$)")

# `.archive` / `_archived` / `/archive/` を含む淘汰済みパス。
_ARCHIVE_RE = re.compile(r"(?:^|/)(?:\.archive|_archived|archive)(?:/|$)")

# 先頭スラッシュ + 拡張子なし = SSM 風論理パラメータパス（/myapp/db/password）。
_SSM_RE = re.compile(r"^/[A-Za-z0-9_][\w./-]*$")

# 英大文字のみ 2-5 文字の汎用略語（SSM / API / TODO / FIXME(6) は対象外）。
_ABBREV_RE = re.compile(r"^[A-Z]{2,5}$")

# 拡張子（末尾 .ext）。これがあれば実ファイル寄りと見なし論理パス判定から外す。
_HAS_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,8}$")


def match_known_fp(text: Optional[str]) -> Optional[str]:
    """text が既知 FP パターンに一致すれば pattern 名、しなければ None。

    純関数・決定論。複数該当時は「より具体的なもの優先」の固定順で 1 つ返す。
    """
    if not text or not isinstance(text, str):
        return None
    s = text.strip()
    if not s:
        return None

    # archive は深い階層でも拾うため最優先（/tmp/.archive 等の取りこぼし防止）。
    if _ARCHIVE_RE.search(s):
        return "archive_path"

    # 一時パス。
    if _TMP_RE.match(s):
        return "tmp_path"

    has_ext = bool(_HAS_EXTENSION_RE.search(s))

    # 先頭スラッシュ + 拡張子なし → SSM 風論理パラメータ。
    if s.startswith("/") and not has_ext and _SSM_RE.match(s):
        return "ssm_style_path"

    # 英大文字のみの汎用略語。
    if _ABBREV_RE.match(s):
        return "generic_abbreviation"

    # 先頭スラッシュなし・拡張子なし・スラッシュ区切り = 論理識別子。
    if (
        not s.startswith("/")
        and "/" in s
        and not has_ext
        and re.fullmatch(r"[\w./-]+", s) is not None
    ):
        return "extensionless_logical_path"

    return None


def _issue_candidate_strings(issue: Dict[str, Any]) -> list:
    """issue dict から FP 照合対象の文字列を取り出す（detail.path / matched 等）。"""
    out: list = []
    if not isinstance(issue, dict):
        return out
    detail = issue.get("detail")
    if isinstance(detail, dict):
        for key in ("path", "matched", "ref", "target", "name"):
            val = detail.get(key)
            if isinstance(val, str) and val:
                out.append(val)
    # top-level の path/target も保険で見る。
    for key in ("path", "target"):
        val = issue.get(key)
        if isinstance(val, str) and val:
            out.append(val)
    return out


def match_known_fp_in_issue(issue: Dict[str, Any]) -> Optional[str]:
    """remediation issue dict が既知 FP に一致すれば pattern 名、しなければ None。

    detail.path / detail.matched 等を順に照合し、最初に一致した pattern 名を返す。
    """
    for s in _issue_candidate_strings(issue):
        name = match_known_fp(s)
        if name:
            return name
    return None
