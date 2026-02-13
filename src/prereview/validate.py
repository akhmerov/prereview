from __future__ import annotations

from collections import defaultdict
from typing import Any

from prereview.annotations import iter_file_comments, validate_annotation_schema


def _issue(level: str, code: str, message: str, location: str) -> dict[str, str]:
    return {"level": level, "code": code, "message": message, "location": location}


def _changed_lines(file_entry: dict[str, Any]) -> set[int]:
    lines: set[int] = set()
    for hunk in file_entry.get("hunks", []):
        for line in hunk.get("lines", []):
            new_line = line.get("new_line")
            if isinstance(new_line, int):
                lines.add(new_line)
    return lines


def _hunk_index(file_entry: dict[str, Any]) -> tuple[set[str], list[tuple[int, int]]]:
    ids: set[str] = set()
    ranges: list[tuple[int, int]] = []
    for hunk in file_entry.get("hunks", []):
        hunk_id = hunk.get("hunk_id")
        if isinstance(hunk_id, str) and hunk_id:
            ids.add(hunk_id)
        new_start = hunk.get("new_start")
        new_count = hunk.get("new_count")
        if isinstance(new_start, int) and isinstance(new_count, int):
            new_end = new_start + max(new_count - 1, 0)
            ranges.append((new_start, new_end))
    return ids, ranges


def _line_range(comment: dict[str, Any]) -> tuple[int | None, int | None]:
    start = comment.get("line_start")
    end = comment.get("line_end", start)
    if isinstance(start, int) and isinstance(end, int):
        if end < start:
            return end, start
        return start, end
    return None, None


def validate_annotations(
    prepared: Any,
    annotations: Any,
    *,
    strict: bool,
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    issues.extend(validate_annotation_schema(annotations))

    if not isinstance(prepared, dict):
        issues.append(_issue("error", "prepared_type", "Prepared review must be a JSON object.", "$"))
        return {
            "valid": False,
            "issues": issues,
            "stats": {
                "mapped_comments": 0,
                "unmapped_comments": 0,
                "files_with_annotations": 0,
            },
        }

    prepared_id = prepared.get("prepared_id")
    target = annotations.get("target_prepared_review") if isinstance(annotations, dict) else None
    if isinstance(prepared_id, str) and isinstance(target, str) and target != prepared_id:
        issues.append(
            _issue(
                "error",
                "prepared_mismatch",
                "target_prepared_review does not match prepared_id.",
                "$.target_prepared_review",
            )
        )

    prepared_files = prepared.get("files", [])
    file_map: dict[str, dict[str, Any]] = {}
    for file_entry in prepared_files:
        path = file_entry.get("path")
        if isinstance(path, str):
            file_map[path] = file_entry

    mapped_comments = 0
    unmapped_comments = 0

    if isinstance(annotations, dict) and isinstance(annotations.get("files"), list):
        for file_idx, file_annotation in enumerate(annotations["files"]):
            if not isinstance(file_annotation, dict):
                continue
            path = file_annotation.get("path")
            location = f"$.files[{file_idx}]"
            if not isinstance(path, str):
                continue

            prepared_file = file_map.get(path)
            if prepared_file is None:
                level = "error" if strict else "warning"
                issues.append(
                    _issue(
                        level,
                        "unknown_file",
                        f"Annotation file path {path!r} not found in prepared diff.",
                        f"{location}.path",
                    )
                )
                continue

            valid_new_lines = _changed_lines(prepared_file)
            hunk_ids, hunk_ranges = _hunk_index(prepared_file)

            for hunk_idx, hunk_annotation in enumerate(file_annotation.get("hunks", [])):
                if not isinstance(hunk_annotation, dict):
                    continue
                hunk_location = f"{location}.hunks[{hunk_idx}]"
                hunk_id = hunk_annotation.get("hunk_id")
                if isinstance(hunk_id, str) and hunk_id and hunk_id not in hunk_ids:
                    level = "error" if strict else "warning"
                    issues.append(
                        _issue(
                            level,
                            "unknown_hunk",
                            f"Hunk id {hunk_id!r} was not found for file {path!r}.",
                            f"{hunk_location}.hunk_id",
                        )
                    )

                new_start = hunk_annotation.get("new_start")
                new_end = hunk_annotation.get("new_end")
                if isinstance(new_start, int) and isinstance(new_end, int):
                    if new_end < new_start:
                        new_start, new_end = new_end, new_start
                    intersects = any(new_start <= end and new_end >= start for start, end in hunk_ranges)
                    if not intersects:
                        level = "error" if strict else "warning"
                        issues.append(
                            _issue(
                                level,
                                "unmapped_hunk_range",
                                "Hunk range is outside all prepared hunks.",
                                hunk_location,
                            )
                        )

            for comment_kind, comment in iter_file_comments(file_annotation):
                start, end = _line_range(comment)
                if start is None or end is None:
                    continue

                if any(line not in valid_new_lines for line in range(start, end + 1)):
                    unmapped_comments += 1
                    level = "error" if strict else "warning"
                    issues.append(
                        _issue(
                            level,
                            "unmapped_comment",
                            f"{comment_kind} comment lines {start}-{end} do not map to changed new-file lines.",
                            location,
                        )
                    )
                else:
                    mapped_comments += 1

    if strict:
        # Treat schema warnings as errors in strict mode.
        for issue in issues:
            if issue["level"] == "warning":
                issue["level"] = "error"

    valid = not any(issue["level"] == "error" for issue in issues)
    files_with_annotations = 0
    if isinstance(annotations, dict) and isinstance(annotations.get("files"), list):
        files_with_annotations = sum(1 for file_entry in annotations["files"] if isinstance(file_entry, dict))

    return {
        "valid": valid,
        "issues": issues,
        "stats": {
            "mapped_comments": mapped_comments,
            "unmapped_comments": unmapped_comments,
            "files_with_annotations": files_with_annotations,
        },
    }


def grouped_issues(report: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for issue in report.get("issues", []):
        grouped[str(issue.get("level", "warning"))].append(issue)
    return dict(grouped)
