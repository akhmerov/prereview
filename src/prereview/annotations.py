from __future__ import annotations

from typing import Any, Iterable

_ALLOWED_SEVERITIES = {"info", "note", "warning", "risk"}


def _error(code: str, message: str, location: str) -> dict[str, str]:
    return {"level": "error", "code": code, "message": message, "location": location}


def _warning(code: str, message: str, location: str) -> dict[str, str]:
    return {"level": "warning", "code": code, "message": message, "location": location}


def validate_annotation_schema(annotations: Any) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    if not isinstance(annotations, dict):
        return [_error("root_type", "Annotations must be a JSON object.", "$")]

    if annotations.get("version") != "2":
        issues.append(_error("bad_version", "version must be the string '2'.", "$.version"))

    target = annotations.get("target_context_id")
    if not isinstance(target, str) or not target:
        issues.append(
            _error(
                "missing_target",
                "target_context_id must be a non-empty string.",
                "$.target_context_id",
            )
        )

    overview = annotations.get("overview")
    if overview is not None:
        if not isinstance(overview, list) or not all(isinstance(line, str) and line.strip() for line in overview):
            issues.append(
                _error(
                    "overview_type",
                    "overview must be a list of non-empty strings.",
                    "$.overview",
                )
            )
        elif len(overview) > 8:
            issues.append(
                _warning(
                    "overview_length",
                    "overview should typically be 2-5 lines for reviewer readability.",
                    "$.overview",
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

        if "summary" in file_entry and not isinstance(file_entry["summary"], str):
            issues.append(_error("summary_type", "summary must be a string.", f"{location}.summary"))

        anchors = file_entry.get("anchors")
        if not isinstance(anchors, list):
            issues.append(_error("anchors_type", "files.anchors must be a list.", f"{location}.anchors"))
            continue

        for anchor_idx, anchor in enumerate(anchors):
            anchor_loc = f"{location}.anchors[{anchor_idx}]"
            if not isinstance(anchor, dict):
                issues.append(_error("anchor_type", "Anchor must be an object.", anchor_loc))
                continue

            anchor_id = anchor.get("anchor_id")
            if not isinstance(anchor_id, str) or not anchor_id:
                issues.append(_error("anchor_id", "anchor_id must be a non-empty string.", f"{anchor_loc}.anchor_id"))

            what_changed = anchor.get("what_changed")
            if not isinstance(what_changed, str) or not what_changed.strip():
                issues.append(
                    _error(
                        "what_changed",
                        "what_changed must be a non-empty string.",
                        f"{anchor_loc}.what_changed",
                    )
                )

            why_changed = anchor.get("why_changed")
            if not isinstance(why_changed, str) or not why_changed.strip():
                issues.append(
                    _error(
                        "why_changed",
                        "why_changed must be a non-empty string.",
                        f"{anchor_loc}.why_changed",
                    )
                )

            if "title" in anchor and not isinstance(anchor.get("title"), str):
                issues.append(_error("title_type", "title must be a string.", f"{anchor_loc}.title"))

            if "reviewer_focus" in anchor and not isinstance(anchor.get("reviewer_focus"), str):
                issues.append(
                    _error(
                        "reviewer_focus_type",
                        "reviewer_focus must be a string.",
                        f"{anchor_loc}.reviewer_focus",
                    )
                )

            if "risk" in anchor and not isinstance(anchor.get("risk"), str):
                issues.append(_error("risk_type", "risk must be a string.", f"{anchor_loc}.risk"))

            severity = anchor.get("severity")
            if severity is not None and severity not in _ALLOWED_SEVERITIES:
                issues.append(
                    _error(
                        "bad_severity",
                        "severity must be one of info, note, warning, or risk.",
                        f"{anchor_loc}.severity",
                    )
                )

    return issues


def iter_file_anchors(file_annotation: dict[str, Any]) -> Iterable[dict[str, Any]]:
    anchors = file_annotation.get("anchors", [])
    if isinstance(anchors, list):
        for anchor in anchors:
            if isinstance(anchor, dict):
                yield anchor
