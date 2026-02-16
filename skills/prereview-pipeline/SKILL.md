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
2. Read `prereview/review-input.txt`.
3. Write notes to `prereview/review-notes.jsonl`.
4. Run `prereview` again to parse notes, validate/sanitize, and rebuild `prereview/review.html`.
5. Optional cleanup: run `prereview clean` to remove `prereview/` artifacts and local git exclude entry.

`prereview` performs end-to-end execution in one command:
- recompute context from current diff;
- write agent-facing `prereview/review-input.txt`;
- parse `prereview/review-notes.jsonl`;
- move malformed/unmappable note lines into `prereview/rejected-notes.jsonl`;
- build `prereview/review.html` with validation issues shown in the report.

Canonical JSON artifacts are internal (`prereview/review-context.json`, `prereview/annotations.json`) and should not be manually edited.

## Input source selection

Choose one source:

- Patch file: `--patch-file PATH`
- Commit range: `--git-range A..B`
- Default current working tree diff vs `HEAD` when no source flags are given

By default, untracked files are excluded.
Use `--include PATH` to scope review to matching paths (tracked or untracked, repeatable).
Without `--include`, prereview uses tracked working-tree changes and excludes binary diffs.
If context preparation fails due diff-size safeguards, narrow scope with `--include` before retrying.

## Annotation authoring guidance

- Notes are authored as JSONL records in `prereview/review-notes.jsonl`:
  - `{"type":"overview","text":"..."}`
  - `{"type":"file_summary","path":"...","summary":"..."}`
  - `{"type":"anchor_note","anchor_id":"...","what_changed":"...","why_changed":"...","title":"...","reviewer_focus":"...","risk":"...","severity":"note"}`
- Use `anchor_id` exactly from `prereview/review-input.txt`.
- Missing or unknown `anchor_id` records are rejected and written to `prereview/rejected-notes.jsonl`.
- Keep annotations focused on non-computable reviewer value (intent, rationale, risk, compatibility impact).
- Do not restate automatically available facts such as line numbers, hunk ids, diff stats, or file counts.
- Add top-level `overview` with 2-4 concise lines for the header:
  scope, primary intent, and reviewer focus/risk.
- For every anchor note include both:
  `what_changed` (concrete behavior/data/API change), and
  `why_changed` (intent, fix, reliability, maintainability, compatibility, etc.).
- Prefer hunk-level anchor explanations as the default.
- Keep high-severity notes rare (`warning`/`risk`) and reserve them for genuinely important reviewer attention points.

Read `references/annotation-schema.md` for field semantics; for day-to-day work, rely on `prereview/review-input.txt` + JSONL records.

## Recovery rules

- If report shows rejected lines, fix `prereview/review-notes.jsonl` and rerun `prereview`.
- If context-related warnings appear, rerun `prereview` to regenerate context/input from the latest diff.
- Do not patch output HTML directly; update notes and rerun.
