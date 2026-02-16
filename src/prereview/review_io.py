from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prereview.util import write_text

_ALLOWED_SEVERITIES = {"info", "note", "warning", "risk"}


def _warning(code: str, message: str, location: str) -> dict[str, str]:
    return {"level": "warning", "code": code, "message": message, "location": location}


def default_review_notes_template() -> str:
    return (
        "# prereview notes JSONL\n"
        "# One JSON object per line.\n"
        '# {"type":"overview","text":"Scope: ..."}\n'
        '# {"type":"file_summary","path":"src/example.py","summary":"..."}\n'
        '# {"type":"anchor_note","anchor_id":"<anchor-id>","what_changed":"...","why_changed":"..."}\n'
    )


def _compact_json_line(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=True, separators=(",", ":"))


def notes_payload_to_jsonl_lines(notes_payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []

    overview = notes_payload.get("overview")
    if isinstance(overview, list):
        for item in overview:
            if isinstance(item, str) and item.strip():
                lines.append(
                    _compact_json_line({"type": "overview", "text": item.strip()})
                )

    file_summaries = notes_payload.get("file_summaries")
    if isinstance(file_summaries, list):
        for summary in file_summaries:
            if not isinstance(summary, dict):
                continue
            path = summary.get("path")
            text = summary.get("summary")
            if (
                isinstance(path, str)
                and path
                and isinstance(text, str)
                and text.strip()
            ):
                lines.append(
                    _compact_json_line(
                        {"type": "file_summary", "path": path, "summary": text.strip()}
                    )
                )

    anchors = notes_payload.get("anchors")
    if isinstance(anchors, list):
        for anchor in anchors:
            if not isinstance(anchor, dict):
                continue
            anchor_id = anchor.get("anchor_id")
            what_changed = anchor.get("what_changed")
            why_changed = anchor.get("why_changed")
            if not (
                isinstance(anchor_id, str)
                and anchor_id
                and isinstance(what_changed, str)
                and what_changed.strip()
                and isinstance(why_changed, str)
                and why_changed.strip()
            ):
                continue

            record: dict[str, Any] = {
                "type": "anchor_note",
                "anchor_id": anchor_id,
                "what_changed": what_changed.strip(),
                "why_changed": why_changed.strip(),
            }
            for field in ("title", "reviewer_focus", "risk", "severity"):
                value = anchor.get(field)
                if isinstance(value, str) and value.strip():
                    record[field] = value.strip()

            lines.append(_compact_json_line(record))

    return lines


def render_review_input(context: dict[str, Any], *, notes_file: str) -> str:
    stats = context.get("stats", {})
    files = context.get("files", [])

    files_changed = int(stats.get("files_changed", 0))
    additions = int(stats.get("additions", 0))
    deletions = int(stats.get("deletions", 0))

    lines = [
        "PREREVIEW REVIEW INPUT v1",
        f"target_context_id: {context.get('context_id', '')}",
        f"diff_fingerprint: {context.get('diff_fingerprint', '')}",
        f"stats: files={files_changed} additions={additions} deletions={deletions}",
        f"write_notes_to: {notes_file}",
        "",
        "Write JSONL records. Supported record types:",
        '{"type":"overview","text":"..."}',
        '{"type":"file_summary","path":"...","summary":"..."}',
        '{"type":"anchor_note","anchor_id":"...","what_changed":"...","why_changed":"...","title":"...","reviewer_focus":"...","risk":"...","severity":"note"}',
        "",
        "CONTEXT START",
    ]

    for file_entry in files:
        if not isinstance(file_entry, dict):
            continue
        path = file_entry.get("path")
        if not isinstance(path, str) or not path:
            continue
        status = file_entry.get("status", "modified")
        lines.append(f"FILE path={path} status={status}")
        for anchor in file_entry.get("anchors", []):
            if not isinstance(anchor, dict):
                continue
            anchor_id = anchor.get("anchor_id")
            if not isinstance(anchor_id, str) or not anchor_id:
                continue
            lines.append(f"ANCHOR id={anchor_id}")

            title = anchor.get("title")
            if isinstance(title, str) and title.strip():
                lines.append(f"TITLE {title.strip()}")

            snippets = anchor.get("focus_snippets")
            if isinstance(snippets, list):
                for snippet in snippets:
                    if isinstance(snippet, str) and snippet.strip():
                        lines.append(f"SNIPPET {snippet.strip()}")

            risk_hint = anchor.get("risk_hint")
            if isinstance(risk_hint, str) and risk_hint.strip():
                lines.append(f"RISK_HINT {risk_hint.strip()}")

            lines.append("END_ANCHOR")
        lines.append("END_FILE")

    lines.append("CONTEXT END")
    return "\n".join(lines) + "\n"


def parse_review_notes_jsonl(
    path: Path,
    context: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, Any]]]:
    issues: list[dict[str, str]] = []
    rejected: list[dict[str, Any]] = []

    file_order: list[str] = []
    known_paths: set[str] = set()
    anchor_order: list[str] = []
    known_anchors: dict[str, str] = {}
    for file_entry in context.get("files", []):
        if not isinstance(file_entry, dict):
            continue
        file_path = file_entry.get("path")
        if not isinstance(file_path, str) or not file_path:
            continue
        file_order.append(file_path)
        known_paths.add(file_path)
        for anchor in file_entry.get("anchors", []):
            if not isinstance(anchor, dict):
                continue
            anchor_id = anchor.get("anchor_id")
            if not isinstance(anchor_id, str) or not anchor_id:
                continue
            anchor_order.append(anchor_id)
            known_anchors[anchor_id] = file_path

    overview: list[str] = []
    file_summaries_by_path: dict[str, str] = {}
    anchors_by_id: dict[str, dict[str, Any]] = {}

    if path.exists():
        for line_no, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            location = f"{path.name}:{line_no}"
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                issues.append(
                    _warning(
                        "invalid_jsonl",
                        f"Rejected line {line_no}: invalid JSON ({exc.msg}).",
                        location,
                    )
                )
                rejected.append(
                    {
                        "line": line_no,
                        "code": "invalid_jsonl",
                        "message": exc.msg,
                        "raw": raw_line,
                    }
                )
                continue

            if not isinstance(record, dict):
                issues.append(
                    _warning(
                        "record_type",
                        f"Rejected line {line_no}: record must be a JSON object.",
                        location,
                    )
                )
                rejected.append(
                    {
                        "line": line_no,
                        "code": "record_type",
                        "message": "record must be a JSON object",
                        "record": record,
                    }
                )
                continue

            record_type = record.get("type")
            if not isinstance(record_type, str) or not record_type.strip():
                issues.append(
                    _warning(
                        "missing_type",
                        f"Rejected line {line_no}: missing record type.",
                        location,
                    )
                )
                rejected.append(
                    {
                        "line": line_no,
                        "code": "missing_type",
                        "message": "missing record type",
                        "record": record,
                    }
                )
                continue

            if record_type == "overview":
                text = record.get("text")
                if isinstance(text, str) and text.strip():
                    overview.append(text.strip())
                else:
                    issues.append(
                        _warning(
                            "overview_text",
                            f"Rejected line {line_no}: overview.text must be non-empty.",
                            location,
                        )
                    )
                    rejected.append(
                        {
                            "line": line_no,
                            "code": "overview_text",
                            "message": "overview.text must be non-empty",
                            "record": record,
                        }
                    )
                continue

            if record_type == "file_summary":
                file_path = record.get("path")
                summary = record.get("summary")
                if not isinstance(file_path, str) or not file_path:
                    issues.append(
                        _warning(
                            "file_summary_path",
                            f"Rejected line {line_no}: file_summary.path is required.",
                            location,
                        )
                    )
                    rejected.append(
                        {
                            "line": line_no,
                            "code": "file_summary_path",
                            "message": "file_summary.path is required",
                            "record": record,
                        }
                    )
                    continue
                if file_path not in known_paths:
                    issues.append(
                        _warning(
                            "unknown_file",
                            f"Rejected line {line_no}: unknown file path {file_path!r}.",
                            location,
                        )
                    )
                    rejected.append(
                        {
                            "line": line_no,
                            "code": "unknown_file",
                            "message": f"unknown file path {file_path!r}",
                            "record": record,
                        }
                    )
                    continue
                if not isinstance(summary, str) or not summary.strip():
                    issues.append(
                        _warning(
                            "file_summary_text",
                            f"Rejected line {line_no}: file_summary.summary must be non-empty.",
                            location,
                        )
                    )
                    rejected.append(
                        {
                            "line": line_no,
                            "code": "file_summary_text",
                            "message": "file_summary.summary must be non-empty",
                            "record": record,
                        }
                    )
                    continue
                if file_path in file_summaries_by_path:
                    issues.append(
                        _warning(
                            "duplicate_file_summary",
                            f"Duplicate file_summary for {file_path!r}; keeping the latest.",
                            location,
                        )
                    )
                file_summaries_by_path[file_path] = summary.strip()
                continue

            if record_type == "anchor_note":
                anchor_id = record.get("anchor_id")
                what_changed = record.get("what_changed")
                why_changed = record.get("why_changed")
                if not isinstance(anchor_id, str) or not anchor_id:
                    issues.append(
                        _warning(
                            "missing_anchor_id",
                            f"Rejected line {line_no}: anchor_note.anchor_id is required.",
                            location,
                        )
                    )
                    rejected.append(
                        {
                            "line": line_no,
                            "code": "missing_anchor_id",
                            "message": "anchor_note.anchor_id is required",
                            "record": record,
                        }
                    )
                    continue
                if anchor_id not in known_anchors:
                    issues.append(
                        _warning(
                            "unknown_anchor_id",
                            f"Rejected line {line_no}: unknown anchor_id {anchor_id!r}.",
                            location,
                        )
                    )
                    rejected.append(
                        {
                            "line": line_no,
                            "code": "unknown_anchor_id",
                            "message": f"unknown anchor_id {anchor_id!r}",
                            "record": record,
                        }
                    )
                    continue
                if not isinstance(what_changed, str) or not what_changed.strip():
                    issues.append(
                        _warning(
                            "missing_what_changed",
                            f"Rejected line {line_no}: what_changed is required.",
                            location,
                        )
                    )
                    rejected.append(
                        {
                            "line": line_no,
                            "code": "missing_what_changed",
                            "message": "what_changed is required",
                            "record": record,
                        }
                    )
                    continue
                if not isinstance(why_changed, str) or not why_changed.strip():
                    issues.append(
                        _warning(
                            "missing_why_changed",
                            f"Rejected line {line_no}: why_changed is required.",
                            location,
                        )
                    )
                    rejected.append(
                        {
                            "line": line_no,
                            "code": "missing_why_changed",
                            "message": "why_changed is required",
                            "record": record,
                        }
                    )
                    continue

                note_record: dict[str, Any] = {
                    "anchor_id": anchor_id,
                    "what_changed": what_changed.strip(),
                    "why_changed": why_changed.strip(),
                }

                title = record.get("title")
                if isinstance(title, str) and title.strip():
                    note_record["title"] = title.strip()

                reviewer_focus = record.get("reviewer_focus")
                if isinstance(reviewer_focus, str) and reviewer_focus.strip():
                    note_record["reviewer_focus"] = reviewer_focus.strip()

                risk = record.get("risk")
                if isinstance(risk, str) and risk.strip():
                    note_record["risk"] = risk.strip()

                severity = record.get("severity")
                if isinstance(severity, str) and severity.strip():
                    normalized_severity = severity.strip().lower()
                    if normalized_severity in _ALLOWED_SEVERITIES:
                        note_record["severity"] = normalized_severity
                    else:
                        issues.append(
                            _warning(
                                "bad_severity",
                                f"Rejected line {line_no}: bad severity {severity!r}.",
                                location,
                            )
                        )
                        rejected.append(
                            {
                                "line": line_no,
                                "code": "bad_severity",
                                "message": f"bad severity {severity!r}",
                                "record": record,
                            }
                        )
                        continue

                if anchor_id in anchors_by_id:
                    issues.append(
                        _warning(
                            "duplicate_anchor_note",
                            f"Duplicate anchor note for {anchor_id!r}; keeping the latest.",
                            location,
                        )
                    )
                anchors_by_id[anchor_id] = note_record
                continue

            issues.append(
                _warning(
                    "unknown_type",
                    f"Rejected line {line_no}: unsupported record type {record_type!r}.",
                    location,
                )
            )
            rejected.append(
                {
                    "line": line_no,
                    "code": "unknown_type",
                    "message": f"unsupported record type {record_type!r}",
                    "record": record,
                }
            )

    context_id = context.get("context_id")
    target_context_id = context_id if isinstance(context_id, str) else ""

    anchor_entries = [
        anchors_by_id[anchor_id]
        for anchor_id in anchor_order
        if anchor_id in anchors_by_id
    ]
    file_summary_entries = [
        {"path": file_path, "summary": file_summaries_by_path[file_path]}
        for file_path in file_order
        if file_path in file_summaries_by_path
    ]

    notes_payload: dict[str, Any] = {
        "version": "1",
        "target_context_id": target_context_id,
        "overview": overview,
        "anchors": anchor_entries,
    }
    if file_summary_entries:
        notes_payload["file_summaries"] = file_summary_entries

    return notes_payload, issues, rejected


def write_rejected_notes_jsonl(path: Path, rejected: list[dict[str, Any]]) -> None:
    if not rejected:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return

    encoded_lines = [json.dumps(record, sort_keys=True) for record in rejected]
    write_text(path, "\n".join(encoded_lines) + "\n")


def rewrite_review_notes_jsonl(path: Path, notes_payload: dict[str, Any]) -> None:
    lines = notes_payload_to_jsonl_lines(notes_payload)
    if not lines:
        write_text(path, default_review_notes_template())
        return
    write_text(path, "\n".join(lines) + "\n")
