from __future__ import annotations

import os
import subprocess
import sys
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

SAMPLE_PATCH_SHIFTED_HEADER = """diff --git a/src/demo.py b/src/demo.py
index 1111111..2222222 100644
--- a/src/demo.py
+++ b/src/demo.py
@@ -11,2 +11,3 @@
 def greet():
-    return \"hi\"
+    message = \"hi\"
+    return message
"""


def _context_from_patch(
    patch: str, *, include_paths: list[str] | None = None
) -> dict[str, object]:
    patch_path = Path("/tmp/prereview-context.patch")
    patch_path.write_text(patch, encoding="utf-8")
    source_spec = build_source_spec(
        patch_file=patch_path,
        git_range=None,
        include_paths=include_paths or [],
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
            "files": [file_patch.to_dict() for file_patch in runtime["files"]],
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
            "files": [file_patch.to_dict() for file_patch in runtime["files"]],
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
            "files": [file_patch.to_dict() for file_patch in runtime["files"]],
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
            "files": [file_patch.to_dict() for file_patch in runtime["files"]],
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
            "files": [file_patch.to_dict() for file_patch in runtime["files"]],
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
            "files": [file_patch.to_dict() for file_patch in runtime["files"]],
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
            "files": [file_patch.to_dict() for file_patch in runtime["files"]],
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
    assert 'copySingleButton.textContent = "Copy Prompt";' in html
    assert "Write reviewer comment text to copy." in html
    assert "reviewer-comment-buttons" not in html
    assert "data-comment-trigger='line'" in html
    assert "data-location-key='src/demo.py::" in html
    assert 'content: "💬";' in html
    assert ".line-row.has-reviewer-comment td {" not in html
    assert "buildAgentPrompt" in html
    assert "buildSingleCommentPrompt" in html
    assert "Address all of the following comments one by one." in html
    assert "Address the following comment." in html
    assert "target_context_id:" not in html
    assert " | hunk_id=" not in html
    assert "editing existing comment" in html
    assert "findCommentForLine(lineRow)" in html
    assert 'saveButton.textContent = existingComment ? "Update" : "Save";' in html
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
            "files": [file_patch.to_dict() for file_patch in runtime["files"]],
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


def test_cli_draft_annotations_subcommand_is_removed() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["draft-annotations"])
    assert excinfo.value.code == 2


def test_cli_prepare_context_subcommand_is_removed() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["prepare-context"])
    assert excinfo.value.code == 2


def test_cli_build_subcommand_is_removed() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["build"])
    assert excinfo.value.code == 2


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


def test_render_review_input_marks_uncommented_hunks_and_embeds_diff() -> None:
    context = _context_from_patch(SAMPLE_PATCH)
    runtime = recompute_runtime_from_context(context)
    anchor_id = context["files"][0]["anchors"][0]["anchor_id"]
    metadata = runtime["anchor_index"]["src/demo.py"][anchor_id]

    rendered = render_review_input(
        context,
        notes_file="review-notes.jsonl",
        anchor_states={
            anchor_id: {
                "uncommented": True,
                "changed_loc": metadata.get("changed_loc"),
                "diff_lines": [
                    "@@ -1,2 +1,3 @@",
                    " def greet():",
                    '-    return "hi"',
                    '+    message = "hi"',
                    "+    return message",
                ],
            }
        },
    )

    assert f"ANCHOR id={anchor_id} uncommented=true changed_loc=3" in rendered
    assert "DIFF_START" in rendered
    assert "@@ -1,2 +1,3 @@" in rendered
    assert '+    message = "hi"' in rendered
    assert "DIFF_END" in rendered
    assert "\nSNIPPET " not in rendered


def test_render_review_input_marks_commented_hunks_without_diff() -> None:
    context = _context_from_patch(SAMPLE_PATCH)
    anchor_id = context["files"][0]["anchors"][0]["anchor_id"]
    rendered = render_review_input(
        context,
        notes_file="review-notes.jsonl",
        anchor_states={anchor_id: {"uncommented": False, "changed_loc": 3}},
    )

    assert f"ANCHOR id={anchor_id} uncommented=false changed_loc=3" in rendered
    assert "DIFF_START" not in rendered
    assert "\nSNIPPET " in rendered


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
    artifacts_dir = tmp_path / "prereview"
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

    input_text = (artifacts_dir / "review-input.txt").read_text(encoding="utf-8")
    assert "ANCHOR id=" in input_text
    assert "uncommented=true changed_loc=3" in input_text
    assert "DIFF_START" in input_text
    assert "DIFF_END" in input_text
    assert "\nSNIPPET " not in input_text
    assert "UNCOMMENTED HUNKS START" not in input_text

    html = (artifacts_dir / "review.html").read_text(encoding="utf-8")
    assert "src/demo.py" in html
    assert "prereview-embedded-data" in html


def test_cli_install_skill_with_target_dir(tmp_path: Path) -> None:
    target_dir = tmp_path / "skills-root"

    assert main(["install-skill", "--target-dir", str(target_dir)]) == 0

    installed_dir = target_dir / "prereview-pipeline"
    assert (installed_dir / "SKILL.md").exists()
    assert (installed_dir / "assets" / "annotation-notes.template.json").exists()
    assert (installed_dir / "references" / "annotation-schema.md").exists()


def test_cli_install_skill_local_uses_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["install-skill", "--local", "--agent", "codex"]) == 0
    assert (tmp_path / ".codex" / "skills" / "prereview-pipeline" / "SKILL.md").exists()


def test_cli_install_skill_local_copilot_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["install-skill", "--local", "--agent", "copilot"]) == 0
    assert (
        tmp_path / ".github" / "skills" / "prereview-pipeline" / "SKILL.md"
    ).exists()


def test_cli_install_skill_requires_target_when_noninteractive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit) as excinfo:
        main(["install-skill"])
    assert "target folder" in str(excinfo.value)


def test_cli_install_skill_force_overwrites_existing(tmp_path: Path) -> None:
    target_dir = tmp_path / "skills-root"
    existing = target_dir / "prereview-pipeline"
    existing.mkdir(parents=True, exist_ok=True)
    (existing / "SKILL.md").write_text("stale", encoding="utf-8")

    assert main(["install-skill", "--target-dir", str(target_dir), "--force"]) == 0

    skill_text = (existing / "SKILL.md").read_text(encoding="utf-8")
    assert "name: prereview-pipeline" in skill_text


def test_cli_run_writes_rejected_notes_for_bad_jsonl(tmp_path: Path) -> None:
    patch_path = tmp_path / "change.patch"
    artifacts_dir = tmp_path / "prereview"
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
    artifacts_dir = tmp_path / "prereview"
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


def test_cli_run_prints_uncommented_loc_and_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    patch_path = tmp_path / "change.patch"
    artifacts_dir = tmp_path / "prereview"
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

    stdout = capsys.readouterr().out
    assert "Uncommented hunks: 1" in stdout
    assert "Uncommented changed LOC: 3" in stdout
    assert "Uncommented files: src/demo.py" in stdout


def test_cli_run_subcommand_is_removed() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["run"])
    assert excinfo.value.code == 2


def test_cli_default_include_paths_in_run_mode() -> None:
    args = build_parser().parse_args([])
    assert args.include == []
    assert args.artifacts_dir == Path("prereview")


def test_cli_clean_removes_workspace_outside_git(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "prereview"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "review.html").write_text("x", encoding="utf-8")

    cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        assert main(["clean"]) == 0
    finally:
        os.chdir(cwd)

    assert not artifacts_dir.exists()


def test_cli_clean_removes_workspace_and_local_exclude(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    artifacts_dir = tmp_path / "prereview"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "review.html").write_text("x", encoding="utf-8")

    exclude_path = tmp_path / ".git" / "info" / "exclude"
    exclude_path.write_text("/prereview/\n/keep-me/\n", encoding="utf-8")

    cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        assert main(["clean"]) == 0
    finally:
        os.chdir(cwd)

    assert not artifacts_dir.exists()
    updated = exclude_path.read_text(encoding="utf-8")
    assert "/prereview/" not in updated
    assert "/keep-me/" in updated


def test_recompute_runtime_include_paths_filters_to_selected_files() -> None:
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
        include_paths=["src/**"],
    )
    context = build_review_context(patch, source_spec)
    runtime = recompute_runtime_from_context(context)
    paths = [entry.path for entry in runtime["files"]]
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


def test_parse_stable_hunk_id_ignores_hunk_line_number_shifts() -> None:
    original_hunk = parse_unified_diff(SAMPLE_PATCH)[0].hunks[0]
    shifted_hunk = parse_unified_diff(SAMPLE_PATCH_SHIFTED_HEADER)[0].hunks[0]

    assert original_hunk.hunk_id != shifted_hunk.hunk_id
    assert original_hunk.stable_hunk_id == shifted_hunk.stable_hunk_id


def test_parse_stable_hunk_id_disambiguates_identical_hunks() -> None:
    patch = """diff --git a/src/repeated.py b/src/repeated.py
index 1111111..2222222 100644
--- a/src/repeated.py
+++ b/src/repeated.py
@@ -1,2 +1,2 @@
-x = 1
+x = 2
 unchanged = True
@@ -11,2 +11,2 @@
-x = 1
+x = 2
 unchanged = True
"""
    hunks = parse_unified_diff(patch)[0].hunks
    assert len(hunks) == 2
    assert hunks[0].stable_hunk_id != hunks[1].stable_hunk_id


def test_recompute_runtime_matches_anchors_across_header_shifts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context_from_patch(SAMPLE_PATCH)
    expected_anchor_id = context["files"][0]["anchors"][0]["anchor_id"]

    monkeypatch.setattr(
        prepare_module,
        "collect_patch_text_from_source",
        lambda _source_spec: SAMPLE_PATCH_SHIFTED_HEADER,
    )
    runtime = recompute_runtime_from_context(context)
    file_anchor_index = runtime["anchor_index"]["src/demo.py"]

    assert expected_anchor_id in file_anchor_index


def test_collect_patch_uses_git_pathspec_includes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[list[str], int | None]] = []

    def fake_run(args: list[str], *, max_output_bytes: int | None = None) -> str:
        captured.append((args, max_output_bytes))
        return ""

    monkeypatch.setattr(prepare_module, "_run_git_command", fake_run)
    source_spec = {
        "mode": "working-tree",
        "include_paths": ["showcase/**", "./tmp/**"],
    }

    patch = prepare_module.collect_patch_text_from_source(source_spec)
    assert patch == ""
    assert captured[0][0] == [
        "diff",
        "HEAD",
        "--",
        ":(glob)showcase/**",
        ":(glob)tmp/**",
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
        prepare_module._build_untracked_patch(["artifact.txt"])


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
