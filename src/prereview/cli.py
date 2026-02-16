from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from prereview.annotations import compile_annotations_from_notes
from prereview.models import FilePatch, Hunk
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
from prereview.skill_install import (
    AGENT_CHOICES,
    SKILL_NAME,
    install_packaged_skill,
    local_target_root,
)
from prereview.renderer import render_html
from prereview.util import ensure_parent, write_json, write_text
from prereview.validate import evaluate_annotations, materialize_annotations_for_render

_MAX_UNCOMMENTED_DIFF_LINES_PER_HUNK = 80
_MAX_UNCOMMENTED_DIFF_CHARS_PER_HUNK = 8_000
_MAX_UNCOMMENTED_DIFF_TOTAL_CHARS = 48_000
_LINE_PREFIX_BY_TYPE = {"add": "+", "del": "-", "context": " "}
_DEFAULT_REPORT_TITLE = "Prereview Report"
_DEFAULT_MAX_EXPANDED_LINES = 120


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
    level = issue["level"].strip() if "level" in issue else "warning"
    level = level or "warning"
    if level not in {"warning", "error"}:
        level = "warning"
    return {
        "level": level,
        "code": issue["code"] if "code" in issue else "issue",
        "message": issue["message"] if "message" in issue else "",
        "location": issue["location"] if "location" in issue else "",
    }


def _format_hunk_header(hunk: Hunk) -> str:
    old_start_text = hunk.old_start
    old_count_text = hunk.old_count
    new_start_text = hunk.new_start
    new_count_text = hunk.new_count
    trailer = hunk.header.strip()
    base = (
        f"@@ -{old_start_text},{old_count_text} +{new_start_text},{new_count_text} @@"
    )
    return f"{base} {trailer}".rstrip()


def _render_uncommented_diff_lines(
    hunk: Hunk,
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

    body_lines = hunk.lines

    truncated = False
    for line in body_lines:
        line_type = line.line_type
        if line_type not in _LINE_PREFIX_BY_TYPE:
            continue
        content = line.content
        if not append_line(_LINE_PREFIX_BY_TYPE[line_type] + content):
            truncated = True
            break

    return rendered, used_chars, truncated


def _runtime_hunks_by_stable_id(
    runtime: dict[str, Any],
) -> dict[str, dict[str, Hunk]]:
    return {
        file_entry.path: {hunk.stable_hunk_id: hunk for hunk in file_entry.hunks}
        for file_entry in runtime["files"]
    }


def _runtime_files_payload(runtime_files: list[FilePatch]) -> list[dict[str, object]]:
    return [file_patch.to_dict() for file_patch in runtime_files]


def _collect_anchor_states(
    context: dict[str, Any],
    runtime: dict[str, Any],
    notes_payload: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    commented_anchor_ids = {
        anchor["anchor_id"]
        for anchor in notes_payload["anchors"]
        if anchor["anchor_id"]
    }

    anchor_states: dict[str, dict[str, Any]] = {}
    anchor_index = runtime["anchor_index"]
    hunk_index = _runtime_hunks_by_stable_id(runtime)
    remaining_chars_budget = _MAX_UNCOMMENTED_DIFF_TOTAL_CHARS

    for file_entry in context["files"]:
        path = file_entry["path"]
        file_anchor_index = anchor_index[path]
        for anchor in file_entry["anchors"]:
            anchor_id = anchor["anchor_id"]
            runtime_meta = file_anchor_index[anchor_id]

            state: dict[str, Any] = {
                "path": path,
                "changed_loc": runtime_meta["changed_loc"],
                "uncommented": anchor_id not in commented_anchor_ids,
            }

            if state["uncommented"]:
                stable_hunk_id = runtime_meta["stable_hunk_id"]
                hunk = hunk_index[path][stable_hunk_id]
                if hunk:
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

    source_spec = build_source_spec(
        patch_file=args.patch_file,
        git_range=args.git_range,
        include_paths=list(args.include),
    )
    raw_patch = collect_patch_text_from_source(source_spec)
    context = build_review_context(raw_patch, source_spec)

    context_path = artifacts_dir / "review-context.json"
    input_path = artifacts_dir / "review-input.txt"
    notes_path = artifacts_dir / "review-notes.jsonl"
    rejected_path = artifacts_dir / "rejected-notes.jsonl"
    annotations_path = artifacts_dir / "annotations.json"
    html_path = artifacts_dir / "review.html"

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

    runtime_issues = report["issues"]
    normalized_runtime_issues = [
        normalized
        for raw_issue in runtime_issues
        if (normalized := _normalize_issue(raw_issue)) is not None
    ]

    combined_issues = [*extra_issues, *normalized_runtime_issues]
    report = {
        "valid": not any(issue["level"] == "error" for issue in combined_issues),
        "issues": combined_issues,
        "stats": report["stats"],
    }

    render_annotations = materialize_annotations_for_render(runtime, annotations)
    html = render_html(
        {
            "stats": runtime["stats"],
            "files": _runtime_files_payload(runtime["files"]),
        },
        render_annotations,
        report,
        title=_DEFAULT_REPORT_TITLE,
        max_expanded_lines=_DEFAULT_MAX_EXPANDED_LINES,
        collapse_large_hunks=True,
        allow_split_hunks=True,
        embedded_data={
            "context": context,
            "annotation_notes": notes_payload,
            "annotations": annotations,
            "validation_report": report,
            "rejected_notes": rejected_records,
        },
    )
    write_text(html_path, html)

    stats = context["stats"]
    uncommented_states = [
        state for state in anchor_states.values() if state["uncommented"]
    ]
    uncommented_changed_loc = sum(state["changed_loc"] for state in uncommented_states)
    uncommented_paths = sorted(
        {state["path"] for state in uncommented_states if state["path"]}
    )
    uncommented_files = ", ".join(uncommented_paths) if uncommented_paths else "(none)"
    print(
        "Prepared context "
        f"{context['context_id']} with {stats['files_changed']} files, "
        f"+{stats['additions']} / -{stats['deletions']}"
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


def _prompt_target_dir(agent: str) -> Path:
    if not sys.stdin.isatty():
        raise SystemExit(
            "install-skill needs a target folder in non-interactive mode. "
            "Use --target-dir or --local."
        )
    target_text = input(f"Target folder for {agent} skills: ").strip()
    if not target_text:
        raise SystemExit("Target folder is required.")
    return Path(target_text)


def _install_skill_cmd(args: argparse.Namespace) -> int:
    if args.local:
        target_root = local_target_root(args.agent, project_root=Path.cwd())
    elif args.target_dir is not None:
        target_root = args.target_dir
    else:
        target_root = _prompt_target_dir(args.agent)

    install_path = target_root.expanduser().resolve() / SKILL_NAME
    force = args.force
    if install_path.exists() and not force:
        if not sys.stdin.isatty():
            raise SystemExit(
                f"Skill already exists at {install_path}; rerun with --force."
            )
        overwrite = (
            input(f"Skill already exists at {install_path}. Overwrite? [y/N]: ")
            .strip()
            .lower()
        )
        if overwrite not in {"y", "yes"}:
            print("Installation cancelled.")
            return 1
        force = True

    installed_path = install_packaged_skill(target_root=target_root, force=force)
    print(f"Installed {SKILL_NAME} for {args.agent} at {installed_path}")
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
        "--include",
        action="append",
        default=[],
        help=(
            "Include only paths matching this glob (repeatable). "
            "When set, matching tracked and untracked files are included."
        ),
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("prereview"),
        help="Directory for generated review artifacts.",
    )
    subparsers = parser.add_subparsers(dest="command")

    clean_parser = subparsers.add_parser(
        "clean",
        help="Delete artifacts workspace and remove it from local git excludes.",
    )
    clean_parser.set_defaults(func=_clean_cmd)

    install_skill_parser = subparsers.add_parser(
        "install-skill",
        help="Install bundled prereview skill files into an agent skills folder.",
    )
    install_skill_parser.add_argument(
        "--agent",
        choices=list(AGENT_CHOICES),
        default="codex",
        help="Agent type; used for default local target root.",
    )
    target_group = install_skill_parser.add_mutually_exclusive_group()
    target_group.add_argument(
        "--local",
        action="store_true",
        help=(
            "Install into a local project folder derived from CWD: "
            "codex=.codex/skills, claude=.claude/skills, copilot=.github/skills."
        ),
    )
    target_group.add_argument(
        "--target-dir",
        type=Path,
        help="Install into this skills root directory (skip interactive prompt).",
    )
    install_skill_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing destination folder without confirmation.",
    )
    install_skill_parser.set_defaults(func=_install_skill_cmd)

    parser.set_defaults(func=_run_cmd)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
