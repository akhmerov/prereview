from __future__ import annotations

from collections import defaultdict
from typing import Any

from prereview.annotations import iter_file_anchors, validate_annotation_schema
from prereview.prepare import recompute_runtime_from_context


def _issue(level: str, code: str, message: str, location: str) -> dict[str, str]:
    return {"level": level, "code": code, "message": message, "location": location}


def evaluate_annotations(
    context: Any,
    annotations: Any,
    *,
    strict: bool,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    issues: list[dict[str, str]] = []
    issues.extend(validate_annotation_schema(annotations))

    if not isinstance(context, dict):
        issues.append(_issue("error", "context_type", "Context must be a JSON object.", "$"))
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
    target = annotations.get("target_context_id") if isinstance(annotations, dict) else None
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
    except Exception as exc:  # noqa: BLE001
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
        actual_fingerprint = runtime.get("diff_fingerprint")
        if isinstance(expected_fingerprint, str) and expected_fingerprint != actual_fingerprint:
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

    if runtime is not None and isinstance(annotations, dict) and isinstance(annotations.get("files"), list):
        runtime_file_paths = {str(file_entry.get("path")): file_entry for file_entry in runtime.get("files", []) if isinstance(file_entry, dict)}
        anchor_index = runtime.get("anchor_index", {})

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

            file_anchor_index = anchor_index.get(path, {}) if isinstance(anchor_index, dict) else {}
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


def validate_annotations(
    context: Any,
    annotations: Any,
    *,
    strict: bool,
) -> dict[str, Any]:
    report, _ = evaluate_annotations(context, annotations, strict=strict)
    return report


def materialize_annotations_for_render(
    runtime: dict[str, Any],
    annotations: dict[str, Any],
) -> dict[str, Any]:
    runtime_files = runtime.get("files", [])
    anchor_index = runtime.get("anchor_index", {})

    annotations_by_file: dict[str, dict[str, Any]] = {}
    for file_annotation in annotations.get("files", []):
        if isinstance(file_annotation, dict) and isinstance(file_annotation.get("path"), str):
            annotations_by_file[file_annotation["path"]] = file_annotation

    render_files: list[dict[str, Any]] = []
    for runtime_file in runtime_files:
        if not isinstance(runtime_file, dict):
            continue
        path = runtime_file.get("path")
        if not isinstance(path, str):
            continue

        file_annotation = annotations_by_file.get(path, {})
        render_file = {
            "path": path,
            "breadcrumbs": file_annotation.get("breadcrumbs", path.split("/")),
            "summary": file_annotation.get("summary"),
            "comments": [],
            "hunks": [],
        }

        per_file_anchor_index = anchor_index.get(path, {}) if isinstance(anchor_index, dict) else {}
        for anchor in iter_file_anchors(file_annotation):
            anchor_id = anchor.get("anchor_id")
            if not isinstance(anchor_id, str):
                continue
            resolved = per_file_anchor_index.get(anchor_id)
            if not isinstance(resolved, dict):
                continue

            what_changed = str(anchor.get("what_changed", "")).strip()
            why_changed = str(anchor.get("why_changed", "")).strip()
            reviewer_focus = str(anchor.get("reviewer_focus", "")).strip()
            risk = str(anchor.get("risk", "")).strip()
            severity = str(anchor.get("severity", "note"))

            explanation_parts = [f"What changed: {what_changed}.", f"Why: {why_changed}."]
            if reviewer_focus:
                explanation_parts.append(f"Reviewer focus: {reviewer_focus}.")
            if risk:
                explanation_parts.append(f"Risk: {risk}.")

            hunk_annotation = {
                "hunk_id": resolved.get("hunk_id"),
                "new_start": resolved.get("new_start"),
                "new_end": resolved.get("new_end"),
                "title": anchor.get("title") or "Review focus",
                "explanation": " ".join(explanation_parts),
                "comments": [],
            }

            # Keep line-level notes rare: only materialize for warning/risk anchors.
            anchor_line = resolved.get("anchor_line")
            if isinstance(anchor_line, int) and severity in {"warning", "risk"}:
                text_bits = []
                if reviewer_focus:
                    text_bits.append(f"Reviewer focus: {reviewer_focus}.")
                if risk:
                    text_bits.append(f"Risk: {risk}.")
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
        "overview": annotations.get("overview", []),
        "files": render_files,
    }


def grouped_issues(report: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for issue in report.get("issues", []):
        grouped[str(issue.get("level", "warning"))].append(issue)
    return dict(grouped)
