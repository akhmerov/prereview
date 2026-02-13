from __future__ import annotations

import json
from pathlib import Path

from prereview.cli import main
from prereview.diff_parser import parse_unified_diff
from prereview.draft import draft_annotations
from prereview.prepare import make_prepared_review
from prereview.renderer import render_html
from prereview.validate import validate_annotations

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


def test_validation_modes() -> None:
    prepared = make_prepared_review(SAMPLE_PATCH, {"mode": "patch-file"})
    annotations = {
        "version": "1",
        "target_prepared_review": prepared["prepared_id"],
        "files": [
            {
                "path": "src/demo.py",
                "summary": "Refactors greeting return path.",
                "comments": [
                    {
                        "line_start": 2,
                        "text": "Variable extraction improves traceability.",
                        "severity": "note",
                    }
                ],
            }
        ],
    }

    strict_report = validate_annotations(prepared, annotations, strict=True)
    assert strict_report["valid"] is True
    assert strict_report["stats"]["mapped_comments"] == 1

    bad_annotations = {
        **annotations,
        "files": [{"path": "src/demo.py", "comments": [{"line_start": 99, "text": "out of range"}]}],
    }
    non_strict_report = validate_annotations(prepared, bad_annotations, strict=False)
    assert non_strict_report["valid"] is True
    assert non_strict_report["stats"]["unmapped_comments"] == 1

    strict_bad_report = validate_annotations(prepared, bad_annotations, strict=True)
    assert strict_bad_report["valid"] is False


def test_cli_pipeline(tmp_path: Path) -> None:
    patch_path = tmp_path / "change.patch"
    prepared_path = tmp_path / "prepared-review.json"
    annotations_path = tmp_path / "annotations.json"
    report_path = tmp_path / "validation-report.json"
    html_path = tmp_path / "preview.html"

    patch_path.write_text(SAMPLE_PATCH, encoding="utf-8")

    assert main(["prepare-diff", "--patch-file", str(patch_path), "--out", str(prepared_path)]) == 0

    prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
    annotations = {
        "version": "1",
        "target_prepared_review": prepared["prepared_id"],
        "files": [
            {
                "path": "src/demo.py",
                "breadcrumbs": ["src", "demo.py"],
                "summary": "Agent annotation summary.",
                "hunks": [
                    {
                        "new_start": 2,
                        "new_end": 3,
                        "title": "Refactor notes",
                        "explanation": "Split a literal return into assignment then return.",
                        "comments": [
                            {
                                "line_start": 2,
                                "text": "Intermediate variable clarifies intent.",
                                "severity": "info",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    annotations_path.write_text(json.dumps(annotations), encoding="utf-8")

    assert (
        main(
            [
                "validate-annotations",
                "--prepared",
                str(prepared_path),
                "--annotations",
                str(annotations_path),
                "--report",
                str(report_path),
            ]
        )
        == 0
    )

    assert (
        main(
            [
                "build",
                "--prepared",
                str(prepared_path),
                "--annotations",
                str(annotations_path),
                "--output",
                str(html_path),
                "--title",
                "Pipeline Test",
            ]
        )
        == 0
    )

    rendered = html_path.read_text(encoding="utf-8")
    assert "Pipeline Test" in rendered
    assert "src/demo.py" in rendered
    assert "Intermediate variable clarifies intent." in rendered

    validation_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert validation_report["valid"] is True


def test_render_function_smoke() -> None:
    prepared = make_prepared_review(SAMPLE_PATCH, {"mode": "patch-file"})
    annotations = {
        "version": "1",
        "target_prepared_review": prepared["prepared_id"],
        "overview": [
            "Scope: 1 file changed.",
            "Primary intent: smoke-check rendering.",
        ],
        "files": [{"path": "src/demo.py", "comments": [{"line_start": 2, "text": "hello"}]}],
    }
    report = validate_annotations(prepared, annotations, strict=False)
    html = render_html(
        prepared,
        annotations,
        report,
        title="Smoke",
        max_expanded_lines=120,
        collapse_large_hunks=True,
        allow_split_hunks=True,
    )
    assert "Smoke" in html
    assert "Review Overview" in html
    assert "white-space: pre;" in html
    assert "class='code'" in html
    assert "<span class='diff-prefix'>+</span>    message = &quot;hi&quot;" in html
    assert "hello" in html


def test_draft_annotations_include_reviewer_overview_and_what_why_explanations() -> None:
    patch = """diff --git a/src/a.py b/src/a.py
new file mode 100644
--- /dev/null
+++ b/src/a.py
@@ -0,0 +1,2 @@
+def alpha():
+    return 1
diff --git a/src/b.py b/src/b.py
new file mode 100644
--- /dev/null
+++ b/src/b.py
@@ -0,0 +1,2 @@
+def beta():
+    return 2
"""
    prepared = make_prepared_review(patch, {"mode": "patch-file"})
    annotations = draft_annotations(prepared)

    overview = annotations.get("overview")
    assert isinstance(overview, list)
    assert len(overview) >= 3

    for file_entry in annotations["files"]:
        assert "What changed:" in file_entry.get("summary", "")
        assert "Why:" in file_entry.get("summary", "")
        for hunk in file_entry.get("hunks", []):
            explanation = hunk.get("explanation", "")
            assert "What changed:" in explanation
            assert "Why:" in explanation


def test_draft_annotations_keep_line_notes_rare_and_high_importance() -> None:
    low_risk_patch = """diff --git a/src/a.py b/src/a.py
new file mode 100644
--- /dev/null
+++ b/src/a.py
@@ -0,0 +1,3 @@
+def alpha():
+    value = 1
+    return value
"""
    low_risk_prepared = make_prepared_review(low_risk_patch, {"mode": "patch-file"})
    low_risk_annotations = draft_annotations(low_risk_prepared)
    low_risk_line_notes = sum(
        len(hunk.get("comments", []))
        for file_entry in low_risk_annotations["files"]
        for hunk in file_entry.get("hunks", [])
    )
    assert low_risk_line_notes == 0

    high_risk_patch = """diff --git a/src/run.py b/src/run.py
new file mode 100644
--- /dev/null
+++ b/src/run.py
@@ -0,0 +1,3 @@
+import subprocess
+result = subprocess.run(["echo", "x"], check=False)
+print(result.returncode)
"""
    high_risk_prepared = make_prepared_review(high_risk_patch, {"mode": "patch-file"})
    high_risk_annotations = draft_annotations(high_risk_prepared)
    high_risk_line_notes = [
        comment
        for file_entry in high_risk_annotations["files"]
        for hunk in file_entry.get("hunks", [])
        for comment in hunk.get("comments", [])
    ]
    assert len(high_risk_line_notes) == 1
    assert high_risk_line_notes[0]["severity"] == "warning"
    assert "Why:" in high_risk_line_notes[0]["text"]


def test_cli_draft_annotations(tmp_path: Path) -> None:
    patch_path = tmp_path / "change.patch"
    prepared_path = tmp_path / "prepared-review.json"
    annotations_path = tmp_path / "draft-annotations.json"
    patch_path.write_text(SAMPLE_PATCH, encoding="utf-8")

    assert main(["prepare-diff", "--patch-file", str(patch_path), "--out", str(prepared_path)]) == 0
    assert (
        main(
            [
                "draft-annotations",
                "--prepared",
                str(prepared_path),
                "--output",
                str(annotations_path),
            ]
        )
        == 0
    )

    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    assert annotations["version"] == "1"
    assert isinstance(annotations.get("overview"), list)
    assert annotations["files"]


def test_make_prepared_review_excludes_paths() -> None:
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
+print("keep")
"""
    prepared = make_prepared_review(
        patch,
        {"mode": "patch-file"},
        exclude_paths=["showcase/**"],
    )
    paths = [file_entry["path"] for file_entry in prepared["files"]]
    assert "src/keep.py" in paths
    assert "showcase/out.txt" not in paths
    assert "showcase/nested/out2.txt" not in paths


def test_cli_prepare_diff_exclude_path(tmp_path: Path) -> None:
    patch_path = tmp_path / "change.patch"
    prepared_path = tmp_path / "prepared-review.json"
    patch_path.write_text(
        """diff --git a/showcase/out.txt b/showcase/out.txt
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
+print("keep")
""",
        encoding="utf-8",
    )

    assert (
        main(
            [
                "prepare-diff",
                "--patch-file",
                str(patch_path),
                "--exclude-path",
                "showcase/**",
                "--out",
                str(prepared_path),
            ]
        )
        == 0
    )
    prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
    paths = [file_entry["path"] for file_entry in prepared["files"]]
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
