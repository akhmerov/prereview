from __future__ import annotations

import re
from dataclasses import replace

from prereview.models import FilePatch, Hunk, Line
from prereview.util import hash_text

_DIFF_GIT_RE = re.compile(r"^diff --git a/(.+) b/(.+)$")
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


def _normalize_path(value: str) -> str:
    path = value.strip()
    if path.startswith('"') and path.endswith('"') and len(path) >= 2:
        path = path[1:-1]
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    return path


def _normalize_header_path(value: str) -> str | None:
    path = value.strip().split("\t", 1)[0]
    if path == "/dev/null":
        return None
    return _normalize_path(path)


def _parse_hunk(lines: list[str], start: int, file_path: str) -> tuple[Hunk, int]:
    header_line = lines[start]
    match = _HUNK_RE.match(header_line)
    if not match:
        raise ValueError(f"Invalid hunk header: {header_line}")

    old_start = int(match.group(1))
    old_count = int(match.group(2) or "1")
    new_start = int(match.group(3))
    new_count = int(match.group(4) or "1")
    trailing_header = match.group(5).strip()

    old_line = old_start
    new_line = new_start
    parsed_lines: list[Line] = []

    idx = start + 1
    while idx < len(lines):
        line = lines[idx]
        if line.startswith("diff --git ") or line.startswith("@@ "):
            break
        if line.startswith("\\ No newline at end of file"):
            idx += 1
            continue

        if line.startswith("+") and not line.startswith("+++ "):
            content = line[1:]
            line_key = f"{file_path}:add:{new_line}:{content}"
            parsed_lines.append(
                Line(
                    line_id=hash_text(line_key),
                    line_type="add",
                    content=content,
                    old_line=None,
                    new_line=new_line,
                )
            )
            new_line += 1
        elif line.startswith("-") and not line.startswith("--- "):
            content = line[1:]
            line_key = f"{file_path}:del:{old_line}:{content}"
            parsed_lines.append(
                Line(
                    line_id=hash_text(line_key),
                    line_type="del",
                    content=content,
                    old_line=old_line,
                    new_line=None,
                )
            )
            old_line += 1
        else:
            content = line[1:] if line.startswith(" ") else line
            line_key = f"{file_path}:ctx:{old_line}:{new_line}:{content}"
            parsed_lines.append(
                Line(
                    line_id=hash_text(line_key),
                    line_type="context",
                    content=content,
                    old_line=old_line,
                    new_line=new_line,
                )
            )
            old_line += 1
            new_line += 1
        idx += 1

    hunk_key = f"{file_path}:{old_start}:{old_count}:{new_start}:{new_count}:{trailing_header}"
    hunk = Hunk(
        hunk_id=hash_text(hunk_key),
        old_start=old_start,
        old_count=old_count,
        new_start=new_start,
        new_count=new_count,
        header=trailing_header,
        lines=parsed_lines,
    )
    return hunk, idx


def parse_unified_diff(raw_patch: str) -> list[FilePatch]:
    if not raw_patch.strip():
        return []

    lines = raw_patch.splitlines()
    files: list[FilePatch] = []
    current: FilePatch | None = None

    idx = 0
    while idx < len(lines):
        line = lines[idx]

        if line.startswith("diff --git "):
            if current is not None:
                files.append(current)

            old_path: str | None = None
            new_path: str | None = None
            match = _DIFF_GIT_RE.match(line)
            if match:
                old_path = _normalize_path(match.group(1))
                new_path = _normalize_path(match.group(2))

            default_path = new_path or old_path or "unknown"
            file_id = hash_text(default_path)
            current = FilePatch(
                file_id=file_id,
                path=default_path,
                old_path=old_path,
                new_path=new_path,
            )
            idx += 1
            continue

        if current is None:
            idx += 1
            continue

        if line.startswith("new file mode "):
            current.is_new = True
        elif line.startswith("deleted file mode "):
            current.is_deleted = True
        elif line.startswith("rename from "):
            current.is_rename = True
            current.old_path = _normalize_path(line.removeprefix("rename from "))
        elif line.startswith("rename to "):
            current.is_rename = True
            current.new_path = _normalize_path(line.removeprefix("rename to "))
        elif line.startswith("Binary files "):
            current.is_binary = True
        elif line.startswith("--- "):
            current.old_path = _normalize_header_path(line.removeprefix("--- "))
        elif line.startswith("+++ "):
            current.new_path = _normalize_header_path(line.removeprefix("+++ "))
        elif line.startswith("@@ "):
            hunk, idx = _parse_hunk(lines, idx, current.path)
            current.hunks.append(hunk)
            continue

        idx += 1

    if current is not None:
        files.append(current)

    normalized_files: list[FilePatch] = []
    for file_patch in files:
        canonical_path = file_patch.new_path or file_patch.old_path or file_patch.path
        file_id = hash_text(canonical_path)
        normalized_files.append(
            replace(file_patch, file_id=file_id, path=canonical_path)
        )

    return normalized_files
