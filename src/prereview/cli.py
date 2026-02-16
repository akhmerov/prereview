from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

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
    write_text(
        input_path,
        render_review_input(context, notes_file=notes_path.name),
    )
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
    print(
        "Prepared context "
        f"{context.get('context_id', '')} with {stats.get('files_changed', 0)} files, "
        f"+{stats.get('additions', 0)} / -{stats.get('deletions', 0)}"
    )
    print(f"Wrote agent input: {input_path}")
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
