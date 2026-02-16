---
name: prereview-pipeline
description: >-
  Use this skill when the user wants a reliable local preview workflow for code review artifacts:
  generate reviewer context, author reviewer-relevant notes, and build static HTML with
  validation.
---

# Prereview Pipeline

Use this workflow when the task is to generate local rich previews from agent-generated code changes.

## Trigger examples

- "Prepare context first, then annotate, then build review HTML"
- "Generate prereview output from my local changes"
- "Use staged workflow for code preview"

## Required flow

1. Run `prereview`.
2. Read `review/review-input.txt`.
3. Write notes to `review/review-notes.jsonl`.
4. Run `prereview` again to parse notes, validate/sanitize, and rebuild `review/review.html`.

`prereview` performs end-to-end execution in one command:
- recompute context from current diff;
- write agent-facing `review/review-input.txt`;
- parse `review/review-notes.jsonl`;
- keep only valid note records in `review/review-notes.jsonl`;
- move malformed/unmappable note lines into `review/rejected-notes.jsonl`;
- build `review/review.html` with validation issues shown in the report.

Canonical JSON artifacts are internal (`review/review-context.json`, `review/annotations.json`) and should not be manually edited.

## Input source selection

Choose one source:

- Patch file: `--patch-file PATH`
- Commit range: `--git-range A..B`
- Default current working tree diff vs `HEAD` when no source flags are given

By default, untracked files are excluded.
Use `--include-untracked` when relevant untracked files should be included in review scope.
Use `--exclude-path` to remove generated or irrelevant paths (for example `showcase/**`).
Binary diffs are excluded by default; only use `--include-binary` when the binary change itself is review-critical.
If context preparation fails due diff-size safeguards, narrow scope with `--exclude-path` before retrying.

## Annotation authoring guidance

- Notes are authored as JSONL records in `review/review-notes.jsonl`:
  - `{"type":"overview","text":"..."}`
  - `{"type":"file_summary","path":"...","summary":"..."}`
  - `{"type":"anchor_note","anchor_id":"...","what_changed":"...","why_changed":"...","title":"...","reviewer_focus":"...","risk":"...","severity":"note"}`
- Use `anchor_id` exactly from `review/review-input.txt`.
- Missing or unknown `anchor_id` records are rejected and written to `review/rejected-notes.jsonl`.
- Keep annotations focused on non-computable reviewer value (intent, rationale, risk, compatibility impact).
- Do not restate automatically available facts such as line numbers, hunk ids, diff stats, or file counts.
- Add top-level `overview` with 2-4 concise lines for the header:
  scope, primary intent, and reviewer focus/risk.
- For every anchor note include both:
  `what_changed` (concrete behavior/data/API change), and
  `why_changed` (intent, fix, reliability, maintainability, compatibility, etc.).
- Prefer hunk-level anchor explanations as the default.
- Keep high-severity notes rare (`warning`/`risk`) and reserve them for genuinely important reviewer attention points.

Read `references/annotation-schema.md` for field semantics; for day-to-day work, rely on `review/review-input.txt` + JSONL records.

## Recovery rules

- If report shows rejected lines, fix `review/review-notes.jsonl` and rerun `prereview`.
- If context-related warnings appear, rerun `prereview` to regenerate context/input from the latest diff.
- Do not patch output HTML directly; update notes and rerun.
