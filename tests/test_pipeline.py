from __future__ import annotations

import json
from pathlib import Path

from prereview.cli import main
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
    assert "hello" in html
