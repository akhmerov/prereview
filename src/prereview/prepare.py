from __future__ import annotations

import fnmatch
import json
import subprocess
from pathlib import PurePosixPath
from pathlib import Path
from typing import Any

from prereview.diff_parser import parse_unified_diff
from prereview.models import FilePatch, Hunk, Line
from prereview.util import hash_text, utc_now_iso

_MAX_UNTRACKED_FILE_BYTES = 8 * 1024 * 1024
_MAX_UNTRACKED_FILE_PATCH_BYTES = 8 * 1024 * 1024
_MAX_UNTRACKED_PATCH_BYTES = 24 * 1024 * 1024
_MAX_TRACKED_PATCH_BYTES = 24 * 1024 * 1024


def _run_git_command(args: list[str], *, max_output_bytes: int | None = None) -> str:
    if max_output_bytes is not None:
        proc = subprocess.Popen(
            ["git", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if proc.stdout is None:
            raise RuntimeError("git command did not provide stdout stream")

        chunks: list[bytes] = []
        total_bytes = 0
        while True:
            chunk = proc.stdout.read(64 * 1024)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > max_output_bytes:
                proc.kill()
                proc.wait()
                raise RuntimeError(
                    "Diff output exceeded safe size budget "
                    f"({max_output_bytes} bytes). Narrow scope with --exclude-path."
                )
            chunks.append(chunk)

        returncode = proc.wait()
        output = b"".join(chunks).decode("utf-8", errors="replace")
        if returncode not in {0, 1}:
            raise RuntimeError(
                output.strip() or f"git command failed: {' '.join(args)}"
            )
        return output

    proc = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode not in {0, 1}:
        raise RuntimeError(
            proc.stderr.strip() or f"git command failed: {' '.join(args)}"
        )
    return proc.stdout


def _read_patch_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _build_untracked_patch(exclude_paths: list[str]) -> str:
    names = _run_git_command(
        ["ls-files", "--others", "--exclude-standard"]
    ).splitlines()
    chunks: list[str] = []
    total_bytes = 0
    for name in names:
        if _is_excluded(name, exclude_paths):
            continue
        file_path = Path(name)
        if not file_path.is_file():
            continue
        try:
            file_size = file_path.stat().st_size
        except OSError:
            continue

        if file_size > _MAX_UNTRACKED_FILE_BYTES:
            raise RuntimeError(
                "Refusing to include oversized untracked file "
                f"{name!r} ({file_size} bytes). Use --exclude-path to filter generated artifacts."
            )

        patch_text = _run_git_command(
            ["diff", "--no-index", "--", "/dev/null", str(file_path)],
            max_output_bytes=_MAX_UNTRACKED_FILE_PATCH_BYTES,
        )
        if not patch_text:
            continue

        patch_piece = patch_text.rstrip("\n")
        total_bytes += len(patch_piece.encode("utf-8")) + 2
        if total_bytes > _MAX_UNTRACKED_PATCH_BYTES:
            raise RuntimeError(
                "Untracked diff payload exceeded safe size budget "
                f"({_MAX_UNTRACKED_PATCH_BYTES} bytes). Use --exclude-path to scope generated artifacts."
            )
        chunks.append(patch_piece)
    if not chunks:
        return ""
    return "\n\n".join(chunks) + "\n"


def build_source_spec(
    *,
    patch_file: Path | None,
    git_range: str | None,
    use_working_tree: bool,
    include_untracked: bool,
    exclude_paths: list[str],
    exclude_binary: bool = True,
) -> dict[str, Any]:
    if patch_file is not None:
        mode = "patch-file"
    elif git_range:
        mode = "git-range"
    else:
        mode = "working-tree"

    source_spec: dict[str, Any] = {
        "mode": mode,
        "patch_file": str(patch_file.resolve()) if patch_file is not None else None,
        "git_range": git_range,
        "use_working_tree": use_working_tree or mode == "working-tree",
        "include_untracked": include_untracked,
        "exclude_binary": exclude_binary,
        "exclude_paths": exclude_paths,
        "cwd": str(Path.cwd()),
    }
    return source_spec


def collect_patch_text_from_source(source_spec: dict[str, Any]) -> str:
    exclude_paths = source_spec.get("exclude_paths", [])
    if not isinstance(exclude_paths, list):
        exclude_paths = []
    exclude_patterns = [str(pattern) for pattern in exclude_paths]
    exclude_pathspecs = [
        f":(exclude,glob){pattern.lstrip('./')}"
        for pattern in exclude_patterns
        if pattern.strip()
    ]

    mode = source_spec.get("mode")
    if mode == "patch-file":
        patch_file = source_spec.get("patch_file")
        if not isinstance(patch_file, str) or not patch_file:
            raise RuntimeError("source_spec.patch_file is required for patch-file mode")
        raw_patch = _read_patch_file(Path(patch_file))
    elif mode == "git-range":
        git_range = source_spec.get("git_range")
        if not isinstance(git_range, str) or not git_range:
            raise RuntimeError("source_spec.git_range is required for git-range mode")
        args = ["diff", git_range]
        if exclude_pathspecs:
            args.extend(["--", ".", *exclude_pathspecs])
        raw_patch = _run_git_command(args, max_output_bytes=_MAX_TRACKED_PATCH_BYTES)
    elif mode == "working-tree":
        args = ["diff", "HEAD"]
        if exclude_pathspecs:
            args.extend(["--", ".", *exclude_pathspecs])
        raw_patch = _run_git_command(args, max_output_bytes=_MAX_TRACKED_PATCH_BYTES)
    else:
        raise RuntimeError(f"Unsupported source mode: {mode}")

    if bool(source_spec.get("include_untracked")):
        untracked_patch = _build_untracked_patch(exclude_patterns)
        if untracked_patch:
            if raw_patch and not raw_patch.endswith("\n"):
                raw_patch += "\n"
            raw_patch += untracked_patch

    return raw_patch


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


def _parse_files(
    raw_patch: str, exclude_paths: list[str], *, exclude_binary: bool
) -> list[FilePatch]:
    files = parse_unified_diff(raw_patch)
    if exclude_paths:
        files = [file for file in files if not _is_excluded(file.path, exclude_paths)]
    if exclude_binary:
        files = [file for file in files if not file.is_binary]
    return files


def _stats(files: list[FilePatch]) -> dict[str, int]:
    return {
        "files_changed": len(files),
        "additions": sum(file.additions for file in files),
        "deletions": sum(file.deletions for file in files),
    }


def _line_excerpt(value: str, limit: int = 88) -> str:
    compact = " ".join(value.strip().split())
    if not compact:
        return "<empty>"
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _risk_hint(lines: list[Line]) -> str | None:
    risky_tokens = (
        ("subprocess", "process execution behavior changed"),
        ("check=False", "error handling behavior changed"),
        ("shell=True", "command execution safety assumptions changed"),
        ("re.compile(", "pattern matching/parsing behavior changed"),
        ("exclude_path", "review scoping behavior changed"),
        ("strict", "validation strictness behavior changed"),
        ("raise ", "error propagation behavior changed"),
        ("except ", "error handling branches changed"),
    )
    for line in lines:
        if line.line_type != "add":
            continue
        text = line.content
        for token, reason in risky_tokens:
            if token in text:
                return reason
    return None


def _focus_snippets(lines: list[Line], *, limit: int = 3) -> list[str]:
    snippets: list[str] = []
    prefixes = (
        "def ",
        "class ",
        "if ",
        "for ",
        "while ",
        "with ",
        "return ",
        "raise ",
        "try:",
        "except ",
    )
    for line in lines:
        if line.line_type != "add":
            continue
        content = line.content.strip()
        if not content:
            continue
        if any(content.startswith(prefix) for prefix in prefixes) or (
            "=" in content and not content.startswith("#")
        ):
            snippets.append(_line_excerpt(content))
        if len(snippets) >= limit:
            break

    if not snippets:
        for line in lines:
            if line.line_type == "add" and line.content.strip():
                snippets.append(_line_excerpt(line.content))
                break
    return snippets


def _anchor_id(path: str, hunk: Hunk) -> str:
    return hash_text(f"{path}:{hunk.stable_hunk_id}")


def _anchor_title(path: str, hunk_index: int) -> str:
    return f"{path} change focus {hunk_index}"


def _build_context_files(files: list[FilePatch]) -> list[dict[str, Any]]:
    context_files: list[dict[str, Any]] = []
    for file_patch in files:
        anchors: list[dict[str, Any]] = []
        for index, hunk in enumerate(file_patch.hunks, start=1):
            anchor = {
                "anchor_id": _anchor_id(file_patch.path, hunk),
                "title": _anchor_title(file_patch.path, index),
                "focus_snippets": _focus_snippets(hunk.lines),
                "risk_hint": _risk_hint(hunk.lines),
            }
            anchors.append(anchor)

        context_files.append(
            {
                "path": file_patch.path,
                "status": file_patch.status,
                "anchors": anchors,
            }
        )
    return context_files


def build_review_context(raw_patch: str, source_spec: dict[str, Any]) -> dict[str, Any]:
    exclude_paths = source_spec.get("exclude_paths", [])
    if not isinstance(exclude_paths, list):
        exclude_paths = []
    exclude_binary = bool(source_spec.get("exclude_binary", True))

    files = _parse_files(
        raw_patch,
        [str(pattern) for pattern in exclude_paths],
        exclude_binary=exclude_binary,
    )
    diff_fingerprint = hash_text(raw_patch)
    context_files = _build_context_files(files)

    context_payload = {
        "version": "2",
        "generated_at": utc_now_iso(),
        "source_spec": source_spec,
        "diff_fingerprint": diff_fingerprint,
        "stats": _stats(files),
        "files": context_files,
    }
    context_payload["context_id"] = hash_text(
        json.dumps(
            {
                "source_spec": source_spec,
                "diff_fingerprint": diff_fingerprint,
                "files": [
                    {
                        "path": file_entry["path"],
                        "anchors": [a["anchor_id"] for a in file_entry["anchors"]],
                    }
                    for file_entry in context_files
                ],
            },
            sort_keys=True,
        )
    )
    return context_payload


def recompute_runtime_from_context(context: dict[str, Any]) -> dict[str, Any]:
    source_spec = context.get("source_spec")
    if not isinstance(source_spec, dict):
        raise RuntimeError("Context is missing source_spec.")

    raw_patch = collect_patch_text_from_source(source_spec)
    exclude_paths = source_spec.get("exclude_paths", [])
    if not isinstance(exclude_paths, list):
        exclude_paths = []
    exclude_binary = bool(source_spec.get("exclude_binary", True))

    files = _parse_files(
        raw_patch,
        [str(pattern) for pattern in exclude_paths],
        exclude_binary=exclude_binary,
    )
    runtime_files = [file_patch.to_dict() for file_patch in files]

    anchor_index: dict[str, dict[str, dict[str, Any]]] = {}
    for file_dict in runtime_files:
        path = file_dict.get("path")
        if not isinstance(path, str):
            continue
        file_anchor_map: dict[str, dict[str, Any]] = {}
        for hunk in file_dict.get("hunks", []):
            if not isinstance(hunk, dict):
                continue
            stable_hunk_id = hunk.get("stable_hunk_id")
            if not isinstance(stable_hunk_id, str) or not stable_hunk_id:
                continue
            anchor_id = hash_text(f"{path}:{stable_hunk_id}")
            anchor_line: int | None = None
            for line in hunk.get("lines", []):
                if (
                    isinstance(line, dict)
                    and line.get("type") == "add"
                    and isinstance(line.get("new_line"), int)
                ):
                    anchor_line = line["new_line"]
                    break

            file_anchor_map[anchor_id] = {
                "anchor_id": anchor_id,
                "hunk_id": hunk.get("hunk_id"),
                "stable_hunk_id": stable_hunk_id,
                "new_start": hunk.get("new_start"),
                "new_end": (
                    hunk.get("new_start", 1) + max(int(hunk.get("new_count", 1)) - 1, 0)
                )
                if isinstance(hunk.get("new_start"), int)
                else 1,
                "anchor_line": anchor_line,
            }
        anchor_index[path] = file_anchor_map

    return {
        "diff_fingerprint": hash_text(raw_patch),
        "stats": _stats(files),
        "files": runtime_files,
        "anchor_index": anchor_index,
    }
