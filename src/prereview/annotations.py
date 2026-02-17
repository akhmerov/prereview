from __future__ import annotations

from typing import Any

from prereview.models import Severity


def _error(code: str, message: str, location: str) -> dict[str, str]:
    return {"level": "error", "code": code, "message": message, "location": location}


def _warning(code: str, message: str, location: str) -> dict[str, str]:
    return {"level": "warning", "code": code, "message": message, "location": location}


def _is_non_empty_string(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _coerce_severity(value: object) -> Severity | None:
    try:
        return Severity(value.strip())
    except (AttributeError, ValueError):
        return None


def _validate_overview_field(
    overview: object,
    *,
    issues: list[dict[str, str]],
    location: str,
) -> None:
    if overview is None:
        return
    if not isinstance(overview, list) or not all(
        isinstance(line, str) and line.strip() for line in overview
    ):
        issues.append(
            _error(
                "overview_type",
                "overview must be a list of non-empty strings.",
                location,
            )
        )
        return
    if len(overview) > 8:
        issues.append(
            _warning(
                "overview_length",
                "overview should typically be 2-5 lines for reviewer readability.",
                location,
            )
        )


def _validate_anchor_fields(
    anchor: dict[str, Any],
    *,
    location: str,
    issues: list[dict[str, str]],
) -> None:
    if not _is_non_empty_string(anchor.get("anchor_id")):
        issues.append(
            _error(
                "anchor_id",
                "anchor_id must be a non-empty string.",
                f"{location}.anchor_id",
            )
        )

    if not _is_non_empty_string(anchor.get("what_changed")):
        issues.append(
            _error(
                "what_changed",
                "what_changed must be a non-empty string.",
                f"{location}.what_changed",
            )
        )

    if not _is_non_empty_string(anchor.get("why_changed")):
        issues.append(
            _error(
                "why_changed",
                "why_changed must be a non-empty string.",
                f"{location}.why_changed",
            )
        )

    if "title" in anchor and not isinstance(anchor.get("title"), str):
        issues.append(
            _error("title_type", "title must be a string.", f"{location}.title")
        )

    if "reviewer_focus" in anchor and not isinstance(anchor.get("reviewer_focus"), str):
        issues.append(
            _error(
                "reviewer_focus_type",
                "reviewer_focus must be a string.",
                f"{location}.reviewer_focus",
            )
        )

    if "risk" in anchor and not isinstance(anchor.get("risk"), str):
        issues.append(_error("risk_type", "risk must be a string.", f"{location}.risk"))

    severity = _coerce_severity(anchor.get("severity", Severity.NOTE.value))
    if severity is None:
        issues.append(
            _error(
                "bad_severity",
                "severity must be one of info, note, warning, or risk.",
                f"{location}.severity",
            )
        )


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

    _validate_overview_field(
        notes.get("overview"), issues=issues, location="$.overview"
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
        if not _is_non_empty_string(anchor_id):
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

        _validate_anchor_fields(anchor, location=anchor_loc, issues=issues)

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

    if not isinstance(notes, dict) or not isinstance(context, dict):
        return {
            "version": "2",
            "target_context_id": "",
            "overview": [],
            "files": [],
        }, issues

    file_order: list[str] = []
    known_paths: set[str] = set()
    anchor_to_path: dict[str, str] = {}
    anchor_default_title: dict[str, str] = {}

    for file_entry in context["files"]:
        path = file_entry["path"]
        file_order.append(path)
        known_paths.add(path)
        for anchor in file_entry["anchors"]:
            anchor_id = anchor["anchor_id"]
            anchor_to_path[anchor_id] = path
            title = anchor["title"] if "title" in anchor else None
            if _is_non_empty_string(title):
                anchor_default_title[anchor_id] = title

    anchors_by_file: dict[str, list[dict[str, Any]]] = {}
    for anchor_idx, anchor_note in enumerate(notes["anchors"]):
        anchor_id = anchor_note["anchor_id"]
        severity = _coerce_severity(anchor_note.get("severity", Severity.NOTE.value))

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
        if severity is None:
            continue

        compiled_anchor: dict[str, Any] = {
            "anchor_id": anchor_id,
            "what_changed": anchor_note["what_changed"].strip(),
            "why_changed": anchor_note["why_changed"].strip(),
            "severity": severity.value,
        }

        title = anchor_note["title"] if "title" in anchor_note else None
        if _is_non_empty_string(title):
            compiled_anchor["title"] = title.strip()
        elif anchor_id in anchor_default_title:
            compiled_anchor["title"] = anchor_default_title[anchor_id]

        reviewer_focus = (
            anchor_note["reviewer_focus"] if "reviewer_focus" in anchor_note else None
        )
        if _is_non_empty_string(reviewer_focus):
            compiled_anchor["reviewer_focus"] = reviewer_focus.strip()

        risk = anchor_note["risk"] if "risk" in anchor_note else None
        if _is_non_empty_string(risk):
            compiled_anchor["risk"] = risk.strip()

        anchors_by_file.setdefault(path, []).append(compiled_anchor)

    summary_by_path: dict[str, str] = {}
    for summary_idx, summary_entry in enumerate(
        notes["file_summaries"] if "file_summaries" in notes else []
    ):
        path = summary_entry["path"]
        summary = summary_entry["summary"]
        if path not in known_paths:
            issues.append(
                _error(
                    "unknown_file",
                    f"file summary path {path!r} was not found in context.",
                    f"$.file_summaries[{summary_idx}].path",
                )
            )
            continue
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

    overview = notes.get("overview", [])
    compiled: dict[str, Any] = {
        "version": "2",
        "target_context_id": notes["target_context_id"],
        "overview": overview,
        "files": annotation_files,
    }
    return compiled, issues
