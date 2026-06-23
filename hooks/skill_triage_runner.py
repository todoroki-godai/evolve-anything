#!/usr/bin/env python3
"""skill_triage_runner.py — 非同期 skill triage を実行し結果をキャッシュに書き出す。

session_summary.py の Stop hook から subprocess.Popen で非同期起動される。
5秒制限の hook 外で動作するため時間制約なし。LLM 呼び出しは行わない（MUST NOT）。
"""
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

_HOOKS_DIR = Path(__file__).resolve().parent
_LIB_DIR = _HOOKS_DIR.parent / "scripts" / "lib"
sys.path.insert(0, str(_LIB_DIR))
sys.path.insert(0, str(_HOOKS_DIR))

_PLUGIN_DATA_ENV = os.environ.get("CLAUDE_PLUGIN_DATA", "")
# #45(b)/#364: marker-aware 解決。hook 文脈（CLAUDE_PLUGIN_DATA=plugins/data 配下）でも
# canonical に一元化 marker があれば canonical に redirect する。これで usage.jsonl を
# canonical の live データから読み、skill-triage-cache.json を reader（instructions_loaded=
# common.DATA_DIR = canonical）と同じ dir に書く（writer/reader split の解消）。
try:
    from rl_common import resolve_data_dir
    DATA_DIR = resolve_data_dir(_PLUGIN_DATA_ENV)
except Exception:
    # fail-silent subprocess: rl_common 解決に失敗しても従来の naive 解決で継続する
    DATA_DIR = Path(_PLUGIN_DATA_ENV) if _PLUGIN_DATA_ENV else Path.home() / ".claude" / "evolve-anything"
TRIAGE_CACHE_FILE = DATA_DIR / "skill-triage-cache.json"


def _load_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    records = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    except OSError:
        pass
    return records


def run() -> None:
    try:
        from skill_triage import triage_all_skills
        import session_store
    except ImportError:
        return

    # sessions は session_store 経由（DuckDB が SoR）。直近 2000 件に限定しメモリを抑制
    sessions = session_store.query(limit=2000)
    usage = _load_jsonl(DATA_DIR / "usage.jsonl")
    # missed_skills は discover の LLM 結果。非同期実行では LLM 不可なので空リスト
    missed_skills: list = []

    try:
        result = triage_all_skills(
            sessions=sessions,
            usage=usage,
            missed_skills=missed_skills,
        )
    except Exception as exc:
        # 非同期 subprocess で stdout/stderr=DEVNULL のため、
        # error.log に書き出してデバッグ可能にする（fail-silent 契約は維持）
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            error_log = DATA_DIR / "skill-triage-runner-error.log"
            with open(error_log, "a", encoding="utf-8") as f:
                ts = datetime.now(timezone.utc).isoformat()
                f.write(f"{ts} {exc}\n{traceback.format_exc()}\n")
        except OSError:
            pass
        return

    # UPDATE/CREATE の上位候補を confidence 降順で抽出
    candidates = []
    for action in ("UPDATE", "CREATE"):
        for item in result.get(action, []):
            skill = item.get("skill", "")
            if not skill:
                continue
            candidates.append({
                "action": action,
                "skill": skill,
                "confidence": item.get("confidence", 0.0),
                "reason": item.get("reason", ""),
            })

    candidates.sort(key=lambda x: x["confidence"], reverse=True)
    candidates = candidates[:5]

    if not candidates:
        return

    cache = {
        "candidates": candidates,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # アトミック書き込み: 複数インスタンス同時起動による JSON 破損を防ぐ
    tmp_file = DATA_DIR / "skill-triage-cache.json.tmp"
    tmp_file.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_file, TRIAGE_CACHE_FILE)


if __name__ == "__main__":
    run()
