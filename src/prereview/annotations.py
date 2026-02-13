from __future__ import annotations

from typing import Any, Iterable

_ALLOWED_SEVERITIES = {"info", "note", "warning", "risk"}


def _error(code: str, message: str, location: str) -> dict[str, str]:
    return {"level": "error", "code": code, "message": message, "location": location}


def _warning(code: str, message: str, location: str) -> dict[str, str]:
    return {"level": "warning", "code": code, "message": message, "location": location}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_comment(comment: Any, location: str) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not isinstance(comment, dict):
        return [_error("comment_type", "Comment must be an object.", location)]

    if "line_start" not in comment:
        issues.append(_error("missing_line_start", "Comment is missing line_start.", location))
    elif not _is_int(comment["line_start"]):
        issues.append(_error("line_start_type", "line_start must be an integer.", location))

    if "line_end" in comment and not _is_int(comment["line_end"]):
        issues.append(_error("line_end_type", "line_end must be an integer.", location))

    if "text" not in comment:
        issues.append(_error("missing_text", "Comment is missing text.", location))
    elif not isinstance(comment["text"], str) or not comment["text"].strip():
        issues.append(_error("text_type", "Comment text must be a non-empty string.", location))

    severity = comment.get("severity")
    if severity is not None and severity not in _ALLOWED_SEVERITIES:
        issues.append(
            _error(
                "bad_severity",
                "severity must be one of info, note, warning, or risk.",
                location,
            )
        )
    return issues


def _validate_hunk(hunk: Any, location: str) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not isinstance(hunk, dict):
        return [_error("hunk_type", "Hunk annotation must be an object.", location)]

    has_hunk_id = isinstance(hunk.get("hunk_id"), str) and bool(hunk.get("hunk_id"))
    has_range = _is_int(hunk.get("new_start")) and _is_int(hunk.get("new_end"))
    if not (has_hunk_id or has_range):
        issues.append(
            _error(
                "hunk_anchor_missing",
                "Hunk annotation needs either hunk_id or both new_start/new_end.",
                location,
            )
        )

    comments = hunk.get("comments", [])
    if not isinstance(comments, list):
        issues.append(_error("hunk_comments_type", "hunk.comments must be a list.", location))
    else:
        for idx, comment in enumerate(comments):
            issues.extend(_validate_comment(comment, f"{location}.comments[{idx}]"))

    return issues


def validate_annotation_schema(annotations: Any) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    if not isinstance(annotations, dict):
        return [_error("root_type", "Annotations must be a JSON object.", "$")]

    if annotations.get("version") != "1":
        issues.append(_error("bad_version", "version must be the string '1'.", "$.version"))

    target = annotations.get("target_prepared_review")
    if not isinstance(target, str) or not target:
        issues.append(
            _error(
                "missing_target",
                "target_prepared_review must be a non-empty string.",
                "$.target_prepared_review",
            )
        )

    files = annotations.get("files")
    if not isinstance(files, list):
        issues.append(_error("files_type", "files must be a list.", "$.files"))
        return issues

    for file_idx, file_entry in enumerate(files):
        location = f"$.files[{file_idx}]"
        if not isinstance(file_entry, dict):
            issues.append(_error("file_type", "Each files entry must be an object.", location))
            continue

        path = file_entry.get("path")
        if not isinstance(path, str) or not path:
            issues.append(_error("file_path", "files.path must be a non-empty string.", f"{location}.path"))

        breadcrumbs = file_entry.get("breadcrumbs")
        if breadcrumbs is not None:
            if not isinstance(breadcrumbs, list) or not all(isinstance(p, str) for p in breadcrumbs):
                issues.append(
                    _error(
                        "breadcrumbs_type",
                        "breadcrumbs must be a list of strings.",
                        f"{location}.breadcrumbs",
                    )
                )

        comments = file_entry.get("comments", [])
        if not isinstance(comments, list):
            issues.append(_error("file_comments_type", "files.comments must be a list.", f"{location}.comments"))
        else:
            for comment_idx, comment in enumerate(comments):
                issues.extend(_validate_comment(comment, f"{location}.comments[{comment_idx}]"))

        hunks = file_entry.get("hunks", [])
        if not isinstance(hunks, list):
            issues.append(_error("hunks_type", "files.hunks must be a list.", f"{location}.hunks"))
        else:
            for hunk_idx, hunk in enumerate(hunks):
                issues.extend(_validate_hunk(hunk, f"{location}.hunks[{hunk_idx}]"))

        if "summary" in file_entry and not isinstance(file_entry["summary"], str):
            issues.append(_warning("summary_type", "summary should be a string.", f"{location}.summary"))

    return issues


def iter_file_comments(file_annotation: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    for comment in file_annotation.get("comments", []):
        yield "file", comment
    for hunk in file_annotation.get("hunks", []):
        for comment in hunk.get("comments", []):
            yield "hunk", comment
