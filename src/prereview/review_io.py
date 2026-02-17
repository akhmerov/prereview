from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from jinja2 import Environment

from prereview.models import Severity
from prereview.util import write_text

_TEMPLATE_ENV = Environment(
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)
_REVIEW_INPUT_TEMPLATE = _TEMPLATE_ENV.from_string(
    resources.files("prereview")
    .joinpath("templates/review-input.txt.j2")
    .read_text(encoding="utf-8")
)


def _warning(code: str, message: str, location: str) -> dict[str, str]:
    return {"level": "warning", "code": code, "message": message, "location": location}


def render_review_input(
    context: dict[str, Any],
    *,
    notes_file: str,
    anchor_states: dict[str, dict[str, Any]],
) -> str:
    stats = context["stats"]
    files_view: list[dict[str, Any]] = []

    for file_entry in context["files"]:
        anchors_view: list[dict[str, Any]] = []
        for anchor in file_entry["anchors"]:
            anchor_id = anchor["anchor_id"]
            state = anchor_states[anchor_id]
            uncommented = state["uncommented"]
            changed_loc_text = str(state["changed_loc"])

            snippets = (
                [
                    snippet.strip()
                    for snippet in anchor["focus_snippets"]
                    if snippet.strip()
                ]
                if not uncommented
                else []
            )

            risk_hint_text = (anchor["risk_hint"] or "").strip()

            diff_lines: list[str] = []
            if uncommented:
                maybe_lines = state.get("diff_lines", [])
                if maybe_lines:
                    diff_lines = list(maybe_lines)
                    if state.get("diff_truncated"):
                        diff_lines.append("... (diff truncated)")
                elif state.get("diff_omitted"):
                    diff_lines.append("... (diff omitted: budget exceeded)")

            anchors_view.append(
                {
                    "anchor_id": anchor_id,
                    "uncommented": uncommented,
                    "changed_loc_text": changed_loc_text,
                    "title": anchor["title"].strip(),
                    "snippets": snippets,
                    "risk_hint": risk_hint_text,
                    "diff_lines": diff_lines,
                }
            )

        files_view.append(
            {
                "path": file_entry["path"],
                "status": file_entry["status"],
                "anchors": anchors_view,
            }
        )

    return _REVIEW_INPUT_TEMPLATE.render(
        context_id=context["context_id"],
        diff_fingerprint=context["diff_fingerprint"],
        stats=stats,
        notes_file=notes_file,
        files=files_view,
    )


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

    def reject_record(
        *,
        line_no: int,
        location: str,
        code: str,
        warning_detail: str,
        message: str,
        record: Any | None = None,
        raw: str | None = None,
    ) -> None:
        issues.append(
            _warning(
                code,
                f"Rejected line {line_no}: {warning_detail}",
                location,
            )
        )
        rejected_entry: dict[str, Any] = {
            "line": line_no,
            "code": code,
            "message": message,
        }
        if record is not None:
            rejected_entry["record"] = record
        if raw is not None:
            rejected_entry["raw"] = raw
        rejected.append(rejected_entry)

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
                reject_record(
                    line_no=line_no,
                    location=location,
                    code="invalid_jsonl",
                    warning_detail=f"invalid JSON ({exc.msg}).",
                    message=exc.msg,
                    raw=raw_line,
                )
                continue

            if not isinstance(record, dict):
                reject_record(
                    line_no=line_no,
                    location=location,
                    code="record_type",
                    warning_detail="record must be a JSON object.",
                    message="record must be a JSON object",
                    record=record,
                )
                continue

            record_type = record.get("type")
            if not isinstance(record_type, str) or not record_type.strip():
                reject_record(
                    line_no=line_no,
                    location=location,
                    code="missing_type",
                    warning_detail="missing record type.",
                    message="missing record type",
                    record=record,
                )
                continue

            if record_type == "overview":
                text = record.get("text")
                if isinstance(text, str) and text.strip():
                    overview.append(text.strip())
                else:
                    reject_record(
                        line_no=line_no,
                        location=location,
                        code="overview_text",
                        warning_detail="overview.text must be non-empty.",
                        message="overview.text must be non-empty",
                        record=record,
                    )
                continue

            if record_type == "file_summary":
                file_path = record.get("path")
                summary = record.get("summary")
                if not isinstance(file_path, str) or not file_path:
                    reject_record(
                        line_no=line_no,
                        location=location,
                        code="file_summary_path",
                        warning_detail="file_summary.path is required.",
                        message="file_summary.path is required",
                        record=record,
                    )
                    continue
                if file_path not in known_paths:
                    reject_record(
                        line_no=line_no,
                        location=location,
                        code="unknown_file",
                        warning_detail=f"unknown file path {file_path!r}.",
                        message=f"unknown file path {file_path!r}",
                        record=record,
                    )
                    continue
                if not isinstance(summary, str) or not summary.strip():
                    reject_record(
                        line_no=line_no,
                        location=location,
                        code="file_summary_text",
                        warning_detail="file_summary.summary must be non-empty.",
                        message="file_summary.summary must be non-empty",
                        record=record,
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
                    reject_record(
                        line_no=line_no,
                        location=location,
                        code="missing_anchor_id",
                        warning_detail="anchor_note.anchor_id is required.",
                        message="anchor_note.anchor_id is required",
                        record=record,
                    )
                    continue
                if anchor_id not in known_anchors:
                    reject_record(
                        line_no=line_no,
                        location=location,
                        code="unknown_anchor_id",
                        warning_detail=f"unknown anchor_id {anchor_id!r}.",
                        message=f"unknown anchor_id {anchor_id!r}",
                        record=record,
                    )
                    continue
                if not isinstance(what_changed, str) or not what_changed.strip():
                    reject_record(
                        line_no=line_no,
                        location=location,
                        code="missing_what_changed",
                        warning_detail="what_changed is required.",
                        message="what_changed is required",
                        record=record,
                    )
                    continue
                if not isinstance(why_changed, str) or not why_changed.strip():
                    reject_record(
                        line_no=line_no,
                        location=location,
                        code="missing_why_changed",
                        warning_detail="why_changed is required.",
                        message="why_changed is required",
                        record=record,
                    )
                    continue

                note_record: dict[str, Any] = {
                    "anchor_id": anchor_id,
                    "what_changed": what_changed.strip(),
                    "why_changed": why_changed.strip(),
                    "severity": Severity.NOTE.value,
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

                severity = record.get("severity", Severity.NOTE.value)
                try:
                    note_record["severity"] = Severity(severity.strip().lower()).value
                except (AttributeError, ValueError):
                    reject_record(
                        line_no=line_no,
                        location=location,
                        code="bad_severity",
                        warning_detail=f"bad severity {severity!r}.",
                        message=f"bad severity {severity!r}",
                        record=record,
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

            reject_record(
                line_no=line_no,
                location=location,
                code="unknown_type",
                warning_detail=f"unsupported record type {record_type!r}.",
                message=f"unsupported record type {record_type!r}",
                record=record,
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


def rewrite_review_notes_jsonl(path: Path, rejected: list[dict[str, Any]]) -> None:
    if not rejected or not path.exists():
        return

    rejected_lines = {
        line_no for entry in rejected if isinstance(line_no := entry.get("line"), int)
    }
    if not rejected_lines:
        return

    kept_lines = [
        line
        for line_no, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        )
        if line_no not in rejected_lines
    ]
    output = "\n".join(kept_lines)
    if output:
        output += "\n"
    write_text(path, output)
