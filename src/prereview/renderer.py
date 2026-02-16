from __future__ import annotations

import html
import json
from importlib import resources
from typing import Any

from jinja2 import Environment


def _line_number(value: int | None) -> str:
    # Diff rows are side-specific: added lines have no old number and deleted
    # lines have no new number.
    return "" if value is None else str(value)


def _hunk_range(start: int, count: int, prefix: str) -> str:
    span = count
    if span < 1:
        span = 1
    end = start + span - 1
    if end == start:
        return f"{prefix}{start}"
    return f"{prefix}{start}-{end}"


def _normalize_file_summary(path: str, summary: str | None) -> str | None:
    if summary is None:
        return None
    text = summary.strip()
    if not text:
        return None

    candidates = [path, path.split("/")[-1]]
    lower_text = text.lower()
    for candidate in candidates:
        token = candidate.strip()
        if not token:
            continue
        lower_token = token.lower()
        if lower_text == lower_token:
            return None
        if lower_text.startswith(lower_token):
            remainder = text[len(token) :].lstrip(" \t:-|")
            return remainder or None
    return text


def _json_for_html_script(payload: Any) -> str:
    # Avoid closing script tags from embedded JSON text.
    return json.dumps(payload, sort_keys=True).replace("</", "<\\/")


def _comments_by_line(
    file_annotation: dict[str, Any],
) -> dict[int, list[dict[str, Any]]]:
    by_line: dict[int, list[dict[str, Any]]] = {}

    def add_comment(comment: dict[str, Any]) -> None:
        start = comment["line_start"]
        by_line.setdefault(start, []).append(comment)

    for comment in file_annotation["comments"]:
        add_comment(comment)

    for hunk in file_annotation["hunks"]:
        for comment in hunk["comments"]:
            add_comment(comment)

    return by_line


def _hunk_annotations(
    file_annotation: dict[str, Any],
    hunk: dict[str, Any],
    allow_split_hunks: bool,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    hunk_id = hunk["hunk_id"]
    hunk_start = hunk["new_start"]
    hunk_end = hunk_start + max(hunk["new_count"] - 1, 0)

    for hunk_annotation in file_annotation["hunks"]:
        if hunk_id and hunk_annotation["hunk_id"] == hunk_id:
            selected.append(hunk_annotation)
            continue
        if not allow_split_hunks:
            continue

        new_start = hunk_annotation["new_start"]
        new_end = hunk_annotation["new_end"]
        if new_end < new_start:
            new_start, new_end = new_end, new_start
        if new_start <= hunk_end and new_end >= hunk_start:
            selected.append(hunk_annotation)

    return selected


_TEMPLATE_ENV = Environment(autoescape=True, trim_blocks=True, lstrip_blocks=True)


_TEMPLATE_TEXT = (
    resources.files("prereview")
    .joinpath("templates/review.html.j2")
    .read_text(encoding="utf-8")
)
_TEMPLATE = _TEMPLATE_ENV.from_string(_TEMPLATE_TEXT)


def render_html(
    prepared: dict[str, Any],
    annotations: dict[str, Any],
    validation_report: dict[str, Any],
    *,
    title: str,
    max_expanded_lines: int,
    collapse_large_hunks: bool,
    allow_split_hunks: bool,
    embedded_data: dict[str, Any] | None = None,
) -> str:
    prepared_stats = prepared["stats"]
    files = prepared["files"]
    overview = annotations["overview"]

    file_annotations: dict[str, dict[str, Any]] = {}
    for file_annotation in annotations["files"]:
        file_annotations[file_annotation["path"]] = file_annotation

    issues = validation_report["issues"]
    error_count = sum(1 for issue in issues if issue["level"] == "error")
    warning_count = sum(1 for issue in issues if issue["level"] == "warning")

    overview_lines = [line for line in overview if line.strip()][:8]

    issues_render: list[dict[str, str]] = []
    for issue in issues[:25]:
        issues_render.append(
            {
                "level": issue["level"],
                "code": issue["code"],
                "message": issue["message"],
                "location": issue["location"],
            }
        )
    issues_extra_count = max(len(issues) - 25, 0)

    files_render: list[dict[str, Any]] = []
    for file_index, file_entry in enumerate(files, start=1):
        path = file_entry["path"]
        file_annotation = file_annotations[path]

        path_parts = [part for part in path.split("/") if part]
        file_name = path_parts[-1] if path_parts else path
        file_dir = "/".join(path_parts[:-1])

        file_anchor_id = f"file-{file_index}"
        file_view: dict[str, Any] = {
            "anchor_id": file_anchor_id,
            "toc_label": path,
            "path": path,
            "status": file_entry["status"],
            "file_dir": file_dir,
            "file_name": file_name,
            "summary_text": _normalize_file_summary(path, file_annotation["summary"]),
            "is_binary": file_entry["is_binary"],
            "hunks": [],
        }

        if file_view["is_binary"]:
            files_render.append(file_view)
            continue

        comments_by_line = _comments_by_line(file_annotation)
        for hunk_index, hunk in enumerate(file_entry["hunks"], start=1):
            hunk_annotations = _hunk_annotations(
                file_annotation, hunk, allow_split_hunks
            )
            lines_list = hunk["lines"]

            is_open = not (
                collapse_large_hunks and len(lines_list) > max_expanded_lines
            )

            added_lines = sum(1 for line in lines_list if line["type"] == "add")
            removed_lines = sum(1 for line in lines_list if line["type"] == "del")

            new_range = _hunk_range(hunk["new_start"], hunk["new_count"], "+")
            old_range = _hunk_range(hunk["old_start"], hunk["old_count"], "-")
            summary_label = f"Change {new_range} (from {old_range})"
            for hunk_annotation in hunk_annotations:
                title_text = hunk_annotation["title"]
                if isinstance(title_text, str) and title_text.strip():
                    summary_label = title_text.strip()
                    break

            notes: list[dict[str, str]] = []
            for hunk_annotation in hunk_annotations:
                note_fields = hunk_annotation["note_fields"]
                if note_fields:
                    structured_note: dict[str, str] = {}
                    for field in (
                        "what_changed",
                        "why_changed",
                        "reviewer_focus",
                        "risk",
                    ):
                        value = (
                            note_fields[field].strip() if field in note_fields else ""
                        )
                        if value:
                            structured_note[field] = value
                    if structured_note:
                        notes.append(structured_note)
                        continue

                explanation = hunk_annotation["explanation"]
                if isinstance(explanation, str) and explanation.strip():
                    notes.append({"explanation": explanation.strip()})

            new_start = hunk["new_start"]
            span = hunk["new_count"]
            if span < 1:
                span = 1
            new_end = new_start + span - 1

            rows: list[dict[str, Any]] = []
            for line in lines_list:
                line_type = line["type"]
                class_name = ""
                symbol = " "
                if line_type == "add":
                    class_name = "line-add"
                    symbol = "+"
                elif line_type == "del":
                    class_name = "line-del"
                    symbol = "-"

                rows.append(
                    {
                        "kind": "line",
                        "class_name": class_name,
                        "symbol": symbol,
                        "old_no": _line_number(line["old_line"]),
                        "new_no": _line_number(line["new_line"]),
                        "old_line": line["old_line"],
                        "new_line": line["new_line"],
                        "raw_content": line["content"],
                        "content": html.escape(line["content"], quote=True),
                    }
                )

                new_line = line["new_line"]
                if new_line in comments_by_line:
                    for comment in comments_by_line[new_line]:
                        severity = comment["severity"].strip().lower() or "info"
                        rows.append(
                            {
                                "kind": "comment",
                                "severity": severity,
                                "text": comment["text"],
                            }
                        )

            file_view["hunks"].append(
                {
                    "anchor_id": f"{file_anchor_id}-hunk-{hunk_index}",
                    "hunk_id": hunk["hunk_id"],
                    "new_start": new_start,
                    "new_end": new_end,
                    "is_open": is_open,
                    "summary_label": summary_label,
                    "added_lines": added_lines,
                    "removed_lines": removed_lines,
                    "notes": notes,
                    "rows": rows,
                }
            )

        files_render.append(file_view)

    embedded_json = (
        _json_for_html_script(embedded_data) if embedded_data is not None else None
    )

    return _TEMPLATE.render(
        title=title,
        files_changed=prepared_stats["files_changed"],
        additions=prepared_stats["additions"],
        deletions=prepared_stats["deletions"],
        error_count=error_count,
        warning_count=warning_count,
        overview_lines=overview_lines,
        issues_render=issues_render,
        issues_extra_count=issues_extra_count,
        files_render=files_render,
        embedded_json=embedded_json,
    )
