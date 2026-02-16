from __future__ import annotations

import json
from pathlib import Path

import pytest

from prereview.annotations import compile_annotations_from_notes
from prereview.cli import build_parser, main
from prereview.diff_parser import parse_unified_diff
import prereview.prepare as prepare_module
from prereview.prepare import (
    build_review_context,
    build_source_spec,
    recompute_runtime_from_context,
)
from prereview.review_io import (
    default_review_notes_template,
    notes_payload_to_jsonl_lines,
    parse_review_notes_jsonl,
    rewrite_review_notes_jsonl,
    render_review_input,
)
from prereview.renderer import render_html
from prereview.validate import evaluate_annotations, materialize_annotations_for_render

SAMPLE_PATCH = """diff --git a/src/demo.py b/src/demo.py
index 1111111..2222222 100644
--- a/src/demo.py
+++ b/src/demo.py
@@ -1,2 +1,3 @@
 def greet():
-    return \"hi\"
+    message = \"hi\"
+    return message
"""


def _context_from_patch(
    patch: str, *, exclude_paths: list[str] | None = None
) -> dict[str, object]:
    patch_path = Path("/tmp/prereview-context.patch")
    patch_path.write_text(patch, encoding="utf-8")
    source_spec = build_source_spec(
        patch_file=patch_path,
        git_range=None,
        use_working_tree=False,
        include_untracked=False,
        exclude_paths=exclude_paths or [],
    )
    return build_review_context(patch, source_spec)


def _annotations_from_context(context: dict[str, object]) -> dict[str, object]:
    file_annotations: list[dict[str, object]] = []
    for file_entry in context.get("files", []):
        if not isinstance(file_entry, dict):
            continue
        path = file_entry.get("path")
        if not isinstance(path, str) or not path:
            continue

        anchors: list[dict[str, object]] = []
        for anchor in file_entry.get("anchors", []):
            if not isinstance(anchor, dict):
                continue
            anchor_id = anchor.get("anchor_id")
            if not isinstance(anchor_id, str) or not anchor_id:
                continue
            anchors.append(
                {
                    "anchor_id": anchor_id,
                    "title": "Change focus",
                    "what_changed": "Behavior was adjusted in this change focus.",
                    "why_changed": "To improve correctness and maintainability.",
                }
            )

        file_annotations.append(
            {
                "path": path,
                "summary": "What changed: focused updates in this file. Why: improve correctness and maintainability.",
                "anchors": anchors,
            }
        )

    return {
        "version": "2",
        "target_context_id": context["context_id"],
        "overview": [
            "Scope: focused diff under review.",
            "Primary intent: explain what changed and why.",
            "Reviewer focus: verify behavioral impact and risk assumptions.",
        ],
        "files": file_annotations,
    }


def _notes_from_context(context: dict[str, object]) -> dict[str, object]:
    notes_anchors: list[dict[str, object]] = []
    file_summaries: list[dict[str, object]] = []
    for file_entry in context.get("files", []):
        if not isinstance(file_entry, dict):
            continue
        path = file_entry.get("path")
        if isinstance(path, str) and path:
            file_summaries.append(
                {
                    "path": path,
                    "summary": "Focused file update; see anchors for behavior and intent.",
                }
            )
        for anchor in file_entry.get("anchors", []):
            if not isinstance(anchor, dict):
                continue
            anchor_id = anchor.get("anchor_id")
            if not isinstance(anchor_id, str) or not anchor_id:
                continue
            notes_anchors.append(
                {
                    "anchor_id": anchor_id,
                    "what_changed": "Behavior was adjusted in this change focus.",
                    "why_changed": "To improve correctness and maintainability.",
                    "title": "Change focus",
                }
            )

    return {
        "version": "1",
        "target_context_id": context["context_id"],
        "overview": [
            "Scope: focused diff under review.",
            "Primary intent: explain what changed and why.",
            "Reviewer focus: verify behavioral impact and risk assumptions.",
        ],
        "file_summaries": file_summaries,
        "anchors": notes_anchors,
    }


def test_build_review_context_does_not_store_raw_patch() -> None:
    context = _context_from_patch(SAMPLE_PATCH)
    assert context["version"] == "2"
    assert "context_id" in context
    assert "raw_patch" not in context
    assert context["stats"]["files_changed"] == 1
    assert context["files"]
    first_file = context["files"][0]
    assert first_file["path"] == "src/demo.py"
    assert first_file["anchors"]


def test_authored_annotations_use_anchor_ids() -> None:
    context = _context_from_patch(SAMPLE_PATCH)
    annotations = _annotations_from_context(context)
    assert annotations["version"] == "2"
    assert annotations["target_context_id"] == context["context_id"]
    assert isinstance(annotations.get("overview"), list)

    file_entry = annotations["files"][0]
    assert file_entry["path"] == "src/demo.py"
    assert "What changed:" in file_entry["summary"]
    assert "Why:" in file_entry["summary"]

    anchor = file_entry["anchors"][0]
    assert "anchor_id" in anchor
    assert "what_changed" in anchor
    assert "why_changed" in anchor
    assert "line_start" not in anchor


def test_compile_notes_to_annotations_maps_anchors() -> None:
    context = _context_from_patch(SAMPLE_PATCH)
    notes = _notes_from_context(context)
    annotations, issues = compile_annotations_from_notes(context, notes)
    assert not any(issue["level"] == "error" for issue in issues)
    assert annotations["version"] == "2"
    assert annotations["target_context_id"] == context["context_id"]
    assert annotations["files"]
    compiled_anchor = annotations["files"][0]["anchors"][0]
    assert (
        compiled_anchor["anchor_id"] == context["files"][0]["anchors"][0]["anchor_id"]
    )
    assert "what_changed" in compiled_anchor
    assert "why_changed" in compiled_anchor


def test_validate_and_materialize_annotations() -> None:
    context = _context_from_patch(SAMPLE_PATCH)
    annotations = _annotations_from_context(context)

    report, runtime = evaluate_annotations(context, annotations, strict=True)
    assert report["valid"] is True
    assert runtime is not None

    render_annotations = materialize_annotations_for_render(runtime, annotations)
    assert render_annotations["files"]
    first_hunk = render_annotations["files"][0]["hunks"][0]
    assert first_hunk["note_fields"]["what_changed"]
    assert first_hunk["note_fields"]["why_changed"]
    assert "What changed:" in first_hunk["explanation"]
    assert "Why:" in first_hunk["explanation"]


def test_materialize_does_not_double_terminal_periods() -> None:
    context = _context_from_patch(SAMPLE_PATCH)
    annotations = _annotations_from_context(context)
    annotations["files"][0]["anchors"][0]["what_changed"] = "Changed greeting flow."
    annotations["files"][0]["anchors"][0]["why_changed"] = "Keep return path explicit."

    report, runtime = evaluate_annotations(context, annotations, strict=True)
    assert report["valid"] is True
    assert runtime is not None

    render_annotations = materialize_annotations_for_render(runtime, annotations)
    first_hunk = render_annotations["files"][0]["hunks"][0]
    explanation = first_hunk["explanation"]
    note_fields = first_hunk["note_fields"]

    assert "What changed: Changed greeting flow." in explanation
    assert "Why: Keep return path explicit." in explanation
    assert "flow.." not in explanation
    assert "explicit.." not in explanation
    assert note_fields["what_changed"] == "Changed greeting flow."
    assert note_fields["why_changed"] == "Keep return path explicit."


def test_validate_fails_on_unknown_anchor() -> None:
    context = _context_from_patch(SAMPLE_PATCH)
    annotations = {
        "version": "2",
        "target_context_id": context["context_id"],
        "overview": ["Scope: 1 file."],
        "files": [
            {
                "path": "src/demo.py",
                "summary": "What changed: placeholder. Why: placeholder.",
                "anchors": [
                    {
                        "anchor_id": "missing-anchor-id",
                        "title": "X",
                        "what_changed": "something",
                        "why_changed": "reason",
                    }
                ],
            }
        ],
    }

    report, _ = evaluate_annotations(context, annotations, strict=True)
    assert report["valid"] is False
    assert any(issue["code"] == "unknown_anchor" for issue in report["issues"])


def test_render_preserves_indentation() -> None:
    context = _context_from_patch(SAMPLE_PATCH)
    annotations = _annotations_from_context(context)
    report, runtime = evaluate_annotations(context, annotations, strict=True)
    assert report["valid"] is True
    assert runtime is not None

    render_annotations = materialize_annotations_for_render(runtime, annotations)
    html = render_html(
        {
            "stats": runtime["stats"],
            "files": runtime["files"],
        },
        render_annotations,
        report,
        title="Indent",
        max_expanded_lines=120,
        collapse_large_hunks=True,
        allow_split_hunks=True,
    )
    assert "white-space: pre;" in html
    assert "class='code'" in html
    assert "<span class='diff-prefix'>+</span>    message = &quot;hi&quot;" in html
    assert "class='headline-stats'" in html
    assert "Mapped notes" not in html
    assert "Unmapped notes" not in html
    assert "class='diff-scroll'" in html
    assert "overflow-y: auto;" in html
    assert "width: 2.2rem;" in html
    assert "width: 3rem;" not in html


def test_render_line_note_meta_shows_severity_only() -> None:
    context = _context_from_patch(SAMPLE_PATCH)
    annotations = _annotations_from_context(context)
    annotations["files"][0]["anchors"][0]["severity"] = "warning"
    annotations["files"][0]["anchors"][0]["reviewer_focus"] = "Check behavior."

    report, runtime = evaluate_annotations(context, annotations, strict=True)
    assert report["valid"] is True
    assert runtime is not None

    render_annotations = materialize_annotations_for_render(runtime, annotations)
    html = render_html(
        {
            "stats": runtime["stats"],
            "files": runtime["files"],
        },
        render_annotations,
        report,
        title="Line meta",
        max_expanded_lines=120,
        collapse_large_hunks=True,
        allow_split_hunks=True,
    )

    assert "<h4>" not in html
    assert "class='comment-meta comment-severity-warning'>warning</div>" in html
    assert " | hunk | " not in html
    assert " | prereview | " not in html
    assert " | L" not in html


def test_render_uses_readable_hunk_summary_label() -> None:
    context = _context_from_patch(SAMPLE_PATCH)
    annotations = _annotations_from_context(context)
    report, runtime = evaluate_annotations(context, annotations, strict=True)
    assert report["valid"] is True
    assert runtime is not None

    render_annotations = materialize_annotations_for_render(runtime, annotations)
    html = render_html(
        {
            "stats": runtime["stats"],
            "files": runtime["files"],
        },
        render_annotations,
        report,
        title="Hunk summary",
        max_expanded_lines=120,
        collapse_large_hunks=True,
        allow_split_hunks=True,
    )

    assert "<summary><span>Change focus</span>" in html
    assert "+2 / -1" in html
    assert "Change +1-3 (from -1-2)" not in html
    assert "@@ -1 +1 @@" not in html


def test_render_hunk_notes_use_structured_labels() -> None:
    context = _context_from_patch(SAMPLE_PATCH)
    annotations = _annotations_from_context(context)
    report, runtime = evaluate_annotations(context, annotations, strict=True)
    assert report["valid"] is True
    assert runtime is not None

    render_annotations = materialize_annotations_for_render(runtime, annotations)
    html = render_html(
        {
            "stats": runtime["stats"],
            "files": runtime["files"],
        },
        render_annotations,
        report,
        title="Structured notes",
        max_expanded_lines=120,
        collapse_large_hunks=True,
        allow_split_hunks=True,
    )

    assert "<strong>What changed:</strong>" in html
    assert "<strong>Why:</strong>" in html
    assert "class='hunk-note-row'" in html


def test_render_summary_deduplicates_filename_prefix() -> None:
    context = _context_from_patch(SAMPLE_PATCH)
    annotations = _annotations_from_context(context)
    path = str(annotations["files"][0]["path"])
    annotations["files"][0]["summary"] = (
        f"{path}: Focused update for greeting behavior."
    )

    report, runtime = evaluate_annotations(context, annotations, strict=True)
    assert report["valid"] is True
    assert runtime is not None

    render_annotations = materialize_annotations_for_render(runtime, annotations)
    html = render_html(
        {
            "stats": runtime["stats"],
            "files": runtime["files"],
        },
        render_annotations,
        report,
        title="Summary dedupe",
        max_expanded_lines=120,
        collapse_large_hunks=True,
        allow_split_hunks=True,
    )

    assert "Focused update for greeting behavior." in html
    assert f"{path}: Focused update for greeting behavior." not in html
    assert "class='file-name'>demo.py</div>" in html
    assert "class='file-dir'>src/</div>" in html


def test_render_includes_toc_with_file_and_hunk_links() -> None:
    context = _context_from_patch(SAMPLE_PATCH)
    annotations = _annotations_from_context(context)
    report, runtime = evaluate_annotations(context, annotations, strict=True)
    assert report["valid"] is True
    assert runtime is not None

    render_annotations = materialize_annotations_for_render(runtime, annotations)
    html = render_html(
        {
            "stats": runtime["stats"],
            "files": runtime["files"],
        },
        render_annotations,
        report,
        title="TOC",
        max_expanded_lines=120,
        collapse_large_hunks=True,
        allow_split_hunks=True,
    )

    assert "class='toc'" in html
    assert "aria-label='Table of contents'" in html
    assert "href='#file-1'" in html
    assert "href='#file-1-hunk-1'" in html
    assert "data-toc-link='file-1-hunk-1'" in html
    assert "class='toc-link toc-hunk-link'" in html
    assert 'classList.toggle("is-active"' in html


def test_render_includes_reviewer_commenting_ui() -> None:
    context = _context_from_patch(SAMPLE_PATCH)
    annotations = _annotations_from_context(context)
    report, runtime = evaluate_annotations(context, annotations, strict=True)
    assert report["valid"] is True
    assert runtime is not None

    render_annotations = materialize_annotations_for_render(runtime, annotations)
    html = render_html(
        {
            "stats": runtime["stats"],
            "files": runtime["files"],
        },
        render_annotations,
        report,
        title="Reviewer comments",
        max_expanded_lines=120,
        collapse_large_hunks=True,
        allow_split_hunks=True,
    )

    assert "id='copy-agent-prompt'" in html
    assert "id='reviewer-comment-list'" in html
    assert "id='clear-reviewer-comments'" in html
    assert "data-comment-trigger='line'" in html
    assert "data-location-key='src/demo.py::" in html
    assert "buildAgentPrompt" in html
    assert "Address all of the following comments one by one." in html
    assert "target_context_id:" not in html
    assert " | hunk_id=" not in html
    assert "Click a line number in the diff to add a reviewer comment." in html


def test_render_file_sections_are_collapsible_from_header() -> None:
    context = _context_from_patch(SAMPLE_PATCH)
    annotations = _annotations_from_context(context)
    report, runtime = evaluate_annotations(context, annotations, strict=True)
    assert report["valid"] is True
    assert runtime is not None

    render_annotations = materialize_annotations_for_render(runtime, annotations)
    html = render_html(
        {
            "stats": runtime["stats"],
            "files": runtime["files"],
        },
        render_annotations,
        report,
        title="File collapse",
        max_expanded_lines=120,
        collapse_large_hunks=True,
        allow_split_hunks=True,
    )

    assert "<details class='file toc-target'" in html
    assert "<summary class='file-header'>" in html
    assert "class='file-toggle'" in html
    assert 'parent.parentElement.closest("details")' in html


def test_cli_context_pipeline(tmp_path: Path) -> None:
    patch_path = tmp_path / "change.patch"
    context_path = tmp_path / "review-context.json"
    notes_path = tmp_path / "annotation-notes.json"
    html_path = tmp_path / "preview.html"

    patch_path.write_text(SAMPLE_PATCH, encoding="utf-8")

    assert (
        main(
            [
                "prepare-context",
                "--patch-file",
                str(patch_path),
                "--out",
                str(context_path),
            ]
        )
        == 0
    )

    context = json.loads(context_path.read_text(encoding="utf-8"))
    notes = _notes_from_context(context)
    notes_path.write_text(json.dumps(notes), encoding="utf-8")

    assert (
        main(
            [
                "build",
                "--context",
                str(context_path),
                "--notes",
                str(notes_path),
                "--output",
                str(html_path),
            ]
        )
        == 0
    )

    rendered = html_path.read_text(encoding="utf-8")
    assert "Review Overview" in rendered
    assert "src/demo.py" in rendered
    assert "prereview-embedded-data" in rendered
    assert '"validation_report"' in rendered
    assert '"annotation_notes"' in rendered
    assert not context_path.exists()
    assert not notes_path.exists()


def test_cli_build_keep_inputs_opt_out(tmp_path: Path) -> None:
    patch_path = tmp_path / "change.patch"
    context_path = tmp_path / "review-context.json"
    notes_path = tmp_path / "annotation-notes.json"
    html_path = tmp_path / "review.html"

    patch_path.write_text(SAMPLE_PATCH, encoding="utf-8")

    assert (
        main(
            [
                "prepare-context",
                "--patch-file",
                str(patch_path),
                "--out",
                str(context_path),
            ]
        )
        == 0
    )
    context = json.loads(context_path.read_text(encoding="utf-8"))
    notes = _notes_from_context(context)
    notes_path.write_text(json.dumps(notes), encoding="utf-8")
    assert (
        main(
            [
                "build",
                "--context",
                str(context_path),
                "--notes",
                str(notes_path),
                "--output",
                str(html_path),
                "--keep-inputs",
            ]
        )
        == 0
    )

    assert context_path.exists()
    assert notes_path.exists()
    rendered = html_path.read_text(encoding="utf-8")
    assert "prereview-embedded-data" in rendered


def test_cli_build_failure_prints_fix_guidance(tmp_path: Path) -> None:
    patch_path = tmp_path / "change.patch"
    context_path = tmp_path / "review-context.json"
    notes_path = tmp_path / "annotation-notes.json"
    html_path = tmp_path / "review.html"

    patch_path.write_text(SAMPLE_PATCH, encoding="utf-8")

    assert (
        main(
            [
                "prepare-context",
                "--patch-file",
                str(patch_path),
                "--out",
                str(context_path),
            ]
        )
        == 0
    )

    context = json.loads(context_path.read_text(encoding="utf-8"))
    broken_notes = {
        "version": "1",
        "target_context_id": context["context_id"],
        "overview": ["Scope: 1 file."],
        "anchors": [
            {
                "anchor_id": "missing-anchor-id",
                "title": "Bad anchor",
                "what_changed": "placeholder",
                "why_changed": "placeholder",
            }
        ],
    }
    notes_path.write_text(json.dumps(broken_notes), encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "build",
                "--context",
                str(context_path),
                "--notes",
                str(notes_path),
                "--output",
                str(html_path),
            ]
        )

    message = str(excinfo.value)
    assert "Build validation failed" in message
    assert "Agent action:" in message
    assert "unknown_anchor" in message
    assert "Rerun after fixes:" in message
    assert "draft-annotations" not in message
    assert context_path.exists()
    assert notes_path.exists()
    assert not html_path.exists()


def test_cli_build_defaults_to_root_review_html(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_path = tmp_path / "change.patch"
    context_path = tmp_path / "review-context.json"
    notes_path = tmp_path / "annotation-notes.json"

    patch_path.write_text(SAMPLE_PATCH, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "prepare-context",
                "--patch-file",
                str(patch_path),
                "--out",
                str(context_path),
            ]
        )
        == 0
    )
    context = json.loads(context_path.read_text(encoding="utf-8"))
    notes = _notes_from_context(context)
    notes_path.write_text(json.dumps(notes), encoding="utf-8")
    assert (
        main(
            [
                "build",
                "--context",
                str(context_path),
                "--notes",
                str(notes_path),
            ]
        )
        == 0
    )

    assert (tmp_path / "review.html").exists()


def test_cli_draft_annotations_subcommand_is_removed() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["draft-annotations"])
    assert excinfo.value.code == 2


def test_cli_build_supports_legacy_annotations_input(tmp_path: Path) -> None:
    patch_path = tmp_path / "change.patch"
    context_path = tmp_path / "review-context.json"
    annotations_path = tmp_path / "annotations.json"
    html_path = tmp_path / "legacy-preview.html"

    patch_path.write_text(SAMPLE_PATCH, encoding="utf-8")

    assert (
        main(
            [
                "prepare-context",
                "--patch-file",
                str(patch_path),
                "--out",
                str(context_path),
            ]
        )
        == 0
    )
    context = json.loads(context_path.read_text(encoding="utf-8"))
    annotations = _annotations_from_context(context)
    annotations_path.write_text(json.dumps(annotations), encoding="utf-8")

    assert (
        main(
            [
                "build",
                "--context",
                str(context_path),
                "--annotations",
                str(annotations_path),
                "--output",
                str(html_path),
            ]
        )
        == 0
    )
    assert html_path.exists()


def test_render_review_input_uses_markers_and_anchor_ids() -> None:
    context = _context_from_patch(SAMPLE_PATCH)
    rendered = render_review_input(context, notes_file="review-notes.jsonl")

    assert "PREREVIEW REVIEW INPUT v1" in rendered
    assert "write_notes_to: review-notes.jsonl" in rendered
    assert "CONTEXT START" in rendered
    assert "FILE path=src/demo.py" in rendered
    assert "ANCHOR id=" in rendered
    assert "SNIPPET" in rendered
    assert "CONTEXT END" in rendered


def test_parse_review_notes_jsonl_rejects_invalid_records(tmp_path: Path) -> None:
    context = _context_from_patch(SAMPLE_PATCH)
    anchor_id = context["files"][0]["anchors"][0]["anchor_id"]
    notes_path = tmp_path / "review-notes.jsonl"
    notes_path.write_text(
        "\n".join(
            [
                '{"type":"overview","text":"Scope: greeting refactor."}',
                '{"type":"anchor_note","what_changed":"x","why_changed":"y"}',
                '{"type":"anchor_note","anchor_id":"unknown","what_changed":"x","why_changed":"y"}',
                f'{{"type":"anchor_note","anchor_id":"{anchor_id}","what_changed":"Use temp var","why_changed":"Improve readability"}}',
                '{"type":"file_summary","path":"src/demo.py","summary":"Small focused update."}',
                "this is not json",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    notes_payload, issues, rejected = parse_review_notes_jsonl(notes_path, context)

    assert notes_payload["version"] == "1"
    assert notes_payload["target_context_id"] == context["context_id"]
    assert len(notes_payload["overview"]) == 1
    assert len(notes_payload["anchors"]) == 1
    assert notes_payload["anchors"][0]["anchor_id"] == anchor_id
    assert len(notes_payload["file_summaries"]) == 1
    assert notes_payload["file_summaries"][0]["path"] == "src/demo.py"
    assert any(issue["code"] == "missing_anchor_id" for issue in issues)
    assert any(issue["code"] == "unknown_anchor_id" for issue in issues)
    assert any(issue["code"] == "invalid_jsonl" for issue in issues)
    assert len(rejected) == 3


def test_rewrite_review_notes_jsonl_writes_template_when_empty(tmp_path: Path) -> None:
    path = tmp_path / "review-notes.jsonl"
    rewrite_review_notes_jsonl(
        path,
        {
            "version": "1",
            "target_context_id": "ctx",
            "overview": [],
            "anchors": [],
        },
    )
    assert path.read_text(encoding="utf-8") == default_review_notes_template()


def test_notes_payload_to_jsonl_lines_roundtrip_fields() -> None:
    payload = {
        "version": "1",
        "target_context_id": "ctx",
        "overview": ["Scope: demo."],
        "file_summaries": [{"path": "src/demo.py", "summary": "Focused update."}],
        "anchors": [
            {
                "anchor_id": "abc",
                "what_changed": "Updated flow.",
                "why_changed": "Improve clarity.",
                "title": "Flow update",
                "reviewer_focus": "Error paths.",
                "risk": "Low compatibility risk.",
                "severity": "note",
            }
        ],
    }

    lines = notes_payload_to_jsonl_lines(payload)
    assert lines[0] == '{"type":"overview","text":"Scope: demo."}'
    assert lines[1] == (
        '{"type":"file_summary","path":"src/demo.py","summary":"Focused update."}'
    )
    assert '"type":"anchor_note"' in lines[2]
    assert '"anchor_id":"abc"' in lines[2]
    assert '"severity":"note"' in lines[2]


def test_cli_run_generates_workspace_and_html(tmp_path: Path) -> None:
    patch_path = tmp_path / "change.patch"
    artifacts_dir = tmp_path / "review"
    patch_path.write_text(SAMPLE_PATCH, encoding="utf-8")

    assert (
        main(
            [
                "--patch-file",
                str(patch_path),
                "--artifacts-dir",
                str(artifacts_dir),
            ]
        )
        == 0
    )

    assert (artifacts_dir / ".gitignore").exists()
    assert (artifacts_dir / ".gitignore").read_text(encoding="utf-8") == "*\n"
    assert (artifacts_dir / "review-context.json").exists()
    assert (artifacts_dir / "review-input.txt").exists()
    assert (artifacts_dir / "review-notes.jsonl").exists()
    assert (artifacts_dir / "annotations.json").exists()
    assert (artifacts_dir / "review.html").exists()

    notes_text = (artifacts_dir / "review-notes.jsonl").read_text(encoding="utf-8")
    assert default_review_notes_template().strip() in notes_text

    html = (artifacts_dir / "review.html").read_text(encoding="utf-8")
    assert "src/demo.py" in html
    assert "prereview-embedded-data" in html


def test_cli_run_writes_rejected_notes_for_bad_jsonl(tmp_path: Path) -> None:
    patch_path = tmp_path / "change.patch"
    artifacts_dir = tmp_path / "review"
    notes_path = artifacts_dir / "review-notes.jsonl"
    patch_path.write_text(SAMPLE_PATCH, encoding="utf-8")

    context = _context_from_patch(SAMPLE_PATCH)
    anchor_id = context["files"][0]["anchors"][0]["anchor_id"]

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(
        "\n".join(
            [
                '{"type":"anchor_note","what_changed":"missing id","why_changed":"missing id"}',
                '{"type":"anchor_note","anchor_id":"unknown","what_changed":"x","why_changed":"y"}',
                f'{{"type":"anchor_note","anchor_id":"{anchor_id}","what_changed":"Use temp var","why_changed":"Improve readability"}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert (
        main(
            [
                "--patch-file",
                str(patch_path),
                "--artifacts-dir",
                str(artifacts_dir),
            ]
        )
        == 0
    )

    rejected_path = artifacts_dir / "rejected-notes.jsonl"
    assert rejected_path.exists()
    rejected_text = rejected_path.read_text(encoding="utf-8")
    assert "missing_anchor_id" in rejected_text
    assert "unknown_anchor_id" in rejected_text

    rewritten_notes_text = notes_path.read_text(encoding="utf-8")
    assert "missing id" not in rewritten_notes_text
    assert '"anchor_id":"unknown"' not in rewritten_notes_text
    assert f'"anchor_id":"{anchor_id}"' in rewritten_notes_text

    html = (artifacts_dir / "review.html").read_text(encoding="utf-8")
    assert "missing_anchor_id" in html
    assert "unknown_anchor_id" in html


def test_cli_no_subcommand_defaults_to_run(tmp_path: Path) -> None:
    patch_path = tmp_path / "change.patch"
    artifacts_dir = tmp_path / "review"
    patch_path.write_text(SAMPLE_PATCH, encoding="utf-8")

    assert (
        main(
            [
                "--patch-file",
                str(patch_path),
                "--artifacts-dir",
                str(artifacts_dir),
            ]
        )
        == 0
    )
    assert (artifacts_dir / "review.html").exists()


def test_cli_run_subcommand_is_removed() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["run"])
    assert excinfo.value.code == 2


def test_cli_default_excludes_untracked_in_run_mode() -> None:
    args = build_parser().parse_args([])
    assert args.include_untracked is False


def test_recompute_runtime_excludes_nested_paths() -> None:
    patch = """diff --git a/showcase/out.txt b/showcase/out.txt
new file mode 100644
--- /dev/null
+++ b/showcase/out.txt
@@ -0,0 +1 @@
+artifact
diff --git a/showcase/nested/out2.txt b/showcase/nested/out2.txt
new file mode 100644
--- /dev/null
+++ b/showcase/nested/out2.txt
@@ -0,0 +1 @@
+artifact-2
diff --git a/src/keep.py b/src/keep.py
new file mode 100644
--- /dev/null
+++ b/src/keep.py
@@ -0,0 +1 @@
+print(\"keep\")
"""

    tmp_patch = Path("/tmp/prereview-test-exclude.patch")
    tmp_patch.write_text(patch, encoding="utf-8")

    source_spec = build_source_spec(
        patch_file=tmp_patch,
        git_range=None,
        use_working_tree=False,
        include_untracked=False,
        exclude_paths=["showcase/**"],
    )
    context = build_review_context(patch, source_spec)
    runtime = recompute_runtime_from_context(context)
    paths = [entry["path"] for entry in runtime["files"]]
    assert "src/keep.py" in paths
    assert "showcase/out.txt" not in paths
    assert "showcase/nested/out2.txt" not in paths


def test_parse_handles_mnemonic_and_noindex_prefixes() -> None:
    patch = """diff --git w/src/demo.py w/src/demo.py
index 1111111..2222222 100644
--- w/src/demo.py
+++ w/src/demo.py
@@ -1 +1 @@
-old
+new
diff --git 1/./notes.txt 2/./notes.txt
new file mode 100644
--- /dev/null
+++ 2/./notes.txt
@@ -0,0 +1 @@
+hello
"""
    files = parse_unified_diff(patch)
    paths = [file_patch.path for file_patch in files]
    assert "src/demo.py" in paths
    assert "notes.txt" in paths


def test_collect_patch_uses_git_pathspec_excludes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[list[str], int | None]] = []

    def fake_run(args: list[str], *, max_output_bytes: int | None = None) -> str:
        captured.append((args, max_output_bytes))
        return ""

    monkeypatch.setattr(prepare_module, "_run_git_command", fake_run)
    source_spec = {
        "mode": "working-tree",
        "include_untracked": False,
        "exclude_paths": ["showcase/**", "./tmp/**"],
    }

    patch = prepare_module.collect_patch_text_from_source(source_spec)
    assert patch == ""
    assert captured[0][0] == [
        "diff",
        "HEAD",
        "--",
        ".",
        ":(exclude,glob)showcase/**",
        ":(exclude,glob)tmp/**",
    ]
    assert captured[0][1] == prepare_module._MAX_TRACKED_PATCH_BYTES


def test_build_untracked_patch_rejects_oversized_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("x" * 16, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    def fake_run(args: list[str], *, max_output_bytes: int | None = None) -> str:
        if args == ["ls-files", "--others", "--exclude-standard"]:
            return "artifact.txt\n"
        return ""

    monkeypatch.setattr(prepare_module, "_run_git_command", fake_run)
    monkeypatch.setattr(prepare_module, "_MAX_UNTRACKED_FILE_BYTES", 8)

    with pytest.raises(RuntimeError, match="oversized untracked file"):
        prepare_module._build_untracked_patch([])


def test_build_review_context_excludes_binary_files_by_default() -> None:
    patch = """diff --git a/assets/logo.bin b/assets/logo.bin
index 1234567..89abcde 100644
Binary files a/assets/logo.bin and b/assets/logo.bin differ
diff --git a/src/keep.py b/src/keep.py
new file mode 100644
--- /dev/null
+++ b/src/keep.py
@@ -0,0 +1 @@
+print("keep")
"""
    context = _context_from_patch(patch)
    paths = [file_entry["path"] for file_entry in context["files"]]
    assert "src/keep.py" in paths
    assert "assets/logo.bin" not in paths
