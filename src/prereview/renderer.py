from __future__ import annotations

import html
import json
from importlib import resources
from typing import Any

from jinja2 import Environment


def _line_number(value: Any) -> str:
    return "" if value is None else str(value)


def _hunk_range(start: Any, count: Any, prefix: str) -> str:
    if not isinstance(start, int):
        return "?"
    span = count if isinstance(count, int) else 1
    if span < 1:
        span = 1
    end = start + span - 1
    if end == start:
        return f"{prefix}{start}"
    return f"{prefix}{start}-{end}"


def _normalize_file_summary(path: str, summary: Any) -> str | None:
    if not isinstance(summary, str):
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


def _comment_range(comment: dict[str, Any]) -> tuple[int | None, int | None]:
    start = comment.get("line_start")
    end = comment.get("line_end", start)
    if isinstance(start, int) and isinstance(end, int):
        if end < start:
            return end, start
        return start, end
    return None, None


def _comments_by_line(file_annotation: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    by_line: dict[int, list[dict[str, Any]]] = {}

    def add_comment(comment: dict[str, Any], source: str) -> None:
        start, end = _comment_range(comment)
        if start is None or end is None:
            return
        comment_with_source = dict(comment)
        comment_with_source["_source"] = source
        by_line.setdefault(start, []).append(comment_with_source)

    for comment in file_annotation.get("comments", []):
        if isinstance(comment, dict):
            add_comment(comment, "file")

    for hunk in file_annotation.get("hunks", []):
        if not isinstance(hunk, dict):
            continue
        for comment in hunk.get("comments", []):
            if isinstance(comment, dict):
                add_comment(comment, "hunk")

    return by_line


def _hunk_annotations(
    file_annotation: dict[str, Any],
    hunk: dict[str, Any],
    allow_split_hunks: bool,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    hunk_id = hunk.get("hunk_id")
    hunk_start = hunk.get("new_start")
    hunk_end = None
    if isinstance(hunk_start, int):
        hunk_end = hunk_start + max(int(hunk.get("new_count", 1)) - 1, 0)

    for hunk_annotation in file_annotation.get("hunks", []):
        if not isinstance(hunk_annotation, dict):
            continue
        if hunk_id and hunk_annotation.get("hunk_id") == hunk_id:
            selected.append(hunk_annotation)
            continue
        if not allow_split_hunks:
            continue

        new_start = hunk_annotation.get("new_start")
        new_end = hunk_annotation.get("new_end")
        if isinstance(new_start, int) and isinstance(new_end, int) and isinstance(hunk_start, int) and isinstance(hunk_end, int):
            if new_end < new_start:
                new_start, new_end = new_end, new_start
            if new_start <= hunk_end and new_end >= hunk_start:
                selected.append(hunk_annotation)

    return selected


_TEMPLATE_ENV = Environment(autoescape=True, trim_blocks=True, lstrip_blocks=True)


_TEMPLATE_TEXT = resources.files("prereview").joinpath("templates/review.html.j2").read_text(encoding="utf-8")
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
    prepared_stats = prepared.get("stats", {})
    files = prepared.get("files", [])
    overview = annotations.get("overview", [])

    file_annotations: dict[str, dict[str, Any]] = {}
    for file_annotation in annotations.get("files", []):
        if isinstance(file_annotation, dict) and isinstance(file_annotation.get("path"), str):
            file_annotations[file_annotation["path"]] = file_annotation

    issues = validation_report.get("issues", [])
    error_count = sum(1 for issue in issues if isinstance(issue, dict) and issue.get("level") == "error")
    warning_count = sum(1 for issue in issues if isinstance(issue, dict) and issue.get("level") == "warning")

    overview_lines = []
    if isinstance(overview, list):
        overview_lines = [line for line in overview if isinstance(line, str) and line.strip()][:8]

    issues_render: list[dict[str, str]] = []
    for issue in issues[:25]:
        if not isinstance(issue, dict):
            continue
        issues_render.append(
            {
                "level": str(issue.get("level", "warning")),
                "code": str(issue.get("code", "issue")),
                "message": str(issue.get("message", "")),
                "location": str(issue.get("location", "")),
            }
        )
    issues_extra_count = max(len(issues) - 25, 0)

    files_render: list[dict[str, Any]] = []
    for file_index, file_entry in enumerate(files, start=1):
        if not isinstance(file_entry, dict):
            continue

        path = str(file_entry.get("path", "unknown"))
        file_annotation = file_annotations.get(path, {})

        path_parts = [part for part in path.split("/") if part]
        file_name = path_parts[-1] if path_parts else path
        file_dir = "/".join(path_parts[:-1])

        file_anchor_id = f"file-{file_index}"
        file_view: dict[str, Any] = {
            "anchor_id": file_anchor_id,
            "toc_label": path,
            "status": str(file_entry.get("status", "modified")),
            "file_dir": file_dir,
            "file_name": file_name,
            "summary_text": _normalize_file_summary(path, file_annotation.get("summary")),
            "is_binary": bool(file_entry.get("is_binary")),
            "hunks": [],
        }

        if file_view["is_binary"]:
            files_render.append(file_view)
            continue

        comments_by_line = _comments_by_line(file_annotation)
        for hunk_index, hunk in enumerate(file_entry.get("hunks", []), start=1):
            if not isinstance(hunk, dict):
                continue

            hunk_annotations = _hunk_annotations(file_annotation, hunk, allow_split_hunks)
            lines = hunk.get("lines", [])
            lines_list = lines if isinstance(lines, list) else []

            is_open = not bool(collapse_large_hunks and len(lines_list) > max_expanded_lines)

            added_lines = sum(
                1
                for line in lines_list
                if isinstance(line, dict) and line.get("type") == "add"
            )
            removed_lines = sum(
                1
                for line in lines_list
                if isinstance(line, dict) and line.get("type") == "del"
            )

            new_range = _hunk_range(hunk.get("new_start"), hunk.get("new_count"), "+")
            old_range = _hunk_range(hunk.get("old_start"), hunk.get("old_count"), "-")
            summary_label = f"Change {new_range} (from {old_range})"
            for hunk_annotation in hunk_annotations:
                title_text = hunk_annotation.get("title")
                if isinstance(title_text, str) and title_text.strip():
                    summary_label = title_text.strip()
                    break

            notes: list[dict[str, str]] = []
            for hunk_annotation in hunk_annotations:
                explanation = hunk_annotation.get("explanation")
                if isinstance(explanation, str) and explanation.strip():
                    notes.append({"explanation": explanation.strip()})

            rows: list[dict[str, str]] = []
            for line in lines_list:
                if not isinstance(line, dict):
                    continue

                line_type = str(line.get("type", "context"))
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
                        "old_no": _line_number(line.get("old_line")),
                        "new_no": _line_number(line.get("new_line")),
                        "content": html.escape(str(line.get("content", "")), quote=True),
                    }
                )

                new_line = line.get("new_line")
                if isinstance(new_line, int) and new_line in comments_by_line:
                    for comment in comments_by_line[new_line]:
                        severity = str(comment.get("severity", "info")).strip().lower() or "info"
                        rows.append(
                            {
                                "kind": "comment",
                                "severity": severity,
                                "text": str(comment.get("text", "")),
                            }
                        )

            file_view["hunks"].append(
                {
                    "anchor_id": f"{file_anchor_id}-hunk-{hunk_index}",
                    "is_open": is_open,
                    "summary_label": summary_label,
                    "added_lines": added_lines,
                    "removed_lines": removed_lines,
                    "notes": notes,
                    "rows": rows,
                }
            )

        files_render.append(file_view)

    embedded_json = _json_for_html_script(embedded_data) if embedded_data is not None else None

    return _TEMPLATE.render(
        title=title,
        files_changed=prepared_stats.get("files_changed", 0),
        additions=prepared_stats.get("additions", 0),
        deletions=prepared_stats.get("deletions", 0),
        error_count=error_count,
        warning_count=warning_count,
        overview_lines=overview_lines,
        issues_render=issues_render,
        issues_extra_count=issues_extra_count,
        files_render=files_render,
        embedded_json=embedded_json,
    )
