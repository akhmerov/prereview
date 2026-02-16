from __future__ import annotations

from collections import defaultdict
from typing import Any

from prereview.annotations import iter_file_anchors, validate_annotation_schema
from prereview.prepare import recompute_runtime_from_context


def _issue(level: str, code: str, message: str, location: str) -> dict[str, str]:
    return {"level": level, "code": code, "message": message, "location": location}


def _ensure_terminal_punctuation(text: str) -> str:
    trimmed = text.strip()
    if not trimmed:
        return trimmed
    if trimmed.endswith(("...", "…", ".", "!", "?")):
        return trimmed
    return f"{trimmed}."


def evaluate_annotations(
    context: Any,
    annotations: Any,
    *,
    strict: bool,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    issues: list[dict[str, str]] = []
    issues.extend(validate_annotation_schema(annotations))

    if not isinstance(context, dict):
        issues.append(
            _issue("error", "context_type", "Context must be a JSON object.", "$")
        )
        report = {
            "valid": False,
            "issues": issues,
            "stats": {
                "mapped_anchors": 0,
                "unmapped_anchors": 0,
                "files_with_annotations": 0,
            },
        }
        return report, None

    context_id = context.get("context_id")
    target = (
        annotations.get("target_context_id") if isinstance(annotations, dict) else None
    )
    if isinstance(context_id, str) and isinstance(target, str) and target != context_id:
        issues.append(
            _issue(
                "error",
                "context_mismatch",
                "target_context_id does not match context_id.",
                "$.target_context_id",
            )
        )

    runtime: dict[str, Any] | None = None
    try:
        runtime = recompute_runtime_from_context(context)
    except RuntimeError as exc:
        issues.append(
            _issue(
                "error",
                "runtime_recompute_failed",
                f"Failed to recompute diff from source_spec: {exc}",
                "$.source_spec",
            )
        )

    if runtime is not None:
        expected_fingerprint = context.get("diff_fingerprint")
        actual_fingerprint = runtime["diff_fingerprint"]
        if (
            isinstance(expected_fingerprint, str)
            and expected_fingerprint != actual_fingerprint
        ):
            issues.append(
                _issue(
                    "error",
                    "context_stale",
                    "Current diff fingerprint does not match context; regenerate context before validating/building.",
                    "$.diff_fingerprint",
                )
            )

    mapped_anchors = 0
    unmapped_anchors = 0
    files_with_annotations = 0

    if (
        runtime is not None
        and isinstance(annotations, dict)
        and isinstance(annotations.get("files"), list)
    ):
        runtime_file_paths = {
            file_entry.path: file_entry for file_entry in runtime["files"]
        }
        anchor_index = runtime["anchor_index"]

        for file_idx, file_annotation in enumerate(annotations["files"]):
            if not isinstance(file_annotation, dict):
                continue
            files_with_annotations += 1
            path = file_annotation.get("path")
            location = f"$.files[{file_idx}]"
            if not isinstance(path, str):
                continue

            if path not in runtime_file_paths:
                level = "error" if strict else "warning"
                issues.append(
                    _issue(
                        level,
                        "unknown_file",
                        f"Annotation file path {path!r} not found in recomputed diff.",
                        f"{location}.path",
                    )
                )
                continue

            file_anchor_index = anchor_index[path]
            for anchor_idx, anchor in enumerate(iter_file_anchors(file_annotation)):
                anchor_id = anchor.get("anchor_id")
                if not isinstance(anchor_id, str):
                    continue
                if anchor_id not in file_anchor_index:
                    unmapped_anchors += 1
                    level = "error" if strict else "warning"
                    issues.append(
                        _issue(
                            level,
                            "unknown_anchor",
                            f"anchor_id {anchor_id!r} was not found for file {path!r}.",
                            f"{location}.anchors[{anchor_idx}].anchor_id",
                        )
                    )
                else:
                    mapped_anchors += 1

    if strict:
        for issue in issues:
            if issue["level"] == "warning":
                issue["level"] = "error"

    report = {
        "valid": not any(issue["level"] == "error" for issue in issues),
        "issues": issues,
        "stats": {
            "mapped_anchors": mapped_anchors,
            "unmapped_anchors": unmapped_anchors,
            "files_with_annotations": files_with_annotations,
        },
    }
    return report, runtime


def materialize_annotations_for_render(
    runtime: dict[str, Any],
    annotations: dict[str, Any],
) -> dict[str, Any]:
    runtime_files = runtime["files"]
    anchor_index = runtime["anchor_index"]

    annotations_by_file: dict[str, dict[str, Any]] = {}
    for file_annotation in annotations["files"]:
        path = file_annotation["path"]
        annotations_by_file[path] = {
            "path": path,
            "breadcrumbs": (
                file_annotation["breadcrumbs"]
                if "breadcrumbs" in file_annotation
                else path.split("/")
            ),
            "summary": file_annotation["summary"]
            if "summary" in file_annotation
            else None,
            "anchors": file_annotation["anchors"],
        }

    render_files: list[dict[str, Any]] = []
    for runtime_file in runtime_files:
        path = runtime_file.path
        file_annotation = (
            annotations_by_file[path]
            if path in annotations_by_file
            else {"breadcrumbs": path.split("/"), "summary": None, "anchors": []}
        )
        render_file = {
            "path": path,
            "breadcrumbs": file_annotation["breadcrumbs"],
            "summary": file_annotation["summary"],
            "comments": [],
            "hunks": [],
        }

        per_file_anchor_index = anchor_index[path]
        for anchor in iter_file_anchors(file_annotation):
            anchor_id = anchor["anchor_id"]
            if not isinstance(anchor_id, str):
                continue
            if anchor_id not in per_file_anchor_index:
                continue
            resolved = per_file_anchor_index[anchor_id]

            what_changed = anchor["what_changed"].strip()
            why_changed = anchor["why_changed"].strip()
            reviewer_focus = (
                anchor["reviewer_focus"].strip() if "reviewer_focus" in anchor else ""
            )
            risk = anchor["risk"].strip() if "risk" in anchor else ""
            severity = anchor["severity"] if "severity" in anchor else "note"

            note_fields = {
                "what_changed": _ensure_terminal_punctuation(what_changed),
                "why_changed": _ensure_terminal_punctuation(why_changed),
            }
            if reviewer_focus:
                note_fields["reviewer_focus"] = _ensure_terminal_punctuation(
                    reviewer_focus
                )
            if risk:
                note_fields["risk"] = _ensure_terminal_punctuation(risk)

            hunk_annotation = {
                "hunk_id": resolved["hunk_id"],
                "new_start": resolved["new_start"],
                "new_end": resolved["new_end"],
                "title": (anchor["title"] if "title" in anchor else "")
                or "Review focus",
                "note_fields": note_fields,
                # Keep legacy flattened explanation for compatibility with older renderers/tests.
                "explanation": " ".join(
                    [
                        f"What changed: {note_fields['what_changed']}",
                        f"Why: {note_fields['why_changed']}",
                        *(
                            [f"Reviewer focus: {note_fields['reviewer_focus']}"]
                            if "reviewer_focus" in note_fields
                            else []
                        ),
                        *(
                            [f"Risk: {note_fields['risk']}"]
                            if "risk" in note_fields
                            else []
                        ),
                    ]
                ),
                "comments": [],
            }

            # Keep line-level notes rare: only materialize for warning/risk anchors.
            anchor_line = resolved["anchor_line"]
            if isinstance(anchor_line, int) and severity in {"warning", "risk"}:
                text_bits = []
                if reviewer_focus:
                    text_bits.append(
                        f"Reviewer focus: {_ensure_terminal_punctuation(reviewer_focus)}"
                    )
                if risk:
                    text_bits.append(f"Risk: {_ensure_terminal_punctuation(risk)}")
                if text_bits:
                    hunk_annotation["comments"].append(
                        {
                            "line_start": anchor_line,
                            "text": " ".join(text_bits),
                            "severity": severity,
                            "author": "prereview",
                        }
                    )

            render_file["hunks"].append(hunk_annotation)

        render_files.append(render_file)

    return {
        "version": "render-2",
        "overview": annotations["overview"],
        "files": render_files,
    }


def grouped_issues(report: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for issue in report["issues"]:
        grouped[issue["level"]].append(issue)
    return dict(grouped)
