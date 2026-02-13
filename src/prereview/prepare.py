from __future__ import annotations

import fnmatch
import subprocess
from pathlib import PurePosixPath
from pathlib import Path
from typing import Any

from prereview.diff_parser import parse_unified_diff
from prereview.models import FilePatch
from prereview.util import hash_text, utc_now_iso


def _run_git_command(args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode not in {0, 1}:
        raise RuntimeError(proc.stderr.strip() or f"git command failed: {' '.join(args)}")
    return proc.stdout


def _read_patch_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_stdin_patch() -> str:
    import sys

    return sys.stdin.read()


def _build_untracked_patch() -> str:
    names = _run_git_command(["ls-files", "--others", "--exclude-standard"]).splitlines()
    chunks: list[str] = []
    for name in names:
        file_path = Path(name)
        if not file_path.is_file():
            continue
        proc = subprocess.run(
            ["git", "diff", "--no-index", "--", "/dev/null", str(file_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode in {0, 1} and proc.stdout:
            chunks.append(proc.stdout.rstrip("\n"))
    if not chunks:
        return ""
    return "\n\n".join(chunks) + "\n"


def collect_patch_text(
    patch_file: Path | None,
    stdin_patch: bool,
    git_range: str | None,
    use_working_tree: bool,
    include_untracked: bool,
    exclude_paths: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    mode = ""
    raw_patch = ""

    if patch_file is not None:
        mode = "patch-file"
        raw_patch = _read_patch_file(patch_file)
    elif stdin_patch:
        mode = "stdin"
        raw_patch = _read_stdin_patch()
    elif git_range:
        mode = "git-range"
        raw_patch = _run_git_command(["diff", git_range])
    else:
        mode = "working-tree"
        if use_working_tree or not (patch_file or stdin_patch or git_range):
            raw_patch = _run_git_command(["diff", "HEAD"])

    if include_untracked:
        untracked_patch = _build_untracked_patch()
        if untracked_patch:
            if raw_patch and not raw_patch.endswith("\n"):
                raw_patch += "\n"
            raw_patch += untracked_patch

    source = {
        "mode": mode,
        "git_range": git_range,
        "include_untracked": include_untracked,
        "exclude_paths": exclude_paths or [],
        "cwd": str(Path.cwd()),
        "prepared_at": utc_now_iso(),
    }
    return raw_patch, source


def _stats(files: list[FilePatch]) -> dict[str, int]:
    return {
        "files_changed": len(files),
        "additions": sum(file.additions for file in files),
        "deletions": sum(file.deletions for file in files),
    }


def _is_excluded(path: str, exclude_paths: list[str]) -> bool:
    normalized = str(PurePosixPath(path)).lstrip("./")
    for pattern in exclude_paths:
        normalized_pattern = pattern.strip().lstrip("./")
        if not normalized_pattern:
            continue
        if normalized_pattern.endswith("/**"):
            prefix = normalized_pattern[:-3].rstrip("/")
            if normalized == prefix or normalized.startswith(prefix + "/"):
                return True
        if fnmatch.fnmatchcase(normalized, normalized_pattern):
            return True
    return False


def make_prepared_review(
    raw_patch: str,
    source: dict[str, Any],
    *,
    exclude_paths: list[str] | None = None,
) -> dict[str, Any]:
    files = parse_unified_diff(raw_patch)
    patterns = exclude_paths or []
    if patterns:
        files = [file for file in files if not _is_excluded(file.path, patterns)]
    prepared_id = hash_text(raw_patch)
    prepared = {
        "version": "1",
        "prepared_id": prepared_id,
        "source": source,
        "raw_patch": raw_patch,
        "stats": _stats(files),
        "files": [file.to_dict() for file in files],
    }
    return prepared
