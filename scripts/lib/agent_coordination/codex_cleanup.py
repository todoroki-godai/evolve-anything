"""既知の Claude→Codex 機械置換4指紋だけを修復する一回性ツール（#268）。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

from .core import CoordinationError

FINGERPRINTS: tuple[tuple[str, str, str | None], ...] = (
    ("dot_codex_plugin", ".Codex-plugin", ".claude-plugin"),
    ("uppercase_codex_home", ".Codex/", ".claude/"),
    ("missing_codex_validate", "Codex plugin validate", "claude plugin validate"),
    # 復元先が一意でないため検出だけ行い、自動置換しない。
    ("codex_code_name", "Codex Code", None),
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _targets(root: Path) -> Iterable[Path]:
    agents_md = root / "AGENTS.md"
    if agents_md.is_file():
        yield agents_md
    agents = root / "agents"
    if agents.is_dir():
        yield from sorted(agents.glob("*.toml"))


def audit(root: Path) -> dict[str, Any]:
    """対象ファイルを変更せず、既知指紋の件数を返す。"""
    root = Path(root).resolve()
    files: list[dict[str, Any]] = []
    for path in _targets(root):
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        findings = {
            fingerprint: text.count(before)
            for fingerprint, before, _after in FINGERPRINTS
            if before in text
        }
        if findings:
            files.append(
                {
                    "path": str(path),
                    "sha256": _sha256(raw),
                    "findings": findings,
                }
            )
    return {
        "schema_version": 1,
        "root": str(root),
        "files": files,
        "finding_count": sum(sum(f["findings"].values()) for f in files),
    }


def _replacement(text: str) -> tuple[str, list[str]]:
    after = text
    applied: list[str] = []
    for fingerprint, needle, replacement in FINGERPRINTS:
        if replacement is not None and needle in after:
            after = after.replace(needle, replacement)
            applied.append(fingerprint)
    return after, applied


def build_plan(root: Path) -> dict[str, Any]:
    report = audit(root)
    changes: list[dict[str, Any]] = []
    for finding in report["files"]:
        path = Path(finding["path"])
        before = path.read_text(encoding="utf-8")
        after, applied = _replacement(before)
        if after != before:
            changes.append(
                {
                    "path": str(path),
                    "before_sha256": finding["sha256"],
                    "after_sha256": _sha256(after.encode("utf-8")),
                    "fingerprints": applied,
                    "replacement_text": after,
                }
            )
    return {
        "schema_version": 1,
        "root": report["root"],
        "changes": changes,
    }


def write_plan(root: Path, output: Path) -> dict[str, Any]:
    plan = build_plan(root)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return plan


def apply_plan(plan_path: Path, *, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise CoordinationError("applyには --yes による明示承認が必要です")
    try:
        plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CoordinationError(f"planを読めません: {exc}") from exc
    changes = plan.get("changes")
    if not isinstance(changes, list):
        raise CoordinationError("plan.changesが配列ではありません")
    try:
        root = Path(str(plan["root"])).resolve(strict=True)
    except (KeyError, OSError) as exc:
        raise CoordinationError(f"plan.rootが不正です: {exc}") from exc

    # 1件でもstaleなら1バイトも変更しない。
    for change in changes:
        path = Path(str(change["path"])).resolve(strict=True)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise CoordinationError(f"plan.root外のpathです: {path}") from exc
        current = path.read_bytes()
        if _sha256(current) != change.get("before_sha256"):
            raise CoordinationError(f"plan作成後に変更されています: {path}")
        replacement_text, fingerprints = _replacement(current.decode("utf-8"))
        if (
            replacement_text != change.get("replacement_text")
            or fingerprints != change.get("fingerprints")
            or _sha256(replacement_text.encode("utf-8")) != change.get("after_sha256")
        ):
            raise CoordinationError(f"plan内容を既知fingerprintから再現できません: {path}")

    applied: list[dict[str, str]] = []
    for change in changes:
        path = Path(str(change["path"])).resolve(strict=True)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise CoordinationError(f"plan.root外のpathです: {path}") from exc
        before_sha = str(change["before_sha256"])
        backup = path.with_name(f"{path.name}.bak.{before_sha[:12]}")
        if backup.exists():
            if _sha256(backup.read_bytes()) != before_sha:
                raise CoordinationError(f"backupが既存内容と衝突します: {backup}")
        else:
            backup.write_bytes(path.read_bytes())
        replacement = str(change["replacement_text"]).encode("utf-8")
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with tmp.open("xb") as handle:
                handle.write(replacement)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()
        applied.append({"path": str(path), "backup": str(backup)})
    return {"applied": applied, "count": len(applied)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evolve-codex-config-cleanup")
    parser.add_argument("--root", type=Path, default=Path.home() / ".codex")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit")
    plan = sub.add_parser("plan")
    plan.add_argument("--output", type=Path, required=True)
    apply = sub.add_parser("apply")
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--yes", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "audit":
            result = audit(args.root)
        elif args.command == "plan":
            result = write_plan(args.root, args.output)
        else:
            result = apply_plan(args.plan, confirmed=args.yes)
    except CoordinationError as exc:
        print(f"[codex-config-cleanup] error: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
