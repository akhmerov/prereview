from __future__ import annotations

import argparse
import sys
from pathlib import Path

from prereview.draft import draft_annotations
from prereview.prepare import (
    build_review_context,
    build_source_spec,
    collect_patch_text_from_source,
)
from prereview.renderer import render_html
from prereview.util import load_json, write_json, write_text
from prereview.validate import (
    evaluate_annotations,
    grouped_issues,
    materialize_annotations_for_render,
)


def _prepare_context_cmd(args: argparse.Namespace) -> int:
    if args.stdin_patch:
        raise SystemExit("prepare-context does not support --stdin-patch because validate/build must recompute diff deterministically.")

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


def _validate_cmd(args: argparse.Namespace) -> int:
    context = load_json(args.context)
    annotations = load_json(args.annotations)
    report, _ = evaluate_annotations(context, annotations, strict=args.strict)

    if args.report is not None:
        write_json(args.report, report)

    grouped = grouped_issues(report)
    errors = len(grouped.get("error", []))
    warnings = len(grouped.get("warning", []))
    print(f"Validation: {errors} errors, {warnings} warnings")

    for issue in report["issues"][:40]:
        print(f"- [{issue['level']}] {issue['code']}: {issue['message']} ({issue['location']})")

    return 0 if report["valid"] else 1


def _draft_cmd(args: argparse.Namespace) -> int:
    context = load_json(args.context)
    annotations = draft_annotations(context)
    write_json(args.output, annotations)
    print(f"Wrote draft annotations for {len(annotations['files'])} files -> {args.output}")
    return 0


def _build_cmd(args: argparse.Namespace) -> int:
    context = load_json(args.context)
    annotations = load_json(args.annotations)
    report, runtime = evaluate_annotations(context, annotations, strict=args.strict)

    if not report["valid"]:
        grouped = grouped_issues(report)
        errors = len(grouped.get("error", []))
        warnings = len(grouped.get("warning", []))
        message = f"Cannot build preview due to validation issues ({errors} errors, {warnings} warnings)."
        raise SystemExit(message)

    if runtime is None:
        raise SystemExit("Cannot build preview because runtime diff recomputation failed.")

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
    )
    write_text(args.output, html)

    print(f"Built static preview at {args.output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prereview",
        description="Generate rich local HTML previews for agent-generated code diffs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare-context", help="Prepare reviewer-focused context input.")
    source_group = prepare_parser.add_mutually_exclusive_group()
    source_group.add_argument("--patch-file", type=Path, help="Read unified diff from file.")
    source_group.add_argument("--stdin-patch", action="store_true", help="(unsupported) kept for explicit error message.")
    source_group.add_argument("--git-range", help="Generate diff from a git range (e.g. HEAD~1..HEAD).")
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
    prepare_parser.add_argument("--out", type=Path, required=True, help="Output review-context JSON path.")
    prepare_parser.set_defaults(func=_prepare_context_cmd)

    draft_parser = subparsers.add_parser("draft-annotations", help="Generate draft annotations from review context.")
    draft_parser.add_argument("--context", type=Path, required=True, help="Path to review-context JSON.")
    draft_parser.add_argument("--output", type=Path, required=True, help="Output path for draft annotations JSON.")
    draft_parser.set_defaults(func=_draft_cmd)

    validate_parser = subparsers.add_parser("validate-annotations", help="Validate anchor-based annotations.")
    validate_parser.add_argument("--context", type=Path, required=True, help="Path to review-context JSON.")
    validate_parser.add_argument("--annotations", type=Path, required=True, help="Path to annotations JSON.")
    validate_parser.add_argument("--report", type=Path, help="Optional machine-readable validation report path.")
    validate_parser.add_argument(
        "--no-strict",
        action="store_false",
        dest="strict",
        help="Downgrade unknown anchors/files to warnings.",
    )
    validate_parser.set_defaults(strict=True)
    validate_parser.set_defaults(func=_validate_cmd)

    build_parser = subparsers.add_parser("build", help="Build static HTML from context and annotations.")
    build_parser.add_argument("--context", type=Path, required=True, help="Path to review-context JSON.")
    build_parser.add_argument("--annotations", type=Path, required=True, help="Path to annotations JSON.")
    build_parser.add_argument("--output", type=Path, default=Path("prereview.html"), help="Output HTML file path.")
    build_parser.add_argument("--title", default="Prereview Report", help="Report title.")
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
        help="Enable strict validation before rendering.",
    )
    build_parser.set_defaults(collapse_large_hunks=True, allow_split_hunks=True)
    build_parser.set_defaults(func=_build_cmd)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
