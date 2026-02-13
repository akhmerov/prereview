from __future__ import annotations

from typing import Any


def _why_for_path(path: str) -> str:
    lowered = path.lower()
    if lowered.startswith("tests/"):
        return "to increase confidence and prevent regressions"
    if lowered.startswith("skills/"):
        return "to improve agent guidance and reviewer relevance"
    if lowered.endswith("prepare.py"):
        return "to improve review-context generation and reproducibility"
    if lowered.endswith("validate.py"):
        return "to ensure annotation/context consistency against current repository state"
    if lowered.endswith("renderer.py"):
        return "to improve readability and reviewer experience"
    if lowered.endswith("cli.py"):
        return "to expose workflow changes through the command interface"
    if lowered.endswith("annotations.py"):
        return "to align annotation schema with reviewer-focused fields"
    if lowered.endswith("draft.py"):
        return "to produce clearer, lower-noise reviewer summaries"
    return "to improve maintainability and review clarity"


def _focus_sentence(anchor: dict[str, Any]) -> str:
    snippets = anchor.get("focus_snippets", [])
    if isinstance(snippets, list):
        cleaned = [str(item).strip() for item in snippets if isinstance(item, str) and item.strip()]
    else:
        cleaned = []
    if not cleaned:
        return "localized logic in this change focus"
    return "; ".join(f"`{item}`" for item in cleaned[:3])


def draft_annotations(context: dict[str, Any]) -> dict[str, Any]:
    context_id = context.get("context_id")
    if not isinstance(context_id, str) or not context_id:
        raise ValueError("Context is missing context_id.")

    stats = context.get("stats", {})
    files_changed = int(stats.get("files_changed", 0))
    additions = int(stats.get("additions", 0))
    deletions = int(stats.get("deletions", 0))

    annotations: dict[str, Any] = {
        "version": "2",
        "target_context_id": context_id,
        "overview": [
            f"Scope: {files_changed} file(s), +{additions}/-{deletions} lines changed.",
            "Primary intent: summarize what changed and why in reviewer-relevant terms.",
            "Reviewer focus: verify behavioral impact and high-risk assumptions in each change focus.",
        ],
        "files": [],
    }

    for file_entry in context.get("files", []):
        if not isinstance(file_entry, dict):
            continue
        path = file_entry.get("path")
        if not isinstance(path, str) or not path:
            continue

        status = str(file_entry.get("status", "modified"))
        why = _why_for_path(path)

        file_annotation: dict[str, Any] = {
            "path": path,
            "summary": f"What changed: {status} file with focused updates. Why: {why}.",
            "anchors": [],
        }

        for anchor in file_entry.get("anchors", []):
            if not isinstance(anchor, dict):
                continue
            anchor_id = anchor.get("anchor_id")
            if not isinstance(anchor_id, str) or not anchor_id:
                continue

            focus_sentence = _focus_sentence(anchor)
            risk_hint = anchor.get("risk_hint")
            has_risk = isinstance(risk_hint, str) and risk_hint.strip()

            anchor_annotation: dict[str, Any] = {
                "anchor_id": anchor_id,
                "title": anchor.get("title") or "Review focus",
                "what_changed": f"this change focus updates behavior around {focus_sentence}",
                "why_changed": why,
                "reviewer_focus": "confirm expected behavior and edge-case handling",
                "severity": "warning" if has_risk else "note",
            }
            if has_risk:
                anchor_annotation["risk"] = str(risk_hint)

            file_annotation["anchors"].append(anchor_annotation)

        annotations["files"].append(file_annotation)

    return annotations
