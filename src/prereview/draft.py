from __future__ import annotations

from typing import Any


def _line_excerpt(value: str, limit: int = 72) -> str:
    compact = " ".join(value.strip().split())
    if not compact:
        return "<empty>"
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _count_hunk_lines(hunk: dict[str, Any]) -> tuple[int, int]:
    additions = 0
    deletions = 0
    for line in hunk.get("lines", []):
        if not isinstance(line, dict):
            continue
        line_type = line.get("type")
        if line_type == "add":
            additions += 1
        elif line_type == "del":
            deletions += 1
    return additions, deletions


def _file_kind(path: str) -> str:
    lowered = path.lower()
    if lowered.endswith(".py"):
        return "Python module"
    if lowered.endswith((".md", ".rst")):
        return "documentation file"
    if lowered.endswith((".toml", ".yaml", ".yml", ".json", ".lock", ".ini")):
        return "configuration file"
    if lowered.endswith((".html", ".css", ".js", ".ts")):
        return "frontend file"
    if lowered.endswith(".conda"):
        return "package artifact"
    return "source file"


def _collect_added_lines(hunk: dict[str, Any]) -> list[tuple[int, str]]:
    collected: list[tuple[int, str]] = []
    for line in hunk.get("lines", []):
        if not isinstance(line, dict):
            continue
        if line.get("type") != "add":
            continue
        new_line = line.get("new_line")
        if not isinstance(new_line, int):
            continue
        content = str(line.get("content", "")).strip()
        if not content:
            continue
        collected.append((new_line, content))
    return collected


def _extract_signals(hunk: dict[str, Any], *, limit: int = 2) -> list[str]:
    added_lines = [content for _, content in _collect_added_lines(hunk)]

    signals: list[str] = []
    high_priority_prefixes = ("def ", "class ", "return ", "if ", "for ", "while ", "with ")
    medium_priority_prefixes = ("raise ", "try:", "except ", "match ", "case ")
    low_priority_prefixes = ("import ", "from ")

    def add_matching(prefixes: tuple[str, ...]) -> None:
        for content in added_lines:
            if any(content.startswith(prefix) for prefix in prefixes):
                signal = _line_excerpt(content)
                if signal not in signals:
                    signals.append(signal)
                if len(signals) >= limit:
                    return

    add_matching(high_priority_prefixes)
    if len(signals) < limit:
        add_matching(medium_priority_prefixes)
    if len(signals) < limit:
        for content in added_lines:
            if "=" in content and not content.startswith("#"):
                signal = _line_excerpt(content)
                if signal not in signals:
                    signals.append(signal)
                if len(signals) >= limit:
                    break
    if len(signals) < limit:
        add_matching(low_priority_prefixes)

    if not signals:
        if added_lines:
            signals.append(_line_excerpt(added_lines[0]))
    return signals


def _why_for_path(path: str) -> str:
    lowered = path.lower()
    if lowered.startswith("tests/"):
        return "to add regression coverage and keep behavior stable"
    if lowered.startswith("skills/"):
        return "to improve agent workflow guidance for reviewer-focused output"
    if lowered.endswith("prepare.py"):
        return "to improve change-set scoping and reduce generated-artifact noise"
    if lowered.endswith("diff_parser.py"):
        return "to normalize diff paths and avoid incorrect file labels in reviews"
    if lowered.endswith("renderer.py"):
        return "to improve readability of review output for humans"
    if lowered.endswith("cli.py"):
        return "to expose improved review workflow controls to users"
    if lowered.endswith("draft.py"):
        return "to generate reviewer-relevant explanations with less annotation noise"
    return "to improve review clarity and maintainability"


def _file_summary(path: str, status: str, additions: int, deletions: int, hunks: int) -> str:
    kind = _file_kind(path)
    why = _why_for_path(path)
    return (
        f"What changed: {status.lower()} {kind} with {hunks} hunk(s), +{additions}/-{deletions}. "
        f"Why: {why}."
    )


def _high_importance_anchor(hunk: dict[str, Any]) -> tuple[int, str, str] | None:
    patterns: list[tuple[str, str]] = [
        ("check=False", "it changes subprocess failure behavior"),
        ("shell=True", "it changes command execution safety assumptions"),
        ("subprocess.", "it changes process execution behavior"),
        ("PurePosixPath", "it changes path matching and filtering semantics"),
        ("exclude_path", "it changes which files enter review scope"),
        ("raise ", "it changes error propagation behavior"),
        ("except ", "it changes error handling behavior"),
        ("strict", "it changes validation strictness behavior"),
        ("re.compile(", "it changes parsing pattern behavior"),
    ]
    for line_no, content in _collect_added_lines(hunk):
        lowered = content.lower()
        for token, reason in patterns:
            if token.lower() in lowered:
                return line_no, _line_excerpt(content), reason
    return None


def _build_overview(prepared: dict[str, Any], touched_files: list[dict[str, Any]]) -> list[str]:
    stats = prepared.get("stats", {})
    files_changed = int(stats.get("files_changed", 0))
    additions = int(stats.get("additions", 0))
    deletions = int(stats.get("deletions", 0))

    overview: list[str] = [
        f"Scope: {files_changed} file(s), +{additions}/-{deletions} lines in this review set.",
    ]

    if touched_files:
        largest = sorted(
            touched_files,
            key=lambda file_entry: int(file_entry.get("additions", 0)) + int(file_entry.get("deletions", 0)),
            reverse=True,
        )[:3]
        largest_text = ", ".join(
            f"{entry['path']} (+{entry['additions']}/-{entry['deletions']})" for entry in largest
        )
        overview.append(f"Largest deltas: {largest_text}.")

        why_signals: list[str] = []
        for entry in touched_files:
            why = _why_for_path(str(entry.get("path", "")))
            if why not in why_signals:
                why_signals.append(why)
            if len(why_signals) >= 3:
                break
        if why_signals:
            overview.append(f"Primary intent: {', '.join(why_signals)}.")

    overview.append(
        "Reviewer focus: verify behavior and rationale in hunk explanations; line notes are reserved for high-importance risk anchors."
    )
    return overview


def draft_annotations(prepared: dict[str, Any], *, max_hunks_per_file: int = 1) -> dict[str, Any]:
    prepared_id = prepared.get("prepared_id")
    if not isinstance(prepared_id, str) or not prepared_id:
        raise ValueError("Prepared review is missing prepared_id.")

    if max_hunks_per_file < 1:
        raise ValueError("max_hunks_per_file must be at least 1.")

    annotations: dict[str, Any] = {
        "version": "1",
        "target_prepared_review": prepared_id,
        "overview": [],
        "files": [],
    }
    touched_files: list[dict[str, Any]] = []

    for file_entry in prepared.get("files", []):
        if not isinstance(file_entry, dict):
            continue
        path = file_entry.get("path")
        if not isinstance(path, str) or not path:
            continue

        additions = int(file_entry.get("additions", 0))
        deletions = int(file_entry.get("deletions", 0))
        status = str(file_entry.get("status", "modified"))
        hunks_data = [h for h in file_entry.get("hunks", []) if isinstance(h, dict)]
        touched_files.append(
            {
                "path": path,
                "additions": additions,
                "deletions": deletions,
            }
        )

        file_annotation: dict[str, Any] = {
            "path": path,
            "breadcrumbs": [segment for segment in path.split("/") if segment],
            "summary": _file_summary(path, status, additions, deletions, len(hunks_data)),
            "hunks": [],
            "comments": [],
        }

        selected_hunks: list[dict[str, Any]] = []
        for hunk in hunks_data:
            selected_hunks.append(hunk)
            if len(selected_hunks) >= max_hunks_per_file:
                break

        line_note_emitted = False
        for index, hunk in enumerate(selected_hunks, start=1):
            new_start = hunk.get("new_start")
            new_count = hunk.get("new_count", 1)
            if isinstance(new_start, int) and isinstance(new_count, int):
                new_end = new_start + max(new_count - 1, 0)
            else:
                new_start = 1
                new_end = 1

            hunk_additions, hunk_deletions = _count_hunk_lines(hunk)
            signals = _extract_signals(hunk)
            if signals:
                signals_text = "; ".join(f"`{signal}`" for signal in signals)
            else:
                signals_text = "localized line-level adjustments"
            why = _why_for_path(path)
            comments: list[dict[str, Any]] = []

            if not line_note_emitted:
                anchor = _high_importance_anchor(hunk)
            else:
                anchor = None
            if anchor is not None:
                line_no, excerpt, reason = anchor
                comments.append(
                    {
                        "line_start": line_no,
                        "text": (
                            f"What changed: high-importance anchor `{excerpt}` in {path}. "
                            f"Why: {reason}."
                        ),
                        "severity": "warning",
                        "author": "prereview-draft",
                    }
                )
                line_note_emitted = True

            hunk_annotation = {
                "hunk_id": hunk.get("hunk_id"),
                "new_start": new_start,
                "new_end": new_end,
                "title": f"{path} hunk {index}",
                "explanation": (
                    f"What changed: lines {new_start}-{new_end} change +{hunk_additions}/-{hunk_deletions}, "
                    f"with notable logic around {signals_text}. "
                    f"Why: {why}."
                ),
                "comments": comments,
            }
            file_annotation["hunks"].append(hunk_annotation)

        annotations["files"].append(file_annotation)

    annotations["overview"] = _build_overview(prepared, touched_files)
    return annotations
