from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from prereview.annotations import compile_annotations_from_notes
from prereview.prepare import (
    build_review_context,
    build_source_spec,
    collect_patch_text_from_source,
)
from prereview.review_io import (
    default_review_notes_template,
    parse_review_notes_jsonl,
    rewrite_review_notes_jsonl,
    render_review_input,
    write_rejected_notes_jsonl,
)
from prereview.renderer import render_html
from prereview.util import ensure_parent, load_json, write_json, write_text
from prereview.validate import (
    evaluate_annotations,
    grouped_issues,
    materialize_annotations_for_render,
)

_MAX_UNCOMMENTED_DIFF_LINES_PER_HUNK = 80
_MAX_UNCOMMENTED_DIFF_CHARS_PER_HUNK = 8_000
_MAX_UNCOMMENTED_DIFF_TOTAL_CHARS = 48_000
_LINE_PREFIX_BY_TYPE = {"add": "+", "del": "-", "context": " "}


def _ensure_artifacts_workspace(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    gitignore_path = path / ".gitignore"
    # Keep all generated artifacts out of git status, including this file.
    write_text(gitignore_path, "*\n")


def _git_exclude_entry(path: Path) -> tuple[Path, str] | None:
    try:
        repo_root_proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
        )
        exclude_path_proc = subprocess.run(
            ["git", "rev-parse", "--git-path", "info/exclude"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return

    if repo_root_proc.returncode != 0 or exclude_path_proc.returncode != 0:
        return

    repo_root = Path(repo_root_proc.stdout.strip())
    if not repo_root.is_absolute():
        return

    try:
        relative = path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return

    normalized = relative.as_posix().strip().strip("/")
    if not normalized:
        return None

    ignore_pattern = f"/{normalized}/"
    exclude_path = Path(exclude_path_proc.stdout.strip())
    if not exclude_path.is_absolute():
        exclude_path = (Path.cwd() / exclude_path).resolve()
    return exclude_path, ignore_pattern


def _ensure_git_info_exclude(path: Path) -> None:
    entry = _git_exclude_entry(path)
    if entry is None:
        return
    exclude_path, ignore_pattern = entry

    ensure_parent(exclude_path)
    current_text = (
        exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
    )
    if ignore_pattern in current_text.splitlines():
        return

    prefix = "" if not current_text or current_text.endswith("\n") else "\n"
    with exclude_path.open("a", encoding="utf-8") as handle:
        if prefix:
            handle.write(prefix)
        handle.write(ignore_pattern + "\n")


def _remove_git_info_exclude(path: Path) -> None:
    entry = _git_exclude_entry(path)
    if entry is None:
        return
    exclude_path, ignore_pattern = entry
    if not exclude_path.exists():
        return

    lines = exclude_path.read_text(encoding="utf-8").splitlines()
    filtered = [line for line in lines if line != ignore_pattern]
    if filtered == lines:
        return

    if not filtered:
        exclude_path.write_text("", encoding="utf-8")
        return
    exclude_path.write_text("\n".join(filtered) + "\n", encoding="utf-8")


def _normalize_issue(issue: object) -> dict[str, str] | None:
    if not isinstance(issue, dict):
        return None
    level = str(issue.get("level", "warning")).strip() or "warning"
    if level not in {"warning", "error"}:
        level = "warning"
    return {
        "level": level,
        "code": str(issue.get("code", "issue")),
        "message": str(issue.get("message", "")),
        "location": str(issue.get("location", "")),
    }


def _format_hunk_header(hunk: dict[str, Any]) -> str:
    old_start = hunk.get("old_start")
    old_count = hunk.get("old_count")
    new_start = hunk.get("new_start")
    new_count = hunk.get("new_count")
    old_start_text = str(old_start) if isinstance(old_start, int) else "?"
    old_count_text = str(old_count) if isinstance(old_count, int) else "?"
    new_start_text = str(new_start) if isinstance(new_start, int) else "?"
    new_count_text = str(new_count) if isinstance(new_count, int) else "?"
    trailer = str(hunk.get("header", "")).strip()
    base = (
        f"@@ -{old_start_text},{old_count_text} +{new_start_text},{new_count_text} @@"
    )
    return f"{base} {trailer}".rstrip()


def _render_uncommented_diff_lines(
    hunk: dict[str, Any],
    *,
    max_lines: int,
    max_chars: int,
) -> tuple[list[str], int, bool]:
    rendered: list[str] = []
    used_chars = 0

    def append_line(value: str) -> bool:
        nonlocal used_chars
        projected = used_chars + len(value) + 1
        if len(rendered) >= max_lines or projected > max_chars:
            return False
        rendered.append(value)
        used_chars = projected
        return True

    if not append_line(_format_hunk_header(hunk)):
        return [], 0, True

    body_lines = hunk.get("lines", [])
    if not isinstance(body_lines, list):
        body_lines = []

    truncated = False
    for line in body_lines:
        if not isinstance(line, dict):
            continue
        line_type = line.get("type")
        if line_type not in _LINE_PREFIX_BY_TYPE:
            continue
        content = str(line.get("content", ""))
        if not append_line(_LINE_PREFIX_BY_TYPE[line_type] + content):
            truncated = True
            break

    return rendered, used_chars, truncated


def _runtime_hunks_by_stable_id(
    runtime: dict[str, Any],
) -> dict[str, dict[str, dict[str, Any]]]:
    by_file: dict[str, dict[str, dict[str, Any]]] = {}
    for file_entry in runtime.get("files", []):
        if not isinstance(file_entry, dict):
            continue
        path = file_entry.get("path")
        if not isinstance(path, str) or not path:
            continue

        per_file: dict[str, dict[str, Any]] = {}
        for hunk in file_entry.get("hunks", []):
            if not isinstance(hunk, dict):
                continue
            stable_hunk_id = hunk.get("stable_hunk_id")
            if not isinstance(stable_hunk_id, str) or not stable_hunk_id:
                continue
            per_file[stable_hunk_id] = hunk
        by_file[path] = per_file
    return by_file


def _collect_anchor_states(
    context: dict[str, Any],
    runtime: dict[str, Any],
    notes_payload: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    commented_anchor_ids = {
        anchor_id
        for anchor in notes_payload.get("anchors", [])
        if isinstance(anchor, dict)
        and isinstance((anchor_id := anchor.get("anchor_id")), str)
        and anchor_id
    }

    anchor_states: dict[str, dict[str, Any]] = {}
    anchor_index = runtime.get("anchor_index", {})
    hunk_index = _runtime_hunks_by_stable_id(runtime)
    remaining_chars_budget = _MAX_UNCOMMENTED_DIFF_TOTAL_CHARS

    for file_entry in context.get("files", []):
        if not isinstance(file_entry, dict):
            continue
        path = file_entry.get("path")
        if not isinstance(path, str) or not path:
            continue

        file_anchor_index = (
            anchor_index.get(path, {}) if isinstance(anchor_index, dict) else {}
        )
        for anchor in file_entry.get("anchors", []):
            if not isinstance(anchor, dict):
                continue
            anchor_id = anchor.get("anchor_id")
            if not isinstance(anchor_id, str) or not anchor_id:
                continue
            runtime_meta = (
                file_anchor_index.get(anchor_id)
                if isinstance(file_anchor_index, dict)
                else {}
            )
            if not isinstance(runtime_meta, dict):
                runtime_meta = {}

            state: dict[str, Any] = {
                "path": path,
                "changed_loc": runtime_meta.get("changed_loc"),
                "uncommented": anchor_id not in commented_anchor_ids,
            }

            if state["uncommented"]:
                stable_hunk_id = runtime_meta.get("stable_hunk_id")
                hunk = (
                    hunk_index.get(path, {}).get(stable_hunk_id)
                    if isinstance(stable_hunk_id, str)
                    else None
                )
                if isinstance(hunk, dict):
                    max_chars = min(
                        _MAX_UNCOMMENTED_DIFF_CHARS_PER_HUNK,
                        max(remaining_chars_budget, 0),
                    )
                    if max_chars > 0:
                        diff_lines, used_chars, truncated = (
                            _render_uncommented_diff_lines(
                                hunk,
                                max_lines=_MAX_UNCOMMENTED_DIFF_LINES_PER_HUNK,
                                max_chars=max_chars,
                            )
                        )
                        if diff_lines:
                            state["diff_lines"] = diff_lines
                            if truncated:
                                state["diff_truncated"] = True
                            remaining_chars_budget -= used_chars
                        else:
                            state["diff_omitted"] = True
                    else:
                        state["diff_omitted"] = True

            anchor_states[anchor_id] = state

    return anchor_states


def _run_cmd(args: argparse.Namespace) -> int:
    artifacts_dir = args.artifacts_dir
    _ensure_artifacts_workspace(artifacts_dir)
    _ensure_git_info_exclude(artifacts_dir)

    exclude_paths = list(args.exclude_path)
    if not artifacts_dir.is_absolute():
        artifacts_glob = artifacts_dir.as_posix().strip().lstrip("./")
        if artifacts_glob and artifacts_glob != ".":
            exclude_paths.append(f"{artifacts_glob.rstrip('/')}/**")

    source_spec = build_source_spec(
        patch_file=args.patch_file,
        git_range=args.git_range,
        use_working_tree=args.use_working_tree,
        include_untracked=args.include_untracked,
        exclude_paths=exclude_paths,
        exclude_binary=not args.include_binary,
    )
    raw_patch = collect_patch_text_from_source(source_spec)
    context = build_review_context(raw_patch, source_spec)

    context_path = artifacts_dir / "review-context.json"
    input_path = artifacts_dir / "review-input.txt"
    notes_path = artifacts_dir / "review-notes.jsonl"
    rejected_path = artifacts_dir / "rejected-notes.jsonl"
    annotations_path = artifacts_dir / "annotations.json"
    html_path = (
        args.output if args.output is not None else artifacts_dir / "review.html"
    )

    write_json(context_path, context)
    if not notes_path.exists():
        write_text(notes_path, default_review_notes_template())

    notes_payload, notes_issues, rejected_records = parse_review_notes_jsonl(
        notes_path, context
    )
    if rejected_records:
        rewrite_review_notes_jsonl(notes_path, notes_payload)
    write_rejected_notes_jsonl(rejected_path, rejected_records)

    annotations, compile_issues = compile_annotations_from_notes(context, notes_payload)
    write_json(annotations_path, annotations)

    report, runtime = evaluate_annotations(context, annotations, strict=False)
    if runtime is None:
        raise SystemExit(
            "Cannot build preview because runtime diff recomputation failed."
        )

    anchor_states = _collect_anchor_states(context, runtime, notes_payload)
    write_text(
        input_path,
        render_review_input(
            context,
            notes_file=notes_path.name,
            anchor_states=anchor_states,
        ),
    )

    extra_issues: list[dict[str, str]] = []
    for raw_issue in [*notes_issues, *compile_issues]:
        normalized = _normalize_issue(raw_issue)
        if normalized is None:
            continue
        # Rejected/sanitized notes should be reported but should not block rendering.
        if normalized["level"] == "error":
            normalized["level"] = "warning"
        extra_issues.append(normalized)

    runtime_issues = report.get("issues", [])
    normalized_runtime_issues = [
        normalized
        for raw_issue in runtime_issues
        if (normalized := _normalize_issue(raw_issue)) is not None
    ]

    combined_issues = [*extra_issues, *normalized_runtime_issues]
    report = {
        "valid": not any(issue["level"] == "error" for issue in combined_issues),
        "issues": combined_issues,
        "stats": report.get("stats", {}),
    }

    render_annotations = materialize_annotations_for_render(runtime, annotations)
    html = render_html(
        {
            "stats": runtime.get("stats", {}),
            "files": runtime.get("files", []),
        },
        render_annotations,
        report,
        title=args.title,
        max_expanded_lines=args.max_expanded_lines,
        collapse_large_hunks=args.collapse_large_hunks,
        allow_split_hunks=args.allow_split_hunks,
        embedded_data={
            "context": context,
            "annotation_notes": notes_payload,
            "annotations": annotations,
            "validation_report": report,
            "rejected_notes": rejected_records,
        },
    )
    write_text(html_path, html)

    stats = context.get("stats", {})
    uncommented_states = [
        state for state in anchor_states.values() if state.get("uncommented") is True
    ]
    uncommented_changed_loc = sum(
        int(state.get("changed_loc", 0))
        for state in uncommented_states
        if isinstance(state.get("changed_loc"), int)
    )
    uncommented_paths = sorted(
        {
            str(state.get("path"))
            for state in uncommented_states
            if isinstance(state.get("path"), str) and state.get("path")
        }
    )
    uncommented_files = ", ".join(uncommented_paths) if uncommented_paths else "(none)"
    print(
        "Prepared context "
        f"{context.get('context_id', '')} with {stats.get('files_changed', 0)} files, "
        f"+{stats.get('additions', 0)} / -{stats.get('deletions', 0)}"
    )
    print(f"Wrote agent input: {input_path}")
    print(f"Uncommented hunks: {len(uncommented_states)}")
    print(f"Uncommented changed LOC: {uncommented_changed_loc}")
    print(f"Uncommented files: {uncommented_files}")
    print(f"Parsed notes file: {notes_path}")
    print(f"Rejected notes: {len(rejected_records)} -> {rejected_path}")
    print(f"Built static preview at {html_path}")
    return 0


def _clean_cmd(args: argparse.Namespace) -> int:
    artifacts_dir = args.artifacts_dir
    _remove_git_info_exclude(artifacts_dir)

    if artifacts_dir.exists():
        if artifacts_dir.is_dir():
            shutil.rmtree(artifacts_dir)
        else:
            artifacts_dir.unlink()
        print(f"Removed artifacts workspace: {artifacts_dir}")
    else:
        print(f"No artifacts workspace found at: {artifacts_dir}")
    return 0


def _prepare_context_cmd(args: argparse.Namespace) -> int:
    if args.stdin_patch:
        raise SystemExit(
            "prepare-context does not support --stdin-patch because validate/build must recompute diff deterministically."
        )

    source_spec = build_source_spec(
        patch_file=args.patch_file,
        git_range=args.git_range,
        use_working_tree=args.use_working_tree,
        include_untracked=args.include_untracked,
        exclude_paths=args.exclude_path,
        exclude_binary=not args.include_binary,
    )
    raw_patch = collect_patch_text_from_source(source_spec)
    context = build_review_context(raw_patch, source_spec)
    write_json(args.out, context)

    stats = context["stats"]
    print(
        f"Prepared context {context['context_id']} with {stats['files_changed']} files, "
        f"+{stats['additions']} / -{stats['deletions']} -> {args.out}"
    )
    return 0


def _build_validation_failure_message(
    args: argparse.Namespace, report: dict[str, object]
) -> str:
    grouped = grouped_issues(report)
    errors = len(grouped.get("error", []))
    warnings = len(grouped.get("warning", []))
    issues = report.get("issues", [])
    issue_list = issues if isinstance(issues, list) else []

    lines = [f"Build validation failed: {errors} errors, {warnings} warnings."]
    for issue in issue_list[:20]:
        if not isinstance(issue, dict):
            continue
        lines.append(
            f"- [{issue.get('level', 'warning')}] {issue.get('code', 'issue')}: "
            f"{issue.get('message', '')} ({issue.get('location', '')})"
        )
    if len(issue_list) > 20:
        lines.append(f"- ... {len(issue_list) - 20} more issues")

    if args.notes is not None:
        input_flag = "--notes"
        input_path = args.notes
        input_label = "Notes file"
        action_subject = "notes"
    else:
        input_flag = "--annotations"
        input_path = args.annotations
        input_label = "Annotations file"
        action_subject = "annotations"

    lines.insert(
        1,
        f"Agent action: update {action_subject} to resolve the validation issues below, then rerun build.",
    )
    lines.append(f"Context file: {args.context}")
    lines.append(f"{input_label}: {input_path}")
    lines.append("Rerun after fixes:")
    lines.append(
        f"prereview build --context {args.context} {input_flag} {input_path} --output {args.output}"
    )
    return "\n".join(lines)


def _build_cmd(args: argparse.Namespace) -> int:
    context = load_json(args.context)
    notes = None
    compile_issues: list[dict[str, str]] = []

    if args.notes is not None:
        notes = load_json(args.notes)
        annotations, compile_issues = compile_annotations_from_notes(context, notes)
    else:
        annotations = load_json(args.annotations)

    if args.strict:
        for issue in compile_issues:
            if issue.get("level") == "warning":
                issue["level"] = "error"

    report, runtime = evaluate_annotations(context, annotations, strict=args.strict)
    if compile_issues:
        combined_issues = [*compile_issues, *report.get("issues", [])]
        report = {
            "valid": not any(
                issue.get("level") == "error"
                for issue in combined_issues
                if isinstance(issue, dict)
            ),
            "issues": combined_issues,
            "stats": report.get("stats", {}),
        }

    if not report["valid"]:
        raise SystemExit(_build_validation_failure_message(args, report))

    if runtime is None:
        raise SystemExit(
            "Cannot build preview because runtime diff recomputation failed."
        )

    render_annotations = materialize_annotations_for_render(runtime, annotations)
    html = render_html(
        {
            "stats": runtime.get("stats", {}),
            "files": runtime.get("files", []),
        },
        render_annotations,
        report,
        title=args.title,
        max_expanded_lines=args.max_expanded_lines,
        collapse_large_hunks=args.collapse_large_hunks,
        allow_split_hunks=args.allow_split_hunks,
        embedded_data={
            "context": context,
            "annotation_notes": notes,
            "annotations": annotations,
            "validation_report": report,
        },
    )
    write_text(args.output, html)

    consumed_paths: list[Path] = []
    if not args.keep_inputs:
        build_input = args.notes if args.notes is not None else args.annotations
        for candidate in {args.context, build_input}:
            try:
                candidate.unlink()
            except FileNotFoundError:
                continue
            consumed_paths.append(candidate)

    print(f"Built static preview at {args.output}")
    if consumed_paths:
        consumed_display = ", ".join(str(path) for path in consumed_paths)
        print(f"Consumed intermediate artifacts: {consumed_display}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prereview",
        description="Generate rich local HTML previews for agent-generated code diffs.",
    )
    run_source_group = parser.add_mutually_exclusive_group()
    run_source_group.add_argument(
        "--patch-file", type=Path, help="Read unified diff from file."
    )
    run_source_group.add_argument(
        "--git-range", help="Generate diff from git (single ref or range)."
    )
    parser.add_argument(
        "--use-working-tree",
        action="store_true",
        help="Force working tree diff against HEAD (default when no source is given).",
    )
    parser.add_argument(
        "--include-untracked",
        action="store_true",
        help="Include untracked files as additions (default: disabled).",
    )
    parser.add_argument(
        "--include-binary",
        action="store_true",
        help="Include binary file changes. By default, binary diffs are excluded.",
    )
    parser.add_argument(
        "--exclude-path",
        action="append",
        default=[],
        help="Exclude paths matching this glob (repeatable).",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("prereview"),
        help="Directory for generated review artifacts.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output HTML file path (default: ARTIFACTS_DIR/review.html).",
    )
    parser.add_argument("--title", default="Prereview Report", help="Report title.")
    parser.add_argument(
        "--max-expanded-lines",
        type=int,
        default=120,
        help="Hunks longer than this are collapsed by default.",
    )
    parser.add_argument(
        "--no-collapse-large-hunks",
        dest="collapse_large_hunks",
        action="store_false",
        help="Disable default collapsing of large hunks.",
    )
    parser.add_argument(
        "--no-split-hunks",
        dest="allow_split_hunks",
        action="store_false",
        help="Ignore split hunk annotations and only map by hunk id.",
    )

    subparsers = parser.add_subparsers(dest="command")

    clean_parser = subparsers.add_parser(
        "clean",
        help="Delete artifacts workspace and remove it from local git excludes.",
    )
    clean_parser.set_defaults(func=_clean_cmd)

    prepare_parser = subparsers.add_parser(
        "prepare-context", help="Prepare reviewer-focused context input."
    )
    source_group = prepare_parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--patch-file", type=Path, help="Read unified diff from file."
    )
    source_group.add_argument(
        "--stdin-patch",
        action="store_true",
        help="(unsupported) kept for explicit error message.",
    )
    source_group.add_argument(
        "--git-range", help="Generate diff from a git range (e.g. HEAD~1..HEAD)."
    )
    prepare_parser.add_argument(
        "--use-working-tree",
        action="store_true",
        help="Force working tree diff against HEAD. This is the default when no source option is provided.",
    )
    prepare_parser.add_argument(
        "--include-untracked",
        action="store_true",
        help="Include untracked files as additions.",
    )
    prepare_parser.add_argument(
        "--include-binary",
        action="store_true",
        help="Include binary file changes. By default, binary diffs are excluded from review context.",
    )
    prepare_parser.add_argument(
        "--exclude-path",
        action="append",
        default=[],
        help="Exclude paths matching this glob from context generation (repeatable, e.g. 'showcase/**').",
    )
    prepare_parser.add_argument(
        "--out", type=Path, required=True, help="Output review-context JSON path."
    )
    prepare_parser.set_defaults(func=_prepare_context_cmd)

    build_parser = subparsers.add_parser(
        "build",
        help="Build static HTML from context + notes (or legacy annotations) with validation.",
    )
    build_parser.add_argument(
        "--context", type=Path, required=True, help="Path to review-context JSON."
    )
    input_group = build_parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--notes",
        type=Path,
        help="Path to annotation notes JSON (recommended; compiled to annotations during build).",
    )
    input_group.add_argument(
        "--annotations",
        type=Path,
        help="Path to canonical annotations JSON (legacy input mode).",
    )
    build_parser.add_argument(
        "--output",
        type=Path,
        default=Path("review.html"),
        help="Output HTML file path at repository root by default.",
    )
    build_parser.add_argument(
        "--title", default="Prereview Report", help="Report title."
    )
    build_parser.add_argument(
        "--keep-inputs",
        action="store_true",
        help="Do not delete context and input JSON files after build.",
    )
    build_parser.add_argument(
        "--max-expanded-lines",
        type=int,
        default=120,
        help="Hunks longer than this are collapsed by default.",
    )
    build_parser.add_argument(
        "--no-collapse-large-hunks",
        dest="collapse_large_hunks",
        action="store_false",
        help="Disable default collapsing of large hunks.",
    )
    build_parser.add_argument(
        "--no-split-hunks",
        dest="allow_split_hunks",
        action="store_false",
        help="Ignore split hunk annotations and only map by hunk id.",
    )
    build_parser.add_argument(
        "--strict",
        action="store_true",
        dest="strict",
        help="Treat unresolved anchors/files as errors during build validation (default).",
    )
    build_parser.add_argument(
        "--no-strict",
        action="store_false",
        dest="strict",
        help="Downgrade unknown anchors/files to warnings during build validation.",
    )
    build_parser.set_defaults(
        collapse_large_hunks=True, allow_split_hunks=True, strict=True
    )
    build_parser.set_defaults(func=_build_cmd)

    parser.set_defaults(
        collapse_large_hunks=True, allow_split_hunks=True, func=_run_cmd
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
