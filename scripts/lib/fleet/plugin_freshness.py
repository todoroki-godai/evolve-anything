"""Claude Code プラグインの freshness（最新性）を決定論で診断する。

version フィールドを持たないプラグイン（例: skill-creator）は `claude plugin update`
がバージョン比較できず「最新」と誤判定して cache を同期しない問題がある。本モジュールは
正本 3 点を突き合わせてその種の stale を検出する:

1. `installed_plugins.json`            … インストール中バージョン + installPath（cache 実体）
2. `marketplaces/<mp>/.../marketplace.json` … 最新バージョン + source 相対パス
3. cache の installPath ツリー vs marketplace source ツリーのコンテンツ差分

判定:
- ok      : 最新版と一致 + cache コンテンツも source と一致
- update  : marketplace に新しい semver がある（インストール版が古い）
- drift   : 同一/比較不能バージョンだが cache コンテンツが source と乖離（要再インストール）
- unknown : marketplace に該当が無く比較不能（Directory marketplace の自前 PJ 等）

LLM 非依存・ファイルシステムのみ。`bin/evolve-fleet plugins` から利用される。
"""
from __future__ import annotations

import filecmp
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# diff 比較から除外するノイズ（実行時生成物・利用マーカー）
_IGNORE_NAMES = {".in_use", "__pycache__", ".DS_Store", ".git"}

_SEMVER_RE = re.compile(r"^\d+(\.\d+)*$")


@dataclass
class PluginFreshness:
    """1 プラグインの最新性診断結果。"""

    name: str  # "<plugin>@<marketplace>"
    marketplace: str
    installed_version: str
    latest_version: str | None
    status: str  # ok | update | drift | unknown
    detail: str = ""


def _default_plugins_root() -> Path:
    return Path.home() / ".claude" / "plugins"


def _git_head_sha(repo_dir: Path) -> str | None:
    """git marketplace の現在の HEAD sha を返す。.git 無し・git 不在・失敗時は None。

    self-dir（source: './'）の git marketplace では content-diff がリポ root と
    パッケージ済み cache を比較してしまい偽 drift を出すため、git-sha 管理の
    プラグインは HEAD sha 比較を正準シグナルとして使う。
    """
    if not (repo_dir / ".git").exists():
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip()
    return sha or None


def _parse_version_tuple(v: str) -> tuple[int, ...] | None:
    """semver 風の文字列を比較用タプルに。比較不能（'unknown'/git sha 等）は None。"""
    if not v or not _SEMVER_RE.match(v):
        return None
    return tuple(int(x) for x in v.split("."))


def _dirs_differ(a: Path, b: Path) -> bool:
    """2 ディレクトリのコンテンツが異なれば True（ノイズは無視、再帰比較）。"""
    if not a.is_dir() or not b.is_dir():
        return True
    cmp = filecmp.dircmp(a, b, ignore=list(_IGNORE_NAMES))
    if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
        return True
    for sub in cmp.common_dirs:
        if _dirs_differ(a / sub, b / sub):
            return True
    return False


def _load_marketplace_index(plugins_root: Path) -> dict[str, dict[str, dict]]:
    """{marketplace_name: {plugin_name: {"version":..., "source_dir": Path}}} を構築。"""
    index: dict[str, dict[str, dict]] = {}
    mp_root = plugins_root / "marketplaces"
    if not mp_root.is_dir():
        return index
    for mp_dir in sorted(p for p in mp_root.iterdir() if p.is_dir()):
        manifest = mp_dir / ".claude-plugin" / "marketplace.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        plugins = data.get("plugins") if isinstance(data, dict) else data
        if not isinstance(plugins, list):
            continue
        head_sha = _git_head_sha(mp_dir)  # git marketplace のみ非 None（1 mp 1 回）
        entry: dict[str, dict] = {}
        for p in plugins:
            if not isinstance(p, dict):
                continue
            pname = p.get("name")
            if not pname:
                continue
            source = p.get("source")
            source_dir: Path | None = None
            if isinstance(source, str):
                source_dir = (mp_dir / source).resolve()
            entry[pname] = {
                "version": p.get("version"),
                "source_dir": source_dir,
                "head_sha": head_sha,
            }
        index[mp_dir.name] = entry
    return index


def _split_key(key: str) -> tuple[str, str]:
    """'plugin@marketplace' を (plugin, marketplace) に分割。"""
    if "@" in key:
        plug, mp = key.rsplit("@", 1)
        return plug, mp
    return key, ""


def check_plugin_freshness(plugins_root: Path | None = None) -> list[PluginFreshness]:
    """インストール済みプラグインの最新性を診断して結果リストを返す。"""
    root = plugins_root or _default_plugins_root()
    installed_path = root / "installed_plugins.json"
    if not installed_path.is_file():
        return []
    try:
        installed = json.loads(installed_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    plugins = installed.get("plugins", {}) if isinstance(installed, dict) else {}
    mp_index = _load_marketplace_index(root)

    rows: list[PluginFreshness] = []
    for key, installs in sorted(plugins.items()):
        if not isinstance(installs, list) or not installs:
            continue
        plug_name, mp_name = _split_key(key)
        # 同一 key に複数 scope がある場合は user scope を優先、無ければ先頭
        info = next((i for i in installs if i.get("scope") == "user"), installs[0])
        inst_ver = str(info.get("version", "") or "")
        inst_sha = info.get("gitCommitSha") or None
        install_path = Path(info["installPath"]) if info.get("installPath") else None

        mp_entry = mp_index.get(mp_name, {}).get(plug_name)
        if mp_entry is None:
            rows.append(PluginFreshness(
                name=key, marketplace=mp_name, installed_version=inst_ver,
                latest_version=None, status="unknown",
                detail="marketplace に該当プラグインなし（Directory marketplace 等）",
            ))
            continue

        latest_ver = mp_entry.get("version")
        latest_ver_s = str(latest_ver) if latest_ver is not None else None
        source_dir = mp_entry.get("source_dir")

        # 1) semver 比較が可能なら版ずれを最優先で検出
        iv = _parse_version_tuple(inst_ver)
        lv = _parse_version_tuple(latest_ver_s or "")
        if iv is not None and lv is not None and lv > iv:
            rows.append(PluginFreshness(
                name=key, marketplace=mp_name, installed_version=inst_ver,
                latest_version=latest_ver_s, status="update",
                detail=f"{inst_ver} → {latest_ver_s} が利用可能",
            ))
            continue

        # 1.5) git-sha 管理プラグイン（semver 比較不能）は HEAD sha 比較を優先。
        #      self-dir git marketplace の content-diff スコープ不一致 FP を回避する。
        head_sha = mp_entry.get("head_sha")
        if iv is None and lv is None and head_sha and inst_sha:
            git_status = "ok" if head_sha.startswith(inst_sha[:12]) else "update"
            rows.append(PluginFreshness(
                name=key, marketplace=mp_name, installed_version=inst_ver,
                latest_version=latest_ver_s,
                status=git_status,
                detail=("git marketplace に新しい commit あり（再インストール推奨）"
                        if git_status == "update" else ""),
            ))
            continue

        version_comparable = iv is not None and lv is not None
        content_comparable = (
            install_path is not None
            and source_dir is not None
            and source_dir.is_dir()
        )

        # 2) コンテンツ差分で drift を検出（version 比較不能 or 同版での乖離）
        if content_comparable:
            if _dirs_differ(source_dir, install_path):
                rows.append(PluginFreshness(
                    name=key, marketplace=mp_name, installed_version=inst_ver,
                    latest_version=latest_ver_s, status="drift",
                    detail="cache コンテンツが marketplace source と乖離（再インストール推奨）",
                ))
                continue

        # 3) いずれの検証も行えなかった場合は ok と誤認せず unknown を返す
        #    （silence≠verified: 外部 git source + version 無しのプラグイン等）
        if not version_comparable and not content_comparable:
            rows.append(PluginFreshness(
                name=key, marketplace=mp_name, installed_version=inst_ver,
                latest_version=latest_ver_s, status="unknown",
                detail="version 比較不能 + source 取得不能（外部 git source 等）で検証できず",
            ))
            continue

        # 4) いずれかの検証を通過 → 最新
        rows.append(PluginFreshness(
            name=key, marketplace=mp_name, installed_version=inst_ver,
            latest_version=latest_ver_s, status="ok",
        ))
    return rows


_STATUS_MARK = {"ok": "✔", "update": "⬆", "drift": "✘", "unknown": "?"}


def format_plugin_freshness_table(rows: list[PluginFreshness], as_json: bool = False) -> str:
    """診断結果を表（または JSON）に整形する。"""
    if as_json:
        return json.dumps([{
            "name": r.name,
            "marketplace": r.marketplace,
            "installed_version": r.installed_version,
            "latest_version": r.latest_version,
            "status": r.status,
            "detail": r.detail,
        } for r in rows], indent=2, ensure_ascii=False)

    if not rows:
        return "[fleet] インストール済みプラグインが見つかりません。\n"

    name_w = max(len("PLUGIN"), max(len(r.name) for r in rows))
    iv_w = max(len("INSTALLED"), max(len(r.installed_version) for r in rows))
    lv_w = max(len("LATEST"), max(len(r.latest_version or "-") for r in rows))

    lines = []
    header = f"{'':2}{'PLUGIN':<{name_w}}  {'INSTALLED':<{iv_w}}  {'LATEST':<{lv_w}}  STATUS"
    lines.append(header)
    lines.append("-" * len(header))
    # update/drift を上に寄せて目立たせる
    order = {"update": 0, "drift": 1, "unknown": 2, "ok": 3}
    for r in sorted(rows, key=lambda x: (order.get(x.status, 9), x.name)):
        mark = _STATUS_MARK.get(r.status, " ")
        lv = r.latest_version or "-"
        line = f"{mark} {r.name:<{name_w}}  {r.installed_version:<{iv_w}}  {lv:<{lv_w}}  {r.status}"
        if r.detail and r.status in ("update", "drift"):
            line += f"  — {r.detail}"
        lines.append(line)

    n_update = sum(1 for r in rows if r.status == "update")
    n_drift = sum(1 for r in rows if r.status == "drift")
    lines.append("")
    if n_update or n_drift:
        lines.append(
            f"[fleet] 要対応: update {n_update} 件 / drift {n_drift} 件。"
            " update は `claude plugin update <name>`、drift は uninstall→install で同期。"
        )
    else:
        lines.append("[fleet] すべて最新です ✓")
    return "\n".join(lines) + "\n"
