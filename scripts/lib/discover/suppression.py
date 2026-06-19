"""discover の抑制リスト / JSONL ローダ / バリデータ / トークン抽出ヘルパ。

discover/__init__.py から re-export される（後方互換）。
SUPPRESSION_FILE は DATA_DIR を遅延参照（テスト patch 追従）。
"""
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from line_limit import MAX_RULE_LINES, MAX_SKILL_LINES
from similarity import tokenize

# #26: recommended_artifact を「導入しない」と判断したときのクールダウン日数。
# この日数を過ぎたら 1 回だけ再提示する（triage_ledger の TTL 方針を踏襲）。
# 「ずっと黙る」と環境変化で本当に必要になった artifact を取りこぼすため再評価窓を設ける。
ARTIFACT_SUPPRESSION_TTL_DAYS = 45
_DAY_SECONDS = 86400.0


def load_jsonl(filepath: Path) -> List[Dict[str, Any]]:
    """JSONL ファイルを読み込む。"""
    if not filepath.exists():
        return []
    records = []
    for line in filepath.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _suppression_file() -> Path:
    """SUPPRESSION_FILE を package attribute 経由で遅延参照する。

    既存テストは `mock.patch.object(discover, "SUPPRESSION_FILE", ...)` で
    パッケージ属性そのものを差し替える。Bare import では import-time に値が
    固定されてしまうため、毎回パッケージモジュールから取り出す。
    """
    from . import SUPPRESSION_FILE as _f  # noqa: PLC0415
    return _f


def load_suppression_list() -> set:
    """抑制リスト（2回 reject されたパターン）を読み込む。

    type: "merge" エントリは除外し、type 未指定エントリのみを返す。
    """
    records = load_jsonl(_suppression_file())
    return set(r.get("pattern", "") for r in records if r.get("type") != "merge")


def load_merge_suppression() -> set:
    """merge suppression リスト（type: "merge" エントリ）を読み込み、ペアキーの set を返す。"""
    records = load_jsonl(_suppression_file())
    return set(r.get("pattern", "") for r in records if r.get("type") == "merge")


def add_merge_suppression(skill_a: str, skill_b: str) -> None:
    """merge suppression エントリを追加する。スキル名をソートし :: 結合で正規化。

    書き込み失敗時は stderr にエラー出力し、例外を送出しない。
    """
    from . import DATA_DIR
    key = "::".join(sorted([skill_a, skill_b]))
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(_suppression_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps({"pattern": key, "type": "merge"}, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[evolve-anything] merge suppression write failed: {e}", file=sys.stderr)


def add_to_suppression_list(pattern: str) -> None:
    """抑制リストにパターンを追加する。"""
    from . import DATA_DIR
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_suppression_file(), "a", encoding="utf-8") as f:
        f.write(json.dumps({"pattern": pattern}, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# #26: recommended_artifact の「導入しない」判断を記憶し再提示を抑制する。
#
# detect_recommended_artifacts はディスク上の存在チェックだけで「未導入」を毎回
# 全件再提示していた。ユーザーが導入を見送った artifact も再三提案され、本当に
# 必要な提案の signal を薄めていた（#26）。merge suppression（type:"merge"）と
# triage_ledger の TTL 方針を踏襲し、type:"artifact" エントリに decided_at を記録、
# TTL 窓内は畳む。RECOMMENDED_ARTIFACTS は ~/.claude 配下を見る home-global な
# カタログのため、suppression も slug 非依存のグローバルストアに置く。
# ---------------------------------------------------------------------------

def add_artifact_suppression(
    artifact_id: str, *, now: Optional[float] = None,
) -> None:
    """artifact 導入見送りを記録する（type:"artifact" + decided_at）。

    書き込み失敗時は stderr に出力し例外を送出しない（merge suppression と同方針）。
    """
    from . import DATA_DIR
    decided_at = time.time() if now is None else now
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(_suppression_file(), "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "pattern": artifact_id,
                        "type": "artifact",
                        "decided_at": decided_at,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError as e:
        print(
            f"[evolve-anything] artifact suppression write failed: {e}",
            file=sys.stderr,
        )


def _artifact_suppression_records() -> Dict[str, float]:
    """artifact suppression を {artifact_id: latest_decided_at} で返す。

    同一 id が複数回見送られた場合は最新（最大）の decided_at を採用する
    （最後の判断から TTL を測るため）。decided_at 欠落は 0.0 扱い。
    """
    latest: Dict[str, float] = {}
    for r in load_jsonl(_suppression_file()):
        if r.get("type") != "artifact":
            continue
        aid = r.get("pattern", "")
        if not aid:
            continue
        decided_at = float(r.get("decided_at", 0.0) or 0.0)
        if aid not in latest or decided_at > latest[aid]:
            latest[aid] = decided_at
    return latest


def load_artifact_suppression() -> set:
    """現在抑制中（TTL 窓内）の artifact id の set を返す。"""
    return {
        aid
        for aid in _artifact_suppression_records()
        if is_artifact_suppressed(aid)
    }


def is_artifact_suppressed(
    artifact_id: str,
    *,
    now: Optional[float] = None,
    ttl_days: int = ARTIFACT_SUPPRESSION_TTL_DAYS,
) -> bool:
    """artifact が見送り済みかつ TTL 窓内なら True（＝再提示しない）。

    記録が無い / TTL を過ぎた場合は False（＝1回再提示して再評価を促す）。
    """
    records = _artifact_suppression_records()
    decided_at = records.get(artifact_id)
    if decided_at is None:
        return False
    current = time.time() if now is None else now
    return current < decided_at + ttl_days * _DAY_SECONDS


def validate_skill_content(content: str) -> bool:
    """スキル候補の構造バリデーション（MUST 500行以下）。"""
    lines = content.count("\n") + 1
    return lines <= MAX_SKILL_LINES


def validate_rule_content(content: str) -> bool:
    """ルール候補の構造バリデーション（MUST 3行以内）。"""
    lines = content.count("\n") + 1
    return lines <= MAX_RULE_LINES


def load_claude_reflect_data() -> List[Dict[str, Any]]:
    """corrections.jsonl から pending の修正データのみ取り込む。未生成時はスキップ。

    reflect が処理するのは pending のみであるため、
    evolve の reflect_data_count と reflect の認識を一致させる。
    """
    from . import DATA_DIR
    corrections_file = DATA_DIR / "corrections.jsonl"

    if not corrections_file.exists():
        return []

    records = load_jsonl(corrections_file)
    return [r for r in records if r.get("reflect_status", "pending") == "pending"]


def _load_skill_tokens(skill_path: Path) -> Dict[str, Any]:
    """SKILL.md の先頭 50 行 + スキル名からトークン集合を生成する。"""
    from typing import Set as _Set

    tokens: _Set[str] = set()
    skill_name = skill_path.parent.name
    tokens |= tokenize(skill_name)

    try:
        lines = skill_path.read_text(encoding="utf-8").splitlines()[:50]
        for line in lines:
            tokens |= tokenize(line)
    except OSError:
        pass

    return {"path": skill_path, "name": skill_name, "tokens": tokens}


def _load_classify_usage_skill():
    """audit.py の _is_plugin_skill と classify_usage_skill を遅延インポートで取得する。

    Returns:
        _is_plugin_skill 関数（classify_usage_skill + _is_gstack_skill + _is_openspec_skill の併用）
    """
    import sys as _sys
    from plugin_root import PLUGIN_ROOT
    _audit_scripts = PLUGIN_ROOT / "skills" / "audit" / "scripts"
    if str(_audit_scripts) not in _sys.path:
        _sys.path.insert(0, str(_audit_scripts))
    from audit import _is_plugin_skill, classify_usage_skill
    return _is_plugin_skill, classify_usage_skill
