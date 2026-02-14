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
        issues.append(
            _error("bad_version", "version must be the string '2'.", "$.version")
        )

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
        if not isinstance(overview, list) or not all(
            isinstance(line, str) and line.strip() for line in overview
        ):
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
            issues.append(
                _error("file_type", "Each files entry must be an object.", location)
            )
            continue

        path = file_entry.get("path")
        if not isinstance(path, str) or not path:
            issues.append(
                _error(
                    "file_path",
                    "files.path must be a non-empty string.",
                    f"{location}.path",
                )
            )

        if "summary" in file_entry and not isinstance(file_entry["summary"], str):
            issues.append(
                _error(
                    "summary_type", "summary must be a string.", f"{location}.summary"
                )
            )

        anchors = file_entry.get("anchors")
        if not isinstance(anchors, list):
            issues.append(
                _error(
                    "anchors_type",
                    "files.anchors must be a list.",
                    f"{location}.anchors",
                )
            )
            continue

        for anchor_idx, anchor in enumerate(anchors):
            anchor_loc = f"{location}.anchors[{anchor_idx}]"
            if not isinstance(anchor, dict):
                issues.append(
                    _error("anchor_type", "Anchor must be an object.", anchor_loc)
                )
                continue

            anchor_id = anchor.get("anchor_id")
            if not isinstance(anchor_id, str) or not anchor_id:
                issues.append(
                    _error(
                        "anchor_id",
                        "anchor_id must be a non-empty string.",
                        f"{anchor_loc}.anchor_id",
                    )
                )

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
                issues.append(
                    _error(
                        "title_type", "title must be a string.", f"{anchor_loc}.title"
                    )
                )

            if "reviewer_focus" in anchor and not isinstance(
                anchor.get("reviewer_focus"), str
            ):
                issues.append(
                    _error(
                        "reviewer_focus_type",
                        "reviewer_focus must be a string.",
                        f"{anchor_loc}.reviewer_focus",
                    )
                )

            if "risk" in anchor and not isinstance(anchor.get("risk"), str):
                issues.append(
                    _error("risk_type", "risk must be a string.", f"{anchor_loc}.risk")
                )

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


def validate_annotation_notes_schema(notes: Any) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    if not isinstance(notes, dict):
        return [_error("root_type", "Annotation notes must be a JSON object.", "$")]

    if notes.get("version") != "1":
        issues.append(
            _error("bad_version", "version must be the string '1'.", "$.version")
        )

    target = notes.get("target_context_id")
    if not isinstance(target, str) or not target:
        issues.append(
            _error(
                "missing_target",
                "target_context_id must be a non-empty string.",
                "$.target_context_id",
            )
        )

    overview = notes.get("overview")
    if overview is not None:
        if not isinstance(overview, list) or not all(
            isinstance(line, str) and line.strip() for line in overview
        ):
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

    anchors = notes.get("anchors")
    if not isinstance(anchors, list):
        issues.append(_error("anchors_type", "anchors must be a list.", "$.anchors"))
        return issues

    seen_anchor_ids: set[str] = set()
    for anchor_idx, anchor in enumerate(anchors):
        anchor_loc = f"$.anchors[{anchor_idx}]"
        if not isinstance(anchor, dict):
            issues.append(
                _error("anchor_type", "Anchor note must be an object.", anchor_loc)
            )
            continue

        anchor_id = anchor.get("anchor_id")
        if not isinstance(anchor_id, str) or not anchor_id:
            issues.append(
                _error(
                    "anchor_id",
                    "anchor_id must be a non-empty string.",
                    f"{anchor_loc}.anchor_id",
                )
            )
        elif anchor_id in seen_anchor_ids:
            issues.append(
                _error(
                    "duplicate_anchor_id",
                    f"anchor_id {anchor_id!r} is duplicated in notes.",
                    f"{anchor_loc}.anchor_id",
                )
            )
        else:
            seen_anchor_ids.add(anchor_id)

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
            issues.append(
                _error("title_type", "title must be a string.", f"{anchor_loc}.title")
            )

        if "reviewer_focus" in anchor and not isinstance(
            anchor.get("reviewer_focus"), str
        ):
            issues.append(
                _error(
                    "reviewer_focus_type",
                    "reviewer_focus must be a string.",
                    f"{anchor_loc}.reviewer_focus",
                )
            )

        if "risk" in anchor and not isinstance(anchor.get("risk"), str):
            issues.append(
                _error("risk_type", "risk must be a string.", f"{anchor_loc}.risk")
            )

        severity = anchor.get("severity")
        if severity is not None and severity not in _ALLOWED_SEVERITIES:
            issues.append(
                _error(
                    "bad_severity",
                    "severity must be one of info, note, warning, or risk.",
                    f"{anchor_loc}.severity",
                )
            )

    file_summaries = notes.get("file_summaries")
    if file_summaries is not None:
        if not isinstance(file_summaries, list):
            issues.append(
                _error(
                    "file_summaries_type",
                    "file_summaries must be a list.",
                    "$.file_summaries",
                )
            )
        else:
            for summary_idx, summary_entry in enumerate(file_summaries):
                location = f"$.file_summaries[{summary_idx}]"
                if not isinstance(summary_entry, dict):
                    issues.append(
                        _error(
                            "file_summary_type",
                            "file summary must be an object.",
                            location,
                        )
                    )
                    continue
                path = summary_entry.get("path")
                if not isinstance(path, str) or not path:
                    issues.append(
                        _error(
                            "file_summary_path",
                            "file_summaries.path must be a non-empty string.",
                            f"{location}.path",
                        )
                    )
                summary = summary_entry.get("summary")
                if not isinstance(summary, str) or not summary.strip():
                    issues.append(
                        _error(
                            "file_summary_text",
                            "file_summaries.summary must be a non-empty string.",
                            f"{location}.summary",
                        )
                    )

    return issues


def compile_annotations_from_notes(
    context: Any,
    notes: Any,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    issues: list[dict[str, str]] = []
    issues.extend(validate_annotation_notes_schema(notes))

    notes_obj = notes if isinstance(notes, dict) else {}
    context_obj = context if isinstance(context, dict) else {}

    file_order: list[str] = []
    known_paths: set[str] = set()
    anchor_to_path: dict[str, str] = {}
    anchor_default_title: dict[str, str] = {}

    for file_entry in context_obj.get("files", []):
        if not isinstance(file_entry, dict):
            continue
        path = file_entry.get("path")
        if not isinstance(path, str) or not path:
            continue
        file_order.append(path)
        known_paths.add(path)
        for anchor in file_entry.get("anchors", []):
            if not isinstance(anchor, dict):
                continue
            anchor_id = anchor.get("anchor_id")
            if not isinstance(anchor_id, str) or not anchor_id:
                continue
            anchor_to_path[anchor_id] = path
            title = anchor.get("title")
            if isinstance(title, str) and title.strip():
                anchor_default_title[anchor_id] = title

    anchors_by_file: dict[str, list[dict[str, Any]]] = {}
    notes_anchors = notes_obj.get("anchors")
    if isinstance(notes_anchors, list):
        for anchor_idx, anchor_note in enumerate(notes_anchors):
            if not isinstance(anchor_note, dict):
                continue
            anchor_id = anchor_note.get("anchor_id")
            if not isinstance(anchor_id, str) or not anchor_id:
                continue

            path = anchor_to_path.get(anchor_id)
            if path is None:
                issues.append(
                    _error(
                        "unknown_anchor",
                        f"anchor_id {anchor_id!r} was not found in context.",
                        f"$.anchors[{anchor_idx}].anchor_id",
                    )
                )
                continue

            compiled_anchor: dict[str, Any] = {
                "anchor_id": anchor_id,
                "what_changed": str(anchor_note.get("what_changed", "")).strip(),
                "why_changed": str(anchor_note.get("why_changed", "")).strip(),
            }

            title = anchor_note.get("title")
            if isinstance(title, str) and title.strip():
                compiled_anchor["title"] = title.strip()
            elif anchor_id in anchor_default_title:
                compiled_anchor["title"] = anchor_default_title[anchor_id]

            reviewer_focus = anchor_note.get("reviewer_focus")
            if isinstance(reviewer_focus, str) and reviewer_focus.strip():
                compiled_anchor["reviewer_focus"] = reviewer_focus.strip()

            risk = anchor_note.get("risk")
            if isinstance(risk, str) and risk.strip():
                compiled_anchor["risk"] = risk.strip()

            severity = anchor_note.get("severity")
            if isinstance(severity, str) and severity.strip():
                compiled_anchor["severity"] = severity.strip()

            anchors_by_file.setdefault(path, []).append(compiled_anchor)

    summary_by_path: dict[str, str] = {}
    file_summaries = notes_obj.get("file_summaries")
    if isinstance(file_summaries, list):
        for summary_idx, summary_entry in enumerate(file_summaries):
            if not isinstance(summary_entry, dict):
                continue
            path = summary_entry.get("path")
            summary = summary_entry.get("summary")
            if not isinstance(path, str) or not path:
                continue
            if path not in known_paths:
                issues.append(
                    _error(
                        "unknown_file",
                        f"file summary path {path!r} was not found in context.",
                        f"$.file_summaries[{summary_idx}].path",
                    )
                )
                continue
            if isinstance(summary, str) and summary.strip():
                summary_by_path[path] = summary.strip()

    annotation_files: list[dict[str, Any]] = []
    for path in file_order:
        anchors = anchors_by_file.get(path, [])
        summary = summary_by_path.get(path)
        if not anchors and summary is None:
            continue

        file_entry: dict[str, Any] = {
            "path": path,
            "anchors": anchors,
        }
        if summary is not None:
            file_entry["summary"] = summary
        annotation_files.append(file_entry)

    overview = notes_obj.get("overview")
    compiled: dict[str, Any] = {
        "version": "2",
        "target_context_id": notes_obj.get("target_context_id", ""),
        "overview": overview if isinstance(overview, list) else [],
        "files": annotation_files,
    }
    return compiled, issues
