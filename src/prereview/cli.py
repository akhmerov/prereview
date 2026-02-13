from __future__ import annotations

import argparse
import sys
from pathlib import Path

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


def _build_validation_failure_message(args: argparse.Namespace, report: dict[str, object]) -> str:
    grouped = grouped_issues(report)
    errors = len(grouped.get("error", []))
    warnings = len(grouped.get("warning", []))
    issues = report.get("issues", [])
    issue_list = issues if isinstance(issues, list) else []

    lines = [
        f"Build validation failed: {errors} errors, {warnings} warnings.",
        "Agent action: update annotations to resolve the validation issues below, then rerun build.",
    ]
    for issue in issue_list[:20]:
        if not isinstance(issue, dict):
            continue
        lines.append(
            f"- [{issue.get('level', 'warning')}] {issue.get('code', 'issue')}: "
            f"{issue.get('message', '')} ({issue.get('location', '')})"
        )
    if len(issue_list) > 20:
        lines.append(f"- ... {len(issue_list) - 20} more issues")

    lines.append(f"Context file: {args.context}")
    lines.append(f"Annotations file: {args.annotations}")
    lines.append("Rerun after fixes:")
    lines.append(
        f"prereview build --context {args.context} --annotations {args.annotations} --output {args.output}"
    )
    return "\n".join(lines)


def _build_cmd(args: argparse.Namespace) -> int:
    context = load_json(args.context)
    annotations = load_json(args.annotations)
    report, runtime = evaluate_annotations(context, annotations, strict=args.strict)

    if not report["valid"]:
        raise SystemExit(_build_validation_failure_message(args, report))

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
        embedded_data={
            "context": context,
            "annotations": annotations,
            "validation_report": report,
        },
    )
    write_text(args.output, html)

    consumed_paths: list[Path] = []
    if not args.keep_inputs:
        for candidate in {args.context, args.annotations}:
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

    build_parser = subparsers.add_parser(
        "build",
        help="Build static HTML from context and annotations (includes validation).",
    )
    build_parser.add_argument("--context", type=Path, required=True, help="Path to review-context JSON.")
    build_parser.add_argument("--annotations", type=Path, required=True, help="Path to annotations JSON.")
    build_parser.add_argument(
        "--output",
        type=Path,
        default=Path("review.html"),
        help="Output HTML file path at repository root by default.",
    )
    build_parser.add_argument("--title", default="Prereview Report", help="Report title.")
    build_parser.add_argument(
        "--keep-inputs",
        action="store_true",
        help="Do not delete context and annotations JSON files after build.",
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
    build_parser.set_defaults(collapse_large_hunks=True, allow_split_hunks=True, strict=True)
    build_parser.set_defaults(func=_build_cmd)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
